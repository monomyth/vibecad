# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.create_sketch``."""

from __future__ import annotations

from typing import Any

from . import core_get_active_document, core_get_task_panel, partdesign_find_subelements
from VibeCADTransactions import run_freecad_transaction

TOOL_SPEC = {'contextual': True,
 'description': 'Create a Sketch inside a PartDesign Body. Use origin planes for '
                'governing layouts, datum planes for loft/sweep sections, and '
                "support_type='face' with subelement or normal for feature sketches "
                'on existing solids.',
 'name': 'partdesign.create_sketch',
 'parameters': {'properties': {'body_name': {'description': 'Optional target PartDesign Body internal name or visible label returned by partdesign.create_body or partdesign.get_bodies.',
                                              'type': 'string'},
                               'label': {'type': 'string'},
                               'support_type': {'description': "Attachment support kind: 'origin_plane' (default, uses plane), 'datum_plane' or 'face' (both use support_object).",
                                                'enum': ['origin_plane', 'datum_plane', 'face'],
                                                'type': 'string'},
                               'plane': {'description': "Body origin plane, used when support_type='origin_plane'.",
                                         'enum': ['XY_Plane', 'XZ_Plane', 'YZ_Plane'],
                                         'type': 'string'},
                               'support_object': {'description': 'Datum plane or solid feature internal name or label, required for datum_plane and face support.',
                                                  'type': 'string'},
                               'subelement': {'description': "Planar face subelement such as 'Face3'. For support_type='face', provide this or normal.",
                                               'type': 'string'},
                               'normal': {'description': 'For support_type=face: resolve or validate a planar face by outward normal, e.g. {"z": 1} for the top face.',
                                          'properties': {'x': {'type': 'number'},
                                                         'y': {'type': 'number'},
                                                         'z': {'type': 'number'}},
                                          'type': 'object'},
                               'normal_tolerance_degrees': {'description': 'Angular tolerance for normal face selection. Default 5 degrees.',
                                                            'type': 'number'},
                               'map_mode': {'description': 'Attachment MapMode (default FlatFace).',
                                            'type': 'string'}},
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'PartDesignWorkbench'}


def run(
    service,
    label: str = "Sketch",
    plane: str = "XY_Plane",
    body_name: str | None = None,
    support_type: str = "origin_plane",
    support_object: str | None = None,
    subelement: str | None = None,
    normal: dict[str, Any] | None = None,
    normal_tolerance_degrees: float = 5.0,
    map_mode: str = "FlatFace",
) -> dict[str, Any]:
    clean_support_type = str(support_type or "origin_plane")
    if clean_support_type not in {"origin_plane", "datum_plane", "face"}:
        return {
            "ok": False,
            "error": "support_type must be origin_plane, datum_plane, or face.",
        }
    requested_plane = str(plane or "XY_Plane")
    if clean_support_type == "origin_plane" and requested_plane not in {
        "XY_Plane",
        "XZ_Plane",
        "YZ_Plane",
    }:
        return {
            "ok": False,
            "error": "Only default origin planes are supported: XY_Plane, XZ_Plane, YZ_Plane.",
        }
    if clean_support_type in {"datum_plane", "face"} and not support_object:
        return {
            "ok": False,
            "error": "support_object is required when support_type is datum_plane or face.",
        }
    if clean_support_type == "face" and not str(subelement or "").strip() and normal is None:
        return {
            "ok": False,
            "error": "subelement or normal is required when support_type is face.",
        }

    def _create() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument or App.newDocument("VibeCAD")

        target = None
        if clean_support_type in {"datum_plane", "face"}:
            target = doc.getObject(str(support_object))
            if target is None:
                for candidate in doc.Objects:
                    if getattr(candidate, "Label", None) == str(support_object):
                        target = candidate
                        break
            if target is None:
                raise RuntimeError(f"Support object not found: {support_object}")

        body = service._get_partdesign_body(body_name) if body_name else None
        if body_name and body is None:
            raise RuntimeError(f"PartDesign Body not found by name or label: {body_name}")
        if body is None and target is not None:
            getter = getattr(target, "getParentGeoFeatureGroup", None)
            owner = getter() if callable(getter) else None
            if getattr(owner, "TypeId", "") == "PartDesign::Body":
                body = owner
        if body is None:
            active = getattr(doc, "ActiveObject", None)
            if getattr(active, "TypeId", "") == "PartDesign::Body":
                body = active
        if body is None:
            bodies = [
                obj
                for obj in doc.Objects
                if getattr(obj, "TypeId", "") == "PartDesign::Body"
            ]
            body = bodies[0] if bodies else None
        if body is None:
            body = doc.addObject("PartDesign::Body", "Body")
            body.Label = "Body"

        origin = getattr(body, "Origin", None)
        features = list(getattr(origin, "OriginFeatures", []) or [])
        def _normalized_origin_name(item) -> str:
            name = str(getattr(item, "Name", ""))
            label = str(getattr(item, "Label", "")).replace("-", "_")
            for value in (name, label):
                for plane_name in ("XY_Plane", "XZ_Plane", "YZ_Plane"):
                    if value == plane_name or value.startswith(plane_name):
                        return plane_name
            return name or label

        face_resolution: dict[str, Any] | None = None
        if clean_support_type == "origin_plane":
            support = next(
                (
                    item
                    for item in features
                    if _normalized_origin_name(item) == requested_plane
                ),
                None,
            )
            if support is None:
                raise RuntimeError(f"Body origin plane not found: {requested_plane}")
            support_subelement = ""
        else:
            support = target
            support_subelement = str(subelement or "")
            if clean_support_type == "face" and normal is not None:
                face_resolution = _resolve_face_support(
                    service,
                    str(support_object),
                    support_subelement,
                    normal,
                    normal_tolerance_degrees,
                )
                support_subelement = str(face_resolution["subelement"])

        sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
        sketch.Label = label or "Sketch"
        body.addObject(sketch)
        sketch.AttachmentSupport = [(support, support_subelement)]
        sketch.MapMode = str(map_mode or "FlatFace")
        doc.recompute()
        try:
            import FreeCADGui as Gui

            Gui.ActiveDocument.setEdit(sketch.Name)
            Gui.updateGui()
        except Exception:
            pass
        return {
            "document": doc.Name,
            "body": body.Name,
            "body_label": getattr(body, "Label", body.Name),
            "sketch": sketch.Name,
            "sketch_label": getattr(sketch, "Label", sketch.Name),
            "support_type": clean_support_type,
            "plane": requested_plane if clean_support_type == "origin_plane" else None,
            "subelement": support_subelement,
            "resolved_face": face_resolution,
            "attachment_support": getattr(support, "Name", None),
            "map_mode": getattr(sketch, "MapMode", None),
            "active_workbench": _active_workbench_name(),
            "document_summary": core_get_active_document.run(service),
            "sketcher": service.sketcher_summary(sketch.Name),
            "active_sketch": sketch.Name,
            "profile_status": service._sketch_profile_status(sketch),
            "next_actions": service._sketch_next_actions(sketch),
            "task_panel": core_get_task_panel.run(service),
        }

    support_description = (
        requested_plane if clean_support_type == "origin_plane" else str(support_object)
    )
    transaction = run_freecad_transaction(
        f"Create PartDesign sketch on {support_description}",
        _create,
    )
    return {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "active_sketch": (
            transaction.get("result", {}).get("sketch")
            if isinstance(transaction.get("result"), dict)
            else None
        ),
        "next_action": "Add closed sketch geometry, then call partdesign.extrude with operation='pad' or 'pocket'.",
    }


def _resolve_face_support(
    service: Any,
    support_object: str,
    requested_subelement: str,
    normal: dict[str, Any],
    normal_tolerance_degrees: float,
) -> dict[str, Any]:
    result = partdesign_find_subelements.run(
        service,
        object_name=support_object,
        element_type="face",
        geometry_type="plane",
        normal=normal,
        normal_tolerance_degrees=normal_tolerance_degrees,
        limit=100,
    )
    if not result.get("found"):
        raise RuntimeError(str(result.get("error") or "No matching planar face found."))
    matches = [
        item
        for item in result.get("matches", []) or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if not matches:
        raise RuntimeError(
            "No planar face on "
            f"{support_object} matches normal {normal} within "
            f"{float(normal_tolerance_degrees):g} degrees."
        )
    requested = str(requested_subelement or "").strip()
    if requested:
        selected = next((item for item in matches if item.get("name") == requested), None)
        if selected is None:
            candidates = ", ".join(str(item.get("name")) for item in matches[:8])
            raise RuntimeError(
                f"{requested} on {support_object} does not match normal {normal}. "
                f"Matching planar faces: {candidates or 'none'}."
            )
    else:
        selected = max(matches, key=lambda item: float(item.get("area", 0.0) or 0.0))
    return {
        "subelement": selected["name"],
        "normal": result.get("filters", {}).get("normal"),
        "normal_tolerance_degrees": float(normal_tolerance_degrees),
        "candidate_count": len(matches),
        "selected": selected,
        "candidates": matches[:8],
    }


def _active_workbench_name():
    try:
        import FreeCADGui as Gui

        workbench = Gui.activeWorkbench()
        if workbench:
            return workbench.name()
    except Exception:
        pass
    return None
