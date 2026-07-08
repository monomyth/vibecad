# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``part.dressup``.

Consolidates the retired ``part.apply_fillet``, ``part.apply_chamfer``, and
``part.apply_thickness`` tools behind a single ``operation`` discriminator.
"""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction
from . import domain_runtime


TOOL_SPEC = {'description': 'Apply a Part fillet, chamfer, or thickness feature. '
                'Use after the base shape is correct, not as a substitute for it.',
 'name': 'part.dressup',
 'parameters': {'properties': {'operation': {'description': "One of 'fillet', 'chamfer', "
                                                            "or 'thickness'.",
                                             'enum': ['fillet', 'chamfer', 'thickness'],
                                             'type': 'string'},
                               'object_name': {'description': 'Existing document object '
                                                              'to dress up.',
                                               'type': 'string'},
                               'label': {'description': 'Label for the created feature.',
                                         'type': 'string'},
                               'radius': {'description': 'Fillet radius in mm '
                                                         '(fillet only, default 1.0).',
                                          'type': 'number'},
                               'distance': {'description': 'Chamfer distance in mm '
                                                           '(chamfer only, default 1.0).',
                                            'type': 'number'},
                               'edge_indices': {'description': '1-based edge indices to '
                                                               'dress up (fillet/chamfer). '
                                                               'Defaults to the first 12 '
                                                               'edges.',
                                                'items': {'type': 'integer'},
                                                'type': 'array'},
                               'wall_thickness': {'description': 'Shell wall thickness in '
                                                                 'mm (thickness only, '
                                                                 'default 1.5).',
                                                  'type': 'number'},
                               'face_names': {'description': 'Faces to remove for the '
                                                             'shell opening, e.g. '
                                                             "['Face6'] (thickness only).",
                                              'items': {'type': 'string'},
                                              'type': 'array'},
                               'inward': {'description': 'Thicken inward (default true; '
                                                         'thickness only).',
                                          'type': 'boolean'},
                               'mode': {'description': 'Part Thickness mode (0=Skin, '
                                                       '1=Pipe, 2=RectoVerso; thickness '
                                                       'only).',
                                        'type': 'integer'},
                               'join': {'description': 'Part Thickness join type (0=Arc, '
                                                       '1=Tangent, 2=Intersection; '
                                                       'thickness only).',
                                        'type': 'integer'}},
                'required': ['operation', 'object_name'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'PartWorkbench'}


_OPERATIONS = ("fillet", "chamfer", "thickness")


def run(
    service,
    operation: str,
    object_name: str,
    label: str | None = None,
    radius: float = 1.0,
    distance: float = 1.0,
    edge_indices: list[int] | None = None,
    wall_thickness: float = 1.5,
    face_names: list[str] | None = None,
    inward: bool = True,
    mode: int = 0,
    join: int = 0,
) -> dict[str, Any]:
    op = str(operation).strip().lower()
    if op not in _OPERATIONS:
        return {
            "ok": False,
            "error": f"Unknown operation: {operation!r}. Valid operations: {list(_OPERATIONS)}",
        }
    source = service._get_document_object(object_name)
    if source is None:
        return {"ok": False, "error": f"Object not found: {object_name}"}

    if op == "fillet":
        return _run_fillet(service, object_name, label or "VibeCAD Fillet", radius, edge_indices)
    if op == "chamfer":
        return _run_chamfer(service, object_name, label or "VibeCAD Chamfer", distance, edge_indices)
    return _run_thickness(
        service,
        object_name,
        label or "VibeCAD Thickness",
        wall_thickness,
        face_names,
        inward,
        mode,
        join,
    )


def _select_edges(shape: Any, object_name: str, edge_indices: list[int] | None, verb: str) -> list[Any]:
    edges = list(getattr(shape, "Edges", []) or [])
    if not edges:
        raise RuntimeError(f"Object has no {verb} edges: {object_name}")
    indices = edge_indices if edge_indices is not None else list(range(1, min(len(edges), 12) + 1))
    selected_edges = []
    for index in indices:
        edge_index = int(index)
        if 1 <= edge_index <= len(edges):
            selected_edges.append(edges[edge_index - 1])
    if not selected_edges:
        raise RuntimeError(f"No valid edge indices selected for {verb}.")
    return selected_edges


def _finalize(service, transaction: dict[str, Any], op: str) -> dict[str, Any]:
    return domain_runtime.build_mutation_result(
        transaction,
        extra={
            "operation": op,
            "part": domain_runtime.part_summary(service),
        },
    )


def _run_fillet(
    service,
    object_name: str,
    label: str,
    radius: float,
    edge_indices: list[int] | None,
) -> dict[str, Any]:
    if float(radius) <= 0:
        return {"ok": False, "error": "radius must be positive"}

    def _fillet() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target = service._get_document_object(object_name)
        if target is None:
            raise RuntimeError(f"Object not found: {object_name}")
        shape = getattr(target, "Shape", None)
        selected_edges = _select_edges(shape, object_name, edge_indices, "filletable")
        feature = doc.addObject("Part::Feature", "VibeCAD_Fillet")
        feature.Label = label
        feature.Shape = shape.makeFillet(float(radius), selected_edges)
        doc.recompute()
        return {
            "object": feature.Name,
            "label": feature.Label,
            "type": feature.TypeId,
            "source": target.Name,
            "radius": float(radius),
            "edge_count": len(selected_edges),
        }

    transaction = run_freecad_transaction(
        f"Apply Part fillet to {object_name}",
        _fillet,
    )
    return _finalize(service, transaction, "fillet")


def _run_chamfer(
    service,
    object_name: str,
    label: str,
    distance: float,
    edge_indices: list[int] | None,
) -> dict[str, Any]:
    if float(distance) <= 0:
        return {"ok": False, "error": "distance must be positive"}

    def _chamfer() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target = service._get_document_object(object_name)
        if target is None:
            raise RuntimeError(f"Object not found: {object_name}")
        shape = getattr(target, "Shape", None)
        selected_edges = _select_edges(shape, object_name, edge_indices, "chamferable")
        feature = doc.addObject("Part::Feature", "VibeCAD_Chamfer")
        feature.Label = label
        feature.Shape = shape.makeChamfer(float(distance), selected_edges)
        doc.recompute()
        return {
            "object": feature.Name,
            "label": feature.Label,
            "type": feature.TypeId,
            "source": target.Name,
            "distance": float(distance),
            "edge_count": len(selected_edges),
        }

    transaction = run_freecad_transaction(
        f"Apply Part chamfer to {object_name}",
        _chamfer,
    )
    return _finalize(service, transaction, "chamfer")


def _run_thickness(
    service,
    object_name: str,
    label: str,
    wall_thickness: float,
    face_names: list[str] | None,
    inward: bool,
    mode: int,
    join: int,
) -> dict[str, Any]:
    if float(wall_thickness) <= 0:
        return {"ok": False, "error": "wall_thickness must be positive"}
    selected_faces = [str(item) for item in (face_names or ["Face6"])]
    if not selected_faces:
        return {"ok": False, "error": "At least one face name is required."}

    def _thickness() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target = service._get_document_object(object_name)
        if target is None:
            raise RuntimeError(f"Object not found: {object_name}")
        shape = getattr(target, "Shape", None)
        faces = list(getattr(shape, "Faces", []) or [])
        if not faces:
            raise RuntimeError(f"Object has no faces for thickness: {object_name}")
        for face_name in selected_faces:
            if not face_name.startswith("Face"):
                raise RuntimeError(f"Invalid face name: {face_name}")
            try:
                face_index = int(face_name[4:])
            except ValueError as exc:
                raise RuntimeError(f"Invalid face name: {face_name}") from exc
            if face_index < 1 or face_index > len(faces):
                raise RuntimeError(f"Face name out of range for {object_name}: {face_name}")
        feature = doc.addObject("Part::Thickness", "VibeCAD_Thickness")
        feature.Label = label
        feature.Faces = (target, selected_faces)
        feature.Value = -float(wall_thickness) if inward else float(wall_thickness)
        feature.Mode = int(mode)
        feature.Join = int(join)
        doc.recompute()
        return {
            "object": feature.Name,
            "label": feature.Label,
            "type": feature.TypeId,
            "source": target.Name,
            "face_names": selected_faces,
            "wall_thickness": float(wall_thickness),
            "inward": bool(inward),
            "mode": int(mode),
            "join": int(join),
            "mode_label": str(getattr(feature, "Mode", "")),
            "join_label": str(getattr(feature, "Join", "")),
            "face_count": len(getattr(getattr(feature, "Shape", None), "Faces", []) or []),
            "solid_count": len(getattr(getattr(feature, "Shape", None), "Solids", []) or []),
            "volume": float(getattr(getattr(feature, "Shape", None), "Volume", 0.0) or 0.0),
        }

    transaction = run_freecad_transaction(
        f"Apply Part thickness to {object_name}",
        _thickness,
    )
    return _finalize(service, transaction, "thickness")
