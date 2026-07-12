# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD provider session orchestration.

The session owns context, tool exposure, execution, steering, cancellation,
and persistence. Product intent stays in the conversation. FreeCAD state stays
in the live state packet. There is no workflow phase machine or prose parser.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable

from VibeCADCore import VibeCADService, get_service
from VibeCADProvider import (
    AnthropicProvider,
    BaseProvider,
    OfflineProvider,
    OpenAIProvider,
    ProviderUnavailable,
)
from VibeCADTools import (
    SafetyLevel,
    ToolArgumentValidationError,
    normalize_tool_failure,
    tool_failure,
)
from VibeCADWorkbenchTools import get_tool_pack


ProgressCallback = Callable[[dict[str, Any]], None]
CancellationCheck = Callable[[], bool]
SteeringCheck = Callable[[], list[str]]
QuestionCallback = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
DocumentThreadDispatch = Callable[[Callable[[], Any]], Any]

PROVIDER_SAFE_LEVELS = {
    SafetyLevel.READ,
    SafetyLevel.VIEW,
    SafetyLevel.SAFE_WRITE,
}

CORE_PROVIDER_TOOLS = {
    "conversation.ask_user",
    "project.update_design_document",
    "core.capture_view_screenshot",
    "core.delete_object",
    "core.set_view",
}


@dataclass(frozen=True)
class VibeCADResponse:
    provider: str
    final_output: str
    context: dict[str, Any]
    tool_trace: list[dict[str, Any]]
    error: str | None = None


def _on_document_thread(
    dispatch: DocumentThreadDispatch | None,
    operation: Callable[[], Any],
) -> Any:
    """Run one FreeCAD/service operation on the owning document thread."""
    if dispatch is None:
        return operation()
    return dispatch(operation)


def choose_provider(
    service: VibeCADService,
    prefer_online: bool = True,
) -> BaseProvider:
    auth = service.auth_state()
    if not prefer_online or not auth.can_call_provider:
        return OfflineProvider()
    provider_class: type[BaseProvider] = (
        AnthropicProvider if service.provider_name() == "anthropic" else OpenAIProvider
    )
    return provider_class(
        model=service.provider_model(),
        api_key=service.provider_api_key(),
        reasoning_effort=service.provider_reasoning_effort(),
        base_url=service.provider_base_url(),
    )


def _active_document_exists(service: VibeCADService) -> bool:
    return service._active_document() is not None


def _surface_tool_names(
    service: VibeCADService,
    workbench: str | None,
) -> set[str]:
    names = set(CORE_PROVIDER_TOOLS)
    pack = get_tool_pack(workbench)
    if pack is not None:
        names.update(pack.tool_names)
        names.update(pack.required_adjacent_tool_names)
    if not _active_document_exists(service):
        names = {
            name
            for name in names
            if service.registry.get(name).safety in {SafetyLevel.READ, SafetyLevel.VIEW}
        }
    return names


def _current_edit_mode(service: VibeCADService) -> str:
    state = _runtime_state(service)
    if state.get("edit_mode") and _active_sketch_name(state):
        return "sketch"
    return "none"


def is_provider_safe_tool(
    service: VibeCADService,
    tool_name: str,
    workbench: str | None = None,
) -> bool:
    try:
        tool = service.registry.get(tool_name)
    except KeyError:
        return False
    active = workbench or service.active_workbench_name()
    if tool.safety not in PROVIDER_SAFE_LEVELS:
        return False
    if tool_name not in _surface_tool_names(service, active):
        return False
    return tool.spec.supports_edit_mode(_current_edit_mode(service))


def provider_tool_schemas(
    service: VibeCADService,
    workbench: str | None,
) -> list[dict[str, Any]]:
    names = _surface_tool_names(service, workbench)
    return [
        service.registry.get(name).to_schema(active_workbench=workbench)
        for name in sorted(names)
        if is_provider_safe_tool(service, name, workbench)
    ]


def _runtime_state(service: VibeCADService) -> dict[str, Any]:
    native_diagnostics: dict[str, Any] | None = None
    try:
        raw = service.recompute_diagnostics()
        if isinstance(raw, dict):
            native_diagnostics = raw
    except Exception as exc:
        native_diagnostics = {"captured": False, "reason": str(exc)}
    return service.cad_state_summary(native_diagnostics=native_diagnostics)


def _context_for_provider(
    service: VibeCADService,
    session_trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = service.provider_context_summary()
    workbench = service.active_workbench_name()
    context["workbench"] = workbench
    context["_vibecad_debug"] = service.provider_debug_config()
    if not isinstance(context.get("cad_state"), dict):
        context["cad_state"] = _runtime_state(service)
    context["provider_tool_schemas"] = provider_tool_schemas(service, workbench)
    context["provider_tool_scope"] = {
        "workbench": workbench,
        "active_tool_count": len(context["provider_tool_schemas"]),
        "rule": "active workbench pack plus required adjacent operations",
    }
    if session_trigger:
        context["session_trigger"] = dict(session_trigger)
    return context


def _conversation_for_prompt(context: dict[str, Any]) -> list[dict[str, str]]:
    raw = context.get("conversation")
    turns = raw.get("conversation") if isinstance(raw, dict) else []
    if not isinstance(turns, list):
        return []
    result: list[dict[str, str]] = []
    for item in turns:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant", "system"} and content:
            result.append({"role": role, "content": content})
    return result


def _provider_state_payload(context: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "vibecad_project",
        "document",
        "selection",
        "view",
        "task_panel",
        "reference_images",
        "cad_state",
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
        key: context[key]
        for key in keys
        if key in context and context[key] not in (None, "", [], {})
    }


def _provider_prompt(
    prompt: str,
    context: dict[str, Any],
    *,
    prompt_section: str = "CURRENT_USER_MESSAGE",
) -> str:
    conversation = _conversation_for_prompt(context)
    if (
        conversation
        and conversation[-1]["role"] == "user"
        and conversation[-1]["content"].strip() == prompt.strip()
    ):
        conversation = conversation[:-1]
    payload = {
        "conversation": conversation,
        "current_cad": _provider_state_payload(context),
    }
    session_trigger = context.get("session_trigger")
    if isinstance(session_trigger, dict) and session_trigger:
        payload["session_trigger"] = session_trigger
    return (
        "VIBECAD_CONTEXT_JSON\n"
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)
        + "\nEND_VIBECAD_CONTEXT_JSON\n\n"
        + f"{prompt_section}\n"
        + prompt
    )


def _run_provider(
    provider: BaseProvider,
    prompt: str,
    context: dict[str, Any],
    tool_runner: Callable[[str, str], dict[str, Any]],
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
):
    return provider.run(
        prompt,
        context,
        tool_runner=tool_runner,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )


def _parse_arguments(arguments_json: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(arguments_json or "{}")
    except (TypeError, ValueError) as exc:
        return None, f"Tool arguments are not valid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "Tool arguments must be a JSON object."
    return value, None


def _active_sketch_name(state: dict[str, Any]) -> str:
    sketch = state.get("active_sketch")
    if not isinstance(sketch, dict):
        return ""
    return str(sketch.get("name") or "").strip()


def _edit_mode_block(
    tool: Any,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    edit_mode = (
        "sketch" if state.get("edit_mode") and _active_sketch_name(state) else "none"
    )
    if tool.spec.supports_edit_mode(edit_mode):
        return None
    if edit_mode == "sketch":
        explanation = (
            f"Sketch {_active_sketch_name(state)} is open for editing. Finish or "
            f"close it before running {tool.name}."
        )
    else:
        explanation = (
            f"{tool.name} requires an open Sketcher edit session. Open the exact "
            "target sketch first."
        )
    return tool_failure(
        tool.name,
        "EDIT_STATE_MISMATCH",
        "edit_state",
        explanation,
        observed={
            "active_edit_mode": edit_mode,
            "active_edit_object": _active_sketch_name(state) or None,
            "allowed_edit_modes": sorted(tool.spec.edit_modes),
            "human_action": (
                "Finish or close the active sketch."
                if edit_mode == "sketch"
                else "Open the exact target sketch for editing."
            ),
        },
        allowed_values=sorted(tool.spec.edit_modes),
        required_changes=[
            {
                "action": (
                    "close_active_sketch"
                    if edit_mode == "sketch"
                    else "open_target_sketch"
                )
            }
        ],
    )


def _consume_steering(steering_check: SteeringCheck | None) -> list[str]:
    if steering_check is None:
        return []
    values = steering_check() or []
    return [str(value).strip() for value in values if str(value).strip()]


def _emit(progress_callback: ProgressCallback | None, event: dict[str, Any]) -> None:
    if progress_callback is None:
        return
    progress_callback(event)


_TRACE_ITEM_LIMIT = 32
_TRACE_STRING_LIMIT = 1400
_TRACE_DEPTH_LIMIT = 6


def _bounded_trace_value(
    value: Any,
    *,
    path: str,
    depth: int,
    truncated: list[dict[str, Any]],
) -> Any:
    if depth >= _TRACE_DEPTH_LIMIT:
        truncated.append({"path": path, "reason": "depth", "limit": _TRACE_DEPTH_LIMIT})
        return "<truncated>"
    if isinstance(value, str):
        if len(value) <= _TRACE_STRING_LIMIT:
            return value
        truncated.append(
            {
                "path": path,
                "reason": "string_length",
                "original": len(value),
                "limit": _TRACE_STRING_LIMIT,
            }
        )
        return value[: _TRACE_STRING_LIMIT - 3] + "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > _TRACE_ITEM_LIMIT:
            truncated.append(
                {
                    "path": path,
                    "reason": "mapping_items",
                    "original": len(items),
                    "limit": _TRACE_ITEM_LIMIT,
                }
            )
            items = items[:_TRACE_ITEM_LIMIT]
        return {
            str(key): _bounded_trace_value(
                item,
                path=f"{path}.{key}" if path else str(key),
                depth=depth + 1,
                truncated=truncated,
            )
            for key, item in items
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if len(items) > _TRACE_ITEM_LIMIT:
            truncated.append(
                {
                    "path": path,
                    "reason": "sequence_items",
                    "original": len(items),
                    "limit": _TRACE_ITEM_LIMIT,
                }
            )
            items = items[:_TRACE_ITEM_LIMIT]
        return [
            _bounded_trace_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                truncated=truncated,
            )
            for index, item in enumerate(items)
        ]
    return _bounded_trace_value(
        repr(value), path=path, depth=depth, truncated=truncated
    )


def _trace_result(payload: dict[str, Any]) -> dict[str, Any]:
    selected = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }
    selected["ok"] = bool(payload.get("ok"))
    truncated: list[dict[str, Any]] = []
    result = _bounded_trace_value(
        selected,
        path="result",
        depth=0,
        truncated=truncated,
    )
    if truncated:
        result["truncation"] = {
            "truncated": True,
            "entries": truncated[:_TRACE_ITEM_LIMIT],
            "entry_count": len(truncated),
        }
    return result


def make_provider_tool_runner(
    service: VibeCADService,
    *,
    tool_trace: list[dict[str, Any]],
    progress_callback: ProgressCallback | None,
    cancellation_check: CancellationCheck | None,
    steering_check: SteeringCheck | None,
    question_callback: QuestionCallback | None,
    session_trigger: dict[str, Any] | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
):
    def run(tool_name: str, arguments_json: str = "{}") -> dict[str, Any]:
        started = time.monotonic()
        tool = None
        args: dict[str, Any] = {}

        def finalize(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal args, tool
            if not bool(payload.get("ok")):
                payload = normalize_tool_failure(tool_name, args, payload)
            trace_result = _trace_result(payload)
            trace = {
                "tool_name": tool_name,
                "arguments": args,
                "safety": tool.safety.value if tool is not None else None,
                "workbench": tool.workbench if tool is not None else None,
                "ok": bool(payload.get("ok")),
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "result": trace_result,
            }
            tool_trace.append(trace)
            _emit(
                progress_callback,
                {
                    "event": "tool_call_completed",
                    "tool_name": tool_name,
                    "ok": bool(payload.get("ok")),
                    "result": trace_result,
                },
            )
            return payload

        if cancellation_check is not None and cancellation_check():
            return finalize(
                tool_failure(
                    tool_name,
                    "RUN_CANCELLED",
                    "precondition",
                    "VibeCAD run stopped before this tool executed.",
                    requested={"arguments_json": arguments_json},
                    observed={"cancel_requested": True},
                    cancelled=True,
                )
            )
        try:
            tool = service.registry.get(tool_name)
        except KeyError:
            active_workbench = _on_document_thread(
                document_thread_dispatch,
                service.active_workbench_name,
            )
            available = _on_document_thread(
                document_thread_dispatch,
                lambda: sorted(
                    schema["name"]
                    for schema in provider_tool_schemas(service, active_workbench)
                ),
            )
            runtime_state = _on_document_thread(
                document_thread_dispatch,
                lambda: _runtime_state(service),
            )
            return finalize(
                tool_failure(
                    tool_name,
                    "UNKNOWN_TOOL",
                    "surface",
                    f"Unknown VibeCAD tool: {tool_name}",
                    requested={"arguments_json": arguments_json},
                    observed={
                        "active_workbench": active_workbench,
                        "active_edit_mode": runtime_state.get("edit_mode"),
                    },
                    candidates=available,
                    required_changes=[{"choose_available_tool": available}],
                )
            )
        visible_names = _on_document_thread(
            document_thread_dispatch,
            lambda: sorted(
                schema["name"]
                for schema in provider_tool_schemas(
                    service,
                    service.active_workbench_name(),
                )
            ),
        )
        if tool_name not in visible_names:
            runtime_state = _on_document_thread(
                document_thread_dispatch,
                lambda: _runtime_state(service),
            )
            active_workbench = _on_document_thread(
                document_thread_dispatch,
                service.active_workbench_name,
            )
            return finalize(
                tool_failure(
                    tool_name,
                    "TOOL_NOT_ON_ACTIVE_SURFACE",
                    "surface",
                    f"Tool is not in the active provider surface: {tool_name}.",
                    requested={"arguments_json": arguments_json},
                    observed={
                        "active_workbench": active_workbench,
                        "active_edit_mode": runtime_state.get("edit_mode"),
                        "active_edit_object": _active_sketch_name(runtime_state) or None,
                    },
                    candidates=visible_names,
                    required_changes=[{"choose_available_tool": visible_names}],
                )
            )
        args, argument_error = _parse_arguments(arguments_json)
        if argument_error:
            args = {}
            return finalize(
                tool_failure(
                    tool_name,
                    "INVALID_TOOL_ARGUMENTS_JSON",
                    "schema",
                    argument_error,
                    requested={"arguments_json": arguments_json},
                    observed={"expected": "JSON object"},
                    required_changes=[{"provide": "one valid JSON object"}],
                )
            )
        assert args is not None
        try:
            tool.spec.validate_arguments(args)
        except ToolArgumentValidationError as exc:
            return finalize(exc.payload)
        if tool_name == "conversation.ask_user":
            questions = args.get("questions")
            assert isinstance(questions, list) and questions
            if question_callback is None:
                return finalize(
                    tool_failure(
                        tool_name,
                        "QUESTION_UI_UNAVAILABLE",
                        "precondition",
                        "The interactive question UI is unavailable in this session.",
                        requested=args,
                        observed={"question_count": len(questions)},
                    )
                )
            try:
                answers = question_callback(questions)
            except Exception as exc:
                completed_answers = list(
                    getattr(exc, "completed_answers", []) or []
                )
                return finalize(
                    tool_failure(
                        tool_name,
                        "QUESTION_ROUND_FAILED",
                        "precondition",
                        f"The question round failed: {exc}",
                        requested=args,
                        observed={
                            "question_count": len(questions),
                            "completed_answer_count": len(completed_answers),
                        },
                        completed_answers=completed_answers,
                    )
                )
            payload = {
                "ok": bool(answers),
                "answers": answers,
                "cancelled": not bool(answers),
            }
            if not answers:
                payload = tool_failure(
                    tool_name,
                    "QUESTION_ROUND_CANCELLED",
                    "precondition",
                    "The user cancelled the question round.",
                    requested=args,
                    observed={"question_count": len(questions)},
                    cancelled=True,
                    answers=[],
                )
            return finalize(payload)
        state_before = _on_document_thread(
            document_thread_dispatch,
            lambda: _runtime_state(service),
        )
        edit_block = _edit_mode_block(tool, state_before)
        if edit_block is not None:
            edit_block["requested"] = args
            return finalize(edit_block)
        try:
            raw = _on_document_thread(
                document_thread_dispatch,
                lambda: service.registry.call(tool_name, **args),
            )
            payload = dict(raw) if isinstance(raw, dict) else {"value": raw}
            payload.setdefault("ok", payload.get("error") in (None, ""))
        except ToolArgumentValidationError as exc:
            payload = exc.payload
        except Exception as exc:
            payload = tool_failure(
                tool_name,
                "TOOL_HANDLER_EXCEPTION",
                "native_call",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
        try:
            steering = _consume_steering(steering_check)
        except Exception as exc:
            steering = []
            payload["human_steering_error"] = str(exc)
        if steering:
            payload["human_steering"] = steering
            _emit(
                progress_callback,
                {"event": "human_steering_consumed", "message_count": len(steering)},
            )
        return finalize(payload)

    run.provider_update = lambda: _on_document_thread(
        document_thread_dispatch,
        lambda: _context_for_provider(service, session_trigger),
    )
    return run


def _run_session_turn(
    prompt: str,
    *,
    service: VibeCADService | None,
    prefer_online: bool,
    provider: BaseProvider | None,
    progress_callback: ProgressCallback | None,
    cancellation_check: CancellationCheck | None,
    steering_check: SteeringCheck | None,
    question_callback: QuestionCallback | None,
    session_trigger: dict[str, Any] | None,
    persist_input_as_user: bool,
    prompt_section: str,
    document_thread_dispatch: DocumentThreadDispatch | None,
) -> VibeCADResponse:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Prompt cannot be empty.")
    active_service = service or _on_document_thread(
        document_thread_dispatch,
        get_service,
    )
    persistence = _on_document_thread(
        document_thread_dispatch,
        active_service.document_persistence_state,
    )
    if not persistence.get("enabled"):
        raise RuntimeError(
            str(
                persistence.get("message")
                or "Save the active document to enable VibeCAD."
            )
        )
    _emit(progress_callback, {"event": "context_build_started"})
    context = _on_document_thread(
        document_thread_dispatch,
        lambda: _context_for_provider(active_service, session_trigger),
    )
    if persist_input_as_user:
        _on_document_thread(
            document_thread_dispatch,
            lambda: active_service.record_conversation_turn("user", clean_prompt),
        )
    tool_trace: list[dict[str, Any]] = []
    _emit(
        progress_callback,
        {
            "event": "context_build_completed",
            "workbench": context.get("workbench"),
            "provider_tool_count": len(context.get("provider_tool_schemas") or []),
        },
    )
    active_provider = provider or _on_document_thread(
        document_thread_dispatch,
        lambda: choose_provider(
            active_service,
            prefer_online=prefer_online,
        ),
    )
    provider_name = active_provider.__class__.__name__
    tool_runner = make_provider_tool_runner(
        active_service,
        tool_trace=tool_trace,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
        session_trigger=session_trigger,
        document_thread_dispatch=document_thread_dispatch,
    )
    _emit(
        progress_callback,
        {"event": "provider_turn_started", "provider": provider_name, "turn": 1},
    )
    try:
        result = _run_provider(
            active_provider,
            _provider_prompt(
                clean_prompt,
                context,
                prompt_section=prompt_section,
            ),
            context,
            tool_runner,
            cancellation_check,
            progress_callback,
        )
        final_output = str(result.final_output or "").strip()
        final_context = _on_document_thread(
            document_thread_dispatch,
            lambda: _context_for_provider(active_service, session_trigger),
        )
        if final_output:
            _on_document_thread(
                document_thread_dispatch,
                lambda: active_service.record_conversation_turn(
                    "assistant",
                    final_output,
                    provider=provider_name,
                    tool_trace=tool_trace,
                    metadata={"session_trigger": session_trigger}
                    if session_trigger
                    else None,
                ),
            )
            _emit(
                progress_callback,
                {
                    "event": "provider_turn_output",
                    "provider": provider_name,
                    "turn": 1,
                    "text": final_output,
                },
            )
        _emit(
            progress_callback,
            {
                "event": "provider_turn_completed",
                "provider": provider_name,
                "turn": 1,
                "tool_count": len(tool_trace),
            },
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=final_context,
            tool_trace=tool_trace,
        )
    except ProviderUnavailable as exc:
        final_output = (
            f"{provider_name} failed before returning a usable AI result: {exc}"
        )
        _on_document_thread(
            document_thread_dispatch,
            lambda: active_service.record_conversation_turn(
                "assistant",
                final_output,
                provider=provider_name,
                tool_trace=tool_trace,
                metadata={
                    "provider_error": str(exc),
                    **(
                        {"session_trigger": session_trigger}
                        if session_trigger
                        else {}
                    ),
                },
            ),
        )
        _emit(
            progress_callback,
            {
                "event": "provider_turn_failed",
                "provider": provider_name,
                "turn": 1,
                "error": str(exc),
                "tool_count": len(tool_trace),
            },
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=_on_document_thread(
                document_thread_dispatch,
                lambda: _context_for_provider(active_service, session_trigger),
            ),
            tool_trace=tool_trace,
            error=str(exc),
        )


def run_prompt(
    prompt: str,
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
    question_callback: QuestionCallback | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
) -> VibeCADResponse:
    return _run_session_turn(
        prompt,
        service=service,
        prefer_online=prefer_online,
        provider=provider,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
        session_trigger=None,
        persist_input_as_user=True,
        prompt_section="CURRENT_USER_MESSAGE",
        document_thread_dispatch=document_thread_dispatch,
    )


def run_sketch_close_continuation(
    event: dict[str, Any],
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
    question_callback: QuestionCallback | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
) -> VibeCADResponse:
    if not isinstance(event, dict):
        raise ValueError("Sketch-close continuation event must be an object.")
    expected_fields = {
        "type",
        "document_uid",
        "document_name",
        "sketch_name",
        "sketch_label",
        "owner_body",
    }
    if set(event) != expected_fields:
        raise ValueError(
            "Sketch-close continuation event requires exactly: "
            + ", ".join(sorted(expected_fields))
            + "."
        )
    if str(event.get("type") or "").strip() != "human_closed_sketch":
        raise ValueError(
            "Sketch-close continuation event type must be human_closed_sketch."
        )
    clean_event = {
        "type": "human_closed_sketch",
        "document_uid": str(event.get("document_uid") or "").strip(),
        "document_name": str(event.get("document_name") or "").strip(),
        "sketch_name": str(event.get("sketch_name") or "").strip(),
        "sketch_label": str(event.get("sketch_label") or "").strip(),
        "owner_body": str(event.get("owner_body") or "").strip(),
    }
    missing = [
        key
        for key in ("document_uid", "document_name", "sketch_name", "owner_body")
        if not clean_event[key]
    ]
    if missing:
        raise ValueError(
            "Sketch-close continuation event is missing: " + ", ".join(missing) + "."
        )
    prompt = (
        f"The human closed sketch {clean_event['sketch_name']} "
        f"({clean_event['sketch_label'] or clean_event['sketch_name']}) in Body "
        f"{clean_event['owner_body']}. Continue the existing CAD obligation from the "
        "current post-edit document state. Closing the sketch is a handoff to continue, "
        "not proof that the sketch is valid or permission to skip verification. Inspect "
        "its current readiness and native errors before choosing the next operation. Do "
        "not restart requirement refinement or restate the accepted design."
    )
    return _run_session_turn(
        prompt,
        service=service,
        prefer_online=prefer_online,
        provider=provider,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
        session_trigger=clean_event,
        persist_input_as_user=False,
        prompt_section="CURRENT_SESSION_EVENT",
        document_thread_dispatch=document_thread_dispatch,
    )


def _format_document_delta(delta: Any) -> str:
    if not isinstance(delta, dict):
        return ""
    added = delta.get("added") or []
    removed = delta.get("removed") or []
    changed = delta.get("changed") or []
    parts: list[str] = []
    if added:
        parts.append(f"+{len(added)} objects")
    if removed:
        parts.append(f"-{len(removed)} objects")
    if changed:
        parts.append(f"{len(changed)} changed")
    return ", ".join(parts)
