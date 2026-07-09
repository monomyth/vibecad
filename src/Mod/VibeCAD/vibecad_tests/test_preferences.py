# SPDX-License-Identifier: LGPL-2.1-or-later

import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

from VibeCADAuth import (
    AuthStatus,
    KEYRING_SERVICE,
    KEYRING_USERNAME,
)
import VibeCADPreferences
from VibeCADPreferences import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_MODEL,
    REASONING_EFFORTS,
    VibeCADPreferencesPage,
    VibeCADSettings,
    VibeCADToolsPreferencesPage,
    fetch_models_for_provider,
    load_settings,
    native_tool_workbench_choices,
    normalize_provider,
    normalize_reasoning_effort,
    preferences,
    reset_settings,
    save_settings,
)
from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS

from vibecad_tests.support import (
    FakeKeyringModule,
    _ensure_offscreen_qapplication,
)

class TestVibeCADPreferences(unittest.TestCase):
    def setUp(self):
        self._pref = preferences()
        self._old_use_online = self._pref.GetBool("UseOnlineProvider", True)
        self._old_model = self._pref.GetString("Model", DEFAULT_MODEL)
        self._old_dotenv = self._pref.GetString("DotenvPath", "")
        self._old_reasoning_effort = self._pref.GetString(
            "ReasoningEffort",
            DEFAULT_REASONING_EFFORT,
        )
        self._old_provider = self._pref.GetString("Provider", "openai")
        self._old_anthropic_model = self._pref.GetString(
            "AnthropicModel", DEFAULT_ANTHROPIC_MODEL
        )
        self._old_enable_build_script = self._pref.GetBool("EnableBuildScript", False)
        self._old_enable_native_tools = self._pref.GetBool(
            "EnableNativeFreeCADTools", False
        )
        self._old_native_workbenches = self._pref.GetString(
            "NativeToolWorkbenches", ""
        )
        self._old_openai_base_url = self._pref.GetString("OpenAIBaseUrl", "")
        self._old_anthropic_base_url = self._pref.GetString("AnthropicBaseUrl", "")

    def tearDown(self):
        save_settings(
            VibeCADSettings(
                use_online_provider=self._old_use_online,
                model=self._old_model,
                dotenv_path=self._old_dotenv,
                reasoning_effort=self._old_reasoning_effort,
                provider=self._old_provider,
                anthropic_model=self._old_anthropic_model,
                enable_build_script=self._old_enable_build_script,
                enable_native_freecad_tools=self._old_enable_native_tools,
                native_tool_workbenches=tuple(
                    item for item in self._old_native_workbenches.split(",") if item
                ),
                openai_base_url=self._old_openai_base_url,
                anthropic_base_url=self._old_anthropic_base_url,
            )
        )

    def test_preferences_persist_non_secret_settings(self):
        save_settings(
            VibeCADSettings(
                use_online_provider=False,
                model=DEFAULT_MODEL,
                dotenv_path="/tmp/vibecad-test.env",
                reasoning_effort="xhigh",
                enable_native_freecad_tools=True,
                native_tool_workbenches=("PartWorkbench", "SketcherWorkbench"),
            )
        )
        settings = load_settings()
        self.assertFalse(settings.use_online_provider)
        self.assertEqual(settings.model, DEFAULT_MODEL)
        self.assertEqual(settings.dotenv_path, "/tmp/vibecad-test.env")
        self.assertTrue(settings.enable_native_freecad_tools)
        self.assertEqual(
            settings.native_tool_workbenches,
            ("PartWorkbench", "SketcherWorkbench"),
        )
        self.assertEqual(settings.reasoning_effort, "xhigh")
        self.assertEqual(self._pref.GetString("OpenAIApiKey", ""), "")

    def test_preferences_persist_enable_build_script(self):
        settings = load_settings()
        self.assertFalse(settings.enable_build_script)
        save_settings(
            VibeCADSettings(
                enable_build_script=True,
            )
        )
        self.assertTrue(load_settings().enable_build_script)
        reset_settings()
        self.assertFalse(load_settings().enable_build_script)

    def test_preferences_reset_to_defaults(self):
        save_settings(VibeCADSettings(False, DEFAULT_MODEL, "/tmp/test.env", (), "low"))
        reset_settings()
        settings = load_settings()
        self.assertTrue(settings.use_online_provider)
        self.assertEqual(settings.model, DEFAULT_MODEL)
        self.assertEqual(settings.dotenv_path, "")
        self.assertFalse(settings.enable_native_freecad_tools)
        self.assertEqual(settings.native_tool_workbenches, native_tool_workbench_choices())
        self.assertEqual(settings.reasoning_effort, DEFAULT_REASONING_EFFORT)

    def test_native_tool_workbench_choices_exclude_empty_packs(self):
        choices = native_tool_workbench_choices()
        self.assertIn("PartDesignWorkbench", choices)
        self.assertIn("SketcherWorkbench", choices)
        self.assertIn("AssemblyWorkbench", choices)
        self.assertNotIn("FemWorkbench", choices)
        self.assertNotIn("MeshWorkbench", choices)
        self.assertEqual(
            choices,
            tuple(
                sorted(
                    workbench
                    for workbench, pack in WORKBENCH_TOOL_PACKS.items()
                    if tuple(pack.provider_tool_names())
                )
            ),
        )

    def test_preferences_normalize_reasoning_effort(self):
        self.assertEqual(tuple(REASONING_EFFORTS), ("none", "minimal", "low", "medium", "high", "xhigh"))
        self.assertEqual(normalize_reasoning_effort("LOW"), "low")
        self.assertEqual(normalize_reasoning_effort("not-real"), DEFAULT_REASONING_EFFORT)

    def test_preferences_normalize_provider(self):
        self.assertEqual(normalize_provider("anthropic"), "anthropic")
        self.assertEqual(normalize_provider("Anthropic"), "anthropic")
        self.assertEqual(normalize_provider("OPENAI"), "openai")
        self.assertEqual(normalize_provider("not-a-provider"), "openai")
        self.assertEqual(normalize_provider(None), "openai")

    def test_preferences_persist_provider_and_per_provider_models(self):
        save_settings(
            VibeCADSettings(
                provider="anthropic",
                model="gpt-5.5",
                anthropic_model="claude-sonnet-5",
            )
        )
        settings = load_settings()
        self.assertEqual(settings.provider, "anthropic")
        self.assertEqual(settings.model, "gpt-5.5")
        self.assertEqual(settings.anthropic_model, "claude-sonnet-5")
        self.assertEqual(settings.active_model, "claude-sonnet-5")

        save_settings(
            VibeCADSettings(
                provider="openai",
                model="gpt-5.5",
                anthropic_model="claude-sonnet-5",
            )
        )
        self.assertEqual(load_settings().active_model, "gpt-5.5")

        reset_settings()
        settings = load_settings()
        self.assertEqual(settings.provider, "openai")
        self.assertEqual(settings.anthropic_model, DEFAULT_ANTHROPIC_MODEL)

    def test_preferences_persist_and_reset_base_urls(self):
        save_settings(
            VibeCADSettings(
                openai_base_url="http://localhost:8000/v1",
                anthropic_base_url="http://localhost:9000",
            )
        )
        settings = load_settings()
        self.assertEqual(settings.openai_base_url, "http://localhost:8000/v1")
        self.assertEqual(settings.anthropic_base_url, "http://localhost:9000")

        reset_settings()
        settings = load_settings()
        self.assertEqual(settings.openai_base_url, "")
        self.assertEqual(settings.anthropic_base_url, "")
        self.assertIsNone(settings.active_base_url)

    def test_active_base_url_and_base_url_for_normalize_blank_values(self):
        blank = VibeCADSettings()
        self.assertIsNone(blank.active_base_url)
        self.assertIsNone(blank.base_url_for("openai"))
        self.assertIsNone(blank.base_url_for("anthropic"))

        whitespace = VibeCADSettings(
            openai_base_url="   ", anthropic_base_url="\t"
        )
        self.assertIsNone(whitespace.active_base_url)
        self.assertIsNone(whitespace.base_url_for("openai"))
        self.assertIsNone(whitespace.base_url_for("anthropic"))

        configured = VibeCADSettings(
            provider="openai",
            openai_base_url=" http://localhost:8000/v1 ",
            anthropic_base_url="http://localhost:9000",
        )
        self.assertEqual(configured.active_base_url, "http://localhost:8000/v1")
        self.assertEqual(
            configured.base_url_for("openai"), "http://localhost:8000/v1"
        )
        self.assertEqual(
            configured.base_url_for("anthropic"), "http://localhost:9000"
        )

        anthropic_selected = VibeCADSettings(
            provider="anthropic",
            openai_base_url="http://localhost:8000/v1",
            anthropic_base_url="http://localhost:9000",
        )
        self.assertEqual(
            anthropic_selected.active_base_url, "http://localhost:9000"
        )

    def test_fetch_models_for_provider_reports_missing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_env = Path(tmp) / ".env"
            empty_env.write_text("", encoding="utf-8")
            old_env = os.environ.get("ANTHROPIC_API_KEY")
            original_keyring = sys.modules.get("keyring")
            sys.modules["keyring"] = FakeKeyringModule()
            try:
                os.environ.pop("ANTHROPIC_API_KEY", None)
                payload = fetch_models_for_provider("anthropic", dotenv_path=empty_env)
                self.assertFalse(payload["ok"])
                self.assertIn("Anthropic", payload["error"])
                self.assertEqual(payload["models"], [])
            finally:
                if old_env is not None:
                    os.environ["ANTHROPIC_API_KEY"] = old_env
                if original_keyring is None:
                    sys.modules.pop("keyring", None)
                else:
                    sys.modules["keyring"] = original_keyring

    def test_fetch_models_for_provider_uses_configured_dotenv_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "ANTHROPIC_API_KEY='sk-ant-key1234'\n", encoding="utf-8"
            )
            calls = []
            original_list = VibeCADPreferences.list_provider_models
            old_env = os.environ.get("ANTHROPIC_API_KEY")
            original_keyring = sys.modules.get("keyring")
            sys.modules["keyring"] = FakeKeyringModule()
            try:
                os.environ.pop("ANTHROPIC_API_KEY", None)

                def fake_list(api_key, *, provider="openai", **kwargs):
                    calls.append((api_key, provider, kwargs.get("base_url")))
                    return {"ok": True, "models": ["claude-sonnet-5"], "error": None}

                VibeCADPreferences.list_provider_models = fake_list
                payload = fetch_models_for_provider("anthropic", dotenv_path=env_path)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["models"], ["claude-sonnet-5"])
                self.assertEqual(calls, [("sk-ant-key1234", "anthropic", None)])

                payload = fetch_models_for_provider(
                    "anthropic",
                    dotenv_path=env_path,
                    base_url="http://localhost:9000",
                )
                self.assertTrue(payload["ok"])
                self.assertEqual(
                    calls[-1],
                    ("sk-ant-key1234", "anthropic", "http://localhost:9000"),
                )
            finally:
                VibeCADPreferences.list_provider_models = original_list
                if old_env is not None:
                    os.environ["ANTHROPIC_API_KEY"] = old_env
                if original_keyring is None:
                    sys.modules.pop("keyring", None)
                else:
                    sys.modules["keyring"] = original_keyring

    def test_preferences_page_title_and_widget_inventory(self):
        try:
            from PySide import QtWidgets
        except Exception:
            self.skipTest("PySide unavailable")

        app = _ensure_offscreen_qapplication()
        if app is None:
            self.skipTest("QApplication unavailable")
        page = VibeCADPreferencesPage()
        try:
            self.assertEqual(page.form.windowTitle(), "VibeCAD")
            expected_widgets = {
                "VibeCADPrefUseOnlineProvider": QtWidgets.QCheckBox,
                "VibeCADPrefProvider": QtWidgets.QComboBox,
                "VibeCADPrefModel": QtWidgets.QComboBox,
                "VibeCADPrefAnthropicModel": QtWidgets.QComboBox,
                "VibeCADPrefFetchModels": QtWidgets.QPushButton,
                "VibeCADPrefReasoningEffort": QtWidgets.QComboBox,
                "VibeCADPrefDotenvPath": QtWidgets.QLineEdit,
                "VibeCADPrefOpenAIBaseUrl": QtWidgets.QLineEdit,
                "VibeCADPrefAnthropicBaseUrl": QtWidgets.QLineEdit,
                "VibeCADPrefBrowseDotenv": QtWidgets.QPushButton,
                "VibeCADPrefApiKey": QtWidgets.QLineEdit,
                "VibeCADPrefSaveApiKey": QtWidgets.QPushButton,
                "VibeCADPrefValidateAuth": QtWidgets.QPushButton,
                "VibeCADPrefLogout": QtWidgets.QPushButton,
                "VibeCADPrefAuthStatus": QtWidgets.QLabel,
                "VibeCADPrefRefreshAuth": QtWidgets.QPushButton,
            }
            for object_name, widget_type in expected_widgets.items():
                child = page.form.findChild(widget_type, object_name)
                self.assertIsNotNone(
                    child,
                    f"Preferences page is missing configurable widget {object_name}",
                )
            self.assertIsNone(
                page.form.findChild(QtWidgets.QCheckBox, "VibeCADPrefEnableBuildScript")
            )
        finally:
            page.form.setParent(None)
            app.processEvents()

    def test_tools_preferences_page_persists_native_workbenches(self):
        try:
            from PySide import QtCore, QtWidgets
        except Exception:
            self.skipTest("PySide unavailable")

        page = None
        try:
            app = _ensure_offscreen_qapplication()
            if app is None:
                self.skipTest("QApplication unavailable")
            page = VibeCADToolsPreferencesPage()
            save_settings(
                VibeCADSettings(
                    enable_build_script=False,
                    enable_native_freecad_tools=True,
                    native_tool_workbenches=("DraftWorkbench",),
                )
            )
            page.loadSettings()
            self.assertEqual(page.form.windowTitle(), "Tools")
            script_mode = page.form.findChild(
                QtWidgets.QCheckBox, "VibeCADPrefEnableBuildScript"
            )
            self.assertIsNotNone(script_mode)
            self.assertFalse(script_mode.isChecked())
            self.assertTrue(page.enable_native.isChecked())
            checklist = page.form.findChild(
                QtWidgets.QListWidget, "VibeCADPrefNativeToolWorkbenches"
            )
            self.assertIsNotNone(checklist)
            part_item = checklist.findItems("PartWorkbench", QtCore.Qt.MatchExactly)[0]
            draft_item = checklist.findItems("DraftWorkbench", QtCore.Qt.MatchExactly)[0]
            self.assertFalse(checklist.findItems("FemWorkbench", QtCore.Qt.MatchExactly))
            self.assertFalse(checklist.findItems("MeshWorkbench", QtCore.Qt.MatchExactly))
            self.assertEqual(part_item.checkState(), QtCore.Qt.Unchecked)
            self.assertEqual(draft_item.checkState(), QtCore.Qt.Checked)
            script_mode.setChecked(True)
            part_item.setCheckState(QtCore.Qt.Checked)
            page.saveSettings()
            if app:
                app.processEvents()
            settings = load_settings()
            self.assertTrue(settings.enable_build_script)
            self.assertTrue(settings.enable_native_freecad_tools)
            self.assertEqual(settings.native_tool_workbenches, ("DraftWorkbench", "PartWorkbench"))
        finally:
            if page is not None:
                page.form.setParent(None)
                if app:
                    app.processEvents()

    def test_preferences_api_key_controls_use_keyring_and_clear_secret(self):
        try:
            from PySide import QtWidgets
        except Exception:
            self.skipTest("PySide unavailable")

        original = sys.modules.get("keyring")
        fake = FakeKeyringModule()
        sys.modules["keyring"] = fake
        page = None
        try:
            app = QtWidgets.QApplication.instance()
            if app is None:
                self.skipTest("QApplication unavailable")
            page = VibeCADPreferencesPage()
            page.loadSettings()
            page.api_key.setText("sk-test123456")
            save_button = page.form.findChild(QtWidgets.QPushButton, "VibeCADPrefSaveApiKey")
            validate_button = page.form.findChild(QtWidgets.QPushButton, "VibeCADPrefValidateAuth")
            logout_button = page.form.findChild(QtWidgets.QPushButton, "VibeCADPrefLogout")
            self.assertIsNotNone(save_button)
            self.assertIsNotNone(validate_button)
            self.assertIsNotNone(logout_button)

            save_button.click()
            if app:
                app.processEvents()

            self.assertEqual(page.api_key.text(), "")
            self.assertEqual(
                fake.values[(KEYRING_SERVICE, KEYRING_USERNAME)],
                "sk-test123456",
            )
            self.assertIn("OS keyring", page.status.text())
            self.assertIn("sk-...3456", page.status.text())
            self.assertNotIn("test123456", page.status.text())
            self.assertEqual(self._pref.GetString("OpenAIApiKey", ""), "")

            original_validate_configured = VibeCADPreferences.validate_configured_openai_auth
            try:
                VibeCADPreferences.validate_configured_openai_auth = lambda **_kwargs: types.SimpleNamespace(
                    status=AuthStatus.VERIFIED,
                    source="OS keyring",
                    redacted_key="sk-...3456",
                    message="OpenAI API key validated.",
                )
                validate_button.click()
                if app:
                    app.processEvents()
                self.assertIn("verified", page.status.text())
                self.assertIn("OS keyring", page.status.text())
                self.assertIn("sk-...3456", page.status.text())
                self.assertNotIn("test123456", page.status.text())
            finally:
                VibeCADPreferences.validate_configured_openai_auth = original_validate_configured

            original_validate_typed = VibeCADPreferences.validate_openai_api_key
            try:
                calls = []

                def fake_validate_typed(value, **_kwargs):
                    calls.append(value)
                    return types.SimpleNamespace(
                        status=AuthStatus.INVALID,
                        source="unsaved API key",
                        redacted_key="sk-...0000",
                        message="OpenAI credential validation failed with HTTP 401.",
                    )

                VibeCADPreferences.validate_openai_api_key = fake_validate_typed
                page.api_key.setText("sk-invalid0000")
                validate_button.click()
                if app:
                    app.processEvents()
                self.assertEqual(calls, ["sk-invalid0000"])
                self.assertEqual(page.api_key.text(), "")
                self.assertIn("invalid", page.status.text())
                self.assertIn("unsaved API key", page.status.text())
                self.assertIn("sk-...0000", page.status.text())
                self.assertNotIn("invalid0000", page.status.text())
            finally:
                VibeCADPreferences.validate_openai_api_key = original_validate_typed

            logout_button.click()
            if app:
                app.processEvents()

            self.assertNotIn((KEYRING_SERVICE, KEYRING_USERNAME), fake.values)
            self.assertEqual(page.api_key.text(), "")
            self.assertEqual(self._pref.GetString("OpenAIApiKey", ""), "")
        finally:
            if page is not None:
                page.form.setParent(None)
                app = QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
            if original is None:
                sys.modules.pop("keyring", None)
            else:
                sys.modules["keyring"] = original
