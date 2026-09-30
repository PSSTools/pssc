# Activities, Flow Objects and Resources on bc — Design for Review

Status: REVISED after review, 2026-09-30. The review decisions are recorded in §11,
and every cross-cutting commitment is folded into the body (§12 traces each one to
where it lives). Scope: the `bc` backend (be-bc oracle VM; rt-eng later), the pssc
front end that feeds it, and the pss-corpus executable tier that adjudicates it.
Nothing here is implemented yet.

Priority: activities take precedence over the op-model procedural work for now
(review decision D13).

The document covers:

- what the LRM requires (§1);
- where we are today, with the evidence (§2);
- the design: one scenario planner with two commit policies, **global**
  (pre-generated) and **on-the-fly** (runtime wait), and two arbitration
  policies, **planned** and **live**, over one set of runtime primitives
  (§3–§6);
- the use cases (§7);
- the tests: pss-corpus (§8) and focused pssc tests (§9);
- phasing (§10);
- **review decisions and remaining open issues** (§11), and the
  **cross-cutting commitments** with where each is built (§12).

LRM references are to the PSS 3.1 Public Review Draft (2026-08-28).

---

## 0. Summary

1. **bc runs sequence, parallel, select, repeat, if and match today. It has no
   flow-object or resource semantics.** Worse than absent, several constructs
   are *silently miscompiled*:
   - a handle traversal (`a1; b1;`) runs coroutine 0 twice;
   - `do pss_top::B` runs the wrong action;
   - `atomic {}` prints nothing;
   - `schedule` becomes `parallel` even when a buffer orders its members.

   The front end also **crashes on any model containing `bind`**. That is 11
   failing tests in `tests/unit/integration/` today, and they are not listed in
   `KNOWN_TEST_FAILURES.md`.

2. **Every decision is made by one planner.** The decisions are inference,
   binding, resource-instance assignment, component assignment, order within
   `schedule`, and attribute values. The two generation modes differ only in
   *horizon* (how much of the scenario is planned at once) and *commit point*
   (when the plan is fixed):
   - **Global:** the horizon is the whole root activity, committed once before
     execution. The committed plan *is* the pre-generated test.
   - **On-the-fly (OTF):** the horizon is a scope, such as a compound action's
     activity. It is committed when that scope is entered, and re-planned only at
     statically-identified *sample points*: places where exec-body data feeds
     later constraints (LRM 13.4.13).
   - Today's bc, which solves each action when it is traversed, is the degenerate
     horizon of one action. It already violates LRM 13.4.9 lookahead, because
     it solves each action *alone*.

   **Lookahead does not need a wide horizon; it needs hoisting** (§5.3). When an
   action's value solve runs, it includes the constraints of every action it is
   *connected* to whose structure is already known:
   - consumers of its outputs;
   - their other producers, transitively;
   - parent constraints over sibling handles;
   - co-claimants of a resource instance.

   Those other actions' variables are *auxiliary*: they are solved to prove a
   completion exists, but they are not committed. The later solve of each
   connected action is then guaranteed satisfiable.

   This works even with per-traversal (greedy) value commit. The horizon now
   governs only *structural* decisions (which producer, which branch, which
   instance, which order). That is also where the remaining conformance gap
   lives.

3. **Runtime waits enforce planned decisions. Under live arbitration, the
   runtime may also pick, but only within bounds the planner set.**
   - The VM gains a small set of suspending primitives:
     - an event per buffer object;
     - a rendezvous per stream;
     - a ticketed read/write gate per state pool;
     - a ticketed claim table per resource pool.
   - In OTF mode these primitives carry the dependencies. In global mode the same
     primitives run as monitors that assert the plan is being honoured.
   - Under **planned** arbitration (the default, and the first implemented),
     waits are ticketed rather than first-come. The realised scenario is then
     independent of action durations. That independence is **random stability
     under timing change**, and it is tested (§9.4).
   - Under **live** arbitration (§5.7), needed for long-running live platforms,
     structural choices the plan left open are made by *availability*: the
     first free compatible resource instance, or the first ready `schedule`
     member. The result is faster execution. The cost is that reproducibility
     then depends on timing. Legality still does not: the planner bounds every
     candidate set, and the checker judges the trace the same way.

4. **The corpus is the oracle.** The pss-corpus checker already has flow and
   resource semantics *designed* (§6.4–6.5: binding witness, Rules C/J, resource
   exclusion), but it implements only P1. It also has zero flow or resource
   tests. The corpus work in this document is to build checker P2/P3 and about
   60 L3/L4 tests. The one addition to the adapter contract is a
   `--mode {global,otf}` flag, plus an optional `plan.json` that the checker
   adjudicates *without executing anything* (§8.3). The two plan forms have
   different jobs: `.zbc` is what the runtime executes, and `plan.json` is what
   the checker reads.

---

## 1. What the LRM requires

### 1.1 Semantic core (normative)

| Topic | Rule | LRM |
|---|---|---|
| Buffer | One producer, any number of consumers. Every consumer starts after the producer *completes*. An action may not both input and output the same buffer object. | 5.1.1, 9.3.1 |
| Stream | Exactly one producer and one consumer. The two start *synchronized*: same predecessors, no introduced delay. | 5.1.2, 9.3.2, 6.3.1c |
| State | A pool holds one object at any time. A writer is exclusive against readers and writers; readers may overlap. `initial` is true until the first write. `prev` refers to the previous object and is vacuous on the initial one. The read/write order is *deterministic*. | 5.1.3, 9.3.3, 12.5, 13.3 |
| Flow arrays | Equivalent to N independent refs. The elements of a state array must bind to distinct pools. | 9.3.4 |
| Resource | `lock` is exclusive for the action's duration. `share` excludes a concurrent lock of the same instance. `instance_id` is in [0, size). Actions claiming the same instance agree on *all* its attributes. | 5.2, 9.4, 12.4 |
| Resource arrays | All elements come from one pool, and the pool must be at least as large as the array. | 9.4.2 |
| Pools and binding | Static binding comes from `bind`, explicit or wildcard. Binding is top-down, with explicit taking precedence over default. Conflicting explicit binds are illegal, as are multiple defaults from the same component. The object type must match the pool type *exactly*: derived to base is illegal. | 12.3 |
| Activity bind | Operands must have the same type and the same static pool, and must obey the kind's scheduling rules. Hierarchical bind delegates a compound action's ref to a sub-action ref. | 11.9–11.11 |
| Component assignment | `comp` is randomized. The pools an action uses follow from the component it is assigned to. | 13.4.5 |
| Inference | Infer only as required. Candidates are first explicitly-traversed actions (the ICL); anonymous instances only if there are none. Chains continue up to a tool limit, after which the tool must error. Nothing may be inferred after the terminating action, except a stream partner. An inferred action is never inside an `atomic` set. | 5.3.2, 14, Annex E |
| Resources and inference | Resources may *prevent* an inference, never *cause* one. If concurrent actions over-subscribe a pool's locks, that is an error. | 5.3.2, Annex E b.1.v |
| Scheduling | Sequential, parallel (synchronized start), concurrent. `schedule` means *any* legal order, including overlap. Join specs `join_branch/select/none/first` apply. `constraint sequence/parallel {…}` is a scheduling constraint. `atomic` is a scheduling cluster. | 6.3, 11.3.4–11.3.7, 13.2 |
| Randomization | Values are assigned in traversal order **with lookahead** over the rest of the activity, including inference, binding and scheduling. Exec-body writes are checked *after* the body completes; they cause no backtracking and may be an error. | 13.4.7–13.4.13 |
| Random stability | See the procedural randomization rules. | 13.4.6.2 |
| Concurrency on one executor | `yield` and blocking channel operations let concurrent bodies interleave. | 20.7.14, 20.8 |

### 1.2 What the LRM means for the two modes

- **Lookahead is normative** (13.4.9). A generator that picks values greedily
  and later hits unsat is *non-conforming* if a legal completion existed. This
  holds unless the conflict comes from exec-body data, which 13.4.13 explicitly
  permits to be an error. The mode design has to put the error boundary exactly
  there.
- **Scheduling is a partial order a solver determines and a scheduler obeys**
  (6.3.1b). The LRM already separates deciding from realising, and this design
  follows that separation.
- **State order is deterministic** (9.3.3a). An OTF runtime may not let a race
  between two `schedule`d writers decide which one writes first.
- **Over-subscribed `parallel` locks are an error, not a wait** (Annex E
  b.1.v.2, plus the synchronized start of 6.3.3). Under `schedule` the same
  claims are legal, because the solver serializes them.

---

## 2. Where we are (evidence)

All of the following was verified by running probes through the real
pipeline: `Parser` → `AstToIrTranslator` → `PSSToScenarioPass` →
`lower_module` → `run_model`.

### 2.1 Front end: `pssparser` → `ast2ir`

`pssparser` represents and resolves everything that is needed:

- `StructKind`, `FieldRef`, `FieldClaim` and `FieldPool`;
- `ComponentBind` with component-path targets and ranges;
- `ActivityBindStmt`, labels and all join specs;
- `ActivityAtomicBlock`, `ActivityReplicate` and `ExportAction`;
- the built-ins `initial` and `instance_id`, with `prev` and `uid` in the symbol
  table.

`ast2ir` has fallen behind the parser's bind-operand rework (pssparser
CHANGELOG, "bind-operand resolution"). Its defects:

| # | Defect | Effect | Where |
|---|---|---|---|
| F1 | Component `bind`: `pool_path.split` on an `ExprRefPathContext` | **crash**, so any pooled model fails | `ast2ir.py:1128` |
| F2 | Activity `bind a.o b.i`: `_hier_id_to_expr` expects `numElems` | **crash** | `ast2ir.py:1501, 2388` |
| F3 | `atomic {}` reads `children()`, but the content is in `getBody()` | empty block, **silent** | `ast2ir.py:1382` |
| F4 | Single-statement `repeat`/`replicate` bodies | `body=[]`, **silent** | `ast2ir.py:5026` |
| F5 | `foreach` reads `getTarget()`, but the collection is in `getPath()` | `collection=None` | `ast2ir.py:1412` |
| F6 | Indexed handle traversal `h[i]`; traversal/handle initializers | index and initializers dropped | `ast2ir.py:1526` |
| F7 | Join spec kind is a string, not `JoinKind`; `join_branch` labels dropped; `schedule` join never read | crash in bc (`kind.name`) or lost | `ast2ir.py:2397`, `1378` |
| F8 | Labels on labeled scopes, and `replicate` `it_label` | dropped | — |
| F9 | `ActivitySchedulingConstraint`, symbol decl/call | debug-log-only drop | `ast2ir.py:1515` |
| F10 | Pool size: literal only (const-folding helper exists but is unused) | `capacity=None` | `ast2ir.py:1179` |
| F11 | Pools and binds in `extend component` | dropped | `ast2ir.py:679` |
| F12 | `export A;` (`ExportAction`) | dropped; root comes from a heuristic | `ast2ir.py:507` |
| F13 | `comp`, `prev`, `uid` never reach the IR | `prev` constraints cannot be expressed | — |
| F14 | `flow_kind` stored as a string where ir-core annotates `FlowKind` | type drift (SV depends on the string) | `ast2ir.py:2463` |
| F15 | `unique {a.x, b.x}` keeps only the last element of each operand (found by the P0 plan) | becomes `unique {x, x}`, **silent miscompile**; `unique {arr}` dropped | `ast2ir.py:2773` |

Crashes and silent drops are *both* reportable. By the house rule ("a
statement ast2ir cannot translate is a translation error, never dropped", in
AGENTS.md), F3, F4, F8 and F9 are defects even before any activity work
starts.

### 2.2 Scenario lowering (`PSSToScenarioPass`) and bc

| Construct | Status |
|---|---|
| seq, nested compound, `parallel` join ALL, `select` (guard, const weight), `repeat(n)`, `if`, `match` | works, tested (incl. legacy differential) |
| `parallel join_none/first` | works in VM on hand-built IR; **crashes** from PSS (F7) |
| `join_select`, `join_branch` | rejected |
| `schedule` | lowered as `parallel` + ALL. **Wrong** whenever flow/state/resource order members. |
| handle traversal `a1;` | **silent miscompile**: `ScInvoke(target=<field name>)`, `_coro_index.get(name, 0)` → coroutine 0 (`lower.py:375`, `orchestration.py:79`) |
| `do pkg::T`, `do T` with T bodiless | **silent miscompile** (same fallback) |
| `do T with {…}`, `h with {…}` | rejected ("later phase") |
| `do T with comp == x` | `comp_expr` ignored |
| `repeat … while`, `replicate`, `super`, activity `constraint`, activity `bind` | rejected |
| `foreach` | rejected by bc (`control.py:52`) |
| `atomic` | body lost (F3); even populated, it is inlined with no cluster semantics |
| compound `pre_solve`/`post_solve` | **silently dropped** |
| only the root component's actions are lowered | `lower.py:153`, so **multi-component models do not run** |
| flow refs | an int slot; attributes not solved; `o.v` → `LoweringError("reference through ExprAttribute")` |
| pools, binds, inference, state, stream pairing | absent (no reader in ir-core or be-bc) |
| resources, claims, `instance_id`, exclusion | absent |
| solving | **per action at INVOKE, fresh `Obj`, forked seed**; parent and child constraints never meet; `strategy`/`members`/`inject`/`solve_before` unused |
| `Op.BIND` | exists as a traced no-op; nothing emits it |
| minor | `message("%d")` of `bit[4]` printed negative values |

### 2.3 Reusable implementations elsewhere

| Where | What | Fit for bc |
|---|---|---|
| `zuspec-solver/src/zuspec/solver/` (mirrored in dv-solve) | `icl.py`, `buffer_inference.py`, `stream_solver.py`, `scheduling_graph.py` (seq/concurrent/mutex → `StagedPlan`), `state_graph.py`, `structural_solver.py`, `flow_constraint_store.py` | **Best.** Runtime-agnostic and dict-based; only their own tests use them. |
| `pssc/targets/sv/` | `analyze_flow`, `analyze_activity` (ICL, staging), `lower_inference`, `lower_resources`, `lower_schedule`, `lower_flow_constraints`; design in `docs/design/pure-sv-incremental-design.md` (resolution pass, incremental traversal, forwarding, solve-groups) | The IR-level analysis should be *shared*, not copied (§4.2). |
| `zuspec-be-py/.../rt/activity_runner.py` and related | Full runtime: traverse with injection, state pools, `acquire_resources`, `ScheduleGraph`, inferred actions (stream-inference raises) | Differential reference until it is retired at P3-exit; semantics unverified. |
| dv-solve C API | `solver_pin_var`, `solver_checkpoint`/`restore`, `solver_add_constraint`, `solver_exclude_value` | Exactly what replanning with pinned history needs (§5.4). |

### 2.4 pss-corpus

- **Tests.** Of 90 executable tests, 0 use flow objects or resources and 2 use an
  activity.
- **Checker.** It implements P1 only: `do`/`seq`/const `repeat`, linear match.
  `refs` are ignored, and any other node is a `ModelError`.
- **Design.** COMPLIANCE-DESIGN.md already designs the rest:
  - the binding witness (one SMT problem per candidate occurrence graph, with an
    existential supplier per demand);
  - Rules C and J (completeness, and justification by well-founded ranks);
  - resource exclusion;
  - `occ=` yield ladders;
  - cheat mutations.
- **Plan.** These are phase P2/P3 of the corpus plan.
- **Seed models.** `curated/language-ref/{flow_basic,resource_arbitration,activity_shapes}.pss`,
  `curated/lrm31/ex170_prev_sequencing.pss`, the `curated/example2` DMA set, and
  `pssc/tests/patterns/*.pss`.

### 2.5 After P0 (2026-09-30)

P0 is tracked in [activity-p0-plan.md](activity-p0-plan.md). The tables
above are the evidence as found; this is what changed.

- **Front end.**
  - Fixed: F1–F12 and F14.
  - Now refused with a located error: F15, traversal initializers (F6),
    activity symbols (F9), bind ranges, block-level `action` data fields, and
    a labeled `replicate` statement.
  - F13 is unchanged (P1/P4b).
  - Traversals carry `type_qname`, the linker's resolution of the action type.
- **Baseline.** The "11 failing" baseline had drifted to 151 against
  pssparser's R1–R4 batches. Every failure was pssc's to fix, and pssparser had
  filed each one (`cross-repo-followups.md` X-5, X-8, X-10, X-14 to X-18). Two
  were silent: named constraints and covergroup names were keyed by AST
  objects. One real pssparser bug was filed
  (`pssparser/docs/design/pssc-requests-2026-09-30.md` P1: an instance name
  accepted as a type qualifier).
- **Scenario lowering and bc (§2.2).**
  - Handle, qualified and bodiless traversals run their own action. An
    unknown target is refused in the pass and in bc; coroutine 0 is gone.
  - `parallel join_none/first` run from PSS.
  - `atomic` bodies run.
  - `repeat … while` lowers to a do-while.
  - `replicate` lowers only in a sequential scope and only unlabeled.
  - A compound action runs its own `pre_solve`/`post_solve`.
  - `schedule` runs only when its members do not interact; otherwise it is
    refused (D4).
  - Everything else in §2.2 is unchanged: flow objects, resources and
    inference are P1 onward.
- **Guards.** Two registry tests: every pssparser activity node, and every
  ir-core activity IR node, translates or lowers, or is refused with a located
  error.
- **Corpus (§2.4).** There is a first L3 slice (8 `act.*` tests); the checker
  handles `replicate`, and the mutants cover `act` records.

---

## 3. Architecture

```
            PSS ──► pssparser ──► ast2ir ──► ir.Context
                                               │
                   ┌───────────────────────────┴──────────────┐
                   ▼                                           │
     (A) Static elaboration (compile time, both modes)         │
         component instance tree · pool-binding table (12.3)   │
         · ICL tables · activity skeleton (labels, handles)    │
         · sample-point analysis                               │
                   │                                           │
                   ▼                                           ▼
     (B) Scenario planner  ◄── horizon/commit policy ──  PSSToScenarioPass
         structure search + joint value solve (dv-solve)     + be-bc lower
                   │                                           │
        global:    │ plan committed once                       │
        OTF:       │ plan per scope, replan at sample points   │
                   ▼                                           ▼
     (C) Plan  ───────────────────────────────►  (D) VM runtime primitives
         serialisable; = pre-generated test         events · stream rendezvous ·
         checkable by the corpus w/o execution      state gate · claim table
                                                    (enforce in OTF, monitor in global)
```

Four layers, each testable on its own:

| Layer | Pure? | Lives in (D1) | Tested by |
|---|---|---|---|
| A. Static elaboration | pure function of IR | ir-core `xf/` (shared with the SV target) | IR-level unit tests |
| B. Planner | pure function of (IR, A, seed, history) | new `zuspec.ir.core.xf.plan`, reusing zuspec-solver engines | planner tests plus exhaustive-enumeration oracle (§9.3) |
| C. Plan | data | ir-core scenario dialect (the plan *model* is the single source); projected to a `.zbc` section (executed by the runtime) and to `plan.json` (read by the checker) through one emitter family (ADR-001) | round-trip; the two projections agree (§9.4); corpus plan adjudication (§8.3) |
| D. VM primitives | runtime | be-bc `interp/` (later rt-eng) | hand-built scenario IR, timing fuzz |

The rule that holds the layers together: **the planner bounds, and the
runtime picks only within those bounds.**
- Under planned arbitration the bound is a single value. Every choice is
  recorded in the plan (or made by the planner at an OTF commit point) from a
  seeded stream. The VM's primitives wait for a planned condition and assert it
  once it is reached.
- Under live arbitration (§5.7) the bound is a *candidate set* the planner has
  proved feasible. The VM picks from it by availability, with a seeded
  tie-break, and records the pick in its trace.
- The VM never invents a candidate.

---

## 4. Static elaboration (layer A)

This layer is computed once per compile and is identical for both modes.

1. **Component instance tree.** This covers arrays, and the paths are what `comp`
   ranges over. It fixes the root-component-only restriction (`lower.py:153`):
   an action type is lowered once per *type*, and its component context becomes
   a runtime parameter.
2. **Pool-binding table.** This is `(component instance, action type, ref
   field[, index]) → pool instance`, following all of 12.3:
   - top-down precedence, explicit over default;
   - illegal conflicts and illegal duplicate defaults;
   - exact-type match;
   - array element ranges;
   - `extend component` contributions.

   It is **one walk**, in the same spirit as `reg_layout.collect_accessors`,
   where two backends folding offsets two ways was "the worst duplication
   available". Pools are the flow-object equivalent of register offsets. The
   consumers are:
   - bc's planner;
   - the SV target's pool resolution, which is migrated onto it. Its golden
     snapshots prove the move preserved output.
   - `--emit-manifest`, which gains `pools`, `bindings` and `icl` sections, so a
     consumer (a test planner or coverage tool) never re-derives them. Adding
     sections is not a `manifest.VERSION` bump.
3. **ICL tables.** Per pool, these record which (type, ref) pairs produce and
   which consume. From them, the candidate producer types for every unbound
   input are known *statically*. The domain of every inference decision is
   therefore finite and known at compile time. This point is taken from the
   pure-SV design's second corollary.
4. **Activity skeleton.** This is every traversal site with a stable id (label
   path plus index). The id is the key that plan entries, trace `occ` values and
   `constraint sequence{…}` all refer to.
5. **Sample-point analysis.** This finds every attribute that is written by an
   exec body, or by `post_solve` on the target side, *and* read by a constraint,
   a guard or a flow-object attribute of a later decision. Each sample point is
   classified:
   - **value-only:** it feeds only later value solves. No replan is needed,
     because late hoisted value solves see the sampled value pinned (§5.3).
   - **structural:** it feeds a guard, a loop condition, `comp`, an
     instance constraint, or an attribute that decides a binding. The OTF
     structure is re-planned there (§5.4).

   A global plan with any sample point cannot be pre-generated faithfully, so it
   is diagnosed (D6).
7. **Hoisting cones.** For each traversal site, the static part of its cone
   (§5.3): the constraint and binding edges known from the model alone
   (explicit `bind`, single-candidate ICL, parent/sibling constraints). The
   runtime adds the edges that come from structural decisions. Precomputing the
   static part keeps a per-traversal solve from rediscovering its neighbours.

   The cone is an IR-level object, not a bc one. The sv-pure design's
   `randomize() with` carries the same cone, with auxiliary variables as local
   `rand` fields of a wrapper. That replaces solve-groups as sv-pure's
   correctness mechanism for the "consumer's own rand" case, so bc and sv-pure
   share one lookahead story (§10, P5b).
   
   **Cone-break analysis (D15).** This finds edges where a cone can be cut
   *without* losing lookahead, so cones stay small. The candidate simplifying
   assumptions are below. Each is checked statically before it is used, and
   **none is adopted until cone telemetry on real models shows it is needed
   (D18)**:
   - **Value-transparent edge.** The consumer places no constraint on the
     object's attributes beyond the type's own, so nothing needs hoisting
     across the edge.
   - **Determined producer.** The producer's output attributes are fixed by
     constants or by pinned history, so the edge carries no choice.
   - **Early instance pinning.** A resource instance's attributes are committed
     at its first claim, which turns the co-claimant edges into pins.
   - **Loop summarization.** `repeat` iterations that share a pool but have
     identical per-iteration constraints can be represented by one iteration's
     projection instead of N copies.

   Every cut is logged with its justification. A cut that loses lookahead
   (UNSAT later, where the uncut cone was SAT) is a bug that the enumeration
   oracle (§9.3) catches.

   **Cone telemetry.** Every solve records its cone size (occurrences,
   variables, edges by kind). Runs report a histogram, and warn above a
   threshold with the chain that made the cone large. This is how the large-cone
   cases the review asked us to watch for get *found* rather than guessed.
8. **Static diagnostics.** These are errors caught before any solve:
   - a stream ref in a context that can never pair with a partner;
   - array claims larger than the pool;
   - a state array on a single pool;
   - a bind across pools or kinds;
   - `parallel` lock demand that exceeds the pool size on every path.

---

## 5. The planner (layer B) and the two modes

### 5.1 What a plan contains

A plan is a finite **occurrence graph**, which is the same object the corpus
checker reconstructs from a trace:

- **Occurrences.** Each records its traversal-site id or `inferred(k)`, action
  type, component instance, and solved attribute values. Values cover the
  action's own fields and the fields of its flow and resource objects.
- **Flow objects.** Each records a pool, a producer occurrence, consumer
  occurrences, and values.
- **Resource assignments.** Each is (pool, `instance_id`) per claim. Each
  resource instance has *one* value record that every claimant agrees on.
- **Order.** This is a partial order: the explicit edges from the activity, plus
  flow edges, plus state sequencing, plus lock serialization inside `schedule`.
  It also records synchronization groups (`parallel` starts and stream pairs),
  and ticket numbers per state pool and per resource instance.
- **Control resolutions.** These record chosen `select` branches, `if`/`match`
  outcomes, `repeat` counts, and `replicate` N.

### 5.2 How the planner decides: structure search, then a joint value solve

A single monolithic finite-domain encoding is possible in principle: activation
booleans for bounded inference slots, selector variables per binding, and order
booleans. But it would be the largest, slowest and least debuggable option.
**Proposal: a two-level planner.**

1. **Structure search.** This is a seeded, randomized, backtracking search over
   the discrete decisions:
   - control resolutions;
   - which ICL candidate satisfies each unbound demand, whether an existing
     occurrence or a new inferred occurrence of a statically known type;
   - resource instance per claim;
   - `comp` per occurrence;
   - order where `schedule` leaves it free.

   The search expands demands breadth-first, bounded by an **inference limit**
   (D5). Pruning comes from cheap structural checks, taken from the
   zuspec-solver engines:
   - the stream 1:1 pairing and synchronization rules;
   - the state writer-chain rules (`state_graph` breadth-first search);
   - resource over-subscription;
   - that the order graph has no cycles.
2. **Joint value solve** for a candidate structure. This is one dv-solve problem
   over every occurrence's attributes and every flow and resource object's
   attributes, with:
   - type constraints, inline `with` constraints, and activity constraints;
   - parent constraints over sub-action handles;
   - binding equalities (consumer ref ≡ producer object);
   - state `prev` chains following the ticket order;
   - `instance_id` values, and equality of attributes per resource instance.

   Unsat returns a structured *nogood* to step 1: the unsat core mapped back to
   decisions, which is where dv-solve's contradiction analysis earns its keep.
   The search then backtracks.

**Completeness.** Within the inference limit, the search is complete whenever
backtracking is exhaustive. That makes it LRM-conforming on lookahead. Budgets
turn "exhausted" into a diagnosed error, never into a silent partial scenario.

**Distribution.** The structure search randomizes the *order* in which
alternatives are tried, from the seed. That gives spread across candidate
producers, instances and orders. The spread is a *quality* property (corpus §8
"inference choice spread"), not a normative one. Uniform sampling over legal
scenarios is out of scope; D10 decides this.

### 5.3 Value lookahead by hoisting

Hoisting separates *checking that a completion exists* from *committing values*.

**The cone.** When occurrence `a` is value-solved, its cone is the connected
component of `a` in the constraint/binding graph, restricted to occurrences
whose *structure* is already committed. Edges are:

| Edge | From |
|---|---|
| output → bound or planned consumer input | flow binding (explicit `bind`, a single-candidate ICL, or an inference already decided) |
| input → the other inputs' producers of the same consumer | transitively, through the consumer |
| sub-action handle ↔ parent constraints and sibling handles | compound-action constraints, inline `with` |
| resource-instance co-claimants | the same (pool, `instance_id`) in the plan |
| state writer → next reader/writer | ticket order; `prev` constraints |

**The solve.**
1. `a`'s solve problem is `a`'s own constraints ∧ every constraint in the cone.
2. Variables of the cone's *uncommitted* occurrences take part as auxiliary
   variables: solved, not committed.
3. Only `a`'s values, plus the values of flow and resource objects `a` owns
   (its outputs, and a first claim's instance attributes), are committed.

**The guarantee.** Suppose that when a cone member `b` is later solved, its
committed neighbours are pinned, and nothing new has entered the cone. Then
`b`'s problem is satisfiable, because the witness found at `a`'s solve still
satisfies it. By induction, a traversal-order sequence of hoisted solves never
dead-ends on constraints that were known at commit time.

**Auxiliary variables are re-drawn (D16).** A later cone member's values are
drawn fresh at its own solve, with its committed neighbours pinned. They are
*not* read from the earlier witness. So each value is drawn at the latest
moment, from values that admit a completion (13.4.9). This is tentative: its
marginal distribution differs from global mode's joint draw, which is
acceptable under the quality-only distribution stance (D10). It is revisited
once the enumeration oracle produces distribution data (D20). The `commit-aux`
fault switch (§9.6) keeps the alternative testable.

**Cone size (D15).** A cone is a connected component. Long buffer chains, a
`repeat` whose iterations share a pool, or a heavily shared resource instance
can make it most of the scenario. Three measures, in order:
1. **Break it** where static cone-break analysis (§4.7) proves that is safe.
2. **Reuse it.** Solve the cone once; its witness serves each member's
   feasibility until something new enters the cone. That turns N solves into one
   plus N cheap pinned re-draws.
3. **Cap it** at size K as a safety valve. The dropped edges are exactly the
   lookahead given up. The cap is deterministic, and reported with the chain
   that hit it.

Cone telemetry (§4.7) shows which of the three is carrying the load on real
models.

**Relation to the pure-SV design.** `pure-sv-incremental-design.md` §6.3 forwards
consumer constraints onto the producer's `randomize() with`. It sends the case
"`in.x == k`, where `k` is the consumer's own rand" to solve-groups (§10 there).
Hoisting with auxiliary variables handles that case directly: `k` is simply an
auxiliary variable in the producer's solve. Solve-groups become an
*optimisation* (commit a whole cone at once), not a correctness mechanism.

**What hoisting cannot see, and so what the horizon is still for:**
1. **Exec-body data** (13.4.13). A value sampled after `a` commits is not in
   `a`'s cone. A conflict is the LRM-sanctioned error. Hoisting reduces how
   often it happens: values are solved *late* (per traversal), so a solve that
   runs after the sample sees the sampled value pinned.
2. **Uncommitted structure.** A consumer whose producer is not yet chosen, an
   unchosen `select` branch, or an unassigned resource instance is not an edge
   yet. Two remedies, in increasing cost:
   - **Feasibility-filtered choice.** When a structural decision is taken,
     consider only alternatives whose hoisted cone is SAT. This is one-step
     structural lookahead, and it is cheap: the alternative sets are the static
     ICL tables.
   - **Disjunctive hoisting.** Hoist ∨ over the alternatives' cones. Taken over
     all future choices, this *is* global planning. The continuum from greedy
     to global is therefore just how much future structure gets hoisted.

### 5.4 Horizon and commit: structure and values separately

Hoisting lets the two kinds of decision commit at different times, which gives
OTF a natural shape:

- **Structure** (binding, inference, instances, order, branches) commits at the
  *horizon*.
- **Values** commit *per traversal, as late as possible*, with the hoisted cone.

| Setting | Structure committed | Values committed | Conforming? | Use |
|---|---|---|---|---|
| `global` | whole root activity, once | once, before execution (a plan needs values) | yes (within limits) | pre-generated tests; plan export; sample points diagnosed |
| `scope` (OTF default) | on entry to each compound action's activity; structural **replan** only when a sample point feeds a *structural* decision | per traversal, hoisted | yes, up to exec-body data (13.4.13) | OTF with runtime feedback; unbounded `repeat…while` |
| `greedy` | per traversal, with feasibility-filtered choice | per traversal, hoisted | value lookahead yes. Structural lookahead only one step: can fail when two *future* structural choices interact. | fastest OTF; the lower bound |
| `greedy-nohoist` (test only) | per traversal | per traversal, own constraints only (today's bc) | no | calibration: a lookahead test this setting passes is not testing lookahead |

Consequence for OTF: with values committed late and hoisted, a sample point
matters only when it feeds a *structural* decision (a `select` guard, an
`if`/`repeat…while` condition, a `comp` or instance constraint, or an
inference-relevant flow attribute). Sample points that feed *values* need no
replan: the later value solve simply runs with the sampled value pinned.
Replanning becomes rare. Static analysis (§4.5) classifies each sample point as
value-only or structural.

**Replan with pinned history.** At a *structural* sample point the planner
re-solves the remainder of the current scope. The inputs are:

- everything already executed, pinned: `solver_pin_var` for values; structure
  as fixed facts;
- the newly sampled values, pinned;
- `solver_checkpoint`/`restore`, so that scope-level solves do not recompile the
  problem.

If the remainder is unsat, that is the 13.4.13 error: "exec body values
conflict; no backtracking". The error names the sample point and the
constraint that failed. It is a legal, deterministic outcome, not a crash.

### 5.5 What OTF must decide early, and why

Some decisions cannot wait until the action that needs them runs:

- **Stream partner.** It must be spawned for a *synchronized* start, so it is
  decided when the producer's *start* is planned, not when its body runs.
- **Buffer producer for a later consumer.** The producer must complete first.
  Inferred producers are placed immediately before the consumer's start within
  the scope, which is legal because an inferred action may be anywhere before
  its consumer. Explicit producers already in the scope are candidates, and the
  ICL rule (explicit before anonymous) is enforced *as a search order*. Once
  the binding is decided, the consumer's constraints are hoisted onto the
  producer (§5.3). This holds even when the producer is solved long before the
  consumer runs.
- **Resource attributes.** All claimants of an instance share one set of values
  (9.4.1b). In OTF the first commit that assigns an instance *pins* its
  attributes for the whole run. The constraints of every co-claimant whose
  instance is already planned are hoisted into that first solve. A later
  claimant that was *not* yet planned (for example, one in a scope entered
  later) chooses among the instances whose pinned attributes it is compatible
  with. Under live arbitration the choice is by availability (§5.7). If no
  instance is compatible, that is a diagnosed conflict. **This residual gap is
  accepted (D7)** as the price of faster execution on long-running platforms;
  UC4b records it.
- **State ticket order.** Writer order is decided at commit time, never by a
  runtime race (9.3.3a).

### 5.6 The same seed, two modes (D9)

A model with no sample points should give an *equally legal* scenario under
`global` and under `scope` for the same seed. It is **not** required to give the
*same* scenario, because the horizons differ. It is required to pass the
checker, and for a fully-determined model (one legal scenario) the two must
match exactly. That gives a cheap cross-mode test class (§9.4).

Random stability across *model* versions is not promised either. Adding an
action type to a pool changes the ICL search order, and so it changes
scenarios for existing seeds. That is accepted (D9).

### 5.7 Arbitration policy: planned and live (D3, D7)

The commit policy (§5.4) says *when* structure is decided. The arbitration
policy says *who picks* among the alternatives the planner has proved feasible.

| Policy | Who picks | Reproducible under timing change | When |
|---|---|---|---|
| `planned` (default; built first) | the planner, from the seed, as a ticket | yes | pre-generated and OTF tests; regressions; the corpus |
| `live` | the runtime, by availability, from a planner-bounded candidate set | no. Reproducible given the same timing (seed plus availability log). | long-running live platforms, where waiting for a planned instance or order costs throughput |

What `live` may decide at runtime, each from a bounded candidate set:

| Decision | Candidate set (bounded by the planner) | Runtime pick |
|---|---|---|
| Resource instance per claim | instances of the bound pool whose pinned attributes are compatible with the claimant's hoisted cone | first free compatible, seeded tie-break |
| Order among `schedule` members | all orders the plan's partial order allows | first ready |
| Order of state writers under `schedule` | the same | first ready. **Option, off by default** (D17). 9.3.3a says state read/write is deterministic, so this is a documented departure used only when asked for. It is built only when a live target needs it. |

What `live` never decides:
- binding;
- inference;
- `select` branches;
- `comp`;
- values.

Those stay with the planner (with hoisting), because changing them changes
*which scenario* runs rather than *when* its parts run.

How it is realised:
- **`ClaimTable` gains a candidate-set claim.**
  `CLAIM {pool, candidates, mode}*` picks the first all-free compatible
  combination, all-or-nothing. It then commits the pick through a runtime pin:
  the instance's attributes are pinned for later claimants.
- **`schedule` order** becomes the natural ready order of frames whose `Event`
  predecessors are satisfied.
- **The pick log.** Every runtime pick is written to the VM trace as a
  `pick` event. Replaying the pick log turns a live run into a planned replay
  of the same scenario. That is the debugging path for a live-run failure. On
  silicon, the log has to be buffered on target. That is deferred (D21), but it
  is important for long-running on-silicon generators.
- **Conflicts.** A claimant with no compatible instance left is a diagnosed
  conflict (D7) naming the earlier claim that pinned it. A claimant waits only
  while some compatible instance exists and is held. If none is compatible,
  it fails at once rather than waiting forever.

What stays true under `live`:
- legality: the checker adjudicates live runs the same way;
- deadlock freedom: all-or-nothing claims, and no hold-and-wait;
- the corpus result: tests that pass `planned` must pass `live`, except those
  strict-listed as live-sensitive (UC4b, and UC17 in its planned-only form).

---

## 6. Runtime primitives (layer D)

The VM already has frames, SPAWN/JOIN/INVOKE, a ready queue and a timed heap
(`WAIT`), seeded forks, and cancellation for FIRST(n). The additions are
**ticketed** under planned arbitration: a waiter carries the ticket the plan
assigned, and a primitive releases waiters in ticket order, not arrival order.
Under live arbitration (§5.7), `ClaimTable` and `schedule` order release by
availability, within the planner's candidate sets.

These are new opcodes (D2): `EV_WAIT/EV_SET`, `RDV`, `ST_ACQ/ST_REL`,
`CLAIM/RELEASE`, `PLAN/REPLAN`, plus the existing no-op `BIND` slot reused for
trace. Each is added to rt-eng under the drift guard.

| Primitive | State | Operations | OTF semantics | Global semantics |
|---|---|---|---|---|
| `Event` | done flag | `EV_WAIT e`, `EV_SET e` | buffer ready; DAG edges not expressible as series-parallel | same (plan edges) |
| `Rendezvous` | 2-party barrier | `RDV_ARRIVE r` | stream producer/consumer synchronized start; `parallel` start sets | same |
| `StateGate` | current object, next writer ticket, active readers | `ST_ACQ pool, ticket, mode`, `ST_REL` | readers overlap; writer exclusive; tickets enforce deterministic order; `initial` → false on first write | monitor: assert ticket order |
| `ClaimTable` | per instance: lock holder / sharer set, next ticket, pinned attributes | planned: `CLAIM {pool, iid, mode, ticket}*`; live: `CLAIM {pool, candidates, mode}*`; both all-or-nothing; `RELEASE` | planned: wait until every planned instance is free *and* it is our ticket's turn. Live: first free compatible combination, seeded tie-break, pin on first claim, `pick` trace event. Atomic multi-claim avoids hold-and-wait deadlock. | monitor: assert exclusion |

Notes:

- **Deadlock freedom.** Claims are acquired all-or-nothing at action start,
  and the plan's ticket order is a topological order of the plan's partial
  order. So a wait cycle requires a cycle in the plan, and the planner rejects
  those. A VM stall with frames blocked on primitives is therefore reported as
  an internal error (a planner bug), never as a hang.
- **Resource lifetime.** A claim is released at the action's end. That is the
  body's end for an atomic action, and the activity's end for a compound action
  that claims (9.4.2).
- **Trace.** Every primitive emits a VM trace event (the trace-schema already
  reserves `BIND`). These events explain *why* the scenario came out the way it
  did. They are a debugging aid, **not** the adjudication input: the corpus
  still judges only `@@PSS-TRACE` records.
- **Durations.** In bc an action body takes zero virtual time unless it
  `WAIT`s. A harness-only `ImportProvider` can inject seeded per-action delays.
  This is how timing fuzz works (§9.4) and how waits are exercised for real.

### 6.1 Global-mode execution

1. The plan is lowered to bytecode:
   - one coroutine instance per occurrence, with its `Obj` pre-populated
     (no `SOLVE`);
   - series-parallel structure as SPAWN/JOIN;
   - every remaining edge as an `Event`;
   - tickets as constants.
2. The primitives are still executed, so a global run is self-checking: an
   assertion fires if realised order ever violates the plan.
3. This also gives a **replay**: the same plan can be re-executed under timing
   fuzz, and every run must produce the same records.

### 6.2 OTF-mode execution

1. The compound action's coroutine calls `PLAN scope` on entry. This is a new
   op that calls the planner through the host; later it becomes a native
   planner or a precompiled solve.
2. It receives the scope's committed decisions into its frame.
3. It then runs the lowered activity with the primitives as real waits.
4. Sample points lower to `REPLAN` after the sampling body completes.

The planner call is the one place the VM consults an external decider. Its
inputs are the pinned history. Its output is deterministic given (seed,
history). Under live arbitration, history includes the pick log, so a replay of
the pick log reproduces the run.

The pinned-history binding is new work in `NativeBlobBackend`: it needs
`solver_pin_var`, `solver_checkpoint`/`restore`, `solver_add_constraint` and
`solver_exclude_value`, which already exist and are documented in dv-solve's
`solver_api.md`. No solver work is needed. dv-solve's contradiction analysis
maps unsat cores back to planner decisions, which is how the planner's nogoods
(§5.2) are built.

### 6.3 The primitives are also op-model primitives

A ticketed claim table and a state gate are what an operation model needs to
arbitrate concurrent calls on one executor (LRM Ex 319, `channel_c`). The
primitives are therefore specified once, in `zuspec-be-bc/docs/spec/`, as
**the reference semantics** for concurrency arbitration:
- bc's VM implements them first;
- the op-model backends' runtimes are held to the same spec. op-model-sv is the
  first, since its concurrency is currently unobserved by the corpus.
- The corpus yield-ladder tests that exercise them (§8.5) run on bc and on the
  op-model backends alike.

---

## 7. Use cases

Each use case names the LRM behaviour, the mode(s) it exercises, and what
proves it. The PSS below is sketch-level.

### UC1 — Explicit buffer, sequential (the baseline)

```pss
buffer data_b { rand bit[8] v; }
component pss_top {
  pool data_b p; bind p *;
  action P { output data_b o; constraint o.v in [1..10]; exec body { /* act o.v */ } }
  action C { input  data_b i; constraint i.v > 5;          exec body { /* act i.v */ } }
  action T { P p1; C c1; activity { p1; c1; bind p1.o c1.i; } }
}
```

Proves:

- activity `bind` (F2);
- a joint solve across producer and consumer, so that `c1.i.v` is in [6..10]
  (lookahead);
- attribute references through flow refs.

Modes: all three. `greedy` must pass, because `c1`'s constraint is hoisted onto
`p1`'s solve (§5.3). `greedy-nohoist` fails on roughly half the seeds
(`o.v ∈ [1..5]`), which proves the test exercises lookahead.

### UC2 — Implicit binding, no inference allowed

Same model with the `bind` removed. `p1` is explicitly traversed and
compatible, so Annex E puts it in the ICL and **no action may be inferred**.
Proves the ICL order rule: explicit candidates before anonymous ones, and
Rule J (an unjustified inferred action is a FAIL).

### UC3 — Inference chain with a constraint forcing a second producer (LRM Ex 175)

`write1` produces `dat ∈ [1..5]`, the consumer needs `[8..12]`, and `write2`
produces `[6..10]`. The activity `write1; read;` must infer `write2`, and
`read.in_obj.dat ∈ {8,10}`. Proves:

- inference triggered by a *data* incompatibility;
- a joint solve over the inferred occurrence;
- in `greedy`: feasibility-filtered choice. `write1` is already committed, so
  its cone with `read` is UNSAT and `write2` is inferred. `read`'s constraint is
  then hoisted onto `write2`.

All settings must pass.

### UC4 — Lookahead, split by what hoisting can see

**UC4a — value lookahead through the consumer's own rand (hoisting suffices).**
The consumer has `rand bit[4] k; constraint i.v == k * 2; k > 5;`, and the
producer is solved first. This is the case the pure-SV design sent to
solve-groups. Hoisting with `k` as an auxiliary variable solves it.

| Setting | Expected |
|---|---|
| `global`, `scope`, `greedy` | PASS |
| `greedy-nohoist` | fails on some seeds (calibration) |

**UC4b — structural lookahead across horizons (hoisting cannot see it).**
Compound `S1` shares a `pool[2]` resource with `kind != A`. A later compound
`S2` locks one with `instance_id == 0 && kind == A`. `S1`'s instance is chosen
before `S2`'s structure is committed. If it picks instance 0 and pins
`kind != A`, then `S2` is unsat.

| Setting | Expected |
|---|---|
| `global` | PASS |
| `scope`, `greedy` | may fail on some seeds; strict-listed as the documented OTF gap accepted by D7 |
| `scope` with `live` arbitration | same verdicts as `scope`. The D7 conflict diagnostic fires immediately at `S2`'s claim, rather than hanging. |

The pair is the precise statement of what OTF with hoisting does and does not
guarantee.

### UC5 — Stream pairing and inferred stream partner (LRM Ex 190, send/receive)

`do send_data;` with a stream output. `receive_data` must be inferred and start
*synchronized* with `send_data`. Proves:

- stream inference, which legacy be-py cannot do;
- `Rendezvous`;
- that nothing is inferred after the terminating action except the stream
  partner.

A negative variant makes a stream producer and consumer explicitly
*sequential* with a `bind`. It must be rejected, with a located error.

### UC6 — State machine with `initial` and `prev` (LRM Ex 135, Ex 170)

`configure` needs `prev_conf.mode == UNKNOWN`, and the initial state is
UNKNOWN. Proves:

- `initial` is true exactly until the first write;
- `prev` constraints over the ticket order;
- more than one `configure` per pool is impossible unless a reset is inferred.

A `schedule { w1; w2; r; }` variant proves the order is decided by the plan,
not by a race. Under timing fuzz, the order of the `act` records must be
invariant for a fixed seed.

### UC7 — Resource lock and share in `parallel` (LRM Ex 176)

This is `pool[2]` with `a1`, `a2` sharing and `a3` locking in parallel, then
`a4` locking with `kind==A`. Exactly one legal instance assignment exists up to
symmetry, and `a3.kind == A` must be derived through the instance equality.
Proves:

- attribute agreement per instance;
- exclusion;
- resource constraints propagating across sequential time.

### UC8 — Over-subscription: error vs wait

- `parallel { lock; lock; lock; }` on `pool[2]` must be an **error**.
  Pre-execution in global mode; at planning of the scope in OTF. A negative
  corpus test requires a located diagnostic.
- The same claims under `schedule` must **pass**, with the third lock
  serialized after one of the first two.

### UC9 — Arrays of claims (LRM Ex 70)

`lock config c[8]` on `pool[16]`: two in parallel is legal; three is an error.
`share config c[16]` in parallel is legal. Proves array claims, all-or-nothing
acquisition, and the static diagnostic "array larger than pool".

### UC10 — Multi-component pools and `comp` (LRM Ex 134)

A per-`dma_c` channel pool plus a top-level core pool bound to `{dma0.*, dma1.*}`,
with `constraint xfer_a.comp != xfer_b.comp`. The same-`instance_id` channel
constraint is legal; the same-core constraint conflicts. Proves:

- component assignment;
- pools resolved per component instance;
- lowering actions outside the root component.

This is the multi-component gap in §2.2.

### UC11 — Pool binding rules (12.3)

Covers:

- explicit over default;
- top-down precedence;
- array-range component paths;
- `extend component` binds;
- exact-type binding.

The negative variants are derived-to-base binding and conflicting explicit
binds. These are mostly elaboration tests (layer A); the corpus gets the
subset that is observable.

### UC12 — `atomic` excludes inference (LRM Ex 101)

`atomic { A_a; B_a; }`, where `B_a` needs a buffer. The inferred producer may
not land between `A_a` and `B_a`. Proves the atomic cluster rules (11.3.7),
and that inferred actions are never inside an atomic set.

### UC13 — Scheduling constraints and join specs

This is Example 169 (`constraint sequence{sf1.a, sf2.b}` and
`constraint parallel{…}` across `schedule`d compounds), plus each join spec:

- `join_branch(L)` with a follow-on;
- `join_none`;
- `join_first(1)` with cancellation.

Proves the join specs from PSS (F7) and scheduling constraints (F9).

### UC14 — Runtime sampling (OTF only)

An exec body writes a non-rand `y` from an import (for example, a value read
from the DUT). A later traversal's inline constraint depends on `y`. Proves:

- a **value-only** sample: the later value solve runs with `y` pinned, and no
  replan happens;
- a **structural** variant, where `y` feeds a `select` guard or `repeat…while`
  condition: a replan at the sample point with pinned history;
- the 13.4.13 error when `y` conflicts with values *already committed* before
  the sample, which is a located, deterministic `solve_fail` at the sample
  point.

Global mode must *diagnose* this model at compile time (D6), not generate a
plan that silently ignores `y`.

### UC15 — Random cardinality

`repeat (n)` with `rand n` and a producer/consumer pair inside, and
`replicate (i:N)` with `N` rand, where each replica consumes from a pool fed by
an explicit producer before the loop. Proves:

- bounded unrolling in global mode;
- per-iteration planning in scope mode;
- buffers shared by many consumers.

### UC16 — Timing independence

Any of UC5–UC8 run under seeded duration fuzz. The `act`, `chk` and `acc`
record *sets and values* must be identical across fuzz seeds for a fixed
generation seed, and their order must be identical wherever the plan orders
them. This proves "waits enforce, never decide" under **planned** arbitration.

It is also the property to advertise: a PSS test stays reproducible when the
DUT's latency changes. Runtime generation usually loses that.

### UC17 — Live arbitration

`resource.lock.exclusion` and a `pool[4]` channel model under `schedule`, with
long and varied seeded durations, run with `live` arbitration. Proves:
- instance picks follow availability, each from the planner's candidate set;
- every live run is legal (checker PASS);
- the pick log replayed as a planned run reproduces the records exactly;
- throughput: the live run's virtual end time is ≤ the planned run's on the
  same durations. That is the reason live exists, so it is measured.
- A claimant whose compatible instances are all pinned incompatibly fails
  immediately with the D7 conflict diagnostic, rather than waiting forever.

---

## 8. pss-corpus work

### 8.1 Checker (build the designed P2/P3)

| Item | COMPLIANCE-DESIGN ref | Needed by |
|---|---|---|
| Activity matcher: `par` and `schedule` (shuffle match), `select`, `if`/`match`, `repeat`/`replicate`/`foreach`, labels | §6.3–6.4 stage 2 | every L3 test |
| `refs` on occurrences, dotted keys (`o.v`, `r.instance_id`), `comp.pct_id` | §4.3, §6.5 pool identity | every L4 test |
| Occurrence graph with leftover → candidate inferred occurrences | §6.4 | inference tests |
| SMT: supplier disjunction per demand, per-kind order rules, state sequence with `prev`, resource range, exclusion and attribute agreement | §6.4 stage 3 | L4 |
| Rules C and J with ranks | §6.5 | inference and anti-cheat |
| `occ=` attribution and the four ladder checks | §4.9 | parallel/schedule/resource-exclusion observation |
| Cheat mutations in `validate` | §6.5 | proving the checker bites |
| **New:** plan adjudication (§8.3) | extends §4.7 native mode | global mode, solve-only tools |
| **New:** structure enumeration (all-SAT over the legal-scenario encoding, blocking per structure) | reuses stage 3 | the planner's enumeration oracle (§9.3), structural coverage (§12) |

**Scaling (D8).** The 64-record budget will bind for flow tests with `repeat`,
because each occurrence carries refs. Tests are written to fit within it for
now. P3c then *measures* both scaling approaches on the same growing suite
(occurrence count × pool sharing):
1. raise the budget with an incremental solver;
2. with a plan supplied, break the problem into parts along the plan's
   structure. Plan adjudication makes the witness search unnecessary.

The better performer becomes the default.

### 8.2 Adapter contract

- **`--mode {global,otf}`** (optional). A tool with a single mode ignores it and
  reports `mode` in `outcome.json`. A test may declare
  `"run": {"modes": ["global"]}`. For example, UC14 is OTF-only; a global run of
  it expects a compile-time diagnostic.
- **`plan.json`** (optional output). A tool that pre-generates writes its plan.
  The checker then adjudicates the plan in addition to the trace, and checks
  that the **trace realises the plan** (§8.3).
- **`picks.json`** (optional output, live arbitration only). This is the
  runtime pick log. The checker does not need it to judge legality. It is kept
  so a failing live run can be replayed.
- The bc adapter wires `--mode`, and stops rejecting `--iterations` once
  scope-mode replanning is in.

### 8.3 Plan adjudication (native mode, built)

The corpus design contemplates a "native mode" for solve-only tools (§4.7) but
leaves it unbuilt. This design builds it, because a plan *is* an occurrence
graph with values.

- **`pss-corpus check --plan plan.json`** adjudicates a plan with no execution.
  With the witness given, stage 3 is one satisfiability check with no supplier
  disjunction. It is fast and scales far past the 64-record budget.
- **Solve-only tools.** A tool that "solves at generation time and emits target
  code" (Perspec, `HANDOFF.md`) is judged on its *decisions*, with no target,
  simulator or build. Its export bundle may return `plan.json` instead of, or as
  well as, a trace.
- **Trace realises plan.** When both are present, a second, independent check
  runs. The verdicts are split into:
  - `PLAN-FAIL`: the generator decided wrongly;
  - `REALISE-FAIL`: the runtime executed a correct plan wrongly.

  This is the most useful diagnostic split available.
- **`plan.json` schema.** It lives in the corpus (`schema/plan-1.0.json`). It is
  a projection of the ir-core plan model, generated alongside the `.zbc` plan
  section by one emitter family (D14). The runtime never reads `plan.json`, and
  the checker never reads `.zbc`.

### 8.4 Test catalogue (L3 activities, L4 flow/resource)

Each test is an instrumented `test.pss` plus a `test.json` legality model.
IDs follow the design's `<area>.<feature>.<variant>.NNN` style. The
**Derived** column points at seed models, rewritten and never vendored (the
LRM © rule, §2.1 of the corpus design).

**L3 — activity structure** (prerequisite; no flow objects)

| ID | Proves | Derived |
|---|---|---|
| `act.handle.traverse.001` | `a1; b1;` runs A then B (today: runs coroutine 0 twice) | — |
| `act.traverse.qualified.001` | `do pkg::T` | — |
| `act.with.inline.001..3` | `do T with`, `h with`, sub-handle constraint from the parent | Ex 179, 183 |
| `act.lookahead.001` | Ex 183 `v; s1;` with `s1.a.val == v.val` (13.4.9): passes only if the parent constraint is hoisted onto `v` | Ex 183 |
| `flow.lookahead.hoist.001` | UC4a: consumer's own rand hoisted onto producer | UC4a |
| `resource.lookahead.cross_scope.001` | UC4b: global-only guarantee; strict-listed for OTF | UC4b |
| `act.schedule.order.001` | `schedule` members all run, any order; distribution checks both orders reachable | Ex 92, 93 |
| `act.join.{branch,none,first,select}.001` | join semantics from PSS source | Ex 94–98 |
| `act.atomic.001` | atomic body runs (today: dropped) | Ex 101 |
| `act.repeat.single_stmt.001` | `repeat (3) do P;` runs 3× (today: 0×) | — |
| `act.repeat_while.001`, `act.replicate.{const,rand}.001`, `act.foreach.001` | the rejected constructs | — |
| `act.schedcon.{sequence,parallel}.001` | `constraint sequence{…}` | Ex 169 |
| `act.compound.pre_post_solve.001` | compound pre/post_solve run (today: dropped) | — |
| `act.comp.select.001` | `do T with comp == x` | — |

**L4 — flow objects**

| ID | Proves | UC |
|---|---|---|
| `flow.buffer.bind.explicit.001` | UC1 | UC1 |
| `flow.buffer.bind.implicit.001` | UC2, no inference when explicit is compatible | UC2 |
| `flow.buffer.infer.data.001` | Ex 175, inference by data incompatibility | UC3 |
| `flow.buffer.infer.chain.001` | multi-level chain (xfer_data), terminates | Ex 190+ |
| `flow.buffer.multi_consumer.001` | one producer, many consumers, all after producer | Ex 67 |
| `flow.buffer.array_ref.001` | `output B o[3]`, element-wise constraints, bind to element | Ex 67 |
| `flow.buffer.equality.001` | `gd1.src == gd2.src` ⇒ one producer | Ex 191 |
| `flow.buffer.parallel_consumers.001` | producer before a `parallel` of consumers | — |
| `flow.stream.explicit.001` | explicit stream in `parallel`, synchronized | Ex 66 |
| `flow.stream.infer.001` | inferred partner, terminating-action rule | UC5, Ex 190 |
| `flow.stream.infer_buffer_to_partner.001` | inferred stream partner needs its own buffer, so a chain | Fig 5(i) |
| `flow.state.initial.001` | `initial` true only before first write | UC6, Ex 135 |
| `flow.state.prev.001` | `prev` sequencing (Ex 170 power states). Strict-listed on bc until `prev` reaches the IR (D11, P4b). | UC6 |
| `flow.state.infer_writer.001` | `!curr.initial` forces an inferred writer | Ex 190 |
| `flow.state.schedule_rw.001` | writers exclusive, readers overlap (ladders) | UC6 |
| `flow.state.array_pools.001` | state array bound to distinct pools | Ex 133 |
| `flow.pool.two_pools.001` | Ex 193: pool decides which producer type is inferred | Ex 193 |
| `flow.pool.bind_precedence.001` | explicit over default; top-down | UC11 |
| `flow.pool.subtree_infer.001` | infer a partner in another component's subtree | Ex 194 |
| `flow.hier.bind.001` | compound action's ref delegated to sub-action (11.10) | — |
| `flow.atomic.no_infer_inside.001` | UC12 | UC12 |

**L4 — resources**

| ID | Proves | UC |
|---|---|---|
| `resource.lock.exclusion.001` | two locks of `pool[1]` serialize under `schedule` | UC8 |
| `resource.lock.parallel_fit.001` | two locks of `pool[2]` in `parallel`, distinct ids | — |
| `resource.share.parallel.001` | many shares overlap | Ex 70 |
| `resource.lock_share.mix.001` | Ex 176 attribute agreement and derivation | UC7 |
| `resource.array.001` | `lock c[8]` on `pool[16]` | UC9 |
| `resource.instance_id.constraint.001` | `instance_id` constraints honoured | — |
| `resource.comp.per_instance_pool.001` | Ex 134 | UC10 |
| `resource.compound_claim.001` | claim held for a compound action's whole activity | — |
| `resource.prevents_inference.001` | an inference candidate excluded because it would over-subscribe | 5.3.2 |

**L4 — combinations (P4 generators later).** The corpus design's `flow-scheduling`
group is {buffer, stream, state} × {explicit, implicit} × {seq, par, schedule,
replicate}, less stream×seq. This design adds one more axis for resources,
{none, lock, share}, to cover pairwise interactions.

**Negative (compile/solve-time, located diagnostics)**

| ID | Rule |
|---|---|
| `neg.flow.bind.type_mismatch.001` | 11.9a |
| `neg.flow.bind.cross_pool.001` | 11.9c |
| `neg.flow.stream.sequential.001` | stream bound across `sequence` |
| `neg.flow.buffer.in_and_out.001` | 9.3.1f |
| `neg.pool.derived_to_base.001` | 12.3g |
| `neg.pool.conflicting_explicit.001` | 12.3d |
| `neg.resource.array_gt_pool.001` | 9.4.2d |
| `neg.resource.parallel_oversubscribe.001` | Annex E b.1.v.2 |
| `neg.flow.no_producer.001` | ICL empty (Annex E b.2.vi) |
| `neg.flow.infer_limit.001` | cycle-only producers ⇒ inference limit error |
| `neg.otf.sample_conflict.001` | UC14 13.4.13 error (OTF mode) |

**Distribution entries (P5, quality):**

- inference choice spread (Ex 190 `setup_A` vs `setup_B`);
- `schedule` order reachability;
- `instance_id` spread in `resource.lock.parallel_fit`;
- `select` over flow alternatives (Ex 189).

These stay quality-only (D10). Hitting rare structures on purpose is the job
of targeted scenario identification (deferred, D19), not of
making the distribution normative.

**Harvested models (before be-py retirement).** be-py's `activity_runner` and
its tests hold a year of edge cases:
- `zuspec-dataclasses/tests/unit/test_rt_{flow_objects,resource,binding_solver,pool_resolver,schedule_graph,runner_parallel}.py`;
- `test_p1_flow_binding.py`, `test_p2_structural_inference.py`,
  `test_state_inference.py`, `test_activity_flow_resource.py`.

Their *models* are rewritten as corpus tests, with legality models rather than
be-py's expected values, before P3-exit retirement. That keeps the knowledge in
a tool-neutral form instead of deleting it with the runtime. Each harvested
test records `"derived_from"` in its `test.json`. This is a tracked work item
in P3c/P4 with a deadline, not an aspiration.

### 8.5 Instrumentation conventions to add (corpus design §4)

- **`act` records.** Print every ref's fields, `r.instance_id`, and `comp.pct_id`
  whenever a type is reachable through more than one pool. This is already
  designed.
- **Inferred actions.** They print `act` like any other action. The checker
  never needs a tool to *say* "inferred" (Rule J decides).
- **Stream and state tests.** These use yield ladders with `occ=` to *observe*
  synchronized start and reader overlap. bc must support `yield`: it is
  currently UNSUPPORTED (`proc.yield.single.001`), so that support is a
  prerequisite.

---

## 9. Focused pssc tests

The approach follows the house rule: **construct-level tests through bc**, not
codegen diffs. The tests are layered, so a failure points at one layer.

### 9.1 Front end (IR shape)

`tests/unit/integration/`: one probe per F1–F14 row, asserting IR shape. The
11 failing tests already cover F1 and F2 and are the first to go green. Add:

- atomic body non-empty;
- single-statement loop bodies;
- `foreach` collection;
- indexed handle;
- join kind is `JoinKind`, with branch labels;
- `schedule` join;
- scheduling constraint present;
- `extend component` pools and binds;
- const-folded pool size;
- `export A;` recorded;
- `prev` reachable in a constraint (after D11's deferral ends; strict xfail
  until then).

Each one is the "silent drop becomes a translation error" guard.

**Silent-drop audits** (P0). F3, F4, F8, F9 and the coroutine-0 fallback share
one pattern: an unhandled case falls through to a default. Two registry tests
catch the whole class, following the `test_dispatch_matches_registry`
precedent:
- `test_every_activity_node_translates`: walks every pssparser activity AST
  node kind (from the schema, not a hand list). Each must either produce an IR
  node or raise a translation error. The debug-log fall-through at
  `ast2ir.py:1515` is deleted.
- `test_every_activity_ir_lowers`: walks every ir-core `Activity*` class. Each
  must either lower in `PSSToScenarioPass` and be-bc, or raise
  `UnsupportedConstructError`. An unresolved INVOKE/SPAWN target is a
  `LoweringError`, never coroutine 0.

### 9.2 Static elaboration

`tests/unit/xf/test_pool_binding.py`, as table tests over small component
trees:

- every 12.3 rule a–g;
- array paths;
- extension contributions;
- the exact-type rule.

Also:
- `test_icl_tables.py` (the candidate sets);
- `test_sample_points.py` (value-only vs structural);
- `test_cone_static.py`: static cone edges, and cone-break justifications, each
  checked against the uncut cone on the enumeration suite;
- `test_manifest_pools.py`: `--emit-manifest` pools, bindings and ICL match the
  table.

These tests are pure Python and fast. The SV target's resolution tests run
against the *same* table, and its golden snapshots stay byte-identical across
the migration.

### 9.3 Planner

- **Deterministic unit tests** on tiny models, one per UC1–UC13, asserting
  plan properties rather than whole plans.
- **An exhaustive-enumeration oracle.** For models with at most about 6
  occurrences and small domains, enumerate *all* legal scenarios with the
  corpus checker's SMT encoding (all-SAT, blocking clause per scenario
  structure). Then assert:
  1. every plan the planner produces over N seeds is in the set (soundness);
  2. every *structure* in the set is produced by some seed within a budget
     (search completeness and reachability);
  3. a χ² spread report (quality, not a gate).

  This is the strongest test available, and it costs almost nothing once the
  checker's P3 SMT exists. It catches "the search never tries the second ICL
  candidate" bugs that no number of PASSes reveals, and most generators never
  test reachability at all. The enumerated structures also define the
  *structural coverage space* for targeted scenario identification. That use
  is deferred (D19).
- **Nogood tests.** A contrived unsat structure must backtrack to the right
  decision; the unsat core maps back to decisions.
- **Hoisting tests.** These are the core OTF unit tests:
  - cone construction per edge kind (flow, sibling/parent, co-claimant, state
    ticket);
  - auxiliary variables solved but not committed;
  - the induction guarantee as a property test: over random tiny models and
    seeds, a `greedy` run with *no structural choice* never hits unsat.
- **UC4a vs UC4b** across all four settings pins exactly what hoisting buys and
  what only a wider horizon buys.
- **Cone size.** Reuse gives the same commits as a fresh cone solve, given the
  same pins. A cap produces the deterministic warning. Telemetry histograms are
  emitted.
- **Cross-backend hoisting (P5b).** UC4a runs on bc and on sv-pure with the
  same IR cone.

### 9.4 VM primitives and modes

- `packages/zuspec-be-bc/tests/unit/interp/test_flow_primitives.py`: hand-built
  scenario IR covering event ordering, rendezvous, state-gate ticket order under
  adversarial arrival, claim-table all-or-nothing and ticket order, and a
  deadlock-detection error on an injected cyclic plan.
- **Timing fuzz** (UC16): a harness `ImportProvider` with seeded per-body
  delays; record-set invariance over K fuzz seeds.
- **Global replay:** plan → execute twice under different fuzz → identical
  records; injected plan violation → monitor assertion fires.
- **Cross-mode:** for fully-determined models, `global` = `scope` exactly; for
  others both pass the checker.
- **Live arbitration** (UC17):
  - every live run passes the checker;
  - the pick-log replay reproduces the records;
  - live virtual end time ≤ planned on the same durations;
  - the D7 conflict fails immediately;
  - the whole corpus L4 suite is run under `live` as well as `planned`, with
    live-sensitive tests strict-listed separately
    (`expected/bc-live.toml`).
- **Plan projections agree:** decoding the `.zbc` plan section and projecting
  it to `plan.json` gives byte-identical output to the planner's direct
  `plan.json` (D14).
- **Op-model conformance:** the primitive spec's tests (§6.3) are parametrized
  over bc and, as their runtimes adopt the spec, the op-model backends.
- **Goldens:** disassembly goldens for the new ops, following the existing
  `ZBC_REGEN_GOLDEN` discipline.

### 9.5 Legacy differential (time-boxed)

Before be-py's `activity_runner` retires (P3-exit), extend
`tests/diff/test_t1a_activity_differential.py` to flow and resource models.
This is **not** used as an oracle: where it disagrees with bc, the corpus
checker decides who is wrong. It is used as a *discovery* tool. Its test models
are harvested into the corpus.

### 9.6 Fault injection: prove the tests bite

Planner and VM builds gain a test-only `--fault=` switch:

| Fault | Behaviour |
|---|---|
| `skip-buffer-wait` | consumer does not wait for its buffer producer |
| `race-state` | state order decided by runtime arrival |
| `ignore-lock` | lock claims do not exclude |
| `extra-infer` | infer an action that is not required |
| `drop-stream-partner` | no stream partner is spawned |
| `stale-prev` | `prev` refers to the wrong earlier state |
| `nohoist` | the `greedy-nohoist` setting: value solves see only their own constraints |
| `commit-aux` | auxiliary cone variables are committed rather than re-solved (random-stability and distribution tests must notice) |
| `bad-cut` | cone-break analysis cuts an edge that is not value-transparent |
| `live-escape` | live arbitration picks outside the planner's candidate set |

For each fault, the named corpus tests must go FAIL. This is the tool-side twin
of the checker's cheat mutations: those prove the checker catches bad traces,
and these prove the *test suite* catches bad tools. Together they measure the
corpus's sensitivity from both sides, and the faults double as documentation of
what each primitive is for. The kill matrix (fault × test) is checked in, and a
fault that no test kills fails the build.

---

## 10. Phasing

Each phase ends with a gate that tests can check. Activities take priority over
the op-model procedural work for now (D13).

| Phase | Content | Gate |
|---|---|---|
| **P0 — Stop the bleeding** | F1–F14 in ast2ir, `bind` first (except F13's `prev`/`uid`, deferred by D11). bc: an unknown traversal target is an **error**, never coroutine 0; handle → type resolution; `JoinKind`; `repeat…while` and `replicate` mapped to existing VM loops; compound pre/post_solve. `schedule` is **rejected** when its members have flow, state or resource relations, until P3/P4 do it right (D4), rather than silently run as parallel. Both silent-drop registry tests (§9.1). | the 11 failing tests green; both registry tests green; L3 corpus tests that need no flow objects pass or are strict-listed |
| **P1 — Compound-scope solving** | handles are objects the parent solves (first use of hoisting: parent and sibling constraints form the cone); INVOKE with a provided `Obj`; inline `with`; activity constraints; struct and attribute references (`o.v`) in bc (also unblocks 5 `types.*` corpus tests); `comp` assignment and non-root components; `yield` | Ex 179/183/184 lookahead tests pass; `types.*` UNSUPPORTED entries drop |
| **P2 — Static elaboration** | in ir-core `xf/` (D1): component instance tree; the pool-binding table as one walk; ICL tables; sample points (value-only vs structural); static cones plus cone-break analysis and telemetry; static diagnostics. **The SV target migrated onto the table.** `--emit-manifest` gains `pools`/`bindings`/`icl`. | table tests; SV golden snapshots byte-identical; manifest tests |
| **P3 — Explicit flow and resources, global mode, planned arbitration** | planner v0 in `zuspec.ir.core.xf.plan` (no inference): explicit and implicit-to-explicit binding, resources, state tickets, `schedule` ordering. Plan model with its `.zbc` and `plan.json` projections; VM primitives as new opcodes (D2); global execution with monitors; timing-fuzz harness | UC1, 2, 6, 7, 8, 9, 10 pass on the corpus; UC16 timing-fuzz invariance; plan projections agree |
| **P3c — Corpus checker P2/P3** | runs alongside P3: matcher, refs, witness SMT, Rules C/J, ladders, cheat mutations; **plan adjudication** (`check --plan`, PLAN-FAIL/REALISE-FAIL); structure enumeration; the D8 scaling experiment; start harvesting be-py models (§8.4) | checker self-tests plus 100% cheat-mutation kill; D8 measured and a default chosen |
| **P4 — Inference** | buffer, state and stream inference, chains, limit (D5: depth 8 / 32 per scope), atomic exclusion, nogoods through dv-solve contradiction analysis; **enumeration oracle** on the tiny-model suite; fault-injection kill matrix; harvest complete before be-py retirement | UC3, 5, 12; enumeration soundness and reachability green; every fault killed |
| **P4b — Deferred front-end items** | `prev` and `uid` into the IR, fixed at source in pssparser (D11) | `flow.state.prev.001` and Ex 170 pass |
| **P5 — OTF, planned arbitration** | `PLAN`/`REPLAN` ops; hoisted per-traversal value solves (runtime cone edges added to the static part) with re-drawn auxiliary variables (D16); cone reuse and cap (D15); feasibility-filtered structural choice; scope horizon; structural-only replan through the pinned-history binding in `NativeBlobBackend`; 13.4.13 errors; `greedy` and `greedy-nohoist` settings | UC4a, 14, 15, 16; UC4b strict-listed for OTF (D7); cross-mode tests |
| **P5b — Hoisting in sv-pure** | the sv-pure target consumes the IR cone; auxiliary variables as wrapper-local `rand`; solve-groups become optional | UC4a passes on both bc and sv-pure |
| **P6 — Live arbitration** (D3, D7) | candidate-set `CLAIM`; availability-ordered `schedule`; pick log and replay; the state-order-by-arrival option (off by default) only when a live target needs it (D17); D7 conflict diagnostic; `bc-live.toml` | UC17; L4 suite under `live` |
| **P7 — Op-model alignment** | the primitive spec in `zuspec-be-bc/docs/spec/` is the reference; op-model-sv's runtime conforms first, then the others | spec tests parametrized over bc and op-model-sv |
| **P8 — rt-eng** | port primitives and the planner call to the native engine. The planner stays host-side, or becomes a precompiled solve per scope. | rt-eng differential vs oracle on the L4 suite |

P0 is worth doing on its own even if the rest waits: today's silent
miscompiles make *every* activity result from bc untrustworthy.

---

## 11. Review decisions and remaining open issues

### 11.1 Decisions (review of 2026-09-30)

Numbers match the questions of the draft (Q1–Q16).

| # | Topic | Decision | Where it lands |
|---|---|---|---|
| D1 | Where the planner lives | Layer A in ir-core `xf/`; the planner in `zuspec.ir.core.xf.plan`, importing the zuspec-solver engines. The duplicated dv-solve mirror of those engines gets one owner (zuspec-solver). | §3, P2, P3 |
| D2 | Opcodes or imports | New opcodes: `EV_WAIT/EV_SET`, `RDV`, `ST_ACQ/ST_REL`, `CLAIM/RELEASE`, `PLAN/REPLAN`, plus the `BIND` slot for trace; mirrored in rt-eng | §6 |
| D3 | Arbitration | Deterministic (`planned`) now. Opportunistic (`live`) will definitely be needed for live runs, so it is designed now and built in P6. | §5.7, P6 |
| D4 | `schedule` before it is done right | Reject interacting `schedule` members until P3/P4 | P0 |
| D5 | Inference limit | Depth 8 and 32 inferred occurrences per scope, overridable; the error names the unfinished demand chain | P4 |
| D6 | Sample points in global mode | Diagnose at compile time. Conditional plans are a research item. | §4.5 |
| D7 | Structural dependencies OTF cannot see | Accept them: some conflicts are resolved at runtime by availability, and the rest are diagnosed. The trade-off is faster execution on long-running platforms. Horizon widening is not pursued now. | §5.5, §5.7, UC4b, UC17 |
| D8 | Checker scalability | Measure both approaches (incremental budget raise; break the problem into parts along a supplied plan) and keep the better | §8.1, P3c |
| D9 | Random stability | Across modes: equally legal, not identical. Across model versions: adding an action type may change scenarios. | §5.6 |
| D10 | Distribution | Quality-only for inference. Rare cases are reached through targeted scenario identification (coverage), not normative distribution. | §8.4, §12 |
| D11 | `prev`/`uid` in the IR | Yes, fixed at source in pssparser, deferred to P4b | P4b |
| D12 | `comp` in records | Always report `comp.pct_id` when more than one instance exists | §8.5 |
| D13 | Priority | Activities now, ahead of the op-model procedural work | header, §10 |
| D14 | Plan format | `.zbc` is for the runtime and `plan.json` is for checking. Both are projections of one ir-core plan model, generated by one emitter family and tested to agree. | §3, §8.3, §9.4 |
| D15 | Cone size | Reuse, with a cap as a safety valve. Watch for large cones (telemetry) and look for simplifying assumptions that break chains safely (cone-break analysis). | §4.7, §5.3 |
| D16 | Auxiliary variables | Re-drawn per traversal (tentative) | §5.3 |

### 11.2 Follow-up decisions (second review, 2026-09-30)

The draft's remaining issues R1–R5 are now settled or deferred. Nothing blocks
P0–P5.

| # | Topic | Decision | Where it lands |
|---|---|---|---|
| D17 (R1) | State order by arrival under `live` | Yes, as an option, off by default. It is prioritized only when it better enables live targets, not built speculatively. | §5.7, P6 |
| D18 (R2) | Which cone breaks are safe | Measure with cone telemetry on real models (WB DMA, curated example2), then decide which §4.7 assumptions to adopt. None is adopted before data. | §4.7, P2/P5 |
| D19 (R3) | Structural coverage and targeted scenarios | Deferred until the pipeline runs end to end. The enumeration oracle is still built, for soundness and reachability, not for coverage. | §9.3 |
| D20 (R4) | Re-drawing auxiliary variables (D16) | Stays tentative; revisit when the enumeration oracle produces distribution data | §5.3 |
| D21 (R5) | Pick-log buffering on silicon | Deferred. It is recorded as important for long-running on-silicon activity generators: a live generator on target needs a bounded pick log (or periodic flush through the executor/channel seam) to stay replayable. | §5.7 |

### 11.3 Parked for later (not blocking)

- **Structural coverage (D19).** Structure classes rather than full
  structures; steering by ICL search order or by constraints; the relation to
  PSS `cover` and monitors (Clause 16).
- **On-target pick logs (D21).** Size bound, format, and flush policy for
  long-running on-silicon generators.
- **Conditional global plans (D6).** Research item.

---

## 12. Cross-cutting commitments (formerly "overlooked opportunities")

The review accepted all of these. Each is now a commitment in the body; this
table traces it.

| # | Commitment | Built in | Phase | Proved by |
|---|---|---|---|---|
| 12.1 | The plan is the pre-generated test, and the corpus adjudicates it without execution (PLAN-FAIL vs REALISE-FAIL) | §8.3, §8.2 | P3c | plan-adjudication self-tests; the bc round trip |
| 12.2 | Exhaustive enumeration oracle for small models: soundness *and* reachability | §8.1, §9.3 | P3c, P4 | the enumeration suite |
| 12.3 | Timing independence as a tested and advertised property (planned arbitration) | §6, UC16, §9.4 | P3 | timing fuzz |
| 12.4 | Fault-injected generators, the twin of cheat mutations; the kill matrix fails the build | §9.6 | P4 | the kill matrix |
| 12.5 | One static elaboration for SV, bc and the manifest | §4.2 | P2 | SV goldens byte-identical; manifest tests |
| 12.6 | One planner; the modes are commit and arbitration settings; `greedy-nohoist` as calibration | §5.4, §5.7 | P3, P5, P6 | cross-mode tests; UC4a/b |
| 12.7 | Use dv-solve's existing incremental API; contradiction analysis provides nogoods | §5.2, §6.2 | P4, P5 | pinned-history and nogood tests |
| 12.8 | The runtime primitives are the reference semantics for op-model concurrency | §6.3 | P7 | spec tests over bc and op-model-sv |
| 12.9 | Harvest be-py flow and resource models into the corpus before retirement | §8.4 | P3c, P4 | harvested tests carry `derived_from` |
| 12.10 | Silent-drop audits at both layers | §9.1 | P0 | the two registry tests |
| 12.11 | Hoisting is backend-neutral: one IR cone for bc and sv-pure | §4.7, §5.3 | P5b | UC4a on both backends |
| new | Live arbitration: the planner bounds, the runtime picks | §5.7, §6 | P6 | UC17 |
| new | Cone-break analysis and cone telemetry | §4.7 | P2, P5 | `test_cone_static.py`; telemetry |
| new | Structural coverage from enumeration, as the path to rare scenarios | §9.3, §11.3 | deferred (D19) | — |

---

## Appendix A — Evidence index

| Claim | Where |
|---|---|
| bind crash, 11 failing tests | `ast2ir.py:1128`; `tests/unit/integration/test_{component_bind,activity_bind,state_flow_objects,resource_fields}.py` |
| coroutine-0 fallback | `zuspec-be-bc/.../lower/orchestration.py:74,79` |
| handle traversal targets the field name | `zuspec-ir-core/.../xf/pss_lower/lower.py:375` |
| root-component-only lowering | same file `:153` |
| `schedule` ≈ `parallel` | same file `:405-410` |
| per-action SOLVE with fresh Obj | `zuspec-be-bc/.../interp/vm.py:50-67`, `ops_orch.py:152-179` |
| `Op.BIND` no-op | `ops_orch.py:216` |
| checker P1 scope | `pss-corpus/checker/src/pss_corpus/check.py:3-9, 272-285` |
| corpus flow/resource design | `pss-corpus/COMPLIANCE-DESIGN.md` §4.9, §6.4–6.5, §13 |
| dv-solve incremental API | `packages/dv-solve/docs/solver_api.md` |
| pure-SV incremental design | `docs/design/pure-sv-incremental-design.md` |
| single decider, many realizers | `docs/design/dynamic-multi-actor-executors.md` §5.0 |
