# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``model.build_from_script``.

Executes a complete parametric FreeCAD Python script inside a single
document transaction so the provider can author (or re-author) a whole
part in one deliberate, reviewable step instead of dozens of micro tool
calls. On failure the transaction is aborted and the returned error
includes a script-line-accurate traceback plus the failing source line.
"""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from VibeCADTransactions import run_freecad_transaction

SCRIPT_FILENAME = "<vibecad_build_script>"

TOOL_SPEC = {
    "contextual": True,
    "description": (
        "Execute one FreeCAD Python script as a single undoable transaction. "
        "Use named dimensions, editable feature history, and self-checks. "
        "Exceptions roll back and report the failing line."
    ),
    "name": "model.build_from_script",
    "parameters": {
        "properties": {
            "script": {
                "type": "string",
                "description": (
                    "FreeCAD Python source for the full design increment. "
                    "Millimeters. Use doc, App, Gui, Part, Sketcher, math."
                ),
            },
            "description": {
                "type": "string",
                "description": "One-line summary of what this script builds or changes.",
            },
        },
        "required": ["script"],
        "type": "object",
    },
    "safety": "SAFE_WRITE",
    "workbench": None,
}


def _script_error_report(exc: BaseException, script: str) -> str:
    lines = script.splitlines()
    frames = [
        frame
        for frame in traceback.extract_tb(exc.__traceback__)
        if frame.filename == SCRIPT_FILENAME
    ]
    parts: list[str] = [f"{type(exc).__name__}: {exc}"]
    if isinstance(exc, SyntaxError) and exc.filename == SCRIPT_FILENAME:
        lineno = int(exc.lineno or 0)
        if 1 <= lineno <= len(lines):
            parts.append(f"line {lineno}: {lines[lineno - 1].strip()}")
    for frame in frames:
        source = ""
        if frame.lineno and 1 <= frame.lineno <= len(lines):
            source = lines[frame.lineno - 1].strip()
        parts.append(f"script line {frame.lineno}: {source or frame.line or ''}")
    return "\n".join(parts)


def _solid_validity(doc: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for obj in getattr(doc, "Objects", []) or []:
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            solids = list(getattr(shape, "Solids", []) or [])
            if not solids:
                continue
            checks.append(
                {
                    "name": getattr(obj, "Name", ""),
                    "label": getattr(obj, "Label", getattr(obj, "Name", "")),
                    "solids": len(solids),
                    "valid": bool(shape.isValid()),
                    "volume_mm3": round(float(getattr(shape, "Volume", 0.0) or 0.0), 3),
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": getattr(obj, "Name", ""),
                    "valid": False,
                    "error": str(exc),
                }
            )
    return checks


def run(
    service: Any,
    script: str = "",
    description: str | None = None,
) -> dict[str, Any]:
    source = str(script or "").strip()
    if not source:
        return {
            "ok": False,
            "error": "script is required and cannot be empty.",
        }

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def _execute() -> dict[str, Any]:
        import math

        import FreeCAD as App

        try:
            import FreeCADGui as Gui
        except Exception:
            Gui = None
        try:
            import Part
        except Exception:
            Part = None
        try:
            import Sketcher
        except Exception:
            Sketcher = None

        doc = App.ActiveDocument
        if doc is None:
            doc = App.newDocument("VibeCAD")
        script_globals: dict[str, Any] = {
            "__name__": "__vibecad_build__",
            "App": App,
            "FreeCAD": App,
            "Gui": Gui,
            "FreeCADGui": Gui,
            "Part": Part,
            "Sketcher": Sketcher,
            "math": math,
            "doc": doc,
        }
        try:
            compiled = compile(source, SCRIPT_FILENAME, "exec")
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(compiled, script_globals)  # noqa: S102 - deliberate script tool
        except BaseException as exc:
            raise RuntimeError(_script_error_report(exc, source)) from exc

        active = App.ActiveDocument or doc
        if active is not None and hasattr(active, "recompute"):
            active.recompute()
        return {
            "description": description or "",
            "solid_validity": _solid_validity(active),
            "stdout": stdout_buffer.getvalue()[-4000:],
            "stderr": stderr_buffer.getvalue()[-2000:],
        }

    transaction = run_freecad_transaction(
        description or "VibeCAD build from script",
        _execute,
    )
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    response: dict[str, Any] = {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "document_delta": transaction.get("document_delta"),
        "report_view_errors": transaction.get("report_view_errors"),
    }
    if transaction.get("ok"):
        response["solid_validity"] = result.get("solid_validity", [])
        response["stdout"] = result.get("stdout", "")
        response["stderr"] = result.get("stderr", "")
        invalid = [item for item in response["solid_validity"] if not item.get("valid")]
        if invalid:
            response["warnings"] = ["Invalid solids; fix."]
        response["next_actions"] = [
            {
                "tool": "model.get_geometry_report",
                "why": "Verify geometry.",
            },
            {
                "tool": "core.capture_view_screenshot",
                "why": "Review view.",
            },
        ]
    else:
        response["error"] = transaction.get("error", "Script execution failed.")
        response["stdout"] = stdout_buffer.getvalue()[-4000:]
        response["stderr"] = stderr_buffer.getvalue()[-2000:]
        response["next_actions"] = [
            {
                "tool": "model.build_from_script",
                "why": (
                    "The transaction was rolled back; fix the script using the "
                    "traceback above and resubmit the complete corrected script."
                ),
            }
        ]
    return response
