# VibeCAD Tool Runtime Verification

Updated: 2026-07-11

This record describes the current AI-native tool surface. It is not a speculative
manual checklist for deleted tools.

## Current Surface

- 115 unique provider tools.
- 21 workbench packs.
- Exact equality between registered names and core/pack-owned names.
- No provider tool can create a document or switch workbenches.
- OpenSCAD and Reverse Engineering expose no tools until native provenance exists.
- Part has no primitive-creation or arbitrary-placement escape hatch.
- PartDesign has no arbitrary property editor.
- Sketcher exposes native translation only; hand-built copy/mirror/offset/array modes
  were deleted.

## Verified In This Build

- Every tool module imports and every JSON Schema validates.
- Registry, pack, handler signature, duplicate, orphan, dangling-name, and workbench
  ownership audits pass.
- Provider failures use the common structured envelope. Schema branch errors,
  inactive-surface failures, edit-state failures, cancellation, and question UI
  failures all reach the same bounded trace path.
- FreeCAD exposes generation-scoped recompute diagnostics through
  `Document.getRecomputeDiagnostics()`.
- A live invalid PartDesign fillet reports `BREP_FILLET_FAILED`, object
  `BadFillet`, property `Base`, and subelement `Edge99`.
- Sketcher live probes pass for native profile/FaceMaker diagnostics,
  non-mutating constraint feasibility, and geometry/constraint mutation maps.
- PartDesign Hole catalog and transform occurrence/child diagnostics are native and
  queryable.
- TechDraw projected-element/source mappings and Mesh defect counts are native and
  queryable.
- Assembly solver diagnostics are native and queryable.
- Gmsh and CalculiX use asynchronous cancellable process operations with operation
  IDs and structured process/result state.
- CAM face, outside-profile, and through-drilling chains generate nonempty paths and
  native stock/collision results.
- Exact CAM circular sweeps produce valid single solids for ball-end, chamfer, and
  V-bit tools; no chord-discretization path remains.
- Python compilation and `git diff --check` pass.
- Targeted App, Part, PartDesign, Sketcher, CAM, PathSimulator, and VibeCAD builds
  pass.
- The complete incremental build passes, including all application, workbench, and
  native-test targets (615 of 615 remaining actions).

## Environment-Dependent Checks

These are external-state checks, not alternate code paths:

- A real Gmsh installation is required to complete a production volume mesh.
  Missing Gmsh must fail before a mesh object is claimed complete.
- A real CalculiX installation is required to complete a solve. Missing CalculiX
  must fail before a solve is claimed started.
- Holder and fixture collision checks are unavailable when the CAM job contains no
  holder or fixture geometry; the result reports each unavailable check explicitly.
- Screenshot capture and panel rendering require a GUI process and should be checked
  in the normal VibeCAD application after visual changes.
- Machine postprocessing remains a human-controlled CAM export action and is not a
  provider tool.

## Release Gate

Before a release, require:

1. A complete incremental build with no errors.
2. Clean FreeCADCmd startup and VibeCAD registry initialization.
3. The live Sketcher, recompute-diagnostic, and exact CAM sweep probes above.
4. GUI startup without VibeCAD Python errors.
5. Provider SDK/keyring smoke tests inside each packaged artifact.
