# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Sketcher add-external-geometry tool."""

from __future__ import annotations

from typing import Any

from .common import (
    active_response,
    external_geometry_summary,
    find_document_object,
    get_sketch,
    run_freecad_transaction,
    subelement_references,
)


TOOL_SPEC = {
    "name": "sketcher.add_external_geometry",
    "description": (
        "Add one Sketcher external-geometry reference from an existing object "
        "subelement. Use to constrain sketches to existing edges/vertices."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "source_object": {"type": "string", "description": "Document object name providing the subelement."},
            "subelement": {"type": "string", "description": "Subelement name on the source object, e.g. Edge1 or Vertex2."},
            "defining": {"type": "boolean", "description": "Import as defining (driving) geometry. Default false (reference only)."},
            "intersection": {"type": "boolean", "description": "Project the subelement's intersection with the sketch plane. Default false."},
        },
        "required": ["source_object", "subelement"],
    },
}


def run(
    service: Any,
    sketch_name: str | None = None,
    source_object: str | None = None,
    subelement: str | None = None,
    defining: bool = False,
    intersection: bool = False,
) -> dict[str, Any]:
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return {"ok": False, "error": "Sketch not found.", "requested": sketch_name}
    obj = find_document_object(service, source_object)
    if obj is None:
        return {"ok": False, "error": f"Source object not found: {source_object}"}
    clean_subelement = str(subelement or "").strip()
    if not clean_subelement:
        return {"ok": False, "error": "subelement is required."}
    valid = {item["subelement"] for item in subelement_references(obj)}
    if valid and clean_subelement not in valid:
        return {
            "ok": False,
            "error": f"Subelement {clean_subelement} was not found on {getattr(obj, 'Name', source_object)}.",
            "available_subelements": sorted(valid)[:120],
        }

    def _add() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before = external_geometry_summary(target)
        before_count = len(before)
        target.addExternal(getattr(obj, "Name", str(source_object)), clean_subelement, bool(defining), bool(intersection))
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        after = external_geometry_summary(target)
        return {
            "sketch": target.Name,
            "source_object": getattr(obj, "Name", str(source_object)),
            "source_label": getattr(obj, "Label", getattr(obj, "Name", str(source_object))),
            "subelement": clean_subelement,
            "defining": bool(defining),
            "intersection": bool(intersection),
            "external_geometry_index": before_count,
            "external_geometry_id": -before_count - 1,
            "external_geometry_count_before": before_count,
            "external_geometry_count": len(after),
            "external_geometry": after,
        }

    return active_response(service, sketch, run_freecad_transaction("Add Sketcher external geometry", _add))
