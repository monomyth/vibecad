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


MAX_PROVIDER_IMAGE_BYTES = 2_000_000
PROVIDER_IMAGE_MAX_EDGE = 1568
PROVIDER_IMAGE_MIN_EDGE = 512
OPENAI_REQUEST_DUMP_DIR_ENV = "VIBECAD_OPENAI_REQUEST_DUMP_DIR"
ANTHROPIC_REQUEST_DUMP_DIR_ENV = "VIBECAD_ANTHROPIC_REQUEST_DUMP_DIR"
DEFAULT_ANTHROPIC_MAX_TOKENS = 8192
ANTHROPIC_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
}
ANTHROPIC_ADAPTIVE_EFFORT = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
}
ANTHROPIC_STREAM_MAX_ATTEMPTS = 3


def _vibecad_home() -> Path:
    configured = str(os.environ.get("VIBECAD_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        return Path.home() / ".vibecad"
    except Exception:
        return Path.cwd() / ".vibecad"


DEFAULT_OPENAI_REQUEST_DUMP_DIR = _vibecad_home() / "debug" / "openai-request-dumps"
DEFAULT_ANTHROPIC_REQUEST_DUMP_DIR = (
    _vibecad_home() / "debug" / "anthropic-request-dumps"
)


VIBECAD_SYSTEM_INSTRUCTIONS = (
    "VibeCAD mechanical CAD engineer. Build editable manufacturable native "
    "geometry matching intent, refs, active model. Preserve existing work unless "
    "replacement is requested. Use dimensions, datums, constraints, clearances, "
    "fits, load paths. Choose by form: pad/pocket prismatic; revolve axial; "
    "loft/sweep/surface curved; helix threads; pattern repeats; joints "
    "assemblies. No placeholders, primitive stand-ins, cosmetic substitutes. "
    "Curves must be arcs/splines/lofts/sweeps; fillets/chamfers finish edges. "
    "After each write verify target, errors, DoF, geometry types, topology, "
    "dimensions; repair before more writes."
)


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


class OpenAIAgentsProvider(BaseProvider):
    """OpenAI Agents SDK adapter.

    The official quickstart pattern is Agent + Runner.run + function tools.
    This adapter keeps that dependency optional so FreeCAD can start without the
    SDK installed.
    """

    def __init__(
        self,
        model: str = "gpt-5.5",
        api_key: str | None = None,
        reasoning_effort: str = "high",
        timeout_seconds: float | None = None,
        max_turns: int | None = 80,
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
            return _run_agents_subprocess(
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
                    f"OpenAI Agents provider timed out after {self.timeout_seconds:g} seconds."
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
        max_turns: int | None = 80,
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
            return _run_agents_subprocess(
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


@contextmanager
def _temporary_openai_env(api_key: str | None, base_url: str | None = None):
    """Temporarily set OpenAI SDK environment overrides, restoring them after.

    The Agents SDK constructs its own OpenAI client internally, so the API key
    and any custom endpoint must be delivered through the standard
    ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` environment variables.
    """
    overrides = {
        name: value
        for name, value in (
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_BASE_URL", base_url),
        )
        if value
    }
    if not overrides:
        yield
        return
    originals = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for name, original in originals.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original


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
        candidates.extend(current_executable.with_name(name) for name in executable_names)
    elif current_executable.name:
        candidates.extend(current_executable.with_name(name) for name in executable_names)

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
    result = _run_agents_subprocess(
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
            "Expected provider subprocess smoke to use pythonw.exe, "
            f"got {executable!r}"
        )


def _run_agents_subprocess(
    *,
    prompt: str,
    context: dict[str, Any],
    tool_runner: ToolRunner | None,
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None = 80,
    base_url: str | None = None,
    clear_inherited_modules: bool = True,
    event_pump: Callable[[], None] | None = None,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    child_main: Callable[..., None] | None = None,
    provider_label: str = "OpenAI Agents provider",
    prefer_windowless_python: bool | None = None,
) -> ProviderResult:
    multiprocessing_context = _provider_multiprocessing_context(
        prefer_windowless_python=prefer_windowless_python
    )
    reasoning_effort = _provider_reasoning_effort(reasoning_effort)
    parent_conn, child_conn = multiprocessing_context.Pipe()
    process = multiprocessing_context.Process(
        target=child_main or _agents_child_main,
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
                    parent_conn.send({"type": "tool_result", "result": result})
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
    try:
        from PySide import QtCore, QtWidgets
    except Exception:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    try:
        app.processEvents(QtCore.QEventLoop.AllEvents, 10)
    except TypeError:
        app.processEvents()


def _emit_provider_progress(
    progress_callback: ProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(dict(event))
    except Exception:
        return


def _send_child_progress(conn: Any, event: dict[str, Any]) -> None:
    try:
        conn.send({"type": "progress", "event": _json_safe(event)})
    except Exception:
        pass


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


def _model_visible_context(
    context: dict[str, Any],
    arguments: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    from provider_tools.core_get_current_freecad_context import (
        _model_visible_context as visible,
    )

    return visible(context, arguments)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return repr(value)


def _provider_tool_request_schema(tool: Any) -> dict[str, Any]:
    return {
        "function_name": getattr(tool, "name", None) or getattr(tool, "__name__", ""),
        "description": getattr(tool, "description", None)
        or getattr(tool, "__doc__", None)
        or "",
        "params_json_schema": _json_safe(getattr(tool, "params_json_schema", None)),
        "strict_json_schema": getattr(tool, "strict_json_schema", None),
        "callable": bool(getattr(tool, "on_invoke_tool", None) or callable(tool)),
    }


def _openai_request_dump_dir() -> Path | None:
    configured = os.environ.get(OPENAI_REQUEST_DUMP_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return _vibecad_home() / "debug" / "openai-request-dumps"


def _write_openai_request_dump(payload: dict[str, Any]) -> str | None:
    dump_dir = _openai_request_dump_dir()
    dump_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_json_safe(payload), indent=2, sort_keys=True)
    timestamped = (
        dump_dir / f"openai-request-{int(time.time() * 1000)}-{os.getpid()}.json"
    )
    latest = dump_dir / "latest-openai-request.json"
    timestamped.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return str(timestamped)


def _build_provider_function_tools(
    context: dict[str, Any],
    conn: Any,
    FunctionTool: Any,
) -> list[Any]:
    from provider_tools import create_tool

    tools = []
    for schema in context.get("provider_tool_schemas", []) or []:
        if not isinstance(schema, dict) or not schema.get("name"):
            continue
        tool_name = str(schema["name"])
        tool = create_tool(schema, conn, FunctionTool)
        tools.append(tool)
    context.pop("provider_tool_schemas", None)
    return tools


def _build_context_function_tool(context: dict[str, Any], FunctionTool: Any) -> Any:
    from provider_tools import create_context_tool

    schema = {
        "name": "core.get_current_freecad_context",
        "description": "state",
        "parameters": {
            "type": "object",
            "properties": {
                "object_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "max_objects": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
        },
        "workbench": "global",
        "safety": "read",
    }
    tool = create_context_tool(schema, context, FunctionTool)
    return tool


def _agents_child_main(
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
        os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "true")
        os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "true")
        os.environ.setdefault("OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA", "false")
        import asyncio
        from agents import Agent, FunctionTool, RunConfig, Runner

        try:
            from agents import ModelSettings
        except Exception:
            ModelSettings = None
        try:
            from openai.types.shared import Reasoning
        except Exception:
            Reasoning = None
    except Exception as exc:
        conn.send(
            {
                "type": "error",
                "error": (
                    "OpenAI Agents SDK is not available. Install the optional "
                    f"'agents' package and configure authentication. ({exc})"
                ),
            }
        )
        conn.close()
        return

    model_settings = None
    if reasoning_effort and ModelSettings is not None:
        reasoning = (
            Reasoning(effort=reasoning_effort)
            if Reasoning is not None
            else {"effort": reasoning_effort}
        )
        model_settings = ModelSettings(reasoning=reasoning)

    provider_function_tools = _build_provider_function_tools(
        context, conn, FunctionTool
    )
    context_function_tool = _build_context_function_tool(context, FunctionTool)
    agent_kwargs = {
        "name": "VibeCAD",
        "instructions": VIBECAD_SYSTEM_INSTRUCTIONS,
        "model": model,
        "tools": [
            context_function_tool,
            *provider_function_tools,
        ],
    }
    if model_settings is not None:
        agent_kwargs["model_settings"] = model_settings
    agent = Agent(**agent_kwargs)

    async def _run() -> Any:
        agent_input = _agents_input_from_context(
            prompt, _model_visible_context(context)
        )
        _write_openai_request_dump(
            {
                "schema": "vibecad-openai-agents-request-v1",
                "created_at_unix": time.time(),
                "model": model,
                "base_url": base_url,
                "reasoning_effort": reasoning_effort,
                "max_turns": max_turns,
                "timeout_seconds": timeout_seconds,
                "agent": {
                    "name": agent_kwargs["name"],
                    "instructions": agent_kwargs["instructions"],
                    "tools": [
                        _provider_tool_request_schema(tool)
                        for tool in agent_kwargs["tools"]
                    ],
                    "model_settings": _json_safe(model_settings),
                },
                "run": {
                    "input": agent_input,
                    "run_config": {"tracing_disabled": True},
                    "max_turns": max_turns,
                },
                "model_visible_context": _model_visible_context(context),
            }
        )
        run_task = Runner.run(
            agent,
            agent_input,
            run_config=RunConfig(tracing_disabled=True),
            max_turns=max_turns,
        )
        if timeout_seconds is not None and timeout_seconds > 0:
            return await asyncio.wait_for(run_task, timeout=timeout_seconds)
        return await run_task

    try:
        with _temporary_openai_env(api_key, base_url):
            result = asyncio.run(_run())
        conn.send({"type": "done", "final_output": result.final_output, "raw": None})
    except TimeoutError:
        timeout_text = (
            f"{timeout_seconds:g} seconds"
            if timeout_seconds is not None and timeout_seconds > 0
            else "the configured deadline"
        )
        conn.send(
            {
                "type": "error",
                "error": f"OpenAI Agents provider timed out after {timeout_text}.",
            }
        )
    except Exception as exc:
        conn.send({"type": "error", "error": str(exc)})
    finally:
        conn.close()


def _provider_qt_modules() -> tuple[Any, Any] | None:
    try:
        from PySide import QtCore, QtGui

        return QtCore, QtGui
    except Exception:
        try:
            from PySide6 import QtCore, QtGui

            return QtCore, QtGui
        except Exception:
            return None


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


def _agents_input_from_context(
    prompt: str, context: dict[str, Any]
) -> str | list[dict[str, Any]]:
    blocks = _context_image_blocks(context)
    delivery_notes = _context_image_delivery_notes(context)
    if not blocks and not delivery_notes:
        return prompt
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for note in delivery_notes:
        content.append({"type": "input_text", "text": note})
    for label_text, mime_type, image_data in blocks:
        content.append({"type": "input_text", "text": label_text})
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{image_data}",
                "detail": "auto",
            }
        )
    return [
        {
            "role": "user",
            "content": content,
        }
    ]


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
    if not isinstance(screenshot_summary, dict) or not screenshot_summary.get("captured"):
        return []
    references = context.get("reference_images")
    if not isinstance(references, dict) or not references.get("images"):
        return []
    visual_context = {
        "reference_images": references,
        "view_screenshot": screenshot_summary,
    }
    blocks = _context_image_blocks(visual_context)
    if not blocks:
        return []
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "R vs V.",
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


def _anthropic_request_dump_dir() -> Path | None:
    configured = os.environ.get(ANTHROPIC_REQUEST_DUMP_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_ANTHROPIC_REQUEST_DUMP_DIR


def _write_anthropic_request_dump(payload: dict[str, Any]) -> str | None:
    dump_dir = _anthropic_request_dump_dir()
    dump_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_json_safe(payload), indent=2, sort_keys=True)
    timestamped = (
        dump_dir / f"anthropic-request-{int(time.time() * 1000)}-{os.getpid()}.json"
    )
    latest = dump_dir / "latest-anthropic-request.json"
    timestamped.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return str(timestamped)


class _AnthropicFunctionTool:
    """Minimal FunctionTool stand-in so provider_tools factories work unchanged."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        params_json_schema: dict[str, Any],
        on_invoke_tool: Any,
        strict_json_schema: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.params_json_schema = params_json_schema
        self.on_invoke_tool = on_invoke_tool
        self.strict_json_schema = strict_json_schema


def _anthropic_tool_definition(tool: Any) -> dict[str, Any]:
    """Convert a provider function tool to the Anthropic Messages tool shape."""
    input_schema = _json_safe(getattr(tool, "params_json_schema", None))
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}
    return {
        "name": str(getattr(tool, "name", "")),
        "description": str(getattr(tool, "description", "")),
        "input_schema": input_schema,
    }


def _anthropic_thinking_config(reasoning_effort: str | None) -> dict[str, Any] | None:
    if not reasoning_effort:
        return None
    budget = ANTHROPIC_THINKING_BUDGETS.get(str(reasoning_effort).strip().lower())
    if budget is None:
        return None
    return {"type": "enabled", "budget_tokens": budget}


def _anthropic_adaptive_effort(reasoning_effort: str | None) -> str | None:
    """Map VibeCAD reasoning effort to the adaptive-thinking effort literal.

    Newer Anthropic models reject ``thinking.type: enabled`` and require
    ``thinking.type: adaptive`` with ``output_config.effort`` instead.
    """
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


def _anthropic_assistant_request_content(content_blocks: list[Any]) -> list[dict[str, Any]]:
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
    text = " | ".join(
        f"{item.__class__.__name__}: {item}" for item in chain
    ).lower()
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


def _anthropic_request_debug_payload(
    *,
    model: str,
    reasoning_effort: str | None,
    thinking: dict[str, Any] | None,
    max_tokens: int,
    max_turns: int | None,
    timeout_seconds: float | None,
    system_blocks: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    context: dict[str, Any],
    turn: int,
    base_url: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "vibecad-anthropic-request-v1",
        "created_at_unix": time.time(),
        "turn": turn,
        "model": model,
        "base_url": base_url,
        "reasoning_effort": reasoning_effort,
        "thinking": thinking,
        "max_tokens": max_tokens,
        "max_turns": max_turns,
        "timeout_seconds": timeout_seconds,
        "system": system_blocks,
        "tools": tool_definitions,
        "messages": messages,
        "model_visible_context": _model_visible_context(context),
    }


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
        import asyncio

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
        provider_function_tools = _build_provider_function_tools(
            context, conn, _AnthropicFunctionTool
        )
        context_function_tool = _build_context_function_tool(
            context, _AnthropicFunctionTool
        )
        all_tools = [context_function_tool, *provider_function_tools]
        tools_by_name = {tool.name: tool for tool in all_tools}
        tool_definitions = [_anthropic_tool_definition(tool) for tool in all_tools]
        if tool_definitions:
            tool_definitions[-1]["cache_control"] = {"type": "ephemeral"}

        thinking = _anthropic_thinking_config(reasoning_effort)
        max_tokens = DEFAULT_ANTHROPIC_MAX_TOKENS
        if thinking is not None:
            max_tokens += int(thinking["budget_tokens"])

        system_blocks = [
            {
                "type": "text",
                "text": VIBECAD_SYSTEM_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": _anthropic_user_content(
                    prompt, _model_visible_context(context)
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

        latest_dump = _anthropic_request_dump_dir() / "latest-anthropic-request.json"

        def _dump_request(turn: int) -> str | None:
            path = _write_anthropic_request_dump(
                _anthropic_request_debug_payload(
                    model=model,
                    base_url=base_url,
                    reasoning_effort=reasoning_effort,
                    thinking=request_kwargs.get("thinking"),
                    max_tokens=max_tokens,
                    max_turns=max_turns,
                    timeout_seconds=timeout_seconds,
                    system_blocks=system_blocks,
                    tool_definitions=tool_definitions,
                    messages=messages,
                    context=context,
                    turn=turn,
                )
            )
            _send_child_progress(
                conn,
                {
                    "event": "anthropic_request_dumped",
                    "turn": turn,
                    "dump_path": path,
                    "latest_dump_path": str(latest_dump),
                },
            )
            return path

        def _stream_response(turn: int) -> Any:
            # The SDK rejects non-streaming requests that could exceed ten
            # minutes (large max_tokens plus thinking budgets), so always
            # stream and accumulate the final message.
            _send_child_progress(
                conn,
                {
                    "event": "anthropic_request_started",
                    "turn": turn,
                    "model": model,
                    "message_count": len(messages),
                    "tool_count": len(tool_definitions),
                    "max_tokens": max_tokens,
                    "thinking": request_kwargs.get("thinking"),
                    "output_config": request_kwargs.get("output_config"),
                },
            )
            with client.messages.stream(
                messages=messages, **request_kwargs
            ) as stream:
                event_count = 0
                last_delta_notice_at = 0.0
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
                        _send_child_progress(
                            conn,
                            {
                                "event": "provider_text_delta",
                                "provider": "Anthropic",
                                "turn": turn,
                                "text": text_delta,
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
                    return _stream_response(turn)
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

        turn_limit = max_turns if max_turns is not None and max_turns > 0 else 80
        loop = asyncio.new_event_loop()
        try:
            for turn in range(1, turn_limit + 1):
                _dump_request(turn)
                try:
                    response = _stream_response_with_retries(turn)
                except anthropic.BadRequestError as exc:
                    if (
                        "thinking.type.enabled" not in str(exc)
                        or "thinking" not in request_kwargs
                    ):
                        raise
                    # Newer models require adaptive thinking + output_config
                    # effort instead of an explicit token budget.
                    request_kwargs["thinking"] = {"type": "adaptive"}
                    effort = _anthropic_adaptive_effort(reasoning_effort)
                    if effort:
                        request_kwargs["output_config"] = {"effort": effort}
                    _send_child_progress(
                        conn,
                        {
                            "event": "anthropic_request_retried",
                            "turn": turn,
                            "reason": "adaptive_thinking_required",
                            "thinking": request_kwargs.get("thinking"),
                            "output_config": request_kwargs.get("output_config"),
                        },
                    )
                    _dump_request(turn)
                    response = _stream_response_with_retries(turn)
                content_blocks = list(response.content)
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
                            "final_output": _anthropic_final_text(content_blocks),
                            "raw": None,
                        }
                    )
                    return
                tool_results: list[dict[str, Any]] = []
                visual_repin_blocks: list[dict[str, Any]] = []
                for block in tool_use_blocks:
                    tool = tools_by_name.get(block.name)
                    if tool is None:
                        result: Any = {
                            "ok": False,
                            "error": f"Unknown VibeCAD tool: {block.name}",
                        }
                    else:
                        arguments_json = json.dumps(_json_safe(block.input or {}))
                        result = loop.run_until_complete(
                            tool.on_invoke_tool(None, arguments_json)
                        )
                    if block.name == "core_capture_view_screenshot":
                        screenshot_summary = (
                            result.get("result")
                            if isinstance(result, dict)
                            and isinstance(result.get("result"), dict)
                            else result
                        )
                        if isinstance(screenshot_summary, dict):
                            visual_repin_blocks.extend(
                                _anthropic_visual_repin_content(
                                    context, screenshot_summary
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
            conn.send(
                {
                    "type": "error",
                    "error": f"Anthropic provider exceeded the maximum of {turn_limit} turns.",
                }
            )
        finally:
            loop.close()
    except Exception as exc:
        try:
            conn.send({"type": "error", "error": str(exc)})
        except Exception:
            pass
    finally:
        conn.close()


def _clear_inherited_sdk_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "agents"
            or name.startswith("agents.")
            or name == "pydantic"
            or name.startswith("pydantic.")
            or name == "openai"
            or name.startswith("openai.")
            or name == "anthropic"
            or name.startswith("anthropic.")
            or name == "httpx"
            or name.startswith("httpx.")
        ):
            sys.modules.pop(name, None)
