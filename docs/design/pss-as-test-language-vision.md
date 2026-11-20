# PSS as the test language: turning separation into an advantage (vision, for review)

**Status:** brainstorm and position paper, 2026-11-20. It is not a plan. Each
idea says what exists today, what the roadmap
(`full-pss-on-bc-roadmap.md`) adds, and what would be new.

---

## 0. The thesis

The usual complaint is that a PSS test is a stranger in a SystemVerilog bench:
- it is another language;
- checking lives in the SV scoreboards, and the test cannot see it;
- debugging crosses a language boundary.

All three are true when PSS is treated as a *generator*: a tool that emits SV
or C and then leaves.

They stop being true when the PSS test is **executed by an engine that we
control, through one narrow interface that we define**. Then the separation is
the asset:
- The test never needs a recompile of the bench.
- The test is **deterministic**: its behaviour is a function of seed, image
  and platform responses.
- Everything it does crosses **one observable boundary**: register and memory
  accesses, imports, waits and events.
- That boundary can be **recorded, replayed, checked, mocked and moved**: to a
  fake, a Python model, RTL, an emulator or silicon.
- The engine knows the test's **intent** (actions, flow objects, resources,
  constraints), not just its instructions.

SV class-based tests have none of these properties, because they live inside
the simulator's scheduler and share its process, its time and its random
state. Separating them out is what makes them engineerable.

> **SV tests are code that runs in the simulator. PSS tests are a program that
> talks to the simulator.** Everything below follows from that difference.

---

## 1. Where the cost really is, and how each part flips

| Apparent liability | Why it hurts today | How it flips |
|---|---|---|
| A second language | A team learns two; there are two toolchains | PSS is small, declarative and analyzable. One source gives the test, the firmware driver (op-model C) and the documentation of the programming sequence (§8) |
| Checking lives in SV | The test cannot see the scoreboard, and the scoreboard does not know the test's intent | The boundary carries intent *out* to SV checkers, and PSS predictors run on the same boundary (§4) |
| Debug crosses a boundary | Two debuggers, two timelines | One timeline: the scenario, the bus and the waveform, joined by simulation timestamps. The test-side half is replayable off the simulator (§2, §3) |
| Tool maturity | Commercial PSS tools generate code, and their debug stops at the generated code | We own the engine, the image and the debug protocol (DAP), so the debugger can be better than SV's (§3) |

---

## 2. Iterate faster: tests are data, not part of the bench

**Exists today:**
- A new or edited PSS test is a new image. The bench is not recompiled.
- **Server mode:**
  - the simulator stays up;
  - `pssc sim run` sends it test after test;
  - `pssc sim reload` swaps in an edited test (hot reload, be-bc
    `generations.md`) at an operation or test boundary.
- A failure can hold the simulator for a debugger to attach.

**Ideas that build on it:**

1. **Boot once, test many.** Elaborate and boot (reset, firmware load, link
   training) once, then run PSS tests from that state:
   - in sequence, reusing the server;
   - or from a simulator checkpoint taken after boot.

   Most simulators can save and restore, and so can Verilator with
   `--savable`. On an SoC where boot is minutes, this is the biggest
   throughput lever there is, and SV tests cannot use it, because the test is
   compiled into the image that was booted.
2. **A test shell in the simulator.** An interactive prompt in a live
   simulation:
   - run an action;
   - call an operation;
   - solve and run "this action with `with {ch == 2;}`";
   - peek a register.

   That is a REPL for the hardware. Bring-up engineers already work this way
   on silicon; PSS gives them the same thing at RTL.
3. **Fix and continue.** At a stop: edit the PSS, `reload`, and continue on
   the new generation without restarting the simulation. Code already in an
   active frame finishes on the old generation (generations' rule), so the
   behaviour is well defined.
4. **Seed sweeps without the simulator.** Run thousands of seeds standalone
   against a model (§6). Send only the seeds that reach something new to the
   simulator.

---

## 3. Debug better than SV can

This is the strongest argument. The language matters less here than the
execution model: a deterministic engine outside the simulator, with one
recorded boundary. That model is only possible because the test is not SV.

### 3.1 Reproduce a simulation failure without the simulator

The engine's behaviour is a pure function of (image, seed, platform responses);
be-bc's `determinism.md` makes that bit-identical across engines. So:
1. During a regression, record the **boundary transcript**: every request the
   test made and every response the platform gave (read data, import results,
   event arrivals). This is small: transactions, not waves.
2. A failing six-hour regression run becomes a **replay** that runs in seconds
   on a laptop, with no simulator licence: the replay platform answers from
   the transcript.

   This already exists in pssc as `testing/replay.py`.
3. In the replay, the full debugger works: breakpoints, stepping, watches,
   evaluation and the solver's state.
4. **Divergence detection:** if a fix to the test changes what it asks the
   platform, the replay reports the first divergent request. That is "your
   change altered the stimulus here".

SV has no equivalent. A UVM test's random state, its scheduling and the DUT
are entangled in one process, so reproducing it needs the full simulation, and
small changes break random stability.

### 3.2 Time-travel through the test

- pyvm's state is data: frames, objects, solver handles and the seed tree.
  Checkpointing it is cheap, and replay makes "run back to point *t*" exact.
  Reverse stepping, `stepBack` and "who last wrote this field" are therefore
  practical on the test side (`bc-debugger-plan.md` P8), with no waveform
  dump and no simulator support.
- Combined with §3.1: reverse-debug a failure from a farm, on a laptop.

### 3.3 Debug at the level of intent

The debugger knows what the test *means*, not only which line it is on
(`bc-debugger-plan.md`: "stop on anything, watch anything"):

**Breakpoints**
- on "action `dma_xfer` traversed";
- on "buffer `mem_b` bound";
- on "resource `chan[2]` claimed";
- on "field `CTRL.EN` written 1", and watches on a register field, an action
  attribute or a pool's state;
- on "covergroup bin hit for the first time".

**Views**
- **The scenario as a graph.** The activity tree, with completed, running and
  pending nodes, and the solved values on each. The view is live in a
  stopped simulation, and in a replay.
- **A scenario timeline beside the waveform.** Actions are spans on a
  timeline, and bus accesses are ticks under them. Traces already carry
  femtosecond simulation time, so a click on an action's span jumps the
  waveform viewer to it, and a cursor on the waveform shows which action
  (and which line of PSS) caused that bus cycle.

  Today an SV engineer connects "this bus write" to "this sequence item" by
  hand, through prints.

### 3.4 Explain randomization

- **"Why this value?"** At a stop, show the cone: the constraints that were
  active, what was pinned, and which soft constraints were dropped (dv-solve
  reports which softs stayed active).
- **"Why unsatisfiable?"** Name the PSS constraints, with file and line,
  through dv-solve's contradiction analysis (roadmap §4.10: unsat back to
  source).

  SV's `randomize()` failure debug is famously poor, because the solver is a
  black box inside a vendor kernel.
- **"What if?"** Re-solve the current action with a modified or extra
  constraint at the debug prompt, and see the new values without re-running
  anything. Then run the rest of the scenario from there in replay mode, with
  the remainder executed against a model (§6).

### 3.5 One debugger, everywhere

- The same DAP adapter, the same VS Code session and the same breakpoints work
  in every setting:
  - a standalone unit test;
  - a replay;
  - a live Verilator or commercial simulation;
  - the generated C on a board (op-model C with source maps, later).
- SV debuggers are per-vendor and per-simulator. Here, the test engineer's
  debugger does not change when the platform does.

### 3.6 Debug the test before there is a bench

- pssunit with the register fake and PSS mocks runs a programming sequence
  or a scenario in milliseconds, with checks and code coverage of *the test
  itself*.
- Most "test bugs" are found before the first simulation. In SV, a test
  cannot run at all until the bench compiles and the DUT is out of reset.

---

## 4. Checking: from the weakest point to a strength

The fear is that checks live in SV, so PSS tests are blind. The flip has two
parts.

### 4.1 Carry intent out to SV checking

A scoreboard has to *infer* intent from monitors. The PSS test *knows* it:
the solved action object holds the channel, the addresses, the length and the
expected outcome.

- Return the completed action object to SV (roadmap R6b), or publish it on
  the trace channel, so a scoreboard receives **expected transactions
  directly from the test's intent**.
- The scoreboard's predictor then gets simpler: it checks that the design
  did what the test asked, and it does not reconstruct what the test asked.
- Action spans carry an id. A bus monitor's transaction can be tagged with the
  action that caused it, because the boundary knows which thread and executor
  issued each access (ABI v2 carries the executor id). An SV assertion failure
  can then be reported as "during `dma_xfer#17`, channel 2, PSS line 88".

### 4.2 Run PSS checking on the same boundary

- **The mock is the predictor.** The PSS mock written to unit-test a sequence
  (`mock_c`, with `on_write`/`on_event` reaction hooks) models the design's
  register-level behaviour. In simulation the same mock runs *beside* the
  DUT, shadowing the boundary:
  - every completed RTL access is also applied to the mock;
  - any disagreement in read data or status is a check failure.

  The hif shim already keeps a shadow `RegisterFake` in step with simulation
  values. This generalizes it from a mirror into a predictor.

  Outcome: the model is written once, used for both unit tests and
  simulation checking, and portable to silicon.
- **Register semantics come free.** The register model (field kinds such as
  RC and W1C, reset values) checks every access against the specification on
  any platform. A write to a read-only field, or a read-to-clear that did
  not clear, needs no SV code.
- **Post-conditions on actions.** A check in an action's body or `post`
  (back-door reads through the platform) states the action's contract. It
  travels with the action to every platform, including silicon, where an SV
  scoreboard cannot go.
- **Sanitizers.** Unmapped accesses, runaway polls and accesses outside a
  claimed resource are boundary monitors, and they apply to every test for
  free (pssc's `sanitizers.py` today).

### 4.2b Connecting the two: checks in PSS

When the checks are written in PSS and run in the same engine as the
stimulus, the separation becomes a debugging aid:
- SV only observes: its monitors feed transactions to the engine;
- a stimulus action posts its expectation, and the engine records which
  action produced it;
- a failed check is attributed **by construction**: VS Code stops at the
  PSS check, with the stimulus action as a "caused by" frame;
- the failure replays off the simulator, and an edited checker can be
  re-run on recorded observations without re-simulating.

Design: `pss-checking-design.md`.

### 4.2a Bench failures attributed to stimulus

When a bench checker fails, the failure is captured by the runtime and
attributed to the PSS stimulus that provoked it:
- tags on requests;
- ownership of registers, resources and address regions;
- timing.

It then fails the PSS test, can stop the debugger, and the scenario can be
reduced to a minimal reproducer. Design: `bench-failure-attribution.md`.

### 4.3 The division of labour

**PSS does not replace SVA or protocol monitors.** Signal-level timing
belongs in SV. The proposal:
- **SV checks the protocol**;
- **PSS checks intent and behaviour at the transaction and register level**;
- the boundary joins the two, with ids and timestamps.

Each language does what it is good at, and neither is blind to the other.

---

## 5. Coverage: closure by construction, not by hope

- **Intent coverage.** PSS covergroups over actions and flow objects measure
  *scenarios*: which channel combinations ran concurrently, and which
  producer/consumer pairs occurred. That is what plans are written in. SV
  measures them indirectly, through signals.
- **One database.** Functional, code (of the test), register and operation
  coverage are in one covsight database, merged across seeds and platforms
  (roadmap R2).
- **Targeted generation.** Because the test is declarative and the solver is
  ours, an unhit bin can be turned into a constraint, and the scenario that
  hits it can be **solved for**, rather than waited for:
  - dv-solve's coverage bias (roadmap §4.10);
  - structural coverage through the enumeration oracle (activity D19).

  SV's procedural tests cannot be inverted this way.
- **Static reachability.** Before any run, the coverage points that no legal
  scenario can reach can be reported (enumeration or solver proof). Those are
  dead bins, or over-constrained models, found at compile time.

---

## 6. Spend simulation cycles only where they matter

- A scenario runs at engine speed against a model: a register fake, a PSS
  mock, or a Python or Zuspec transaction-level model of the design. Run
  10,000 seeds there and record each seed's coverage.
- **Then pick the subset that adds coverage, and only those go to RTL.**
  RTL time goes to stimulus already known to be new, and every seed is
  reproducible, because it is the same image with the same seed.
- **Checking the model.** When RTL and the model disagree on a promoted seed,
  either the design or the model is wrong, and both are informative. Over
  time the model becomes trustworthy enough that a large share of
  scenario-level verification runs without a simulator.
- An SV test cannot leave the simulator, so this funnel is impossible there.

---

## 7. Portability, made concrete

The classic PSS pitch is "the same test on simulation, emulation and silicon".
The engine makes it operational:
- **The same boundary transcript format on every platform.** A silicon failure
  that is recorded (a pick log plus the responses at the boundary,
  `dynamic-multi-actor-executors.md` D21) can be replayed in simulation or on
  the laptop.
- **Differential platforms.** Run the same seed on the fake, the model, RTL
  and the emulator, and compare the transcripts executor by executor. The
  first divergence localizes the bug to a layer.
- **The firmware you verify is the firmware you ship.** op-model C is
  generated from the same PSS whose behaviour was unit-tested, covered and
  run against RTL. In an SV flow, the driver sequence and the firmware are two
  hand-written copies that drift.

---

## 8. Analysis that only a declarative test allows

- **The plan is the test.** In global mode, the planned scenario (`plan.json`)
  can be reviewed, diffed between versions, and adjudicated by the corpus
  checker *without running it* (activity design §8.3). A test review becomes
  a review of what will happen, not of code that might do it.
- **Impact analysis.** pssunit's `--changed` slicing already runs only the
  tests a change can affect. For scenarios, a changed constraint maps to the
  coverage bins and seeds whose plans change.
- **Lint for scenarios.** Report resources that are never contended,
  `select` branches that can never be chosen, and actions no inference can
  reach.

---

## 9. Agents and automation

PSS is a better target for AI-assisted test work than UVM, for structural
reasons. It is:
- small and declarative, so the model space is constrained;
- run standalone in milliseconds;
- traced as structured events;
- deterministic and replayable;
- driven by a stateful `--json` CLI (the agent interface recorded for this
  project).

An agent can:
- write a scenario;
- run it against mocks;
- read the unsat explanation;
- fix it;
- sweep seeds, pick the seeds that add coverage, and hand them to the
  simulator.

It can also triage a failing regression by replaying its transcript and
bisecting to the first divergent request. None of that loop needs a
simulator licence until the last step.

---

## 10. The honest costs, and what we do about them

| Cost | Mitigation |
|---|---|
| Two languages | Keep PSS for intent, scenario and register-level behaviour; keep SV for protocol and signals. The boundary connects them (ids, timestamps, intent export). |
| pyvm speed | Fine for unit tests, debugging and replay; the C engine (roadmap R8) for long simulations and seed sweeps |
| Engine-in-simulator integration (DPI, pyhdl-if) per simulator | One SV package and one host ABI; one commercial-simulator smoke target (R6c) |
| Concurrency inside a test, across one boundary | Host ABI v2: concurrent requests with executor ids (R6b) |
| PSS gaps (inference, collections, …) | the roadmap's R1–R5, with the corpus as oracle |
| The ecosystem expects UVM sequences | The scoreboard intent export (§4.1). The PSS test can drive UVM sequences through blocking imports, so existing VIP stays. |

---

## 11. The best reasons, ranked

1. **Failures reproduce off the simulator.** A deterministic engine plus a
   recorded boundary turns any simulation failure into a seconds-long,
   licence-free, reverse-debuggable replay (§3.1, §3.2).
2. **No recompiles, boot once, a live shell, fix and continue.** These iterate
   at software speed on a hardware bench (§2).
3. **The debugger knows intent.** Breakpoints on actions, claims and field
   writes; the scenario graph; a scenario timeline linked to the waveform;
   explained randomization (§3.3, §3.4).
4. **The test is tested before the bench exists.** pssunit with mocks and
   coverage of the test itself (§3.6).
5. **Intent-aware checking.** Scoreboards get expected transactions from the
   test; the PSS mock is reused as a predictor in simulation; register
   semantics are checked for free (§4).
6. **Closure by construction.** Unhit bins are solved for, unreachable bins are
   found statically, and seeds that add coverage are chosen on a model before
   any simulation (§5, §6).
7. **One debugger, one transcript, one coverage database on every platform,
   and the firmware that ships is the firmware that was verified** (§3.5,
   §7).

---

## 12. Demonstrations that would make the case

Each one is small, and each maps to roadmap items.

| Demo | Shows | Needs |
|---|---|---|
| WB DMA: a failing simulation becomes a laptop replay; set a breakpoint on the action, step back to the register write that caused it | reasons 1 and 3 | replay of sim transcripts (exists in part), `bc-debugger-plan` P2 and P8 |
| Live Verilator plus VS Code: stop on "channel 2 claimed", edit the PSS, reload, continue | reasons 2 and 3 | server mode and reload (exist), the DAP adapter in be-bc |
| A scenario timeline alongside the waveform (an action span ↔ the waveform cursor) | reason 3 | traces with fs time (exists), a viewer plugin |
| The PSS mock as a predictor beside the RTL; inject an RTL bug and watch the mock flag it | reason 5 | the shadow mock (generalize the hif shim's shadow fake) |
| 10k seeds on the model, the 20 that add coverage go to RTL; compare closure against random RTL seeds | reason 6 | R2 coverage, the run API, `bench_bc` |
| "Why unsat?" naming PSS lines | reason 3 | the dv-solve unsat-to-source item |

---

## 13. Questions for review

1. Which demo comes first? The recommendation is the replay demo: it is mostly
   built and it is the most surprising to an SV audience.
2. Should the **boundary transcript** be a first-class, specified artifact
   (format, versioning, size) in be-bc next to `trace-schema.md`? This
   document leans on it heavily. Today it is pssc's replay format.
3. Should the shadow-mock predictor be on the roadmap (R6), or wait for demand?
4. Is a simulator checkpoint after boot (§2.1) in scope for the SV runtime
   package, or left to each bench?
