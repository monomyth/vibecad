# SPDX-License-Identifier: LGPL-2.1-or-later

import math

from VibeCADCore import (
    VibeCADService,
)

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
)


class TestVibeCADPartDesignAssembly(SettingsSnapshotTestCase):
    def test_partdesign_summary_reads_real_body_and_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignSummaryTest")
        try:
            body = doc.addObject("PartDesign::Body", "BodyForSummary")
            body.Label = "Readable Body"
            feature = body.newObject("PartDesign::AdditiveBox", "BoxForSummary")
            feature.Label = "Readable Additive Box"
            doc.recompute()
            service = VibeCADService()
            summary = service.partdesign_summary(body.Name)
            self.assertEqual(summary["body_count"], 1)
            self.assertEqual(summary["selected"]["name"], body.Name)
            self.assertEqual(summary["selected"]["label"], "Readable Body")
            self.assertEqual(summary["selected"]["feature_count"], 1)
            self.assertEqual(summary["selected"]["features"][0]["name"], feature.Name)
            self.assertEqual(summary["selected"]["tip"]["name"], feature.Name)
        finally:
            App.closeDocument(doc.Name)

    def test_create_partdesign_sketch_uses_default_xy_plane_without_picker(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignDefaultSketchTest")
        try:
            service = VibeCADService()
            result = service.registry.call("partdesign.create_sketch", label="AI Sketch")
            self.assertTrue(result["ok"], result)
            bodies = [obj for obj in doc.Objects if obj.TypeId == "PartDesign::Body"]
            sketches = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"]
            self.assertEqual(len(bodies), 1)
            self.assertEqual(len(sketches), 1)
            self.assertEqual(sketches[0].Label, "AI Sketch")
            self.assertEqual(getattr(sketches[0], "MapMode", ""), "FlatFace")
            support = list(getattr(sketches[0], "AttachmentSupport", []) or [])
            self.assertTrue(support)
            self.assertEqual(getattr(support[0][0], "Name", ""), "XY_Plane")
            transaction_result = result["transaction"]["result"]
            self.assertEqual(transaction_result["plane"], "XY_Plane")
            self.assertEqual(transaction_result["sketch"], sketches[0].Name)
        finally:
            App.closeDocument(doc.Name)

    def test_create_partdesign_sketch_attaches_to_offset_datum_plane(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignDatumSketchTest")
        try:
            service = VibeCADService()
            body_result = service.registry.call("partdesign.create_body", label="Datum Body")
            self.assertTrue(body_result["ok"], body_result)
            body = doc.getObject(body_result["active_body"])
            self.assertIsNotNone(body)

            datum_result = service.registry.call(
                "partdesign.create_datum_plane",
                label="Section Plane",
                support_plane="XY_Plane",
                body_name=body.Name,
            )
            self.assertTrue(datum_result["ok"], datum_result)
            datum = doc.getObject(datum_result["datum"])
            self.assertIsNotNone(datum)
            datum.AttachmentOffset = App.Placement(
                App.Vector(0, 0, 25), App.Rotation()
            )
            doc.recompute()

            sketch_result = service.registry.call(
                "partdesign.create_sketch",
                label="Datum Section Sketch",
                support_type="datum_plane",
                support_object=datum.Name,
                body_name=body.Name,
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = doc.getObject(sketch_result["active_sketch"])
            self.assertIsNotNone(sketch)
            support = list(getattr(sketch, "AttachmentSupport", []) or [])
            self.assertTrue(support)
            self.assertEqual(getattr(support[0][0], "Name", ""), datum.Name)
            self.assertIn(sketch, list(getattr(body, "Group", []) or []))
            self.assertAlmostEqual(sketch.Placement.Base.z, 25.0, places=5)
            transaction_result = sketch_result["transaction"]["result"]
            self.assertEqual(transaction_result["support_type"], "datum_plane")
            self.assertEqual(transaction_result["attachment_support"], datum.Name)

            missing = service.registry.call(
                "partdesign.create_sketch",
                label="Bad Sketch",
                support_type="datum_plane",
            )
            self.assertFalse(missing["ok"], missing)
            self.assertIn("support_object", missing["error"])
        finally:
            App.closeDocument(doc.Name)

    def test_create_datum_plane_and_line_support_offset_and_rotation(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADDatumOffsetTest")
        try:
            service = VibeCADService()
            body_result = service.registry.call("partdesign.create_body", label="Offset Datum Body")
            self.assertTrue(body_result["ok"], body_result)
            body = doc.getObject(body_result["active_body"])
            self.assertIsNotNone(body)

            plane_result = service.registry.call(
                "partdesign.create_datum_plane",
                label="Offset Section Plane",
                support_plane="XY_Plane",
                body_name=body.Name,
                offset_z=25.0,
            )
            self.assertTrue(plane_result["ok"], plane_result)
            plane = doc.getObject(plane_result["datum"])
            self.assertIsNotNone(plane)
            self.assertAlmostEqual(plane.Placement.Base.z, 25.0, places=5)
            plane_txn = plane_result["transaction"]["result"]
            self.assertAlmostEqual(plane_txn["placement"]["base"][2], 25.0, places=5)
            self.assertAlmostEqual(plane_txn["offset"]["z"], 25.0, places=5)

            line_result = service.registry.call(
                "partdesign.create_datum_line",
                label="Offset Axis",
                support_axis="Z_Axis",
                body_name=body.Name,
                offset_x=10.0,
                offset_y=-5.0,
            )
            self.assertTrue(line_result["ok"], line_result)
            line = doc.getObject(line_result["datum"])
            self.assertIsNotNone(line)
            self.assertAlmostEqual(line.Placement.Base.x, 10.0, places=5)
            self.assertAlmostEqual(line.Placement.Base.y, -5.0, places=5)
            line_txn = line_result["transaction"]["result"]
            self.assertAlmostEqual(line_txn["placement"]["base"][0], 10.0, places=5)
            self.assertAlmostEqual(line_txn["placement"]["base"][1], -5.0, places=5)

            default_plane_result = service.registry.call(
                "partdesign.create_datum_plane",
                label="Default Plane",
                support_plane="XY_Plane",
                body_name=body.Name,
            )
            self.assertTrue(default_plane_result["ok"], default_plane_result)
            default_plane = doc.getObject(default_plane_result["datum"])
            self.assertIsNotNone(default_plane)
            self.assertAlmostEqual(default_plane.Placement.Base.x, 0.0, places=7)
            self.assertAlmostEqual(default_plane.Placement.Base.y, 0.0, places=7)
            self.assertAlmostEqual(default_plane.Placement.Base.z, 0.0, places=7)
            self.assertTrue(default_plane.Placement.Rotation.isIdentity())

            bad_axis = service.registry.call(
                "partdesign.create_datum_plane",
                label="Bad Axis Plane",
                rotation_axis="w",
            )
            self.assertFalse(bad_axis["ok"], bad_axis)
            self.assertIn("rotation_axis", bad_axis["error"])
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_pad_sketch_and_feature_dimension_edit_work_in_place(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignPadEditTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Pad Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            draw_result = service.registry.call("sketcher.draw_rectangle",
                width=10,
                height=10,
                sketch_name=sketch.Name,
            )
            self.assertTrue(draw_result["ok"], draw_result)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=sketch.Name, label='Editable Pad', length=7)
            self.assertTrue(pad_result["ok"], pad_result)
            self.assertTrue(pad_result["feature_effect"]["ok"], pad_result)
            self.assertGreater(
                pad_result["feature_effect"]["body_shape_delta"]["volume_delta"],
                0.0,
                pad_result,
            )
            self.assertGreater(pad_result["feature_shape"]["faces"], 0, pad_result)
            pad_name = pad_result["transaction"]["result"]["feature"]
            pad = doc.getObject(pad_name)
            self.assertIsNotNone(pad)
            self.assertEqual(pad.TypeId, "PartDesign::Pad")
            self.assertAlmostEqual(float(pad.Length), 7.0)
            edit_result = service.registry.call("partdesign.set_feature_dimensions",
                feature_name=pad.Name,
                length=12,
            )
            self.assertTrue(edit_result["ok"], edit_result)
            self.assertIs(doc.getObject(pad_name), pad)
            self.assertAlmostEqual(float(pad.Length), 12.0)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_create_body_allows_separate_component_sketches(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignCreateBodyTest")
        try:
            service = VibeCADService()
            first = service.registry.call("partdesign.create_body", label="Base Component")
            self.assertTrue(first["ok"], first)
            second = service.registry.call("partdesign.create_body", label="Arm Link Component")
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["active_body"], second["active_body"])

            sketch_result = service.registry.call(
                "partdesign.create_sketch",
                body_name=second["active_body"],
                label="Arm Link Sketch",
                plane="XZ_Plane",
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch_name = sketch_result["active_sketch"]
            second_body = doc.getObject(second["active_body"])
            self.assertIsNotNone(second_body)
            self.assertIn(doc.getObject(sketch_name), list(getattr(second_body, "Group", []) or []))

            label_targeted_sketch = service.registry.call(
                "partdesign.create_sketch",
                body_name=second["active_body_label"],
                label="Arm Link Label Targeted Sketch",
                plane="XY_Plane",
            )
            self.assertTrue(label_targeted_sketch["ok"], label_targeted_sketch)
            label_targeted_name = label_targeted_sketch["active_sketch"]
            self.assertIn(
                doc.getObject(label_targeted_name),
                list(getattr(second_body, "Group", []) or []),
            )
            self.assertEqual(
                label_targeted_sketch["transaction"]["result"]["body"],
                second["active_body"],
            )

            summary = service.partdesign_summary()
            self.assertEqual(summary["body_count"], 2)
            labels = {body["label"] for body in summary["bodies"]}
            self.assertIn("Base Component", labels)
            self.assertIn("Arm Link Component", labels)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_pad_rejects_open_sketch_with_recoverable_next_actions(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignOpenSketchTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Open Pad Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            line_result = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=sketch.Name, points=[[0, 0], [10, 0]])
            self.assertTrue(line_result["ok"], line_result)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=sketch.Name, label='Should Not Pad', length=5)
            self.assertFalse(pad_result["ok"], pad_result)
            self.assertTrue(pad_result["recoverable"], pad_result)
            self.assertFalse(pad_result["profile_status"]["closed_profile"], pad_result)
            self.assertIn("closed profile", pad_result["error"])
            self.assertIn('sketcher.add_geometry', {item['tool'] for item in pad_result['next_actions']})
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_pocket_sketch_creates_native_subtractive_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignPocketTest")
        try:
            service = VibeCADService()
            base_sketch_result = service.registry.call("partdesign.create_sketch", label="Base Pad Sketch")
            self.assertTrue(base_sketch_result["ok"], base_sketch_result)
            base_sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            base_draw = service.registry.call("sketcher.draw_rectangle",
                width=30,
                height=20,
                sketch_name=base_sketch.Name,
            )
            self.assertTrue(base_draw["ok"], base_draw)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=base_sketch.Name, label='Pocket Base Pad', length=10)
            self.assertTrue(pad_result["ok"], pad_result)

            pocket_sketch_result = service.registry.call("partdesign.create_sketch", label="Pocket Sketch")
            self.assertTrue(pocket_sketch_result["ok"], pocket_sketch_result)
            pocket_sketches = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Label == "Pocket Sketch"
            ]
            self.assertEqual(len(pocket_sketches), 1)
            pocket_sketch = pocket_sketches[0]
            pocket_draw = service.registry.call("sketcher.draw_rectangle",
                width=8,
                height=6,
                sketch_name=pocket_sketch.Name,
            )
            self.assertTrue(pocket_draw["ok"], pocket_draw)
            pocket_result = service.registry.call('partdesign.extrude', operation='pocket', sketch_name=pocket_sketch.Name, label='Cable Recess Pocket', length=3, reversed=True)
            self.assertTrue(pocket_result["ok"], pocket_result)
            self.assertTrue(pocket_result["feature_effect"]["ok"], pocket_result)
            self.assertLess(
                pocket_result["feature_effect"]["body_shape_delta"]["volume_delta"],
                0.0,
                pocket_result,
            )
            self.assertGreater(pocket_result["feature_shape"]["faces"], 0, pocket_result)
            pocket_name = pocket_result["transaction"]["result"]["feature"]
            pocket = doc.getObject(pocket_name)
            self.assertIsNotNone(pocket)
            self.assertEqual(pocket.TypeId, "PartDesign::Pocket")
            self.assertAlmostEqual(float(pocket.Length), 3.0)
            body = [obj for obj in doc.Objects if obj.TypeId == "PartDesign::Body"][0]
            self.assertIs(getattr(body, "Tip", None), pocket)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_hole_from_sketch_creates_native_hole_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignHoleTest")
        try:
            service = VibeCADService()
            base_sketch_result = service.registry.call("partdesign.create_sketch", label="Hole Base Sketch")
            self.assertTrue(base_sketch_result["ok"], base_sketch_result)
            base_sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            base_draw = service.registry.call(
                "sketcher.draw_rectangle",
                width=30,
                height=20,
                sketch_name=base_sketch.Name,
            )
            self.assertTrue(base_draw["ok"], base_draw)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=base_sketch.Name, label='Hole Base Pad', length=10)
            self.assertTrue(pad_result["ok"], pad_result)

            hole_sketch_result = service.registry.call(
                "partdesign.create_sketch",
                label="Bolt Hole Sketch",
            )
            self.assertTrue(hole_sketch_result["ok"], hole_sketch_result)
            hole_sketch = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Label == "Bolt Hole Sketch"
            ][0]
            circle = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=hole_sketch.Name, radius=2, center=[0, 0])
            self.assertTrue(circle["ok"], circle)
            hole_result = service.registry.call(
                "partdesign.hole_from_sketch",
                sketch_name=hole_sketch.Name,
                label="Native Bolt Hole",
                diameter=6,
                depth=10,
                depth_type=0,
                hole_cut_type=1,
                hole_cut_diameter=9,
                hole_cut_depth=3,
                sketch_map_reversed=True,
            )
            self.assertTrue(hole_result["ok"], hole_result)
            self.assertTrue(hole_result["feature_effect"]["ok"], hole_result)
            self.assertLess(
                hole_result["feature_effect"]["body_shape_delta"]["volume_delta"],
                0.0,
                hole_result,
            )
            hole_name = hole_result["transaction"]["result"]["feature"]
            hole = doc.getObject(hole_name)
            self.assertIsNotNone(hole)
            self.assertEqual(hole.TypeId, "PartDesign::Hole")
            self.assertAlmostEqual(float(hole.Diameter), 6.0)
            self.assertIn(str(hole_result["transaction"]["result"]["hole_cut_type"]), {"1", "Counterbore"})
            self.assertAlmostEqual(float(hole.HoleCutDiameter), 9.0)
            body = [obj for obj in doc.Objects if obj.TypeId == "PartDesign::Body"][0]
            self.assertIs(getattr(body, "Tip", None), hole)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_pocket_reports_no_effect_as_recoverable_failure(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignPocketNoEffectTest")
        try:
            service = VibeCADService()
            base_sketch_result = service.registry.call("partdesign.create_sketch", label="Base Pad Sketch")
            self.assertTrue(base_sketch_result["ok"], base_sketch_result)
            base_sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            base_draw = service.registry.call(
                "sketcher.draw_rectangle",
                width=30,
                height=20,
                sketch_name=base_sketch.Name,
            )
            self.assertTrue(base_draw["ok"], base_draw)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=base_sketch.Name, label='Pocket Base Pad', length=10)
            self.assertTrue(pad_result["ok"], pad_result)

            pocket_sketch_result = service.registry.call("partdesign.create_sketch", label="Pocket Sketch")
            self.assertTrue(pocket_sketch_result["ok"], pocket_sketch_result)
            pocket_sketch = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Label == "Pocket Sketch"
            ][0]
            pocket_draw = service.registry.call(
                "sketcher.draw_rectangle",
                width=8,
                height=6,
                sketch_name=pocket_sketch.Name,
            )
            self.assertTrue(pocket_draw["ok"], pocket_draw)
            pocket_result = service.registry.call('partdesign.extrude', operation='pocket', sketch_name=pocket_sketch.Name, label='No Effect Pocket', length=3)
            self.assertFalse(pocket_result["ok"], pocket_result)
            self.assertTrue(pocket_result["recoverable"], pocket_result)
            self.assertTrue(pocket_result["rolled_back_feature"], pocket_result)
            self.assertIsNone(doc.getObject(pocket_result["active_feature"]))
            self.assertFalse(pocket_result["feature_effect"]["ok"], pocket_result)
            self.assertEqual(
                0.0,
                pocket_result["feature_effect"]["body_shape_delta"]["volume_delta"],
            )
            self.assertIn("did not produce an effective body shape change", pocket_result["error"])
            # Diagnostic content must not regress to the generic one-liner.
            self.assertIn("Body shape delta", pocket_result["error"])
            self.assertIn("Likely cause:", pocket_result["error"])
            self.assertIn("removed automatically", pocket_result["error"])
            self.assertTrue(pocket_result.get("likely_cause"), pocket_result)
            self.assertIsInstance(pocket_result.get("feature_state"), dict)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_pocket_on_point_only_sketch_reports_specific_reason(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignPointOnlyPocketTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Point Only Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            point_result = service.registry.call(
                "sketcher.add_geometry",
                kind="point",
                sketch_name=sketch.Name,
                points=[[3, 3]],
            )
            self.assertTrue(point_result["ok"], point_result)

            pocket_result = service.registry.call(
                "partdesign.extrude",
                operation="pocket",
                sketch_name=sketch.Name,
                label="Point Only Pocket",
                length=5,
            )
            self.assertFalse(pocket_result["ok"], pocket_result)
            self.assertTrue(pocket_result["recoverable"], pocket_result)
            self.assertIn("not ready for PartDesign Pocket", pocket_result["error"])
            # Must surface the specific profile_status reason, not the generic
            # "does not contain a closed profile that is fully constrained."
            self.assertIn("does not expose a closed profile yet", pocket_result["error"])
            self.assertNotIn(
                "does not contain a closed profile that is fully constrained",
                pocket_result["error"],
            )
            self.assertIsInstance(pocket_result.get("profile_status"), dict)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_pocket_on_underconstrained_sketch_reports_dof_count(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignUnderconstrainedPocketTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Loose Circle Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            circle_result = service.registry.call(
                "sketcher.add_geometry",
                kind="circle",
                sketch_name=sketch.Name,
                center=[5, 5],
                radius=3,
            )
            self.assertTrue(circle_result["ok"], circle_result)

            pocket_result = service.registry.call(
                "partdesign.extrude",
                operation="pocket",
                sketch_name=sketch.Name,
                label="Underconstrained Pocket",
                length=5,
            )
            self.assertFalse(pocket_result["ok"], pocket_result)
            self.assertTrue(pocket_result["recoverable"], pocket_result)
            self.assertIn("not ready for PartDesign Pocket", pocket_result["error"])
            self.assertIn("under-constrained", pocket_result["error"])
            self.assertIn("degrees of freedom", pocket_result["error"])
            dof = pocket_result["profile_status"]["degrees_of_freedom"]
            self.assertGreater(dof, 0, pocket_result)
            self.assertIn(f"({dof} degrees of freedom)", pocket_result["error"])
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_polar_pattern_rollback_reports_diagnostics(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignPolarPatternRollbackTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Symmetric Square Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            draw_result = service.registry.call(
                "sketcher.draw_rectangle",
                width=10,
                height=10,
                sketch_name=sketch.Name,
            )
            self.assertTrue(draw_result["ok"], draw_result)
            pad_result = service.registry.call(
                "partdesign.extrude",
                operation="pad",
                sketch_name=sketch.Name,
                label="Symmetric Square Pad",
                length=4,
            )
            self.assertTrue(pad_result["ok"], pad_result)

            # A square centered on the Z axis is invariant under 90-degree
            # rotation, so a 4x polar pattern adds no material: the pattern
            # feature computes a valid shape but the body delta is zero and
            # the feature is rolled back with a diagnostic error.
            pattern_result = service.registry.call(
                "partdesign.pattern",
                operation="polar",
                feature_name=pad_result["active_feature"],
                label="No Effect Polar Pattern",
                axis="Z_Axis",
                angle=360,
                occurrences=4,
            )
            self.assertFalse(pattern_result["ok"], pattern_result)
            self.assertTrue(pattern_result["recoverable"], pattern_result)
            self.assertTrue(pattern_result["rolled_back_feature"], pattern_result)
            self.assertIsNone(doc.getObject(pattern_result["active_feature"]))
            error = pattern_result["error"]
            self.assertIn("PartDesign PolarPattern", error)
            self.assertIn("did not produce an effective body shape change", error)
            # Diagnostic sections that must not regress to the one-liner.
            self.assertIn("The feature itself computed a shape", error)
            self.assertIn("Body shape delta", error)
            self.assertIn("Likely cause:", error)
            self.assertIn("removed automatically", error)
            self.assertTrue(pattern_result.get("likely_cause"), pattern_result)
            self.assertIsInstance(pattern_result.get("feature_state"), dict)
        finally:
            App.closeDocument(doc.Name)

    def test_ineffective_feature_error_composer_reports_diagnostics(self):
        from tool_impl.service import domain_runtime

        message, likely_cause = domain_runtime.describe_ineffective_partdesign_feature(
            "polarpattern",
            feature_shape={"available": True, "solids": 1, "faces": 141, "volume": 19127.2},
            feature_effect={
                "ok": False,
                "feature_has_shape": True,
                "body_shape_delta": {"volume_delta": 0.0, "solids_delta": 0, "faces_delta": 0},
            },
            feature_state={"marked_invalid": False, "state": ["Touched"]},
            report_errors=["Result has multiple solids: this is not currently supported."],
            rolled_back=True,
        )
        self.assertIn("PartDesign polarpattern", message)
        self.assertIn("did not produce an effective body shape change", message)
        self.assertIn("1 solid(s), 141 face(s)", message)
        self.assertIn("volume 19127.200 mm^3", message)
        self.assertIn("Body shape delta", message)
        self.assertIn("Result has multiple solids", message)
        self.assertIn("Likely cause:", message)
        self.assertIn("multiple solids", likely_cause)
        self.assertIn("removed automatically", message)

        invalid_message, invalid_cause = domain_runtime.describe_ineffective_partdesign_feature(
            "hole",
            feature_shape={"available": False},
            feature_effect={
                "ok": False,
                "feature_has_shape": False,
                "body_shape_delta": {"volume_delta": 0.0, "solids_delta": 0, "faces_delta": 0},
            },
            feature_state={"marked_invalid": True, "state": ["Invalid"]},
            report_errors=[],
            rolled_back=True,
        )
        self.assertIn("did not compute a usable shape", invalid_message)
        self.assertIn("marked the feature Invalid", invalid_message)
        self.assertIn("Invalid during recompute", invalid_cause)

    def test_partdesign_linear_pattern_reports_feature_effect(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignLinearPatternEffectTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Pattern Base Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            draw_result = service.registry.call(
                "sketcher.draw_rectangle",
                width=8,
                height=6,
                sketch_name=sketch.Name,
            )
            self.assertTrue(draw_result["ok"], draw_result)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=sketch.Name, label='Pattern Source Pad', length=4)
            self.assertTrue(pad_result["ok"], pad_result)

            pattern_result = service.registry.call('partdesign.pattern', operation='linear', feature_name=pad_result['active_feature'], label='Verified Linear Pattern', direction='X_Axis', length=18, occurrences=2)
            self.assertTrue(pattern_result["ok"], pattern_result)
            self.assertTrue(pattern_result["feature_effect"]["ok"], pattern_result)
            self.assertGreater(pattern_result["body_shape_delta"]["volume_delta"], 0.0, pattern_result)
            self.assertFalse(pattern_result["rolled_back_feature"], pattern_result)
            pattern = doc.getObject(pattern_result["active_feature"])
            self.assertIsNotNone(pattern)
            self.assertEqual(pattern.TypeId, "PartDesign::LinearPattern")
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_fillet_reports_invalid_no_effect_as_recoverable_failure(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignFilletNoEffectTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Fillet Base Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            draw_result = service.registry.call(
                "sketcher.draw_rectangle",
                width=10,
                height=10,
                sketch_name=sketch.Name,
            )
            self.assertTrue(draw_result["ok"], draw_result)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=sketch.Name, label='Fillet Base Pad', length=3)
            self.assertTrue(pad_result["ok"], pad_result)

            fillet_result = service.registry.call('partdesign.dressup', operation='fillet', feature_name=pad_result['active_feature'], label='Impossible Fillet', radius=1000)
            self.assertFalse(fillet_result["ok"], fillet_result)
            self.assertTrue(fillet_result.get("recoverable"), fillet_result)
            if fillet_result.get("feature_effect") is not None:
                self.assertFalse(fillet_result["feature_effect"]["ok"], fillet_result)
            if fillet_result.get("active_feature"):
                self.assertTrue(fillet_result["rolled_back_feature"], fillet_result)
                self.assertIsNone(doc.getObject(fillet_result["active_feature"]))
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_revolve_sketch_creates_native_revolution_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignRevolutionTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Revolution Profile")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            profile_result = service.registry.call('sketcher.add_geometry', kind='polyline', sketch_name=sketch.Name, points=[[2, 0], [4, 0], [4, 6], [2, 6]], closed=True)
            self.assertTrue(profile_result["ok"], profile_result)
            self.assertEqual(profile_result["profile_status"]["degrees_of_freedom"], 0)
            revolve_result = service.registry.call('partdesign.revolve', operation='revolve', sketch_name=sketch.Name, label='Turned Test Boss', angle=180, axis='X_Axis')
            self.assertTrue(revolve_result["ok"], revolve_result)
            feature_name = revolve_result["transaction"]["result"]["feature"]
            feature = doc.getObject(feature_name)
            self.assertIsNotNone(feature)
            self.assertEqual(feature.TypeId, "PartDesign::Revolution")
            self.assertAlmostEqual(float(feature.Angle), 180.0)
            self.assertGreater(len(getattr(feature.Shape, "Faces", [])), 0)
            self.assertGreater(float(getattr(feature.Shape, "Volume", 0.0)), 0.0)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_revolve_rejects_profile_crossing_in_plane_axis(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignRevolutionPreflightTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Crossing Revolution Profile")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            profile_result = service.registry.call(
                "sketcher.draw_rectangle",
                sketch_name=sketch.Name,
                width=2,
                height=4,
                center_x=3,
                center_y=0,
            )
            self.assertTrue(profile_result["ok"], profile_result)

            revolve_result = service.registry.call('partdesign.revolve', operation='revolve', sketch_name=sketch.Name, label='Invalid Crossing Revolution', axis='X_Axis')

            self.assertFalse(revolve_result["ok"], revolve_result)
            self.assertTrue(revolve_result["recoverable"])
            self.assertTrue(revolve_result["revolution_preflight"]["axis_crosses_profile"])
            self.assertIn("crosses the requested in-plane revolution axis", revolve_result["error"])
            self.assertFalse([
                obj for obj in doc.Objects
                if getattr(obj, "TypeId", "") == "PartDesign::Revolution"
            ])
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_groove_sketch_creates_native_groove_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignGrooveTest")
        try:
            service = VibeCADService()
            base_sketch_result = service.registry.call("partdesign.create_sketch", label="Groove Base Sketch")
            self.assertTrue(base_sketch_result["ok"], base_sketch_result)
            base_sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            base_profile = service.registry.call(
                "sketcher.draw_rectangle",
                sketch_name=base_sketch.Name,
                width=10,
                height=8,
            )
            self.assertTrue(base_profile["ok"], base_profile)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=base_sketch.Name, label='Groove Base Pad', length=8)
            self.assertTrue(pad_result["ok"], pad_result)

            groove_sketch_result = service.registry.call("partdesign.create_sketch", label="Groove Cut Sketch")
            self.assertTrue(groove_sketch_result["ok"], groove_sketch_result)
            groove_sketches = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Name != base_sketch.Name
            ]
            self.assertEqual(len(groove_sketches), 1)
            groove_sketch = groove_sketches[0]
            groove_profile = service.registry.call('sketcher.add_geometry', kind='polyline', sketch_name=groove_sketch.Name, points=[[-3, 1], [3, 1], [3, 3], [-3, 3]], closed=True)
            self.assertTrue(groove_profile["ok"], groove_profile)

            groove_result = service.registry.call('partdesign.revolve', operation='groove', sketch_name=groove_sketch.Name, label='Native Test Groove', angle=360, axis='X_Axis')
            self.assertTrue(groove_result["ok"], groove_result)
            self.assertTrue(groove_result["feature_effect"]["ok"], groove_result)
            self.assertLess(groove_result["body_shape_delta"]["volume_delta"], 0.0, groove_result)
            groove = doc.getObject(groove_result["active_feature"])
            self.assertIsNotNone(groove)
            self.assertEqual(groove.TypeId, "PartDesign::Groove")
            self.assertAlmostEqual(float(groove.Angle), 360.0)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_datum_and_draft_feature_create_native_draft(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignDraftFeatureTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call("partdesign.create_sketch", label="Draft Base Sketch")
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            draw_result = service.registry.call(
                "sketcher.draw_rectangle",
                sketch_name=sketch.Name,
                width=10,
                height=10,
            )
            self.assertTrue(draw_result["ok"], draw_result)
            pad_result = service.registry.call('partdesign.extrude', operation='pad', sketch_name=sketch.Name, label='Draft Base Pad', length=10)
            self.assertTrue(pad_result["ok"], pad_result)
            pad = doc.getObject(pad_result["active_feature"])
            self.assertIsNotNone(pad)

            plane_result = service.registry.call(
                "partdesign.create_datum_plane",
                label="Draft Neutral Plane",
                support_plane="YZ_Plane",
            )
            self.assertTrue(plane_result["ok"], plane_result)
            line_result = service.registry.call(
                "partdesign.create_datum_line",
                label="Draft Pull Direction",
                support_axis="X_Axis",
            )
            self.assertTrue(line_result["ok"], line_result)

            faces = list(getattr(pad.Shape, "Faces", []) or [])
            z_faces = [
                index for index, face in enumerate(faces)
                if getattr(getattr(face, "Surface", None), "Axis", None) == App.Vector(0, 0, 1)
            ]
            self.assertGreaterEqual(len(z_faces), 1)
            top_index = max(z_faces, key=lambda index: faces[index].CenterOfMass.z)
            draft_result = service.registry.call('partdesign.dressup', operation='draft', feature_name=pad.Name, face_names=[f'Face{top_index + 1}'], neutral_plane_name=plane_result['datum'], pull_direction_name=line_result['datum'], label='Native Test Draft', angle=10, reversed=True)
            self.assertTrue(draft_result["ok"], draft_result)
            self.assertTrue(draft_result["feature_effect"]["ok"], draft_result)
            draft = doc.getObject(draft_result["active_feature"])
            self.assertIsNotNone(draft)
            self.assertEqual(draft.TypeId, "PartDesign::Draft")
            self.assertAlmostEqual(float(draft.Angle), 10.0)
            self.assertGreater(abs(draft_result["body_shape_delta"]["volume_delta"]), 0.0, draft_result)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_boolean_bodies_creates_native_boolean_cut(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignBooleanBodiesTest")
        try:
            service = VibeCADService()
            tool_body = doc.addObject("PartDesign::Body", "ToolBody")
            tool_box = doc.addObject("PartDesign::AdditiveBox", "ToolBox")
            tool_box.Length = 10
            tool_box.Width = 10
            tool_box.Height = 10
            tool_body.addObject(tool_box)
            tool_body.Tip = tool_box

            target_body = doc.addObject("PartDesign::Body", "TargetBody")
            target_box = doc.addObject("PartDesign::AdditiveBox", "TargetBox")
            target_box.Length = 10
            target_box.Width = 10
            target_box.Height = 10
            target_box.Placement.Base = App.Vector(-5, 0, 0)
            target_body.addObject(target_box)
            target_body.Tip = target_box
            doc.recompute()

            boolean_result = service.registry.call(
                "partdesign.boolean_bodies",
                target_body_name=target_body.Name,
                tool_body_names=[tool_body.Name],
                operation="cut",
                label="Native Boolean Cut",
            )
            self.assertTrue(boolean_result["ok"], boolean_result)
            self.assertTrue(boolean_result["feature_effect"]["ok"], boolean_result)
            self.assertLess(boolean_result["body_shape_delta"]["volume_delta"], 0.0, boolean_result)
            boolean = doc.getObject(boolean_result["active_feature"])
            self.assertIsNotNone(boolean)
            self.assertEqual(boolean.TypeId, "PartDesign::Boolean")
            self.assertIn(str(boolean.Type), {"1", "Cut"})
            self.assertAlmostEqual(float(getattr(boolean.Shape, "Volume", 0.0)), 500.0)
            self.assertIs(getattr(target_body, "Tip", None), boolean)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_loft_profiles_creates_native_additive_loft_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignLoftTest")
        try:
            service = VibeCADService()
            profile_result = service.registry.call("partdesign.create_sketch", label="Loft Profile", plane="XY_Plane")
            self.assertTrue(profile_result["ok"], profile_result)
            profile = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            profile_draw = service.registry.call("sketcher.draw_rectangle",
                width=6,
                height=4,
                sketch_name=profile.Name,
            )
            self.assertTrue(profile_draw["ok"], profile_draw)
            section_result = service.registry.call("partdesign.create_sketch", label="Loft Section", plane="XZ_Plane")
            self.assertTrue(section_result["ok"], section_result)
            section = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Name != profile.Name
            ][0]
            section_draw = service.registry.call("sketcher.draw_rectangle",
                width=3,
                height=2,
                sketch_name=section.Name,
            )
            self.assertTrue(section_draw["ok"], section_draw)

            loft_result = service.registry.call("partdesign.loft_profiles",
                profile_sketch_name=profile.Name,
                section_sketch_names=[section.Name],
                label="Native Additive Loft",
            )
            self.assertTrue(loft_result["ok"], loft_result)
            loft_name = loft_result["transaction"]["result"]["feature"]
            loft = doc.getObject(loft_name)
            self.assertIsNotNone(loft)
            self.assertEqual(loft.TypeId, "PartDesign::AdditiveLoft")
            self.assertEqual(loft.Profile[0], profile)
            self.assertEqual([item[0] for item in loft.Sections], [section])
            self.assertGreater(float(getattr(loft.Shape, "Volume", 0.0)), 0.0)
            body = [obj for obj in doc.Objects if obj.TypeId == "PartDesign::Body"][0]
            self.assertIs(getattr(body, "Tip", None), loft)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_sweep_profile_creates_native_additive_pipe_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignSweepTest")
        try:
            service = VibeCADService()
            profile_result = service.registry.call("partdesign.create_sketch", label="Sweep Profile", plane="XY_Plane")
            self.assertTrue(profile_result["ok"], profile_result)
            profile = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            circle_result = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=profile.Name, radius=1, center=[0, 0])
            self.assertTrue(circle_result["ok"], circle_result)
            spine_result = service.registry.call("partdesign.create_sketch", label="Sweep Spine", plane="XZ_Plane")
            self.assertTrue(spine_result["ok"], spine_result)
            spine = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Name != profile.Name
            ][0]
            line_result = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=spine.Name, points=[[0, 0], [0, 5]])
            self.assertTrue(line_result["ok"], line_result)

            sweep_result = service.registry.call("partdesign.sweep_profile",
                profile_sketch_name=profile.Name,
                spine_sketch_name=spine.Name,
                label="Native Additive Pipe",
            )
            self.assertTrue(sweep_result["ok"], sweep_result)
            sweep_name = sweep_result["transaction"]["result"]["feature"]
            sweep = doc.getObject(sweep_name)
            self.assertIsNotNone(sweep)
            self.assertEqual(sweep.TypeId, "PartDesign::AdditivePipe")
            self.assertEqual(sweep.Profile[0], profile)
            self.assertEqual(sweep.Spine[0], spine)
            self.assertGreater(float(getattr(sweep.Shape, "Volume", 0.0)), 0.0)
            body = [obj for obj in doc.Objects if obj.TypeId == "PartDesign::Body"][0]
            self.assertIs(getattr(body, "Tip", None), sweep)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_sweep_profile_multisection_creates_variable_section_pipe(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignMultisectionSweepTest")
        try:
            service = VibeCADService()
            profile_result = service.registry.call("partdesign.create_sketch", label="Volute Profile", plane="XY_Plane")
            self.assertTrue(profile_result["ok"], profile_result)
            profile = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            profile_draw = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=profile.Name, radius=2, center=[0, 0])
            self.assertTrue(profile_draw["ok"], profile_draw)

            spine_result = service.registry.call("partdesign.create_sketch", label="Volute Spine", plane="XZ_Plane")
            self.assertTrue(spine_result["ok"], spine_result)
            spine = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Name != profile.Name
            ][0]
            spine_draw = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=spine.Name, points=[[0, 0], [0, 10]])
            self.assertTrue(spine_draw["ok"], spine_draw)

            section_result = service.registry.call("partdesign.create_sketch", label="Volute End Section", plane="XY_Plane")
            self.assertTrue(section_result["ok"], section_result)
            section = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Name not in {profile.Name, spine.Name}
            ][0]
            section.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
            section_draw = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=section.Name, radius=1, center=[0, 0])
            self.assertTrue(section_draw["ok"], section_draw)
            doc.recompute()

            sweep_result = service.registry.call("partdesign.sweep_profile",
                profile_sketch_name=profile.Name,
                spine_sketch_name=spine.Name,
                section_sketch_names=[section.Name],
                label="Variable Section Pipe",
            )
            self.assertTrue(sweep_result["ok"], sweep_result)
            result = sweep_result["transaction"]["result"]
            self.assertEqual(result["transformation"], "Multisection", result)
            self.assertEqual(result["sections"], [section.Name], result)
            sweep = doc.getObject(result["feature"])
            self.assertIsNotNone(sweep)
            self.assertEqual(sweep.TypeId, "PartDesign::AdditivePipe")
            self.assertEqual(str(sweep.Transformation), "Multisection")
            self.assertEqual([item[0] for item in sweep.Sections], [section])
            volume = float(getattr(sweep.Shape, "Volume", 0.0))
            small_cylinder = math.pi * 1.0**2 * 10.0
            large_cylinder = math.pi * 2.0**2 * 10.0
            self.assertGreater(volume, small_cylinder, volume)
            self.assertLess(volume, large_cylinder, volume)
            body = [obj for obj in doc.Objects if obj.TypeId == "PartDesign::Body"][0]
            self.assertIs(getattr(body, "Tip", None), sweep)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_sweep_profile_rejects_missing_section_sketch(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignSweepBadSectionTest")
        try:
            service = VibeCADService()
            profile_result = service.registry.call("partdesign.create_sketch", label="Sweep Profile", plane="XY_Plane")
            self.assertTrue(profile_result["ok"], profile_result)
            profile = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            circle_result = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=profile.Name, radius=1, center=[0, 0])
            self.assertTrue(circle_result["ok"], circle_result)
            spine_result = service.registry.call("partdesign.create_sketch", label="Sweep Spine", plane="XZ_Plane")
            self.assertTrue(spine_result["ok"], spine_result)
            spine = [
                obj for obj in doc.Objects
                if obj.TypeId == "Sketcher::SketchObject" and obj.Name != profile.Name
            ][0]
            line_result = service.registry.call('sketcher.add_geometry', kind='line', sketch_name=spine.Name, points=[[0, 0], [0, 5]])
            self.assertTrue(line_result["ok"], line_result)

            sweep_result = service.registry.call("partdesign.sweep_profile",
                profile_sketch_name=profile.Name,
                spine_sketch_name=spine.Name,
                section_sketch_names=["NoSuchSketch"],
            )
            self.assertFalse(sweep_result["ok"], sweep_result)
            self.assertEqual(sweep_result["error"], "Section sketch not found.")
            self.assertEqual(sweep_result["requested"], "NoSuchSketch")
            self.assertEqual(
                [obj for obj in doc.Objects if obj.TypeId in {"PartDesign::AdditivePipe", "PartDesign::SubtractivePipe"}],
                [],
            )
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_helix_profile_creates_native_additive_helix_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignHelixTest")
        try:
            service = VibeCADService()
            profile_result = service.registry.call("partdesign.create_sketch", label="Helix Profile")
            self.assertTrue(profile_result["ok"], profile_result)
            profile = [obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"][0]
            circle_result = service.registry.call('sketcher.add_geometry', kind='circle', sketch_name=profile.Name, radius=0.5, center=[2, 0])
            self.assertTrue(circle_result["ok"], circle_result)
            radius_result = service.registry.call('sketcher.add_constraint', constraint_type='Radius', sketch_name=profile.Name, value=0.5, first_geometry=0)
            self.assertTrue(radius_result["ok"], radius_result)
            lock_result = service.registry.call('sketcher.add_constraint', constraint_type='Lock', sketch_name=profile.Name, x=2, y=0, first_geometry=0, first_point='center')
            self.assertTrue(lock_result["ok"], lock_result)
            close_result = service.registry.call("sketcher.close_sketch", sketch_name=profile.Name)
            self.assertTrue(close_result["ok"], close_result)

            helix_result = service.registry.call(
                "partdesign.helix_profile",
                profile_sketch_name=profile.Name,
                label="Native Additive Helix",
                mode="additive",
                reference_axis="V_Axis",
                pitch=3,
                height=9,
                turns=3,
                native_mode=0,
            )
            self.assertTrue(helix_result["ok"], helix_result)
            self.assertTrue(helix_result["feature_effect"]["ok"], helix_result)
            self.assertGreater(helix_result["body_shape_delta"]["volume_delta"], 0.0, helix_result)
            helix = doc.getObject(helix_result["active_feature"])
            self.assertIsNotNone(helix)
            self.assertEqual(helix.TypeId, "PartDesign::AdditiveHelix")
            self.assertGreater(float(getattr(helix.Shape, "Volume", 0.0)), 0.0)
            body = [obj for obj in doc.Objects if obj.TypeId == "PartDesign::Body"][0]
            self.assertIs(getattr(body, "Tip", None), helix)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_native_dressup_features_work_on_existing_features(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignEdgeFeaturesTest")
        try:
            body_fillet = doc.addObject("PartDesign::Body", "FilletBody")
            fillet_box = body_fillet.newObject("PartDesign::AdditiveBox", "FilletBaseBox")
            fillet_box.Length = 10
            fillet_box.Width = 8
            fillet_box.Height = 4
            body_fillet.Tip = fillet_box
            body_chamfer = doc.addObject("PartDesign::Body", "ChamferBody")
            chamfer_box = body_chamfer.newObject("PartDesign::AdditiveBox", "ChamferBaseBox")
            chamfer_box.Length = 10
            chamfer_box.Width = 8
            chamfer_box.Height = 4
            body_chamfer.Tip = chamfer_box
            body_thickness = doc.addObject("PartDesign::Body", "ThicknessBody")
            thickness_box = body_thickness.newObject("PartDesign::AdditiveBox", "ThicknessBaseBox")
            thickness_box.Length = 10
            thickness_box.Width = 8
            thickness_box.Height = 4
            body_thickness.Tip = thickness_box
            doc.recompute()

            service = VibeCADService()
            fillet_result = service.registry.call('partdesign.dressup', operation='fillet', feature_name=fillet_box.Name, label='Native Body Fillet', radius=0.5)
            self.assertTrue(fillet_result["ok"], fillet_result)
            fillet_name = fillet_result["transaction"]["result"]["feature"]
            fillet = doc.getObject(fillet_name)
            self.assertIsNotNone(fillet)
            self.assertEqual(fillet.TypeId, "PartDesign::Fillet")
            self.assertAlmostEqual(float(fillet.Radius), 0.5)
            self.assertGreater(float(getattr(fillet.Shape, "Volume", 0.0)), 0.0)

            chamfer_result = service.registry.call('partdesign.dressup', operation='chamfer', feature_name=chamfer_box.Name, label='Native Body Chamfer', size=0.5)
            self.assertTrue(chamfer_result["ok"], chamfer_result)
            chamfer_name = chamfer_result["transaction"]["result"]["feature"]
            chamfer = doc.getObject(chamfer_name)
            self.assertIsNotNone(chamfer)
            self.assertEqual(chamfer.TypeId, "PartDesign::Chamfer")
            self.assertAlmostEqual(float(chamfer.Size), 0.5)
            self.assertGreater(float(getattr(chamfer.Shape, "Volume", 0.0)), 0.0)

            thickness_result = service.registry.call('partdesign.dressup', operation='thickness', feature_name=thickness_box.Name, label='Native Body Thickness', wall_thickness=0.75, face_names=['Face1'], inward=True)
            self.assertTrue(thickness_result["ok"], thickness_result)
            thickness_name = thickness_result["transaction"]["result"]["feature"]
            thickness = doc.getObject(thickness_name)
            self.assertIsNotNone(thickness)
            self.assertEqual(thickness.TypeId, "PartDesign::Thickness")
            self.assertAlmostEqual(float(thickness.Value), 0.75)
            self.assertEqual(int(thickness.Reversed), 1)
            self.assertGreater(len(getattr(thickness.Shape, "Faces", []) or []), 0)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_dressup_description_advertises_shelling(self):
        service = VibeCADService()
        spec = service.registry.get("partdesign.dressup").to_schema()
        description = str(spec.get("description", "")).lower()
        self.assertIn("shell", description)
        self.assertIn("hollow", description)
        self.assertIn("wall thickness", description)

    def test_modeling_tool_descriptions_carry_when_to_use_steering(self):
        service = VibeCADService()

        def _description(tool_name):
            return str(service.registry.get(tool_name).to_schema().get("description", "")).lower()

        # High-leverage shape tools steer the model toward their use cases
        # in general geometric terms (no named parts: steering must stay
        # general-purpose).
        loft = _description("partdesign.loft_profiles")
        self.assertIn("dissimilar cross-sections", loft)
        self.assertIn("curvature-continuous", loft)
        self.assertIn("datum plane", loft)
        helix = _description("partdesign.helix_profile")
        self.assertIn("thread", helix)
        self.assertIn("spring", helix)
        sweep = _description("partdesign.sweep_profile")
        self.assertIn("variable cross-section", sweep)
        self.assertIn("along the path", sweep)
        revolve = _description("partdesign.revolve")
        self.assertIn("axisymmetric", revolve)

        # Overlapping tools cross-reference their sibling alternative.
        self.assertIn("partdesign.hole_from_sketch", _description("part.cut_cylindrical_hole"))
        self.assertIn("partdesign.pattern", _description("draft.create_array"))
        self.assertIn("assembly.set_component_placement", _description("part.set_placement"))
        self.assertIn("part.set_placement", _description("assembly.set_component_placement"))

        # Stale phrases are gone from every registered tool description.
        for tool_name in service.registry.names():
            description = str(service.registry.get(tool_name).to_schema().get("description", ""))
            self.assertNotIn("motors, wheels", description, tool_name)
            self.assertNotIn("XY_Plane unless", description, tool_name)

    def test_partdesign_thickness_shells_padded_box_and_decreases_volume(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignShellPaddedBoxTest")
        try:
            service = VibeCADService()
            sketch_result = service.registry.call(
                "partdesign.create_sketch", label="Shell Base Sketch"
            )
            self.assertTrue(sketch_result["ok"], sketch_result)
            sketch = [
                obj for obj in doc.Objects if obj.TypeId == "Sketcher::SketchObject"
            ][0]
            draw_result = service.registry.call(
                "sketcher.draw_rectangle",
                width=20,
                height=15,
                sketch_name=sketch.Name,
            )
            self.assertTrue(draw_result["ok"], draw_result)
            pad_result = service.registry.call(
                "partdesign.extrude",
                operation="pad",
                sketch_name=sketch.Name,
                label="Shell Base Pad",
                length=10,
            )
            self.assertTrue(pad_result["ok"], pad_result)
            pad = doc.getObject(pad_result["active_feature"])
            self.assertIsNotNone(pad)
            pad_volume = float(pad.Shape.Volume)
            self.assertGreater(pad_volume, 0.0)
            faces = list(pad.Shape.Faces)
            top_index = max(
                range(len(faces)),
                key=lambda index: float(faces[index].CenterOfMass.z),
            )

            thickness_result = service.registry.call(
                "partdesign.dressup",
                operation="thickness",
                feature_name=pad.Name,
                label="Shelled Padded Box",
                wall_thickness=1.5,
                face_names=[f"Face{top_index + 1}"],
                inward=True,
            )
            self.assertTrue(thickness_result["ok"], thickness_result)
            thickness_name = thickness_result["transaction"]["result"]["feature"]
            thickness = doc.getObject(thickness_name)
            self.assertIsNotNone(thickness)
            self.assertEqual(thickness.TypeId, "PartDesign::Thickness")
            self.assertTrue(thickness.Shape.isValid())
            shelled_volume = float(thickness.Shape.Volume)
            self.assertGreater(shelled_volume, 0.0)
            self.assertLess(shelled_volume, pad_volume)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_find_subelements_finds_top_planar_face_of_box(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADFindSubelementsBoxTest")
        try:
            body = doc.addObject("PartDesign::Body", "FindFaceBody")
            box = body.newObject("PartDesign::AdditiveBox", "FindFaceBaseBox")
            box.Length = 10
            box.Width = 8
            box.Height = 4
            body.Tip = box
            doc.recompute()

            service = VibeCADService()
            result = service.registry.call(
                "partdesign.find_subelements",
                object_name=box.Name,
                element_type="face",
                geometry_type="plane",
                normal={"z": 1},
            )
            self.assertTrue(result["found"], result)
            self.assertEqual(result["element_type"], "face")
            self.assertEqual(result["total_elements"], 6)
            self.assertEqual(result["match_count"], 1, result)
            match = result["matches"][0]
            self.assertRegex(match["name"], r"^Face\d+$")
            self.assertEqual(match["geometry_type"], "plane")
            self.assertAlmostEqual(match["center_of_mass"]["z"], 4.0, places=5)
            self.assertAlmostEqual(match["outward_normal"]["z"], 1.0, places=5)
            self.assertAlmostEqual(match["area"], 80.0, places=4)

            # The returned name must resolve to the same geometric face.
            face_index = int(match["name"][4:]) - 1
            face = box.Shape.Faces[face_index]
            self.assertAlmostEqual(float(face.CenterOfMass.z), 4.0, places=5)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_find_subelements_finds_cylindrical_face_by_radius(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADFindSubelementsCylinderTest")
        try:
            cylinder = doc.addObject("Part::Cylinder", "FindFaceCylinder")
            cylinder.Radius = 5
            cylinder.Height = 12
            doc.recompute()

            service = VibeCADService()
            result = service.registry.call(
                "partdesign.find_subelements",
                object_name=cylinder.Name,
                element_type="face",
                geometry_type="cylinder",
                radius=5,
            )
            self.assertTrue(result["found"], result)
            self.assertEqual(result["match_count"], 1, result)
            match = result["matches"][0]
            self.assertEqual(match["geometry_type"], "cylinder")
            self.assertAlmostEqual(match["radius"], 5.0, places=5)

            # A radius that matches nothing returns zero matches, not an error.
            miss = service.registry.call(
                "partdesign.find_subelements",
                object_name=cylinder.Name,
                element_type="face",
                geometry_type="cylinder",
                radius=7,
            )
            self.assertTrue(miss["found"], miss)
            self.assertEqual(miss["match_count"], 0, miss)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_find_subelements_finds_circular_edge_and_rejects_bad_input(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADFindSubelementsEdgeTest")
        try:
            cylinder = doc.addObject("Part::Cylinder", "FindEdgeCylinder")
            cylinder.Radius = 5
            cylinder.Height = 12
            doc.recompute()

            service = VibeCADService()
            result = service.registry.call(
                "partdesign.find_subelements",
                object_name=cylinder.Name,
                element_type="edge",
                geometry_type="circle",
                near_point={"x": 0, "y": 0, "z": 12},
                max_distance=0.5,
            )
            self.assertTrue(result["found"], result)
            self.assertEqual(result["match_count"], 1, result)
            match = result["matches"][0]
            self.assertRegex(match["name"], r"^Edge\d+$")
            self.assertEqual(match["geometry_type"], "circle")
            self.assertAlmostEqual(match["radius"], 5.0, places=5)
            self.assertAlmostEqual(match["center_of_mass"]["z"], 12.0, places=5)

            bad_kind = service.registry.call(
                "partdesign.find_subelements",
                object_name=cylinder.Name,
                element_type="vertex",
            )
            self.assertFalse(bad_kind["found"], bad_kind)
            self.assertIn("element_type", bad_kind["error"])

            missing = service.registry.call(
                "partdesign.find_subelements",
                object_name="NoSuchObject",
            )
            self.assertFalse(missing["found"], missing)
            self.assertIn("not found", missing["error"].lower())
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_check_interference_reports_overlap_volume_for_intersecting_boxes(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADInterferenceOverlapTest")
        try:
            box_a = doc.addObject("Part::Box", "OverlapBoxA")
            box_a.Length = 10
            box_a.Width = 10
            box_a.Height = 10
            box_b = doc.addObject("Part::Box", "OverlapBoxB")
            box_b.Length = 10
            box_b.Width = 10
            box_b.Height = 10
            # Shift B by 6mm in X: 4 x 10 x 10 = 400 mm^3 overlap.
            box_b.Placement = App.Placement(
                App.Vector(6, 0, 0), App.Rotation()
            )
            doc.recompute()

            service = VibeCADService()
            result = service.registry.call(
                "assembly.check_interference",
                object_names=[box_a.Name, box_b.Name],
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["pair_count"], 1)
            self.assertEqual(result["interference_count"], 1)
            self.assertEqual(result["clear_count"], 0)
            pair = result["pairs"][0]
            self.assertEqual(pair["status"], "interference")
            self.assertAlmostEqual(pair["overlap_volume"], 400.0, places=3)
            self.assertEqual(pair["min_distance"], 0.0)
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_check_interference_reports_min_distance_for_separated_boxes(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADInterferenceClearanceTest")
        try:
            box_a = doc.addObject("Part::Box", "ClearBoxA")
            box_a.Length = 10
            box_a.Width = 10
            box_a.Height = 10
            box_b = doc.addObject("Part::Box", "ClearBoxB")
            box_b.Length = 10
            box_b.Width = 10
            box_b.Height = 10
            # Shift B by 13mm in X: faces are 3mm apart.
            box_b.Placement = App.Placement(
                App.Vector(13, 0, 0), App.Rotation()
            )
            doc.recompute()

            service = VibeCADService()
            result = service.registry.call(
                "assembly.check_interference",
                object_names=[box_a.Name, box_b.Name],
                clearance_threshold=5.0,
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["interference_count"], 0)
            self.assertEqual(result["clear_count"], 1)
            self.assertEqual(result["below_clearance_count"], 1)
            pair = result["pairs"][0]
            self.assertEqual(pair["status"], "clear")
            self.assertEqual(pair["overlap_volume"], 0.0)
            self.assertAlmostEqual(pair["min_distance"], 3.0, places=5)
            self.assertTrue(pair["below_clearance"])

            # A wide enough gap is not flagged against the threshold.
            relaxed = service.registry.call(
                "assembly.check_interference",
                object_names=[box_a.Name, box_b.Name],
                clearance_threshold=2.0,
            )
            self.assertTrue(relaxed["ok"], relaxed)
            self.assertEqual(relaxed["below_clearance_count"], 0)
            self.assertFalse(relaxed["pairs"][0]["below_clearance"])
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_check_interference_rejects_missing_and_insufficient_objects(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADInterferenceErrorTest")
        try:
            box = doc.addObject("Part::Box", "LonelyBox")
            box.Length = 10
            box.Width = 10
            box.Height = 10
            doc.recompute()

            service = VibeCADService()
            missing = service.registry.call(
                "assembly.check_interference",
                object_names=[box.Name, "NoSuchObject"],
            )
            self.assertFalse(missing["ok"], missing)
            self.assertIn("NoSuchObject", missing["error"])

            lonely = service.registry.call(
                "assembly.check_interference",
                object_names=[box.Name],
            )
            self.assertFalse(lonely["ok"], lonely)
            self.assertIn("at least two", lonely["error"])

            no_targets = service.registry.call("assembly.check_interference")
            self.assertFalse(no_targets["ok"], no_targets)
            self.assertIn("error", no_targets)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_native_linear_and_polar_patterns_work_on_existing_features(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignPatternFeaturesTest")
        try:
            body_linear = doc.addObject("PartDesign::Body", "LinearBody")
            linear_box = body_linear.newObject("PartDesign::AdditiveBox", "LinearBaseBox")
            linear_box.Length = 10
            linear_box.Width = 8
            linear_box.Height = 4
            body_linear.Tip = linear_box
            body_polar = doc.addObject("PartDesign::Body", "PolarBody")
            polar_box = body_polar.newObject("PartDesign::AdditiveBox", "PolarBaseBox")
            polar_box.Length = 10
            polar_box.Width = 8
            polar_box.Height = 4
            body_polar.Tip = polar_box
            doc.recompute()

            service = VibeCADService()
            linear_result = service.registry.call('partdesign.pattern', operation='linear', feature_name=linear_box.Name, label='Native Linear Pattern', direction='X_Axis', length=30, occurrences=3)
            self.assertTrue(linear_result["ok"], linear_result)
            linear_name = linear_result["transaction"]["result"]["feature"]
            linear_pattern = doc.getObject(linear_name)
            self.assertIsNotNone(linear_pattern)
            self.assertEqual(linear_pattern.TypeId, "PartDesign::LinearPattern")
            self.assertAlmostEqual(float(linear_pattern.Length), 30.0)
            self.assertEqual(int(linear_pattern.Occurrences), 3)
            self.assertGreater(float(getattr(linear_pattern.Shape, "Volume", 0.0)), 0.0)

            polar_result = service.registry.call('partdesign.pattern', operation='polar', feature_name=polar_box.Name, label='Native Polar Pattern', axis='Z_Axis', angle=360, occurrences=4)
            self.assertTrue(polar_result["ok"], polar_result)
            polar_name = polar_result["transaction"]["result"]["feature"]
            polar_pattern = doc.getObject(polar_name)
            self.assertIsNotNone(polar_pattern)
            self.assertEqual(polar_pattern.TypeId, "PartDesign::PolarPattern")
            self.assertAlmostEqual(float(polar_pattern.Angle), 360.0)
            self.assertEqual(int(polar_pattern.Occurrences), 4)
            self.assertGreater(float(getattr(polar_pattern.Shape, "Volume", 0.0)), 0.0)
        finally:
            App.closeDocument(doc.Name)

    def test_partdesign_native_mirror_feature_works_on_existing_feature(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADPartDesignMirrorFeatureTest")
        try:
            body = doc.addObject("PartDesign::Body", "MirrorBody")
            box = body.newObject("PartDesign::AdditiveBox", "MirrorBaseBox")
            box.Length = 10
            box.Width = 8
            box.Height = 4
            body.Tip = box
            doc.recompute()

            service = VibeCADService()
            mirror_result = service.registry.call('partdesign.pattern', operation='mirror', feature_name=box.Name, label='Native Mirrored Feature', mirror_plane='YZ_Plane')
            self.assertTrue(mirror_result["ok"], mirror_result)
            mirror_name = mirror_result["transaction"]["result"]["feature"]
            mirrored = doc.getObject(mirror_name)
            self.assertIsNotNone(mirrored)
            self.assertEqual(mirrored.TypeId, "PartDesign::Mirrored")
            self.assertEqual(mirror_result["transaction"]["result"]["mirror_plane"], "YZ_Plane")
            self.assertGreater(float(getattr(mirrored.Shape, "Volume", 0.0)), 0.0)
            self.assertIs(getattr(body, "Tip", None), mirrored)
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_summary_reads_real_assembly(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblySummaryTest")
        try:
            assembly = doc.addObject("Assembly::AssemblyObject", "AssemblyForSummary")
            assembly.Label = "Readable Assembly"
            assembly.Type = "Assembly"
            joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
            box = assembly.newObject("Part::Box", "BoxInAssembly")
            box.Label = "Assembly Component"
            doc.recompute()
            service = VibeCADService()
            summary = service.assembly_summary()
            self.assertEqual(summary["assembly_count"], 1)
            item = summary["assemblies"][0]
            self.assertEqual(item["name"], assembly.Name)
            self.assertEqual(item["label"], "Readable Assembly")
            self.assertEqual(item["joint_groups"], 1)
            self.assertEqual(item["joints"], 0)
            self.assertEqual(item["grounded_count"], 0)
            self.assertEqual(item["joint_children"], [])
            self.assertEqual(item["components"], 1)
            child_names = {child["name"] for child in item["children"]}
            self.assertIn(joint_group.Name, child_names)
            self.assertIn(box.Name, child_names)
            component_names = {child["name"] for child in item["component_children"]}
            self.assertEqual(component_names, {box.Name})
        finally:
            App.closeDocument(doc.Name)

    def test_add_assembly_component_adds_existing_object_incrementally(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblyAddComponentTest")
        try:
            base = doc.addObject("Part::Box", "BasePlate")
            base.Label = "Base Plate"
            arm = doc.addObject("Part::Box", "ArmLink")
            arm.Label = "Arm Link"
            doc.recompute()
            service = VibeCADService()
            create_result = service.registry.call("assembly.create_assembly", label="Incremental Assembly")
            self.assertTrue(create_result["ok"], create_result)
            self.assertEqual(create_result["assembly_label"], "Incremental Assembly")
            self.assertEqual(create_result["assembly_summary"]["assembly_count"], 1)
            summary = service.assembly_summary()
            self.assertEqual(summary["assemblies"][0]["components"], 0)

            first = service.registry.call(
                "assembly.add_component",
                assembly_name="Incremental Assembly",
                component_name="Base Plate",
            )
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["transaction"]["result"]["components"], 1)
            self.assertEqual(first["component_label"], "Base Plate")
            self.assertEqual(first["components"], 1)
            self.assertEqual(first["assembly_summary"]["assemblies"][0]["components"], 1)
            second = service.registry.call(
                "assembly.add_component",
                assembly_name="Incremental Assembly",
                component_name=arm.Name,
            )
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["transaction"]["result"]["components"], 2)
            self.assertEqual(second["component_label"], "Arm Link")
            self.assertEqual(second["components"], 2)
            duplicate = service.registry.call(
                "assembly.add_component",
                assembly_name="Incremental Assembly",
                component_name="Base Plate",
            )
            self.assertTrue(duplicate["ok"], duplicate)
            self.assertTrue(duplicate["transaction"]["result"]["already_present"])
            self.assertTrue(duplicate["already_present"])
            self.assertEqual(duplicate["transaction"]["result"]["components"], 2)

            assembly = service.assembly_summary()["assemblies"][0]
            self.assertEqual(assembly["components"], 2)
            labels = {child["label"] for child in assembly["children"]}
            self.assertIn("Base Plate", labels)
            self.assertIn("Arm Link", labels)
        finally:
            App.closeDocument(doc.Name)

    def test_set_assembly_component_placement_positions_existing_component(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblyPlacementTest")
        try:
            arm = doc.addObject("Part::Box", "ArmLink")
            arm.Label = "Arm Link"
            doc.recompute()
            service = VibeCADService()
            create_result = service.registry.call("assembly.create_assembly", label="Positioned Assembly")
            self.assertTrue(create_result["ok"], create_result)
            add_result = service.registry.call(
                "assembly.add_component",
                assembly_name="Positioned Assembly",
                component_name=arm.Name,
            )
            self.assertTrue(add_result["ok"], add_result)

            placement_result = service.registry.call(
                "assembly.set_component_placement",
                assembly_name="Positioned Assembly",
                component_name="Arm Link",
                x=42,
                y=-8,
                z=12,
                yaw_degrees=30,
                pitch_degrees=0,
                roll_degrees=90,
            )
            self.assertTrue(placement_result["ok"], placement_result)
            self.assertEqual(placement_result["component"], arm.Name)
            self.assertAlmostEqual(float(arm.Placement.Base.x), 42.0)
            self.assertAlmostEqual(float(arm.Placement.Base.y), -8.0)
            self.assertAlmostEqual(float(arm.Placement.Base.z), 12.0)
            self.assertEqual(placement_result["placement"], {"x": 42.0, "y": -8.0, "z": 12.0})
            self.assertEqual(placement_result["assembly_summary"]["assemblies"][0]["components"], 1)
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_ground_component_grounds_base_and_is_idempotent(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblyGroundTest")
        try:
            doc.addObject("Part::Box", "BaseBox")
            doc.recompute()
            service = VibeCADService()
            create = service.registry.call("assembly.create_assembly", label="Ground Test")
            self.assertTrue(create["ok"], create)
            added = service.registry.call("assembly.add_component", component_name="BaseBox")
            self.assertTrue(added["ok"], added)

            grounded = service.registry.call(
                "assembly.ground_component", component_name="BaseBox"
            )
            self.assertTrue(grounded["ok"], grounded)
            self.assertFalse(grounded["already_grounded"])
            joint = doc.getObject(grounded["grounded_joint"])
            self.assertIsNotNone(joint)
            self.assertEqual(joint.ObjectToGround.Name, "BaseBox")
            self.assertEqual(grounded["grounded_components"], ["BaseBox"])

            summary = service.assembly_summary()["assemblies"][0]
            self.assertEqual(summary["grounded_count"], 1)
            self.assertEqual(summary["joints"], 0)
            self.assertEqual(summary["components"], 1)
            self.assertEqual(len(summary["joint_children"]), 1)
            self.assertTrue(summary["joint_children"][0]["grounded"])
            self.assertEqual(summary["joint_children"][0]["object_to_ground"], "BaseBox")

            again = service.registry.call(
                "assembly.ground_component", component_name="BaseBox"
            )
            self.assertTrue(again["ok"], again)
            self.assertTrue(again["already_grounded"])
            self.assertEqual(
                service.assembly_summary()["assemblies"][0]["grounded_count"], 1
            )
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_ground_component_rejects_missing_and_non_child_components(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblyGroundErrorTest")
        try:
            doc.addObject("Part::Box", "LooseBox")
            doc.recompute()
            service = VibeCADService()

            missing_assembly = service.registry.call(
                "assembly.ground_component",
                assembly_name="NoSuchAssembly",
                component_name="LooseBox",
            )
            self.assertFalse(missing_assembly["ok"], missing_assembly)
            self.assertTrue(missing_assembly["recoverable"])
            self.assertIn("assembly.create_assembly", str(missing_assembly["next_actions"]))

            create = service.registry.call("assembly.create_assembly", label="Ground Errors")
            self.assertTrue(create["ok"], create)

            missing_component = service.registry.call(
                "assembly.ground_component", component_name="NoSuchComponent"
            )
            self.assertFalse(missing_component["ok"], missing_component)
            self.assertTrue(missing_component["recoverable"])

            not_a_child = service.registry.call(
                "assembly.ground_component", component_name="LooseBox"
            )
            self.assertFalse(not_a_child["ok"], not_a_child)
            self.assertTrue(not_a_child["recoverable"])
            self.assertIn("assembly.add_component", str(not_a_child["next_actions"]))
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_create_joint_fixed_mates_displaced_boxes(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblyFixedJointTest")
        try:
            box_a = doc.addObject("Part::Box", "BoxA")
            box_b = doc.addObject("Part::Box", "BoxB")
            box_b.Placement = App.Placement(
                App.Vector(40, 50, 60), App.Rotation(45, 55, 65)
            )
            doc.recompute()
            service = VibeCADService()
            create = service.registry.call("assembly.create_assembly", label="Fixed Joint")
            self.assertTrue(create["ok"], create)
            for name in ("BoxA", "BoxB"):
                added = service.registry.call("assembly.add_component", component_name=name)
                self.assertTrue(added["ok"], added)
            grounded = service.registry.call(
                "assembly.ground_component", component_name="BoxA"
            )
            self.assertTrue(grounded["ok"], grounded)

            joint = service.registry.call(
                "assembly.create_joint",
                joint_type="Fixed",
                component1="BoxA",
                element1="Face6",
                vertex1="Vertex7",
                component2="BoxB",
                element2="Face6",
                vertex2="Vertex7",
            )
            self.assertTrue(joint["ok"], joint)
            self.assertEqual(joint["solver_return_code"], 0)
            self.assertIsNotNone(doc.getObject(joint["joint"]))
            self.assertTrue(
                box_a.Placement.isSame(box_b.Placement, 1e-6),
                (box_a.Placement, box_b.Placement),
            )
            self.assertIn("BoxA", joint["component_placements"])
            self.assertIn("BoxB", joint["component_placements"])

            summary = joint["assembly_summary"]["assemblies"][0]
            self.assertEqual(summary["components"], 2)
            self.assertEqual(summary["grounded_count"], 1)
            self.assertEqual(summary["joints"], 1)
            connecting = [
                child for child in summary["joint_children"] if not child["grounded"]
            ]
            self.assertEqual(len(connecting), 1)
            self.assertEqual(connecting[0]["joint_type"], "Fixed")
            self.assertEqual(connecting[0]["reference1"]["object"], "BoxA")
            self.assertEqual(connecting[0]["reference2"]["object"], "BoxB")
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_create_joint_revolute_aligns_cylinder_axes(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblyRevoluteJointTest")
        try:
            cyl_a = doc.addObject("Part::Cylinder", "CylA")
            cyl_b = doc.addObject("Part::Cylinder", "CylB")
            cyl_b.Placement = App.Placement(
                App.Vector(25, -10, 5), App.Rotation(30, 40, 50)
            )
            doc.recompute()
            service = VibeCADService()
            create = service.registry.call(
                "assembly.create_assembly", label="Revolute Joint"
            )
            self.assertTrue(create["ok"], create)
            for name in ("CylA", "CylB"):
                added = service.registry.call("assembly.add_component", component_name=name)
                self.assertTrue(added["ok"], added)
            grounded = service.registry.call(
                "assembly.ground_component", component_name="CylA"
            )
            self.assertTrue(grounded["ok"], grounded)

            joint = service.registry.call(
                "assembly.create_joint",
                joint_type="Revolute",
                component1="CylA",
                element1="Face1",
                component2="CylB",
                element2="Face1",
            )
            self.assertTrue(joint["ok"], joint)
            self.assertEqual(joint["solver_return_code"], 0)
            axis_a = cyl_a.Placement.Rotation.multVec(App.Vector(0, 0, 1))
            axis_b = cyl_b.Placement.Rotation.multVec(App.Vector(0, 0, 1))
            self.assertGreater(abs(axis_a.dot(axis_b)), 1.0 - 1e-6, (axis_a, axis_b))
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_create_joint_rejects_bad_type_elements_and_membership(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblyJointErrorTest")
        try:
            doc.addObject("Part::Box", "BoxA")
            doc.addObject("Part::Box", "BoxB")
            doc.addObject("Part::Box", "LooseBox")
            doc.recompute()
            service = VibeCADService()
            create = service.registry.call("assembly.create_assembly", label="Joint Errors")
            self.assertTrue(create["ok"], create)
            for name in ("BoxA", "BoxB"):
                added = service.registry.call("assembly.add_component", component_name=name)
                self.assertTrue(added["ok"], added)

            unknown_type = service.registry.call(
                "assembly.create_joint",
                joint_type="Weld",
                component1="BoxA",
                component2="BoxB",
            )
            self.assertFalse(unknown_type["ok"], unknown_type)
            self.assertIn("Fixed", unknown_type["supported_joint_types"])
            self.assertIn("Revolute", unknown_type["supported_joint_types"])

            bad_element = service.registry.call(
                "assembly.create_joint",
                joint_type="Fixed",
                component1="BoxA",
                element1="Face99",
                component2="BoxB",
                element2="Face6",
            )
            self.assertFalse(bad_element["ok"], bad_element)
            self.assertTrue(bad_element["recoverable"])
            self.assertIn("partdesign.find_subelements", str(bad_element["next_actions"]))

            malformed = service.registry.call(
                "assembly.create_joint",
                joint_type="Fixed",
                component1="BoxA",
                element1="TopFace",
                component2="BoxB",
            )
            self.assertFalse(malformed["ok"], malformed)
            self.assertTrue(malformed["recoverable"])

            same_component = service.registry.call(
                "assembly.create_joint",
                joint_type="Fixed",
                component1="BoxA",
                component2="BoxA",
            )
            self.assertFalse(same_component["ok"], same_component)

            not_a_child = service.registry.call(
                "assembly.create_joint",
                joint_type="Fixed",
                component1="BoxA",
                component2="LooseBox",
            )
            self.assertFalse(not_a_child["ok"], not_a_child)
            self.assertIn("assembly.add_component", str(not_a_child["next_actions"]))
        finally:
            App.closeDocument(doc.Name)

    def test_assembly_solve_resolves_jointed_assembly_and_guides_jointless(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADAssemblySolveTest")
        try:
            service = VibeCADService()

            missing = service.registry.call("assembly.solve")
            self.assertFalse(missing["ok"], missing)
            self.assertTrue(missing["recoverable"])
            self.assertIn("assembly.create_assembly", str(missing["next_actions"]))

            create = service.registry.call("assembly.create_assembly", label="Solve Test")
            self.assertTrue(create["ok"], create)
            doc.addObject("Part::Box", "BoxA")
            box_b = doc.addObject("Part::Box", "BoxB")
            box_b.Placement = App.Placement(
                App.Vector(40, 50, 60), App.Rotation(45, 55, 65)
            )
            doc.recompute()
            for name in ("BoxA", "BoxB"):
                added = service.registry.call("assembly.add_component", component_name=name)
                self.assertTrue(added["ok"], added)

            jointless = service.registry.call("assembly.solve")
            self.assertFalse(jointless["ok"], jointless)
            self.assertEqual(jointless["grounded_count"], 0)
            self.assertEqual(jointless["joint_count"], 0)
            actions = str(jointless["next_actions"])
            self.assertIn("assembly.ground_component", actions)
            self.assertIn("assembly.create_joint", actions)

            grounded = service.registry.call(
                "assembly.ground_component", component_name="BoxA"
            )
            self.assertTrue(grounded["ok"], grounded)
            grounded_jointless = service.registry.call("assembly.solve")
            self.assertFalse(grounded_jointless["ok"], grounded_jointless)
            self.assertEqual(grounded_jointless["grounded_count"], 1)
            actions = str(grounded_jointless["next_actions"])
            self.assertNotIn("assembly.ground_component", actions)
            self.assertIn("assembly.create_joint", actions)

            joint = service.registry.call(
                "assembly.create_joint",
                joint_type="Fixed",
                component1="BoxA",
                element1="Face6",
                vertex1="Vertex7",
                component2="BoxB",
                element2="Face6",
                vertex2="Vertex7",
            )
            self.assertTrue(joint["ok"], joint)

            # Perturb the mated component so the solve has real work to do.
            box_b.Placement = App.Placement(
                App.Vector(120, -30, 90), App.Rotation(10, 20, 30)
            )
            doc.recompute()
            solved = service.registry.call("assembly.solve")
            self.assertTrue(solved["ok"], solved)
            self.assertEqual(solved["solver_return_code"], 0)
            self.assertEqual(solved["grounded_count"], 1)
            self.assertEqual(solved["joint_count"], 1)
            placements = solved["component_placements"]
            self.assertEqual(set(placements), {"BoxA", "BoxB"})
            for key in ("x", "y", "z"):
                self.assertAlmostEqual(
                    placements["BoxA"][key], placements["BoxB"][key], places=6
                )
        finally:
            App.closeDocument(doc.Name)
