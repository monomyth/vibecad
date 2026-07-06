# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``core.get_tool_shape_report``."""

from __future__ import annotations

from VibeCADTools import SafetyLevel


TOOL_SPEC = {'description': 'Explain provider-visible VibeCAD tools, capability coverage, and '
                'missing CAD tool classes that can make results too primitive.',
 'name': 'core.get_tool_shape_report',
 'parameters': {'properties': {'full_workspace': {'description': 'When true, report '
                                                                 'the full provider-safe '
                                                                 'surface for the '
                                                                 'workspace instead of '
                                                                 'the legacy contract '
                                                                 'allowlist.',
                                                  'type': 'boolean'},
                               'workbench': {'description': 'Optional workbench name. '
                                                            'Defaults to the active '
                                                            'workbench.',
                                             'type': 'string'}},
                'type': 'object'},
 'safety': 'READ'}


def run(service, **kwargs):
    from VibeCADSession import is_provider_safe_tool
    from . import core_list_active_workbench_commands

    active = kwargs.get("workbench") or _active_workbench_name()
    apply_workbench_allowlist = not bool(kwargs.get("full_workspace"))
    provider_tools = [
        service.registry.get(name).to_schema(active_workbench=active)
        for name in service.registry.names()
        if is_provider_safe_tool(
            service,
            name,
            active,
            apply_workbench_allowlist=apply_workbench_allowlist,
        )
    ]
    provider_names = {tool["name"] for tool in provider_tools}
    all_registered = [service.registry.get(name) for name in service.registry.names()]
    registered_safe_write = sorted(tool.name for tool in all_registered if tool.safety is SafetyLevel.SAFE_WRITE)
    blocked_write = sorted(
        tool.name for tool in all_registered
        if tool.safety in {SafetyLevel.WRITE, SafetyLevel.DESTRUCTIVE}
    )
    thickness_tools = (
        {"partdesign.dressup"}
        if active == "PartDesignWorkbench"
        else {"part.dressup"}
    )
    capability_checks = {
        "document_lifecycle": {"core.create_new_document", "core.open_document"},
        "component_placement": {"part.set_placement"},
        "sketch_creation": {"partdesign.create_sketch"},
        "atomic_sketch_geometry": {"sketcher.add_geometry", "sketcher.add_slot"},
        "atomic_sketch_constraints": {"sketcher.add_constraint"},
        "sketch_rectangle_constraints": {"sketcher.draw_rectangle"},
        "sketch_dimension_edits": {"sketcher.edit_constraint"},
        "partdesign_pad_features": {"partdesign.extrude"},
        "partdesign_pocket_features": {"partdesign.extrude"},
        "partdesign_hole_features": {"partdesign.hole_from_sketch"},
        "partdesign_revolution_features": {"partdesign.revolve"},
        "partdesign_groove_features": {"partdesign.revolve"},
        "partdesign_loft_features": {"partdesign.loft_profiles"},
        "partdesign_sweep_features": {"partdesign.sweep_profile"},
        "partdesign_helix_features": {"partdesign.helix_profile"},
        "partdesign_pattern_features": {"partdesign.pattern"},
        "partdesign_mirror_features": {"partdesign.pattern"},
        "partdesign_datum_features": {"partdesign.create_datum_plane", "partdesign.create_datum_line"},
        "partdesign_draft_features": {"partdesign.dressup"},
        "partdesign_boolean_features": {"partdesign.boolean_bodies"},
        "partdesign_edge_finishing": {"partdesign.dressup"},
        "partdesign_feature_dimension_edits": {"partdesign.set_feature_dimensions"},
        "iterative_delete": {"core.delete_object"},
        "holes_and_openings": {"part.cut_cylindrical_hole"},
        "shells_and_wall_thickness": thickness_tools,
        "edge_rounding": {"part.dressup"},
        "edge_chamfering": {"part.dressup"},
        "material_appearance": {"material.apply_appearance"},
        "detail_drawings": {"techdraw.create_page", "techdraw.add_view"},
        "patterns_and_arrays": {"draft.create_array"},
        "freeform_surfaces": {"surface.create_surface"},
        "space_curves_3d": {"draft.create_wire"},
        "surface_to_solid": {"part.thicken_surface"},
        "assemblies": {"assembly.create_assembly"},
        "assembly_component_add": {"assembly.add_component"},
        "assembly_component_placement": {"assembly.set_component_placement"},
    "assembly_grounding": {"assembly.ground_component"},
    "kinematic_joints": {"assembly.create_joint"},
    "kinematic_solve": {"assembly.solve"},
        "visual_feedback": {"core.capture_view_screenshot"},
        "report_errors": {"core.get_report_view_errors"},
        "user_gui_continuation": {"core.wait_for_user_gui_action"},
    }
    capability_checks.update(_sketcher_capability_checks())
    capability_status = {}
    for capability, required in capability_checks.items():
        capability_status[capability] = {
            "available": bool(required.issubset(provider_names)),
            "tools": sorted(required.intersection(provider_names)),
            "missing_tools": sorted(required.difference(provider_names)),
        }
    try:
        command_summary = core_list_active_workbench_commands.run(service, workbench=active)
    except Exception as exc:
        command_summary = {"error": str(exc), "commands": []}
    missing_capabilities = [
        name for name, status in capability_status.items() if not status["available"]
    ]
    sketcher_human_command_coverage = _sketcher_human_command_coverage(provider_names)
    still_missing_tool_classes = [
        item["tool_class"]
        for item in sketcher_human_command_coverage
        if item["coverage"] != "covered"
    ] + [
        "assembly constraints/joints and kinematic relationships",
        "tolerances, fastener libraries, BOM automation, and manufacturing checks",
        "automated semantic visual quality gates beyond provider screenshot judgment",
    ]
    return {
        "active_workbench": active,
        "full_workspace": not apply_workbench_allowlist,
        "tool_pack_enabled": service.is_workbench_tool_pack_enabled(active),
        "provider_tool_count": len(provider_tools),
        "provider_tools": provider_tools,
        "provider_tool_names": sorted(provider_names),
        "recent_tool_shape_feedback": service._tool_shape_feedback[-10:],
        "registered_safe_write_tools": registered_safe_write,
        "blocked_write_tools": blocked_write,
        "capabilities": capability_status,
        "missing_capabilities": missing_capabilities,
        "sketcher_human_command_coverage": sketcher_human_command_coverage,
        "still_missing_tool_classes": still_missing_tool_classes,
        "human_workbench_command_count": len(command_summary.get("commands", []) or []),
        "human_workbench_command_sample": list(command_summary.get("commands", []) or [])[:80],
        "why_results_can_be_primitive": (
            "The provider can only create what appears in provider_tools. "
            "If a design needs one of still_missing_tool_classes, VibeCAD must either "
            "report the missing tool shape or the native tool surface must be expanded before it can reliably "
            "produce production-quality feature history."
        ),
    }


def _sketcher_capability_checks():
    return {
        "sketcher_profile_validation": {
            "sketcher.inspect_sketch",
        },
        "sketcher_solver_diagnosis": {
            "sketcher.inspect_sketch",
        },
        "sketcher_geometry_listing": {
            "sketcher.inspect_sketch",
            "sketcher.resolve_geometry",
        },
        "sketcher_constraint_listing": {
            "sketcher.inspect_sketch",
            "sketcher.edit_constraint",
        },
        "sketcher_external_geometry": {
            "sketcher.inspect_sketch",
            "sketcher.add_external_geometry",
            "sketcher.remove_external_geometry",
        },
        "sketcher_named_geometry_and_constraints": {
            "sketcher.set_geometry_name",
            "sketcher.edit_constraint",
        },
        "sketcher_point_editing": {
            "sketcher.move_point",
            "sketcher.transform_geometry",
        },
        "sketcher_curve_editing": {
            "sketcher.modify_geometry",
        },
        "sketcher_delete_editing": {
            "sketcher.delete_items",
        },
        "sketcher_construction_toggle": {"sketcher.set_construction"},
        "sketcher_detailed_constraints": {
            "sketcher.add_constraint",
            "sketcher.edit_constraint",
        },
    }


def _sketcher_human_command_coverage(provider_names):
    command_classes = [
        {
            "tool_class": "Sketcher create primitive/profile geometry",
            "representative_human_commands": [
                "Sketcher_CreateLine",
                "Sketcher_CreatePoint",
                "Sketcher_CreatePolyline",
                "Sketcher_CreateRectangle",
                "Sketcher_CreateCircle",
                "Sketcher_CreateArc",
                "Sketcher_CreateEllipse",
                "Sketcher_CreateBSpline",
                "Sketcher_CreateSlot",
            ],
            "provider_tools": [
                "sketcher.add_geometry",
                "sketcher.draw_rectangle",
                "sketcher.add_slot",
            ],
            "desired_provider_tools": [],
        },
        {
            "tool_class": "Sketcher named constraints and value edits",
            "representative_human_commands": [
                "Sketcher_ConstrainDistance",
                "Sketcher_ConstrainDistanceX",
                "Sketcher_ConstrainDistanceY",
                "Sketcher_ConstrainRadius",
                "Sketcher_ConstrainDiameter",
                "Sketcher_ConstrainAngle",
            ],
            "provider_tools": sorted(_sketcher_capability_checks()["sketcher_detailed_constraints"]),
            "desired_provider_tools": [],
        },
        {
            "tool_class": "Sketcher solver diagnosis and profile validation",
            "representative_human_commands": [
                "Sketcher_SelectConflictingConstraints",
                "Sketcher_SelectRedundantConstraints",
                "Sketcher_SelectElementsWithDoFs",
            ],
            "provider_tools": [
                "sketcher.inspect_sketch",
            ],
            "desired_provider_tools": [],
        },
        {
            "tool_class": "Sketcher curve repair and local editing",
            "representative_human_commands": [
                "Sketcher_Trimming",
                "Sketcher_Extend",
                "Sketcher_Split",
                "Sketcher_CreateFillet",
            ],
            "provider_tools": [
                "sketcher.modify_geometry",
            ],
            "desired_provider_tools": [],
        },
        {
            "tool_class": "Sketcher external/reference geometry",
            "representative_human_commands": [
                "Sketcher_External",
                "Sketcher_CarbonCopy",
            ],
            "provider_tools": [
                "sketcher.inspect_sketch",
                "sketcher.add_external_geometry",
                "sketcher.remove_external_geometry",
            ],
            "desired_provider_tools": ["sketcher.carbon_copy"],
        },
        {
            "tool_class": "Sketcher bulk transform and duplicate operations",
            "representative_human_commands": [
                "Sketcher_Copy",
                "Sketcher_Clone",
                "Sketcher_Move",
                "Sketcher_Translate",
                "Sketcher_Rotate",
                "Sketcher_Scale",
                "Sketcher_RectangularArray",
            ],
            "provider_tools": [
                "sketcher.move_point",
                "sketcher.transform_geometry",
            ],
            "desired_provider_tools": [
                "sketcher.clone_geometry",
            ],
        },
        {
            "tool_class": "Sketcher offset and derived-profile operations",
            "representative_human_commands": [
                "Sketcher_Offset",
                "Sketcher_Symmetry",
            ],
            "provider_tools": [
                "sketcher.add_constraint",
                "sketcher.transform_geometry",
            ],
            "desired_provider_tools": [],
        },
        {
            "tool_class": "Sketcher B-spline advanced editing",
            "representative_human_commands": [
                "Sketcher_BSplineInsertKnot",
                "Sketcher_BSplineIncreaseDegree",
                "Sketcher_BSplineDecreaseDegree",
                "Sketcher_JoinCurves",
            ],
            "provider_tools": ["sketcher.add_geometry"],
            "desired_provider_tools": [
                "sketcher.bspline_insert_knot",
                "sketcher.bspline_set_degree",
                "sketcher.join_curves",
            ],
        },
        {
            "tool_class": "Sketcher text geometry",
            "representative_human_commands": ["Sketcher_CreateText"],
            "provider_tools": [],
            "desired_provider_tools": ["sketcher.add_text"],
        },
        {
            "tool_class": "Sketcher bulk deletion and cleanup",
            "representative_human_commands": [
                "Sketcher_DeleteAllGeometry",
                "Sketcher_DeleteAllConstraints",
                "Sketcher_RemoveAxesAlignment",
            ],
            "provider_tools": [
                "sketcher.delete_items",
            ],
            "desired_provider_tools": [
                "sketcher.remove_axes_alignment",
            ],
        },
    ]
    coverage = []
    for item in command_classes:
        provider_tools = set(item["provider_tools"])
        desired_tools = set(item["desired_provider_tools"])
        present = sorted(provider_tools.intersection(provider_names))
        missing_existing = sorted(provider_tools.difference(provider_names))
        missing_desired = sorted(desired_tools.difference(provider_names))
        if not missing_existing and not missing_desired:
            status = "covered"
        elif present:
            status = "partial"
        else:
            status = "missing"
        coverage.append(
            {
                "tool_class": item["tool_class"],
                "coverage": status,
                "representative_human_commands": item["representative_human_commands"],
                "provider_tools": sorted(provider_tools),
                "available_provider_tools": present,
                "missing_existing_tools": missing_existing,
                "missing_desired_tools": missing_desired,
            }
        )
    return coverage


def _active_workbench_name():
    try:
        import FreeCADGui as Gui

        workbench = Gui.activeWorkbench()
        if workbench:
            return workbench.name()
    except Exception:
        pass
    return None
