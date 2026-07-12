# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Draft circle or circular arc at an exact global position."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "draft.create_circle",
    "description": (
        "Create one native Draft circle or circular arc on the global XY plane "
        "at an exact center and radius. A full circle with make_face=true "
        "becomes a filled planar face usable as an extrusion profile; supply "
        "start/end angles to create an open arc instead."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "DraftWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "center": domain_runtime.vector_schema(
                "Exact global center of the circle in mm."
            ),
            "radius_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Circle radius in mm.",
            },
            "geometry": {
                "description": "Choose a full circle or an open arc; irrelevant fields are rejected.",
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "full_circle"},
                            "make_face": {
                                "type": "boolean",
                                "description": "Fill the full circle as a planar face.",
                            },
                        },
                        "required": ["type", "make_face"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "arc"},
                            "start_angle_degrees": {"type": "number"},
                            "end_angle_degrees": {"type": "number"},
                        },
                        "required": ["type", "start_angle_degrees", "end_angle_degrees"],
                        "additionalProperties": False,
                    },
                ],
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new object, e.g. 'BoltCircle'.",
            },
        },
        "required": ["center", "radius_mm", "geometry", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    center: dict[str, Any],
    radius_mm: float,
    geometry: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    radius = float(radius_mm)
    if radius <= 0:
        return _invalid("radius_mm must be greater than 0.")
    if not isinstance(geometry, dict):
        return _invalid("geometry must select full_circle or arc.")
    kind = str(geometry.get("type") or "")
    if kind not in {"full_circle", "arc"}:
        return _invalid("geometry.type must be full_circle or arc.")
    is_arc = kind == "arc"
    if is_arc:
        start = float(geometry["start_angle_degrees"])
        end = float(geometry["end_angle_degrees"])
        if abs(end - start) <= 1e-9:
            return _invalid(
                "arc start_angle_degrees and end_angle_degrees must differ."
            )
    else:
        start = 0.0
        end = 360.0
    make_face = bool(geometry.get("make_face", False))
    normalized_sweep = (end - start) % 360.0 if is_arc else 360.0
    if is_arc and normalized_sweep <= 1.0e-9:
        return _invalid(
            "An arc sweep cannot normalize to a full circle; use geometry.type=full_circle.",
            requested_start_degrees=start,
            requested_end_degrees=end,
            normalized_sweep_degrees=normalized_sweep,
        )

    def create() -> dict[str, Any]:
        import Draft
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        placement = App.Placement(domain_runtime.parse_vector(center), App.Rotation())
        if is_arc:
            obj = Draft.make_circle(
                radius,
                placement=placement,
                face=False,
                startangle=start,
                endangle=end,
            )
        else:
            obj = Draft.make_circle(
                radius,
                placement=placement,
                face=bool(make_face),
            )
        if obj is None:
            raise RuntimeError("Draft.make_circle did not create an object.")
        obj.Label = clean_label
        doc.recompute()
        return {
            "document": doc.Name,
            "feature": obj.Name,
            "feature_label": obj.Label,
            "feature_type": obj.TypeId,
            "radius_mm": radius,
            "is_arc": is_arc,
            "requested_geometry": dict(geometry),
            "normalized_requested_sweep_degrees": normalized_sweep,
            "actual_geometry": {
                "radius_mm": float(getattr(obj, "Radius", radius)),
                "first_angle_degrees": float(getattr(obj, "FirstAngle", start)),
                "last_angle_degrees": float(getattr(obj, "LastAngle", end)),
                "make_face": bool(getattr(obj, "MakeFace", False)),
            },
            "profile_diagnostics": domain_runtime.shape_profile_diagnostics(obj),
            "shape": domain_runtime.shape_summary(obj),
            "feature_state": domain_runtime.feature_state_summary(obj),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        profile = result.get("profile_diagnostics") or {}
        shape = result.get("shape") or {}
        checks = [
            {
                "name": "arc_or_circle_topology",
                "ok": int(shape.get("edges", 0)) == 1,
                "actual": shape,
            },
            {
                "name": "face_contract",
                "ok": (
                    int(shape.get("faces", 0)) == 0
                    if is_arc
                    else not make_face
                    or bool(profile.get("face_buildable"))
                    and int(shape.get("faces", 0)) == 1
                ),
                "actual": profile,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    kind = "arc" if is_arc else "circle"
    transaction = run_freecad_transaction(
        f"Create Draft {kind}: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation=f"create_{kind}")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
