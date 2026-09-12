# Pure-SV PSS Lowering — Implementation, Test & Doc Plan

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

*Plan of record for review and tracking.*

Status: draft for review. Date: 2026-06-26.
Design: [`pure-sv-incremental-design.md`](pure-sv-incremental-design.md)
(background: [`pure-sv-lowering-design.md`](pure-sv-lowering-design.md)).

Scope: deliver the incremental-traversal pure-SystemVerilog target across three
repos — `zuspec-ir-core`, `zuspec-be-sv`, and `pssc` — such that **pssc lowers
PSS to a structured IR that `zuspec-be-sv` emits mechanically** (classes, random
variables, constraints, procedural code), with **no DPI** on the default path.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done. IDs (e.g. `A1`) are stable
for cross-referencing in commits/PRs.

---

## 1. Target architecture

```
PSS sources
  │  pssc.frontend → ast2ir → zuspec.ir.core.Context        (semantic IR; exists)
  ▼
pssc incremental-traversal lowering  (all PSS solve/schedule/inference semantics)
  │     produces ↓ a fully-structured, backend-neutral lowered IR
  ▼
Lowered IR  =  DataType classes + rand fields + STRUCTURED constraints
               + STRUCTURED procedural bodies (if/for/foreach/while/case,
                 fork/join, randomize(), event wait/trigger, channel/sem ops)
  │  zuspec.be.sv: translate(core IR → SV IR) → SVEmitter
  ▼
*.sv   +   shipped zsp_rt_pkg.sv (extended)
```

The defining goal: **move every PSS-specific decision into pssc's lowering** so
the backend is a mechanical printer. Today `pssc/targets/sv/*` contains solve
classification, flow analysis, inference, schedule synthesis, and renders
constraints/procedural code as **raw SV strings** into `SVRawItem`/`body_lines`.
The end state replaces raw strings with **structured IR nodes** the backend
prints deterministically.

---

## 2. Key decisions to confirm (please review)

These shape all downstream work. Recommendations given; flag disagreement before
M1.

- **D1 — Emission-target IR.** *Recommend:* pssc lowers to **`zuspec.ir.core`**
  (extended), and `zuspec-be-sv` owns a **core→SV-IR translation pass** plus the
  existing `SVEmitter`. Keeps pssc backend-neutral, preserves IR
  serialization/round-trip, and matches "add IR in ir-core, codegen in be-sv."
  *Alternative:* pssc emits `zuspec.be.sv.ir.sv` directly (faster, but couples
  pssc to SV and loses serialization/other backends). 
- **D2 — Where procedural concurrency lives.** *Recommend:* extend the existing
  **core Scenario IR** (`scenario.py`: `ScSeq/ScPar/ScSpawn/ScJoin/ScWait/
  ScInvoke/ScLoop/ScSelect`) as the backend-neutral concurrency layer, adding
  only the missing sync nodes (event wait/trigger, channel/semaphore ops,
  formalized randomize). The SV backend lowers Scenario IR → `fork/join` etc.
  *Alternative:* new `StmtFork/StmtJoin/...` in `stmt.py` (Layer 0). Decide
  whether activities lower to Scenario IR or to Layer-0 procedural.
- **D3 — Constraints: structured vs strings.** *Recommend:* introduce
  **structured constraint IR** (implication, foreach/forall, soft, dist,
  solve-before, inside, unique) in core, and emit from structure. Removes the
  string-rendering in `lower_constraints.py`/`lower_exprs.py` and enables
  forwarding/solve-group transforms to operate on IR.
- **D4 — Expression sharing.** *Recommend:* the SV backend gains a single
  **core `Expr` → SV-text emitter**, so constraints and procedural expressions
  share one path (replaces `SVConstraintBlock.exprs: List[str]` with
  `List[Expr]`).
- **D5 — Target id & fallback.** *Recommend:* new target `sv-pure` (or
  `pure_sv: true` on the `sv-native` dvflow task). DPI/`dv-solve` path remains
  opt-in (`--allow-dpi`) for over-capacity problems; never default.

---

## 3. Gap summary (the work, by repo)

**zuspec-ir-core** (add semantic IR):
- Structured constraint nodes (D3): `ConstraintImplies`, `ConstraintForeach`,
  `ConstraintSoft`, `ConstraintDist`, `ConstraintSolveBefore`, `ConstraintUnique`,
  `ConstraintInside` (or `inside` as `Expr`). Today constraints are
  `Function` + `_is_constraint` metadata with raw `StmtExpr` bodies.
- Formalized solve: tighten `StmtRandomize` (target + structured constraint list
  + writeback) into the emission contract.
- Concurrency/sync (D2): event datatype + `ScEventWait`/`ScEventTrigger`,
  channel `put/get`, semaphore/pool `lock/unlock/share`, in Scenario IR.
- `Function.is_task` distinction (task vs function) for SV emission.
- Serializer/deserializer + validate.py coverage for all new nodes.

**zuspec-be-sv** (add codegen):
- Core→SV-IR **translation pass** (D1).
- Core `Expr`→SV-text emitter (D4); `SVConstraintBlock.exprs` → structured.
- Structured procedural statement IR + emit: `SVIfStmt`, `SVForStmt`,
  `SVForeachStmt`, `SVWhileStmt`, `SVCaseStmt`, `SVAssign`, `SVCall`,
  `SVForkJoin` (`join`/`join_any`/`join_none`), `SVRandomizeCall`
  (`x.randomize() with {...}`), `SVEventWait`/`SVEventTrigger`, `SVMailboxCall`,
  `SVSemaphoreCall`, `$cast`. Replaces raw `body_lines: List[str]`.
- Constraint emit for `dist`, `soft`, `solve before`, `foreach`, `implies`,
  `unique`, `inside` (some already supported as strings; make structured).

**pssc** (incremental lowering — the design):
- `resolve_binding.py` (new): static/runtime resolution pass (design §5).
- Rework `analyze_activity.py`/`lower_activities.py`/`lower_schedule.py` to the
  incremental traversal recipe (design §3) producing Scenario IR.
- Static constraint forwarding (design §6.3) replacing the DPI trigger in
  `classify_constraints.py`.
- `lower_solve_group.py` (new): localized joint-solve wrappers (design §10).
- Flow runtime forms, demand-driven inference, resources, state, replicate
  (design §6–§11), emitting structured IR + runtime-library calls.
- Static resource-risk analysis → SAFE/INFEASIBLE/AT-RISK (design §7.1).
- `sv-pure` target + Tier-3 diagnostics (design §12).

**runtime library** `pssc/share/sv/zsp_rt_pkg.sv` (design §13):
- `zsp_buffer_box#(T)`, `zsp_resource_pool#(T)` allowed-set + `claim_unique`,
  `zsp_state_pool#(T)` version counter + `wait_version`, `zsp_solve_group`,
  `zsp_scheduler` helpers.

---

## 4. Workstreams

- **WS-A · IR-core constraints & solve** (D3, D4 schema side)
- **WS-B · IR-core concurrency/sync** (D2)
- **WS-C · be-sv structured procedural + translation** (D1, D4 emit side)
- **WS-D · runtime library** (`zsp_rt_pkg` extensions)
- **WS-E · pssc lowering** (incremental traversal, forwarding, resolution,
  inference, resources, state, replicate, solve-groups)
- **WS-F · target, diagnostics, integration** (`sv-pure`, Tier-3, dvflow)
- **WS-G · test infrastructure & docs**

Dependency order: A,B,D can start in parallel; C depends on A,B; E depends on
C,D; F depends on E; G runs throughout.

---

## 5. Milestones

Milestones map to design §16 phasing. Each lists tasks (with repo tag) and an
**exit criterion** that is a runnable/checkable artifact.

### M0 — Foundations & scaffolding
- [x] `B0` **Scenario-IR fit spike** (gated D2): **GO-WITH-CHANGES.** Scenario IR
  is decoupled from `coro_fsm.py` (FSM pass is an optional consumer SV skips),
  target-neutral, serializes generically, and already lowers
  `sequence/parallel/schedule/select/if/repeat/foreach/atomic/do`. Findings:
  (a) Scenario IR is **currently unwired** — today's SV backend lowers from
  Layer-0, so we build a fresh be-sv consumer (`ScenarioToSV` pass).
  (b) Activity-lowering gaps to fill: `do ... with {}` inline constraints
  (raises `UnsupportedConstructError`), flow-object bindings (no node),
  `replicate` (not lowered). (c) The field-mapped `ScSolveProblem` **suffices**
  for the incremental design (solve-groups reuse it over multiple objects); the
  slot-array "generic solve mode" is **not** needed.
- [x] `G1` Cross-repo test harness: Verilator build+run helper
  (`tests/sim/sv/_verilator.py`) + M0 smoke (`test_sv_pure_smoke.py`).
  **Verilator 5.041 confirmed to support class/rand/constraint/randomize() with
  `--binary --timing`** → it is the primary CI simulator for the pure-SV path.
- [x] `G2` Decided & documented D1–D5 (see §10).
- [x] `F1` (pssc) `sv-pure` target registered (`targets/sv_pure_tgt.py`); M0 stub
  delegates to `sv-native`; `--allow-dpi` flag added.
- **Exit:** ✅ `sv-pure` selectable; harness compiles+runs golden `.sv` (SV solver
  honors constraints); target-registry tests green.

### M1 — Structured constraints in core + be-sv (WS-A, WS-C) ✅
- [x] `A1` (ir-core) Constraint IR nodes (D3) in `constraint.py`: `ConstraintBlock`
  + `ConstraintExpr/Implies/IfElse/Foreach/Unique/Soft/Dist/SolveBefore` +
  `DistWeight`; exported from package `__init__` (profile-discovered).
- [x] `A2` (ir-core) Visitor dispatch + serializer round-trip; 4 tests green
  (`tests/test_constraint_ir.py`). Full ir-core suite (72) still green.
- [x] `C1` (be-sv) Core `Expr`→SV-text emitter (D4): `ir/expr_emit.py`
  `SVExprEmitter` with hooks (field_resolver/call_hook/type_namer); ports pssc
  conventions. 17 unit tests green (`tests/unit/test_expr_emit.py`).
- [x] `C2` (be-sv) `SVConstraintEmitter` (`ir/constraint_emit.py`) renders core
  `ConstraintBlock`; `SVEmitter` accepts structured *or* legacy-string blocks.
  Emits implies/foreach/inside/unique/dist/soft/solve-before. 10 tests green.
  **Verilator-driven fix:** implication renders `ant -> (conj)` / `if(ant){..}`
  (Verilator rejects `-> { ... }`).
- [x] `E1` (pssc) `lower_constraint_func_ir` in `lower_constraints.py` emits
  structured `ConstraintBlock` (additive — string path retained for sv-native so
  goldens stay green; sv-native callers flip in M2 with the translation pass).
  7 tests green; existing 23 string-path tests untouched.
- **Exit:** ✅ structured constraint IR (align + inside + implication) →
  `SVEmitter` → SV that Verilator compiles *and solves correctly*
  (`tests/sim/sv/test_sv_pure_smoke.py::test_structured_constraints_compile_and_solve`).
  Note: end-to-end *PSS-text* parse→sv-pure lands with the M2/M3 pipeline; the
  IR→SV→solve half of the exit is proven now.

### M2 — Structured procedural & translation pass (WS-C, WS-B) ✅ (atomic subset)
*Exit met for the atomic-action subset; Scenario-IR consumer + compound/exec
built-ins carried into M3 where activities/components arrive.*
- [x] `B1` (ir-core) Scenario IR for procedural emission (D2): added `is_task`
  to `Function` (default False); `ScSeq/ScIf/ScLoop/ScInvoke/ScExecBlock`
  coverage confirmed by `B0`. Field-mapped `ScSolveProblem` reused as-is. Suite
  green (72).
- [~] `C5b` (be-sv) **`sv-pure` lowering wired** for the atomic-action subset
  (`pssc.targets.sv.lower_pure`): `SvPureTarget.run` now builds `SVClass`es via
  `core_to_sv` (fields + structured constraints + translated body) and emits a
  standalone harness — real PSS→sv-pure→SV, no DPI. *Deferred to M3:* the
  Scenario-IR `ScenarioToSV` consumer + `PSSToScenarioPass` wiring, needed once
  activities/compound actions arrive.
- [ ] `E2b` (pssc) Lower `do ... with {}` inline traversal constraints (today
  raises `UnsupportedConstructError`) into `ScInvoke.inline_constraints`
  (with activities, M3).
- [x] `C3` (be-sv) Structured SV procedural statement IR (`ir/stmt.py`) + emitter
  (`ir/stmt_emit.py`): `SVStmtIf/For/Foreach/While/Repeat/Case/Assign/Expr/
  Return/VarDecl/Raw/Comment` (`$cast` via `SVStmtExpr`+`ExprCall`). `SVTaskDecl`/
  `SVFunctionDecl` gained an optional structured `body` (legacy `body_lines`
  retained). 13 tests green.
- [x] `C4` (be-sv) `SVStmtRandomize` emit (`if (!x.randomize() with {...})
  $fatal`, void-cast when unchecked); reuses `SVConstraintEmitter` for the
  `with` body. Covered by `test_stmt_emit.py`.
- [x] `C5` (be-sv) Core→SV-IR **translation pass** (D1): `ir/core_to_sv.py` —
  `sv_type_str` (DataType→SV type, name-hooked) + `translate_field` +
  `translate_class` (fields + structured constraints → `SVClass`) +
  `translate_stmts` (core procedural `Stmt`→`SVStmt`). 5 tests green.
  Note: `passes/` is `zuspec.synth`-coupled, so this lives under `ir/`.
- [~] `E2` (pssc) `sv-pure` produces structured IR for the atomic-action subset
  (`lower_pure_action`): constraints via `lower_constraint_func_ir`, body via
  `translate_stmts`, class via `translate_class`. *Remaining (M3):* PSS exec
  built-ins (`message`/`print`/…→`$display`) in the structured statement path,
  and re-targeting the full `lower_stmts`/`lower_actions` for compound actions.
- **Exit:** ✅ **PSS source → `sv-pure` → SV → Verilator** for the atomic-action
  subset (`tests/sim/sv/test_sv_pure_e2e.py`): satisfiable constraints solve;
  contradictory constraints make `randomize()` fail (proving constraints are
  emitted + enforced); sv-pure path selected over sv-native fallback. The
  hand-built structured-IR atomic action also simulates
  (`test_atomic_action_structured_body_simulates`).

### M3 — Concurrency primitives + scheduling (WS-B, WS-C, WS-E) ✅ (static fork/join)
- [~] `B2` (ir-core) Scenario sync nodes — **deferred to M4**: the M3 exit
  (sequence/parallel) is met by lowering the activity graph directly to static
  fork/join, which needs no Scenario sync nodes. `ScEventWait`/channel/semaphore
  are pulled forward when flow-object runtime sync arrives (M4).
- [x] `C6` (be-sv) `SVStmtFork` node + emitter (`fork ... join/join_any/
  join_none`). Event/mailbox/semaphore emission deferred with `B2`. 2 tests.
- [x] `E3` (pssc) Incremental-traversal lowering for compound actions
  (`lower_pure._lower_activity`/`_traversal_stmts`): per-traversal lifecycle
  (create → comp → pre_solve → randomize → post_solve → activity), compound
  recurses. Plus PSS exec built-in mapping (`message`/`print`/`error`/`fatal`→
  `$display`/`$write`/`$error`/`$fatal`, `_map_exec_builtins`). Emitted directly
  to structured SV (not via Scenario IR — internal-representation choice; output
  identical; Scenario routing folds in at M4 with sync).
- [x] `E4` (pssc) Scheduling: `sequence`→ordered stmts, `parallel`/`schedule`→
  `SVStmtFork`. Static path (the M3 scope); runtime dataflow sync arrives in M4.
- [~] `D1` (rt-lib) `zsp_stream_channel` already shipped; `zsp_scheduler`
  helpers deferred to M4 (runtime sync).
- **Exit:** ✅ `sequence`/`parallel` over atomic actions simulate with correct
  ordering — `tests/sim/sv/test_sv_pure_e2e.py`: `sequence{a;b}` runs `LEAF_A`
  before `LEAF_B`; `parallel{a;b}` (fork/join) runs both; both reach
  `ZSP_PURE_DONE` under Verilator.

### M4 — Flow objects + static constraint forwarding + inference (WS-E, WS-D) ✅
- [~] `E5` (pssc) Binding resolution: `_collect_binds`/`_resolve_action` map
  producer→consumer buffer binds; single static producer (`bind` directive).
  *Remaining:* multi-candidate / inferred bindings (E8).
- [x] `E6` (pssc) **Buffer + state + stream** value flow: emit flow types as SV
  classes (`lower_flow_type`; buffer→`zsp_buffer`, state→`zsp_state`,
  stream→`zsp_stream`). Buffer/state bind = sequential value injection; **stream
  bind = fork + `zsp_stream_channel#(T)` put/get** (concurrent producer/consumer,
  runtime sync). **State coupling** (`next.x == prev.x`) solved at the output's
  own `randomize()` qualifying the already-solved input. Bind direction by field
  kind; SV-keyword identifier sanitization (`initial`). All Verilator-clean,
  no DPI.
- [x] `E7` (pssc) **Static constraint forwarding** — the headline. A consumer
  input constraint (`in.x==5`) is forwarded onto the **producer's buffer solve**
  (`p.out_d.randomize() with { x==5; }`) and resolved **natively, no DPI**.
  Producer-output constraints flow the same way; flow-referencing constraints are
  dropped from the action class. Proven: `consumed x=5` under Verilator.
  **Key finding:** Verilator ignores *hierarchical* constraints (`out.x` via a
  rand sub-handle, design §3.1) → buffers are randomized as their own object
  (local constraints), not hierarchically. Validates the design's §3.1 caution.
- [~] `E8` (pssc) **Demand-driven inference** (design §6.4): an unbound consumer
  input infers its producer from the candidate list (`_producers_of`); for a
  single candidate it synthesizes the producer ahead of the consumer (local var),
  forwards constraints, and binds — gated by `_inference_feasible`. Proven:
  `got x=9` under Verilator, no DPI. *Remaining:* multi-candidate runtime
  selection + recursive (depth>1) inference.
- [~] `D2` (rt-lib) buffer uses `zsp_buffer` base (shipped); `zsp_buffer_box`
  ready-event form deferred to the concurrent/stream case.
- **Exit:** ✅ back-propagating `in.x==5` buffer case + state coupling + stream
  pipeline all simulate **with no DPI** (`tests/sim/sv/test_sv_pure_e2e.py`):
  `consumed x=5` (buffer fwd), `step v=3` (state coupling), `frame d=7` (stream
  fork+channel), `got x=9` (inference). *Remaining (advanced, deferred):*
  multi-candidate / recursive inference; abstract-action+inheritance pipelines.

### M5 — Resources + static risk analysis (WS-E, WS-D)
- [~] `E9` (pssc) **Resource claim lowering** (design §7): one
  `zsp_resource_pool#(T)` per resource type declared at the activity top;
  `lock`→`claim()`/`unlock()`, `share`→`claim_shared()`/`unshare()`; the handle
  is bound to the claimed pool instance (`r = pool.get(id)`), released on
  completion (scope-based). Concurrent claims get distinct instances (claim() is
  an atomic function). Proven: 2 parallel lockers + 2 sharers run, no DPI.
  *Remaining:* constrained `instance_id` (allowed-set claim).
- [ ] `E10` (pssc) Static resource-risk analysis (design §7.1): Model-1 overlap +
  Model-2 wait-for; SAFE/INFEASIBLE/AT-RISK verdict; canonical claim order;
  claim-all-at-stage. *(deferred to next M5 slice)*
- [~] `D3` (rt-lib) `zsp_resource_pool#(T)`: added `claim()`/`claim_shared()`
  (find+lock/share a free instance). Allowed-set args deferred with constrained
  `instance_id`.
- [ ] `F2` (pssc) INFEASIBLE → compile error; AT-RISK → `try_lock`+timeout +
  warning. *(deferred with E10)*
- **Exit:** ✅ (partial) lock/share resources simulate with no DPI
  (`tests/sim/sv/test_sv_pure_e2e.py::test_sv_pure_resource_*`). *Remaining:*
  static risk verdict (E10/F2) + constrained instance_id.

### M6 — State + replicate/arrays (WS-E, WS-D) ✅ (repeat/replicate + anon + structs)
- [x] `E11` (pssc) State value flow — delivered in M4 (state coupling, no DPI).
- [ ] `D4` (rt-lib) `zsp_state_pool#(T)` version/`wait_version` — for runtime
  state ordering; current static state path doesn't need it. *(deferred)*
- [~] `E12` (pssc) **repeat/replicate + anon traversal + plain structs**:
  `repeat (N)`/`replicate (N)` → `repeat`/indexed-`for` loop (random `N` solved
  on the action then drives the loop); `do X` anonymous traversals synthesize a
  local instance (registered in the flow/resource analysis); plain structs
  emitted as SV classes. *Remaining (advanced):* replicate-fork-N into a
  parallel scope; cross-scope label arrays.
- **Exit:** ✅ `repeat`+`do` runs N times; **two full real-world patterns** now
  lower and simulate with no DPI — `resource_share.pss` (nested compound, do X,
  repeat, lock+share, parallel) and `producer_consumer.pss` (repeat burst +
  nested struct payload) both reach `ZSP_PURE_DONE`
  (`tests/sim/sv/test_sv_pure_e2e.py`).

### M7 — Solve-groups (WS-E)
- [~] `E13` (pssc) Residual joint case **detected and gated**: an input flow
  constraint coupled to the consumer's own rand field (`in.x == k`) is
  recognized (`_couples_input_to_local_rand`) and the model **falls back to
  sv-native** rather than emitting broken SV (subset-gating tests in
  `tests/unit/sv/test_lower_pure_subset.py`). *Remaining:* actually *handling* it
  via a `zsp_solve_group` joint `randomize()` — hard under Verilator's
  hierarchical-constraint limitation; deferred.
- [ ] `D5` (rt-lib) `zsp_solve_group` base. *(deferred)*
- [ ] `E14` (pssc) Cross-action resource-instance constraints → solve-group. *(deferred)*
- **Exit (interim):** ✅ the residual joint case is never mis-lowered — it falls
  back cleanly. Full pure-SV solve-group realization deferred.

### M8 — Subset gating, diagnostics, hardening (WS-F, WS-G)
- [ ] `F3` (pssc) Tier-3 detection + actionable diagnostics (design §12);
  `--allow-dpi` opt-in fallback.
- [ ] `F4` dvflow: `pure_sv` knob on `sv-native` task / `SvPure` task.
- [ ] `G3` Multi-simulator validation (≥2: Verilator + one commercial if
  available) for capacity, distribution sanity, deadlock-freedom (design §15).
- [ ] `G4` Performance/capacity benchmarks for bounded slot/solve-group sizes.
- **Exit:** full `sv-pure` pattern suite green on ≥2 simulators; Tier-3
  constructs refused with clear messages; docs complete (§7).

---

## 6. Test plan

Three levels, applied per feature:

1. **IR unit tests** (in each package's `tests/unit/`):
   - ir-core: construct each new node, serialize→deserialize→equal, `validate`
     accepts/rejects correctly.
   - be-sv: construct SV IR (or translate from core), assert emitted text
     (golden) for each construct (`test_sv_ir_emit.py` style).
2. **PSS→SV golden tests** (pssc `tests/sv/` patterns): for each PSS feature,
   assert the lowered structured IR shape and the emitted SV (golden), so
   regressions in lowering are caught without a simulator.
3. **Compile + simulate** (pssc `tests/sim/sv/`, Verilator; gated when absent):
   - **Compile**: every generated `.sv` + `zsp_rt_pkg.sv` elaborates.
   - **Behavioral**: run the scenario; assert via `ZSP_TRACE` output —
     action order, instance counts, flow values, resource exclusivity, no
     deadlock (timeout = fail).

**Feature → test matrix (each row needs unit + golden + sim):**

| Feature | Milestone | Behavioral assertion |
|---|---|---|
| atomic action + local constraints | M2 | randomize satisfies; body runs |
| sequence / parallel ordering | M3 | trace order; concurrency |
| buffer producer→consumer | M4 | consumer sees produced value |
| back-prop `in.x==5` (no DPI) | M4 | producer made x==5 |
| inference (multi-candidate) | M4 | a valid producer selected & run |
| resource lock exclusivity | M5 | no two lockers share an instance |
| over-subscribed pool | M5 | **compile-time INFEASIBLE** |
| state read/write chain | M6 | reader sees latest write; serialized |
| replicate random N (parallel) | M6 | N instances, N in [LO:HI] |
| solve-group (rand-coupled) | M7 | joint solution consistent |
| Tier-3 construct | M8 | **compile-time diagnostic** |

**Regression guard:** keep the existing `sv-native`/DPI tests green throughout;
`sv-pure` is additive until M8.

---

## 7. Documentation plan

- [ ] `DOC1` Update `docs/architecture.md`: add the lowered-IR contract and the
  core→SV-IR translation boundary (D1).
- [ ] `DOC2` New `docs/sv-ir-reference.md`: the structured SV IR / core nodes the
  backend emits (for contributors).
- [ ] `DOC3` New `docs/pss-to-sv_user_guide.md` section: the `sv-pure` subset
  (design §12), how to invoke, and the Tier-3 diagnostics catalog.
- [ ] `DOC4` Update `docs/sv_runtime_library.md`: new `zsp_rt_pkg` classes
  (design §13) with usage.
- [ ] `DOC5` `docs/targets.md` / `docs/dvflow-tasks.md`: `sv-pure` target and
  dvflow knob.
- [ ] `DOC6` Keep this plan and the design doc updated as decisions land;
  changelog at bottom.
- [ ] `DOC7` Per-milestone: short "what changed" notes in repo CHANGELOGs.

---

## 8. Risks & open questions (track to closure)

Carried from design §15, plus implementation-specific:
1. **SV solver capacity / vendor variance** — gate M8 on multi-sim benchmarks
   (`G3`/`G4`).
2. **State runtime ordering** — does the version-token suffice vs keeping state
   static? (design §9) — resolve in M6.
3. **Deadlock coverage** — validate Model-2 + canonical order + claim-at-stage on
   real activities; timeout policy (M5).
4. **Greedy completeness** — characterize residual dead-ends beyond solve-groups;
   decide if bounded-retry is needed (M7).
5. **Bounding policy** — inference depth / replicate-max / candidate-set size:
   inferred vs pragma/CLI (M4/M6).
6. **D1/D2 churn** — if pssc→core-IR proves too heavy, the fallback (pssc→SV-IR)
   changes WS-C/E scope; decide before M2.
7. **Scenario IR fit** — confirm `scenario.py` was not over-specialized for the
   coroutine-FSM path before reusing it for SV concurrency (M3).
8. **Constraint forwarding correctness** — prove forwarded constraints preserve
   PSS semantics for the FLOW_PROP vs value-determinable split (M4).

---

## 9. Tracking conventions

- This file is the source of truth; check boxes as work lands; reference task IDs
  (`A1`, `E7`, …) in commit messages and PR titles.
- Each milestone = one tracking issue/epic per repo as needed; the exit criterion
  is the issue's acceptance test.
- Update §10 when a D-decision is finalized.

## 10. Decision log

- D1 — **CONFIRMED 2026-06-26**: pssc→`zuspec.ir.core` (extended); be-sv owns the
  core→SV-IR translation pass + `SVEmitter`. pssc stays backend-neutral.
- D2 — **CONFIRMED 2026-06-26**: extend the core **Scenario IR** (`scenario.py`)
  as the concurrency layer; be-sv lowers it to `fork/join`/event/channel.
  *Precondition:* the Scenario-IR fit spike (`B0`, M0) must confirm `scenario.py`
  is not over-specialized to the coroutine-FSM path.
- D3 — **ACCEPTED** (recommended default): structured constraint IR in core.
- D4 — **ACCEPTED** (recommended default): shared core-`Expr`→SV emitter.
- D5 — **ACCEPTED** (recommended default): `sv-pure` target; opt-in DPI fallback.

All five empowered for cross-repo co-development (zuspec-* vendored as editable
installs under `pssc/packages/`).
</content>
