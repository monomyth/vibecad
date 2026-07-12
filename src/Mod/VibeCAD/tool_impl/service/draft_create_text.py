# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Draft text annotation at an exact global position."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "draft.create_text",
    "description": (
        "Create one native Draft text annotation at an exact global position. "
        "The text is a document annotation object; it does not cut, engrave, or "
        "modify any solid geometry."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "DraftWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "One line of annotation text.",
                },
                "minItems": 1,
                "description": "Annotation text, one array entry per displayed line.",
            },
            "position": domain_runtime.vector_schema(
                "Exact global position of the text anchor in mm."
            ),
            "height_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Character height of the displayed text in mm.",
            },
            "label": {
                "type": "string",
                "description": "Visible label for the annotation object.",
            },
        },
        "required": ["lines", "position", "height_mm", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    lines: list[str],
    position: dict[str, Any],
    height_mm: float,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if not isinstance(lines, list) or not lines:
        return _invalid("lines must contain at least one text line.")
    text_lines = [str(line) for line in lines]
    if not any(line.strip() for line in text_lines):
        return _invalid("lines must contain non-empty text.")
    height = float(height_mm)
    if height <= 0:
        return _invalid("height_mm must be greater than 0.")

    def create() -> dict[str, Any]:
        import Draft
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        placement = App.Placement(domain_runtime.parse_vector(position), App.Rotation())
        obj = Draft.make_text(text_lines, placement=placement)
        if obj is None:
            raise RuntimeError("Draft.make_text did not create an object.")
        obj.Label = clean_label
        view = getattr(obj, "ViewObject", None)
        font_size_supported = view is not None and hasattr(view, "FontSize")
        font_size_before = None
        font_size_error = None
        if font_size_supported:
            try:
                font_size_before = float(view.FontSize)
                view.FontSize = height
            except Exception as exc:
                font_size_error = str(exc)
        doc.recompute()
        font_size_after = None
        if font_size_supported and font_size_error is None:
            try:
                font_size_after = float(view.FontSize)
            except Exception as exc:
                font_size_error = str(exc)
        return {
            "document": doc.Name,
            "feature": obj.Name,
            "feature_label": obj.Label,
            "feature_type": obj.TypeId,
            "line_count": len(text_lines),
            "requested_lines": text_lines,
            "actual_lines": [str(line) for line in list(getattr(obj, "Text", []) or [])],
            "requested_position": domain_runtime.vector_values(placement.Base),
            "actual_position": domain_runtime.vector_values(obj.Placement.Base),
            "requested_height_mm": height,
            "font_size_supported": font_size_supported,
            "font_size_before_mm": font_size_before,
            "font_size_after_mm": font_size_after,
            "font_size_error": font_size_error,
            "feature_state": domain_runtime.feature_state_summary(obj),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        font_size = result.get("font_size_after_mm")
        checks = [
            {
                "name": "font_size_property_available",
                "ok": bool(result.get("font_size_supported")),
                "actual": result.get("font_size_supported"),
            },
            {
                "name": "font_size_applied",
                "ok": result.get("font_size_error") is None
                and isinstance(font_size, (int, float))
                and abs(float(font_size) - height) <= 1.0e-9,
                "expected": height,
                "actual": font_size,
                "native_error": result.get("font_size_error"),
            },
            {
                "name": "text_readback",
                "ok": result.get("actual_lines") == text_lines,
                "expected": text_lines,
                "actual": result.get("actual_lines"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Draft text: {clean_label}",
        create,
        verifier=verify,
    )
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "create_text", "mutation": mutation},
        next_action=(
            "Capture a screenshot to confirm annotation placement, or continue "
            "modeling."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
