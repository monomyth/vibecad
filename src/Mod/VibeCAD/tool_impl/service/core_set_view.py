# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``core.set_view``."""

from __future__ import annotations


ORIENTATION_METHODS = {
    "front": "viewFront",
    "top": "viewTop",
    "right": "viewRight",
    "rear": "viewRear",
    "bottom": "viewBottom",
    "left": "viewLeft",
    "isometric": "viewIsometric",
    "axometric": "viewAxometric",
}

ALLOWED_ORIENTATIONS = tuple(ORIENTATION_METHODS) + ("none",)


TOOL_SPEC = {'description': 'Set camera orientation, visibility, and fit before '
                'inspection or screenshot capture.',
 'name': 'core.set_view',
 'parameters': {'properties': {'orientation': {'description': 'Standard camera orientation: '
                'front, top, right, rear, bottom, left, isometric, axometric, or '
                'none to keep the current camera. Default none.',
                'type': 'string'},
                'fit_all': {'description': 'When true, zoom/fit the view to all visible '
                'geometry after orientation and visibility changes. Default false.',
                'type': 'boolean'},
                'show_objects': {'description': 'Object Names or Labels to make visible.',
                'items': {'type': 'string'}, 'type': 'array'},
                'hide_objects': {'description': 'Object Names or Labels to hide.',
                'items': {'type': 'string'}, 'type': 'array'}},
                'type': 'object'},
 'safety': 'VIEW'}


def run(service, orientation=None, fit_all=False, show_objects=None, hide_objects=None, **kwargs):
    orientation_name = str(orientation or "none").strip().lower()
    if orientation_name not in ALLOWED_ORIENTATIONS:
        return {
            "ok": False,
            "error": f"Unknown orientation {orientation_name!r}.",
            "allowed_orientations": list(ALLOWED_ORIENTATIONS),
        }

    try:
        import FreeCAD as App
        import FreeCADGui as Gui
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    document = App.ActiveDocument
    if document is None:
        return {"ok": False, "error": "No active document."}

    shown, hidden, unknown = _apply_visibility(document, show_objects, hide_objects)

    gui_document = getattr(Gui, "ActiveDocument", None)
    view = getattr(gui_document, "ActiveView", None) if gui_document else None
    oriented = False
    fitted = False
    view_error = None
    if view is None:
        if orientation_name != "none" or fit_all:
            view_error = "No active 3D view is available."
    else:
        try:
            if orientation_name != "none":
                getattr(view, ORIENTATION_METHODS[orientation_name])()
                oriented = True
            if fit_all:
                view.fitAll()
                fitted = True
        except Exception as exc:
            view_error = str(exc)

    result = {
        "ok": view_error is None,
        "orientation": orientation_name,
        "oriented": oriented,
        "fit_all": fitted,
        "shown": shown,
        "hidden": hidden,
        "unknown_objects": unknown,
        "document": document.Name,
    }
    if view_error is not None:
        result["error"] = view_error
    return result


def _apply_visibility(document, show_objects, hide_objects):
    shown: list[str] = []
    hidden: list[str] = []
    unknown: list[str] = []
    for names, visible, applied in (
        (show_objects, True, shown),
        (hide_objects, False, hidden),
    ):
        for name in list(names or []):
            obj = _find_object(document, str(name))
            if obj is None:
                unknown.append(str(name))
                continue
            try:
                obj.ViewObject.Visibility = visible
                applied.append(obj.Name)
            except Exception:
                unknown.append(str(name))
    return shown, hidden, unknown


def _find_object(document, name):
    obj = document.getObject(name)
    if obj is not None:
        return obj
    matches = document.getObjectsByLabel(name)
    if matches:
        return matches[0]
    return None
