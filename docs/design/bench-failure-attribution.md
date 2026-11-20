# Bench failures back to PSS stimulus: design for review

**Status:** draft, 2026-11-20. Follows from
`pss-as-test-language-vision.md` §4, and feeds `full-pss-on-bc-roadmap.md` R6.

> **Companion:** `pss-checking-design.md` moves checks into PSS, where
> attribution is by construction. This document covers the checkers that
> stay in SV (existing benches, protocol and SVA), where attribution is by
> evidence.

## 0. Problem

In IP-level simulation the PSS test drives, but the **bench checks**:
- UVM scoreboards and comparators;
- SVA and protocol monitors;
- `$error` in models;
- end-of-test count checks.

A bench check that fails is outside PSS's view:
- the PSS test still "passes";
- its report says nothing;
- the engineer reconstructs by hand which action, which solved values and
  which PSS line provoked the failure.

`examples/fw_wb_dma_uvm` is a typical case. `wb_dma_scoreboard` reports
`copy word 3: dst[0x00004010]=… != src[…]` and `completion order[1] = ch2,
expected ch3`, and neither message names any PSS stimulus.

**Goal:**
- every bench failure is **captured** by the PSS runtime;
- it is **attributed** to the stimulus that most likely provoked it, with
  evidence;
- it **affects** the PSS verdict and the debugger;
- it is **reproducible and reducible** from the seed.

All of this is engine-side and bench-agnostic, and it costs the bench at most
a few lines.

## 1. Why attribution is hard

| Difficulty | Example |
|---|---|
| **Invisible:** the failure happens in SV code that PSS never calls | a `uvm_error` in a scoreboard |
| **Delayed:** detection happens long after the cause | a comparison at the end of a transfer, or end-of-test counts |
| **Concurrent:** several actions are in flight | four channels programmed in `parallel` |
| **Indirect:** the DUT, not the test, generated the failing traffic | DMA memory writes; the test only wrote registers |
| **Aggregated:** no single transaction is at fault | "done-interrupt count 3 != 4" |

No single mechanism covers all five. The design therefore combines several
**kinds of evidence** into a ranked list of suspects, and says which evidence
supports each one (§3).

## 2. Capture: bench failures become engine events

### 2.1 Sources

The SV runtime package (`pssvm_pkg`) provides the adapters. A bench enables
those it needs.

| Source | Adapter | Notes |
|---|---|---|
| UVM reports (`uvm_error`, `uvm_fatal`, `uvm_warning` if configured) | `pssvm_report_catcher`, a `uvm_report_catcher` that the runtime installs | Passes the report through unchanged; forwards severity, id, message, component path, file:line and time. Filters by id or severity. Catches every existing scoreboard with no edit. |
| SVA failures | (a) standard VPI assertion callbacks (IEEE 1800 ch. 39, `cbAssertionFailure`) in a small VPI library; (b) a fallback macro for action blocks, `` `PSSVM_ASSERT_FAIL(msg) `` | (a) needs no source change where the simulator supports it; Verilator's support is limited, so (b) is the portable path |
| Direct `$error`/`$fatal` | the macro; or, offline, a log scrape that matches timestamps (§3.6) | there is no portable live hook |
| Structured reports | `pssvm_bench_fail(string kind, string keys[string], string msg)` | the precise path: a scoreboard names its **context keys** (address, channel, register, transaction tag; §3.3, §3.4) |
| Simulator death (timeout, `$finish` mid-test, X-prop fatal) | the runtime's end-of-simulation hook | recorded as "test did not complete" with the in-flight snapshot |

### 2.2 ABI and trace

- **New host-to-engine notification** (be-bc `host-abi.md`):
  `pssvm_report(h, kind, t_fs, json)`. It needs no response and may be
  called at any time, including from a thread that is not running a call.
- **The engine records an `EV_BENCH` trace event** (be-bc `trace-schema.md`)
  carrying:
  - the report;
  - the simulation time;
  - an **in-flight snapshot**: every live thread, its activation node path,
    the action instance and the current `src_ref`.

  The engine already knows all of this at the time, so the snapshot is
  cheap and exact.
- These live in Zuspec (be-bc and the SV runtime package), not in pssc. A
  non-PSS front end on bc gets the same thing.

## 3. Attribute: evidence, combined into ranked suspects

### 3.1 Stable stimulus ids

Every action instance gets an id that is **stable for a given (image, seed)**:
- its activation node path (`top.xfers[3].cfg`);
- plus an occurrence count for loops (`#17`).

Operation calls in today's function-only sim path get `fn#n`. A rerun or a
replay of the same seed produces the same ids, so "dma_xfer#17" means the
same thing in the regression log, in the rerun and in the debugger.

The id goes into every trace event and **every host request** (§3.3).

### 3.2 E1: Temporal

Suspects are actions in flight at the failure time, plus actions that
completed within a window before it. The window is configurable per report
id, defaulting to the action's duration. Temporal evidence is always
available, but weak when much runs in parallel.

### 3.3 E2: Causal tags

- Each request (`pssvm_req`) carries `stim_id` beside the `src_ref` it
  already has.
- The bench's platform tasks receive it, and can stamp it on the sequence
  item or transaction they create.
- Monitors and scoreboards that later report on that transaction include the
  tag:
  - through `pssvm_bench_fail(keys{"stim": id})`;
  - or, with no API change, by putting a `pss#<id>` token in the
    `uvm_error` message, which the catcher parses.

This is exact for traffic the test issued directly (register accesses and
transactions through blocking imports). It does nothing for traffic the DUT
generates, which E3 covers.

### 3.4 E3: Ownership

**The key fact:** PSS already models *who owns what*, and the engine records
it. An **ownership ledger** is built from the trace:

| Key | Owner | Source |
|---|---|---|
| register or field | the action, and `src_ref`, that last wrote it before the failure | ACCESS events (they carry `reg_id` and `src_ref` today) |
| resource instance (`chan[2]`) | the action holding a claim at the time | claim and release events (activation hooks) |
| address range | the action whose attribute or buffer defines the region | flow-object and buffer attributes; `addr_claim` once supported; or any attribute annotated as an address range (below) |
| executor | the action running on it | executor id on requests (roadmap R6b) |
| interrupt line or host event | the action waiting on it | wait events |

**The binding.** A failure states its keys: the scoreboard's `dst[0x4010]`
is an address and `ch2` is a channel. A small **key binding** maps bench key
names to PSS concepts. It is declared in PSS, so it ships with the model and
not with each bench:

```pss
component dma_c {
    @bench_key("ch")   // a bench report "ch=2" means resource chan[2]
    pool [4] chan_r chans;
}
action xfer_a {
    @addr_region(.size="len")   // [src, src+len) is owned by this action
    rand bit[32] src;
    @addr_region(.size="len")
    rand bit[32] dst;
    rand bit[16] len;
}
```

The annotations are pssc front-end syntax. They lower to image metadata that
the attribution service reads. Ownership is the evidence that solves the
*indirect* case: the DMA's write to `0x4010` is owned by the `xfer` whose
`dst` region contains it.

### 3.5 E4: Exported intent

When the bench receives expected transactions straight from the test
(vision §4.1: the solved action object returned to SV), a mismatch arrives
already tagged. This is the strongest evidence, and costs one line in the
scoreboard.

### 3.6 Combining

The output of attribution is an **attribution record**:
- the failure;
- ranked suspects;
- for each suspect:
  - its id;
  - its PSS path;
  - the source line of the traversal and of the access involved;
  - its solved attribute values;
  - the evidence that supports it.

Evidence ranks by strength: E4/E2 (tag), then E3 (ownership), then E1
(temporal). Agreement between kinds raises a suspect's rank.

The **aggregated** case ("count 3 != 4") gets an attribution by
*omission*: the actions whose expected effect is missing (for example, the
`xfer` whose completion was never seen). This is reported as such, not
guessed.

Attribution runs in two places over the same data:
- **online:** in the runtime and the DAP adapter, for verdicts and debugger
  stops;
- **offline:** a tool over trace plus failure events (and a simulator log, for
  the scrape fallback), for regressions.

One implementation, a Zuspec library, serves both.

## 4. Act: verdicts, reports, debugger

**Policies**, set per report kind or id:
- **record** (the default): attribute and continue;
- **fail**: the PSS test's verdict fails;
- **stop**: hold for the debugger;
- **abort**: end the activation cleanly, running `run_end`, then finish the
  test.

**Verdict.** A PSS test passes only if its own checks pass *and* no bench
failure lands in its window. CTRF and `pssc test` output list each bench
failure under its attributed action, with the replay line (seed, image hash,
`--focus` id).

**Debugger.** A bench failure is a DAP stop with reason `exception`, under an
exception-filter checkbox "Bench failures" (`bc-debugger-plan.md`'s DAP
mapping):
- the stopped thread is the top suspect's thread;
- the exception text gives the report and the evidence.

The simulation is held through the existing `dbg_wait(why)` hold, so the
engineer looks at the PSS state and the waveform at the same instant.

## 5. Reproduce and reduce

- **Rerun:** the same image and seed reproduce the stimulus exactly. Server
  mode makes a rerun cheap: no recompile, and boot once (vision §2).
- **Focus:**
  - `--focus dma_xfer#17` reruns with a breakpoint at the suspect's traversal;
  - `--trace-from` starts detailed tracing a window before it, so the run
    goes at full speed until the interesting part.
- **Scenario reduction (delta debugging).** Because the scenario is
  declarative and the plan is data, the tool can try smaller scenarios:
  - drop actions outside the suspect set;
  - pin each suspect's solved values;
  - let the planner keep the result legal.

  Each candidate is one simulation run in server mode, and the result is a
  **minimal failing scenario**. When removing other traffic makes the
  failure go away, that is itself the finding: an interaction bug, together
  with the actions involved. SV tests cannot be reduced this way, because
  their stimulus is code, not a constrained structure.

## 6. Regression-level localization

Across many seeds, the question changes from "what caused this failure" to
"what do the failing runs have in common". Use spectrum-based fault
localization (for example Ochiai) over **scenario features**:
- action types;
- bins of solved attributes;
- covergroup bins;
- pairs of concurrent actions.

The per-test hit data is already in covsight's UCIS database (test-to-
coveritem associations). The output reads like "fails when `ch2` and `ch3`
run concurrently with `burst=16`".

This needs nothing from the bench beyond §2, and it improves as functional
coverage (roadmap R2) grows.

## 7. Where it lives

| Piece | Home |
|---|---|
| `pssvm_report` ABI, `EV_BENCH`, `stim_id` on requests, stable ids | be-bc spec, pyvm, and rt-eng later |
| Report catcher, VPI assertion library, macros, `pssvm_bench_fail` | the SV runtime package (be-bc `share/sv`, roadmap S4) |
| Ownership ledger, attribution library, reduction driver | Zuspec (be-bc run and analysis layer) |
| `@bench_key`, `@addr_region` syntax; `pssc test` verdicts and reports; a `pssc triage` command | pssc |

## 8. Phasing

| Step | Content | Useful with today's function-only sim path? |
|---|---|---|
| A1 Capture | catcher, `pssvm_bench_fail`, `pssvm_report`, `EV_BENCH` with an in-flight snapshot, the verdict rule, reporting | yes: attributes to operation calls |
| A2 Ids and tags | stable ids; `stim_id` on requests and in `pssvm_platform_if`; `pss#id` token parsing; temporal ranking | yes |
| A3 Ownership | ledger from the trace; `@bench_key` / `@addr_region`; attribution records | registers yes; resources and regions with action entries (R6a) |
| A4 Debugger | the exception-filter stop; simulation hold; `--focus` | after the DAP adapter (`bc-debugger-plan.md` P2) |
| A5 Reduction | delta debugging over the plan in server mode | needs action scenarios (R6a), and global planning (R4) for the planner-legal version |
| A6 Localization | features over UCIS across a regression | grows with R2 |

**Demo:** on `fw_wb_dma_uvm`, inject an RTL bug that corrupts channel 2's
copy. The scoreboard's unchanged `uvm_error` (`dst[0x4010] …`) is reported
by `pssc test` as "bench failure, attributed to `xfer#5` (ch 2,
dst=0x4000..0x40ff, line 88): ownership of address 0x4010, plus temporal".
The debugger stops there with the simulation held. Covers A1–A3, plus A4
when ready.

## 9. Questions for review

1. Is the key binding (`@bench_key`, `@addr_region`) right in PSS, where it
   ships with the model, or should it be a table in the bench, owned by the
   bench writer? PSS is recommended, with an SV-side override.
2. The default policy: **fail** for `uvm_error`/`uvm_fatal` and SVA,
   **record** for warnings?
3. Stopping on a bench failure: should all PSS threads stop, or only the
   suspect's? Simulation time is held either way.
4. VPI assertion capture: worth building now, or the macro only until a
   commercial simulator is in the loop?
5. Should `stim_id` be visible to PSS-unaware VIP through a standard field
   (for example, a UVM sequence-item extension the runtime package
   provides)?
