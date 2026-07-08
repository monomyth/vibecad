# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.helix_profile``."""

from __future__ import annotations

from typing import Any

from . import domain_runtime
from VibeCADTransactions import run_freecad_transaction


TOOL_SPEC = {'contextual': True,
 'description': 'Create a native PartDesign AdditiveHelix/SubtractiveHelix by '
                'sweeping a profile along a helical path. Use for threads, '
                'springs, coils, worm gears, and auger flights.',
 'name': 'partdesign.helix_profile',
 'parameters': {'properties': {'angle': {'description': 'Helix taper angle in degrees (default 0 = constant radius).',
                                         'type': 'number'},
                               'growth': {'description': 'Radial growth in mm per turn; only used when native_mode=3.',
                                          'type': 'number'},
                               'height': {'description': 'Total helix height in mm along the axis (default 9).',
                                          'type': 'number'},
                               'label': {'type': 'string'},
                               'left_handed': {'description': 'Left-handed helix (default false = right-handed).',
                                               'type': 'boolean'},
                               'mode': {'description': 'additive adds material; subtractive cuts a helical groove.',
                                        'enum': ['additive', 'subtractive'],
                                        'type': 'string'},
                               'native_mode': {'description': 'Native Helix Mode integer: 0 pitch-height-angle (default), 1 pitch-turns-angle, 2 height-turns-angle, 3 height-turns-growth.',
                                               'type': 'integer'},
                               'pitch': {'description': 'Axial distance in mm per turn (default 3).',
                                         'type': 'number'},
                               'profile_sketch_name': {'description': 'Profile sketch (name or label) placed beside the axis; its offset from the axis sets the helix radius.',
                                                       'type': 'string'},
                               'reference_axis': {'description': 'Sketch axis used as helix axis (default V_Axis).',
                                                  'enum': ['H_Axis', 'V_Axis', 'N_Axis'],
                                                  'type': 'string'},
                               'reversed': {'description': 'Sweep in the opposite axis direction.',
                                            'type': 'boolean'},
                               'turns': {'description': 'Number of turns (default 3); used by native modes 1-3.',
                                         'type': 'number'}},
                'required': ['profile_sketch_name'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'PartDesignWorkbench'}


def run(
    service,
    profile_sketch_name: str,
    label: str = "VibeCAD Helix",
    mode: str = "additive",
    reference_axis: str = "V_Axis",
    pitch: float = 3.0,
    height: float = 9.0,
    turns: float = 3.0,
    angle: float = 0.0,
    growth: float = 0.0,
    native_mode: int = 0,
    left_handed: bool = False,
    reversed: bool = False,
) -> dict[str, Any]:
    profile = service._get_sketch(profile_sketch_name)
    if profile is None:
        return {"ok": False, "error": "Profile sketch not found.", "requested": profile_sketch_name}
    requested_mode = str(mode or "additive").lower()
    if requested_mode not in {"additive", "subtractive"}:
        return {"ok": False, "error": "mode must be additive or subtractive."}
    requested_axis = str(reference_axis or "V_Axis")
    if requested_axis not in {"H_Axis", "V_Axis", "N_Axis"}:
        return {"ok": False, "error": "reference_axis must be H_Axis, V_Axis, or N_Axis."}
    if float(pitch) <= 0:
        return {"ok": False, "error": "pitch must be positive."}
    if float(height) <= 0:
        return {"ok": False, "error": "height must be positive."}
    if float(turns) <= 0:
        return {"ok": False, "error": "turns must be positive."}
    if int(native_mode) not in {0, 1, 2, 3}:
        return {"ok": False, "error": "native_mode must be 0, 1, 2, or 3."}
    if float(angle) <= -89 or float(angle) >= 89:
        return {"ok": False, "error": "angle must be greater than -89 and less than 89 degrees."}

    def _helix() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target_profile = service._get_sketch(profile.Name)
        if target_profile is None:
            raise RuntimeError(f"Profile sketch not found: {profile.Name}")
        body = service._partdesign_body_for_feature(target_profile)
        if body is None:
            raise RuntimeError("No PartDesign Body found for helix.")
        body_shape_before = domain_runtime.shape_summary(body)
        type_name = "PartDesign::AdditiveHelix" if requested_mode == "additive" else "PartDesign::SubtractiveHelix"
        object_name = "VibeCAD_AdditiveHelix" if requested_mode == "additive" else "VibeCAD_SubtractiveHelix"
        helix = body.newObject(type_name, object_name)
        helix.Label = label or "VibeCAD Helix"
        helix.Profile = target_profile
        helix.ReferenceAxis = (target_profile, requested_axis)
        helix.Mode = int(native_mode)
        helix.Pitch = float(pitch)
        helix.Height = float(height)
        helix.Turns = float(turns)
        helix.Angle = float(angle)
        helix.Growth = float(growth)
        helix.LeftHanded = bool(left_handed)
        helix.Reversed = bool(reversed)
        body.Tip = helix
        doc.recompute()
        helix_name = helix.Name
        helix_label = getattr(helix, "Label", helix.Name)
        helix_type = getattr(helix, "TypeId", "")
        helix_native_mode = _property_value(helix, "Mode")
        effect = domain_runtime.finalize_partdesign_feature_effect(
            doc,
            body,
            helix,
            "helix" if requested_mode == "additive" else "subtractive_helix",
            body_shape_before,
        )
        return {
            "document": doc.Name,
            "body": body.Name,
            "profile": target_profile.Name,
            "feature": helix_name,
            "label": helix_label,
            "type": helix_type,
            "mode": requested_mode,
            "reference_axis": requested_axis,
            "native_mode": helix_native_mode,
            "pitch": float(getattr(helix, "Pitch", 0.0) or 0.0),
            "height": float(getattr(helix, "Height", 0.0) or 0.0),
            "turns": float(getattr(helix, "Turns", 0.0) or 0.0),
            "angle": float(getattr(helix, "Angle", 0.0) or 0.0),
            "growth": float(getattr(helix, "Growth", 0.0) or 0.0),
            "left_handed": bool(getattr(helix, "LeftHanded", False)),
            "reversed": bool(getattr(helix, "Reversed", False)),
            **effect,
        }

    transaction = run_freecad_transaction(
        f"Create PartDesign {requested_mode} helix from profile: {getattr(profile, 'Label', profile.Name)}",
        _helix,
    )
    transaction_result = transaction.get("result", {}) if isinstance(transaction.get("result"), dict) else {}
    feature_effect = transaction_result.get("feature_effect")
    effective = not isinstance(feature_effect, dict) or bool(feature_effect.get("ok"))
    ok = bool(transaction.get("ok")) and effective
    error = None
    likely_cause = None
    if not transaction.get("ok"):
        error = transaction.get("error") or "PartDesign Helix failed."
    elif not effective:
        error, likely_cause = domain_runtime.describe_ineffective_partdesign_feature(
            "helix" if requested_mode == "additive" else "subtractive_helix",
            feature_shape=transaction_result.get("feature_shape"),
            feature_effect=feature_effect,
            feature_state=transaction_result.get("feature_state"),
            report_errors=domain_runtime.recompute_errors(transaction),
            rolled_back=bool(transaction_result.get("rolled_back_feature")),
            lead_in="PartDesign Helix was created but did not produce an effective body shape change.",
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
