# Full PSS on bc: phased implementation, test and documentation plan

Tracking file. Started 2026-11-20. Nothing here is implemented yet.

This turns the review documents into work that can be done and checked off:
- `full-pss-on-bc-roadmap.md`: strategy, architecture, roadmap R0–R8, decisions S1–S10;
- `activity-flow-resource-bc-design.md`: activity P2–P8 (planner, inference, OTF);
- `pss-checking-design.md`: checking in PSS, K1–K7;
- `bench-failure-attribution.md`: attribution for SV checkers, A1–A6;
- `bc-debugger-plan.md`: the DAP debugger, P0–P11;
- `pss-as-test-language-vision.md`: why all of this is worth doing.

Those documents say *what* and *why*. This one says *in what order, how each item
is tested, what gets documented, and where it stands*. Where they disagree, the
design document wins and this plan is corrected.

**How to use this file**
- A fresh session reads §0 (status) first, then the current phase.
- Tick an item only when its test exists and passes. Its docs must be written
  too. Record the date and the change in §0's log.
- A design decision found while implementing goes into the owning design
  document, not here. This file links to it.
- Never commit or push from a session working this plan unless told to in
  that message.

---

## 0. Status

| Phase | Name | State |
|---|---|---|
| F0 | Decisions, spikes and the feature ledger | not started |
| F1 | Foundations and the simulation lane | not started |
| F2 | Actions in simulation: the first vertical slice | not started |
| F3 | Randomization | not started |
| F4 | Functional coverage | not started |
| F5 | Host ABI v2: concurrency, executors, events, observation | not started |
| F6 | Data types and the value ABI | not started |
| F7 | Memory: address spaces, claims, allocation | not started |
| F8 | Activities completeness (planner v0) | not started |
| F9 | Inference | not started |
| F10 | Checking in PSS, attribution and debug in simulation | not started |
| F11 | Monitors and cover statements | not started |
| F12 | Simulation breadth and regression flow | not started |
| F13 | OTF and live arbitration | when needed |
| F14 | C engine alignment | after F9 |

**Log** (newest first)
- 2026-11-20: plan written. Waiting on review of the decisions in F0.

---

## 1. What "all PSS features" means: the feature ledger

The ledger is the LRM's own syntax list (PSS 3.1 draft, Syntax 1–165) grouped
into feature areas. Each area has one row here; F0.4 expands it into a
generated, per-construct matrix (`docs/pss-feature-support.md`). The matrix is
what "done" is measured against.

State as of 2026-11-20, from `full-pss-on-bc-roadmap.md` §2.2 and the
construct inventory. "?" means F0.4 must establish it.

| Area | LRM syntax | State on bc today | Phase |
|---|---|---|---|
| Lexical, literals (numeric, string, triple-quoted, aggregate, map, struct) | 1–7 | numeric and string done; aggregates partly; triple-quoted format expansion ? | F6 |
| Integer, bool, enum | 8, 9, 11 | done (`in` domains on declarations ?) | F0.4 verify |
| Floating point and float library | 10, 120–124 | ? (likely refused) | F6 |
| Strings | 12, 24 | interned; `==`/`!=` only | F6 |
| chandle | 13 | ? | F6 |
| Structs | 14 | done (leaf slots); not across `CALL`/`IMPORT` | F6 |
| Collections: array, list, map, set | 15 | refused | F6 |
| Reference types | 16 | ? | F6 |
| typedef, cast | 17, 18 | done ? | F0.4 verify |
| Annotations, `code_doc` | 19, 20, 125, 126 | in IR; no execution meaning | n/a (verify no drop) |
| Expressions, `?:`, `in`, string slice | 21–24 | done except collections and slices | F6 |
| Components: declaration, instances, `comp` ref qualifier, `mutable`, pure | 25–27 | instances and `comp` choice done; `comp` ref qualifier and `mutable` ? | F8 |
| Actions: atomic, compound, abstract, override | 28 | done; abstract and `override` ? | F8 |
| Flow objects: buffer, state | 29, 31, 32 | done (no `prev`/`uid`) | F9 (P4b) |
| Flow objects: stream | 30 | refused | F8 |
| Resources: lock, share | 33, 34 | done (≤64 instances); attributes and claim arrays refused | F8 |
| Template types | 35–38 | specialized by ast2ir | F0.4 verify |
| Activity: traversal, sequence, parallel, atomic, repeat, if, match, replicate, symbol, bind | 39–42, 45–47, 50–54 | done | — |
| Activity: `schedule` | 43 | partial (refused when members interact) | F8 |
| Activity: join specs | 44 | ALL/NONE/FIRST; SELECT/BRANCH refused | F8 |
| Activity: `foreach`, `select` with feasibility | 48, 49 | `foreach` refused; `select` not filtered | F8 |
| Pools, static bind | 55, 56 | done | — |
| Constraints: expression, implication, conditional, `default` | 57–61, 65 | done | — |
| Constraints: `foreach`, `forall`, `unique` | 62–64 | `foreach` deferred; `forall`, `unique` ? | F3, F6 |
| Constraints: `soft`, `dist` | 66, 67 | refused | F3 |
| Scheduling constraints | 68 | ? (likely refused) | F8 |
| Covergroups, coverpoints, bins, crosses | 69–73 | IR only | F4 |
| Cover statements and monitors | 74–84 | parsed; refused by ast2ir | F11 |
| Type extension, override, packages, import | 85–88 | extension done; `override` ? | F0.4 verify, F8 |
| `compile if`/`has`/`assert` | 89–91 | front end | F0.4 verify |
| Exec blocks, all kinds | 92, 98 | done except `run_start`/`run_end` (silently dropped) | F1 |
| Target-template exec and functions | 99 | absent | refuse with location (F1) |
| Functions: native, import, export, qualifiers | 93–96 | done; import ≤2 args at orchestration level | F6 |
| Import class | 97 | ? | F6 (refuse or support) |
| Procedural statements | 100–110, 112 | done | — |
| Procedural `randomize` | 111 | absent | F3 |
| Export action | 113 | in the op-model targets; not an SV-runtime entry | F2 |
| Messages, string formatting, file I/O, errors, `urandom` | 114–119 | messages done; file I/O ?; `urandom` ? | F6 |
| Executors, executor groups, claims, query, `set_executor` | 127–132 | static `set_executor`; claims and groups ? | F5 |
| Channels | 133 | done (blocking ops spin) | F5 (host events) |
| Address spaces, regions, claims, handles | 134–153 | transparent spaces only; no allocation | F7 |
| `packed_s`, `sizeof_s`, memory primitives | 145, 146, 154–159 | primitives done; packed struct read/write ? | F7 |
| Registers, register groups | 160–165 | done (op model, pssunit) | F0.4 verify |

**Rule:** an area is finished when every construct in it is either
supported on bc with tests, or refused with a location and a reason in the
matrix. Silence is never an option (roadmap P4).

---

## 2. Integration focus: simulation from the start

Simulation is not a phase at the end. A feature is not finished until it runs
in simulation. Three standing lanes carry that rule.

**Lane S1: the corpus in simulation.**
- The corpus's executable tier is run with pyvm hosted in Verilator, through
  the SV runtime and a generic bench (`pssvm_tb`):
  - a memory model on a bus;
  - a register file;
  - an IRQ and event source;
  - an observation feed;
  - the corpus's L0-tap/L0-protocol output channel.
- New adapter: `tests/compliance/adapters/pssc_bc_sim.py`.
- New test: `tests/compliance/test_compliance_bc_sim.py`, marker `sim`, with a
  strict `expected/bc-sim.toml`.
- The verdict comes from the corpus checker, as for every other adapter.

**Lane S2: standalone/simulation differential.**
- For every S1 test, the run trace (`trace-schema.md`) recorded standalone and
  the one recorded in simulation must agree on everything that does not
  depend on timing:
  - each thread's events in order;
  - each executor's bus accesses in order;
  - solved values;
  - coverage samples.
- This turns "it runs in simulation" into a checked claim. It is the main
  integration invariant of the plan.
- The comparison tool (`zuspec.be.bc.trace.diff`) is built in F1 and used
  everywhere after.

**Lane S3: the WB DMA bench.**
- `examples/fw_wb_dma_uvm` grows scenario tests phase by phase.
- The same `test_a` runs on:
  - the register fake;
  - a PSS mock of the DMA;
  - a Python DMA model;
  - Verilator RTL.
- Per-executor bus traces must match across all four, and coverage is merged.
- This is where checking (F5/F10) and attribution are demonstrated.

Each phase gate below includes its S1/S2/S3 rows. A phase that adds a
construct adds the corpus tests for it, and those tests run in all three
lanes from day one. A test that can't yet run in simulation goes in
`expected/bc-sim.toml` with the defect that blocks it.

---

## 3. Cross-cutting rules for every work item

**Definition of done:**
1. **Implementation:** in the owning package (homes below). Zuspec packages
   never import pssc.
2. **Tests:**
   - unit tests in the owning package;
   - corpus tests for language-visible behaviour (authored in `pss-corpus`,
     judged by its checker);
   - S1 and S2 rows;
   - a calibration test for any semantic property: it must fail with the
     feature switched off (roadmap P7).
3. **Refusals:** anything not supported is refused with a location. A
   registry test covers the new node kinds.
4. **Docs:**
   - the be-bc spec page for engine semantics;
   - the pssc user doc for anything a user writes or runs;
   - the feature matrix row;
   - AGENTS.md for any new invariant a future change could break.
5. **C engine:** it refuses the new op or section before running anything,
   and a test shows it (until F14).
6. **Perf:** if the item touches the solve or step loop, the `tests/perf`
   budget is rerun and recorded.

**Homes:**

| Concern | Package |
|---|---|
| PSS syntax, linking, typing, ast2ir, pssunit, CLI front ends | pssc, pssparser |
| PSS lowering, elaboration, planner | zuspec-ir-core `xf/pss_lower`, `xf/plan` |
| Image format, lowering to bc, pyvm, run API, platforms, coverage service, trace, SV runtime | zuspec-be-bc |
| Solving | dv-solve (features), zuspec-solver (IR to dv-solve translation) |
| Coverage database | covsight |
| C engine, DPI host | zuspec-rt-eng |
| Language tests and the checker | pss-corpus |

**Docs that already exist and are checked:**
- `tests/unit/test_doc_references.py`: every new design note goes on its
  allow-list.
- `tests/unit/test_doc_examples.py`: user-guide code blocks are quoted from
  sources that run.

New user docs follow the same pattern.

**Test commands** (output to a log file, report pass/fail):
```
PYTHONPATH=src packages/python/bin/python -m pytest tests/unit > unit.log 2>&1
PYTHONPATH=src packages/python/bin/python -m pytest tests/compliance > corpus.log 2>&1
PYTHONPATH=src packages/python/bin/python -m pytest -m sim tests/compliance > sim.log 2>&1
```
The be-bc, ir-core and dv-solve suites run from their own packages.

---

## F0. Decisions, spikes and the feature ledger

**Goal:** settle what blocks design choices, and measure what we would
otherwise guess.

| ID | Item | Output | Done |
|---|---|---|---|
| F0.1 | Review decisions: roadmap S1, S3–S10; checking C1–C5; attribution §9; debugger D1–D9 | the decisions recorded in each design document | [ ] |
| F0.2 | dv-solve capability spike: run each item of roadmap §4.10 through dv-solve directly (soft ranks matching PSS priority, dist over rand, `seq_pick`, `randc`, array sizes, contradiction cores, enumeration) | `docs/design/dv-solve-gap-report.md`: what exists, what to add (dv-solve work items with owners) | [ ] |
| F0.3 | Simulation cost spike: pyvm under Verilator via pyhdl-if on the WB DMA bench; time per request, per solve and per observation (C5) | numbers in `pss-checking-design.md` §7 and roadmap S10; a `tests/perf` budget row | [ ] |
| F0.4 | LRM example sweep: run every LRM example and Syntax-box construct through parse → ast2ir → lower → run; record the stage reached | `scripts/lrm_sweep.py`; `docs/pss-feature-support.md` generated from it; the "?" cells of §1 filled | [ ] |
| F0.5 | Corpus authoring plan: the list of new corpus tests per phase (randomization statistics, coverage outcomes, L4 flow and resource, inference, monitors), with checker features each one needs | `packages/pss-corpus/PLAN.md` section | [ ] |

**Gate:** F0.1 decisions recorded; F0.2 and F0.3 reports in the repo; the
feature matrix generated with no "?".

**Docs:** the three reports above; §1 of this file updated.

---

## F1. Foundations and the simulation lane

Roadmap R0, plus bringing up the simulation lane on what runs today
(exported functions). Moving code out of pssc changes no behaviour, and lands
as its own changes.

### F1a. Layering and the run API

| ID | Item | Test | Done |
|---|---|---|---|
| F1.1 | Layering test: no `zuspec.*` module imports `pssc` | be-bc and pssc tests scan imports | [ ] |
| F1.2 | be-bc `run` API: `run(image, entry, platform, seed, opts) -> RunResult` (result, trace, coverage, verdict); `Platform` protocol (memory, imports, events, time, observe) | unit tests on hand-built images; the pssc suite result is unchanged | [ ] |
| F1.3 | Port `pssc test`, the compliance adapter and `scripts/bench_bc.py` onto it; delete the copied run glue | full pssc suite, corpus, perf: unchanged | [ ] |
| F1.4 | `pssc run --root C::A --seed N [--platform ...]` front end | CLI tests; `docs/cli.md` | [ ] |
| F1.5 | `zuspec-bc run image.zbc --entry ... --platform ...` | runs a compliance image with pssc absent from `sys.path` | [ ] |

### F1b. The self-describing image (shared with debugger P1)

| ID | Item | Test | Done |
|---|---|---|---|
| F1.6 | `ENTRY` extended (kind, qualified name, parameters, action object layout) | format round trip; the C header generated from `spec.py` | [ ] |
| F1.7 | `LAYOUT`, `REGMAP` sections | round trip; the fake and the debugger read them instead of the IR | [ ] |
| F1.8 | `META:<ns>`; pssc's checks and hooks move into `META:pssc` | pssunit suite unchanged | [ ] |
| F1.9 | rt-eng ignores unknown sections and refuses unknown ops up front | rt-eng test | [ ] |

### F1c. Moving execution code out of pssc

| ID | Item | Test | Done |
|---|---|---|---|
| F1.10 | Register fake and field semantics → be-bc `platform` | the fake's tests move with it | [ ] |
| F1.11 | Coverage service, sinks, UCIS writer → be-bc `cov` | `.cdb` byte-identical before and after on the existing tests | [ ] |
| F1.12 | Trace writer, replay, sanitizers → be-bc `trace`; `trace.diff` (lane S2's comparator, timing-independent projection) | diff tests, including a known-different pair | [ ] |
| F1.13 | SV runtime (`pssvm_pkg`) and the Python host → be-bc `share/sv` (S4); the host rewritten on the run API, with no `pssc.testing` import; pssc's `sv-pssvm-path` points there | existing sim tests pass | [ ] |

### F1d. No silent drops and run_start/run_end

| ID | Item | Test | Done |
|---|---|---|---|
| F1.14 | bc lowering registry: every IR element lowered or refused with a location | registry test fails on an unlisted kind | [ ] |
| F1.15 | `run_start`/`run_end` run around the root activation | corpus test (new); a calibration that fails if they are dropped | [ ] |
| F1.16 | Target-template exec blocks and functions, `header`/`declaration`: located refusal on bc | refusal tests | [ ] |
| F1.17 | Spec catch-up: `activation.md` (`ACT`), `host-abi.md` (current v1 exactly) | doc review | [ ] |

### F1e. The simulation lane, on what runs today

| ID | Item | Test | Done |
|---|---|---|---|
| F1.18 | `pssvm_tb`: a generic SV bench (memory, register file, IRQ source, observation feed stub, output channel) under be-bc `share/sv/tb` | builds under Verilator; a self-test | [ ] |
| F1.19 | Lane S1 adapter `pssc_bc_sim.py` and `test_compliance_bc_sim.py` (marker `sim`), running function entries; `expected/bc-sim.toml` lists everything that needs action entries | strict expected list | [ ] |
| F1.20 | Lane S2: the S1 runs record a trace; `trace.diff` against standalone | diff clean on every passing S1 test | [ ] |
| F1.21 | One Verilator build per bench, reused across tests (images loaded at run time, the "no recompile" property) | a timing assertion: a second test does not rebuild | [ ] |

**Gate:**
- the pssc suite, corpus and perf are unchanged;
- the layering test passes;
- `zuspec-bc run` works without pssc;
- lane S1 runs the function-entry corpus tests in Verilator, with S2 clean.

**Docs:**
- be-bc: `docs/spec/run-api.md` (new), `platform.md` (new), `zbc-format.md`
  sections, `host-abi.md`;
- pssc: `docs/cli.md` (`pssc run`), `docs/simulation.rst` (the SV runtime's new
  home, the bench);
- AGENTS.md: the layering rule; "a feature runs in lane S1".

---

## F2. Actions in simulation: the first vertical slice

Roadmap R6a, plus attribution A1/A2. The milestone is the roadmap's slice
(R0+R2a+R6a), except that coverage arrives in F4. The slice's scenario runs
here without the covergroup, and F4 adds it.

| ID | Item | Test | Done |
|---|---|---|---|
| F2.1 | Action entries in the SV facade, runner and Python host (`call_start_obj`) | S1: the `act/*` and `comp/*` corpus tests leave `bc-sim.toml` | [ ] |
| F2.2 | Seeds from plusargs (`+pss_seed`), recorded in the trace header | same seed, same trace, in and out of simulation (S2) | [ ] |
| F2.3 | Exported actions (`export action`, Syntax 113) become image entries, with typed parameters from SV | corpus test; generated SV entry signatures | [ ] |
| F2.4 | Trace and code/register coverage written from the simulator | `.cdb` from simulation merges with standalone | [ ] |
| F2.5 | `run_start`/`run_end` once per simulation run | S1 | [ ] |
| F2.6 | Stable stimulus ids (node path plus `#n`) in the trace and on requests | unit test; ids stable across seeds when the structure is the same | [ ] |
| F2.7 | Attribution A1: report catcher, `pssvm_bench_fail`, `pssvm_report`, `EV_BENCH` with an in-flight snapshot, the verdict rule | sim test: an injected `uvm_error` attributed to the in-flight action | [ ] |
| F2.8 | Attribution A2: `stim_id` on requests and in `pssvm_platform_if`; `pss#id` token parsing; temporal ranking | sim test with two overlapping actions | [ ] |
| F2.9 | WB DMA scenario: channel-configuration actions with rand fields, a compound `parallel` over channels | lane S3 on fake, PSS mock and RTL (the Python model is F2.10) | [ ] |
| F2.10 | Python design-model platform: a Python WB DMA model behind the `Platform` protocol | lane S3: the fourth column | [ ] |

**Gate:**
- lane S1 runs every currently-passing corpus test in simulation; S2 is clean;
- the WB DMA scenario passes on all four platforms with matching
  per-executor traces;
- a bench error in the WB DMA run names the action that provoked it.

**Docs:**
- `docs/simulation.rst`: running actions, seeds, reading the trace,
  attribution reports;
- be-bc `host-abi.md`: action entries, `stim_id`;
- a new tutorial page, "Your first scenario in simulation" (WB DMA), whose
  code blocks are quoted from the example and checked by `test_doc_examples`;
- `examples/fw_wb_dma_uvm/README.md`.

---

## F3. Randomization

Roadmap R1. dv-solve items come from F0.2's gap report and land in dv-solve
first, with dv-solve tests.

| ID | Item | Test | Done |
|---|---|---|---|
| F3.1 | ast2ir: `soft`, `dist`, `solve…before` into IR (remove the `untranslated` records for them) | IR-shape tests; constraint registry updated | [ ] |
| F3.2 | Soft priority (LRM ordering by declaration, extension, `with`, activity constraints) mapped to dv-solve ranks; dv-solve gains an explicit rank if needed | corpus tests over each priority source; calibrated: fails with ranks flattened | [ ] |
| F3.3 | `dist` into the blob; dist over rand variables decided against the LRM, then supported or refused | statistical corpus tests (chi-square with fixed seeds and a stated false-failure rate); calibrated against uniform | [ ] |
| F3.4 | `solve…before` to dv-solve `seq_pick` | distribution test (the classic `a -> b` skew); calibrated | [ ] |
| F3.5 | `randc` with a solver handle per object | cycle test over N draws; C engine refusal | [ ] |
| F3.6 | `unique`, `forall` constraints (if F0.4 finds them missing) | corpus tests | [ ] |
| F3.7 | Procedural `randomize x with {…}` (Syntax 111): new IR statement; `SOLVE` on a frame-relative base | corpus tests in `exec` and functions; unsat is a located run-time error | [ ] |
| F3.8 | Unsat reported by source: dv-solve contradiction core mapped back to PSS constraint locations | test: an unsat cone names both constraints' lines | [ ] |
| F3.9 | Random stability: adding an unrelated action leaves an existing traversal's values unchanged (within activity D9's limits) | stability test | [ ] |
| F3.10 | Soft-drop and solve explanation in the trace (`EV_SOLVE` lists dropped softs) | trace test; feeds the debugger's "explained randomization" | [ ] |

**Gate:**
- the new randomization corpus tests pass in all three lanes;
- S2 shows identical solved values in and out of simulation;
- `expected/bc.toml` stays empty.

**Docs:**
- be-bc `docs/spec/solve.md` (new): the solve service, soft ranks, the dist
  rule, randc state, the seed tree;
- pssc user doc "Randomization on bc" (supported forms, how to read an unsat
  report);
- dv-solve docs for each feature added there.

---

## F4. Functional coverage

Roadmap R2a and R2b, design in roadmap §4.6.

| ID | Item | Test | Done |
|---|---|---|---|
| F4.1 | ir-core `CovergroupDef` made pure data (expressions, no Python callables); retire `PssCoverGroup` | ir-core tests; the SV target still builds | [ ] |
| F4.2 | Corpus tests that pin the sampling points (action covergroup after `post_solve`; struct on randomize; explicit `sample()`); the checker reads a coverage export | the corpus checker's self-tests | [ ] |
| F4.3 | R2a: ast2ir for inline action covergroups: coverpoints, automatic and explicit value bins, crosses | IR-shape tests; registry rows | [ ] |
| F4.4 | R2a: lowering emits a sample function per covergroup type; `COV_SAMPLE` op; `COVG` section | be-bc unit tests; C engine refuses `COV_SAMPLE` | [ ] |
| F4.5 | R2a: the binning service in be-bc `cov`; UCIS covergroups in covsight next to code and register coverage | one `.cdb` holds all three kinds for one run; calibrated: sampling switched off fails | [ ] |
| F4.6 | R2a in simulation: coverage written from the simulator and merged with standalone | S1, S2 (samples identical), S3 | [ ] |
| F4.7 | WB DMA slice gets its covergroup over the channel configuration (completes the roadmap's first milestone) | S3: merged coverage across the four platforms | [ ] |
| F4.8 | R2b: covergroup types and instances, `iff`, ignore/illegal bins, ranges, `default`, options (`weight`, `goal`, `at_least`, `per_instance`) | corpus tests per form; illegal bin hit is a located run-time error | [ ] |
| F4.9 | R2b: struct covergroups and explicit `sample()` | corpus tests | [ ] |
| F4.10 | R2b: the SV target onto the same IR model | SV golden snapshots reviewed | [ ] |
| F4.11 | pssunit `check_covered("cg.cp.bin")`; `pssc cov merge` across seeds and platforms; `pssc cov gaps` | pssc tests | [ ] |

**Gate:**
- corpus coverage tests pass in all three lanes;
- one `.cdb` from a seed sweep that mixes standalone and simulation runs;
- the first milestone (WB DMA slice with coverage, four platforms) is done.

**Docs:**
- be-bc `docs/spec/coverage.md` (new): sample points, `COV_SAMPLE`, the
  `COVG` format, the service interface;
- pssc user doc "Functional coverage" (writing covergroups, reading `pssc cov`
  output, merging simulation and standalone);
- covsight notes for the new covergroup kinds.

---

## F5. Host ABI v2: concurrency, executors, events, observation

Roadmap R6b, plus checking K1, K2, K4 and attribution A3. This is the phase
that makes PSS scenarios behave like real stimulus in a bench.

| ID | Item | Test | Done |
|---|---|---|---|
| F5.1 | ABI v2 spec first: thread id per request, several outstanding requests, out-of-order completion, executor id, host events, the returned action object, time units in the image; v1 hosts never see a second request | spec review; ABI conformance tests over a scripted host | [ ] |
| F5.2 | pyvm: concurrent outstanding requests from `parallel` branches | unit test: two branches' accesses overlap on a scripted host; calibrated against v1 serialization | [ ] |
| F5.3 | SV runtime: one SV thread per outstanding request; routing by executor id to agents or sequencers | WB DMA: two channels' bus traffic overlaps in the waveform; S2 per-executor order unchanged | [ ] |
| F5.4 | Executors in full (Syntax 127–132): executor groups, executor claims and queries, the executor id carried on requests | corpus tests; routing test in S3 | [ ] |
| F5.5 | Host events: named event handles in the image; IRQ becomes one kind; `CHAN` notify implemented; PSS waits through a blocking import or a channel | S1 test with an event from the bench; standalone with an event from a mock | [ ] |
| F5.6 | Returned action object: the bench reads solved values after `call_start_obj` | sim test | [ ] |
| F5.7 | Time units: stated in the image, converted by the host | test across two timescales | [ ] |
| F5.8 | Checking K1: observation ports (C1 decision), `OBSERVE` section, `pssvm_observe`, generated SV functions plus a `uvm_subscriber`; standalone posting from mocks | S3: the WB DMA monitor feeds a PSS port; standalone the mock feeds the same port | [ ] |
| F5.9 | Checking K2: channel items carry the putter's stimulus id and `src_ref`; "caused by" in check failures and reports | test: a failing `check_eq` names the stimulus action | [ ] |
| F5.10 | Checking K4: observations in the replay transcript; `pssc test --recheck TRANSCRIPT` | re-check reproduces a simulation failure with no simulator; an edited checker is re-checked without re-simulating | [ ] |
| F5.11 | Attribution A3: ownership ledger from the trace; `@bench_key` and `@addr_region` | sim test: a scoreboard error keyed by channel is attributed to the action owning that channel | [ ] |

**Gate:**
- WB DMA runs channels concurrently on RTL, with matching per-executor
  traces against the fake and the Python model;
- a PSS checker fed by the bench's monitor catches an injected RTL fault and
  names the stimulus;
- the failure reproduces by re-check on a laptop.

**Docs:**
- be-bc `host-abi.md` v2, including the compatibility rules;
- `docs/spec/observe.md` (new);
- pssc "Checking in PSS" user guide: observation ports, expectations,
  `check_*`, re-check;
- `docs/simulation.rst`: hooking a UVM monitor, executor routing.

---

## F6. Data types and the value ABI

Roadmap R3, and the areas F0.4 marks open (floats, chandle, references, file
I/O). The heap is specified before it is built.

| ID | Item | Test | Done |
|---|---|---|---|
| F6.1 | Fixed-size data arrays with a run-time index (`LD/ST_FIELD_IX`, bounds checked); array methods (`size`, `sum`, `in`) | corpus `types.*`; an out-of-bounds index is a located error | [ ] |
| F6.2 | Constraint `foreach` and random array sizes through dv-solve arrays | corpus tests; distribution of sizes | [ ] |
| F6.3 | Strings: ordering, concatenation, formatting, slices, methods; strings passed to imports (string-table index plus a host lookup) | corpus tests; S1: a string reaches an SV import | [ ] |
| F6.4 | Struct parameters and results across `CALL` and `IMPORT` (by slot range, `value-abi.md`) | corpus tests; S1: a struct crosses into SV (DPI marshalling generated) | [ ] |
| F6.5 | Import arity: remove the 2-argument orchestration limit; retire pssc's `check_rewrite` workaround | corpus tests; pssunit suite | [ ] |
| F6.6 | Floating point and the float library (Syntax 10, 120–124) | corpus tests; S2 bit-identical | [ ] |
| F6.7 | chandle (opaque host handle, passed through imports) | S1 test with an SV `chandle` | [ ] |
| F6.8 | Reference types (Syntax 16) | corpus tests | [ ] |
| F6.9 | Heap spec in be-bc (per-run arena, handles in slots, size and iteration ops); C engine refusal | spec review | [ ] |
| F6.10 | `list`, `map`, `set` on the heap, procedural and in constraints (dv-solve: `size()`, `in`, uniqueness) | corpus tests; calibrated randomization of lists | [ ] |
| F6.11 | File I/O and error-reporting library (Syntax 114–119), with file operations as host services so they work in simulation | S1 tests | [ ] |
| F6.12 | Aggregate and triple-quoted literals in full | corpus tests | [ ] |
| F6.13 | Import class (Syntax 97): support or a located refusal (F0.1 decision) | test either way | [ ] |

**Gate:** corpus `types.*` and `proc.*` additions pass in all three lanes;
rt-eng refuses the heap ops up front.

**Docs:**
- be-bc `value-abi.md` (structs, strings, chandles across the ABI);
- `memory.md` / `heap.md` (new);
- `host-abi.md` (string and struct marshalling);
- the generated SV DPI declarations documented in `docs/simulation.rst`.

---

## F7. Memory: address spaces, claims, allocation

The LRM's address-space model (Syntax 134–159). This is central to simulation:
DMA descriptors and buffers live in memory the bench models, and claims are
what keep concurrent actions from colliding.

| ID | Item | Test | Done |
|---|---|---|---|
| F7.1 | Design note `docs/design/address-space-bc-design.md`: contiguous and transparent spaces; regions; claims as rand objects allocated by the solver (dv-solve over address ranges, non-overlap for concurrent claims, alignment, `allocation` modes) or by an allocator service; what the bench's memory model needs to know | review | [ ] |
| F7.2 | Address spaces and regions elaborated (static, in ir-core), and a `MEMMAP` image section | table tests | [ ] |
| F7.3 | Claims allocated at solve time; non-overlap across actions that may run concurrently; claim sharing between overlapping actions (21.11) | corpus tests; calibrated: overlap check switched off fails | [ ] |
| F7.4 | Handles: `make_handle_from_claim`/`_from_handle`, `addr_value*`, `get_tag` for non-transparent spaces | corpus tests | [ ] |
| F7.5 | `packed_s`, `sizeof_s`, packed-struct read and write (Syntax 145, 146, 158) | corpus tests; S1 bus trace byte-exact | [ ] |
| F7.6 | Memory in simulation: the bench's memory model sized and mapped from `MEMMAP`; backdoor preload and readback as host services | S3: WB DMA descriptors placed by claims, verified by RTL transfers | [ ] |
| F7.7 | Ownership ledger (A3) reads claims, so a memory-corruption check attributes to the action that owns the region | sim test | [ ] |

**Gate:** WB DMA transfers whose buffers are claimed in PSS pass on all four
platforms; concurrent transfers never overlap (checked by a sanitizer).

**Docs:** be-bc `memory.md` (allocation); pssc "Memory and buffers"
user guide.

---

## F8. Activities completeness (planner v0)

Roadmap R4, which is activity P2, P3 and P3c. The work items, tests and gates
are in `activity-flow-resource-bc-design.md` §8–§10. This plan adds only the
simulation rows and the remaining component and action features.

| ID | Item | Test | Done |
|---|---|---|---|
| F8.1 | Activity P2: static elaboration (pool-binding table, ICL tables, sample points, static cones) | per the activity design §9.2 | [ ] |
| F8.2 | Activity P3: planner v0 (no inference); ordered `schedule`; join specs SELECT/BRANCH; feasibility-filtered `select`; state tickets; the D2 primitives as opcodes; `PLAN` section | UC1, 2, 6–10 on the corpus | [ ] |
| F8.3 | Activity P3c: the corpus checker's P2/P3 (matcher, witness SMT, plan adjudication, cheat mutations) | checker self-tests; 100% mutation kill | [ ] |
| F8.4 | Activity `while`, `foreach`; handle arrays with a run-time index | corpus tests | [ ] |
| F8.5 | Streams: `RDV` pairing by the planner (explicit; inferred partners are F9) | UC5 (explicit part) | [ ] |
| F8.6 | Resource attributes and claim arrays (the claim table generalized from a 64-bit mask) | UC9 | [ ] |
| F8.7 | Scheduling constraints (Syntax 68) | corpus tests | [ ] |
| F8.8 | `override` (type and instance), abstract actions, `comp` ref instance qualifier, `mutable` (if F0.4 finds them missing) | corpus tests | [ ] |
| F8.9 | Timing independence in simulation: the UC16 timing fuzz rerun with real simulator timing (random bus latencies in `pssvm_tb`) | S1 under randomized latency; S2 clean | [ ] |
| F8.10 | Attribution A5: delta-debugging reduction over the plan in server mode | a planted fault reduced to a minimal scenario | [ ] |

**Gate:** the activity design's P3 gate, plus the UC tests in lane S1 under
randomized bus latency.

**Docs:**
- be-bc `docs/spec/plan.md` (new): the `PLAN` format, the primitives;
- pssc user doc "Activities on bc" (scheduling semantics, what is planned,
  what is arbitrated).

---

## F9. Inference

Roadmap R5, which is activity P4 and P4b.

| ID | Item | Test | Done |
|---|---|---|---|
| F9.1 | Buffer, state and stream inference; chains; depth limits (D5); `atomic` exclusion; nogoods from dv-solve contradiction cores | UC3, 5, 12 | [ ] |
| F9.2 | The enumeration oracle on the tiny-model suite (dv-solve enumeration and counting) | soundness and reachability green | [ ] |
| F9.3 | Fault-injection kill matrix | every fault killed | [ ] |
| F9.4 | `prev` and `uid`, fixed at source in pssparser (P4b) | `flow.state.prev.001`, Ex 170 | [ ] |
| F9.5 | Inferred actions in simulation: the trace names inferred nodes; the debugger and attribution show them as such | S1 on the inference corpus; S3: WB DMA setup actions inferred from a transfer | [ ] |
| F9.6 | Harvest be-py's flow and resource models into the corpus; then decide on retiring the frozen runtimes (S9) | corpus | [ ] |

**Gate:** the activity design's P4 gate, plus lane S1 on the inference
corpus.

**Docs:** be-bc `plan.md` (inference); pssc user doc "Inference" (what gets
inferred, the limits, reading why something was inferred).

---

## F10. Checking in PSS, attribution and debug in simulation

Checking K3, K5, K6; attribution A4 and A6; the debugger's simulation phase.
This needs the DAP adapter (`bc-debugger-plan.md` P2), which runs on its own
track and is tracked in that file.

| ID | Item | Test | Done |
|---|---|---|---|
| F10.1 | Checking K3: `@on_observe` reactive checks; scoreboard actions run beside the scenario | WB DMA's copy check ported from `wb_dma_scoreboard` | [ ] |
| F10.2 | Checking K6: the mock as reference model in simulation (generalizes the hif shim's shadow fake) | S3: mock prediction against RTL | [ ] |
| F10.3 | Checker calibration in pssunit: checkers tested against a correct mock and faulty mocks (dropped write, wrong address) | mutation kill table for the WB DMA checkers | [ ] |
| F10.4 | Debugger P10 (simulation): the DAP adapter attached to pyvm under the simulator, with the simulation held by `dbg_wait` | sim-attached DAP test | [ ] |
| F10.5 | Checking K5: "caused by" frames; stop on a failed check; waveform cursor link | DAP test: a stop on `check_eq` shows the stimulus frame | [ ] |
| F10.6 | Attribution A4: "Bench failures" exception stop; `--focus` | DAP test | [ ] |
| F10.7 | Attribution A6: fault localization (Ochiai) over UCIS across a regression | test on a seeded regression with a planted fault | [ ] |
| F10.8 | The checking demo end to end (`pss-checking-design.md` §6): injected channel-2 bug → stop at the PSS check with `xfer#5` as cause → the same stop from replay → checker fix verified by re-check | scripted demo test (sim marker) | [ ] |

**Gate:** the demo in F10.8 runs as a test.

**Docs:**
- pssc "Debugging scenarios in simulation" (VS Code setup, stops, replay,
  re-check);
- "Checking in PSS" completed;
- `docs/simulation.rst`: attribution reports.

---

## F11. Monitors and cover statements

Checking K7; LRM Syntax 74–84.

| ID | Item | Test | Done |
|---|---|---|---|
| F11.1 | ast2ir: `monitor` declarations, monitor activities (sequence, concat, eventually, overlap, select, schedule), monitor constraints, `cover` statements | IR-shape tests; registry rows | [ ] |
| F11.2 | Design note: monitor matching on bc, over the stream of executed actions and observed transactions (a runtime service, as coverage is) | review | [ ] |
| F11.3 | Monitor matching service; `cover` results into UCIS | corpus tests (new: monitor outcomes); calibrated | [ ] |
| F11.4 | Monitors over observations (K1 ports) in simulation | S3: a behavioural cover over observed DMA transfers | [ ] |
| F11.5 | `require` (C2 decision: pssc extension, or not) | tests if accepted | [ ] |

**Gate:** monitor corpus tests in all three lanes; behavioural coverage of the
DUT in the WB DMA `.cdb`.

**Docs:** be-bc `docs/spec/monitor.md` (new); pssc "Monitors and behavioural
coverage".

---

## F12. Simulation breadth and regression flow

Roadmap R6c, plus the iteration features from the vision paper (§2) that make
simulation use productive.

| ID | Item | Test | Done |
|---|---|---|---|
| F12.1 | Server mode: boot the simulator once, then run many tests and seeds against one elaborated bench (a checkpoint after reset where the simulator supports one) | throughput measured against one process per test | [ ] |
| F12.2 | dv-flow tasks: build bench, run scenario, sweep seeds, merge coverage (`src/pssc/dvflow/flow.yaml` and be-bc's own) | flow tests | [ ] |
| F12.3 | One commercial simulator smoke run (SV runtime and DPI host) | manual, recorded in this file | [ ] |
| F12.4 | cocotb host spike (S6): pyvm platform requests as cocotb coroutines; no DPI | the WB DMA slice under cocotb + Verilator | [ ] |
| F12.5 | Seed sweeps with failure triage: each failure gets a replay line, attribution and a reduced scenario | regression test on a planted fault | [ ] |
| F12.6 | Performance in simulation: budgets for requests per second and solves per second under the simulator; solve-cache hit rates (S10) | `tests/perf` sim rows | [ ] |

**Gate:** a WB DMA seed sweep in server mode, merged coverage, triaged
failures; a commercial simulator smoke run recorded.

**Docs:** "Running regressions" user guide; dv-flow task reference.

---

## F13. OTF and live arbitration

Activity P5, P5b and P6. Built when a live platform needs it (a real-silicon
or emulation target, or a bench whose responses decide structure). The work
items and gates are in the activity design. This plan adds only one row: a
cross-mode test in lane S1 (same seed, global vs OTF, same verdict).

| ID | Item | Done |
|---|---|---|
| F13.1 | Activity P5 (OTF, planned arbitration) | [ ] |
| F13.2 | Activity P5b (hoisting in sv-pure) | [ ] |
| F13.3 | Activity P6 (live arbitration) | [ ] |
| F13.4 | Cross-mode test in lane S1 | [ ] |

---

## F14. C engine alignment

Roadmap R8 and activity P8; debugger P3/P4 on C.

| ID | Item | Test | Done |
|---|---|---|---|
| F14.1 | A conformance suite over both engines: every be-bc spec test parametrized by engine | pyvm green; rt-eng green or refusing up front | [ ] |
| F14.2 | Port order: activations, component ops, flow and resource primitives, `COV_SAMPLE`, soft/dist blobs, ABI v2, heap last; the planner stays a host service | the differential grows per step | [ ] |
| F14.3 | rt-eng as an alternative engine in simulation (the DPI host) | lanes S1/S2 parametrized by engine | [ ] |
| F14.4 | rt-eng differential against pyvm on the corpus and the L4 suite | gate | [ ] |

**Docs:** rt-eng conformance status page; `docs/simulation.rst`, choosing an
engine.

---

## 4. Order and parallel tracks

```
F0 ─► F1 ─► F2 ─┬─► F3 ─► F6 ─► F7 ──────────────┐
                ├─► F4 ───────────────► F11      ├─► F12
                ├─► F5 ─────► F10 ◄── debugger P2 │
                └─► F8 ─► F9 ─────────────────────┴─► F13 ─► F14
```

- F3, F4, F5 and F8 can run in parallel after F2; they touch different
  layers (solve, coverage, ABI, planner).
- F6 waits for F3 (constraint `foreach` and collections in constraints need
  the solve plumbing).
- F7 waits for F6's value ABI and F3's solve work.
- F10 waits for F5 (observation, provenance) and the debugger's P2 adapter.
- F11 waits for F4 (coverage database) and F5 (observation ports).
- F12 can start any time after F2 for server mode and dv-flow, and closes
  after F10.
- The debugger plan (`bc-debugger-plan.md`) runs as its own track. Its P1 is
  F1b here; do it once.

**Milestones:**
1. **M1 (end of F2):** an action scenario runs in Verilator with seeds,
   trace and attribution; the corpus runs in simulation.
2. **M2 (end of F4):** the roadmap's first vertical slice, WB DMA with
   randomization and coverage on four platforms.
3. **M3 (end of F5):** concurrent scenarios and PSS checking on RTL, with
   re-check.
4. **M4 (end of F9):** full activity semantics, including inference, in
   simulation.
5. **M5 (end of F10 and F11):** the checking and debugging story of the
   vision paper, demonstrated.
6. **M6 (end of F12, F14):** regression-ready on two engines and more than one
   simulator.

---

## 5. Risks tracked here

| Risk | Watch | Mitigation |
|---|---|---|
| pyvm too slow under a simulator | F0.3 numbers; F12.6 budgets | solve-cache reuse, server mode, F14 |
| dv-solve features late | F0.2 report; each dv-solve item scheduled one phase ahead | a spike in F0; a located refusal rather than a workaround |
| Planner (F8/F9) schedule | the activity design's own gates | the milestones up to M3 do not wait on it |
| Lane S1 flakiness from simulator timing | S2 diffs that differ only by timing | the trace projection is timing-independent by construction; fuzz the latencies in F8.9 on purpose |
| Corpus authoring is the bottleneck | F0.5 list per phase | tests written at the start of each phase, before implementation |
| Code moves in F1 break pssc | the suite and the `.cdb` stay byte-identical | moves land alone, with nothing else in the same change |
