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

# Machine-first machining is one coherent workflow: define/select a machine
# (limits, spindle, postprocessor), create a job bound to it, add tool
# controllers within spindle limits, create operations, validate the job
# against the machine, then post-process to G-code.
CAM_PACK_TOOL_NAMES: tuple[str, ...] = (
    "cam.define_machine",
    "cam.create_job",
    "cam.add_tool",
    "cam.create_operation",
    "cam.validate_job",
    "cam.postprocess",
    # Operation base geometry targets faces/edges; the geometric resolver
    # picks them deterministically instead of guessing element names.
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
        "assemblies",
        "Components, joints, solve, clash.",
        ("Assembly_",),
        ("Assembly::AssemblyObject",),
        ({"name": "assembly", "object_type": "Assembly::AssemblyObject"},),
        tool_names=ASSEMBLY_PACK_TOOL_NAMES,
    ),
    "BIMWorkbench": WorkbenchToolPack(
        "BIMWorkbench",
        "BIM",
        "Preserve BIM/IFC.",
        ("BIM_", "Arch_", "Draft_"),
        ("Arch::", "BIM::"),
        (
            {"name": "building", "object_type": "App::DocumentObjectGroup"},
            {"name": "level", "object_type": "App::DocumentObjectGroup"},
        ),
    ),
    "CAMWorkbench": WorkbenchToolPack(
        "CAMWorkbench",
        "CAM",
        "Machine, job, toolpath, validate, post.",
        ("CAM_",),
        (),
        ({"name": "job_container", "object_type": "App::DocumentObjectGroup"},),
        tool_names=CAM_PACK_TOOL_NAMES,
    ),
    "DraftWorkbench": WorkbenchToolPack(
        "DraftWorkbench",
        "drafting",
        "2D/3D curves, arrays.",
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
        "FEA",
        "Loads, mesh, solve.",
        ("Fem_",),
        ("Fem::",),
        (
            {"name": "analysis_group", "object_type": "App::DocumentObjectGroup"},
            {"name": "constraint_group", "object_type": "App::DocumentObjectGroup"},
        ),
    ),
    "InspectionWorkbench": WorkbenchToolPack(
        "InspectionWorkbench",
        "inspection",
        "Measure.",
        ("Inspection_",),
        (),
        ({"name": "inspection_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "MaterialWorkbench": WorkbenchToolPack(
        "MaterialWorkbench",
        "materials",
        "Material/appearance.",
        ("Material_", "Mat"),
        (),
        ({"name": "material_group", "object_type": "App::DocumentObjectGroup"},),
        tool_names=("material.apply_appearance",),
    ),
    "MeshWorkbench": WorkbenchToolPack(
        "MeshWorkbench",
        "mesh",
        "Inspect/repair mesh.",
        ("Mesh_",),
        ("Mesh::",),
        ({"name": "mesh_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "MeshPartWorkbench": WorkbenchToolPack(
        "MeshPartWorkbench",
        "mesh conversion",
        "Mesh<->BREP; verify source.",
        ("MeshPart_",),
        ("Mesh::", "Part::"),
        ({"name": "mesh_from_shape", "object_type": "Mesh::Feature"},),
    ),
    "NoneWorkbench": WorkbenchToolPack(
        "NoneWorkbench",
        "no active workbench",
        "Inspect; enter workspace.",
        (),
        (),
        ({"name": "context_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "OpenSCADWorkbench": WorkbenchToolPack(
        "OpenSCADWorkbench",
        "CSG",
        "Inspect CSG.",
        ("OpenSCAD_",),
        (),
        ({"name": "csg_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "PartDesignWorkbench": WorkbenchToolPack(
        "PartDesignWorkbench",
        "solids",
        "Body, sketch, feature. Match form; verify DoF/topology.",
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
        "BREP",
        "Direct BREP edit.",
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
        "Preserve cloud.",
        ("Points_",),
        ("Points::",),
        ({"name": "points_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "ReverseEngineeringWorkbench": WorkbenchToolPack(
        "ReverseEngineeringWorkbench",
        "reverse engineering",
        "Reconstruct surfaces.",
        ("ReverseEngineering_",),
        (),
        ({"name": "reverse_engineering_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "RobotWorkbench": WorkbenchToolPack(
        "RobotWorkbench",
        "robot simulation",
        "Trajectories/setup.",
        ("Robot_",),
        ("Robot::",),
        ({"name": "robot_simulation_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "SketcherWorkbench": WorkbenchToolPack(
        "SketcherWorkbench",
        "sketching",
        "Lines/arcs/splines/slots. Constrain; DoF 0.",
        ("Sketcher_",),
        ("Sketcher::SketchObject",),
        ({"name": "sketch", "object_type": "Sketcher::SketchObject"},),
        tool_names=SKETCHER_PACK_TOOL_NAMES,
    ),
    "SpreadsheetWorkbench": WorkbenchToolPack(
        "SpreadsheetWorkbench",
        "spreadsheet",
        "Aliases/formulas.",
        ("Spreadsheet_",),
        ("Spreadsheet::Sheet",),
        ({"name": "sheet", "object_type": "Spreadsheet::Sheet"},),
        tool_names=("spreadsheet.get_sheet",),
    ),
    "SurfaceWorkbench": WorkbenchToolPack(
        "SurfaceWorkbench",
        "surfaces",
        "Curves, fills, sections, thicken.",
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
        "drawings",
        "Pages/views.",
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
        "Read-only.",
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
