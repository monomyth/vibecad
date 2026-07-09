# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD assistant GUI: native dock panel and shared workbench commands.

The panel registers through ``MainWindow.addDockWindow`` (DockWindowManager)
so it gets the same first-class treatment as the Tree and Tasks panels:
the native overlay title bar, overlay-mode eligibility, visibility
persistence, and a View -> Panels entry. No hand-rolled placement code.
"""

from __future__ import annotations

import html
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADCore import get_service
from VibeCADSession import (
    _design_preflight_user_questions_answered,
    _format_document_delta,
    run_prompt,
)
from VibeCADWorkbenchTools import get_tool_pack


COMMANDS = [
    "VibeCAD_AskAI",
    "VibeCAD_ExplainSelection",
    "VibeCAD_OpenAssistant",
    "VibeCAD_OpenPreferences",
    "VibeCAD_AuthStatus",
]

CONTEXT_COMMANDS = [
    "VibeCAD_ExplainSelection",
    "VibeCAD_OpenAssistant",
    "VibeCAD_AskAI",
]

DOCK_NAME = "VibeCADAssistantPanel"

ICON_MARK = "preferences-vibecad.svg"
ICON_OPEN_ASSISTANT = "vibecad-open-assistant.svg"
ICON_SEND = "vibecad-send.svg"
ICON_STOP = "vibecad-stop.svg"
ICON_ACTIVITY = "vibecad-activity.svg"

_commands_registered = False
_preferences_registered = False
_workbench_manipulator = None
_workbench_activation_connected = False
_document_observer_connected = False
_document_observer = None
_document_save_conversations: dict[str, dict[str, Any]] = {}
_document_save_references: dict[str, dict[str, Any]] = {}

_IDLE_STATUS_TEXT = "Ready. Tell VibeCAD what to make or change."


class _AssistantRunController:
    """Single source of truth for the active GUI-launched provider loop."""

    def __init__(self) -> None:
        self._run_id = 0
        self._active = False
        self._cancel_requested = False

    def begin(self) -> int:
        self._run_id += 1
        self._active = True
        self._cancel_requested = False
        return self._run_id

    def request_cancel(self) -> bool:
        if not self._active:
            return False
        self._cancel_requested = True
        return True

    def finish(self, run_id: int) -> None:
        if run_id != self._run_id:
            return
        self._active = False
        self._cancel_requested = False

    def is_cancelled(self, run_id: int) -> bool:
        return run_id != self._run_id or self._cancel_requested

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self._run_id,
            "active": self._active,
            "cancel_requested": self._cancel_requested,
        }


_assistant_run_controller = _AssistantRunController()


class _WorkbenchManipulator:
    """Expose VibeCAD commands in C++-backed workbenches."""

    def modifyMenuBar(self) -> list[dict[str, str]]:
        return [
            {"append": command, "menuItem": "Std_DlgParameter"}
            for command in COMMANDS
        ]

    def modifyToolBars(self) -> list[dict[str, str]]:
        return [
            {"append": command, "toolBar": "File"}
            for command in COMMANDS
        ]


def _print(message: str) -> None:
    App.Console.PrintMessage(f"{message}\n")


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"{message}\n")


def _icon_path(name: str) -> str:
    return str(Path(__file__).resolve().parent / name)


# ---------------------------------------------------------------------------
# Widget lookup helpers
# ---------------------------------------------------------------------------


def _find_dock():
    try:
        from PySide import QtWidgets
    except Exception:
        return None
    main_window = Gui.getMainWindow()
    if main_window is None:
        return None
    return main_window.findChild(QtWidgets.QDockWidget, DOCK_NAME)


def _find_child(widget_type: str, name: str, dock: Any | None = None):
    try:
        from PySide import QtWidgets
    except Exception:
        return None
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return None
    qt_type = getattr(QtWidgets, widget_type, None)
    if qt_type is None:
        return None
    return dock.findChild(qt_type, name)


def _is_assistant_run_active() -> bool:
    return bool(_assistant_run_controller.snapshot()["active"])


def _is_assistant_cancel_requested() -> bool:
    return bool(_assistant_run_controller.snapshot()["cancel_requested"])


def _pump_assistant_ui_events() -> None:
    try:
        from PySide import QtCore, QtWidgets
    except Exception:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    try:
        app.processEvents(QtCore.QEventLoop.AllEvents, 10)
    except TypeError:
        app.processEvents()


# ---------------------------------------------------------------------------
# Conversation rendering
# ---------------------------------------------------------------------------


def _scroll_to_end(edit: Any) -> None:
    try:
        from PySide import QtGui

        edit.moveCursor(QtGui.QTextCursor.End)
    except Exception:
        pass


#: Display width (pixels) for inline conversation thumbnails.
TRANSCRIPT_THUMBNAIL_WIDTH = 160
#: Display width (pixels) for chip tooltip previews.
CHIP_PREVIEW_WIDTH = 256
#: Icon edge (pixels) for reference chip thumbnails.
CHIP_ICON_SIZE = 32


def _image_file_uri(raw_path: str) -> str | None:
    """Return a file:// URI for an existing image file, else None."""
    clean = str(raw_path or "").strip()
    if not clean:
        return None
    try:
        path = Path(clean).expanduser()
        if not path.is_file():
            return None
        return path.resolve().as_uri()
    except (OSError, ValueError):
        return None


def _html_body_fragment(document_html: str) -> str:
    lower = document_html.lower()
    start = lower.find("<body")
    if start < 0:
        return document_html
    start = lower.find(">", start)
    if start < 0:
        return document_html
    end = lower.rfind("</body>")
    if end < 0:
        end = len(document_html)
    return document_html[start + 1 : end]


_MARKDOWN_LIST_MARKER_RE = re.compile(r"^\s{0,3}(?:[-+*]\s+|\d{1,9}[.)]\s+)")


def _normalize_markdown_for_qtext(markdown_text: str) -> str:
    """Add the blank lines Qt Markdown needs before lists in chat prose."""
    lines = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            normalized.append(line)
            continue
        is_list = bool(_MARKDOWN_LIST_MARKER_RE.match(line)) if not in_fence else False
        if is_list and normalized:
            previous = normalized[-1]
            previous_is_list = bool(_MARKDOWN_LIST_MARKER_RE.match(previous))
            if previous.strip() and not previous_is_list:
                normalized.append("")
        normalized.append(line)
    return "\n".join(normalized)


def _markdown_fragment_html(markdown_text: str) -> str:
    normalized_markdown = _normalize_markdown_for_qtext(markdown_text)
    try:
        from PySide import QtGui

        features = (
            QtGui.QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QtGui.QTextDocument.MarkdownFeature.MarkdownNoHTML
        )
        fragment = QtGui.QTextDocumentFragment.fromMarkdown(
            normalized_markdown,
            features,
        )
        return _html_body_fragment(fragment.toHtml())
    except Exception:
        escaped = html.escape(normalized_markdown).replace("\n", "<br/>")
        return f'<p style="white-space:pre-wrap;">{escaped}</p>'


def _split_transcript_role(text: str) -> tuple[str | None, str]:
    raw = str(text or "")
    first, separator, rest = raw.partition("\n")
    if separator and first.endswith(":") and 1 <= len(first) <= 48:
        return first[:-1], rest
    return None, raw


def _transcript_block_html(text: str, image_paths: list[str] | None = None) -> str:
    """Render one conversation turn as markdown-backed HTML plus thumbnails.

    Missing or unreadable image files degrade to text-only output.
    """
    role, body = _split_transcript_role(str(text))
    parts = ['<div style="margin:0 0 10px 0;">']
    if role:
        parts.append(
            '<div style="display:block; margin:0 0 4px 0; font-weight:700;">'
            f"{html.escape(role)}:"
            "</div>"
        )
    parts.append('<div style="display:block; margin:0;">')
    parts.append(_markdown_fragment_html(body))
    parts.append("</div>")
    for raw in image_paths or []:
        uri = _image_file_uri(raw)
        if uri is None:
            continue
        parts.append(
            f'<p style="margin:6px 0 0 0;"><img src="{html.escape(uri, quote=True)}" '
            f'width="{TRANSCRIPT_THUMBNAIL_WIDTH}"/></p>'
        )
    parts.append("</div>")
    return "".join(parts)


def _turn_image_paths(entry: dict[str, Any]) -> list[str]:
    """Extract attached image paths from a persisted conversation turn."""
    metadata = entry.get("metadata")
    if not isinstance(metadata, dict):
        return []
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return []
    paths: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("type") != "image":
            continue
        path = str(attachment.get("path", "")).strip()
        if path:
            paths.append(path)
    return paths


def _append_transcript_block(output: Any, block_html: str) -> None:
    """Append one HTML block, preserving a blank line between turns."""
    if output.toPlainText().strip():
        output.append("")
    output.append(block_html)


def _append_output(text: str, image_paths: list[str] | None = None) -> None:
    output = _find_child("QTextBrowser", "VibeConversation")
    if output is None:
        _print(text)
        return
    _append_transcript_block(output, _transcript_block_html(text, image_paths))
    _scroll_to_end(output)


def _append_thinking(text: str) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    thinking = _find_child("QPlainTextEdit", "VibeThinking")
    if thinking is None:
        return
    current = thinking.toPlainText().strip()
    merged = clean if not current else f"{current}\n\n{clean}"
    thinking.setPlainText(merged)
    thinking.setVisible(True)
    _scroll_to_end(thinking)


def _append_live_delta(text: str) -> None:
    delta = str(text or "")
    if not delta:
        return
    thinking = _find_child("QPlainTextEdit", "VibeThinking")
    if thinking is None:
        return
    from PySide import QtGui

    if not bool(thinking.property("VibeStreamingProviderText")):
        current = thinking.toPlainText().rstrip()
        prefix = "VibeCAD is writing:\n"
        thinking.setPlainText(f"{current}\n\n{prefix}" if current else prefix)
        thinking.setProperty("VibeStreamingProviderText", True)
    cursor = thinking.textCursor()
    cursor.movePosition(QtGui.QTextCursor.End)
    cursor.insertText(delta)
    thinking.setTextCursor(cursor)
    thinking.setVisible(True)
    _scroll_to_end(thinking)


def _clear_thinking(dock: Any | None = None) -> None:
    thinking = _find_child("QPlainTextEdit", "VibeThinking", dock)
    if thinking is None:
        return
    thinking.clear()
    thinking.setProperty("VibeStreamingProviderText", False)
    thinking.setVisible(False)


def _storage_role_for_conversation(role: str) -> str | None:
    return {
        "User": "user",
        "VibeCAD": "assistant",
        "System": "system",
    }.get(str(role))


def _record_conversation_turn(
    role: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    storage_role = _storage_role_for_conversation(role)
    clean = str(text or "").strip()
    if storage_role is None or not clean:
        return
    try:
        get_service().record_conversation_turn(storage_role, clean, metadata=metadata)
    except Exception as exc:
        _warn(f"VibeCAD conversation save failed: {exc}")


def _format_saved_conversation(conversation: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    labels = {
        "user": "User",
        "assistant": "VibeCAD",
        "system": "System",
    }
    for entry in conversation:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        content = str(entry.get("content", "")).strip()
        label = labels.get(role)
        if label is None or not content:
            continue
        lines.append(f"{label}:\n{content}")
    return "\n\n".join(lines)


def _saved_conversation_blocks(conversation: list[dict[str, Any]]) -> list[str]:
    """Render persisted conversation turns as HTML blocks with thumbnails."""
    labels = {
        "user": "User",
        "assistant": "VibeCAD",
        "system": "System",
    }
    blocks: list[str] = []
    for entry in conversation:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        content = str(entry.get("content", "")).strip()
        label = labels.get(role)
        if label is None or not content:
            continue
        blocks.append(
            _transcript_block_html(f"{label}:\n{content}", _turn_image_paths(entry))
        )
    return blocks


def _render_saved_conversation(dock: Any | None = None) -> None:
    if _is_assistant_run_active():
        return
    output = _find_child("QTextBrowser", "VibeConversation", dock)
    if output is None:
        return
    try:
        history = get_service().conversation_history()
    except Exception as exc:
        _warn(f"VibeCAD conversation load failed: {exc}")
        return
    output.clear()
    for block in _saved_conversation_blocks(history.get("conversation", [])):
        _append_transcript_block(output, block)
    output.setProperty("VibeConversationPath", str(history.get("path", "")))
    _scroll_to_end(output)


def _append_conversation(
    role: str,
    text: str,
    *,
    persist: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    if role == "AI thinking":
        _append_thinking(clean)
        return
    image_paths = _turn_image_paths({"metadata": metadata}) if metadata else []
    _append_output(f"{role}:\n{clean}", image_paths)
    if persist:
        _record_conversation_turn(role, clean, metadata=metadata)


def _design_preflight_questions() -> list[dict[str, Any]]:
    try:
        context = get_service().project_context()
    except Exception:
        return []
    preflight = (
        context.get("design_preflight")
        if isinstance(context, dict)
        else None
    )
    if not isinstance(preflight, dict) or preflight.get("status") != "needs_user":
        return []
    if _design_preflight_user_questions_answered(preflight):
        return []
    questions = preflight.get("user_questions")
    if not isinstance(questions, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        options: list[dict[str, str]] = []
        for option in item.get("options") or []:
            if isinstance(option, dict):
                answer = str(option.get("answer") or option.get("value") or "").strip()
                label = str(option.get("label") or option.get("text") or "").strip()
                if not answer:
                    answer = label
                if not label:
                    label = answer
            else:
                label = str(option).strip()
                answer = label
            if label and answer:
                options.append({"label": label, "answer": answer})
        cleaned.append(
            {
                "question": question,
                "default_answer": str(item.get("default_answer") or "").strip(),
                "why_it_matters": str(
                    item.get("why_it_matters") or item.get("why") or ""
                ).strip(),
                "options": options,
            }
        )
    return cleaned


def _clear_layout(layout: Any) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child_layout is not None:
            _clear_layout(child_layout)


def _hide_question_panel(dock: Any | None = None) -> None:
    panel = _find_child("QScrollArea", "VibeQuestionPanel", dock)
    if panel is not None:
        panel.setVisible(False)


def _render_preflight_questions(dock: Any | None = None) -> None:
    try:
        from PySide import QtCore, QtWidgets
    except Exception:
        return
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return
    panel = _find_child("QScrollArea", "VibeQuestionPanel", dock)
    body = _find_child("QWidget", "VibeQuestionList", dock)
    if panel is None or body is None:
        return
    layout = body.layout()
    if layout is None:
        return
    _clear_layout(layout)
    questions = _design_preflight_questions()
    if not questions:
        panel.setVisible(False)
        return

    header = QtWidgets.QLabel("Design questions", body)
    header.setObjectName("VibeQuestionHeader")
    layout.addWidget(header)

    for index, question in enumerate(questions):
        card = QtWidgets.QWidget(body)
        card.setObjectName("VibeQuestionCard")
        card.setProperty("question_text", question["question"])
        card.setProperty("default_answer", question["default_answer"])
        card.setProperty("options", question["options"])
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(6)

        label = QtWidgets.QLabel(question["question"], card)
        label.setObjectName("VibeQuestionText")
        label.setWordWrap(True)
        card_layout.addWidget(label)

        if question["why_it_matters"]:
            why = QtWidgets.QLabel(question["why_it_matters"], card)
            why.setObjectName("VibeQuestionWhy")
            why.setWordWrap(True)
            card_layout.addWidget(why)

        group = QtWidgets.QButtonGroup(card)
        group.setExclusive(True)
        default_answer = question["default_answer"]
        checked = False
        for option_index, option in enumerate(question["options"]):
            label = str(option.get("label") or "").strip()
            answer = str(option.get("answer") or label).strip()
            radio = QtWidgets.QRadioButton(label, card)
            radio.setObjectName(f"VibeQuestionOption_{index}_{option_index}")
            radio.setProperty("answer_text", answer)
            group.addButton(radio)
            if default_answer and (
                answer.casefold() == default_answer.casefold()
                or label.casefold() == default_answer.casefold()
            ):
                radio.setChecked(True)
                checked = True
            card_layout.addWidget(radio)
        if question["options"] and not checked and group.buttons():
            group.buttons()[0].setChecked(True)

        custom = QtWidgets.QLineEdit(card)
        custom.setObjectName(f"VibeQuestionCustom_{index}")
        custom.setPlaceholderText(
            "Custom answer"
            + (f" (default: {default_answer})" if default_answer else "")
        )
        custom.returnPressed.connect(_submit_preflight_answers)
        card_layout.addWidget(custom)
        layout.addWidget(card)

    submit = QtWidgets.QPushButton("Submit Answers", body)
    submit.setObjectName("VibeQuestionSubmit")
    submit.clicked.connect(_submit_preflight_answers)
    layout.addWidget(submit)
    layout.addStretch(1)
    panel.setMinimumHeight(min(280, 72 + len(questions) * 112))
    panel.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    panel.setVisible(True)


def _collect_preflight_answers(dock: Any | None = None) -> list[dict[str, Any]]:
    try:
        from PySide import QtWidgets
    except Exception:
        return []
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return []
    panel = _find_child("QScrollArea", "VibeQuestionPanel", dock)
    if panel is None:
        return []
    answers: list[dict[str, Any]] = []
    for card in panel.findChildren(QtWidgets.QWidget, "VibeQuestionCard"):
        question = str(card.property("question_text") or "").strip()
        default_answer = str(card.property("default_answer") or "").strip()
        options = card.property("options") or []
        custom = card.findChild(QtWidgets.QLineEdit)
        custom_answer = str(custom.text() if custom is not None else "").strip()
        selected = ""
        for radio in card.findChildren(QtWidgets.QRadioButton):
            if radio.isChecked():
                selected = str(radio.property("answer_text") or radio.text()).strip()
                break
        answer = custom_answer or selected or default_answer
        if question and answer:
            answers.append(
                {
                    "question": question,
                    "answer": answer,
                    "source": "custom" if custom_answer else "choice",
                    "options": list(options) if isinstance(options, list) else [],
                    "default_answer": default_answer,
                }
            )
    return answers


def _submit_preflight_answers() -> None:
    dock = _find_dock()
    if dock is None or _is_assistant_run_active():
        return
    answers = _collect_preflight_answers(dock)
    if not answers:
        _set_status_line("Answer at least one design question.", dock=dock)
        return
    try:
        get_service().record_design_preflight_answers(answers)
    except Exception as exc:
        _set_status_line(f"Could not save design answers: {exc}", dock=dock)
        return
    lines = ["Design preflight answers:"]
    for index, item in enumerate(answers, start=1):
        lines.append(f"{index}. {item['question']}\nAnswer: {item['answer']}")
    prompt_box = _find_child("QPlainTextEdit", "VibePrompt", dock)
    if prompt_box is None:
        return
    prompt_box.setPlainText("\n\n".join(lines))
    _hide_question_panel(dock)
    _run_prompt_from_panel()


# ---------------------------------------------------------------------------
# Status + progress rendering
# ---------------------------------------------------------------------------


def _set_status_line(text: str, *, dock: Any | None = None) -> None:
    label = _find_child("QLabel", "VibeStatusLine", dock)
    if label is None:
        return
    clean = str(text or "").strip()
    label.setText(clean)
    label.setVisible(bool(clean) and clean != _IDLE_STATUS_TEXT)


def _format_progress_event(event: dict[str, Any]) -> str:
    name = str(event.get("event", "progress"))
    if name == "context_build_started":
        return "Looking at the current FreeCAD document..."
    if name == "context_build_completed":
        return "I have the document context."
    if name == "design_preflight_started":
        return "Creating accepted design memory..."
    if name == "design_preflight_completed":
        status = str(event.get("status") or "").strip()
        if status == "needs_user":
            return "Design questions are ready."
        if status == "build_ready":
            return "Accepted design memory ready. CAD tools are available."
        return "Accepted design memory updated."
    if name == "provider_subprocess_started":
        return (
            f"{event.get('provider', 'Provider')} process started"
            + (f" | pid {event.get('pid')}" if event.get("pid") else "")
        )
    if name == "provider_waiting":
        return (
            f"Waiting on {event.get('provider', 'provider')} response..."
            f" | idle {float(event.get('idle_seconds', 0) or 0):.1f}s"
            f" | total {float(event.get('elapsed_seconds', 0) or 0):.1f}s"
        )
    if name == "provider_turn_started":
        base = "Thinking about the next CAD move..."
        delta = _format_document_delta(event.get("document_delta"))
        if delta and not delta.startswith("not available"):
            return f"{base} | {delta}"
        return base
    if name == "provider_turn_completed":
        return "CAD step completed."
    if name == "provider_turn_output":
        return f"VibeCAD wrote turn {event.get('turn', '?')}."
    if name == "provider_text_delta":
        return ""
    if name == "provider_turn_failed":
        return (
            f"Provider turn {event.get('turn', '?')} failed: "
            f"{event.get('error', 'unknown error')}"
        )
    if name == "provider_total_timeout":
        return (
            f"Autonomous loop reached {event.get('elapsed_seconds', 0):.1f}s | "
            f"tools: {event.get('tool_count', 0)}"
        )
    if name == "provider_run_cancelled":
        return "Run stopped by user."
    if name == "human_steering_consumed":
        return "Applied your latest correction."
    if name == "tool_workspace_handoff_reached":
        workbench = str(event.get("active_workbench") or "").strip()
        return f"Workspace active: {workbench}" if workbench else "Workspace changed."
    if name == "anthropic_request_dumped":
        return ""
    if name == "anthropic_request_started":
        thinking = event.get("thinking")
        if isinstance(thinking, dict) and thinking.get("budget_tokens"):
            thinking_text = f", thinking {thinking['budget_tokens']} tokens"
        elif isinstance(thinking, dict) and thinking.get("type"):
            thinking_text = f", thinking {thinking['type']}"
        else:
            thinking_text = ""
        return (
            f"Anthropic request sent: turn {event.get('turn', '?')}, "
            f"{event.get('message_count', 0)} messages, "
            f"{event.get('tool_count', 0)} tools{thinking_text}"
        )
    if name == "anthropic_request_retried":
        return "Anthropic request retried with adaptive thinking."
    if name == "anthropic_stream_retrying":
        return (
            f"Anthropic stream interrupted; retry "
            f"{event.get('next_attempt', '?')}/"
            f"{event.get('max_attempts', 3)}."
        )
    if name == "anthropic_stream_waiting":
        return f"Anthropic stream opened: waiting for turn {event.get('turn', '?')}."
    if name == "anthropic_stream_event":
        stream_type = str(event.get("stream_event_type") or "event")
        if stream_type == "content_block_start":
            block = str(event.get("block_type") or "block")
            tool = event.get("tool_name")
            return (
                f"Anthropic stream: started {block}"
                + (f" {tool}" if tool else "")
            )
        if stream_type == "content_block_stop":
            return "Anthropic stream: finished content block."
        if stream_type == "message_delta" and event.get("stop_reason"):
            return f"Anthropic stream: stop reason {event['stop_reason']}."
        if stream_type == "message_stop":
            return "Anthropic stream: message complete."
        if event.get("delta_type"):
            return f"Anthropic stream: receiving {event['delta_type']}."
        return f"Anthropic stream: {stream_type}."
    if name == "anthropic_stream_completed":
        return (
            f"Anthropic stream completed: {event.get('event_count', 0)} events."
        )
    if name == "anthropic_response_received":
        counts = event.get("block_counts")
        if isinstance(counts, dict) and counts:
            blocks = ", ".join(
                f"{key}={value}" for key, value in sorted(counts.items())
            )
        else:
            blocks = "no content blocks"
        tools = event.get("tool_names")
        tool_text = ""
        if isinstance(tools, list) and tools:
            joined = ", ".join(str(tool) for tool in tools[:4])
            remaining = int(event.get("tool_name_count", len(tools)) or len(tools))
            suffix = f" +{remaining - 4}" if remaining > 4 else ""
            tool_text = f" | wants {joined}{suffix}"
        return (
            f"Anthropic response: stop={event.get('stop_reason', 'unknown')}; "
            f"{blocks}{tool_text}"
        )
    if name == "provider_tool_requested":
        arguments = event.get("arguments")
        arg_text = ""
        if isinstance(arguments, dict):
            keys = arguments.get("keys")
            if isinstance(keys, list) and keys:
                arg_text = " | args: " + ", ".join(str(key) for key in keys[:6])
            elif arguments.get("key_count") == 0:
                arg_text = " | args: none"
            elif arguments.get("valid_json") is False:
                arg_text = " | args: invalid JSON"
        return (
            f"{event.get('provider', 'Provider')} requested CAD tool: "
            f"{event.get('tool_name', 'unknown')}{arg_text}"
        )
    if name == "provider_tool_result_sent":
        status = "ok" if event.get("ok") else "blocked"
        detail = f" | {event.get('error')}" if event.get("error") else ""
        return (
            f"Provider received CAD tool result: "
            f"{event.get('tool_name', 'unknown')} {status}{detail}"
        )
    if name == "tool_call_completed":
        status = "ok" if event.get("ok") else "blocked"
        result = event.get("result", {}) if isinstance(event.get("result"), dict) else {}
        if result.get("title"):
            return f"CAD action {status}: {result['title']}"
        if result.get("error"):
            return f"CAD action {status}: {result['error']}"
        return f"CAD action {status}: {event.get('tool_name', 'unknown')}"
    return name.replace("_", " ")


_PROGRESS_THINKING_EVENTS = {
    "context_build_started",
    "context_build_completed",
    "design_preflight_started",
    "design_preflight_completed",
    "provider_waiting",
    "provider_turn_started",
    "provider_turn_completed",
    "provider_tool_requested",
    "tool_call_completed",
    "provider_turn_failed",
    "provider_total_timeout",
    "provider_run_cancelled",
    "human_steering_consumed",
    "anthropic_stream_retrying",
    "tool_workspace_handoff_reached",
}

_PROGRESS_STATUS_ONLY_EVENTS: set[str] = set()


def _progress_event_should_update_status(event: dict[str, Any]) -> bool:
    name = str(event.get("event", "progress"))
    return name in _PROGRESS_STATUS_ONLY_EVENTS


def _progress_event_should_append_thinking(event: dict[str, Any]) -> bool:
    return str(event.get("event", "progress")) in _PROGRESS_THINKING_EVENTS


def _handle_progress_event(
    dock: Any,
    event: dict[str, Any],
) -> None:
    if event.get("event") == "provider_text_delta":
        _append_live_delta(str(event.get("text") or ""))
        _pump_assistant_ui_events()
        return
    text = _format_progress_event(event)
    if not text:
        return
    if _progress_event_should_update_status(event):
        _set_status_line(text, dock=dock)
    if _progress_event_should_append_thinking(event):
        _append_thinking(text)
    if event.get("event") == "design_preflight_completed":
        _render_preflight_questions(dock)
    _pump_assistant_ui_events()


def _set_view_status(summary: dict[str, Any]) -> None:
    status = _find_child("QLabel", "VibeViewStatus")
    if status is None:
        return
    if summary.get("captured"):
        size = summary.get("size") or ["?", "?"]
        text = f"View attached: {size[0]}x{size[1]} | {summary.get('camera_type', 'camera')}"
    elif summary.get("error"):
        text = f"View not attached: {summary['error']}"
    else:
        text = ""
    status.setText(text)
    status.setVisible(bool(text))


def _capture_view_from_panel() -> None:
    summary = get_service().capture_view_screenshot()
    _set_view_status(summary)
    if summary.get("captured"):
        _append_conversation(
            "AI thinking",
            "Attached viewport screenshot: "
            f"{summary.get('size', ['?', '?'])} {summary.get('camera_type', 'camera')}",
        )
    else:
        _append_conversation(
            "VibeCAD",
            f"Viewport screenshot failed: {summary.get('error', 'unknown error')}",
        )


# ---------------------------------------------------------------------------
# Reference images (user-supplied targets)
# ---------------------------------------------------------------------------


def _chip_thumbnail_icon(path: str) -> Any | None:
    """Build a QIcon thumbnail for a reference chip; None when unavailable."""
    try:
        from PySide import QtCore, QtGui
    except Exception:
        return None
    clean = str(path or "").strip()
    if not clean or not Path(clean).expanduser().is_file():
        return None
    try:
        pixmap = QtGui.QPixmap(clean)
        if pixmap.isNull():
            return None
        scaled = pixmap.scaled(
            CHIP_ICON_SIZE,
            CHIP_ICON_SIZE,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        return QtGui.QIcon(scaled)
    except Exception:
        return None


def _chip_tooltip(name: str, path: str) -> str:
    """Tooltip with a larger inline preview; text-only when the file is gone."""
    uri = _image_file_uri(path)
    text = f"Reference image: {html.escape(name)}<br/>Click to remove."
    if uri is None:
        return f"<p>{text}</p>"
    return (
        f'<p>{text}</p>'
        f'<p><img src="{html.escape(uri, quote=True)}" '
        f'width="{CHIP_PREVIEW_WIDTH}"/></p>'
    )


def _refresh_reference_chips(dock: Any | None = None) -> None:
    """Rebuild the removable reference-image chips row from service state."""
    try:
        from PySide import QtCore, QtWidgets
    except Exception:
        return
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return
    row = _find_child("QWidget", "VibeReferenceChips", dock)
    if row is None:
        return
    layout = row.layout()
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
    try:
        summary = get_service().reference_images_summary()
    except Exception as exc:
        _warn(f"VibeCAD reference image summary failed: {exc}")
        summary = {"count": 0, "images": []}
    images = summary.get("images") or []
    for entry in images:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "image"))
        reference_id = str(entry.get("id", ""))
        stored_path = str(entry.get("path", ""))
        chip = QtWidgets.QPushButton(f"{name}  \u2715", row)
        chip.setObjectName(f"VibeReferenceChip_{reference_id}")
        chip.setProperty("VibeReferenceId", reference_id)
        icon = _chip_thumbnail_icon(stored_path)
        if icon is not None:
            chip.setIcon(icon)
            chip.setIconSize(QtCore.QSize(CHIP_ICON_SIZE, CHIP_ICON_SIZE))
        chip.setToolTip(_chip_tooltip(name, stored_path))
        chip.clicked.connect(
            lambda checked=False, rid=reference_id: _remove_reference_from_panel(rid)
        )
        layout.addWidget(chip)
    if images:
        layout.addStretch(1)
    row.setVisible(bool(images))


def _remove_reference_from_panel(reference_id: str) -> None:
    try:
        result = get_service().remove_reference_image(reference_id)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    if result.get("ok"):
        removed = result.get("removed") or {}
        _set_status_line(f"Removed reference image: {removed.get('name', reference_id)}.")
    else:
        _set_status_line(str(result.get("error", "Could not remove reference image.")))
    _refresh_reference_chips()


def _attach_reference_paths(paths: list[str], *, source: str) -> None:
    """Attach each path via the service; report failures without raising."""
    if _is_assistant_run_active():
        _set_status_line("Cannot attach reference images while a run is active.")
        return
    attached = 0
    for path in paths:
        try:
            result = get_service().attach_reference_image(path)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if result.get("ok"):
            attached += 1
            reference = result.get("reference") or {}
            stored_path = str(reference.get("path", "")).strip()
            metadata: dict[str, Any] | None = None
            if stored_path:
                metadata = {
                    "attachments": [
                        {
                            "type": "image",
                            "path": stored_path,
                            "name": str(reference.get("name", "")),
                            "reference_id": str(reference.get("id", "")),
                        }
                    ]
                }
            _append_conversation(
                "System",
                f"Attached reference image: {reference.get('name', path)}",
                persist=True,
                metadata=metadata,
            )
        else:
            _append_conversation(
                "VibeCAD",
                f"Reference image not attached: {result.get('error', 'unknown error')}",
            )
    if attached:
        noun = "image" if attached == 1 else "images"
        _set_status_line(f"Attached {attached} reference {noun} ({source}).")
    _refresh_reference_chips()


def _attach_image_from_panel() -> None:
    try:
        from PySide import QtWidgets
    except Exception:
        return
    if _is_assistant_run_active():
        _set_status_line("Cannot attach reference images while a run is active.")
        return
    dock = _find_dock()
    paths, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
        dock,
        "Attach reference images",
        "",
        "Images (*.png *.jpg *.jpeg *.webp)",
    )
    if not paths:
        return
    _attach_reference_paths([str(path) for path in paths], source="file dialog")


def _paste_clipboard_reference() -> bool:
    """Attach a clipboard image as a reference. True if the clipboard held one."""
    try:
        from PySide import QtWidgets
    except Exception:
        return False
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    clipboard = app.clipboard()
    mime = clipboard.mimeData()
    if mime is None or not mime.hasImage():
        return False
    image = clipboard.image()
    if image is None or image.isNull():
        return False
    if _is_assistant_run_active():
        _set_status_line("Cannot attach reference images while a run is active.")
        return True
    target = Path(tempfile.gettempdir()) / f"vibecad-paste-{uuid.uuid4().hex[:8]}.png"
    try:
        saved = bool(image.save(str(target), "PNG"))
    except Exception as exc:
        _set_status_line(f"Could not save pasted image: {exc}")
        return True
    if not saved:
        _set_status_line("Could not save pasted image.")
        return True
    _attach_reference_paths([str(target)], source="clipboard")
    try:
        target.unlink()
    except OSError:
        pass
    return True


def _install_prompt_paste_filter(prompt: Any) -> None:
    """Intercept Ctrl+V on the prompt box when the clipboard holds an image."""
    try:
        from PySide import QtCore, QtGui
    except Exception:
        return

    class _PasteFilter(QtCore.QObject):
        def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802 (Qt API)
            try:
                if event.type() == QtCore.QEvent.KeyPress and event.matches(
                    QtGui.QKeySequence.Paste
                ):
                    if _paste_clipboard_reference():
                        return True
            except Exception as exc:
                _warn(f"VibeCAD paste handling failed: {exc}")
            return False

    paste_filter = _PasteFilter(prompt)
    prompt.installEventFilter(paste_filter)
    prompt.setProperty("VibePasteFilterInstalled", True)


# ---------------------------------------------------------------------------
# Run / stop / steering
# ---------------------------------------------------------------------------


def _render_assistant_run_state(dock: Any, text: str | None = None) -> None:
    if dock is None:
        return
    busy = _is_assistant_run_active()
    dock.setProperty("VibeRunActive", busy)
    dock.setProperty("VibeCancelRequested", _is_assistant_cancel_requested())

    send_button = _find_child("QPushButton", "VibeSend", dock)
    stop_button = _find_child("QPushButton", "VibeStop", dock)
    prompt_box = _find_child("QPlainTextEdit", "VibePrompt", dock)
    attach_button = _find_child("QPushButton", "VibeAttachView", dock)
    attach_image_button = _find_child("QPushButton", "VibeAttachImage", dock)

    if send_button is not None:
        send_button.setEnabled(True)
        send_button.setText("Steer" if busy else "Send")
    if stop_button is not None:
        stop_button.setEnabled(busy)
    if attach_button is not None:
        attach_button.setEnabled(not busy)
    if attach_image_button is not None:
        attach_image_button.setEnabled(not busy)
    if prompt_box is not None:
        prompt_box.setReadOnly(False)
        prompt_box.setPlaceholderText(
            "Steer the current CAD run..." if busy else "Message VibeCAD..."
        )
    if busy:
        status_text = text or ""
    else:
        status_text = text or _IDLE_STATUS_TEXT
    _set_status_line(status_text, dock=dock)


def _stop_prompt_from_panel() -> None:
    dock = _find_dock()
    if dock is None:
        return
    if not _is_assistant_run_active():
        _render_assistant_run_state(dock)
        return
    _assistant_run_controller.request_cancel()
    _render_assistant_run_state(dock, text="Stopping after the current provider/tool step...")
    _pump_assistant_ui_events()
    _append_conversation("User", "Stop.", persist=True, metadata={"source": "stop"})
    _append_conversation("AI thinking", "Stopping after the current provider/tool step.")


def _run_prompt_from_panel() -> None:
    dock = _find_dock()
    if dock is None:
        return
    prompt_box = _find_child("QPlainTextEdit", "VibePrompt", dock)
    if prompt_box is None:
        return

    prompt = prompt_box.toPlainText().strip()
    if not prompt:
        _set_status_line("Enter a message before sending.", dock=dock)
        return

    service = get_service()
    if _is_assistant_run_active():
        result = service.queue_steering_message(prompt)
        if result.get("ok"):
            prompt_box.clear()
            _append_conversation("User", prompt, persist=True, metadata={"source": "steering"})
            _append_conversation("AI thinking", "Received. I will apply that to the current CAD run.")
        else:
            _append_conversation(
                "VibeCAD",
                result.get("error", "Unable to send correction."),
                persist=True,
                metadata={"source": "steering_error"},
            )
        return

    prefer_online = service.use_online_provider_by_default()
    run_id = _assistant_run_controller.begin()
    _render_assistant_run_state(dock)
    _pump_assistant_ui_events()
    _append_conversation("User", prompt, persist=True, metadata={"source": "prompt"})
    _clear_thinking(dock)
    prompt_box.clear()
    displayed_provider_texts: list[str] = []

    def _cancelled() -> bool:
        return _assistant_run_controller.is_cancelled(run_id)

    def _steering_messages() -> list[str]:
        return [
            str(item.get("text", "")).strip()
            for item in service.consume_steering_messages()
            if str(item.get("text", "")).strip()
        ]

    def _progress(event: dict[str, Any]) -> None:
        nonlocal displayed_provider_texts
        _render_assistant_run_state(dock)
        if event.get("event") == "provider_turn_output":
            text = str(event.get("text") or "").strip()
            if text:
                displayed_provider_texts.append(text)
                _append_conversation("VibeCAD", text)
        _handle_progress_event(dock, event)

    try:
        _pump_assistant_ui_events()
        response = run_prompt(
            prompt,
            service=service,
            prefer_online=prefer_online,
            progress_callback=_progress,
            cancellation_check=_cancelled,
            steering_check=_steering_messages,
        )
        final_text = str(response.final_output or "").strip()
        error = ""
        if response.error and response.error not in final_text:
            error = f"\nProvider note: {response.error}"
        displayed_text = "\n\n".join(displayed_provider_texts).strip()
        undisplayed_tail = ""
        if displayed_text and final_text.startswith(displayed_text):
            undisplayed_tail = final_text[len(displayed_text) :].strip()
        elif not displayed_text:
            undisplayed_tail = final_text
        if undisplayed_tail or error:
            _append_conversation("VibeCAD", f"{undisplayed_tail}{error}".strip())
    except Exception as exc:
        _append_conversation(
            "VibeCAD",
            f"The CAD run failed: {exc}",
            persist=True,
            metadata={"source": "prompt_exception"},
        )
    finally:
        _assistant_run_controller.finish(run_id)
        _clear_thinking(dock)
        _render_assistant_run_state(dock)
        _refresh_view_status(dock)
        _render_preflight_questions(dock)


# ---------------------------------------------------------------------------
# View-status refresh
# ---------------------------------------------------------------------------


def _refresh_view_status(dock: Any | None = None) -> None:
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return
    try:
        _set_view_status(get_service().view_screenshot_summary())
    except Exception as exc:
        _warn(f"VibeCAD view-status refresh failed: {exc}")


# ---------------------------------------------------------------------------
# Document observer: conversation persistence across saves
# ---------------------------------------------------------------------------


def _refresh_assistant_for_document_change() -> None:
    dock = _find_dock()
    if dock is None or not _assistant_panel_is_built(dock):
        return
    _render_saved_conversation(dock)
    _refresh_reference_chips(dock)
    _refresh_view_status(dock)
    _render_assistant_run_state(dock)


def _schedule_assistant_document_refresh() -> None:
    try:
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, _refresh_assistant_for_document_change)
    except Exception:
        _refresh_assistant_for_document_change()


def _snapshot_active_document_conversation(doc: Any) -> None:
    if doc is None:
        return
    try:
        active_doc = App.ActiveDocument
    except Exception:
        active_doc = None
    if active_doc is not doc and getattr(active_doc, "Name", None) != getattr(doc, "Name", None):
        return
    try:
        history = get_service().conversation_history()
    except Exception as exc:
        _warn(f"VibeCAD conversation snapshot failed: {exc}")
        history = {"conversation": []}
    conversation = history.get("conversation", [])
    if isinstance(conversation, list) and conversation:
        _document_save_conversations[str(getattr(doc, "Name", ""))] = {
            "conversation": [
                dict(item) for item in conversation if isinstance(item, dict)
            ],
            # Pre-save location: for a first save this is the unsaved
            # session-keyed project folder; used to relocate (not just copy)
            # the conversation into the saved document's project folder.
            "path": str(history.get("path") or ""),
        }
    try:
        references = get_service().reference_images_summary().get("images", [])
    except Exception as exc:
        _warn(f"VibeCAD reference snapshot failed: {exc}")
        references = []
    if isinstance(references, list) and references:
        _document_save_references[str(getattr(doc, "Name", ""))] = {
            "references": [
                dict(item) for item in references if isinstance(item, dict)
            ],
        }


def _move_saved_document_conversation(doc: Any, filepath: str) -> None:
    doc_name = str(getattr(doc, "Name", ""))
    snapshot = _document_save_conversations.pop(doc_name, None) or {}
    reference_snapshot = _document_save_references.pop(doc_name, None) or {}
    conversation = snapshot.get("conversation") or []
    previous_path = str(snapshot.get("path") or "")
    if not conversation:
        try:
            history = get_service().conversation_history()
            current = history.get("conversation", [])
            conversation = [dict(item) for item in current if isinstance(item, dict)]
            previous_path = str(history.get("path") or "")
        except Exception:
            conversation = []
            previous_path = ""
    if conversation:
        try:
            result = get_service().write_conversation_for_document_file(
                filepath, conversation
            )
        except Exception as exc:
            _warn(f"VibeCAD saved-document conversation write failed: {exc}")
        else:
            _remove_relocated_conversation(previous_path, str(result.get("path") or ""))
    references = reference_snapshot.get("references") or []
    if isinstance(references, list) and references:
        try:
            get_service().write_references_for_document_file(filepath, references)
        except Exception as exc:
            _warn(f"VibeCAD saved-document references write failed: {exc}")


def _remove_relocated_conversation(previous_path: str, new_path: str) -> None:
    """Delete the pre-save conversation file after a successful relocation.

    Only removes a ``conversation.json`` inside the central VibeCAD data
    directory (the unsaved session project folder). Legacy sidecar files are
    never the conversation scope path, so they are never touched. A stale
    copy left behind would be wrongly inherited by the session's next
    unsaved document.
    """
    if not previous_path or not new_path:
        return
    try:
        old = Path(previous_path)
        if old == Path(new_path) or old.name != "conversation.json":
            return
        from VibeCADProject import vibecad_data_dir

        if not old.is_relative_to(vibecad_data_dir()):
            return
        old.unlink(missing_ok=True)
    except Exception as exc:
        _warn(f"VibeCAD conversation relocation cleanup failed: {exc}")


class _VibeCADDocumentObserver:
    def slotCreatedDocument(self, doc) -> None:
        _schedule_assistant_document_refresh()

    def slotActivateDocument(self, doc) -> None:
        _schedule_assistant_document_refresh()

    def slotStartSaveDocument(self, doc, filepath) -> None:
        _snapshot_active_document_conversation(doc)

    def slotFinishSaveDocument(self, doc, filepath) -> None:
        _move_saved_document_conversation(doc, str(filepath))
        _schedule_assistant_document_refresh()

    def slotDeletedDocument(self, doc) -> None:
        _document_save_conversations.pop(str(getattr(doc, "Name", "")), None)
        _document_save_references.pop(str(getattr(doc, "Name", "")), None)
        _schedule_assistant_document_refresh()


def _connect_document_observer() -> None:
    global _document_observer_connected, _document_observer
    if _document_observer_connected:
        return
    try:
        _document_observer = _VibeCADDocumentObserver()
        App.addDocumentObserver(_document_observer)
        _document_observer_connected = True
    except Exception as exc:
        _warn(f"VibeCAD document observer failed: {exc}")


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def _assistant_panel_is_built(dock: Any) -> bool:
    return (
        dock is not None
        and dock.widget() is not None
        and _find_child("QTextBrowser", "VibeConversation", dock) is not None
        and _find_child("QPlainTextEdit", "VibePrompt", dock) is not None
    )


def _build_panel_widget():
    """Build the panel content widget (no dock chrome — that is native now)."""
    from PySide import QtCore, QtGui, QtWidgets

    icon_size = QtCore.QSize(16, 16)

    root = QtWidgets.QWidget()
    root.setObjectName("VibePanelRoot")
    root.setWindowTitle("VibeCAD")
    layout = QtWidgets.QVBoxLayout(root)
    layout.setContentsMargins(10, 8, 10, 10)
    layout.setSpacing(8)

    # --- Conversation ----------------------------------------------------
    conversation = QtWidgets.QTextBrowser(root)
    conversation.setObjectName("VibeConversation")
    conversation.setReadOnly(True)
    conversation.setOpenExternalLinks(False)
    conversation.setOpenLinks(False)
    conversation.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
    conversation.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    conversation.setFrameShape(QtWidgets.QFrame.NoFrame)
    conversation.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Expanding,
    )
    layout.addWidget(conversation, 1)

    # --- Live thinking stream (hidden while idle) ------------------------
    thinking = QtWidgets.QPlainTextEdit(root)
    thinking.setObjectName("VibeThinking")
    thinking.setReadOnly(True)
    thinking.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    thinking.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    thinking.setFrameShape(QtWidgets.QFrame.NoFrame)
    thinking.setMaximumHeight(96)
    thinking.setFocusPolicy(QtCore.Qt.NoFocus)
    thinking.setVisible(False)
    layout.addWidget(thinking)

    # --- Structured design preflight questions (hidden while idle) --------
    question_panel = QtWidgets.QScrollArea(root)
    question_panel.setObjectName("VibeQuestionPanel")
    question_panel.setWidgetResizable(True)
    question_panel.setFrameShape(QtWidgets.QFrame.NoFrame)
    question_panel.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    question_panel.setVisible(False)
    question_body = QtWidgets.QWidget(question_panel)
    question_body.setObjectName("VibeQuestionList")
    question_layout = QtWidgets.QVBoxLayout(question_body)
    question_layout.setContentsMargins(0, 0, 0, 0)
    question_layout.setSpacing(6)
    question_panel.setWidget(question_body)
    layout.addWidget(question_panel)

    # --- Status lines -----------------------------------------------------
    view_status = QtWidgets.QLabel(root)
    view_status.setObjectName("VibeViewStatus")
    view_status.setVisible(False)
    layout.addWidget(view_status)

    status_line = QtWidgets.QLabel(root)
    status_line.setObjectName("VibeStatusLine")
    status_line.setWordWrap(True)
    status_line.setVisible(False)
    layout.addWidget(status_line)

    # --- Composer ----------------------------------------------------------
    composer = QtWidgets.QWidget(root)
    composer.setObjectName("VibeComposer")
    composer_layout = QtWidgets.QVBoxLayout(composer)
    composer_layout.setContentsMargins(0, 0, 0, 0)
    composer_layout.setSpacing(6)

    chips_row = QtWidgets.QWidget(composer)
    chips_row.setObjectName("VibeReferenceChips")
    chips_layout = QtWidgets.QHBoxLayout(chips_row)
    chips_layout.setContentsMargins(0, 0, 0, 0)
    chips_layout.setSpacing(4)
    chips_row.setVisible(False)
    composer_layout.addWidget(chips_row)

    prompt = QtWidgets.QPlainTextEdit(composer)
    prompt.setObjectName("VibePrompt")
    prompt.setPlaceholderText("Message VibeCAD...")
    prompt.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    prompt.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    prompt.setMinimumHeight(56)
    prompt.setMaximumHeight(140)
    _install_prompt_paste_filter(prompt)
    composer_layout.addWidget(prompt)

    composer_buttons = QtWidgets.QWidget(composer)
    composer_buttons.setObjectName("VibeComposerButtons")
    buttons_layout = QtWidgets.QHBoxLayout(composer_buttons)
    buttons_layout.setContentsMargins(0, 0, 0, 0)
    buttons_layout.setSpacing(6)

    attach_button = QtWidgets.QPushButton("Attach View", composer_buttons)
    attach_button.setObjectName("VibeAttachView")
    attach_button.setIcon(QtGui.QIcon(_icon_path(ICON_OPEN_ASSISTANT)))
    attach_button.setIconSize(icon_size)
    attach_button.clicked.connect(_capture_view_from_panel)

    attach_image_button = QtWidgets.QPushButton("Attach Image", composer_buttons)
    attach_image_button.setObjectName("VibeAttachImage")
    attach_image_button.setIcon(QtGui.QIcon(_icon_path(ICON_OPEN_ASSISTANT)))
    attach_image_button.setIconSize(icon_size)
    attach_image_button.setToolTip(
        "Attach a reference image (a picture of the part you want).\n"
        "You can also paste an image into the message box with Ctrl+V."
    )
    attach_image_button.clicked.connect(_attach_image_from_panel)

    send_button = QtWidgets.QPushButton("Send", composer_buttons)
    send_button.setObjectName("VibeSend")
    send_button.setIcon(QtGui.QIcon(_icon_path(ICON_SEND)))
    send_button.setIconSize(icon_size)
    send_button.setDefault(True)
    send_button.clicked.connect(_run_prompt_from_panel)

    stop_button = QtWidgets.QPushButton("Stop", composer_buttons)
    stop_button.setObjectName("VibeStop")
    stop_button.setIcon(QtGui.QIcon(_icon_path(ICON_STOP)))
    stop_button.setIconSize(icon_size)
    stop_button.setEnabled(False)
    stop_button.clicked.connect(_stop_prompt_from_panel)

    buttons_layout.addWidget(attach_button)
    buttons_layout.addWidget(attach_image_button)
    buttons_layout.addStretch(1)
    buttons_layout.addWidget(send_button)
    buttons_layout.addWidget(stop_button)
    composer_layout.addWidget(composer_buttons)

    layout.addWidget(composer)
    return root


def _register_native_dock(widget) -> Any:
    """Register through DockWindowManager for a native dock."""
    main_window = Gui.getMainWindow()
    if main_window is None:
        raise RuntimeError("FreeCAD main window is not available.")
    add_dock_window = getattr(main_window, "addDockWindow", None)
    if not callable(add_dock_window):
        raise RuntimeError(
            "FreeCAD main window does not expose DockWindowManager.addDockWindow."
        )
    return add_dock_window(widget, DOCK_NAME, "right")


def _show_panel(text: str = "") -> None:
    try:
        from PySide import QtWidgets  # noqa: F401 - availability probe
    except Exception:
        _print(text or "VibeCAD assistant panel requires Qt.")
        return

    dock = _find_dock()
    if dock is None or not _assistant_panel_is_built(dock):
        widget = _build_panel_widget()
        if dock is not None:
            # Dock exists (e.g. restored shell) but content is missing: rebuild.
            old = dock.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
            dock.setWidget(widget)
        else:
            try:
                dock = _register_native_dock(widget)
            except Exception as exc:
                message = f"VibeCAD assistant panel could not open: {exc}"
                _warn(message)
                _print(message)
                return
            dock.setMinimumWidth(300)

    dock.show()
    dock.raise_()
    _pump_assistant_ui_events()

    if text:
        output = _find_child("QTextBrowser", "VibeConversation", dock)
        if output is not None:
            output.clear()
            _append_transcript_block(output, _transcript_block_html(text))
            _scroll_to_end(output)
    else:
        _render_saved_conversation(dock)
    _refresh_view_status(dock)
    _refresh_reference_chips(dock)
    _render_preflight_questions(dock)
    _render_assistant_run_state(dock)


def show_assistant_for_active_workbench() -> None:
    _show_panel()


# ---------------------------------------------------------------------------
# Workbench activation
# ---------------------------------------------------------------------------


def _on_workbench_activated(workbench_name: str) -> None:
    if get_tool_pack(str(workbench_name)) is None:
        return
    try:
        from PySide import QtCore
    except Exception:
        return

    def refresh_or_open() -> None:
        dock = _find_dock()
        if dock is not None:
            if _assistant_panel_is_built(dock):
                _refresh_view_status(dock)
            return
        _show_panel()

    QtCore.QTimer.singleShot(0, refresh_or_open)


def _connect_workbench_activation() -> None:
    global _workbench_activation_connected
    if _workbench_activation_connected:
        return
    try:
        main_window = Gui.getMainWindow()
        main_window.workbenchActivated.connect(_on_workbench_activated)
        _workbench_activation_connected = True
    except Exception as exc:
        _warn(f"VibeCAD AI assistant could not watch workbench activation: {exc}")


def _wrap_workbench_activation(workbench: Any) -> None:
    if getattr(workbench, "__VibeCADActivatedWrapped__", False):
        return
    original = getattr(workbench, "Activated", None)

    def _activated_with_vibecad(*args: Any, **kwargs: Any) -> Any:
        result = None
        if callable(original):
            result = original(*args, **kwargs)
        try:
            active = get_service().active_workbench_name()
            if active:
                _on_workbench_activated(active)
        except Exception as exc:
            _warn(f"VibeCAD assistant could not open after workbench activation: {exc}")
        return result

    setattr(workbench, "__VibeCADOriginalActivated__", original)
    setattr(workbench, "Activated", _activated_with_vibecad)
    setattr(workbench, "__VibeCADActivatedWrapped__", True)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class _BaseCommand:
    name = "VibeCAD"
    menu_text = "VibeCAD"
    tooltip = "VibeCAD AI command"
    pixmap = ICON_MARK

    def GetResources(self) -> dict[str, Any]:
        return {
            "Pixmap": self.pixmap,
            "MenuText": self.menu_text,
            "ToolTip": self.tooltip,
        }

    def IsActive(self) -> bool:
        return True


class AskAICommand(_BaseCommand):
    menu_text = "Ask AI"
    tooltip = "Ask VibeCAD in the current workbench context"
    pixmap = ICON_SEND

    def Activated(self) -> None:
        service = get_service()
        response = run_prompt("Summarize the current FreeCAD context.", service=service)
        _show_panel(f"[{response.provider}] {response.final_output}")


class ExplainSelectionCommand(_BaseCommand):
    menu_text = "Explain Selection"
    tooltip = "Explain the current selection using VibeCAD context tools"
    pixmap = ICON_ACTIVITY

    def Activated(self) -> None:
        selection = get_service().selection_summary()
        _show_panel(f"Selection context:\n{selection}")


class OpenAssistantCommand(_BaseCommand):
    menu_text = "Open AI Assistant"
    tooltip = "Open the VibeCAD assistant panel for the active workbench"
    pixmap = ICON_OPEN_ASSISTANT

    def Activated(self) -> None:
        _show_panel()


class OpenPreferencesCommand(_BaseCommand):
    menu_text = "AI Preferences"
    tooltip = "Open VibeCAD preferences"
    pixmap = ICON_MARK

    def Activated(self) -> None:
        ensure_preferences_registered()
        try:
            Gui.showPreferencesByName("VibeCAD", "VibeCAD")
        except Exception:
            try:
                Gui.showPreferences("VibeCAD", 0)
            except Exception as exc:
                _show_panel(f"VibeCAD preferences could not be opened: {exc}")


class AuthStatusCommand(_BaseCommand):
    menu_text = "AI Auth Status"
    tooltip = "Show VibeCAD authentication status"
    pixmap = ICON_ACTIVITY

    def Activated(self) -> None:
        auth = get_service().auth_state()
        source = f" from {auth.source}" if auth.source else ""
        _show_panel(f"VibeCAD auth status: {auth.status.value}{source}\n{auth.message}")


def ensure_preferences_registered() -> None:
    global _preferences_registered
    if _preferences_registered:
        return
    import VibeCADPreferences

    Gui.addIconPath(str(Path(__file__).resolve().parent))
    Gui.addPreferencePage(VibeCADPreferences.VibeCADPreferencesPage, "VibeCAD")
    Gui.addPreferencePage(VibeCADPreferences.VibeCADToolsPreferencesPage, "VibeCAD")
    _preferences_registered = True


def ensure_commands_registered() -> None:
    global _commands_registered, _workbench_manipulator
    ensure_preferences_registered()
    _connect_document_observer()
    if _commands_registered:
        _connect_workbench_activation()
        return
    Gui.addCommand("VibeCAD_AskAI", AskAICommand())
    Gui.addCommand("VibeCAD_ExplainSelection", ExplainSelectionCommand())
    Gui.addCommand("VibeCAD_OpenAssistant", OpenAssistantCommand())
    Gui.addCommand("VibeCAD_OpenPreferences", OpenPreferencesCommand())
    Gui.addCommand("VibeCAD_AuthStatus", AuthStatusCommand())
    _workbench_manipulator = _WorkbenchManipulator()
    Gui.addWorkbenchManipulator(_workbench_manipulator)
    _connect_workbench_activation()
    _commands_registered = True


def register_ai_commands_for_workbench(workbench: Any, workbench_name: str) -> None:
    """Attach shared VibeCAD commands to an existing workbench.

    This does not create or register a VibeCAD workbench. It adds native AI
    affordances to the workbench that called it.
    """

    ensure_commands_registered()
    _wrap_workbench_activation(workbench)
    native_workbench = getattr(workbench, "__Workbench__", None)
    if native_workbench is None:
        return

    try:
        native_workbench.appendToolbar("AI", COMMANDS)
        native_workbench.appendMenu(["AI"], COMMANDS)
        native_workbench.appendContextMenu("VibeCAD", CONTEXT_COMMANDS)
    except Exception as exc:
        _warn(f"VibeCAD could not attach AI UI to {workbench_name}: {exc}")
