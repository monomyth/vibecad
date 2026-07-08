# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.pattern``.

Consolidates the former ``partdesign.linear_pattern``,
``partdesign.polar_pattern``, and ``partdesign.mirror_feature`` tools behind
an ``operation`` discriminator.
"""

from __future__ import annotations

from typing import Any

from . import domain_runtime
from VibeCADTransactions import run_freecad_transaction


TOOL_SPEC = {
    "contextual": True,
    "description": (
        "Replicate an existing PartDesign feature with native linear, polar, "
        "or mirror pattern features. Use for repeated features inside a Body."
    ),
    "name": "partdesign.pattern",
    "parameters": {
        "properties": {
            "operation": {
                "enum": ["linear", "polar", "mirror"],
                "type": "string",
                "description": "linear: evenly spaced copies along an axis; polar: copies around an axis; mirror: reflect across a plane.",
            },
            "feature_name": {
                "type": "string",
                "description": "Existing PartDesign feature internal name or label to replicate.",
            },
            "label": {"type": "string"},
            "direction": {
                "enum": ["X_Axis", "Y_Axis", "Z_Axis"],
                "type": "string",
                "description": "linear only: pattern direction (default X_Axis).",
            },
            "length": {
                "type": "number",
                "description": "linear only: total pattern length in mm (default 20).",
            },
            "axis": {
                "enum": ["X_Axis", "Y_Axis", "Z_Axis"],
                "type": "string",
                "description": "polar only: rotation axis (default Z_Axis).",
            },
            "angle": {
                "type": "number",
                "description": "polar only: total pattern angle in degrees (default 360).",
            },
            "occurrences": {
                "type": "integer",
                "description": (
                    "linear/polar: number of occurrences including the original "
                    "(default 2 linear, 4 polar; minimum 2)."
                ),
            },
            "mirror_plane": {
                "enum": ["XY_Plane", "XZ_Plane", "YZ_Plane"],
                "type": "string",
                "description": "mirror only: Body origin mirror plane (default YZ_Plane).",
            },
            "refine": {
                "type": "boolean",
                "description": "Refine the resulting shape by removing redundant edges (default false).",
            },
        },
        "required": ["operation", "feature_name"],
        "type": "object",
    },
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
}

_VALID_AXES = {"X_Axis", "Y_Axis", "Z_Axis"}
_VALID_PLANES = {"XY_Plane", "XZ_Plane", "YZ_Plane"}


def run(
    service: Any,
    operation: str = "",
    feature_name: str = "",
    label: str | None = None,
    direction: str | None = None,
    length: float = 20.0,
    axis: str | None = None,
    angle: float = 360.0,
    occurrences: int | None = None,
    mirror_plane: str | None = None,
    refine: bool = True,
) -> dict[str, Any]:
    op = str(operation or "").strip().lower()
    if op not in {"linear", "polar", "mirror"}:
        return {
            "ok": False,
            "error": "operation must be one of: linear, polar, mirror.",
            "requested_operation": operation,
        }
    feature = service._get_document_object(feature_name)
    if feature is None:
        return {"ok": False, "error": f"PartDesign feature not found: {feature_name}"}
    if not str(getattr(feature, "TypeId", "")).startswith("PartDesign::"):
        return {"ok": False, "error": f"Object is not a PartDesign feature: {feature_name}"}

    if op == "linear":
        requested_reference = str(direction or "X_Axis")
        if requested_reference not in _VALID_AXES:
            return {"ok": False, "error": "direction must be X_Axis, Y_Axis, or Z_Axis."}
        if float(length) <= 0:
            return {"ok": False, "error": "Linear pattern length must be positive."}
        effective_occurrences = 2 if occurrences is None else int(occurrences)
        if effective_occurrences < 2:
            return {"ok": False, "error": "Linear pattern occurrences must be at least 2."}
        display = "LinearPattern"
        effective_label = label or "VibeCAD Linear Pattern"
    elif op == "polar":
        requested_reference = str(axis or "Z_Axis")
        if requested_reference not in _VALID_AXES:
            return {"ok": False, "error": "axis must be X_Axis, Y_Axis, or Z_Axis."}
        if float(angle) <= 0 or float(angle) > 360:
            return {
                "ok": False,
                "error": "Polar pattern angle must be greater than 0 and no more than 360 degrees.",
            }
        effective_occurrences = 4 if occurrences is None else int(occurrences)
        if effective_occurrences < 2:
            return {"ok": False, "error": "Polar pattern occurrences must be at least 2."}
        display = "PolarPattern"
        effective_label = label or "VibeCAD Polar Pattern"
    else:
        requested_reference = str(mirror_plane or "YZ_Plane")
        if requested_reference not in _VALID_PLANES:
            return {"ok": False, "error": "mirror_plane must be XY_Plane, XZ_Plane, or YZ_Plane."}
        effective_occurrences = 0
        display = "Mirrored"
        effective_label = label or "VibeCAD Mirrored Feature"

    def _pattern() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target = service._get_document_object(feature.Name)
        if target is None:
            raise RuntimeError(f"PartDesign feature not found: {feature.Name}")
        body = service._partdesign_body_for_feature(target)
        if body is None:
            raise RuntimeError(f"No PartDesign Body found for {op} pattern.")
        reference_feature = service._partdesign_origin_feature(body, requested_reference)
        if reference_feature is None:
            raise RuntimeError(f"Body origin reference not found: {requested_reference}")
        body_shape_before = domain_runtime.shape_summary(body)
        details: dict[str, Any]
        if op == "linear":
            pattern = body.newObject("PartDesign::LinearPattern", "VibeCAD_LinearPattern")
            pattern.Label = effective_label
            pattern.Originals = [target]
            pattern.Direction = (reference_feature, [""])
            pattern.Length = float(length)
            pattern.Occurrences = effective_occurrences
            pattern.Refine = bool(refine)
            effect_operation = "linear_pattern"
            details = {
                "direction": requested_reference,
                "length": float(pattern.Length),
                "occurrences": int(pattern.Occurrences),
            }
        elif op == "polar":
            pattern = body.newObject("PartDesign::PolarPattern", "VibeCAD_PolarPattern")
            pattern.Label = effective_label
            pattern.Originals = [target]
            pattern.Axis = (reference_feature, [""])
            pattern.Angle = float(angle)
            pattern.Occurrences = effective_occurrences
            pattern.Refine = bool(refine)
            effect_operation = "polar_pattern"
            details = {
                "axis": requested_reference,
                "angle": float(pattern.Angle),
                "occurrences": int(pattern.Occurrences),
            }
        else:
            pattern = body.newObject("PartDesign::Mirrored", "VibeCAD_Mirrored")
            pattern.Label = effective_label
            pattern.Originals = [target]
            pattern.MirrorPlane = (reference_feature, [""])
            pattern.Refine = bool(refine)
            effect_operation = "mirror"
            details = {"mirror_plane": requested_reference}
        body.Tip = pattern
        doc.recompute()
        pattern_name = pattern.Name
        pattern_label = getattr(pattern, "Label", pattern_name)
        pattern_type = getattr(pattern, "TypeId", "")
        pattern_refine = bool(getattr(pattern, "Refine", False))
        pattern_solid_count = len(getattr(getattr(pattern, "Shape", None), "Solids", []) or [])
        pattern_volume = float(getattr(getattr(pattern, "Shape", None), "Volume", 0.0) or 0.0)
        effect = domain_runtime.finalize_partdesign_feature_effect(
            doc,
            body,
            pattern,
            effect_operation,
            body_shape_before,
        )
        return {
            "document": doc.Name,
            "body": body.Name,
            "base_feature": target.Name,
            "operation": op,
            "feature": pattern_name,
            "label": pattern_label,
            "type": pattern_type,
            "refine": pattern_refine,
            "solid_count": pattern_solid_count,
            "volume": pattern_volume,
            **details,
            **effect,
        }

    transaction = run_freecad_transaction(
        f"Create PartDesign {op} pattern from feature: "
        f"{getattr(feature, 'Label', feature.Name)}",
        _pattern,
    )
    response = domain_runtime.build_partdesign_feature_result(
        service,
        transaction,
        operation=display,
    )
    if not response.get("ok") and not bool(transaction.get("ok")):
        body = service._partdesign_body_for_feature(feature)
        response.setdefault(
            "error", transaction.get("error") or f"PartDesign {display} failed."
        )
        response["recoverable"] = True
        response["failure_context"] = {
            "feature": service._object_summary(feature),
            "body": service._partdesign_body_summary(body) if body is not None else None,
            "operation": op,
            "reference": requested_reference,
            "document_delta": transaction.get("document_delta"),
            "report_view_errors": transaction.get("report_view_errors"),
        }
        response["next_actions"] = [
            {
                "tool": "partdesign.get_bodies",
                "why": (
                    "Inspect the active Body, feature names, Tip, and origin references "
                    "before retrying the pattern."
                ),
            },
            {
                "tool": "core.get_object_properties",
                "arguments": {"object_name": feature.Name},
                "why": (
                    "Verify the selected original feature exists, is inside the expected "
                    "Body, and has a valid shape."
                ),
            },
            {
                "tool": "core.delete_object",
                "why": (
                    "If the failed pattern left invalid feature objects in the document "
                    "delta, delete those invalid objects before continuing."
                ),
            },
            {
                "tool": "partdesign.create_sketch",
                "why": (
                    "If the same native pattern fails repeatedly with an invalid-shape "
                    "error, create the replicated detail as a normal sketch-driven "
                    "PartDesign feature instead of retrying the unchanged pattern."
                ),
            },
        ]
    return response
