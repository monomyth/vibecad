# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared factory for explicit VibeCAD provider function tools."""

from __future__ import annotations

import json
from typing import Any

PROVIDER_TOOL_DESCRIPTIONS: dict[str, str] = {
    "cad.create_feature": "feature",
    "cad.create_profile": "profile",
    "cad.define_component": "component",
    "cad.define_envelope": "envelope",
    "cad.define_interface": "interface",
    "cad.define_mechanism": "mechanism",
    "cad.inspect_state": "state",
    "cad.verify_design": "verify",
    "assembly.add_component": "comp",
    "assembly.check_interference": "clash",
    "assembly.create_assembly": "assy",
    "assembly.create_joint": "mate",
    "assembly.get_assemblies": "state",
    "assembly.ground_component": "ground",
    "assembly.set_component_placement": "place",
    "assembly.solve": "solve",
    "cam.add_tool": "cutter",
    "cam.create_job": "job",
    "cam.create_operation": "path",
    "cam.define_machine": "machine",
    "cam.postprocess": "gcode",
    "cam.validate_job": "verify",
    "core.capture_view_screenshot": "shot",
    "core.get_report_view_errors": "errors",
    "core.list_workbench_objects": "objects",
    "core.set_view": "camera",
    "core.submit_design_preflight": "preflight",
    "core.update_design_memory": "memory",
    "draft.create_array": "array",
    "draft.create_wire": "wire",
    "material.apply_appearance": "color",
    "model.build_from_script": "script",
    "part.cut_cylindrical_hole": "hole",
    "part.dressup": "finish",
    "part.set_placement": "place",
    "part.thicken_surface": "thicken",
    "partdesign.boolean_bodies": "boolean",
    "partdesign.create_body": "body",
    "partdesign.create_datum_line": "axis",
    "partdesign.create_datum_plane": "plane",
    "partdesign.create_sketch": "sketch",
    "partdesign.dressup": "finish",
    "partdesign.extrude": "extrude",
    "partdesign.find_subelements": "pick",
    "partdesign.get_bodies": "bodies",
    "partdesign.helix_profile": "helix",
    "partdesign.hole_from_sketch": "hole",
    "partdesign.loft_profiles": "loft",
    "partdesign.pattern": "pattern",
    "partdesign.revolve": "revolve",
    "partdesign.set_feature_dimensions": "dims",
    "partdesign.sweep_profile": "sweep",
    "sketcher.add_constraint": "constr",
    "sketcher.add_external_geometry": "ref",
    "sketcher.add_geometry": "draw",
    "sketcher.add_hole_pattern": "holes",
    "sketcher.add_slot": "slot",
    "sketcher.close_sketch": "close",
    "sketcher.create_sketch": "sketch",
    "sketcher.delete_items": "delete",
    "sketcher.draw_rectangle": "rect",
    "sketcher.edit_constraint": "edit",
    "sketcher.inspect_sketch": "inspect",
    "sketcher.modify_geometry": "modify",
    "sketcher.move_point": "move",
    "sketcher.open_sketch": "open",
    "sketcher.remove_external_geometry": "unref",
    "sketcher.resolve_geometry": "resolve",
    "sketcher.set_construction": "const",
    "sketcher.set_geometry_name": "name",
    "sketcher.transform_geometry": "xfm",
    "spreadsheet.get_sheet": "sheet",
    "surface.create_surface": "surface",
    "techdraw.add_view": "view",
    "techdraw.create_page": "page",
    "techdraw.get_pages": "pages",
}


def _provider_safe_function_name(tool_name: str) -> str:
    text = str(tool_name or "").strip()
    safe = "".join(ch if ch.isalnum() else "_" for ch in text)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "vibecad_tool"


PROVIDER_FUNCTION_NAMES: dict[str, str] = {
    tool_name: _provider_safe_function_name(tool_name)
    for tool_name in PROVIDER_TOOL_DESCRIPTIONS
}


def tool_description(schema: dict[str, Any]) -> str:
    tool_name = str(schema.get("name", ""))
    if tool_name in PROVIDER_TOOL_DESCRIPTIONS:
        return PROVIDER_TOOL_DESCRIPTIONS[tool_name]
    return tool_name


def provider_function_name(tool_name: str, default_name: str) -> str:
    text = str(tool_name or default_name or "")
    return PROVIDER_FUNCTION_NAMES.get(text, _provider_safe_function_name(text))


_PROVIDER_SCHEMA_FIELDS: dict[str, set[str]] = {
    "partdesign.dressup": {
        "operation",
        "feature_name",
        "label",
        "radius",
        "size",
        "all_edges",
        "edge_names",
        "face_names",
        "angle",
        "reverse",
        "thickness_value",
    },
    "partdesign.find_subelements": {
        "object_name",
        "element_type",
        "geometry_type",
        "normal",
        "radius",
        "min_area",
        "max_area",
        "min_length",
        "max_length",
        "near_point",
        "max_results",
    },
    "partdesign.hole_from_sketch": {
        "sketch_name",
        "label",
        "diameter",
        "depth",
        "depth_type",
        "hole_cut_type",
        "hole_cut_diameter",
        "hole_cut_depth",
        "countersink_angle",
        "thread_type",
    },
    "sketcher.add_constraint": {
        "sketch_name",
        "constraint_type",
        "first_geometry",
        "first_point",
        "second_geometry",
        "second_point",
        "third_geometry",
        "third_point",
        "value",
        "x",
        "y",
    },
    "sketcher.add_hole_pattern": {
        "sketch_name",
        "pattern",
        "hole_diameter",
        "center_x",
        "center_y",
        "count_x",
        "count_y",
        "spacing_x",
        "spacing_y",
        "count",
        "linear_angle_degrees",
        "bolt_circle_diameter",
        "start_angle_degrees",
    },
    "sketcher.add_slot": {
        "sketch_name",
        "center_x",
        "center_y",
        "overall_length",
        "center_distance",
        "width",
        "angle_degrees",
    },
    "sketcher.edit_constraint": {
        "action",
        "sketch_name",
        "constraint_index",
        "constraint_name",
        "value",
        "new_name",
        "driving",
        "expression",
    },
    "sketcher.move_point": {
        "sketch_name",
        "geometry_index",
        "point",
        "relative",
        "x",
        "y",
    },
    "sketcher.set_construction": {
        "sketch_name",
        "geometry_index",
        "construction",
    },
    "sketcher.set_geometry_name": {
        "sketch_name",
        "geometry_index",
        "geometry_name",
    },
    "sketcher.modify_geometry": {
        "operation",
        "sketch_name",
        "geometry_index",
        "x",
        "y",
        "endpoint",
        "increment",
        "first_geometry",
        "first_point",
        "second_geometry",
        "first_reference_x",
        "first_reference_y",
        "second_reference_x",
        "second_reference_y",
        "radius",
        "chamfer",
    },
    "sketcher.transform_geometry": {
        "operation",
        "sketch_name",
        "geometry_indices",
        "dx",
        "dy",
        "axis_point_x",
        "axis_point_y",
        "axis_direction_x",
        "axis_direction_y",
        "keep_original",
        "distance",
        "side",
        "columns",
        "rows",
        "column_dx",
        "column_dy",
        "row_dx",
        "row_dy",
    },
}


_KEEP_OBJECT_SHAPE_KEYS = {
    "normal",
    "near_point",
}

_KEEP_ARRAY_SHAPE_KEYS = {
    "center",
    "normal",
    "points",
    "near_point",
}

_DROP_PROVIDER_ENUM_KEYS: set[str] = set()


def _schema_for_provider_tool(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    keep = _PROVIDER_SCHEMA_FIELDS.get(tool_name)
    if not keep:
        return dict(parameters)
    result = dict(parameters)
    properties = result.get("properties")
    if isinstance(properties, dict):
        result["properties"] = {
            key: value for key, value in properties.items() if str(key) in keep
        }
    required = result.get("required")
    if isinstance(required, list):
        result["required"] = [item for item in required if str(item) in keep]
    return result

def _provider_argument_names(schema: dict[str, Any]) -> list[str]:
    tool_name = str(schema.get("name", ""))
    parameters = schema.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    exposed_parameters = _schema_for_provider_tool(tool_name, parameters)
    properties = exposed_parameters.get("properties")
    if not isinstance(properties, dict):
        return []
    return sorted(str(key) for key in properties)


def _argument_error(tool_name: str, message: str, *, allowed: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "error": message,
        "retry_same_call": False,
        "allowed_arguments": allowed,
    }
    return result


def _validate_backend_arguments(
    tool_name: str,
    schema: dict[str, Any],
    arguments_json: str,
) -> tuple[str | None, dict[str, Any] | None]:
    allowed = _provider_argument_names(schema)
    allowed_set = set(allowed)
    try:
        args = json.loads(arguments_json or "{}")
    except (TypeError, ValueError) as exc:
        return None, _argument_error(
            tool_name,
            f"Invalid JSON arguments for {tool_name}: {exc}.",
            allowed=allowed,
        )
    if not isinstance(args, dict):
        return None, _argument_error(
            tool_name,
            f"Arguments for {tool_name} must be a JSON object.",
            allowed=allowed,
        )
    unsupported = sorted(str(key) for key in args if str(key) not in allowed_set)
    if unsupported:
        if allowed:
            allowed_text = ", ".join(allowed)
            guidance = f"Use only: {allowed_text}."
        else:
            guidance = "This tool does not accept arguments."
        message = (
            f"Unsupported argument(s) for {tool_name}: "
            f"{', '.join(unsupported)}. {guidance}"
        )
        error = _argument_error(tool_name, message, allowed=allowed)
        error["unsupported_arguments"] = unsupported
        return None, error
    return json.dumps(args, separators=(",", ":")), None


def _schema_type_contains(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in {str(item) for item in value}
    return False


def _compact_schema_type(value: Any, allowed: set[str]) -> Any:
    if isinstance(value, str):
        return value if value in allowed else None
    if isinstance(value, list):
        types = [str(item) for item in value if str(item) in allowed]
        if not types:
            return None
        return types[0] if len(types) == 1 else types
    return None


def _provider_schema(value: Any, *, root: bool = False, key_name: str = "") -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {
                "additionalProperties",
                "default",
                "description",
                "maximum",
                "maxItems",
                "minimum",
                "minItems",
            } and key_name not in _KEEP_ARRAY_SHAPE_KEYS:
                continue
            if key == "properties" and isinstance(item, dict):
                result[key] = {
                    str(name): _provider_schema(schema, key_name=str(name))
                    for name, schema in item.items()
                }
            elif key == "items" and key_name in _KEEP_ARRAY_SHAPE_KEYS:
                result[key] = _provider_schema(item, key_name=key_name)
            else:
                result[key] = _provider_schema(item, key_name=str(key))
        if root:
            result.setdefault("type", "object")
            result.setdefault("properties", {})
            if result.get("required") == []:
                result.pop("required", None)
            return result
        if "enum" in result and key_name in _DROP_PROVIDER_ENUM_KEYS:
            return {}
        if "enum" in result:
            return {"enum": result["enum"]}
        schema_type = result.get("type")
        if _schema_type_contains(schema_type, "object") or "properties" in result:
            if key_name not in _KEEP_OBJECT_SHAPE_KEYS:
                return {}
            compact = {"properties": result.get("properties", {})}
            if result.get("required"):
                compact["required"] = result["required"]
            return compact
        if _schema_type_contains(schema_type, "array"):
            if key_name in _KEEP_ARRAY_SHAPE_KEYS:
                compact = {"type": "array"}
                for count_key in ("minItems", "maxItems"):
                    if count_key in result:
                        compact[count_key] = result[count_key]
                items = result.get("items")
                if isinstance(items, dict):
                    compact["items"] = items
                return compact
            items = result.get("items")
            if isinstance(items, dict) and "enum" in items:
                return {"items": items}
            return {}
        if key_name in _KEEP_ARRAY_SHAPE_KEYS:
            compact_type = _compact_schema_type(
                schema_type,
                {"number", "integer", "string", "boolean"},
            )
            if compact_type is not None:
                return {"type": compact_type}
        return {}
    if isinstance(value, list):
        return [_provider_schema(item, key_name=key_name) for item in value]
    return value


def tool_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(schema.get("name", ""))
    parameters = schema.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    parameters = _schema_for_provider_tool(tool_name, parameters)
    result = _provider_schema(dict(parameters), root=True)
    return result


_OMIT = object()
_DROP_RESULT_KEYS = {
    "arguments_json",
    "blocked_arguments_json",
    "document_summary",
    "document_after",
    "document_before",
    "full_context",
    "next_action",
    "next_actions",
    "provider_tool_schemas",
    "provider_tool_surface",
    "recent_feedback",
    "suggested_next_actions",
    "sketcher",
    "stdout",
    "task_panel",
    "tool_shape_report",
    "warnings",
    "why",
}
_KEEP_TRANSACTION_KEYS = (
    "error",
    "mutated_document",
    "ok",
    "report_view_errors",
    "result",
    "rolled_back",
)
_MAX_RESULT_TEXT = 240
_MAX_RESULT_ITEMS = 6
_MAX_RESULT_DEPTH = 4
_INSPECT_LIST_ITEM_LIMITS = {
    "constraints": 50,
    "degenerate_geometry": 50,
    "dependent_parameters": 50,
    "duplicate_edges": 50,
    "geometry": 50,
    "line_self_intersections": 50,
    "nonconstruction_edges": 50,
    "open_nodes": 50,
    "t_junction_nodes": 50,
    "tiny_edges": 50,
}


def _compact_text(value: Any, limit: int = _MAX_RESULT_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "." * max(0, limit)
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_provider_result(tool_name: str, value: Any) -> Any:
    return _compact_provider_value(tool_name, value, depth=0, key="")


def _result_item_limit(tool_name: str, key: str) -> int:
    if tool_name == "sketcher.inspect_sketch" and key in _INSPECT_LIST_ITEM_LIMITS:
        return _INSPECT_LIST_ITEM_LIMITS[key]
    return _MAX_RESULT_ITEMS


def _result_text_limit(key: str) -> int:
    if key in {"error", "err", "stderr"}:
        return 480
    if key in {"reason", "why", "status"}:
        return 120
    if key in {"name", "label", "feature", "active_sketch", "active_body"}:
        return 64
    return _MAX_RESULT_TEXT


def _compact_provider_value(
    tool_name: str,
    value: Any,
    *,
    depth: int,
    key: str,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _compact_text(value, _result_text_limit(key))
    if depth >= _MAX_RESULT_DEPTH:
        return _compact_text(value)
    if isinstance(value, (list, tuple)):
        item_limit = _result_item_limit(tool_name, key)
        return [
            item
            for item in (
                _compact_provider_value(tool_name, item, depth=depth + 1, key=key)
                for item in list(value)[:item_limit]
            )
            if item is not _OMIT
        ]
    if isinstance(value, dict):
        if key == "transaction":
            return _compact_transaction(tool_name, value, depth=depth)
        result: dict[str, Any] = {}
        top_level_has_payload = key == "" and any(
            str(candidate_key)
            not in {
                "ok",
                "transaction",
                "next_action",
                "next_actions",
                "suggested_next_actions",
                "warnings",
                "why",
            }
            for candidate_key in value
        )
        for raw_key, raw_item in value.items():
            item_key = str(raw_key)
            if _drop_result_field(
                tool_name,
                item_key,
                raw_item,
                parent_key=key,
                top_level_has_payload=top_level_has_payload,
            ):
                continue
            if item_key in _DROP_RESULT_KEYS:
                if item_key == "sketcher" and tool_name == "sketcher.inspect_sketch":
                    pass
                else:
                    continue
            compact = _compact_provider_value(
                tool_name,
                raw_item,
                depth=depth + 1,
                key=item_key,
            )
            if compact is _OMIT or compact in (None, "", [], {}):
                continue
            result[item_key] = compact
        return result
    return _compact_text(value)


def _drop_result_field(
    tool_name: str,
    item_key: str,
    raw_item: Any,
    *,
    parent_key: str,
    top_level_has_payload: bool,
) -> bool:
    if (
        item_key == "transaction"
        and parent_key == ""
        and top_level_has_payload
        and _transaction_is_clean_success(raw_item)
    ):
        return True
    if item_key == "report_view_errors" and not _report_view_errors_have_signal(raw_item):
        return True
    if item_key in {"profile_validation", "profile_validation_deep"}:
        return tool_name != "sketcher.inspect_sketch"
    if parent_key == "solver_status" and item_key == "profile_status":
        return True
    if parent_key == "mutation" and item_key == "solver_status":
        return True
    if tool_name != "sketcher.inspect_sketch":
        if parent_key == "profile_status" and item_key in {
            "closed_edge_loop",
            "construction_geometry_count",
            "edge_count",
            "face_count",
            "sketch_label",
            "under_constrained",
        }:
            return True
        if parent_key == "solver_status" and item_key in {"sketch_label"}:
            return True
    if item_key == "partdesign" and tool_name != "partdesign.get_bodies":
        return True
    return False


def _transaction_is_clean_success(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not bool(value.get("ok")):
        return False
    for key in (
        "error",
        "aborted_transaction",
        "rollback_incomplete",
        "rolled_back_transaction",
        "created_object_cleanup",
    ):
        if value.get(key):
            return False
    if _report_view_errors_have_signal(value.get("report_view_errors")):
        return False
    verification = value.get("verification")
    if isinstance(verification, dict) and verification.get("ok") is False:
        return False
    return True


def _report_view_errors_have_signal(value: Any) -> bool:
    if not isinstance(value, dict):
        return bool(value)
    errors = value.get("errors")
    if isinstance(errors, list) and errors:
        return True
    if errors and not isinstance(errors, list):
        return True
    for key in (
        "error",
        "exception",
        "traceback",
        "tracebacks",
        "exceptions",
        "aborted_transaction",
        "rollback_incomplete",
    ):
        if value.get(key):
            return True
    for key in ("error_count", "new_error_count"):
        try:
            if int(value.get(key, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _compact_transaction(tool_name: str, transaction: dict[str, Any], *, depth: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _KEEP_TRANSACTION_KEYS:
        if key not in transaction:
            continue
        if key == "report_view_errors" and not _report_view_errors_have_signal(
            transaction[key]
        ):
            continue
        compact = _compact_provider_value(
            tool_name,
            transaction[key],
            depth=depth + 1,
            key=key,
        )
        if compact not in (None, "", [], {}):
            result[key] = compact
    return result


def create_provider_tool(
    tool_name: str,
    function_name: str,
    schema: dict[str, Any],
    conn: Any,
    FunctionTool: Any,
) -> Any:
    async def _invoke(_tool_context, arguments_json: str):
        valid_arguments_json, argument_error = _validate_backend_arguments(
            tool_name, schema, arguments_json or "{}"
        )
        if argument_error is not None:
            return argument_error
        conn.send(
            {
                "type": "tool",
                "tool_name": tool_name,
                "arguments_json": valid_arguments_json,
            }
        )
        response = conn.recv()
        if response.get("type") != "tool_result":
            return {"ok": False, "error": "Invalid VibeCAD tool bridge response."}
        return _compact_provider_result(
            tool_name,
            response.get("result", {"ok": False, "error": "Missing tool result."}),
        )

    return FunctionTool(
        name=provider_function_name(tool_name, function_name),
        description=tool_description(schema),
        params_json_schema=tool_json_schema(schema),
        on_invoke_tool=_invoke,
        strict_json_schema=False,
    )
