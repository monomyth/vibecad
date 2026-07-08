# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared helpers for typed Sketcher constraint tools."""

from __future__ import annotations


POINT_POSITIONS = {
    "whole": 0,
    "edge": 0,
    "curve": 0,
    "start": 1,
    "end": 2,
    "center": 3,
    "midpoint": 3,
    "origin": 1,
}


POINT_ROLE_ALIASES = {
    "all": "whole",
    "begin": "start",
    "beginning": "start",
    "centre": "center",
    "entity": "whole",
    "entire": "whole",
    "finish": "end",
    "last": "end",
    "mid": "midpoint",
    "middle": "midpoint",
    "object": "whole",
    "on_curve": "curve",
    "perimeter": "curve",
    "startpoint": "start",
    "start_point": "start",
    "endpoint_start": "start",
    "endpoint_end": "end",
    "end_point": "end",
    "endpoint": "end",
}

GENERIC_POINT_ROLES = {"point", "vertex", "node", "endpoint_any"}


def normalized_point_role(
    value: str | None,
    default: str = "whole",
    geometry_kind: str | None = None,
) -> str:
    clean = str(value or default or "whole").strip().lower()
    clean = POINT_ROLE_ALIASES.get(clean, clean)
    if clean in GENERIC_POINT_ROLES:
        kind = str(geometry_kind or "").strip().lower()
        if kind in {"circle", "part::geomcircle", "ellipse", "part::geomellipse"}:
            clean = "center"
        else:
            clean = POINT_ROLE_ALIASES.get(
                str(default or "whole").strip().lower(),
                str(default or "whole").strip().lower(),
            )
    if clean not in POINT_POSITIONS:
        raise ValueError(
            "point role must be one of: "
            + ", ".join(sorted(POINT_POSITIONS))
            + ". Common aliases accepted: "
            + ", ".join(sorted(point_role_aliases()))
        )
    return clean


def point_position(value: str, geometry_kind: str | None = None) -> int:
    return POINT_POSITIONS[normalized_point_role(value, geometry_kind=geometry_kind)]


def optional_point_position(
    value: str | None,
    geometry_handle: str | None = None,
    default: str = "whole",
    geometry_kind: str | None = None,
) -> int:
    if value is None:
        clean_handle = str(geometry_handle or "").strip().lower()
        if clean_handle in {"origin", "root", "rootpoint", "root_point"}:
            return POINT_POSITIONS["start"]
    return POINT_POSITIONS[
        normalized_point_role(value, default=default, geometry_kind=geometry_kind)
    ]


def point_role_enum() -> list[str]:
    return sorted(set(POINT_POSITIONS) | set(point_role_aliases()))


def point_role_aliases() -> dict[str, str]:
    aliases = dict(POINT_ROLE_ALIASES)
    aliases.update({role: role for role in GENERIC_POINT_ROLES})
    return aliases
