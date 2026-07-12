# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomically replace the active project's model-maintained design document."""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "name": "project.update_design_document",
    "description": (
        "Replace the active CAD project's complete design.md whenever accepted intent, "
        "required parts, interfaces, or verified remaining work has changed. Preserve the "
        "original outcome and all still-valid decisions. The document is working memory, "
        "not an approval gate. Use the exact revision supplied in design_document context."
    ),
    "safety": "SAFE_WRITE",
    "requires_document": True,
    "edit_modes": ["none", "sketch"],
    "parameters": {
        "type": "object",
        "properties": {
            "expected_revision": {
                "type": "string",
                "minLength": 64,
                "maxLength": 64,
                "description": "Exact current design_document revision.",
            },
            "markdown": {
                "type": "string",
                "minLength": 1,
                "description": "Complete replacement contents for design.md.",
            },
        },
        "required": ["expected_revision", "markdown"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    expected_revision: str,
    markdown: str,
) -> dict[str, Any]:
    return service.update_design_document(
        expected_revision=expected_revision,
        markdown=markdown,
    )
