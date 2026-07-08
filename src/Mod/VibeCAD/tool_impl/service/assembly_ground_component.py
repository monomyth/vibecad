# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.ground_component``."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from .assembly_common import resolve_existing_component
from . import domain_runtime


TOOL_SPEC = {'description': 'Ground one assembly component as the fixed solver reference. '
                'Use exactly one grounded component per jointed assembly.',
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
    resolved = resolve_existing_component(service, assembly, component_name)
    if not resolved.get("ok"):
        return {
            "ok": False,
            "error": resolved.get("error") or f"Component not found: {component_name}",
            "component_resolution": resolved.get("resolution"),
            "recoverable": True,
            "next_actions": [
                {
                    "tool": "assembly.add_component",
                    "arguments": {
                        "assembly_name": getattr(assembly, "Name", None),
                        "component_name": component_name,
                    },
                    "why": "Add the component to the assembly before grounding it.",
                },
            ],
        }
    component = resolved["object"]

    def _ground_component() -> dict[str, Any]:
        import FreeCAD as App
        import JointObject

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target_assembly = service._get_assembly(assembly.Name)
        if target_assembly is None:
            raise RuntimeError(f"Assembly not found: {assembly.Name}")
        target_resolved = resolve_existing_component(service, target_assembly, component.Name)
        if not target_resolved.get("ok"):
            raise RuntimeError(target_resolved.get("error") or f"Component not found: {component.Name}")
        target_component = target_resolved["object"]
        joint_group = _joint_group(target_assembly)
        if joint_group is None:
            joint_group = target_assembly.newObject("Assembly::JointGroup", "Joints")
        already_grounded = _grounded_components(joint_group)
        if target_component.Name in already_grounded:
            return {
                "document": doc.Name,
                "assembly": target_assembly.Name,
                "component": target_component.Name,
                "component_type": getattr(target_component, "TypeId", ""),
                "component_resolution": resolved.get("resolution"),
                "grounded_joint": None,
                "already_grounded": True,
                "grounded_components": already_grounded,
            }
        ground = joint_group.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, target_component)
        if App.GuiUp:
            JointObject.ViewProviderGroundedJoint(ground.ViewObject)
        doc.recompute()
        return {
            "document": doc.Name,
            "assembly": target_assembly.Name,
            "component": target_component.Name,
            "component_type": getattr(target_component, "TypeId", ""),
            "component_resolution": resolved.get("resolution"),
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
        "component_type": result.get("component_type", getattr(component, "TypeId", None)),
        "component_resolution": result.get("component_resolution", resolved.get("resolution")),
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
