# SPDX-License-Identifier: LGPL-2.1-or-later

"""List the BIM spatial structure and elements in the active document."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "bim.list_structure",
    "description": (
        "List every BIM object in the active document with its exact internal "
        "name, label, IFC type, and children. Use the returned internal names "
        "to target bim.create_wall level assignment, bim.add_window host "
        "walls, and other tools that take an exact object name."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "BIMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.bim_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list BIM objects: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
