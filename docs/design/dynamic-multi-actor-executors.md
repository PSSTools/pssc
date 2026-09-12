# Dynamic Multi-Actor Executors in PSS

**Status:** assessment for review
**Date:** 2026-09-12
**Scope:** viability of treating PSS executors as independent, runtime-addressable behavior
engines that interact peer-to-peer, rather than as static partitions of one pre-scheduled
scenario.

> **Read §§9–10 first.** §§1–8 assess the original executor-centric proposal and are the basis for
> what follows, but §9 ("Distributed scenarios as communicating PSS models") supersedes their
> framing and §10 ("Hierarchical scenarios") is the worked architecture and the current
> recommendation. The decisive move is compositional: bound the solve domain per model and declare
> the interfaces, rather than distributing decision-making across a single flat model.
>
> **Bottom line:** a hierarchical scenario — a super-model whose activity invokes per-model root
> actions as functions, each implemented as a randomized behavior — is largely expressible in
> standard PSS 3.1 today. Three gaps need standardization: return values from exported actions,
> constraints on interface parameters, and delegated address space.
>
> §11 sharpens the super-model's role from *dispatching operations* to **coordinating the
> parameters of N concurrent per-rack traffic scenarios so they interact**. That is the strongest
> form of the argument — it puts the solver on a small, densely-correlated problem and off a large,
> nearly-independent one — and it surfaces the deepest open issue: the super-model is definitionally
> blind to whether the interaction it engineered actually occurred (§11.6).

---

## 1. The proposition

Today's deployment model for PSS on multi-executor systems is *static partitioning*: the tool
solves the whole scenario on the solve platform, projects it onto each executor, and emits one
program per executor. Those programs synchronize with each other (by a tool-private mechanism)
to reproduce the scheduling relations the model declared. Everything that will happen is decided
before the first instruction executes.

The proposal is to invert the runtime: each executor becomes an independent engine that accepts
*instructions to run behaviors* and returns results — an RPC-shaped interface. One executor is
designated the initiator (for bring-up, seeding, and teardown), but any executor may originate
work for any other. Motivation: complex, long-running traffic scenarios on systems larger than a
multi-core SoC, where the interesting behavior is the *interaction* and no static schedule can
plausibly cover it.

This document assesses viability against the PSS 3.1 public review draft
(`PSS 3.1 Public Review Draft 2026.08.28`, Clauses 16, 20, 21) and against prior art in
distributed-systems testing.

---

## 2. What PSS 3.1 already gives you

Read carefully, PSS 3.1 is much closer to this model than the prevailing deployment style
suggests. Four existing features are load-bearing.

### 2.1 Executors are already an abstraction over "who executes", not "which core"

§21.7.1 is explicit that an executor may be an embedded core, a bus BFM, or a testbench
transactor. §21.8 (`target_execution_unit_c`) lets each executor's realization be emitted into a
different file *in a different target language* (`C`, `CPP`, `SV`). The dual-cluster example in
§21.8 pairs real RISC-V/DSP cores with a SystemVerilog transactor standing in for a coreless
cluster, treated symmetrically.

This is the feature that generalizes the idea past multi-core SoC. An "actor" in the proposed
system need not be a core; it can be a host process, a service model, a traffic generator, or a
peer chip. The standard already permits heterogeneous actors as equal peers. Most users don't
exploit this.

### 2.2 `sync_pkg::channel_c` — new in 3.1 — is a real inter-executor transport

§21.9 adds a typed, bounded FIFO with blocking `get`/`put` and non-blocking `try_get`/`try_put`,
and requires (§21.9 item d) that implementations provide mutual exclusion and synchronization
**"between test realization running on multiple executors independent of the language
implementing those executors."**

That sentence is the whole ballgame. The standard now obliges a conforming tool to provide a
cross-executor, cross-language message transport. §20.8 completes it: blocking on a channel
yields the executor to other concurrently-assigned exec blocks, so an actor can wait on incoming
work without starving its other duties.

A request/response RPC is a pair of channels. The transport layer for the proposal exists in the
3.1 standard as written.

### 2.3 Exported actions are already an inbound behavior-request interface

§20.10: `export target comp::A1(bit mode)` exposes a PSS action as a callable function in the
target language. Critically, §20.10.1(b): *"Each call into an export action infers an independent
tree of actions, components, and resources."*

So "foreign code asks PSS to run a behavior, with parameters, and PSS figures out the details" is
already a standardized primitive — and it is already available on the **target** platform via the
`platform_qualifier`. That is an RPC entry point in all but name.

### 2.4 Runtime executor identity already exists

`executor()` (§21.7.2.5) returns a reference to the currently operative executor at target time,
and `executor_base_c::get_context()` (§21.7.1.1.1) returns a `chandle` identifying the execution
context. Between them you have "which actor am I" and a token by which an actor can be named.
§21.7.2.5's delegation example (`my_target_op()` dispatching to `executor().my_target_op_impl()`)
is polymorphic dispatch on actor identity.

Executor **traits** (§21.7.2.3) are, structurally, a capability-description mechanism — "this
actor is in cluster 0", "this actor can do X". They are consulted only at solve time today.

**Conclusion of §2:** the proposal is prototypable in standard PSS 3.1 *without language
changes*, as a library (`actor_pkg`) layered on `channel_c` + `export target action` +
`executor()`. That is the single most important practical finding in this document.

---

## 3. What PSS 3.1 does *not* give you

### 3.1 Solving is defined as a solve-platform activity

The entire Clause 20 framing is a solve platform / target platform split. Table 28 does bless
three flows, and one of them — **per-model target code generation** (generate code once per
entity *type*, reuse it to execute scenarios) — is precisely the substrate a dynamic dispatcher
needs. But the standard never says where a *target*-qualified exported action's inferred tree is
solved, how big that solver is, or what it may see. That is the central unspecified area.

### 3.2 Constraints do not cross the RPC boundary

§20.10.1(c): *"Constraints and resource allocation are considered within the inferred action tree
and are not considered across imported function / exported action call chains."*

This is the strongest objection to a naive version of the proposal, and it deserves to be stated
plainly: **if inter-actor work is dispatched as independent exported-action calls, you lose
global constrained-random solving between actors.** You can no longer express "the DMA that core
3 runs must target a buffer disjoint from the one core 5 picks," because the two are inferred
independently. Flow objects (buffer/stream/state) cannot bind across the boundary either, so the
scheduling relations that are PSS's primary value proposition stop spanning actors.

Taken to its conclusion, a pure-RPC system degrades PSS from "one solved scenario" to "a set of
independent mini-scenarios plus ad-hoc messaging" — which is a worse UVM virtual sequence, not a
better PSS. §5 proposes the structure that avoids this.

### 3.3 Address allocation is framed around a bounded scenario

§21.11.2 says outright: *"This standard does not define any method by which the PSS tool might
resolve address claims at solve time or might generate code for runtime allocation."* Claims are
made; nothing is ever freed. For a scenario that runs for minutes or hours and issues work
continuously, monotone allocation is an exhaustion bug with a long fuse. A dynamic multi-actor
engine needs a **target-time allocator with free semantics** and that is entirely
implementation-defined today.

### 3.4 Non-determinism is declared but not managed

The draft is honest about non-determinism in exactly the places the proposal would amplify it:
- §21.9.1.1 — order in which multiple channel waiters are serviced is non-deterministic
- §20.8 — order in which blocked exec blocks are awakened is non-deterministic
- §20.1 (Example 287) — `run_start` ordering *across* executors is arbitrarily interleaved

That is appropriate for a standard describing a static scenario whose *outcome* is determined by
the solve. It is not sufficient once runtime interleaving determines *what happens at all*.
There is no standard seed-derivation rule, no trace format, and no replay story.

### 3.5 No termination model for open-ended behavior

PSS scenarios are finite graphs with completion semantics. "Long-running traffic" has no natural
end. Nothing in the standard expresses "run until stopped," and nothing defines what scenario
coverage means for a process rather than a traversal.

### 3.6 Deadlock freedom is silently given up

In the static model, deadlock freedom comes free: the tool computes a legal schedule and emits
it. Introduce blocking `get`/`put` between actors under cooperative multitasking and you have
reintroduced classic distributed deadlock, on a bare-metal target with no debugger. `channel_c`
offers `try_get`/`try_put` as escape hatches, but there are **no timed variants** and no watchdog
concept.

---

## 4. Prior art, and what it predicts

### 4.1 This is endpoint projection, and the theory is known

What PSS tools do today — take a global scenario and derive a per-participant program — is
exactly **endpoint projection** in multiparty session types (MPST): a *global type* is projected
into a *local type* per participant, with deadlock freedom and protocol conformance guaranteed by
construction ([Hybrid Multiparty Session Types, POPL/PACMPL
2023](https://dl.acm.org/doi/abs/10.1145/3586031);
[arXiv:2302.01979](https://arxiv.org/abs/2302.01979)).

The useful prediction from that literature: **not every global protocol is projectable.** The
projectable subset is the one where each participant can locally determine its next move. Where a
choice is made by one party and observed by others, the deciding party must *explicitly notify*
the affected parties — projection fails otherwise. Applied here: as soon as runtime choice
replaces solve-time choice, PSS needs either (a) explicit notification edges in the model
wherever one actor's random choice constrains another's, or (b) runtime monitors that check
conformance instead of guaranteeing it. There is no third option, and the standard currently
relies on a fourth that only works statically ("the tool inserts whatever sync it needs").

### 4.2 On-target generation is proven in exactly this domain

IBM's **Threadmill** is a bare-metal post-silicon exerciser that generates test cases *on
platform*, continuously, executes and checks them without host interaction — explicitly designed
for multi-threaded processors, generating multiple threads with **shared generated addresses to
maximize collisions**, running indefinitely with fresh randomization per round
([Adir et al., DAC 2011](http://www.eecs.umich.edu/courses/eecs578/eecs578.f15/papers/adi11.pdf)).
The stated design pressures — keep the tool simple, minimize environment interaction, maximize
platform utilization — are the same pressures the proposal will face.

The follow-on work on [pre-generated
data](https://link.springer.com/chapter/10.1007/978-3-319-03077-7_12) is directly relevant to
§5: it resolves the footprint problem by precomputing expensive artifacts (address translation
paths) on the host and having the on-platform engine select among them. RISC-V's STING takes a
similar bare-metal-generator posture.

So "each actor generates its own work at runtime" is not speculative. It is the established
architecture for long-running multi-threaded stress.

### 4.3 Choreography's tax is well characterized

The proposal is a move from **orchestration** (initiator decides everything) to **choreography**
(actors react to each other), with one orchestrator retained for lifecycle. The microservices/saga
literature is unanimous on the trade: choreography buys decoupling and throughput, and costs you
*implicit global state, distributed logic, and the ability to answer "what happened to
request #12345?"* without forensic log analysis
([Temporal](https://temporal.io/blog/to-choreograph-or-orchestrate-your-saga-that-is-the-question);
[AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga-choreography.html)).
The recurring practical conclusion is *hybrid*: choreography for high-throughput loosely-coupled
flow, orchestration for the stateful business-critical parts. That maps onto §5's two tiers
almost one-to-one.

For verification the debuggability cost is not a footnote — it is the dominant cost. A failing
long-running multi-actor test that cannot be reduced is close to worthless.

### 4.4 Determinism is the counterweight the industry converged on

Every serious modern distributed-systems testing practice answers non-determinism the same way:
**deterministic simulation testing** — control all sources of entropy, run the whole system under
one logical scheduler, and replay failures exactly. FoundationDB pioneered it; Antithesis
productized it; TigerBeetle and WarpStream use it
([Antithesis
docs](https://antithesis.com/docs/resources/deterministic_simulation_testing/);
[TigerBeetle, protocol-aware
DST](https://tigerbeetle.com/blog/2026-08-20-protocol-aware-dst/)). The mechanism that makes this
tractable in practice is **hierarchical seeding**: one master seed derives per-participant seeds,
so one number reproduces an entire multi-agent run — a pattern also standard in multi-agent
traffic simulation.

Microsoft's **P** / **Coyote** line is the other half: model actors as communicating state
machines and let a controlled scheduler systematically explore interleavings
([Coyote](https://microsoft.github.io/coyote/concepts/actors/overview/); P is used at AWS for
model-checking distributed protocols). The relevance to PSS is that PSS *already is* a declarative
actor-ish model; the missing piece is a controllable scheduler, not a modeling language.

**The prediction I'd stake the assessment on:** the dynamic multi-actor mechanism is the easy
part. Reproducibility is the part that decides whether it is usable.

---

## 5. Assessment

**Viable, with one structural condition.** The condition is that *dynamism must be introduced at
the realization layer, not by replacing the scenario model.*

### 5.0 The invariant this rests on: single decider, many realizers

PSS semantics are already, in effect, *one logical decision-maker and N realizers*. All
randomization, all scheduling choice, all resource and address allocation, all executor
assignment — every **decision** — is made in one place with one global view. Executors only
carry out **procedural realization** of decisions already made. Executor assignment itself is a
solved variable (§21.7.2), not a runtime routing choice.

This is worth stating as the starting point rather than as a limitation, for three reasons:

- **It is already a distributed system with a centralized controller.** The multi-executor test
  running on silicon today *is* multi-actor; what is centralized is the decision-making, and that
  centralization happens before the test starts. So the gap to "dynamic multi-actor" is narrower
  than it looks: the actors exist, the transport now exists (§2.2), and what is missing is
  runtime decision authority — nothing else.
- **It cleanly separates the two axes.** Decision topology and realization topology are
  independent (§5.3). The invariant says PSS today is *centralized decisions + distributed
  realization*. The interesting next point is not the far corner (distributed decisions +
  distributed realization); it is *centralized decisions + distributed **dispatch***, which is
  Tier 1 below.
- **It makes Tier 1 conformant by construction.** If decisions stay where the standard puts them,
  changing when and how realization is dispatched is a code-generation strategy, not a semantic
  change. No LRM work is needed to try it — which is what makes it the right first path.

The productive way to frame the whole program, then, is: *how much decision authority can be
moved to the edge, and in what increments, before the guarantees that make PSS valuable break?*
Tier 1 moves none — only dispatch timing — and already buys peer-to-peer interaction. Tier 2
moves it all. Those are the endpoints of a spectrum worth traversing deliberately; §7.10 notes
the intermediate steps that are probably the real design space.

Concretely, two tiers, which should be designed together and kept distinct:

### 5.1 Tier 1 — Dynamic realization of a statically solved scenario (recommended first step)

Keep the scenario graph exactly as PSS defines it: one solve, global constraints, flow objects
and resources spanning all actors, full scheduling semantics. Change only *how the schedule is
realized at runtime*: instead of emitting a straight-line program per executor, emit
(a) per-action-type parameterized behavior code per executor — the "per-model target code
generation" flow Table 28 already sanctions — and (b) a small dispatcher per executor that
receives work items over `channel_c` and runs them.

What this buys: any actor can originate a work item for any other; the initiator is no longer a
control bottleneck; ordering and interleaving are decided at runtime within the envelope the
solve declared legal. What it preserves: everything in §3.2. Nothing about the PSS semantic model
changes — this is a code-generation strategy, and arguably conformant today.

This is the highest-value, lowest-risk step and it should be proven first.

### 5.2 Tier 2 — Autonomous actors with local generation (for open-ended traffic)

For genuinely unbounded traffic, each actor runs a loop: pick a behavior, solve its parameters
locally, execute, repeat — Threadmill's architecture. Here §3.2's loss is real and must be
accepted rather than papered over. The mitigation is **not** cross-actor constraints; it is
shared *target-time services* — a runtime address allocator with free, a runtime resource
manager, a shared entropy/seed service — plus behavioral-coverage monitors (§6.4) as the
correctness mechanism in place of solve-time guarantees.

Footprint is managed by the pre-generated-data pattern: solve time emits a parameterized *corpus*
of pre-solved scenario fragments; the target-side engine selects and instantiates from it. This
keeps the on-target solver small or absent while preserving constraint fidelity, and gives
effectively unbounded runtime variety.

### 5.3 The insight worth separating out

Discussions of this topic routinely conflate two independent questions:

1. **Who decides what happens next?** (decision topology — orchestration vs. choreography)
2. **Who solves?** (generation topology — host vs. target vs. hybrid)

They are orthogonal, and §5.0 pins where PSS sits today: centralized on both. Tier 1 moves only
*dispatch* to the edge — peer-originated work items, runtime interleaving — while decisions and
solving stay central. That already delivers most of what the proposal is after: actors interact
peer-to-peer, the initiator stops being a control bottleneck, and nothing in §3.2 is lost.

Recognizing this decouples the attractive part of the proposal from its expensive part.

---

## 6. Overlooked opportunities

1. **Prototype it in standard 3.1, today.** `channel_c` + `export target action` + `executor()`
   compose into request/response RPC with no language extension. Building `actor_pkg` as a
   library and discovering where it breaks is far cheaper than standardizing syntax first, and it
   produces exactly the evidence a WG proposal needs.

2. **Executor traits as runtime service discovery.** Traits are already a structured capability
   description consulted by a matching algorithm. Lifting trait matching from solve time to target
   time ("route this work item to any actor whose trait satisfies *C*") is a small conceptual step
   with a large payoff, and reuses syntax and semantics users already know.

3. **`get_context()` is already an actor address.** The naming/addressing problem that every
   distributed system has to solve has a partial answer sitting in §21.7.1.1.1, currently used
   only for export-function context.

4. **Behavioral coverage (Clause 16) is the right checker for this, and appears to be the most
   overlooked asset.** Monitors and `cover` statements specify and observe *traces* — which is
   precisely what you can still assert about a system whose schedule you no longer control. This
   is the MPST "runtime monitoring" answer, and PSS already standardized it. Pairing dynamic
   multi-actor execution with monitor-based conformance checking is the strongest available reply
   to §3.2 and §4.3, and it should be central to the proposal rather than an afterthought.

5. **Mutable component attributes (new in 3.1, §9.1.6)** give each actor durable local state —
   the state field of an actor in the actor-model sense.

6. **Heterogeneous target execution units (§21.8)** mean "actor" already spans cores, BFMs, and
   host-side processes in different languages. The system-of-systems framing the proposal wants is
   already licensed by the standard; it mostly needs to be said out loud.

---

## 7. Open issues

Ordered by how likely each is to kill the effort.

1. **Reproducibility and replay.** No standard seed-derivation rule, no trace format, no replay
   semantics. Recommend a hierarchical seeding rule (master seed → per-executor seed derived from
   executor identity → per-request seed derived from request sequence) so one number reproduces a
   run, plus a standardized event trace. Without this, failures in long-running multi-actor runs
   are not reducible and the feature is unusable regardless of how elegant the mechanism is.

2. **Loss of cross-actor constraint solving (§3.2).** Structural, not incidental. Tier 1 avoids
   it; Tier 2 must replace it with runtime services plus monitors. Any proposal must say which
   tier it is in.

3. **Deadlock.** Blocking channel ops + cooperative multitasking + no preemption on bare metal.
   Needs at minimum timed channel operations and a standard watchdog/timeout concept;
   better, a projectability check on the model (per §4.1) that rejects protocols where an actor
   cannot locally determine its next move.

4. **Runtime address allocation with free (§3.3).** Explicitly outside the standard today.
   A continuously-running exerciser cannot use a monotone allocator. This is a concrete gap with
   a concrete fix and it is likely underestimated.

5. **Termination and progress.** What does "the test passed" mean for an open-ended scenario?
   Needs a standard stop condition, a distributed quiesce/drain protocol, and a definition of
   coverage-of-a-process.

6. **Distributed failure semantics.** §21.3 error reporting is local. If one actor faults or an
   assertion fires, what happens to its peers, to in-flight requests, and to shared resources?
   Undefined.

7. **Scheduler fairness.** §20.8 already imposes a cooperative/preemptive multitasking obligation
   on the tool. Many actors × long runtimes amplifies it, and the standard says nothing about
   fairness, priority, or starvation.

8. **Target-time coverage.** Coverage is defined over solve-time value selection. Move selection
   to runtime and coverage must be sampled on target and exported back. Not standardized.

9. **Conformance and portability.** If the dispatch protocol is tool-private, tests stop being
   portable across tools in exactly the scenarios that are most expensive to write. The wire
   contract between actors likely has to be standardized, or at least the PSS-visible interface
   to it.

10. **What are the intermediate steps between Tier 1 and Tier 2?** This is a design question, not
    a risk, but it is probably where the real work is. Given §5.0, the program is "move decision
    authority outward in increments." Candidate increments, roughly in order of increasing cost:
    - **Runtime selection among pre-solved alternatives.** The solve emits a set of legal
      choices; the actor picks one at runtime. Decision authority moves, constraint fidelity does
      not — every alternative was solved globally. This is the pre-generated-data pattern (§4.2)
      applied to control rather than data, and it may be the single highest-value increment.
    - **Runtime executor routing.** Keep the solve's decision about *what* runs; defer *where* it
      runs to a trait match evaluated at target time (§6.2). Cheap, and directly serves the
      "actors as interchangeable engines" goal.
    - **Runtime iteration counts / repetition.** Let a globally-solved scenario fragment be
      replayed an unbounded number of times with re-randomized parameters drawn from a
      solve-time-computed envelope. This is the minimum needed for "long-running," and it may not
      require any decision authority to move at all.
    - **Local solve within a globally-solved envelope.** The solve fixes the inter-actor
      relations; each actor solves its own intra-action details locally. Loses only intra-action
      cross-actor constraints — a much smaller loss than §3.2's full one.

    Each of these can be evaluated independently against the guarantees it costs. Enumerating
    them is more useful than debating Tier 1 vs. Tier 2 as a binary.

---

## 8. Recommendation

1. Build `actor_pkg` on standard 3.1 primitives (`channel_c`, `export target action`,
   `executor()`) and stand up a Tier-1 demonstrator: one solved scenario, per-action-type
   generated behaviors, per-executor dispatcher, peer-originated work items. Measure what breaks.
2. In parallel, settle the reproducibility story — hierarchical seeding plus a trace/replay
   format. Treat it as a gating requirement, not a follow-on.
3. Pair the demonstrator with Clause 16 monitors from day one, so the correctness argument is
   trace-based rather than schedule-based.
4. Rather than jumping from Tier 1 to Tier 2, work the increments in §7.10 — each moves a
   specific piece of decision authority outward at a known cost. "Runtime selection among
   pre-solved alternatives" is the one to try second; it moves real decision authority to the
   edge while costing nothing in constraint fidelity.
5. Only then take Tier 2 (autonomous local generation) to the WG, scoped explicitly as a separate
   capability with acknowledged loss of cross-actor constraint solving, and with the runtime
   address-allocator gap addressed.
6. Anything that would make the inter-actor protocol tool-private should be treated as a
   portability regression and resisted.

---

## 9. Distributed scenarios as communicating PSS models

This section supersedes the framing in §5 rather than extending it. §5 asked "how much decision
authority can move to the edge?" and treated the loss of cross-actor constraint solving (§3.2) as
a cost to be minimized. The model-composition architecture reframes that loss as **encapsulation**
— which is a different thing entirely, and a much better answer.

### 9.1 The proposal

A distributed scenario is composed of multiple communicating PSS models. Each model:

- owns and manages its **local resources** — its own pools, address space, and solve domain;
- exposes an **interface** of operations it can perform;
- solves its own internals, seeing only its own scope.

A **super-model** composes sub-models by treating each one as a **resource**. `lock` gives "one
operation at a time on this sub-model"; `share` permits concurrent operations on models that
support them. The super-model's solver reasons about *which sub-model does what, when* — the
interaction topology — without reasoning about any sub-model's internals.

### 9.2 Why this is the right shape

**It is the established answer to scale in every adjacent field.** Compositional / assume-guarantee
reasoning in formal verification; bounded contexts in distributed software; IP-level vs.
integration-level verification in hardware. None of these scale by making the global analysis
smarter; they scale by declaring boundaries and reasoning per-boundary. Flat global solving over a
system-of-systems is the thing that does not scale, and it is what PSS does today.

**The session-types literature has the compositionality result.** The Hybrid MPST work
([PACMPL 2023](https://dl.acm.org/doi/abs/10.1145/3586031)) is precisely a theory of composing
*subprotocols* that interact with each other, with a compatibility relation between them and a
semantics-preserving composition. §4.1 noted that PSS's static projection is endpoint projection;
the compositional version of that theory is the formal grounding for communicating PSS models, and
it says the interesting obligation is a **compatibility check between interfaces**, not a global
analysis.

**§3.2 stops being a defect.** The standard says constraints and resource allocation are not
considered across exported-action call chains (§20.10.1(c)), and each call infers an independent
action tree (§20.10.1(b)). Against ad-hoc RPC that is a serious loss — unstructured, invisible,
and impossible to reason about. Against *declared model boundaries* it is exactly the semantics you
want: the sub-model's solve is independent **because it is encapsulated**, and the interface says
what crosses. Same mechanism, opposite verdict, purely because the boundary became declared rather
than incidental.

**Deadlock becomes tractable again.** §3.6 worried that peer-to-peer blocking reintroduces
distributed deadlock. Under this architecture, sub-model acquisition happens through the
super-model's *resource pool*, so the super-model's solver sees every acquisition and can schedule
them consistently — the same guarantee §21.7.2.4 already provides for executor resources. Deadlock
freedom comes back for free at the composition layer. This is a strong argument for
sub-model-as-resource over sub-model-as-free-agent.

**"One operation at a time" is a good default.** It makes each sub-model's internal solve
sequential and self-contained, so no intra-model concurrency reasoning is needed at the super
level. `lock` expresses it exactly; `share` is the escape hatch.

### 9.3 PSS already has the pattern, one level down

§21.7.2.4 ("Executor resources") establishes that *an execution agent can be a resource*: a
resource object derived from `executor_claim_s` functions as an executor claim for any action that
locks or shares it, so that `lock my_core_r core;` means "this action gets a core exclusively for
its duration."

The proposal is the same pattern applied to a coarser unit: from "an executor is a resource" to "a
**model** is a resource." The semantics, the syntax, and the user's mental model all carry over.
That continuity is worth a lot — it is an argument that this is a natural extension of PSS rather
than a bolt-on.

### 9.4 Exported actions as the entrypoint

Treating exported actions as functions is what makes the interface concrete, and it works better
than it first appears.

§20.10.3 already specifies that an exported action *is* exposed as an ordinary function in the
target language, in a namespace derived from the component (`namespace comp { void A1(unsigned
char mode); }`). So:

- A sub-model's **interface is its set of exported actions**, which appear to the outside world as
  plain functions.
- The super-model declares them as imported functions and **calls them from an action's `exec
  body`** — no new invocation syntax required.
- §20.10.1(b)'s "independent tree of actions, components, and resources" per call *is* the
  sub-model's local solve. The mechanism the flat architecture needed to work around is the one
  this architecture needs to work.
- Composition is **symmetric**: a sub-model that needs to call back into the super-model or into a
  peer imports *their* exported actions. Each model exports its interface and imports its peers'.
  That is exactly the endpoint-type picture — a model's local type is its exports (inputs) plus its
  imports (outputs).

**Blocking is the semantics you want here.** An action has duration, so an exported-action-as-
function call should block for that duration; combined with `lock`, that gives "the sub-model is
busy for exactly as long as the operation takes" with no extra machinery. Concurrency across
sub-models is then expressed where it belongs — in the super-model's activity (`parallel`,
`schedule`), with each concurrent branch locking a different sub-model. §20.8's cooperative
multitasking obligation makes this work. This is a genuinely clean result: **you do not need
asynchronous RPC**, because the concurrency is already expressible at the composition layer.

Two concrete gaps in using exported actions this way, both small:

1. **No results come back.** The generated signature in §20.10.3 is `void A1(unsigned char mode)`,
   and Annex D.1(c) says all parameters of export functions "are treated as input." The underlying
   `function_parameter_list_prototype` production does admit `output`/`inout` directions, so the
   grammar is mostly there — but the mapping rules are not. An operation you cannot get a result
   from is a weak interface primitive. **Exported actions need output parameters or a return
   value**, and this looks like the single smallest, highest-value language change in the whole
   proposal.
2. **Only data crosses, not scenario structure.** Parameters are restricted to scalars and const
   structs; you cannot pass a flow object or an action handle. That is the right boundary for
   encapsulation, but it should be stated as a deliberate design decision rather than discovered as
   a restriction.

### 9.5 The structural blocker: PSS has exactly one root

§9.1 of the LRM (line 8259 of the draft): *"There can only be one root component in any valid
scenario."* `pss_top` is implicitly instantiated and is the root of the one component instance
tree; §17 elaboration and §20.1 exec ordering are all defined relative to that single root.

**Multiple communicating models means multiple roots, and the standard has no concept of that
today.** There is no notion of a PSS model as a unit of composition, no separate elaboration, no
inter-model reference, no way to instantiate model *A* inside model *B*'s scope. Everything else in
§9 is reachable by library and methodology; this one is not.

This is the central language gap for the architecture, and it is worth scoping precisely, because
there are two quite different ways to close it:

- **Multiple elaboration units, composed externally.** Each model is elaborated independently with
  its own `pss_top`, and composition happens at the realization layer via imported/exported
  functions. Minimal LRM change — arguably just a statement that a system may comprise several
  independently-elaborated models plus a way to name them. Loses any super-model *solve* over
  sub-models, so "sub-model as resource" would have to be realized by convention.
- **A first-class model/subsystem construct.** The super-model instantiates sub-models as typed
  entities with declared interfaces, and the super-model's solver treats them as resources with
  `lock`/`share`. Real LRM work, but this is the version that delivers §9.2's benefits — especially
  deadlock freedom at the composition layer, which requires the super-model's solver to actually
  see acquisitions.

The second is the one worth wanting. The first is the one to prototype against.

### 9.6 Models and executors are not the same thing

Worth being crisp about, because conflating them will cause trouble:

- An **executor** is *where code runs* — a realization concept (§21.7.1).
- A **model** is *a scope of decision-making and resource ownership* — a modeling concept.

They may coincide (one model per core) but need not. A sub-model might span several executors (a
subsystem with a core, a DMA engine, and a BFM); several sub-models might share one executor. Keeping
these orthogonal is what lets executors stay exactly as they are while the new concept is added
above them.

### 9.7 Reconciling hierarchy with peer-to-peer

There is a real tension between "super-model manages sub-models as resources" (hierarchical,
orchestrated) and the original goal of peer-to-peer interaction. If sub-models talk to each other
directly, the super-model's resource reasoning is unsound — it is scheduling against a picture that
omits real traffic.

The resolution is the one both SoC interconnect and service meshes use, and it is what the saga
literature means by hybrid (§4.3): **structure is hierarchical and declared; traffic is
peer-to-peer within declared channels.** A peer channel between two sub-models is declared at the
super-model level — a binding, much like PSS pool binding — so the super-model knows the channel
exists and which models it couples, even though it does not sequence individual messages on it.
The super-model solves *topology and exclusion*; peers handle *flow*.

This gives a principled answer to "who is the initiator": the super-model is the initiator for
lifecycle, resource arbitration, and structure, and is *not* in the path of ordinary inter-model
traffic. That is precisely the split the original proposal was reaching for.

### 9.8 Open issues specific to this architecture

These are in addition to §7, and several of §7's issues soften considerably under this architecture
(deadlock, per §9.2; constraint scoping, per §9.2).

1. **Cross-cutting resources.** Sub-models own local resources, but some resources genuinely span
   models — shared memory, interconnect bandwidth, a shared DMA. Needs a delegation model:
   the super-model grants a *budget* or *partition* downward, and sub-models allocate within it.
   **Address spaces are the acute case** — §21.10–21.12 treat address space as essentially global,
   so per-model partitioning with downward delegation is required and does not exist. This
   interacts badly with §7.4 (no runtime free), and together they are probably the largest piece of
   real work.
2. **What is the contract?** Assume-guarantee reasoning needs declared assumptions and guarantees.
   PSS can declare an interface's *behavior* (exported actions) but not properties like "never more
   than N outstanding transactions" or "assumes the clock is running." Without contracts, the
   compatibility check of §9.2 has nothing to check. This is a genuine language gap and the one
   most likely to be underestimated.
3. **Compositional coverage.** If each sub-model measures coverage locally, what does system-level
   coverage mean? This is the IP-to-SoC coverage reuse problem, which is unsolved in general.
   Clause 16 monitors (§6.4) are the most promising angle — system-level intent expressed as traces
   over inter-model interface events, independent of sub-model internals.
4. **Recursion.** Can a sub-model itself be a super-model? For systems-of-systems it must be, but
   then resource delegation, seeding, and coverage aggregation all have to compose recursively.
   Worth deciding early; retrofitting recursion is painful.
5. **Model instances vs. types.** Eight identical accelerator subsystems should be eight instances
   of one sub-model type, mapping naturally onto a resource pool of size 8. This needs to be in the
   design from the start, and it interacts with how per-instance state and seeds are derived.
6. **Independent evolution.** A real benefit of the architecture — sub-models developed and
   verified separately — only materializes if interfaces are versioned and compatibility is
   checkable. Otherwise composition silently rots.
7. **Where does the super-model's solve run?** If sub-models are resources it holds, the
   super-model has a live solver making scheduling decisions. On a long-running system test, is
   that a host process, a designated core, or a service? This is §5.3's "who solves" question
   returning at the composition layer, and it is a deployment question the architecture does not
   answer by itself.

### 9.9 Revised recommendation

The §8 sequence still holds for the near term — `actor_pkg` on 3.1 primitives, reproducibility as a
gating requirement, monitors from day one. But the target architecture should be model composition,
not the Tier-2 free-agent model, and that changes what the prototype is *for*:

- Build the Tier-1 demonstrator (§5.1) as before, but structure it as **two models with a declared
  interface** rather than one model with distributed dispatch. Use exported-actions-as-functions as
  the interface and convention-based `lock` for exclusion. This exercises §9.4 and §9.5's first
  option using only standard 3.1.
- Take **output parameters / return values on exported actions** (§9.4 gap 1) to the WG early. It
  is small, it is independently justifiable, and every version of this architecture needs it.
- Treat **multi-root / model-as-unit-of-composition** (§9.5) as the central standardization
  question, and the **contract language** (§9.8.2) as its necessary companion. Neither is small, and
  the second is easy to forget until the first is done and the composition turns out to be
  uncheckable.
- Scope **per-model address space partitioning with downward delegation** (§9.8.1) as a separate
  workstream. It is the piece most likely to be discovered late.

## 10. Hierarchical scenarios — the worked shape

§9 argued for model composition in the abstract. This section works the concrete shape, which
turns out to be both simpler and more nearly legal-today than §9.5 concluded.

### 10.1 The structure

Take two racks of GPUs. Each rack:

- is a PSS model over its own components — GPUs, NICs, switches, local memory;
- defines a **set of root actions**, each one an operation the rack can perform (run a collective,
  stream a dataset, run an inference batch);
- each such root action is a *compound* action whose activity comprehends the scheduling
  relationships **within** the rack — flow objects between GPU and NIC, resource pools over
  engines, local address space, executor assignment across the rack's cores and BFMs.

The super-scenario does not model any of that. It has an activity whose leaves are **function
calls** that activate behaviors on a named rack. Each call is implemented as a randomized
behavior: the same call, made twice, produces two different legal realizations.

### 10.2 "A series of root actions" is already the LRM's own model

This is the part that makes the architecture fit rather than fight the standard. The draft's
glossary (§3, "root action") says:

> *"root action: An action designated explicitly as the entry point for the generation of a
> specific scenario. **Any action in a model can serve as the root action of some scenario.**"*

The one-root-action rule is therefore **per scenario, not per model**. A model is already,
conceptually, a menu of potential entry points; a tool run just picks one. Nothing needs to change
for a rack model to *have* a set of root actions — it already does, implicitly, and the rack team
already benefits from that when they run each operation standalone.

What export actions add is making that menu **externally selectable**: §20.10.1(b)'s "each call
into an export action infers an independent tree of actions, components, and resources" is
precisely "invoke root action *X* and solve its scenario." §9.4 called this the entrypoint; it is
better than that — it is the standard already describing per-invocation root-action selection with
an independent solve, in so many words.

This substantially softens §9.5. The blocker there was framed as "multiple roots cannot coexist."
The real requirement is weaker: **one component tree per model, many root actions per model,
selected per invocation.** That is what PSS already does.

### 10.3 Why the hierarchy pays — the combinatorial argument

This is the case worth making to the WG, because it is not merely an ergonomics argument.

If rack *A*'s scenario space is |A| and rack *B*'s is |B|, a monolithic two-rack model presents the
solver with |A|×|B|, and almost all of that product is *independent* — the cross terms carry no
information. Hierarchically, you solve a small super-scenario over the interface, then |A| and |B|
separately, once per invocation. The saving is not constant-factor; it is the difference between a
product and a sum.

The stronger point is that the monolithic model does not merely get slow — **it gets unwritable.**
Someone has to author a single model naming every component in both racks, and re-author it for
three racks. The hierarchical version scales by instantiating another rack resource. For "systems
larger than a multi-core SoC," that authoring cliff arrives well before the solver cliff.

And note where the complexity actually lives: it is *intra*-rack, which is exactly where PSS's
flow objects, resource pools, and scheduling semantics earn their keep. Inter-rack is comparatively
simple — which operation, on which rack, in what order, with what data. Monolithic solving spends
its effort on the part that needed it least.

### 10.4 Randomized behaviors behind a function-call interface

"Those functions are implemented as randomized behaviors" is the property that makes long-running
traffic work, and it is worth naming explicitly because it resolves a problem §5 struggled with.

A bounded, statically-solved super-scenario can drive **unbounded variety**, because each
invocation re-randomizes its own realization. §7.10 proposed "runtime selection among pre-solved
alternatives" as the best increment; this is strictly better — the alternatives are not enumerated
at solve time, they are *generated* at invocation time, from a model that already knows how to
constrain them. The rack is the engine the original proposal asked for in §1: instructed at the
level of intent, randomizing the realization.

It also lands the intent/realization split PSS already talks about on a natural boundary.
`run_collective(ALLREDUCE, msg_size, participant_mask)` is coarse intent; which GPUs, in what
order, over which links, with what local buffers, is realization the rack owns.

### 10.5 What this is expressible with today

Working the example through, the pattern needs no new syntax:

**Rack model** (independently elaborated, independently runnable standalone):

```
component rack_top {
  gpu_c gpus[8]; nic_c nics[4]; ...
  pool link_r links; bind links *;
  action collective {            // one of several root actions
    rand collective_op_e op;
    rand int in [64..1048576] msg_size;
    activity { /* coordinates gpus/nics: flow objects, resources, executors */ }
  }
  action stream_dataset { ... }
  action inference_batch { ... }
}
package rack_api {
  export target rack_top::collective(int op, int msg_size);
  export target rack_top::stream_dataset(int bytes);
}
```

**Super-model** (its own elaboration; racks appear as resources + imported functions):

```
component super_top {
  pool [2] rack_r racks;  bind racks *;
  import target function void rack_collective(int rack_id, int op, int msg_size);

  action run_collective {
    lock rack_r rack;                    // one operation at a time per rack
    rand collective_op_e op;
    rand int in [64..1048576] msg_size;  // must mirror the rack's contract — see 10.6
    exec body {
      rack_collective(rack.instance_id, op, msg_size);
    }
  }
  action test {
    activity { schedule { replicate (20) { do run_collective; } } }
  }
}
```

Everything here is standard PSS 3.1. The super-model's `lock` gives per-rack exclusion and its
solver sees every acquisition (so §9.2's deadlock-freedom result holds); the blocking imported call
gives "the rack is busy for exactly the operation's duration" (§9.4); concurrency across racks is
expressed in the super-activity where it belongs.

So the honest revised verdict is: **the hierarchical architecture is largely prototypable in
standard PSS 3.1.** What is genuinely missing is smaller than §9 implied, and reduces to the three
items below.

### 10.6 The three real gaps, made concrete by this example

**1. Results cannot come back (§9.4 gap 1) — now blocking, not merely valuable.** For GPU racks the
super-model needs to know: did the collective complete, what bandwidth was observed, which rack is
now congested. Without return values the super-scenario is write-only — it can dispatch but never
react, which rules out the adaptive long-running traffic that motivated the whole exercise.
§20.10.3 generates `void A1(...)` and Annex D.1(c) treats all export parameters as input, though
the underlying `function_parameter_list_prototype` does admit `output`/`inout`. This should be the
first WG item.

**2. Interface contracts — the minimum viable version is small.** This is the real cost of
encapsulation, and the example makes it sharp. The super-model's solver has no visibility into rack
feasibility. If it asks a rack for an operation the rack cannot satisfy, the failure surfaces at
*runtime*, inside the rack's solve, deep into a long-running test — where a monolithic solve would
have caught it up front. Note the duplicated `msg_size in [64..1048576]` in §10.5: today that
constraint must be hand-mirrored on both sides of the interface, with nothing checking that the
copies agree.

The full assume-guarantee contract language of §9.8.2 is a large undertaking. But the *minimum
viable* version is much smaller and worth separating out: **the ability to attach constraints to an
imported/exported function's parameters**, declared once with the interface and enforced in the
caller's solve. That alone converts a class of runtime solve failures into solve-time errors, and
is a plausible near-term proposal.

**3. Delegated address space (§9.8.1) — unavoidable here.** A cross-rack allreduce needs a shared
buffer. Each rack owns its local address space; the super-model must own the inter-rack space and
delegate a descriptor downward, with each rack allocating locally within its grant. Parameters are
restricted to scalars and const structs, so a descriptor passes fine — but §21.10–21.12 have no
notion of a partitioned or delegated space, and §7.4's missing runtime free compounds it on a
long-running test. This is the largest piece of real work and the one most likely to be found late.

### 10.7 Consequences worth deciding early

- **Cross-rack traffic.** GPU-direct / RDMA between racks is real traffic the super-model must know
  exists — for exclusion and bandwidth accounting — but should not sequence message-by-message.
  §9.7's answer applies directly: declare the peer channel at the super level as a binding; the
  super-model solves topology and exclusion, the racks handle flow.
- **Reacting to results makes the super-model dynamic.** A statically-solved super-activity with
  randomized leaves is the sweet spot (§10.4) and needs nothing new. But "rack A reported
  congestion, so do something different" requires runtime decision-making at the super level, and
  that is where §7's reproducibility and §5.0's decision-authority questions come back. Worth being
  deliberate about whether the super-scenario is a fixed plan with randomized realization, or an
  adaptive controller — they have very different costs.
- **N racks, not 2.** The payoff compounds: a pool of N rack resources with `replicate`/`foreach`
  over them. The design should be validated at N, since two racks is the one case where a
  monolithic model is still writable and the argument therefore looks weakest.
- **Rack-internal executors stay invisible.** Each rack does its own executor assignment; the
  super-model never sees them. This reinforces §9.6 — models and executors are orthogonal — and
  keeps §21.7 untouched.

### 10.8 Revised recommendation

Replacing §9.9:

1. **Build the two-rack demonstrator in standard PSS 3.1**, per §10.5 — rack model with several
   exported root actions, super-model with rack resources and imported functions. No language
   changes. This is now the primary experiment, and its purpose is to find what §10.6 missed.
2. **Take output/return values on exported actions to the WG first** (§10.6.1). Small, independently
   justifiable, and every version of this architecture is write-only without it.
3. **Propose interface parameter constraints** (§10.6.2) as the minimum viable contract. Much
   smaller than full assume-guarantee, and it removes the hand-mirroring in §10.5.
4. **Scope delegated address space as its own workstream** (§10.6.3), jointly with runtime free
   (§7.4).
5. Keep reproducibility (§7.1) gating and Clause 16 monitors (§6.4) central — under encapsulation,
   trace-level conformance at the interface is the main thing you *can* still check system-wide.
6. Validate at N racks, not 2 (§10.7).

## 11. The super-graph as parameter coordinator

§10 framed the super-model as dispatching operations. The sharper framing is that the super-graph
**coordinates the parameters of N concurrent, independently-randomized per-rack traffic scenarios
so that they interact.** The interaction is *emergent from correlated parameters*, not sequenced
message-by-message. This changes several conclusions and produces the strongest form of the
argument.

### 11.1 Why this is the best version of the case

Put the solver where its value-per-unit-work is highest.

- The **super-model** is a *small* constraint problem — a few dozen parameters across N racks —
  whose variables are **densely correlated** and where each correlation is exactly the thing that
  makes the test interesting.
- The **per-rack models** are *large* constraint problems that are **nearly independent** of each
  other.

Solving them together spends almost all the solver's effort on the large, uncorrelated part
(§10.3's |A|×|B|) and gains nothing for it. Splitting them is not a compromise forced by scale —
it is putting the solver on the part of the problem that actually needs a solver.

And it targets precisely the thing hand-written multi-agent stress cannot do. Configuring N traffic
generators and letting them collide is standard practice; what is hard, and what a constraint
solver is uniquely good at, is *correlating the profiles* so the collisions are interesting rather
than accidental — overlapping-but-not-identical address ranges, aggregate injection deliberately
oversubscribing the fabric, exactly two racks contending for one region. That is the super-graph's
job, and it is a job nothing else in the verification stack does well.

### 11.2 What the super-graph actually leans on

A consequence worth noticing: under this framing the super-model uses PSS's **data constraint
language** far more than its activity/flow semantics. Its content is mostly a structured parameter
vector per rack plus global constraints over the collection — and the aggregate forms this needs
are already there (`sum()` reductions over arrays, `foreach`, cardinality via summed booleans):

```
rand rack_profile_s profiles[N];
constraint {
  // aggregate injection oversubscribes the fabric
  profiles.sum(p) { p.inject_gbps } > FABRIC_CAPACITY_GBPS * 12 / 10;
  // exactly two racks contend for the shared region
  profiles.sum(p) { p.targets_shared_region ? 1 : 0 } == 2;
  // at least one rack idle, to expose asymmetry
  profiles.sum(p) { p.idle ? 1 : 0 } >= 1;
}
```

The activity above this can be thin — often just "run all N concurrently for the duration." That is
a useful thing to know when deciding which language features matter: for the super-model, algebraic
constraints over arrays (§13.1) carry the weight, not flow objects.

### 11.3 The concurrency shape changes — and §9.2's deadlock result weakens

§10.5 modelled racks as resources acquired and released per operation (`lock rack_r rack;`), which
is what gave §9.2's deadlock-freedom result: the super-model's solver sees every acquisition.

Under this framing each rack runs **one long traffic scenario for the duration of the test**, all N
concurrently. The activity is closer to:

```
activity { parallel { foreach (p: profiles) { do run_rack_traffic with { profile == p; }; } } }
```

There is little dynamic acquisition left, so:

- **The `lock`-based exclusion framing is less load-bearing here.** It is still the right way to
  represent a rack that can only do one thing at a time, but if racks run continuously it is
  arbitrating much less.
- **§9.2's deadlock-freedom argument correspondingly weakens.** It was contingent on the
  super-model's solver seeing acquisitions; with few acquisitions, it guarantees correspondingly
  less. Deadlock risk shifts to the *cross-rack traffic* (§9.7), which the super-model does not
  sequence. This should be tracked as a real risk rather than assumed solved.
- **Return values (§10.6.1) shift in character.** They matter less for "dispatch in a loop and
  react" and more for (a) *checking* — did the rack observe expected throughput — and (b) *phased*
  scenarios, where a long test runs profile 1, then profile 2 with adjusted parameters. Still
  important; the urgency argument is different from §10.6.

### 11.4 Timing correlation: the data plane, not the scheduling plane

"End up interacting" requires overlap **in time** — rack A's burst must land while rack B's
collective is in flight. But PSS's scenario semantics are deliberately *relational*, not timed:
§13.2 scheduling constraints express ordering relations (before, after, parallel, overlap), and
there is no notion of duration, delay, or rate in the scenario model.

The workable answer is that **timing coordination happens in the data plane**: the super-model
solves `start_delay_us`, `burst_period`, `ramp_rate`, `duration` as ordinary `rand` scalars in each
rack's profile, and each rack *realizes* them. This works today and needs nothing new.

It is worth being explicit about it as a design decision, though, because it means:

- The correlations that matter most are expressed as arithmetic over numbers, not as PSS scheduling
  relations — so the super-graph's "graph" is doing less work than the name suggests (§11.2).
- Whether the intended temporal overlap *actually occurred* is not checkable from the model. It
  depends on each rack honoring its timing parameters, which is a contract obligation (§11.5) and
  an observation problem (§11.6).

Proposing timed scheduling relations for PSS would be a much larger undertaking and is probably the
wrong lever; the data-plane answer is adequate and honest.

### 11.5 Aggregate constraints need a *quantitative* rack contract

This upgrades §10.6.2 in an important way. A constraint like "aggregate injection exceeds fabric
capacity by 20%" requires the super-model to know **each rack's injection rate as a function of its
parameters**. That is not a feasibility guard on parameter ranges — it is a *summary model of the
rack's resource behavior*, exposed at the interface.

So the contract has two tiers, and they are quite different in cost:

1. **Parameter-range constraints** (§10.6.2) — "`msg_size in [64..1M]`", declared once with the
   interface. Small, near-term, removes the hand-mirroring in §10.5.
2. **Quantitative behavioral summaries** — "this profile injects approximately *f(params)* Gbps and
   holds *g(params)* buffers." This is the assume-guarantee *guarantee* side made numeric, and it
   is what aggregate correlation actually requires. Substantially harder: it must be accurate
   enough to be useful, conservative enough to be safe, and maintained alongside the rack model.

Tier 2 is the part most likely to be underestimated, because tier 1 looks like it solves the
contract problem and does not. Without it, global constraints like the `sum()` example in §11.2 are
written against numbers the super-model has no principled way to know.

### 11.6 The central open issue: emergent interaction is not verifiable from the model

This is the hardest consequence of the reframing and, I think, the most important open issue in the
document.

If interaction is *emergent from correlated parameters*, then:

- **You can cover the parameter space** — covergroups over the super-model's solved profiles.
  Available today, cheap, and it tells you what you *asked for*.
- **You cannot cover the interaction** from the super-model, because whether the racks actually
  collided is a runtime property observable only inside the racks and on the fabric between them.

The gap between those two is the whole risk. You constrained address ranges to overlap in order to
provoke contention; contention may never have occurred — wrong timing, a rack that serialized
internally, a fabric that absorbed it. The super-model's coverage reports success either way. This
is the familiar "did my stimulus hit the corner" problem, but the encapsulation boundary makes it
structurally worse: the super-model is *definitionally* blind to the thing it is trying to cause.

Two partial answers, and they should be designed in from the start rather than retrofitted:

- **Interface-level trace monitoring** (Clause 16). Specify system intent as monitors over events at
  the inter-rack interface — cross-rack traffic, shared-region accesses, completion orderings. This
  is the most that can be checked without breaking encapsulation, and §6.4 argued it should be
  central anyway.
- **Feedback from realization to generation.** Racks report what actually happened — observed
  contention, achieved bandwidth, queue occupancy — and system-level coverage is measured on *those
  reports* rather than on solved parameters. This closes the loop and is, I suspect, the genuinely
  overlooked opportunity here: it turns the super-model from an open-loop stimulus shaper into
  something that can be told whether its shaping worked, and eventually into something that adapts
  (§10.7's adaptive-controller branch, with §7.1's reproducibility caveats).

This also raises a question the architecture does not answer: **what does "coverage of a
hierarchical scenario" mean?** Per-rack coverage composes poorly (§9.8.3), parameter coverage is
necessary but insufficient, and interaction coverage requires cross-boundary observation. Worth
naming as an open problem rather than assuming it falls out.

### 11.7 What this changes for the demonstrator

Refining §10.8:

1. The demonstrator should be **N racks running concurrent long traffic scenarios under
   super-solved profiles**, not a dispatch loop of discrete operations. That exercises the actual
   proposition.
2. Make the super-model's content a **`rand` profile array with aggregate constraints** (§11.2) and
   keep the activity thin. This tests whether `sum()`/`foreach` over profile arrays expresses the
   correlations that matter — a cheap, early, falsifiable check.
3. Include **timing parameters in the profile** from the start (§11.4) and verify that intended
   temporal overlap is achievable purely as data.
4. **Instrument the interaction, not just the parameters** (§11.6). Have racks report observed
   contention and measure coverage on the reports. Even a crude version answers the question that
   decides whether the whole approach works: *do correlated parameters reliably produce the
   intended interaction?* If the answer is no, that is the finding, and it is better to have it
   from a two-rack prototype than after standardization.
5. Track cross-rack deadlock as an open risk rather than relying on §9.2 (§11.3).

## Sources

- [Hybrid Multiparty Session Types: Compositionality for Protocol Specification through Endpoint Projection (PACMPL 2023)](https://dl.acm.org/doi/abs/10.1145/3586031) · [full version, arXiv:2302.01979](https://arxiv.org/abs/2302.01979) · [Mobility Reading Group](https://mrg.cs.ox.ac.uk/publications/hybrid-multiparty-session-types/)
- [Threadmill: A post-silicon exerciser for multi-threaded processors (Adir et al.)](http://www.eecs.umich.edu/courses/eecs578/eecs578.f15/papers/adi11.pdf) · [Semantic Scholar entry](https://www.semanticscholar.org/paper/Threadmill:-A-post-silicon-exerciser-for-processors-Adir-Golubev/f8a3c3b23f0d00eaec8ef3f7ab3f588cc924a6eb)
- [Improving Post-silicon Validation Efficiency by Using Pre-generated Data](https://link.springer.com/chapter/10.1007/978-3-319-03077-7_12)
- [Coyote: actors and controlled concurrency testing](https://microsoft.github.io/coyote/concepts/actors/overview/) · [Industrial-Strength Controlled Concurrency Testing for C# (TACAS 2023)](https://pdeligia.github.io/lib/papers/coyote_tacas23.pdf) · [Model-based Testing Distributed Systems with P](https://www.mydistributed.systems/2021/06/p-language.html)
- [Deterministic simulation testing — Antithesis](https://antithesis.com/docs/resources/deterministic_simulation_testing/) · [Protocol-Aware Deterministic Simulation Testing — TigerBeetle](https://tigerbeetle.com/blog/2026-08-20-protocol-aware-dst/) · [What's the big deal about DST — Phil Eaton](https://notes.eatonphil.com/2024-08-20-deterministic-simulation-testing.html) · [Deterministic Simulation Testing for Our Entire SaaS — WarpStream](https://www.warpstream.com/blog/deterministic-simulation-testing-for-our-entire-saas)
- [Saga Orchestration vs Choreography — Temporal](https://temporal.io/blog/to-choreograph-or-orchestrate-your-saga-that-is-the-question) · [Saga choreography pattern — AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga-choreography.html)
- [RISC-V Test Generation: random and directed stimulus (STING)](https://alpinumconsulting.com/blogs/risc-v/risc-v-test-generation-random-directed-coverage/)
- [Cadence Perspec System Verifier](https://www.cadence.com/en_US/home/tools/system-design-and-verification/software-driven-verification/perspec-system-verifier.html) · [Breker Portable Stimulus](https://brekersystems.com/products/portable-stimulus/)
- PSS 3.1 Public Review Draft, 2026.08.28 — §§16, 20.1, 20.8, 20.10, 21.7, 21.8, 21.9, 21.11
