# SPDX-License-Identifier: LGPL-2.1-or-later

"""Consolidated Sketcher inspection tool.

Replaces sketcher.get_sketch, sketcher.list_geometry, sketcher.list_constraints,
sketcher.get_solver_status, sketcher.validate_profile,
sketcher.validate_profile_deep, sketcher.diagnose_constraints,
sketcher.list_external_geometry, and sketcher.list_reference_geometry
with one section-based report tool.
"""

from __future__ import annotations

from typing import Any

from .common import (
    constraint_diagnostics,
    external_geometry_summary,
    find_document_object,
    geometry_inventory,
    get_sketch,
    no_sketch,
    profile_validation,
    profile_validation_deep,
    resolve_geometry_names,
    solver_status,
    subelement_references,
)


DEFAULT_SECTIONS = ("geometry", "constraints", "solver", "profile")
ALL_SECTIONS = (
    "geometry",
    "constraints",
    "solver",
    "profile",
    "profile_deep",
    "constraint_diagnostics",
    "external_geometry",
    "reference_geometry",
)

TOOL_SPEC = {
    "name": "sketcher.inspect_sketch",
    "description": (
        "Inspect Sketcher geometry types, constraints, solver/DoF, profile "
        "closure, external references, and feature readiness. Use before "
        "solid features when shape or constraints matter."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "SketcherWorkbench",
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": list(ALL_SECTIONS)},
                "description": (
                    "Report sections to include. Defaults to "
                    "['geometry', 'constraints', 'solver', 'profile']."
                ),
            },
            "tolerance": {
                "type": "number",
                "default": 0.000001,
                "description": (
                    "Coordinate tolerance in millimeters for profile_deep and "
                    "constraint_diagnostics endpoint-graph checks."
                ),
            },
            "reference_object_name": {
                "type": "string",
                "description": "Restrict the reference_geometry section to one document object.",
            },
            "max_references": {
                "type": "integer",
                "default": 80,
                "description": "Maximum subelement references per object in the reference_geometry section.",
            },
        },
    },
}


def _reference_object_entry(obj: Any, max_references: int) -> dict[str, Any]:
    refs = subelement_references(obj)
    limit = max(0, int(max_references))
    return {
        "object": getattr(obj, "Name", None),
        "label": getattr(obj, "Label", getattr(obj, "Name", None)),
        "type": getattr(obj, "TypeId", None),
        "reference_count": len(refs),
        "references": refs[:limit],
        "references_truncated": len(refs) > limit,
    }


def _reference_geometry_section(
    service: Any,
    reference_object_name: str | None,
    max_references: int,
) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return {"ok": False, "error": "No active document."}
    limit = max(1, int(max_references))
    if reference_object_name:
        obj = find_document_object(service, reference_object_name)
        if obj is None:
            return {"ok": False, "error": f"Object not found: {reference_object_name}"}
        return {"ok": True, "document": doc.Name, "objects": [_reference_object_entry(obj, limit)]}
    objects = []
    for obj in getattr(doc, "Objects", []) or []:
        if getattr(obj, "TypeId", "") == "Sketcher::SketchObject":
            continue
        if subelement_references(obj):
            objects.append(_reference_object_entry(obj, limit))
    return {
        "ok": True,
        "document": doc.Name,
        "object_count": len(objects),
        "objects": objects[:40],
        "objects_truncated": len(objects) > 40,
    }


def run(
    service: Any,
    sketch_name: str | None = None,
    include: list[str] | None = None,
    tolerance: float = 0.000001,
    reference_object_name: str | None = None,
    max_references: int = 80,
) -> dict[str, Any]:
    if include is None:
        sections = list(DEFAULT_SECTIONS)
    elif isinstance(include, str):
        sections = [include]
    else:
        sections = [str(section) for section in include]
    if not sections:
        sections = list(DEFAULT_SECTIONS)
    unknown = sorted(set(sections) - set(ALL_SECTIONS))
    if unknown:
        return {
            "ok": False,
            "error": f"Unknown inspect sections: {unknown}. Valid sections: {list(ALL_SECTIONS)}.",
        }

    sketch = get_sketch(service, sketch_name)
    sketch_sections = [section for section in sections if section != "reference_geometry"]
    result: dict[str, Any] = {"ok": True, "sections": sections}

    if sketch is None and sketch_sections:
        if "reference_geometry" not in sections:
            return no_sketch(sketch_name)
        result["warnings"] = [
            f"Sketch not found ({sketch_name!r}); sketch sections skipped: {sketch_sections}."
        ]
        sketch_sections = []

    if sketch is not None:
        result["sketch"] = getattr(sketch, "Name", None)
        result["sketch_label"] = getattr(sketch, "Label", getattr(sketch, "Name", None))

    if sketch is not None and "geometry" in sketch_sections:
        geometry = geometry_inventory(service, sketch)
        summary = service.sketcher_summary(getattr(sketch, "Name", None))
        result["geometry_count"] = len(geometry)
        result["geometry"] = geometry
        result["internal_geometry"] = summary.get("internal_geometry", {})
        result["named_geometry"] = resolve_geometry_names(service, sketch, include_missing=True)
    if sketch is not None and "constraints" in sketch_sections:
        summary = service.sketcher_summary(getattr(sketch, "Name", None))
        result["constraint_count"] = summary.get("constraint_count", 0)
        result["constraints"] = summary.get("constraints", [])
    if sketch is not None and "solver" in sketch_sections:
        result["solver_status"] = solver_status(service, sketch)
    if sketch is not None and "profile" in sketch_sections:
        result["profile_validation"] = profile_validation(service, sketch)
    if sketch is not None and "profile_deep" in sketch_sections:
        result["profile_validation_deep"] = profile_validation_deep(service, sketch, float(tolerance))
    if sketch is not None and "constraint_diagnostics" in sketch_sections:
        result["constraint_diagnostics"] = constraint_diagnostics(service, sketch, float(tolerance))
    if sketch is not None and "external_geometry" in sketch_sections:
        external = external_geometry_summary(sketch)
        result["external_geometry_count"] = len(external)
        result["external_geometry"] = external
    if "reference_geometry" in sections:
        result["reference_geometry"] = _reference_geometry_section(
            service, reference_object_name, max_references
        )
    return result
