# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD preferences for VibeCAD.

Preferences intentionally store only non-secret settings. API keys are read
from the process environment, OS keyring, or a user-selected .env file by
VibeCADAuth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import FreeCAD as App

from VibeCADAuth import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    delete_keyring_key,
    list_provider_models,
    resolve_auth_credential,
    resolve_auth_state,
    store_keyring_key,
    validate_api_key,
    validate_configured_auth,
)
from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS


PREFERENCE_GROUP = "User parameter:BaseApp/Preferences/Mod/VibeCAD"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_MODELS = {"openai": DEFAULT_MODEL, "anthropic": DEFAULT_ANTHROPIC_MODEL}
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
DEFAULT_REASONING_EFFORT = "high"


def normalize_provider(value: str | None) -> str:
    clean = (value or "").strip().lower()
    return clean if clean in PROVIDERS else DEFAULT_PROVIDER


@dataclass(frozen=True)
class VibeCADSettings:
    use_online_provider: bool = True
    model: str = DEFAULT_MODEL
    dotenv_path: str = ""
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    provider: str = DEFAULT_PROVIDER
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    enable_build_script: bool = False
    enable_native_freecad_tools: bool = False
    native_tool_workbenches: tuple[str, ...] = ()
    openai_base_url: str = ""
    anthropic_base_url: str = ""

    @property
    def resolved_dotenv_path(self) -> Path | None:
        if not self.dotenv_path:
            return None
        return Path(self.dotenv_path).expanduser()

    @property
    def active_model(self) -> str:
        """Model for the selected provider."""
        if normalize_provider(self.provider) == "anthropic":
            return self.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
        return self.model.strip() or DEFAULT_MODEL

    @property
    def active_base_url(self) -> str | None:
        """Base URL override for the selected provider; None means official endpoint."""
        if normalize_provider(self.provider) == "anthropic":
            override = self.anthropic_base_url.strip()
        else:
            override = self.openai_base_url.strip()
        return override or None

    def base_url_for(self, provider: str) -> str | None:
        """Base URL override for ``provider``; None means official endpoint."""
        if normalize_provider(provider) == "anthropic":
            override = self.anthropic_base_url.strip()
        else:
            override = self.openai_base_url.strip()
        return override or None


def preferences():
    return App.ParamGet(PREFERENCE_GROUP)


def native_tool_workbench_choices() -> tuple[str, ...]:
    return tuple(
        sorted(
            workbench
            for workbench, pack in WORKBENCH_TOOL_PACKS.items()
            if tuple(pack.provider_tool_names())
        )
    )


def _parse_workbench_list(value: str, *, default_all: bool = False) -> tuple[str, ...]:
    known = set(native_tool_workbench_choices())
    if not str(value or "").strip() and default_all:
        return native_tool_workbench_choices()
    items = []
    for item in str(value or "").split(","):
        workbench = item.strip()
        if workbench and workbench in known and workbench not in items:
            items.append(workbench)
    return tuple(sorted(items))


def _format_workbench_list(value: tuple[str, ...]) -> str:
    return ",".join(sorted(set(value).intersection(native_tool_workbench_choices())))


def normalize_reasoning_effort(value: str | None) -> str:
    clean = (value or "").strip().lower()
    return clean if clean in REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


def load_settings() -> VibeCADSettings:
    pref = preferences()
    return VibeCADSettings(
        use_online_provider=pref.GetBool("UseOnlineProvider", True),
        model=pref.GetString("Model", DEFAULT_MODEL) or DEFAULT_MODEL,
        dotenv_path=pref.GetString("DotenvPath", ""),
        reasoning_effort=normalize_reasoning_effort(
            pref.GetString("ReasoningEffort", DEFAULT_REASONING_EFFORT)
        ),
        provider=normalize_provider(pref.GetString("Provider", DEFAULT_PROVIDER)),
        anthropic_model=pref.GetString("AnthropicModel", DEFAULT_ANTHROPIC_MODEL)
        or DEFAULT_ANTHROPIC_MODEL,
        enable_build_script=pref.GetBool("EnableBuildScript", False),
        enable_native_freecad_tools=pref.GetBool("EnableNativeFreeCADTools", False),
        native_tool_workbenches=_parse_workbench_list(
            pref.GetString("NativeToolWorkbenches", ""), default_all=True
        ),
        openai_base_url=pref.GetString("OpenAIBaseUrl", ""),
        anthropic_base_url=pref.GetString("AnthropicBaseUrl", ""),
    )


def save_settings(settings: VibeCADSettings) -> None:
    pref = preferences()
    pref.SetBool("UseOnlineProvider", bool(settings.use_online_provider))
    pref.SetString("Model", settings.model.strip() or DEFAULT_MODEL)
    pref.SetString("DotenvPath", settings.dotenv_path.strip())
    pref.SetString(
        "ReasoningEffort", normalize_reasoning_effort(settings.reasoning_effort)
    )
    pref.SetString("Provider", normalize_provider(settings.provider))
    pref.SetString(
        "AnthropicModel", settings.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
    )
    pref.SetBool("EnableBuildScript", bool(settings.enable_build_script))
    pref.SetBool("EnableNativeFreeCADTools", bool(settings.enable_native_freecad_tools))
    pref.SetString(
        "NativeToolWorkbenches",
        _format_workbench_list(settings.native_tool_workbenches),
    )
    pref.SetString("OpenAIBaseUrl", settings.openai_base_url.strip())
    pref.SetString("AnthropicBaseUrl", settings.anthropic_base_url.strip())


def reset_settings() -> None:
    pref = preferences()
    pref.RemBool("UseOnlineProvider")
    pref.RemString("Model")
    pref.RemString("DotenvPath")
    pref.RemString("ReasoningEffort")
    pref.RemString("Provider")
    pref.RemString("AnthropicModel")
    pref.RemBool("EnableBuildScript")
    pref.RemBool("EnableNativeFreeCADTools")
    pref.RemString("NativeToolWorkbenches")
    pref.RemString("OpenAIBaseUrl")
    pref.RemString("AnthropicBaseUrl")


def configured_dotenv_path() -> Path | None:
    settings = load_settings()
    if settings.resolved_dotenv_path is not None:
        return settings.resolved_dotenv_path
    cwd_dotenv = Path.cwd() / ".env"
    return cwd_dotenv if cwd_dotenv.exists() else None


def fetch_models_for_provider(
    provider: str,
    dotenv_path: Path | None = None,
    base_url: str | None = None,
) -> dict:
    """Resolve the configured key for ``provider`` and query its models endpoint.

    Returns the ``list_provider_models`` payload:
    {"ok": bool, "models": [str, ...], "error": str | None}.
    """
    clean_provider = normalize_provider(provider)
    credential = resolve_auth_credential(
        dotenv_path=dotenv_path, provider=clean_provider
    )
    if credential is None:
        display = PROVIDERS[clean_provider].display_name
        return {
            "ok": False,
            "models": [],
            "error": f"No {display} API key is configured.",
        }
    return list_provider_models(
        credential.value, provider=clean_provider, base_url=base_url
    )


class VibeCADPreferencesPage:
    def __init__(self, parent=None):
        from PySide import QtCore, QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADPreferencesPage")
        self.form.setWindowTitle("VibeCAD")
        layout = QtWidgets.QFormLayout(self.form)

        self.use_online = QtWidgets.QCheckBox(self.form)
        self.use_online.setObjectName("VibeCADPrefUseOnlineProvider")
        layout.addRow("Use online provider", self.use_online)

        self.provider = QtWidgets.QComboBox(self.form)
        self.provider.setObjectName("VibeCADPrefProvider")
        for provider_id in sorted(PROVIDERS):
            self.provider.addItem(PROVIDERS[provider_id].display_name, provider_id)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        layout.addRow("Provider", self.provider)

        self.model = QtWidgets.QComboBox(self.form)
        self.model.setObjectName("VibeCADPrefModel")
        self.model.setEditable(True)
        layout.addRow("OpenAI model", self.model)

        self.anthropic_model = QtWidgets.QComboBox(self.form)
        self.anthropic_model.setObjectName("VibeCADPrefAnthropicModel")
        self.anthropic_model.setEditable(True)
        layout.addRow("Anthropic model", self.anthropic_model)

        self.openai_base_url = QtWidgets.QLineEdit(self.form)
        self.openai_base_url.setObjectName("VibeCADPrefOpenAIBaseUrl")
        self.openai_base_url.setPlaceholderText("https://api.openai.com/v1")
        self.openai_base_url.setToolTip(
            "Override the OpenAI API endpoint (include the /v1 segment). "
            "Leave blank to use the official endpoint. Use this to point at "
            "a local server that implements the OpenAI API."
        )
        layout.addRow("OpenAI base URL", self.openai_base_url)

        self.anthropic_base_url = QtWidgets.QLineEdit(self.form)
        self.anthropic_base_url.setObjectName("VibeCADPrefAnthropicBaseUrl")
        self.anthropic_base_url.setPlaceholderText("https://api.anthropic.com")
        self.anthropic_base_url.setToolTip(
            "Override the Anthropic API endpoint (without the /v1 segment). "
            "Leave blank to use the official endpoint."
        )
        layout.addRow("Anthropic base URL", self.anthropic_base_url)

        self.fetch_models = QtWidgets.QPushButton("Fetch models", self.form)
        self.fetch_models.setObjectName("VibeCADPrefFetchModels")
        self.fetch_models.clicked.connect(self._fetch_models)
        layout.addRow("", self.fetch_models)

        self.reasoning_effort = QtWidgets.QComboBox(self.form)
        self.reasoning_effort.setObjectName("VibeCADPrefReasoningEffort")
        self.reasoning_effort.addItems(REASONING_EFFORTS)
        layout.addRow("Reasoning effort", self.reasoning_effort)

        dotenv_row = QtWidgets.QHBoxLayout()
        self.dotenv_path = QtWidgets.QLineEdit(self.form)
        self.dotenv_path.setObjectName("VibeCADPrefDotenvPath")
        browse = QtWidgets.QPushButton("Browse", self.form)
        browse.setObjectName("VibeCADPrefBrowseDotenv")
        browse.clicked.connect(self._browse_dotenv)
        dotenv_row.addWidget(self.dotenv_path, 1)
        dotenv_row.addWidget(browse)
        layout.addRow(".env path", dotenv_row)

        api_key_row = QtWidgets.QHBoxLayout()
        self.api_key = QtWidgets.QLineEdit(self.form)
        self.api_key.setObjectName("VibeCADPrefApiKey")
        self.api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key.setPlaceholderText("Paste an API key for the selected provider")
        save_key = QtWidgets.QPushButton("Save Key", self.form)
        save_key.setObjectName("VibeCADPrefSaveApiKey")
        save_key.clicked.connect(self._save_api_key)
        logout = QtWidgets.QPushButton("Logout", self.form)
        logout.setObjectName("VibeCADPrefLogout")
        logout.clicked.connect(self._logout)
        validate = QtWidgets.QPushButton("Validate", self.form)
        validate.setObjectName("VibeCADPrefValidateAuth")
        validate.clicked.connect(self._validate_auth)
        api_key_row.addWidget(self.api_key, 1)
        api_key_row.addWidget(save_key)
        api_key_row.addWidget(validate)
        api_key_row.addWidget(logout)
        layout.addRow("API key", api_key_row)

        self.status = QtWidgets.QLabel(self.form)
        self.status.setObjectName("VibeCADPrefAuthStatus")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("Auth status", self.status)

        refresh = QtWidgets.QPushButton("Refresh", self.form)
        refresh.setObjectName("VibeCADPrefRefreshAuth")
        refresh.clicked.connect(self._refresh_status)
        layout.addRow("", refresh)

    def _browse_dotenv(self) -> None:
        from PySide import QtWidgets

        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self.form,
            "Select .env file",
            self.dotenv_path.text() or str(Path.home()),
            "Environment files (*.env);;All files (*)",
        )
        if selected:
            self.dotenv_path.setText(selected)
            self._refresh_status()

    def _selected_provider(self) -> str:
        data = self.provider.currentData()
        return normalize_provider(data if isinstance(data, str) else None)

    def _provider_changed(self, _index: int = 0) -> None:
        self.api_key.clear()
        self._refresh_status()

    def _set_combo_text(self, combo, text: str) -> None:
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(text)

    def _fetch_models(self) -> None:
        provider = self._selected_provider()
        settings = self._current_settings()
        result = fetch_models_for_provider(
            provider,
            dotenv_path=settings.resolved_dotenv_path,
            base_url=settings.base_url_for(provider),
        )
        if not result["ok"]:
            self.status.setText(f"models_error | {result['error']}")
            return
        combo = self.anthropic_model if provider == "anthropic" else self.model
        current = combo.currentText().strip()
        combo.clear()
        combo.addItems(result["models"])
        if current:
            self._set_combo_text(combo, current)
        display = PROVIDERS[provider].display_name
        self.status.setText(f"models_ok | {display} | {len(result['models'])} models")

    def _save_api_key(self) -> None:
        result = store_keyring_key(
            self.api_key.text(), provider=self._selected_provider()
        )
        self.api_key.clear()
        if not result["stored"]:
            self.status.setText(f"not_configured | {result['error']}")
            return
        self._refresh_status()

    def _logout(self) -> None:
        delete_keyring_key(provider=self._selected_provider())
        self.api_key.clear()
        self._refresh_status()

    def _validate_auth(self) -> None:
        provider = self._selected_provider()
        typed_key = self.api_key.text().strip()
        settings = self._current_settings()
        base_url = settings.base_url_for(provider)
        if typed_key:
            auth = validate_api_key(
                typed_key,
                provider=provider,
                source="unsaved API key",
                base_url=base_url,
            )
            self.api_key.clear()
        else:
            auth = validate_configured_auth(
                provider=provider,
                dotenv_path=settings.resolved_dotenv_path,
                base_url=base_url,
            )
        source = f" | {auth.source}" if auth.source else ""
        key = f" | {auth.redacted_key}" if auth.redacted_key else ""
        message = f" | {auth.message}" if auth.message else ""
        self.status.setText(f"{auth.status.value}{source}{key}{message}")

    def _current_settings(self) -> VibeCADSettings:
        existing = load_settings()
        return VibeCADSettings(
            use_online_provider=self.use_online.isChecked(),
            model=self.model.currentText().strip() or DEFAULT_MODEL,
            dotenv_path=self.dotenv_path.text().strip(),
            reasoning_effort=normalize_reasoning_effort(
                self.reasoning_effort.currentText()
            ),
            provider=self._selected_provider(),
            anthropic_model=self.anthropic_model.currentText().strip()
            or DEFAULT_ANTHROPIC_MODEL,
            enable_build_script=existing.enable_build_script,
            enable_native_freecad_tools=existing.enable_native_freecad_tools,
            native_tool_workbenches=existing.native_tool_workbenches,
            openai_base_url=self.openai_base_url.text().strip(),
            anthropic_base_url=self.anthropic_base_url.text().strip(),
        )

    def _refresh_status(self) -> None:
        settings = self._current_settings()
        auth = resolve_auth_state(
            dotenv_path=settings.resolved_dotenv_path,
            provider=self._selected_provider(),
        )
        source = f" | {auth.source}" if auth.source else ""
        key = f" | {auth.redacted_key}" if auth.redacted_key else ""
        self.status.setText(f"{auth.status.value}{source}{key}")

    def saveSettings(self) -> None:
        save_settings(self._current_settings())

    def loadSettings(self) -> None:
        from PySide import QtCore

        settings = load_settings()
        self.use_online.setChecked(settings.use_online_provider)
        provider_index = self.provider.findData(normalize_provider(settings.provider))
        self.provider.setCurrentIndex(provider_index if provider_index >= 0 else 0)
        self._set_combo_text(self.model, settings.model)
        self._set_combo_text(self.anthropic_model, settings.anthropic_model)
        index = self.reasoning_effort.findText(settings.reasoning_effort)
        self.reasoning_effort.setCurrentIndex(index if index >= 0 else 0)
        self.dotenv_path.setText(settings.dotenv_path)
        self.openai_base_url.setText(settings.openai_base_url)
        self.anthropic_base_url.setText(settings.anthropic_base_url)
        self.api_key.clear()
        self._refresh_status()


class VibeCADToolsPreferencesPage:
    def __init__(self, parent=None):
        from PySide import QtCore, QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADToolsPreferencesPage")
        self.form.setWindowTitle("Tools")
        layout = QtWidgets.QVBoxLayout(self.form)

        self.enable_build_script = QtWidgets.QCheckBox(
            "Expose model.build_from_script as the geometry write path", self.form
        )
        self.enable_build_script.setObjectName("VibeCADPrefEnableBuildScript")
        self.enable_build_script.setToolTip(
            "Off by default. When enabled, the model writes FreeCAD Python "
            "through model.build_from_script and structured geometry write "
            "tools are hidden. Read and view tools stay available."
        )
        layout.addWidget(self.enable_build_script)

        self.enable_native = QtWidgets.QCheckBox(
            "Expose native FreeCAD workbench tools to the AI model", self.form
        )
        self.enable_native.setObjectName("VibeCADPrefEnableNativeFreeCADTools")
        self.enable_native.setToolTip(
            "Off by default. When enabled, VibeCAD exposes only the native "
            "tool pack for the currently selected/entered FreeCAD workbench. "
            "The AI-native CAD tools remain available either way."
        )
        layout.addWidget(self.enable_native)

        self.tool_packs = QtWidgets.QListWidget(self.form)
        self.tool_packs.setObjectName("VibeCADPrefNativeToolWorkbenches")
        self.tool_packs.setMinimumHeight(260)
        for workbench in native_tool_workbench_choices():
            item = QtWidgets.QListWidgetItem(workbench)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked)
            self.tool_packs.addItem(item)
        layout.addWidget(self.tool_packs, 1)

        self.status = QtWidgets.QLabel(self.form)
        self.status.setObjectName("VibeCADPrefToolsStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _current_settings(self) -> VibeCADSettings:
        from PySide import QtCore

        existing = load_settings()
        enabled_workbenches = []
        for index in range(self.tool_packs.count()):
            item = self.tool_packs.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                enabled_workbenches.append(item.text())
        return VibeCADSettings(
            use_online_provider=existing.use_online_provider,
            model=existing.model,
            dotenv_path=existing.dotenv_path,
            reasoning_effort=existing.reasoning_effort,
            provider=existing.provider,
            anthropic_model=existing.anthropic_model,
            enable_build_script=self.enable_build_script.isChecked(),
            enable_native_freecad_tools=self.enable_native.isChecked(),
            native_tool_workbenches=tuple(enabled_workbenches),
            openai_base_url=existing.openai_base_url,
            anthropic_base_url=existing.anthropic_base_url,
        )

    def saveSettings(self) -> None:
        save_settings(self._current_settings())

    def loadSettings(self) -> None:
        from PySide import QtCore

        settings = load_settings()
        self.enable_build_script.setChecked(settings.enable_build_script)
        self.enable_native.setChecked(settings.enable_native_freecad_tools)
        enabled = set(settings.native_tool_workbenches)
        for index in range(self.tool_packs.count()):
            item = self.tool_packs.item(index)
            item.setCheckState(
                QtCore.Qt.Checked if item.text() in enabled else QtCore.Qt.Unchecked
            )
        if settings.enable_build_script:
            self.status.setText(
                "Script mode is on. The model gets model.build_from_script as "
                "the geometry write path; structured/native write tools stay hidden."
            )
        elif settings.enable_native_freecad_tools:
            self.status.setText(
                "Native mode is on. The model will see only the native tools "
                "belonging to the active VibeCAD/FreeCAD workbench pack."
            )
        else:
            self.status.setText(
                "Native mode is off. The model sees the AI-native CAD tools by "
                "default; raw workbench tools stay hidden."
            )
