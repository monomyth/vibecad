#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
from typing import Iterable


SYSTEM_PREFIXES = (
    Path("/System/Library"),
    Path("/usr/lib"),
    Path("/Library/Apple/System/Library"),
)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair the known absolute LC_RPATH and LC_REEXPORT_DYLIB entries "
            "in the top-level macOS bundle library directory."
        )
    )
    parser.add_argument("scan_path", type=Path)
    parser.add_argument("--bundle-prefix", required=True, type=Path)
    parser.add_argument("--source-prefix", required=True, type=Path)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Report required changes without modifying files.",
    )
    return parser.parse_args()


def _normalized(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_system_path(path: Path) -> bool:
    return any(_is_relative_to(path, prefix) for prefix in SYSTEM_PREFIXES)


def _load_commands(file_path: Path) -> tuple[list[str], list[str]] | None:
    result = subprocess.run(
        ["otool", "-l", str(file_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        diagnostic = f"{result.stdout}\n{result.stderr}"
        if "is not an object file" in diagnostic or "The file was not recognized" in diagnostic:
            return None
        raise RuntimeError(
            f"otool failed for {file_path} with status {result.returncode}:\n"
            f"{diagnostic.strip()}"
        )

    rpaths: list[str] = []
    reexports: list[str] = []
    command = ""
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("cmd "):
            command = line.removeprefix("cmd ")
            continue
        if command == "LC_RPATH" and line.startswith("path "):
            rpaths.append(line.removeprefix("path ").split(" (offset", 1)[0])
            command = ""
        elif command == "LC_REEXPORT_DYLIB" and line.startswith("name "):
            reexports.append(line.removeprefix("name ").split(" (offset", 1)[0])
            command = ""
    return rpaths, reexports


def _run_install_name_tool(arguments: Iterable[str], file_path: Path) -> None:
    subprocess.run(
        ["install_name_tool", *arguments, str(file_path)],
        check=True,
        text=True,
    )


def _sign(file_path: Path) -> None:
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(file_path)],
        check=True,
        text=True,
    )


def _source_destination(
    path: Path,
    *,
    source_prefix: Path,
    bundle_prefix: Path,
) -> Path | None:
    if not _is_relative_to(path, source_prefix):
        return None
    return bundle_prefix / path.relative_to(source_prefix)


def _repair_file(
    file_path: Path,
    *,
    bundle_prefix: Path,
    source_prefix: Path,
    scan_only: bool,
) -> int:
    commands = _load_commands(file_path)
    if commands is None:
        return 0
    rpaths, reexports = commands
    changes: list[tuple[str, ...]] = []

    for rpath in rpaths:
        if not os.path.isabs(rpath):
            continue
        resolved = _normalized(Path(rpath))
        relocated = _source_destination(
            resolved,
            source_prefix=source_prefix,
            bundle_prefix=bundle_prefix,
        )
        if relocated is not None:
            if relocated != file_path.parent:
                raise RuntimeError(
                    f"Unsupported source-prefix RPATH in {file_path}: {rpath} "
                    f"would relocate to {relocated}."
                )
            changes.append(("-delete_rpath", rpath))
            continue
        if resolved == file_path.parent:
            changes.append(("-delete_rpath", rpath))
            continue
        if _is_relative_to(resolved, bundle_prefix):
            raise RuntimeError(
                f"Unsupported absolute bundle RPATH in {file_path}: {rpath}. "
                "The package must use an @loader_path or @rpath-relative entry."
            )
        if not _is_system_path(resolved):
            raise RuntimeError(
                f"External absolute RPATH in {file_path}: {rpath}. "
                "The macOS app would not be self-contained."
            )

    for reexport in reexports:
        if not os.path.isabs(reexport):
            continue
        resolved = _normalized(Path(reexport))
        relocated = _source_destination(
            resolved,
            source_prefix=source_prefix,
            bundle_prefix=bundle_prefix,
        )
        bundled_library = relocated if relocated is not None else resolved
        if _is_relative_to(bundled_library, bundle_prefix):
            if not bundled_library.exists():
                raise RuntimeError(
                    f"Re-exported library is missing from the app bundle: "
                    f"{reexport} should resolve to {bundled_library}."
                )
            changes.append(
                ("-change", reexport, f"@rpath/{Path(reexport).name}")
            )
            continue
        if not _is_system_path(resolved):
            raise RuntimeError(
                f"External absolute re-export in {file_path}: {reexport}. "
                "The macOS app would not be self-contained."
            )

    if not changes:
        return 0

    for change in changes:
        print(f"{file_path}: install_name_tool {' '.join(change)}", flush=True)
        if not scan_only:
            _run_install_name_tool(change, file_path)
    if not scan_only:
        _sign(file_path)
    return len(changes)


def main() -> int:
    arguments = _parse_arguments()
    scan_path = _normalized(arguments.scan_path)
    bundle_prefix = _normalized(arguments.bundle_prefix)
    source_prefix = _normalized(arguments.source_prefix)

    if not scan_path.is_dir():
        raise SystemExit(f"macOS library directory does not exist: {scan_path}")
    if not bundle_prefix.is_dir():
        raise SystemExit(f"macOS bundle prefix does not exist: {bundle_prefix}")
    if not source_prefix.is_dir():
        raise SystemExit(f"Source conda prefix does not exist: {source_prefix}")

    changed = 0
    scanned = 0
    for file_path in sorted(scan_path.iterdir()):
        if not file_path.is_file() or file_path.is_symlink():
            continue
        scanned += 1
        changed += _repair_file(
            file_path,
            bundle_prefix=bundle_prefix,
            source_prefix=source_prefix,
            scan_only=arguments.scan_only,
        )

    action = "required" if arguments.scan_only else "applied"
    print(
        f"macOS top-level library repair complete: scanned {scanned} files; "
        f"{changed} load-command changes {action}.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
