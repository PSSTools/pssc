# pssc C Bridge Runtime — Design

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **Draft for review**
Date: 2026-06-19
Scope: Design the **C bridge runtime** that lets a `pssc`-generated C scenario
(the `zsp_timebase` coroutine runtime emitted via `zuspec.be.sw.CGenerator`) be
driven from a SystemVerilog testbench over DPI, exposing **multiple named entry
points** (one per *exported* action) instead of today's single `pssc_run`. This
is the first prerequisite for
`design/pss-runtime-export-action-integration-design.md` (the SV-side adapter /
`pss_action_run_if` / `_runner` proxy generation), which *consumes* this runtime.

**Explicitly out of scope for this first cut (per direction):** PSS `export`
parsing. There is no `export` qualifier in the front end or IR today, and we do
**not** add one here. Instead the set of actions to expose is **specified
explicitly to pssc** (a caller-supplied list of root-action names — CLI / API).
This mirrors the note already in
`pss-runtime-export-action-integration-design.md` §7.1 ("the tool should accept a
list of root actions to export, not explicitly exported in the source").

Companions:
- `pss-runtime-export-action-integration-design.md` — the SV-side generation that
  sits on top of this runtime (the adapter, the generic-`run` redirect, proxies).
- `pss-c-sv-bridge.md` — the **upstream** `zuspec-be-sw` bridge (B1–B4) this
  design adapts. That bridge is **not present** in this repo's vendored
  `packages/zuspec-be-sw`; this document re-grounds its concepts on the runtime
  pssc actually ships.
MSB: Note that we'll want to leverage zuspec-be-sw as much as possible -- enhancing as necessary.

> **Resolved (MSB directive + zuspec-fe-pss review).** Two corrections to the
> original framing, captured in §0:
> 1. The C-bridge *runtime* (mailbox / spawn / run) should **live in and enhance
>    `zuspec-be-sw`** — the upstream bridge's home — rather than be re-implemented
>    as pssc-local `share/c` code. pssc *consumes* a be-sw-provided bridge.
> 2. The SV `export_api_if` / `import_api_if` / `factory_if` contract this bridge
>    targets **already exists** as a *native-SV* projection in `zuspec-fe-pss`
>    (`sv/lower_export_api.py`) that pssc dropped during migration. Porting it is
>    the real first prerequisite, and the C bridge becomes a *second backend*
>    behind that same generated contract.

---

## 0. Reframing — one SV API contract, two backends

Reviewing the source project this repo was migrated from
(`~/projects/zuspec/zuspec-examples/packages/zuspec-fe-pss`) changes the plan.

**Finding.** `zuspec-fe-pss` has a **native-SV** export-API projection that pssc
**did not carry over**:

- `sv/lower_export_api.py` (+ the `oo_api` wiring in `__init__._generate_sv_from_ctx`)
  emits the `import_api_if` / `import_api_base` / `export_api_if` / `factory_if` /
  `export_api_impl` classes and **augments the root component into the factory**
  (`type_id()` → `create(imp)` → `export_api_if`).
- `export_api_impl.<action>()` runs each export action's lifecycle **in the
  simulator** — `construct → comp wiring → pre_solve → randomize() → post_solve →
  activity()/body()` (`lower_top.emit_root_action_lifecycle`) — with imports
  routed through `comp.import_if.<fn>(...)`. **No C, no DPI, no bridge.**
- Package-scope `import target/solve` protos are captured in
  `ast_to_ir._translate_import_proto` onto `ctx.import_functions`, which drives
  the `import_api_if` surface.

pssc migrated the SV-native **harness** projection (`generate_top_module`, a
standalone `zsp_test_top`) but **dropped the export-API (`oo_api`) projection**.

**Consequence.** The `export_api_if` / `import_api_if` / `factory_if` SV contract
that *both* `pss-runtime-export-action-integration-design.md` and this document
aim to produce **already exists upstream**, and is precisely the contract the C
bridge was built to *reproduce* (`pss-c-sv-bridge.md` §1: "present the **same**
SystemVerilog interface the native PSS→SV flow emits"). We were about to
re-invent it.

**Reframe — one contract, two backends:**

| Backend | `export_api_impl.<action>()` body | Needs | Use |
| --- | --- | --- | --- |
| **A — native SV** (`lower_export_api.py`) | run the action lifecycle in-simulator (`randomize()`/`activity()`) | the SV simulator's own solver; **no C, no bridge** | in-simulator verification |
| **B — C bridge** (this doc) | the trampoline: `spawn`→`run`→drain imports→`fork`→`complete` over DPI into `libpssc_scenario.so` | the be-sw bridge runtime + DPI | compiled-C / firmware-style runtime |

Both emit the **same** `import_api_if` / `export_api_if` / `factory_if`; they
differ only in the `export_api_impl` task bodies (and Backend B adds the C
runtime + ids). So Backend B **reuses Backend A's interface/factory generation**
verbatim and supplies an alternative impl — it does not define a parallel SV
scheme.

**Revised prerequisite order:**

1. **Port the native-SV `oo_api` export-API projection into pssc** (Backend A) —
   see the adaptation list in §13. This alone delivers "exported actions as a
   callable SV API," in-simulator, with no bridge. It is the new **Phase C0** and
   the true unblocker.
2. **Build the C bridge as Backend B** (§4–§11), hosted in `zuspec-be-sw` per the
   MSB directive, reusing the Phase-C0 interface/factory nodes and swapping only
   the `export_api_impl` bodies for the trampoline.

The rest of this document specifies Backend B; read §5/§9's "ships in
`src/pssc/share/c`" as **"provided by `zuspec-be-sw`, consumed by pssc"** (§9.1).

---

## 1. Why this exists — the gap

Two facts about the current repo drive this design:

1. **The upstream `zsp_bridge` runtime is absent.** `pss-c-sv-bridge.md`
   documents a bridge built in `zuspec-be-sw` (B1–B4, 2026-06-02), but the
   vendored `packages/zuspec-be-sw` here exports none of it
   (`generate_c_bridge`, `zsp_bridge_*`, the trampoline, `ACTION_*` ids). We
   cannot "consume it"; we must build a pssc-local equivalent.

2. **Today's `sv-dpi` target is a single-entry facade.** `sw_tgt.py`'s
   `SvDpiTarget` emits one `pssc_run(int seed)` DPI function (instantiate the
   root action, drive its activity, `zsp_timebase_run`) plus a trivial
   `pssc_top.sv` that calls it from an `initial` block. There is no per-action
   entry, no import callback path, no host event loop in SV.

3. **The native-SV export-API projection was dropped in migration.** The
   `oo_api` projection that produces the target SV contract exists upstream but
   not here (§0). So the *first* gap to close is porting Backend A, not building
   the bridge.

The runtime export-action work needs **N callable entry points**, each spawning a
specific action's scenario, plus a **two-direction** bridge (SV drives C; C calls
back into SV for DUT interaction). This document specifies that runtime (Backend
B), on top of the ported Backend-A contract.

---

## 2. What we build on — the existing C runtime

Grounding the design in what pssc emits today (verified against generated output
under `/tmp/dpi/` and `src/pssc/targets/sw_tgt.py`):

- **Scheduler.** `zsp_timebase_t` (`share/include/zsp_timebase.h`) is a
  ready-queue + min-heap event executor. Key primitives we reuse verbatim:
  - `zsp_timebase_init(tb, alloc, resolution)` / `zsp_timebase_run(tb)` — run one
    ready thread; returns 1 while work remains.
  - `zsp_timebase_schedule(tb, thread)` — (re)queue a thread at the current time.
  - `zsp_timebase_has_pending(tb)` — any ready thread or timed event pending.
  - Threads carry `flags` (incl. `ZSP_THREAD_FLAGS_BLOCKED`) and `rval` (the
    resume/return value slot).
- **Coroutine shape.** Each action body compiles to a `zsp_task_func`
  `<Comp>_body_task(tb, thread, idx, args)` — a `switch (idx)` FSM. `case 0`
  allocates the frame and reads `self` via `va_arg`; suspension is "advance
  `idx`, `break`"; resume re-enters at the next case. A `<Comp>_body(self, tb)`
  wrapper does `zsp_timebase_thread_create(tb, &..._body_task, flags, self)`.
- **Instantiation.** `<Comp>_init(ctxt, self, name, parent)` initializes the
  component; `ctxt` carries `{alloc, timebase}`.
- **Untimed model.** PSS actions consume no time; all time/DUT interaction lives
  in imported `target` tasks (standard PSS semantics, same as the upstream
  bridge §2). So the timebase here is a pure ready-queue executor — `BLOCKED`
  coroutines sit out until re-woken by `zsp_timebase_schedule`, exactly as the
  `parallel` join already does.
- **Solver TU split.** `sw_tgt.py` already keeps the dv-solve translation unit
  (`pssc_solve.c`, includes `zsp_problem.h`/`zsp_ctx.h`) **separate** from the
  runtime TUs because `dv-solve` and `be-sw` ship structurally different
  `zsp_alloc.h` with the same typedef name; they must not co-occur in one TU.
  The bridge inherits this constraint (§9).

The bridge is **purely additive plumbing over this runtime** — no new coroutine
semantics for Phase C1.

---

## 3. Architecture

```
          ┌──────────────────────── SV testbench ───────────────────────────┐
          │  <root>_import_if imp = new(...);   // DUT callbacks (later)      │
          │  <root>_if ep = <root>::create(imp, cfg);                         │
          │  ep.mem_to_mem_copy(status, a, b, 64);   // a generated entry     │
          └───────────────────────────────┬──────────────────────────────────┘
                                          │ (drop-in SV interfaces — companion doc)
          ┌──────────── generated SV shim (companion design) ────────────────┐
          │  pss_action_run_if / <root>_if / <root>_import_if / <root> adapter│
          │  run_scenario(): THE TRAMPOLINE (SV event loop)                   │
          │     spawn → run C to fixpoint → drain requests → fork DUT tasks   │
          │                → complete → repeat until done                     │
          │  import "DPI-C": zsp_bridge_create/spawn/run/next_request/...      │
          │  export "DPI-C": zsp_bridge_call_function  (synchronous solve)     │
          └───────────────────────────────┬──────────────────────────────────┘
                                          │ DPI (no time-consuming calls)
          ┌──────────── libpssc_scenario.so (one DPI shared object) ─────────┐
          │  THIS DOC:  zsp_bridge runtime  (mailbox, spawn dispatch, resume) │
          │  generated <action>_body coroutines   (CGenerator, unchanged)     │
          │  zsp_timebase + share/rt runtime    +    dv-solve (separate TU)    │
          └───────────────────────────────────────────────────────────────────┘
```

The bridge moves the host event loop out of C `main` (today's `pssc_run` calls
`zsp_timebase_run` directly) and into the SV trampoline, so SV owns blocking and
time while C stays master of structure, ordering, and constraint solving.

---

## 4. The explicit export set (no PSS `export`)

Today `sw_tgt.py` resolves **one** root action (`_resolve_root`) and synthesizes
one `pssc_run`. We generalize:

- pssc accepts an **ordered list of action names** to expose as entry points
  (CLI `--export-action NAME` repeatable, or an API list). Each name resolves
  through the existing `type_m` lookup (`_resolve_root` logic, applied per name).
- Each resolved action is assigned a stable integer id in list order:
  `ACTION_<name> = 0, 1, 2, …`. This id table is the single source of truth
  shared by the C spawn dispatcher and the SV `localparam`s.
- If the list is empty, fall back to today's behavior (auto-detect the single
  top action → `ACTION_<root> = 0`), so existing flows are unchanged.

No source-level qualifier is read; "exported" simply means "named in the list."
When front-end `export action` support later lands, it becomes an *additional*
source of names unioned with the explicit list — the id-assignment and runtime
below do not change.

---

## 5. Bridge runtime C API (`zsp_bridge.{h,c}`)

Ships as a hand-written runtime under `src/pssc/share/c/` (sibling of the
`pssc_mem*.h` seam headers), copied into the build like the existing share files.
It owns one `zsp_timebase` per scenario instance.

```c
/* zsp_bridge.h — minimal, svdpi-free (locally prototyped chandle as void*) */
typedef struct zsp_bridge_s zsp_bridge_t;

/* lifecycle */
zsp_bridge_t *zsp_bridge_create(void);
void          zsp_bridge_destroy(zsp_bridge_t *b);

/* SV → C: drive the scenario */
void zsp_bridge_spawn(zsp_bridge_t *b, int action_id, long long seed);
void zsp_bridge_run(zsp_bridge_t *b);     /* run C ready-queue to fixpoint   */
int  zsp_bridge_done(zsp_bridge_t *b);    /* 1 when no pending work remains   */

/* C → SV: import request mailbox (Phase C2+) */
int  zsp_bridge_next_request(zsp_bridge_t *b,
                             int *req_id, int *fn_id, void **args);
void zsp_bridge_complete(zsp_bridge_t *b, int req_id, long long ret);

/* arg marshalling */
void      zsp_bridge_set_arg(zsp_bridge_t *b, int idx, long long val);  /* SV→C input */
long long zsp_bridge_arg_i(void *args, int idx);     /* SV reads request arg */
const char *zsp_bridge_arg_s(void *args, int idx);
long long zsp_bridge_status(zsp_bridge_t *b);         /* action precondition/status */
```

Internals (grounded in `zsp_timebase.h`):

```c
struct zsp_bridge_s {
    zsp_alloc_t       alloc;        /* malloc-backed, like pssc_run today      */
    zsp_timebase_t    tb;           /* the ready-queue executor                */
    zsp_init_ctxt_t   ctxt;         /* {alloc, timebase} handed to <Comp>_init */
    /* request mailbox (C2+) */
    zsp_bridge_req_t *pending_head, *pending_tail;   /* posted, awaiting SV    */
    zsp_bridge_req_t *blocked_map;                   /* req_id → blocked thread */
    int               next_req_id;
    long long         status;       /* last spawned action's status/result     */
    /* root storage for the active action (set by the generated spawn switch) */
    void             *root_obj;
};
```

- `zsp_bridge_run` is a thin loop: `while (zsp_timebase_run(&b->tb)) {}` — runs
  the ready queue until empty (all coroutines either done or `BLOCKED` on a
  posted request). It is **non-time-consuming** from SV's perspective: it returns
  as soon as the queue quiesces.
- `zsp_bridge_done` returns `!zsp_timebase_has_pending(&b->tb) && pending_head ==
  NULL && blocked_map == NULL`.

---

## 6. Spawn dispatch (generated, one switch over `ACTION_*`)

The generated TU emits a single dispatcher that maps an `action_id` to the
right root-action instantiation + body spawn, reusing the existing
`_init`/`_body` pattern from `pssc_run`:

```c
/* generated into the scenario TU (replaces the fixed pssc_run body) */
void zsp_bridge_spawn(zsp_bridge_t *b, int action_id, long long seed) {
    pssc_solve_all((unsigned long long)seed);   /* runtime-solve, if enabled */
    switch (action_id) {
    case ACTION_mem_to_mem_copy: {
        static wb_dma__mem_to_mem_copy root;     /* root storage              */
        b->root_obj = &root;
        wb_dma__mem_to_mem_copy_init(&b->ctxt, &root, "root", NULL);
        /* input args were staged via zsp_bridge_set_arg → copied into fields  */
        wb_dma__mem_to_mem_copy_body(&root, &b->tb);
        break;
    }
    case ACTION_channel_reset: { /* … */ break; }
    }
}
```

Argument staging: for an action with input fields, `zsp_bridge_set_arg(b, i,
val)` records values that the spawn switch copies into the freshly-`_init`'d
root's input fields *before* `_body`. (Scalars only in v1; widths/strings per
§8.) The companion SV adapter marshals the proxy's bound args into these slots
right before `run_scenario`.

This is the exact generalization the runtime export-action design calls for:
today's single `pssc_run` is `case ACTION_<root>:` of this switch.

---

## 7. Two-direction blocking bridge (Phases C2–C3)

Identical in spirit to `pss-c-sv-bridge.md` §4, re-expressed on `zsp_timebase`.
**Note:** this half depends on **import lowering in be-sw**, which is *not present
today* (no `ScImport`/blocking-call rendering in the vendored `stmt_generator`).
It is therefore staged after C1 and tracked as a be-sw prerequisite (§10).

### 7.1 SV → C (the trampoline; lives in generated SV — companion doc)

The SV `run_scenario` task is the host event loop: `spawn` → `run` (C to
fixpoint) → drain `next_request` → `fork` each blocking DUT task → `complete`
(re-wake) → repeat until `done`. All blocking is in SV `fork` branches; every DPI
call returns immediately (Verilator-safe).

### 7.2 C → SV blocking `target` (suspend point)

The emitter renders an `import target` call as a suspend — the same shape the FSM
already uses for a sub-call:

```c
/* import target imp.write(addr,data): marshal → post → BLOCK → resume */
locals->__req.fn_id   = FN_write;
locals->__req.argc    = 2;
locals->__req.argv[0] = (long long)addr;
locals->__req.argv[1] = (long long)data;
zsp_bridge_post_request(thread, &locals->__req);  /* enqueue + set BLOCKED   */
ret->idx = K + 1; break;                           /* suspend                 */
/* case K+1: resume (value-returning import → read thread->rval) */
```

`zsp_bridge_post_request` appends to `pending_head` and clears the thread from the
ready queue (leaves it `BLOCKED`); it does **not** schedule. `zsp_bridge_complete(b,
req_id, ret)` stores `ret` in the thread's `rval` and calls
`zsp_timebase_schedule(&b->tb, thread)` to re-wake it; the next `zsp_bridge_run`
resumes at `idx = K+1`.

### 7.3 C → SV non-blocking `solve` (synchronous re-entry)

A `solve`/value import does not suspend; inside `zsp_bridge_run` (a `context` DPI
import on the SV side) C calls back synchronously via
`export "DPI-C" zsp_bridge_call_function(fn_id, args)`, which the SV shim
dispatches to `imp.<fn>(...)` (a SV `function`). The `target`/`solve` split maps
onto SV `task`/`function`, matching the companion adapter's import redirect.

This requires capturing the SV scope before re-entry (`svGetScope`/`svSetScope`),
exactly the Verilator finding in `pss-c-sv-bridge.md` §B3; the bridge exposes a
`context` `zsp_bridge_capture_scope` called from `run`.

---

## 8. DPI ABI

The SV shim (companion doc) declares the import/export DPI surface; this runtime
implements the C side. Mirrors the upstream `zsp_bridge_*` chandle style so the
two stay source-compatible:

```sv
import "DPI-C" function chandle zsp_bridge_create();
import "DPI-C" function void    zsp_bridge_destroy(chandle b);
import "DPI-C" function void    zsp_bridge_spawn(chandle b, int action_id, longint seed);
import "DPI-C" function void    zsp_bridge_set_arg(chandle b, int idx, longint val);
import "DPI-C" context function void zsp_bridge_run(chandle b);
import "DPI-C" function int     zsp_bridge_next_request(chandle b,
                                  output int req_id, output int fn_id, output chandle args);
import "DPI-C" function void    zsp_bridge_complete(chandle b, int req_id, longint ret);
import "DPI-C" function int     zsp_bridge_done(chandle b);
import "DPI-C" function longint zsp_bridge_arg_i(chandle args, int idx);
import "DPI-C" function longint zsp_bridge_status(chandle b);
```

`ACTION_*` / `FN_*` integer ids are emitted as SV `localparam`s in the same TU as
the generated C ids, from one shared table (§4).

**Marshalling (v1):** scalar integral (≤64-bit → `longint`) and `string`; wider
vectors (`bit[N]`, N>64), structs, and arrays are follow-on (packed buffer +
width-aware getters), matching the upstream v1 limits.

---

## 9. Build & translation-unit layout

Produce one DPI shared object `libpssc_scenario.so`. Reuse
`zuspec.be.sw.compiler.CCompiler.compile_shared` (already builds `-fPIC -shared`).

TUs, respecting the `zsp_alloc.h` conflict (§2):

| TU | Contents | Includes |
| --- | --- | --- |
| `<scenario>.c` + `<comp>_*.c` | generated coroutines + spawn dispatch + import marshallers | runtime headers (`zsp_timebase.h`, `zsp_init_ctxt.h`, `zsp_bridge.h`) |
| `zsp_bridge.c` | the bridge runtime (mailbox, run loop, resume) | runtime headers only |
| `pssc_solve.c` | `pssc_solve_all` (dv-solve) | dv-solve headers only — **never** co-included with runtime headers |
| `share/rt/*.c` | `zsp_timebase`, alloc, par-block, … | runtime headers |

The bridge entry points **replace `main`** for the DPI target (today's
`harness_with_main = False` path); the standalone `main` stays for `c-host`.
`pssc_run` is retained as `case ACTION_<root>` of the spawn switch (or as a thin
back-compat wrapper calling `spawn(0, seed)` + `run`) so existing single-entry
callers keep working.

### 9.1 Home: `zuspec-be-sw`, not pssc-local (MSB directive)

Per the directive to "leverage `zuspec-be-sw` as much as possible — enhancing as
necessary," the **runtime and its generators live in `zuspec-be-sw`**, not as
pssc-local `share/c` code:

- `zsp_bridge.{h,c}` ships in `zuspec-be-sw/.../share/` (model-independent), and
  the spawn-dispatch + import-marshaller emission and `build_dpi_library` become
  **be-sw entry points** (the upstream `generate_c_bridge` / `build_dpi_library`
  re-created here). This is the gap noted in §1 — the vendored be-sw exports
  none of it yet; Phase C1 *adds* it to be-sw.
- pssc's role stays thin: select the export set (§4), invoke the be-sw bridge
  generator, and emit the SV side (Backend B's `export_api_impl` over the
  Phase-C0 interfaces). Read every "ships in `src/pssc/share/c`" above as
  "provided by be-sw, consumed by pssc."

This keeps a single C-runtime source of truth shared with the upstream and avoids
forking a second bridge in pssc.

---

## 10. Dependencies, determinism, limits

- **be-sw import lowering (prerequisite for C2+).** Blocking/solve imports need
  the emitter to render import calls as the suspend/synchronous-call shapes in
  §7. The vendored `stmt_generator` does not do this yet; Phase C1 deliberately
  needs none of it (no imports), so C1 is buildable now and de-risks the
  plumbing.
- **Determinism.** Seed flows through `zsp_bridge_spawn(seed)` →
  `pssc_solve_all(seed)`, preserving today's reproducibility contract.
- **Concurrency.** `parallel` is unchanged in C (`zsp_par_block`); when two
  branches post import requests at the same C-logical instant, the SV trampoline
  `fork`s both DUT tasks → genuine concurrent activity, joined C-side. (C2+.)
- **One timebase per instance.** The bridge owns its `zsp_timebase`; multiple
  concurrent scenario instances each get their own `chandle`. Concurrent
  `fork`'d entry calls on a *single* handle are a documented serial contract for
  v1 (the companion design's open question).
- **Flow objects / pools / resources** remain solved internally C-side; they do
  not change the bridge.

---

## 11. Phased plan

- **C0 — port the native-SV `oo_api` export-API projection (Backend A).** Bring
  `lower_export_api.py` and its supporting changes (§13) into pssc: import-proto
  capture, `pss_to_sv_with_ctx`, `emit_root_action_lifecycle`, the `oo_api`
  projection wiring, root auto-detection, and the explicit `export_actions` list.
  Delivers exported-actions-as-SV-API **in-simulator, with no bridge** — the real
  unblocker. Testable with the upstream smoke e2e (`tests/sim/sv/test_smoke_e2e.py`)
  adapted to pssc, gated on `available_sims()`.
- **C1 — bridge runtime + multi-action spawn/run, no imports.** `zsp_bridge.{h,c}`,
  the generated `ACTION_*` table + spawn dispatch, build `libpssc_scenario.so`,
  and a minimal SV trampoline that just `spawn`s + `run`s to `done`. Proves a
  Phase-1–style C scenario (sequential / `parallel`, no imports) drives from SV
  and matches the standalone `pssc_run` output. *No new C semantics — pure
  plumbing; buildable against today's be-sw.* This is the unblocking step.
- **C2 — blocking imports (C→SV tasks).** Request mailbox + post/complete +
  trampoline `fork`. **Blocked on** be-sw import lowering.
- **C3 — non-blocking `solve` imports** via `export "DPI-C"` + scope capture.
- **C4 — marshalling breadth + examples + an SV-vs-C cross-check** (diff observed
  import-call order against a native run). Wire Backend B into the **Phase-C0
  interfaces**: emit an alternative `export_api_impl` whose task bodies are the
  trampoline (reusing `import_api_if` / `export_api_if` / `factory_if` unchanged),
  so a testbench written against Backend A runs against the C-backed scenario
  unchanged. The `pss_action_run_if` / `_runner` proxy layer
  (`pss-runtime-export-action-integration-design.md`) layers on either backend.

Each phase is independently testable and leaves the bridge runnable.

---

## 12. Open questions

1. **Where the spawn switch is emitted.** Generate it into the existing scenario
   TU (alongside coroutines) vs. a dedicated `zsp_bridge_dispatch.c`. Leaning:
   dedicated file, so `zsp_bridge.c` stays model-independent and copyable.
2. **Arg staging vs. constructor args.** Copy `set_arg` values into root fields
   in the spawn switch (chosen) vs. threading them through `<comp>_init`. Field
   copy keeps `_init` untouched; revisit if inputs must influence construction.
3. **Status/precondition surfacing.** `zsp_bridge_status` returns a single
   `long long`; is that enough, or do we need a richer result once precondition
   asserts (companion §4.4 / action-lowering design) exist?
4. **C1 e2e gating.** With no simulator guaranteed in CI, is C1 validated by (a)
   building the `.so` + a tiny C harness that calls `spawn/run/done` directly
   (no SV), plus (b) a Verilator run behind the existing `available_sims()`
   gate? Leaning: both — (a) always, (b) when a sim is present.
5. **Naming.** `zsp_bridge_*` (match upstream, source-portable) vs. `pssc_bridge_*`
   (repo-local). Leaning: keep `zsp_bridge_*` for drop-in compatibility with the
   upstream shim and the companion design's DPI decls.

---

## 13. What to adapt from `zuspec-fe-pss` (Phase C0, Backend A)

The native-SV export-API projection is an **uncommitted** enhancement in
`~/projects/zuspec/zuspec-examples/packages/zuspec-fe-pss`. The pieces to port
into pssc (paths are pssc destinations):

| From `zuspec-fe-pss` | To pssc | Notes |
| --- | --- | --- |
| `sv/lower_export_api.py` (new, 305 ln) | `src/pssc/targets/sv/lower_export_api.py` | The core: `ExportAction`, `build_oo_api_nodes`, `_lower_{import_api_if,import_api_base,export_api_if,factory_if,export_api_impl}`, `_augment_root_factory`. Imports `zuspec.be.sv.ir.sv` — **present in pssc**. Near-verbatim. |
| `lower_top.emit_root_action_lifecycle` | `src/pssc/targets/sv/lower_top.py` | pssc has `generate_top_module`/`_auto` but **not** this shared emitter. Add it and refactor the harness path to call it (single source of truth). |
| `pss_to_sv.pss_to_sv_with_ctx` | `src/pssc/targets/sv/pss_to_sv.py` | pssc's `pss_to_sv` returns only nodes; add the ctx-returning variant (needed for `ctx.mangle_name` during export-action resolution). |
| `ast_to_ir._translate_import_proto` + `ctx.import_functions` | `src/pssc/ast2ir.py` | pssc captures per-function `is_target`/`is_solve` but does **not** aggregate package-scope `import` protos. Add the aggregation onto the context. |
| `_generate_sv_from_ctx` `oo_api` wiring + `_auto_detect_roots` + `_resolve_qualified_name` + `_resolve_export_action` | `src/pssc/__init__.py` (or the SV driver) | Adds `projection='oo_api'` (default), `export_actions`, `package_name`; resolves names and splices `prefix + sv_nodes + suffix`. |
| `emit_files` `package_name` / `emit_dpi` / projection params | `src/pssc/targets/sv/emit_files.py` | Mechanical signature additions. |
| `tests/sim/sv/test_smoke_e2e.py` (new) | `tests/` | The e2e that drives `factory_if::type_id().create(imp); ep.<action>()`. Gate on `available_sims()`. |

Wire it into the **`sv-native` target** (`SvDpiTarget` is unrelated) via an
`--export-action NAME` (repeatable) / `--projection oo_api|harness` option, so the
**same explicit action-list mechanism** (§4) serves both backends.

**Deliberately *not* in Phase C0 scope** (adjacent changes seen in the same diff,
port separately if wanted): `replicate`→`repeat` sentinel transform
(`_transform_replicate` + ast_to_ir `ActivityReplicate` recovery), and the
per-type pending-range-constraint isolation fix in `ast_to_ir` (a correctness fix
for `rand .. in [range]` leaking between types — worth porting on its own merits).
