# The NVMe IO benchmark on bc: implementation plan

Status: **in progress** (2026-10-02). The §9 recommendations were taken as
the decisions when implementation started; D-B8 turned out to be moot (§2).
B1 and B2 are done (committed 2026-10-02). B4's dv-solve
work landed upstream; D-B11 option 2 is done and option 1 (lifted
explanations, learning on in bc) is done. B3 (G2–G6) is done and its gate is
met (committed 2026-10-02); G7 is open and off the model's path. B5 is done:
the unmodified files pass every test (committed 2026-10-02). B6 is in
progress.

**Why this plan.** `nvme_pss_bench/` (untracked, in the pssc root; never
committed) is a cut-down IO model from a real NVMe verification library. Its
README states the job: solve a scenario of N IOs, each IO 10 atomic actions,
fast, for N up to 1000 and beyond, and solve three conformance probes that
need lookahead. It is the first real model the activity work has met, and it
is the "real model" that design D18 asked for before any cone break is
adopted. This plan makes bc run it, correctly and in linear time, by building
general mechanisms. Nothing in it is specific to the benchmark.

**Gate.**

1. Every benchmark test (`bench_seq`, `bench_par`, `bench_rw`,
   `bench_rw_pool`) gets `PASS` from the benchmark's own `check_bench.py` at
   N = 100, 200, 500 and 1000, from the **unmodified** model files
   (`pss/10_*`, `20_*`, `30_*`). The only edit allowed is `BENCH_N` in
   `00_bench_cfg.pss`.
2. All three probes pass on 50 seeds each.
3. Time grows linearly. For `bench_seq`, the time at N = 1000 is at most
   12× the time at N = 100, and N = 1000 runs in under 10 s on one core.
   Peak RSS at N = 1000 is under 1 GB.
4. The values `check_bench.py` does not check also hold: every `op` is a
   member of `op_e`, and the variety report (§7) shows every `(op,
   lba_bytes)` combination the constraints allow.
5. No regressions. Corpus verdicts are unchanged, apart from new tests that
   pass. The unit, progseq and compliance suites keep the baselines in
   `status-2026-10-01.md`, and the SV golden snapshots stay byte-identical.

---

## 1. Baseline (measured 2026-10-01)

### 1.1 What happens today

The front end parses and links the model cleanly, including
`compile if (!compile has(BENCH_N))`. bc refuses it at the first `bind`
(`20_lane_c.pss:135`, "unsupported activity construct ActivityBind"). Flow
objects, pools and resources are Activities P3, which is not built.

To measure the rest, a **values-only variant** was built in the scratchpad.
It keeps the same `io_s` constraints, the same 10-stage compound and the
same `with` constraints, but turns buffers, state and locks into plain
attributes. Getting it through bc hit six more gaps, in this order:

| # | Construct in the model | Where it stops | Phase |
|---|---|---|---|
| G1 | `bind`, buffer/state input and output, `lock`, pools | bc lowering | B5 |
| G2 | `constraint default x == v;`, `default disable x;` | ir-core `collect_solve_problem` (`metadata["untranslated"]`) | B3 |
| G3 | `!rd_verify -> …` (a negated boolean as antecedent) | be-bc `constraints._neg_literals` | B3 |
| G4 | `io.lane == comp.lane_id` (a component attribute in a constraint) | be-bc `constraints._expr` | B3 |
| G5 | `prep.io == io` (equality of two struct attributes) | be-bc `constraints._expr` (`ExprAttribute`) | B3 |
| G6 | `repeat (i : N) { do X with { io.tag == i + 1; }; }` | be-bc `constraints._expr` (`self.i`) | B3 |

### 1.2 Wrong results

**bc never limits a `rand` enum to its declared values**, whether the field
is a top-level action field or a struct leaf, on every seed. In the
values-only `bench_seq` at N = 1000, 2 of 1000 `op` values were members of
`op_e`. `check_bench.py` passes anyway, because it only tests
`op == OP_FLUSH`. The corpus has no `rand` enum test either. Cause: both
places that build solver variables (`constraints.collect_solve_problem` and
`action_tree.TreeBuilder._vars`) take only a width from the leaf's type, and
an enum's width defaults to 32. `ScSolveVar.domain` exists and is never set.

### 1.3 Performance

Values-only variant, one core:

| Run | `bench_seq` per IO | N = 1000 |
|---|---|---|
| bc today | ~0.32 s | about 5 min (projected) |
| bc reusing one compiled solver context (prototype, §3) | ~1 ms | 1.3 s / 145 MB, checker `PASS` |

- **94% of run time is building solver contexts.** Each IO is one cone of
  11 nodes. Each node's `SOLVE_NODE` builds a new `SolveCtx` from the same
  6 KB blob (`interp/activation.py`, `Activation.solve`). The compile takes
  ~28 ms; checkpoint, pin, solve and restore on a compiled context take
  ~0.03 ms. The per-node path (`interp/extern.py`, `NativeBlobBackend`) and
  the native engine (`rt-eng/share/rt/zbc_solver.c`) also compile on every
  solve.
- **dv-solve can hang on `bench_rw`.** Reduced case: one action with two
  `io_s` and `b.slba + b.nlb <= a.slba + a.nlb`. It runs more than 20 s on 2
  of 3 seeds; without that constraint it takes 0.25 s. The hang needs both
  nonlinear `io_s` constraints, `nbytes == nlb * lba_bytes` and
  `n_ptrs == (first_off + nbytes + 4095) / 4096`; removing either one fixes
  it. bc as it is today hangs the same way (27 s to over 60 s on one solve),
  so the cache is not the cause. Copying a struct with 14 field equalities
  (`prep.io == io`) adds about another second per solve.
- `bench_par` scales like `bench_seq` (N = 1000: 1.4 s). The
  `probe_child_constraint` and `probe_grandchild_rw` value problems pass on
  3 seeds each: P1's cone lookahead already handles constraints on children
  and grandchildren.

### 1.4 Why the model fits bc's design

IOs interact only through history that is already settled when a later IO
solves: the `ready` state, slot locks that have been released, and extents
already written. Solved IO by IO with that history pinned, each IO's cone has
a fixed size, so the whole run is linear. A design that solves the whole
scenario jointly grows super-linearly, which is the behaviour the
benchmark's README reports. bc's per-scope cones (P1) and greedy commit with
lookahead (design §5.3) are the right shape. What is missing is the per-solve
constant (B2), solver robustness (B4) and the flow/resource semantics (B5).

Two tests do need real structural decisions, and §6 deals with them:
`bench_rw_pool` (which write each read verifies) and `probe_par_unpinned`
(which lane each stream uses, under DMA-channel limits).

### 1.5 A finding about the benchmark

The benchmark README says `probe_par_unpinned` can be solved "by spreading
streams over lanes, or by ordering the `xfer`s". The second option is not
legal PSS. LRM 11.3.4 a) says the invocation of an action in one parallel
branch "does not wait for the completion of any action in another", and the
resource rules for concurrently scheduled actions say an error is generated
when they lock more instances than exist; they do not say the actions are
serialized. So every `xfer_a` in one branch is concurrent with every
`xfer_a` in every other branch. With 2 DMA channels per lane, at most two
streams can use any one lane. The gate accepts only lane spreading. We should
tell the benchmark's owners. We should also tell them that `check_bench.py`
does not check `op`'s range (§1.2).

---

## 2. B1 — The enum domain (correctness, small) — DONE

- [x] **One helper decides a leaf's solver domain.** `layout.py` has
      `leaf_domain(leaf, types) -> Domain(width, signed, runs)`. An enum is
      as wide as its widest member needs (signed when a member is negative)
      and its members, merged into runs of consecutive values, are its legal
      values. `collect_solve_problem` and `TreeBuilder._vars` both call it;
      neither reads `bits` any more.
- [x] **The domain becomes a constraint**: `domain_expr(slot, dom)` is
      `slot in [runs]`, added beside the type's own constraints, both to an
      action's own problem and, as a `TYPE` constraint of its node, to the
      action tree. A dense enum is one range (`expr_in_range`); a sparse one
      is a union.
- [x] ~~Inline field domains (D-B8).~~ Moot: ast2ir already turns
      `rand bit[8] x in [0..9]` into a named constraint
      (`_flush_range_constraints`), so `Field.domain` is never set and the
      refusal in `collect_solve_problem` is unreachable.
- [x] Tests: `tests/unit/integration/test_bc_enum_domain.py` (action field,
      struct leaf, sparse `{1, 5, 9}`, negative members, an enum beside other
      constraints with a wide value tied to it, an enum in a cone, an enum
      struct leaf in a cone; 200 seeds each). Calibrated: with the domain
      constraint disabled, all 7 fail.
- [ ] Corpus test `types.enum.rand.001` (pss-corpus). Not yet written.

**Gate:** met for the tests and suites (unit, compliance, be-bc, ir-core,
rt-eng unchanged). The values-only `bench_seq` gate is blocked, see below.

**What B1 exposed.** With `op` limited to `op_e`, the values-only
`bench_seq` exhausts the solve budget on the IO cone (2 of 2 runs). The
cause is not the enum. A small case reproduces it: two `io_s` attributes
tied field by field (`c.f == io.f`, as `prep.io == io` is), `op` limited to
three values. 2–7 of 50 seeds exhaust a 20 000-conflict budget, at about 1 s
per solve. `rand bit[2] op; op < 3` behaves the same way. `rand bit[32] op`
solves all 50 seeds in 1.5 s, because a random 32-bit `op` is almost never
`OP_FLUSH`, which switches the `n_ptrs` division off and `nlb == 1` on. One
`io_s` alone solves with the enum on all 50 seeds. So the weakness is
dv-solve's handling of equal variables across two copies of the nonlinear
constraints: the B4 item "equalities between variables". The bug was hiding
it. B4 is now on the critical path.

## 3. B2 — Reuse compiled solver contexts (performance, small) — DONE

- [x] be-bc `interp/solve_cache.py`: `SolveCache`, compiled dv-solve
      contexts keyed by problem blob (the blob determines the problem, so one
      key serves cones and per-action problems alike), least recently used
      evicted beyond `capacity` (default 64). Each solve runs in
      `cache.session(blob)`: checkpoint, pin, solve, read, restore.
- [x] One cache per run, owned by the VM (`vm.solves`), used by
      `Activation.solve` (cones) and by the VM's `NativeBlobBackend` (an
      action's own problem). The module-global blob backend in `ops_orch` is
      gone. `run_model(solve_cache=..., max_restarts=...)`: pass a cache to
      share it across runs; `SolveCache(capacity=0)` compiles every solve
      afresh. A run that made its own cache releases it at the end.
- [x] **Solve budget.** Every solve passes `max_restarts` (default 10 000;
      see the correction below). Giving up (`SOLVE_TIMEOUT`) raises
      `SolveBudgetError` (a `VMError`), naming the traversal (`choosing values
      for traversal 'f' (T)`) or the action (`solving 'T'`). Before, a give-up
      was reported as unsat.
- [x] **Equivalence.** `tests/unit/integration/test_solve_cache.py`: six
      models (Ex 179/180/183/184, a parent constraint, an action's own problem
      repeated, a cone in a loop) on 200 seeds, cached against fresh: equal
      logs. Plus compile counts, cross-run sharing, eviction at capacity 1,
      and the budget error for an action's own problem and for a cone (a
      ten-into-nine pigeonhole, which gives up after 1 restart on all of 100
      seeds). `tests/compliance/test_solve_cache_equivalence.py`: every
      corpus test that lowers on bc (105), on its own seeds, gives the same
      log and the same error with and without the cache.
- [ ] rt-eng mirrors this in P8, not now.

**Gate:** met. On the values-only variant with `op` as `bit[32]` (the
problem as it was before B1): `bench_seq` N = 100 run 0.13 s, N = 1000 run
1.02 s (1.22 s end to end, 142 MB); `bench_par` N = 1000 1.33 s; checker
`PASS` on all four. With the enum fixed and the restart budget (§5.1 item 2):
`bench_seq` N = 100 0.97 s, N = 1000 6.9 s, `bench_par` N = 1000 6.7 s, checker
`PASS`: linear, but 2% of solves (heavy tail) take 69% of the solve time. B4.

- [x] **Budget correction (found in B4).** The budget was first
      `max_conflicts=100000`; that is dv-solve's restart unit, not a total,
      and it switched restarts off. It is now `max_restarts` (default 10 000,
      dv-solve's own: the bound solves had before B2). `run_model(
      max_restarts=...)`; the error says "gave up after N restarts".

## 4. B3 — Constructs the model uses (gaps G2–G6) — DONE (G7 open)

Each item is general, has focused tests, and gets a corpus test where the
LRM has an example.

- [x] **G2 — `default` and `default disable` (LRM 13.1.11).** ir-core has two
      new statements, `StmtDefault(target, value)` and
      `StmtDefaultDisable(target)`; ast2ir emits them in a constraint block
      instead of the `untranslated` ledger. They are resolved over the whole
      object, never alone (`xf/pss_lower/defaults.py`). Every statement gets
      a rank: in an action's own problem, `(-struct depth, order)`; in the
      action tree, `(-node depth, -struct depth, order)`. Struct bases come
      before derived types in that order, so 13.1.11 d's three rules are
      "highest rank wins". The winner per rand scalar is either an equality,
      owned by the node that wrote it, or nothing (a disable). A default or
      disable that another node wrote puts the target node in a cone, so its
      own problem (which holds its own defaults) is not used for it.
      Refused, with a location: a default under a condition (f); one on a
      non-rand attribute (c); a valued default on an aggregate; and a
      default inside a `with` or an activity constraint (ast2ir's existing
      "not supported yet" path). Defaults on action inheritance cannot be
      tested yet, because bc does not lay out a base action's fields; struct
      inheritance is covered. Found on the way: an action whose own problem
      is unsat raised a raw dv-solve exception. It is now `SolveUnsatError`
      (be-bc), which names the action.
      Tests: `test_bc_default_constraints.py` (Ex 154, same-type order,
      derived struct, aggregate disable, contradiction, parent/grandparent
      across the tree, refusals). The cone rule is calibrated: without it,
      `test_a_disabled_childs_default_does_not_reach_its_own_solve` fails.
- [x] **G3 — boolean antecedents** (be-bc `constraints.py`). A bare value is
      true when nonzero, so its negation literal is `b == 0`. The negation
      of `!e` is `e`'s own clause. The same rules apply in a consequent
      clause and in an `if`/`else` condition. Still refused:
      `!(a && b) -> e`, which needs two clauses.
      Tests: `test_bc_boolean_antecedents.py`; seven forms match the
      satisfying set of an exhaustive enumeration exactly.
- [x] **G4 — component attributes in constraints.** In the action tree, a
      read of `comp.<path>` from node n becomes one input per instance n may
      run in. Each input is a non-rand variable in a slot past the
      subtrees, with `ScScopeVar.comp_read` naming its component-object
      slot, and bc pins it from the component object when the cone is
      solved. With one instance, the read is that input. With several, the
      read is a variable tied by `comp == k -> v == input_k`. A node whose
      constraints read `comp` is always solved in a cone, and its own
      problem leaves such constraints out (`reads_comp`).
      Tests: `test_bc_comp_attr_constraints.py` covers: the root; a choice
      among five instances; steering through the value and through
      `comp == …`; an unsat value; a child in the instance its parent chose;
      loops.
- [x] **G5 — aggregate `==` and `!=`.** `resolve_refs` expands a
      comparison of two struct attributes into per-leaf `==` (AND) or `!=`
      (OR). It uses the resolver's `leaves(root, path)`, so it works both in
      an action's own problem and across nodes (`prep.io == io`). Leaf sets
      that differ are a located error.
      Tests: `test_bc_struct_compare.py`.
- [x] **G6 — loop indices in `with`.** The action tree records the index
      variables around each traversal site (`_Site.indices`). A `with` that
      reads one gets a variable of the traversed node (`ScScopeVar.
      loop_local`/`loop_node`). It is pinned when that node is solved, to
      the counter in the nearest frame above that runs the loop. Before
      that it is free (lookahead); afterwards it is committed with the
      node's values. The index shadows an attribute of the same name. A
      labeled `replicate` still substitutes constants. An activity
      `constraint` that reads a loop index is not covered yet.
      Tests: `test_bc_loop_index_with.py` covers `repeat`, unlabeled
      `replicate`, nesting, shadowing, `parallel` branches, and a handle
      with a parent constraint.

- [ ] **G7 — casts in constraints** (found in B2). be-bc's constraint
      translator refuses `ExprCast` (`(bit[64])x * (bit[64])y == …`). The
      model casts only in exec bodies, so this is not on its path; it is
      listed so it is not lost.

**Gate:** the values-only variant runs with the original spellings of G2–G6
restored (only G1 stays rewritten), and passes `check_bench.py` on 200
seeds. **Met (2026-10-02):** `bench_seq`, `bench_par`, `bench_rw`,
`probe_child_constraint` and `probe_grandchild_rw` each pass on seeds 1–200
at N = 20 (1000 of 1000). Spot checks of what the checker does not look at:
`src_tag` is 0 on every IO outside a read-verify (the default), each read's
`src_tag` is its write's tag (the disable), and every `xfer` line's
`comp.lane_id` matches its IO's `lane`.

The variant now differs from the model only in G1: flow objects and
resources are plain attributes and constraints, and `bind` is spelled as
equalities. One process, end to end:

| Test | N = 100 | 200 | 500 | 1000 | RSS at 1000 |
|---|---|---|---|---|---|
| `bench_seq` | 0.36 s | 0.50 s | 0.89 s | 1.54 s | 133 MB |
| `bench_par` | 0.46 s | 0.57 s | 0.95 s | 1.53 s | 136 MB |
| `bench_rw`  | 0.79 s | 1.29 s | 2.81 s | 5.41 s | 214 MB |

Lanes now come from the component choice (`io.lane == comp.lane_id`, four
candidates), not from `io.lane < 4`.

Also fixed on the way: ir-core did not export `Loc`, so no located IR node
could be deserialized (`test_ir_handoff.py`). WB DMA's
`default src_mask == 0xfffffffc` now reaches the IR, and progseq's strict
xfail for it became a positive check. Suites: pssc unit 2188 passed (the 4
known doc failures), compliance 662 passed / 57 xfailed (unchanged), progseq
952 passed (the 8 known failures), be-bc 310, rt-eng 115, ir-core 89.

## 5. B4 — Solver robustness (dv-solve) — IN PROGRESS

These fixes belong in dv-solve itself (fix at the source). pssc adds no
rewriting to avoid them.

### 5.1 What the investigation found (2026-10-01/02)

The original hypotheses (a weak modular add, struct copies) were wrong.
Measured instead, with a captured problem blob, a debug build and gdb
(`perf` is not permitted on this machine):

1. **The Python `SolveOpts` was shorter than the C struct.** The C struct
   ends with `time_limit_ms`; `dv_solve.ctx._SolveOpts` (24 bytes) did not
   have it, so every solve read 4 bytes past the caller's buffer as its
   wall-clock limit. rt-eng's `zbc_solver.c` copy has the same defect (on the
   stack). 31 private copies in dv-solve's own tests are also short.
2. **`max_conflicts` is not a total budget.** It is the restart unit
   (`luby(i) * max_conflicts` conflicts per restart, default 100);
   `max_restarts` (default 10 000) is the bound. B2 first passed
   `max_conflicts=100000`, which turned restarts off, and the enum-fixed
   `bench_seq` then exhausted it. The Python docstring said "give up after
   this many conflicts". There is also a default 10 s wall-clock deadline.
3. **The `bench_rw` hang is slow convergence.** In the two-IO problem,
   `b.slba >= a.slba` and `b.slba + b.nlb <= a.slba + a.nlb` form a cycle of
   four propagators (two 64-bit adds, two comparisons). Once a decision makes
   the cycle infeasible (`b.nlb > a.nlb`), bounds propagation proves it by
   moving `slba`'s bounds 49 apart per round: about 20 000 rounds, ~5 ms, per
   such conflict (traced: 755 000 bound changes on 12 variables in 3 s). Each
   `slba + nlb` also gets two separate result variables, one per constraint
   it appears in.
4. **Chronological backtracking thrashes.** With the implied constraint
   added by hand (`b.nlb <= a.nlb`), the crawl is gone but solves still take
   ~20 000 conflicts: `b.lane`, constrained by nothing, is re-decided 3000
   times in one solve, because a conflict caused by an early decision is
   answered by enumerating the latest one.
5. **Lazy clause generation fixes both, but is unsound.** dv-solve's LCG
   (CDCL with backjumping, `SolveOpts.use_lcg`, which nothing in bc or the
   Python API turned on) takes the original two-IO problem from 29/30 seeds
   over budget (median 2.2 s) to 2/30 (median 0.28 ms). But on a satisfiable
   8-queens it reports UNSAT on 9 of 200 seeds; without LCG, 0 of 200.
   Pre-existing (the installed library does the same).
6. **LCG state survived `restore`.** VSIDS activity and the clause arena
   were kept, so a reused context would search differently from a fresh one,
   and the 4 MiB arena, never reclaimed, would fill after enough solves.
7. A one-sided tightening for a wrapping modular interval
   (`_tighten_to_modiv`) was tried and reverted: it fired, but learned nothing
   on these problems (`x + n <= C` already solves with 0 conflicts).
8. Bounds shaving (`max_shave_iters`) is far too slow here (>60 s for 200
   solves); bc keeps it off.
9. The single-IO problem is ~3 ms per solve with a heavy tail when solved
   with `fair_pick` or with any extra variable; its 0.26 ms otherwise is a
   lucky fixed decision order.

PSS note: `slba + nlb <= NS_LBAS` is evaluated in 64 bits and wraps (LRM 8.7,
Example 42), so `slba` within 256 of 2^64 also satisfies it. No solve here
produced such a value (0 of 200 on the minimal case), but the benchmark's
checker would reject one. For the benchmark's owners (D-B10).

### 5.2 Items

- [x] `_SolveOpts` gains `time_limit_ms`; `solve()` gains `time_limit_ms` and
      `use_lcg`; its docstring states what `max_conflicts`/`max_restarts`
      mean. `docs/solver_api.md` shows the real struct.
      `test_solve_opts_layout.py` compiles a probe against `zsp_search.h` and
      compares `sizeof`/`offsetof` field by field (fails on the old struct:
      24 vs 32).
- [x] bc's budget is `max_restarts` (default 10 000, dv-solve's own), not
      `max_conflicts` (§3).
- [x] `lcg_reset`; a checkpoint records whether the LCG had learnt anything,
      and a restore to a pristine checkpoint resets it (clauses, watches,
      arena, activity). `test_lcg_restore.py`: a reused context answers as a
      fresh one, also after 4000 reuses. (Blocked on the next item: its
      problem is the 8-queens that LCG gets wrong.)
- [x] **One LCG soundness bug fixed**: `explain_bounds_ne` left out the
      shaved variable's own bound (`x != y` with y = v removes v from x only
      when v is x's endpoint). False UNSAT on 6-queens: 135/400 seeds -> 5/400;
      8-queens 14/400 -> 0/400.
- [ ] **LCG is still unsound, by design**: the analyzer asks every explainer
      for the CURRENT bound (not the one the trail entry set), and explainers
      cite the other variables' current bounds, which may have been derived
      later from the very entry being explained. Traced on 4-queens: the
      bit-vector subtract's explanation (`_explain_bvbin_64`) yields a
      circular, invalid clause. Fixing it means explanations that describe the
      state at the entry's trail position -- a rework of the analyzer (dv-solve
      has its own plan for this, `docs/cdcl_explain_soundness_plan.md`).
- [ ] **LCG cannot generalize a decision on a wide domain.** With the cycle
      detector in, LCG on the two-IO problem picks `a.slba` = a random 64-bit
      value, conflicts with `slba + nlb <= 2^20`, learns `a.slba != X`, and
      repeats: 30/30 seeds time out. The fix is LIFTING: explain the weakest
      bound that still conflicts (`r >= 2^20 + 1`, not `r >= X + 1`), and
      give the modular add an explanation that uses it (`a >= 2^20 - lo(b)`
      and the no-wrap bound) -- one conflict then learns `slba <= 2^20 - 1 or
      slba >= 2^64 - 256`. Same analyzer rework.
- [x] `use_lcg` is per solve (it stayed on once an LCG existed);
      `solver_solve_n` zeroes its `SolveOpts` (its `time_limit_ms` was
      uninitialized stack).
- [x] **Negative-cycle detection** (`zsp_diffcycle.c`): after compile, the
      difference relations (`<=`, `<`, `==`, and adds/subtracts that cannot
      wrap under the current bounds) of every cycle of relations get one
      propagator that runs Bellman-Ford over edge weights read from current
      bounds, and reports a negative cycle as a conflict explained by exactly
      the bounds the weights and no-wrap conditions read. Two-IO reproducer,
      LCG off: 29/30 seeds over budget -> 0/30 (median 200 ms, max 1.25 s,
      ~8000 conflicts: the crawl is gone, the thrashing is not).
      Values-only `bench_rw`, LCG off, checker `PASS`: N = 20 in 19.6 s,
      N = 100 in 62.6 s (median solve 0.06 ms; 48 lookahead solves of up to
      7 s take 99% of the time). dv-solve unit suite unchanged (841 pass;
      the 2 failures also fail without the change, or come from pointing
      `ZSP_SOLVER_PATH` at a bare build directory). The dv-solve changes are
      built in a scratch directory only: the installed library, and so
      every pssc suite, does not have them yet.
- [x] bc: `SolveCache(use_lcg=...)` (default off): with it on, a solve learns
      clauses and an UNSAT or give-up is confirmed by a plain solve before it
      is believed. Off until LCG is sound.
- [x] rt-eng `zbc_solver.c`: the struct fix, with a `_Static_assert` on its
      size and the offset of `time_limit_ms`.
- [ ] Private `SolveOpts` copies in 31 dv-solve tests (same over-read).
- [ ] Tests for `zsp_diffcycle.c` (builder-level: the cycle, a wrapping add
      that must not count, the explanation's soundness under LCG).

**Status (2026-10-02, later).** D-B11 decided: finish B4 as it stands
(option 2), then rework dv-solve's conflict analysis (option 1) as its own
step. Since then:

- The dv-solve changes above landed upstream (`a2bc755`), merged with the
  `zsp_` -> `dvs_` rename and a soundness campaign. Consumers updated: rt-eng
  `zbc_solver.c` (forward declarations; its `_Static_assert` had the wrong
  offset for `time_limit_ms`, 28 instead of 24, and caught it), pssc
  `sw_tgt._solve_c` (now includes the public `dv_solve.h`),
  `test_dvsolve_resources.py`, and the be-bc format spec comment (the
  generated `zbc_format.h` in rt-core regenerated with it). The two dv-solve
  tests still carrying a short private `SolveOpts` now use the canonical one.
- **A latent B2 defect, found on rt-eng:** a solve that has to relax a soft
  constraint calls `dvs_solver_reset`, which discarded pins and the trail
  every open checkpoint points into; the next `restore` crashed. Fixed in
  dv-solve (see "dv-solve issues resolved" below); the temporary bc/rt-eng
  workaround (`has_soft`, no reuse of soft problems) is gone again.
- `tests/unit/test_diffcycle.py` (dv-solve): an infeasible cycle over a
  2^40-wide range is UNSAT at once, with and without learning; with unbounded
  64-bit operands the same constraints are satisfiable through wrap-around and
  stay SAT; a feasible cycle solves; 60 random cycles give the same verdict
  with and without learning, every model checked. Calibrated: with the
  detector's construction disabled, 3 of the 5 fail.
- With the merged dv-solve, LCG reports no false UNSAT on 4- to 8-queens
  (400 seeds each; 6-queens was 5/400).
- Suites with the merged dv-solve: unit, progseq, compliance (662 passed,
  57 xfailed), be-bc 310, ir-core 89, rt-eng 115 -- all at baseline.

**Option 1 (conflict analysis), 2026-10-02.** Most of it had already landed
upstream with the soundness campaign: explainers now read the domains rewound
to the trail entry they explain (B56), each literal is attributed to the
entry that made it true, the variable's own previous bound is added where an
explainer needs it, and the analyzer asks each explainer for the literal it
needs (the weakest crossing bound at the conflict). What was missing for this
model was an explainer that USES the requested bound:

- `_explain_bvsum_lifted` (dv-solve `dvs_prop_templates.c`): an unsigned add
  or subtract that cannot wrap, read as X = Y + Z, explains a requested bound
  by the weakest operand bounds that imply it plus the no-wrap (no-borrow)
  bounds -- e.g. `a + b >= B` by `a >= B - lo(b)`, `b >= lo(b)`,
  `a <= 2^w - 1 - hi(b)`, `b <= hi(b)`. It applies only when those literals
  hold in the rewound state; otherwise the conservative explanation is used.
  One conflict on a 64-bit `slba` now learns `slba <= 2^20 - 1 or slba >=
  2^64 - 256`, where it used to learn `slba != X`.
- Tests: `test_lcg_lifted_sum.py` (the wide decision solves within 5
  restarts; 200 random 5-bit add/subtract problems, every verdict with and
  without learning equal to exhaustive enumeration -- 94 SAT, 106 UNSAT --
  and every model checked). The LCG stress set passes on the step-checker
  build (every learnt clause and explanation checked).
- LCG on the IO problems (median per solve, 30 seeds): two-IO 2.0 s -> 0.11 ms,
  struct copy -> 0.06 ms, single IO -> 0.04 ms, but 1-9% of seeds still run
  long: the search decides `nbytes` (VSIDS ranks it high) to a value that is
  not `nlb * lba_bytes`, and divisibility cannot be generalized by bound
  literals, so it learns `nbytes != X` value by value. That is a branching
  question (value decisions on a wide, functionally determined variable), not
  an explanation one; left open.
- **bc uses learning by default**, bounded: `SolveCache.solve` runs the
  learning search for `lcg_restarts` = 5 restarts, keeps a solution if it
  finds one, and otherwise settles the solve with the plain search under the
  full budget (which also confirms any UNSAT). Problems with soft constraints
  skip the first step. rt-eng's engine runs the same sequence, so engine and
  oracle still agree (rt-eng 115 passed). Over 200 seeds, 5 restarts beat 20
  and 100 on all three IO problems (two-IO: 7.0 s total vs 134 s at 100).
- **Benchmark (values-only variant), checker `PASS` on all:**

  | Test | N | Before option 1 | Now |
  |---|---|---|---|
  | `bench_seq` | 1000 | 6.9 s | 2.8 s |
  | `bench_par` | 1000 | 6.7 s | 3.3 s |
  | `bench_rw` | 100 | 62.6 s | 4.1 s |
  | `bench_rw` | 1000 | -- | 22.2 s, 196 MB |

  B4's gate (`bench_rw` N = 1000 at most 12x N = 100) is met: 5.4x.
- Suites with learning on: unit, compliance (662 passed, 57 xfailed -- no
  verdict changed), be-bc 310, rt-eng 115.
- dv-solve's own unit suite with the lifted explainer: 945 passed, 5 failed.
  None is caused by it (the same 3 doc tests fail on a build without it):
  `test_e2e` (2, a zuspec-be-py API mismatch; dv-solve's CI skips the file)
  and 3 Verilator doc tests in which `dv-solve-smt2 --interactive` segfaults
  in `dvs_solver_restore` on `(pop)`. That crash reproduces on upstream
  `305f281` (before the merge with these changes), from input captured off a
  Verilator run (`docs/examples/verilator/packet.sv`). It is the same shape
  as the soft-constraint defect above: a solve inside a push scope resets the
  context, and the pop restores a checkpoint that no longer exists. For the
  dv-solve owner.

**dv-solve issues resolved (2026-10-02, at the user's request; in
`packages/dv-solve`, uncommitted).**

1. **`dvs_solver_reset` inside a checkpoint scope.** It returned the context
   to its compiled state, destroying the open checkpoints (the next restore
   crashed) and the pins made in the scope (a pinned value came back
   different: `e` pinned to 1 read back as 153). Inside a scope it now rewinds
   the trail to just after the innermost checkpoint, keeps the propagators
   (so constraints asserted in the scope survive) and re-establishes the
   bounds the scope set: pins, and the compile-time bound tightenings of
   constraints added in the scope (`dvs_ctx_t.scope_log_*`; harvested before
   propagation, so nothing derived from a soft constraint is frozen in).
   Relaxed soft assumptions are written through the trail inside a scope, so
   the caller's restore re-activates them. Outside a scope, unchanged.
   Fixes both the soft-relaxation crash and `dv-solve-smt2 --interactive`
   segfaulting on `(pop)` (a second `check-sat` in a push scope reset the
   context). Tests: `test_soft_checkpoint.py` (restore after a relaxing
   solve; a pin survives a relaxation; a restore re-activates the soft; a
   reset in a scope keeps a pin and an added constraint and the scope still
   restores -- calibrated: without the compile-time log, that one fails).
2. **n-ary `concat` in the SMT-LIB front end.** Verilator writes
   `(concat a b c)`; only the binary form was accepted, so the assertion was
   dropped and every hash-constrained `randomize()` came back `unknown`
   (also on upstream; hidden behind the crash). Now folded left-associative,
   in translation and in `get-value` evaluation.
3. **The learning tail.** Two causes: the modular multiply reasoned backward
   only from a fixed result (`_bv_mul_divide`: interval division when the
   unsigned product cannot wrap), and learning decided a wide variable to a
   value, so a failure taught one value (`_bound_decision`: a variable that
   has taken part in conflicts and has more than 64 values is decided by a
   bound, `x <= v` or `x >= v`, side chosen like a split's). Both are needed:
   with either alone 3-8 of 100 seeds still ran long; with both, 0 of 100 on
   all three IO problems. Test: `test_a_product_with_holes_is_learnt_by_range`
   (fails without bound decisions).
4. **Leaks.** `SolveCtx.destroy` and rt-eng's `zbc_solver_run` now call
   `dvs_solver_destroy`: each context that learnt leaked its clause-learning
   state (a 4 MiB arena).

Checks: step-checker build (every learnt clause and explanation checked):
the touched unit tests and the 13 LCG stress problems, 0 invalid steps, all
answers equal to z3's. dv-solve unit suite: 949 passed, and of the
failures 2 are `test_e2e` (a zuspec-be-py API mismatch, skipped by dv-solve's
CI) and 1 is `test_verilator_guide_unknown` (the page quotes another
Verilator version's warning text); `random.expected` was regenerated in the
same change (a seeded model, still valid: `addr = 0xFC, len = 3`). pssc unit, compliance (662 passed,
57 xfailed), be-bc 310, rt-eng 115: baseline.

**Benchmark (values-only), checker `PASS`, no solve over 10 ms:**

| Test | N | Before today | Now |
|---|---|---|---|
| `bench_seq` | 1000 | 2.8 s | 1.6 s |
| `bench_par` | 1000 | 3.3 s | 1.7 s |
| `bench_rw` | 100 | 4.1 s | 0.7 s |
| `bench_rw` | 1000 | 22.2 s | 4.9 s, 198 MB |

**Gate:** the reproducer solves in under 50 ms on 200 seeds; values-only
`bench_rw` at N = 1000 is linear (N = 1000 at most 12× N = 100). Met: no
solve over 10 ms; N = 1000 / N = 100 = 7x.

## 6. B5 — Flow objects and resources: what the model needs

This is a slice of design P2 and P3. It is built the way that design says
(layers A–D), restricted to the constructs below, and gated by this model
together with the design's own use cases. Each item names the use case it
closes.

**How it is built (2026-10-02).** bc already solves each traversal in its
cone, with committed history pinned and later traversals free. B5 keeps that
shape and adds flow and resource semantics where it already has hooks, rather
than building the design's global planner (P3), which this model does not
need (§1.4).

- **A reference is an object in its node.** A flow or resource reference
  takes one slot per scalar of its type, laid out by `layout.py` like a struct
  attribute (`inp.tag`, `chan.instance_id`). Exec bodies and constraints
  reach it the way they reach a struct.
- **`bind` is equality** (D-B13). `bind a.out b.inp` is the cone constraint
  `a.out == b.inp`, leaf by leaf, in force in the bind's scope. Each reference
  keeps its own slots. The producer has committed by the time the consumer
  solves, so the consumer's values are pinned to the producer's. A value the
  producer's exec body wrote is pinned too, because pins read the object.
- **A state pool's current object lives in the activation object** (D-B14),
  in slots past the subtrees. Its initial object is solved with the root,
  with `initial` true. A state input is pinned to those slots when its own
  node is solved, and is free before that (lookahead). A state output has
  `initial` false, and is copied into those slots when its node completes.
- **The runtime primitives are activation hooks** (D-B12). Claims, releases,
  state reads and state writes all happen at a node boundary the activation
  already sees: the node's solve (`SOLVE_NODE`) and its frame's completion.
  So they are built there, not as the new opcodes of design D2.
  - A claim is recorded when the node's solve commits, and released when its
    frame completes or is cancelled.
  - A lock's `instance_id` is excluded from the instances busy at its solve:
    one bit-mask input per candidate pool, pinned at the node's own solve
    and free before.
  - The native engine already refuses activations (P1-D6). The opcodes come
    with P3's global mode, which needs them as monitors.
- **Nodes with flow or resource semantics are always solved in a cone**, as
  nodes reading `comp` are (B3 G4), so their own per-type problem never
  solves them without the pins.

**B5a — static elaboration (P2 subset).** DONE.
- [x] The pool-binding table, as one walk: ir-core `xf/pss_lower/pools.py`
      (`PoolTable.pool_of(instance, action, field)`). It applies 12.3 c
      (explicit over default), e (the top-most instance wins), and refuses
      d, f and g with the clause in the message. The SV target is not moved
      onto it (D-B4). It found an ast2ir bug, fixed at the source:
      `bind p {sub.*}` (LRM Ex 134) reached the IR as a bare `*`, which binds
      every instance. It is now `sub.*`.
- [x] A reference is laid out by `layout.ref_leaves`, one slot per scalar of
      its object. A claim's `instance_id` and a state's `initial` are solved
      with the action. Tests: `test_pool_binding.py` (6).

**B5b — explicit binding and state (UC1, UC6 without `prev`).** DONE.
- [x] `bind` is a cone equality, leaf by leaf, in force in its scope
      (D-B13), including a compound passing a child's output on. Operands
      that are not two references are refused.
- [x] State pools as described above (D-B14). `constraint cfg.ready;` on an
      object that does not satisfy it is a located `ScopeUnsatError`.
      Tests: `test_bc_flow_objects.py` (10, including the pick tests below).

**B5c — locks (UC7, UC8, UC9).** DONE for lock and share. Resource
attributes other than `instance_id`, and claim arrays (UC9), are refused.
- [x] `instance_id` is in `[0, size)` of the pool its node's instance
      reaches, and outside a busy mask pinned at the node's own solve. The
      mask holds the instances a conflicting claim holds now (other than an
      ancestor's), and those a claim in another branch of an enclosing
      `parallel` took since that `parallel` was entered. A compound's lock
      is held until its frame completes.
- [x] Over-subscription in a `parallel` is a located error (UC8).

**B5d — choosing a producer (`bench_rw_pool`).** DONE.
- [x] An unbound buffer input picks one of the objects that completed
      actions output to its pool. A pool selector, tied to the node's `comp`,
      makes the lane and the pick one choice. Candidates are tried in a
      seeded order, the least-consumed first, and the first the cone
      accepts wins. With no candidate the error says inference is not
      supported. Consumption counts matter here: any pick is legal, but the
      model derives each read's tag from its write's, and the checker wants
      unique tags. Picking at random made two reads of one write, so this
      goes to the benchmark's owners too (D-B10).

**B5e — claims across parallel branches (`probe_par_unpinned`).** DONE,
built differently from the plan.
- [x] Built first as planned: footprint variables in the `parallel`'s owner,
      solved with lookahead over one traversal of each branch. That cone
      holds four whole IOs, descriptors included, and the root's solve
      exhausted the 10 000-restart budget even for `bench_par`. That is
      B4's finding again: IOs solved jointly are hard.
- [x] Now: on entry to the `parallel`, the activation probes each branch in
      turn. A probe is an uncommitted solve of the cone of the branch's
      first locking node, with the instances earlier branches reserved
      excluded. A branch's footprint is what its probe locked. Every
      instance no probe took is dealt out at random among the branches that
      use its pool. Each lock then stays inside its branch's footprint, as
      part of its busy mask.
  - The cones stay one IO wide, and the work per `parallel` entry is
    branches × one IO solve, independent of N.
  - Calibrated: on a two-lane model where each lane has one channel, the
    test fails on some seed with footprints off
    (`test_footprints_spread_branches_over_lanes`).
  - LRM Ex 134 runs. The shared core is the located conflict the example
    calls one. Tests: `test_bc_resources.py` (9).
  - It is greedy across branches: a branch probed early can take what a
    later one needed. That gives a located error, never a wrong result.

**Gate.** Met for the benchmark. Every test passes its checker from the
unmodified files: `bench_seq`, `bench_par`, `bench_rw`, `bench_rw_pool` and
`probe_par_unpinned` at N = 100, 200, 500 and 1000. All three probes pass
on 50 seeds out of 50. Every `op` is a member of `op_e`, and `bench_seq`
shows all six `(op, lba_bytes)` combinations.

| Test (end to end, one process) | N=100 | N=200 | N=500 | N=1000 | RSS at 1000 |
|---|---|---|---|---|---|
| `bench_seq` | 0.49 s | 0.67 s | 1.36 s | 2.59 s | 158 MB |
| `bench_par` | 0.56 s | 0.77 s | 1.43 s | 2.64 s | 164 MB |
| `bench_rw` | 1.45 s | 2.60 s | 6.05 s | 11.96 s | 268 MB |
| `bench_rw_pool` | 0.57 s | 0.86 s | 1.77 s | 3.41 s | 185 MB |
| `probe_par_unpinned` | 0.57 s | 0.78 s | 1.49 s | 2.75 s | 164 MB |

`bench_seq` at N=1000 takes 5.3× its N=100 time, against the 12× bound.
`bench_rw` is about 2× its B3 time: each of a pair's ~25 traversals solves
the pair's whole cone, which now has a reference per stage (2,502 solves at
0.65 ms each at N=200). Reusing a cone's last solution when it still holds
would remove most of them. That is for B6.

Not done: the corpus use cases (no L4 corpus tests exist yet, §8.4 of the
design), and timing-fuzz invariance.

## 7. B6 — Scale, telemetry and the report — IN PROGRESS

- [x] **B6a** `scripts/bench_bc.py` (generic, not named after any model):
      runs a model's exports on bc for a list of constant values and seeds.
      A constant is overridden by a file parsed first (`const int N = v;`),
      which a model guarded with `compile if (!compile has(N))` takes in
      place of its own; nothing is copied or edited. Each (export, value)
      runs in its own process, so peak RSS is that configuration's. It
      records per-stage time (parse/link, ast2ir, scenario, bc lower, run),
      peak RSS, solves, compiled contexts and cache hits, cone telemetry
      (design §4.7: nodes, variables, constraints by kind) and, given a
      checker command, the checker's verdict per seed. **Done.** It drops
      the run's event trace (`--trace` keeps it): the trace holds every
      event, so it grows with the run and nothing in the report reads it.
- [x] **B6b** A variety report: the distribution of every `rand` leaf of
      every action, as solved, and of selected tuples (here
      `(op, lba_bytes, nlb)`). It is collected through one optional hook,
      `run_model(on_solve=...)`, called after each solve writes back; a run
      without it is unchanged. In the values-only run `lba_bytes` was 512 in
      89% of IOs. That is legal (4096-byte IOs are limited to 64 blocks),
      and D-B7 is decided by comparing it with `SolveCache(fair_pick=True)`.
      **Done (2026-10-02).** Every `(op, lba_bytes)` combination comes up,
      4096 is about half of each op, and the ops are even (FLUSH 32%). It
      took three fixes, all below: kept draws with set-size MRV (dv-solve),
      the comp-read tie (ir-core, be-bc) and the `% c` value pick
      (dv-solve).
      **Measured (2026-10-02, `bench_seq`, N=1000, seeds 1-3):** 4096 is 5.0%
      of IOs by default and 6.0% with `fair_pick`, and FLUSH is 39% of ops
      either way. Every other leaf is well spread. So `fair_pick` is not the
      lever, and D-B7 is to leave it off. The skew is dv-solve's search, and a
      model of `io_s` alone reproduces it (4096 8%, FLUSH 39%):
      - MRV sizes a domain by its bounds, so `lba_bytes in [512, 4096]`
        counts as 3585 values, not 2. Smaller domains (`nlb`, `n_ptrs`) are
        decided first and mostly leave 512 as the only legal value. Taking
        the set's size for MRV (a prototype) makes `lba_bytes` the first
        decision at 50/50, but the result only rose from 8% to 15%.
      - The rest is restarts. A conflict deep in the 4096 branch (most often
        `slba % 8 == 0`, which a random `slba` meets one time in 8) restarts
        the search, and the restart decides `lba_bytes` again. A branch that
        conflicts more is rejected more, so the run samples each choice
        weighted by how easily it solves. Without the `% 8` rule, FLUSH IOs
        are 50/50, and the others are 26%, held down by `n_ptrs`'s division.
      - In the bench's 66-variable cone the effect is stronger. With the MRV
        prototype, 4096 was 4% in `bench_seq` and 1.4% in `bench_rw`.

      **The ceiling for picking values that meet `x % c == 0`** (measured by
      dropping the rule from a scratch copy of the model; N=1000, seeds 1-3):

      | | 4096 overall | 4096 in READ / WRITE / FLUSH | FLUSH share |
      |---|---|---|---|
      | `bench_seq` as is | 5.0% | 4% / 5% / 5% | 39% |
      | `bench_seq` without `% 8` | 29.5% | 16% / 18% / 49% | 40% |
      | `bench_rw` as is | 1.0% | 1% / 1% / n/a | n/a |
      | `bench_rw` without `% 8` | 12.7% | 13% / 13% / n/a | n/a |

      The set-size MRV prototype changes none of these on the bench (4.1%
      and 28.4% for `bench_seq`), so it is not part of the fix. It mattered
      only in the descriptor-only model. FLUSH's share does not move at all.

      **Why READ/WRITE stay low** (a trace of 400 solves of the descriptor
      alone, with the `% 8` rule dropped). Ablations: dropping
      `nbytes <= MDTS_BYTES` lifts READ/WRITE 4096 from 23% to 57%, and
      dropping `qid == sq_slot / 4 + 1` brings FLUSH from 39% to 23% of ops.
      Neither rule is the problem. They matter because of how the search
      treats the conflicts other rules cause:
      - A solve averages 5-8 conflicts. Most are local to one later
        variable: `first_off % 4 == 0` (a random `first_off` meets it one
        time in 4), and `sq_slot` after `qid` (the division does not narrow
        `sq_slot` once `qid` is fixed, so 3 picks in 4 fail). The clause
        learnt from such a conflict often names only that variable, so it
        backjumps to level 0 (2.5-3.5 times per solve), and every earlier
        decision goes with it.
      - The decisions are then made again, at random, and in a different
        order: after the first conflict, VSIDS activity replaces MRV. `op`
        was decided more than once in 356 of 400 solves. `lba_bytes`, with no
        activity, now comes after `nlb`, which leaves 4096 legal only when
        `nlb <= 64` (1 in 4). That is the transfer limit's effect.
      - A branch that conflicts more is redrawn more. READ/WRITE solves
        average 8.1 conflicts and FLUSH solves 5.5, so FLUSH keeps its
        draws and ends at 39%.

      So the skew is the search re-sampling its decisions on every backjump,
      and each conflict source (`% c`, a division's reverse, the 64-bit
      `slba + nlb` wrap) only feeds it. Fixing value picks for `% c`
      removes one source. Keeping a decision's value when the search makes
      it again (phase saving in seeded solves, made safe against B18's
      infeasible saved values) removes the mechanism.

      **Prototype (2026-10-02, dv-solve, uncommitted).** Two changes:
      - *Kept draws.* A seeded solve keeps each variable's first drawn
        value. When the search decides the variable again it takes that
        value if the domain still allows it. A value whose own decision
        conflicts is dropped for the rest of the solve, and keeping stops
        at the first restart. So an infeasible kept value (B18's case) is
        tried at most once. `DVS_KEEP_DRAWS=0` turns it off.
      - *MRV by set size.* An unconditional `x in [..]` records how many
        values `x` has, and variable order uses that, not the bounds' span.

      N = 1000, seeds 1-3. Each cell is the 4096 share within READ / WRITE /
      FLUSH, with FLUSH's share of ops in brackets. The isolated rows are
      the `io_s` descriptor alone, 3000 solves.

      | Run | before | kept draws | kept draws + MRV |
      |---|---|---|---|
      | `bench_seq` as is | 4 / 5 / 5 (39%) | 4 / 4 / 7 (32%) | 5 / 4 / 8 (30%) |
      | `bench_seq` without `% 8` | 16 / 18 / 49 (40%) | 17 / 18 / 49 (32%) | 24 / 25 / 51 (32%) |
      | `bench_rw` as is | 1 / 1 | 2 / 2 | 6 / 6 |
      | `bench_rw` without `% 8` | 13 / 13 | 13 / 13 | **50 / 50** |
      | descriptor alone, without `% 8` | | | **56 / 50 / 50 (34%)** |

      The MRV change alone, on the descriptor, gives 28 / 25 / 47 (35%).

      - Kept draws fix the op skew: FLUSH goes from 39% to 32%.
      - Kept draws with MRV give the even split wherever the descriptor's
        own rules are what's in play.
      - Run time is 5-15% lower.
      - dv-solve's unit tests and ctest show no new failures.

      What was left, and how each was fixed:
      - ~~**`% 8`.**~~ **Fixed (dv-solve, uncommitted).** With the rule in,
        4096 stayed at about 5%. bc writes the rule as the clause
        `lba_bytes != 4096 || slba % 8 == 0`. Once `lba_bytes` is 4096 the
        remainder is fixed to 0, but the modulo propagator aligns only
        `slba`'s bounds, so a uniform draw between them met the rule one
        time in 8 and the other seven were conflicts. dv-solve now records
        each `r == a % c` with `c` a positive constant (unsigned) when its
        propagator is added (`DvsModLink`, rolled back with it on restore).
        When it draws `a` and `r`'s domain is narrower than `0..c-1`, it
        keeps the draw's quotient and takes a remainder at random from `r`'s
        domain (`_pick_mod_aligned`). While the guard is undecided `r` spans
        every remainder and nothing moves: a 512-byte `slba` is aligned
        12.5% of the time, exactly 1 in 8. Test: dv-solve
        `test_mod_value_pick.py` (calibrated: the previous library gives
        600 of 1000 4096s on that descriptor, this one 509).

        | Run (N = 1000, seeds 1-3) | before | `% c` pick |
        |---|---|---|
        | `bench_seq` | 23 / 20 / 11 (20%) | **50 / 49 / 51 (32%)** |
        | `bench_par` | 24 / 21 / 11 (23%) | **50 / 49 / 49 (35%)** |
        | `bench_rw` | 6 / 6 | **51 / 51** |

        All seeds pass the checker. Run time drops: `bench_seq` 1.24 s to
        1.05 s, `bench_rw` 4.3 s to 3.0 s.
      - ~~**`bench_seq`'s cone.**~~ **Fixed (ir-core, be-bc).**
        READ/WRITE reached 24%, not 50%. Traced over 200 `io_a` solves:
        `lba_bytes` is drawn first, and 4096 about half the time. In
        READ/WRITE solves, a learnt clause set it back to 512 after a
        backjump in about half of those (28 of 56); in FLUSH, never (0 of
        37). The backjumps came from `io.lane`: `io.lane == comp.lane_id`
        did not narrow `lane`, so it was drawn from 0..255 and rejected one
        value at a time (up to 15 times in a row). Ablations, without
        `% 8`: dropping the lane rule or the `qid` rule each give 52-57%,
        and adding `io.lane < 4` alone gives 52 / 50 / 51.

        Cause: with several candidate instances, `comp.lane_id` was a
        variable tied to each instance's value by one implication per
        instance (`comp == k -> v == in_k`). Until `comp` is chosen those
        propagate nothing, so `v` kept the attribute type's whole range.
        The tie is now one conditional value, `v == (comp == k0 ? in0 :
        comp == k1 ? in1 : ...)` (`action_tree._comp_attr`), which the
        solver bounds by the inputs once they are pinned, and from which it
        narrows `comp`. bc lowers `ExprIfExp` in a constraint to the
        solver's if-then-else value (before, it refused a `?:` in a
        constraint). Tests: `test_bc_comp_attr_constraints.py` (the bound,
        calibrated: the implication encoding leaves 0..255) and
        `test_bc_conditional_value.py`.

        | Run (kept draws + MRV) | before | conditional tie |
        |---|---|---|
        | `bench_seq` without `% 8` | 24 / 25 / 51 (32%) | **52 / 50 / 51 (32%)** |
        | `bench_seq` as is | 5 / 4 / 8 (30%) | 23 / 20 / 11 (20%) |
        | `bench_par` as is | | 24 / 21 / 11 (23%) |
        | `bench_rw` as is | 6 / 6 | 6 / 6 |

        All seeds pass the checker, and run time is unchanged. With `% 8`
        in, FLUSH fell to 20% of ops: the `% 8` conflicts then dominated
        (fixed above).
- [x] **B6c** N = 10 000 (bc has no `repeat` count limit), to show the line
      keeps going. **Done.** The first run passed but was superlinear (35 s
      and 412 MB for `bench_seq`, 72 s and 654 MB for `bench_rw`). Four
      causes, each fixed where it was:
      - The claim table kept every claim ever made, and each node's exit
        and each `_busy` scanned all of it. A held claim is now indexed by
        its node, and a released one keeps only its latest instance per
        (node, site, instance, lock): it matters only to a concurrent claim
        made since their parallel was entered (`activation.py`).
      - A picked buffer input shuffled and sorted every object its pool
        ever received, per pick. Objects are now bucketed by times picked,
        and the order is drawn lazily from the least-picked bucket (same
        distribution: least picked first, uniform among equals).
      - A completed frame stayed in its parent's `children`, so a loop kept
        every iteration's frames. Only live children are ever read; a done
        one now leaves the list.
      - dv-solve: `restore` popped the dynamic stack to the solve's level-0
        seal, not to the checkpoint's mark, so every trail entry made by the
        pins and the solve's first propagation stayed allocated. A reused
        context grew about 13 KB per solve. Fixed in `dvs_checkpoint.c`,
        with a calibrated test (`test_checkpoint_memory.py`: 10 -> 594
        blocks over 3000 cycles without the fix, 10 with it).

      Now (one seed, run time in s / peak RSS in MB; all pass):

      | export | N = 100 | N = 1000 | N = 10 000 |
      |---|---|---|---|
      | `bench_seq` | 0.14 / 66 | 1.21 / 67 | 12.4 / 78 |
      | `bench_par` | 0.23 / 70 | 1.16 / 71 | 10.5 / 81 |
      | `bench_rw` | 0.51 / 67 | 4.50 / 68 | 47.9 / 79 |
      | `bench_rw_pool` | 0.25 / 68 | 1.79 / 69 | 17.1 / 82 |
      | `probe_par_unpinned` | 0.23 / 71 | 1.23 / 72 | 11.3 / 80 |
- [x] **B6d** A `perf` pytest marker, not in the default run, on a
      pssc-owned model (D-B1) in `tests/perf/` at N = 200: a generous time
      bound and a check that time scales linearly. **Done:**
      `tests/perf/model/jobs.pss` (exports `jobs_seq`, `jobs_par`, `jobs_rw`),
      checked against its own rules at N = 40 by default; the timing tests
      run with `pytest -m perf tests/perf`.
- [x] **B6e** `bench_rw` back to its B3 time. Each of a pair's traversals
      solves the pair's whole cone. Find where the time goes with B6a's
      telemetry first; the candidate is reusing the cone's last solution
      when it still satisfies the pins and constraints now in force.
      **Done** (be-bc `docs/spec/activation.md`, "The cone's last
      solution"): `bench_rw` at N = 1000 went from 11.96 s to 4.5 s, and
      four of every five traversals now reuse (`test_bc_cone_reuse.py`,
      calibrated on the state-input case).

## 8. Order and size

| Phase | Size | Depends on | Unblocks |
|---|---|---|---|
| B1 enum domain | S | — | correct values everywhere |
| B2 context reuse + budget | S | — | ~300× on the solve path, no hangs |
| B3 G2–G6 | M | B1 | the model's constructs, outside flow/resources |
| B4 dv-solve | M (investigation first) | — | `bench_rw` |
| B5a–c | L | B2, B3 | `bench_seq`, `bench_par`, `bench_rw`, probes 1–2 |
| B5d | M | B5a–c | `bench_rw_pool` |
| B5e | M | B5c | probe 3 |
| B6 | S, runs alongside | B2 | the report |

B1, B2 and B4 are independent and can run in parallel. B1 and B2 pay off for
every bc user immediately.

---

## 9. Decisions for review

| # | Question | Recommendation |
|---|---|---|
| D-B1 | Where the benchmark lives. Its files cannot be committed (house rule). | Leave it untracked and run it through `scripts/bench_bc.py` by path. For regression, write a **pssc-owned model** in `tests/perf/` from the same constructs (pipelined compound with explicit binds, a state prerequisite, per-component pools and locks, a write→read flow), written fresh rather than copied. |
| D-B2 | Must the context cache be invisible, i.e. byte-identical logs? | Yes, and test it (§3). Measured identical in 319 of 319 cases. If a dv-solve change ever breaks this, fix `restore` rather than accept drift. |
| D-B3 | Default solve budget | ~~`max_conflicts` = 100 000~~ (that is dv-solve's restart unit, §5.1). Taken: `max_restarts` = 10 000 (dv-solve's default), overridable per run. Exhausting it is a located error. |
| D-B4 | Build the pool-binding table now without moving the SV target onto it | Yes. The table lands in ir-core `xf/` as design D1 says; moving SV onto it stays in P2 with its golden-snapshot gate. |
| D-B5 | Producer choice in B5d: try candidates in random order, or one selector variable over all candidates | Random-order tries. A selector over N/2 candidates makes each read's cone O(N), so the run is O(N²). **Built** (B5d), least-consumed first. |
| D-B6 | Adopt footprint variables (a cone break) for claims across parallel branches | Yes, with design §9.3's enumeration oracle on small cases (2–4 branches, 2–3 instances) as the soundness check, since D18 asked for data and this is the data. **Built** (B5e) as probed footprints, not variables: as variables, the root's cone held one IO per branch and did not solve. The enumeration oracle is not built yet. |
| D-B7 | `fair_pick` for SOLVE_NODE | Measure both in B6 and decide on the data; don't change the default blind (it changes every scenario's values). |
| D-B8 | Fold inline field domains (`x in [0..9]`) into B1 | Yes. It's the same helper and removes a refusal. |
| D-B9 | Priority against the op-model gaps (status doc: "next") | B1 and B2 now (small, they fix correctness and speed for every bc user). The user then picks B3–B5 or the op-model gaps. |
| D-B10 | Report §1.5 and the checker's missing `op` range check to the benchmark's owners | Yes. Don't edit their files. Add (B5d): `read_verify_a` derives its tag from its source's, and nothing makes two reads take different sources, so a legal `bench_rw_pool` scenario can fail the checker's unique-tag rule. |
| D-B12 | (new, B5) Build claims, releases and state reads/writes as activation hooks at the node's solve and completion, instead of the opcodes of design D2? | Yes, for now. Every one of them happens at a node boundary the activation already sees, and the native engine refuses activations anyway. The opcodes come with P3's global mode. |
| D-B13 | (new, B5) `bind` as leaf-by-leaf equality between two references' own slots, instead of one shared slot range? | Yes. Shared slots would need an indirect load in bc. With the producer committed first, equality pins the consumer to exactly the producer's values, including values its exec body wrote. |
| D-B14 | (new, B5) A state pool's current object in slots of the activation object, with the initial object solved with the root? | Yes. One activation runs one export, so the activation object is the pool's lifetime. Moving pools into the component object is for when several activations share a component tree. |
| D-B11 | (new, from B4; **decided 2026-10-02: option 2 now, then option 1**) dv-solve's clause learning is the lever for this model's hard solves (median 2.2 s -> 0.3 ms on the two-IO problem), but it is unsound and cannot lift explanations. Rework dv-solve's conflict analysis now (explanations at trail position + lifting), or ship B4 with the cycle detector and LCG off (bench_rw runs, slowly) and do the rework as its own plan? | Open. |

