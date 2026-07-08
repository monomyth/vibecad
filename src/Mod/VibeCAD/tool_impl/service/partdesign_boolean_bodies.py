# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.boolean_bodies``."""

from __future__ import annotations

from typing import Any

from . import domain_runtime
from VibeCADTransactions import run_freecad_transaction


TOOL_SPEC = {'contextual': True,
 'description': 'Create a native PartDesign Boolean (fuse/cut/common) in a '
                'target Body using other Bodies as consumed tools.',
 'name': 'partdesign.boolean_bodies',
 'parameters': {'properties': {'label': {'type': 'string'},
                               'operation': {'description': 'Boolean operation: fuse, cut, or common.',
                                             'enum': ['fuse', 'cut', 'common'],
                                             'type': 'string'},
                               'target_body_name': {'description': 'Target Body internal name or visible label where the Boolean feature is created.',
                                                    'type': 'string'},
                               'tool_body_names': {'description': 'Tool Body internal names or visible labels consumed by the native Boolean.',
                                                   'items': {'type': 'string'},
                                                   'type': 'array'}},
                'required': ['target_body_name', 'tool_body_names'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'PartDesignWorkbench'}


_OPERATION_TYPES = {
    "fuse": 0,
    "cut": 1,
    "common": 2,
}


def run(
    service,
    target_body_name: str,
    tool_body_names: list[str],
    operation: str = "fuse",
    label: str = "VibeCAD PartDesign Boolean",
) -> dict[str, Any]:
    target_body = service._get_partdesign_body(target_body_name)
    if target_body is None:
        return {"ok": False, "error": f"Target PartDesign Body not found: {target_body_name}"}
    requested_operation = str(operation or "fuse").lower()
    if requested_operation not in _OPERATION_TYPES:
        return {"ok": False, "error": "operation must be fuse, cut, or common."}
    tool_names = [str(item) for item in (tool_body_names or [])]
    if not tool_names:
        return {"ok": False, "error": "At least one tool Body is required."}
    tool_bodies = []
    for name in tool_names:
        body = service._get_partdesign_body(name)
        if body is None:
            return {"ok": False, "error": f"Tool PartDesign Body not found: {name}"}
        if body.Name == target_body.Name:
            return {"ok": False, "error": "Target Body cannot also be a Boolean tool Body."}
        tool_bodies.append(body)

    def _boolean() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target = service._get_partdesign_body(target_body.Name)
        if target is None:
            raise RuntimeError(f"Target PartDesign Body not found: {target_body.Name}")
        tools = []
        for body in tool_bodies:
            resolved = service._get_partdesign_body(body.Name)
            if resolved is None:
                raise RuntimeError(f"Tool PartDesign Body not found: {body.Name}")
            tools.append(resolved)
        body_shape_before = domain_runtime.shape_summary(target)
        boolean = doc.addObject("PartDesign::Boolean", "VibeCAD_PD_Boolean")
        boolean.Label = label or "VibeCAD PartDesign Boolean"
        target.addObject(boolean)
        boolean.setObjects(tools)
        boolean.Type = _OPERATION_TYPES[requested_operation]
        target.Tip = boolean
        doc.recompute()
        boolean_name = boolean.Name
        boolean_label = getattr(boolean, "Label", boolean.Name)
        boolean_type = getattr(boolean, "TypeId", "")
        native_type = _property_value(boolean, "Type")
        effect = domain_runtime.finalize_partdesign_feature_effect(
            doc,
            target,
            boolean,
            "boolean",
            body_shape_before,
        )
        return {
            "document": doc.Name,
            "target_body": target.Name,
            "tool_bodies": [body.Name for body in tools],
            "feature": boolean_name,
            "label": boolean_label,
            "type": boolean_type,
            "operation": requested_operation,
            "native_type": native_type,
            **effect,
        }

    transaction = run_freecad_transaction(
        f"Create PartDesign Boolean {requested_operation} in {getattr(target_body, 'Label', target_body.Name)}",
        _boolean,
    )
    transaction_result = transaction.get("result", {}) if isinstance(transaction.get("result"), dict) else {}
    feature_effect = transaction_result.get("feature_effect")
    effective = not isinstance(feature_effect, dict) or bool(feature_effect.get("ok"))
    ok = bool(transaction.get("ok")) and effective
    error = None
    likely_cause = None
    if not transaction.get("ok"):
        error = transaction.get("error") or "PartDesign Boolean failed."
    elif not effective:
        error, likely_cause = domain_runtime.describe_ineffective_partdesign_feature(
            "boolean",
            feature_shape=transaction_result.get("feature_shape"),
            feature_effect=feature_effect,
            feature_state=transaction_result.get("feature_state"),
            report_errors=domain_runtime.recompute_errors(transaction),
            rolled_back=bool(transaction_result.get("rolled_back_feature")),
            lead_in="PartDesign Boolean was created but did not produce an effective body shape change.",
        )
    return {
        "ok": ok,
        **({"error": error, "recoverable": True} if error else {}),
        "transaction": transaction,
        "partdesign": domain_runtime.partdesign_summary(service),
        "active_feature": transaction_result.get("feature"),
        "feature_shape": transaction_result.get("feature_shape"),
        "feature_state": transaction_result.get("feature_state"),
        "likely_cause": likely_cause,
        "body_shape_before": transaction_result.get("body_shape_before"),
        "body_shape_after": transaction_result.get("body_shape_after"),
        "body_shape_delta": transaction_result.get("body_shape_delta"),
        "feature_effect": feature_effect,
        "rolled_back_feature": transaction_result.get("rolled_back_feature"),
        "body_shape_after_rollback": transaction_result.get("body_shape_after_rollback"),
    }


def _property_value(obj, name: str):
    try:
        value = getattr(obj, name)
    except Exception:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
