# SPDX-License-Identifier: LGPL-2.1-or-later

"""Prompt/session orchestration for the VibeCAD assistant."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import re
import time
from typing import Any, Callable

from VibeCADCore import VibeCADService, get_service
from VibeCADProvider import (
    AnthropicProvider,
    BaseProvider,
    OfflineProvider,
    OpenAIAgentsProvider,
    ProviderUnavailable,
)
from VibeCADTools import SafetyLevel
from VibeCADWorkbenchTools import get_tool_pack
from provider_tools.base import provider_function_name

MAX_AUTONOMOUS_PROVIDER_TURNS: int | None = None
MAX_AUTONOMOUS_PROVIDER_SECONDS: float | None = None
REFERENCE_BRIEF_MARKER = "REFERENCE_BRIEF_JSON:"
DESIGN_PREFLIGHT_SCHEMA = "vibecad-design-preflight-v1"
DESIGN_PREFLIGHT_BUILD_READY = "build_ready"
DESIGN_PREFLIGHT_NEEDS_USER = "needs_user"
DESIGN_PREFLIGHT_SUBMIT_TOOL = "core.submit_design_preflight"
DESIGN_PREFLIGHT_CONTINUATION_PROMPTS = {
    "continue",
    "continue.",
    "continue please",
    "keep going",
    "keep going please",
    "proceed",
    "proceed.",
    "go on",
    "go ahead",
    "next",
    "next step",
    "resume",
    "build it",
    "do it",
    "yes",
    "yes.",
    "ok",
    "okay",
}


def _array_of_strings_schema(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


DESIGN_PREFLIGHT_SUBMIT_SCHEMA: dict[str, Any] = {
    "name": DESIGN_PREFLIGHT_SUBMIT_TOOL,
    "description": (
        "Submit the structured design preflight state. This is the only way "
        "to unlock CAD tools after requirement refinement, design drafting, "
        "and adversarial review."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "schema": {"type": "string"},
            "status": {
                "type": "string",
                "enum": [DESIGN_PREFLIGHT_BUILD_READY, DESIGN_PREFLIGHT_NEEDS_USER],
            },
            "user_intent": {"type": "string"},
            "requirement_refinement": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "model_answer": {"type": "string"},
                        "assumption": {"type": "boolean"},
                        "why_it_matters": {"type": "string"},
                    },
                    "required": [
                        "question",
                        "model_answer",
                        "assumption",
                        "why_it_matters",
                    ],
                },
            },
            "user_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "default_answer": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "answer": {"type": "string"},
                                },
                                "required": ["label", "answer"],
                            },
                        },
                    },
                    "required": ["question", "default_answer", "options"],
                },
            },
            "design_intent_draft": {
                "type": "object",
                "properties": {
                    "architecture": {"type": "string"},
                    "bodies_components": _array_of_strings_schema(
                        "Bodies/components in the intended design."
                    ),
                    "interfaces": _array_of_strings_schema(
                        "Mechanical, geometric, or assembly interfaces."
                    ),
                    "envelopes": _array_of_strings_schema(
                        "Clearance, keepout, swept-motion, fit, flow, or load envelopes."
                    ),
                    "mechanisms": _array_of_strings_schema(
                        "Mechanisms and moving/contact behavior."
                    ),
                    "manufacturing_assumptions": _array_of_strings_schema(
                        "Material/process assumptions."
                    ),
                    "non_negotiable_geometry": _array_of_strings_schema(
                        "Geometry that must not be simplified away."
                    ),
                    "risks": _array_of_strings_schema(
                        "Design risks found before CAD execution."
                    ),
                },
            },
            "adversarial_review": {
                "type": "object",
                "properties": {
                    "blocking_issues": _array_of_strings_schema(
                        "Issues that prevent CAD execution."
                    ),
                    "criticisms": _array_of_strings_schema(
                        "Adversarial criticisms of the draft."
                    ),
                    "required_revisions": _array_of_strings_schema(
                        "Revisions applied before final plan."
                    ),
                },
            },
            "final_build_plan": {
                "type": "object",
                "properties": {
                    "architecture": {"type": "string"},
                    "bodies": _array_of_strings_schema("Bodies to build."),
                    "sketches_features": _array_of_strings_schema(
                        "Sketches/features to create."
                    ),
                    "interfaces": _array_of_strings_schema(
                        "Interfaces to verify."
                    ),
                    "envelopes": _array_of_strings_schema(
                        "Envelopes to model/verify."
                    ),
                    "mechanisms": _array_of_strings_schema(
                        "Mechanisms to model/verify."
                    ),
                    "manufacturing_assumptions": _array_of_strings_schema(
                        "Manufacturing assumptions."
                    ),
                    "critical_geometry": _array_of_strings_schema(
                        "Critical geometry to verify."
                    ),
                    "construction_order": _array_of_strings_schema(
                        "Ordered construction steps."
                    ),
                    "verification_checks": _array_of_strings_schema(
                        "Checks required after/during construction."
                    ),
                    "forbidden_shortcuts": _array_of_strings_schema(
                        "Shortcuts the CAD loop must not take."
                    ),
                },
            },
        },
        "required": [
            "schema",
            "status",
            "user_intent",
            "requirement_refinement",
        ],
    },
    "workbench": "global",
    "safety": "safe_write",
}
ProgressCallback = Callable[[dict[str, Any]], None]
CancellationCheck = Callable[[], bool]
SteeringCheck = Callable[[], list[str]]


@dataclass(frozen=True)
class VibeCADResponse:
    provider: str
    final_output: str
    context: dict[str, Any]
    tool_trace: list[dict[str, Any]]
    error: str | None = None


@dataclass(frozen=True)
class ProviderToolScope:
    workbench: str | None
    stage: str
    reason: str = ""
    tool_names: set[str] | None = None


PROVIDER_SAFE_LEVELS = {
    SafetyLevel.READ,
    SafetyLevel.VIEW,
    SafetyLevel.SAFE_WRITE,
}

PROVIDER_COMMAND_WRITE_TOOLS = {
    "cad.define_component",
    "cad.define_interface",
    "cad.define_envelope",
    "cad.define_mechanism",
    "cad.create_profile",
    "cad.create_feature",
    "core.create_new_document",
    "core.open_document",
    "core.delete_object",
    "core.update_design_memory",
    # Only surfaced when the user opts into script mode; see
    # VibeCADService.is_tool_enabled_for_provider.
    "model.build_from_script",
    "partdesign.create_body",
    "partdesign.create_sketch",
    "partdesign.create_datum_plane",
    "partdesign.create_datum_line",
    "partdesign.extrude",
    "partdesign.hole_from_sketch",
    "partdesign.revolve",
    "partdesign.loft_profiles",
    "partdesign.sweep_profile",
    "partdesign.helix_profile",
    "partdesign.pattern",
    "partdesign.dressup",
    "partdesign.boolean_bodies",
    "partdesign.set_feature_dimensions",
    "part.set_placement",
    "part.cut_cylindrical_hole",
    "part.dressup",
    "part.thicken_surface",
    "draft.create_array",
    "draft.create_wire",
    "surface.create_surface",
    "material.apply_appearance",
    "techdraw.create_page",
    "techdraw.add_view",
    "assembly.create_assembly",
    "assembly.add_component",
    "assembly.set_component_placement",
    "assembly.ground_component",
    "assembly.create_joint",
    "assembly.solve",
    "cam.define_machine",
    "cam.create_job",
    "cam.add_tool",
    "cam.create_operation",
    "cam.postprocess",
    "sketcher.create_sketch",
    "sketcher.open_sketch",
    "sketcher.close_sketch",
    "sketcher.set_geometry_name",
    "sketcher.edit_constraint",
    "sketcher.add_external_geometry",
    "sketcher.remove_external_geometry",
    "sketcher.add_geometry",
    "sketcher.add_hole_pattern",
    "sketcher.add_slot",
    "sketcher.add_constraint",
    "sketcher.draw_rectangle",
    "sketcher.move_point",
    "sketcher.transform_geometry",
    "sketcher.modify_geometry",
    "sketcher.delete_items",
    "sketcher.set_construction",
}

DOCUMENT_MANAGEMENT_TOOLS = {
    "core.create_new_document",
    "core.open_document",
}

PROVIDER_QUEUE_TOOLS = {
    "core.undo_last_vibecad_action",
    "core.clear_local_session",
}

SKETCH_EDIT_ALLOWED_TOOLS = {
    "core.update_design_memory",
    "sketcher.close_sketch",
    "sketcher.inspect_sketch",
    "sketcher.add_geometry",
    "sketcher.add_constraint",
    "sketcher.edit_constraint",
    "sketcher.move_point",
    "sketcher.modify_geometry",
    "sketcher.transform_geometry",
    "sketcher.delete_items",
    "sketcher.set_construction",
    "sketcher.set_geometry_name",
    "sketcher.add_external_geometry",
    "sketcher.remove_external_geometry",
    "sketcher.resolve_geometry",
    "sketcher.add_hole_pattern",
    "sketcher.add_slot",
    "sketcher.draw_rectangle",
}

# Session-internal tools that must never appear in a provider tool listing.
# Workspace activation remains callable for internal flows only; the model sees
# AI-native tools by default and native workbench packs only through preferences.
INTERNAL_SESSION_TOOLS = {
    "core.activate_workbench",
    "core.get_tool_shape_report",
    "core.report_tool_shape_gap",
}

AI_NATIVE_CAD_TOOLS = {
    "cad.inspect_state",
    "cad.define_component",
    "cad.define_interface",
    "cad.define_envelope",
    "cad.define_mechanism",
    "cad.create_profile",
    "cad.create_feature",
    "cad.verify_design",
}

CORE_PROVIDER_TOOLS = {
    "cad.inspect_state",
    "cad.define_component",
    "cad.define_interface",
    "cad.define_envelope",
    "cad.define_mechanism",
    "cad.create_profile",
    "cad.create_feature",
    "cad.verify_design",
    "core.update_design_memory",
    "core.capture_view_screenshot",
    "core.set_view",
    "core.get_report_view_errors",
    # Global script-mode write path; hidden unless the user enables script
    # mode in preferences (VibeCADService.is_tool_enabled_for_provider).
    "model.build_from_script",
}

PROVIDER_WORKSPACE_CONTROL_TOOLS = {
    "cad.inspect_state",
    "cad.define_component",
    "cad.define_interface",
    "cad.define_envelope",
    "cad.define_mechanism",
    "cad.create_profile",
    "cad.create_feature",
    "cad.verify_design",
    "core.update_design_memory",
    "core.capture_view_screenshot",
    "core.set_view",
    "core.get_report_view_errors",
}

WORKBENCH_READ_TOOLS = {
    "PartDesignWorkbench": {"partdesign.get_bodies"},
    "SketcherWorkbench": {"sketcher.inspect_sketch"},
    "PartWorkbench": {"core.list_workbench_objects"},
    "AssemblyWorkbench": {"assembly.get_assemblies"},
    "TechDrawWorkbench": {"techdraw.get_pages"},
    "MaterialWorkbench": {"core.list_workbench_objects"},
    "CAMWorkbench": {"core.list_workbench_objects"},
}

def is_provider_safe_tool(
    service: VibeCADService,
    tool_name: str,
    workbench: str | None = None,
    *,
    apply_workbench_allowlist: bool = True,
) -> bool:
    try:
        tool = service.registry.get(tool_name)
    except KeyError:
        return False
    if tool.name in DOCUMENT_MANAGEMENT_TOOLS:
        return False
    if tool.name in INTERNAL_SESSION_TOOLS:
        return False
    if apply_workbench_allowlist:
        allowlist = _provider_tool_allowlist_for_workbench(service, workbench)
        if allowlist is not None and tool_name not in allowlist:
            return False
    if tool.name in PROVIDER_QUEUE_TOOLS:
        return False
    if not is_provider_tool_kind_allowed(tool.safety, tool.name):
        return False
    return _is_tool_available_for_provider_context(
        service, tool, workbench
    ) and service.is_tool_enabled_for_provider(tool, workbench)


def _provider_tool_allowlist_for_workbench(
    service: VibeCADService, workbench: str | None
) -> set[str] | None:
    if not service.native_freecad_tools_enabled():
        return set(CORE_PROVIDER_TOOLS)
    if not workbench:
        return set(CORE_PROVIDER_TOOLS)
    pack = get_tool_pack(workbench)
    if pack is None:
        return set(CORE_PROVIDER_TOOLS)
    allowlist = set(CORE_PROVIDER_TOOLS)
    allowlist.update(WORKBENCH_READ_TOOLS.get(workbench, set()))
    allowlist.update(pack.tool_names)
    return allowlist


def is_provider_tool_kind_allowed(safety: SafetyLevel, tool_name: str) -> bool:
    return safety in {SafetyLevel.READ, SafetyLevel.VIEW} or (
        safety is SafetyLevel.SAFE_WRITE and tool_name in PROVIDER_COMMAND_WRITE_TOOLS
    )


def _is_partdesign_sketcher_tool(tool_name: str) -> bool:
    """Sketcher tools usable inside PartDesign, per the PartDesign pack."""
    pack = get_tool_pack("PartDesignWorkbench")
    if pack is None:
        return False
    return tool_name.startswith("sketcher.") and tool_name in pack.tool_names


def _is_tool_available_for_provider_context(
    service: VibeCADService,
    tool: Any,
    workbench: str | None,
) -> bool:
    if tool.is_available_for(workbench):
        return True
    if workbench == "PartDesignWorkbench" and _is_partdesign_sketcher_tool(tool.name):
        return True
    return False


def choose_provider(
    service: VibeCADService, prefer_online: bool = True
) -> BaseProvider:
    auth = service.auth_state()
    if prefer_online and auth.can_call_provider:
        provider_class: type[BaseProvider] = (
            AnthropicProvider
            if service.provider_name() == "anthropic"
            else OpenAIAgentsProvider
        )
        return provider_class(
            model=service.provider_model(),
            api_key=service.provider_api_key(),
            reasoning_effort=service.provider_reasoning_effort(),
            base_url=service.provider_base_url(),
        )
    return OfflineProvider()


def _run_provider_with_optional_cancellation(
    provider: BaseProvider,
    prompt: str,
    context: dict[str, Any],
    tool_runner: Callable[[str, str], dict[str, Any]] | None,
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
):
    parameters = inspect.signature(provider.run).parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs: dict[str, Any] = {"tool_runner": tool_runner}
    if accepts_kwargs or "cancellation_check" in parameters:
        kwargs["cancellation_check"] = cancellation_check
    if accepts_kwargs or "progress_callback" in parameters:
        kwargs["progress_callback"] = progress_callback
    return provider.run(prompt, context, **kwargs)


def _design_preflight_state(context: dict[str, Any]) -> dict[str, Any]:
    project = context.get("vibecad_project")
    if not isinstance(project, dict):
        return {}
    preflight = project.get("design_preflight")
    return preflight if isinstance(preflight, dict) else {}


def _design_memory_state(context: dict[str, Any]) -> dict[str, Any]:
    project = context.get("vibecad_project")
    if not isinstance(project, dict):
        return {}
    memory = project.get("design_memory")
    return memory if isinstance(memory, dict) else {}


def _design_memory_has_signal(memory: dict[str, Any]) -> bool:
    if not isinstance(memory, dict):
        return False
    for key, value in memory.items():
        if key in {"schema", "status", "created_at", "updated_at", "source"}:
            continue
        if value not in (None, "", [], {}):
            return True
    return False


def _design_memory_text_list(value: Any, limit: int = 8) -> list[str]:
    if value in (None, "", [], {}):
        return []
    raw_items = value if isinstance(value, list) else [value]
    result: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _design_memory_from_preflight_for_prompt(preflight: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(preflight, dict) or not preflight:
        return {}
    plan = preflight.get("final_build_plan")
    if not isinstance(plan, dict):
        plan = {}
    draft = preflight.get("design_intent_draft")
    if not isinstance(draft, dict):
        draft = {}
    assumptions: list[str] = []
    refinement = preflight.get("requirement_refinement")
    if isinstance(refinement, list):
        for item in refinement:
            if not isinstance(item, dict) or item.get("assumption") is not True:
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("model_answer") or "").strip()
            if question and answer:
                assumptions.append(f"{question}: {answer}")
            elif answer:
                assumptions.append(answer)
    for item in _design_preflight_answers(preflight):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question and answer:
            assumptions.append(f"{question}: {answer}")
    return {
        "user_intent": preflight.get("user_intent")
        or preflight.get("initial_user_prompt")
        or preflight.get("source_prompt"),
        "summary": plan.get("architecture") or draft.get("architecture"),
        "accepted_assumptions": assumptions,
        "components": plan.get("bodies") or draft.get("bodies_components"),
        "sketches_features": plan.get("sketches_features"),
        "interfaces": plan.get("interfaces") or draft.get("interfaces"),
        "envelopes": plan.get("envelopes") or draft.get("envelopes"),
        "mechanisms": plan.get("mechanisms") or draft.get("mechanisms"),
        "non_negotiable_product_behavior": draft.get("non_negotiable_geometry"),
        "critical_geometry": plan.get("critical_geometry"),
        "verification_checks": plan.get("verification_checks"),
        "construction_order": plan.get("construction_order"),
        "forbidden_shortcuts": plan.get("forbidden_shortcuts"),
        "current_obligation": (
            "Continue building or repairing CAD according to this accepted design "
            "memory. Do not restart requirement refinement unless the user changes "
            "the product."
        ),
    }


def _accepted_design_memory(context: dict[str, Any]) -> dict[str, Any]:
    memory = _design_memory_state(context)
    if _design_memory_has_signal(memory):
        return memory
    preflight = _design_preflight_state(context)
    if _design_preflight_build_ready(context):
        return _design_memory_from_preflight_for_prompt(preflight)
    return {}


def _accepted_design_memory_present(context: dict[str, Any]) -> bool:
    return _design_memory_has_signal(_accepted_design_memory(context))


def _design_preflight_build_ready(context: dict[str, Any]) -> bool:
    preflight = _design_preflight_state(context)
    return (
        preflight.get("schema") == DESIGN_PREFLIGHT_SCHEMA
        and preflight.get("status") == DESIGN_PREFLIGHT_BUILD_READY
        and isinstance(preflight.get("final_build_plan"), dict)
        and bool(preflight.get("final_build_plan"))
        and not _design_preflight_missing_fields(preflight)
    )


def _is_explicit_preflight_continuation_prompt(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", str(prompt or "").strip().lower())
    return text in DESIGN_PREFLIGHT_CONTINUATION_PROMPTS


def _design_preflight_required_for_prompt(
    prompt: str,
    context: dict[str, Any],
) -> bool:
    if _is_design_preflight_answer_prompt(prompt):
        return True
    if _accepted_design_memory_present(context):
        return False
    if not _design_preflight_build_ready(context):
        return True
    return False


def _is_design_preflight_answer_prompt(prompt: str) -> bool:
    return str(prompt or "").lstrip().lower().startswith("design preflight answers:")


def _design_preflight_initial_prompt(
    prompt: str,
    existing: dict[str, Any],
) -> str:
    clean_prompt = str(prompt or "").strip()
    existing_initial = str(existing.get("initial_user_prompt") or "").strip()
    if _is_design_preflight_answer_prompt(clean_prompt) and existing_initial:
        return existing_initial
    existing_source = str(existing.get("source_prompt") or "").strip()
    if _is_design_preflight_answer_prompt(clean_prompt) and existing_source:
        return existing_source
    return clean_prompt


def _merge_persisted_design_preflight_fields(
    payload: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(payload)
    for key in (
        "user_questions",
        "user_answer_rounds",
        "last_user_answers",
        "user_answers",
        "initial_user_prompt",
        "source_prompt",
    ):
        if merged.get(key) in (None, "", [], {}) and existing.get(key) not in (
            None,
            "",
            [],
            {},
        ):
            merged[key] = existing[key]
    if merged.get("user_answers") in (None, "", [], {}) and merged.get(
        "last_user_answers"
    ) not in (None, "", [], {}):
        merged["user_answers"] = merged["last_user_answers"]
    return merged


def _project_requirement_memory(context: dict[str, Any]) -> list[dict[str, Any]]:
    project = context.get("vibecad_project")
    if not isinstance(project, dict):
        return []
    memory = project.get("requirement_memory")
    return [item for item in memory if isinstance(item, dict)] if isinstance(memory, list) else []


def _requirement_memory_lines(context: dict[str, Any]) -> list[str]:
    memory = _project_requirement_memory(context)
    if not memory:
        return []
    visible = memory
    omitted = 0
    if len(memory) > 36:
        head = memory[:12]
        tail = memory[-24:]
        visible = head + tail
        omitted = len(memory) - len(visible)
    lines = [
        (
            "Persistent requirement memory (authoritative customer asks and "
            "corrections; never claim no requirements when this section is present):"
        )
    ]
    for index, item in enumerate(visible, start=1):
        role = str(item.get("role") or "user").upper()
        source = str(item.get("source") or "").strip()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        source_text = f" source={_trace_text(source, 32)}" if source else ""
        lines.append(
            f"{index}. {role}{source_text}: {_trace_text(content, 420)}"
        )
    if omitted:
        lines.insert(13, f"... {omitted} middle requirement-memory items omitted ...")
    return lines


def _design_preflight_user_questions_answered(preflight: dict[str, Any]) -> bool:
    if not isinstance(preflight, dict):
        return False
    questions = preflight.get("user_questions")
    if not isinstance(questions, list) or not questions:
        return False
    question_texts = {
        str(item.get("question") or "").strip()
        for item in questions
        if isinstance(item, dict) and str(item.get("question") or "").strip()
    }
    if not question_texts:
        return False
    answers = _design_preflight_answers(preflight)
    if not isinstance(answers, list) or not answers:
        return False
    answered_texts = {
        str(item.get("question") or "").strip()
        for item in answers
        if isinstance(item, dict)
        and str(item.get("question") or "").strip()
        and str(item.get("answer") or "").strip()
    }
    return question_texts.issubset(answered_texts)


def _design_preflight_answers(preflight: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(preflight, dict):
        return []
    merged: dict[str, dict[str, Any]] = {}

    def add_answers(raw_answers: Any) -> None:
        if not isinstance(raw_answers, list):
            return
        for item in raw_answers:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if not question or not answer:
                continue
            merged[question] = dict(item)

    add_answers(preflight.get("user_answers"))
    rounds = preflight.get("user_answer_rounds")
    if isinstance(rounds, list):
        for answer_round in rounds:
            if isinstance(answer_round, dict):
                add_answers(answer_round.get("answers"))
    add_answers(preflight.get("last_user_answers"))
    return list(merged.values())


def _design_preflight_existing_state_lines(preflight: dict[str, Any]) -> list[str]:
    if not isinstance(preflight, dict) or not preflight:
        return []
    lines = ["Existing design preflight state (authoritative):"]
    status = str(preflight.get("status") or "").strip()
    if status:
        lines.append(f"status: {_trace_text(status, 80)}")
    initial_prompt = str(preflight.get("initial_user_prompt") or "").strip()
    if initial_prompt:
        lines.append(
            f"initial_user_prompt: {_trace_text(initial_prompt, 260)}"
        )
    intent = str(preflight.get("user_intent") or "").strip()
    if intent:
        lines.append(f"user_intent: {_trace_text(intent, 220)}")

    refinement = preflight.get("requirement_refinement")
    if isinstance(refinement, list) and refinement:
        lines.append("requirement_refinement:")
        for index, item in enumerate(refinement[:12], start=1):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("model_answer") or "").strip()
            why = str(item.get("why_it_matters") or "").strip()
            assumption = item.get("assumption")
            parts = []
            if question:
                parts.append(f"q={_trace_text(question, 100)}")
            if answer:
                parts.append(f"model_answer={_trace_text(answer, 100)}")
            if isinstance(assumption, bool):
                parts.append(f"assumption={str(assumption).lower()}")
            if why:
                parts.append(f"why={_trace_text(why, 120)}")
            if parts:
                lines.append(f"- {index}. " + " | ".join(parts))

    questions = preflight.get("user_questions")
    if isinstance(questions, list) and questions:
        questions_answered = _design_preflight_user_questions_answered(preflight)
        lines.append(
            "answered_user_questions:"
            if questions_answered
            else "pending_user_questions:"
        )
        for index, item in enumerate(questions[:12], start=1):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            default = str(item.get("default_answer") or "").strip()
            options = item.get("options")
            option_values: list[str] = []
            if isinstance(options, list):
                for option in options[:8]:
                    if isinstance(option, dict):
                        value = option.get("answer") or option.get("label")
                    else:
                        value = option
                    clean = str(value or "").strip()
                    if clean:
                        option_values.append(_trace_text(clean, 50))
            parts = []
            if question:
                parts.append(f"q={_trace_text(question, 100)}")
            if default:
                parts.append(f"default={_trace_text(default, 80)}")
            if option_values:
                parts.append(f"options={';'.join(option_values)}")
            if parts:
                lines.append(f"- {index}. " + " | ".join(parts))

    answers = _design_preflight_answers(preflight)
    if isinstance(answers, list) and answers:
        lines.append(
            "user_answers: use these as binding requirements; do not ask the "
            "same answered questions again."
        )
        for index, item in enumerate(answers[:12], start=1):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            source = str(item.get("source") or "").strip()
            parts = []
            if question:
                parts.append(f"q={_trace_text(question, 100)}")
            if answer:
                parts.append(f"answer={_trace_text(answer, 100)}")
            if source:
                parts.append(f"source={_trace_text(source, 40)}")
            if parts:
                lines.append(f"- {index}. " + " | ".join(parts))

    draft = preflight.get("design_intent_draft")
    if isinstance(draft, dict) and draft:
        lines.append("design_intent_draft:")
        architecture = str(draft.get("architecture") or "").strip()
        if architecture:
            lines.append(f"- architecture={_trace_text(architecture, 160)}")
        for key in (
            "bodies_components",
            "interfaces",
            "envelopes",
            "mechanisms",
            "manufacturing_assumptions",
            "non_negotiable_geometry",
            "risks",
        ):
            values = draft.get(key)
            if isinstance(values, list) and values:
                cleaned = [
                    _trace_text(str(value), 70)
                    for value in values[:8]
                    if str(value).strip()
                ]
                if cleaned:
                    lines.append(f"- {key}={';'.join(cleaned)}")

    review = preflight.get("adversarial_review")
    if isinstance(review, dict) and review:
        lines.append("adversarial_review:")
        for key in ("blocking_issues", "criticisms", "required_revisions"):
            values = review.get(key)
            if isinstance(values, list) and values:
                cleaned = [
                    _trace_text(str(value), 90)
                    for value in values[:8]
                    if str(value).strip()
                ]
                if cleaned:
                    lines.append(f"- {key}={';'.join(cleaned)}")

    plan = preflight.get("final_build_plan")
    if isinstance(plan, dict) and plan:
        lines.append("final_build_plan:")
        architecture = str(plan.get("architecture") or "").strip()
        if architecture:
            lines.append(f"- architecture={_trace_text(architecture, 160)}")
        for key in (
            "bodies",
            "sketches_features",
            "interfaces",
            "envelopes",
            "mechanisms",
            "manufacturing_assumptions",
            "critical_geometry",
            "construction_order",
            "verification_checks",
            "forbidden_shortcuts",
        ):
            values = plan.get(key)
            if isinstance(values, list) and values:
                cleaned = [
                    _trace_text(str(value), 70)
                    for value in values[:8]
                    if str(value).strip()
                ]
                if cleaned:
                    lines.append(f"- {key}={';'.join(cleaned)}")
    return lines


def _design_preflight_prompt(prompt: str, context: dict[str, Any]) -> str:
    existing = _design_preflight_state(context)
    prior = "\n".join(_design_preflight_existing_state_lines(existing))
    return "\n\n".join(
        line
        for line in (
            "You are the VibeCAD design preflight author and reviewer.",
            (
                "Before any CAD geometry is created, refine the requirements, "
                "draft the design intent in writing, adversarially review that "
                "draft, revise it, and then either ask the user for blocking "
                "answers or produce the final build plan."
            ),
            (
                "The user-visible prose must start by restating the customer's "
                "intended outcome in concrete terms, then explain any blocking "
                "questions or accepted assumptions."
            ),
            (
                "You must answer your own refinement questions using the user's "
                "request, reference images, current CAD context, and explicit "
                "engineering assumptions. Ask the user only for choices that "
                "materially change the design and cannot be responsibly assumed."
            ),
            (
                "The sequence is mandatory: user intent -> requirement refinement "
                "with model answers/defaults -> design intent draft -> adversarial "
                "review of that draft -> revised final build plan. Do not call "
                "CAD tools. Do not start sketching. Do not produce placeholder "
                "geometry instructions."
            ),
            (
                "If an existing design preflight state is present, compare the "
                "current user request against it. If the request changes the "
                "target object, constraints, manufacturing assumptions, interfaces, "
                "mechanisms, or quality bar, revise the preflight JSON for the "
                "current request before CAD tools unlock. Reuse the existing final "
                "plan only when the current request is clearly just continuation "
                "of that same plan."
            ),
            (
                "The final plan must apply to any domain: aerospace, knives, "
                "automotive, drones, rockets, fixtures, enclosures, mechanisms. "
                "It must name how parts fit together, interfaces, load paths, "
                "materials/manufacturing assumptions, critical geometry, "
                "verification checks, and forbidden shortcuts."
            ),
            (
                "Do not embed JSON or machine state in your visible prose. "
                f"Submit the machine-readable preflight by calling "
                f"{DESIGN_PREFLIGHT_SUBMIT_TOOL}. Use status 'needs_user' only "
                "when user input is truly blocking; otherwise use status "
                "'build_ready'. After the tool call, respond to the user in "
                "plain prose."
            ),
            (
                "Required submit-tool shape: {"
                f"\"schema\":\"{DESIGN_PREFLIGHT_SCHEMA}\", "
                "\"status\":\"build_ready|needs_user\", "
                "\"user_intent\":\"...\", "
                "\"requirement_refinement\":[{\"question\":\"...\","
                "\"model_answer\":\"...\",\"assumption\":true|false,"
                "\"why_it_matters\":\"...\"}], "
                "\"user_questions\":[{\"question\":\"...\","
                "\"default_answer\":\"...\",\"options\":["
                "{\"label\":\"...\",\"answer\":\"...\"}],"
                "\"why_it_matters\":\"...\"}], "
                "\"design_intent_draft\":{"
                "\"architecture\":\"...\",\"bodies_components\":[],"
                "\"interfaces\":[],\"envelopes\":[],\"mechanisms\":[],"
                "\"manufacturing_assumptions\":[],"
                "\"non_negotiable_geometry\":[],\"risks\":[]}, "
                "\"adversarial_review\":{\"blocking_issues\":[],"
                "\"criticisms\":[],\"required_revisions\":[]}, "
                "\"final_build_plan\":{"
                "\"architecture\":\"...\",\"bodies\":[],\"interfaces\":[],"
                "\"sketches_features\":[],\"envelopes\":[],\"mechanisms\":[],"
                "\"manufacturing_assumptions\":[],\"critical_geometry\":[],"
                "\"construction_order\":[],"
                "\"verification_checks\":[],\"forbidden_shortcuts\":[]}}"
            ),
            prior,
            _prompt_with_conversation(prompt, context),
        )
        if line
    )


def _design_preflight_missing_fields(payload: dict[str, Any]) -> list[str]:
    required = [
        "schema",
        "status",
        "user_intent",
        "requirement_refinement",
    ]
    missing = [key for key in required if payload.get(key) in (None, "", [], {})]
    if payload.get("schema") != DESIGN_PREFLIGHT_SCHEMA:
        missing.append("schema=vibecad-design-preflight-v1")
    status = payload.get("status")
    if status not in {DESIGN_PREFLIGHT_BUILD_READY, DESIGN_PREFLIGHT_NEEDS_USER}:
        missing.append("status=build_ready|needs_user")

    refinement = payload.get("requirement_refinement")
    if isinstance(refinement, list):
        for index, item in enumerate(refinement, start=1):
            if not isinstance(item, dict):
                missing.append(f"requirement_refinement[{index}]")
                continue
            if item.get("question") in (None, "", [], {}):
                missing.append(f"requirement_refinement[{index}].question")
            if item.get("model_answer") in (None, "", [], {}):
                missing.append(f"requirement_refinement[{index}].model_answer")
            if not isinstance(item.get("assumption"), bool):
                missing.append(f"requirement_refinement[{index}].assumption")
            if item.get("why_it_matters") in (None, "", [], {}):
                missing.append(f"requirement_refinement[{index}].why_it_matters")
    elif refinement not in (None, "", [], {}):
        missing.append("requirement_refinement=list")

    if status == DESIGN_PREFLIGHT_NEEDS_USER:
        for key in ("design_intent_draft", "adversarial_review", "final_build_plan"):
            if payload.get(key) not in (None, "", [], {}):
                missing.append(f"{key}=not_allowed_for_needs_user")
        questions = payload.get("user_questions")
        if not isinstance(questions, list) or not questions:
            missing.append("user_questions")
        else:
            for index, question in enumerate(questions, start=1):
                if not isinstance(question, dict):
                    missing.append(f"user_questions[{index}]")
                    continue
                if question.get("question") in (None, "", [], {}):
                    missing.append(f"user_questions[{index}].question")
                if question.get("default_answer") in (None, "", [], {}):
                    missing.append(f"user_questions[{index}].default_answer")
                options = question.get("options")
                if not isinstance(options, list) or not options:
                    missing.append(f"user_questions[{index}].options")
                elif isinstance(options, list):
                    for option_index, option in enumerate(options, start=1):
                        if not isinstance(option, dict):
                            missing.append(
                                f"user_questions[{index}].options[{option_index}]"
                            )
                            continue
                        if option.get("label") in (None, "", [], {}):
                            missing.append(
                                f"user_questions[{index}].options[{option_index}].label"
                            )
                        if option.get("answer") in (None, "", [], {}):
                            missing.append(
                                f"user_questions[{index}].options[{option_index}].answer"
                            )
        return sorted(set(missing))

    if status == DESIGN_PREFLIGHT_BUILD_READY:
        questions = payload.get("user_questions")
        if questions not in (None, "", [], {}):
            if not isinstance(questions, list):
                missing.append("user_questions=list")
            elif not _design_preflight_user_questions_answered(payload):
                missing.append("user_questions.unanswered")
        answers = _design_preflight_answers(payload)
        answered_questions = {
            str(item.get("question") or "").strip()
            for item in answers
            if isinstance(item, dict)
            and str(item.get("question") or "").strip()
            and str(item.get("answer") or "").strip()
        } if isinstance(answers, list) else set()
        if isinstance(refinement, list):
            for index, item in enumerate(refinement, start=1):
                if not isinstance(item, dict) or item.get("assumption") is not False:
                    continue
                question = str(item.get("question") or "").strip()
                if not question or question not in answered_questions:
                    missing.append(
                        f"requirement_refinement[{index}].user_answer"
                    )
        for key in ("design_intent_draft", "adversarial_review", "final_build_plan"):
            if payload.get(key) in (None, "", [], {}):
                missing.append(key)
        draft = payload.get("design_intent_draft")
        if isinstance(draft, dict):
            for key in (
                "architecture",
                "bodies_components",
                "interfaces",
                "mechanisms",
                "manufacturing_assumptions",
                "non_negotiable_geometry",
                "risks",
            ):
                if draft.get(key) in (None, "", [], {}):
                    missing.append(f"design_intent_draft.{key}")
        review = payload.get("adversarial_review")
        if isinstance(review, dict):
            for key in ("blocking_issues", "required_revisions"):
                if not isinstance(review.get(key), list):
                    missing.append(f"adversarial_review.{key}")
            if review.get("criticisms") in (None, "", [], {}):
                missing.append("adversarial_review.criticisms")

    plan = payload.get("final_build_plan")
    if status == DESIGN_PREFLIGHT_BUILD_READY and isinstance(plan, dict):
        for key in (
            "architecture",
            "bodies",
            "sketches_features",
            "interfaces",
            "mechanisms",
            "manufacturing_assumptions",
            "critical_geometry",
            "construction_order",
            "verification_checks",
            "forbidden_shortcuts",
        ):
            if plan.get(key) in (None, "", [], {}):
                missing.append(f"final_build_plan.{key}")
    return sorted(set(missing))


def _persist_submitted_design_preflight(
    service: VibeCADService,
    payload: dict[str, Any],
    *,
    prompt: str,
    provider_name: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "Design preflight submission must be a JSON object.",
        }
    project_context = service.project_context()
    existing_preflight = (
        project_context.get("design_preflight")
        if isinstance(project_context, dict)
        else {}
    )
    if not isinstance(existing_preflight, dict):
        existing_preflight = {}
    submitted = _merge_persisted_design_preflight_fields(
        dict(payload),
        existing_preflight,
    )
    missing = _design_preflight_missing_fields(submitted)
    if missing:
        return {
            "ok": False,
            "error": (
                "Design preflight submission is incomplete; CAD tools remain locked. "
                f"Missing: {', '.join(missing)}"
            ),
            "preflight": submitted,
            "missing": missing,
        }
    submitted.setdefault("schema", DESIGN_PREFLIGHT_SCHEMA)
    initial_prompt = _design_preflight_initial_prompt(prompt, existing_preflight)
    submitted["initial_user_prompt"] = initial_prompt
    if _is_design_preflight_answer_prompt(prompt):
        submitted["source_prompt"] = (
            str(existing_preflight.get("source_prompt") or "").strip()
            or initial_prompt
        )
    else:
        submitted["source_prompt"] = prompt
    submitted["latest_preflight_prompt"] = prompt
    submitted["provider"] = provider_name
    try:
        saved = service.update_design_preflight(submitted)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Design preflight could not be persisted: {exc}",
            "preflight": submitted,
        }
    return {
        "ok": True,
        "preflight": saved.get("design_preflight") or submitted,
    }


def _make_design_preflight_tool_runner(
    service: VibeCADService,
    *,
    prompt: str,
    provider_name: str,
    submission_box: dict[str, Any],
) -> Callable[[str, str], dict[str, Any]]:
    def _run(tool_name: str, arguments_json: str = "{}") -> dict[str, Any]:
        if tool_name != DESIGN_PREFLIGHT_SUBMIT_TOOL:
            return {
                "ok": False,
                "error": (
                    "CAD tools are locked during design preflight. Submit the "
                    f"preflight with {DESIGN_PREFLIGHT_SUBMIT_TOOL}."
                ),
                "cad_write_tools_locked": True,
            }
        try:
            payload = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "error": f"Design preflight submission arguments are invalid JSON: {exc}",
                "cad_write_tools_locked": True,
            }
        result = _persist_submitted_design_preflight(
            service,
            payload,
            prompt=prompt,
            provider_name=provider_name,
        )
        if result.get("ok"):
            submission_box["preflight"] = result.get("preflight")
        return result

    return _run


def _ensure_design_preflight(
    service: VibeCADService,
    provider: BaseProvider,
    provider_name: str,
    prompt: str,
    context: dict[str, Any],
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
) -> tuple[bool, str, dict[str, Any]]:
    if not _design_preflight_required_for_prompt(prompt, context):
        return True, "", context
    preflight_context = dict(context)
    preflight_context["provider_tool_schemas"] = [dict(DESIGN_PREFLIGHT_SUBMIT_SCHEMA)]
    preflight_context["provider_tool_scope"] = {
        "stage": "design_preflight",
        "workbench": context.get("workbench"),
        "active_tool_count": 1,
        "cad_write_tools_locked": True,
    }
    preflight_context["vibecad_design_preflight"] = {
        "required": True,
        "cad_write_tools_locked": True,
        "submit_tool": DESIGN_PREFLIGHT_SUBMIT_TOOL,
        "existing_plan_requires_review": _design_preflight_build_ready(context),
    }
    _emit_progress(
        progress_callback,
        {
            "event": "design_preflight_started",
            "provider": provider_name,
        },
    )
    submission_box: dict[str, Any] = {}
    result = _run_provider_with_optional_cancellation(
        provider,
        _design_preflight_prompt(prompt, preflight_context),
        preflight_context,
        _make_design_preflight_tool_runner(
            service,
            prompt=prompt,
            provider_name=provider_name,
            submission_box=submission_box,
        ),
        cancellation_check,
        progress_callback,
    )
    raw_output = str(result.final_output or "").strip()
    displayed = raw_output
    preflight = (
        submission_box.get("preflight")
        if isinstance(submission_box.get("preflight"), dict)
        else {}
    )
    if not preflight:
        message = "\n\n".join(
            item
            for item in (
                displayed,
                (
                    "Design preflight failed: the provider did not submit "
                    f"{DESIGN_PREFLIGHT_SUBMIT_TOOL}; CAD tools remain locked."
                ),
            )
            if item
        )
        _emit_progress(
            progress_callback,
            {
                "event": "provider_turn_output",
                "provider": provider_name,
                "turn": 0,
                "text": message,
            },
        )
        return False, message, context
    status = preflight.get("status")
    next_context = service.provider_context_summary()
    _apply_provider_surface(
        service,
        next_context,
        service.active_workbench_name(),
    )
    message = displayed
    _emit_progress(
        progress_callback,
        {
            "event": "design_preflight_completed",
            "provider": provider_name,
            "status": status,
        },
    )
    if message:
        _emit_progress(
            progress_callback,
            {
                "event": "provider_turn_output",
                "provider": provider_name,
                "turn": 0,
                "text": message,
            },
        )
    return status == DESIGN_PREFLIGHT_BUILD_READY, message, next_context


def run_prompt(
    prompt: str,
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    max_provider_seconds: float | None = MAX_AUTONOMOUS_PROVIDER_SECONDS,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
) -> VibeCADResponse:
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("Prompt cannot be empty.")

    active_service = service or get_service()
    _emit_progress(progress_callback, {"event": "context_build_started"})
    entered_workspace: str | None = None
    active_workbench = active_service.active_workbench_name()
    context = active_service.provider_context_summary()
    _apply_provider_surface(
        active_service,
        context,
        active_workbench,
    )
    tool_trace: list[dict[str, Any]] = []
    context["vibecad_loop"] = _provider_loop_state(
        clean_prompt,
        context,
        tool_trace,
        turn=1,
    )
    _emit_progress(
        progress_callback,
        {
            "event": "context_build_completed",
            "workbench": context.get("workbench"),
            "active_workbench": active_workbench,
            "workspace_mode": context.get("vibecad_workspace", {}).get("mode"),
            "provider_tool_count": len(context["provider_tool_schemas"]),
        },
    )
    active_provider = provider or choose_provider(
        active_service, prefer_online=prefer_online
    )
    provider_name = active_provider.__class__.__name__
    started_at = time.monotonic()

    try:
        _inject_human_steering(context, _consume_steering(steering_check))
        preflight_ready, preflight_output, context = _ensure_design_preflight(
            active_service,
            active_provider,
            provider_name,
            clean_prompt,
            context,
            cancellation_check,
            progress_callback,
        )
        if not preflight_ready:
            final_output = preflight_output.strip()
            active_service.record_conversation_turn("user", clean_prompt)
            active_service.record_conversation_turn(
                "assistant",
                final_output,
                provider=provider_name,
                tool_trace=tool_trace,
                metadata={"design_preflight_waiting": True},
            )
            return VibeCADResponse(
                provider=provider_name,
                final_output=final_output,
                context=context,
                tool_trace=tool_trace,
            )
        context["vibecad_loop"] = _provider_loop_state(
            clean_prompt,
            context,
            tool_trace,
            turn=1,
        )
        active_workbench = active_service.active_workbench_name()
        entered_workspace = (
            str(context.get("workbench") or "").strip() or entered_workspace
        )
    except ProviderUnavailable as exc:
        final_output = (
            f"{provider_name} failed before returning a usable AI result: {exc}"
        )
        active_service.record_conversation_turn("user", clean_prompt)
        active_service.record_conversation_turn(
            "assistant",
            final_output,
            provider=provider_name,
            tool_trace=tool_trace,
            metadata={"provider_error": str(exc), "design_preflight": True},
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=context,
            tool_trace=tool_trace,
            error=str(exc),
        )

    provider_workbench = (
        str(context.get("workbench") or "").strip() or entered_workspace
    )
    tool_runner = make_provider_tool_runner(
        active_service,
        provider_workbench,
        tool_trace=tool_trace,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
    )

    try:
        provider_prompt = _prompt_with_conversation(clean_prompt, context)
        outputs: list[str] = [preflight_output] if preflight_output else []
        turn_index = 0
        while True:
            if cancellation_check is not None and cancellation_check():
                _emit_progress(
                    progress_callback,
                    {
                        "event": "provider_run_cancelled",
                        "provider": provider_name,
                        "turn": turn_index + 1,
                        "tool_count": len(tool_trace),
                    },
                )
                outputs.append("VibeCAD run stopped by user.")
                break
            if _provider_time_exceeded(started_at, max_provider_seconds):
                outputs.append(
                    "The autonomous provider loop reached the configured "
                    f"{max_provider_seconds:g} second limit before completion."
                )
                _emit_progress(
                    progress_callback,
                    {
                        "event": "provider_total_timeout",
                        "provider": provider_name,
                        "turn": turn_index + 1,
                        "elapsed_seconds": time.monotonic() - started_at,
                        "tool_count": len(tool_trace),
                    },
                )
                break
            steering_messages = _consume_steering(steering_check)
            if steering_messages:
                _inject_human_steering(context, steering_messages)
                _emit_progress(
                    progress_callback,
                    {
                        "event": "human_steering_consumed",
                        "message_count": len(steering_messages),
                        "turn": turn_index + 1,
                    },
                )
            existing_loop_state = context.get("vibecad_loop")
            if not (
                isinstance(existing_loop_state, dict)
                and int(existing_loop_state.get("turn", 0) or 0) == turn_index + 1
            ):
                context["vibecad_loop"] = _provider_loop_state(
                    clean_prompt,
                    context,
                    tool_trace,
                    turn=turn_index + 1,
                )
            _emit_progress(
                progress_callback,
                {
                    "event": "provider_turn_started",
                    "provider": provider_name,
                    "turn": turn_index + 1,
                    "tool_count": len(tool_trace),
                    "document_delta": context["vibecad_loop"].get("document_delta"),
                },
            )
            trace_count_before_turn = len(tool_trace)
            try:
                result = _run_provider_with_optional_cancellation(
                    active_provider,
                    provider_prompt,
                    context,
                    tool_runner,
                    cancellation_check,
                    progress_callback,
                )
            except ProviderUnavailable as exc:
                _emit_progress(
                    progress_callback,
                    {
                        "event": "provider_turn_failed",
                        "provider": provider_name,
                        "turn": turn_index + 1,
                        "error": str(exc),
                        "tool_count": len(tool_trace),
                    },
                )
                if len(tool_trace) <= trace_count_before_turn:
                    raise
                outputs.append(
                    "The provider made partial FreeCAD changes but did not "
                    f"return a final answer before stopping: {exc}"
                )
                entered_workspace = _workspace_session_from_trace(
                    tool_trace,
                    entered_workspace,
                )
                context = _refresh_provider_context(
                    active_service,
                    clean_prompt,
                    tool_trace,
                    turn_index + 2,
                    previous_context=context,
                    entered_workspace=entered_workspace,
                )
                if not _should_continue_autonomously(
                    clean_prompt,
                    outputs[-1],
                    active_service,
                    tool_trace,
                    turn_index,
                ):
                    break
                provider_prompt = _continuation_prompt(
                    clean_prompt,
                    outputs,
                    context,
                    tool_trace,
                )
                turn_index += 1
                continue
            raw_turn_output = str(result.final_output or "").strip()
            if raw_turn_output:
                _capture_reference_briefs_from_output(active_service, raw_turn_output)
            turn_output = _strip_reference_brief_json_blocks(raw_turn_output)
            if turn_output:
                outputs.append(turn_output)
                _emit_progress(
                    progress_callback,
                    {
                        "event": "provider_turn_output",
                        "provider": provider_name,
                        "turn": turn_index + 1,
                        "text": turn_output,
                    },
                )
            entered_workspace = _workspace_session_from_trace(
                tool_trace,
                entered_workspace,
            )
            post_turn_context = _refresh_provider_context(
                active_service,
                clean_prompt,
                tool_trace,
                turn_index + 2,
                previous_context=context,
                entered_workspace=entered_workspace,
            )
            _emit_progress(
                progress_callback,
                {
                    "event": "provider_turn_completed",
                    "provider": provider_name,
                    "turn": turn_index + 1,
                    "tool_count": len(tool_trace),
                },
            )
            if not _should_continue_autonomously(
                clean_prompt,
                turn_output,
                active_service,
                tool_trace,
                turn_index,
            ):
                break
            context = post_turn_context
            provider_prompt = _continuation_prompt(
                clean_prompt,
                outputs,
                context,
                tool_trace,
            )
            turn_index += 1
        final_output = "\n\n".join(outputs)
        active_service.record_conversation_turn("user", clean_prompt)
        active_service.record_conversation_turn(
            "assistant",
            final_output,
            provider=provider_name,
            tool_trace=tool_trace,
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=context,
            tool_trace=tool_trace,
        )
    except ProviderUnavailable as exc:
        final_output = (
            f"{provider_name} failed before returning a usable AI result: {exc}"
        )
        active_service.record_conversation_turn("user", clean_prompt)
        active_service.record_conversation_turn(
            "assistant",
            final_output,
            provider=provider_name,
            tool_trace=tool_trace,
            metadata={"provider_error": str(exc)},
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=context,
            tool_trace=tool_trace,
            error=str(exc),
        )


def _refresh_provider_context(
    service: VibeCADService,
    prompt: str | None = None,
    tool_trace: list[dict[str, Any]] | None = None,
    turn: int = 1,
    previous_context: dict[str, Any] | None = None,
    entered_workspace: str | None = None,
) -> dict[str, Any]:
    active_workbench = service.active_workbench_name()
    context = service.provider_context_summary()
    _apply_provider_surface(
        service,
        context,
        active_workbench,
        entered_workspace=entered_workspace,
    )
    if prompt is not None:
        context["vibecad_loop"] = _provider_loop_state(
            prompt,
            context,
            tool_trace or [],
            turn=turn,
            previous_context=previous_context,
        )
    return context


def _apply_provider_surface(
    service: VibeCADService,
    context: dict[str, Any],
    active_workbench: str | None,
    *,
    entered_workspace: str | None = None,
) -> None:
    try:
        context["vibecad_project"] = service.project_context()
    except Exception:
        context["vibecad_project"] = {}
    if entered_workspace:
        _apply_entered_workspace_provider_surface(
            service,
            context,
            active_workbench,
            entered_workspace,
        )
        return
    if active_workbench and get_tool_pack(active_workbench) is not None:
        _apply_entered_workspace_provider_surface(
            service,
            context,
            active_workbench,
            active_workbench,
        )
        return
    _apply_core_provider_surface(service, context, active_workbench)


def _apply_core_provider_surface(
    service: VibeCADService,
    context: dict[str, Any],
    active_workbench: str | None,
) -> None:
    schemas = provider_safe_tool_schemas(
        service,
        None,
        tool_names=PROVIDER_WORKSPACE_CONTROL_TOOLS,
    )
    full_active_count = len(
        provider_safe_tool_schemas(
            service,
            active_workbench,
            apply_workbench_allowlist=False,
        )
    )
    scope = {
        "workbench": None,
        "stage": "core",
        "active_tool_count": len(schemas),
        "full_workbench_tool_count": full_active_count,
        "omitted_tool_count": max(0, full_active_count - len(schemas)),
    }
    context["active_workbench"] = active_workbench
    context["workbench"] = None
    context["provider_tool_schemas"] = schemas
    context["provider_tool_scope"] = scope
    context["vibecad_workspace"] = {
        "mode": "core",
        "active_workbench": active_workbench,
        "entered_workbench": None,
    }


def _apply_entered_workspace_provider_surface(
    service: VibeCADService,
    context: dict[str, Any],
    active_workbench: str | None,
    entered_workspace: str,
) -> None:
    workspace = entered_workspace or active_workbench
    scope_spec = provider_tool_scope_for_context(service, workspace)
    schemas = provider_safe_tool_schemas(
        service,
        workspace,
        tool_names=scope_spec.tool_names,
    )
    full_schemas = provider_safe_tool_schemas(
        service,
        workspace,
        apply_workbench_allowlist=False,
    )
    scope = {
        "workbench": workspace,
        "stage": "entered_workspace",
        "active_tool_count": len(schemas),
        "full_workbench_tool_count": len(full_schemas),
        "omitted_tool_count": max(0, len(full_schemas) - len(schemas)),
    }
    context["active_workbench"] = active_workbench
    context["workbench"] = workspace
    context["provider_tool_schemas"] = schemas
    context["provider_tool_scope"] = scope
    context.update(service._provider_domain_context(workspace))
    context["vibecad_workspace"] = {
        "mode": "workspace",
        "active_workbench": active_workbench,
        "entered_workbench": workspace,
    }


def _workspace_session_from_trace(
    tool_trace: list[dict[str, Any]],
    current_workspace: str | None,
) -> str | None:
    workspace = current_workspace
    for item in tool_trace:
        if not isinstance(item, dict) or not item.get("ok"):
            continue
        tool_name = str(item.get("tool_name") or "")
        if tool_name not in {"core.enter_workspace", "core.activate_workbench"}:
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        required_next = result.get("required_next_action")
        if isinstance(required_next, dict) and required_next.get("next_turn_workbench"):
            workspace = str(required_next["next_turn_workbench"])
            continue
        for key in ("active_workbench", "workspace", "workbench"):
            if result.get(key):
                workspace = str(result[key])
                break
        if workspace is None and item.get("active_workbench"):
            workspace = str(item["active_workbench"])
    return workspace


def _effective_provider_workbench(
    service: VibeCADService,
    active_workbench: str | None,
) -> str | None:
    return active_workbench


def provider_tool_scope_for_context(
    service: VibeCADService,
    workbench: str | None,
) -> ProviderToolScope:
    """AI-native default scope; native FreeCAD packs are explicit opt-in."""
    if not service.native_freecad_tools_enabled():
        return ProviderToolScope(
            workbench=workbench,
            stage="ai_native_cad",
            reason="Native FreeCAD workbench tools are disabled in VibeCAD Tools preferences.",
            tool_names=set(CORE_PROVIDER_TOOLS),
        )
    pack = get_tool_pack(workbench)
    if pack is None:
        return ProviderToolScope(
            workbench=workbench,
            stage="ai_native_cad",
            reason="No native tool pack exists for this workbench.",
            tool_names=set(CORE_PROVIDER_TOOLS),
        )
    if not service.is_workbench_tool_pack_enabled(workbench):
        return ProviderToolScope(
            workbench=workbench,
            stage="ai_native_cad",
            reason="This native workbench tool pack is disabled in VibeCAD Tools preferences.",
            tool_names=set(CORE_PROVIDER_TOOLS),
        )
    return ProviderToolScope(
        workbench=workbench,
        stage="native_workbench_pack",
        reason="Native FreeCAD workbench tools are enabled for this specific workbench.",
        tool_names=(
            set(CORE_PROVIDER_TOOLS)
            | set(WORKBENCH_READ_TOOLS.get(workbench, set()))
            | set(pack.tool_names)
        ),
    )


def _provider_loop_state(
    prompt: str,
    context: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    turn: int,
    previous_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace_state = (
        context.get("vibecad_workspace", {}) if isinstance(context, dict) else {}
    )
    workspace_mode = (
        str(workspace_state.get("mode") or "")
        if isinstance(workspace_state, dict)
        else ""
    )
    recent_trace = [
        {
            "tool_name": item.get("tool_name"),
            "ok": bool(item.get("ok")),
            "active_workbench": item.get("active_workbench"),
            "result": item.get("result"),
        }
        for item in tool_trace[-6:]
        if isinstance(item, dict)
    ]
    document = context.get("document", {}) if isinstance(context, dict) else {}
    object_count = (
        int(document.get("object_count", 0) or 0) if isinstance(document, dict) else 0
    )
    screenshot = context.get("view_screenshot", {}) if isinstance(context, dict) else {}
    observation = (
        screenshot.get("visual_observation") if isinstance(screenshot, dict) else None
    )
    attention_flags = (
        list(observation.get("attention_flags") or [])
        if isinstance(observation, dict)
        else []
    )
    return {
        "turn": max(1, int(turn)),
        "workspace_mode": workspace_mode,
        "recent_tool_trace": recent_trace,
        "document_delta": _document_delta(previous_context, context),
        "document_object_count": object_count,
        "screenshot_captured": bool(
            isinstance(screenshot, dict) and screenshot.get("captured")
        ),
        "visual_attention_flags": attention_flags,
    }


def _document_delta(
    previous_context: dict[str, Any] | None,
    current_context: dict[str, Any],
    limit: int = 12,
) -> dict[str, Any]:
    previous_objects = _document_object_map(previous_context)
    current_objects = _document_object_map(current_context)
    previous_keys = set(previous_objects)
    current_keys = set(current_objects)
    created_keys = sorted(current_keys.difference(previous_keys))
    deleted_keys = sorted(previous_keys.difference(current_keys))
    changed = []
    for key in sorted(previous_keys.intersection(current_keys)):
        before = previous_objects[key]
        after = current_objects[key]
        changed_fields = [
            field
            for field in (
                "label",
                "type",
                "placement",
                "bound_box",
                "shape",
                "material",
            )
            if before.get(field) != after.get(field)
        ]
        if changed_fields:
            changed.append(
                {
                    "name": key,
                    "label": after.get("label") or key,
                    "fields": changed_fields,
                }
            )
    return {
        "available": previous_context is not None,
        "created": [
            _document_delta_item(current_objects[key], key)
            for key in created_keys[:limit]
        ],
        "deleted": [
            _document_delta_item(previous_objects[key], key)
            for key in deleted_keys[:limit]
        ],
        "changed": changed[:limit],
        "created_omitted": max(0, len(created_keys) - limit),
        "deleted_omitted": max(0, len(deleted_keys) - limit),
        "changed_omitted": max(0, len(changed) - limit),
        "before_object_count": len(previous_objects),
        "after_object_count": len(current_objects),
    }


def _document_object_map(context: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(context, dict):
        return {}
    document = context.get("document")
    if not isinstance(document, dict):
        return {}
    objects = document.get("objects")
    if not isinstance(objects, list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for item in objects:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or item.get("label") or "").strip()
        if key:
            mapped[key] = {
                "name": item.get("name"),
                "label": item.get("label"),
                "type": item.get("type"),
                "placement": item.get("placement"),
                "bound_box": item.get("bound_box"),
                "shape": item.get("shape"),
                "material": item.get("material"),
            }
    return mapped


def _document_delta_item(item: dict[str, Any], key: str) -> dict[str, Any]:
    return {
        "name": item.get("name") or key,
        "label": item.get("label") or item.get("name") or key,
        "type": item.get("type"),
    }


def _format_document_delta(delta: Any) -> str:
    if not isinstance(delta, dict) or not delta.get("available"):
        return "not available before the first inspected turn"
    parts = []
    for key, label in (
        ("created", "created"),
        ("deleted", "deleted"),
        ("changed", "changed"),
    ):
        items = delta.get(key)
        if not isinstance(items, list) or not items:
            continue
        names = [
            str(item.get("label") or item.get("name") or item)
            if isinstance(item, dict)
            else str(item)
            for item in items[:8]
        ]
        omitted = int(delta.get(f"{key}_omitted", 0) or 0)
        suffix = f" (+{omitted} more)" if omitted else ""
        parts.append(f"{label}: {', '.join(names)}{suffix}")
    if not parts:
        return (
            "no object-level changes "
            f"({delta.get('before_object_count', 0)} -> {delta.get('after_object_count', 0)} objects)"
        )
    return "; ".join(parts)


def _prompt_with_conversation(prompt: str, context: dict[str, Any]) -> str:
    session_preamble = _session_prompt_preamble(context)
    conversation_context = context.get("conversation", {})
    conversation = (
        conversation_context.get("conversation", [])
        if isinstance(conversation_context, dict)
        else []
    )
    if not conversation:
        return "\n".join(line for line in (session_preamble, f"U: {prompt}") if line)
    scope = (
        conversation_context.get("scope", {})
        if isinstance(conversation_context, dict)
        else {}
    )
    document = scope.get("document")
    file_path = scope.get("file_path")
    scope_parts: list[str] = []
    if document:
        scope_parts.append(f"d={document}")
    if file_path:
        scope_parts.append(f"f={file_path}")
    turn_lines: list[str] = []
    if scope_parts:
        header = (
            "Saved conversation context "
            f"({','.join(scope_parts)}; authoritative requirements and prior decisions):"
        )
    else:
        header = (
            "Saved conversation context "
            "(authoritative requirements and prior decisions):"
        )
    current_prompt = str(prompt or "").strip()
    last_index = len(conversation) - 1
    for index, item in enumerate(conversation):
        if not isinstance(item, dict):
            continue
        raw_role = str(item.get("role", "unknown")).lower()
        if raw_role.startswith("user"):
            role = "USER"
        elif raw_role.startswith("assistant"):
            role = "VIBECAD"
        elif raw_role.startswith("system"):
            role = "SYSTEM"
        else:
            role = raw_role.upper() or "UNKNOWN"
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if index == last_index and role == "USER" and content == current_prompt:
            continue
        turn_lines.append(f"{len(turn_lines) + 1}. {role}:\n{content}")
    lines = [header, *turn_lines] if turn_lines else []
    return "\n".join(
        line for line in (session_preamble, *lines, f"U: {prompt}") if line
    )


def _session_prompt_preamble(context: dict[str, Any]) -> str:
    workspace = context.get("vibecad_workspace", {})
    lines = []
    if isinstance(workspace, dict):
        workbench = workspace.get("entered_workbench") or workspace.get("active_workbench")
        if workbench:
            lines.append(f"W:{_prompt_workbench_label(workbench)}")
        elif workspace.get("mode") == "core":
            lines.append("W:core")
    requirement_lines = _requirement_memory_lines(context)
    if requirement_lines:
        lines.extend(requirement_lines)
    memory_lines = _accepted_design_memory_lines(context)
    if memory_lines:
        lines.extend(memory_lines)
    edit_lines = _active_edit_prompt_lines(context)
    if edit_lines:
        lines.extend(edit_lines)
    steering = context.get("human_steering", {})
    if isinstance(steering, dict) and steering.get("active_messages"):
        lines.append(
            "H: "
            + " | ".join(
                _trace_text(str(item), 80)
                for item in steering["active_messages"][-3:]
            )
        )
    error_lines = _report_error_prompt_lines(context)
    if error_lines:
        lines.extend(error_lines)
    reference_lines = _reference_image_lines(context)
    if reference_lines:
        lines.extend(reference_lines)
    return "\n".join(lines)


def _active_edit_prompt_lines(context: dict[str, Any]) -> list[str]:
    task = context.get("task_panel")
    if not isinstance(task, dict) or not task.get("edit_mode"):
        return []
    edit_object = task.get("edit_object")
    object_name = ""
    object_type = ""
    if isinstance(edit_object, dict):
        object_name = str(
            edit_object.get("label") or edit_object.get("name") or ""
        ).strip()
        object_type = str(edit_object.get("type") or "").strip()
    active_sketch = str(task.get("active_sketch") or object_name).strip()
    profile = task.get("profile_status")
    if not isinstance(profile, dict):
        return [
            (
                "EDIT: FreeCAD is currently editing "
                f"{_trace_text(object_name or active_sketch or 'an object', 80)}"
                + (f" ({_trace_text(object_type, 60)})" if object_type else "")
                + "; close or finish the edit mode before unrelated PartDesign/Assembly operations."
            )
        ]
    ready = bool(profile.get("ready_for_pad") or profile.get("ready_for_pocket"))
    dof = profile.get("degrees_of_freedom")
    closed = profile.get("closed_profile")
    faces = profile.get("face_count")
    reason = str(profile.get("reason") or "").strip()
    action = (
        "close with sketcher.close_sketch before creating PartDesign features"
        if ready
        else "repair/close the sketch before pad, pocket, dressup, or assembly operations"
    )
    return [
        (
            "EDIT: active sketch="
            f"{_trace_text(active_sketch or object_name or 'unknown', 80)} "
            f"ready={str(ready).lower()} closed={str(bool(closed)).lower()} "
            f"dof={dof if dof is not None else '?'} faces={faces if faces is not None else '?'}; "
            f"{action}; reason={_trace_text(reason, 180)}"
        )
    ]


def _accepted_design_memory_lines(context: dict[str, Any]) -> list[str]:
    memory = _accepted_design_memory(context)
    if not _design_memory_has_signal(memory):
        return []
    lines = [
        (
            "ACCEPTED DESIGN MEMORY (standing project memory, not a phase): "
            "use this as the controlling design intent for CAD choices."
        )
    ]
    intent = str(memory.get("user_intent") or "").strip()
    if intent:
        lines.append(f"User intent: {_trace_text(intent, 260)}")
    summary = str(memory.get("summary") or "").strip()
    if summary:
        lines.append(f"Accepted architecture: {_trace_text(summary, 260)}")

    def add_list(label: str, key: str, *, limit: int = 8, item_limit: int = 180) -> None:
        values = _design_memory_text_list(memory.get(key), limit=limit)
        if values:
            lines.append(
                f"{label}: "
                + " | ".join(_trace_text(item, item_limit) for item in values)
            )

    add_list("Accepted assumptions", "accepted_assumptions", limit=6, item_limit=160)
    add_list("Components", "components", limit=8, item_limit=150)
    add_list("Sketches/features", "sketches_features", limit=8, item_limit=160)
    add_list("Interfaces", "interfaces", limit=8, item_limit=170)
    add_list("Envelopes", "envelopes", limit=8, item_limit=180)
    add_list("Mechanisms", "mechanisms", limit=8, item_limit=180)
    add_list(
        "Non-negotiable product behavior",
        "non_negotiable_product_behavior",
        limit=10,
        item_limit=180,
    )
    add_list("Critical geometry", "critical_geometry", limit=8, item_limit=170)
    add_list("Verification checks", "verification_checks", limit=8, item_limit=180)
    add_list("Construction order", "construction_order", limit=8, item_limit=170)
    add_list("Forbidden shortcuts", "forbidden_shortcuts", limit=8, item_limit=170)
    add_list("Known failures", "known_failures", limit=8, item_limit=190)
    add_list("Corrections", "corrections", limit=8, item_limit=190)
    add_list("Open questions", "open_questions", limit=5, item_limit=170)
    obligation = str(memory.get("current_obligation") or "").strip()
    if obligation:
        lines.append(f"Current obligation: {_trace_text(obligation, 260)}")
    lines.append(
        "When the user corrects or changes the design, call "
        "core.update_design_memory during the normal loop; do not restart "
        "requirement refinement unless no accepted memory exists."
    )
    return lines


def _report_error_prompt_lines(context: dict[str, Any]) -> list[str]:
    report = context.get("report_view_errors")
    if not isinstance(report, dict):
        return []
    raw_errors = report.get("errors")
    if not isinstance(raw_errors, list) or not raw_errors:
        return []
    hard_errors = [
        str(item).strip()
        for item in raw_errors
        if str(item).strip() and _is_hard_geometry_failure(str(item))
    ]
    if not hard_errors:
        return []
    visible = " | ".join(_trace_text(item, 140) for item in hard_errors[-4:])
    return [
        (
            "ERRORS: unresolved hard FreeCAD geometry failures are present: "
            f"{visible}. Before any new feature/assembly/dressup operation, "
            "inspect the failed sketch/body, repair the upstream geometry, and "
            "verify the corrected object."
        )
    ]


def _prompt_workbench_label(value: Any) -> str:
    text = str(value or "")
    return text[: -len("Workbench")] if text.endswith("Workbench") else text


def _reference_image_lines(context: dict[str, Any]) -> list[str]:
    references = context.get("reference_images", {})
    if not isinstance(references, dict):
        return []
    images = references.get("images", [])
    if not isinstance(images, list) or not images:
        return []
    lines = []
    for index, entry in enumerate(images, start=1):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("id") or f"image {index}")
        label = str(entry.get("label") or "").strip()
        suffixes = [label] if label else []
        brief_summary = _reference_brief_summary(entry.get("visual_brief"))
        if brief_summary:
            suffixes.append(f"b={brief_summary}")
        delivery = entry.get("provider_delivery")
        if isinstance(delivery, dict) and delivery.get("available") is False:
            reason = str(delivery.get("reason") or "not delivered").strip()
            if reason:
                suffixes.append(f"miss={_trace_text(reason, 60)}")
        suffix = f"|{'|'.join(suffixes)}" if suffixes else ""
        lines.append(f"R{index}:{_trace_text(name, 40)}{suffix}")
    return lines


def _reference_brief_summary(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    summary = str(raw.get("summary") or "").strip()
    if summary:
        return _trace_text(summary, 96)
    parts: list[str] = []
    object_type = str(raw.get("object_type") or "").strip()
    if object_type:
        parts.append(object_type)
    for key in ("counts_patterns", "must_preserve", "unknown_dimensions"):
        values = raw.get(key)
        if isinstance(values, list):
            cleaned = [str(item).strip() for item in values if str(item).strip()]
            if cleaned:
                label = {
                    "counts_patterns": "c",
                    "must_preserve": "p",
                    "unknown_dimensions": "?",
                }[key]
                parts.append(f"{label}:{','.join(cleaned[:3])}")
    return _trace_text(";".join(parts), 112)


def _reference_brief_payload_matches(
    text: str,
) -> list[tuple[dict[str, Any], tuple[int, int]]]:
    matches: list[tuple[dict[str, Any], tuple[int, int]]] = []
    if not text:
        return matches
    decoder = json.JSONDecoder()
    marker_pattern = re.compile(re.escape(REFERENCE_BRIEF_MARKER), re.IGNORECASE)
    for marker in marker_pattern.finditer(text):
        cursor = marker.end()
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        span_end: int | None = None
        payload_text = ""
        payload_offset = cursor
        if text.startswith("```", cursor):
            cursor += 3
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if text[cursor : cursor + 4].lower() == "json":
                cursor += 4
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            fence_end = text.find("```", cursor)
            if fence_end < 0:
                continue
            payload_text = text[cursor:fence_end]
            payload_offset = cursor
            span_end = fence_end + 3
            span_start = marker.start()
            while span_start > 0 and text[span_start - 1] in " \t":
                span_start -= 1
            # If the marker began on a line by itself, remove that line break too.
            if span_start > 0 and text[span_start - 1] == "\n":
                span_start -= 1
        else:
            payload_text = text[cursor:]
            payload_offset = cursor
            span_start = marker.start()
        try:
            payload, consumed = decoder.raw_decode(payload_text)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if span_end is None:
            span_end = payload_offset + consumed
            while span_end < len(text) and text[span_end] in " \t":
                span_end += 1
            if span_end < len(text) and text[span_end] == "\n":
                span_end += 1
        matches.append((payload, (span_start, span_end)))
    return matches


def _extract_reference_brief_payloads(text: str) -> list[dict[str, Any]]:
    return [payload for payload, _span in _reference_brief_payload_matches(text)]


def _strip_reference_brief_json_blocks(text: str) -> str:
    if not text:
        return ""
    stripped = text
    for _payload, (start, end) in reversed(_reference_brief_payload_matches(text)):
        stripped = stripped[:start] + stripped[end:]
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def _capture_reference_briefs_from_output(
    service: VibeCADService, text: str
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    for payload in _extract_reference_brief_payloads(text):
        raw_ids = payload.get("reference_ids") or payload.get("references") or []
        reference_ids = (
            [str(item).strip() for item in raw_ids if str(item).strip()]
            if isinstance(raw_ids, list)
            else []
        )
        brief = payload.get("brief") if isinstance(payload.get("brief"), dict) else payload
        if not isinstance(brief, dict):
            continue
        try:
            result = service.update_reference_visual_brief(reference_ids, brief)
        except Exception:
            continue
        if isinstance(result, dict) and result.get("ok"):
            captured.append(result)
    return captured


def _prompt_type_label(value: Any) -> str:
    text = str(value or "")
    if text == "Sketcher::SketchObject":
        return "Sketch"
    if "::" in text:
        prefix, name = text.split("::", 1)
        if prefix in {"PartDesign", "Part", "Assembly", "Surface", "TechDraw"} and name:
            return name
    return text


def _prompt_tool_label(value: Any) -> str:
    text = str(value or "")
    return provider_function_name(text, text)


def _continuation_prompt(
    prompt: str,
    outputs: list[str],
    context: dict[str, Any],
    tool_trace: list[dict[str, Any]],
) -> str:
    session_preamble = _session_prompt_preamble(context)
    objects = context.get("document", {}).get("objects", [])
    object_lines = [
        f"{_trace_text(item.get('label') or item.get('name'), 40)}:{_prompt_type_label(item.get('type'))}"
        for item in objects[-6:]
        if isinstance(item, dict)
    ]
    trace_lines = []
    for item in tool_trace[-3:]:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        details = []
        if isinstance(result, dict):
            if result.get("workspace_handoff"):
                details.append(f"wh={result.get('workspace_handoff')}")
            if result.get("error"):
                details.append(f"err={_trace_text(result.get('error'), 80)}")
            for key, label in (
                ("feature", "feat"),
                ("label", "lbl"),
                ("active_sketch", "sk"),
            ):
                if result.get(key):
                    details.append(f"{label}={_trace_text(result.get(key), 48)}")
        suffix = f"({';'.join(details)})" if details else ""
        trace_lines.append(
            f"{_prompt_tool_label(item.get('tool_name'))}:{'ok' if item.get('ok') else 'fail'}{suffix}"
        )
    screenshot = context.get("view_screenshot", {})
    visual = (
        screenshot.get("visual_observation", {}) if isinstance(screenshot, dict) else {}
    )
    visual_lines = []
    if isinstance(visual, dict) and visual.get("available"):
        visual_lines.extend(
            [
                f"f:{visual.get('attention_flags', [])}",
                f"v:{_trace_text(visual.get('inspection_summary'), 80)}",
            ]
        )
    elif isinstance(screenshot, dict) and screenshot.get("captured"):
        visual_lines.append("shot:no_vision")
    lines = []
    if session_preamble:
        lines.append(session_preamble)
    lines.append(f"U: {prompt}")
    if trace_lines:
        lines.extend(["T:", "\n".join(trace_lines)])
    if object_lines:
        lines.extend(["O:", "\n".join(object_lines)])
    if visual_lines:
        lines.extend(["V:", "\n".join(visual_lines)])
    return "\n".join(lines)


def _should_continue_autonomously(
    prompt: str,
    output: str,
    service: VibeCADService,
    tool_trace: list[dict[str, Any]],
    turn_index: int,
) -> bool:
    if (
        MAX_AUTONOMOUS_PROVIDER_TURNS is not None
        and turn_index >= MAX_AUTONOMOUS_PROVIDER_TURNS - 1
    ):
        return False
    if _tool_batch_workspace_handoff_reached(tool_trace):
        return True
    return False


def _tool_batch_workspace_handoff_reached(tool_trace: list[dict[str, Any]]) -> bool:
    for item in reversed(tool_trace):
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("workspace_handoff") in {"workbench_switch", "workspace_entry"}:
            return True
        error = str(result.get("error", "")).lower()
        if "workspace handoff" in error:
            return True
        if item.get("ok"):
            return False
    return False


def provider_safe_tool_schemas(
    service: VibeCADService,
    workbench: str | None = None,
    tool_names: set[str] | None = None,
    *,
    apply_workbench_allowlist: bool = True,
) -> list[dict[str, Any]]:
    schemas = []
    for tool_name in service.registry.names():
        if tool_names is not None and tool_name not in tool_names:
            continue
        if is_provider_safe_tool(
            service,
            tool_name,
            workbench,
            apply_workbench_allowlist=apply_workbench_allowlist,
        ):
            schemas.append(
                _lean_provider_tool_schema(
                    service.registry.get(tool_name).to_schema(active_workbench=workbench)
                )
            )
    return schemas


def _lean_provider_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": schema.get("name"),
        "parameters": _lean_provider_schema_value(schema.get("parameters")),
    }


def _lean_provider_schema_value(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            compact = _lean_provider_schema_value(item)
            if key == "required" and compact == []:
                continue
            if key in {
                "description",
                "default",
                "examples",
                "maximum",
                "maxItems",
                "minimum",
                "minItems",
                "title",
            }:
                continue
            result[str(key)] = compact
        return result
    if isinstance(value, list):
        return [_lean_provider_schema_value(item) for item in value]
    return value


def _policy_tool_block(
    service: VibeCADService,
    tool: Any,
    live_workbench: str | None,
) -> dict[str, Any] | None:
    if tool.name in DOCUMENT_MANAGEMENT_TOOLS:
        return {
            "ok": False,
            "error": (
                f"{tool.name} is not available to the autonomous CAD loop. "
                "Document creation/opening must be an explicit user/UI action."
            ),
            "recoverable": True,
        }
    return None


def _active_sketch_edit_tool_block(
    service: VibeCADService,
    tool: Any,
    tool_name: str,
    live_workbench: str | None,
) -> dict[str, Any] | None:
    if tool.safety in {SafetyLevel.READ, SafetyLevel.VIEW}:
        return None
    if tool_name in SKETCH_EDIT_ALLOWED_TOOLS:
        return None
    try:
        task = service.task_panel_summary()
    except Exception as exc:
        return {
            "ok": False,
            "error": (
                "Could not verify whether a sketch is currently open before "
                f"executing {tool_name}: {exc}"
            ),
            "retry_same_call": False,
            "recoverable": True,
            "required_next_action": {
                "tool": "core.get_task_panel",
                "why": "Verify edit mode before running write tools.",
            },
            "active_workbench": live_workbench,
            "tool_workbench": tool.workbench,
        }
    if (
        not isinstance(task, dict)
        or not task.get("edit_mode")
        or not task.get("active_sketch")
    ):
        return None
    profile_status = (
        task.get("profile_status")
        if isinstance(task.get("profile_status"), dict)
        else {}
    )
    ready = bool(
        profile_status.get("ready_for_pad")
        or profile_status.get("ready_for_pocket")
    )
    reason = str(profile_status.get("reason") or "").strip()
    return {
        "ok": False,
        "error": (
            f"FreeCAD is still editing sketch {task.get('active_sketch')}. "
            f"Close or repair that sketch before running {tool_name}."
        ),
        "retry_same_call": False,
        "recoverable": True,
        "active_workbench": live_workbench,
        "tool_workbench": tool.workbench,
        "active_sketch": task.get("active_sketch"),
        "profile_status": profile_status,
        "task_panel": task,
        "required_next_action": {
            "tool": "sketcher.close_sketch" if ready else "sketcher.inspect_sketch",
            "arguments": {"sketch_name": task.get("active_sketch")},
            "why": (
                "The sketch is ready; close edit mode before PartDesign features."
                if ready
                else (
                    "The active sketch is not ready for PartDesign features"
                    + (f": {reason}" if reason else ".")
                )
            ),
        },
    }


def _consume_steering(steering_check: SteeringCheck | None) -> list[str]:
    if steering_check is None:
        return []
    try:
        messages = steering_check()
    except Exception:
        return []
    if not isinstance(messages, list):
        return []
    return [str(item).strip() for item in messages if str(item).strip()]


def _inject_human_steering(context: dict[str, Any], messages: list[str]) -> None:
    if not messages:
        return
    existing = context.get("human_steering")
    if not isinstance(existing, dict):
        existing = {}
    applied = list(existing.get("active_messages") or [])
    applied.extend(messages)
    existing["active_messages"] = applied[-12:]
    context["human_steering"] = existing


def _attach_steering_to_tool_result(
    result: dict[str, Any],
    steering_check: SteeringCheck | None,
    progress_callback: ProgressCallback | None,
) -> None:
    messages = _consume_steering(steering_check)
    if not messages:
        return
    result["human_steering"] = {"messages": messages}
    _emit_progress(
        progress_callback,
        {
            "event": "human_steering_consumed",
            "message_count": len(messages),
        },
    )


def make_provider_tool_runner(
    service: VibeCADService,
    workbench: str | None = None,
    tool_trace: list[dict[str, Any]] | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
):
    provider_workbench = str(workbench or "").strip() or None

    def _run(tool_name: str, arguments_json: str = "{}") -> dict[str, Any]:
        nonlocal provider_workbench
        actual_workbench = service.active_workbench_name()
        live_workbench = provider_workbench or actual_workbench
        trace_entry: dict[str, Any] = {
            "tool_name": tool_name,
            "active_workbench": live_workbench,
            "gui_workbench": actual_workbench,
            "arguments_json": _trace_text(arguments_json or "{}"),
            "ok": False,
        }

        def _finalize_result(
            result: dict[str, Any],
            *,
            attach_steering: bool = True,
        ) -> dict[str, Any]:
            if attach_steering:
                _attach_steering_to_tool_result(
                    result, steering_check, progress_callback
                )
            _record_tool_trace(tool_trace, trace_entry, result, progress_callback)
            return result

        if cancellation_check is not None and cancellation_check():
            result = {
                "ok": False,
                "error": "VibeCAD run stopped by user before executing tool.",
                "cancelled": True,
                "active_workbench": live_workbench,
            }
            return _finalize_result(result, attach_steering=False)
        try:
            tool = service.registry.get(tool_name)
        except KeyError:
            result = {"ok": False, "error": f"Unknown VibeCAD tool: {tool_name}"}
            return _finalize_result(result)

        trace_entry["safety"] = tool.safety.value
        trace_entry["tool_workbench"] = tool.workbench

        if not is_provider_tool_kind_allowed(tool.safety, tool.name):
            result = {
                "ok": False,
                "error": (
                    "Tool is not exposed to the AI loop because VibeCAD actions "
                    f"must drive human-equivalent FreeCAD commands: {tool_name}"
                ),
                "safety": tool.safety.value,
                "active_workbench": live_workbench,
                "tool_workbench": tool.workbench,
            }
            return _finalize_result(result)

        policy_block = _policy_tool_block(service, tool, live_workbench)
        if policy_block is not None:
            return _finalize_result(policy_block)

        sketch_edit_block = _active_sketch_edit_tool_block(
            service,
            tool,
            tool_name,
            live_workbench,
        )
        if sketch_edit_block is not None:
            return _finalize_result(sketch_edit_block)

        try:
            args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            result = {"ok": False, "error": f"Invalid JSON arguments: {exc}"}
            return _finalize_result(result)
        if not isinstance(args, dict):
            result = {"ok": False, "error": "Tool arguments must be a JSON object."}
            return _finalize_result(result)
        arguments_signature = _tool_call_signature(tool_name, args)
        trace_entry["arguments_signature"] = arguments_signature
        repeated_block = _repeated_hard_blocked_tool_result(
            tool_trace,
            tool_name,
            arguments_signature,
        )
        if repeated_block is not None:
            result = {
                "ok": False,
                "error": (
                    "This exact tool call already failed and was marked "
                    "non-repeatable. Do not retry it unchanged; repair or "
                    "inspect the referenced model state, then call the tool "
                    "with corrected arguments."
                ),
                "blocked_tool": tool_name,
                "blocked_arguments_json": _trace_text(arguments_json or "{}"),
                "previous_failure": repeated_block,
                "retry_same_call": False,
                "recoverable": True,
                "active_workbench": live_workbench,
                "tool_workbench": tool.workbench,
            }
            return _finalize_result(result)

        allowlist = _provider_tool_allowlist_for_workbench(service, live_workbench)
        if (
            (allowlist is not None and tool_name not in allowlist)
            or not _is_tool_available_for_provider_context(service, tool, live_workbench)
        ):
            result = {
                "ok": False,
                "error": (
                    f"Tool is not available for the selected workspace: {tool_name}"
                ),
                "selected_workbench": live_workbench,
                "active_workbench": actual_workbench,
                "tool_workbench": tool.workbench,
                "recoverable": True,
                "required_next_action": None,
            }
            return _finalize_result(result)

        if not service.is_tool_enabled_for_provider(tool, live_workbench):
            script_mode = False
            try:
                script_mode = bool(service.build_script_mode_enabled())
            except Exception:
                script_mode = False
            if tool_name == "model.build_from_script" and not script_mode:
                error_text = (
                    "model.build_from_script is disabled. Script mode is an "
                    "opt-in preference; use the structured modeling tools "
                    "instead."
                )
            elif script_mode:
                error_text = (
                    f"Structured write tools are disabled in script mode: "
                    f"{tool_name}. Author geometry through "
                    "model.build_from_script instead."
                )
            else:
                error_text = (
                    f"Tool pack is disabled for the active workbench: {tool_name}"
                )
            result = {
                "ok": False,
                "error": error_text,
                "active_workbench": live_workbench,
                "tool_workbench": tool.workbench,
            }
            return _finalize_result(result)

        try:
            if cancellation_check is not None and cancellation_check():
                result = {
                    "ok": False,
                    "error": "VibeCAD run stopped by user before executing tool.",
                    "cancelled": True,
                    "active_workbench": live_workbench,
                    "tool_workbench": tool.workbench,
                }
                return _finalize_result(result, attach_steering=False)
            activation = _ensure_tool_execution_workbench(
                service,
                tool,
                actual_workbench,
                live_workbench,
            )
            if activation is not None:
                if not activation.get("ok"):
                    result = {
                        "ok": False,
                        "error": activation.get("error")
                        or (
                            "Could not activate the FreeCAD workbench required "
                            f"to execute {tool_name}."
                        ),
                        "active_workbench": actual_workbench,
                        "selected_workbench": live_workbench,
                        "tool_workbench": tool.workbench,
                        "recoverable": True,
                    }
                    return _finalize_result(result)
                actual_workbench = str(
                    activation.get("active_workbench")
                    or activation.get("active")
                    or tool.workbench
                    or ""
                ).strip() or actual_workbench
                trace_entry["gui_workbench"] = actual_workbench
                _emit_progress(
                    progress_callback,
                    {
                        "event": "tool_execution_workbench_activated",
                        "tool_name": tool_name,
                        "active_workbench": actual_workbench,
                        "selected_workbench": live_workbench,
                        "tool_workbench": tool.workbench,
                    },
                )
            payload = service.registry.call(tool_name, **args)
            result = {
                "ok": not (isinstance(payload, dict) and payload.get("ok") is False),
                "result": payload,
            }
            hard_failure = _result_hard_geometry_failure_text(payload)
            if hard_failure:
                result.update(_hard_geometry_failure_result(hard_failure))
            if result["ok"] and tool_name in {
                "core.activate_workbench",
                "core.enter_workspace",
            }:
                requested_workbench = str(args.get("name", "") or "").strip()
                resolved_workbench = requested_workbench
                if isinstance(payload, dict):
                    resolved_workbench = str(
                        payload.get("workspace")
                        or payload.get("active_workbench")
                        or requested_workbench
                    ).strip()
                should_handoff = bool(resolved_workbench) and (
                    tool_name == "core.enter_workspace"
                    or resolved_workbench != live_workbench
                )
                if should_handoff:
                    handoff_name = (
                        "workspace_entry"
                        if tool_name == "core.enter_workspace"
                        else "workbench_switch"
                    )
                    provider_workbench = resolved_workbench
                    trace_entry["active_workbench"] = resolved_workbench
                    result["workspace_handoff"] = handoff_name
                    _emit_progress(
                        progress_callback,
                        {
                            "event": "tool_workspace_handoff_reached",
                            "tool_name": tool_name,
                            "active_workbench": resolved_workbench,
                        },
                    )
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            if _is_hard_geometry_failure(str(exc)):
                result.update(_hard_geometry_failure_result(str(exc)))
        return _finalize_result(result)
    return _run


def _provider_time_exceeded(
    started_at: float, max_provider_seconds: float | None
) -> bool:
    return (
        max_provider_seconds is not None
        and max_provider_seconds > 0
        and time.monotonic() - started_at >= max_provider_seconds
    )


def _is_tool_available_in_live_context(
    service: VibeCADService,
    tool: Any,
    workbench: str | None,
) -> bool:
    if tool.is_available_for(workbench):
        return True
    if workbench == "PartDesignWorkbench" and _is_partdesign_sketcher_tool(tool.name):
        try:
            return tool.name == "sketcher.inspect_sketch" or bool(
                service.sketcher_summary().get("found")
            )
        except Exception:
            return False
    return False


def _tool_call_signature(tool_name: str, args: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        encoded = str(args)
    return f"{tool_name}:{encoded}"


def _repeated_hard_blocked_tool_result(
    tool_trace: list[dict[str, Any]] | None,
    tool_name: str,
    arguments_signature: str,
) -> dict[str, Any] | None:
    if not tool_trace:
        return None
    for item in reversed(tool_trace[-30:]):
        if not isinstance(item, dict):
            continue
        if item.get("ok") and item.get("safety") in {
            SafetyLevel.SAFE_WRITE.value,
            SafetyLevel.WRITE.value,
            SafetyLevel.DESTRUCTIVE.value,
        }:
            return None
        if (
            item.get("tool_name") == tool_name
            and item.get("arguments_signature") == arguments_signature
        ):
            result = item.get("result")
            if isinstance(result, dict) and result.get("retry_same_call") is False:
                return result
            return None
    return None


def _hard_geometry_failure_result(error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(error or "Hard FreeCAD geometry failure."),
        "retry_same_call": False,
        "recoverable": True,
        "required_state_before_retry": (
            "Repair or rebuild the invalid upstream geometry, recompute "
            "successfully, then retry with corrected arguments. Do not repeat "
            "the same call unchanged."
        ),
        "required_next_action": {
            "tool": "core.get_report_view_errors",
            "arguments": {"include_stale": True},
            "why": (
                "Read the hard FreeCAD errors, then inspect the failed sketch, "
                "feature, body, or referenced subelement before another write."
            ),
        },
    }


def _result_hard_geometry_failure_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("error", "reason", "message"):
            text = str(value.get(key) or "").strip()
            if text and _is_hard_geometry_failure(text):
                return text
        report = value.get("report_view_errors")
        if isinstance(report, dict):
            for item in report.get("errors") or []:
                text = str(item or "").strip()
                if text and _is_hard_geometry_failure(text):
                    return text
        transaction = value.get("transaction")
        if isinstance(transaction, dict):
            text = _result_hard_geometry_failure_text(transaction)
            if text:
                return text
        for key in ("verification", "feature_effect", "body_shape_after", "feature_shape"):
            nested = value.get(key)
            if isinstance(nested, dict):
                text = _result_hard_geometry_failure_text(nested)
                if text:
                    return text
        return ""
    if isinstance(value, list):
        for item in value:
            text = _result_hard_geometry_failure_text(item)
            if text:
                return text
    return ""


def _is_hard_geometry_failure(error: str) -> bool:
    text = str(error or "").lower()
    return any(
        marker in text
        for marker in (
            "shape is invalid",
            "brep_api",
            "command not done",
            "invalid edge link",
            "result shape is null",
            "failed to make face",
            "wire is not closed",
            "links are out of scope",
            "out of scope links",
            "graph must be a dag",
            "tip shape is empty",
            "shape is empty",
        )
    )


def _ensure_tool_execution_workbench(
    service: VibeCADService,
    tool: Any,
    actual_workbench: str | None,
    provider_workbench: str | None,
) -> dict[str, Any] | None:
    target = str(tool.workbench or "").strip()
    if not target or target == actual_workbench:
        return None
    if target == "SketcherWorkbench" and provider_workbench == "PartDesignWorkbench":
        if actual_workbench == "PartDesignWorkbench":
            return None
    try:
        result = service.activate_workbench(target)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    active = str(result.get("active") or result.get("active_workbench") or "").strip()
    ok = bool(result.get("activated")) and (not active or active == target)
    return {
        "ok": ok,
        "active_workbench": active or target,
        **({"error": result["error"]} if result.get("error") else {}),
    }


def _trace_text(value: Any, limit: int = 500) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _summary_value(value: Any, limit: int = 1200) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
        if len(text) <= limit:
            return value
        return _trace_text(text, limit)
    return _trace_text(value, limit)


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"ok": bool(result.get("ok"))}
    for key in (
        "status",
        "error",
        "workspace_handoff",
        "blocked_tool",
        "blocked_arguments_json",
        "required_next_action",
        "next_action",
        "active_workbench",
        "tool_workbench",
        "recoverable",
        "retry_same_call",
        "executed",
        "mutated_document",
        "rolled_back_feature",
        "selected_workbench",
        "gui_workbench",
    ):
        if key in result:
            summary[key] = _summary_value(result[key])
    payload = result.get("result")
    if isinstance(payload, dict):
        for key in (
            "id",
            "title",
            "status",
            "safety",
            "active_workbench",
            "workbench",
            "assembly",
            "assembly_label",
            "component",
            "component_label",
            "components",
            "components_added",
            "missing_components",
            "already_present",
            "assembly_summary",
            "active_body",
            "active_sketch",
            "active_feature",
            "next_action",
            "required_next_action",
            "profile_status",
            "next_actions",
            "feature_shape",
            "body_shape_before",
            "body_shape_after",
            "body_shape_delta",
            "feature_effect",
            "rolled_back_feature",
            "body_shape_after_rollback",
            "recoverable",
            "retry_same_call",
            "required_state_before_retry",
            "error",
        ):
            if key in payload:
                summary[key] = _summary_value(payload[key])
    transaction = result.get("transaction")
    if not isinstance(transaction, dict) and isinstance(payload, dict):
        transaction = payload.get("transaction")
    if isinstance(transaction, dict):
        for key in ("error", "verification", "report_view_errors", "document_delta"):
            if key in transaction:
                summary[f"transaction_{key}"] = _summary_value(transaction[key])
    return summary


def _record_tool_trace(
    tool_trace: list[dict[str, Any]] | None,
    trace_entry: dict[str, Any],
    result: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> None:
    entry = dict(trace_entry)
    entry["ok"] = bool(result.get("ok"))
    entry["result"] = _result_summary(result)
    if tool_trace is not None:
        tool_trace.append(entry)
    _emit_progress(
        progress_callback,
        {
            "event": "tool_call_completed",
            "tool_name": entry.get("tool_name"),
            "ok": entry.get("ok"),
            "result": entry.get("result"),
            "active_workbench": entry.get("active_workbench"),
            "safety": entry.get("safety"),
        },
    )


def _emit_progress(
    progress_callback: ProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(dict(event))
    except Exception:
        return
