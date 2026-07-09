# SPDX-License-Identifier: LGPL-2.1-or-later

import contextlib
import json
from pathlib import Path
import sys
import tempfile
import types
from typing import Any
from unittest import mock

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
    WORKBENCH_READ_TOOLS,
    _effective_provider_workbench,
    _provider_loop_state,
    _result_summary,
    _should_continue_autonomously,
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
from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS, get_tool_pack

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
    def test_provider_schema_allowlists_reference_backend_parameters(self):
        from provider_tools.base import _PROVIDER_SCHEMA_FIELDS

        service = VibeCADService()
        stale_fields = {}
        for tool_name, keep_fields in sorted(_PROVIDER_SCHEMA_FIELDS.items()):
            schema = service.registry.get(tool_name).to_schema()
            parameters = schema.get("parameters") or {}
            properties = parameters.get("properties") or {}
            missing = sorted(set(keep_fields) - set(properties))
            if missing:
                stale_fields[tool_name] = missing
        self.assertEqual({}, stale_fields)

    def test_provider_json_schemas_preserve_backend_tool_parameters(self):
        from provider_tools import registered_tool_names
        from provider_tools.base import tool_json_schema

        service = VibeCADService()
        hidden_fields = {}
        for tool_name in sorted(registered_tool_names()):
            schema = service.registry.get(tool_name).to_schema()
            parameters = schema.get("parameters") or {}
            backend_properties = set((parameters.get("properties") or {}).keys())
            exposed_properties = set(tool_json_schema(schema).get("properties") or {})
            hidden = sorted(backend_properties - exposed_properties)
            if hidden:
                hidden_fields[tool_name] = hidden
        self.assertEqual({}, hidden_fields)

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
        result = runner("cad.inspect_state", "{}")

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
        self.assertEqual(trace[-1]["tool_name"], "cad.inspect_state")

    def test_tool_runner_cancellation_does_not_consume_queued_steering(self):
        queued = ["change direction after the stop clears"]
        runner = make_provider_tool_runner(
            VibeCADService(),
            cancellation_check=lambda: True,
            steering_check=lambda: [queued.pop(0)] if queued else [],
        )

        result = runner("cad.inspect_state", "{}")

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

    def test_no_tool_unresolved_loop_stop_is_not_user_visible_chat(self):
        import FreeCAD as App
        import Part

        class NoToolProvider(BaseProvider):
            def run(self, prompt, context, tool_runner=None, cancellation_check=None):
                return ProviderResult("I inspected the open sketch and need to continue.")

        doc = App.newDocument("VibeCADNoToolStopChatTest")
        events: list[dict[str, Any]] = []
        try:
            sketch = doc.addObject("Sketcher::SketchObject", "OpenSketchForNoToolStop")
            sketch.addGeometry(
                Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
                False,
            )
            doc.recompute()

            service = VibeCADService()
            service.active_workbench_name = lambda: "SketcherWorkbench"  # type: ignore[method-assign]
            response = run_prompt(
                "Finish this sketch into a closed profile.",
                service=service,
                provider=NoToolProvider(),
                progress_callback=events.append,
            )

            self.assertEqual(
                response.final_output,
                "I inspected the open sketch and need to continue.",
            )
            self.assertNotIn("Stopping autonomous loop", response.final_output)
            self.assertNotIn("verified requirements remain unresolved", response.final_output)
            stop_events = [
                event
                for event in events
                if event.get("event") == "provider_loop_stopped"
            ]
            self.assertEqual(len(stop_events), 1, events)
            self.assertEqual(
                stop_events[0]["reason"],
                "no_tools_with_unresolved_requirements",
            )
        finally:
            App.closeDocument(doc.Name)

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

    def test_provider_safe_tool_schemas_expose_only_command_write_tools(self):
        service = VibeCADService()
        names = {schema["name"] for schema in provider_safe_tool_schemas(service)}
        for semantic in (
            "cad.inspect_state",
            "cad.define_component",
            "cad.define_interface",
            "cad.define_envelope",
            "cad.define_mechanism",
            "cad.create_profile",
            "cad.create_feature",
            "cad.verify_design",
        ):
            self.assertIn(semantic, names)
        self.assertNotIn("core.get_active_document", names)
        self.assertNotIn("core.create_new_document", names)
        self.assertNotIn("core.open_document", names)
        self.assertNotIn("core.delete_object", names)
        self.assertNotIn("core.enter_workspace", names)
        self.assertNotIn("core.get_object_properties", names)
        self.assertNotIn("core.report_tool_shape_gap", names)
        self.assertNotIn("core.run_workbench_command", names)
        self.assertNotIn("core.get_tool_shape_report", names)
        self.assertNotIn("core.wait_for_user_gui_action", names)
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
        profile_schema = next(
            schema for schema in provider_safe_tool_schemas(service)
            if schema["name"] == "cad.create_profile"
        )
        entity_kind_enum = (
            profile_schema["parameters"]["properties"]["entities"]["items"]
            ["properties"]["kind"]["enum"]
        )
        for entity_kind in ("rectangle", "slot", "hole_pattern"):
            self.assertIn(entity_kind, entity_kind_enum)

    def test_cad_create_profile_verifies_actual_curve_geometry(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSemanticProfileCurveCheck")
        original_project_info = VibeCADProject._active_document_info
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            service = VibeCADService()
            original_project_info = _attach_temp_project_store(
                service,
                Path(tmp_dir.name),
                "Semantic Profile Curve Check",
            )

            line_only = service.registry.call(
                "cad.create_profile",
                component_name="Blade",
                profile_name="StraightStandInProfile",
                purpose="A blade belly that must be authored as a real curve.",
                requires_curves=True,
                entities=[
                    {
                        "name": "top",
                        "kind": "line",
                        "points": [[0, 12], [40, 12]],
                    },
                    {
                        "name": "nose",
                        "kind": "line",
                        "points": [[40, 12], [40, 0]],
                    },
                    {
                        "name": "bottom",
                        "kind": "line",
                        "points": [[40, 0], [0, 0]],
                    },
                    {
                        "name": "heel",
                        "kind": "line",
                        "points": [[0, 0], [0, 12]],
                    },
                ],
            )
            self.assertFalse(line_only["ok"], line_only)
            self.assertEqual(line_only["requested_curve_entity_count"], 0)
            self.assertEqual(line_only["actual_curve_geometry_count"], 0)
            self.assertIn("non-construction curve geometry", line_only["error"])
            self.assertIn("requires_curves=true", " ".join(line_only["warnings"]))

            curved = service.registry.call(
                "cad.create_profile",
                component_name="Blade",
                profile_name="CurvedBellyProfile",
                purpose="A blade belly authored with a native Sketcher arc.",
                requires_curves=True,
                entities=[
                    {
                        "name": "belly_arc",
                        "kind": "arc",
                        "center": [20, -30],
                        "radius": 36,
                        "start_angle_degrees": 55,
                        "end_angle_degrees": 125,
                    }
                ],
            )
            self.assertTrue(curved["ok"], curved)
            self.assertEqual(curved["requested_curve_entity_count"], 1)
            self.assertGreaterEqual(curved["actual_curve_geometry_count"], 1)
            self.assertIn("ArcOfCircle", curved["actual_curve_geometry_types"])
        finally:
            VibeCADProject._active_document_info = original_project_info
            tmp_dir.cleanup()
            App.closeDocument(doc.Name)

    def test_cad_create_profile_supports_backend_profile_primitives(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSemanticProfilePrimitiveCheck")
        original_project_info = VibeCADProject._active_document_info
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            service = VibeCADService()
            original_project_info = _attach_temp_project_store(
                service,
                Path(tmp_dir.name),
                "Semantic Profile Primitive Check",
            )

            result = service.registry.call(
                "cad.create_profile",
                component_name="MountPlate",
                profile_name="MountPlateProfile",
                purpose=(
                    "A mounting plate profile with a rectangular outline, "
                    "a slotted adjustment cutout, and repeated mounting holes."
                ),
                requires_curves=True,
                entities=[
                    {
                        "name": "outline",
                        "kind": "rectangle",
                        "width": 80,
                        "height": 32,
                        "center_x": 0,
                        "center_y": 0,
                    },
                    {
                        "name": "adjust_slot",
                        "kind": "slot",
                        "center_x": 0,
                        "center_y": 0,
                        "overall_length": 24,
                        "width": 6,
                    },
                    {
                        "name": "m4_mount",
                        "kind": "hole_pattern",
                        "pattern": "rectangular",
                        "hole_diameter": 4.5,
                        "center_x": 0,
                        "center_y": 0,
                        "count_x": 2,
                        "count_y": 1,
                        "spacing_x": 52,
                    },
                ],
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["entity_kind_counts"]["rectangle"], 1)
            self.assertEqual(result["entity_kind_counts"]["slot"], 1)
            self.assertEqual(result["entity_kind_counts"]["hole_pattern"], 1)
            self.assertEqual(result["requested_curve_entity_count"], 2)
            self.assertGreaterEqual(result["actual_curve_geometry_count"], 4)
            self.assertIn("ArcOfCircle", result["actual_curve_geometry_types"])
            self.assertIn("Circle", result["actual_curve_geometry_types"])

            rectangle_result = result["entity_results"][0]
            self.assertEqual(
                rectangle_result["semantic_handles"],
                [
                    "name:outline_top",
                    "name:outline_right",
                    "name:outline_bottom",
                    "name:outline_left",
                ],
            )
            slot_result = result["entity_results"][1]
            self.assertEqual(
                slot_result["semantic_handles"],
                [
                    "name:adjust_slot_top_side",
                    "name:adjust_slot_right_end",
                    "name:adjust_slot_bottom_side",
                    "name:adjust_slot_left_end",
                ],
            )
            hole_payload = result["entity_results"][2]["transaction"]["result"]
            self.assertEqual(
                hole_payload["semantic_handles"],
                ["name:m4_mount_1", "name:m4_mount_2"],
            )

            resolved = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=result["profile"],
                geometry_handle="name:adjust_slot_right_end",
            )
            self.assertTrue(resolved["ok"], resolved)
        finally:
            VibeCADProject._active_document_info = original_project_info
            tmp_dir.cleanup()
            App.closeDocument(doc.Name)

    def test_cad_create_feature_finish_edges_preserves_draft_and_thickness_fields(self):
        from tool_impl.service import cad_create_feature

        class FakeRegistry:
            def __init__(self):
                self.calls = []

            def call(self, tool_name, **kwargs):
                self.calls.append((tool_name, kwargs))
                return {"ok": True, "tool": tool_name}

        registry = FakeRegistry()
        service = types.SimpleNamespace(registry=registry)

        draft = cad_create_feature.run(
            service,
            operation="finish_edges",
            purpose="Add manufacturable draft to molded side faces.",
            feature_name="HousingPad",
            finish_operation="draft",
            face_names=["Face3", "Face4"],
            neutral_plane_name="PartingPlane",
            pull_direction_name="PullAxis",
            angle=2.5,
            reversed=True,
        )
        self.assertTrue(draft["ok"], draft)
        draft_call = [
            call for call in registry.calls if call[0] == "partdesign.dressup"
        ][-1]
        self.assertEqual(draft_call[1]["operation"], "draft")
        self.assertEqual(draft_call[1]["neutral_plane_name"], "PartingPlane")
        self.assertEqual(draft_call[1]["pull_direction_name"], "PullAxis")
        self.assertEqual(draft_call[1]["angle"], 2.5)
        self.assertIs(draft_call[1]["reversed"], True)

        thickness = cad_create_feature.run(
            service,
            operation="finish_edges",
            purpose="Shell the housing with controlled wall thickness.",
            feature_name="HousingPad",
            finish_operation="thickness",
            face_names=["Face6"],
            wall_thickness=1.8,
            inward=True,
            mode=0,
            join=2,
        )
        self.assertTrue(thickness["ok"], thickness)
        thickness_call = [
            call for call in registry.calls if call[0] == "partdesign.dressup"
        ][-1]
        self.assertEqual(thickness_call[1]["operation"], "thickness")
        self.assertEqual(thickness_call[1]["wall_thickness"], 1.8)
        self.assertIs(thickness_call[1]["inward"], True)
        self.assertEqual(thickness_call[1]["mode"], 0)
        self.assertEqual(thickness_call[1]["join"], 2)

    def test_cad_create_feature_blocks_when_required_profile_close_fails(self):
        from tool_impl.service import cad_create_feature

        class FakeRegistry:
            def __init__(self):
                self.calls = []

            def call(self, tool_name, **kwargs):
                self.calls.append((tool_name, kwargs))
                if tool_name == "sketcher.close_sketch":
                    sketch_name = kwargs.get("sketch_name")
                    if sketch_name == "BadSection":
                        return {
                            "ok": False,
                            "error": "Sketch is not closed.",
                            "profile_status": {"closed_profile": False},
                        }
                    return {"ok": True, "profile_status": {"closed_profile": True}}
                if tool_name == "core.update_design_memory":
                    return {"ok": True}
                return {"ok": True, "tool": tool_name}

        registry = FakeRegistry()
        service = types.SimpleNamespace(registry=registry)

        result = cad_create_feature.run(
            service,
            operation="add_loft",
            purpose="Loft only after every section sketch closes cleanly.",
            profiles=["RootSection", "BadSection", "TipSection"],
        )

        self.assertFalse(result["ok"], result)
        self.assertFalse(result["retry_same_call"], result)
        self.assertEqual(result["failed_profile"], "BadSection")
        self.assertIn("could not be closed", result["error"])
        called_tools = [tool_name for tool_name, _ in registry.calls]
        self.assertEqual(
            called_tools,
            ["sketcher.close_sketch", "sketcher.close_sketch"],
        )
        self.assertNotIn("partdesign.loft_profiles", called_tools)
        self.assertNotIn("core.update_design_memory", called_tools)

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
        old_settings = load_settings()
        self.addCleanup(save_settings, old_settings)
        save_settings(
            VibeCADSettings(
                enable_native_freecad_tools=True,
                native_tool_workbenches=(
                    "AssemblyWorkbench",
                    "BIMWorkbench",
                    "CAMWorkbench",
                    "DraftWorkbench",
                    "FemWorkbench",
                    "InspectionWorkbench",
                    "MaterialWorkbench",
                    "MeshPartWorkbench",
                    "MeshWorkbench",
                    "NoneWorkbench",
                    "OpenSCADWorkbench",
                    "PartDesignWorkbench",
                    "PartWorkbench",
                    "PointsWorkbench",
                    "ReverseEngineeringWorkbench",
                    "RobotWorkbench",
                    "SketcherWorkbench",
                    "SpreadsheetWorkbench",
                    "SurfaceWorkbench",
                    "TechDrawWorkbench",
                ),
            )
        )
        service = VibeCADService()

        def surface(workbench):
            return {
                schema["name"]
                for schema in provider_safe_tool_schemas(service, workbench)
            }

        all_native_pack_tools: set[str] = set()
        for pack in WORKBENCH_TOOL_PACKS.values():
            all_native_pack_tools.update(pack.provider_tool_names())
        for read_tools in WORKBENCH_READ_TOOLS.values():
            all_native_pack_tools.update(read_tools)

        def allowed_native_tools(workbench):
            allowed = set(WORKBENCH_READ_TOOLS.get(workbench, set()))
            pack = get_tool_pack(workbench)
            if pack is None:
                return allowed
            for tool_name in pack.tool_names:
                try:
                    tool = service.registry.get(tool_name)
                except KeyError:
                    continue
                owner = getattr(tool, "workbench", None)
                if owner == workbench:
                    allowed.add(tool_name)
            allowed.update(pack.required_adjacent_tool_names)
            return allowed

        for workbench in sorted(WORKBENCH_TOOL_PACKS):
            with self.subTest(native_tool_scope=workbench):
                names = surface(workbench)
                leaked = (names & all_native_pack_tools) - allowed_native_tools(workbench)
                self.assertFalse(leaked, (workbench, sorted(leaked)))

        part_names = surface("PartWorkbench")
        sketcher_names = surface("SketcherWorkbench")
        partdesign_names = surface("PartDesignWorkbench")

        # Part workbench pack: consolidated dressup, no PartDesign/sketcher tools.
        self.assertIn("part.set_placement", part_names)
        self.assertIn("part.cut_cylindrical_hole", part_names)
        self.assertIn("part.dressup", part_names)
        self.assertIn("part.thicken_surface", part_names)
        self.assertNotIn("draft.create_array", part_names)
        self.assertNotIn("partdesign.find_subelements", part_names)
        self.assertNotIn("partdesign.create_sketch", part_names)
        self.assertNotIn("sketcher.add_geometry", part_names)
        self.assertNotIn("core.run_workbench_command", part_names)

        # Sketcher pack: consolidated multi-function tools only.
        # sketcher.get_sketch was retired: sketcher.inspect_sketch is a strict
        # superset (adds solver DoF, profile readiness, repair diagnostics).
        self.assertNotIn("core.report_tool_shape_gap", sketcher_names)
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

        # PartDesign pack: consolidated features + required adjacent Sketcher
        # tools minus sketcher.create_sketch (Body sketches come from
        # partdesign.create_sketch).
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
        self.assertIn("core.list_workbench_objects", draft_names)
        self.assertIn("draft.create_array", draft_names)
        self.assertIn("draft.create_wire", draft_names)
        self.assertNotIn("part.set_placement", draft_names)
        techdraw_names = surface("TechDrawWorkbench")
        self.assertIn("core.list_workbench_objects", techdraw_names)
        self.assertIn("techdraw.get_pages", techdraw_names)
        self.assertIn("techdraw.create_page", techdraw_names)
        self.assertIn("techdraw.add_view", techdraw_names)
        assembly_names = surface("AssemblyWorkbench")
        self.assertIn("core.list_workbench_objects", assembly_names)
        self.assertIn("assembly.get_assemblies", assembly_names)
        self.assertIn("assembly.create_assembly", assembly_names)
        self.assertIn("assembly.add_component", assembly_names)
        self.assertIn("assembly.set_component_placement", assembly_names)
        self.assertIn("assembly.check_interference", assembly_names)
        # Kinematic mating stays inside the Assembly workbench pack.
        self.assertIn("assembly.ground_component", assembly_names)
        self.assertIn("assembly.create_joint", assembly_names)
        self.assertIn("assembly.solve", assembly_names)
        self.assertIn("partdesign.find_subelements", assembly_names)
        self.assertNotIn("partdesign.extrude", assembly_names)
        self.assertNotIn("assembly.check_interference", partdesign_names)
        material_names = surface("MaterialWorkbench")
        self.assertIn("material.apply_appearance", material_names)

        # Surface pack exposes Surface-owned operations plus required adjacent
        # tools for boundary curves, topology picking, and thickening to solid.
        surface_names = surface("SurfaceWorkbench")
        self.assertIn("core.list_workbench_objects", surface_names)
        self.assertIn("surface.create_surface", surface_names)
        self.assertIn("draft.create_wire", surface_names)
        self.assertIn("part.thicken_surface", surface_names)
        self.assertIn("partdesign.find_subelements", surface_names)
        self.assertNotIn("draft.create_array", surface_names)
        self.assertNotIn("partdesign.extrude", surface_names)
        self.assertNotIn("sketcher.add_geometry", surface_names)
        self.assertNotIn("part.set_placement", surface_names)

        # Workbenches without native provider tools stay on the AI-native
        # surface and never leak modeling pack tools.
        for workbench in (
            "FemWorkbench",
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
                self.assertIn("cad.inspect_state", names)
                self.assertNotIn("core.list_workbench_objects", names)
                self.assertNotIn("sketcher.add_geometry", names)
                self.assertNotIn("partdesign.extrude", names)
                self.assertNotIn("part.set_placement", names)

        test_names = surface("TestWorkbench")
        self.assertIn("cad.inspect_state", test_names)
        self.assertNotIn("core.list_workbench_objects", test_names)
        self.assertNotIn("core.run_workbench_command", test_names)
        none_names = surface("NoneWorkbench")
        self.assertIn("cad.inspect_state", none_names)
        self.assertNotIn("core.get_active_document", none_names)
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
                "cam.validate_job",
                "cam.postprocess",
            ):
                self.assertNotIn(hidden, cam_names, hidden)
            self.assertIn("model.build_from_script", cam_names)
            # AI-native read/view tools stay available in script mode.
            for kept in (
                "cad.inspect_state",
                "cad.verify_design",
                "core.capture_view_screenshot",
                "core.set_view",
                "core.get_report_view_errors",
            ):
                self.assertIn(kept, names, kept)
        finally:
            save_settings(VibeCADSettings(enable_build_script=False))

    def test_script_mode_runner_blocks_structured_writes_with_specific_error(self):
        import FreeCAD as App

        service = VibeCADService()
        doc = App.newDocument("VibeCADScriptModeGateTest")
        try:
            save_settings(
                VibeCADSettings(
                    enable_build_script=True,
                    enable_native_freecad_tools=True,
                    native_tool_workbenches=("PartDesignWorkbench",),
                )
            )
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

    def test_provider_tool_scope_is_ai_native_until_native_pack_enabled(self):
        service = VibeCADService()
        old_settings = load_settings()
        self.addCleanup(save_settings, old_settings)

        save_settings(VibeCADSettings(enable_native_freecad_tools=False))
        scope = provider_tool_scope_for_context(service, "PartDesignWorkbench")
        self.assertEqual(scope.stage, "ai_native_cad")
        self.assertEqual(scope.tool_names, set(CORE_PROVIDER_TOOLS))
        self.assertNotIn("partdesign.create_sketch", scope.tool_names)
        self.assertNotIn("sketcher.add_geometry", scope.tool_names)

        save_settings(
            VibeCADSettings(
                enable_native_freecad_tools=True,
                native_tool_workbenches=("PartDesignWorkbench",),
            )
        )
        pd_pack = get_tool_pack("PartDesignWorkbench")
        pd_scope = provider_tool_scope_for_context(service, "PartDesignWorkbench")
        self.assertEqual(pd_scope.stage, "native_workbench_pack")
        self.assertEqual(
            pd_scope.tool_names,
            set(CORE_PROVIDER_TOOLS)
            | {"partdesign.get_bodies"}
            | set(pd_pack.provider_tool_names()),
        )
        self.assertNotIn("sketcher.create_sketch", pd_scope.tool_names)
        self.assertIn("partdesign.create_sketch", pd_scope.tool_names)
        self.assertIn("sketcher.add_geometry", pd_scope.tool_names)

        unknown_scope = provider_tool_scope_for_context(service, "NoSuchWorkbench")
        self.assertEqual(unknown_scope.stage, "ai_native_cad")
        self.assertEqual(unknown_scope.tool_names, set(CORE_PROVIDER_TOOLS))

    def test_provider_surfaces_do_not_expose_workspace_switch_tools(self):
        from provider_tools import registered_tool_names

        service = VibeCADService()
        self.assertNotIn("core.activate_workbench", registered_tool_names())
        self.assertNotIn("core.enter_workspace", registered_tool_names())
        self.assertNotIn("core.activate_workbench", CORE_PROVIDER_TOOLS)
        self.assertNotIn("core.enter_workspace", CORE_PROVIDER_TOOLS)
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
            self.assertNotIn("core.enter_workspace", names, workbench)
        # Workspace alignment is an internal service method, not a tool.
        registry_names = set(service.registry.names())
        self.assertNotIn("core.activate_workbench", registry_names)
        self.assertNotIn("core.enter_workspace", registry_names)

    def test_partdesign_create_sketch_does_not_force_hidden_workspace_handoff(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADCreateSketchRefreshHandoffTest")
        try:
            old_settings = load_settings()
            self.addCleanup(save_settings, old_settings)
            save_settings(
                VibeCADSettings(
                    enable_native_freecad_tools=True,
                    native_tool_workbenches=("PartDesignWorkbench",),
                )
            )
            service = VibeCADService()
            with _temporary_design_project(service, "Create Sketch Refresh"):
                service.active_workbench_name = lambda: "PartDesignWorkbench"  # type: ignore[method-assign]
                body = service.registry.call("partdesign.create_body", label="Handoff Body")
                self.assertTrue(body["ok"], body)
                trace = []
                runner = make_provider_tool_runner(
                    service,
                    "PartDesignWorkbench",
                    tool_trace=trace,
                )

                result = runner(
                    "partdesign.create_sketch",
                    '{"body_name": "Handoff Body", "label": "Component Sketch", "plane": "XY_Plane"}',
                )

                self.assertTrue(result["ok"], result)
                self.assertIsNone(result.get("workspace_handoff"))
                self.assertNotIn("required_next_action", result)

                drawn = runner(
                    "sketcher.draw_rectangle",
                    '{"sketch_name": "Component Sketch", "width": 10, "height": 10}',
                )
                self.assertTrue(drawn["ok"], drawn)
                self.assertIsNone(drawn.get("status"))
                self.assertIsNone(drawn.get("workspace_handoff"))
        finally:
            App.closeDocument(doc.Name)

    def test_provider_workbench_does_not_remap_while_editing_body_sketch(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignEffectiveWorkbenchTest")
        try:
            old_settings = load_settings()
            self.addCleanup(save_settings, old_settings)
            save_settings(
                VibeCADSettings(
                    enable_native_freecad_tools=True,
                    native_tool_workbenches=("SketcherWorkbench",),
                )
            )
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
            self.assertNotIn("core.enter_workspace", names)
        finally:
            App.closeDocument(doc.Name)

    def test_provider_safe_sketcher_context_requires_explicit_partdesign_entry(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketcherPartDesignBridgeTest")
        try:
            old_settings = load_settings()
            self.addCleanup(save_settings, old_settings)
            save_settings(
                VibeCADSettings(
                    enable_native_freecad_tools=True,
                    native_tool_workbenches=("SketcherWorkbench",),
                )
            )
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
            self.assertNotIn("core.enter_workspace", after_names)
        finally:
            App.closeDocument(doc.Name)

    def test_part_pack_tools_are_native_in_part_workbench(self):
        old_settings = load_settings()
        self.addCleanup(save_settings, old_settings)
        save_settings(
            VibeCADSettings(
                enable_native_freecad_tools=True,
                native_tool_workbenches=("PartWorkbench", "PartDesignWorkbench"),
            )
        )
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
        self.assertFalse(blocked["retry_same_call"])
        self.assertTrue(blocked["recoverable"])

    def test_provider_tool_runner_blocks_out_of_scope_workbench_tools(self):
        service = VibeCADService()
        with _temporary_design_project(service, "Out Of Scope Workbench"):
            runner = make_provider_tool_runner(service, "SketcherWorkbench")
            blocked = runner("part.set_placement", '{"object_name": "Missing", "position": [0, 0, 0]}')
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["tool_workbench"], "PartWorkbench")
        self.assertIn("Tool is not available", blocked["error"])
        self.assertFalse(blocked["retry_same_call"])
        self.assertTrue(blocked["recoverable"])

    def test_provider_tool_runner_blocks_partdesign_write_while_sketch_is_open(self):
        service = VibeCADService()
        service.active_workbench_name = lambda: "PartDesignWorkbench"  # type: ignore[method-assign]
        service.task_panel_summary = lambda: {  # type: ignore[method-assign]
            "available": True,
            "edit_mode": True,
            "active_sketch": "BladeProfile",
            "profile_status": {
                "ready_for_pad": False,
                "ready_for_pocket": False,
                "closed_profile": False,
                "reason": "Wire is not closed.",
            },
        }
        runner = make_provider_tool_runner(service, "PartDesignWorkbench")

        blocked = runner(
            "partdesign.extrude",
            '{"sketch_name": "BladeProfile", "operation": "pad", "length": 3.2}',
        )

        self.assertFalse(blocked["ok"], blocked)
        self.assertEqual(blocked["active_sketch"], "BladeProfile")
        self.assertFalse(blocked["retry_same_call"])
        self.assertEqual(
            blocked["required_next_action"]["tool"],
            "sketcher.inspect_sketch",
        )
        self.assertIn("still editing sketch", blocked["error"])

    def test_provider_tool_runner_marks_hard_geometry_payload_non_repeatable(self):
        service = VibeCADService()
        service.active_workbench_name = lambda: "PartDesignWorkbench"  # type: ignore[method-assign]
        hard_payload = {
            "ok": False,
            "transaction": {
                "report_view_errors": {
                    "errors": ["BladePad: Wire is not closed."]
                }
            },
        }
        with mock.patch.object(service.registry, "call", return_value=hard_payload):
            runner = make_provider_tool_runner(service, "PartDesignWorkbench")
            result = runner(
                "partdesign.dressup",
                '{"operation": "fillet", "feature_name": "BladePad", "edge_names": ["Edge1"], "radius": 1.0}',
            )

        self.assertFalse(result["ok"], result)
        self.assertFalse(result["retry_same_call"])
        self.assertIn("Wire is not closed", result["error"])
        self.assertEqual(
            result["required_next_action"]["tool"],
            "core.get_report_view_errors",
        )

    def test_provider_tool_runner_rejects_part_tools_in_partdesign(self):
        service = VibeCADService()
        with _temporary_design_project(service, "Reject Part Tool"):
            runner = make_provider_tool_runner(service, "PartDesignWorkbench")
            blocked = runner(
                "part.dressup",
                '{"object_name": "Missing", "operation": "fillet", "radius": 1}',
            )
        self.assertFalse(blocked["ok"])
        self.assertIn("Tool is not available for the selected workspace", blocked["error"])
        self.assertEqual(blocked["selected_workbench"], "PartDesignWorkbench")
        self.assertEqual(blocked["tool_workbench"], "PartWorkbench")
        self.assertFalse(blocked["retry_same_call"])
        self.assertTrue(blocked["recoverable"])

    def test_provider_tool_runner_blocks_tools_from_other_native_workbenches(self):
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
            self.assertFalse(blocked["ok"], blocked)
            self.assertIn("not available for the selected workspace", blocked["error"])
            self.assertEqual(blocked["selected_workbench"], "PartWorkbench")
            self.assertEqual(blocked["tool_workbench"], "PartDesignWorkbench")
        finally:
            if doc is not None:
                App.closeDocument(doc.Name)

    def test_provider_tool_runner_blocks_internal_workbench_activation(self):
        service = VibeCADService()
        runner = make_provider_tool_runner(
            service,
            "PartDesignWorkbench",
        )
        result = runner("core.activate_workbench", '{"name": "PartDesignWorkbench"}')

        self.assertFalse(result["ok"], result)
        self.assertIn("Unknown VibeCAD tool", result["error"])
        self.assertIsNone(result.get("workspace_handoff"))

    def test_autonomous_loop_ignores_stale_workspace_handoff_trace(self):
        trace = [
            {
                "tool_name": "core.activate_workbench",
                "ok": True,
                "result": {"ok": True, "workspace_handoff": "workbench_switch"},
            }
        ]
        service = VibeCADService()
        self.assertFalse(
            _should_continue_autonomously(
                "Design a usable quadcopter drone concept.",
                "Workspace changed.",
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

    def test_provider_tool_runner_ignores_stale_loop_budget_state(self):
        import FreeCAD as App

        service = VibeCADService()
        old_settings = load_settings()
        self.addCleanup(save_settings, old_settings)
        save_settings(
            VibeCADSettings(
                enable_native_freecad_tools=True,
                native_tool_workbenches=("PartDesignWorkbench",),
            )
        )
        with _temporary_design_project(service, "Stale Operation Score Ignored"):
            service.active_workbench_name = lambda: "PartDesignWorkbench"  # type: ignore[method-assign]
            runner = make_provider_tool_runner(
                service,
                "PartDesignWorkbench",
            )
            result = runner(
                "partdesign.create_sketch",
                '{"label": "No Budget Gate Sketch", "plane": "XY_Plane"}',
            )
        doc = App.ActiveDocument
        try:
            self.assertTrue(result["ok"], result)
            self.assertIsNone(result.get("status"))
            self.assertIsNone(result.get("workspace_handoff"))
            self.assertTrue(
                any(
                    getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
                    and getattr(obj, "Label", "") == "No Budget Gate Sketch"
                    for obj in (doc.Objects if doc else [])
                )
            )
        finally:
            if doc is not None:
                App.closeDocument(doc.Name)

    def test_autonomous_loop_does_not_continue_for_legacy_workspace_handoff(self):
        service = VibeCADService()
        trace = [
            {
                "tool_name": "core.enter_workspace",
                "ok": True,
                "result": {"ok": True, "workspace_handoff": "workspace_entry"},
            }
        ]
        self.assertFalse(
            _should_continue_autonomously(
                "Design a usable bearing carrier bracket and capture the viewport.",
                "Entered PartDesign.",
                service,
                trace,
                0,
            )
        )

    def test_autonomous_loop_ignores_output_phrases_without_structured_signals(self):
        service = VibeCADService()
        self.assertFalse(
            _should_continue_autonomously(
                "Design a usable bearing carrier bracket.",
                "Next steps: I'm ready to continue once the tools allow. "
                "Progress refresh requested. Please confirm?",
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
        self.assertFalse(result["retry_same_call"])
        self.assertTrue(result["recoverable"])
        result = service.registry.call("core.create_new_document", name="VibeCADNamedDocument")
        try:
            self.assertTrue(result["ok"], result)
            self.assertIsNotNone(App.getDocument("VibeCADNamedDocument"))
        finally:
            doc = App.getDocument("VibeCADNamedDocument")
            if doc is not None:
                App.closeDocument(doc.Name)

    def test_native_tool_pack_is_hidden_until_enabled(self):
        old_settings = load_settings()
        try:
            save_settings(
                VibeCADSettings(enable_native_freecad_tools=False)
            )
            service = VibeCADService()
            schemas = provider_safe_tool_schemas(service, "PartWorkbench")
            names = {schema["name"] for schema in schemas}
            self.assertIn("cad.create_profile", names)
            self.assertNotIn("part.dressup", names)

            surface = service.provider_tool_surface("PartWorkbench")
            self.assertFalse(surface["tool_pack_enabled"])
            surface_names = {tool["name"] for tool in surface["tools"]}
            self.assertNotIn("part.dressup", surface_names)
            self.assertFalse(service.is_provider_tool_available("part.dressup", "PartWorkbench"))

            runner = make_provider_tool_runner(service, "PartWorkbench")
            blocked = runner("part.dressup", "{}")
            self.assertFalse(blocked["ok"])
            self.assertIn("not available for the selected workspace", blocked["error"])
            self.assertFalse(blocked["retry_same_call"])
            self.assertTrue(blocked["recoverable"])
        finally:
            save_settings(old_settings)

    def test_all_native_tool_packs_are_hidden_until_global_native_mode_enabled(self):
        old_settings = load_settings()
        self.addCleanup(save_settings, old_settings)
        save_settings(VibeCADSettings(enable_native_freecad_tools=False))
        service = VibeCADService()

        native_or_workbench_read_tools: set[str] = set()
        for pack in WORKBENCH_TOOL_PACKS.values():
            native_or_workbench_read_tools.update(pack.provider_tool_names())
        for read_tools in WORKBENCH_READ_TOOLS.values():
            native_or_workbench_read_tools.update(read_tools)

        for workbench in sorted(WORKBENCH_TOOL_PACKS):
            with self.subTest(workbench=workbench):
                names = {
                    schema["name"]
                    for schema in provider_safe_tool_schemas(service, workbench)
                }
                leaked = names & native_or_workbench_read_tools
                self.assertFalse(leaked, (workbench, sorted(leaked)))
                scope = provider_tool_scope_for_context(service, workbench)
                self.assertEqual(scope.stage, "ai_native_cad")
                self.assertEqual(scope.tool_names, set(CORE_PROVIDER_TOOLS))

    def test_service_settings_accessors_do_not_hide_load_failures(self):
        import VibeCADCore

        service = VibeCADService()
        with mock.patch.object(
            VibeCADCore,
            "load_settings",
            side_effect=RuntimeError("settings store failed"),
        ):
            accessors = (
                service.provider_name,
                service.provider_model,
                service.provider_base_url,
                service.provider_reasoning_effort,
                service.use_online_provider_by_default,
                service.native_freecad_tools_enabled,
                service.enabled_native_tool_workbenches,
                service.build_script_mode_enabled,
            )
            for accessor in accessors:
                with self.subTest(accessor=accessor.__name__):
                    with self.assertRaisesRegex(RuntimeError, "settings store failed"):
                        accessor()

    def test_provider_tool_surface_reports_scoped_tools(self):
        old_settings = load_settings()
        try:
            save_settings(
                VibeCADSettings(
                    enable_native_freecad_tools=True,
                    native_tool_workbenches=("PartWorkbench",),
                )
            )
            service = VibeCADService()
            surface = service.provider_tool_surface("PartWorkbench")
            names = {tool["name"] for tool in surface["tools"]}
            self.assertEqual(surface["active_workbench"], "PartWorkbench")
            self.assertTrue(surface["tool_pack_enabled"])
            self.assertNotIn("core.run_workbench_command", names)
            self.assertIn("cad.create_profile", names)
            self.assertIn("core.list_workbench_objects", names)
            self.assertIn("part.set_placement", names)
            self.assertIn("part.cut_cylindrical_hole", names)
            self.assertIn("part.dressup", names)
        finally:
            save_settings(old_settings)

    def test_required_adjacent_tools_follow_enabled_pack_not_owner_pack(self):
        old_settings = load_settings()
        try:
            save_settings(VibeCADSettings(enable_native_freecad_tools=False))
            service = VibeCADService()
            self.assertFalse(
                service.is_provider_tool_available(
                    "partdesign.find_subelements",
                    "AssemblyWorkbench",
                )
            )

            save_settings(
                VibeCADSettings(
                    enable_native_freecad_tools=True,
                    native_tool_workbenches=("AssemblyWorkbench",),
                )
            )
            service = VibeCADService()
            self.assertTrue(
                service.is_provider_tool_available(
                    "partdesign.find_subelements",
                    "AssemblyWorkbench",
                )
            )
            self.assertFalse(
                service.is_provider_tool_available(
                    "partdesign.extrude",
                    "AssemblyWorkbench",
                )
            )
        finally:
            save_settings(old_settings)

    def test_tool_shape_report_explains_available_and_missing_provider_capabilities(self):
        old_settings = load_settings()
        self.addCleanup(save_settings, old_settings)
        save_settings(
            VibeCADSettings(
                enable_native_freecad_tools=True,
                native_tool_workbenches=("PartDesignWorkbench", "AssemblyWorkbench"),
            )
        )
        service = VibeCADService()
        report = service.tool_shape_report("PartDesignWorkbench")
        names = set(report["provider_tool_names"])
        self.assertEqual(report["active_workbench"], "PartDesignWorkbench")
        self.assertNotIn("core.get_tool_shape_report", names)
        self.assertNotIn("core.report_tool_shape_gap", names)
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
        self.assertNotIn("length", slot_properties)
        self.assertNotIn("length_mode", slot_properties)
        self.assertIn("end-to-end", slot_properties["overall_length"]["description"])
        self.assertEqual(
            slot_schema["parameters"].get("anyOf"),
            [{"required": ["overall_length"]}, {"required": ["center_distance"]}],
        )
        self.assertIn("sketcher.add_constraint", names)
        self.assertNotIn("core.delete_object", names)
        self.assertNotIn("draft.create_array", names)
        self.assertNotIn("assembly.create_assembly", names)
        self.assertNotIn("techdraw.create_page", names)
        self.assertTrue(report["capabilities"]["atomic_sketch_geometry"]["available"])
        self.assertTrue(report["capabilities"]["atomic_sketch_constraints"]["available"])
        self.assertFalse(report["capabilities"]["iterative_delete"]["available"])
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
            draft_blocked = runner(
                "draft.create_array",
                '{"object_name": "Motor plate center bore", "label": "Motor plate bore pattern", "array_type": "polar", "polar_count": 4, "polar_angle": 360, "center_x": 30, "center_y": 20, "center_z": 0}',
            )
            self.assertFalse(draft_blocked["ok"], draft_blocked)
            self.assertIn("not available for the selected workspace", draft_blocked["error"])
            fillet = runner('part.dressup', '{"operation": "fillet", "object_name": "Motor plate center bore", "label": "Rounded motor plate", "radius": 0.5, "edge_indices": [1, 2, 3, 4]}')
            self.assertTrue(fillet["ok"], fillet)
            chamfer = runner('part.dressup', '{"operation": "chamfer", "object_name": "Motor plate", "label": "Chamfered motor plate", "distance": 0.5, "edge_indices": [1, 2, 3, 4]}')
            self.assertTrue(chamfer["ok"], chamfer)
            thickness = runner('part.dressup', '{"operation": "thickness", "object_name": "Motor plate", "label": "Hollow motor plate", "wall_thickness": 1.0, "face_names": ["Face6"], "inward": true}')
            self.assertTrue(thickness["ok"], thickness)

            labels = {getattr(obj, "Label", obj.Name) for obj in doc.Objects}
            self.assertIn("Motor plate", labels)
            self.assertIn("Motor plate center bore", labels)
            self.assertNotIn("Motor plate bore pattern", labels)
            self.assertIn("Rounded motor plate", labels)
            self.assertIn("Chamfered motor plate", labels)
            self.assertIn("Hollow motor plate", labels)
            self.assertAlmostEqual(float(plate.Length), 70.0)
            self.assertAlmostEqual(float(plate.Width), 45.0)
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
        self.assertIn("cad.inspect_state", names)
        self.assertIn("core.get_report_view_errors", names)
        self.assertNotIn("core.get_active_document", names)
        self.assertNotIn("partdesign.create_sketch", names)
        self.assertNotIn("sketcher.add_geometry", names)
        self.assertNotIn("core.run_workbench_command", names)
        self.assertNotIn("core.propose_create_workbench_object", names)
