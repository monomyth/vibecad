# SPDX-License-Identifier: LGPL-2.1-or-later

"""AI-native profile authoring tool."""

from __future__ import annotations

from typing import Any

from .cad_common import append_design_memory, backend_ok, call_backend, find_body_name


CURVE_KINDS = {"arc", "circle", "ellipse", "bspline", "slot", "hole_pattern"}
FREECAD_CURVE_TYPES = {
    "ArcOfCircle",
    "ArcOfEllipse",
    "ArcOfHyperbola",
    "ArcOfParabola",
    "BSplineCurve",
    "BezierCurve",
    "Circle",
    "Ellipse",
    "Hyperbola",
    "Parabola",
}
ENTITY_KINDS = (
    "line",
    "polyline",
    "arc",
    "circle",
    "ellipse",
    "bspline",
    "point",
    "rectangle",
    "slot",
    "hole_pattern",
)
MULTI_ENTITY_NAME_SUFFIXES = {
    "rectangle": ("top", "right", "bottom", "left"),
    "slot": ("top_side", "right_end", "bottom_side", "left_end"),
}


TOOL_SPEC = {
    "name": "cad.create_profile",
    "description": (
        "Create a named Sketcher profile for a component using explicit entity "
        "types. Lines are straight; arcs/ellipses/bsplines/slots/hole patterns "
        "are real curves. Use this instead of low-level sketcher tools for "
        "normal profile work."
    ),
    "safety": "SAFE_WRITE",
    "parameters": {
        "type": "object",
        "properties": {
            "component_name": {"type": "string"},
            "body_name": {"type": "string"},
            "profile_name": {"type": "string"},
            "purpose": {"type": "string"},
            "support": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["origin_plane", "datum_plane", "face"],
                    },
                    "plane": {
                        "type": "string",
                        "enum": ["XY_Plane", "XZ_Plane", "YZ_Plane"],
                    },
                    "object": {"type": "string"},
                    "subelement": {"type": "string"},
                    "normal": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "z": {"type": "number"},
                        },
                    },
                },
            },
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": list(ENTITY_KINDS)},
                        "points": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                        },
                        "center": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "radius": {"type": "number"},
                        "start_angle_degrees": {"type": "number"},
                        "end_angle_degrees": {"type": "number"},
                        "major_radius": {"type": "number"},
                        "minor_radius": {"type": "number"},
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                        "center_x": {"type": "number"},
                        "center_y": {"type": "number"},
                        "overall_length": {"type": "number"},
                        "center_distance": {"type": "number"},
                        "hole_diameter": {"type": "number"},
                        "pattern": {
                            "type": "string",
                            "enum": ["rectangular", "linear", "circular"],
                        },
                        "count_x": {"type": "integer"},
                        "count_y": {"type": "integer"},
                        "spacing_x": {"type": "number"},
                        "spacing_y": {"type": "number"},
                        "count": {"type": "integer"},
                        "linear_angle_degrees": {"type": "number"},
                        "bolt_circle_diameter": {"type": "number"},
                        "name_prefix": {"type": "string"},
                        "lock_centers": {"type": "boolean"},
                        "equal_radii": {"type": "boolean"},
                        "angle_degrees": {"type": "number"},
                        "closed": {"type": "boolean"},
                        "periodic": {"type": "boolean"},
                        "interpolate": {"type": "boolean"},
                        "construction": {"type": "boolean"},
                    },
                    "required": ["kind"],
                },
            },
            "constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Native Sketcher constraint type, e.g. Coincident, Tangent, Radius, DistanceX, DistanceY, Lock, Block.",
                        },
                        "entity": {
                            "type": "string",
                            "description": "First entity name from entities[].name.",
                        },
                        "point": {
                            "type": "string",
                            "description": "Point role on entity: start, end, center, midpoint, whole.",
                        },
                        "second_entity": {"type": "string"},
                        "second_point": {"type": "string"},
                        "third_entity": {"type": "string"},
                        "third_point": {"type": "string"},
                        "value": {"type": "number"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                    "required": ["type", "entity"],
                },
            },
            "requires_curves": {"type": "boolean"},
            "close_after": {"type": "boolean"},
        },
        "required": ["component_name", "profile_name", "purpose", "entities"],
    },
}


def _result_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    transaction = result.get("transaction")
    if not isinstance(transaction, dict):
        return None
    payload = transaction.get("result")
    if not isinstance(payload, dict):
        return None
    return payload


def _created_geometry_indices(result: dict[str, Any]) -> list[int]:
    payload = _result_payload(result)
    if not isinstance(payload, dict):
        return []
    raw_indices = payload.get("created_geometry_indices")
    if isinstance(raw_indices, list):
        indices: list[int] = []
        for item in raw_indices:
            try:
                indices.append(int(item))
            except (TypeError, ValueError):
                return []
        return indices
    base_index = payload.get("geometry_index")
    geometry_added = payload.get("geometry_added")
    try:
        base = int(base_index)
    except (TypeError, ValueError):
        return []
    try:
        count = int(geometry_added)
    except (TypeError, ValueError):
        count = 1
    if count <= 0:
        return []
    return [base + offset for offset in range(count)]


def _missing_entity_fields(entity: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    missing = []
    for field in fields:
        if field not in entity or entity[field] is None:
            missing.append(field)
    return missing


def _copy_present(source: dict[str, Any], target: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        if field in source:
            target[field] = source[field]


def _entity_name_sequence(kind: str, base_name: str, count: int) -> list[str]:
    if count <= 0:
        return []
    if count == 1:
        return [base_name]
    suffixes = MULTI_ENTITY_NAME_SUFFIXES.get(kind, ())
    if len(suffixes) == count:
        return [f"{base_name}_{suffix}" for suffix in suffixes]
    return [f"{base_name}_{offset}" for offset in range(1, count + 1)]


def _apply_entity_names(
    service: Any,
    sketch_name: str,
    result: dict[str, Any],
    *,
    kind: str,
    base_name: str,
) -> dict[str, Any]:
    clean_name = str(base_name or "").strip()
    if not clean_name or not backend_ok(result):
        return result
    indices = _created_geometry_indices(result)
    if not indices:
        return {
            **result,
            "ok": False,
            "error": f"Could not name profile entity {clean_name!r}: backend returned no geometry indices.",
        }
    names = _entity_name_sequence(kind, clean_name, len(indices))
    name_results = []
    for geometry_index, geometry_name in zip(indices, names):
        name_results.append(
            call_backend(
                service,
                "sketcher.set_geometry_name",
                sketch_name=sketch_name,
                geometry_index=geometry_index,
                geometry_name=geometry_name,
            )
        )
    result["name_results"] = name_results
    result["semantic_handles"] = [f"name:{name}" for name in names]
    if not all(backend_ok(item) for item in name_results):
        error = _first_backend_error(name_results) or "Sketcher semantic geometry naming failed."
        result["ok"] = False
        result["error"] = f"Could not name profile entity {clean_name!r}: {error}"
    return result


def _body_for_profile(service: Any, component_name: str, body_name: str | None) -> tuple[str | None, dict[str, Any] | None]:
    requested_body = find_body_name(service, body_name) if body_name else None
    if requested_body:
        return requested_body, None
    component_body = find_body_name(service, component_name)
    if component_body:
        return component_body, None
    created = call_backend(service, "partdesign.create_body", label=component_name)
    body = created.get("active_body") if isinstance(created, dict) else None
    return (str(body) if body else None), created


def _add_rectangle_entity(service: Any, sketch_name: str, entity: dict[str, Any]) -> dict[str, Any]:
    missing = _missing_entity_fields(
        entity,
        ("width", "height", "center_x", "center_y", "construction"),
    )
    if missing:
        return {
            "ok": False,
            "error": f"rectangle entity requires: {', '.join(missing)}.",
        }
    args: dict[str, Any] = {"sketch_name": sketch_name}
    _copy_present(entity, args, ("width", "height", "center_x", "center_y", "construction"))
    result = call_backend(service, "sketcher.draw_rectangle", **args)
    return _apply_entity_names(
        service,
        sketch_name,
        result,
        kind="rectangle",
        base_name=str(entity.get("name") or ""),
    )


def _add_slot_entity(service: Any, sketch_name: str, entity: dict[str, Any]) -> dict[str, Any]:
    missing = _missing_entity_fields(entity, ("center_x", "center_y", "width"))
    if missing:
        return {"ok": False, "error": f"slot entity requires: {', '.join(missing)}."}
    has_overall = "overall_length" in entity and entity["overall_length"] is not None
    has_center_distance = "center_distance" in entity and entity["center_distance"] is not None
    if has_overall == has_center_distance:
        return {
            "ok": False,
            "error": "slot entity requires exactly one of overall_length or center_distance.",
        }
    args: dict[str, Any] = {"sketch_name": sketch_name}
    _copy_present(
        entity,
        args,
        (
            "center_x",
            "center_y",
            "overall_length",
            "center_distance",
            "width",
            "angle_degrees",
            "construction",
        ),
    )
    result = call_backend(service, "sketcher.add_slot", **args)
    return _apply_entity_names(
        service,
        sketch_name,
        result,
        kind="slot",
        base_name=str(entity.get("name") or ""),
    )


def _add_hole_pattern_entity(service: Any, sketch_name: str, entity: dict[str, Any]) -> dict[str, Any]:
    missing = _missing_entity_fields(entity, ("pattern", "hole_diameter", "center_x", "center_y"))
    if missing:
        return {
            "ok": False,
            "error": f"hole_pattern entity requires: {', '.join(missing)}.",
        }
    pattern = str(entity.get("pattern") or "").strip().lower()
    if pattern == "rectangular":
        missing = _missing_entity_fields(entity, ("count_x", "count_y"))
        if missing:
            return {
                "ok": False,
                "error": f"rectangular hole_pattern entity requires: {', '.join(missing)}.",
            }
        try:
            count_x = int(entity["count_x"])
            count_y = int(entity["count_y"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "rectangular hole_pattern count_x and count_y must be integers."}
        spacing_fields = []
        if count_x > 1 and ("spacing_x" not in entity or entity["spacing_x"] is None):
            spacing_fields.append("spacing_x")
        if count_y > 1 and ("spacing_y" not in entity or entity["spacing_y"] is None):
            spacing_fields.append("spacing_y")
        if spacing_fields:
            return {
                "ok": False,
                "error": f"rectangular hole_pattern entity requires: {', '.join(spacing_fields)}.",
            }
    elif pattern == "linear":
        missing = _missing_entity_fields(entity, ("count", "spacing_x", "linear_angle_degrees"))
        if missing:
            return {
                "ok": False,
                "error": f"linear hole_pattern entity requires: {', '.join(missing)}.",
            }
    elif pattern == "circular":
        missing = _missing_entity_fields(entity, ("count", "bolt_circle_diameter", "start_angle_degrees"))
        if missing:
            return {
                "ok": False,
                "error": f"circular hole_pattern entity requires: {', '.join(missing)}.",
            }
    else:
        return {
            "ok": False,
            "error": "hole_pattern entity pattern must be rectangular, linear, or circular.",
        }
    args: dict[str, Any] = {"sketch_name": sketch_name}
    _copy_present(
        entity,
        args,
        (
            "pattern",
            "hole_diameter",
            "center_x",
            "center_y",
            "count_x",
            "count_y",
            "spacing_x",
            "spacing_y",
            "count",
            "linear_angle_degrees",
            "bolt_circle_diameter",
            "start_angle_degrees",
            "name_prefix",
            "construction",
            "lock_centers",
            "equal_radii",
        ),
    )
    if "name_prefix" not in args and str(entity.get("name") or "").strip():
        args["name_prefix"] = str(entity["name"]).strip()
    return call_backend(service, "sketcher.add_hole_pattern", **args)


def _add_entity(service: Any, sketch_name: str, entity: dict[str, Any]) -> dict[str, Any]:
    kind = str(entity.get("kind") or "").strip().lower()
    if kind not in ENTITY_KINDS:
        return {"ok": False, "error": f"Unknown profile entity kind: {kind!r}."}
    if kind == "rectangle":
        return _add_rectangle_entity(service, sketch_name, entity)
    if kind == "slot":
        return _add_slot_entity(service, sketch_name, entity)
    if kind == "hole_pattern":
        return _add_hole_pattern_entity(service, sketch_name, entity)
    args: dict[str, Any] = {
        "sketch_name": sketch_name,
        "kind": kind,
        "construction": bool(entity.get("construction", False)),
    }
    for key in (
        "points",
        "center",
        "radius",
        "start_angle_degrees",
        "end_angle_degrees",
        "major_radius",
        "minor_radius",
        "angle_degrees",
        "closed",
        "periodic",
        "interpolate",
    ):
        if key in entity:
            args[key] = entity[key]
    result = call_backend(service, "sketcher.add_geometry", **args)
    return _apply_entity_names(
        service,
        sketch_name,
        result,
        kind=kind,
        base_name=str(entity.get("name") or ""),
    )


def _constraint_args(sketch_name: str, item: dict[str, Any]) -> dict[str, Any]:
    clean_type = str(item.get("type") or "").strip()
    entity = str(item.get("entity") or "").strip()
    if not clean_type:
        raise ValueError("Constraint type is required.")
    if not entity:
        raise ValueError("Constraint entity is required.")
    args: dict[str, Any] = {
        "sketch_name": sketch_name,
        "constraint_type": clean_type,
        "first_geometry_handle": f"name:{entity}",
    }
    if str(item.get("point") or "").strip():
        args["first_point"] = str(item["point"]).strip()
    second = str(item.get("second_entity") or "").strip()
    if second:
        args["second_geometry_handle"] = f"name:{second}"
        if str(item.get("second_point") or "").strip():
            args["second_point"] = str(item["second_point"]).strip()
    third = str(item.get("third_entity") or "").strip()
    if third:
        args["third_geometry_handle"] = f"name:{third}"
        if str(item.get("third_point") or "").strip():
            args["third_point"] = str(item["third_point"]).strip()
    for key in ("value", "x", "y"):
        if key in item:
            args[key] = item[key]
    return args


def _add_constraint(service: Any, sketch_name: str, item: dict[str, Any]) -> dict[str, Any]:
    try:
        args = _constraint_args(sketch_name, item)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "constraint": item}
    return call_backend(service, "sketcher.add_constraint", **args)


def _actual_geometry(inspect_result: dict[str, Any]) -> list[dict[str, Any]]:
    geometry = (
        inspect_result.get("geometry")
        if isinstance(inspect_result, dict)
        else None
    )
    if not isinstance(geometry, list):
        return []
    return [item for item in geometry if isinstance(item, dict)]


def _actual_geometry_types(inspect_result: dict[str, Any]) -> list[str]:
    return [
        str(item.get("type") or "")
        for item in _actual_geometry(inspect_result)
        if str(item.get("type") or "").strip()
    ]


def _actual_profile_curve_geometry(inspect_result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in _actual_geometry(inspect_result)
        if str(item.get("type") or "") in FREECAD_CURVE_TYPES
        and not bool(item.get("construction"))
    ]


def _first_backend_error(results: list[dict[str, Any]]) -> str | None:
    for result in results:
        if backend_ok(result):
            continue
        error = str(result.get("error") or "").strip()
        if error:
            return error
        transaction = result.get("transaction")
        if isinstance(transaction, dict):
            error = str(transaction.get("error") or "").strip()
            if error:
                return error
    return None


def run(
    service: Any,
    component_name: str,
    profile_name: str,
    purpose: str,
    entities: list[dict[str, Any]],
    body_name: str | None = None,
    support: dict[str, Any] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    requires_curves: bool = False,
    close_after: bool = False,
) -> dict[str, Any]:
    clean_component = str(component_name or "").strip()
    clean_profile = str(profile_name or "").strip()
    clean_purpose = str(purpose or "").strip()
    if not clean_component:
        return {"ok": False, "error": "component_name is required."}
    if not clean_profile:
        return {"ok": False, "error": "profile_name is required."}
    if not clean_purpose:
        return {"ok": False, "error": "purpose is required."}
    if not isinstance(entities, list) or not entities:
        return {"ok": False, "error": "entities must be a non-empty list."}

    body, body_result = _body_for_profile(service, clean_component, body_name)
    if not body:
        return {
            "ok": False,
            "error": f"No PartDesign Body available for component {clean_component!r}.",
            "body_result": body_result,
        }

    support = support or {}
    support_type = str(support.get("type") or "origin_plane")
    sketch_result = call_backend(
        service,
        "partdesign.create_sketch",
        body_name=body,
        label=clean_profile,
        support_type=support_type,
        plane=str(support.get("plane") or "XY_Plane"),
        support_object=support.get("object"),
        subelement=support.get("subelement"),
        normal=support.get("normal"),
    )
    sketch_name = sketch_result.get("active_sketch") or sketch_result.get("sketch")
    if not sketch_name:
        transaction = sketch_result.get("transaction")
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict):
            sketch_name = transaction["result"].get("sketch")
    if not backend_ok(sketch_result) or not sketch_name:
        return {
            "ok": False,
            "error": "Could not create profile sketch.",
            "sketch_result": sketch_result,
            "body_result": body_result,
        }

    entity_results = []
    kind_counts: dict[str, int] = {}
    for raw_entity in entities:
        if not isinstance(raw_entity, dict):
            entity_results.append({"ok": False, "error": "Profile entity must be an object."})
            continue
        kind = str(raw_entity.get("kind") or "").strip().lower()
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        entity_results.append(_add_entity(service, str(sketch_name), raw_entity))
        if not backend_ok(entity_results[-1]):
            break

    constraint_results = []
    if all(backend_ok(item) for item in entity_results):
        for raw_constraint in constraints or []:
            if not isinstance(raw_constraint, dict):
                constraint_results.append(
                    {"ok": False, "error": "Profile constraint must be an object."}
                )
                continue
            constraint_results.append(
                _add_constraint(service, str(sketch_name), raw_constraint)
            )
            if not backend_ok(constraint_results[-1]):
                break

    inspect = call_backend(
        service,
        "sketcher.inspect_sketch",
        sketch_name=str(sketch_name),
        include=["geometry", "solver", "profile", "profile_deep"],
    )
    close_result = None
    if bool(close_after):
        close_result = call_backend(
            service, "sketcher.close_sketch", sketch_name=str(sketch_name)
        )

    requested_curve_count = sum(kind_counts.get(kind, 0) for kind in CURVE_KINDS)
    actual_geometry_types = _actual_geometry_types(inspect)
    actual_curve_geometry = _actual_profile_curve_geometry(inspect)
    actual_curve_count = len(actual_curve_geometry)
    actual_curve_types = sorted(
        {str(item.get("type") or "") for item in actual_curve_geometry}
    )
    warnings = []
    if bool(requires_curves) and actual_curve_count == 0:
        warnings.append(
            "requires_curves=true but the resulting FreeCAD sketch contains no non-construction curve geometry."
        )
    if requested_curve_count > 0 and backend_ok(inspect) and actual_curve_count == 0:
        warnings.append(
            "Curve entities were requested, but the resulting FreeCAD sketch inventory contains only straight or construction geometry."
        )
    memory = append_design_memory(
        service,
        sketches_features=[
            (
                f"{clean_component}.{clean_profile}: {clean_purpose}; "
                f"entities={kind_counts}; actual_geometry_types={actual_geometry_types}"
            )
        ],
    )
    ok = (
        all(backend_ok(item) for item in entity_results)
        and all(backend_ok(item) for item in constraint_results)
        and backend_ok(inspect)
        and (close_result is None or backend_ok(close_result))
        and not warnings
    )
    repair_actions = []
    if not all(backend_ok(item) for item in entity_results):
        repair_actions.append(
            {
                "tool": "cad.create_profile",
                "why": (
                    "Recreate the profile with corrected entity definitions. "
                    "Use explicit entity kinds and sketch-local [x,y] points."
                ),
            }
        )
    if not all(backend_ok(item) for item in constraint_results):
        repair_actions.append(
            {
                "tool": "cad.create_profile",
                "why": (
                    "Recreate or extend the profile with corrected named constraints. "
                    "Use entity names from entities[].name and point roles such as start, end, center, whole."
                ),
            }
        )
    if warnings:
        repair_actions.append(
            {
                "tool": "cad.create_profile",
                "why": (
                    "The profile contradicted its stated surface character. "
                    "Author real arc, ellipse, circle, bspline, loft, or sweep geometry instead of straight stand-ins."
                ),
            }
        )
    if not backend_ok(inspect):
        repair_actions.append(
            {
                "tool": "cad.inspect_state",
                "why": "Inspect current document, active sketch, and errors before another profile write.",
            }
        )
    error = None
    if not ok:
        error = (
            _first_backend_error(entity_results)
            or _first_backend_error(constraint_results)
            or (str(inspect.get("error") or "").strip() if isinstance(inspect, dict) else "")
            or (
                str(close_result.get("error") or "").strip()
                if isinstance(close_result, dict)
                else ""
            )
            or (warnings[0] if warnings else "")
            or "Profile creation did not pass semantic verification."
        )
    return {
        "ok": ok,
        "error": error,
        "component": clean_component,
        "body": body,
        "profile": str(sketch_name),
        "profile_label": clean_profile,
        "purpose": clean_purpose,
        "body_result": body_result,
        "sketch_result": sketch_result,
        "entity_kind_counts": kind_counts,
        "requested_curve_entity_count": requested_curve_count,
        "actual_curve_geometry_count": actual_curve_count,
        "actual_curve_geometry_types": actual_curve_types,
        "actual_geometry_types": actual_geometry_types,
        "entity_results": entity_results,
        "constraint_results": constraint_results,
        "inspect_result": inspect,
        "close_result": close_result,
        "warnings": warnings,
        "repair_actions": repair_actions,
        "design_memory_result": memory,
    }
