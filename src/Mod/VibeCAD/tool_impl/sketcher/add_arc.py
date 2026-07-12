# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create a circular Sketcher arc."""

from __future__ import annotations

from typing import Any

from . import add_geometry


TOOL_SPEC = {
    "name": "sketcher.add_arc",
    "safety": "SAFE_WRITE",
    "edit_modes": ["sketch"],
    "description": (
        "Add one circular arc from center, radius, and start/end angles in "
        "the sketch plane. Use arcs for tangent radii, circular reliefs, cams, "
        "and controlled curved profile segments instead of faceted lines."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "center": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 2,
                "maxItems": 2,
            },
            "radius": {"type": "number", "exclusiveMinimum": 0},
            "start_angle_degrees": {"type": "number"},
            "end_angle_degrees": {"type": "number"},
            "construction": {"type": "boolean"},
        },
        "required": [
            "center",
            "radius",
            "start_angle_degrees",
            "end_angle_degrees",
            "construction",
        ],
        "additionalProperties": False,
    },
}


def run(service: Any, **kwargs: Any) -> dict[str, Any]:
    return add_geometry.run(service, kind="arc", **kwargs)
