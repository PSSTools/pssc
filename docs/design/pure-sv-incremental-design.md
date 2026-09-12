# Pure-SystemVerilog PSS Lowering — Incremental-Traversal Design

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

*Detailed design for review.*

Status: draft for review. Date: 2026-06-26.
Supersedes the solve-model sections of
[`pure-sv-lowering-design.md`](pure-sv-lowering-design.md) (which framed a single
monolithic solve); this document makes **incremental traversal** the backbone and
demotes joint solving to a localized escape hatch.

---

## 1. Objective

Lower a useful subset of Accellera PSS v3.0 to **pure SystemVerilog**: emit only
`.sv` files that compile and run on a standard simulator, using SV's native
constrained-random solver (`randomize()`/`constraint`) for *all* value solving,
with **no DPI and no external `dv-solve`**. Where PSS semantics exceed what SV's
solver expresses on a fixed object graph, supply **SystemVerilog runtime
machinery** (an extended `zsp_rt_pkg`) plus thin generated glue.

The non-pure DPI/`dv-solve` path (`analyze_activity.py` → base64 `SolveProblem`
→ `zsp_dpi_*`) remains available as an **opt-in fallback** for problems that
exceed practical SV-solver capacity; it is never the default in this target.

---

## 2. The governing principle

> **A structural fact — a flow-object binding, a resource instance, an
> execution order, a replica count — is encoded in generated control flow when
> it is statically known, and arbitrated by a SystemVerilog runtime construct
> when it is not. Either way the solver only ever assigns *values*, and sees
> every structural fact as a pinned input.**

Two corollaries drive the whole design:

1. **A resolution pass runs first** (§5) and classifies each structural fact as
   *static* or *runtime*. Maximizing the static set is the primary lever for
   solver reliability and runtime performance.
2. **Even runtime-arbitrated facts have statically-bounded domains.** PSS pool
   binding is elaboration-time, so the *pool* — hence the *type* and the *finite
   candidate set* — of every flow object, resource, and `ref` is always known at
   compile time. Only *which instance* / *which peer* / *how many* may be
   dynamic.

---

## 3. Execution model: incremental traversal

### 3.1 The per-traversal recipe

Every action traversal is lowered to the same staged sequence, which mirrors PSS
execution semantics (`pre_solve` → solve → `post_solve` → body) and the existing
`zsp_action` lifecycle:

```
traverse(action a, component ctx, inputs, claimed_resources):
  1. a = new()                          // create
  2. a.comp = ctx                       // component context
  3. bind a.<input fields> = inputs     // pinned input flow objects
  4. a.<resource fields> = claimed_resources
  5. a.pre_solve()                      // PSS exec pre_solve (sets non-rand state)
  6. a.randomize() with {               // SV-native value solve
        <forwarded downstream input constraints>;   // §6.3 static forwarding
        <pinned instance ids / input values>;       // §7, §8
     }
  7. a.post_solve()                     // PSS exec post_solve
  8a. atomic:   a.body()
  8b. compound: <lowered activity>      // recurses into sub-traversals
  9. release claimed_resources          // on body/activity return (lock lifetime)
```

Steps 1–4 establish structural facts (already resolved as static or runtime by
§5). Step 6 is the only solve. Step 8 may recurse (compound actions, inference,
`replicate`).

### 3.2 Why incremental is the backbone

- **Activities & recursion** are nested `traverse()` calls — no whole-scenario
  flattening.
- **Runtime values** (`exec body`/`post_solve` results consumed by a later
  sub-traversal's constraints) work naturally: later traversals solve *after*
  earlier bodies run. A monolithic solve cannot express this ordering.
- **Inference** is demand-driven: an unbound input triggers a producer
  `traverse()` first (§6.4).
- **Random cardinality** (`replicate (i:N)` with rand `N`) is a solved count
  feeding a runtime create-loop (§9) — no fixed pre-elaborated slot pool.

### 3.3 The cost incremental imposes, and how we recover it

Solving each action when it is created is **greedy** (producer before consumer),
which reintroduces the back-propagation gap named in `classify_constraints.py`.
The gap is narrower than it looks; §6.3 (static constraint forwarding) and §10
(solve-groups) recover almost all of it without DPI.

---

## 4. Architecture / where this lives

New/changed passes under `src/pssc/targets/sv/`:

| Concern | Existing file | Change |
|---|---|---|
| Constraint classification | `classify_constraints.py` | reused; drives forwarding vs solve-group instead of DPI |
| Flow analysis | `analyze_flow.py` | reused; feeds resolution pass |
| Activity analysis | `analyze_activity.py` | DPI chain compilation becomes opt-in; emit traversal plan |
| **Resolution pass** | *new* `resolve_binding.py` | classify every edge static/runtime (§5) |
| Inference | `lower_inference.py` | `COMPLEX` tier no longer routes to DPI; uses runtime selection over bounded domain |
| Flow objects | `lower_flow_objects.py` | add ready-event / channel runtime forms |
| Resources | `lower_resources.py`, `lower_head_solve.py` | claim-from-pool + claim-all-at-stage |
| Schedule | `lower_schedule.py`, `lower_activities.py` | static fork/join vs runtime dataflow sync |
| **Solve-groups** | *new* `lower_solve_group.py` | localized joint `randomize()` wrappers (§10) |
| Runtime library | `share/sv/zsp_rt_pkg.sv` | extensions in §11 |

A new target id (suggested `sv-pure`, or `pure_sv: true` on the existing
`sv-native` dvflow task) selects this path and enables the Tier-3 diagnostics of
§12.

---

## 5. The binding/scheduling resolution pass

Runs after `analyze_flow`/`analyze_activity`, before lowering. For each
structural fact it emits a `Resolution { kind, static: bool, domain }`.

| Fact | Statically resolvable? | Lowering when static | Lowering when runtime |
|---|---|---|---|
| Pool membership (flow/resource/`ref`) | **Always** (component tree + `bind`) | compile-time fact; bounds type & candidate set | — (never runtime) |
| Flow edge, single candidate (ICL=1) | Yes | direct wire / inline producer traversal | — |
| Flow edge, multi-candidate inference | No (peer dynamic) | — | runtime selection over the *known* candidate set |
| Resource instance within a pool | No (which instance) | — | runtime claim from known pool |
| `ref` to component/object | **Always** (hierarchy) | static handle | — |
| Execution order | If derivable from construct + static edges | fixed control flow (§8) | dataflow sync (§8) |
| Replica count `N` | If literal/const | unroll | solve `N`, loop-create (§9) |

**Key outputs:**
- A set of *static wires* (producer field → consumer field, emitted as direct
  assignment/injection).
- A set of *runtime edges*, each annotated with its statically-bounded domain
  (candidate type ids, pool handle, max cardinality).
- A *schedule plan* per activity scope: static stage assignment where possible,
  else "fork all + sync."

This pass is where "how much can be static" (the design's main efficiency lever)
is actually decided.

---

## 6. Flow objects

### 6.1 Buffer

Value-passing, producer-completes-before-consumer-reads.

- **Static (single producer, known order):** emit producer body, then assign the
  buffer value into the consumer's input field, then solve the consumer
  (today's `lower_flow_objects.py` behavior).
- **Runtime (producer inferred, or in a concurrent branch):** the buffer becomes
  a `{ value, ready-event }`; producer triggers the event after producing,
  consumer `wait`s before step 5–6 of the recipe.

### 6.2 Stream

Concurrent producer/consumer with in-time interleaving → always the runtime
form: `zsp_stream_channel #(T)` mailbox (`put`/`get` already block). Stream
chains run as forked processes; ordering emerges from `get()` blocking.

### 6.3 Static constraint forwarding (the back-propagation recovery)

The compiler already knows (via `classify_constraints.py`) what constraints a
downstream consumer places on each input flow object. When lowering the
**producer's** `randomize()`, it injects those as `with { ... }`:

| Consumer input constraint | Class | Recovery |
|---|---|---|
| `in.x == out.y` (coupled to producer's free output) | `FLOW_PROP` | producer solves freely; value injected; consumer couples. ✅ today |
| `in.x == 5` / `== f(non-rand)` (value-determinable) | `DPI_REQUIRED` | **forward** `out.x == 5` onto the producer's `randomize() with`. ✅ native |
| `in.x == k` where `k` is the consumer's own **rand** field | `DPI_REQUIRED` | chicken-and-egg → **solve-group** (§10). ❌ greedy |

This converts most current `DPI_REQUIRED` cases to native SV inside the
incremental model.

### 6.4 Inference

Demand-driven: an unbound input triggers a producer traversal *before* the
consumer, selecting the producer type from the resolution pass's
statically-bounded candidate set (`$urandom`-based selector for small sets, as in
`lower_inference.py`; a `randomize`d index with `dist` for weighted/larger sets).
The `COMPLEX` tier (>3 candidates / depth>1) **no longer routes to DPI** — it
recurses with a generation-time **inference depth bound** (§12, §13).

---

## 7. Resources

Resource `lock`/`share` is a **runtime-arbitrated** fact, served by
`zsp_resource_pool #(T)` (already in `zsp_rt_pkg.sv`). The claimed instance is a
pinned input to the action's solve.

Three cases by difficulty:

1. **Needs any free instance** — `pool.lock()` at runtime; no solver
   involvement.
2. **Constrains its own `instance_id`** (`r.instance_id inside {0,1}`) — derive
   the allowed mask (static or tiny pre-solve), `pool.lock(mask)`, then pin
   `with { r.instance_id == <claimed> }`. *Requires a small API extension: an
   allowed-set argument to `lock`/`share`.*
3. **Cross-action instance constraints** (`a.r.instance_id != b.r.instance_id`)
   — the resource analogue of §6.3's coupled case → **solve-group** (§10).

**Lifetime:** claim before `body()`, release on return (scope-based), matching
PSS lock duration.

**Allocation quality & deadlock:** greedy per-traversal claiming can miss a
feasible allocation and surfaces over-subscription as a runtime *hang* rather
than a clean solve failure. Mitigations, in order:
- **Claim in canonical order** (by schedule stage / fixed slot order) →
  deterministic greedy.
- **Claim-all-at-stage-start:** at a `parallel`/`schedule` stage the concurrent
  claim set is known → `unique`-allocate all at once (what `lower_head_solve.py`
  already reaches for) — solver-quality allocation, locally.
- **`try_lock` + bounded retry + timeout** → converts a deadlock into a
  diagnosable error.

### 7.1 Static resource-risk analysis (up-front detection)

Because pool sizes, `lock`/`share` modes, pool membership (elaboration-time),
and — where the schedule is static — the concurrency structure are all known at
compile time, most resource risk is detectable *before* runtime. The analysis
builds two static models per pool and emits a three-valued verdict.

**Model 1 — claim-interval overlap (capacity).** Each claim has a *live
interval* = from claim to release, ordered by the schedule; claims on the same
pool whose intervals overlap compete. This is interval-graph reasoning:
- pure `lock`: each concurrent locker needs a distinct instance ⇒ feasible iff
  **max overlap (= max clique, exact for interval graphs) ≤ size(P)**.
- `share`: an instance is either locked-by-one or shared-by-many; lock intervals
  must be mutually exclusive and must exclude overlapping share intervals.

**Model 2 — wait-for / ordering graph (deadlock).** Nodes = pools + flow
channels. Edges: (a) action holds R1 while acquiring R2 ⇒ `R1→R2`; (b) action
holds R across a blocking flow-wait on a producer that itself needs R ⇒
`R→(producer's claim)`. **Any cycle ⇒ deadlock risk.**

**Three-valued verdict per pool/scope**, using worst- and best-case bounds on
the concurrent claim set (tight when cardinality and inference depth are
bounded, §11/§12):
- worst-case overlap ≤ capacity ⇒ **SAFE** — emit cheap unconditional `lock()`.
- best-case overlap > capacity ⇒ **INFEASIBLE** — compile error (resource demand
  can never be met).
- otherwise ⇒ **AT-RISK** (depends on solved counts / inference) — emit the
  defensive `try_lock`+timeout path, warn, and report the bound.

The concurrent set is *over*-approximated, so **SAFE is sound** (never a false
SAFE); imprecision only yields false AT-RISK/INFEASIBLE, which is acceptable.

**Risks eliminated by construction (not merely detected):**
- **Lock-order cycles** → impose a canonical claim order (sort by pool id);
  Model-2 multi-claim cycles vanish.
- **Greedy ordering-dependent failure at a static stage** → claim-all-at-stage:
  one `unique` randomize over the statically-known concurrent claim set
  (generalizes `lower_head_solve.py`). Feasible exactly when Model-1 says SAFE.
- **Held-during-wait** → narrow the claim scope (claim as late, release as early
  as the dependency graph allows); Model-2 hold-across-wait edges drop out.

**Static limits (fall to AT-RISK):** data-dependent conditional claims, inference
whose depth/breadth isn't tightly bounded, random `replicate N` whose max exceeds
capacity, and pools shared across many components where the concurrent set is
hard to bound precisely.

Net effect: §7's runtime hazards become a compile-time **SAFE / INFEASIBLE /
AT-RISK** verdict, with by-construction elimination for static-schedule cases and
the runtime safety net reserved only for the genuinely runtime-dependent residue.

---

## 8. Scheduling

The schedule is a partial order from two sources: the activity construct and the
flow/resource dependencies. Two orthogonal axes:

1. **Concurrency** (from the construct): `parallel`/`schedule`/stream ⇒
   `fork ... join`; `sequence` ⇒ straight-line.
2. **Order determinacy** (resolution pass): are the edges known at compile time?

| | Order static | Order runtime |
|---|---|---|
| **Sequence** | straight-line `body()` calls | rare; straight-line with runtime branch |
| **Parallel/Schedule** | staged `fork/join` (topological, as today) | fork all; **consumer blocks until inputs ready** via channels/ready-events |

**Runtime scheduling** realizes a dataflow/actor execution: each traversal is a
forked process; each flow object is a synchronizing handle; a consumer process
blocks until its producer posts. Whatever consistent order the simulator
produces is a valid `schedule` outcome (spec-compliant, since `schedule` grants
that freedom).

**What forces runtime ordering:** inference (edge identity dynamic), `schedule`
blocks, streams / time-consuming bodies, and `replicate` results crossing into an
outer scope (§9).

**Deadlock:** flow dependencies form a DAG (safe alone), but **flow-wait +
resource-hold together** is dining-philosophers. Same mitigations as §7
(canonical claim order, claim-all-at-stage, `try_lock`+timeout).

---

## 9. State objects

A `state` pool is a **single-value store with read/write semantics**, not a
value channel. Two concerns ride on it:

1. **Value flow** — writer's output state flows to the next reader; solved/
   injected like a buffer, with §6.3 forwarding and §10 solve-groups for
   reader-input constraints.
2. **Serialization** — accesses to one pool form a **total order**: the pool
   holds one value at a time, a reader sees the latest write, and an
   `output state` action implicitly reads the *previous* state
   (read-modify-write). PSS therefore forbids two parallel writers; readers may
   be concurrent with each other but not with a writer → **multi-reader /
   single-writer**, exactly `zsp_state_pool #(T)`'s RW semaphore.

- **Static:** emit accesses in the derived order; inject values along the chain.
- **Runtime:** state pool = `{ value, RW-lock, initialized-event }`; readers
  `wait` for initialization, writers serialize.

**The ordering wrinkle (flag for review):** a bare RW lock gives *mutual
exclusion but not a specific order*. PSS state evolution is deterministic
(write₁ → read → write₂). When the order is decided at runtime, exclusion is
insufficient — we need a **version/sequence token** the reader waits for, or we
keep state on the static path. This is the one place the runtime mechanism is
strictly weaker than the static one.

---

## 10. Solve-groups (the shared escape hatch)

A *solve-group* is the maximal set of actions connected by constraints that
**must be co-solved** because a fact couples to another action's *rand* state.
The traversal treats a solve-group as one node: create all members, then one
joint `randomize()` over a generated wrapper (the §5.1 joint-solve from the prior
doc, now applied to a small cluster rather than the whole activity):

```systemverilog
class solve_group_PtoC extends zsp_solve_group;
  rand P p; rand C c;
  constraint bind_b { p.out_b.x == c.in_b.x; }   // binding equality
  function new(); p = new(); c = new(); endfunction
endclass
// create members, group.randomize() once, then execute in schedule order
```

Solve-groups are entered only for the residual coupled cases:
- §6.3 row 3 — consumer input coupled to consumer's own rand field.
- §7 case 3 — explicit cross-action resource-instance constraints.

Most actions are their own (singleton) solve-group and solve inline per §3.1.
Group boundaries are computed by the resolution pass from the constraint graph.

---

## 11. `replicate` and action/array cardinality

`replicate (i:N) { ... }` materializes **N anonymous sub-activities scheduled
under the enclosing scope**. `N` may be random (constrained); `i` may appear in
constraints and bind a label array referenced from outer scope.

- **Static `N`** (literal/const) → unroll at codegen.
- **Random `N`** → solve a bounded `rand int n` first, then a runtime loop
  creating a dynamic array of handles and traversing them under the container's
  semantics:

```systemverilog
if (!randomize(n) with { n inside {[LO:HI]}; }) $fatal(1, "...");
zsp_action insts[]; insts = new[n];
// sequence: for-loop traverse;  parallel/schedule: staged fork/join
for (int i = 0; i < n; i++) traverse(insts[i], ctx, /*idx=*/i);
```

- The index is pinned per instance (`with { idx == i }`).
- **Label arrays consumed in an outer scope** cross a *dynamic-count* boundary →
  those bindings are **runtime** (array-indexed channels/events), never static
  wires.
- `N` must be **bounded** for schedule/resource predictability.

Arrays of action handles in activities follow the same rule (static size →
unroll; dynamic → solve-then-loop). This is a concrete reason the incremental
backbone beats a fixed `enabled`-gated slot pool: random cardinality is the slot
array's worst case and the loop's trivial case.

---

## 12. The `sv-pure` subset

**Tier 1 — direct (works today):** component, action, struct, enum; `rand`
fields; data constraints (arithmetic, relational, logical, slices, `inside`,
`dist`, `foreach` on data arrays, `->`, `if/else`); `exec body/pre_solve/
post_solve`; covergroups; `import target` (void procedural calls).

**Tier 2 — pure-SV via this design:** flow objects `buffer`/`stream`/`state`
incl. back-propagating input constraints (via forwarding + solve-groups);
activities (`sequence`/`parallel`/`schedule`/`select`/`if`/`repeat`/`replicate`)
with **bounded** counts; resource `lock`/`share`; action **inference** with a
**bounded candidate set and depth**; weighted `select` → `dist`.

**Tier 3 — out of subset (diagnose at compile time):**
- `import solve` foreign-language solve functions (no SV equivalent).
- Unbounded inference depth or unbounded `replicate`.
- Constraints whose values depend on `exec body` results computed *between*
  solves within a single solve-group (runtime fixpoint).
- Explicit cross-action coupling too large for a practical solve-group.
- 4-state (`x`/`z`) constraint semantics; constraint arithmetic beyond practical
  bit widths.

The `sv-pure` target must **refuse Tier-3 constructs with actionable
diagnostics**, never silently emit DPI. The DPI/`dv-solve` path stays available
under an explicit opt-in for over-capacity problems.

---

## 13. Runtime library (`zsp_rt_pkg`) extensions

Building on the existing `zsp_component`, `zsp_action`, `zsp_resource_pool#(T)`,
`zsp_stream_channel#(T)`, `zsp_state_pool#(T)`:

- `zsp_buffer_box #(T)` — `{ value, ready-event }` for runtime-form buffers
  (§6.1).
- `zsp_resource_pool #(T)` — add allowed-set arguments to
  `lock`/`try_lock`/`share` (§7 case 2); add `claim_unique(n, mask[])` for
  claim-all-at-stage (§7).
- `zsp_state_pool #(T)` — add a monotonic **version counter** + `wait_version()`
  to give runtime-ordered state the sequencing the bare RW lock lacks (§9).
- `zsp_solve_group` — base for generated joint-solve wrappers (§10).
- `zsp_scheduler` helpers — staged fork/join driver and a dataflow-process
  spawner that wires consumers to channel/event waits (§8).

All additions are ordinary SV classes; no DPI.

---

## 14. Worked sketches

**(a) Producer → buffer → consumer, consumer requires `in.x == 5` (was DPI):**

```systemverilog
// resolution: single static producer; forwarded constraint
p = new(); p.comp = ctx; p.pre_solve();
if (!p.randomize() with { out_b.x == 5; }) $fatal(1,"p"); // §6.3 forward
p.post_solve(); p.body();

c = new(); c.comp = ctx; c.in_b = p.out_b;                // static wire
c.pre_solve();
if (!c.randomize()) $fatal(1,"c"); c.post_solve(); c.body();
```

**(b) Consumer requires `in.x == c.k` (k rand) → solve-group:**

```systemverilog
g = new(); // class with rand p,c and constraint p.out_b.x == c.in_b.x
if (!g.randomize()) $fatal(1,"group");
g.p.body(); g.c.body();
```

**(c) `replicate` of a locking action inside `parallel`:**

```systemverilog
if (!randomize(n) with { n inside {[1:MAX]}; }) $fatal(1,"n");
fork
  for (int i = 0; i < n; i++) begin
    automatic int k = i;
    begin
      automatic act_c a = new(); a.comp = ctx;
      a.r = pool.lock();                         // §7 runtime claim
      if (!a.randomize() with { idx==k; r.instance_id==a.r.instance_id; })
        $fatal(1,"a");
      a.body();
      pool.unlock(a.r);
    end
  end
join
```

---

## 15. Open issues / risks (for review)

1. **State runtime ordering** (§9) — does a version-token suffice, or do some
   patterns require keeping state strictly on the static path? Needs review
   against PSS state-pool reference semantics.
2. **Deadlock from flow-wait + resource-hold** (§7/§7.1/§8) — confirm the
   Model-2 wait-for analysis plus canonical claim order + claim-all-at-stage
   covers real activities; define the timeout policy for the AT-RISK residue.
   Validate the SAFE/INFEASIBLE/AT-RISK verdict against real pool patterns.
3. **Greedy completeness** — characterize PSS patterns where greedy
   incremental + forwarding still dead-ends but a global solve would succeed
   (beyond the known solve-group cases). Is bounded retry worth adding as a
   completeness net?
4. **Bounding policy** — inference depth, `replicate`/array max, candidate-set
   size: inferred from the activity+pool graph, or user pragmas/CLI knobs?
5. **Structure↔value simultaneity** — incremental solves structure (runtime) then
   values (solve), breaking PSS's single-solve-space ideal. Identify patterns
   sensitive to this (e.g. a data constraint that should *steer* which producer
   is inferred) and decide whether they escalate to a solve-group.
6. **Distribution fidelity** — SV solver bias ≠ `dv-solve`; document that
   distributions may differ across targets; use `dist`/`solve...before` to honor
   stated intent.
7. **`select`/`if` interplay with inference** — runtime branch chooses which
   traversals exist; ensure the resolution pass bounds candidate sets per branch.
8. **Diagnostics quality** — Tier-3 refusal messages must point at the offending
   PSS construct with a remediation hint.

---

## 16. Phasing

1. **Resolution pass + static path** (§5, §6.1 static, §8 static fork/join) —
   reproduce today's behavior under the new structure; add static constraint
   forwarding (§6.3) and measure how many current DPI cases go native.
2. **Runtime flow forms** (§6.1 runtime, §6.2, ready-events) + **demand-driven
   inference** (§6.4) over bounded domains.
3. **Resources** (§7) — claim-from-pool, allowed-set, claim-all-at-stage; deadlock
   mitigations.
4. **State** (§9) — RW + version token; serialization.
5. **`replicate`/arrays** (§11) — solve-then-loop.
6. **Solve-groups** (§10) — localized joint solves for residual coupled cases.
7. **`sv-pure` target + Tier-3 diagnostics** (§12); DPI demoted to opt-in.
8. **Empirical validation** on ≥2 simulators (capacity, distribution, deadlock).
</content>
