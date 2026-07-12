# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create an elliptical Sketcher profile."""

from __future__ import annotations

from typing import Any

from . import add_geometry


TOOL_SPEC = {
    "name": "sketcher.add_ellipse",
    "safety": "SAFE_WRITE",
    "edit_modes": ["sketch"],
    "description": (
        "Add one exact Sketcher ellipse from center, major/minor radii, and "
        "orientation. Use for elliptical sections and intentional conic form."
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
            "major_radius": {"type": "number", "exclusiveMinimum": 0},
            "minor_radius": {"type": "number", "exclusiveMinimum": 0},
            "angle_degrees": {"type": "number"},
            "construction": {"type": "boolean"},
        },
        "required": [
            "center",
            "major_radius",
            "minor_radius",
            "angle_degrees",
            "construction",
        ],
        "additionalProperties": False,
    },
}


def run(service: Any, **kwargs: Any) -> dict[str, Any]:
    return add_geometry.run(service, kind="ellipse", **kwargs)
