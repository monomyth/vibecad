# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider tool definition for ``core.update_design_memory``."""

from __future__ import annotations

from .base import create_provider_tool


TOOL_NAME = "core.update_design_memory"
FUNCTION_NAME = "core_update_design_memory"


def create(schema, conn, FunctionTool):
    return create_provider_tool(TOOL_NAME, FUNCTION_NAME, schema, conn, FunctionTool)
