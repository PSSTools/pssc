# Full PSS on Zuspec bytecode: strategy, design and roadmap (for review)

**Status:** draft for review, 2026-11-20. Nothing here is built yet unless it
is marked as existing.

**Scope:** how to grow from "programming sequences, unit-tested on bc" to
"any PSS model executes": actions, activities, randomization, inference,
flow objects, resources and functional coverage. Each must run standalone on
the Python engine (pyvm) and in a simulator connected to a model of the
design. The C engine follows later and is aligned by conformance tests.

**Relationship to other designs.** This document is the umbrella. It does not
re-derive what those designs settled; it says how they fit together and what
is still missing.

| Design | Owns | Status |
|---|---|---|
| `activity-flow-resource-bc-design.md` | the planner, inference, flow objects, resources, global/OTF modes, D1–D21 | P0, P1 done; B5 (flow objects, resources) done through `nvme-bench-plan.md`; P2–P8 open |
| `pss-test-debug-env-design.md` | pssunit, platform fake, code coverage, debug | P0–P2 done, P3–P5 in progress |
| `bc-debugger-plan.md` | a DAP adapter in be-bc, a self-describing image | draft |
| `dynamic-multi-actor-executors.md` | hierarchical and distributed scenarios | assessment |
| `bc-procedural-gaps-plan.md` | procedural code on bc | done |

---

## 0. Summary

1. **Most of the PSS scenario core already runs on bc in pyvm.** This is
   further along than the "programming sequences" framing suggests. The
   following already run, with run-time solving by dv-solve and lookahead
   cones calibrated against the LRM's examples:
   - action traversal (handles, anonymous, `with`, initializers);
   - `sequence`, `parallel`, `select`, `repeat`, `replicate`, `if` and `match`;
   - component trees and `comp` choice;
   - buffer and state flow objects, `bind`, and lock/share resources;
   - every pss-corpus executable test (`expected/bc.toml` is empty).

   The gaps are well defined (§2):
   - **inference**;
   - **ordered `schedule`**;
   - **streams**;
   - **`soft`/`dist`**;
   - **procedural `randomize`**;
   - **functional coverage** (covergroups reach the IR and stop there);
   - **collections**;
   - **actions in simulation** (only functions run there today);
   - **the C engine**, which refuses every activation feature.

2. **Layering rule: pssc is the PSS language, Zuspec is execution.**
   - pssc parses, links and translates to Zuspec IR, owns the pssunit
     library, and provides the CLI.
   - Everything after the IR belongs to Zuspec packages:
     - elaboration, planning, lowering;
     - the image;
     - the engines and the host ABI;
     - platforms, coverage sampling, tracing and debugging;
     - the simulator runtime package.
   - Zuspec packages already import nothing from pssc. But a lot of execution
     code lives *in* pssc: the run glue, the platform fake, coverage sampling,
     the UCIS writer, the SV `pssvm_pkg` and the pyhdl-if shim. Moving it out
     (R0) comes first, because every later phase would otherwise add to it.

3. **The image is the contract.** Anything a run needs is in the `.zbc` image:
   - entries and activations;
   - covergroups;
   - register map and checks;
   - plan tables.

   An engine and a platform need neither the IR nor pssc. Today pssc attaches
   checks, registers and hooks to the model after lowering. That becomes
   image sections (shared with `bc-debugger-plan.md` P1).

4. **One platform seam: the host ABI.**
   - A run reaches the outside world only through ABI requests: `ACCESS`,
     `IMPORT`, `WAIT`, `NOTIFY`.
   - A register fake, a Python or Zuspec transaction-level model of the
     design, a replay log, and an RTL simulator are all *platforms* answering
     the same requests.
   - In PSS, time passes inside blocking target functions, so "connected to a
     simulator" means "blocking imports are SV tasks". No PSS construct needs
     simulator time inside the engine.
   - The same test therefore runs against a fake, a model and RTL, and the
     traces can be compared.

5. **pyvm is the reference engine.** Each feature lands as four parts:
   - a spec section in `zuspec-be-bc/docs/spec/`;
   - a pyvm implementation;
   - an engine-parametrized conformance test;
   - in rt-eng, an implementation or a refusal before the run starts (the
     pattern rt-eng already follows).

   The C engine catches up in R8 against that suite. Nothing is designed in a
   way C cannot implement: policy-heavy work (planning, solving, pool
   arbitration, coverage binning) goes through defined runtime services, not
   Python-only shortcuts.

6. **Get to "a randomized action scenario on RTL" early, then broaden.** The
   first milestone is a vertical slice on WB DMA (§7, R0 + R2a + R6a):
   - randomized channel-configuration actions;
   - a covergroup over the configuration;
   - run standalone against the register fake and a PSS mock of the DMA,
     then in Verilator against the RTL (`examples/fw_wb_dma_uvm` already has
     the bench).

   It needs no inference. It proves the layering, the image contract, the
   platform seam and action entries in simulation, all before the heavy
   planner work.

---

## 1. Goals and non-goals

**Goals**
- G1. Every execution-oriented PSS construct has a defined bc representation
  (opcodes, image sections, or a runtime service) and a pyvm implementation,
  or a located refusal. Silent drops are not allowed.
- G2. One image runs standalone (pyvm, pssunit, CLI) and in a simulator,
  unchanged.
- G3. Randomized action scenarios are tests: pssunit `test_a` actions with
  activities, run over seeds, with checks, coverage and replay.
- G4. Functional coverage (covergroups) and code/register coverage land in one
  UCIS database (covsight), mergeable across tests, seeds and platforms.
- G5. Correctness rests on the LRM and an independent oracle (the pss-corpus
  checker), never on pssc's own output.
- G6. Zuspec stays independent of pssc. Another front end (Python Zuspec
  dataclasses, fw-hdl) can reuse the same execution stack.

**Non-goals for now**
- C-engine parity (R8). It is designed for but not built.
- Commercial simulators beyond one smoke target.
- On-silicon generators. Pick logs and executors are designed for
  (`dynamic-multi-actor-executors.md`, D21), not built.
- Retiring the generated-SV (`sv` target) and be-py live-runtime paths. That
  is a decision (S9), not part of this work.

---

## 2. Where we are

Evidence comes from code inspection on 2026-11-20. File references are
relative to `packages/` for zuspec and to the repo root for pssc.

### 2.1 Pipeline today

```
PSS ─► pssparser ─► pssc ast2ir ─► Zuspec IR
                                     │
       zuspec-ir-core xf/pss_lower:  PSSToScenarioPass
         layout · action_tree · comp_tree · pools · constraints · defaults
                                     │ ScenarioModule
       zuspec-be-bc lower_module ────┘─► ZbcModel ─► .zbc
                                     │
       pyvm (be-bc interp/: vm, scheduler, activation, solve_cache)
       rt-eng (C: procedural, CALL, SOLVE; refuses activations)
                                     │ host ABI (HostCall / pssvm_run)
       platforms: pssc testing/ (fake, mocks, replay, sim_platform)
                  pssc share/sv/pssvm (SV), share/py/pssvm_dpi_shim.py
```

The PSS-specific lowering is already in Zuspec (ir-core `xf/pss_lower`), and
the run-time activation semantics are in be-bc (`interp/activation.py`). That
split is right, and this design keeps it.

### 2.2 Construct inventory

The levels are:
- **op**: an opcode;
- **svc**: a runtime service called by the VM (activation hooks, solver);
- **lower**: decided at lowering.

| Construct | State | Level |
|---|---|---|
| Traversal: handle, anonymous, `with`, initializers, handle arrays (static index) | done | op `INVOKE\|NODE`, action tree |
| Handle array, run-time index | refused | — |
| `sequence`, `if`, `match`, `repeat`, `repeat…while`, `replicate`, `atomic` | done | op |
| `select` | partial: weighted draw, not filtered for feasibility | op `SELECT` |
| `parallel` with join ALL/NONE/FIRST | done; join SELECT/BRANCH refused | op `SPAWN/JOIN` |
| `schedule` | partial: refused when members interact (activity D4) | lower |
| activity `while`, `foreach` | refused | — |
| Activity constraints, `bind` | done | svc (cones) |
| Exec `pre_solve`, `post_solve`, `body`, `init_down`, `init_up`, `super;` | done | lower |
| Exec `run_start`, `run_end` | **silently dropped** (bug) | — |
| Exec `header`, `declaration` (target templates) | absent | n/a on bc |
| Constraint solving, `default` | done; run-time dv-solve over blobs built at lowering | svc |
| Lookahead (cones) | done in pyvm | svc `SOLVE_NODE` |
| `soft`, `dist` | refused (recorded as untranslated) | dv-solve supports both |
| `solve…before` | accepted and dropped (dv-solve supports a solve order; bc does not pass it) | — |
| Constraint `foreach` | deferred | — |
| Procedural `randomize` | absent (not translated by ast2ir) | — |
| Buffer and state flow objects, pools | done (no `prev`/`uid`) | svc |
| Streams | refused | — |
| Resources lock/share (`instance_id`, ≤64) | done; attributes and claim arrays refused | svc |
| Inference | absent (located error) | — |
| Components, `comp` choice, executors (static) | done | op `LD/ST_COMP`, lower |
| Covergroups | IR (inline in action or struct, no bins); nothing on bc | — |
| Code coverage (statements, branches), register coverage, operation envelope | done (pssc sinks over be-bc trace events) | trace |
| Strings | interned; `==`/`!=` only; not passed to imports | — |
| Data arrays, `list`, `map`, `set` | refused | — |
| Native functions; recursion as `CALL` | done (no struct parameters or results) | op |
| Import/target functions | done; ≤2 arguments at orchestration level | op `IMPORT` |
| Channels (`sync_pkg`) | done; blocking ops spin on `YIELD\|SPIN` | op |

### 2.3 Execution environments

**Standalone.**
- `pssc test` runs pssunit functions and `test_a` actions on pyvm, with the
  register fake, PSS reaction-hook mocks or Python doubles, and seeds.
- `pssc run` does not exist (`future-projects.md`). The ~20-line run glue is
  copied in `tests/compliance/adapters/pssc_bc.py` and `scripts/bench_bc.py`.

**Simulation.**
- `op-model-sv-vm` and `pssvm_pkg` run **exported functions only**. An
  exported action prints "not run by the engine yet" (`targets/sv_vm/facade.py`).
  The shim and the runner accept only `kind == "function"` entries.
- The host ABI has `pssvm_call_start_obj` for actions, but nothing in SV
  uses it.
- **One request per call:** a `parallel` inside one call cannot overlap bus
  transactions.
- Executors are inlined statically, so a request carries no executor
  identity for the platform to route to an agent.
- Host events: IRQ only; `CHAN` notify is unimplemented.
- Only Verilator is tested.

**C engine.** It refuses `SCOPE_ENTER`, `SOLVE_NODE`, `INVOKE|INITED` and the
component ops, so it runs no activity.

### 2.4 Execution code living in pssc

None of this is about the PSS language, so all of it moves into Zuspec
packages (R0):

| pssc today | What it is |
|---|---|
| `testing/bc_session.py` `run`/`_drain`/`_call_root`; `testing/vm.py` `Session` | run an image on a platform |
| `testing/fake.py`, `reg_semantics.py`, `bc_session._registers` | register model and field semantics |
| `testing/coverage.py` (sampling, bins, crosses), `cov_ucis.py` | coverage service and UCIS writer |
| `testing/trace.py`, `replay.py`, `sanitizers.py` | trace writer, replay platform, access monitors |
| `testing/native.py` engine build and cache | C engine packaging |
| `share/sv/pssvm/*.sv` | SV binding of the host ABI |
| `share/py/pssvm_dpi_shim.py` | pyhdl-if host for pyvm (imports `pssc.testing`) |
| `devenv/debug/*` | debug adapter (`bc-debugger-plan.md` moves it) |
| `devenv/build_cmd.py` image metadata | image sections (§3.3) |

These stay in pssc:
- ast2ir;
- `pssunit_pkg` and its check implementations, `check_rewrite`, hook
  resolution;
- discovery, slicing and reporting;
- `pssc test`, `pssc run`, `pssc cov` as front ends.

---

## 3. Principles and architecture

### 3.1 Target architecture

```
 pssc (PSS language)               other front ends (Zuspec dataclasses, fw-hdl)
   parse · link · ast2ir                   │
   pssunit_pkg · pssc test/run/cov/dbg     │
          │ Zuspec IR (PSS dialect)        │ Zuspec IR
          ▼                                ▼
 zuspec-ir-core   static elaboration (comp tree, pools, action tree, layout,
                  covergroup model) · planner (xf/plan) · PSSToScenarioPass
          │ ScenarioModule
          ▼
 zuspec-be-bc     lower ─► .zbc (self-describing)
                  pyvm (reference engine) ─ runtime services:
                     solver (dv-solve) · activation/planner · coverage · trace
                  host ABI spec · run API · platform protocol
                  platforms: fake registers · Python/Zuspec model · replay
                  SV binding of the host ABI (pkg + pyhdl-if host)
          │ same ABI, same image
          ▼
 zuspec-rt-eng    C engine (later parity) · DPI host
 covsight         UCIS database: code, register, functional coverage
```

### 3.2 Principles

- **P1. Layering.** Zuspec packages never import pssc. This is already
  true; R0 adds a test that keeps it true. pssc may import Zuspec freely. A
  PSS-language decision (scoping, linking, typing a literal) is pssc's. A
  semantic decision about *execution* (what a traversal does, when a
  covergroup samples, how a pool arbitrates) is Zuspec's, and is specified in
  be-bc's `docs/spec/`.
- **P2. The image is the contract.**
  - The engine and the platforms read the image only.
  - A front end's private data goes in a namespaced metadata section that
    engines ignore (pssc's `pssunit` section holds checks and hooks).
  - The register map is not PSS-specific. It gets its own section, which the
    debugger and the platform fake both use.
- **P3. Opcodes for mechanism, services for policy.**
  - Control flow, data movement, suspension and sampling are opcodes.
  - Solving, planning, pool arbitration and coverage binning are runtime
    services with a specified interface. pyvm implements them in Python; the
    C engine implements them in C, or calls the same service through a
    host callback.
  - This generalizes how activation hooks already work, and keeps policy
    changes from changing the instruction set.
  - The activity design's D2 (flow and resource primitives as opcodes) stands:
    those are mechanism.
- **P4. No silent drops, at any layer.** Registry tests already cover
  pssparser nodes, IR kinds, activity nodes and body elements. R0 extends them
  to the lowering into bc: every IR element is lowered or refused with a
  location. `run_start`/`run_end` is the case that slipped through.
- **P5. Reference engine, then conformance.** Each spec section has
  engine-parametrized tests. pyvm passes them first; rt-eng passes them or
  refuses up front.
- **P6. Determinism.**
  - Seeded and planned arbitration by default (activity D3).
  - A recorded seed and image reproduce a run on any platform whose
    responses are the same.
  - Timing independence is tested (activity P3).
- **P7. Calibrated tests.** A test of a semantic property must fail with that
  property switched off. The lookahead tests already follow this rule; it
  extends to coverage sampling, soft constraints and inference.

- **P8. dv-solve is the one solver.** Every solve goes through dv-solve:
  - a per-node or cone solve;
  - procedural `randomize`;
  - the planner's joint value solve;
  - inference nogoods (contradiction analysis);
  - the enumeration oracle.

  zuspec-solver's job is to translate Zuspec IR into dv-solve problems
  (`translate/walk.py`); it is not a second engine. When PSS needs something
  dv-solve lacks, **the feature goes into dv-solve**, with its own tests. No
  workaround is built in the runtime, the lowering or pssc. Because dv-solve
  is C, both engines get the same solver semantics from the same library,
  which removes a whole class of pyvm/C divergence (R8). §4.10 lists the
  features expected.

### 3.3 The self-describing image

New or extended `.zbc` sections. Formats are owned by be-bc's
`format/spec.py`.

| Section | Contents | Replaces |
|---|---|---|
| `ENTRY` (extend) | kind (function/action/test), qualified name, parameter names and types, the action's object layout | pssc-side discovery data |
| `LAYOUT` | component and action-object slot layouts with names and types | the debugger's IR reads |
| `REGMAP` | register map, field kinds, groups, offsets | `bc_session._registers`, the fake's IR reads |
| `COVG` | covergroup types, instances, sample sites, bins, crosses (§4.6) | — |
| `PLAN` | the planner's tables (activity P3) | — |
| `META:<ns>` | front-end-private data (pssc: checks, hooks, pssunit annotations) | `model.checks`/`hooks` attached after lowering |

---

## 4. Representing PSS in bytecode, construct by construct

Each subsection gives the semantics owner, the representation, and the phase.

### 4.1 Actions and traversal (done; fill-ins in R4)

- **Representation:** an activation is one object; a traversal is
  `INVOKE|NODE` at a static base (action tree).
- **Remaining:**
  - handle arrays with a run-time index: a dispatch, as component arrays
    already do;
  - activity `while` and `foreach`: loops over `SCOPE_ENTER`, with `foreach`
    over a handle or data array once arrays exist (R3).

### 4.2 Activity scheduling (R4, following activity P2/P3)

- `sequence`, `parallel` and `select` are done.
- Ordered `schedule`, the SELECT/BRANCH join specs and feasibility-filtered
  `select` all need the planner (activity P3).
- **Representation:** the plan section (`PLAN`) plus the D2 primitives
  (`EV_WAIT/EV_SET`, `RDV`, `ST_ACQ/REL`, `CLAIM/RELEASE`). Global mode runs
  these as monitors; OTF mode enforces with them.

### 4.3 Randomization (R1)

| Item | Representation |
|---|---|
| `soft` | ast2ir translates it; ir-core carries `soft` with a priority. The solve blob uses dv-solve's soft constraints (`problem_add_soft_constraint`; relaxation by priority). The trace records which softs were dropped, for debugging. |
| `dist` | translated into the blob as a dv-solve dist (dv-solve has `n_dists`; a dist reading a rand variable is `DVS_COMPILE_DIST_RAND` and needs an LRM decision). Distribution is tested statistically and calibrated. |
| `solve…before` | It affects the distribution, not legality. The blob carries the order, and the solve sets dv-solve's `seq_pick`, which is what that option is for. No runtime-side sequencing is needed. |
| Constraint `foreach`, random array sizes | dv-solve's array problems (`dvs_randomize_arrays`, `dvs_randomize_sizes`: sizes first, then a `foreach` per element, as SV does). Needs bc arrays (R3); no unrolling in lowering. |
| Procedural `randomize x with {…}` | a new IR statement. The lowering builds a solve problem over the *local's* slots plus the inline constraints, and uses the existing `SOLVE` op on a frame-local object. No new opcode is needed if `SOLVE` takes a frame-relative base. |
| `randc` | dv-solve's RANDC variables keep their cycle on the solver handle. The solve service keeps one handle per object, so the C engine gets the same behaviour from the same library. |
| Random stability | `determinism.md` defines the seed tree (per thread, per node). R1 adds a stability test: adding an unrelated action does not change an existing traversal's values when the seed is the same (activity D9 sets the limits of this promise). |

### 4.4 Flow objects and resources (mostly done; rest in R4/R5)

- Streams: a rendezvous primitive (`RDV`, activity D2), paired by the
  planner.
- Resource attributes and claim arrays: the claim table generalized from a
  64-bit mask to sets.
- `prev`/`uid`: activity P4b.

### 4.5 Inference (R5 = activity P3/P4)

- No new mechanism is proposed here. The activity design's planner (structure
  search plus a joint value solve, depth limits from D5, atomic exclusion,
  nogoods from dv-solve contradiction analysis) is the plan.
- **Representation:**
  - an inferred action is a node the planner adds to the action tree;
  - its coroutine is in the image because lowering emits every action type
    that a pool binding makes a candidate producer;
  - the plan records the inferred nodes;
  - OTF mode uses `PLAN`/`REPLAN`.

  This is why the image must carry every candidate (P2): the engine cannot
  lower a new action at run time.
- **Testing:** the exhaustive-enumeration oracle on tiny models, plus the
  corpus L4 tests and the fault-injection kill matrix.

### 4.6 Functional coverage (R2), a new design

**Semantics owner:** Zuspec. pssc only translates syntax.

1. **IR.**
   - ast2ir translates covergroup types, instances and inline covergroups in
     full: coverpoints with `iff`, explicit bins (values, ranges, arrays,
     `default`), `ignore_bins` and `illegal_bins`, crosses, and
     `option`/`type_option` (weight, goal, `at_least`, `per_instance`).
   - The target is ir-core's richer `CovergroupDef` model. It is made pure
     data (expressions instead of Python callables), so that one model serves
     both the bc and the SV targets.
   - The thin `PssCoverGroup` is retired.
   - Refusals keep their locations until each form lands.
2. **Sampling** (LRM 19): per sample site. The exact sampling points are
   pinned by corpus tests before implementation; no guesses are written into
   the spec.
   - A covergroup in an action or flow object samples once the action's
     values are final, after `post_solve`.
   - A covergroup in a struct samples when that struct is randomized.
   - An explicit `sample()` call samples at that call.
3. **bc representation.**
   - Lowering emits one *sample function* per covergroup type: ordinary
     bytecode that evaluates each coverpoint expression and its `iff` into
     registers.
   - The function ends with a new op, `COV_SAMPLE inst, n, r0…`, which hands
     the values to the coverage service.
   - Bins, crosses and options are data in the `COVG` section.
   - The engine's job ends at "instance *i* was sampled with values *v*",
     which C can implement cheaply.
   - The binning is a service. It runs in Python for both engines at first:
     the C engine emits `EV_COV` trace events, which the host bins. Native
     binning is an R8 optimization.
4. **Database.**
   - The coverage service writes covsight UCIS covergroups next to the
     existing code, register and envelope coverage.
   - The register and envelope coverage was already shaped as covergroups for
     exactly this reason (pss-test-debug-env D1).
   - The service, sinks and UCIS writer move from pssc to Zuspec (R0), so the
     same database is filled standalone and in simulation.
5. **Use in tests.**
   - A pssunit check asserts coverage (`check_covered("cg.cp.bin")`).
   - `pssc cov gaps` lists unhit bins across a merged run.
   - Coverage-driven steering (structural coverage, D19) stays deferred, but
     the database is the input it will use.

### 4.7 Exec blocks

- **`run_start`/`run_end`:** run around the root activation, and in
  simulation once per run. Fixed in R0 as a silent-drop bug.
- **`header`/`declaration` target templates:** these are code-generation
  constructs, not execution. bc refuses them with a location. Op-model
  targets keep them.

### 4.8 Data types (R3)

- **Fixed-size data arrays:** slot ranges (already how layouts work), plus
  indexed load and store with a run-time index (`LD/ST_FIELD_IX`, bounds
  checked).
- **`list`, `map`, `set`:** need a heap. That is a VM-level object store with
  handles in slots, reference counting or a per-run arena, and size and
  iteration ops.

  This is the most invasive item for the C engine. It is spec'd before it is
  built.
- **Strings:** add ordering, concatenation and formatting, and allow passing
  a string to an import (the ABI carries it as a string-table index plus a
  host-side lookup).
- **Struct parameters and results** across `CALL` and `IMPORT`: by slot range,
  as the value ABI (`value-abi.md`) already describes for scalars.
- **Import arity:** remove the 2-argument orchestration limit. This also
  removes the need for pssc's `check_rewrite` workaround.

### 4.9 Concurrency, time and events (R6)

- **Time.** PSS consumes time only through blocking target functions and
  `sync_pkg`.
  - In simulation, a blocking import is an SV task.
  - Standalone, it is a platform method, and a Python design model may
    advance its own time.
  - The engine's `WAIT TIME` stays for non-PSS front ends.
- **Events from the design.** Generalize `NOTIFY`:
  - a host event handle, bound by name in the image, that PSS code waits on
    through a blocking import or a channel;
  - `CHAN` notify gets implemented;
  - an IRQ becomes one kind of host event.
- **Host ABI v2.**
  - **Concurrent requests:** each request carries a thread id. The host may
    hold several outstanding requests and complete them in any order. That
    gives `parallel` branches overlapping bus traffic, and is the fix for
    "one request per call".
  - **Executor identity:** a request carries the executor (instance id plus
    PSS path) its thread runs on, so a platform routes it to an agent or
    sequencer. Executors stay statically resolved where possible; the id is
    carried, not looked up.
  - **Action entries:** `call_start_obj` returns the final action object, so
    the testbench can read solved values for scoreboarding.
  - **Units:** the engine's time unit is stated in the image and converted by
    the host.

  The v2 changes are additive. A v1 host never sees a second outstanding
  request.

### 4.10 dv-solve features expected

dv-solve already implements SV-style randomization. Constraints already
exercised include:
- `soft` with ranks;
- `dist`;
- RANDC;
- a solve order (`seq_pick`);
- arrays with random sizes and `foreach`;
- pins, checkpoints, incremental solves;
- contradiction analysis;
- `canonical` mode.

So R1 is mostly translation work. These are where PSS needs more, each to be
confirmed against dv-solve before it is scheduled. Each becomes a dv-solve
work item with tests in dv-solve, owned there.

| Need | Why | Phase |
|---|---|---|
| PSS soft priority (LRM 16.1.10) mapped to dv-solve ranks | PSS orders softs by declaration and nesting (type extension, traversal `with`, activity constraints), not by SV class structure. Give the problem an explicit rank per soft if the existing rank rule cannot express it. | R1 |
| `dist` whose weights or ranges read a rand variable (`DVS_COMPILE_DIST_RAND` today) | Settle what the LRM allows first. Then support it, or refuse it with a location. | R1 |
| Unsat reported by source | a cone's unsat names the PSS constraints involved (`future-projects.md`: unsat back to provenance) | R1 |
| Structural variables for the planner | which producer to infer, which branch, which instance and which order are finite-domain choices solved jointly with values. This needs incremental addition and removal of constraint groups (assumptions), with nogoods returned for retry. | R4, R5 |
| Model enumeration and counting on small problems | the exhaustive-enumeration oracle (activity 12.2) and statistical distribution tests | R4 |
| Collections (`list`, `set`, `map`) in constraints | `size()`, membership (`in`), uniqueness over a list | R3 |
| Coverage-directed bias (later) | prefer values that hit unhit bins: a weight or bias hook per variable | after R2 |
| Solve-call cost on small cones | pyvm scenarios are dominated by many small solves. Profile them, and add a compile cache keyed by problem shape if needed (`solve_cache.py` does this host-side today) | R0 benchmarks, then as needed |

---

## 5. Execution environments

### 5.1 Standalone (pyvm)

- **Run API in be-bc:** load an image, pick an entry, give a platform and a
  seed, and get a result, a trace and coverage. It is moved out of pssc's
  `bc_session`/`Session`.
- `pssc run --root C::A --seed N` and `pssc test` are front ends over it. A
  `zuspec-bc run image.zbc --entry … --platform …` command shows that it
  needs no pssc.
- **Platforms**, all implementing one `Platform` protocol (memory, imports,
  events, time):
  - the register fake;
  - **a design model in Python**: a Zuspec dataclasses component or plain
    Python. This is the "model of the design" without an HDL simulator, and
    the cheapest closed loop for scenario development;
  - **a PSS mock** (`mock_c` with reaction hooks), running in the same VM;
  - replay.
- **pssunit for scenarios:**
  - a `test_a` with an activity is a test;
  - `@property(runs=N)` sweeps seeds;
  - a failure prints a replay line (seed plus image hash);
  - coverage is merged across seeds.

### 5.2 Simulation

**Path for the Python engine:** pyvm through pyhdl-if (exists today), driven
by the SV runtime package.
- The package moves to be-bc (S4), with the Python host rewritten over the
  run API: no `pssc.testing` import.
- **cocotb:** an alternative host worth a spike (S6). pyvm already runs in
  Python, so a cocotb platform needs no DPI layer: platform requests become
  cocotb coroutines on bus drivers. It would add a mixed-language-free path
  and open-source simulators beyond Verilator.

**Scenario support needed in the SV runtime (R6):**
- action entries in the facade and runner;
- seeds from plusargs;
- ABI v2's concurrent requests, each served by its own SV thread and routed
  by executor id;
- host events from monitors;
- trace and coverage written from the simulator;
- run_start/run_end.

**Portability check:** the same `test_a` runs on the fake, a Python model and
RTL. Bus traces are compared on the parts that are timing-independent (per
executor, in order).

### 5.3 C engine (R8)

The C engine is ported against the conformance suite in this order:
activations, component ops, flow and resource primitives, the `COV_SAMPLE`
event, `soft`/`dist` solve blobs, and the heap last. The planner stays a host
service, or becomes a precompiled solve per scope (activity P8).

---

## 6. Test strategy

| Layer | What | Where |
|---|---|---|
| Front end | IR shape for every construct; registry tests (no silent drops) | pssc `tests/unit` |
| Elaboration and planner | table tests; exhaustive-enumeration oracle; cone telemetry | ir-core tests |
| Image | round trip; `spec.py` ↔ C header generation | be-bc |
| Engine semantics | hand-built scenario IR → pyvm; spec conformance tests parametrized over engines | be-bc, rt-eng |
| Language end to end | pss-corpus executable tier, judged by the corpus checker. To grow: L4 flow/resource, inference, randomization distribution (statistical), coverage outcomes (the checker reads a coverage export) | pss-corpus + `tests/compliance` |
| Platform | the same test on fake, Python model and Verilator; trace comparison | pssc `tests/unit`, examples |
| Calibration | each semantic test fails with its feature disabled (lookahead, soft, coverage sampling, inference) | everywhere |
| Performance | `scripts/bench_bc.py` budgets: solves per second, steps per second, cone size | `tests/perf` |

The corpus checker, not pssc, judges the outcome. A pssc disagreement with the
corpus is settled by the LRM, never by editing the corpus model.

---

## 7. Roadmap

Each phase ends with a gate that tests check. The phases are ordered by
dependency, and some can run in parallel (see the graph at the end of this
section).

### R0: Foundations (layering, run API, image contract)

**Work**
1. A layering test: no Zuspec package imports pssc.
2. A be-bc run API and `Platform` protocol, moved out of `bc_session`/`Session`.
   `pssc test`, the compliance adapter and `bench_bc` use it, which deletes
   the copied run glue. Add `pssc run`.
3. Image sections `ENTRY` (extended), `LAYOUT`, `REGMAP` and `META:pssc`.
   pssc stops attaching data to the model after lowering. (Shared with
   `bc-debugger-plan.md` P1; do it once.)
4. Move the register fake, the coverage service and UCIS writer, the trace
   writer, replay and sanitizers to Zuspec. pssc keeps the pssunit-specific
   parts.
5. Fix `run_start`/`run_end`. Add a bc lowering registry test (P4).
6. Spec catch-up: `activation.md` is stale on `ACT`.

**Gate:** the whole pssc suite is unchanged in result; the layering test
passes; `zuspec-bc run` runs a compliance image with no pssc on the path.

### R1: Randomization completeness

**Work:** translate `soft`, `dist`, procedural `randomize`, `randc` and
`solve…before` into dv-solve problems; add the R1 dv-solve items from
§4.10; add the random-stability test. Constraint `foreach` waits for R3's
arrays.

**Gate:** corpus randomization tests (new, statistical, calibrated) pass;
`expected/bc.toml` stays empty.

### R2: Functional coverage

- **R2a (slice):** covergroups inline in actions; coverpoints with
  automatic and explicit value bins; crosses; sampling after `post_solve`;
  `COV_SAMPLE`; `COVG`; UCIS output.
- **R2b:** covergroup types and instances, `iff`, ignore/illegal bins,
  options, struct sampling, `sample()`, the SV target onto the same IR
  model.

**Gate:** corpus coverage tests; one `.cdb` holds code, register and
functional coverage for one run; `pssc cov merge` across seeds.

### R3: Data types

**Work:** fixed arrays with a run-time index; constraint `foreach`; strings;
struct parameters and results; import arity. Then collections on a heap,
spec first.

**Gate:** corpus `types.*` and `proc.*` additions pass; rt-eng refuses the
heap ops up front.

### R4: Activities completeness (= activity P2, P3, P3c)

**Work:** static elaboration; planner v0; ordered `schedule`; join specs;
feasibility-filtered `select`; activity `while`/`foreach`; streams pairing;
the corpus checker's P2/P3.

**Gate:** as set in the activity design (UC1, 2, 6–10; timing fuzz).

### R5: Inference (= activity P4, P4b)

**Gate:** UC3, 5, 12; the enumeration oracle; the fault-kill matrix.

### R6: Scenarios in simulation

- **R6a (slice):** action entries in the SV runtime (facade, runner, host);
  seeds; trace and coverage from the simulator; run_start/run_end. The SV
  runtime and the Python host move into be-bc (S4).
- **R6b:** host ABI v2 (concurrent requests, executor id, host events,
  returned action object, time units); one SV thread per outstanding
  request; routing by executor id.
- **R6 also carries bench-failure attribution** (`bench-failure-attribution.md`):
  - A1 and A2 can land with R6a, or earlier on the function-only path;
  - A3 lands with R6a;
  - A5 lands after R4.
- **R6 also carries checking in PSS** (`pss-checking-design.md`):
  - K1, K2 and K4 alongside R6a;
  - K3, K5 and K6 after R6a;
  - K7, monitors, after R2.
- **R6c:** a smoke run on one commercial simulator; the cocotb host spike (S6).

**Gate:** the WB DMA slice test passes on the fake, a Python DMA model and
Verilator RTL, with matching per-executor bus traces and merged coverage.

### R7: OTF and live arbitration (= activity P5, P6)

Built when a live platform needs it.

### R8: C engine alignment

**Work:** a conformance suite over both engines; port in the order given in
§5.3.

**Gate:** an rt-eng differential against pyvm on the corpus and the L4 suite.

### Order and parallelism

```
R0 ──┬─► R1 ──► R3 ─┐
     ├─► R2a ─► R2b │
     ├─► R6a ─► R6b ┼─► R6c
     └─► R4 ──► R5 ─┴─► R7 ──► R8
```

**First milestone (the vertical slice): R0 + R2a + R6a.**
- WB DMA channel-configuration actions with rand fields and constraints, a
  covergroup over the configuration, and a compound scenario action
  (`parallel` over channels).
- It runs standalone against the fake and a PSS mock of the DMA, and in
  Verilator against `fw_wb_dma_uvm`'s RTL view.
- No inference and no planner are needed: today's activity support covers it.
- Once it passes, scenario tests can be written while R4/R5 proceed.

---

## 8. Decisions for review

| # | Question | Recommendation |
|---|---|---|
| S1 | Where the host-side runtime (run API, platforms, coverage service, trace) lives | In **zuspec-be-bc** (`zuspec.be.bc.run`, `.platform`, `.cov`). It is code over be-bc's own ABI and trace schema. The alternative is a new `zuspec-rt-py` package; split later if be-bc grows too large. |
| S2 | `solve…before` | Settled: pass the order to dv-solve (`seq_pick`). Kept as a row only so that numbering is stable. |
| S3 | Where the PSS-specific lowering lives | It stays in **ir-core `xf/pss_lower`** (the PSS dialect of Zuspec IR); pssc stays a front end. The alternative is a `zuspec-pss` package, only if ir-core's dependency weight becomes a problem. |
| S4 | Where the SV runtime package (`pssvm_pkg`, host) lives | **be-bc `share/sv`** (it binds be-bc's ABI). The C DPI library stays in rt-eng. pssc's `sv-pssvm-path` points there. |
| S5 | The first vertical slice | WB DMA as described in §7; the alternative is the NVMe bench model (no RTL) |
| S6 | Python engine in simulation: pyhdl-if only, or a cocotb host too | Keep pyhdl-if; spike cocotb in R6c |
| S7 | Coverage binning: engine or host service | A host service for both engines first; native binning later |
| S8 | Collections: heap design before or after the slice | After. Spec it in R3; nothing in the slice needs it. |
| S9 | The fate of the generated-SV `sv` target scenario path and be-py's live `ScenarioRunner` | Freeze them. Harvest be-py's flow and resource models into the corpus (activity 12.9), then decide retirement once R4/R5 pass. |
| S10 | pyvm speed (~11 µs per loop iteration; solves dominate scenarios) | Accept it for standalone and the slice. Budget it in `tests/perf`. Native binning, solve caching and the C engine are the levers. |

---

## 9. Risks

- **pyvm speed in simulation.** Long scenarios with many solves may be slow
  under a simulator. Mitigations: solve-cache hit rates (cone reuse, B6),
  measuring in the slice, and R8.
- **Solver features on the critical path.** R1, R4 and R5 depend on the
  dv-solve items in §4.10. Each is scheduled into dv-solve before the phase
  that needs it, with a spike to confirm what dv-solve already covers.
- **Planner complexity (R4/R5).** This is the largest item. It is already
  designed, with oracle-backed tests, but it remains the schedule risk. The
  slice is ordered so it does not wait on it.
- **The heap for collections** adds GC-like concerns to the C engine. Spec it
  first and keep it per-run (an arena).
- **Two other scenario runtimes** (the generated-SV target and be-py's
  runner) drift while bc grows. S9 freezes them.
- **Moving code out of pssc (R0)** touches many tests. The gate is "suite
  unchanged", and nothing else is done in the same change.

---

## 10. Status

- [x] Research and inventory (§2), 2026-11-20
- Implementation tracking: `full-pss-implementation-plan.md` (phases F0–F14)
- [ ] Review: decisions S1–S10
- [ ] R0 · [ ] R1 · [ ] R2a · [ ] R2b · [ ] R3 · [ ] R4 · [ ] R5 · [ ] R6a · [ ] R6b · [ ] R6c · [ ] R7 · [ ] R8
- Nothing committed.
