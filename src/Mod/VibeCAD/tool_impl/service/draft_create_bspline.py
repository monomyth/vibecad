# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Draft B-spline through exact global points."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "draft.create_bspline",
    "description": (
        "Create one native Draft B-spline curve that passes through exact "
        "global points in order. Use it for smooth free-form curves; use "
        "draft.create_wire for straight-segment outlines. A closed spline with "
        "make_face=true becomes a filled planar face when the points are "
        "coplanar."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "DraftWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "items": domain_runtime.vector_schema(
                    "One exact global interpolation point in mm; the curve "
                    "passes through every point."
                ),
                "minItems": 3,
                "description": (
                    "Ordered interpolation points; the spline passes through "
                    "each one in sequence."
                ),
            },
            "closed": {
                "type": "boolean",
                "description": (
                    "True to close the spline smoothly from the last point back "
                    "to the first."
                ),
            },
            "make_face": {
                "type": "boolean",
                "description": (
                    "True to fill the closed spline into a planar face usable "
                    "as an extrusion profile; requires closed=true and coplanar "
                    "points."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new spline, e.g. 'BlendCurve'.",
            },
        },
        "required": ["points", "closed", "make_face", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    points: list[dict[str, Any]],
    closed: bool,
    make_face: bool,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if not isinstance(points, list) or len(points) < 3:
        return _invalid("points must contain at least 3 interpolation points.")
    if bool(make_face) and not bool(closed):
        return _invalid("make_face=true requires closed=true.")
    try:
        vectors = [domain_runtime.parse_vector(point) for point in points]
    except Exception as exc:
        return _invalid("points contains an invalid XYZ coordinate.", native_error=str(exc))
    point_diagnostics = domain_runtime.ordered_point_diagnostics(
        vectors,
        closed=bool(closed),
    )
    point_failure = bool(point_diagnostics.get("duplicate_points")) or not bool(
        (point_diagnostics.get("plane") or {}).get("planar")
    )
    if point_failure:
        return _invalid(
            "The interpolation points contain duplicates or are non-planar; no B-spline was created.",
            point_diagnostics=point_diagnostics,
        )
    if bool(make_face) and (point_diagnostics.get("plane") or {}).get("under_determined"):
        return _invalid(
            "A spline face requires at least three non-collinear interpolation points.",
            point_diagnostics=point_diagnostics,
        )

    def create() -> dict[str, Any]:
        import Draft
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        native_vectors = [domain_runtime.parse_vector(point) for point in points]
        obj = Draft.make_bspline(native_vectors, closed=bool(closed), face=bool(make_face))
        if obj is None:
            raise RuntimeError("Draft.make_bspline did not create an object.")
        obj.Label = clean_label
        doc.recompute()
        return {
            "document": doc.Name,
            "feature": obj.Name,
            "feature_label": obj.Label,
            "feature_type": obj.TypeId,
            "point_count": len(native_vectors),
            "requested_points": point_diagnostics,
            "interpolation_polygon_intersections_are_advisory": True,
            "actual_points": [
                domain_runtime.vector_values(point)
                for point in list(getattr(obj, "Points", []) or [])
            ],
            "closed_requested": bool(closed),
            "closed_actual": bool(getattr(obj, "Closed", False)),
            "make_face_requested": bool(make_face),
            "make_face_actual": bool(getattr(obj, "MakeFace", False)),
            "profile_diagnostics": domain_runtime.shape_profile_diagnostics(obj),
            "shape": domain_runtime.shape_summary(obj),
            "feature_state": domain_runtime.feature_state_summary(obj),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        profile = result.get("profile_diagnostics") or {}
        checks = [
            {
                "name": "closed_state",
                "ok": bool(result.get("closed_actual")) == bool(closed),
                "expected": bool(closed),
                "actual": result.get("closed_actual"),
            },
            {
                "name": "native_wire_validity",
                "ok": bool(profile.get("all_wires_valid")),
                "actual": profile,
            },
            {
                "name": "face_result",
                "ok": not bool(make_face)
                or bool(profile.get("face_buildable"))
                and int(profile.get("existing_face_count", 0)) > 0,
                "actual": profile,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Draft B-spline: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation="create_bspline")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
