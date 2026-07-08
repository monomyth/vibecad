# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import json
from pathlib import Path
import tempfile

import VibeCADProject
from VibeCADCore import (
    VibeCADService,
)
from VibeCADPreferences import (
    DEFAULT_MODEL,
    VibeCADSettings,
    save_settings,
)
from VibeCADProvider import (
    OPENAI_REQUEST_DUMP_DIR_ENV,
    VIBECAD_SYSTEM_INSTRUCTIONS,
    _build_provider_function_tools,
    _model_visible_context,
    _openai_request_dump_dir,
    _provider_tool_request_schema,
    _write_openai_request_dump,
)
from VibeCADSession import (
    _refresh_provider_context,
    make_provider_tool_runner,
    provider_tool_scope_for_context,
    provider_safe_tool_schemas,
    run_prompt,
)
from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS
from provider_tools.base import (
    PROVIDER_FUNCTION_NAMES,
    PROVIDER_TOOL_DESCRIPTIONS,
    _compact_provider_result,
)

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
    _attach_temp_project_store,
)


class TestVibeCADProviderPayloads(SettingsSnapshotTestCase):
    def test_system_prompt_is_compact_production_cad_contract(self):
        self.assertLessEqual(len(VIBECAD_SYSTEM_INSTRUCTIONS), 620)
        self.assertNotIn("Before first geometry write", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertNotIn("state the customer's intended outcome", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertNotIn("Before geometry writes", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertIn("expert mechanical CAD", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertIn("manufacturable geometry", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertIn("fits, load paths", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertIn("No placeholders", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertIn("Fillets/chamfers finish edges", VIBECAD_SYSTEM_INSTRUCTIONS)
        self.assertIn("curves are authored curves", VIBECAD_SYSTEM_INSTRUCTIONS)

    def test_provider_tool_descriptions_are_single_compact_terms(self):
        descriptions = list(PROVIDER_TOOL_DESCRIPTIONS.values())
        self.assertLessEqual(sum(len(item) for item in descriptions), 425)
        self.assertLessEqual(max(len(item) for item in descriptions), 10)
        self.assertFalse([item for item in descriptions if " " in item])

    def test_provider_function_names_are_readable_canonical_tool_names(self):
        names = list(PROVIDER_FUNCTION_NAMES.values())
        self.assertEqual(
            PROVIDER_FUNCTION_NAMES["partdesign.extrude"],
            "partdesign_extrude",
        )
        self.assertEqual(
            PROVIDER_FUNCTION_NAMES["sketcher.add_constraint"],
            "sketcher_add_constraint",
        )
        self.assertEqual(
            PROVIDER_FUNCTION_NAMES["core.get_report_view_errors"],
            "core_get_report_view_errors",
        )
        self.assertLessEqual(max(len(item) for item in names), 64)
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse([item for item in names if " " in item or "." in item])

    def test_provider_tool_results_keep_readable_canonical_keys(self):
        long_reason = " ".join(["profile validation explanation"] * 40)
        compact = _compact_provider_result(
            "partdesign.extrude",
            {
                "ok": True,
                "error": long_reason,
                "feature_effect": {
                    "ok": True,
                    "body_shape_delta": {
                        "volume_delta": 12.5,
                        "faces_delta": 2,
                    },
                },
                "profile_status": {
                    "fully_constrained": True,
                    "degrees_of_freedom": 0,
                    "ready_for_pad": True,
                    "geometry_count": 4,
                    "constraint_count": 8,
                },
                "mutation": {
                    "created_geometry_indices": [0, 1, 2, 3],
                    "created_constraint_indices": [0, 1, 2, 3],
                },
                "transaction": {
                    "ok": True,
                    "mutated_document": True,
                    "result": {"feature": "Pad"},
                },
            },
        )
        self.assertIn("feature_effect", compact)
        self.assertIn("body_shape_delta", compact["feature_effect"])
        self.assertEqual(
            compact["feature_effect"]["body_shape_delta"]["volume_delta"],
            12.5,
        )
        self.assertEqual(compact["profile_status"]["degrees_of_freedom"], 0)
        self.assertTrue(compact["profile_status"]["fully_constrained"])
        self.assertEqual(compact["profile_status"]["geometry_count"], 4)
        self.assertEqual(compact["profile_status"]["constraint_count"], 8)
        self.assertEqual(
            compact["mutation"]["created_geometry_indices"],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            compact["mutation"]["created_constraint_indices"],
            [0, 1, 2, 3],
        )
        self.assertLessEqual(len(compact["error"]), 480)
        self.assertNotIn("fx", compact)
        self.assertNotIn("dV", str(compact))
        self.assertNotIn("g_new", str(compact))
        self.assertNotIn("edit", compact)

    def test_sketch_inspect_result_does_not_silently_truncate_geometry(self):
        geometry = [
            {
                "index": index,
                "handle": f"geometry:{index}",
                "type": "LineSegment",
            }
            for index in range(8)
        ]
        compact = _compact_provider_result(
            "sketcher.inspect_sketch",
            {
                "ok": True,
                "geometry_count": 8,
                "geometry": geometry,
            },
        )
        self.assertEqual(compact["geometry_count"], 8)
        self.assertEqual(len(compact["geometry"]), 8)
        self.assertEqual(compact["geometry"][-1]["index"], 7)

    def test_run_prompt_includes_provider_tool_schemas(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = VibeCADService()
            original = _attach_temp_project_store(service, Path(tmp))
            try:
                response = run_prompt("hello", service=service, prefer_online=False)
                self.assertEqual(response.provider, "OfflineProvider")
                self.assertNotIn("available_tools", response.context)
                self.assertIn("provider_tool_schemas", response.context)
                provider_tool_names = {
                    schema["name"]
                    for schema in response.context["provider_tool_schemas"]
                    if isinstance(schema, dict)
                }
                self.assertIn("cad.inspect_state", provider_tool_names)
                self.assertIn("cad.define_component", provider_tool_names)
                self.assertIn("cad.create_profile", provider_tool_names)
                self.assertIn("cad.create_feature", provider_tool_names)
                self.assertIn("cad.verify_design", provider_tool_names)
                self.assertNotIn("core.get_active_document", provider_tool_names)
                self.assertNotIn("core.enter_workspace", provider_tool_names)
                self.assertNotIn("core.activate_workbench", provider_tool_names)
                self.assertNotIn("partdesign.create_body", provider_tool_names)
                self.assertNotIn("sketcher.add_geometry", provider_tool_names)
                self.assertIn("provider_tool_scope", response.context)
                self.assertEqual(
                    response.context["provider_tool_scope"]["stage"], "ai_native_cad"
                )
                self.assertIn("active_tool_count", response.context["provider_tool_scope"])
                self.assertNotIn("active_tool_names", response.context["provider_tool_scope"])
                self.assertNotIn("omitted_tool_names", response.context["provider_tool_scope"])
                self.assertEqual(response.context["vibecad_workspace"]["mode"], "ai_native_cad")
                visible = _model_visible_context(response.context)
                self.assertNotIn("provider_tool_scope", visible)
                self.assertNotIn("provider_tool_schemas", visible)
                self.assertNotIn("provider_tool_surface", visible)
                self.assertNotIn("available_tools", response.context)
                self.assertIn("view_screenshot", response.context)
                self.assertIn("task_panel", response.context)
                self.assertIn("report_view_errors", response.context)
            finally:
                VibeCADProject._active_document_info = original

    def test_modify_existing_request_allows_new_body_creation(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADModifyAllowsBodyTest")
        with tempfile.TemporaryDirectory() as tmp:
            service = VibeCADService()
            original = _attach_temp_project_store(service, Path(tmp), "Modify Allows Body")
            try:
                created = service.registry.call("partdesign.create_body", label="Existing Frame")
                self.assertTrue(created["ok"], created)
                runner = make_provider_tool_runner(
                    service,
                    workbench="PartDesignWorkbench",
                )
                result = runner("partdesign.create_body", '{"label": "Housing Body"}')
                self.assertTrue(result["ok"], result)
            finally:
                VibeCADProject._active_document_info = original
                App.closeDocument(doc.Name)

    def test_modify_existing_prompt_keeps_ai_native_tools_visible(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADModifySurfaceTest")
        with tempfile.TemporaryDirectory() as tmp:
            service = VibeCADService()
            original = _attach_temp_project_store(service, Path(tmp), "Modify Surface")
            try:
                created = service.registry.call("partdesign.create_body", label="Existing Frame")
                self.assertTrue(created["ok"], created)

                context = _refresh_provider_context(
                    service,
                    prompt="fix this model",
                    entered_workspace="PartDesignWorkbench",
                )
                names = {
                    schema["name"]
                    for schema in context["provider_tool_schemas"]
                    if isinstance(schema, dict)
                }

                self.assertNotIn("vibecad_request", context)
                self.assertIn("cad.inspect_state", names)
                self.assertIn("cad.create_profile", names)
                self.assertIn("cad.create_feature", names)
                self.assertIn("cad.verify_design", names)
                self.assertNotIn("core.delete_object", names)
                self.assertNotIn("partdesign.create_sketch", names)
                self.assertNotIn("partdesign.extrude", names)
                scope = context["provider_tool_scope"]
                self.assertNotIn("request_filter", scope)
            finally:
                VibeCADProject._active_document_info = original
                App.closeDocument(doc.Name)

    def test_document_management_tools_remain_blocked_for_provider(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADDocMgmtBlockedTest")
        with tempfile.TemporaryDirectory() as tmp:
            service = VibeCADService()
            original = _attach_temp_project_store(service, Path(tmp), "Doc Mgmt Blocked")
            try:
                runner = make_provider_tool_runner(
                    service,
                    workbench="PartDesignWorkbench",
                )
                result = runner("core.create_new_document", '{"name": "Sneaky"}')
                self.assertFalse(result["ok"])
                self.assertIn("not available to the autonomous CAD loop", result["error"])
            finally:
                VibeCADProject._active_document_info = original
                App.closeDocument(doc.Name)

    def test_openai_provider_request_uses_precise_function_tools_not_generic_dispatcher(self):
        from provider_tools import create_tool

        class FakeFunctionTool:
            def __init__(
                self,
                name,
                description,
                params_json_schema,
                on_invoke_tool,
                strict_json_schema,
            ):
                self.name = name
                self.description = description
                self.params_json_schema = params_json_schema
                self.on_invoke_tool = on_invoke_tool
                self.strict_json_schema = strict_json_schema

        class FakeConn:
            def send(self, _message):
                raise AssertionError("tool should not be invoked while building request schema")

        save_settings(
            VibeCADSettings(
                enable_native_freecad_tools=True,
                native_tool_workbenches=("PartDesignWorkbench",),
            )
        )
        service = VibeCADService()
        schemas = provider_safe_tool_schemas(service, "PartDesignWorkbench")
        selected_names = {
            "cad.inspect_state",
            "partdesign.create_sketch",
            "sketcher.draw_rectangle",
            "partdesign.extrude",
        }
        selected = [schema for schema in schemas if schema["name"] in selected_names]
        self.assertEqual({schema["name"] for schema in selected}, selected_names)

        request_tools = [
            _provider_tool_request_schema(create_tool(schema, FakeConn(), FakeFunctionTool))
            for schema in selected
        ]
        function_names = {tool["function_name"] for tool in request_tools}
        self.assertEqual(
            function_names,
            {
                "cad_inspect_state",
                "partdesign_create_sketch",
                "sketcher_draw_rectangle",
                "partdesign_extrude",
            },
        )
        self.assertNotIn("execute_vibecad_tool", function_names)
        for tool in request_tools:
            self.assertTrue(tool["callable"], tool)
            self.assertIsInstance(tool["description"], str)
            self.assertTrue(tool["description"].strip())
            self.assertIsInstance(tool["params_json_schema"], dict)
            self.assertEqual(tool["params_json_schema"]["type"], "object")
            self.assertIn("properties", tool["params_json_schema"])

    def test_openai_request_tool_list_uses_active_scoped_surface(self):
        class FakeFunctionTool:
            def __init__(
                self,
                name,
                description,
                params_json_schema,
                on_invoke_tool,
                strict_json_schema,
            ):
                self.name = name
                self.description = description
                self.params_json_schema = params_json_schema
                self.on_invoke_tool = on_invoke_tool
                self.strict_json_schema = strict_json_schema

        class FakeConn:
            def send(self, _message):
                raise AssertionError("tool should not be invoked while building request schema")

        save_settings(
            VibeCADSettings(
                enable_native_freecad_tools=True,
                native_tool_workbenches=("PartDesignWorkbench",),
            )
        )
        service = VibeCADService()
        full = provider_safe_tool_schemas(service, "PartDesignWorkbench")
        scope = provider_tool_scope_for_context(service, "PartDesignWorkbench")
        scoped = provider_safe_tool_schemas(
            service,
            "PartDesignWorkbench",
            tool_names=scope.tool_names,
        )
        context = {"provider_tool_schemas": scoped}

        provider_tools = _build_provider_function_tools(context, FakeConn(), FakeFunctionTool)
        request_tools = [_provider_tool_request_schema(tool) for tool in provider_tools]
        function_names = {tool["function_name"] for tool in request_tools}

        self.assertEqual(scope.stage, "native_workbench_pack")
        self.assertEqual(len(request_tools), len(scoped))
        self.assertLessEqual(len(request_tools), len(full))
        self.assertIn("partdesign_create_body", function_names)
        self.assertIn("partdesign_create_sketch", function_names)
        self.assertIn("partdesign_extrude", function_names)
        self.assertNotIn("sketcher_create_sketch", function_names)
        self.assertNotIn("execute_vibecad_tool", function_names)
        self.assertNotIn("provider_function_tools", context)

    def test_provider_tool_builder_rejects_malformed_schema_entries(self):
        class FakeFunctionTool:
            def __init__(
                self,
                name,
                description,
                params_json_schema,
                on_invoke_tool,
                strict_json_schema,
            ):
                self.name = name
                self.description = description
                self.params_json_schema = params_json_schema
                self.on_invoke_tool = on_invoke_tool
                self.strict_json_schema = strict_json_schema

        with self.assertRaisesRegex(ValueError, "schema 0 must be an object"):
            _build_provider_function_tools(
                {"provider_tool_schemas": [None]}, object(), FakeFunctionTool
            )
        with self.assertRaisesRegex(ValueError, "schema 0 is missing name"):
            _build_provider_function_tools(
                {"provider_tool_schemas": [{}]}, object(), FakeFunctionTool
            )

    def test_provider_tool_builder_rejects_duplicate_function_names(self):
        class FakeFunctionTool:
            def __init__(
                self,
                name,
                description,
                params_json_schema,
                on_invoke_tool,
                strict_json_schema,
            ):
                self.name = name
                self.description = description
                self.params_json_schema = params_json_schema
                self.on_invoke_tool = on_invoke_tool
                self.strict_json_schema = strict_json_schema

        service = VibeCADService()
        schemas = provider_safe_tool_schemas(service)
        cad_state = next(schema for schema in schemas if schema["name"] == "cad.inspect_state")
        with self.assertRaisesRegex(
            ValueError,
            "Duplicate provider function name cad_inspect_state",
        ):
            _build_provider_function_tools(
                {"provider_tool_schemas": [cad_state, dict(cad_state)]},
                object(),
                FakeFunctionTool,
            )

    def test_model_visible_context_is_internal_not_provider_tool(self):
        from provider_tools import registered_tool_names

        context = {
            "workbench": "PartDesignWorkbench",
            "provider_tool_schemas": [{"name": "partdesign.create_sketch"}],
            "provider_function_tools": [
                {
                    "tool_name": "cad.inspect_state",
                    "function_name": "cad_inspect_state",
                }
            ],
            "provider_tool_surface": {"tools": ["partdesign.create_sketch"]},
            "conversation": {"messages": [{"role": "user", "content": "make it"}]},
            "document": {"document": "Doc", "objects": []},
        }

        self.assertNotIn("core.get_current_freecad_context", registered_tool_names())
        visible = _model_visible_context(context)
        self.assertIn("document", visible)
        self.assertIn("conversation", visible)
        self.assertNotIn("provider_tool_schemas", visible)
        self.assertNotIn("provider_function_tools", visible)
        self.assertNotIn("provider_tool_surface", visible)

    def test_openai_provider_has_no_inline_function_tool_context_helper(self):
        import VibeCADProvider

        import inspect

        source = inspect.getsource(VibeCADProvider)
        self.assertNotIn("@function_tool", source)
        self.assertNotIn("create_context_tool", source)

    def test_openai_request_dump_writes_full_provider_payload(self):
        old_dump_dir = os.environ.get(OPENAI_REQUEST_DUMP_DIR_ENV)
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ[OPENAI_REQUEST_DUMP_DIR_ENV] = directory
                path = _write_openai_request_dump(
                    {
                        "schema": "vibecad-openai-agents-request-v1",
                        "model": DEFAULT_MODEL,
                        "agent": {
                            "instructions": "full system instructions",
                            "tools": [
                                {
                                    "function_name": "partdesign_create_sketch",
                                    "params_json_schema": {
                                        "type": "object",
                                        "properties": {"plane": {"type": "string"}},
                                    },
                                }
                            ],
                        },
                        "run": {"input": "make a 10mm square"},
                    }
                )
                self.assertIsNotNone(path)
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertEqual(data["model"], DEFAULT_MODEL)
                self.assertEqual(data["agent"]["instructions"], "full system instructions")
                self.assertEqual(
                    data["agent"]["tools"][0]["function_name"],
                    "partdesign_create_sketch",
                )
                self.assertEqual(data["run"]["input"], "make a 10mm square")
                latest = Path(directory) / "latest-openai-request.json"
                self.assertTrue(latest.is_file())
                latest_data = json.loads(latest.read_text(encoding="utf-8"))
                self.assertEqual(latest_data["schema"], "vibecad-openai-agents-request-v1")
        finally:
            if old_dump_dir is None:
                os.environ.pop(OPENAI_REQUEST_DUMP_DIR_ENV, None)
            else:
                os.environ[OPENAI_REQUEST_DUMP_DIR_ENV] = old_dump_dir

    def test_openai_request_dump_defaults_to_durable_user_storage(self):
        old_dump_dir = os.environ.get(OPENAI_REQUEST_DUMP_DIR_ENV)
        old_home = os.environ.get("VIBECAD_HOME")
        try:
            os.environ.pop(OPENAI_REQUEST_DUMP_DIR_ENV, None)
            with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
                home = Path(directory) / "vibecad-home"
                os.environ["VIBECAD_HOME"] = str(home)
                dump_dir = _openai_request_dump_dir()
                self.assertEqual(
                    dump_dir,
                    home / "debug" / "openai-request-dumps",
                )
                path = _write_openai_request_dump(
                    {"schema": "vibecad-openai-agents-request-v1", "model": DEFAULT_MODEL}
                )
                self.assertIsNotNone(path)
                self.assertTrue(str(path).startswith(str(home)))
                self.assertTrue((home / "debug" / "openai-request-dumps" / "latest-openai-request.json").is_file())
        finally:
            if old_dump_dir is None:
                os.environ.pop(OPENAI_REQUEST_DUMP_DIR_ENV, None)
            else:
                os.environ[OPENAI_REQUEST_DUMP_DIR_ENV] = old_dump_dir
            if old_home is None:
                os.environ.pop("VIBECAD_HOME", None)
            else:
                os.environ["VIBECAD_HOME"] = old_home
