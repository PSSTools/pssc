# Checking in PSS: one engine for stimulus and checking (design for review)

**Status:** draft, 2026-11-20. Builds on `pss-as-test-language-vision.md`
§4 and `bench-failure-attribution.md`.

## 0. The idea

`bench-failure-attribution.md` takes the bench as it is: checkers in SV,
stimulus in PSS. It recovers the link between a failure and its stimulus
from *evidence*: tags, ownership and timing. That is necessary for existing
benches, but it is still detective work.

This design closes the gap at its source. **The checks are written in PSS
too, and run in the same engine as the stimulus.** SV keeps one job:
**observation**. Its monitors turn pins into transactions and hand them to
the engine. Then:

- **Attribution comes by construction, not by evidence.** A stimulus
  action states what it expects, and a checker compares that expectation
  with what was observed. The engine records which action produced each
  expectation. A failed check *knows* its stimulus: a single step in the
  debugger.
- **Checking becomes debuggable like the test.** The same breakpoints,
  watches, time travel and VS Code session apply. A failing check is a
  stop at a PSS line, with the stimulus behind it on the call stack.
- **Checking becomes replayable.** Observations are inputs at the boundary,
  so they are in the transcript. A failing check reproduces on a laptop
  with no simulator. A *new version of a checker* can be re-run against a
  regression's recorded observations without re-simulating ("re-check, don't
  re-simulate").
- **Checking becomes portable and testable.** The same checkers run against
  the register fake, a model, RTL and silicon (where readback replaces
  monitors). They are unit-tested in pssunit before any bench exists, and
  calibrated: a faulty mock must make them fire.

The separate language stops being a liability. Stimulus and checking in one
deterministic, observable, replayable engine is a **better debugging
environment than an SV bench**, where stimulus and checking are separate
class hierarchies linked only by a TLM port and print statements.

## 1. Division of labour

| Concern | Where | Why |
|---|---|---|
| Pin-level protocol, timing, X-checks | SV (SVA, protocol VIP monitors) | cycle-accurate and signal-level; the simulator is the right engine |
| Turning pins into transactions | SV monitors (existing VIP) | the VIP already does it; we only subscribe |
| Stimulus: actions, scenarios, randomization | PSS | — |
| **Expectations** (what this stimulus should cause) | PSS, inside the stimulus action | the action knows its intent; no reverse engineering |
| **Transaction-level checking:** scoreboards, reference models, ordering, completion | PSS checkers | one engine with the stimulus: attribution, debugging, replay, portability |
| Behavioural (temporal) checking and coverage over transactions | PSS monitors (LRM 3.x `monitor`) | declarative patterns over observed and executed behaviour |

Existing SV scoreboards keep working, and are attributed through
`bench-failure-attribution.md`. Migrating a check into PSS upgrades its
attribution from *evidence* to *construction*.

## 2. Building blocks

### 2.1 Observation ports: monitors in, typed

- A PSS model declares the transactions it observes:

  ```pss
  struct mem_wr_s { bit[32] addr; bit[32] data; bit[4] sel; }
  struct irq_s    { bit[3] ch; }

  component dma_obs_c {
      observe mem_wr_s mem_wr;   // proposed: an inbound port the bench feeds
      observe irq_s    irq;
  }
  ```

  PSS has no `observe` keyword. This is either a pssc extension, or (better
  for the standard) an `import`ed channel convention: a `sync_pkg` channel
  marked `@pssc::observe` that only the host puts to. **Decision C1.**
- **Image:** an `OBSERVE` section names each port, its struct layout and
  its channel.
- **Host ABI:** `pssvm_observe(h, port_id, t_fs, values[])` posts one
  transaction. This is the general form of the `CHAN` notify that is
  unimplemented today.
- **SV runtime package:** `op-model-sv-vm` generates one typed function per
  port (`dma_obs_mem_wr(addr, data, sel)`) and a `uvm_subscriber` adapter, so
  hooking up an existing VIP monitor is one `connect` of its analysis port.
- **Standalone:** the platform posts observations. A register fake or mock
  posts the transactions *it* would cause (the mock's DMA model posts
  `mem_wr`), so checkers run unchanged without a simulator.
- **Timing:** an observation carries simulation time. The engine processes
  it while the simulator is blocked in the call, so checking takes zero
  simulation time.

### 2.2 Expectations with provenance

A stimulus action states its expectation by **posting an item to a checker's
channel**:

```pss
action xfer_a {
    rand bit[32] src, dst; rand bit[16] len; rand bit[3] ch;
    exec body {
        program_channel(ch, src, dst, len);   // the operation model
        comp.chk.exp.put(exp_copy_s{ch, src, dst, len});
    }
}
```

**Engine feature: provenance on channel items.**
- Every item put on a channel carries the putting thread's **stimulus id**
  (the stable action-instance id from `bench-failure-attribution.md` §3.1)
  and its `src_ref`.
- This costs nothing to write, and the engine already knows both.
- A checker that `get`s the item inherits its provenance. Any check that
  fails while it handles the item is attributed to the item's producer.

Flow objects carry the same lineage natively: a buffer's producer is in the
plan. A checker that consumes an expectation *buffer* gets the same link
through the activity design's binding.

### 2.3 Checkers

There are three forms, from the simplest up. All are ordinary PSS on bc.

1. **Reactive checks.** A function runs on each observation:

   ```pss
   @pssc::on_observe("mem_wr")
   function void on_mem_wr(mem_wr_s w) { ... }
   ```

   This generalizes pssunit's `on_write`/`on_event` hooks, which already
   run PSS functions on platform events.
2. **Scoreboard actions.** A checker action is traversed in `parallel` with
   the scenario, or started at `run_start`. It loops on `get` from the
   expectation and observation channels and compares:

   ```pss
   action copy_sb_a {
       exec body {
           repeat {
               exp_copy_s e = comp.exp.get();       // provenance: xfer#5
               repeat (i : e.len / 4) {
                   mem_wr_s w = comp.obs.mem_wr.get();
                   check_eq(w.addr, e.dst + i*4, "dst address");
                   check_eq(w.data, comp.mem.read32(e.src + i*4), "copied data");
               }
           }
       }
   }
   ```

   `check_eq` is pssunit's existing check, with the existing ledger and
   verdict.
3. **Reference-model checking.** The PSS mock that unit-tests the sequence
   (`mock_c`, a register-level model of the device) runs as the predictor:
   - stimulus accesses drive it;
   - its predicted side effects (memory writes, interrupts, status) are
     compared with the observations.

   It is written once, used in pssunit, used in simulation, and portable.

### 2.4 Behavioural checks: PSS monitors

LRM 3.x `monitor` types describe temporal patterns: `sequence`,
`concat`, `eventually`, `overlap`, `schedule` over action executions, with
`cover` for behavioural coverage. pssparser parses them; ast2ir refuses them
today.

- **Proposal:** run monitors on bc over **two event streams**:
  - executed actions (stimulus);
  - observed transactions, wrapped as observation actions.
- Their natural use is behavioural **coverage** ("an interrupt came after
  every transfer, possibly overlapping the next").
- **Checking** is a small step from there: a monitor whose completion is
  *required* by a deadline or by end of test. LRM 3.x has no assertion form
  for monitors, so this would be a pssc extension (`@pssc::require`) or a
  check on the monitor's completion count. **Decision C2.**
- **Representation:** a monitor is a matcher coroutine in the engine. It is
  lowered like an activity, but it *consumes* events instead of issuing
  traversals. A spec section in be-bc is needed.

### 2.5 Coverage of what happened, not only of what was asked

- Covergroups over **observed** transactions (roadmap R2 sampling, with
  observation sites added) measure what the DUT actually did.
- Comparing them with intent coverage over the stimulus is itself a
  diagnostic. For example, "the test requested 400 bursts of 16; the bus saw
  none" means the DUT split them.

## 3. The debugging experience

| Situation | What the engineer sees |
|---|---|
| A check fails in simulation | VS Code stops at the `check_eq` line in `copy_sb_a`, with the simulation held. The **stack shows a "caused by" frame**: `xfer#5` at line 88, with its solved values. The observed `mem_wr` shows its simulation time and monitor path; one click moves the waveform cursor there. |
| "Why did it expect that?" | Step back (time travel) from the checker into `xfer#5`'s body, to the `put` of the expectation and the solve that produced `dst`. |
| A regression failure | Replay the transcript: stimulus responses *and* observations. The check fails identically on a laptop with no simulator, with the full debugger. |
| A checker bug is suspected | Edit the checker and **re-check the recorded transcript** of that failure, or of a whole regression, without re-simulating. A false failure is fixed and verified in minutes. |
| One timeline | Stimulus action spans, observation ticks and check results on one timeline, linked to the waveform by simulation time. |
| An intermittent failure | Fault localization over scenario features across seeds (`bench-failure-attribution.md` §6), now with *exact* attribution per failure. |
| Minimizing | Reduce the scenario (§5 of the attribution doc). The checker comes along unchanged, so each reduced run is checked identically. |

**Where SV falls short:**
- A scoreboard failure in SV is a `uvm_error` line, a time and whatever the
  message author printed.
- The stimulus that caused it is in another class hierarchy, reachable by
  reading code and logs.
- Neither side can be replayed without the simulator.

## 4. Testing the checkers

- **Unit tests in pssunit:** run the checker against the PSS mock posting
  correct observations. It must pass.
- **Calibration** (vision P7): run the same tests with a **faulty mock**:
  - a dropped write;
  - a wrong address;
  - a reordered completion.

  The checker must fail, and attribute the failure to the right action.
  This is mutation testing of the checking, done before RTL exists.
- **Coverage of the checker:** code coverage (it exists today) shows checker
  branches that no test exercises.

## 5. Where it lives

| Piece | Home |
|---|---|
| `pssvm_observe` ABI, the `OBSERVE` section, channel-item provenance, the "caused by" link in traces | be-bc spec, pyvm; rt-eng later |
| Monitor matcher semantics and lowering | ir-core `xf/pss_lower` (monitor lowering), be-bc runtime |
| Typed observe functions, `uvm_subscriber` adapters | the SV runtime package (generated by `op-model-sv-vm`) |
| "Caused by" frames, re-checking a transcript | the DAP adapter and run API (Zuspec) |
| `observe` / `@pssc::observe` syntax, `@pssc::on_observe`, `@pssc::require` | pssc |

## 6. Phasing

Each step stands alone and is useful by itself.

| Step | Content | Depends on |
|---|---|---|
| K1 Observation ports | ABI `pssvm_observe`, `OBSERVE` section, generated SV functions and subscribers; standalone posting from mocks | R0 (image, run API) |
| K2 Provenance | stimulus ids; channel-item provenance; "caused by" in check failures and reports | attribution A2 |
| K3 Reactive checks and scoreboard actions | `@on_observe`; checker actions beside the scenario | K1, actions in simulation (R6a) |
| K4 Re-checking | the transcript includes observations; `pssc test --recheck TRANSCRIPT` | K1, replay |
| K5 Debugger | "caused by" frames, a stop on a failed check, waveform link | `bc-debugger-plan.md` P2 |
| K6 Reference model in simulation | mock as predictor (generalizes the hif shim's shadow fake) | K1 |
| K7 Monitors | monitor lowering; behavioural coverage; `require` | R2 coverage |

**First demo** (extends the attribution demo on `fw_wb_dma_uvm`):
1. Port `wb_dma_scoreboard`'s copy check to a PSS `copy_sb_a`, fed by the
   bench's existing Wishbone monitor through a generated subscriber.
2. Inject the channel-2 RTL bug.
3. VS Code stops at the PSS check, with `xfer#5` as "caused by".
4. Replay the transcript on a laptop and get the same stop.
5. Fix a deliberately planted checker bug and re-check without
   re-simulating.

## 7. Questions for review

- **C1.** How to declare observation ports: a pssc `observe` extension, or a
  `sync_pkg` channel with an annotation (standard PSS plus an annotation)?
  The annotation is recommended; it stays within the LRM.
- **C2.** Required monitors: is a pssc extension acceptable, or should
  checks stay procedural (scoreboard actions) until the PSS working group
  addresses it?
- **C3.** Scope: transaction-level checks only (recommended), or also
  cycle-level expectations? Cycle-level belongs in SVA, and pyvm's speed
  argues the same way.
- **C4.** Should expectation items be plain channel items (simple, works
  now) or buffer flow objects (lineage through the planner, inference can
  add checkers)? The recommendation is channels first, buffers once R4/R5
  land.
- **C5.** Performance: each observation runs engine code under the
  simulator. Measure on the WB DMA bench (pyvm), and set the rate at which
  the C engine becomes necessary.
