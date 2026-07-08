# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.add_component``."""

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


TOOL_SPEC = {'description': 'Add an existing Body or top-level part to a native Assembly. '
                'Prefer the owning PartDesign Body over a nested feature object.',
 'name': 'assembly.add_component',
 'parameters': {'properties': {'assembly_name': {'description': 'Assembly name or label. Defaults to the first assembly in the document.',
                                                 'type': 'string'},
                               'component_name': {'description': 'Object name or label to add.',
                                                  'type': 'string'}},
                'required': ['component_name'],
                'type': 'object'},
 'safety': 'SAFE_WRITE',
 'workbench': 'AssemblyWorkbench'}


def run(
    service,
    assembly_name: str | None = None,
    component_name: str = "",
) -> dict[str, Any]:
    assembly = service._get_assembly(assembly_name)
    if assembly is None:
        return {"ok": False, "error": "Assembly not found.", "requested": assembly_name}
    resolved = resolve_component_to_add(service, component_name)
    if not resolved.get("ok"):
        response = {
            "ok": False,
            "error": resolved.get("error") or f"Component not found: {component_name}",
            "component_resolution": resolved.get("resolution"),
            "recoverable": True,
        }
        if resolved.get("suggested_component"):
            response["suggested_component"] = resolved["suggested_component"]
            response["next_actions"] = [
                {
                    "tool": "assembly.add_component",
                    "arguments": {
                        "assembly_name": getattr(assembly, "Name", None),
                        "component_name": resolved["suggested_component"].get("name"),
                    },
                    "why": "Add the owning PartDesign Body instead of a nested Body feature.",
                }
            ]
        return response
    component = resolved["object"]
    if component is assembly:
        return {"ok": False, "error": "Cannot add an assembly to itself."}

    def _add_component() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target_assembly = service._get_assembly(assembly.Name)
        if target_assembly is None:
            raise RuntimeError(f"Assembly not found: {assembly.Name}")
        target_component = service._get_document_object(component.Name)
        if target_component is None:
            raise RuntimeError(f"Component not found: {component.Name}")
        body_obj = (
            target_component
            if getattr(target_component, "TypeId", "") == "PartDesign::Body"
            else partdesign_owner_body(service, target_component)
        )
        before_group = list(getattr(target_assembly, "Group", []) or [])
        before_membership = container_memberships(service, target_component)
        body_snapshot = capture_body_membership(body_obj)
        body_before = body_state(service, body_obj)
        already_present = target_component in before_group
        if not already_present:
            try:
                target_assembly.addObject(target_component)
            except Exception:
                target_assembly.Group = before_group + [target_component]
        doc.recompute()
        body_repair = restore_body_membership_if_changed(body_snapshot)
        if body_repair.get("changed"):
            doc.recompute()
        after_membership = container_memberships(service, target_component)
        body_after = body_state(service, body_obj)
        return {
            "document": doc.Name,
            "assembly": target_assembly.Name,
            "assembly_label": getattr(target_assembly, "Label", target_assembly.Name),
            "component": target_component.Name,
            "component_label": getattr(target_component, "Label", target_component.Name),
            "component_type": getattr(target_component, "TypeId", ""),
            "component_resolution": resolved.get("resolution"),
            "already_present": already_present,
            "component_added_to_assembly": bool(
                not already_present
                and target_component in list(getattr(target_assembly, "Group", []) or [])
            ),
            "assembly_group_before": [object_ref(child) for child in before_group],
            "assembly_group_after": group_refs(target_assembly),
            "source_container_membership_before": before_membership,
            "source_container_membership_after": after_membership,
            "source_container_membership_delta": membership_delta(
                before_membership,
                after_membership,
            ),
            "body_state_before": body_before,
            "body_state_after": body_after,
            "body_state_repair": body_repair,
            "components": service._assembly_child_counts(target_assembly)["components"],
            "assembly_summary": domain_runtime.assembly_summary(service),
        }

    transaction = run_freecad_transaction(
        f"Add component {component.Name} to Assembly {assembly.Name}",
        _add_component,
    )
    summary = domain_runtime.assembly_summary(service)
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    response = {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "assembly": result.get("assembly", getattr(assembly, "Name", None)),
        "assembly_label": result.get("assembly_label", getattr(assembly, "Label", getattr(assembly, "Name", None))),
        "component": result.get("component", getattr(component, "Name", None)),
        "component_label": result.get("component_label", getattr(component, "Label", getattr(component, "Name", None))),
        "component_type": result.get("component_type", getattr(component, "TypeId", None)),
        "component_resolution": result.get("component_resolution", resolved.get("resolution")),
        "already_present": bool(result.get("already_present", False)),
        "component_added_to_assembly": bool(result.get("component_added_to_assembly", False)),
        "source_container_membership_delta": result.get("source_container_membership_delta"),
        "body_state_repair": result.get("body_state_repair"),
        "components": result.get("components"),
        "assembly_summary": summary,
    }
    if not response["ok"]:
        response["error"] = transaction.get("error") or "Adding assembly component failed."
        response["recoverable"] = True
        response["next_actions"] = [
            {
                "tool": "assembly.get_assemblies",
                "why": "Inspect available assemblies and current component membership.",
            },
            {
                "tool": "core.get_active_document",
                "why": "Inspect available document object names and labels before retrying.",
            },
        ]
    return response
