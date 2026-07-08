# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import importlib
import json
from pathlib import Path
import tempfile
import time
import unittest

import VibeCADProject
from VibeCADCore import (
    VibeCADService,
)
from VibeCADProject import (
    PROJECT_SCHEMA,
    VibeCADProjectStore,
    project_root_for_document_file,
    vibecad_data_dir,
)

from vibecad_tests.support import (
    _fake_active_document,
    _temporary_vibecad_home,
)
from vibecad_tests.test_provider_misc import TestVibeCADReferenceImages


class TestVibeCADProject(unittest.TestCase):
    def test_project_store_writes_manifest_and_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = VibeCADProjectStore(
                f"unit-{time.time_ns()}",
                index_path=root / "index.sqlite",
            )
            original = VibeCADProject._active_document_info
            VibeCADProject._active_document_info = lambda: {
                "document": "UnitTestProject",
                "label": "Unit Test Project",
                "file_path": str(root / "Unit_Test_Project.FCStd"),
                "saved": True,
            }
            try:
                context = store.context()
                self.assertEqual(context["schema"], "vibecad-project-context-v2")
                self.assertEqual(context["title"], "Unit Test Project")
                self.assertTrue(context["persistent"])
                self.assertTrue(Path(context["manifest_path"]).exists())
                manifest = json.loads(Path(context["manifest_path"]).read_text(encoding="utf-8"))
                self.assertEqual(manifest["schema"], "vibecad-project-v2")
                self.assertNotIn("phase", manifest)
                self.assertNotIn("intent", manifest)

                updated = store.update_summary(
                    title="Bracket Project",
                    summary="A mounting bracket with four bolt holes.",
                )
                self.assertTrue(updated["ok"])
                refreshed = store.context()
                self.assertEqual(refreshed["title"], "Bracket Project")
                self.assertEqual(
                    refreshed["summary"],
                    "A mounting bracket with four bolt holes.",
                )
            finally:
                VibeCADProject._active_document_info = original

    def test_unsaved_project_store_uses_durable_user_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = VibeCADProjectStore(
                f"unit-{time.time_ns()}",
                index_path=root / "index.sqlite",
            )
            original = VibeCADProject._active_document_info
            VibeCADProject._active_document_info = lambda: {
                "document": "Unnamed",
                "label": "Unnamed",
                "file_path": "",
                "saved": False,
            }
            try:
                scope = store.project_scope()
                self.assertTrue(scope["persistent"])
                self.assertFalse(scope["document_saved"])
                self.assertIn("projects", scope["root"])
            finally:
                VibeCADProject._active_document_info = original

    def test_design_memory_persists_in_project_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = VibeCADProjectStore(
                f"unit-{time.time_ns()}",
                index_path=root / "index.sqlite",
            )
            original = VibeCADProject._active_document_info
            VibeCADProject._active_document_info = lambda: {
                "document": "MemoryDoc",
                "label": "Memory Doc",
                "file_path": str(root / "Memory_Doc.FCStd"),
                "saved": True,
            }
            try:
                result = store.update_design_memory(
                    {
                        "user_intent": "Build a proper folding knife.",
                        "known_failures": ["Blade belly could not close."],
                        "verification_checks": ["Closed blade fits handle."],
                    }
                )
                self.assertTrue(result["ok"])
                context = store.context()
                memory = context["design_memory"]
                self.assertEqual(memory["user_intent"], "Build a proper folding knife.")
                self.assertIn(
                    "Blade belly could not close.",
                    memory["known_failures"],
                )
                self.assertIn(
                    "Closed blade fits handle.",
                    memory["verification_checks"],
                )
            finally:
                VibeCADProject._active_document_info = original



class TestVibeCADStorageLayout(unittest.TestCase):
    """Central data-dir resolution, per-project layout, and legacy migration."""

    _MINIMAL_PNG = TestVibeCADReferenceImages._MINIMAL_PNG

    # --- data dir resolution ---------------------------------------------

    def test_vibecad_data_dir_prefers_vibecad_home_override(self):
        with _temporary_vibecad_home() as home:
            self.assertEqual(vibecad_data_dir(), home)

    def test_vibecad_data_dir_uses_freecad_appdata_when_available(self):
        old_home = os.environ.pop("VIBECAD_HOME", None)
        original = VibeCADProject._freecad_user_appdata
        try:
            with tempfile.TemporaryDirectory() as tmp:
                appdata = Path(tmp) / "FreeCADData"
                VibeCADProject._freecad_user_appdata = lambda: appdata
                self.assertEqual(vibecad_data_dir(), appdata / "VibeCAD")
        finally:
            VibeCADProject._freecad_user_appdata = original
            if old_home is not None:
                os.environ["VIBECAD_HOME"] = old_home

    def test_vibecad_data_dir_platform_fallback_without_freecad(self):
        old_home = os.environ.pop("VIBECAD_HOME", None)
        original = VibeCADProject._freecad_user_appdata
        try:
            with tempfile.TemporaryDirectory() as tmp:
                VibeCADProject._freecad_user_appdata = lambda: None
                if os.name == "nt":
                    old_env = os.environ.get("APPDATA")
                    os.environ["APPDATA"] = tmp
                    try:
                        self.assertEqual(
                            vibecad_data_dir(), Path(tmp) / "VibeCAD"
                        )
                    finally:
                        if old_env is None:
                            os.environ.pop("APPDATA", None)
                        else:
                            os.environ["APPDATA"] = old_env
                else:
                    old_env = os.environ.get("XDG_DATA_HOME")
                    os.environ["XDG_DATA_HOME"] = tmp
                    try:
                        self.assertEqual(
                            vibecad_data_dir(), Path(tmp) / "vibecad"
                        )
                    finally:
                        if old_env is None:
                            os.environ.pop("XDG_DATA_HOME", None)
                        else:
                            os.environ["XDG_DATA_HOME"] = old_env
        finally:
            VibeCADProject._freecad_user_appdata = original
            if old_home is not None:
                os.environ["VIBECAD_HOME"] = old_home

    # --- per-project layout ------------------------------------------------

    def test_project_root_for_saved_document_is_under_central_data_dir(self):
        with _temporary_vibecad_home() as home, tempfile.TemporaryDirectory() as tmp:
            cad_file = Path(tmp) / "Bracket.FCStd"
            root = project_root_for_document_file(cad_file)
            self.assertTrue(root.is_relative_to(home / "projects"))
            self.assertFalse(root.is_relative_to(Path(tmp)))
            self.assertTrue(root.name.startswith("Bracket-"))
            # Stable: same file resolves to the same folder.
            self.assertEqual(root, project_root_for_document_file(cad_file))

    def test_project_scope_never_creates_sidecar_next_to_cad_file(self):
        with _temporary_vibecad_home() as home, tempfile.TemporaryDirectory() as tmp:
            cad_dir = Path(tmp)
            cad_file = cad_dir / "Widget.FCStd"
            store = VibeCADProjectStore(f"unit-{time.time_ns()}")
            with _fake_active_document(
                {
                    "document": "Widget",
                    "label": "Widget",
                    "file_path": str(cad_file),
                    "saved": True,
                }
            ):
                context = store.context()
            manifest_path = Path(context["manifest_path"])
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(manifest_path.is_relative_to(home))
            self.assertFalse((cad_dir / ".vibecad").exists())
            self.assertEqual(
                [p.name for p in cad_dir.iterdir()],
                [],
                "nothing may be written next to the CAD file",
            )
            index_path = Path(context["index_path"])
            self.assertTrue(index_path.is_relative_to(home))

    def test_manifest_and_conversation_share_one_project_folder(self):
        with _temporary_vibecad_home(), tempfile.TemporaryDirectory() as tmp:
            cad_file = Path(tmp) / "Shared.FCStd"
            conversation_path = VibeCADService.conversation_path_for_document_file(
                cad_file
            )
            self.assertEqual(
                conversation_path.parent, project_root_for_document_file(cad_file)
            )
            self.assertEqual(conversation_path.name, "conversation.json")

    # --- legacy migration ----------------------------------------------------

    def test_legacy_sidecar_manifest_is_read_without_new_sidecar_writes(self):
        with _temporary_vibecad_home(), tempfile.TemporaryDirectory() as tmp:
            cad_dir = Path(tmp)
            cad_file = cad_dir / "Legacy.FCStd"
            store = VibeCADProjectStore(f"unit-{time.time_ns()}")
            doc_info = {
                "document": "Legacy",
                "label": "Legacy",
                "file_path": str(cad_file),
                "saved": True,
            }
            with _fake_active_document(doc_info):
                scope = store.project_scope()
                legacy_manifest = Path(scope["legacy_manifest_path"])
                legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
                legacy_manifest.write_text(
                    json.dumps(
                        {
                            "schema": PROJECT_SCHEMA,
                            "version": 1,
                            "project_id": scope["project_id"],
                            "title": "Legacy Sidecar Title",
                            "summary": "from the old sidecar",
                        }
                    ),
                    encoding="utf-8",
                )
                legacy_bytes = legacy_manifest.read_bytes()

                manifest = store.load_manifest()
                self.assertEqual(manifest["title"], "Legacy Sidecar Title")
                self.assertEqual(manifest["summary"], "from the old sidecar")

                saved = store.save_manifest(manifest)
                self.assertEqual(saved["title"], "Legacy Sidecar Title")
                new_manifest = Path(scope["manifest_path"])
                self.assertTrue(new_manifest.is_file())
                self.assertEqual(
                    legacy_manifest.read_bytes(),
                    legacy_bytes,
                    "legacy sidecar must remain untouched",
                )

    def test_new_manifest_location_wins_over_legacy_sidecar(self):
        with _temporary_vibecad_home(), tempfile.TemporaryDirectory() as tmp:
            cad_file = Path(tmp) / "Precedence.FCStd"
            store = VibeCADProjectStore(f"unit-{time.time_ns()}")
            doc_info = {
                "document": "Precedence",
                "label": "Precedence",
                "file_path": str(cad_file),
                "saved": True,
            }
            with _fake_active_document(doc_info):
                scope = store.project_scope()
                for path, title in (
                    (Path(scope["legacy_manifest_path"]), "Old Title"),
                    (Path(scope["manifest_path"]), "New Title"),
                ):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps(
                            {
                                "schema": PROJECT_SCHEMA,
                                "version": 1,
                                "project_id": scope["project_id"],
                                "title": title,
                                "summary": "",
                            }
                        ),
                        encoding="utf-8",
                    )
                self.assertEqual(store.load_manifest()["title"], "New Title")

    def test_legacy_chat_sidecar_is_migrated_on_first_read(self):
        with _temporary_vibecad_home(), tempfile.TemporaryDirectory() as tmp:
            cad_file = Path(tmp) / "ChatLegacy.FCStd"
            cad_file.write_bytes(b"fake cad")
            legacy_chat = cad_file.with_name(f"{cad_file.name}.vibecad-chat.json")
            legacy_chat.write_text(
                json.dumps(
                    {
                        "conversation": [
                            {"role": "user", "content": "legacy memory"},
                            {"role": "assistant", "content": "legacy answer"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            legacy_bytes = legacy_chat.read_bytes()
            service = VibeCADService()
            with _fake_active_document(
                {
                    "document": "ChatLegacy",
                    "label": "ChatLegacy",
                    "file_path": str(cad_file),
                    "saved": True,
                }
            ):
                history = service.conversation_history()
                self.assertEqual(history["scope"]["kind"], "saved_document")
                self.assertEqual(history["turn_count"], 2)
                self.assertEqual(
                    history["conversation"][0]["content"], "legacy memory"
                )
                new_path = Path(history["path"])
                self.assertEqual(
                    new_path,
                    VibeCADService.conversation_path_for_document_file(cad_file),
                )
                self.assertTrue(new_path.is_file(), "migration must persist a copy")
                self.assertEqual(
                    legacy_chat.read_bytes(),
                    legacy_bytes,
                    "legacy chat sidecar must remain untouched",
                )
            # Only the CAD file and the legacy sidecar remain next to the CAD file.
            self.assertEqual(
                sorted(p.name for p in Path(tmp).iterdir()),
                sorted([cad_file.name, legacy_chat.name]),
            )

    def test_new_conversation_location_wins_over_legacy_chat_sidecar(self):
        with _temporary_vibecad_home(), tempfile.TemporaryDirectory() as tmp:
            cad_file = Path(tmp) / "ChatPrecedence.FCStd"
            legacy_chat = cad_file.with_name(f"{cad_file.name}.vibecad-chat.json")
            legacy_chat.write_text(
                json.dumps(
                    {"conversation": [{"role": "user", "content": "stale legacy"}]}
                ),
                encoding="utf-8",
            )
            new_path = VibeCADService.conversation_path_for_document_file(cad_file)
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text(
                json.dumps(
                    {"conversation": [{"role": "user", "content": "fresh new"}]}
                ),
                encoding="utf-8",
            )
            service = VibeCADService()
            with _fake_active_document(
                {
                    "document": "ChatPrecedence",
                    "label": "ChatPrecedence",
                    "file_path": str(cad_file),
                    "saved": True,
                }
            ):
                history = service.conversation_history()
                self.assertEqual(history["turn_count"], 1)
                self.assertEqual(history["conversation"][0]["content"], "fresh new")

    def test_recorded_turns_never_create_sidecars_next_to_cad_file(self):
        with _temporary_vibecad_home() as home, tempfile.TemporaryDirectory() as tmp:
            cad_file = Path(tmp) / "NoSidecar.FCStd"
            cad_file.write_bytes(b"fake cad")
            service = VibeCADService()
            with _fake_active_document(
                {
                    "document": "NoSidecar",
                    "label": "NoSidecar",
                    "file_path": str(cad_file),
                    "saved": True,
                }
            ):
                service.record_conversation_turn("user", "store me centrally")
                history = service.conversation_history()
            self.assertTrue(Path(history["path"]).is_relative_to(home))
            self.assertEqual(
                [p.name for p in Path(tmp).iterdir()],
                [cad_file.name],
                "recording a turn must not write next to the CAD file",
            )

    # --- artifact directories -------------------------------------------------

    def test_screenshot_dir_lives_in_project_folder_with_data_dir_fallback(self):
        module = importlib.import_module(
            "tool_impl.service.core_capture_view_screenshot"
        )
        with _temporary_vibecad_home() as home, tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project-root"

            class FakeService:
                def project_context(self):
                    return {"root": str(root)}

            class NoProjectService:
                def project_context(self):
                    return {}

            self.assertEqual(
                module._screenshot_artifact_dir(FakeService()),
                root / "screenshots",
            )
            self.assertEqual(
                module._screenshot_artifact_dir(NoProjectService()),
                home / "screenshots",
            )

    def test_reference_dir_lives_in_project_folder_with_data_dir_fallback(self):
        with _temporary_vibecad_home() as home, tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project-root"
            service = VibeCADService()
            service.project_context = lambda: {"root": str(root)}
            self.assertEqual(
                service._reference_artifact_dir(), root / "references"
            )
            service.project_context = lambda: {}
            self.assertEqual(
                service._reference_artifact_dir(), home / "references"
            )
