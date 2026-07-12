# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Part chamfer on exact named edges."""

from __future__ import annotations

from typing import Any

from . import part_fillet, partdesign_dressup_feature


TOOL_SPEC = {
    "name": "part.chamfer",
    "description": (
        "Create one native Part chamfer that bevels count-guarded geometric edges of one shaped "
        "object with an equal-distance cut. Finishing operation; apply after the "
        "primary form is complete. Resolve edge names with part.find_subelements "
        "first - never guess them. The source object becomes a hidden child of the "
        "chamfer result."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": "Exact internal name of the object whose edges are chamfered.",
            },
            "selection": partdesign_dressup_feature.selection_schema(
                allow_all_edges=True,
                edge_only=True,
            ),
            "size_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Chamfer distance in mm measured from the edge along both "
                    "adjacent faces."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the chamfer result.",
            },
        },
        "required": ["object_name", "selection", "size_mm", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    object_name: str,
    selection: dict[str, Any],
    size_mm: float,
    label: str,
) -> dict[str, Any]:
    return part_fillet.run_edge_finish(
        service,
        object_name=object_name,
        selection=selection,
        size_mm=size_mm,
        label=label,
        native_type="Part::Chamfer",
        operation="chamfer",
    )
