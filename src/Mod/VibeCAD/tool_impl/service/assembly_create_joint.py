# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.create_joint``."""

from __future__ import annotations

import re
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


JOINT_TYPES = [
    "Fixed",
    "Revolute",
    "Cylindrical",
    "Slider",
    "Ball",
    "Distance",
    "Parallel",
    "Perpendicular",
    "Angle",
    "RackPinion",
    "Screw",
    "Gears",
    "Belt",
]

_JOINTS_NEEDING_DISTANCE = {"RackPinion", "Screw", "Gears", "Belt"}
_JOINTS_NEEDING_DISTANCE2 = {"Gears", "Belt"}

_ELEMENT_RE = re.compile(r"^(Face|Edge|Vertex)(\d+)$")

_OFFSET_SCHEMA = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"},
        "yaw_degrees": {"type": "number"},
        "pitch_degrees": {"type": "number"},
        "roll_degrees": {"type": "number"},
    },
}


TOOL_SPEC = {
    "description": (
        "Create a kinematic joint between two assembly components by "
        "referencing their geometry (faces, edges, vertices). The joint "
        "mates the referenced geometry and the assembly solver immediately "
        "repositions the components, returning the solver return code and "
        "resulting placements. Use joints instead of raw placements to mate "
        "parts: joints stay valid when parts are edited. Ground one "
        "component first with assembly.ground_component. Pick reference "
        "elements geometrically with partdesign.find_subelements instead of "
        "guessing positional names."
    ),
    "name": "assembly.create_joint",
    "parameters": {
        "properties": {
            "assembly_name": {
                "description": (
                    "Assembly name or label. Defaults to the first assembly "
                    "in the document."
                ),
                "type": "string",
            },
            "joint_type": {
                "description": (
                    "Kinematic joint type. Fixed locks all degrees of "
                    "freedom; Revolute allows rotation about the matched "
                    "axis; Cylindrical allows rotation plus translation "
                    "along the axis; Slider allows translation only; Ball "
                    "allows rotation about a point; Distance holds a "
                    "distance between references; Parallel/Perpendicular/"
                    "Angle constrain orientations; RackPinion, Screw, "
                    "Gears, Belt couple motions between two other joints' "
                    "components."
                ),
                "enum": JOINT_TYPES,
                "type": "string",
            },
            "component1": {
                "description": "Name or label of the first assembly component.",
                "type": "string",
            },
            "element1": {
                "description": (
                    "Subelement of component1 to mate, e.g. Face6, Edge3, "
                    "Vertex1. Omit to use the component origin."
                ),
                "type": "string",
            },
            "vertex1": {
                "description": (
                    "Optional anchor vertex on element1 (e.g. Vertex7) "
                    "selecting where along the element the joint coordinate "
                    "system sits. Defaults to the element itself (its "
                    "center for faces/circular edges)."
                ),
                "type": "string",
            },
            "component2": {
                "description": "Name or label of the second assembly component.",
                "type": "string",
            },
            "element2": {
                "description": (
                    "Subelement of component2 to mate. Omit to use the "
                    "component origin."
                ),
                "type": "string",
            },
            "vertex2": {
                "description": (
                    "Optional anchor vertex on element2. Defaults to the "
                    "element itself."
                ),
                "type": "string",
            },
            "offset1": {
                **_OFFSET_SCHEMA,
                "description": (
                    "Optional attachment offset applied to the first joint "
                    "coordinate system (mm and degrees)."
                ),
            },
            "offset2": {
                **_OFFSET_SCHEMA,
                "description": (
                    "Optional attachment offset applied to the second joint "
                    "coordinate system (mm and degrees)."
                ),
            },
            "distance": {
                "description": (
                    "Joint distance in mm: separation for Distance joints, "
                    "pitch radius for RackPinion, pitch for Screw, first "
                    "radius for Gears/Belt."
                ),
                "type": "number",
            },
            "distance2": {
                "description": "Second radius in mm for Gears/Belt joints.",
                "type": "number",
            },
            "angle_degrees": {
                "description": "Target angle in degrees for Angle joints.",
                "type": "number",
            },
            "length_min": {
                "description": (
                    "Optional minimum translation limit in mm "
                    "(Cylindrical/Slider joints)."
                ),
                "type": "number",
            },
            "length_max": {
                "description": (
                    "Optional maximum translation limit in mm "
                    "(Cylindrical/Slider joints)."
                ),
                "type": "number",
            },
            "angle_min": {
                "description": (
                    "Optional minimum rotation limit in degrees "
                    "(Revolute/Cylindrical joints)."
                ),
                "type": "number",
            },
            "angle_max": {
                "description": (
                    "Optional maximum rotation limit in degrees "
                    "(Revolute/Cylindrical joints)."
                ),
                "type": "number",
            },
            "label": {
                "description": "Optional label for the joint object.",
                "type": "string",
            },
        },
        "required": ["joint_type", "component1", "component2"],
        "type": "object",
    },
    "safety": "SAFE_WRITE",
    "workbench": "AssemblyWorkbench",
}


def _joint_group(assembly: Any) -> Any | None:
    for child in list(getattr(assembly, "Group", []) or []):
        if getattr(child, "TypeId", "") == "Assembly::JointGroup":
            return child
    return None


def _validate_element(component: Any, element: str) -> str | None:
    """Return an error string when ``element`` does not exist on ``component``."""
    shape = getattr(component, "Shape", None)
    if shape is None:
        return f"{component.Name} has no shape to reference."
    match = _ELEMENT_RE.match(element)
    if match is None:
        return (
            f"Unrecognized subelement name '{element}' on {component.Name}. "
            "Use names like Face6, Edge3, or Vertex1."
        )
    kind, index = match.group(1), int(match.group(2))
    counts = {
        "Face": len(getattr(shape, "Faces", []) or []),
        "Edge": len(getattr(shape, "Edges", []) or []),
        "Vertex": len(getattr(shape, "Vertexes", []) or []),
    }
    available = counts[kind]
    if index < 1 or index > available:
        return (
            f"{component.Name} has no {kind}{index}: the shape has "
            f"{available} {kind.lower()}(s)."
        )
    return None


def _placement_dict(obj: Any) -> dict[str, Any]:
    placement = obj.Placement
    euler = placement.Rotation.toEuler()
    return {
        "x": float(placement.Base.x),
        "y": float(placement.Base.y),
        "z": float(placement.Base.z),
        "yaw": float(euler[0]),
        "pitch": float(euler[1]),
        "roll": float(euler[2]),
    }


def _offset_placement(App: Any, offset: dict[str, Any]) -> Any:
    rotation = (
        App.Rotation(App.Vector(0, 0, 1), float(offset.get("yaw_degrees", 0.0)))
        * App.Rotation(App.Vector(0, 1, 0), float(offset.get("pitch_degrees", 0.0)))
        * App.Rotation(App.Vector(1, 0, 0), float(offset.get("roll_degrees", 0.0)))
    )
    return App.Placement(
        App.Vector(
            float(offset.get("x", 0.0)),
            float(offset.get("y", 0.0)),
            float(offset.get("z", 0.0)),
        ),
        rotation,
    )


def run(
    service,
    joint_type: str = "",
    component1: str = "",
    component2: str = "",
    assembly_name: str | None = None,
    element1: str | None = None,
    vertex1: str | None = None,
    element2: str | None = None,
    vertex2: str | None = None,
    offset1: dict[str, Any] | None = None,
    offset2: dict[str, Any] | None = None,
    distance: float | None = None,
    distance2: float | None = None,
    angle_degrees: float | None = None,
    length_min: float | None = None,
    length_max: float | None = None,
    angle_min: float | None = None,
    angle_max: float | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    if joint_type not in JOINT_TYPES:
        return {
            "ok": False,
            "error": f"Unknown joint type: {joint_type!r}.",
            "supported_joint_types": JOINT_TYPES,
            "recoverable": True,
        }
    assembly = service._get_assembly(assembly_name)
    if assembly is None:
        return {
            "ok": False,
            "error": "Assembly not found.",
            "requested": assembly_name,
            "recoverable": True,
            "next_actions": [
                {
                    "tool": "assembly.create_assembly",
                    "why": "Create an Assembly container before creating joints.",
                },
                {
                    "tool": "assembly.get_assemblies",
                    "why": "Inspect existing Assembly objects and their names.",
                },
            ],
        }
    comp1 = service._get_document_object(component1)
    comp2 = service._get_document_object(component2)
    for name, comp in ((component1, comp1), (component2, comp2)):
        if comp is None:
            return {
                "ok": False,
                "error": f"Component not found: {name}",
                "recoverable": True,
                "next_actions": [
                    {
                        "tool": "core.get_active_document",
                        "why": "Inspect document object names and labels before retrying.",
                    },
                ],
            }
    if comp1 is comp2:
        return {
            "ok": False,
            "error": "A joint needs two different components; both references "
            f"resolve to {comp1.Name}.",
            "recoverable": True,
        }
    children = list(getattr(assembly, "Group", []) or [])
    for comp in (comp1, comp2):
        if comp not in children:
            return {
                "ok": False,
                "error": (
                    f"Component {comp.Name} is not a child of assembly "
                    f"{assembly.Name}. Add it first."
                ),
                "recoverable": True,
                "next_actions": [
                    {
                        "tool": "assembly.add_component",
                        "why": "Add the component to the assembly before jointing it.",
                    },
                ],
            }
    for comp, element in ((comp1, element1), (comp2, element2)):
        for sub in (element, vertex1 if comp is comp1 else vertex2):
            if not sub:
                continue
            problem = _validate_element(comp, sub)
            if problem is not None:
                return {
                    "ok": False,
                    "error": problem,
                    "recoverable": True,
                    "next_actions": [
                        {
                            "tool": "partdesign.find_subelements",
                            "arguments": {"object_name": comp.Name},
                            "why": (
                                "Resolve valid face/edge/vertex names "
                                "geometrically instead of guessing."
                            ),
                        },
                    ],
                }
    if joint_type in _JOINTS_NEEDING_DISTANCE and distance is None:
        return {
            "ok": False,
            "error": f"{joint_type} joints require 'distance' "
            "(pitch radius, pitch, or first radius in mm).",
            "recoverable": True,
        }
    if joint_type in _JOINTS_NEEDING_DISTANCE2 and distance2 is None:
        return {
            "ok": False,
            "error": f"{joint_type} joints require 'distance2' (second radius in mm).",
            "recoverable": True,
        }

    def _create_joint() -> dict[str, Any]:
        import FreeCAD as App
        import JointObject

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        joint_group = _joint_group(assembly)
        if joint_group is None:
            joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
        joint = joint_group.newObject("App::FeaturePython", "Joint")
        JointObject.Joint(joint, JOINT_TYPES.index(joint_type))
        if App.GuiUp:
            JointObject.ViewProviderJoint(joint.ViewObject)
        if label:
            joint.Label = label
        if distance is not None:
            joint.Distance = float(distance)
        if distance2 is not None:
            joint.Distance2 = float(distance2)
        if angle_degrees is not None:
            joint.Angle = float(angle_degrees)
        if offset1:
            joint.Offset1 = _offset_placement(App, offset1)
        if offset2:
            joint.Offset2 = _offset_placement(App, offset2)
        if length_min is not None:
            joint.EnableLengthMin = True
            joint.LengthMin = float(length_min)
        if length_max is not None:
            joint.EnableLengthMax = True
            joint.LengthMax = float(length_max)
        if angle_min is not None:
            joint.EnableAngleMin = True
            joint.AngleMin = float(angle_min)
        if angle_max is not None:
            joint.EnableAngleMax = True
            joint.AngleMax = float(angle_max)
        ref1 = [comp1, [element1 or "", vertex1 or element1 or ""]]
        ref2 = [comp2, [element2 or "", vertex2 or element2 or ""]]
        joint.Proxy.setJointConnectors(joint, [ref1, ref2])
        doc.recompute()
        return_code = assembly.solve()
        doc.recompute()
        return {
            "document": doc.Name,
            "assembly": assembly.Name,
            "joint": joint.Name,
            "joint_label": joint.Label,
            "joint_type": joint_type,
            "solver_return_code": int(return_code),
            "reference1": {
                "component": comp1.Name,
                "element": element1 or "",
                "vertex": vertex1 or element1 or "",
            },
            "reference2": {
                "component": comp2.Name,
                "element": element2 or "",
                "vertex": vertex2 or element2 or "",
            },
            "component_placements": {
                comp1.Name: _placement_dict(comp1),
                comp2.Name: _placement_dict(comp2),
            },
        }

    transaction = run_freecad_transaction(
        f"Create {joint_type} joint between {comp1.Name} and {comp2.Name}",
        _create_joint,
    )
    summary = domain_runtime.assembly_summary(service)
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    solver_return_code = result.get("solver_return_code")
    solved = bool(transaction.get("ok")) and solver_return_code == 0
    response = {
        "ok": solved,
        "transaction": transaction,
        "assembly": result.get("assembly", getattr(assembly, "Name", None)),
        "joint": result.get("joint"),
        "joint_label": result.get("joint_label"),
        "joint_type": joint_type,
        "solver_return_code": solver_return_code,
        "reference1": result.get("reference1"),
        "reference2": result.get("reference2"),
        "component_placements": result.get("component_placements"),
        "assembly_summary": summary,
    }
    if not response["ok"]:
        if transaction.get("ok") and solver_return_code not in (0, None):
            response["error"] = (
                f"Joint created but the assembly solver failed with return "
                f"code {solver_return_code}. The mated geometry may be "
                "over-constrained or unreachable."
            )
        else:
            response["error"] = transaction.get("error") or "Joint creation failed."
        response["recoverable"] = True
        response["next_actions"] = [
            {
                "tool": "assembly.get_assemblies",
                "why": "Inspect assemblies, components, and existing joints before retrying.",
            },
            {
                "tool": "partdesign.find_subelements",
                "why": "Re-resolve the mating faces/edges geometrically.",
            },
        ]
    return response
