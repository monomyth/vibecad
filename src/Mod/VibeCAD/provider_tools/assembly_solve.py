# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider function tool for ``assembly.solve``."""

from __future__ import annotations

from .base import create_provider_tool


TOOL_NAME = "assembly.solve"
FUNCTION_NAME = "assembly_solve"


def create(schema, conn, FunctionTool):
    return create_provider_tool(TOOL_NAME, FUNCTION_NAME, schema, conn, FunctionTool)
