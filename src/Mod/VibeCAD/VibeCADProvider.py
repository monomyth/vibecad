# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider abstraction for VibeCAD AI runtimes."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any, Callable

from VibeCADDebug import capture_provider_request


MAX_PROVIDER_IMAGE_BYTES = 2_000_000
PROVIDER_IMAGE_MAX_EDGE = 1568
PROVIDER_IMAGE_MIN_EDGE = 512
DEFAULT_ANTHROPIC_MAX_TOKENS = 8192
ANTHROPIC_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 32768,
}
ANTHROPIC_ADAPTIVE_EFFORT = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}
ANTHROPIC_STREAM_MAX_ATTEMPTS = 3


VIBECAD_SYSTEM_INSTRUCTIONS = (
    "You are VibeCAD, a senior mechanical CAD engineer operating the user's "
    "live FreeCAD document through native editable features. The requested "
    "product and its real use are the authority. A simple shape that merely "
    "resembles the request is a failure.\n\n"
    "For a new substantial design, your first visible prose must restate the "
    "user's intended outcome and describe the concrete design you propose "
    "before the first CAD write. Develop that design in writing: components, "
    "interfaces, load/contact/motion paths, fit and swept envelopes, mechanism "
    "behavior, manufacturing process, critical dimensions, and failure modes. "
    "Challenge it adversarially: determine how it assembles, moves, closes, "
    "clears, carries load, and can be manufactured. This written design is part "
    "of the conversation and remains the working intent. Write this design "
    "once; when the conversation already contains an accepted design, continue "
    "that design from the actual document state instead of restarting "
    "refinement.\n\n"
    "Resolve ordinary engineering choices yourself using defensible defaults. "
    "When a user choice would materially change geometry or function, call "
    "conversation.ask_user with useful options and your recommended answer. "
    "This is clarification, not an approval gate.\n\n"
    "The pinned DESIGN DOCUMENT is your durable working record for this CAD file. "
    "For creation or modification work, create it once the intended outcome is understood, "
    "then keep its outcome, accepted decisions, required parts, interfaces, and remaining "
    "work synchronized with user decisions and verified document state. Update it only "
    "through project.update_design_document. The latest user message and conversation "
    "remain authoritative if they conflict with the document. The Markdown is memory, "
    "not a workflow gate or permission system.\n\n"
    "Author the geometry the design actually requires. Lines represent truly "
    "straight edges; arcs, conics, and splines represent curved form. Pads and "
    "pockets represent constant sections; revolves represent axisymmetry; "
    "lofts and sweeps represent changing or guided sections; patterns represent "
    "real repetition. Fillets and chamfers are finishing operations, never a "
    "substitute for omitted primary form. Use separate Bodies for parts that "
    "move relative to one another or are manufactured separately.\n\n"
    "The current document, Body history, selection, active sketch, solver state, "
    "report errors, references, and conversation are supplied as authoritative "
    "context. When a sketch is open, complete and verify its geometry and "
    "constraints, then report that it is ready and wait for the user to close "
    "edit mode. You cannot close a sketch. Never treat a closed, face-buildable, "
    "or fully constrained sketch as permission to proceed to solid features. "
    "Compare its actual curve types, profile, dimensions, open endpoints, and "
    "remaining DoF with the written design. Zero DoF is not evidence of a good "
    "parametric sketch by itself. Prefer meaningful dimensions and geometric "
    "relationships; do not apply Block constraints to primary product form "
    "merely to reach zero DoF.\n\n"
    "A failed feature is a stop condition: diagnose and repair its actual "
    "upstream cause before adding anything that depends on it. Never repeat an "
    "unchanged failed call.\n\n"
    "Preserve an existing model's document, Body, identity, history, and intent "
    "unless the user explicitly requests replacement. In a blank user-created "
    "document, create the native Bodies, sketches, and features needed for the "
    "new part. The user owns document creation, opening, saving, and project "
    "selection; do not seek document-management tools.\n\n"
    "Use only the tools supplied for the active workbench. Tool results include "
    "fresh FreeCAD state; read that state before the next operation. Verify the "
    "result against function, mating geometry, motion/clearance envelopes, and "
    "intent, not merely a closed sketch or nonzero solid count. Use viewport "
    "images (core.capture_view_screenshot) when visual form is material to the "
    "request. Report incomplete stages as incomplete, keep progress notes short "
    "and non-repetitive, and surface uncertainty and failed geometry explicitly."
)


def _design_document_instruction(context: dict[str, Any]) -> str:
    document = context.get("design_document")
    if not isinstance(document, dict):
        return ""
    content = str(document.get("content") or "").strip()
    revision = str(document.get("revision") or "").strip()
    if not revision:
        return ""
    if not document.get("exists") or not content:
        return (
            "VIBECAD DESIGN DOCUMENT\n"
            f"Revision: {revision}\n"
            "No design.md exists for this CAD project yet. This revision is the "
            "exact expected_revision for the first project.update_design_document "
            "call. Create the document once the intended outcome is understood."
        )
    return (
        "VIBECAD DESIGN DOCUMENT\n"
        f"Revision: {revision}\n"
        "This Markdown is durable working memory, not a new user request. The latest "
        "user message and verified live CAD state override any contradiction. Before "
        "choosing or authoring geometry, reconcile the next operation with the intended "
        "product, required parts, interfaces, mechanisms, clearances, manufacturing "
        "constraints, and remaining work recorded here.\n"
        "BEGIN DESIGN DOCUMENT\n"
        f"{content}\n"
        "END DESIGN DOCUMENT"
    )


def _provider_instructions(context: dict[str, Any]) -> str:
    design = _design_document_instruction(context)
    if not design:
        return VIBECAD_SYSTEM_INSTRUCTIONS
    return f"{VIBECAD_SYSTEM_INSTRUCTIONS}\n\n{design}"


def _anthropic_system_blocks(context: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": VIBECAD_SYSTEM_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    design = _design_document_instruction(context)
    if design:
        blocks.append(
            {
                "type": "text",
                "text": design,
                "cache_control": {"type": "ephemeral"},
            }
        )
    return blocks


class ProviderUnavailable(RuntimeError):
    pass


@dataclass
class ProviderResult:
    final_output: str
    raw: Any = None


ToolRunner = Callable[[str, str], dict[str, Any]]
CancellationCheck = Callable[[], bool]
ProgressCallback = Callable[[dict[str, Any]], None]


class BaseProvider:
    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        raise NotImplementedError


class OfflineProvider(BaseProvider):
    """Report that AI is unavailable without pretending to perform CAD work."""

    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        if cancellation_check is not None and cancellation_check():
            raise ProviderUnavailable("VibeCAD run stopped by user.")
        workbench = context.get("workbench") or "unknown"
        return ProviderResult(
            "VibeCAD is offline. "
            f"Active workbench: {workbench}. "
            "Configure authentication before asking the AI provider."
        )


class OpenAIProvider(BaseProvider):
    """OpenAI SDK adapter driven by VibeCAD's own streaming tool loop."""

    def __init__(
        self,
        model: str = "gpt-5.5",
        api_key: str | None = None,
        reasoning_effort: str = "high",
        timeout_seconds: float | None = None,
        max_turns: int | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns
        self.base_url = base_url

    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        try:
            return _run_provider_subprocess(
                prompt=prompt,
                context=context,
                tool_runner=tool_runner,
                model=self.model,
                api_key=self.api_key,
                reasoning_effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                max_turns=self.max_turns,
                base_url=self.base_url,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            )
        except TimeoutError as exc:
            if self.timeout_seconds and self.timeout_seconds > 0:
                raise ProviderUnavailable(
                    f"OpenAI provider timed out after {self.timeout_seconds:g} seconds."
                ) from exc
            raise


class AnthropicProvider(BaseProvider):
    """Native Anthropic Messages API adapter.

    Drives a tool-use loop over the same parent/child pipe bridge as the
    OpenAI path: the child sends ``tool`` requests, the parent executes the
    real FreeCAD tool and replies with ``tool_result``. The dependency on the
    ``anthropic`` SDK stays optional so FreeCAD can start without it.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        api_key: str | None = None,
        reasoning_effort: str = "high",
        timeout_seconds: float | None = None,
        max_turns: int | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns
        self.base_url = base_url

    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        try:
            return _run_provider_subprocess(
                prompt=prompt,
                context=context,
                tool_runner=tool_runner,
                model=self.model,
                api_key=self.api_key,
                reasoning_effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                max_turns=self.max_turns,
                base_url=self.base_url,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
                child_main=_anthropic_child_main,
                provider_label="Anthropic provider",
            )
        except TimeoutError as exc:
            if self.timeout_seconds and self.timeout_seconds > 0:
                raise ProviderUnavailable(
                    f"Anthropic provider timed out after {self.timeout_seconds:g} seconds."
                ) from exc
            raise


def _run_with_deadline(call: Callable[[], Any], timeout_seconds: float) -> Any:
    if (
        timeout_seconds <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
    ):
        return call()

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(signum, frame):
        raise TimeoutError

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return call()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _provider_reasoning_effort(value: str | None) -> str | None:
    clean = str(value or "").strip().lower()
    if clean in {"", "none", "off", "disabled", "false", "0"}:
        return None
    return clean


def _provider_windows_gui_session() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from PySide import QtWidgets
    except Exception:
        return False
    try:
        return QtWidgets.QApplication.instance() is not None
    except Exception:
        return False


def _provider_spawn_python_executable(
    prefer_windowless: bool | None = None,
) -> str | None:
    if sys.platform != "win32":
        return None

    use_windowless = (
        _provider_windows_gui_session()
        if prefer_windowless is None
        else bool(prefer_windowless)
    )
    executable_names = (
        ("pythonw.exe", "python.exe")
        if use_windowless
        else ("python.exe", "pythonw.exe")
    )
    candidates: list[Path] = []
    current_executable = Path(sys.executable or "")
    if current_executable.name.lower() in {"python.exe", "pythonw.exe"}:
        candidates.extend(
            current_executable.with_name(name) for name in executable_names
        )
    elif current_executable.name:
        candidates.extend(
            current_executable.with_name(name) for name in executable_names
        )

    for prefix in {sys.prefix, getattr(sys, "base_prefix", "")}:
        if prefix:
            candidates.extend(Path(prefix) / name for name in executable_names)

    seen: set[str] = set()
    for candidate in candidates:
        candidate_text = str(candidate)
        if not candidate_text or candidate_text in seen:
            continue
        seen.add(candidate_text)
        if candidate.exists():
            return candidate_text
    return None


def _provider_multiprocessing_context(
    prefer_windowless_python: bool | None = None,
) -> multiprocessing.context.BaseContext:
    start_methods = multiprocessing.get_all_start_methods()
    if "fork" in start_methods:
        return multiprocessing.get_context("fork")

    if sys.platform == "win32":
        python_executable = _provider_spawn_python_executable(
            prefer_windowless=prefer_windowless_python
        )
        if not python_executable:
            raise ProviderUnavailable(
                "VibeCAD cannot start the AI provider process because python.exe "
                "or pythonw.exe was not found in the packaged runtime."
            )
        multiprocessing.set_executable(python_executable)

    if "spawn" in start_methods:
        return multiprocessing.get_context("spawn")
    return multiprocessing.get_context()


@contextmanager
def _provider_spawn_bootstrap_environment():
    """Force multiprocessing spawn to use the packaged Python in Windows hosts.

    Python's Windows spawn command ignores ``multiprocessing.set_executable()``
    when ``sys.frozen`` is true and launches ``sys.executable`` with
    ``--multiprocessing-fork`` instead.  FreeCAD is an embedded application, not
    a Python-frozen app with a multiprocessing-aware executable, so the child can
    exit cleanly without ever running the target. Temporarily clearing the flag
    lets multiprocessing generate the normal ``python[w].exe -c spawn_main(...)``
    command line.
    """

    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        yield
        return

    sentinel = object()
    original = getattr(sys, "frozen", sentinel)
    try:
        try:
            delattr(sys, "frozen")
        except Exception:
            setattr(sys, "frozen", False)
        yield
    finally:
        if original is sentinel:
            try:
                delattr(sys, "frozen")
            except Exception:
                pass
        else:
            setattr(sys, "frozen", original)


def _provider_subprocess_smoke_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        conn.send(
            {
                "type": "done",
                "final_output": "ok",
                "raw": {"pid": os.getpid(), "executable": sys.executable},
            }
        )
    finally:
        conn.close()


def _provider_subprocess_smoke(
    *,
    prefer_windowless_python: bool | None = None,
    require_windowless_python: bool = False,
) -> None:
    result = _run_provider_subprocess(
        prompt="smoke",
        context={},
        tool_runner=None,
        model="smoke",
        api_key=None,
        reasoning_effort=None,
        timeout_seconds=10.0,
        max_turns=1,
        clear_inherited_modules=False,
        child_main=_provider_subprocess_smoke_child_main,
        provider_label="VibeCAD provider subprocess smoke",
        prefer_windowless_python=prefer_windowless_python,
    )
    if result.final_output != "ok":
        raise RuntimeError(f"Unexpected provider subprocess smoke result: {result!r}")
    executable = ""
    if isinstance(result.raw, dict):
        executable = str(result.raw.get("executable") or "")
    if (
        require_windowless_python
        and sys.platform == "win32"
        and not executable.lower().endswith("pythonw.exe")
    ):
        raise RuntimeError(
            f"Expected provider subprocess smoke to use pythonw.exe, got {executable!r}"
        )


def _run_provider_subprocess(
    *,
    prompt: str,
    context: dict[str, Any],
    tool_runner: ToolRunner | None,
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None = None,
    base_url: str | None = None,
    clear_inherited_modules: bool = True,
    event_pump: Callable[[], None] | None = None,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    child_main: Callable[..., None] | None = None,
    provider_label: str = "OpenAI provider",
    prefer_windowless_python: bool | None = None,
) -> ProviderResult:
    multiprocessing_context = _provider_multiprocessing_context(
        prefer_windowless_python=prefer_windowless_python
    )
    reasoning_effort = _provider_reasoning_effort(reasoning_effort)
    parent_conn, child_conn = multiprocessing_context.Pipe()
    process = multiprocessing_context.Process(
        target=child_main or _openai_child_main,
        args=(
            child_conn,
            prompt,
            context,
            model,
            api_key,
            reasoning_effort,
            timeout_seconds,
            max_turns,
            clear_inherited_modules,
            base_url,
        ),
    )
    process.daemon = True
    original_stdin = sys.stdin
    replacement_stdin = None
    try:
        if not hasattr(sys.stdin, "close"):
            replacement_stdin = open(os.devnull, "r", encoding="utf-8")
            sys.stdin = replacement_stdin
        with _provider_spawn_bootstrap_environment():
            process.start()
    finally:
        sys.stdin = original_stdin
        if replacement_stdin is not None:
            replacement_stdin.close()
    child_conn.close()
    provider_started_at = time.monotonic()
    last_provider_activity_at = provider_started_at
    last_wait_notice_at = 0.0
    _emit_provider_progress(
        progress_callback,
        {
            "event": "provider_subprocess_started",
            "provider": provider_label,
            "pid": process.pid,
        },
    )

    deadline = (
        time.monotonic() + timeout_seconds
        if timeout_seconds is not None and timeout_seconds > 0
        else None
    )
    pump_events = event_pump or _process_provider_wait_events
    try:
        while True:
            if cancellation_check is not None and cancellation_check():
                raise ProviderUnavailable("VibeCAD run stopped by user.")
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            if deadline is not None and remaining <= 0:
                raise TimeoutError
            wait_seconds = 0.05 if remaining is None else min(0.05, remaining)
            if parent_conn.poll(wait_seconds):
                try:
                    message = parent_conn.recv()
                except EOFError as exc:
                    raise ProviderUnavailable(
                        f"{provider_label} process ended before sending a result."
                    ) from exc
                last_provider_activity_at = time.monotonic()
                message_type = message.get("type")
                last_wait_notice_at = 0.0
                if message_type == "tool":
                    if cancellation_check is not None and cancellation_check():
                        raise ProviderUnavailable("VibeCAD run stopped by user.")
                    tool_name = str(message.get("tool_name", ""))
                    arguments_json = str(message.get("arguments_json") or "{}")
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_tool_requested",
                            "provider": provider_label,
                            "tool_name": tool_name,
                            "arguments": _tool_arguments_summary(arguments_json),
                        },
                    )
                    result = _call_parent_tool(
                        tool_runner,
                        tool_name,
                        arguments_json,
                    )
                    parent_conn.send(
                        {
                            "type": "tool_result",
                            "result": result,
                            "context": _tool_runner_provider_update(tool_runner),
                        }
                    )
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_tool_result_sent",
                            "provider": provider_label,
                            "tool_name": tool_name,
                            "ok": bool(result.get("ok")),
                            "error": result.get("error"),
                        },
                    )
                    continue
                elif message_type == "done":
                    process.join(timeout=0.2)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=1)
                    return ProviderResult(
                        final_output=str(message.get("final_output", "")),
                        raw=message.get("raw"),
                    )
                elif message_type == "progress":
                    event = message.get("event")
                    if isinstance(event, dict):
                        _emit_provider_progress(progress_callback, event)
                    continue
                elif message_type == "error":
                    error = str(message.get("error", "unknown provider error"))
                    raise ProviderUnavailable(error)
                else:
                    continue
            else:
                pump_events()
                now = time.monotonic()
                if (
                    progress_callback is not None
                    and now - last_provider_activity_at >= 8.0
                    and now - last_wait_notice_at >= 15.0
                ):
                    last_wait_notice_at = now
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_waiting",
                            "provider": provider_label,
                            "elapsed_seconds": now - provider_started_at,
                            "idle_seconds": now - last_provider_activity_at,
                            "pid": process.pid,
                        },
                    )

            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError

            if not process.is_alive():
                process.join(timeout=1)
                # A short-lived Windows pythonw child can finish immediately
                # after writing its final pipe message.  Give that message one
                # last bounded drain before treating a clean exit as empty.
                if parent_conn.poll(0.2):
                    continue
                if process.exitcode == 0:
                    raise ProviderUnavailable(
                        f"{provider_label} exited without a result."
                    )
                raise ProviderUnavailable(
                    f"{provider_label} process exited with code {process.exitcode}."
                )
    finally:
        parent_conn.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2)


def _process_provider_wait_events() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    from PySide import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    app.processEvents(QtCore.QEventLoop.AllEvents, 10)


def _emit_provider_progress(
    progress_callback: ProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    progress_callback(dict(event))


def _send_child_progress(conn: Any, event: dict[str, Any]) -> None:
    conn.send({"type": "progress", "event": _json_safe(event)})


def _tool_arguments_summary(arguments_json: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"bytes": len(arguments_json.encode("utf-8"))}
    try:
        arguments = json.loads(arguments_json or "{}")
    except Exception:
        summary["valid_json"] = False
        return summary
    summary["valid_json"] = True
    if not isinstance(arguments, dict):
        summary["shape"] = type(arguments).__name__
        return summary
    keys = [str(key) for key in arguments]
    summary["key_count"] = len(keys)
    summary["keys"] = keys[:8]
    if len(keys) > 8:
        summary["truncated"] = True
    return summary


def _call_parent_tool(
    tool_runner: ToolRunner | None,
    tool_name: str,
    arguments_json: str,
) -> dict[str, Any]:
    if tool_runner is None:
        return {"ok": False, "error": "No VibeCAD tool runner is available."}
    try:
        return tool_runner(tool_name, arguments_json)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _tool_runner_provider_update(
    tool_runner: ToolRunner | None,
) -> dict[str, Any]:
    if tool_runner is None:
        raise RuntimeError("No VibeCAD tool runner is available for state refresh.")
    refresh = getattr(tool_runner, "provider_update", None)
    if not callable(refresh):
        raise RuntimeError("The VibeCAD tool runner has no provider_update contract.")
    value = refresh()
    if not isinstance(value, dict):
        raise RuntimeError("VibeCAD provider_update returned no structured context.")
    return value


def _model_visible_context(
    context: dict[str, Any],
) -> dict[str, Any]:
    sections = (
        "workbench",
        "vibecad_project",
        "document",
        "selection",
        "view",
        "task_panel",
        "cad_state",
        "view_screenshot",
        "reference_images",
        "conversation",
        "partdesign",
        "sketcher",
        "part",
        "assembly",
        "surface",
        "draft",
        "techdraw",
        "cam",
        "fem",
        "material",
        "mesh",
        "spreadsheet",
    )
    return {
        key: _json_safe(context[key])
        for key in sections
        if key in context and context[key] not in (None, "", [], {})
    }


def _provider_function_name(tool_name: str) -> str:
    clean = "_".join(
        part
        for part in "".join(
            character if character.isalnum() else "_"
            for character in str(tool_name or "").strip()
        ).split("_")
        if part
    )
    if not clean:
        raise ValueError("Provider tool name cannot be empty.")
    return clean


def _provider_tool_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    parameters = schema.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError(f"Provider tool {schema.get('name')!r} has no object schema.")
    if not isinstance(parameters.get("properties"), dict):
        raise ValueError(f"Provider tool {schema.get('name')!r} has no properties.")
    return _json_safe(parameters)


def _selected_fields(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in keys
        if key in value and value[key] not in (None, "", [], {})
    }


def _compact_profile_status(value: Any) -> dict[str, Any]:
    return _selected_fields(
        value,
        (
            "found",
            "geometry_count",
            "constraint_count",
            "degrees_of_freedom",
            "constraint_state",
            "fully_constrained",
            "under_constrained",
            "construction_geometry_count",
            "edge_count",
            "wire_count",
            "closed_wire_count",
            "open_wire_count",
            "closed_profile",
            "ready_for_closed_profile_feature",
            "ready_for_pad",
            "ready_for_pocket",
            "ready_for_revolve",
            "ready_for_loft_section",
            "ready_for_hole_centers",
            "ready_for_path",
            "ready_for_layout",
            "geometry_types",
            "face_build_errors",
            "conflicting_constraint_indices",
            "redundant_constraint_indices",
            "constraint_type_counts",
            "block_constraint_count",
            "reason",
        ),
    )


def _compact_active_sketch_state(
    value: Any,
    *,
    include_profile: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _selected_fields(
        value,
        (
            "found",
            "name",
            "label",
            "is_open",
            "owner_body",
            "map_mode",
            "support",
            "geometry_bounds",
        ),
    )
    if include_profile:
        profile = _compact_profile_status(value.get("profile_status"))
        if profile:
            result["profile_status"] = profile

    debt = _selected_fields(
        value.get("constraint_debt"),
        (
            "open_endpoint_count",
            "open_endpoints",
            "unconstrained_geometry_count",
            "unconstrained_geometry",
            "conflicting_constraint_indices",
            "redundant_constraint_indices",
            "native_degenerate_geometry_count",
            "visible_degenerate_geometry",
        ),
    )
    if debt:
        result["constraint_debt"] = debt

    junctions = value.get("junction_diagnostics")
    if isinstance(junctions, dict):
        compact_junctions = _selected_fields(
            junctions,
            (
                "junction_count",
                "non_tangent_junction_count",
                "tangent_tolerance_degrees",
                "near_tangent_tolerance_degrees",
            ),
        )
        if compact_junctions:
            result["junction_diagnostics"] = compact_junctions
    return result


def _provider_state_after_tool(
    context: dict[str, Any],
    tool_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cad_state = context.get("cad_state")
    sketch_open = bool(isinstance(cad_state, dict) and cad_state.get("active_sketch"))
    compact_cad_state = dict(cad_state) if isinstance(cad_state, dict) else {}
    if sketch_open:
        result_has_profile = bool(
            isinstance(tool_result, dict)
            and (
                isinstance(tool_result.get("profile_status"), dict)
                or isinstance(tool_result.get("sketch_snapshot"), dict)
            )
        )
        compact_cad_state["active_sketch"] = _compact_active_sketch_state(
            cad_state.get("active_sketch"),
            include_profile=not result_has_profile,
        )
    keys = ["workbench", "cad_state", "selection"]
    if not sketch_open:
        keys.append("document")
        domain_key = {
            "PartDesignWorkbench": "partdesign",
            "PartWorkbench": "part",
            "AssemblyWorkbench": "assembly",
            "SurfaceWorkbench": "surface",
            "DraftWorkbench": "draft",
            "TechDrawWorkbench": "techdraw",
            "CAMWorkbench": "cam",
            "FemWorkbench": "fem",
            "MaterialWorkbench": "material",
            "MeshWorkbench": "mesh",
            "SpreadsheetWorkbench": "spreadsheet",
        }.get(str(context.get("workbench") or ""))
        if domain_key:
            keys.append(domain_key)
    result = {
        key: _json_safe(context[key])
        for key in keys
        if key in context and context[key] not in (None, "", [], {})
    }
    if compact_cad_state:
        result["cad_state"] = _json_safe(compact_cad_state)
    return result


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("Provider payload dictionaries must use string keys.")
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Provider payload contains non-JSON value {type(value).__name__}.")


def _capture_outbound_request(
    context: dict[str, Any],
    *,
    provider: str,
    sdk_call: str,
    turn: int,
    request: dict[str, Any],
    base_url: str | None,
    attempt: int = 1,
) -> dict[str, Any] | None:
    config = context.get("_vibecad_debug")
    if not isinstance(config, dict) or not config.get("enabled"):
        return None
    directory = str(config.get("capture_directory") or "").strip()
    if not directory:
        raise RuntimeError(
            "Context debugging is enabled without a provider request capture directory."
        )
    return capture_provider_request(
        directory=directory,
        provider=provider,
        sdk_call=sdk_call,
        turn=turn,
        attempt=attempt,
        request=_json_safe(request),
        base_url=base_url,
    )


def _openai_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        if clear_inherited_modules:
            _clear_inherited_sdk_modules()
        from openai import OpenAI
    except Exception as exc:
        conn.send(
            {
                "type": "error",
                "error": (
                    f"OpenAI SDK is not available in the VibeCAD runtime. ({exc})"
                ),
            }
        )
        conn.close()
        return

    def tool_surface(
        live_context: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        definitions: list[dict[str, Any]] = []
        names: dict[str, str] = {}
        for index, schema in enumerate(live_context.get("provider_tool_schemas") or []):
            if not isinstance(schema, dict):
                raise ValueError(f"Provider tool schema {index} must be an object.")
            tool_name = str(schema.get("name") or "").strip()
            if not tool_name:
                raise ValueError(f"Provider tool schema {index} is missing name.")
            function_name = _provider_function_name(tool_name)
            if function_name in names:
                raise RuntimeError(f"Duplicate provider function name: {function_name}")
            names[function_name] = tool_name
            definitions.append(
                {
                    "type": "function",
                    "name": function_name,
                    "description": str(schema.get("description") or ""),
                    "parameters": _provider_tool_parameters(schema),
                    "strict": False,
                }
            )
        return definitions, names

    def user_input(text: str, live_context: dict[str, Any]) -> list[dict[str, Any]]:
        visible = _model_visible_context(live_context)
        blocks = _context_image_blocks(visible)
        notes = _context_image_delivery_notes(visible)
        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        for note in notes:
            content.append({"type": "input_text", "text": note})
        for label, mime_type, data in blocks:
            content.append({"type": "input_text", "text": label})
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{data}",
                    "detail": "high",
                }
            )
        return [{"role": "user", "content": content}]

    client_kwargs: dict[str, Any] = {
        "api_key": api_key or os.environ.get("OPENAI_API_KEY") or "vibecad-local",
        "max_retries": 2,
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    if timeout_seconds is not None and timeout_seconds > 0:
        client_kwargs["timeout"] = timeout_seconds
    client = OpenAI(**client_kwargs)
    live_context = dict(context)
    tools, function_to_tool = tool_surface(live_context)
    pending_input = user_input(prompt, live_context)
    previous_response_id: str | None = None
    turn_texts: list[str] = []
    try:
        turn = 1
        while max_turns is None or max_turns <= 0 or turn <= max_turns:
            request: dict[str, Any] = {
                "model": model,
                "instructions": _provider_instructions(live_context),
                "input": pending_input,
                "parallel_tool_calls": False,
                "stream": True,
            }
            if tools:
                request["tools"] = tools
                request["tool_choice"] = "auto"
            if previous_response_id:
                request["previous_response_id"] = previous_response_id
            if reasoning_effort:
                reasoning: dict[str, Any] = {"effort": reasoning_effort}
                if str(reasoning_effort).strip().lower() != "none":
                    reasoning["summary"] = "auto"
                request["reasoning"] = reasoning
            _capture_outbound_request(
                live_context,
                provider="openai",
                sdk_call="OpenAI.responses.create",
                turn=turn,
                request=request,
                base_url=base_url,
            )
            stream = client.responses.create(**request)
            text_parts: list[str] = []
            completed_response = None
            calls: list[Any] = []
            try:
                for event in stream:
                    event_type = str(getattr(event, "type", "") or "")
                    if event_type == "response.output_text.delta":
                        text = str(getattr(event, "delta", "") or "")
                        if not text:
                            continue
                        streamed_text = (
                            "\n\n" + text if not text_parts and turn_texts else text
                        )
                        text_parts.append(text)
                        _send_child_progress(
                            conn,
                            {
                                "event": "provider_text_delta",
                                "provider": "OpenAI",
                                "turn": turn,
                                "text": streamed_text,
                            },
                        )
                    elif event_type == "response.reasoning_summary_text.delta":
                        delta = str(getattr(event, "delta", "") or "")
                        if delta:
                            _send_child_progress(
                                conn,
                                {
                                    "event": "provider_reasoning_delta",
                                    "provider": "OpenAI",
                                    "turn": turn,
                                    "text": delta,
                                },
                            )
                    elif event_type == "response.output_item.done":
                        item = getattr(event, "item", None)
                        if getattr(item, "type", None) == "function_call":
                            calls.append(item)
                    elif event_type == "response.completed":
                        completed_response = getattr(event, "response", None)
                    elif event_type in {"response.failed", "response.incomplete"}:
                        failed_response = getattr(event, "response", None)
                        error = getattr(failed_response, "error", None)
                        raise RuntimeError(
                            f"OpenAI response did not complete: {error or event_type}"
                        )
            finally:
                close_stream = getattr(stream, "close", None)
                if callable(close_stream):
                    close_stream()
            if completed_response is None:
                raise RuntimeError(
                    "OpenAI Responses stream ended without response.completed."
                )
            assistant_text = str(
                getattr(completed_response, "output_text", "") or "".join(text_parts)
            )
            if assistant_text.strip():
                turn_texts.append(assistant_text.strip())
            if not calls:
                calls = [
                    item
                    for item in list(getattr(completed_response, "output", []) or [])
                    if getattr(item, "type", None) == "function_call"
                ]
            if not calls:
                conn.send(
                    {
                        "type": "done",
                        "final_output": "\n\n".join(turn_texts),
                        "raw": None,
                    }
                )
                return

            response_function_map = dict(function_to_tool)
            pending_input = []
            repin_context: dict[str, Any] | None = None
            for item in calls:
                function_name = str(getattr(item, "name", "") or "")
                call_id = str(getattr(item, "call_id", "") or "")
                arguments_json = str(getattr(item, "arguments", "") or "{}")
                if not call_id:
                    raise RuntimeError(
                        f"OpenAI function call {function_name!r} has no call_id."
                    )
                tool_name = response_function_map.get(function_name)
                if tool_name is None:
                    result: dict[str, Any] = {
                        "ok": False,
                        "error": f"Unknown VibeCAD operation: {function_name}",
                    }
                    updated_context = None
                else:
                    conn.send(
                        {
                            "type": "tool",
                            "tool_name": tool_name,
                            "arguments_json": arguments_json,
                        }
                    )
                    bridge = conn.recv()
                    if bridge.get("type") != "tool_result":
                        raise RuntimeError("Invalid VibeCAD tool bridge response.")
                    result = bridge.get("result")
                    if not isinstance(result, dict):
                        result = {
                            "ok": False,
                            "error": "Missing structured tool result.",
                        }
                    updated_context = bridge.get("context")
                    if isinstance(updated_context, dict):
                        live_context = updated_context
                        tools, function_to_tool = tool_surface(live_context)
                    if (
                        tool_name == "core.capture_view_screenshot"
                        and result.get("captured")
                        and result.get("new_observation", True)
                    ):
                        repin_context = live_context
                model_result = dict(result)
                model_result["vibecad_state_after"] = _provider_state_after_tool(
                    live_context,
                    result,
                )
                pending_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(
                            _json_safe(model_result), separators=(",", ":")
                        ),
                    }
                )
            if repin_context is not None:
                references = repin_context.get("reference_images")
                has_references = bool(
                    isinstance(references, dict) and references.get("images")
                )
                visual_instruction = (
                    "Compare the current viewport directly with the attached user references. "
                    "Name visible mismatches before deciding whether work is complete."
                    if has_references
                    else "Inspect the current viewport against the accepted design intent. "
                    "Name visible geometric or functional shortcomings before deciding whether work is complete."
                )
                pending_input.extend(user_input(visual_instruction, repin_context))
            previous_response_id = str(getattr(completed_response, "id", "") or "")
            if not previous_response_id:
                raise RuntimeError("OpenAI completed response has no response id.")
            turn += 1
        conn.send({"type": "error", "error": "OpenAI provider turn limit reached."})
    except Exception as exc:
        conn.send({"type": "error", "error": str(exc)})
    finally:
        conn.close()


def _provider_qt_modules() -> tuple[Any, Any] | None:
    try:
        from PySide import QtCore, QtGui
    except ImportError:
        return None
    return QtCore, QtGui


def _provider_image_mime_for_suffix(suffix: str) -> str | None:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(str(suffix or "").lower())


def _provider_encoded_image_payload(
    path: Path,
) -> tuple[str, bytes, dict[str, Any]] | None:
    """Encode an oversized image into a provider-safe payload.

    This is intentionally provider-local instead of importing Core's attachment
    helper: provider payload limits are runtime concerns and this module must
    stay importable in the child process without creating Core/Session cycles.
    """
    qt_modules = _provider_qt_modules()
    if qt_modules is None:
        return None
    qt_core, qt_gui = qt_modules
    image = qt_gui.QImage(str(path))
    if image.isNull():
        return None
    width = int(image.width())
    height = int(image.height())
    if width <= 0 or height <= 0:
        return None

    original_format = {
        ".png": "PNG",
        ".jpg": "JPG",
        ".jpeg": "JPG",
        ".webp": "WEBP",
    }.get(path.suffix.lower(), "PNG")
    attempts: list[tuple[str, str, int]] = [
        (
            original_format,
            _provider_image_mime_for_suffix(path.suffix) or "image/png",
            90,
        ),
    ]
    if original_format != "JPG":
        attempts.append(("JPG", "image/jpeg", 85))

    best: tuple[str, bytes, dict[str, Any]] | None = None
    long_edge = max(width, height)
    for encode_format, mime_type, starting_quality in attempts:
        edge = min(long_edge, PROVIDER_IMAGE_MAX_EDGE)
        quality = starting_quality
        for _attempt in range(10):
            scaled = image
            if max(width, height) > edge:
                scaled = image.scaled(
                    edge,
                    edge,
                    qt_core.Qt.KeepAspectRatio,
                    qt_core.Qt.SmoothTransformation,
                )
            buffer = qt_core.QBuffer()
            buffer.open(qt_core.QIODevice.WriteOnly)
            saved = scaled.save(buffer, encode_format, quality)
            payload = bytes(buffer.data())
            buffer.close()
            if saved and payload:
                metadata = {
                    "resized": True,
                    "encoded_format": encode_format.lower(),
                    "image_size": [int(scaled.width()), int(scaled.height())],
                    "size_bytes": len(payload),
                }
                candidate = (mime_type, payload, metadata)
                if best is None or len(payload) < len(best[1]):
                    best = candidate
                if len(payload) <= MAX_PROVIDER_IMAGE_BYTES:
                    return candidate
            if encode_format in {"JPG", "WEBP"} and quality > 40:
                quality -= 15
            elif edge > PROVIDER_IMAGE_MIN_EDGE:
                edge = max(PROVIDER_IMAGE_MIN_EDGE, int(edge * 0.75))
            else:
                break
    if best is not None and len(best[1]) <= MAX_PROVIDER_IMAGE_BYTES:
        return best
    return None


def _image_file_payload(path_text: Any) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) for an image file, or None if unusable."""
    payload = _image_file_payload_with_status(path_text)
    if not payload.get("available"):
        return None
    return str(payload["mime_type"]), str(payload["data"])


def _image_file_payload_with_status(path_text: Any) -> dict[str, Any]:
    """Return provider payload data plus explicit delivery status."""
    if not path_text:
        return {"available": False, "reason": "empty image path"}
    try:
        path = Path(str(path_text))
        if not path.is_file():
            return {"available": False, "reason": f"image file not found: {path}"}
        size = path.stat().st_size
        if size <= 0:
            return {"available": False, "reason": "image file is empty"}
        suffix = path.suffix.lower()
        mime_type = _provider_image_mime_for_suffix(suffix)
        if mime_type is None:
            return {
                "available": False,
                "reason": f"unsupported image type: {suffix or path.name}",
            }
        if size <= MAX_PROVIDER_IMAGE_BYTES:
            return {
                "available": True,
                "mime_type": mime_type,
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                "resized": False,
                "size_bytes": size,
            }
        encoded = _provider_encoded_image_payload(path)
        if encoded is None:
            return {
                "available": False,
                "reason": (
                    f"image is {size} bytes and could not be resized below "
                    f"{MAX_PROVIDER_IMAGE_BYTES} bytes"
                ),
                "size_bytes": size,
            }
        encoded_mime, raw, metadata = encoded
        return {
            "available": True,
            "mime_type": encoded_mime,
            "data": base64.b64encode(raw).decode("ascii"),
            "resized": True,
            "source_size_bytes": size,
            **metadata,
        }
    except Exception as exc:
        return {"available": False, "reason": f"image payload failed: {exc}"}


def _screenshot_image_payload(context: dict[str, Any]) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) for the captured viewport screenshot."""
    screenshot = context.get("view_screenshot")
    if not isinstance(screenshot, dict) or not screenshot.get("captured"):
        return None
    return _image_file_payload(screenshot.get("path"))


def _context_image_blocks(context: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return labeled image payloads as (label_text, mime_type, base64_data)."""
    blocks: list[tuple[str, str, str]] = []
    references = context.get("reference_images")
    entries: list[dict[str, Any]] = []
    if isinstance(references, dict):
        raw_entries = references.get("images")
        if isinstance(raw_entries, list):
            entries = [entry for entry in raw_entries if isinstance(entry, dict)]
    usable: list[tuple[dict[str, Any], tuple[str, str]]] = []
    unavailable: list[dict[str, str]] = []
    for entry in entries:
        payload = _image_file_payload_with_status(entry.get("path"))
        entry["provider_delivery"] = {
            key: value
            for key, value in payload.items()
            if key not in {"data", "mime_type"}
        }
        if payload.get("available"):
            usable.append((entry, (str(payload["mime_type"]), str(payload["data"]))))
        else:
            unavailable.append(
                {
                    "name": str(entry.get("name") or entry.get("id") or "reference"),
                    "reason": str(payload.get("reason") or "image unavailable"),
                }
            )
    if unavailable and isinstance(references, dict):
        references["provider_delivery_notes"] = unavailable
    total = len(usable)
    for index, (entry, (mime_type, image_data)) in enumerate(usable, start=1):
        name = str(entry.get("name") or f"reference-{index}")
        user_label = str(entry.get("label") or "").strip()
        suffix = f"|{user_label}" if user_label else ""
        label_text = f"R{index}/{total}:{name}{suffix}"
        blocks.append((label_text, mime_type, image_data))
    screenshot_payload = _screenshot_image_payload(context)
    if screenshot_payload is not None:
        mime_type, image_data = screenshot_payload
        blocks.append(
            (
                "V:current",
                mime_type,
                image_data,
            )
        )
    return blocks


def _context_image_delivery_notes(context: dict[str, Any]) -> list[str]:
    references = context.get("reference_images")
    if not isinstance(references, dict):
        return []
    notes = references.get("provider_delivery_notes")
    if not isinstance(notes, list):
        return []
    lines: list[str] = []
    for item in notes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "reference")
        reason = str(item.get("reason") or "not delivered")
        lines.append(f"R_MISS:{name}|{reason}")
    return lines


def _anthropic_user_content(
    prompt: str, context: dict[str, Any]
) -> str | list[dict[str, Any]]:
    blocks = _context_image_blocks(context)
    delivery_notes = _context_image_delivery_notes(context)
    if not blocks and not delivery_notes:
        return prompt
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for note in delivery_notes:
        content.append({"type": "text", "text": note})
    for label_text, mime_type, image_data in blocks:
        content.append({"type": "text", "text": label_text})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": image_data,
                },
            }
        )
    return content


def _anthropic_visual_repin_content(
    context: dict[str, Any], screenshot_summary: dict[str, Any]
) -> list[dict[str, Any]]:
    if (
        not isinstance(screenshot_summary, dict)
        or not screenshot_summary.get("captured")
        or not screenshot_summary.get("new_observation", True)
    ):
        return []
    references = context.get("reference_images")
    has_references = bool(isinstance(references, dict) and references.get("images"))
    visual_context = {
        "view_screenshot": screenshot_summary,
    }
    if has_references:
        visual_context["reference_images"] = references
    blocks = _context_image_blocks(visual_context)
    if not blocks:
        return []
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Compare the current viewport directly with the attached user references. "
                "Name visible mismatches before deciding whether work is complete."
                if has_references
                else "Inspect the current viewport against the accepted design intent. "
                "Name visible geometric or functional shortcomings before deciding whether work is complete."
            ),
        }
    ]
    for label_text, mime_type, image_data in blocks:
        content.append({"type": "text", "text": label_text})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": image_data,
                },
            }
        )
    return content


def _anthropic_thinking_config(reasoning_effort: str | None) -> dict[str, Any] | None:
    if _anthropic_adaptive_effort(reasoning_effort) is None:
        return None
    return {"type": "adaptive"}


def _anthropic_adaptive_effort(reasoning_effort: str | None) -> str | None:
    """Map the user setting to Anthropic's adaptive-thinking effort literal."""
    if not reasoning_effort:
        return None
    return ANTHROPIC_ADAPTIVE_EFFORT.get(str(reasoning_effort).strip().lower())


def _anthropic_final_text(content_blocks: list[Any]) -> str:
    parts: list[str] = []
    for block in content_blocks:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type != "text":
            continue
        text = getattr(block, "text", None) or (
            block.get("text") if isinstance(block, dict) else None
        )
        if text:
            parts.append(str(text))
    return "\n\n".join(parts).strip()


def _anthropic_assistant_request_content(
    content_blocks: list[Any],
) -> list[dict[str, Any]]:
    request_blocks: list[dict[str, Any]] = []
    for block in content_blocks:
        block_type = _anthropic_block_type(block)
        if block_type == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            request_blocks.append({"type": "text", "text": str(text or "")})
            continue
        if block_type == "thinking":
            thinking = getattr(block, "thinking", None) or (
                block.get("thinking") if isinstance(block, dict) else None
            )
            signature = getattr(block, "signature", None) or (
                block.get("signature") if isinstance(block, dict) else None
            )
            item = {"type": "thinking", "thinking": str(thinking or "")}
            if signature:
                item["signature"] = str(signature)
            request_blocks.append(item)
            continue
        if block_type == "redacted_thinking":
            data = getattr(block, "data", None) or (
                block.get("data") if isinstance(block, dict) else None
            )
            item = {"type": "redacted_thinking"}
            if data:
                item["data"] = str(data)
            request_blocks.append(item)
            continue
        if block_type == "tool_use":
            block_id = getattr(block, "id", None) or (
                block.get("id") if isinstance(block, dict) else None
            )
            name = getattr(block, "name", None) or (
                block.get("name") if isinstance(block, dict) else None
            )
            tool_input = getattr(block, "input", None)
            if tool_input is None and isinstance(block, dict):
                tool_input = block.get("input")
            request_blocks.append(
                {
                    "type": "tool_use",
                    "id": str(block_id or ""),
                    "name": str(name or ""),
                    "input": _json_safe(tool_input or {}),
                }
            )
            continue
        if isinstance(block, dict):
            request_blocks.append(_json_safe(block))
    return request_blocks


def _anthropic_block_type(block: Any) -> str:
    block_type = getattr(block, "type", None) or (
        block.get("type") if isinstance(block, dict) else None
    )
    return str(block_type or "unknown")


def _anthropic_response_summary(response: Any) -> dict[str, Any]:
    blocks = list(getattr(response, "content", []) or [])
    counts: dict[str, int] = {}
    text_chars = 0
    thinking_chars = 0
    tool_names: list[str] = []
    for block in blocks:
        block_type = _anthropic_block_type(block)
        counts[block_type] = counts.get(block_type, 0) + 1
        if block_type == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            if text:
                text_chars += len(str(text))
        elif block_type == "thinking":
            thinking = getattr(block, "thinking", None) or (
                block.get("thinking") if isinstance(block, dict) else None
            )
            if thinking:
                thinking_chars += len(str(thinking))
        elif block_type == "tool_use":
            name = getattr(block, "name", None) or (
                block.get("name") if isinstance(block, dict) else None
            )
            if name:
                tool_names.append(str(name))
    return {
        "stop_reason": str(getattr(response, "stop_reason", "") or ""),
        "block_counts": counts,
        "text_chars": text_chars,
        "thinking_chars": thinking_chars,
        "tool_names": tool_names[:8],
        "tool_name_count": len(tool_names),
    }


def _anthropic_stream_event_summary(event: Any) -> dict[str, Any]:
    event_type = getattr(event, "type", None) or (
        event.get("type") if isinstance(event, dict) else None
    )
    summary: dict[str, Any] = {"stream_event_type": str(event_type or "unknown")}
    block = getattr(event, "content_block", None) or (
        event.get("content_block") if isinstance(event, dict) else None
    )
    if block is not None:
        summary["block_type"] = _anthropic_block_type(block)
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        if name:
            summary["tool_name"] = str(name)
    delta = getattr(event, "delta", None) or (
        event.get("delta") if isinstance(event, dict) else None
    )
    if delta is not None:
        delta_type = getattr(delta, "type", None) or (
            delta.get("type") if isinstance(delta, dict) else None
        )
        if delta_type:
            summary["delta_type"] = str(delta_type)
        stop_reason = getattr(delta, "stop_reason", None) or (
            delta.get("stop_reason") if isinstance(delta, dict) else None
        )
        if stop_reason:
            summary["stop_reason"] = str(stop_reason)
        text = getattr(delta, "text", None) or (
            delta.get("text") if isinstance(delta, dict) else None
        )
        if text and str(delta_type or "") == "text_delta":
            summary["text_delta"] = str(text)
        thinking = getattr(delta, "thinking", None) or (
            delta.get("thinking") if isinstance(delta, dict) else None
        )
        if thinking and str(delta_type or "") == "thinking_delta":
            summary["reasoning_delta"] = str(thinking)
    return summary


def _short_provider_error(exc: BaseException, limit: int = 180) -> str:
    text = " ".join(str(exc or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _is_retryable_anthropic_stream_error(
    exc: BaseException,
    anthropic_module: Any | None = None,
) -> bool:
    if anthropic_module is not None:
        for name in ("APIConnectionError", "APITimeoutError"):
            error_type = getattr(anthropic_module, name, None)
            if error_type is not None and isinstance(exc, error_type):
                return True
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 6:
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = " | ".join(f"{item.__class__.__name__}: {item}" for item in chain).lower()
    retry_tokens = (
        "api connection",
        "api timeout",
        "broken pipe",
        "connection aborted",
        "connection reset",
        "connection timed out",
        "incomplete chunked read",
        "peer closed connection",
        "readerror",
        "read error",
        "readtimeout",
        "remoteprotocolerror",
        "server disconnected",
    )
    return any(token in text for token in retry_tokens)


def _anthropic_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        if clear_inherited_modules:
            _clear_inherited_sdk_modules()
        import anthropic
    except Exception as exc:
        conn.send(
            {
                "type": "error",
                "error": (
                    "Anthropic SDK is not available. Install the optional "
                    f"'anthropic' package and configure authentication. ({exc})"
                ),
            }
        )
        conn.close()
        return

    try:
        live_context = dict(context)

        def build_tool_surface(
            surface_context: dict[str, Any],
        ) -> tuple[dict[str, str], list[dict[str, Any]]]:
            by_name: dict[str, str] = {}
            definitions: list[dict[str, Any]] = []
            for index, schema in enumerate(
                surface_context.get("provider_tool_schemas") or []
            ):
                if not isinstance(schema, dict):
                    raise ValueError(f"Provider tool schema {index} must be an object.")
                tool_name = str(schema.get("name") or "").strip()
                if not tool_name:
                    raise ValueError(f"Provider tool schema {index} is missing name.")
                function_name = _provider_function_name(tool_name)
                if function_name in by_name:
                    raise ValueError(
                        f"Duplicate provider function name: {function_name}"
                    )
                by_name[function_name] = tool_name
                definitions.append(
                    {
                        "name": function_name,
                        "description": str(schema.get("description") or ""),
                        "input_schema": _provider_tool_parameters(schema),
                    }
                )
            if definitions:
                definitions[-1]["cache_control"] = {"type": "ephemeral"}
            return by_name, definitions

        tools_by_name, tool_definitions = build_tool_surface(live_context)
        turn_texts: list[str] = []

        thinking = _anthropic_thinking_config(reasoning_effort)
        max_tokens = DEFAULT_ANTHROPIC_MAX_TOKENS
        if thinking is not None:
            max_tokens += int(
                ANTHROPIC_THINKING_BUDGETS[str(reasoning_effort).strip().lower()]
            )

        system_blocks = _anthropic_system_blocks(live_context)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": _anthropic_user_content(
                    prompt, _model_visible_context(live_context)
                ),
            }
        ]

        client_kwargs: dict[str, Any] = {"max_retries": 2}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        if timeout_seconds is not None and timeout_seconds > 0:
            client_kwargs["timeout"] = timeout_seconds
        client = anthropic.Anthropic(**client_kwargs)

        request_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "tools": tool_definitions,
        }
        if thinking is not None:
            request_kwargs["thinking"] = thinking
            request_kwargs["output_config"] = {
                "effort": _anthropic_adaptive_effort(reasoning_effort)
            }

        def _stream_response(turn: int, attempt: int) -> Any:
            # The SDK rejects non-streaming requests that could exceed ten
            # minutes (large max_tokens plus thinking budgets), so always
            # stream and accumulate the final message.
            system_blocks = _anthropic_system_blocks(live_context)
            sdk_request = {
                "messages": messages,
                **request_kwargs,
                "system": system_blocks,
            }
            _capture_outbound_request(
                live_context,
                provider="anthropic",
                sdk_call="Anthropic.messages.stream",
                turn=turn,
                attempt=attempt,
                request=sdk_request,
                base_url=base_url,
            )
            _send_child_progress(
                conn,
                {
                    "event": "anthropic_request_started",
                    "turn": turn,
                    "attempt": attempt,
                    "model": model,
                    "message_count": len(messages),
                    "tool_count": len(tool_definitions),
                    "max_tokens": max_tokens,
                    "thinking": request_kwargs.get("thinking"),
                    "output_config": request_kwargs.get("output_config"),
                },
            )
            with client.messages.stream(**sdk_request) as stream:
                event_count = 0
                last_delta_notice_at = 0.0
                text_delta_started = False
                try:
                    iterator = iter(stream)
                except TypeError:
                    _send_child_progress(
                        conn,
                        {
                            "event": "anthropic_stream_waiting",
                            "turn": turn,
                        },
                    )
                    return stream.get_final_message()
                for stream_event in iterator:
                    event_count += 1
                    summary = _anthropic_stream_event_summary(stream_event)
                    stream_event_type = summary.get("stream_event_type")
                    delta_type = summary.get("delta_type")
                    text_delta = summary.get("text_delta")
                    if text_delta:
                        streamed_text = (
                            "\n\n" + str(text_delta)
                            if not text_delta_started and turn_texts
                            else str(text_delta)
                        )
                        text_delta_started = True
                        _send_child_progress(
                            conn,
                            {
                                "event": "provider_text_delta",
                                "provider": "Anthropic",
                                "turn": turn,
                                "text": streamed_text,
                            },
                        )
                    reasoning_delta = summary.get("reasoning_delta")
                    if reasoning_delta:
                        _send_child_progress(
                            conn,
                            {
                                "event": "provider_reasoning_delta",
                                "provider": "Anthropic",
                                "turn": turn,
                                "text": reasoning_delta,
                            },
                        )
                    now = time.monotonic()
                    should_report = stream_event_type in {
                        "message_start",
                        "content_block_start",
                        "content_block_stop",
                        "message_delta",
                        "message_stop",
                    }
                    if (
                        not should_report
                        and delta_type
                        and now - last_delta_notice_at >= 5.0
                    ):
                        should_report = True
                        last_delta_notice_at = now
                    if should_report:
                        event = {
                            "event": "anthropic_stream_event",
                            "turn": turn,
                            "event_count": event_count,
                        }
                        event.update(summary)
                        _send_child_progress(conn, event)
                _send_child_progress(
                    conn,
                    {
                        "event": "anthropic_stream_completed",
                        "turn": turn,
                        "event_count": event_count,
                    },
                )
                return stream.get_final_message()

        def _stream_response_with_retries(turn: int) -> Any:
            for attempt in range(1, ANTHROPIC_STREAM_MAX_ATTEMPTS + 1):
                try:
                    return _stream_response(turn, attempt)
                except anthropic.BadRequestError:
                    raise
                except Exception as exc:
                    if (
                        attempt >= ANTHROPIC_STREAM_MAX_ATTEMPTS
                        or not _is_retryable_anthropic_stream_error(exc, anthropic)
                    ):
                        raise
                    _send_child_progress(
                        conn,
                        {
                            "event": "anthropic_stream_retrying",
                            "turn": turn,
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "max_attempts": ANTHROPIC_STREAM_MAX_ATTEMPTS,
                            "error": _short_provider_error(exc),
                        },
                    )
                    time.sleep(min(2.0, 0.25 * attempt))
            raise RuntimeError("Anthropic stream retry loop exited unexpectedly.")

        turn = 1
        while max_turns is None or max_turns <= 0 or turn <= max_turns:
            response = _stream_response_with_retries(turn)
            content_blocks = list(response.content)
            response_text = _anthropic_final_text(content_blocks)
            if response_text:
                turn_texts.append(response_text)
            _send_child_progress(
                conn,
                {
                    "event": "anthropic_response_received",
                    "turn": turn,
                    **_anthropic_response_summary(response),
                },
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": _anthropic_assistant_request_content(content_blocks),
                }
            )
            tool_use_blocks = [
                block
                for block in content_blocks
                if getattr(block, "type", None) == "tool_use"
            ]
            if response.stop_reason != "tool_use" or not tool_use_blocks:
                conn.send(
                    {
                        "type": "done",
                        "final_output": "\n\n".join(turn_texts),
                        "raw": None,
                    }
                )
                return
            tool_results: list[dict[str, Any]] = []
            visual_repin_blocks: list[dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_name = tools_by_name.get(block.name)
                updated_context = None
                if tool_name is None:
                    result: Any = {
                        "ok": False,
                        "error": f"Unknown VibeCAD tool: {block.name}",
                    }
                else:
                    arguments_json = json.dumps(_json_safe(block.input or {}))
                    conn.send(
                        {
                            "type": "tool",
                            "tool_name": tool_name,
                            "arguments_json": arguments_json,
                        }
                    )
                    bridge = conn.recv()
                    if bridge.get("type") != "tool_result":
                        raise RuntimeError("Invalid VibeCAD tool bridge response.")
                    result = bridge.get("result")
                    if not isinstance(result, dict):
                        result = {
                            "ok": False,
                            "error": "VibeCAD tool returned no structured result.",
                        }
                    updated_context = bridge.get("context")
                if isinstance(updated_context, dict):
                    live_context = updated_context
                    tools_by_name, tool_definitions = build_tool_surface(live_context)
                    request_kwargs["tools"] = tool_definitions
                if isinstance(result, dict):
                    result["vibecad_state_after"] = _provider_state_after_tool(
                        live_context,
                        result,
                    )
                if tool_name == "core.capture_view_screenshot":
                    screenshot_summary = (
                        result.get("result")
                        if isinstance(result, dict)
                        and isinstance(result.get("result"), dict)
                        else result
                    )
                    if isinstance(screenshot_summary, dict):
                        visual_repin_blocks.extend(
                            _anthropic_visual_repin_content(
                                live_context, screenshot_summary
                            )
                        )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(_json_safe(result)),
                    }
                )
            messages.append(
                {"role": "user", "content": [*tool_results, *visual_repin_blocks]}
            )
            turn += 1
        conn.send(
            {
                "type": "error",
                "error": "Anthropic provider turn limit reached.",
            }
        )
    except Exception as exc:
        conn.send({"type": "error", "error": str(exc)})
    finally:
        conn.close()


def _clear_inherited_sdk_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "pydantic"
            or name.startswith("pydantic.")
            or name == "openai"
            or name.startswith("openai.")
            or name == "anthropic"
            or name.startswith("anthropic.")
            or name == "httpx"
            or name.startswith("httpx.")
        ):
            sys.modules.pop(name, None)
