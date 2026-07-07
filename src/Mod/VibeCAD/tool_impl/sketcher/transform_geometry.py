# SPDX-License-Identifier: LGPL-2.1-or-later

"""Consolidated native Sketcher bulk geometry transform tool.

Replaces the retired single-operation tools ``sketcher.transform_geometry``
(translate), ``sketcher.copy_geometry``, ``sketcher.mirror_geometry``,
``sketcher.offset_geometry``, and ``sketcher.rectangular_array`` with one
operation-discriminated tool.
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


OPERATIONS = ("translate", "copy", "mirror", "offset", "array")

TOOL_SPEC = {
    "name": "sketcher.transform_geometry",
    "description": (
        "Transform one or more native Sketcher geometry elements with one of five operations. "
        "operation='translate': move selected geometry by a 2D delta (requires dx, dy). "
        "operation='copy': duplicate selected geometry with a 2D offset (requires dx, dy). "
        "operation='mirror': mirror selected geometry across an explicit 2D axis (requires "
        "axis_point_x/y and axis_direction_x/y; keep_original controls copy vs in-place). "
        "operation='offset': create offset copies of line/circle/arc geometry (requires "
        "distance; side selects direction). "
        "operation='array': create a rectangular array of selected geometry (requires columns, "
        "rows, column_dx/dy, row_dx/dy). Equivalent to Sketcher's move/copy/symmetry/offset/"
        "array workbench tools. Operates on whole elements — use sketcher.modify_geometry to "
        "trim/extend/split/fillet curves and sketcher.move_point to drag one point."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(OPERATIONS),
                "description": "Which transform to perform.",
            },
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "geometry_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Geometry indices to transform.",
            },
            "geometry_handles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Semantic geometry handles to transform.",
            },
            "dx": {"type": "number", "description": "translate/copy: X delta in mm."},
            "dy": {"type": "number", "description": "translate/copy: Y delta in mm."},
            "axis_point_x": {"type": "number", "description": "mirror: X in mm of a point on the mirror axis."},
            "axis_point_y": {"type": "number", "description": "mirror: Y in mm of a point on the mirror axis."},
            "axis_direction_x": {"type": "number", "description": "mirror: X component of the axis direction."},
            "axis_direction_y": {"type": "number", "description": "mirror: Y component of the axis direction."},
            "keep_original": {
                "type": "boolean",
                "description": "mirror: when true (default), keep originals and add mirrored copies; when false, mirror in place.",
            },
            "distance": {"type": "number", "description": "offset: signed offset distance in mm."},
            "side": {
                "type": "string",
                "enum": ["left", "right", "outward", "inward"],
                "description": (
                    "offset: direction semantics. Lines use left/right relative to start-to-end; "
                    "circles and arcs use outward/inward radius change. Default left."
                ),
            },
            "construction": {
                "type": "boolean",
                "description": "offset: optional construction flag for created offset geometry.",
            },
            "columns": {"type": "integer", "description": "array: number of columns including the original column."},
            "rows": {"type": "integer", "description": "array: number of rows including the original row."},
            "column_dx": {"type": "number", "description": "array: X offset in mm between adjacent columns."},
            "column_dy": {"type": "number", "description": "array: Y offset in mm between adjacent columns."},
            "row_dx": {"type": "number", "description": "array: X offset in mm between adjacent rows."},
            "row_dy": {"type": "number", "description": "array: Y offset in mm between adjacent rows."},
            "include_original": {
                "type": "boolean",
                "description": "array: when true, also creates a duplicate at row 0 column 0.",
            },
        },
        "required": ["operation"],
    },
}


def run(
    service: Any,
    operation: str = "",
    sketch_name: str | None = None,
    geometry_indices: list[int] | None = None,
    geometry_handles: list[str] | None = None,
    dx: float | None = None,
    dy: float | None = None,
    axis_point_x: float | None = None,
    axis_point_y: float | None = None,
    axis_direction_x: float | None = None,
    axis_direction_y: float | None = None,
    keep_original: bool = True,
    distance: float | None = None,
    side: str = "left",
    construction: bool | None = None,
    columns: int | None = None,
    rows: int | None = None,
    column_dx: float | None = None,
    column_dy: float | None = None,
    row_dx: float | None = None,
    row_dy: float | None = None,
    include_original: bool = False,
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
    try:
        indices = _resolve_indices(service, sketch, geometry_indices, geometry_handles)
    except (KeyError, ValueError, RuntimeError, TypeError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "geometry_indices": geometry_indices,
            "geometry_handles": geometry_handles,
        }
    if not indices:
        return {"ok": False, "error": "At least one geometry index or handle is required."}
    for index in indices:
        invalid = validate_geometry_index(sketch, index)
        if invalid:
            return invalid

    if op in {"translate", "copy"}:
        if dx is None or dy is None:
            return {"ok": False, "error": f"operation='{op}' requires dx and dy."}
        if op == "translate":
            return _run_translate(service, sketch, indices, geometry_handles, float(dx), float(dy))
        return _run_copy(service, sketch, indices, geometry_handles, float(dx), float(dy))
    if op == "mirror":
        if None in (axis_point_x, axis_point_y, axis_direction_x, axis_direction_y):
            return {
                "ok": False,
                "error": "operation='mirror' requires axis_point_x, axis_point_y, axis_direction_x, and axis_direction_y.",
            }
        if abs(float(axis_direction_x)) < 1e-12 and abs(float(axis_direction_y)) < 1e-12:
            return {"ok": False, "error": "Mirror axis direction vector must be non-zero."}
        return _run_mirror(
            service,
            sketch,
            indices,
            geometry_handles,
            float(axis_point_x),
            float(axis_point_y),
            float(axis_direction_x),
            float(axis_direction_y),
            bool(keep_original),
        )
    if op == "offset":
        if distance is None:
            return {"ok": False, "error": "operation='offset' requires distance."}
        if abs(float(distance)) < 1e-12:
            return {"ok": False, "error": "Offset distance must be non-zero."}
        return _run_offset(service, sketch, indices, geometry_handles, float(distance), side, construction)
    # op == "array"
    if None in (columns, rows, column_dx, column_dy, row_dx, row_dy):
        return {
            "ok": False,
            "error": "operation='array' requires columns, rows, column_dx, column_dy, row_dx, and row_dy.",
        }
    if int(columns) < 1 or int(rows) < 1:
        return {"ok": False, "error": "columns and rows must be at least 1."}
    if int(columns) == 1 and int(rows) == 1 and not include_original:
        return {
            "ok": False,
            "error": "Array would create no new geometry; increase rows/columns or set include_original.",
        }
    return _run_array(
        service,
        sketch,
        indices,
        geometry_handles,
        int(columns),
        int(rows),
        float(column_dx),
        float(column_dy),
        float(row_dx),
        float(row_dy),
        bool(include_original),
    )


def _run_translate(
    service: Any,
    sketch: Any,
    indices: list[int],
    geometry_handles: list[str] | None,
    dx: float,
    dy: float,
) -> dict[str, Any]:
    def _transform() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        for index in indices:
            target.moveGeometry(index, 0, App.Vector(dx, dy, 0.0), 1)
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        geometry = [
            service._geometry_summary(list(getattr(target, "Geometry", []))[index], index, target)
            for index in indices
        ]
        return {
            "sketch": target.Name,
            "operation": "translate",
            "modified_geometry_indices": indices,
            "geometry_indices": indices,
            "geometry_handles": geometry_handles or [f"geometry:{index}" for index in indices],
            "dx": dx,
            "dy": dy,
            "geometry": geometry,
            "old_to_new_geometry_index": {
                str(index): index for index in range(len(getattr(target, "Geometry", [])))
            },
        }

    return active_response(service, sketch, run_freecad_transaction("Transform Sketcher geometry", _transform))


def _run_copy(
    service: Any,
    sketch: Any,
    indices: list[int],
    geometry_handles: list[str] | None,
    dx: float,
    dy: float,
) -> dict[str, Any]:
    def _copy() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_count = len(getattr(target, "Geometry", []))
        created: list[int] = []
        vector = App.Vector(dx, dy, 0.0)
        source_geometry = list(getattr(target, "Geometry", []))
        for index in indices:
            source = source_geometry[index]
            copied = source.copy()
            copied.translate(vector)
            copy_construction = bool(target.getConstruction(index))
            created.append(int(target.addGeometry(copied, copy_construction)))
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        geometry = [
            service._geometry_summary(list(getattr(target, "Geometry", []))[index], index, target)
            for index in created
        ]
        return {
            "sketch": target.Name,
            "operation": "copy",
            "source_geometry_indices": indices,
            "source_geometry_handles": geometry_handles or [f"geometry:{index}" for index in indices],
            "created_geometry_indices": created,
            "geometry_index": created[0] if created else None,
            "geometry_added": len(created),
            "geometry_count_before": before_count,
            "geometry_count": len(getattr(target, "Geometry", [])),
            "dx": dx,
            "dy": dy,
            "geometry": geometry,
            "old_to_new_geometry_index": {
                str(index): index for index in range(len(getattr(target, "Geometry", [])))
            },
        }

    return active_response(service, sketch, run_freecad_transaction("Copy Sketcher geometry", _copy))


def _run_mirror(
    service: Any,
    sketch: Any,
    indices: list[int],
    geometry_handles: list[str] | None,
    axis_point_x: float,
    axis_point_y: float,
    axis_direction_x: float,
    axis_direction_y: float,
    keep_original: bool,
) -> dict[str, Any]:
    def _mirror() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_count = len(getattr(target, "Geometry", []))
        created: list[int] = []
        modified: list[int] = []
        source_geometry = list(getattr(target, "Geometry", []))
        axis_point = App.Vector(axis_point_x, axis_point_y, 0.0)
        axis_direction = App.Vector(axis_direction_x, axis_direction_y, 0.0)
        for index in indices:
            mirrored = source_geometry[index].copy()
            mirrored.mirror(axis_point, axis_direction)
            if keep_original:
                mirror_construction = bool(target.getConstruction(index))
                created.append(int(target.addGeometry(mirrored, mirror_construction)))
            else:
                target.setGeometry(index, mirrored)
                modified.append(index)
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        all_geometry = list(getattr(target, "Geometry", []))
        affected = created if keep_original else modified
        geometry = [service._geometry_summary(all_geometry[index], index, target) for index in affected]
        return {
            "sketch": target.Name,
            "operation": "mirror",
            "source_geometry_indices": indices,
            "source_geometry_handles": geometry_handles or [f"geometry:{index}" for index in indices],
            "created_geometry_indices": created,
            "modified_geometry_indices": modified,
            "geometry_index": created[0] if created else None,
            "geometry_added": len(created),
            "geometry_count_before": before_count,
            "geometry_count": len(all_geometry),
            "mirror_axis": {
                "point": [axis_point_x, axis_point_y],
                "direction": [axis_direction_x, axis_direction_y],
            },
            "keep_original": keep_original,
            "geometry": geometry,
            "old_to_new_geometry_index": {
                str(index): index for index in range(len(all_geometry))
            },
        }

    return active_response(service, sketch, run_freecad_transaction("Mirror Sketcher geometry", _mirror))


def _run_offset(
    service: Any,
    sketch: Any,
    indices: list[int],
    geometry_handles: list[str] | None,
    distance: float,
    side: str,
    construction: bool | None,
) -> dict[str, Any]:
    def _offset() -> dict[str, Any]:
        import FreeCAD as App
        import Part

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_count = len(getattr(target, "Geometry", []))
        created: list[int] = []
        source_geometry = list(getattr(target, "Geometry", []))
        normalized_side = str(side or "left").strip().lower()
        for index in indices:
            source = source_geometry[index]
            offset = _offset_one(App, Part, source, distance, normalized_side)
            if offset is None:
                raise RuntimeError(
                    "Offset currently supports LineSegment, Circle, and ArcOfCircle geometry; "
                    f"geometry {index} is {type(source).__name__}."
                )
            offset_construction = (
                bool(target.getConstruction(index)) if construction is None else bool(construction)
            )
            created.append(int(target.addGeometry(offset, offset_construction)))
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        all_geometry = list(getattr(target, "Geometry", []))
        geometry = [service._geometry_summary(all_geometry[index], index, target) for index in created]
        return {
            "sketch": target.Name,
            "operation": "offset",
            "source_geometry_indices": indices,
            "source_geometry_handles": geometry_handles or [f"geometry:{index}" for index in indices],
            "created_geometry_indices": created,
            "geometry_index": created[0] if created else None,
            "geometry_added": len(created),
            "geometry_count_before": before_count,
            "geometry_count": len(all_geometry),
            "distance": distance,
            "side": normalized_side,
            "construction": construction,
            "geometry": geometry,
            "old_to_new_geometry_index": {
                str(index): index for index in range(len(all_geometry))
            },
        }

    return active_response(service, sketch, run_freecad_transaction("Offset Sketcher geometry", _offset))


def _run_array(
    service: Any,
    sketch: Any,
    indices: list[int],
    geometry_handles: list[str] | None,
    columns: int,
    rows: int,
    column_dx: float,
    column_dy: float,
    row_dx: float,
    row_dy: float,
    include_original: bool,
) -> dict[str, Any]:
    def _array() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_count = len(getattr(target, "Geometry", []))
        created: list[int] = []
        source_geometry = list(getattr(target, "Geometry", []))
        source_handles = geometry_handles or [f"geometry:{index}" for index in indices]
        placements: list[dict[str, Any]] = []
        for row in range(rows):
            for column in range(columns):
                if row == 0 and column == 0 and not include_original:
                    continue
                dx = column_dx * column + row_dx * row
                dy = column_dy * column + row_dy * row
                vector = App.Vector(dx, dy, 0.0)
                for index in indices:
                    source = source_geometry[index]
                    copied = source.copy()
                    copied.translate(vector)
                    array_construction = bool(target.getConstruction(index))
                    new_index = int(target.addGeometry(copied, array_construction))
                    created.append(new_index)
                    placements.append(
                        {
                            "row": row,
                            "column": column,
                            "source_geometry_index": index,
                            "created_geometry_index": new_index,
                            "dx": dx,
                            "dy": dy,
                        }
                    )
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        geometry = [
            service._geometry_summary(list(getattr(target, "Geometry", []))[index], index, target)
            for index in created
        ]
        return {
            "sketch": target.Name,
            "operation": "array",
            "source_geometry_indices": indices,
            "source_geometry_handles": source_handles,
            "created_geometry_indices": created,
            "geometry_index": created[0] if created else None,
            "geometry_added": len(created),
            "geometry_count_before": before_count,
            "geometry_count": len(getattr(target, "Geometry", [])),
            "columns": columns,
            "rows": rows,
            "column_vector": [column_dx, column_dy],
            "row_vector": [row_dx, row_dy],
            "include_original": include_original,
            "placements": placements,
            "geometry": geometry,
            "old_to_new_geometry_index": {
                str(index): index for index in range(len(getattr(target, "Geometry", [])))
            },
        }

    return active_response(service, sketch, run_freecad_transaction("Create Sketcher rectangular array", _array))


def _offset_one(App: Any, Part: Any, source: Any, distance: float, side: str) -> Any:
    name = type(source).__name__
    if name == "LineSegment":
        start = source.StartPoint
        end = source.EndPoint
        dx = float(end.x) - float(start.x)
        dy = float(end.y) - float(start.y)
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            raise RuntimeError("Cannot offset a zero-length line segment.")
        sign = -1.0 if side == "right" else 1.0
        vector = App.Vector(-dy / length * distance * sign, dx / length * distance * sign, 0.0)
        return Part.LineSegment(start + vector, end + vector)
    if name == "Circle":
        radius = _offset_radius(float(source.Radius), distance, side)
        return Part.Circle(source.Center, source.Axis, radius)
    if name == "ArcOfCircle":
        radius = _offset_radius(float(source.Radius), distance, side)
        circle = Part.Circle(source.Center, source.Axis, radius)
        return Part.ArcOfCircle(circle, float(source.FirstParameter), float(source.LastParameter))
    return None


def _offset_radius(radius: float, distance: float, side: str) -> float:
    if side == "inward":
        result = radius - abs(distance)
    elif side == "outward":
        result = radius + abs(distance)
    else:
        result = radius + distance
    if result <= 1e-12:
        raise RuntimeError("Offset radius must remain positive.")
    return result


def _resolve_indices(
    service: Any,
    sketch: Any,
    geometry_indices: list[int] | None,
    geometry_handles: list[str] | None,
) -> list[int]:
    resolved: list[int] = []
    for raw_index in geometry_indices or []:
        index = int(raw_index)
        if index not in resolved:
            resolved.append(index)
    for handle in geometry_handles or []:
        index = resolve_geometry_index(service, sketch, None, handle)
        if index not in resolved:
            resolved.append(index)
    return resolved
