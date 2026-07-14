# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fusion-style always-on 3D grid for VibeCAD.

Reuses the Draft workbench grid machinery (``Gui.Snapper`` + ``gridTracker``)
to keep a reference grid visible in every 3D view regardless of the active
workbench, similar to Fusion 360's viewport grid.

Behavior:

- Draft grid preferences (``alwaysShowGrid`` and ``GridHideInOtherWorkbenches``)
  are seeded exactly once, guarded by a flag parameter, so later manual
  changes made by the user in Draft preferences are never overridden.
- A lightweight observer on the MDI area initializes the grid for each 3D
  view at most once. Manually toggling the grid off in a view (e.g. via
  Draft's Toggle Grid) is respected: already-initialized views are skipped.
- The native ``VibeCAD_ToggleGrid`` command owns the View-menu action. This
  module only updates the global preference and existing per-view trackers;
  when no GUI document is open it updates the preference without creating the
  Draft Snapper or attempting to render a grid.
- The automatic always-on-at-startup behavior sits behind the ``Mod/VibeCAD``
  ``AlwaysShowGrid`` boolean preference (default: enabled) as a master
  kill-switch.

The module imports safely outside FreeCAD (guarded imports) so tooling such
as linters and test collectors can load it.
"""

from __future__ import annotations

from typing import Any

try:
    import FreeCAD as App
except ImportError:  # pragma: no cover - only outside FreeCAD (tooling/tests)
    App = None  # type: ignore[assignment]

_PARAM_ROOT = "User parameter:BaseApp/Preferences/"
_VIBECAD_PARAM_PATH = _PARAM_ROOT + "Mod/VibeCAD"
_DRAFT_PARAM_PATH = _PARAM_ROOT + "Mod/Draft"
_SEED_FLAG = "GridPreferencesSeeded"

# Fast-path identity cache of views already handled by this module. The
# authoritative "grid already exists for this view" check is membership in
# ``Gui.Snapper.trackers[0]`` (equality-based, stable across Python wrapper
# objects); this id() set merely short-circuits repeated activations of the
# same wrapper. Each view is initialized at most once either way.
_seen_views: set[int] = set()
_observer_installed = False


def _warn(message: str) -> None:
    """Print a console warning when FreeCAD is available."""
    if App is not None:
        App.Console.PrintWarning(f"VibeCAD grid: {message}\n")


def is_enabled() -> bool:
    """Return True when the always-on grid feature is enabled."""
    if App is None:
        return False
    return App.ParamGet(_VIBECAD_PARAM_PATH).GetBool("AlwaysShowGrid", True)


def seed_grid_preferences() -> None:
    """Seed Draft grid preferences once so the grid shows app-wide.

    Write-once: guarded by a flag in the VibeCAD parameter group. If the user
    later disables the grid via Draft preferences, we never force it back on.
    """
    if App is None:
        return
    vibecad_params = App.ParamGet(_VIBECAD_PARAM_PATH)
    if vibecad_params.GetBool(_SEED_FLAG, False):
        return
    draft_params = App.ParamGet(_DRAFT_PARAM_PATH)
    draft_params.SetBool("alwaysShowGrid", True)
    draft_params.SetBool("GridHideInOtherWorkbenches", False)
    vibecad_params.SetBool(_SEED_FLAG, True)


def _get_snapper() -> Any:
    """Return the shared Draft Snapper, creating it if it does not exist."""
    import FreeCADGui as Gui

    snapper = getattr(Gui, "Snapper", None)
    if snapper is None:
        from draftguitools import gui_snapper

        snapper = gui_snapper.Snapper()
        Gui.Snapper = snapper
    return snapper


def _grid_should_always_show() -> bool:
    """Return the current value of the Draft ``alwaysShowGrid`` preference."""
    if App is None:
        return False
    return App.ParamGet(_DRAFT_PARAM_PATH).GetBool("alwaysShowGrid", False)


def is_grid_visible() -> bool:
    """Return True when the grid is visible in at least one 3D view.

    Reads the actual tracker state so the answer stays correct even when the
    grid was toggled through Draft's own command. Falls back to the
    ``alwaysShowGrid`` preference before any tracker exists (e.g. at startup).
    """
    if App is None or not App.GuiUp:
        return False
    try:
        import FreeCADGui as Gui

        snapper = getattr(Gui, "Snapper", None)
        if snapper is not None and snapper.trackers[1]:
            return any(
                bool(getattr(grid, "Visible", False)) for grid in snapper.trackers[1]
            )
    except Exception as exc:
        _warn(f"grid visibility query failed: {exc}")
    return _grid_should_always_show()


def toggle_grid(show: bool | None = None) -> None:
    """Show or hide the grid in every 3D view, current and future.

    Writes the Draft ``alwaysShowGrid`` preference (so views opened later
    follow suit) and flips all existing grid trackers. With ``show=None`` the
    current visibility is inverted.
    """
    if App is None or not App.GuiUp:
        return
    try:
        if show is None:
            show = not is_grid_visible()
        show = bool(show)
        App.ParamGet(_DRAFT_PARAM_PATH).SetBool("alwaysShowGrid", show)

        import FreeCADGui as Gui

        # A native View-menu command remains available before a document is
        # opened. In that state the preference is all that should change:
        # creating Draft's Snapper would attempt to initialize view trackers
        # without a 3D view.
        if Gui.activeDocument() is None:
            return

        snapper = _get_snapper()
        if show:
            for grid in snapper.trackers[1]:
                grid.show_always = True
            # Turn on every grid tracker, then make sure the active view has
            # one at all (creates it if needed) and align it to the working
            # plane.
            snapper.show()
            snapper.setTrackers()
        else:
            for grid in snapper.trackers[1]:
                grid.show_always = False
                grid.off()
    except Exception as exc:
        _warn(f"grid toggle failed: {exc}")


# ---------------------------------------------------------------------------
# Per-view grid initialization
# ---------------------------------------------------------------------------


def _show_grid_in_active_view() -> None:
    """Initialize and show the grid for the active 3D view, at most once.

    Only acts while the Draft ``alwaysShowGrid`` preference is set (i.e. the
    grid is toggled on). Views whose grid tracker already exists are skipped
    so a manual grid toggle by the user is never fought.
    """
    if App is None or not App.GuiUp:
        return
    if not _grid_should_always_show():
        return
    try:
        from draftutils import gui_utils

        view = gui_utils.get_3d_view()
        if view is None:
            return
        key = id(view)
        if key in _seen_views:
            return
        snapper = _get_snapper()
        if view in snapper.trackers[0]:
            # Grid tracker already exists (e.g. created by Draft itself or a
            # previous wrapper of the same view); respect its current state.
            _seen_views.add(key)
            return
        # Creates the per-view trackers; because alwaysShowGrid is set, the
        # new grid gets show_always=True and is displayed immediately.
        snapper.setTrackers()
        _seen_views.add(key)
    except Exception as exc:
        _warn(f"unable to show grid in active view: {exc}")


def _on_sub_window_activated(_window: Any = None) -> None:
    """Defer grid initialization until the view activation settles."""
    try:
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, _show_grid_in_active_view)
    except Exception as exc:
        _warn(f"deferred grid update failed: {exc}")


def _install_view_observer() -> None:
    """Install the MDI observer that grids new 3D views (idempotent)."""
    global _observer_installed
    if _observer_installed:
        return
    try:
        import FreeCADGui as Gui
        from PySide import QtWidgets

        main_window = Gui.getMainWindow()
        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        if mdi_area is None:
            _warn("MDI area not found; grid observer not installed")
            return
        mdi_area.subWindowActivated.connect(_on_sub_window_activated)
        _observer_installed = True
        # Handle a 3D view that is already active at setup time.
        _on_sub_window_activated()
    except Exception as exc:
        _warn(f"observer installation failed: {exc}")


def setup() -> None:
    """Install the grid feature (idempotent).

    Preference seeding is gated by the ``AlwaysShowGrid`` kill-switch, so the
    grid only turns itself on at startup when the feature is enabled. The view
    observer follows the Draft ``alwaysShowGrid`` preference for current and
    future 3D views. View-menu ownership remains entirely native.
    """
    if App is None or not App.GuiUp:
        return
    if is_enabled():
        seed_grid_preferences()
    _install_view_observer()
