# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``draft.create_array``."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {'description': 'Create a native Draft ortho or polar array of a whole '
                'object. Use partdesign.pattern to repeat features inside a Body. '
                'Set fuse=true only when touching copies must merge.',
 'name': 'draft.create_array',
 'parameters': {'properties': {'array_type': {'description': 'ortho: rectangular grid; polar: circular arrangement.',
                                              'enum': ['ortho', 'polar'],
                                              'type': 'string'},
                               'center_x': {'description': 'polar only: center X in mm.',
                                            'type': 'number'},
                               'center_y': {'description': 'polar only: center Y in mm.',
                                            'type': 'number'},
                               'center_z': {'description': 'polar only: center Z in mm.',
                                            'type': 'number'},
                               'interval_x': {'description': 'ortho only: X spacing in mm (default 10).',
                                              'type': 'number'},
                               'interval_y': {'description': 'ortho only: Y spacing in mm.',
                                              'type': 'number'},
                               'interval_z': {'description': 'ortho only: Z spacing in mm.',
                                              'type': 'number'},
                               'label': {'type': 'string'},
                               'number_x': {'description': 'ortho only: copies along X (default 2).',
                                            'type': 'integer'},
                               'number_y': {'description': 'ortho only: copies along Y (default 1).',
                                            'type': 'integer'},
                               'number_z': {'description': 'ortho only: copies along Z (default 1).',
                                            'type': 'integer'},
                               'object_name': {'description': 'Object name or label to array.',
                                               'type': 'string'},
                               'polar_angle': {'description': 'polar only: total sweep angle in degrees (default 360).',
                                               'type': 'number'},
                               'polar_count': {'description': 'polar only: number of copies including the original (default 4).',
                                               'type': 'integer'},
                               'use_link': {'description': 'Create a lightweight Link array instead of copies (default false). Link arrays cannot be fused.',
                                            'type': 'boolean'},
                               'fuse': {'description': 'Fuse touching/overlapping copies into one connected solid (default false; requires use_link=false).',
                                        'type': 'boolean'}},
                'required': ['object_name', 'array_type'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'DraftWorkbench'}


def run(
    service,
    object_name: str,
    label: str = "VibeCAD Array",
    array_type: str = "ortho",
    number_x: int = 2,
    number_y: int = 1,
    number_z: int = 1,
    interval_x: float = 10.0,
    interval_y: float = 0.0,
    interval_z: float = 0.0,
    polar_count: int = 4,
    polar_angle: float = 360.0,
    center_x: float = 0.0,
    center_y: float = 0.0,
    center_z: float = 0.0,
    use_link: bool = False,
    fuse: bool = False,
) -> dict[str, Any]:
    source = service._get_document_object(object_name)
    if source is None:
        return {"ok": False, "error": f"Object not found: {object_name}"}
    kind = str(array_type or "ortho").lower().strip()
    if kind in {"orthogonal", "rect", "rectangular"}:
        kind = "ortho"
    if kind not in {"ortho", "polar"}:
        return {"ok": False, "error": "array_type must be ortho or polar"}
    if kind == "ortho":
        counts = (int(number_x), int(number_y), int(number_z))
        if any(count < 1 for count in counts) or counts == (1, 1, 1):
            return {"ok": False, "error": "Ortho arrays need positive counts and at least one repeated axis."}
    else:
        if int(polar_count) < 2:
            return {"ok": False, "error": "Polar arrays need at least two copies."}
    if bool(fuse) and bool(use_link):
        return {
            "ok": False,
            "error": "fuse=true requires a regular array; set use_link=false.",
        }

    def _create() -> dict[str, Any]:
        import FreeCAD as App
        import Draft

        base = service._get_document_object(object_name)
        if base is None:
            raise RuntimeError(f"Object not found: {object_name}")
        if kind == "ortho":
            array_obj = Draft.make_ortho_array(
                base,
                App.Vector(float(interval_x), 0, 0),
                App.Vector(0, float(interval_y), 0),
                App.Vector(0, 0, float(interval_z)),
                int(number_x),
                int(number_y),
                int(number_z),
                use_link=bool(use_link),
            )
            metadata = {
                "array_type": "ortho",
                "counts": [int(number_x), int(number_y), int(number_z)],
                "intervals": [float(interval_x), float(interval_y), float(interval_z)],
            }
        else:
            center = App.Vector(float(center_x), float(center_y), float(center_z))
            array_obj = Draft.make_polar_array(
                base,
                int(polar_count),
                float(polar_angle),
                center,
                use_link=bool(use_link),
            )
            metadata = {
                "array_type": "polar",
                "count": int(polar_count),
                "angle": float(polar_angle),
                "center": [float(center_x), float(center_y), float(center_z)],
            }
        array_obj.Label = label
        if bool(fuse) and hasattr(array_obj, "Fuse"):
            array_obj.Fuse = True
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        shape = getattr(array_obj, "Shape", None)
        metadata.update(
            {
                "object": array_obj.Name,
                "label": array_obj.Label,
                "type": getattr(array_obj, "TypeId", ""),
                "base": base.Name,
                "use_link": bool(use_link),
                "fuse": bool(getattr(array_obj, "Fuse", False)),
                "solids": len(getattr(shape, "Solids", []) or []),
            }
        )
        return metadata

    transaction = run_freecad_transaction(
        f"Create Draft {kind} array: {object_name}",
        _create,
    )
    return {"ok": bool(transaction.get("ok")), "transaction": transaction, "draft": domain_runtime.draft_summary(service)}
