# SPDX-License-Identifier: LGPL-2.1-or-later

import importlib
import inspect
import json
import re
from pathlib import Path
import tempfile

from VibeCADCore import (
    VibeCADService,
)
from VibeCADPreferences import (
    VibeCADSettings,
    load_settings,
    save_settings,
)
from VibeCADProvider import (
    OfflineProvider,
    _agents_input_from_context,
    _model_visible_context,
)
from VibeCADSession import (
    _prompt_with_conversation,
    provider_safe_tool_schemas,
)

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
    _temporary_vibecad_home,
)


class TestVibeCADServiceContext(SettingsSnapshotTestCase):
    def test_service_has_core_read_tools(self):
        service = VibeCADService()
        names = set(service.registry.names())

        core_tools = {
            "core.get_active_document",
            "core.get_selection",
            "core.get_view_state",
            "core.get_task_panel",
            "core.wait_for_user_gui_action",
            "core.capture_view_screenshot",
            "core.get_report_view_errors",
            "core.list_workbenches",
            "core.list_registered_commands",
            "core.list_active_workbench_commands",
            "core.activate_workbench",
            "core.enter_workspace",
            "core.get_active_workbench_tool_pack",
            "core.list_workbench_tool_packs",
            "core.list_workbench_object_templates",
            "core.list_workbench_objects",
            "core.get_object_properties",
            "core.get_tool_shape_report",
            "core.report_tool_shape_gap",
            "core.run_workbench_command",
            "core.create_new_document",
            "core.open_document",
            "core.delete_object",
            "core.list_pending_actions",
            "core.apply_action",
            "core.reject_action",
            "core.undo_last_vibecad_action",
            "core.clear_local_session",
        }
        self.assertTrue(core_tools <= names, sorted(core_tools - names))

        sketcher_tools = {
            "sketcher.create_sketch",
            "sketcher.open_sketch",
            "sketcher.close_sketch",
            "sketcher.inspect_sketch",
            "sketcher.add_geometry",
            "sketcher.add_hole_pattern",
            "sketcher.add_slot",
            "sketcher.draw_rectangle",
            "sketcher.add_constraint",
            "sketcher.edit_constraint",
            "sketcher.delete_items",
            "sketcher.modify_geometry",
            "sketcher.transform_geometry",
            "sketcher.move_point",
            "sketcher.resolve_geometry",
            "sketcher.set_geometry_name",
            "sketcher.set_construction",
            "sketcher.add_external_geometry",
            "sketcher.remove_external_geometry",
        }
        self.assertTrue(sketcher_tools <= names, sorted(sketcher_tools - names))

        partdesign_tools = {
            "partdesign.get_bodies",
            "partdesign.find_subelements",
            "partdesign.create_body",
            "partdesign.create_sketch",
            "partdesign.extrude",
            "partdesign.revolve",
            "partdesign.pattern",
            "partdesign.dressup",
            "partdesign.hole_from_sketch",
            "partdesign.loft_profiles",
            "partdesign.sweep_profile",
            "partdesign.helix_profile",
            "partdesign.boolean_bodies",
            "partdesign.create_datum_line",
            "partdesign.create_datum_plane",
            "partdesign.set_feature_dimensions",
            "assembly.check_interference",
        }
        self.assertTrue(partdesign_tools <= names, sorted(partdesign_tools - names))

        other_workbench_tools = {
            "part.set_placement",
            "part.cut_cylindrical_hole",
            "part.dressup",
            "part.thicken_surface",
            "assembly.get_assemblies",
            "assembly.create_assembly",
            "assembly.add_component",
            "assembly.set_component_placement",
            "assembly.check_interference",
            "assembly.ground_component",
            "assembly.create_joint",
            "assembly.solve",
            "techdraw.get_pages",
            "techdraw.create_page",
            "techdraw.add_view",
            "draft.create_array",
            "draft.create_wire",
            "surface.create_surface",
            "material.apply_appearance",
            "spreadsheet.get_sheet",
            "model.build_from_script",
        }
        self.assertTrue(other_workbench_tools <= names, sorted(other_workbench_tools - names))

        # Retired tool families must not resurface.
        retired = {
            "phase.get_project_context",
            "phase.set_current",
            "phase.validate_document",
            "phase.audit_workflow",
            "intent.update_brief",
            "sketcher.add_line",
            "sketcher.add_point",
            "sketcher.add_polyline",
            "sketcher.add_circle",
            "sketcher.add_arc",
            "sketcher.add_ellipse",
            "sketcher.add_bspline",
            "sketcher.constrain_coincident",
            "sketcher.constrain_horizontal",
            "sketcher.constrain_distance",
            "sketcher.constrain_radius",
            "sketcher.set_constraint_value",
            "sketcher.set_constraint_name",
            "sketcher.get_constraint_by_name",
            "sketcher.list_geometry",
            "sketcher.list_constraints",
            "sketcher.list_external_geometry",
            "sketcher.list_reference_geometry",
            "sketcher.get_solver_status",
            "sketcher.validate_profile",
            "sketcher.validate_profile_deep",
            "sketcher.diagnose_constraints",
            "sketcher.delete_geometry",
            "sketcher.delete_constraint",
            "sketcher.delete_all_geometry",
            "sketcher.delete_all_constraints",
            "sketcher.trim_geometry",
            "sketcher.extend_geometry",
            "sketcher.split_geometry",
            "sketcher.fillet_corner",
            "sketcher.copy_geometry",
            "sketcher.mirror_geometry",
            "sketcher.offset_geometry",
            "sketcher.rectangular_array",
            "partdesign.pad_sketch",
            "partdesign.pocket_sketch",
            "partdesign.revolve_sketch",
            "partdesign.groove_sketch",
            "partdesign.linear_pattern",
            "partdesign.polar_pattern",
            "partdesign.mirror_feature",
            "partdesign.fillet_feature",
            "partdesign.chamfer_feature",
            "partdesign.draft_feature",
            "partdesign.thickness_feature",
            "part.apply_fillet",
            "part.apply_chamfer",
            "part.apply_thickness",
            "part.get_objects",
            "draft.get_objects",
            "mesh.get_objects",
            "points.get_objects",
            "material.get_objects",
            "bim.get_objects",
            "inspection.get_objects",
            "openscad.get_objects",
            "surface.get_objects",
            "reverseengineering.get_objects",
            "robot.get_objects",
            "meshpart.get_objects",
            "fem.get_analyses",
            "cam.get_jobs",
        }
        leaked = retired & names
        self.assertFalse(leaked, sorted(leaked))

        # No propose_* approval-queue variants in the registry.
        proposals = {name for name in names if ".propose_" in name}
        self.assertFalse(proposals, sorted(proposals))

    def test_partdesign_tool_implementations_live_in_tool_modules(self):
        partdesign_modules = (
            "partdesign_create_sketch",
            "partdesign_extrude",
            "partdesign_revolve",
            "partdesign_pattern",
            "partdesign_dressup",
            "partdesign_loft_profiles",
            "partdesign_sweep_profile",
            "partdesign_set_feature_dimensions",
            "partdesign_get_bodies",
        )
        for module_name in partdesign_modules:
            with self.subTest(module_name=module_name):
                module = importlib.import_module(f"tool_impl.service.{module_name}")
                self.assertTrue(callable(getattr(module, "run", None)))
                self.assertNotIn("handler", module.TOOL_SPEC)

        core_source = inspect.getsource(VibeCADService)
        self.assertNotIn("def create_partdesign_", core_source)
        self.assertNotIn("def propose_create_partdesign_body", core_source)
        self.assertNotIn("def propose_add_partdesign_box", core_source)

        register_source = inspect.getsource(VibeCADService._register_core_tools)
        self.assertIn("service_tools.register_tools(self._registry, self)", register_source)
        self.assertIn("sketcher_tools.register_tools(self._registry, self)", register_source)
        self.assertNotIn("VibeCADTool", register_source)

    def test_service_tools_all_have_module_run_entrypoints(self):
        from tool_impl import service as service_tools

        core_source = inspect.getsource(VibeCADService)
        self.assertNotIn("def propose_", core_source)
        registrar_source = inspect.getsource(service_tools.register_tools)
        self.assertNotIn("getattr(service", registrar_source)
        for module_name in service_tools.TOOL_MODULE_NAMES:
            with self.subTest(module_name=module_name):
                module = importlib.import_module(f"tool_impl.service.{module_name}")
                self.assertTrue(callable(getattr(module, "run", None)))
                self.assertNotIn("handler", module.TOOL_SPEC)
                module_source = inspect.getsource(module)
                self.assertNotIn("return service.propose_", module_source)
                if module_name != "core_apply_action":
                    self.assertNotRegex(
                        module_source,
                        r"return service\.(create_|add_|apply_|cut_|set_)",
                    )

    def test_service_tool_modules_do_not_delegate_back_to_core_tool_methods(self):
        from tool_impl import service as service_tools

        blocked_core_tool_methods = {
            "activate_workbench",
            "all_workbench_tool_packs",
            "apply_action",
            "assembly_summary",
            "bim_summary",
            "cam_summary",
            "capture_view_screenshot",
            "clear_local_session",
            "command_summary",
            "document_summary",
            "draft_summary",
            "fem_summary",
            "inspection_summary",
            "material_summary",
            "mesh_summary",
            "meshpart_summary",
            "object_property_summary",
            "openscad_summary",
            "part_summary",
            "partdesign_summary",
            "pending_actions",
            "points_summary",
            "reject_action",
            "report_tool_shape_gap",
            "report_view_errors",
            "reverseengineering_summary",
            "robot_summary",
            "run_workbench_command",
            "selection_summary",
            "spreadsheet_summary",
            "surface_summary",
            "task_panel_summary",
            "techdraw_summary",
            "tool_shape_report",
            "undo_last_vibecad_action",
            "view_state",
            "wait_for_user_gui_action",
            "workbench_command_summary",
            "workbench_object_summary",
            "workbench_object_templates",
            "workbench_summary",
            "workbench_tool_pack_summary",
        }
        pattern = re.compile(r"service\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(")
        for module_name in service_tools.TOOL_MODULE_NAMES:
            module = importlib.import_module(f"tool_impl.service.{module_name}")
            module_source = inspect.getsource(module)
            leaked = sorted(
                {
                    match.group("name")
                    for match in pattern.finditer(module_source)
                    if match.group("name") in blocked_core_tool_methods
                }
            )
            self.assertEqual([], leaked, module_name)

    def test_service_tool_modules_do_not_use_shared_runtime_dispatchers(self):
        from tool_impl import service as service_tools

        service_dir = Path(__file__).resolve().parent / "tool_impl" / "service"
        self.assertFalse((service_dir / "core_runtime.py").exists())
        for module_name in service_tools.TOOL_MODULE_NAMES:
            module = importlib.import_module(f"tool_impl.service.{module_name}")
            module_source = inspect.getsource(module)
            self.assertNotIn("core_runtime", module_source, module_name)
            self.assertNotRegex(module_source, r"return\s+domain_runtime\.(?!build_)", module_name)

    def test_partdesign_extrude_tools_do_not_set_deprecated_midplane_property(self):
        for module_name in ("partdesign_extrude",):
            with self.subTest(module_name=module_name):
                module = importlib.import_module(f"tool_impl.service.{module_name}")
                module_source = inspect.getsource(module)
                self.assertNotIn(".Midplane =", module_source)

    def test_context_summary_auth_is_json_safe(self):
        service = VibeCADService()
        auth = service.context_summary()["auth"]
        self.assertIsInstance(auth["status"], str)
        self.assertNotIn("redacted_key", auth)

    def test_context_summary_includes_provider_preferences(self):
        service = VibeCADService()
        provider = service.context_summary()["provider"]
        self.assertIn("model", provider)
        self.assertIn("reasoning_effort", provider)
        self.assertIn("use_online_by_default", provider)

    def test_provider_context_is_scoped_to_active_workbench_domains(self):
        service = VibeCADService()
        service.active_workbench_name = lambda: "PartDesignWorkbench"

        context = service.provider_context_summary()
        self.assertEqual(context["workbench"], "PartDesignWorkbench")
        self.assertIn("partdesign", context)
        self.assertIn("sketcher", context)
        self.assertIn("provider_tool_surface", context)
        self.assertEqual(
            context["provider_tool_surface"]["active_workbench"],
            "PartDesignWorkbench",
        )
        for unrelated in (
            "mesh",
            "points",
            "draft",
            "techdraw",
            "fem",
            "cam",
            "bim",
            "inspection",
            "openscad",
            "surface",
            "reverseengineering",
            "robot",
            "meshpart",
        ):
            self.assertNotIn(unrelated, context)

    def test_model_visible_context_uses_scoped_provider_context(self):
        service = VibeCADService()
        service.active_workbench_name = lambda: "PartDesignWorkbench"
        context = service.provider_context_summary()
        context["provider_tool_schemas"] = provider_safe_tool_schemas(
            service,
            "PartDesignWorkbench",
        )
        context["provider_tool_schemas_workbench"] = "PartDesignWorkbench"

        visible = _model_visible_context(context)
        self.assertIn("partdesign", visible)
        self.assertIn("sketcher", visible)
        self.assertNotIn("provider_tool_schemas", visible)
        self.assertNotIn("mesh", visible)
        self.assertNotIn("bim", visible)
        self.assertNotIn("robot", visible)

    def test_provider_api_key_reads_dotenv_without_exposing_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY='sk-test123456'\n", encoding="utf-8")
            service = VibeCADService(dotenv_path=path)
            self.assertEqual(service.provider_api_key(), "sk-test123456")
            self.assertNotIn("sk-test123456", str(service.context_summary()))

    def test_provider_api_key_reads_preference_dotenv_without_exposing_context(self):
        old_settings = load_settings()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ".env"
                path.write_text("OPENAI_API_KEY='sk-test123456'\n", encoding="utf-8")
                save_settings(VibeCADSettings(dotenv_path=str(path)))
                service = VibeCADService()
                self.assertEqual(service.provider_api_key(), "sk-test123456")
                self.assertNotIn("sk-test123456", str(service.context_summary()))
        finally:
            save_settings(old_settings)

    def test_offline_provider_reports_context(self):
        result = OfflineProvider().run("hello", {"workbench": "SketcherWorkbench"})
        self.assertIn("SketcherWorkbench", result.final_output)

    def test_agents_input_from_context_keeps_text_when_no_screenshot_file(self):
        self.assertEqual(
            _agents_input_from_context(
                "plain prompt",
                {"view_screenshot": {"captured": True, "path": "/tmp/no-such-vibecad-image.png"}},
            ),
            "plain prompt",
        )

    def test_model_visible_context_hides_internal_tool_menu(self):
        visible = _model_visible_context(
            {
                "workbench": "PartDesignWorkbench",
                "available_tools": [{"name": "partdesign.create_sketch"}],
                "available_tools_workbench": "PartDesignWorkbench",
                "provider_tool_schemas": [{"name": "partdesign.create_sketch"}],
                "provider_tool_schemas_workbench": "PartDesignWorkbench",
                "provider_function_tools": [
                    {
                        "tool_name": "partdesign.create_sketch",
                        "function_name": "partdesign_create_sketch",
                    }
                ],
                "provider_tool_surface": {"tools": ["partdesign.create_sketch"]},
                "tool_shape_report": {"provider_visible_tool_count": 1},
            }
        )
        self.assertNotIn("available_tools", visible)
        self.assertNotIn("available_tools_workbench", visible)
        self.assertNotIn("provider_tool_schemas", visible)
        self.assertNotIn("provider_tool_schemas_workbench", visible)
        self.assertNotIn("provider_function_tools", visible)
        self.assertNotIn("provider_tool_surface", visible)
        self.assertNotIn("tool_shape_report", visible)

    def test_conversation_history_is_scoped_to_active_document(self):
        import FreeCAD as App

        for existing in list(App.listDocuments().values()):
            App.closeDocument(existing.Name)
        with _temporary_vibecad_home(), tempfile.TemporaryDirectory() as tmp:
            service = VibeCADService()
            doc_one = App.newDocument("VibeCADScopedConversationOne")
            try:
                doc_one.saveAs(str(Path(tmp) / "scoped-one.FCStd"))
                service.record_conversation_turn("user", "only document one should know this")
                first_history = service.conversation_history()
                self.assertEqual(first_history["scope"]["kind"], "saved_document")
                self.assertEqual(first_history["turn_count"], 1)
                self.assertIn("scoped-one.FCStd", first_history["scope"]["file_path"])

                doc_two = App.newDocument("VibeCADScopedConversationTwo")
                doc_two.saveAs(str(Path(tmp) / "scoped-two.FCStd"))
                App.setActiveDocument(doc_two.Name)
                second_history = service.conversation_history()
                self.assertEqual(second_history["scope"]["kind"], "saved_document")
                self.assertEqual(second_history["conversation"], [])
                self.assertNotIn(
                    "only document one should know this",
                    json.dumps(service.context_summary()["conversation"]),
                )

                App.setActiveDocument(doc_one.Name)
                reloaded = service.conversation_history()
                self.assertEqual(reloaded["turn_count"], 1)
                self.assertEqual(
                    reloaded["conversation"][0]["content"],
                    "only document one should know this",
                )
            finally:
                for document in list(App.listDocuments().values()):
                    App.closeDocument(document.Name)

    def test_unsaved_conversation_history_does_not_leak_between_documents(self):
        import FreeCAD as App

        for existing in list(App.listDocuments().values()):
            App.closeDocument(existing.Name)
        with _temporary_vibecad_home() as home:
            service = VibeCADService()
            App.newDocument("VibeCADUnsavedConversationOne")
            try:
                service.record_conversation_turn("user", "unsaved one memory")
                first_history = service.conversation_history()
                self.assertEqual(first_history["scope"]["kind"], "unsaved_document")
                self.assertTrue(first_history["scope"]["persistent"])
                self.assertTrue(first_history["path"].startswith(str(home)))
                self.assertIn("projects", first_history["path"])
                self.assertEqual(first_history["turn_count"], 1)

                doc_two = App.newDocument("VibeCADUnsavedConversationTwo")
                App.setActiveDocument(doc_two.Name)
                second_history = service.conversation_history()
                self.assertEqual(second_history["scope"]["kind"], "unsaved_document")
                self.assertTrue(second_history["scope"]["persistent"])
                self.assertTrue(second_history["path"].startswith(str(home)))
                self.assertIn("projects", second_history["path"])
                self.assertEqual(second_history["conversation"], [])
                self.assertNotIn(
                    "unsaved one memory",
                    json.dumps(service.context_summary()["conversation"]),
                )
            finally:
                for document in list(App.listDocuments().values()):
                    App.closeDocument(document.Name)

    def test_prompt_with_conversation_marks_memory_as_document_scoped(self):
        prompt = _prompt_with_conversation(
            "Continue the bracket",
            {
                "conversation": {
                    "scope": {
                        "kind": "saved_document",
                        "document": "BracketDoc",
                        "file_path": "/tmp/bracket.FCStd",
                    },
                    "conversation": [
                        {"role": "user", "content": "Make a mounting bracket."},
                        {"role": "assistant", "content": "Created the base body."},
                    ],
                }
            },
        )

        self.assertIn("only as current document/project memory", prompt)
        self.assertIn("Conversation scope: scope=saved_document", prompt)
        self.assertIn("document=BracketDoc", prompt)
        self.assertIn("file=/tmp/bracket.FCStd", prompt)
        self.assertIn("do not treat unrelated documents", prompt)
        self.assertIn("Current user request: Continue the bracket", prompt)
