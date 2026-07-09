# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.set_feature_dimensions``."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {'contextual': True,
 'description': 'Edit dimension properties of an existing PartDesign feature in '
                'place (e.g. Pad Length, AdditiveBox Length/Width/Height) — the '
                'parametric way to resize without rebuilding the feature.',
 'name': 'partdesign.set_feature_dimensions',
 'parameters': {'properties': {'feature_name': {'description': 'PartDesign feature name or label to edit.',
                                                'type': 'string'},
                               'height': {'description': 'New Height in mm, if the feature has one.',
                                          'type': 'number'},
                               'length': {'description': 'New Length in mm, if the feature has one.',
                                          'type': 'number'},
                               'radius': {'description': 'New Radius in mm, if the feature has one.',
                                          'type': 'number'},
                               'width': {'description': 'New Width in mm, if the feature has one.',
                                         'type': 'number'}},
                'required': ['feature_name'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'PartDesignWorkbench'}


def run(
    service,
    feature_name: str,
    length: float | None = None,
    width: float | None = None,
    height: float | None = None,
    radius: float | None = None,
) -> dict[str, Any]:
    feature = service._get_document_object(feature_name)
    if feature is None:
        return {"ok": False, "error": f"PartDesign feature not found: {feature_name}"}
    type_id = getattr(feature, "TypeId", "")
    editable = {
        "Length": length,
        "Width": width,
        "Height": height,
        "Radius": radius,
    }
    if not type_id.startswith("PartDesign::"):
        return {
            "ok": False,
            "error": f"Object is not a PartDesign feature: {feature_name}",
            "type": type_id,
        }
    available = sorted(key for key in editable if hasattr(feature, key))
    unsupported = sorted(
        key for key, value in editable.items() if value is not None and key not in available
    )
    if unsupported:
        guidance = (
            f"Available dimension properties: {', '.join(available)}."
            if available
            else "This feature has no editable Length/Width/Height/Radius properties."
        )
        return {
            "ok": False,
            "error": (
                f"Unsupported dimension(s) for {feature_name}: "
                f"{', '.join(unsupported)}. {guidance}"
            ),
            "type": type_id,
            "unsupported_dimensions": unsupported,
            "available_dimensions": available,
            "retry_same_call": False,
        }
    try:
        requested = {
            key: float(value)
            for key, value in editable.items()
            if value is not None and key in available
        }
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "error": f"PartDesign feature dimensions must be numeric: {exc}",
            "type": type_id,
            "available_dimensions": available,
            "retry_same_call": False,
        }
    if not requested:
        return {
            "ok": False,
            "error": "No editable PartDesign dimension properties were provided.",
            "type": type_id,
            "available_dimensions": available,
        }
    if any(value <= 0 for value in requested.values()):
        return {"ok": False, "error": "PartDesign feature dimensions must be positive."}

    def _set() -> dict[str, Any]:
        import FreeCAD as App

        target = service._get_document_object(feature_name)
        if target is None:
            raise RuntimeError(f"PartDesign feature not found: {feature_name}")
        before = {
            key: float(getattr(target, key))
            for key in requested
            if hasattr(target, key)
        }
        for key, value in requested.items():
            setattr(target, key, value)
        doc = App.ActiveDocument
        if doc is not None:
            doc.recompute()
        after = {
            key: float(getattr(target, key))
            for key in requested
            if hasattr(target, key)
        }
        return {
            "feature": target.Name,
            "label": getattr(target, "Label", target.Name),
            "type": getattr(target, "TypeId", ""),
            "before": before,
            "after": after,
        }

    transaction = run_freecad_transaction(
        f"Edit PartDesign feature dimensions: {feature_name}",
        _set,
    )
    return {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "partdesign": domain_runtime.partdesign_summary(service),
    }
