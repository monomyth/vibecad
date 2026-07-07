# SPDX-License-Identifier: LGPL-2.1-or-later

import contextlib
import os
import json
from pathlib import Path
import sys
import tempfile
import types
from typing import Any

import VibeCADProject
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
    ProviderResult,
    ProviderUnavailable,
)
from VibeCADSession import (
    CORE_PROVIDER_TOOLS,
    MAX_OPERATION_SCORE_PER_PROVIDER_TURN,
    MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV,
    MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV,
    _effective_provider_workbench,
    _max_operation_score_per_provider_turn,
    _max_mutating_tool_calls_per_provider_turn,
    _missing_requirement_lines,
    _operation_score_for_tool,
    _provider_loop_state,
    _result_summary,
    _should_continue_autonomously,
    _tool_batch_checkpoint_reached,
    make_provider_tool_runner,
    provider_tool_scope_for_context,
    provider_safe_tool_schemas,
    run_prompt,
)
from VibeCADTools import SafetyLevel
import VibeCADTransactions
from VibeCADTransactions import (
    _bounded_report_view_line,
    _extract_error_blocks,
    _is_report_view_error_line,
    report_view_error_summary,
    run_freecad_transaction,
)
from VibeCADWorkbenchTools import get_tool_pack

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
    _attach_temp_project_store,
    _gui_workbench_api_available,
    _temporary_design_project,
)


class _FakeReportViewWidget:
    """Minimal stand-in for the FreeCAD Report View text widget."""

    def __init__(self) -> None:
        self._text = ""

    def set_text(self, text: str) -> None:
        self._text = text

    def append_text(self, text: str) -> None:
        self._text += text

    def objectName(self) -> str:  # noqa: N802 - Qt naming
        return "Report view"

    def windowTitle(self) -> str:  # noqa: N802 - Qt naming
        return "Report view"

    def toPlainText(self) -> str:  # noqa: N802 - Qt naming
        return self._text


@contextlib.contextmanager
def _fake_report_view_widget():
    """Patch FreeCADGui/PySide so report_view_error_summary sees a fake widget."""
    widget = _FakeReportViewWidget()

    class _FakeMainWindow:
        def findChildren(self, widget_class):  # noqa: N802 - Qt naming
            if widget_class is _FakeQtWidgets.QPlainTextEdit:
                return [widget]
            return []

    class _FakeQtWidgets:
        class QPlainTextEdit:
            pass

        class QTextEdit:
            pass

    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.getMainWindow = lambda: _FakeMainWindow()
    fake_pyside = types.ModuleType("PySide")
    fake_pyside.QtWidgets = _FakeQtWidgets

    saved_modules = {name: sys.modules.get(name) for name in ("FreeCADGui", "PySide")}
    saved_cursors = dict(VibeCADTransactions._REPORT_VIEW_CURSORS)
    VibeCADTransactions._REPORT_VIEW_CURSORS.clear()
    sys.modules["FreeCADGui"] = fake_gui
    sys.modules["PySide"] = fake_pyside
    try:
        yield widget
    finally:
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        VibeCADTransactions._REPORT_VIEW_CURSORS.clear()
        VibeCADTransactions._REPORT_VIEW_CURSORS.update(saved_cursors)


class TestVibeCADSessionLoop(SettingsSnapshotTestCase):
    def test_tool_runner_attaches_midrun_steering_to_tool_result(self):
        service = VibeCADService()
        queued = ["make the yoke removable before continuing"]
        events: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []

        def steering_check():
            if not queued:
                return []
            return [queued.pop(0)]

        runner = make_provider_tool_runner(
            service,
            tool_trace=trace,
            progress_callback=events.append,
            steering_check=steering_check,
        )
        result = runner("core.get_active_document", "{}")

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["human_steering"]["messages"],
            ["make the yoke removable before continuing"],
        )
        self.assertEqual(queued, [])
        self.assertTrue(
            any(
                item.get("event") == "human_steering_consumed"
                and item.get("message_count") == 1
                for item in events
            ),
            events,
        )
        self.assertEqual(trace[-1]["tool_name"], "core.get_active_document")

    def test_tool_runner_cancellation_does_not_consume_queued_steering(self):
        queued = ["change direction after the stop clears"]
        runner = make_provider_tool_runner(
            VibeCADService(),
            cancellation_check=lambda: True,
            steering_check=lambda: [queued.pop(0)] if queued else [],
        )

        result = runner("core.get_active_document", "{}")

        self.assertFalse(result["ok"], result)
        self.assertTrue(result["cancelled"])
        self.assertNotIn("human_steering", result)
        self.assertEqual(queued, ["change direction after the stop clears"])

    def test_run_prompt_does_not_fake_offline_response_after_provider_failure(self):
        class FailingProvider(BaseProvider):
            def run(self, prompt, context, tool_runner=None):
                raise ProviderUnavailable("configured provider failed")

        service = VibeCADService()
        response = run_prompt("hello", service=service, provider=FailingProvider())
        self.assertEqual(response.provider, "FailingProvider")
        self.assertEqual(response.error, "configured provider failed")
        self.assertIn("configured provider failed", response.final_output)
        self.assertNotIn("OfflineProvider", response.final_output)

    def test_run_prompt_cancel_before_provider_turn_does_not_call_provider(self):
        class CountingProvider(BaseProvider):
            def __init__(self):
                self.calls = 0

            def run(self, prompt, context, tool_runner=None, cancellation_check=None):
                self.calls += 1
                return ProviderResult("should not run")

        events = []
        provider = CountingProvider()
        response = run_prompt(
            "hello",
            service=VibeCADService(),
            provider=provider,
            cancellation_check=lambda: True,
            progress_callback=events.append,
        )

        self.assertEqual(provider.calls, 0)
        self.assertIn("stopped by user", response.final_output)
        self.assertTrue(
            any(event.get("event") == "provider_run_cancelled" for event in events)
        )

    def test_result_summary_includes_native_transaction_failure_details(self):
        summary = _result_summary(
            {
                "ok": False,
                "transaction": {
                    "ok": False,
                    "error": "native mirror failed",
                    "report_view_errors": {
                        "captured": True,
                        "errors": ["native mirror failed"],
                    },
                    "document_delta": {"object_count_delta": 0},
                },
            }
        )

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["transaction_error"], "native mirror failed")
        self.assertIn("native mirror failed", str(summary["transaction_report_view_errors"]))
        self.assertEqual(summary["transaction_document_delta"]["object_count_delta"], 0)

        nested_summary = _result_summary(
            {
                "ok": False,
                "result": {
                    "ok": False,
                    "transaction": {
                        "ok": False,
                        "error": "native revolve failed",
                        "document_delta": {"object_count_delta": 1},
                    },
                },
            }
        )
        self.assertEqual(nested_summary["transaction_error"], "native revolve failed")
        self.assertEqual(nested_summary["transaction_document_delta"]["object_count_delta"], 1)

    def test_report_view_error_filter_ignores_vibecad_progress_noise(self):
        self.assertFalse(_is_report_view_error_line("Report errors: 12"))
        self.assertFalse(_is_report_view_error_line("No report-view errors detected."))
        self.assertFalse(
            _is_report_view_error_line(
                '23:05:13  {"progress": {"event": "tool_call_completed", "ok": true}}'
            )
        )
        self.assertTrue(_is_report_view_error_line("Traceback: native sketch solver failed"))
        self.assertTrue(_is_report_view_error_line("PartDesign error: pocket failed"))
        self.assertTrue(
            _is_report_view_error_line(
                "<Sketch> SketchObject.cpp(426): Failed to make face for sketch: "
                "Part::FaceMaker: result shape is null."
            )
        )
        self.assertTrue(
            _is_report_view_error_line(
                "EdgeBevelChamfer: Invalid edge link: ;#c:2;:H29c,E.Edge9"
            )
        )
        self.assertTrue(
            _is_report_view_error_line(
                "Micro_Edge_Break_All_Blades_And_Hub: BRep_API: command not done"
            )
        )
        self.assertEqual(
            _bounded_report_view_line("x" * 600),
            ("x" * 497) + "...",
        )

    def test_extract_error_blocks_groups_python_tracebacks(self):
        lines = [
            "12:00:01 Info: recompute finished",
            "Traceback (most recent call last):",
            '  File "macro.py", line 3, in <module>',
            "    body.newObject('PartDesign::Pocket')",
            "RuntimeError: native pocket failed",
            "12:00:02 Info: done",
            "Part error: fillet radius too large",
        ]
        blocks = _extract_error_blocks(lines)
        self.assertEqual(len(blocks), 2, blocks)
        traceback_start, traceback_block = blocks[0]
        self.assertEqual(traceback_start, 1)
        self.assertIn("Traceback (most recent call last):", traceback_block)
        self.assertIn('File "macro.py"', traceback_block)
        self.assertIn("RuntimeError: native pocket failed", traceback_block)
        self.assertEqual(traceback_block.count("\n"), 3)
        fillet_start, fillet_block = blocks[1]
        self.assertEqual(fillet_start, 6)
        self.assertEqual(fillet_block, "Part error: fillet radius too large")

    def test_extract_error_blocks_bounds_giant_tracebacks(self):
        lines = ["Traceback (most recent call last):"]
        lines += [f'  File "deep.py", line {i}, in frame_{i}' for i in range(200)]
        lines += ["RecursionError: maximum recursion depth exceeded"]
        blocks = _extract_error_blocks(lines)
        self.assertEqual(len(blocks), 1)
        self.assertLessEqual(len(blocks[0][1]), 2000)

    def test_report_view_error_summary_returns_only_new_errors(self):
        with _fake_report_view_widget() as widget:
            widget.set_text(
                "Recompute......\n"
                "PartDesign error: pocket failed\n"
            )
            first = report_view_error_summary()
            self.assertTrue(first["captured"], first)
            self.assertEqual(first["errors"], ["PartDesign error: pocket failed"])
            self.assertEqual(first["stale_error_count"], 0)

            # Same widget text: the error was consumed by the first read.
            second = report_view_error_summary()
            self.assertEqual(second["errors"], [])
            self.assertEqual(second["stale_error_count"], 1)

            # A new error after a successful feature is reported alone.
            widget.append_text(
                "Recompute......\n"
                "Traceback (most recent call last):\n"
                '  File "op.py", line 9, in <module>\n'
                "ValueError: helix pitch must be positive\n"
            )
            third = report_view_error_summary()
            self.assertEqual(len(third["errors"]), 1, third)
            self.assertIn("ValueError: helix pitch must be positive", third["errors"][0])
            self.assertIn("Traceback (most recent call last):", third["errors"][0])
            self.assertEqual(third["stale_error_count"], 1)

            # include_stale re-reads the full history without resetting counts.
            stale = report_view_error_summary(include_stale=True)
            self.assertEqual(len(stale["errors"]), 2, stale)
            self.assertEqual(stale["stale_error_count"], 2)

    def test_report_view_error_summary_resets_cursor_when_widget_clears(self):
        with _fake_report_view_widget() as widget:
            widget.set_text("Part error: boolean failed\n" * 5)
            report_view_error_summary()

            # Report view cleared and a fresh error arrives: it must be new.
            widget.set_text("Sketcher error: over-constrained\n")
            summary = report_view_error_summary()
            self.assertEqual(summary["errors"], ["Sketcher error: over-constrained"])
            self.assertEqual(summary["stale_error_count"], 0)

    def test_transaction_fails_on_fresh_report_view_errors(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADReportErrorTransactionTest")
        try:
            with _fake_report_view_widget() as widget:
                def _bad_operation():
                    doc.addObject("App::DocumentObjectGroup", "ReportErrorCreatedObject")
                    widget.append_text(
                        "<Sketch> SketchObject.cpp(426): Failed to make face for sketch: "
                        "Part::FaceMaker: result shape is null.\n"
                    )
                    return {"created": "ReportErrorCreatedObject"}

                result = run_freecad_transaction("Report error operation", _bad_operation)

            self.assertFalse(result["ok"], result)
            self.assertIn("FreeCAD reported an error", result.get("error", ""))
            self.assertTrue(result.get("rolled_back_transaction"), result)
            self.assertEqual(
                result["created_object_cleanup"]["removed_objects"],
                ["ReportErrorCreatedObject"],
            )
            self.assertIn("Failed to make face", str(result.get("report_view_errors")))
            self.assertFalse(result["verification"]["ok"])
            self.assertIsNone(doc.getObject("ReportErrorCreatedObject"))
        finally:
            App.closeDocument(doc.Name)

    def test_result_summary_includes_assembly_payload(self):
        summary = _result_summary(
            {
                "ok": True,
                "result": {
                    "ok": True,
                    "assembly": "Assembly",
                    "assembly_label": "Fixture Assembly",
                    "component": "BasePlate",
                    "component_label": "Base Plate",
                    "components": 2,
                    "components_added": ["BasePlate", "Jaw"],
                    "missing_components": [],
                    "already_present": False,
                    "assembly_summary": {
                        "assembly_count": 1,
                        "assemblies": [{"label": "Fixture Assembly", "components": 2}],
                    },
                },
            }
        )

        self.assertEqual(summary["assembly_label"], "Fixture Assembly")
        self.assertEqual(summary["component_label"], "Base Plate")
        self.assertEqual(summary["components"], 2)
        self.assertEqual(summary["components_added"], ["BasePlate", "Jaw"])
        self.assertEqual(summary["assembly_summary"]["assembly_count"], 1)

    def test_failed_transaction_includes_document_delta(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADFailedTransactionDeltaTest")
        try:
            def _fail_after_object():
                doc.addObject("App::DocumentObjectGroup", "FailedCreatedObject")
                raise RuntimeError("intentional native failure")

            result = run_freecad_transaction("Fail after object", _fail_after_object)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "intentional native failure")
            self.assertIn("document_delta", result)
            self.assertGreaterEqual(result["document_delta"]["object_count_delta"], 0)
            created_names = {
                item["name"]
                for item in result["document_delta"].get("created_objects", [])
            }
            if doc.getObject("FailedCreatedObject") is not None:
                self.assertIn("FailedCreatedObject", created_names)
        finally:
            App.closeDocument(doc.Name)

    def test_transaction_document_delta_includes_shape_changes(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADTransactionShapeDeltaTest")
        try:
            box = doc.addObject("Part::Box", "ShapeDeltaBox")
            box.Length = 10
            box.Width = 10
            box.Height = 10
            doc.recompute()

            def _change_shape():
                box.Length = 20
                doc.recompute()
                return {"box": box.Name}

            result = run_freecad_transaction("Change box shape", _change_shape)

            self.assertTrue(result["ok"], result)
            changed = {
                item["name"]: item
                for item in result["document_delta"].get("changed_objects", [])
            }
            self.assertIn("ShapeDeltaBox", changed)
            before_shape = changed["ShapeDeltaBox"]["before"]["shape"]
            after_shape = changed["ShapeDeltaBox"]["after"]["shape"]
            self.assertAlmostEqual(before_shape["volume"], 1000.0)
            self.assertAlmostEqual(after_shape["volume"], 2000.0)
        finally:
            App.closeDocument(doc.Name)

    def test_transaction_snapshot_omits_app_datum_shapes(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADTransactionDatumShapeFilterTest")
        try:
            plane = doc.addObject("App::Plane", "DatumPlane")
            box = doc.addObject("Part::Box", "RealBox")
            box.Length = 10
            box.Width = 10
            box.Height = 10
            doc.recompute()

            def _change_shape():
                box.Height = 12
                doc.recompute()
                return {"box": box.Name, "plane": plane.Name}

            result = run_freecad_transaction("Filter datum shapes", _change_shape)

            self.assertTrue(result["ok"], result)
            after = {
                item["name"]: item
                for item in result["document_after"].get("objects", [])
            }
            self.assertNotIn("shape", after["DatumPlane"])
            self.assertIn("shape", after["RealBox"])
        finally:
            App.closeDocument(doc.Name)

    def test_result_summary_preserves_partdesign_feature_effect(self):
        summary = _result_summary(
            {
                "ok": True,
                "result": {
                    "active_feature": "Pad",
                    "feature_effect": {
                        "ok": True,
                        "operation": "pad",
                        "body_shape_delta": {"volume_delta": 100.0},
                    },
                    "feature_shape": {"available": True, "faces": 6, "volume": 100.0},
                    "body_shape_delta": {"volume_delta": 100.0},
                    "rolled_back_feature": True,
                    "body_shape_after_rollback": {
                        "available": True,
                        "faces": 6,
                        "volume": 100.0,
                    },
                },
            }
        )

        self.assertTrue(summary["feature_effect"]["ok"])
        self.assertEqual(summary["body_shape_delta"]["volume_delta"], 100.0)
        self.assertTrue(summary["rolled_back_feature"])
        self.assertEqual(summary["body_shape_after_rollback"]["volume"], 100.0)

    def test_loop_requirements_are_state_based_not_prompt_keyword_gates(self):
        prompts = (
            "Make me a mounting bracket with bolt holes and chamfered edges.",
            "Make me a robot with at least: base, shoulder, wrist.",
            "Paint this assembly with transparent blue material appearance.",
            "Create a TechDraw detail drawing page for this model.",
        )
        empty_context = {"document": {"object_count": 0, "objects": []}}
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(_missing_requirement_lines(prompt, empty_context, []), [])

        attempted_write = [{"tool_name": "partdesign.extrude", "ok": False, "safety": "safe_write"}]
        self.assertIn(
            "first meaningful native FreeCAD geometry",
            _missing_requirement_lines("Any prompt text", empty_context, attempted_write)[0],
        )

        sketch_context = {
            "document": {"object_count": 1, "objects": [{"type": "Sketcher::SketchObject"}]},
            "sketcher": {
                "profile_status": {
                    "found": True,
                    "sketch": "Sketch",
                    "geometry_count": 4,
                    "closed_profile": True,
                    "fully_constrained": False,
                    "degrees_of_freedom": 2,
                }
            },
        }
        self.assertEqual(
            _missing_requirement_lines("No keyword dependency", sketch_context, []),
            ["- fully constrain Sketch (2 degrees of freedom) before creating dependent features"],
        )

    def test_loop_requirements_need_fresh_screenshot_after_latest_write(self):
        context = {
            "document": {
                "object_count": 2,
                "objects": [{"type": "PartDesign::Body"}, {"type": "PartDesign::Pad"}],
            },
            "view_screenshot": {
                "captured": True,
                "file_size": 1234,
                "visual_observation": {"available": True, "mostly_blank": False},
            },
        }
        write = {
            "tool_name": "partdesign.extrude",
            "ok": True,
            "safety": SafetyLevel.SAFE_WRITE.value,
        }
        screenshot = {
            "tool_name": "core.capture_view_screenshot",
            "ok": True,
            "safety": SafetyLevel.VIEW.value,
        }

        stale_lines = _missing_requirement_lines(
            "Create CAD geometry",
            context,
            [screenshot, write],
        )
        self.assertIn("after the latest geometry changes", stale_lines[-1])

        fresh_lines = _missing_requirement_lines(
            "Create CAD geometry",
            context,
            [write, screenshot],
        )
        self.assertNotIn("after the latest geometry changes", "\n".join(fresh_lines))

        headless_screenshot = {
            "tool_name": "core.capture_view_screenshot",
            "ok": False,
            "safety": SafetyLevel.VIEW.value,
            "result": {"error": "module 'FreeCADGui' has no attribute 'ActiveDocument'"},
        }
        headless_lines = _missing_requirement_lines(
            "Create CAD geometry",
            context,
            [write, headless_screenshot],
        )
        self.assertNotIn("after the latest geometry changes", "\n".join(headless_lines))

    def test_loop_requirements_do_not_parse_prompt_for_scenario_gates(self):
        context = {
            "document": {
                "object_count": 3,
                "objects": [
                    {"type": "PartDesign::Body"},
                    {"type": "PartDesign::Pad"},
                    {"type": "Sketcher::SketchObject"},
                ],
            },
            "partdesign": {
                "bodies": [
                    {
                        "features": [
                            {"type": "Sketcher::SketchObject"},
                            {"type": "PartDesign::Pad"},
                        ]
                    },
                ]
            },
            "assembly": {"assembly_count": 0, "assemblies": []},
        }
        lines = _missing_requirement_lines(
            "Create a native assembly with at least four surviving native PartDesign features.",
            context,
            [],
        )
        self.assertFalse(any("native PartDesign feature depth" in line for line in lines), lines)
        self.assertFalse(any("native Assembly object" in line for line in lines), lines)

    def test_loop_requirements_require_assembly_for_multi_body_state(self):
        context = {
            "document": {
                "object_count": 4,
                "objects": [
                    {"type": "PartDesign::Body"},
                    {"type": "PartDesign::Pad"},
                    {"type": "PartDesign::Body"},
                    {"type": "PartDesign::Pad"},
                ],
            },
            "partdesign": {
                "body_count": 2,
                "bodies": [
                    {"features": [{"type": "PartDesign::Pad"}]},
                    {"features": [{"type": "PartDesign::Pad"}]},
                ],
            },
            "assembly": {"assembly_count": 0, "assemblies": []},
        }
        for prompt in (
            "Continue this model.",
            "Create a native assembly with at least four surviving native PartDesign features.",
        ):
            with self.subTest(prompt=prompt):
                lines = _missing_requirement_lines(prompt, context, [])
                self.assertTrue(any("multi-body component geometry" in line for line in lines), lines)
                self.assertFalse(any("native PartDesign feature depth" in line for line in lines), lines)

        context["assembly"] = {"assembly_count": 1, "assemblies": [{"components": 1}]}
        lines = _missing_requirement_lines("Continue this model.", context, [])
        self.assertTrue(any("fewer components than generated PartDesign bodies" in line for line in lines), lines)

        context["assembly"] = {"assembly_count": 1, "assemblies": [{"components": 2}]}
        lines = _missing_requirement_lines("Continue this model.", context, [])
        self.assertFalse(any("Assembly" in line or "component bodies" in line for line in lines), lines)

    def test_loop_requirements_gate_grounding_and_joints_for_kinematic_assemblies(self):
        context = {
            "document": {"object_count": 1, "objects": [{"type": "Assembly::AssemblyObject"}]},
            "partdesign": {"body_count": 0, "bodies": []},
            "assembly": {
                "assembly_count": 1,
                "assemblies": [
                    {
                        "name": "Assembly",
                        "components": 2,
                        "grounded_count": 0,
                        "joints": 0,
                    }
                ],
            },
        }
        lines = _missing_requirement_lines("Continue this model.", context, [])
        self.assertTrue(any("assembly.ground_component" in line for line in lines), lines)
        self.assertFalse(any("assembly.create_joint" in line for line in lines), lines)

        context["assembly"]["assemblies"][0]["grounded_count"] = 1
        lines = _missing_requirement_lines("Continue this model.", context, [])
        self.assertFalse(any("assembly.ground_component" in line for line in lines), lines)
        joints_lines = [line for line in lines if "assembly.create_joint" in line]
        self.assertEqual(len(joints_lines), 1, lines)
        self.assertIn("assembly.solve", joints_lines[0])
        self.assertIn("raw placement is layout, not mating", joints_lines[0])

        context["assembly"]["assemblies"][0]["joints"] = 1
        lines = _missing_requirement_lines("Continue this model.", context, [])
        self.assertFalse(
            any("assembly.ground_component" in line or "assembly.create_joint" in line for line in lines),
            lines,
        )

        # Single-component assemblies need no kinematic gating.
        context["assembly"]["assemblies"][0].update(
            {"components": 1, "grounded_count": 0, "joints": 0}
        )
        lines = _missing_requirement_lines("Continue this model.", context, [])
        self.assertFalse(
            any("assembly.ground_component" in line or "assembly.create_joint" in line for line in lines),
            lines,
        )

        # Malformed assembly context entries are tolerated without gating noise.
        context["assembly"] = {
            "assembly_count": 1,
            "assemblies": [None, "junk", {"components": "not-a-number"}],
        }
        lines = _missing_requirement_lines("Continue this model.", context, [])
        self.assertFalse(
            any("assembly.ground_component" in line or "assembly.create_joint" in line for line in lines),
            lines,
        )

    def test_cam_execution_contract_requires_machine_validated_machining(self):
        from VibeCADSession import _execution_contract_for_context

        contract = _execution_contract_for_context({"workbench": "CAMWorkbench"})
        self.assertEqual(contract["mode"], "machine_validated_machining")
        required_order = " ".join(contract["required_order"])
        for tool in (
            "cam.define_machine",
            "cam.create_job",
            "cam.add_tool",
            "cam.create_operation",
            "cam.validate_job",
            "cam.postprocess",
        ):
            self.assertIn(tool, required_order, tool)
        gates = " ".join(contract["completion_gates"])
        self.assertIn("bound to a machine", gates)
        self.assertIn("validation", gates)

    def test_loop_requirements_gate_cam_jobs_without_machine_binding(self):
        context = {
            "workbench": "CAMWorkbench",
            "document": {"object_count": 3, "objects": []},
            "cam": {
                "job_count": 1,
                "jobs": [{"name": "Job", "label": "Job", "machine": None}],
            },
        }
        lines = _missing_requirement_lines("machine the part", context, [])
        self.assertTrue(any("not bound to a machine" in line for line in lines), lines)

        context["cam"]["jobs"][0]["machine"] = "Generic LinuxCNC Mill"
        lines = _missing_requirement_lines("machine the part", context, [])
        self.assertFalse(any("not bound to a machine" in line for line in lines), lines)

        # Malformed cam context entries are tolerated without gating noise.
        context["cam"] = {"job_count": 1, "jobs": [None, "junk", {"no_machine_key": True}]}
        lines = _missing_requirement_lines("machine the part", context, [])
        self.assertFalse(any("not bound to a machine" in line for line in lines), lines)

    def test_loop_requirements_gate_unvalidated_or_forced_postprocess(self):
        context = {
            "workbench": "CAMWorkbench",
            "document": {"object_count": 3, "objects": []},
            "cam": {
                "job_count": 1,
                "jobs": [{"name": "Job", "label": "Job", "machine": "Mill"}],
            },
        }

        unvalidated = [
            {
                "tool_name": "cam.postprocess",
                "ok": True,
                "result": {"ok": True, "output_path": "/tmp/x.nc"},
            }
        ]
        lines = _missing_requirement_lines("machine the part", context, unvalidated)
        self.assertTrue(
            any("without a machine validation" in line for line in lines), lines
        )

        forced = [
            {
                "tool_name": "cam.postprocess",
                "ok": True,
                "result": {
                    "ok": True,
                    "output_path": "/tmp/x.nc",
                    "validation": {"error_count": 1, "warning_count": 0, "forced": True},
                },
            }
        ]
        lines = _missing_requirement_lines("machine the part", context, forced)
        self.assertTrue(
            any("despite unresolved machine" in line for line in lines), lines
        )

        clean = [
            {
                "tool_name": "cam.postprocess",
                "ok": True,
                "result": {
                    "ok": True,
                    "output_path": "/tmp/x.nc",
                    "validation": {"valid": True, "error_count": 0, "warning_count": 0},
                },
            }
        ]
        lines = _missing_requirement_lines("machine the part", context, clean)
        self.assertFalse(
            any("validation" in line or "G-code" in line for line in lines), lines
        )

        # The steering lines flow into loop-state validation notes so an
        # unvalidated postprocess refuses autonomous completion.
        state = _provider_loop_state("machine the part", context, unvalidated, 1, False)
        notes = state.get("state_validation_notes", [])
        self.assertTrue(
            any("without a machine validation" in str(note) for note in notes), notes
        )

    def test_assembly_execution_contract_requires_grounding_joints_and_solve(self):
        from VibeCADSession import _execution_contract_for_context

        contract = _execution_contract_for_context({"workbench": "AssemblyWorkbench"})
        self.assertEqual(contract["mode"], "native_assembly")
        required_order = " ".join(contract["required_order"])
        self.assertIn("assembly.ground_component", required_order)
        self.assertIn("assembly.create_joint", required_order)
        self.assertIn("partdesign.find_subelements", required_order)
        self.assertIn("never by dead-reckoned raw placement", required_order)
        self.assertIn("assembly.solve", required_order)
        gates = " ".join(contract["completion_gates"])
        self.assertIn("grounded component", gates)
        self.assertIn("raw placement is layout, not mating", gates)
        self.assertIn("solve successfully", gates)

    def test_provider_safe_tool_schemas_expose_only_command_write_tools(self):
        service = VibeCADService()
        names = {schema["name"] for schema in provider_safe_tool_schemas(service)}
        self.assertIn("core.get_active_document", names)
        self.assertNotIn("core.create_new_document", names)
        self.assertNotIn("core.open_document", names)
        self.assertIn("core.delete_object", names)
        self.assertIn("core.report_tool_shape_gap", names)
        self.assertNotIn("core.run_workbench_command", names)
        self.assertIn("core.get_tool_shape_report", names)
        self.assertIn("core.wait_for_user_gui_action", names)
        self.assertNotIn("core.propose_run_workbench_command", names)
        self.assertIn("core.capture_view_screenshot", names)
        self.assertIn("core.set_view", names)
        self.assertIn("core.get_report_view_errors", names)
        self.assertNotIn("core.propose_create_part_box", names)
        self.assertNotIn("core.propose_create_workbench_object", names)
        self.assertNotIn("core.propose_set_object_label", names)
        self.assertNotIn("core.propose_set_selected_property", names)
        self.assertNotIn("core.undo_last_vibecad_action", names)
        self.assertNotIn("core.clear_local_session", names)
        self.assertNotIn("core.run_workbench_command", names)

    def test_provider_tool_modules_cover_provider_safe_tools(self):
        from provider_tools import registered_tool_names

        service = VibeCADService()
        workbenches = [
            "PartWorkbench",
            "PartDesignWorkbench",
            "SketcherWorkbench",
            "DraftWorkbench",
            "AssemblyWorkbench",
            "TechDrawWorkbench",
            "MaterialWorkbench",
            "NoneWorkbench",
        ]
        missing = []
        registered = registered_tool_names()
        for workbench in workbenches:
            for schema in provider_safe_tool_schemas(service, workbench):
                if schema["name"] not in registered:
                    missing.append((workbench, schema["name"]))
        self.assertEqual(missing, [])

    def test_provider_tool_registry_contains_only_direct_model_tools(self):
        from provider_tools import registered_tool_names

        names = registered_tool_names()
        self.assertFalse([name for name in names if ".propose_" in name], names)
        self.assertNotIn("core.undo_last_vibecad_action", names)
        self.assertNotIn("core.clear_local_session", names)

    def test_provider_safe_tool_schemas_are_workbench_scoped(self):
        service = VibeCADService()

        def surface(workbench):
            return {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, workbench)
            }

        part_names = surface("PartWorkbench")
        sketcher_names = surface("SketcherWorkbench")
        partdesign_names = surface("PartDesignWorkbench")

        # Part workbench pack: consolidated dressup, no PartDesign/sketcher tools.
        self.assertIn("part.set_placement", part_names)
        self.assertIn("part.cut_cylindrical_hole", part_names)
        self.assertIn("part.dressup", part_names)
        self.assertIn("part.thicken_surface", part_names)
        self.assertIn("partdesign.find_subelements", part_names)
        self.assertNotIn("draft.create_array", part_names)
        self.assertNotIn("partdesign.create_sketch", part_names)
        self.assertNotIn("sketcher.add_geometry", part_names)
        self.assertNotIn("core.run_workbench_command", part_names)

        # Sketcher pack: consolidated multi-function tools only.
        # sketcher.get_sketch was retired: sketcher.inspect_sketch is a strict
        # superset (adds solver DoF, profile readiness, repair diagnostics).
        self.assertIn("core.report_tool_shape_gap", sketcher_names)
        self.assertNotIn("sketcher.get_sketch", sketcher_names)
        self.assertIn("sketcher.create_sketch", sketcher_names)
        self.assertIn("sketcher.open_sketch", sketcher_names)
        self.assertIn("sketcher.close_sketch", sketcher_names)
        self.assertIn("sketcher.inspect_sketch", sketcher_names)
        self.assertIn("sketcher.add_geometry", sketcher_names)
        self.assertIn("sketcher.add_hole_pattern", sketcher_names)
        self.assertIn("sketcher.add_slot", sketcher_names)
        self.assertIn("sketcher.draw_rectangle", sketcher_names)
        self.assertIn("sketcher.add_constraint", sketcher_names)
        self.assertIn("sketcher.edit_constraint", sketcher_names)
        self.assertIn("sketcher.delete_items", sketcher_names)
        self.assertIn("sketcher.modify_geometry", sketcher_names)
        self.assertIn("sketcher.transform_geometry", sketcher_names)
        self.assertIn("sketcher.move_point", sketcher_names)
        self.assertIn("sketcher.resolve_geometry", sketcher_names)
        self.assertIn("sketcher.set_geometry_name", sketcher_names)
        self.assertIn("sketcher.set_construction", sketcher_names)
        self.assertIn("sketcher.add_external_geometry", sketcher_names)
        self.assertIn("sketcher.remove_external_geometry", sketcher_names)
        self.assertNotIn("partdesign.extrude", sketcher_names)

        # Retired single-function sketcher tools must not resurface anywhere.
        retired_sketcher = {
            "sketcher.add_line",
            "sketcher.add_point",
            "sketcher.add_polyline",
            "sketcher.add_circle",
            "sketcher.add_arc",
            "sketcher.add_ellipse",
            "sketcher.add_bspline",
            "sketcher.constrain_coincident",
            "sketcher.constrain_distance",
            "sketcher.constrain_radius",
            "sketcher.set_constraint_value",
            "sketcher.get_constraint_by_name",
            "sketcher.list_geometry",
            "sketcher.list_constraints",
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
        }
        for names, label in (
            (sketcher_names, "sketcher"),
            (partdesign_names, "partdesign"),
        ):
            leaked = retired_sketcher & names
            self.assertFalse(leaked, (label, sorted(leaked)))

        # PartDesign pack: consolidated features + sketcher tools minus
        # sketcher.create_sketch (Body sketches come from partdesign.create_sketch).
        self.assertIn("partdesign.get_bodies", partdesign_names)
        self.assertIn("partdesign.create_body", partdesign_names)
        self.assertIn("partdesign.create_sketch", partdesign_names)
        self.assertIn("partdesign.extrude", partdesign_names)
        self.assertIn("partdesign.revolve", partdesign_names)
        self.assertIn("partdesign.pattern", partdesign_names)
        self.assertIn("partdesign.dressup", partdesign_names)
        self.assertIn("partdesign.hole_from_sketch", partdesign_names)
        self.assertIn("partdesign.loft_profiles", partdesign_names)
        self.assertIn("partdesign.sweep_profile", partdesign_names)
        self.assertIn("partdesign.helix_profile", partdesign_names)
        self.assertIn("partdesign.set_feature_dimensions", partdesign_names)
        self.assertIn("sketcher.add_geometry", partdesign_names)
        self.assertIn("sketcher.add_hole_pattern", partdesign_names)
        self.assertIn("sketcher.add_slot", partdesign_names)
        self.assertIn("sketcher.add_constraint", partdesign_names)
        self.assertIn("sketcher.edit_constraint", partdesign_names)
        self.assertIn("sketcher.draw_rectangle", partdesign_names)
        self.assertIn("sketcher.inspect_sketch", partdesign_names)
        self.assertIn("sketcher.transform_geometry", partdesign_names)
        self.assertIn("sketcher.modify_geometry", partdesign_names)
        self.assertIn("sketcher.delete_items", partdesign_names)
        self.assertNotIn("sketcher.create_sketch", partdesign_names)
        self.assertNotIn("core.run_workbench_command", partdesign_names)
        self.assertNotIn("part.set_placement", partdesign_names)
        self.assertNotIn("part.dressup", partdesign_names)
        self.assertNotIn("draft.create_array", partdesign_names)
        self.assertNotIn("assembly.create_assembly", partdesign_names)
        self.assertNotIn("techdraw.create_page", partdesign_names)
        self.assertNotIn("material.apply_appearance", partdesign_names)
        self.assertLessEqual(len(partdesign_names), 100)

        # Remaining modeling/documentation packs.
        spreadsheet_names = surface("SpreadsheetWorkbench")
        self.assertIn("spreadsheet.get_sheet", spreadsheet_names)
        draft_names = surface("DraftWorkbench")
        self.assertIn("draft.create_array", draft_names)
        self.assertIn("draft.create_wire", draft_names)
        self.assertNotIn("part.set_placement", draft_names)
        techdraw_names = surface("TechDrawWorkbench")
        self.assertIn("techdraw.get_pages", techdraw_names)
        self.assertIn("techdraw.create_page", techdraw_names)
        self.assertIn("techdraw.add_view", techdraw_names)
        assembly_names = surface("AssemblyWorkbench")
        self.assertIn("assembly.get_assemblies", assembly_names)
        self.assertIn("assembly.create_assembly", assembly_names)
        self.assertIn("assembly.add_component", assembly_names)
        self.assertIn("assembly.set_component_placement", assembly_names)
        self.assertIn("assembly.check_interference", assembly_names)
        # Kinematic mating: ground, joint on referenced geometry, solve — with
        # the geometric subelement resolver for deterministic references.
        self.assertIn("assembly.ground_component", assembly_names)
        self.assertIn("assembly.create_joint", assembly_names)
        self.assertIn("assembly.solve", assembly_names)
        self.assertIn("partdesign.find_subelements", assembly_names)
        # Clearance checks are part of the modeling loop: PartDesign exposes
        # interference checking without a workbench switch.
        self.assertIn("assembly.check_interference", partdesign_names)
        material_names = surface("MaterialWorkbench")
        self.assertIn("material.apply_appearance", material_names)

        # Surface pack: the full surface-first workflow (3D curves -> filled
        # or lofted surfaces -> thickened solids) without other modeling tools.
        surface_names = surface("SurfaceWorkbench")
        self.assertIn("surface.create_surface", surface_names)
        self.assertIn("draft.create_wire", surface_names)
        self.assertIn("part.thicken_surface", surface_names)
        self.assertIn("partdesign.find_subelements", surface_names)
        self.assertNotIn("partdesign.extrude", surface_names)
        self.assertNotIn("sketcher.add_geometry", surface_names)
        self.assertNotIn("part.set_placement", surface_names)

        # Non-modeling workbenches: core tools only, list objects via
        # core.list_workbench_objects, and never modeling pack tools.
        for workbench in (
            "FemWorkbench",
            "CAMWorkbench",
            "BIMWorkbench",
            "MeshWorkbench",
            "PointsWorkbench",
            "InspectionWorkbench",
            "OpenSCADWorkbench",
            "ReverseEngineeringWorkbench",
            "RobotWorkbench",
            "MeshPartWorkbench",
        ):
            with self.subTest(workbench=workbench):
                names = surface(workbench)
                self.assertIn("core.list_workbench_objects", names)
                self.assertNotIn("sketcher.add_geometry", names)
                self.assertNotIn("partdesign.extrude", names)
                self.assertNotIn("part.set_placement", names)

        test_names = surface("TestWorkbench")
        self.assertIn("core.list_workbench_objects", test_names)
        self.assertNotIn("core.run_workbench_command", test_names)
        none_names = surface("NoneWorkbench")
        self.assertIn("core.get_active_document", none_names)
        self.assertNotIn("core.run_workbench_command", none_names)

    def test_build_script_tool_hidden_unless_script_mode_enabled(self):
        service = VibeCADService()

        def surface(workbench):
            return {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, workbench)
            }

        # Default (guided mode): the script tool is invisible everywhere.
        self.assertFalse(service.build_script_mode_enabled())
        for workbench in (None, "PartDesignWorkbench", "SurfaceWorkbench"):
            with self.subTest(workbench=workbench, mode="guided"):
                self.assertNotIn("model.build_from_script", surface(workbench))

        # Script mode: the script tool is the only geometry write path.
        save_settings(VibeCADSettings(enable_build_script=True))
        try:
            self.assertTrue(service.build_script_mode_enabled())
            names = surface("PartDesignWorkbench")
            self.assertIn("model.build_from_script", names)
            # Structured write tools are hidden in script mode.
            for hidden in (
                "partdesign.extrude",
                "partdesign.create_body",
                "sketcher.add_geometry",
                "partdesign.pattern",
            ):
                self.assertNotIn(hidden, names)
            part_names = surface("PartWorkbench")
            self.assertNotIn("part.dressup", part_names)
            self.assertNotIn("draft.create_array", surface("DraftWorkbench"))
            # CAM write tools are hidden in script mode; the read-only
            # validator and the script tool remain available.
            cam_names = surface("CAMWorkbench")
            for hidden in (
                "cam.define_machine",
                "cam.create_job",
                "cam.add_tool",
                "cam.create_operation",
                "cam.postprocess",
            ):
                self.assertNotIn(hidden, cam_names, hidden)
            self.assertIn("cam.validate_job", cam_names)
            self.assertIn("model.build_from_script", cam_names)
            # Read/view/feedback tools stay available in script mode.
            for kept in (
                "core.get_active_document",
                "core.capture_view_screenshot",
                "core.set_view",
                "core.get_report_view_errors",
                "core.enter_workspace",
                "core.report_tool_shape_gap",
                "partdesign.get_bodies",
            ):
                self.assertIn(kept, names, kept)
        finally:
            save_settings(VibeCADSettings(enable_build_script=False))

    def test_script_mode_runner_blocks_structured_writes_with_specific_error(self):
        import FreeCAD as App

        service = VibeCADService()
        doc = App.newDocument("VibeCADScriptModeGateTest")
        try:
            save_settings(VibeCADSettings(enable_build_script=True))
            runner = make_provider_tool_runner(service, "PartDesignWorkbench")
            blocked = runner("partdesign.create_body", '{"label": "Blocked Body"}')
            self.assertFalse(blocked["ok"], blocked)
            self.assertIn("script mode", blocked["error"])
            self.assertIn("model.build_from_script", blocked["error"])

            built = runner(
                "model.build_from_script",
                json.dumps(
                    {
                        "script": (
                            "box = doc.addObject('Part::Box', 'ScriptBox')\n"
                            "box.Length = 10\nbox.Width = 8\nbox.Height = 4\n"
                            "doc.recompute()\n"
                        ),
                        "description": "Script-mode smoke box",
                    }
                ),
            )
            self.assertTrue(built["ok"], built)
            self.assertIsNotNone(doc.getObject("ScriptBox"))

            save_settings(VibeCADSettings(enable_build_script=False))
            script_blocked = runner(
                "model.build_from_script", '{"script": "doc.recompute()"}'
            )
            self.assertFalse(script_blocked["ok"], script_blocked)
            self.assertIn("disabled", script_blocked["error"])
            self.assertIn("structured", script_blocked["error"])
        finally:
            save_settings(VibeCADSettings(enable_build_script=False))
            App.closeDocument(doc.Name)

    def test_provider_tool_scope_is_pack_based(self):
        service = VibeCADService()
        pack = get_tool_pack("SketcherWorkbench")
        scope = provider_tool_scope_for_context(service, "SketcherWorkbench")
        self.assertEqual(scope.stage, "workbench_pack")
        self.assertEqual(
            scope.tool_names,
            set(CORE_PROVIDER_TOOLS) | set(pack.tool_names),
        )

        pd_pack = get_tool_pack("PartDesignWorkbench")
        pd_scope = provider_tool_scope_for_context(service, "PartDesignWorkbench")
        self.assertEqual(pd_scope.stage, "workbench_pack")
        self.assertEqual(
            pd_scope.tool_names,
            set(CORE_PROVIDER_TOOLS) | set(pd_pack.tool_names),
        )
        self.assertNotIn("sketcher.create_sketch", pd_scope.tool_names)
        self.assertIn("partdesign.create_sketch", pd_scope.tool_names)

        unknown_scope = provider_tool_scope_for_context(service, "NoSuchWorkbench")
        self.assertEqual(unknown_scope.stage, "core_tools")
        self.assertEqual(unknown_scope.tool_names, set(CORE_PROVIDER_TOOLS))

    def test_provider_surfaces_expose_single_workspace_switch_tool(self):
        from provider_tools import registered_tool_names

        service = VibeCADService()
        self.assertNotIn("core.activate_workbench", registered_tool_names())
        self.assertNotIn("core.activate_workbench", CORE_PROVIDER_TOOLS)
        for workbench in [
            None,
            "PartWorkbench",
            "PartDesignWorkbench",
            "SketcherWorkbench",
            "DraftWorkbench",
            "AssemblyWorkbench",
            "TechDrawWorkbench",
            "MaterialWorkbench",
        ]:
            names = {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, workbench)
            }
            self.assertNotIn("core.activate_workbench", names, workbench)
        # Internal session flows still call the tool through the registry.
        self.assertIn("core.activate_workbench", set(service.registry.names()))

    def test_autonomous_loop_enters_workspace_then_exposes_full_tool_surface(self):
        import FreeCAD as App

        class ScopeProbeProvider(BaseProvider):
            def __init__(self):
                self.scopes = []
                self.tool_names = []

            def run(self, _prompt, context, tool_runner=None):
                scope = dict(context.get("provider_tool_scope") or {})
                names = [
                    schema.get("name")
                    for schema in context.get("provider_tool_schemas", [])
                    if isinstance(schema, dict)
                ]
                self.scopes.append(scope)
                self.tool_names.append(names)
                if len(self.scopes) == 1:
                    self.assert_tool(tool_runner)
                    result = tool_runner(
                        "core.enter_workspace",
                        json.dumps(
                            {
                                "name": "PartDesignWorkbench",
                                "goal": "Create the base parametric body.",
                            }
                        ),
                    )
                    if not result.get("ok"):
                        raise AssertionError(result)
                    if result.get("checkpoint") != "workspace_entry":
                        raise AssertionError(result)
                    return ProviderResult("Entered PartDesign; refreshing tool surface.")
                self.assert_tool(tool_runner)
                tool_runner("partdesign.create_body", '{"label": "Scoped Body"}')
                return ProviderResult("Created body with entered workspace tools.")

            @staticmethod
            def assert_tool(tool_runner):
                if tool_runner is None:
                    raise AssertionError("tool_runner is required")

        doc = App.newDocument("VibeCADScopedTurnRefreshTest")
        original_project_info = VibeCADProject._active_document_info
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            service = VibeCADService()
            original_project_info = _attach_temp_project_store(
                service,
                Path(tmp_dir.name),
                "Scoped Turn Refresh",
            )
            service.active_workbench_name = lambda: "PartDesignWorkbench"  # type: ignore[method-assign]
            provider = ScopeProbeProvider()
            response = run_prompt(
                "Create a PartDesign part in small verified steps.",
                service=service,
                provider=provider,
                enforce_small_steps=True,
            )

            self.assertEqual(response.provider, "ScopeProbeProvider")
            self.assertGreaterEqual(len(provider.scopes), 2)
            self.assertEqual(provider.scopes[0]["stage"], "workspace_planner")
            self.assertEqual(provider.scopes[1]["stage"], "entered_workspace")
            self.assertIsNone(provider.scopes[0]["workbench"])
            self.assertEqual(provider.scopes[1]["workbench"], "PartDesignWorkbench")
            self.assertIn("core.enter_workspace", provider.tool_names[0])
            self.assertNotIn("partdesign.create_body", provider.tool_names[0])
            self.assertNotIn("sketcher.add_geometry", provider.tool_names[0])
            self.assertIn("partdesign.create_body", provider.tool_names[1])
            self.assertIn("sketcher.add_geometry", provider.tool_names[1])
            self.assertIn("partdesign.create_sketch", provider.tool_names[1])
            self.assertIn("partdesign.extrude", provider.tool_names[1])
            self.assertIn("partdesign.dressup", provider.tool_names[1])
            self.assertTrue(
                any(
                    getattr(obj, "TypeId", "") == "PartDesign::Body"
                    and getattr(obj, "Label", "") == "Scoped Body"
                    for obj in doc.Objects
                )
            )
        finally:
            VibeCADProject._active_document_info = original_project_info
            tmp_dir.cleanup()
            App.closeDocument(doc.Name)

    def test_partdesign_create_sketch_does_not_force_hidden_tool_surface_refresh(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADCreateSketchRefreshCheckpointTest")
        try:
            service = VibeCADService()
            with _temporary_design_project(service, "Create Sketch Refresh"):
                service.active_workbench_name = lambda: "PartDesignWorkbench"  # type: ignore[method-assign]
                body = service.registry.call("partdesign.create_body", label="Checkpoint Body")
                self.assertTrue(body["ok"], body)
                trace = []
                runner = make_provider_tool_runner(
                    service,
                    "PartDesignWorkbench",
                    tool_trace=trace,
                    turn_state={"turn": 1, "mutating_tool_calls": 0},
                )

                result = runner(
                    "partdesign.create_sketch",
                    '{"body_name": "Checkpoint Body", "label": "Component Sketch", "plane": "XY_Plane"}',
                )

                self.assertTrue(result["ok"], result)
                self.assertIsNone(result.get("checkpoint"))
                self.assertNotIn("required_next_action", result)

                drawn = runner(
                    "sketcher.draw_rectangle",
                    '{"sketch_name": "Component Sketch", "width": 10, "height": 10}',
                )
                self.assertTrue(drawn["ok"], drawn)
                self.assertNotEqual(drawn.get("status"), "deferred_checkpoint")
                self.assertIsNone(drawn.get("checkpoint"))
        finally:
            App.closeDocument(doc.Name)

    def test_provider_workbench_does_not_remap_while_editing_body_sketch(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignEffectiveWorkbenchTest")
        try:
            service = VibeCADService()
            service.registry.call("partdesign.create_body", label="Body")
            service.registry.call("partdesign.create_sketch", label="Sketch")

            effective = _effective_provider_workbench(service, "SketcherWorkbench")
            self.assertEqual(effective, "SketcherWorkbench")

            names = {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, effective)
            }
            self.assertIn("sketcher.draw_rectangle", names)
            self.assertNotIn("partdesign.create_sketch", names)
            self.assertNotIn("partdesign.extrude", names)
            self.assertIn("core.enter_workspace", names)
        finally:
            App.closeDocument(doc.Name)

    def test_provider_safe_sketcher_context_requires_explicit_partdesign_entry(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketcherPartDesignBridgeTest")
        try:
            service = VibeCADService()
            before_names = {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, "SketcherWorkbench")
            }
            self.assertNotIn("partdesign.extrude", before_names)

            sketch_result = service.registry.call(
                "sketcher.create_sketch",
                label="Bridge Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            after_names = {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, "SketcherWorkbench")
            }
            self.assertNotIn("partdesign.extrude", after_names)
            self.assertNotIn("partdesign.revolve", after_names)
            self.assertIn("core.enter_workspace", after_names)
        finally:
            App.closeDocument(doc.Name)

    def test_part_pack_tools_are_native_in_part_workbench(self):
        service = VibeCADService()
        with _temporary_design_project(service, "Part Pack Provider"):
            names = {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, "PartWorkbench")
            }
            self.assertIn("part.set_placement", names)
            self.assertIn("part.cut_cylindrical_hole", names)
            self.assertIn("part.dressup", names)

            partdesign_names = {
                schema["name"]
                for schema in provider_safe_tool_schemas(
                    service,
                    "PartDesignWorkbench",
                    apply_workbench_allowlist=False,
                )
            }
            self.assertNotIn("part.dressup", partdesign_names)

    def test_provider_tool_runner_blocks_direct_write_tools(self):
        service = VibeCADService()
        runner = make_provider_tool_runner(service)
        blocked = runner("core.undo_last_vibecad_action", "{}")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["safety"], "write")

    def test_provider_tool_runner_blocks_out_of_scope_workbench_tools(self):
        service = VibeCADService()
        with _temporary_design_project(service, "Out Of Scope Workbench"):
            runner = make_provider_tool_runner(service, "SketcherWorkbench")
            blocked = runner("part.set_placement", '{"object_name": "Missing", "position": [0, 0, 0]}')
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["tool_workbench"], "PartWorkbench")
        self.assertIn("Tool is not available", blocked["error"])

    def test_provider_tool_runner_rejects_part_tools_in_partdesign(self):
        service = VibeCADService()
        with _temporary_design_project(service, "Reject Part Tool"):
            runner = make_provider_tool_runner(service, "PartDesignWorkbench")
            blocked = runner(
                "part.dressup",
                '{"object_name": "Missing", "operation": "fillet", "radius": 1}',
            )
        self.assertFalse(blocked["ok"])
        self.assertIn("Tool is not available for the active workbench", blocked["error"])
        self.assertEqual(blocked["active_workbench"], "PartDesignWorkbench")
        self.assertEqual(blocked["tool_workbench"], "PartWorkbench")

    def test_provider_tool_runner_requires_explicit_workbench_switch(self):
        if not _gui_workbench_api_available():
            self.skipTest("FreeCAD GUI workbench API unavailable")
        import FreeCAD as App

        service = VibeCADService()
        with _temporary_design_project(service, "Explicit Workbench Switch"):
            runner = make_provider_tool_runner(service, "PartWorkbench")
            blocked = runner(
                "partdesign.create_sketch",
                '{"label": "Auto Switch Sketch", "plane": "XY_Plane"}',
            )
        doc = App.ActiveDocument
        try:
            self.assertTrue(blocked["ok"], blocked)
            result = blocked["result"]
            self.assertEqual(result["transaction"]["result"]["sketch_label"], "Auto Switch Sketch")
            self.assertIsNotNone(result["active_sketch"])
        finally:
            if doc is not None:
                App.closeDocument(doc.Name)

    def test_provider_tool_runner_does_not_checkpoint_same_workbench_activation(self):
        if not _gui_workbench_api_available():
            self.skipTest("FreeCAD GUI workbench API unavailable")
        service = VibeCADService()
        service.activate_workbench("PartDesignWorkbench")
        turn_state = {"turn": 1, "mutating_tool_calls": 0, "checkpoint_reached": False}
        runner = make_provider_tool_runner(
            service,
            "PartDesignWorkbench",
            turn_state=turn_state,
        )
        result = runner("core.activate_workbench", '{"name": "PartDesignWorkbench"}')

        self.assertTrue(result["ok"], result)
        self.assertNotEqual(result.get("checkpoint"), "workbench_switch")
        self.assertFalse(turn_state.get("workbench_switch_reached", False))

    def test_autonomous_loop_continues_after_workbench_switch_checkpoint(self):
        trace = [
            {
                "tool_name": "core.activate_workbench",
                "ok": True,
                "result": {"ok": True, "checkpoint": "workbench_switch"},
            }
        ]
        self.assertTrue(_tool_batch_checkpoint_reached(trace))
        service = VibeCADService()
        self.assertTrue(
            _should_continue_autonomously(
                "Design a usable quadcopter drone concept.",
                "Checkpoint after required workbench switch.",
                service,
                trace,
                turn_index=1,
            )
        )

    def test_provider_tool_runner_uses_actual_active_workbench_before_each_call(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADLiveWorkbenchTrackingTest")
        try:
            service = VibeCADService()
            with _temporary_design_project(service, "Live Workbench Tracking"):
                service.active_workbench_name = lambda: "SketcherWorkbench"  # type: ignore[method-assign]
                runner = make_provider_tool_runner(service, "PartDesignWorkbench")
                result = runner(
                    "sketcher.create_sketch",
                    '{"label": "Actual Workbench Sketch", "support_type": "origin_plane", "plane": "XY_Plane"}',
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(
                result["result"]["transaction"]["result"]["active_workbench"],
                "SketcherWorkbench",
            )
            self.assertTrue(
                any(
                    getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
                    and getattr(obj, "Label", "") == "Actual Workbench Sketch"
                    for obj in doc.Objects
                )
            )
        finally:
            App.closeDocument(doc.Name)

    def test_provider_tool_runner_reports_small_step_checkpoint_after_completed_mutation(self):
        import FreeCAD as App

        service = VibeCADService()
        with _temporary_design_project(service, "Small Step Checkpoint"):
            turn_state = {
                "turn": 1,
                "operation_score": MAX_OPERATION_SCORE_PER_PROVIDER_TURN
                - _operation_score_for_tool(
                    "partdesign.create_sketch", SafetyLevel.SAFE_WRITE
                ),
                "mutating_tool_calls": 4,
                "checkpoint_reached": False,
            }
            runner = make_provider_tool_runner(
                service,
                "PartDesignWorkbench",
                turn_state=turn_state,
            )
            result = runner(
                "partdesign.create_sketch",
                '{"label": "Checkpoint Sketch", "plane": "XY_Plane"}',
            )
        doc = App.ActiveDocument
        try:
            self.assertTrue(result["ok"], result)
            self.assertEqual(result.get("checkpoint"), "small_step")
            self.assertTrue(turn_state.get("checkpoint_reached"))
            self.assertEqual(
                result.get("operation_score"),
                MAX_OPERATION_SCORE_PER_PROVIDER_TURN,
            )
            self.assertEqual(
                result.get("score_limit"),
                MAX_OPERATION_SCORE_PER_PROVIDER_TURN,
            )
            self.assertEqual(
                result.get("required_next_action", {}).get("finish_current_turn"),
                True,
            )
            self.assertTrue(
                any(
                    getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
                    and getattr(obj, "Label", "") == "Checkpoint Sketch"
                    for obj in (doc.Objects if doc else [])
                )
            )
        finally:
            if doc is not None:
                App.closeDocument(doc.Name)

    def test_provider_tool_runner_defers_over_budget_operation_as_recoverable_failure(self):
        import FreeCAD as App

        service = VibeCADService()
        with _temporary_design_project(service, "Deferred Small Step Checkpoint"):
            turn_state = {
                "turn": 1,
                "operation_score": MAX_OPERATION_SCORE_PER_PROVIDER_TURN,
                "mutating_tool_calls": 6,
                "checkpoint_reached": False,
            }
            runner = make_provider_tool_runner(
                service,
                "PartDesignWorkbench",
                turn_state=turn_state,
            )
            result = runner(
                "partdesign.create_sketch",
                '{"label": "Deferred Sketch", "plane": "XY_Plane"}',
            )
        doc = App.ActiveDocument
        try:
            self.assertFalse(result["ok"], result)
            self.assertEqual(result.get("status"), "deferred_checkpoint")
            self.assertFalse(result.get("executed"))
            self.assertFalse(result.get("mutated_document"))
            self.assertTrue(result.get("recoverable"))
            self.assertEqual(result.get("checkpoint"), "small_step")
            self.assertEqual(result.get("blocked_tool"), "partdesign.create_sketch")
            self.assertEqual(
                result.get("next_operation_score"),
                _operation_score_for_tool(
                    "partdesign.create_sketch", SafetyLevel.SAFE_WRITE
                ),
            )
            self.assertFalse(
                any(
                    getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
                    and getattr(obj, "Label", "") == "Deferred Sketch"
                    for obj in (doc.Objects if doc else [])
                )
            )
        finally:
            if doc is not None:
                App.closeDocument(doc.Name)

    def test_operation_score_budget_is_configurable(self):
        old_score_value = os.environ.get(MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV)
        old_mutation_value = os.environ.get(MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV)
        try:
            os.environ[MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV] = "11"
            os.environ.pop(MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV, None)
            self.assertEqual(_max_operation_score_per_provider_turn(), 11)
            self.assertEqual(_max_mutating_tool_calls_per_provider_turn(), 11)
            state = _provider_loop_state(
                "make a bracket",
                {"document": {"object_count": 1}, "workbench": "PartDesignWorkbench"},
                [],
                turn=1,
                visual_feedback_consumed=False,
            )
            self.assertEqual(state["max_operation_score_per_turn"], 11)
            self.assertEqual(state["max_mutating_tool_calls_per_turn"], 11)
        finally:
            if old_score_value is None:
                os.environ.pop(MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV, None)
            else:
                os.environ[MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV] = old_score_value
            if old_mutation_value is None:
                os.environ.pop(MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV, None)
            else:
                os.environ[MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV] = (
                    old_mutation_value
                )

    def test_legacy_mutating_tool_limit_env_still_configures_operation_budget(self):
        old_score_value = os.environ.get(MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV)
        old_mutation_value = os.environ.get(MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV)
        try:
            os.environ.pop(MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV, None)
            os.environ[MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV] = "7"
            self.assertEqual(_max_operation_score_per_provider_turn(), 7)
            self.assertEqual(_max_mutating_tool_calls_per_provider_turn(), 7)
        finally:
            if old_score_value is None:
                os.environ.pop(MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV, None)
            else:
                os.environ[MAX_OPERATION_SCORE_PER_PROVIDER_TURN_ENV] = old_score_value
            if old_mutation_value is None:
                os.environ.pop(MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV, None)
            else:
                os.environ[MAX_MUTATING_TOOL_CALLS_PER_PROVIDER_TURN_ENV] = (
                    old_mutation_value
                )

    def test_operation_scores_weight_sketch_constraints_lower_than_solid_features(self):
        self.assertEqual(
            _operation_score_for_tool("sketcher.add_constraint", SafetyLevel.SAFE_WRITE),
            1,
        )
        self.assertGreater(
            _operation_score_for_tool(
                "partdesign.loft_profiles", SafetyLevel.SAFE_WRITE
            ),
            _operation_score_for_tool("sketcher.add_constraint", SafetyLevel.SAFE_WRITE),
        )

    def test_autonomous_loop_continues_when_tool_trace_reports_checkpoint(self):
        service = VibeCADService()
        trace = [
            {
                "tool_name": "partdesign.pad_profile",
                "ok": False,
                "result": {"ok": False, "checkpoint": "small_step"},
            }
        ]
        self.assertTrue(
            _should_continue_autonomously(
                "Design a usable bearing carrier bracket and capture the viewport.",
                "Progress checkpoint: VibeCAD requested a checkpoint before further edits.",
                service,
                trace,
                0,
                visual_feedback_consumed=True,
            )
        )

    def test_autonomous_loop_ignores_output_phrases_without_structured_signals(self):
        service = VibeCADService()
        self.assertFalse(
            _should_continue_autonomously(
                "Design a usable bearing carrier bracket.",
                "Next steps: I'm ready to continue once the tools allow. "
                "Progress checkpoint requested. Please confirm?",
                service,
                [],
                0,
                visual_feedback_consumed=True,
            )
        )

    def test_autonomous_loop_continues_when_verified_requirements_remain(self):
        class FakeService:
            def document_summary(self):
                return {"object_count": 4}

            def context_summary(self):
                return {
                    "document": {
                        "object_count": 4,
                        "objects": [
                            {"type": "PartDesign::Body"},
                            {"type": "PartDesign::Body"},
                            {"type": "PartDesign::Pad"},
                            {"type": "PartDesign::Pad"},
                        ],
                    },
                    "assembly": {"assembly_count": 0, "assemblies": []},
                }

            def provider_context_summary(self):
                return self.context_summary()

        self.assertTrue(
            _should_continue_autonomously(
                "Design a usable multi-part fixture assembly with native assembly structure.",
                "Created a base plate and fixed jaw.",
                FakeService(),
                [],
                0,
            )
        )

    def test_provider_tool_runner_blocks_document_creation_but_service_tool_accepts_name(self):
        import FreeCAD as App

        service = VibeCADService()
        runner = make_provider_tool_runner(service)
        result = runner("core.create_new_document", '{"name": "VibeCADNamedDocument"}')
        self.assertFalse(result["ok"])
        self.assertIn("not available to the autonomous CAD loop", result["error"])
        result = service.registry.call("core.create_new_document", name="VibeCADNamedDocument")
        try:
            self.assertTrue(result["ok"], result)
            self.assertIsNotNone(App.getDocument("VibeCADNamedDocument"))
        finally:
            doc = App.getDocument("VibeCADNamedDocument")
            if doc is not None:
                App.closeDocument(doc.Name)

    def test_disabled_tool_pack_blocks_provider_surface_and_runner(self):
        old_settings = load_settings()
        try:
            save_settings(
                VibeCADSettings(disabled_workbenches=("PartWorkbench",))
            )
            service = VibeCADService()
            schemas = provider_safe_tool_schemas(service, "PartWorkbench")
            names = {schema["name"] for schema in schemas}
            self.assertIn("core.get_active_document", names)
            self.assertNotIn("core.propose_create_part_box", names)
            self.assertNotIn("part.dressup", names)
            self.assertNotIn("core.propose_create_workbench_object", names)

            surface = service.provider_tool_surface("PartWorkbench")
            self.assertFalse(surface["tool_pack_enabled"])
            surface_names = {tool["name"] for tool in surface["tools"]}
            self.assertNotIn("part.dressup", surface_names)
            self.assertFalse(service.is_provider_tool_available("part.dressup", "PartWorkbench"))

            runner = make_provider_tool_runner(service, "PartWorkbench")
            blocked = runner("part.dressup", "{}")
            self.assertFalse(blocked["ok"])
            self.assertIn("Tool pack is disabled", blocked["error"])
        finally:
            save_settings(old_settings)

    def test_provider_tool_surface_reports_scoped_tools(self):
        service = VibeCADService()
        surface = service.provider_tool_surface("PartWorkbench")
        names = {tool["name"] for tool in surface["tools"]}
        self.assertEqual(surface["active_workbench"], "PartWorkbench")
        self.assertTrue(surface["tool_pack_enabled"])
        self.assertNotIn("core.run_workbench_command", names)
        self.assertNotIn("core.propose_run_workbench_command", names)
        self.assertNotIn("core.propose_create_part_box", names)
        self.assertIn("core.list_workbench_objects", names)
        self.assertIn("part.set_placement", names)
        self.assertIn("part.cut_cylindrical_hole", names)
        self.assertIn("part.dressup", names)

    def test_tool_shape_report_explains_available_and_missing_provider_capabilities(self):
        service = VibeCADService()
        report = service.tool_shape_report("PartDesignWorkbench")
        names = set(report["provider_tool_names"])
        self.assertEqual(report["active_workbench"], "PartDesignWorkbench")
        self.assertIn("core.get_tool_shape_report", names)
        self.assertIn("partdesign.create_sketch", names)
        self.assertIn("partdesign.extrude", names)
        self.assertIn("partdesign.hole_from_sketch", names)
        self.assertIn("partdesign.revolve", names)
        self.assertIn("partdesign.loft_profiles", names)
        self.assertIn("partdesign.sweep_profile", names)
        self.assertIn("partdesign.pattern", names)
        self.assertIn("partdesign.dressup", names)
        self.assertIn("partdesign.set_feature_dimensions", names)
        self.assertIn("sketcher.add_geometry", names)
        self.assertIn("sketcher.add_hole_pattern", names)
        self.assertIn("sketcher.add_slot", names)
        slot_schema = next(schema for schema in report["provider_tools"] if schema["name"] == "sketcher.add_slot")
        slot_properties = slot_schema["parameters"]["properties"]
        self.assertIn("overall_length", slot_properties)
        self.assertIn("center_distance", slot_properties)
        self.assertIn("length_mode", slot_properties)
        self.assertIn("overall end-to-end", slot_properties["length"]["description"])
        self.assertNotIn("length", slot_schema["parameters"]["required"])
        self.assertIn("sketcher.add_constraint", names)
        self.assertIn("core.delete_object", names)
        self.assertNotIn("draft.create_array", names)
        self.assertNotIn("assembly.create_assembly", names)
        self.assertNotIn("techdraw.create_page", names)
        self.assertTrue(report["capabilities"]["atomic_sketch_geometry"]["available"])
        self.assertTrue(report["capabilities"]["atomic_sketch_constraints"]["available"])
        self.assertTrue(report["capabilities"]["iterative_delete"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_pad_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_pocket_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_hole_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_revolution_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_groove_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_loft_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_sweep_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_helix_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_pattern_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_mirror_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_datum_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_draft_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_boolean_features"]["available"])
        self.assertTrue(report["capabilities"]["partdesign_edge_finishing"]["available"])
        self.assertTrue(report["capabilities"]["sketch_dimension_edits"]["available"])
        self.assertTrue(report["capabilities"]["sketcher_profile_validation"]["available"])
        self.assertTrue(report["capabilities"]["sketcher_solver_diagnosis"]["available"])
        self.assertTrue(report["capabilities"]["sketcher_external_geometry"]["available"])
        self.assertTrue(report["capabilities"]["sketcher_curve_editing"]["available"])
        self.assertTrue(report["capabilities"]["sketcher_detailed_constraints"]["available"])
        self.assertNotIn("part_primitives", report["capabilities"])
        self.assertTrue(report["capabilities"]["shells_and_wall_thickness"]["available"])
        self.assertFalse(report["capabilities"]["edge_chamfering"]["available"])
        self.assertFalse(report["capabilities"]["detail_drawings"]["available"])
        self.assertFalse(report["capabilities"]["assembly_component_add"]["available"])
        self.assertFalse(report["capabilities"]["assembly_grounding"]["available"])
        self.assertFalse(report["capabilities"]["kinematic_joints"]["available"])
        self.assertFalse(report["capabilities"]["kinematic_solve"]["available"])

        assembly_report = service.tool_shape_report("AssemblyWorkbench")
        assembly_capabilities = assembly_report["capabilities"]
        self.assertTrue(assembly_capabilities["assembly_grounding"]["available"])
        self.assertTrue(assembly_capabilities["kinematic_joints"]["available"])
        self.assertTrue(assembly_capabilities["kinematic_solve"]["available"])
        assembly_names = set(assembly_report["provider_tool_names"])
        self.assertIn("assembly.ground_component", assembly_names)
        self.assertIn("assembly.create_joint", assembly_names)
        self.assertIn("assembly.solve", assembly_names)
        self.assertIn("partdesign.find_subelements", assembly_names)
        coverage = {
            item["tool_class"]: item
            for item in report["sketcher_human_command_coverage"]
        }
        self.assertEqual(
            coverage["Sketcher create primitive/profile geometry"]["coverage"],
            "covered",
        )
        self.assertIn(
            "sketcher.add_geometry",
            coverage["Sketcher create primitive/profile geometry"]["available_provider_tools"],
        )
        self.assertEqual(
            coverage["Sketcher curve repair and local editing"]["coverage"],
            "covered",
        )
        self.assertIn(
            "sketcher.modify_geometry",
            coverage["Sketcher curve repair and local editing"]["available_provider_tools"],
        )
        self.assertEqual(
            coverage["Sketcher external/reference geometry"]["coverage"],
            "partial",
        )
        self.assertIn(
            "sketcher.carbon_copy",
            coverage["Sketcher external/reference geometry"]["missing_desired_tools"],
        )
        self.assertEqual(
            coverage["Sketcher bulk transform and duplicate operations"]["coverage"],
            "partial",
        )
        self.assertIn(
            "sketcher.transform_geometry",
            coverage["Sketcher bulk transform and duplicate operations"]["available_provider_tools"],
        )
        self.assertEqual(
            coverage["Sketcher offset and derived-profile operations"]["coverage"],
            "covered",
        )
        self.assertIn(
            "sketcher.transform_geometry",
            coverage["Sketcher offset and derived-profile operations"]["available_provider_tools"],
        )
        self.assertIn(
            "sketcher.delete_items",
            coverage["Sketcher bulk deletion and cleanup"]["available_provider_tools"],
        )
        self.assertEqual(
            coverage["Sketcher bulk deletion and cleanup"]["missing_desired_tools"],
            ["sketcher.remove_axes_alignment"],
        )
        self.assertNotIn(
            "Sketcher trim/extend, external geometry references, and named datum lookup",
            report["still_missing_tool_classes"],
        )
        self.assertIn("still_missing_tool_classes", report)
        self.assertIn("why_results_can_be_primitive", report)
        self.assertGreaterEqual(report["human_workbench_command_count"], 0)

    def test_provider_can_report_tool_shape_gaps_during_run(self):
        service = VibeCADService()
        runner = make_provider_tool_runner(service, "PartDesignWorkbench")
        result = runner(
            "core.report_tool_shape_gap",
            json.dumps(
                {
                    "missing_capability": "parametric NEMA17 mounting-hole sketch helper",
                    "why_needed": "Robot motor mounts need constrained hole layout workflows.",
                    "desired_native_tool": "partdesign.create_mounting_hole_sketch",
                    "current_workaround": "manual sketch constraints",
                    "active_workbench": "PartDesignWorkbench",
                }
            ),
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("feedback_id", result["result"])
        self.assertIn("recent_feedback", result["result"])
        report = service.tool_shape_report("PartDesignWorkbench")
        feedback = report["recent_tool_shape_feedback"]
        self.assertTrue(feedback)
        self.assertEqual(
            feedback[-1]["desired_native_tool"],
            "partdesign.create_mounting_hole_sketch",
        )

    def test_provider_can_report_tool_shape_gap_with_model_preferred_fields(self):
        service = VibeCADService()
        runner = make_provider_tool_runner(service, "SketcherWorkbench")
        result = runner(
            "core.report_tool_shape_gap",
            json.dumps(
                {
                    "tool_or_class": "sketcher.offset_geometry",
                    "severity": "high",
                    "why_blocks_quality": "Offset is needed for wall thickness and clearance profiles.",
                    "needed_schema": "geometry_handles, offset_distance, side, join_style",
                    "needed_result_data": "created_geometry_indices, old_to_new_geometry_index, solver_status",
                    "active_workbench": "SketcherWorkbench",
                }
            ),
        )
        self.assertTrue(result["ok"], result)
        recorded = result["result"]["recorded"]
        self.assertEqual(recorded["missing_capability"], "sketcher.offset_geometry")
        self.assertEqual(recorded["desired_native_tool"], "sketcher.offset_geometry")
        self.assertEqual(recorded["severity"], "high")
        self.assertIn("created_geometry_indices", recorded["needed_result_data"])

    def test_provider_tool_runner_can_create_detailed_part_features(self):
        if not _gui_workbench_api_available():
            self.skipTest("FreeCAD GUI workbench API unavailable")
        import FreeCAD as App

        doc = App.newDocument("VibeCADDetailedPartTools")
        original_project_info = VibeCADProject._active_document_info
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            service = VibeCADService()
            original_project_info = _attach_temp_project_store(
                service,
                Path(tmp_dir.name),
                "Detailed Part Tools",
            )
            runner = make_provider_tool_runner(service, "PartWorkbench")
            plate = doc.addObject("Part::Box", "MotorPlate")
            plate.Label = "Motor plate"
            plate.Length = 70
            plate.Width = 45
            plate.Height = 5
            doc.recompute()
            moved = runner(
                "part.set_placement",
                '{"object_name": "Motor plate", "x": 10, "y": 5, "z": 2, "yaw_degrees": 15}',
            )
            self.assertTrue(moved["ok"], moved)
            cut = runner(
                "part.cut_cylindrical_hole",
                '{"target_name": "Motor plate", "label": "Motor plate center bore", "radius": 4, "depth": 12, "x": 30, "y": 20, "z": -3, "axis": "Z"}',
            )
            self.assertTrue(cut["ok"], cut)
            switched_to_draft = runner("core.activate_workbench", '{"name": "DraftWorkbench"}')
            self.assertTrue(switched_to_draft["ok"], switched_to_draft)
            array = runner(
                "draft.create_array",
                '{"object_name": "Motor plate center bore", "label": "Motor plate bore pattern", "array_type": "polar", "polar_count": 4, "polar_angle": 360, "center_x": 30, "center_y": 20, "center_z": 0}',
            )
            self.assertTrue(array["ok"], array)
            switched_to_part = runner("core.activate_workbench", '{"name": "PartWorkbench"}')
            self.assertTrue(switched_to_part["ok"], switched_to_part)
            fillet = runner('part.dressup', '{"operation": "fillet", "object_name": "Motor plate center bore", "label": "Rounded motor plate", "radius": 0.5, "edge_indices": [1, 2, 3, 4]}')
            self.assertTrue(fillet["ok"], fillet)
            chamfer = runner('part.dressup', '{"operation": "chamfer", "object_name": "Motor plate", "label": "Chamfered motor plate", "distance": 0.5, "edge_indices": [1, 2, 3, 4]}')
            self.assertTrue(chamfer["ok"], chamfer)
            thickness = runner('part.dressup', '{"operation": "thickness", "object_name": "Motor plate", "label": "Hollow motor plate", "wall_thickness": 1.0, "face_names": ["Face6"], "inward": true}')
            self.assertTrue(thickness["ok"], thickness)

            labels = {getattr(obj, "Label", obj.Name) for obj in doc.Objects}
            self.assertIn("Motor plate", labels)
            self.assertIn("Motor plate center bore", labels)
            self.assertIn("Motor plate bore pattern", labels)
            self.assertIn("Rounded motor plate", labels)
            self.assertIn("Chamfered motor plate", labels)
            self.assertIn("Hollow motor plate", labels)
            self.assertAlmostEqual(float(plate.Length), 70.0)
            self.assertAlmostEqual(float(plate.Width), 45.0)
            pattern = next(
                obj
                for obj in doc.Objects
                if getattr(obj, "Label", "") == "Motor plate bore pattern"
            )
            self.assertIn("Array", getattr(pattern, "Proxy", pattern).__class__.__name__)
            rounded = next(
                obj
                for obj in doc.Objects
                if getattr(obj, "Label", "") == "Rounded motor plate"
            )
            self.assertGreater(len(getattr(rounded.Shape, "Faces", [])), 0)
            self.assertGreater(float(getattr(rounded.Shape, "Volume", 0.0)), 0.0)
            chamfered = next(
                obj
                for obj in doc.Objects
                if getattr(obj, "Label", "") == "Chamfered motor plate"
            )
            self.assertGreater(len(getattr(chamfered.Shape, "Faces", [])), 0)
            self.assertGreater(float(getattr(chamfered.Shape, "Volume", 0.0)), 0.0)
            hollow = next(
                obj
                for obj in doc.Objects
                if getattr(obj, "Label", "") == "Hollow motor plate"
            )
            self.assertEqual(hollow.TypeId, "Part::Thickness")
            self.assertEqual(hollow.Faces[0], plate)
            self.assertIn("Face6", hollow.Faces[1])
            self.assertLess(float(hollow.Value), 0.0)
            self.assertGreater(len(getattr(hollow.Shape, "Faces", [])), 0)
            self.assertGreater(float(getattr(hollow.Shape, "Volume", 0.0)), 0.0)
        finally:
            VibeCADProject._active_document_info = original_project_info
            tmp_dir.cleanup()
            App.closeDocument(doc.Name)

    def test_test_workbench_tool_pack_scopes_commands(self):
        service = VibeCADService()
        summary = service.workbench_command_summary("TestWorkbench")
        self.assertEqual(summary["active_workbench"], "TestWorkbench")
        self.assertEqual(summary["command_prefixes"], ["Test_", "Std_Test"])
        self.assertIn("commands", summary)
        templates = service.workbench_object_templates("TestWorkbench")
        self.assertIn({"name": "test_group", "object_type": "App::DocumentObjectGroup"}, templates["templates"])

    def test_none_workbench_tool_pack_exposes_core_context(self):
        service = VibeCADService()
        summary = service.workbench_tool_pack_summary("NoneWorkbench")
        self.assertEqual(summary["tool_pack"]["workbench"], "NoneWorkbench")
        tools = service.provider_tool_surface("NoneWorkbench")
        names = {tool["name"] for tool in tools["tools"]}
        self.assertIn("core.get_active_document", names)
        self.assertNotIn("core.run_workbench_command", names)
        self.assertNotIn("core.propose_create_workbench_object", names)
