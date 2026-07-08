# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD semantic name tool for native Sketcher geometry."""

from __future__ import annotations

from typing import Any

from .common import (
    active_response,
    geometry_fingerprint,
    geometry_metadata,
    get_sketch,
    resolve_geometry_index,
    run_freecad_transaction,
    set_geometry_metadata,
    validate_geometry_index,
)


TOOL_SPEC = {
    "name": "sketcher.set_geometry_name",
    "description": (
        "Assign a semantic name to existing Sketcher geometry so later tools "
        "can target name:<name> and detect stale topology."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "geometry_index": {"type": "integer", "description": "Target geometry index."},
            "geometry_handle": {
                "type": "string",
                "description": "Geometry handle (geometry:N / name:X) alternative to geometry_index.",
            },
            "geometry_name": {"type": "string", "description": "Semantic name to assign, targetable later as name:<name>."},
        },
        "required": ["geometry_name"],
    },
}


def run(
    service: Any,
    geometry_name: str,
    sketch_name: str | None = None,
    geometry_index: int | None = None,
    geometry_handle: str | None = None,
) -> dict[str, Any]:
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return {"ok": False, "error": "Sketch not found.", "requested": sketch_name}
    try:
        index = resolve_geometry_index(service, sketch, geometry_index, geometry_handle)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "geometry_handle": geometry_handle, "geometry_index": geometry_index}
    invalid = validate_geometry_index(sketch, index)
    if invalid:
        return invalid
    clean_name = str(geometry_name).strip()
    if not clean_name:
        return {"ok": False, "error": "geometry_name must not be empty."}

    def _name() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        summary = service.sketcher_summary(target.Name)["geometry"][index]
        metadata = geometry_metadata(target)
        metadata.setdefault("names", {})[clean_name] = {
            "index": index,
            "fingerprint": geometry_fingerprint(summary),
        }
        set_geometry_metadata(target, metadata)
        if App.ActiveDocument is not None:
            App.ActiveDocument.recompute()
        return {
            "sketch": target.Name,
            "geometry_index": index,
            "geometry_handle": f"geometry:{index}",
            "geometry_name": clean_name,
            "semantic_handle": f"name:{clean_name}",
        }

    return active_response(
        service,
        sketch,
        run_freecad_transaction(f"Name Sketcher geometry {index}: {clean_name}", _name),
    )
