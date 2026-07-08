# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.dressup``.

Consolidates the former ``partdesign.fillet_feature``,
``partdesign.chamfer_feature``, ``partdesign.draft_feature``, and
``partdesign.thickness_feature`` tools behind an ``operation`` discriminator.
"""

from __future__ import annotations

from typing import Any

from . import domain_runtime
from VibeCADTransactions import run_freecad_transaction


TOOL_SPEC = {
    "contextual": True,
    "description": (
        "Apply PartDesign fillet, chamfer, draft, or thickness after the base "
        "shape is correct. Dressups refine edges/walls; they do not replace "
        "missing curves, ribs, load paths, or mating geometry."
    ),
    "name": "partdesign.dressup",
    "parameters": {
        "properties": {
            "operation": {
                "enum": ["fillet", "chamfer", "draft", "thickness"],
                "type": "string",
            },
            "feature_name": {
                "type": "string",
                "description": "Base PartDesign feature internal name or label.",
            },
            "label": {"type": "string"},
            "radius": {
                "type": "number",
                "description": "fillet only: fillet radius in mm (default 0.5).",
            },
            "size": {
                "type": "number",
                "description": "chamfer only: chamfer size in mm (default 0.5).",
            },
            "all_edges": {
                "type": "boolean",
                "description": (
                    "fillet/chamfer: apply to all edges (default true when no "
                    "edge_names are given)."
                ),
            },
            "edge_names": {
                "items": {"type": "string"},
                "type": "array",
                "description": "fillet/chamfer: explicit edge names such as Edge1.",
            },
            "face_names": {
                "items": {"type": "string"},
                "type": "array",
                "description": (
                    "draft/thickness: face names such as Face1 or Face6. Required "
                    "for draft; defaults to ['Face1'] for thickness."
                ),
            },
            "neutral_plane_name": {
                "type": "string",
                "description": (
                    "draft only: PartDesign Datum Plane or other neutral-plane "
                    "object name/label."
                ),
            },
            "pull_direction_name": {
                "type": "string",
                "description": (
                    "draft only: PartDesign Datum Line or other pull-direction "
                    "object name/label."
                ),
            },
            "angle": {
                "type": "number",
                "description": "draft only: draft angle in degrees (0 < angle < 89, default 3).",
            },
            "reversed": {
                "type": "boolean",
                "description": "draft only: reverse the draft direction.",
            },
            "wall_thickness": {
                "type": "number",
                "description": (
                    "thickness only: shell wall thickness in mm (default 1.5); "
                    "the remaining hollow body keeps walls of this thickness."
                ),
            },
            "inward": {
                "type": "boolean",
                "description": (
                    "thickness only: when true, make thickness inwards like the "
                    "PartDesign task panel option (default true)."
                ),
            },
            "mode": {
                "type": "integer",
                "description": "thickness only: native PartDesign thickness mode integer.",
            },
            "join": {
                "type": "integer",
                "description": "thickness only: native PartDesign thickness join mode integer.",
            },
        },
        "required": ["operation", "feature_name"],
        "type": "object",
    },
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
}


def _get_body_item(service: Any, name_or_label: str) -> Any:
    item = service._get_document_object(name_or_label)
    if item is not None:
        return item
    for body in service._partdesign_bodies():
        if getattr(body, "Label", None) == name_or_label:
            return body
        for child in list(getattr(body, "Group", []) or []):
            if (
                getattr(child, "Name", None) == name_or_label
                or getattr(child, "Label", None) == name_or_label
            ):
                return child
    return None


def _validate_face_names(target: Any, selected_faces: list[str]) -> None:
    faces = list(getattr(getattr(target, "Shape", None), "Faces", []) or [])
    if not faces:
        raise RuntimeError(f"Feature has no faces: {target.Name}")
    for face_name in selected_faces:
        if not face_name.startswith("Face"):
            raise RuntimeError(f"Invalid face name: {face_name}")
        try:
            face_index = int(face_name[4:])
        except ValueError as exc:
            raise RuntimeError(f"Invalid face name: {face_name}") from exc
        if face_index < 1 or face_index > len(faces):
            raise RuntimeError(f"Face name out of range for {target.Name}: {face_name}")


def _shape_validity(obj: Any) -> dict[str, Any]:
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return {"available": False, "valid": None, "null": None}
    try:
        is_null = bool(shape.isNull())
    except Exception:
        is_null = None
    try:
        is_valid = None if is_null else bool(shape.isValid())
    except Exception as exc:
        return {
            "available": True,
            "valid": None,
            "null": is_null,
            "error": str(exc),
        }
    return {"available": True, "valid": is_valid, "null": is_null}


def _invalid_base_response(
    *,
    operation: str,
    feature_name: str,
    feature: Any,
    validity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"Cannot apply PartDesign {operation}: base feature "
            f"{getattr(feature, 'Name', feature_name)} has an invalid or null "
            "shape. Re-picking edges or retrying the same dressup will not fix "
            "this; repair the upstream feature/body first."
        ),
        "operation": operation,
        "base_feature": getattr(feature, "Name", feature_name),
        "base_feature_label": getattr(feature, "Label", feature_name),
        "base_shape_validity": validity,
        "recoverable": True,
        "retry_same_call": False,
        "required_state_before_retry": (
            "The base feature and owning Body must recompute to a valid shape. "
            "Inspect report-view errors and the source sketch/profile, repair "
            "the upstream geometry, then find fresh edges and retry dressup "
            "with corrected arguments."
        ),
    }


def _is_invalid_geometry_failure(envelope: dict[str, Any]) -> bool:
    chunks: list[str] = []
    for key in ("error", "likely_cause"):
        if envelope.get(key):
            chunks.append(str(envelope[key]))
    for line in envelope.get("recompute_errors", []) or []:
        chunks.append(str(line))
    transaction = envelope.get("transaction")
    if isinstance(transaction, dict):
        if transaction.get("error"):
            chunks.append(str(transaction["error"]))
        report = transaction.get("report_view_errors")
        if isinstance(report, dict):
            chunks.extend(str(item) for item in report.get("errors", []) or [])
    text = " ".join(chunks).lower()
    return any(
        marker in text
        for marker in (
            "shape is invalid",
            "brep_api",
            "command not done",
            "invalid edge link",
            "result shape is null",
            "failed to make face",
        )
    )


def _mark_nonrepeatable_geometry_failure(
    envelope: dict[str, Any],
    *,
    operation: str,
    feature_name: str,
) -> None:
    envelope["recoverable"] = True
    envelope["retry_same_call"] = False
    envelope["base_feature"] = feature_name
    envelope["required_state_before_retry"] = (
        "Do not retry this same dressup call and do not keep re-picking edges "
        "against the same invalid shape. First repair the upstream feature/body "
        "until it recomputes as a valid solid, then run find_subelements again "
        "and call partdesign.dressup with fresh edge/face names and corrected "
        "radius/size."
    )
    envelope["required_next_action"] = {
        "inspect": [
            "core.get_report_view_errors",
            "core.get_object_properties",
        ],
        "repair": "Fix or rebuild the upstream feature that made the Body invalid.",
        "then": "Recompute, find fresh subelements, and retry partdesign.dressup.",
    }
    if envelope.get("error"):
        envelope["error"] = (
            f"PartDesign {operation} cannot proceed because the source geometry "
            f"for {feature_name} is invalid. {envelope['error']}"
        )


def run(
    service: Any,
    operation: str = "",
    feature_name: str = "",
    label: str | None = None,
    radius: float = 0.5,
    size: float = 0.5,
    all_edges: bool = True,
    edge_names: list[str] | None = None,
    face_names: list[str] | None = None,
    neutral_plane_name: str | None = None,
    pull_direction_name: str | None = None,
    angle: float = 3.0,
    reversed: bool = False,
    wall_thickness: float = 1.5,
    inward: bool = True,
    mode: int = 0,
    join: int = 0,
) -> dict[str, Any]:
    op = str(operation or "").strip().lower()
    if op not in {"fillet", "chamfer", "draft", "thickness"}:
        return {
            "ok": False,
            "error": "operation must be one of: fillet, chamfer, draft, thickness.",
            "requested_operation": operation,
        }
    feature = _get_body_item(service, feature_name)
    if feature is None:
        return {"ok": False, "error": f"PartDesign feature not found: {feature_name}"}
    if not str(getattr(feature, "TypeId", "")).startswith("PartDesign::"):
        return {"ok": False, "error": f"Object is not a PartDesign feature: {feature_name}"}
    base_validity = _shape_validity(feature)
    if base_validity.get("available") and (
        base_validity.get("null") is True or base_validity.get("valid") is False
    ):
        return _invalid_base_response(
            operation=op or str(operation or ""),
            feature_name=feature_name,
            feature=feature,
            validity=base_validity,
        )

    neutral_plane = None
    pull_direction = None
    selected_faces: list[str] = []
    if op == "fillet":
        if float(radius) <= 0:
            return {"ok": False, "error": "Fillet radius must be positive."}
        display = "Fillet"
        effective_label = label or "VibeCAD PartDesign Fillet"
    elif op == "chamfer":
        if float(size) <= 0:
            return {"ok": False, "error": "Chamfer size must be positive."}
        display = "Chamfer"
        effective_label = label or "VibeCAD PartDesign Chamfer"
    elif op == "draft":
        if not neutral_plane_name:
            return {"ok": False, "error": "neutral_plane_name is required for draft."}
        if not pull_direction_name:
            return {"ok": False, "error": "pull_direction_name is required for draft."}
        neutral_plane = _get_body_item(service, neutral_plane_name)
        if neutral_plane is None:
            return {"ok": False, "error": f"Neutral plane object not found: {neutral_plane_name}"}
        pull_direction = _get_body_item(service, pull_direction_name)
        if pull_direction is None:
            return {
                "ok": False,
                "error": f"Pull direction object not found: {pull_direction_name}",
            }
        selected_faces = [str(item) for item in (face_names or [])]
        if not selected_faces:
            return {"ok": False, "error": "At least one face name is required for draft."}
        if float(angle) <= 0 or float(angle) >= 89:
            return {
                "ok": False,
                "error": "Draft angle must be greater than 0 and less than 89 degrees.",
            }
        display = "Draft"
        effective_label = label or "VibeCAD PartDesign Draft"
    else:
        if float(wall_thickness) <= 0:
            return {"ok": False, "error": "wall_thickness must be positive."}
        selected_faces = [str(item) for item in (face_names or ["Face1"])]
        if not selected_faces:
            return {"ok": False, "error": "At least one face name is required for thickness."}
        display = "Thickness"
        effective_label = label or "VibeCAD PartDesign Thickness"

    def _dressup() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target = _get_body_item(service, feature.Name)
        if target is None:
            raise RuntimeError(f"PartDesign feature not found: {feature.Name}")
        body = service._partdesign_body_for_feature(target)
        if body is None:
            raise RuntimeError(f"No PartDesign Body found for {op}.")
        body_shape_before = domain_runtime.shape_summary(body)
        details: dict[str, Any]
        if op == "fillet":
            dressup = body.newObject("PartDesign::Fillet", "VibeCAD_PD_Fillet")
            dressup.Label = effective_label
            selected_edges = [str(item) for item in edge_names or []]
            dressup.Base = (target, selected_edges)
            dressup.Radius = float(radius)
            dressup.UseAllEdges = bool(all_edges or not selected_edges)
            details = {
                "radius": float(dressup.Radius),
                "use_all_edges": bool(getattr(dressup, "UseAllEdges", False)),
                "edge_names": selected_edges,
            }
        elif op == "chamfer":
            dressup = body.newObject("PartDesign::Chamfer", "VibeCAD_PD_Chamfer")
            dressup.Label = effective_label
            selected_edges = [str(item) for item in edge_names or []]
            dressup.Base = (target, selected_edges)
            dressup.Size = float(size)
            dressup.UseAllEdges = bool(all_edges or not selected_edges)
            details = {
                "size": float(dressup.Size),
                "use_all_edges": bool(getattr(dressup, "UseAllEdges", False)),
                "edge_names": selected_edges,
            }
        elif op == "draft":
            neutral = _get_body_item(service, neutral_plane.Name)
            pull = _get_body_item(service, pull_direction.Name)
            if neutral is None or pull is None:
                raise RuntimeError("Draft neutral plane or pull direction is missing.")
            _validate_face_names(target, selected_faces)
            dressup = doc.addObject("PartDesign::Draft", "VibeCAD_PD_Draft")
            dressup.Label = effective_label
            dressup.Base = (target, selected_faces)
            dressup.NeutralPlane = (neutral, [""])
            dressup.PullDirection = (pull, [""])
            dressup.Angle = float(angle)
            dressup.Reversed = bool(reversed)
            body.addObject(dressup)
            details = {
                "face_names": selected_faces,
                "neutral_plane": neutral.Name,
                "pull_direction": pull.Name,
                "angle": float(dressup.Angle),
            }
        else:
            _validate_face_names(target, selected_faces)
            dressup = doc.addObject("PartDesign::Thickness", "VibeCAD_PD_Thickness")
            dressup.Label = effective_label
            dressup.Base = (target, selected_faces)
            body.addObject(dressup)
            dressup.Value = float(wall_thickness)
            dressup.Reversed = 1 if inward else 0
            dressup.Mode = int(mode)
            dressup.Join = int(join)
            dressup.Base = (target, selected_faces)
            details = {
                "face_names": selected_faces,
                "wall_thickness": float(wall_thickness),
                "inward": bool(inward),
                "mode": int(mode),
                "join": int(join),
            }
        body.Tip = dressup
        doc.recompute()
        if op == "draft" and "Invalid" in list(getattr(dressup, "State", []) or []):
            dressup.Reversed = not bool(getattr(dressup, "Reversed", False))
            doc.recompute()
        if op == "draft":
            details["reversed"] = bool(getattr(dressup, "Reversed", False))
        dressup_name = dressup.Name
        dressup_label = getattr(dressup, "Label", dressup_name)
        dressup_type = getattr(dressup, "TypeId", "")
        dressup_face_count = len(getattr(getattr(dressup, "Shape", None), "Faces", []) or [])
        dressup_volume = float(getattr(getattr(dressup, "Shape", None), "Volume", 0.0) or 0.0)
        effect = domain_runtime.finalize_partdesign_feature_effect(
            doc,
            body,
            dressup,
            op,
            body_shape_before,
        )
        return {
            "document": doc.Name,
            "body": body.Name,
            "base_feature": target.Name,
            "operation": op,
            "feature": dressup_name,
            "label": dressup_label,
            "type": dressup_type,
            "face_count": dressup_face_count,
            "volume": dressup_volume,
            **details,
            **effect,
        }

    transaction = run_freecad_transaction(
        f"Create PartDesign {op} from feature: {getattr(feature, 'Label', feature.Name)}",
        _dressup,
    )
    envelope = domain_runtime.build_partdesign_feature_result(
        service,
        transaction,
        operation=display,
    )
    if envelope.get("error"):
        # Dress-up failures (e.g. an impossible radius producing an invalid
        # shape) roll back cleanly and are retryable with adjusted parameters.
        envelope["recoverable"] = True
        if _is_invalid_geometry_failure(envelope):
            _mark_nonrepeatable_geometry_failure(
                envelope,
                operation=op,
                feature_name=getattr(feature, "Name", feature_name),
            )
    return envelope
