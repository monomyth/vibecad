# SPDX-License-Identifier: LGPL-2.1-or-later

"""Core VibeCAD context and read-only FreeCAD tools."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import time
from typing import Any
import uuid

from VibeCADAuth import (
    AuthState,
    provider_spec,
    resolve_auth_credential,
    resolve_auth_state,
)
from VibeCADPreferences import configured_dotenv_path, load_settings
from VibeCADProject import (
    VibeCADProjectStore,
    project_root_for_document_file,
    vibecad_data_dir,
)
from VibeCADTools import SafetyLevel, ToolRegistry
from VibeCADWorkbenchTools import get_tool_pack, list_tool_packs
from tool_impl import service as service_tools
from tool_impl import sketcher as sketcher_tools


MAX_CONTEXT_OBJECTS = 25
MAX_CONTEXT_COMMANDS = 120
MAX_CONTEXT_WORKBENCH_OBJECTS = 40
MAX_CONVERSATION_TURNS = 40
REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_REFERENCE_IMAGES = 6
REFERENCE_IMAGE_MAX_EDGE = 1568
REFERENCE_IMAGE_MAX_BYTES = 2_000_000
_REFERENCE_IMAGE_MIN_EDGE = 512
_REFERENCE_ENCODE_FORMATS = {
    "jpg": "JPG",
    "jpeg": "JPG",
    "png": "PNG",
    "webp": "WEBP",
}

# Script mode (opt-in preference) swaps the write surface: the script tool
# becomes the only geometry write path and the structured write tools are
# hidden, so the model faces one coherent authoring paradigm at a time.
BUILD_SCRIPT_TOOL_NAME = "model.build_from_script"
# Non-geometry write tools that stay available in script mode because they
# are part of the feedback/reporting loop, not competing authoring paths.
SCRIPT_MODE_ALLOWED_WRITE_TOOLS = {
    BUILD_SCRIPT_TOOL_NAME,
    "core.report_tool_shape_gap",
    "core.update_design_memory",
    "core.undo_last_vibecad_action",
}


def _slug_filename(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return slug[:64] or "reference"


def _load_qt_modules() -> tuple[Any, Any] | None:
    """Return (QtCore, QtGui) via the FreeCAD PySide shim or PySide6, else None."""
    try:
        from PySide import QtCore, QtGui

        return QtCore, QtGui
    except Exception:
        try:
            from PySide6 import QtCore, QtGui

            return QtCore, QtGui
        except Exception:
            return None


def downscale_reference_image(
    path: str | Path,
    max_edge: int = REFERENCE_IMAGE_MAX_EDGE,
    max_bytes: int = REFERENCE_IMAGE_MAX_BYTES,
) -> dict[str, Any]:
    """Downscale/re-encode an image file in place when it exceeds limits.

    Pure function of the file at ``path``: reads it, optionally rewrites it,
    and returns metadata (``size_bytes``, ``image_size``, ``downscaled``,
    ``qt_available``). Gracefully no-ops when Qt bindings are unavailable
    and never raises for bad input.
    """
    target = Path(str(path or "")).expanduser()
    result: dict[str, Any] = {
        "path": str(target),
        "downscaled": False,
        "qt_available": False,
        "size_bytes": None,
        "image_size": None,
    }
    try:
        original_bytes = int(target.stat().st_size)
    except OSError as exc:
        result["error"] = f"Reference image not readable: {exc}"
        return result
    result["size_bytes"] = original_bytes

    qt_modules = _load_qt_modules()
    if qt_modules is None:
        return result
    qt_core, qt_gui = qt_modules
    result["qt_available"] = True

    try:
        image = qt_gui.QImage(str(target))
        if image.isNull():
            result["error"] = "Image could not be decoded for downscaling."
            return result
        width = int(image.width())
        height = int(image.height())
        result["image_size"] = [width, height]
        long_edge = max(width, height)
        if long_edge <= max_edge and original_bytes <= max_bytes:
            return result

        encode_format = _REFERENCE_ENCODE_FORMATS.get(
            target.suffix.lower().lstrip("."), "PNG"
        )
        edge = min(long_edge, max_edge)
        quality = 90
        best_payload: bytes | None = None
        for _attempt in range(8):
            scaled = image
            if max(width, height) > edge:
                scaled = image.scaled(
                    edge,
                    edge,
                    qt_core.Qt.KeepAspectRatio,
                    qt_core.Qt.SmoothTransformation,
                )
            buffer = qt_core.QBuffer()
            buffer.open(qt_core.QIODevice.WriteOnly)
            saved = scaled.save(buffer, encode_format, quality)
            payload = bytes(buffer.data())
            buffer.close()
            if saved and payload:
                if best_payload is None or len(payload) < len(best_payload):
                    best_payload = payload
                if len(payload) <= max_bytes:
                    break
            if encode_format in ("JPG", "WEBP") and quality > 40:
                quality -= 15
            elif edge > _REFERENCE_IMAGE_MIN_EDGE:
                edge = max(_REFERENCE_IMAGE_MIN_EDGE, int(edge * 0.75))
            else:
                break

        if best_payload is None:
            result["error"] = "Image could not be re-encoded for downscaling."
            return result
        if len(best_payload) >= original_bytes and long_edge <= max_edge:
            # Re-encoding gained nothing and dimensions were already fine.
            return result
        target.write_bytes(best_payload)
        final = qt_gui.QImage(str(target))
        result["downscaled"] = True
        result["size_bytes"] = len(best_payload)
        if not final.isNull():
            result["image_size"] = [int(final.width()), int(final.height())]
        return result
    except Exception as exc:
        result["error"] = f"Downscale failed: {exc}"
        return result


def _vibecad_user_data_dir() -> Path:
    """Backward-compatible alias for the central VibeCAD data dir."""
    return vibecad_data_dir()


class VibeCADService:
    """Shared state for existing workbench integrations."""

    def __init__(self, dotenv_path: Path | None = None) -> None:
        self.dotenv_path = dotenv_path
        self._registry = ToolRegistry()
        self._last_view_screenshot: dict[str, Any] | None = None
        self._reference_images: list[dict[str, Any]] = []
        self._reference_cache_key: str | None = None
        self._conversation_cache: list[dict[str, Any]] = []
        self._conversation_cache_key: str | None = None
        self._local_session_id = uuid.uuid4().hex
        self._tool_shape_feedback: list[dict[str, Any]] = []
        self._project_store = VibeCADProjectStore(self._local_session_id)
        self._steering_messages: list[dict[str, Any]] = []
        self._steering_sequence = 0
        self._register_core_tools()

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def auth_state(self) -> AuthState:
        return resolve_auth_state(
            dotenv_path=self._dotenv_path(), provider=self.provider_name()
        )

    def _dotenv_path(self) -> Path | None:
        if self.dotenv_path is not None:
            return self.dotenv_path
        return configured_dotenv_path()

    def provider_name(self) -> str:
        try:
            name = load_settings().provider
            provider_spec(name)
            return name
        except Exception:
            return "openai"

    def provider_api_key(self) -> str | None:
        try:
            credential = resolve_auth_credential(
                dotenv_path=self._dotenv_path(), provider=self.provider_name()
            )
        except Exception:
            return None
        return credential.value if credential is not None else None

    def provider_model(self) -> str:
        try:
            return load_settings().active_model
        except Exception:
            return "gpt-5.5"

    def provider_base_url(self) -> str | None:
        """Base URL override for the selected provider, or None for official."""
        try:
            return load_settings().base_url_for(self.provider_name())
        except Exception:
            return None

    def provider_reasoning_effort(self) -> str:
        try:
            return load_settings().reasoning_effort
        except Exception:
            return "high"

    def use_online_provider_by_default(self) -> bool:
        try:
            return load_settings().use_online_provider
        except Exception:
            return True

    def disabled_workbenches(self) -> set[str]:
        try:
            return set(load_settings().disabled_workbenches)
        except Exception:
            return set()

    def build_script_mode_enabled(self) -> bool:
        """Whether the user opted into script mode (model.build_from_script).

        Script mode is a deliberate, mutually exclusive choice: when enabled,
        the provider authors geometry exclusively through FreeCAD Python
        scripts and the structured write tools are hidden; when disabled
        (the default), only the structured tools are available. Read and
        view tools remain available in both modes.
        """
        try:
            return bool(load_settings().enable_build_script)
        except Exception:
            return False

    def is_workbench_tool_pack_enabled(self, workbench: str | None) -> bool:
        if not workbench:
            return True
        return workbench not in self.disabled_workbenches()

    def is_tool_enabled_for_provider(
        self,
        tool: Any,
        workbench: str | None = None,
    ) -> bool:
        script_mode = self.build_script_mode_enabled()
        if tool.name == BUILD_SCRIPT_TOOL_NAME:
            return script_mode
        if (
            script_mode
            and tool.safety
            in {SafetyLevel.SAFE_WRITE, SafetyLevel.WRITE, SafetyLevel.DESTRUCTIVE}
            and tool.name not in SCRIPT_MODE_ALLOWED_WRITE_TOOLS
        ):
            return False
        active = workbench or self.active_workbench_name()
        if tool.workbench and not self.is_workbench_tool_pack_enabled(tool.workbench):
            return False
        if (
            tool.contextual
            and tool.safety in {SafetyLevel.SAFE_WRITE, SafetyLevel.WRITE}
            and not self.is_workbench_tool_pack_enabled(active)
        ):
            return False
        return True

    def active_workbench_name(self) -> str | None:
        try:
            import FreeCADGui as Gui

            workbench = Gui.activeWorkbench()
            if workbench:
                return workbench.name()
        except Exception:
            return None
        return None

    def document_summary(self) -> dict[str, Any]:
        try:
            import FreeCAD as App
        except Exception:
            return {"document": None, "objects": []}

        doc = App.ActiveDocument
        if doc is None:
            return {"document": None, "objects": []}
        objects = [self._document_object_summary(obj) for obj in doc.Objects]
        visible_objects, bounds = self._bounded_items(objects, MAX_CONTEXT_OBJECTS)
        return {
            "document": doc.Name,
            "label": getattr(doc, "Label", doc.Name),
            "object_count": len(doc.Objects),
            "object_limit": bounds["limit"],
            "objects_truncated": bounds["truncated"],
            "objects_omitted": bounds["omitted"],
            "objects": visible_objects,
        }

    def _active_document(self):
        try:
            import FreeCAD as App

            return App.ActiveDocument
        except Exception:
            return None

    @staticmethod
    def _object_matches_pack(obj: Any, pack: Any) -> bool:
        object_types = getattr(pack, "object_types", ()) if pack else ()
        if not object_types:
            return False
        type_id = getattr(obj, "TypeId", "")
        return any(type_id.startswith(prefix) for prefix in object_types)

    @staticmethod
    def _short_value(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        text = str(value)
        return text if len(text) <= 160 else text[:157] + "..."

    @staticmethod
    def _object_summary(obj: Any) -> dict[str, Any]:
        return {
            "name": obj.Name,
            "label": getattr(obj, "Label", obj.Name),
            "type": obj.TypeId,
        }

    @staticmethod
    def _linked_object_summary(obj: Any) -> dict[str, Any] | None:
        if obj is None or not hasattr(obj, "Name"):
            return None
        return {
            "name": obj.Name,
            "label": getattr(obj, "Label", obj.Name),
            "type": getattr(obj, "TypeId", type(obj).__name__),
        }

    @classmethod
    def _document_object_summary(cls, obj: Any) -> dict[str, Any]:
        item = cls._object_summary(obj)
        placement = cls._placement_summary(getattr(obj, "Placement", None))
        if placement:
            item["placement"] = placement
        for property_name in ("Base", "Tool", "Source", "BaseFeature"):
            try:
                value = getattr(obj, property_name, None)
            except Exception:
                continue
            if isinstance(value, (list, tuple)):
                linked = [
                    summary
                    for summary in (
                        cls._linked_object_summary(child) for child in value[:20]
                    )
                    if summary
                ]
                if linked:
                    item[property_name.lower()] = linked
                continue
            linked = cls._linked_object_summary(value)
            if linked:
                item[property_name.lower()] = linked
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = {
                    "solids": len(getattr(shape, "Solids", [])),
                    "faces": len(getattr(shape, "Faces", [])),
                    "edges": len(getattr(shape, "Edges", [])),
                    "volume": float(getattr(shape, "Volume", 0.0)),
                }
                bound_box = cls._bound_box_summary(getattr(shape, "BoundBox", None))
                if bound_box:
                    item["bound_box"] = bound_box
            except Exception:
                pass
        material = getattr(obj, "ShapeMaterial", None)
        if material is not None:
            material_summary: dict[str, Any] = {
                "name": cls._short_value(getattr(material, "Name", "")),
                "uuid": cls._short_value(getattr(material, "UUID", "")),
            }
            try:
                if material.hasAppearanceProperty("DiffuseColor"):
                    material_summary["diffuse_color"] = cls._short_value(
                        material.getAppearanceValue("DiffuseColor")
                    )
            except Exception:
                pass
            try:
                if material.hasAppearanceProperty("Transparency"):
                    material_summary["transparency"] = float(
                        material.getAppearanceValue("Transparency")
                    )
            except Exception:
                pass
            item["material"] = material_summary
        return item

    @staticmethod
    def _bounded_items(
        items: list[Any], limit: int
    ) -> tuple[list[Any], dict[str, Any]]:
        safe_limit = max(0, int(limit))
        total = len(items)
        visible = items[:safe_limit]
        return visible, {
            "total": total,
            "limit": safe_limit,
            "returned": len(visible),
            "truncated": total > len(visible),
            "omitted": max(0, total - len(visible)),
        }

    def selection_summary(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui

            selection_api = Gui.Selection
        except Exception:
            return {"selection": []}

        selection = []
        try:
            selected_items = selection_api.getSelectionEx()
        except Exception:
            selected_items = []
        for item in selected_items:
            try:
                obj = item.Object
                selection.append(
                    {
                        "object": obj.Name,
                        "label": getattr(obj, "Label", obj.Name),
                        "type": obj.TypeId,
                        "subelements": list(item.SubElementNames),
                    }
                )
            except Exception:
                continue
        return {"selection": selection}

    def view_state(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui

            view = Gui.ActiveDocument.ActiveView
            width, height = view.getSize()
            return {
                "size": [width, height],
                "camera_type": view.getCameraType(),
                "workbench": self.active_workbench_name(),
            }
        except Exception:
            return {
                "size": None,
                "camera_type": None,
                "workbench": self.active_workbench_name(),
            }

    def task_panel_summary(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui
        except Exception as exc:
            return {"available": False, "reason": str(exc), "widgets": []}

        try:
            active_dialog = bool(Gui.Control.activeDialog())
            result: dict[str, Any] = {
                "available": True,
                "active_dialog": active_dialog,
                "widget_count": 0,
                "widgets": [],
            }
            edit_object = None
            edit_reason = ""
            try:
                active_gui_doc = getattr(Gui, "ActiveDocument", None)
                get_in_edit = getattr(active_gui_doc, "getInEdit", None)
                if callable(get_in_edit):
                    edit_object = get_in_edit()
            except Exception as exc:
                edit_reason = str(exc)
            if isinstance(edit_object, (tuple, list)) and edit_object:
                edit_object = edit_object[0]
            provider_object = getattr(edit_object, "Object", None)
            if provider_object is not None:
                edit_object = provider_object
            if edit_object is not None:
                result["edit_mode"] = True
                result["edit_object"] = self._object_summary(edit_object)
                if getattr(edit_object, "TypeId", "") == "Sketcher::SketchObject":
                    result["active_sketch"] = getattr(edit_object, "Name", "")
                    result["profile_status"] = self._sketch_profile_status(edit_object)
                    result["next_actions"] = self._sketch_next_actions(edit_object)
            else:
                result["edit_mode"] = False
                if edit_reason:
                    result["edit_state_reason"] = edit_reason
            return result
        except Exception as exc:
            return {"available": False, "reason": str(exc), "widgets": []}

    def wait_for_user_gui_action(
        self, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        return self._registry.call(
            "core.wait_for_user_gui_action",
            timeout_seconds=timeout_seconds,
        )

    def capture_view_screenshot(self) -> dict[str, Any]:
        return self._registry.call("core.capture_view_screenshot")

    def set_view(
        self,
        orientation: str | None = None,
        fit_all: bool = False,
        show_objects: list[str] | None = None,
        hide_objects: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._registry.call(
            "core.set_view",
            orientation=orientation,
            fit_all=fit_all,
            show_objects=show_objects,
            hide_objects=hide_objects,
        )

    @staticmethod
    def _screenshot_visual_observation(path: Path) -> dict[str, Any]:
        try:
            try:
                from PySide import QtGui
            except Exception:
                from PySide6 import QtGui
            image = QtGui.QImage(str(path))
            if image.isNull():
                return {
                    "available": False,
                    "error": "Screenshot image could not be loaded.",
                }
            width = int(image.width())
            height = int(image.height())
            if width <= 0 or height <= 0:
                return {"available": False, "error": "Screenshot image has no pixels."}

            corner_points = [
                (0, 0),
                (max(0, width - 1), 0),
                (0, max(0, height - 1)),
                (max(0, width - 1), max(0, height - 1)),
            ]
            corners = []
            for x, y in corner_points:
                color = QtGui.QColor(image.pixel(x, y))
                corners.append((color.red(), color.green(), color.blue()))
            background = tuple(
                int(sum(color[index] for color in corners) / len(corners))
                for index in range(3)
            )

            x_steps = min(96, width)
            y_steps = min(96, height)
            sampled = 0
            foreground = 0
            foreground_grid = [[False for _ in range(x_steps)] for _ in range(y_steps)]
            min_x = width
            min_y = height
            max_x = -1
            max_y = -1
            red_total = 0
            green_total = 0
            blue_total = 0
            threshold = 38
            for y_index in range(y_steps):
                y = int(round(y_index * (height - 1) / max(1, y_steps - 1)))
                for x_index in range(x_steps):
                    x = int(round(x_index * (width - 1) / max(1, x_steps - 1)))
                    color = QtGui.QColor(image.pixel(x, y))
                    red = color.red()
                    green = color.green()
                    blue = color.blue()
                    red_total += red
                    green_total += green
                    blue_total += blue
                    sampled += 1
                    distance = (
                        abs(red - background[0])
                        + abs(green - background[1])
                        + abs(blue - background[2])
                    )
                    if distance > threshold:
                        foreground += 1
                        foreground_grid[y_index][x_index] = True
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)

            foreground_ratio = float(foreground / sampled) if sampled else 0.0
            bbox = None
            center_offset = None
            if foreground:
                bbox = [int(min_x), int(min_y), int(max_x), int(max_y)]
                bbox_center_x = (min_x + max_x) / 2.0
                bbox_center_y = (min_y + max_y) / 2.0
                center_offset = [
                    round((bbox_center_x - width / 2.0) / max(1.0, width / 2.0), 3),
                    round((bbox_center_y - height / 2.0) / max(1.0, height / 2.0), 3),
                ]
            bbox_coverage = 0.0
            if bbox:
                bbox_width = max(0, bbox[2] - bbox[0] + 1)
                bbox_height = max(0, bbox[3] - bbox[1] + 1)
                bbox_coverage = (bbox_width * bbox_height) / max(1, width * height)

            visited = [[False for _ in range(x_steps)] for _ in range(y_steps)]
            component_sizes: list[int] = []
            for grid_y in range(y_steps):
                for grid_x in range(x_steps):
                    if visited[grid_y][grid_x] or not foreground_grid[grid_y][grid_x]:
                        continue
                    stack = [(grid_x, grid_y)]
                    visited[grid_y][grid_x] = True
                    component_size = 0
                    while stack:
                        current_x, current_y = stack.pop()
                        component_size += 1
                        for next_x, next_y in (
                            (current_x - 1, current_y),
                            (current_x + 1, current_y),
                            (current_x, current_y - 1),
                            (current_x, current_y + 1),
                        ):
                            if (
                                0 <= next_x < x_steps
                                and 0 <= next_y < y_steps
                                and not visited[next_y][next_x]
                                and foreground_grid[next_y][next_x]
                            ):
                                visited[next_y][next_x] = True
                                stack.append((next_x, next_y))
                    component_sizes.append(component_size)
            component_sizes.sort(reverse=True)
            component_count = len(component_sizes)
            largest_component_ratio = (
                component_sizes[0] / max(1, foreground) if component_sizes else 0.0
            )
            average_rgb = [
                int(red_total / sampled) if sampled else 0,
                int(green_total / sampled) if sampled else 0,
                int(blue_total / sampled) if sampled else 0,
            ]
            mostly_blank = foreground_ratio < 0.003
            attention_flags = []
            if mostly_blank:
                attention_flags.append("mostly_blank")
            elif foreground_ratio < 0.01:
                attention_flags.append("tiny_visible_model")
            if center_offset and (
                abs(float(center_offset[0])) > 0.45
                or abs(float(center_offset[1])) > 0.45
            ):
                attention_flags.append("off_center_model")
            if (
                component_count > 4
                and largest_component_ratio < 0.75
                and foreground_ratio > 0.01
            ):
                attention_flags.append("fragmented_view")
            if bbox and bbox_coverage > 0.92:
                attention_flags.append("model_fills_view_edges")
            layout_summary = (
                "No visible model layout detected."
                if mostly_blank
                else (
                    f"Visible model covers {foreground_ratio:.1%} of sampled pixels, "
                    f"bbox covers {bbox_coverage:.1%} of the viewport, "
                    f"with {component_count} foreground component"
                    f"{'' if component_count == 1 else 's'}."
                )
            )
            return {
                "available": True,
                "image_size": [width, height],
                "sampled_pixels": sampled,
                "background_rgb": list(background),
                "average_rgb": average_rgb,
                "foreground_pixel_ratio": round(foreground_ratio, 5),
                "foreground_bbox": bbox,
                "foreground_bbox_coverage": round(bbox_coverage, 5),
                "foreground_center_offset": center_offset,
                "foreground_component_count": component_count,
                "largest_component_pixel_ratio": round(largest_component_ratio, 5),
                "attention_flags": attention_flags,
                "mostly_blank": mostly_blank,
                "layout_summary": layout_summary,
                "inspection_summary": (
                    "No visible non-background model content detected."
                    if mostly_blank
                    else "Visible non-background model content detected in the viewport screenshot."
                ),
            }
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    def view_screenshot_summary(self) -> dict[str, Any]:
        if self._last_view_screenshot is None:
            return {"captured": False, "path": None}
        return dict(self._last_view_screenshot)

    def _reference_artifact_dir(self) -> Path:
        """Reference-image folder inside the per-document project directory.

        Project roots always live under the central VibeCAD data dir, so
        references are never written next to the CAD file. Without a project
        context the fallback still lands inside ``vibecad_data_dir()``.
        """
        try:
            project_context = self.project_context()
        except Exception:
            project_context = {}
        root = project_context.get("root") if isinstance(project_context, dict) else None
        if root:
            return Path(str(root)).expanduser() / "references"
        return vibecad_data_dir() / "references"

    def _reference_state_path(self) -> Path:
        return self._reference_artifact_dir().parent / "references.json"

    @staticmethod
    def reference_state_path_for_document_file(file_path: str | Path) -> Path:
        return project_root_for_document_file(file_path) / "references.json"

    @staticmethod
    def _reference_artifact_dir_for_document_file(file_path: str | Path) -> Path:
        return project_root_for_document_file(file_path) / "references"

    @staticmethod
    def _clean_reference_entry(entry: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "id",
            "name",
            "label",
            "path",
            "size_bytes",
            "image_size",
            "downscaled",
            "format",
            "artifact_role",
            "attached_at",
            "provider_delivery",
            "visual_brief",
            "visual_brief_updated_at",
            "downscale_error",
        }
        clean = {key: entry.get(key) for key in allowed if key in entry}
        clean["id"] = str(clean.get("id") or uuid.uuid4().hex[:12])
        clean["name"] = str(clean.get("name") or clean.get("id") or "reference")
        clean["label"] = str(clean.get("label") or "").strip()
        clean["path"] = str(clean.get("path") or "").strip()
        clean["artifact_role"] = "user_reference"
        return clean

    def _load_reference_images_for_active_project(self) -> None:
        try:
            path = self._reference_state_path()
        except Exception:
            path = vibecad_data_dir() / "references.json"
        key = str(path)
        if key == self._reference_cache_key:
            return
        loaded: list[dict[str, Any]] = []
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                raw_images = (
                    data.get("reference_images", [])
                    if isinstance(data, dict)
                    else data
                )
                if isinstance(raw_images, list):
                    loaded = [
                        self._clean_reference_entry(item)
                        for item in raw_images
                        if isinstance(item, dict)
                    ][:MAX_REFERENCE_IMAGES]
        except Exception:
            loaded = []
        self._reference_cache_key = key
        self._reference_images = loaded

    def _write_reference_images(self) -> None:
        try:
            path = self._reference_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": "VibeCAD reference images",
                "version": 1,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "reference_images": [
                    self._clean_reference_entry(item)
                    for item in self._reference_images
                    if isinstance(item, dict)
                ][-MAX_REFERENCE_IMAGES:],
            }
            tmp = path.with_name(f"{path.name}.tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            tmp.replace(path)
            self._reference_cache_key = str(path)
        except Exception:
            pass

    @staticmethod
    def _reference_delivery_status(entry: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(entry.get("path") or "").strip()
        if not raw_path:
            return {"available": False, "reason": "reference image path is empty"}
        path = Path(raw_path).expanduser()
        try:
            if not path.is_file():
                return {
                    "available": False,
                    "reason": f"reference image file not found: {path}",
                }
            size = int(path.stat().st_size)
        except OSError as exc:
            return {"available": False, "reason": str(exc)}
        if size <= 0:
            return {"available": False, "reason": "reference image file is empty"}
        if size > REFERENCE_IMAGE_MAX_BYTES:
            return {
                "available": False,
                "reason": (
                    f"reference image is {size} bytes; provider limit is "
                    f"{REFERENCE_IMAGE_MAX_BYTES} bytes"
                ),
                "size_bytes": size,
            }
        return {"available": True, "size_bytes": size}

    def _refresh_reference_delivery_status(self) -> None:
        for entry in self._reference_images:
            if isinstance(entry, dict):
                entry["provider_delivery"] = self._reference_delivery_status(entry)

    def attach_reference_image(self, source_path: str, label: str = "") -> dict[str, Any]:
        """Copy a user-supplied reference image into the project artifact store.

        Returns ``{"ok": True, "reference": {...}}`` on success or a structured
        ``{"ok": False, "error": ...}`` result; never raises for bad input.
        """
        raw = str(source_path or "").strip()
        if not raw:
            return {"ok": False, "error": "Reference image path cannot be empty."}
        self._load_reference_images_for_active_project()
        source = Path(raw).expanduser()
        suffix = source.suffix.lower()
        if suffix not in REFERENCE_IMAGE_EXTENSIONS:
            supported = ", ".join(sorted(ext.lstrip(".") for ext in REFERENCE_IMAGE_EXTENSIONS))
            return {
                "ok": False,
                "error": (
                    f"Unsupported reference image type '{suffix or source.name}'. "
                    f"Supported formats: {supported}."
                ),
            }
        if not source.is_file():
            return {"ok": False, "error": f"Reference image not found: {source}"}
        if len(self._reference_images) >= MAX_REFERENCE_IMAGES:
            return {
                "ok": False,
                "error": (
                    f"At most {MAX_REFERENCE_IMAGES} reference images may be "
                    "attached; remove one first."
                ),
            }
        try:
            target_dir = self._reference_artifact_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            reference_id = uuid.uuid4().hex[:12]
            safe_name = _slug_filename(source.name)
            target = target_dir / f"{reference_id}-{safe_name}"
            shutil.copyfile(source, target)
            size_bytes = target.stat().st_size
        except OSError as exc:
            return {"ok": False, "error": f"Could not copy reference image: {exc}"}
        downscale = downscale_reference_image(target)
        if downscale.get("size_bytes"):
            size_bytes = int(downscale["size_bytes"])
        entry: dict[str, Any] = {
            "id": reference_id,
            "name": source.name,
            "label": str(label or "").strip(),
            "path": str(target),
            "size_bytes": size_bytes,
            "image_size": downscale.get("image_size"),
            "downscaled": bool(downscale.get("downscaled")),
            "format": suffix.lstrip("."),
            "artifact_role": "user_reference",
            "attached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if downscale.get("error"):
            entry["downscale_error"] = str(downscale.get("error"))
        entry["provider_delivery"] = self._reference_delivery_status(entry)
        self._reference_images.append(entry)
        self._write_reference_images()
        return {"ok": True, "reference": dict(entry), "count": len(self._reference_images)}

    def remove_reference_image(self, reference_id: str) -> dict[str, Any]:
        ident = str(reference_id or "").strip()
        if not ident:
            return {"ok": False, "error": "Reference image id cannot be empty."}
        self._load_reference_images_for_active_project()
        for index, entry in enumerate(self._reference_images):
            if entry.get("id") == ident or entry.get("name") == ident:
                removed = self._reference_images.pop(index)
                self._write_reference_images()
                return {
                    "ok": True,
                    "removed": dict(removed),
                    "count": len(self._reference_images),
                }
        return {"ok": False, "error": f"No attached reference image matches '{ident}'."}

    def clear_reference_images(self) -> dict[str, Any]:
        self._load_reference_images_for_active_project()
        cleared = len(self._reference_images)
        self._reference_images = []
        self._write_reference_images()
        return {"ok": True, "cleared": cleared}

    def reference_images_summary(self) -> dict[str, Any]:
        self._load_reference_images_for_active_project()
        self._refresh_reference_delivery_status()
        return {
            "count": len(self._reference_images),
            "images": [dict(entry) for entry in self._reference_images],
        }

    @staticmethod
    def _normalize_reference_visual_brief(raw: dict[str, Any]) -> dict[str, Any]:
        def clean_text(value: Any, limit: int = 160) -> str:
            text = str(value or "").strip()
            return text[:limit]

        def clean_list(value: Any, limit: int = 8) -> list[str]:
            if isinstance(value, str):
                items = [value]
            elif isinstance(value, list):
                items = value
            else:
                items = []
            cleaned = [clean_text(item) for item in items]
            return [item for item in cleaned if item][:limit]

        brief = {
            "object_type": clean_text(raw.get("object_type"), 80),
            "must_preserve": clean_list(raw.get("must_preserve")),
            "counts_patterns": clean_list(raw.get("counts_patterns")),
            "proportion_notes": clean_list(raw.get("proportion_notes")),
            "unknown_dimensions": clean_list(raw.get("unknown_dimensions")),
            "do_not_simplify": clean_list(raw.get("do_not_simplify")),
        }
        summary = clean_text(raw.get("summary"), 240)
        if summary:
            brief["summary"] = summary
        return {key: value for key, value in brief.items() if bool(value)}

    def update_reference_visual_brief(
        self, reference_ids: list[str] | None, brief: dict[str, Any]
    ) -> dict[str, Any]:
        self._load_reference_images_for_active_project()
        normalized = self._normalize_reference_visual_brief(brief)
        if not normalized:
            return {"ok": False, "error": "Reference brief is empty."}
        requested = {
            str(item).strip()
            for item in (reference_ids or [])
            if str(item).strip()
        }
        changed = 0
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for entry in self._reference_images:
            if not isinstance(entry, dict):
                continue
            if requested and str(entry.get("id") or "") not in requested:
                continue
            entry["visual_brief"] = dict(normalized)
            entry["visual_brief_updated_at"] = timestamp
            changed += 1
        if changed:
            self._write_reference_images()
        return {
            "ok": bool(changed),
            "updated": changed,
            "brief": dict(normalized),
        }

    def write_references_for_document_file(
        self, file_path: str | Path, references: list[dict[str, Any]]
    ) -> dict[str, Any]:
        target_dir = self._reference_artifact_dir_for_document_file(file_path)
        target_dir.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, Any]] = []
        for raw_entry in references:
            if not isinstance(raw_entry, dict):
                continue
            entry = self._clean_reference_entry(raw_entry)
            source_path = Path(str(entry.get("path") or "")).expanduser()
            if source_path.is_file() and source_path.parent != target_dir:
                target_name = f"{entry['id']}-{_slug_filename(entry['name'])}"
                target_path = target_dir / target_name
                try:
                    shutil.copyfile(source_path, target_path)
                    entry["path"] = str(target_path)
                    entry["size_bytes"] = target_path.stat().st_size
                except OSError:
                    pass
            entry["provider_delivery"] = self._reference_delivery_status(entry)
            saved.append(entry)
        saved = saved[-MAX_REFERENCE_IMAGES:]
        path = self.reference_state_path_for_document_file(file_path)
        payload = {
            "format": "VibeCAD reference images",
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reference_images": saved,
        }
        tmp = path.with_name(f"{path.name}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        self._reference_images = [dict(item) for item in saved]
        self._reference_cache_key = str(path)
        return {"path": str(path), "count": len(saved), "reference_images": saved}

    def workbench_summary(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui

            workbenches = sorted(Gui.listWorkbenches().keys())
        except Exception:
            workbenches = []
        return {
            "active": self.active_workbench_name(),
            "workbenches": workbenches,
        }

    def command_summary(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui

            commands = sorted(Gui.listCommands())
        except Exception:
            commands = []
        return {
            "active_workbench": self.active_workbench_name(),
            "command_count": len(commands),
            "command_limit": min(MAX_CONTEXT_COMMANDS, len(commands)),
            "commands_truncated": len(commands) > MAX_CONTEXT_COMMANDS,
            "commands_omitted": max(0, len(commands) - MAX_CONTEXT_COMMANDS),
            "commands": commands[:MAX_CONTEXT_COMMANDS],
        }

    def workbench_command_summary(self, workbench: str | None = None) -> dict[str, Any]:
        active = workbench or self.active_workbench_name()
        pack = get_tool_pack(active)
        try:
            import FreeCADGui as Gui

            all_commands = sorted(Gui.listCommands())
        except Exception:
            all_commands = []

        prefixes = pack.command_prefixes if pack else ()
        if prefixes:
            commands = [
                name
                for name in all_commands
                if any(name.startswith(prefix) for prefix in prefixes)
            ]
        else:
            commands = []
        return {
            "active_workbench": active,
            "domain": pack.domain if pack else None,
            "command_prefixes": list(prefixes),
            "command_count": len(commands),
            "command_limit": min(MAX_CONTEXT_COMMANDS, len(commands)),
            "commands_truncated": len(commands) > MAX_CONTEXT_COMMANDS,
            "commands_omitted": max(0, len(commands) - MAX_CONTEXT_COMMANDS),
            "commands": commands[:MAX_CONTEXT_COMMANDS],
        }

    def workbench_tool_pack_summary(
        self, workbench: str | None = None
    ) -> dict[str, Any]:
        active = workbench or self.active_workbench_name()
        pack = get_tool_pack(active)
        summary = pack.summary() if pack else None
        if summary is not None:
            summary["enabled"] = self.is_workbench_tool_pack_enabled(active)
        return {
            "active_workbench": active,
            "tool_pack": summary,
        }

    def all_workbench_tool_packs(self) -> dict[str, Any]:
        disabled = self.disabled_workbenches()
        tool_packs = []
        for summary in list_tool_packs():
            item = dict(summary)
            item["enabled"] = item["workbench"] not in disabled
            tool_packs.append(item)
        return {"tool_packs": tool_packs, "disabled_workbenches": sorted(disabled)}

    def workbench_object_templates(
        self, workbench: str | None = None
    ) -> dict[str, Any]:
        active = workbench or self.active_workbench_name()
        pack = get_tool_pack(active)
        return {
            "active_workbench": active,
            "templates": list(pack.object_templates) if pack else [],
        }

    def workbench_object_summary(self, workbench: str | None = None) -> dict[str, Any]:
        active = workbench or self.active_workbench_name()
        pack = get_tool_pack(active)
        doc = self._active_document()
        if doc is None:
            return {
                "active_workbench": active,
                "document": None,
                "object_count": 0,
                "objects": [],
            }
        objects = []
        if pack:
            objects = [
                self._object_summary(obj)
                for obj in doc.Objects
                if self._object_matches_pack(obj, pack)
            ]
        visible_objects, bounds = self._bounded_items(
            objects,
            MAX_CONTEXT_WORKBENCH_OBJECTS,
        )
        return {
            "active_workbench": active,
            "document": doc.Name,
            "object_count": len(objects),
            "object_limit": bounds["limit"],
            "objects_truncated": bounds["truncated"],
            "objects_omitted": bounds["omitted"],
            "objects": visible_objects,
        }

    def object_property_summary(self, object_name: str) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "found": False,
                "error": "No active document.",
                "object": object_name,
            }
        obj = doc.getObject(object_name)
        if obj is None:
            return {
                "found": False,
                "error": f"Object not found: {object_name}",
                "object": object_name,
            }

        properties = {}
        for property_name in getattr(obj, "PropertiesList", []):
            try:
                properties[property_name] = self._short_value(
                    getattr(obj, property_name)
                )
            except Exception as exc:
                properties[property_name] = f"<unreadable: {exc}>"
        return {
            "found": True,
            "object": self._object_summary(obj),
            "properties": properties,
        }

    def _get_document_object(self, object_name: str | None):
        doc = self._active_document()
        if doc is None or not object_name:
            return None
        obj = doc.getObject(str(object_name))
        if obj is not None:
            return obj
        for candidate in doc.Objects:
            if getattr(candidate, "Label", None) == str(object_name):
                return candidate
        return None

    def _sketch_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
        ]

    def _get_sketch(self, sketch_name: str | None = None):
        sketches = self._sketch_objects()
        if sketch_name:
            for sketch in sketches:
                if (
                    sketch.Name == sketch_name
                    or getattr(sketch, "Label", None) == sketch_name
                ):
                    return sketch
            return None
        return sketches[0] if sketches else None

    @staticmethod
    def _geometry_construction_state(
        geometry: Any,
        index: int,
        sketch: Any | None = None,
    ) -> bool:
        if sketch is not None:
            try:
                return bool(sketch.getConstruction(index))
            except Exception:
                pass
        return bool(getattr(geometry, "Construction", False))

    @staticmethod
    def _geometry_summary(
        geometry: Any,
        index: int,
        sketch: Any | None = None,
    ) -> dict[str, Any]:
        item = {
            "index": index,
            "handle": f"geometry:{index}",
            "type": geometry.__class__.__name__,
            "construction": VibeCADService._geometry_construction_state(
                geometry,
                index,
                sketch,
            ),
        }
        start = getattr(geometry, "StartPoint", None)
        end = getattr(geometry, "EndPoint", None)
        center = getattr(geometry, "Center", None)
        radius = getattr(geometry, "Radius", None)
        major_radius = getattr(geometry, "MajorRadius", None)
        minor_radius = getattr(geometry, "MinorRadius", None)
        if start is not None:
            item["start"] = [float(start.x), float(start.y), float(start.z)]
        if end is not None:
            item["end"] = [float(end.x), float(end.y), float(end.z)]
        if center is not None:
            item["center"] = [float(center.x), float(center.y), float(center.z)]
        if radius is not None:
            item["radius"] = float(radius)
            if abs(float(radius)) <= 1e-9:
                item["degenerate"] = True
                item["degenerate_reason"] = "zero_radius"
        if major_radius is not None:
            item["major_radius"] = float(major_radius)
        if minor_radius is not None:
            item["minor_radius"] = float(minor_radius)
        poles = None
        if hasattr(geometry, "getPoles"):
            try:
                poles = list(geometry.getPoles())
            except Exception:
                poles = None
        elif hasattr(geometry, "Poles"):
            try:
                poles = list(getattr(geometry, "Poles"))
            except Exception:
                poles = None
        if poles:
            item["pole_count"] = len(poles)
            item["poles"] = [
                [float(point.x), float(point.y), float(point.z)]
                for point in list(poles)[:20]
            ]
        if hasattr(geometry, "Degree"):
            try:
                item["degree"] = int(getattr(geometry, "Degree"))
            except Exception:
                pass
        for method_name, output_key in (
            ("getKnots", "knots"),
            ("getMultiplicities", "multiplicities"),
            ("getWeights", "weights"),
        ):
            if not hasattr(geometry, method_name):
                continue
            try:
                values = list(getattr(geometry, method_name)())
            except Exception:
                continue
            item[output_key] = [float(value) for value in values[:20]]
            item[f"{output_key}_count"] = len(values)
        for method_name, output_key in (
            ("isPeriodic", "periodic"),
            ("isRational", "rational"),
        ):
            if not hasattr(geometry, method_name):
                continue
            try:
                item[output_key] = bool(getattr(geometry, method_name)())
            except Exception:
                pass
        if sketch is not None and hasattr(sketch, "detectDegeneratedGeometries"):
            try:
                degenerate_count = int(sketch.detectDegeneratedGeometries(index))
            except Exception:
                degenerate_count = 0
            item["internal_degenerate_geometry_count"] = degenerate_count
            if degenerate_count > 0:
                item["degenerate"] = True
        return item

    @staticmethod
    def _constraint_summary(constraint: Any, index: int) -> dict[str, Any]:
        item = {
            "index": index,
            "handle": f"constraint:{index}",
            "type": getattr(constraint, "Type", constraint.__class__.__name__),
            "name": getattr(constraint, "Name", ""),
            "driving": getattr(
                constraint, "Driving", getattr(constraint, "isDriving", None)
            ),
        }
        for attribute in (
            "First",
            "FirstPos",
            "Second",
            "SecondPos",
            "Third",
            "ThirdPos",
            "Value",
        ):
            if hasattr(constraint, attribute):
                try:
                    item[attribute.lower()] = getattr(constraint, attribute)
                except Exception:
                    continue
        return item

    def sketcher_summary(self, sketch_name: str | None = None) -> dict[str, Any]:
        sketch = self._get_sketch(sketch_name)
        sketches = self._sketch_objects()
        if sketch is None:
            return {
                "found": False,
                "requested": sketch_name,
                "sketch_count": len(sketches),
                "sketches": [self._object_summary(item) for item in sketches],
            }
        geometry = list(getattr(sketch, "Geometry", []))
        constraints = list(getattr(sketch, "Constraints", []))
        expressions = {}
        try:
            expressions = {}
            for path, expression in sketch.ExpressionEngine:
                raw_path = str(path)
                expressions[raw_path] = str(expression)
                expressions[raw_path.lstrip(".")] = str(expression)
        except Exception:
            expressions = {}
        constraint_summaries = [
            self._constraint_summary(item, index)
            for index, item in enumerate(constraints[:80])
        ]
        for item in constraint_summaries:
            index = item["index"]
            name = item.get("name")
            expression = expressions.get(f"Constraints[{index}]") or expressions.get(
                f".Constraints[{index}]"
            )
            if name:
                expression = (
                    expressions.get(f"Constraints.{name}")
                    or expressions.get(f".Constraints.{name}")
                    or expression
                )
            if expression is not None:
                item["expression"] = expression
        internal_geometry = self._sketch_internal_geometry_summary(sketch, geometry)
        return {
            "found": True,
            "sketch": self._object_summary(sketch),
            "geometry_count": len(geometry),
            "constraint_count": len(constraints),
            "geometry": [
                self._geometry_summary(item, index, sketch)
                for index, item in enumerate(geometry[:50])
            ],
            "constraints": constraint_summaries,
            "internal_geometry": internal_geometry,
            "profile_status": self._sketch_profile_status(sketch),
            "next_actions": self._sketch_next_actions(sketch),
        }

    @staticmethod
    def _sketch_internal_geometry_summary(
        sketch: Any,
        geometry: list[Any] | None = None,
    ) -> dict[str, Any]:
        geometry = list(geometry or getattr(sketch, "Geometry", []) or [])
        try:
            facades = list(getattr(sketch, "GeometryFacadeList", []) or [])
        except Exception:
            facades = []
        dependent_parameters: list[dict[str, Any]] = []
        if hasattr(sketch, "getGeometryWithDependentParameters"):
            try:
                raw_dependencies = list(sketch.getGeometryWithDependentParameters() or [])
            except Exception:
                raw_dependencies = []
            for item in raw_dependencies[:80]:
                try:
                    dependent_parameters.append(
                        {
                            "geometry_index": int(item[0]),
                            "parameter_index": int(item[1]),
                        }
                    )
                except Exception:
                    dependent_parameters.append({"raw": str(item)})
        degenerate_geometry: list[dict[str, Any]] = []
        if hasattr(sketch, "detectDegeneratedGeometries"):
            for index in range(len(geometry)):
                try:
                    count = int(sketch.detectDegeneratedGeometries(index))
                except Exception:
                    continue
                if count > 0:
                    degenerate_geometry.append(
                        {
                            "geometry_index": index,
                            "geometry_handle": f"geometry:{index}",
                            "type": geometry[index].__class__.__name__,
                            "degenerate_count": count,
                        }
                    )
        return {
            "geometry_count": len(geometry),
            "geometry_facade_count": len(facades),
            "dependent_parameter_count": len(dependent_parameters),
            "dependent_parameters": dependent_parameters,
            "degenerate_geometry_count": len(degenerate_geometry),
            "degenerate_geometry": degenerate_geometry,
            "has_internal_or_dependent_geometry": bool(
                len(facades) != len(geometry)
                or dependent_parameters
                or degenerate_geometry
            ),
            "note": (
                "Read-only internal Sketcher metadata. VibeCAD does not expose or delete "
                "internal geometry during inspection."
            ),
        }

    def _sketch_profile_status(self, sketch: Any | None) -> dict[str, Any]:
        if sketch is None:
            return {
                "found": False,
                "ready_for_pad": False,
                "reason": "No active sketch.",
            }
        geometry = list(getattr(sketch, "Geometry", []) or [])
        constraints = list(getattr(sketch, "Constraints", []) or [])
        try:
            degrees_of_freedom = int(getattr(sketch, "DoF"))
        except Exception:
            degrees_of_freedom = None
        shape = getattr(sketch, "Shape", None)
        faces = list(getattr(shape, "Faces", []) or []) if shape is not None else []
        edges = list(getattr(shape, "Edges", []) or []) if shape is not None else []
        construction_count = 0
        drawable_geometry: list[Any] = []
        for index, _geometry in enumerate(geometry):
            if self._geometry_construction_state(_geometry, index, sketch):
                construction_count += 1
                continue
            drawable_geometry.append(_geometry)
        closed_loop = self._sketch_geometry_has_closed_profile(drawable_geometry)
        usable_shape_profile = bool(faces) or bool(edges)
        closed_profile = usable_shape_profile and (bool(faces) or closed_loop)
        fully_constrained = (
            degrees_of_freedom == 0 if degrees_of_freedom is not None else False
        )
        ready = closed_profile and fully_constrained
        if ready:
            reason = "Sketch has a closed, fully constrained profile and can be used by PartDesign pad/pocket."
        elif closed_loop and not usable_shape_profile:
            reason = (
                "Sketch endpoint geometry appears closed, but FreeCAD did not expose usable "
                "Shape edges or faces; inspect or rebuild the profile before pad/pocket."
            )
        elif not closed_profile:
            reason = "Sketch does not expose a closed profile yet; add or close profile geometry before pad/pocket."
        elif degrees_of_freedom is None:
            reason = "Sketch has a closed profile, but its constraint completeness could not be verified."
        else:
            reason = (
                "Sketch has a closed profile but is still under-constrained "
                f"({degrees_of_freedom} degrees of freedom); add dimensional/positional constraints before pad/pocket."
            )
        return {
            "found": True,
            "sketch": getattr(sketch, "Name", ""),
            "sketch_label": getattr(sketch, "Label", getattr(sketch, "Name", "")),
            "geometry_count": len(geometry),
            "constraint_count": len(constraints),
            "degrees_of_freedom": degrees_of_freedom,
            "fully_constrained": fully_constrained,
            "under_constrained": bool(
                degrees_of_freedom is not None and degrees_of_freedom > 0
            ),
            "construction_geometry_count": construction_count,
            "edge_count": len(edges),
            "face_count": len(faces),
            "closed_edge_loop": closed_loop,
            "closed_profile": closed_profile,
            "ready_for_pad": ready,
            "ready_for_pocket": ready,
            "reason": reason,
        }

    @staticmethod
    def _sketch_geometry_has_closed_profile(geometry: list[Any]) -> bool:
        endpoints: list[tuple[float, float, float]] = []
        for item in geometry:
            class_name = item.__class__.__name__.lower()
            if "circle" in class_name and "arc" not in class_name:
                return True
            start = getattr(item, "StartPoint", None)
            end = getattr(item, "EndPoint", None)
            if start is None or end is None:
                continue
            endpoints.append(
                (
                    round(float(start.x), 6),
                    round(float(start.y), 6),
                    round(float(start.z), 6),
                )
            )
            endpoints.append(
                (round(float(end.x), 6), round(float(end.y), 6), round(float(end.z), 6))
            )
        if len(endpoints) < 6 or len(endpoints) % 2:
            return False
        counts: dict[tuple[float, float, float], int] = {}
        for endpoint in endpoints:
            counts[endpoint] = counts.get(endpoint, 0) + 1
        return bool(counts) and all(count >= 2 for count in counts.values())

    def _sketch_next_actions(self, sketch: Any | None) -> list[dict[str, Any]]:
        status = self._sketch_profile_status(sketch)
        if not status.get("found"):
            return [
                {
                    "tool": "partdesign.create_sketch",
                    "why": "Create a sketch on a default plane before adding geometry.",
                }
            ]
        if not status.get("ready_for_pad"):
            if status.get("closed_profile") and status.get("under_constrained"):
                return [
                    {
                        "tool": "sketcher.add_constraint",
                        "why": (
                            "The profile is closed but under-constrained; add radius, "
                            "distance, horizontal/vertical, coincident, or equality constraints before creating a feature."
                        ),
                    },
                    {
                        "tool": "sketcher.edit_constraint",
                        "why": "Set existing dimension constraints to the requested values before creating a feature.",
                    },
                ]
            return [
                {
                    "tool": "sketcher.draw_rectangle",
                    "why": "Create a closed profile quickly when a rectangular solid is acceptable.",
                },
                {
                    "tool": "sketcher.add_geometry",
                    "why": "Add lines (kind='line') and coincident constraints until the profile is closed.",
                },
            ]
        return [
            {
                "tool": "partdesign.extrude",
                "arguments": {"operation": "pad", "sketch_name": status.get("sketch")},
                "why": "The active sketch has a closed profile and is ready for an additive feature.",
            },
            {
                "tool": "partdesign.extrude",
                "arguments": {
                    "operation": "pocket",
                    "sketch_name": status.get("sketch"),
                },
                "why": "Use this sketch as a subtractive feature when it is mapped to a solid face.",
            },
        ]

    def _delegate_removed_sketcher_tools_marker(self) -> None:
        # Native Sketcher write tools live in tool_impl/sketcher/*.py and are
        # registered directly as per-tool handlers. Keep VibeCADCore limited to
        # shared document context and Sketcher state inspection helpers.
        return None

    @staticmethod
    def _cell_name(column_index: int, row: int) -> str:
        value = int(column_index)
        column = ""
        while value:
            value, remainder = divmod(value - 1, 26)
            column = chr(ord("A") + remainder) + column
        return f"{column}{row}"

    @staticmethod
    def _valid_cell(cell: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,5}", cell.upper()))

    def _get_spreadsheet(self, sheet_name: str | None = None):
        sheets = [
            obj
            for obj in (
                self._active_document().Objects if self._active_document() else []
            )
            if getattr(obj, "TypeId", "") == "Spreadsheet::Sheet"
        ]
        if sheet_name:
            for sheet in sheets:
                if (
                    sheet.Name == sheet_name
                    or getattr(sheet, "Label", None) == sheet_name
                ):
                    return sheet
            return None
        return sheets[0] if sheets else None

    def _spreadsheet_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Spreadsheet::Sheet"
        ]

    def spreadsheet_summary(
        self,
        sheet_name: str | None = None,
        max_columns: int = 8,
        max_rows: int = 20,
    ) -> dict[str, Any]:
        sheet = self._get_spreadsheet(sheet_name)
        sheets = self._spreadsheet_objects()
        if sheet is None:
            return {
                "found": False,
                "requested": sheet_name,
                "sheet_count": len(sheets),
                "sheets": [self._object_summary(item) for item in sheets],
            }

        safe_columns = max(1, min(int(max_columns), 26))
        safe_rows = max(1, min(int(max_rows), 200))
        cells = []
        for column_index in range(1, safe_columns + 1):
            for row in range(1, safe_rows + 1):
                cell = self._cell_name(column_index, row)
                try:
                    contents = sheet.getContents(cell)
                except Exception:
                    contents = ""
                if contents in ("", None):
                    continue
                try:
                    value = sheet.get(cell)
                except Exception as exc:
                    value = f"<error: {exc}>"
                cells.append(
                    {
                        "cell": cell,
                        "contents": self._short_value(contents),
                        "value": self._short_value(value),
                    }
                )
        return {
            "found": True,
            "sheet": self._object_summary(sheet),
            "scanned_columns": safe_columns,
            "scanned_rows": safe_rows,
            "non_empty_count": len(cells),
            "cells": cells,
        }

    def _draft_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [obj for obj in doc.Objects if self._is_draft_object(obj)]

    @staticmethod
    def _is_draft_object(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        proxy_type = getattr(getattr(obj, "Proxy", None), "Type", "")
        draft_proxy_types = {
            "Array",
            "BSpline",
            "BezCurve",
            "Circle",
            "Clone",
            "Dimension",
            "Ellipse",
            "Facebinder",
            "Label",
            "Line",
            "PathArray",
            "Point",
            "Polygon",
            "Rectangle",
            "ShapeString",
            "Text",
            "Wire",
        }
        return (
            type_id.startswith("Part::Part2DObject")
            or (type_id == "Part::FeaturePython" and proxy_type in draft_proxy_types)
            or proxy_type.startswith("Draft")
        )

    @staticmethod
    def _vector_summary(value: Any) -> list[float] | None:
        if value is None:
            return None
        return [float(value.x), float(value.y), float(value.z)]

    def _draft_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        item["proxy_type"] = getattr(getattr(obj, "Proxy", None), "Type", None)
        for property_name in ("Start", "End", "Length", "Height", "Radius"):
            if hasattr(obj, property_name):
                try:
                    value = getattr(obj, property_name)
                    if (
                        hasattr(value, "x")
                        and hasattr(value, "y")
                        and hasattr(value, "z")
                    ):
                        item[property_name.lower()] = self._vector_summary(value)
                    else:
                        item[property_name.lower()] = self._short_value(value)
                except Exception:
                    continue
        return item

    def draft_summary(self) -> dict[str, Any]:
        objects = [self._draft_object_summary(obj) for obj in self._draft_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects,
        }

    def _partdesign_bodies(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "PartDesign::Body"
        ]

    def _get_partdesign_body(self, body_name: str | None = None):
        bodies = self._partdesign_bodies()
        if body_name:
            for body in bodies:
                if body.Name == body_name or getattr(body, "Label", None) == body_name:
                    return body
            return None
        return bodies[0] if bodies else None

    @staticmethod
    def _partdesign_origin_feature(body: Any, role: str):
        origin = getattr(body, "Origin", None)
        for item in list(getattr(origin, "OriginFeatures", []) or []):
            if (
                getattr(item, "Role", "") == role
                or getattr(item, "Name", "") == role
                or getattr(item, "Label", "").replace("-", "_") == role
            ):
                return item
        return None

    def _partdesign_body_for_feature(self, feature: Any):
        for candidate in self._partdesign_bodies():
            if feature in list(getattr(candidate, "Group", []) or []):
                return candidate
        return self._get_partdesign_body()

    def _partdesign_body_summary(self, body: Any) -> dict[str, Any]:
        features = [
            self._object_summary(item) for item in list(getattr(body, "Group", []))[:80]
        ]
        tip = getattr(body, "Tip", None)
        item = self._object_summary(body)
        item["feature_count"] = len(getattr(body, "Group", []))
        item["features"] = features
        item["tip"] = self._object_summary(tip) if tip else None
        base_feature = getattr(body, "BaseFeature", None)
        item["base_feature"] = (
            self._object_summary(base_feature) if base_feature else None
        )
        return item

    def partdesign_summary(self, body_name: str | None = None) -> dict[str, Any]:
        bodies = self._partdesign_bodies()
        selected = self._get_partdesign_body(body_name)
        return {
            "document": self._active_document().Name
            if self._active_document()
            else None,
            "body_count": len(bodies),
            "requested": body_name,
            "selected": self._partdesign_body_summary(selected) if selected else None,
            "bodies": [self._partdesign_body_summary(body) for body in bodies[:20]],
        }

    def _techdraw_pages(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        pages = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("TechDraw::DrawPage"):
                    pages.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "") == "TechDraw::DrawPage":
                    pages.append(obj)
        return pages

    def _get_techdraw_page(self, page_name: str | None = None):
        pages = self._techdraw_pages()
        if page_name:
            for page in pages:
                if page.Name == page_name or getattr(page, "Label", None) == page_name:
                    return page
            return None
        return pages[0] if pages else None

    def _techdraw_view_summary(self, view: Any) -> dict[str, Any]:
        item = self._object_summary(view)
        for property_name in ("X", "Y", "Scale"):
            if hasattr(view, property_name):
                try:
                    item[property_name.lower()] = self._short_value(
                        getattr(view, property_name)
                    )
                except Exception:
                    continue
        sources = []
        for source in list(getattr(view, "Source", []) or [])[:20]:
            sources.append(self._object_summary(source))
        item["source_count"] = len(getattr(view, "Source", []) or [])
        item["sources"] = sources
        return item

    def _techdraw_page_summary(self, page: Any) -> dict[str, Any]:
        item = self._object_summary(page)
        template = getattr(page, "Template", None)
        views = list(getattr(page, "Views", []) or [])
        item["template"] = self._object_summary(template) if template else None
        item["view_count"] = len(views)
        item["views"] = [self._techdraw_view_summary(view) for view in views[:50]]
        if hasattr(page, "Scale"):
            try:
                item["scale"] = self._short_value(getattr(page, "Scale"))
            except Exception:
                pass
        return item

    def techdraw_summary(self, page_name: str | None = None) -> dict[str, Any]:
        pages = self._techdraw_pages()
        selected = self._get_techdraw_page(page_name)
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "page_count": len(pages),
            "requested": page_name,
            "selected": self._techdraw_page_summary(selected) if selected else None,
            "pages": [self._techdraw_page_summary(page) for page in pages[:20]],
        }

    def _fem_analyses(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        analyses = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Fem::FemAnalysis"):
                    analyses.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "") == "Fem::FemAnalysis":
                    analyses.append(obj)
        return analyses

    def _get_fem_analysis(self, analysis_name: str | None = None):
        analyses = self._fem_analyses()
        if analysis_name:
            for analysis in analyses:
                if (
                    analysis.Name == analysis_name
                    or getattr(analysis, "Label", None) == analysis_name
                ):
                    return analysis
            return None
        return analyses[0] if analyses else None

    @staticmethod
    def _fem_member_category(obj: Any) -> str:
        type_id = getattr(obj, "TypeId", "")
        if "Solver" in type_id:
            return "solver"
        if "FemMesh" in type_id or "Mesh" in type_id:
            return "mesh"
        if "Material" in type_id:
            return "material"
        if "Constraint" in type_id:
            return "constraint"
        if "Result" in type_id:
            return "result"
        return "member"

    def _fem_analysis_summary(self, analysis: Any) -> dict[str, Any]:
        members = list(getattr(analysis, "Group", []) or [])
        item = self._object_summary(analysis)
        item["member_count"] = len(members)
        item["members"] = [
            {
                **self._object_summary(member),
                "category": self._fem_member_category(member),
            }
            for member in members[:80]
        ]
        counts: dict[str, int] = {}
        for member in members:
            category = self._fem_member_category(member)
            counts[category] = counts.get(category, 0) + 1
        item["member_categories"] = counts
        return item

    def fem_summary(self, analysis_name: str | None = None) -> dict[str, Any]:
        analyses = self._fem_analyses()
        selected = self._get_fem_analysis(analysis_name)
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "analysis_count": len(analyses),
            "requested": analysis_name,
            "selected": self._fem_analysis_summary(selected) if selected else None,
            "analyses": [
                self._fem_analysis_summary(analysis) for analysis in analyses[:20]
            ],
        }

    @staticmethod
    def _is_cam_job(obj: Any) -> bool:
        proxy = getattr(obj, "Proxy", None)
        return (
            getattr(obj, "TypeId", "") == "Path::FeaturePython"
            and proxy is not None
            and proxy.__class__.__name__ == "ObjectJob"
            and proxy.__class__.__module__ == "Path.Main.Job"
        )

    def _cam_jobs(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [obj for obj in doc.Objects if self._is_cam_job(obj)]

    def _get_cam_job(self, job_name: str | None = None):
        jobs = self._cam_jobs()
        if job_name:
            for job in jobs:
                if job.Name == job_name or getattr(job, "Label", None) == job_name:
                    return job
            return None
        return jobs[0] if jobs else None

    def _cam_group_summary(self, group: Any) -> dict[str, Any] | None:
        if group is None:
            return None
        objects = list(getattr(group, "Group", []) or [])
        item = self._object_summary(group)
        item["object_count"] = len(objects)
        item["objects"] = [self._object_summary(obj) for obj in objects[:80]]
        return item

    def _cam_job_summary(self, job: Any) -> dict[str, Any]:
        item = self._object_summary(job)
        item["machine"] = str(getattr(job, "Machine", "") or "") or None
        item["postprocessor"] = str(getattr(job, "PostProcessor", "") or "") or None
        for property_name in ("Model", "Operations", "Tools"):
            item[property_name.lower()] = self._cam_group_summary(
                getattr(job, property_name, None)
            )
        for property_name in ("Stock", "SetupSheet"):
            value = getattr(job, property_name, None)
            item[property_name.lower()] = self._object_summary(value) if value else None
        return item

    def cam_summary(self, job_name: str | None = None) -> dict[str, Any]:
        jobs = self._cam_jobs()
        selected = self._get_cam_job(job_name)
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "job_count": len(jobs),
            "requested": job_name,
            "selected": self._cam_job_summary(selected) if selected else None,
            "jobs": [self._cam_job_summary(job) for job in jobs[:20]],
        }

    @staticmethod
    def _is_bim_object(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        proxy_type = getattr(getattr(obj, "Proxy", None), "Type", "")
        return (
            type_id.startswith(("Arch::", "BIM::"))
            or hasattr(obj, "IfcType")
            or proxy_type
            in {
                "Building",
                "BuildingPart",
                "Component",
                "Floor",
                "Site",
                "Space",
                "Structure",
                "Wall",
                "Window",
            }
        )

    def _bim_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [obj for obj in doc.Objects if self._is_bim_object(obj)]

    def _bim_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        item["proxy_type"] = getattr(getattr(obj, "Proxy", None), "Type", None)
        item["ifc_type"] = getattr(obj, "IfcType", None)
        children = list(getattr(obj, "Group", []) or [])
        item["child_count"] = len(children)
        item["children"] = [self._object_summary(child) for child in children[:60]]
        for property_name in ("CompositionType", "Height"):
            if hasattr(obj, property_name):
                try:
                    item[property_name.lower()] = self._short_value(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
        return item

    def bim_summary(self) -> dict[str, Any]:
        objects = self._bim_objects()
        doc = self._active_document()
        ifc_counts: dict[str, int] = {}
        for obj in objects:
            ifc_type = str(getattr(obj, "IfcType", "") or "Unclassified")
            ifc_counts[ifc_type] = ifc_counts.get(ifc_type, 0) + 1
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "ifc_type_counts": ifc_counts,
            "objects": [self._bim_object_summary(obj) for obj in objects[:80]],
        }

    def _assembly_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Assembly::AssemblyObject"):
                    objects.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "") == "Assembly::AssemblyObject":
                    objects.append(obj)
        return objects

    def _get_assembly(self, assembly_name: str | None = None):
        assemblies = self._assembly_objects()
        if assembly_name:
            for assembly in assemblies:
                if (
                    assembly.Name == assembly_name
                    or getattr(assembly, "Label", None) == assembly_name
                ):
                    return assembly
            return None
        return assemblies[0] if assemblies else None

    @staticmethod
    def _is_grounded_joint(obj: Any) -> bool:
        return getattr(obj, "ObjectToGround", None) is not None

    @staticmethod
    def _assembly_joint_objects(assembly: Any) -> list[Any]:
        joints: list[Any] = []
        for child in list(getattr(assembly, "Group", []) or []):
            if getattr(child, "TypeId", "") == "Assembly::JointGroup":
                joints.extend(list(getattr(child, "Group", []) or []))
        return joints

    def _assembly_child_counts(self, assembly: Any) -> dict[str, int]:
        counts = {
            "components": 0,
            "joints": 0,
            "grounded_count": 0,
            "joint_groups": 0,
            "bom_groups": 0,
            "view_groups": 0,
            "simulation_groups": 0,
        }
        joint_names = {
            getattr(joint, "Name", None)
            for joint in self._assembly_joint_objects(assembly)
        }
        for child in list(getattr(assembly, "Group", []) or []):
            type_id = getattr(child, "TypeId", "")
            if type_id == "Assembly::JointGroup":
                counts["joint_groups"] += 1
                for joint in list(getattr(child, "Group", []) or []):
                    if self._is_grounded_joint(joint):
                        counts["grounded_count"] += 1
                    else:
                        counts["joints"] += 1
            elif type_id == "Assembly::BomGroup":
                counts["bom_groups"] += 1
            elif type_id == "Assembly::ViewGroup":
                counts["view_groups"] += 1
            elif type_id == "Assembly::SimulationGroup":
                counts["simulation_groups"] += 1
            elif getattr(child, "Name", None) in joint_names:
                # App::Part-style groups list nested members recursively; joint
                # objects already counted through their JointGroup are not
                # components.
                continue
            else:
                counts["components"] += 1
        return counts

    def _assembly_component_children(self, assembly: Any) -> list[dict[str, Any]]:
        components = []
        joint_names = {
            getattr(joint, "Name", None)
            for joint in self._assembly_joint_objects(assembly)
        }
        for child in list(getattr(assembly, "Group", []) or []):
            type_id = getattr(child, "TypeId", "")
            if type_id in {
                "Assembly::JointGroup",
                "Assembly::BomGroup",
                "Assembly::ViewGroup",
                "Assembly::SimulationGroup",
            }:
                continue
            if getattr(child, "Name", None) in joint_names:
                continue
            components.append(self._object_summary(child))
        return components

    @staticmethod
    def _joint_reference_summary(reference: Any) -> dict[str, Any] | None:
        if not reference:
            return None
        try:
            obj = reference[0]
            subelements = [str(sub) for sub in (reference[1] or []) if sub]
        except (TypeError, IndexError):
            return None
        return {
            "object": getattr(obj, "Name", None),
            "label": getattr(obj, "Label", None),
            "elements": subelements,
        }

    def _joint_summary(self, joint: Any) -> dict[str, Any]:
        item = self._object_summary(joint)
        if self._is_grounded_joint(joint):
            grounded_obj = getattr(joint, "ObjectToGround", None)
            item["grounded"] = True
            item["object_to_ground"] = getattr(grounded_obj, "Name", None)
            return item
        item["grounded"] = False
        item["joint_type"] = getattr(joint, "JointType", None)
        item["reference1"] = self._joint_reference_summary(
            getattr(joint, "Reference1", None)
        )
        item["reference2"] = self._joint_reference_summary(
            getattr(joint, "Reference2", None)
        )
        return item

    def _assembly_summary(self, assembly: Any) -> dict[str, Any]:
        item = self._object_summary(assembly)
        item["type_property"] = getattr(assembly, "Type", None)
        item["child_count"] = len(list(getattr(assembly, "Group", []) or []))
        item.update(self._assembly_child_counts(assembly))
        item["component_children"] = self._assembly_component_children(assembly)[:60]
        item["joint_children"] = [
            self._joint_summary(joint)
            for joint in self._assembly_joint_objects(assembly)[:40]
        ]
        item["children"] = [
            self._object_summary(child)
            for child in list(getattr(assembly, "Group", []) or [])[:40]
        ]
        return item

    def assembly_summary(self) -> dict[str, Any]:
        assemblies = self._assembly_objects()
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "assembly_count": len(assemblies),
            "assemblies": [self._assembly_summary(item) for item in assemblies[:40]],
        }

    @staticmethod
    def _is_inspection_geometry(obj: Any) -> bool:
        for type_name in ("Mesh::Feature", "Points::Feature", "Part::Feature"):
            try:
                if obj.isDerivedFrom(type_name):
                    return True
            except Exception:
                continue
        type_id = getattr(obj, "TypeId", "")
        return (
            type_id.startswith("Mesh::")
            or type_id.startswith("Points::")
            or type_id.startswith("Part::")
        )

    def _inspection_features(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "")
            in {"Inspection::Feature", "Inspection::Group"}
        ]

    def _inspection_candidates(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [obj for obj in doc.Objects if self._is_inspection_geometry(obj)]

    @staticmethod
    def _inspection_distances_count(feature: Any) -> int:
        distances = getattr(feature, "Distances", None)
        if distances is None:
            return 0
        try:
            return len(distances)
        except Exception:
            try:
                return int(distances.getSize())
            except Exception:
                return 0

    def _inspection_feature_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        if getattr(obj, "TypeId", "") == "Inspection::Group":
            group = list(getattr(obj, "Group", []) or [])
            item["feature_count"] = len(
                [
                    child
                    for child in group
                    if getattr(child, "TypeId", "") == "Inspection::Feature"
                ]
            )
            item["children"] = [self._object_summary(child) for child in group[:40]]
            return item

        actual = getattr(obj, "Actual", None)
        nominals = list(getattr(obj, "Nominals", []) or [])
        item["actual"] = self._object_summary(actual) if actual else None
        item["nominal_count"] = len(nominals)
        item["nominals"] = [self._object_summary(nominal) for nominal in nominals[:20]]
        item["search_radius"] = self._short_value(getattr(obj, "SearchRadius", None))
        item["thickness"] = self._short_value(getattr(obj, "Thickness", None))
        item["distance_count"] = self._inspection_distances_count(obj)
        return item

    def inspection_summary(self) -> dict[str, Any]:
        objects = self._inspection_features()
        candidates = self._inspection_candidates()
        groups = [
            obj for obj in objects if getattr(obj, "TypeId", "") == "Inspection::Group"
        ]
        features = [
            obj
            for obj in objects
            if getattr(obj, "TypeId", "") == "Inspection::Feature"
        ]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "group_count": len(groups),
            "feature_count": len(features),
            "candidate_count": len(candidates),
            "groups": [self._inspection_feature_summary(obj) for obj in groups[:40]],
            "features": [
                self._inspection_feature_summary(obj) for obj in features[:80]
            ],
            "candidates": [self._object_summary(obj) for obj in candidates[:80]],
        }

    @staticmethod
    def _is_openscad_related(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        if type_id.startswith("Part::") or type_id.startswith("Mesh::"):
            return True
        proxy = getattr(obj, "Proxy", None)
        module = getattr(proxy.__class__, "__module__", "") if proxy else ""
        return module.startswith("OpenSCAD")

    def _openscad_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        proxy = getattr(obj, "Proxy", None)
        if proxy is not None:
            item["proxy_module"] = getattr(proxy.__class__, "__module__", "")
            item["proxy_type"] = proxy.__class__.__name__
        for property_name in ("Arguments", "Children", "Components", "Objects"):
            if hasattr(obj, property_name):
                try:
                    value = getattr(obj, property_name)
                    if isinstance(value, list):
                        item[property_name.lower()] = [
                            self._object_summary(child)
                            for child in value[:20]
                            if hasattr(child, "Name")
                        ]
                    else:
                        item[property_name.lower()] = self._short_value(value)
                except Exception:
                    continue
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = {
                    "solids": len(getattr(shape, "Solids", [])),
                    "faces": len(getattr(shape, "Faces", [])),
                    "edges": len(getattr(shape, "Edges", [])),
                    "volume": float(getattr(shape, "Volume", 0.0)),
                }
            except Exception:
                pass
        mesh = getattr(obj, "Mesh", None)
        if mesh is not None:
            item["mesh"] = {
                "points": int(getattr(mesh, "CountPoints", 0)),
                "facets": int(getattr(mesh, "CountFacets", 0)),
            }
        return item

    def openscad_summary(self) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "document": None,
                "object_count": 0,
                "objects": [],
                "openscad_executable": "",
                "openscad_executable_configured": False,
            }
        try:
            import FreeCAD as App

            params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/OpenSCAD")
            executable = params.GetString("openscadexecutable")
        except Exception:
            executable = ""
        objects = [
            self._openscad_object_summary(obj)
            for obj in doc.Objects
            if self._is_openscad_related(obj)
        ]
        return {
            "document": doc.Name,
            "object_count": len(objects),
            "objects": objects[:80],
            "openscad_executable": executable,
            "openscad_executable_configured": bool(executable),
        }

    @staticmethod
    def _validate_csg_text(csg_text: str) -> str:
        text = (csg_text or "").strip()
        if not text:
            raise ValueError("csg_text cannot be empty.")
        if len(text) > 20000:
            raise ValueError("csg_text is limited to 20000 characters.")
        forbidden = ("import(", "include", "use <", "surface(")
        lowered = text.lower().replace(" ", "")
        if any(token.replace(" ", "") in lowered for token in forbidden):
            raise ValueError("csg_text may not reference external files.")
        return text + ("\n" if not text.endswith("\n") else "")

    def activate_workbench(self, name: str) -> dict[str, Any]:
        result = self._registry.call("core.activate_workbench", name=name)
        return {
            "activated": bool(result.get("ok")),
            "requested": name,
            "active": result.get("active_workbench"),
            **({"error": result["error"]} if result.get("error") else {}),
        }

    def project_context(self) -> dict[str, Any]:
        return self._project_store.context()

    def update_project_summary(
        self, *, title: str = "", summary: str = ""
    ) -> dict[str, Any]:
        return self._project_store.update_summary(title=title, summary=summary)

    def update_design_preflight(self, preflight: dict[str, Any]) -> dict[str, Any]:
        return self._project_store.update_design_preflight(preflight)

    def update_design_memory(self, memory_update: dict[str, Any]) -> dict[str, Any]:
        return self._project_store.update_design_memory(memory_update)

    def record_design_preflight_answers(
        self,
        answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._project_store.record_design_preflight_answers(answers)

    def queue_steering_message(self, text: str, source: str = "user") -> dict[str, Any]:
        clean = str(text or "").strip()
        if not clean:
            return {"ok": False, "error": "Steering message cannot be empty."}
        self._steering_sequence += 1
        entry = {
            "id": self._steering_sequence,
            "source": str(source or "user"),
            "text": clean,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "consumed": False,
        }
        self._steering_messages.append(entry)
        self._steering_messages = self._steering_messages[-40:]
        return {"ok": True, "message": entry}

    def consume_steering_messages(self) -> list[dict[str, Any]]:
        pending = [
            item
            for item in self._steering_messages
            if isinstance(item, dict) and not item.get("consumed")
        ]
        for item in pending:
            item["consumed"] = True
            item["consumed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return [dict(item) for item in pending]

    def steering_state(self) -> dict[str, Any]:
        return {
            "queued_count": len(
                [item for item in self._steering_messages if not item.get("consumed")]
            ),
            "messages": [dict(item) for item in self._steering_messages[-20:]],
        }

    def _conversation_scope(self) -> dict[str, Any]:
        """Where the active conversation lives.

        Conversations are stored alongside the project manifest in the
        per-document project folder under the central VibeCAD data dir —
        never next to the CAD file. Legacy ``.vibecad-chat.json`` sidecars
        are exposed via ``legacy_path`` for read-only migration.
        """
        project = self._project_store.project_scope()
        root = Path(str(project["root"]))
        path = root / "conversation.json"
        doc_info = project.get("document") or {}
        document_name = str(doc_info.get("document") or "")
        file_path = doc_info.get("file_path")
        if file_path:
            legacy = Path(str(file_path))
            return {
                "kind": "saved_document",
                "document": document_name,
                "file_path": str(file_path),
                "path": str(path),
                "legacy_path": str(
                    legacy.with_name(f"{legacy.name}.vibecad-chat.json")
                ),
                "persistent": True,
            }
        if document_name:
            return {
                "kind": "unsaved_document",
                "document": document_name,
                "file_path": None,
                "path": str(path),
                "persistent": True,
                "document_saved": False,
            }
        return {
            "kind": "no_document",
            "document": None,
            "file_path": None,
            "path": str(path),
            "persistent": True,
            "document_saved": False,
        }

    def _conversation_path(self) -> Path:
        return Path(str(self._conversation_scope()["path"]))

    def _load_conversation_for_active_document(
        self,
    ) -> tuple[Path, list[dict[str, Any]]]:
        scope = self._conversation_scope()
        path = Path(str(scope["path"]))
        key = str(path)
        if key == self._conversation_cache_key:
            return path, list(self._conversation_cache)

        loaded: list[dict[str, Any]] = []
        migrated_from_legacy = False
        try:
            source: Path | None = None
            if bool(scope.get("persistent")):
                if path.exists():
                    source = path
                else:
                    legacy = str(scope.get("legacy_path") or "")
                    if legacy and Path(legacy).exists():
                        source = Path(legacy)
                        migrated_from_legacy = True
            if source is not None:
                data = json.loads(source.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    turns = data.get("conversation", [])
                else:
                    turns = data
                if isinstance(turns, list):
                    loaded = [
                        item
                        for item in turns
                        if isinstance(item, dict)
                        and item.get("role") in {"user", "assistant", "system"}
                    ][-MAX_CONVERSATION_TURNS:]
        except Exception:
            loaded = []
            migrated_from_legacy = False
        self._conversation_cache_key = key
        self._conversation_cache = loaded
        if migrated_from_legacy and loaded:
            # One-time migration: persist the legacy sidecar content in the
            # new project-folder location. The legacy file is left untouched.
            self._write_conversation(path, loaded)
        return path, list(self._conversation_cache)

    def _write_conversation(
        self, path: Path, conversation: list[dict[str, Any]]
    ) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": "VibeCAD conversation",
                "version": 1,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "conversation": conversation[-MAX_CONVERSATION_TURNS:],
            }
            tmp = path.with_name(f"{path.name}.tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            tmp.replace(path)
        except Exception:
            pass

    @staticmethod
    def conversation_path_for_document_file(file_path: str | Path) -> Path:
        return project_root_for_document_file(file_path) / "conversation.json"

    @staticmethod
    def _clean_conversation_turns(
        conversation: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for item in conversation:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant", "system"} or not content:
                continue
            turn = dict(item)
            turn["role"] = role
            turn["content"] = content
            cleaned.append(turn)
        return cleaned[-MAX_CONVERSATION_TURNS:]

    def write_conversation_for_document_file(
        self,
        file_path: str | Path,
        conversation: list[dict[str, Any]],
    ) -> dict[str, Any]:
        path = self.conversation_path_for_document_file(file_path)
        cleaned = self._clean_conversation_turns(conversation)
        self._conversation_cache = cleaned
        self._conversation_cache_key = str(path)
        self._write_conversation(path, cleaned)
        return {
            "path": str(path),
            "turn_count": len(cleaned),
            "conversation": cleaned,
        }

    def conversation_history(self) -> dict[str, Any]:
        scope = self._conversation_scope()
        path, conversation = self._load_conversation_for_active_document()
        return {
            "path": str(path),
            "scope": scope,
            "turn_count": len(conversation),
            "turn_limit": MAX_CONVERSATION_TURNS,
            "conversation": conversation,
        }

    def record_conversation_turn(
        self,
        role: str,
        content: str,
        provider: str | None = None,
        tool_trace: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Unsupported conversation role: {role}")
        path, conversation = self._load_conversation_for_active_document()
        clean_content = str(content).strip()
        if not clean_content:
            return self.conversation_history()
        if conversation:
            if role == "user":
                for previous in reversed(conversation):
                    previous_role = previous.get("role")
                    if previous_role == "assistant":
                        break
                    if (
                        previous_role == "user"
                        and str(previous.get("content", "")).strip() == clean_content
                    ):
                        return self.conversation_history()
            latest = conversation[-1]
            if (
                latest.get("role") == role
                and str(latest.get("content", "")).strip() == clean_content
            ):
                return self.conversation_history()
        entry: dict[str, Any] = {
            "role": role,
            "content": clean_content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if provider:
            entry["provider"] = provider
        if tool_trace:
            entry["tool_trace"] = tool_trace[-20:]
        if metadata:
            entry["metadata"] = metadata
        conversation.append(entry)
        conversation = conversation[-MAX_CONVERSATION_TURNS:]
        self._conversation_cache = conversation
        self._conversation_cache_key = str(path)
        self._write_conversation(path, conversation)
        if role == "user":
            self._project_store.record_requirement_memory(
                role=role,
                content=clean_content,
                metadata=metadata,
            )
        return self.conversation_history()

    def report_view_errors(self) -> dict[str, Any]:
        return self._registry.call("core.get_report_view_errors")

    def clear_local_session(self) -> dict[str, Any]:
        self._steering_messages.clear()
        result = self._registry.call("core.clear_local_session")
        return result

    def tool_shape_report(
        self,
        workbench: str | None = None,
        *,
        full_workspace: bool = False,
    ) -> dict[str, Any]:
        kwargs = {"workbench": workbench} if workbench else {}
        if full_workspace:
            kwargs["full_workspace"] = True
        return self._registry.call("core.get_tool_shape_report", **kwargs)

    def report_tool_shape_gap(
        self,
        missing_capability: str,
        why_needed: str,
        desired_native_tool: str,
        current_workaround: str = "",
        active_workbench: str = "",
    ) -> dict[str, Any]:
        return self._registry.call(
            "core.report_tool_shape_gap",
            missing_capability=missing_capability,
            why_needed=why_needed,
            desired_native_tool=desired_native_tool,
            current_workaround=current_workaround,
            active_workbench=active_workbench,
        )

    def provider_tool_surface(self, workbench: str | None = None) -> dict[str, Any]:
        from VibeCADSession import is_provider_safe_tool

        active = workbench or self.active_workbench_name()
        tools = [
            self._registry.get(name).to_schema(active_workbench=active)
            for name in self._registry.names()
            if is_provider_safe_tool(self, name, active)
        ]
        return {
            "active_workbench": active,
            "tool_pack_enabled": self.is_workbench_tool_pack_enabled(active),
            "tool_count": len(tools),
            "tools": tools,
        }

    def is_provider_tool_available(
        self,
        tool_name: str,
        workbench: str | None = None,
    ) -> bool:
        from VibeCADSession import is_provider_safe_tool

        return is_provider_safe_tool(
            self, tool_name, workbench or self.active_workbench_name()
        )

    def undo_last_vibecad_action(self) -> dict[str, Any]:
        return self._registry.call("core.undo_last_vibecad_action")

    def _surface_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "").startswith("Surface::")
        ]

    @staticmethod
    def _link_sub_count(value: Any) -> int:
        try:
            return len(value)
        except Exception:
            pass
        try:
            return len(value.getSubValues())
        except Exception:
            return 0

    def _surface_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = {
                    "faces": len(getattr(shape, "Faces", [])),
                    "edges": len(getattr(shape, "Edges", [])),
                    "vertices": len(getattr(shape, "Vertexes", [])),
                }
            except Exception:
                pass
        for property_name in (
            "BoundaryEdges",
            "BoundaryList",
            "NSections",
            "UnboundEdges",
            "FreeFaces",
            "Points",
        ):
            if hasattr(obj, property_name):
                try:
                    item[property_name.lower()] = self._link_sub_count(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
        for property_name in (
            "FillType",
            "Degree",
            "PointsOnCurve",
            "Iterations",
            "MaximumDegree",
            "MaximumSegments",
        ):
            if hasattr(obj, property_name):
                try:
                    item[property_name.lower()] = self._short_value(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
        return item

    def surface_summary(self) -> dict[str, Any]:
        objects = [self._surface_object_summary(obj) for obj in self._surface_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
            "feature_types": sorted({item["type"] for item in objects}),
        }

    @staticmethod
    def _is_reverseengineering_candidate(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        return type_id.startswith("Points::") or type_id.startswith("Mesh::")

    @staticmethod
    def _is_reverseengineering_output(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        label = str(getattr(obj, "Label", ""))
        name = str(getattr(obj, "Name", ""))
        if type_id == "Part::Spline":
            return True
        if type_id.startswith("Part::") and any(
            token in (label + " " + name).lower()
            for token in ("fit", "approx", "segment", "spline")
        ):
            return True
        return False

    def _reverseengineering_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        points = getattr(obj, "Points", None)
        if points is not None:
            try:
                item["points"] = {
                    "count": int(points.count()),
                    "bound_box": self._bound_box_summary(
                        getattr(points, "BoundBox", None)
                    ),
                }
            except Exception:
                try:
                    item["points"] = {"count": len(points)}
                except Exception:
                    pass
        mesh = getattr(obj, "Mesh", None)
        if mesh is not None:
            item["mesh"] = {
                "points": int(getattr(mesh, "CountPoints", 0)),
                "facets": int(getattr(mesh, "CountFacets", 0)),
                "bound_box": self._bound_box_summary(getattr(mesh, "BoundBox", None)),
            }
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = {
                    "edges": len(getattr(shape, "Edges", [])),
                    "faces": len(getattr(shape, "Faces", [])),
                    "solids": len(getattr(shape, "Solids", [])),
                }
            except Exception:
                pass
        return item

    def reverseengineering_summary(self) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "document": None,
                "candidate_count": 0,
                "reconstruction_count": 0,
                "candidates": [],
                "reconstructions": [],
            }
        candidates = [
            self._reverseengineering_object_summary(obj)
            for obj in doc.Objects
            if self._is_reverseengineering_candidate(obj)
        ]
        outputs = [
            self._reverseengineering_object_summary(obj)
            for obj in doc.Objects
            if self._is_reverseengineering_output(obj)
        ]
        return {
            "document": doc.Name,
            "candidate_count": len(candidates),
            "reconstruction_count": len(outputs),
            "candidates": candidates[:80],
            "reconstructions": outputs[:80],
        }

    @staticmethod
    def _placement_summary(value: Any) -> dict[str, Any] | None:
        try:
            base = value.Base
            rotation = value.Rotation
            return {
                "base": [float(base.x), float(base.y), float(base.z)],
                "rotation_euler": [float(item) for item in rotation.toEuler()],
            }
        except Exception:
            return None

    @staticmethod
    def _waypoint_summary(waypoint: Any, index: int) -> dict[str, Any]:
        item = {
            "index": index,
            "name": getattr(waypoint, "Name", ""),
            "type": getattr(waypoint, "Type", ""),
            "velocity": getattr(waypoint, "Velocity", None),
            "acceleration": getattr(waypoint, "Acceleration", None),
            "continuous": bool(getattr(waypoint, "Cont", False)),
        }
        placement = getattr(waypoint, "Pos", None)
        if placement is not None:
            item["placement"] = VibeCADService._placement_summary(placement)
        return item

    def _robot_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        if getattr(obj, "TypeId", "") == "Robot::RobotObject":
            item["axes"] = {}
            for axis in range(1, 7):
                property_name = f"Axis{axis}"
                try:
                    item["axes"][property_name.lower()] = float(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
            item["tcp"] = self._placement_summary(getattr(obj, "Tcp", None))
            item["base"] = self._placement_summary(getattr(obj, "Base", None))
            item["tool"] = self._placement_summary(getattr(obj, "Tool", None))
            for property_name in ("RobotVrmlFile", "RobotKinematicFile", "Error"):
                if hasattr(obj, property_name):
                    try:
                        item[property_name.lower()] = self._short_value(
                            getattr(obj, property_name)
                        )
                    except Exception:
                        continue
        if getattr(obj, "TypeId", "") == "Robot::TrajectoryObject":
            trajectory = getattr(obj, "Trajectory", None)
            waypoints = (
                list(getattr(trajectory, "Waypoints", []) or []) if trajectory else []
            )
            item["waypoint_count"] = len(waypoints)
            try:
                item["duration"] = float(getattr(trajectory, "Duration", 0.0))
            except Exception:
                pass
            try:
                item["length"] = float(getattr(trajectory, "Length", 0.0))
            except Exception:
                pass
            item["waypoints"] = [
                self._waypoint_summary(waypoint, index)
                for index, waypoint in enumerate(waypoints[:20])
            ]
        return item

    def robot_summary(self) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "document": None,
                "robot_count": 0,
                "trajectory_count": 0,
                "robots": [],
                "trajectories": [],
            }
        robots = [
            self._robot_object_summary(obj)
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Robot::RobotObject"
        ]
        trajectories = [
            self._robot_object_summary(obj)
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Robot::TrajectoryObject"
        ]
        return {
            "document": doc.Name,
            "robot_count": len(robots),
            "trajectory_count": len(trajectories),
            "robots": robots[:40],
            "trajectories": trajectories[:40],
        }

    @staticmethod
    def _is_meshpart_part_candidate(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        return type_id.startswith("Part::") and hasattr(obj, "Shape")

    @staticmethod
    def _is_meshpart_mesh_output(obj: Any) -> bool:
        return getattr(obj, "TypeId", "").startswith("Mesh::")

    def meshpart_summary(self) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "document": None,
                "part_candidate_count": 0,
                "mesh_count": 0,
                "part_candidates": [],
                "meshes": [],
            }
        part_candidates = [
            self._part_object_summary(obj)
            for obj in doc.Objects
            if self._is_meshpart_part_candidate(obj)
        ]
        meshes = [
            self._mesh_object_summary(obj)
            for obj in doc.Objects
            if self._is_meshpart_mesh_output(obj)
        ]
        return {
            "document": doc.Name,
            "part_candidate_count": len(part_candidates),
            "mesh_count": len(meshes),
            "part_candidates": part_candidates[:80],
            "meshes": meshes[:80],
        }

    def _part_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "").startswith("Part::")
        ]

    def _get_document_object(self, object_name: str | None):
        doc = self._active_document()
        if doc is None or not object_name:
            return None
        obj = doc.getObject(str(object_name))
        if obj is not None:
            return obj
        return next(
            (
                item
                for item in doc.Objects
                if getattr(item, "Label", None) == str(object_name)
            ),
            None,
        )

    def _part_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        for property_name in (
            "Length",
            "Width",
            "Height",
            "Radius",
            "Angle",
            "Placement",
        ):
            if hasattr(obj, property_name):
                try:
                    item[property_name.lower()] = self._short_value(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = {
                    "solids": len(getattr(shape, "Solids", [])),
                    "faces": len(getattr(shape, "Faces", [])),
                    "edges": len(getattr(shape, "Edges", [])),
                    "volume": float(getattr(shape, "Volume", 0.0)),
                }
            except Exception:
                pass
        return item

    def part_summary(self) -> dict[str, Any]:
        objects = [self._part_object_summary(obj) for obj in self._part_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    def _mesh_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Mesh::Feature"):
                    objects.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "").startswith("Mesh::"):
                    objects.append(obj)
        return objects

    @staticmethod
    def _bound_box_summary(bound_box: Any) -> dict[str, float] | None:
        if bound_box is None:
            return None
        try:
            return {
                "xmin": float(bound_box.XMin),
                "ymin": float(bound_box.YMin),
                "zmin": float(bound_box.ZMin),
                "xmax": float(bound_box.XMax),
                "ymax": float(bound_box.YMax),
                "zmax": float(bound_box.ZMax),
            }
        except Exception:
            return None

    def _mesh_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        mesh = getattr(obj, "Mesh", None)
        if mesh is not None:
            item["mesh"] = {
                "points": int(getattr(mesh, "CountPoints", 0)),
                "edges": int(getattr(mesh, "CountEdges", 0)),
                "facets": int(getattr(mesh, "CountFacets", 0)),
                "bound_box": self._bound_box_summary(getattr(mesh, "BoundBox", None)),
            }
        return item

    def mesh_summary(self) -> dict[str, Any]:
        objects = [self._mesh_object_summary(obj) for obj in self._mesh_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    def _points_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Points::Feature"):
                    objects.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "").startswith("Points::"):
                    objects.append(obj)
        return objects

    def _points_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        points = getattr(obj, "Points", None)
        point_list = (
            list(getattr(points, "Points", []) or []) if points is not None else []
        )
        item["point_count"] = len(point_list)
        item["bound_box"] = self._bound_box_summary(getattr(points, "BoundBox", None))
        item["sample"] = [
            [float(point.x), float(point.y), float(point.z)]
            for point in point_list[:8]
            if hasattr(point, "x") and hasattr(point, "y") and hasattr(point, "z")
        ]
        return item

    def points_summary(self) -> dict[str, Any]:
        objects = [self._points_object_summary(obj) for obj in self._points_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    def _material_capable_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            if hasattr(obj, "ShapeMaterial"):
                objects.append(obj)
                continue
            try:
                view_object = getattr(obj, "ViewObject", None)
                if view_object is not None and hasattr(view_object, "ShapeAppearance"):
                    objects.append(obj)
            except Exception:
                continue
        return objects

    def _material_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        material = getattr(obj, "ShapeMaterial", None)
        if material is not None:
            item["material_name"] = self._short_value(getattr(material, "Name", ""))
            item["material_uuid"] = self._short_value(getattr(material, "UUID", ""))
            for property_name in (
                "DiffuseColor",
                "AmbientColor",
                "SpecularColor",
                "EmissiveColor",
                "Transparency",
            ):
                try:
                    if material.hasAppearanceProperty(property_name):
                        item[property_name.lower()] = self._short_value(
                            material.getAppearanceValue(property_name)
                        )
                except Exception:
                    properties = getattr(material, "AppearanceProperties", {}) or {}
                    if property_name in properties:
                        item[property_name.lower()] = self._short_value(
                            properties[property_name]
                        )
        try:
            view_object = getattr(obj, "ViewObject", None)
            appearances = (
                list(getattr(view_object, "ShapeAppearance", []) or [])
                if view_object
                else []
            )
            item["shape_appearance_count"] = len(appearances)
            if appearances:
                appearance = appearances[0]
                item["first_shape_diffuse_color"] = self._short_value(
                    tuple(getattr(appearance, "DiffuseColor", ()))
                )
                item["first_shape_transparency"] = self._short_value(
                    getattr(appearance, "Transparency", None)
                )
        except Exception:
            item["shape_appearance_count"] = None
        return item

    def material_summary(self) -> dict[str, Any]:
        objects = [
            self._material_object_summary(obj)
            for obj in self._material_capable_objects()
        ]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    @staticmethod
    def _coerce_rgb(color: Any) -> tuple[float, float, float]:
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError("diffuse_color must be [r, g, b].")
        values = tuple(float(component) for component in color)
        if any(component < 0.0 or component > 1.0 for component in values):
            raise ValueError("diffuse_color components must be between 0.0 and 1.0.")
        return values

    @staticmethod
    def _coerce_points(points: Any) -> list[list[float]]:
        if points is None:
            return [[0.0, 0.0, 0.0]]
        if not isinstance(points, list):
            raise ValueError("points must be a list of [x, y, z] coordinates.")
        if not points:
            raise ValueError("points must contain at least one coordinate.")
        if len(points) > 1000:
            raise ValueError("points is limited to 1000 coordinates.")
        coerced = []
        for index, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                raise ValueError(f"Point {index} must be [x, y, z].")
            coerced.append([float(point[0]), float(point[1]), float(point[2])])
        return coerced

    def run_workbench_command(self, command_name: str) -> dict[str, Any]:
        return self._registry.call(
            "core.run_workbench_command", command_name=command_name
        )

    def _select_default_sketch_plane(self) -> dict[str, Any]:
        try:
            import FreeCAD as App
            import FreeCADGui as Gui
        except Exception as exc:
            return {"selected": False, "error": str(exc)}

        doc = App.ActiveDocument
        if doc is None:
            return {"selected": False, "error": "No active document."}
        try:
            if Gui.Selection.getSelectionEx():
                return {"selected": False, "reason": "Existing selection preserved."}
        except Exception:
            pass
        bodies = [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "PartDesign::Body"
        ]
        if not bodies:
            return {"selected": False, "error": "No PartDesign body."}
        body = bodies[0]
        origin = getattr(body, "Origin", None)
        features = list(getattr(origin, "OriginFeatures", []) or [])
        plane = next(
            (
                item
                for item in features
                if getattr(item, "Name", "") == "XY_Plane"
                or getattr(item, "Label", "") in {"XY-plane", "XY_Plane", "XY plane"}
            ),
            features[3] if len(features) > 3 else None,
        )
        if plane is None:
            return {"selected": False, "error": "XY plane not found."}
        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, plane.Name)
            return {
                "selected": True,
                "object": plane.Name,
                "label": getattr(plane, "Label", plane.Name),
            }
        except Exception as exc:
            return {"selected": False, "error": str(exc)}

    def context_summary(self) -> dict[str, Any]:
        auth = self.auth_state()
        return {
            "auth": {
                "status": auth.status.value,
                "source": auth.source,
                "configured": auth.can_call_provider,
            },
            "provider": {
                "model": self.provider_model(),
                "reasoning_effort": self.provider_reasoning_effort(),
                "use_online_by_default": self.use_online_provider_by_default(),
            },
            "workbench": self.active_workbench_name(),
            "vibecad_project": self.project_context(),
            "human_steering": self.steering_state(),
            "document": self.document_summary(),
            "selection": self.selection_summary(),
            "view": self.view_state(),
            "task_panel": self.task_panel_summary(),
            "view_screenshot": self.view_screenshot_summary(),
            "reference_images": self.reference_images_summary(),
            "workbenches": self.workbench_summary(),
            "workbench_tool_pack": self.workbench_tool_pack_summary(),
            "workbench_commands": self.workbench_command_summary(),
            "workbench_object_templates": self.workbench_object_templates(),
            "workbench_objects": self.workbench_object_summary(),
            "part": self.part_summary(),
            "mesh": self.mesh_summary(),
            "points": self.points_summary(),
            "material": self.material_summary(),
            "sketcher": self.sketcher_summary(),
            "spreadsheet": self.spreadsheet_summary(),
            "draft": self.draft_summary(),
            "partdesign": self.partdesign_summary(),
            "techdraw": self.techdraw_summary(),
            "fem": self.fem_summary(),
            "cam": self.cam_summary(),
            "bim": self.bim_summary(),
            "assembly": self.assembly_summary(),
            "inspection": self.inspection_summary(),
            "openscad": self.openscad_summary(),
            "surface": self.surface_summary(),
            "reverseengineering": self.reverseengineering_summary(),
            "robot": self.robot_summary(),
            "meshpart": self.meshpart_summary(),
            "provider_tool_surface": self.provider_tool_surface(),
            "tool_shape_report": self.tool_shape_report(),
            "conversation": self.conversation_history(),
            "report_view_errors": self.report_view_errors(),
        }

    def provider_context_summary(self) -> dict[str, Any]:
        """Return the scoped context sent to the OpenAI CAD operator.

        The general UI context intentionally contains broad summaries. The
        provider context stays narrow: current document, active workbench,
        visual/reference state, conversation, and domain state for the active
        workbench. Auth/model settings are resolved before this context is sent;
        direct function tools carry the callable surface.
        """
        active_workbench = self.active_workbench_name()
        context: dict[str, Any] = {
            "workbench": active_workbench,
            "vibecad_project": self.project_context(),
            "human_steering": self.steering_state(),
            "document": self.document_summary(),
            "selection": self.selection_summary(),
            "view": self.view_state(),
            "task_panel": self.task_panel_summary(),
            "view_screenshot": self.view_screenshot_summary(),
            "reference_images": self.reference_images_summary(),
            "conversation": self.conversation_history(),
            "report_view_errors": self.report_view_errors(),
        }
        context.update(self._provider_domain_context(active_workbench))
        return context

    def _provider_domain_context(self, workbench: str | None) -> dict[str, Any]:
        if workbench == "PartDesignWorkbench":
            return {
                "partdesign": self.partdesign_summary(),
                "sketcher": self.sketcher_summary(),
                "material": self.material_summary(),
                "assembly": self.assembly_summary(),
            }
        if workbench == "SketcherWorkbench":
            return {"sketcher": self.sketcher_summary()}
        if workbench == "PartWorkbench":
            return {
                "part": self.part_summary(),
                "material": self.material_summary(),
                "assembly": self.assembly_summary(),
            }
        if workbench == "AssemblyWorkbench":
            return {
                "assembly": self.assembly_summary(),
                "partdesign": self.partdesign_summary(),
                "part": self.part_summary(),
            }
        if workbench == "DraftWorkbench":
            return {"draft": self.draft_summary()}
        if workbench == "MaterialWorkbench":
            return {"material": self.material_summary()}
        if workbench == "TechDrawWorkbench":
            return {
                "techdraw": self.techdraw_summary(),
                "document": self.document_summary(),
            }
        if workbench == "MeshWorkbench":
            return {"mesh": self.mesh_summary()}
        if workbench == "MeshPartWorkbench":
            return {
                "meshpart": self.meshpart_summary(),
                "mesh": self.mesh_summary(),
                "part": self.part_summary(),
            }
        if workbench == "PointsWorkbench":
            return {"points": self.points_summary()}
        if workbench == "SpreadsheetWorkbench":
            return {"spreadsheet": self.spreadsheet_summary()}
        if workbench == "FemWorkbench":
            return {"fem": self.fem_summary()}
        if workbench == "CAMWorkbench":
            return {"cam": self.cam_summary()}
        if workbench == "BIMWorkbench":
            return {"bim": self.bim_summary()}
        if workbench == "InspectionWorkbench":
            return {"inspection": self.inspection_summary()}
        if workbench == "OpenSCADWorkbench":
            return {"openscad": self.openscad_summary()}
        if workbench == "SurfaceWorkbench":
            return {"surface": self.surface_summary()}
        if workbench == "ReverseEngineeringWorkbench":
            return {"reverseengineering": self.reverseengineering_summary()}
        if workbench == "RobotWorkbench":
            return {"robot": self.robot_summary()}
        return {}

    def _register_core_tools(self) -> None:
        service_tools.register_tools(self._registry, self)
        sketcher_tools.register_tools(self._registry, self)


_service: VibeCADService | None = None


def get_service() -> VibeCADService:
    global _service
    if _service is None:
        _service = VibeCADService()
    return _service
