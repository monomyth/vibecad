# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``partdesign.loft_profiles``."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {'contextual': True,
 'description': 'Create a PartDesign AdditiveLoft/SubtractiveLoft through ordered '
                'section sketches. Use for blades, ducts, ergonomic forms, twist, '
                'camber, and changing sections.',
 'name': 'partdesign.loft_profiles',
 'parameters': {'properties': {'closed': {'description': 'Close the loft back to the first profile, forming a ring (default false).',
                                          'type': 'boolean'},
                               'label': {'type': 'string'},
                               'mode': {'description': 'additive adds material; subtractive removes it.',
                                        'enum': ['additive', 'subtractive'],
                                        'type': 'string'},
                               'profile_sketch_name': {'description': 'Starting cross-section sketch (name or label).',
                                                       'type': 'string'},
                               'ruled': {'description': 'Use straight (ruled) transitions between sections instead of smooth blending (default false).',
                                         'type': 'boolean'},
                               'section_sketch_names': {'description': 'Remaining cross-section sketches in loft order, each on a different plane.',
                                                        'items': {'type': 'string'},
                                                        'type': 'array'}},
                'required': ['profile_sketch_name', 'section_sketch_names'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'PartDesignWorkbench'}


def run(
    service,
    profile_sketch_name: str,
    section_sketch_names: list[str],
    label: str = "VibeCAD Loft",
    mode: str = "additive",
    closed: bool = False,
    ruled: bool = False,
) -> dict[str, Any]:
    profile = service._get_sketch(profile_sketch_name)
    if profile is None:
        return {"ok": False, "error": "Profile sketch not found.", "requested": profile_sketch_name}
    if not section_sketch_names:
        return {"ok": False, "error": "At least one section sketch is required."}
    sections = []
    missing = []
    for name in section_sketch_names:
        section = service._get_sketch(name)
        if section is None:
            missing.append(name)
        else:
            sections.append(section)
    if missing:
        return {"ok": False, "error": "Section sketch not found.", "missing": missing}
    requested_mode = str(mode or "additive").lower()
    if requested_mode not in {"additive", "subtractive"}:
        return {"ok": False, "error": "mode must be additive or subtractive."}

    def _loft() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target_profile = service._get_sketch(profile.Name)
        if target_profile is None:
            raise RuntimeError(f"Profile sketch not found: {profile.Name}")
        target_sections = []
        for section in sections:
            target_section = service._get_sketch(section.Name)
            if target_section is None:
                raise RuntimeError(f"Section sketch not found: {section.Name}")
            target_sections.append(target_section)
        body = service._partdesign_body_for_feature(target_profile)
        if body is None:
            raise RuntimeError("No PartDesign Body found for loft.")
        for section in target_sections:
            if section not in list(getattr(body, "Group", []) or []):
                body.addObject(section)
        type_name = "PartDesign::AdditiveLoft" if requested_mode == "additive" else "PartDesign::SubtractiveLoft"
        object_name = "VibeCAD_AdditiveLoft" if requested_mode == "additive" else "VibeCAD_SubtractiveLoft"
        loft = body.newObject(type_name, object_name)
        loft.Label = label or "VibeCAD Loft"
        loft.Profile = target_profile
        loft.Sections = target_sections
        if hasattr(loft, "Closed"):
            loft.Closed = bool(closed)
        if hasattr(loft, "Ruled"):
            loft.Ruled = bool(ruled)
        body.Tip = loft
        doc.recompute()
        return {
            "document": doc.Name,
            "body": body.Name,
            "profile": target_profile.Name,
            "sections": [section.Name for section in target_sections],
            "feature": loft.Name,
            "label": getattr(loft, "Label", loft.Name),
            "type": getattr(loft, "TypeId", ""),
            "mode": requested_mode,
            "closed": bool(getattr(loft, "Closed", False)),
            "ruled": bool(getattr(loft, "Ruled", False)),
            "face_count": len(getattr(getattr(loft, "Shape", None), "Faces", []) or []),
            "volume": float(getattr(getattr(loft, "Shape", None), "Volume", 0.0) or 0.0),
        }

    transaction = run_freecad_transaction(
        f"Create PartDesign loft from sketch: {getattr(profile, 'Label', profile.Name)}",
        _loft,
    )
    return {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "partdesign": domain_runtime.partdesign_summary(service),
    }
