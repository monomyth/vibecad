# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import json
import multiprocessing
import re
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from VibeCADAuth import (
    AuthState,
    AuthStatus,
)
from VibeCADCore import (
    MAX_REFERENCE_IMAGES,
    VibeCADService,
)
from VibeCADPreferences import (
    DEFAULT_ANTHROPIC_MODEL,
)
from VibeCADProvider import (
    ANTHROPIC_REQUEST_DUMP_DIR_ENV,
    ANTHROPIC_THINKING_BUDGETS,
    AnthropicProvider,
    OfflineProvider,
    ProviderUnavailable,
    OpenAIAgentsProvider,
    _AnthropicFunctionTool,
    _agents_input_from_context,
    _anthropic_child_main,
    _anthropic_final_text,
    _anthropic_request_dump_dir,
    _anthropic_thinking_config,
    _anthropic_tool_definition,
    _anthropic_user_content,
    _build_context_function_tool,
    _context_image_blocks,
    _image_file_payload,
    MAX_PROVIDER_IMAGE_BYTES,
    _build_provider_function_tools,
    _provider_reasoning_effort,
    _provider_spawn_bootstrap_environment,
    _provider_spawn_python_executable,
    _provider_subprocess_smoke,
    _run_agents_subprocess,
    _temporary_openai_env,
    _write_anthropic_request_dump,
)
from VibeCADSession import (
    choose_provider,
    _continuation_prompt,
    _reference_image_lines,
    _session_prompt_preamble,
    provider_safe_tool_schemas,
)

from vibecad_tests.support import (
    _fake_anthropic_module,
    _temporary_design_project,
)

class TestVibeCADAnthropicProvider(unittest.TestCase):
    """Unit tests for the native Anthropic Messages API provider loop."""

    _MINIMAL_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x00\x03\x00\x01\x9f\xbb\xd3\x1f\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def test_anthropic_thinking_config_maps_reasoning_effort(self):
        self.assertIsNone(_anthropic_thinking_config(None))
        self.assertIsNone(_anthropic_thinking_config(""))
        self.assertIsNone(_anthropic_thinking_config("none"))
        self.assertIsNone(_anthropic_thinking_config("not-real"))
        for effort, budget in ANTHROPIC_THINKING_BUDGETS.items():
            config = _anthropic_thinking_config(effort)
            self.assertEqual(config["type"], "enabled")
            self.assertEqual(config["budget_tokens"], budget)
        self.assertEqual(
            _anthropic_thinking_config("HIGH")["budget_tokens"],
            ANTHROPIC_THINKING_BUDGETS["high"],
        )

    def test_anthropic_final_text_joins_text_blocks_only(self):
        blocks = [
            types.SimpleNamespace(type="thinking", thinking="internal"),
            types.SimpleNamespace(type="text", text="First part."),
            types.SimpleNamespace(type="tool_use", name="x", id="1", input={}),
            {"type": "text", "text": "Second part."},
        ]
        self.assertEqual(
            _anthropic_final_text(blocks), "First part.\n\nSecond part."
        )
        self.assertEqual(_anthropic_final_text([]), "")

    def test_all_registered_tools_convert_to_anthropic_tool_shape(self):
        service = VibeCADService()
        schemas = provider_safe_tool_schemas(service, apply_workbench_allowlist=False)
        self.assertGreaterEqual(len(schemas), 60)
        context = {"provider_tool_schemas": schemas}
        tools = _build_provider_function_tools(
            context, None, _AnthropicFunctionTool
        )
        tools.append(_build_context_function_tool(context, _AnthropicFunctionTool))
        self.assertEqual(len(tools), len(schemas) + 1)
        name_pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
        seen_names = set()
        for tool in tools:
            definition = _anthropic_tool_definition(tool)
            self.assertEqual(
                set(definition), {"name", "description", "input_schema"}
            )
            self.assertRegex(definition["name"], name_pattern)
            self.assertNotIn(definition["name"], seen_names)
            seen_names.add(definition["name"])
            self.assertTrue(definition["description"].strip())
            schema = definition["input_schema"]
            self.assertIsInstance(schema, dict)
            self.assertEqual(schema.get("type"), "object")
            self.assertIsInstance(schema.get("properties"), dict)
            json.dumps(definition)

    def test_anthropic_user_content_embeds_screenshot_image_block(self):
        self.assertEqual(_anthropic_user_content("plain prompt", {}), "plain prompt")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "viewport.png"
            path.write_bytes(self._MINIMAL_PNG)
            context = {
                "view_screenshot": {"captured": True, "path": str(path)}
            }
            content = _anthropic_user_content("look at this", context)
            self.assertIsInstance(content, list)
            self.assertEqual(content[0], {"type": "text", "text": "look at this"})
            label_block = content[1]
            self.assertEqual(label_block["type"], "text")
            self.assertTrue(label_block["text"].startswith("CURRENT VIEWPORT"))
            image_block = content[2]
            self.assertEqual(image_block["type"], "image")
            self.assertEqual(image_block["source"]["type"], "base64")
            self.assertEqual(image_block["source"]["media_type"], "image/png")
            self.assertTrue(image_block["source"]["data"])

    def test_anthropic_request_dump_writes_payload_and_latest(self):
        old_dump_dir = os.environ.get(ANTHROPIC_REQUEST_DUMP_DIR_ENV)
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ[ANTHROPIC_REQUEST_DUMP_DIR_ENV] = directory
                self.assertEqual(_anthropic_request_dump_dir(), Path(directory))
                path = _write_anthropic_request_dump(
                    {
                        "schema": "vibecad-anthropic-request-v1",
                        "model": DEFAULT_ANTHROPIC_MODEL,
                        "tools": [{"name": "core_get_active_document"}],
                    }
                )
                self.assertIsNotNone(path)
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertEqual(data["schema"], "vibecad-anthropic-request-v1")
                self.assertEqual(data["model"], DEFAULT_ANTHROPIC_MODEL)
                latest = Path(directory) / "latest-anthropic-request.json"
                self.assertTrue(latest.is_file())
        finally:
            if old_dump_dir is None:
                os.environ.pop(ANTHROPIC_REQUEST_DUMP_DIR_ENV, None)
            else:
                os.environ[ANTHROPIC_REQUEST_DUMP_DIR_ENV] = old_dump_dir

    def _run_anthropic_subprocess(self, fake_module, tool_runner, max_turns=5):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("fork start method unavailable")
        schema = {
            "name": "core.get_active_document",
            "description": "Get the active document.",
            "parameters": {"type": "object", "properties": {}},
            "workbench": "global",
            "safety": "read",
        }
        context = {"provider_tool_schemas": [schema]}
        original = sys.modules.get("anthropic")
        sys.modules["anthropic"] = fake_module
        old_dump_dir = os.environ.get(ANTHROPIC_REQUEST_DUMP_DIR_ENV)
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ[ANTHROPIC_REQUEST_DUMP_DIR_ENV] = directory
                return _run_agents_subprocess(
                    prompt="check the active document",
                    context=context,
                    tool_runner=tool_runner,
                    model="claude-sonnet-5",
                    api_key="sk-ant-test123456",
                    reasoning_effort=None,
                    timeout_seconds=60,
                    max_turns=max_turns,
                    clear_inherited_modules=False,
                    child_main=_anthropic_child_main,
                    provider_label="Anthropic provider",
                )
        finally:
            if old_dump_dir is None:
                os.environ.pop(ANTHROPIC_REQUEST_DUMP_DIR_ENV, None)
            else:
                os.environ[ANTHROPIC_REQUEST_DUMP_DIR_ENV] = old_dump_dir
            if original is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = original

    def test_anthropic_loop_completes_tool_round_trip_over_real_pipe(self):
        tool_calls = []

        def tool_runner(tool_name, arguments_json):
            tool_calls.append((tool_name, arguments_json))
            return {"ok": True, "document": "UnitTestDoc"}

        result = self._run_anthropic_subprocess(
            _fake_anthropic_module(
                "core_get_active_document", final_text="Bridge round-trip OK."
            ),
            tool_runner,
        )
        self.assertEqual(result.final_output, "Bridge round-trip OK.")
        self.assertEqual(tool_calls, [("core.get_active_document", "{}")])

    def test_anthropic_loop_reports_max_turns_exceeded(self):
        def tool_runner(_tool_name, _arguments_json):
            return {"ok": True}

        with self.assertRaises(ProviderUnavailable) as caught:
            self._run_anthropic_subprocess(
                _fake_anthropic_module(
                    "core_get_active_document", always_tool_use=True
                ),
                tool_runner,
                max_turns=2,
            )
        self.assertIn("maximum of 2 turns", str(caught.exception))

    def test_anthropic_provider_defaults_match_preferences(self):
        provider = AnthropicProvider()
        self.assertEqual(provider.model, DEFAULT_ANTHROPIC_MODEL)
        self.assertEqual(provider.reasoning_effort, "high")
        self.assertIsNone(provider.base_url)
        configured = AnthropicProvider(
            model="claude-sonnet-5",
            api_key="sk-ant-test",
            reasoning_effort="medium",
            base_url="http://localhost:9000",
        )
        self.assertEqual(configured.model, "claude-sonnet-5")
        self.assertEqual(configured.api_key, "sk-ant-test")
        self.assertEqual(configured.reasoning_effort, "medium")
        self.assertEqual(configured.base_url, "http://localhost:9000")

    def test_provider_reasoning_effort_none_disables_reasoning_payload(self):
        for value in (None, "", "none", "None", "off", "disabled", "false", "0"):
            self.assertIsNone(_provider_reasoning_effort(value))
        self.assertEqual(_provider_reasoning_effort("LOW"), "low")
        self.assertEqual(_provider_reasoning_effort(" high "), "high")

    def test_provider_spawn_python_executable_prefers_adjacent_python_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            freecad_exe = directory / "FreeCAD.exe"
            python_exe = directory / "python.exe"
            freecad_exe.write_text("", encoding="utf-8")
            python_exe.write_text("", encoding="utf-8")
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
                sys, "executable", str(freecad_exe)
            ):
                self.assertEqual(_provider_spawn_python_executable(), str(python_exe))

    def test_provider_spawn_bootstrap_uses_python_when_freecad_appears_frozen(self):
        from multiprocessing import spawn

        original_executable = spawn.get_executable()
        sentinel = object()
        original_frozen = getattr(sys, "frozen", sentinel)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                python_exe = Path(tmp) / "python.exe"
                python_exe.write_text("", encoding="utf-8")
                multiprocessing.set_executable(str(python_exe))
                with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
                    sys, "executable", "FreeCADCmd.exe"
                ):
                    sys.frozen = True
                    frozen_cmd = spawn.get_command_line(pipe_handle=1)
                    self.assertEqual(frozen_cmd[0], "FreeCADCmd.exe")

                    with _provider_spawn_bootstrap_environment():
                        spawn_cmd = spawn.get_command_line(pipe_handle=1)

                    self.assertEqual(os.fsdecode(spawn_cmd[0]), str(python_exe))
                    self.assertIn("spawn_main", " ".join(map(str, spawn_cmd)))
                    self.assertTrue(getattr(sys, "frozen"))
        finally:
            multiprocessing.set_executable(original_executable)
            if original_frozen is sentinel:
                try:
                    delattr(sys, "frozen")
                except Exception:
                    pass
            else:
                sys.frozen = original_frozen

    def test_provider_subprocess_smoke_completes(self):
        _provider_subprocess_smoke()


class TestVibeCADProviderBaseUrl(unittest.TestCase):
    """Base URL overrides for provider constructors and the OpenAI env bridge."""

    def test_openai_provider_stores_base_url(self):
        self.assertIsNone(OpenAIAgentsProvider().base_url)
        provider = OpenAIAgentsProvider(base_url="http://localhost:8000/v1")
        self.assertEqual(provider.base_url, "http://localhost:8000/v1")

    def test_temporary_openai_env_sets_and_restores_overrides(self):
        old_key = os.environ.get("OPENAI_API_KEY")
        old_base = os.environ.get("OPENAI_BASE_URL")
        try:
            os.environ["OPENAI_API_KEY"] = "sk-original"
            os.environ.pop("OPENAI_BASE_URL", None)
            with _temporary_openai_env("sk-override", "http://localhost:8000/v1"):
                self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-override")
                self.assertEqual(
                    os.environ["OPENAI_BASE_URL"], "http://localhost:8000/v1"
                )
            self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-original")
            self.assertNotIn("OPENAI_BASE_URL", os.environ)

            # Key-only override leaves OPENAI_BASE_URL untouched.
            os.environ["OPENAI_BASE_URL"] = "http://pre-existing:1234/v1"
            with _temporary_openai_env("sk-override", None):
                self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-override")
                self.assertEqual(
                    os.environ["OPENAI_BASE_URL"], "http://pre-existing:1234/v1"
                )
            self.assertEqual(
                os.environ["OPENAI_BASE_URL"], "http://pre-existing:1234/v1"
            )

            # No overrides at all is a no-op.
            with _temporary_openai_env(None, None):
                self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-original")
        finally:
            for name, value in (
                ("OPENAI_API_KEY", old_key),
                ("OPENAI_BASE_URL", old_base),
            ):
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_temporary_openai_env_restores_on_exception(self):
        old_base = os.environ.get("OPENAI_BASE_URL")
        try:
            os.environ.pop("OPENAI_BASE_URL", None)
            with self.assertRaises(RuntimeError):
                with _temporary_openai_env(None, "http://localhost:8000/v1"):
                    self.assertEqual(
                        os.environ["OPENAI_BASE_URL"], "http://localhost:8000/v1"
                    )
                    raise RuntimeError("boom")
            self.assertNotIn("OPENAI_BASE_URL", os.environ)
        finally:
            if old_base is None:
                os.environ.pop("OPENAI_BASE_URL", None)
            else:
                os.environ["OPENAI_BASE_URL"] = old_base


class _ProviderDispatchStubService:
    """Minimal stand-in for VibeCADService used by choose_provider tests."""

    def __init__(self, provider_name, model, can_call=True, base_url=None):
        self._provider_name = provider_name
        self._model = model
        self._can_call = can_call
        self._base_url = base_url

    def auth_state(self):
        if self._can_call:
            return AuthState(AuthStatus.CONFIGURED_UNVERIFIED, source="unit-test")
        return AuthState(AuthStatus.NOT_CONFIGURED)

    def provider_name(self):
        return self._provider_name

    def provider_model(self):
        return self._model

    def provider_api_key(self):
        return "sk-unit-test-key"

    def provider_reasoning_effort(self):
        return "medium"

    def provider_base_url(self):
        return self._base_url


class TestVibeCADProviderDispatch(unittest.TestCase):
    def test_choose_provider_dispatches_anthropic_preference(self):
        provider = choose_provider(
            _ProviderDispatchStubService("anthropic", "claude-sonnet-5")
        )
        self.assertIsInstance(provider, AnthropicProvider)
        self.assertEqual(provider.model, "claude-sonnet-5")
        self.assertEqual(provider.api_key, "sk-unit-test-key")
        self.assertEqual(provider.reasoning_effort, "medium")
        self.assertIsNone(provider.base_url)

    def test_choose_provider_dispatches_openai_preference(self):
        provider = choose_provider(
            _ProviderDispatchStubService("openai", "gpt-5.5")
        )
        self.assertIsInstance(provider, OpenAIAgentsProvider)
        self.assertEqual(provider.model, "gpt-5.5")
        self.assertEqual(provider.api_key, "sk-unit-test-key")
        self.assertIsNone(provider.base_url)

    def test_choose_provider_passes_configured_base_url(self):
        anthropic = choose_provider(
            _ProviderDispatchStubService(
                "anthropic",
                "claude-sonnet-5",
                base_url="http://localhost:9000",
            )
        )
        self.assertIsInstance(anthropic, AnthropicProvider)
        self.assertEqual(anthropic.base_url, "http://localhost:9000")

        openai = choose_provider(
            _ProviderDispatchStubService(
                "openai",
                "gpt-5.5",
                base_url="http://localhost:8000/v1",
            )
        )
        self.assertIsInstance(openai, OpenAIAgentsProvider)
        self.assertEqual(openai.base_url, "http://localhost:8000/v1")

    def test_choose_provider_falls_back_to_offline(self):
        offline_by_preference = choose_provider(
            _ProviderDispatchStubService("anthropic", "claude-sonnet-5"),
            prefer_online=False,
        )
        self.assertIsInstance(offline_by_preference, OfflineProvider)

        offline_by_auth = choose_provider(
            _ProviderDispatchStubService("anthropic", "claude-sonnet-5", can_call=False)
        )
        self.assertIsInstance(offline_by_auth, OfflineProvider)


class TestVibeCADReferenceImages(unittest.TestCase):
    """Reference-image attachment lifecycle, payload labeling, and steering."""

    _MINIMAL_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x00\x03\x00\x01\x9f\xbb\xd3\x1f\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def _service_with_reference_dir(self, root: Path) -> VibeCADService:
        service = VibeCADService()
        reference_dir = root / "artifacts" / "references"
        service._reference_artifact_dir = lambda: reference_dir  # noqa: SLF001
        return service

    def _write_png(self, directory: Path, name: str = "bracket.png") -> Path:
        path = directory / name
        path.write_bytes(self._MINIMAL_PNG)
        return path

    # --- attachment lifecycle -------------------------------------------------

    def test_attach_remove_clear_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self._service_with_reference_dir(root)
            source = self._write_png(root)

            attached = service.attach_reference_image(str(source), label="target bracket")
            self.assertTrue(attached["ok"])
            reference = attached["reference"]
            self.assertEqual(reference["name"], "bracket.png")
            self.assertEqual(reference["label"], "target bracket")
            self.assertEqual(reference["artifact_role"], "user_reference")
            copied = Path(reference["path"])
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.parent, root / "artifacts" / "references")
            self.assertTrue(source.is_file(), "source file must remain untouched")

            summary = service.reference_images_summary()
            self.assertEqual(summary["count"], 1)
            self.assertEqual(summary["images"][0]["id"], reference["id"])

            removed = service.remove_reference_image(reference["id"])
            self.assertTrue(removed["ok"])
            self.assertEqual(removed["count"], 0)
            self.assertEqual(service.reference_images_summary()["count"], 0)

            service.attach_reference_image(str(source))
            service.attach_reference_image(str(source))
            cleared = service.clear_reference_images()
            self.assertTrue(cleared["ok"])
            self.assertEqual(cleared["cleared"], 2)
            self.assertEqual(service.reference_images_summary()["count"], 0)

    def test_attach_rejects_bad_input_with_structured_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self._service_with_reference_dir(root)

            empty = service.attach_reference_image("")
            self.assertFalse(empty["ok"])
            self.assertIn("empty", empty["error"].lower())

            missing = service.attach_reference_image(str(root / "no-such-file.png"))
            self.assertFalse(missing["ok"])
            self.assertIn("not found", missing["error"])

            unsupported_path = root / "model.step"
            unsupported_path.write_bytes(b"not an image")
            unsupported = service.attach_reference_image(str(unsupported_path))
            self.assertFalse(unsupported["ok"])
            self.assertIn("Unsupported", unsupported["error"])

            bad_remove = service.remove_reference_image("not-an-id")
            self.assertFalse(bad_remove["ok"])

    def test_attach_enforces_max_reference_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self._service_with_reference_dir(root)
            source = self._write_png(root)
            for _ in range(MAX_REFERENCE_IMAGES):
                self.assertTrue(service.attach_reference_image(str(source))["ok"])
            overflow = service.attach_reference_image(str(source))
            self.assertFalse(overflow["ok"])
            self.assertIn(str(MAX_REFERENCE_IMAGES), overflow["error"])
            self.assertEqual(
                service.reference_images_summary()["count"], MAX_REFERENCE_IMAGES
            )

    def test_reference_images_appear_in_provider_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self._service_with_reference_dir(root)
            source = self._write_png(root)
            service.attach_reference_image(str(source), label="front view")
            context = service.provider_context_summary()
            references = context.get("reference_images")
            self.assertIsInstance(references, dict)
            self.assertEqual(references["count"], 1)
            self.assertEqual(references["images"][0]["name"], "bracket.png")
            self.assertEqual(references["images"][0]["label"], "front view")

    def test_clear_local_session_clears_reference_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self._service_with_reference_dir(root)
            with _temporary_design_project(service):
                source = self._write_png(root)
                service.attach_reference_image(str(source))
                result = service.registry.call("core.clear_local_session")
                self.assertTrue(result["ok"])
                self.assertEqual(result["reference_images_cleared"], 1)
                self.assertEqual(service.reference_images_summary()["count"], 0)

    # --- payload building and labeling ---------------------------------------

    def _context_with_images(
        self, directory: Path, reference_names: list[str], screenshot: bool
    ) -> dict:
        images = []
        for name in reference_names:
            path = directory / name
            if not path.exists():
                path.write_bytes(self._MINIMAL_PNG)
            images.append({"name": name, "label": "", "path": str(path)})
        context: dict = {"reference_images": {"count": len(images), "images": images}}
        if screenshot:
            shot = directory / "viewport.png"
            shot.write_bytes(self._MINIMAL_PNG)
            context["view_screenshot"] = {"captured": True, "path": str(shot)}
        return context

    def test_context_image_blocks_orders_references_before_viewport(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context = self._context_with_images(
                directory, ["front.png", "side.png"], screenshot=True
            )
            context["reference_images"]["images"][1]["label"] = "side profile"
            blocks = _context_image_blocks(context)
            self.assertEqual(len(blocks), 3)
            self.assertIn('REFERENCE (user-supplied, image 1 of 2): "front.png"', blocks[0][0])
            self.assertIn("TARGET", blocks[0][0])
            self.assertIn('REFERENCE (user-supplied, image 2 of 2): "side.png"', blocks[1][0])
            self.assertIn("side profile", blocks[1][0])
            self.assertTrue(blocks[2][0].startswith("CURRENT VIEWPORT"))
            for _, mime_type, image_data in blocks:
                self.assertEqual(mime_type, "image/png")
                self.assertTrue(image_data)

    def test_context_image_blocks_skips_missing_and_oversize_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context = self._context_with_images(directory, ["good.png"], screenshot=False)
            oversize = directory / "huge.png"
            oversize.write_bytes(b"\x00" * (MAX_PROVIDER_IMAGE_BYTES + 1))
            context["reference_images"]["images"].extend(
                [
                    {"name": "huge.png", "label": "", "path": str(oversize)},
                    {"name": "gone.png", "label": "", "path": str(directory / "gone.png")},
                ]
            )
            blocks = _context_image_blocks(context)
            self.assertEqual(len(blocks), 1)
            self.assertIn('image 1 of 1): "good.png"', blocks[0][0])

    def test_image_file_payload_rejects_unusable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertIsNone(_image_file_payload(None))
            self.assertIsNone(_image_file_payload(""))
            self.assertIsNone(_image_file_payload(str(directory / "missing.png")))
            empty = directory / "empty.png"
            empty.write_bytes(b"")
            self.assertIsNone(_image_file_payload(str(empty)))
            unsupported = directory / "drawing.bmp"
            unsupported.write_bytes(self._MINIMAL_PNG)
            self.assertIsNone(_image_file_payload(str(unsupported)))
            good = self._write_png(directory)
            payload = _image_file_payload(str(good))
            self.assertIsNotNone(payload)
            self.assertEqual(payload[0], "image/png")

    def test_agents_input_labels_references_and_viewport(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context = self._context_with_images(
                directory, ["front.png"], screenshot=True
            )
            result = _agents_input_from_context("make this bracket", context)
            self.assertIsInstance(result, list)
            content = result[0]["content"]
            self.assertEqual(
                content[0], {"type": "input_text", "text": "make this bracket"}
            )
            self.assertEqual(content[1]["type"], "input_text")
            self.assertIn("REFERENCE (user-supplied, image 1 of 1)", content[1]["text"])
            self.assertEqual(content[2]["type"], "input_image")
            self.assertTrue(content[2]["image_url"].startswith("data:image/png;base64,"))
            self.assertEqual(content[3]["type"], "input_text")
            self.assertTrue(content[3]["text"].startswith("CURRENT VIEWPORT"))
            self.assertEqual(content[4]["type"], "input_image")
            self.assertEqual(len(content), 5)

    def test_anthropic_content_labels_references_and_viewport(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context = self._context_with_images(
                directory, ["front.png", "side.png"], screenshot=True
            )
            content = _anthropic_user_content("make this bracket", context)
            self.assertIsInstance(content, list)
            self.assertEqual(content[0], {"type": "text", "text": "make this bracket"})
            self.assertIn("image 1 of 2", content[1]["text"])
            self.assertEqual(content[2]["type"], "image")
            self.assertEqual(content[2]["source"]["media_type"], "image/png")
            self.assertIn("image 2 of 2", content[3]["text"])
            self.assertEqual(content[4]["type"], "image")
            self.assertTrue(content[5]["text"].startswith("CURRENT VIEWPORT"))
            self.assertEqual(content[6]["type"], "image")
            self.assertEqual(len(content), 7)

    def test_formatters_return_plain_prompt_without_usable_images(self):
        context = {
            "reference_images": {"count": 0, "images": []},
            "view_screenshot": {"captured": False, "path": None},
        }
        self.assertEqual(_agents_input_from_context("plain", context), "plain")
        self.assertEqual(_anthropic_user_content("plain", context), "plain")

    def test_formatters_handle_references_without_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context = self._context_with_images(directory, ["only.png"], screenshot=False)
            agents = _agents_input_from_context("build it", context)
            self.assertIsInstance(agents, list)
            texts = [
                item["text"]
                for item in agents[0]["content"]
                if item["type"] == "input_text"
            ]
            self.assertFalse(any(text.startswith("CURRENT VIEWPORT") for text in texts))
            anthropic = _anthropic_user_content("build it", context)
            self.assertIsInstance(anthropic, list)
            block_texts = [
                item["text"] for item in anthropic if item["type"] == "text"
            ]
            self.assertFalse(
                any(text.startswith("CURRENT VIEWPORT") for text in block_texts)
            )

    # --- session steering -----------------------------------------------------

    def _reference_context(self, entries: list[dict]) -> dict:
        return {"reference_images": {"count": len(entries), "images": entries}}

    def test_preamble_has_no_reference_block_without_references(self):
        preamble = _session_prompt_preamble({})
        self.assertNotIn("reference images:", preamble)
        self.assertNotIn("TARGET design", preamble)
        self.assertEqual(
            preamble, _session_prompt_preamble({"reference_images": {"images": []}})
        )

    def test_preamble_reference_block_lists_names_and_scale_steering(self):
        context = self._reference_context(
            [
                {"name": "bracket.png", "label": "front view"},
                {"name": "photo.jpg", "label": ""},
            ]
        )
        preamble = _session_prompt_preamble(context)
        self.assertIn("the user attached 2 reference images", preamble)
        self.assertIn("TARGET design", preamble)
        self.assertIn("not current document geometry", preamble)
        self.assertIn("anchor scale", preamble)
        self.assertIn("state every dimensional assumption", preamble)
        self.assertIn("ask the user for one anchor dimension", preamble)
        self.assertIn("CURRENT VIEWPORT", preamble)
        self.assertIn("reference 1: bracket.png — front view", preamble)
        self.assertIn("reference 2: photo.jpg", preamble)

    def test_preamble_reference_block_uses_singular_grammar(self):
        context = self._reference_context([{"name": "one.png", "label": ""}])
        preamble = _session_prompt_preamble(context)
        self.assertIn("the user attached 1 reference image ", preamble)
        self.assertNotIn("1 reference images", preamble)

    def test_reference_image_lines_ignores_malformed_context(self):
        self.assertEqual(_reference_image_lines({}), [])
        self.assertEqual(_reference_image_lines({"reference_images": None}), [])
        self.assertEqual(_reference_image_lines({"reference_images": "bogus"}), [])
        self.assertEqual(
            _reference_image_lines({"reference_images": {"images": "bogus"}}), []
        )
        lines = _reference_image_lines(
            {"reference_images": {"images": ["junk", {"name": "ok.png"}]}}
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("ok.png", lines[0])

    def test_continuation_prompt_lists_references_only_when_attached(self):
        without = _continuation_prompt("build a bracket", ["previous output"], {}, [])
        self.assertNotIn("Attached user reference images", without)

        context = self._reference_context(
            [
                {"name": "bracket.png", "label": "front view"},
                {"name": "photo.jpg", "label": ""},
            ]
        )
        with_refs = _continuation_prompt(
            "build a bracket", ["previous output"], context, []
        )
        self.assertIn("Attached user reference images (2)", with_refs)
        self.assertIn("bracket.png", with_refs)
        self.assertIn("photo.jpg", with_refs)
        self.assertIn("TARGET design", with_refs)
