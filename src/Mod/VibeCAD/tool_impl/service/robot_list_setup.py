# SPDX-License-Identifier: LGPL-2.1-or-later

"""List robot-simulation objects with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "robot.list_setup",
    "description": (
        "List robot-simulation objects (robots, trajectories, and related "
        "geometry) in the active document with exact internal names and "
        "their detected roles. Robot placement and trajectory editing run "
        "in the FreeCAD GUI; use this to read the current simulation setup."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "RobotWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.robot_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list robot-simulation objects: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
