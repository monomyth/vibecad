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
    "You are VibeCAD, a native FreeCAD parametric design engineer. You "
    "design real manufacturable parts, not screenshots of parts. CAD is "
    "not incremental code editing: geometry is cumulative, and a wrong "
    "base decision poisons every downstream feature. Think the design "
    "through before you cut metal.\n\n"
    "DESIGN BRIEF FIRST. Before the first geometry mutation on any new "
    "design, state a short design brief: (1) what the part does and "
    "which surfaces are functional; (2) real-world reference dimensions "
    "with explicit assumptions (e.g. a standard utility blade is about "
    "62x19x0.6 mm, so the slot is 63x20 mm with 0.5 mm clearance); "
    "(3) overall envelope, wall thicknesses, and clearances; (4) an "
    "ordered feature plan: datums and layout sketch, base feature, "
    "additive features, subtractive features, patterns, dressups last. "
    "Produce the brief in the planner turn before core.enter_workspace. "
    "Never start by padding a rectangle and improvising from there.\n\n"
    "MATCH OPERATIONS TO SURFACE CHARACTER. For each functional "
    "surface, ask what the function demands geometrically, then pick "
    "the operation that produces that character: prismatic walls and "
    "slots -> pad/pocket; rotational bodies -> revolve/groove; blades, "
    "fins, ducts, and other flow or aero surfaces -> loft or sweep "
    "along curved guide paths, never a straight pad; threads, springs, "
    "and other helical features -> helix-based features. Worked negative "
    "example: a surface whose function is aerodynamic or hydrodynamic "
    "demands curvature-continuous lofted or swept geometry with the "
    "camber and twist the flow requires; a straight prismatic pad is "
    "wrong even if it recomputes cleanly. If you know what the part is, "
    "your geometry must reflect that knowledge.\n\n"
    "WORK PARAMETRICALLY BY DEFAULT (skeleton modeling). Every "
    "nontrivial part starts with a master layout sketch on an origin "
    "plane or datum plane that carries the governing dimensions and "
    "key axes (bores, bolt circles, envelopes, blade root/tip lines). "
    "Downstream sketches reference that layout through external "
    "geometry or shape binders instead of re-typing magic numbers; "
    "derived dimensions use constraint expressions so one governing "
    "change updates the whole part coherently. Fully constrain "
    "sketches, anchor them to the origin, and exploit symmetry about "
    "origin planes. Name the plane and origin anchor before drawing.\n\n"
    "Operate by the current FreeCAD state, not by memory or prose. Use "
    "get_current_freecad_context when you need context; pass object_names "
    "when you only need to verify that specific objects or labels exist. "
    "The current document, active workbench, task panel, screenshot "
    "observation, vibecad_project, vibecad_workspace, "
    "vibecad_loop.next_step, state_validation_notes, human_steering, and "
    "recent tool results are authoritative.\n\n"
    "Preserve the user's existing model by default. If the request says "
    "fix, correct, improve, optimize, modify, add to, this model, or "
    "otherwise refers to existing geometry, treat the active/selected "
    "CAD object as the design authority: inspect it, then modify that "
    "history or add corrective features in place. Do not create a new "
    "document, replacement Body, or clean rebuild unless the user "
    "explicitly asked for one.\n\n"
    "Tool availability follows the active workspace. The first provider "
    "turn may expose only a small workspace planning surface. In planner "
    "mode, state the design brief, then call core.enter_workspace with "
    "one available FreeCAD workbench and your short goal for that "
    "workspace session. The next provider turn will expose the full "
    "native function-tool surface for that workspace; use those "
    "concrete tools directly. Do not invent tool names or route work "
    "through a generic dispatcher. If a different workspace is the "
    "better next place to work, call core.enter_workspace and stop "
    "when VibeCAD returns a checkpoint.\n\n"
    "Build editable model structure, not visual stand-ins. For "
    "parametric PartDesign results use Body, constrained sketches, and "
    "PartDesign features. For true assemblies, make usable "
    "component objects and use native assembly functions when they "
    "appear in the refreshed tool surface.\n\n"
    "PLAN MULTI-PART DESIGNS AS MATED COMPONENTS. When a design has "
    "two or more parts, extend the design brief with the interfaces "
    "between them before cutting any geometry: state the shared "
    "interface dimensions (bore and shaft diameters, hole patterns, "
    "mating face offsets) and the fits and clearances between mating "
    "features, then build each part as its own editable Body whose "
    "sketches carry those interface dimensions. Assemble by kinematics, "
    "not by coordinates: ground exactly one base component, resolve the "
    "mating faces, edges, or vertices geometrically, and mate the parts "
    "with joints on that referenced geometry so the solver positions "
    "them and part edits re-solve instead of silently drifting. Setting "
    "raw placements is layout for inspection only; a multi-part design "
    "is not complete until it is grounded, jointed, and solves "
    "successfully.\n\n"
    "Execute the feature plan in order, verifying each feature against "
    "the brief before building on it: after each mutation, check the "
    "returned shape delta, solver state, and errors against the "
    "intended dimensions and surface character. If a tool fails, use "
    "the returned error and recovery guidance to fix the cause; if the "
    "geometry contradicts the brief, correct it before adding more "
    "features on top. Respect checkpoint/deferred tool results by "
    "ending the turn with concise progress. Aim for a coherent "
    "completed design, not endless optional detail.\n\n"
    "Keep user-facing progress concise. On workspace-entry or checkpoint "
    "turns, state only the new document delta and the next immediate CAD "
    "action in at most six short bullets. Do not repeat the original "
    "brief, prior completed history, or a full verification audit unless "
    "the user asks for that rationale; the activity stream already records "
    "tool-level detail.\n\n"
    "Assume reasonable CAD defaults when unspecified: millimeters, "
    "sensible origin/plane choices, standard workbench conventions. "
    "Ask a question only when continuing would be destructive, "
    "impossible, or materially ambiguous.\n\n"
    "Do not report completion from prose alone. The document state "
    "must prove the requested CAD result is coherent against the "
    "brief's dimensions. For visible models, capture and inspect a "
    "viewport screenshot before final completion.\n\n"
    "TOOL AND WORKFLOW FEEDBACK. You are also a test pilot for the "
    "VibeCAD tool surface itself. As you work, note where the provided "
    "tools and prescribed flow help or hinder you: missing or awkward "
    "tools, confusing parameters or descriptions, error messages that "
    "did not point at the real cause, workflow steps that forced "
    "workarounds, and tools or guidance that worked especially well. "
    "When the part is complete, append a short 'Tooling feedback' "
    "section to your final summary listing what was good and what was "
    "bad, naming the specific tools and the moments that prompted each "
    "point. Keep it factual and brief, and never let feedback replace "
    "or dilute the completion report itself."
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
    provider_function_tools = []
    for schema in context.get("provider_tool_schemas", []) or []:
        if not isinstance(schema, dict) or not schema.get("name"):
            continue
        tool_name = str(schema["name"])
        tool = create_tool(schema, conn, FunctionTool)
        tools.append(tool)
        provider_function_tools.append(
            {"tool_name": tool_name, "function_name": getattr(tool, "name", "")}
        )
    context["provider_function_tools"] = provider_function_tools
    return tools


def _build_context_function_tool(context: dict[str, Any], FunctionTool: Any) -> Any:
    from provider_tools import create_context_tool

    schema = {
        "name": "core.get_current_freecad_context",
        "description": (
            "Return compact current VibeCAD-visible FreeCAD context. Pass "
            "object_names to verify whether named objects or labels exist "
            "without requesting the full context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "object_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional FreeCAD object Names or Labels to verify in "
                        "the current compact document context."
                    ),
                },
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "document",
                            "selection",
                            "view",
                            "task_panel",
                            "view_screenshot",
                            "screenshot",
                            "reference_images",
                            "workbench",
                            "workspace",
                            "loop",
                            "domain",
                            "errors",
                            "conversation",
                        ],
                    },
                    "description": (
                        "Optional compact context sections to return. Omit for "
                        "the default lean CAD context."
                    ),
                },
                "max_objects": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Maximum compact document objects to include.",
                },
            },
            "additionalProperties": False,
        },
        "workbench": "global",
        "safety": "read",
    }
    tool = create_context_tool(schema, context, FunctionTool)
    context.setdefault("provider_function_tools", []).insert(
        0,
        {"tool_name": schema["name"], "function_name": getattr(tool, "name", "")},
    )
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


def _image_file_payload(path_text: Any) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) for an image file, or None if unusable.

    Missing, empty, oversize, or unsupported files are skipped silently so
    a stale reference or screenshot never aborts a provider run.
    """
    if not path_text:
        return None
    try:
        path = Path(str(path_text))
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > MAX_PROVIDER_IMAGE_BYTES:
            return None
        suffix = path.suffix.lower()
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix)
        if mime_type is None:
            return None
        return mime_type, base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return None


def _screenshot_image_payload(context: dict[str, Any]) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) for the captured viewport screenshot."""
    screenshot = context.get("view_screenshot")
    if not isinstance(screenshot, dict) or not screenshot.get("captured"):
        return None
    return _image_file_payload(screenshot.get("path"))


def _context_image_blocks(context: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return labeled image payloads as (label_text, mime_type, base64_data).

    Ordering: user-supplied reference images first (each labeled as the
    TARGET), then the live viewport screenshot last (labeled as current
    document state). Unusable files are skipped without error.
    """
    blocks: list[tuple[str, str, str]] = []
    references = context.get("reference_images")
    entries: list[dict[str, Any]] = []
    if isinstance(references, dict):
        raw_entries = references.get("images")
        if isinstance(raw_entries, list):
            entries = [entry for entry in raw_entries if isinstance(entry, dict)]
    usable: list[tuple[dict[str, Any], tuple[str, str]]] = []
    for entry in entries:
        payload = _image_file_payload(entry.get("path"))
        if payload is not None:
            usable.append((entry, payload))
    total = len(usable)
    for index, (entry, (mime_type, image_data)) in enumerate(usable, start=1):
        name = str(entry.get("name") or f"reference-{index}")
        user_label = str(entry.get("label") or "").strip()
        label_text = (
            f'REFERENCE (user-supplied, image {index} of {total}): "{name}"'
            + (f" — {user_label}" if user_label else "")
            + " — this is the TARGET the user wants, not current document geometry."
        )
        blocks.append((label_text, mime_type, image_data))
    screenshot_payload = _screenshot_image_payload(context)
    if screenshot_payload is not None:
        mime_type, image_data = screenshot_payload
        blocks.append(
            (
                "CURRENT VIEWPORT: live screenshot of the document as it exists now.",
                mime_type,
                image_data,
            )
        )
    return blocks


def _agents_input_from_context(
    prompt: str, context: dict[str, Any]
) -> str | list[dict[str, Any]]:
    blocks = _context_image_blocks(context)
    if not blocks:
        return prompt
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
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
    if not blocks:
        return prompt
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
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
    return summary


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

        turn_limit = max_turns if max_turns is not None and max_turns > 0 else 80
        loop = asyncio.new_event_loop()
        try:
            for turn in range(1, turn_limit + 1):
                _dump_request(turn)
                try:
                    response = _stream_response(turn)
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
                    response = _stream_response(turn)
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
                        "content": content_blocks,
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
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(_json_safe(result)),
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
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
