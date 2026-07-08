# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``core.run_workbench_command``."""

from __future__ import annotations

from VibeCADTransactions import run_freecad_transaction


TOOL_SPEC = {'contextual': True,
 'description': 'Fallback: run an exact FreeCAD GUI command. Use only when no '
                'structured VibeCAD tool covers the operation; may open a task panel.',
 'name': 'core.run_workbench_command',
 'parameters': {'properties': {'command_name': {'description': 'Exact registered GUI '
                                                               'command name, e.g. '
                                                               'PartDesign_Pad.',
                                                'type': 'string'}},
                'required': ['command_name'],
                'type': 'object'},
 'safety': 'SAFE_WRITE'}


def run(service, **kwargs):
    command_name = kwargs["command_name"]
    try:
        import FreeCADGui as Gui
    except Exception as exc:
        return {"ok": False, "command": command_name, "error": str(exc)}

    from . import core_get_active_document, core_get_task_panel

    before_doc = core_get_active_document.run(service)

    def _run():
        Gui.runCommand(command_name, 0)
        try:
            Gui.updateGui()
        except Exception:
            pass

    result = run_freecad_transaction(f"Run workbench command {command_name}", _run)
    after_doc = core_get_active_document.run(service)
    result.update(
        {
            "command": command_name,
            "active_workbench": _active_workbench_name(),
            "before": before_doc,
            "after": after_doc,
            "task_panel": core_get_task_panel.run(service),
        }
    )
    return result


def _active_workbench_name():
    try:
        import FreeCADGui as Gui

        workbench = Gui.activeWorkbench()
        if workbench:
            return workbench.name()
    except Exception:
        pass
    return None
