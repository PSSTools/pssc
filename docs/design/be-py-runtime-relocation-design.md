# Design: Relocate the Python Runtime & Solver into `zuspec-be-py`

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

**Date:** 2026-06-28
**Branch:** `migrate/pssc-phase1`
**Status:** Draft for review
**Companion:** `docs/zuspec-dataclasses-dependency-audit.md` (the audit that motivated this)

---

## Goal

Make `zuspec-dataclasses` **just a frontend** (a Python-source authoring DSL that emits `zuspec-ir-core` IR). Relocate the Python *execution runtime* and *constraint-solver* behavior into `zuspec-be-py`. End state:

> **`pssc` (and `zuspec-be-py`) have no dependency on `zuspec-dataclasses` except in tests.**

This requires **inverting** the current dependency edge: today `be-py → dataclasses`; the target is `dataclasses → be-py` (dataclasses, when it wants to *run* a model, uses the be-py runtime), with `pssc → be-py` and `pssc ⊥ dataclasses` in production.

---

## What "frontend" must mean here

A frontend converts source → IR. For `zuspec-dataclasses` that is: the `@zdc.dataclass` / `@zdc.field` decorators, the scalar type vocabulary (`u32`, `bit`, `bv`, …), and the lowering that produces `zuspec.ir.core` IR. Everything that *executes* IR — randomization, action/activity scheduling, resource claiming, coverage sampling — is **not** frontend and should live with the backend that runs it.

---

## Current reality (empirically mapped)

`zuspec-dataclasses` today is **three things fused into one package**:

| Role | Where | Size | Belongs in |
|---|---|---|---|
| **1. Frontend / authoring DSL** | `decorators.py`, `types.py` (`Component`, `Action`, `field`, `u32`/`bit`/`bv`), `activity_dsl.py`, `constraint_parser.py`, … | — | **stays** (`zuspec-dataclasses`) |
| **2. Object / runtime type-model** | `types.py` + `decorators.py` (`@dataclass`, `Component`, `Action`, `field`, `pool`, scalar types) used *as the runtime object representation* | — | **the crux — see §“Central decision”** |
| **3. Execution runtime + solver + coverage** | `rt/` (≈13.3k LoC, ~60 modules), `solver/` (≈17.3k LoC), `coverage/` (≈1.2k LoC) | ~31.8k LoC | **moves → `zuspec-be-py`** |

### The dependency tangle (why this is not a directory move)

```
            ┌────────────────────────── zuspec-dataclasses ───────────────────────────┐
            │  frontend (types.py, decorators.py)                                      │
            │      │  (lazy) types.py → rt.activity_runner / action_context /          │
            │      │          action_infra / list_claim_pool   ← frontend USES runtime │
            │      ▼                                                                    │
            │   rt/  (PSS-exec: executor, ir_compiler, scenario_runner, activity_      │
            │      │   runner, import_resolver, resource_rt, action_*, compiled_       │
            │      │   scenario, pool_resolver, structural_solver, coverage_model …)   │
            │      │        scenario_runner → solver.api.randomize + coverage_model    │
            │      ▼        activity_runner → solver.api.randomize                      │
            │   solver/  ── randomize() operates on FRONTEND objects:                  │
            │                solver → ..types.Component, ..data_model_factory,          │
            │                          ..action_bind                                   │
            └───────────────────────────────────────────────────────────────────────────┘
                       ▲
   zuspec-be-py ───────┘  builder.py:  import zuspec.dataclasses as zdc
       builds runtime classes with the zdc OBJECT MODEL:
         zdc.dataclass, zdc.Component, zdc.Action, zdc.field, zdc.pool, zdc.inst,
         zdc.u1..u64 / i8..i64, types.ClaimPool
       and runs them with rt.executor.ObjectExecutor / rt.scenario_runner.ScenarioRunner
```

Three cycles of coupling exist today:

1. **frontend → runtime**: `types.py` (the object model) lazily imports `rt.activity_runner`, `rt.action_context`, `rt.action_infra`, `rt.list_buffer_pool`, `rt.list_claim_pool` to give `@zdc` instances `.randomize()`/run convenience.
2. **runtime → solver → frontend**: `rt.scenario_runner` / `rt.activity_runner` call `solver.api.randomize`; the solver imports `..types.Component`, `..data_model_factory`, `..action_bind`. The solver randomizes **dataclasses Python objects**, not ir-core IR.
3. **be-py → (object model + runtime)**: `be-py/builder.py` is documented as "the PSS-agnostic core … consumes only the canonical IR `Context`," yet it materializes runtime classes by calling the **dataclasses frontend decorator** (`zdc.dataclass(cls)`, `zdc.Component`, `zdc.Action[...]`, `zdc.field(...)`) and runs `rt.executor` / `rt.scenario_runner` on them.

### `be-py`'s exact import surface on dataclasses (the edge to cut)

```
from zuspec.dataclasses.rt.executor        import ObjectExecutor, AsyncObjectExecutor, _ReturnSignal
from zuspec.dataclasses.rt.scenario_runner import ScenarioRunner
from zuspec.dataclasses.rt.import_resolver import ImportResolver, ImportSpec, PssImportError
from zuspec.dataclasses.rt.ir_compiler     import IRCompiler        # (lazy)
from zuspec.dataclasses.rt.resource_rt     import make_resource     # (lazy)
from zuspec.dataclasses.types              import ClaimPool         # (lazy)
import zuspec.dataclasses as zdc           # object model: dataclass, Component, Action,
                                           #   field, pool, inst, u*/i* scalar aliases
```

`be-sv` has a smaller, parallel edge (`rand_class_emitter`, `generator`, `passes/sv_emit` → `transform.pass_manager`, plus the `ir` shim) — addressed in Phase 5.

---

## Central decision (the one thing to settle first)

**Where does the object / runtime type-model (role #2) live?** This determines whether the dependency can actually be *cut* vs merely *relocated-but-still-imported*.

`be-py` builds its runtime objects with `zdc.dataclass` / `Component` / `Action` / `field` / scalar types. The solver randomizes those same objects. So the object model is the **shared substrate** beneath both the frontend (authoring) and the backend (runtime). Moving `rt/` + `solver/` into `be-py` without addressing the object model just turns `be-py → dataclasses.rt` into `be-py → dataclasses.types` — the edge is not cut.

Three options:

### Option A — Move the object/runtime model down into `be-py` (recommended)
`be-py` owns the runtime object framework: the `Component`/`Action`/`field`/`pool` runtime base classes, claim pools, resource runtime, and the scalar runtime types. The `@zdc.dataclass` *authoring decorator* stays in dataclasses but is re-expressed as a thin layer that (a) emits ir-core IR and (b) for *running*, delegates to be-py's runtime model. The good news from the code: `decorators.py` already imports its runtime hooks **lazily** (`rt.list_claim_pool`, `ir.core.data_type`) and is largely self-contained, so the authoring decorator and the runtime bases are separable.

- **Pros:** Actually cuts the edge. `be-py` becomes self-contained; `dataclasses → be-py` (only when running). Matches "dataclasses = frontend."
- **Cons:** Largest move; must split `types.py`/`decorators.py` into "authoring" vs "runtime object model"; risk of churn in both packages.

### Option B — Move only `rt/` + `solver/` + `coverage/`, keep the object model in dataclasses
`be-py` still does `import zuspec.dataclasses` for `Component`/`field`/scalars.
- **Pros:** Smaller, mechanical.
- **Cons:** **Does not meet the goal** — `pssc → be-py → dataclasses` persists. Rejected unless the goal is relaxed to "no *direct* pssc dependency."

### Option C — Introduce a neutral shared layer `zuspec-model` (or fold the model into `zuspec-ir-core`)
The object/runtime type-model becomes its own package that both `dataclasses` (frontend) and `be-py` (runtime) depend on.
- **Pros:** Cleanest layering; neither frontend nor backend owns the other.
- **Cons:** New package + packaging churn; scalar types/`Component` semantics must be agreed as canonical. Heaviest up-front.

**Recommendation: Option A**, with the scalar type *descriptors* anchored in `zuspec-ir-core` (they are already partly there — `decorators.py` reaches into `zuspec.ir.core.data_type`). Re-evaluate Option C if the runtime object model proves to want an independent release cadence.

> This is the primary item to confirm before Phase 2+ begins. Phases 0–1 below are valuable and safe regardless of which option is chosen.

---

## Target architecture

```
zuspec-ir-core      IR nodes + canonical scalar/type descriptors                (no change of role)
        ▲
        │
zuspec-be-py        Python backend = runtime object model (Component/Action/field/pool runtime
        │           bases, claim pools, resource_rt) + execution (executor, ir_compiler,
        │           scenario_runner, activity_runner, import_resolver, action_*, compiled_
        │           scenario, structural_solver) + constraint solver + coverage runtime.
        │           builder.py consumes ir-core Context ONLY; imports NO dataclasses.
        ▲   ▲
        │   └────────────── pssc  (targets/python_tgt.py, runtime.py)   →  be-py only
        │
zuspec-dataclasses  PURE frontend: @zdc.dataclass / @zdc.field / scalar vocabulary → emits ir-core IR.
                    To *run* a @zdc model it calls be-py (dataclasses → be-py, an inverted edge).
                    Its own RTL behavioral-sim runtime (edge/gather/simulate/sim_domain/timebase/
                    vcd_tracer/pipeline_rt) is a SEPARATE concern — see "Out of scope / open" below.
```

Resulting production edges: `pssc → be-py → ir-core`. `pssc ⊥ dataclasses`. Tests may still import dataclasses freely.

---

## `rt/` module classification (move vs stay vs shared)

`rt/` mixes the **PSS-execution runtime** (move to be-py) with the dataclasses frontend's **own RTL behavioral-simulation runtime** (stays, or out of scope). This split must be validated module-by-module before moving; initial classification:

**Move → be-py (PSS execution):**
`executor`, `ir_compiler`, `scenario_runner`, `activity_runner`, `action_context`, `action_infra`, `action_registry`, `compiled_scenario`, `pool_resolver`, `import_resolver`, `icl_table`, `structural_solver`, `resource_rt`, `flow_obj_rt`, `flow_constraint_store`, `forward_constraint_propagator`, `binding_solver`, `regfile_rt`, `indexed_regfile_rt`, `indexed_pool_rt`, `memory_rt`, `address_space_rt`, `addr_handle_rt`, `channel_rt`, `queue_rt`, `lock_rt`, `select_rt`, `spawn_rt`, `state_graph_factory`, `obj_factory`, `contract_checker`, `completion_rt`, `coverage_model`. Plus `solver/` (whole) and `coverage/` (whole).

**Stay → dataclasses frontend (RTL behavioral sim):**
`edge`, `gather`, `simulate`, `sim_domain`, `timebase`, `event_rt`, `vcd_tracer`, `tracer`, `pipeline_rt`, `pipeline_locks_rt`, `comp_impl_rt`, `cdc`-related.

**Shared / decide (used by both PSS-exec and RTL-sim):**
`executor`, `eval_state`, `expr_eval`, `debug_rt`. If RTL-sim genuinely needs these, either (a) move to be-py and let dataclasses-sim import them from be-py (`dataclasses → be-py`, acceptable for the goal), or (b) hoist to the shared model layer. **Action:** verify each shared module's real consumers before moving.

> ⚠️ The classification above is derived from import edges, not yet from a full read of each module. Validate before relying on it.

---

## Phased migration plan

Each phase must end **test-green** (`pytest.ini` suite + the package suites) and be independently reviewable/revertible.

### Phase 0 — Cheap, safe wins (no architectural change) ✅ do first
- Replace the 21 production `from zuspec.dataclasses import ir` in `src/pssc/targets/sv/*` with `import zuspec.ir.core as ir` (the shim already resolves there). Zero behavior change.
- Extend `tests/unit/test_package_layout.py` to forbid `from zuspec.dataclasses import ir` across **all** of `src/pssc`, not just `ast2ir.py`.
- Fix the two `randomize` docstring imports in `src/pssc/__init__.py` once the runtime's public home is chosen (Phase 4).
- *Outcome:* removes ~all *direct* pssc→dataclasses references; the only remaining edge is transitive via be-py.

### Phase 1 — Decide & lock the object-model question (§Central decision)
- Confirm Option A/B/C. Spike: can `@zdc.dataclass` (authoring) and the `Component`/`Action`/`field` runtime bases be separated? (`decorators.py`'s lazy imports suggest yes.)
- Define the public runtime API surface be-py will export (the things dataclasses and pssc consume): `IrToRuntimeBuilder`, `ClassRegistry`, `ScenarioRunner`, `run_action`/`run_action_sync`, `randomize`, `PssCoverageModel`, `ImportSpec`/`ImportResolver`, resource helpers.

### Phase 2 — Move the solver into be-py
- Relocate `solver/` → `zuspec-be-py` (e.g. `zuspec/be/py/solver/`). Re-point its `..types`/`..data_model_factory`/`..action_bind` imports at the relocated object model (per the Option chosen).
- Keep a temporary `zuspec.dataclasses.solver` re-export shim so dataclasses' own `randomize` and tests keep working during transition.
- *Risk:* solver is the largest body (~17k LoC) and is coupled to the object model — gate Phase 2 on Phase 1's object-model decision.

### ✅ Phase 3 COMPLETE (2026-06-29)

Moved `rt/` (52 files), `solver/` (59 files), `coverage/` (4 files) → `zuspec/be/py/`. Net ~31k LoC relocated; ~350 import references re-pointed across dataclasses, be-py, and tests.

- **Object-model refs** in moved code (`..types`/`..domain`/`..decorators`) → absolute `zuspec.be.py.model.*`. **Frontend refs** (`..config`, `..activity_parser`, …) → absolute `zuspec.dataclasses.*`. **Sibling cross-refs** among rt/solver/coverage kept relative (preserved by the move).
- **Cycle breaks:** `rt/__init__.py` made lazy (PEP 562) so importing a PSS-exec submodule (`scenario_runner`) does NOT eagerly fan out the RTL-sim modules (`obj_factory`/`tracer`/`pipeline_rt`) that carry frontend coupling. `compiled_scenario`'s eager `activity_parser` import lazified (its 2 use-sites got function-local imports). Fixed a latent bug: `rt/activity_runner.py`'s `from ..ir.core…` (which referenced the nonexistent `zuspec.dataclasses.ir`) → `zuspec.ir.core`.
- **Re-points:** dataclasses frontend (`__init__`, `config`, `counter`, `pipeline_ns`, `completion`, `queue_type`, `spawn`, `select`) → `zuspec.be.py.{rt,solver,coverage}`; be-py core (`builder`/`export_api`/`__init__`) and `model/*` lazy back-edges → `zuspec.be.py.rt`; pssc + be-py + dataclasses **tests** deep-submodule imports re-pointed (incl. 2 event tests' `sys.path` hack).
- **Verification:** all 4 import entry points clean (cycle-free); be-py **18** ✅; **pssc unit 982 passed** / 4 skipped (external); dataclasses **stable failures 87 == Phase-2 baseline** (same files: solver constraint tests / rt_runner / state_inference — all pre-existing), flaky `test_zdc_builtins.py` deselected. **Zero real regressions.**

**Remaining be-py → dataclasses back-edges (Phase 4 cuts these):** `builder.py` `import zuspec.dataclasses as zdc`; 3 stale `zuspec.dataclasses.types` (→ `be.py.model.types`); lazy frontend-parser edges (`config`, `pipeline_ns`, `activity_parser`, `data_model_factory`, `constraint_parser`, `action_bind`, `tlm`, `pipeline_locks`, `profiles`). The parser edges are the genuine runtime↔frontend coupling: Phase 4 must determine which fire on pssc's IR-driven path (refactor to consume IR) vs only the `@zdc`-authored path (leave dormant / cut).

### Phase 3 (original plan) — Move the PSS-execution `rt/` modules + `coverage/` into be-py
- Move the "Move → be-py" set above into `zuspec/be/py/rt/` (or flatten into be-py).
- Re-point cross-imports; leave `zuspec.dataclasses.rt.*` re-export shims for the modules dataclasses' own frontend still references lazily (`activity_runner`, `action_context`, `action_infra`, claim pools) so `types.py` keeps working.
- `coverage/` → `zuspec/be/py/coverage/` with a dataclasses re-export shim for `PssCoverageModel`.

### 🔄 Phase 4 IN PROGRESS (2026-06-29)

**Part (a) DONE — be-py is now import-clean w.r.t. dataclasses.**
- `builder.py`'s `import zuspec.dataclasses as zdc` → `from zuspec.be.py import model as zdc`; `model/__init__` now re-exports the object-model surface (`from .types/​.decorators/​.domain import *`) so the backend uses its own model. Stale `from zuspec.dataclasses.types import …` in `builder.py` + `solver/frontend/constraint_system_builder.py` → `zuspec.be.py.model.types`.
- **Verified:** importing **any** be-py module (`model`, `rt.executor`, `rt.scenario_runner`, `builder`, `export_api`) pulls in **0** `zuspec.dataclasses` modules. be-py no longer eagerly depends on dataclasses.

**Diagnostic — the remaining pssc→dataclasses coupling has TWO independent roots** (a `sys.modules` probe shows `import pssc` still pulls 45 `zuspec.dataclasses.*` modules; `import zuspec.be.py.*` pulls 0):

1. **`zuspec-synth` (the dominant, import-time root).** `pssc/ir.py:36` runs `DEFAULT_LAYER = _default_layer()` at module load → `from zuspec.synth.ir.layers import IRLayer` → … → `zuspec/synth/pcf_gen.py:25 import zuspec.dataclasses as zdc` → triggers dataclasses' heavy `__init__` (which eagerly loads the whole frontend = the 45 modules). **This is orthogonal to the runtime relocation** — it's `pssc → zuspec.synth → dataclasses`. Options: make synth not import dataclasses eagerly, make `pssc/ir.py` not eagerly build a synth-backed default layer, or treat synth as a test-only/optional dep.

2. **be-py runtime's lazy frontend-parser back-edges (Part b — the genuine runtime decoupling).** `solver/_core_solve.py`, `solver/frontend/constraint_system_builder.py`, `rt/comp_impl_rt.py`, `rt/pool_resolver.py`, `rt/activity_runner.py`, `rt/contract_checker.py` lazily import `data_model_factory` / `action_bind` / `constraint_parser`. These fire when the runtime re-derives the constraint/data model from live `@zdc` objects at solve time. Cutting them means the solver/runtime consuming the **IR** (which be-py's builder already holds) instead of re-parsing Python — a real refactor of the solver's entry points, not a move. (Today these are masked by root #1 already having loaded everything; isolating them needs root #1 cut first.)

> Net: the **relocation** goal (runtime lives in be-py; be-py import-clean) is achieved. The **"pssc ⊥ dataclasses at runtime"** goal needs (1) the synth edge addressed and (2) the solver/runtime decoupled from the frontend parsers. Both are scoped above.

**Part (b) + roots — DONE for all common paths (2026-06-29). `import pssc` and the full runtime path now pull 0 `zuspec.dataclasses` modules.** Cuts made, each verified by a `sys.modules` probe + suites green:

- **Root #1 (synth):** `pssc/ir.py` `DEFAULT_LAYER` is now computed lazily (cached, via PEP 562 `__getattr__`) instead of at module load — `import pssc` no longer eagerly pulls `zuspec.synth` (→ dataclasses). synth loads only on explicit `dump_ir`/`DEFAULT_LAYER` (debug) use.
- **be-sv ir-shim:** `zuspec/be/sv/generator.py:19` `from zuspec.dataclasses import ir` → `import zuspec.ir.core as ir` (semantically identical; verified pre-existing be-sv failures unchanged by revert test). This was firing via `load_pss → _auto_detect_roots → targets.sv → be.sv.generator`.
- **Object-model leaves → `be.py.model`:** `config.py` (44-line `Config`/`ObjFactory` — needed by `Component.__new__`) and `tlm.py` (32-line port/channel Protocols — identity-checked by `obj_factory`) were tiny pure-stdlib leaves the runtime object model needs; moved into `model/` (sibling imports) with dataclasses re-export shims, mirroring `domain.py`.
- **compiled_scenario fast-path:** `ScenarioCompiler.compile()` re-parsed the action's Python `activity()` via the dataclasses `ActivityParser`. Added a guard: IR-built actions (whose `__activity__` is a `zuspec.ir.core.activity.*` object — detected by module-name string, no import) **skip the fast-path** and use the generic IR activity runner. Cuts the last execution-path back-edge; pssc actions were already handled by the generic runner, so no behavior change (rt_runner tests went green).

**Verification:** `import pssc` → **0** dataclasses; full `load_pss → create → activity-exec` → **0**; diverse features (set/`in`, `foreach`, flow buffer objects) → **0**. **pssc unit 982 ✅**, be-py 18 ✅, dataclasses stable 78 fail (⊂ pre-existing set; *fewer* than the 87 baseline — no new failures), be-sv non-sim failures confirmed pre-existing. Locked in by `test_package_layout.py::test_import_pssc_does_not_pull_in_dataclasses` (subprocess guard).

**Remaining (dormant) back-edges** — present in code but *not triggered* on the paths probed; to be audited for edge-case features before declaring full independence: `data_model_factory` (`rt/comp_impl_rt`, `solver/_core_solve`), `action_bind` (`rt/pool_resolver`, `solver/frontend/constraint_system_builder`), `constraint_parser` (`rt/activity_runner`, `rt/contract_checker`). These fire (if at all) only on specific features (explicit `bind`, contract checking) and are the final slice of Phase 4(b) + the Phase 5 packaging move (dataclasses → test/dev extra).

### Phase 4 (original plan) — Cut be-py's import of dataclasses
- `builder.py`: replace `import zuspec.dataclasses as zdc` with the relocated runtime object model. Per Option A, `Component`/`Action`/`field`/`pool`/scalars now come from be-py itself.
- Verify `grep -r "zuspec.dataclasses" packages/zuspec-be-py/src` returns **nothing**.
- Update `src/pssc/runtime.py` / `__init__.py` docstrings to import `randomize` etc. from the new home.

### ✅ Phase 5 DONE (2026-06-29) — backends cleaned, edge inverted, packaging updated

- **be-sv:** eager ir-shim `generator.py` `from zuspec.dataclasses import ir` → `import zuspec.ir.core as ir`. Remaining `zdc`/`DataModelFactory`/`transform` couplings are lazy @zdc→SV-path only.
- **be-sw (C backend):** swept **34** `from zuspec.dataclasses import ir` shims → `import zuspec.ir.core as ir`; re-pointed object-model refs (`zuspec.dataclasses.types.*` → `zuspec.be.py.model.types`); removed the unused eager `import zuspec.dataclasses as zdc`; lazified `DataModelFactory` (3 sites incl. `__init__`, `co_obj_factory`). `import zuspec.be.sw` now pulls **0** dataclasses. (be-sw tests re-pointed to the moved subpackages; remaining be-sw failures are pre-existing — C-exec env, `AbstractionFieldIR.datatype` schema bug, local-model `org`/`pipeline` paths.)
- **Edge inverted:** `zuspec.dataclasses` now depends on `zuspec.be.py` (its `types`/`decorators`/`domain`/`config`/`tlm`/`rt`/`solver`/`coverage` are thin re-export shims pointing at the backend). `be.py`/`pssc` no longer depend on dataclasses for production.
- **Packaging:** `pssc/pyproject.toml` moved `zuspec-dataclasses` → `[project.optional-dependencies] test`. `zuspec-be-py/pyproject.toml` moved it → a `zdc` extra (the @zdc-authored path) and `dev`.

**End-to-end verification — the whole pssc production surface is dataclasses-free:** `import pssc` = 0; full runtime (`load_pss`→`create`→activity-exec) = 0; **all 16 codegen targets** (python, sv/sv-dpi/sv-native/sv-pure/…, c-host/c-embedded/…) compile with **0** dataclasses modules pulled. Locked by `test_package_layout.py::{test_import_pssc_does_not_pull_in_dataclasses, test_backends_import_clean_of_dataclasses}`. Suites green-equivalent: pssc unit **983**, be-py **18**, dataclasses stable **78** (⊂ pre-existing), be-sv/be-sw remaining failures confirmed pre-existing.

**What deliberately remains** (legitimate, not blocking the goal): be-py/be-sv/be-sw retain *lazy* back-edges to the dataclasses frontend parsers (`DataModelFactory`, `ConstraintParser`, `action_bind`, `ActivityParser`) that fire **only** on the `@zdc`-authored Python-source path — which pssc never uses. dataclasses remains a normal test dependency, as intended.

### Phase 5 follow-up (2026-06-30) — one stale moved-subpackage reference fixed

A re-audit found exactly **one** *broken* (not merely deliberate) back-edge: `solver/_core_solve.py::_ensure_output_payloads` imported `_OutputBufferProxy` from `zuspec.dataclasses.solver.frontend.constraint_system_builder`. That subpackage was relocated into be-py in Phase 3, so `zuspec.dataclasses.solver` **no longer exists** — the import always raised `ModuleNotFoundError`, silently caught by the function's `except ImportError: return`, turning it into a dead no-op (output-buffer fields holding the `Output()` marker were never initialized before a cached dotted-path write). Fixed to the relative sibling import `from .frontend.constraint_system_builder import _OutputBufferProxy` (matching `_core_solve.py:15`). Verified: import resolves, `_ensure_output_payloads` is live again, pssc unit **992** ✅, be-py **18** ✅, 25 layout guards ✅. A scan confirms this was the *only* stale reference to a moved (`.rt`/`.solver`/`.coverage`) subpackage anywhere in be-py production code; all other be-py→dataclasses edges point at frontend parsers / RTL-sim namespaces that still legitimately live in dataclasses.

### Phase 5 (original plan) — Invert the dataclasses edge & clean up
- Make `zuspec-dataclasses`'s run/randomize/coverage paths depend on `zuspec-be-py` (drop the temporary re-export shims, or keep them as thin `dataclasses → be-py` forwards for backward compatibility).
- Address `be-sv`'s dataclasses touch-points (`rand_class_emitter`, `generator`, `transform.pass_manager`) — likely move `transform/` with the runtime or re-point.
- Update `pyproject.toml`: `pssc` drops `zuspec-dataclasses` from `dependencies`, adds it to a test/dev extra.

### Phase 6 — Enforce
- CI guard: `pssc` and `be-py` production imports of `zuspec.dataclasses` fail the build (extend `test_package_layout.py` + a be-py equivalent).
- `dataclasses` may import `be-py`; `be-py`/`pssc` may not import `dataclasses` (outside tests).

---

## Phase 1 spike outcome (2026-06-29) — Option A confirmed feasible

A read-through of `decorators.py` / `types.py` / `rt/` confirms the object model is **cleanly separable**. Findings:

- **Field declarators** (`field`, `pool`, `inst`, `const`, `array`, `rand`, `randc`, …) are pure `dataclasses.field()` metadata factories — zero `.rt`/`.solver`/`ir.core` dependencies.
- **Scalar types** (`u1..u64`, `i8..i64`, `bit`, `bv[N]`, `bitv`) are pure `Annotated[int, …]` aliases / `int` subclasses — zero runtime dependencies.
- **`Component` / `Action` / `TypeBase`** carry no eager `.rt`/`.solver` imports. All runtime behavior is delegated through pluggable protocols (`CompImpl`, `ObjFactory` via `Config.inst().factory`) and **lazy** function-local imports (`Action.__call__` → `rt.activity_runner`/`action_context`/`action_infra`; `ClaimPool.fromList` → `rt.list_claim_pool`).
- **`@zdc.dataclass`** has two responsibilities: (a) runtime object construction — resolve annotations, inject `pool` `__post_init__`, apply stdlib `dataclass`, packed-struct layout — and (b) authoring: parse a Python `activity()` method body to IR via `ActivityParser`. **be-py needs only (a)** — it builds activity behavior from IR it already holds, so it never invokes the source-parsing path.
- **be-py's object-model surface** (must be provided by be-py after the move): scalar map `u1..u64`/`i8..i64`; `issubclass(v, Action|Component)` checks; `Component` as base; `dataclass(cls)`, `Action[comp]`, `field(...)`, `pool(...)`, `inst()` constructors; `types.ClaimPool`.
- **Coupling points to cut** are exactly the modules already slated to move: `rt/activity_runner.py` & `rt/executor.py` eagerly import `zuspec.ir.core.activity/stmt/expr`; `solver/api.py` imports `ir.core.data_type`. All three are PSS-execution and belong in be-py regardless. **No structural cycle** remains once layered.

### Refined phase ordering (object model is foundational)

The object/runtime model must land in be-py **before** solver/rt, because solver imports `..types.Component` and rt imports the object model. Revised sequence:

- **Phase 2 (was: solver) → now: establish runtime object model in be-py.** Move `Component`/`Action`/`TypeBase`, field declarators, scalar types, pools/`ClaimPool`, and the runtime half of `@dataclass` into `zuspec.be.py` (e.g. `zuspec/be/py/model/`). `zuspec.dataclasses.types`/`decorators` re-export them from be-py (inverts the object-model edge). dataclasses keeps the authoring half of `@dataclass` (activity parsing) layered on top.
- **Phase 3: move solver + PSS-exec `rt/` + coverage** into be-py (now able to import the object model locally).
- **Phase 4: cut be-py → dataclasses** entirely; verify clean.
- **Phases 5–6:** invert remaining edges, be-sv cleanup, packaging, CI enforcement (unchanged).

> The lazy back-edges from the object model into `.rt` (`Action.__call__`, `ClaimPool.fromList`) are tolerated via temporary shims between Phase 2 and Phase 3, then resolved when `rt/` lands in be-py.

### Phase 2 mechanics — two structural findings (2026-06-29)

Deeper planning of the move surfaced two things the high-level plan glossed:

1. **`decorators.py` is a runtime/authoring *mix*, not a clean unit.** Besides the runtime declarators (`field`/`pool`/`inst`/scalars/runtime-`dataclass`), it also holds **authoring** decorators: the activity-parse half of `@dataclass` (`decorators.py:185-187`) and the **`extend`** decorator (`decorators.py:~460-491`), both of which call `ActivityParser` (Python `activity()` source → IR). The chosen split (Option 1) puts the activity-parse into the dataclasses *wrapper*; `extend` stays in dataclasses too. **Pragmatic sequencing:** move `decorators.py` wholesale to `be/py/model/` first with a *temporary* absolute back-edge `zuspec.dataclasses.activity_parser` (same tolerated category as the `.rt` back-edges), get green, then lift the activity-parse + `extend` back into dataclasses as a follow-up. Avoids partitioning a 1271-line file under load.

2. **The shim creates a `dataclasses ⇄ be.py` import cycle that must be pre-broken.** Once `dataclasses/types.py` is a shim doing `from zuspec.be.py.model.types import *`, importing it runs `zuspec.be.py.__init__`, which **eagerly** imports `builder` → `import zuspec.dataclasses as zdc` with **module-level** `zdc.u1…` use (`builder.py:28-38`). If dataclasses is still mid-initialization, those names don't exist yet → ImportError. **Fix:** make `zuspec.be.py.__init__` lazy via PEP 562 `__getattr__` (defer `builder`/`export_api`/`import_resolver` imports until first attribute access). Then importing `be.py.model.types` runs a lightweight `be.py.__init__` (stdlib-only), dataclasses finishes loading, and `be.py.IrToRuntimeBuilder` resolves later against the fully-loaded dataclasses. This is the standard cycle-break and is a prerequisite step of Phase 2.

Concrete Phase 2 step order: (A) make `be.py.__init__` lazy + verify its 18 tests; (B) create `be/py/model/`, move `types.py`+`decorators.py`, rewrite relative lazy imports (`.rt`/`.config`/`.domain`/`.activity_parser`/`.profiles` → absolute `zuspec.dataclasses.*`; keep `.types`/`.decorators` sibling-relative); (C) `model/__init__.py` public surface; (D) replace `dataclasses/types.py`+`decorators.py` with re-export shims (+ activity-parse wrapper on `dataclass`); (E) run dataclasses (169 files) + be-py (18) + pssc suites to green.

### ✅ Phase 2 COMPLETE (2026-06-29)

Landed and verified. What moved into `zuspec/be/py/model/`:
- **`types.py`** (Component/Action/TypeBase, scalar types, pools/ClaimPool) — relative lazy imports of `.config`/`.rt.*` rewritten to absolute `zuspec.dataclasses.*` (temporary back-edges, all function-local).
- **`decorators.py`** (runtime `@dataclass` + all field declarators) — moved wholesale; the activity-parse + `extend` authoring bits remain with a temporary lazy `zuspec.dataclasses.activity_parser` back-edge (lift back to dataclasses is a Phase-3/cleanup follow-up).
- **`domain.py`** (`ClockDomain`/`ResetDomain` — a pure-stdlib leaf the base `Component` needs eagerly). **Had to move** because an *eager* class-body `from zuspec.dataclasses.domain import …` would trigger `dataclasses.__init__` mid-`model` load → partial-init cycle. Rule learned: **`model/` must have zero eager `zuspec.dataclasses.*` imports** (every back-edge must be function-local).

`zuspec.dataclasses.types` / `.decorators` / `.domain` are now thin **full-namespace re-export shims** (copy all non-dunder names, incl. privates like `_LegacyForwardingDecl`, preserving object identity). `be.py.__init__` is lazy (PEP 562) to break the cycle.

**Verification:** object identity preserved (`zdc.Component is be.py.model.types.Component`); all three import entry points clean; be-py 18 ✅, pssc 547 ✅; dataclasses **stable** failure set byte-identical to baseline (87==87) with the flaky `test_zdc_builtins.py` deselected → **zero real regressions**. See [[dataclasses-test-baseline]] memory.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Solver↔frontend coupling deeper than mapped (operates on live `@zdc` objects) | Phase-1 spike before moving; keep re-export shims so both packages stay green throughout |
| `rt/` "shared" modules (`executor`, `eval_state`, `expr_eval`) needed by dataclasses' own RTL sim | Validate consumers; move + let dataclasses-sim import from be-py, or hoist to model layer |
| Circular import after move (frontend→runtime→solver→frontend) | Breaking the cycle is the *point* of Option A; enforce a strict layer order (model → runtime → solver; frontend on top) |
| Large blast radius (~32k LoC relocating) | Phase incrementally; re-export shims at every step; never a big-bang move |
| Hidden consumers outside this repo importing `zuspec.dataclasses.rt.*` / `.solver.*` | Keep deprecating re-export shims for ≥1 release; the test audit (companion doc §4) lists the real surface |

---

## Test strategy

- Keep the full suite green at **every** phase (re-export shims make this achievable).
- The integration tests already exercise the runtime surface that must survive the move — use them as the contract:
  - `randomize`: `tests/unit/integration/*_rt.py` (10 files)
  - runner/coverage/solver internals: `test_pss_activity_rt`, `test_covergroup_*`, `test_pad_assignment`, `test_01_hello_world`, `test_pattern_integration`, `test_fill_activity`, `test_forall_struct_rt` (8 files)
- After Phase 4, add the negative guard test: no `zuspec.dataclasses` import reachable from `pssc`/`be-py` production code.

---

## Out of scope / open questions

1. **The dataclasses RTL behavioral-sim runtime** (`edge`/`gather`/`simulate`/`sim_domain`/`vcd_tracer`/`pipeline_rt`). It is *not* PSS execution and not what pssc needs. Does it stay in dataclasses (frontend keeps a self-run sim capability) or also relocate? Recommend: **stays** in dataclasses for now — out of scope for the pssc goal — but flagged because it shares low-level modules with the PSS runtime.
2. **New package vs fold-in.** Do we want a standalone `zuspec-runtime`/`zuspec-model` (Option C) for independent versioning, or is folding the runtime into `zuspec-be-py` (Option A) acceptable long-term?
3. **`randomize`'s public home and signature.** Today `zuspec.dataclasses.solver.api.randomize`. After the move, is the canonical entry point `zuspec.be.py.randomize`, and does dataclasses re-export it?
4. **`be-sv` parallel cleanup** — fold into Phase 5 or track separately?

---

## Recommendation summary

1. Do **Phase 0** now (cheap, safe, removes all direct pssc references).
2. Settle the **object-model decision** (Option A recommended) before any code moves.
3. Move **solver → rt(PSS-exec) → coverage** into be-py behind re-export shims, phase by phase, test-green throughout.
4. Cut be-py's `import zuspec.dataclasses`, invert the edge so `dataclasses → be-py`.
5. Enforce with CI guards; `pssc` lists `zuspec-dataclasses` only as a test/dev extra.
