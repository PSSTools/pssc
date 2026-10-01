# P1 "Compound-scope solving": implementation, test and doc plan

Status: **reviewed** (2026-09-30, §7); P1.0 done (uncommitted), P1.1 next. This file
tracks P1 of [activity-flow-resource-bc-design.md](activity-flow-resource-bc-design.md)
(§10); P0 is [activity-p0-plan.md](activity-p0-plan.md). Tick items as they land.

**Goal.** After P1, bc solves an action's attributes the way LRM 13.4 says: in
traversal order, each value chosen with **lookahead** over the constraints the
rest of the enclosing activity will impose (13.4.9), across sub-action handles
(13.4.10), inline `with` constraints (13.1.4) and activity constraints. Actions
run in the component instance the LRM assigns them (11.3.1 f, 13.4.5), in any
component of the tree, not only the root. P1 adds **no** flow-object, resource
or inference semantics (P3/P4); a flow reference's attributes (`o.v`) become
readable, which is all P1 needs of them.

**Gate** (design §10, refined here):

1. LRM Ex 179, 183 and 184 run on bc over 200 seeds each with no solve failure
   and every constraint holding, and `a.val` (Ex 183) / `v.val` (Ex 184) never
   takes a value lookahead forbids (≥ 14). With lookahead switched off (P1.6,
   the calibration switch) the same tests **fail** on some seed: proof they test
   lookahead.
2. `act.multi_comp.001` and the five `types.struct.*`/`types.bitpack` entries
   leave `expected/bc.toml`; every other bc entry that P1 closes leaves too
   (the strict list fails otherwise).
3. New corpus tests for Ex 179/180/183/184, inline `with` (incl. Ex 142's
   ordering), an activity constraint, and Ex 50's random component choice pass
   on bc, judged by the corpus checker (P1.7).
4. No silent drop remains in what P1 touches: the four found by the P1 survey
   (§1.2) are translated or refused, and the registry tests still hold.
5. SV golden snapshots byte-identical; op-model and compliance tiers unchanged
   apart from entries P1 closes.
6. The native engine (`zuspec-rt-eng`) refuses a model that uses a P1 opcode,
   by name, rather than running it wrong (P1-D6).

---

## 1. Baseline (surveyed 2026-09-30, after P0 + O6)

### 1.1 What bc does today

- One coroutine per action, keyed by **simple name**; only the root
  component's actions are lowered (`lower.py:176-179`, refusals at `:189`,
  `:236`, `:431`, `:445`).
- A traversal is `ScInvoke(target, inst, inline_constraints)`; bc emits
  `INVOKE target` and drops `inst`. The child always gets a **fresh** `Obj`
  (`vm.spawn_child` has an unused `obj=` parameter, `interp/vm.py:57`).
- Each action solves **its own rand fields, in its own frame**, from a blob
  built at lowering time (`collect_solve_problem`,
  `xf/pss_lower/constraints.py:38`; `build_solve_blob`,
  `bc/lower/constraints.py:898`). A constraint over a non-rand field is
  refused ("not a declared rand var"), and `b1.x` / `o.v` reach bc as
  `ExprAttribute` and are refused ("unsupported constraint expression").
  Parent and child constraints never meet: Ex 179 today either refuses or,
  if the parent constraint is dropped, violates `a < b < c`.
- `ScSolveProblem.members`/`inject`, `SolveStrategy.JOINT_CHAIN` and
  `ScActionInst` exist in ir-core as unused shapes.
- The VM value model is flat: an `Obj` is a list of int slots; there is no
  record value, no nested object, no component storage. `comp` exists only at
  compile time (the static type prefix of `action_type`), so `comp.f()` works
  and `comp.x` does not.
- `yield` reaches bc as `StmtYield` and is refused, although `Op.YIELD` exists
  in both engines.
- The native engine passes the **parent's** `Obj` to a child on
  INVOKE/SPAWN (`zbc_interp.c:278-310`); the Python VM gives it a fresh one.
  A divergence already, whatever P1 does.

### 1.2 Silent drops P0 missed (found by the P1 survey)

| # | Drop | Where |
|---|---|---|
| S1 | `ActivityTraversal.index` (`h[i]`): set by ast2ir, never read; the traversal runs the element type with no element | `lower.py:519-543` |
| S2 | `ActivityAnonTraversal.comp_expr`: ast2ir strips `comp == X` out of a `do T with`, the pass ignores it | `ast2ir._extract_comp_expr`; `lower.py` |
| S3 | `init_bindings`: never read (Python front end only) | `lower.py` |
| S4 | `with` and activity `constraint` bodies keep only `StmtExpr`; an `if`/`foreach`/`unique` inside is dropped | `ast2ir.py` `_extract_inline_constraints`, `ActivityConstraint` build |
| S5 | In `b1 with { x > px; }` the child's `x` and the parent's `px` are both `self.<name>`: the IR cannot tell whose field it is | ast2ir |

S1–S4 are P0-class bugs and are fixed first (P1.0). S5 is not a drop but
makes `with` untranslatable; it is P1.1.

### 1.3 What the LRM demands (quoted in the survey; clause numbers checked)

- **13.4.7 / Ex 179**: sub-action fields get values in the order encountered in
  the activity; `constraint abc_c { a.val < b.val; b.val < c.val; }` holds.
- **13.4.9 / Ex 183**: choosing `a.val` must account for `b` and `c`
  ("restricts the legal values of a.val to 0 to 13").
- **13.4.10 / Ex 184**: lookahead crosses into a compound child's activity
  that has not been traversed yet (`s1.a.val == v.val` ⇒ `v.val ≤ 13`).
- **13.4.8 / Ex 180, 181**: handles are uninitialized on entry to an activity
  block and reset on re-entry (loop iterations); a constraint through an
  uninitialized handle is vacuously satisfied.
- **13.1.4 / Ex 142**: inline `with` names resolve child-first, then parent;
  `this.` reaches the parent. Inline constraints apply to that traversal
  only, and see the most recently selected values of siblings already
  traversed; referencing one not yet traversed is illegal.
- **13.1.9 b.3**: an activity constraint applies in the activity scope
  immediately enclosing it.
- **11.3.1 f, 13.4.5, 9.1.5, Ex 50/52/143**: the component instance of a
  traversal is chosen at random among instances of the right type in the
  context component's subtree, as part of solving, satisfying `comp`
  constraints. `comp` cannot be assigned by the user.

---

## 2. Design decisions this plan proposes (for review)

Each is a choice the design doc left open or that the survey forced. The
recommendation is what the work items assume.

- **P1-D1 — One flattened object per activation.** The **action tree** of an
  exported action — every handle field and every anonymous traversal site,
  recursively through compound types — is laid out statically in ONE `Obj`:
  each node owns a slot range, and a frame carries a **base offset**. Structs
  are flattened the same way (a struct field is N slots), and so is the
  **component tree** (one component `Obj`, each instance a base offset). No
  record values, no references, no heap; the solver's slot model and the
  `arrays` precedent already work this way. *Alternative*: one `Obj` per node
  with reference slots and indirect loads (new value kind in both engines).
  *Cost of the recommendation*: the tree must be finite, so an action that
  (transitively) traverses itself is refused in P1 (P1-D5); `replicate` with an
  iteration label needs a static bound or is refused.
- **P1-D2 — Hoisted per-traversal solve over the committed-structure cone**
  (design §5.3, first use). Each traversal solves one problem: the whole
  activation's rand variables and every constraint in force, with values
  already committed **pinned**, and commits only the traversed node's values.
  The cone includes only nodes whose *structure* is committed. Structure
  commits recursively: entering a scope commits every node of its
  sequential/parallel statements *and their compound subtrees* down to the
  first `select`/`if`/`match` branch or loop body, which commit only when that
  branch or iteration is entered. So Ex 184's `v` sees `s1.a/b/c`, although
  `s1` has not been traversed. That is the
  design's rule, and it means P1 gives full value lookahead, and no structural
  lookahead (a constraint that makes an untaken `select` branch necessary is P5's
  feasibility-filtered choice).
- **P1-D3 — Isolated nodes keep today's code path.** A node whose constraints
  touch nothing outside its own fields (the static cone is a singleton) emits
  today's `SOLVE` unchanged. Only connected cones use the new opcode. This
  keeps every existing model's bytecode, and the rt-eng differential, stable.
- **P1-D4 — `comp` is a solver variable.** A traversal's component instance is
  an enum-like variable over the candidate instances (static: the subtree of
  the context component, filtered by type). `comp == X` constraints (from
  `with`, from S2's `comp_expr`) constrain it; the frame's component base is
  read from the solved value. One mechanism for random choice and for
  steering, and it composes with P3's pool constraints.
- **P1-D5 — Refusals kept in P1**, each located: recursive action trees;
  activity symbols (O2); a handle-array element with a non-constant index
  inside a constraint; a handle traversed twice in one scope where a later
  traversal has a `with` (O-P1-3).
- **P1-D6 — rt-eng refuses P1 opcodes** by name until P8 ports them (gate 6).
  The existing shared-parent-`Obj` divergence (§1.1) is fixed as part of the
  base-offset change, in both engines, because both must agree on what
  INVOKE's operand means.

---

## 3. Work items

Landing order: P1.0 → P1.1 → (P1.2 ∥ P1.3) → P1.4 → P1.5 → P1.6 → P1.7.
Each lands with its tests; the progress log (§8) records the counts.

### P1.0 Close the silent drops (S1–S4) and small independents

- [x] **S1** `h[i]` traversal: the pass refuses a traversal with an index
  (P1.2 gives the element a node). SV warns (it runs the whole handle).
- [x] **S2** `comp_expr`: `comp ==` is now taken out on a **handle**
  traversal too (new `ActivityTraversal.comp_expr`, ir-core); the pass refuses
  either form until P1.5, SV warns.
- [x] **S3** `init_bindings`: refused by the pass when non-empty.
- [x] **S4** a `with` or activity-constraint statement that is not an
  expression (the IR fields are `List[Expr]`) is a located error in ast2ir.
  SV's activity constraint, emitted as a comment claiming it was "handled at
  randomize time", now warns that it is not applied.
- [x] **Found while doing S4 — the constraint translator itself.**
  `_collect_constraint_stmt` kept only expression statements under `if` and
  `->`, dropped `soft`/`default`/`dist` and anything unknown behind a debug
  log, and an `if` whose true branch was empty lost its `else`. For the
  nested cases the whole constraint block usually vanished. Now nested bodies
  translate at any depth (an implication with a non-expression consequent
  becomes `if (c) {...}`; an empty true branch becomes `if (!c) {else}`), and
  an unknown kind is a located error. `soft`, `dist`, `default` and `default
  disable` have no IR form yet: inside a constraint block they are recorded
  on the block's function as `metadata["untranslated"]` (kind, line) and
  `collect_solve_problem` refuses such a block, so bc never solves a weaker
  problem than the one written; outside a block (a `with`, an activity
  constraint) they are located errors. A first cut refused them in ast2ir and
  broke the whole op-model tier: WB DMA's value structs use `default`, and the
  op-model targets never solve them. Registry:
  `test_constraint_stmt_registry.py`; `dist` is unlocated (pssparser request
  P2).
- [x] **`yield`** in bc procedural code: `StmtYield` → `Op.YIELD`
  (`test_bc_yield.py`: parallel bodies interleave, and the control without
  yield does not). Closes `proc.yield.single.001`.
- [ ] ~~**Qualified coroutine keys (O5)**~~ → **moved to P1.5.** While only
  the root's actions are lowered, simple names are unique by construction
  (one component cannot declare two actions of one name), so the refusal
  cannot fire; switching now would churn `exports`/`entry_action` for
  nothing. It lands with the first non-root coroutine.
- [x] **dv-solve**: `SolveCtx.pin(var_id, value)` wrapping `solver_pin_var`
  (`tests/unit/test_ctx_pin.py`: pin holds through a solve, a conflicting
  pin is reported, restore undoes it).

### P1.1 Front end: scope of names in `with`; initializers; activity data

- [ ] **S5**: in an inline `with`, a name the linker resolved to the
  traversed action's field becomes `ExprAttribute(<handle ref>, name)` — the
  same shape a parent constraint `b1.x` has — and a parent name stays
  `self.name`. Read the linker's resolution (`_linked_type_name`-style), never
  re-resolve (fix-at-source rule). For `do T with`, the "handle" is the
  anonymous site's node (P1.2 names it `__site<k>`). `this.x` stays parent.
- [ ] **Traversal initializers (O3)**: IR `initializers: List[(field, Expr)]`
  on both traversal kinds; lowered in P1.4 as assignments before `pre_solve`
  (11.3.1 b steps i–ii). Remove the P0 refusal.
- [ ] **Activity-block data fields** (`action int n;` in an activity block):
  a node-owned slot of the enclosing activation. Remove the P0 refusal.
- [ ] **Labeled `replicate` (O4)**: `L[k].h` names per-iteration nodes; with a
  constant count they are N nodes; otherwise refused (P1-D1).
- [ ] Registry tests updated: the P0 "P1" refusals are now rows that
  translate.

### P1.2 ir-core: the action tree and the cone

- [ ] **Action tree** (`xf/pss_lower/action_tree.py`): from an exported
  action, the static tree of nodes (handle fields, anonymous sites, labeled
  replicate iterations, activity data), each with its type, qualified path
  (`s1.a`), slot range in the activation `Obj`, and the activity scope that
  owns it (for handle reset, 13.4.8). Recursion is a located error.
- [ ] **Constraint collection over the tree**: every type constraint of every
  node, parent member constraints (`b1.x < b2.x`), inline `with` (tagged with
  its traversal site), activity constraints (tagged with their scope),
  rewritten from `ExprAttribute` paths to (node, slot) variables. Non-rand
  fields a constraint reads become variables that are **pinned** at solve time
  from the `Obj` (fixes "not a declared rand var").
- [ ] **Static cones**: connected components of the node/constraint graph.
  A singleton cone keeps today's `ScSolveProblem` (P1-D3). A larger cone gets a
  `ScScopeProblem` (new IR node, docstring citing 13.4.9/13.4.10): its
  variables as (node, slot), its constraints each tagged with the structure
  condition under which it is in force (always / on entry to scope S / at
  traversal site T only).
- [ ] `ScInvoke` gains `node` (the child's node id → base offset); the
  traversal of a node in a connected cone lowers to
  `SOLVE_NODE(problem, node)` inside the child coroutine in place of `SOLVE`.
- [ ] Serializer round-trip and `test_activity_ir_registry.py` rows for the new
  nodes.

### P1.3 bc: flattened structs and attribute paths (independent of P1.2)

- [ ] `lower/types.py`: `DataTypeStruct` → a flattened layout (slot map by
  field path), copy/param/return as N moves. Closes `types.struct.*` and
  `types.bitpack.001`.
- [ ] `_resolve_name`/`_store`: `ExprAttribute` chains over self, struct
  fields and (after P1.4) child nodes resolve to slots; package constants
  (`cfg_pkg::X`) fold at lowering. Closes `types.const.package.001` and the
  `proc.compile_if.001` entry if that is its only gap.
- [ ] Constraint lowering accepts the same resolved paths.

### P1.4 bc: activations, base offsets, the scope solve

- [ ] **Frame base offset**: every `LD_FIELD`/`ST_FIELD` is relative to the
  frame's base; `INVOKE`/`SPAWN` take the child node's base (P1-D1). Python VM
  and `zbc_interp.c` together (fixes the shared-`Obj` divergence).
- [ ] **Scope solve context**: one per activation of a connected cone; holds a
  `SolveCtx` over the cone's blob and the committed (node, slot) values.
  `SOLVE_NODE`: checkpoint → pin committed values and runtime-read non-rand
  values → enable the constraints in force (scope entry, this site's `with`) →
  solve with the frame's seed draw → write back the node's slots → commit →
  restore. UNSAT is the 13.4.13-style error naming the traversal and the
  constraints, never a silent fallback.
- [ ] **Handle reset** (13.4.8): on entry to an activity block (and each loop
  iteration), un-commit the nodes that block owns.
- [ ] **Lifecycle order** (11.3.1 b): initializers → `pre_solve` → solve →
  `post_solve` → body/activity, per traversal, unchanged in shape; only the
  solve step changes.
- [ ] **Parent reads of child attributes** after traversal (`b1.x` in the
  parent's exec code) read the node's slots.
- [ ] rt-eng: refuses `SOLVE_NODE` by name (P1-D6) and implements the base
  offset.

### P1.5 Components: instance tree, `comp`, non-root actions

- [ ] **Component instance tree** in ir-core (this is the first slice of P2's
  static elaboration: instances only, no pools): paths, types, component `Obj`
  layout (flattened, P1-D1), init blocks per instance.
- [ ] bc: component `Obj` built and `init_down`/`init_up` run per instance in
  LRM order before the root action; `comp.x` reads/writes and deep calls
  (`comp.a.f()`, `ch[i].f()` with constant `i`) resolve through the frame's
  component base. Closes the `comp.*`/`sync.channel.*` entries whose only gap
  is the path (each re-checked; an entry that then fails differently is
  re-listed with its next gap).
- [ ] **Qualified coroutine keys (O5)**, moved from P1.0: key coroutines by
  qualified name, drop the duplicate-simple-name refusal; `exports` keep
  accepting a simple name when it is unambiguous.
- [ ] **`comp` choice** (P1-D4): a traversal of a non-root action gets a
  `comp` variable over its candidates; `comp ==` (S2, handle and `do` forms)
  constrains it. Remove the root-only refusals (`lower.py:176, 236, 431,
  445`). Closes `act.multi_comp.001`.
- [ ] 9.1.5.1: a traversal of an action whose component is not in the
  context's subtree is a located error (Ex 51).

### P1.6 Lookahead calibration and fault injection

- [ ] `PSSToScenarioPass(lookahead=False)` (test-only switch, design §5.4
  `greedy-nohoist`): cones are cut to the traversed node. Ex 183/184 tests run
  both ways and require the no-lookahead run to fail on some seed (gate 1).
- [ ] Fault switch `commit-aux` (design §9.6) deferred to P5; noted here.

### P1.7 Corpus and checker (pss-corpus)

- [ ] Checker: handle names on activity nodes; compound types carry `fields`
  and `constraints`; a parent constraint over `a.val` binds to the matched
  occurrence; handle reset per 13.4.8 for loops (Ex 180).
- [ ] New L3 tests: `act.solve.order.001` (Ex 179), `act.solve.reset.001`
  (Ex 180), `act.lookahead.001` (Ex 183), `act.lookahead.sub.001` (Ex 184),
  `act.with.001`, `act.with.order.001` (Ex 142 legal form), `act.constraint.001`,
  `act.comp.random.001` (Ex 50, needs `comp.pct_id` from COMPLIANCE-DESIGN
  §4.3), `act.comp.steer.001` (Ex 143).
- [ ] Mutants: a trace violating a parent constraint must be caught.
- [ ] O7 (§7): `"traced": false` on atomic types (checker + lint), observers
  and `obs` records (§4.4); tests `act.traverse.bodiless.001`,
  `act.compound.pre_post.001`.

---

## 4. Tests in pssc (per item)

Construct-level tests go through bc + dv-solve under Python, never generated
SV/C (the construct-test harness). Each refusal gets a located-error test.

| Item | Tests |
|---|---|
| P1.0 | `test_activity_silent_drops.py` (S1–S4 each refused or translated); `test_bc_yield.py`; `test_coroutine_qualified_keys.py`; dv-solve `test_pin.py` |
| P1.1 | `test_with_scoping.py` (child vs parent vs `this.`), `test_traversal_initializers.py`, registry rows |
| P1.2 | `test_action_tree.py` (layout, recursion refused), `test_scope_cone.py` (singletons keep `SOLVE`; cones, tags) |
| P1.3 | `test_bc_struct_values.py`, `test_bc_attribute_paths.py` |
| P1.4 | `test_lookahead_ex179_183_184.py` (200 seeds, calibration), `test_handle_reset.py` (Ex 180), `test_with_semantics.py`, `test_activity_constraint.py`, `test_scope_unsat_error.py`, rt-eng refusal test |
| P1.5 | `test_component_tree.py`, `test_comp_choice.py` (Ex 50 distribution over 3 instances; Ex 143 steer), `test_comp_attr_paths.py`, Ex 51 refusal |

## 5. Docs

- [ ] Design doc §2.6 "After P1"; §10 P1 row marked done with the gate record.
- [ ] AGENTS.md "Activities on bc": the flattened-activation rule (P1-D1), the
  cone rule (P1-D2/D3), `comp` as a variable (P1-D4), and the tests that hold
  each.
- [ ] be-bc `docs/spec/`: `SOLVE_NODE` and the base-offset operand (the
  primitive spec P7 builds on).
- [ ] ir-core docstrings on `ScScopeProblem`, `ScInvoke.node`, the action tree.
- [ ] Corpus README: the new `act.*` tests; COMPLIANCE-DESIGN notes the checker
  scope change.

## 6. Risks

- **Cone size** on real models (WB DMA, example2): one activation's connected
  cone could be most of the tree. P1 measures it (cone telemetry, design §4.7)
  and does not cap; D15's cap lands when data says it is needed.
- **Blob built at lowering vs runtime pins**: pins go through `solver_pin_var`
  after `checkpoint`; conditional constraints need an enable literal per tag.
  If dv-solve's builder cannot express "constraint active iff selector", tags
  become separate aux problems added with `add_constraint` after the
  checkpoint (both exist; the choice is made in P1.2 by a probe).
- **Seed stability**: models with only singleton cones keep their exact
  draws (P1-D3); models with connected cones change their scenarios. That is
  allowed by D9 (versions may change scenarios) and is stated in the release
  notes.

## 7. Review decisions (2026-09-30)

- **O-P1-1 → flat.** One flattened object per activation (P1-D1), "for now":
  the per-node alternative stays open if a later phase needs references.
- **O-P1-2 → accepted.** No structural lookahead in P1 (P1-D2).
- **O-P1-3 → reject.** A handle traversed more than once in the same
  activity scope, where a later traversal carries a `with`, is a located
  error. (Re-traversal in a new iteration or a re-entered block is a new
  scope entry, 13.4.8, and stays supported, as does `a; a;` with no `with`.)
- **O-P1-4 → accepted.** rt-eng refuses P1 opcodes by name until P8 (P1-D6).
- **O-P1-5 → deferred.** Activity symbols stay refused.
- **O-P1-6 (O7) → decided.** (a) An atomic type may carry `"traced": false`:
  the checker keeps it in the structure and consumes no record for it; lint
  requires the type to have no `exec body`. (b) No schema change: a compound's
  solve-time blocks are observed through COMPLIANCE-DESIGN §4.3 (order
  signatures in a non-rand attribute) and §4.4 (an observer action tied by
  `with`), which P1.7 implements in the checker (`obs` records). P1.7 adds
  `act.traverse.bodiless.001` and `act.compound.pre_post.001`.

## 8. Progress log

| Date | Item | Note |
|---|---|---|
| 2026-09-30 | plan drafted | from three code surveys (ir-core pass, bc solve/INVOKE, LRM + front end + corpus) |
| 2026-09-30 | P1.0 | S1–S4, constraint-statement registry + ledger, `yield`, `SolveCtx.pin`; O5 moved to P1.5. Unit 1931 passed / 4 (docs); progseq 8 pre-existing; compliance bc 165 / 42 xfailed (`proc.yield.single.001` closed), op-model-sv 80 / 19; sim 13 pre-existing; ir-core 83, be-bc 310, dv-solve 790 (ex. scipy-only `test_dist_quality.py`) |
