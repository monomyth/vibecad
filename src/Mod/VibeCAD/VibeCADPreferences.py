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
from VibeCADDebug import default_capture_directory, resolve_capture_directory

PREFERENCE_GROUP = "User parameter:BaseApp/Preferences/Mod/VibeCAD"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_MODELS = {"openai": DEFAULT_MODEL, "anthropic": DEFAULT_ANTHROPIC_MODEL}
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_SCRIPTED_TIMEOUT_SECONDS = 300.0
DEFAULT_SCRIPTED_MEMORY_LIMIT_MB = 6144


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
    openai_base_url: str = ""
    anthropic_base_url: str = ""
    intent_memory_enabled: bool = True
    openai_intent_memory_model: str = ""
    anthropic_intent_memory_model: str = ""
    build123d_enabled: bool = False
    openscad_enabled: bool = False
    openscad_executable: str = ""
    openscad_library_paths: str = ""
    scripted_timeout_seconds: float = DEFAULT_SCRIPTED_TIMEOUT_SECONDS
    scripted_memory_limit_mb: int = DEFAULT_SCRIPTED_MEMORY_LIMIT_MB

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

    def model_for(self, provider: str) -> str:
        """Configured interactive model for ``provider``."""
        if normalize_provider(provider) == "anthropic":
            return self.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
        return self.model.strip() or DEFAULT_MODEL

    def intent_memory_model_for(self, provider: str) -> str:
        """Intent compiler model, defaulting explicitly to the interactive model."""
        if normalize_provider(provider) == "anthropic":
            override = self.anthropic_intent_memory_model.strip()
        else:
            override = self.openai_intent_memory_model.strip()
        return override or self.model_for(provider)


@dataclass(frozen=True)
class VibeCADDebugSettings:
    context_debug_enabled: bool = False
    capture_directory: str = ""

    @property
    def resolved_capture_directory(self) -> Path:
        return resolve_capture_directory(self.capture_directory)


def preferences():
    return App.ParamGet(PREFERENCE_GROUP)


def normalize_reasoning_effort(value: str | None) -> str:
    clean = (value or "").strip().lower()
    return clean if clean in REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


def _positive_float(value: object, default: float) -> float:
    try:
        clean = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return clean if clean > 0 else default


def _positive_int(value: object, default: int) -> int:
    try:
        clean = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return clean if clean > 0 else default


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
        openai_base_url=pref.GetString("OpenAIBaseUrl", ""),
        anthropic_base_url=pref.GetString("AnthropicBaseUrl", ""),
        intent_memory_enabled=pref.GetBool("IntentMemoryEnabled", True),
        openai_intent_memory_model=pref.GetString(
            "OpenAIIntentMemoryModel", ""
        ),
        anthropic_intent_memory_model=pref.GetString(
            "AnthropicIntentMemoryModel", ""
        ),
        build123d_enabled=pref.GetBool("Build123dEnabled", False),
        openscad_enabled=pref.GetBool("OpenSCADEnabled", False),
        openscad_executable=pref.GetString("OpenSCADExecutable", ""),
        openscad_library_paths=pref.GetString("OpenSCADLibraryPaths", ""),
        scripted_timeout_seconds=_positive_float(
            pref.GetFloat("ScriptedTimeoutSeconds", DEFAULT_SCRIPTED_TIMEOUT_SECONDS),
            DEFAULT_SCRIPTED_TIMEOUT_SECONDS,
        ),
        scripted_memory_limit_mb=_positive_int(
            pref.GetInt("ScriptedMemoryLimitMB", DEFAULT_SCRIPTED_MEMORY_LIMIT_MB),
            DEFAULT_SCRIPTED_MEMORY_LIMIT_MB,
        ),
    )


def load_debug_settings() -> VibeCADDebugSettings:
    pref = preferences()
    return VibeCADDebugSettings(
        context_debug_enabled=pref.GetBool("ContextDebugEnabled", False),
        capture_directory=pref.GetString("ContextDebugDirectory", ""),
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
    pref.SetString("OpenAIBaseUrl", settings.openai_base_url.strip())
    pref.SetString("AnthropicBaseUrl", settings.anthropic_base_url.strip())
    pref.SetBool("IntentMemoryEnabled", bool(settings.intent_memory_enabled))
    pref.SetString(
        "OpenAIIntentMemoryModel", settings.openai_intent_memory_model.strip()
    )
    pref.SetString(
        "AnthropicIntentMemoryModel", settings.anthropic_intent_memory_model.strip()
    )
    pref.SetBool("Build123dEnabled", bool(settings.build123d_enabled))
    pref.SetBool("OpenSCADEnabled", bool(settings.openscad_enabled))
    pref.SetString("OpenSCADExecutable", settings.openscad_executable.strip())
    pref.SetString("OpenSCADLibraryPaths", settings.openscad_library_paths.strip())
    pref.SetFloat(
        "ScriptedTimeoutSeconds",
        _positive_float(
            settings.scripted_timeout_seconds, DEFAULT_SCRIPTED_TIMEOUT_SECONDS
        ),
    )
    pref.SetInt(
        "ScriptedMemoryLimitMB",
        _positive_int(
            settings.scripted_memory_limit_mb, DEFAULT_SCRIPTED_MEMORY_LIMIT_MB
        ),
    )


def save_debug_settings(settings: VibeCADDebugSettings) -> None:
    pref = preferences()
    pref.SetBool("ContextDebugEnabled", bool(settings.context_debug_enabled))
    pref.SetString("ContextDebugDirectory", settings.capture_directory.strip())


def reset_settings() -> None:
    pref = preferences()
    pref.RemBool("UseOnlineProvider")
    pref.RemString("Model")
    pref.RemString("DotenvPath")
    pref.RemString("ReasoningEffort")
    pref.RemString("Provider")
    pref.RemString("AnthropicModel")
    pref.RemString("OpenAIBaseUrl")
    pref.RemString("AnthropicBaseUrl")
    pref.RemBool("IntentMemoryEnabled")
    pref.RemString("OpenAIIntentMemoryModel")
    pref.RemString("AnthropicIntentMemoryModel")
    pref.RemBool("Build123dEnabled")
    pref.RemBool("OpenSCADEnabled")
    pref.RemString("OpenSCADExecutable")
    pref.RemString("OpenSCADLibraryPaths")
    pref.RemFloat("ScriptedTimeoutSeconds")
    pref.RemInt("ScriptedMemoryLimitMB")
    pref.RemBool("ContextDebugEnabled")
    pref.RemString("ContextDebugDirectory")


def configured_dotenv_path() -> Path | None:
    return load_settings().resolved_dotenv_path


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

        self.build123d_enabled = QtWidgets.QCheckBox(self.form)
        self.build123d_enabled.setObjectName("VibeCADPrefBuild123dEnabled")
        self.build123d_enabled.setToolTip(
            "Make the isolated build123d modeling engine available in the "
            "PartDesign VibeCAD panel. Model-generated code never runs in "
            "FreeCAD's Python process."
        )
        self.build123d_enabled.toggled.connect(self._refresh_build123d_status)
        layout.addRow("Enable build123d", self.build123d_enabled)

        self.build123d_status = QtWidgets.QLabel(self.form)
        self.build123d_status.setObjectName("VibeCADPrefBuild123dStatus")
        self.build123d_status.setWordWrap(True)
        self.build123d_status.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
        )
        layout.addRow("build123d status", self.build123d_status)

        self.openscad_enabled = QtWidgets.QCheckBox(self.form)
        self.openscad_enabled.setObjectName("VibeCADPrefOpenSCADEnabled")
        self.openscad_enabled.setToolTip(
            "Make the isolated OpenSCAD source engine available in PartDesign. "
            "Compilation and CSG conversion run outside the FreeCAD GUI process."
        )
        self.openscad_enabled.toggled.connect(self._refresh_openscad_status)
        layout.addRow("Enable OpenSCAD", self.openscad_enabled)

        openscad_executable_row = QtWidgets.QHBoxLayout()
        self.openscad_executable = QtWidgets.QLineEdit(self.form)
        self.openscad_executable.setObjectName("VibeCADPrefOpenSCADExecutable")
        self.openscad_executable.setPlaceholderText("Use bundled OpenSCAD")
        self.openscad_executable.setToolTip(
            "Optional explicit OpenSCAD CLI override. Leave blank to use the "
            "runtime bundled with VibeCAD. VibeCAD does not search PATH."
        )
        self.openscad_executable.textChanged.connect(self._refresh_openscad_status)
        browse_openscad = QtWidgets.QPushButton("Browse", self.form)
        browse_openscad.setObjectName("VibeCADPrefBrowseOpenSCADExecutable")
        browse_openscad.clicked.connect(self._browse_openscad_executable)
        openscad_executable_row.addWidget(self.openscad_executable, 1)
        openscad_executable_row.addWidget(browse_openscad)
        layout.addRow("OpenSCAD executable", openscad_executable_row)

        self.openscad_library_paths = QtWidgets.QPlainTextEdit(self.form)
        self.openscad_library_paths.setObjectName("VibeCADPrefOpenSCADLibraryPaths")
        self.openscad_library_paths.setPlaceholderText(
            "One additional OpenSCAD library directory per line"
        )
        self.openscad_library_paths.setMaximumHeight(72)
        self.openscad_library_paths.setToolTip(
            "Explicit user library directories. Project libraries and bundled "
            "BOSL2/MCAD are always available."
        )
        layout.addRow("OpenSCAD libraries", self.openscad_library_paths)

        self.openscad_status = QtWidgets.QLabel(self.form)
        self.openscad_status.setObjectName("VibeCADPrefOpenSCADStatus")
        self.openscad_status.setWordWrap(True)
        self.openscad_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("OpenSCAD status", self.openscad_status)

        self.intent_memory_enabled = QtWidgets.QCheckBox(self.form)
        self.intent_memory_enabled.setObjectName("VibeCADPrefIntentMemoryEnabled")
        self.intent_memory_enabled.setToolTip(
            "Compile durable project intent after completed conversations so long "
            "sessions stay coherent without replaying the entire chat."
        )
        layout.addRow("Intent Memory", self.intent_memory_enabled)

        self.openai_intent_memory_model = QtWidgets.QComboBox(self.form)
        self.openai_intent_memory_model.setObjectName(
            "VibeCADPrefOpenAIIntentMemoryModel"
        )
        self.openai_intent_memory_model.addItem("Use active OpenAI model", "")
        layout.addRow("OpenAI memory model", self.openai_intent_memory_model)

        self.anthropic_intent_memory_model = QtWidgets.QComboBox(self.form)
        self.anthropic_intent_memory_model.setObjectName(
            "VibeCADPrefAnthropicIntentMemoryModel"
        )
        self.anthropic_intent_memory_model.addItem("Use active Anthropic model", "")
        layout.addRow("Anthropic memory model", self.anthropic_intent_memory_model)

        self.rebuild_intent_memory = QtWidgets.QPushButton(
            "Rebuild Intent Memory", self.form
        )
        self.rebuild_intent_memory.setObjectName(
            "VibeCADPrefRebuildIntentMemory"
        )
        self.rebuild_intent_memory.clicked.connect(self._rebuild_intent_memory)
        layout.addRow("", self.rebuild_intent_memory)

        self.intent_memory_status = QtWidgets.QLabel(self.form)
        self.intent_memory_status.setObjectName("VibeCADPrefIntentMemoryStatus")
        self.intent_memory_status.setWordWrap(True)
        layout.addRow("Memory status", self.intent_memory_status)

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

    def _refresh_build123d_status(self, _enabled: bool | None = None) -> None:
        if not self.build123d_enabled.isChecked():
            self.build123d_status.setText("disabled")
            return
        try:
            from VibeCADBuild123d import runtime_health

            health = runtime_health(refresh=True)
        except Exception as exc:
            self.build123d_status.setText(f"unavailable | {exc}")
            return
        if health.get("ready"):
            self.build123d_status.setText(
                f"ready | build123d {health.get('version')} | "
                "isolated process"
            )
        else:
            self.build123d_status.setText(
                f"unavailable | {health.get('error') or 'runtime check failed'}"
            )

    def _browse_openscad_executable(self) -> None:
        from PySide import QtWidgets

        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self.form,
            "Select OpenSCAD executable",
            self.openscad_executable.text() or str(Path.home()),
            "Executables (*.exe);;All files (*)",
        )
        if selected:
            self.openscad_executable.setText(selected)
            self._refresh_openscad_status()

    def _refresh_openscad_status(self, _enabled: bool | None = None) -> None:
        if not self.openscad_enabled.isChecked():
            self.openscad_status.setText("disabled")
            return
        try:
            from VibeCADOpenSCAD import runtime_health

            health = runtime_health(
                executable_override=self.openscad_executable.text().strip(),
                refresh=True,
            )
        except Exception as exc:
            self.openscad_status.setText(f"unavailable | {exc}")
            return
        if health.get("ready"):
            source = "override" if health.get("source") == "preference" else "bundled"
            self.openscad_status.setText(
                f"ready | {health.get('version')} | {source} | isolated process"
            )
        else:
            self.openscad_status.setText(
                f"unavailable | {health.get('error') or 'runtime check failed'}"
            )

    def _set_combo_text(self, combo, text: str) -> None:
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(text)

    def _memory_model_value(self, combo) -> str:
        data = combo.currentData()
        return str(data if data is not None else combo.currentText()).strip()

    def _set_memory_models(
        self,
        combo,
        models: list[str],
        current: str,
        active_label: str,
    ) -> None:
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(active_label, "")
            for model_name in models:
                combo.addItem(model_name, model_name)
            if current:
                index = combo.findData(current)
                if index < 0:
                    combo.addItem(current, current)
                    index = combo.count() - 1
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)

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
        memory_combo = (
            self.anthropic_intent_memory_model
            if provider == "anthropic"
            else self.openai_intent_memory_model
        )
        memory_current = self._memory_model_value(memory_combo)
        self._set_memory_models(
            memory_combo,
            result["models"],
            memory_current,
            (
                "Use active Anthropic model"
                if provider == "anthropic"
                else "Use active OpenAI model"
            ),
        )
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

    def _rebuild_intent_memory(self) -> None:
        save_settings(self._current_settings())
        if not self.intent_memory_enabled.isChecked():
            self.intent_memory_status.setText(
                "Enable Intent Memory before rebuilding it."
            )
            return
        try:
            import VibeCADGui

            result = VibeCADGui.rebuild_intent_memory_async()
        except Exception as exc:
            self.intent_memory_status.setText(str(exc))
            return
        if result.get("started"):
            self.intent_memory_status.setText(
                "Rebuild started. Progress is shown in the VibeCAD panel."
            )
        else:
            self.intent_memory_status.setText(str(result.get("error") or "Not started."))

    def _logout(self) -> None:
        delete_keyring_key(provider=self._selected_provider())
        self.api_key.clear()
        self.intent_memory_status.clear()
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
        persisted = load_settings()
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
            openai_base_url=self.openai_base_url.text().strip(),
            anthropic_base_url=self.anthropic_base_url.text().strip(),
            intent_memory_enabled=self.intent_memory_enabled.isChecked(),
            openai_intent_memory_model=self._memory_model_value(
                self.openai_intent_memory_model
            ),
            anthropic_intent_memory_model=(
                self._memory_model_value(self.anthropic_intent_memory_model)
            ),
            build123d_enabled=self.build123d_enabled.isChecked(),
            openscad_enabled=self.openscad_enabled.isChecked(),
            openscad_executable=self.openscad_executable.text().strip(),
            openscad_library_paths=self.openscad_library_paths.toPlainText().strip(),
            scripted_timeout_seconds=persisted.scripted_timeout_seconds,
            scripted_memory_limit_mb=persisted.scripted_memory_limit_mb,
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
        try:
            import VibeCADGui

            VibeCADGui.apply_modeling_preferences()
        except Exception as exc:
            App.Console.PrintWarning(
                f"VibeCAD modeling preference update failed: {exc}\n"
            )

    def loadSettings(self) -> None:
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
        self.intent_memory_enabled.setChecked(settings.intent_memory_enabled)
        self._set_memory_models(
            self.openai_intent_memory_model,
            [],
            settings.openai_intent_memory_model,
            "Use active OpenAI model",
        )
        self._set_memory_models(
            self.anthropic_intent_memory_model,
            [],
            settings.anthropic_intent_memory_model,
            "Use active Anthropic model",
        )
        self.build123d_enabled.setChecked(settings.build123d_enabled)
        self._refresh_build123d_status()
        self.openscad_enabled.setChecked(settings.openscad_enabled)
        self.openscad_executable.setText(settings.openscad_executable)
        self.openscad_library_paths.setPlainText(settings.openscad_library_paths)
        self._refresh_openscad_status()
        self.api_key.clear()
        self._refresh_status()


class VibeCADDebugPreferencesPage:
    """Preferences for the opt-in exact provider-request debugger."""

    def __init__(self, parent=None):
        from PySide import QtCore, QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADDebugPreferencesPage")
        self.form.setWindowTitle("Debug")
        layout = QtWidgets.QFormLayout(self.form)

        self.enabled = QtWidgets.QCheckBox(self.form)
        self.enabled.setObjectName("VibeCADPrefContextDebugEnabled")
        self.enabled.setToolTip(
            "Capture every exact provider SDK request. Captures contain prompts, "
            "conversation history, tools, CAD context, and encoded images."
        )
        self.enabled.toggled.connect(self._enabled_changed)
        layout.addRow("Context debugger", self.enabled)

        directory_row = QtWidgets.QHBoxLayout()
        self.directory = QtWidgets.QLineEdit(self.form)
        self.directory.setObjectName("VibeCADPrefContextDebugDirectory")
        self.directory.setPlaceholderText(str(default_capture_directory()))
        self.directory.setToolTip(
            "Directory for timestamped JSON request captures. Leave blank to use "
            "the VibeCAD debug directory."
        )
        browse = QtWidgets.QPushButton("Browse", self.form)
        browse.setObjectName("VibeCADPrefBrowseContextDebugDirectory")
        browse.clicked.connect(self._browse_directory)
        directory_row.addWidget(self.directory, 1)
        directory_row.addWidget(browse)
        layout.addRow("Capture directory", directory_row)

        actions = QtWidgets.QHBoxLayout()
        self.open_viewer = QtWidgets.QPushButton("Open Viewer", self.form)
        self.open_viewer.setObjectName("VibeCADPrefOpenContextDebugViewer")
        self.open_viewer.clicked.connect(self._open_viewer)
        open_folder = QtWidgets.QPushButton("Open Folder", self.form)
        open_folder.setObjectName("VibeCADPrefOpenContextDebugFolder")
        open_folder.clicked.connect(self._open_folder)
        actions.addWidget(self.open_viewer)
        actions.addWidget(open_folder)
        actions.addStretch(1)
        layout.addRow("", actions)

        self.status = QtWidgets.QLabel(self.form)
        self.status.setObjectName("VibeCADPrefContextDebugStatus")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.status.setWordWrap(True)
        layout.addRow("Capture status", self.status)

    def _settings(self) -> VibeCADDebugSettings:
        return VibeCADDebugSettings(
            context_debug_enabled=self.enabled.isChecked(),
            capture_directory=self.directory.text().strip(),
        )

    def _enabled_changed(self, enabled: bool) -> None:
        self.open_viewer.setEnabled(bool(enabled))
        self._refresh_status()

    def _refresh_status(self) -> None:
        settings = self._settings()
        state = "enabled" if settings.context_debug_enabled else "disabled"
        self.status.setText(f"{state} | {settings.resolved_capture_directory}")

    def _browse_directory(self) -> None:
        from PySide import QtWidgets

        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self.form,
            "Select provider request capture directory",
            str(self._settings().resolved_capture_directory),
        )
        if selected:
            self.directory.setText(selected)
            self._refresh_status()

    def _open_folder(self) -> None:
        from PySide import QtCore, QtGui

        directory = self._settings().resolved_capture_directory
        directory.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(directory)))

    def _open_viewer(self) -> None:
        if not self.enabled.isChecked():
            return
        save_debug_settings(self._settings())
        import VibeCADGui

        VibeCADGui.show_context_debugger()

    def saveSettings(self) -> None:
        save_debug_settings(self._settings())
        try:
            import VibeCADGui

            VibeCADGui.apply_context_debug_preferences()
        except Exception as exc:
            App.Console.PrintWarning(
                f"VibeCAD context debugger preference update failed: {exc}\n"
            )

    def loadSettings(self) -> None:
        settings = load_debug_settings()
        self.enabled.setChecked(settings.context_debug_enabled)
        self.directory.setText(settings.capture_directory)
        self._enabled_changed(settings.context_debug_enabled)
