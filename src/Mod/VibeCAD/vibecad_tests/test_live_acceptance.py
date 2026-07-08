# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import json
import runpy
from pathlib import Path
import tempfile

import VibeCADProject
from VibeCADCore import (
    VibeCADService,
)
from VibeCADPreferences import (
    DEFAULT_MODEL,
)
from VibeCADProvider import (
    BaseProvider,
    ProviderResult,
)
from VibeCADSession import (
    run_prompt,
)

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
    _attach_temp_project_store,
    _repo_tool_script,
)


class TestVibeCADLiveAcceptance(SettingsSnapshotTestCase):
    def test_live_steering_is_injected_into_provider_context(self):
        class SteeringProvider(BaseProvider):
            def __init__(self):
                self.messages = []

            def run(self, prompt, context, tool_runner=None, cancellation_check=None):
                steering = context.get("human_steering", {})
                self.messages = list(steering.get("active_messages", []))
                return ProviderResult("used steering")

        with tempfile.TemporaryDirectory() as tmp:
            service = VibeCADService()
            original = _attach_temp_project_store(service, Path(tmp))
            try:
                service.queue_steering_message("make the yoke removable")
                provider = SteeringProvider()
                run_prompt(
                    "continue",
                    service=service,
                    provider=provider,
                    steering_check=lambda: [
                        item["text"] for item in service.consume_steering_messages()
                    ],
                )
                self.assertIn("make the yoke removable", provider.messages)
            finally:
                VibeCADProject._active_document_info = original

    def test_live_provider_acceptance_covers_required_goal_categories(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        scenarios = data["SCENARIOS"]
        requirements_for = data["_requirements_for_scenario"]
        completion_directive = data["COMPLETION_DIRECTIVE"]
        expected = {
            "mechanical",
            "partdesign",
            "robot",
            "drone",
            "automotive",
            "aerospace",
            "marine",
            "enclosure",
            "assembly",
            "documentation",
            "rocket_engine",
        }
        self.assertTrue(expected.issubset(set(scenarios)))
        for scenario in expected:
            self.assertIn(completion_directive, scenarios[scenario])
            requirements = requirements_for(scenario)
            self.assertGreaterEqual(int(requirements["minimum_objects"]), 3)
            self.assertGreaterEqual(int(requirements["minimum_mutating_tools"]), 6)
            self.assertIn("sketcher.", requirements["required_tool_prefixes"])
            self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        assembly_requirements = requirements_for("assembly")
        self.assertGreaterEqual(int(assembly_requirements["minimum_assemblies"]), 1)
        self.assertGreaterEqual(int(assembly_requirements["minimum_assembly_components"]), 2)
        self.assertIn("assembly.", assembly_requirements["required_tool_prefixes"])
        self.assertIn("intentionally bounded", scenarios["revision"])
        self.assertIn("revised in the next prompt", scenarios["revision"])
        documentation_requirements = requirements_for("documentation")
        self.assertGreaterEqual(int(documentation_requirements["minimum_techdraw_pages"]), 1)
        self.assertGreaterEqual(int(documentation_requirements["minimum_techdraw_views"]), 1)
        self.assertIn("techdraw.", documentation_requirements["required_tool_prefixes"])
        self.assertIn("TechDraw drawing page", scenarios["documentation"])

    def test_live_provider_acceptance_reports_request_dump_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        old_dump_dir = os.environ.get("VIBECAD_OPENAI_REQUEST_DUMP_DIR")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["VIBECAD_OPENAI_REQUEST_DUMP_DIR"] = directory
                data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
                latest = Path(directory) / "latest-openai-request.json"
                latest.write_text(
                    json.dumps(
                        {
                            "schema": "vibecad-openai-agents-request-v1",
                            "model": DEFAULT_MODEL,
                            "agent": {"tools": [{"function_name": "c_doc"}]},
                            "model_visible_context": {"workbench": "PartDesignWorkbench"},
                        }
                    ),
                    encoding="utf-8",
                )
                summary = data["_latest_request_dump_summary"]()
                self.assertTrue(summary["exists"])
                self.assertEqual(summary["schema"], "vibecad-openai-agents-request-v1")
                self.assertEqual(summary["model"], DEFAULT_MODEL)
                self.assertEqual(summary["tool_count"], 1)
                self.assertTrue(summary["has_model_visible_context"])
                self.assertFalse(summary["has_generic_dispatcher"])
                self.assertFalse(summary["has_available_tools"])
                self.assertFalse(summary["has_tool_menu_context"])
                self.assertEqual(summary["proposal_or_queue_functions"], [])

                latest.write_text(
                    json.dumps(
                        {
                            "schema": "vibecad-openai-agents-request-v1",
                            "model": DEFAULT_MODEL,
                            "agent": {
                                "tools": [
                                    {"function_name": "execute_vibecad_tool"},
                                    {"function_name": "core_apply_action"},
                                    {"function_name": "legacy_queue_apply"},
                                ]
                            },
                            "model_visible_context": {
                                "workbench": "PartDesignWorkbench",
                                "available_tools": [{"name": "partdesign.create_sketch"}],
                                "provider_function_tools": ["pd_sk"],
                                "provider_tool_surface": {"tools": ["partdesign.create_sketch"]},
                                "tool_shape_report": {"provider_visible_tool_count": 1},
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                bad_summary = data["_latest_request_dump_summary"]()
                self.assertTrue(bad_summary["has_generic_dispatcher"])
                self.assertTrue(bad_summary["has_available_tools"])
                self.assertTrue(bad_summary["has_tool_menu_context"])
                self.assertEqual(
                    bad_summary["proposal_or_queue_functions"],
                    ["core_apply_action"],
                )
        finally:
            if old_dump_dir is None:
                os.environ.pop("VIBECAD_OPENAI_REQUEST_DUMP_DIR", None)
            else:
                os.environ["VIBECAD_OPENAI_REQUEST_DUMP_DIR"] = old_dump_dir

    def test_live_provider_acceptance_rejects_unresolved_final_output(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        reasons = data["_final_output_unresolved_reasons"](
            "Current status: attempted pockets were created, but the body volume "
            "remained unchanged and the cutouts were not actually cutting the web. "
            "Next step after refresh: recreate them."
        )
        self.assertIn("ineffective-geometry", reasons)
        self.assertIn("next-step", reasons)

    def test_live_provider_acceptance_rejects_timeout_final_output(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        reasons = data["_final_output_unresolved_reasons"](
            "The autonomous provider loop reached the configured 600 second "
            "limit before completion."
        )
        self.assertIn("timeout", reasons)

    def test_live_provider_acceptance_allows_prior_refresh_before_completion(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        reasons = data["_final_output_unresolved_reasons"](
            "Workspace refreshed: I will continue with the current tool surface. "
            "Completed a coherent first-pass aerospace wing-rib CAD model. "
            "Captured and inspected the viewport screenshot; the model is visible."
        )
        self.assertEqual([], reasons)

    def test_live_provider_acceptance_detects_ineffective_partdesign_features(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        ineffective = data["_ineffective_partdesign_features"](
            [
                {
                    "tool_name": "partdesign.extrude",
                    "ok": True,
                    "result": {
                        "active_feature": "Pocket",
                        "feature_effect": {
                            "ok": False,
                            "operation": "pocket",
                            "body_shape_delta": {"volume_delta": 0.0},
                        },
                    },
                },
                {
                    "tool_name": "partdesign.dressup",
                    "ok": True,
                    "result": {
                        "active_feature": "Draft",
                        "feature_effect": {
                            "ok": False,
                            "operation": "draft",
                            "body_shape_delta": {"volume_delta": 0.0},
                        },
                    },
                },
                {
                    "tool_name": "partdesign.get_bodies",
                    "ok": True,
                    "result": {"body_count": 1},
                },
                {
                    "tool_name": "partdesign.hole_from_sketch",
                    "ok": True,
                    "result": {
                        "active_feature": "Hole",
                        "feature_effect": {
                            "ok": True,
                            "operation": "hole",
                            "body_shape_delta": {"volume_delta": -100.0},
                        },
                    },
                },
            ]
        )
        self.assertEqual(len(ineffective), 2)
        self.assertEqual(ineffective[0]["feature"], "Pocket")
        self.assertEqual(ineffective[1]["tool_name"], "partdesign.dressup")
        self.assertEqual(ineffective[1]["feature"], "Draft")

    def test_live_provider_acceptance_ignores_deleted_ineffective_partdesign_features(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        ineffective = data["_ineffective_partdesign_features"](
            [
                {
                    "tool_name": "partdesign.extrude",
                    "ok": False,
                    "result": {
                        "active_feature": "Pocket",
                        "feature_effect": {
                            "ok": False,
                            "operation": "pocket",
                            "body_shape_delta": {"volume_delta": 0.0},
                        },
                    },
                },
                {
                    "tool_name": "core.delete_object",
                    "ok": True,
                    "result": {
                        "transaction_document_delta": {
                            "deleted_objects": [
                                {"name": "Pocket", "type": "PartDesign::Pocket"}
                            ]
                        }
                    },
                },
                {
                    "tool_name": "partdesign.extrude",
                    "ok": True,
                    "result": {
                        "active_feature": "Pocket",
                        "feature_effect": {
                            "ok": True,
                            "operation": "pocket",
                            "body_shape_delta": {"volume_delta": -50.0},
                        },
                    },
                },
            ]
        )
        self.assertEqual([], ineffective)

    def test_live_provider_acceptance_ignores_rolled_back_ineffective_partdesign_features(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        ineffective = data["_ineffective_partdesign_features"](
            [
                {
                    "tool_name": "partdesign.extrude",
                    "ok": False,
                    "result": {
                        "active_feature": "Pad",
                        "rolled_back_feature": True,
                        "feature_effect": {
                            "ok": False,
                            "operation": "pad",
                            "body_shape_delta": {"volume_delta": 0.0},
                        },
                    },
                }
            ]
        )
        self.assertEqual([], ineffective)

    def test_live_provider_robot_acceptance_requires_multipart_assembly_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("robot")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 2)
        self.assertGreaterEqual(requirements["minimum_assemblies"], 1)
        self.assertGreaterEqual(requirements["minimum_assembly_components"], 2)
        self.assertIn("assembly.", requirements["required_tool_prefixes"])

        evidence = data["_partdesign_evidence"](
            {
                "body_count": 1,
                "bodies": [
                    {
                        "features": [
                            {"type": "Sketcher::SketchObject"},
                            {"type": "PartDesign::Pad"},
                            {"type": "PartDesign::Body"},
                        ]
                    }
                ],
            }
        )
        self.assertEqual(evidence["body_count"], 1)
        self.assertEqual(evidence["feature_count"], 3)
        self.assertEqual(evidence["native_feature_count"], 1)

    def test_live_provider_drone_acceptance_requires_multipart_assembly_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("drone")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 2)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 4)
        self.assertGreaterEqual(requirements["minimum_assemblies"], 1)
        self.assertGreaterEqual(requirements["minimum_assembly_components"], 2)
        self.assertIn("assembly.", requirements["required_tool_prefixes"])

    def test_live_provider_automotive_acceptance_requires_complex_partdesign_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("automotive")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 1)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 4)
        self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        self.assertIn("sketcher.", requirements["required_tool_prefixes"])

    def test_live_provider_aerospace_acceptance_requires_complex_partdesign_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("aerospace")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 1)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 4)
        self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        self.assertIn("sketcher.", requirements["required_tool_prefixes"])

    def test_live_provider_marine_acceptance_requires_complex_partdesign_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("marine")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 1)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 4)
        self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        self.assertIn("sketcher.", requirements["required_tool_prefixes"])

    def test_live_provider_enclosure_acceptance_requires_multipart_assembly_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("enclosure")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 2)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 5)
        self.assertGreaterEqual(requirements["minimum_assemblies"], 1)
        self.assertGreaterEqual(requirements["minimum_assembly_components"], 2)
        self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        self.assertIn("sketcher.", requirements["required_tool_prefixes"])
        self.assertIn("assembly.", requirements["required_tool_prefixes"])

    def test_live_provider_mechanical_acceptance_requires_complex_partdesign_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("mechanical")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 1)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 4)
        self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        self.assertIn("sketcher.", requirements["required_tool_prefixes"])

    def test_live_provider_documentation_acceptance_requires_techdraw_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("documentation")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 1)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 3)
        self.assertGreaterEqual(requirements["minimum_techdraw_pages"], 1)
        self.assertGreaterEqual(requirements["minimum_techdraw_views"], 1)
        self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        self.assertIn("sketcher.", requirements["required_tool_prefixes"])
        self.assertIn("techdraw.", requirements["required_tool_prefixes"])
        evidence = data["_techdraw_evidence"](
            {
                "page_count": 1,
                "pages": [
                    {
                        "views": [
                            {"source_count": 1},
                            {"source_count": 0},
                        ]
                    }
                ],
            }
        )
        self.assertEqual(evidence["page_count"], 1)
        self.assertEqual(evidence["view_count"], 2)
        self.assertEqual(evidence["sourced_view_count"], 1)

    def test_live_provider_rocket_engine_acceptance_requires_complex_assembly_evidence(self):
        script = _repo_tool_script("vibecad_live_provider_acceptance.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_provider_acceptance_test")
        requirements = data["_requirements_for_scenario"]("rocket_engine")
        self.assertGreaterEqual(requirements["minimum_partdesign_bodies"], 3)
        self.assertGreaterEqual(requirements["minimum_partdesign_features"], 6)
        self.assertGreaterEqual(requirements["minimum_assemblies"], 1)
        self.assertGreaterEqual(requirements["minimum_assembly_components"], 3)
        self.assertIn("partdesign.", requirements["required_tool_prefixes"])
        self.assertIn("sketcher.", requirements["required_tool_prefixes"])
        self.assertIn("assembly.", requirements["required_tool_prefixes"])

        scenarios = data["SCENARIOS"]
        self.assertIn("multiple named PartDesign component bodies", scenarios["rocket_engine"])
        self.assertIn("at least six surviving native PartDesign features", scenarios["rocket_engine"])

    def test_live_acceptance_matrix_defaults_cover_full_goal_matrix(self):
        script = _repo_tool_script("vibecad_live_acceptance_matrix.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_acceptance_matrix_test")
        scenarios = set(data["DEFAULT_SCENARIOS"])
        self.assertTrue(
            {
                "mechanical",
                "partdesign",
                "robot",
                "drone",
                "automotive",
                "aerospace",
                "marine",
                "enclosure",
                "assembly",
                "documentation",
                "rocket_engine",
            }.issubset(scenarios)
        )

    def test_live_acceptance_matrix_preserves_timeout_partial_evidence(self):
        script = _repo_tool_script("vibecad_live_acceptance_matrix.py")
        data = runpy.run_path(str(script), run_name="vibecad_live_acceptance_matrix_test")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            progress = base / "progress.jsonl"
            request_dumps = base / "request-dumps"
            request_dumps.mkdir()
            progress.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event": "tool_call_completed",
                                "tool_name": "partdesign.create_sketch",
                                "ok": True,
                                "safety": "safe_write",
                                "result": {
                                    "transaction_document_delta": {
                                        "object_count_after": 10,
                                    }
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "event": "tool_call_completed",
                                "tool_name": "core.capture_view_screenshot",
                                "ok": True,
                                "safety": "view",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            (request_dumps / "latest-openai-request.json").write_text(
                json.dumps(
                    {
                        "schema": "vibecad-openai-agents-request-v1",
                        "agent": {"tools": [{"function_name": "pd_sk"}]},
                        "model_visible_context": {"workbench": "PartDesignWorkbench"},
                    }
                ),
                encoding="utf-8",
            )

            result = data["_partial_result_from_progress"](
                "mechanical",
                progress,
                request_dumps,
                "matrix process timeout after 1 seconds",
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["partial_evidence"])
        self.assertEqual(result["tool_count"], 2)
        self.assertEqual(result["mutating_tool_count"], 1)
        self.assertEqual(result["object_count"], 10)
        self.assertTrue(result["screenshot_captured"])
        self.assertEqual(result["request_dump"]["schema"], "vibecad-openai-agents-request-v1")
        summary = data["_scenario_summary"](
            "mechanical",
            {"ok": True, "provider_timeout_event_count": 2, "request_dump": result["request_dump"]},
            1.0,
            0,
        )
        self.assertEqual(summary["provider_timeout_event_count"], 2)
