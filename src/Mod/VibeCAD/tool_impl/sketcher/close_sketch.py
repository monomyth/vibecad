# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Sketcher close/edit-exit tool."""

from __future__ import annotations

from typing import Any

from .common import get_sketch, no_sketch, profile_validation, run_freecad_transaction, solver_status


TOOL_SPEC = {
    "name": "sketcher.close_sketch",
    "description": (
        "Close the active Sketcher edit session and return solver/profile "
        "state. Use before creating PartDesign features from an open sketch."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active/first sketch.",
            },
        },
    },
}


def run(service: Any, sketch_name: str | None = None) -> dict[str, Any]:
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return no_sketch(sketch_name)

    def _close() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        closed_edit = False
        try:
            import FreeCADGui as Gui

            if Gui.ActiveDocument is not None:
                Gui.ActiveDocument.resetEdit()
                Gui.updateGui()
                closed_edit = True
        except Exception:
            closed_edit = False
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        return {
            "sketch": target.Name,
            "sketch_label": getattr(target, "Label", target.Name),
            "closed_edit": closed_edit,
            "active_workbench": service.active_workbench_name(),
            "task_panel": service.task_panel_summary(),
            "profile_status": service._sketch_profile_status(target),
            "solver_status": solver_status(service, target),
            "profile_validation": profile_validation(service, target),
        }

    transaction = run_freecad_transaction("Close Sketcher sketch", _close)
    updated = get_sketch(service, sketch.Name)
    profile_status = service._sketch_profile_status(updated)
    next_actions = []
    if profile_status.get("ready_for_pad"):
        next_actions.append(
            {
                "tool": "partdesign.extrude",
                "arguments": {"operation": "pad", "sketch_name": sketch.Name},
                "why": "The closed sketch is fully constrained and ready for an additive PartDesign feature.",
            }
        )
    if profile_status.get("ready_for_pocket"):
        next_actions.append(
            {
                "tool": "partdesign.extrude",
                "arguments": {"operation": "pocket", "sketch_name": sketch.Name},
                "why": "The closed sketch is fully constrained and ready for a subtractive PartDesign feature.",
            }
        )
    return {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "active_sketch": sketch.Name,
        "active_workbench": service.active_workbench_name(),
        "task_panel": service.task_panel_summary(),
        "profile_status": profile_status,
        "solver_status": solver_status(service, updated),
        "profile_validation": profile_validation(service, updated),
        "next_actions": next_actions or service._sketch_next_actions(updated),
    }
