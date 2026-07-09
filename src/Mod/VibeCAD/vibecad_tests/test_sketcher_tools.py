# SPDX-License-Identifier: LGPL-2.1-or-later

import types

from VibeCADCore import (
    VibeCADService,
)

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
)


class TestVibeCADSketcherTools(SettingsSnapshotTestCase):
    def test_sketcher_summary_reads_real_sketch(self):
        import FreeCAD as App
        import Part

        doc = App.newDocument("VibeCADSketcherSummaryTest")
        try:
            sketch = doc.addObject("Sketcher::SketchObject", "SketchForSummary")
            sketch.addGeometry(
                Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
                False,
            )
            sketch.addGeometry(
                Part.LineSegment(App.Vector(0, 5, 0), App.Vector(10, 5, 0)),
                True,
            )
            doc.recompute()
            service = VibeCADService()
            summary = service.sketcher_summary(sketch.Name)
            self.assertTrue(summary["found"])
            self.assertEqual(summary["sketch"]["name"], sketch.Name)
            self.assertEqual(summary["geometry_count"], 2)
            self.assertEqual(summary["geometry"][0]["type"], "LineSegment")
            self.assertFalse(summary["geometry"][0]["construction"])
            self.assertTrue(summary["geometry"][1]["construction"])
            self.assertEqual(summary["profile_status"]["construction_geometry_count"], 1)
        finally:
            App.closeDocument(doc.Name)

    def test_set_sketcher_constraint_value_edits_existing_dimension(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchConstraintEditTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Editable Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            draw_result = service.registry.call("sketcher.draw_rectangle",
            center_x=0,
            center_y=0,
            construction=False,
                width=10,
                height=5,
                sketch_name=sketch.Name,
            )
            self.assertTrue(draw_result["ok"], draw_result)
            summary = service.sketcher_summary(sketch.Name)
            distance_constraints = [
                item for item in summary["constraints"]
                if item["type"] == "Distance" and abs(float(item.get("value", 0.0)) - 10.0) < 1e-6
            ]
            self.assertTrue(distance_constraints, summary["constraints"])
            edit_result = service.registry.call('sketcher.edit_constraint', action='set_value', sketch_name=sketch.Name, constraint_index=distance_constraints[0]['index'], value=20)
            self.assertTrue(edit_result["ok"], edit_result)
            edited = service.sketcher_summary(sketch.Name)
            edited_constraint = edited["constraints"][distance_constraints[0]["index"]]
            self.assertAlmostEqual(float(edited_constraint["value"]), 20.0)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_constraint_identity_tools_edit_design_intent(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchConstraintIdentityTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Named Constraint Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            draw_result = service.registry.call(
                "sketcher.draw_rectangle",
                center_x=0,
                center_y=0,
                construction=False,
                width=30,
                height=12,
                sketch_name=sketch.Name,
            )
            self.assertTrue(draw_result["ok"], draw_result)

            summary = service.registry.call('sketcher.inspect_sketch', include=['constraints'], sketch_name=sketch.Name)
            self.assertTrue(summary["ok"], summary)
            width_constraints = [
                item for item in summary["constraints"]
                if item["type"] == "Distance" and abs(float(item.get("value", 0.0)) - 30.0) < 1e-6
            ]
            self.assertTrue(width_constraints, summary["constraints"])
            width_index = width_constraints[0]["index"]

            rename_result = service.registry.call('sketcher.edit_constraint', action='set_name', sketch_name=sketch.Name, constraint_index=width_index, new_name='Width')
            self.assertTrue(rename_result["ok"], rename_result)
            lookup_result = service.registry.call('sketcher.edit_constraint', action='get', sketch_name=sketch.Name, constraint_name='Width')
            self.assertTrue(lookup_result["ok"], lookup_result)
            self.assertEqual(lookup_result["constraint_index"], width_index)
            self.assertEqual(lookup_result["constraint"]["name"], "Width")

            edit_result = service.registry.call('sketcher.edit_constraint', action='set_value', sketch_name=sketch.Name, constraint_name='Width', value=42)
            self.assertTrue(edit_result["ok"], edit_result)
            edited_lookup = service.registry.call('sketcher.edit_constraint', action='get', sketch_name=sketch.Name, constraint_name='Width')
            self.assertAlmostEqual(float(edited_lookup["constraint"]["value"]), 42.0)

            expression_result = service.registry.call('sketcher.edit_constraint', action='set_expression', sketch_name=sketch.Name, constraint_index=width_index, expression='21 * 2')
            self.assertTrue(expression_result["ok"], expression_result)
            expression_summary = service.registry.call('sketcher.inspect_sketch', include=['constraints'], sketch_name=sketch.Name)
            width_after_expression = [
                item for item in expression_summary["constraints"]
                if item.get("name") == "Width"
            ][0]
            self.assertIn("expression", width_after_expression)

            driving_result = service.registry.call('sketcher.edit_constraint', action='set_driving', sketch_name=sketch.Name, constraint_index=width_index, driving=False)
            self.assertTrue(driving_result["ok"], driving_result)
            reference_lookup = service.registry.call('sketcher.edit_constraint', action='get', sketch_name=sketch.Name, constraint_name='Width')
            self.assertFalse(reference_lookup["constraint"]["driving"])
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_geometry_identity_tools_target_named_geometry(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchGeometryIdentityTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Named Geometry Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            line_result = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[0, 0], [25, 0]], construction=False)
            self.assertTrue(line_result["ok"], line_result)
            name_result = service.registry.call(
                "sketcher.set_geometry_name",
                sketch_name=sketch.Name,
                geometry_index=0,
                geometry_name="base_edge",
            )
            self.assertTrue(name_result["ok"], name_result)
            self.assertEqual(name_result["transaction"]["result"]["semantic_handle"], "name:base_edge")

            inventory = service.registry.call('sketcher.inspect_sketch', include=['geometry'], sketch_name=sketch.Name)
            self.assertTrue(inventory["ok"], inventory)
            self.assertEqual(inventory["named_geometry"]["base_edge"]["geometry_index"], 0)
            self.assertIn("name:base_edge", inventory["geometry"][0]["semantic_handles"])

            resolved = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=sketch.Name,
                geometry_handle="name:base_edge",
            )
            self.assertTrue(resolved["ok"], resolved)
            self.assertEqual(resolved["geometry_index"], 0)

            horizontal = service.registry.call('sketcher.add_constraint', constraint_type='Horizontal', sketch_name=sketch.Name, first_geometry_handle='name:base_edge')
            self.assertTrue(horizontal["ok"], horizontal)
            distance = service.registry.call('sketcher.add_constraint', constraint_type='Distance', sketch_name=sketch.Name, value=25, first_geometry_handle='name:base_edge')
            self.assertTrue(distance["ok"], distance)

            moved = service.registry.call(
                "sketcher.move_point",
                sketch_name=sketch.Name,
                geometry_handle="name:base_edge",
                point="whole",
                x=0,
                y=5,
                relative=True,
            )
            self.assertTrue(moved["ok"], moved)
            moved_inventory = service.registry.call('sketcher.inspect_sketch', include=['geometry'], sketch_name=sketch.Name)
            self.assertTrue(moved_inventory["named_geometry"]["base_edge"]["ok"], moved_inventory["named_geometry"])
            self.assertTrue(moved_inventory["named_geometry"]["base_edge"]["fingerprint_changed"])

            name_again = service.registry.call(
                "sketcher.set_geometry_name",
                sketch_name=sketch.Name,
                geometry_index=0,
                geometry_name="base_edge",
            )
            self.assertTrue(name_again["ok"], name_again)
            construction = service.registry.call(
                "sketcher.set_construction",
                sketch_name=sketch.Name,
                geometry_handle="name:base_edge",
                construction=True,
            )
            self.assertTrue(construction["ok"], construction)

            delete_result = service.registry.call('sketcher.delete_items', sketch_name=sketch.Name, geometry_items=['name:base_edge'])
            self.assertTrue(delete_result["ok"], delete_result)
            stale = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=sketch.Name,
                geometry_handle="name:base_edge",
            )
            self.assertFalse(stale["ok"], stale)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_semantic_constraint_tools_use_handles_and_references(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchSemanticConstraintTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Semantic Constraint Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            base = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[10, 10], [30, 10]], construction=False)
            self.assertTrue(base["ok"], base)
            upright = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[40, 0], [40, 20]], construction=False)
            self.assertTrue(upright["ok"], upright)
            circle = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=sketch.Name, radius=5, center=[60, 10], construction=False)
            self.assertTrue(circle["ok"], circle)

            for index, name in ((0, "base_edge"), (1, "upright_edge"), (2, "locator_circle")):
                named = service.registry.call(
                    "sketcher.set_geometry_name",
                    sketch_name=sketch.Name,
                    geometry_index=index,
                    geometry_name=name,
                )
                self.assertTrue(named["ok"], named)

            lock = service.registry.call('sketcher.add_constraint', constraint_type='Lock', sketch_name=sketch.Name, x=10, y=10, first_geometry_handle='name:base_edge', first_point='start')
            self.assertTrue(lock["ok"], lock)
            point_distance = service.registry.call('sketcher.add_constraint', constraint_type='Distance', sketch_name=sketch.Name, first_geometry_handle='name:base_edge', first_point='start', second_geometry_handle='origin', second_point='origin', value=14.1421356237)
            self.assertTrue(point_distance["ok"], point_distance)
            point_on_axis = service.registry.call('sketcher.add_constraint', constraint_type='PointOnObject', sketch_name=sketch.Name, first_geometry_handle='name:upright_edge', first_point='start', second_geometry_handle='axis:H')
            self.assertTrue(point_on_axis["ok"], point_on_axis)
            angle = service.registry.call('sketcher.add_constraint', constraint_type='Angle', sketch_name=sketch.Name, first_geometry_handle='name:base_edge', first_point='whole', second_geometry_handle='name:upright_edge', second_point='whole', value=90)
            self.assertTrue(angle["ok"], angle)
            block = service.registry.call('sketcher.add_constraint', constraint_type='Block', sketch_name=sketch.Name, first_geometry_handle='name:locator_circle')
            self.assertTrue(block["ok"], block)

            summary = service.registry.call('sketcher.inspect_sketch', include=['constraints'], sketch_name=sketch.Name)
            self.assertTrue(summary["ok"], summary)
            types = [item["type"] for item in summary["constraints"]]
            self.assertIn("DistanceX", types)
            self.assertIn("DistanceY", types)
            self.assertIn("Distance", types)
            self.assertIn("PointOnObject", types)
            self.assertIn("Angle", types)
            self.assertIn("Block", types)
            point_on_object = [item for item in summary["constraints"] if item["type"] == "PointOnObject"][-1]
            self.assertEqual(point_on_object["second"], -1)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_constraint_tools_accept_semantic_handles(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchSemanticHandleTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Semantic Handle Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            lines = [
                ("line_a", (0, 0, 20, 0)),
                ("line_b", (0, 5, 20, 5)),
                ("line_c", (30, 0, 30, 15)),
                ("line_d", (40, 0, 40, 15)),
            ]
            for name, coords in lines:
                line = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[coords[0], coords[1]], [coords[2], coords[3]]], construction=False)
                self.assertTrue(line["ok"], line)
                named = service.registry.call(
                    "sketcher.set_geometry_name",
                    sketch_name=sketch.Name,
                    geometry_index=line["transaction"]["result"]["geometry_index"],
                    geometry_name=name,
                )
                self.assertTrue(named["ok"], named)

            parallel = service.registry.call('sketcher.add_constraint', constraint_type='Parallel', sketch_name=sketch.Name, first_geometry_handle='name:line_a', second_geometry_handle='name:line_b')
            self.assertTrue(parallel["ok"], parallel)
            perpendicular = service.registry.call('sketcher.add_constraint', constraint_type='Perpendicular', sketch_name=sketch.Name, first_geometry_handle='name:line_a', second_geometry_handle='name:line_c')
            self.assertTrue(perpendicular["ok"], perpendicular)
            equal = service.registry.call('sketcher.add_constraint', constraint_type='Equal', sketch_name=sketch.Name, first_geometry_handle='name:line_c', second_geometry_handle='name:line_d')
            self.assertTrue(equal["ok"], equal)
            coincident = service.registry.call('sketcher.add_constraint', constraint_type='Coincident', sketch_name=sketch.Name, first_geometry_handle='name:line_a', first_point='start', second_geometry_handle='origin', second_point='origin')
            self.assertTrue(coincident["ok"], coincident)
            symmetric = service.registry.call('sketcher.add_constraint', constraint_type='Symmetric', sketch_name=sketch.Name, first_geometry_handle='name:line_c', first_point='start', second_geometry_handle='name:line_d', second_point='start', third_geometry_handle='axis:V', third_point='whole')
            self.assertTrue(symmetric["ok"], symmetric)

            dimension = service.registry.call('sketcher.add_constraint', constraint_type='Distance', sketch_name=sketch.Name, value=20, first_geometry_handle='name:line_b')
            self.assertTrue(dimension["ok"], dimension)
            dimension_index = dimension["transaction"]["result"]["constraint_index"]
            named_dimension = service.registry.call('sketcher.edit_constraint', action='set_name', sketch_name=sketch.Name, constraint_index=dimension_index, new_name='LineBLength')
            self.assertTrue(named_dimension["ok"], named_dimension)
            expression = service.registry.call('sketcher.edit_constraint', action='set_expression', sketch_name=sketch.Name, constraint_name='LineBLength', expression='10 + 10')
            self.assertTrue(expression["ok"], expression)
            driving = service.registry.call('sketcher.edit_constraint', action='set_driving', sketch_name=sketch.Name, constraint_name='LineBLength', driving=False)
            self.assertTrue(driving["ok"], driving)
            deleted = service.registry.call('sketcher.delete_items', sketch_name=sketch.Name, constraint_items=['LineBLength'])
            self.assertTrue(deleted["ok"], deleted)

            summary = service.registry.call('sketcher.inspect_sketch', include=['constraints'], sketch_name=sketch.Name)
            self.assertTrue(summary["ok"], summary)
            constraint_types = [item["type"] for item in summary["constraints"]]
            for constraint_type in ("Parallel", "Perpendicular", "Equal", "Coincident", "Symmetric"):
                self.assertIn(constraint_type, constraint_types)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_topology_edit_tools_accept_geometry_handles(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchTopologyHandleTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Topology Handle Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            line = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[0, 0], [20, 0]], construction=False)
            self.assertTrue(line["ok"], line)
            named = service.registry.call(
                "sketcher.set_geometry_name",
                sketch_name=sketch.Name,
                geometry_index=0,
                geometry_name="editable_line",
            )
            self.assertTrue(named["ok"], named)
            split = service.registry.call('sketcher.modify_geometry', operation='split', sketch_name=sketch.Name, geometry_handle='name:editable_line', x=10, y=0)
            self.assertTrue(split["ok"], split)
            renamed = service.registry.call(
                "sketcher.set_geometry_name",
                sketch_name=sketch.Name,
                geometry_index=0,
                geometry_name="editable_segment",
            )
            self.assertTrue(renamed["ok"], renamed)
            trim = service.registry.call('sketcher.modify_geometry', operation='trim', sketch_name=sketch.Name, geometry_handle='name:editable_segment', x=5, y=0)
            self.assertTrue(trim["ok"], trim)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_create_open_solver_and_profile_tools(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchLifecycleTest")
        try:
            service = VibeCADService()
            create_result = service.registry.call(
                "sketcher.create_sketch",
                label="Lifecycle Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(create_result["ok"], create_result)
            sketch_name = create_result["active_sketch"]
            sketch = doc.getObject(sketch_name)
            self.assertIsNotNone(sketch)
            self.assertEqual(sketch.Label, "Lifecycle Sketch")
            self.assertIn("solver_status", create_result)
            self.assertIn("profile_validation", create_result)

            open_result = service.registry.call("sketcher.open_sketch", sketch_name=sketch_name)
            self.assertTrue(open_result["ok"], open_result)
            self.assertEqual(open_result["active_sketch"], sketch_name)

            close_result = service.registry.call("sketcher.close_sketch", sketch_name=sketch_name)
            self.assertTrue(close_result["ok"], close_result)
            self.assertEqual(close_result["active_sketch"], sketch_name)
            self.assertIn("task_panel", close_result)
            self.assertIn("solver_status", close_result)
            self.assertIn("profile_validation", close_result)

            line_result = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch_name, points=[[0, 0], [10, 0]], construction=False)
            self.assertTrue(line_result["ok"], line_result)
            self.assertIn("solver_status", line_result)
            self.assertIn("profile_validation", line_result)

            solver = service.registry.call('sketcher.inspect_sketch', include=['solver'], sketch_name=sketch_name)
            self.assertTrue(solver["ok"], solver)
            self.assertEqual(solver["sketch"], sketch_name)
            self.assertEqual(solver["solver_status"]["geometry_count"], 1)

            profile = service.registry.call('sketcher.inspect_sketch', include=['profile'], sketch_name=sketch_name)
            self.assertTrue(profile["ok"], profile)
            self.assertFalse(profile["profile_validation"]["closed_profile"])
            self.assertGreaterEqual(profile["profile_validation"]["open_endpoint_count"], 2)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_deep_profile_and_constraint_diagnostics(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchDeepDiagnosticsTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "sketcher.create_sketch",
                label="Deep Diagnostics Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch_name = sketch_result["active_sketch"]
            line = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch_name, points=[[0, 0], [10, 0]], construction=False)
            self.assertTrue(line["ok"], line)

            profile = service.registry.call('sketcher.inspect_sketch', include=['profile_deep'], sketch_name=sketch_name)
            self.assertTrue(profile["ok"], profile)
            deep = profile["profile_validation_deep"]
            self.assertFalse(deep["closed_profile"])
            self.assertEqual(deep["nonconstruction_edge_count"], 1)
            self.assertGreaterEqual(len(deep["open_nodes"]), 2)
            blocker_kinds = {item["kind"] for item in deep["feature_readiness"]["blockers"]}
            self.assertIn("open_endpoints", blocker_kinds)
            self.assertNotIn("no_faces", blocker_kinds)
            self.assertFalse(deep["feature_readiness"]["pad"])
            self.assertIn("shape_face_diagnostic", deep)

            report = service.registry.call('sketcher.inspect_sketch', include=['constraint_diagnostics'], sketch_name=sketch_name)
            self.assertTrue(report["ok"], report)
            diagnostics = report["constraint_diagnostics"]
            self.assertTrue(diagnostics["solver_status"]["under_constrained"], diagnostics)
            self.assertFalse(diagnostics["limits"]["exact_per_parameter_dof_available"])
            self.assertEqual(len(diagnostics["per_geometry_constraint_coverage"]), 1)
            self.assertTrue(diagnostics["suggested_next_checks"], diagnostics)
            suggested_kinds = {item["kind"] for item in diagnostics["suggested_next_checks"]}
            self.assertIn("close_endpoint", suggested_kinds)
            self.assertIn("solver_repair_actions", diagnostics)
            self.assertIn("next_actions", diagnostics)
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_solver_repair_actions_are_direct_tool_calls(self):
        from tool_impl.sketcher.common import solver_repair_actions

        sketch = types.SimpleNamespace(Name="Sketch")
        solver = {
            "conflicting_constraint_indices": [3],
            "redundant_constraint_indices": [8, 11],
        }
        constraints = [{"index": index, "handle": f"constraint:{index}", "type": "Distance"} for index in range(12)]

        actions = solver_repair_actions(sketch, solver, constraints)

        self.assertEqual([item["kind"] for item in actions], [
            "remove_conflicting_constraint",
            "remove_redundant_constraint",
            "remove_redundant_constraint",
        ])
        for action, index in zip(actions, [3, 8, 11]):
            self.assertEqual(action["tool"], "sketcher.delete_items")
            self.assertEqual(action["arguments"], {"sketch_name": "Sketch", "constraint_items": [index]})
            self.assertEqual(action["target_constraint"]["handle"], f"constraint:{index}")

    def test_draw_rectangle_requires_explicit_target_center_and_construction(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADDrawRectangleExplicitContractTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "partdesign.create_sketch",
                label="Rectangle Contract Sketch",
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = doc.getObject(sketch_result["active_sketch"])
            self.assertIsNotNone(sketch)
            before_geometry = len(getattr(sketch, "Geometry", []))

            missing_sketch = service.registry.call(
                "sketcher.draw_rectangle",
                width=10,
                height=5,
                center_x=0,
                center_y=0,
                construction=False,
            )
            self.assertFalse(missing_sketch["ok"], missing_sketch)
            self.assertIn("sketch_name is required", missing_sketch["error"])
            self.assertFalse(missing_sketch.get("retry_same_call", True))

            missing_center = service.registry.call(
                "sketcher.draw_rectangle",
                sketch_name=sketch.Name,
                width=10,
                height=5,
                center_x=0,
                construction=False,
            )
            self.assertFalse(missing_center["ok"], missing_center)
            self.assertIn("center_y is required", missing_center["error"])
            self.assertFalse(missing_center.get("retry_same_call", True))

            missing_construction = service.registry.call(
                "sketcher.draw_rectangle",
                sketch_name=sketch.Name,
                width=10,
                height=5,
                center_x=0,
                center_y=0,
            )
            self.assertFalse(missing_construction["ok"], missing_construction)
            self.assertIn("construction is required", missing_construction["error"])
            self.assertEqual(before_geometry, len(getattr(sketch, "Geometry", [])))

            spec = service.registry.get("sketcher.draw_rectangle").to_schema()
            required = set(spec["parameters"]["required"])
            self.assertTrue(
                {"sketch_name", "width", "height", "center_x", "center_y", "construction"}
                <= required
            )
            serialized = str(spec).lower()
            self.assertNotIn("default 0", serialized)
            self.assertNotIn("default false", serialized)
            self.assertNotIn("first sketch", serialized)
        finally:
            App.closeDocument(doc.Name)

    def test_atomic_sketcher_tools_add_geometry_and_constraints(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAtomicSketchToolsTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Atomic Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            horizontal = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[0, 0], [10, 0]], construction=False)
            self.assertTrue(horizontal["ok"], horizontal)
            self.assertEqual(horizontal["mutation"]["created_geometry_indices"], [0])
            self.assertEqual(horizontal["mutation"]["geometry_count"], 1)
            vertical = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[10, 0], [10, 5]], construction=False)
            self.assertTrue(vertical["ok"], vertical)
            circle = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=sketch.Name, radius=2, center=[5, 2.5], construction=False)
            self.assertTrue(circle["ok"], circle)
            self.assertEqual(circle["mutation"]["created_geometry_indices"], [2])
            circle_next_tools = {
                item.get("tool")
                for item in circle["next_actions"]
                if isinstance(item, dict)
            }
            self.assertIn('sketcher.add_constraint', circle_next_tools)
            arc = service.registry.call('sketcher.add_geometry', kind='arc', sketch_name=sketch.Name, radius=4, start_angle_degrees=0, end_angle_degrees=90, center=[0, 0], construction=False)
            self.assertTrue(arc["ok"], arc)
            slot = service.registry.call("sketcher.add_slot",
                sketch_name=sketch.Name,
                center_x=20,
                center_y=0,
                overall_length=14,
                width=4,
                angle_degrees=0,
                construction=False,
            )
            self.assertTrue(slot["ok"], slot)
            self.assertEqual(slot["mutation"]["created_geometry_indices"], [4, 5, 6, 7])
            self.assertEqual(len(slot["mutation"]["created_constraint_indices"]), 4)
            self.assertGreaterEqual(slot["profile_status"]["edge_count"], 4)
            slot_block_suggestions = [
                item
                for item in slot["next_actions"]
                if isinstance(item, dict)
                and item.get("tool") == "sketcher.add_constraint"
                and (item.get("arguments") or {}).get("constraint_type") == "Block"
            ]
            self.assertFalse(slot_block_suggestions, slot_block_suggestions)

            coincident_constraint = service.registry.call('sketcher.add_constraint', constraint_type='Coincident', sketch_name=sketch.Name, first_geometry=0, first_point='end', second_geometry=1, second_point='start')
            self.assertTrue(coincident_constraint["ok"], coincident_constraint)
            horizontal_constraint = service.registry.call("sketcher.add_constraint",
                sketch_name=sketch.Name,
                constraint_type="Horizontal",
                first_geometry=0,
            )
            self.assertTrue(horizontal_constraint["ok"], horizontal_constraint)
            vertical_constraint = service.registry.call('sketcher.add_constraint', constraint_type='Vertical', sketch_name=sketch.Name, first_geometry=1)
            self.assertTrue(vertical_constraint["ok"], vertical_constraint)
            length_constraint = service.registry.call('sketcher.add_constraint', constraint_type='Distance', sketch_name=sketch.Name, value=10, first_geometry=0)
            self.assertTrue(length_constraint["ok"], length_constraint)
            radius_constraint = service.registry.call('sketcher.add_constraint', constraint_type='Radius', sketch_name=sketch.Name, value=2, first_geometry=2)
            self.assertTrue(radius_constraint["ok"], radius_constraint)
            self.assertEqual(len(radius_constraint["mutation"]["created_constraint_indices"]), 1)
            arc_radius_constraint = service.registry.call("sketcher.add_constraint",
                sketch_name=sketch.Name,
                constraint_type="Radius",
                first_geometry=3,
                value=4,
            )
            self.assertTrue(arc_radius_constraint["ok"], arc_radius_constraint)

            summary = service.sketcher_summary(sketch.Name)
            self.assertEqual(summary["geometry_count"], 8)
            self.assertGreaterEqual(summary["constraint_count"], 10)
            self.assertIn("handle", summary["geometry"][0])
            self.assertIn("handle", summary["constraints"][0])
            constraint_types = [item["type"] for item in summary["constraints"]]
            for constraint_type in ("Coincident", "Horizontal", "Vertical", "Distance", "Radius"):
                self.assertIn(constraint_type, constraint_types)
            geometry_types = [item["type"] for item in summary["geometry"]]
            self.assertIn("ArcOfCircle", geometry_types)
            self.assertGreaterEqual(geometry_types.count("ArcOfCircle"), 3)
            self.assertGreaterEqual(geometry_types.count("LineSegment"), 4)
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_hole_pattern_creates_fully_constrained_named_profiles(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADHolePatternSketchTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Hole Pattern Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            result = service.registry.call(
                "sketcher.add_hole_pattern",
                sketch_name=sketch.Name,
                pattern="rectangular",
                hole_diameter=4.5,
                center_x=0,
                center_y=0,
                count_x=2,
                count_y=2,
                spacing_x=50,
                spacing_y=20,
                name_prefix="m4_clearance",
                construction=False,
                lock_centers=True,
                equal_radii=True,
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["mutation"]["created_geometry_indices"], [0, 1, 2, 3])
            self.assertEqual(len(result["mutation"]["created_constraint_indices"]), 12)
            self.assertEqual(result["profile_status"]["degrees_of_freedom"], 0)
            self.assertTrue(result["profile_status"]["ready_for_pocket"])
            self.assertTrue(result["profile_status"]["fully_constrained"])
            transaction = result["transaction"]["result"]
            self.assertEqual(transaction["hole_diameter"], 4.5)
            self.assertEqual(transaction["centers"], [[-25.0, -10.0], [25.0, -10.0], [-25.0, 10.0], [25.0, 10.0]])
            self.assertEqual(
                transaction["semantic_handles"],
                [
                    "name:m4_clearance_1",
                    "name:m4_clearance_2",
                    "name:m4_clearance_3",
                    "name:m4_clearance_4",
                ],
            )
            next_tools = {
                item.get("tool")
                for item in result["next_actions"]
                if isinstance(item, dict)
            }
            self.assertIn('partdesign.extrude', next_tools)
            self.assertIn("partdesign.hole_from_sketch", next_tools)
            resolved = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=sketch.Name,
                geometry_handle="name:m4_clearance_3",
            )
            self.assertTrue(resolved["ok"], resolved)
            self.assertEqual(resolved["geometry_index"], 2)
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_hole_pattern_requires_explicit_layout_and_behavior(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADHolePatternExplicitContractTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "partdesign.create_sketch",
                label="Hole Pattern Contract",
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = doc.getObject(sketch_result["active_sketch"])
            self.assertIsNotNone(sketch)
            before_geometry = len(getattr(sketch, "Geometry", []))

            missing_sketch = service.registry.call(
                "sketcher.add_hole_pattern",
                pattern="rectangular",
                hole_diameter=4.5,
                center_x=0,
                center_y=0,
                count_x=2,
                count_y=1,
                spacing_x=20,
                spacing_y=0,
                name_prefix="m4",
                construction=False,
                lock_centers=True,
                equal_radii=True,
            )
            self.assertFalse(missing_sketch["ok"], missing_sketch)
            self.assertIn("sketch_name is required", missing_sketch["error"])
            self.assertFalse(missing_sketch.get("retry_same_call", True))

            missing_spacing = service.registry.call(
                "sketcher.add_hole_pattern",
                sketch_name=sketch.Name,
                pattern="rectangular",
                hole_diameter=4.5,
                center_x=0,
                center_y=0,
                count_x=2,
                count_y=1,
                spacing_x=20,
                name_prefix="m4",
                construction=False,
                lock_centers=True,
                equal_radii=True,
            )
            self.assertFalse(missing_spacing["ok"], missing_spacing)
            self.assertIn("spacing_y is required", missing_spacing["error"])
            self.assertFalse(missing_spacing.get("retry_same_call", True))

            missing_behavior = service.registry.call(
                "sketcher.add_hole_pattern",
                sketch_name=sketch.Name,
                pattern="rectangular",
                hole_diameter=4.5,
                center_x=0,
                center_y=0,
                count_x=2,
                count_y=1,
                spacing_x=20,
                spacing_y=0,
                name_prefix="m4",
                construction=False,
                lock_centers=True,
            )
            self.assertFalse(missing_behavior["ok"], missing_behavior)
            self.assertIn("equal_radii is required", missing_behavior["error"])

            missing_prefix = service.registry.call(
                "sketcher.add_hole_pattern",
                sketch_name=sketch.Name,
                pattern="rectangular",
                hole_diameter=4.5,
                center_x=0,
                center_y=0,
                count_x=2,
                count_y=1,
                spacing_x=20,
                spacing_y=0,
                construction=False,
                lock_centers=True,
                equal_radii=True,
            )
            self.assertFalse(missing_prefix["ok"], missing_prefix)
            self.assertIn("name_prefix is required", missing_prefix["error"])
            self.assertEqual(before_geometry, len(getattr(sketch, "Geometry", [])))

            spec = service.registry.get("sketcher.add_hole_pattern").to_schema()
            required = set(spec["parameters"]["required"])
            self.assertTrue(
                {
                    "sketch_name",
                    "pattern",
                    "hole_diameter",
                    "center_x",
                    "center_y",
                    "name_prefix",
                    "construction",
                    "lock_centers",
                    "equal_radii",
                }
                <= required
            )
            serialized = str(spec).lower()
            self.assertNotIn("default 0", serialized)
            self.assertNotIn("default false", serialized)
            self.assertNotIn("default true", serialized)
            self.assertNotIn("first sketch", serialized)
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_slot_returns_partdesign_usable_profile(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSlotProfileReadinessTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Slot Profile")
            self.assertTrue(sketch_result["ok"], sketch_result)
            slot = service.registry.call(
                "sketcher.add_slot",
                sketch_name="Sketch",
                center_x=0,
                center_y=0,
                overall_length=20,
                width=6,
                angle_degrees=0,
                construction=False,
            )
            self.assertTrue(slot["ok"], slot)
            profile = slot["profile_status"]
            slot_result = slot["transaction"]["result"]
            self.assertEqual(slot_result["overall_length"], 20.0)
            self.assertEqual(slot_result["center_distance"], 14.0)
            self.assertEqual(slot_result["straight_segment_length"], 14.0)
            self.assertEqual(slot_result["radius"], 3.0)
            self.assertEqual(slot_result["bounding_box"]["width"], 20.0)
            self.assertEqual(slot_result["bounding_box"]["height"], 6.0)
            self.assertEqual(profile["edge_count"], 4, profile)
            self.assertTrue(profile["closed_profile"], profile)
            self.assertTrue(profile["fully_constrained"], profile)
            self.assertTrue(profile["ready_for_pad"], profile)
            self.assertTrue(profile["ready_for_pocket"], profile)
            deep_profile = service.registry.call(
                "sketcher.inspect_sketch",
                sketch_name="Sketch",
                include=["profile_deep"],
            )
            self.assertTrue(deep_profile["ok"], deep_profile)
            deep = deep_profile["profile_validation_deep"]
            self.assertTrue(deep["ready_for_pad"], deep)
            self.assertTrue(deep["feature_readiness"]["pad"], deep)
            self.assertNotIn(
                "no_faces",
                {item["kind"] for item in deep["feature_readiness"]["blockers"]},
            )
            self.assertFalse(slot["solver_status"]["conflicting_constraint_indices"], slot)
            self.assertFalse(slot["solver_status"]["redundant_constraint_indices"], slot)
            next_tools = {
                item.get("tool")
                for item in slot["next_actions"]
                if isinstance(item, dict)
            }
            self.assertIn('partdesign.extrude', next_tools)
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_slot_accepts_explicit_center_distance(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSlotCenterDistanceTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Slot Center Distance")
            self.assertTrue(sketch_result["ok"], sketch_result)
            slot = service.registry.call(
                "sketcher.add_slot",
                sketch_name="Sketch",
                center_x=0,
                center_y=0,
                center_distance=14,
                width=6,
                angle_degrees=0,
                construction=False,
            )
            self.assertTrue(slot["ok"], slot)
            slot_result = slot["transaction"]["result"]
            self.assertEqual(slot_result["overall_length"], 20.0)
            self.assertEqual(slot_result["center_distance"], 14.0)
            self.assertEqual(slot_result["bounding_box"]["width"], 20.0)

            missing_length = service.registry.call(
                "sketcher.add_slot",
                sketch_name="Sketch",
                center_x=40,
                center_y=0,
                width=6,
                angle_degrees=0,
                construction=False,
            )
            self.assertFalse(missing_length["ok"], missing_length)
            self.assertIn("overall_length or center_distance", missing_length["error"])

            old_alias = service.registry.call(
                "sketcher.add_slot",
                sketch_name="Sketch",
                center_x=40,
                center_y=0,
                length=14,
                width=6,
                angle_degrees=0,
                construction=False,
            )
            self.assertFalse(old_alias["ok"], old_alias)
            self.assertIn("Unsupported slot parameter", old_alias["error"])
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_slot_requires_explicit_target_orientation_and_construction(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSlotExplicitContractTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "partdesign.create_sketch",
                label="Slot Contract",
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = doc.getObject(sketch_result["active_sketch"])
            self.assertIsNotNone(sketch)
            before_geometry = len(getattr(sketch, "Geometry", []))

            missing_sketch = service.registry.call(
                "sketcher.add_slot",
                center_x=0,
                center_y=0,
                overall_length=20,
                width=6,
                angle_degrees=0,
                construction=False,
            )
            self.assertFalse(missing_sketch["ok"], missing_sketch)
            self.assertIn("sketch_name is required", missing_sketch["error"])
            self.assertFalse(missing_sketch.get("retry_same_call", True))

            missing_angle = service.registry.call(
                "sketcher.add_slot",
                sketch_name=sketch.Name,
                center_x=0,
                center_y=0,
                overall_length=20,
                width=6,
                construction=False,
            )
            self.assertFalse(missing_angle["ok"], missing_angle)
            self.assertIn("angle_degrees is required", missing_angle["error"])
            self.assertFalse(missing_angle.get("retry_same_call", True))

            missing_construction = service.registry.call(
                "sketcher.add_slot",
                sketch_name=sketch.Name,
                center_x=0,
                center_y=0,
                overall_length=20,
                width=6,
                angle_degrees=0,
            )
            self.assertFalse(missing_construction["ok"], missing_construction)
            self.assertIn("construction is required", missing_construction["error"])

            missing_length = service.registry.call(
                "sketcher.add_slot",
                sketch_name=sketch.Name,
                center_x=0,
                center_y=0,
                width=6,
                angle_degrees=0,
                construction=False,
            )
            self.assertFalse(missing_length["ok"], missing_length)
            self.assertIn("overall_length or center_distance", missing_length["error"])
            self.assertEqual(before_geometry, len(getattr(sketch, "Geometry", [])))

            spec = service.registry.get("sketcher.add_slot").to_schema()
            required = set(spec["parameters"]["required"])
            self.assertTrue(
                {
                    "sketch_name",
                    "center_x",
                    "center_y",
                    "width",
                    "angle_degrees",
                    "construction",
                }
                <= required
            )
            serialized = str(spec).lower()
            self.assertNotIn("default 0", serialized)
            self.assertNotIn("default false", serialized)
            self.assertNotIn("first sketch", serialized)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_tools_create_edit_and_delete_geometry(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADNativeSketchToolsTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Native Tool Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            point = service.registry.call('sketcher.add_geometry', kind='point', sketch_name=sketch.Name, points=[[1, 2]], construction=False)
            self.assertTrue(point["ok"], point)
            polyline = service.registry.call('sketcher.add_geometry', kind='polyline', sketch_name=sketch.Name, points=[[0, 0], [10, 0], [10, 5], [0, 5]], closed=True, constrain_points=True, construction=False)
            self.assertTrue(polyline["ok"], polyline)
            self.assertEqual(
                polyline["transaction"]["result"]["point_dimension_constraints_added"],
                8,
            )
            ellipse = service.registry.call('sketcher.add_geometry', kind='ellipse', sketch_name=sketch.Name, major_radius=6, minor_radius=3, angle_degrees=15, center=[20, 5], construction=False)
            self.assertTrue(ellipse["ok"], ellipse)
            bspline = service.registry.call('sketcher.add_geometry', kind='bspline', sketch_name=sketch.Name, points=[[0, 10], [5, 14], [10, 10]], interpolate=True, periodic=False, construction=False)
            self.assertTrue(bspline["ok"], bspline)

            construction = service.registry.call(
                "sketcher.set_construction",
                sketch_name=sketch.Name,
                geometry_index=0,
                construction=True,
            )
            self.assertTrue(construction["ok"], construction)
            self.assertTrue(construction["transaction"]["result"]["after"])
            self.assertTrue(construction["transaction"]["result"]["after_construction"])
            self.assertTrue(construction["transaction"]["result"]["geometry"]["construction"])
            self.assertEqual(
                construction["transaction"]["result"]["profile_effect"],
                "ignored_by_profile_validation",
            )
            self.assertEqual(construction["mutation"]["modified_geometry_indices"], [0])
            self.assertTrue(construction["sketcher"]["geometry"][0]["construction"])
            self.assertEqual(
                construction["profile_validation"]["construction_geometry_count"],
                1,
            )

            inspected = service.registry.call(
                "sketcher.inspect_sketch",
                sketch_name=sketch.Name,
                include=["geometry", "profile"],
            )
            self.assertTrue(inspected["geometry"][0]["construction"], inspected)
            self.assertEqual(
                inspected["profile_validation"]["construction_geometry_count"],
                1,
            )

            delete_constraint = service.registry.call('sketcher.delete_items', sketch_name=sketch.Name, constraint_items=[0])
            self.assertTrue(delete_constraint["ok"], delete_constraint)
            self.assertEqual(delete_constraint["mutation"]["deleted_constraint_indices"], [0])
            self.assertIn(
                "old_to_new_constraint_index",
                delete_constraint["transaction"]["result"],
            )
            delete_geometry = service.registry.call('sketcher.delete_items', sketch_name=sketch.Name, geometry_items=[0])
            self.assertTrue(delete_geometry["ok"], delete_geometry)
            self.assertEqual(delete_geometry["mutation"]["deleted_geometry_indices"], [0])
            self.assertIn(
                "old_to_new_geometry_index",
                delete_geometry["transaction"]["result"],
            )

            summary = service.sketcher_summary(sketch.Name)
            geometry_types = [item["type"] for item in summary["geometry"]]
            self.assertIn("LineSegment", geometry_types)
            self.assertIn("Ellipse", geometry_types)
            self.assertIn("BSplineCurve", geometry_types)
            spline_summary = [
                item for item in summary["geometry"] if item["type"] == "BSplineCurve"
            ][0]
            self.assertGreaterEqual(spline_summary["pole_count"], 3)
            self.assertIn("degree", spline_summary)
            self.assertIn("internal_degenerate_geometry_count", spline_summary)
            self.assertIn("internal_geometry", summary)
            self.assertIn("dependent_parameter_count", summary["internal_geometry"])

            bulk_constraints = service.registry.call('sketcher.delete_items', all_constraints=True, sketch_name=sketch.Name)
            self.assertTrue(bulk_constraints["ok"], bulk_constraints)
            self.assertEqual(
                bulk_constraints["mutation"]["deleted_constraint_indices"],
                bulk_constraints["transaction"]["result"]["deleted_constraint_indices"],
            )
            self.assertEqual(bulk_constraints["sketcher"]["constraint_count"], 0)

            bulk_geometry = service.registry.call('sketcher.delete_items', all_geometry=True, sketch_name=sketch.Name, delete_constraints_first=False)
            self.assertTrue(bulk_geometry["ok"], bulk_geometry)
            self.assertEqual(bulk_geometry["sketcher"]["geometry_count"], 0)
            self.assertEqual(bulk_geometry["sketcher"]["constraint_count"], 0)
            self.assertEqual(bulk_geometry["transaction"]["result"]["old_to_new_geometry_index"], {})
        finally:
            App.closeDocument(doc.Name)

    def test_slot_tool_returns_forward_actions_when_profile_is_fully_defined(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSlotConstraintTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Slot Probe")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            slot = service.registry.call(
                "sketcher.add_slot",
                sketch_name=sketch.Name,
                center_x=20,
                center_y=0,
                overall_length=14,
                width=4,
                angle_degrees=0,
                construction=False,
            )

            self.assertTrue(slot["ok"], slot)
            self.assertEqual(slot["profile_status"]["degrees_of_freedom"], 0)
            self.assertTrue(slot["profile_status"]["fully_constrained"])
            self.assertEqual(slot["solver_status"]["conflicting_constraint_indices"], [])
            self.assertEqual(slot["solver_repair_actions"], [])
            slot_next_tools = {
                item.get("tool")
                for item in slot["next_actions"]
                if isinstance(item, dict)
            }
            self.assertIn('partdesign.extrude', slot_next_tools)
            self.assertNotIn('sketcher.add_constraint', slot_next_tools)
            self.assertNotIn('sketcher.delete_items', slot_next_tools)
        finally:
            App.closeDocument(doc.Name)

    def test_polyline_points_create_solver_defined_profile(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPolylineConstraintTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Polyline Probe")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            polyline = service.registry.call('sketcher.add_geometry', kind='polyline', sketch_name=sketch.Name, points=[[0, 0], [10, 0], [10, 5], [0, 5]], closed=True, constrain_points=True, construction=False)

            self.assertTrue(polyline["ok"], polyline)
            self.assertEqual(polyline["profile_status"]["degrees_of_freedom"], 0)
            self.assertTrue(polyline["profile_status"]["fully_constrained"])
            self.assertEqual(polyline["transaction"]["result"]["constraints_added"], 12)
            self.assertEqual(
                polyline["transaction"]["result"]["point_dimension_constraints_added"],
                8,
            )
            self.assertEqual(polyline["solver_status"]["conflicting_constraint_indices"], [])
            self.assertEqual(polyline["solver_status"]["redundant_constraint_indices"], [])
            self.assertNotIn('sketcher.add_constraint', [action.get('tool') for action in polyline.get('next_actions', [])])
        finally:
            App.closeDocument(doc.Name)

    def test_typed_sketcher_constraint_and_move_tools_execute_natively(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADTypedSketchConstraintToolsTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Typed Constraint Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]

            lines = [
                service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[0, 0], [10, 0]], construction=False),
                service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[0, 5], [10, 5]], construction=False),
                service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[5, 0], [5, 5]], construction=False),
                service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[20, 5], [30, 5]], construction=False),
            ]
            for result in lines:
                self.assertTrue(result["ok"], result)
            circle = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=sketch.Name, radius=5, center=[25, 0], construction=False)
            self.assertTrue(circle["ok"], circle)

            transform = service.registry.call(
                "sketcher.transform_geometry",
                operation="translate",
                sketch_name=sketch.Name,
                geometry_indices=[0, 1],
                dx=2,
                dy=3,
            )
            self.assertTrue(transform["ok"], transform)
            self.assertEqual(transform["mutation"]["modified_geometry_indices"], [0, 1])
            self.assertAlmostEqual(sketch.Geometry[0].StartPoint.x, 2.0)
            self.assertAlmostEqual(sketch.Geometry[0].StartPoint.y, 3.0)
            self.assertAlmostEqual(sketch.Geometry[1].StartPoint.x, 2.0)
            self.assertAlmostEqual(sketch.Geometry[1].StartPoint.y, 8.0)

            copied = service.registry.call('sketcher.transform_geometry', operation='copy', sketch_name=sketch.Name, geometry_indices=[0, 1], dx=0, dy=10)
            self.assertTrue(copied["ok"], copied)
            self.assertEqual(copied["mutation"]["created_geometry_indices"], [5, 6])
            self.assertEqual(copied["transaction"]["result"]["source_geometry_indices"], [0, 1])
            self.assertAlmostEqual(sketch.Geometry[5].StartPoint.x, 2.0)
            self.assertAlmostEqual(sketch.Geometry[5].StartPoint.y, 13.0)
            self.assertAlmostEqual(sketch.Geometry[6].StartPoint.x, 2.0)
            self.assertAlmostEqual(sketch.Geometry[6].StartPoint.y, 18.0)

            array = service.registry.call('sketcher.transform_geometry', operation='array', sketch_name=sketch.Name, geometry_indices=[2], columns=2, rows=2, column_dx=10, column_dy=0, row_dx=0, row_dy=10)
            self.assertTrue(array["ok"], array)
            self.assertEqual(array["mutation"]["created_geometry_indices"], [7, 8, 9])
            self.assertEqual(array["transaction"]["result"]["source_geometry_indices"], [2])
            self.assertEqual(len(array["transaction"]["result"]["placements"]), 3)
            self.assertAlmostEqual(sketch.Geometry[7].StartPoint.x, 15.0)
            self.assertAlmostEqual(sketch.Geometry[7].StartPoint.y, 0.0)
            self.assertAlmostEqual(sketch.Geometry[8].StartPoint.x, 5.0)
            self.assertAlmostEqual(sketch.Geometry[8].StartPoint.y, 10.0)
            self.assertAlmostEqual(sketch.Geometry[9].StartPoint.x, 15.0)
            self.assertAlmostEqual(sketch.Geometry[9].StartPoint.y, 10.0)

            mirrored = service.registry.call('sketcher.transform_geometry', operation='mirror', sketch_name=sketch.Name, geometry_indices=[0], axis_point_x=0, axis_point_y=0, axis_direction_x=0, axis_direction_y=1)
            self.assertTrue(mirrored["ok"], mirrored)
            self.assertEqual(mirrored["mutation"]["created_geometry_indices"], [10])
            self.assertEqual(mirrored["transaction"]["result"]["source_geometry_indices"], [0])
            self.assertAlmostEqual(sketch.Geometry[10].StartPoint.x, -2.0)
            self.assertAlmostEqual(sketch.Geometry[10].StartPoint.y, 3.0)
            self.assertAlmostEqual(sketch.Geometry[10].EndPoint.x, -12.0)
            self.assertAlmostEqual(sketch.Geometry[10].EndPoint.y, 3.0)

            offset_line = service.registry.call('sketcher.transform_geometry', operation='offset', sketch_name=sketch.Name, geometry_indices=[0], distance=4, side='left')
            self.assertTrue(offset_line["ok"], offset_line)
            self.assertEqual(offset_line["mutation"]["created_geometry_indices"], [11])
            self.assertEqual(offset_line["transaction"]["result"]["source_geometry_indices"], [0])
            self.assertAlmostEqual(sketch.Geometry[11].StartPoint.x, 2.0)
            self.assertAlmostEqual(sketch.Geometry[11].StartPoint.y, 7.0)
            self.assertAlmostEqual(sketch.Geometry[11].EndPoint.x, 12.0)
            self.assertAlmostEqual(sketch.Geometry[11].EndPoint.y, 7.0)

            offset_circle = service.registry.call('sketcher.transform_geometry', operation='offset', sketch_name=sketch.Name, geometry_indices=[4], distance=2, side='outward')
            self.assertTrue(offset_circle["ok"], offset_circle)
            self.assertEqual(offset_circle["mutation"]["created_geometry_indices"], [12])
            self.assertAlmostEqual(sketch.Geometry[12].Radius, 7.0)

            tool_calls = [
                ("Parallel", {"first_geometry": 0, "second_geometry": 1}),
                ("Perpendicular", {"first_geometry": 0, "second_geometry": 2}),
                ("Equal", {"first_geometry": 0, "second_geometry": 1}),
                ("DistanceX", {"first_geometry": 0, "first_point": "start", "second_geometry": 0, "second_point": "end", "value": 10}),
                ("DistanceY", {"first_geometry": 2, "first_point": "start", "second_geometry": 2, "second_point": "end", "value": 5}),
                ("Diameter", {"first_geometry": 4, "value": 10}),
                ("Tangent", {"first_geometry": 3, "second_geometry": 4}),
                ("PointOnObject", {"first_geometry": 0, "first_point": "start", "second_geometry": 2}),
            ]
            for constraint_type, kwargs in tool_calls:
                result = service.registry.call(
                    "sketcher.add_constraint",
                    sketch_name=sketch.Name,
                    constraint_type=constraint_type,
                    **kwargs,
                )
                self.assertTrue(result["ok"], (constraint_type, result))

            move = service.registry.call(
                "sketcher.move_point",
                sketch_name=sketch.Name,
                geometry_index=3,
                point="end",
                x=32,
                y=5,
            )
            self.assertTrue(move["ok"], move)
            self.assertEqual(move["transaction"]["result"]["point"], "end")
            self.assertEqual(move["mutation"]["modified_geometry_indices"], [3])

            summary = service.sketcher_summary(sketch.Name)
            constraint_types = [item["type"] for item in summary["constraints"]]
            for constraint_type in (
                "Parallel",
                "Perpendicular",
                "Equal",
                "DistanceX",
                "DistanceY",
                "Diameter",
                "Tangent",
                "PointOnObject",
            ):
                self.assertIn(constraint_type, constraint_types)
        finally:
            App.closeDocument(doc.Name)

    def test_native_sketcher_edit_tools_trim_extend_split_and_fillet(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADNativeSketchEditToolsTest")
        try:
            service = VibeCADService()

            extend_sketch = service.registry.call("partdesign.create_sketch", label="Extend Sketch")
            self.assertTrue(extend_sketch["ok"], extend_sketch)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            line = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[0, 0], [10, 0]], construction=False)
            self.assertTrue(line["ok"], line)
            extend = service.registry.call('sketcher.modify_geometry', operation='extend', sketch_name=sketch.Name, geometry_index=0, endpoint='end', increment=5)
            self.assertTrue(extend["ok"], extend)
            self.assertEqual(extend["mutation"]["modified_geometry_indices"], [0])
            self.assertAlmostEqual(sketch.Geometry[0].EndPoint.x, 15.0)

            split_sketch = service.registry.call(
                "sketcher.create_sketch",
                label="Split Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(split_sketch["ok"], split_sketch)
            split_name = split_sketch["active_sketch"]
            split_line = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=split_name, points=[[0, 0], [10, 0]], construction=False)
            self.assertTrue(split_line["ok"], split_line)
            split = service.registry.call('sketcher.modify_geometry', operation='split', sketch_name=split_name, geometry_index=0, x=5, y=0)
            self.assertTrue(split["ok"], split)
            self.assertGreaterEqual(split["mutation"]["geometry_count"], 2)

            trim_sketch = service.registry.call(
                "sketcher.create_sketch",
                label="Trim Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(trim_sketch["ok"], trim_sketch)
            trim_name = trim_sketch["active_sketch"]
            circle = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=trim_name, radius=5, center=[0, 0], construction=False)
            self.assertTrue(circle["ok"], circle)
            trim = service.registry.call('sketcher.modify_geometry', operation='trim', sketch_name=trim_name, geometry_index=0, x=5, y=0)
            self.assertTrue(trim["ok"], trim)
            self.assertEqual(trim["transaction"]["result"]["geometry_count_before"], 1)
            self.assertLessEqual(trim["mutation"]["geometry_count"], 1)

            fillet_sketch = service.registry.call(
                "sketcher.create_sketch",
                label="Fillet Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(fillet_sketch["ok"], fillet_sketch)
            fillet_name = fillet_sketch["active_sketch"]
            first = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=fillet_name, points=[[0, 0], [10, 0]], construction=False)
            second = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=fillet_name, points=[[10, 0], [10, 10]], construction=False)
            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            coincident = service.registry.call('sketcher.add_constraint', constraint_type='Coincident', sketch_name=fillet_name, first_geometry=0, first_point='end', second_geometry=1, second_point='start')
            self.assertTrue(coincident["ok"], coincident)
            fillet = service.registry.call('sketcher.modify_geometry', operation='fillet', sketch_name=fillet_name, first_geometry=0, first_point='end', radius=2, trim=True, preserve_corner=True)
            self.assertTrue(fillet["ok"], fillet)
            self.assertGreaterEqual(len(fillet["mutation"]["created_geometry_indices"]), 1)
            fillet_summary = service.sketcher_summary(fillet_name)
            self.assertIn("ArcOfCircle", [item["type"] for item in fillet_summary["geometry"]])
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_add_geometry_requires_canonical_sketch_points(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchCanonicalPointTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "sketcher.create_sketch",
                label="Canonical Point Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch_name = sketch_result["active_sketch"]

            before_geometry = len(getattr(doc.getObject(sketch_name), "Geometry", []))
            missing_sketch = service.registry.call(
                "sketcher.add_geometry",
                kind="line",
                points=[[0, 0], [10, 0]],
                construction=False,
            )
            self.assertFalse(missing_sketch["ok"], missing_sketch)
            self.assertIn("sketch_name is required", missing_sketch["error"])
            self.assertFalse(missing_sketch.get("retry_same_call", True))

            missing_construction = service.registry.call(
                "sketcher.add_geometry",
                kind="line",
                sketch_name=sketch_name,
                points=[[0, 0], [10, 0]],
            )
            self.assertFalse(missing_construction["ok"], missing_construction)
            self.assertIn("construction is required", missing_construction["error"])
            self.assertFalse(missing_construction.get("retry_same_call", True))
            self.assertEqual(before_geometry, len(getattr(doc.getObject(sketch_name), "Geometry", [])))

            line = service.registry.call(
                "sketcher.add_geometry",
                kind="line",
                sketch_name=sketch_name,
                points=[[0, 0], [10, 0]],
                construction=False,
            )
            self.assertTrue(line["ok"], line)
            self.assertEqual(line["transaction"]["result"]["start"], [0.0, 0.0])
            self.assertEqual(line["transaction"]["result"]["end"], [10.0, 0.0])

            circle = service.registry.call(
                "sketcher.add_geometry",
                kind="circle",
                sketch_name=sketch_name,
                center=[5, 2],
                radius=2,
                construction=False,
            )
            self.assertTrue(circle["ok"], circle)
            self.assertEqual(circle["transaction"]["result"]["center"], [5.0, 2.0])

            three_d_line = service.registry.call(
                "sketcher.add_geometry",
                kind="line",
                sketch_name=sketch_name,
                points=[[0, 0, 0], [10, 0, 0]],
                construction=False,
            )
            self.assertFalse(three_d_line["ok"], three_d_line)
            self.assertIn("exactly [x, y]", three_d_line["error"])

            object_center = service.registry.call(
                "sketcher.add_geometry",
                kind="circle",
                sketch_name=sketch_name,
                center={"x": 5, "y": 2},
                radius=2,
                construction=False,
            )
            self.assertFalse(object_center["ok"], object_center)
            self.assertIn("center=[x, y]", object_center["error"])
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_add_constraint_rejects_point_role_aliases(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchPointRoleStrictTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "sketcher.create_sketch",
                label="Point Role Alias Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch_name = sketch_result["active_sketch"]
            properties = service.registry.get("sketcher.add_constraint").to_schema()[
                "parameters"
            ]["properties"]
            required = set(
                service.registry.get("sketcher.add_constraint").to_schema()[
                    "parameters"
                ]["required"]
            )
            self.assertTrue({"sketch_name", "constraint_type"} <= required)
            self.assertNotIn("first_pos", properties)
            self.assertNotIn("second_pos", properties)
            self.assertNotIn("third_pos", properties)

            circle = service.registry.call(
                "sketcher.add_geometry",
                kind="circle",
                sketch_name=sketch_name,
                center=[5, 2],
                radius=2,
                construction=False,
            )
            self.assertTrue(circle["ok"], circle)
            origin = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=sketch_name,
                geometry_handle="origin",
            )
            self.assertTrue(origin["ok"], origin)
            self.assertEqual(origin["geometry_index"], -1)
            self.assertIsNone(origin["geometry"])
            horizontal_axis = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=sketch_name,
                geometry_handle="axis:H",
            )
            self.assertTrue(horizontal_axis["ok"], horizontal_axis)
            self.assertEqual(horizontal_axis["geometry_index"], -1)
            old_origin_alias = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=sketch_name,
                geometry_handle="rootpoint",
            )
            self.assertFalse(old_origin_alias["ok"], old_origin_alias)
            old_axis_alias = service.registry.call(
                "sketcher.resolve_geometry",
                sketch_name=sketch_name,
                geometry_handle="horizontal_axis",
            )
            self.assertFalse(old_axis_alias["ok"], old_axis_alias)
            missing_sketch = service.registry.call(
                "sketcher.add_constraint",
                constraint_type="Lock",
                first_geometry=0,
                first_point="center",
                x=5,
                y=2,
            )
            self.assertFalse(missing_sketch["ok"], missing_sketch)
            self.assertIn("sketch_name is required", missing_sketch["error"])
            self.assertFalse(missing_sketch.get("retry_same_call", True))
            missing_point = service.registry.call(
                "sketcher.add_constraint",
                constraint_type="Lock",
                sketch_name=sketch_name,
                first_geometry=0,
                x=5,
                y=2,
            )
            self.assertFalse(missing_point["ok"], missing_point)
            self.assertIn("requires explicit point role", missing_point["error"])
            self.assertFalse(missing_point.get("retry_same_call", True))
            lock = service.registry.call(
                "sketcher.add_constraint",
                constraint_type="Lock",
                sketch_name=sketch_name,
                first_geometry=0,
                first_point="point",
                x=5,
                y=2,
            )
            self.assertFalse(lock["ok"], lock)
            self.assertIn("point role must be one of", lock["error"])
            self.assertNotIn("aliases", lock["error"].lower())

            canonical_lock = service.registry.call(
                "sketcher.add_constraint",
                constraint_type="Lock",
                sketch_name=sketch_name,
                first_geometry=0,
                first_point="center",
                x=5,
                y=2,
            )
            self.assertTrue(canonical_lock["ok"], canonical_lock)
            self.assertEqual(
                canonical_lock["transaction"]["result"]["constraints_added"],
                2,
            )
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_modify_geometry_infers_fillet_reference_points(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchFilletInferenceTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "sketcher.create_sketch",
                label="Fillet Inference Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch_name = sketch_result["active_sketch"]

            first = service.registry.call(
                "sketcher.add_geometry",
                kind="line",
                sketch_name=sketch_name,
                points=[[0, 0], [10, 0]],
                construction=False,
            )
            second = service.registry.call(
                "sketcher.add_geometry",
                kind="line",
                sketch_name=sketch_name,
                points=[[10, 0], [10, 10]],
                construction=False,
            )
            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            fillet = service.registry.call(
                "sketcher.modify_geometry",
                operation="fillet",
                sketch_name=sketch_name,
                first_geometry=0,
                second_geometry=1,
                radius=2,
            )
            self.assertTrue(fillet["ok"], fillet)
            self.assertEqual(
                fillet["transaction"]["result"]["reference_mode"],
                "inferred_shared_endpoint",
            )
            self.assertGreaterEqual(len(fillet["mutation"]["created_geometry_indices"]), 1)
        finally:
            App.closeDocument(doc.Name)

    def test_provider_schema_keeps_sketcher_point_role_guidance(self):
        from provider_tools.base import tool_json_schema

        def non_null(schema):
            variants = schema.get("anyOf")
            if isinstance(variants, list):
                for variant in variants:
                    if isinstance(variant, dict) and variant.get("type") != "null":
                        return variant
            return schema

        service = VibeCADService()
        schema = tool_json_schema(service.registry.get("sketcher.add_constraint").to_schema())
        self.assertIs(schema["additionalProperties"], False)
        self.assertIn("first_geometry_handle", schema["properties"])
        self.assertIn("second_geometry_handle", schema["properties"])
        self.assertIn("third_geometry_handle", schema["properties"])
        self.assertNotIn("first_pos", schema["properties"])
        self.assertNotIn("second_pos", schema["properties"])
        self.assertNotIn("third_pos", schema["properties"])
        first_point = non_null(schema["properties"]["first_point"])
        self.assertIn("enum", first_point)
        self.assertIn("center", first_point["enum"])
        self.assertIn("start", first_point["enum"])

        move_schema = tool_json_schema(
            service.registry.get("sketcher.move_point").to_schema()
        )
        self.assertIn("geometry_handle", move_schema["properties"])
        self.assertIn("geometry_index", move_schema["properties"])

        construction_schema = tool_json_schema(
            service.registry.get("sketcher.set_construction").to_schema()
        )
        self.assertIn("geometry_handle", construction_schema["properties"])
        self.assertIn("geometry_index", construction_schema["properties"])

        name_schema = tool_json_schema(
            service.registry.get("sketcher.set_geometry_name").to_schema()
        )
        self.assertIn("geometry_handle", name_schema["properties"])
        self.assertIn("geometry_index", name_schema["properties"])

        modify_schema = tool_json_schema(
            service.registry.get("sketcher.modify_geometry").to_schema()
        )
        self.assertIn("geometry_handle", modify_schema["properties"])
        self.assertIn("first_geometry_handle", modify_schema["properties"])
        self.assertIn("second_geometry_handle", modify_schema["properties"])
        self.assertIn("trim", modify_schema["properties"])
        self.assertIn("preserve_corner", modify_schema["properties"])

        transform_schema = tool_json_schema(
            service.registry.get("sketcher.transform_geometry").to_schema()
        )
        self.assertIn("geometry_handles", transform_schema["properties"])
        self.assertIn("geometry_indices", transform_schema["properties"])
        self.assertIn("construction", transform_schema["properties"])
        self.assertIn("include_original", transform_schema["properties"])

        draw_schema = tool_json_schema(service.registry.get("sketcher.add_geometry").to_schema())
        self.assertIs(draw_schema["additionalProperties"], False)
        self.assertTrue(
            {"sketch_name", "kind", "construction"}
            <= set(draw_schema.get("required", []))
        )
        points = non_null(draw_schema["properties"]["points"])
        self.assertEqual(points["type"], "array")
        self.assertEqual(points["items"]["type"], "array")
        self.assertEqual(points["items"]["minItems"], 2)
        self.assertEqual(points["items"]["maxItems"], 2)
        self.assertEqual(points["items"]["items"]["type"], "number")
        center = non_null(draw_schema["properties"]["center"])
        self.assertEqual(center["type"], "array")
        self.assertEqual(center["minItems"], 2)
        self.assertEqual(center["maxItems"], 2)
        self.assertEqual(center["items"]["type"], "number")

    def test_native_sketcher_external_geometry_tools_add_list_and_remove(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADSketchExternalGeometryToolsTest")
        try:
            service = VibeCADService()
            box = doc.addObject("Part::Box", "ReferenceBox")
            box.Length = 10
            box.Width = 8
            box.Height = 4
            doc.recompute()

            sketch_result = service.registry.call(
                "sketcher.create_sketch",
                label="External Reference Sketch",
                support_type="origin_plane",
                plane="XY_Plane",
                open_for_edit=False,
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch_name = sketch_result["active_sketch"]

            references = service.registry.call('sketcher.inspect_sketch', include=['reference_geometry'], max_references=30, reference_object_name=box.Name)
            self.assertTrue(references["ok"], references)
            reference_names = {
                ref["subelement"]
                for obj in references["reference_geometry"]["objects"]
                for ref in obj["references"]
            }
            self.assertIn("Edge1", reference_names)
            self.assertIn("Face1", reference_names)

            add_external = service.registry.call(
                "sketcher.add_external_geometry",
                sketch_name=sketch_name,
                source_object=box.Name,
                subelement="Edge1",
            )
            self.assertTrue(add_external["ok"], add_external)
            self.assertEqual(add_external["transaction"]["result"]["external_geometry_index"], 0)
            self.assertEqual(add_external["transaction"]["result"]["external_geometry_id"], -1)

            external = service.registry.call('sketcher.inspect_sketch', include=['external_geometry'], sketch_name=sketch_name)
            self.assertTrue(external["ok"], external)
            self.assertEqual(external["external_geometry_count"], 1)
            self.assertEqual(external["external_geometry"][0]["source_object"], box.Name)
            self.assertEqual(external["external_geometry"][0]["subelements"], ["Edge1"])

            remove = service.registry.call(
                "sketcher.remove_external_geometry",
                sketch_name=sketch_name,
                external_geometry_index=0,
            )
            self.assertTrue(remove["ok"], remove)
            self.assertEqual(remove["transaction"]["result"]["deleted_external_geometry_index"], 0)
            external_after = service.registry.call('sketcher.inspect_sketch', include=['external_geometry'], sketch_name=sketch_name)
            self.assertTrue(external_after["ok"], external_after)
            self.assertEqual(external_after["external_geometry_count"], 0)
        finally:
            App.closeDocument(doc.Name)

    def test_sketcher_tool_descriptions_distinguish_trios_and_state_units(self):
        service = VibeCADService()

        def _description(tool_name):
            return str(service.registry.get(tool_name).to_schema().get("description", "")).lower()

        def _param_descriptions(tool_name):
            schema = service.registry.get(tool_name).to_schema()
            properties = schema.get("parameters", {}).get("properties", {})
            return {name: str(spec.get("description", "")) for name, spec in properties.items()}

        # Constraint trio: each tool names its sibling alternatives.
        add_constraint = _description("sketcher.add_constraint")
        self.assertIn("sketcher.edit_constraint", add_constraint)
        self.assertIn("sketcher.delete_items", add_constraint)
        edit_constraint = _description("sketcher.edit_constraint")
        self.assertIn("sketcher.add_constraint", edit_constraint)
        self.assertIn("sketcher.delete_items", edit_constraint)
        delete_items = _description("sketcher.delete_items")
        self.assertIn("sketcher.edit_constraint", delete_items)

        # Geometry-edit trio: each tool names its sibling alternatives.
        modify = _description("sketcher.modify_geometry")
        self.assertIn("sketcher.transform_geometry", modify)
        self.assertIn("sketcher.move_point", modify)
        transform = _description("sketcher.transform_geometry")
        self.assertIn("sketcher.modify_geometry", transform)
        self.assertIn("sketcher.move_point", transform)
        move_point = _description("sketcher.move_point")
        self.assertIn("sketcher.transform_geometry", move_point)
        self.assertIn("sketcher.modify_geometry", move_point)

        # Numeric parameter descriptions state units (mm / degrees).
        for tool_name, param_name in (
            ("sketcher.add_geometry", "radius"),
            ("sketcher.add_geometry", "start_angle_degrees"),
            ("sketcher.transform_geometry", "dx"),
            ("sketcher.transform_geometry", "distance"),
            ("sketcher.modify_geometry", "increment"),
            ("sketcher.modify_geometry", "radius"),
            ("sketcher.move_point", "x"),
            ("sketcher.add_slot", "width"),
            ("sketcher.add_slot", "overall_length"),
            ("sketcher.add_hole_pattern", "hole_diameter"),
            ("sketcher.add_hole_pattern", "spacing_x"),
            ("sketcher.draw_rectangle", "width"),
            ("sketcher.add_constraint", "value"),
            ("sketcher.edit_constraint", "value"),
            ("sketcher.inspect_sketch", "tolerance"),
        ):
            description = _param_descriptions(tool_name).get(param_name, "")
            lowered = description.lower()
            self.assertTrue(
                ("mm" in lowered) or ("millimeter" in lowered) or ("degree" in lowered),
                f"{tool_name}.{param_name} lacks units: {description!r}",
            )

        # Stale vague phrasing is gone from every sketcher tool description and param.
        sketcher_tools = [name for name in service.registry.names() if name.startswith("sketcher.")]
        self.assertGreater(len(sketcher_tools), 10)
        for tool_name in sketcher_tools:
            description = str(service.registry.get(tool_name).to_schema().get("description", ""))
            self.assertNotIn("XY plane/origin", description, tool_name)
            for param_name, param_description in _param_descriptions(tool_name).items():
                self.assertNotIn("sketch units", param_description, f"{tool_name}.{param_name}")
                self.assertNotIn("sketch space", param_description, f"{tool_name}.{param_name}")
