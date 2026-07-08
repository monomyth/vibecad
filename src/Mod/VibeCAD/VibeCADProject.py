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
DESIGN_MEMORY_SCHEMA = "vibecad-design-memory-v1"
MAX_REQUIREMENT_MEMORY_ITEMS = 120
REQUIREMENT_MEMORY_HEAD_ITEMS = 16
MAX_DESIGN_MEMORY_ITEMS = 48

DESIGN_MEMORY_LIST_FIELDS = (
    "accepted_assumptions",
    "components",
    "sketches_features",
    "interfaces",
    "mechanisms",
    "manufacturing_assumptions",
    "non_negotiable_product_behavior",
    "critical_geometry",
    "verification_checks",
    "construction_order",
    "forbidden_shortcuts",
    "known_failures",
    "corrections",
    "open_questions",
    "notes",
)

DESIGN_MEMORY_TEXT_FIELDS = (
    "user_intent",
    "summary",
    "current_obligation",
    "source",
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slugify(value: str, fallback: str = "vibecad-project") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return slug[:80] or fallback


def _freecad_user_appdata() -> Path | None:
    """FreeCAD's per-user application data dir, or None when unavailable."""
    try:
        import FreeCAD as App

        raw = str(App.getUserAppDataDir() or "").strip()
        if raw:
            return Path(raw).expanduser()
    except Exception:
        pass
    return None


def _platform_data_dir() -> Path:
    """Platform-appropriate per-user data dir without FreeCAD."""
    if os.name == "nt":
        appdata = str(os.environ.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / "VibeCAD"
        try:
            return Path.home() / "AppData" / "Roaming" / "VibeCAD"
        except Exception:
            return Path.cwd() / "VibeCAD"
    xdg = str(os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "vibecad"
    try:
        return Path.home() / ".local" / "share" / "vibecad"
    except Exception:
        return Path.cwd() / ".vibecad"


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


def _vibecad_home() -> Path:
    """Backward-compatible alias for the central data dir."""
    return vibecad_data_dir()


def _legacy_vibecad_home() -> Path:
    """Pre-move home dir (``VIBECAD_HOME`` or ``~/.vibecad``) for migration reads."""
    configured = str(os.environ.get("VIBECAD_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        return Path.home() / ".vibecad"
    except Exception:
        return Path.cwd() / ".vibecad"


def _merged_answers_by_question(*answer_lists: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for answers in answer_lists:
        if not isinstance(answers, list):
            continue
        for item in answers:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if not question or not answer:
                continue
            merged[question] = dict(item)
    return list(merged.values())


def _clean_text(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _clean_text_list(value: Any, *, limit: int = MAX_DESIGN_MEMORY_ITEMS) -> list[str]:
    if value in (None, "", [], {}):
        return []
    raw_items = value if isinstance(value, list) else [value]
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        if isinstance(raw, dict):
            text = _clean_text(
                raw.get("text")
                or raw.get("answer")
                or raw.get("value")
                or raw.get("description")
                or raw.get("question")
            )
        else:
            text = _clean_text(raw)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _merge_text_lists(existing: Any, incoming: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in _clean_text_list(existing, limit=MAX_DESIGN_MEMORY_ITEMS):
        key = item.lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    for item in _clean_text_list(incoming, limit=MAX_DESIGN_MEMORY_ITEMS):
        key = item.lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged[-MAX_DESIGN_MEMORY_ITEMS:]


def _design_memory_alias_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    aliases = {
        "assumptions": "accepted_assumptions",
        "non_negotiables": "non_negotiable_product_behavior",
        "non_negotiable_geometry": "non_negotiable_product_behavior",
        "product_behaviors": "non_negotiable_product_behavior",
        "product_behavior": "non_negotiable_product_behavior",
    "bodies": "components",
    "bodies_components": "components",
    "features": "sketches_features",
    "feat": "sketches_features",
    "order": "construction_order",
    "failures": "known_failures",
        "known_failure": "known_failures",
        "correction": "corrections",
        "questions": "open_questions",
    }
    for source, target in aliases.items():
        if source in result and target not in result:
            result[target] = result[source]
    return result


def _normalize_design_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    payload = _design_memory_alias_payload(payload)
    result: dict[str, Any] = {
        "schema": DESIGN_MEMORY_SCHEMA,
        "status": _clean_text(payload.get("status") or "active", 40) or "active",
    }
    for key in DESIGN_MEMORY_TEXT_FIELDS:
        text = _clean_text(payload.get(key))
        if text:
            result[key] = text
    for key in DESIGN_MEMORY_LIST_FIELDS:
        items = _clean_text_list(payload.get(key), limit=MAX_DESIGN_MEMORY_ITEMS)
        if items:
            result[key] = items
    if payload.get("created_at"):
        result["created_at"] = _clean_text(payload.get("created_at"), 40)
    if payload.get("updated_at"):
        result["updated_at"] = _clean_text(payload.get("updated_at"), 40)
    return result


def _merge_design_memory(
    existing: dict[str, Any] | None,
    update: dict[str, Any],
    *,
    replace: bool = False,
) -> dict[str, Any]:
    base = {} if replace else _normalize_design_memory(existing)
    incoming = _normalize_design_memory(update)
    result = dict(base)
    result["schema"] = DESIGN_MEMORY_SCHEMA
    result["status"] = incoming.get("status") or result.get("status") or "active"
    for key in DESIGN_MEMORY_TEXT_FIELDS:
        if incoming.get(key):
            result[key] = incoming[key]
    for key in DESIGN_MEMORY_LIST_FIELDS:
        result[key] = _merge_text_lists(result.get(key), incoming.get(key))
        if not result[key]:
            result.pop(key, None)
    result.setdefault("created_at", base.get("created_at") or now_iso())
    result["updated_at"] = now_iso()
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _design_memory_from_preflight(preflight: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(preflight, dict) or not preflight:
        return {}
    draft = preflight.get("design_intent_draft")
    if not isinstance(draft, dict):
        draft = {}
    plan = preflight.get("final_build_plan")
    if not isinstance(plan, dict):
        plan = {}

    assumptions: list[str] = []
    refinement = preflight.get("requirement_refinement")
    if isinstance(refinement, list):
        for item in refinement:
            if not isinstance(item, dict) or item.get("assumption") is not True:
                continue
            question = _clean_text(item.get("question"), 160)
            answer = _clean_text(item.get("model_answer"), 260)
            if question and answer:
                assumptions.append(f"{question}: {answer}")
            elif answer:
                assumptions.append(answer)
    for item in _merged_answers_by_question(
        preflight.get("user_answers"),
        preflight.get("last_user_answers"),
    ):
        question = _clean_text(item.get("question"), 160)
        answer = _clean_text(item.get("answer"), 260)
        if question and answer:
            assumptions.append(f"{question}: {answer}")

    payload = {
        "schema": DESIGN_MEMORY_SCHEMA,
        "status": "active",
        "source": "design_preflight",
        "user_intent": preflight.get("user_intent")
        or preflight.get("initial_user_prompt")
        or preflight.get("source_prompt"),
        "accepted_assumptions": assumptions,
        "summary": plan.get("architecture") or draft.get("architecture"),
        "components": plan.get("bodies") or draft.get("bodies_components"),
        "sketches_features": plan.get("sketches_features"),
        "interfaces": plan.get("interfaces") or draft.get("interfaces"),
        "mechanisms": plan.get("mechanisms") or draft.get("mechanisms"),
        "manufacturing_assumptions": plan.get("manufacturing_assumptions")
        or draft.get("manufacturing_assumptions"),
        "non_negotiable_product_behavior": draft.get("non_negotiable_geometry"),
        "critical_geometry": plan.get("critical_geometry"),
        "verification_checks": plan.get("verification_checks"),
        "construction_order": plan.get("construction_order"),
        "forbidden_shortcuts": plan.get("forbidden_shortcuts"),
        "notes": draft.get("risks"),
        "current_obligation": (
            "Continue building or repairing CAD according to this accepted design "
            "memory. Do not rerun requirement refinement unless the user changes "
            "the product or contradicts this memory."
        ),
    }
    return _normalize_design_memory(payload)


def _default_index_path() -> Path:
    try:
        return vibecad_data_dir() / "index.sqlite"
    except Exception:
        return Path.cwd() / ".vibecad" / "index.sqlite"


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
    source = str(Path(str(file_path)).expanduser().resolve()) if file_path else session_id
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def project_root_for_document_file(file_path: str | Path) -> Path:
    """Per-document project folder for a saved CAD file.

    Matches ``VibeCADProjectStore.project_scope()`` for saved documents so all
    document artifacts (manifest, conversation, screenshots, references) share
    one folder under the central data dir.
    """
    cad_path = Path(str(file_path)).expanduser()
    source = str(cad_path.resolve())
    project_id = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
    folder_name = f"{slugify(cad_path.stem)}-{project_id[:8]}"
    return vibecad_data_dir() / "projects" / folder_name


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
            legacy_root = cad_path.parent / ".vibecad" / folder_name
            root = project_root_for_document_file(cad_path)
        else:
            folder_name = f"{slugify(str(label))}-{project_id[:8]}"
            legacy_root = _legacy_vibecad_home() / "projects" / folder_name
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
            "legacy_root": str(legacy_root),
            "legacy_manifest_path": str(legacy_root / "project.vibecad.json"),
        }

    def load_manifest(self) -> dict[str, Any]:
        scope = self.project_scope()
        for candidate in self._manifest_candidates(scope):
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("schema") == PROJECT_SCHEMA:
                    return self._merge_manifest_defaults(data, scope)
            except (OSError, ValueError):
                continue
        return self._default_manifest(scope)

    @staticmethod
    def _manifest_candidates(scope: dict[str, Any]) -> list[Path]:
        """New manifest location first; legacy sidecar only as a read fallback."""
        candidates = [Path(str(scope["manifest_path"]))]
        legacy = str(scope.get("legacy_manifest_path") or "")
        if legacy and legacy != str(scope["manifest_path"]):
            candidates.append(Path(legacy))
        return candidates

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
            "design_preflight": manifest.get("design_preflight") or {},
            "design_memory": manifest.get("design_memory")
            or _design_memory_from_preflight(manifest.get("design_preflight")),
            "requirement_memory": manifest.get("requirement_memory") or [],
            "root": scope["root"],
            "manifest_path": scope["manifest_path"],
            "index_path": scope["index_path"],
            "persistent": bool(scope.get("persistent")),
            "document_saved": bool(scope.get("document_saved")),
            "document": scope.get("document", {}),
            "documents": manifest.get("documents", {}),
        }

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

    def update_design_preflight(self, preflight: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(preflight, dict):
            raise ValueError("design preflight must be a dictionary.")
        manifest = self.load_manifest()
        previous = (
            manifest.get("design_preflight")
            if isinstance(manifest.get("design_preflight"), dict)
            else {}
        )
        item = dict(preflight)
        for key in (
            "user_questions",
            "user_answer_rounds",
            "last_user_answers",
            "initial_user_prompt",
            "source_prompt",
        ):
            if item.get(key) in (None, "", [], {}) and previous.get(key) not in (
                None,
                "",
                [],
                {},
            ):
                item[key] = previous[key]
        item["user_answers"] = _merged_answers_by_question(
            previous.get("user_answers"),
            previous.get("last_user_answers"),
            item.get("user_answers"),
            item.get("last_user_answers"),
        )
        item["updated_at"] = now_iso()
        manifest["design_preflight"] = item
        existing_memory = manifest.get("design_memory")
        if (
            item.get("status") == "build_ready"
            and (not isinstance(existing_memory, dict) or not existing_memory)
        ):
            seeded = _design_memory_from_preflight(item)
            if seeded:
                manifest["design_memory"] = seeded
        saved = self.save_manifest(manifest)
        return {
            "ok": True,
            "design_preflight": saved.get("design_preflight") or {},
            "design_memory": saved.get("design_memory") or {},
            "manifest_path": self.project_scope()["manifest_path"],
            "updated_at": saved.get("updated_at"),
        }

    def update_design_memory(self, memory_update: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(memory_update, dict):
            raise ValueError("design memory update must be a dictionary.")
        manifest = self.load_manifest()
        previous = (
            manifest.get("design_memory")
            if isinstance(manifest.get("design_memory"), dict)
            else {}
        )
        if not previous:
            previous = _design_memory_from_preflight(manifest.get("design_preflight"))
        replace = bool(memory_update.get("replace"))
        memory = _merge_design_memory(previous, memory_update, replace=replace)
        manifest["design_memory"] = memory
        saved = self.save_manifest(manifest)
        return {
            "ok": True,
            "design_memory": saved.get("design_memory") or {},
            "manifest_path": self.project_scope()["manifest_path"],
            "updated_at": saved.get("updated_at"),
        }

    def record_requirement_memory(
        self,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        role = str(role or "").strip()
        clean = str(content or "").strip()
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Unsupported requirement memory role: {role}")
        if not clean:
            raise ValueError("requirement memory content cannot be empty.")
        manifest = self.load_manifest()
        items = manifest.get("requirement_memory")
        if not isinstance(items, list):
            items = []
        if items:
            latest = items[-1]
            if (
                isinstance(latest, dict)
                and latest.get("role") == role
                and str(latest.get("content") or "").strip() == clean
            ):
                return {
                    "ok": True,
                    "requirement_memory": items,
                    "manifest_path": self.project_scope()["manifest_path"],
                    "updated_at": manifest.get("updated_at"),
                }
        entry: dict[str, Any] = {
            "role": role,
            "content": clean,
            "timestamp": now_iso(),
        }
        if isinstance(metadata, dict):
            source = str(metadata.get("source") or "").strip()
            if source:
                entry["source"] = source
        items.append(entry)
        if len(items) > MAX_REQUIREMENT_MEMORY_ITEMS:
            tail_count = MAX_REQUIREMENT_MEMORY_ITEMS - REQUIREMENT_MEMORY_HEAD_ITEMS
            items = items[:REQUIREMENT_MEMORY_HEAD_ITEMS] + items[-tail_count:]
        manifest["requirement_memory"] = items
        saved = self.save_manifest(manifest)
        return {
            "ok": True,
            "requirement_memory": saved.get("requirement_memory") or [],
            "manifest_path": self.project_scope()["manifest_path"],
            "updated_at": saved.get("updated_at"),
        }

    def record_design_preflight_answers(
        self,
        answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(answers, list):
            raise ValueError("design preflight answers must be a list.")
        cleaned: list[dict[str, Any]] = []
        for item in answers:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if not question or not answer:
                continue
            options: list[dict[str, str]] = []
            for option in item.get("options") or []:
                if isinstance(option, dict):
                    option_answer = str(
                        option.get("answer") or option.get("value") or ""
                    ).strip()
                    option_label = str(
                        option.get("label") or option.get("text") or ""
                    ).strip()
                    if not option_answer:
                        option_answer = option_label
                    if not option_label:
                        option_label = option_answer
                else:
                    option_label = str(option).strip()
                    option_answer = option_label
                if option_label and option_answer:
                    options.append(
                        {
                            "label": option_label,
                            "answer": option_answer,
                        }
                    )
            cleaned.append(
                {
                    "question": question,
                    "answer": answer,
                    "source": str(item.get("source") or "user").strip() or "user",
                    "options": options,
                    "default_answer": str(item.get("default_answer") or "").strip(),
                }
            )
        if not cleaned:
            raise ValueError("at least one design preflight answer is required.")
        manifest = self.load_manifest()
        preflight = dict(manifest.get("design_preflight") or {})
        rounds = preflight.get("user_answer_rounds")
        if not isinstance(rounds, list):
            rounds = []
        rounds.append({"answered_at": now_iso(), "answers": cleaned})
        preflight["user_answer_rounds"] = rounds
        preflight["last_user_answers"] = cleaned
        preflight["user_answers"] = _merged_answers_by_question(
            preflight.get("user_answers"),
            cleaned,
        )
        preflight["updated_at"] = now_iso()
        manifest["design_preflight"] = preflight
        saved = self.save_manifest(manifest)
        return {
            "ok": True,
            "answers": cleaned,
            "design_preflight": saved.get("design_preflight") or {},
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
            "design_preflight": {},
            "design_memory": {},
            "requirement_memory": [],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "documents": {"active": scope.get("document", {})},
        }

    def _merge_manifest_defaults(self, manifest: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
        default = self._default_manifest(scope)
        merged = dict(default)
        merged.update(
            {key: value for key, value in manifest.items() if key in default and value is not None}
        )
        merged["schema"] = PROJECT_SCHEMA
        merged["project_id"] = scope["project_id"]
        merged["documents"] = dict(merged.get("documents") or {})
        merged["documents"]["active"] = scope.get("document", {})
        return merged

    def _update_index(self, manifest: dict[str, Any], scope: dict[str, Any]) -> None:
        try:
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
        except (OSError, sqlite3.Error):
            pass
