# SPDX-License-Identifier: LGPL-2.1-or-later

import importlib
import inspect
import json
import re
from pathlib import Path
import tempfile
from typing import Any

from VibeCADCore import (
    VibeCADService,
)
from VibeCADPreferences import (
    VibeCADSettings,
    load_settings,
    save_settings,
)
from VibeCADProvider import (
    BaseProvider,
    OfflineProvider,
    ProviderResult,
    _agents_input_from_context,
    _model_visible_context,
)
from VibeCADSession import (
    DESIGN_PREFLIGHT_SUBMIT_TOOL,
    _design_preflight_build_ready,
    _design_preflight_existing_state_lines,
    _design_preflight_missing_fields,
    _design_preflight_prompt,
    _design_preflight_user_questions_answered,
    _accepted_design_memory_lines,
    _continuation_prompt,
    _persist_submitted_design_preflight,
    _prompt_with_conversation,
    provider_safe_tool_schemas,
    run_prompt,
)

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
    _temporary_vibecad_home,
)


class TestVibeCADServiceContext(SettingsSnapshotTestCase):
    def _build_ready_preflight_payload(
        self,
        *,
        intent: str = "Make a functional part.",
        architecture: str = "single solid body",
    ) -> dict[str, Any]:
        return {
            "schema": "vibecad-design-preflight-v1",
            "status": "build_ready",
            "user_intent": intent,
            "requirement_refinement": [
                {
                    "question": "Which material?",
                    "model_answer": "CNC aluminum",
                    "assumption": True,
                    "why_it_matters": "It drives wall thickness and load path.",
                }
            ],
            "design_intent_draft": {
                "architecture": architecture,
                "bodies_components": ["Primary body"],
                "interfaces": ["mounting face"],
                "mechanisms": ["none"],
                "manufacturing_assumptions": ["CNC aluminum"],
                "non_negotiable_geometry": ["mounting face"],
                "risks": ["placeholder geometry"],
            },
            "adversarial_review": {
                "blocking_issues": [],
                "criticisms": ["Load path and manufacturing method must be explicit."],
                "required_revisions": [],
            },
            "final_build_plan": {
                "architecture": architecture,
                "bodies": ["Primary body"],
                "sketches_features": ["master sketch", "pad"],
                "interfaces": ["mounting face"],
                "mechanisms": ["none"],
                "manufacturing_assumptions": ["CNC aluminum"],
                "critical_geometry": ["mounting face"],
                "construction_order": ["sketch", "pad", "verify"],
                "verification_checks": ["inspect dimensions"],
                "forbidden_shortcuts": ["placeholder box"],
            },
        }

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
            "core.set_view",
            "core.get_report_view_errors",
            "core.list_workbenches",
            "core.list_registered_commands",
            "core.list_active_workbench_commands",
            "core.get_active_workbench_tool_pack",
            "core.list_workbench_tool_packs",
            "core.list_workbench_object_templates",
            "core.list_workbench_objects",
            "core.get_object_properties",
            "core.get_tool_shape_report",
            "core.report_tool_shape_gap",
            "core.create_new_document",
            "core.open_document",
            "core.delete_object",
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
                self.assertNotRegex(
                    module_source,
                    r"return service\.(create_|add_|apply_|cut_|set_)",
                )

    def test_service_tool_modules_do_not_delegate_back_to_core_tool_methods(self):
        from tool_impl import service as service_tools

        blocked_core_tool_methods = {
            "activate_workbench",
            "all_workbench_tool_packs",
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
            "points_summary",
            "report_tool_shape_gap",
            "report_view_errors",
            "reverseengineering_summary",
            "robot_summary",
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
        self.assertNotIn("provider_tool_surface", context)
        self.assertNotIn("tool_shape_report", context)
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

    def test_provider_context_for_cam_workbench_exposes_cam_summary(self):
        service = VibeCADService()
        service.active_workbench_name = lambda: "CAMWorkbench"

        context = service.provider_context_summary()
        self.assertEqual(context["workbench"], "CAMWorkbench")
        self.assertIn("cam", context)
        cam = context["cam"]
        self.assertIn("job_count", cam)
        self.assertIn("jobs", cam)
        for unrelated in ("partdesign", "sketcher", "mesh", "fem", "bim"):
            self.assertNotIn(unrelated, context)

    def test_model_visible_context_uses_scoped_provider_context(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADScopedProviderContextTest")
        service = VibeCADService()
        service.active_workbench_name = lambda: "PartDesignWorkbench"
        try:
            doc.addObject("PartDesign::Body", "Body")
            doc.addObject("Sketcher::SketchObject", "Sketch")
            doc.recompute()
            context = service.provider_context_summary()
            context["provider_tool_schemas"] = provider_safe_tool_schemas(
                service,
                "PartDesignWorkbench",
            )
            context["provider_tool_schemas_workbench"] = "PartDesignWorkbench"

            visible = _model_visible_context(context, {"sections": ["domain"]})
            self.assertIn("partdesign", visible)
            self.assertIn("sketcher", visible)
            self.assertNotIn("provider_tool_schemas", visible)
            self.assertNotIn("mesh", visible)
            self.assertNotIn("bim", visible)
            self.assertNotIn("robot", visible)
        finally:
            App.closeDocument(doc.Name)

    def test_model_visible_domain_context_is_compact(self):
        long_reason = " ".join(["long validation text"] * 30)
        visible = _model_visible_context(
            {
                "workbench": "PartDesignWorkbench",
                "partdesign": {
                    "document": "Doc",
                    "requested": None,
                    "body_count": 1,
                    "selected": {
                        "name": "Body",
                        "label": "Body",
                        "type": "PartDesign::Body",
                        "features": [
                            {
                                "name": f"Pad{i}",
                                "label": f"Pad {i}",
                                "type": "PartDesign::Pad",
                                "reason": long_reason,
                            }
                            for i in range(25)
                        ],
                    },
                },
                "sketcher": {
                    "found": True,
                    "geometry": [
                        {
                            "index": i,
                            "type": "LineSegment",
                            "construction": False,
                            "start": [1.123456789, 2.0, 0.0],
                        }
                        for i in range(30)
                    ],
                    "profile_status": {
                        "degrees_of_freedom": 16,
                        "ready_for_pad": False,
                        "reason": long_reason,
                    },
                },
                "provider_tool_schemas": [{"name": "partdesign.create_sketch"}],
            },
            {"sections": ["domain"]},
        )

        partdesign = visible["partdesign"]
        self.assertNotIn("document", partdesign)
        self.assertNotIn("requested", partdesign)
        features = partdesign["selected"]["features"]
        self.assertEqual(len(features), 12)
        self.assertEqual(partdesign["selected"]["features_omitted"], 13)
        self.assertEqual(features[0]["type"], "Pad")
        self.assertLessEqual(len(features[0]["reason"]), 120)

        sketcher = visible["sketcher"]
        geometry = sketcher["geometry"]
        self.assertEqual(len(geometry), 16)
        self.assertEqual(sketcher["geometry_omitted"], 14)
        self.assertEqual(geometry[0]["type"], "LineSegment")
        self.assertNotIn("construction", geometry[0])
        self.assertEqual(
            sketcher["profile_status"]["degrees_of_freedom"],
            16,
        )
        self.assertNotIn("provider_tool_schemas", visible)

    def test_provider_api_key_reads_dotenv_without_exposing_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY='sk-test123456'\n", encoding="utf-8")
            service = VibeCADService(dotenv_path=path)
            service.provider_name = lambda: "openai"
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

    def test_model_visible_context_is_compact_and_filters_object_names(self):
        visible = _model_visible_context(
            {
                "workbench": "PartDesignWorkbench",
                "document": {
                    "document": "Doc",
                    "label": "Doc",
                    "object_count": 3,
                    "objects": [
                        {
                            "name": "TopHeadPlane",
                            "label": "Top Head Plane",
                            "type": "PartDesign::Plane",
                            "base": {"name": "Origin"},
                            "shape": {"solids": 0, "faces": 0, "edges": 0},
                        },
                        {
                            "name": "Sketch001",
                            "label": "TopHeadSketch",
                            "type": "Sketcher::SketchObject",
                            "bound_box": {"x_length": 10.0},
                        },
                    ],
                },
                "conversation": {
                    "conversation": [
                        {"role": "user", "content": "large old prompt"},
                        {"role": "assistant", "content": "large old answer"},
                    ],
                    "path": "/tmp/conversation.json",
                },
                "provider_tool_surface": {"tools": ["partdesign.create_sketch"]},
                "workbench_tool_pack": {
                    "active_workbench": "PartDesignWorkbench",
                    "tool_pack": {
                        "workbench": "PartDesignWorkbench",
                        "domain": "parametric solids",
                        "enabled": True,
                        "tool_names": ["a", "b", "c"],
                    },
                },
            },
            {"object_names": ["TopHeadPlane", "TopHeadSketch", "Missing"], "max_objects": 1},
        )
        self.assertIn("conversation", visible)
        self.assertEqual(
            visible["conversation"]["items"][0]["content"],
            "large old prompt",
        )
        self.assertEqual(
            visible["conversation"]["items"][1]["content"],
            "large old answer",
        )
        self.assertNotIn("provider_tool_surface", visible)
        self.assertEqual(len(visible["document"]["objects"]), 1)
        self.assertEqual(visible["document"]["objects"][0]["name"], "TopHeadPlane")
        self.assertNotIn("base", visible["document"]["objects"][0])
        self.assertNotIn("bound_box", visible["document"]["objects"][0])
        query = visible["object_query"]
        self.assertFalse(query["all"])
        self.assertEqual(
            [item["ok"] for item in query["results"]],
            [True, True, False],
        )
        self.assertEqual(
            query["results"][1]["matches"][0]["label"],
            "TopHeadSketch",
        )

    def test_model_visible_context_conversation_section_returns_saved_turns(self):
        visible = _model_visible_context(
            {
                "workbench": "PartDesignWorkbench",
                "conversation": {
                    "conversation": [
                        {"role": "user", "content": "keep this requirement"}
                    ],
                    "scope": {
                        "kind": "saved_document",
                        "document": "PartDoc",
                        "file_path": "/tmp/part.FCStd",
                        "persistent": True,
                    },
                    "path": "/tmp/conversation.json",
                },
            },
            {"sections": ["conversation"]},
        )
        self.assertEqual(visible["conversation"]["turns"], 1)
        self.assertEqual(visible["conversation"]["scope"]["document"], "PartDoc")
        self.assertEqual(
            visible["conversation"]["items"],
            [{"role": "user", "content": "keep this requirement"}],
        )

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

    def test_design_preflight_answers_preserve_presented_options(self):
        with _temporary_vibecad_home():
            service = VibeCADService()
            service.update_design_preflight(
                {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "needs_user",
                    "user_intent": "Make a functional part.",
                    "requirement_refinement": [
                        {
                            "question": "Which profile?",
                            "model_answer": "clip point",
                            "assumption": True,
                            "why_it_matters": "The profile controls the curve geometry.",
                        }
                    ],
                    "user_questions": [
                        {
                            "question": "Which profile should be used?",
                            "default_answer": "clip_point",
                            "options": [
                                {"label": "Clip point", "answer": "clip_point"},
                                {"label": "Drop point", "answer": "drop_point"},
                            ],
                        }
                    ],
                }
            )

            result = service.record_design_preflight_answers(
                [
                    {
                        "question": "Which profile should be used?",
                        "answer": "drop_point",
                        "source": "choice",
                        "default_answer": "clip_point",
                        "options": [
                            {"label": "Clip point", "answer": "clip_point"},
                            {"label": "Drop point", "answer": "drop_point"},
                        ],
                    }
                ]
            )

        saved = result["design_preflight"]["last_user_answers"][0]
        self.assertEqual(saved["answer"], "drop_point")
        self.assertEqual(
            saved["options"],
            [
                {"label": "Clip point", "answer": "clip_point"},
                {"label": "Drop point", "answer": "drop_point"},
            ],
        )

    def test_design_preflight_update_preserves_answer_rounds_for_build_plan(self):
        with _temporary_vibecad_home():
            service = VibeCADService()
            service.update_design_preflight(
                {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "needs_user",
                    "user_intent": "Make a functional part.",
                    "requirement_refinement": [
                        {
                            "question": "Which profile?",
                            "model_answer": "clip point",
                            "assumption": True,
                            "why_it_matters": "The profile controls the curve geometry.",
                        }
                    ],
                    "user_questions": [
                        {
                            "question": "Which profile should be used?",
                            "default_answer": "clip_point",
                            "options": [
                                {"label": "Clip point", "answer": "clip_point"},
                                {"label": "Drop point", "answer": "drop_point"},
                            ],
                        }
                    ],
                }
            )
            service.record_design_preflight_answers(
                [
                    {
                        "question": "Which profile should be used?",
                        "answer": "drop_point",
                        "source": "choice",
                        "default_answer": "clip_point",
                        "options": [
                            {"label": "Clip point", "answer": "clip_point"},
                            {"label": "Drop point", "answer": "drop_point"},
                        ],
                    }
                ]
            )

            result = service.update_design_preflight(
                {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "build_ready",
                    "user_intent": "Make a functional part.",
                    "requirement_refinement": [
                        {
                            "question": "Which profile?",
                            "model_answer": "drop point",
                            "assumption": False,
                            "why_it_matters": "The user answer controls the curve geometry.",
                        }
                    ],
                    "design_intent_draft": {
                        "architecture": "curved blade body",
                        "bodies_components": ["Blade"],
                        "interfaces": ["pivot bore"],
                        "mechanisms": ["pivot"],
                        "manufacturing_assumptions": ["CNC"],
                        "non_negotiable_geometry": ["drop-point curve"],
                        "risks": ["straight placeholder profile"],
                    },
                    "adversarial_review": {
                        "blocking_issues": [],
                        "criticisms": ["no straight placeholder"],
                        "required_revisions": [],
                    },
                    "final_build_plan": {
                        "architecture": "curved blade body",
                        "bodies": ["Blade"],
                        "sketches_features": ["profile sketch", "pad"],
                        "interfaces": ["pivot bore"],
                        "mechanisms": ["pivot"],
                        "manufacturing_assumptions": ["CNC"],
                        "critical_geometry": ["drop-point curve"],
                        "construction_order": ["sketch", "verify curves", "pad"],
                        "verification_checks": ["inspect geometry types"],
                        "forbidden_shortcuts": ["straight polygon profile"],
                    },
                }
            )

        preflight = result["design_preflight"]
        self.assertEqual(
            preflight["last_user_answers"][0]["answer"],
            "drop_point",
        )
        self.assertEqual(
            preflight["user_answers"][0]["answer"],
            "drop_point",
        )
        self.assertEqual(len(preflight["user_answer_rounds"]), 1)

    def test_design_preflight_answer_rounds_accumulate_across_questions(self):
        with _temporary_vibecad_home():
            service = VibeCADService()
            service.update_design_preflight(
                {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "needs_user",
                    "user_intent": "Make a folding knife.",
                    "requirement_refinement": [
                        {
                            "question": "What tip type?",
                            "model_answer": "Drop point",
                            "assumption": False,
                            "why_it_matters": "The tip drives the blade curve.",
                        },
                        {
                            "question": "What lock?",
                            "model_answer": "Liner lock",
                            "assumption": False,
                            "why_it_matters": "The lock drives the mechanism.",
                        },
                    ],
                    "user_questions": [
                        {
                            "question": "What tip type?",
                            "default_answer": "drop_point",
                            "options": [
                                {"label": "Drop point", "answer": "drop_point"}
                            ],
                        },
                        {
                            "question": "What lock?",
                            "default_answer": "liner_lock",
                            "options": [
                                {"label": "Liner lock", "answer": "liner_lock"}
                            ],
                        },
                    ],
                }
            )
            service.record_design_preflight_answers(
                [
                    {
                        "question": "What tip type?",
                        "answer": "drop_point",
                        "source": "choice",
                    }
                ]
            )
            result = service.record_design_preflight_answers(
                [
                    {
                        "question": "What lock?",
                        "answer": "liner_lock",
                        "source": "choice",
                    }
                ]
            )

        preflight = result["design_preflight"]
        answers = {item["question"]: item["answer"] for item in preflight["user_answers"]}
        self.assertEqual(
            answers,
            {
                "What tip type?": "drop_point",
                "What lock?": "liner_lock",
            },
        )
        self.assertEqual(len(preflight["user_answer_rounds"]), 2)

    def test_design_preflight_prompt_and_context_include_answers(self):
        context = {
            "vibecad_project": {
                "design_preflight": {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "build_ready",
                    "user_intent": "Make a functional part.",
                    "requirement_refinement": [
                        {
                            "question": "Which material?",
                            "model_answer": "CNC aluminum",
                            "assumption": True,
                            "why_it_matters": "It drives wall thickness.",
                        }
                    ],
                    "last_user_answers": [
                        {
                            "question": "Which profile should be used?",
                            "answer": "drop_point",
                        }
                    ],
                    "design_intent_draft": {
                        "architecture": "curved blade body",
                        "bodies_components": ["Blade"],
                        "interfaces": ["pivot bore"],
                        "mechanisms": ["pivot"],
                        "manufacturing_assumptions": ["CNC"],
                        "non_negotiable_geometry": ["drop-point curve"],
                        "risks": ["straight placeholder profile"],
                    },
                    "adversarial_review": {
                        "blocking_issues": [],
                        "criticisms": ["no straight placeholder"],
                        "required_revisions": [],
                    },
                    "final_build_plan": {
                        "architecture": "curved blade body",
                        "bodies": ["Blade"],
                        "sketches_features": ["profile sketch", "pad"],
                        "interfaces": ["pivot bore"],
                        "mechanisms": ["pivot"],
                        "manufacturing_assumptions": ["CNC"],
                        "critical_geometry": ["drop-point curve"],
                        "construction_order": ["sketch", "verify curves", "pad"],
                        "verification_checks": ["inspect geometry types"],
                        "forbidden_shortcuts": ["straight polygon profile"],
                    },
                }
            },
            "conversation": {
                "conversation": [
                    {"role": "user", "content": "Use a drop point profile."}
                ]
            },
        }

        lines = _accepted_design_memory_lines(context)
        self.assertTrue(lines, lines)
        text = "\n".join(lines)
        self.assertIn("ACCEPTED DESIGN MEMORY", text)
        self.assertIn("Which profile should be used?: drop_point", text)
        self.assertIn("Sketches/features: profile sketch | pad", text)
        self.assertIn("Mechanisms: pivot", text)
        self.assertIn("Construction order: sketch | verify curves | pad", text)
        prompt = _prompt_with_conversation("Continue.", context)
        self.assertIn("ACCEPTED DESIGN MEMORY", prompt)
        self.assertIn("Which profile should be used?: drop_point", prompt)
        visible = _model_visible_context(context, {"sections": ["design_preflight"]})
        self.assertEqual(
            visible["design_preflight"]["answers"][0]["answer"],
            "drop_point",
        )
        self.assertEqual(
            visible["design_preflight"]["sketches_features"],
            ["profile sketch", "pad"],
        )
        self.assertEqual(visible["design_preflight"]["mechanisms"], ["pivot"])
        self.assertEqual(
            visible["design_preflight"]["manufacturing_assumptions"],
            ["CNC"],
        )
        self.assertEqual(
            visible["design_preflight"]["construction_order"],
            ["sketch", "verify curves", "pad"],
        )

    def test_design_preflight_prompt_and_context_include_assumptions(self):
        context = {
            "vibecad_project": {
                "design_preflight": {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "build_ready",
                    "user_intent": "Make a functional part.",
                    "requirement_refinement": [
                        {
                            "question": "Which material?",
                            "model_answer": "CNC aluminum",
                            "assumption": True,
                            "why_it_matters": "It drives wall thickness.",
                        }
                    ],
                    "design_intent_draft": {
                        "architecture": "single body",
                        "bodies_components": ["Body"],
                        "interfaces": ["mounting face"],
                        "mechanisms": ["none"],
                        "manufacturing_assumptions": ["CNC aluminum"],
                        "non_negotiable_geometry": ["mounting face"],
                        "risks": ["wall thickness"],
                    },
                    "adversarial_review": {
                        "blocking_issues": [],
                        "criticisms": ["load path must be explicit"],
                        "required_revisions": [],
                    },
                    "final_build_plan": {
                        "architecture": "single body",
                        "bodies": ["Body"],
                        "sketches_features": ["base sketch", "pad"],
                        "interfaces": ["mounting face"],
                        "mechanisms": ["none"],
                        "manufacturing_assumptions": ["CNC aluminum"],
                        "critical_geometry": ["mounting face"],
                        "construction_order": ["sketch", "pad"],
                        "verification_checks": ["inspect dimensions"],
                        "forbidden_shortcuts": ["placeholder box"],
                    },
                }
            }
        }

        lines = _accepted_design_memory_lines(context)
        text = "\n".join(lines)
        self.assertIn("Accepted assumptions", text)
        self.assertIn("CNC aluminum", text)
        visible = _model_visible_context(context, {"sections": ["design_preflight"]})
        self.assertEqual(
            visible["design_preflight"]["assumptions"][0]["answer"],
            "CNC aluminum",
        )

    def test_prompt_preamble_includes_hard_report_errors_as_repair_state(self):
        context = {
            "report_view_errors": {
                "errors": [
                    "BladePad: Wire is not closed.",
                    "EdgeBevelChamfer: BRep_API: command not done",
                ]
            }
        }

        prompt = _prompt_with_conversation("Continue.", context)

        self.assertIn("ERRORS: unresolved hard FreeCAD geometry failures", prompt)
        self.assertIn("Wire is not closed", prompt)
        self.assertIn("inspect the failed sketch/body", prompt)

    def test_design_preflight_existing_state_keeps_answer_round_visible(self):
        preflight = {
            "schema": "vibecad-design-preflight-v1",
            "status": "needs_user",
            "initial_user_prompt": "Make a folding knife with a real curved blade.",
            "user_intent": "Make a functional assembly.",
            "requirement_refinement": [
                {
                    "question": f"Question {index}?",
                    "model_answer": f"Default {index}",
                    "assumption": True,
                    "why_it_matters": "It changes geometry.",
                }
                for index in range(20)
            ],
            "user_questions": [
                {
                    "question": f"Blocking question {index}?",
                    "default_answer": f"default_{index}",
                    "options": [
                        {"label": f"Option {index} A", "answer": f"option_{index}_a"},
                        {"label": f"Option {index} B", "answer": f"option_{index}_b"},
                    ],
                }
                for index in range(20)
            ],
            "last_user_answers": [
                {
                    "question": "Which locking interface should be used?",
                    "answer": "liner_lock",
                    "source": "choice",
                }
            ],
        }

        lines = _design_preflight_existing_state_lines(preflight)
        text = "\n".join(lines)
        self.assertIn("initial_user_prompt", text)
        self.assertIn("real curved blade", text)
        self.assertIn("user_answers: use these as binding requirements", text)
        self.assertIn("Which locking interface should be used?", text)
        self.assertIn("liner_lock", text)
        prompt = _design_preflight_prompt(
            "Continue from the answered questions.",
            {"vibecad_project": {"design_preflight": preflight}},
        )
        self.assertIn("liner_lock", prompt)

    def test_design_preflight_answered_question_round_is_not_pending(self):
        preflight = {
            "schema": "vibecad-design-preflight-v1",
            "status": "needs_user",
            "user_intent": "Make a functional part.",
            "user_questions": [
                {
                    "question": "Which material?",
                    "default_answer": "aluminum",
                    "options": [{"label": "Aluminum", "answer": "aluminum"}],
                },
                {
                    "question": "Which mounting pattern?",
                    "default_answer": "M3",
                    "options": [{"label": "M3", "answer": "M3"}],
                },
            ],
            "last_user_answers": [
                {
                    "question": "Which material?",
                    "answer": "aluminum",
                    "source": "choice",
                },
                {
                    "question": "Which mounting pattern?",
                    "answer": "M3",
                    "source": "choice",
                },
            ],
        }

        self.assertTrue(_design_preflight_user_questions_answered(preflight))
        text = "\n".join(_design_preflight_existing_state_lines(preflight))
        self.assertIn("answered_user_questions:", text)
        self.assertNotIn("pending_user_questions:", text)

    def test_design_preflight_structured_submit_persists_needs_user(self):
        payload = {
            "schema": "vibecad-design-preflight-v1",
            "status": "needs_user",
            "user_intent": "Make a functional part.",
            "requirement_refinement": [
                {
                    "question": "Which material?",
                    "model_answer": "aluminum",
                    "assumption": True,
                    "why_it_matters": "It changes wall thickness.",
                }
            ],
            "user_questions": [
                {
                    "question": "Which material?",
                    "default_answer": "aluminum",
                    "options": [{"label": "Aluminum", "answer": "aluminum"}],
                }
            ],
        }

        with _temporary_vibecad_home():
            result = _persist_submitted_design_preflight(
                VibeCADService(),
                payload,
                prompt="Make a part.",
                provider_name="FakeProvider",
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["preflight"]["status"], "needs_user")
        self.assertEqual(result["preflight"]["initial_user_prompt"], "Make a part.")

    def test_design_preflight_structured_submit_reports_missing_fields(self):
        with _temporary_vibecad_home():
            result = _persist_submitted_design_preflight(
                VibeCADService(),
                {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "build_ready",
                    "user_intent": "Make a functional part.",
                    "requirement_refinement": [
                        {
                            "question": "Which material?",
                            "model_answer": "aluminum",
                            "assumption": True,
                            "why_it_matters": "It changes wall thickness.",
                        }
                    ],
                    "final_build_plan": {"architecture": "single body"},
                },
                prompt="Make a part.",
                provider_name="FakeProvider",
            )

        self.assertFalse(result["ok"], result)
        self.assertIn("Design preflight submission is incomplete", result["error"])
        self.assertIn("design_intent_draft", result["missing"])

    def test_design_preflight_capture_preserves_original_user_prompt(self):
        with _temporary_vibecad_home():
            service = VibeCADService()
            first = {
                "schema": "vibecad-design-preflight-v1",
                "status": "needs_user",
                "user_intent": "Make a 4.5 in folding knife blade.",
                "requirement_refinement": [
                    {
                        "question": "What opening feature?",
                        "model_answer": "Ask the user.",
                        "assumption": False,
                        "why_it_matters": "It changes blade/tang geometry.",
                    }
                ],
                "user_questions": [
                    {
                        "question": "What opening feature?",
                        "default_answer": "Thumb stud.",
                        "options": [
                            {
                                "label": "Thumb stud",
                                "answer": "Thumb stud on the blade.",
                            }
                        ],
                        "why_it_matters": "It changes blade/tang geometry.",
                    }
                ],
            }
            capture = _persist_submitted_design_preflight(
                service,
                first,
                prompt="Make a 4.5 in folding knife blade.",
                provider_name="UnitProvider",
            )
            self.assertTrue(capture["ok"], capture)
            service.record_design_preflight_answers(
                [
                    {
                        "question": "What opening feature?",
                        "answer": "Thumb stud on the blade.",
                    }
                ]
            )

            second = {
                "schema": "vibecad-design-preflight-v1",
                "status": "build_ready",
                "user_intent": "Make a 4.5 in folding knife blade.",
                "requirement_refinement": [
                    {
                        "question": "What opening feature?",
                        "model_answer": "Thumb stud on the blade.",
                        "assumption": False,
                        "why_it_matters": "It changes blade/tang geometry.",
                    }
                ],
                "design_intent_draft": {
                    "architecture": "curved blade and handle interface",
                    "bodies_components": ["Blade"],
                    "interfaces": ["pivot bore", "stop pin"],
                    "mechanisms": ["folding pivot"],
                    "manufacturing_assumptions": ["CNC steel"],
                    "non_negotiable_geometry": ["curved blade profile"],
                    "risks": ["flat polygon placeholder"],
                },
                "adversarial_review": {
                    "blocking_issues": [],
                    "criticisms": ["Do not use straight placeholder edges."],
                    "required_revisions": [],
                },
                "final_build_plan": {
                    "architecture": "curved blade and handle interface",
                    "bodies": ["Blade"],
                    "sketches_features": ["arc/spline profile", "pad"],
                    "interfaces": ["pivot bore", "stop pin"],
                    "mechanisms": ["folding pivot"],
                    "manufacturing_assumptions": ["CNC steel"],
                    "critical_geometry": ["4.5 in curved blade"],
                    "construction_order": ["sketch", "verify curves", "pad"],
                    "verification_checks": ["inspect sketch geometry types"],
                    "forbidden_shortcuts": ["flat polygon blade"],
                },
            }
            capture = _persist_submitted_design_preflight(
                service,
                second,
                prompt=(
                    "Design preflight answers:\n\n"
                    "1. What opening feature?\nAnswer: Thumb stud on the blade."
                ),
                provider_name="UnitProvider",
            )

            self.assertTrue(capture["ok"], capture)
            preflight = capture["preflight"]
            self.assertEqual(
                preflight["initial_user_prompt"],
                "Make a 4.5 in folding knife blade.",
            )
            self.assertEqual(
                preflight["source_prompt"],
                "Make a 4.5 in folding knife blade.",
            )
            self.assertIn(
                "Design preflight answers",
                preflight["latest_preflight_prompt"],
            )

    def test_design_preflight_validation_requires_real_refinement_and_review(self):
        question_dump = {
            "schema": "vibecad-design-preflight-v1",
            "status": "needs_user",
            "user_intent": "Make a part.",
            "requirement_refinement": [{"question": "Which material?"}],
            "user_questions": [
                {
                    "question": "Which material?",
                    "default_answer": "aluminum",
                    "options": [{"label": "Aluminum", "answer": "aluminum"}],
                }
            ],
        }
        self.assertIn(
            "requirement_refinement[1].model_answer",
            _design_preflight_missing_fields(question_dump),
        )
        self.assertIn(
            "requirement_refinement[1].assumption",
            _design_preflight_missing_fields(question_dump),
        )

        malformed_options = dict(question_dump)
        malformed_options["requirement_refinement"] = [
            {
                "question": "Which material?",
                "model_answer": "aluminum",
                "assumption": True,
                "why_it_matters": "It drives wall thickness.",
            }
        ]
        malformed_options["user_questions"] = [
            {
                "question": "Which material?",
                "default_answer": "aluminum",
                "options": ["aluminum"],
            }
        ]
        self.assertIn(
            "user_questions[1].options[1]",
            _design_preflight_missing_fields(malformed_options),
        )

        contradictory_interview = {
            "schema": "vibecad-design-preflight-v1",
            "status": "needs_user",
            "user_intent": "Make a part.",
            "requirement_refinement": [
                {
                    "question": "Which material?",
                    "model_answer": "aluminum",
                    "assumption": True,
                    "why_it_matters": "It drives wall thickness.",
                }
            ],
            "user_questions": [
                {
                    "question": "Which material?",
                    "default_answer": "aluminum",
                    "options": [{"label": "Aluminum", "answer": "aluminum"}],
                }
            ],
            "final_build_plan": {
                "architecture": "single body",
                "bodies": ["Body"],
            },
        }
        self.assertIn(
            "final_build_plan=not_allowed_for_needs_user",
            _design_preflight_missing_fields(contradictory_interview),
        )

        weak_plan = {
            "schema": "vibecad-design-preflight-v1",
            "status": "build_ready",
            "user_intent": "Make a part.",
            "requirement_refinement": [
                {
                    "question": "Which material?",
                    "model_answer": "aluminum",
                    "assumption": True,
                    "why_it_matters": "It drives wall thickness.",
                }
            ],
            "design_intent_draft": {"architecture": "single body"},
            "adversarial_review": {"criticisms": []},
            "final_build_plan": {"architecture": "single body"},
        }
        missing = _design_preflight_missing_fields(weak_plan)
        self.assertIn("design_intent_draft.bodies_components", missing)
        self.assertIn("adversarial_review.criticisms", missing)
        self.assertIn("final_build_plan.sketches_features", missing)
        self.assertIn("final_build_plan.mechanisms", missing)

    def test_design_preflight_build_ready_requires_user_answers_for_non_assumptions(self):
        payload = {
            "schema": "vibecad-design-preflight-v1",
            "status": "build_ready",
            "user_intent": "Make a functional part.",
            "requirement_refinement": [
                {
                    "question": "Which material?",
                    "model_answer": "steel",
                    "assumption": False,
                    "why_it_matters": "It drives wall thickness.",
                }
            ],
            "design_intent_draft": {
                "architecture": "single body",
                "bodies_components": ["Body"],
                "interfaces": ["mounting face"],
                "mechanisms": ["none"],
                "manufacturing_assumptions": ["steel"],
                "non_negotiable_geometry": ["mounting face"],
                "risks": ["wall thickness"],
            },
            "adversarial_review": {
                "blocking_issues": [],
                "criticisms": ["material drives stiffness"],
                "required_revisions": [],
            },
            "final_build_plan": {
                "architecture": "single body",
                "bodies": ["Body"],
                "sketches_features": ["base sketch", "pad"],
                "interfaces": ["mounting face"],
                "mechanisms": ["none"],
                "manufacturing_assumptions": ["steel"],
                "critical_geometry": ["mounting face"],
                "construction_order": ["sketch", "pad"],
                "verification_checks": ["inspect dimensions"],
                "forbidden_shortcuts": ["placeholder box"],
            },
        }

        self.assertIn(
            "requirement_refinement[1].user_answer",
            _design_preflight_missing_fields(payload),
        )
        payload["last_user_answers"] = [
            {
                "question": "Which mounting pattern?",
                "answer": "M3",
                "source": "choice",
            }
        ]
        self.assertIn(
            "requirement_refinement[1].user_answer",
            _design_preflight_missing_fields(payload),
        )
        payload["last_user_answers"] = [
            {"question": "Which material?", "answer": "steel", "source": "choice"}
        ]
        self.assertNotIn(
            "requirement_refinement[1].user_answer",
            _design_preflight_missing_fields(payload),
        )

    def test_design_preflight_build_ready_rejects_unanswered_user_questions(self):
        payload = {
            "schema": "vibecad-design-preflight-v1",
            "status": "build_ready",
            "user_intent": "Make a functional part.",
            "requirement_refinement": [
                {
                    "question": "Which material?",
                    "model_answer": "aluminum",
                    "assumption": True,
                    "why_it_matters": "It drives wall thickness.",
                }
            ],
            "user_questions": [
                {
                    "question": "Which mounting pattern?",
                    "default_answer": "M3",
                    "options": [{"label": "M3", "answer": "M3"}],
                }
            ],
            "design_intent_draft": {
                "architecture": "single body",
                "bodies_components": ["Body"],
                "interfaces": ["mounting face"],
                "mechanisms": ["none"],
                "manufacturing_assumptions": ["aluminum"],
                "non_negotiable_geometry": ["mounting face"],
                "risks": ["wall thickness"],
            },
            "adversarial_review": {
                "blocking_issues": [],
                "criticisms": ["mounting pattern must be explicit"],
                "required_revisions": [],
            },
            "final_build_plan": {
                "architecture": "single body",
                "bodies": ["Body"],
                "sketches_features": ["base sketch", "pad"],
                "interfaces": ["mounting face"],
                "mechanisms": ["none"],
                "manufacturing_assumptions": ["aluminum"],
                "critical_geometry": ["mounting face"],
                "construction_order": ["sketch", "pad"],
                "verification_checks": ["inspect dimensions"],
                "forbidden_shortcuts": ["placeholder box"],
            },
        }

        self.assertIn("user_questions.unanswered", _design_preflight_missing_fields(payload))
        payload["last_user_answers"] = [
            {
                "question": "Which mounting pattern?",
                "answer": "M3",
                "source": "choice",
            }
        ]
        self.assertNotIn(
            "user_questions.unanswered",
            _design_preflight_missing_fields(payload),
        )

    def test_design_preflight_build_ready_requires_valid_full_contract(self):
        shallow_context = {
            "vibecad_project": {
                "design_preflight": {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "build_ready",
                    "user_intent": "Make a part.",
                    "requirement_refinement": [
                        {
                            "question": "Which material?",
                            "model_answer": "aluminum",
                            "assumption": True,
                            "why_it_matters": "It drives wall thickness.",
                        }
                    ],
                    "final_build_plan": {"architecture": "single body"},
                }
            }
        }
        self.assertFalse(_design_preflight_build_ready(shallow_context))

    def test_design_preflight_prompt_requires_visible_intent_restatement(self):
        prompt = _design_preflight_prompt(
            "Make a drone frame.",
            {"conversation": {"conversation": []}},
        )

        self.assertIn("user-visible prose must start", prompt)
        self.assertIn("customer's intended outcome", prompt)
        self.assertIn("Do not call CAD tools", prompt)
        self.assertIn(DESIGN_PREFLIGHT_SUBMIT_TOOL, prompt)
        self.assertIn("Do not embed JSON", prompt)

    def test_run_prompt_revalidates_persisted_build_ready_before_unlocking_tools(self):
        class Provider(BaseProvider):
            def __init__(self):
                self.calls: list[dict[str, Any]] = []

            def run(
                self,
                prompt,
                context,
                tool_runner=None,
                cancellation_check=None,
                progress_callback=None,
            ):
                self.calls.append(
                    {
                        "tool_runner": tool_runner is not None,
                        "tool_count": len(context.get("provider_tool_schemas") or []),
                        "stage": (context.get("provider_tool_scope") or {}).get("stage"),
                    }
                )
                if len(self.calls) == 1:
                    payload = {
                        "schema": "vibecad-design-preflight-v1",
                        "status": "build_ready",
                        "user_intent": "Make a functional part.",
                        "requirement_refinement": [
                            {
                                "question": "Which material?",
                                "model_answer": "aluminum",
                                "assumption": True,
                                "why_it_matters": "It drives wall thickness.",
                            }
                        ],
                        "design_intent_draft": {
                            "architecture": "single solid body",
                            "bodies_components": ["Body"],
                            "interfaces": ["mounting face"],
                            "mechanisms": ["none"],
                            "manufacturing_assumptions": ["CNC"],
                            "non_negotiable_geometry": ["mounting face"],
                            "risks": ["weak wall thickness"],
                        },
                        "adversarial_review": {
                            "blocking_issues": [],
                            "criticisms": ["load path must be explicit"],
                            "required_revisions": [],
                        },
                        "final_build_plan": {
                            "architecture": "single solid body",
                            "bodies": ["Body"],
                            "sketches_features": ["base sketch", "pad"],
                            "interfaces": ["mounting face"],
                            "mechanisms": ["none"],
                            "manufacturing_assumptions": ["CNC"],
                            "critical_geometry": ["mounting face"],
                            "construction_order": ["sketch", "pad", "verify"],
                            "verification_checks": ["inspect body dimensions"],
                            "forbidden_shortcuts": ["placeholder box"],
                        },
                    }
                    assert tool_runner is not None
                    submit = tool_runner(
                        DESIGN_PREFLIGHT_SUBMIT_TOOL,
                        json.dumps(payload),
                    )
                    assert submit["ok"], submit
                    return ProviderResult("Intended outcome: make a functional part.")
                return ProviderResult("CAD turn reached.")

        with _temporary_vibecad_home():
            service = VibeCADService()
            service.update_design_preflight(
                {
                    "schema": "vibecad-design-preflight-v1",
                    "status": "build_ready",
                    "user_intent": "Make a weak stale part.",
                    "requirement_refinement": [
                        {
                            "question": "Which material?",
                            "model_answer": "unknown",
                            "assumption": True,
                            "why_it_matters": "It matters.",
                        }
                    ],
                    "final_build_plan": {"architecture": "placeholder"},
                }
            )
            provider = Provider()
            response = run_prompt("Continue.", service=service, provider=provider)

        self.assertEqual(len(provider.calls), 2, provider.calls)
        self.assertTrue(provider.calls[0]["tool_runner"], provider.calls)
        self.assertEqual(provider.calls[0]["tool_count"], 1, provider.calls)
        self.assertEqual(provider.calls[0]["stage"], "design_preflight")
        self.assertTrue(provider.calls[1]["tool_runner"], provider.calls)
        self.assertIn("CAD turn reached.", response.final_output)

    def test_run_prompt_rechecks_valid_build_plan_for_new_user_request(self):
        class Provider(BaseProvider):
            def __init__(self, outer):
                self.outer = outer
                self.calls: list[dict[str, Any]] = []

            def run(
                self,
                prompt,
                context,
                tool_runner=None,
                cancellation_check=None,
                progress_callback=None,
            ):
                self.calls.append(
                    {
                        "prompt": prompt,
                        "tool_runner": tool_runner is not None,
                        "tool_count": len(context.get("provider_tool_schemas") or []),
                        "stage": (context.get("provider_tool_scope") or {}).get("stage"),
                    }
                )
                if len(self.calls) == 1:
                    payload = self.outer._build_ready_preflight_payload(
                        intent="Make a drone frame for a 10 lb payload.",
                        architecture="X8 drone frame with explicit load paths",
                    )
                    self.outer.assertIsNotNone(tool_runner)
                    submit = tool_runner(
                        DESIGN_PREFLIGHT_SUBMIT_TOOL,
                        json.dumps(payload),
                    )
                    self.outer.assertTrue(submit["ok"], submit)
                    return ProviderResult(
                        "The customer now wants a drone frame, so I am replacing "
                        "the stale knife plan before CAD tools unlock."
                    )
                return ProviderResult("CAD turn reached.")

        with _temporary_vibecad_home():
            service = VibeCADService()
            service.update_design_preflight(
                self._build_ready_preflight_payload(
                    intent="Make a folding knife blade.",
                    architecture="curved folding knife blade",
                )
            )
            provider = Provider(self)
            response = run_prompt(
                "Make a drone frame for a 10 lb payload.",
                service=service,
                provider=provider,
            )
            saved = service.project_context()["design_preflight"]

        self.assertEqual(len(provider.calls), 2, provider.calls)
        self.assertTrue(provider.calls[0]["tool_runner"], provider.calls)
        self.assertEqual(provider.calls[0]["tool_count"], 1, provider.calls)
        self.assertEqual(provider.calls[0]["stage"], "design_preflight")
        self.assertIn("Make a folding knife blade", provider.calls[0]["prompt"])
        self.assertIn("Make a drone frame for a 10 lb payload.", provider.calls[0]["prompt"])
        self.assertTrue(provider.calls[1]["tool_runner"], provider.calls)
        self.assertEqual(saved["user_intent"], "Make a drone frame for a 10 lb payload.")
        self.assertEqual(
            saved["initial_user_prompt"],
            "Make a drone frame for a 10 lb payload.",
        )
        self.assertIn("CAD turn reached.", response.final_output)

    def test_run_prompt_allows_explicit_continuation_to_use_valid_preflight(self):
        class Provider(BaseProvider):
            def __init__(self):
                self.calls: list[dict[str, Any]] = []

            def run(
                self,
                prompt,
                context,
                tool_runner=None,
                cancellation_check=None,
                progress_callback=None,
            ):
                self.calls.append(
                    {
                        "tool_runner": tool_runner is not None,
                        "stage": (context.get("provider_tool_scope") or {}).get("stage"),
                    }
                )
                return ProviderResult("CAD turn reached.")

        with _temporary_vibecad_home():
            service = VibeCADService()
            service.update_design_preflight(self._build_ready_preflight_payload())
            provider = Provider()
            response = run_prompt("Continue.", service=service, provider=provider)

        self.assertEqual(len(provider.calls), 1, provider.calls)
        self.assertTrue(provider.calls[0]["tool_runner"], provider.calls)
        self.assertNotEqual(provider.calls[0]["stage"], "design_preflight")
        self.assertEqual(response.final_output, "CAD turn reached.")

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

        self.assertIn(
            "Saved conversation context "
            "(d=BracketDoc,f=/tmp/bracket.FCStd; authoritative requirements",
            prompt,
        )
        self.assertIn("1. USER:\nMake a mounting bracket.", prompt)
        self.assertIn("2. VIBECAD:\nCreated the base body.", prompt)
        self.assertIn("U: Continue the bracket", prompt)
        self.assertNotIn("only as current document/project memory", prompt)
        self.assertNotIn("Conversation scope:", prompt)
        self.assertNotIn("Current user request:", prompt)

    def test_requirement_memory_keeps_original_ask_after_chat_window_ages_out(self):
        with _temporary_vibecad_home():
            service = VibeCADService()
            original = (
                "Make a 4.5 in folding blade with a real curved drop point, "
                "liner lock, and no flat polygon placeholder."
            )
            service.record_conversation_turn(
                "user",
                original,
                metadata={"source": "prompt"},
            )
            for index in range(55):
                service.record_conversation_turn("assistant", f"Progress {index}.")
                service.record_conversation_turn(
                    "user",
                    f"Continue detailed CAD step {index}.",
                    metadata={"source": "prompt"},
                )

            context = service.provider_context_summary()
            recent = json.dumps(context["conversation"]["conversation"])
            self.assertNotIn("4.5 in folding blade", recent)

            prompt = _prompt_with_conversation(
                "What requirements did I give you to begin with?",
                context,
            )
            self.assertIn("Persistent requirement memory", prompt)
            self.assertIn("4.5 in folding blade", prompt)
            self.assertIn("no flat polygon placeholder", prompt)

            visible = _model_visible_context(context, {"sections": ["conversation"]})
            visible_text = json.dumps(visible["conversation"]["requirements"])
            self.assertIn("4.5 in folding blade", visible_text)
            self.assertIn("omitted", visible_text)

    def test_prompt_with_conversation_includes_all_saved_requirement_turns(self):
        prompt = _prompt_with_conversation(
            "Continue with the reviewed design.",
            {
                "conversation": {
                    "scope": {
                        "kind": "unsaved_document",
                        "document": "KnifeDoc",
                    },
                    "conversation": [
                        {
                            "role": "user",
                            "content": (
                                "Make a 4.5 in folding blade with a clip point, "
                                "a thumb stud, a liner lock, and no flat polygon "
                                "placeholder profile."
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": (
                                "I will refine requirements before CAD and verify "
                                "the blade curve uses arcs or splines."
                            ),
                        },
                        {
                            "role": "user",
                            "content": "Use the prior requirements as authoritative.",
                        },
                    ],
                }
            },
        )

        self.assertIn("Make a 4.5 in folding blade", prompt)
        self.assertIn("liner lock", prompt)
        self.assertIn("no flat polygon placeholder profile", prompt)
        self.assertIn("verify the blade curve uses arcs or splines", prompt)
        self.assertIn("Use the prior requirements as authoritative.", prompt)

    def test_prompt_with_conversation_omits_duplicate_current_user_turn(self):
        prompt = _prompt_with_conversation(
            "Continue the bracket",
            {
                "conversation": {
                    "scope": {"kind": "unsaved_document", "document": "BracketDoc"},
                    "conversation": [
                        {"role": "user", "content": "Make a mounting bracket."},
                        {"role": "user", "content": "Continue the bracket"},
                    ],
                }
            },
        )

        self.assertIn("1. USER:\nMake a mounting bracket.", prompt)
        self.assertEqual(prompt.count("Continue the bracket"), 1)

    def test_continuation_prompt_keeps_requirement_memory_and_design_memory(self):
        context = {
            "vibecad_workspace": {"mode": "workspace", "entered_workbench": "PartDesignWorkbench"},
            "vibecad_project": {
                "requirement_memory": [
                    {
                        "role": "user",
                        "content": (
                            "Make a 4.5 in folding blade with a real curved "
                            "drop point and no flat polygon placeholder."
                        ),
                        "source": "prompt",
                    }
                ],
                "design_preflight": {
                    **self._build_ready_preflight_payload(
                        intent="Make a 4.5 in folding knife blade.",
                        architecture="curved folding knife blade",
                    ),
                    "initial_user_prompt": "Make a 4.5 in folding blade.",
                    "final_build_plan": {
                        "architecture": "curved folding knife blade",
                        "bodies": ["Blade"],
                        "sketches_features": ["arc/spline profile sketch", "pad"],
                        "interfaces": ["pivot bore", "stop pin"],
                        "mechanisms": ["folding pivot"],
                        "manufacturing_assumptions": ["CNC steel"],
                        "critical_geometry": ["drop-point curve"],
                        "construction_order": ["sketch", "verify curves", "pad"],
                        "verification_checks": ["inspect sketch geometry types"],
                        "forbidden_shortcuts": ["straight polygon profile"],
                    },
                },
                "design_memory": {
                    "user_intent": "Make a 4.5 in folding knife blade.",
                    "summary": "curved folding knife blade",
                    "accepted_assumptions": ["4.5 in means blade length"],
                    "mechanisms": ["folding pivot"],
                    "critical_geometry": ["drop-point curve"],
                    "verification_checks": ["inspect sketch geometry types"],
                    "forbidden_shortcuts": ["straight polygon profile"],
                    "known_failures": ["flat polygon placeholder profile"],
                },
            },
            "document": {
                "objects": [
                    {"name": "Body", "label": "BladeBody", "type": "PartDesign::Body"}
                ]
            },
        }

        prompt = _continuation_prompt(
            "Continue.",
            ["Workspace entered."],
            context,
            [
                {
                    "tool_name": "core.enter_workspace",
                    "ok": True,
                    "result": {"workspace_handoff": "workspace_entry"},
                }
            ],
        )

        self.assertIn("Persistent requirement memory", prompt)
        self.assertIn("4.5 in folding blade", prompt)
        self.assertIn("no flat polygon placeholder", prompt)
        self.assertIn("ACCEPTED DESIGN MEMORY", prompt)
        self.assertIn("User intent: Make a 4.5 in folding knife blade.", prompt)
        self.assertIn("Accepted architecture: curved folding knife blade", prompt)
        self.assertIn("Accepted assumptions: 4.5 in means blade length", prompt)
        self.assertIn("Mechanisms: folding pivot", prompt)
        self.assertIn("Critical geometry: drop-point curve", prompt)
        self.assertIn("Verification checks: inspect sketch geometry types", prompt)
        self.assertIn("Forbidden shortcuts: straight polygon profile", prompt)
        self.assertIn("Known failures: flat polygon placeholder profile", prompt)
        self.assertNotIn("P:", prompt)
        self.assertIn("T:", prompt)
        self.assertIn("O:", prompt)
