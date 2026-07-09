# SPDX-License-Identifier: LGPL-2.1-or-later

"""AI-native feature creation tool."""

from __future__ import annotations

from typing import Any

from .cad_common import append_design_memory, backend_ok, call_backend


FEATURE_OPERATIONS = (
    "add_prismatic",
    "cut_prismatic",
    "add_revolved",
    "cut_revolved",
    "add_loft",
    "cut_loft",
    "add_sweep",
    "cut_sweep",
    "pattern_feature",
    "finish_edges",
)


TOOL_SPEC = {
    "name": "cad.create_feature",
    "description": (
        "Create a native FreeCAD feature from named design intent: prismatic "
        "pad/pocket, revolve/groove, loft, sweep, pattern, or finishing "
        "dressup. The tool closes required sketches before feature creation "
        "and blocks instead of continuing when any sketch cannot close cleanly."
    ),
    "safety": "SAFE_WRITE",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": list(FEATURE_OPERATIONS)},
            "label": {"type": "string"},
            "purpose": {"type": "string"},
            "profile": {"type": "string"},
            "profiles": {"type": "array", "items": {"type": "string"}},
            "spine": {"type": "string"},
            "feature_name": {"type": "string"},
            "length": {"type": "number"},
            "midplane": {"type": "boolean"},
            "reversed": {"type": "boolean"},
            "axis": {"type": "string", "enum": ["X_Axis", "Y_Axis", "Z_Axis"]},
            "angle": {"type": "number"},
            "ruled": {"type": "boolean"},
            "closed": {"type": "boolean"},
            "pattern_operation": {
                "type": "string",
                "enum": ["linear", "polar", "mirror"],
            },
            "occurrences": {"type": "integer"},
            "direction": {"type": "string", "enum": ["X_Axis", "Y_Axis", "Z_Axis"]},
            "mirror_plane": {
                "type": "string",
                "enum": ["XY_Plane", "XZ_Plane", "YZ_Plane"],
            },
            "finish_operation": {
                "type": "string",
                "enum": ["fillet", "chamfer", "draft", "thickness"],
            },
            "radius": {"type": "number"},
            "size": {"type": "number"},
            "edge_names": {"type": "array", "items": {"type": "string"}},
            "face_names": {"type": "array", "items": {"type": "string"}},
            "all_edges": {"type": "boolean"},
            "neutral_plane_name": {"type": "string"},
            "pull_direction_name": {"type": "string"},
            "wall_thickness": {"type": "number"},
            "inward": {"type": "boolean"},
            "mode": {"type": "integer"},
            "join": {"type": "integer"},
        },
        "required": ["operation", "purpose"],
    },
}


def _backend_error(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    error = str(result.get("error") or "").strip()
    if error:
        return error
    transaction = result.get("transaction")
    if isinstance(transaction, dict):
        error = str(transaction.get("error") or "").strip()
        if error:
            return error
    return ""


def _close_required_profiles(
    service: Any,
    profiles: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    results: list[dict[str, Any]] = []
    for profile in profiles:
        clean_profile = str(profile or "").strip()
        if not clean_profile:
            continue
        result = call_backend(
            service,
            "sketcher.close_sketch",
            sketch_name=clean_profile,
        )
        results.append(
            {
                "profile": clean_profile,
                "result": result,
            }
        )
        if not backend_ok(result):
            error = _backend_error(result) or "Sketch could not be closed cleanly."
            return results, {
                "ok": False,
                "error": (
                    f"Feature creation blocked because required sketch "
                    f"{clean_profile!r} could not be closed: {error}"
                ),
                "retry_same_call": False,
                "failed_profile": clean_profile,
                "profile_close_results": results,
                "repair_actions": [
                    {
                        "tool": "cad.verify_design",
                        "why": (
                            "Inspect report errors, sketch profile closure, and "
                            "solver state before another feature write."
                        ),
                    },
                    {
                        "tool": "cad.create_profile",
                        "why": (
                            "Repair or recreate the failing profile with explicit "
                            "geometry that matches the intended feature."
                        ),
                    },
                ],
            }
    return results, None


def run(
    service: Any,
    operation: str,
    purpose: str,
    label: str = "",
    profile: str = "",
    profiles: list[str] | None = None,
    spine: str = "",
    feature_name: str = "",
    length: float | None = None,
    midplane: bool = False,
    reversed: bool = False,
    axis: str = "X_Axis",
    angle: float = 360.0,
    ruled: bool = False,
    closed: bool = False,
    pattern_operation: str = "",
    occurrences: int | None = None,
    direction: str = "X_Axis",
    mirror_plane: str = "YZ_Plane",
    finish_operation: str = "",
    radius: float | None = None,
    size: float | None = None,
    edge_names: list[str] | None = None,
    face_names: list[str] | None = None,
    all_edges: bool | None = None,
    neutral_plane_name: str = "",
    pull_direction_name: str = "",
    wall_thickness: float | None = None,
    inward: bool | None = None,
    mode: int | None = None,
    join: int | None = None,
) -> dict[str, Any]:
    op = str(operation or "").strip()
    clean_purpose = str(purpose or "").strip()
    if op not in FEATURE_OPERATIONS:
        return {"ok": False, "error": f"operation must be one of: {', '.join(FEATURE_OPERATIONS)}."}
    if not clean_purpose:
        return {"ok": False, "error": "purpose is required."}
    clean_label = str(label or "").strip() or f"VibeCAD {op.replace('_', ' ').title()}"

    profile_close_results: list[dict[str, Any]] = []
    backend_result: dict[str, Any]
    if op in {"add_prismatic", "cut_prismatic"}:
        if not str(profile or "").strip():
            return {"ok": False, "error": "profile is required for prismatic features."}
        profile_close_results, close_error = _close_required_profiles(
            service,
            [str(profile).strip()],
        )
        if close_error is not None:
            return {
                **close_error,
                "operation": op,
                "purpose": clean_purpose,
                "label": clean_label,
            }
        backend_result = call_backend(
            service,
            "partdesign.extrude",
            operation="pad" if op == "add_prismatic" else "pocket",
            sketch_name=str(profile).strip(),
            label=clean_label,
            length=length,
            midplane=bool(midplane),
            reversed=bool(reversed),
        )
    elif op in {"add_revolved", "cut_revolved"}:
        if not str(profile or "").strip():
            return {"ok": False, "error": "profile is required for revolved features."}
        profile_close_results, close_error = _close_required_profiles(
            service,
            [str(profile).strip()],
        )
        if close_error is not None:
            return {
                **close_error,
                "operation": op,
                "purpose": clean_purpose,
                "label": clean_label,
            }
        backend_result = call_backend(
            service,
            "partdesign.revolve",
            operation="revolve" if op == "add_revolved" else "groove",
            sketch_name=str(profile).strip(),
            label=clean_label,
            angle=float(angle),
            axis=str(axis or "X_Axis"),
            midplane=bool(midplane),
            reversed=bool(reversed),
        )
    elif op in {"add_loft", "cut_loft"}:
        ordered = [str(item).strip() for item in (profiles or []) if str(item).strip()]
        if len(ordered) < 2:
            return {"ok": False, "error": "profiles must contain at least two sketch names for loft."}
        profile_close_results, close_error = _close_required_profiles(service, ordered)
        if close_error is not None:
            return {
                **close_error,
                "operation": op,
                "purpose": clean_purpose,
                "label": clean_label,
            }
        backend_result = call_backend(
            service,
            "partdesign.loft_profiles",
            profile_sketch_name=ordered[0],
            section_sketch_names=ordered[1:],
            label=clean_label,
            mode="additive" if op == "add_loft" else "subtractive",
            closed=bool(closed),
            ruled=bool(ruled),
        )
    elif op in {"add_sweep", "cut_sweep"}:
        ordered_sections = [str(item).strip() for item in (profiles or []) if str(item).strip()]
        if not str(profile or "").strip() or not str(spine or "").strip():
            return {"ok": False, "error": "profile and spine are required for sweep."}
        profile_close_results, close_error = _close_required_profiles(
            service,
            [str(profile).strip(), str(spine).strip(), *ordered_sections],
        )
        if close_error is not None:
            return {
                **close_error,
                "operation": op,
                "purpose": clean_purpose,
                "label": clean_label,
            }
        backend_result = call_backend(
            service,
            "partdesign.sweep_profile",
            profile_sketch_name=str(profile).strip(),
            spine_sketch_name=str(spine).strip(),
            label=clean_label,
            mode="additive" if op == "add_sweep" else "subtractive",
            section_sketch_names=ordered_sections,
        )
    elif op == "pattern_feature":
        if not str(feature_name or "").strip():
            return {"ok": False, "error": "feature_name is required for pattern_feature."}
        backend_result = call_backend(
            service,
            "partdesign.pattern",
            operation=str(pattern_operation or "polar"),
            feature_name=str(feature_name).strip(),
            label=clean_label,
            direction=str(direction or "X_Axis"),
            axis=str(axis or "Z_Axis"),
            angle=float(angle),
            occurrences=occurrences,
            mirror_plane=str(mirror_plane or "YZ_Plane"),
        )
    else:
        if not str(feature_name or "").strip():
            return {"ok": False, "error": "feature_name is required for finish_edges."}
        finish = str(finish_operation or "").strip()
        if finish not in {"fillet", "chamfer", "draft", "thickness"}:
            return {"ok": False, "error": "finish_operation must be fillet, chamfer, draft, or thickness."}
        args: dict[str, Any] = {
            "operation": finish,
            "feature_name": str(feature_name).strip(),
            "label": clean_label,
            "edge_names": list(edge_names or []),
            "face_names": list(face_names or []),
        }
        if radius is not None:
            args["radius"] = float(radius)
        if size is not None:
            args["size"] = float(size)
        if all_edges is not None:
            args["all_edges"] = bool(all_edges)
        if str(neutral_plane_name or "").strip():
            args["neutral_plane_name"] = str(neutral_plane_name).strip()
        if str(pull_direction_name or "").strip():
            args["pull_direction_name"] = str(pull_direction_name).strip()
        if finish == "draft":
            args["angle"] = float(angle)
            args["reversed"] = bool(reversed)
        if wall_thickness is not None:
            args["wall_thickness"] = float(wall_thickness)
        if inward is not None:
            args["inward"] = bool(inward)
        if mode is not None:
            args["mode"] = int(mode)
        if join is not None:
            args["join"] = int(join)
        backend_result = call_backend(service, "partdesign.dressup", **args)

    memory = append_design_memory(
        service,
        sketches_features=[f"{clean_label}: {op}; purpose={clean_purpose}"],
    )
    ok = backend_ok(backend_result)
    repair_actions = []
    if not ok:
        if op in {
            "add_prismatic",
            "cut_prismatic",
            "add_revolved",
            "cut_revolved",
            "add_loft",
            "cut_loft",
            "add_sweep",
            "cut_sweep",
        }:
            repair_actions.append(
                {
                    "tool": "cad.verify_design",
                    "why": (
                        "Verify sketch profile closure, solver state, report errors, "
                        "and body shape before another feature write."
                    ),
                }
            )
            repair_actions.append(
                {
                    "tool": "cad.create_profile",
                    "why": (
                        "Repair by authoring a corrected named profile or section set "
                        "that matches the intended surface character."
                    ),
                }
            )
        elif op == "pattern_feature":
            repair_actions.append(
                {
                    "tool": "cad.verify_design",
                    "why": "Verify the source feature exists, is valid, and belongs to the intended Body.",
                }
            )
        else:
            repair_actions.append(
                {
                    "tool": "cad.verify_design",
                    "why": (
                        "Verify the source feature and selected edges/faces before "
                        "another finishing operation."
                    ),
                }
            )
    return {
        "ok": ok,
        "operation": op,
        "purpose": clean_purpose,
        "label": clean_label,
        "profile_close_results": profile_close_results,
        "backend_result": backend_result,
        "repair_actions": repair_actions,
        "design_memory_result": memory,
    }
