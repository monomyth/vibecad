# SPDX-License-Identifier: LGPL-2.1-or-later

"""List every point cloud in the active document with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "points.list_clouds",
    "description": (
        "List every point cloud in the active document with its exact "
        "internal name, point count, bounding box, and a small coordinate "
        "sample. Point clouds are read-only source data — never modify or "
        "delete them; importing and converting clouds runs in the FreeCAD GUI."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "PointsWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.points_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list point clouds: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
