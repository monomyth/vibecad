# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``cam.validate_job``."""

from __future__ import annotations

from typing import Any

from . import cam_runtime


TOOL_SPEC = {
    "description": (
        "Validate a CAM job against its machine: spindle range, travel, and "
        "feed limits. Returns severity-tagged violations; postprocess blocks "
        "while errors remain unless forced."
    ),
    "name": "cam.validate_job",
    "parameters": {
        "type": "object",
        "properties": {
            "job_name": {
                "type": "string",
                "description": "Job name or label. Defaults to the first job in the document.",
            },
            "machine_name": {
                "type": "string",
                "description": (
                    "Optional machine to validate against, overriding the job's bound machine."
                ),
            },
        },
    },
    "safety": "READ",
    "workbench": "CAMWorkbench",
}


def validate(service, job, machine_name: str = "") -> dict[str, Any]:
    """Run machine validation for a resolved job; shared with cam.postprocess."""
    from Machine.models import validate_job_against_machine

    machine = None
    if machine_name:
        machine = cam_runtime.resolve_machine(machine_name)
        if machine is None:
            return {
                "ok": False,
                "error": f"Machine not found: {machine_name}",
                "recoverable": True,
                "available_machines": cam_runtime.available_machine_names(),
            }

    violations = validate_job_against_machine(job, machine=machine)
    errors = [v for v in violations if v.is_error]
    warnings = [v for v in violations if not v.is_error]
    resolved_machine = (
        machine
        if machine is not None
        else cam_runtime.resolve_machine(getattr(job, "Machine", "") or None)
    )
    result: dict[str, Any] = {
        "ok": True,
        "job": job.Name,
        "machine": getattr(resolved_machine, "name", None) or (getattr(job, "Machine", "") or None),
        "valid": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "violations": [v.to_dict() for v in violations],
    }
    if errors:
        result["next_action"] = (
            "Fix the reported violations (adjust spindle speeds, feeds, or "
            "operation depths/geometry) and validate again before "
            "postprocessing."
        )
    elif warnings:
        result["next_action"] = (
            "Review the warnings; the job can be postprocessed with cam.postprocess."
        )
    else:
        result["next_action"] = "The job passed validation. Emit G-code with cam.postprocess."
    return result


def run(service, job_name: str = "", machine_name: str = "") -> dict[str, Any]:
    job = service._get_cam_job(job_name or None)
    if job is None:
        return cam_runtime.no_job_error(job_name or None)
    return validate(service, job, machine_name=machine_name)
