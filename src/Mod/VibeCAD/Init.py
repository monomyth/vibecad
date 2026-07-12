# SPDX-License-Identifier: LGPL-2.1-or-later

"""FreeCAD init script for the VibeCAD shared AI subsystem.

VibeCAD intentionally does not register a standalone workbench. Existing
workbenches opt in to native AI commands by calling VibeCADGui registration
helpers from their own InitGui.py files.
"""
