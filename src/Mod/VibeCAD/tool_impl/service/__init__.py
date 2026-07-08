# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service-backed VibeCAD tool registration.

Each module in this package owns one provider-visible tool shape and must expose
``run(service, **kwargs)``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from VibeCADTools import SafetyLevel, VibeCADTool


TOOL_MODULE_NAMES = (
    'core_get_active_document',
    'core_get_selection',
    'core_get_view_state',
    'core_get_task_panel',
    'core_wait_for_user_gui_action',
    'core_capture_view_screenshot',
    'core_set_view',
    'core_get_report_view_errors',
    'core_list_workbenches',
    'core_list_registered_commands',
    'core_list_active_workbench_commands',
    'core_get_tool_shape_report',
    'core_report_tool_shape_gap',
    'core_update_design_memory',
    'core_enter_workspace',
    'core_activate_workbench',
    'core_get_active_workbench_tool_pack',
    'core_list_workbench_tool_packs',
    'core_list_workbench_object_templates',
    'core_list_workbench_objects',
    'core_get_object_properties',
    'part_set_placement',
    'part_cut_cylindrical_hole',
    'part_dressup',
    'part_thicken_surface',
    'draft_create_array',
    'draft_create_wire',
    'surface_create_surface',
    'material_apply_appearance',
    'core_run_workbench_command',
    'core_create_new_document',
    'core_open_document',
    'core_delete_object',
    'partdesign_create_body',
    'partdesign_create_sketch',
    'partdesign_create_datum_plane',
    'partdesign_create_datum_line',
    'partdesign_extrude',
    'partdesign_hole_from_sketch',
    'partdesign_revolve',
    'partdesign_loft_profiles',
    'partdesign_sweep_profile',
    'partdesign_helix_profile',
    'partdesign_pattern',
    'partdesign_dressup',
    'partdesign_boolean_bodies',
    'partdesign_set_feature_dimensions',
    'spreadsheet_get_sheet',
    'partdesign_get_bodies',
    'partdesign_find_subelements',
    'techdraw_get_pages',
    'techdraw_create_page',
    'techdraw_add_view',
    'assembly_get_assemblies',
    'assembly_create_assembly',
    'assembly_add_component',
    'assembly_set_component_placement',
    'assembly_ground_component',
    'assembly_create_joint',
    'assembly_solve',
    'assembly_check_interference',
    'cam_define_machine',
    'cam_create_job',
    'cam_add_tool',
    'cam_create_operation',
    'cam_validate_job',
    'cam_postprocess',
    'model_build_from_script',
    'core_undo_last_vibecad_action',
    'core_clear_local_session',
)


def register_tools(registry: Any, service: Any) -> None:
    for module_name in TOOL_MODULE_NAMES:
        module = import_module(f"{__name__}.{module_name}")
        spec = module.TOOL_SPEC
        module_run = getattr(module, "run", None)
        if not callable(module_run):
            raise ValueError(f"VibeCAD service tool module has no run(): {module_name}")

        def handler(_module=module, **kwargs):
            return _module.run(service, **kwargs)

        registry.register(
            VibeCADTool(
                name=spec["name"],
                description=spec["description"],
                handler=handler,
                safety=getattr(SafetyLevel, spec["safety"]),
                workbench=spec.get("workbench"),
                contextual=bool(spec.get("contextual", False)),
                parameters=spec.get("parameters", {"type": "object", "properties": {}}),
            )
        )
