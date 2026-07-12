# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Part fillet on exact named edges."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime, partdesign_dressup_feature


TOOL_SPEC = {
    "name": "part.fillet",
    "description": (
        "Create one native Part fillet that rounds count-guarded geometric edges of one shaped "
        "object. Finishing operation; apply after the primary form is complete. "
        "Resolve edge names with part.find_subelements first - never guess them. "
        "The source object becomes a hidden child of the fillet result."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": "Exact internal name of the object whose edges are filleted.",
            },
            "selection": partdesign_dressup_feature.selection_schema(
                allow_all_edges=True,
                edge_only=True,
            ),
            "radius_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Fillet radius in mm; must be smaller than the adjacent faces "
                    "can absorb or the fillet fails."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the fillet result.",
            },
        },
        "required": ["object_name", "selection", "radius_mm", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    object_name: str,
    selection: dict[str, Any],
    radius_mm: float,
    label: str,
) -> dict[str, Any]:
    return run_edge_finish(
        service,
        object_name=object_name,
        selection=selection,
        size_mm=radius_mm,
        label=label,
        native_type="Part::Fillet",
        operation="fillet",
    )


def run_edge_finish(
    service: Any,
    *,
    object_name: str,
    selection: dict[str, Any],
    size_mm: float,
    label: str,
    native_type: str,
    operation: str,
) -> dict[str, Any]:
    """Shared implementation for Part fillet and chamfer."""
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    size = float(size_mm)
    if size <= 0:
        return _invalid(f"{operation} size must be positive.")
    clean_name = str(object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Object has no shape geometry: {clean_name}")
    selection_state = partdesign_dressup_feature.resolve_selection(
        service,
        obj,
        selection,
        allow_all_edges=True,
        face_only=False,
        edge_only=True,
    )
    if not selection_state.get("ok"):
        return selection_state
    names = list(selection_state["subelements"])
    if selection_state.get("use_all_edges"):
        names = [item["name"] for item in selection_state["resolved_geometry"]]
    indexes = [int(name.removeprefix("Edge")) for name in names]
    source_health = domain_runtime.shape_health(obj)
    visibility_before = domain_runtime.view_visibility_summary(obj)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        base = active.getObject(clean_name)
        if base is None:
            raise RuntimeError("The object no longer exists.")
        feature = active.addObject(native_type, operation.capitalize())
        feature.Label = clean_label
        feature.Base = base
        feature.Edges = [(index, size, size) for index in indexes]
        active.recompute()
        view = getattr(base, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            view.Visibility = False
        return {
            "document": active.Name,
            "feature": feature.Name,
            "feature_label": feature.Label,
            "feature_type": feature.TypeId,
            "source_object": base.Name,
            "selection_request": dict(selection),
            "resolved_edges": selection_state["resolved_geometry"],
            "native_edge_indices": indexes,
            "size_mm": size,
            "source_shape": source_health,
            "source_visibility_before": visibility_before,
            "source_visibility_after": domain_runtime.view_visibility_summary(base),
            "native_edge_property": [list(item) for item in list(feature.Edges or [])],
            "shape": domain_runtime.shape_summary(feature),
            "feature_state": domain_runtime.feature_state_summary(feature),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        visibility = result.get("source_visibility_after") or {}
        feature_state = result.get("feature_state") or {}
        result_shape = result.get("shape") or {}
        checks = [
            {
                "name": "valid_dressup_shape",
                "ok": bool(result_shape.get("available"))
                and int(result_shape.get("solids", 0)) > 0
                and feature_state.get("shape_valid") is True
                and not feature_state.get("marked_invalid"),
                "actual": result_shape,
            },
            {
                "name": "resolved_edge_count",
                "ok": len(result.get("resolved_edges") or []) == len(indexes),
                "expected": len(indexes),
                "actual": len(result.get("resolved_edges") or []),
            },
            {
                "name": "source_visibility",
                "ok": not visibility.get("supported") or visibility.get("visible") is False,
                "actual": visibility,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Part {operation}: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation=operation)


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
