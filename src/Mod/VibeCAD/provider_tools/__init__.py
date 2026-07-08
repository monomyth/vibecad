# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit provider function-tool module registry for VibeCAD."""

from __future__ import annotations

from importlib import import_module


TOOL_MODULES = {
    "core.get_current_freecad_context": "provider_tools.core_get_current_freecad_context",
    "core.submit_design_preflight": "provider_tools.core_submit_design_preflight",
    "core.update_design_memory": "provider_tools.core_update_design_memory",
    "core.get_active_document": "provider_tools.core_get_active_document",
    "core.get_selection": "provider_tools.core_get_selection",
    "core.get_view_state": "provider_tools.core_get_view_state",
    "core.get_task_panel": "provider_tools.core_get_task_panel",
    "core.wait_for_user_gui_action": "provider_tools.core_wait_for_user_gui_action",
    "core.capture_view_screenshot": "provider_tools.core_capture_view_screenshot",
    "core.set_view": "provider_tools.core_set_view",
    "core.get_report_view_errors": "provider_tools.core_get_report_view_errors",
    "core.list_workbenches": "provider_tools.core_list_workbenches",
    "core.list_registered_commands": "provider_tools.core_list_registered_commands",
    "core.list_active_workbench_commands": "provider_tools.core_list_active_workbench_commands",
    "core.get_tool_shape_report": "provider_tools.core_get_tool_shape_report",
    "core.report_tool_shape_gap": "provider_tools.core_report_tool_shape_gap",
    "core.enter_workspace": "provider_tools.core_enter_workspace",
    "core.get_active_workbench_tool_pack": "provider_tools.core_get_active_workbench_tool_pack",
    "core.list_workbench_tool_packs": "provider_tools.core_list_workbench_tool_packs",
    "core.list_workbench_object_templates": "provider_tools.core_list_workbench_object_templates",
    "core.list_workbench_objects": "provider_tools.core_list_workbench_objects",
    "core.get_object_properties": "provider_tools.core_get_object_properties",
    "part.set_placement": "provider_tools.part_set_placement",
    "part.cut_cylindrical_hole": "provider_tools.part_cut_cylindrical_hole",
    "part.dressup": "provider_tools.part_dressup",
    "part.thicken_surface": "provider_tools.part_thicken_surface",
    "draft.create_array": "provider_tools.draft_create_array",
    "draft.create_wire": "provider_tools.draft_create_wire",
    "surface.create_surface": "provider_tools.surface_create_surface",
    "material.apply_appearance": "provider_tools.material_apply_appearance",
    "core.create_new_document": "provider_tools.core_create_new_document",
    "core.open_document": "provider_tools.core_open_document",
    "core.delete_object": "provider_tools.core_delete_object",
    "partdesign.create_body": "provider_tools.partdesign_create_body",
    "partdesign.create_sketch": "provider_tools.partdesign_create_sketch",
    "partdesign.create_datum_plane": "provider_tools.partdesign_create_datum_plane",
    "partdesign.create_datum_line": "provider_tools.partdesign_create_datum_line",
    "partdesign.extrude": "provider_tools.partdesign_extrude",
    "partdesign.hole_from_sketch": "provider_tools.partdesign_hole_from_sketch",
    "partdesign.revolve": "provider_tools.partdesign_revolve",
    "partdesign.loft_profiles": "provider_tools.partdesign_loft_profiles",
    "partdesign.sweep_profile": "provider_tools.partdesign_sweep_profile",
    "partdesign.helix_profile": "provider_tools.partdesign_helix_profile",
    "partdesign.pattern": "provider_tools.partdesign_pattern",
    "partdesign.dressup": "provider_tools.partdesign_dressup",
    "partdesign.boolean_bodies": "provider_tools.partdesign_boolean_bodies",
    "partdesign.set_feature_dimensions": "provider_tools.partdesign_set_feature_dimensions",
    "sketcher.create_sketch": "provider_tools.sketcher_create_sketch",
    "sketcher.open_sketch": "provider_tools.sketcher_open_sketch",
    "sketcher.close_sketch": "provider_tools.sketcher_close_sketch",
    "sketcher.inspect_sketch": "provider_tools.sketcher_inspect_sketch",
    "sketcher.resolve_geometry": "provider_tools.sketcher_resolve_geometry",
    "sketcher.set_geometry_name": "provider_tools.sketcher_set_geometry_name",
    "sketcher.draw_rectangle": "provider_tools.sketcher_draw_rectangle",
    "sketcher.add_geometry": "provider_tools.sketcher_add_geometry",
    "sketcher.add_hole_pattern": "provider_tools.sketcher_add_hole_pattern",
    "sketcher.add_slot": "provider_tools.sketcher_add_slot",
    "sketcher.add_constraint": "provider_tools.sketcher_add_constraint",
    "sketcher.edit_constraint": "provider_tools.sketcher_edit_constraint",
    "sketcher.move_point": "provider_tools.sketcher_move_point",
    "sketcher.transform_geometry": "provider_tools.sketcher_transform_geometry",
    "sketcher.modify_geometry": "provider_tools.sketcher_modify_geometry",
    "sketcher.add_external_geometry": "provider_tools.sketcher_add_external_geometry",
    "sketcher.remove_external_geometry": "provider_tools.sketcher_remove_external_geometry",
    "sketcher.delete_items": "provider_tools.sketcher_delete_items",
    "sketcher.set_construction": "provider_tools.sketcher_set_construction",
    "spreadsheet.get_sheet": "provider_tools.spreadsheet_get_sheet",
    "partdesign.get_bodies": "provider_tools.partdesign_get_bodies",
    "partdesign.find_subelements": "provider_tools.partdesign_find_subelements",
    "techdraw.get_pages": "provider_tools.techdraw_get_pages",
    "techdraw.create_page": "provider_tools.techdraw_create_page",
    "techdraw.add_view": "provider_tools.techdraw_add_view",
    "assembly.get_assemblies": "provider_tools.assembly_get_assemblies",
    "assembly.create_assembly": "provider_tools.assembly_create_assembly",
    "assembly.add_component": "provider_tools.assembly_add_component",
    "assembly.set_component_placement": "provider_tools.assembly_set_component_placement",
    "assembly.ground_component": "provider_tools.assembly_ground_component",
    "assembly.create_joint": "provider_tools.assembly_create_joint",
    "assembly.solve": "provider_tools.assembly_solve",
    "assembly.check_interference": "provider_tools.assembly_check_interference",
    "cam.define_machine": "provider_tools.cam_define_machine",
    "cam.create_job": "provider_tools.cam_create_job",
    "cam.add_tool": "provider_tools.cam_add_tool",
    "cam.create_operation": "provider_tools.cam_create_operation",
    "cam.validate_job": "provider_tools.cam_validate_job",
    "cam.postprocess": "provider_tools.cam_postprocess",
    "model.build_from_script": "provider_tools.model_build_from_script",
}


def create_tool(schema, conn, FunctionTool):
    tool_name = str(schema.get("name", ""))
    module_name = TOOL_MODULES.get(tool_name)
    if module_name is None:
        raise KeyError(f"No provider tool module is registered for {tool_name}")
    module = import_module(module_name)
    return module.create(schema, conn, FunctionTool)


def create_context_tool(schema, context, FunctionTool):
    tool_name = str(schema.get("name", ""))
    module_name = TOOL_MODULES.get(tool_name)
    if module_name is None:
        raise KeyError(f"No provider tool module is registered for {tool_name}")
    module = import_module(module_name)
    return module.create(schema, context, FunctionTool)


def registered_tool_names():
    return set(TOOL_MODULES)
