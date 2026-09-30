# P0 "Stop the bleeding": implementation, test and doc plan

Status: **DONE, uncommitted**: all nine landing steps implemented and verified on 2026-09-30 (see §9). Nothing is committed yet. This file tracks P0 of
[activity-flow-resource-bc-design.md](activity-flow-resource-bc-design.md)
(§10). Tick items here as they land.

**Goal.** After P0, every activity that bc runs is run *correctly*. Anything
bc cannot run correctly is rejected, with a location. P0 adds **no** new
flow-object or resource semantics; those start in P1 and P3. P0 removes every
way the front end or bc can silently produce a different scenario from the one
written.

**Gate** (design §10, refined here):

1. The 25 `bind`-crash tests are green (F1, F2 and `unique`; see §1).
2. Both silent-drop registry tests are green (§4).
3. No activity construct reaches coroutine 0 by default: an unresolved
   INVOKE/SPAWN target is a `LoweringError`.
4. The new L3 `act.*` corpus tests pass on bc, or are strict-listed in
   `expected/bc.toml` with an owning phase (§5).
5. SV golden snapshots are byte-identical. The F7 and F14 changes touch the SV
   target.
6. The unit-suite failure count only goes down. Every remaining failure is
   recorded in `KNOWN_TEST_FAILURES.md` with an owner.

**Gate status (2026-09-30):** all six met.
1. The bind/`unique` crash tests are green (28, counted after the re-baseline).
2. Both registry tests are green (`test_activity_registry.py`,
   `test_activity_ir_registry.py`).
3. No default coroutine: an unknown INVOKE/SPAWN target is refused by the
   pass and by bc, and `test_invoke_of_unknown_target_is_refused` holds it.
4. `act.*`: bc passes 7; 1 is strict UNSUPPORTED (P1).
5. The progseq golden snapshots pass unchanged. SV output is byte-identical
   across the `FlowKind` migration on all 12 pattern models.
6. Unit suite: 151 → 4 failed (1847 passed). The 4 are the op-model doc
   references, listed with an owner in `KNOWN_TEST_FAILURES.md` along with
   the pre-existing progseq and SV-sim failures.

---

## 1. Baseline (measured 2026-09-30)

`PYTHONPATH=src:packages/zuspec-ir-core/src packages/python/bin/python -m pytest tests/unit`
gives **151 failed, 1559 passed, 22 skipped**. The design doc says "11
failing". pssparser has moved since (the symbol-resolution R4 checkpoint), so
the baseline has drifted. The count was re-measured after a clean pssparser
rebuild at `57c4dde` (its own suite: 5683 passed), with the same result. Every
failure is attributed below, by traceback, and both `ExprId` causes were
confirmed by patching a scratch copy of `src/` (151 → 103).

| Cause | Tests | P0? | Note |
|---|---|---|---|
| `print`/`message` used without `import std_pkg::*;` (pssparser now enforces package visibility, LRM 18.1) | 71 | **yes, fixtures only** | masks activity tests: `test_chost_activity_*`, `test_foreach_body_*`, `test_nested_activity_inlined`, SV `test_modeling_patterns[producer_consumer, resource_share, state_flow, …]`. See P0.0b |
| `ConstraintBlock.getName()` is now an `ExprId`, not a `str` (pssparser `65c8a0d`); `ast2ir.py:2828` uses it as the function name, so every *named* constraint is keyed by an AST object | 42 | **yes** (P0.0e) | silent: generic-constraint references cannot find `c`, and they are left as `ExprCall`, which bc refuses. Was mis-triaged as "generic constraints workstream" |
| F1: `ComponentBind.getPool_path()` is now an `ExprRefPathContext` (`ast2ir.py:1128`, `.split`) | 11 | **yes** | resource, state and flow fixtures, `test_component_bind` |
| F2: activity `bind` / `schedule` operands are `ExprRefPathContext` (`ast2ir.py:2388`, `.numElems`) | 10 | **yes** | `test_activity_bind`, SV analyze/pure-subset |
| Same drift in `ConstraintStmtUnique` (`ast2ir.py:2773`) | 5 | **yes** | same helper fixes it (P0.1); one is in `test_generic_constraints` |
| Covergroup coverpoint / cross names are now `ExprRefName`; `_id_name` (`ast2ir.py:4976`) calls `.getId()` once and returns the inner `ExprId` | 6 | **yes** (P0.0e) | silent: names are AST objects, so sampling records 0 hits |
| pssparser now declares `initial`/`instance_id` itself, so a test that redeclares them is an error (PSS duplicate declaration) | 2 | **yes** | the tests are wrong, not pssc; rewrite them (P0.0c) |
| Doc references to deleted `docs/op-model-*.md` | 3 | no | doc housekeeping |
| `docs/design/*.md` notes with no archived-note banner | 1 | **yes** | P0.0a |

A defect confirmed on today's tree through the real bc adapter
(`tests/compliance/adapters/pssc_bc.py`):

```pss
action T { B b1;  activity { b1; do pss_top::B; atomic { do B; } } }
```

The trace should be `B B B`. bc produces `A A`: the handle and the qualified
traversal both run coroutine 0 (`A`), and the `atomic` body is dropped. This
probe becomes test `test_p0_trace_probe` (P0.20).

---

## 2. Principles for every P0 item

- **The test comes first.** Each item lands with a test that fails on today's
  tree for the stated reason. A crash test goes green; a silent-drop test goes
  from "wrong IR" or "wrong trace" to "right IR" or "a located error".
- **Loud over silent.** An unsupported case is `ctx.add_error(...)` in ast2ir
  (aggregated into `PssTranslationError`), `UnsupportedConstructError` in
  `PSSToScenarioPass`, or `LoweringError` in bc. It is never `return None`, a
  debug log, or a default.
- **Use the linker's resolution.** ast2ir reads `ExprRefPath.getTarget()`
  (`SymbolRefPath`) through `_symbol_scope_at`, and does not re-resolve names
  from strings (the house rule: fix at source, never work around in pssc). A parser gap is fixed
  in pssparser, not worked around.
- **No golden regen.** F7 and F14 change SV code, not SV output.
- **IR additions round-trip.** Every new IR field is checked against
  ir-core's serializer and deserializer (`zuspec.ir.core.deserializer`) and
  its round-trip tests.

---

## 3. Work items

Items are grouped by layer and numbered for tracking. The "Commits" list in §6
gives the landing order.

### P0.0 Housekeeping and baseline

- [x] **P0.0a Doc allow-list.** Add `activity-flow-resource-bc-design.md` and
  `activity-p0-plan.md` to the allow-list in
  `tests/unit/test_doc_references.py::test_the_archived_notes_say_they_are_archived`.
  Both are notes being written now.
- [x] **P0.0b `std_pkg` fixture fix.** Add `import std_pkg::*;` to the pssc
  test fixtures and inline sources that call `print`/`message` without it:
  `tests/unit/test_target_sw.py`, `tests/unit/test_target_sv_oo.py`,
  `tests/patterns/*.pss`, the foreach/match/nested-activity tests, and the
  others in the ~60. This is a mechanical fixture change and the parser is
  right (LRM 18.1). It is in P0 because it hides the activity and flow tests
  P0 has to watch. Rule: fixtures only; no pssc source change. Re-measure
  afterwards and re-triage the covergroup failures.
- [x] **P0.0c Rewrite the duplicate-built-in tests.**
  `test_pss_preprocess.py::test_no_double_injection_{initial,instance_id}`
  now assert that a redeclared `initial`/`instance_id` is rejected (with its
  location), and that the built-in reaches the IR exactly once when it is not
  redeclared. Check that
  `test_state_constraint_initial_parseable`/`test_resource_constraint_instance_id_parseable`
  pass once F1 is fixed. They fail on F1 today.
- [x] **P0.0e `ExprId` / `ExprRefName` names.** Two one-line fixes, each
  with a test that fails today:
  - At `ast2ir.py:2828`, a named constraint takes `getName().getId()`.
    Test: `constraint c1 {…}` gives an IR function named `'c1'` (a `str`).
  - `_id_name` unwraps an `ExprRefName` to its `ExprId` and then to the
    string. Test: coverpoint and cross names are `str`.

  Also sweep ast2ir for every other field that the diff since `3fc5cac`
  changed to `ExprRefName`. `ExportFunction.name` and `ExprAggrStructElem.name`
  are already handled. `ComponentBindTarget.field` (`ast2ir.py:1159`,
  `field.getId()` joined as a `str`) has the same bug, and it will be the next
  crash once F1's `.split` is gone; P0.2 owns it. `SymbolCall.target` is
  rejected in P0 (P0.10). These fixes clear 48 of the 151.
- [x] **P0.0d Record what is out of scope.** Add a new section to
  `KNOWN_TEST_FAILURES.md`, "pssc unit, pssparser R4 drift (2026-09-30)",
  listing the generic-constraint and doc-reference failures by test with
  their owner. After that, the P0 gate measures against a list rather than a
  count.

### P0.1 One ref-path reader (the root cause of F1, F2 and `unique`)

pssparser's bind-operand rework made every bind operand, and the `unique`
list, an `ExprRefPathContext`: an `ExprHierarchicalId` in `getHier_id()` plus
the linker's `SymbolRefPath` in `getTarget()`. ast2ir has three places that
still expect the older shapes.

- [x] **As built:** no new reader was needed. `_translate_expression`
  already translates an `ExprRefPathContext` for every constraint
  (subscripts, locals, `super`, calls), so bind operands go through it
  (`_bind_operand`). Paths kept as names (the bound pool) use the small
  `_ref_path_names`. `_hier_id_to_expr` is deleted. The planned design is
  kept below for the record.
- [ ] ~~Add `AstToIrTranslator._ref_path_elems(ctx, ref) -> List[_PathElem]`,~~
  where `_PathElem = (name, subscripts: List[ir.Expr], resolved_scope)`.
  Names come from `getHier_id()`; `resolved_scope` comes from walking
  `getTarget()` with `_symbol_scope_at` (so `super` steps and labels resolve
  as the linker did). Also add `_ref_path_to_expr(ctx, ref) -> ir.Expr`,
  which builds the `ExprAttribute`/`ExprIndex` chain from `TypeExprRefSelf`.
  `_hier_id_to_expr` becomes a thin wrapper, or is deleted.
- [x] If `getTarget()` is `None` on a path the linker accepted, that is a
  pssparser defect. pssparser's own `TaskCheckRefsResolved` already reports
  it ("left unbound by pssparser"), so pssc does not repeat the check. That
  message is how the instance-qualifier bug was found (pssparser request P1).
- Tests: `tests/unit/integration/test_ref_path_reader.py`. Cover a plain
  path, a path through an instance (`sub.p`), a subscript (`arr[1].p`), a
  label path (`L1.a.o`), and a path whose target is `None` (a located error).

### P0.2 F1: component `bind`

- [x] `_translate_component_binds`: read the pool with P0.1. `PoolBind`
  gains `pool_path: List[str]` for a path through an instance (Example 132,
  `bind gfx0.power_state_var …`). `pool_name` stays the last element.
- [x] `_bind_target_path`: include `ComponentBindTarget.getPath()`, the
  component-instance elements that are dropped today. So `bind p {sub.A.out}`
  keeps `sub`.
- [x] A **range** on a path element or a target (`c[0..1].A.o`) produces a
  located error: "bind ranges are not supported yet (P2 pool-binding
  table)". Today the range is dropped without a word.
- [x] Add `pool_path` to ir-core `PoolBind` with a docstring, and cover it in
  the serializer round trip.
- Tests: the 11 existing F1 tests go green. Add
  `test_component_bind.py::{test_bind_through_instance_keeps_path, test_bind_target_keeps_component_path, test_bind_range_is_a_located_error}`.

### P0.3 F2: activity `bind`, and `unique`

- [x] `ActivityBindStmt`: `src`/`dst` come from `_ref_path_to_expr`. A label
  path (`L1.a.o`) must resolve through the label scope. The IR records the
  path as written, and P3 consumes it.
- [x] `ConstraintStmtUnique`: today it keeps only the last element, so
  `unique {a.x, b.x}` becomes `unique {x, x}`, which is **another silent
  miscompile** that this plan now records. **As built:** `StmtUnique.vars`
  is `List[str]` with four consumers (be-py, ir-core, SV and `sw_solve`), so
  widening it is not P0. A path operand, and the one-operand form
  (`unique {arr}`, which the `>= 2` check dropped without a word), are
  located errors. Carrying expressions is left for the constraint work.
- [x] bc: `ActivityBind` still reaches `UnsupportedConstructError` in
  `PSSToScenarioPass` (P3 implements it). Assert that it does.
- Tests: the 14 existing F2 tests go green. Add
  `test_activity_bind.py::test_label_path_operand` and
  `test_pss_unique_constraints_rt.py::test_unique_keeps_full_paths` (the
  `a.x`/`b.x` case, solved through bc, must give distinct values).
  **As built:** `test_unique_{path,single}_operand_is_a_located_error`,
  `test_activity_bind.py::test_bc_refuses_activity_bind`, and the ir-core
  round trip `test_serializer_roundtrip.py::TestPoolBindRoundTrip`.

### P0.4 F3: the `atomic` body

- [x] `ActivityAtomicBlock`: its content is `getBody()`, not `children()`.
  Translate the body with P0.5's helper.
- [x] Scenario lowering: `ScAtomic` inlined as a sequence is **correct for
  P0**. `atomic` only restricts inference and scheduling interleaving
  (11.3.7), and P0 has neither. Say so in a comment at
  `orchestration.py`'s `ScAtomic` case, pointing at P4 (atomic exclusion).
- Tests: `test_activity_shape.py::test_atomic_body_non_empty`, plus the
  trace probe (P0.20).

### P0.5 F4: single-statement loop bodies

- [x] Replace `_activity_body_children(body)` with `_activity_body(ctx, body)
  -> List[ir.ActivityStmt]`. It returns `[]` only for a `None` body, and the
  unlabeled scope's children for an unlabeled `{…}` scope. Otherwise it
  returns `[translate(body)]`, which keeps a **labeled** body block as a
  labeled block (F8).
- [x] Apply it to every body: repeat, repeat-while, foreach, replicate, match
  choice, select branch, and if/else. The if/else and select paths currently
  flatten `.stmts` by `hasattr`, which also loses a labeled block.
- Tests: `test_activity_shape.py::test_single_statement_{repeat,replicate,dowhile,foreach}_body`.

### P0.6 F5: the `foreach` collection

- [x] `ActivityForeach`: the collection is `getPath()` (an
  `ExprRefPathContext`, read with P0.1), not `getTarget()`.
- [x] bc still rejects `foreach` (`control.py:52`). Keep it rejected, but make
  sure the rejection names `foreach` and the location.
- Tests: `test_activity_shape.py::test_foreach_collection_present`.

### P0.7 F6: indexed handle traversal, and initializers

- [x] `_translate_handle_traversal`: build the handle with P0.1, and carry a
  subscript into `ActivityTraversal.index` (`h[i]` is currently `h` with the
  index lost).
- [x] Traversal initializers (`getInitializers()` on both traversal kinds)
  produce a located error, "traversal initializers are not supported yet
  (P1)". They are dropped today.
- Tests: `test_activity_shape.py::{test_indexed_handle_keeps_index, test_initializer_is_a_located_error}`.

### P0.8 F7: join specs

- [x] `_translate_join_spec` returns `ir.JoinKind` members, not strings. It
  translates `count` **with the context** (it passes `None` today). It reads
  `ActivityJoinSpecBranch.getBranches()` into a new
  `JoinSpec.branch_labels: List[str]`, which replaces the unused
  `branch_label: Optional[str]` (a join-branch names *several* labels).
- [x] Read `ActivitySchedule.getJoin_spec()`. It is never read today.
- [x] Consumers compared against strings: `src/pssc/targets/sv/lower_activities.py:917-921`
  (`kind == "none"/"first"`) moves to `JoinKind`. be-py already uses
  `JoinKind`. Grep for every other `.kind ==` on a join spec before landing.
- [x] bc (`parallel.py`) already dispatches on `JoinKind`. With this change,
  `join_none`/`join_first` from PSS run, and `join_select`/`join_branch` stay
  located `LoweringError`s.
- Tests: `test_activity_shape.py::{test_join_kinds_are_enum, test_join_branch_labels, test_schedule_join_read}`;
  a bc run of `parallel join_first(1)` and `join_none` from PSS
  (`tests/unit/integration/test_activity_bc_runs.py`); SV goldens unchanged.

### P0.9 F8: labels

- [x] IR: add `label: Optional[str] = None` to `ActivityStmt`. Subclasses
  that already declare `label` (`ActivityAnonTraversal`, `ActivityReplicate`)
  keep theirs; the dataclass redeclaration is compatible. Check the
  serializer.
- [x] ast2ir: set `label` from `ActivityLabeledStmt.getLabel()` and
  `ActivityLabeledScope.getLabel()` on every node, and set replicate's
  `it_label` into `ActivityReplicate.label`.
- [x] Nothing consumes labels in P0 except the join-branch labels (validated
  to name a branch in the same `parallel`: a located error otherwise) and
  replicate (P0.22).
- Tests: `test_activity_shape.py::{test_scope_labels_kept, test_traversal_label_kept, test_replicate_it_label_kept}`.

### P0.10 F9: scheduling constraints, activity symbols, and the fall-through

- [x] IR: new `ActivitySchedulingConstraint(is_parallel: bool, targets: List[Expr])`
  in `activity.py` (with `accept`, a visitor hook, and the serializer).
  ast2ir translates `ActivitySchedulingConstraint` (targets via P0.1).
- [x] `PSSToScenarioPass`: a scheduling constraint is rejected with
  `UnsupportedConstructError` (P3, D4) rather than ignored.
- [x] `ActivitySymbolCall` (and the activity symbol declaration) produce a
  located error: "activity symbols are not supported yet". Proposed owner:
  P1, by inline expansion. See open item O2.
- [x] **Delete** the `self.logger.debug("Unhandled activity stmt type")`
  fall-through at `ast2ir.py:1515`. An unrecognised node is
  `ctx.add_error(f"activity statement {type} is not translated", loc)`.
  Registry test 1 (§4) keeps it that way.
- Tests: `test_activity_shape.py::{test_scheduling_constraint_present, test_symbol_call_is_a_located_error}`.

**As built (P0.4–P0.10).** As planned, with these differences:
- `JoinSpec` keeps `branch_label` (zuspec-dataclasses sets it) and gains
  `branch_labels`. The consumers were SV `lower_activities.py`, now on
  `JoinKind`, and be-sw `activity_lower.py`, which maps `JoinKind` to its
  string `SwParBlock.join`. SV unit tests that hand-built string kinds now
  use the enum.
- Join-branch label validation is pssparser's
  (`test_a7_unknown_join_branch_label_is_still_an_error`), so it is not
  repeated.
- A labeled `replicate` *statement* (`L: replicate …`) is a located error,
  because `ActivityReplicate.label` holds the `R[]:` iteration label.
- Block declarations (X-8): an `ActionHandleField` in a block is skipped
  (a traversal of it resolves through `type_qname`); an `action` data
  field in a block is a located error (P1).
- A handle array `A arr[3]` is `array<A,3>` in the AST, so `arr[i]`
  resolves its element type from the template argument.
- `ActivityForeach` stays rejected in bc (`control.py`), unchanged.

Tests: `test_activity_shape.py` (20), `test_activity_registry.py` (27:
every pssparser activity node has a row, plus the fall-through check), and
in `test_activity_bc_runs.py` the trace probe (`B B B`), single-statement
bodies, and `join_first`/`join_none`/`join_select`/`join_branch` on bc.

### P0.11 F10: pool size

- [x] `_eval_pool_size` uses the existing `_fold_const_expr`
  (`ast2ir.py:992`), so a package constant or an expression works. A size
  that does not fold is a located error, rather than `capacity=None`.
- Tests: `test_pool_decl.py::{test_const_expr_pool_size, test_nonconst_pool_size_is_an_error}`.

### P0.12 F11: pools and binds in `extend component`

- [x] Move `FieldPool` and `ComponentBind` dispatch into
  `_translate_type_body`, the shared path its docstring says exists so that
  component and extension "cannot diverge". Then remove the separate calls at
  `ast2ir.py:981,985`.
- Tests: `test_pool_decl.py::{test_pool_in_extend_component, test_bind_in_extend_component}`.

### P0.13 F12: `export A;`

- [x] Record each `ExportAction` as a qualified name on `ctx.export_actions`,
  resolved through `_linked_type_name`.
- [x] `PSSToScenarioPass`: when the caller gives no `exports`, the recorded
  ones take precedence over `_auto_exports`. The root-component choice
  prefers the component owning an exported action over the "most actions"
  heuristic.
- Tests: `test_export_action.py::{test_export_recorded, test_export_selects_root}`.

**As built (P0.11–P0.13).** `_fold_const_expr` is deliberately shallow:
a literal, or a named package/imported constant. Any other size is a
located error, not unbounded. `ExportAction` is recorded from package,
global and component scope. An exported action that is not the root's is
refused (P1). Tests: `test_pool_decl.py` (6), `test_export_action.py` (4).

### P0.14 F14: `flow_kind` as `FlowKind`

- [x] ast2ir sets `struct_ir.flow_kind = ir.FlowKind.<X>`, the type ir-core
  annotates.
- [x] Migrate the eleven consumers that compare strings:
  - SV: `lower_flow_objects.py`, `lower_pure.py`, `analyze_flow.py`,
    `lower_activities.py`, `analyze_activity.py`, `lower_components.py`,
    `lower_inference.py`;
  - `sw_lower.py`;
  - be-py `builder.py`;
  - ir-core `xf/validate.py`.

  There is one commit for the lot; a half migration is a silent `False` on
  every comparison.
- [x] Add a tripwire test: `test_flow_kind_is_enum` asserts that no string
  `flow_kind` reaches any struct in the translated patterns suite.
- Gate: SV goldens byte-identical, and `tests/unit/sv` no worse than baseline.

**As built (P0.14).** The SV target keeps strings in its *own* binding
records (`FlowBindingInfo`, `FlowBinding`, `InferenceSlot`, documented as
`"buffer"|"stream"|"state"`), so the migration happens where SV reads the IR:
`analyze_flow._resolve_flow_kind` converts the kind (the only place an IR
kind enters an SV record). `lower_components`, `analyze_activity` and
`lower_pure` compare against `FlowKind` (`_FLOW_BASE` is keyed by it); be-py
`builder.py` does too. `sw_lower` only copies the value; ir-core
`validate.py` only prints it. Two tests asserted the old string contract
(`test_state_flow_objects.py`) and now assert `FlowKind.STATE`. SV output is
byte-identical for all 12 `tests/patterns` models (snapshot diff before and
after).

F13 (`comp`, `prev`, `uid`) is **not** in P0. `prev`/`uid` are deferred to P4b
(D11), and `comp` assignment is P1.

### P0.20 bc: no default coroutine

- [x] `orchestration.py:74,79`: replace `_coro_index.get(s.target, 0)` with a
  lookup that raises `LoweringError(f"traversal target {s.target!r} does not
  name a lowered action", src_ref)`.
- [x] `PSSToScenarioPass` (earlier, where the location is better): after the
  module is built, every `ScInvoke`/`ScSpawn` target must name a coroutine.
  Otherwise raise `UnsupportedConstructError`, naming the target, the reason
  (an action of another component: P1; an unknown name: a bug) and the
  location.
- Tests: `test_activity_bc_runs.py::test_p0_trace_probe` (§1's model, trace
  `B B B`); `test_unresolved_target_is_an_error` (a hand-built `ScInvoke` to a
  missing name raises, and does not run coroutine 0).

### P0.21 Handle to type resolution

- [x] `PSSToScenarioPass._lower_activity_stmt(ActivityTraversal)`: look up
  the handle field on the compound action (`dt.fields`, including inherited
  fields). Take the action type from the field's datatype, then the
  coroutine whose `action_type` is that qualified name. Emit
  `ScInvoke(target=<coroutine>, inst=<handle>)`.
- [x] A handle whose type is an action of a non-root component raises
  `UnsupportedConstructError` ("P1: non-root components").
- Tests: part of `test_p0_trace_probe`. Add `test_two_handles_same_type`
  (`B b1, b2; activity { b1; b2; }` gives `B B`), and
  `test_handle_of_other_component_is_rejected`.

**As built (P0.20–P0.22).** The traversal's action type comes from the
linker, not from a name lookup in the pass. `ActivityTraversal` and
`ActivityAnonTraversal` gain an additive `type_qname`, which ast2ir fills by
following the reference's `SymbolRefPath` (`_traversed_type_qname`): a type
scope gives its qualified name; a handle's `Field`/`ActionHandleField`
gives its declared type's linked name. That also covers handles declared
in activity blocks (pssparser X-8) and `do b1` (which pssparser links to
the handle). `action_type` stays as written, so the SV, sw and be-py
consumers are untouched. IR built by hand (bc and ir-core tests, zdc) has
no `type_qname`; the pass then accepts only an exact owned name. Two
related fixes: `_is_action` now includes a bodiless action (it was not
collected, so `do Z` had no coroutine), and `ScenarioValidator` no longer
rejects one. `_linked_type_name` crashed on a reference linked to a field
(`_scope_qname` now returns None there).
Tests: `tests/unit/integration/test_activity_bc_runs.py` (7; all failed
with coroutine-0 traces before). The trace probe with `atomic` lands with
F3 in commit 4.

### P0.22 Qualified and bodiless type traversals

- [x] Coroutines stay keyed by their simple name, but the pass builds
  `qualified action type -> coroutine name` from `ScCoroutine.action_type`.
  Every `ActivityAnonTraversal.action_type` (`B`, `pss_top::B`, `pkg::T`) is
  resolved through the linker's name (ast2ir records the qualified name with
  `_linked_type_name`) and then that map.
- [x] Two owned actions with the same simple name are a located error until
  the coroutine key is qualified. Today the second is silently rejected, or
  shadows the first.
- [x] A bodiless atomic action lowers to an empty coroutine and traverses
  correctly (it contributes no trace line). Test that it is *not* coroutine 0.
- Tests: `test_activity_bc_runs.py::{test_qualified_type_traversal, test_package_qualified_traversal_rejected_or_run, test_bodiless_traversal_runs_nothing}`.

### P0.23 `repeat … while` and `replicate`

- [x] `ActivityDoWhile` becomes `ScLoop(kind="dowhile", cond, body)`. bc's
  `control.lower_loop` already supports `dowhile`. Activity has only the
  do-while form (`PSSParser.g4:897`), so ast2ir's mapping is right.
- [x] `ActivityReplicate` with no label becomes `ScLoop(kind="repeat",
  count, index_var)`. That is trace-equivalent while nothing refers to
  per-iteration instances. A **labeled** replicate raises
  `UnsupportedConstructError` ("P1: replicate labels name per-iteration
  instances").
- Tests: `test_activity_bc_runs.py::{test_repeat_while_runs_until_false, test_replicate_runs_count_times, test_labeled_replicate_rejected}`.

### P0.24 Compound `pre_solve`/`post_solve`

- [x] `_lower_compound` emits `pre_solve`, then the solve, then `post_solve`,
  then the activity: the same lifecycle as `_lower_atomic`. Factor out the
  shared part; do not copy it. ConstraintCollect's `idx` logic already
  handles a leading `pre_solve`.
- [x] LRM order check: a compound's `pre_solve` runs before its children's.
  Children solve at INVOKE, which is after, so the per-action solving that
  P1 replaces cannot reorder it.
- Tests: `test_activity_bc_runs.py::test_compound_pre_post_solve_run` (trace
  shows parent pre, parent post, then child), and a variable written in
  `pre_solve` read by the activity's `if`.

**As built (P0.23–P0.24).** Unlabeled `replicate` becomes a counted loop
only in a sequential scope. Directly inside `parallel`/`schedule`, it
expands into branches (LRM 11.5.1), and a loop would be one branch, which
changes what a join waits for. That case is refused (P1). The compound
lifecycle shares `_exec_blocks`/`_lifecycle` with `_lower_atomic`.
`test_pre_solve_value_reaches_the_activity` was a live miscompile: before,
the activity read `k == 0` and took the `else` branch.

### P0.25 `schedule`: reject when members interact (D4)

- [x] `PSSToScenarioPass`: before lowering `ActivitySchedule`, collect the
  action types it traverses, transitively through compound actions. If any
  of them declares an input or output flow-object reference, or a
  lock/share claim, or if the block contains an
  `ActivitySchedulingConstraint`, raise `UnsupportedConstructError`
  ("schedule with interacting members is P3/P4 (D4)").
- [x] Otherwise `schedule` lowers as `parallel`/ALL. That is one legal
  realisation (11.3.5) when members do not interact. Replace the
  `_log.info("approximating")` with a comment citing D4.
- Tests: `test_activity_bc_runs.py::{test_schedule_noninteracting_runs_all, test_schedule_with_buffer_member_rejected, test_schedule_with_lock_member_rejected}`.

---

## 4. The two silent-drop registry tests

Following `test_dispatch_matches_registry`, each is a table that has to be
complete, plus a behaviour check per row.

### `tests/unit/integration/test_activity_registry.py::test_every_activity_node_translates`

- Enumerate pssparser AST classes **by introspection**: every class in
  `pssparser.ast` that subclasses `ActivityStmt`, `ActivityLabeledScope`,
  `ActivityJoinSpec`, `ActivitySchedulingConstraint`, `ActivitySelectBranch`
  or `ActivityMatchChoice`, minus abstract bases.
- `CASES: Dict[str, Tuple[str, Expect]]` maps the class name to a minimal PSS
  snippet producing it, and to the expected IR node type or a
  `TranslationError(match=…)`.
- **Completeness.** Every enumerated class has a row. A new parser node fails
  the test by name, forcing a decision. A row for a class that no longer
  exists fails too.
- **Behaviour.** Each snippet produces its expected IR node, or its located
  error. It never produces a missing node.
- A second check, `test_no_activity_fallthrough`, asserts that
  `_translate_activity_stmt` has no `return None` path left, by feeding it a
  synthetic unknown node.

### `tests/unit/integration/test_activity_ir_registry.py::test_every_activity_ir_node_lowers_or_is_refused` (as built; pssc has no `tests/unit/xf`)

- Enumerate ir-core `ActivityStmt` subclasses recursively (`__subclasses__`),
  including the zdc-origin ones: `ActivityWhileDo`, `ActivityFill`,
  `ActivityChain`, `ActivityConstraintForall`.
- `CASES` maps each to a hand-built IR fragment and to `lowers` or
  `rejects(match)`. For `lowers`, the fragment goes through
  `PSSToScenarioPass` and `lower_module`. For `rejects`, it must raise
  `UnsupportedConstructError`/`LoweringError`. Completeness is enforced both
  ways, as above.
- A property check: `ScInvoke`/`ScSpawn` to an unknown target raises
  `LoweringError`. This is gate item 3.

Expected P0 table (the rest of §2.2 of the design stays "rejects"):

| IR node | P0 outcome |
|---|---|
| SequenceBlock, Parallel (ALL/NONE/FIRST), Repeat, DoWhile, IfElse, Match, Select, Atomic, Traversal, AnonTraversal, Replicate (unlabeled), Schedule (non-interacting) | lowers |
| Parallel (SELECT/BRANCH), Foreach, Replicate (labeled), Schedule (interacting), Constraint, Bind, SchedulingConstraint, Super, WhileDo, Fill, Chain, ConstraintForall, inline `with` | rejects |

---

## 5. Corpus: first L3 `act.*` tests

The checker is P1-only: it handles `do`, `seq` and constant `repeat`. P0 adds
the tests it can already adjudicate, plus one small checker extension. Each
test is `compliance/act/<name>/{test.pss,test.json}` with `level: "L3"`,
imports `std_pkg`, and traces through `@@PSS-TRACE act`.

| Test id | Proves | Expected on bc after P0 |
|---|---|---|
| `act.traverse.handle.001` | handle traversal runs its type (not coroutine 0) | PASS |
| `act.traverse.handle_multi.001` | two handles of one type, and two of different types, in order | PASS |
| `act.traverse.qualified.001` | `do pss_top::B` | PASS |
| `act.traverse.bodiless.001` | a bodiless action contributes nothing and does not shift the next traversal | PASS |
| `act.atomic.body.001` | an `atomic { … }` body runs | PASS |
| `act.repeat.single_stmt.001` | `repeat (3) do B;` without braces | PASS |
| `act.replicate.001` | `replicate (3) do B;` gives three `B` | PASS (needs the checker extension) |
| `act.compound.pre_post.001` | a compound's `pre_solve`/`post_solve` run, in LRM order | PASS (trace via `chk`) |
| `act.compound.nested_labeled.001` | a labeled nested sequence keeps order | PASS |
| `act.multi_comp.001` | a traversal of an action in a sub-component | strict `UNSUPPORTED`, reason "P1: non-root components" |

- [x] **Checker extension (corpus-owned):** `replicate` with a constant
  count expands like `repeat` in `check.py::_expand` and in its matcher twin.
  Add a checker self-test, and a cheat mutation (a trace with one `B` too
  few must FAIL).
- [x] Add each test to `manifest.toml`. Run the op-model-py and op-model-sv
  compliance runs too: an `act.*` test is not an op-model test, so check that
  those adapters report `UNSUPPORTED`/`skip` by level and do not fail. Add
  strict entries to their `expected/*.toml` if needed.
- [x] `parallel`, `schedule`, `select` and `if` need checker P2
  (interleavings and choices), so they are **not** in P0's corpus slice.
  They are covered by pssc tests in §3 instead.

**As built (§5).** Eight tests landed under `compliance/act/`; bc passes
seven, and `act.multi_comp.001` is strict UNSUPPORTED (P1). Both op-model
targets list all eight as strict UNSUPPORTED (an operation model has no
activity). Differences from the table above:
- No `manifest.toml` entry is needed; tests are discovered by directory.
- The checker has no "matcher twin" for activities; only `_expand_node`
  changed. `synth.py` learned `repeat`/`replicate`.
- The mutants in `test_models.py` now also drop, duplicate and swap `act`
  lines. They only covered `chk`/`acc` lines, so an activity test had no
  cheat to catch; "one `B` too few" is now detected. All 380 checker tests
  pass.
- `act.traverse.bodiless.001` and `act.compound.pre_post.001` are **not**
  in the corpus: the model has no way to say "this atomic emits no `act`
  record", and the P1 checker rejects a `chk` before any `act`. Both stay
  covered by pssc's bc tests. See O7.
- The corpus README gains an activities catalogue.

Compliance: 157 passed / 34 xfailed became 164 passed / 43 xfailed. The
hand-off round trip passes.

Rule reminder: a model is never edited to make pssc pass. A disagreement goes
in `expected/bc.toml` with the owning phase.

---

## 6. Landing order (commits)

Each commit leaves the suite no worse than before it.

1. **Housekeeping.** P0.0a–d. The unit count drops by roughly 60; the
   remainder is listed in `KNOWN_TEST_FAILURES.md`.
2. **Ref-path reader, F1, F2, `unique`.** P0.1–P0.3. The 25 crash tests go
   green.
3. **bc loud failure and resolution.** P0.20–P0.22. The coroutine-0 fallback
   goes; the trace probe passes.
4. **Activity shape in ast2ir.** P0.4–P0.10 (F3–F9), plus registry test 1.
5. **Scenario lowering.** P0.23–P0.25, plus registry test 2.
6. **Component-level front end.** P0.11–P0.13 (F10–F12).
7. **`FlowKind`.** P0.14, alone, with the SV goldens check.
8. **Corpus `act.*` slice.** §5, plus the checker extension and the
   `expected/*.toml` entries.
9. **Docs.** §7.

Commits 2 and 3 are independent, and so are 4 and 6. Each pair can proceed in
parallel.

---

## 7. Documentation

- [x] **Design doc.** In §2.1 and §2.2, mark F1–F12 and F14 fixed and the
  bc rows updated. Record the baseline drift from §1, and the `unique`
  miscompile found by this plan, as a new row F15.
- [x] **AGENTS.md.** Add a short "Activities on bc" section:
  - the "no default coroutine" rule;
  - the two registry tests, and what a new AST or IR node must do;
  - the join-kind and `FlowKind` enum rule;
  - a pointer to the design doc.

  Keep it to AGENTS.md's style: a rule, its reason, and the test that
  enforces it.
- [x] **ir-core docstrings.** For the new IR fields and nodes (`label`,
  `branch_labels`, `pool_path`, `ActivitySchedulingConstraint`), each
  docstring states the PSS construct and the LRM clause.
- [x] **pss-corpus.** `compliance/README.md` lists the `act` area. The
  checker's README (or COMPLIANCE-DESIGN) notes that `replicate` is
  supported at P1.
- [x] **`KNOWN_TEST_FAILURES.md`.** Updated in commit 1, and trimmed as
  items land.
- No user-facing doc changes: P0 adds no CLI option and no language feature.

---

## 8. Open items for review

- **O1 — the `std_pkg` fixture sweep (P0.0b).** It is proposed inside P0
  because it unmasks the activity and flow tests. The alternative is to
  strict-list them and fix them separately. Recommendation: do it in P0.
- **O2 — activity symbols.** They are rejected in P0. Should they be
  implemented in P1 (inline expansion) or later? They are rare in the curated
  models.
- **O3 — bind ranges and traversal initializers.** Both are rejected in P0.
  Proposed owners: ranges in P2 (the pool-binding table), initializers in P1.
- **O4 — per-iteration labels on `replicate`.** Rejected in P0, owned by P1
  with the handle-as-object work.
- **O5 — the qualified coroutine key.** P0 errors on a simple-name
  collision. P1 (non-root components) needs qualified keys anyway, and could
  switch now at the cost of touching the bc context and driver.
  Recommendation: switch in P1.

---

- **O6 — `_translate_type_body` still ends in a debug-only fall-through.**
  **Done 2026-09-30.** Component, action and struct bodies (and `extend` of
  each) now share one table, `AstToIrTranslator._BODY_ELEMENTS`. An element
  with no row, or in a body its row does not name, is a located error.
  `test_type_body_registry.py` enumerates pssparser's `ScopeChild` classes
  and requires a row or a stated reason for each. What changed in meaning
  (`test_type_body_semantics.py`):
  - Exec blocks of one kind in a scope merge into one function, in source
    order, with the initial definition before its extensions (LRM 22.1 d).
    bc used to run the last and SV the first. Two blocks declaring the same
    top-level local are refused, since the IR has no block statement to
    scope them.
  - `extend action` now reaches `input`/`output`/`lock`/`share` (these were dropped).
  - A second activity in an action is refused (it replaced the first; LRM
    11.1 runs them as one `schedule`, which is P3).
  - Newly refused, previously dropped: `override` blocks, monitors, `cover`
    statements, covergroup types and instances, target-template exec blocks
    and functions (`exec file` included; `test_exec_file.py` updated), action
    `symbol`s, action-level scheduling constraints, component-scope `import
    function`/`import class`, a typedef, `extend` or `extend enum` inside a
    type, an activity in a component, and action `exec pre_body`/`run_start`/
    `run_end`/`header`/`declaration`.
  - Inert by design: `import p::*`, function prototypes (library types) and
    an action's implicit `comp`.
  - The action-level `ActionHandleField` branch was dead code: pssparser
    parses `B b1;` in an action body as a `Field`. Removed.
  None of pssc's suites exercised the newly refused constructs: unit 1898
  passed / 4 (docs), progseq, compliance (bc 164 / 43 xfailed, op-model-sv
  80 / 19) and sim (13 pre-existing) unchanged.

- **O7 — two corpus schema gaps for activities.** (a) A bodiless atomic
  action emits no `act` record, but a model's atomic type always expects
  one. It needs a field such as `"traced": false`. (b) A compound action's
  own exec blocks (`pre_solve`/`post_solve` checkpoints) have nowhere to
  go: the P1 checker rejects a `chk` before any `act`. Both are
  corpus-owned design decisions.

## 9. Progress log

| Date | Item | Note |
|---|---|---|
| 2026-09-30 | plan written | baseline 151 failed / 1559 passed; trace probe reproduces `A A` for the expected `B B B` |
| 2026-09-30 | commit 1 (P0.0a–e) | unit 151 → 32 failed (28 are F1/F2/`unique`, next; 4 op-model doc refs). Goldens byte-identical; compliance 157 passed / 34 xfailed, unchanged. pssparser's `cross-repo-followups.md` X-5/X-10/X-17/X-18 are the pssc edits these items make. Found one real pssparser bug (instance name as type qualifier), filed as `../pssparser/docs/design/pssc-requests-2026-09-30.md` P1; `tests/patterns/producer_consumer.pss` fixed to `tx_c::` |
| 2026-09-30 | commit 2 (P0.1–P0.3) | unit 32 → 4 failed (the op-model doc refs only; 1713 passed). progseq/compliance/ir-core unchanged apart from the new tests (same 8 pre-existing progseq failures). Deviations from the plan are recorded under P0.1 and P0.3 |
| 2026-09-30 | commit 3 (P0.20–P0.22) | unit 1723 passed / 4 failed (docs); compliance, progseq, be-bc (310), ir-core (83) and be-sw unit (521) unchanged. Coroutine-0 fallback gone from bc; unknown targets rejected in the pass and in bc |
| 2026-09-30 | commit 4 (P0.4–P0.10) | unit 1774 passed / 4 failed (docs). Compliance, progseq, be-bc, ir-core and be-sw unit unchanged. The trace probe now gives `B B B` (was `A A`) |
| 2026-09-30 | commit 5 (P0.23–P0.25 + registry 2) | unit 1821 passed / 4 failed (docs); rest unchanged. Registry 2 found nothing new: every ir-core activity node already lowered or was refused |
| 2026-09-30 | commit 6 (P0.11–P0.13) | unit 1833 passed / 4 failed (docs); rest unchanged |
| 2026-09-30 | commit 7 (P0.14) | unit 1847 passed / 4 failed (docs); SV output byte-identical on the patterns; be-py unit 22 passed; rest unchanged |
| 2026-09-30 | commit 8 (§5 corpus slice) | 8 `act.*` tests: bc 7 PASS + 1 strict UNSUPPORTED; checker 380 passed. Also swept `tests/sim/sv` for `std_pkg`: sim 25 → 13 failed, all 13 pre-existing sv-pure `std_pkg` type-declaration failures (in `KNOWN_TEST_FAILURES.md`) |
| 2026-09-30 | commit 9 (§7 docs) | design doc §2.5 "After P0" + F15; AGENTS.md "Activities on bc"; corpus README catalogue; ir-core docstrings on `label`, `branch_labels`, `type_qname`, `pool_path` and `ActivitySchedulingConstraint` |
| 2026-09-30 | O6 | one type-body dispatch + registry (43 cases) + 6 semantics tests. Unit 1898 passed / 4 failed (docs); progseq 8 pre-existing; compliance bc 164/43xf, op-model-sv 80/19xf; sim 13 pre-existing |
