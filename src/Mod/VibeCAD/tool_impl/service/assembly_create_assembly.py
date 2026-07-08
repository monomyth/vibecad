# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.create_assembly``."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from .assembly_common import (
    body_state,
    capture_body_membership,
    container_memberships,
    group_refs,
    membership_delta,
    object_ref,
    partdesign_owner_body,
    resolve_component_to_add,
    restore_body_membership_if_changed,
)
from . import domain_runtime


TOOL_SPEC = {'description': 'Create a native Assembly container for real components. '
                'Add owning Bodies or top-level parts, then ground and joint them; '
                'raw placement is only layout.',
 'name': 'assembly.create_assembly',
 'parameters': {'properties': {'component_names': {'description': 'Existing object names or labels to add as components.',
                                                   'items': {'type': 'string'},
                                                   'type': 'array'},
                               'label': {'type': 'string'}},
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'AssemblyWorkbench'}


def run(
    service,
    label: str = "VibeCAD Assembly",
    component_names: list[str] | None = None,
) -> dict[str, Any]:
    resolved_components = []
    for component_name in component_names or []:
        resolved = resolve_component_to_add(service, component_name)
        if not resolved.get("ok"):
            response = {
                "ok": False,
                "error": resolved.get("error") or f"Component not found: {component_name}",
                "component_resolution": resolved.get("resolution"),
                "missing_components": [str(component_name)],
                "recoverable": True,
                "next_actions": [
                    {
                        "tool": "core.get_active_document",
                        "why": "Inspect document object names, labels, and object types before retrying.",
                    }
                ],
            }
            if resolved.get("suggested_component"):
                response["suggested_component"] = resolved["suggested_component"]
                response["next_actions"].insert(
                    0,
                    {
                        "tool": "assembly.create_assembly",
                        "arguments": {
                            "label": label,
                            "component_names": [resolved["suggested_component"].get("name")],
                        },
                        "why": "Create the assembly with the owning PartDesign Body, not a nested Body feature.",
                    },
                )
            return response
        resolved_components.append(resolved)

    def _create_assembly() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument or App.newDocument()
        assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
        assembly.Label = label
        assembly.Type = "Assembly"
        joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
        added = []
        add_results = []
        for resolved in resolved_components:
            component = doc.getObject(resolved["object"].Name)
            if component is None:
                raise RuntimeError(f"Component disappeared before assembly creation: {resolved['object'].Name}")
            body_obj = (
                component
                if getattr(component, "TypeId", "") == "PartDesign::Body"
                else partdesign_owner_body(service, component)
            )
            before_group = list(getattr(assembly, "Group", []) or [])
            before_membership = container_memberships(service, component)
            body_snapshot = capture_body_membership(body_obj)
            body_before = body_state(service, body_obj)
            try:
                assembly.addObject(component)
            except Exception:
                group = list(getattr(assembly, "Group", []) or [])
                if component not in group:
                    assembly.Group = group + [component]
            body_repair = restore_body_membership_if_changed(body_snapshot)
            if body_repair.get("changed"):
                doc.recompute()
            after_membership = container_memberships(service, component)
            body_after = body_state(service, body_obj)
            added.append(component.Name)
            add_results.append(
                {
                    "component": component.Name,
                    "component_label": getattr(component, "Label", component.Name),
                    "component_type": getattr(component, "TypeId", ""),
                    "component_resolution": resolved.get("resolution"),
                    "assembly_group_before": [object_ref(child) for child in before_group],
                    "assembly_group_after": group_refs(assembly),
                    "source_container_membership_before": before_membership,
                    "source_container_membership_after": after_membership,
                    "source_container_membership_delta": membership_delta(
                        before_membership,
                        after_membership,
                    ),
                    "body_state_before": body_before,
                    "body_state_after": body_after,
                    "body_state_repair": body_repair,
                }
            )
        doc.recompute()
        return {
            "document": doc.Name,
            "assembly": assembly.Name,
            "label": assembly.Label,
            "type": assembly.TypeId,
            "joint_group": joint_group.Name,
            "joint_group_type": joint_group.TypeId,
            "components_added": added,
            "component_add_results": add_results,
            "missing_components": [],
            "assembly_summary": domain_runtime.assembly_summary(service),
        }

    transaction = run_freecad_transaction(
        f"Create Assembly with {len(component_names or [])} components",
        _create_assembly,
    )
    summary = domain_runtime.assembly_summary(service)
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    response = {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "assembly": result.get("assembly"),
        "assembly_label": result.get("label"),
        "components_added": result.get("components_added", []),
        "component_add_results": result.get("component_add_results", []),
        "missing_components": result.get("missing_components", []),
        "assembly_summary": summary,
    }
    if not response["ok"]:
        response["error"] = transaction.get("error") or "Assembly creation failed."
        response["recoverable"] = True
        response["next_actions"] = [
            {
                "tool": "core.get_active_document",
                "why": "Inspect existing objects before retrying assembly creation.",
            },
            {
                "tool": "assembly.get_assemblies",
                "why": "Inspect current Assembly objects and component counts.",
            },
        ]
    return response
