# A develop / test / debug environment for PSS operation models

Status: **draft for review, revision 2** (2026-11-18). Nothing here is
implemented yet.

Inputs: `testing_and_debugging_pss.md` (the requirements sketch, especially
*Key capabilities*), the DVCon 2027 draft *Separating Device Operations from
Test Scenarios for Twice the Portability* (`~/data/notes/2027/DVCon/paper_draft`),
a survey of what pssc / be-bc / rt-eng / covsight already provide, and a
survey of test and debug tooling in other ecosystems (§19).

Revision 2 records the review decisions in §1 and changes the design to
follow them: actions as tests (with action functions as an extension), hot
reload in a running simulation, a fast CLI instead of MCP, covsight as the
coverage database, a native engine for the simulator, and Python-hosted
mocks. A later addition (D8, §13) records formal analysis with
constraints: unreachable code, corner-case witnesses, proofs and equivalence.

---

## 0. Summary

The operation-model strategy asks IP DV teams to write device programming
sequences in PSS once and to generate the SV, C and Python layers from that.
The paper argues the case on **portability**. It says nothing about the
**inner loop**: how someone writes a sequence, tests it, finds out why it is
wrong, and proves it is right. If that loop means "generate C, build it, link
a test harness, run it, and debug the generated code", a team will reasonably
decide to write the C by hand, and the PSS step then looks like pure
overhead.

This design gives a PSS author a loop that is **faster, better instrumented
and easier to automate** than the best embedded-C loop (Unity/CMock/Ceedling
or GoogleTest on the host, then the same code on a CPU model in simulation).
It has ten parts:

1. **`pssunit`**: tests written in PSS. A test is an **action**, so
   fixtures and test families come from action inheritance, `super;` builds
   setup in layers, `rand` attributes make property tests, and activities
   test concurrency. Lightweight **function tests** on a suite component are
   also available. **Action functions** (a pssc extension) allow template-
   method fixtures. Checks report the values of their subexpressions.
2. **Platform doubles generated from the register model.** Every test gets
   a register-file fake with reset values and field semantics, with nothing
   written by hand. Behaviour is added in tiers: PSS reaction hooks, a
   **Python-hosted run in which any import, and any PSS function, can be
   implemented in Python**, then RTL.
3. **One execution model, `pssvm`**, built on bc and running from a loadable
   model image. It has two engines: the Python interpreter (reference,
   debugging, Python-hosted runs) and a **native engine** for running inside
   the simulator. Both produce one structured event stream and have a source
   location for every instruction.
4. **A simulator integration that only reloads, including in the middle of
   a session.** A generic SV package is compiled once, plus a typed facade
   that matches op-model-sv's `<root>_ctxt_if` and changes only when an API
   signature does. **Hot reload** swaps a new image into a running simulation
   between operations, so a fix doesn't cost the 30-minute SoC boot again.
5. **Coverage in three layers** (PSS code, registers/fields, operation
   envelope), stored in **covsight** (UCIS), per test, merged across engines
   and with the simulator's own coverage. This is the same database PSS
   functional coverage will use.
6. **Debugging over the Debug Adapter Protocol**: live in-process, live in a
   simulation (through a small engine debug stub, in the gdbserver style), and
   **time-travel over a recorded trace**. Breakpoints can stop on register
   fields. "Why" queries trace a value back to the PSS statement behind it.
7. **A fidelity guard.** `pssc test` applies the generators' gate, and
   tests can be re-run on the *generated* code with bus traces compared event
   for event.
8. **A fast CLI that is the AI interface.** A per-workspace daemon keeps the
   model parsed, lowered and loaded, so every `pssc` command answers in tens of
   milliseconds. Every command has `--json`. **Debug sessions are stateful CLI
   objects** that an agent drives with ordinary shell commands, and that a
   human can watch in VS Code at the same time. No MCP.
9. **Formal analysis with dv-solve** (§13): symbolic execution of the
   operations proves code unreachable (and records it in covsight as a
   UCIS exclusion with its proof), turns coverage gaps into generated corner-
   case tests that are then run to validate them, proves checks for all
   inputs, and checks two versions of an operation for equivalence.
10. **The argument against C** (§16): a sequence is tested against a
   generated fake in milliseconds, run unchanged against RTL with no rebuild,
   fixed and reloaded into a simulation that is still running, debugged at
   PSS source with register breakpoints and reverse execution, and covered at
   register level. An agent can do all of it from a shell.

---

## 1. Review decisions (2026-11-18)

| # | Decision | Where |
|---|---|---|
| D1 | **Coverage is stored in covsight** (UCIS), since that aligns with collecting PSS functional coverage later | §11 |
| D2 | **Actions are tests too**, so inheritance can be used. **Functions on actions** are supported as a pssc extension. | §6 |
| D3 | **Hot reload** in a running simulation session is a first-class feature | §10.6 |
| D4 | **No MCP.** The AI path is a fast, stateful CLI. | §14 |
| D5 | **Build on bc.** The Python interpreter is today's engine. A **native engine** is needed for good simulator integration. | §8 |
| D6 | **pyhdl-if in the simulator is to be tried** as the first way in, before the native engine | §10.4 |
| D7 | **Python-hosted runs**: run within a Python environment so functions can be implemented in Python for mocking | §7.3 |
| D8 | **Formal analysis with constraints** (dv-solve) to find unreachable code and corner cases, recorded as part of the design | §13 |

---

## 2. Problem, and the bar to clear

### 2.1 What a C team has today, at best

| Need | Best-practice C | Pain |
|---|---|---|
| Unit test on host | Unity + CMock (Ceedling), GoogleTest/GoogleMock, fff | Mocking registers means a HAL wrapper around every access, or `volatile` pointers aimed at a RAM array. CMock's strict ordering breaks on refactors. Each test file builds and links separately. |
| Stub / abstract device | Hand-written fake, or nothing | Someone has to write it, keep it in sync with the register map, and give it field semantics (RO, W1C, RC). |
| Run against RTL | Cross-compile for a CPU model in the SoC sim, or link through DPI | Each change means a firmware rebuild and often a sim rebuild **and a full re-boot of the SoC sim**. A blocking DPI import can't consume time. A stray pointer crashes the simulator (paper §II). |
| Debug | gdb on host; disassembly against waves on the CPU model | No link from a bus transaction in the waves to the C line. Long-sim failures have to be re-run. |
| Coverage | gcov/llvm-cov on host only | Nothing at register or field level, and nothing from the RTL run |
| Automation / AI | JUnit XML | No structured trace, no semantic queries |

### 2.2 The bar

- **Edit-to-result in ≤ 1 s** for a unit test on a fake, with no generated
  files in the loop.
- **Zero hand-written scaffolding** for the first test of a new operation.
- **The same test, unchanged, against RTL**, with no recompile of anything
  but PSS, and **no restart of a simulation that is already up**.
- **Debugging at PSS source everywhere**: in the editor, in the sim, and
  after the fact.
- **Every capability reachable from a shell command with JSON output** in
  well under a second, so an agent uses the same tool a human does.

---

## 3. Requirements

| # | Requirement | Section |
|---|---|---|
| R1 | Internal code coverage, limited to the boundary we care about | §11 |
| R2 | A unit-test framework with discovery for standalone runs | §6, §9 |
| R3 | Result checking and error reporting | §6.6, §9.2 |
| R4 | Debug trace | §8.5, §12.3 |
| R5 | Interactive debug, live and trace-based | §12 |
| R6 | Integration with simulation: run what you develop, via an interpreter integrated through SV, remote debug over a socket, no recompile of anything but PSS | §10 |
| R7 | Stub / mock environment support, including Python | §7 |
| R8 | Develop against an abstract model (stub) | §7 |
| R9 | What is tested is what ships: the generated layers behave as the tested model did | §8.6 |
| R10 | The test environment refuses what the generators would refuse | §8.6 |
| R11 | Everything AI-enabled, through a fast CLI | §14 |
| R12 | Hot reload into a running simulation | §10.6 |
| R13 | Find unreachable code and corner cases formally, using constraints | §13 |

---

## 4. Design principles

1. **PSS is the language of the test.** A firmware engineer shouldn't need
   Python to write a test, and a DV engineer shouldn't need SV. Python is a
   first-class **host and mock language** (§7.3), not a requirement.
2. **Interpret to develop, generate to deploy, compare to trust.**
3. **The register model is the mock.** Every double starts from the model's
   own `reg_group_c` tree (`reg_layout.collect_accessors`, the one register
   walk).
4. **One event stream feeds everything**: trace, coverage, debugger,
   snapshots, waveform links and CLI queries.
5. **Every run is reproducible from what it printed**: image hash,
   platform, seed and test ID, plus the image generation for hot-reloaded
   sessions.
6. **Locations always.** Every failure, check, sanitizer hit and engine
   error names a PSS `file:line:col`.
7. **Engines and platforms are peers behind interfaces.** Nothing assumes
   the Python engine, a particular simulator or a particular device model.
8. **The CLI is the API.** The editor, CI and agents are clients of the same
   commands. No feature exists only in one UI.

---

## 5. Architecture

```
          PSS sources (model + tests + mocks)        Python mocks/hosts (optional)
                        │                                        │
         pssparser ─► ast2ir ─► op-model gate (R10)              │
                        │                                        │
       ┌────────────────┼──────────────────────┐                 │
       ▼                ▼                      ▼                 │
  generators      pssvm lowering         test catalog            │
 (op-model-*)     (bc, debug mode)      (static discovery)       │
       │                │                      │                 │
       │     model image (.zbc): code, line table, provenance,   │
       │     API hash, catalog, reg map, generation id           │
       │                │                                        │
       │     ┌──────────┴──────────────┐                         │
       │     ▼                         ▼                         │
       │   pyvm (Python)          native engine (C)              │
       │   reference · debug ·    simulator · speed ·            │
       │   Python-hosted runs     no-Python sites                │
       │     │  hooks               │  hooks + debug stub        │
       │     └────────┬─────────────┘                            │
       │        platform seam: memory · imports · notify ◄───────┘
       │     ┌────────┼─────────┬──────────────┬─────────────┐
       │     ▼        ▼         ▼              ▼             ▼
       │  reg fake  PSS hooks  Python mocks   RTL (UVM/BFM)  replay
       │                │
       │         event stream (NDJSON / binary)
       │    ┌──────┬────┴────┬─────────┬─────────┬──────────┐
       │    ▼      ▼         ▼         ▼         ▼          ▼
       │  trace  covsight   debug     snapshots  wave-link  hot-reload
       │         (UCIS)     front end                       control
       │                       │
       │                 pssd daemon ◄── pssc CLI (humans, agents, CI)
       │                       └──── DAP ──► VS Code / other editors
       │
       └──► differential run: same test on generated py/sv, traces must match
```

| Component | Home | New or existing |
|---|---|---|
| `pssunit` PSS package (test actions, checks, doubles API) | `src/pssc/share/pss/pssunit/` contributed as a **prelude** (`pssc.preludes`) | New |
| Action functions (extension) | pssparser grammar and linker; `ast2ir`; bc lowering; `export_action.py` | New (§6.3) |
| Test catalog | `src/pssc/testing/catalog.py` | New; uses `pssc.annotations` |
| pssvm lowering: operation and test entries, debug mode | `packages/zuspec-be-bc/lower/` | Extends bc |
| pyvm hooks, stepping, generations, Python dispatch | `packages/zuspec-be-bc/interp/` | Extends `VM.run_frame` |
| Native engine | `packages/zuspec-rt-eng` | Extends the walking skeleton (§8.4) |
| Daemon and CLI (`pssd`, `pssc test/dbg/trace/cov/sim/model`) | `src/pssc/devenv/` | New |
| Register fake, Python host API | `src/pssc/testing/` | New; built from `reg_layout`, extends `pssc.testing` |
| Debug front end (DAP, CLI sessions, engine debug protocol) | `src/pssc/devenv/debug/` | New |
| SV host package, facade generator, reload control | `src/pssc/share/sv/pssvm/`, target `op-model-sv-vm` | New |
| Coverage database | `packages/covsight-core` (UCIS); pssc writes and queries it | Existing; pssc adds a producer |
| Symbolic engine `pssvm-sym` (formal, §13) | `src/pssc/devenv/formal/`, solving with `packages/dv-solve` | New |
| VS Code: Test Explorer, debug, coverage | `vscode-pss-support` | Extends |

---

## 6. Writing tests: `pssunit`

### 6.1 Two forms of test

| Form | What it is | Best for |
|---|---|---|
| **Test action** (primary) | A concrete action derived from `pssunit::test_a`, declared in a suite component | Fixtures by inheritance, families of tests, `rand` property tests, concurrency through activities, scenario tests |
| **Test function** | A target function of a suite component marked `@pssunit::test` | A three-line check of one operation |

Both are discovered statically (§6.7), run on the same runner, and report the
same way.

### 6.2 Test actions and inheritance

The suite is a component holding the model under test. Tests are actions in
it. Abstract actions are fixtures and are never run. Concrete ones are tests.

```pss
import pssunit::*;
import wb_dma_types_pkg::*;

@pssunit::under_test {.comp = "wb_dma_c"}              // coverage boundary, §11.2
component wb_dma_suite : pssunit::suite_c {
    wb_dma_c dma;

    exec init_down {
        dma.initialize(pssunit::base("dma"));          // base from the platform
    }

    // ---- Fixture: a channel with a device that completes ----
    abstract action ch_fixture_a : pssunit::test_a {
        rand int in [0..7] ch;
        rand wb_dma_ch_cfg_s cfg;
        constraint cfg.tot_sz in [1..64]; cfg.chk_sz == 0;

        exec body {
            pssunit::complete_after(comp.dma.ch[ch].regs.csr, "ch_en", 2);
        }
    }

    // ---- Tests: derive, call super; for the fixture, then test ----
    action single_transfer_completes : ch_fixture_a {
        exec body {
            super;                                      // fixture set-up (LRM 17.1)
            check(comp.dma.ch[ch].transfer_single(cfg) == wb_dma_status_e::WB_DMA_DONE);
            check_field(comp.dma.ch[ch].regs.sz, "tot_sz", cfg.tot_sz);
        }
    }

    @pssunit::property {.runs = 200}                    // rand attributes are solved per run
    action any_cfg_programs_sz : single_transfer_completes { }

    @pssunit::expect_error {.text = "already has an operation in progress"}
    action double_start_is_refused : ch_fixture_a {
        exec body {
            super;
            comp.dma.ch[ch].transfer_single_start(cfg);
            comp.dma.ch[ch].transfer_single_start(cfg);
        }
    }

    // ---- Concurrency: a compound test action ----
    action two_channels_in_parallel : pssunit::test_a {
        activity {
            parallel {
                do single_transfer_completes with { ch == 0; };
                do single_transfer_completes with { ch == 1; };
            }
        }
    }
}
```

What inheritance gives, using only constructs the toolchain already handles:

- **Layered set-up**: `exec body { super; ... }`. `export_action.py`
  already lowers `super;` (each base body becomes its own private method,
  `EntryPoint.supers`), and so does bc.
- **Test families**: a derived action with no body re-runs its base's test
  under its own constraints, attributes or annotations.
  `any_cfg_programs_sz` above is the same test as a 200-run property test.
- **Suites by inheritance**: a derived suite component (`component
  wb_dma_v2_suite : wb_dma_suite { ... }`) inherits every test action. This is
  the JUnit abstract-test-class pattern: one test set run against several
  variants or configurations of an IP.
- **Property tests come for free**: `rand` attributes and constraints are
  ordinary PSS. Each run is solved with the model's own constraints, with
  seeds and shrinking (§6.5).
- **Concurrency**: a compound test action's activity (`parallel`,
  `schedule`) runs operations on several channels at once. In a simulation
  that is real concurrency against RTL (§10.3).

### 6.3 Action functions (pssc extension)

LRM 3.x actions have exec blocks but no functions, so set-up and tear-down
can only be layered by `super;` at the top of a body. Plenty of test fixtures
want the **template-method** pattern instead: a base action that defines the
flow and derived actions that override steps. pssc will accept **functions
declared in an action** as an extension, reported as a pssparser warning (as
PSS120 is for exported instance functions) so a model knows when it has left
the standard:

```pss
abstract action dma_test_a : pssunit::test_a {
    rand int in [0..7] ch;

    function void arrange() { }                         // default: nothing
    function void act();                                // must be overridden
    function void verify() { }

    exec body { arrange(); act(); verify(); }
}

action stop_mid_transfer : dma_test_a {
    function void arrange() { pssunit::complete_after(comp.dma.ch[ch].regs.csr, "ch_en", 100); }
    function void act()     { comp.dma.ch[ch].transfer_single_start(cfg_default());
                              check(comp.dma.ch[ch].stop_channel() == wb_dma_status_e::WB_DMA_STOPPED); }
}
```

Semantics, chosen to match what pssc already does for component functions:

- **Scope**: an action function sees the action's attributes (read-only
  outside `pre_solve`/`post_solve`, as exec code does), `comp`, and its
  parameters. It may call target functions of the component tree.
- **Virtual**: a call from a base body reaches the most-derived override.
  This is resolved **statically per concrete action type**, by the same
  copy-into-derived approach `comp_inherit.complete` uses for component
  functions, so no target needs a dispatch table. `super.f()` calls the base
  version.
- **A function declared with no body is abstract**. A concrete action that
  leaves one unimplemented is a located error.
- **Where it works**: bc (lowered as a call with the action frame as `this`)
  and the op-model targets through `--export-action`, where an action's
  functions become private methods next to its exec body, as `super` bodies
  are today.

The work is a pssparser grammar and linker change (functions in an action
body, symbol scope), an `_BODY_ELEMENTS` row in ast2ir, bc lowering and
export-action rendering. It's independent of the rest and can come in Phase 2.
It isn't needed for tests to use actions (§6.2 works with `super;` alone).

### 6.4 Test functions

For the small case, a target function of the suite component is a test:

```pss
@pssunit::test
target function void reset_values_read_back() {
    check_reg(dma.ch[0].regs.csr, 0);
}
```

It shares the suite's fixture (`init_down`) and the runner's isolation.

### 6.5 Isolation, set-up, tear-down, seeds

- **Fresh tree per test.** Building the component tree costs microseconds in
  either engine, so per-test isolation is the default on fakes. PSS component
  attributes are immutable after init (LRM 9.1.6), so there's little to leak
  anyway.
- **Set-up**: the suite's `init_down`/`init_up`; the action's
  `pre_solve`/`post_solve` and `super;` chain or `arrange()`; or an optional
  `@pssunit::setup` function.
- **Tear-down**: `@pssunit::teardown` on a suite function, always run, then
  the sanitizers (§6.8).
- **On RTL**, a per-test DUT reset is the platform's `reset()` hook. A
  platform that can't reset declares one test per sim, or runs tests in a
  shared session with **`@pssunit::session`** fixtures (boot once, keep the
  DUT up across tests, as §10.6 depends on).
- **Seeds and shrinking**: every run prints its seed. A failing property run
  shrinks by re-solving with tightened bounds toward each `rand` field's
  minimum (Hypothesis-style) and reports the minimal counterexample. Failing
  seeds are kept in `.pssunit/regressions/<test>.seeds` and always re-run, as
  Go's fuzz corpus is.

### 6.6 Checks and error reporting (R3)

| Call | Meaning |
|---|---|
| `check(bool e)` / `require(bool e)` | Continue / stop on failure |
| `check_eq(a, b)`, `check_ne`, `check_lt`, … | Typed comparisons; report both sides |
| `check_reg(reg, v)` / `check_field(reg, "f", v)` | Read through the **back door** (no side effects, no bus access) and compare |
| `check_written(reg, "f", v)` / `check_not_accessed(reg)` | Against the bus log for this test |
| `fail(string)` | Unconditional |

Checks report their subexpressions, as pytest's assertion rewriting does.
A pssc pass rewrites each `check*` call to capture each operand once and to
carry the source text. That pass is shared by bc lowering and by the
generators.

```
FAIL wb_dma_suite.pss:31:13  wb_dma_suite::single_transfer_completes  seed=7 ch=3
  check(comp.dma.ch[ch].transfer_single(cfg) == wb_dma_status_e::WB_DMA_DONE)
        │                                    │
        WB_DMA_ERR (2)                       WB_DMA_DONE (1)
  last bus activity (3 of 11):
    #9  R32 0x0000_0088  ch[3].regs.csr = 0x0000_0041 {ch_en=1, err=1}   check_completion.pss:61
    ...
  repro: pssc test wb_dma_suite::single_transfer_completes --seed 7
```

Register values are **decoded into fields** wherever an address resolves to
a register. `message`, `error` and `fatal` keep their LRM meaning;
`error`/`fatal` become located failures. The checks are `import target
function`s in `pssunit`, implemented by the runner on every engine. That is
what lets a test run on generated code too (§8.6).

### 6.7 Discovery and identity

- A **test** is a concrete action derived (directly or not) from
  `pssunit::test_a`, or a function marked `@pssunit::test`, inside a component
  derived from `pssunit::suite_c`. It's found after parse and link from types
  and annotations, with no elaboration.
- **Test ID**: `<suite>::<test>[<param-id>]`, for example
  `wb_dma_suite::any_cfg_programs_sz[seed=7]`. The file is metadata, not part
  of the ID, so moving a file doesn't rename tests.
- **Selection**: ID glob, `file:line` (any line inside the test's action or
  function), tag (`@pssunit::tag {.name = "slow"}`), `--lf`, and `--changed`
  (tests whose call graph touches changed code, §9.5).
- The catalog is stored **in the model image**, so a simulation with only the
  image can list and run tests.

### 6.8 Sanitizers

After tear-down, each test is checked for what it left behind. Each check can
be switched off per test with `@pssunit::allow {.what = "..."}`.

| Sanitizer | Fails when |
|---|---|
| `inflight` | A channel used as a latch still holds a token (an operation started and never completed) |
| `unconsumed-irq` | A notification was raised and never consumed |
| `poll-budget` | A loop crossed the seam more than N times without progress. **A spin on a fake that never completes is a located failure, not a hang.** |
| `unmapped` | An access hit an address no register or declared region covers |
| `access-rule` | A write to a READONLY register or field, or a read of a write-only one |
| `rc-read` | A read-to-clear register's value was read and discarded |
| `unbound` | A group was reached without a `set_handle` |

Sanitizers are hooks on the event stream (§8.5), so they also run on RTL.

### 6.9 Examples in documentation

A ```` ```pss-example ```` block in an operation's doc comment is compiled
and run as a test on the fake, as Rust and ExUnit doctests are. (Phase 4.)

---

## 7. Platform doubles (R7, R8)

The environment is defined by the model's seam: core-library memory
primitives, user `import target function`s, and `notify_irq`. A double
implements that seam. A test names the minimum tier it needs
(`@pssunit::platform {.min = "hooks"}`), and the runner can run it on any
tier at or above that.

### 7.1 Tier 0: generated register fake (default, zero code)

- **Storage per register** with reset values, built from
  `reg_layout.collect_accessors`.
- **Field semantics**: READWRITE / READONLY / WRITEONLY come from PSS.
  RC, W1C, W1S and volatile status come from
  `@pssunit::field_access {.field = "err", .kind = "rc"}` annotations or
  from the register's SystemRDL / IP-XACT source (`--reg-semantics
  src/rdl/wb_dma_ch_regs.rdl`).
- **Sparse memory** for non-register regions, with `pssunit::mem_poke/peek`.
- **A bus log**, decoded to `group.reg{fields}` plus the PSS location.
- **A back door**: `check_reg`, `poke_reg`, `poke_field`, which cause no side
  effects and leave no log entry.
- **An unimplemented import fails the test with a location**. It never
  silently returns 0.

### 7.2 Tier 1: reaction hooks in PSS

```pss
component wb_dma_mock_c : pssunit::mock_c {
    @pssunit::on_write {.reg = "wb_dma_ch_regs_c.csr", .field = "ch_en"}
    target function void armed(pssunit::access_s a) {
        pssunit::schedule_after(2, "done", a.index);
    }
    @pssunit::on_event {.name = "done"}
    target function void finish(int ch) {
        pssunit::poke_field_at(ch, "wb_dma_ch_regs_c.csr", "done", 1);
        pssunit::raise_irq();
    }
}
```

Register paths are resolved at compile time, so a typo is a located error.
Component attributes are immutable outside init, so mock state lives in the
double (register contents plus `pssunit::state_get/set`). Helpers cover the
common shapes: `complete_after`, `status_sequence`, `fail_on_access`.
PSS hooks are the **portable** mock form. They run on both engines and in
every simulator, with or without Python.

### 7.3 Tier 2: Python-hosted runs (D7)

pssvm can run **inside a Python process**, either under pytest, under the
`pssc` runner, or (through pyhdl-if) inside a simulator. Python can then
implement any part of the platform, and can also **replace any PSS function**:

```python
# tests/wb_dma_mocks.py
import pssc.testing.vm as pv

@pv.implements("plat_delay_us")                 # a user import
def delay(ctx, us):
    ctx.time.advance_us(us)

@pv.mock("timer_c::wait_us")                    # ANY PSS function, by qualified name
def fast_wait(ctx, self_, us):
    return None                                 # skip the timer entirely

@pv.spy("wb_dma_ch_c::check_completion")        # call through, record calls and results
def _(call): pass

class WbDmaModel(pv.RegisterFake):              # a device model with real behaviour
    @pv.on_write("wb_dma_ch_regs_c.csr", field="ch_en")
    def armed(self, ctx, acc):
        self.copy(acc.index)                    # actually move the words in fake memory
        self.poke_field(acc.index, "csr", "done", 1)
        ctx.raise_irq()
```

- **Binding from PSS, no pytest needed**:
  `@pssunit::python {.module = "wb_dma_mocks", .platform = "WbDmaModel"}` on a
  suite or a test tells `pssc test` to import the module. A firmware engineer
  runs `pssc test` and never writes a conftest.
- **Binding from pytest**: `pytest-pssunit` collects PSS tests as pytest items,
  and a `pss` fixture gives Python tests the same engine. Python tests can
  call operations directly (`pss.dma.ch[0].transfer_single(cfg)`) and use
  fixtures, parametrization and plugins.
- **Mocking a PSS function** works because debug lowering keeps real call
  frames (§8.3): the engine dispatches a `CALL` whose target has a Python
  binding to Python. This is how to unit-test a subsystem operation
  (`dma_subsys_c::copy`) with its children's operations stubbed. CMock needs
  a generated header and a link-time trick for the same thing.
- **Mocks are scoped** to the test that binds them and are recorded in the
  event stream (`call` events marked `mocked`), so a trace shows what was
  real.
- **The seam is op-model-py's**: a Python platform has the same shape as
  op-model-py's generated `<Root>ImportApi` Protocol, so a model written to
  test generated op-model-py code is a pssvm platform, and the reverse holds
  too.
- **Engines**: pyvm calls Python directly. The native engine calls Python
  through its C host ABI (cffi bindings), so Python mocks also work with the
  native engine when Python is present. In a simulator without Python (§10.4),
  Python mocks are unavailable and the runner refuses such a test up front with
  a located message. Use Tier 1 hooks for mocks that must go everywhere.

### 7.4 Tier 3: RTL in simulation

The platform is the SV environment's implementation of the import interface,
typically the paper's UVM adapter. See §10.

### 7.5 Tier 4 (later): emulation and silicon

The same seam served over a socket (in the spirit of QEMU qtest), to an
emulator transactor or a debug probe. Nothing is built for it in Phases 1–4.

### 7.6 Expectation mocks: opt-in

`pssunit::expect_seq { write(reg, "f", v); read(reg); … }` checks the bus log
in order, within a window. It is never the default (CMock's strict ordering
is its most common complaint).

### 7.7 Record and replay

Any run can record the platform side of the event stream: read data, import
returns and notification times. `--platform replay:<trace>` plays them back.
It turns an expensive sim run into a fast regression (and reports the first
divergent access), and it lets any run be debugged in pyvm (§12.4).

---

## 8. The execution model: pssvm (D5)

### 8.1 The model image

`pssc build --image out/wb_dma.zbc` lowers the model, tests and PSS mocks to
one file:

- bytecode for every exported operation, test, hook, action function and
  scenario entry, keyed by **stable symbol** (component type + function, or
  action type + exec kind), not by position
- the line table (`ZBC_SEC_LINE`) and provenance (`FILE`/`PROV`), which the
  `.zbc` format already defines
- the **API hash** (every export and import signature) and a **generation
  id**
- the test catalog, the register map and field semantics
- the PSS sources (optional, on by default), so a debugger can show lines on
  a machine that has only the image

### 8.2 One semantics, two engines

| | pyvm (Python) | native engine (C) |
|---|---|---|
| Today | `zuspec-be-bc/interp` and the bc reference | `zuspec-rt-eng`: a walking skeleton with no memory hook, no callbacks, not wired to pssc |
| Role | Reference semantics; debugging; Python-hosted runs; CLI and CI on fakes | **Simulator integration** (DPI with no Python), speed, emulation, no-Python sites |
| Host ABI | Python objects | A C ABI that DPI calls directly: memory and import callbacks, yieldable completion, notify |
| Debugging | In-process hooks | **Engine debug stub** (§12.2) driven by the front end in the daemon |
| Coverage | covsight Python API | Event stream; or `ucis_writer.h` in-process |

The native engine is held to pyvm the way generated code is held to the
construct matrix: the matrix gains pyvm and native as targets, and every test
can run on both with bus traces compared (§8.6).

### 8.3 What bc needs to become pssvm

| Gap | Change |
|---|---|
| The entry is an action; target functions are inlined | **Operation and test entries**: each exported target function, test action and test function is a callable unit. `vm.call(root, "ch[0].transfer_single", args)`, `vm.run_test(id, seed)`. |
| Inlining loses frames | **Debug lowering** (the default for test and debug): no inlining, real `CALL` frames with named locals, one statement marker per PSS statement. Release lowering keeps today's bytecode. |
| No per-instruction hook | **Event hooks** gated by a mask per code unit, after PEP 669's `sys.monitoring`: free until enabled, and switchable per site |
| `VMError` / `error()` have no location | Every run-time error carries `src_ref`, resolved to `file:line:col` |
| Memory seam is address only | Each access also carries its **register path** (known at lowering) |
| Imports are synchronous | **Yieldable imports and accesses**: the engine returns "pending", and the host completes them later (the `zsp_bridge` mailbox pattern). Needed for SV tasks that consume time. |
| Code is fixed for the life of a VM | **Generations**: an image can be loaded into a running VM next to the old one, and calls bind by stable symbol (§10.6) |
| Function calls can't leave the engine | **Host-bound functions**: any function can be bound to a host implementation (Python mocks, §7.3) |
| Action functions don't exist | Lower them (§6.3) |

### 8.4 Native engine work

Built in `zuspec-rt-eng` against the same opcode set and the `.zbc` image:

- **Host ABI**, all plain C callable from DPI: `pssvm_load`,
  `pssvm_construct`, `pssvm_call_start`, `pssvm_run` (until the next yield:
  ACCESS / IMPORT / WAIT / DONE / STOP), `pssvm_complete`, `pssvm_notify`,
  `pssvm_reload`
- event hooks with masks; a binary event sink; recording for replay
- the engine debug stub (§12.2)
- generations for hot reload
- dv-solve linked only when the image needs solving (property tests,
  compound tests). Today the engine can't load without it (`future-projects.md`)
- conformance: the matrix, plus differential traces against pyvm on the full
  pssunit suite of each example model

### 8.5 The event stream (R4)

One schema, extending bc's `trace/schema.py`:

| Event | Fields |
|---|---|
| `test.start/end` | id, seed, platform, image hash, generation, verdict, duration |
| `stmt` | src_ref, frame (only when enabled) |
| `branch` | src_ref, arm, per-condition values (MC/DC) |
| `call/return` | function, args/result, frame, `mocked` flag |
| `access` | R/W, width, addr, data, register path, decoded fields, src_ref, platform time |
| `import` | name, args, result, src_ref, time |
| `notify`, `chan` | source / channel path, op, result, time |
| `check`, `sanitizer`, `message` | pass/fail or kind, operands or text, src_ref |
| `reload` | old/new generation, symbols changed, migration result |

Every event carries platform time when there is one. Sinks: in memory, NDJSON,
compact binary (`.pstrace`) and a live socket. Line-level events are off by
default and are enabled by the consumers that need them (coverage, debugger).

### 8.6 The fidelity guard (R9, R10)

- **The gate runs first.** `pssc test` runs the op-model gate for the
  project's declared targets. **If it passes `pssc test`, it generates.**
- **Same integer semantics.** Both engines are held to `matrix/ref8_7.py`,
  and the construct matrix gains pyvm and native as targets.
- **Differential runs.** `pssc test --engine gen-py` (and `gen-sv` under
  Verilator) compiles the model and its **atomic** tests with op-model-py/sv
  (`--export-action` already renders action inheritance and `super;`), runs
  them through the same runner and doubles, and compares bus traces with
  pyvm. Compound test actions (activities) have no op-model rendering and run
  on bc only. That's acceptable, because what the guard protects is the
  operations, not the test's own control flow.
- pssvm lowers from the post-gate `OpModel`, so register-access shapes
  (`reg_rmw` reductions) match the generators by construction, and the
  differential run checks that.

---

## 9. Runner

### 9.1 Commands

The CLI (§14) is the whole surface. For running tests:

```
pssc test [SEL...] [--platform fake|hooks|py:<mod>|sim:<session>|replay:<trace>]
          [--engine pyvm|native|gen-py|gen-sv] [--seed N|--seeds N] [-k GLOB] [-m TAG]
          [--lf] [--changed] [--watch] [--json] [--report ctrf.json] [--junit junit.xml]
          [--cov] [--trace off|fail|all] [-j N]
```

### 9.2 Output and exit codes

Output is quiet by default: one failure card per failure (§6.6) and a
summary. `--json` streams NDJSON events (schema `pssunit.events/1`).
`--report` writes CTRF and `--junit` writes JUnit XML. Failures also go out as
SARIF and LSP diagnostics. Exit codes: `0` pass, `1` test failure, `2`
compile or gate error (located), `3` platform or infrastructure error, `5`
nothing selected.

### 9.3 pytest

`pytest-pssunit` collects PSS tests as pytest items and provides the `pss`
fixture (§7.3).

### 9.4 DV Flow Manager

`pssc.dvflow` gains `Test` and `Image` tasks. A sim flow depends on `Image`,
never on generated SV, so a PSS edit doesn't invalidate the sim build.

### 9.5 Caching, affected tests and watch mode

Results are cached by (hash of the test's call-graph slice, platform, seed,
engine version). The slice comes from `OpModel.state_uses`/`callees`.
`--changed` and `--watch` re-run only the tests whose slice changed. On fakes
that takes milliseconds. With `--platform sim:<session>` the new image is
**hot-reloaded into the running simulation** (§10.6) and the affected tests
run there.

---

## 10. Simulation integration (R6, R12)

### 10.1 Goals

1. Run the same tests, and the operation layer itself, against RTL.
2. Never recompile the simulation for a PSS body change.
3. **Never restart a running simulation for a PSS body change** (hot
   reload).
4. Keep the UVM code that calls the operation layer identical, whether the
   layer is generated SV or interpreted.
5. Debug live in the sim, and after the fact from a trace.

### 10.2 Two SV pieces, compiled once

**(a) `pssvm_pkg`**: generic and independent of any model. It holds the DPI
bindings to the engine host ABI (§8.4); `pssvm_platform_if` with
`read8..64`/`write8..64` tasks, a generic `call_import`, and `reset()`;
`pssvm_runner`, which runs `+pssunit=SEL` or every test and writes reports
and traces; and the **control channel** (§10.6).

**(b) A typed facade**, generated by a new target `op-model-sv-vm` with
**exactly op-model-sv's API** (`<root>_ctxt_if`, `<root>_imp_if`, and
`<root>_root_vm #(Timp)::create(imp, args)` next to `<root>_root
#(Timp)::create`). It depends only on the API, and embeds the API hash.

| Edit | Rebuild sim? | Restart sim? |
|---|---|---|
| Operation body, test, PSS mock, constant | No | **No: hot reload** |
| Add a test or mock | No | No |
| Change an exported signature or an import | Yes, the facade | Yes |

`+pss_impl=vm|gen` picks the implementation. Development and regression run
on `vm`; a signoff run on `gen` closes the fidelity loop on real RTL.

### 10.3 Time, blocking and concurrency

The engine never owns a thread or a clock. The SV side drives it:

```
task call(op, args):
  h = pssvm_call_start(op, args)
  forever case (pssvm_run(h))
    ACCESS:  plat.read32/write32(...) → pssvm_complete(h, data)   // consumes sim time
    IMPORT:  plat.call_import(...)    → pssvm_complete(h, ret)
    WAIT:    @(pssvm notify)                                       // channel get() on wake
    STOP:    pssvm_dbg_wait()                                      // debugger holds sim time
    DONE:    return pssvm_result(h)
```

A time-consuming access or import is an ordinary SV task, so no DPI callback
is needed. Parallel operations are SV threads, each driving its own engine
thread on one shared model. An engine error is a located failure and can't
take the simulator down.

### 10.4 Which engine runs in the simulator (D5, D6)

- **First: pyvm through pyhdl-if** (D6, to try). It's the fastest way to the
  whole workflow in a sim: Python mocks, the debugger and the sanitizers are
  all the same code. Operation sequences are bound by bus transactions, so
  interpreter overhead per statement is small next to sim time per access.
- **Then: the native engine** (D5) as the production integration: DPI
  directly to C, no Python in the simulator, fewer site dependencies, faster.
  `pssvm_pkg`'s DPI contract is the native host ABI from the start, and under
  pyhdl-if it's backed by a shim. **Switching engines changes no SV.**

### 10.5 Server mode

`+pssvm_serve` makes the testbench, after boot, register a **session** with
the workspace daemon (by connecting out, or by listening on a port). The
session accepts images, test selections, reloads and debug requests until it
ends. Start-up, elaboration and SoC boot happen once.

### 10.6 Hot reload (D3)

In a slow SoC sim, getting to the point where the DMA can be exercised (boot
ROM, clocks, PCIe link-up) can take tens of minutes. A bug found after that
shouldn't cost another boot. Hot reload swaps a new image into the running
engine.

**Why it is safe for operation models in particular.** An operation model
has almost no software state. Component attributes are fixed at
initialization and set by `initialize()` from constructor arguments, which
just bind register handles. **Device state lives in the device**, in RTL,
not in the model. The software state that matters is channel tokens (`wake`,
`inflight`) and whatever engine threads are running. So a reload amounts to
"rebuild the tree with the same arguments, carry over the channels, re-bind
calls", which is cheap and well defined.

**Mechanics**:

1. **Trigger**: `pssc sim reload <session>` (or `--watch` with
   `--platform sim:<session>`, or the debugger's *Reload*). The daemon builds
   the new image and checks it against the session's: the API hash must
   match (otherwise the facade can't call it, so it's refused with the list of
   changed signatures), and the root's constructor arguments must still apply.
2. **Load**: the engine loads the image as generation N+1 next to N.
3. **Quiesce at a reload point**. The default is an **operation boundary**:
   no engine thread is inside generation-N code. Threads already in an
   operation finish on generation N, unless `--cancel` is given. New calls
   wait. A **test boundary** is also a reload point. A reload never replaces
   code in an active frame (no on-stack replacement).
4. **Migrate**: rebuild the component tree under N+1 with the recorded root
   constructor arguments, in a **bus-isolated mode**. Construction and init
   should never touch the bus; if they do, the reload is refused, because that
   would re-program the device behind the test's back. Channel contents are
   carried across by path. A **structural change** (a component, channel or
   sub-component added or removed) is reported as a diff. Components the
   change doesn't touch migrate, the rest start fresh, and `--strict` refuses
   any structural change.
5. **Commit**: new calls bind to generation N+1 by stable symbol. The
   session logs a `reload` event (with symbols changed and the migration
   result) to the trace, the console and coverage.

**What reload means for coverage and results.** Every result and history
node records its generation. Coverage under different generations merges by
stable coveritem ID (O13), and a report can be limited to the latest
generation.

**Combine with simulator checkpoints.** If the DUT itself was left in a bad
state, hot reload can't help. The session can be started from a simulator
save point (`$save` / the vendor's restore), so "restore the post-boot
checkpoint and run the fixed image" is one command:
`pssc sim restart <session> --from post_boot`.

**Python mocks reload too.** Under pyvm the mock modules are re-imported at
the same reload point.

### 10.7 Waveforms

Every event carries sim time, and the trace sits next to the wave database.
`pssc trace show --at 1.234us` maps a time to the PSS line, and a line to every
time it ran. With `+pssvm_dbgsig`, the facade drives `pssvm_dbg.cur_op`,
`cur_line` and `cur_file` signals, which any viewer shows next to the bus.

### 10.8 Debugging in the simulation

The engine debug stub (§12.2) talks to the daemon over the session's
connection. When the debugger stops, the SV thread waits in
`pssvm_dbg_wait()`, which **freezes sim time**. `+pssvm_dbg_on_fail` stops
the session when a check fails, instead of ending the test.

---

## 11. Coverage (R1, D1)

### 11.1 Three layers

1. **Code coverage of PSS**: statements, branches and, as an option,
   conditions/MC/DC.
2. **Register and field coverage**: whether each register was read and
   written, which values each field took (bins: 0, max, each enum item,
   toggles), and which RC/W1C side effects happened.
3. **Operation-envelope coverage**: which exported operations were called,
   with which argument bins, and with which results. The paper's "verified
   envelope", shipped with the model.

### 11.2 The boundary

The components under test (`@pssunit::under_test` or `--cov-scope`), with
inheritance and sub-components. Excluded by default: `pssunit`, mocks, hooks,
suites and test actions, the core library, executors and Python. Lines can be
excluded with `@pssunit::nocover`. The scope comes from the component tree,
not file globs.

### 11.3 The coverage database: covsight

All three layers live in **one covsight-core database**
(`packages/covsight-core`: a UCIS 1.0 data model stored as partitioned
Parquet, with C, Python and TypeScript APIs and UCIS-XML import/export).
PSS **functional coverage** (covergroups, later) belongs in the same database
as the code coverage of the operations that realize it, and next to the
simulator's own coverage of the RTL. covsight's testplan layer
(`packages/covsight`) closes a plan over all of it, including pssunit tests.

| pssunit coverage | UCIS representation |
|---|---|
| Component type / instance | Design-unit scope per component type, `UCIS_INSTANCE` per instance (source `UCIS_OTHER`, user attribute `language = "PSS"`) |
| Statements | `UCIS_BLOCK` scopes, a `UCIS_STMTBIN` per PSS statement, source info from the line table |
| Branches | `UCIS_BRANCH` per `if`/if-chain/`match`, a `UCIS_BRANCHBIN` per arm, `UCIS_IS_BR_ELSE` on the implicit else |
| Conditions / MC/DC | `UCIS_COND` / `UCIS_EXPR` scopes with truth-table bins |
| Registers and fields | A generated covergroup per register type, a cover instance per register instance, coverpoints for access, field values and side effects |
| Operation envelope | A covergroup per exported operation: parameter coverpoints (bins from enum items and `rand` ranges), a result coverpoint, crosses on request |
| Test run | `UCIS_HISTORYNODE_TEST` (test ID, seed, status, engine, platform, image generation). Merges are `UCIS_HISTORYNODE_MERGE`. |
| "Which tests cover this?" | Test↔coveritem association |

Register and envelope coverage are **ordinary covergroups**, so they and
user-written functional coverage are one kind of object. pyvm writes through
covsight-core's Python API; the native engine through the event stream or
`ucis_writer.h`.

### 11.4 Merging, diffs, views

- **Merge** across engines, platforms and image generations by stable
  coveritem ID, and with the simulator's own UCIS export (code, toggle, SV
  covergroups).
- **`pssc cov diff --base origin/main`**: coverage of the changed lines only.
- **Views** over the database: LCOV (editor gutters, Codecov), Cobertura,
  JSON for the CLI, UCIS-XML. They belong in covsight where they help every
  producer (O11).

### 11.5 Generated-code parity

`--line-directives` on the generators (`#line` in C/C++, `` `line`` in SV)
makes gcov, gdb and simulator source debuggers report PSS locations for
generated code.

---

## 12. Debugging (R4, R5)

### 12.1 One front end, many back ends

The **debug front end** runs in the daemon (§14.1). It owns breakpoint
resolution (line → pc through the image's line table), register breakpoints,
expression evaluation, the variable model and trace queries. Two kinds of
client use it: **DAP** (VS Code and other editors) and **the CLI** (`pssc
dbg …`, §14.3). Both can be attached to the **same session at once**, so an
agent can set breakpoints and step while a human watches in the editor.

### 12.2 The engine debug stub

Engines implement a deliberately small protocol, in the spirit of
gdbserver's RSP, so the native engine doesn't have to implement DAP:

`stop/continue`, `step {stmt|into|out}` (statement markers),
`set_pc_breaks [...]`, `set_access_watch [reg path, R/W, value]`,
`threads`, `frames(thread)`, `read_frame(frame)` (locals, args, `this`),
`read_comp(path)`, `backdoor_read(reg)`, `exec_snippet(bytecode)` (for
evaluating expressions the front end compiled), `reload`, plus stop events
(breakpoint, watch, check failure, sanitizer, error, unimplemented import).

pyvm implements it in-process, and the native engine over the session
connection. Everything else, including DAP, lives once in the front end.

### 12.3 Features

| Feature | Notes |
|---|---|
| Line, conditional and hit-count breakpoints; logpoints | Standard |
| **Register breakpoints** | "Break when `ch[0].regs.csr.ch_en` is written / written with 0 / read". Set by path or from the Registers view. |
| Break on check failure, sanitizer, `error`, unimplemented import, mocked call | Exception breakpoints |
| Stack | Test action → `transfer_single` → `wait_completion` → `check_completion`, with a `[platform]` frame for a pending access |
| Variables | Locals, action attributes, component fields; **Registers** (the mirror, decoded, through the back door); **Channels**; **Bus** (the last N accesses) |
| Evaluate | PSS expressions in the frame. Register reads go through the back door, never the bus. |
| Threads | One per engine thread (parallel test branches, forked operations) |
| Reload | "Apply change" during a debug session: hot reload at the next operation boundary (§10.6) |

### 12.4 Time-travel over a trace

A trace with `stmt` events (`--trace all`, or `--trace fail`, which re-runs a
failing test with full tracing) can be opened with `pssc dbg start --trace
run.pstrace`: the same session commands plus `back`, `reverse-continue` and
"run to the last write of a field" (DAP `supportsStepBack`). Queries:

- `pssc trace why run.pstrace 'ch[0].regs.csr.err'`: the access, the
  statement, and the dataflow back to the operation's inputs
- `pssc trace slice --reg ch[0].regs.sz`
- `pssc trace diff a.pstrace b.pstrace`: the first divergence

### 12.5 Debugging what ran somewhere else

Recorded platform responses (§7.7) plus determinism mean any run (a
four-hour nightly sim, a native-engine run, an emulator) can be **replayed in
pyvm under the debugger**, with no re-run of the sim.

---

## 13. Formal analysis with constraints (R13, D8)

Testing shows a path works. A solver can also say which paths **cannot**
happen, find inputs for paths no test has reached yet, and prove that a check
holds for every input. Operation models are an unusually good fit for this,
and the solver is one we already own.

### 13.1 Why operation models fit

- **The arithmetic is exact bit-vector arithmetic.** PSS integers have fixed
  widths, and `IntSemantics` already decides every widening, wrap and sign
  extension. Translating to QF_BV (the SMT logic of fixed-width bit-vectors)
  is exact, not an approximation.
- **The inputs are few and typed.** For an operation they are:
  - its parameters, under the `rand` constraints of their struct types;
  - the value of every register read, bounded by field widths and enum items;
  - the result of every import.
- **The control flow is small.** Calls inline, `foreach` over a fixed-size
  array unrolls exactly, and almost the only unbounded loops are polls.
- **The solver exists.** dv-solve is a sound bit-vector solver: `sat` and
  `unsat` are definitive and it says `unknown` rather than guessing. It has C
  and Python APIs, accepts SMT-LIB2, and is already pssc's randomization
  solver.

### 13.2 The engine: symbolic execution of the lowered model

A third pssvm engine, `pssvm-sym`, executes the same lowered code (debug
lowering, §8.3, or the post-gate `OpModel` directly) with symbolic values:

- Parameters are solver variables, constrained by their struct constraints.
- **A register read returns a fresh variable**: the device may answer
  anything the field widths allow, unless device facts (§13.4) say otherwise.
  The register path from the lowering names it (`csr@3` = the third read of
  `ch[ch].regs.csr`).
- An import returns a fresh variable of its return type. A `notify` can arrive
  at any seam crossing, which is modelled as a boolean input per crossing.
- Each branch adds its condition to the path condition. A query is the path
  condition plus a goal.
- Results map back through the line table and coverage IDs, so every finding
  is located and lines up with covsight.

### 13.3 What it answers

**1. Unreachable code.** For each branch arm and statement, ask whether any
input reaches it. An `unsat` answer is reported as one of three findings,
because they mean different things:

| Finding | Assumption | Means |
|---|---|---|
| `dead` | Register reads unconstrained | **Sound for every possible device.** A real bug or real dead code. |
| `dead-under-params` | Parameter constraints applied | Unreachable for any legal call. May be deliberate (defensive code), or may be a constraint that's too tight. |
| `dead-under-device` | Device facts (§13.4) applied | Only as true as those facts |

Proven-unreachable coverage bins are written to covsight as **UCIS
exclusions that carry their proof** (the finding kind, the assumptions and
the solver result). Coverage then closes honestly instead of by hand-waived
exclusion. Envelope coverage (§11.1) benefits most: an infeasible cross of an
operation's parameters is excluded automatically.

**2. Corner cases as tests.** For each coverage gap, ask for a **witness**: the
parameter values plus the sequence of values each register read must return.
The witness is emitted as an ordinary pssunit test, a derived test action
whose `with` constraints pin the parameters, with a `status_sequence` (or a
replay trace) supplying the reads:

```pss
// generated by: pssc formal witness wb_dma_ch_c/functions/check_completion.pss:74
action check_completion_err_and_done_74 : ch_fixture_a {
    constraint ch == 2; cfg.tot_sz == 1;
    exec body {
        super;
        pssunit::status_sequence(comp.dma.ch[ch].regs.csr, {0x0000_0001, 0x0000_0061});
        comp.dma.ch[ch].transfer_single(cfg);
    }
}
```

**The witness is then run, not trusted.** With unconstrained reads a witness
can need device behaviour that's impossible. Running it on the fake, the PSS
hooks, a Python device model or RTL is the check (concolic testing in the
DART/KLEE tradition). A witness the device model contradicts fails visibly,
and that failure is the cue to state a device fact.

**3. Proving checks.** A `check`, a sanitizer or a contract becomes a proof
obligation over all inputs (bounded where §13.5 says so):

- no path writes a READONLY field or a reserved member;
- every computed address stays inside its register group or declared region
  (overflow in `bit[32]` address arithmetic is a classic C bug);
- `inflight` is released on every exit path, early returns and error paths
  included;
- a read-to-clear register's value is never read and discarded;
- user contracts: `@pssunit::requires {.expr = "..."}` and
  `@pssunit::ensures {.expr = "..."}` on an operation, the expressions checked
  at compile time like every other pssunit string (O3).

A failed proof comes with a **counterexample, emitted as a test** (as in item
2), so it can be run, debugged and kept as a regression.

**4. Equivalence of two versions.** "Old and new `transfer_single` produce
the same bus trace (the same accesses, in order, with the same data) for every
input, up to k poll iterations" is a bounded equivalence check between two
bodies. Uses:

- refactors: proving a change preserves behaviour, which a golden trace only
  samples;
- retrofit: a PSS translation of an operation against an earlier PSS version
  of it;
- hot reload: `pssc sim reload --equiv` reports which operations the new
  image changes behaviourally, not just textually.

### 13.4 Device facts

Unconstrained reads keep `dead` proofs sound but let witnesses ask for the
impossible. A model can state what the device guarantees, as constraints over
register fields:

```pss
@pssunit::device_fact {.reg = "wb_dma_ch_regs_c.csr", .expr = "!(done && err)"}
```

Facts can also come from the register's SystemRDL / IP-XACT source (for
example, reserved fields read as zero). Every finding records which facts it
relied on. A fact can be checked in turn: as an assertion over the bus log in
every test run (a sanitizer, §6.8) and, against RTL, by the simulation. A
false fact is then caught by evidence rather than trusted.

### 13.5 Limits

| Issue | Effect | Mitigation |
|---|---|---|
| Polling loops whose exit depends on device reads | No fixed iteration bound | Unroll k iterations (a **bounded** result, labelled as such), or summarize a poll as "eventually reads a value satisfying the exit condition". k-induction only if a real need appears. |
| Unconstrained device | `dead` is sound; witnesses may be impossible | Validate every witness by running it (§13.3); state device facts |
| Concurrency (parallel operations on channels) | One thread is modelled | `notify` arriving at any seam crossing is in scope. Interleavings of parallel threads are out of scope. |
| Very large generated `if` chains | One query per arm | Incremental solving; each query is small |
| Strings, `message` formats | Not relevant to reachability | Ignored |
| `unknown` from the solver | No answer | Reported as `unknown` with a timeout, never as either result |

### 13.6 Interface

```
pssc formal dead    [SCOPE] [--params] [--facts] [--json]      # unreachable code
pssc formal witness <file:line|coverage-gap-id> [--out DIR]    # corner case → test file
pssc formal gaps    [--scope ..] [--diff BASE] [--out DIR]     # every covsight gap: witness or exclusion
pssc formal prove   [SCOPE] [--k N] [--json]                   # checks, sanitizers, contracts
pssc formal equiv   <old-image|rev> <new-image|rev> [OP...] [--k N]
```

All of it runs in the daemon (§14.1), shares its parsed model, and returns
located, JSON-able findings. `pssc formal gaps` is the natural partner of
`pssc cov gaps`: each gap comes back either as a new test, as a proven
exclusion, or as still open. An agent only has to deal with the open ones.

---

## 14. The CLI is the AI interface (R11, D4)

MCP adds a second protocol, server configuration, and a tool schema to keep
in sync, and it hides what the agent did from the human. An agent with a
shell can already run commands. What it needs is commands that are **fast,
structured, stateful where that helps, and the same ones a human uses**.

### 14.1 `pssd`: a per-workspace daemon

- Started automatically by the first `pssc` command in a workspace (keyed on
  the project root), and stopped after a period with no use. `pssc daemon
  status|stop` manages it.
- **Keeps hot**: the parsed and linked model (re-parsing only changed files;
  pssparser parses whole sources today, so caching is per file), the gated
  `OpModel`, the lowered image, a loaded pyvm, the test catalog, the covsight
  database handle, open debug sessions and simulation sessions.
- **Serves** the CLI over a Unix socket, and DAP for editors.
- **Thin client**: the `pssc` client does no heavy imports. A query is a
  socket round trip. Targets: **< 50 ms** for list, query, model and coverage
  commands; **< 300 ms** from an edit to the result of an affected unit test
  on a fake.
- `PSSC_NO_DAEMON=1` runs any command in-process, for CI and for isolating a
  problem (like `PSSC_NO_PLUGINS`).

### 14.2 Command design rules

- **Noun-verb, discoverable**: `pssc help --json` returns every command, its
  arguments and its output schema, so an agent learns the tool in one call.
- **`--json` on everything**, with versioned schemas. Default text output is
  short (failure cards, not logs), because agents pay per token.
- **Stable IDs** in every output (test IDs, session IDs, breakpoint IDs,
  `file:line:col`), so the output of one command is the input of the next.
- **Large outputs go to files** and are referenced by path (`--out`), with a
  summary on stdout.
- **Every failure prints a repro command.**
- **Exit codes** are meaningful and documented (§9.2).

### 14.3 The command set

```
pssc test     ...                                   # §9
pssc model    api|regs|explain <addr|path>|tests    # the elaborated model, from the manifest
pssc dbg      start <test> [--platform ..] [--session S] [--trace F] -> {session}
pssc dbg      break add <file:line|fn> [--if EXPR] | watch <reg.field> [--write|--read] [--value V]
pssc dbg      run|continue [--timeout T]            # blocks until a stop; prints the stop record
pssc dbg      step|next|out|back|rc                 # rc = reverse-continue (traces)
pssc dbg      bt|locals|regs [path]|bus [-n N]|eval <expr>|threads
pssc dbg      reload|end
pssc trace    show|why|slice|diff ...
pssc cov      gaps [--scope ..] [--diff BASE]|report|lcov|merge
pssc sim      list|attach|reload|restart [--from CKPT]|run <sel>|log
pssc formal   dead|witness|gaps|prove|equiv ...                # §13.6
pssc daemon   status|stop
```

A debug session is a **stateful object in the daemon**: `pssc dbg start`
returns an ID, and later commands default to the most recent session. A
command that runs (`continue`, `step`) blocks until the next stop and prints
one JSON stop record (reason, location, the source line, top frame, and the
register that tripped a watch). An agent's debugging is then a short shell
transcript, every step of which a human can read and repeat:

```
$ pssc dbg start wb_dma_suite::single_transfer_completes --seed 7 --json
{"session":"s3","stopped":"entry","at":"wb_dma_suite.pss:24:9"}
$ pssc dbg watch 'dma.ch[3].regs.csr.err' --write
$ pssc dbg continue --json
{"stopped":"watch","at":"wb_dma_ch_c/functions/configure_channel.pss:48:9",
 "access":{"op":"W32","reg":"ch[3].regs.csr","fields":{"err":1,"ch_en":0}}, ...}
$ pssc trace why --session s3 'ch[3].regs.csr.err' --json
```

### 14.4 Loops an agent can close with it

- **Test to coverage**: `pssc cov gaps --diff origin/main --json` (each gap
  with source context) → write a test → `pssc test --changed --cov` → keep it
  only if it passes and fills a gap.
- **Triage**: failure card → `pssc trace why` → fix → `pssc test --lf` →
  `pssc trace diff` against the last passing run.
- **Fix in a running sim**: `pssc sim run <sel>` fails → `pssc dbg` on the
  session → edit → `pssc sim reload` → `pssc sim run <sel>`. No reboot.
- **Retrofit from UVM**: record a bus trace from the existing UVM sequence,
  write the PSS operation, and use `--platform replay:uvm.pstrace` as the
  oracle.
- **Mutation testing** (`pssc test --mutate`): report the mutants no test
  killed.

### 14.5 Skill

A `pssunit` agent skill (alongside `pssparser`): the test API, the CLI command
reference (generated from `pssc help --json`, so it can't drift), and worked
WB DMA examples. Its examples are checked like `docs/custom-generator-styles.md`.

---

## 15. Editor integration

In `vscode-pss-support`, as a client of the daemon:

- **Test Explorer**: the catalog as the tree (suites → test actions →
  derived tests), with Run, Debug and Coverage profiles, and failures shown
  inline at the check.
- **Coverage gutters** from covsight (via LCOV or directly), with "tests
  covering this line".
- **CodeLens**: "run 3 tests · 87% · last: pass" on operations and test
  actions.
- **Debug**: a `pss` debug type over the daemon's DAP, with a Registers view
  and data breakpoints, *Attach to simulation session*, and *Apply change*
  (hot reload).
- **Shared sessions**: a session the agent started from the CLI shows in the
  editor's debug view.

---

## 16. Why this beats writing the C

| Question | Hand-written C | PSS with this environment |
|---|---|---|
| Edit → test result? | Rebuild and link (seconds), or firmware plus CPU sim (minutes) | Milliseconds on a fake; seconds in a warm sim |
| Fix a bug found 30 minutes into an SoC sim? | Rebuild the firmware, restart, re-boot | **Hot reload; carry on from where you were** |
| Scaffolding for the first test? | HAL indirection, a hand fake, CMock configuration, a build per file | None |
| Does the fake know the device? | Only if someone wrote it | Generated from the register model plus RDL semantics |
| Mock one function of a sub-module? | CMock-generated header plus link tricks | `@pv.mock("timer_c::wait_us")`, or a PSS hook |
| Reuse a test fixture across tests? | Copy-paste or macros | Action inheritance, `super;`, action functions |
| Same test against RTL? | Different harness, different build | Same test, `--platform sim:…` |
| Debugging against RTL? | Disassembly against waves | PSS source, register breakpoints, step back, frozen sim time |
| A failure in last night's sim? | Re-run with logging | Open the trace and step back |
| A hang? | Watchdog, no location | `poll-budget`: the loop and the register it polled |
| Coverage? | gcov on host | Lines, branches, MC/DC, registers/fields, envelope, in covsight with the RTL's coverage |
| Dead code and corner cases? | Static analyzers flag some; reaching a corner means hand-crafting a test | Proven unreachable (and excluded from coverage with the proof), or a generated test that reaches it |
| A crash in the sim? | A stray pointer kills it | A located failure |
| Agent-usable? | JUnit XML | A fast JSON CLI, stateful debug sessions, trace queries, coverage gaps |
| And afterwards? | One C library; SV and Python are separate work | Generated C, SV and Python layers, held to the tested behaviour |

---

## 17. Roadmap

**Phase 1: tests on a fake, from the CLI.**
pssunit prelude (test actions, test functions, checks, the fake API);
static catalog; bc operation and test entries, debug lowering, hooks, located
errors, host-bound functions; Tier 0 fake; sanitizers (`poll-budget`,
`unmapped`, `access-rule`, unimplemented import); **Python-hosted runs** (the
`pssc.testing.vm` API, `@pv.implements/mock/spy`, `@pssunit::python`);
`pssd` daemon and thin client; `pssc test`, `pssc model`; CTRF, JUnit,
`--json`; `pytest-pssunit`.
*Exit*: the WB DMA operation tests from the companion example are written as
test actions with an inherited fixture and run on the fake in under 1 s.
`pssc test` on an unchanged workspace answers in < 300 ms through the daemon.
pyvm is a target in the construct matrix.

**Phase 2: debugging and coverage.**
Debug front end with DAP and `pssc dbg` sessions (line and register
breakpoints, Registers/Bus scopes); engine debug stub in pyvm; coverage to
covsight (UCIS mapping, §11.3); `pssc cov gaps/diff`; LCOV view; `--trace` and
`pssc trace show`; Tier 1 hooks; VS Code Test Explorer and debug type; **action
functions** (pssparser, ast2ir, bc, export-action).
*Exit*: an agent, using only `pssc` commands, finds a seeded WB DMA bug (wrong
`chk_sz` field) through a register watch, and a human watches the same
session in VS Code.

**Phase 3: simulation, then hot reload.**
Yieldable imports; `pssvm_pkg` with the native-shaped DPI contract, backed
by pyvm through pyhdl-if (D6); the `op-model-sv-vm` facade with API hash;
`pssvm_runner`; server-mode sessions; generations and **hot reload** at
operation and test boundaries; debugging in the sim; `+pssvm_dbgsig`.
*Exit*: the fw-wb-dma UVM bench runs the same tests against RTL under
Verilator and one commercial simulator. A body edit is hot-reloaded into a
running session and the failing test passes without a restart. The `vm` and
`gen` implementations give identical bus traces.

**Phase 4: native engine.**
`zuspec-rt-eng` brought up to the host ABI (§8.4): callbacks, yields,
hooks, generations, debug stub, recording, coverage. It replaces pyvm behind
`pssvm_pkg` with no SV change. It's held to pyvm by differential traces on the
full suite.
*Exit*: the Phase 3 bench runs on the native engine with no Python in the
simulator, including hot reload and live debugging.

**Phase 5: replay, time travel, fidelity, reach.**
Record/replay; `back`/`rc`; `why`/`slice`/`diff`; `--engine gen-py/gen-sv`;
property shrinking and seed corpus; envelope coverage; simulator-checkpoint
restart; doc examples; `--line-directives`; mutation testing; the agent
skill; Tier 4 seam.

**Phase 6: formal analysis (§13).** In order of value for cost:
(1) `pssc formal dead`, with no device facts, as a lint-style pass, plus the
covsight exclusions; (2) `pssc formal witness`/`gaps`, emitting tests that are
then run; (3) device facts, with their sanitizer; (4) `prove` for the built-in
obligations and `requires`/`ensures`; (5) `equiv`, including `sim reload
--equiv`. It depends on Phase 2 (coverage IDs, debug lowering) and on dv-solve
being reachable from the daemon. It doesn't depend on the simulator phases, so
parts (1) and (2) can start right after Phase 2 if wanted.
*Exit*: on WB DMA, every statement and branch of `wb_dma_ch_c` is either
covered, proven unreachable (with the kind of proof recorded in covsight), or
reported `unknown` with a reason; at least one generated witness test
reaches a branch the hand-written tests missed.

---

## 18. Open questions and risks

| # | Question / risk | Recommendation |
|---|---|---|
| O1 | Action functions are outside the LRM | Report them as an extension (a PSS120-style warning). Keep `super;`-only fixtures as the portable form, and propose action functions to the PSS WG with the use case. |
| O2 | pyhdl-if in commercial simulators is site-dependent | The DPI contract is the native ABI from day one, so the native engine can replace it with no SV change (§10.4) |
| O3 | Annotation values are scalar or string, so register paths and table cases are strings | pssc checks them at compile time, with locations |
| O4 | RC / W1C / W1S semantics aren't in PSS | RDL / IP-XACT import plus annotations. Feed the use case back to the PSS WG. |
| O5 | Fresh tree per test vs. a DUT that's slow to reset | Platform `reset()`; `@pssunit::session` fixtures for tests sharing one booted DUT; hot reload between them |
| O6 | Hot reload: construction or init that touches the bus, and structural changes | Refuse a reload whose construction touches the bus. Report structural diffs, and refuse them under `--strict`. Never replace code in an active frame. |
| O7 | Pausing a sim in the debugger can trip watchdogs and license heartbeats | Document per simulator. Prefer `+pssvm_dbg_on_fail` and trace replay for long pauses. |
| O8 | Hierarchical exports (`ss.dma()`) aren't implemented | Tests call through the tree in PSS, so they don't depend on it |
| O9 | The daemon is one more process to go wrong | `PSSC_NO_DAEMON=1` runs everything in-process. The daemon holds no state that can't be rebuilt, and a version mismatch restarts it. |
| O10 | Who owns `pssunit` as a standard? | A pssc prelude first. Propose it to Accellera if it's adopted. |
| O11 | Where the coverage views live | In covsight where possible; pssc keeps the UCIS mapping |
| O12 | UCIS has no PSS source-language value | `UCIS_OTHER` plus a `language = "PSS"` attribute |
| O13 | Coveritem identity across rebuilds and generations | Names from PSS structure (type, function, statement path), lines in source info only |
| O14 | Python mocks are unavailable on a no-Python native sim | Refuse such a test up front with a location. PSS hooks are the portable form. |
| O15 | Symbolic execution over bc bytecode, or over the post-gate `OpModel` IR? | `OpModel`: smaller, typed, and has `IntSemantics` decisions attached. Located via the same `loc`s. Revisit if the two drift. |
| O16 | Bounded results (k poll iterations) could be read as full proofs | Every result states its bound and its assumptions (params, facts) in the CLI output and in the covsight exclusion record |
| O17 | Device facts could be wrong, making `dead-under-device` false | Each fact is also a run-time sanitizer, so tests and RTL runs check it; findings list the facts they used |

---

## 19. References

- **Tests in the language**: Rust `cargo test`, Zig `test` blocks and testing
  allocator, Elixir ExUnit (doctests, `--stale`, `file:line`), Deno sanitizers.
- **Fixtures and inheritance**: pytest fixtures and parametrization;
  JUnit 5 (abstract test classes, extensions); GoogleTest `TEST_F`/`TEST_P`.
- **Event streams, fuzz corpus**: `go test -json`, Go native fuzzing.
- **Embedded C baseline**: Unity / CMock / Ceedling, fff, CppUTest.
- **HDL**: SVUnit / SVMock, VUnit, cocotb, pyuvm, UVM RAL, OSVVM.
- **Device doubles**: Zephyr bus emulators, QEMU qtest, Renode, KUnit.
- **Mocks**: Fowler, *Mocks Aren't Stubs*; VCR.py; Hypothesis shrinking; Pact.
- **Coverage**: llvm-cov and MC/DC, coverage.py contexts, diff-cover, CoverUp,
  Meta TestGen-LLM (arXiv 2402.09171), PIT / mutmut; UCIS 1.0.
- **Debug**: Debug Adapter Protocol; gdbserver RSP (the stub/front-end split);
  VS Code Testing API; rr, Pernosco, WinDbg TTD, Replay.io; PEP 669; debugpy.
- **Formal**: symbolic and concolic execution (KLEE, DART, CUTE, SymCC);
  bounded model checking (CBMC) and k-induction; SMT-LIB2 QF_BV; UCIS
  exclusions; dv-solve (`packages/dv-solve`).
- **Hot reload precedents**: Erlang/OTP hot code loading (two code
  generations, calls bound by name), JVM HotSwap / DCEVM, .NET Edit and
  Continue, Smalltalk images.
- **Daemonized fast CLIs**: Gradle daemon, `mypy dmypy`, Sorbet, the
  TypeScript `tsserver`, `rust-analyzer`.
- **Simulator embedding**: pyhdl-if; *DPI Isn't Enough* (bitsbytesgates.com,
  2024).
- **Reports**: CTRF, JUnit XML, SARIF.
- **In this repo**: bc interpreter, trace schema and `.zbc` line table
  (`packages/zuspec-be-bc`); `zuspec-rt-eng`; `zsp_bridge` mailbox
  (`docs/design/pssc-c-bridge-runtime-design.md`); `export_action.py`
  (action inheritance, `super;`); `comp_inherit.py`; op-model-py seam and
  `MemoryBus`; the manifest; `pssc.annotations`; preludes; `reg_layout`;
  the construct matrix; covsight-core (`docs/ucis-feature-catalog.md`,
  `docs/ucis-parquet-mapping.md`, `c/ucis-writer`) and covsight (testplan);
  `future-projects.md`.
