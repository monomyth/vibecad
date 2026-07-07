# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider function tool for ``core.get_current_freecad_context``."""

from __future__ import annotations

import json
from typing import Any

from .base import tool_json_schema


TOOL_NAME = "core.get_current_freecad_context"
FUNCTION_NAME = "core_get_current_freecad_context"

_DEFAULT_SECTIONS = {
    "document",
    "selection",
    "view",
    "task_panel",
    "view_screenshot",
    "reference_images",
    "workbench",
    "workspace",
    "loop",
    "domain",
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


def _compact_object_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    result = {
        "name": item.get("name"),
        "label": item.get("label"),
        "type": item.get("type"),
    }
    for key in ("shape", "bound_box", "placement"):
        if key in item:
            result[key] = item[key]
    return {key: value for key, value in result.items() if value not in (None, "")}


def _compact_document(document: Any, *, max_objects: int) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {"document": None, "object_count": 0, "objects": []}
    raw_objects = document.get("objects")
    objects = raw_objects if isinstance(raw_objects, list) else []
    compact_objects = [
        compact
        for compact in (_compact_object_summary(item) for item in objects[:max_objects])
        if compact is not None
    ]
    return {
        "document": document.get("document"),
        "label": document.get("label"),
        "object_count": document.get("object_count", len(objects)),
        "object_limit": max_objects,
        "objects_truncated": bool(
            document.get("objects_truncated")
            or int(document.get("object_count", len(objects)) or 0) > len(compact_objects)
        ),
        "objects_omitted": max(
            0, int(document.get("object_count", len(objects)) or 0) - len(compact_objects)
        ),
        "objects": compact_objects,
    }


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
                "exists": bool(matches),
                "match_count": len(matches),
                "matches": matches,
            }
        )
    return {
        "all_found": all(item["exists"] for item in results) if results else True,
        "queries": results,
    }


def _compact_conversation(conversation: Any) -> dict[str, Any]:
    if not isinstance(conversation, dict):
        return {"turn_count": 0}
    turns = conversation.get("conversation")
    turn_count = len(turns) if isinstance(turns, list) else 0
    return {
        "turn_count": turn_count,
        "path": conversation.get("path"),
        "content_omitted": True,
    }


def _compact_loop(loop: Any) -> dict[str, Any] | None:
    if not isinstance(loop, dict):
        return None
    result = dict(loop)
    trace = result.get("recent_tool_trace")
    if isinstance(trace, list):
        result["recent_tool_trace"] = trace[-4:]
        result["recent_tool_trace_omitted"] = max(0, len(trace) - 4)
    contract = result.get("execution_contract")
    if isinstance(contract, dict):
        visible_contract = dict(contract)
        tool_names = visible_contract.pop("available_tools_this_turn", None)
        if isinstance(tool_names, list):
            visible_contract["available_tool_count"] = len(tool_names)
        result["execution_contract"] = visible_contract
    return result


def _compact_tool_scope(scope: Any) -> dict[str, Any] | None:
    if not isinstance(scope, dict):
        return None
    visible_scope = dict(scope)
    active_names = visible_scope.pop("active_tool_names", None)
    if isinstance(active_names, list):
        visible_scope["active_tool_name_count"] = len(active_names)
    return visible_scope


def _compact_tool_pack(tool_pack: Any) -> dict[str, Any] | None:
    if not isinstance(tool_pack, dict):
        return None
    pack = tool_pack.get("tool_pack")
    if not isinstance(pack, dict):
        return {
            "active_workbench": tool_pack.get("active_workbench"),
            "tool_pack": None,
        }
    return {
        "active_workbench": tool_pack.get("active_workbench"),
        "tool_pack": {
            "workbench": pack.get("workbench"),
            "domain": pack.get("domain"),
            "enabled": pack.get("enabled"),
            "tool_count": len(pack.get("tool_names") or []),
        },
    }


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
    max_objects = _int_arg(args.get("max_objects"), 30, minimum=0, maximum=100)
    object_names = _string_list(
        args.get("object_names") or args.get("objects") or args.get("names"),
        limit=50,
    )

    visible: dict[str, Any] = {
        "context_kind": "compact_current_freecad_context",
        "workbench": context.get("workbench"),
    }
    if "document" in sections or object_names:
        compact_document = _compact_document(context.get("document"), max_objects=max_objects)
        visible["document"] = compact_document
        if object_names:
            visible["object_query"] = _match_objects(context.get("document"), object_names)
    if "selection" in sections:
        visible["selection"] = context.get("selection")
    if "view" in sections:
        visible["view"] = context.get("view")
    if "task_panel" in sections:
        visible["task_panel"] = context.get("task_panel")
    if "view_screenshot" in sections or "screenshot" in sections:
        visible["view_screenshot"] = context.get("view_screenshot")
    if "reference_images" in sections:
        visible["reference_images"] = context.get("reference_images")
    if "workspace" in sections:
        if "vibecad_workspace" in context:
            visible["vibecad_workspace"] = context.get("vibecad_workspace")
        tool_pack = _compact_tool_pack(context.get("workbench_tool_pack"))
        if tool_pack is not None:
            visible["workbench_tool_pack"] = tool_pack
        scope = _compact_tool_scope(context.get("provider_tool_scope"))
        if scope is not None:
            visible["provider_tool_scope"] = scope
    if "loop" in sections:
        loop = _compact_loop(context.get("vibecad_loop"))
        if loop is not None:
            visible["vibecad_loop"] = loop
    if "errors" in sections:
        visible["report_view_errors"] = context.get("report_view_errors")
    if "conversation" in sections:
        visible["conversation"] = _compact_conversation(context.get("conversation"))
    if "domain" in sections:
        for key in sorted(_DOMAIN_CONTEXT_KEYS):
            if key in context:
                visible[key] = context[key]
    return visible


def create(schema: dict[str, Any], context: dict[str, Any], FunctionTool: Any) -> Any:
    async def _invoke(_tool_context, arguments_json: str):
        return _model_visible_context(context, arguments_json)

    description = (
        "Return a compact VibeCAD-visible FreeCAD context for this provider "
        "turn. The default response is intentionally lean: compact document "
        "objects, active workbench/workspace state, screenshot/task state, loop "
        "state, active domain summaries, and report errors. It omits full "
        "conversation text, provider tool schemas, and verbose tool-pack "
        "listings. Pass object_names to quickly verify whether named objects or "
        "labels exist. This is a read-only context inspection tool, not a "
        "generic CAD operation router.\n\n"
        f"Native VibeCAD tool: {TOOL_NAME}. Workbench: global. Safety: read. "
        "Use this exact function directly."
    )
    return FunctionTool(
        name=FUNCTION_NAME,
        description=description,
        params_json_schema=tool_json_schema(schema),
        on_invoke_tool=_invoke,
        strict_json_schema=False,
    )
