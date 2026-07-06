# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.solve``."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "description": (
        "Run the assembly kinematic solver to reposition components "
        "according to the existing joints. Use this after editing a part "
        "or a joint to bring the assembly back into a mated state. Returns "
        "the solver return code (0 means solved) and the resulting "
        "component placements. Requires at least one joint; ground one "
        "component and create joints first."
    ),
    "name": "assembly.solve",
    "parameters": {
        "properties": {
            "assembly_name": {
                "description": (
                    "Assembly name or label. Defaults to the first assembly "
                    "in the document."
                ),
                "type": "string",
            },
        },
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


def _classify_joints(joint_group: Any | None) -> tuple[list[Any], list[Any]]:
    """Split joint group children into (grounded joints, connecting joints)."""
    grounded: list[Any] = []
    connecting: list[Any] = []
    for child in list(getattr(joint_group, "Group", []) or []):
        if getattr(child, "ObjectToGround", None) is not None:
            grounded.append(child)
        elif hasattr(child, "JointType"):
            connecting.append(child)
    return grounded, connecting


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


def _component_placements(assembly: Any) -> dict[str, dict[str, Any]]:
    placements: dict[str, dict[str, Any]] = {}
    for child in list(getattr(assembly, "Group", []) or []):
        if getattr(child, "TypeId", "") == "Assembly::JointGroup":
            continue
        if not hasattr(child, "Placement"):
            continue
        placements[child.Name] = _placement_dict(child)
    return placements


def run(
    service,
    assembly_name: str | None = None,
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
                    "why": "Create an Assembly container before solving.",
                },
                {
                    "tool": "assembly.get_assemblies",
                    "why": "Inspect existing Assembly objects and their names.",
                },
            ],
        }
    joint_group = _joint_group(assembly)
    grounded, connecting = _classify_joints(joint_group)
    if not connecting:
        response: dict[str, Any] = {
            "ok": False,
            "error": (
                f"Assembly {assembly.Name} has no joints to solve. Ground "
                "one component and create joints between components first; "
                "the solver only repositions components connected by joints."
            ),
            "assembly": assembly.Name,
            "grounded_count": len(grounded),
            "joint_count": 0,
            "recoverable": True,
            "next_actions": [],
        }
        if not grounded:
            response["next_actions"].append(
                {
                    "tool": "assembly.ground_component",
                    "why": "Anchor one component so the solver has a fixed reference.",
                }
            )
        response["next_actions"].append(
            {
                "tool": "assembly.create_joint",
                "why": "Mate components by referencing their geometry.",
            }
        )
        return response

    def _solve() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        return_code = assembly.solve()
        doc.recompute()
        return {
            "document": doc.Name,
            "assembly": assembly.Name,
            "solver_return_code": int(return_code),
            "component_placements": _component_placements(assembly),
        }

    transaction = run_freecad_transaction(
        f"Solve assembly {assembly.Name}",
        _solve,
    )
    summary = domain_runtime.assembly_summary(service)
    result = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    solver_return_code = result.get("solver_return_code")
    solved = bool(transaction.get("ok")) and solver_return_code == 0
    response = {
        "ok": solved,
        "transaction": transaction,
        "assembly": result.get("assembly", getattr(assembly, "Name", None)),
        "solver_return_code": solver_return_code,
        "grounded_count": len(grounded),
        "joint_count": len(connecting),
        "component_placements": result.get("component_placements"),
        "assembly_summary": summary,
    }
    if not response["ok"]:
        if transaction.get("ok") and solver_return_code not in (0, None):
            response["error"] = (
                f"Assembly solver failed with return code {solver_return_code}. "
                "The joints may be over-constrained, conflicting, or reference "
                "geometry that no longer exists after a part edit."
            )
        else:
            response["error"] = transaction.get("error") or "Assembly solve failed."
        response["recoverable"] = True
        response["next_actions"] = [
            {
                "tool": "assembly.get_assemblies",
                "why": "Inspect assemblies, components, and existing joints.",
            },
            {
                "tool": "partdesign.find_subelements",
                "why": "Re-resolve joint reference geometry after part edits.",
            },
        ]
    return response
