# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.find_subelements``.

Geometric topology resolver: finds faces/edges by geometric queries (surface
type, outward normal, radius, area/length, proximity) instead of trusting
positional names like ``Face3`` that shift when the feature history changes.
"""

from __future__ import annotations

import math
from typing import Any


TOOL_SPEC = {
    "contextual": True,
    "description": (
        "Find faces or edges by geometry instead of brittle names like Face3. "
        "Filter by type, normal, radius, area/length, or proximity. Use "
        "returned subelement names for dressups, drafts, shell openings, "
        "joints, and sketch attachment."
    ),
    "name": "partdesign.find_subelements",
    "parameters": {
        "properties": {
            "object_name": {
                "type": "string",
                "description": "Document object name or label whose shape is queried.",
            },
            "element_type": {
                "enum": ["face", "edge"],
                "type": "string",
                "description": "Subelement kind to search (default 'face').",
            },
            "geometry_type": {
                "type": "string",
                "description": (
                    "Geometry class filter. Faces: plane, cylinder, cone, "
                    "sphere, torus, bspline. Edges: line, circle, ellipse, "
                    "bspline."
                ),
            },
            "normal": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                },
                "description": (
                    "Planar faces only: required outward normal direction, "
                    "e.g. {\"z\": 1} for the top face. Compared within "
                    "normal_tolerance_degrees."
                ),
            },
            "normal_tolerance_degrees": {
                "type": "number",
                "description": "Angular tolerance for the normal filter (default 5).",
            },
            "radius": {
                "type": "number",
                "description": (
                    "Radius filter for cylindrical/spherical faces or "
                    "circular edges, matched within radius_tolerance."
                ),
            },
            "radius_tolerance": {
                "type": "number",
                "description": "Absolute radius tolerance in mm (default 0.01).",
            },
            "min_area": {
                "type": "number",
                "description": "Faces only: minimum area in mm^2.",
            },
            "max_area": {
                "type": "number",
                "description": "Faces only: maximum area in mm^2.",
            },
            "min_length": {
                "type": "number",
                "description": "Edges only: minimum length in mm.",
            },
            "max_length": {
                "type": "number",
                "description": "Edges only: maximum length in mm.",
            },
            "near_point": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                },
                "description": (
                    "Keep only subelements whose center of mass lies within "
                    "max_distance of this point."
                ),
            },
            "max_distance": {
                "type": "number",
                "description": "Distance limit in mm for near_point (default 1.0).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum matches to return (default 20).",
            },
        },
        "required": ["object_name"],
        "type": "object",
    },
    "safety": "READ",
}


_GEOMETRY_ALIASES = {
    "plane": "plane",
    "planar": "plane",
    "flat": "plane",
    "cylinder": "cylinder",
    "cylindrical": "cylinder",
    "cone": "cone",
    "conical": "cone",
    "sphere": "sphere",
    "spherical": "sphere",
    "torus": "toroid",
    "toroid": "toroid",
    "toroidal": "toroid",
    "bspline": "bspline",
    "nurbs": "bspline",
    "freeform": "bspline",
    "line": "line",
    "linear": "line",
    "straight": "line",
    "circle": "circle",
    "circular": "circle",
    "arc": "circle",
    "ellipse": "ellipse",
    "elliptical": "ellipse",
}

_KNOWN_GEOMETRY_PREFIXES = (
    "plane",
    "cylinder",
    "cone",
    "sphere",
    "toroid",
    "line",
    "circle",
    "ellipse",
)


def _canonical_geometry_type(class_name: str) -> str:
    lowered = str(class_name or "").lower()
    if "bspline" in lowered:
        return "bspline"
    for known in _KNOWN_GEOMETRY_PREFIXES:
        if lowered.startswith(known):
            return known
    return lowered


def _requested_geometry_type(value: str) -> str | None:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return None
    return _GEOMETRY_ALIASES.get(lowered, lowered)


def _vector_dict(vector: Any) -> dict[str, float]:
    return {
        "x": round(float(vector.x), 6),
        "y": round(float(vector.y), 6),
        "z": round(float(vector.z), 6),
    }


def _bounding_box_dict(bound_box: Any) -> dict[str, float]:
    return {
        "x_min": round(float(bound_box.XMin), 6),
        "x_max": round(float(bound_box.XMax), 6),
        "y_min": round(float(bound_box.YMin), 6),
        "y_max": round(float(bound_box.YMax), 6),
        "z_min": round(float(bound_box.ZMin), 6),
        "z_max": round(float(bound_box.ZMax), 6),
    }


def _surface_normal(face: Any) -> Any | None:
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
        normal = face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
    except Exception:
        return None
    if float(normal.Length) <= 1e-9:
        return None
    return normal.normalize()


def _outward_normal(shape: Any, face: Any) -> Any | None:
    """Best-effort outward normal for a planar face of a solid."""
    normal = _surface_normal(face)
    if normal is None:
        return None
    try:
        if float(getattr(shape, "Volume", 0.0) or 0.0) <= 0.0:
            return normal
        diagonal = float(shape.BoundBox.DiagonalLength)
        offset = max(diagonal * 1e-3, 1e-4)
        probe = face.CenterOfMass.add(
            type(normal)(normal.x * offset, normal.y * offset, normal.z * offset)
        )
        if shape.isInside(probe, offset * 0.1, False):
            return normal.multiply(-1.0)
    except Exception:
        return normal
    return normal


def _element_radius(geometry: Any) -> float | None:
    radius = getattr(geometry, "Radius", None)
    if radius is None:
        return None
    try:
        return float(radius)
    except (TypeError, ValueError):
        return None


def run(
    service: Any,
    object_name: str = "",
    element_type: str = "face",
    geometry_type: str | None = None,
    normal: dict[str, Any] | None = None,
    normal_tolerance_degrees: float = 5.0,
    radius: float | None = None,
    radius_tolerance: float = 0.01,
    min_area: float | None = None,
    max_area: float | None = None,
    min_length: float | None = None,
    max_length: float | None = None,
    near_point: dict[str, Any] | None = None,
    max_distance: float = 1.0,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    import FreeCAD as App

    kind = str(element_type or "face").strip().lower()
    if kind not in {"face", "edge"}:
        return {
            "found": False,
            "error": "element_type must be 'face' or 'edge'.",
            "requested_element_type": element_type,
        }
    obj = service._get_document_object(object_name)
    if obj is None:
        return {"found": False, "error": f"Object not found: {object_name}"}
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return {
            "found": False,
            "error": f"Object has no shape geometry: {object_name}",
        }

    requested_type = _requested_geometry_type(geometry_type or "")
    wanted_normal = None
    if normal is not None:
        wanted_normal = App.Vector(
            float(normal.get("x", 0.0) or 0.0),
            float(normal.get("y", 0.0) or 0.0),
            float(normal.get("z", 0.0) or 0.0),
        )
        if float(wanted_normal.Length) <= 1e-9:
            return {"found": False, "error": "normal must be a non-zero direction."}
        wanted_normal.normalize()
    target_point = None
    if near_point is not None:
        target_point = App.Vector(
            float(near_point.get("x", 0.0) or 0.0),
            float(near_point.get("y", 0.0) or 0.0),
            float(near_point.get("z", 0.0) or 0.0),
        )
    cos_tolerance = math.cos(math.radians(max(float(normal_tolerance_degrees), 0.0)))
    max_matches = max(1, min(int(limit), 100))

    if kind == "face":
        elements = list(getattr(shape, "Faces", []) or [])
        name_prefix = "Face"
    else:
        elements = list(getattr(shape, "Edges", []) or [])
        name_prefix = "Edge"

    matches: list[dict[str, Any]] = []
    for index, element in enumerate(elements):
        if kind == "face":
            geometry = getattr(element, "Surface", None)
            measure = float(getattr(element, "Area", 0.0) or 0.0)
        else:
            geometry = getattr(element, "Curve", None)
            measure = float(getattr(element, "Length", 0.0) or 0.0)
        canonical = _canonical_geometry_type(type(geometry).__name__ if geometry else "")
        if requested_type is not None and canonical != requested_type:
            continue
        if kind == "face":
            if min_area is not None and measure < float(min_area):
                continue
            if max_area is not None and measure > float(max_area):
                continue
        else:
            if min_length is not None and measure < float(min_length):
                continue
            if max_length is not None and measure > float(max_length):
                continue
        element_radius = _element_radius(geometry)
        if radius is not None:
            if element_radius is None:
                continue
            if abs(element_radius - float(radius)) > float(radius_tolerance):
                continue
        outward = None
        if kind == "face" and canonical == "plane":
            outward = _outward_normal(shape, element)
        if wanted_normal is not None:
            if outward is None:
                continue
            if float(outward.dot(wanted_normal)) < cos_tolerance:
                continue
        center = element.CenterOfMass
        if target_point is not None:
            if float(center.distanceToPoint(target_point)) > float(max_distance):
                continue
        entry: dict[str, Any] = {
            "name": f"{name_prefix}{index + 1}",
            "geometry_type": canonical,
            "center_of_mass": _vector_dict(center),
            "bounding_box": _bounding_box_dict(element.BoundBox),
        }
        if kind == "face":
            entry["area"] = round(measure, 6)
        else:
            entry["length"] = round(measure, 6)
        if outward is not None:
            entry["outward_normal"] = _vector_dict(outward)
        if element_radius is not None:
            entry["radius"] = round(element_radius, 6)
        matches.append(entry)
        if len(matches) >= max_matches:
            break

    return {
        "found": True,
        "object": service._document_object_summary(obj),
        "element_type": kind,
        "total_elements": len(elements),
        "match_count": len(matches),
        "matches": matches,
        "filters": {
            "geometry_type": requested_type,
            "normal": _vector_dict(wanted_normal) if wanted_normal is not None else None,
            "radius": float(radius) if radius is not None else None,
            "near_point": _vector_dict(target_point) if target_point is not None else None,
        },
    }
