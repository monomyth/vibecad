# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``part.thicken_surface``.

Turns an open surface/shell (for example a Surface workbench feature) into
a solid by offsetting it and filling the offset volume — the standard path
from surface-first modeling to a manufacturable solid.
"""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "description": (
        "Thicken a surface, shell, or face object into a solid by offsetting "
        "and filling it. Use after surface modeling; use thickness dressup "
        "instead to hollow an existing solid."
    ),
    "name": "part.thicken_surface",
    "parameters": {
        "properties": {
            "object_name": {
                "description": "Existing surface/shell object name or label.",
                "type": "string",
            },
            "thickness": {
                "description": (
                    "Wall thickness in mm (default 1.0). Sign selects the "
                    "offset direction relative to the surface normal."
                ),
                "type": "number",
            },
            "join": {
                "description": "Offset join type: 0=Arc, 1=Tangent, 2=Intersection (default 0).",
                "type": "integer",
            },
            "intersection": {
                "description": "Allow intersecting offset geometry (default false).",
                "type": "boolean",
            },
            "label": {"type": "string"},
        },
        "required": ["object_name"],
        "type": "object",
    },
    "safety": "SAFE_WRITE",
    # Cross-pack tool: surfaced by the Part and Surface packs via their
    # allowlists (like assembly.check_interference in PartDesign).
    "workbench": None,
    "contextual": True,
}


def run(
    service,
    object_name: str,
    thickness: float = 1.0,
    join: int = 0,
    intersection: bool = False,
    label: str = "VibeCAD Thickened Surface",
) -> dict[str, Any]:
    source = service._get_document_object(object_name)
    if source is None:
        return {"ok": False, "error": f"Object not found: {object_name}"}
    value = float(thickness)
    if value == 0.0:
        return {"ok": False, "error": "thickness must be non-zero."}
    join_type = int(join)
    if join_type not in (0, 1, 2):
        return {"ok": False, "error": "join must be 0 (Arc), 1 (Tangent), or 2 (Intersection)."}
    shape = getattr(source, "Shape", None)
    if shape is None or shape.isNull():
        return {
            "ok": False,
            "error": f"Object has no shape to thicken: {object_name}",
        }
    if not (getattr(shape, "Faces", None) or []):
        return {
            "ok": False,
            "error": (
                f"Object has no faces to thicken: {object_name}. Thickening "
                "needs a surface, shell, or face."
            ),
        }

    def _thicken() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        base = service._get_document_object(object_name)
        if base is None:
            raise RuntimeError(f"Object not found: {object_name}")
        feature = doc.addObject("Part::Offset", "VibeCAD_Thicken")
        feature.Source = base
        feature.Value = value
        feature.Mode = 0
        feature.Join = join_type
        feature.Intersection = bool(intersection)
        feature.SelfIntersection = False
        feature.Fill = True
        feature.Label = label or "VibeCAD Thickened Surface"
        if hasattr(base, "Visibility"):
            base.Visibility = False
        doc.recompute()
        result_shape = getattr(feature, "Shape", None)
        solids = list(getattr(result_shape, "Solids", []) or [])
        if result_shape is None or result_shape.isNull() or not solids:
            state = list(getattr(feature, "State", []) or [])
            raise RuntimeError(
                f"Thicken produced no solid (State={state}). Try a smaller "
                "thickness, a different join type, or check that the source "
                "surface is clean and non-self-intersecting."
            )
        return {
            "object": feature.Name,
            "label": feature.Label,
            "type": getattr(feature, "TypeId", ""),
            "source": base.Name,
            "thickness_mm": value,
            "join": join_type,
            "solids": len(solids),
            "valid": bool(result_shape.isValid()),
            "volume_mm3": round(float(getattr(result_shape, "Volume", 0.0) or 0.0), 3),
        }

    transaction = run_freecad_transaction(
        f"Thicken surface into solid: {object_name}",
        _thicken,
    )
    return {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "part": domain_runtime.part_summary(service),
    }
