# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for the shared VibeCAD assistant."""

from __future__ import annotations

import FreeCAD as App


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"{message}\n")


try:
    from PySide import QtCore

    import VibeCADGui

    VibeCADGui.ensure_commands_registered()

    def _open_startup_assistant() -> None:
        try:
            import VibeCADGui as _VibeCADGui

            _VibeCADGui.ensure_commands_registered()
            _VibeCADGui.show_assistant_for_active_workbench()
        except Exception as exc:
            try:
                import FreeCAD as _App

                _App.Console.PrintWarning(
                    f"VibeCAD assistant startup open failed: {exc}\n"
                )
            except Exception:
                pass

    def _setup_always_on_grid() -> None:
        try:
            import VibeCADGrid

            VibeCADGrid.setup()
        except Exception as exc:
            try:
                import FreeCAD as _App

                _App.Console.PrintWarning(f"VibeCAD grid startup setup failed: {exc}\n")
            except Exception:
                pass

    QtCore.QTimer.singleShot(0, _open_startup_assistant)
    QtCore.QTimer.singleShot(0, _setup_always_on_grid)
except Exception as exc:
    _warn(f"VibeCAD GUI bootstrap failed: {exc}")
