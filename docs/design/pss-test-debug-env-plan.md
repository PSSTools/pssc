# PSS test / debug environment: implementation, test and doc plan

Status: **draft for review** (2026-11-18). Nothing is implemented. Tick items
here as they land, and keep §11 (status and progress log) current, so a fresh
session can pick the work up from this file alone.

Design of record: [pss-test-debug-env-design.md](pss-test-debug-env-design.md)
(revision 2). Section references (§N) are to that design unless they say
otherwise.

**Scope.** Design Phases 1 to 5 (§17): pssunit, platform doubles, pssvm on bc,
the runner, the daemon and CLI, debugging, coverage in covsight, simulation
integration with hot reload, the native engine, replay and the fidelity
guard.

**Out of scope for now:** formal analysis. That is design §13, decision D8,
requirement R13, Phase 6, `pssvm-sym`, `pssc formal …`, and open questions
O15 to O17. Nothing in this plan depends on it. Two choices here keep it
possible later without rework, and both are needed for other reasons anyway:
coverage items have stable IDs (P2.7), and debug lowering keeps real frames
(P1.4).

---

## 0. How to use this document

Every unit of work has a stable ID: `P1.4` is a work item, `T1.4a` a test, `D3`
a doc. If an item is dropped, it is struck through, never renumbered, so a
commit message or a review comment that names `P1.4` keeps one meaning. The
convention follows the pssunit plan
(`~/projects/psstools/pssunit/pssunit-implementation-plan.md` §0).

Each work item has five fields:

| Field | Meaning |
|---|---|
| **Change** | What the code does afterwards that it did not do before |
| **Files** | Where the change goes |
| **Tests** | The test IDs that prove it (details in §8) |
| **Docs** | The doc IDs that describe it (details in §9) |
| **Size** | S (≤ 2 days), M (≤ 1 week), L (≤ 3 weeks), XL (more; split it before starting) |

**A phase closes only when its tests and its docs close.** Docs written a
phase late end up being written from memory.

---

## 1. Baseline: what exists today (surveyed 2026-11-18)

| Area | State | Where |
|---|---|---|
| `pssc` CLI | argparse subcommands in `build_parser()`. Commands: `compile`, `parse`, `targets`, `*-core-path`. No `test`. | `src/pssc/cli.py:134` |
| **Earlier pssunit runner** | **Uncommitted, in another checkout.** `pssc test` with sources → discover → run on bc → CTRF report, ~1170 lines across `src/pssc/testing/{cmd,discover,runner,report,sources}.py`, plus `std_libs/pssunit_pkg.pss`, 9 unit-test files, `tests/pssunit/`, and 4 docs (`test-command.rst`, `ctrf-report.rst`, `annotations.rst`, `test-internals.rst`). Its plan says M1, M2, M3.2, M3.4, M4, M5.2 and M5.3 are landed. | `~/projects/psstools/pssunit/packages/pssc` (git status `??`), plan in `~/projects/psstools/pssunit/` |
| pssunit framework | Normative `pssunit_pkg` (`test_a`, `test_c`, a `test` annotation with `group/skip/xfail/reason/samples`). Design rev 4: "a fixture is a root component, a test is an action". | `~/projects/psstools/pssunit/src/pss/pssunit_pkg/` |
| bc interpreter | `VM.run_frame` (`interp/vm.py:173`). Its only seams are `sink` (orchestration events), `on_solve`, `out` and `import_provider` (`ImportProvider.call(fn_id, args)`, `extern.py:158`), plus `Memory` (sparse, logged). **No breakpoint, step or per-instruction hook.** | `packages/zuspec-be-bc/src/zuspec/be/bc/` |
| bc lowering | Entry is one action (`lower_module(module, entry_action=…)`, `lower/driver.py:240`). Component functions are inlined, and a recursive call becomes a `CALL`. `Instr.src_ref` → `ProvTable` (file, line, col). | `lower/`, `model.py:184,486` |
| `.zbc` format | Sections 1–15. FILE=8, PROV=9, CMNT=10, LINE=11 are defined, but **`write_image` rejects them** ("absent from the runtime profile"). | `format/spec.py:267`, `format/writer.py:29,89` |
| Trace | `TRACE_SCHEMA_VERSION=1`, JSONL. Orchestration kinds only (SPAWN … YIELD), no procedural events. | `trace/schema.py`, `trace/sink.py`, `docs/spec/trace-schema.md` |
| pssc → bc | No target or library API. The only path is the compliance adapter: parse → link → ast2ir → `PSSToScenarioPass` → `lower_module` → `run_model`. | `tests/compliance/adapters/pssc_bc.py:66` |
| Native engine | `zuspec-rt-eng`: ~1400 lines of C (`zbc_interp.c`, reader, engine, solver glue) on `zuspec-rt-core`, with an optional IMPORT callback. Tests via ctypes: select, import, solve, par, bind, invoke, components, differential. No memory hook, no trace, no debug. | `packages/zuspec-rt-eng/share/` |
| op-model-py seam | `<Root>ImportApi` Protocol, `MemoryBus` (`read8..64`/`write8..64`/`message`), `Chan1`, `check_import_api()` | `src/pssc/targets/py/`, `src/pssc/share/py/pssc_rt.py` |
| Annotations, preludes | `Base.annotations`, `pssc.annotations.of/all_of/find_all`. `pssc.preludes` entry points. Std libs in `src/pssc/std_libs/` (flat). | `src/pssc/annotations.py`, `preludes.py` |
| SV runtime, bridge | `zsp_rt_pkg.sv`, `pssc_reg_pkg.sv`. `zsp_bridge.{h,c}` mailbox (`sv-dpi-bridge` target). **pyhdl-if is not used by pssc.** | `src/pssc/share/{sv,c}/` |
| covsight | `covsight-core`: a UCIS Python API with in-memory, NCDB, Parquet and XML backends, and a C `ucis_writer.h` (no Python binding). `covsight` provides the CLI, testplan and a dv-flow extension. | `packages/covsight-core/python/covsight/core/`, `packages/covsight` |
| dv-flow | `src/pssc/dvflow/flow.yaml`: Check, RegPkg, CoreC, CoreCpp, PySource, SvNative, SvDpi | |
| pytest | No `pytest11` plugin. Markers: slow, integration, perf, sim, dpi, c_toolchain, golden, plugin, … | `pytest.ini` |
| Editor | `vscode-pss-support` has an LSP (`server/src/server.ts`), no `debuggers` contribution and no Test Explorer | `~/projects/psstools/vscode-pss-support` |
| Agent skills | `agent-skills-pss` repo (`skills/`) | `~/projects/psstools/agent-skills-pss` |
| dv-solve | CMake C library plus a ctypes Python package (`SolveCtx`), already used by bc | `packages/dv-solve` |
| WB DMA | The model in `examples/op_model/pss/`. RTL bench in `~/projects/featherweight-ip/fw-wb-dma` (also `featherweight-hdl/fw-wb-dma*`). | |

**P0.1 (below) measures the unit-suite baseline before any change**, as the
activity P0 plan did. No number is assumed here.

---

## 2. Decisions to confirm before Phase 1

The earlier pssunit work and the design disagree in a few places. Each row
gives a recommendation, and **the plan is written assuming the
recommendation**. Changing a row changes the items it names.

| # | Question | Recommendation | Items affected |
|---|---|---|---|
| R-1 | Package name: design `pssunit::` vs existing `pssunit_pkg` | **`pssunit_pkg`.** It is the normative package in the pssunit repo and already has users (`examples/wb_dma`). The design's `pssunit::` reads as shorthand. Update the design's examples when P1.1 lands. | P1.1, all docs |
| R-2 | Suite model: design `suite_c` vs pssunit's "fixture is a root component" (`fixture_c`, `pssc test --fixture fx_8_c`) | **Adopt the fixture model.** A design "suite" *is* a pssunit fixture: a root component that instantiates the DUT, with tests as actions in it. Keep `--fixture`, and treat derived fixtures (component inheritance) as the design's "suites by inheritance". Test ID: `<fixture>::<test>[param]`. | P1.1, P1.2, D2 |
| R-3 | pssunit's binding invariant ("nothing in PSS source names the emulator") vs the design's `@pssunit::python {.module=…}` and Tier 1 PSS `mock_c` hooks | **Keep the invariant for model sources; relax it for test sources.** A double is bound by the run (CLI `--platform py:<mod>`, or `[tool.pssunit]` in `pyproject.toml` / a `pssunit.toml`), never by an annotation. PSS reaction hooks (Tier 1) live in **test** files, in components derived from `pssunit_pkg::mock_c`, and are bound by fixture composition. Drop `@pssunit::python`. | P1.15, P2.6, D3 |
| R-4 | Base for the runner | **Port the uncommitted runner into this repo** as P0.2 (sources, discover, runner, report, cmd, the tests, the docs), then extend it. Don't rewrite it: its discovery rules and its "tool error vs test failure" split are already tested. | P0.2 |
| R-5 | Exit codes: the earlier work uses `1` for both parse errors and test failures; the design uses `1` test failure, `2` compile/gate error, `3` infrastructure, `5` nothing selected | **The design's codes.** An agent must be able to tell "my test failed" from "my PSS doesn't compile" without parsing text. The earlier runner's T2.11 changes from "exit 1" to "exit 2". | P1.16 |
| R-6 | Where the runner code lives | Runner, catalog, fake and Python host API in `src/pssc/testing/`, as the earlier work does (it extends the public `pssc.testing` kit). Daemon, CLI client, debug front end and sim sessions in `src/pssc/devenv/`. | all |
| R-7 | `pytest-pssunit` packaging | A `pytest11` entry point in pssc, **inert unless** `pssunit_paths` is set in the pytest config, so a pytest run in an environment with pssc installed collects nothing new | P1.19 |

---

## 3. Cross-cutting conventions

**Repos touched, and in what order changes land.**

| Repo | What changes | Phases |
|---|---|---|
| pssc (this repo) | Runner, catalog, fake, Python host API, `pssd`, CLI, debug front end, coverage producer, `op-model-sv-vm`, `pssvm_pkg`, dv-flow tasks | 1–5 |
| `packages/zuspec-be-bc` | Operation and test entries, debug lowering, hooks and events, located errors, register paths on accesses, host-bound calls, debug stub, yieldable seam, generations, debug image profile | 1–3 |
| `packages/zuspec-rt-eng` | Host ABI, hooks, debug stub, generations, recording | 4 |
| `packages/pssparser` | Functions in action bodies (an **AST change**, which needs a clean cmake rebuild) | 2 |
| `packages/zuspec-ir-core` | Only if action functions need an IR field (expected: none, since `DataTypeClass` already holds functions) | 2 |
| `packages/covsight-core` | LCOV / Cobertura views (O11), and any missing Python API for history-node ↔ coveritem association | 2 |
| `vscode-pss-support` | Test Explorer, `pss` debug type, coverage gutters | 2, 3 |
| `pssunit` repo | Normative `pssunit_pkg` additions, framework docs | 1, 2 |
| `agent-skills-pss` | A `pssunit` skill | 5 |
| `fw-wb-dma` bench | pssvm platform adapter, `+pss_impl=vm` | 3, 4 |

Ordering rule from the pssunit bring-up: a change to what the IR *means*
(as opposed to an addition to it) lands pssparser → ir-core → be-bc → pssc, in
that order.

**Invariants every item keeps** (and AGENTS.md records as they land, D14):

1. **Release lowering is unchanged.** Debug lowering is a mode. A release-mode
   `.zbc` for every compliance and matrix model is byte-identical before and
   after each be-bc change (T-G1).
2. **Located always.** Every failure, check, sanitizer, engine error and
   refusal names `file:line:col` (`pssc.diag.at` / `Refusal` at compile time,
   `src_ref` at run time) (T-G4).
3. **A tool limitation is never a model failure.** ERROR (ours) and FAIL
   (theirs) stay distinct, as the earlier runner's `SolveUnsupported` vs
   `SolveFailure` split does.
4. **One event stream.** Coverage, sanitizers, debugger, trace and reload all
   consume `pssvm` events. None adds a private hook into the VM.
5. **The thin client imports nothing heavy** (T-G6).
6. **`--json` output has a checked-in schema** under `docs/schemas/`, and
   tests validate real output against it (T-G5).
7. **Calibrated detectors.** Every sanitizer and every differential check has
   a test showing it **fires** on a mutated input. A detector never seen to
   fire isn't counted as a guard (T-G7).

**Engineering conventions.**
- Build, sim and test output goes to log files.
- Every new module's docstring says what it is for and what it deliberately
  doesn't do.
- No new runtime dependency for core `pssc test`; CTRF and JUnit are written
  with the standard library. pyhdl-if, cffi and covsight-core are optional
  extras (`pssc[sim]`, `pssc[cov]`). `pssc test --cov` without covsight-core
  is an exit-3 error that names the extra.

---

## 4. Phase 0: baseline and harvest

**Goal:** this repo has the earlier runner, green, on a measured baseline.

- [x] **P0.1 Baseline.**
  - *Change:* measure `tests/unit`, `tests/progseq`, `tests/compliance`, and
    be-bc's and rt-eng's own suites, each to a log. Record the counts and
    known failures in §11.
  - *Files:* this document.
  - *Tests:* none.
  - *Docs:* none.
  - *Size:* S.
- [x] **P0.2 Port the earlier runner (R-4).**
  - *Change:* copy `src/pssc/testing/{cmd,discover,runner,report,sources}.py`,
    `std_libs/pssunit_pkg.pss`, `tests/pssunit/`, the 9 `test_pssunit_*`,
    `test_annotations_ast_to_ir` and `test_doc_*` files, and the 4 docs. Then
    fix it against this tree:
    - this tree's ast2ir already records annotations, so drop the duplicate
      ast2ir and driver edits from that checkout;
    - discovery uses `pssc.annotations`;
    - bc entry goes through the compliance adapter's path, factored into
      `pssc.testing.bc_session` (it becomes P1.3's home).
  - *Files:* above, `cli.py` (the `test` subparser).
  - *Tests:* the ported tests (T0.2) green, and no new unit-suite failures
    against P0.1.
  - *Docs:* D0.
  - *Size:* M.
- [x] **P0.3 Make the `pssunit_pkg` copy equality-checked.**
  - *Change:* a test that pssc's `std_libs/pssunit_pkg.pss` matches the
    normative copy in the pssunit repo, or is a declared superset during
    Phase 1 (pssunit plan, open question 1).
  - *Tests:* T0.3.
  - *Size:* S.

*Exit:* `pssc test examples/...` runs the pssunit repo's `examples/wb_dma` in
this repo. The unit suite has no new failures.

---

## 5. Phase 1: tests on a fake, from the CLI

**Goal (design §17):**
- The WB DMA operation tests are written as test actions with an inherited
  fixture, and run on the generated fake in under 1 s.
- `pssc test` on an unchanged workspace answers in under 300 ms through the
  daemon.
- pyvm is a target in the construct matrix.

### 5.1 pssunit_pkg and the catalog

- [x] **P1.1 `pssunit_pkg` additions.**
  - *Change:* add the rest of design §6, in `pssunit_pkg` names:
    - **types:** `test_a` (exists), `fixture_c` (R-2), `mock_c` (body in P2.6),
      `access_s`;
    - **annotations:** `test` (exists; add `tags`), `property {runs}`,
      `expect_error {text}`, `tag {name}`, `under_test {comp}`,
      `platform {min}`, `field_access {reg?, field, kind}`, `allow {what}`,
      `setup`, `teardown`;
    - **check imports:** `check`, `require`, `check_eq/ne/lt/le/gt/ge`,
      `check_reg`, `check_field`, `check_written`, `check_not_accessed`,
      `fail`;
    - **fake API imports:** `base(name)`, `poke_reg`, `poke_field`,
      `mem_poke`, `mem_peek`, `complete_after`, `status_sequence`,
      `fail_on_access`.

    Every function is an `import target function`, implemented by the runner.
    That keeps tests runnable on generated code later (P5.4).
  - *Files:* `src/pssc/std_libs/pssunit_pkg.pss`, the normative copy in the
    pssunit repo.
  - *Tests:* T1.1.
  - *Docs:* D2.
  - *Size:* M.
- [x] **P1.2 Static catalog.**
  - *Change:* extend `discover.py` into `catalog.py`. A test is:
    - a concrete action whose `super` chain reaches `pssunit_pkg::test_a`, in
      a fixture (a component derived from `fixture_c`), **including actions
      inherited by a derived fixture**; or
    - a fixture function marked `@pssunit_pkg::test`.

    The catalog records:
    - stable ID `<fixture>::<test>` (the file is metadata);
    - location span, used for `file:line` selection;
    - tags, property runs, `expect_error`, `platform.min`, the base chain;
    - the call graph slice (`OpModel.state_uses`/`callees`), which P2.13 uses.

    Output: `pssc test --collect-only [--json]`, with schema
    `pssunit.catalog/1`. An unresolvable base is a located tool error
    (keeping the earlier "discovery shouts" rule).
  - *Files:* `src/pssc/testing/catalog.py`, `docs/schemas/pssunit.catalog-1.json`.
  - *Tests:* T1.2.
  - *Docs:* D2, D5.
  - *Size:* M.

### 5.2 bc becomes pssvm (Python engine)

All in `packages/zuspec-be-bc`. Spec text goes in its `docs/spec/`.

- [x] **P1.3 Operation and test entries.**
  - *Change:* `lower_module` accepts a set of entries:
    - every exported target function of the component tree, keyed by stable
      symbol (`<comp type>::<fn>`), each a callable code unit with its
      parameters as leading locals;
    - each test action (`<action type>::body`, plus its `super` chain);
    - each test function.

    `ZbcModel` gains an entry table: symbol → code unit, signature. The
    Python session API in pssc:
    - `s = pssvm.load(model)`
    - `root = s.construct(root_type, args)`
    - `s.call(root, "ch[0].transfer_single", args)`
    - `s.run_test(test_id, seed)`

    Component construction reuses `$comp_init`.
  - *Files:* be-bc `lower/driver.py`, `model.py`, `format/spec.py` (an entry
    section), `interp/oracle.py`; pssc `src/pssc/testing/bc_session.py`.
  - *Tests:* T1.3 (including the **op-model-py bus-trace differential**).
  - *Docs:* D11a.
  - *Size:* L. Split (2026-11-18) into:
    - [x] **P1.3a** function entries: `lower_module(entry_functions=[...])`,
      `ZbcModel.entries` (symbol -> `EntryPoint`: coro, kind, params, returns),
      in-memory; the runner runs `@pssunit_pkg::test` functions. Test:
      `python -m pytest tests/unit/test_pssunit_entries.py`.
    - [x] **P1.3b** session API `pssvm` in `bc_session.py` (`load`,
      `construct`, `call(root, "ch[0].f", args)` on any instance, `run_test`);
      entries for every exported target function of the component tree
      (not only the root instance's). Test:
      `python -m pytest tests/unit/test_pssunit_entries.py` (9 pass).
    - [x] **P1.3c** test action `super` chain (T1.3 second bullet). Test:
      `python -m pytest tests/unit/test_pssunit_entries.py` (10 pass).
    - [x] **P1.3d1a** bc lowers register access (found 2026-11-18: lowering
      the WB DMA op model with `functions=["*"]` fails in `_inline` of
      `reg_c::write(R r)`, "type NoneType () is not supported": be-bc has no
      `reg_c`/`reg_group_c` support). Needed by the model: `write`, `read`,
      `write_val`, `write_field`, `set_handle` on registers in reg groups
      (address = group handle + register offset; width = register size).
      Memory builtins (`read32`/`write32`, `make_handle_from_handle`) already
      lower (`_MEM`, `_library_type` in be-bc lower/procedural.py).
      Test: `python -m pytest tests/unit/test_bc_registers.py` (5 pass).
    - [x] **P1.3d1b** entry functions with struct parameters (be-bc
      `lower_entry_function`), so WB DMA lowers with `functions=["*"]`.
    - [x] **P1.3d1** op-model-py bus-trace differential (T1.3 first bullet):
      for every exported operation of WB DMA, `bc_session.Session.call` on
      pyvm and the generated op-model-py on a `MemoryBus` give identical
      traces (addr, width, data, order) for a fixed argument set; calibrated
      by an offset mutation in the bc lowering that makes it fail.
      Test: `tests/unit/test_pssunit_optrace.py` (new).
    - [x] **P1.3d2** spec entry section (`format/spec.py`, reader/writer
      round-trip of `ZbcModel.entries`, `interp/oracle.py`); D11a.
- [x] **P1.4 Debug lowering.**
  - *Change:* a `debug=True` lowering mode:
    - no inlining of target functions: each becomes a code unit called with
      `CALL`, using the existing called form (`ARG`/`LD_ARG`);
    - locals carry names in a side table;
    - every PSS statement starts with exactly one statement marker (a `STMT`
      no-op opcode, or a flag on the LINE table, whichever keeps release
      images byte-identical; decide in the spec).

    `pssc test` uses debug lowering by default.
  - *Files:* be-bc `lower/procedural.py`, `lower/context.py`, `model.py`.
  - *Tests:* T1.4, T-G1.
  - *Docs:* D11a.
  - *Size:* L.
- [x] **P1.5 Debug image profile and located errors.** Split in three:
  **P1.5a** [x] located `VMError` and `error()`/`fatal()` in pssc's output;
  **P1.5b** [x] `profile="debug"`; **P1.5c** [x] API hash + generation id (an `IDENT`
  section, not the header: the 48-byte header has no spare field).
  - *Change:*
    - `write_image(profile="debug")` keeps FILE/PROV/LINE (and optionally the
      PSS sources);
    - `VMError` and the PSS `error()`/`fatal()` carry `src_ref`, resolved to
      `file:line:col` in pssc's failure output;
    - the API hash (every export and import signature) and a generation id go
      in the header.
  - *Files:* be-bc `format/writer.py`, `format/spec.py`, `interp/vm.py`.
  - *Tests:* T1.5.
  - *Docs:* D11a.
  - *Size:* M.
- [x] **P1.6 Event hooks and the event stream.** Split in three:
  **P1.6a** [x] the mechanism: per-code-unit event mask checked in
  `run_frame`, the three sinks, events `stmt`, `call/return` and
  `test.start/end`; T-G3 overhead check;
  **P1.6b** [x] events `access`, `import` (with `mocked`), `message`, `check`;
  **P1.6c** [x] events `notify`, `chan`, `sanitizer`; `trace/schema.py`
  version 2 and `docs/spec/trace-schema.md`; D11b.
  - *Change:*
    - a per-code-unit event mask, checked in `run_frame`; the cost while
      disabled is one attribute test per code-unit entry, not per
      instruction (in the spirit of PEP 669);
    - events: `stmt`, `call/return` (with a `mocked` flag), `access`,
      `import`, `notify`, `chan`, `check`, `sanitizer`, `message`,
      `test.start/end`;
    - `trace/schema.py` goes to version 2 (orchestration events unchanged);
    - sinks: memory, NDJSON file, and a callback.
  - *Files:* be-bc `interp/vm.py`, `interp/ops_proc.py`, `interp/ops_orch.py`,
    `trace/schema.py`, `trace/sink.py`, `docs/spec/trace-schema.md`.
  - *Tests:* T1.6, T-G3 (overhead).
  - *Docs:* D11b.
  - *Size:* L.
- [x] **P1.7 Register path on every access.**
  - *Change:* lowering knows each register access's `RegAccessor` (from
    `reg_layout.collect_accessors`, never a second walk). It records a
    reg-id per access instruction in a side table, so `access` events and the
    platform receive `(addr, width, data, reg_id)`. pssc decodes reg-id to the
    path and fields.
  - *Files:* be-bc `lower/procedural.py`, `model.py`; pssc `bc_session.py`.
  - *Tests:* T1.7.
  - *Docs:* D11a.
  - *Size:* M.
- [x] **P1.8 Host-bound functions.**
  - *Change:* a binding table, symbol → host callable. A `CALL` whose target
    is bound runs the host callable (args in, result out) and emits
    `call` with `mocked=true`. Bindings are scoped to one test run.
  - *Files:* be-bc `interp/vm.py`.
  - *Tests:* T1.8.
  - *Docs:* D11a.
  - *Size:* M.
  - *Depends on:* P1.4.

### 5.3 Doubles, checks and sanitizers

- [x] **P1.9 Python host API, `pssc.testing.vm`.** Split (`pv.RegisterFake` moved to P1.10):
  - [x] *P1.9a* `pv.load`, `pv.Session`, `pv.Platform` (memory, user imports,
    `message` routed to the platform); `check_import_api` against
    op-model-py's `<Root>ImportApi` (T1.9a: tests/unit/test_pssunit_vm.py).
  - [x] *P1.9b* `@pv.implements/mock/spy` (mock and spy on `VM.bind`; implements
    on the platform's import seam, `mocked` on `import` events), `ctx` with
    `ctx.time`, `ctx.raise_irq`; `pv.RegisterFake` stays with P1.10
    (T1.9b: tests/unit/test_pssunit_vm.py).
  - *Change:* the public API of design §7.3:
    - `pv.load`, `Session`, `Platform`;
    - `@pv.implements(import)`, `@pv.mock(symbol)`, `@pv.spy(symbol)`;
    - `pv.RegisterFake` (subclassable, with `@pv.on_write/on_read`);
    - `ctx.raise_irq`, `ctx.time`.

    A `Platform` has op-model-py's `<Root>ImportApi` shape, and the test runs
    `check_import_api` against the generated Protocol (R-3, D7).
  - *Files:* `src/pssc/testing/vm.py`.
  - *Tests:* T1.9.
  - *Docs:* D3.
  - *Size:* M.
- [x] **P1.10 Tier 0 register fake.** Split:
  - [x] *P1.10a* `pv.RegisterFake` (`src/pssc/testing/fake.py`, a
    `pv.Platform`): storage per register instance of a debug image
    (`model.registers`), reset values (`resets` by path; PSS has none),
    READONLY/WRITEONLY, aliased instances merged by address, sparse memory
    elsewhere; be-bc tells the memory the access's register first
    (`note_access`) (T1.10a: tests/unit/test_pssunit_fake.py).
  - [x] *P1.10b* RC/W1C/W1S/volatile from `@pssunit::field_access`.
  - [x] *P1.10c* decoded bus log (`group.reg{fields}` + `src_ref`); back
    door `check_reg`, `poke_field`, `mem_peek/poke`.
  - [x] *P1.10d* `@pv.on_write/on_read` hooks; an unimplemented import is
    a located FAIL.
  - *Change:*
    - built from the `OpModel`'s accessors: storage per register instance,
      reset values;
    - RO/WO/RW from PSS; RC/W1C/W1S/volatile from `field_access` annotations
      or from an IP-XACT / SystemRDL file (`--reg-semantics`, P5.6);
    - sparse memory for non-register regions;
    - a bus log, decoded to `group.reg{fields}` plus `src_ref`;
    - a back door (`check_reg`, `poke_*`) with no side effects and no log
      entry;
    - an unimplemented import is a located FAIL, never a silent 0.
  - *Files:* `src/pssc/testing/fake.py`.
  - *Tests:* T1.10.
  - *Docs:* D3.
  - *Size:* L.
- [x] **P1.11 Checks with operand capture.**
  - *Change:* an IR pass, run before bc lowering (and reused by the
    generators in P5.4):
    - it rewrites each `check*`/`require` call to evaluate every
      subexpression once into a temporary;
    - it passes the operands and a check id to the runner import;
    - a table maps the check id to source text and operand spans.

    The runner renders the failure card of design §6.6: operand values,
    register values decoded into fields, the last N bus accesses with their
    locations, and the repro command. `require` stops the test; `check`
    continues.
  - *Parts:* [x] P1.11a rewrite into temporaries + `check_operand`/
    `check_capture(id, ok)` (bc IMPORT takes at most 2 args) + check table
    (`model.checks`); [x] P1.11b `pv.Session` routes `pssunit_pkg` imports to
    `imports.IMPORTS` with a `RunState` (checks = model.checks; `require`
    stops the test; ledger counts in `run_test`); [x] P1.11c failure card
    (operand values, decoded registers, last N `RegisterFake.bus` accesses,
    repro) in report.py.
  - *Files:* `src/pssc/testing/check_rewrite.py`, `src/pssc/testing/report.py`.
  - *Tests:* T1.11.
  - *Docs:* D2.
  - *Size:* M.
- [x] **P1.12 Sanitizers, first set.**
  - *Change:* `poll-budget` (N seam crossings without progress, located at
    the loop and the polled register), `unmapped` and `access-rule`, as
    event-stream consumers. `@pssunit_pkg::allow` switches one off. A failing
    sanitizer is a FAIL whose location is the access.
  - *Files:* `src/pssc/testing/sanitizers.py`.
  - *Tests:* T1.12 (calibrated).
  - *Docs:* D2.
  - *Size:* M.
- [x] **P1.13 Isolation, seeds, property runs, `expect_error`.** In parts:
  - [x] **P1.13a** a fresh tree per test; `@pssunit_pkg::setup`/`teardown`
    (`catalog.hooks`; `bc_session.run(setup=, teardown=)`). Seeds were
    already printed (summary, repro line) — test_pssunit_isolation.py.
  - [x] **P1.13b** property runs (`@property {.runs=N}`, a seed per run, each
    printed) and `.pssunit/regressions/<test>.seeds` (kept, always re-run).
  - [x] **P1.13c** `expect_error {text}`: PASS only on a matching error,
    located.
  - [x] **P1.13d** `pssc test` runs through `vm.Session` (one code path with
    the Python API): the CLI then counts pkg import checks, prints failure
    cards (P1.11c) and runs sanitizers (P1.12). Before P1.16.
  - *Change:*
    - a fresh component tree per test;
    - setup and teardown;
    - `@property {.runs=N}`: N runs, each re-solved with its own seed;
    - every run prints its seed;
    - failing seeds are kept in `.pssunit/regressions/<test>.seeds` and
      always re-run;
    - `expect_error`: the test passes only on an error matching the text,
      with a location.
  - *Files:* `src/pssc/testing/runner.py`.
  - *Tests:* T1.13.
  - *Docs:* D2.
  - *Size:* M.
- [x] **P1.14 The gate runs first.**
  - *Change:* `pssc test` runs the op-model gate (`validate_calls` and
    friends) for the project's declared targets (`--targets` or
    `[tool.pssunit].targets`; default `op-model-c`). Test sources and
    `pssunit_pkg` are exempt. A refusal is exit 2, located.
  - *Files:* `src/pssc/testing/cmd.py`.
  - *Tests:* T1.14.
  - *Docs:* D2.
  - *Size:* S.
- [x] **P1.15 Python-hosted runs bound by the run (R-3).**
  - *Change:* `--platform py:<module>[:<Class>]` and
    `[tool.pssunit] platform = …` import a module of `@pv.*` bindings.
    Bindings are scoped to each test. A module that fails to import is
    exit 3, with its traceback in the log file and the summary on stdout.
  - *Files:* `src/pssc/testing/cmd.py`, `vm.py`.
  - *Tests:* T1.15.
  - *Docs:* D3.
  - *Size:* S.

### 5.4 Runner, daemon and CLI

- [x] **P1.16 `pssc test` (Phase 1 option set).** Split into one step each:
  - [x] P1.16a selection: test-ID glob src-specs (`SUITE::TEST`, any spec with
    `::` that is not a path), `-m TAG` (repeatable, union), `--lf`
    (`.pssunit/lastfailed`). `FILE:LINE`, `-k`, `--fixture`, `--group` and the
    R-5 exit codes already existed. Tests: `tests/unit/test_pssunit_select.py`.
  - [x] P1.16b runs: `--platform fake`, `--seeds N`, `-j N` (process pool, one
    image per worker; `-j` verdicts equal `-j1`). Tests:
    `tests/unit/test_pssunit_runs.py`.
  - [x] P1.16c output: `--json` NDJSON `pssunit.events/1`
    (`docs/schemas/pssunit.events-1.json`), `--report` (CTRF alias of
    `--ctrf`), `--junit PATH`; CTRF/JUnit/NDJSON validate.
  - [x] P1.16d every failure prints its repro command, and running it
    reproduces the failure; default output = cards + summary.
  - *Change:* the options:
    - selection: `SEL…` (ID glob, `file:line`, fixture), `-k`, `-m TAG`,
      `--lf`;
    - runs: `--platform fake|py:…`, `--seed N|--seeds N`, `-j N` (a process
      pool, one image per worker);
    - output: `--json` (NDJSON `pssunit.events/1`), `--report` (CTRF),
      `--junit`.

    Exit codes follow R-5. The default output is one failure card per failure
    plus a summary, and every failure prints its repro command.
  - *Files:* `src/pssc/testing/cmd.py`, `report.py`,
    `docs/schemas/pssunit.events-1.json`.
  - *Tests:* T1.16.
  - *Docs:* D1, D4, D5.
  - *Size:* M.
- [x] **P1.17 `pssc model api|regs|explain <addr|path>|tests`.**
  - *Change:* answers from the manifest data (`targets/manifest.py`), with no
    re-walk. `explain 0x88` gives the register, the fields, and the
    operations that touch it.
  - *Files:* `src/pssc/devenv/model_cmd.py`.
  - *Tests:* T1.17.
  - *Docs:* D4.
  - *Size:* S.
- [x] **P1.18 `pssd` daemon and thin client.**
  - *Change:*
    - **daemon:** per workspace (keyed on the project root), Unix socket,
      versioned JSON framing; started on first use and stopped after idle;
      `pssc daemon status|stop`.
    - **what it keeps:** the imported pipeline (~160 ms of imports), and,
      keyed on the hash of every source file, the translated context,
      catalog, lowered images and the gate verdict. A changed file rebuilds
      parse → link → translate → lower whole (S1: pssparser cannot re-link
      cached units, and a whole rebuild is ~95 ms for WB DMA); the gate is
      re-run only when a gated (device) file's hash changed.
    - **thin client:** the console entry must not import `pssc/__init__`'s
      parser, so add a light entry module (`pssc/_entry.py`) that routes to
      the daemon or falls back in-process. `PSSC_NO_DAEMON=1` forces
      in-process. A version mismatch restarts the daemon.
    - **`pssc help --json`:** every command, its arguments and output
      schemas.
  - *Files:* `src/pssc/devenv/{daemon,client,protocol}.py`, `src/pssc/_entry.py`,
    `pyproject.toml` (the entry point moves to `_entry:main`).
  - *Tests:* T1.18, T-G2, T-G6.
  - *Docs:* D4, D10.
  - *Size:* L, in parts:
    - [x] **P1.18a** protocol (versioned JSON over a Unix socket), daemon
      (per workspace, idle stop, version-mismatch restart), client,
      `pssc/_entry.py` routing + `PSSC_NO_DAEMON=1`, `pssc daemon
      status|stop`; `pssc test` and `pssc model` served (T1.18, T-G6).
    - [x] **P1.18b** source-hash caches (context, catalog, images, gate
      verdict); T-G2 measured and recorded.
    - [x] **P1.18c** `pssc help --json`.
  - *Risk:* (S1, done) pssparser cannot re-link cached units; whole rebuild
    is fast enough, so the cache sits downstream of the parser.
- [x] **P1.19 `pytest-pssunit` (R-7).**
  - *Change:* a `pytest11` entry point that is inert unless `pssunit_paths`
    is set. It collects catalog tests as items, and provides a `pss` fixture
    (a session on the same engine) that Python tests can call operations
    through.
  - *Files:* `src/pssc/testing/pytest_plugin.py`, `pyproject.toml`.
  - *Tests:* T1.19.
  - *Docs:* D3.
  - *Size:* S.
- [x] **P1.20 pyvm in the construct matrix.**
  - *Change:* `trace_harness.py` gains a `pyvm` target that runs each case's
    operations through P1.3 and records the same trace. A case pyvm can't
    handle is a strict xfail naming its defect. `matrix/test_coverage.py`
    covers pyvm.
  - *Files:* `tests/progseq/matrix/trace_harness.py`, the matrix modules.
  - *Tests:* T1.20.
  - *Docs:* none.
  - *Size:* M. Split:
  - [x] P1.20a `trace_harness` target `pyvm` (`_run_pyvm`, `_lower_pyvm`,
    `PYVM_GATE`: gated by op-model-c as `pssc test` is; `_matrix.expected`
    gives pyvm C's refusal). Census: 217 pass, 252 fail -- 240 on one defect
    (an inline `reg_c<...> R;` field: ir-core comp_tree "'reg_c<None>' is not
    a component type"), 5 `read_struct`/`write_struct` unknown, 3 C refusals.
    pyvm is not in `_matrix.ALL` yet.
  - [x] P1.20b fix the inline `reg_c<...>` field in pyvm lowering (ir-core
    comp_tree `CompLayouts._add_anonymous`: the field's own `reg_c<None>`
    object gets a type-map key `reg_c<bit[32], READWRITE, 32>`). pyvm census:
    352 pass, 117 fail (be-bc gaps: arrays, `in`, `**`, struct returns, ...).
  - [x] P1.20c pyvm in `_matrix.ALL`; strict xfails for the rest; coverage.
- [x] **P1.21 The WB DMA test suite.**
  - *Change:* write `examples/op_model/tests/` with:
    - a fixture;
    - an inherited channel fixture;
    - the companion example's operation tests as test actions;
    - one property test;
    - one `expect_error`;
    - one test function.

    This is both the Phase 1 exit check and the D1 tutorial's source.
  - *Files:* `examples/op_model/tests/*.pss`.
  - *Tests:* T1.21.
  - *Docs:* D1.
  - *Size:* M.

*Phase 1 exit:*
- T1.21 passes in under 1 s.
- T-G2 (300 ms warm) passes.
- T1.20 passes.
- The guards T-G1, T-G4, T-G5, T-G6 and T-G7 are green.
- D0–D5 are written.

---

## 6. Phase 2: debugging, coverage, hooks, action functions

**Goal (design §17):** an agent using only `pssc` commands finds a seeded WB
DMA bug (a wrong `chk_sz` field) through a register watch, and a human watches
the same session in VS Code.

### 6.1 Debugging

- [x] **P2.1 Engine debug-stub protocol, implemented in pyvm.**
  - *Change:* specify the stub (design §12.2):
    - control: `stop/continue`, `step stmt|into|out`, `set_pc_breaks`,
      `set_access_watch {reg_id, R/W, value?}`;
    - inspection: `threads`, `frames`, `read_frame`, `read_comp`,
      `backdoor_read`, `exec_snippet`;
    - stop events.

    Messages are JSON, so the transport is the same in-process and over a
    socket (P3.9). pyvm implements it on the P1.6 hooks.
  - *Files:* be-bc `interp/debug.py`, `docs/spec/debug-stub.md`.
  - *Tests:* T2.1.
  - *Docs:* D11c.
  - *Size:* L.
- [x] **P2.2 Debug front end.**
  - *Change:* in the daemon:
    - breakpoint resolution, line → pc (LINE table plus statement markers),
      including conditional, hit-count and logpoint breakpoints;
    - register watches by path (`dma.ch[3].regs.csr.err`);
    - the variable model: locals, action attributes, component fields,
      **Registers** (back door, decoded), **Channels**, **Bus** (last N);
    - expression evaluation: parse the PSS expression in the frame's scope
      and compile it to an `exec_snippet`. **Spike S2:** can pssparser parse
      a standalone expression against a linked scope?
  - *Files:* `src/pssc/devenv/debug/{frontend,breakpoints,variables,eval}.py`.
  - *Tests:* T2.2.
  - *Docs:* D6.
  - *Size:* L.
- [x] **P2.3 `pssc dbg` sessions.**
  - *Change:* `start` (returns a session ID), `break add`, `watch`,
    `run/continue [--timeout]`, `step/next/out`, `bt`, `locals`,
    `regs [path]`, `bus [-n]`, `eval`, `threads`, `end`.
    - Commands default to the most recent session.
    - A run command blocks until the next stop and prints one stop record
      (`pssunit.stop/1`).
    - A session survives client exits and ends on `end` or daemon idle.
  - *Files:* `src/pssc/devenv/dbg_cmd.py`, `docs/schemas/pssunit.stop-1.json`.
  - *Tests:* T2.3.
  - *Docs:* D4, D6, D10.
  - *Size:* M.
- [x] **P2.4 DAP server.**
  - *Change:* in the daemon:
    - `launch` (a test) and `attach` (an existing session, shared with the
      CLI);
    - `setBreakpoints`, `setDataBreakpoints` (register watches),
      `setExceptionBreakpoints` (check failure, sanitizer, `error`,
      unimplemented import, mocked call);
    - `stackTrace`, `scopes`, `variables`, `evaluate`, `threads`, stepping.

    Stop events fan out to every client attached to the session.
  - *Files:* `src/pssc/devenv/debug/dap.py`.
  - *Tests:* T2.4.
  - *Docs:* D6.
  - *Size:* L.
  - *Parts* (one step each; opened 2026-11-18):
    - [x] a. `dap.py`: Content-Length framing; `Connection` (one client,
      transport-free: binary in/out streams) with `initialize`, `launch`
      (`sources`, `test`, `seed`, `platform`, `cwd`) and `attach`
      (`session`) on `sessions.REGISTRY`, `configurationDone`, `threads`,
      `disconnect` (ends a launched session, detaches an attached one).
    - [x] b. `setBreakpoints`, `setDataBreakpoints` (+ `dataBreakpointInfo`),
      `setExceptionBreakpoints` (filters = stub stop-on kinds),
      `stackTrace`, `scopes`, `variables`, `evaluate`, `continue`, `next`,
      `stepIn`, `stepOut`, `pause`; `stopped`/`output`/`terminated` events.
    - [x] c1. one session, many clients: `DebugSession` gets one waiter and
      listeners, so a stop reached by a CLI run command or a DAP
      `continue` goes to every attached client.
    - [x] c2. the daemon serves DAP on a second socket (thread per
      connection) and `pssc dap` relays stdio to it (in-process with
      `PSSC_NO_DAEMON=1`) for VS Code.
    - [x] d. T2.4 transcript test (recorded requests → expected responses,
      volatile fields masked) + CLI and DAP on one session both see a stop.
    - [x] e. D6 "VS Code" section of docs/debugging.rst; D4 regenerated.
- [x] **P2.5 Remaining sanitizers** (d moved to P2.6b).
  - *Change:* `inflight`, `unconsumed-irq`, `rc-read`, `unbound`.
    - [x] a. `inflight` on `chan` events: a latch (a channel the model only
      uses with `try_put`/`try_get`) holding a token at the end of a test;
      located at the untaken `try_put`; D2 row.
    - [x] b. `rc-read`: a read of a register with an RC field whose value
      is discarded (an expression statement; debug lowering marks the read,
      the `access` event says `discarded`); D2 row.
    - [x] c. `unbound`: a register group accessed before any `set_handle`.
    - [-] d. `unconsumed-irq`: a notification raised and never consumed.
      Deferred to P2.6b: `ctx.raise_irq` only records `Session.irqs` and a
      `notify` event; nothing in pssc or be-bc delivers it to the model's
      `notify_irq`, so there is no "consumed" signal yet (checked 2026-11-18).
  - *Files:* `sanitizers.py`.
  - *Tests:* T2.5 (calibrated).
  - *Docs:* D2.
  - *Size:* M.

### 6.2 Tier 1 doubles

- [x] **P2.6 PSS reaction hooks (R-3).**
  - *Change:*
    - `pssunit_pkg::mock_c`, with `@on_write`, `@on_read` and `@on_event`
      functions;
    - `schedule_after`, `poke_field_at`, `raise_irq`, `state_get/set`;
    - the register path strings in annotations are resolved at compile time
      (a typo is a located error, O3);
    - `complete_after`, `status_sequence` and `fail_on_access` are
      re-implemented as library hooks.

    Hooks are compiled into the image, run on both engines, and are bound by
    fixture composition.
  - *Parts* (one step each; a and c start with a short spike, findings in §11 log):
    - [x] a. (done 2026-11-18: handler thread queued at raise time, runs at
      the next scheduling point; `vm.Session(irq_handler=)`, default
      `notify_irq`; no `--irq` CLI option yet -- §11 log)
      IRQ delivery (Tier 0): `ctx.raise_irq` queues the irq; the
      Session delivers it at the next seam crossing (after the access or
      import returns) by running the model's handler (WB DMA
      `wb_dma_c::notify_irq`) on the same VM. Spike first: can be-bc run a
      second entry while the test entry is suspended at a seam? How does the
      run name the handler (R-3: by the run, not an annotation -- guess:
      `--irq <comp>::<fn>` / `[tool.pssunit] irq`, default `notify_irq` of
      the root component)? A `delivered` field on the `notify` event.
    - [x] b. `unconsumed-irq` (was P2.5d): an irq raised and never
      delivered by the end of the test (or delivered with no handler),
      located at the hook/import that raised it; NAMES + `allows`; fixture +
      seeded/clean/allowed tests in test_pssunit_sanitizers.py; D2 row.
    - [x] c. Hook declarations: `@pssunit_pkg::on_write/on_read/on_event`
      annotations on `target function`s of a `mock_c`-derived component in
      a test file; register path strings resolved at compile time (a typo is
      a located error, O3). Spike first: annotation access from ast2ir /
      catalog.
    - [x] d. Hook code units: be-bc emits each hook as an entry in the image
      (debug and release); `src/pssc/testing/hooks.py` binds them to the
      RegisterFake `on_write`/`on_read` dispatch when the fixture composes
      the mock (fixture composition, R-3).
    - [ ] e. Hook library, split:
      - [x] e1. (done 2026-11-18) `poke_field_at`, `raise_irq`, `state_get/set`
        in pssunit_pkg.pss, routed in imports.py; qualified `pssunit_pkg::f`
        calls rewritten to the `self.f` form bc lowers.
      - [x] e2. `schedule_after` + `on_event` dispatch (virtual time,
        `Ctx.time`); `access_s` argument (with `index`) for register hooks.
    - [x] f. (done 2026-11-18: Python library hooks in the fake's hook list,
      not PSS -- §11 log) `complete_after`, `status_sequence`, `fail_on_access` re-done as
      library hooks (same behaviour; existing fake tests still pass).
    - [x] g. (done 2026-11-18; §11 log) T2.6 on the WB DMA suite (design §7.2 example as a test-file
      mock: hook fires on the `ch_en` write, typo is a located error) + D3
      "PSS reaction hooks" section of docs/platform-doubles.rst (replace the
      "not implemented" note).
  - *Files:* `pssunit_pkg.pss`, `src/pssc/testing/hooks.py`, be-bc (hook code
    units as entries).
  - *Tests:* T2.6.
  - *Docs:* D3.
  - *Size:* L.

### 6.3 Coverage

- [x] **P2.7 Coverage collection with stable IDs.**
  - *Change:*
    - statement and branch counters from `stmt` events (and branch-arm
      events: add `branch` to P1.6's set);
    - coveritem IDs from PSS structure: type, function, statement path
      (O13); lines are source info only;
    - the boundary from `@under_test` / `--cov-scope` over the component
      tree, with `@nocover`;
    - register and field coverage (access, field values in bins 0/max/enum
      items/toggle, RC/W1C side effects).
  - *Parts* (one step each):
    - [x] a. be-bc, debug lowering: a coverage table per coroutine
      (`CoroDescriptor.cov`, in memory like `chans`): each statement marker
      gets a structural path (index in its block, `then`/`else`/arm `k`
      below an `if`/if-chain/`match`/loop body); each branch arm gets a
      marker pc of its own (a NOP for an empty or implicit `else`, so the
      implicit else is counted). New `branch` event (`EV_BRANCH`, schema
      kind) with `{branch, arm}`; `stmt` event detail gains `path`. Check:
      `tests/unit/test_bc_cov_lowering.py` (pssc: needs PSS source, as
      test_bc_debug_lowering does) + be-bc suite + pssc unit.
    - [x] b. `src/pssc/testing/coverage.py`: a collector on the event sink
      (Session `events=`) counting statements and arms by stable ID
      `<scope>/<path>` (scope = function qname or `<action>.<exec kind>`),
      lines kept as source info only; `report()` dict. T2.7 part 1: counts
      match a hand count on small models; IDs equal across an edit that
      moves lines. Check: `tests/unit/test_pssunit_coverage.py` (new).
    - [x] c1. `pssc test --cov <file.json>` writes the report (each test
      counts its own, `Result.coverage`; merged in `run`, also under `-j`;
      `--no-debug` refused); cli-reference regen. Check: same test file.
    - [x] c2. Boundary: `@pssunit_pkg::under_test {.comp=}` / `--cov-scope`
      over the component tree (inheritance, sub-components); excluded:
      pssunit_pkg, mocks/hooks, fixtures, test actions, std libs;
      `@pssunit_pkg::nocover` (new annotation in the pkg superset) on a
      function or statement; `--cov-scope` (cli-reference regen). T2.7
      part 2. Check: same test file.
    - [x] d. Register and field coverage from `access` events + the fake:
      per register read/written, field value bins (0, max, each enum item,
      toggle per bit), RC/W1C side effects seen. Check: same test file
      (register cases on the FIELD_ACCESS_PSS fixture).
    - [x] e. D7 skeleton `docs/coverage.rst` (layers, boundary, IDs, the
      JSON report; covsight mapping waits for P2.8), blocks verbatim from
      tests + doc test; WB DMA suite coverage smoke. Check:
      `tests/unit/test_doc_coverage.py` + `-k doc` + Sphinx.
  - *Files:* `src/pssc/testing/coverage.py`.
  - *Tests:* T2.7.
  - *Docs:* D7.
  - *Size:* L.
- [ ] **P2.8 covsight producer.**
  - *Change:* write the design §11.3 mapping through covsight-core's Python
    API:
    - design units and instances (`UCIS_OTHER` + `language="PSS"`);
    - `UCIS_BLOCK`/`STMTBIN`, `UCIS_BRANCH`/`BRANCHBIN`;
    - register covergroups;
    - `HISTORYNODE_TEST` per test (ID, seed, status, engine, platform,
      generation);
    - test ↔ coveritem association;
    - merge.

    **Spike S3:** confirm covsight-core's Python API has the association and
    history-node writes. Add them upstream if not.
    *S3 done 2026-11-18: all present, nothing upstream (see §11 log).*
  - *Split (2026-11-18):*
    - [x] **a** S3 spike: script built a PSS-shaped DB, wrote/read `.cdb`,
      merged, `covsight show summary|tests` read it.
    - [x] **b** `cov_ucis.py` code coverage: `pssunit.coverage/1` report →
      MemUCIS → `.cdb`: DU per component type (`DU_MODULE`, `SourceT.OTHER`,
      attribute `language=PSS`), one `INSTANCE` per type (pyvm scopes are
      per type, not per instance), `BLOCK` per scope with a `STMTBIN` per
      statement (name = stable path, source line), `BRANCH` per branch with
      a `BRANCHBIN` per arm, `IS_BR_ELSE` on implicit arms. Test:
      `tests/unit/test_pssunit_cov_ucis.py` (round trip with `NcdbReader`).
    - [x] **c** registers: covergroup per register type, cover instance per
      register, coverpoint per field (+ access), `CVGBIN` per bin.
    - [x] **d** `HISTORYNODE_TEST` per test (ID, seed, status, engine,
      platform, image generation attributes) + test ↔ coveritem association
      from each `Result.coverage` (flat DFS bin index); `pssc test --cov
      x.cdb` writes covsight (suffix picks the format; `.json` unchanged);
      cli-reference regenerated if help changes.
    - [x] **e** merge (`NcdbMerger`; `HISTORYNODE_MERGE`), T2.8 WB DMA
      smoke read by `covsight show summary|tests`, D7 covsight section in
      docs/coverage.rst.
  - *Files:* `src/pssc/testing/cov_ucis.py`; covsight-core if needed.
  - *Tests:* T2.8.
  - *Docs:* D7.
  - *Size:* L.
- [x] **P2.9 `pssc cov gaps|report|lcov|merge|diff --base`.**
  - *Change:* `gaps --json` gives each gap with source context. `diff`
    covers changed lines only (git diff → spans). LCOV and Cobertura views
    are implemented **in covsight-core** (O11); pssc calls them.
    (They exist: covsight `analysis/lcov.py` `write_lcov`,
    `analysis/cobertura.py` `write_cobertura`, over `CodeCoverage(db)`.)
  - *Parts:*
    - [x] **a** `pssc cov gaps [--json] [--kind] [--context N]
      [--source-root DIR] REPORT` over a `pssunit.coverage/1` JSON:
      unhit statements/arms with source lines, unaccessed registers,
      unseen bins of accessed ones; schema `pssc.cov-gaps/1`.
    - [x] **b** `.cdb` input for `pssc cov` (read back to the report
      shape) and `pssc cov report [--json]` (per kind, per file/scope).
    - [x] **c** `pssc cov lcov|cobertura REPORT -o FILE` (JSON → `to_ucis`
      → covsight writers; check genhtml-free: parse the output).
    - [x] **d** `pssc cov merge A B -o OUT` (JSON: `Coverage.merge`;
      `.cdb`: NcdbMerger) and `pssc cov diff --base REF` (git diff →
      changed-line spans; gaps on those lines only).
    - [x] **e** T2.9 WB DMA end to end, D7 `pssc cov` section, D4 regen.
  - *Files:* `src/pssc/devenv/cov_cmd.py`; covsight-core views.
  - *Tests:* T2.9.
  - *Docs:* D4, D7.
  - *Size:* M.

### 6.4 Trace, caching, action functions, editor

- [x] **P2.10 `--trace off|fail|all` and `pssc trace show [--at T]`.**
  - *Change:*
    - `fail` re-runs a failing test with full events and keeps the trace;
    - traces are NDJSON, version 2 (the binary `.pstrace` comes in P4.4);
    - `show` prints events with source lines, filtered.
  - *Parts:*
    - [x] **a** `src/pssc/testing/trace.py`: `TraceWriter(stream, model)`
      (a be-bc `TraceSink`; header `{"zbc_trace": 2}` plus a `test` line;
      each event gets `file`/`line` from `model.prov.entries[src_ref]`, as
      `coverage._where`), and `session_sink(cov, trace)` so `--cov` and a
      trace share the session's one `events=` (`Coverage.forward` = the
      trace; watch = OR of their masks).
      `runner._session_for(..., trace=)`. Unit test: a traced inline test
      gives stmt/call/check events with lines; with coverage too, both fill.
    - [x] **b** `pssc test --trace off|fail|all [--trace-dir DIR]` (default
      `.pssunit/traces/`, one `<test-id>.ndjson` per test, seed in the
      header): `all` traces every test (`EV_ALL`); `Result.trace_file` =
      path (`Result.trace` is the Python traceback),
      in `--json` `test.end` and the failure card. help OUTPUTS row,
      cli-reference regen.
    - [x] **c** `--trace fail`: tests run untraced; a failing test is
      re-run once with the same seed and full events, and only its trace
      is kept (a re-run that passes is reported as flaky in the card, the
      result stays the first run's). `-j N` workers do the same.
    - [x] **d** `pssc trace show FILE [--kind K]... [--coro N] [--at IDX]
      [--line FILE:N] [--context N] [--json]` in
      `src/pssc/devenv/trace_cmd.py`: one line per event with its source
      line; `--at` maps an event index to its line (with context);
      `--line` lists every index that ran it. Schema
      `pssc.trace-show/1`; help OUTPUTS; cli-reference regen.
    - [x] **e** T2.10 (WB DMA suite plus one failing test in a tmp copy:
      `--trace fail` keeps only the failure's trace; `show --at` maps an
      index to its line), D6 docs/debugging.rst "Traces" section, D4 regen.
  - *Files:* `src/pssc/devenv/trace_cmd.py`.
  - *Tests:* T2.10.
  - *Docs:* D6.
  - *Size:* M.
- [x] **P2.11 Action functions (pssc extension, D2).**
  - *Change:*
    - **pssparser:** functions in an action body (grammar plus linker scope);
      a PSS120-style warning; an **AST change**, so a clean rebuild.
    - **ast2ir:** a `_BODY_ELEMENTS` row; functions recorded on the action's
      `DataTypeClass`.
    - **New `action_inherit.py`:** virtual by copy-into-derived per concrete
      action (the `comp_inherit.complete` approach); `super.f()` static,
      through a private copy; a body-less function is abstract, and a
      concrete action leaving one unimplemented is a located error.
    - **bc:** a call with the action frame as `this`.
    - **`export_action.py`:** private methods next to the exec body on C, C++,
      Python and SV.
  - *Files:* pssparser `ast/`, grammar and linker; `src/pssc/ast2ir.py`;
    `src/pssc/targets/action_inherit.py`, `export_action.py`; be-bc lowering.
  - *Tests:* T2.11.
  - *Docs:* D12.
  - *Size:* XL; split it as P2.11a parser, P2.11b ast2ir and inherit,
    P2.11c bc, P2.11d export-action.
  - *Parts* (pssparser is its own repo, edited in place; rebuild after a
    grammar or C++ change with `cmake --build build -j24 && cmake --install
    build` in packages/pssparser, ~15 s; an AST yaml change also needs
    `build_ext`):
    - [x] **a1** grammar: `function_decl` in `action_body_item`. No AST or
      linker change was needed: the linker already scopes the body in the
      action (fields, inherited functions, `super.f()`), and a body-less
      prototype in an abstract action links. pssparser
      `tests/python/parsing/test_action_functions.py` (4).
    - [x] **a2** the extension warning: a new core-emitted lint rule
      (`LINT412` `action-function`, tier portability) raised in the linker
      on a function declared in an action; every generated artefact moves
      with it (lint/catalog.py, src/lint/catalog/catalog.json via
      scripts/gen_catalog.py, tests/data/core-codes.lock, ts parity
      fixtures, docs/lint-rules.rst, CHANGELOG). Check: pssparser
      `python -m pytest tests/python -q` (9322 at a1).
    - [x] **b1** ast2ir: a `_BODY_ELEMENTS` row; functions recorded on the
      action's `DataTypeClass`; `test_type_body_registry` row (today:
      "'FunctionDefinition' in action ... is not supported yet").
    - [x] **b2** `src/pssc/targets/action_inherit.py`: copy-into-derived per
      concrete action, `super.f()` through a private copy, an unimplemented
      abstract function is a located error. A prototype in an action is an
      abstract `ir.Function` (`metadata["abstract"]`, ast2ir
      `_body_prototype`); `complete(ctx)` runs at the end of ast2ir
      `translate`, in place, functions only (fields and exec blocks are
      inherited by each consumer's `super` walk, as before).
      `tests/unit/test_action_inherit.py` (9).
    - [x] **c** bc lowering (inlined on the action's object as `this`); a
      template-method fixture runs under `pssc test`.
      `tests/unit/test_bc_action_functions.py` (6).
    - [x] **d1** `export_action.py` helpers + op-model-py (C and C++ render
      no exported actions at all, so they refuse an entry as before).
      `tests/progseq/test_op_model_entry.py` (17).
    - [x] **d2** op-model-sv (helpers are protected tasks, a value one
      through `output status`), simulated with Verilator; goldens unchanged.
      No matrix file: the matrix drives `run()`, which cannot start an
      action, and C/C++ refuse entries; py, sv and pyvm (P2.11c) each have
      their own run test. `tests/progseq/test_op_model_entry.py` (18).
    - [x] **e** D12 docs (semantics, portability warning, PSS WG note):
      `docs/action-functions.rst` (in the toctree after writing-tests).
      `tests/unit/test_doc_action_functions.py` (3) runs the page's suite and
      checks its op-model block is `_AF_MODEL`.
- [x] **P2.12 VS Code.**
  - *Change:* in `vscode-pss-support`:
    - a Test Explorer from `pssc test --collect-only --json` (fixtures → test
      actions → derived tests), with Run, Debug and Coverage profiles, and
      failures shown inline at the check;
    - a `pss` debug type over the daemon's DAP, with a Registers view, data
      breakpoints on registers, and *Attach to session*;
    - coverage gutters from LCOV.
  - *Files:* the extension's `client/`, `package.json`.
  - *Tests:* T2.12.
  - *Docs:* D6.
  - *Size:* L.
  - *Parts* (the extension is another repo: pssc-side gaps here, the rest
    by request `vscode-p212` to project `vscode-pss-test-explorer`):
    - [x] **a** pssc side. Checked present: `--collect-only --json`
      (catalog), `--json` events, `pssc dap` + `<sock>.dap`, DAP
      launch/attach, Registers scope, data breakpoints on registers,
      `pssc dbg list --json`, `pssc cov lcov`. Gap filled: `test.end` gets
      `failures` (`{file, line, col, kind, message}` per failed check and
      located sanitizer finding; schema pssunit.events/1, documented in
      docs/test-command.rst) so failures show inline at the check.
      `tests/unit/test_pssunit_failure_locations.py` (2).
    - [x] **b** the extension (Test Explorer, `pss` debug type, Attach,
      Registers, coverage gutters; T2.12 a–c): by request `vscode-p212`
      (vscode-pss-support branch wpk/vscode-pss-test-explorer, ff571cc).
      Its headless e2e passes against this tree (7).
    - [x] **c** pssc gaps the extension found: a test id after an option
      was rejected (cli.py `parse_argv`); DAP `dataBreakpointInfo` ignored
      `variablesReference` (variables.py `register_path`). The dir form
      without `@model.f` is by design (docs/tutorial.rst says so).
- [x] **P2.13 Result cache, `--changed`, `--watch` (on fakes).**
  - *Change:* cache key: (slice hash, platform, seed, engine version). The
    slice comes from P1.2. `--changed` runs tests whose slice changed since
    the last run or `--base REV`. `--watch` re-runs affected tests on save,
    in the daemon.
  - *Files:* `src/pssc/testing/slice.py` (slice, result record),
    `src/pssc/testing/cmd.py`, `src/pssc/devenv/cache.py` (watch).
  - *Tests:* T2.13.
  - *Docs:* D4.
  - *Size:* M.
  - Parts:
    - [x] **a** slice + result record + `--changed` (since the last run).
      The slice is textual, over-approximating: a test's own span, every
      action/component function/package function it reaches by name (to a
      fixed point; plus its root's set-up/tear-down), the "rest" (all source
      outside every such unit) and the pssc/be-bc versions. Key = slice hash
      + verdict-changing options (`replay_options`, holds the platform) +
      seed (any seed when no `--seed`). Record `.pssunit/results.json`
      (beside `LAST_FAILED`), written by the first `--changed` and by every
      run once it exists; only passes (pass/skip/xfail) are reused. Catalog
      `calls` = `{units, slice}`. `tests/unit/test_pssunit_changed.py` (4).
    - [x] **b** `--base REV`: slices of the sources at git REV (`git show
      REV:path` into a temp tree, translate, slice); run tests whose slice
      differs from REV's (no result record needed). Needs `--changed`.
      Slice hashes name files relative to `source_root` (path-independent).
    - [x] **c** `--watch`: re-run `--changed` on each save (poll the
      resolved sources' size/mtime; NDJSON events per round); Ctrl-C ends
      it. The loop runs in the client (`src/pssc/testing/watch.py`, routed
      by `_entry`); each round goes to the daemon, so output streams per
      round. In-process too (`cmd_test`).
    - [x] **d** T2.13 end to end on WB DMA (edit one op, only its tests
      re-run; daemon path), D4: cli docs, `pssc help --rst` regen.

*Phase 2 exit:* (met 2026-11-18)
- [x] T2.E (the scripted agent session on a seeded `chk_sz` bug, using only
  `pssc` commands) passes: `tests/unit/test_wb_dma_t2e.py`.
- [x] The VS Code shared-session check (T2.12c) passes: the extension's e2e
  "attaches to a CLI session stopped at a breakpoint" against this tree.
- [x] Coverage for the WB DMA suite opens in covsight:
  `test_wb_dma_suite.py::test_cov_cdb_runs_merge_in_covsight` (T2.8).

---

## 7. Phases 3 to 5

### 7.1 Phase 3: simulation, then hot reload

**Goal (design §17):**
- The fw-wb-dma UVM bench runs the same tests against RTL under Verilator.
  (A commercial-simulator rerun is deferred until one is installed: owner,
  2026-11-18.)
- A body edit is hot-reloaded into a running session, and the failing test
  then passes without a restart.
- `vm` and `gen` give identical bus traces.

- [x] **P3.1 Host ABI spec and yieldable seam in pyvm.**
  - *Change:* first write the native-shaped ABI (`pssvm_load`, `construct`,
    `call_start`, `run` → ACCESS/IMPORT/WAIT/STOP/DONE, `complete`, `notify`,
    `reload`) as a spec. Then make pyvm's accesses and imports yieldable:
    `run` returns a pending request and the host completes it (the
    `zsp_bridge` mailbox pattern). The synchronous Python API of P1.9 is a
    loop over it.
  - *Files:* be-bc `docs/spec/host-abi.md`, `interp/vm.py`; pssc
    `bc_session.py`.
  - *Tests:* T3.1.
  - *Docs:* D11d.
  - *Size:* L.
  - *Progress:* P3.1a spec done (be-bc `docs/spec/host-abi.md`); P3.1b seam done (be-bc `interp/host.py`
    `HostCall`/`run_sync`, T3.1 `tests/unit/interp/test_host_abi.py`); P3.1c done (pssc `bc_session._drain`
    = one `HostCall(construct=False)` + `run_sync` per frame; D11d note in host-abi.md).
- [x] **P3.2 Model image file.** (a = `pssc build --image`; b1 = a read-back image runs in pyvm; b2 = `pssc test --image`)
  - *Change:* `pssc build --image out/x.zbc` writes the debug-profile image
    with:
    - stable-symbol entries;
    - the API hash and generation id;
    - the catalog, the register map and field semantics;
    - the PSS sources (on by default).

    An image alone can list and run tests (`pssc test --image`).
  - *Files:* `src/pssc/devenv/build_cmd.py`, be-bc format.
  - *Tests:* T3.2.
  - *Docs:* D4, D11a.
  - *Size:* M.
  - *Progress:* P3.2a done (`src/pssc/devenv/build_cmd.py`; be-bc `META` section 20, debug profile only;
    `tests/unit/test_devenv_image.py`). The model's in-memory side channels (solve problems, select tables,
    messages, strings, object layouts, activation tables, component table, per-coroutine debug tables,
    register map objects) have no be-bc format yet, so P3.2b1 carries them in an interim pyvm-only `META`
    document (pickle, checked against the be-bc version; image files are trusted local build outputs).
    A specified encoding of them is owed to the native engine (P4.3, which loads images), and replaces that document.
    P3.2b1 done (`pssc.pyvm` doc; `build_cmd.load` -> runnable `_Image` + test items, no IR context needed).
    P3.2b2 done (`pssc test --image FILE`; docs test-command.rst, test-internals.rst, cli-reference.rst).
- [x] **P3.3a Spike S4: pyvm under pyhdl-if (D6).** Go (2026-11-18; §11 log).
  - *Change:* one operation call from SV under Verilator through pyhdl-if,
    with a time-consuming access, measuring per-access overhead.
  - *Decision:* go, or go straight to P4 for the sim engine. Record the
    result in §11.
  - *Size:* S.
- [x] **P3.3 `pssvm_pkg` (SV) and the pyhdl-if shim.**
  - *Progress:* P3.3b done (pkg, run loop, `pssc sv-pssvm-path`; §11 log 2026-11-18); P3.3c done (pyhdl-if shim + `pssvm_hif_pkg`, `sv-pssvm-path --py`; §11 log 2026-11-18); P3.3d done (`pssvm_runner`, stdout PASS/FAIL + summary; §11 log 2026-11-18). Control channel = P3.5a; report files (CTRF) and traces from the runner not done yet (with P3.5/P3.7).
  - *Change:*
    - DPI declarations exactly matching the host ABI;
    - `pssvm_platform_if` (`read8..64`/`write8..64` tasks, `call_import`,
      `reset`);
    - the run loop of design §10.3 (one task per call, SV threads for
      parallel calls);
    - `pssvm_runner` (`+pssunit=SEL`, reports and traces);
    - the control channel;
    - a shim implementing the DPI functions in Python on pyvm.

    An engine error is a located failure and never kills the simulator.
  - *Files:* `src/pssc/share/sv/pssvm/pssvm_pkg.sv`, `src/pssc/share/py/pssvm_dpi_shim.py`,
    `pssc sv-pssvm-path`.
  - *Tests:* T3.3.
  - *Docs:* D8.
  - *Size:* L.
- [x] **P3.4 `op-model-sv-vm` target.**
  - *Change:* a facade with **exactly op-model-sv's API**:
    - `<root>_ctxt_if`, `<root>_imp_if`;
    - `<root>_root_vm #(Timp)::create(imp, args)` next to `<root>_root`'s;
    - the API hash embedded and checked at load.

    It reuses `sv/backend.py` sections for the API types, and only the
    implementation calls `pssvm_pkg`. `+pss_impl=vm|gen` selects the
    implementation.
  - *Files:* `src/pssc/targets/sv_vm/`, a target registration.
  - *Tests:* T3.4.
  - *Docs:* D8.
  - *Size:* L.
  - *Progress:* P3.4a1 done (target `op-model-sv-vm`, facade, `pssvm_set_default_engine`; Verilator lint; §11 log 2026-11-18). P3.4a2 done (`pssc build --api`; WB DMA facade calls run in Verilator over pyvm, accesses == pyvm's; `create` refuses an image whose API differs, `pssvm_engine::api_check`). P3.4b done: `--with-gen` (op-model-sv's package + facade + `<root>_root_sel::create`, steered by `+pss_impl=vm|gen`); one Verilator TB, compiled once, same bus trace both ways; docs/simulation.rst (D8, first part). The UVM-bench form of T3.4 runs at P3.10/P3.E.
- [x] **P3.5 Server-mode sessions.**
  - *Change:*
    - `+pssvm_serve` registers the testbench with the workspace daemon after
      boot;
    - `pssc sim list|attach|run <sel>|log`;
    - the session accepts images, test selections, reloads and debug
      requests;
    - the transport carries the P2.1 stub messages.
  - *Files:* `src/pssc/devenv/sim/`, `pssvm_pkg.sv` (control channel).
  - *Tests:* T3.5.
  - *Docs:* D8.
  - *Size:* L.
  - *Progress:* split 2026-11-19: P3.5a1 control channel without a sim
    (daemon `<socket>.sim`, `devenv/sim/registry.py` `REGISTRY`,
    testbench end `devenv/sim/serve.py` `Server`; one JSON line per
    message in the debug stub's request/reply/event shapes) done
    (`tests/unit/test_devenv_sim_channel.py`); P3.5a2 `+pssvm_serve` in
    the shim/runner under Verilator done 2026-11-19 (shim `serve`/`poll`,
    `pssvm_runner.serve`, env `PSSC_SIM_SOCKET`); P3.5b split 2026-11-19:
    P3.5b1 the testbench answers `run {sel}` (bus calls are SV tasks, so
    `poll` only queues the selection and returns 2; `pssvm_runner.serve`
    runs the tests, a `test` event each, and the reply carries the
    counts, sent later by `Server.reply`) done 2026-11-19
    (`test_devenv_sim_channel.py` `test_verilator_serve`: two runs in one
    sim); P3.5b2 `pssc sim
    list|run|log|end` over the daemon command socket (daemon op `sim`,
    answered on its own thread; `devenv/sim/ops.py`, `devenv/sim_cmd.py`)
    done 2026-11-19 (T3.5 in `test_verilator_serve`). `pssc sim attach`
    (DAP over a sim session) moves to P3.7, with the stub in the sim;
    images and reloads through the session to P3.6c.
  - *T3.5:* a Verilator testbench under `+pssvm_serve` shows in `pssc sim
    list`; two `pssc sim run <sel>` in the same sim process each run
    their tests and report pass/fail; `pssc sim log` shows the `test`
    events; `pssc sim end` ends the simulation.
- [x] **P3.6 Generations and hot reload.**
  - *Change:* in pyvm first, testable without a simulator:
    - load generation N+1 next to N;
    - quiesce at an operation boundary (and at a test boundary);
    - threads already inside generation N finish on it unless `--cancel`;
      no on-stack replacement;
    - API-hash check, with the list of changed signatures on refusal;
    - rebuild under N+1 with the recorded root arguments in **bus-isolated
      mode** (an access during construction refuses the reload);
    - carry channel contents across by path;
    - report a structural diff, refused under `--strict`;
    - `reload` event; Python mocks re-imported.

    Then expose it: `pssc sim reload`, `--watch --platform sim:<s>`, and the
    debugger's *Apply change*.
  - *Files:* be-bc `interp/generations.py`, `docs/spec/generations.md`;
    `src/pssc/devenv/sim/reload.py`.
  - *Tests:* T3.6.
  - *Docs:* D9, D11e.
  - *Size:* L.
  - *Progress:* split (2026-11-18): P3.6a1 spec be-bc `docs/spec/generations.md` + API check
    (`interp/generations.py` `check_api`/`api_diff`/`ReloadRefused`) — done; P3.6a2 load N+1 beside N, quiesce at
    call/test boundary, running calls finish on N, `cancel`, commit by symbol — done (`Generations`); P3.6a3 bus-isolated rebuild
    (pyvm construction takes no root args), state incl. channels migrated by instance path, structural diff, `strict`
    — done; P3.6b pssc sessions (no sim), split: P3.6b1 `bc_session.Session.reload` + `reload` event — done;
    P3.6b2 `pssc dbg reload`, mocks re-imported — done; P3.6b3 coverage merge across generations, D9 page, D11e — done (pyvm part
    complete); P3.6c sim, split (2026-11-19): P3.6c1 shim `reload` handler + `pssc sim reload [IMAGE] [--strict]`
    (T3.6 sim part in `test_devenv_sim_channel.py` `test_verilator_reload`) — done; P3.6c2 `pssc test --watch
    --platform sim:<s>` (rebuild the image on a source change, `sim reload`, rerun) — done; P3.6c3 DAP *Apply change* (custom request `pssc/reload`, pyvm dbg sessions;
    sim sessions wait for P3.7) — done. P3.6 done 2026-11-19.
- [x] **P3.7 Debugging in the simulation.**
  - *Change:*
    - the stub runs over the session connection;
    - `pssvm_dbg_wait()` freezes sim time while stopped;
    - `+pssvm_dbg_on_fail`;
    - `+pssvm_dbgsig` drives `pssvm_dbg.cur_op/cur_line/cur_file`;
    - every event carries sim time;
    - `pssc trace show --at <time>`.
  - *Files:* `pssvm_pkg.sv`, `src/pssc/devenv/sim/`.
  - *Tests:* T3.7.
  - *Docs:* D8, D6.
  - *Size:* M.
  - *Progress:* split 2026-11-19 (pssc-debug notes p3.7): P3.7a1 sim-side stub: shim serve handler `debug` puts
    a be-bc `Stub` on the sim's VM (no stop at entry: it stops at breakpoints inside pssvm calls, on the SV
    thread, so a stop blocks the DPI call and sim time with it); the serve channel answers stub requests on its
    reader thread (the SV thread is blocked while stopped); stub events go to the daemon. P3.7a2 `pssc sim
    attach` = a `pssc dbg` session (and DAP) over the sim channel (`RemoteStub`), T3.7 in Verilator. P3.7b
    `pssvm_dbg_wait()` + `+pssvm_dbg_on_fail`. P3.7c `+pssvm_dbgsig`, sim time in every event, `pssc trace
    show --at`, docs D8/D6. P3.7a1, a2, b, c1, c2, c3 done (2026-11-19; §11 log); c split
    into c1 sim time in events, c2 `+pssvm_trace=DIR` + `trace show --at <time>`, c3 `+pssvm_dbgsig` + D6.
    P3.7 done 2026-11-19.
- [x] **P3.8 Session fixtures and platform reset.**
  - *Change:* `@pssunit_pkg::session` (boot once, tests share the DUT) and
    the platform's `reset()` hook. A platform that can't reset declares one
    test per sim.
  - *Files:* runner, `pssvm_pkg.sv`.
  - *Tests:* T3.8.
  - *Docs:* D8.
  - *Size:* M.
  - *Progress:* split 2026-11-19: a `@pssunit_pkg::session` in pssc's own `pssunit_pkg` (the pssunit repo's
    copy has no fixtures), catalog + image META `session`, pyvm runs it before set-up on each fresh tree;
    b the sim runner: fresh tree + platform `reset()` between tests, `can_reset()` = 0 → one test per sim,
    session fixtures run once and suppress the reset; T3.8 in Verilator; c docs D8 + serve mode. P3.8a, b, c
    done 2026-11-19 (§11 log).
- [x] **P3.9 dv-flow `Image` and `Test` tasks.**
  - *Change:* a sim flow depends on `Image`, never on generated SV, so a PSS
    edit doesn't invalidate the sim build.
  - *Files:* `src/pssc/dvflow/flow.yaml`, `__ext__`.
  - *Tests:* T3.9.
  - *Docs:* D8.
  - *Size:* S.
  - *Progress:* done 2026-11-19 (§11 log): `pssc.Image`, `pssc.Test` in
    `src/pssc/dvflow/devenv.py`.
- [x] **P3.10 fw-wb-dma bench integration.**
  - *Change:* a pssvm platform adapter over the bench's UVM adapter;
    `+pss_impl=vm`. Runs under Verilator (commercial simulator deferred).
  - *Files:* a copy of the bench in pssc `examples/fw_wb_dma_uvm/` (owner,
    2026-11-18; the `featherweight-ip` original is not edited).
  - *Tests:* T3.10.
  - *Docs:* D8.
  - *Size:* L.
  - *Parts:* a) copy the bench and its build (dv-flow + Verilator) into
    `examples/fw_wb_dma_uvm/`, all its tests passing as they are; b) the
    adapter: `wb_dma_pss_mem_adapter` builds its handle with
    `wb_dma_c_root_sel::create` from pssc's `op-model-sv-vm --with-gen`
    instead of `new` on the generated class, with the pssvm shim linked into
    the image (pyhdl-if); c) `+pss_impl=vm` runs and T3.10.
  - *Progress:* P3.10a done: copy in `examples/fw_wb_dma_uvm/` (`src/`,
    `tests/uvm/`, project flow; README, `env.sh`); builds on the fw-wb-dma
    project's packages through a gitignored `packages` symlink; `dfm run tests`
    14/14 (tlm, wb, rtl views). Two findings: the original no longer compiles
    against its fw-proto-wb (memory adapters moved into the config-gated
    `fw_proto_wb_spl_pkg`; the copy compiles that package itself), and
    Verilator 5.055 (pssc's) fails the class model's C++ (`wb_dma_irq`: no
    unique final overrider for `VlClass::to_string`), so the bench uses that
    project's Verilator 5.052.
    P3.10b1 done: the model API is built by this repo's pssc
    (`src/pss/pssvm_gen.py`, a bench-local dv-flow pytask that runs
    `pssc compile -t op-model-sv-vm --with-gen --sv-enum-items verbatim` with
    `$PSSC_PYTHON`; output `pssvm_pkg.sv`, `pssc_reg_pkg.sv`,
    `wb_dma_c_pkg.sv`), and the adapter's handle is
    `wb_dma_c_root_sel #(wb_dma_c_imp_if)::create(this, base)`; 14/14 on the
    default gen path. Bench SV renames: `wb_dma_c_if` -> `wb_dma_c_exp_if`,
    `wb_dma_ch_c_if` -> `wb_dma_ch_c_exp_if`, `wb_dma_c_import_if` ->
    `wb_dma_c_imp_if`; struct arguments are now `ref` (PSS passes a handle), so
    the 10 `mk_cfg(...)`/`cfg_for(...)` call arguments go through a local.
    P3.10b2 done: the image also compiles pyhdl-if's `pyhdl_if.sv` and the
    shim packages (`pssvm_hif_api_pkg.sv`, `pssvm_hif_pkg.sv`) and links
    `libpyhdl_if.so` (task param `dpi`, default on); still 14/14 on gen.
    `PSSVM_DPI` stays off: it only selects the native C engine, and Verilator
    5.052 cannot compile its open-array import.
    P3.10c1/c2 done: `+pss_impl=vm +pss_img=<zbc>` runs the bench over pyvm;
    `vm_cmp.sh` runs all 14 tests both ways and the bus accesses match (T3.10).
    P3.10c3 done: `dfm run uvm.tests-vm` runs the same 14 cases on pyvm
    (image task `pss.op-model-vm-img`); `dfm run tests` stays on gen.

*Phase 3 exit:* (met 2026-11-19, under Verilator only)
- [x] The fw-wb-dma UVM bench copy runs its 14 tests (tlm, wb, rtl views)
  under Verilator, on the generated model (`dfm run tests`) and on pyvm
  (`dfm run uvm.tests-vm`).
- [x] Hot reload: a failing test, a fix, `pssc sim reload`, the same test
  passing in the same simulator process
  (`test_devenv_sim_channel.py::test_verilator_reload`, on the pssvm_runner
  TB; pyvm-only reload in `test_devenv_reload.py`). Not yet shown inside the
  UVM bench itself.
- [x] `vm` and `gen` give the same bus accesses: `vm_cmp.sh`, all 14 (T3.10).
- [-] Commercial-simulator rerun (P3.F): dropped, no simulator (owner 2026-11-20).

### 7.2 Phase 4: native engine

**Goal (design §17):** the Phase 3 bench runs on the native engine with no
Python in the simulator, including hot reload and live debugging.

- [x] **P4.1 Opcode gap audit.** Compare the opcodes debug lowering emits
  (P1.4–P1.8) against `zbc_interp.c`, and list the gaps in §11. Extend
  `test_ops_match_model.py`. *Size:* S.
- [x] **P4.2 Procedural and call coverage.** Implement the missing opcodes:
  `CALL` frames, statement markers, register-path side table, memory seam.
  *Tests:* T4.2. *Size:* L.
  - *Progress:* a components, b error/fatal halt, c memory seam, d SPIN, e1 traversal initializers,
    g message text (§11 log). e2/e3 (solve scopes, non-root instances) moved to P4.10. f (register-path
    table, statement markers) folded into P4.4: statement markers are already in the image (LINE,
    `ZBC_SECF_LINE_STMT`), and the reg/chan/discard tables are debug-only side tables whose only use is
    raising events, so they are written to the image and read in C with the event hooks.
- [x] **P4.3 Host ABI in C.**
  - *Change:* `pssvm.h`, implementing P3.1's spec. dv-solve is linked only
    when the image needs solving (the `future-projects.md` item). Python
    binding through cffi or ctypes, with host-bound functions as callbacks
    (so Python mocks work on the native engine when Python is present).
  - *Files:* `zuspec-rt-eng/share/include/pssvm.h`, `share/rt/pssvm_host.c`.
  - *Tests:* T4.3.
  - *Docs:* D11d.
  - *Size:* L.
  - *Split:* a `pssvm.h` + `pssvm_host.c` skeleton: `abi_version`, `load` (header, ENTRY, IDENT read in
    C), `construct`, `call_start` by entry name, `run` to DONE with imports answered as today, `result`,
    `call_end`, `error` (JSON), `free`; ctypes binding; differential vs `zbc_run`. b ACCESS and IMPORT
    yield: the interp thread suspends at `IMPORT` and resumes after `pssvm_complete`; `pssvm_request`
    (value ABI args, `reg_id` 0 until P4.4). c WAIT TIME / WAIT EVENT, `pssvm_notify` (IRQ, CHAN), located
    errors. d Python host loop on the C engine (pyvm `run_sync` shape, so Python mocks run on it); dv-solve
    linked only when the image solves; D11d. Reload is P4.6, STOP is P4.5.
  - *Progress:* a, b, c done (§11 log); c without WAIT EVENT / `pssvm_notify` (needs host-fed channels, with P4.4/P4.10) and without file/line (LINE table in C, P4.4).
    d split (2026-11-19): d1 the Python binding with be-bc's `HostCall` / `run_sync` shape (done); d2 pssc's
    test session runs on the C engine on request (same mocks), dv-solve linked only when the image solves, D11d (done; P4.3 closed).
- [x] **P4.4 Hooks, binary `.pstrace`, recording.**
  - *Change:* event masks in C, a binary sink with a shared spec (trace
    schema v2, binary form), and recording of the platform side. The daemon
    reads `.pstrace` everywhere NDJSON is read.
  - *Tests:* T4.4.
  - *Docs:* D11b.
  - *Size:* M.
  - *Split (2026-11-19):* a the debug side tables (register accesses with their discards, channel
    operations) are written to the image (sections REG, CHAN) and read in C; C requests carry `reg_id`
    (old P4.2f). b `src_ref` in `pssvm_req_t` and file/line in the error JSON (LINE/PROV/FILE read in C).
    c event masks and a binary sink in C, one spec shared with be-bc's trace v2. d the daemon reads binary
    traces wherever it reads NDJSON. e WAIT EVENT + `pssvm_notify` (host-fed IRQ/channel events).
    f recording of the platform side.
    c in three (2026-11-19): c1 the binary form specified (be-bc trace-schema.md "Binary form") with a
    Python writer/reader (`zuspec.be.bc.trace.binary`, `BinarySink`) and a round-trip test; c2 a C sink
    writing that form and a per-coroutine event mask in rt-eng (`pssvm` watch + file/callback sink),
    raising the kinds C runs (stmt, call/return, access, import, message, chan), replacing the interim
    message callback/import log; c3 a differential test: the same small models' events from pyvm and C,
    byte-equal after conversion.
    c2 in two (2026-11-19): c2a the C sink, masks, stmt/call/return/message events and
    `pssvm_set_events`; c2b access, import and chan events, then retire the interim message
    callback/import log of `pssvm_set_host`.
  - *Progress:* a, b, c (c1, c2a, c2b, c3), d, e, f done (§11 log; P4.4 closed); e = IRQ only (`N_CHAN` has no
    handler until host-fed channels, P3.6a/P4.10); pssc `Session(engine="c")` does not yet set the
    IRQ handler or notify (with P4.9's shim swap). c3 covers hand-built models only: a
    pssc-compiled differential needs an events hook on rt-eng `host.Engine` (pssc `Session(engine="c")`
    refuses events today), so it moves to P4.4d/P4.8. Gaps: C `import` events have `name: null` and
    `mocked: false` (no IMPORT name table in the image); pssvm verbosity is only settable via `pssvm_set_host`.
- [x] **P4.5 Debug stub in C.** The P2.1 protocol over a socket. *Tests:*
  T4.5. *Size:* L.
  - *Split (2026-11-19):* the engine stays single-threaded: a stop is a host-ABI yield (`PSSVM_STOP`,
    reserved in host-abi.md), so the host's `pssvm_run` returns and the host serves the stub until a
    resume, then calls `pssvm_run` again.
    a stops in C: a debug block on the host (pc breaks by coro/pc, a pause flag, a step of one thread
    `stmt`/`into`/`out` on LINE statement starts) checked at the instruction boundary of
    `zbc_interp_task`; a stop posts yield state STOP (reason, frame, coro, pc, src_ref, hits), keeps the
    pc, and the resume does not re-hit it; `pssvm_run` returns `PSSVM_STOP`; C API `pssvm_dbg_*`.
    b inspection in C: threads and frames (live frame ids), `read_frame` (C reads LOCAL), `read_comp`,
    `backdoor_read` (a host peek), access watches and `set_stop_on` (error) stops. Split (2026-11-19):
    b1 threads, frames and a frame's registers/locals/args (C), component slots (`pssvm_comp_read`),
    memory host-side (done); b2 access watches and `set_stop_on` (error) stops in C (done).
    c the stub protocol for C (split, step 198): c1 a host-side `NativeStub` (rt-eng `zuspec.rt.eng.debug`)
    with pyvm `Stub`'s interface (`request`/`next_event`/`join`/`state`) over the `pssvm_dbg_*` API; the
    host's run loop (`run_sync`) hands each `PSSVM_STOP` to it and blocks while stopped (`entry: false`);
    the be-bc conformance cases (test_debug_stub.py `CASES`) replayed on it, with the C differences listed
    (engine name, `exec_snippet` `unsupported`, `this` null, `set_stop_on` knows only `error`). c1 done.
    c2 line JSON over a socket for any stub object (one request/reply/event per line); the cases replayed
    over a socketpair. c2 done (be-bc `zuspec.be.bc.interp.stub_socket`). c3 the stub in C itself (no Python host) moves to P4.9, where the simulator
    has no Python; until then pssc's sim channel already carries a Python-hosted stub (P3.7).
    d pssc: `Session(engine="c")` and the daemon's debugger attach to the C engine (a DAP-level test). d done
    (the registry `pssc dbg`/DAP use picks `NativeStub` when the session's engine is `c`).
- [x] **P4.6 Generations in C.** P3.6 semantics. *Tests:* T4.6. *Size:* M.
  - *Split* (2026-11-19): a rt-eng: a Python host reloads a C engine (`pssvm_comp_write`, `Engine.construct`,
    `zuspec.rt.eng.generations.reload_engine`: be-bc `check_reload` + `migrate` on the models, N+1 a new engine
    constructed bus-isolated). a done. b pssc: `Session(engine="c").reload` (no tree kept between C tests: model
    swap + `reload` event; `pssc dbg reload` on a C debug session staged until its run ends, mocks re-imported;
    lift the refusal in sessions.py `Registry.reload`). c a reload inside a C debug run's set-up/test/tear-down
    boundary (the stub's engine replaced by `reload_engine` between calls) if b shows a need; else dropped.
    b done; c dropped (py stages a reload for the whole debug run too, so b already matches P3.6).
    The C engine reloading alone (`pssvm_reload`, host-abi.md §Reload, no Python host) moves to P4.9.
- [x] **P4.7 Coverage from the native engine.** The event stream feeds the
  daemon's covsight producer (no in-process `ucis_writer.h` until profiling
  shows a need). *Tests:* T4.7. *Size:* S.
  - *Done:* the coverage items are in the image (`SEC_COV`, `zbc_covent`); the C engine's `stmt` events carry
    `path`/`scope` and it raises `branch` events, so a `Coverage` sink counts a C run as a py one. `pssc test`
    still runs on py (it reaches C with P4.8/P4.9), so its reports say engine `pyvm`.
- [x] **P4.8 Conformance.**
  - *Change:* `--engine native`; a `native` construct-matrix target;
    differential bus traces against pyvm on the full pssunit suite of every
    example model.
  - *Tests:* T4.8, T-G8.
  - *Size:* M.
  - *Split* (2026-11-19): a `vm.Session(engine="c")` runs function tests (set-up/tear-down via `run_entry`)
    on a plain platform, counting pssunit's rewritten checks; what needs the live VM is refused up front
    (register fake, sanitizers, mocks, spies, interrupt delivery, action entries). b `pssc test --engine c`
    (runner `_session_for`; reports' engine label follows, cmd.py near `write_cdb`); a refused test is an
    error naming why; the C debug run (`_c_debug`) counts the pssunit ledger as `vm.Session` does. c the
    live-VM features on C (sanitizers, register fakes, mocks/spies, interrupts), each as its own part if
    large. d T-G8: every example's pssunit suite on both engines, same verdicts and bus traces, a test that
    fails on divergence. e a `native` construct-matrix target.
    a done. b done (also the register fake on C: rt-eng `run_sync` calls `note_access`).
    d split (2026-11-19): d1 C frames sized per function; the op_model function tests give the same
    verdicts and bus logs on both engines, guarded by a test (done). d2 action tests on C (bc_session
    `_run_entry_c` refuses them; to P4.10 if they need solve scopes; done: an action whose activation table
    is its root node alone runs on C, one with an activity is refused). d3 the other examples' suites (done:
    examples/fw_wb_dma_uvm has no pssunit suite, its UVM bench is P4.9; pssunit's examples/wb_dma, 2 tests, same
    verdicts on both). d done.
    e done: the `native` matrix target (trace_harness.py `_run_native`; inherits pyvm's xfails).
    c split: c1 sanitizers + spies (from the run's trace when it ends), c2 mocks (the C engine calls a
    bound host function in place of a PSS function), c3 interrupt delivery (a handler thread on C).
    c1 done. c2 done. c3 done: c done.
- [x] **P4.9 Swap behind `pssvm_pkg`.**
  - *Change:* the DPI binds directly to C, and no SV changes. A test needing
    Python mocks is refused up front with its location (O14).
  - *Tests:* T4.9.
  - *Docs:* D8.
  - *Size:* M.
  - P4.4d (2026-11-19): `pssc trace show` reads `.pstrace` by its magic (be-bc `binary.read`); events with
    only `src_ref` get `file`/`line` from the image (`--image FILE.zbc`, or header `image`). rt-eng
    `host.Engine.trace(out, mask, header)` writes a call's events; pssc `native.run_test(events=, watch=)`
    passes them to the session's sink after the run (import `name`/`mocked` filled host-side), so
    `Session(engine="c", events=TraceWriter)` traces; coverage on C still refused (P4.7). `pssc test` does
    not reach the C engine yet (PSSC_ENGINE is read by bc_session only).
  - *Split* (2026-11-19): pssvm_pkg.sv already has `pssvm_engine_dpi` under `PSSVM_DPI`, but the C side of its
    `pssvm_dpi_*` imports exists nowhere, and its open-array import (`input longint unsigned args[]`) is a
    Verilator 5.052 internal error (V3Task.cpp). a the DPI adapter in C (rt-eng `pssvm_dpi.c`: load from a
    path, request fields, args staged one at a time instead of an open array) and a Verilator test of the
    pssvm_shim TB on `pssvm_engine_dpi` giving the hif engine's output; pssc tells a build where the library
    is. b the bench on C: `+pss_impl=native` cells (`dfm run uvm.tests-native` 14/14, no Python in simv) and
    vm_cmp gen == native. c a test needing Python mocks/spies is refused up front with its location (O14).
    d the debug stub in C (P4.5c3: line JSON on the sim channel). e the C engine reloading alone
    (`pssvm_reload`, host-abi.md §Reload; instance paths in C). f docs D8.
    a2 (found by a, 2026-11-19): the image carries no import names the C engine reads (names ride only in the
    pickled `pssc.pyvm` META doc), so `req.name` is "" over DPI and a platform cannot dispatch imports by name;
    `pssvm_engine_dpi` has no `checks_failed`/test list/hooks (the runner needs them). Needed before b.
    Done: a, a2, b, c (2026-11-19).
    d split (2026-11-19): the native simv has no Python, so both the stub and the sim channel (serve.py:
    register, ping/info/end, `debug` on) must be C. The image carries coro names (CORO `name`), LOCAL
    (debug profile; not yet indexed by the C reader) and no component paths/field names (read_comp: `no_comp`).
    d1 the stub in C (rt-eng `pssvm_stub.c`, pthreads): line-JSON `request` -> reply, an event queue,
    `on_stop(call)` blocks the run thread until a resume, as rt-eng debug.py `NativeStub`; test: rt-eng
    tests/test_native_stub.py's cases replayed on it through ctypes. d2 pssvm_dpi.c's sim channel in C
    (connect to the daemon's socket from a plusarg/env, register, answer ping/info/end/debug, a reader thread
    forwards the rest to the d1 stub) and `pssc dbg` attaching to the pssvm_shim Verilator TB on the C engine.
    d3 the bench's native cell under the debugger, docs note.
    Done: d1, d2, d3 (2026-11-19).
    e split (2026-11-19): pyvm's sim reload (pssvm_dpi_shim.py `_on_reload` -> BcSession.reload) loads the
    image path the daemon sends (`reload {strict, cancel, image?}`) between tests on the SV thread, so the C
    sim channel can apply it at once in `handle()`. The C image has IDENT (api_hash) and COMP bases but no
    instance paths/types/field names; pssc's `pssc.native` META has `inst <path>` lines in base order.
    e1 `pssvm_reload(e, img, len, flags)` + `pssvm_generation` in C, in place (SV keeps its handle): api
    check, N+1 constructed beside N (construct-yield -> `bus`), N's slots copied when the layout is the
    same, any layout change refused `structure`; rt-eng host.py `Engine.reload`; tests rt-eng
    test_generations.py `-k c_reload`. e2 migration by instance path in C (paths from `pssc.native` inst
    lines, plus per-instance type + field names there, so `changed` is found; new parts start fresh;
    strict refuses). e3 the C sim channel answers `reload` (image path or the current one; reply
    old/new/changed/structure/migrated/image like the shim; refusal `refused` + reason; re-read tests,
    clear the stub's breakpoints) and a Verilator test like test_devenv_sim_channel.py
    `test_verilator_reload` on the DPI TB. e4 the bench: `pssc sim reload` on a held native cell, then rerun.
    Done: e1, e2, e3, e4 (2026-11-19). f done (2026-11-19): docs/simulation.rst "Running on the C engine". P4.9 done.
- [x] **P4.10 Activations in C.** (from P4.2e2/e3, deferred 2026-11-19; done 2026-11-19, a..f)
  - *Change:* an action with an activity (traversals, blocks, solve scopes and cones, resource-pool claims,
    a root in a non-root component instance) runs on the C engine as on pyvm. Today the image does not carry
    the activation table (`ZbcModel.activations` is in memory only, model.py), C refuses SCOPE_ENTER/SOLVE_NODE
    (zbc_engine.c, rt-eng `NATIVE_OP_GAPS`) and pssc refuses such actions (testing/native.py `_action_obj`).
    C already solves dv-solve blobs (zbc_solver.c, ZBC_OP_SOLVE).
  - *Tests:* the activation suites (tests/unit/integration test_scope_solve, test_lookahead, test_bc_cone_reuse,
    test_action_tree, test_generic_constraints, test_bc_flow_objects, test_bc_comp_attr_constraints) stay green
    on pyvm at each step; from c on, a py-vs-C comparison over their sources (same seed: same values, same trace).
  - *Size:* L.
  - *Split* (2026-11-19): a ACT image section in be-bc: the `SC.ScActionTree` the table is built from (nodes,
    scopes, sites, pools, cone vars, picks, state/buffer writes, init values) plus per-constraint metadata
    (kind/owner/scope/site/nodes), written and parsed back; pyvm runs from the parsed table (round-trip test).
    b per cone a vars-only base blob plus one blob per constraint, composed at run time with dv-solve
    `add_constraint` (b1: pyvm `ActivationTable.blob` + `SolveCache._compile`; b2: the blobs and one description
    per constraint in ACT, so a parsed table solves). A single blob with guarded constraints (`en_i -> c_i`,
    enables pinned) was rejected: dv-solve does not propagate through an OR whose other literal is false, so
    the resource-conflict test ex134 ran out of restarts instead of failing unsat. c C reads ACT; nodes, sites and blocks without cones: INVOKE into a node
    (enter/exit_node), SCOPE_ENTER commits, the root in a non-root instance (e3 NODE_INST); pssc lets such actions
    run on C. c split (2026-11-19): SCOPE_ENTER/SOLVE_NODE are emitted only when an activation has a cone, so
    a cone-free activity is plain bytecode C already runs; c1 such activities with no pool writes, all in the
    root instance, run on C (`ZbcModel.from_container` sets `activations` from ACT; pssc `_root_only` widened;
    py-vs-C test); c2 C parses ACT; c3 C does a node's state/buffer writes at its end; c4 non-root instances
    (NODE_INST, `comp_rel`). d SOLVE_NODE in C: pins, enables, witness reuse, readback, the unsat message. e lookahead
    footprints and pool claims (`_footprints`/`_probe`/`_busy`/`_claim`, picks). f matrix `native` xfails for
    activities removed, op_model suite on `--engine c`, docs; rt-eng `NATIVE_OP_GAPS` keeps PAR only.
    d split (2026-11-19; C has no activation run state yet, zbc_solver.c only compiles one blob and solves):
    d1 C activation state (committed/started/by_site/fired/entered, enter_node/_reset_node, SCOPE_ENTER resets)
    and SOLVE_NODE for cones without pools, picks, claims or a choice of instance: `_in_force`, `_pin` (rand,
    init values, comp_read, loop index, committed), base blob + `dvs_solver_add_constraint` per constraint in
    force, `dvs_solver_pin_var`, the LCG-then-full solve, readback of the node's vars, the unsat message, the
    SOLVE event; witness reuse (B6e) too, since it decides the values of every multi-node cone. d2 a choice of
    instance: the frame's comp from the solved `comp_slot`. d3 state pools: the root solve sets each pool's
    initial object, `live_from` pins, `exit_node` state writes. d4 buffer pools: `exit_node` buffer writes,
    picks (`_candidates` draws with Python's `random.Random(seed).randrange`: C must reproduce MT19937 and
    randrange), the pick unsat message.
  - *Done:* a (2026-11-19; pyvm still runs the in-memory table until b puts the cone blobs in ACT);
    b (2026-11-19; b1 pyvm composes the blobs, b2 ACT carries them: a model read from its image runs its
    activities on pyvm with the same values and unsat messages); c1 (2026-11-19; repeat, if, select, nested
    activities and unconstrained rand fields in the root instance run alike on C and pyvm); c2 (2026-11-19;
    zbc_reader.c parses and checks ACT, `zbc_image_act` + node/site/scope/write/list accessors); c3 (2026-11-19, re-scoped: a
    buffer output no action reads has no pool and no write, so it runs on C as is; every output another action
    can see, a state pool or a buffer with a consumer, comes with a cone (the root solve chooses a state pool's
    initial object; a consumer's solve picks), so the C node writes `exit_node` does move into d, where they can
    first be checked); c4 (2026-11-19; a node in the one instance of a sub-component runs there on C: frames
    carry activation, node and instance, a node INVOKE maps its site to the target node and adds the node's one
    candidate (`comp_rel`); no ACT field needed. A choice of instance (`comp_slot`) brings a cone: d); d1 (2026-11-19;
    cones over plain fields -- `with`, activity constraints, component-attribute pins, several nodes in one cone
    -- solve on C to pyvm's values; gaps kept: loop-index pins (lifted by f3), no SOLVE event, unsat without pyvm's text); d2
    (2026-11-19; a node whose instance the solve chooses (`comp_slot`) runs in that instance on C, and its
    children in it + their `comp_rel`, as pyvm's `_op_solve_node` / `VM.set_comp`); d3 (2026-11-19; state pools: the root
    solve copies each pool's initial object, `live_from` pins, `act_exit_node` state writes at `halt`; buffers still
    refused); d4 (2026-11-19; buffer pools: `act_exit_node` buffer writes into a per-activation outputs store, picks
    drawn as pyvm's `_candidates` with Python's MT19937 + `randrange` in C (zbc_rng.c), the picked object moved
    on; gap kept: a pick unsat has no pyvm message text). d done (d1..d4).
    e1 (2026-11-19; locks and shares claim on C as pyvm's `_busy`/`_pin`/`_claim`: claims recorded after the solve,
    released at the node's end and on a `first` cancel, busy masks per pool; resource pools up to 64 instances; a
    parallel two of whose own branches lock one pool is still refused, e2; gap kept: claim unsat without pyvm's text).
    e2 (2026-11-19; a parallel two of whose own branches lock one pool deals each branch a footprint of it as pyvm's
    `_footprints`/`_probe`/`_fp_mask`: branch table at activation start, a seed drawn at such a parallel's entry,
    claims kept in the footprint; gap kept: no footprint has no pyvm text). e done (e1, e2).
    f1 (opt-in C sweep of every integration run_model), f2 (scheduling, footprints, register count, comp read),
    f3 (loop-index pins), f4 (docs/test-command.rst "What the C engine runs"; act_sweep.py `GUARD` files swept
    by default, a C run that differs fails its test). f done; P4.10 done. Refused on C still: stream pools,
    resource pools over 64, nodes in no known instance; unsat solve/pick/claim lacks pyvm's text.

### 7.3 Phase 5: replay, time travel, fidelity, reach

Each item is independent, listed in rough order of value.

- [x] **P5.1 Record and replay platform.**
  - *Change:* `--platform replay:<trace>`, which reports the first divergent
    access. A sim run can be replayed in pyvm under the debugger (§12.5).
  - *Tests:* T5.1.
  - *Docs:* D6.
  - *Size:* M.
  - *Split:* a — `replay:<trace>` (NDJSON file or per-test directory) answers
    reads and user imports, FAIL kind `diverged` at the first divergent access
    or an early end (done); b1 — replay under `pssc dbg` and the pytest
    plugin (the session's test picks its trace), `--seed` defaults to the
    recorded one (done); b2 — recorded interrupts (`notify` events) raised again
    at their recorded step (done; channel events need nothing: the handler
    makes them); b3 — binary `.pstrace` (C engine) traces as input, read by their magic;
    in a directory a test's `.ndjson` before its `.pstrace`; D6 "Replay and time
    travel" in docs/debugging.rst (done).
- [x] **P5.2 Time travel over traces.**
  - *Change:* `pssc dbg start --trace`; `back`, `rc`, and "run to the last
    write of a field"; DAP `supportsStepBack`.
  - *Tests:* T5.2.
  - *Docs:* D6.
  - *Size:* L.
  - *Split:* no state snapshots: a Python-engine run is deterministic for a
    seed and a deterministic platform (fake, `replay:`), so going back means
    running again from the start to an earlier statement count. a — the
    be-bc debug stub counts statement starts (`tick` in each stop) and runs
    to a given count (`run_to`); b — `pssc dbg back` (the previous statement
    of the thread) and `rc` (two runs: note the user stops before now, then
    run to the last one), `start --trace <trace>` as `--platform
    replay:<trace>`; C engine and sim sessions refuse; c — DAP
    `supportsStepBack` (`stepBack`, `reverseContinue`), "last write of a
    field" as `rc` with a write watch, D6 docs.
- [x] **P5.3 `pssc trace why|slice|diff`.**
  - *Change:* dataflow from a register value back to the operation's inputs.
    This needs def-use events (operand `src_ref`s) in `stmt` events under
    `--trace all`.
  - *Tests:* T5.3.
  - *Docs:* D6.
  - *Size:* L.
- [x] **P5.4 `--engine gen-py` and `gen-sv` differential runs.**
  - *Change:* atomic tests are compiled with `--export-action` (with the
    check rewrite of P1.11 applied on the generator side), run through the
    same doubles, and their bus traces compared with pyvm.
  - *Tests:* T5.4 (calibrated).
  - *Docs:* D2.
  - *Size:* L.
  - *Progress:* [x] a export test actions; [x] b run generated Python on the
    fake; [x] c `--engine gen-py`; [x] d `--engine gen-sv`; [x] e docs.
- [x] **P5.5 Property shrinking and the seed corpus.** Hypothesis-style:
  re-solve with tightened bounds. *Tests:* T5.5. *Size:* M.
  - *Progress:* [x] a bounded re-solve in be-bc; [x] b shrink loop in the
    runner; [x] c docs. Python engine only; the corpus is the regressions file.
- [x] **P5.6 RDL and IP-XACT field semantics.** `--reg-semantics`, through
  `peakrdl-pss` where possible. *Tests:* T5.6. *Size:* M.
  - *Progress:* [x] a1 IP-XACT / JSON reader (`testing/reg_semantics.py`);
    [x] a2 `--reg-semantics` into the fake; [x] b SystemRDL (optional
    `systemrdl-compiler`; peakrdl-pss keeps `rclr` only in comments); [x] c docs.
- [x] **P5.7 Envelope coverage.** A covergroup per exported operation, with
  parameter bins, a result bin, and crosses on request. *Tests:* T5.7.
  *Docs:* D7. *Size:* M. [x] a `--cov` envelope; [x] b crosses + .cdb;
  [x] c C engine, docs.
- [x] **P5.8 Expectation mocks.** `expect_seq` over the bus log, opt-in.
  *Tests:* T5.8. *Size:* S. Done as `expect_write`/`expect_read` then
  `check_seq` / `check_seq_strict` (PSS has no statement-block argument).
- [-] (dropped: no simulator with save/restore; owner 2026-11-20) **P5.9 Simulator checkpoint restart.** `pssc sim restart --from CKPT`,
  documented per simulator (O7). *Tests:* T5.9. *Size:* M. Deferred: needs a
  simulator with save/restore under timing (Verilator refuses it; §11 log).
- [x] **P5.10 Doc examples.** A ```` ```pss-example ```` block in a doc
  comment runs as a test on the fake. *Tests:* T5.10. *Size:* M.
  - [x] P5.10a The blocks become tests in the run (testing/doctest.py; `.pssunit/doctest.pss`).
  - [x] P5.10b A failing example points at its doc-comment line; `--no-doctest`; C engine.
  - [x] P5.10c Docs section and CLI reference.
- [x] **P5.11 `--line-directives` on the generators.** `#line` and `` `line``.
  Goldens unchanged with the option off. *Tests:* T5.11. *Size:* M.
  - [x] P5.11a C and C++ (`#line` from `BodyWalker.stmt`; C++ own walker).
  - [x] P5.11b SystemVerilog (`` `line N "file" 0 ``), built by Verilator.
  - [x] P5.11c Python (`# file.pss:N` comments); docs and CLI reference.
- [x] **P5.12 Mutation testing.** `pssc test --mutate` reports surviving
  mutants: operator, constant, field and offset mutations. *Tests:* T5.12.
  *Size:* L.
  - [x] P5.12a Mutant generator (testing/mutate.py): PSS source edits, comments
    and strings skipped, model files only.
  - [x] P5.12b `--mutate`: green baseline, tests chosen by slice, one child
    `pssc test` per mutant with a timeout, `--jobs`, survivors listed, exit 1.
  - [x] P5.12c `--json` events, `--mutate-kinds`, `--mutate-max`; docs, CLI
    reference.
- [x] **P5.13 Agent skill.** `pssunit` in `agent-skills-pss`, with its CLI
  reference generated from `pssc help --json`. *Tests:* T5.13. *Docs:* D10.
  *Size:* S.
- [ ] ~~**P5.14 Tier 4 seam**~~: design only. No work is planned (design
  §7.5).

---

## 8. Test plan

### 8.1 Where tests live

| Kind | Location | Marker | Runs by default |
|---|---|---|---|
| be-bc unit and spec tests | `packages/zuspec-be-bc/tests/` | none | yes (its own suite) |
| rt-eng tests (ctypes) | `packages/zuspec-rt-eng/tests/` | none | yes (its own suite) |
| Runner, catalog, fake, checks, sanitizers | `tests/unit/test_pssunit_*.py` | none | yes |
| End-to-end over real PSS | `tests/pssunit/` (one directory per scenario) | `integration` | yes |
| Daemon, CLI, debug sessions, DAP | `tests/devenv/` | `integration` | yes |
| Latency budgets | `tests/perf/test_devenv_latency.py` | `perf` | no (`-m perf`) |
| Simulation (Verilator) | `tests/sim/pssvm/` | `sim` | no |
| Commercial simulator | bench repo CI | n/a | no |
| Construct matrix | `tests/progseq/matrix/` (pyvm, then native targets) | none | yes |
| VS Code | the extension repo, `@vscode/test-electron` | n/a | its CI |

pytest.ini gains `tests/devenv` in `testpaths`. `tests/pssunit` follows the
earlier work's placement.

### 8.2 Standing guards (run on every change)

| ID | Guard | How |
|---|---|---|
| T-G1 | Release bytecode is unchanged | Release-profile `.zbc` bytes for every compliance and matrix model, compared with a pre-change snapshot (held like the golden snapshots: regenerate only with an intentional change) |
| T-G2 | Latency | `pssc test --collect-only` < 50 ms warm; touch one operation file → affected test result < 300 ms; `pssc dbg step` < 50 ms. Medians over 20 runs (`perf`). **Measured 2026-11-18 (P1.18b, served, wall incl. ~15 ms client start, medians of 20):** collect-only 27-28 ms; op_model WB DMA (24 files) one-file edit -> result 244 ms with the op-model-c gate, 137 ms `--targets none`; no edit 29 ms; pssunit wb_dma edit 77 ms. `dbg step` not yet built |
| T-G3 | Hook overhead | `scripts/bench_bc.py` on the compliance set: hooks compiled in but disabled cost < 5 % over the baseline; all events on is reported, not gated |
| T-G4 | Located everything | One test per failure kind (check, require, sanitizer ×N, `error`, `fatal`, VMError, unimplemented import, gate refusal, parse error, discovery error). Each output has `file:line:col` that points at the right line. |
| T-G5 | Schemas | Real output of every `--json` command validates against `docs/schemas/*`. `pssc help --json` lists exactly the registered commands. |
| T-G6 | Thin client | Structural (`ast`) check that `_entry.py` and `devenv/client.py` import nothing from pssparser, ast2ir, be-bc or targets at module scope, plus a measured `python -X importtime` budget |
| T-G7 | Calibration | Each sanitizer and each differential comparison has a mutated input on which it must fire (the `lookahead=False` pattern) |
| T-G8 | Engine parity (from Phase 4) | pyvm vs native bus traces and verdicts on every pssunit suite in the repo |
| existing | Goldens, compliance on bc / op-model-py / op-model-sv, the matrix, doc-example tests | Unchanged. A pssunit change that moves a golden is a defect, except in P2.11d and P5.11, which state their changes. |

### 8.3 Per-item tests

Phase 0
- **T0.2:** the ported earlier tests pass unchanged except for R-5's
  exit-code update. Unit-suite failures are ≤ P0.1's.
- **T0.3:** `pssunit_pkg` copy equality (or a declared superset).

Phase 1
- **T1.1:** the prelude parses on every target. Each annotation is readable
  through `pssc.annotations.of`. Each check and fake import has a runner
  implementation (registry test: a new import without one fails).
- **T1.2:** Discovery.
  - Concrete vs abstract actions.
  - Inherited tests in a derived fixture get their own IDs.
  - Test functions.
  - `file:line` inside a body selects the test; a line outside selects
    nothing (exit 5).
  - Moving a file keeps IDs.
  - An unresolvable base is exit 2, located.
- **T1.3:** Operation entries.
  - For **every exported operation of WB DMA**, pyvm and generated
    op-model-py on a `MemoryBus` give identical bus traces (addr, width,
    data, order) for a fixed argument set. Calibrated: an offset mutation in
    the bc lowering makes it fail.
  - Test entries run their `super` chain in order.
- **T1.4:** Debug lowering.
  - Each PSS statement has exactly one marker (counted against ast2ir
    statements).
  - Frames are named.
  - The recursion form still works.
  - T-G1 holds.
- **T1.5:** an `error()` and a VMError (e.g. a divide by zero) report the
  right `file:line:col`. A debug image round-trips through write and read
  with PROV/LINE intact.
- **T1.6:** each event kind is emitted with schema-valid fields, and nothing
  is emitted with the mask off. Orchestration events are byte-identical to v1
  apart from the version header.
- **T1.7:** every `access` event in a WB DMA run decodes to the register the
  manifest gives for its address.
- **T1.8:** a bound function replaces the PSS body. A `mocked` event is
  emitted. The binding doesn't leak into the next test.
- **T1.9:** a `pv.Platform` passes `check_import_api` against the generated
  `<Root>ImportApi` (T1.9a). `@pv.mock`, `@pv.spy` and `@pv.implements` each
  work (T1.9b).
- **T1.10:** Fake semantics.
  - Reset values.
  - RO write: sanitizer.
  - W1C clears.
  - RC clears on read.
  - Back door has no side effect and no log entry.
  - An unimplemented import is a located FAIL.
  - Bus log decoding.
- **T1.11:** a failing `check` shows each operand's value. Each operand is
  evaluated exactly once (a counting import proves it). `require` stops the
  test. The card format is snapshot-tested.
- **T1.12:** Calibrated sanitizer tests.
  - `poll-budget` fires on a fake that never completes, at the polling loop,
    in < 1 s.
  - `unmapped` and `access-rule` fire on mutated offsets.
  - `allow` silences each.
- **T1.13:** each test gets a fresh tree. Property runs: N seeds, values vary
  and are reproducible from the printed seed. Failing seeds persist and
  re-run. `expect_error` passes only on the right text.
- **T1.14:** a construct the C gate refuses makes `pssc test` exit 2 with the
  gate's location. Test sources aren't gated.
- **T1.15:** `--platform py:mod` runs a Python device model. A missing module
  is exit 3.
- **T1.16:** each exit code. The CTRF and JUnit output validates. NDJSON
  validates. `-j` gives the same verdicts as `-j1`. Every failure has a repro
  command, and running it reproduces the failure.
- **T1.17:** `explain` of every WB DMA register address matches the manifest.
- **T1.18:** Daemon.
  - Auto-start, idle stop.
  - Edit → one rebuild; no edit → no rebuild (source-hash cache, S1).
  - Version-mismatch restart.
  - `PSSC_NO_DAEMON=1` gives byte-identical results.
  - Two workspaces get two daemons.
  - A crashed daemon is restarted, and the command succeeds.
- **T1.19:** pytest collects PSS tests only when `pssunit_paths` is set. The
  `pss` fixture calls an operation.
- **T1.20:** the matrix runs on pyvm. Each non-passing case is a strict xfail
  naming a defect.
- **T1.21:** the WB DMA suite passes on the fake in under 1 s. A seeded bug
  set (wrong offset, wrong field, missing completion) each fails at least one
  test.

Phase 2
- **T2.1:** stub conformance: a scripted sequence of stub messages against a
  small model gives the expected stop records (a reusable suite, which P4.5
  runs against native).
- **T2.2:** a breakpoint on each statement kind resolves. Conditional and
  hit-count breakpoints work. A register watch stops on write, read and
  value. `eval` of locals, attributes and fields works, and register `eval`
  never touches the bus (bus log unchanged).
- **T2.3:** a CLI session survives across client processes. Stop records are
  schema-valid. `--timeout` works. Concurrent sessions are independent.
- **T2.4:** a DAP transcript test (recorded requests → expected responses).
  CLI and DAP attached to one session both see each stop.
- **T2.5:** calibrated tests for `inflight`, `unconsumed-irq`, `rc-read` and
  `unbound`.
- **T2.6:** a hook fires on the field write. A typo in a register path is a
  located compile error. The same hook test passes on pyvm (and on native in
  Phase 4).
- **T2.7:** statement and branch counts match a hand count on small models.
  The boundary excludes `pssunit_pkg`, mocks and fixtures. IDs are stable
  across an edit that moves lines.
- **T2.8:** the database written for WB DMA reads back through covsight's
  reader. Test association answers "which tests cover this line". Merging
  two runs is additive.
- **T2.9:** `gaps --diff` lists only changed lines. LCOV validates with
  `lcov --summary` when available.
- **T2.10:** `--trace fail` keeps a trace only for failures. `show --at`
  maps an event index to its line.
- **T2.11:**
  - pssparser grammar tests (extension warning, abstract function);
  - `test_type_body_registry` row;
  - a template-method fixture runs on bc;
  - `test_m_action_functions.py` in the matrix on all four op-model targets
    plus pyvm;
  - an unimplemented abstract function is a located error;
  - goldens unchanged for models without action functions.
- **T2.12:** (a) the Test Explorer tree from the catalog; (b) a launch hits a
  breakpoint; (c) the shared-session check: attach to a CLI-started session
  and see the stop.
- **T2.13:** an edit to one operation re-runs only tests whose slice contains
  it. A cached result is reused when nothing changed.
- **T2.E:** the Phase 2 exit check. A script drives only `pssc` subprocess
  commands on WB DMA with `chk_sz` mutated. The watch stop's location is the
  register write the mutated line feeds (the next line), where the locals
  show the wrong `chk_sz`.

Phases 3 to 5 (one test per item, written when the item starts; the
headline ones are listed)
- **T3.1:** the yieldable and synchronous APIs give identical traces on the
  WB DMA suite.
- **T3.3:** under Verilator, a pssunit test runs through `pssvm_pkg` with a
  BFM platform. A runtime engine error is reported and the sim continues.
- **T3.4:** one UVM test source, compiled once, runs with `+pss_impl=vm` and
  `+pss_impl=gen`, with identical bus traces (the Phase 3 fidelity exit).
  Signature mismatch: the API-hash refusal message lists the changed
  signatures.
- **T3.6:** run in pyvm (no sim):
  - reload between operations;
  - carried channel tokens;
  - an in-flight operation finishes on the old generation;
  - a bus-touching constructor refuses the reload;
  - structural diffs reported, and refused under `--strict`;
  - a coverage merge across generations.

  Then the same under Verilator: a failing test, a fix, `pssc sim reload`,
  the same test passing, and the sim PID unchanged.
- **T3.8:** under Verilator, three tests that each count on a clean DUT
  pass in one sim run: the runner rebuilds the tree and calls the platform's
  `reset()` before every test but the first. A platform whose `can_reset()`
  is 0 runs only the first selected test and says why. A suite with a
  `@pssunit_pkg::session` function runs it once and shares the DUT (no
  `reset()`). On pyvm the session function runs before each test's set-up.
- **T3.9:** a dv-flow graph `std.FileSet(pssSource) -> pssc.Image -> pssc.Test`
  builds a `pssVmImage` fileset that holds the suite's tests and runs them in
  pyvm; a failing test fails `pssc.Test`. Rebuilt after an edit that leaves the
  image's bytes alone (a trailing comment, sources not in the image),
  `pssc.Image` reports its output unchanged; after a body edit, changed
  (`tests/dvflow/test_devenv_tasks.py`).
- **T3.10:** the fw-wb-dma UVM bench copy (`examples/fw_wb_dma_uvm/`), built
  once under Verilator, runs its universal tests (sw_copy, arb, arb_eq, err)
  with `+pss_impl=gen` and `+pss_impl=vm`: every test passes both ways, and
  the bus accesses the operation model issues are the same sequence both ways.
  With `+pss_impl=vm` and an image whose API differs, the build's `create`
  refuses with the API-hash message.
- **T3.7:** a breakpoint in a sim session stops sim time (`$time` unchanged
  across the stop).
- **T4.x:** the T2.1 stub suite, T3.1 trace parity and T3.6 reload tests
  rerun against native.
- **T5.3:** on a debug-image test that computes a register value from a
  parameter through a local and a function call, `pssc trace why <trace>
  REG.field --json` names the write access, its statement, and a chain ending
  at the parameter; `trace slice --reg REG` lists every access to REG with its
  writer statement; `trace diff` of two runs names the first divergent access
  (step N, both sides); a C-engine trace gives the access with no chain
  (`tests/unit/test_trace_why.py`).
- **T5.4:** gen-py and gen-sv differential on every atomic test of
  examples/op_model/tests (pass, no divergence; compound tests SKIP with the
  reason). Calibrated: a generator mutation (register offset +4) and, for SV,
  one served read fewer are each caught as a divergence naming the access
  (`tests/unit/test_gen_py.py`, `tests/unit/test_gen_sv.py`).
- **T5.5:** be-bc re-solves a test's rand fields within given bounds and
  reports the values chosen, in a cone and outside one
  (`tests/unit/integration/test_bc_solve_bounds.py`); a property test that
  fails for `x >= 128` reports `shrunk: x=128` with its failing seed, and a
  passing one reports nothing shrunk (`tests/unit/test_pssunit_isolation.py`).
- **T5.6:** an IP-XACT file, a SystemRDL file (skipped without
  `systemrdl-compiler`) and a JSON table give the expected field kinds; the
  bench's `wb_dma_ch_regs.rdl` gives `int_err`/`int_done`/`int_chk_done` `RC`;
  `pssc test --reg-semantics` makes the fake clear an RC field on read while a
  `field_access` annotation still wins; a bad file exits 5
  (`tests/unit/test_reg_semantics.py`).
- **T5.7:** each operation under test gets bins from its parameter and result
  types; only the outermost call counts; `cover_cross` adds crosses (an unknown
  name drops one); reports merge; the envelope round-trips through a `.cdb`;
  `--engine c` gives the Python engine's envelope (`tests/unit/test_cov_envelope.py`).
- **T5.8:** queued `expect_write`/`expect_read` pass `check_seq` in order with
  other accesses between; `check_seq_strict` fails on an extra access; an
  out-of-order or missing access fails, its card naming it; expectations never
  checked fail at the test's end; matching restarts after each `check_seq`;
  the same on `--engine c` (`tests/unit/test_pssunit_expect_seq.py`).
- **T5.12:** on a copy of `examples/op_model`, the mutant set contains the
  seeded offset and field bugs of T1.21 and `--mutate` kills them; a mutant
  that hangs a poll is killed by the timeout; with only `regmap_a.pss` as the
  tests, survivors are listed with file:line and the run exits 1
  (`tests/unit/test_mutate.py`).

### 8.4 Calibration and seeded bugs

Keep a small library of model mutations in `tests/pssunit/mutants/`:
- offset +4;
- wrong field;
- missing `inflight` release;
- a never-completing poll;
- `chk_sz` wrong;
- RO write.

Every detector (sanitizers, differential runs, T1.21, T2.E) names the
mutants it must catch. The same library later seeds P5.12.

---

## 9. Documentation plan

**Where docs live.**
- User docs are in pssc's `docs/` (Markdown or rst, as the existing tree
  mixes).
- Normative framework docs are in the pssunit repo (`docs/writing-tests.rst`
  exists).
- Engine specs are in be-bc's `docs/spec/`.
- Every code block in a user doc is **quoted verbatim** from a file the
  suite runs, and is checked by `tests/unit/test_doc_examples.py`'s
  mechanism, extended to the new pages. A shown `--flag` must be a real
  option.

| ID | Doc | Content | Phase |
|---|---|---|---|
| D0 | Ported earlier docs | `test-command.rst`, `ctrf-report.rst`, `annotations.rst`, `test-internals.rst`, updated for R-1…R-5 | 0 |
| D1 | **Tutorial: your first operation test** | WB DMA from nothing to a passing test, a failing card, a fix, in five minutes. Quotes `examples/op_model/tests/`. | 1 |
| D2 | **Writing tests** (reference) | Fixtures, test actions, inheritance and `super;`, test functions, property tests, checks, sanitizers and `allow`, the gate, IDs and selection | 1 (+2, 5) |
| D3 | **Platform doubles** | Tier 0 fake and field semantics, Python host API (`pssc.testing.vm`) reference, Tier 1 hooks (P2), binding rules (R-3), pytest | 1, 2 |
| D4 | **CLI reference** | **Generated** from `pssc help --json`, with a test that the checked-in page matches | 1+ |
| D5 | **JSON schemas** | events, catalog, stop record, cov gaps; versioning rules | 1+ |
| D6 | **Debugging** | CLI sessions, VS Code, register watches, traces, replay, time travel | 2, 5 |
| D7 | **Coverage** | Layers, boundary, IDs, covsight mapping (the §11.3 table as reference), views, merging | 2, 5 |
| D8 | **Simulation integration** | `pssvm_pkg`, `op-model-sv-vm`, `+pss_impl`, server mode, pyhdl-if set-up, the native engine, dv-flow tasks, debugging in a sim, per-simulator notes (O7) | 3, 4 |
| D9 | **Hot reload** | What reloads, reload points, migration, refusals, `--strict`, coverage across generations | 3 |
| D10 | **For agents** | The command rules (§14.2), loops (§14.4) as runnable transcripts, and the skill (P5.13) | 1, 2, 5 |
| D11 | **be-bc specs** | a: entries, debug lowering, image profile, register ids, host binding; b: trace schema v2 and `.pstrace`; c: debug stub; d: host ABI; e: generations | 1–4 |
| D12 | **Action functions** (extension) | Semantics, portability warning, a note for the PSS WG (O1) | 2 |
| D13 | **Coming from C** | The design §16 comparison, each row linked to the doc that shows it | 2 |
| D14 | **AGENTS.md** | One paragraph per load-bearing invariant as it lands: release bytecode unchanged; one event stream; located everything; thin client; doubles bound by the run; calibrated detectors | as landed |
| D15 | **Design doc upkeep** | Apply R-1/R-2/R-3 to the design's examples and mark sections implemented. Record deviations in the design, not only here. | as landed |
| D16 | **Release notes** | One entry per phase | per phase |

Doc tests to add:
- **T-D1:** D1, D2 and D3 code blocks are verbatim from tested sources.
- **T-D2:** the D4 page matches `pssc help --json`.
- **T-D3:** D5's schemas are the ones T-G5 validates against.
- **T-D4:** D10's transcripts run (as T2.E does) with outputs matched by
  schema, not bytes.

---

## 10. Sequencing, dependencies and spikes

```
P0 ─► P1.1,P1.2 ─────────────────────────────► P1.16 ─► P1.21 (exit 1)
  └─► P1.3 ─► P1.4 ─► P1.5,P1.6,P1.7,P1.8 ─► P1.9..P1.15 ┘
                         P1.18 daemon (after S1) ─────┘   P1.20 matrix
P1.6 ─► P2.1 ─► P2.2 ─► P2.3, P2.4 ─► P2.12 ─► (exit 2)
P1.6 ─► P2.7 ─► P2.8 ─► P2.9        P2.11 (independent; parser first)
P1.x ─► P3.1 ─► S4 ─► P3.3 ─► P3.4 ─► P3.5 ─► P3.7 ─► P3.10 (exit 3)
          P3.6 (pyvm part needs only P3.1)
P3.1 spec ─► P4.* (can start in parallel with P3.3)
P5.* after the item each depends on (P5.1 after P1.6, P5.4 after P1.11)
```

Parallel tracks once Phase 1's bc work (P1.3–P1.8) is in:
- (a) runner, doubles, daemon;
- (b) debug;
- (c) coverage;
- (d) action functions, any time;
- (e) native engine bring-up, from P3.1's spec on.

| Spike | Question | Before | Fallback |
|---|---|---|---|
| S1 | Can pssparser re-link with cached per-file parses, and how long does a one-file edit take to re-parse, link, translate, gate and lower for WB DMA? | P1.18 | Cache downstream of the parser (translate and lower per changed component); relax T-G2 to a measured number recorded here | **Done 2026-11-18: no re-link; fallback taken, T-G2 kept.** `link()` links only the units parsed since the last link (the root owns earlier ones), so one changed file re-parsed alone fails with "unknown type". Whole rebuild, op_model WB DMA (24 files, 1743 lines), warm process, medians: parse 18 ms, link 2.5, translate 51, lower (debug, all fns) 20 = ~92 ms; the op-model-c gate 187 ms (two more translates + build_model 8 + check 17), so cache its verdict on device-file hashes; imports 157 ms (kept by the daemon). pssunit examples/wb_dma: collect 20 ms + run 2 ms warm |
| S2 | Parse a standalone PSS expression against a linked scope (for `eval`, conditional breakpoints) | P2.2 | A small expression parser in pssc over linked symbols  **Done 2026-11-18: no expression entry point; neither fallback needed.** pssparser parses only whole units and links once, but a synthetic unit appended after the sources (`extend action P::A { exec post_solve { <locals> int __pssc_dbg = (EXPR); } }`) goes through the normal translate: pssc's type checks report errors at `<dbg>` columns, ast2ir yields a typed IR expr (`ExprRefLocal` for the frame's locals, redeclared from the LOCAL table; `ExprAttribute` on self/`comp`). WB DMA: ~70 ms per expression, warm. **P2.2e used the small parser after all** (the wrapper needs struct locals' type names, which the LOCAL table lacks; §11 log) |
| S3 | covsight-core Python API: history nodes, test ↔ coveritem association, STMTBIN/BRANCHBIN writes | P2.8 | Add them to covsight-core (cross-repo, owner needed)  **Done 2026-11-18: yes, all present** (`MemFactory.create`, `createHistoryNode(..., TEST)` + `TestData`, `record_test_association(hist_idx, bin_idx, n)`, STMTBIN/BRANCHBIN + `IS_BR_ELSE`, covergroups, `NcdbWriter`/`NcdbMerger`); no fallback needed |
| S4 | pyvm under pyhdl-if in Verilator: does it work, and what does an access cost? | P3.3 | Go to the native engine first for the sim (move P4.1–P4.3 and P4.9 ahead of P3.3) |
| S5 | Statement markers: an opcode or a LINE-table flag, keeping release images identical | P1.4 | Either works; the spec records the choice. **Done 2026-11-18: the LINE table.** Debug lowering writes one `zbc_lineent` per PSS statement (DWARF `is_stmt` style); no `STMT` opcode. Release lowering and the runtime profile are unchanged |

---

## 11. Status and progress log

| Phase | State |
|---|---|
| Decisions R-1…R-7 | accepted as recommended (owner hand-off, 2026-11-18) |
| P0 | done (exit met: `pssc test` runs pssunit's examples/wb_dma, 2 pass) |
| P1 | in progress: P1.1, P1.2, P1.3 (a-d), S5, P1.4, P1.5, P1.6, P1.7, P1.8, P1.9, P1.10, P1.11, P1.12, P1.13, P1.14, P1.15, P1.16, P1.17, S1, P1.18, P1.19, P1.20, P1.21 done; exit gate checked 2026-11-18: T-G4 closed (E1), D2 closed (E2), D3 closed (E3), D4 + T-D2 closed (E4); Phase 1 gaps closed |
| P2 | done 2026-11-18: P2.1–P2.13 (P2.5d moved into P2.6b); exit met (T2.E tests/unit/test_wb_dma_t2e.py, T2.12c by the extension's e2e, WB DMA `.cdb` in covsight by T2.8). Per-item detail: the log below |
| P3 | in progress: owner answers 2026-11-18: commercial-sim rerun deferred (Verilator only), pyhdl-if installed from the local clone at P3.3a, fw-wb-dma bench copied into examples/fw_wb_dma_uvm/ |
| P4 | in progress: P4.1–P4.4 done (2026-11-19); P4.5 split a..d, a and b (b1, b2) done, c split c1..c3 (c3 -> P4.9), c1 and c2 done, d done: P4.5 done; P4.6 split a..c, a and b done, c dropped: P4.6 done; P4.7 done; P4.8 a..e done: P4.8 done; P4.9 split a..f (+a2), all done: P4.9 done (2026-11-19); P4.10 split a..f |
| P5 | not started |

Baseline (P0.1), 2026-11-18, HEAD 6dfac17 plus the two untracked design docs.
Run without direnv (not approved here) as
`PATH=packages/{python,verilator,iverilog,xezim,node}/bin:$PATH PYTHONPATH=./src python -m pytest ...`;
the system verilator (5.040) on the default PATH fails ~24 more progseq tests, so
the vendored one (5.055) must come first. `z3-solver` (declared by
`pss-corpus/checker`) was missing from `packages/python` and was installed with
`uv pip install --python packages/python/bin/python z3-solver`; without it 545
`test_compliance_bc` cases fail.

| Suite | Passed | Failed | Skipped | xfail | Known failures |
|---|---|---|---|---|---|
| `tests/unit` | 2866 | 1 | 8 | 4 | `test_ir_walk::test_every_base_field_is_either_a_child_or_listed`: `zuspec-ir-core` `Base` gained an `anchor` field that `ir_walk.NON_CHILD_FIELDS` lacks (dependency drift) |
| `tests/progseq` | 3117 | 10 | 7 | 114 | 5 `test_golden_op_models[c-*]` and `test_c_seam::test_the_header_names_the_generator_version` (header reads `pssc <VERSION> version)`: version string from the uninstalled tree); `test_op_model_order` ×2; `test_emitter_kind_registry::test_every_ir_kind_has_a_row`; `matrix/test_m_channels::test_construct[sv-channels of a struct, an enum and a bool]` |
| `tests/compliance` | 925 | 0 | 5 | 59 | none |
| be-bc (`packages/zuspec-be-bc`) | 250 | 0 | 0 | 0 | none |
| rt-eng (`packages/zuspec-rt-eng`) | 2 | 0 | 0 | 0 | 113 errors: the test `.so` has an undefined symbol `dvs_solver_solve` (dv-solve not linked/built) |

The unit failure `test_doc_references::test_the_archived_notes_say_they_are_archived`
was caused by this plan and the design being in `docs/design/` without the
archive banner; both are now on its list of live docs.

Log (newest first):
- 2026-11-18: P1.8 done. be-bc: `VM.bind(qname, fn)` (fn None unbinds) maps
  each called form whose `fn_qname` is `qname` and that some `CALL` targets to
  a host callable (`VM.bindings`, coro index -> (qname, fn); per VM, so per
  run). `ops_orch._op_call` -> `_call_bound`: the callable gets the raw
  argument values, its result (None: 0) goes to the call's register; with
  `EV_CALL` the caller emits `call` `{fn, depth, mocked: true, args}` and
  `return` `{..., mocked: true, retval}`. A release lowering inlines calls, so
  `bind` raises KeyError ("bindings need a debug lowering"). pssc
  `bc_session.run(..., bindings=)`, `Session.bind`, `Session.run_test(...,
  bindings=)` (cleared by the next construct). T1.8: tests/unit/test_bc_bind.py
  (5); be-bc test_events.py bound-call test (be-bc 289); trace-schema.md
  `call`/`return` row. Release bytes unchanged. Unit 3165 + 1 baseline fail.
  Gap: struct-parameter functions are inlined even in debug, so cannot be bound.
  Next: P1.9.
- 2026-11-18: P1.10d done (P1.10 complete). fake.py: `pv.on_write(reg,
  field=None)` / `pv.on_read(reg)` mark `RegisterFake` subclass methods
  `fn(self, ctx, acc)`; `reg` is an instance path, the path without
  indices, or `<group type>.<reg>` (plain or package-qualified type, from
  `model.components.types`); a name matching no register (or an unknown
  `field`) fails at `attach`. on_write runs after the store (with `field`:
  only if the written field is non-zero); on_read runs before the value is
  taken. `pv.Access`: op width addr data reg group index fields src old new.
  `Session` sets `fake.ctx`. Back door runs no hook. Unimplemented import:
  already a VMError stamped by the dispatch loop; T1.10d asserts its
  `location` is the call's line. Tests: test_pssunit_fake.py (12),
  test_pssunit_vm.py. Unit 3187 + 1 baseline fail; pssunit 67; wb_dma 2.
- 2026-11-18: P1.10c done. fake.py: `RegisterFake.bus`, one dict per
  access beside `log`'s tuples (same length): op, width, addr, data, `reg`
  (path of the instance accessed, not its alias's cell; None for memory),
  `fields` (name -> value), `src` (`file:line` from the noted src_ref via
  `model.prov`, `where()`; None for a plain host access). Back door, no
  log entry: `peek_field`/`poke_field` (any kind), `check_reg(path,
  value=None, **fields)` (AssertionError naming each mismatch),
  `mem_peek/mem_poke(addr, [value,] width=32)` little-endian bytes. T1.10c:
  2 tests (inline PSS). Unit 3185 + 1 baseline fail.
- 2026-11-18: P1.10b done. `bc_session._registers` gives each field a
  `kind` (`FIELD_KINDS`: RO WO RW RC W1C W1S VOLATILE; default from the
  register's access) from `@pssunit_pkg::field_access` (`_field_access`):
  register type, then each enclosing group's type and instance (outermost
  first; `reg` = path from the group, with or without indices), then the
  register instance; later wins. Unknown field/kind: ValueError. ast2ir:
  a named `reg_c` component keeps its annotations (were dropped).
  fake.py: per-cell kind masks (`kinds`, `_kind_masks`, `_written`;
  aliases `_merged`); RC cleared by a read (written as RW), W1C/W1S, RO
  bits kept, WO read 0, VOLATILE stored as RW. T1.10b: 2 tests (inline
  PSS). Unit 3183 + 1 baseline fail. Note: `@pssunit::x` (wrong package
  name) is silently accepted by the front end, no error.
- 2026-11-18: P1.10a done. `src/pssc/testing/fake.py`: `RegisterFake(Platform)`
  (also `pv.RegisterFake`). `vm.Session` calls `platform.attach(model)` and,
  for a platform with `note_access`, ORs EV_ACCESS onto the VM mask (the
  user's sink still sees only its `watch`). be-bc `ops_orch._note_access`:
  with EV_ACCESS on, `vm.memory.note_access(reg_id, src_ref)` just before a
  read/write (extern.py `Memory` doc). The fake keeps a value per reg-id;
  one noted access gives its component's register base (`bases`), placing
  all that component's registers (`reg_at`); two instances at one address
  (`ch[i].regs` = `regs.bank[i]`) merge (`alias`), the first placed keeps
  its value. Gap: a component's base is known only after an access to one
  of its registers (`initialize` makes none). `peek_reg`/`poke_reg` by
  path. T1.10a: 6 tests. Unit 3181 + 1 baseline fail; be-bc 289.
- 2026-11-18: P1.9b done. `vm.py`: `Registry` (decorator methods);
  module-level `@pv.implements(name)`, `@pv.mock(qname)`, `@pv.spy(qname)`
  file the function under its `__module__` (so `@pv.spy(..) def _` names
  can be reused); `Session.use(registry | module | module name)` and
  `load(..., use=[...])` apply them; mocks and spy watches are re-applied
  after every `construct`. implements: `_Imports` calls `fn(ctx, *args)`
  before the platform method, `is_mocked` True. mock: `VM.bind` with
  `fn(ctx, None, *args)` -- `self_` is None (a bound CALL does not see the
  callee instance; open). spy: VM.bind cannot call through (synchronous),
  so the session's events sink is a `_Tee`: EV_CALL is ORed onto the spied
  coros, `call`/`return` pairs become `pv.Call(qname, args, result, mocked)`
  (`session.calls`, then each spy fn), and the user's sink gets only the
  kinds in the user's `watch`. be-bc: a real `call` event now carries
  `args` (vm.py `_run_frame_ev`; test_events.py + trace-schema.md updated).
  `ctx.time`: `now`, `advance(ticks)`, `advance_ns/us` on `vm.sched.now`
  (`session.ticks_per_us` = 1000, a guess until PSS time units are
  lowered). `ctx.raise_irq(n)`: `session.irqs`, a host `notify` event
  `{irq}`, and `platform.raise_irq(n)` if it has one. T1.9b: 3 more tests
  (6). Unit 3173 + 1 baseline fail; be-bc 289. Next: P1.10.
- 2026-11-18: P1.9a done. `src/pssc/testing/vm.py`: `Platform` (sparse
  little-endian memory behind `read8..64`/`write8..64`, `log` in
  `pssc_rt.MemoryBus.log` shape, `messages`); `Session(model, platform=)`
  (a `bc_session.Session` whose VM gets `_Memory` -> `read<w>`/`write<w>` and
  `_Imports` -> the platform method named as the import; a missing one is a
  VMError naming it); `load(files, root, platform=)` (translate with
  pssunit_pkg + preludes, debug lowering, every function an entry).
  `bc_session.Session(memory=, imports=)` added. T1.9a:
  tests/unit/test_pssunit_vm.py (3): a `Platform` subclass passes
  `check_import_api` against op-model-py's `BoardImportApi`; a run reaches
  the platform's imports and memory; a missing import is named. Note: the
  product model is compiled for op-model-py WITHOUT pssunit_pkg, whose own
  imports (`check_eq`, `mem_peek`, ...) would otherwise join the Protocol.
  Unit 3170 + 1 baseline fail. Next: P1.9b.
- 2026-11-18: P1.7 done. be-bc: debug lowering `_reg_mark` (procedural.py
  `_reg_call`) records `(IMPORT pc, register instance from the frame's)` in
  `CoroDescriptor.regs` (in memory; release image unchanged, T-G1 passes); VM
  `watch` builds `_ev_regs`, `VM.reg_id(frame)` = frame.comp + rel, and
  `ops_orch._access` adds `reg_id` to `access` events. pssc `bc_session`:
  `lower(debug=True)` sets `model.registers` (reg-id -> path, owning component,
  offset, width, access, fields) from `reg_layout.collect_accessors` of the
  nearest non-register component; `decode_access(model, detail)` adds `reg` and
  `fields`. T1.7: tests/unit/test_bc_reg_access.py (4) on examples/op_model WB
  DMA -- every register access is at the manifest's address. Unit 3159 + 1
  baseline fail. Gap: the platform (memory) does not receive reg_id yet (P3).
  Next: P1.8.
- 2026-11-18: P1.6c done; P1.6 closed. be-bc `trace/events.py`: `EV_CHAN`,
  `EV_NOTIFY`, `EV_SANITIZER`, `EV_HOST` (the host kinds: test, notify, sanitizer;
  `VM.host_event(kind, detail, src_ref)` tests each kind's bit). `chan`: debug
  lowering ends each channel op (`_channel_call`) with a NOP of its own and records
  `(pc, src_ref, channel, op, value reg, ok reg)` in `CoroDescriptor.chans` (in memory);
  the VM raises `{channel, op, value}` (+ `ok`) there. `notify` and `sanitizer` are
  host-raised (no platform notify or sanitizer exists yet: P1.10+, P3). Trace schema
  version 2 (debug kinds; orchestration records unchanged; no golden file held the
  header); be-bc docs/spec/trace-schema.md has the event table (D11b; `.pstrace`, time
  and register path not yet). Tests: be-bc test_events.py +3 (288);
  test_bc_debug_lowering.py +3 (chan, release has no markers, host kinds). Unit 3154 +
  1 baseline fail; T-G1 passes. Gap: `channel` is the name as written, not the
  instance path. Next: P1.7.
- 2026-11-18: P1.6b done. be-bc `trace/events.py`: `EV_ACCESS`, `EV_IMPORT`,
  `EV_MESSAGE`, `EV_CHECK` (`EV_AT_IMPORT`); `ops_orch._op_import` reads the frame's
  mask once (`_ev_mask`, one dict test when off) and raises `access` (memory builtins:
  rw, width, addr, data), `import` (name via `VM.import_name`, args, ret, blocking,
  `mocked` = provider's `is_mocked(fn_id)`, False without one; import doubles are not
  built yet) and `message` (info with level; error/fatal before the run stops).
  `check`: `VM.on_check(coro, hook)`, raised at a watched called frame's entry;
  `CoroDescriptor.fn_qname` (in memory, debug called forms) names the function.
  pssc `bc_session.watch_checks`: the native ledger's `rt_c::record`/`record_skip`
  → `{verdict, what, check}` at the test's `rt.check_*` call site. Not yet: register
  path/decoded fields and platform time on `access`; pssunit import checks
  (`check_eq` imports) as `check` events. be-bc 285 (+6); unit 3151 + 1 baseline fail;
  orch trace unchanged (test). Next: P1.6c.
- 2026-11-18: P1.6a done. be-bc: debug events go to a separate `events` sink
  on the VM (`VM(events=...)`), never the trace `sink`, so orchestration
  traces are byte-identical with events on (tested). `trace/events.py`:
  masks `EV_STMT`, `EV_CALL` (call+return), `EV_TEST`, `mask_of(names)`.
  `VM.watch(mask, coros=None)` sets a per-coroutine mask (dict keyed by
  coroutine); `run_frame` tests `self._ev` once per frame entry/resume and
  hands a watched frame to `_run_frame_ev` (same loop + a pc->statement test
  per instruction; `call` on a called frame's entry at pc 0, `return` before
  `_complete`; retval when an int). `VM.host_event` for `test.start/end`.
  `CallbackSink` added; `EventKind.DEBUG` admits the new kinds (schema
  version stays 1 until P1.6c). `oracle.run_model(events=, watch=)`. pssc:
  `bc_session.run(events=, watch=, name=)` and `Session(events=, watch=)`
  emit `test.start/end` (run_test adds the ledger counts). Not wired to the
  CLI yet. T-G3: measured, not a CI test (timing flakes): a 2-instruction
  frame, 50k runs, best of 7, 1.36/1.38 us HEAD vs 1.39/1.40 us now (~2%,
  the worst case: one dict test per frame entry); threshold 5% is a guess.
  Tests: be-bc `tests/unit/interp/test_events.py` (8; be-bc 279); pssc
  `test_bc_debug_lowering.py` +3 (debug call/stmt lines, test events). unit
  3149 pass + 1 baseline fail; pssunit 67; wb_dma 2 pass.
- 2026-11-18: P1.5c done (P1.5 closed). be-bc `IDENT` section (kind 19,
  `zbc_ident` {api_hash u64, generation u64}), written by the debug profile
  only and only when a field is set, so release bytes are unchanged (T-G1
  holds). `model.api_hash_of(entries, imports)`: FNV-1a/64 over canonical JSON
  of entry symbol/kind/comp type/signature and import signatures, sorted by
  name, coroutine indices left out. `lower_scenario` sets `ZbcModel.imports`
  and `api_hash`; `generation` is the caller's (pssc `bc_session.lower(...,
  generation=)`, default 0). Oracle round-trip checks both. zbc-format.md,
  rt-core header regenerated. Tests: be-bc `test_debug_profile.py` (+3, be-bc
  271); pssc `test_located_errors.py` (+2, 13). P1.6 split into a/b/c.
- 2026-11-18: P1.5b done. be-bc profile "debug" = codegen + header flag
  `ZBC_HDR_PROFILE_DEBUG` (0x0008) + optional `SRC` section (kind 18,
  `zbc_srcent` {path, text} StrIds, sorted by path; provenance family, so a
  runtime image refuses it); `ZbcModel.sources`; reader/inspect report the
  profile; zbc-format.md + rt-core `zbc_format.h` regenerated. pssc
  `bc_session.lower(debug=True)` makes a debug image and fills `sources` from
  the files provenance names. Release (codegen) bytes unchanged (T-G1 holds).
  T1.5 part b: `tests/unit/test_located_errors.py` (11), be-bc
  `tests/unit/format/test_debug_profile.py` (6).
- 2026-11-18: P1.5a done. be-bc `VMError` carries `src_ref` + `prov` and a
  `location` (`file:line:col`); `VM.run_frame` stamps it once, at the innermost
  frame, from the failing instruction's `src_ref` (else its coroutine's). std_pkg
  `error(fmt, ...)` / `fatal(status, fmt, ...)` did not lower in bc before: now
  IMPORTs of new builtins `BUILTIN_ERROR_MSG`/`BUILTIN_FATAL_MSG` (BASE+10/11)
  over a `message()` table entry (first slot = status, 0 for error), raising
  `VMError("error: ...")` / `"fatal (status N): ..."`. pssc `run_one._fail`
  prefixes the location to `detail`. Works in release and debug (instructions
  carry `src_ref` in both). T1.5 part a: `tests/unit/test_located_errors.py` (7).
  T-G1 holds (no release image changes). Not done: the C runtime (rt-core) and
  emitted C know nothing of the two new builtins.
- 2026-11-18: P1.4e done (P1.4 closed). T-G1 = `tests/unit/test_release_zbc.py`
  (marked slow + golden, ~13 s) over `tests/unit/data/release_zbc_sha256.json`,
  written by `tests/unit/release_zbc.py --write`: 596 models (113 compliance, 483
  matrix cases, root `pss_top`), release lowering, sha256 of `to_bytes()` or
  `error:<type>`. Checked against be-bc + ir-core at HEAD (git archive): section by
  section, every image that lowered there is identical except the new ENTRY section
  (kind 16, P1.3d2); 9 matrix models (registers) lower only now. Matrix models are
  read through a relative path (`m.pss`), since an image may carry its file name.
  Only 117 of 596 lower in release bc (most matrix cases are op-model-only).
- 2026-11-18: P1.4d done. `pssc test` lowers with `debug=True` by default
  (`runner.run(debug=...)` -> `_build_image` -> `bc_session.lower`); `--no-debug`
  lowers the release image (docs/test-command.rst, docs/cli.md; debug paragraph
  in docs/test-internals.rst = D11a). T1.4 in `tests/unit/test_bc_debug_lowering.py`
  (25 pass): markers counted against the ast2ir statement tree (entry and called
  forms), frames named `<fn>$callN`, recursion (fact, fib, mutual) same in debug
  and release. T-G1 (release `.zbc` bytes over compliance + matrix models vs a
  pre-change snapshot) split out as **P1.4e**: the snapshot must come from be-bc
  at its HEAD, which the uncommitted P1.4a-c edits have moved past.
- 2026-11-18: P1.4c done. Named locals are a new provenance-family `LOCAL`
  section (kind 17, `zbc_localent` 24 bytes: coro, slot, name StrId, global
  `[pc_start, pc_end)`, width, `zbc_local_kind`, `ZBC_LOCAL_F_SIGNED`); spec
  in be-bc zbc-format.md "Named locals (`LOCAL`)"; rt-core `zbc_format.h`
  regenerated. be-bc `model.LocalVar`, `CoroDescriptor.locals`;
  lowering records each `_declare`d variable (a struct per leaf, `cfg.src`) in
  `CoroCtx.named_locals` and closes it in `_close_scope` (`_pop_scope`, and
  `_inline`'s frame); `CoroCtx.debug_locals()` at the three coroutine
  finalizers. Temporaries are not named. 3 new tests (16 pass). Forced-debug
  probe: unit fails only release-assuming tests + baseline. be-bc 258,
  rt-core 38, pssunit 67, unit 1 baseline fail.
- 2026-11-18: P1.4b done. In debug, `_inline` (be-bc `lower/procedural.py`)
  turns a call into a `CALL` of the function's called form (`<fn>$callN`, one
  per function and instance, with its own LINE markers) when
  `_has_called_form` holds: scalar parameters (at most `CALL_MAX_ARGS`, no
  varargs) and a scalar or void result. **Decision:** a function taking or
  returning a struct is still inlined in debug (its statements are marked in
  the caller); flattening struct params/results per leaf for `CALL`, as
  entries do, is later work. Release lowering is unchanged. 5 new tests in
  `test_bc_debug_lowering.py` (13 pass). Probe: with debug forced on in the
  be-bc driver, be-bc 256 pass and `tests/unit` fails only the 3 tests that
  assert release behaviour plus the baseline. pssunit 67.
- 2026-11-18: P1.4a done (P1.4 split a-d: a markers, b no inlining, c local
  names, d `pssc test` default). be-bc `Lowerer.debug`, `lower_scenario/
  lower_module(debug=)`; `lower_stmt` marks each statement (`CoroCtx.stmts`,
  `CoroDescriptor.stmts` = (pc, src_ref)), a NOP where a statement has no code
  or shares its first pc with an enclosing one; `ZbcModel` writes/reads `LINE`
  with `ZBC_SECF_LINE_STMT` (rt-core `zbc_format.h` regenerated).
  `bc_session.lower(debug=)`. `tests/unit/test_bc_debug_lowering.py` (8 pass):
  markers resolve to the statement lines, release has no LINE, both compute the
  same. be-bc 256, rt-core 38, pssunit 67, unit 1 baseline fail.
- 2026-11-18: S5 done: statement markers are the LINE table, not a `STMT` opcode.
  A `STMT` opcode would shift every pc (and block ranges) between debug and
  release code and cost one dispatch per statement; the LINE section
  (`zbc_lineent{pc_start, src_ref}`, kind 11) was specified but never written.
  Debug lowering now owns it: one entry per PSS statement, `pc_start` = global
  CODE index of the statement's first instruction, sorted ascending; a statement
  that emits no code gets a `NOP` so every statement has its own pc. Section
  flag `ZBC_SECF_LINE_STMT` (0x0002) says the entries are statement starts. The VM
  turns the table into a per-code-unit pc set only when `stmt` events are on
  (P1.6), so the cost is zero otherwise. Release images stay byte-identical:
  release lowering never builds the table, and the runtime profile strips it.
  Recorded in be-bc `docs/spec/zbc-format.md` "Statement markers (`LINE`)".
  Notes: packet `state/notes/s5.md`.
- 2026-11-18: P1.3d2 done. be-bc `format/spec.py` gains section `ENTRY=16`,
  record `zbc_entry` (32 bytes: name, coro, kind, comp_type StrIds, `sig` =
  StrId of canonical JSON `{params, returns}`) and enum `zbc_entry_kind`;
  `ZbcModel._serialize_entries/_parse_entries` (codegen profile only: it
  needs the string table); `oracle.roundtrip` checks entries (codegen) and
  reattaches them (runtime). rt-core `zbc_format.h` regenerated (additive).
  D11a: be-bc `docs/spec/zbc-format.md` "Entry points". Tests: be-bc
  `tests/unit/format/test_entry_section.py` (4), pssc
  `test_pssunit_optrace.py::test_the_image_carries_the_entry_points`.
  be-bc 256, rt-core 38, unit 1 baseline fail, pssunit 67. Found, not
  fixed: a runtime-profile image with named coroutines cannot be read back
  (names interned, STRB not written; `from_container` IndexError).
- 2026-11-18: P1.3d1 done. `tests/unit/test_pssunit_optrace.py` (9 pass):
  each exported WB DMA operation (configure_interrupt_routing x2,
  pause_engine x2, read_descriptor_residual, write_descriptor with and
  without `prev`) runs on a bc `Session` (after `initialize(0x1000)`) and on
  op-model-py over `MemoryBus`; the traces `(kind, width, addr, data)` are
  identical, first time (no be-bc fix needed). `notify_irq` is excluded (no
  bus access); a coverage test fails if an export is added and not listed.
  Calibrated: `bc_session._reg_offset` +4 makes the trace differ. Limit: the
  exports touch only root registers (CSR, INT_MSK_A/B) and memory; channel
  bank registers are covered by `test_bc_registers.py` only. Unit: 3102
  pass, 1 baseline fail.
- 2026-11-18: P1.3d1b done. An entry function's struct parameter is one
  parameter per leaf, named `cfg.src`, `cfg.dst`, ... in declaration order
  (be-bc `lower_entry_function`; `_called_form` loads each leaf from its
  own `LD_ARG`); `Session.call` takes them flat. A struct return is still
  refused. WB DMA lowers with `functions=["*"]` (every entry). Test:
  `tests/unit/test_bc_registers.py` (6 pass). be-bc 250, ir-core 226,
  unit 1 baseline fail, pssunit 67, `pssc test examples/wb_dma` 2 pass.
- 2026-11-18: P1.3d1a done. be-bc lowers `reg_c` `read`, `read_val`, `write`,
  `write_val`, `write_field`, `write_masked`, `write_val_masked`, `get_handle`
  and `reg_group_c` `set_handle`/`get_handle` (be-bc lower/procedural.py,
  "Registers and register groups"): address = the group's `$handle` slot
  (new in ir-core comp_tree) + the register's offset; a struct is packed
  first field at bit 0; the field/masked writes are read-modify-write.
  Offsets are the group's own `get_offset_of_instance[_array]`, evaluated by
  pssc (`bc_session._reg_offset` -> `progseq_model.scalar_offset` /
  `array_base_stride`, passed as `lower_module(reg_offset=...)`); the
  front end's dense `offset_map` placed a register at 0x8 at 0x0 and has no
  sub-groups. `set_handle` binds every sub-group, arrays included.
  `_executor_for` no longer refuses memory access in a multi-instance
  component when the model has no executors. `write_fields` and register
  arrays (`grp.r[i]`) are not supported. Test:
  `tests/unit/test_bc_registers.py` (5 pass). be-bc 250, ir-core 226,
  unit 1 baseline fail, pssunit 67. WB DMA lowers except the 4 entries with
  a struct parameter (P1.3d1b).
- 2026-11-18: P1.3c done. ir-core pss_lower did not model action inheritance
  at all: a derived action had no inherited fields, exec blocks or named
  constraints, and bc refused `super;`. Now: `layout.action_fields` (fields
  along the chain, bases first) at every object-layout site (lower.py
  `_field_layout`/`_init_block`/handle lookup, action_tree `Layouts._build` and
  ref outputs, constraints `collect_solve_problem`/struct sites);
  `PSSLower._exec_stmts` (nearest exec block of a kind, top-level `super;`
  spliced with the base's, nested `super;` refused); pending constraints
  from the chain, same name overrides. T1.3c: 1 test (10 in file). Re-run
  after the change: compliance 925/0/5/59 and progseq 3117 pass, 10 fail (the
  same 10 as the baseline) — no regression. P1.3d split into d1 (bus-trace
  differential) and d2 (spec entry section).
- 2026-11-18: P1.3b done. be-bc: `lower_entry_function` takes any instance
  type (`<T>::f` resolved from the root if root is_a T, else from T), lowers
  with a relative `CompCtx(T, 0, 0)` so one entry serves every instance of T,
  returns the type (`EntryPoint.comp_type`); `entry_functions=["*"]` expands
  via `entry_function_symbols` (every bodied non-exec function of each tree
  type); `CompTable.types`. pssc: `bc_session.Session` (`load`, `construct`
  runs `$comp_init` on a persistent VM, `instance(path)`, `call(inst,
  "ch[0].f", *args)` = `sched.new_frame` + `set_comp` + `_drain`, `run_test`
  = fresh construct + call in root -> ledger counts). T1.3b: 4 more tests in
  tests/unit/test_pssunit_entries.py (9). Docs: test-internals.rst Execute.
  Unit baseline fail only; tests/pssunit 67; wb_dma 2; be-bc 250; ir-core 226.
- 2026-11-18: P1.3a done (P1.3 split a-d). be-bc: `lower_entry_function`
  (lower/procedural.py, next to `lower_comp_init`) builds an entry from the
  function's called form (`_called_form`) in the root instance, looked up
  virtually from the root type; `lower_scenario`/`lower_module` take
  `entry_functions`; `ZbcModel.entries` + `EntryPoint` (model.py; compare=False,
  not serialized). ir-core `PSSToScenarioPass.lower`: a root component with no
  actions no longer raises when it has a component tree (a fixture of test
  functions). pssc: `bc_session.lower(ctx, comp, functions=)`, `entries()` reads
  `model.entries`, `run(..., args=)` sets the root frame's args (retval in the
  result); the runner collects each comp's test functions and enters them by
  `decl`. Test functions use native checks (`rt.check_eq_int`); the import
  checks (`check_eq` ...) still fail lowering on string args (open, P1.3b/P1.4).
  T1.3a = tests/unit/test_pssunit_entries.py (5). Docs: test-internals.rst
  (Execute: entries). Unit: baseline fail only; tests/pssunit 67; wb_dma 2;
  be-bc 250; ir-core 226.
- 2026-11-18: P1.2 done. `src/pssc/testing/catalog.py` (`build`, `base_chain`,
  `span_end`, `select_lines`, `to_json`); `discover.discover` delegates to it and
  `TestItem` gains kind, decl (`type_name`), end_line, tags, property_runs,
  expect_error, platform_min, bases, is_fixture. Roots = every concrete component
  with a test, own or inherited (R-2 kept: non-fixture declaring components still
  roots); inherited tests get `<root>::<test>`; the runner looks up fields by
  `item.type_name`. `@pssunit_pkg::test` functions are catalogued (`kind=function`)
  but the runner cannot run them until P1.3 (they would ERROR "did not lower").
  Spans: the IR has only the start Loc (action Loc is the `action` keyword, after
  its annotations), so `span_end` brace-matches the source. `FILE:LINE` src-spec
  selects; no match exits 5. `pssc test --collect-only --json`, schema
  docs/schemas/pssunit.catalog-1.json. Unknown base: the front end already rejects
  it located (exit 2); the catalog's own path prefixes `file:line:col`. **Call-graph
  slice is a stub** (`calls: null`) -- OpModel not wired; P2.13 must fill it. Inherited
  tests run: the derived comp's bc image names the entry `<base>::<test>`, so the
  runner falls back to `item.decl`. T1.2 = tests/unit/test_pssunit_catalog.py (16). Docs:
  test-command.rst (--json, IDs, FILE:LINE), test-internals.rst. Unit: baseline fail
  only; tests/pssunit 67 pass; wb_dma 2 pass.
- 2026-11-18: P1.1 done. `pssunit_pkg.pss` gains fixture_c, mock_c, access_s,
  access_kind_e, the annotations (test.tags, property, expect_error, tag, under_test,
  platform, field_access, allow, setup, teardown), the check imports and the fake API
  imports. Runner side: `src/pssc/testing/imports.py` (IMPORTS registry, Ledger,
  RunState, Double protocol, `missing(ctx)`); T1.1 = tests/unit/test_pssunit_imports.py.
  D2 started: docs/writing-tests.rst (package reference). Decisions: a register is a
  **string path from the fixture** ("dma.ch[0].regs.csr"): an import cannot take a
  component (PSS137); `status_sequence` queues one value per call (no list args);
  compares take `int[64]`. **Open for P1.3/P1.4:** bc refuses string args to imports
  (`_no_string_import`, be-bc lower/procedural.py) and ImportProvider gets only
  `fn_id`+u64 args — pssvm must pass strings and names for pssunit_pkg imports.
  pssc's copy now adds a member to `annotation test`; the superset test compares
  member-wise. The pssunit repo's normative copy is NOT updated (read-only here).
  Unit: 1 baseline fail only; tests/pssunit 67 pass.
- 2026-11-18: P0.2 done: test_bc_path.py rewritten on `pssc.testing.bc_session`
  (wb_dma case needs PSSUNIT_EXAMPLES=<pssunit>/examples; the `print` escape case uses
  `message`, as this tree's bc does not lower `print`). P0.3 done:
  tests/unit/test_pssunit_pkg_copy.py, a declared-superset check against
  <pssunit>/src/pss/pssunit_pkg/*.pss (PSSUNIT_ROOT; skips if absent; the normative
  `test_c` is outside the package so it is not compared). Unit 3029 pass / 1 baseline fail;
  tests/pssunit 67 pass. Phase 0 closed. Next: Phase 1 in §10 order.
- 2026-11-18: P0.2 mostly done (not ticked): runner, bc_session.py, docs, tests ported;
  R-5 codes in `pssc test`; unit 3026 pass/1 baseline fail; tests/pssunit 54 pass.
  Open: tests/pssunit/test_bc_path.py module-skipped (old bc API), rewrite on bc_session.
- 2026-11-18: R-1…R-7 accepted as recommended. P0.1 done: baselines above;
  logs were in /tmp/pssc-debug-baseline/ (not kept).
- 2026-11-18: plan written from the design (revision 2) and a survey of
  pssc, be-bc, rt-eng, covsight, the pssunit repo and its uncommitted runner,
  and vscode-pss-support. Formal analysis (design §13) is excluded.
- 2026-11-18 P1.11a done: `check_rewrite.rewrite(ctx)` (run by `bc_session.lower`)
  turns pssunit_pkg checks into temporaries, `check_operand(v)` calls and
  `check_capture(id, ok)`; table `model.checks` (CheckSite: text, operands with
  offsets, what, reg strings). pkg gains the two imports; `imports.py` implements
  them (RunState.checks/operands). check(a OP b) temps are int[64] (unsigned >= 2**63
  misorders). Tests: test_pssunit_check_rewrite.py (4); unit 3192 + 1 baseline.
- 2026-11-18 P1.11b done: `vm._Imports.call` sends pssunit_pkg imports (the lowering
  names them unqualified) to `imports.IMPORTS` on `Session.run_state` (a `RunState`
  per construction: `model.checks`, double = the platform if a `RegisterFake`); a
  platform method of the same name still wins, except check_reg/field/written/
  not_accessed (`_PKG_FIRST`; `RegisterFake.check_reg` is the back door).
  `vm.Session.run_test` adds the RunState ledger to rt_c's counts; a failed require/
  fail ends the test (`RequireFailed` reaches the host unwrapped) and sets
  `Session.stopped`. No double: `NoDouble` propagates. `pssc test` (runner.py) does
  not run through vm.Session yet. Tests: test_pssunit_check_rewrite.py (7); unit 3196
  + 1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.11c done: `report.failure_card(site, values, test, seed, bus, last,
  registers, repro)` renders design 6.6 (location, `what`, call text, a bar and the
  value under each operand, greedy rows when values overlap; register decoded
  `regs.ctl = 0x... {f=v}`; last N `RegisterFake.bus` lines `#i W32 0x1000_0004 ... src`;
  `repro: pssc test -k <fn> --seed N`). `RegisterFake.decoded(path)`;
  `RunState.captured` (id, ok, operands) per rewritten check; `vm.Session.failure_cards(test)`.
  Not yet: enum names for operand values; `pssc test` (runner.py, report_human) does
  not run through vm.Session, so cards are not printed by the CLI yet (runner item).
  Doc: docs/writing-tests.rst. Tests: test_pssunit_check_rewrite.py (9); unit 3198 + 1.
- 2026-11-18 P1.12 done: `src/pssc/testing/sanitizers.py` (`Sanitizers`, `Finding`,
  `SanitizerFailed`, `allows(ctx)`): `poll-budget` (same address, same value, no write
  between, > `poll_budget` reads, default 1000; stops the test), `unmapped` (a hole in a
  placed register block; with `regions` declared, anything outside registers and regions),
  `access-rule` (write to RO / read of WO register; a register with fields: all fields RO /
  all WO), consuming `access` events (reg_id) only, so they run on any stream. `vm.Session`
  (`sanitize=True, regions=None` -> platform `.regions`, `poll_budget`) feeds them from
  `_Tee`, raises `sanitizer` host events, counts each finding as a FAIL (`Session.findings`,
  `stopped`); `vm.load` sets `model.allows` from `@pssunit_pkg::allow` (unknown name ->
  ValueError). Location = the access's statement (for poll-budget the polling read inside
  the loop; the loop itself is not named separately). Not yet: `pssc test` (runner.py)
  does not run through vm.Session, so the CLI does not run sanitizers; findings are not
  in `failure_cards`. Doc: docs/writing-tests.rst "Sanitizers". Tests:
  test_pssunit_sanitizers.py (9); unit 3209 + 1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.13a done: P1.13 split into a (isolation, set-up/tear-down), b (property
  runs, regression seeds), c (`expect_error`), d (`pssc test` through vm.Session; was a
  gap noted in P1.11b/c and P1.12). Fresh tree: already so (bc_session.run builds a VM and
  component object per test); now covered by a test. `catalog.hooks(ctx, comp)` -> the
  `@pssunit_pkg::setup` (base first) and `teardown` (derived first) function symbols of a
  root's lineage; a redeclared function runs once, as the derived one. runner
  `_build_image` lowers them with entries (`_Image.setup/teardown`), `bc_session.run(...,
  setup=, teardown=)` runs them on the root (`_call_root`) after construction / after the
  entry; teardown runs after an error too, and its own error then does not replace the
  test's. Seeds: the summary and every failure's repro line already print the run seed;
  per-run seeds come with property runs (b). Doc: docs/writing-tests.rst "Set-up and
  tear-down". Tests: test_pssunit_isolation.py (5); targeted + runner set 853 + 1 skip;
  unit 3215 + 1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.13b done: runner `run_property` runs a `@pssunit_pkg::property {.runs=N}`
  test (default 100) once per seed: recorded regression seeds first, then
  `run_seeds(seed, item, N)` (run 0 = the run seed, so `--seed S` replays a failing run
  first; the rest from `random.Random(seed ^ crc32(qname))`). First non-PASS run ends it:
  `Result.failing_seed`, `runs`, detail "run i of n failed (seed S): ...", seed appended to
  `.pssunit/regressions/<qname, :: -> .>.seeds` (cwd-relative; `run(regressions=)`, None
  keeps none; not recorded when the image did not lower). Passing: runs and checks summed.
  report: replay uses `failing_seed`; line shows "N runs"; CTRF extra `runs`/`failingSeed`.
  Shrinking left out (design §6.5; not in P1.13). Doc: writing-tests.rst "Property tests".
  Tests: test_pssunit_isolation.py (8); unit 3218 + 1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.13c done: runner `_settle_expect_error` (applied in `run` before xfail):
  `@pssunit_pkg::expect_error {.text}` -> PASS only on Status.ERROR whose detail (located,
  "file:line:col: error: msg") contains text; detail "expected error: ...". Error with other
  text, a run to completion, or a failed check -> FAIL saying what was expected and what
  happened. UNSUPPORTED/UNSAT (lowering/solve) left as they are. Not handled: property +
  expect_error together (run_property stops at the first non-PASS run). Doc:
  writing-tests.rst "Expected errors". Tests: test_pssunit_isolation.py (10); unit 3220 +
  1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.13d done: runner `run_one` builds a `vm.Session` per run on the cached
  image (`_RunPlatform`: `vm.Platform` sparse memory; a user import nothing implements
  returns 0, as be-bc's RecordingImportProvider did) and calls the new
  `Session.run_entry(coro, name, seed, field_names, setup, teardown)` (bc_session: action or
  root function on a fresh VM, `_new_vm` split out of `construct`; vm.Session counts pkg
  checks + sanitizer findings via `_counted`, shared with `run_test`). `_build_image` sets
  `model.allows`. A failed test's detail: count, `stopped:`, failure cards (else ledger
  failure texts), findings. Not done: RegisterFake platform / `--regions` / `--no-sanitize`
  CLI flags; card's `repro:` line lacks the source path (report's `replay:` has it). Doc:
  writing-tests.rst. Tests: test_pssunit_isolation.py (12); unit 3222 + 1 baseline; pssunit
  67; wb_dma 2 pass.
- 2026-11-18 P1.14 done: new `src/pssc/testing/gate.py` (`declared_targets`, `exempt_files`,
  `device_roots`, `check`), called from cmd.py `_cmd_test` after collection (not under
  `--collect-only`). Targets: `--targets a,b` | `./pyproject.toml [tool.pssunit].targets` |
  `op-model-c`; `none` gates nothing. Gated: the topmost device components under the test
  roots (a component is test-side if declared in a file that declares a test, a fixture, or
  holding a pssunit_pkg component), each on a fresh translate via the target's
  `build_model` + `check`; refusals located in a test file or pssunit_pkg are dropped; a
  device with zero operations is skipped. Gating the test root itself was rejected:
  pssunit_pkg's string refusals (category "types C does not render") stop `check` before it
  reaches the device's. Known gap: `check` stops at the first refusing category, so an
  exempt refusal there can mask a later device one (only when the device subtree itself
  uses exempt code). Doc: test-command.rst (`--targets`, exit 2). Tests:
  test_pssunit_gate.py (4); unit 3228 + 1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.15: `--platform py:<mod>[:<Class>]` / `[tool.pssunit] platform` (vm.py
  `declared_platform`, `load_platform` -> `Hosted`; `PlatformLoadError`). cwd is searched
  first. runner `run/run_property/run_one(platform=)`: each test gets `Hosted.make()` (new
  `Class()`, else `_RunPlatform`) and `Session(use=Hosted.doubles)` (the module, if its
  `@pv.*` declared any), so bindings are per test. A spec that does not load: exit 3, one
  line on stdout, traceback in `.pssunit/platform.log` (cmd.py `_write_platform_log`). A
  module with no doubles and no `:Class` is a load error. Doc: test-command.rst
  (`--platform`, exit 3, table). Tests: test_pssunit_platform.py (5); unit 3234 + 1
  baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.16a: P1.16 split into a-d. Selection: a src-spec containing `::` that is
  not an existing path is a test-ID glob (cmd.py `split_id_specs`; `fnmatchcase` on the
  whole ID, no substring fallback; only IDs -> sources default to cwd). `-m TAG`
  (`args.tags`, append; any of them) on `TestItem.tags`. `--lf`/`--last-failed`:
  `.pssunit/lastfailed` (`{"failed": [ids]}`, cmd.py `LAST_FAILED`, `read_last_failed`,
  `write_last_failed`, `_select_last_failed`); a run updates the record for the tests it
  ran and keeps the rest; written only when it changes; nothing recorded among the
  selection -> runs all, with a line. Fix: the gate got raw specs, so `FILE:LINE` / ID
  specs broke a real run; now stripped. tests/conftest.py autouse fixture moves
  `LAST_FAILED` to pytest's basetemp (tests run `pssc test` from the repo root). Doc:
  test-command.rst (selection table + prose, option table). Tests:
  test_pssunit_select.py (6); unit 3241 + 1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.16b: `--platform fake` = a new `RegisterFake` per test
  (runner `_FakeRunPlatform`, `vm.Hosted.fake`; unimplemented user imports
  still return 0). `--seeds N` runs every test N times through
  `run_property(runs=)` (first run = run seed; failure reports
  `failing_seed`); property tests run max(N, own count) and only they record
  regression seeds. `-j/--jobs N`: forked `multiprocessing` pool
  (`runner._run_parallel`, `_run_item`), images built per worker, results in
  collection order, `-x` cuts at the same failure. Tests
  `tests/unit/test_pssunit_runs.py` (5). Unit 3247 + 1 baseline.

- 2026-11-18 P1.16c done: `--json` (when running) streams NDJSON
  `pssunit.events/1` (`docs/schemas/pssunit.events-1.json`): `run.start`,
  `test.start`/`test.end` per test (runner `run(on_start=, on_result=)`; with
  `-j` the start comes just before its end), `run.end` (summary, exit); stdout
  carries nothing else. `test.end` has the runner status, message, stdout,
  checks and `repro` on failing verdicts. `--report PATH` = `--ctrf PATH`;
  `--junit PATH` (report.py `build_junit`/`write_junit`, one testsuite per
  root component, failure text ends with the repro). Docs: test-command.rst,
  cli.md. Tests `tests/unit/test_pssunit_output.py` (5, schema-validated).
  Unit 3253 + 1 baseline.
- 2026-11-18 P1.16d done (P1.16 closed): the replay/repro line names the test
  by ID (`suite::test`, not `-k`), keeps the sources (ID and `FILE:LINE`
  selections dropped) and the verdict-changing options (cmd.py
  `replay_options`: `--platform`, `--targets`, `--no-debug`, `--strict`,
  `--runxfail`, `--noskip`), shell-quoted (report.py `replay_command(...,
  options)`, also in NDJSON `repro` and JUnit). Check cards no longer print a
  second, shorter repro in the CLI (runner passes `repro=""`); the Session-API
  card default is `pssc test <qname> --seed N`. Default output kept as the
  per-test lines + failure blocks (cards) + summary; `-q` is summary only.
  Test: `test_the_replay_line_reproduces_each_failure` (runs each printed line
  through `main`, same FAIL, fixed point). Unit 3255 + 1 baseline.
- 2026-11-18 P1.17 done: `pssc model api|regs|explain|tests` (src/pssc/devenv/model_cmd.py,
  wired in cli.py; `--json` = one `pssc.model/1` document each). Answers from the manifest
  document (`manifest.build` on `build_model`, target op-model-c), plus each operation's
  `reads`/`writes` (`OpModel.state_uses`). Manifest registers gained `counts` and `array_at`
  (reg_layout `RegAccessor.counts/array_at`; docs/op-model-manifest.md) so every array element
  can be enumerated. Sub-component groups are bound at run time; one whose layout equals an
  outermost root group array of the same count is taken as bound to element i ("bound by
  layout", `via: layout`, aliases) -- checked against real VM accesses. Tests:
  tests/unit/test_devenv_model.py (5, T1.17). Docs: docs/cli.md `pssc model`. Unit 3263 + 1 baseline.
- 2026-11-18 S1 done (spike; no code change): pssparser `Parser.link()` links only the units
  parsed since the previous link (the root takes ownership of earlier ones), so re-parsing one
  changed file and re-linking fails ("unknown type"); per-file re-link would need a pssparser
  API. Not needed: a whole parse+link+translate+lower of examples/op_model WB DMA is ~92 ms warm
  (parse 18, link 2.5, translate 51, lower 20); the gate (187 ms: it re-translates twice) is
  cached on device-file hashes instead. T-G2 (300 ms) kept. P1.18 split into a/b/c; T1.18's
  "re-parse only that file" became "edit -> one rebuild; no edit -> none". Script: measured
  in-process with `Parser`, `AstToIrTranslator`, `gate.check`, `bc_session.lower` timers.
- 2026-11-18 P1.18a done: src/pssc/devenv/{protocol,daemon,client}.py + src/pssc/_entry.py
  (console script and `python -m pssc` now go through `_entry:main`). Frames: 4-byte length +
  JSON; id = protocol 1 + pssc version + `build` (count and newest mtime of the package's .py
  files, ~3 ms, so an edited checkout restarts the daemon). Workspace = nearest ancestor with
  pyproject.toml/.git/flow.dv/flow.yaml; socket `$XDG_RUNTIME_DIR/pssd-<uid>/<sha1(root)[:16]>.sock`
  (`PSSC_DAEMON_DIR` overrides). The daemon runs `cli.main(argv)` with the client's cwd and env
  and captured stdout/stderr, one request at a time; idle stop (`PSSC_DAEMON_IDLE`, default
  900 s); `status` and `stop` answer any version. Client: start on first use, restart on
  mismatch or a dead daemon (stale socket), fall back in-process when none can start;
  `PSSC_NO_DAEMON=1` (tests/conftest.py sets it for the suite). `pssc/__init__` is now lazy
  (module `__getattr__`), so `import pssc._entry, pssc.devenv.client` is ~5 ms with no
  pssparser/zuspec/pssc.cli loaded. pssunit wb_dma: 0.30 s in-process -> 0.09 s served (wall).
  Output is streamed only at the end (no live NDJSON yet). Tests: tests/unit/test_devenv_daemon.py
  (8: T1.18 bar the cache bullet, which is P1.18b; T-G6 structural + measured). Docs: docs/cli.md
  `pssc daemon`. Unit 3276 + 1 baseline.
- 2026-11-18 P1.18b done: src/pssc/devenv/cache.py (on only in the daemon: `cache.enable()` in
  daemon `main`; in-process runs are unchanged). Keyed on (realpath, sha256) of every source:
  `cache.translate` (cmd.collect; catalog is rediscovered from the cached ctx, ~free), `cache.image`
  (runner `_run_item`, key comp+functions+debug, only for the cached ctx), `cache.gate` (gate.check
  now takes the run's ctx instead of a probe translate; key = targets + device roots + hashes of
  non-exempt files; a refusal is cached and re-raised). Latest build only. `status` reply/`daemon
  status` gain `cache` {builds, lowered, gated, hits}. T-G2 met (plan T-G2 row): collect-only
  warm 28 ms; op_model WB DMA edit -> result 244 ms gated (137 ms ungated); pssunit wb_dma 77 ms.
  Test: test_devenv_daemon.py::test_source_hash_cache (2 runs -> 1 build; test-file edit -> 2
  builds, gate not re-run; DUT edit -> 3 builds, gate re-run; served == in-process). Docs:
  docs/cli.md `pssc daemon`. Unit 3278 + 1 baseline; pssunit 67.
- 2026-11-18 P1.18c done: `pssc help [COMMAND...] [--json]` (src/pssc/devenv/help_cmd.py,
  registered in cli.build_parser; in-process, not served). `--json` walks the real argparse tree:
  {schema pssc.help/1, prog, version, args, commands: [{name, path, help, args: [{name, flags,
  positional, kind, type, required, default, choices?, metavar?, nargs?, const?, help}],
  commands?, outputs?: [{when, schema, format, file?}]}]}; outputs from `help_cmd.OUTPUTS`
  (test --json -> pssunit.events-1.json, --collect-only/model tests -> catalog-1). Schema
  docs/schemas/pssc.help-1.json. Test: tests/unit/test_devenv_help.py (4; validates, exact
  command list = T-G5 half). Docs: docs/cli.md `pssc help`. Unit 3284 + 1 baseline; pssunit 67.
- 2026-11-18: P1.19 done. `src/pssc/testing/pytest_plugin.py` + `pytest11` entry point
  `pssunit` (pyproject). Inert unless ini `pssunit_paths` (type paths) is set; then each `.pss`
  file under them that pytest visits is a `PssFile` -> `PssSuite` (fixture) -> `PssItem` (node
  ID `pss/suite.pss::SUITE::TEST`), run via `runner._run_item` on one shared `cmd.collect`
  (stash `_Run`): one seed (`--pss-seed`), ini `pssunit_platform`, regressions under rootdir
  `.pssunit/regressions`; skip/xfail map to pytest's, fail/error/xpass fail with the replay line;
  tags become markers. `pss` fixture = `Engine.load(root, ...)` -> `vm.from_ctx` (new, split out
  of `vm.load`) on the same ctx. Docs: docs/test-command.rst "Under pytest" (D3 for now; no D3
  page yet). T1.19: tests/unit/test_pssunit_pytest_plugin.py (4, subprocess pytest `-p`).
  Unit 3290 + 1 baseline; pssunit 67; wb_dma 2 pass.
- 2026-11-18 P1.20a: tests/progseq/trace_harness.py target `pyvm` (`_PyvmPlatform` cell memory
  like the C harness; `_lower_pyvm` gates with op-model-c's compile -- only its CompileError
  counts, a C codegen crash is C's -- then translate + `bc_session.lower(functions=["*"])`;
  `_run_pyvm` constructs, calls `initialize(*args)`, `run`). `_matrix.expected`: pyvm takes C's
  refusal. With pyvm in ALL: 217 pass / 252 fail (240 inline `reg_c<...>` field, a
  DataTypeRegister named 'reg_c<None>' not in the type map; 5 read_struct/write_struct; 3 C
  refusals). pyvm left out of ALL until P1.20b/c. Matrix (4 targets): 1840 pass, 1 fail
  pre-existing (sv "channels of a struct, an enum and a bool", fails without the change too).
- 2026-11-18 P1.20b: an inline `reg_c<bit[32], READWRITE, 32> R;` field's datatype is its own
  DataTypeRegister `reg_c<None>` the type map does not hold. ir-core comp_tree `field_type` now
  keys such a type (`_add_anonymous`, `reg_c<T, ACC, SZ>`, `#n` on a clash) into the type map.
  pyvm (in ALL, temporarily): 352 pass / 117 fail; top: 33 DataTypeArray unsupported by bc, 16
  array element, 7 ExprIn, 3 each `**`/struct literal/read_struct/write_struct/62-bit register
  struct; rest 1-2 each. T-G1 snapshot regenerated: the 338 changed models were all
  `error:UnsupportedConstructError` before (333 now lower, 5 LoweringError); no existing bytecode
  changed. ir-core 226, be-bc 289, unit 3290 + 1 baseline, matrix 1840 + the 1 pre-existing sv fail.
- 2026-11-18 P1.20c: pyvm is in `_matrix.ALL`. `_run_pyvm` turns the VM's stop on `error()`,
  `fatal()` and an unmatched/overlapping `match` into the `error: `/`fatal: ` trace line (5 cases
  were harness gaps, now pass). The other 111 are strict pyvm xfails, set per file by
  `_matrix.pyvm_xfail(CASES, why, *names)` after CASES; shared defects `BC_ARRAY` (33),
  `BC_SELECT` (16), `BC_IN`, `BC_POW`, `BC_STRUCT_LIT`, `BC_STRUCT_RET`, `BC_CALL_TARGET`,
  `BC_PACKED_STRUCT`, `BC_DOMAIN`, `BC_NULL`, `BC_REG_ARRAY`, `BC_ENUM_WIDTH`; `why=None` reuses
  the case's C defect (pyvm runs behind op-model-c's gate). Wrong-result one-offs (named, not
  diagnosed): local hiding a field, shadowed static fn from an inherited body, static const struct
  reads 0, 64-bit shift by 64, chained addr_value, memory primitives, match common type, generated
  names, big-endian register value/packing. The enum-manifest test skips pyvm (no manifest).
  coverage.yaml: pyvm dispositions for IP-61, DT-INT.8, DT-REF.11, EX-REF.3, EX-REF.5,
  ST-ASSIGN.8, ST-RET.5. Matrix: 2198 pass, 238 xfail, 1 pre-existing sv XPASS(strict) W64;
  unit 3290 + 1 baseline (T-G1 unchanged).
- 2026-11-18 P1.21a: platform doubles taking strings run on bc: pssunit_pkg `double_call(id, v)`/
  `double_handle(id)`, check_rewrite `DOUBLES` rewrites the call sites (imports.py
  `_double_call`/`_double_handle`). RegisterFake gained `base(name)` (fixed windows),
  `status_sequence`, `complete_after`, `fail_on_access`, `last_written`, `accessed`; scripts are
  keyed by the register INSTANCE, since its aliases are learned lazily on access.
  examples/op_model/tests: model.f, fixture `wb_dma_fx_c`, a polling target_cfg.pss (the fake
  raises no IRQ), `sw_copy_a` passes. docs/writing-tests.rst names the two doubles; unit test
  test_double_calls_with_strings_run_on_bc. Unit 3291 + 1 baseline. Known, not fixed: pkg
  mem_poke/mem_peek `size` is bytes, the fake's `width` is bits.
- 2026-11-18 P1.21b: examples/op_model/tests: derived fixture `wb_dma_routed_fx_c : wb_dma_fx_c`
  (its `@setup route_all` routes all channels to bank A after the base's `bind_dma`; it inherits
  `sw_copy_a`), and the companion operation tests `arb_a` (ch0-3 at prio 3..0, sequential on the
  fake), `abort_a` (start, `stop_channel`, collect: both ERROR, STOP written) and
  `hw_handshake_a` (mode HW_HANDSHAKE, DONE scripted after 3 pending polls). 5 pass, 0.5 s total.
  Fix: `check_rewrite.rewrite` rewrites the shared ctx in place, so the second fixture lowered got
  an empty table (`KeyError` in `_double_call`; checks lost their cards silently); it now keeps
  the table on the ctx (`_pssunit_checks`) and returns it again. Unit test
  test_a_second_fixture_from_one_context_keeps_the_table. No test needed memory, so the
  mem_poke size/width mismatch stays unfixed. Unit 3292 + 1 baseline.
- **P1.21c** (step 53): examples/op_model/tests/prio_a.pss adds, on `wb_dma_fx_c` (so inherited
  by `wb_dma_routed_fx_c`): property action `prio_a` (`rand bit[2] prio`, `.runs=8`; ch0 transfer,
  `check_field csr.prio`), test function `t_routing_mask` (bank B routing; `check_reg` +
  `check_written` on int_msk_b), and `expect_error {.text="fail_on_access"}` test function
  `t_forbidden_mask` (routing bank A with int_msk_a forbidden). A `check_not_accessed` on
  int_msk_a failed rightly under the routed fixture (its setup writes it), so it was dropped.
  Suite: 11 pass, rc 0, 0.02 s; no `.pssunit` left. Unit 3292 + 1 baseline.
- **P1.21d** (step 54): T1.21 is tests/unit/test_wb_dma_suite.py (subprocess `pssc test --json`
  on a tmp copy of examples/op_model/{pss,tests}): the suite passes with `run.end` duration
  < 1000 ms (0.03 s runner, ~0.5 s with startup), and 8 seeded bugs each fail >= 1 test. The
  fake lays registers out from the model itself, so offset/field-layout mutations passed every
  existing test; two files were added to pin the datasheet: regmap_a.pss (`t_device_offsets`
  via `get_offset_of_instance[_array]`, `t_csr_prio_bits` write_field prio=5 -> 0xa000,
  `t_csr_status_bits` poke_reg raw DONE/ERR bits + check_field) and completion_a.pss
  (`t_copy_error`: ERR poll -> transfer_single returns ERROR; `t_poll_pending`: check_completion
  PENDING then DONE). Suite 21 pass. Unit 3302 + 1 baseline.
- **P1.21e** (step 55): D1 tutorial docs/tutorial.rst (in index toctree after quickstart):
  model.f/target_cfg, fixture, `sw_copy_a`, run, failing card (leave out the `status_sequence`
  lines -> poll-budget sanitizer stop), fix, then the rest of the suite. PSS via `literalinclude`
  of examples/op_model/tests (`:language: none`; `pss` is no Pygments lexer). Test
  tests/unit/test_doc_tutorial.py (T-D1 for D1): include paths/markers exist in the suite dir;
  runs the 3-file project (pass block) and the mutated one (card block, seeds/times as regex).
  Sphinx build: no tutorial warnings. Unit 3307 + 1 baseline. P1.21 done.
- 2026-11-18: Phase 1 exit gate checked. Pass: T1.21 (<1 s), T-G1, T-G2 (measured), T1.20
  (pyvm 365 pass/125 xfail), T-G6, T-G7, D0, D1. Closed here: T-G5 (`pssc model --json` had no
  schema: docs/schemas/pssc.model-1.json, help OUTPUTS `file`, test_devenv_model.py
  `test_json_validates_against_schema`) and D5 (schema versioning rules, docs/cli.md). Open: T-G4
  (no single test per failure kind; sanitizer/check cards are `file:line`, no `:col`), D2
  (writing-tests.rst lacks inheritance/`super;`, gate, IDs), D3 (no Platform doubles page), D4
  (cli.md hand-written; no page generated from `pssc help --json`, no T-D2). Unit 3311 + 1 baseline.
- 2026-11-18: E1 (T-G4) closed. Owner: close the exit gaps before P2. New
  tests/unit/test_located_kinds.py (12): check + require cards, error, fatal, VMError (via
  `pssc test`), unimplemented import (`vm.load` with a bare `Platform`: `pssc test` returns 0 for
  them), the three sanitizers (`--platform fake`), gate refusal, parse error, discovery error
  (hand-built ctx; the front end rejects an unknown base first); each asserts `file:line:col`.
  Sanitizer findings and bus-log `src` were `file:line`: `vm._where` and `RegisterFake.where` now
  give `file:line:col` (col from Prov; `file:line` if 0). Updated test_pssunit_sanitizers.py,
  test_pssunit_fake.py, test_pssunit_check_rewrite.py, docs/tutorial.rst card. Unit 3324 + 1 baseline.
- 2026-11-18: E2 (D2) closed. docs/writing-tests.rst gains "Test IDs", "Inheritance" (fixture
  inheritance, abstract bases, `super;` chain) and "The op-model gate", each cross-linking
  test-command.rst. Their PSS blocks are verbatim from test_pssunit_select.py `TESTS`,
  test_pssunit_catalog.py `SUITE`, test_pssunit_entries.py `SUPER`, test_pssunit_gate.py
  `DUT_REFUSED`. New tests/unit/test_doc_writing_tests.py (T-D1) checks every `code-block:: pss`
  on the page is a verbatim excerpt of a tests/unit source; the 3 older sketches (set-up,
  property, expect_error) are listed as unchecked. Unit 3331 + 1 baseline.
- 2026-11-18: E3 (D3) closed. New docs/platform-doubles.rst (in the index toctree; linked from
  writing-tests.rst "Platform double" and test-command.rst `--platform`): plain memory, the
  register fake (field kinds via `@pssunit_pkg::field_access`, back door, decoded `bus` log,
  `on_write`/`on_read` hooks), `pssc.testing.vm` (`load`, `Session`, `Platform`, the op-model-py
  ImportApi seam, `implements`/`mock`/`spy`, `ctx`), R-3 binding (`--platform` >
  `[tool.pssunit] platform` > plain memory; `load_platform`, exit 3) and the pytest plugin. Tier 1
  `mock_c` hooks noted as not implemented (P2.6). Every pss/python block is verbatim from
  test_pssunit_fake/vm/platform/pytest_plugin sources; new tests/unit/test_doc_platform_doubles.py
  (T-D1, toctree, back-links; 15 tests). Unit 3348 + 1 baseline.
- 2026-11-18: E4 (D4, T-D2) closed. `pssc help --rst` (help_cmd `render_rst`, `REGENERATE`)
  renders the `pssc.help/1` document as docs/cli-reference.rst (every command/sub-command, each
  option with metavar, choices, defaults the help does not state, and the machine-readable
  outputs with their schema files; version left out so a release does not change it). The page is
  checked in, in the index toctree, and linked from docs/cli.md (now "the guide"; `pssc help` section
  lists `--rst`). New tests/unit/test_doc_cli_reference.py (T-D2: page == generator output, failing
  with the regenerate command; sections for every command path, `test` options/schemas, toctree;
  6 tests). docutils parses the page with no messages. Unit 3356 + 1 baseline. Phase 1 exit-gate
  gaps all closed.
- 2026-11-18: P2 started (owner: "yes, P2.1"). P2.1 split: P2.1a spec, P2.1b pyvm control
  (stop/continue/step/pc breaks on the P1.6 hooks), P2.1c inspection + access watch + stop-on
  kinds, each with T2.1 tests. P2.1a done: be-bc docs/spec/debug-stub.md (v1): request/reply/event
  JSON shapes, error codes, all-stop model starting stopped, threads = scheduler frames and
  frames = CALL chain (`Frame.id`), `step stmt|into|out` on statement markers, `set_pc_breaks`
  by image coro index + pc, `set_access_watch` by addr/size/rw/value/mask (front end resolves
  register paths), `set_stop_on` kinds, inspection (`threads`, `frames`, `read_frame`,
  `read_comp`, `backdoor_read`, `exec_snippet` = JSON PROC-op list), `reload` reserved (P3.6),
  stop reasons with details, versioning. JSON examples parse; no code yet.
- 2026-11-18: P2.1b done: be-bc `interp/debug.py` `Stub(vm, root|run, on_event)`: `request(msg)
  -> reply`, events queued (`next_event`) and to `on_event`; the run goes on a host thread,
  starts stopped before its first instruction (attach waits for it). attach/detach/terminate,
  continue/stop, step stmt|into|out (thread = frame up the `called` chain; depth = CALL hops;
  an ended thread's step stops at any thread's next statement), set_pc_breaks (replace,
  verified), `stopped`/`exited` events. VM: `_dbg` (None normally); `run_frame` sends every frame
  to `_run_frame_ev` while one is attached, which calls `Stub._before(frame, pc)` per
  instruction. Engine errors exit with status error (stop-on `error` is P2.1c). T2.1:
  tests/unit/interp/test_debug_stub.py, 13 data-driven cases (`CASES`, `run_case`) + 6 more
  (19 pass); be-bc 308 pass.
- 2026-11-18: P2.1c split c1 (inspection), c2 (`exec_snippet`), c3 (access watch, stop-on, D11c).
  P2.1c1 done: `Stub` `threads` (root + `children` walk + frames `_before` saw, so threads started
  inside a called function count; state running/ready/waiting), `frames` (innermost first; pc =
  next instruction, src_ref = last statement start <= pc), `read_frame` (LOCAL table in scope;
  raw `regs` without one; args; `this`), `read_comp` (index or path; fields = slots up to the next
  instance's base -- a guess for nested trees), `backdoor_read` (memory `peek(addr, n)` else
  `extern.Memory.bytes`; no log). `step` takes any live thread. T2.1: 3 more CASES + 2 tests
  (24 pass); be-bc 313; pssc unit 3356 + 1 baseline.
- 2026-11-18: P2.1c2 done: be-bc interp/debug.py `run_snippet` + `Stub._cmd_exec_snippet` (read-only
  scratch frame, back-door memory reads; write/ORCH ops -> `snippet` error).
- 2026-11-18: P2.1c3 done, P2.1 closed: `set_access_watch` (`_WatchedMemory`), `set_stop_on`
  (check/error/sanitizer via a tee over `vm.events`; unimplemented/mocked not accepted in v1),
  engine-error stop (default stop set `["error"]`, detail `{"error": message}`); watch/event stops
  reported at the next instruction boundary. Spec pyvm note in debug-stub.md; D11c: trace-schema.md
  links debug-stub.md. T2.1: 30 pass; be-bc 319; pssc unit 3356 + 1 baseline.
- 2026-11-18: P2.2a Spike S2 done (§10 S2 row): eval/conditions compile a synthetic `extend`
  unit appended after the sources through `driver.translate` and take the typed IR expr of the
  `<dbg>` statement; P2.2e adds a trailing in-memory unit to `translate` (prelude units go first,
  and an `extend` of an action declared in an `extend component` fails there with "unknown type").
- 2026-11-18: P2.2b done: `src/pssc/devenv/debug/breakpoints.py` (`LineIndex` over LINE +
  prov: a non-statement line moves to the next statement within a coroutine spanning it, else
  unverified; every coroutine's copy of the line; file matched by abspath or path suffix;
  `BreakpointSet.set_file/pc_breaks/apply/on_stop`: condition, hit condition `N|==|>=|>|<=|<|%N`,
  logpoint `{expr}` via a caller evaluator). Limit: a function's header line (no statement) is
  unverified (coroutines carry no function span). T2.2 part: tests/unit/test_debug_breakpoints.py
  (40, incl. runs over the pyvm Stub). Unit 3399 + 1 baseline.
- 2026-11-18: P2.2c done: `src/pssc/devenv/debug/watches.py` (`resolve` register/field path over
  `model.registers`; `WatchSet.set/learn/access_watches/apply/on_stop`: rw r|w|rw, field value as
  engine value/mask, condition, hit condition; a component's base is learnt at run time by
  `BaseLearner` on the VM's memory (an inert watch keeps the stub's memory wrapper in place so the
  first access is matched); `paths` for completion). The stub and the learner take the session's
  `vm`, not the `pv.Session`. T2.2 part: tests/unit/test_debug_watches.py (7: write, field value,
  read + condition + hit count, alias bank/channel). Unit 3408 + 1 baseline.
- 2026-11-18: P2.2d done: `src/pssc/devenv/debug/variables.py` (`Variables.scopes/variables/
  invalidate`, DAP-shaped refs; Locals with struct leaves grouped, Action (pyvm frame `obj`
  fallback: `read_frame` has no `attrs` yet), Component (relative names), Registers under the
  frame's component via stub `backdoor_read`, decoded by field, Channels (`ChannelLog` events sink
  on `chan` events), Bus (last N of the fake's `bus`, newest first)). Back door added:
  `Platform.peek`, `RegisterFake.peek` (placed register's cell, no side effect), `pv._Memory.peek`.
  T2.2 part: tests/unit/test_debug_variables.py (9). Unit 3419 + 1 baseline; be-bc 319.
- 2026-11-18: P2.2e done: `src/pssc/devenv/debug/eval.py`. **Deviation from S2**: no synthetic
  `extend` unit; a small expression parser (C-like operators, `?:`, sized/hex literals, dotted and
  indexed paths) resolves names against the frame's debug tables (LOCAL table at pc -> `LD_LOCAL`,
  `argN` -> `LD_ARG`, own component field -> `LD_COMP`, pyvm action attr -> `LD_FIELD`) and compiles
  to an `exec_snippet` program; registers (relative to the frame's component, or absolute, `.field`)
  and other instances' fields are read when compiling (`backdoor_read` / `read_comp`) and become
  constants, so eval leaves the bus log unchanged. Why: the wrapper needs the PSS type names of
  struct locals/params, which the LOCAL table does not carry, and the sources at eval time. Signed
  when either operand is; `&&`/`||`/`?:` evaluate both sides. Not yet: enum literals, calls,
  structs, strings. `Evaluator.at(frame, coro, pc)` / `at_stop(body)` give the `Evaluate` callable
  for BreakpointSet/WatchSet `on_stop`. T2.2 part: tests/unit/test_debug_eval.py (31). Unit 3452 +
  1 baseline.
- 2026-11-18: P2.2f done, P2.2 closed: `src/pssc/devenv/debug/frontend.py` `DebugSession(stub,
  model, platform)`: transport-free session over one stub; owns BreakpointSet, WatchSet (seeded from
  the platform's bases, `learn_bases` on the VM's memory), Variables, Evaluator. `start()` attaches
  and sends breaks/watches; `resume`/`cont`/`step` invalidate variables, then `wait()` filters
  `breakpoint`/`watch` stops through `on_stop` (conditions, hit counts, values; logpoint lines ->
  `output`) and continues past the rest; returns one record (`stopped` + `source`, `breakpoints` or
  `watches`, `output`; or `exited`). `frames`/`scopes`/`vars`/`evaluate(text, frame=)`, `end()`.
  Fix in eval.py `Evaluator.scope`: a register's struct fields also have (never written) component
  slots, which shadowed `regs.csr.prio` (read 0); register paths now win. D6 skeleton:
  docs/debugging.rst (in index toctree). T2.2 part: tests/unit/test_debug_frontend.py (7). Unit
  3461 + 1 baseline.
- 2026-11-18: P2.3 opened, split a) stop schema, b) daemon session registry, c) dbg_cmd.py + `--json`
  + cli-reference, d) T2.3 tests, e) D6 "Command-line sessions". P2.3a done:
  docs/schemas/pssunit.stop-1.json = `DebugSession.wait` record + envelope `schema`/`session`;
  `event` stopped|exited|running (`running`: `--timeout` ran out, run goes on, `{timeout}`).
  Test `test_stop_records_match_the_stop_schema` (test_debug_frontend.py, 8). Unit 3463 + 1 baseline.
- 2026-11-18: P2.3b done: src/pssc/devenv/debug/sessions.py `Registry` (`REGISTRY`, per process):
  `start(sources, test, seed, platform, timeout)` collects, requires exactly one test (ID glob or
  `-k`), builds the debug image (`runner._build_image(debug=True)`), runs `vm.Session.run_entry`
  on a `Stub` thread; `get/end/end_all/list`, `latest`. No new daemon op: `pssc dbg` will run via
  the `run` op (cli.main in the daemon process sees `REGISTRY`); `Daemon.serve` ends all sessions
  on exit (`_end_dbg_sessions`). Changes: runner `_entry_of`/`_session_for` split out of
  `run_one`; bc_session `run_entry(new_vm=False)` runs on the VM the stub holds;
  `DebugSession.wait(timeout)` returns `{"event":"running","timeout","output"}` instead of
  raising (overall deadline), a run command while running only waits, `step` refuses.
  Fix (breakpoints.py `LineIndex`): consecutive markers at one file:line:col (a rewritten
  `check_reg` lowers to 3 statements) are one breakpoint location, not 3 stops.
  tests/unit/test_devenv_dbg.py (5). Unit 3470 + 1 baseline.
- 2026-11-18: P2.3c done: src/pssc/devenv/dbg_cmd.py `pssc dbg` start (SRC... TEST-ID | -k,
  --seed, --platform, --timeout) / break add FILE:LINE [--condition --hit --log] / break list /
  watch PATH [--rw --value --condition] / run|continue|step(into)|next(stmt)|out [--timeout] /
  pause / bt / locals [--frame] / regs [PATH] / bus [-n] / eval EXPR / threads / end / list;
  `--session ID`, `--json` (run commands + start: pssunit.stop/1 record + envelope, `start`
  adds test/seed, `exited` adds checks {passed,failed,skipped}). "dbg" added to
  protocol.SERVED (daemon-routed); help_cmd.OUTPUTS entries; docs/cli-reference.rst regenerated.
  BreakpointSet.add / WatchSet.add (+ DebugSession.add_breakpoint/add_watch) keep existing ids.
  Errors: rc 1, `pssc: error: ...` on stderr. tests/unit/test_devenv_dbg.py (+4 = 9). Unit 3475 + 1 baseline.
  Not checked yet: across real daemon client processes (P2.3d).
- 2026-11-18: P2.3d done (T2.3): tests/unit/test_devenv_dbg_daemon.py (2) runs every `pssc dbg`
  command as its own `python -m pssc` subprocess against a real daemon (tmp workspace = copy of
  examples/op_model/{pss,tests} + pyproject.toml; private PSSC_DAEMON_DIR; daemon stopped and
  killed in teardown). Session survives across clients (start / break add / run / bt / continue
  to exited / end, then "no session"); every stop record validates against pssunit.stop-1.json;
  `--timeout 0` returns `running` (or the stop if the engine was quicker) and the next run
  command finds the stop; two sessions by `--session` are independent. No fix needed: the daemon
  runs `cli.main` in its own process (daemon.py docstring reworded: "no fork"). 3 runs stable,
  ~1.2 s. Unit 3478 + 1 baseline.
- 2026-11-18: P2.3e done; P2.3 closed. docs/debugging.rst "Command-line sessions" (D6): what a
  session is and where it lives, a real text transcript on the WB DMA suite (captured from
  `pssc dbg` against a daemon on a tmp copy), the commands, `--timeout`/`running`, `--json` =
  pssunit.stop/1 with an example, errors (rc 1). Skeleton note updated. D4 already regenerated
  at P2.3c. D10 (agents page) not started yet (P5.13); `pssc dbg` goes in it then. The transcript
  is not checked by a test (D6 is not under T-D1).
- 2026-11-18: P2.4 opened, split into a–e (see the P2.4 item): a framing + `Connection` with
  initialize/launch/attach/configurationDone/threads/disconnect; b breakpoints, data
  breakpoints, exception filters, stack/scopes/variables/evaluate, stepping, events; c one
  session many clients (single waiter + listeners in `DebugSession`; DAP on a second daemon
  socket, a thread per connection; `pssc dap` stdio relay); d T2.4; e D6 "VS Code". Decision:
  `Connection` is transport-free (two binary streams) so tests drive it over `socket.socketpair`
  and the daemon and `pssc dap` only add the wire.
- 2026-11-18: P2.4a done. src/pssc/devenv/debug/dap.py: `read_message`/`write_message`
  (Content-Length framing), `Connection(rfile, wfile, registry=REGISTRY)` with `serve`/`handle`
  (`on_<command>`; a handler may return `(body, after)` to send events after its response;
  DebugError/KeyError/ValueError/TypeError → `success: false` + message), `initialize`
  (+ `initialized` event), `launch` (cwd, sources, test, seed, platform, timeout, stopOnEntry;
  chdir under a module lock), `attach` (session), `configurationDone` (entry stop event, or
  `continue` in a thread → `stopped`/`exited`+`terminated` events via `stop_events`), `threads`
  (cached while running), `disconnect` (ends a launched session; `terminateDebuggee` overrides).
  The entry stop record has no thread: the event uses the first stub thread.
  tests/unit/test_devenv_dap.py (5, socketpair client). Unit 3485 + 1 baseline.
- 2026-11-18: P2.4b done. dap.py `Connection`: `setBreakpoints` (source.path as given; the
  front end matches it to provenance names by path suffix; condition/hitCondition/logMessage →
  `set_breakpoints` specs; reply line = resolved line), `setExceptionBreakpoints` (filters =
  stub stop-on kinds error/check/sanitizer via new `DebugSession.set_stop_on`; filter list in
  CAPABILITIES, error on by default as in the stub), `dataBreakpointInfo` (dataId = register or
  field path, checked with watches.`resolve`), `setDataBreakpoints` (accessType read/write/
  readWrite → rw r/w/rw), `continue`, `next`/`stepIn`/`stepOut` (step kinds stmt/into/out),
  `pause` (stub `stop` while a run thread waits; a no-op when stopped), `stackTrace` (absolute
  source path from the new `Live.cwd`; startFrame/levels), `scopes`, `variables`, `evaluate`
  (result = str of the int). One run thread at a time (`Connection.busy`; a step or continue
  while it waits fails "pause first"); the thread waits again while the run is still going.
  Any handler exception is now a failed response (an EvalError killed the serve thread).
  Stub thread ids start at 0. Stepping out of the outermost frame runs to the end; `next` on a
  rewritten check stays on its line for a few steps. Tests +5 (10; pause test gates the stub's
  `continue` and sets `_pause` as be-bc's test_stop_while_running). Unit 3490 + 1 baseline.
- 2026-11-18: P2.4c split into c1 (listeners) and c2 (daemon socket + `pssc dap`); c1 done.
  frontend.py `DebugSession`: `add_listener`/`remove_listener`; `_waited` makes `resume` and
  `wait` the one waiter (a lock held from the stub request to the record; a second caller gets
  DebugError "the run is going (another client waits for it; pause first)"); every stop/exit
  record goes to the listeners, `resume` also sends `{"event": "continued", "thread"}` and `end`
  an exited record; listener exceptions are swallowed. New `waiting`/`going` properties.
  dap.py: a Connection listens from launch/attach until disconnect or EOF (`_join`/`_leave`,
  `_on_record`); its own run thread (`_runner`) skips the listener so its stop comes once;
  others' runs become `continued` + `stopped`/`exited`/`terminated`; `pause` while another
  waits only asks the stub to stop. Tests +2 (12; two clients + a `step` from the session as
  `pssc dbg step` does; one waiter). Unit 3492 + 1 baseline.
- 2026-11-18: P2.4c2 done. daemon.py serves DAP on `<socket minus .sock>.dap` (`dap_path`; not
  `*.sock`, which tests glob as command sockets): `_serve_dap` accept thread, a thread per client
  running `dap.Connection(...).serve()` on the default REGISTRY (shared with `pssc dbg`); no idle
  stop while a DAP client is connected; op `dap` replies the path. `Daemon.run` holds
  dap.`_CWD_LOCK` (as DAP `launch` does). New devenv/dap_cmd.py `pssc dap`: _entry.py relays
  stdin/stdout to the daemon's DAP socket (raw fds: a thread blocked in buffered stdin aborted
  the exit with SIGABRT), in-process `Connection` when PSSC_NO_DAEMON=1 or no daemon; cli.py
  registers it (in-process); docs/cli-reference.rst regenerated. Tests +2: test_devenv_dap.py
  (subprocess, in-process), test_devenv_dbg_daemon.py (`dbg start`, `pssc dap` attaches, entry
  stop, `dbg threads` meanwhile). Found: `pssc dbg bt` right after `start` fails "needs an int
  thread" (the entry record has no thread) - follow-up. Unit 3495 + 1 baseline.
- 2026-11-18: P2.4d done. T2.4: new tests/unit/test_devenv_dap_transcript.py plays initialize,
  launch, setBreakpoints, configurationDone, stackTrace, scopes, variables (Locals, Registers),
  continue, disconnect over a socket pair and compares every server message with the recorded
  one (session id and suite path masked; server `seq` 1..n; responses in request order; events
  initialized/stopped/exited/terminated in order, each after the request causing it; Registers
  checked by shape + a few values, ~70 entries). test_devenv_dbg_daemon.py: `_Dap` helper (the
  `pssc dap` subprocess) and `test_cli_and_dap_on_one_session_both_see_a_stop`: `dbg run` hits a
  breakpoint, the DAP client gets `continued` then `stopped` at the same line; a DAP `continue`
  runs to `exited`, after which `dbg run` says "the run has ended". Unit 3498 + 1 baseline.
- 2026-11-18 P2.4e done (P2.4 done): docs/debugging.rst "VS Code": `pssc dap` (stdio, relays to
  the daemon; in-process with PSSC_NO_DAEMON=1), launch.json (`pss` type, which P2.12 adds;
  launch: cwd/sources/test/seed/platform/stopOnEntry; attach: session), what the editor supports
  (bp condition/hit/log, register data breakpoints, exception filters error/check/sanitizer,
  scopes Locals/Component/Registers/Bus, evaluate/hover), CLI + editor share stops. No CLI option
  changed: `pssc help --rst` equals docs/cli-reference.rst. No doc test covers debugging.rst.
  Unit 3498 + 1 baseline.
- 2026-11-18 P2.5a done: P2.5 split into a `inflight`, b `rc-read`, c `unbound`, d `unconsumed-irq`
  (d may need P2.6: nothing delivers `ctx.raise_irq` to the model's `notify_irq` yet).
  sanitizers.py: `latches(model)` (channels with only try_put+try_get ops in `coros[*].chans`),
  `Sanitizers.chan` (stack of put src_refs per latch name), `Sanitizers.end` (finding per held
  latch, at the last untaken put). vm.py: `_Tee` feeds `chan` events, `EV_CHAN` watched when
  there are latches, `_counted` calls `end()` only when the test ran to its end and raises a
  `sanitizer` event per finding. T2.5a: LATCH fixture in test_pssunit_sanitizers.py (seeded
  missing release caught at the try_put line; clean, allowed, stale wake not flagged; 13 pass).
  D2 row + note in writing-tests.rst. Unit 3502 + 1 baseline.
- 2026-11-18 P2.5b done: `rc-read` is static + runtime. be-bc (uncommitted, own repo): lower
  `StmtExpr` sets `CoroCtx.discard_call` to the statement's call; `_reg_call` read branch records
  the IMPORT pc in `ctx.discards` (debug only) -> `CoroDescriptor.discards`; VM `_ev_discards` +
  `VM.discarded(frame)`; ops_orch `_access` adds `"discarded": True` to a register read's detail.
  `(void)r.read_val();` reaches bc as a plain call (the cast is lost before lowering), so it is
  flagged too; the escape is `@pssunit_pkg::allow {.what = "rc-read"}`. sanitizers.py
  `_rc_read` (a discarded read of a register with any field of kind RC; finding names the RC
  fields), "rc-read" in NAMES. T2.5b: RC fixture in test_pssunit_sanitizers.py (dropped value
  caught at the read line; used value, plain register dropped, allowed: not flagged; stream
  consumer alone; 16 pass). D2 row. Unit 3505 + 1 baseline; be-bc 319; release bytes unchanged.
- 2026-11-18 P2.5c done: `unbound` is runtime only, no be-bc change. An unplaced group's handle
  slot is 0 (checked: `regs.ctl` at offset 4 is accessed at addr 4), and `_set_handle` binds
  sub-groups at parent + offset, so an unbound access has addr == the register's table offset.
  sanitizers.py `Sanitizers.access` -> `_unbound` (once per reg-id per test, so a poll of it still
  reaches poll-budget; the register is placed anyway); "unbound" in NAMES (dropped from the
  `allows` leftover set). False positive: a group really placed at 0 (escape: allow). Component
  state is fresh per `run_test` (an unplaced test after a placed one is still unbound). T2.5c:
  UNBOUND fixture (RC with t_unplaced/t_unplaced_allowed) in test_pssunit_sanitizers.py (19 pass).
  D2 row. Unit 3508 + 1 baseline.
- 2026-11-18 P2.5 closed, P2.5d deferred to P2.6b: grep of `notify_irq`/`raise_irq` in src/ and
  be-bc finds no delivery -- `Ctx.raise_irq` (testing/vm.py) only appends `Session.irqs`, emits a
  `notify` host event and calls `platform.raise_irq`; the model's `notify_irq` is never run, so
  "consumed" has no signal. P2.6 split into a (irq delivery, spike), b (`unconsumed-irq`), c (hook
  annotations + compile-time paths), d (hook entries + hooks.py binding), e (hook library), f
  (helpers as library hooks), g (T2.6 + D3).
- 2026-11-18 P2.6a IRQ delivery done. Spike: no re-entry needed. pyvm is a cooperative scheduler
  (interp/vm.py `_drain` runs ready frames to quiescence; an ACCESS/IMPORT is a synchronous host
  call inside `run_frame`), so a second entry "while the test is suspended" is just another frame:
  `Ctx.raise_irq` builds a frame of the handler's function entry in the root component (as
  bc_session `_call_root` does: `sched.new_frame`, `cobj`, `set_comp(frame, 0)`) and
  `sched.ready`s it; it runs when the raising thread next yields (a blocking get, a wait, a SPIN
  yield), or before the drain ends if nothing yields -- an interrupt taken between instructions.
  Same component object, so the handler's `try_put` wakes the test's `get`. Naming (R-3, by the
  run): `vm.Session(irq_handler=)` takes an entry symbol, a bare function name (first function
  entry so named), a coroutine index or None; default `vm.IRQ_HANDLER = "notify_irq"`;
  `Session.irq_entry` is the resolved coroutine (None: not delivered). runner `_build_image` lowers
  the root's `notify_irq` (new `catalog.function_of`: root or nearest base) so `pssc test` gets it.
  No `--irq`/`[tool.pssunit] irq` option yet (default only). `notify` event is now
  `{"irq", "delivered"}`. Note: examples/op_model/tests/target_cfg.pss sets HAVE_EVENT_WAIT=false,
  so the WB DMA test image has no `notify_irq` (compile-if'd out); P2.6g needs an event-wait
  profile. T2.6a: tests/unit/test_pssunit_irq.py (4: blocked test woken, handler runs without a
  waiter, run-named handler, none -> delivered false). Unit 3513 + 1 baseline; be-bc 319.
- 2026-11-18 P2.6b `unconsumed-irq` done. Since delivery is immediate (P2.6a), "never delivered by
  the end" reduces to: raised with no handler (`irq_entry` None) and a platform with no
  `raise_irq` of its own (a platform that has one takes it; test_pssunit_vm's IrqBoard). "Handler
  ran but consumed nothing" is skipped (no signal). Located at the raising site without a be-bc
  change: `Sanitizers.irq` queues it, the next `access`/`import` event (`_Tee`; session now watches
  EV_IMPORT when sanitizers are on) is the access/import in progress and stamps its src_ref
  (`Sanitizers.site`); leftovers at `end`. One finding per irq number per test. NAMES +
  `allows` (dropped the special case). T: IRQ fixture in test_pssunit_sanitizers.py (22 pass);
  test_pssunit_irq no-handler test now (0,1,0). D2 row (table column widened). Unit 3516 + 1
  baseline; `-k doc` 756 pass + 7 skip.
- 2026-11-18 P2.6c hook declarations done (spike + implementation in one step). Spike: ast2ir
  keeps `on_*` annotations (with `loc`) on the mock's function nodes; a fixture's `mock_c`
  instance is in `model.components`; `model.registers` exists at lower time, so names resolve in
  `bc_session.lower` (debug only) -> `model.hooks`. New `src/pssc/testing/hooks.py`: `Hook`,
  `reg_names` (moved from fake `_find_hooks`, same spellings: instance path, path without
  indices, `<group type>.<reg>`), `declared(ctx, model)` (mock instances by `base_chain`, own +
  base functions, redeclaration wins); unknown reg / missing field / non-target function =
  `Refusal` located at the annotation. pssunit_pkg.pss: `on_write {reg, field=""}`,
  `on_read {reg}`, `on_event {name}`. Note: the mock's functions get no entries yet with
  `functions=["*"]` (only dev_c/grp_c fns did) -- P2.6d's job. T: tests/unit/test_pssunit_hooks.py
  (5). D2: rows + note in writing-tests.rst annotations table. Unit 3523 + 1 baseline; `-k doc`
  758 + 7 skip; tests/pssunit 67.
- 2026-11-18 P2.6d hook code units done. Why no entries: be-bc `entry_function_symbols` (what
  `"*"` expands to) skips functions with an empty body, and `pssc test` names its functions, so
  hooks need their own: `hooks.entry_symbols(ctx, root)` (mock types reached from the root by
  component fields, own + bases) is added to `functions` in `bc_session.lower` (debug and
  release; an explicit symbol lowers fine with an empty body; no be-bc change). `Hook.entry` =
  `<instance type>::<fn>`. `hooks.bind(session)` (called by `vm.Session` after the platform's
  `attach`) appends each write/read hook to `RegisterFake._hooks`; the callback readies a frame
  of the entry in the mock instance (as P2.6a's irq handler), so it runs at the next scheduling
  point and does not change the access. A hook with parameters is a ValueError until access_s
  (P2.6e); on_event hooks are not run (P2.6e). T: test_pssunit_hooks.py (8). D2: writing-tests.rst
  note. Unit 3526 + 1 baseline; `-k doc` 758 + 7 skip; tests/pssunit 67.
- 2026-11-18 P2.6e split into e1 (library calls) and e2 (schedule_after/on_event, access_s).
  e1 done: pssunit_pkg `poke_field_at(index, reg, field, v)`, `raise_irq(irq = 0)`,
  `state_get(key)`, `state_set(key, v)`. bc imports take <= 2 scalars and no strings, so:
  state_get/set join check_rewrite `DOUBLES`; `poke_field_at` is a new `LEADS` kind (statement
  only: `check_operand(index)` then `double_call(id, v)`; `_double_call` takes the operand).
  Found: a qualified `pssunit_pkg::f(...)` is `ExprRefUnresolved(f)`, which be-bc does not lower
  as an import; `_Rewriter.callee` accepts it and `double()` rewrites it to `self.f`.
  `raise_irq` -> `RunState.irq` (= `Ctx.raise_irq`; in vm `_PKG_FIRST`); state in
  `RunState.state` (per test). `RegisterFake.poke_field_at`: the register named (hooks
  `reg_names`, kept as `fake._names`) whose first `[n]` is index (index 0 = an unindexed one).
  T: test_pssunit_hooks.py (10). D2: writing-tests.rst hook-library table. Unit 3528 + 1
  baseline; `-k doc` 758 + 7 skip; tests/pssunit 67.
- 2026-11-18 P2.6e2 done: `access_s` gains `int index` (-1 = not in an array). A register
  hook takes no args or one `access_s` (entry params `a.addr,a.width,a.data,a.is_write,a.index`,
  hooks `_access_param`); `_starter` fills `frame.args` from the fake's `Access`.
  `schedule_after(int delay, string name, int arg)`: check_rewrite `LEADS` (1 string, v);
  `_double_call` now pops the lead for any `LEADS` kind; -> `RunState.schedule` =
  `Ctx.schedule_after` -> `hooks.schedule`: each `on_event` hook named `name` gets a frame
  (`[arg]` if it has one param, error if >1) on `vm.sched.schedule_at(now + delay)`; `_drain`
  runs timed frames, so it runs within the same `Session.call`. No hook of that name = ValueError.
  Found: an int passed to a host import arrives unsigned (-1 -> 2**64-1). T: test_pssunit_hooks.py
  (12). D2: writing-tests.rst. Unit 3530 + 1 baseline; `-k doc` 758 + 7; pssunit 67.
- 2026-11-18 P2.6f done: the three helpers stay host imports (they take a register path at run
  time; PSS hooks are bound by annotation at compile time, so a PSS version would need dynamic
  hook registration -- not worth it, same behaviour). Each call now adds a hook to
  `RegisterFake._hooks` (`_script`; listed in `_scripts`, dropped by `reset`), dispatched by
  `_run_hooks` with two new ops: `access` (before the access: fail_on_access) and `value` (maps
  the value read: status_sequence, one hook per register with its queue; complete_after, one
  per call, removes itself). `countdown`/`forbidden`/`_scripted` gone. T: test_pssunit_fake.py
  `test_scripts_are_library_hooks` (13). No doc change. Unit 3531 + 1 baseline; pssunit 67.
- 2026-11-18 P2.6g done (P2.6 complete): T2.6 `tests/unit/test_wb_dma_hooks.py` (2) runs the
  design §7.2 mock (`pssunit_pkg::`) in a tmp copy of the WB DMA fixture with
  `HAVE_EVENT_WAIT = true` via `pssc test`; the typo `wb_dma_ch_regs_c.cs` is a located error at
  the annotation. Fixes it needed: (1) `hooks.entry_symbols` also gives the default IRQ handler
  (`vm.IRQ_HANDLER`, `notify_irq`) an entry -- `pssc test` names only its test functions, so
  `raise_irq` was never delivered there (P2.6a gap); (2) `RegisterFake.poke_field_at` treats
  aliased paths (`ch[2].regs.csr` / `regs.bank[2].csr`) as one and prefers placed registers.
  Limit found: virtual time moves only when every thread waits or SPIN-yields, so a polling
  driver (plain `yield`, the suite's own `HAVE_EVENT_WAIT = false`) never reaches a
  `schedule_after` event; documented. D3: platform-doubles.rst "PSS reaction hooks" replaces
  the "not implemented" note (2 PSS blocks verbatim from the test). Unit 3534 + 1 baseline;
  `-k doc` 761 + 7 skip; pssunit 67; sphinx clean.
- 2026-11-18 P2.7 split a-e (§6.3). P2.7a code in, its test not yet written: be-bc
  `CoroDescriptor.cov` `(kind, pc, scope, path, src_ref, implicit)`; procedural.py `_lower_at`,
  `_arm` (debug: a NOP per arm; `if`/if-chain always get an else arm, `implicit`), scopes for
  called forms (`fn_qname`), inlined bodies, `$comp_init` (type qname, `<kind>[n]`), exec blocks
  (`<kind>[n]/<i>`). events.py `EV_BRANCH`/`branch`; vm.py `_ev_paths`/`_ev_arms`; stmt detail
  gains `path` (+ `scope`). be-bc 299 pass; pssc unit 3536 + 1 baseline.
- 2026-11-18 P2.7a done: test `tests/unit/test_bc_cov_lowering.py` (9; in pssc, not be-bc, since
  it lowers PSS source): if/implicit else, if-chain, match arms and the arm taken per value;
  stmt `path`/`scope`; called form scoped `cov_c::helper`; release has no NOP / empty `cov`;
  paths equal and lines +3 after blank lines above. A function's paths start at its body
  (`2/then/0`). be-bc 299; pssc unit 3546 + 1 baseline.
- 2026-11-18 P2.7b done: `src/pssc/testing/coverage.py` `Coverage` (a TraceSink; `WATCH`,
  `forward=`, `add_model` reads every coroutine's `cov` so unreached points show 0, `report()`
  version 1: statements/arms by ID + summary). be-bc driver: an empty cov scope becomes the
  coroutine's name (action exec blocks: `a_t/body/0`). Comp-init points are per type (one ID
  across instances). T2.7 part 1 `tests/unit/test_pssunit_coverage.py` (5): hand counts, unreached
  points, IDs across moved lines, action exec + init points, forwarding. pssc unit 3553 + 1 baseline.
- 2026-11-18 P2.7c split c1 (`--cov`) / c2 (boundary). c1 done: `pssc test --cov PATH` (cli.py,
  cmd.py `_cmd_test`; `--no-debug` refused); runner `_run_item(cov=)` gives each test a fresh
  `Coverage(image.model)`, `Result.coverage` = its report, `run(coverage=)` merges
  (`Coverage.merge`), so `-j` matches `-j1`; help OUTPUTS `pssunit.coverage/1`; test-command.rst
  options row; cli-reference regenerated. Tests: test_pssunit_coverage.py +3 (8). Unit 3556 + 1 baseline.
- 2026-11-18 P2.7c2 done: coverage.py `boundary(ctx, items, scopes)` -> predicate on a point's
  scope; `Coverage.report(keep)`. Under test = `--cov-scope COMP` (repeatable; cli.py, cmd.py) plus
  fixtures' `@pssunit_pkg::under_test {.comp}`: closure over derived types, sub-component field
  types, then bases; an action counts if its component does. None named: all but excluded. Always
  excluded: std packages (pssunit_pkg, std_pkg, addr_reg_pkg, executor_pkg, sync_pkg, sml_pkg, and
  std_libs/stdlib files), tests' root comps + bases, fixture_c/mock_c derived, test actions,
  `@pssunit_pkg::nocover` (new, empty annotation; on a function, action or component). Statement-level
  `nocover` NOT done: PSS annotations go on declarations only. `--cov-scope` without `--cov` refused.
  Docs: test-command.rst row (and fixed the `--targets` row's column), writing-tests.rst table row,
  cli-reference regenerated. Tests: test_pssunit_coverage.py +4 (12). Unit 3560 + 1 baseline.
- 2026-11-18 P2.7d done: register/field coverage from the RegisterFake. Session sets
  `fake.cov = events` when the events sink has `register_access` (a Coverage); the fake's
  `_decoded` calls `cov.register_access(reg, op, data, cleared)` per register access (`cleared`: bits
  an RC read / W1C write cleared). `Coverage.registers`: path -> {comp, reads, writes, fields{name:
  {kind, width, bins}}}; bins `bit<i>=0|1` (toggle), enum items (else `zero`, `max`), `cleared` for
  RC/W1C. `add_model` lists every register (0 hits); report() adds `registers` + summary
  `registers`/`bins` {total, hit} (format version kept 1: keys only added); `merge` sums. Boundary
  does not filter registers. bc_session `_field` adds `enum` (item -> value) to an enum field.
  Tests +4 (16). Unit 3564 + 1 baseline. Not checked: `pssc test --platform fake --cov` end to end.
- 2026-11-18 P2.7e done: D7 skeleton docs/coverage.rst (layers, stable IDs, boundary, report
  `pssunit.coverage/1`, Python use; covsight mapping waits for P2.8), in the index toctree, linked
  from test-command.rst; code blocks verbatim from test_pssunit_coverage.py, checked by
  tests/unit/test_doc_coverage.py (9). WB DMA smoke (`pssc test @model.f . --platform fake --targets
  none --no-ctrf --cov c.json` on a copy of examples/op_model): 21 pass; statements 85/104, arms
  20/30, registers 26/69, bins 988/4964 -- the end-to-end register path of P2.7d works. It showed a
  boundary gap: an action test's exec scope is the bare action name (`abort_a/body/0`), which
  `boundary.keep` did not resolve, so test actions were counted; keep now maps a bare name to the
  one type of that short name (BOUNDARY fixture gained action test `t_act_a`). Unit 3575 + 1 baseline.
- 2026-11-18 P2.8a (S3) done: covsight-core (`covsight.core`, pure Python, already importable from
  packages/python with `covsight` CLI) has every write P2.8 needs. A spike (/tmp, not kept) built a
  DB with `MemFactory.create()`: file handle, DU `dma_c` (`SourceT.OTHER`, attribute language=PSS),
  INSTANCE, BLOCK + STMTBIN with source lines, BRANCH + BRANCHBIN, a covergroup, two TEST history
  nodes with `TestData` (status OK/ERROR, seed, date) and attributes, `record_test_association`;
  `NcdbWriter().write` → `NcdbReader().read` kept nodes, attributes and associations
  (`get_test_coverage_api().get_coveritems_for_test`); `NcdbMerger().merge` adds a MERGE node and
  keeps associations; `covsight show summary|tests` read it (1 pass, 1 fail). Not checked: flat bin
  index order for nested covergroup bins (DFS, per covsight-core docs). P2.8 split b-e above.
- 2026-11-18 P2.8b done: src/pssc/testing/cov_ucis.py `to_ucis(report, comp=comp_of)` → MemUCIS,
  `write_cdb(report, path)` via `NcdbWriter`. DU_MODULE (+ `language=PSS`) and one INSTANCE per
  component type (`comp_of`: scope before the last `::`; a scope with no `::`, i.e. a type's init
  blocks or an action of `pss_top`, is its own unit; `comp=` overrides), BLOCK per scope under the
  instance, STMTBIN per statement (name = path), BRANCH (name = branch path relative to the scope)
  under the BLOCK, BRANCHBIN per arm, `IS_BR_ELSE` on implicit arms; file handles per source file,
  SourceInfo line from the report. tests/unit/test_pssunit_cov_ucis.py (4: round trip counts,
  names, lines, else flag, action/init scopes, mapper). Unit 3581 + 1 baseline.
- 2026-11-18 P2.8c done: registers in `to_ucis` (`_registers`, `_fill`): COVERGROUP per register
  type under the INSTANCE of the holding component's type (shared with code coverage; unit
  `registers` if the report has no type), COVERINSTANCE per register path, COVERPOINT `access`
  (bins `read`/`write` = access counts) + one per field, CVGBIN per value bin; the covergroup's own
  coverpoints sum its instances. bc_session `_registers` now records `type` (register type) and
  `comp_type`; `Coverage._register` keeps both in the report (docs/coverage.rst schema list
  updated). test_pssunit_cov_ucis.py 6. Unit 3583 + 1 baseline.
- 2026-11-18 P2.8d done: `to_ucis(..., tests=[{name, status, seed, coverage, engine, platform}])`
  makes a HISTORYNODE_TEST per test (TestData status pass/skip/xfail→OK, fail/xpass→ERROR,
  error→FATAL; seed; attributes `pssunit.status|engine|platform`) and records, for every point the
  test's own report hit, `record_test_association(hist_idx, flat_bin_index(...), hits)`; flat
  indices via covsight `ncdb.dfs_util.flat_bin_index` after the tree is built (a register bin counts
  on its cover instance and on the type-level covergroup bin). `pssc test --cov x.cdb` writes the
  `.cdb` (any other suffix: JSON unchanged); help text + `help --json` OUTPUTS (`ucis-ncdb`,
  format `cdb-file`, added to docs/schemas/pssc.help-1.json enum); cli-reference regenerated.
  Image generation attribute not recorded (nothing exposes it per run). test_pssunit_cov_ucis.py 8.
  Unit 3585 + 1 baseline; `-k doc` 777.
- 2026-11-18 P2.8e done: merge is covsight's own (`covsight merge a.cdb b.cdb -o m.cdb`, NcdbMerger;
  adds a MERGE history node, keeps both runs' tests and associations); no pssc wrapper (P2.9 `pssc cov
  merge` may add one). Found: a `-k` run's image lowered only the selected test functions, so its
  `.cdb` had fewer points, and covsight took the cross-schema path, which doubled the test nodes
  (21+4 → 50) and dropped all associations. Fix: `Collection.found` (every discovered test, cmd.py
  `collect`); runner `run` lowers all of `found`'s functions per component when `coverage` is given.
  T2.8 `test_wb_dma_suite.py::test_cov_cdb_runs_merge_in_covsight` (suite + `-k prio` → merge, no
  schema warning, same code total, covered grows, tests = 21 + prio ones, via `covsight show
  summary|tests`). D7: docs/coverage.rst "Coverage databases" (commands, tree, history nodes,
  merge). Unit 3587 + 1 baseline; `-k doc` 778; Sphinx clean for coverage.rst.
- 2026-11-18 P2.9a done: `src/pssc/devenv/cov_cmd.py` `pssc cov gaps REPORT` (registered in cli.py
  after dap): `load`, `gaps` (statements, arms, never-accessed registers, unseen bins of accessed
  registers as `<reg>.<field>:<bin>`), `Sources` (relative `file` looked up under `--source-root`
  dirs, cwd, report dir; provenance spells paths relative to a filelist's dir, e.g. WB DMA
  `../pss/...` needs `--source-root examples/op_model/tests`), text and `--json`
  (`pssc.cov-gaps/1`, docs/schemas/pssc.cov-gaps-1.json, help OUTPUTS); cli-reference regenerated.
  LCOV/Cobertura writers already exist in covsight (`analysis/lcov.py`, `cobertura.py`): no
  request. tests/unit/test_devenv_cov.py 6. Unit 3595 + 1 baseline.
- 2026-11-18 P2.9b done: cov_cmd `load` reads a `.cdb` (`load_cdb`: NcdbReader, the cov_ucis tree
  back to `pssunit.coverage/1`: BLOCK = scope, STMTBIN name = path, BRANCH/BRANCHBIN → arm
  `<branch>/<arm>`, IS_BR_ELSE → implicit, COVERINSTANCE → register with `access` → reads/writes and
  field coverpoints → bins; `comp` and field kind/width are not kept). Fixed cov_ucis: the file
  handle was the basename only (covsight's mem backend drops `workdir`), now the whole path.
  `pssc cov report [--json] REPORT`: `totals` = summary per kind + per scope (statements, arms, file)
  + per register (reads, writes, bins); schema `pssc.cov-report/1` (docs/schemas/pssc.cov-report-1.json,
  help OUTPUTS); cov-gaps schema `comp` may be null. test_devenv_cov.py 11. Unit 3600 + 1 baseline.
- 2026-11-18 P2.9c done: `pssc cov lcov|cobertura REPORT -o FILE [--source-root DIR]` (JSON or `.cdb`):
  cov_cmd `write_code` = `to_ucis` (in memory) → covsight `CodeCoverage` → `write_lcov`/`write_cobertura`
  (our STMTBIN/BRANCHBIN with file+line are read as is). `located`: a relative `file` found under
  `--source-root`, the cwd or the report's dir is written relative to the cwd; else as the report spells
  it. help OUTPUTS: lcov (new format `lcov-file` in pssc.help-1.json) and cobertura (`xml-file`).
  test_devenv_cov.py 14 (LCOV/XML parsed: sign() then arm BRDA 0, `return -1` DA 0; .cdb = JSON
  tracefile). cli-reference regenerated. Unit 3603 + 1 baseline.
- 2026-11-18 P2.9d done: `pssc cov merge REPORT... -o OUT` (cov_cmd `merge`): all `.cdb` into `.cdb` =
  covsight `NcdbMerger` (each run's tests kept); otherwise each input `load`ed and summed by
  `Coverage.merge` → `.report()`, written as JSON or (`-o x.cdb`) `write_cdb` without tests.
  `pssc cov diff --base REF REPORT [--kind] [--context] [--source-root] [--json]`: `changed_lines`
  (`git rev-parse --show-toplevel`, `git diff -U0 REF --` hunks `+c,d` → {root-relative file: lines};
  untracked files not included), `on_changed` keeps gaps whose `Sources.find` file and line are in it.
  New schema `pssc.cov-diff/1` (docs/schemas/pssc.cov-diff-1.json: gaps doc + `base`, `changed`).
  help OUTPUTS for both. test_devenv_cov.py 19 (+5: merge json/cdb-keeps-tests/errors, diff on a tmp git
  repo, bad base → 2). cli-reference regenerated. Unit 3608 + 1 baseline.
- 2026-11-18 P2.9e done (P2.9 done): T2.9 in tests/unit/test_wb_dma_suite.py (module fixture `cov_run`:
  the suite once with `--cov c.json`, once with `c.cdb`; helper `_pssc`): gaps `--source-root .` files all
  exist and some gap has non-blank context; `report --json` summary equal from JSON and `.cdb`; lcov `SF:`
  files exist; `merge` of a run with itself doubles every statement/arm hit (json and cdb). +5 tests.
  D7: docs/coverage.rst "Reading a report" section (gaps/report/lcov/cobertura/merge/diff, console
  examples from the WB DMA run); covsight section says `pssc cov merge` does the same. cli-reference
  regenerated (no change). `-k "doc or wb_dma"` 798 + 7 skip; Sphinx clean. Unit 3613 + 1 baseline.
- 2026-11-18 P2.10 split a-e (trace writer + fan-out sink; `--trace all`; `--trace fail` re-run;
  `pssc trace show`; T2.10 + D6). Traces are be-bc NDJSON v2 with `file`/`line` added per event.
- 2026-11-18 P2.10a done: src/pssc/testing/trace.py `TraceWriter` (be-bc JSONL v2, header + test/seed,
  per-event `file`/`line`), `session_sink`; runner `_session_for`/`run_one(trace=)`.
  tests/unit/test_pssunit_trace.py (2). Unit 3617 + 1 baseline.
- 2026-11-18 P2.10b done: `pssc test --trace off|fail|all --trace-dir` (cli.py; cmd.py checks debug
  image, `--trace-dir` alone is an error); runner `run(trace=(mode, dir))` → `_run_item` →
  `_run_image` (split out; run_property got `trace=`); file = trace.py `trace_path` (`::`→`.`,
  default `TRACE_DIR` .pssunit/traces); `Result.trace_file` in `test.end` (events schema) and the
  failure card (`  trace: PATH`). help OUTPUTS `zbc-trace/2` `ndjson-file`; test-command.rst table;
  cli-reference regen. `fail` parses but traces nothing yet (c). test_pssunit_trace.py (5).
  Unit 3620 + 1 baseline.
- 2026-11-18 P2.10c done: `--trace fail` — runner `_run_item` settles first, then a failing test
  re-runs once via `_traced` + `run_one` at `failing_seed` (else the run seed), no coverage, no
  regression record; `Result.trace_flaky` when the re-run's status differs from the first run's raw
  status (card: "(... flaky)", `test.end` `trace_flaky`, events schema). Works under `-j` (in the
  worker). test_pssunit_trace.py (8). Unit 3623 + 1 baseline.
- 2026-11-18 P2.10d done: src/pssc/devenv/trace_cmd.py `pssc trace show TRACE [--kind K]... [--coro N]
  [--at IDX | --line FILE:N] [--context N] [--source-root DIR] [--json]`; index = event `seq`; `--at` on an
  unlocated event (call/return) uses the last located event before it (`from`); `--line` matches FILE
  by path suffix, `indexes` before --kind/--coro. Sources (cov_cmd `Sources`): --source-root, cwd,
  the trace dir's grandparent (run cwd for .pssunit/traces), the trace dir. Schema
  docs/schemas/pssc.trace-show-1.json, help OUTPUTS, cli-reference regen. test_devenv_trace.py (7).
  Unit 3632 + 1 baseline.
- 2026-11-18 P2.10e done: T2.10 test_wb_dma_suite.py `test_trace_fail_keeps_only_the_failures_trace`
  (seeded `driver_wrong_field`; failures there are sanitizer verdicts, so the test takes check or
  sanitizer); D6 docs/debugging.rst "Traces" (subsection; "Replay and time travel" still to come);
  cli-reference regen. Seen: WB DMA traces have no `check` events though cards count checks (checks
  there are not `rt.check_*`? unverified). Unit 3633 + 1 baseline. P2.10 done.
- 2026-11-18 P2.11 split a1/a2/b1/b2/c/d1/d2/e (plan item *Parts*). a1 done: pssparser grammar
  `| function_decl` in `action_body_item` (src/PSSParser.g4); linker needed nothing (scope, inherited
  calls, `super.arrange()` resolve; abstract prototype links). pssparser suite 9318 → 9322 (new
  tests/python/parsing/test_action_functions.py); pssc unit 3633 + 1 baseline; pssc now stops at
  ast2ir "'FunctionDefinition' in action ... is not supported yet".
- 2026-11-18 P2.11a2 done: LINT412 `action-function` (portability, decl, 2026.2) in pssparser
  lint/catalog.py CORE_EMITTED; raised by TaskBuildSymbolTree::reportActionFunction (called from
  visitFunctionDefinition and visitFunctionPrototype when the scope is an action). Regenerated
  catalog.json, docs/lint-rules.rst + markers.rst, core-codes.lock, ts lint-parity fixture; new
  tests/lint/action-function/{l01,ok_01}.pss, test_the_pin list, 2 tests in test_action_functions.py.
  pssparser suite 9327 pass; pssc unit 3633 + 1 baseline. `pssc parse` prints no parser warnings at all
  (LINT403 is silent too), so LINT412 shows only through pssparser/lint, not pssc (follow-up).
- 2026-11-18 P2.11b1 done: ast2ir `_BODY_ELEMENTS['FunctionDefinition']` accepted in {'component',
  'action'}; the function lands on the action's DataTypeClass.functions, field refs lower to
  `self.<f>`. A FunctionPrototype in an action is still dropped silently (row None) -- b2 handles it.
  New test_function_in_an_action_is_on_the_action (tests/unit/integration/test_type_body_registry.py);
  `pssc parse /tmp/af/a.pss` OK; unit 3634 + 1 baseline.
- 2026-11-18 P2.11b2 done: ast2ir `_body_prototype` records `function void f();` in an action as an
  abstract Function (body [], metadata abstract); new src/pssc/targets/action_inherit.py (reuses
  comp_inherit `_copy`/`_hidden`/`_rewrite`): `complete(ctx)` called at the end of
  AstToIrTranslator.translate copies inherited functions into each derived action (override wins,
  `inherited_from`), `super.f()` -> private `_pss_super_<base>_f` (`super_impl`), `declared()` keeps
  the as-written list; errors "line N: action 'd_a' does not implement function 'act' (...)" and
  `super.f()` of an abstract f. Exec blocks/fields untouched. tests/unit/test_action_inherit.py (9);
  unit 3645 + 1 baseline.
- 2026-11-18 P2.11c done: ast2ir marks action functions `metadata["action_function"]`; zuspec-ir-core
  pss_lower no longer takes them for constraint blocks (`is_pending_constraint`) and lists non-abstract
  ones in ScenarioModule.functions as `<action>::f`; zuspec-be-bc lower/procedural.py inlines `f()` /
  `super.f()` in action code on the action's object (`_Fn.action`, `_action_function`, `_comp_code`),
  debug and release alike; recursion is a LoweringError. tests/unit/test_bc_action_functions.py (6,
  incl. `pssc test`). be-bc 319, ir-core 226; unit 3652 + 1 baseline; matrix pyvm 358 pass, 125 xfail.
- 2026-11-18 P2.11d1 done: C/C++ op-model targets render no exported actions (`supports_entries`
  only py and SV), so d1 = export_action + Python, d2 = SV + matrix. export_action.py `AF_PREFIX`,
  `EntryPoint.helpers` (non-abstract action functions as `_pss_af_<entry>_<f>`, `_InComponentScope`
  rewrites `f()`/`super.f()`); validate_calls `ops_for_call`, expr_types `_entry_helpers`, py walker
  `entry_helpers` + backend `emit_entry`. tests/progseq/test_op_model_entry.py +4 (template method with
  `super`, value fn, recursion renders and runs — unlike bc, which refuses it — helper names, attribute
  read refused); 17 pass. Unit 3648 + 1 baseline (+ dbg_daemon 4); compliance op-model-py 86 pass,
  28 xfail, no golden moved.
- 2026-11-18 P2.11d2 done: sv/lower_progseq.py `_entry_defs` renders `entry.helpers` with `_routine`
  as `protected` tasks (args, `output status` for a value); `_BodyEmitter.add_helpers` makes them
  callable (`own_fn_names`, `op_fns`, `out_calls`). test_op_model_entry.py
  `test_action_functions_run_on_op_model_sv` (Verilator: template method, `super`, value fn, recursion).
  No matrix file (matrix drives `run()` only; C/C++ refuse entries). Unit 3648 + 1 baseline; matrix
  `-k "sv or pyvm"` 820 pass, 150 xfail, 1 pre-existing W64 XPASS; compliance sv/py 210 pass.
- 2026-11-18 P2.11e done (P2.11 done): D12 = new page docs/action-functions.rst (toctree after
  writing-tests): template-method suite, semantics (scope, virtual by copy, `super.f()`, abstract),
  LINT412 (portability tier: not in the default selection, `pssparser --select LINT412`), per-target
  table (bc inlines, recursion = ERROR; op-model-py/-sv `_pss_af_<entry>_<f>` helpers, recursion works;
  C/C++ render no exported action), attribute reads refused in exported actions, PSS WG note.
  tests/unit/test_doc_action_functions.py (3): runs the page's suite (2 PASS), op-model block ==
  `_AF_MODEL`. Sphinx clean but the usual 'pss' lexer warnings; unit 3653 + 1 baseline.
- 2026-11-18 P2.12a done: pssc side of VS Code. Present already: catalog, events, `pssc dap`/`<sock>.dap`,
  launch/attach, Registers scope, register data breakpoints, `dbg list --json`, `cov lcov`. Added
  `test.end.failures` [{file,line,col,kind,message}] (runner `_failure_locations`, report
  `event_test_end`, events schema, test-command.rst). tests/unit/test_pssunit_failure_locations.py (2);
  unit 3656 + 1 baseline (dbg_daemon excluded). P2.12b (the extension) = request `vscode-p212`.
- 2026-11-18 P2.13a done: `src/pssc/testing/slice.py` (`Slicer`: textual name-reach slice over
  action/component-fn/package-fn spans + "rest" hash + engine version; `.pssunit/results.json`
  record), `pssc test --changed` (cmd `_select_changed`/`_record_results`; record beside
  `LAST_FAILED`, written once `--changed` used), catalog `calls` = `{units, slice}`; `collect`
  clears catalog `_LINES` (spans were stale in the daemon after an edit). Docs: test-command.rst,
  test-internals.rst, catalog schema, cli-reference.rst regen. tests/unit/test_pssunit_changed.py (4);
  unit 3662 + 1 baseline (dbg_daemon excluded; it passes alone, 4).
- 2026-11-18 P2.13b done: `pssc test --changed --base REV` (cli `--base`; cmd `_select_changed_since`;
  slice `base_slices`: sources at REV via `git show` into a temp tree with the same layout, translated,
  sliced; absent file = empty, file outside the work tree = as now, REV not translating = run all;
  not a work tree / bad REV / `--base` without `--changed` = exit 5; no record read or started).
  `rest_hash` names files relative to `source_root(paths)` so hashes are path-independent. Adding an
  annotated test re-runs all (its annotation line is "rest"). Docs: test-command.rst, cli-reference.rst
  regen. test_pssunit_changed.py (6); unit 3664 + 1 baseline; dbg_daemon 4 alone.
- 2026-11-18 P2.13c done: `pssc test --watch` (cli; `src/pssc/testing/watch.py` `loop`/`snapshot`/
  `round_argv`/`main`). The loop is client-side (`_entry.main` routes `test ... --watch`; each round
  `client.run(argv + --changed)` → daemon, else in-process `cli.main`), not in the daemon: the daemon
  replies once per request, so a daemon-side loop could not stream rounds. `cmd_test` loops in-process
  for `cli.main([... --watch])`. Checked by hand through a real daemon (edit op_b → round 2 ran t_b only).
  Docs: test-command.rst, cli-reference.rst regen. test_pssunit_changed.py (9); unit 3668 + 1 baseline.
- 2026-11-18 P2.13d done: T2.13 = test_wb_dma_suite.py `test_changed_reruns_only_the_edited_operations_tests_through_the_daemon`
  (WB DMA copy with pyproject.toml root, PSSC_DAEMON_DIR, PSSC_NO_DAEMON unset; asserts the daemon's root;
  `--changed` runs all 21, then none; an edit to `wb_dma_ch_c::check_completion`'s body re-runs exactly
  the tests whose catalog `calls.units` include it, then none). D4: docs/cli.md `pssc test` synopsis +
  `--changed`/`--base`/`--watch` rows. P2.13 closed. Unit 3669 + 1 baseline; dbg_daemon 4.
- 2026-11-18 P2.12b done: request `vscode-p212` returned (vscode-pss-support wpk/vscode-pss-test-explorer, ff571cc).
  Its e2e (`client/`: `E2E_TMP=/tmp/... E2E_PSSC_ROOT=<this tree> npm run test:e2e`, headless VS Code) passes
  against this tree, 7: T2.12a "lists the 21 op_model tests under their 2 fixtures", T2.12b "the Debug profile
  stops at a breakpoint in a test body", T2.12c "attaches to a CLI session stopped at a breakpoint and sets a
  register write breakpoint", plus Run, Coverage, source location, smoke. Gaps → P2.12c.
- 2026-11-18 P2.12c done (P2.12 closed): `pssc test` takes SRC-SPECs/IDs after options (cli.py `parse_argv`,
  used by `main` and watch.py; argparse `parse_intermixed_args` refuses subcommands) —
  test_pssunit_select.py `test_id_after_an_option_still_selects`; DAP `dataBreakpointInfo` resolves `name`
  under its `variablesReference` (Variables `register_path`: a Registers scope's component prefix, a
  register's own path for its fields) — test_devenv_dap.py `..._relative_to_the_variables_shown`.
  Gap #1 (dir form reads no ../pss) documented in docs/tutorial.rst. Unit 3671 + 1 baseline; dbg_daemon 4.
- 2026-11-18 Phase 2 exit met. T2.E = tests/unit/test_wb_dma_t2e.py: a WB DMA copy with `configure_channel`'s
  `sz.chk_sz = cfg.chk_sz;` seeded to `cfg.tot_sz`; subprocess-only `pssc` through the copy's daemon: `pssc test
  --json` fails only `wb_dma_routed_fx_c::hw_handshake_a` at its new check (examples/op_model/tests/hw_handshake_a.pss
  gains `check_field("dma.ch[2].regs.sz", "chk_sz", 16, ...)`: nothing checked chk_sz before); `pssc dbg start`,
  `watch dma.ch[2].regs.sz.chk_sz`, `run` stops (reason watch) at `regs.sz.write(sz)`, the line after the mutated
  one (a register watch stops at the write, not the assignment); `eval sz.chk_sz` 64 vs `cfg.chk_sz` 16.
  T2.12c: the extension e2e attach test (P2.12b). covsight: T2.8. Unit 3672 + 1 baseline (computed: 3671 + T2.E;
  doc/wb_dma/pssunit/devenv subset 1199 rerun after the example change).
- 2026-11-18 Phase 3 started. Owner: commercial-sim rerun deferred (exit is Verilator only; a P3.F rerun when one is
  installed), pyhdl-if installed from ~/projects/fvutils/pyhdl-if at P3.3a, fw-wb-dma bench copied into
  examples/fw_wb_dma_uvm/ (P3.10). P3.1a done: be-bc docs/spec/host-abi.md (handles, `pssvm_load` … `pssvm_free`,
  run status DONE/ACCESS/IMPORT/WAIT TIME|EVENT/STOP/ERROR, one pending request per call, non-blocking imports
  yield too, notify IRQ/CHAN, reload at a quiescent boundary refused on API-hash change, pyvm mapping table).
  be-bc 319 (`python -m pytest tests`).
- 2026-11-18 P3.1b: be-bc `interp/host.py`: `HostCall(vm, root, on_notify=, irq_entry=)` runs the call on its own
  thread that blocks at each yield; `run()` → `Request` (DONE/ACCESS/IMPORT/WAIT TIME|EVENT/ERROR; STOP not mapped
  yet: the debug stub still blocks the VM thread itself, P3.7), `complete(value)`, `notify(kind,id,value)` (applied
  on the VM thread at the next run; N_IRQ starts `irq_entry`), `result`, `end()` (cancels). `vm.host` (None = old
  path) is consulted in `ops_orch._op_import`/`_builtin` memory ops and `VM._drain` (WAIT TIME via new
  `Scheduler.peek_time`; WAIT EVENT replaces the deadlock error). `run_sync(call)` answers from vm.memory /
  import_provider; WAIT EVENT there = deadlock VMError. Construction yields = `construct-yield` VMError.
  T3.1 7 tests; be-bc 326; matrix `-k pyvm` 358 + 125 xfail (unchanged).
- 2026-11-18 P3.1c: pssc sync API over the seam. `bc_session._drain(vm, frame)` (new) = `HostCall(vm, frame,
  construct=False)` + `run_sync` + `end()`; replaces the 4 `vm._drain` sites (`run`, `_call_root`, `Session.call`,
  `Session.run_entry`). be-bc: `HostCall(construct=False)` (no rebuild of a standing tree), `HostCall.fail(exc)`
  (a platform callback's error is raised in the VM thread, so located failures/sanitizers see it as before),
  `run_sync` looks up vm.memory/import_provider per request (the debug stub's `_WatchedMemory` is swapped in
  mid-run: without this 9 test_debug_* watch tests failed). T3.1 +2 tests (be-bc 328). Unit 3673 + baseline,
  ~96 s both with and without the seam (no per-call thread cost visible); dbg_daemon 4; matrix `-k pyvm` 358 + 125 xfail.
- 2026-11-18 P3.2a: `pssc build --image OUT [SRC] [--root C] [--generation N] [--no-sources] [--json]`
  (src/pssc/devenv/build_cmd.py: `build`, `read`, `summary`, `cmd_build`) writes the debug .zbc of one root
  (several roots -> exit 2 asking for `--root`). be-bc: new section `ZBC_SEC_META` = 20 (`zbc_metaent` {name,
  text}), `ZbcModel.meta: Dict[str,str]`, debug profile only (runtime refuses it); docs/spec/zbc-format.md
  "Host metadata (`META`)"; rt-core zbc_format.h regenerated; be-bc 332. META docs: `pssc.catalog`,
  `pssc.image` (schema pssc.image/1: root, setup, teardown, irq, allows), `pssc.registers` (map, hooks, checks).
  "zbc-file" output in help schema; cli-reference regenerated. T3.2 part: tests/unit/test_devenv_image.py 3.
  Unit 3678 + baseline; dbg_daemon 4. Pre-existing, not P3.2a: tests/progseq/test_op_model_order.py (2) fails
  `_refs > 300` (got 0): zuspec-ir-core's IRSerializer now cuts only cycles as `_ref`, so the count is ~0.
  Gap: side channels not serialized (see P3.2 *Progress*); a pickle of a WB DMA root's whole model is 350-560 KB
  and round-trips.
- 2026-11-18 P3.2b1: a read-back image runs in pyvm. build_cmd writes META `pssc.pyvm` (schema pssc.pyvm/1, be-bc
  `ABI_ID`, base64 pickle of: ZbcModel fields with compare=False except entries/sources/meta, attributes pssc set
  (allows, registers, hooks, checks, imports), each coroutine's compare=False fields, the TestItems, action field
  names by type). `read()` restores them onto the model; `load(path) -> (runner._Image, items, meta)` (refuses
  another ABI: BuildError "rebuild"). runner: `_Image.fields` (action field names when there is no IR context;
  `run_one` takes ctx=None then). T3.2 part: test_devenv_image.py 6 (read-back verdicts == in-memory on a
  function+action suite; ABI refusal). WB DMA probe: 21 tests, same verdicts from file and memory. Unit 3681 + baseline.
- 2026-11-18 P3.2b2: `pssc test --image FILE [IDS]`: testing/cmd.py `collect_image` (Collection ctx=None, filtered like
  `collect`), `runner.run(images=...)` pre-seeds the per-root image dict (and -j workers'), so nothing lowers; slicer
  None (no results record), gate skipped; replay line carries `--image`. Refused, exit 2: --changed/--base/--watch/--cov/
  --cov-scope/--targets and src-spec positionals; unreadable image exit 2. --lf, --trace, -j, --collect-only [--json] work.
  T3.2: test_devenv_image.py 13. D4 cli-reference regen; D11a: test-internals.rst image-file section, test-command.rst
  `--image` row + paragraph. Unit 3688 + baseline. P3.2 closed.
- 2026-11-18 P3.6a1: P3.6a split into a1/a2/a3 (P3.6 *Progress*). be-bc `docs/spec/generations.md` (steps, API check,
  refusal reasons api/bus/structure, JSON of a refusal); `interp/generations.py`: `api_of`, `api_diff` (entry:/import:
  symbols, added/removed/changed), `check_api` (equal hashes accept; unequal refuse with the diff, or `import:*` when
  only unserialized imports differ; unset hash → compare signatures), `ReloadRefused` (code -4, reason, changes,
  to_json). host-abi.md §Reload links it. T3.6 (a1): be-bc `tests/unit/interp/test_generations.py` 5; be-bc 337.
- 2026-11-18 P3.6a2: be-bc `interp/generations.py` `Generations` (spec steps 2, 3, 5): `call(symbol, *args, comp=)` →
  HostCall bound by ENTRY symbol (action: root_frame + construct; function: frame on the tree, args); `reload(model,
  cancel=)` = check_api (refusal leaves N and any staged image) → stage N+1 (fresh VM + tree; migration is a3) →
  apply when no call of N is live (at reload/call/`at_test_boundary`, which errors on a live call); result dict
  {old,new,changed (entry symbols whose coro code differs, `changed_symbols`),cancelled,applied} in `history`.
  Spec Status line updated. T3.6 (a2): test_generations.py 10; be-bc 342.
- 2026-11-18 P3.6a3: be-bc `generations.py`: N+1's tree is constructed under `_BusFence` (vm.host whose
  access/import_ raise) → `ReloadRefused("bus")`, N and staged image untouched; `structure_diff` (instance paths
  added/removed/changed = type or field list differs) in result `"structure"`; `Generations(strict=True)` refuses any
  change (`"structure"`); at apply, `_migrate` copies N's component-object slots into N+1 per unchanged instance path
  (channel tokens are component fields) → result `"migrated"`. No root args in pyvm (init coroutine takes none).
  Spec step 4 + Status updated. T3.6 (a3): test_generations.py 16; be-bc 348.
- 2026-11-18 P3.6b1: P3.6b split (b1 session, b2 daemon/CLI/docs). pssc `bc_session.Session.reload(model, cancel=,
  strict=)` → result dict (+ `unbound`), `generation`, `history`: checks via be-bc `check_reload` (new public; also used
  by `Generations.reload`), N+1 tree built by `construct_isolated` (new public, `_BusFence`), `migrate` (was `_migrate`)
  at apply; a reload inside a call (binding / stopped debugger) is staged and applied when the outermost `call` returns
  (`cancel` there refused: sync call); bindings re-bound on N+1. be-bc events: `reload` = `EV_RELOAD` (1<<11, in
  EV_HOST/EV_ALL, schema DEBUG kinds), raised to the session's sink when applied. T3.6 pyvm: tests/unit/test_devenv_reload.py
  (9: between calls, channel tokens, staged in a call, bindings, api/bus/structure refusals, event, no tree).
  Unit 3698 + 1 baseline; be-bc 348.
- 2026-11-18 P3.6b2 done: `pssc dbg reload [--strict] [--cancel] [--json]` (devenv/dbg_cmd.py, `_text_reload`;
  devenv/debug/sessions.py `Registry.reload`: re-collect the session's sources relative to its cwd, rebuild the debug
  image, `Session.reload`, then `vm.Session.reimport`); `Live` keeps session/sources/test/hosted. Refusal = exit 1,
  `--json` `{"error": ReloadRefused.to_json()}`. `bc_session.Session.run_entry` now counts as a call (`_depth`): a
  reload during a run (debugger stopped) is staged until the run ends. Fix: bc_session's apply renamed
  `_apply_reload` (vm.Session's `_apply` = mocks overrode it); vm.Session `_apply_reload` refreshes import names, irq
  entry, checks, spied coros, mock bindings. `vm.Session.used`/`reimport()` (importlib.reload after dropping the
  module's .pyc: same-size edits in one second kept old code). help_cmd.OUTPUTS `dbg reload`; cli-reference regen;
  docs/debugging.rst command list. Tests test_devenv_reload.py (11). Unit 3700 + 1 baseline; dbg_daemon 4; matrix pyvm 358.
- 2026-11-18 P3.6b3 done: coverage points were already keyed by scope/block path (not pc), so N and N+1 count on the
  same points; added: `Coverage.add_model` refreshes an existing point's file/line, and `bc_session.Session._apply_reload`
  calls `events.add_model(model)` when the events sink has one (new points, new lines). D9 docs/hot-reload.rst (index
  toctree; linked from debugging.rst `reload`); D11e be-bc docs/spec/generations.md "## pssc" section + status line.
  test_devenv_reload.py 12 (`test_coverage_spans_generations`). Unit 3702 + 1 baseline; dbg_daemon 4. P3.6 pyvm part done.
- 2026-11-18 P3.3a (Spike S4) done: GO. pyhdl-if 0.2.1 installed into packages/python from
  ~/projects/fvutils/pyhdl-if (`uv pip install`; ships libpyhdl_if.so, `hdl_if.share()`/`libs()`). Verilator 5.055
  `--binary` build of pyhdl_if.sv + `python -m hdl_if api-gen-sv` package + pyhdl-if's call_sv_bfm Wishbone BFM
  (+ an assoc-array memory slave): ~2 s build. SV calls Python `Test.run(bfm)`; it builds the WB DMA pyvm
  `bc_session.Session` (examples/op_model/pss, 0.35 s), and an async run loop over be-bc `HostCall` (run_sync with
  ACCESS answered by `await bfm.read/write`, clocked) runs `initialize` + `configure_channel` (6 accesses, values read
  back through the bus) and `probe_status`. Cost: raw pyhdl-if await = 14 us/access; one-access call through the
  VM = 305 us vs 142 us with no sim (run_sync) — the HostCall thread per call + handoffs dominate (switch interval no
  effect). ~3k VM accesses/s is enough for tests; a threadless HostCall is the optimisation if needed (P3.3c/P4).
  Spike files + commands: packet notes s4.md (not in the repo).
- 2026-11-18 P3.3b done: `src/pssc/share/sv/pssvm/pssvm_pkg.sv` + `pssc sv-pssvm-path [--file]` (cli.py
  `sv_pssvm_dir`/`SV_PSSVM_PKG`; pyproject package data `share/sv/pssvm/*.sv`; docs/cli.md, cli-reference.rst).
  **Boundary decision:** the run loop and testbench talk to an abstract SV class `pssvm_engine` (the host ABI as
  methods: call_start/run/request/complete/result/call_end/notify/error, call handles as ints, request flattened
  into class `pssvm_req`, values as 64-bit scalars, import args one 64-bit value each). Two backings:
  `pssvm_engine_dpi` (`+define+PSSVM_DPI`; `import "DPI-C"` of the native engine via a thin chandle adapter,
  `pssvm_dpi_*` — raw C pointer/len args can't be DPI-C) and, in P3.3c, a pyhdl-if class over the Python shim
  (`@hif.exp` methods; generated SV wrapper). So switching engines changes only the line creating the engine; raw
  DPI-C backed by Python was rejected (pyhdl-if can't implement arbitrary DPI-C imports in Python).
  `pssvm_platform_if` (virtual class: read8..64/write8..64, call_import, wait_time, dbg_wait, reset; defaults
  report, never stop), `pssvm_ctxt::call` = design §10.3 loop (ACCESS/IMPORT/WAIT TIME/WAIT EVENT on `notify`/STOP
  → `dbg_wait`/ERROR = `last_error` + `n_errors`, sim continues). Tests: tests/unit/test_pssvm_pkg.py (4: path,
  Verilator lint both backings, run loop with a scripted SV engine under `verilator --binary --timing`; skipped
  without verilator). Unit 3708 + 1 baseline.
- 2026-11-18 P3.3c done: `src/pssc/share/py/pssvm_dpi_shim.py` (`Engine`: pyvm behind the host ABI, int handles over
  be-bc HostCall on a `testing.vm.Session`; pssunit imports served in Python; with platform "fake" the register fake
  shadows the sim's bus values so back-door checks see them; u64 crosses pyhdl-if as signed 64; `PssvmEngine`
  = sync `@hif.exp` wrappers). `share/sv/pssvm/pssvm_hif_api_pkg.sv` (generated: `python -m hdl_if api-gen-sv -m
  pssvm_dpi_shim -p pssvm_hif_api_pkg`, shipped; a test checks it is current) and hand-written `pssvm_hif_pkg.sv`
  (`pssvm_engine_hif extends pssvm_engine`, `create(path, root_type, platform)`, `checks_failed()`).
  `pssc sv-pssvm-path --py` prints the PYTHONPATH (cli.py `sv_pssvm_pythonpath`; docs/cli.md, cli-reference.rst).
  Tests: tests/unit/test_pssvm_shim.py (5: Engine on the WB DMA image, errors, `--py`, api pkg current, Verilator:
  `bind_dma` + `t_csr_prio_bits` through `pssvm_ctxt` on an assoc-array memory, check passes, bad entry = located
  error, sim continues — T3.3 with a memory platform). Unit 3717 + 1 baseline (dbg_daemon 4 alone).
  `pssvm_runner` (`+pssunit=SEL`, reports) split to P3.3d.
- 2026-11-18 P3.3d done: shim `Engine.test_name(i)` (image function tests, not skipped, as `comp::fn`; "" past
  the end), `test_expect(i)` (`expect_error`), `hook("setup"|"teardown", i)` (META `pssc.image` setup/teardown);
  regenerated api pkg. `pssvm_engine` gained non-pure virtual `test_name/test_expect/hook/checks_failed` (default:
  no tests; `_dpi` unchanged), overridden in `pssvm_engine_hif`. `pssvm_pkg::pssvm_runner(ctxt)`: `+pssunit=SEL`
  (comma-separated `*`/`?` globs on `comp::fn` or `fn`) or all; per test: setup hooks, call, teardown hooks; pass =
  calls ok and `checks_failed` unchanged (inverted when an error is expected); prints `PSSVM-TEST: PASS|FAIL name`,
  `PSSVM-SUMMARY: n passed, m failed`; `run(output failed)`. State carries over between tests (until P3.8: now a fresh tree + platform `reset()` per test).
  docs/cli.md sv-pssvm-path. Tests: test_pssvm_shim.py 7 (+ Engine listing, Verilator runner: `*prio*` = 1 PASS,
  `t_csr_*,nope` = 2 PASS, all = one line per test). On the memory bus, all = 3 pass / 4 fail (polling tests hit the
  poll-budget sanitizer: no DMA behind the memory; expected, the real bench is P3.10). Unit 3719 + 1 baseline.
- 2026-11-18 P3.4a1 done: target `op-model-sv-vm` (src/pssc/targets/sv_vm/: `SvVmTarget(ProgSeqTarget)`,
  `SvVmBackend` keeps op-model-sv's constants/reg_model/api_types/import_api/exp_if/context_api sections and adds
  facade.py `<type>_exp_vm`, `<root>_vm_plat #(Timp)`, `<root>_root_vm #(Timp)::create` (runs `<root>::initialize`
  in the engine; must not yield)). pssvm_pkg `pssvm_set_default_engine`/`pssvm_default_engine`. call_legality
  registers `op-model-sv-vm` inheriting op-model-sv. WB DMA: API files byte-identical to op-model-sv's; package +
  TB calling `wb_dma_c_root_vm#(T)::create` lints in Verilator. Tests: tests/unit/test_sv_vm_target.py (9);
  test_call_legality builtins set updated. Unit 3731 + 1 baseline. Not run yet (P3.4a2); imports untested (WB DMA has none).
- 2026-11-18 P3.4a2 done: `pssc build --api --root <comp>` (build_cmd.py: every function an entry). Facade
  `create` first calls `pss_eng.api_check(spec)` (facade.py `api_spec`: sorted `;`-joined `comp::fn(p,s.f)[->r]`
  for the root ctor and every API function, PSS leaf names) and returns null with "image API differs: ..." on a
  mismatch; shim `Engine.api_check`, pssvm_pkg `pssvm_engine::api_check` (default ""), pssvm_hif_pkg override.
  test_sv_vm_target.py (13): Verilator runs create/pause_engine/ch(0).probe_status/ch(1).set_auto_restart over
  pyvm, accesses == pyvm's; spec ok on the image; tampered/other-config (`notify_irq` with HAVE_EVENT_WAIT on)
  reported. The facade must be compiled with the image's target_cfg (`--target-cfg`). Unit 3735 + 1 baseline.
- 2026-11-18 P3.4b done: `op-model-sv-vm --with-gen` keeps op-model-sv's whole package (files identical) plus the
  facade, and adds `<root>_root_sel #(Timp)::create` (facade.py `emit_root_sel`): `+pss_impl=vm` -> `_root_vm`,
  `gen` or none -> `_root`, else PSSVM-ERROR + null. T3.4 (plain SV TB; UVM bench at P3.10): test_sv_vm_target.py
  `test_pss_impl_vm_and_gen_same_bus_trace` (one binary, both runs: same accesses, times, status) and
  `test_with_gen_holds_both` (15 pass). D8 first part: docs/simulation.rst (index). cli-reference regenerated.
  Unit 3738 + 1 baseline. P3.4 done.
- 2026-11-19 P3.5a1 done: server-mode control channel without a sim. The daemon binds `<socket>.sim` next to
  the command and DAP sockets (`daemon.Daemon._serve_sim`) and does not idle-stop while a session is connected;
  `devenv/sim/registry.py` `REGISTRY` (`accept` reads `register`, `get(id|name)`, `list`, `SimSession.request`
  / `next_event`); `devenv/sim/serve.py` `Server` is the testbench end (reader thread queues requests, the sim
  thread answers with `poll`; `ping`/`info`/`end` local, other commands to an attached P2.1 stub, else
  `unsupported`). Wire = one JSON per line in the stub's request/reply/event shapes. Test:
  tests/unit/test_devenv_sim_channel.py (6). Unit 3748 + 1 baseline.
- 2026-11-19 P3.5a2 done: `+pssvm_serve[=NAME]` under Verilator. Shim `Engine.serve(name, top)` (a `Server`,
  no daemon start; `connect` takes env `PSSC_SIM_SOCKET` first) and `poll(timeout_ms)` (1/0/-1, -1 after `end`),
  pyhdl-if wrappers, `pssvm_hif_api_pkg.sv` regenerated; `pssvm_engine` virtual `serve`/`poll` (defaults: none),
  `pssvm_runner` serve mode instead of the tests (`while (poll(100) >= 0) #1;`, prints `PSSVM-SERVE: <id>` /
  `end`). Tests: test_devenv_sim_channel.py `test_verilator_serve` (RUNNER_TB as a subprocess: register, ping,
  info, `run` unsupported, end, exit 0) and `test_shim_engine_serve_and_poll` (8). docs/simulation.rst "Server
  mode". Unit 3750 + 1 baseline.
- 2026-11-19 P3.5b split (b1 testbench `run`, b2 `pssc sim list|run|log|end`; `attach` to P3.7) and T3.5
  defined under P3.5. P3.5b1 done: `serve.DEFER` + `Server.reply(seq, body|err)` (`Server.seq` = request
  being handled); shim `run {sel}` handler queues the selection, `poll` -> 2 while pending, `pending_sel`,
  `serve_test(name, passed)` (`test` event), `serve_done(n_pass, n_fail)` (reply `{passed, failed}`); SV
  engine virtuals + hif backing + regenerated api pkg; `pssvm_runner.serve_run`. Verilator link fix: pyhdl-if
  library ahead of the model archive (`-MAKEFLAGS LDFLAGS=-Wl,--no-as-needed,-L<lib>,-lpyhdl_if`; the
  with-gen sim failed to link without it) in both test builders and docs/simulation.rst. Tests:
  test_devenv_sim_channel.py 8 (two runs in one Verilator sim); shim/sv_vm/pssvm_pkg 26; unit 3750 + 1.
- 2026-11-19 P3.5b2 done (P3.5 done): `pssc sim list|run|log|end [--session ID] [--json]` (`devenv/sim_cmd.py`,
  routed in `_entry` like `daemon`; never starts a daemon). Daemon op `sim` (`devenv/sim/ops.py` `handle`)
  is answered on its own thread (`Daemon._answer`) so a long `run` does not block the command socket.
  `SimSession.history`/`log(since)`: numbered events, not popped (`next_event` still pops). `run` replies
  {session, passed, failed, tests} (the `test` events of that run), rc 1 on a failure. help OUTPUTS rows,
  cli-reference regen, docs/simulation.rst "Server mode". T3.5 in `test_verilator_serve` via the CLI;
  tests `test_sim_cli`, `test_sim_run_does_not_block_daemon` (no sim).
- 2026-11-19 P3.6c1 done: P3.6c split (c1 sim reload, c2 `--watch --platform sim:`, c3 DAP Apply change). Shim
  `Engine._on_reload` (serve handler `reload` {image?, strict, cancel}): `build_cmd.load` + `vm.Session.reload` +
  `reimport`; answered in `poll`, i.e. between runs (test boundary, applied at once); tests/hooks/image refreshed;
  refusal = error code `refused` with `detail` = `ReloadRefused.to_json()` (sim `error(..., detail)`,
  `Server.reply(detail=)`). Daemon ops `reload` (default timeout 600 s; updates the session's `image`);
  `pssc sim reload [IMAGE] [--strict] [--cancel] [--timeout]` (IMAGE made absolute; default = the loaded image,
  rebuilt in place); `--json` adds `refused`. help OUTPUTS row, cli-reference regen, docs/simulation.rst
  "Hot reload", hot-reload.rst link. No new hif method (api pkg unchanged). T3.6 sim part:
  test_devenv_sim_channel.py `test_shim_reload` (no sim; strict structure refusal) + `test_verilator_reload`
  (fail, fix, rebuild, reload, pass, same pid). Unit 3756 + 1 baseline; dbg_daemon 4.
- 2026-11-19 P3.6c2 done: `pssc test --platform sim:<s>` (src/pssc/testing/sim_platform.py `run`, called first
  in cmd `_cmd_test`): collect, build the root's image (all its tests) to `.pssunit/sim/<s>.zbc`, daemon `sim reload`
  when the sha256 differs from the last sent in this process (`_SENT`), `sim run` with the selected qnames; prints
  non-pass lines + "N passed, M failed (sim session X)"; exit 1/0, 3 on no daemon/refusal. Refused with sim:
  --json/--cov/--cov-scope/--trace/--image/--junit/--seeds; --changed ignored; one root only; CLI only (not
  pyproject). `_entry._sim_platform`: such a `pssc test` (and its --watch rounds) runs in-process, not in the daemon.
  Docs: test-command.rst row, simulation.rst "From pssc test", --platform help + cli-reference regen. Tests
  test_devenv_sim_channel.py `test_sim_platform`, `test_sim_platform_watch` (T3.6 watch, no sim; 14). Unit 3759 + 1.
- 2026-11-19 P3.6c3 done (P3.6 done): DAP *Apply change* = custom request `pssc/reload` {strict, cancel} in
  src/pssc/devenv/debug/dap.py `Connection.on_pssc_reload` (`handle` maps `/` -> `_`), `Registry.reload` on the client's
  session under `_CWD_LOCK`; reply = `pssc dbg reload --json` object; `output` event (applied/staged), `invalidated`
  {areas: all} when applied; refusal = failed response with `body.error` = `to_json()`. `initialize` lists it as
  `pssc: {requests: ["pssc/reload"]}`. pyvm dbg sessions only (no DAP over a sim until P3.7). Docs hot-reload.rst
  "From the debugger", debugging.rst VS Code list. Tests test_devenv_dap.py `test_apply_change_*` (2; 18),
  transcript CAPS updated. VS Code command: follow-up for vscode-pss-support. Unit 3761 + 1 baseline.
- 2026-11-19 P3.7 split into a1 (sim-side stub over the serve channel), a2 (`pssc sim attach`, T3.7), b
  (`pssvm_dbg_wait`, `+pssvm_dbg_on_fail`), c (`+pssvm_dbgsig`, sim time in events, `trace show --at`); see P3.7
  *Progress*. Design point: pyvm runs inside the DPI `run(h)` call on the SV thread, so a stop there freezes the
  whole sim (T3.7 holds by construction); `Server.poll` then never runs, so stub requests must be answered on
  the channel's reader thread.
- 2026-11-19 P3.7a1 done: be-bc `Stub` `attach {"entry": false}` (starts running; `_root` taken per stop, each
  host call has its own root; debug-stub.md); serve.py `Server(direct=)` handlers and stub requests answered on
  the reader thread (`_on_reader`); shim serve handler `debug` {on} (direct) = `Stub(session.vm, run=Event.wait)`
  + `attach_stub`, off detaches and ends it. Test test_devenv_sim_channel.py `test_shim_debug_stops_inside_a_call`
  (pc break at the test's entry; the TB's call is held while stopped; `threads` answered; continue ends the run;
  15 in the file). Unit 3762 + 1 baseline; be-bc 348.
- 2026-11-19 P3.7a2 done: `pssc sim attach` (ops `attach`, sim_cmd) = a `pssc dbg` session (sessions `Registry.attach_sim`)
  over `sim/remote.py` `RemoteStub` (terminate = `debug {on:false}`; image model via `build_cmd.load`); frontend
  `DebugSession.start(entry=False)` (state running); `sim end` ends its dbg sessions first; `dbg reload` refused on it.
  Runner `PSSVM-TEST:` line gets `@start..end` sim time. T3.7: test_devenv_sim_channel.py `test_sim_attach_dbg`,
  `test_verilator_attach` (same `$time` across a 0.5 s stop). Docs simulation.rst "Debugging a simulation", cli.md,
  cli-reference. Unit 3765 + 1 baseline.
- 2026-11-19 P3.7b done: `+pssvm_dbg_on_fail` — `pssvm_runner.run_test` (now a loop over `run_once`) prints
  `PSSVM-DBG: held NAME @t` after a failure and calls `pssvm_engine::dbg_wait(why)` (default -1; hif: shim
  `Engine.dbg_wait`: registers if not serving, `held` event, polls on the SV thread so sim time stands still, until
  a `resume {rerun}` request; 1 rerun, 0 go on, -1 no daemon/ended). Only the last run of a test counts. Package
  `pssvm_dbg_wait(why)` (default engine) for testbench code. `run` refused while held. `pssc sim resume [--rerun]`
  (ops `resume`, sim_cmd). "Hold" = sim held at the test boundary after the failure (not stopped at the failing
  check: rt_c checks are platform calls, no VM check event); attach/reload while held, rerun under the debugger.
  Test: test_devenv_sim_channel.py `test_verilator_dbg_on_fail` (held time == rerun start time). Docs
  simulation.rst "Holding a failing test", cli.md, cli-reference. Unit 3766 + 1 baseline.
- 2026-11-19 P3.7c1 done: sim time in every sim event. `pssvm_engine::set_time(t_fs)` (default no-op), package
  `pssvm_now_fs()` (`$realtime` scaled by `real'(1s)`, so any timescale); `pssvm_ctxt::call` sets it before
  `call_start` and each `run` step; hif engine before `poll`, `serve_test`, `serve_done`, `dbg_wait`. Shim
  `Engine.set_time`/`now_fs`; `serve.Server(clock=)` stamps `t` (fs) on every event in `_send` (stub events too).
  P3.7c split: c2 `+pssvm_trace` + `trace show --at <time>`, c3 `+pssvm_dbgsig` + docs D6. Tests:
  test_devenv_sim_channel.py `test_event_time`, `t` checks in `test_shim_engine_serve_and_poll`,
  `test_verilator_serve`, `test_verilator_dbg_on_fail` (held `t` == printed `$time` x 1e6). Docs simulation.rst.
  Sim set 80; unit 3767 + 1 baseline.
- 2026-11-19 P3.7c2 done: `+pssvm_trace=DIR` — runner (pssvm_pkg.sv `pssvm_runner.trace_dir`, `run_once`) calls
  `pssvm_engine::trace(dir, name)` (default ""; hif override; shim `Engine.trace`, api pkg regenerated) before
  setup and `trace(dir, "")` after teardown, prints `PSSVM-TRACE: file`. Shim points the session's `_Tee` sink at a
  `TraceWriter(clock=)` (testing/trace.py; adds `t` fs per event) and `vm.watch(session.watch | EV_ALL)` for the
  test, restores after. `pssc trace show --at` takes IDX or a time with a unit (trace_cmd.py `_at`/`AtTime`,
  `fmt_time`; last event with t <= time; `at_time` in JSON; rows show `@time`); schema pssc.trace-show-1.json
  (`at_time`, event `t`). Tests: test_devenv_trace.py `test_at_a_time_*` (2), test_devenv_sim_channel.py
  `test_shim_trace`, `test_verilator_trace`. Docs simulation.rst "Traces and waveforms", debugging.rst, cli-reference.
  Sim set 82 (+ trace file 9); unit 3771 + 1 baseline; dbg_daemon 4.
- 2026-11-19 P3.7c3 done (P3.7 done): `+pssvm_dbgsig` — module `pssvm_dbg` (end of pssvm_pkg.sv; `cur_op`,
  `cur_file`, `cur_line`, `seq`) mirrors package vars `pssvm_dbg_*` that `pssvm_ctxt::call` sets after each `run`
  step (plusarg read once, `pssvm_dbgsig_on()`) from new engine virtuals `cur_op/cur_file/cur_line(h)` (default
  ""/0; hif overrides; shim `Engine.where[h]` = [entry, file, line], `_at(h, src_ref)` from each request's
  `src_ref` via `model.prov`; api pkg regenerated). The place updates only where the call yields to SV (access,
  target import); builtins (checks) never yield, so show nothing. Test test_devenv_sim_channel.py
  `test_verilator_dbgsig` (target import at d.pss:8). Docs simulation.rst "Traces and waveforms",
  debugging.rst "Debugging in a simulation" (D6). Sim set 92; unit 3772 + 1 baseline; dbg_daemon 4.
- 2026-11-19 P3.8a done: `@pssunit_pkg::session` added to pssc's src/pssc/std_libs/pssunit_pkg.pss (after
  `teardown`). catalog.py `SESSION_ANNOTATION`, `session_hooks(ctx, comp)`, shared `_annotated`; `hooks()` now
  puts session functions first in set-up (pyvm: fresh tree per test, so each boots). build_cmd META IMAGE
  `session` list, `setup` without them; `load` puts them back at the head of `Image.setup`. Docs writing-tests.rst
  (table row, "Set-up and tear-down" paragraph). Tests test_pssunit_isolation.py
  `test_session_runs_before_set_up_on_every_fresh_tree`, `test_an_image_keeps_session_apart_from_set_up`.
  T3.8 written in §8. Unit 3774 + 1 baseline; sim set 92 + dbg_daemon 4.
- 2026-11-19 P3.8b+c done (P3.8 done): shim `Engine.fresh()` (session.construct again: new VM, same model,
  platform, doubles; ends stray calls; an attached debugger follows via new be-bc `Stub.rebind(vm)` in
  interp/debug.py), `HOOK_KINDS` incl. "session" in `load`/`_on_reload`; hif `fresh` (+ regenerated
  pssvm_hif_api_pkg.sv). pssvm_pkg.sv: engine `fresh()` (dpi: construct again), platform `can_reset()` (default
  1), runner `n_runs`/`n_skip`/`session_ok`: session fns once before the first test then shared state; else
  fresh + `reset()` before every run but the first; can_reset 0 → `PSSVM-TEST: SKIP name (platform cannot reset:
  one test per sim)`; summary adds ", N skipped" only when N > 0. Serve mode: count persists across `run`s.
  docs/simulation.rst "Several tests in one simulation". Tests test_pssvm_shim.py
  `test_verilator_fresh_tree_and_reset_between_tests` (T3.8), `test_engine_fresh_rebuilds_the_tree`.
  Unit 3776 + 1 baseline; sim set 94; dbg_daemon 4; be-bc 348; Sphinx clean. `Stub.rebind` has no own test.
- 2026-11-19 P3.9 done: `src/pssc/dvflow/devenv.py` `Image` (child `pssc build --image <rundir>/<name>.zbc`,
  output `pssVmImage` fileset; memento = source hash + options; `changed` = image md5 differs from the last)
  and `Test` (child `pssc test --image X --ctrf pssunit-ctrf.json [-k/--platform/--seed]`, PSSC_NO_DAEMON=1,
  `ctrfJson` output, status 1 + marker "tests failed" when rc 1); flow.yaml tasks + skill text;
  docs/simulation.rst "In a dv-flow flow". T3.9 tests/dvflow/test_devenv_tasks.py (5). tests/dvflow needs
  pssc's `dv_flow.mgr` entry point: pssc is not pip-installed in packages/python, so the suite was run with a
  stub dist-info on PYTHONPATH (all 36 pre-existing task tests otherwise fail "Failed to find package pssc").
  SimRun cannot yet take the image path from a `pssVmImage` fileset (plusargs are static).
- 2026-11-19 P3.10a done: bench copied into `examples/fw_wb_dma_uvm/` (fw-wb-dma `src/`, `tests/uvm/`, `flow.yaml`
  minus perf/bench/fw fragments, `uvm-dv-project*`; README.md, env.sh, .gitignore `rundir/` + `packages` symlink to
  the fw-wb-dma packages). Fix in the copy: fw-proto-wb moved `wb_proto_client_export`/`wb_mem_*` into
  `fw_proto_wb_spl_pkg` (exported only under its `use-fw-hdl` config) -> task `fw-proto-wb-spl` in tests/uvm/flow.yaml
  + import in env/wb_dma_env_pkg.sv. Verilator 5.055 (pssc packages) verilates but the C++ fails
  (`wb_dma_irq` "no unique final overrider for VlClass::to_string"; a minimal extends+2-interface-class case does
  not reproduce it) -> bench uses fw-wb-dma's Verilator 5.052. `dfm run tests` 14/14 (~60 s), `-D images=tlm` 6/6.
  T3.10 written (§8). Unit 3778 + 1 baseline.
- 2026-11-19 P3.10b1 done: bench model API from this repo's pssc. `examples/fw_wb_dma_uvm/src/pss/pssvm_gen.py`
  (pytask `OpModelSvVm`, task `pss.op-model-sv` in src/pss/flow.yaml; params root/package_name/enum_items; memento =
  argv + source + pssvm_pkg.sv hash) runs `$PSSC_PYTHON -m pssc compile ... -t op-model-sv-vm --with-gen
  --sv-enum-items verbatim` (env.sh sets PSSC_ROOT/PSSC_PYTHON). Adapter: `wb_dma_c_exp_if dma =
  wb_dma_c_root_sel #(wb_dma_c_imp_if)::create(this, base)`; renames *_if -> *_exp_if, import_if -> imp_if; struct
  args are `ref`, so `mk_cfg`/`cfg_for` results go via a local (10 sites). `dfm run tests` 14/14 (gen path, pssvm_pkg
  without PSSVM_DPI). pssc src unchanged.
- 2026-11-19 P3.10b2 done: bench image links the pyhdl-if shim. pssvm_gen.py (param `dpi`, default true) adds
  hdl_if's `share/dpi/pyhdl_if.sv` FileSet (HAVE_PYHDL_IF) ahead of pssvm_pkg.sv + pssvm_hif_api_pkg.sv +
  pssvm_hif_pkg.sv (copied from pssc share), and `hdlsim.SimElabArgs(dpilibs=[libpyhdl_if.so])` (a
  systemVerilogDPI FileSet reaches hdlsim's Verilator SimRun, which raises "DPI libraries not supported yet").
  No PSSVM_DPI (native-engine import `args[]` = Verilator 5.052 internal error V3Task.cpp:1171). `ldd simv` shows
  libpyhdl_if; `dfm run tests` 14/14. dv-flow reuses a pytask's cached output unless params change: rm its rundir.
- 2026-11-19 P3.10c1 done: adapter `+pss_impl=vm +pss_img=<zbc>` (pssvm_engine_hif), `+pss_bus_log`;
  pssvm_pkg.sv `pssvm_ctxt::call` DONE triggers `notified` (an irq hook's notify_irq call wakes EVENT waiters).
  examples/fw_wb_dma_uvm/vm_cmp.sh. tlm sw_copy passes on vm.
- 2026-11-19 P3.10c2 done: vm == gen bus accesses for all 14 bench tests (`vm_cmp.sh` tlm 6, wb 4, rtl 4). Cause
  of the two differences: two SV calls live at once (a transfer waiting, the irq pump's notify_irq) shared one
  be-bc scheduler, so notify_irq's call ran the transfer's thread (its CSR read landed inside notify_irq, and the
  pump missed the deassert edge -> one missing read per transfer; arb hung). be-bc interp/host.py: a HostCall
  started while another is live gets its own scheduler (shared clock and frame ids), swapped in on each resume.
  be-bc test_host_abi.py `test_a_second_live_call_runs_only_its_own_threads`; be-bc 349; unit 3778 + 1 baseline.
- 2026-11-19 P3.10c3 done: the bench flow runs the tests on pyvm. `pss.op-model-vm-img` (pytask
  `pssvm_gen.PssvmImage`: `pssc build --api --image`, outputs SimRunArgs `+pss_impl=vm +pss_img=<zbc>`);
  suites `uvm-universal-vm`/`uvm-hs-only-vm` + root `uvm.tests-vm` in tests/uvm/flow.yaml; cells run
  `tests/uvm/pssvm_case.py` (stock Verilator UVM case + PYHDL_IF_PYTHON/PYTHONPATH). `dfm run tests` 14/14 (gen),
  `dfm run uvm.tests-vm` 14/14.
- 2026-11-19 P3.E Phase 3 exit met (Verilator only): bench `dfm run tests` 14/14, `vm_cmp.sh` 14/14
  PASS, reload tests (sim + pyvm) 14 pass; hot reload is shown on the pssvm_runner TB, not inside the UVM
  bench. Unit 3778 + 1 baseline; be-bc 349. Commercial rerun deferred (P3.F).
- 2026-11-19 P4.1 done (opcode gap audit). Native engine = zuspec-rt-eng `share/rt/zbc_interp.c` (+ up-front
  refusals in `zbc_engine.c` `zbc_run`). Debug lowering adds no opcodes (statement markers are LINE-table
  entries; P1.4 uses `CALL`/`ARG`/`LD_ARG`, which C has). Gaps, now pinned by rt-eng
  `tests/test_ops_match_model.py` (`NATIVE_OP_GAPS`, `NATIVE_FLAG_GAPS`, `NATIVE_BUILTIN_GAPS`):
  - opcodes with no C case: `LD_COMP`, `ST_COMP` (component attributes), `SCOPE_ENTER`, `SOLVE_NODE`
    (activation solve scope); `PAR` is reserved (pyvm rejects it too). All but PAR refused before running.
  - flags refused: `INVOKE`+`INITED` (traversal initializers), `YIELD`+`SPIN` (channel blocking wait).
  - builtins: memory read/write 8..64 refused (no platform memory); `ERROR_MSG`/`FATAL_MSG` have no
    `ZBC_BUILTIN_*` and run as plain logged imports, so `error(msg)`/`fatal(msg)` do not halt; `MESSAGE`
    is logged, not printed.
  - not opcodes (later items): event hooks/stream (P4.4), host-bound `CALL` (P1.8; P4.3), async host
    imports (C `IMPORT` is synchronous with a per-fn return table; P4.3), sections LINE/LOCAL/SRC/IDENT/META
    unread (P4.4–P4.6), generations (P4.6), debug stub (P4.5).
  - bench image (`examples/fw_wb_dma_uvm`, wb_dma_c.zbc, debug profile) uses LD_COMP 84, ST_COMP 57,
    YIELD+SPIN 1, memory imports 51, ERROR 7: C refuses it today. P4.2 order for the bench: LD/ST_COMP, memory
    seam, SPIN, ERROR_MSG/FATAL_MSG; then SCOPE_ENTER/SOLVE_NODE, INITED.
  rt-eng conftest: dv-solve cmake now gets `-DPACKAGES_DIR` (CaDiCaL sources); rt-eng `pytest tests` 118 pass.
- 2026-11-19 P4.2a done (component state in C). be-bc image section COMP (kind 21, `zbc_comp`: n_slots,
  init_coro, n_inst, flags + u32 instance bases; `ZBC_COMP_F_NODE_INST` when a node targets a non-root
  instance); rt-core `zbc_format.h` regenerated (had drifted). rt-eng: reader loads COMP, `zbc_run` allocates
  the component object and runs init_coro to completion before the entry; `LD_COMP`/`ST_COMP` on instance 0
  only; NODE_INST (and COMP_INIT without COMP) refused (`ZBC_ERR_COMP_INIT`); new `ZBC_ERR_OOB_COMP` -26.
  pssc: pyvm images still restore components from META `pssc.pyvm` (overrides COMP on read-back); release
  snapshot `tests/unit/data/release_zbc_sha256.json` regenerated (only COMP differs, checked). rt-eng 124,
  be-bc 351, unit 3778 + 1 baseline.
- 2026-11-19 P4.2b done (error()/fatal() halt in C). rt-eng zbc_ops.h `ZBC_BUILTIN_ERROR_MSG`/`FATAL_MSG`
  (base+10/+11); zbc_interp.c `IMPORT` logs the call (arg0 = message entry) then halts `ZBC_ERR_RUNTIME` (-25),
  as pyvm raises. `NATIVE_BUILTIN_GAPS` now empty. Not done: the text. The message table (`ZbcModel.messages`:
  fmt, arg descriptors, local slots) is not in the image, so C cannot format message()/error() text or read
  fatal's status; split out as P4.2g. rt-eng `tests/test_engine_messages.py` (3); rt-eng 127.
- 2026-11-19 P4.2c done (memory seam in C). rt-eng zbc_interp.h `zbc_host_t` gains `mem_access(ctx, is_write,
  addr, nbytes, data)` + `mem_ctx`; zbc_interp.c `IMPORT` sends read8..64/write8..64 there (write data and read
  result masked to the width; the call is still logged), as pyvm `_builtin` uses `vm.memory`. zbc_engine.c still
  refuses memory builtins up front, but only when the host has no `mem_access`. Image bytes unchanged.
  rt-eng `tests/test_engine_memory.py` (3: differential vs `run_model(memory=Memory())`); rt-eng 130.
- 2026-11-19 P4.2d done (channel blocking wait in C). zbc_engine.c `drain` replaces both run loops: it counts
  thread runs ending in a no-progress SPIN yield (zbc_interp.c `YIELD` vs `resume_pc`, flag via
  `zbc_image_t.spin_stuck`) and, past the ready count, advances time or halts `ZBC_ERR_DEADLOCK` (-27), as pyvm
  `VM._drain`. YIELD+SPIN no longer refused (`NATIVE_FLAG_GAPS` = INVOKE+INITED only). Difference (guess, not
  seen): C's advance readies every thread at the next time, pyvm pops one; only interleaving could differ.
  rt-eng `tests/test_engine_spin.py` (3); activation's SPIN-refused test removed; rt-eng 132. be-bc docs
  memory.md / components.md "Native engine" updated.
- 2026-11-19 P4.2e1 done (traversal initializers in C). zbc_interp_task takes a last va_arg `start_pc` (all 7
  callers pass it; INVOKE passes `arg3` when `ZBC_F_INITED`, else 0; a start past the coro halts
  `ZBC_ERR_BAD_TARGET`). INVOKE+INITED no longer refused (`NATIVE_FLAG_GAPS` empty). rt-eng
  tests/test_engine_activation.py: refusal test replaced by 4 differentials (blocking, non-blocking+JOIN,
  defaults kept, bad start); rt-eng 135. Rest of P4.2e split: the activation table (`ZbcModel.activations`,
  interp/activation.py `ActivationTable`, 936 lines: nodes, sites, scopes, cones) is in memory only, not in the
  image, so SCOPE_ENTER/SOLVE_NODE need an image section first (e2a), then C commit/scope reset with no cones
  (e2b), then the cone solve in C (e2c); non-root instances (NODE_INST: node comp_rel/comp_slot) need the
  table too (e3).
- 2026-11-19 P4.2e2/e3 deferred to new item P4.10 (after P4.9): the activation runtime (be-bc interp/activation.py:
  lookahead footprints, pool claims, witness reuse) is far over two steps, and its cone constraints are IR
  `Constraint` objects compiled lazily to a dv-solve blob per enabled-constraint subset (`ActivationTable.blob`),
  so the image cannot carry them as-is; recommended encoding is one blob per cone with each constraint guarded by
  a pinned enable variable. Not on the bench's path (no SCOPE_ENTER/SOLVE_NODE there); C keeps refusing them.
- 2026-11-19 P4.2g done (message text in C). be-bc MSG section (kind 22; spec `zbc_msgtab`/`zbc_msg`/`zbc_msgarg`/
  `zbc_msgitem`; model.py `_serialize_messages`/`_parse_messages`), written when a model has messages or strings.
  rt-eng zbc_reader.c `open_msg`; rt/zbc_fmt.c `zbc_format_message` (C twin of interp/fmt.py);
  `zbc_host_t.message`/`msg_ctx`/`verbosity` (zero = NONE: hosts must set it; pyvm defaults MEDIUM). error/fatal
  deliver their text, then halt. Release golden (T-G1) regenerated: 114 images moved, and with messages/strings
  cleared all 596 hash as before, so MSG is the only change. be-bc tests/unit/format/test_msg_section.py (5),
  be-bc 364; rt-eng tests/test_engine_messages.py (13), rt-eng 145; test_release_zbc + test_pssvm_shim 10 pass.
- 2026-11-19 P4.2f folded into P4.4 (reasons under P4.2 *Progress*); P4.2 closed. P4.3 split into a..d
  (P4.3 *Split*). C reads no ENTRY/IDENT yet (zbc_reader.c reads CODE, CORO, OPLIST, SELECT, SOLVE, SPROB,
  COMP, MSG), so P4.3a starts there.
- 2026-11-19 P4.3a done (load / start / run in C). rt-eng `share/include/pssvm.h` + `share/rt/pssvm_host.c`:
  `pssvm_abi_version/load/construct/call_start/run/result/call_end/error/free`, plus interim
  `pssvm_set_host` (the synchronous zbc_host_t until P4.3b) and `pssvm_call_status` (ZBC status, halting op,
  end ticks; for differential tests). zbc_reader.c reads STRB/STRO, ENTRY, IDENT (`zbc_image_str`);
  zbc_engine.c exports `zbc_image_check` and `zbc_drain` (split out of `zbc_run`, unchanged behaviour). The
  engine owns a copy of the image and the component object (state carries across calls); each call owns a
  timebase. Interim limits: root instance only (`comp_path` ""), no constructor args, args are u64 leaves
  (no count check against `sig`), result 8 bytes unless `sig` says `"returns":null`; error JSON is
  `{message,status,halted_op}` (location fields: P4.3c). rt-eng `tests/test_pssvm_host.py` (16); rt-eng 161.
- 2026-11-19 P4.3b done (ACCESS / IMPORT yield in C). `zbc_host_t.yield` (`zbc_yield_t`, zbc_interp.h): with it
  set, `IMPORT` other than message/error/error-msg/fatal-msg posts the request, sets the thread BLOCKED and
  stays on the IMPORT; `pssvm_complete` stores the answer and puts the thread at the *front* of the ready queue,
  so it re-runs the IMPORT first (same order as inline answers; spawned-threads test). `zbc_drain_until` stops
  on a pending request; `zbc_drain` / `zbc_run` unchanged (yield NULL). pssvm.h: `pssvm_req_t` (status, kind,
  addr, size, data, reg_id/src_ref = 0xFFFFFFFF until P4.4 side tables, fn_id, name = NULL until images carry
  import names, args u64 leaves, blocking, has_ret), `pssvm_request`, `pssvm_complete` (read: size or 8 bytes;
  write: 0; import: 8 if has_ret else 0; else / nothing pending: E_STATE). `pssvm_run` with a request still
  unanswered returns it again without running. Each call gets its own host: `pssvm_set_host` now supplies only
  message callback, verbosity and import log (interim until P4.4 events); its mem_access/import_returns are
  ignored. Construction that yields fails (`construct-yield`). rt-eng tests/test_pssvm_host.py 21; rt-eng 165.
- 2026-11-19 P4.3c done (WAIT TIME, located errors). `zbc_yield_t` gains `wait_time`, `delay`, `err_coro`, `err_pc`
  and state `ZBC_YIELD_WAIT`; `zbc_drain_until` (now non-const `y`) under `wait_time` moves to the next timed batch
  and posts WAIT with the delay instead of running on (not after the run failed: a failed run winds down inline).
  pssvm calls set `wait_time` and `tb->running = 1` (else `zsp_timebase_wait`'s fast path skips a lone thread's
  time inline); `zbc_run` and construction unchanged (construction still advances time inline). `pssvm_req_t` +
  `on` (`PSSVM_ON_TIME`=1/`EVENT`=2), `delay` (ticks, ps) appended; `pssvm_request` shows a WAIT; `pssvm_complete`
  on a WAIT is E_STATE; the next `pssvm_run` goes on at the new time. `halt` (zbc_interp.c) records the first
  error's coro/pc; a call's ERROR JSON = `{message,status,halted_op,call,coro,pc,src_ref,file,line,col,thread}`
  (coro/pc null for deadlock; src_ref/file/line/col/thread null until P4.4). Not done: WAIT EVENT + `pssvm_notify`
  (all-threads-spinning is still `deadlock`). rt-eng tests/test_pssvm_host.py 26; rt-eng 170; golden+shim 10.
- 2026-11-19 P4.3d1 done (Python drives the C engine). rt-eng `src/zuspec/rt/eng/host.py`: `bind(lib)` (pssvm_*
  ctypes signatures), `Engine(lib, image)` (`call(entry, args, comp_path, seed)`, `free`, context manager),
  `NativeCall` with `HostCall`'s interface (`run`/`complete`/`fail`/`notify`/`request`/`result`/`end`/`over`,
  plus `status()`), `Request` with be-bc's fields (+`has_ret`), `NativeError(.error = pssvm_error JSON)`,
  `run_sync(call, memory, import_provider)` (WAIT TIME runs on; WAIT EVENT = deadlock; call always ended).
  Differences from pyvm: `fail(exc)` ends the call with ERROR carrying `exc` (C cannot raise it inside the model;
  run_sync re-raises it, so pytest sees the mock's own exception as on pyvm); `notify` raises HostStateError
  (P4.4); the C engine does not touch memory itself, so `memory=None` + an access is a NativeError. rt-eng
  tests/test_native_host.py (6: same result + same host-call log on pyvm and C with one Python mock); rt-eng 176;
  golden+shim 10. Next d2: pssc `testing/bc_session.py` (`Session`, VM at ~line 500, HostCall/run_sync ~line 318)
  runs on the C engine on request.
- 2026-11-19 P4.3d2 done; P4.3 closed. pssc `src/pssc/testing/native.py`: `engine_name` (`Session(engine=)`, else
  env `PSSC_ENGINE`, else `py`), `check_model` (refuses SCOPE_ENTER/SOLVE_NODE: `NativeUnsupported`),
  `needs_solver` (any SOLVE op), `engine_lib(solver)` (built once, cached in `~/.cache/pssc/rt-eng/<sha1 of
  rt-core+rt-eng C sources>/libpssvm[-solve].so`; solver variant links `dv_solve.get_libdirs()[0]`), `run_test`.
  `bc_session.Session.run_test` -> `_run_test_c` when engine is `c` (only run_test; `call`/`run_entry` stay pyvm).
  Decision: refuse, never fall back to pyvm (a pass on `c` ran on C): bindings and debug events (`watch`) refuse;
  `out` messages are dropped on C (interim). rt-eng: `pssvm_comp_read` (pssvm.h; copies the component object's
  slots) + `Engine.comp_slots()` so the ledger is read as on pyvm; zbc_solver.c dvs_* externs are weak (lib
  loads without libdv_solve; SOLVE then fails ZBC_SOLVER_FAIL); `build.build_shared_lib` prepends
  `-Wl,--no-as-needed` to link args (weak-only refs otherwise drop DT_NEEDED: 23 real-solve tests failed).
  D11d: be-bc docs/spec/host-abi.md "Python host for the C engine". tests/unit/test_native_session.py (6: same
  ledger + same mock log on py and c, failing mock = same exception, env choice, refusal, no solver linked);
  rt-eng 176; golden+shim+entries+native 26.
- 2026-11-19 P4.4 split a..f (under P4.4 *Split*). P4.4a done: be-bc spec sections REG (23, `zbc_regent`: pc,
  inst, flags `ZBC_REG_F_DISCARD`) and CHAN (24, `zbc_chanent`: pc, src_ref, channel/op StrIds, val/ok regs),
  provenance-family (runtime profile has none); `ZbcModel.to_container`/`from_container` write and read
  `CoroDescriptor.regs/discards/chans` (were in memory only); zbc_format.h regenerated; docs/spec/zbc-format.md.
  rt-eng: `zbc_image_t.regs/chans` checked in `zbc_image_open` (sorted, pcs in CODE, StrIds), `zbc_image_reg_at`
  / `zbc_image_chan_at` (binary search); `zbc_yield_t.pc` (global pc of the IMPORT); `pssvm_request` fills
  `reg_id` = REG `inst` (C runs at the root instance only, so no instance base is added: revisit with P4.10).
  rt-eng tests/test_side_tables.py (4); rt-eng 180; be-bc 368; golden+shim+native 16; unit unchanged.
- 2026-11-19 P4.4b done: rt-eng `zbc_image_open` reads FILE (path StrIds checked) and PROV (FileIds checked);
  `zbc_image_prov`, `zbc_image_file`, `zbc_image_src_ref(img, coro, pc)` (the instruction's src_ref, else its
  coroutine's, as pyvm `VM._locate`). LINE is not read: every instruction carries its own src_ref, which is what
  pyvm uses. `pssvm_request` sets `src_ref` from the requesting instruction (0xFFFFFFFF: none; the Python host
  maps that to 0, pyvm's "none"). `set_call_err` fills "src_ref", "file" (JSON-escaped, null for FileId 0),
  "line", "col" from the first error's coro/pc. rt-eng tests/test_side_tables.py +3; rt-eng 183;
  golden+shim+native 16; be-bc unchanged.
- 2026-11-19 P4.4c split c1..c3 (under P4.4 *Split*). c1 done: no binary form existed; be-bc
  src/zuspec/be/bc/trace/binary.py (magic `PSTRACE\0`, uvarint version, header map, length-prefixed records:
  seq delta, kind, coro, src_ref, detail, extras; tagged values with per-file string interning, sorted map keys),
  `BinarySink`, `read`/`load` (either form)/`to_ndjson`/`from_ndjson`; spec be-bc docs/spec/trace-schema.md
  "Binary form (`.pstrace`)". ~3.6x smaller than NDJSON on access records. be-bc tests/unit/trace/
  test_trace_binary.py (5); be-bc 373.
- 2026-11-19 P4.4c2 split c2a/c2b; c2a done: rt-eng include/zbc_events.h + rt/zbc_events.c (`zbc_ev_sink_t`
  writing the binary form; `zbc_ev_new`/`zbc_ev_delete` for ctypes hosts; `zbc_ev_watch(mask, coro|-1)`);
  `zbc_host_t.events`; zbc_interp.c raises stmt (LINE_STMT table, `zbc_image_stmts_at`), call/return
  (frame ids as pyvm `Frame.id`) and message. pssvm.h `pssvm_set_events(e, sink)` (wins over
  pssvm_set_host's host). rt-eng tests/test_events.py (7): zbc_run and pssvm events equal pyvm
  `run_model(events=MemorySink(), watch=)` on a recursive-call and a message model, ignoring seq and stmt
  path/scope (no COV in those models); per-coroutine mask; mask 0 = no output. rt-eng 190; be-bc 373;
  golden+shim+native 16. Interim `pssvm_set_host` note kept (import log still needed until c2b).
- 2026-11-19 P4.4c2b done: zbc_interp.c raises `access` (rw, width bits, addr, data, reg_id/discarded from
  the REG table, `ev_access`), `import` (user imports only, after the answer: `ev_import`; name null, mocked
  false: the image has no import names) and `chan` (CHAN table NOP at loop top, after stmt: value only with
  no ok or ok true). Same code path for zbc_run and pssvm (event written when the answer resumes the
  IMPORT). zbc_events `zbc_ev_null`. rt-eng tests/test_events.py +5 (import with returns, access plain and
  with registers/discard, pssvm answered access+import, chan put/try_get ok/not ok): all equal pyvm.
  rt-eng 195; be-bc 373; golden+shim+native 16. `pssvm_set_host` kept (no users in pssc/shim; only way to
  set pssvm verbosity), its note says events now carry message/import log.
- 2026-11-19 P4.4c3 done (P4.4c done): rt-eng tests/test_events.py `test_both_engines_write_the_same_trace`
  (6 cases: fact recursion, message, import with returns, access plain and with registers/discard, chan),
  mask = every kind C raises (stmt|call|access|import|message|chan) at once; pyvm through `BinarySink`, C its
  own bytes; `_canon` re-encodes both (seq from 0, stmt path/scope dropped, no header): byte-equal, extras
  included. `_chan_model` shared with the chan test. A pssc-compiled differential needs an events hook on
  rt-eng `host.Engine`: moved to P4.4d/P4.8. rt-eng 201; golden+shim+native 16.
- 2026-11-19 P4.4d done: devenv/trace_cmd.py `load` reads NDJSON or binary, `_locate` resolves `src_ref` via
  the image's PROV (`--image`, header `image`); docs debugging.rst + cli-reference.rst regenerated. rt-eng
  host.py `Engine.trace` (+ `EV_OUT_FN`, zbc_ev_* in `bind`); pssc testing/native.py `run_test(events, watch)`,
  `_emit`, `ENGINE_C_EVENTS`; bc_session `_run_test_c` refuses only Coverage. Tests: test_devenv_trace.py
  (+2), test_native_session.py (+2: pssc-compiled py/C trace differential, coverage refused), rt-eng
  test_events.py `test_the_host_engine_writes_a_trace`. rt-eng 202; repo trace/native/golden/shim/docs 764.
- 2026-11-19 P4.4e done: C WAIT EVENT + `pssvm_notify`/`pssvm_set_irq` (rt-eng pssvm.h, pssvm_host.c
  `take_notes`; zbc_engine.c `zbc_drain_until` posts WAIT on EVENT where it would deadlock when
  `zbc_yield_t.wait_event`). rt-eng host.py `Engine.set_irq/notify`, `NativeCall.notify`; run_sync keeps
  the deadlock error. be-bc host-abi.md "Notify" updated. Tests rt-eng test_native_host.py +3 (py/C alike
  for an IRQ wake; repeated WAIT EVENT; bad handler / CHAN error). rt-eng 205; repo golden/shim/native/trace 29.
  Unit run after P4.4d: 3790 pass + 1 baseline.
- 2026-11-19 P4.4f done (P4.4 closed): the platform side of a run is recorded. Read data (`access` r
  `data`) and import returns (`import` `ret`) were already events on both engines; added a `notify` event
  for each host-ABI notification as a run takes it (be-bc interp/host.py `HostCall._ev_notify`; rt-eng
  pssvm_host.c `ev_notify` in `take_notes`): `{irq|chan: id, value, delivered}`. be-bc trace/events.py
  `EV_PLATFORM` (`"platform"` in `mask_of`) and `platform_side(events)` (read/import/notify tuples, equal
  across engines). Fixed a P4.4e bug: `take_notes` did not pass `start_pc` to the IRQ thread (read garbage;
  passed by luck). Docs: be-bc trace-schema.md "The platform side", host-abi.md "Notify". Tests rt-eng
  test_native_host.py +2 (`test_both_engines_record_the_same_platform_side`,
  `test_a_notification_with_no_handler_is_recorded_undelivered`). rt-eng 207; be-bc 373; unit 3790 + 1
  baseline; dbg_daemon 4. pssc `--trace all` records it (EV_ALL); no `--trace platform` choice and no
  platform time yet (P5.1).
- 2026-11-19 P4.5 split a..d (under P4.5 *Split*): a stop in C is a host-ABI yield (`PSSVM_STOP`, reserved in
  host-abi.md), so the engine stays single-threaded and the host serves the stub between `pssvm_run` calls.
  a stops (breaks, pause, step) in `zbc_interp_task`; b inspection; c the line-JSON protocol over a socket,
  checked by replaying be-bc test_debug_stub.py's cases against C; d pssc daemon/DAP on the C engine.
- 2026-11-19 P4.5a done: the C engine stops at breakpoints, on pause and after a step (stmt/into/out), all-stop,
  as a `PSSVM_STOP` yield. rt-eng zbc_interp.h `zbc_dbg_t`/`zbc_dbg_stop_t`, `zbc_host_t.dbg`; zbc_interp.c
  `dbg_check` (before stmt events; per-frame `dbg_pass_pc` skips the check on re-entry at a checked pc: resume,
  IMPORT answer, wait), `halt` marks the step thread done; zbc_engine.c drain returns on STOP; pssvm.h/pssvm_host.c
  `pssvm_dbg_enable/set_breaks/pause/resume/stop` (run while stopped = continue); host.py `Engine.dbg_*`,
  `NativeCall.dbg_stop/dbg_resume`, STOP request. Doc: be-bc host-abi.md "Debug stops in the C engine".
  Tests: rt-eng tests/test_debug_stop.py (11); conftest `_Host` mirror got `dbg`. rt-eng 218, be-bc 373.
- 2026-11-19 P4.5b1 done: a stopped C engine lists its threads and frames and reads a frame. zbc_interp.c keeps
  the live frames in `zbc_dbg_t.frames` (linked at frame init with dbg on, unlinked at `halt` and when a FIRST
  join cancels a sibling's thread; pssvm `call_host` resets it per call); `zbc_dbg_threads/frames/frame` fill
  `zbc_dbg_frame_t` (id, thread id, coro, pc, depth, call depth, thread state running/ready/waiting, base,
  regs/locals/args pointers). pssvm.h `pssvm_dbg_threads/frames/frame` (0 unless stopped); host.py
  `NativeCall.dbg_threads/dbg_frames/dbg_read_frame` (raw slots; names from the image are the host's, part c).
  A caller's pc is past its CALL (as pyvm). Component fields stay `pssvm_comp_read`; back-door reads are the
  host's memory. Doc: be-bc host-abi.md (after "Debug stops"). Tests: test_debug_stop.py +3; rt-eng 221,
  be-bc 373, pssc test_native_session + test_devenv_trace 19.
- 2026-11-19 P4.5b2 done: the C engine stops on access watches and on errors. `zbc_dbg_t` has `watches`
  (`zbc_dbg_watch_t`: id, rw mask, addr, size, value/mask) and `stop_on_error`; a watched bus access, after
  it is done (a read has its value, as pyvm's `_WatchedMemory`), leaves a pending stop (`zbc_dbg_t.pend`)
  that the next debug check shows: reason 4 watch, hits, src_ref of the access, and is_write/size/addr/data
  in `zbc_dbg_stop_t`. An error in `halt` (the run's first) with stop_on_error stops there (reason 5, status
  + op; pssvm fills `message`), frames live; the resume halts with it (interp_locals_t `dbg_err`). Stops share
  `dbg_post_stop`. pssvm.h `pssvm_dbg_set_watches` (`pssvm_watch_t`), `pssvm_dbg_set_stop_on`
  (`PSSVM_STOP_ON_ERROR`; default off in C, the stub turns it on as pyvm's default); host.py
  `Engine.dbg_set_watches/dbg_set_stop_on`, `dbg_stop()["detail"]` as the stub's `stopped` detail. Tests:
  test_debug_stop.py +5; rt-eng 226, be-bc 373, pssc test_native_session + test_devenv_trace 19.
- 2026-11-19 P4.5c split (survey, no code): the pyvm stub is put on a socket by pssc, not be-bc (the sim
  shim's `_on_debug` makes a `Stub(vm, run=done.wait)` and `attach_stub`s it to the sim channel; the daemon
  side is `RemoteStub`). So a host-side Python stub over the C engine with the same interface plugs into that
  transport and the daemon's `DebugSession` unchanged. Deviation from "the stub protocol in C": c1 =
  `NativeStub` in rt-eng Python over `pssvm_dbg_*`, c2 = a line-JSON socket transport for any stub, c3 = the
  stub in C itself, moved to P4.9 (the simulator without Python). C differences to list in the replay:
  `engine`, `exec_snippet` `unsupported` (capability absent), `this` null (no per-frame component in C),
  `set_stop_on` keeps only `error`; comp/memory setup through the host (comp writes, host memory dict).
- 2026-11-19 P4.5c1 done: rt-eng `zuspec.rt.eng.debug.NativeStub(engine, model, run=None, memory=None,
  import_provider=None, on_event=None)` serves pyvm `Stub`'s requests/events over `pssvm_dbg_*`; host.py
  `run_sync(..., on_stop=)` hands each `PSSVM_STOP` to it (it blocks while stopped, then resumes). Error
  stops on by default (as pyvm). Construction (`$comp_init`, pssvm_host.c `pssvm_construct`) now runs with
  no debug hook, so an entry pause waits for the run instead of failing construction ("construct-yield").
  rt-eng tests/test_native_stub.py replays be-bc test_debug_stub.py `CASES` (all but exec_snippet) with the
  C differences as overrides; `par` uses rt-eng test_debug_stop.py `par_model` (writes a local; be-bc's writes
  action fields, which C runs have none of). rt-eng 247, be-bc 373, pssc test_native_session 8.
- 2026-11-19 P4.5c2 done: be-bc `zuspec.be.bc.interp.stub_socket`: `serve_stub(stub, sock) -> StubServer`
  (two daemon threads; requests served in order, events only between replies; each line adds `stub_state`;
  client EOF detaches a still-attached stub) and `SocketStub(sock, timeout=30, on_event=None)` (stub
  interface; errors `closed`/`timeout`; EOF -> one `exited` {detached, closed}). Spec: be-bc
  docs/spec/debug-stub.md "Socket transport". Tests: be-bc test_debug_stub.py `test_scripted` parametrized
  direct|socket (`over_socket`, `_play`), socket transcript == direct, close detaches; rt-eng
  test_native_stub.py `run_case(..., transport)` same. be-bc 393, rt-eng 265, pssc test_native_session 8.
  pssc's sim channel (serve.py `attach_stub` / remote.py `RemoteStub`) left as is (its framing carries the
  testbench's commands too).
- 2026-11-19 P4.5d done (P4.5 done): pssc's debugger debugs a test function on the C engine. `Registry.start`
  (src/pssc/devenv/debug/sessions.py) picks by the session's engine (`PSSC_ENGINE`): `_py_debug` (be-bc `Stub`,
  as before) or `_c_debug`: testing/native.py `debug_stub(model, symbols, seed, memory, imports, on_end)` -> a
  rt-eng `NativeStub` on a fresh engine whose run calls set-up, the test and tear-down on one tree with the
  vm.Session's platform memory and imports; the DebugSession/DAP above it are unchanged. Refused on C: action
  tests, ops C lacks (DebugError), reload (P4.6). Messages are not captured on C. Known gap: pssunit's rewritten
  checks (`check_capture`) count from the VM's live events, which C does not feed, so a C run's ledger shows
  them failed (WB DMA t_routing_mask: py (2,0,0), C (0,2,0); same bus and import calls) -> P4.8. Also
  `vm.Session(engine="c").run_test` fails before running (`_Tee` has no `next_seq`, native.py `_emit`).
  Test: tests/unit/test_native_debug.py (3: entry stop, breakpoint, frames, locals, 3 steps same lines as py,
  run to exit; end; reload refused). Unit 3794 + 1 baseline; daemon file 4.
- 2026-11-19 P4.6a done: P4.6 split a..c (*Split*). rt-eng: `pssvm_comp_write` (pssvm.h/pssvm_host.c),
  host.py `Engine.construct` (explicit `pssvm_construct`; construct-yield = NativeError) and `Engine.comp_write`;
  new `zuspec/rt/eng/generations.py` `reload_engine(engine, old, new, strict=, number=)` -> (engine N+1, result):
  be-bc `check_reload`, new engine, construct when N has a tree (construct-yield -> `ReloadRefused("bus")`),
  be-bc `migrate` on slot lists, `comp_write`; caller frees N and quiesces. be-bc host-abi.md §Reload +
  generations.md Status. T4.6 (a): rt-eng tests/test_generations.py 8 (be-bc `_comp_model` cases: migrate by
  path, structural change, strict, bus read/import, api, no tree). rt-eng 273.
- 2026-11-19 P4.6b done, P4.6c dropped: P4.6 done. sessions.py `Registry.reload` no longer refuses C sessions;
  `_c_debug` counts the C run as a call (`session._depth`) and applies a staged reload in its `on_end`, so a
  reload during a C debug run is staged (run ends on N, next on N+1), as on py. `bc_session.Session.reload` on C
  (no VM) swaps the model at once and raises the `reload` event. c dropped: py also stages for the whole run.
  T4.6 (b): test_devenv_reload.py `test_c_session_reload_swaps_the_image_for_the_next_test`,
  `test_c_dbg_reload_is_staged_until_the_run_ends`; test_native_debug.py `test_reload_during_a_c_run_is_staged`
  (was "reload refused"). Unit 3796 + 1 baseline; daemon file 4.
- 2026-11-19 P4.7 done (one step). be-bc: spec.py `zbc_covent` (pc, src_ref, flags, scope, path; 20 bytes),
  `ZBC_SEC_COV` = 25, `zbc_cov_flags` (ARM, IMPLICIT); model.py writes/reads `CoroDescriptor.cov` (was memory
  only); rt-core zbc_format.h regenerated; docs/spec/zbc-format.md. rt-eng: zbc_reader.c reads COV
  (`zbc_image_cov_at`); zbc_interp.c adds `path`/`scope` to `stmt` and raises `branch` (pc, branch, arm, scope,
  implicit) like the py VM; neither is raised again for an IMPORT run again (`ev_again`). pssc: native.py
  `ENGINE_C_EVENTS` + EV_BRANCH; bc_session `_run_test_c` no longer refuses a `Coverage` sink.
  T4.7: rt-eng test_events.py `test_coverage_events_match_the_python_vm` (3), test_side_tables.py (COV round trip);
  test_native_session.py `test_coverage_is_the_same_on_both_engines` (was "refused"), and the traced test now
  compares stmt path/scope too. rt-eng 276, be-bc 395, unit 3796 + 1 baseline, daemon file 4.
- 2026-11-19 P4.8a done: P4.8 split a..e (*Split*). pssc: native.py `run_test(..., keep=, setup=, teardown=)`
  (tear-down also after a failed test; `keep` gets the component object, also on failure) and `_emit` goes
  through the sink's `event` (so a `vm.Session`'s `_Tee` works: the `next_seq` failure is gone);
  bc_session `comp_obj` is the last C run's (`_c_cobj`), `_run_entry_c` maps a function entry's coroutine
  to its symbol (actions refused); vm.Session `engine=`, `_run_test_c` (new `run_state` per run; refuses
  register fake, sanitizers, mocks, spies), `Ctx.raise_irq` refuses delivery on C. T4.8 (a):
  test_native_session.py `test_a_python_session_counts_the_same_on_both_engines` (2: rewritten checks,
  require stop, set-up, bus log, access events) and `..._refuses_on_c_what_needs_the_vm`. Unit 3799 + 1
  baseline, daemon file 4; rt-eng/be-bc unchanged.
- 2026-11-19 P4.8b done: `pssc test --engine py|c` (cli.py; default `$PSSC_ENGINE`, else py; cmd.py resolves it
  with native `engine_name`, puts `--engine` in replay lines and `"engine": "native"|"pyvm"` in a .cdb) threads
  `engine` through runner `run`/`_run_item`/`_run_image`/`run_property`/`run_one` to `_session_for`
  (`vm.Session(..., engine=, sanitize=engine != "c")`); runner `_classify` makes `NativeUnsupported` ERROR
  UNSUPPORTED with its reason. Register fake on C: rt-eng host.py `run_sync` calls `memory.note_access(reg_id,
  src_ref)` before each access (the C request carries both), so vm.Session `_run_test_c` no longer refuses
  it; sessions.py `_c_debug` gives each run a new `run_state`. WB DMA t_routing_mask C debug now (2,0,0) as py.
  rt-eng pssvm_host.c names ZBC_ERR_OOB_REG/-LOCAL. op_model suite on C: 8 pass, 13 ERROR (actions refused;
  t_copy_error, t_poll_pending: ZBC_MAX_LOCALS (32) exceeded; t_device_offsets: ZBC_MAX_REGS (64)) -> P4.8d.
  Docs: cli.md, test-command.rst, cli-reference.rst (regenerated). Tests: test_native_session.py +3 (CLI py/c,
  bad engine), test_native_debug.py end asserts (2,0,0). Unit 3802 + 1 baseline, daemon file 4; rt-eng 276.
- 2026-11-19 P4.8c1 done (sanitizers + spies on C): P4.8c split c1..c3 (*Split*). Decision: on C the sanitizers
  and spies run after the run, from its trace (native.py `_emit` -> vm.py `_Tee.event`): the same events in
  the same order as on py, so the same findings, locations and calls; a spy's callback runs after the run.
  A poll the C run cannot leave is stopped in the host by vm.py `_PollGuard` (memory wrapper, every read,
  2*budget+16), and the trace's own `poll-budget` finding then ends the test as on py. `_Tee.host` sends
  host events to the user's sink when there is no VM. `_run_test_c` refuses mocks only; runner
  `_session_for` sanitizes on C too. Not on C yet: unconsumed-irq (with interrupts, c3). op_model on C
  unchanged (8 pass, 13 ERROR). Tests: test_pssunit_sanitizers.py +11 (10 cases py == c, sink events on C);
  test_pssunit_vm.py +2 (spy py/c). Docs: cli.md, test-command.rst, cli-reference.rst, cli.py help.
- 2026-11-19 P4.8c2 done (mocks on C): the C engine calls a bound host function in place of a PSS
  function: rt-eng C `pssvm_bind(e, coro, on)`; a CALL of a bound coro yields ZBC_YIELD_BOUND (an IMPORT
  request with `bound`=1, fn_id = the coro, up to 16 args) and emits call/return with `mocked: true`.
  Python: rt-eng host.py `Engine.bind`, `run_sync(bound=)`; pssc native.py `run_test(bindings=)`/`_bind`
  (qname -> the coros CALLed; a function never called through a CALL is refused, as `VM.bind`);
  bc_session/vm.py `_run_test_c` pass the session's mocks. A mock needs a debug lowering (codegen inlines
  small functions; `vm.load` lowers with debug=True). Tests: rt-eng tests/test_bind.py (4, py == C);
  test_native_session.py +1 (mock py == C, trace `mocked`), refusal test renamed. Docs: cli.md `--engine`.
  Left on C: interrupts (c3), unconsumed-irq.
- 2026-11-19 P4.8c3 done (interrupts on C; P4.8c done): `ctx.raise_irq` on C (vm.py `Session._c_irq`) notifies
  the running engine (`Engine.notify(N_IRQ, irq)`; native.py `run_test(irq=<handler entry>, live=)` sets
  `Engine.set_irq` and hands the live engine to the session); the C engine's trace has the `notify` event
  (native.py `ENGINE_C_EVENTS` + EV_NOTIFY). With no handler the engine is not told (a notify with no handler
  is an engine error, host-abi.md); vm.py `_Served` counts the requests served, and `_Tee.event` makes the
  `notify {delivered: false}` host event and the unconsumed-irq finding at that access/import of the replayed
  trace (raised after the last one: at the run's end). Tests: test_pssunit_sanitizers.py
  `test_irqs_are_the_same_on_the_c_engine` (4, py == C: counts, findings, irqs, notify/chan/sanitizer trace);
  test_pssunit_vm.py `test_a_mock_replaces_a_function_every_test` on both engines. Docs: cli.md `--engine`.
  Unit 3820 + 1 baseline. Not done: an irq raised from a mock of a PSS function on C with no handler is
  located at the next access/import (as py's `site`); unchecked whether py and C agree there.
- 2026-11-19 P4.8d1 done (op_model function tests on C): rt-eng C frames are sized for their coroutine
  (zbc_interp.c `verify_coro` counts registers/locals, at least ZBC_MIN_REGS 64 / ZBC_MIN_LOCALS 32; the frame
  must fit ZBC_MAX_FRAME_BYTES 8000 of the 8 KiB zsp stack block, else ZBC_ERR_OOB_REG); the compiler uses about
  one register per instruction (WB DMA up to ~330). `pssvm_frame_t` gains `nregs`/`nlocals` (host.py `_Frame`).
  op_model suite on C: 14 pass, 7 ERROR (action tests refused), was 8/13. Tests: rt-eng test_engine_safety.py +2
  (rt-eng 282); test_native_session.py `test_the_example_suite_gives_the_same_verdicts_and_bus_logs_on_both_engines`
  (verdicts + register-fake bus logs, py == C). Unit 3821 + 1 baseline.
- 2026-11-19 P4.8d2 done (op_model action tests on C): all 21 tests pass on both engines with the same checks
  and the same register-fake bus logs. rt-core zsp_timebase.c `zsp_timebase_alloc_frame`: a frame over the 8 KiB
  stack block gets a block of its own (freed, not pooled); rt-eng ZBC_MAX_FRAME_BYTES 65536, ZBC_MAX_REGS 1024 ->
  4096 (routed `arb` body uses ~1500 registers, one per instruction). rt-eng C `pssvm_call_start_obj` (host-owned
  action object; host.py `Engine.call(obj_slots=)`, `NativeCall.obj`; be-bc host-abi.md). pssc native.py
  `_action_obj` sizes the object and `_root_only` lets an action run whose activation table is its root node in
  the root component with no sites, scopes, cones or pool writes (pyvm's `Activation` then does nothing a plain
  call does not); an activity is refused ("the C engine does not run activities yet", P4.10). Every op_model
  action test has such a one-node table. Tests: rt-eng test_engine_safety +2, test_native_host +1 (rt-eng 284);
  test_native_session.py example suite now compares all 21 (no ERRORs) + `test_an_action_with_an_activity_is_
  refused_on_the_c_engine`. Unit 3822 + 1 baseline.
- 2026-11-19 P4.8d3 done (P4.8d done): the example suites pssc runs besides op_model. examples/fw_wb_dma_uvm
  has no pssunit suite (a dfm/UVM bench; it reaches C with P4.9). pssunit's examples/wb_dma (its repo, via
  PSSUNIT_EXAMPLES): smoke_a, smoke_1_a pass on both engines with the same report lines. Test:
  test_native_session.py `test_the_pssunit_example_gives_the_same_verdicts_on_both_engines` (skipped without
  PSSUNIT_EXAMPLES; `_suite_on(where=, suite=)`).
- 2026-11-19 P4.8e done (P4.8 done): a `native` construct-matrix target. trace_harness.py `_run_native` lowers
  like pyvm and runs `pss_top::initialize` then `run` on the rt-eng `Engine` (load option `verbosity=255`; new in
  pssvm_host.c, documented in be-bc host-abi.md, because a host-less run dropped every message above NONE);
  the trace is its access and message events. _matrix.py: ALL has "native"; native inherits pyvm's strict
  xfails (`xfail_of`) unless `native_xfail` gives its own (3 match cases: C_MATCH_ERR; 3 wb_dma: C_SOLVE, to
  P4.10). coverage.yaml gives native the 7 dispositions pyvm has as open/n-a. Matrix 2548 pass + 369 xfail
  (+ the pre-existing sv W64 XPASS); native alone 352 + 131. Unit 3822 + the test_ir_walk baseline.
- 2026-11-19 P4.9a done: the C engine under Verilator through DPI-C, no Python in the simulator. rt-eng
  share/rt/pssvm_dpi.c (built into libpssvm.so with the rest of rt/*.c): `pssvm_dpi_load(path, opts)` (reads the
  `pssc build --image` file; opts as pssvm_load plus `seed=N`), `_construct` (= new `pssvm_fresh`: drop and
  rebuild the tree, so `fresh()` works), `_arg_push` + `_call_start(e, entry, comp_path)` (staged arguments in
  place of the open array), `_req_int/u64/str/nargs/arg`, `_complete(h, u64)` (sizes the answer from the
  request), `_result`, `_error`. pssvm_pkg.sv: the two imports and `pssvm_engine_dpi.call_start` changed.
  `pssc sv-pssvm-path --lib` prints the engine library (testing/native.py `engine_lib`; no solver). Compiles and
  runs on Verilator 5.055 and the bench's 5.052. Test: test_pssvm_shim.py `test_verilator_dpi_native` (the
  `test_verilator` TB on `pssvm_engine_dpi`, simv without PYTHONPATH: same BIND/TEST/BAD lines as pyvm, imports
  fn 16 and 13 seen, `fresh()` 0). Gap -> new P4.9a2: no import names from the image, no checks_failed.
  rt-eng 284, shim + doc refs 713, native session + cli 43.
- 2026-11-19 P4.9a2 done: import names and test results on the C engine over DPI. be-bc: IMPORT section (kind
  26, record `zbc_impent {fn_id, name}`; format/spec.py, model.py `import_names`, docs/spec/zbc-format.md); rt-eng
  zbc_reader.c `zbc_image_import_name`/`zbc_image_meta`, pssvm_host.c sets `pssvm_req_t.name` and `pssvm_meta`,
  `ev_import` carries the name. pssc build writes META `pssc.native` (build_cmd.py `_native_doc`: tab-separated
  `test <qname> <expect>`, `session|setup|teardown <fn>`, `fail <comp slot of a ledger's n_fail>`), read by
  pssvm_dpi.c `pssvm_dpi_test_name/_test_expect/_hook/_checks_failed`; pssvm_engine_dpi overrides them. Tests:
  test_pssvm_shim.py `test_verilator_dpi_native` (imports logged as `double_handle`, `check_operand`) and new
  `test_verilator_runner_dpi_native` (pssvm_runner on the C engine: filter, every test of the image, a failing
  check counted: `1 passed, 1 failed`). Release snapshot tests/unit/data/release_zbc_sha256.json regenerated (3
  models with imports gained the IMPORT section). Unit 3824 + 1 baseline (test_ir_walk) + dbg daemon 4; rt-eng
  284; be-bc 398.
- 2026-11-19 P4.9b2 done: the C engine runs calls on a sub-component instance (`ch[i]`), not only the root.
  rt-eng zbc_interp.h `zbc_host_t.cbase` (base slot of the instance; tests/conftest.py ctypes mirror),
  zbc_interp.c frames take `cbase` at init and LD_COMP/ST_COMP use `cobj[cbase+slot]`; pssvm_host.c `inst_index`
  maps a comp_path to an instance from META `pssc.native` `inst\t<path>` lines (pre-order = image comp_bases
  order), written by pssc build_cmd.py `_native_doc`. Test: rt-eng tests/test_pssvm_host.py
  `test_a_call_by_instance_path_reads_that_instances_fields` (285 pass). Bench (P4.9b1 added the native
  build, `+pss_impl=native`, simv without Python): `dfm run uvm.tests-native` tlm+wb 10/10; the rtl native image
  failed to link (dv-flow-libhdlsim put V<top>__Dpi.o after V<top>__ALL.a). Unchecked: IRQ handler threads
  run in the call's instance. docs/cli-reference.rst regenerated (b1 help text).
- 2026-11-19 P4.9b3 done: all 14 bench cells pass on the C engine with the same bus accesses as the generated
  model. Link fix (owner: edit and push upstream) in fw-wb-dma packages/dv-flow-libhdlsim vlt_sim_image.py:
  V<top>__Dpi.o goes in VM_USER_LDFLAGS (before the objects and archive), as the newer upstream copy in
  pssc-debug packages/ already does. examples/fw_wb_dma_uvm/vm_cmp.sh adds a `native` run (the native flow's
  simv and wb_dma_c.zbc) when its image exists. `dfm run uvm.tests-native` 14/14, `vm_cmp.sh` 14/14 "native
  same", `dfm run tests` 14/14 and `uvm.tests-vm` 14/14 (opt images relinked with the fix).
- 2026-11-19 P4.9c done: `pssc build --native [--platform SPEC]` refuses (exit 2, no image written) when the
  Python platform's doubles (`--platform`, else `[tool.pssunit] platform`) name a function or import in the
  image: one line each, `<py file>:<line>: @pv.mock('c::f') is Python; c::f (<pss file>:<line>) cannot run
  without it` (implements: the import's name). Doubles of nothing in the image pass. pssc build_cmd.py
  `native_doubles`, `cmd_build`; test tests/unit/test_build_native.py (4). The bench's PssvmImage passes
  `--native` for impl native (examples/fw_wb_dma_uvm src/pss/pssvm_gen.py); `dfm run uvm.tests-native` 14/14
  with the image rebuilt. docs/cli-reference.rst regenerated. Unit 3829 + baseline test_ir_walk; daemon file 4.
  Not checked: a sim:<session> run (`pssc test --platform sim:`) does not know the session's engine, so it
  does not refuse; the refusal is at image build.
- 2026-11-19 P4.9d split d1..d3 (*Split*). P4.9d1 done: the debug stub in C, rt-eng share/rt/pssvm_stub.c
  (pthreads; small JSON reader/writer `jparse`/`jget`; `cmd_*` table, `dispatch`). API in pssvm.h:
  `pssvm_stub_new/free`, `pssvm_stub_request(s, line)` -> reply (stub-owned until next request),
  `pssvm_stub_wait_attached(s, ms)` (the run thread waits for attach before its first pssvm_run),
  `pssvm_stub_on_stop(s, h)` (blocks until resumed; 1 = terminate), `pssvm_stub_exited`, `pssvm_stub_next_event`,
  `pssvm_stub_state`, `pssvm_stub_set_backdoor(s, peek, ctx)`; `pssvm_image(e)`. The C reader indexes LOCAL
  (`img->locals/local_count`; pc_start/pc_end global). build.py links -pthread. host.py `Engine.stub()` ->
  `CStub`. tests/test_c_stub.py (41): be-bc stub cases direct + over the socket transport. Differs from
  NativeStub: no image sha256; read_comp always `no_comp` (no paths/field names in the image). rt-eng 326.
- 2026-11-19 P4.9d2 done: the sim channel in C, rt-eng share/rt/pssvm_dpi.c server section
  (`pssvm_dpi_serve/poll/pending_sel/serve_test/serve_done/set_time/dbg_wait/run`): a reader pthread + queue;
  register, ping/info/end; `debug` on/off creates/frees the d1 C stub, the event pump adds `t`; `pssvm_dpi_run`
  hands STOP to `pssvm_stub_on_stop` (terminate -> ERROR). JSON helpers moved to share/rt/pssvm_json.h. The
  default sim socket path is computed in C (small SHA-1) and matches Python. SV pssvm_pkg.sv `pssvm_engine_dpi`
  imports these and overrides serve/run. Tests (tests/unit/test_pssvm_shim.py): `test_verilator_serve_dpi_native`,
  `test_verilator_debug_dpi_native`; `-k dpi` 4. rt-eng 326; unit 3829 + baseline; daemon file 4.
  Gaps: `reload` answers unsupported (P4.9e); `+pssvm_dbg_on_fail` hold untested natively; the debug test talks
  to the daemon registry directly, not through the `pssc dbg` CLI (d3).
- 2026-11-19 P4.9d3 done: new `+pssvm_dbg_start` (pssvm_pkg.sv `pssvm_dbg_start_hold`, from
  `pssvm_engine_dpi::new` and `pssvm_engine_hif::create`) holds a TB with no runner before its first call; new
  `pssc daemon start` (devenv/client.py `cmd_daemon`; replaces a stale daemon) because a native simv cannot start
  one. Tests: test_pssvm_shim.py `test_verilator_dbg_on_fail_dpi_native`, `test_verilator_attach_dpi_native` (CLI),
  `test_verilator_dbg_start_dpi_native` (`-k dpi` 7); test_devenv_daemon.py `test_daemon_start_command`.
  Bench by hand (wb_dma_sw_copy_test, tlm native): attach, break configure_channel.pss:28, resume -> stopped
  (breakpoint) there, `dbg end`, TEST PASSED. Docs: simulation.rst "Holding at start"; cli-reference regenerated.
  rt-eng 326; unit 3835 + baseline; daemon file 4. Gap: `pssc sim reload` on C (P4.9e).
- 2026-11-19 P4.9e split e1..e4 (*Split*). P4.9e1 done: rt-eng `pssvm_reload`/`pssvm_generation`
  (share/rt/pssvm_host.c, after `pssvm_comp_write`; include/pssvm.h `PSSVM_R_STRICT`), in place: refusal
  message starts with the reason (api/structure/bus/construct); same-layout state carries; bindings, irq and
  breakpoints cleared. host.py `Engine.reload`, `Engine.generation`. Tests rt-eng test_generations.py
  `test_c_reload_*` (8). be-bc host-abi.md §Reload notes it. rt-eng 334; pssc `-k dpi` 7.
- 2026-11-19 P4.9e3 done (before e2: a logic-only fix keeps the layout): rt-eng share/rt/pssvm_dpi.c
  `on_reload` answers `reload` {image?, strict?} on the SV thread: reads the file (`read_image`, default the
  remembered path), `pssvm_reload`; refusal = error `refused` with `detail` {code, reason, message, changes:[]}
  (reason = message prefix) and "reload refused (<reason>): ..."; success = the shim's body (+`breaks_cleared`
  when the debug stub is on: pssvm_reload clears breakpoints, the client re-sends) and updates the remembered
  image and `info.image` (`make_info`). Test tests/unit/test_pssvm_shim.py `test_verilator_reload_dpi_native`.
  pssc `-k dpi` 8; rt-eng 334.
- 2026-11-19 P4.9e2 done: be-bc interp/generations.py `native_inst_lines` (from `_instances`) writes
  `inst <path> <type> <base> <end> <crc32 of field names>` lines; pssc devenv/build_cmd.py `_native_doc` uses it.
  rt-eng pssvm_host.c: `next_inst` parses them (`inst_index` too; path-only lines still work for call-by-path);
  `pssvm_reload` non-strict with a changed layout migrates by path (`migrate_by_path`: same type, sig and size
  -> N's slots, else fresh), and still refuses `structure` under strict or when either image lacks the layout
  columns. The reply's `migrated` list is not filled by C yet. Tests: rt-eng test_generations.py
  `test_c_reload_migrates_by_path` (2: changed+added, and an instance moved by an insert);
  test_pssvm_shim.py `test_verilator_reload_dpi_native` now also takes the tree change without --strict.
  rt-eng 336, be-bc 398, pssc shim+sim-channel+build+doc refs 747.
- 2026-11-19 P4.9e4 done: examples/fw_wb_dma_uvm/reload_native.sh (after `dfm run uvm.tests-native -D
  images=tlm`): holds the tlm sw_copy native cell at start (`+pssvm_dbg_start`, own daemon dir), builds a
  changed copy of the model with `pssc build --native`, `pssc sim reload <img>` -> `generation 1 -> 2`, then
  attach, break configure_channel.pss:28, resume, continue -> stopped (breakpoint), end; TEST PASSED, same
  simulator pid. Prints PASS. Found: a native simv built before e3 answers "reload: not supported" — the
  native cells must be rebuilt after a C engine change (rm the *native rundirs).
- 2026-11-19 P4.9f done: docs/simulation.rst new section "Running on the C engine" (build --native,
  `pssc sv-pssvm-path --lib`, +define+PSSVM_DPI, `pssvm_engine_dpi`, daemon start); Hot reload gains a C-engine
  paragraph (migration by path, breakpoints cleared, reload_native.sh); "Holding at start" no longer says
  reload is missing. Sphinx clean, doc refs 704, shim `-k dpi` 8. P4.9 done.
- 2026-11-19 P4.10 opened and split a..f (§7.2 P4.10 *Split*): ACT section first (the activation table is in
  memory only today), then the guarded per-cone blob (one blob, enables pinned) so C needs no IR constraints,
  then C runtime in three parts (nodes/scopes, cone solve, lookahead + pools), then the suites. Baseline: the
  seven activation suites in tests/unit/integration pass 111 on pyvm.
- 2026-11-19 P4.10a done: be-bc ACT section (kind 27; spec.py `zbc_acttab`..`zbc_actpick`, 13 records;
  rt-core zbc_format.h regenerated). model.py `_serialize_activations`/`_parse_activations`: every
  activation's tree, slot names and initial values; constraint expressions are left out (cone blobs, P4.10b).
  Parsed tables go to `ZbcModel.image_activations` (pyvm keeps `activations` until b); a model read from an
  image writes them back (oracle byte-stability). Tests: tests/unit/integration/conftest.py round-trips every
  table the integration suites build (act_roundtrip.py), test_act_section.py (image has ACT; none without
  activations). rt-eng test_engine_components builds real tables. Release snapshot regenerated: only ACT
  differs (checked by hashing with ACT suppressed: 0 of 596 moved). Guard 111, integration 676, be-bc 424,
  rt-eng 336, doc refs 707, unit 3840 + 1 baseline.
- 2026-11-19 P4.10b split b1/b2; b1 done: be-bc `ActivationTable.blob(cone, enabled)` returns (base blob of the
  cone's vars, one blob per enabled constraint), cached per cone; `SolveCache._compile` compiles the base and
  adds each with dv-solve `add_constraint` (rc -2 -> CompileUnsatError). The guarded single blob (`en_i -> c_i`)
  was tried and reverted (zuspec-solver unchanged): ex134's unsat resource lock hit SolveBudgetError.
  Integration 676, guard 111, be-bc 424, rt-eng 336, unit 3841 + 1 baseline (doc refs included). Values not
  compared bit-for-bit with the old per-subset blobs (suites pass).
- 2026-11-19 P4.10b2 done (P4.10b done): ACT carries each cone's blobs. spec.py: `zbc_acttab.blob_size` (was
  _pad; blob area after the string text, 8-aligned, text padded to reach it), `zbc_actcone` +base_off/base_size
  (40 bytes), `zbc_actcon` +blob_off/blob_size/text (40 bytes; text = the constraint as written, for the unsat
  message). be-bc `ActivationTable.cone_image`/`load_cone`/`con_text`; model.py writer fills the blob area,
  `_parse_activations` preloads each table. zbc_format.h regenerated. Tests: act_roundtrip.check compares
  cone blobs and text for every integration table; test_act_section.py runs a model read from its image
  (same lines over 5 seeds) and checks its unsat message. Release snapshot: 108 of the 111 models with ACT
  moved, none without. Integration 679, be-bc 424, rt-eng 336.
- 2026-11-19 P4.10c split c1..c4 after a survey: be-bc emits SCOPE_ENTER/SOLVE_NODE only when an activation
  has a cone (lower/context.py `scoped`), so a cone-free activity lowers to the plain bytecode C already runs
  (INVOKE with the node flag; INITED honoured). What C lacks for it: a node's state/buffer writes at its end,
  and non-root instances (zbc_engine.c refuses the COMP flag NODE_INST). C does not read ACT yet. Decided:
  `ZbcModel.from_container` sets `activations` from the image's tables when it has none (c1). Unit 3843 + 1
  baseline, rt-eng 336.
- 2026-11-19 P4.10c1 done: be-bc `ZbcModel.from_container` sets `activations` (and `image_activations`) from
  ACT, so a model read from its image runs its activities without a manual assignment (test_act_section.py
  simplified). pssc testing/native.py `_root_only` -> `_plain_activity`: no cones, pools, claims, picks or
  flow-object writes, every node `comp_rel` 0 and no `comp_slot`; the action object is `len(table.names)`
  (pyvm `root_frame`'s one object per activation). No C change: test_native_session.py runs `t_seq` (repeat
  of a non-rand count, if on a component field, a nested activity, select, an unconstrained rand) on both
  engines over 6 seeds, same order and values. A cone anywhere in a model puts SOLVE_NODE in the image, so
  `check_model` refuses the whole model on C (until d), not just that action. Unit 3851 + 1 baseline,
  rt-eng 336, be-bc 424, pyvm guard 111.
- 2026-11-19 P4.10c2 done: rt-eng zbc_reader.c `open_act` indexes ACT into `zbc_image_t` (acttab, acts, actnodes
  ... actpicks, actlist, actstr_off/actstrb, actblob) and checks every range and id (local ids against the
  activation's counts, node base+size within the object, init slots, blob ranges), else ZBC_ERR_BAD_SECTIONS.
  Accessors `zbc_image_act(img, coro)`, `zbc_act_node/scope/site(img, act, id)`, `zbc_act_write(img, i)`,
  `zbc_act_list(img, i)`, `zbc_image_act_str`. rt-eng tests/test_act_reader.py: the C view (nodes, children,
  state/buffer writes, sites, scopes, names) == `ZbcModel.from_bytes`'s table; 5 corrupted tables refused.
  pssc's ACTIVITIES image opens in C with the same node/site counts. Unit 3851 + 1 baseline, rt-eng 343,
  native session + pssvm shim 41, pyvm guard + ACT 115.
- 2026-11-19 P4.10c3 done, re-scoped (§7.2 P4.10 *Done*). Probe: a state pool always brings cones (the root's
  solve picks the initial object, `cp@<root>.$init.*` slots) even with no reader and no constraints; a buffer
  output brings a pool, a pick and a cone only once some action consumes it; with no consumer the table has
  no pool and no buffer write and the activity is plain bytecode. So C state/buffer writes at a node's end
  cannot be reached without SOLVE_NODE: they move into P4.10d. pssc tests/unit/test_native_session.py
  `FLOW`: a consumer-less buffer output runs alike on C; a state pool and a buffer consumer are refused with
  SOLVE_NODE. Native session 27 + 1 skip. No C change.
- 2026-11-19 P4.10c4 done (§7.2 P4.10 *Done*). rt-eng zbc_interp.c: `interp_locals_t` gains act/node/comp,
  passed as three more task args (roots: `ZBC_FRAME_ROOT`, zbc_interp.h), `frame_comp` sets cbase from the
  instance, `child_node` maps INVOKE's site (arg2) as pyvm `ActivationTable.site`; zbc_engine.c refuses
  ZBC_COMP_F_NODE_INST only without ACT. pssc native.py `_plain_activity` accepts any single `comp_rel`.
  Tests: rt-eng test_engine_components.py `test_a_node_in_another_instance_runs_there` (C == pyvm) and
  `..._needs_the_activity_table`; pssc test_native_session.py `SUB` (`do sub_c::A` twice: s1.cnt 2 on both).
  rt-eng 343, native session 28 + 1 skip, unit 3855 + 1 baseline, pyvm guard 111, bench native 14/14 + vm_cmp same.
- 2026-11-19 P4.10d split (survey only, no code): C has no activation run state and solves only single blobs;
  d1 constrained rand fields (state, SCOPE_ENTER, SOLVE_NODE with enables, pins, composed blobs via
  `dvs_solver_add_constraint`/`dvs_solver_pin_var`, witness reuse, unsat text), d2 choice of instance
  (`comp_slot`), d3 state pools, d4 buffer picks (C must reproduce Python `random.Random` randrange).
- 2026-11-19 P4.10d1 done (§7.2 P4.10 *Done*). rt-eng: zbc_solver.c `zbc_solver_cone` (base + constraint blobs,
  pins, LCG then full search; restarts 5/10000 as be-bc), zbc_interp.c activation run state and
  `case ZBC_OP_SOLVE_NODE` (`act_solve`: constraints in force, pins incl. comp_read, witness reuse, commit),
  zbc_engine.c `act_solve_supported` refuses SOLVE_NODE only for claims, picks, pools with cones, comp_slot nodes
  and busy/pick/live/loop-index vars. test_engine_activation.py: SCOPE_ENTER/SOLVE_NODE without ACT now run;
  test_ops_match_model.py `_refusal_block` anchored on the image-check loop. pssc native.py: `ENGINE_C_GAPS` empty,
  `needs_solver` counts SOLVE_NODE, `check_model` refuses an image whose cones C cannot take (`_cone_solvable`,
  mirrors act_solve_supported), `_plain_activity` admits cones. test_native_session.py `CONES` (with, comp pin,
  activity constraint over r1/r2; 4 seeds each, C == py) and an unsat test (py ScopeUnsatError, C NativeError).
  Cone tests live in pssc, not rt-eng: rt-eng tests have no PSS front end. Gaps kept: loop-index pins refused, no
  SOLVE event, unsat has no constraint text. rt-eng 344, native session 40 + 1 skip, pyvm guard 111, matrix native
  352 + 131 xfail (wb_dma's 3 C_SOLVE xfails still refused: flow objects, d3/d4), build/debug/shim/act 28.
  Bench after the C change: `dfm run uvm.tests-native` 14/14 (0 tb-img errors), `./vm_cmp.sh` all native same.
- 2026-11-19 P4.10d2 done (§7.2 P4.10 *Done*). rt-eng zbc_interp.c: `case ZBC_OP_SOLVE_NODE` sets the frame's
  comp from the solved `comp_slot` (new `frame_cbase`, shared with `frame_comp`); `child_node` already adds
  `comp_rel` to it. zbc_engine.c `act_solve_supported` no longer refuses comp_slot nodes. pssc native.py
  `_cone_solvable`/`_plain_activity` admit them (refusal texts drop "a choice of component instance").
  test_native_session.py `SUB2` (two sub_c, `do sub_c::A` x3 and compound `AA` x2; 6 seeds; C == py, choices
  vary). Matrix: wb_dma's 3 native xfails (`C_SOLVE`, removed) were instance choices and now pass. rt-eng 344,
  native session 52 + 1 skip, pyvm guard 111, matrix native 355 + 128 xfail, unit 3879 + 1 baseline.
- 2026-11-19 P4.10d3 done (§7.2 P4.10 *Done*). rt-eng zbc_interp.c: node frames marked (`ACT_NODE_FRAME` ->
  `is_node`), `act_exit_node` (state writes) at the top of `halt`, `act_pin` pins live_from, the root solve copies
  each pool's initial object; zbc_engine.c `act_solve_supported` allows state pools / live_from; zbc_reader.c no
  longer range-checks live_from as a node id. pssc native.py `_cone_solvable`/`_plain_activity` admit state pools.
  test_native_session.py `test_a_state_pool_flows_alike_on_both_engines` (6 seeds); buffers still refused (own
  test). rt-eng 344, native session 57 + 1 skip, pyvm guard 111, matrix native 355 + 128 xfail (no XPASS), unit
  3884 + 1 baseline. Bench native rebuilt: 14/14 (0 tb-img errors), `./vm_cmp.sh` all native same.
- 2026-11-19 P4.10d4 done (§7.2 P4.10 *Done*; P4.10d done). rt-eng share/rt/zbc_rng.c: Python's MT19937 seeding
  (`init_by_array`) and `randrange` (`zbc_mt_seed`, `zbc_mt_randrange`), checked against Python for 9 seeds
  (tests/test_rng.py). zbc_interp.c: per-activation outputs store (`bobj`/`bval`), `act_exit_node` appends buffer
  writes, `act_attempt` split from `act_solve`, `act_pick` (pyvm `_candidates`' lazy Fisher-Yates over sorted
  pools per consumer level, `_picked` swap-remove); picks skip witness reuse as in pyvm. zbc_engine.c
  `act_solve_supported` and pssc native.py `_cone_solvable`/`_plain_activity` admit buffer pools and picks.
  test_native_session.py `BUF` (permutation + constraint rejection, 6 seeds each, C == py). Gap: a pick unsat
  gives ZBC_ERR_SOLVE_UNSAT without pyvm's text. rt-eng 353, matrix native 355 + 128 xfail, unit 3895 + 1
  baseline; bench native 14/14 (0 tb-img errors), `./vm_cmp.sh` all native same.
- 2026-11-19 P4.10e1 done (§7.2 P4.10 *Done*). rt-eng zbc_interp.c: claim table in `zbc_actrun_t` (`act_claim`
  after the solve commit, `act_release` at `act_exit_node` and for cancelled `first` branches), `act_busy` /
  `act_concurrent_since` as pyvm `_busy`, `act_pin` pins the busy mask; `zbc_act_par_shared` counts only the
  parallel's own branches (sites owned by its node, as pyvm `ActivationTable.par`; a sub-action's activity is in its
  site's branch). zbc_engine.c `act_solve_supported` and pssc native.py `_cone_solvable` admit resource pools <= 64
  and refuse `table.par`. test_native_session.py `RES` (lock/share in sequence, parallel, nested lock; 3 tests x
  6 seeds C == py; pool [1] unsat on both). rt-eng 353, native session 87 + 1 skip, pyvm guard 111, matrix native
  355 + 128 xfail.
- 2026-11-19 P4.10e2 done (§7.2 P4.10 *Done*); P4.10e done. rt-eng zbc_interp.c: per-parallel branch table
  (`act_par_build`, at activation start), footprints dealt at SCOPE_ENTER of a parallel with shared locked pools
  (`act_footprints`/`act_probe`, MT19937, seed drawn only there), footprint mask in `act_busy`/`act_pin`. Refusals
  lifted: zbc_engine.c `act_solve_supported` (no `zbc_act_par_shared` check), pssc native.py `_cone_solvable`
  (no `table.par`). test_native_session.py: `t_pl` (`parallel { do L; do L; }`), `t_ph` (`parallel { do H; do L; }`)
  x 6 seeds C == py; pool [1] -> both unsat; refusal test admits `par`. rt-eng 353, native session 95 + 1 skip,
  pyvm guard 111, matrix native 355 + 128 xfail, doc refs pass.
- 2026-11-19 P4.10f1 done. Opt-in py-vs-C sweep: tests/unit/integration/act_sweep.py, hooked by conftest.py
  `_act_sweep` when `PSSC_ACT_SWEEP=c`: every be-bc `run_model` an integration test makes is rerun on the C engine
  in a forked child (crash = verdict) and its info `message()` lines and ok/error outcome compared; one JSON line per
  run to `PSSC_ACT_SWEEP_OUT` (default /tmp/act_sweep.jsonl). First sweep: 2109 runs "engine error (-12)": the C
  verifier refused a branch to `len(code)` (the oracle's fall-off exit, from `repeat`/`if` lowering); zbc_interp.c
  BR/BRZ check now `> n` (rt-eng test_engine_safety.py `test_a_branch_to_the_end_of_the_coro_completes_it`); this
  also removed 120 segfaults in test_bc_resources.py. Sweep now: 17669 runs, 17444 same, 180 refused (loop-index
  cones, f3), 45 differ (categories in the packet's P4.10f notes). Default integration run unchanged (678 pass,
  4 skip, 4 xfail); rt-eng 354, native session + doc refs pass, matrix native 128 xfail unchanged.
- 2026-11-19 P4.10f2a done. The C engine now schedules called bodies as the Python VM does: a called function
  (CALL, depth > 0) runs at once instead of waiting a turn at its entry (zbc_interp.c `zbc_interp_task`, idx 0
  returns early only for depth 0), and the caller of a blocking INVOKE or a SELECT branch yields once when the
  callee returns (`invoke_yield`), as the oracle readies it at the back. Fixes `parallel join_first(1)` dropping a
  started branch and a recursive call letting another branch run. New tests/unit/integration/test_act_c_schedule.py
  (5, C == py). Sweep: 17451 same, 180 refused, 42 differ (A footprints 40, D if-chain 1, E comp read 1). Integration
  683 pass, 4 skip, 4 xfail; rt-eng 354; native session 100 + 1 skip; matrix native/session 455 + 128 xfail; doc
  refs pass; bench native rebuilt 14/14, vm_cmp native same.
- 2026-11-19 P4.10f2b done, no engine change. All 40 category-A diffs were the test's calibration runs
  (test_bc_resources.py `_runs(footprints=False)` clears `table.par` in memory so pyvm runs without footprints); the C
  engine derives the parallel footprints from the image's activity table (zbc_interp.c `act_par_build`), so it cannot
  see that switch and an image "without footprints" does not exist. Calibration models now set
  `act_sweep_unmirrored` and the sweep skips them (act_sweep.py `wrap`). With footprints, C and py pick the same lanes
  on every seed: new test_act_c_schedule.py `test_footprints_give_the_same_lanes_on_c` (40 seeds). File sweep: 247
  same, 40 skipped. Integration 684 pass, 4 skip, 4 xfail; doc refs pass.
- 2026-11-19 P4.10f2c done, so P4.10f2 is done: the integration sweep on C has 0 differ / crashed (17494 same,
  180 refused for f3, 41 skipped). D: bc lowering gives each temporary its own register, and test_bc_if_chain.py's
  600-clause chain uses ~9700; frames are sized per coroutine (verify_coro) and a big frame gets its own stack block,
  so zbc_interp.h ZBC_MAX_REGS 4096 -> 65536 and ZBC_MAX_FRAME_BYTES 64 KiB -> 1 MiB. E: C now marks a node whose
  instance its solve has not chosen (`COMP_UNCHOSEN`, set by zbc_interp.c `child_node` when the node has more than
  one candidate or its parent is unchosen), and LD_COMP/ST_COMP there halt with new ZBC_ERR_COMP_UNCHOSEN (-28) and
  pyvm's message (ops_proc.py `_comp_slot`). New tests in test_act_c_schedule.py (8). rt-eng 354, native/matrix 455
  + 1 skip + 128 xfail, integration 686/4 skip/4 xfail, doc refs pass; bench native rebuilt 14/14, vm_cmp 14 same.
- 2026-11-19 P4.10f3 done: a loop index read by a `with` is solved on C. be-bc ACT `zbc_actvar.loop0` (was `_pad`):
  a u32 list `n, (coro, frame slot)*` of the coros holding `loop_local` (model.py `_serialize_activations`);
  zbc_reader.c checks it; zbc_interp.c `act_loop_counter` walks `frame_up` (caller on the thread's stack, else
  `parent_locals`) to the frame running `loop_node`, as pyvm `_loop_counter`; free in footprint probes.
  zbc_engine.c `act_solve_supported` and pssc native.py `_cone_solvable` no longer refuse it. Sweep: 17682 same,
  0 refused/differ, 41 skipped; integration 694/4 skip/4 xfail (8 new in test_act_c_schedule.py); rt-eng 354,
  be-bc 424, native/matrix/release/doc refs pass; bench native rebuilt 14/14, vm_cmp 14 same.
- P4.10f4 (2026-11-19): docs/test-command.rst gains "What the C engine runs" (what C runs, what it refuses with
  `--engine c`); docs/cli.md `--engine` row updated. tests/unit/integration/act_sweep.py `GUARD` (14 files: pools,
  picks, claims, loop index, cone reuse, scheduling, scope solve, lookahead, generic and comp-attr constraints)
  is swept by default via conftest.py `_act_sweep`; any C run not `same`/`skipped` fails the test at teardown
  (checked by forcing a bogus C result). Full sweep stays opt-in (`PSSC_ACT_SWEEP=c`). Default integration
  694/4 skip/4 xfail in ~2.5 min (was ~40 s without the guard); native/matrix 1164 + 1 skip + 128 xfail; doc refs 709.
  P4.10 done.
- 2026-11-19 P5.1a: `--platform replay:<trace>` (src/pssc/testing/replay.py `ReplayPlatform`; vm.py
  `load_platform`/`Hosted.replay`, `Hosted.make(default, test=)`; runner.py `_session_for(test=)`,
  `Kind.DIVERGED`, `ReplayPlatform.left` checked after the run). Accesses and user imports (not pssunit_pkg's)
  are one ordered sequence; writes compare data; reads return recorded data. Docs: test-command.rst
  (platform section + options table), cli.md, test-internals.rst (Kind list). Test:
  tests/unit/test_replay_platform.py (5). Unit 3951 pass + 13 skip + 4 xfail + 1 baseline (435 s); daemon 4.
- P5.1b1 (step 263): replay under `pssc dbg` (devenv/debug/sessions.py `Registry.start` passes `test=item.qname`
  to `_session_for`) and the pytest plugin; `--seed` defaults to the trace header's `seed` (replay.py
  `recorded_seed`, vm.py `Hosted.recorded_seed`, used by cmd.py, sessions.py, pytest_plugin.py). Docs:
  test-command.rst replay section. Tests: test_replay_platform.py (7).
- P5.1b2 (2026-11-19): replay.py `load_trace` keeps `notify` irqs on the next access/import step (`irqs`);
  `ReplayPlatform._next` raises them via `Ctx.raise_irq` after serving that step; vm.py `Session.__init__` calls
  the platform type's `attach_session`. An irq recorded after the last step is not raised. Docs: test-command.rst
  replay section. Test: test_replay_platform.py `test_a_recorded_interrupt_is_raised_again` (8 pass).
- P5.1b3 (2026-11-19): replay.py `_records` reads NDJSON or binary (`binary.is_binary`/`binary.read` from be-bc);
  `load_trace` names a nameless (C) import `""`; `trace_for` falls back to `<test>.pstrace`; `recorded_seed` reads
  either form. C-engine `access`/`import` details have the same keys (zbc_interp.c `ev_access`/`ev_import`), but no
  `file`/`line`. Docs: debugging.rst "Replay and time travel" (D6), test-command.rst. Tests: test_replay_platform.py
  `test_a_binary_trace_replays`, `..._without_lines_reports_the_divergence` (10 pass). P5.1 done.
- P5.2a (2026-11-19): be-bc debug.py `Stub` counts statement starts (`tick`, `history`), command `run_to {tick,
  breaks?}` (reason "goto"), capability "run_to"; test_debug_stub.py.
- P5.2b (2026-11-19): frontend.py `DebugSession.back`/`reverse` re-run the same run (`respawn`: same image + seed,
  fresh platform, set in sessions.py `_py_debug`) to an earlier tick, counting breakpoint hits on the way (`rc`
  restores the counts of the stop it lands on); `pssc dbg back|rc`, `start --trace` (= `--platform replay:`);
  stop schema: reason "start", `tick`. `step` at entry takes the running thread. Tests: test_dbg_back.py (4),
  test_replay_platform.py `test_dbg_start_trace_replays_and_steps_back`.
- P5.2c (2026-11-19): dap.py CAPABILITIES `supportsStepBack`; `stepBack`/`reverseContinue` -> `DebugSession.back`/
  `reverse` (a session that cannot go back refuses the request: frontend.py `check_back`); stop reason "start" ->
  DAP "entry". `pssc dbg rc --watch PATH` (`reverse(watch=)`): back to the last write of a register/field through a
  temporary watch (watches.py `WatchSet.remove`), landing at the writing statement's start. docs/debugging.rst
  "Replay and time travel"; cli-reference.rst regenerated. Tests: test_devenv_dap.py
  `test_step_back_and_reverse_continue`, test_dbg_back.py (5), transcript caps. P5.2 done.
- P5.3 (2026-11-19): a — be-bc interp/defuse.py: static defs/uses per statement in `stmt` events; pssc TraceWriter
  names each access's register and fields. b — `pssc trace why` (devenv/trace_why.py; component fields absolute,
  `c:<path>`). c — `pssc trace slice --reg` and `pssc trace diff A B` (devenv/trace_reg.py; diff exits 1 on a
  divergence); the match rule is replay.py `same_step`, shared with `ReplayPlatform`. Schemas pssc.trace-why/1,
  -slice/1, -diff/1; docs/debugging.rst "Where a value came from"; T5.3 in §8. `why --session SID` dropped: a
  session's trace is a file (`pssc dbg start --trace`), so `why FILE` covers it. P5.3 done.
- P5.4a (2026-11-19): pssunit test actions export to generated code. targets/export_action.py `_is_marker`: the
  base chain ends at an empty package-scope action (`pssunit_pkg::test_a`). A refused `--export-action` (and
  "does not yet render exported actions" on op-model-c) is a user error (driver.py wraps `tgt.run`), exit 1.
  op-model-py renders a sub-component constructor called outside a constructor (a fixture's `@setup` binding its
  device) as constructing it anew (py/lower_progseq.py `_BodyEmitter.call_subcomp_ctor`). Tests in
  tests/progseq/test_op_model_entry.py. `wb_dma_fx_c::sw_copy_a` now exports to op-model-py and op-model-sv.
- P5.4b/c (2026-11-19): `pssc test --engine gen-py` runs each exportable test on pyvm (the reference: register bases,
  bus) and then as generated op-model-py (testing/gen_py.py `Cache`, `build`, `run`, `Adapter`) on a fresh fake with
  the same doubles; the verdict is the generated run's checks, and a bus access that is not pyvm's fails it
  (`Kind.DIVERGED`, "diverges from pyvm at bus access N: expected ..., got ..."; `gen_py.diverges`). Test functions and
  tests the exporter refuses SKIP with the reason (runner.py `_gen_skip`, also before `run_property`); generator
  errors are an ERROR for that test only (`Built.broken`). Generated accesses that match the reference are named to
  the fake by the reference's register (aliases). op-model-py folds register-group offsets through sub-components
  (py/lower_progseq.py `_fold_group`). T5.4 in tests/unit/test_gen_py.py (pass/CHECK, calibrated DIVERGED by
  shifting the generated writes, skips, `diverges`). op_model suite on gen-py: 4 pass, 17 skip.
- P5.4d1 (2026-11-19): a test runs as generated op-model-sv under Verilator, offline (testing/gen_sv.py `build`,
  `run`, `parse`, `Log`): the testbench's platform is generated from the root's `<root>_imp_if` (every pssunit and
  user import is a task there), serves each read and each import output from values given before the run (`+served=`
  file; the reference run's, in order) and logs every access and import call (`PSSGEN` lines). One Verilator binary
  per root component, the test chosen by `+test=`. A read past the served ones ends the run (a poll fed stale data
  would not end); that read is already a divergence. gen_py.py `generate` (shared with gen_sv), `Adapter.returns`.
  tests/unit/test_gen_sv.py: sw_copy_a's SV accesses equal pyvm's.
- P5.4d2 (2026-11-19): `pssc test --engine gen-sv` (runner.py `_run_gen(..., engine)`, `GEN_ENGINES`; cli.py,
  native.py `ENGINES`). The import outputs the SV run is served come from a gen-py run of the same test (the
  generated code calls the check rewrite's `double_*` imports; recording pyvm's import returns was not tried); gen_sv.py `Cache` (Verilator binary + `Built.py`),
  `check` (gen-py run, SV run served the reference's reads, `diverges`), `replay` (the SV log's accesses and import
  calls applied in order to a fresh fake: the checks and cards). An SV run that stops without a divergence is an
  ERROR. sv/lower_progseq.py `_sub_reg_group`: `dma.regs.get_offset_of_instance(..)` folds through sub-components
  (as py's `_fold_group`; it refused the whole module). op_model suite `--engine gen-sv`: 4 pass, 17 skip (as
  gen-py). tests/unit/test_gen_sv.py +2 (PASS/CHECK/SKIP; calibration: one served read fewer -> DIVERGED).
  cli-reference regenerated. Unit 3995 + 13 skip + 4 xfail + baseline test_ir_walk; daemon file 4.
- P5.4e (2026-11-19): docs/writing-tests.rst "Generated code" (the reference run, the generated run on a fresh fake,
  the DIVERGED message, what SKIPs, gen-sv's Verilator build dir and served reads / gen-py import outputs);
  test-command.rst and cli.md `--engine` rows name gen-py/gen-sv. Help text unchanged (cli-reference current).
  P5.4 done.
- P5.5a (2026-11-19): be-bc activation.py `ActivationTable.bound_blob` (bounds as `>=`/`<=` constraints, an extra
  blob), `Activation(bounds=)`, `Activation.solved`; vm.py `VM.solve_bounds`; oracle.py `run_model(solve_bounds=)`,
  `RunResult.solved`. tests/unit/integration/test_bc_solve_bounds.py (3).
- P5.5b (2026-11-19): a plain SOLVE of the entry action (outside a cone) keeps to the bounds too: be-bc vm.py
  `VM.set_entry` (entry frame, `VM.solved` = its activation's `solved` or a dict of its own), ops_orch.py `_bounded`
  (bound blob from model.py `SolveProblem.source`, the in-memory ScSolveProblem; none from an image). pssc
  bc_session.py `Session.solve_bounds/solved`; runner.py `run_one(bounds=)`, `_shrinkable`, `_shrink` (per top-level
  field: binary search pinning the field to `mid`, `[0, mid]` if that is unsat; a shrunk field stays pinned;
  `SHRINK_RUNS` 64), `Result.shrunk`, detail `(seed S, shrunk: x=128 (from 202))`. test_pssunit_isolation.py +1.
  Unit 4000 + 13 skip + 4 xfail + baseline test_ir_walk; daemon file 4; be-bc 432.
- P5.5c (2026-11-19): docs/writing-tests.rst "Property tests" describes shrinking (Python engine only; C, gen-*,
  replay and the `--trace fail` re-run keep the unshrunk seed); T5.5 in §8. P5.5 done.
- P5.6a (2026-11-19): testing/reg_semantics.py reads IP-XACT (`readAction`, `modifiedWriteValue`, access,
  volatile) or a JSON table into field kinds; `pssc test --reg-semantics FILE` (also `pssc dbg start`, DAP launch
  `regSemantics`, pytest ini `pssunit_reg_semantics`, `pv.load(reg_semantics=)`) lowers them into
  `model.registers` (bc_session.py `_registers`): file kinds are a base, `field_access` annotations win, fields the
  PSS register lacks are dropped; the image cache key holds the file's mtime. docs/test-command.rst. Tests
  tests/unit/test_reg_semantics.py (7).
- P5.6b (2026-11-19): `.rdl` via optional `systemrdl-compiler` (extra `pssc[rdl]`, lazy import; absent -> usage
  error): reg_semantics.py `rdl`/`_rdl_terms` (rclr->RC, woclr/woset->W1C/W1S, sw r/w->RO/WO, hw-written/hwclr/
  hwset/counter rw->VOLATILE); a file with no addrmap is wrapped (each root regfile, else reg, type) and keyed
  from the block. Bench wb_dma_ch_regs.rdl: int_err/int_done/int_chk_done RC. Tests +3 (skip without systemrdl).
- P5.6c (2026-11-19): docs/platform-doubles.rst field kinds name `--reg-semantics` (base under annotations);
  T5.6 in §8. P5.6 done.
- P5.7a (2026-11-19): `--cov` reports `envelope`: per operation under test (a public function, op_api
  `public_functions`, of a component type the boundary keeps; `<comp type>::<fn>`) its calls and a bin per
  parameter and the result (enum items; false/true; ints of up to 3 bits each value, else zero/other/max and,
  signed, min/neg). Only the outermost operation call counts (a call event's coroutine maps to its function by
  `fn_qname`). testing/coverage.py `envelope_ops`, `Coverage(ops=)`, `merge`, `report` (summary `envelope`);
  runner `_run_item(cov=ops)`; cov_cmd `totals` summary. The .cdb has no envelope until P5.7b (two JSON-vs-cdb
  tests leave it out). Tests: tests/unit/test_cov_envelope.py.
- P5.7b (2026-11-19): crosses on request: `@pssunit_pkg::cover_cross {.params = "m,result"}` (repeatable; a
  name that is not a point of the operation drops the cross) -> `envelope_ops` `crosses`; report
  `envelope.<op>.crosses.<"a,b">` {points, bins "x,y"} (cross bins without the result counted at the call, with
  it at the return); summary and merge include them. `.cdb`: cov_ucis.py `_envelope` = a covergroup per
  operation under its component type's unit (attributes pssunit.kind/op/calls; coverpoint per param and
  `result` with pssunit.type; `createCross`), test associations keyed ("e", op, kind, name, bin); cov_cmd.py
  `load_cdb` reads it back (`_envelope`). The two JSON-vs-cdb tests compare the envelope again.
  docs/writing-tests.rst annotation row. Tests: test_cov_envelope.py (crosses, cdb round-trip).
- P5.7c (2026-11-19): the C engine's call events already carry arguments and result, so `--cov --engine c`
  gives the same envelope as py (no rt-eng change); test `test_cov_envelope_on_the_c_engine`.
  docs/coverage.rst "Operation envelope" (bins per type, outermost call, `cover_cross`, .cdb shape) and the
  report's `envelope` key; T5.7 in §8. P5.7 done.
- P5.8 (2026-11-19): expectation mocks. pssunit_pkg.pss imports `expect_write(reg, field, v)` ("" field =
  whole value), `expect_read(reg)`, `check_seq(what)` (subsequence of the bus log since the last check_seq),
  `check_seq_strict(what)` (exactly). imports.py `_seq`, RunState `expected`/`seq_mark`/`reasons`;
  check_rewrite `_REGISTER` (check_seq*) and `DOUBLES` (expect_*); vm.py `_counted` calls
  `imports.unchecked` (unchecked expectations fail), `failure_cards` shows the reason. Works on the C
  engine unchanged. docs/writing-tests.rst "Expected access sequences"; T5.8. Generated code not tried.
  Unit suite after P5.8: 4027 pass, 13 skip, 4 xfail, 1 baseline; test_pssvm_shim `test_verilator_dpi_native`
  no longer pins import fn ids (pssunit_pkg's new imports moved them by 4).
- P5.9 (2026-11-19): deferred, like P3.F. Verilator 5.055 refuses `--savable` with `--timing` ("Unsupported:
  --timing and --savable not supported together") and with class `new` (UVM); the bench needs both, and
  the engine's state (Python or C) lives outside the saved model anyway. Needs a simulator with `$save`.
- P5.10a (2026-11-19): testing/doctest.py `examples(ctx)` collects ```` ```pss-example ```` blocks from component
  function docs (IR `Function.doc`, markers already stripped); `synth` wraps each in a
  `doc_<comp>_<fn>_<n> : pssunit_pkg::fixture_c` with `<comp> dut;` and one `@pssunit_pkg::test t_example`.
  cmd.py `_with_doc_examples` (from `collect`) writes `.pssunit/doctest.pss` and translates again only when
  there are examples. Test: tests/unit/test_pssunit_doctest.py (4; 1 pass + 1 fail end to end).
- P5.10b/c (2026-11-19): the wrapper header `// pss-example <comp>::<fn> <n> @ <file>:<line>` names the block's
  first line in the source (doctest.py `_fences` finds the fences above the decl); `doctest.source` maps a
  wrapper `file:line:col` back (check_rewrite.py `CheckSite.location`, so cards say `dev.pss:17:13`) and
  `doctest.item_loc` gives a wrapper test the fences' span (catalog.py `_item`; `FILE:LINE` selects it,
  `--collect-only`/CTRF show it). `pssc test --no-doctest` (`collect(doctest=False)`). C engine runs them too.
  Docs: writing-tests.rst "Examples in doc comments", test-command.rst table, cli-reference.rst regenerated.
  Tests: test_pssunit_doctest.py 8. Unit 4039 pass + 13 skip + 4 xfail + 1 baseline; dbg daemon 4.
- 2026-11-19 P5.11a: `--line-directives` (op_model.py `OpModelTarget.add_args`, all op-model targets) puts
  `#line N "/abs/file.pss"` before each body statement in C and C++. body_walker.py `BodyWalker.source_line` /
  `line_directive` (off: no change), `source_loc` (no loc or no file on disk: none), `c_line_directive`;
  C: `CSettings.line_directives`, `COpModelBackend.line_directives`, set in `OpCtx.body_emitter` and the ctor
  emitter; C++: `_BodyEmitter(line_directives=)` via `generate`. cli-reference.rst regenerated. Tests:
  tests/progseq/test_line_directives.py 6 (C builds and passes the wb_dma trace; `gcc -g -S` line table names
  the .pss; C++ header compiles). Goldens: 7 pass + the 5 banner-only C fails (baseline). tests/progseq also
  has 10 pre-existing fails unrelated to this (banner, StmtFork registry row, bc lowering, IR counts, W64 XPASS).
- 2026-11-19 P5.11b: SV `` `line N "file.pss" 0 `` (sv/lower_progseq.py `_BodyEmitter.line_directive`); the flag
  goes progseq_tgt.py `backend_for` -> `SvOpModelBackend.line_directives` -> `emit_component_class` /
  `emit_package_functions` (keyword `line_directives`, through `_emitter`, `_initialize_def` and the
  `_init_hook_defs`/`_method_defs`/`_entry_defs` helpers). Test: test_line_directives.py SV case (Verilator builds
  and runs test_sv_op_model_sim.py's model with the option: same trace; every `line names model.pss). Goldens
  unchanged (7 + 5 banner-only C fails); tests/progseq -k "sv or comment" 673 pass, only the known W64 XPASS fails.
- 2026-11-20 P5.11c: Python `# /abs/file.pss:N` comment at the statement's indent (py/lower_progseq.py
  `_BodyEmitter.line_directive`); flag via py_progseq_tgt.py `backend_for` -> `PyOpModelBackend.line_directives`,
  set on each body emitter it builds (`_emitter`, package functions, non-hier ctor; initializers are exprs). Test:
  test_line_directives.py py case (only comment lines added, lines exist, `py_compile`). Help text names Python;
  docs/cli.md option row + section, docs/debugging.rst "Debugging generated code"; cli-reference.rst regenerated.
  Goldens 7 + 5 banner-only C fails; tests/progseq -k "py and not pyvm" 3484 pass, 13 known fails. P5.11 done.
- 2026-11-20 P5.12a: testing/mutate.py `Mutant(kind, file, line, col, old, new, pos)`, `mutants(paths,
  test_files, kinds)`, `apply`, `model_files(paths, items)` (drops `TestItem.loc.file`), `mask` (comments and
  strings blanked in place). Operators need spaces around one-char ops; widths `bit[N]` not mutated; field =
  adjacent packed-struct decl lines swapped + `.f` -> each neighbour (never `rsvd*`). op_model: 219 mutants
  (81 field, 78 constant, 41 operator, 19 offset), incl. SEEDED offset_int_msk_a, offset_bank,
  field_done_err_swapped, driver_wrong_field. Test: tests/unit/test_mutate.py 11 pass.
- P5.12b (step 300): `pssc test --mutate` (cli.py; cmd.py `_cmd_test` refuses a red baseline with exit 2, then
  mutate.py `run_mutants`). Per mutant `tests_for` = tests whose decl or `Slicer.calls` unit spans the line (no unit:
  all; none: NOT COVERED); `run_one` = child `pssc test <files, mutated one in a tmp dir> <ids> --json -x` in a tmp
  cwd, timeout max(10 s, 10 x baseline); rc 1/timeout killed, 0 survived, else invalid. `-j N` = N mutants at once.
  Exit 1 on survivors or not covered. Docs: test-command.rst row; cli-reference.rst regenerated. T5.12 in
  tests/unit/test_mutate.py (14 pass): the 4 seeded-equal mutants killed; one test only -> SURVIVED/NOT COVERED rc 1.
- P5.12c (step 301): `--json` adds `mutant.end` per mutant {file,line,col,kind,old,new,status,by,tests} and
  `mutate.end` {killed,survived,uncovered,invalid,score,exit} after `run.end` (schema pssunit.events-1.json).
  `--mutate-kinds K,K` (cli.py `_mutate_kinds`), `--mutate-max N` (mutate.py `sample`, by --seed, source order).
  `Mutant.__str__` shows the first line of a multi-line edit + " ...". Docs: test-command.rst, cli.md,
  cli-reference.rst. test_mutate.py 20 pass; doc/help/output tests 881 pass. P5.12 done.
- P5.13 (step 302): owner: skill lives in a local clone packages/agent-skills-pss (uncommitted), not a
  request. skills/pssunit/{SKILL.md, reference/pssc-help.json + generated cli.md, examples/wb-dma.md},
  scripts/gen-pssunit-cli.py (--check), tests/test_pssunit_skill.py (live-snapshot test runs with pssc on
  PYTHONPATH). 8 pass; test_entrypoint_registered fails as before (package not installed). P5.13 done.
- Close (step 303, 2026-11-20): full unit suite 4062 pass + 13 skip + 4 xfail + 1 baseline (test_ir_walk.py),
  447 s; test_devenv_dbg_daemon.py alone 4 pass. Owner: P3.F and P5.9 dropped (no simulator). Plan finished.
