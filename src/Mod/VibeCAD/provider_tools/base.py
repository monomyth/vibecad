# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared factory for explicit VibeCAD provider function tools."""

from __future__ import annotations

import json
from typing import Any

PROVIDER_TOOL_DESCRIPTIONS: dict[str, str] = {
    "cad.create_feature": "feature",
    "cad.create_profile": "profile",
    "cad.define_component": "component",
    "cad.define_envelope": "envelope",
    "cad.define_interface": "interface",
    "cad.define_mechanism": "mechanism",
    "cad.inspect_state": "state",
    "cad.verify_design": "verify",
    "assembly.add_component": "comp",
    "assembly.check_interference": "clash",
    "assembly.create_assembly": "assy",
    "assembly.create_joint": "mate",
    "assembly.get_assemblies": "state",
    "assembly.ground_component": "ground",
    "assembly.set_component_placement": "place",
    "assembly.solve": "solve",
    "cam.add_tool": "cutter",
    "cam.create_job": "job",
    "cam.create_operation": "path",
    "cam.define_machine": "machine",
    "cam.postprocess": "gcode",
    "cam.validate_job": "verify",
    "core.capture_view_screenshot": "shot",
    "core.get_report_view_errors": "errors",
    "core.list_workbench_objects": "objects",
    "core.set_view": "camera",
    "core.submit_design_preflight": "preflight",
    "core.update_design_memory": "memory",
    "draft.create_array": "array",
    "draft.create_wire": "wire",
    "material.apply_appearance": "color",
    "model.build_from_script": "script",
    "part.cut_cylindrical_hole": "hole",
    "part.dressup": "finish",
    "part.set_placement": "place",
    "part.thicken_surface": "thicken",
    "partdesign.boolean_bodies": "boolean",
    "partdesign.create_body": "body",
    "partdesign.create_datum_line": "axis",
    "partdesign.create_datum_plane": "plane",
    "partdesign.create_sketch": "sketch",
    "partdesign.dressup": "finish",
    "partdesign.extrude": "extrude",
    "partdesign.find_subelements": "pick",
    "partdesign.get_bodies": "bodies",
    "partdesign.helix_profile": "helix",
    "partdesign.hole_from_sketch": "hole",
    "partdesign.loft_profiles": "loft",
    "partdesign.pattern": "pattern",
    "partdesign.revolve": "revolve",
    "partdesign.set_feature_dimensions": "dims",
    "partdesign.sweep_profile": "sweep",
    "sketcher.add_constraint": "constr",
    "sketcher.add_external_geometry": "ref",
    "sketcher.add_geometry": "draw",
    "sketcher.add_hole_pattern": "holes",
    "sketcher.add_slot": "slot",
    "sketcher.close_sketch": "close",
    "sketcher.create_sketch": "sketch",
    "sketcher.delete_items": "delete",
    "sketcher.draw_rectangle": "rect",
    "sketcher.edit_constraint": "edit",
    "sketcher.inspect_sketch": "inspect",
    "sketcher.modify_geometry": "modify",
    "sketcher.move_point": "move",
    "sketcher.open_sketch": "open",
    "sketcher.remove_external_geometry": "unref",
    "sketcher.resolve_geometry": "resolve",
    "sketcher.set_construction": "const",
    "sketcher.set_geometry_name": "name",
    "sketcher.transform_geometry": "xfm",
    "spreadsheet.get_sheet": "sheet",
    "surface.create_surface": "surface",
    "techdraw.add_view": "view",
    "techdraw.create_page": "page",
    "techdraw.get_pages": "pages",
}


PROVIDER_FUNCTION_NAMES: dict[str, str] = {
    "cad.create_feature": "cad_feat",
    "cad.create_profile": "cad_prof",
    "cad.define_component": "cad_comp",
    "cad.define_envelope": "cad_env",
    "cad.define_interface": "cad_ifc",
    "cad.define_mechanism": "cad_mech",
    "cad.inspect_state": "cad_state",
    "cad.verify_design": "cad_check",
    "assembly.add_component": "a_comp",
    "assembly.check_interference": "a_clash",
    "assembly.create_assembly": "a_asm",
    "assembly.create_joint": "a_mate",
    "assembly.get_assemblies": "a_state",
    "assembly.ground_component": "a_fix",
    "assembly.set_component_placement": "a_place",
    "assembly.solve": "a_solve",
    "cam.add_tool": "cam_cut",
    "cam.create_job": "cam_job",
    "cam.create_operation": "cam_op",
    "cam.define_machine": "cam_mac",
    "cam.postprocess": "cam_post",
    "cam.validate_job": "cam_chk",
    "core.capture_view_screenshot": "c_shot",
    "core.get_report_view_errors": "c_err",
    "core.list_workbench_objects": "c_objs",
    "core.set_view": "c_cam",
    "core.submit_design_preflight": "c_plan",
    "core.update_design_memory": "c_mem",
    "draft.create_array": "dr_arr",
    "draft.create_wire": "dr_wire",
    "material.apply_appearance": "mat",
    "model.build_from_script": "script",
    "part.cut_cylindrical_hole": "p_hole",
    "part.dressup": "p_fin",
    "part.set_placement": "p_place",
    "part.thicken_surface": "p_thk",
    "partdesign.boolean_bodies": "pd_bool",
    "partdesign.create_body": "pd_body",
    "partdesign.create_datum_line": "pd_axis",
    "partdesign.create_datum_plane": "pd_pln",
    "partdesign.create_sketch": "pd_sk",
    "partdesign.dressup": "pd_fin",
    "partdesign.extrude": "pd_ext",
    "partdesign.find_subelements": "pd_pick",
    "partdesign.get_bodies": "pd_bods",
    "partdesign.helix_profile": "pd_hlx",
    "partdesign.hole_from_sketch": "pd_hole",
    "partdesign.loft_profiles": "pd_loft",
    "partdesign.pattern": "pd_pat",
    "partdesign.revolve": "pd_rev",
    "partdesign.set_feature_dimensions": "pd_dim",
    "partdesign.sweep_profile": "pd_swp",
    "sketcher.add_constraint": "sk_con",
    "sketcher.add_external_geometry": "sk_ref",
    "sketcher.add_geometry": "sk_draw",
    "sketcher.add_hole_pattern": "sk_hole",
    "sketcher.add_slot": "sk_slot",
    "sketcher.close_sketch": "sk_cls",
    "sketcher.create_sketch": "sk_new",
    "sketcher.delete_items": "sk_del",
    "sketcher.draw_rectangle": "sk_rect",
    "sketcher.edit_constraint": "sk_edit",
    "sketcher.inspect_sketch": "sk_ins",
    "sketcher.modify_geometry": "sk_mod",
    "sketcher.move_point": "sk_move",
    "sketcher.open_sketch": "sk_open",
    "sketcher.remove_external_geometry": "sk_unr",
    "sketcher.resolve_geometry": "sk_res",
    "sketcher.set_construction": "sk_aux",
    "sketcher.set_geometry_name": "sk_name",
    "sketcher.transform_geometry": "sk_xfm",
    "spreadsheet.get_sheet": "ss",
    "surface.create_surface": "sf",
    "techdraw.add_view": "td_view",
    "techdraw.create_page": "td_page",
    "techdraw.get_pages": "td_pgs",
}


def tool_description(schema: dict[str, Any]) -> str:
    tool_name = str(schema.get("name", ""))
    if tool_name in PROVIDER_TOOL_DESCRIPTIONS:
        return PROVIDER_TOOL_DESCRIPTIONS[tool_name]
    return tool_name


def provider_function_name(tool_name: str, fallback: str) -> str:
    return PROVIDER_FUNCTION_NAMES.get(str(tool_name or ""), fallback)


_PROVIDER_SCHEMA_FIELDS: dict[str, set[str]] = {
    "partdesign.dressup": {
        "operation",
        "feature_name",
        "label",
        "radius",
        "size",
        "all_edges",
        "edge_names",
        "face_names",
        "angle",
        "reverse",
        "thickness_value",
    },
    "partdesign.find_subelements": {
        "object_name",
        "element_type",
        "geometry_type",
        "normal",
        "radius",
        "min_area",
        "max_area",
        "min_length",
        "max_length",
        "near_point",
        "max_results",
    },
    "partdesign.hole_from_sketch": {
        "sketch_name",
        "label",
        "diameter",
        "depth",
        "depth_type",
        "hole_cut_type",
        "hole_cut_diameter",
        "hole_cut_depth",
        "countersink_angle",
        "thread_type",
    },
    "sketcher.add_constraint": {
        "sketch_name",
        "constraint_type",
        "first_geometry",
        "first_point",
        "second_geometry",
        "second_point",
        "third_geometry",
        "third_point",
        "value",
        "x",
        "y",
    },
    "sketcher.add_hole_pattern": {
        "sketch_name",
        "pattern",
        "hole_diameter",
        "center_x",
        "center_y",
        "count_x",
        "count_y",
        "spacing_x",
        "spacing_y",
        "count",
        "linear_angle_degrees",
        "bolt_circle_diameter",
        "start_angle_degrees",
    },
    "sketcher.add_slot": {
        "sketch_name",
        "center_x",
        "center_y",
        "length",
        "overall_length",
        "center_distance",
        "length_mode",
        "width",
        "angle_degrees",
    },
    "sketcher.edit_constraint": {
        "action",
        "sketch_name",
        "constraint_index",
        "constraint_name",
        "value",
        "new_name",
        "driving",
        "expression",
    },
    "sketcher.move_point": {
        "sketch_name",
        "geometry_index",
        "point",
        "relative",
        "x",
        "y",
    },
    "sketcher.set_construction": {
        "sketch_name",
        "geometry_index",
        "construction",
    },
    "sketcher.set_geometry_name": {
        "sketch_name",
        "geometry_index",
        "geometry_name",
    },
    "sketcher.modify_geometry": {
        "operation",
        "sketch_name",
        "geometry_index",
        "x",
        "y",
        "endpoint",
        "increment",
        "first_geometry",
        "first_point",
        "second_geometry",
        "first_reference_x",
        "first_reference_y",
        "second_reference_x",
        "second_reference_y",
        "radius",
        "chamfer",
    },
    "sketcher.transform_geometry": {
        "operation",
        "sketch_name",
        "geometry_indices",
        "dx",
        "dy",
        "axis_point_x",
        "axis_point_y",
        "axis_direction_x",
        "axis_direction_y",
        "keep_original",
        "distance",
        "side",
        "columns",
        "rows",
        "column_dx",
        "column_dy",
        "row_dx",
        "row_dy",
    },
}


_KEEP_OBJECT_SHAPE_KEYS = {
    "n",
    "normal",
    "p",
    "near_point",
}

_KEEP_ARRAY_SHAPE_KEYS = {
    "c",
    "center",
    "n",
    "normal",
    "p",
    "points",
    "near_point",
}

_DROP_PROVIDER_ENUM_KEYS: set[str] = set()

_PROVIDER_ARG_ALIASES: dict[str, dict[str, str]] = {
    "core.capture_view_screenshot": {
        "fit_all": "fit",
    },
    "core.get_report_view_errors": {
        "include_stale": "stale",
    },
    "core.list_workbench_objects": {
        "object_name": "obj",
    },
    "core.set_view": {
        "fit_all": "fit",
        "show_objects": "show",
        "hide_objects": "hide",
    },
    "assembly.add_component": {
        "assembly_name": "asm",
        "component_name": "comp",
    },
    "assembly.check_interference": {
        "assembly_name": "asm",
        "object_names": "objects",
        "clearance_threshold": "clearance",
    },
    "assembly.create_assembly": {
        "component_names": "components",
    },
    "assembly.create_joint": {
        "assembly_name": "asm",
        "joint_type": "j",
        "component1": "c1",
        "element1": "e1",
        "vertex1": "v1",
        "component2": "c2",
        "element2": "e2",
        "vertex2": "v2",
        "offset1": "o1",
        "offset2": "o2",
        "distance": "d",
        "distance2": "d2",
        "angle_degrees": "a",
        "length_min": "l0",
        "length_max": "l1",
        "angle_min": "a0",
        "angle_max": "a1",
        "label": "lbl",
    },
    "assembly.ground_component": {
        "assembly_name": "asm",
        "component_name": "comp",
    },
    "assembly.set_component_placement": {
        "assembly_name": "asm",
        "component_name": "comp",
        "yaw_degrees": "yaw",
        "pitch_degrees": "pitch",
        "roll_degrees": "roll",
    },
    "cam.add_tool": {
        "job_name": "job",
        "label": "lbl",
        "tool_number": "no",
        "tool_shape": "shape",
        "diameter": "dia",
        "spindle_speed": "rpm",
        "horiz_feed": "feed",
        "vert_feed": "plunge",
        "tool_length_offset": "h",
    },
    "cam.create_job": {
        "model_names": "models",
        "machine_name": "machine",
        "stock_extension": "stock",
    },
    "cam.create_operation": {
        "operation_type": "op",
        "job_name": "job",
        "label": "lbl",
        "tool_controller": "tool",
        "base_object": "obj",
        "sub_elements": "sub",
        "start_depth": "z0",
        "final_depth": "z1",
        "step_down": "step",
        "properties": "props",
    },
    "cam.postprocess": {
        "job_name": "job",
        "output_path": "out",
    },
    "cam.validate_job": {
        "job_name": "job",
        "machine_name": "machine",
    },
    "cam.define_machine": {
        "manufacturer": "maker",
        "description": "desc",
        "linear_axes": "linear",
        "rotary_axes": "rotary",
        "postprocessor": "post",
        "output_tool_length_offset": "h",
    },
    "partdesign.create_sketch": {
        "body_name": "b",
        "label": "lbl",
        "support_type": "on",
        "support_object": "obj",
        "subelement": "sub",
        "normal": "n",
        "normal_tolerance_degrees": "tol",
        "map_mode": "map",
    },
    "partdesign.create_datum_line": {
        "body_name": "body",
        "label": "lbl",
        "support_axis": "axis",
        "rotation_axis": "rot",
        "rotation_deg": "angle",
        "offset_x": "x",
        "offset_y": "y",
        "offset_z": "z",
        "map_mode": "map",
    },
    "partdesign.create_datum_plane": {
        "body_name": "body",
        "label": "lbl",
        "support_plane": "plane",
        "rotation_axis": "rot",
        "rotation_deg": "angle",
        "offset_x": "x",
        "offset_y": "y",
        "offset_z": "z",
        "map_mode": "map",
    },
    "partdesign.dressup": {
        "operation": "op",
        "feature_name": "f",
        "label": "lbl",
        "radius": "r",
        "size": "s",
        "all_edges": "all",
        "edge_names": "edges",
        "face_names": "faces",
        "thickness_value": "thick",
    },
    "partdesign.find_subelements": {
        "object_name": "obj",
        "element_type": "el",
        "geometry_type": "g",
        "normal": "n",
        "radius": "r",
        "min_area": "a0",
        "max_area": "a1",
        "min_length": "l0",
        "max_length": "l1",
        "near_point": "p",
        "max_results": "m",
    },
    "partdesign.helix_profile": {
        "label": "lbl",
        "height": "h",
        "profile_sketch_name": "sk",
        "reference_axis": "axis",
        "left_handed": "left",
        "native_mode": "native",
        "reversed": "rev",
    },
    "partdesign.hole_from_sketch": {
        "sketch_name": "sk",
        "label": "lbl",
        "diameter": "dia",
        "depth_type": "mode",
        "hole_cut_type": "cut",
        "hole_cut_diameter": "cut_dia",
        "hole_cut_depth": "cut_depth",
        "countersink_angle": "angle",
        "thread_type": "thread",
    },
    "partdesign.pattern": {
        "operation": "op",
        "feature_name": "f",
        "label": "lbl",
        "direction": "d",
        "length": "l",
        "occurrences": "n",
        "mirror_plane": "pl",
        "angle": "a",
        "axis": "ax",
        "refine": "rf",
    },
    "partdesign.extrude": {
        "operation": "op",
        "sketch_name": "sk",
        "label": "lbl",
        "length": "l",
        "symmetric": "sym",
        "reversed": "rev",
    },
    "partdesign.revolve": {
        "operation": "op",
        "sketch_name": "sk",
        "label": "lbl",
        "axis": "axis",
        "angle": "angle",
        "symmetric": "sym",
        "reversed": "rev",
    },
    "partdesign.set_feature_dimensions": {
        "feature_name": "feature",
        "length": "l",
        "radius": "r",
        "angle": "a",
    },
    "partdesign.loft_profiles": {
        "label": "lbl",
        "profile_sketch_name": "profile",
        "section_sketch_names": "sections",
        "profile_names": "profiles",
        "section_names": "sections",
        "operation": "op",
        "solid": "solid",
        "ruled": "ruled",
    },
    "partdesign.sweep_profile": {
        "label": "lbl",
        "profile_sketch_name": "profile",
        "section_sketch_names": "sections",
        "spine_sketch_name": "spine",
        "spine_name": "spine",
        "operation": "op",
    },
    "partdesign.boolean_bodies": {
        "label": "lbl",
        "operation": "op",
        "base_body_name": "base",
        "target_body_name": "base",
        "tool_body_names": "tools",
    },
    "part.cut_cylindrical_hole": {
        "label": "lbl",
        "target_name": "obj",
        "radius": "r",
    },
    "part.dressup": {
        "operation": "op",
        "object_name": "obj",
        "label": "lbl",
        "radius": "r",
        "distance": "d",
        "edge_indices": "edges",
        "face_names": "faces",
        "wall_thickness": "thick",
    },
    "part.set_placement": {
        "object_name": "obj",
        "yaw_degrees": "yaw",
        "pitch_degrees": "pitch",
        "roll_degrees": "roll",
    },
    "part.thicken_surface": {
        "object_name": "obj",
        "thickness": "t",
    },
    "draft.create_array": {
        "label": "lbl",
        "source_object": "obj",
        "object_name": "obj",
        "array_type": "type",
        "number_x": "nx",
        "number_y": "ny",
        "number_z": "nz",
        "count_x": "nx",
        "count_y": "ny",
        "count_z": "nz",
        "center_x": "cx",
        "center_y": "cy",
        "center_z": "cz",
        "interval_x": "dx",
        "interval_y": "dy",
        "interval_z": "dz",
        "polar_count": "n",
        "polar_angle": "angle",
        "polar_center": "center",
        "use_link": "link",
    },
    "draft.create_wire": {
        "points": "p",
        "label": "lbl",
        "curve_type": "type",
        "closed": "close",
    },
    "surface.create_surface": {
        "operation": "op",
        "boundaries": "b",
        "fill_type": "fill",
        "label": "lbl",
    },
    "techdraw.add_view": {
        "label": "lbl",
        "page_name": "page",
        "source_name": "src",
    },
    "techdraw.create_page": {
        "with_template": "template",
    },
    "techdraw.get_pages": {
        "page_name": "page",
    },
    "sketcher.add_geometry": {
        "sketch_name": "sk",
        "kind": "k",
        "points": "p",
        "center": "c",
        "radius": "r",
        "start_angle_degrees": "a1",
        "end_angle_degrees": "a2",
        "angle_degrees": "a",
        "major_radius": "rx",
        "minor_radius": "ry",
        "closed": "cl",
        "constrain_points": "fix",
        "construction": "con",
        "interpolate": "i",
        "periodic": "per",
    },
    "sketcher.add_constraint": {
        "constraint_type": "t",
        "sketch_name": "sk",
        "first_geometry": "g1",
        "first_point": "p1",
        "second_geometry": "g2",
        "second_point": "p2",
        "third_geometry": "g3",
        "third_point": "p3",
        "value": "v",
    },
    "sketcher.add_hole_pattern": {
        "sketch_name": "sk",
        "hole_diameter": "dia",
        "center_x": "cx",
        "center_y": "cy",
        "count_x": "nx",
        "count_y": "ny",
        "spacing_x": "sx",
        "spacing_y": "sy",
        "count": "n",
        "linear_angle_degrees": "angle",
        "bolt_circle_diameter": "bcd",
        "start_angle_degrees": "start_angle",
    },
    "sketcher.add_slot": {
        "sketch_name": "sk",
        "center_x": "cx",
        "center_y": "cy",
        "length": "len",
        "center_distance": "distance",
        "overall_length": "overall",
        "length_mode": "mode",
        "width": "w",
        "angle_degrees": "angle",
    },
    "sketcher.create_sketch": {
        "label": "lbl",
        "support_type": "support",
        "support_object": "obj",
        "subelement": "sub",
        "map_mode": "map",
        "open_for_edit": "open",
    },
    "sketcher.delete_items": {
        "sketch_name": "sk",
        "geometry_items": "g",
        "constraint_items": "c",
        "all_geometry": "all_g",
        "all_constraints": "all_c",
        "delete_constraints_first": "constraints_first",
    },
    "sketcher.edit_constraint": {
        "action": "op",
        "sketch_name": "sk",
        "constraint_index": "c",
        "constraint_name": "name",
        "value": "v",
        "new_name": "new",
        "driving": "drive",
        "expression": "expr",
    },
    "sketcher.modify_geometry": {
        "operation": "op",
        "sketch_name": "sk",
        "geometry_index": "g",
        "endpoint": "e",
        "increment": "i",
        "first_geometry": "g1",
        "first_point": "p1",
        "second_geometry": "g2",
        "first_reference_x": "x1",
        "first_reference_y": "y1",
        "second_reference_x": "x2",
        "second_reference_y": "y2",
        "radius": "r",
        "chamfer": "ch",
    },
    "sketcher.inspect_sketch": {
        "sketch_name": "sk",
        "include": "inc",
        "tolerance": "tol",
        "reference_object_name": "ref",
        "max_references": "max",
    },
    "sketcher.move_point": {
        "sketch_name": "sk",
        "geometry_index": "g",
        "relative": "rel",
    },
    "sketcher.set_construction": {
        "sketch_name": "sk",
        "geometry_index": "g",
        "construction": "const",
    },
    "sketcher.set_geometry_name": {
        "sketch_name": "sk",
        "geometry_index": "g",
        "geometry_name": "name",
    },
    "sketcher.transform_geometry": {
        "operation": "op",
        "sketch_name": "sk",
        "geometry_indices": "g",
        "axis_point_x": "x0",
        "axis_point_y": "y0",
        "axis_direction_x": "ux",
        "axis_direction_y": "uy",
        "keep_original": "keep",
        "distance": "d",
        "columns": "nx",
        "column_dx": "cdx",
        "column_dy": "cdy",
        "rows": "ny",
        "row_dx": "rdx",
        "row_dy": "rdy",
    },
    "sketcher.add_external_geometry": {
        "sketch_name": "sk",
        "source_object": "obj",
        "subelement": "sub",
    },
    "sketcher.remove_external_geometry": {
        "sketch_name": "sk",
        "external_geometry_index": "g",
    },
    "sketcher.draw_rectangle": {
        "sketch_name": "sk",
        "center_x": "cx",
        "center_y": "cy",
    },
    "material.apply_appearance": {
        "object_name": "obj",
        "diffuse_color": "color",
    },
}

_PROVIDER_REF_ALIASES: dict[str, dict[str, tuple[str, str]]] = {
    "sketcher.add_constraint": {
        "g1": ("first_geometry", "first_geometry_handle"),
        "g2": ("second_geometry", "second_geometry_handle"),
        "g3": ("third_geometry", "third_geometry_handle"),
    },
    "sketcher.modify_geometry": {
        "g": ("geometry_index", "geometry_handle"),
        "g1": ("first_geometry", "first_geometry_handle"),
        "g2": ("second_geometry", "second_geometry_handle"),
    },
    "sketcher.transform_geometry": {
        "g": ("geometry_indices", "geometry_handles"),
    },
    "sketcher.edit_constraint": {
        "c": ("constraint_index", "constraint_handle"),
    },
    "sketcher.move_point": {
        "g": ("geometry_index", "geometry_handle"),
    },
    "sketcher.set_construction": {
        "g": ("geometry_index", "geometry_handle"),
    },
    "sketcher.set_geometry_name": {
        "g": ("geometry_index", "geometry_handle"),
    },
}


_PROVIDER_ENUM_ALIASES: dict[str, dict[str, dict[str, str]]] = {
    "assembly.create_joint": {
        "j": {
            "Fixed": "fix",
            "Revolute": "rev",
            "Cylindrical": "cyl",
            "Slider": "slide",
            "Ball": "ball",
            "Distance": "dist",
            "Parallel": "par",
            "Perpendicular": "perp",
            "Angle": "ang",
            "RackPinion": "rack",
            "Screw": "screw",
            "Gears": "gear",
            "Belt": "belt",
        },
    },
    "cam.create_operation": {
        "op": {
            "adaptive": "adapt",
            "drill": "drill",
            "helix": "helix",
            "pocket": "pocket",
            "profile": "profile",
            "surface": "surface",
        },
    },
    "part.cut_cylindrical_hole": {
        "axis": {"X": "X", "Y": "Y", "Z": "Z"},
    },
    "part.dressup": {
        "op": {"fillet": "fillet", "chamfer": "chamfer", "thickness": "thick"},
    },
    "partdesign.create_datum_plane": {
        "plane": {"XY_Plane": "XY", "XZ_Plane": "XZ", "YZ_Plane": "YZ"},
    },
    "partdesign.create_sketch": {
        "on": {"origin_plane": "origin", "datum_plane": "datum", "face": "face"},
        "plane": {"XY_Plane": "XY", "XZ_Plane": "XZ", "YZ_Plane": "YZ"},
    },
    "partdesign.dressup": {
        "op": {
            "fillet": "fillet",
            "chamfer": "chamfer",
            "draft": "draft",
            "thickness": "thick",
        },
    },
    "partdesign.helix_profile": {
        "axis": {"H_Axis": "H", "V_Axis": "V", "N_Axis": "N"},
        "mode": {"additive": "add", "subtractive": "sub"},
    },
    "partdesign.pattern": {
        "ax": {"X_Axis": "X", "Y_Axis": "Y", "Z_Axis": "Z"},
        "d": {"X_Axis": "X", "Y_Axis": "Y", "Z_Axis": "Z"},
        "pl": {"XY_Plane": "XY", "XZ_Plane": "XZ", "YZ_Plane": "YZ"},
    },
    "partdesign.revolve": {
        "axis": {
            "X_Axis": "X",
            "Y_Axis": "Y",
            "Z_Axis": "Z",
            "H_Axis": "H",
            "V_Axis": "V",
            "N_Axis": "N",
        },
    },
    "sketcher.add_constraint": {
        "t": {
            "Horizontal": "H",
            "Vertical": "V",
            "Parallel": "par",
            "Perpendicular": "perp",
            "Tangent": "tan",
            "Equal": "eq",
            "Symmetric": "sym",
            "Block": "block",
            "Coincident": "coin",
            "PointOnObject": "on",
            "Distance": "dist",
            "DistanceX": "dx",
            "DistanceY": "dy",
            "Radius": "r",
            "Diameter": "dia",
            "Angle": "ang",
            "Lock": "lock",
        },
        "p1": {
            "whole": "whole",
            "edge": "edge",
            "curve": "curve",
            "start": "start",
            "end": "end",
            "center": "center",
            "midpoint": "mid",
            "origin": "origin",
            "point": "point",
            "vertex": "vertex",
        },
        "p2": {
            "whole": "whole",
            "edge": "edge",
            "curve": "curve",
            "start": "start",
            "end": "end",
            "center": "center",
            "midpoint": "mid",
            "origin": "origin",
            "point": "point",
            "vertex": "vertex",
        },
        "p3": {
            "whole": "whole",
            "edge": "edge",
            "curve": "curve",
            "start": "start",
            "end": "end",
            "center": "center",
            "midpoint": "mid",
            "origin": "origin",
            "point": "point",
            "vertex": "vertex",
        },
    },
    "sketcher.add_geometry": {
        "k": {
            "line": "line",
            "point": "point",
            "arc": "arc",
            "circle": "circle",
            "ellipse": "ellipse",
            "bspline": "spline",
            "polyline": "poly",
        },
    },
    "sketcher.add_hole_pattern": {
        "pattern": {"rectangular": "rect", "linear": "linear", "circular": "circle"},
    },
    "sketcher.add_slot": {
        "mode": {"overall": "overall", "center_to_center": "centers"},
    },
    "sketcher.create_sketch": {
        "support": {
            "origin_plane": "origin",
            "face": "face",
            "datum_plane": "datum",
            "none": "none",
        },
        "plane": {"XY_Plane": "XY", "XZ_Plane": "XZ", "YZ_Plane": "YZ"},
    },
    "sketcher.edit_constraint": {
        "op": {
            "set_value": "value",
            "set_name": "name",
            "set_driving": "drive",
            "set_expression": "expr",
            "get": "get",
        },
    },
    "sketcher.inspect_sketch": {
        "inc": {
            "geometry": "geom",
            "constraints": "constr",
            "solver": "solver",
            "profile": "profile",
            "profile_deep": "deep",
            "constraint_diagnostics": "diag",
            "external_geometry": "external",
            "reference_geometry": "ref",
        },
    },
    "sketcher.modify_geometry": {
        "op": {"trim": "trim", "extend": "extend", "split": "split", "fillet": "fillet"},
    },
    "sketcher.move_point": {
        "point": {
            "whole": "whole",
            "start": "start",
            "end": "end",
            "center": "center",
            "midpoint": "mid",
        },
    },
    "sketcher.transform_geometry": {
        "op": {
            "translate": "move",
            "copy": "copy",
            "mirror": "mirror",
            "offset": "offset",
            "array": "array",
        },
        "side": {"left": "left", "right": "right", "outward": "out", "inward": "in"},
    },
    "surface.create_surface": {
        "fill": {"Stretched": "stretch", "Coons": "coons", "Curved": "curve"},
        "op": {"filling": "fill", "geomfill": "geom", "sections": "sections"},
    },
}


def _enum_alias_reverse(tool_name: str) -> dict[str, dict[str, str]]:
    return {
        key: {alias: canonical for canonical, alias in aliases.items()}
        for key, aliases in _PROVIDER_ENUM_ALIASES.get(tool_name, {}).items()
    }


def _schema_for_provider_tool(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    keep = _PROVIDER_SCHEMA_FIELDS.get(tool_name)
    if not keep:
        return dict(parameters)
    result = dict(parameters)
    properties = result.get("properties")
    if isinstance(properties, dict):
        result["properties"] = {
            key: value for key, value in properties.items() if str(key) in keep
        }
    required = result.get("required")
    if isinstance(required, list):
        result["required"] = [item for item in required if str(item) in keep]
    return result


def _provider_alias_schema(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    aliases = _PROVIDER_ARG_ALIASES.get(tool_name)
    if not aliases:
        return parameters
    result = dict(parameters)
    properties = result.get("properties")
    if isinstance(properties, dict):
        result["properties"] = {
            aliases.get(str(name), str(name)): value for name, value in properties.items()
        }
    required = result.get("required")
    if isinstance(required, list):
        result["required"] = [aliases.get(str(item), str(item)) for item in required]
    return result


def _provider_enum_alias_schema(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    enum_aliases = _PROVIDER_ENUM_ALIASES.get(tool_name)
    if not enum_aliases:
        return parameters
    result = dict(parameters)
    properties = result.get("properties")
    if not isinstance(properties, dict):
        return result
    compact_properties = dict(properties)
    for property_name, aliases in enum_aliases.items():
        schema = compact_properties.get(property_name)
        if isinstance(schema, dict):
            compact_properties[property_name] = _replace_schema_enum_values(schema, aliases)
    result["properties"] = compact_properties
    return result


def _replace_schema_enum_values(schema: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    result = dict(schema)
    enum_values = result.get("enum")
    if isinstance(enum_values, list):
        result["enum"] = [aliases.get(str(value), value) for value in enum_values]
    items = result.get("items")
    if isinstance(items, dict):
        result["items"] = _replace_schema_enum_values(items, aliases)
    return result


def _expand_enum_alias_value(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, list):
        return [_expand_enum_alias_value(item, aliases) for item in value]
    return value


def _expand_provider_argument_aliases(tool_name: str, arguments_json: str) -> str:
    aliases = _PROVIDER_ARG_ALIASES.get(tool_name)
    if not aliases:
        aliases = {}
    try:
        args = json.loads(arguments_json or "{}")
    except Exception:
        return arguments_json or "{}"
    if not isinstance(args, dict):
        return arguments_json or "{}"
    reverse = {alias: canonical for canonical, alias in aliases.items()}
    expanded = dict(args)
    ref_aliases = _PROVIDER_REF_ALIASES.get(tool_name, {})
    for alias, (index_key, handle_key) in ref_aliases.items():
        if alias not in expanded:
            continue
        value = expanded.get(alias)
        if isinstance(value, list):
            indices = [item for item in value if isinstance(item, int)]
            handles = [item for item in value if not isinstance(item, int)]
            if indices and index_key not in expanded:
                expanded[index_key] = indices
            if handles and handle_key not in expanded:
                expanded[handle_key] = handles
        elif isinstance(value, int):
            if index_key not in expanded:
                expanded[index_key] = value
        elif handle_key not in expanded:
            expanded[handle_key] = value
        expanded.pop(alias, None)
    for alias, canonical in reverse.items():
        if alias not in expanded:
            continue
        if alias == canonical:
            continue
        if canonical not in expanded:
            expanded[canonical] = expanded[alias]
        expanded.pop(alias, None)
    enum_aliases = _enum_alias_reverse(tool_name)
    for provider_key, value_aliases in enum_aliases.items():
        canonical_key = reverse.get(provider_key, provider_key)
        if canonical_key in expanded:
            expanded[canonical_key] = _expand_enum_alias_value(
                expanded[canonical_key], value_aliases
            )
    return json.dumps(expanded, separators=(",", ":"))


def _filter_backend_arguments(schema: dict[str, Any], arguments_json: str) -> str:
    parameters = schema.get("parameters")
    properties = parameters.get("properties") if isinstance(parameters, dict) else None
    if not isinstance(properties, dict):
        return arguments_json or "{}"
    allowed = {str(key) for key in properties}
    try:
        args = json.loads(arguments_json or "{}")
    except Exception:
        return arguments_json or "{}"
    if not isinstance(args, dict):
        return arguments_json or "{}"
    filtered = {key: value for key, value in args.items() if str(key) in allowed}
    return json.dumps(filtered, separators=(",", ":"))


def _schema_type_contains(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in {str(item) for item in value}
    return False


def _compact_schema_type(value: Any, allowed: set[str]) -> Any:
    if isinstance(value, str):
        return value if value in allowed else None
    if isinstance(value, list):
        types = [str(item) for item in value if str(item) in allowed]
        if not types:
            return None
        return types[0] if len(types) == 1 else types
    return None


def _provider_schema(value: Any, *, root: bool = False, key_name: str = "") -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {
                "additionalProperties",
                "default",
                "description",
                "maximum",
                "maxItems",
                "minimum",
                "minItems",
            } and key_name not in _KEEP_ARRAY_SHAPE_KEYS:
                continue
            if key == "properties" and isinstance(item, dict):
                result[key] = {
                    str(name): _provider_schema(schema, key_name=str(name))
                    for name, schema in item.items()
                }
            elif key == "items" and key_name in _KEEP_ARRAY_SHAPE_KEYS:
                result[key] = _provider_schema(item, key_name=key_name)
            else:
                result[key] = _provider_schema(item, key_name=str(key))
        if root:
            result.setdefault("type", "object")
            result.setdefault("properties", {})
            if result.get("required") == []:
                result.pop("required", None)
            return result
        if "enum" in result and key_name in _DROP_PROVIDER_ENUM_KEYS:
            return {}
        if "enum" in result:
            return {"enum": result["enum"]}
        schema_type = result.get("type")
        if _schema_type_contains(schema_type, "object") or "properties" in result:
            if key_name not in _KEEP_OBJECT_SHAPE_KEYS:
                return {}
            compact = {"properties": result.get("properties", {})}
            if result.get("required"):
                compact["required"] = result["required"]
            return compact
        if _schema_type_contains(schema_type, "array"):
            if key_name in _KEEP_ARRAY_SHAPE_KEYS:
                compact = {"type": "array"}
                for count_key in ("minItems", "maxItems"):
                    if count_key in result:
                        compact[count_key] = result[count_key]
                items = result.get("items")
                if isinstance(items, dict):
                    compact["items"] = items
                return compact
            items = result.get("items")
            if isinstance(items, dict) and "enum" in items:
                return {"items": items}
            return {}
        if key_name in _KEEP_ARRAY_SHAPE_KEYS:
            compact_type = _compact_schema_type(
                schema_type,
                {"number", "integer", "string", "boolean"},
            )
            if compact_type is not None:
                return {"type": compact_type}
        return {}
    if isinstance(value, list):
        return [_provider_schema(item, key_name=key_name) for item in value]
    return value


def tool_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(schema.get("name", ""))
    parameters = schema.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    parameters = _schema_for_provider_tool(tool_name, parameters)
    parameters = _provider_alias_schema(tool_name, parameters)
    parameters = _provider_enum_alias_schema(tool_name, parameters)
    result = _provider_schema(dict(parameters), root=True)
    return result


_OMIT = object()
_DROP_RESULT_KEYS = {
    "arguments_json",
    "blocked_arguments_json",
    "document_summary",
    "document_after",
    "document_before",
    "full_context",
    "next_action",
    "next_actions",
    "provider_tool_schemas",
    "provider_tool_surface",
    "recent_feedback",
    "suggested_next_actions",
    "sketcher",
    "stdout",
    "task_panel",
    "tool_shape_report",
    "warnings",
    "why",
}
_KEEP_TRANSACTION_KEYS = (
    "error",
    "mutated_document",
    "ok",
    "report_view_errors",
    "result",
    "rolled_back",
)
_PROVIDER_RESULT_KEY_ALIASES = {
    "active_body": "body",
    "active_feature": "feat",
    "active_sketch": "sk",
    "active_workbench": "wb",
    "actual_curve_geometry_count": "curve_n",
    "actual_curve_geometry_types": "curve_t",
    "actual_geometry_types": "geom_t",
    "body_shape_delta": "shape",
    "changed_objects": "changed",
    "closed_profile": "closed",
    "constraint_count": "cons",
    "constraint_index": "c",
    "constraint_indices": "c",
    "created_constraint_indices": "c_new",
    "created_geometry_indices": "g_new",
    "conflicting_constraint_indices": "conflict",
    "created_objects": "created",
    "degrees_of_freedom": "dof",
    "deleted_constraint_indices": "c_del",
    "deleted_geometry_indices": "g_del",
    "deleted_objects": "deleted",
    "document_delta": "doc",
    "edges_delta": "dE",
    "entity_kind_counts": "entities",
    "error": "err",
    "errors": "errs",
    "executed": "exec",
    "face_count": "faces",
    "faces_delta": "dF",
    "feature": "feat",
    "feature_effect": "fx",
    "fully_constrained": "full",
    "geometry_count": "geom",
    "geometry_added": "g_add",
    "geometry_index": "g",
    "geometry_indices": "g",
    "mutation": "edit",
    "mutated_document": "mut",
    "modified_constraint_indices": "c_mod",
    "modified_geometry_indices": "g_mod",
    "object_count_delta": "dObj",
    "open_endpoint_count": "open",
    "profile_status": "profile",
    "profile_validation": "prof",
    "profile_validation_deep": "prof2",
    "ready_for_pad": "pad_ok",
    "ready_for_pocket": "pocket_ok",
    "redundant_constraint_indices": "redundant",
    "report_view_errors": "errs",
    "requested_curve_entity_count": "curve_req",
    "result": "r",
    "rolled_back": "rb",
    "rolled_back_feature": "rb",
    "solids_delta": "dS",
    "solver_status": "solver",
    "status": "st",
    "tool_workbench": "tool_wb",
    "transaction": "tx",
    "volume_delta": "dV",
}
_MAX_RESULT_TEXT = 240
_MAX_RESULT_ITEMS = 6
_MAX_RESULT_DEPTH = 4
_INSPECT_LIST_ITEM_LIMITS = {
    "constraints": 50,
    "degenerate_geometry": 50,
    "dependent_parameters": 50,
    "duplicate_edges": 50,
    "geometry": 50,
    "line_self_intersections": 50,
    "nonconstruction_edges": 50,
    "open_nodes": 50,
    "t_junction_nodes": 50,
    "tiny_edges": 50,
}


def _compact_text(value: Any, limit: int = _MAX_RESULT_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "." * max(0, limit)
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_provider_result(tool_name: str, value: Any) -> Any:
    return _compact_provider_value(tool_name, value, depth=0, key="")


def _result_item_limit(tool_name: str, key: str) -> int:
    if tool_name == "sketcher.inspect_sketch" and key in _INSPECT_LIST_ITEM_LIMITS:
        return _INSPECT_LIST_ITEM_LIMITS[key]
    return _MAX_RESULT_ITEMS


def _result_text_limit(key: str) -> int:
    if key in {"error", "err", "stderr"}:
        return 480
    if key in {"reason", "why", "status"}:
        return 120
    if key in {"name", "label", "feature", "active_sketch", "active_body"}:
        return 64
    return _MAX_RESULT_TEXT


def _compact_provider_value(
    tool_name: str,
    value: Any,
    *,
    depth: int,
    key: str,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _compact_text(value, _result_text_limit(key))
    if depth >= _MAX_RESULT_DEPTH:
        return _compact_text(value)
    if isinstance(value, (list, tuple)):
        item_limit = _result_item_limit(tool_name, key)
        return [
            item
            for item in (
                _compact_provider_value(tool_name, item, depth=depth + 1, key=key)
                for item in list(value)[:item_limit]
            )
            if item is not _OMIT
        ]
    if isinstance(value, dict):
        if key == "transaction":
            return _compact_transaction(tool_name, value, depth=depth)
        result: dict[str, Any] = {}
        top_level_has_payload = key == "" and any(
            str(candidate_key)
            not in {
                "ok",
                "transaction",
                "next_action",
                "next_actions",
                "suggested_next_actions",
                "warnings",
                "why",
            }
            for candidate_key in value
        )
        for raw_key, raw_item in value.items():
            item_key = str(raw_key)
            if _drop_result_field(
                tool_name,
                item_key,
                raw_item,
                parent_key=key,
                top_level_has_payload=top_level_has_payload,
            ):
                continue
            if item_key in _DROP_RESULT_KEYS:
                if item_key == "sketcher" and tool_name == "sketcher.inspect_sketch":
                    pass
                else:
                    continue
            compact = _compact_provider_value(
                tool_name,
                raw_item,
                depth=depth + 1,
                key=item_key,
            )
            if compact is _OMIT or compact in (None, "", [], {}):
                continue
            output_key = _PROVIDER_RESULT_KEY_ALIASES.get(item_key, item_key)
            if output_key in result and output_key != item_key:
                output_key = item_key
            result[output_key] = compact
        return result
    return _compact_text(value)


def _drop_result_field(
    tool_name: str,
    item_key: str,
    raw_item: Any,
    *,
    parent_key: str,
    top_level_has_payload: bool,
) -> bool:
    if (
        item_key == "transaction"
        and parent_key == ""
        and top_level_has_payload
        and _transaction_is_clean_success(raw_item)
    ):
        return True
    if item_key == "report_view_errors" and not _report_view_errors_have_signal(raw_item):
        return True
    if item_key in {"profile_validation", "profile_validation_deep"}:
        return tool_name != "sketcher.inspect_sketch"
    if parent_key == "solver_status" and item_key == "profile_status":
        return True
    if parent_key == "mutation" and item_key == "solver_status":
        return True
    if tool_name != "sketcher.inspect_sketch":
        if parent_key == "profile_status" and item_key in {
            "closed_edge_loop",
            "construction_geometry_count",
            "edge_count",
            "face_count",
            "sketch_label",
            "under_constrained",
        }:
            return True
        if parent_key == "solver_status" and item_key in {"sketch_label"}:
            return True
    if item_key == "partdesign" and tool_name != "partdesign.get_bodies":
        return True
    return False


def _transaction_is_clean_success(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not bool(value.get("ok")):
        return False
    for key in (
        "error",
        "aborted_transaction",
        "rollback_incomplete",
        "rolled_back_transaction",
        "created_object_cleanup",
    ):
        if value.get(key):
            return False
    if _report_view_errors_have_signal(value.get("report_view_errors")):
        return False
    verification = value.get("verification")
    if isinstance(verification, dict) and verification.get("ok") is False:
        return False
    return True


def _report_view_errors_have_signal(value: Any) -> bool:
    if not isinstance(value, dict):
        return bool(value)
    errors = value.get("errors")
    if isinstance(errors, list) and errors:
        return True
    if errors and not isinstance(errors, list):
        return True
    for key in (
        "error",
        "exception",
        "traceback",
        "tracebacks",
        "exceptions",
        "aborted_transaction",
        "rollback_incomplete",
    ):
        if value.get(key):
            return True
    for key in ("error_count", "new_error_count"):
        try:
            if int(value.get(key, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _compact_transaction(tool_name: str, transaction: dict[str, Any], *, depth: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _KEEP_TRANSACTION_KEYS:
        if key not in transaction:
            continue
        if key == "report_view_errors" and not _report_view_errors_have_signal(
            transaction[key]
        ):
            continue
        compact = _compact_provider_value(
            tool_name,
            transaction[key],
            depth=depth + 1,
            key=key,
        )
        if compact not in (None, "", [], {}):
            output_key = _PROVIDER_RESULT_KEY_ALIASES.get(key, key)
            if output_key in result and output_key != key:
                output_key = key
            result[output_key] = compact
    return result


def create_provider_tool(
    tool_name: str,
    function_name: str,
    schema: dict[str, Any],
    conn: Any,
    FunctionTool: Any,
) -> Any:
    async def _invoke(_tool_context, arguments_json: str):
        expanded_arguments_json = _expand_provider_argument_aliases(
            tool_name, arguments_json or "{}"
        )
        filtered_arguments_json = _filter_backend_arguments(
            schema, expanded_arguments_json
        )
        conn.send(
            {
                "type": "tool",
                "tool_name": tool_name,
                "arguments_json": filtered_arguments_json,
            }
        )
        response = conn.recv()
        if response.get("type") != "tool_result":
            return {"ok": False, "error": "Invalid VibeCAD tool bridge response."}
        return _compact_provider_result(
            tool_name,
            response.get("result", {"ok": False, "error": "Missing tool result."}),
        )

    return FunctionTool(
        name=provider_function_name(tool_name, function_name),
        description=tool_description(schema),
        params_json_schema=tool_json_schema(schema),
        on_invoke_tool=_invoke,
        strict_json_schema=False,
    )
