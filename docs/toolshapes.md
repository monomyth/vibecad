# VibeCAD Tool Shapes Audit

## Remediation status

The findings below are the pre-remediation baseline. The current provider surface has
**115 tools across 21 workbench surfaces**. The count fell from 120 because unsafe tools
were deleted rather than hidden, and the broad Sketcher transform tool was replaced by one
native translation operation.

All findings in this audit have now been acted on:

| Area | Current contract |
| --- | --- |
| Provider dispatch | Every attempted call, including JSON, schema, surface, edit-state, cancellation, and question-UI rejection, reaches one structured failure envelope and one bounded trace path. Tool-specific facts are retained under `observed`; truncation is explicit. |
| Transactions | Every write reports the actual transaction lifecycle and retained document delta. Failed mutations are never described as rolled back or cleaned up. |
| FreeCAD diagnostics | `Document.getRecomputeDiagnostics()` exposes generation-scoped stable codes, object, property/subelement, algorithm, severity, and message. VibeCAD no longer scrapes Report View. |
| Sketcher | Native profile/FaceMaker diagnostics, non-mutating constraint feasibility, internal B-spline geometry attribution, and geometry/constraint mutation maps are exposed and consumed. The redundant custom deep-profile classifier was deleted. |
| PartDesign and Part | Profile/support/extent/axis variants are discriminated; selection is count guarded; exact native state is read back; unknown validity is failure. Hole catalogs and transform occurrence/child diagnostics come from native APIs. Native dress-up code no longer skips invalid requested edges/faces. |
| Draft, Spreadsheet, Surface | Inputs are preflighted, partial writes and per-field failures are explicit, requested properties are read back, and required counts/parameter bounds cannot silently become unknown. |
| Assembly and BIM | Component identity, source/container membership, local/global placement, hierarchy, host opening effects, joint references, and native solver diagnostics are verified. Missing native structure is not auto-created as a compatibility path. |
| TechDraw, Material, Mesh | Projected geometry/source maps, material UUID/property readback, appearance subsets, complete mesh defect status, repair deltas, and conversion postconditions are structured. |
| FEM | Gmsh and CalculiX run through asynchronous, cancellable native process APIs with operation IDs, process diagnostics, prerequisite checks, and solve-generation result attribution. |
| CAM | Jobs, stock, tools, controllers, and operation properties are exact and read back. Native generation diagnostics and queryable stock/collision simulation are required. Circular cutter sweeps use exact OCC geometry; no chord-discretization fallback remains. |

Deleted provider affordances: `part.create_primitive`, `partdesign.edit_feature`,
`part.set_placement`, non-translation `sketcher.transform_geometry`,
`openscad.list_csg`, and `reveng.list_candidates`. There are no compatibility aliases for
them.

Verification completed during remediation:

- Registry import: 115 unique tools, 21 packs, exact pack/registry set equality, no orphan,
  dangling, duplicate, or workbench-ownership mismatch.
- Native live probes: Sketcher profile/feasibility/mutation APIs; exact
  `BREP_FILLET_FAILED` diagnostics with `Base/Edge99`; exact ball-end, chamfer, and V-bit
  circular sweeps; CAM face/profile/drilling chains; TechDraw, Mesh, FEM, and provider trace
  contracts.
- Targeted native builds for App, Part, PartDesign, Sketcher, Path, PathSimulator, and
  VibeCAD completed successfully. A complete incremental build then completed all 615
  remaining application, workbench, and native-test actions with no errors.

Independent post-remediation review (second engineer) re-verified the shipped surface
against a live FreeCADCmd build through the real provider runner path
(`make_provider_tool_runner`), not direct handler calls:

- Registry equality re-confirmed: 115 registered tools == 115 surfaced tools across the
  21 packs, no orphans, no dangling names; deleted affordances have no aliases.
- Envelope contract re-confirmed live for every early-failure class: unknown tool,
  inactive surface, JSON-schema failure with branch errors and selected discriminants,
  `conversation.ask_user` validated through its own `ToolSpec`, question-UI-unavailable,
  and cancellation — each returned the common envelope and produced exactly one trace
  entry.
- Retained-failure contract re-confirmed live: an impossible PartDesign fillet returned
  `BREP_FILLET_FAILED` at `native_recompute` with the full transaction lifecycle
  (`transaction_opened/mutation_started/commit_attempted/commit_succeeded`), the retained
  feature in `created_objects`/`repair_targets`, and generation-scoped native diagnostics
  that matched the document's actual retained state.
- Native remediation APIs re-confirmed live: `Document.getRecomputeDiagnostics()`
  (exact `BREP_FILLET_FAILED`/`Base`/`Edge99`), Sketcher `delGeometry` mutation maps,
  structured `exposeInternalGeometry` with per-geometry roles, non-mutating
  `diagnoseAdditionalConstraints`, `getProfileDiagnostics`, Assembly
  `getSolverDiagnostics`, TechDraw `getProjectedElementDescriptors`, Mesh defect counts,
  and the open-profile `ok=true, incomplete_edit=true` contract replacing the deleted
  text heuristic.
- Cross-pack execution smoke: Part boolean/fillet/measure/find_subelements, Draft,
  Sketcher end-to-end (body → sketch → rectangle → pad → hole), Spreadsheet batch
  write/read with partial-prefix failure reporting, TechDraw page/view with projected
  descriptors, Material apply with UUID readback, Mesh/MeshPart round trip with
  `actual_solid_count` readback, Assembly insert/ground/joint/solve with per-joint
  diagnostics, FEM analysis creation, and CAM job creation all executed correctly.
- One real defect was found and fixed during this review: the C++ module exports
  `getHoleThreadCatalog` on `_PartDesign`, but `src/Mod/PartDesign/__init__.py` never
  re-exported it, so every `partdesign.hole` call failed with
  `HOLE_THREAD_CATALOG_UNAVAILABLE` while direct `_PartDesign` probes passed. The shim
  now re-exports it and the full `partdesign.hole` chain succeeds end to end.
- Native regression check: 257 Sketcher and PartDesign application tests pass against
  the modified native code.

## Baseline scope and verdict

This audit covers every provider-visible tool in the authoritative registry and every
configured workbench surface as of 2026-07-11.

- Provider-visible tools audited: **120 of 120**
- Workbench surfaces audited: **21 of 21**
- Tools with a tool-specific defect or removal recommendation: **89**
- Tools omitted from the findings: **31**. Omission means the tool-specific schema,
  validation, native binding, and result contract were coherent enough for its current
  scope. Omission does not exempt a tool from the platform-wide findings below.
- Runtime used for native binding checks: FreeCAD 26.3.0 dev, revision 20260710.

The runtime probe successfully imported every module used by the tools and created every
claimed native object type. Sampled native properties for PartDesign, Surface, TechDraw,
Assembly, Hole, CAM, FEM, Materials, MeshPart, and Sketcher were present. The central
problem is therefore not that the wrappers point at nonexistent APIs. The problem is that
many wrappers expose a valid native symbol through an incomplete semantic contract, ignore
unsupported behavior, or cannot explain why the native operation failed.

## Baseline provider surface

All 120 tools are registered unconditionally, then filtered for the active workbench and
edit mode. Raw FreeCAD command wrappers are not currently provider-visible. The 33 tools in
the PartDesign pack are VibeCAD wrappers, but several are still too close to raw FreeCAD
properties or offer an unsafe shortcut and should be deleted as noted below.

PartDesign does **not** send its raw 56-tool union (5 global + 33 PartDesign + 18
required-adjacent Sketcher tools) on one turn:

- Normal PartDesign state: 5 global tools + 33 PartDesign tools = **38**.
- While a PartDesign-owned sketch is open: 4 edit-compatible global tools + 18 Sketcher
  tools + `partdesign.find_subelements` = **23**. `core.delete_object` is correctly
  excluded while the sketch is open. `partdesign.find_subelements` remains visible
  because READ tools that declare no `edit_modes` default to every edit mode, while
  its sibling `partdesign.measure` declares `edit_modes: ["none"]` and is excluded —
  an undocumented asymmetry the surface contract should state deliberately.

The registry/workspace inventory is:

| Surface | Registered tools | Tool-specific findings |
| --- | ---: | ---: |
| Global | 5 | 4 |
| Sketcher | 18 | 10 |
| PartDesign | 33 | 27 |
| Part | 10 | 9 |
| Draft | 7 | 5 |
| Spreadsheet | 3 | 2 |
| Surface | 5 | 5 |
| Assembly | 6 | 4 |
| BIM | 5 | 4 |
| TechDraw | 5 | 3 |
| Material | 3 | 2 |
| Mesh | 3 | 2 |
| MeshPart | 2 | 2 |
| FEM | 6 | 5 |
| CAM | 4 | 3 |
| Inspection | 1 | 0 |
| OpenSCAD | 1 | 1 |
| Points | 1 | 0 |
| Reverse Engineering | 1 | 1 |
| Robot | 1 | 0 |

`NoneWorkbench` and `TestWorkbench` expose only the five global tools and add no native
workbench tools.

## Required misuse response

Every rejected or failed call should return one stable structured envelope. Free-form
`error` text remains useful for display, but it cannot be the API contract.

```json
{
  "ok": false,
  "tool": "partdesign.chamfer",
  "failure_code": "BREP_DRESSUP_FAILED",
  "failure_stage": "native_recompute",
  "requested": {},
  "normalized": {},
  "observed": {},
  "candidates": [],
  "allowed_values": [],
  "state_change": {
    "mutation_started": true,
    "changed": true,
    "retained": true,
    "created_objects": [],
    "changed_objects": [],
    "deleted_objects": [],
    "repair_targets": []
  },
  "native_diagnostics": [],
  "retry": {
    "same_call": false,
    "required_changes": []
  },
  "error": "Human-readable summary"
}
```

Required semantics:

1. `failure_code` is stable and machine-actionable. It is not parsed from prose.
2. `failure_stage` is one of `schema`, `surface`, `edit_state`, `precondition`,
   `native_call`, `native_recompute`, `postcondition`, or `external_process`.
3. `requested` preserves the caller's values. `normalized` shows exactly what FreeCAD
   received, including units and resolved object/subelement references.
4. `observed` contains only facts that caused the rejection: current object type, Body
   ownership, active Tip, shape validity, solver state, selected geometry, or process exit.
5. `candidates` contains the live alternatives needed to correct a reference. Do not dump
   unrelated document state.
6. `state_change` is mandatory. VibeCAD intentionally retains failed native mutations, so
   the response must say exactly what changed and what object must be repaired or deleted.
7. `native_diagnostics` contains only diagnostics produced by this operation/recompute.
8. `retry.required_changes` states the smallest factual correction. It must not offer a
   guessed redesign or generic list of possible causes.

## Platform-wide findings

### P1. JSON Schema failures are flattened

**Source:** `VibeCADTools.ToolSpec.validate_arguments`

The validator selects one leaf error and raises one `ValueError` string. The provider loses
the JSON path array, failed validator, expected value, received value, and the other branch
errors from `oneOf` schemas. This is why a discriminated request can receive a misleading
single-field message.

**Required change:** return `failure_code=SCHEMA_VALIDATION_FAILED`, the full path, validator,
expected/received values, selected discriminant, and concise branch errors. Set
`state_change.changed=false` before dispatch.

### P2. Early runner failures are invisible to the normal trace

**Source:** `VibeCADSession.make_provider_tool_runner`

Cancellation, unknown-tool, inactive-surface, JSON parsing, edit-mode, and question-UI
failures return before the common trace/event path. The model receives sparse text, while
the activity/debug trace may contain no record of the rejected call.

**Required change:** one finalization path must trace and emit every attempt. Surface failures
must include active workbench, edit mode, and the exact currently available tool names.
Edit-state failures must include the active edit object and the action the human must take.

### P3. `conversation.ask_user` bypasses its own schema

The runner special-cases this tool before `registry.call`, validating only that `questions`
is a non-empty list. Malformed nested questions/options can reach the UI. The registered
handler has a different behavior and always reports that an interactive panel is required.

**Required change:** validate through the same `ToolSpec` first, then invoke the UI adapter.
Delete the divergent dead handler path. Return the failing question/option index and preserve
the completed answers if a later question fails.

### P4. Rich tool results are stripped from activity/debug traces

**Source:** `VibeCADSession._trace_result`

The trace retains only a few scalar keys. Candidate geometry, native diagnostics, solver
status, normalized references, and retained-mutation details disappear even when the full
provider result contains them.

**Required change:** store a bounded structured failure summary using the common envelope.
Keep candidate lists and native diagnostics with explicit item/byte limits and truncation
metadata.

### P5. Retained failures do not have an explicit mutation contract

**Source:** `VibeCADTransactions.run_freecad_transaction`

Retaining failed FreeCAD state is intentional. The current responses nevertheless vary
between `failed_feature_retained`, `document_delta`, nested `transaction`, and no indication
at all. `committed_transaction` can also be true when no transaction was opened.

**Required change:** emit the common `state_change` object for every write. Distinguish
`transaction_opened`, `mutation_started`, `commit_attempted`, `commit_succeeded`, and
`document_changed`. Never label a failed native object as cleanup or rollback.

### P6. Report View scraping is the error source of record

**Source:** `VibeCADTransactions.report_view_error_summary`

The implementation searches Qt text widgets, keeps line-count cursors, and parses text.
This is fragile across GUI layouts, localization, headless runs, widget recreation, and
multi-document operation. It cannot reliably associate an error with one object/property.

**Required FreeCAD change:** expose per-document, per-recompute diagnostics through App and
Python. Each diagnostic needs generation ID, severity, stable code, object, property or
subelement, originating feature/algorithm, and message. VibeCAD should consume that API and
delete Report View scraping.

### P7. Sketch face errors are converted from failure to success heuristically

**Source:** `tool_impl/sketcher/common.py::_is_committed_open_profile_face_noise`

If Report View text resembles a known FaceMaker message and the current custom profile
classifier sees an open wire, `active_response` changes the failed transaction to `ok=true`.
This hides whether the native message was harmless intermediate state or a real corruption.

**Required change:** remove the text heuristic after FreeCAD exposes structured sketch
profile diagnostics. Until then, return `ok=false` or a distinct `incomplete_edit=true` state;
never rewrite a native failure into success.

## Global tool findings

### `core.delete_object`

The deletion result sets `ok=true` when the object is gone and commit did not throw, even if
recompute failed or new Report View errors show that the remaining document is invalid — the
response carries `recompute_error` and `native_errors` fields, but the verdict ignores them.
`committed_transaction` is inferred from `opened is false`, which is also true when no native
transaction was opened. Missing-object failure returns no live object candidates. The tool
already returns incoming/outgoing references before deletion and the owning Body's state
before/after; those parts of the contract are sound.

**Required response/change:** the success verdict must incorporate recompute health and the
new Report View errors already being collected. Add every object changed by native cascade
and any retained invalid state to the existing before/after reference data. Report
transaction lifecycle fields explicitly instead of inferring them from one boolean, and
return live object candidates when the exact name is missing.

### `core.set_view`

Camera validation is strong, but the operation applies visibility, camera, sketch
annotations, framing, and zoom sequentially inside one try block. If a later stage fails,
earlier view mutations (visibility changes already applied via `_apply_visibility`, the
camera already set) remain while the failure returns only `{ok:false, error}` with no
record of which stages executed. Missing show/hide object failures list the missing names
but omit the otherwise available live object candidates.

**Required response:** `failure_stage`, viewport state before/after, exact visibility
changes, camera requested/resolved/effective, framing targets/candidates, and whether each
stage was applied. A partially changed viewport is a retained state change even though no
CAD document geometry changed.

### `core.capture_view_screenshot`

The capture can change the persistent camera before file save, pixel fingerprinting, or visual
observation fails. A PNG may exist even when a later exception returns `captured=false`, and
the failure does not report the camera change or artifact path. Duplicate detection is useful
but should not erase this stage information.

**Required response:** capture stage, camera before/after, temporary versus persistent view
changes, artifact-created/path/size state, save error, fingerprint error, and visual-observation
error as separate fields. If the file exists, report it even when later analysis failed.

## Delete instead of repair

These tools or modes create the wrong model affordance. Removing them means deleting their
pack entry, registry entry, implementation, and dead helpers. Do not keep hidden aliases or
compatibility fallbacks.

| Tool or mode | Reason |
| --- | --- |
| `part.create_primitive` | It is the highest-leverage shortcut and has repeatedly pulled the model toward box/cylinder compositions instead of intentional parametric form. The Part workbench can remain an inspection/direct-BREP workspace without giving the model this default authoring escape hatch. |
| `partdesign.edit_feature` | It exposes arbitrary FreeCAD property names and link properties. Type checking cannot enforce operation-specific relationships, ownership, DAG safety, or design semantics. Replace only proven edit needs with focused typed operations. |
| `part.set_placement` in its current form | It claims to set global placement but writes the object's local `Placement`, which is not global inside App::Part, Assembly, Link, or other containers. Delete it unless replaced by an explicitly local, container-aware operation with verified global result. |
| Non-translate modes of `sketcher.transform_geometry` | Copy, mirror, offset, and array are hand-built geometry copies. They do not preserve the native constraints, expressions, or source-to-result relationships claimed by an AI-native parametric transform. Retain native translation only until FreeCAD exposes constraint-preserving transforms. |
| `openscad.list_csg` current implementation | It classifies every Part and Mesh object as OpenSCAD-related, so the output is not an OpenSCAD CSG tree. Remove it from the pack until native import provenance/CSG links are used. |
| `reveng.list_candidates` current output classifier | It decides that Part objects are reconstructed outputs from label/name tokens such as `fit`, `approx`, `segment`, and `spline`. Remove the heuristic classifier until outputs are identified by native type/proxy/provenance. |

## Sketcher findings

### `sketcher.add_spline`

The original concern here — that native `addGeometry` silently adds dependent internal
geometry — is not what the code does: `SketchObject::addGeometry` appends exactly one
geometry and returns its index, so the tool's `geometry_added=1` is truthful at creation
time. The real gaps are adjacent. The tool never calls or exposes
`exposeInternalGeometry`, so control poles, knot points, and alignment construction
geometry cannot be enumerated or constrained — B-spline shape control through constraints
is effectively unavailable. And native operations elsewhere (GUI B-spline creation, knot
insertion, and the curve joins behind `sketcher.modify_geometry`) do call
`exposeInternalGeometry`, injecting construction geometry that no handle table attributes
back to its owning spline.

**Required response/change:** expose internal-alignment geometry deliberately: primary
geometry handle plus every internal handle with its role (control pole, knot helper,
alignment) whenever internal geometry exists or is created. **Required FreeCAD change:**
`exposeInternalGeometry` and the operations that call it should return the created
geometry IDs with stable roles instead of only a status code.

### `sketcher.constrain`

The batch is structurally validated before mutation, which is good, but native solver
feasibility is not. A syntactically valid batch can become conflicting or redundant after it
is retained. The failure should identify the exact introduced constraint and conflict set.

**Required response:** batch item index, generated native constraint, affected DoF, conflicting
and redundant indices before/after, and retained indices. **Required FreeCAD change:** expose a
non-mutating constraint feasibility check or structured solver result from batch insertion.

### `sketcher.edit_constraint`

The schema allows action fields and target selectors in one broad object. Runtime resolution
is better than the schema, but invalid requests are taught through trial and error.

**Required change:** use one discriminated schema per action and require exactly one target
selector. On failure return the resolved target, native property status, allowed values, and
the live constraint table already available to the implementation.

### `sketcher.move_point`

Native `moveGeometry` can be constrained into a no-op or move to a solver-selected position.
The tool returns success without comparing requested and actual coordinates.

**Required response:** requested point, resolved handle/role, before point, after point,
displacement error, DoF change, and `effect_applied`. A constrained no-op is a postcondition
failure, not success.

### `sketcher.modify_geometry`

Trim, split, extend, and fillet use native Sketcher methods, but those methods do not return
an authoritative created/deleted/renumbered geometry and constraint map. The implementation
infers effects from counts and indices. Its endpoint-ambiguity response is the model to keep.

**Required FreeCAD change:** every modifying Sketcher operation should return geometry and
constraint old-to-new maps plus deleted/created IDs. VibeCAD must return those maps and the
actual selected pick locations.

### `sketcher.delete_items`

Geometry deletion can cascade-delete constraints. The tool returns a geometry index map but
does not report the actual constraint cascade; the native deletion API does not return it.

**Required response:** requested handles, resolved IDs, deleted geometry, cascade-deleted
constraints, and old-to-new maps for both collections. **Required FreeCAD change:** return
the cascade map from `delGeometry`/`delConstraint`.

### `sketcher.add_external_geometry` and `sketcher.remove_external_geometry`

Add validates a subelement but discards the geometric summaries it already computed when the
reference is wrong. Remove reports only an index/count and not the live external-reference
table. Neither failure identifies an exact correction.

**Required response:** source object facts, requested subelement, available subelements with
type/center/size, live external references, resolved external index, and state unchanged.

### `sketcher.measure`

Reference resolution failures are flattened. The model cannot tell whether the first or
second reference failed, which semantic point role was invalid, or which roles/handles are
available.

**Required response:** `reference_side`, requested and resolved handle, allowed point roles,
geometry summary, and live alternatives.

### `sketcher.transform_geometry`

Translation is a native `moveGeometry` call. Copy, mirror, offset, and array construct new
Part geometry manually and lose constraints/expressions. Offset supports only a hand-coded
subset of geometry. Those modes are deletion candidates above.

## PartDesign findings

### Profile-driven features

**Tools:** `partdesign.pad`, `partdesign.pocket`, `partdesign.revolution`,
`partdesign.groove`, `partdesign.additive_loft`, `partdesign.subtractive_loft`,
`partdesign.additive_pipe`, `partdesign.subtractive_pipe`,
`partdesign.additive_helix`, `partdesign.subtractive_helix`, `partdesign.hole`.

All depend on the custom `_sketch_profile_status` classifier. It builds faces one wire at a
time and does not authoritatively validate combined nesting, section correspondence, profile
orientation, path continuity, path branching, profile/path intersection, or sweep
self-intersection. Loft and pipe failures therefore collapse to a native error after the
feature exists. Revolution/groove `object_edge` validates existence but not that the edge is
linear. Linear/revolution extent schemas are broad objects rather than discriminated variants.

Hole has an additional dynamic-enum problem: thread sizes/classes/fits become available only
after `ThreadType` is set on a native Hole. The current call can create the Hole and fail while
setting an unavailable enum. It also cannot explain which profile entities became hole
locations.

**Required response:** exact profile wire diagnostics; ordered section/path facts; axis and
target-face geometry; requested/native parameters; native algorithm stage; retained feature;
and a factual correction such as an open vertex pair, branched path edge, non-linear axis, or
unavailable thread choice. Do not return generic "likely" causes.

**Required FreeCAD changes:**

- Expose structured sketch wire/face diagnostics: open vertices and gap distances, wire order,
  self-intersections, nesting, support plane, and FaceMaker status.
- Expose loft/pipe/helix preflight diagnostics: incompatible sections, correspondence,
  discontinuous or branched spine, self-intersection, and failing section/edge.
- Expose Hole thread catalog data independently of a document feature.

### Pattern and mirror features

**Tools:** `partdesign.linear_pattern`, `partdesign.polar_pattern`,
`partdesign.mirror`, `partdesign.multi_transform`.

Reference resolution is native and source ownership is checked, but exact edge/face references
remain topology-fragile and no count-guarded query is accepted here. Success proves a body
effect, not that the requested occurrence count was generated or that every occurrence fused
or cut as intended. Multi-transform also cannot report which child transform failed.

**Required response:** resolved geometric reference, source features, per-transform child
status, requested versus produced occurrence count, skipped/overlapping occurrences, and the
first failing child. **Required FreeCAD change:** expose transformed occurrence results and
per-child MultiTransform diagnostics.

### Dress-up and boolean features

**Tools:** `partdesign.fillet`, `partdesign.chamfer`, `partdesign.draft`,
`partdesign.thickness`, `partdesign.boolean`.

The count-guarded query selection is the strongest pattern in the codebase. Once the selected
geometry reaches OpenCascade, however, failures such as `BRep_API: command not done` or an
invalid edge link have no structured explanation. The retained feature response cannot say
whether the size is too large, the edge chain is discontinuous, the offset self-intersects,
or the Boolean operands do not overlap.

**Required response:** requested and resolved selection, source shape validity, operation
parameters, failing native stage, retained feature, and exact offending subelement(s). For a
query-count mismatch, preserve the current candidate-rich behavior.

**Required FreeCAD change:** expose BRep builder status/error codes and offending input
subshapes for fillet, chamfer, draft, thickness/offset, and Boolean algorithms.

### `partdesign.create_sketch`

The face query is count-guarded, but the outer schema combines fields for three support modes
instead of using a discriminated union. Success does not verify that `AttachmentSupport`,
`MapMode`, Body ownership, and native support state survived recompute.

**Required change:** discriminate support variants and return requested/resolved support,
actual attachment, global sketch plane, native state, and exact Body membership. A failed
attachment must identify candidate support geometry and the retained sketch.

### `partdesign.create_shape_binder` and `partdesign.create_subshape_binder`

References are exact topology names only, available geometry is not returned on misuse, and
the preflight does not reject a reference that creates a dependency cycle or points forward
in the same Body history.

**Required response:** source owner/history position, resolved subshape summaries, dependency
direction, cycle check, and actual binder support after recompute. Add count-guarded query
selection for subshapes.

### `partdesign.find_subelements` and `part.find_subelements`

The core result is useful, but the direct schema permits negative tolerances, inverted min/max
ranges, and out-of-range angle tolerances that the dress-up wrapper rejects. `near_point`
means center-of-mass proximity, not nearest geometric distance; the schema description does
state this, but center-of-mass distance diverges badly from surface distance for large or
concave faces, so the metric choice itself is the trap. Missing-object failures return no
shaped-object candidates.

**Required change:** use the same bounded predicate schema everywhere; reject contradictory
ranges; compute native closest distance (or add it alongside the center-of-mass filter);
return current shaped-object candidates when the exact object is missing.

### `partdesign.measure` and `part.measure`

Reference resolution is layered and mostly sound (datum axes/planes/points and vertices get
analytic paths; only bounded shapes reach OpenCascade), but when the native call throws, the
failure is flattened to `FreeCAD could not measure the requested distance: BRepExtrema_DistShapeShape failed`.
The response drops the already-resolved first/second reference summaries and shape
validity/bounds that `_measure_distance` had in hand, which would distinguish a bad
reference from an algorithm error.

**Required response:** both resolved references, reference kinds, shape validity/bounds,
calculation path, failing BRep stage, and whether any partial extrema were found.
**Required FreeCAD change:** expose `BRepExtrema_DistShapeShape` completion/status codes and
failure details through the Python shape-distance API (`TopoShapePyImp` raises a bare
`RuntimeError` today).

### `partdesign.set_tip`

The tool says only a solid feature can become Tip, but validation checks only a PartDesign type
and the existence of a `Shape` attribute. It can select an invalid, null, shell, or zero-effect
feature and poison downstream history.

**Required response/change:** require a valid non-null single solid, prove ownership and
history/DAG safety, and return Tip before/after plus downstream features made inactive. A
failed native assignment must report whether the Tip changed.

### `partdesign.edit_feature`

This is a deletion candidate. In addition, its current link patches do not validate
subelement existence, Body ownership, dependency cycles, operation-specific property
relationships, or quantity bounds before mutation.

## Part findings

### `part.create_primitive`

Delete from the provider surface as described above. Its native objects are parametric; the
defect is the model affordance, not FreeCAD binding.

### `part.extrude` and `part.revolve`

They check only that the source has a Shape. They do not preflight planarity, closure when a
solid is requested, profile self-intersection, axis crossing, or axis/profile relationship.
They also silently ignore failure to hide the source object.

**Required response:** source wire/face diagnostics, normalized direction/axis, solid
eligibility, retained feature, actual source visibility, and native BRep failure details.

### `part.boolean`

A multi-tool cut silently creates an auxiliary `Part::MultiFuse` named `CutTools`; this hidden
operation is not represented in the contract or result. Failure can leave both auxiliary and
Boolean objects. Operand overlap and validity are not diagnosed.

**Required change:** reject multi-tool cut or expose it as an explicit two-feature operation.
Return every created object and ownership/visibility change. Never introduce an undocumented
helper feature.

### `part.fillet` and `part.chamfer`

These tools use exact edge indices only, unlike the safer PartDesign query-plus-count pattern.
Native dress-up failures are opaque.

**Required change:** accept the shared count-guarded geometric selection contract and return
resolved edge facts plus structured BRep diagnostics.

### `part.set_placement`

Delete or replace as described above. If replaced, the response must distinguish local and
global placement and show the container/link transform chain used to compute the verified
global result.

## Draft findings

### `draft.create_wire` and `draft.create_bspline`

The tools claim face-capable closed profiles but do not preflight duplicate points,
non-coplanarity, self-intersection, or face-buildability. A failed Draft feature is reported
only after native recompute.

**Required response:** point index diagnostics, plane fit/deviation, open gaps,
self-intersections, wire/face result, and retained object.

### `draft.create_circle`

For an arc, `make_face=true` is ignored at runtime. The field description does say faces
are "ignored for arcs", so the behavior is documented rather than silent — but the schema
still admits an option that the selected variant discards, and the response does not
report that the request was narrowed.

**Required change:** make full-circle and arc discriminated variants; reject face creation
for an arc and return the normalized sweep actually created.

### `draft.create_array`

The orthogonal variant allows a zero interval with multiple copies and collinear X/Y intervals
for a 2D grid, producing coincident copies. Success does not verify requested instance count.

**Required response:** interval rank, coincident-instance detection, requested/actual instance
count, and source/link state.

### `draft.create_text`

If `ViewObject.FontSize` is unavailable the requested height is silently skipped, yet the
response reports the requested value as applied.

**Required change:** require the native view property, read it back, and fail with
`state_change` if only the text object was created.

## Spreadsheet findings

### `spreadsheet.set_cells`

The call is described as one batch, but aliases and cells are applied sequentially. A native
failure can retain a partial prefix. Formula evaluation errors are embedded per cell while
top-level `ok` remains true. Alias collisions and formula validity are not preflighted.

**Required response:** per-entry index/status, before/after content and alias, exact retained
prefix, formula parse/evaluation diagnostics, and top-level failure when any requested cell
does not evaluate as required.

### `spreadsheet.read_sheet`

Per-cell content/value failures still return top-level success, and alias-read exceptions are
silently discarded even though the description promises aliases.

**Required response:** complete/partial status, per-cell field errors, unsupported native
methods, and truncation cursor/range so the rest of a large sheet can be read deterministically.

## Surface findings

### `surface.fill`, `surface.loft`, and `surface.blend`

Reference existence is checked, but boundary connectivity/order, closure gaps, profile
intersection, section compatibility, and orientation are not. All use exact topology names
without count guards. Native failures do not identify the edge or section responsible.

**Required response:** ordered resolved curve descriptors, endpoint gaps, closure/orientation,
section compatibility, and failing boundary/section. **Required FreeCAD change:** structured
Surface filling/sections/GeomFill diagnostics and failing input indices.

### `surface.extend`

It accepts only an exact Face index and does not verify the actual applied extension or return
the face's parametric bounds. Native failure provides no valid U/V range or geometry reason.

**Required change:** use count-guarded face selection; return source U/V ranges and requested
versus resulting bounds.

### `surface.thicken`

The implementation creates `Part::Offset`, and the shared Part result accepts any non-empty
faces/edges. It can report success without producing the one solid promised by the tool.

**Required response/change:** require exactly one valid solid when solid thickening is
requested; report offset self-intersections, join failure, actual solids/shells, and source
visibility. Use native offset diagnostics when FreeCAD exposes them.

## Assembly findings

### `assembly.insert_component`

The source can be any document object. A PartDesign feature inside a Body can therefore be
inserted instead of the Body, recreating the observed Pad-versus-Body failure. The response
does not prove that source container membership stayed unchanged. Its `position` is described
as global even though a child link placement is assembly-local.

**Required response/change:** accept only explicit standalone component types; reject a
PartDesign child feature and return its owning Body as the correction. Return source parent/
Group/Tip before and after, link target, assembly-local placement, and verified global
placement.

### `assembly.ground_component`

`assembly_joint_group` silently creates a missing JointGroup. A malformed assembly is thereby
mutated as a compatibility fallback. Grounding success does not verify `ObjectToGround` or a
solver-visible grounded state.

**Required change:** require the assembly's native JointGroup and fail with its actual
structure if missing. Verify the grounded joint and solver state after recompute.

### `assembly.create_joint`

Components are not checked as children of the target assembly; referenced subelements are not
validated for existence or compatibility with the joint type. The static tool exposes 6 of
the 13 joint types available in this build, omitting parallel, perpendicular, angle,
rack-pinion, screw, gears, and belt. Solver failure identifies only a global code, not the
joint/reference/residual.

**Required response:** component membership, resolved connector geometry/frames, joint-type
compatibility, native supported joint types, connector values after assignment, solver code,
and per-joint diagnostics. Unsupported native joint types should be intentionally specified
or documented as excluded, not silently absent.

### `assembly.solve`

The native API returns only a coarse integer and exposes no diagnostic properties in this
build. VibeCAD can say `conflicting_constraints` but cannot identify which joints conflict or
how far constraints miss.

**Required FreeCAD change:** solver results must expose per-joint status, residual, removed
DoF, conflicting/redundant sets, grounded-component status, and component placement delta.

## BIM findings

### `bim.create_spatial_structure`

Levels are created before Building and Site. If a later native call fails, orphan levels are
retained but the response does not identify the successful prefix. Success also does not
verify the Site -> Building -> Level hierarchy or actual elevations.

**Required response:** every created object, hierarchy membership, actual elevation, and exact
retained prefix on failure.

### `bim.create_wall`

The level is checked only for existence, not as a native Building Storey. Baseline edges are
not validated as one usable path. Success verifies wall shape but not level membership,
baseline consumption, alignment, or dimensions.

**Required response:** baseline topology/path diagnostics, level type/hierarchy, requested and
actual wall dimensions/alignment, host membership, and source visibility.

### `bim.create_structure`

The same level-type problem applies. Slab profiles are checked only for at least one face, not
one closed planar region. Column/beam/slab success does not verify IFC classification or level
membership. The beam variant is hard-wired to global X; this is a real limitation that must be
explicit in the result.

**Required response:** profile/solid facts, actual placement/orientation, IFC type, level
membership, and requested versus actual dimensions.

### `bim.add_window`

The result is considered valid when the window object has a shape. It does not prove that the
opening intersects or changes the host wall. A floating window can therefore return success,
with a screenshot suggested as the only validation.

**Required response:** host wall shape before/after, opening volume/topology delta, host link
after recompute, wall intersection, and actual placement. No host change means failure.

## TechDraw findings

### `techdraw.create_page`

If a requested ISO template is missing, the tool substitutes an A4 fallback. The result's
`template_file` field does reveal the substitution to a caller who compares basenames, but
there is no explicit flag or failure: the call returns success and can create a page with
the wrong size. That is an explicit inferior fallback.

**Required change:** delete the fallback. Return the requested path, installed template
candidates, page/template objects retained, and actual page dimensions.

### `techdraw.add_view`

The tool description says projected element names are returned, but the implementation returns
only counts and a naming note. Exceptions reading visible edges/vertices are silently ignored.
Page membership, source list, projection success, and page bounds are not verified.

**Required response:** actual projected EdgeN/VertexN descriptors with geometry type and 2D
bounds, exact source list, page membership, actual scale/position, and projection diagnostics.
**Required FreeCAD change:** expose projected element descriptors and mapping to source
subelements as structured Python data.

### `techdraw.add_dimension`

References are syntax-checked but not validated against the view or dimension type before the
dimension is created. A failed retained dimension receives a generic example rather than the
actual projected geometry facts.

**Required response:** each resolved projected element and geometry type, expected reference
contract for the selected dimension, page/view membership, native state, measured value, and
retained dimension.

## Material findings

### `material.apply_material`

Success does not read back the assigned material UUID/properties from the target. Physical
property extraction silently omits values that throw, while the tool claims those properties
are available to FEM. Appearance change is also asserted rather than verified.

**Required response:** material before/after, assigned UUID read back from the object, required
physical properties and per-property errors, and whether view appearance changed.

### `material.set_appearance`

Color and transparency are applied sequentially. A target supporting only one property can
return success even though both were requested; a later setter failure can retain the first
change without reporting it.

**Required response:** supported properties, before/after values, exact applied subset, and
partial retained state. If both are required by the request, partial application is failure.

## Mesh and MeshPart findings

### `mesh.analyze`

Every unsupported/failed defect check is converted to `null` without its exception. Then
`has_defects` treats unknown checks as false, so an incompletely analyzed mesh can look clean.

**Required response:** `complete`, per-check status/error, known defects, unknown checks, and a
verdict of `unknown` when any required check could not run.

### `mesh.repair`

The repair result trusts the incomplete analyzer and can call a mesh ready even when checks
are unknown or it is not watertight. It does not require the selected defect to improve or
guard against increased component/facet damage.

**Required response:** per-pass native result, before/after known and unknown checks, intended
defect delta, regressions, and a conservative readiness verdict.

### `meshpart.mesh_from_shape`

Top-level success does not require nonzero points/facets or compare tessellated bounds to the
source. Analyzer unknowns are inherited.

**Required response:** source validity/bounds, mesh counts/bounds, deviation settings actually
used, and a nonempty postcondition.

### `meshpart.shape_from_mesh`

When `make_solid=true`, the response reports the requested boolean as `is_solid`; it does not
read back the actual solid count. Mesh defect prerequisites are described but not checked.

**Required response:** mesh analysis completeness, sewn-shell closure, actual solid/shell
counts, shape validity, and the native sewing/solidification stage that failed.

## FEM findings

### `fem.create_analysis`

Analysis types are a static list and are written without reading native enum choices. Success
does not verify solver membership, actual AnalysisType, or solver readiness.

**Required response:** native supported types, actual solver property, analysis Group
membership, and solver object state.

### `fem.add_material`

Failure to set UUID is silently ignored. A structural material without Young's modulus still
returns success with only a warning, producing an analysis known to be unsolvable.

**Required response/change:** validate properties required by the analysis type before
mutation; verify Group membership and native material properties after assignment; do not
silently ignore UUID assignment.

### `fem.add_constraint`

References are checked for existence but not semantic suitability. Pressure can target an
edge, a force direction can be nonlinear, and temperature/loads are not checked against the
analysis type. The constrained object need not be the meshed model.

**Required response:** resolved subelement geometry, required geometry for the constraint,
analysis-type compatibility, relationship to the meshed source, actual native references and
quantities, and retained constraint state.

### `fem.mesh_analysis`

Gmsh runs synchronously with `blocking=True` on the FreeCAD call path, which can freeze the UI.
The mesh object is added to the analysis before the external process succeeds, and success
requires nodes but not volume elements for a solid analysis. There is no progress or
cancellation contract.

**Required response:** preflight executable/configuration, source solid validity, external
process PID/exit/stderr, progress, cancellation state, retained mesh object, node/element
counts by type, and quality metrics. Solid analyses require volume elements.

**Required FreeCAD change:** expose asynchronous cancellable Gmsh execution with structured
progress and diagnostics, while document mutations remain serialized on the GUI/document
thread.

### `fem.solve`

CalculiX also runs synchronously. Prerequisite errors are flattened from text, solver failures
return generic guessed causes, and result collection can include stale result objects already
in the analysis rather than proving they came from this solve.

**Required response:** structured missing prerequisites, process identity/exit/output,
cancellation/progress, result objects created or changed by this run, and result completeness.

**Required FreeCAD change:** asynchronous cancellable solver execution plus structured
prerequisite, input-writer, process, and result-import diagnostics with solve generation IDs.

## CAM findings

### `cam.create_job`

If the native Stock object or its margin properties are absent, requested margins are silently
ignored and then echoed as if applied. Job model clones, Tools/Operations groups, stock bounds,
and coordinate system are not verified.

**Required response:** requested versus actual stock properties/bounds, native model clone map,
group membership, and setup coordinate system. Missing stock support must fail before claiming
margins.

### `cam.add_tool`

Diameter is silently skipped if the tool bit lacks that property. Other geometry required by
drill/chamfer/V-bit tools is left at opaque defaults. Tool-bit creation can survive controller
failure. Feeds, speed, diameter, controller membership, and tool number are not read back.

**Required response:** shape-specific required dimensions, native supported properties,
tool-bit and controller objects created, actual geometry/feeds/speed/number, job membership,
and retained partial state.

### `cam.add_operation`

This tool contains several fallbacks:

- A controller can match by label despite an exact-name contract.
- Omitting a controller relies on factory state rather than explicitly selecting and reporting
  the job's most recent controller.
- `_set_depth` silently skips an absent property and silently ignores expression-clear errors.
- Drilling silently skips hole detection or peck settings when native properties are absent.
- Empty toolpath feedback is a list of generic guesses, not native facts.

It also does not validate tool type/diameter against the operation and geometry, return actual
depths, or prove stock removal/collision safety.

**Required change:** remove every fallback; require exact controller name or deterministically
resolve one exact controller and report it; validate and read back every native property; return
resolved base geometry, path-generation status/log, command count/type, stock-removal bounds,
and retained operation.

**Required FreeCAD change:** Path operations need structured generation diagnostics (stage,
offending base, tool/geometry incompatibility, depth/boundary error), not only an empty Path or
console text. Collision and stock-removal simulation results should be queryable through
Python.

## Long-tail read findings

### `openscad.list_csg`

Delete the current implementation. `_is_openscad_related` treats every Part/Mesh object as
OpenSCAD-related, so the result is a broad document scan rather than a native CSG tree.

### `reveng.list_candidates`

Delete the current output classifier. Reconstruction status is inferred from object names and
labels, not native FreeCAD provenance.

## Required FreeCAD work summary

The following work belongs in FreeCAD rather than another VibeCAD heuristic:

1. Structured per-recompute diagnostics in App/Python, replacing Report View scraping.
2. Sketcher profile diagnostics and authoritative geometry/constraint mutation maps.
3. Sketcher constraint feasibility preview or structured post-insert solver deltas.
4. OpenCascade operation status and offending-input exposure for dress-up, offset, Boolean,
   distance, loft, sweep, helix, and Surface algorithms.
5. Hole thread catalog access independent of a live document feature.
6. PartDesign transform occurrence and MultiTransform child diagnostics.
7. Assembly per-joint solver residual/conflict/redundancy data.
8. TechDraw projected-element descriptors and source mapping.
9. Asynchronous cancellable Gmsh and CalculiX APIs with structured progress/results.
10. CAM toolpath-generation diagnostics plus queryable collision/stock-removal results.

Everything else in this report can be corrected in VibeCAD by deleting the wrong affordance,
using discriminated schemas, validating before mutation, reading native state back after
mutation, and returning the common misuse envelope without fallbacks.
