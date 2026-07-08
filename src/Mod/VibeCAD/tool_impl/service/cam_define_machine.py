# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``cam.define_machine``."""

from __future__ import annotations

from typing import Any

from . import cam_runtime


TOOL_SPEC = {
    "description": (
        "Define a saved CNC machine: axis limits, spindle range, postprocessor, "
        "and tool-length-offset behavior. CAM jobs validate against it."
    ),
    "name": "cam.define_machine",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Machine display name, e.g. 'Shop Router'.",
            },
            "manufacturer": {
                "type": "string",
                "description": "Optional manufacturer name.",
            },
            "description": {
                "type": "string",
                "description": "Optional free-form machine description.",
            },
            "linear_axes": {
                "type": "array",
                "description": (
                    "Linear axes with travel limits. Defaults to X/Y/Z with "
                    "0..300 mm travel when omitted."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Axis name: X, Y, or Z.",
                        },
                        "min_limit": {"type": "number", "description": "Minimum travel (mm)."},
                        "max_limit": {"type": "number", "description": "Maximum travel (mm)."},
                        "max_velocity": {
                            "type": "number",
                            "description": "Maximum axis velocity (mm/min).",
                        },
                    },
                    "required": ["name"],
                },
            },
            "rotary_axes": {
                "type": "array",
                "description": "Optional rotary axes (A/B/C) with angular limits in degrees.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Axis name: A, B, or C.",
                        },
                        "min_limit": {"type": "number", "description": "Minimum angle (deg)."},
                        "max_limit": {"type": "number", "description": "Maximum angle (deg)."},
                        "max_velocity": {
                            "type": "number",
                            "description": "Maximum angular velocity (deg/min).",
                        },
                    },
                    "required": ["name"],
                },
            },
            "spindle": {
                "type": "object",
                "description": "Spindle capabilities used for RPM safety validation.",
                "properties": {
                    "name": {"type": "string", "description": "Spindle name."},
                    "max_rpm": {"type": "number", "description": "Maximum spindle RPM."},
                    "min_rpm": {"type": "number", "description": "Minimum spindle RPM."},
                    "max_power_kw": {"type": "number", "description": "Peak power (kW)."},
                    "tool_change": {
                        "type": "string",
                        "description": "Tool change style: 'manual' or 'automatic'.",
                    },
                },
            },
            "postprocessor": {
                "type": "string",
                "description": ("Post-processor name, e.g. 'grbl', 'linuxcnc', 'mach3_mach4'."),
            },
            "output_tool_length_offset": {
                "type": "boolean",
                "description": (
                    "Emit tool length offsets (G43 H-words) after tool changes. "
                    "Enable only when the controller has an offset table."
                ),
            },
        },
        "required": ["name"],
    },
    "safety": "SAFE_WRITE",
    "workbench": "CAMWorkbench",
}


_DEFAULT_LINEAR_AXES = (
    {"name": "X", "min_limit": 0, "max_limit": 300, "max_velocity": 3000},
    {"name": "Y", "min_limit": 0, "max_limit": 300, "max_velocity": 3000},
    {"name": "Z", "min_limit": 0, "max_limit": 300, "max_velocity": 1500},
)


def run(
    service,
    name: str = "",
    manufacturer: str = "",
    description: str = "",
    linear_axes: list[dict[str, Any]] | None = None,
    rotary_axes: list[dict[str, Any]] | None = None,
    spindle: dict[str, Any] | None = None,
    postprocessor: str = "",
    output_tool_length_offset: bool = False,
) -> dict[str, Any]:
    if not name or not str(name).strip():
        return {
            "ok": False,
            "error": "Machine name is required.",
            "recoverable": True,
        }
    name = str(name).strip()

    try:
        from Machine.models import Machine, MachineFactory
    except ImportError as exc:
        return {
            "ok": False,
            "error": f"CAM machine models are unavailable: {exc}",
            "recoverable": False,
        }

    machine = Machine(
        name=name,
        manufacturer=str(manufacturer or ""),
        description=str(description or ""),
    )

    axes = list(linear_axes) if linear_axes else [dict(a) for a in _DEFAULT_LINEAR_AXES]
    for axis in axes:
        axis_name = str(axis.get("name", "")).upper()
        direction = cam_runtime.LINEAR_AXIS_DIRECTIONS.get(axis_name)
        if direction is None:
            return {
                "ok": False,
                "error": (
                    f"Unknown linear axis '{axis.get('name')}'. "
                    f"Supported: {sorted(cam_runtime.LINEAR_AXIS_DIRECTIONS)}."
                ),
                "recoverable": True,
            }
        machine.add_linear_axis(
            axis_name,
            direction,
            min_limit=float(axis.get("min_limit", 0)),
            max_limit=float(axis.get("max_limit", 300)),
            max_velocity=float(axis.get("max_velocity", 3000)),
        )

    for axis in rotary_axes or []:
        axis_name = str(axis.get("name", "")).upper()
        direction = cam_runtime.ROTARY_AXIS_DIRECTIONS.get(axis_name)
        if direction is None:
            return {
                "ok": False,
                "error": (
                    f"Unknown rotary axis '{axis.get('name')}'. "
                    f"Supported: {sorted(cam_runtime.ROTARY_AXIS_DIRECTIONS)}."
                ),
                "recoverable": True,
            }
        machine.add_rotary_axis(
            axis_name,
            direction,
            min_limit=float(axis.get("min_limit", -360)),
            max_limit=float(axis.get("max_limit", 360)),
            max_velocity=float(axis.get("max_velocity", 36000)),
        )

    spindle = dict(spindle or {})
    machine.add_spindle(
        str(spindle.get("name", "Spindle")),
        id=0,
        max_power_kw=float(spindle.get("max_power_kw", 0)),
        max_rpm=float(spindle.get("max_rpm", 0)),
        min_rpm=float(spindle.get("min_rpm", 0)),
        tool_change=str(spindle.get("tool_change", "manual")),
    )

    if postprocessor:
        machine.postprocessor_file_name = str(postprocessor)
    machine.output.output_tool_length_offset = bool(output_tool_length_offset)

    try:
        saved_path = MachineFactory.save_configuration(machine)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to save machine configuration: {exc}",
            "recoverable": True,
        }

    return {
        "ok": True,
        "machine": cam_runtime.machine_summary(machine),
        "saved_path": str(saved_path),
        "available_machines": cam_runtime.available_machine_names(),
        "next_action": (
            "Assign this machine to a CAM job with cam.create_job so validation "
            "and post-processing enforce its limits."
        ),
    }
