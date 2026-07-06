# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``assembly.get_assemblies``."""

from __future__ import annotations

TOOL_SPEC = {'description': 'Return Assembly workbench assemblies with component summaries, '
                'grounded/connecting joint counts, and joint details (type and '
                'both geometry references) for inspecting or repairing the '
                'kinematic structure.',
 'name': 'assembly.get_assemblies',
 'safety': 'READ',
 'workbench': 'AssemblyWorkbench'}


def run(service, **kwargs):
    assemblies = [service._assembly_summary(obj) for obj in service._assembly_objects()]
    return {"assembly_count": len(assemblies), "assemblies": assemblies}
