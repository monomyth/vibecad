# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Sketcher constraint tool."""

from __future__ import annotations

from typing import Any

from .common import active_response, get_sketch, no_sketch, resolve_geometry_index, run_freecad_transaction
from .constrain_common import optional_point_position, point_role_enum


POINT_ROLE_ENUM = point_role_enum()

TOOL_SPEC = {
    "name": "sketcher.add_constraint",
    "description": (
        "Add one Sketcher constraint. Use dimensional constraints for sizes "
        "and locations, geometric constraints for relationships, and inspect "
        "solver/DoF before downstream features."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "constraint_type": {
                "type": "string",
                "enum": [
                    "Horizontal",
                    "Vertical",
                    "Parallel",
                    "Perpendicular",
                    "Tangent",
                    "Equal",
                    "Symmetric",
                    "Block",
                    "Coincident",
                    "PointOnObject",
                    "Distance",
                    "DistanceX",
                    "DistanceY",
                    "Radius",
                    "Diameter",
                    "Angle",
                    "Lock",
                ],
            },
            "first_geometry": {"type": "integer"},
            "first_geometry_handle": {"type": "string"},
            "first_pos": {"type": "integer"},
            "first_point": {
                "type": "string",
                "enum": POINT_ROLE_ENUM,
                "description": "Semantic point role on the first geometry (alternative to first_pos).",
            },
            "second_geometry": {"type": "integer"},
            "second_geometry_handle": {"type": "string"},
            "second_pos": {"type": "integer"},
            "second_point": {
                "type": "string",
                "enum": POINT_ROLE_ENUM,
                "description": "Semantic point role on the second geometry (alternative to second_pos).",
            },
            "third_geometry": {"type": "integer"},
            "third_geometry_handle": {"type": "string"},
            "third_pos": {"type": "integer"},
            "third_point": {
                "type": "string",
                "enum": POINT_ROLE_ENUM,
                "description": "Semantic point role on the third geometry (alternative to third_pos; used by Symmetric).",
            },
            "value": {
                "type": "number",
                "description": "Dimension value: mm for Distance/DistanceX/DistanceY/Radius/Diameter, degrees for Angle.",
            },
            "x": {"type": "number", "description": "Lock only: exact sketch X coordinate in mm."},
            "y": {"type": "number", "description": "Lock only: exact sketch Y coordinate in mm."},
        },
        "required": ["constraint_type"],
    },
}


SUPPORTED_CONSTRAINTS = {
    "Horizontal",
    "Vertical",
    "Parallel",
    "Perpendicular",
    "Tangent",
    "Equal",
    "Symmetric",
    "Block",
    "Coincident",
    "PointOnObject",
    "Distance",
    "DistanceX",
    "DistanceY",
    "Radius",
    "Diameter",
    "Angle",
    "Lock",
}


def _required_int(name: str, raw_value: int | None, constraint_type: str) -> int:
    if raw_value is None:
        raise ValueError(f"{name} is required for {constraint_type}.")
    return int(raw_value)


def _positive_value(name: str, raw_value: float | None, constraint_type: str) -> float:
    if raw_value is None:
        raise ValueError(f"{name} is required for {constraint_type}.")
    number = float(raw_value)
    if number <= 0:
        raise ValueError(f"{name} must be positive for {constraint_type}.")
    return number


def _number_value(name: str, raw_value: float | None, constraint_type: str) -> float:
    if raw_value is None:
        raise ValueError(f"{name} is required for {constraint_type}.")
    return float(raw_value)


def _constraint_indices(raw_value: Any) -> int | list[int]:
    if isinstance(raw_value, int):
        return int(raw_value)
    if isinstance(raw_value, (list, tuple)):
        flattened: list[int] = []
        for item in raw_value:
            if isinstance(item, int):
                flattened.append(int(item))
            elif isinstance(item, (list, tuple)):
                flattened.extend(int(value) for value in item)
            else:
                flattened.append(int(item))
        return flattened
    return int(raw_value)


def _resolve_point_roles(
    sketch: Any,
    constraint_type: str,
    first_pos: int | None,
    first_point: str | None,
    first_geometry_handle: str | None,
    first_geometry: int | None,
    second_pos: int | None,
    second_point: str | None,
    second_geometry_handle: str | None,
    second_geometry: int | None,
    third_pos: int | None,
    third_point: str | None,
    third_geometry_handle: str | None,
    third_geometry: int | None,
) -> tuple[int | None, int | None, int | None]:
    """Resolve semantic point roles (start/end/center/...) into raw Sketcher pos ints.

    Explicit *_pos integers always win. When only *_point roles are provided they are
    translated through the canonical role table, including the origin-handle shortcut.
    Point-anchored constraint types get explicit defaults when neither pos nor point
    is given.
    """
    defaults: dict[str, tuple[str | None, str | None, str | None]] = {
        "Coincident": ("end", "start", None),
        "PointOnObject": ("start", None, None),
        "Symmetric": ("start", "start", "whole"),
        "Lock": ("start", None, None),
        "Distance": ("start", "start", None),
        "DistanceX": ("start", "start", None),
        "DistanceY": ("start", "start", None),
        "Angle": ("whole", "whole", None),
    }
    first_default, second_default, third_default = defaults.get(constraint_type, (None, None, None))
    has_second = second_geometry is not None or bool(second_geometry_handle)
    # Distance and Angle accept a single-element form; only default the first pos when the
    # constraint is actually point-anchored (a role was named or a second target exists).
    first_needs_default = first_default is not None and (
        constraint_type not in {"Distance", "Angle"} or first_point is not None or has_second
    )
    if first_pos is None and (first_point is not None or first_needs_default):
        first_pos = optional_point_position(
            first_point,
            first_geometry_handle,
            _geometry_default_role(sketch, first_geometry, first_default or "start"),
            _geometry_kind(sketch, first_geometry),
        )
    if second_pos is None and (
        second_point is not None or (second_default is not None and has_second)
    ):
        second_pos = optional_point_position(
            second_point,
            second_geometry_handle,
            _geometry_default_role(sketch, second_geometry, second_default or "start"),
            _geometry_kind(sketch, second_geometry),
        )
    if third_pos is None and (third_point is not None or third_default is not None):
        third_pos = optional_point_position(
            third_point,
            third_geometry_handle,
            _geometry_default_role(sketch, third_geometry, third_default or "whole"),
            _geometry_kind(sketch, third_geometry),
        )
    return first_pos, second_pos, third_pos


def _geometry_kind(sketch: Any, geometry_index: int | None) -> str | None:
    if geometry_index is None or int(geometry_index) < 0:
        return None
    geometry = list(getattr(sketch, "Geometry", []) or [])
    index = int(geometry_index)
    if index >= len(geometry):
        return None
    return geometry[index].__class__.__name__


def _geometry_default_role(sketch: Any, geometry_index: int | None, default: str) -> str:
    kind = str(_geometry_kind(sketch, geometry_index) or "").lower()
    if kind in {"circle", "part::geomcircle", "ellipse", "part::geomellipse"} and default in {"start", "end"}:
        return "center"
    return default


def run(
    service: Any,
    sketch_name: str | None = None,
    constraint_type: str = "Horizontal",
    first_geometry: int | None = None,
    first_geometry_handle: str | None = None,
    first_pos: int | None = None,
    first_point: str | None = None,
    second_geometry: int | None = None,
    second_geometry_handle: str | None = None,
    second_pos: int | None = None,
    second_point: str | None = None,
    third_geometry: int | None = None,
    third_geometry_handle: str | None = None,
    third_pos: int | None = None,
    third_point: str | None = None,
    value: float | None = None,
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return no_sketch(sketch_name)
    clean_type = str(constraint_type or "").strip()
    if clean_type not in SUPPORTED_CONSTRAINTS:
        return {
            "ok": False,
            "error": f"Unsupported Sketcher constraint type: {clean_type}",
            "supported": sorted(SUPPORTED_CONSTRAINTS),
        }
    try:
        first_geometry = resolve_geometry_index(service, sketch, first_geometry, first_geometry_handle)
        if second_geometry is not None or second_geometry_handle:
            second_geometry = resolve_geometry_index(service, sketch, second_geometry, second_geometry_handle)
        if third_geometry is not None or third_geometry_handle:
            third_geometry = resolve_geometry_index(service, sketch, third_geometry, third_geometry_handle)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "first_geometry": first_geometry,
            "first_geometry_handle": first_geometry_handle,
            "second_geometry": second_geometry,
            "second_geometry_handle": second_geometry_handle,
            "third_geometry": third_geometry,
            "third_geometry_handle": third_geometry_handle,
        }
    try:
        first_pos, second_pos, third_pos = _resolve_point_roles(
            sketch,
            clean_type,
            first_pos,
            first_point,
            first_geometry_handle,
            first_geometry,
            second_pos,
            second_point,
            second_geometry_handle,
            second_geometry,
            third_pos,
            third_point,
            third_geometry_handle,
            third_geometry,
        )
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "constraint_type": clean_type,
            "first_geometry": first_geometry,
            "second_geometry": second_geometry,
            "third_geometry": third_geometry,
            "required_next_action": {
                "tool": "sketcher.inspect_sketch",
                "arguments": {"sketch_name": sketch.Name, "include": ["geometry"]},
                "why": "Inspect geometry types and use a valid point role such as start/end for lines or center for circles.",
            },
        }

    def _make_constraint():
        import FreeCAD as App
        import Sketcher

        first = _required_int("first_geometry", first_geometry, clean_type)
        if clean_type in {"Horizontal", "Vertical", "Block"}:
            return Sketcher.Constraint(clean_type, first)
        if clean_type in {"Parallel", "Perpendicular", "Tangent", "Equal"}:
            return Sketcher.Constraint(
                clean_type,
                first,
                _required_int("second_geometry", second_geometry, clean_type),
            )
        if clean_type == "Symmetric":
            return Sketcher.Constraint(
                clean_type,
                first,
                _required_int("first_pos", first_pos, clean_type),
                _required_int("second_geometry", second_geometry, clean_type),
                _required_int("second_pos", second_pos, clean_type),
                _required_int("third_geometry", third_geometry, clean_type),
                _required_int("third_pos", third_pos, clean_type),
            )
        if clean_type == "Coincident":
            return Sketcher.Constraint(
                clean_type,
                first,
                _required_int("first_pos", first_pos, clean_type),
                _required_int("second_geometry", second_geometry, clean_type),
                _required_int("second_pos", second_pos, clean_type),
            )
        if clean_type == "PointOnObject":
            return Sketcher.Constraint(
                clean_type,
                first,
                _required_int("first_pos", first_pos, clean_type),
                _required_int("second_geometry", second_geometry, clean_type),
            )
        if clean_type in {"Radius", "Diameter"}:
            return Sketcher.Constraint(clean_type, first, _positive_value("value", value, clean_type))
        if clean_type == "Distance":
            if first_pos is not None and second_geometry is not None and second_pos is not None:
                return Sketcher.Constraint(
                    clean_type,
                    first,
                    int(first_pos),
                    int(second_geometry),
                    int(second_pos),
                    _positive_value("value", value, clean_type),
                )
            return Sketcher.Constraint(clean_type, first, _positive_value("value", value, clean_type))
        if clean_type in {"DistanceX", "DistanceY"}:
            if second_geometry is not None and second_pos is not None:
                return Sketcher.Constraint(
                    clean_type,
                    first,
                    _required_int("first_pos", first_pos, clean_type),
                    int(second_geometry),
                    int(second_pos),
                    _number_value("value", value, clean_type),
                )
            return Sketcher.Constraint(
                clean_type,
                first,
                _required_int("first_pos", first_pos, clean_type),
                _number_value("value", value, clean_type),
            )
        if clean_type == "Angle":
            angle = App.Units.Quantity(float(_number_value("value", value, clean_type)), App.Units.Angle)
            if first_pos is not None and second_geometry is not None and second_pos is not None:
                return Sketcher.Constraint(clean_type, first, int(first_pos), int(second_geometry), int(second_pos), angle)
            return Sketcher.Constraint(clean_type, first, angle)
        if clean_type == "Lock":
            return [
                Sketcher.Constraint(
                    "DistanceX",
                    first,
                    _required_int("first_pos", first_pos, clean_type),
                    _number_value("x", x, clean_type),
                ),
                Sketcher.Constraint(
                    "DistanceY",
                    first,
                    _required_int("first_pos", first_pos, clean_type),
                    _number_value("y", y, clean_type),
                ),
            ]
        raise ValueError(f"Unsupported Sketcher constraint type: {clean_type}")

    def _add() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_count = len(getattr(target, "Constraints", []))
        constraint = _make_constraint()
        constraint_index = target.addConstraint(constraint)
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        return {
            "sketch": target.Name,
            "constraint_index": _constraint_indices(constraint_index),
            "constraint_type": clean_type,
            "constraint_count_before": before_count,
            "constraint_count": len(getattr(target, "Constraints", [])),
            "constraints_added": len(constraint) if isinstance(constraint, list) else 1,
        }

    return active_response(service, sketch, run_freecad_transaction(f"Add Sketcher {clean_type} constraint", _add))
