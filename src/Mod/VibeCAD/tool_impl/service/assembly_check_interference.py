# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.check_interference``.

Pairwise clearance/interference analysis between shaped document objects:
boolean-common overlap volume for interference detection plus minimum
distance for clearance verification (for example rotor-to-housing gaps).
"""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "contextual": True,
    "description": (
        "Check pairwise interference and clearance between shaped objects or "
        "assembly components. Reports overlap volume, minimum distance, and "
        "clear/contact/interference status; use clearance_threshold to flag "
        "tight gaps."
    ),
    "name": "assembly.check_interference",
    "parameters": {
        "properties": {
            "object_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names or labels of two or more shaped document objects "
                    "to check pairwise. Takes precedence over assembly_name."
                ),
            },
            "assembly_name": {
                "type": "string",
                "description": (
                    "Native Assembly whose shaped components are checked "
                    "pairwise when object_names is omitted. Defaults to the "
                    "first assembly in the document."
                ),
            },
            "clearance_threshold": {
                "type": "number",
                "description": (
                    "Required clearance in mm. Pairs that are clear but "
                    "closer than this are flagged below_clearance "
                    "(default 0 = no flagging)."
                ),
            },
        },
        "required": [],
        "type": "object",
    },
    "safety": "READ",
}


_OVERLAP_VOLUME_TOLERANCE = 1e-9
_CONTACT_DISTANCE_TOLERANCE = 1e-6


def _global_shape(obj: Any) -> Any | None:
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    positioned = shape.copy()
    get_global_placement = getattr(obj, "getGlobalPlacement", None)
    if callable(get_global_placement):
        try:
            positioned.Placement = get_global_placement()
        except Exception:
            pass
    return positioned


def _pair_report(
    name_a: str,
    label_a: str,
    shape_a: Any,
    name_b: str,
    label_b: str,
    shape_b: Any,
    clearance_threshold: float,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "object_a": name_a,
        "label_a": label_a,
        "object_b": name_b,
        "label_b": label_b,
    }
    try:
        overlap_volume = float(shape_a.common(shape_b).Volume)
    except Exception as exc:
        entry["status"] = "error"
        entry["error"] = f"Boolean common failed: {exc}"
        return entry
    if overlap_volume > _OVERLAP_VOLUME_TOLERANCE:
        entry["status"] = "interference"
        entry["overlap_volume"] = round(overlap_volume, 6)
        entry["min_distance"] = 0.0
        entry["below_clearance"] = clearance_threshold > 0.0
        return entry
    try:
        distance = float(shape_a.distToShape(shape_b)[0])
    except Exception as exc:
        entry["status"] = "error"
        entry["error"] = f"Distance computation failed: {exc}"
        return entry
    entry["overlap_volume"] = 0.0
    entry["min_distance"] = round(distance, 6)
    if distance <= _CONTACT_DISTANCE_TOLERANCE:
        entry["status"] = "contact"
        entry["below_clearance"] = clearance_threshold > 0.0
    else:
        entry["status"] = "clear"
        entry["below_clearance"] = (
            clearance_threshold > 0.0 and distance < float(clearance_threshold)
        )
    return entry


def run(
    service: Any,
    object_names: list[str] | None = None,
    assembly_name: str | None = None,
    clearance_threshold: float = 0.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    threshold = max(float(clearance_threshold or 0.0), 0.0)
    requested = [str(name).strip() for name in (object_names or []) if str(name).strip()]

    candidates: list[Any] = []
    if requested:
        missing: list[str] = []
        for name in requested:
            obj = service._get_document_object(name)
            if obj is None:
                missing.append(name)
            elif obj not in candidates:
                candidates.append(obj)
        if missing:
            return {
                "ok": False,
                "error": f"Objects not found: {', '.join(missing)}",
                "requested_objects": requested,
            }
    else:
        assembly = service._get_assembly(assembly_name)
        if assembly is None:
            return {
                "ok": False,
                "error": (
                    "No objects to check: pass object_names, or pass "
                    "assembly_name for a document with a native Assembly."
                ),
                "requested_assembly": assembly_name,
            }
        for obj in list(getattr(assembly, "Group", []) or []):
            if obj not in candidates:
                candidates.append(obj)

    checked: list[dict[str, Any]] = []
    shapes: list[Any] = []
    skipped: list[dict[str, str]] = []
    for obj in candidates:
        shape = _global_shape(obj)
        label = str(getattr(obj, "Label", obj.Name))
        if shape is None:
            skipped.append({"object": obj.Name, "label": label, "reason": "no shape geometry"})
            continue
        checked.append({"object": obj.Name, "label": label})
        shapes.append(shape)

    if len(checked) < 2:
        return {
            "ok": False,
            "error": (
                "Interference checking needs at least two shaped objects; "
                f"got {len(checked)}."
            ),
            "objects": checked,
            "skipped": skipped,
        }

    pairs: list[dict[str, Any]] = []
    for index_a in range(len(checked)):
        for index_b in range(index_a + 1, len(checked)):
            pairs.append(
                _pair_report(
                    checked[index_a]["object"],
                    checked[index_a]["label"],
                    shapes[index_a],
                    checked[index_b]["object"],
                    checked[index_b]["label"],
                    shapes[index_b],
                    threshold,
                )
            )

    status_counts = {"interference": 0, "contact": 0, "clear": 0, "error": 0}
    for pair in pairs:
        status_counts[pair["status"]] = status_counts.get(pair["status"], 0) + 1
    return {
        "ok": True,
        "objects": checked,
        "skipped": skipped,
        "clearance_threshold": threshold,
        "pair_count": len(pairs),
        "interference_count": status_counts["interference"],
        "contact_count": status_counts["contact"],
        "clear_count": status_counts["clear"],
        "error_count": status_counts["error"],
        "below_clearance_count": sum(
            1 for pair in pairs if pair.get("below_clearance")
        ),
        "pairs": pairs,
    }
