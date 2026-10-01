# P1 "Compound-scope solving": implementation, test and doc plan

Status: **reviewed** (2026-09-30, §7); P1.0–P1.4 committed; P1.5 done (uncommitted); P1.6 next. This file
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

- [x] **S5**: a name the linker resolved in the traversed action (its path
  has an `ElemKind_Inline` step) is rooted at a new ir-core node,
  `TypeExprRefTraversed`; a name of the enclosing scope stays `self.name`.
  *Changed from the draft* (`ExprAttribute(<handle ref>, name)`): one root
  serves both traversal forms, since `do T with` has no handle to name until
  P1.2 numbers its site, and P1.2 binds the root to the node either way.
  The linker's resolution decides, never a re-resolution (fix-at-source rule).
- [x] **`this`** (found while doing S5): `this.q` translated as
  `self.this.q` everywhere, not only in a `with`. A path ending in
  `ElemKind_This` now roots at `self`.
- [x] **Traversal initializers (O3)**: `initializers: List[(target, value)]`
  on both traversal kinds, target rooted at `TypeExprRefTraversed`. A handle
  traversal carries its DECLARATION's initializers first (11.3.1 b i-ii) —
  on a handle declared in an action body they were silently dropped. The
  ast2ir refusal is gone; `PSSToScenarioPass` refuses until P1.4; SV warns.
- [x] **Activity-block declarations**: a handle or data field declared in an
  activity block is an `ActivityFieldDecl` (a handle used to be skipped, a
  data field refused). The pass lowers a handle declaration to nothing (its
  traversal names its type) and refuses a data field until P1.4. A field
  declared `action` (Ex 173) now has `Field.action_qualified`, in an action
  body too, where it read as a plain non-rand field.
- [x] **Labeled `replicate` (O4), front-end part**: already carried
  (`ActivityReplicate.label` is the `R[]:` label, `R[0].b.x` translates as
  `self.R[0].b.x`). The N nodes per iteration are action-tree work: moved to
  P1.2. A *statement* label on a replicate (`L: replicate ...`) stays a
  located error: `ActivityReplicate.label` shadows the base `label`.
- [x] Registry tests updated (`test_activity_ir_registry.py` rows for
  `ActivityFieldDecl` and initializers); `test_activity_names.py` (17).

### P1.2 ir-core: the action tree and the cone

- [x] **Action tree** (`xf/pss_lower/action_tree.py`; IR `ScActionTree`,
  `ScActionNode`, `ScActivityScope`, `ScTraversalSite`): from an exported
  action, every handle attribute (each element of a handle array), each
  activity-block handle (`ActivityFieldDecl`), each anonymous site, and each
  iteration's instance under a labeled `replicate` (constant count, else
  refused, O4), recursively. A node owns its type's layout
  (`layout.object_layout`); its children's subtrees follow in declaration
  order, so a type's subtree is the same wherever it sits. Each node records
  the scope that resets it (13.4.8): its parent's ACTIVITY scope for an
  attribute handle, its block for a block handle. A site in a loop is ONE
  node. Recursion is a located error naming the chain.
  `ScenarioModule.trees[export]` holds one per export.
- [x] **Constraint collection over the tree**: each node type's constraints
  (and its struct attributes'), activity constraints (tagged with their
  scope), inline `with` (tagged with its site; `TypeExprRefTraversed` is the
  site's target), resolved to absolute slots through handle paths
  (`s1.a.val`, `arr[1].s.f`). A non-rand slot a constraint reads is a
  variable with `rand=False`, pinned at solve time. A reference the tree
  cannot resolve (`comp.x`, until P1.5) stays as written for bc to refuse.
- [x] **Static cones** (`ScScopeProblem`, `ScScopeVar`,
  `ScScopeConstraint` with `ScopeConstraintKind` TYPE / ACTIVITY / WITH):
  connected components of nodes tied by constraints. A node tied to no other,
  with only its type's constraints, is in no cone and keeps its
  `ScSolveProblem` (P1-D3). A `with` makes a cone even of one node: it holds
  at that traversal only.
- [x] `ScInvoke.child_base` (the plan said `node`): the child's subtree offset
  from the invoking action's base, static per (type, site). **Change from the
  draft:** a coroutine is per action TYPE, and one type can be a singleton
  node in one place and a cone member in another, so "SOLVE_NODE in place of
  SOLVE" cannot be decided per coroutine. P1.4's solve looks the frame's node
  up in the tree's cones at run time instead; the type's `ScSolveProblem`
  stays as the singleton path.
- [x] Side effects: `Field.type_qname` (the linker's name for a field's
  declared type; a handle field's `DataTypeRef` names it as written). P1.0's
  S1 refusal is lifted for a CONSTANT index (`bs[1]` has a node); a computed
  index is still refused. A labeled `replicate` is still refused by the pass
  (P1.4 unrolls it), with the O4 message when its count is not constant.
- [x] No new activity IR node, so no registry row; the scenario dialect has no
  serializer, so there is nothing to round-trip.

### P1.3 bc: flattened structs and attribute paths (independent of P1.2)

- [x] **One layout** (`xf/pss_lower/layout.py`, ir-core): a plain-data
  struct is one slot per scalar leaf, base struct's fields first, named by its
  dotted path (`s.csr.eol`). `ScField` is now one per slot; the solve
  problem, the scenario pass and bc's locals all take slots from it. An
  action handle, flow reference or array stays one opaque slot, so a model
  without struct attributes keeps its exact slots (and bytecode).
- [x] bc (`lower/types.py`, `lower/procedural.py`): a struct value is a
  `StructT` held in a `_Place` (one location per leaf), never a register:
  locals with their fields' initial values, deep copy, `==`/`!=` field by
  field (7.8), a struct parameter as a handle to the caller's instance
  (20.3.2), struct return. A struct where a scalar is needed is an error.
  Closes `types.struct.{copy,fields,param,return}.001`, `types.bitpack.001`.
- [x] Solve: a rand struct attribute is one variable per rand leaf (it was ONE
  32-bit variable), under its type's constraints and its bases', with `self`
  meaning the attribute; `self.s.f` paths resolve to leaf slots. A struct's
  `pre_solve`/`post_solve` is refused (nothing runs it yet).
- [x] **Found and fixed: attribute initial values were never applied** on bc
  (`bit[4] g = 3;` read 0). The pass now emits them as the object's first
  block, `ScExecBlock(kind="init")`, a struct field's initializer rooted at
  its attribute (`layout.prefix_self`).
- [x] Constants (ast2ir): a `static const` folds from an expression
  initializer and from `true`/`false`, and a reference follows the linker's
  target to its declaration (`_linked_static_const`), so a component's
  `static const` folds too. They used to become `self.cfg_pkg.X`. Closes
  `types.const.package.001` and `proc.compile_if.001` on bc, op-model-py and
  op-model-sv.
- [x] Constraint lowering accepts the same resolved paths (they arrive as
  `ExprRefField` slots).

### P1.4 bc: activations, base offsets, the scope solve

- [x] **Frame base offset**: `LD_FIELD`/`ST_FIELD` and SOLVE write-back are
  relative to the frame's base. `INVOKE` with `INSTR_F_NODE` runs the child at
  `base + imm`; `arg2` names the traversal site (`ScInvoke.site`). The root's
  object is the entry type's subtree layout (`ScCoroutine.subtree`). Done in the
  Python VM and `zbc_interp.c` together, so both engines agree on INVOKE's
  operand. An INVOKE without the flag keeps its M1 meaning (hand-built
  scenarios).
- [x] **Scope solve** (`interp/activation.py`, `docs/spec/activation.md` in
  be-bc): `SOLVE_NODE` solves the frame's node in its cone with committed
  values pinned and the constraints in force enabled, then commits the node.
  Non-rand values are pinned too: the object's value for a started node, the
  constant initial value otherwise. **Change from the draft:** there is no
  solver checkpoint and no enable literal per tag. The problem for each set of
  constraints in force is built from the cone's IR and cached; dv-solve's
  `pin` fixes the committed values. Every cone is built once with all of its
  constraints at lowering, so a constraint the solver cannot take is a lowering
  error. UNSAT is a `ScopeUnsatError` naming the traversal, the constraints in
  force and the pinned values.
- [x] **Handle reset** (13.4.8): entering a node resets its subtree.
  `SCOPE_ENTER` on entry to any block resets the nodes traversed in it, and for
  a branch body records that it was entered. Liveness follows P1-D2. One
  refinement: a loop body that surely runs (a positive constant count, or
  do-while) is `LOOP_BODY_CERTAIN`, which commits with its loop. Without it,
  Ex 180's `a` was chosen blind to `b` and `c` and failed on some seeds.
- [x] **Lifecycle order** (11.3.1 b): initializers → `pre_solve` → solve →
  `post_solve` → body/activity. Traversal initializers are now lowered, no
  longer refused: `ScInvoke.init` carries the child's initial values, then its
  handle's initializers, then the traversal's. The parent runs them on the
  child's slots, and the child starts past its own initial values
  (`INSTR_F_INITED`). Ex 84 holds.
- [x] **Parent reads of child attributes**: `self.b1.x` and `self.bs[1].x`
  read the node's slots through the subtree layout (procedural `_index_handles`).
  They are not checked against an uninitialized handle (Ex 181's error); that
  is open.
- [x] `with` and activity `constraint` are no longer refused by the pass (the
  tree holds them). A labeled `replicate` is unrolled onto its nodes, each
  iteration with its index variable fixed (`layout.subst_names`).
- [x] rt-eng implements the base offset. It refuses `SCOPE_ENTER`,
  `SOLVE_NODE` and an `INSTR_F_INITED` INVOKE at load, naming the opcode
  (P1-D6).
- [ ] Deferred from P1.4:
  - an activity data field (`action bit[4] n; n;`, 11.3.1 a) has no node kind
    yet and stays refused;
  - a constraint naming a replicate iteration (`R[1]…`) stays unresolved;
  - `replicate` directly in `parallel`/`schedule` stays refused.

### P1.5 Components: instance tree, `comp`, non-root actions

- [x] **Component instance tree** (ir-core `xf/pss_lower/comp_tree.py`;
  `ScComponentTree`, `ScCompInstance`, `ScCompInit`): every instance under the
  root, a slot range of ONE component object (P1-D1), a type's subtree laid out
  the same wherever it is instantiated, base type's fields first; component
  arrays need a constant size. Instances are numbered in pre-order, so a
  sub-instance is its parent's number plus a static offset.
- [x] bc: the component object is built and constructed by `$comp_init`
  before the root action: every instance's initial values, `init_down`
  top-down, `init_up` bottom-up (Ex 281's order). `comp.x` reads, component
  functions reading and writing their own instance (`self.x`), and deep calls
  (`comp.a.f()`, `self.sub.f()`, `comp.ch[1].f()`) resolve through the frame's
  instance with static offsets: `LD_COMP`/`ST_COMP` (be-bc
  `docs/spec/components.md`). Component inheritance: a base's fields, its
  init blocks when the derived type has none, virtual functions. Closes
  `comp.func.calls`, `comp.init.order`, `comp.init.solve_fn`,
  `comp.instances`, `comp.attr.struct`. `comp.array` (computed index), the
  three `sync.channel.*` (channel built-ins) and `types.string.match`
  (string compare) are re-listed with their next gap.
- [x] **Qualified coroutine keys (O5).** **Change from the draft:** keys are
  qualified *relative to the root component*: the root's actions keep their
  simple names (`T`), another component's are qualified (`sub_c::S`). Unique
  either way, and a model of the root's actions alone keeps its coroutine
  names, so be-sw (which uses them as C identifiers) and every golden are
  unchanged. `coro_key` resolves an export or entry given as a key, a
  qualified name, or an unambiguous simple name; an ambiguous one is an error.
- [x] **`comp` choice** (P1-D4): a node's candidates are the instances of its
  action's component type under its parent's instance. One: static. More: a
  variable of its cone, in a slot past the action subtrees
  (`ScActionNode.comp_slot`), kept among the candidates by a `COMP` constraint;
  `SOLVE_NODE` sets the frame's instance from it. `comp == X` (handle and `do`
  forms) is a `WITH` constraint between instances, decided at lowering when
  both are static. The root-only refusals are gone. Closes
  `act.multi_comp.001`. Ex 50 chooses all three instances about equally
  (300 seeds); Ex 143's steer holds.
- [x] 9.1.5.1: a node with no candidate is a located error (Ex 51).
- [x] Found and fixed on the way:
  - **activity statements had no source location**: ast2ir never set `loc`,
    so every "located" refusal of an activity statement printed none. ast2ir
    now sets it (file from the parser's `file_map`, passed to `translate`).
  - **O-P1-3 was never implemented**: a handle traversed again in the same
    scope with a `with` was accepted. Now a located error. The pass now
    reports why an action has no tree, instead of "needs the action tree".
  - The engine refuses an image that constructs a component tree
    (`ZBC_HDR_COMP_INIT`, new header flag; rt-core's generated `zbc_format.h`)
    as well as `LD_COMP`/`ST_COMP`, so an init block that only calls imports
    is never silently skipped.
- [ ] Deferred from P1.5:
  - a component-array element with a computed index (`comp.ch[j]`,
    `foreach (ch[i])` in a component function): no indexed access on bc;
  - a constraint reading a component attribute (`v < comp.f`): refused by
    name. Needs a pin from the component object, and a table lookup when the
    instance is a choice;
  - candidates are found from the action's declaring component type, so an
    instance only a derived type of it adds is not one;
  - `pre_solve` reading `comp` when the solve chooses the instance is a
    run-time error (it runs before the choice);
  - channel built-ins (`try_put`/`try_get`) and string compare.

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
| P1.1 | `test_activity_names.py` (child vs parent vs `this.`, initializers, declarations), registry rows |
| P1.2 | `test_action_tree.py` (layout, recursion refused), `test_scope_cone.py` (singletons keep `SOLVE`; cones, tags) |
| P1.3 | `test_bc_struct_values.py`, `test_bc_attribute_paths.py` |
| P1.4 | `test_lookahead.py` (Ex 179/180/183/184, 200 seeds; the calibration run is P1.6), `test_scope_solve.py` (`with`, activity constraints, child reads, Ex 84, labeled replicate, the unsat error), rt-eng `test_engine_activation.py` (base offset, refusals) |
| P1.5 | `test_component_tree.py` (layout, Ex 281 order, initial values, inheritance, per-instance state, index refusals), `test_comp_choice.py` (Ex 50 distribution over 3 instances, Ex 143 steer, static steer, a child relative to a chosen parent, Ex 51 refusal, qualified keys); component paths are covered there and by the five corpus entries closed; rt-eng `test_engine_components.py` |

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
| 2026-10-01 | P1.1 | `TypeExprRefTraversed` (S5), `this` fixed, initializers carried (decl first), `ActivityFieldDecl`, `Field.action_qualified`; O4 remainder to P1.2. Unit 1952 passed / 4 (docs); progseq 8 pre-existing; compliance bc 165 / 42, op-model-sv 80 / 19; ir-core 83, be-bc 310; be-sw unchanged from baseline |
| 2026-10-01 | P1.3 | flattened struct layout (ir-core `layout.py`), struct values on bc, rand struct attributes, attribute initial values applied (found), `static const` folding through the linker. Closes 11 strict entries: 5 struct/bitpack + 2 const on bc, 2 const each on op-model-py/sv. Unit 1980 passed / 4 (docs); progseq 8 pre-existing, goldens identical; compliance 256 / 50 xfailed; ir-core 88, be-bc 310; be-sw unchanged from baseline |
| 2026-10-01 | P1.2 | `ScActionTree` per export (nodes, scopes, sites), cones (`ScScopeProblem`, TYPE/ACTIVITY/WITH), `ScInvoke.child_base`, `Field.type_qname`; S1 lifted for a constant index. Solve-node choice moved to run time (P1.4), see P1.2. Unit 2002 passed / 4 (docs); progseq 8 pre-existing, goldens identical; compliance 256 / 50 xfailed; ir-core 88, be-bc 310; be-sw unchanged from baseline |
| 2026-10-01 | P1.4 | one-object activation (`INSTR_F_NODE` base offsets in both engines), `SOLVE_NODE` cone solve with lookahead (per-enabled-set problem + `pin`, no enable literals), `SCOPE_ENTER` resets, `LOOP_BODY_CERTAIN`, traversal initializers (`ScInvoke.init`, `INSTR_F_INITED`), labeled `replicate` unrolled, child reads through handles; rt-eng refuses the P1 ops at load. Found, not fixed: a constraint's `x + 1` wraps at the operand width in bc's own SOLVE too. Unit 2018 passed / 4 (docs); progseq 8 pre-existing; compliance 256 / 50 xfailed (no entry closes: none waits only on P1.4); ir-core 89, be-bc 310, rt-eng 102; be-sw unchanged from baseline |
| 2026-10-01 | P1.5 | component tree as one object (ir-core `comp_tree.py`), `$comp_init` construction (Ex 281 order), `LD_COMP`/`ST_COMP`, component functions in their instance, root-relative coroutine keys (`coro_key`), `comp` choice as a cone variable with `comp ==` steering, Ex 51 refusal; found and fixed: activity statements had no location, O-P1-3 not implemented. Closes 6 strict bc entries (`act.multi_comp` + 5 `comp.*`); 5 re-listed with their next gap. Unit 2040 passed / 4 (docs); progseq 8 pre-existing, goldens identical; compliance 262 / 44 xfailed; ir-core 89, be-bc 310, rt-eng 105, rt-core 38; be-sw unchanged from baseline |
