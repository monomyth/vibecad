# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native spreadsheet object in the active document."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "spreadsheet.create_sheet",
    "description": (
        "Create one native spreadsheet (Spreadsheet::Sheet) in the active "
        "document. Fill it with spreadsheet.set_cells; aliased cells can drive "
        "parametric expressions in other objects as SheetName.alias."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "SpreadsheetWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Visible label for the new spreadsheet object.",
            },
        },
        "required": ["label"],
        "additionalProperties": False,
    },
}


def run(service: Any, label: str) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")

    def create() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        obj = doc.addObject("Spreadsheet::Sheet", "Spreadsheet")
        if obj is None:
            raise RuntimeError("FreeCAD did not create a Spreadsheet::Sheet object.")
        obj.Label = clean_label
        doc.recompute()
        return {
            "document": doc.Name,
            "sheet": obj.Name,
            "sheet_label": obj.Label,
            "sheet_type": obj.TypeId,
        }

    transaction = run_freecad_transaction(
        f"Create spreadsheet: {clean_label}",
        create,
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "create_sheet"},
        next_action=(
            "Write values, formulas, and aliases with spreadsheet.set_cells "
            "using the returned exact sheet name."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
