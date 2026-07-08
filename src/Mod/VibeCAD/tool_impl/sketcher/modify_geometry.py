# SPDX-License-Identifier: LGPL-2.1-or-later

"""Consolidated native Sketcher local-geometry modification tool.

Replaces the retired single-operation tools ``sketcher.trim_geometry``,
``sketcher.extend_geometry``, ``sketcher.split_geometry``, and
``sketcher.fillet_corner`` with one operation-discriminated tool.
"""

from __future__ import annotations

import math
from typing import Any

from .common import (
    active_response,
    get_sketch,
    resolve_geometry_index,
    run_freecad_transaction,
    validate_geometry_index,
)
from .constrain_common import point_position


OPERATIONS = ("trim", "extend", "split", "fillet")

TOOL_SPEC = {
    "name": "sketcher.modify_geometry",
    "description": (
        "Trim, extend, split, fillet, or chamfer existing Sketcher geometry. "
        "Use to repair authored curves/segments; do not use fillets as a "
        "substitute for drawing required base curves."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(OPERATIONS),
                "description": "Which modification to perform.",
            },
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "geometry_index": {
                "type": "integer",
                "description": "Target geometry index (trim/extend/split; alias for first_geometry in fillet).",
            },
            "geometry_handle": {
                "type": "string",
                "description": "Semantic geometry handle for the target (trim/extend/split; alias for first_geometry_handle in fillet).",
            },
            "x": {"type": "number", "description": "trim/split: picked point X in sketch mm."},
            "y": {"type": "number", "description": "trim/split: picked point Y in sketch mm."},
            "endpoint": {
                "type": "string",
                "enum": ["start", "end"],
                "description": "extend: which endpoint to extend.",
            },
            "increment": {
                "type": "number",
                "description": "extend: signed extension length in mm.",
            },
            "first_geometry": {"type": "integer", "description": "fillet: first curve index."},
            "first_geometry_handle": {"type": "string", "description": "fillet: first curve handle."},
            "first_point": {
                "type": "string",
                "enum": ["start", "end"],
                "description": "fillet coincident-point mode: which endpoint of the first curve. Default end.",
            },
            "second_geometry": {"type": "integer", "description": "fillet two-curve mode: second curve index."},
            "second_geometry_handle": {"type": "string", "description": "fillet two-curve mode: second curve handle."},
            "first_reference_x": {"type": "number", "description": "fillet two-curve mode: reference point X in mm on first curve."},
            "first_reference_y": {"type": "number", "description": "fillet two-curve mode: reference point Y in mm on first curve."},
            "second_reference_x": {"type": "number", "description": "fillet two-curve mode: reference point X in mm on second curve."},
            "second_reference_y": {"type": "number", "description": "fillet two-curve mode: reference point Y in mm on second curve."},
            "radius": {"type": "number", "description": "fillet: fillet radius (or chamfer size) in mm. Must be positive."},
            "trim": {
                "type": "boolean",
                "description": "fillet: trim the original corner curves. Default true.",
            },
            "preserve_corner": {
                "type": "boolean",
                "description": "fillet: keep the corner point constrained. Default true.",
            },
            "chamfer": {
                "type": "boolean",
                "description": "fillet: create a straight chamfer instead of a rounded fillet. Default false.",
            },
        },
        "required": ["operation"],
    },
}


def run(
    service: Any,
    operation: str = "",
    sketch_name: str | None = None,
    geometry_index: int | None = None,
    geometry_handle: str | None = None,
    x: float | None = None,
    y: float | None = None,
    endpoint: str = "end",
    increment: float | None = None,
    first_geometry: int | None = None,
    first_geometry_handle: str | None = None,
    first_point: str = "end",
    second_geometry: int | None = None,
    second_geometry_handle: str | None = None,
    first_reference_x: float | None = None,
    first_reference_y: float | None = None,
    second_reference_x: float | None = None,
    second_reference_y: float | None = None,
    radius: float | None = None,
    trim: bool = True,
    preserve_corner: bool = True,
    chamfer: bool = False,
) -> dict[str, Any]:
    op = str(operation or "").strip().lower()
    if op not in OPERATIONS:
        return {
            "ok": False,
            "error": f"Unknown operation: {operation!r}. Valid operations: {', '.join(OPERATIONS)}.",
        }
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return {"ok": False, "error": "Sketch not found.", "requested": sketch_name}
    if op == "fillet":
        return _run_fillet(
            service,
            sketch,
            first_geometry if first_geometry is not None else geometry_index,
            first_geometry_handle or geometry_handle,
            first_point,
            second_geometry,
            second_geometry_handle,
            first_reference_x,
            first_reference_y,
            second_reference_x,
            second_reference_y,
            radius,
            trim,
            preserve_corner,
            chamfer,
        )
    try:
        index = resolve_geometry_index(service, sketch, geometry_index, geometry_handle)
    except (KeyError, ValueError, RuntimeError, TypeError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "geometry_index": geometry_index,
            "geometry_handle": geometry_handle,
        }
    invalid = validate_geometry_index(sketch, index)
    if invalid:
        return invalid
    if op in {"trim", "split"}:
        if x is None or y is None:
            return {"ok": False, "error": f"operation='{op}' requires x and y picked-point coordinates."}
        return _run_trim_or_split(service, sketch, op, index, geometry_handle, float(x), float(y))
    # op == "extend"
    clean_endpoint = str(endpoint or "").strip().lower()
    if clean_endpoint not in {"start", "end"}:
        return {"ok": False, "error": "endpoint must be start or end."}
    if increment is None:
        return {"ok": False, "error": "operation='extend' requires increment."}
    return _run_extend(service, sketch, index, geometry_handle, clean_endpoint, float(increment))


def _run_trim_or_split(
    service: Any,
    sketch: Any,
    op: str,
    index: int,
    geometry_handle: str | None,
    x: float,
    y: float,
) -> dict[str, Any]:
    def _mutate() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_geometry = len(getattr(target, "Geometry", []))
        before_constraints = len(getattr(target, "Constraints", []))
        if op == "trim":
            target.trim(index, App.Vector(x, y, 0.0))
        else:
            target.split(index, App.Vector(x, y, 0.0))
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        after_geometry = len(getattr(target, "Geometry", []))
        after_constraints = len(getattr(target, "Constraints", []))
        return {
            "sketch": target.Name,
            "operation": op,
            "geometry_index": index,
            "geometry_handle": geometry_handle or f"geometry:{index}",
            "picked_point": [x, y],
            "geometry_count_before": before_geometry,
            "geometry_count": after_geometry,
            "constraint_count_before": before_constraints,
            "constraint_count": after_constraints,
            "geometry_added": max(0, after_geometry - before_geometry),
            "constraints_added": max(0, after_constraints - before_constraints),
        }

    label = "Trim Sketcher geometry" if op == "trim" else "Split Sketcher geometry"
    return active_response(service, sketch, run_freecad_transaction(label, _mutate))


def _run_extend(
    service: Any,
    sketch: Any,
    index: int,
    geometry_handle: str | None,
    endpoint: str,
    increment: float,
) -> dict[str, Any]:
    def _extend() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_geometry = len(getattr(target, "Geometry", []))
        before_constraints = len(getattr(target, "Constraints", []))
        target.extend(index, increment, point_position(endpoint))
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        after_geometry = len(getattr(target, "Geometry", []))
        after_constraints = len(getattr(target, "Constraints", []))
        return {
            "sketch": target.Name,
            "operation": "extend",
            "geometry_index": index,
            "geometry_handle": geometry_handle or f"geometry:{index}",
            "endpoint": endpoint,
            "increment": increment,
            "geometry_count_before": before_geometry,
            "geometry_count": after_geometry,
            "constraint_count_before": before_constraints,
            "constraint_count": after_constraints,
        }

    return active_response(service, sketch, run_freecad_transaction("Extend Sketcher geometry", _extend))


def _run_fillet(
    service: Any,
    sketch: Any,
    first_geometry: int | None,
    first_geometry_handle: str | None,
    first_point: str,
    second_geometry: int | None,
    second_geometry_handle: str | None,
    first_reference_x: float | None,
    first_reference_y: float | None,
    second_reference_x: float | None,
    second_reference_y: float | None,
    radius: float | None,
    trim: bool,
    preserve_corner: bool,
    chamfer: bool,
) -> dict[str, Any]:
    if radius is None or float(radius) <= 0:
        return {"ok": False, "error": "operation='fillet' requires a positive radius."}
    try:
        first_index = resolve_geometry_index(service, sketch, first_geometry, first_geometry_handle)
        second_index = (
            resolve_geometry_index(service, sketch, second_geometry, second_geometry_handle)
            if second_geometry is not None or second_geometry_handle
            else None
        )
    except (KeyError, ValueError, RuntimeError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    invalid = validate_geometry_index(sketch, first_index)
    if invalid:
        return invalid
    if second_index is not None:
        invalid = validate_geometry_index(sketch, second_index)
        if invalid:
            return invalid
    resolved_references = _resolve_fillet_references(
        sketch,
        first_index,
        second_index,
        first_reference_x,
        first_reference_y,
        second_reference_x,
        second_reference_y,
        float(radius),
    )
    if not resolved_references.get("ok"):
        return resolved_references

    def _fillet() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_geometry = len(getattr(target, "Geometry", []))
        before_constraints = len(getattr(target, "Constraints", []))
        if second_index is None:
            target.fillet(
                first_index,
                point_position(first_point),
                float(radius),
                int(bool(trim)),
                bool(preserve_corner),
                bool(chamfer),
            )
            reference_mode = "coincident_point"
        else:
            first_ref = resolved_references["first_reference"]
            second_ref = resolved_references["second_reference"]
            target.fillet(
                first_index,
                second_index,
                App.Vector(float(first_ref[0]), float(first_ref[1]), 0.0),
                App.Vector(float(second_ref[0]), float(second_ref[1]), 0.0),
                float(radius),
                int(bool(trim)),
                bool(preserve_corner),
                bool(chamfer),
            )
            reference_mode = str(resolved_references["reference_mode"])
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        after_geometry = len(getattr(target, "Geometry", []))
        after_constraints = len(getattr(target, "Constraints", []))
        return {
            "sketch": target.Name,
            "operation": "fillet",
            "geometry_index": before_geometry,
            "geometry_added": max(0, after_geometry - before_geometry),
            "constraint_index": before_constraints,
            "constraints_added": max(0, after_constraints - before_constraints),
            "first_geometry": first_index,
            "first_geometry_handle": first_geometry_handle or f"geometry:{first_index}",
            "second_geometry": second_index,
            "second_geometry_handle": second_geometry_handle
            or (f"geometry:{second_index}" if second_index is not None else None),
            "reference_mode": reference_mode,
            "first_reference": resolved_references.get("first_reference"),
            "second_reference": resolved_references.get("second_reference"),
            "radius": float(radius),
            "trim": bool(trim),
            "preserve_corner": bool(preserve_corner),
            "chamfer": bool(chamfer),
            "geometry_count_before": before_geometry,
            "geometry_count": after_geometry,
            "constraint_count_before": before_constraints,
            "constraint_count": after_constraints,
        }

    return active_response(service, sketch, run_freecad_transaction("Create Sketcher fillet/chamfer", _fillet))


def _point_xy(point: Any) -> tuple[float, float] | None:
    try:
        return (float(point.x), float(point.y))
    except Exception:
        return None


def _geometry_endpoints(geometry: Any) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for role, attr in (("start", "StartPoint"), ("end", "EndPoint")):
        point = _point_xy(getattr(geometry, attr, None))
        if point is not None:
            endpoints.append({"role": role, "point": [point[0], point[1]]})
    return endpoints


def _distance2(a: list[float], b: list[float]) -> float:
    return (float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2


def _offset_reference_from_corner(geometry: Any, role: str, radius: float) -> list[float] | None:
    start = _point_xy(getattr(geometry, "StartPoint", None))
    end = _point_xy(getattr(geometry, "EndPoint", None))
    if start is None or end is None:
        return None
    corner = start if role == "start" else end
    opposite = end if role == "start" else start
    dx = opposite[0] - corner[0]
    dy = opposite[1] - corner[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    offset = min(max(float(radius) * 0.5, 0.01), length * 0.45)
    return [
        corner[0] + dx / length * offset,
        corner[1] + dy / length * offset,
    ]


def _endpoint_candidates(sketch: Any, first_index: int, second_index: int | None) -> dict[str, Any]:
    geometry = list(getattr(sketch, "Geometry", []) or [])
    result: dict[str, Any] = {
        "first_geometry": first_index,
        "first_endpoints": _geometry_endpoints(geometry[first_index]) if 0 <= first_index < len(geometry) else [],
    }
    if second_index is not None:
        result["second_geometry"] = second_index
        result["second_endpoints"] = (
            _geometry_endpoints(geometry[second_index]) if 0 <= second_index < len(geometry) else []
        )
    return result


def _resolve_fillet_references(
    sketch: Any,
    first_index: int,
    second_index: int | None,
    first_reference_x: float | None,
    first_reference_y: float | None,
    second_reference_x: float | None,
    second_reference_y: float | None,
    radius: float,
) -> dict[str, Any]:
    if second_index is None:
        return {"ok": True, "reference_mode": "coincident_point"}
    if None not in (first_reference_x, first_reference_y, second_reference_x, second_reference_y):
        return {
            "ok": True,
            "reference_mode": "two_curve_references",
            "first_reference": [float(first_reference_x), float(first_reference_y)],
            "second_reference": [float(second_reference_x), float(second_reference_y)],
        }
    geometry = list(getattr(sketch, "Geometry", []) or [])
    first_geo = geometry[first_index]
    second_geo = geometry[second_index]
    first_endpoints = _geometry_endpoints(first_geo)
    second_endpoints = _geometry_endpoints(second_geo)
    pairs: list[dict[str, Any]] = []
    tolerance2 = 1e-10
    for first in first_endpoints:
        for second in second_endpoints:
            distance2 = _distance2(first["point"], second["point"])
            if distance2 <= tolerance2:
                pairs.append(
                    {
                        "first_role": first["role"],
                        "second_role": second["role"],
                        "point": first["point"],
                        "distance": math.sqrt(distance2),
                    }
                )
    if len(pairs) == 1:
        pair = pairs[0]
        first_ref = _offset_reference_from_corner(first_geo, str(pair["first_role"]), radius)
        second_ref = _offset_reference_from_corner(second_geo, str(pair["second_role"]), radius)
        if first_ref is not None and second_ref is not None:
            return {
                "ok": True,
                "reference_mode": "inferred_shared_endpoint",
                "shared_endpoint": pair,
                "first_reference": first_ref,
                "second_reference": second_ref,
            }
    return {
        "ok": False,
        "error": (
            "operation='fillet' with second_geometry needs an unambiguous shared endpoint "
            "or explicit first_reference_x/y and second_reference_x/y pick points."
        ),
        "reference_mode": "needs_explicit_two_curve_references",
        "endpoint_candidates": _endpoint_candidates(sketch, first_index, second_index),
        "required_next_action": {
            "tool": "sketcher.modify_geometry",
            "arguments": {
                "operation": "fillet",
                "sketch_name": getattr(sketch, "Name", None),
                "first_geometry": first_index,
                "second_geometry": second_index,
                "first_reference_x": "<point on first curve near desired corner>",
                "first_reference_y": "<point on first curve near desired corner>",
                "second_reference_x": "<point on second curve near desired corner>",
                "second_reference_y": "<point on second curve near desired corner>",
                "radius": radius,
            },
            "why": "The tool cannot infer which side of the two curves to fillet.",
        },
    }
