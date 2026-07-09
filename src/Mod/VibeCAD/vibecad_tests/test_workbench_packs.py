# SPDX-License-Identifier: LGPL-2.1-or-later


from VibeCADCore import (
    VibeCADService,
)
from VibeCADSession import (
    CORE_PROVIDER_TOOLS,
)
from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS, get_tool_pack

from vibecad_tests.support import (
    SettingsSnapshotTestCase,
    _gui_workbench_api_available,
)


class TestVibeCADWorkbenchPacks(SettingsSnapshotTestCase):
    def test_workbench_command_summary_uses_tool_pack_prefixes(self):
        service = VibeCADService()
        summary = service.workbench_command_summary("PartWorkbench")
        self.assertEqual(summary["active_workbench"], "PartWorkbench")
        self.assertEqual(summary["command_prefixes"], ["Part_"])
        self.assertIn("commands", summary)
        self.assertIn("command_limit", summary)
        self.assertIn("commands_truncated", summary)
        self.assertIn("commands_omitted", summary)
        self.assertLessEqual(len(summary["commands"]), summary["command_limit"])

    def test_workbench_object_templates_are_exposed(self):
        service = VibeCADService()
        summary = service.workbench_object_templates("PartWorkbench")
        self.assertIn({"name": "box", "object_type": "Part::Box"}, summary["templates"])

    def test_workbench_object_summary_filters_by_pack(self):
        import FreeCAD as App

        doc = App.newDocument("VibeCADObjectSummaryTest")
        try:
            box = doc.addObject("Part::Box", "BoxForSummary")
            group = doc.addObject("App::DocumentObjectGroup", "GroupForSummary")
            doc.recompute()
            service = VibeCADService()
            part_summary = service.workbench_object_summary("PartWorkbench")
            sketcher_summary = service.workbench_object_summary("SketcherWorkbench")
            self.assertIn(box.Name, [item["name"] for item in part_summary["objects"]])
            self.assertNotIn(group.Name, [item["name"] for item in part_summary["objects"]])
            self.assertEqual(sketcher_summary["objects"], [])
        finally:
            App.closeDocument(doc.Name)

    def test_workbench_tool_packs_cover_integrated_workbenches(self):
        expected = {
            "AssemblyWorkbench",
            "BIMWorkbench",
            "CAMWorkbench",
            "DraftWorkbench",
            "FemWorkbench",
            "InspectionWorkbench",
            "MaterialWorkbench",
            "MeshWorkbench",
            "MeshPartWorkbench",
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
            "TestWorkbench",
        }
        self.assertEqual(expected, set(WORKBENCH_TOOL_PACKS))
        self.assertEqual(get_tool_pack("PartWorkbench").domain, "boundary-representation solids")
        for pack in WORKBENCH_TOOL_PACKS.values():
            self.assertGreater(len(pack.object_templates), 0, pack.workbench)

    def test_workbench_tool_pack_tool_names_exist_in_provider_registry(self):
        from provider_tools import registered_tool_names

        registered = registered_tool_names()
        missing = []
        for pack in WORKBENCH_TOOL_PACKS.values():
            for tool_name in pack.provider_tool_names():
                if tool_name not in registered:
                    missing.append((pack.workbench, tool_name))
        self.assertEqual(missing, [])

    def test_provider_registry_service_registry_and_packs_are_consistent(self):
        from provider_tools import (
            all_tool_names,
            provider_only_tool_names,
            registered_tool_names,
        )

        provider_names = set(registered_tool_names())
        provider_only_names = set(provider_only_tool_names())
        service = VibeCADService()
        service_names = set(service.registry.names())

        self.assertEqual({"core.submit_design_preflight"}, provider_only_names)
        self.assertTrue(provider_only_names.isdisjoint(provider_names))
        self.assertEqual(provider_names | provider_only_names, set(all_tool_names()))

        # Every normal provider-exposed tool must have a service implementation.
        self.assertEqual(set(), provider_names - service_names)

        # Service-only tools (no provider surface) are a small known set.
        service_only = service_names - provider_names
        allowed_service_only = {
            "core.clear_local_session",
            "core.create_new_document",
            "core.delete_object",
            "core.get_active_document",
            "core.get_active_workbench_tool_pack",
            "core.get_object_properties",
            "core.get_selection",
            "core.get_task_panel",
            "core.get_tool_shape_report",
            "core.get_view_state",
            "core.list_active_workbench_commands",
            "core.list_registered_commands",
            "core.list_workbench_object_templates",
            "core.list_workbench_tool_packs",
            "core.list_workbenches",
            "core.open_document",
            "core.report_tool_shape_gap",
            "core.undo_last_vibecad_action",
            "core.wait_for_user_gui_action",
        }
        self.assertEqual(set(), service_only - allowed_service_only, sorted(service_only))

        # Every pack tool and every core provider tool is in the provider registry.
        pack_union = {
            tool_name
            for pack in WORKBENCH_TOOL_PACKS.values()
            for tool_name in pack.provider_tool_names()
        }
        self.assertEqual(set(), pack_union - provider_names, sorted(pack_union - provider_names))
        self.assertEqual(
            set(),
            set(CORE_PROVIDER_TOOLS) - provider_names,
            sorted(set(CORE_PROVIDER_TOOLS) - provider_names),
        )

    def test_native_pack_tools_belong_to_that_workbench(self):
        service = VibeCADService()
        offenders = []
        adjacent_offenders = []
        for workbench, pack in WORKBENCH_TOOL_PACKS.items():
            for tool_name in pack.tool_names:
                tool = service.registry.get(tool_name)
                owner = tool.workbench
                if owner != workbench:
                    offenders.append((workbench, tool_name, owner))
            for tool_name in pack.required_adjacent_tool_names:
                tool = service.registry.get(tool_name)
                owner = tool.workbench
                if owner == workbench:
                    adjacent_offenders.append((workbench, tool_name, owner))
        self.assertEqual([], offenders)
        self.assertEqual([], adjacent_offenders)

    def test_workbench_tool_pack_summary_includes_tool_names(self):
        pack = get_tool_pack("SketcherWorkbench")
        summary = pack.summary()
        self.assertIn("tool_names", summary)
        self.assertIn("required_adjacent_tool_names", summary)
        self.assertIn("provider_tool_names", summary)
        self.assertIn("sketcher.add_geometry", summary["tool_names"])
        self.assertIn("sketcher.add_constraint", summary["tool_names"])

    def test_partdesign_pack_includes_sketcher_tools_but_not_create_sketch(self):
        pack = get_tool_pack("PartDesignWorkbench")
        self.assertNotIn("sketcher.add_geometry", pack.tool_names)
        self.assertIn("sketcher.add_geometry", pack.required_adjacent_tool_names)
        self.assertIn("sketcher.add_geometry", pack.provider_tool_names())
        self.assertIn("partdesign.extrude", pack.tool_names)
        self.assertNotIn("sketcher.create_sketch", pack.provider_tool_names())
        self.assertIn("partdesign.create_sketch", pack.tool_names)

    def test_cam_pack_covers_machine_validated_machining_workflow(self):
        pack = get_tool_pack("CAMWorkbench")
        for tool_name in (
            "cam.define_machine",
            "cam.create_job",
            "cam.add_tool",
            "cam.create_operation",
            "cam.validate_job",
            "cam.postprocess",
        ):
            self.assertIn(tool_name, pack.tool_names, tool_name)
        self.assertNotIn("partdesign.find_subelements", pack.tool_names)

    def test_assembly_pack_covers_kinematic_mating_workflow(self):
        pack = get_tool_pack("AssemblyWorkbench")
        # Container/layout tools.
        self.assertIn("assembly.create_assembly", pack.tool_names)
        self.assertIn("assembly.add_component", pack.tool_names)
        self.assertIn("assembly.set_component_placement", pack.tool_names)
        self.assertIn("assembly.check_interference", pack.tool_names)
        # Kinematic mating: ground one base component, mate, solve.
        self.assertIn("assembly.ground_component", pack.tool_names)
        self.assertIn("assembly.create_joint", pack.tool_names)
        self.assertIn("assembly.solve", pack.tool_names)
        self.assertNotIn("partdesign.find_subelements", pack.tool_names)
        self.assertIn("partdesign.find_subelements", pack.required_adjacent_tool_names)
        self.assertIn("partdesign.find_subelements", pack.provider_tool_names())
        # The assembly pack is not a modeling pack.
        self.assertNotIn("partdesign.extrude", pack.tool_names)
        self.assertNotIn("sketcher.add_geometry", pack.tool_names)

    def test_surface_pack_covers_surface_to_solid_workflow(self):
        pack = get_tool_pack("SurfaceWorkbench")
        self.assertIn("surface.create_surface", pack.tool_names)
        self.assertIn("draft.create_wire", pack.required_adjacent_tool_names)
        self.assertIn("part.thicken_surface", pack.required_adjacent_tool_names)
        self.assertIn("partdesign.find_subelements", pack.required_adjacent_tool_names)
        self.assertIn("draft.create_wire", pack.provider_tool_names())
        self.assertIn("part.thicken_surface", pack.provider_tool_names())
        self.assertIn("partdesign.find_subelements", pack.provider_tool_names())
        self.assertNotIn("partdesign.extrude", pack.provider_tool_names())
        self.assertNotIn("sketcher.add_geometry", pack.provider_tool_names())

    def test_non_modeling_packs_do_not_expose_modeling_tools(self):
        for workbench in ("FemWorkbench", "MeshWorkbench"):
            pack = get_tool_pack(workbench)
            self.assertEqual((), pack.tool_names, workbench)
        # The CAM pack carries machining tools but no modeling tools.
        cam_pack = get_tool_pack("CAMWorkbench")
        self.assertNotIn("partdesign.extrude", cam_pack.tool_names)
        self.assertNotIn("sketcher.add_geometry", cam_pack.tool_names)

    def test_runtime_workbenches_have_tool_packs(self):
        if not _gui_workbench_api_available():
            self.skipTest("FreeCAD GUI workbench API unavailable")
        try:
            import FreeCADGui as Gui
        except Exception:
            self.skipTest("FreeCADGui unavailable")
        runtime_workbenches = set(Gui.listWorkbenches())
        missing = runtime_workbenches.difference(WORKBENCH_TOOL_PACKS)
        self.assertEqual(set(), missing)
