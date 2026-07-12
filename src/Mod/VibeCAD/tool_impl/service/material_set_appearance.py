# SPDX-License-Identifier: LGPL-2.1-or-later

"""Set the displayed color and transparency of one document object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "material.set_appearance",
    "description": (
        "Set the displayed shape color and transparency of one named document "
        "object. This changes only how the object is rendered in the 3D view; "
        "it does not assign physical material properties (use "
        "material.apply_material for that). Requires the FreeCAD GUI."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "MaterialWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the object whose appearance to set."
                ),
            },
            "red": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "description": "Red component of the shape color, 0-255.",
            },
            "green": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "description": "Green component of the shape color, 0-255.",
            },
            "blue": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "description": "Blue component of the shape color, 0-255.",
            },
            "transparency_percent": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": (
                    "Transparency of the shape: 0 is fully opaque, 100 is "
                    "fully transparent."
                ),
            },
        },
        "required": ["object_name", "red", "green", "blue", "transparency_percent"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    object_name: str,
    red: int,
    green: int,
    blue: int,
    transparency_percent: int,
) -> dict[str, Any]:
    clean_name = str(object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return _invalid(
            "The object has no view representation (FreeCAD is running "
            "without a GUI, or the object is not displayable); appearance "
            f"cannot be set: {clean_name}"
        )
    requested = {
        "shape_color": {
            "red": int(red),
            "green": int(green),
            "blue": int(blue),
        },
        "transparency_percent": int(transparency_percent),
    }
    supported_properties = [
        name for name in ("ShapeColor", "Transparency") if hasattr(view, name)
    ]
    appearance_before = _appearance_summary(view)
    missing = [
        name for name in ("ShapeColor", "Transparency") if name not in supported_properties
    ]
    if missing:
        return _invalid(
            "The target does not support every requested appearance property; "
            "nothing was changed.",
            requested=requested,
            supported_properties=supported_properties,
            missing_properties=missing,
            appearance_before=appearance_before,
            retained_state=appearance_before,
        )

    def apply() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target = active.getObject(clean_name)
        if target is None:
            raise RuntimeError("The object no longer exists.")
        view_object = getattr(target, "ViewObject", None)
        if view_object is None:
            raise RuntimeError("The object no longer has a view representation.")
        color = (float(red) / 255.0, float(green) / 255.0, float(blue) / 255.0)
        applied_subset: list[str] = []
        property_results: list[dict[str, Any]] = []
        try:
            view_object.ShapeColor = color
            applied_subset.append("ShapeColor")
            property_results.append(
                {"property": "ShapeColor", "status": "applied"}
            )
        except Exception as exc:
            property_results.append(
                {
                    "property": "ShapeColor",
                    "status": "failed",
                    "native_error": str(exc),
                }
            )
        try:
            view_object.Transparency = int(transparency_percent)
            applied_subset.append("Transparency")
            property_results.append(
                {"property": "Transparency", "status": "applied"}
            )
        except Exception as exc:
            property_results.append(
                {
                    "property": "Transparency",
                    "status": "failed",
                    "native_error": str(exc),
                }
            )
        recompute_error = None
        try:
            active.recompute()
        except Exception as exc:
            recompute_error = str(exc)
        return {
            "document": active.Name,
            "object": target.Name,
            "object_label": target.Label,
            "requested": requested,
            "supported_properties": supported_properties,
            "appearance_before": appearance_before,
            "property_results": property_results,
            "applied_subset": applied_subset,
            "appearance_after": _appearance_summary(view_object),
            "recompute_error": recompute_error,
            "partial_retained_state": len(applied_subset) != 2,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        after = result.get("appearance_after") or {}
        checks = [
            {
                "name": "all_requested_properties_applied",
                "ok": result.get("applied_subset")
                == ["ShapeColor", "Transparency"],
                "property_results": result.get("property_results"),
                "applied_subset": result.get("applied_subset"),
            },
            {
                "name": "shape_color_readback",
                "ok": _color_matches(after.get("shape_color"), red, green, blue),
                "requested": requested["shape_color"],
                "actual": after.get("shape_color"),
            },
            {
                "name": "transparency_readback",
                "ok": after.get("transparency_percent")
                == int(transparency_percent),
                "requested": int(transparency_percent),
                "actual": after.get("transparency_percent"),
            },
            {
                "name": "recompute",
                "ok": result.get("recompute_error") is None,
                "native_error": result.get("recompute_error"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Set appearance: {clean_name}",
        apply,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "set_appearance", **result},
        next_action=("Verify the new appearance with core.capture_view_screenshot."),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _appearance_summary(view: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "supported_properties": [],
        "errors": [],
        "shape_color": None,
        "transparency_percent": None,
    }
    if hasattr(view, "ShapeColor"):
        result["supported_properties"].append("ShapeColor")
        try:
            color = tuple(view.ShapeColor)
            result["shape_color"] = {
                "red": int(round(float(color[0]) * 255.0)),
                "green": int(round(float(color[1]) * 255.0)),
                "blue": int(round(float(color[2]) * 255.0)),
                "normalized": [float(color[0]), float(color[1]), float(color[2])],
            }
        except Exception as exc:
            result["errors"].append(
                {"property": "ShapeColor", "native_error": str(exc)}
            )
    if hasattr(view, "Transparency"):
        result["supported_properties"].append("Transparency")
        try:
            result["transparency_percent"] = int(view.Transparency)
        except Exception as exc:
            result["errors"].append(
                {"property": "Transparency", "native_error": str(exc)}
            )
    return result


def _color_matches(actual: Any, red: int, green: int, blue: int) -> bool:
    if not isinstance(actual, dict):
        return False
    return all(
        abs(int(actual.get(channel, -1000)) - expected) <= 1
        for channel, expected in (
            ("red", int(red)),
            ("green", int(green)),
            ("blue", int(blue)),
        )
    )
