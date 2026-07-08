# SPDX-License-Identifier: LGPL-2.1-or-later

"""Model-visible context compaction for provider prompts."""

from __future__ import annotations

import json
from typing import Any

from .base import _compact_provider_result, provider_function_name

_DEFAULT_SECTIONS = {
    "conversation",
    "document",
    "selection",
    "view_screenshot",
    "reference_images",
    "design_memory",
    "design_preflight",
    "workbench",
    "task_panel",
    "errors",
}

_DOMAIN_CONTEXT_KEYS = {
    "part",
    "mesh",
    "points",
    "material",
    "sketcher",
    "spreadsheet",
    "draft",
    "partdesign",
    "techdraw",
    "fem",
    "cam",
    "bim",
    "assembly",
    "inspection",
    "openscad",
    "surface",
    "reverseengineering",
    "robot",
    "meshpart",
}

_DOMAIN_KEYS_BY_WORKBENCH = {
    "AssemblyWorkbench": {"assembly"},
    "BIMWorkbench": {"bim"},
    "CAMWorkbench": {"cam"},
    "DraftWorkbench": {"draft"},
    "FemWorkbench": {"fem"},
    "InspectionWorkbench": {"inspection"},
    "MaterialWorkbench": {"material"},
    "MeshPartWorkbench": {"meshpart"},
    "MeshWorkbench": {"mesh"},
    "OpenSCADWorkbench": {"openscad"},
    "PartDesignWorkbench": {"partdesign", "sketcher"},
    "PartWorkbench": {"part"},
    "PointsWorkbench": {"points"},
    "ReverseEngineeringWorkbench": {"reverseengineering"},
    "RobotWorkbench": {"robot"},
    "SketcherWorkbench": {"sketcher"},
    "SpreadsheetWorkbench": {"spreadsheet"},
    "SurfaceWorkbench": {"surface"},
    "TechDrawWorkbench": {"techdraw"},
}

_DOMAIN_DROP_KEYS = {
    "document",
    "requested",
    "available_tools",
    "available_tools_workbench",
    "provider_tool_schemas",
    "provider_tool_schemas_workbench",
    "provider_function_tools",
    "provider_tool_surface",
    "tool_shape_report",
    "conversation",
    "task_panel",
    "children",
}

_DOMAIN_LIST_LIMITS = {
    "assemblies": 6,
    "bodies": 6,
    "component_children": 12,
    "constraints": 16,
    "features": 12,
    "geometry": 16,
    "jobs": 6,
    "joint_children": 12,
    "objects": 12,
    "sketches": 8,
}

_DOMAIN_DEFAULT_LIST_LIMIT = 6
_DOMAIN_DEFAULT_DICT_LIMIT = 18
_DOMAIN_MAX_DEPTH = 4

_OMITTED_SUFFIX = "_omitted"


def _parse_arguments(arguments_json: str | dict[str, Any] | None = None) -> dict[str, Any]:
    if arguments_json is None:
        return {}
    if isinstance(arguments_json, dict):
        return arguments_json
    try:
        parsed = json.loads(str(arguments_json or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_list(value: Any, *, limit: int = 50) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    result: list[str] = []
    for item in values:
        clean = str(item or "").strip()
        if clean:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _int_arg(value: Any, default: int, *, minimum: int = 0, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "." * max(0, limit)
    return text[: max(0, limit - 3)].rstrip() + "..."


def _round_number(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        rounded = round(float(value), 6)
        return int(rounded) if rounded.is_integer() else rounded
    return value


def _round_sequence(values: Any) -> list[Any]:
    if not isinstance(values, (list, tuple)):
        return []
    return [_round_number(value) for value in values]


def _compact_type_id(value: Any) -> Any:
    text = str(value or "")
    if text == "Sketcher::SketchObject":
        return "Sketch"
    if "::" not in text:
        return value
    prefix, name = text.split("::", 1)
    if prefix in {"PartDesign", "Part", "Assembly", "Surface", "TechDraw"} and name:
        return name
    return value


def _compact_workbench_name(value: Any) -> Any:
    text = str(value or "")
    return text[: -len("Workbench")] if text.endswith("Workbench") else value


def _compact_object_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    label = item.get("label")
    result = {
        "name": name,
        "label": label if label != name else None,
        "type": _compact_type_id(item.get("type")),
    }
    shape = item.get("shape")
    if isinstance(shape, dict):
        result["shape"] = {
            key: _round_number(shape[key])
            for key in ("solids", "faces", "edges", "volume")
            if key in shape and shape[key] not in (None, "", [], {})
        }
    bbox = item.get("bound_box")
    if isinstance(bbox, dict):
        axes = ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax")
        if all(axis in bbox for axis in axes):
            result["bbox"] = [_round_number(bbox[axis]) for axis in axes]
        else:
            result["bbox"] = {
                key: _round_number(bbox[key])
                for key in sorted(bbox)
                if key.endswith("_length") or key in axes
            }
    placement = item.get("placement")
    if isinstance(placement, dict) and "bbox" not in result:
        base = placement.get("base")
        rotation = placement.get("rotation_euler")
        if base not in (None, [], [0.0, 0.0, 0.0], [0, 0, 0]):
            result["position"] = _round_sequence(base)
        if rotation not in (None, [], [0.0, 0.0, 0.0], [0, 0, 0]):
            result["rotation"] = _round_sequence(rotation)
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [], {})
    }


def _compact_document(document: Any, *, max_objects: int) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {"name": None, "object_count": 0, "objects": []}
    raw_objects = document.get("objects")
    objects = raw_objects if isinstance(raw_objects, list) else []
    compact_objects = [
        compact
        for compact in (_compact_object_summary(item) for item in objects[:max_objects])
        if compact is not None
    ]
    result = {
        "name": document.get("document"),
        "label": (
            document.get("label")
            if document.get("label") != document.get("document")
            else None
        ),
        "object_count": document.get("object_count", len(objects)),
        "objects": compact_objects,
    }
    truncated = bool(
        document.get("objects_truncated")
        or int(document.get("object_count", len(objects)) or 0) > len(compact_objects)
    )
    omitted = max(
        0, int(document.get("object_count", len(objects)) or 0) - len(compact_objects)
    )
    if truncated:
        result["omitted_count"] = omitted
    return {key: value for key, value in result.items() if value not in (None, "", [], False)}


def _match_objects(document: Any, object_names: list[str]) -> dict[str, Any]:
    objects = document.get("objects") if isinstance(document, dict) else []
    candidates = objects if isinstance(objects, list) else []
    results = []
    for query in object_names:
        exact = []
        folded = []
        contains = []
        query_folded = query.casefold()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            compact = _compact_object_summary(item)
            if compact is None:
                continue
            name = str(item.get("name") or "")
            label = str(item.get("label") or "")
            if query in {name, label}:
                exact.append(dict(compact, matched_by="exact"))
            elif query_folded in {name.casefold(), label.casefold()}:
                folded.append(dict(compact, matched_by="case_insensitive"))
            elif len(query_folded) >= 3 and (
                query_folded in name.casefold() or query_folded in label.casefold()
            ):
                contains.append(dict(compact, matched_by="contains"))
        matches = exact or folded or contains[:8]
        results.append(
            {
                "query": query,
                "ok": bool(matches),
                "match_count": len(matches),
                "matches": matches,
            }
        )
    return {
        "all": all(item["ok"] for item in results) if results else True,
        "results": results,
    }


def _compact_conversation(conversation: Any) -> dict[str, Any]:
    if not isinstance(conversation, dict):
        return {"turn_count": 0}
    turns = conversation.get("conversation")
    turn_count = len(turns) if isinstance(turns, list) else 0
    result: dict[str, Any] = {
        "turns": turn_count,
    }
    scope = conversation.get("scope")
    if isinstance(scope, dict):
        result["scope"] = {
            key: scope.get(key)
            for key in ("kind", "document", "file_path", "persistent")
            if scope.get(key) not in (None, "", [], {})
        }
    if isinstance(turns, list):
        items: list[dict[str, Any]] = []
        for item in turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if not role or not content:
                continue
            entry: dict[str, Any] = {
                "role": role,
                "content": content,
            }
            timestamp = str(item.get("timestamp") or "").strip()
            if timestamp:
                entry["timestamp"] = timestamp
            metadata = item.get("metadata")
            if isinstance(metadata, dict) and metadata:
                entry["metadata"] = {
                    str(key): value
                    for key, value in metadata.items()
                    if value not in (None, "", [], {})
                }
            items.append(entry)
        result["items"] = items
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _compact_requirement_memory(context: dict[str, Any]) -> dict[str, Any] | None:
    project = context.get("vibecad_project")
    if not isinstance(project, dict):
        return None
    memory = project.get("requirement_memory")
    if not isinstance(memory, list) or not memory:
        return None
    visible_memory = memory
    omitted = 0
    if len(memory) > 18:
        visible_memory = memory[:6] + memory[-12:]
        omitted = len(memory) - len(visible_memory)
    items: list[dict[str, Any]] = []
    for item in visible_memory:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        entry: dict[str, Any] = {
            "role": str(item.get("role") or "user"),
            "content": _compact_text(content, 180),
        }
        source = str(item.get("source") or "").strip()
        if source:
            entry["source"] = _compact_text(source, 32)
        items.append(entry)
    if not items:
        return None
    result: dict[str, Any] = {"items": items}
    if omitted:
        result["omitted"] = omitted
    return result


def _compact_design_memory(context: dict[str, Any]) -> dict[str, Any] | None:
    project = context.get("vibecad_project")
    memory = (
        project.get("design_memory")
        if isinstance(project, dict)
        else None
    )
    if not isinstance(memory, dict) or not memory:
        return None
    result: dict[str, Any] = {
        "status": memory.get("status"),
        "intent": _compact_text(memory.get("user_intent"), 180),
        "summary": _compact_text(memory.get("summary"), 180),
        "obligation": _compact_text(memory.get("current_obligation"), 180),
    }
    for key in (
        "accepted_assumptions",
        "components",
        "sketches_features",
        "interfaces",
        "envelopes",
        "mechanisms",
        "non_negotiable_product_behavior",
        "critical_geometry",
        "verification_checks",
        "construction_order",
        "forbidden_shortcuts",
        "known_failures",
        "corrections",
        "open_questions",
        "notes",
    ):
        values = memory.get(key)
        if isinstance(values, list):
            compact_values = [
                _compact_text(item, 120)
                for item in values[:8]
                if str(item or "").strip()
            ]
            if compact_values:
                result[key] = compact_values
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _preflight_answers(preflight: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(preflight, dict):
        return []
    merged: dict[str, dict[str, Any]] = {}

    def add_answers(raw_answers: Any) -> None:
        if not isinstance(raw_answers, list):
            return
        for item in raw_answers:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question and answer:
                merged[question] = dict(item)

    add_answers(preflight.get("user_answers"))
    rounds = preflight.get("user_answer_rounds")
    if isinstance(rounds, list):
        for answer_round in rounds:
            if isinstance(answer_round, dict):
                add_answers(answer_round.get("answers"))
    add_answers(preflight.get("last_user_answers"))
    return list(merged.values())


def _compact_screenshot(screenshot: Any) -> dict[str, Any] | None:
    if not isinstance(screenshot, dict) or not screenshot.get("captured"):
        return None
    result: dict[str, Any] = {"ok": True}
    observation = screenshot.get("visual_observation")
    if isinstance(observation, dict):
        compact_observation = {
            alias: observation.get(key)
            for key, alias in (
                ("available", "ok"),
                ("attention_flags", "flags"),
                ("inspection_summary", "note"),
            )
            if observation.get(key) not in (None, "", [], {})
        }
        if compact_observation:
            result["visual_observation"] = compact_observation
    return result


def _compact_task_panel(task_panel: Any) -> dict[str, Any] | None:
    if not isinstance(task_panel, dict):
        return None
    result: dict[str, Any] = {
        "active": bool(task_panel.get("active_dialog")),
        "edit": bool(task_panel.get("edit_mode")),
    }
    edit_object = task_panel.get("edit_object")
    if isinstance(edit_object, dict):
        obj: dict[str, Any] = {}
        for key, alias in (("name", "name"), ("label", "label"), ("type", "type")):
            value = edit_object.get(key)
            if value not in (None, "", [], {}):
                obj[alias] = _compact_text(value, 96)
        if obj:
            result["edit_object"] = obj
    active_sketch = str(task_panel.get("active_sketch") or "").strip()
    if active_sketch:
        result["active_sketch"] = _compact_text(active_sketch, 96)
    profile = task_panel.get("profile_status")
    if isinstance(profile, dict):
        result["profile"] = {
            key: value
            for key, value in {
                "ready": bool(
                    profile.get("ready_for_pad") or profile.get("ready_for_pocket")
                ),
                "closed": profile.get("closed_profile"),
                "degrees_of_freedom": profile.get("degrees_of_freedom"),
                "faces": profile.get("face_count"),
                "edges": profile.get("edge_count"),
                "reason": _compact_text(profile.get("reason"), 180),
            }.items()
            if value not in (None, "", [], {})
        }
    next_actions = task_panel.get("next_actions")
    if isinstance(next_actions, list) and next_actions:
        compact_actions: list[dict[str, Any]] = []
        for item in next_actions[:4]:
            if not isinstance(item, dict):
                continue
            action: dict[str, Any] = {}
            if item.get("tool"):
                action["tool"] = _compact_text(item.get("tool"), 80)
            if item.get("why"):
                action["why"] = _compact_text(item.get("why"), 120)
            if action:
                compact_actions.append(action)
        if compact_actions:
            result["next_actions"] = compact_actions
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _compact_references(references: Any) -> dict[str, Any] | None:
    if not isinstance(references, dict):
        return None
    images = references.get("images")
    if not isinstance(images, list) or not images:
        notes = references.get("provider_delivery_notes")
        if notes:
            return {"count": references.get("count", 0), "delivery_notes": notes}
        return None
    compact_images: list[dict[str, Any]] = []
    for index, entry in enumerate(images[:8], start=1):
        if not isinstance(entry, dict):
            continue
        item: dict[str, Any] = {
            "id": entry.get("id"),
            "file": _compact_text(entry.get("name") or f"reference {index}", 48),
        }
        if entry.get("label"):
            item["label"] = _compact_text(entry.get("label"), 48)
        brief = entry.get("visual_brief")
        if isinstance(brief, dict):
            summary = brief.get("summary")
            if summary:
                item["visual_brief"] = _compact_text(summary, 140)
            else:
                compact_brief = {
                    alias: brief.get(key)
                    for key, alias in (
                        ("object_type", "object_type"),
                        ("must_preserve", "keep"),
                        ("counts_patterns", "count"),
                        ("do_not_simplify", "no_simplify"),
                    )
                    if brief.get(key) not in (None, "", [], {})
                }
                if compact_brief:
                    item["visual_brief"] = compact_brief
        delivery = entry.get("provider_delivery")
        if isinstance(delivery, dict) and delivery.get("available") is False:
            item["missing_delivery_reason"] = _compact_text(
                delivery.get("reason") or "not delivered", 80
            )
        compact_images.append(
            {key: value for key, value in item.items() if value not in (None, "", [], {})}
        )
    if not compact_images:
        return None
    result: dict[str, Any] = {
        "count": references.get("count", len(images)),
        "images": compact_images,
    }
    if len(images) > len(compact_images):
        result["omitted_count"] = len(images) - len(compact_images)
    return result


def _compact_loop(loop: Any) -> dict[str, Any] | None:
    if not isinstance(loop, dict):
        return None
    result = {}
    for key, alias in (
        ("turn", "turn"),
        ("workspace_mode", "workspace_mode"),
        ("document_delta", "document_delta"),
        ("document_object_count", "document_object_count"),
        ("screenshot_captured", "screenshot_captured"),
        ("visual_attention_flags", "visual_attention_flags"),
    ):
        if key in loop:
            result[alias] = loop.get(key)
    trace = loop.get("recent_tool_trace")
    if isinstance(trace, list):
        result["trace"] = [
            _compact_trace_item(item) for item in trace[-4:] if isinstance(item, dict)
        ]
        omitted = max(0, len(trace) - 4)
        if omitted:
            result["trace_omitted_count"] = omitted
    return result


def _compact_trace_item(item: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(item.get("tool_name") or "")
    result = {
        "tool": provider_function_name(tool_name, tool_name),
        "ok": bool(item.get("ok")),
        "active_workbench": _compact_workbench_name(item.get("active_workbench")),
    }
    payload = item.get("result")
    if isinstance(payload, dict):
        summary = _compact_provider_result(tool_name, payload)
        if summary:
            result["result"] = summary
    return {key: value for key, value in result.items() if value not in (None, "")}


def _compact_tool_scope(scope: Any) -> dict[str, Any] | None:
    if not isinstance(scope, dict):
        return None
    visible_scope = {}
    for key, alias in (
        ("workbench", "workbench"),
        ("stage", "stage"),
        ("active_tool_count", "active_tool_count"),
        ("full_workbench_tool_count", "full_workbench_tool_count"),
        ("omitted_tool_count", "omitted_tool_count"),
    ):
        if key in scope:
            visible_scope[alias] = scope.get(key)
    return visible_scope


def _compact_workspace(workspace: Any) -> dict[str, Any] | None:
    if not isinstance(workspace, dict):
        return None
    result = {}
    for key, alias in (
        ("mode", "mode"),
        ("active_workbench", "active_workbench"),
        ("entered_workbench", "entered_workbench"),
    ):
        if key in workspace:
            result[alias] = workspace.get(key)
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _compact_tool_pack(tool_pack: Any) -> dict[str, Any] | None:
    if not isinstance(tool_pack, dict):
        return None
    pack = tool_pack.get("tool_pack")
    if not isinstance(pack, dict):
        return {
            "active_workbench": _compact_workbench_name(tool_pack.get("active_workbench")),
            "pack": None,
        }
    return {
        "active_workbench": _compact_workbench_name(tool_pack.get("active_workbench")),
        "pack": {
            "workbench": _compact_workbench_name(pack.get("workbench")),
            "domain": pack.get("domain"),
            "enabled": pack.get("enabled"),
            "tool_count": len(pack.get("tool_names") or []),
        },
    }


def _compact_design_preflight(context: dict[str, Any]) -> dict[str, Any] | None:
    project = context.get("vibecad_project")
    preflight = (
        project.get("design_preflight")
        if isinstance(project, dict)
        else None
    )
    if not isinstance(preflight, dict) or not preflight:
        return None
    plan = preflight.get("final_build_plan")
    result: dict[str, Any] = {
        "status": preflight.get("status"),
        "initial": _compact_text(preflight.get("initial_user_prompt"), 180),
        "intent": _compact_text(preflight.get("user_intent"), 160),
    }
    compact_memory = _compact_requirement_memory(context)
    if compact_memory is not None:
        result["requirements"] = compact_memory
    refinement = preflight.get("requirement_refinement")
    if isinstance(refinement, list):
        compact_assumptions: list[dict[str, str]] = []
        for item in refinement[:8]:
            if not isinstance(item, dict) or item.get("assumption") is not True:
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("model_answer") or "").strip()
            if question and answer:
                compact_assumptions.append(
                    {
                        "question": _compact_text(question, 96),
                        "answer": _compact_text(answer, 96),
                    }
                )
        if compact_assumptions:
            result["assumptions"] = compact_assumptions
    questions = preflight.get("user_questions")
    if isinstance(questions, list):
        compact_questions: list[dict[str, Any]] = []
        for item in questions[:6]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            entry: dict[str, Any] = {"question": _compact_text(question, 96)}
            default = str(item.get("default_answer") or "").strip()
            if default:
                entry["default"] = _compact_text(default, 80)
            options = item.get("options")
            if isinstance(options, list):
                compact_options: list[str] = []
                for option in options[:6]:
                    if isinstance(option, dict):
                        text = option.get("answer") or option.get("label")
                    else:
                        text = option
                    clean = str(text or "").strip()
                    if clean:
                        compact_options.append(_compact_text(clean, 48))
                if compact_options:
                    entry["options"] = compact_options
            compact_questions.append(entry)
        if compact_questions:
            result["questions"] = compact_questions
    answers = _preflight_answers(preflight)
    if isinstance(answers, list):
        compact_answers: list[dict[str, str]] = []
        for item in answers[:8]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question and answer:
                compact_answers.append(
                    {
                        "question": _compact_text(question, 96),
                        "answer": _compact_text(answer, 96),
                    }
                )
        if compact_answers:
            result["answers"] = compact_answers
    if isinstance(plan, dict):
        result["architecture"] = _compact_text(plan.get("architecture"), 180)
        for key in (
            "bodies",
            "sketches_features",
            "interfaces",
            "envelopes",
            "mechanisms",
            "manufacturing_assumptions",
            "critical_geometry",
            "construction_order",
            "verification_checks",
            "forbidden_shortcuts",
        ):
            values = plan.get(key)
            if isinstance(values, list):
                compact_values = [
                    _compact_text(item, 96)
                    for item in values[:5]
                    if str(item or "").strip()
                ]
                if compact_values:
                    result[key] = compact_values
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _domain_list_limit(key: str, depth: int) -> int:
    if key in _DOMAIN_LIST_LIMITS:
        return _DOMAIN_LIST_LIMITS[key]
    if depth >= 3:
        return 4
    return _DOMAIN_DEFAULT_LIST_LIMIT


def _domain_text_limit(key: str) -> int:
    if key in {"reason", "error", "status", "inspection_summary"}:
        return 120
    if key in {"name", "label", "type", "tool_name", "feature", "operation"}:
        return 64
    if key in {"path", "file_path"}:
        return 120
    return 96


def _compact_domain_keyed_value(key: str, value: Any) -> Any:
    if key in {"type", "type_id", "TypeId"}:
        return _compact_type_id(value)
    if key in {"workbench", "active_workbench", "entered_workbench"}:
        return _compact_workbench_name(value)
    return value


def _domain_key_alias(key: str) -> str:
    return key


def _compact_domain_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if value in (None, "", [], {}):
        return None
    value = _compact_domain_keyed_value(key, value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _round_number(value)
    if isinstance(value, str):
        return _compact_text(value, _domain_text_limit(key))
    if isinstance(value, (list, tuple)):
        if depth >= _DOMAIN_MAX_DEPTH:
            return None
        limit = _domain_list_limit(key, depth)
        compact_items = []
        for item in list(value)[:limit]:
            compact = _compact_domain_value(item, key=key, depth=depth + 1)
            if compact not in (None, "", [], {}):
                compact_items.append(compact)
        return compact_items or None
    if isinstance(value, dict):
        if depth >= _DOMAIN_MAX_DEPTH:
            return None
        result: dict[str, Any] = {}
        included = 0
        omitted_keys = 0
        for raw_key, raw_item in value.items():
            item_key = str(raw_key)
            if item_key in _DOMAIN_DROP_KEYS:
                continue
            compact = _compact_domain_value(raw_item, key=item_key, depth=depth + 1)
            if compact in (None, "", [], {}):
                continue
            if compact is False and item_key in {"construction"}:
                continue
            if included >= _DOMAIN_DEFAULT_DICT_LIMIT:
                omitted_keys += 1
                continue
            alias = _domain_key_alias(item_key)
            result[alias] = compact
            included += 1
            if isinstance(raw_item, (list, tuple)):
                omitted = max(0, len(raw_item) - len(compact))
                if omitted:
                    result[f"{alias}{_OMITTED_SUFFIX}"] = omitted
        if omitted_keys:
            result["_omitted_keys"] = omitted_keys
        return result or None
    if isinstance(value, tuple):
        return list(value)
    return _compact_text(value, _domain_text_limit(key))


def _has_signal(value: Any) -> bool:
    if value in (None, "", [], {}, False):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, dict):
        return any(_has_signal(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_signal(item) for item in value)
    return True


def _domain_has_signal(value: Any) -> bool:
    if not isinstance(value, dict):
        return _has_signal(value)
    ignored_keys = {"document", "requested", "selected"}
    return any(
        _has_signal(item)
        for key, item in value.items()
        if key not in ignored_keys
    )


def _domain_keys_for_context(context: dict[str, Any]) -> set[str]:
    workbench = str(context.get("workbench") or "")
    return set(_DOMAIN_KEYS_BY_WORKBENCH.get(workbench, _DOMAIN_CONTEXT_KEYS))


def _visible_key(key: str) -> str:
    return key


def _compact_errors(errors: Any) -> dict[str, Any] | None:
    if not isinstance(errors, dict):
        return None
    items = errors.get("errors")
    if not items:
        return None
    compact_items = [
        _compact_text(item, 160)
        for item in (items if isinstance(items, list) else [items])[:6]
    ]
    return {"count": len(items) if isinstance(items, list) else 1, "items": compact_items}


def _compact_selection(selection: Any) -> dict[str, Any] | None:
    if not isinstance(selection, dict):
        return None
    selected = selection.get("selection")
    if not isinstance(selected, list) or not selected:
        return None
    items = [
        compact
        for compact in (_compact_object_summary(item) for item in selected[:8])
        if compact is not None
    ]
    if not items:
        return None
    result: dict[str, Any] = {"count": len(selected), "objects": items}
    if len(selected) > len(items):
        result["omitted_count"] = len(selected) - len(items)
    return result


def _selected_sections(arguments: dict[str, Any]) -> set[str]:
    sections = set(_string_list(arguments.get("sections"), limit=20))
    if not sections:
        return set(_DEFAULT_SECTIONS)
    return sections


def _model_visible_context(
    context: dict[str, Any],
    arguments: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    args = _parse_arguments(arguments)
    sections = _selected_sections(args)
    max_objects = _int_arg(
        args.get("max_objects"), 8, minimum=0, maximum=30
    )
    object_names = _string_list(args.get("object_names"), limit=50)

    visible: dict[str, Any] = {}
    if context.get("workbench"):
        visible[_visible_key("workbench")] = _compact_workbench_name(context.get("workbench"))
    if "document" in sections or object_names:
        compact_document = _compact_document(context.get("document"), max_objects=max_objects)
        if compact_document:
            visible[_visible_key("document")] = compact_document
        if object_names:
            visible[_visible_key("object_query")] = _match_objects(context.get("document"), object_names)
    if "selection" in sections:
        selection = _compact_selection(context.get("selection"))
        if selection is not None:
            visible[_visible_key("selection")] = selection
    if "view" in sections:
        visible["view"] = context.get("view")
    if "task_panel" in sections:
        task_panel = _compact_task_panel(context.get("task_panel"))
        if task_panel is not None:
            visible[_visible_key("task_panel")] = task_panel
    if "view_screenshot" in sections or "screenshot" in sections:
        screenshot = _compact_screenshot(context.get("view_screenshot"))
        if screenshot is not None:
            visible[_visible_key("view_screenshot")] = screenshot
    if "reference_images" in sections:
        references = _compact_references(context.get("reference_images"))
        if references is not None:
            visible[_visible_key("reference_images")] = references
    if "design_memory" in sections:
        memory = _compact_design_memory(context)
        if memory is not None:
            visible[_visible_key("design_memory")] = memory
    if "design_preflight" in sections:
        preflight = _compact_design_preflight(context)
        if preflight is not None:
            visible[_visible_key("design_preflight")] = preflight
    if "workspace" in sections:
        if "vibecad_workspace" in context:
            visible[_visible_key("vibecad_workspace")] = _compact_workspace(
                context.get("vibecad_workspace")
            )
    if "loop" in sections:
        loop = _compact_loop(context.get("vibecad_loop"))
        if loop is not None:
            visible[_visible_key("vibecad_loop")] = loop
    if "errors" in sections:
        errors = _compact_errors(context.get("report_view_errors"))
        if errors is not None:
            visible[_visible_key("report_view_errors")] = errors
    if "conversation" in sections:
        conversation = _compact_conversation(context.get("conversation"))
        requirement_memory = _compact_requirement_memory(context)
        if requirement_memory is not None:
            conversation["requirements"] = requirement_memory
        visible[_visible_key("conversation")] = conversation
    if "domain" in sections:
        for key in sorted(_domain_keys_for_context(context)):
            value = context.get(key)
            if _domain_has_signal(value):
                compact = _compact_domain_value(value, key=key)
                if _has_signal(compact):
                    visible[_visible_key(key)] = compact
    return visible
