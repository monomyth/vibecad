# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Sketcher multi-kind geometry creation tool.

Consolidates the former add_line / add_point / add_arc / add_circle /
add_ellipse / add_bspline / add_polyline tools into one discriminated tool.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from .common import active_response, get_sketch, no_sketch, run_freecad_transaction, vector2


GEOMETRY_KINDS = ("line", "point", "arc", "circle", "ellipse", "bspline", "polyline")

TOOL_SPEC = {
    "name": "sketcher.add_geometry",
    "description": (
        "Add native Sketcher geometry. Use line/polyline only for straight "
        "segments. Use arc, ellipse, or bspline for curved silhouettes, "
        "airfoils, blades, choils, ergonomic profiles, and reference-image "
        "curves; later fillets do not replace authored curve geometry."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "kind": {
                "type": "string",
                "enum": list(GEOMETRY_KINDS),
                "description": "Geometry element kind to create.",
            },
            "points": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "description": "Canonical sketch-local 2D points [[x,y],...] in mm. Do not pass 3D/world points here; project them into the sketch plane first. Used by kind=line (exactly 2), point (exactly 1), polyline (>=2), bspline (>=2).",
            },
            "center": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Canonical sketch-local center [x,y] in mm. Do not pass 3D/world points here; project them into the sketch plane first. Used by kind=arc, circle, ellipse.",
            },
            "radius": {
                "type": "number",
                "description": "Radius in mm. Used by kind=arc, circle.",
            },
            "start_angle_degrees": {
                "type": "number",
                "description": "Arc start angle in degrees. Used by kind=arc.",
            },
            "end_angle_degrees": {
                "type": "number",
                "description": "Arc end angle in degrees. Used by kind=arc.",
            },
            "major_radius": {
                "type": "number",
                "description": "Ellipse major radius in mm. Used by kind=ellipse.",
            },
            "minor_radius": {
                "type": "number",
                "description": "Ellipse minor radius in mm. Used by kind=ellipse.",
            },
            "angle_degrees": {
                "type": "number",
                "description": "Ellipse major-axis rotation in degrees. Used by kind=ellipse.",
            },
            "closed": {
                "type": "boolean",
                "description": "Close the polyline back to its first point. Used by kind=polyline.",
            },
            "constrain_points": {
                "type": "boolean",
                "description": "When true (default), add native DistanceX/DistanceY constraints for each polyline point. Used by kind=polyline.",
            },
            "interpolate": {
                "type": "boolean",
                "description": "When true (default), interpolate the B-spline through the points; otherwise use them as poles. Used by kind=bspline.",
            },
            "periodic": {
                "type": "boolean",
                "description": "Build a periodic (closed) B-spline. Used by kind=bspline.",
            },
            "construction": {"type": "boolean"},
        },
        "required": ["kind"],
    },
}


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _validated_points(
    points: list[Any] | None,
    kind: str,
    minimum: int,
    exact: int | None = None,
) -> tuple[list[list[float]] | None, dict[str, Any] | None]:
    values = points or []
    if exact is not None and len(values) != exact:
        return None, _error(
            f"kind='{kind}' requires exactly {exact} point(s) in 'points'; got {len(values)}."
        )
    if len(values) < minimum:
        return None, _error(
            f"kind='{kind}' requires at least {minimum} points in 'points'; got {len(values)}."
        )
    normalized: list[list[float]] = []
    for index, raw in enumerate(values):
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None, _error(
                f"kind='{kind}' point {index} must be exactly [x, y] in sketch-local mm. "
                f"Got {raw!r}. Do not pass dicts or [x, y, z] to sketcher.add_geometry."
            )
        try:
            normalized.append([float(raw[0]), float(raw[1])])
        except (TypeError, ValueError):
            return None, _error(
                f"kind='{kind}' point {index} must contain numeric x and y coordinates."
            )
    return normalized, None


def _validated_center(
    center: Any, kind: str
) -> tuple[list[float] | None, dict[str, Any] | None]:
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        return None, _error(
            f"kind='{kind}' requires center=[x, y] in sketch-local mm. "
            f"Got {center!r}. Do not pass dicts or [x, y, z] to sketcher.add_geometry."
        )
    try:
        return [float(center[0]), float(center[1])], None
    except (TypeError, ValueError):
        return None, _error(f"kind='{kind}' center must contain numeric x and y coordinates.")


def run(
    service: Any,
    kind: str = "",
    sketch_name: str | None = None,
    points: list[list[float]] | None = None,
    center: list[float] | None = None,
    radius: float | None = None,
    start_angle_degrees: float | None = None,
    end_angle_degrees: float | None = None,
    major_radius: float | None = None,
    minor_radius: float | None = None,
    angle_degrees: float = 0.0,
    closed: bool = False,
    constrain_points: bool = True,
    interpolate: bool = True,
    periodic: bool = False,
    construction: bool = False,
) -> dict[str, Any]:
    kind_value = str(kind or "").strip().lower()
    if kind_value not in GEOMETRY_KINDS:
        return _error(
            f"Unknown geometry kind: {kind!r}. Expected one of: {', '.join(GEOMETRY_KINDS)}."
        )
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return no_sketch(sketch_name)

    builder: Callable[[Any], dict[str, Any]]

    if kind_value == "line":
        line_points, error = _validated_points(points, "line", 2, exact=2)
        if error is not None:
            return error
        assert line_points is not None

        def builder(target: Any) -> dict[str, Any]:
            import FreeCAD as App
            import Part

            geometry_index = target.addGeometry(
                Part.LineSegment(
                    App.Vector(line_points[0][0], line_points[0][1], 0.0),
                    App.Vector(line_points[1][0], line_points[1][1], 0.0),
                ),
                bool(construction),
            )
            return {
                "geometry_index": int(geometry_index),
                "geometry_added": 1,
                "start": line_points[0],
                "end": line_points[1],
            }

    elif kind_value == "point":
        point_values, error = _validated_points(points, "point", 1, exact=1)
        if error is not None:
            return error
        assert point_values is not None

        def builder(target: Any) -> dict[str, Any]:
            import FreeCAD as App
            import Part

            geometry_index = target.addGeometry(
                Part.Point(App.Vector(point_values[0][0], point_values[0][1], 0.0)),
                bool(construction),
            )
            return {
                "geometry_index": int(geometry_index),
                "geometry_added": 1,
                "point": point_values[0],
            }

    elif kind_value == "arc":
        center_value, error = _validated_center(center, "arc")
        if error is not None:
            return error
        assert center_value is not None
        if radius is None or float(radius) <= 0:
            return _error("kind='arc' requires a positive 'radius'.")
        if start_angle_degrees is None or end_angle_degrees is None:
            return _error("kind='arc' requires 'start_angle_degrees' and 'end_angle_degrees'.")
        if abs(float(end_angle_degrees) - float(start_angle_degrees)) < 1e-9:
            return _error("Arc start and end angles must differ.")

        def builder(target: Any) -> dict[str, Any]:
            import FreeCAD as App
            import Part

            circle = Part.Circle(
                App.Vector(center_value[0], center_value[1], 0.0),
                App.Vector(0.0, 0.0, 1.0),
                float(radius),
            )
            geometry_index = target.addGeometry(
                Part.ArcOfCircle(
                    circle,
                    math.radians(float(start_angle_degrees)),
                    math.radians(float(end_angle_degrees)),
                ),
                bool(construction),
            )
            return {
                "geometry_index": int(geometry_index),
                "geometry_added": 1,
                "center": center_value,
                "radius": float(radius),
                "start_angle_degrees": float(start_angle_degrees),
                "end_angle_degrees": float(end_angle_degrees),
            }

    elif kind_value == "circle":
        center_value, error = _validated_center(center, "circle")
        if error is not None:
            return error
        assert center_value is not None
        if radius is None or float(radius) <= 0:
            return _error("kind='circle' requires a positive 'radius'.")

        def builder(target: Any) -> dict[str, Any]:
            import FreeCAD as App
            import Part

            geometry_index = target.addGeometry(
                Part.Circle(
                    App.Vector(center_value[0], center_value[1], 0.0),
                    App.Vector(0.0, 0.0, 1.0),
                    float(radius),
                ),
                bool(construction),
            )
            return {
                "geometry_index": int(geometry_index),
                "created_geometry_indices": [int(geometry_index)],
                "geometry_added": 1,
                "center": center_value,
                "radius": float(radius),
                "suggested_next_actions": [
                    {
                        "tool": "sketcher.add_constraint",
                        "arguments": {
                            "sketch_name": target.Name,
                            "constraint_type": "Radius",
                            "first_geometry": int(geometry_index),
                            "value": float(radius),
                        },
                        "why": "Make the circle size a native editable radius constraint.",
                    },
                    {
                        "tool": "sketcher.add_constraint",
                        "arguments": {
                            "sketch_name": target.Name,
                            "constraint_type": "Lock",
                            "first_geometry": int(geometry_index),
                            "first_pos": 3,
                            "x": center_value[0],
                            "y": center_value[1],
                        },
                        "why": "Lock the circle center to exact sketch coordinates when the feature position is known.",
                    },
                ],
            }

    elif kind_value == "ellipse":
        center_value, error = _validated_center(center, "ellipse")
        if error is not None:
            return error
        assert center_value is not None
        if major_radius is None or minor_radius is None:
            return _error("kind='ellipse' requires 'major_radius' and 'minor_radius'.")
        if float(major_radius) <= 0 or float(minor_radius) <= 0:
            return _error("Ellipse radii must be positive.")
        if float(minor_radius) > float(major_radius):
            return _error("Ellipse 'minor_radius' must not exceed 'major_radius'.")

        def builder(target: Any) -> dict[str, Any]:
            import FreeCAD as App
            import Part

            ellipse = Part.Ellipse(
                App.Vector(center_value[0], center_value[1], 0.0),
                float(major_radius),
                float(minor_radius),
            )
            angle = math.radians(float(angle_degrees))
            ellipse.XAxis = App.Vector(math.cos(angle), math.sin(angle), 0.0)
            geometry_index = target.addGeometry(ellipse, bool(construction))
            return {
                "geometry_index": int(geometry_index),
                "geometry_added": 1,
                "center": center_value,
                "major_radius": float(major_radius),
                "minor_radius": float(minor_radius),
                "angle_degrees": float(angle_degrees),
            }

    elif kind_value == "bspline":
        spline_points, error = _validated_points(points, "bspline", 2)
        if error is not None:
            return error
        assert spline_points is not None

        def builder(target: Any) -> dict[str, Any]:
            import Part

            vectors = [
                vector2(raw, index, "B-spline") for index, raw in enumerate(spline_points)
            ]
            curve = Part.BSplineCurve()
            if bool(interpolate):
                curve.interpolate(vectors, PeriodicFlag=bool(periodic))
            else:
                curve.buildFromPoles(vectors, bool(periodic))
            geometry_index = target.addGeometry(curve, bool(construction))
            return {
                "geometry_index": int(geometry_index),
                "geometry_added": 1,
                "point_count": len(vectors),
                "interpolate": bool(interpolate),
                "periodic": bool(periodic),
            }

    else:  # polyline
        poly_points, error = _validated_points(points, "polyline", 2)
        if error is not None:
            return error
        assert poly_points is not None

        def builder(target: Any) -> dict[str, Any]:
            import Part
            import Sketcher

            before_geometry = len(getattr(target, "Geometry", []))
            before_constraints = len(getattr(target, "Constraints", []))
            vectors = [
                vector2(raw, index, "Polyline") for index, raw in enumerate(poly_points)
            ]
            segments = [
                Part.LineSegment(vectors[index], vectors[index + 1])
                for index in range(len(vectors) - 1)
            ]
            if bool(closed):
                segments.append(Part.LineSegment(vectors[-1], vectors[0]))
            target.addGeometry(segments, bool(construction))
            constraints = [
                Sketcher.Constraint(
                    "Coincident", before_geometry + index, 2, before_geometry + index + 1, 1
                )
                for index in range(len(segments) - 1)
            ]
            if bool(closed) and len(segments) > 1:
                constraints.append(
                    Sketcher.Constraint(
                        "Coincident", before_geometry + len(segments) - 1, 2, before_geometry, 1
                    )
                )
            dimensional_constraints = []
            point_constraint_targets = []
            if bool(constrain_points):
                for point_index, vector in enumerate(vectors):
                    if point_index < len(vectors) - 1:
                        geometry_index = before_geometry + point_index
                        point_pos = 1
                    else:
                        geometry_index = before_geometry + point_index - 1
                        point_pos = 2
                    dimensional_constraints.extend(
                        [
                            Sketcher.Constraint(
                                "DistanceX", geometry_index, point_pos, float(vector.x)
                            ),
                            Sketcher.Constraint(
                                "DistanceY", geometry_index, point_pos, float(vector.y)
                            ),
                        ]
                    )
                    point_constraint_targets.append(
                        {
                            "point_index": point_index,
                            "geometry_index": geometry_index,
                            "point_position": point_pos,
                            "x": float(vector.x),
                            "y": float(vector.y),
                        }
                    )
            constraints.extend(dimensional_constraints)
            if constraints:
                target.addConstraint(constraints)
            return {
                "geometry_index": before_geometry,
                "geometry_added": len(segments),
                "constraints_added": len(constraints),
                "coincident_constraints_added": len(constraints) - len(dimensional_constraints),
                "point_dimension_constraints_added": len(dimensional_constraints),
                "constraint_count_before": before_constraints,
                "constraint_count": len(getattr(target, "Constraints", [])),
                "closed": bool(closed),
                "constrain_points": bool(constrain_points),
                "point_constraint_targets": point_constraint_targets,
                "suggested_next_actions": [
                    {
                        "tool": "partdesign.extrude",
                        "arguments": {"operation": "pad", "sketch_name": target.Name},
                        "why": "Use this closed constrained profile for a native PartDesign pad when it represents an extruded section.",
                    },
                    {
                        "tool": "partdesign.revolve",
                        "arguments": {"operation": "revolve", "sketch_name": target.Name},
                        "why": "Use this closed constrained profile for a native PartDesign revolve when it represents a section about an axis.",
                    },
                    {
                        "tool": "partdesign.extrude",
                        "arguments": {"operation": "pocket", "sketch_name": target.Name},
                        "why": "Use this closed constrained profile for a native PartDesign pocket when it is mapped to an existing solid face.",
                    },
                ]
                if bool(closed)
                else [],
            }

    def _add() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_count = len(getattr(target, "Geometry", []))
        payload = builder(target)
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        result: dict[str, Any] = {
            "sketch": target.Name,
            "kind": kind_value,
            "geometry_count_before": before_count,
            "geometry_count": len(getattr(target, "Geometry", [])),
            "construction": bool(construction),
        }
        result.update(payload)
        return result

    return active_response(
        service,
        sketch,
        run_freecad_transaction(f"Add Sketcher {kind_value}", _add),
    )
