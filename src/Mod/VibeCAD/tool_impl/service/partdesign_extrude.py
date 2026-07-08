# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.extrude``.

Consolidates the former ``partdesign.pad_sketch`` and
``partdesign.pocket_sketch`` tools behind an ``operation`` discriminator.
"""

from __future__ import annotations

from typing import Any

from . import domain_runtime
from VibeCADTransactions import run_freecad_transaction


TOOL_SPEC = {
    "contextual": True,
    "description": (
        "Create a PartDesign Pad or Pocket from a closed sketch. Use only for "
        "prismatic material; use revolve, sweep, or loft for curved, twisted, "
        "blade, duct, or varying-section geometry."
    ),
    "name": "partdesign.extrude",
    "parameters": {
        "properties": {
            "operation": {
                "enum": ["pad", "pocket"],
                "type": "string",
                "description": "pad adds material; pocket removes material.",
            },
            "sketch_name": {
                "type": "string",
                "description": "Sketch name or label to extrude.",
            },
            "label": {"type": "string"},
            "length": {
                "type": "number",
                "description": (
                    "Extrusion length in mm (default 10 for pad, 5 for pocket)."
                ),
            },
            "midplane": {
                "type": "boolean",
                "description": "Extrude symmetrically about the sketch plane.",
            },
            "reversed": {
                "type": "boolean",
                "description": "Extrude toward the opposite side of the sketch plane.",
            },
        },
        "required": ["operation"],
        "type": "object",
    },
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
}

_OPERATIONS: dict[str, dict[str, Any]] = {
    "pad": {
        "type_id": "PartDesign::Pad",
        "object_name": "VibeCAD_Pad",
        "default_label": "VibeCAD Pad",
        "default_length": 10.0,
        "readiness_key": "ready_for_pad",
        "effect_operation": "pad",
        "display": "Pad",
    },
    "pocket": {
        "type_id": "PartDesign::Pocket",
        "object_name": "VibeCAD_Pocket",
        "default_label": "VibeCAD Pocket",
        "default_length": 5.0,
        "readiness_key": "ready_for_pocket",
        "effect_operation": "pocket",
        "display": "Pocket",
    },
}


def _set_side_mode(feature: Any, midplane: bool) -> str:
    requested = bool(midplane)
    if hasattr(feature, "SideType"):
        try:
            choices = list(feature.getEnumerationsOfProperty("SideType") or [])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            choices = []
        if requested:
            for choice in ("Symmetric", "Two sides", "To first"):
                if not choices or choice in choices:
                    feature.SideType = choice
                    return choice
        if not choices or "One side" in choices:
            feature.SideType = "One side"
            return "One side"
    if hasattr(feature, "Midplane"):
        # Legacy fallback only: modern FreeCAD uses SideType; Midplane is deprecated.
        setattr(feature, "Midplane", requested)  # noqa: B010
        return "Midplane"
    return ""


def _is_midplane(feature: Any) -> bool:
    side_type = getattr(feature, "SideType", "")
    if side_type:
        return side_type in {"Symmetric", "Two sides"}
    return bool(getattr(feature, "Midplane", False))


def run(
    service: Any,
    operation: str = "",
    sketch_name: str | None = None,
    label: str | None = None,
    length: float | None = None,
    reversed: bool = False,
    midplane: bool = False,
) -> dict[str, Any]:
    spec = _OPERATIONS.get(str(operation or "").strip().lower())
    if spec is None:
        return {
            "ok": False,
            "error": "operation must be one of: pad, pocket.",
            "requested_operation": operation,
        }
    display = spec["display"]
    effective_label = label or spec["default_label"]
    effective_length = float(spec["default_length"] if length is None else length)
    sketch = service._get_sketch(sketch_name)
    if sketch is None:
        return {"ok": False, "error": "Sketch not found.", "requested": sketch_name}
    if effective_length <= 0:
        return {"ok": False, "error": f"{display} length must be positive."}
    profile_status = service._sketch_profile_status(sketch)
    if not profile_status.get(spec["readiness_key"]):
        specific_reason = str(profile_status.get("reason") or "").strip()
        if not specific_reason:
            specific_reason = (
                "it does not contain a closed profile that is fully constrained."
            )
        error = f"Sketch is not ready for PartDesign {display}: {specific_reason}"
        return {
            "ok": False,
            "error": error,
            "requested": sketch_name,
            "active_sketch": getattr(sketch, "Name", None),
            "profile_status": profile_status,
            "next_actions": service._sketch_next_actions(sketch),
            "recoverable": True,
        }

    def _extrude() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target_sketch = service._get_sketch(sketch.Name)
        if target_sketch is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        body = None
        for candidate in service._partdesign_bodies():
            if target_sketch in list(getattr(candidate, "Group", []) or []):
                body = candidate
                break
        if body is None:
            body = service._get_partdesign_body()
        if body is None:
            raise RuntimeError(f"No PartDesign Body found for {spec['effect_operation']}.")
        body_shape_before = domain_runtime.shape_summary(body)
        feature = body.newObject(spec["type_id"], spec["object_name"])
        feature.Label = effective_label
        feature.Profile = target_sketch
        feature.Length = effective_length
        feature.Reversed = bool(reversed)
        side_type = _set_side_mode(feature, bool(midplane))
        body.Tip = feature
        doc.recompute()
        feature_name = feature.Name
        feature_label = getattr(feature, "Label", feature_name)
        feature_type = getattr(feature, "TypeId", "")
        feature_length = float(feature.Length)
        feature_reversed = bool(getattr(feature, "Reversed", False))
        feature_midplane = _is_midplane(feature)
        feature_side_type = side_type or getattr(feature, "SideType", "")
        effect = domain_runtime.finalize_partdesign_feature_effect(
            doc,
            body,
            feature,
            spec["effect_operation"],
            body_shape_before,
        )
        return {
            "document": doc.Name,
            "body": body.Name,
            "sketch": target_sketch.Name,
            "operation": spec["effect_operation"],
            "feature": feature_name,
            "label": feature_label,
            "type": feature_type,
            "length": feature_length,
            "reversed": feature_reversed,
            "midplane": feature_midplane,
            "side_type": feature_side_type,
            **effect,
        }

    transaction = run_freecad_transaction(
        f"Create PartDesign {spec['effect_operation']} from sketch: "
        f"{getattr(sketch, 'Label', sketch.Name)}",
        _extrude,
    )
    return domain_runtime.build_partdesign_feature_result(
        service,
        transaction,
        operation=display,
        active_sketch=getattr(sketch, "Name", None),
        profile_status=service._sketch_profile_status(sketch),
    )
