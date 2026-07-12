# SPDX-License-Identifier: LGPL-2.1-or-later

"""Headless, queryable CAM stock-removal and protected-model analysis."""

from __future__ import annotations

import math
import pathlib

import FreeCAD
import Part
import Path
import Path.Geom
import PathScripts.PathUtils as PathUtils


_LINEAR_MOTION = {"G0", "G1", "G2", "G3"}
_CUTTING_MOTION = {"G1", "G2", "G3"}
_CANNED_CYCLES = {"G73", "G81", "G82", "G83", "G85"}
_NON_GEOMETRIC = {
    "G4",
    "G17",
    "G18",
    "G19",
    "G20",
    "G21",
    "G54",
    "G55",
    "G56",
    "G57",
    "G58",
    "G59",
    "G80",
    "G90",
    "G91",
    "G94",
    "G98",
    "G99",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
}


def _canonical_command(name):
    value = str(name or "").strip().upper()
    if len(value) == 3 and value[0] in {"G", "M"} and value[1] == "0":
        return value[0] + value[2]
    return value


def _bounds(shape):
    if shape is None or shape.isNull():
        return None
    bb = shape.BoundBox
    values = [
        float(bb.XMin),
        float(bb.YMin),
        float(bb.ZMin),
        float(bb.XMax),
        float(bb.YMax),
        float(bb.ZMax),
    ]
    if not all(math.isfinite(value) for value in values):
        return None
    if bb.XLength < 0.0 or bb.YLength < 0.0 or bb.ZLength < 0.0:
        return None
    return {
        "min": [bb.XMin, bb.YMin, bb.ZMin],
        "max": [bb.XMax, bb.YMax, bb.ZMax],
        "size": [bb.XLength, bb.YLength, bb.ZLength],
    }


def _tool_shape_id(tool):
    raw = str(getattr(tool, "ShapeID", "") or "").strip()
    return pathlib.Path(raw).stem.casefold()


def _quantity(tool, name):
    if name not in getattr(tool, "PropertiesList", []):
        raise ValueError(f"Tool {tool.Name} does not expose required property {name}.")
    value = getattr(tool, name)
    return float(getattr(value, "Value", value))


def _radial_profile(tool, direction, position, axial_extension=0.0):
    """Create the exact supported cutter cross-section in a radial plane."""
    direction = FreeCAD.Vector(direction)
    direction.z = 0.0
    if direction.Length <= 1.0e-12:
        direction = FreeCAD.Vector(1.0, 0.0, 0.0)
    else:
        direction.normalize()
    radial = FreeCAD.Vector(direction.y, -direction.x, 0.0)
    center_bottom = FreeCAD.Vector(position)
    shape_id = _tool_shape_id(tool)
    diameter = _quantity(tool, "Diameter")
    if diameter <= 0.0:
        raise ValueError(f"Tool {tool.Name} has nonpositive Diameter {diameter}.")
    radius = diameter / 2.0

    if shape_id in {"endmill", "ballend"}:
        height = _quantity(tool, "CuttingEdgeHeight")
        if height <= 0.0:
            raise ValueError(
                f"Tool {tool.Name} has nonpositive CuttingEdgeHeight {height}."
            )
        center_top = center_bottom + FreeCAD.Vector(
            0.0, 0.0, height + float(axial_extension)
        )
        outer_top = center_top + radial * radius
        if shape_id == "endmill":
            outer_bottom = center_bottom + radial * radius
            return Part.Wire(
                [
                    Part.makeLine(center_bottom, outer_bottom),
                    Part.makeLine(outer_bottom, outer_top),
                    Part.makeLine(outer_top, center_top),
                ]
            )
        if height <= radius:
            raise ValueError(
                f"Ball-end tool {tool.Name} needs CuttingEdgeHeight greater than "
                f"its radius ({height} <= {radius})."
            )
        midpoint = center_bottom + radial * (radius * 0.5)
        midpoint.z += radius - math.sqrt(radius * radius - (radius * 0.5) ** 2)
        outer_sphere = center_bottom + radial * radius
        outer_sphere.z += radius
        return Part.Wire(
            [
                Part.Edge(Part.Arc(center_bottom, midpoint, outer_sphere)),
                Part.makeLine(outer_sphere, outer_top),
                Part.makeLine(outer_top, center_top),
            ]
        )

    if shape_id == "drill":
        length = _quantity(tool, "Length")
        tip_angle = _quantity(tool, "TipAngle")
        if length <= 0.0 or not 0.0 < tip_angle < 180.0:
            raise ValueError(
                f"Drill {tool.Name} needs positive Length and TipAngle between 0 and 180 degrees."
            )
        tip_height = radius / math.tan(math.radians(tip_angle / 2.0))
        if tip_height >= length:
            raise ValueError(
                f"Drill {tool.Name} tip height {tip_height} is not below Length {length}."
            )
        outer_tip = center_bottom + radial * radius + FreeCAD.Vector(0.0, 0.0, tip_height)
        center_top = center_bottom + FreeCAD.Vector(
            0.0, 0.0, length + float(axial_extension)
        )
        outer_top = center_top + radial * radius
        return Part.Wire(
            [
                Part.makeLine(center_bottom, outer_tip),
                Part.makeLine(outer_tip, outer_top),
                Part.makeLine(outer_top, center_top),
            ]
        )

    if shape_id in {"chamfer", "v-bit"}:
        height = _quantity(tool, "CuttingEdgeHeight")
        included_angle = _quantity(tool, "CuttingEdgeAngle")
        tip_radius = _quantity(tool, "TipDiameter") / 2.0
        if height <= 0.0 or not 0.0 < included_angle < 180.0:
            raise ValueError(
                f"Tool {tool.Name} needs positive CuttingEdgeHeight and "
                "CuttingEdgeAngle between 0 and 180 degrees."
            )
        if tip_radius < 0.0 or tip_radius >= radius:
            raise ValueError(
                f"Tool {tool.Name} needs TipDiameter smaller than Diameter."
            )
        cone_height = (radius - tip_radius) / math.tan(
            math.radians(included_angle / 2.0)
        )
        if cone_height > height:
            raise ValueError(
                f"Tool {tool.Name} cutting height {height} cannot contain its "
                f"specified cone height {cone_height}."
            )
        inner_bottom = center_bottom + radial * tip_radius
        outer_cone = center_bottom + radial * radius + FreeCAD.Vector(0.0, 0.0, cone_height)
        center_top = center_bottom + FreeCAD.Vector(
            0.0, 0.0, height + float(axial_extension)
        )
        outer_top = center_top + radial * radius
        return Part.Wire(
            [
                Part.makeLine(center_bottom, inner_bottom),
                Part.makeLine(inner_bottom, outer_cone),
                Part.makeLine(outer_cone, outer_top),
                Part.makeLine(outer_top, center_top),
            ]
        )

    raise ValueError(
        f"Tool shape {shape_id or '<missing>'} is not supported by native CAM analysis."
    )


def _swept_tool(tool, command, start):
    edge = Path.Geom.edgeForCmd(command, start)
    if edge is None:
        end = Path.Geom.commandEndPoint(command, FreeCAD.Vector(start))
        if float((end - FreeCAD.Vector(start)).Length) <= 1.0e-12:
            return None, end
        raise ValueError(
            f"{command.Name} targets {list(end)} but native edge construction failed."
        )
    end = edge.valueAt(edge.LastParameter)
    if _tool_shape_id(tool) == "endmill" and edge.BoundBox.ZLength <= 1.0e-12:
        radius = _quantity(tool, "Diameter") / 2.0
        height = _quantity(tool, "CuttingEdgeHeight")
        if type(edge.Curve).__name__ in {"Line", "LineSegment"}:
            direction = end - FreeCAD.Vector(start)
            direction.z = 0.0
            if direction.Length <= 1.0e-12:
                raise RuntimeError(f"Linear move {command.Name} has zero XY length.")
            direction.normalize()
            normal = FreeCAD.Vector(-direction.y, direction.x, 0.0) * radius
            start_point = FreeCAD.Vector(start)
            polygon = Part.makePolygon(
                [
                    start_point + normal,
                    end + normal,
                    end - normal,
                    start_point - normal,
                    start_point + normal,
                ]
            )
            center_prism = Part.Face(polygon).extrude(
                FreeCAD.Vector(0.0, 0.0, height)
            )
            start_cap = Part.makeCylinder(radius, height, start_point)
            end_cap = Part.makeCylinder(radius, height, end)
            swept_solid = center_prism.fuse([start_cap, end_cap]).removeSplitter()
            if (
                swept_solid.isNull()
                or not swept_solid.isValid()
                or len(swept_solid.Solids) != 1
            ):
                raise RuntimeError(
                    f"Exact linear endmill sweep for {command.Name} is not one valid solid."
                )
            return swept_solid, end
        if type(edge.Curve).__name__ == "Circle":
            swept_solid = _planar_circular_endmill_sweep(edge, radius, height)
            return swept_solid, end
        edge_z = float(edge.Vertexes[0].Point.z)
        planar_edge = edge.copy()
        planar_edge.translate(FreeCAD.Vector(0.0, 0.0, -edge_z))
        swept_area = Part.Wire(planar_edge).makeOffset2D(
            radius,
            join=0,
            fill=True,
            openResult=True,
        )
        swept_area.translate(FreeCAD.Vector(0.0, 0.0, edge_z))
        swept_solid = swept_area.extrude(FreeCAD.Vector(0.0, 0.0, height))
        if (
            swept_solid.isNull()
            or not swept_solid.isValid()
            or len(swept_solid.Solids) != 1
        ):
            raise RuntimeError(
                f"Exact planar endmill sweep for {command.Name} is not one valid solid."
            )
        return swept_solid.removeSplitter(), end
    delta = end - FreeCAD.Vector(start)
    if math.hypot(float(delta.x), float(delta.y)) <= 1.0e-12:
        lower = FreeCAD.Vector(start) if float(start.z) <= float(end.z) else FreeCAD.Vector(end)
        profile = _radial_profile(
            tool,
            FreeCAD.Vector(1.0, 0.0, 0.0),
            lower,
            axial_extension=abs(float(delta.z)),
        )
        vertices = list(profile.Vertexes)
        if len(vertices) < 2:
            raise RuntimeError(f"Axial tool profile for {command.Name} has no boundary.")
        closed_profile = Part.Wire(
            list(profile.Edges)
            + [Part.makeLine(vertices[-1].Point, vertices[0].Point)]
        )
        profile_face = Part.Face(closed_profile)
        solid = profile_face.revolve(
            lower, FreeCAD.Vector(0.0, 0.0, 1.0), 360.0
        )
        if solid.isNull() or not solid.isValid() or len(solid.Solids) != 1:
            raise RuntimeError(
                f"Axial tool sweep for {command.Name} is not one valid solid."
            )
        return solid.removeSplitter(), end
    start_direction = edge.tangentAt(edge.FirstParameter)
    end_direction = edge.tangentAt(edge.LastParameter)
    start_profile = _radial_profile(tool, start_direction, start)
    rotation = FreeCAD.Matrix()
    rotation.move(FreeCAD.Vector(start).negative())
    rotation.rotateZ(math.pi)
    rotation.move(FreeCAD.Vector(start))
    mirrored_profile = start_profile.transformGeometry(rotation)
    full_profile = Part.Wire([start_profile, mirrored_profile])
    path_wire = Part.Wire(edge)
    if edge.isClosed():
        solid = path_wire.makePipeShell([full_profile], True, True).removeSplitter()
        if solid.isNull() or not solid.isValid() or len(solid.Solids) != 1:
            raise RuntimeError(
                f"Closed-path tool sweep for {command.Name} is not one valid solid."
            )
        return solid, end
    path_shell = path_wire.makePipeShell([full_profile], False, True)
    start_cap = start_profile.revolve(start, FreeCAD.Vector(0.0, 0.0, 1.0), -180.0)
    end_profile = _radial_profile(tool, end_direction, end)
    end_cap = end_profile.revolve(end, FreeCAD.Vector(0.0, 0.0, 1.0), 180.0)
    shell = Part.makeShell(start_cap.Faces + path_shell.Faces + end_cap.Faces)
    solid = Part.makeSolid(shell).removeSplitter()
    if solid.isNull() or not solid.isValid() or len(solid.Solids) != 1:
        raise RuntimeError(f"Tool sweep for {command.Name} is not one valid solid.")
    return solid, end


def _planar_circular_endmill_sweep(edge, tool_radius, tool_height):
    curve = edge.Curve
    path_radius = float(curve.Radius)
    if path_radius <= 0.0:
        raise RuntimeError("Circular tool path has nonpositive radius.")
    center = FreeCAD.Vector(curve.Center)
    z = float(edge.valueAt(edge.FirstParameter).z)
    center.z = z
    outer_radius = path_radius + float(tool_radius)
    inner_radius = max(0.0, path_radius - float(tool_radius))

    if edge.isClosed():
        swept = Part.makeCylinder(outer_radius, tool_height, center)
        if inner_radius > 1.0e-12:
            swept = swept.cut(Part.makeCylinder(inner_radius, tool_height, center))
        if swept.isNull() or not swept.isValid() or len(swept.Solids) != 1:
            raise RuntimeError("Full-circle endmill sweep is not one valid solid.")
        return swept.removeSplitter()

    first_parameter = float(edge.FirstParameter)
    last_parameter = float(edge.LastParameter)
    middle_parameter = (first_parameter + last_parameter) / 2.0

    def radial_point(parameter, radius):
        point = edge.valueAt(parameter)
        radial = FreeCAD.Vector(point.x - center.x, point.y - center.y, 0.0)
        if radial.Length <= 1.0e-12:
            raise RuntimeError("Circular tool path contains a point at its center.")
        radial.normalize()
        return center + radial * radius

    outer_start = radial_point(first_parameter, outer_radius)
    outer_middle = radial_point(middle_parameter, outer_radius)
    outer_end = radial_point(last_parameter, outer_radius)
    outer_arc = Part.Edge(Part.Arc(outer_start, outer_middle, outer_end))
    boundary = [outer_arc]
    if inner_radius > 1.0e-12:
        inner_start = radial_point(first_parameter, inner_radius)
        inner_middle = radial_point(middle_parameter, inner_radius)
        inner_end = radial_point(last_parameter, inner_radius)
        boundary.extend(
            [
                Part.makeLine(outer_end, inner_end),
                Part.Edge(Part.Arc(inner_end, inner_middle, inner_start)),
                Part.makeLine(inner_start, outer_start),
            ]
        )
    else:
        boundary.extend(
            [
                Part.makeLine(outer_end, center),
                Part.makeLine(center, outer_start),
            ]
        )
    sector = Part.Face(Part.Wire(boundary)).extrude(
        FreeCAD.Vector(0.0, 0.0, tool_height)
    )
    start = edge.valueAt(first_parameter)
    end = edge.valueAt(last_parameter)
    swept = sector.fuse(
        [
            Part.makeCylinder(tool_radius, tool_height, start),
            Part.makeCylinder(tool_radius, tool_height, end),
        ]
    ).removeSplitter()
    if swept.isNull() or not swept.isValid() or len(swept.Solids) != 1:
        raise RuntimeError("Circular endmill sweep is not one valid solid.")
    return swept


def _expanded_cycle(command, position):
    params = dict(command.Parameters)
    missing = [name for name in ("X", "Y", "Z", "R") if params.get(name) is None]
    if missing:
        raise ValueError(
            f"Canned cycle {command.Name} is missing parameters: {', '.join(missing)}."
        )
    x = float(params["X"])
    y = float(params["Y"])
    z = float(params["Z"])
    retract = float(params["R"])
    return [
        Path.Command("G0", {"Z": retract}),
        Path.Command("G0", {"X": x, "Y": y, "Z": retract}),
        Path.Command("G1", {"X": x, "Y": y, "Z": z}),
        Path.Command("G0", {"X": x, "Y": y, "Z": retract}),
    ]


def analyze_operation(
    job,
    operation,
    simulation_resolution_mm,
    volume_tolerance=1.0e-7,
):
    """Simulate one generated operation and return structured native facts.

    Stock removal is computed by FreeCAD's native PathSimulator height field.
    Protected-model collision uses OpenCascade cutter-sweep intersections.
    Holder/fixture collision is reported separately when those shapes are not
    represented by the standard CAM job.
    """
    stock_obj = getattr(job, "Stock", None)
    stock = getattr(stock_obj, "Shape", None)
    if stock is None or stock.isNull() or not stock.isValid() or len(stock.Solids) != 1:
        raise ValueError("CAM analysis requires one valid solid stock shape.")
    controller = getattr(operation, "ToolController", None)
    tool = getattr(controller, "Tool", None)
    if controller is None or tool is None:
        raise ValueError("CAM analysis requires an explicit tool controller and tool bit.")
    model_objects = list(getattr(getattr(job, "Model", None), "Group", []) or [])
    model_shapes = [obj.Shape for obj in model_objects]
    if not model_shapes or any(shape.isNull() or not shape.isValid() for shape in model_shapes):
        raise ValueError("CAM analysis requires valid protected model shapes in the job.")
    protected_model = Part.makeCompound(model_shapes)
    commands = list(PathUtils.getPathWithPlacement(operation).Commands)
    if not commands:
        raise ValueError("CAM analysis requires a generated nonempty toolpath.")
    resolution = float(simulation_resolution_mm)
    if not math.isfinite(resolution) or resolution <= 0.0:
        raise ValueError("simulation_resolution_mm must be finite and positive.")
    grid_x = int(math.ceil(stock.BoundBox.XLength / resolution)) + 1
    grid_y = int(math.ceil(stock.BoundBox.YLength / resolution)) + 1
    grid_cells = grid_x * grid_y
    if grid_cells > 4_000_000:
        minimum_resolution = math.sqrt(
            stock.BoundBox.XLength * stock.BoundBox.YLength / 4_000_000.0
        )
        raise ValueError(
            "Requested CAM simulation exceeds 4,000,000 height-field cells; "
            f"use simulation_resolution_mm >= {minimum_resolution:.6g}."
        )
    tool_shape = getattr(tool, "Shape", None)
    if (
        tool_shape is None
        or tool_shape.isNull()
        or not tool_shape.isValid()
        or len(tool_shape.Solids) != 1
    ):
        raise ValueError("CAM analysis requires one valid solid tool-bit shape.")
    import PathSimulator

    simulator = PathSimulator.PathSim()
    simulator.BeginSimulation(stock, resolution)
    simulator.SetToolShape(tool_shape, resolution)
    endpoint_tolerance = max(1.0e-6, resolution * 1.0e-6)

    position = FreeCAD.Placement(
        FreeCAD.Vector(0.0, 0.0, stock.BoundBox.ZMax),
        FreeCAD.Rotation(),
    )
    executed = []
    unsupported = []
    collision_commands = []
    collision_shapes = []
    cut_sweep_count = 0

    def apply(command, source_index, rapid):
        nonlocal position, cut_sweep_count
        start = FreeCAD.Vector(position.Base)
        try:
            sweep, geometric_end = _swept_tool(tool, command, start)
        except Exception as exc:
            raise RuntimeError(
                f"Command {source_index} {_canonical_command(command.Name)} "
                f"with parameters {dict(command.Parameters)} failed tool-sweep "
                f"construction: {exc}"
            ) from exc
        position = simulator.ApplyCommand(position, command)
        if float((position.Base - geometric_end).Length) > endpoint_tolerance:
            raise RuntimeError(
                f"Native simulator endpoint {list(position.Base)} diverged from "
                f"Path.Geom endpoint {list(geometric_end)} at command {source_index} "
                f"with parameters {dict(command.Parameters)}."
            )
        if sweep is None:
            executed.append(
                {
                    "source_index": source_index,
                    "command": _canonical_command(command.Name),
                    "rapid": bool(rapid),
                    "no_motion": True,
                    "sweep_bounds": None,
                }
            )
            return
        overlap = sweep.common(protected_model)
        overlap_volume = float(overlap.Volume) if not overlap.isNull() else 0.0
        if overlap_volume > float(volume_tolerance):
            collision_shapes.append(overlap)
            collision_commands.append(
                {
                    "command_index": source_index,
                    "command": _canonical_command(command.Name),
                    "rapid": bool(rapid),
                    "protected_model_volume_mm3": overlap_volume,
                    "bounds": _bounds(overlap),
                }
            )
        if not rapid:
            cut_sweep_count += 1
        executed.append(
            {
                "source_index": source_index,
                "command": _canonical_command(command.Name),
                "rapid": bool(rapid),
                "sweep_bounds": _bounds(sweep),
            }
        )

    for index, command in enumerate(commands):
        name = _canonical_command(command.Name)
        if not name or name.startswith("("):
            continue
        if name in _LINEAR_MOTION:
            apply(command, index, rapid=name == "G0")
            continue
        if name in _CANNED_CYCLES:
            for expanded in _expanded_cycle(command, position.Base):
                apply(expanded, index, rapid=_canonical_command(expanded.Name) == "G0")
            continue
        if name in _NON_GEOMETRIC:
            continue
        unsupported.append({"command_index": index, "command": name})

    if unsupported:
        return {
            "complete": False,
            "stage": "command_interpretation",
            "error": {
                "code": "unsupported_path_commands",
                "message": "The generated path contains commands not supported by native analysis.",
            },
            "unsupported_commands": unsupported,
            "executed_sweeps": len(executed),
        }
    if cut_sweep_count == 0:
        return {
            "complete": False,
            "stage": "stock_removal",
            "error": {
                "code": "no_cutting_sweeps",
                "message": "The path contains no analyzable cutting sweeps.",
            },
            "unsupported_commands": [],
            "executed_sweeps": len(executed),
        }

    simulation_stats = simulator.GetSimulationStats()
    if int(simulation_stats.get("unsupported_commands", 0) or 0) != 0:
        return {
            "complete": False,
            "stage": "native_stock_simulation",
            "error": {
                "code": "native_simulator_unsupported_commands",
                "message": "PathSimulator received unsupported motion commands.",
            },
            "native_simulation": simulation_stats,
        }
    if int(simulation_stats.get("cut_commands", 0) or 0) != cut_sweep_count:
        return {
            "complete": False,
            "stage": "native_stock_simulation",
            "error": {
                "code": "native_simulator_command_mismatch",
                "message": "PathSimulator cutting-command count differs from interpreted path state.",
            },
            "expected_cut_commands": cut_sweep_count,
            "native_simulation": simulation_stats,
        }
    collision_shape = None
    if collision_shapes:
        collision_shape = collision_shapes[0]
        if len(collision_shapes) > 1:
            collision_shape = collision_shape.multiFuse(collision_shapes[1:]).removeSplitter()
        if collision_shape.isNull() or not collision_shape.isValid():
            raise RuntimeError("Protected-model collision union is null or invalid.")
    collision_volume = float(collision_shape.Volume) if collision_shape is not None else 0.0
    return {
        "complete": True,
        "stage": "complete",
        "error": None,
        "simulation_scope": "single_operation_against_initial_job_stock",
        "command_count": len(commands),
        "executed_sweeps": len(executed),
        "cutting_sweeps": cut_sweep_count,
        "unsupported_commands": [],
        "stock": {
            "object": stock_obj.Name,
            "method": "PathSimulator height field",
            "volume_method": "height_field_cell_quadrature",
            "endpoint_tolerance_mm": endpoint_tolerance,
            **simulation_stats,
        },
        "collision": {
            "protected_model_checked": True,
            "method": "OpenCascade cutter-sweep intersection",
            "volume_tolerance_mm3": float(volume_tolerance),
            "protected_model_collision": collision_volume > float(volume_tolerance),
            "protected_model_volume_mm3": collision_volume,
            "protected_model_bounds": _bounds(collision_shape),
            "commands": collision_commands,
            "holder_checked": False,
            "fixture_checked": False,
            "unavailable_checks": [
                "holder collision: the job has no holder shape",
                "fixture collision: the job has no fixture shapes",
            ],
        },
    }
