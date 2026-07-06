# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.ground_component``."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {'description': 'Anchor one assembly component with a grounded joint so the '
                'kinematic solver has a fixed reference. Every assembly with joints needs '
                'exactly one grounded component; all other components are positioned '
                'relative to it by joints.',
 'name': 'assembly.ground_component',
 'parameters': {'properties': {'assembly_name': {'description': 'Assembly name or label. Defaults to the first assembly in the document.',
                                                 'type': 'string'},
                               'component_name': {'description': 'Name or label of the assembly component to ground.',
                                                  'type': 'string'}},
                'required': ['component_name'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'AssemblyWorkbench'}


def _joint_group(assembly: Any) -> Any | None:
    for child in list(getattr(assembly, "Group", []) or []):
        if getattr(child, "TypeId", "") == "Assembly::JointGroup":
            return child
    return None


def _grounded_components(joint_group: Any) -> list[str]:
    grounded = []
    for child in list(getattr(joint_group, "Group", []) or []):
        target = getattr(child, "ObjectToGround", None)
        if target is not None:
            grounded.append(getattr(target, "Name", str(target)))
    return grounded


def run(
    service,
    assembly_name: str | None = None,
    component_name: str = "",
) -> dict[str, Any]:
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
                    "why": "Create an Assembly container before grounding components.",
                },
                {
                    "tool": "assembly.get_assemblies",
                    "why": "Inspect existing Assembly objects and their names.",
                },
            ],
        }
    component = service._get_document_object(component_name)
    if component is None:
        return {
            "ok": False,
            "error": f"Component not found: {component_name}",
            "recoverable": True,
            "next_actions": [
                {
                    "tool": "core.get_active_document",
                    "why": "Inspect document object names and labels before retrying.",
                },
            ],
        }
    children = list(getattr(assembly, "Group", []) or [])
    if component not in children:
        return {
            "ok": False,
            "error": (
                f"Component {component.Name} is not a child of assembly "
                f"{assembly.Name}. Add it first."
            ),
            "recoverable": True,
            "next_actions": [
                {
                    "tool": "assembly.add_component",
                    "why": "Add the component to the assembly before grounding it.",
                },
            ],
        }

    def _ground_component() -> dict[str, Any]:
        import FreeCAD as App
        import JointObject

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        joint_group = _joint_group(assembly)
        if joint_group is None:
            joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
        already_grounded = _grounded_components(joint_group)
        if component.Name in already_grounded:
            return {
                "document": doc.Name,
                "assembly": assembly.Name,
                "component": component.Name,
                "grounded_joint": None,
                "already_grounded": True,
                "grounded_components": already_grounded,
            }
        ground = joint_group.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, component)
        if App.GuiUp:
            JointObject.ViewProviderGroundedJoint(ground.ViewObject)
        doc.recompute()
        return {
            "document": doc.Name,
            "assembly": assembly.Name,
            "component": component.Name,
            "grounded_joint": ground.Name,
            "already_grounded": False,
            "grounded_components": _grounded_components(joint_group),
        }

    transaction = run_freecad_transaction(
        f"Ground component {component.Name} in Assembly {assembly.Name}",
        _ground_component,
    )
    summary = domain_runtime.assembly_summary(service)
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    response = {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "assembly": result.get("assembly", getattr(assembly, "Name", None)),
        "component": result.get("component", getattr(component, "Name", None)),
        "grounded_joint": result.get("grounded_joint"),
        "already_grounded": bool(result.get("already_grounded", False)),
        "grounded_components": result.get("grounded_components", []),
        "assembly_summary": summary,
    }
    if not response["ok"]:
        response["error"] = transaction.get("error") or "Grounding assembly component failed."
        response["recoverable"] = True
        response["next_actions"] = [
            {
                "tool": "assembly.get_assemblies",
                "why": "Inspect assemblies, components, and existing joints before retrying.",
            },
        ]
    return response
