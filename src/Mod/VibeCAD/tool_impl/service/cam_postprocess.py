# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``cam.postprocess``."""

from __future__ import annotations

import os
from typing import Any

from . import cam_runtime, cam_validate_job


TOOL_SPEC = {
    "description": (
        "Post-process a CAM job to G-code. Validates against the machine first; "
        "error violations block output unless force=true."
    ),
    "name": "cam.postprocess",
    "parameters": {
        "type": "object",
        "properties": {
            "job_name": {
                "type": "string",
                "description": "Job name or label. Defaults to the first job in the document.",
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Destination file path for the G-code (e.g. part.nc or "
                    "part.ngc). Defaults to '<job>.nc' next to the document "
                    "or in the temp directory."
                ),
            },
            "postprocessor": {
                "type": "string",
                "description": (
                    "Postprocessor name override, e.g. 'grbl' or 'linuxcnc'. "
                    "Defaults to the machine's postprocessor, then the job's."
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Emit G-code even when machine validation reports error "
                    "severity violations. Default false."
                ),
            },
        },
    },
    "safety": "SAFE_WRITE",
    "workbench": "CAMWorkbench",
}


def _default_output_path(job: Any) -> str:
    import tempfile

    doc = getattr(job, "Document", None)
    doc_file = getattr(doc, "FileName", "") or ""
    base_dir = os.path.dirname(doc_file) if doc_file else tempfile.gettempdir()
    return os.path.join(base_dir, f"{getattr(job, 'Name', 'Job')}.nc")


def _resolve_postprocessor_name(job: Any, machine: Any, override: str) -> str | None:
    if override:
        return str(override)
    machine_post = getattr(machine, "postprocessor_file_name", None) if machine else None
    if machine_post:
        return str(machine_post)
    job_post = getattr(job, "PostProcessor", "") or ""
    return str(job_post) or None


def run(
    service,
    job_name: str = "",
    output_path: str = "",
    postprocessor: str = "",
    force: bool = False,
) -> dict[str, Any]:
    job = service._get_cam_job(job_name or None)
    if job is None:
        return cam_runtime.no_job_error(job_name or None)

    validation = cam_validate_job.validate(service, job)
    if not validation.get("ok", False):
        return validation
    errors = [v for v in validation.get("violations", []) if v.get("severity") == "error"]
    if errors and not force:
        return {
            "ok": False,
            "error": (
                f"Machine validation found {len(errors)} error(s); refusing to "
                "emit G-code. Fix the violations or pass force=true to "
                "override."
            ),
            "recoverable": True,
            "validation": validation,
            "next_actions": [
                {
                    "tool": "cam.validate_job",
                    "why": "Inspect the violations and fix tools/operations before retrying.",
                },
            ],
        }

    machine = cam_runtime.resolve_machine(getattr(job, "Machine", "") or None)
    post_name = _resolve_postprocessor_name(job, machine, postprocessor)
    if not post_name:
        return {
            "ok": False,
            "error": (
                "No postprocessor configured. Set one on the machine "
                "(cam.define_machine postprocessor=...) or pass the "
                "postprocessor parameter."
            ),
            "recoverable": True,
        }

    destination = output_path or _default_output_path(job)
    if not os.path.splitext(destination)[1]:
        destination += ".nc"

    try:
        from Path.Post.Processor import PostProcessorFactory

        post = PostProcessorFactory.get_post_processor(job, post_name)
    except Exception as exc:  # noqa: BLE001 - report loader failures to the model
        return {
            "ok": False,
            "error": f"Failed to load postprocessor '{post_name}': {exc}",
            "recoverable": True,
        }
    if post is None:
        return {
            "ok": False,
            "error": f"Postprocessor not found: {post_name}",
            "recoverable": True,
        }

    try:
        # Machine-bound jobs use the machine-configured pipeline (export2),
        # which honors output options such as tool length offset registers.
        # Legacy/script postprocessors only provide export().
        use_machine_flow = machine is not None and hasattr(post, "export2")
        sections = post.export2() if use_machine_flow else post.export()
    except Exception as exc:  # noqa: BLE001 - report postprocessing failures
        return {
            "ok": False,
            "error": f"Postprocessing with '{post_name}' failed: {exc}",
            "recoverable": True,
        }
    if not sections:
        return {
            "ok": False,
            "error": "Postprocessing produced no G-code sections.",
            "recoverable": True,
        }

    gcode = "\n".join(str(section[1]) for section in sections if section and section[1])
    if not gcode.strip():
        return {
            "ok": False,
            "error": "Postprocessing produced empty G-code output.",
            "recoverable": True,
        }

    try:
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write(gcode)
    except OSError as exc:
        return {
            "ok": False,
            "error": f"Could not write G-code to {destination}: {exc}",
            "recoverable": True,
        }

    lines = gcode.splitlines()
    response: dict[str, Any] = {
        "ok": True,
        "job": job.Name,
        "postprocessor": post_name,
        "output_path": destination,
        "line_count": len(lines),
        "gcode_preview": "\n".join(lines[:40]),
        "validation": {
            "valid": validation.get("valid"),
            "error_count": validation.get("error_count"),
            "warning_count": validation.get("warning_count"),
        },
    }
    if errors and force:
        response["warning"] = (
            f"G-code emitted despite {len(errors)} machine validation error(s) because force=true."
        )
        response["validation"]["forced"] = True
    return response
