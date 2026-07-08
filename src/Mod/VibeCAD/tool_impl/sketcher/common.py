# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared helpers for native Sketcher tool implementations."""

from __future__ import annotations

import json
import math
from typing import Any

from VibeCADTransactions import run_freecad_transaction

GEOMETRY_METADATA_PROPERTY = "VibeCADGeometryMetadata"


def get_sketch(service: Any, sketch_name: str | None = None) -> Any:
    return service._get_sketch(sketch_name)


def no_sketch(sketch_name: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "No active Sketcher sketch found.",
        "requested": sketch_name,
    }


def recompute_errors(transaction: dict[str, Any]) -> list[str]:
    """Extract recompute/report-view error lines from a transaction result."""
    errors: list[str] = []
    if not isinstance(transaction, dict):
        return errors
    report = transaction.get("report_view_errors")
    if isinstance(report, dict):
        errors.extend(str(line) for line in report.get("errors", []) or [])
    transaction_error = transaction.get("error")
    if transaction_error and str(transaction_error) not in errors:
        errors.append(str(transaction_error))
    return errors


def active_response(service: Any, sketch: Any, transaction: dict[str, Any]) -> dict[str, Any]:
    """Standard rich result envelope for mutating sketcher tools.

    Every sketcher mutation returns this shape: transaction outcome (with
    document before/after/delta), a mutation summary (created/modified/deleted
    indices), current geometry/constraints, post-op solver status (DoF,
    conflicts, redundancies), profile validation, flattened recompute errors,
    and suggested next/repair actions.
    """
    if not isinstance(transaction, dict):
        transaction = {"ok": False, "error": "Invalid transaction result."}
    updated = service._get_sketch(getattr(sketch, "Name", None))
    sketcher = service.sketcher_summary(getattr(sketch, "Name", None))
    solver = solver_status(service, updated)
    profile = profile_validation(service, updated)
    repair_actions = solver_repair_actions(updated, solver, sketcher.get("constraints", []))
    result = transaction.get("result") if isinstance(transaction, dict) else {}
    suggested_actions = (
        list(result.get("suggested_next_actions", []))
        if isinstance(result, dict) and isinstance(result.get("suggested_next_actions"), list)
        else []
    )
    next_actions = repair_actions + suggested_actions + list(service._sketch_next_actions(updated))
    envelope = {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "recompute_errors": recompute_errors(transaction),
        "mutation": mutation_summary(transaction, sketcher, solver, profile),
        "sketcher": sketcher,
        "active_sketch": getattr(sketch, "Name", None),
        "profile_status": service._sketch_profile_status(updated),
        "solver_status": solver,
        "solver_repair_actions": repair_actions,
        "profile_validation": profile,
        "next_actions": next_actions,
    }
    if transaction.get("error"):
        envelope["error"] = str(transaction["error"])
    return envelope


def geometry_metadata(sketch: Any) -> dict[str, Any]:
    raw = getattr(sketch, GEOMETRY_METADATA_PROPERTY, "") or ""
    if not raw:
        return {"names": {}}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {"names": {}}
    if not isinstance(parsed, dict):
        return {"names": {}}
    names = parsed.get("names")
    if not isinstance(names, dict):
        parsed["names"] = {}
    return parsed


def set_geometry_metadata(sketch: Any, metadata: dict[str, Any]) -> None:
    if not hasattr(sketch, GEOMETRY_METADATA_PROPERTY):
        sketch.addProperty(
            "App::PropertyString",
            GEOMETRY_METADATA_PROPERTY,
            "VibeCAD",
            "VibeCAD semantic geometry handle metadata.",
        )
    setattr(sketch, GEOMETRY_METADATA_PROPERTY, json.dumps(metadata, sort_keys=True))


def _rounded_point(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        return [round(float(value[0]), 6), round(float(value[1]), 6), round(float(value[2]), 6)]
    except Exception:
        return None


def geometry_fingerprint(summary: dict[str, Any]) -> dict[str, Any]:
    keys = ("type", "construction", "start", "end", "center", "radius", "major_radius", "minor_radius", "poles")
    fingerprint: dict[str, Any] = {}
    for key in keys:
        if key not in summary:
            continue
        value = summary[key]
        if key in {"start", "end", "center"}:
            fingerprint[key] = _rounded_point(value)
        elif key == "poles" and isinstance(value, list):
            fingerprint[key] = [_rounded_point(point) for point in value]
        elif isinstance(value, float):
            fingerprint[key] = round(value, 6)
        else:
            fingerprint[key] = value
    return fingerprint


def geometry_inventory(service: Any, sketch: Any) -> list[dict[str, Any]]:
    summary = service.sketcher_summary(getattr(sketch, "Name", None))
    geometry = [dict(item) for item in summary.get("geometry", [])]
    metadata = geometry_metadata(sketch)
    names = metadata.get("names", {})
    current_by_name = resolve_geometry_names(service, sketch, include_missing=False)
    for item in geometry:
        index = item.get("index")
        item["stable_handle"] = f"geometry:{index}"
        item["fingerprint"] = geometry_fingerprint(item)
        item_names = [
            str(name)
            for name, resolved in current_by_name.items()
            if resolved.get("ok") and resolved.get("geometry_index") == index
        ]
        if item_names:
            item["names"] = sorted(item_names)
            item["semantic_handles"] = [f"name:{name}" for name in sorted(item_names)]
    if names:
        missing = resolve_geometry_names(service, sketch, include_missing=True)
        unresolved = {
            name: data
            for name, data in missing.items()
            if not data.get("ok")
        }
        if unresolved:
            for item in geometry:
                item.setdefault("unresolved_named_geometry", unresolved)
                break
    return geometry


def resolve_geometry_names(
    service: Any,
    sketch: Any,
    include_missing: bool = True,
) -> dict[str, dict[str, Any]]:
    metadata = geometry_metadata(sketch)
    names = metadata.get("names", {})
    if not isinstance(names, dict):
        return {}
    summary = service.sketcher_summary(getattr(sketch, "Name", None))
    geometry = list(summary.get("geometry", []))
    result: dict[str, dict[str, Any]] = {}
    for raw_name, record in names.items():
        name = str(raw_name)
        if not isinstance(record, dict):
            continue
        stored_fingerprint = record.get("fingerprint")
        preferred_index = record.get("index")
        if isinstance(preferred_index, int) and 0 <= preferred_index < len(geometry):
            current = geometry_fingerprint(geometry[preferred_index])
            if current == stored_fingerprint:
                result[name] = {
                    "ok": True,
                    "name": name,
                    "geometry_index": preferred_index,
                    "geometry": geometry[preferred_index],
                    "match": "preferred_index",
                }
                continue
            if geometry[preferred_index].get("type") == (stored_fingerprint or {}).get("type"):
                result[name] = {
                    "ok": True,
                    "name": name,
                    "geometry_index": preferred_index,
                    "geometry": geometry[preferred_index],
                    "match": "preferred_index_type",
                    "fingerprint_changed": True,
                    "stored_fingerprint": stored_fingerprint,
                    "current_fingerprint": current,
                }
                continue
        matches = [
            item for item in geometry
            if geometry_fingerprint(item) == stored_fingerprint
        ]
        if len(matches) == 1:
            result[name] = {
                "ok": True,
                "name": name,
                "geometry_index": int(matches[0]["index"]),
                "geometry": matches[0],
                "match": "fingerprint",
            }
            continue
        if include_missing:
            result[name] = {
                "ok": False,
                "name": name,
                "error": "Named geometry is missing or ambiguous after sketch topology changed.",
                "match_count": len(matches),
                "stored_index": preferred_index,
                "stored_fingerprint": stored_fingerprint,
            }
    return result


def resolve_geometry_index(service: Any, sketch: Any, geometry_index: int | None = None, geometry_handle: str | None = None) -> int:
    if geometry_handle is None or str(geometry_handle).strip() == "":
        if geometry_index is None:
            raise ValueError("geometry_index or geometry_handle is required.")
        return int(geometry_index)
    handle = str(geometry_handle).strip()
    clean = handle.lower()
    if clean in {"origin", "root", "rootpoint", "root_point"}:
        return -1
    if clean in {"axis:h", "axis:x", "h_axis", "x_axis", "horizontal_axis"}:
        return -1
    if clean in {"axis:v", "axis:y", "v_axis", "y_axis", "vertical_axis"}:
        return -2
    if clean.startswith("external:"):
        return -3 - int(clean.split(":", 1)[1])
    if handle.startswith("geometry:"):
        return int(handle.split(":", 1)[1])
    if handle.startswith("name:"):
        name = handle.split(":", 1)[1]
    else:
        name = handle
    resolved = resolve_geometry_names(service, sketch, include_missing=True).get(name)
    if not resolved or not resolved.get("ok"):
        raise ValueError(f"Geometry handle could not be resolved: {handle}. {resolved or {}}")
    return int(resolved["geometry_index"])


def default_point_position_for_handle(geometry_handle: str | None, fallback: int = 0) -> int:
    if geometry_handle is None:
        return fallback
    clean = str(geometry_handle).strip().lower()
    if clean in {"origin", "root", "rootpoint", "root_point"}:
        return 1
    return fallback


def _range_from_base(raw_index: Any, raw_count: Any) -> list[int]:
    try:
        index = int(raw_index)
        count = int(raw_count)
    except Exception:
        return []
    if count <= 0:
        return []
    return list(range(index, index + count))


def _int_list(raw_value: Any) -> list[int]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        result = []
        for item in raw_value:
            try:
                result.append(int(item))
            except Exception:
                pass
        return result
    try:
        return [int(raw_value)]
    except Exception:
        return []


def mutation_summary(
    transaction: dict[str, Any],
    sketcher: dict[str, Any],
    solver: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    result = transaction.get("result")
    if not isinstance(result, dict):
        result = {}
    created_geometry = _range_from_base(result.get("geometry_index"), result.get("geometry_added"))
    if not created_geometry:
        created_geometry = _int_list(result.get("created_geometry_indices"))
    if not created_geometry:
        created_geometry = _int_list(result.get("created_geometry_index"))
    constraints_added = int(result.get("constraints_added", 0) or 0)
    created_constraints = (
        _range_from_base(result.get("constraint_index"), constraints_added)
        if constraints_added > 1
        else _int_list(result.get("constraint_index"))
    )
    deleted_geometry = _int_list(result.get("deleted_geometry_indices"))
    if not deleted_geometry:
        deleted_geometry = _int_list(result.get("deleted_geometry_index"))
    deleted_constraints = _int_list(result.get("deleted_constraint_indices"))
    if not deleted_constraints:
        deleted_constraints = _int_list(result.get("deleted_constraint_index"))
    modified_geometry = _int_list(result.get("modified_geometry_indices"))
    if not modified_geometry and not created_geometry and not deleted_geometry:
        modified_geometry = _int_list(result.get("geometry_index"))
    modified_constraints = (
        _int_list(result.get("constraint_index")) if not created_constraints and not deleted_constraints else []
    )
    geometry_count = sketcher.get("geometry_count")
    constraint_count = sketcher.get("constraint_count")
    return {
        "sketch": result.get("sketch") or sketcher.get("sketch"),
        "created_geometry_indices": created_geometry,
        "created_constraint_indices": created_constraints,
        "modified_geometry_indices": modified_geometry,
        "modified_constraint_indices": modified_constraints,
        "deleted_geometry_indices": deleted_geometry,
        "deleted_constraint_indices": deleted_constraints,
        "old_to_new_geometry_index": result.get("old_to_new_geometry_index", {}),
        "old_to_new_constraint_index": result.get("old_to_new_constraint_index", {}),
        "geometry_count": geometry_count,
        "constraint_count": constraint_count,
        "geometry": sketcher.get("geometry", []),
        "constraints": sketcher.get("constraints", []),
        "solver_status": solver,
        "profile_validation": profile,
    }


def find_document_object(service: Any, object_name: str | None) -> Any:
    if not object_name:
        return None
    doc = service._active_document()
    if doc is None:
        return None
    target = doc.getObject(str(object_name))
    if target is not None:
        return target
    for candidate in getattr(doc, "Objects", []) or []:
        if getattr(candidate, "Label", None) == str(object_name):
            return candidate
    return None


def external_geometry_summary(sketch: Any) -> list[dict[str, Any]]:
    external = list(getattr(sketch, "ExternalGeometry", []) or [])
    result: list[dict[str, Any]] = []
    for index, item in enumerate(external):
        source = None
        subelements: tuple[str, ...] = ()
        if isinstance(item, tuple) and len(item) >= 2:
            source = item[0]
            raw_subs = item[1]
            if isinstance(raw_subs, str):
                subelements = (raw_subs,)
            else:
                try:
                    subelements = tuple(str(value) for value in raw_subs)
                except Exception:
                    subelements = (str(raw_subs),)
        result.append(
            {
                "external_index": index,
                "external_geometry_id": -index - 1,
                "source_object": getattr(source, "Name", None),
                "source_label": getattr(source, "Label", getattr(source, "Name", None)),
                "source_type": getattr(source, "TypeId", None),
                "subelements": list(subelements),
            }
        )
    return result


def subelement_references(obj: Any) -> list[dict[str, Any]]:
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return []
    refs: list[dict[str, Any]] = []
    for prefix, attr in (("Vertex", "Vertexes"), ("Edge", "Edges"), ("Face", "Faces")):
        items = list(getattr(shape, attr, []) or [])
        for offset, item in enumerate(items, start=1):
            entry: dict[str, Any] = {
                "subelement": f"{prefix}{offset}",
                "kind": prefix.lower(),
            }
            try:
                center = getattr(item, "CenterOfMass", None)
                if center is not None:
                    entry["center"] = [float(center.x), float(center.y), float(center.z)]
            except Exception:
                pass
            try:
                entry["length"] = float(getattr(item, "Length"))
            except Exception:
                pass
            try:
                entry["area"] = float(getattr(item, "Area"))
            except Exception:
                pass
            refs.append(entry)
    return refs


def solver_status(service: Any, sketch: Any | None) -> dict[str, Any]:
    if sketch is None:
        return {"found": False, "error": "Sketch not found."}
    try:
        degrees_of_freedom = int(getattr(sketch, "DoF"))
    except Exception:
        degrees_of_freedom = None
    constraints = list(getattr(sketch, "Constraints", []) or [])
    geometry = list(getattr(sketch, "Geometry", []) or [])
    status = {
        "found": True,
        "sketch": getattr(sketch, "Name", None),
        "sketch_label": getattr(sketch, "Label", getattr(sketch, "Name", None)),
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "degrees_of_freedom": degrees_of_freedom,
        "fully_constrained": degrees_of_freedom == 0 if degrees_of_freedom is not None else False,
        "under_constrained": bool(degrees_of_freedom is not None and degrees_of_freedom > 0),
        "solver_message": None,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
    }
    for attr, key in (
        ("ConflictingConstraints", "conflicting_constraint_indices"),
        ("RedundantConstraints", "redundant_constraint_indices"),
    ):
        try:
            values = getattr(sketch, attr)
        except Exception:
            continue
        if values:
            try:
                status[key] = [int(item) for item in values]
            except Exception:
                status[key] = list(values)
    try:
        status["profile_status"] = service._sketch_profile_status(sketch)
    except Exception as exc:
        status["profile_status_error"] = str(exc)
    return status


def solver_repair_actions(
    sketch: Any | None,
    solver: dict[str, Any],
    constraints: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if sketch is None:
        return []
    sketch_name = getattr(sketch, "Name", None)
    constraint_summaries = constraints or []
    repair_actions: list[dict[str, Any]] = []

    def _constraint(index: int) -> dict[str, Any]:
        if 0 <= index < len(constraint_summaries):
            return constraint_summaries[index]
        return {"index": index, "handle": f"constraint:{index}", "missing_from_summary": True}

    for raw_index in solver.get("conflicting_constraint_indices", []) or []:
        try:
            index = int(raw_index)
        except Exception:
            continue
        constraint = _constraint(index)
        repair_actions.append(
            {
                "kind": "remove_conflicting_constraint",
                "tool": "sketcher.delete_items",
                "arguments": {
                    "sketch_name": sketch_name,
                    "constraint_items": [index],
                },
                "target_constraint": constraint,
                "why": f"FreeCAD solver reports constraint {index} is conflicting; remove or replace it before adding more constraints.",
            }
        )
    if not solver.get("fully_constrained") or solver.get("conflicting_constraint_indices"):
        for raw_index in solver.get("redundant_constraint_indices", []) or []:
            try:
                index = int(raw_index)
            except Exception:
                continue
            constraint = _constraint(index)
            repair_actions.append(
                {
                    "kind": "remove_redundant_constraint",
                    "tool": "sketcher.delete_items",
                    "arguments": {
                        "sketch_name": sketch_name,
                        "constraint_items": [index],
                    },
                    "target_constraint": constraint,
                    "why": f"FreeCAD solver reports constraint {index} is redundant; delete it and re-run sketcher.inspect_sketch with include=['constraint_diagnostics'].",
                }
            )
    return repair_actions


def _point_key(point: Any, tolerance: float = 1e-6) -> tuple[int, int, int]:
    return (
        int(round(float(point.x) / tolerance)),
        int(round(float(point.y) / tolerance)),
        int(round(float(point.z) / tolerance)),
    )


def _point_list(point: Any) -> list[float]:
    return [float(point.x), float(point.y), float(point.z)]


def _distance(a: Any, b: Any) -> float:
    return math.sqrt((float(a.x) - float(b.x)) ** 2 + (float(a.y) - float(b.y)) ** 2 + (float(a.z) - float(b.z)) ** 2)


def _line_segment_intersection_2d(a1: Any, a2: Any, b1: Any, b2: Any, tolerance: float) -> dict[str, Any] | None:
    x1, y1 = float(a1.x), float(a1.y)
    x2, y2 = float(a2.x), float(a2.y)
    x3, y3 = float(b1.x), float(b1.y)
    x4, y4 = float(b2.x), float(b2.y)
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) <= tolerance:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den

    def between(value: float, lo: float, hi: float) -> bool:
        return min(lo, hi) + tolerance < value < max(lo, hi) - tolerance

    if between(px, x1, x2) and between(py, y1, y2) and between(px, x3, x4) and between(py, y3, y4):
        return {"point": [px, py, 0.0]}
    return None


def profile_validation_deep(service: Any, sketch: Any | None, tolerance: float = 1e-6) -> dict[str, Any]:
    if sketch is None:
        return {"found": False, "error": "Sketch not found."}
    base = profile_validation(service, sketch)
    summary = service.sketcher_summary(getattr(sketch, "Name", None))
    geometry_summaries = summary.get("geometry", [])
    geometry = list(getattr(sketch, "Geometry", []) or [])
    nonconstruction_edges: list[dict[str, Any]] = []
    endpoint_nodes: dict[tuple[int, int, int], dict[str, Any]] = {}
    tiny_edges: list[dict[str, Any]] = []
    duplicate_edges: list[dict[str, Any]] = []
    seen_edge_keys: dict[tuple[tuple[int, int, int], tuple[int, int, int]], int] = {}

    for index, item in enumerate(geometry):
        try:
            if bool(sketch.getConstruction(index)):
                continue
        except Exception:
            pass
        start = getattr(item, "StartPoint", None)
        end = getattr(item, "EndPoint", None)
        if start is None or end is None:
            continue
        length = _distance(start, end)
        start_key = _point_key(start, tolerance)
        end_key = _point_key(end, tolerance)
        edge = {
            "geometry_index": index,
            "geometry_handle": f"geometry:{index}",
            "type": item.__class__.__name__,
            "start": _point_list(start),
            "end": _point_list(end),
            "length": length,
            "start_node": str(start_key),
            "end_node": str(end_key),
            "_start_key": start_key,
            "_end_key": end_key,
        }
        nonconstruction_edges.append(edge)
        if length <= tolerance:
            tiny_edges.append(edge)
        for role, key, point in (("start", start_key, start), ("end", end_key, end)):
            node = endpoint_nodes.setdefault(key, {"node": str(key), "point": _point_list(point), "endpoints": []})
            node["endpoints"].append({"geometry_index": index, "geometry_handle": f"geometry:{index}", "role": role})
        unordered_key = tuple(sorted((start_key, end_key)))
        if unordered_key in seen_edge_keys:
            duplicate_edges.append(
                {
                    "first_geometry_index": seen_edge_keys[unordered_key],
                    "first_geometry_handle": f"geometry:{seen_edge_keys[unordered_key]}",
                    "second_geometry_index": index,
                    "second_geometry_handle": f"geometry:{index}",
                }
            )
        else:
            seen_edge_keys[unordered_key] = index

    open_nodes = [
        node for node in endpoint_nodes.values()
        if len(node["endpoints"]) == 1
    ]
    t_junctions = [
        node for node in endpoint_nodes.values()
        if len(node["endpoints"]) > 2
    ]
    connected_components: list[dict[str, Any]] = []
    adjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]] = {}
    edge_by_nodes: dict[tuple[tuple[int, int, int], tuple[int, int, int]], list[int]] = {}
    for edge in nonconstruction_edges:
        start_key = edge["_start_key"]
        end_key = edge["_end_key"]
        adjacency.setdefault(start_key, set()).add(end_key)
        adjacency.setdefault(end_key, set()).add(start_key)
        edge_by_nodes.setdefault((start_key, end_key), []).append(edge["geometry_index"])
        edge_by_nodes.setdefault((end_key, start_key), []).append(edge["geometry_index"])

    visited: set[tuple[int, int, int]] = set()
    for start_key in adjacency:
        if start_key in visited:
            continue
        stack = [start_key]
        nodes: set[tuple[int, int, int]] = set()
        edge_indices: set[int] = set()
        while stack:
            key = stack.pop()
            if key in nodes:
                continue
            nodes.add(key)
            for neighbor in adjacency.get(key, set()):
                edge_indices.update(edge_by_nodes.get((key, neighbor), []))
                if neighbor not in nodes:
                    stack.append(neighbor)
        visited.update(nodes)
        node_degrees = [len(adjacency.get(key, set())) for key in nodes]
        connected_components.append(
            {
                "node_count": len(nodes),
                "edge_count": len(edge_indices),
                "geometry_indices": sorted(edge_indices),
                "geometry_handles": [f"geometry:{index}" for index in sorted(edge_indices)],
                "open_node_count": sum(1 for key in nodes if len(endpoint_nodes.get(key, {}).get("endpoints", [])) == 1),
                "closed_loop_candidate": bool(nodes and all(degree == 2 for degree in node_degrees)),
            }
        )

    line_intersections: list[dict[str, Any]] = []
    for offset, first in enumerate(nonconstruction_edges):
        first_geo = geometry[first["geometry_index"]]
        if first_geo.__class__.__name__ != "LineSegment":
            continue
        for second in nonconstruction_edges[offset + 1:]:
            second_geo = geometry[second["geometry_index"]]
            if second_geo.__class__.__name__ != "LineSegment":
                continue
            intersection = _line_segment_intersection_2d(
                getattr(first_geo, "StartPoint"),
                getattr(first_geo, "EndPoint"),
                getattr(second_geo, "StartPoint"),
                getattr(second_geo, "EndPoint"),
                tolerance,
            )
            if intersection:
                intersection.update(
                    {
                        "first_geometry_index": first["geometry_index"],
                        "first_geometry_handle": first["geometry_handle"],
                        "second_geometry_index": second["geometry_index"],
                        "second_geometry_handle": second["geometry_handle"],
                    }
                )
                line_intersections.append(intersection)

    shape = getattr(sketch, "Shape", None)
    wires = list(getattr(shape, "Wires", []) or []) if shape is not None else []
    faces = list(getattr(shape, "Faces", []) or []) if shape is not None else []
    face_summaries = []
    for index, face in enumerate(faces):
        entry: dict[str, Any] = {"face_index": index}
        try:
            entry["area"] = float(getattr(face, "Area"))
        except Exception:
            pass
        try:
            center = getattr(face, "CenterOfMass", None)
            if center is not None:
                entry["center"] = _point_list(center)
        except Exception:
            pass
        face_summaries.append(entry)

    blockers = []
    if open_nodes:
        blockers.append({"severity": "error", "kind": "open_endpoints", "count": len(open_nodes)})
    if tiny_edges:
        blockers.append({"severity": "error", "kind": "tiny_or_zero_length_edges", "count": len(tiny_edges)})
    if duplicate_edges:
        blockers.append({"severity": "warning", "kind": "duplicate_edges", "count": len(duplicate_edges)})
    if t_junctions:
        blockers.append({"severity": "warning", "kind": "t_junction_or_nonmanifold_nodes", "count": len(t_junctions)})
    if line_intersections:
        blockers.append({"severity": "warning", "kind": "line_self_intersections", "count": len(line_intersections)})
    error_blockers = [item for item in blockers if item.get("severity") == "error"]

    return {
        **base,
        "ok": True,
        "tolerance": float(tolerance),
        "geometry": geometry_summaries,
        "nonconstruction_edge_count": len(nonconstruction_edges),
        "nonconstruction_edges": [
            {key: value for key, value in edge.items() if not str(key).startswith("_")}
            for edge in nonconstruction_edges
        ],
        "endpoint_node_count": len(endpoint_nodes),
        "open_nodes": open_nodes,
        "t_junction_nodes": t_junctions,
        "tiny_edges": tiny_edges,
        "duplicate_edges": duplicate_edges,
        "line_self_intersections": line_intersections,
        "connected_components": connected_components,
        "wire_count": len(wires),
        "face_count": len(faces),
        "faces": face_summaries,
        "shape_face_diagnostic": {
            "face_count": len(faces),
            "note": (
                "Sketch.Shape.Faces is diagnostic only; PartDesign pads may still be valid "
                "when endpoint/profile validation reports ready_for_pad."
            ),
        },
        "feature_readiness": {
            "pad": bool(base.get("ready_for_pad")) and not error_blockers,
            "pocket": bool(base.get("ready_for_pocket")) and not error_blockers,
            "blockers": blockers,
        },
    }


def constraint_diagnostics(service: Any, sketch: Any | None, tolerance: float = 1e-6) -> dict[str, Any]:
    if sketch is None:
        return {"found": False, "error": "Sketch not found."}
    solver = solver_status(service, sketch)
    profile = profile_validation_deep(service, sketch, tolerance)
    summary = service.sketcher_summary(getattr(sketch, "Name", None))
    constraints = summary.get("constraints", [])
    geometry = summary.get("geometry", [])

    def _constraints_by_indices(indices: list[int]) -> list[dict[str, Any]]:
        result = []
        for index in indices:
            if 0 <= index < len(constraints):
                result.append(constraints[index])
            else:
                result.append({"index": index, "handle": f"constraint:{index}", "missing_from_summary": True})
        return result

    involvement: dict[int, list[dict[str, Any]]] = {int(item["index"]): [] for item in geometry if "index" in item}
    for constraint in constraints:
        for attr in ("first", "second", "third"):
            raw = constraint.get(attr)
            if isinstance(raw, int) and raw >= 0 and raw in involvement:
                involvement[raw].append(
                    {
                        "constraint_index": constraint.get("index"),
                        "constraint_handle": constraint.get("handle"),
                        "constraint_type": constraint.get("type"),
                        "role": attr,
                    }
                )
    coverage = []
    for item in geometry:
        index = int(item["index"])
        related = involvement.get(index, [])
        coverage.append(
            {
                "geometry_index": index,
                "geometry_handle": item.get("handle"),
                "type": item.get("type"),
                "constraint_count": len(related),
                "constraints": related,
                "underconstrained_risk": bool(solver.get("under_constrained") and len(related) == 0),
            }
        )

    suggestions = []
    suggestions.extend(solver_repair_actions(sketch, solver, constraints))
    for node in profile.get("open_nodes", [])[:20]:
        endpoints = node.get("endpoints", [])
        if endpoints:
            suggestions.append(
                {
                    "kind": "close_endpoint",
                    "target": endpoints[0],
                    "suggested_tools": ["sketcher.add_constraint", "sketcher.move_point", "sketcher.add_geometry"],
                    "why": "Endpoint is not connected to another non-construction endpoint within tolerance.",
                }
            )
    for item in coverage:
        if item["underconstrained_risk"]:
            suggestions.append(
                {
                    "kind": "constrain_unreferenced_geometry",
                    "target": {"geometry_index": item["geometry_index"], "geometry_handle": item["geometry_handle"]},
                    "suggested_tools": ["sketcher.add_constraint"],
                    "why": "Geometry has no constraints and the sketch is under-constrained.",
                }
            )

    return {
        "ok": True,
        "found": True,
        "sketch": getattr(sketch, "Name", None),
        "solver_status": solver,
        "conflicting_constraints": _constraints_by_indices(solver.get("conflicting_constraint_indices", [])),
        "redundant_constraints": _constraints_by_indices(solver.get("redundant_constraint_indices", [])),
        "solver_repair_actions": solver_repair_actions(sketch, solver, constraints),
        "per_geometry_constraint_coverage": coverage,
        "profile_diagnostics": profile,
        "suggested_next_checks": suggestions[:40],
        "next_actions": suggestions[:40],
        "limits": {
            "exact_per_parameter_dof_available": False,
            "note": "FreeCAD Python exposes aggregate DoF and conflict/redundancy lists here; this tool adds geometry/profile diagnostics without claiming exact solver parameter vectors.",
        },
    }


def profile_validation(service: Any, sketch: Any | None) -> dict[str, Any]:
    if sketch is None:
        return {"found": False, "error": "Sketch not found."}
    geometry = list(getattr(sketch, "Geometry", []) or [])
    shape = getattr(sketch, "Shape", None)
    faces = list(getattr(shape, "Faces", []) or []) if shape is not None else []
    edges = list(getattr(shape, "Edges", []) or []) if shape is not None else []
    endpoints: list[dict[str, Any]] = []
    endpoint_counts: dict[tuple[float, float, float], int] = {}
    for index, item in enumerate(geometry):
        try:
            if bool(sketch.getConstruction(index)):
                continue
        except Exception:
            pass
        for role, point in (("start", getattr(item, "StartPoint", None)), ("end", getattr(item, "EndPoint", None))):
            if point is None:
                continue
            key = (round(float(point.x), 6), round(float(point.y), 6), round(float(point.z), 6))
            endpoint_counts[key] = endpoint_counts.get(key, 0) + 1
            endpoints.append({"geometry_index": index, "role": role, "point": list(key)})
    open_endpoints = [
        endpoint for endpoint in endpoints
        if endpoint_counts[tuple(endpoint["point"])] == 1
    ]
    profile_status = service._sketch_profile_status(sketch)
    return {
        "found": True,
        "sketch": getattr(sketch, "Name", None),
        "edge_count": len(edges),
        "face_count": len(faces),
        "closed_profile": bool(profile_status.get("closed_profile")),
        "closed_edge_loop": bool(profile_status.get("closed_edge_loop")),
        "ready_for_pad": bool(profile_status.get("ready_for_pad")),
        "open_endpoint_count": len(open_endpoints),
        "open_endpoints": open_endpoints[:40],
        "construction_geometry_count": profile_status.get("construction_geometry_count"),
        "reason": profile_status.get("reason"),
    }


def vector2(raw: list[float], index: int, label: str):
    if len(raw) < 2:
        raise ValueError(f"{label} point {index} must contain x and y.")
    import FreeCAD as App

    return App.Vector(float(raw[0]), float(raw[1]), 0.0)


def validate_geometry_index(sketch: Any, geometry_index: int) -> dict[str, Any] | None:
    geometry = list(getattr(sketch, "Geometry", []))
    index = int(geometry_index)
    if index < 0 or index >= len(geometry):
        return {
            "ok": False,
            "error": f"Geometry index out of range: {index}",
            "geometry_count": len(geometry),
        }
    return None


def validate_constraint_index(sketch: Any, constraint_index: int) -> dict[str, Any] | None:
    constraints = list(getattr(sketch, "Constraints", []))
    index = int(constraint_index)
    if index < 0 or index >= len(constraints):
        return {
            "ok": False,
            "error": f"Constraint index out of range: {index}",
            "constraint_count": len(constraints),
        }
    return None


def resolve_constraint_index(
    sketch: Any,
    constraint_index: int | None = None,
    constraint_name: str | None = None,
    constraint_handle: str | None = None,
) -> int:
    if constraint_handle:
        handle = str(constraint_handle).strip()
        if handle.startswith("constraint:"):
            return int(handle.split(":", 1)[1])
        constraint_name = handle[5:] if handle.startswith("name:") else handle
    if constraint_name:
        return int(sketch.getIndexByName(str(constraint_name)))
    if constraint_index is None:
        raise ValueError("constraint_index, constraint_name, or constraint_handle is required.")
    return int(constraint_index)


__all__ = [
    "active_response",
    "external_geometry_summary",
    "find_document_object",
    "geometry_fingerprint",
    "geometry_inventory",
    "geometry_metadata",
    "get_sketch",
    "no_sketch",
    "constraint_diagnostics",
    "profile_validation",
    "profile_validation_deep",
    "resolve_geometry_index",
    "resolve_geometry_names",
    "run_freecad_transaction",
    "set_geometry_metadata",
    "solver_status",
    "solver_repair_actions",
    "resolve_constraint_index",
    "subelement_references",
    "mutation_summary",
    "validate_constraint_index",
    "validate_geometry_index",
    "vector2",
]
