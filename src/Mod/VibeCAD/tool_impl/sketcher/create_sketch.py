# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Sketcher sketch creation tool."""

from __future__ import annotations

from typing import Any

from .common import profile_validation, run_freecad_transaction, solver_status


TOOL_SPEC = {
    "name": "sketcher.create_sketch",
    "description": "Create a standalone Sketcher sketch. In PartDesign workflows use partdesign.create_sketch so the sketch lives inside a Body.",
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "support_type": {
                "type": "string",
                "enum": ["origin_plane", "face", "datum_plane", "none"],
            },
            "plane": {
                "type": "string",
                "enum": ["XY_Plane", "XZ_Plane", "YZ_Plane"],
            },
            "support_object": {"type": "string"},
            "subelement": {"type": "string"},
            "map_mode": {"type": "string"},
            "open_for_edit": {"type": "boolean"},
        },
    },
}


def _placement_for_origin_plane(plane: str):
    import FreeCAD as App

    clean = str(plane or "XY_Plane")
    if clean == "XY_Plane":
        return App.Placement()
    if clean == "XZ_Plane":
        return App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(1, 0, 0), 90))
    if clean == "YZ_Plane":
        return App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 1, 0), 90))
    raise ValueError("plane must be XY_Plane, XZ_Plane, or YZ_Plane.")


def run(
    service: Any,
    label: str = "Sketch",
    support_type: str = "origin_plane",
    plane: str = "XY_Plane",
    support_object: str | None = None,
    subelement: str | None = None,
    map_mode: str = "FlatFace",
    open_for_edit: bool = True,
) -> dict[str, Any]:
    clean_support = str(support_type or "origin_plane")
    if clean_support not in {"origin_plane", "face", "datum_plane", "none"}:
        return {
            "ok": False,
            "error": "support_type must be origin_plane, face, datum_plane, or none.",
        }
    if clean_support in {"face", "datum_plane"} and not support_object:
        return {"ok": False, "error": "support_object is required for face or datum_plane support."}

    def _create() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument or App.newDocument("VibeCAD")
        sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
        sketch.Label = label or "Sketch"
        support_result: dict[str, Any] = {"support_type": clean_support}
        if clean_support == "origin_plane":
            sketch.Placement = _placement_for_origin_plane(plane)
            support_result["plane"] = str(plane or "XY_Plane")
        elif clean_support == "none":
            support_result["plane"] = None
        else:
            target = doc.getObject(str(support_object))
            if target is None:
                for candidate in doc.Objects:
                    if getattr(candidate, "Label", None) == str(support_object):
                        target = candidate
                        break
            if target is None:
                raise RuntimeError(f"Support object not found: {support_object}")
            sketch.AttachmentSupport = [(target, str(subelement or ""))]
            if hasattr(sketch, "MapMode"):
                sketch.MapMode = str(map_mode or "FlatFace")
            support_result.update(
                {
                    "support_object": getattr(target, "Name", None),
                    "support_label": getattr(target, "Label", getattr(target, "Name", None)),
                    "subelement": str(subelement or ""),
                    "map_mode": getattr(sketch, "MapMode", None),
                }
            )
        doc.recompute()
        edit_opened = False
        if bool(open_for_edit):
            try:
                import FreeCADGui as Gui

                Gui.ActiveDocument.setEdit(sketch.Name)
                Gui.updateGui()
                edit_opened = True
            except Exception:
                edit_opened = False
        return {
            "document": doc.Name,
            "sketch": sketch.Name,
            "sketch_label": sketch.Label,
            "type": getattr(sketch, "TypeId", ""),
            "support": support_result,
            "edit_opened": edit_opened,
            "active_workbench": service.active_workbench_name(),
            "sketcher": service.sketcher_summary(sketch.Name),
            "solver_status": solver_status(service, sketch),
            "profile_validation": profile_validation(service, sketch),
        }

    transaction = run_freecad_transaction("Create Sketcher sketch", _create)
    sketch_name = None
    if isinstance(transaction.get("result"), dict):
        sketch_name = transaction["result"].get("sketch")
    sketch = service._get_sketch(sketch_name)
    return {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "active_sketch": sketch_name,
        "sketcher": service.sketcher_summary(sketch_name),
        "solver_status": solver_status(service, sketch),
        "profile_validation": profile_validation(service, sketch),
        "next_actions": service._sketch_next_actions(sketch),
    }
