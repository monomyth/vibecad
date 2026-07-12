# SPDX-License-Identifier: LGPL-2.1-or-later

"""Start, inspect, or cancel one asynchronous native Gmsh FEM mesh run."""

from __future__ import annotations

import platform
import shutil
from typing import Any

from VibeCADTools import tool_failure
from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_START_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "const": "start",
            "description": "Prepare and start a new Gmsh run.",
        },
        "analysis_name": {
            "type": "string",
            "minLength": 1,
            "description": "Exact FEM analysis name.",
        },
        "source_object_name": {
            "type": "string",
            "minLength": 1,
            "description": "Exact shaped solid object to mesh.",
        },
        "max_element_size_mm": {
            "type": "number",
            "minimum": 0,
            "description": "Maximum element edge length; 0 lets Gmsh choose.",
        },
        "element_order": {
            "type": "string",
            "enum": ["1st", "2nd"],
            "description": "Linear or quadratic finite elements.",
        },
        "label": {
            "type": "string",
            "minLength": 1,
            "description": "Visible mesh-object label.",
        },
    },
    "required": [
        "action",
        "analysis_name",
        "source_object_name",
        "max_element_size_mm",
        "element_order",
        "label",
    ],
    "additionalProperties": False,
}

_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "const": "status",
            "description": "Inspect an existing Gmsh run.",
        },
        "operation_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact operation ID returned by operation.action='start'."
            ),
        },
    },
    "required": ["action", "operation_id"],
    "additionalProperties": False,
}

_CANCEL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "const": "cancel",
            "description": "Cancel an existing Gmsh run.",
        },
        "operation_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact operation ID returned by operation.action='start'."
            ),
        },
    },
    "required": ["action", "operation_id"],
    "additionalProperties": False,
}

_OPERATION_SCHEMA = {
    "description": (
        "Exactly one Gmsh lifecycle operation. Each action exposes only the fields "
        "that operation accepts."
    ),
    "oneOf": [_START_SCHEMA, _STATUS_SCHEMA, _CANCEL_SCHEMA],
}


TOOL_SPEC = {
    "name": "fem.mesh_analysis",
    "description": (
        "Control one asynchronous Gmsh mesh lifecycle through one exact operation "
        "object. Start returns immediately with an operation_id and leaves FreeCAD "
        "responsive; status polls that ID and cancel stops it. The mesh is added to "
        "the analysis only after Gmsh exits successfully, import completes, and a "
        "solid source has valid volume elements."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "FemWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {"operation": _OPERATION_SCHEMA},
        "required": ["operation"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    operation: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(operation, dict):
        return _invalid(
            "operation must be an object.",
            failure_code="OPERATION_SCHEMA_INVALID",
            failure_stage="schema",
            requested={"operation": operation},
            required_changes=[{"operation": "one start, status, or cancel object"}],
        )
    clean_action = str(operation.get("action") or "").strip()
    if clean_action == "start":
        result = _start(
            service,
            analysis_name=operation.get("analysis_name"),
            source_object_name=operation.get("source_object_name"),
            max_element_size_mm=operation.get("max_element_size_mm"),
            element_order=operation.get("element_order"),
            label=operation.get("label"),
        )
    elif clean_action == "status":
        result = _status(service, str(operation.get("operation_id") or "").strip())
    elif clean_action == "cancel":
        result = _cancel(service, str(operation.get("operation_id") or "").strip())
    else:
        return _invalid(
            "operation.action must be one of: start, status, cancel.",
            failure_code="OPERATION_ACTION_INVALID",
            failure_stage="schema",
            requested={"operation": operation},
            allowed_values=["start", "status", "cancel"],
            required_changes=[{"operation.action": ["start", "status", "cancel"]}],
        )
    if not result.get("ok") and not result.get("requested"):
        result["requested"] = {"operation": operation}
    return result


def _start(
    service: Any,
    *,
    analysis_name: str | None,
    source_object_name: str | None,
    max_element_size_mm: float | None,
    element_order: str | None,
    label: str | None,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if element_order not in ("1st", "2nd"):
        return _invalid("element_order must be '1st' or '2nd'.")
    max_size = float(max_element_size_mm or 0.0)
    if max_size < 0.0:
        return _invalid("max_element_size_mm cannot be negative.")
    analysis = service._get_fem_analysis(str(analysis_name or "").strip())
    if analysis is None:
        return _invalid(
            f"FEM analysis not found by exact internal name: {analysis_name}."
        )
    clean_source = str(source_object_name or "").strip()
    doc = service._active_document()
    source = doc.getObject(clean_source) if doc is not None and clean_source else None
    if source is None:
        return _invalid(f"Object not found by exact internal name: {source_object_name}")
    source_health = domain_runtime.shape_health(source)
    source_shape = source_health.get("shape") or {}
    if not source_health.get("valid_non_null") or int(source_shape.get("solids", 0)) != 1:
        return _invalid(
            "FEM solid meshing requires exactly one valid BREP solid.",
            source=source_health,
        )
    relationship = _analysis_reference_relationship(analysis, clean_source)
    if relationship["unrelated_reference_objects"]:
        return _invalid(
            "Existing analysis constraints reference objects other than the "
            "requested mesh source.",
            model_relationship=relationship,
        )
    executable = _gmsh_preflight()
    if not executable.get("ok"):
        return _invalid(
            "Gmsh executable preflight failed; no mesh object was created.",
            executable=executable,
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import ObjectsFem
        from femmesh import gmshtools

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target = active.getObject(analysis.Name)
        part_obj = active.getObject(clean_source)
        if target is None or part_obj is None:
            raise RuntimeError("The analysis or source object no longer exists.")
        mesh_obj = ObjectsFem.makeMeshGmsh(active, "FEMMeshGmsh")
        mesh_obj.Label = clean_label
        mesh_obj.Shape = part_obj
        mesh_obj.ElementOrder = element_order
        if max_size > 0.0:
            mesh_obj.CharacteristicLengthMax = f"{max_size} mm"
        tool = gmshtools.GmshTools(mesh_obj)
        operation = tool.run(blocking=False)
        if not tool.process.waitForStarted(5000):
            diagnostics = tool.process_diagnostics()
            raise RuntimeError(
                "Gmsh did not enter the running state: "
                + str((diagnostics.get("process") or {}).get("error") or "unknown error")
            )
        _add_operation_properties(
            mesh_obj,
            operation_id=operation,
            analysis_name=target.Name,
            source_name=part_obj.Name,
            state="running",
        )
        active.recompute()
        process = tool.process_diagnostics()
        return {
            "document": active.Name,
            "analysis": target.Name,
            "mesh_object": mesh_obj.Name,
            "mesh_object_label": mesh_obj.Label,
            "source_object": part_obj.Name,
            "source_shape": source_health,
            "model_relationship": relationship,
            "requested_settings": {
                "max_element_size_mm": max_size,
                "element_order": element_order,
            },
            "actual_settings": {
                "max_element_size_mm": float(mesh_obj.CharacteristicLengthMax.Value),
                "element_order": str(mesh_obj.ElementOrder),
                "working_directory": str(mesh_obj.WorkingDirectory),
            },
            "executable_preflight": executable,
            "operation_id": operation,
            "process": process,
            "analysis_membership": mesh_obj.Name
            in [member.Name for member in list(target.Group or [])],
            "retained_mesh_object": mesh_obj.Name,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        process = result.get("process") or {}
        state = process.get("operation_state")
        checks = [
            {
                "name": "process_started",
                "ok": state in {"running", "completed", "importing_results"},
                "process": process,
            },
            {
                "name": "mesh_not_prematurely_added",
                "ok": result.get("analysis_membership") is False,
                "actual": result.get("analysis_membership"),
            },
            {
                "name": "settings_readback",
                "ok": result.get("actual_settings", {}).get("element_order")
                == element_order
                and (
                    max_size == 0.0
                    or abs(
                        float(
                            result.get("actual_settings", {}).get(
                                "max_element_size_mm", -1.0
                            )
                        )
                        - max_size
                    )
                    <= 1.0e-9
                ),
                "requested": result.get("requested_settings"),
                "actual": result.get("actual_settings"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Start Gmsh FEM mesh: {clean_label}", create, verifier=verify
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "start_mesh_analysis", **result},
        next_action=(
            "Poll fem.mesh_analysis with operation.action='status' and this "
            "operation_id; do not start another mesh run for the same analysis "
            "while it is active."
        ),
    )


def _status(service: Any, operation_id: str) -> dict[str, Any]:
    operation = _operation(service, operation_id)
    if isinstance(operation, dict):
        return operation
    mesh_obj, tool = operation
    diagnostics = tool.process_diagnostics()
    state = diagnostics.get("operation_state")
    if state == "completed" and not bool(mesh_obj.VibeCADOperationFinalized):
        return _finalize_mesh(mesh_obj, tool)
    if state in {"failed", "cancelled"}:
        _persist_operation_state(mesh_obj, state)
        return {
            "ok": False,
            "operation": "mesh_analysis_status",
            "operation_id": operation_id,
            "operation_state": state,
            "complete": True,
            "process": diagnostics,
            "retained_mesh_object": mesh_obj.Name,
            "analysis_membership": _mesh_analysis_membership(mesh_obj),
            "retry_same_call": False,
        }
    return {
        "ok": True,
        "operation": "mesh_analysis_status",
        "operation_id": operation_id,
        "operation_state": state,
        "complete": False,
        "process": diagnostics,
        "retained_mesh_object": mesh_obj.Name,
        "analysis_membership": _mesh_analysis_membership(mesh_obj),
        "next_action": "Poll status again later, or cancel this operation.",
    }


def _cancel(service: Any, operation_id: str) -> dict[str, Any]:
    operation = _operation(service, operation_id)
    if isinstance(operation, dict):
        return operation
    mesh_obj, tool = operation
    from PySide.QtCore import QProcess

    if tool.cancel_requested and tool.process.state() != QProcess.ProcessState.NotRunning:
        diagnostics = tool.kill()
        action_taken = "kill_after_prior_cancel"
    else:
        diagnostics = tool.cancel()
        action_taken = "terminate_requested"
    _persist_operation_state(mesh_obj, diagnostics.get("operation_state") or "cancel_requested")
    return {
        "ok": True,
        "operation": "cancel_mesh_analysis",
        "operation_id": operation_id,
        "action_taken": action_taken,
        "process": diagnostics,
        "retained_mesh_object": mesh_obj.Name,
        "next_action": "Poll status until operation_state is cancelled or failed.",
    }


def _finalize_mesh(mesh_obj: Any, tool: Any) -> dict[str, Any]:
    analysis_name = str(mesh_obj.VibeCADOperationAnalysis)

    def finalize() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        target = active.getObject(analysis_name) if active is not None else None
        current_mesh = active.getObject(mesh_obj.Name) if active is not None else None
        if target is None or current_mesh is None:
            raise RuntimeError("The analysis or pending mesh object no longer exists.")
        summary = _fem_mesh_summary(current_mesh.FemMesh)
        valid = (
            summary["node_count"] > 0
            and summary["volume_element_count"] > 0
            and summary["quality_metrics"].get("degenerate_tetrahedra", 0) == 0
        )
        if valid:
            target.addObject(current_mesh)
            current_mesh.VibeCADOperationState = "completed"
            current_mesh.VibeCADOperationFinalized = True
        else:
            current_mesh.VibeCADOperationState = "failed_postcondition"
        active.recompute()
        members = [member.Name for member in list(target.Group or [])]
        return {
            "document": active.Name,
            "analysis": target.Name,
            "mesh_object": current_mesh.Name,
            "operation_id": str(current_mesh.VibeCADOperationId),
            "operation_state": str(current_mesh.VibeCADOperationState),
            "complete": True,
            "process": tool.process_diagnostics(),
            "mesh": summary,
            "analysis_group_members": members,
            "analysis_membership": current_mesh.Name in members,
            "retained_mesh_object": current_mesh.Name,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        mesh = result.get("mesh") or {}
        checks = [
            {
                "name": "nodes_created",
                "ok": int(mesh.get("node_count", 0)) > 0,
                "actual": mesh.get("node_count"),
            },
            {
                "name": "solid_has_volume_elements",
                "ok": int(mesh.get("volume_element_count", 0)) > 0,
                "element_counts": mesh.get("element_counts"),
            },
            {
                "name": "no_degenerate_tetrahedra",
                "ok": int(
                    (mesh.get("quality_metrics") or {}).get(
                        "degenerate_tetrahedra", 0
                    )
                )
                == 0,
                "quality_metrics": mesh.get("quality_metrics"),
            },
            {
                "name": "analysis_membership_after_success",
                "ok": result.get("analysis_membership") is True,
                "analysis_group_members": result.get("analysis_group_members"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Finalize Gmsh FEM mesh: {mesh_obj.Label}", finalize, verifier=verify
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "finalize_mesh_analysis", **result},
        next_action=(
            "Run fem.solve with operation.action='start' after all required "
            "materials and constraints are present."
        ),
    )


def _gmsh_preflight() -> dict[str, Any]:
    import FreeCAD as App

    configured = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/Fem/Gmsh"
    ).GetString("gmshBinaryPath", "")
    if configured:
        resolved = shutil.which(configured)
        return {
            "ok": bool(resolved),
            "configured_path": configured,
            "resolved_path": resolved,
            "source": "configured_preference",
        }
    resolved = shutil.which("gmsh")
    if not resolved and platform.system() == "Darwin":
        resolved = shutil.which("/Applications/Gmsh.app/Contents/MacOS/gmsh")
    return {
        "ok": bool(resolved),
        "configured_path": "",
        "resolved_path": resolved,
        "source": "system_path",
    }


def _analysis_reference_relationship(analysis: Any, source_name: str) -> dict[str, Any]:
    referenced: set[str] = set()
    for member in list(getattr(analysis, "Group", []) or []):
        for property_name in ("References", "Direction"):
            value = getattr(member, property_name, None)
            entries = list(value) if isinstance(value, (list, tuple)) else []
            if entries and hasattr(entries[0], "Name"):
                entries = [value]
            for entry in entries:
                if isinstance(entry, (list, tuple)) and entry and hasattr(entry[0], "Name"):
                    referenced.add(entry[0].Name)
    return {
        "mesh_source_object": source_name,
        "constraint_reference_objects": sorted(referenced),
        "unrelated_reference_objects": sorted(referenced - {source_name}),
    }


def _add_operation_properties(
    obj: Any,
    *,
    operation_id: str,
    analysis_name: str,
    source_name: str,
    state: str,
) -> None:
    definitions = (
        ("App::PropertyString", "VibeCADOperationId"),
        ("App::PropertyString", "VibeCADOperationKind"),
        ("App::PropertyString", "VibeCADOperationAnalysis"),
        ("App::PropertyString", "VibeCADOperationSource"),
        ("App::PropertyString", "VibeCADOperationState"),
        ("App::PropertyBool", "VibeCADOperationFinalized"),
    )
    for property_type, name in definitions:
        if not hasattr(obj, name):
            obj.addProperty(property_type, name, "VibeCAD Operation")
    obj.VibeCADOperationId = operation_id
    obj.VibeCADOperationKind = "gmsh"
    obj.VibeCADOperationAnalysis = analysis_name
    obj.VibeCADOperationSource = source_name
    obj.VibeCADOperationState = state
    obj.VibeCADOperationFinalized = False


def _operation(service: Any, operation_id: str) -> tuple[Any, Any] | dict[str, Any]:
    if not operation_id:
        return _invalid("operation_id is required.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    matches = [
        obj
        for obj in doc.Objects
        if str(getattr(obj, "VibeCADOperationKind", "")) == "gmsh"
        and str(getattr(obj, "VibeCADOperationId", "")) == operation_id
    ]
    if len(matches) != 1:
        return _invalid(
            "No unique Gmsh operation matches operation_id.",
            operation_id=operation_id,
            match_count=len(matches),
        )
    mesh_obj = matches[0]
    tool = getattr(mesh_obj, "Tool", None)
    if tool is None or not hasattr(tool, "process_diagnostics"):
        return _invalid(
            "The external-process runtime for this mesh operation is no longer "
            "available; the retained mesh object remains inspectable.",
            operation_id=operation_id,
            retained_mesh_object=mesh_obj.Name,
            persisted_state=str(mesh_obj.VibeCADOperationState),
        )
    return mesh_obj, tool


def _persist_operation_state(mesh_obj: Any, state: str) -> None:
    def persist() -> dict[str, Any]:
        mesh_obj.VibeCADOperationState = str(state)
        mesh_obj.Document.recompute()
        return {"mesh_object": mesh_obj.Name, "state": str(mesh_obj.VibeCADOperationState)}

    run_freecad_transaction(f"Persist Gmsh state: {state}", persist)


def _mesh_analysis_membership(mesh_obj: Any) -> bool:
    analysis = mesh_obj.Document.getObject(str(mesh_obj.VibeCADOperationAnalysis))
    return bool(
        analysis is not None
        and mesh_obj.Name in [member.Name for member in list(analysis.Group or [])]
    )


def _fem_mesh_summary(mesh: Any) -> dict[str, Any]:
    element_counts = {
        "edges": int(getattr(mesh, "EdgeCount", 0) or 0),
        "faces": int(getattr(mesh, "FaceCount", 0) or 0),
        "triangles": int(getattr(mesh, "TriangleCount", 0) or 0),
        "quadrangles": int(getattr(mesh, "QuadrangleCount", 0) or 0),
        "volumes": int(getattr(mesh, "VolumeCount", 0) or 0),
        "tetrahedra": int(getattr(mesh, "TetraCount", 0) or 0),
        "hexahedra": int(getattr(mesh, "HexaCount", 0) or 0),
        "pyramids": int(getattr(mesh, "PyramidCount", 0) or 0),
        "prisms": int(getattr(mesh, "PrismCount", 0) or 0),
        "polyhedra": int(getattr(mesh, "PolyhedronCount", 0) or 0),
    }
    return {
        "node_count": int(getattr(mesh, "NodeCount", 0) or 0),
        "face_element_count": element_counts["faces"],
        "volume_element_count": element_counts["volumes"],
        "element_counts": element_counts,
        "mesh_volume_mm3": float(getattr(mesh, "Volume", 0.0) or 0.0),
        "quality_metrics": _tetra_quality(mesh),
    }


def _tetra_quality(mesh: Any) -> dict[str, Any]:
    tetra_ids = list(mesh.getIdByElementType("Volume"))
    nodes = dict(getattr(mesh, "Nodes", {}) or {})
    qualities: list[float] = []
    volumes: list[float] = []
    unsupported: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    degenerate = 0
    for element_id in tetra_ids:
        try:
            element_type = str(mesh.getElementType(element_id))
            node_ids = list(mesh.getElementNodes(element_id))
            if not element_type.lower().startswith("tetra") or len(node_ids) < 4:
                unsupported[element_type] = unsupported.get(element_type, 0) + 1
                continue
            points = [_node_xyz(nodes[node_id]) for node_id in node_ids[:4]]
            volume = abs(_tetra_signed_volume(*points))
            edge_lengths_sq = [
                _distance_sq(points[first], points[second])
                for first, second in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
            ]
            denominator = sum(edge_lengths_sq)
            quality = (
                12.0 * (3.0 * volume) ** (2.0 / 3.0) / denominator
                if volume > 0.0 and denominator > 0.0
                else 0.0
            )
            qualities.append(quality)
            volumes.append(volume)
            if quality <= 1.0e-12:
                degenerate += 1
        except Exception as exc:
            errors.append({"element_id": int(element_id), "native_error": str(exc)})
    analyzed = len(qualities)
    volume_count = int(getattr(mesh, "VolumeCount", 0) or 0)
    return {
        "metric": "mean-ratio tetrahedral quality; 1 is regular, 0 is degenerate",
        "coverage": {
            "analyzed_volume_elements": analyzed,
            "total_volume_elements": volume_count,
            "complete": analyzed == volume_count and not errors,
            "unsupported_element_types": unsupported,
            "errors": errors,
        },
        "minimum": min(qualities) if qualities else None,
        "maximum": max(qualities) if qualities else None,
        "mean": sum(qualities) / len(qualities) if qualities else None,
        "minimum_tetra_volume_mm3": min(volumes) if volumes else None,
        "maximum_tetra_volume_mm3": max(volumes) if volumes else None,
        "degenerate_tetrahedra": degenerate,
    }


def _node_xyz(value: Any) -> tuple[float, float, float]:
    return (float(value.x), float(value.y), float(value.z))


def _distance_sq(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second))


def _tetra_signed_volume(a: tuple[float, ...], b: tuple[float, ...], c: tuple[float, ...], d: tuple[float, ...]) -> float:
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    ad = tuple(d[index] - a[index] for index in range(3))
    cross = (
        ac[1] * ad[2] - ac[2] * ad[1],
        ac[2] * ad[0] - ac[0] * ad[2],
        ac[0] * ad[1] - ac[1] * ad[0],
    )
    return (ab[0] * cross[0] + ab[1] * cross[1] + ab[2] * cross[2]) / 6.0


def _invalid(
    message: str,
    *,
    failure_code: str = "FEM_MESH_OPERATION_REJECTED",
    failure_stage: str = "precondition",
    requested: Any = None,
    observed: Any = None,
    candidates: Any = None,
    allowed_values: Any = None,
    required_changes: list[Any] | None = None,
    **details: Any,
) -> dict[str, Any]:
    return tool_failure(
        TOOL_SPEC["name"],
        failure_code,
        failure_stage,
        message,
        requested=requested,
        observed=observed,
        candidates=candidates,
        allowed_values=allowed_values,
        required_changes=required_changes,
        **details,
    )
