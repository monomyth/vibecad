# SPDX-License-Identifier: LGPL-2.1-or-later

"""Workbench-specific VibeCAD tool-pack metadata.

Each pack declares the explicit provider tool names it contributes to the
model-facing tool surface. The active provider surface is always the shared
core tool set plus the entered pack's ``tool_names``; packs with an empty
``tool_names`` tuple rely on the core tools (document/view inspection,
``core.list_workbench_objects``, workspace switching) alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field


SKETCHER_PACK_TOOL_NAMES: tuple[str, ...] = (
    "sketcher.create_sketch",
    "sketcher.open_sketch",
    "sketcher.close_sketch",
    "sketcher.inspect_sketch",
    "sketcher.resolve_geometry",
    "sketcher.set_geometry_name",
    "sketcher.draw_rectangle",
    "sketcher.add_geometry",
    "sketcher.add_hole_pattern",
    "sketcher.add_slot",
    "sketcher.add_constraint",
    "sketcher.edit_constraint",
    "sketcher.move_point",
    "sketcher.transform_geometry",
    "sketcher.modify_geometry",
    "sketcher.add_external_geometry",
    "sketcher.remove_external_geometry",
    "sketcher.delete_items",
    "sketcher.set_construction",
)

# PartDesign owns its sketches, so the PartDesign pack includes the sketcher
# tool set in addition to the native feature tools. ``sketcher.create_sketch``
# is deliberately excluded: inside PartDesign, sketches must be created with
# ``partdesign.create_sketch`` so they belong to the active Body.
PARTDESIGN_PACK_TOOL_NAMES: tuple[str, ...] = (
    "partdesign.get_bodies",
    "partdesign.find_subelements",
    # Clearance/interference checks between bodies (for example rotor vs
    # housing) belong in the modeling loop, not only after a workbench switch.
    "assembly.check_interference",
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
) + tuple(
    name for name in SKETCHER_PACK_TOOL_NAMES if name != "sketcher.create_sketch"
)

PART_PACK_TOOL_NAMES: tuple[str, ...] = (
    "part.set_placement",
    "part.cut_cylindrical_hole",
    "part.dressup",
    "part.thicken_surface",
    # Geometric face/edge resolver: works on any shaped object, so the Part
    # pack exposes it for stable dressup/hole subelement selection too.
    "partdesign.find_subelements",
)

# Surface-first modeling is one coherent workflow: build 3D boundary curves,
# fill/loft surfaces between them, then thicken into a solid. The pack
# exposes all three stages plus the geometric subelement resolver.
SURFACE_PACK_TOOL_NAMES: tuple[str, ...] = (
    "surface.create_surface",
    "draft.create_wire",
    "part.thicken_surface",
    "partdesign.find_subelements",
)

ASSEMBLY_PACK_TOOL_NAMES: tuple[str, ...] = (
    "assembly.get_assemblies",
    "assembly.create_assembly",
    "assembly.add_component",
    "assembly.set_component_placement",
    # Kinematic mating: anchor one component, mate the rest with joints on
    # referenced geometry, then run the solver. Raw placement is layout,
    # not mating.
    "assembly.ground_component",
    "assembly.create_joint",
    "assembly.solve",
    # Joint references target faces/edges/vertices; the geometric resolver
    # picks them deterministically instead of guessing element names.
    "partdesign.find_subelements",
    "assembly.check_interference",
)

TECHDRAW_PACK_TOOL_NAMES: tuple[str, ...] = (
    "techdraw.get_pages",
    "techdraw.create_page",
    "techdraw.add_view",
)


@dataclass(frozen=True)
class WorkbenchToolPack:
    workbench: str
    domain: str
    instructions: str
    command_prefixes: tuple[str, ...]
    object_types: tuple[str, ...] = ()
    object_templates: tuple[dict[str, str], ...] = ()
    tool_names: tuple[str, ...] = field(default=())

    def summary(self) -> dict[str, object]:
        return {
            "workbench": self.workbench,
            "domain": self.domain,
            "instructions": self.instructions,
            "command_prefixes": list(self.command_prefixes),
            "object_types": list(self.object_types),
            "object_templates": list(self.object_templates),
            "tool_names": list(self.tool_names),
        }


WORKBENCH_TOOL_PACKS: dict[str, WorkbenchToolPack] = {
    "AssemblyWorkbench": WorkbenchToolPack(
        "AssemblyWorkbench",
        "assembly constraints and product structure",
        "Prefer assembly-aware inspection and joint commands before changing geometry.",
        ("Assembly_",),
        ("Assembly::AssemblyObject",),
        ({"name": "assembly", "object_type": "Assembly::AssemblyObject"},),
        tool_names=ASSEMBLY_PACK_TOOL_NAMES,
    ),
    "BIMWorkbench": WorkbenchToolPack(
        "BIMWorkbench",
        "building information modeling",
        "Preserve IFC/BIM semantics and prefer non-destructive inspection when IFC support is unavailable.",
        ("BIM_", "Arch_", "Draft_"),
        ("Arch::", "BIM::"),
        (
            {"name": "building", "object_type": "App::DocumentObjectGroup"},
            {"name": "level", "object_type": "App::DocumentObjectGroup"},
        ),
    ),
    "CAMWorkbench": WorkbenchToolPack(
        "CAMWorkbench",
        "toolpaths and manufacturing setup",
        "Treat CAM operations as high-risk until verified; inspect jobs before changing paths.",
        ("CAM_",),
        (),
        ({"name": "job_container", "object_type": "App::DocumentObjectGroup"},),
    ),
    "DraftWorkbench": WorkbenchToolPack(
        "DraftWorkbench",
        "2D drafting and annotation",
        "Prefer Draft commands for 2D geometry, snaps, dimensions, and annotation.",
        ("Draft_",),
        ("Part::Part2DObject",),
        (
            {"name": "draft_group", "object_type": "App::DocumentObjectGroup"},
            {"name": "annotation_group", "object_type": "App::DocumentObjectGroup"},
        ),
        tool_names=("draft.create_array", "draft.create_wire"),
    ),
    "FemWorkbench": WorkbenchToolPack(
        "FemWorkbench",
        "finite element analysis",
        "Inspect materials, constraints, mesh, and solver setup before changing analysis data.",
        ("Fem_",),
        ("Fem::",),
        (
            {"name": "analysis_group", "object_type": "App::DocumentObjectGroup"},
            {"name": "constraint_group", "object_type": "App::DocumentObjectGroup"},
        ),
    ),
    "InspectionWorkbench": WorkbenchToolPack(
        "InspectionWorkbench",
        "measurement and inspection",
        "Use inspection tools for measurement workflows and avoid geometry mutation by default.",
        ("Inspection_",),
        (),
        ({"name": "inspection_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "MaterialWorkbench": WorkbenchToolPack(
        "MaterialWorkbench",
        "materials",
        "Preserve material-card structure and prefer explicit material assignment actions.",
        ("Material_", "Mat"),
        (),
        ({"name": "material_group", "object_type": "App::DocumentObjectGroup"},),
        tool_names=("material.apply_appearance",),
    ),
    "MeshWorkbench": WorkbenchToolPack(
        "MeshWorkbench",
        "mesh repair and editing",
        "Treat mesh simplification and repair as destructive edits: inspect the mesh and state the intended repair before modifying it.",
        ("Mesh_",),
        ("Mesh::",),
        ({"name": "mesh_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "MeshPartWorkbench": WorkbenchToolPack(
        "MeshPartWorkbench",
        "mesh/part conversion",
        "Use MeshPart tessellation tools for explicit Part-to-mesh workflows; choose deviation/angle settings deliberately and verify the generated mesh against the source solid.",
        ("MeshPart_",),
        ("Mesh::", "Part::"),
        ({"name": "mesh_from_shape", "object_type": "Mesh::Feature"},),
    ),
    "NoneWorkbench": WorkbenchToolPack(
        "NoneWorkbench",
        "no active workbench",
        "Use core document, selection, and view tools until a modeling workbench is active.",
        (),
        (),
        ({"name": "context_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "OpenSCADWorkbench": WorkbenchToolPack(
        "OpenSCADWorkbench",
        "OpenSCAD import and CSG operations",
        "Inspect imported CSG trees before replacement or refinement operations.",
        ("OpenSCAD_",),
        (),
        ({"name": "csg_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "PartDesignWorkbench": WorkbenchToolPack(
        "PartDesignWorkbench",
        "parametric solid features",
        (
            "Model skeleton-first: create a Body, then a master layout sketch on an "
            "origin or datum plane that carries the governing dimensions, axes, and "
            "symmetry lines of the part. Downstream sketches reference that layout via "
            "external geometry instead of re-typing values; derived dimensions use "
            "constraint expressions so one governing change updates the whole part. "
            "Choose each feature by the surface character it must produce: pad/pocket "
            "for prismatic walls, revolve/groove for rotational bodies, loft_profiles "
            "or sweep_profile for blades, fins, ducts, and other flow or transition "
            "surfaces (never a straight pad), helix_profile for threads and springs. "
            "Order the feature tree deliberately: datums, base feature, additive, "
            "subtractive, patterns, dressups last. Fully constrain every sketch and "
            "verify each feature's shape delta against the intended dimensions before "
            "building on it."
        ),
        ("PartDesign_", "Sketcher_"),
        ("PartDesign::", "Sketcher::SketchObject"),
        (
            {"name": "body", "object_type": "PartDesign::Body"},
            {"name": "sketch", "object_type": "Sketcher::SketchObject"},
        ),
        tool_names=PARTDESIGN_PACK_TOOL_NAMES,
    ),
    "PartWorkbench": WorkbenchToolPack(
        "PartWorkbench",
        "boundary-representation solids",
        "Use Part operations for placement, holes, dressups, and boolean modeling; preserve object labels.",
        ("Part_",),
        ("Part::",),
        (
            {"name": "box", "object_type": "Part::Box"},
            {"name": "cylinder", "object_type": "Part::Cylinder"},
            {"name": "sphere", "object_type": "Part::Sphere"},
        ),
        tool_names=PART_PACK_TOOL_NAMES,
    ),
    "PointsWorkbench": WorkbenchToolPack(
        "PointsWorkbench",
        "point clouds",
        "Treat point-cloud modification as write operations and preserve original imports.",
        ("Points_",),
        ("Points::",),
        ({"name": "points_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "ReverseEngineeringWorkbench": WorkbenchToolPack(
        "ReverseEngineeringWorkbench",
        "reverse engineering",
        "Prefer inspection and surface reconstruction actions over destructive mesh edits.",
        ("ReverseEngineering_",),
        (),
        ({"name": "reverse_engineering_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "RobotWorkbench": WorkbenchToolPack(
        "RobotWorkbench",
        "robot simulation",
        "Inspect trajectories and robot setup before changing simulation data.",
        ("Robot_",),
        ("Robot::",),
        ({"name": "robot_simulation_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "SketcherWorkbench": WorkbenchToolPack(
        "SketcherWorkbench",
        "2D constrained sketches",
        (
            "Anchor every sketch to the origin and exploit symmetry about the axes. "
            "Reuse existing model edges through external geometry instead of redrawing "
            "or re-measuring them, and drive derived dimensions with constraint "
            "expressions referencing the governing values. Fully constrain sketches "
            "before they drive features; dimension from part function, not arbitrary "
            "values, and use construction geometry for layout lines and centers."
        ),
        ("Sketcher_",),
        ("Sketcher::SketchObject",),
        ({"name": "sketch", "object_type": "Sketcher::SketchObject"},),
        tool_names=SKETCHER_PACK_TOOL_NAMES,
    ),
    "SpreadsheetWorkbench": WorkbenchToolPack(
        "SpreadsheetWorkbench",
        "spreadsheet data",
        "Treat cell edits as document writes and preserve alias/formula relationships.",
        ("Spreadsheet_",),
        ("Spreadsheet::Sheet",),
        ({"name": "sheet", "object_type": "Spreadsheet::Sheet"},),
        tool_names=("spreadsheet.get_sheet",),
    ),
    "SurfaceWorkbench": WorkbenchToolPack(
        "SurfaceWorkbench",
        "surface modeling",
        (
            "Model surface-first: create boundary curves (sketches or 3D wires "
            "via draft.create_wire), fill or loft surfaces between them with "
            "surface.create_surface, then convert to a solid with "
            "part.thicken_surface when a manufacturable part is the goal. "
            "Inspect edge/face selection context before creating or changing "
            "surface features."
        ),
        ("Surface_",),
        ("Surface::",),
        (
            {"name": "filling", "object_type": "Surface::Filling"},
            {"name": "geom_fill_surface", "object_type": "Surface::GeomFillSurface"},
            {"name": "sections", "object_type": "Surface::Sections"},
        ),
        tool_names=SURFACE_PACK_TOOL_NAMES,
    ),
    "TechDrawWorkbench": WorkbenchToolPack(
        "TechDrawWorkbench",
        "technical drawing pages and views",
        "Prefer page/view/annotation commands and preserve drawing references.",
        ("TechDraw_",),
        ("TechDraw::",),
        (
            {"name": "page_group", "object_type": "App::DocumentObjectGroup"},
            {"name": "drawing_group", "object_type": "App::DocumentObjectGroup"},
        ),
        tool_names=TECHDRAW_PACK_TOOL_NAMES,
    ),
    "TestWorkbench": WorkbenchToolPack(
        "TestWorkbench",
        "test framework",
        "Prefer read-only inspection of test commands; run test commands only when the task explicitly calls for it.",
        ("Test_", "Std_Test"),
        (),
        ({"name": "test_group", "object_type": "App::DocumentObjectGroup"},),
    ),
}


def get_tool_pack(workbench: str | None) -> WorkbenchToolPack | None:
    if not workbench:
        return None
    return WORKBENCH_TOOL_PACKS.get(workbench)


def list_tool_packs() -> list[dict[str, object]]:
    return [pack.summary() for pack in WORKBENCH_TOOL_PACKS.values()]
