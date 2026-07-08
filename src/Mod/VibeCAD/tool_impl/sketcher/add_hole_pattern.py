# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Sketcher hole-pattern helper."""

from __future__ import annotations

import math
from typing import Any

from .common import (
    active_response,
    geometry_fingerprint,
    geometry_metadata,
    get_sketch,
    no_sketch,
    run_freecad_transaction,
    set_geometry_metadata,
)


TOOL_SPEC = {
    "name": "sketcher.add_hole_pattern",
    "description": (
        "Add a fully constrained Sketcher hole/bolt pattern for pockets, "
        "drilled holes, vents, and repeated circular cuts."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "pattern": {
                "type": "string",
                "enum": ["rectangular", "linear", "circular"],
                "description": "Pattern layout. Rectangular is the common centered bolt-pattern layout.",
            },
            "hole_diameter": {"type": "number", "description": "Hole diameter in millimeters."},
            "center_x": {"type": "number", "description": "Pattern center X in mm."},
            "center_y": {"type": "number", "description": "Pattern center Y in mm."},
            "count_x": {"type": "integer", "description": "Rectangular/linear column count."},
            "count_y": {"type": "integer", "description": "Rectangular row count."},
            "spacing_x": {"type": "number", "description": "Rectangular/linear X spacing in mm, or total span when count_x=2."},
            "spacing_y": {"type": "number", "description": "Rectangular Y spacing in mm, or total span when count_y=2."},
            "count": {"type": "integer", "description": "Linear or circular occurrence count."},
            "linear_angle_degrees": {"type": "number", "description": "Linear pattern direction angle in degrees."},
            "bolt_circle_diameter": {"type": "number", "description": "Circular pattern pitch-circle diameter in mm."},
            "start_angle_degrees": {"type": "number", "description": "Circular pattern first-hole angle in degrees."},
            "name_prefix": {"type": "string", "description": "Semantic geometry name prefix."},
            "construction": {"type": "boolean", "description": "Create holes as construction geometry. Default false."},
            "lock_centers": {"type": "boolean", "description": "Constrain hole centers with dimensional constraints. Default true."},
            "equal_radii": {"type": "boolean", "description": "Add Equal constraints so all holes share one radius. Default true."},
        },
        "required": ["hole_diameter"],
    },
}


def run(
    service: Any,
    sketch_name: str | None = None,
    pattern: str = "rectangular",
    hole_diameter: float = 4.5,
    center_x: float = 0.0,
    center_y: float = 0.0,
    count_x: int = 2,
    count_y: int = 2,
    spacing_x: float = 50.0,
    spacing_y: float = 20.0,
    count: int = 4,
    linear_angle_degrees: float = 0.0,
    bolt_circle_diameter: float | None = None,
    start_angle_degrees: float = 0.0,
    name_prefix: str = "hole",
    construction: bool = False,
    lock_centers: bool = True,
    equal_radii: bool = True,
) -> dict[str, Any]:
    diameter = float(hole_diameter)
    if diameter <= 0:
        return {"ok": False, "error": "hole_diameter must be positive."}
    clean_pattern = str(pattern or "rectangular").strip().lower()
    try:
        centers = _centers(
            clean_pattern,
            center_x=float(center_x),
            center_y=float(center_y),
            count_x=int(count_x),
            count_y=int(count_y),
            spacing_x=float(spacing_x),
            spacing_y=float(spacing_y),
            count=int(count),
            linear_angle_degrees=float(linear_angle_degrees),
            bolt_circle_diameter=bolt_circle_diameter,
            start_angle_degrees=float(start_angle_degrees),
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
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
        radius = diameter / 2.0
        created: list[int] = []
        constraints: list[Any] = []
        for x, y in centers:
            geometry_index = target.addGeometry(
                Part.Circle(
                    App.Vector(float(x), float(y), 0.0),
                    App.Vector(0.0, 0.0, 1.0),
                    radius,
                ),
                bool(construction),
            )
            created.append(int(geometry_index))
        if created:
            constraints.append(Sketcher.Constraint("Radius", created[0], radius))
        if equal_radii:
            constraints.extend(
                Sketcher.Constraint("Equal", created[0], index)
                for index in created[1:]
            )
        else:
            constraints.extend(
                Sketcher.Constraint("Radius", index, radius)
                for index in created[1:]
            )
        if lock_centers:
            for index, (x, y) in zip(created, centers):
                constraints.append(Sketcher.Constraint("DistanceX", index, 3, float(x)))
                constraints.append(Sketcher.Constraint("DistanceY", index, 3, float(y)))
        if constraints:
            target.addConstraint(constraints)
            constraint_index = before_constraints
        else:
            constraint_index = []
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        _name_geometry(service, target, created, str(name_prefix or "hole").strip() or "hole")
        if doc is not None:
            doc.recompute()
        return {
            "sketch": target.Name,
            "pattern": clean_pattern,
            "geometry_index": created[0] if created else None,
            "created_geometry_indices": created,
            "geometry_added": len(created),
            "constraint_index": _constraint_indices(constraint_index),
            "constraints_added": len(constraints),
            "created_constraint_indices": list(range(before_constraints, before_constraints + len(constraints))),
            "geometry_count_before": before_geometry,
            "geometry_count": len(getattr(target, "Geometry", [])),
            "constraint_count_before": before_constraints,
            "constraint_count": len(getattr(target, "Constraints", [])),
            "hole_diameter": diameter,
            "hole_radius": radius,
            "centers": [[float(x), float(y)] for x, y in centers],
            "semantic_handles": [
                f"name:{str(name_prefix or 'hole').strip() or 'hole'}_{offset}"
                for offset in range(1, len(created) + 1)
            ],
            "construction": bool(construction),
            "lock_centers": bool(lock_centers),
            "equal_radii": bool(equal_radii),
            "suggested_next_actions": [
                {
                    "tool": "partdesign.extrude",
                    "arguments": {"operation": "pocket", "sketch_name": target.Name},
                    "why": "Cut these closed hole profiles through the active solid.",
                },
                {
                    "tool": "partdesign.hole_from_sketch",
                    "arguments": {"sketch_name": target.Name, "diameter": diameter},
                    "why": "Create a native PartDesign Hole feature from this constrained hole sketch.",
                },
            ],
        }

    return active_response(
        service,
        sketch,
        run_freecad_transaction(f"Add Sketcher {clean_pattern} hole pattern", _add),
    )


def _centers(
    pattern: str,
    *,
    center_x: float,
    center_y: float,
    count_x: int,
    count_y: int,
    spacing_x: float,
    spacing_y: float,
    count: int,
    linear_angle_degrees: float,
    bolt_circle_diameter: float | None,
    start_angle_degrees: float,
) -> list[tuple[float, float]]:
    if pattern == "rectangular":
        if count_x <= 0 or count_y <= 0:
            raise ValueError("count_x and count_y must be positive for rectangular hole patterns.")
        if count_x == 1 and count_y == 1:
            return [(center_x, center_y)]
        if count_x > 1 and spacing_x <= 0:
            raise ValueError("spacing_x must be positive when count_x is greater than 1.")
        if count_y > 1 and spacing_y <= 0:
            raise ValueError("spacing_y must be positive when count_y is greater than 1.")
        x0 = center_x - spacing_x * (count_x - 1) / 2.0
        y0 = center_y - spacing_y * (count_y - 1) / 2.0
        return [
            (x0 + column * spacing_x, y0 + row * spacing_y)
            for row in range(count_y)
            for column in range(count_x)
        ]
    if pattern == "linear":
        if count <= 0:
            raise ValueError("count must be positive for linear hole patterns.")
        if count == 1:
            return [(center_x, center_y)]
        if spacing_x <= 0:
            raise ValueError("spacing_x must be positive for linear hole patterns.")
        angle = math.radians(linear_angle_degrees)
        dx = math.cos(angle) * spacing_x
        dy = math.sin(angle) * spacing_x
        start = -(count - 1) / 2.0
        return [
            (center_x + (start + offset) * dx, center_y + (start + offset) * dy)
            for offset in range(count)
        ]
    if pattern == "circular":
        if count < 2:
            raise ValueError("count must be at least 2 for circular hole patterns.")
        if bolt_circle_diameter is None or float(bolt_circle_diameter) <= 0:
            raise ValueError("bolt_circle_diameter must be positive for circular hole patterns.")
        radius = float(bolt_circle_diameter) / 2.0
        start = math.radians(start_angle_degrees)
        return [
            (
                center_x + math.cos(start + 2.0 * math.pi * offset / count) * radius,
                center_y + math.sin(start + 2.0 * math.pi * offset / count) * radius,
            )
            for offset in range(count)
        ]
    raise ValueError("pattern must be rectangular, linear, or circular.")


def _constraint_indices(raw_value: Any) -> int | list[int]:
    if isinstance(raw_value, int):
        return int(raw_value)
    if isinstance(raw_value, (list, tuple)):
        flattened: list[int] = []
        for item in raw_value:
            if isinstance(item, (list, tuple)):
                flattened.extend(int(value) for value in item)
            else:
                flattened.append(int(item))
        return flattened
    if raw_value is None:
        return []
    try:
        return int(raw_value)
    except Exception:
        return []


def _name_geometry(service: Any, sketch: Any, indices: list[int], prefix: str) -> None:
    metadata = geometry_metadata(sketch)
    names = metadata.setdefault("names", {})
    geometry = service.sketcher_summary(getattr(sketch, "Name", None)).get("geometry", [])
    for offset, index in enumerate(indices, start=1):
        if 0 <= index < len(geometry):
            names[f"{prefix}_{offset}"] = {
                "index": index,
                "fingerprint": geometry_fingerprint(geometry[index]),
            }
    set_geometry_metadata(sketch, metadata)
