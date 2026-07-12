# SPDX-License-Identifier: LGPL-2.1-or-later

"""Project persistence for VibeCAD.

Stores a small durable manifest per CAD project (title, summary, document
scope) plus a sqlite index of known projects. Deliberately contains no
workflow state: tool availability is driven by the active workbench pack,
not by project lifecycle gates.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any


PROJECT_SCHEMA = "vibecad-project-v2"
DESIGN_DOCUMENT_NAME = "design.md"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slugify(value: str, default: str = "vibecad-project") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return slug[:80] or default


def _freecad_user_appdata() -> Path | None:
    """FreeCAD's per-user application data dir, or None outside FreeCAD."""
    try:
        import FreeCAD as App
    except ImportError:
        return None
    raw = str(App.getUserAppDataDir() or "").strip()
    if not raw:
        raise RuntimeError("FreeCAD did not provide a user application data directory.")
    return Path(raw).expanduser()


def _platform_data_dir() -> Path:
    """Platform-appropriate per-user data dir without FreeCAD."""
    if os.name == "nt":
        appdata = str(os.environ.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / "VibeCAD"
        return Path.home() / "AppData" / "Roaming" / "VibeCAD"
    xdg = str(os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "vibecad"
    return Path.home() / ".local" / "share" / "vibecad"


def vibecad_data_dir() -> Path:
    """Central VibeCAD data directory.

    Resolution order:
    1. ``VIBECAD_HOME`` environment override (tests, power users).
    2. FreeCAD's user appdata dir + ``VibeCAD`` when FreeCAD is importable
       (``~/.local/share/FreeCAD/VibeCAD`` on Linux,
       ``%APPDATA%/FreeCAD/VibeCAD`` on Windows).
    3. Platform default without FreeCAD (``$XDG_DATA_HOME/vibecad`` or
       ``~/.local/share/vibecad`` on Linux, ``%APPDATA%/VibeCAD`` on Windows).
    """
    configured = str(os.environ.get("VIBECAD_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser()
    appdata = _freecad_user_appdata()
    if appdata is not None:
        return appdata / "VibeCAD"
    return _platform_data_dir()


def _default_index_path() -> Path:
    return vibecad_data_dir() / "index.sqlite"


def _active_document_info() -> dict[str, Any]:
    try:
        import FreeCAD as App
    except Exception:
        return {"document": None, "label": None, "file_path": None, "saved": False}

    doc = getattr(App, "ActiveDocument", None)
    if doc is None:
        return {"document": None, "label": None, "file_path": None, "saved": False}
    file_path = str(getattr(doc, "FileName", "") or "")
    return {
        "document": str(getattr(doc, "Name", "") or ""),
        "label": str(getattr(doc, "Label", "") or getattr(doc, "Name", "") or ""),
        "file_path": file_path or None,
        "saved": bool(file_path),
    }


def _project_id_for_scope(scope: dict[str, Any], session_id: str) -> str:
    file_path = scope.get("file_path")
    source = (
        str(Path(str(file_path)).expanduser().resolve()) if file_path else session_id
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def project_root_for_document_file(file_path: str | Path) -> Path:
    """Per-document project folder for a saved CAD file.

    Matches ``VibeCADProjectStore.project_scope()`` for saved documents so all
    document artifacts (manifest, conversation, design document, screenshots,
    references) share one folder under the central data dir.
    """
    cad_path = Path(str(file_path)).expanduser()
    source = str(cad_path.resolve())
    project_id = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
    folder_name = f"{slugify(cad_path.stem)}-{project_id[:8]}"
    return vibecad_data_dir() / "projects" / folder_name


def design_document_path_for_document_file(file_path: str | Path) -> Path:
    return project_root_for_document_file(file_path) / DESIGN_DOCUMENT_NAME


def _design_document_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _read_design_document(path: Path) -> dict[str, Any]:
    exists = path.exists()
    content = path.read_text(encoding="utf-8") if exists else ""
    modified_at = None
    if exists:
        modified_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(path.stat().st_mtime),
        )
    return {
        "path": str(path),
        "exists": exists,
        "content": content,
        "revision": _design_document_revision(content),
        "updated_at": modified_at,
    }


def _write_design_document(
    path: Path,
    markdown: str,
    expected_revision: str,
) -> dict[str, Any]:
    current = _read_design_document(path)
    expected = str(expected_revision or "").strip()
    if expected != current["revision"]:
        return {
            "ok": False,
            "error": (
                "design.md changed after this provider turn began; read the current "
                "design_document context and submit a complete updated document."
            ),
            "path": str(path),
            "expected_revision": expected,
            "current_revision": current["revision"],
            "retry_same_call": False,
        }
    content = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in content:
        return {
            "ok": False,
            "error": "design.md cannot contain null bytes.",
            "retry_same_call": False,
        }
    content = content.rstrip() + "\n"
    if not content.strip():
        return {
            "ok": False,
            "error": "design.md cannot be empty.",
            "retry_same_call": False,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    saved = _read_design_document(path)
    return {
        "ok": True,
        "path": str(path),
        "revision": saved["revision"],
        "updated_at": saved["updated_at"],
        "character_count": len(content),
    }


class VibeCADProjectStore:
    """Small durable project store keyed to the active CAD document."""

    def __init__(self, session_id: str, index_path: Path | None = None) -> None:
        self.session_id = str(session_id)
        self.index_path = index_path or _default_index_path()

    def project_scope(self) -> dict[str, Any]:
        doc = _active_document_info()
        project_id = _project_id_for_scope(doc, self.session_id)
        label = doc.get("label") or doc.get("document") or "Unsaved VibeCAD Project"
        if doc.get("file_path"):
            cad_path = Path(str(doc["file_path"])).expanduser()
            folder_name = f"{slugify(cad_path.stem)}-{project_id[:8]}"
            root = project_root_for_document_file(cad_path)
        else:
            folder_name = f"{slugify(str(label))}-{project_id[:8]}"
            root = vibecad_data_dir() / "projects" / folder_name
        persistent = True
        return {
            "project_id": project_id,
            "title": str(label),
            "root": str(root),
            "manifest_path": str(root / "project.vibecad.json"),
            "persistent": persistent,
            "document_saved": bool(doc.get("saved")),
            "document": doc,
            "index_path": str(self.index_path),
        }

    def load_manifest(self) -> dict[str, Any]:
        scope = self.project_scope()
        path = Path(str(scope["manifest_path"]))
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"VibeCAD project manifest could not be read from {path}: {exc}"
                ) from exc
            if not isinstance(data, dict) or data.get("schema") != PROJECT_SCHEMA:
                raise RuntimeError(
                    f"VibeCAD project manifest at {path} has an invalid schema."
                )
            return self._merge_manifest_defaults(data, scope)
        return self._default_manifest(scope)

    def save_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        scope = self.project_scope()
        merged = self._merge_manifest_defaults(manifest, scope)
        merged["updated_at"] = now_iso()
        path = Path(str(scope["manifest_path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        self._update_index(merged, scope)
        return merged

    def context(self) -> dict[str, Any]:
        scope = self.project_scope()
        manifest = self.save_manifest(self.load_manifest())
        return {
            "schema": "vibecad-project-context-v2",
            "project_id": manifest["project_id"],
            "title": manifest.get("title") or scope.get("title"),
            "summary": manifest.get("summary") or "",
            "root": scope["root"],
            "manifest_path": scope["manifest_path"],
            "index_path": scope["index_path"],
            "persistent": bool(scope.get("persistent")),
            "document_saved": bool(scope.get("document_saved")),
            "document": scope.get("document", {}),
            "documents": manifest.get("documents", {}),
        }

    def design_document(self) -> dict[str, Any]:
        root = Path(str(self.project_scope()["root"]))
        return _read_design_document(root / DESIGN_DOCUMENT_NAME)

    def update_design_document(
        self,
        *,
        markdown: str,
        expected_revision: str,
    ) -> dict[str, Any]:
        root = Path(str(self.project_scope()["root"]))
        return _write_design_document(
            root / DESIGN_DOCUMENT_NAME,
            markdown,
            expected_revision,
        )

    @staticmethod
    def write_design_document_for_file(
        file_path: str | Path,
        markdown: str,
    ) -> dict[str, Any]:
        path = design_document_path_for_document_file(file_path)
        current = _read_design_document(path)
        return _write_design_document(path, markdown, current["revision"])

    def update_summary(self, *, title: str = "", summary: str = "") -> dict[str, Any]:
        """Update the human-facing title/summary for the project."""
        manifest = self.load_manifest()
        if str(title or "").strip():
            manifest["title"] = str(title).strip()
        if str(summary or "").strip():
            manifest["summary"] = str(summary).strip()
        saved = self.save_manifest(manifest)
        return {
            "ok": True,
            "title": saved.get("title"),
            "summary": saved.get("summary"),
            "manifest_path": self.project_scope()["manifest_path"],
            "updated_at": saved.get("updated_at"),
        }

    def _default_manifest(self, scope: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": PROJECT_SCHEMA,
            "version": 1,
            "project_id": scope["project_id"],
            "title": scope["title"],
            "summary": "",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "documents": {"active": scope.get("document", {})},
        }

    def _merge_manifest_defaults(
        self, manifest: dict[str, Any], scope: dict[str, Any]
    ) -> dict[str, Any]:
        default = self._default_manifest(scope)
        merged = dict(default)
        merged.update(
            {
                key: value
                for key, value in manifest.items()
                if key in default and value is not None
            }
        )
        merged["schema"] = PROJECT_SCHEMA
        merged["project_id"] = scope["project_id"]
        merged["documents"] = dict(merged.get("documents") or {})
        merged["documents"]["active"] = scope.get("document", {})
        return merged

    def _update_index(self, manifest: dict[str, Any], scope: dict[str, Any]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.index_path)) as conn:
            conn.execute(
                """
                    CREATE TABLE IF NOT EXISTS projects_v2 (
                        project_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        root TEXT NOT NULL,
                        manifest_path TEXT NOT NULL,
                        cad_file TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
            )
            conn.execute(
                """
                    INSERT INTO projects_v2 (
                        project_id, title, summary, root, manifest_path, cad_file, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        title=excluded.title,
                        summary=excluded.summary,
                        root=excluded.root,
                        manifest_path=excluded.manifest_path,
                        cad_file=excluded.cad_file,
                        updated_at=excluded.updated_at
                    """,
                (
                    manifest["project_id"],
                    str(manifest.get("title") or scope.get("title") or ""),
                    str(manifest.get("summary") or ""),
                    str(scope["root"]),
                    str(scope["manifest_path"]),
                    (scope.get("document") or {}).get("file_path"),
                    str(manifest.get("updated_at") or now_iso()),
                ),
            )
