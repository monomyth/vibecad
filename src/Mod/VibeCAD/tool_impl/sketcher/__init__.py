# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sketcher-native VibeCAD tool registration."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from VibeCADTools import SafetyLevel, VibeCADTool


TOOL_MODULE_NAMES = (
    "create_sketch",
    "open_sketch",
    "close_sketch",
    "inspect_sketch",
    "resolve_geometry",
    "set_geometry_name",
    "draw_rectangle",
    "add_geometry",
    "add_hole_pattern",
    "add_slot",
    "add_constraint",
    "edit_constraint",
    "move_point",
    "transform_geometry",
    "modify_geometry",
    "add_external_geometry",
    "remove_external_geometry",
    "delete_items",
    "set_construction",
)


def register_tools(registry: Any, service: Any) -> None:
    for module_name in TOOL_MODULE_NAMES:
        module = import_module(f"{__name__}.{module_name}")
        spec = module.TOOL_SPEC
        safety_name = spec.get("safety", "SAFE_WRITE")
        description = spec["description"]
        registry.register(
            VibeCADTool(
                name=spec["name"],
                description=description,
                handler=lambda _module=module, **kwargs: _module.run(service, **kwargs),
                safety=getattr(SafetyLevel, safety_name),
                workbench=spec.get("workbench", "SketcherWorkbench"),
                contextual=bool(spec.get("contextual", False)),
                parameters=spec.get("parameters", {"type": "object", "properties": {}}),
            )
        )
