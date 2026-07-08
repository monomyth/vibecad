# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``core.update_design_memory``."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "core.update_design_memory",
    "description": (
        "Update the standing accepted design memory for this project. Use this "
        "inside the normal CAD loop when the user corrects the design, when "
        "the model commits to a product behavior, or when a known failure must "
        "remain visible on later turns. This stores design intent only; it does "
        "not create or edit CAD geometry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_intent": {
                "type": "string",
                "description": "Current accepted customer outcome, if changed.",
            },
            "summary": {
                "type": "string",
                "description": "Compact accepted architecture or design summary.",
            },
            "accepted_assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Assumptions the model will carry forward.",
            },
            "components": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Functional parts or assemblies the design requires.",
            },
            "sketches_features": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sketches, features, or construction elements the plan needs.",
            },
            "interfaces": {
                "type": "array",
                "items": {"type": "string"},
                "description": "How parts fit, contact, move, seal, fasten, or align.",
            },
            "mechanisms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Accepted motion, lock, load, compliance, or actuation behavior.",
            },
            "non_negotiable_product_behavior": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Functional truths that make the product usable.",
            },
            "critical_geometry": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Geometry the model must preserve while building.",
            },
            "verification_checks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Model-chosen checks proving the design still works.",
            },
            "construction_order": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Durable build-order commitments to carry across turns.",
            },
            "forbidden_shortcuts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Shortcuts the model must not take for this product.",
            },
            "known_failures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Observed bad outcomes or user corrections to avoid repeating.",
            },
            "corrections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Accepted corrections from the user or reviewer.",
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Remaining important unknowns, if any.",
            },
            "notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Other durable design notes.",
            },
            "current_obligation": {
                "type": "string",
                "description": "What future CAD actions must honor.",
            },
            "replace": {
                "type": "boolean",
                "description": "Replace the memory instead of merging into it.",
            },
        },
    },
    "safety": "SAFE_WRITE",
}


def run(service, **kwargs):
    result = service.update_design_memory(dict(kwargs))
    memory = result.get("design_memory") if isinstance(result, dict) else {}
    if not isinstance(memory, dict):
        memory = {}
    return {
        "ok": True,
        "title": "Design memory updated",
        "design_memory": memory,
        "memory_fields": sorted(
            key for key, value in memory.items() if value not in (None, "", [], {})
        ),
        "manifest_path": result.get("manifest_path") if isinstance(result, dict) else "",
    }
