# SPDX-License-Identifier: LGPL-2.1-or-later

"""List Inspection features and candidate geometry with exact names."""

from __future__ import annotations

from typing import Any

from . import domain_runtime


TOOL_SPEC = {
    "name": "inspection.list_features",
    "description": (
        "List Inspection features (nominal-versus-actual geometry "
        "comparisons) with their exact internal names, actual/nominal "
        "objects, search radius, and computed distance counts, plus the "
        "shaped and meshed objects available as comparison candidates. "
        "Creating and recomputing comparisons runs in the FreeCAD GUI; use "
        "this to read an existing inspection setup and its results."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "InspectionWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    try:
        summary = domain_runtime.inspection_summary(service)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not list inspection features: {exc}",
            "retry_same_call": False,
        }
    return {"ok": True, **summary}
