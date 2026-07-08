# SPDX-License-Identifier: LGPL-2.1-or-later

"""Consolidated Sketcher deletion tool.

Replaces sketcher.delete_geometry, sketcher.delete_constraint,
sketcher.delete_all_geometry, and sketcher.delete_all_constraints with one
tool that deletes single items, bulk selections, or everything.
"""

from __future__ import annotations

from typing import Any

from .common import (
    active_response,
    get_sketch,
    resolve_constraint_index,
    resolve_geometry_index,
    run_freecad_transaction,
    validate_constraint_index,
    validate_geometry_index,
)


TOOL_SPEC = {
    "name": "sketcher.delete_items",
    "description": (
        "Delete Sketcher geometry and/or constraints by index, name, or handle. "
        "Supports bulk lists and all_geometry/all_constraints. Returns "
        "old-to-new index maps for surviving items."
    ),
    "contextual": True,
    "workbench": "SketcherWorkbench",
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "geometry_items": {
                "type": "array",
                "items": {"type": ["integer", "string"]},
                "description": "Geometry to delete: integer indices or handles (geometry:N / name:X).",
            },
            "constraint_items": {
                "type": "array",
                "items": {"type": ["integer", "string"]},
                "description": "Constraints to delete: integer indices, handles (constraint:N), or names.",
            },
            "all_geometry": {
                "type": "boolean",
                "default": False,
                "description": "Delete all editable geometry in the sketch.",
            },
            "all_constraints": {
                "type": "boolean",
                "default": False,
                "description": "Delete all constraints in the sketch.",
            },
            "delete_constraints_first": {
                "type": "boolean",
                "default": True,
                "description": (
                    "When all_geometry is true, also delete all constraints before the geometry. "
                    "Defaults to true because FreeCAD geometry deletion can invalidate constraints."
                ),
            },
        },
    },
}


def _old_to_new_map(before_count: int, deleted: set[int]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for old_index in range(before_count):
        if old_index in deleted:
            continue
        mapping[str(old_index)] = old_index - sum(1 for d in deleted if d < old_index)
    return mapping


def run(
    service: Any,
    sketch_name: str | None = None,
    geometry_items: list[int | str] | None = None,
    constraint_items: list[int | str] | None = None,
    all_geometry: bool = False,
    all_constraints: bool = False,
    delete_constraints_first: bool = True,
) -> dict[str, Any]:
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return {"ok": False, "error": "Sketch not found.", "requested": sketch_name}
    geometry_items = geometry_items or []
    constraint_items = constraint_items or []
    if not geometry_items and not constraint_items and not all_geometry and not all_constraints:
        return {
            "ok": False,
            "error": (
                "Nothing to delete. Provide geometry_items, constraint_items, "
                "all_geometry=true, or all_constraints=true."
            ),
        }
    if all_geometry and geometry_items:
        return {"ok": False, "error": "Use either all_geometry or geometry_items, not both."}
    if all_constraints and constraint_items:
        return {"ok": False, "error": "Use either all_constraints or constraint_items, not both."}

    geometry_indices: list[int] = []
    for item in geometry_items:
        try:
            if isinstance(item, bool):
                raise ValueError(f"Invalid geometry item: {item!r}")
            if isinstance(item, int):
                index = int(item)
            elif isinstance(item, float) and float(item).is_integer():
                index = int(item)
            else:
                index = resolve_geometry_index(service, sketch, None, str(item))
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "error": f"Could not resolve geometry item {item!r}: {exc}"}
        invalid = validate_geometry_index(sketch, index)
        if invalid:
            return invalid
        geometry_indices.append(index)

    constraint_indices: list[int] = []
    for item in constraint_items:
        try:
            if isinstance(item, bool):
                raise ValueError(f"Invalid constraint item: {item!r}")
            if isinstance(item, int):
                index = int(item)
            elif isinstance(item, float) and float(item).is_integer():
                index = int(item)
            else:
                handle = str(item)
                if handle.startswith("constraint:"):
                    index = resolve_constraint_index(sketch, None, None, handle)
                else:
                    index = resolve_constraint_index(sketch, None, handle, None)
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "error": f"Could not resolve constraint item {item!r}: {exc}"}
        invalid = validate_constraint_index(sketch, index)
        if invalid:
            return invalid
        constraint_indices.append(index)

    geometry_targets = sorted(set(geometry_indices))
    constraint_targets = sorted(set(constraint_indices))

    def _delete() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_geometry = len(getattr(target, "Geometry", []))
        before_constraints = len(getattr(target, "Constraints", []))

        deleted_constraints: set[int] = set()
        if all_constraints or (all_geometry and delete_constraints_first):
            for index in reversed(range(before_constraints)):
                target.delConstraint(index)
                deleted_constraints.add(index)
        else:
            for index in reversed(constraint_targets):
                target.delConstraint(index)
                deleted_constraints.add(index)

        deleted_geometry: set[int] = set()
        if all_geometry:
            for index in reversed(range(before_geometry)):
                target.delGeometry(index)
                deleted_geometry.add(index)
        else:
            for index in reversed(geometry_targets):
                target.delGeometry(index)
                deleted_geometry.add(index)

        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        return {
            "sketch": target.Name,
            "deleted_geometry_indices": sorted(deleted_geometry),
            "deleted_constraint_indices": sorted(deleted_constraints),
            "geometry_count_before": before_geometry,
            "constraint_count_before": before_constraints,
            "geometry_count": len(getattr(target, "Geometry", [])),
            "constraint_count": len(getattr(target, "Constraints", [])),
            "old_to_new_geometry_index": _old_to_new_map(before_geometry, deleted_geometry),
            # Constraint index mapping is only reliable when no geometry was
            # deleted, because FreeCAD cascade-deletes constraints attached to
            # deleted geometry.
            "old_to_new_constraint_index": (
                _old_to_new_map(before_constraints, deleted_constraints)
                if not deleted_geometry
                else {}
            ),
        }

    return active_response(service, sketch, run_freecad_transaction("Delete Sketcher items", _delete))
