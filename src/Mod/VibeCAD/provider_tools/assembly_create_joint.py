# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider function tool for ``assembly.create_joint``."""

from __future__ import annotations

from .base import create_provider_tool


TOOL_NAME = "assembly.create_joint"
FUNCTION_NAME = "assembly_create_joint"


def create(schema, conn, FunctionTool):
    return create_provider_tool(TOOL_NAME, FUNCTION_NAME, schema, conn, FunctionTool)
