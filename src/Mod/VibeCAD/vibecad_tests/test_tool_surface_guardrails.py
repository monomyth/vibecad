# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guardrail: every provider-callable tool is deliberate and structurally safe.

Four invariants are enforced:

1. No orphan tools — every registered tool spec is surfaced through
   ``CORE_PROVIDER_TOOLS`` or at least one workbench pack. A tool registered
   without pack membership fails this test, so legacy or experimental tools
   cannot silently become callable by default.
2. No dangling names — every name in ``CORE_PROVIDER_TOOLS`` and every pack
   ``tool_names``/``required_adjacent_tool_names`` entry resolves to a
   registered, validating :class:`ToolSpec`.
3. Writes are transactional — every non-READ tool either contains a FreeCAD
   transaction marker in its own module or in a same-package module it
   imports, or appears in a justified allowlist.
4. No legacy command execution — ``tool_impl`` never contains
   ``runCommand``/``doCommand``/``sendMsgToActiveView``; all FreeCAD
   semantics run through the typed Python APIs.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import re
from typing import Any, Iterator

import pytest

from VibeCADTools import SafetyLevel, ToolSpec

TOOL_PACKAGES = ("tool_impl.service", "tool_impl.sketcher")

TOOL_IMPL_DIR = Path(__file__).resolve().parent.parent / "tool_impl"

# Write-safety tools that legitimately run without a
# FreeCAD document transaction. Each entry needs a reason.
TRANSACTION_EXEMPT = {
    # Enters native sketch edit mode; changes UI state, not document data.
    "partdesign.edit_sketch",
    # Writes the project design document (a project file), not a FreeCAD doc.
    "project.update_design_document",
}

TRANSACTION_MARKERS = ("run_freecad_transaction", "openTransaction")

FORBIDDEN_COMMAND_STRINGS = ("runCommand", "doCommand", "sendMsgToActiveView")

_INTRA_PACKAGE_IMPORT = re.compile(
    r"^from\s+\.\s+import\s+(?P<plain>[\w,\s]+)$|^from\s+\.(?P<dotted>\w+)\s+import\s+",
    re.MULTILINE,
)


def _collect_specs() -> dict[str, tuple[ToolSpec, Path, str]]:
    """Return {tool name: (validated spec, module path, package name)}."""
    specs: dict[str, tuple[ToolSpec, Path, str]] = {}
    for package_name in TOOL_PACKAGES:
        package = import_module(package_name)
        for module_name in package.TOOL_MODULE_NAMES:
            module = import_module(f"{package_name}.{module_name}")
            spec = ToolSpec.from_mapping(module.TOOL_SPEC)
            assert spec.name not in specs, (
                f"Duplicate tool name {spec.name!r} from {module.__file__}"
            )
            specs[spec.name] = (spec, Path(module.__file__), package_name)
    return specs


@pytest.fixture(scope="module")
def specs() -> dict[str, tuple[ToolSpec, Path, str]]:
    return _collect_specs()


@pytest.fixture(scope="module")
def packs() -> list[dict[str, Any]]:
    import VibeCADWorkbenchTools as wbt

    return list(wbt.list_tool_packs())


@pytest.fixture(scope="module")
def core_tools() -> frozenset[str]:
    import VibeCADSession as session

    return frozenset(session.CORE_PROVIDER_TOOLS)


def _surfaced_names(
    core_tools: frozenset[str], packs: list[dict[str, Any]]
) -> set[str]:
    surfaced = set(core_tools)
    for pack in packs:
        surfaced.update(pack["tool_names"])
        surfaced.update(pack.get("required_adjacent_tool_names", ()))
    return surfaced


def test_no_orphan_tools(specs, packs, core_tools) -> None:
    """1. Every registered tool must belong to core or at least one pack."""
    orphans = sorted(set(specs) - _surfaced_names(core_tools, packs))
    assert not orphans, (
        "Tools registered but not surfaced by CORE_PROVIDER_TOOLS or any "
        f"workbench pack (add to a pack or remove the registration): {orphans}"
    )


def test_no_dangling_names(specs, packs, core_tools) -> None:
    """2. Every surfaced name must resolve to a registered spec."""
    dangling = sorted(_surfaced_names(core_tools, packs) - set(specs))
    assert not dangling, (
        f"Names surfaced by core/packs with no registered tool spec: {dangling}"
    )


def _module_sources_with_local_imports(module_path: Path) -> Iterator[str]:
    """Yield the module source plus sources of same-package imports (BFS)."""
    queue = [module_path]
    visited: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        source = path.read_text(encoding="utf-8")
        yield source
        for match in _INTRA_PACKAGE_IMPORT.finditer(source):
            if match.group("dotted"):
                names = [match.group("dotted")]
            else:
                names = [
                    part.strip()
                    for part in (match.group("plain") or "").split(",")
                    if part.strip()
                ]
            queue.extend(path.parent / f"{name}.py" for name in names)


def test_write_tools_run_in_transactions(specs) -> None:
    """3. Every write tool reaches a FreeCAD transaction (possibly via helpers)."""
    read_levels = {SafetyLevel.READ, SafetyLevel.VIEW}
    offenders = []
    for name, (spec, path, _) in sorted(specs.items()):
        if spec.safety in read_levels or name in TRANSACTION_EXEMPT:
            continue
        if not any(
            marker in source
            for source in _module_sources_with_local_imports(path)
            for marker in TRANSACTION_MARKERS
        ):
            offenders.append(name)
    assert not offenders, (
        "Write-safety tools with no transaction marker in their module or "
        f"same-package imports: {offenders}"
    )


def test_transaction_exemptions_are_current(specs) -> None:
    """3b. Transaction exemptions must reference registered tools."""
    unknown = sorted(TRANSACTION_EXEMPT - set(specs))
    assert not unknown, f"Transaction-exempt tools no longer registered: {unknown}"


def test_no_legacy_command_execution() -> None:
    """4. tool_impl never shells out to GUI command names or script strings."""
    offenders = []
    for path in sorted(TOOL_IMPL_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_COMMAND_STRINGS:
            if pattern in source:
                offenders.append(f"{path.name}: {pattern}")
    assert not offenders, (
        "Legacy FreeCAD command-execution strings found in tool_impl "
        f"(implement via typed APIs instead): {offenders}"
    )


def test_intentionally_empty_packs_stay_empty(packs) -> None:
    """TestWorkbench and NoneWorkbench must never surface tools."""
    for pack in packs:
        if pack["workbench"] in {"TestWorkbench", "NoneWorkbench"}:
            assert not pack["tool_names"], (
                f"{pack['workbench']} must stay empty; found {pack['tool_names']}"
            )


def test_adjacent_tools_are_owned_by_another_pack(packs) -> None:
    """Adjacent tools must be borrowed from a pack that lists them natively."""
    owned: set[str] = set()
    for pack in packs:
        owned.update(pack["tool_names"])
    for pack in packs:
        foreign = [
            name
            for name in pack.get("required_adjacent_tool_names", ())
            if name not in owned
        ]
        assert not foreign, (
            f"{pack['workbench']} borrows tools no pack owns natively: {foreign}"
        )
