# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider tool definition for structured design-preflight submission."""

from __future__ import annotations

from .base import create_provider_tool


TOOL_NAME = "core.submit_design_preflight"
FUNCTION_NAME = "core_submit_design_preflight"


def create(schema, conn, FunctionTool):
    return create_provider_tool(TOOL_NAME, FUNCTION_NAME, schema, conn, FunctionTool)
