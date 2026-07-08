# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service tool definition for ``core.enter_workspace``."""

from __future__ import annotations


TOOL_SPEC = {
    "description": "Enter a FreeCAD workspace.",
    "name": "core.enter_workspace",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Workbench name.",
            },
        },
        "required": ["name"],
    },
    "safety": "VIEW",
}


def run(
    service,
    name: str,
    goal: str = "",
    reason: str = "",
) -> dict[str, object]:
    from tool_impl.service.core_activate_workbench import run as activate_workbench
    from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS, get_tool_pack

    requested = str(name or "").strip()
    normalized = _normalize_workspace_name(requested, WORKBENCH_TOOL_PACKS)
    result = activate_workbench(service, name=normalized)
    active = result.get("active")
    if active is None:
        active = result.get("active_workbench")
    known_workspace = get_tool_pack(normalized) is not None
    ok = bool(result.get("activated")) or active == normalized or known_workspace
    response: dict[str, object] = {
        "ok": ok,
        "requested": requested,
        "active_workbench": active or normalized,
        "workspace": active or normalized,
    }
    if normalized != requested:
        response["normalized"] = normalized
    if result.get("error"):
        if ok:
            response["activation_warning"] = result["error"]
        else:
            response["error"] = result["error"]
            response["recoverable"] = True
    return response


def _normalize_workspace_name(name: str, packs: dict[str, object]) -> str:
    clean = str(name or "").strip()
    if not clean:
        return clean
    if clean in packs:
        return clean
    folded = clean.casefold()
    for workbench in packs:
        if folded == workbench.casefold():
            return workbench
        if workbench.endswith("Workbench") and folded == workbench[:-9].casefold():
            return workbench

    root_matches: list[tuple[int, str]] = []
    prefix_matches: list[tuple[int, str]] = []
    for workbench, pack in packs.items():
        prefixes = getattr(pack, "command_prefixes", ()) or ()
        wb_root = workbench[:-9] if workbench.endswith("Workbench") else workbench
        for prefix in prefixes:
            if not prefix or not clean.startswith(prefix):
                continue
            score = len(str(prefix))
            if str(prefix).rstrip("_").casefold() == wb_root.casefold():
                root_matches.append((score, workbench))
            else:
                prefix_matches.append((score, workbench))
    matches = root_matches or prefix_matches
    if matches:
        return sorted(matches, key=lambda item: item[0], reverse=True)[0][1]
    return clean
