# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Sketcher slot profile tool."""

from __future__ import annotations

import math
from typing import Any

from .common import active_response, get_sketch, no_sketch, run_freecad_transaction


TOOL_SPEC = {
    "name": "sketcher.add_slot",
    "description": (
        "Add one constrained slot profile: two straight sides and two "
        "semicircular ends. Prefer overall_length or center_distance."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "center_x": {"type": "number", "description": "Slot center X in mm."},
            "center_y": {"type": "number", "description": "Slot center Y in mm."},
            "length": {
                "type": "number",
                "description": (
                    "Backward-compatible alias in mm for overall end-to-end slot length, "
                    "not center-to-center arc distance."
                ),
            },
            "overall_length": {
                "type": "number",
                "description": "Overall end-to-end slot length in mm including both semicircular ends.",
            },
            "center_distance": {
                "type": "number",
                "description": "Distance in mm between the two semicircular arc centers.",
            },
            "length_mode": {
                "type": "string",
                "enum": ["overall", "center_to_center"],
                "description": (
                    "How to interpret length when overall_length and center_distance "
                    "are omitted. Defaults to overall."
                ),
            },
            "width": {"type": "number", "description": "Slot width (arc diameter) in mm."},
            "angle_degrees": {"type": "number", "description": "Slot axis rotation in degrees. Default 0."},
            "construction": {"type": "boolean", "description": "Create as construction geometry. Default false."},
        },
        "required": ["center_x", "center_y", "width"],
    },
}


def run(
    service: Any,
    sketch_name: str | None = None,
    center_x: float = 0.0,
    center_y: float = 0.0,
    length: float = 20.0,
    overall_length: float | None = None,
    center_distance: float | None = None,
    length_mode: str = "overall",
    width: float = 6.0,
    angle_degrees: float = 0.0,
    construction: bool = False,
) -> dict[str, Any]:
    width = float(width)
    if width <= 0:
        return {"ok": False, "error": "Slot width must be positive."}
    length_data = _resolve_slot_lengths(length, width, overall_length, center_distance, length_mode)
    if not length_data["ok"]:
        return length_data
    overall = float(length_data["overall_length"])
    center_to_center = float(length_data["center_distance"])
    if overall <= 0:
        return {"ok": False, "error": "Slot length and width must be positive."}
    if center_to_center <= 0:
        return {"ok": False, "error": "Slot center distance must be positive; overall length must be greater than width."}
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return no_sketch(sketch_name)

    def _add() -> dict[str, Any]:
        import FreeCAD as App
        import Part
        import Sketcher

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_geometry = len(getattr(target, "Geometry", []))
        before_constraints = len(getattr(target, "Constraints", []))
        angle = math.radians(float(angle_degrees))
        axis = App.Vector(math.cos(angle), math.sin(angle), 0.0)
        normal = App.Vector(-math.sin(angle), math.cos(angle), 0.0)
        center = App.Vector(float(center_x), float(center_y), 0.0)
        radius = width / 2.0
        straight_length = center_to_center
        left_center = center - axis * (straight_length / 2.0)
        right_center = center + axis * (straight_length / 2.0)
        left_top = left_center + normal * radius
        right_top = right_center + normal * radius
        right_bottom = right_center - normal * radius
        left_bottom = left_center - normal * radius
        left_outer = left_center - axis * radius
        right_outer = right_center + axis * radius
        target.addGeometry(
            [
                Part.LineSegment(left_top, right_top),
                Part.ArcOfCircle(right_top, right_center + axis * radius, right_bottom),
                Part.LineSegment(right_bottom, left_bottom),
                Part.ArcOfCircle(left_bottom, left_center - axis * radius, left_top),
            ],
            bool(construction),
        )
        base_index = before_geometry
        constraints = [
            Sketcher.Constraint("Block", base_index + 0),
            Sketcher.Constraint("Block", base_index + 1),
            Sketcher.Constraint("Block", base_index + 2),
            Sketcher.Constraint("Block", base_index + 3),
        ]
        target.addConstraint(constraints)
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        try:
            degrees_of_freedom = int(getattr(target, "DoF"))
        except Exception:
            degrees_of_freedom = None
        fully_constrained = degrees_of_freedom == 0 if degrees_of_freedom is not None else False
        profile_status = service._sketch_profile_status(target)
        if profile_status.get("ready_for_pocket"):
            suggested_next_actions = [
                {
                    "tool": "partdesign.extrude",
                    "arguments": {"operation": "pocket", "sketch_name": target.Name},
                    "why": "Use this fully constrained closed slot profile for a subtractive feature when it is mapped to a solid face.",
                },
            ]
        else:
            suggested_next_actions = [
                {
                    "tool": "sketcher.inspect_sketch",
                    "arguments": {"sketch_name": target.Name, "include": ["profile_deep"]},
                    "why": "Inspect why this slot profile is not yet feature-ready before creating a PartDesign feature.",
                },
                {
                    "tool": "sketcher.add_constraint",
                    "arguments": {
                        "sketch_name": target.Name,
                        "constraint_type": "Lock",
                        "first_geometry": base_index + 1,
                        "first_point": "center",
                        "x": float(right_center.x),
                        "y": float(right_center.y),
                    },
                    "why": "Lock one slot arc center to place the slot without using opaque block constraints.",
                },
                {
                    "tool": "sketcher.add_constraint",
                    "arguments": {
                        "sketch_name": target.Name,
                        "constraint_type": "Angle",
                        "first_geometry": base_index,
                        "value": float(angle_degrees),
                    },
                    "why": "Constrain the slot axis angle when orientation must be explicit.",
                },
            ]
        return {
            "sketch": target.Name,
            "geometry_index": base_index,
            "geometry_added": 4,
            "created_geometry_indices": [base_index, base_index + 1, base_index + 2, base_index + 3],
            "constraint_count_before": before_constraints,
            "constraint_index": before_constraints,
            "constraints_added": len(constraints),
            "created_constraint_indices": list(range(before_constraints, before_constraints + len(constraints))),
            "geometry_count_before": before_geometry,
            "geometry_count": len(getattr(target, "Geometry", [])),
            "constraint_count": len(getattr(target, "Constraints", [])),
            "center": [float(center_x), float(center_y)],
            "length": overall,
            "length_mode": "overall",
            "overall_length": overall,
            "center_distance": center_to_center,
            "straight_segment_length": center_to_center,
            "width": width,
            "radius": radius,
            "angle_degrees": float(angle_degrees),
            "arc_centers": {
                "left": [float(left_center.x), float(left_center.y)],
                "right": [float(right_center.x), float(right_center.y)],
            },
            "profile_points": {
                "left_top": [float(left_top.x), float(left_top.y)],
                "right_top": [float(right_top.x), float(right_top.y)],
                "right_bottom": [float(right_bottom.x), float(right_bottom.y)],
                "left_bottom": [float(left_bottom.x), float(left_bottom.y)],
                "left_outer": [float(left_outer.x), float(left_outer.y)],
                "right_outer": [float(right_outer.x), float(right_outer.y)],
            },
            "bounding_box": _bounding_box([left_top, right_top, right_bottom, left_bottom, left_outer, right_outer]),
            "degrees_of_freedom": degrees_of_freedom,
            "fully_constrained": fully_constrained,
            "construction": bool(construction),
            "suggested_next_actions": suggested_next_actions,
        }

    return active_response(service, sketch, run_freecad_transaction("Add Sketcher slot", _add))


def _resolve_slot_lengths(
    length: float,
    width: float,
    overall_length: float | None,
    center_distance: float | None,
    length_mode: str,
) -> dict[str, Any]:
    if overall_length is not None and center_distance is not None:
        return {"ok": False, "error": "Provide only one of overall_length or center_distance."}
    if overall_length is not None:
        overall = float(overall_length)
        center_to_center = overall - width
    elif center_distance is not None:
        center_to_center = float(center_distance)
        overall = center_to_center + width
    else:
        mode = str(length_mode or "overall").strip().lower()
        if mode not in {"overall", "center_to_center"}:
            return {"ok": False, "error": "length_mode must be 'overall' or 'center_to_center'."}
        raw_length = float(length)
        if mode == "center_to_center":
            center_to_center = raw_length
            overall = center_to_center + width
        else:
            overall = raw_length
            center_to_center = overall - width
    if overall <= 0 or center_to_center <= 0:
        return {
            "ok": False,
            "error": "Slot overall length must be greater than width, and center distance must be positive.",
            "overall_length": overall,
            "center_distance": center_to_center,
            "width": width,
        }
    return {
        "ok": True,
        "overall_length": overall,
        "center_distance": center_to_center,
    }


def _bounding_box(points):
    xs = [float(point.x) for point in points]
    ys = [float(point.y) for point in points]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }
