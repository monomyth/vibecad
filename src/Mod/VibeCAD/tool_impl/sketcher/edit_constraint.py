# SPDX-License-Identifier: LGPL-2.1-or-later

"""Consolidated native Sketcher constraint editing and lookup tool."""

from __future__ import annotations

from typing import Any

from .common import (
    active_response,
    get_sketch,
    profile_validation,
    resolve_constraint_index,
    run_freecad_transaction,
    solver_status,
    validate_constraint_index,
)


TOOL_SPEC = {
    "name": "sketcher.edit_constraint",
    "description": (
        "Edit or inspect an existing Sketcher constraint. Use set_value, "
        "set_name, set_driving, set_expression, or get. Address by index, "
        "name, or handle. Creates nothing; use sketcher.add_constraint for "
        "new constraints."
    ),
    "contextual": True,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set_value", "set_name", "set_driving", "set_expression", "get"],
            },
            "sketch_name": {
                "type": "string",
                "description": "Sketch object name or label. Defaults to the active edit sketch or first sketch.",
            },
            "constraint_index": {"type": "integer", "description": "Target constraint index (0-based)."},
            "constraint_name": {"type": "string", "description": "Target constraint name (alternative to constraint_index)."},
            "constraint_handle": {"type": "string", "description": "Target handle such as constraint:3 or name:width."},
            "value": {
                "type": "number",
                "description": "set_value only: new datum (mm for lengths, degrees for Angle).",
            },
            "new_name": {"type": "string", "description": "set_name only: new constraint name."},
            "driving": {
                "type": "boolean",
                "description": "set_driving only: true for driving, false for reference.",
            },
            "expression": {
                "type": "string",
                "description": "set_expression only: expression text; empty string clears the binding.",
            },
        },
        "required": ["action"],
    },
}


DIMENSION_CONSTRAINTS = {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"}


def _resolve_target(
    service: Any,
    sketch_name: str | None,
    constraint_index: int | None,
    constraint_name: str | None,
    constraint_handle: str | None,
) -> tuple[Any | None, int | None, dict[str, Any] | None]:
    sketch = get_sketch(service, sketch_name)
    if sketch is None:
        return None, None, {"ok": False, "error": "Sketch not found.", "requested": sketch_name}
    try:
        index = resolve_constraint_index(sketch, constraint_index, constraint_name, constraint_handle)
    except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
        available = []
        try:
            available = service.sketcher_summary(getattr(sketch, "Name", None)).get("constraints", [])
        except (RuntimeError, AttributeError, KeyError, TypeError):
            available = []
        return sketch, None, {
            "ok": False,
            "error": str(exc),
            "constraint_index": constraint_index,
            "constraint_name": constraint_name,
            "constraint_handle": constraint_handle,
            "available_constraints": available,
        }
    invalid = validate_constraint_index(sketch, index)
    if invalid:
        return sketch, None, invalid
    return sketch, int(index), None


def _set_value(service: Any, sketch: Any, index: int, value: float | None) -> dict[str, Any]:
    if value is None:
        return {"ok": False, "error": "value is required for action=set_value."}
    constraint = list(getattr(sketch, "Constraints", []))[index]
    constraint_type = str(getattr(constraint, "Type", ""))
    if constraint_type not in DIMENSION_CONSTRAINTS:
        return {
            "ok": False,
            "error": f"Constraint is not a dimension datum: {constraint_type}",
            "constraint_index": index,
            "constraint_type": constraint_type,
        }
    if float(value) <= 0 and constraint_type not in {"Angle", "DistanceX", "DistanceY"}:
        return {"ok": False, "error": f"{constraint_type} values must be positive."}

    def _apply() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before = float(getattr(list(getattr(target, "Constraints", []))[index], "Value", 0.0))
        units = App.Units.Angle if constraint_type == "Angle" else App.Units.Length
        target.setDatum(index, App.Units.Quantity(float(value), units))
        if App.ActiveDocument is not None:
            App.ActiveDocument.recompute()
        after = float(getattr(list(getattr(target, "Constraints", []))[index], "Value", 0.0))
        return {
            "sketch": target.Name,
            "action": "set_value",
            "constraint_index": index,
            "constraint_type": constraint_type,
            "before": before,
            "after": after,
        }

    return active_response(
        service,
        sketch,
        run_freecad_transaction(f"Edit Sketcher constraint {index} value", _apply),
    )


def _set_name(service: Any, sketch: Any, index: int, new_name: str | None) -> dict[str, Any]:
    if not new_name or not str(new_name).strip():
        return {"ok": False, "error": "new_name is required for action=set_name."}

    def _apply() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before = getattr(list(getattr(target, "Constraints", []))[index], "Name", "")
        target.renameConstraint(index, str(new_name))
        if App.ActiveDocument is not None:
            App.ActiveDocument.recompute()
        after = getattr(list(getattr(target, "Constraints", []))[index], "Name", "")
        return {
            "sketch": target.Name,
            "action": "set_name",
            "constraint_index": index,
            "old_constraint_name": before,
            "constraint_name": after,
        }

    return active_response(
        service,
        sketch,
        run_freecad_transaction(f"Rename Sketcher constraint {index}", _apply),
    )


def _set_driving(service: Any, sketch: Any, index: int, driving: bool | None) -> dict[str, Any]:
    if driving is None:
        return {"ok": False, "error": "driving is required for action=set_driving."}

    def _apply() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        before_constraint = list(getattr(target, "Constraints", []))[index]
        before = bool(getattr(before_constraint, "Driving", getattr(before_constraint, "isDriving", True)))
        target.setDriving(index, bool(driving))
        if App.ActiveDocument is not None:
            App.ActiveDocument.recompute()
        after_constraint = list(getattr(target, "Constraints", []))[index]
        after = bool(getattr(after_constraint, "Driving", getattr(after_constraint, "isDriving", True)))
        return {
            "sketch": target.Name,
            "action": "set_driving",
            "constraint_index": index,
            "before_driving": before,
            "driving": after,
        }

    return active_response(
        service,
        sketch,
        run_freecad_transaction(f"Set Sketcher constraint {index} driving state", _apply),
    )


def _set_expression(service: Any, sketch: Any, index: int, expression: str | None) -> dict[str, Any]:
    if expression is None:
        return {"ok": False, "error": "expression is required for action=set_expression (empty string clears)."}

    def _apply() -> dict[str, Any]:
        import FreeCAD as App

        target = get_sketch(service, sketch.Name)
        if target is None:
            raise RuntimeError(f"Sketch not found: {sketch.Name}")
        path = f"Constraints[{index}]"
        clean = str(expression).strip()
        target.setExpression(path, clean or None)
        if App.ActiveDocument is not None:
            App.ActiveDocument.recompute()
        expressions: dict[str, str] = {}
        try:
            expressions = {str(expr_path): str(expr) for expr_path, expr in target.ExpressionEngine}
        except (AttributeError, TypeError, ValueError):
            expressions = {}
        return {
            "sketch": target.Name,
            "action": "set_expression",
            "constraint_index": index,
            "expression_path": path,
            "expression": expressions.get(path),
        }

    return active_response(
        service,
        sketch,
        run_freecad_transaction(f"Set Sketcher constraint {index} expression", _apply),
    )


def _get(service: Any, sketch: Any, index: int) -> dict[str, Any]:
    constraints = service.sketcher_summary(getattr(sketch, "Name", None)).get("constraints", [])
    constraint = next((item for item in constraints if item.get("index") == index), None)
    return {
        "ok": True,
        "action": "get",
        "sketch": getattr(sketch, "Name", None),
        "constraint_index": index,
        "constraint": constraint,
        "solver_status": solver_status(service, sketch),
        "profile_validation": profile_validation(service, sketch),
    }


def run(
    service: Any,
    action: str = "get",
    sketch_name: str | None = None,
    constraint_index: int | None = None,
    constraint_name: str | None = None,
    constraint_handle: str | None = None,
    value: float | None = None,
    new_name: str | None = None,
    driving: bool | None = None,
    expression: str | None = None,
) -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    actions = {"set_value", "set_name", "set_driving", "set_expression", "get"}
    if clean_action not in actions:
        return {
            "ok": False,
            "error": f"Unsupported edit_constraint action: {clean_action}",
            "supported": sorted(actions),
        }
    sketch, index, failure = _resolve_target(
        service, sketch_name, constraint_index, constraint_name, constraint_handle
    )
    if failure is not None:
        return failure
    assert sketch is not None and index is not None
    result: dict[str, Any]
    if clean_action == "set_value":
        result = _set_value(service, sketch, index, value)
    elif clean_action == "set_name":
        result = _set_name(service, sketch, index, new_name)
    elif clean_action == "set_driving":
        result = _set_driving(service, sketch, index, driving)
    elif clean_action == "set_expression":
        result = _set_expression(service, sketch, index, expression)
    else:
        result = _get(service, sketch, index)
    if isinstance(result, dict) and constraint_name:
        result.setdefault("constraint_name", str(constraint_name))
    return result
