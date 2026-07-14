# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human source editor and live-preview controller for scripted PartDesign models."""

from __future__ import annotations

import json
from pathlib import Path
import re
import threading
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADCore import get_service


DOCK_NAME = "VibeCADScriptedModelPanel"
PREVIEW_MARKER = "VibeCADTransientScriptedPreview"
PREVIEW_MODEL_ID = "VibeCADPreviewModelId"
PREVIEW_REVISION = "VibeCADPreviewRevision"
DEBOUNCE_MS = 500

_controller: Any | None = None
_preview_containers: dict[tuple[str, str], str] = {}
_hidden_accepted: dict[tuple[str, str], list[str]] = {}
_refresh_retry_pending = False


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"VibeCAD scripted editor: {message}\n")


def _document_key(doc: Any) -> str:
    return str(getattr(doc, "Uid", "") or getattr(doc, "Name", "") or "")


def _find_dock() -> Any | None:
    from PySide import QtWidgets

    main = Gui.getMainWindow()
    return main.findChild(QtWidgets.QDockWidget, DOCK_NAME) if main is not None else None


def _engine_api(engine: str):
    if engine == "build123d":
        import VibeCADBuild123d as api

        return api
    if engine == "openscad":
        import VibeCADOpenSCAD as api

        return api
    raise RuntimeError("Select build123d or OpenSCAD in the PartDesign engine selector.")


def _model_source_path(engine: str, model: dict[str, Any]) -> Path | None:
    directory = str(model.get("artifact_directory") or "").strip()
    if not directory:
        return None
    return Path(directory) / ("model.py" if engine == "build123d" else "model.scad")


def _add_string_property(obj: Any, name: str) -> None:
    if name not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", name, "VibeCAD Preview")


def _set_shaded_display(obj: Any) -> None:
    modes = list(obj.ViewObject.listDisplayModes())
    if "Shaded" not in modes:
        raise RuntimeError(
            f"Preview object {obj.Name} cannot use Shaded display mode. "
            f"Available modes: {modes}"
        )
    obj.ViewObject.DisplayMode = "Shaded"


def _accepted_objects(doc: Any, engine: str, model_id: str) -> list[Any]:
    property_name = (
        "VibeCADBuild123dModelId" if engine == "build123d" else "VibeCADOpenSCADModelId"
    )
    return [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if property_name in list(getattr(obj, "PropertiesList", []) or [])
        and str(getattr(obj, property_name, "") or "") == model_id
    ]


def _accepted_output_features(doc: Any, model: dict[str, Any]) -> list[Any]:
    """Resolve only manifest-owned output features, never their duplicate Bodies."""
    features: list[Any] = []
    seen: set[str] = set()
    outputs = model.get("outputs")
    if not isinstance(outputs, dict):
        return features
    for item in outputs.values():
        if not isinstance(item, dict):
            continue
        name = str(item.get("feature") or "").strip()
        if not name or name in seen:
            continue
        obj = doc.getObject(name)
        shape = getattr(obj, "Shape", None) if obj is not None else None
        if shape is None or shape.isNull():
            continue
        seen.add(name)
        features.append(obj)
    return features


def _json_merge_patch(before: Any, after: Any) -> Any:
    """Return an RFC 7396-style patch that transforms before into after."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return after
    patch: dict[str, Any] = {}
    for key in before.keys() - after.keys():
        patch[key] = None
    for key, value in after.items():
        if key not in before:
            patch[key] = value
            continue
        old_value = before[key]
        if isinstance(old_value, dict) and isinstance(value, dict):
            nested = _json_merge_patch(old_value, value)
            if nested:
                patch[key] = nested
        elif old_value != value:
            patch[key] = value
    return patch


def _restore_accepted_visibility(doc: Any, model_id: str) -> None:
    key = (_document_key(doc), model_id)
    names = _hidden_accepted.pop(key, [])
    for name in names:
        obj = doc.getObject(name)
        if obj is not None:
            try:
                obj.ViewObject.Visibility = True
            except Exception as exc:
                _warn(f"Could not restore visibility for {name}: {exc}")


def _remove_preview_container(doc: Any, container_name: str) -> None:
    container = doc.getObject(container_name)
    if container is None:
        return
    child_names = [
        str(child.Name)
        for child in list(getattr(container, "Group", []) or [])
    ]
    for child_name in child_names:
        if doc.getObject(child_name) is not None:
            doc.removeObject(child_name)
    if doc.getObject(container_name) is not None:
        doc.removeObject(container_name)


def remove_preview(doc: Any, model_id: str, *, restore_accepted: bool = True) -> None:
    key = (_document_key(doc), model_id)
    object_name = _preview_containers.pop(key, "")
    container = doc.getObject(object_name) if object_name else None
    if container is not None:
        _remove_preview_container(doc, object_name)
        doc.recompute()
    if restore_accepted:
        _restore_accepted_visibility(doc, model_id)


def remove_all_previews(doc: Any | None = None) -> list[dict[str, str]]:
    targets = [doc] if doc is not None else list(getattr(App, "listDocuments", lambda: {})().values())
    removed: list[dict[str, str]] = []
    for current in targets:
        if current is None:
            continue
        previews: list[tuple[str, str]] = []
        for obj in list(getattr(current, "Objects", []) or []):
            if PREVIEW_MARKER not in list(getattr(obj, "PropertiesList", []) or []):
                continue
            previews.append(
                (
                    str(obj.Name),
                    str(getattr(obj, PREVIEW_MODEL_ID, "") or ""),
                )
            )
        for object_name, model_id in previews:
            removed.append({"document": current.Name, "model_id": model_id})
            _remove_preview_container(current, object_name)
            _preview_containers.pop((_document_key(current), model_id), None)
            _restore_accepted_visibility(current, model_id)
        current.recompute()
    return removed


def _show_preview(
    engine: str,
    prepared: dict[str, Any],
    imported: list[dict[str, Any]],
) -> None:
    doc = App.ActiveDocument
    if doc is None or doc.Name != prepared["document_name"]:
        return
    model_id = prepared["model_id"]
    remove_preview(doc, model_id, restore_accepted=True)
    hidden: list[str] = []
    for obj in _accepted_objects(doc, engine, model_id):
        try:
            if obj.ViewObject.Visibility:
                hidden.append(obj.Name)
                obj.ViewObject.Visibility = False
        except Exception as exc:
            _warn(f"Could not hide accepted object {obj.Name}: {exc}")
    _hidden_accepted[(_document_key(doc), model_id)] = hidden
    container = doc.addObject("App::Part", f"VibeCADPreview_{model_id[:8]}")
    container.Label = f"Preview - {prepared['model_name']}"
    for prop in (PREVIEW_MARKER, PREVIEW_MODEL_ID, PREVIEW_REVISION):
        _add_string_property(container, prop)
    setattr(container, PREVIEW_MARKER, "true")
    setattr(container, PREVIEW_MODEL_ID, model_id)
    setattr(container, PREVIEW_REVISION, prepared["revision"])
    for index, item in enumerate(imported, start=1):
        feature = doc.addObject("Part::Feature", f"VibeCADPreviewShape_{model_id[:8]}_{index:03d}")
        feature.Label = f"Preview - {item.get('key') or index}"
        feature.Shape = item["shape"]
        container.addObject(feature)
        try:
            _set_shaded_display(feature)
            feature.ViewObject.ShapeColor = (0.18, 0.68, 0.86)
            feature.ViewObject.LineColor = (0.75, 0.92, 1.0)
            feature.ViewObject.Transparency = 18
        except Exception as exc:
            _warn(f"Could not style preview object {feature.Name}: {exc}")
    doc.recompute()
    _preview_containers[(_document_key(doc), model_id)] = container.Name
    try:
        Gui.activeDocument().activeView().fitAll()
    except Exception as exc:
        _warn(f"Could not frame scripted preview: {exc}")


def _read_scad_project(entry: Path) -> dict[str, str]:
    """Read the main source and project-local include/use graph."""
    root = entry.parent.resolve()
    queue = [entry.resolve()]
    copied: set[Path] = set()
    source_files: dict[str, str] = {}
    include_pattern = re.compile(r"\b(?:include|use)\s*<([^>]+)>")
    while queue:
        source_path = queue.pop(0)
        if source_path in copied:
            continue
        copied.add(source_path)
        try:
            relative = source_path.relative_to(root)
        except ValueError:
            continue
        text = source_path.read_text(encoding="utf-8")
        project_name = "model.scad" if source_path == entry.resolve() else relative.as_posix()
        source_files[project_name] = text
        for match in include_pattern.finditer(text):
            include_path = (source_path.parent / match.group(1)).resolve()
            if root == include_path or root in include_path.parents:
                if include_path.is_file():
                    queue.append(include_path)
    return dict(sorted(source_files.items()))


def _build_widget():
    from PySide import QtCore, QtGui, QtWidgets

    class LineNumberArea(QtWidgets.QWidget):
        def __init__(self, editor):
            super().__init__(editor)
            self.editor = editor

        def sizeHint(self):
            return QtCore.QSize(self.editor.line_number_area_width(), 0)

        def paintEvent(self, event):
            self.editor.paint_line_numbers(event)

    class SourceEditor(QtWidgets.QPlainTextEdit):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.number_area = LineNumberArea(self)
            self.blockCountChanged.connect(self.update_line_number_width)
            self.updateRequest.connect(self.update_line_number_area)
            self.cursorPositionChanged.connect(self.highlight_current_line)
            self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            self.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
            self.update_line_number_width()
            self.highlight_current_line()

        def line_number_area_width(self):
            digits = max(2, len(str(max(1, self.blockCount()))))
            return 10 + self.fontMetrics().horizontalAdvance("9") * digits

        def update_line_number_width(self, _count=0):
            self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

        def update_line_number_area(self, rect, dy):
            if dy:
                self.number_area.scroll(0, dy)
            else:
                self.number_area.update(0, rect.y(), self.number_area.width(), rect.height())
            if rect.contains(self.viewport().rect()):
                self.update_line_number_width()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            rect = self.contentsRect()
            self.number_area.setGeometry(
                QtCore.QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
            )

        def paint_line_numbers(self, event):
            painter = QtGui.QPainter(self.number_area)
            painter.fillRect(event.rect(), self.palette().alternateBase())
            block = self.firstVisibleBlock()
            number = block.blockNumber()
            top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            bottom = top + int(self.blockBoundingRect(block).height())
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    painter.setPen(self.palette().color(QtGui.QPalette.Disabled, QtGui.QPalette.Text))
                    painter.drawText(
                        0,
                        top,
                        self.number_area.width() - 5,
                        self.fontMetrics().height(),
                        QtCore.Qt.AlignRight,
                        str(number + 1),
                    )
                block = block.next()
                top = bottom
                bottom = top + int(self.blockBoundingRect(block).height())
                number += 1

        def highlight_current_line(self):
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format.setBackground(self.palette().alternateBase())
            selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            self.setExtraSelections([selection])

        def goto_line(self, line: int):
            block = self.document().findBlockByNumber(max(0, line - 1))
            if block.isValid():
                cursor = QtGui.QTextCursor(block)
                self.setTextCursor(cursor)
                self.centerCursor()
                self.setFocus()

    class ScriptHighlighter(QtGui.QSyntaxHighlighter):
        def __init__(self, document, engine: str):
            super().__init__(document)
            self.engine = engine
            keyword_color = QtGui.QColor("#65b8ff")
            string_color = QtGui.QColor("#82c995")
            number_color = QtGui.QColor("#f0b86e")
            comment_color = QtGui.QColor("#7f8b96")
            self.rules = []
            keywords = (
                ["module", "function", "include", "use", "for", "if", "else", "let", "each", "true", "false", "undef"]
                if engine == "openscad"
                else ["from", "import", "as", "def", "class", "for", "while", "if", "elif", "else", "return", "assert", "True", "False", "None"]
            )
            for word in keywords:
                expression = QtCore.QRegularExpression(rf"\b{re.escape(word)}\b")
                fmt = QtGui.QTextCharFormat()
                fmt.setForeground(keyword_color)
                fmt.setFontWeight(QtGui.QFont.Bold)
                self.rules.append((expression, fmt))
            comment_pattern = r"//[^\n]*" if engine == "openscad" else r"#[^\n]*"
            for pattern, color in (
                (r"\b(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\b", number_color),
                (r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', string_color),
                (comment_pattern, comment_color),
            ):
                fmt = QtGui.QTextCharFormat()
                fmt.setForeground(color)
                self.rules.append((QtCore.QRegularExpression(pattern), fmt))

        def highlightBlock(self, text):
            for expression, fmt in self.rules:
                iterator = expression.globalMatch(text)
                while iterator.hasNext():
                    match = iterator.next()
                    self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

    class Bridge(QtCore.QObject):
        completed = QtCore.Signal(object)

    root = QtWidgets.QWidget()
    root.setObjectName("VibeScriptedModelRoot")
    root.setWindowTitle("Scripted Model")
    layout = QtWidgets.QVBoxLayout(root)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)

    toolbar = QtWidgets.QWidget(root)
    toolbar.setObjectName("VibeScriptedModelToolbar")
    toolbar_layout = QtWidgets.QHBoxLayout(toolbar)
    toolbar_layout.setContentsMargins(0, 0, 0, 0)
    toolbar_layout.setSpacing(6)
    model_selector = QtWidgets.QComboBox(toolbar)
    model_selector.setObjectName("VibeScriptedModelSelector")
    model_selector.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    toolbar_layout.addWidget(model_selector, 1)
    fidelity_selector = QtWidgets.QComboBox(toolbar)
    fidelity_selector.setObjectName("VibeScriptedFidelitySelector")
    fidelity_selector.setToolTip("OpenSCAD geometry fidelity")
    fidelity_selector.addItem("Exact BREP", "exact_brep")
    fidelity_selector.addItem("Faceted BREP", "faceted_brep")
    toolbar_layout.addWidget(fidelity_selector)
    for text, name, tooltip in (
        ("New", "VibeScriptedNew", "Create a new source-backed model"),
        ("Import", "VibeScriptedImport", "Import an OpenSCAD source project"),
        ("Render", "VibeScriptedRender", "Compile the current working source now"),
        ("Accept", "VibeScriptedAccept", "Accept the current valid preview"),
        ("Revert", "VibeScriptedRevert", "Restore the last accepted source and geometry"),
        ("Export", "VibeScriptedExport", "Export accepted scripted geometry"),
    ):
        button = QtWidgets.QPushButton(text, toolbar)
        button.setObjectName(name)
        button.setToolTip(tooltip)
        toolbar_layout.addWidget(button)
    layout.addWidget(toolbar)

    tabs = QtWidgets.QTabWidget(root)
    tabs.setObjectName("VibeScriptedTabs")
    source_panel = QtWidgets.QWidget(tabs)
    source_panel.setObjectName("VibeScriptedSourcePanel")
    source_layout = QtWidgets.QVBoxLayout(source_panel)
    source_layout.setContentsMargins(0, 0, 0, 0)
    source_layout.setSpacing(4)
    file_selector = QtWidgets.QComboBox(source_panel)
    file_selector.setObjectName("VibeScriptedFileSelector")
    file_selector.setToolTip("OpenSCAD project source file")
    source_layout.addWidget(file_selector)
    source_editor = SourceEditor(source_panel)
    source_editor.setObjectName("VibeScriptedSource")
    source_layout.addWidget(source_editor, 1)
    tabs.addTab(source_panel, "Source")
    parameters_editor = SourceEditor(tabs)
    parameters_editor.setObjectName("VibeScriptedParameters")
    tabs.addTab(parameters_editor, "Parameters")
    layout.addWidget(tabs, 1)

    diagnostics = QtWidgets.QTreeWidget(root)
    diagnostics.setObjectName("VibeScriptedDiagnostics")
    diagnostics.setHeaderLabels(["Severity", "Location", "Message"])
    diagnostics.setRootIsDecorated(False)
    diagnostics.setMaximumHeight(130)
    layout.addWidget(diagnostics)

    status = QtWidgets.QLabel(root)
    status.setObjectName("VibeScriptedStatus")
    status.setWordWrap(True)
    status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    layout.addWidget(status)

    timer = QtCore.QTimer(root)
    timer.setObjectName("VibeScriptedDebounce")
    timer.setSingleShot(True)
    timer.setInterval(DEBOUNCE_MS)
    watcher = QtCore.QFileSystemWatcher(root)
    watcher.setObjectName("VibeScriptedSourceWatcher")
    bridge = Bridge(root)
    root._vibecad_source_highlighter = None
    root._vibecad_parameter_highlighter = ScriptHighlighter(parameters_editor.document(), "build123d")
    root._vibecad_bridge = bridge
    root._vibecad_source_editor_class = SourceEditor
    return root


class ScriptedEditorController:
    def __init__(self, dock: Any):
        from PySide import QtCore, QtWidgets

        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.dock = dock
        self.root = dock.widget()
        self.engine = "native"
        self.model_id = ""
        self.working_revision = ""
        self.accepted_revision = ""
        self.model: dict[str, Any] = {}
        self.source_path: Path | None = None
        self.source_files: dict[str, str] = {}
        self.current_source_file = "model.scad"
        self.loading = False
        self.generation = 0
        self.active_prepared: dict[str, Any] | None = None
        self.active_execution: dict[str, Any] | None = None
        self.active_imported: list[dict[str, Any]] | None = None
        self.active_engine = ""
        self.preview_revision = ""
        self._connect()
        self.refresh()

    def child(self, kind: Any, name: str):
        return self.root.findChild(kind, name)

    @property
    def source(self):
        return self.child(self.QtWidgets.QPlainTextEdit, "VibeScriptedSource")

    @property
    def parameters(self):
        return self.child(self.QtWidgets.QPlainTextEdit, "VibeScriptedParameters")

    @property
    def selector(self):
        return self.child(self.QtWidgets.QComboBox, "VibeScriptedModelSelector")

    @property
    def fidelity_selector(self):
        return self.child(self.QtWidgets.QComboBox, "VibeScriptedFidelitySelector")

    @property
    def file_selector(self):
        return self.child(self.QtWidgets.QComboBox, "VibeScriptedFileSelector")

    @property
    def status(self):
        return self.child(self.QtWidgets.QLabel, "VibeScriptedStatus")

    @property
    def diagnostics(self):
        return self.child(self.QtWidgets.QTreeWidget, "VibeScriptedDiagnostics")

    @property
    def timer(self):
        return self.root.findChild(self.QtCore.QTimer, "VibeScriptedDebounce")

    @property
    def watcher(self):
        return self.root.findChild(self.QtCore.QFileSystemWatcher, "VibeScriptedSourceWatcher")

    def button(self, name: str):
        return self.child(self.QtWidgets.QPushButton, name)

    def _connect(self):
        self.selector.currentIndexChanged.connect(self._select_model)
        self.fidelity_selector.currentIndexChanged.connect(self._fidelity_changed)
        self.file_selector.currentIndexChanged.connect(self._select_source_file)
        self.source.textChanged.connect(self._source_changed)
        self.parameters.textChanged.connect(self._parameters_changed)
        self.timer.timeout.connect(self.render)
        self.watcher.fileChanged.connect(self._external_file_changed)
        self.root._vibecad_bridge.completed.connect(self._preview_completed)
        self.button("VibeScriptedNew").clicked.connect(self.new_model)
        self.button("VibeScriptedImport").clicked.connect(self.import_model)
        self.button("VibeScriptedRender").clicked.connect(self.render)
        self.button("VibeScriptedAccept").clicked.connect(self.accept)
        self.button("VibeScriptedRevert").clicked.connect(self.revert)
        self.button("VibeScriptedExport").clicked.connect(self.export)
        self.diagnostics.itemActivated.connect(self._diagnostic_activated)

    def refresh(self, preferred_model_id: str = ""):
        service = get_service()
        next_engine = service.partdesign_engine()
        if next_engine != self.engine:
            self._cancel_preview(restore_accepted=True)
            self.model_id = ""
            self.model = {}
        self.engine = next_engine
        scripted = self.engine in {"build123d", "openscad"}
        self.root.setEnabled(scripted)
        self.button("VibeScriptedImport").setVisible(self.engine == "openscad")
        self.file_selector.setVisible(self.engine == "openscad")
        self.fidelity_selector.setVisible(self.engine == "openscad")
        if not scripted:
            self.loading = True
            self.selector.clear()
            self.source.clear()
            self.parameters.clear()
            self.file_selector.clear()
            self.source_files = {}
            self.loading = False
            self.status.setText("Select build123d or OpenSCAD as the PartDesign modeling engine.")
            return
        context = service.project_context()
        root = str(context.get("root") or "").strip()
        doc = service._active_document()
        models = _engine_api(self.engine).model_summaries(doc, root) if doc is not None and root else []
        target = preferred_model_id or self.model_id
        self.loading = True
        self.selector.clear()
        for item in models:
            label = str(item.get("label") or item.get("model_id"))
            state = str(item.get("state") or "")
            self.selector.addItem(f"{label}  [{state}]", str(item.get("model_id") or ""))
        index = self.selector.findData(target) if target else (0 if self.selector.count() else -1)
        if index >= 0:
            self.selector.setCurrentIndex(index)
        self.loading = False
        if index >= 0:
            self._load_model(str(self.selector.itemData(index) or ""))
        else:
            self.model_id = ""
            self.source.clear()
            self.parameters.clear()
            self.file_selector.clear()
            self.source_files = {}
            self.status.setText(f"No {self.engine} models in this document. Create or import one.")
        self._update_actions()

    def _select_model(self, index: int):
        if self.loading or index < 0:
            return
        self._load_model(str(self.selector.itemData(index) or ""))

    def _load_model(self, model_id: str):
        if not model_id:
            return
        if self.model_id and model_id != self.model_id:
            self._cancel_preview(restore_accepted=True)
        result = _engine_api(self.engine).inspect_model(get_service(), model_id)
        if not result.get("ok"):
            self._show_failure(result)
            return
        self.model = dict(result["model"])
        self.model_id = model_id
        self.working_revision = str(self.model.get("working_revision") or "")
        self.accepted_revision = str(self.model.get("accepted_revision") or "")
        self.source_path = _model_source_path(self.engine, self.model)
        source_files = self.model.get("source_files")
        if not isinstance(source_files, dict):
            source_files = {
                "model.py" if self.engine == "build123d" else "model.scad": str(
                    self.model.get("source") or ""
                )
            }
        self.source_files = {
            str(path): str(content) for path, content in source_files.items()
        }
        main_name = "model.py" if self.engine == "build123d" else "model.scad"
        self.current_source_file = (
            main_name if main_name in self.source_files else next(iter(self.source_files), main_name)
        )
        self.loading = True
        self.file_selector.clear()
        for path in sorted(self.source_files, key=lambda value: (value != main_name, value)):
            self.file_selector.addItem(path, path)
        selected_file = self.file_selector.findData(self.current_source_file)
        if selected_file >= 0:
            self.file_selector.setCurrentIndex(selected_file)
        self.source.setPlainText(self.source_files.get(self.current_source_file, ""))
        self.parameters.setPlainText(json.dumps(self.model.get("parameters") or {}, indent=2, sort_keys=True))
        if self.engine == "openscad":
            mode_index = self.fidelity_selector.findData(
                str(self.model.get("conversion_mode") or "")
            )
            if mode_index < 0:
                self.loading = False
                self.status.setText("OpenSCAD model has no valid conversion mode.")
                self._update_actions()
                return
            self.fidelity_selector.setCurrentIndex(mode_index)
        self.loading = False
        self._install_highlighter()
        self._watch_source()
        fidelity = str(self.model.get("fidelity") or "not built")
        conversion = str(self.model.get("conversion_mode") or "")
        self.status.setText(
            f"{self.engine} | working {self.working_revision[:10]} | "
            f"accepted {self.accepted_revision[:10] or 'none'} | "
            f"{conversion + ' | ' if conversion else ''}{fidelity}"
        )
        self.diagnostics.clear()
        latest = self.model.get("latest_attempt") or {}
        failure = latest.get("failure") if isinstance(latest, dict) else None
        if isinstance(failure, dict):
            self._populate_diagnostics(failure)
        self._update_actions()

    def _select_source_file(self, index: int):
        if self.loading or index < 0:
            return
        if self.current_source_file:
            self.source_files[self.current_source_file] = self.source.toPlainText()
        target = str(self.file_selector.itemData(index) or "")
        if not target or target == self.current_source_file:
            return
        self.current_source_file = target
        self.loading = True
        self.source.setPlainText(self.source_files.get(target, ""))
        self.loading = False
        self._install_highlighter()

    def _install_highlighter(self):
        from PySide import QtGui

        old = getattr(self.root, "_vibecad_source_highlighter", None)
        if old is not None:
            old.setDocument(None)
        # Reuse the highlighter class already attached to the parameters editor.
        highlighter_class = type(self.root._vibecad_parameter_highlighter)
        self.root._vibecad_source_highlighter = highlighter_class(self.source.document(), self.engine)

    def _watch_source(self):
        for path in self.watcher.files():
            self.watcher.removePath(path)
        if self.source_path is None:
            return
        directory = self.source_path.parent
        for name in self.source_files:
            path = directory / name
            if path.is_file():
                self.watcher.addPath(str(path))

    def _source_changed(self):
        if self.loading or not self.model_id:
            return
        self.source_files[self.current_source_file] = self.source.toPlainText()
        self.status.setText("Working source changed. Rendering preview...")
        self.timer.start()
        self._update_actions()

    def _parameters_changed(self):
        if self.loading or not self.model_id:
            return
        self.status.setText("Parameters changed. Rendering preview...")
        self.timer.start()

    def _fidelity_changed(self, _index: int):
        if self.loading or self.engine != "openscad" or not self.model_id:
            return
        self.status.setText("OpenSCAD conversion mode changed. Rendering preview...")
        self.timer.start()

    def _conversion_mode(self) -> str:
        mode = str(self.fidelity_selector.currentData() or "")
        if mode not in {"exact_brep", "faceted_brep"}:
            raise RuntimeError("Select Exact BREP or Faceted BREP before rendering.")
        return mode

    def _external_file_changed(self, path: str):
        source_path = Path(path)
        if not source_path.is_file():
            return
        try:
            content = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            self.status.setText(f"Could not reload external source change: {exc}")
            return
        if self.source_path is None:
            return
        try:
            relative = source_path.resolve().relative_to(self.source_path.parent.resolve()).as_posix()
        except ValueError:
            return
        previous = self.source_files.get(relative)
        self.source_files[relative] = content
        if content == previous:
            self._watch_source()
            return
        if relative == self.current_source_file and content != self.source.toPlainText():
            cursor_position = self.source.textCursor().position()
            self.loading = True
            self.source.setPlainText(content)
            cursor = self.source.textCursor()
            cursor.setPosition(min(cursor_position, len(content)))
            self.source.setTextCursor(cursor)
            self.loading = False
        self.status.setText(f"External source change detected in {relative}. Rendering preview...")
        self.timer.start()
        self._watch_source()

    def _parse_parameters(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.parameters.toPlainText() or "{}")
        except ValueError as exc:
            self.status.setText(f"Parameters are not valid JSON: {exc}")
            return None
        if not isinstance(value, dict):
            self.status.setText("Parameters must be a JSON object.")
            return None
        return value

    def render(self):
        if not self.model_id or self.engine not in {"build123d", "openscad"}:
            return
        parameters = self._parse_parameters()
        if parameters is None:
            return
        api = _engine_api(self.engine)
        try:
            self.source_files[self.current_source_file] = self.source.toPlainText()
            if self.engine == "openscad":
                source_stage = api.stage_editor_files(
                    get_service(),
                    self.model_id,
                    self.working_revision,
                    self.source_files,
                    self._conversion_mode(),
                )
            else:
                source_stage = api.stage_editor_source(
                    get_service(),
                    self.model_id,
                    self.working_revision,
                    self.source_files.get("model.py", self.source.toPlainText()),
                )
            self.working_revision = str(source_stage["working_revision"])
            if self.engine == "openscad":
                self.model["conversion_mode"] = str(source_stage["conversion_mode"])
            current_parameters = self.model.get("parameters") or {}
            if parameters != current_parameters:
                patch = _json_merge_patch(current_parameters, parameters)
                operation = f"{self.engine}.set_parameters"
                arguments = {
                    "model_id": self.model_id,
                    "expected_revision": self.working_revision,
                    "patch": patch,
                }
                prepared = api.prepare_execution(
                    get_service(),
                    operation,
                    arguments,
                )
            else:
                prepared = api.prepare_execution(
                    get_service(),
                    f"{self.engine}.editor_rebuild",
                    {
                        "model_id": self.model_id,
                        "expected_revision": self.working_revision,
                    },
                )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        self.working_revision = str(prepared["revision"])
        self.model["parameters"] = parameters
        self.generation += 1
        generation = self.generation
        engine = self.engine
        self.status.setText(f"Rendering {self.engine} preview {self.working_revision[:10]}...")
        self.button("VibeScriptedRender").setEnabled(False)

        def work():
            execution = api.execute_prepared(
                prepared,
                cancellation_check=lambda: generation != self.generation,
            )
            self.root._vibecad_bridge.completed.emit(
                {
                    "generation": generation,
                    "engine": engine,
                    "prepared": prepared,
                    "execution": execution,
                }
            )

        threading.Thread(target=work, name="VibeCAD scripted preview", daemon=True).start()

    def _preview_completed(self, event: dict[str, Any]):
        event_engine = str(event.get("engine") or "")
        if (
            int(event.get("generation") or 0) != self.generation
            or event_engine != self.engine
        ):
            _engine_api(event_engine).cleanup_prepared(event["prepared"])
            return
        self.button("VibeScriptedRender").setEnabled(True)
        prepared = event["prepared"]
        execution = event["execution"]
        api = _engine_api(event_engine)
        if not execution.get("ok"):
            try:
                api.record_failed_attempt(prepared, execution)
            except Exception as exc:
                _warn(f"Could not record failed preview: {exc}")
            self._show_failure(execution)
            api.cleanup_prepared(prepared)
            return
        try:
            imported = api.import_validated_outputs(prepared, execution)
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            api.cleanup_prepared(prepared)
            return
        if self.active_prepared is not None:
            _engine_api(self.active_engine).cleanup_prepared(self.active_prepared)
        self.active_prepared = prepared
        self.active_execution = execution
        self.active_imported = imported
        self.active_engine = event_engine
        self.preview_revision = str(prepared["revision"])
        _show_preview(event_engine, prepared, imported)
        fidelity = str(
            execution.get("fidelity")
            or ("exact_brep" if event_engine == "build123d" else "unknown")
        )
        self.status.setText(
            f"Live preview ready | revision {self.preview_revision[:10]} | {fidelity}. "
            "Accepted document geometry is unchanged."
        )
        self.diagnostics.clear()
        self._update_actions()

    def accept(self):
        if (
            self.active_prepared is None
            or self.active_execution is None
            or self.active_imported is None
            or self.preview_revision != self.working_revision
        ):
            self.status.setText("The current working revision has no valid preview to accept.")
            return
        api = _engine_api(self.active_engine)
        doc = App.ActiveDocument
        if doc is not None:
            remove_preview(doc, self.model_id, restore_accepted=True)
        try:
            result = api.commit_outputs(
                get_service(),
                self.active_prepared,
                self.active_execution,
                self.active_imported,
            )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        api.cleanup_prepared(self.active_prepared)
        self.active_prepared = None
        self.active_execution = None
        self.active_imported = None
        self.active_engine = ""
        self.accepted_revision = self.working_revision
        self.preview_revision = ""
        self.status.setText(
            f"Accepted {self.engine} revision {self.accepted_revision[:10]} | "
            f"{result.get('fidelity') or 'exact_brep'}"
        )
        self.refresh(self.model_id)

    def revert(self):
        if not self.model_id:
            return
        self.generation += 1
        api = _engine_api(self.engine)
        try:
            result = api.revert_working_to_accepted(get_service(), self.model_id)
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        if self.active_prepared is not None:
            api.cleanup_prepared(self.active_prepared)
        self.active_prepared = None
        self.active_execution = None
        self.active_imported = None
        self.active_engine = ""
        self.preview_revision = ""
        doc = App.ActiveDocument
        if doc is not None:
            remove_preview(doc, self.model_id, restore_accepted=True)
        self.status.setText(f"Restored accepted revision {result['working_revision'][:10]}.")
        self.refresh(self.model_id)

    def new_model(self):
        name, accepted = self.QtWidgets.QInputDialog.getText(
            self.root, f"New {self.engine} model", "Model name"
        )
        if not accepted or not name.strip():
            return
        if self.engine == "openscad":
            source = (
                "width = 40;\n"
                "depth = 30;\n"
                "height = 12;\n\n"
                "cube([width, depth, height], center = true);\n"
            )
            arguments = {
                "model_name": name.strip(),
                "source": source,
                "parameters": {},
                "conversion_mode": self._conversion_mode(),
            }
        else:
            source = (
                "from build123d import Box\n\n"
                "width = params.get('width', 40.0)\n"
                "depth = params.get('depth', 30.0)\n"
                "height = params.get('height', 12.0)\n"
                "result = {'Part': Box(width, depth, height)}\n"
            )
            arguments = {
                "model_name": name.strip(),
                "source": source,
                "parameters": {"width": 40.0, "depth": 30.0, "height": 12.0},
                "input_objects": {},
                "expected_outputs": ["Part"],
            }
        try:
            prepared = _engine_api(self.engine).prepare_execution(
                get_service(), f"{self.engine}.create_model", arguments
            )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        self.refresh(prepared["model_id"])
        self.working_revision = prepared["revision"]
        self._start_prepared_preview(prepared)

    def _start_prepared_preview(self, prepared: dict[str, Any]):
        engine = self.engine
        api = _engine_api(engine)
        self.generation += 1
        generation = self.generation
        self.status.setText(f"Rendering {self.engine} preview {prepared['revision'][:10]}...")

        def work():
            execution = api.execute_prepared(
                prepared, cancellation_check=lambda: generation != self.generation
            )
            self.root._vibecad_bridge.completed.emit(
                {
                    "generation": generation,
                    "engine": engine,
                    "prepared": prepared,
                    "execution": execution,
                }
            )

        threading.Thread(target=work, name="VibeCAD scripted preview", daemon=True).start()

    def import_model(self):
        if self.engine != "openscad":
            return
        selected, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.root, "Import OpenSCAD source", str(Path.home()), "OpenSCAD (*.scad)"
        )
        if not selected:
            return
        entry = Path(selected)
        try:
            source_files = _read_scad_project(entry)
            source = source_files["model.scad"]
            prepared = _engine_api("openscad").prepare_execution(
                get_service(),
                "openscad.create_model",
                {
                    "model_name": entry.stem,
                    "source": source,
                    "source_files": source_files,
                    "parameters": {},
                    "conversion_mode": self._conversion_mode(),
                },
            )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        self.refresh(prepared["model_id"])
        self.working_revision = prepared["revision"]
        self._start_prepared_preview(prepared)

    def export(self):
        if not self.model_id or not self.accepted_revision:
            self.status.setText("Accept a valid model revision before exporting it.")
            return
        doc = App.ActiveDocument
        if doc is None:
            return
        shaped = _accepted_output_features(doc, self.model)
        if not shaped:
            self.status.setText("The accepted model has no shaped output to export.")
            return
        selected, selected_filter = self.QtWidgets.QFileDialog.getSaveFileName(
            self.root,
            "Export accepted scripted model",
            str(Path.home() / f"{self.model.get('label') or 'model'}.step"),
            "STEP (*.step *.stp);;STL (*.stl);;3MF (*.3mf)",
        )
        if not selected:
            return
        suffix = Path(selected).suffix.lower()
        fidelity = str(self.model.get("fidelity") or "")
        if suffix in {".step", ".stp"} and fidelity in {"faceted_brep", "mixed"}:
            answer = self.QtWidgets.QMessageBox.warning(
                self.root,
                "Faceted STEP export",
                "This accepted model contains tessellated surfaces. The STEP file "
                "will be valid but will not contain fully analytic manufacturing geometry.",
                self.QtWidgets.QMessageBox.Ok | self.QtWidgets.QMessageBox.Cancel,
                self.QtWidgets.QMessageBox.Cancel,
            )
            if answer != self.QtWidgets.QMessageBox.Ok:
                return
        try:
            if suffix in {".step", ".stp"}:
                import Part

                Part.export(shaped, selected)
            elif suffix == ".stl":
                import Mesh

                Mesh.export(shaped, selected)
            elif suffix == ".3mf":
                import Mesh

                Mesh.export(shaped, selected)
            else:
                raise RuntimeError(f"Unsupported export extension: {suffix}")
        except Exception as exc:
            self.status.setText(f"Export failed: {exc}")
            return
        self.status.setText(f"Exported accepted revision to {selected}")

    def _cancel_preview(self, *, restore_accepted: bool) -> None:
        self.generation += 1
        self.timer.stop()
        if self.active_prepared is not None and self.active_engine:
            _engine_api(self.active_engine).cleanup_prepared(self.active_prepared)
        active_model_id = self.model_id
        self.active_prepared = None
        self.active_execution = None
        self.active_imported = None
        self.active_engine = ""
        self.preview_revision = ""
        doc = App.ActiveDocument
        if doc is not None and active_model_id:
            remove_preview(doc, active_model_id, restore_accepted=restore_accepted)

    def _show_failure(self, payload: dict[str, Any]):
        self.status.setText(str(payload.get("error") or "Scripted model operation failed."))
        self._populate_diagnostics(payload)
        self._update_actions()

    def _populate_diagnostics(self, payload: dict[str, Any]):
        self.diagnostics.clear()
        observed = payload.get("observed") if isinstance(payload, dict) else None
        diagnostics = observed.get("diagnostics") if isinstance(observed, dict) else None
        if not isinstance(diagnostics, list):
            diagnostics = []
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict):
                continue
            line = diagnostic.get("line")
            location = f"{diagnostic.get('file') or 'model'}:{line}" if line else str(diagnostic.get("file") or "")
            item = self.QtWidgets.QTreeWidgetItem(
                [
                    str(diagnostic.get("severity") or "error"),
                    location,
                    str(diagnostic.get("message") or ""),
                ]
            )
            item.setData(0, self.QtCore.Qt.UserRole, int(line or 0))
            item.setData(0, int(self.QtCore.Qt.UserRole) + 1, str(diagnostic.get("file") or ""))
            self.diagnostics.addTopLevelItem(item)
        if not diagnostics and payload.get("error"):
            self.diagnostics.addTopLevelItem(
                self.QtWidgets.QTreeWidgetItem(["error", "", str(payload["error"])])
            )
        self.diagnostics.resizeColumnToContents(0)
        self.diagnostics.resizeColumnToContents(1)

    def _diagnostic_activated(self, item: Any, _column: int):
        line = int(item.data(0, self.QtCore.Qt.UserRole) or 0)
        diagnostic_file = str(item.data(0, int(self.QtCore.Qt.UserRole) + 1) or "")
        if diagnostic_file and self.engine == "openscad":
            candidate = Path(diagnostic_file).name
            matches = [
                path
                for path in self.source_files
                if path == diagnostic_file.replace("\\", "/") or Path(path).name == candidate
            ]
            if len(matches) == 1:
                index = self.file_selector.findData(matches[0])
                if index >= 0:
                    self.file_selector.setCurrentIndex(index)
        if line and hasattr(self.source, "goto_line"):
            self.source.goto_line(line)

    def _update_actions(self):
        scripted = self.engine in {"build123d", "openscad"}
        self.button("VibeScriptedNew").setEnabled(scripted)
        self.button("VibeScriptedImport").setEnabled(self.engine == "openscad")
        self.button("VibeScriptedRender").setEnabled(bool(self.model_id))
        self.button("VibeScriptedAccept").setEnabled(
            bool(self.active_prepared)
            and self.preview_revision == self.working_revision
        )
        self.button("VibeScriptedRevert").setEnabled(
            bool(self.model_id and self.accepted_revision)
        )
        self.button("VibeScriptedExport").setEnabled(
            bool(self.model_id and self.accepted_revision)
        )


def _register_dock(widget: Any) -> Any:
    main = Gui.getMainWindow()
    if main is None:
        raise RuntimeError("FreeCAD main window is unavailable.")
    add_dock_window = getattr(main, "addDockWindow", None)
    if not callable(add_dock_window):
        raise RuntimeError("FreeCAD DockWindowManager is unavailable.")
    dock = add_dock_window(widget, DOCK_NAME, "bottom")
    dock.toggleViewAction().setVisible(True)
    return dock


def show_scripted_model_editor() -> None:
    global _controller
    dock = _find_dock()
    if dock is None or dock.widget() is None:
        widget = _build_widget()
        if dock is None:
            dock = _register_dock(widget)
        else:
            dock.setWidget(widget)
        dock.setMinimumWidth(540)
        dock.setMinimumHeight(300)
        _controller = ScriptedEditorController(dock)
    elif _controller is None or _controller.dock is not dock:
        _controller = ScriptedEditorController(dock)
    else:
        _controller.refresh()
    dock.show()
    dock.raise_()


def ensure_scripted_model_editor_registered() -> Any:
    """Create the native dock once so View > Panels can always reopen it."""
    global _controller
    try:
        remove_all_previews()
    except Exception as exc:
        _warn(f"Could not remove stale transient previews: {exc}")
    dock = _find_dock()
    if dock is None or dock.widget() is None:
        widget = _build_widget()
        if dock is None:
            dock = _register_dock(widget)
        else:
            dock.setWidget(widget)
        dock.setMinimumWidth(540)
        dock.setMinimumHeight(300)
        _controller = ScriptedEditorController(dock)
        dock.hide()
    elif _controller is None or _controller.dock is not dock:
        _controller = ScriptedEditorController(dock)
    dock.toggleViewAction().setVisible(True)
    return dock


def refresh_scripted_model_editor() -> None:
    global _refresh_retry_pending
    doc = App.ActiveDocument
    if doc is not None and bool(getattr(doc, "Recomputing", False)):
        if not _refresh_retry_pending:
            from PySide import QtCore

            _refresh_retry_pending = True

            def retry() -> None:
                global _refresh_retry_pending
                _refresh_retry_pending = False
                refresh_scripted_model_editor()

            QtCore.QTimer.singleShot(100, retry)
        return
    _refresh_retry_pending = False
    if doc is not None:
        from VibeCADOpenSCAD import restore_output_display_modes

        restore_output_display_modes(doc)
    if _controller is not None:
        _controller.refresh()


def active_preview_snapshot() -> dict[str, Any] | None:
    if _controller is None or _controller.active_prepared is None:
        return None
    return {
        "engine": _controller.engine,
        "model_id": _controller.model_id,
        "working_revision": _controller.working_revision,
    }


def restore_preview_after_save() -> None:
    if _controller is not None and _controller.model_id:
        _controller.timer.start(0)
