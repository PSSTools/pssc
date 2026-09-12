# System tests for PSS 3.1 generic constraints — design for review

Status: **review pass 1 incorporated** (§8 records the decisions). Phases 0, 1a,
1b, **W1b**, **W1a**, **W2**, **W3** and **W6** are implemented (§7, §7.2.1–7.2.5);
GC-1,
GC-2 (bar 2.4), GC-4.3/4.4, GC-5.3/5.4/5.5, GC-6.3/6.4, GC-9.1/9.2, GC-3.7, GC-10,
GC-11
and every NEG case pass end to end. **Every frontend workstream has landed**; what
is left is
gated on the five non-frontend blockers in §7.4. §7.1 is a *measured* capability map, regenerable
from
`tests/unit/integration/_generic_constraint_probe.py`.

Scope: a system-level (PSS-source-in, solved-values-out) test suite for PSS 3.1
*generic constraints*, run from Python through the **bytecode (bc) backend** with
the **dv-solve** solver.

Source of truth for every rule cited below:
`PSS 3.1 Public Review Draft 2026.08.28 (2).md`, §13.1.1 (member constraints),
§13.1.2 (generic constraints), §13.1.3 (constraint inheritance), §13.1.9 (forall),
§13.3 (default constraints, rule g), §13.4.11 (lookahead and generic constraints),
and Annex B.14 (grammar).

---

## 1. What the spec actually requires

### 1.1 The two declaration forms

```
generic_constraint_bool  ::= [static] constraint identifier generic_constraint_params constraint_set
generic_constraint_value ::= [static] constraint generic_constraint_data_type identifier
                                      generic_constraint_params expression_constraint_item ;
generic_constraint_params ::= ( [ generic_constraint_param { , generic_constraint_param } ] )
generic_constraint_param  ::= [const] generic_constraint_data_type identifier
generic_constraint_data_type ::= numeric | data_type
```
— Syntax58, §13.1.2.1

The *parameter list is what makes a constraint generic*, not the name. `constraint
c { ... }` is a fixed constraint; `constraint c() { ... }` is a generic constraint
with zero parameters (Example136 vs Example137). This distinction — an empty paren
pair changing when the constraint holds — is the single most important thing the
suite has to pin down, and it is easy to get wrong in a frontend.

### 1.2 The core semantic rule

> generic constraints […] only hold when they are referenced by the user by
> traversing them in an activity (see 13.4.11) or referencing them inside a
> constraint. […] Generic constraints only apply once they are referenced,
> directly or indirectly, from a fixed constraint. (§13.1.1)

So an unreferenced generic constraint is *inert*. A test that only checks
"referenced → constraint holds" is half a test; the other half is "unreferenced →
the solver is free", which must be checked statistically (§6).

### 1.3 Rules that each need their own test

| # | Rule | Cite |
|---|---|---|
| a | Generic constraints may not declare local variables | §13.1.2 a |
| b | A value-yielding generic constraint is a single expression, usable anywhere an expression of that type is legal | §13.1.2 b |
| c | When a generic constraint shadows a base-type constraint, return and parameter types shall match | §13.1.2 c |
| d | Recursion is supported provided it is gated by a non-random expression | §13.1.2 d |
| e | Declarable in struct, action, component scope; in package scope always `static` | §13.1.2 |
| f | Named fixed **or** generic constraint in a subtype shadows the same name from the supertype | §13.1.3 |
| g | `default` / `default disable` may not be used under a generic constraint | §13.3, rule g |
| h | A `forall` in a generic constraint takes effect only for the traversal that activates it | §13.1.9 b.3, Example150 |
| i | A generic constraint activated in an activity holds for that branch **and the remainder of the activity**; the tool must look ahead | §13.4.11 |

`numeric` as a parameter type (Example139) is explicitly dual-purpose — the same
constraint text must work for integer and floating-point actuals. Per §8.2 this is
treated as a user convenience that the compiler reifies to a concrete type early,
which is why the suite tests *specialization* (GC-3.7/3.8) rather than a
polymorphic runtime.

### 1.4 The deprecated `dynamic` spelling

> NOTE—dynamic constraints are deprecated. Their functionality is replaced by a
> generic constraint with no parameters. (§13.1.1)

Grammar still admits `[dynamic] constraint identifier constraint_block`. The AST
already carries `is_dynamic` on `GenericConstraintDeclBool`
(`packages/pssparser/python/pssparser/ast.pyi:612`). The suite should assert that
`dynamic constraint c { ... }` behaves *identically* to `constraint c() { ... }`,
and (separately, cheaply) that it emits a deprecation warning rather than silently
diverging.

---

## 2. Where this tree stands today

This matters, because it determines how much of the suite is a *regression* test
and how much is a *specification* test written ahead of the implementation.

**Existing constraint coverage: broad for core kinds, zero for generic.** The
integration suite already covers static, conditional/if-else, logical, set/`in`,
`unique`, `foreach`, and collection constraints, plus `forall` at both IR level
(`test_forall.py`) and runtime (`test_forall_struct_rt.py`). A grep for
`GenericConstraint` across `src/pssc/`, `tests/`, and `zuspec-ir-core/src/`
returns nothing. `ir.ConstraintBlock.is_dynamic` exists
(`packages/zuspec-ir-core/src/zuspec/ir/core/constraint.py:109-114`) but nothing
sets or tests it.

**Parsing: present.** pssparser builds `GenericConstraintDeclBool` /
`GenericConstraintDeclValue` / `GenericConstraintParam` nodes, and there is
already a small parse-only suite at
`packages/pssparser/tests/python/parsing/test_generic_constraints_31.py` covering
bool-in-package, bool-in-action, value/`numeric`, value/typed, and one syntax
error.

**AST → IR: fixed in phase 1b (2026-09-11).** What follows records what 1b found
and changed — kept because the failure mode is the argument for the whole test
category.

*As measured by the Phase-0 spike, before the fix:* `src/pssc/ast2ir.py` had no
*handler* for either generic constraint node, but it did not ignore them either.
`GenericConstraintDeclBool` **subclasses `ConstraintBlock`**, so a generic
constraint matched the ordinary `isinstance` dispatch and was lowered as a fixed
constraint. For

```pss
constraint lim_lt(int lim) { x < lim; }
```

the emitted `ir.Function` had `args=None` — the parameter list dropped — and
`lim` translated to `ExprAttribute(TypeExprRefSelf(), 'lim')`, a reference to a
field the action does not have. The function carried
`metadata={'_is_constraint': True}`, so it was collected into the
`ScSolveProblem` unconditionally.

The consequence was not a missing feature but a **silent change of meaning**: a
generic constraint the LRM says is inert was instead in force, against a phantom
variable. In practice it made the problem unsat — the spike's `x > 200` alongside
an unreferenced `x < lim` failed in dv-solve with `CompileUnsatError: Domain
became empty during compile-time bound tightening`. A model using the construct
was mis-compiled rather than rejected.

**What phase 1b changed.** Two places, because the defect had two halves:

* `ast2ir._translate_constraint_block` now detects a generic constraint
  (`GenericConstraintDeclBool`, or a plain block with `is_dynamic` — the
  deprecated spelling), translates its parameter list into `ir.Arguments`, puts
  the parameter names in scope over the body so `lim` becomes `ExprRefLocal`
  rather than `self.lim`, and marks the result `_is_generic_constraint` instead
  of `_is_constraint`.
* `PSSToScenarioPass` (`zuspec-ir-core`) does not consult `_is_constraint` at
  all: it collects **every** non-lifecycle function on an action as a constraint
  block (`_lower_atomic`, `_lower_compound`). So dropping the `_is_constraint`
  mark was not enough on its own; both collection sites now go through
  `_is_pending_constraint`, which skips generic constraints. *That broader
  assumption — non-lifecycle function ⇒ constraint block — is still there and is
  worth revisiting separately.*

Also removed: `ast2ir` mapped `is_dynamic` to `metadata['is_soft']`, conflating
`dynamic constraint` with a soft constraint. Nothing now sets `is_soft` from the
PSS frontend; `soft`/`default` constraint items are still unimplemented in
`_collect_constraint_stmt`.

**Linking a *reference*: fixed in phase 1a (2026-09-11).** Before it, pssparser
did not enter generic constraints into the symbol table, so every reference form
failed at link:

| reference form | diagnostic (before 1a) |
| --- | --- |
| `lim_lt(20)` in the same action | `unknown identifier 'lim_lt'` |
| `g()`, zero-parameter | `unknown identifier 'g'` |
| `plus1(10)`, value-yielding | `unknown identifier 'plus1'` |
| `p::gt_zero(x)`, package-scope | `'p' has no member named 'gt_zero'` |

The cause was the same inheritance relationship that produced the `ast2ir`
defect: `IGenericConstraintDeclBool` derives from `IConstraintBlock`, so it
reached `TaskBuildSymbolTree::visitConstraintBlock`, which calls the **unnamed**
`addChild()` — right for a fixed constraint block, which cannot be referenced,
wrong for a generic one, which is called by name. Both declaration forms now
register their name (`visitGenericConstraintDeclBool` /
`...DeclValue`), and `TaskResolveRefs::checkCallArity` gained a branch for them:
a generic constraint has no `IFunctionPrototype`, so without one every reference
would be rejected as `'lim_lt' is not a function`.

Two changes in behaviour fall out of making the name a symbol. A generic
constraint that collides with a field in the same scope is now a duplicate
declaration (silent before), and arity is checked at the reference
(`too many arguments to constraint 'lim_lt': expected 1, got 2`).

**Instantiating a reference: done for same-type references (phase 1a, pssc
side).** `ast2ir._inline_generic_constraints` runs once per type after every
constraint on it is translated — so a reference may precede the declaration it
names — and replaces each statement-level reference with the generic's body,
substituting arguments for parameters (`_subst_locals`). Instantiation is
recursive, with a cycle check: a generic constraint reachable from itself is
reported rather than expanded.

Two reference forms are still not instantiated: a **package-scope** one
(`p::lt(x, 20)`) arrives as `ExprRefPathStaticRooted`, which no
constraint-statement handler translates, and its body lives on the package rather
than on the referencing type — it links, but is dropped (GC-5, phase 2). And a
reference in **value position** needs the value-yielding form (GC-3).

**Backend constraint lowering: good coverage, known edges.**
`packages/zuspec-be-bc/src/zuspec/be/bc/lower/constraints.py` lowers ir-core
constraints to a relocatable dv-solve blob and documents its M1 scope in the module
docstring: arithmetic/bitwise/shift, relational + logical, chained compares, `in`
over range lists (including disjoint unions), `||` including the multi-variable
selector encoding, implication and if/else via clausal `!A || consequent`, `dist`,
`soft`, `foreach` (unrolled), `unique`, and `solve...before` accepted-and-dropped.
Unsupported nodes are rejected cleanly rather than silently ignored.

Two consequences for test design:
- The backend already supports every *operator* the generic-constraint examples
  need. A failure in this suite should therefore be attributable to the
  frontend/IR, not to dv-solve — which makes these tests diagnostically useful.
- There is no conditional/ternary primitive; `(a < b) ? b : a` (Example139) will
  lower through the clausal if/else path. Worth an explicit test because the
  docstring notes `expr_ite` is *not* reliably propagated by this solver.

**Conclusion:** the suite should be written now, spec-first, with the
not-yet-implemented cases marked `xfail(strict=True)` so they flip to green
automatically as `ast2ir` lands. Section 7 phases this.

---

## 3. Harness design

### 3.1 The path under test

```
PSS text
  → pssc.Parser().parses([("t.pss", src)]) ; .link()
  → pssc.AstToIrTranslator().translate(root)          # ir.Context
  → zuspec.ir.core.xf.PSSToScenarioPass(root=..., exports=[...]).lower(ctx)
  → zuspec.be.bc.lower.lower_module(module, entry_action=...)   # ZbcModel
  → zuspec.be.bc.interp.run_model(model, obj=Obj(...), seed=N,
                                  solve_backend=NativeBlobBackend())
  → RunResult.obj.as_dict()                            # {field_name: value}
```

Every stage of this already exists and is exercised today. The two existing
templates to copy from are
`packages/zuspec-be-sw/tests/unit/_pss_harness.py` (PSS-source → scenario module,
`lower_pss()` at line 51), `packages/zuspec-be-bc/tests/diff/adapter.py`
(scenario module → bc → run), and
`packages/zuspec-be-bc/tests/unit/lower/test_constraints.py:36-58` (the existing
`_problem()`/`_solve()` pair — the only place today where bc-lowered constraints
actually meet dv-solve).

**No test in this tree currently joins PSS source to the bc backend.** `pssc` has
no `bc` target (`src/pssc/targets/` has `py`, `sv`, `c`, `cpp`, `sw`, `progseq`),
`zuspec-be-bc` is never imported from `src/pssc` or `tests/`, and the bc suite
starts from hand-built `zuspec.ir.core` IR rather than PSS text. So phase 0 of
this work builds a genuinely new seam, which is a secondary benefit independent
of generic constraints — and a risk: the first failures may be seam bugs rather
than constraint bugs, which is exactly why phase 0 exercises an
already-supported *fixed* constraint first.

`NativeBlobBackend` (`packages/zuspec-be-bc/src/zuspec/be/bc/interp/extern.py:115`)
is the real dv-solve seam: it hands `problem.problem_bytes` to `dv_solve.ctx.SolveCtx`,
solves with the run seed, and writes back `{var_name: value}`. It is now exported
from `zuspec.be.bc.interp` (`__init__.py`) — **done**, see §8.6 — so the harness
imports it from the public surface rather than reaching into `...interp.extern`.
Its `dv_solve` import stays lazy (inside `randomize()`), so importing
`zuspec.be.bc.interp` still works on a machine without dv-solve.

### 3.2 Proposed shared harness

**Built, 2026-09-11: `tests/unit/integration/_bc_harness.py`.** The shipped API,
which differs from the sketch this section originally carried:

```python
def translate(src)                                    # → Layer-0 IR context
def solve_pss(src, *, action="pss_top::A", seed=0)    # → {field: value}
def solve_many(src, *, action="pss_top::A", seeds=range(32))
def expect_error(src, match, *, action="pss_top::A")
def solves_in(results, field, lo=None, hi=None)       # seed-naming assertion
```

Three things the spike forced, all of which would have bitten on first use:

- **An action needs a body to be visible.** `PSSToScenarioPass` recognizes an
  action by its exec body or its activity, so a rand-fields-and-constraints-only
  action — exactly the shape a constraint test wants — is invisible to the pass
  and fails with `root component 'pss_top' owns no actions`. The harness injects
  an empty `body` function, a semantic no-op that leaves the solve problem
  intact. (be-bc's own differential adapter hit this and solved it the same way:
  `packages/zuspec-be-bc/tests/diff/adapter.py:_ensure_lowerable`.)
- **`exports=` and `entry_action=` take the *simple* name** (`"A"`), not the
  qualified one, while `ctx.type_map` is keyed by the qualified name
  (`"pss_top::A"`). Passing the qualified name to `lower_module` silently yields
  no entry.
- **`run_model` needs an `Obj` supplied by the caller.** Without
  `obj=Obj(field_names=[...])` the run succeeds and `res.fields` is empty — a
  silent no-result rather than an error. Field names come from the action's rand
  fields.

`expect_error` deliberately spans parse, link, *and* translate: which stage
rejects a construct is an implementation detail, not a spec fact, and generic
constraints in fact fail at link rather than in `ast2ir`.

Guards to add when this runs anywhere but a provisioned checkout:
`pytest.importorskip("dv_solve")` and a skip when the shared library is absent.

### 3.3 Two constraints imposed by the existing test config

Both of these are easy to miss and both would bite on the first commit.

**`ZSP_SOLVER_BACKEND=python`.** `tests/conftest.py:15` sets this for the entire
pssc suite, with an inline comment (`tests/conftest.py:9-14`) explaining that the
native dv-solve backend is "currently incomplete for several constraint kinds".
That switch selects the **be-py** solver via
`packages/zuspec-be-py/.../solver/backend/registry.py:91`; it does **not** affect
the bc path, which reaches dv-solve directly through `NativeBlobBackend`. So this
suite is unaffected — but it means the suite is deliberately running against a
solver the rest of the tree currently opts out of. That is the point (it is what
was asked for) and it should be stated in the module docstring so nobody
"helpfully" reconciles the two later. It also means GC failures may be *solver*
gaps, and the suite should be read with that in mind.

**Markers.** `pytest.ini` uses `--strict-markers`, and `tests/unit/conftest.py:93-107`
auto-applies `integration` to anything under `tests/unit/integration/`. The
`timeout` marker used in GC-4.4 (§6) is **not** registered and pytest-timeout may
not be installed — so phase 4 must either add it to `pytest.ini` or use an
in-test watchdog. Registering it is cleaner.

Invocation is `direnv exec . pytest tests/unit/integration/test_generic_constraints_rt.py`
(per `AGENTS.md:15-28`).

### 3.4 Placement and naming

- `tests/unit/integration/test_generic_constraints_rt.py` — the main suite. This
  matches the existing family: `test_pss_static_constraints_rt.py`,
  `test_pss_logical_constraints_rt.py`, `test_pss_set_constraints_rt.py`,
  `test_pss_unique_constraints_rt.py`, `test_forall_struct_rt.py`.
- `tests/unit/errors/` for the negative/diagnostic cases (§5), matching the
  existing `tests/unit/errors` directory.
- Test function names: `test_<area>_<behavior>`, docstring citing the spec clause
  — e.g. `def test_unreferenced_generic_is_inert():  """§13.1.1 — ..."""`.

**A note on the existing convention.** The current `*_rt.py` integration tests use
`pssc.load_pss` + `zuspec.dataclasses.randomize`, which is the **py** backend, not
bc. This suite intentionally diverges to the bc/dv-solve path: "run in Python"
means **the bytecode backend running under Python**, not the native-Python
translation path (§8.3). The two are worth keeping deliberately separate rather
than blending; if the same PSS should behave identically on both, that is an
optional differential test and belongs in its own file.

---

## 4. Test matrix

IDs are stable handles for review discussion; they should appear in the test
docstrings.

### GC-1 — Activation semantics (the core rule, §13.1.1)

| ID | PSS under test | Assertion |
|---|---|---|
| GC-1.1 | `constraint c() { x == 7; }`, never referenced | Over a 64-seed sweep, `x` takes ≥ 2 distinct values and is not pinned to 7 |
| GC-1.2 | Same, referenced from an unnamed fixed constraint `constraint { c(); }` | `x == 7` for every seed |
| GC-1.3 | Fixed `constraint c { x == 7; }` (no parens) | `x == 7` for every seed — the paren-free form always holds |
| GC-1.4 | Indirect: fixed → `a()`, where `constraint a() { b(); }`, `constraint b() { x == 7; }` | `x == 7` — "directly or indirectly" |
| GC-1.5 | Example138 verbatim: `pkt_sz_c` fixed, `interesting_sz_c` = `small_pkt_c() \|\| jumbo_pkt_c()` | `pkt_sz in [1..100] ∪ [1501..65535]`; over a sweep, both disjuncts are observed |
| GC-1.6 | Two mutually-exclusive generics, neither referenced | Solver unconstrained; no unsat |
| GC-1.7 | Two mutually-exclusive generics, both referenced from one fixed constraint | Reported as unsat — *not* a silent wrong answer. See GC-11 for the provenance requirement |

GC-1.1 and GC-1.6 are the load-bearing negatives. Without them a frontend that
treats every named constraint as always-on passes the rest of the suite.

### GC-2 — Parameters (§13.1.2)

| ID | Case | Assertion |
|---|---|---|
| GC-2.1 | `constraint in_range(int lo, int hi) { x >= lo; x <= hi; }` referenced with literals | `lo <= x <= hi` |
| GC-2.2 | Same, referenced twice with different actuals in one type | Both instantiations hold independently (intersection) |
| GC-2.3 | Actual is a field reference, not a literal: `in_range(a, b)` with `a`,`b` rand | Relation holds jointly across all three vars |
| GC-2.4 | Actual is an expression: `in_range(a+1, a+8)` | Holds |
| GC-2.5 | `const` parameter (`const int idx`) bound to a literal | Accepted; used where a constant is required (e.g. array subscript) |
| GC-2.6 | Wrong arity at the reference site | Elaboration error, §5 |
| GC-2.7 | Zero-parameter form `c()` with an explicit empty list | Same behavior as GC-1.2 |

### GC-3 — Value-yielding generic constraints (§13.1.2 b, Example139)

| ID | Case | Assertion |
|---|---|---|
| GC-3.1 | Example139 verbatim: package-scope `constraint numeric max(numeric a, numeric b) (a<b)?b:a;` used as `j == max(k,l)` | `j == max(k,l)` for every seed; sweep must observe both `k<l` and `k>=l` branches |
| GC-3.2 | Value constraint nested in an arithmetic expression: `j == max(k,l) + 1` | Holds — "usable in any position where an expression of that type is legal" |
| GC-3.3 | Value constraint used as an operand of a comparison inside an implication | Holds |
| GC-3.4 | Value constraint composed: `max(max(a,b), c)` | Holds, equals 3-way max |
| GC-3.5 | Explicitly typed form `constraint int clamp(int x, int y) ...` | Holds |
| GC-3.6 | `numeric` parameter instantiated with a *float* actual | Documented gap: `skip` with a tracking note (solver capability, not a generic-constraints question) |
| GC-3.7 | One `numeric` generic constraint referenced with `bit[8]` and `bit[32]` actuals in the same type | Both hold, at their own widths — the two references reify to **distinct signature-specific specializations**; neither reference's width leaks into the other |
| GC-3.8 | Same, for a signed/unsigned pair | Both hold at their own signedness |

GC-3.1 is the one that exercises the clausal if/else encoding noted in §2; if
`expr_ite` propagation is weak, this is where it shows up, and the failure will be
a *wrong value*, not an error — hence the both-branches sweep requirement.

**GC-3.7/3.8 pin a design decision, not just a behavior** (§8.2). `numeric` is a
user-facing convenience; the compiler is expected to reify it to a concrete type
early, driven by the actual at each reference site. The consequence is that a
generic constraint — and, by the same argument, a generic *function* — is not one
IR entity but a family of **signature-specific specializations**, one per distinct
actual-type signature. These two tests are the cheapest way to make that
observable from the outside, and they will fail loudly if an implementation
instead reifies once at the first reference and reuses it.

### GC-4 — Recursion (§13.1.2 d, Example140)

| ID | Case | Assertion |
|---|---|---|
| GC-4.1 | Example140 verbatim (`gt_a_list_elem` over a 4-element list) | `val > l[i]` for at least one `i`; sweep observes more than one satisfying `i` |
| GC-4.2 | Same over a 1-element list (base case only) | `val > l[0]` |
| GC-4.3 | Recursion gated by a *random* expression | Elaboration error, §5 — the gate must be invariant |
| GC-4.4 | Unbounded/ungated recursion | Diagnosed with a depth-limit error, not a hang. **Must have a pytest timeout.** |

### GC-5 — Scope and `static` (§13.1.2)

| ID | Case | Assertion |
|---|---|---|
| GC-5.1 | Declared in a `struct`, referenced from that struct | Holds |
| GC-5.2 | Declared in an `action` | Holds |
| GC-5.3 | Declared in a `component`, referenced from an action of that component | Holds |
| GC-5.4 | Declared in a `package` with `static` | Holds; referencable from an unrelated type, qualified and unqualified |
| GC-5.5 | Declared in a `package` **without** `static` | **Implicitly static, no diagnostic** — behaves identically to GC-5.4 (decided, §8.1) |
| GC-5.6 | `static` generic constraint referencing a non-static member | Error |

### GC-6 — Inheritance and shadowing (§13.1.3, §13.1.2 c)

| ID | Case | Assertion |
|---|---|---|
| GC-6.1 | Base declares `constraint c() { x < 10; }`, derived shadows with `constraint c() { x > 100; }`; derived instance references `c()` | `x > 100`; base version does not also apply |
| GC-6.2 | Base fixed `constraint c { ... }` shadowed by derived **generic** `constraint c() { ... }` | Per §13.1.3 shadowing applies across the fixed/generic boundary; derived wins, and the derived one is inert until referenced |
| GC-6.3 | Derived shadows with a *different* parameter list | Error (§13.1.2 c) |
| GC-6.4 | Derived shadows a value constraint with a different return type | Error (§13.1.2 c) |
| GC-6.5 | Unnamed fixed constraints in base and derived | Both apply (added, not shadowed) — control case |

### GC-7 — `forall` inside a generic constraint (§13.1.9, Example150)

| ID | Case | Assertion |
|---|---|---|
| GC-7.1 | Example150 verbatim: `do B;` then `do B with { c1; };` where `c1()` contains `forall (it: S) { it.x != 0xff; }` | In the *second* traversal, `s1.x != 0xff` and `s2.x != 0xff`; in the first, `0xff` is reachable over a seed sweep |
| GC-7.2 | Generic constraint with `forall` referenced from a member fixed constraint | Application scope is the whole type subtree + activity (§13.1.9 b.1) |
| GC-7.3 | `forall` with an explicit `in ref_path` inside a generic constraint | Scope restricted to that subtree; siblings unaffected |

GC-7.1 is the sharpest test in the suite — it is the only one that distinguishes
per-traversal activation from type-level activation. `tests/unit/integration/test_forall.py`
already covers `forall` lowering at the IR level; GC-7 is the runtime complement.

### GC-8 — Lookahead (§13.4.11, Example185)

| ID | Case | Assertion |
|---|---|---|
| GC-8.1 | Example185 verbatim: `select { d1; d2; }` between `a` and `b` | Whichever branch is taken, the *whole* branch is satisfiable: if `d1`, `b.val < a.val && b.val <= 5`; if `d2`, `b.val > a.val && b.val <= 7` |
| GC-8.2 | Same, checking the spec's stated ranges | `a.val ∈ [1..15]` when `d1` is selected; `a.val ∈ [0..6]` when `d2` is selected — the solver must not paint itself into a corner by choosing `a.val` first |
| GC-8.3 | Generic constraint activated in one branch, with a later action in the activity it constrains | Constraint "holds for the remainder of the activity" |
| GC-8.4 | Sweep over seeds | Both branches of the `select` are observed (otherwise GC-8.2 is vacuous on one side) |

GC-8.2 is a genuine solver-integration test, not a frontend test: it fails if
solving is done incrementally per-action instead of over the scenario. Expect this
to be the last group to go green.

### GC-9 — Interaction with other constraint forms

| ID | Case | Assertion |
|---|---|---|
| GC-9.1 | Generic constraint referenced from inside `if`/`else` constraint | Applies only on the taken side |
| GC-9.2 | Generic constraint referenced from an implication consequent | Applies only when the antecedent holds |
| GC-9.3 | Generic constraint body containing `dist` | Distribution observed over a sweep |
| GC-9.4 | Generic constraint body containing `soft` | Soft relaxes against a conflicting hard constraint, does not violate it |
| GC-9.5 | Generic constraint body containing `unique` | Holds |
| GC-9.6 | Generic constraint body containing `foreach` over a rand array | Holds elementwise |
| GC-9.7 | Generic constraint referenced from an inline `with { ... }` on a traversal | Applies to that traversal only |
| GC-9.8 | `default` inside a generic constraint | **Error** (§13.3 g) — see §5 |

### GC-10 — Deprecated `dynamic` (§13.1.1 NOTE)

| ID | Case | Assertion |
|---|---|---|
| GC-10.1 | `dynamic constraint c { x == 7; }` unreferenced | Inert (identical to GC-1.1) |
| GC-10.2 | Same, activated in an activity | `x == 7` (identical to GC-1.2) |
| GC-10.3 | Parse of `dynamic constraint` | Deprecation warning emitted; `is_dynamic` set on the AST node |

### GC-11 — Provenance / debuggability (not a spec requirement)

The spec does **not** require unsat-query support, and asking which generic
constraint made a problem unsatisfiable is out of scope. But relating a lowered
constraint expression back to the generic constraint it came from is a critical
debug aid, and it is the kind of thing that silently rots unless tested. These
tests assert the *link exists*, not that any particular diagnostic is emitted.

| ID | Case | Assertion |
|---|---|---|
| GC-11.1 | Any referenced generic constraint | Each lowered `Constraint` carries provenance naming the originating generic constraint |
| GC-11.2 | One generic constraint referenced twice with different actuals | The two lowered constraint sets are distinguishable by reference site, not merged |
| GC-11.3 | A generic constraint reached indirectly (GC-1.4 shape) | Provenance names the chain, or at minimum the innermost declaration |
| GC-11.4 | GC-1.7 (conflicting generics) | The unsat report names both contributing constraints. **Quality goal** — assert the provenance data is present; keep the message-content assertion soft initially |

GC-11 is the one group that inspects IR rather than solved values. That is
deliberate: provenance is a property of the lowering, and asserting it through
solver output would be indirect and brittle.

---

## 5. Negative / diagnostic tests

These assert on the *message*, not just that something failed, and each names the
spec clause it enforces. They ended up in
`tests/unit/integration/test_generic_constraints.py` beside the positive tests
rather than in a separate `tests/unit/errors/` module as planned: the earliest of
them (arity, a foreign body reaching its own scope) were written alongside the
feature they constrain, they share the same harness, and splitting one construct's
tests across two files to honour a filename costs more than it explains.

| ID | Bad input | Required diagnostic |
|---|---|---|
| NEG-1 | Local variable declared in a generic constraint body | "generic constraints may not declare local variables" (§13.1.2 a) |
| NEG-2 | Arity mismatch at reference | names the constraint, expected vs actual count |
| NEG-3 | Type mismatch on an actual | names the parameter |
| NEG-4 | Shadowing with mismatched param/return types | §13.1.2 c, names both declarations |
| NEG-5 | `default` / `default disable` under a generic constraint | §13.3 g |
| NEG-6 | Reference to an undeclared constraint name | undeclared-name error, not a silent no-op |
| NEG-7 | Randomization-gated recursion | §13.1.2 d |
| NEG-8 | Value-yielding constraint used in a boolean position (or vice versa) | type error |
| NEG-9 | Assignment to a `const` parameter | const violation |

NEG-6 deserves emphasis: because generic constraints are inert until referenced,
the failure mode for a typo'd reference is *silently weaker stimulus*. That is the
worst possible failure mode for a verification tool, and it must be an error.

---

## 6. Determinism and statistical assertions

The suite makes three kinds of claim, and they need different treatment.

1. **Must-hold** (`x == 7`): assert on every seed in a fixed sweep. Cheap, exact.
2. **Must-be-free** (GC-1.1, GC-1.6): assert that the value set over N seeds has
   more than one element. This is a probabilistic test and needs a fixed seed list
   and an N chosen so the false-failure probability is negligible. For a `bit[8]`
   field and N = 64, the chance of all-identical under a correct implementation is
   vanishingly small; the suite should still use an explicitly enumerated seed
   list, not `range()` over a global, so a failure is reproducible.
3. **Must-observe-both-branches** (GC-1.5, GC-3.1, GC-8.4): same treatment, but
   the assertion is on branch coverage rather than value distinctness.

Rule: **no test may depend on a specific solved value that is not forced by the
constraints.** dv-solve's value choice for an unconstrained var is an
implementation detail; pinning it would make the suite brittle across solver
updates. (The bc differential suite already documents this concern — see the
`adapter.py` header on why bit-exact independent solving is not asserted.)

Every test that could loop (GC-4.4 especially) carries `@pytest.mark.timeout`.

---

## 7. Capability map and plan

### 7.1 What works today — measured, not assumed

Measured 2026-09-11 by driving one representative of each group through the bc
harness (`scratchpad/probe_matrix.py`, 27 cases). Status is what the *toolchain*
does, not what a test asserts.

| Group | Status | Evidence |
|---|---|---|
| GC-1.1/1.3/1.6 inertness | **works** | unreferenced generic constrains nothing; fixed constraint still holds |
| GC-1.2 reference applies body | **works** | |
| GC-1.4 indirect (`c → a() → b()`) | **works** | recursion through the expansion is real, not a special case |
| GC-1.5 reference inside `\|\|` | **works** (W1a) | the body folds to a conjunction and binds tighter than the `\|\|` around it |
| GC-1.7 conflicting generics | **works; provenance present, unused** | `CompileUnsatError` — right *class* of answer (unsat, not a silent wrong one). Each contributing statement now names its declaration (W6), but no consumer reads it, so the *message* still names nothing (GC-11.4) |
| GC-2.1/2.2 literal params, two refs | **works** | intersection of two instantiations holds |
| GC-2.3 actual is a rand field | **works** | |
| GC-2.4 actual is an expression | **blocked, not ours** | see W4.1 — `x < a + 1` fails *without any generic constraint* |
| GC-2.5 `const` parameter | **works** (accepted; not enforced — NEG-9) | |
| GC-2.7 zero-parameter form | **works** | |
| GC-3.7 value-yielding | **works** (W2) | the expression is substituted at each use site, so two references reify at their own widths |
| GC-3.1 value-yielding `max` | **substitutes; unsolvable** | the LRM's own body is a ternary, and be-bc lowers no ternary at all (W4.5) |
| GC-3.5 value-yielding, typed | **substitutes; unsolvable** | `x == twice(10)` becomes `x == (10 + 10)`, past dv-solve's arithmetic limit (W4.1) |
| NEG-8 value form as a statement | **works, good message** | "yields a value, not a constraint … use it in an expression" |
| GC-4.4 ungated recursion | **works** | reported by name, as *missing a gate* rather than as a bare cycle (W3) |
| GC-5.1 struct scope | **unknown** | harness cannot observe struct sub-fields — see W4 |
| GC-5.3 component scope | **works** (W1b) | body may use only its parameters; reaching for a component field is now reported |
| GC-5.4/5.5 package scope | **works** (W1b) | was: reference resolved, then `ast2ir` dropped it and the constraint silently vanished |
| GC-6.1/6.2 inheritance/shadowing | **resolves; unobservable** | expansion is correct in the IR, but an action inheriting from another reaches the solver with no fields at all — see W4.4 |
| GC-9.1/9.2 under if/else, implication | **works** (W1a) | each arm instantiates its own reference; the condition still selects between them |
| GC-9.5 `unique` in a generic body | **works** | |
| GC-9.6 `foreach` in a generic body | **broken twice** | be-bc lacks array flattening *and* pssparser emits an internal error — see W5 |
| GC-9.7 inline `with {}` | **blocked, not ours** | be-bc: "inline traversal constraints are a later phase" |
| GC-10 deprecated `dynamic` | **works** (inertness); warning untested | |
| NEG-1 local var in body | **error, structural** (W3) | a syntax error, and correctly so — PSS's constraint grammar admits no variable declaration, so §13.1.2 a holds for *every* constraint; a fixed one rejects it identically |
| NEG-6 typo'd reference | **works, good message** | `unknown identifier 'gg'; did you mean 'g'?` |
| NEG-5 `default` under a generic | **works** (W3) | §13.3 g, by name; was *silently dropped*, not merely mis-reported |
| NEG-9 random actual for a `const` param | **works** (W3) | the constant case still solves |
| NEG-4, GC-6.3/6.4 shadow mismatch | **works** (W3) | §13.1.2 c, printing both signatures |
| GC-4.3 random-gated recursion | **works** (W3) | §13.1.2 d; distinguished from ungated recursion and from legal-but-unsupported |
| GC-11.1/11.2/11.3 provenance | **works** (W6) | every instantiated statement, substituted value and folded operand carries `ir.Provenance`: the declaration chain, the declaring scope, and a per-reference site number |
| GC-11.4 unsat names both | **data present; no consumer** | the two statements name `lo` and `hi`; dv-solve's unsat report reads neither, and §8.4 does not require it to |

The shape of that table is the plan. Almost every "broken" row is one of two
defects, and almost every "blocked" row is not a generic-constraints problem at
all.

### 7.2 The two defects behind most of the failures

Both are fixed. They are kept here because they are what the rest of §7 was
written against, and because each mis-compiled silently rather than failing loudly
— which is the property to keep testing for.

**D1 — expansion was shallow.** ~~`_inline_generic_constraints` walks the
*top-level statement list* of each constraint function and rewrites statements that
are exactly a call, so a reference anywhere else survives as an `ir.ExprCall`.~~
**Fixed by W1a, 2026-09-11.** See §7.2.2.

**D2 — expansion was same-type-only.** ~~The pass builds its table from
`type_ir.functions`, so a generic constraint declared on a component, a package,
or a base type is invisible.~~ **Fixed by W1b, 2026-09-11.** See §7.2.1.

#### 7.2.1 What W1b changed

Four changes, all in `src/pssc/ast2ir.py`:

1. **Expansion became a whole-model pass** (`_inline_all_generic_constraints`,
   called from `translate()`) instead of running per type as each finished
   translating. The old placement could not see a scope that had not been
   translated yet, so `action A : Base { constraint c { g(); } }` silently left
   the reference in place whenever `Base` was declared later in the file —
   the order-independence PSS 18.2 requires, and which the three-phase
   elaboration already provides for everything else.
2. **A layered visibility table** (`_visible_generics`): the type itself, then its
   base types, then the enclosing component, then package scope by qualified
   name. First entry for a name wins, which is what makes a derived declaration
   shadow a base one (§13.1.2 c). `super` is resolved by the same rule the
   backends use — exact key, then last segment — because a `super` records the
   name as written while the type is registered qualified.
3. **Package-scope declarations get a registry** (`ctx.generic_constraints`,
   keyed by qualified name). A package is only a namespace prefix in this
   translator, so there was no type for one to live on; they are recorded in the
   CONST pass, before anything that could reference them is translated.
   Qualified-name-only, so a reference cannot resolve into a package the
   referencing scope never named. `ExprRefPathStaticRooted` — the node
   `p::lt(x, 20)` parses to — also gained a translation branch, deliberately
   narrow: it translates a reference to a *known generic constraint* and keeps
   returning `None` for everything else that shares the node.
4. **A diagnostic for the one case substitution cannot serve**: a component- or
   package-scope body that reads a field off its own `self`. Expanding it into
   another type would rebind that field to a same-named one on the referencing
   type, or to nothing. Base-type expansion is exempt — `self` is the same
   object. (The package half is moot in practice: pssparser already rejects a
   field reference in a `static` constraint body as an unknown identifier.)

Consequence worth noting: the reference in **GC-5.4/5.5 used to be dropped
outright**, so only the *other* constraints in the block were in force and the
model solved over a wider range than the source asks for. A constraint compiler
emitting silently weaker stimulus is the worst failure mode in this set, which is
why W1b was sequenced first.

#### 7.2.2 What W1a changed

Same file. The expansion pass stopped being a loop over a flat statement list and
became a recursive walk over statements *and* expressions:

1. **Nested statement lists are descended into** — if/else arms, a `foreach` body,
   anything a later statement kind adds. The walk is over dataclass fields rather
   than a branch per statement type, so a new kind is followed without another
   edit. A statement that is *exactly* a reference is still spliced, so its body
   may hold a `foreach` or a `unique`; only the nested case is restricted.
2. **A reference in an operand position is folded into one expression**
   (`_instantiate_generic_as_expr`): the body's statements, conjoined. That is what
   makes `g(10) || y > 200`, `!g(10)` and an implication consequent work. The fold
   has to bind tighter than the operator around it — `(x > 3 && x < 10) || y > 200`,
   not `x > 3 && (x < 10 || y > 200)` — and
   `test_a_disjunction_is_satisfiable_by_the_other_operand` is the test that can
   tell those apart, by forcing the other operand true and asserting `x` is free.
3. **A body with no value is rejected in an operand position.** `unique`, `foreach`
   and if/else are constraints but not conditions, so a body containing one cannot
   be folded. Reported, rather than folding the part that fits and dropping the
   rest.
4. **Instantiation is one function** (`_instantiate_generic`) shared by both
   positions, so the cycle, depth, foreign-scope and arity checks apply identically
   to a nested reference. Expansion inside a fold is recursive: a generic
   referenced in an operand may reference another.

Both nesting axes compose, since the statement walk and the expression walk call
each other.

One more silent path, found while probing W1a and left for W3: a generic
constraint whose body translates to *nothing* is not registered at all, because
`_translate_constraint_block` returns `None` for an empty body. So a body made
only of statements `ast2ir` does not yet translate — `default x == 3;`, which is
what NEG-5 is — leaves the declaration absent, and the reference to it reaches the
solver as an `ExprCall` and is blamed on the solver. The fix belongs with W3
(diagnose the unsupported statement) rather than here.

#### 7.2.3 What W2 changed

Before W2, `GenericConstraintDeclValue` was not a `ConstraintBlock` subclass — so
unlike the boolean form it was never mis-collected, it was simply **dropped**, with
no handler at any of the four scopes that can declare one. A reference then reached
be-bc as an unexpanded `ExprCall`. Four changes, in `ast2ir`:

1. **The declaration is translated** (`_translate_generic_value_constraint`) and
   registered at all four scopes — struct, action, component, package/global. It
   is stored as an `ir.Function` whose single statement wraps the expression, with
   `_is_generic_constraint` so nothing collects it, plus `_is_generic_value` to
   tell the forms apart.
2. **Substitution needs no new machinery.** Because the body is one `StmtExpr`,
   W1a's operand fold returns exactly that expression, and `j == max(k, l)` falls
   out of the path already there. The forms differ in only two places: a bare
   statement reference, and how a body's own nested references are expanded.
3. **A value form referenced as a bare constraint statement is reported** — a
   value is not a condition. Conversely a *value body's* nested reference is
   expanded as an expression rather than statement-wise, or that same check would
   reject the legal `constraint int plus2(int v) plus1(v) + 1;`.
4. **Arguments are expanded in the caller's context**, before substitution rather
   than after. Expanding after made `plus1(plus1(k))` look like `plus1` reaching
   itself and rejected it as a cycle; a reference in an argument is a sibling
   reference, not recursion. This is a W1a bug that only a value form exposes,
   since the boolean form is rarely composed this way.

**The declared return type is deliberately not recorded.** Substituting at the use
site means each reference is typed by its own arguments and its own context, which
*is* the per-signature specialization §8.2 asks for — GC-3.7 passes with no
specialization machinery at all. Recording one type on the declaration would invite
reifying once and reusing it, which is the thing GC-3.7/3.8 exist to catch.

What W2 does **not** get is a solve for most of GC-3, because the substituted
arithmetic lands outside what dv-solve compiles (W4.1) and the LRM's own ternary
body is not lowered at all (W4.5). Those cases assert on the IR instead, each
naming the hand-written control that fails identically without any generic
constraint — and they assert the *rendered expression*, not merely that no call
remains, since a dropped reference would also leave no call.

#### 7.2.4 What W3 changed

W3 is five unrelated diagnostics, so what they share is worth stating first: in
every case the construct was *accepted* by the frontend and the failure surfaced
one or two layers down, as a be-bc `LoweringError`, a bare unsat, or nothing at
all. A message naming the clause is the deliverable; where in the frontend it
fires is not, which is why the tests go through `expect_error` rather than
asserting a phase.

1. **§13.3 g — `default` under a generic constraint.** The worst of the five,
   because it was not a bad message but a *silently weaker model*. `default` is
   unimplemented everywhere and unimplemented constraint items are skipped, so
   `constraint g() { default x == 3; x < 100; }` dropped the default and compiled
   with `x < 100` as the only thing left. Now reported by name — and only inside a
   generic constraint, since §13.3 g says nothing about `default` elsewhere and
   turning a general gap into an error would reject models that compile today.
2. **§13.1.2 c — a shadowing declaration whose signature differs.** Checked where
   shadowing is already resolved, in `_visible_generics`, and the message prints
   *both* signatures: a mismatch the reader cannot see spelled out is one they
   have to reconstruct by hand. This is the one check that needs the declared
   return type, which W2 deliberately does not record for substitution — so the
   type is recorded for comparison only, and the test that covers it doubles as the
   statement that substitution still never consults it.
3. **§13.1.2 d — recursion, three ways.** Previously one message, "refers to
   itself", for three different situations. Recursion is *legal* when gated by a
   non-random expression, so the gate guarding the recursive reference is found
   (through if/else arms *and* implication consequents, which lower to a call
   rather than a `StmtIf`) and classified: no gate at all, a gate that reads a
   field and so cannot be resolved before solving, or a non-random gate — which is
   correct PSS this compiler cannot yet unroll. That third bucket is the reason the
   split is worth the code: it is Example140's shape, and telling its author their
   constraint is circular sends them to rewrite working code.
4. **A random actual for a `const` parameter.** §13.1.2 gives `const` no meaning
   beyond the grammar; taken here as the reading that makes it useful and that
   GC-2.5 already assumes — the actual must be a constant, which is what lets such
   a parameter size or index an array. A random actual makes that false exactly
   when it matters, after solving, with nothing downstream to notice.
5. **A generic constraint with an empty body is now registered.** This is the
   silent path §7.2.2 left open. A fixed constraint with an empty body is dropped
   harmlessly — nothing references it — but doing the same to a generic one hid the
   declaration from its own reference, which then reached the backend as an
   unexpanded call and got blamed on the solver. With item 1 in place the case that
   produced it is reported at source, and `constraint g() {}` is legal anyway.

**NEG-1 is closed as correct-by-construction, not implemented.** §13.1.2 a
("generic constraints may not declare local variables") is enforced by the
*grammar*: PSS's constraint body admits no variable declaration, so a local var is
a syntax error — in a fixed constraint identically, which is the control that
proves it is structural rather than something the generic path misses. Producing a
§13.1.2 a-specific message would mean widening the grammar to admit the illegal
construct just to reject it better.

**Two things W3 makes observable about how randomness is judged.** Items 3 and 4
both ask "is this expression random?", answered as "does it read a field off
`self`" — in a constraint, a field is what the solver assigns, while a parameter, a
literal and a folded constant are all fixed beforehand. It is conservative in one
direction: a *non-rand* attribute reads as random. That costs a false diagnostic on
a shape no test exercises yet, and the alternative — treating an unknown reference
as constant — would let exactly the cases these checks exist to catch through
silently.

#### 7.2.5 What W6 changed

Decision §8.4 made provenance a first-class requirement, and a generic constraint
is the construct that needs it most: it is the one whose lowered form is *not*
where the author wrote it. The body lands in some other constraint, with arguments
substituted, possibly several times, possibly reached only through another generic
constraint. Any diagnostic about the lowered form otherwise names a constraint the
author never wrote.

1. **`ir.Base` gained a `provenance` slot**, beside `loc` and `doc`, and
   `DomainNode`'s own duplicate declaration of it was removed. The pair is
   complementary, not redundant: `loc` says where in the source a node is,
   `provenance` says which pass produced it out of what, and an instantiated
   statement legitimately has both — a `loc` pointing at the declaration and a
   provenance pointing at the reference.
2. **Each instantiation is stamped** with `pass_name='generic_constraints'`, the
   reference chain (outermost first, ending at the declaration being
   instantiated), a description naming the declaring scope, and `site` — a
   per-reference number.
3. **`site` is what GC-11.2 needs.** Two references to one declaration are spliced
   into the same constraint and, after substitution, may be textually identical;
   the name cannot separate them and neither can the text. Nothing in the AST→IR
   translation carries source locations (`ast2ir` sets `loc` nowhere at all), so
   "distinguishable by reference site" cannot mean file and line here. A counter
   is the honest reading.
4. **Stamped where the node survives, not where it is created.** For the value
   form the provenance goes on the *expression*, because the statement wrapping it
   is discarded when the expression is substituted at the use site. For a
   reference folded into an operand, the conjunction is a new node and is stamped
   after the fold, because the statements it was built from are thrown away. Both
   would have been silent losses — nothing downstream requires the field.
5. **First writer wins, and the first writer is the innermost instantiation**, which
   is the more specific answer and already names the outer references in its
   chain. Stamping copies the node (`dataclasses.replace`) rather than assigning
   through: an unsubstituted part of a declaration's body is shared with the
   declaration and with every other reference to it, so writing in place would give
   two references one provenance and label the declaration as its own instantiation.

**The finding worth keeping: provenance must be inert data, not a node reference.**
The precise link is the declaration's `ir.Function`, and `ir.Provenance` already
has a `source_nodes` field for exactly that. Populating it broke two existing
tests — both generic dataclass walkers, which followed the new back-edge out of
the constraint into the declaration's *unexpanded* body and reported a leftover
reference that is not in the model. A recursive generic constraint would have made
that a cycle rather than a wrong answer, and the same applies to the IR serializer,
which walks fields generically too. So W6 records `source_names`, added to
`ir.Provenance` for the purpose, and the declaring scope goes in the description to
disambiguate a repeated name. An annotation that changes what walking the IR finds
is not an annotation.

What W6 does **not** get: no consumer reads the field yet. GC-11.4 is the case that
wants one — dv-solve's unsat report names no constraint — and the natural next step
is be-bc's `ProvenanceBuilder`, which already propagates `loc` and comments into the
ZBC `PROV` table and could carry this alongside.

### 7.3 Workstreams

Ordered by leverage. W1 turned six broken rows green.

| # | Work | Unblocks | Repo |
|---|---|---|---|
| ~~**W1a**~~ ✅ | Expand references **nested inside** constraint statements and boolean expressions — if/else arms, implication consequents, `foreach` bodies, nested scopes, and operands of `&&`/`\|\|`/`!` | GC-1.5, GC-9.1, GC-9.2 green | pssc |
| ~~**W1b**~~ ✅ | Resolve declarations in **other scopes** — component, package (`static`), and base types | GC-5.3/5.4/5.5 green; GC-6.1/6.2 resolve but are blocked by W4.4 | pssc |
| ~~**W2**~~ ✅ | **Value-yielding form**: lower a reference in value position by substituting the single expression at the use site | GC-3.7 and NEG-8 green; the rest of GC-3 substitutes correctly but waits on W4.1/W4.5 | pssc |
| ~~**W3**~~ ✅ | **Diagnostics at the frontend** instead of as be-bc lowering errors: `default` under a generic (§13.3 g), a random actual for a `const` parameter, random-gated recursion (§13.1.2 d), shadowing with a mismatched signature (§13.1.2 c). NEG-1 (§13.1.2 a) closed as a grammar rule — see §7.2.4 | NEG-1, NEG-4, NEG-5, NEG-7, NEG-9, GC-4.3, GC-4.4, GC-6.3/6.4 green | pssc |
| **W4** | **Not generic constraints** — five independent blockers found while probing (§7.4) | GC-2.4, GC-3.1–3.5, GC-5.1, GC-9.6, GC-9.7, GC-7.x | dv-solve, be-bc, harness |
| **W5** | **pssparser**: a parameter referenced from a `foreach` body inside a generic constraint produces `TaskResolveSymbolPathRef: Failed to get scope`, and that message never reaches the marker list (§7.5) | GC-9.6 | pssparser |
| ~~**W6**~~ ✅ | **Provenance** (decision §8.4): lowered constraints carry the generic they came from — the chain, the declaring scope and a per-reference site number, as inert names rather than node links (§7.2.5) | GC-11.1/11.2/11.3 green; GC-11.4 has the data but no consumer | pssc + ir-core |

### 7.4 Blockers that are not generic-constraint problems

Worth stating separately so they are not "fixed" in the wrong place:

1. **Arithmetic in a comparison is mostly not compiled — by dv-solve, not be-bc.**
   Re-measured while implementing W2, because W2's whole output is arithmetic and
   the attribution had to be right. be-bc lowers these fine; they fail in the
   solver, as `CompileIncompleteError: constraint(s) could not be compiled
   natively`, which is a *different* failure from the `LoweringError` in items 3
   and 5 and points at a different repo.

   The boundary: **one** arithmetic operation with a bare variable *or* a
   constant on the other side of an `==`. With no generic constraint anywhere,
   `j == k + 1`, `j == k * 2`, `j == k + l`, `k + 1 == 200` and `x % 4 == 0` all
   solve, while all of these fail:

   | shape | example |
   |---|---|
   | arithmetic under `<`/`>`/`!=` | `x < a + 1`, `x > a - 1`, `x != a + 1` |
   | two chained operations | `j == (k + 1) + 1`, `j == k * 2 + 1` |
   | arithmetic on both sides | `j + 1 == k + 2` |
   | arithmetic in an equality under an implication | `sel == 1 -> j == k + 1` |
   | arithmetic in an equality under `\|\|` | `j == k + 1 \|\| sel > 200` |
   | literal-only arithmetic (not folded) | `x == 10 + 10` |

   This blocks GC-2.4, GC-3.2/3.3/3.4 and GC-3.5.

   **Written up in full, at dv-solve's own API**, in
   `packages/dv-solve/docs/expr_coverage_gaps_2026-09-11.md` (probe beside it).
   The short version: dv-solve has two engines that both take a `SolveProblem`,
   and every shape the propagator engine rejects, its own bit-blasting engine
   solves correctly — so it is a coverage gap in one engine's shape matcher with
   no fallback, not a missing capability. One missing function arm
   (`_value_to_var` has no `EXPR_BINARY` case) accounts for most of the table,
   and `&&` works where `||` fails on the identical leaf because the two take
   different paths.
2. **The harness cannot observe struct sub-fields.** `rand S s;` yields one
   result key `s`, so a constraint on `s.x` cannot be checked. Blocks GC-5.1 and
   every struct-scoped case. File against `_bc_harness.py`.
3. **`foreach` flattening and inline `with {}` traversal constraints** are
   declared later-phase work by be-bc's own error messages. Blocks GC-9.6, GC-9.7
   and all of GC-7.
4. **Type inheritance does not reach the solve problem.** An action deriving from
   another solves with *no fields at all*: `action Base { rand bit[8] x;
   constraint g { x < 10; } } action A : Base {}` solves to `{}`, with no generic
   constraint anywhere in the source. This is why GC-6.x is verified on the IR
   instead of through a solve — a solve there would report this gap and blame
   generic constraints. Found 2026-09-11 while implementing W1b; file against the
   be-bc lowering path (or `PSSToScenarioPass`, wherever the flattening belongs).
5. **The ternary is not lowered at all.** `ir.ExprIfExp` reaches be-bc as
   `LoweringError: unsupported constraint expression ExprIfExp`, in a comparison
   operand (`j == ((k < l) ? l : k)`) and as a bare constraint statement alike,
   with no generic constraint in the source. This matters more than its size
   suggests: the ternary is the body of the LRM's own value-yielding example
   (Example139, `(a < b) ? b : a`), so GC-3.1 and GC-3.4 cannot be solved until
   it lands, however correct the substitution above it is. Found 2026-09-11 while
   implementing W2. File against be-bc — and note §2 anticipated this as the
   `expr_ite` risk, which turns out to be not weak propagation but no support.

   **And there is a second blocker stacked underneath it.** Measured at
   dv-solve's API on 2026-09-11: a ternary with a var-var *inequality* condition
   — `(k < l) ? l : k`, i.e. the LRM's shape exactly — is rejected by dv-solve's
   propagator engine too, while `(k == 1) ? l : k` and `(k < 10) ? l : k` both
   compile. So landing the be-bc lowering alone will not make GC-3.1/3.4 solve;
   the reification gap (R2 in
   `packages/dv-solve/docs/expr_coverage_gaps_2026-09-11.md`) has to land as
   well. Worth knowing before anyone times the be-bc work and expects the rows
   to turn green.

### 7.5 A silent diagnostic

`constraint f(int lim) { foreach (e : arr) { e < lim; } }` makes pssparser print

    Error: TaskResolveSymbolPathRef: Failed to get scope @ 2/5

and then **link successfully**. It is specific to a *parameter* referenced from a
`foreach` body: the same body with a literal (`e < 20`) is clean, and a plain
`foreach` outside a generic constraint is clean. The parameter-suppression path in
`TaskResolveRefs` (`m_generic_constraint_params`) is consulted in
`visitExprRefPathContext`, which the `foreach` body does not route through.

Two defects, not one. The resolution gap is the smaller of them: an error that is
*printed* rather than added to the marker list is invisible to every consumer —
`pssc.Parser.link()` raises on markers, so this one passes straight through.

### 7.6 Phasing

The suite is written in full up front; groups land as the implementation does.

| Phase | Content | Gate |
|---|---|---|
| 0 ✅ | `_bc_harness.py` + GC-1.3 | **Done 2026-09-11.** Proves the PSS→bc→dv-solve path end to end. |
| 1a ✅ | pssparser: resolve references; `ast2ir` instantiates a same-type reference | **Done 2026-09-11.** §2. |
| 1b ✅ | `ast2ir`: stop auto-collecting generic constraint bodies | **Done 2026-09-11.** §2. |
| 2 (W1b) ✅ | **W1b**, then GC-5.3/5.4/5.5, GC-6.1/6.2 | **Done 2026-09-11** (§7.2.1). GC-5.3/5.4/5.5 green; GC-6.x resolves in the IR but waits on W4.4. |
| 2 (W1a) ✅ | **W1a**, then GC-1.5, GC-9.1/9.2 | **Done 2026-09-11** (§7.2.2). Expansion now recurses through nested statements and folds a reference used as an operand. |
| 3 (W3) ✅ | **W3**, then NEG-1/4/5/7/9, GC-4.3, GC-6.3/6.4 | **Done 2026-09-11** (§7.2.4). Five diagnostics moved to the frontend; NEG-1 closed as a grammar rule. |
| 4 (W2) ✅ | **W2**, then GC-3 | **Done 2026-09-11** (§7.2.3). GC-3.7 and NEG-8 solve; GC-3.1/3.3/3.4/3.5 substitute correctly and are asserted on the IR, gated on W4.1 (dv-solve arithmetic) and W4.5 (no ternary lowering). |
| 5 (W6) ✅ | **W6**, then GC-11 | **Done 2026-09-11** (§7.2.5). `ir.Base.provenance`; every instantiation names its declaration chain and reference site. GC-11.4 stops at "the data is present" — no consumer reads it, which §8.4 anticipated. |
| **6** | GC-4 (recursion over a list), GC-9.3–9.6, GC-7, GC-8 | Gated on W4 — array flattening, inline `with`, and scenario-level lookahead are be-bc work, not frontend work. |

Phases 2–6 are committed as `@pytest.mark.xfail(strict=True, reason="…, §13.1.2")`
so they fail loudly the moment they start passing, and the xfail reason doubles as
the implementation to-do list. `strict=True` is important: a non-strict xfail on a
constraint test can mask a partially-correct implementation indefinitely.

**Every xfail reason must name the workstream** (W1a, W4.1, …) rather than say
"not implemented". The probe above exists because the previous round of reasons
did not distinguish "our bug" from "a be-bc gap", and two of the three
`SOLVE-FAIL` rows turned out not to be generic-constraint problems at all.

Rough sizing: ~61 test functions, ~9 of them negative. Landed so far: 71 —
`tests/unit/integration/test_generic_constraints.py` (58, all passing) and
`packages/pssparser/tests/python/parsing/test_generic_constraints_31.py`
(13 link-level).

Those tests need a pssparser carrying 1a. The suite probes for the capability
rather than checking a version, and marks the reference tests `xfail` against an
older parser — see `_NEEDS_1A` in the test module.

---


## 8. Decisions from review

Resolved in review; recorded here so the rationale survives.

**8.1 — Package-scope without `static` is implicitly static.** No diagnostic.
GC-5.5 becomes a positive test identical in expectation to GC-5.4.

**8.2 — `numeric` is a user convenience, reified early.** Float-typed `numeric`
(GC-3.6) is a documented gap for now — it is a dv-solve capability question, not a
generic-constraints question. The more consequential half of this decision: the
compiler should reify `numeric` to a concrete type early, based on usage at each
reference site. **This implies constraints and functions must support multiple
signature-specific versions** — a generic constraint is a family of
specializations, not one entity. GC-3.7/3.8 exist to make that observable. This
decision reaches well beyond constraints and should be reflected in whatever
handles generic *functions* too.

**8.3 — "Run in Python" means the bytecode backend running under Python**, not
the native-Python translation path. The bc + dv-solve path in §3.1 is the suite's
execution vehicle. The py-backend differential (previously open question 3) is
therefore **optional and secondary** — worth doing after phase 2 as a check that
activation decisions are backend-neutral, but it is not what "in Python" meant and
must not become the default way these tests run.

**8.4 — Unsat reporting is not required; provenance is. Applied** (W6, §7.2.5).
The spec does not
require unsat-query support for individual constraint functions, so GC-1.7 only
has to report unsat. But relating a lowered constraint expression back to its
originating generic constraint is *critical* as a debug aid. That is now its own
test group, GC-11, and it is a first-class requirement rather than a nicety.

**8.5 — Add `.pss` exemplars to `tests/patterns/`.** Example138 and Example185.
Low cost; gives the feature a home outside the test suite.

**8.6 — Export `NativeBlobBackend`. Applied.** It is now imported and listed in
`__all__` in
`packages/zuspec-be-bc/src/zuspec/be/bc/interp/__init__.py`, alongside the other
`SolveBackend` implementations. Rationale: it is the real dv-solve seam and this
suite is its first consumer; an external test suite depending on a private module
path is the worse of the two options. Its `dv_solve` import remains lazy (inside
`randomize()`), so the new export does not make `dv_solve` a hard import
dependency of `zuspec.be.bc.interp`. Verified by import; it was previously
referenced by no test in the tree, so phase 0 is also its first exercise.

No open questions remain.

---

## 9. The bigger pattern: construct-level tests

Worth naming, because this suite is the first clean instance of it and the shape
looks reusable.

These tests verify a **language construct's semantics**, observed through the
smallest execution vehicle that can observe them. They are not codegen tests. We
do not verify generic constraints by emitting SystemVerilog and inspecting it, or
by diffing generated C — the question "does an unreferenced generic constraint
stay inert?" is a property of the *language*, and any backend that gets it wrong
is wrong for the same frontend reason. Testing it through one backend answers it
for all of them; testing it through a generated artifact answers it for none,
because a codegen diff can't distinguish "constraint correctly inert" from
"constraint silently dropped".

The distinction against the suites already in the tree:

| Kind | Asks | Example here |
|---|---|---|
| **Construct** | Does this PSS construct mean what the LRM says? | this suite; `test_pss_*_constraints_rt.py` |
| **Lowering** | Does the frontend produce the right IR? | `test_constraints_ast_to_ir.py`, `test_forall.py` |
| **Codegen** | Does the backend emit the right artifact? | `tests/progseq/` goldens, `pss_to_sv` |
| **Integration** | Does the generated thing run? | `tests/sim/`, be-sw C e2e |

What makes the construct layer worth building on deliberately:

- **One vehicle, many constructs.** The `_bc_harness.py` seam in §3.3 is
  construct-agnostic. Once it exists, the marginal cost of a new construct suite
  is the PSS snippets and the assertions — not another pipeline.
- **Spec-traceable by design.** Every test cites an LRM clause; the matrix *is* a
  conformance checklist. That is exactly what a PSS implementation needs and
  what golden-file codegen tests can never give you.
- **Diagnostic locality.** A failure points at the frontend, because the backend
  path is shared and independently tested. Codegen tests have the opposite
  property — a failure could be anywhere.
- **Writable ahead of implementation.** §7's `xfail(strict=True)` phasing works
  because the tests describe the language, not an implementation's output shape.

Candidate next construct suites, in rough order of payoff: generic/parameterized
*functions* (directly implied by decision 8.2), template types, `default`
constraints (§13.3, only partly covered today), activity scheduling constructs
(`schedule`/`parallel`/`select` semantics as opposed to lowering), inheritance and
type extension, and flow-object binding rules.

Two things to settle before treating this as a house pattern: whether construct
suites get their own directory and marker (rather than living under
`tests/unit/integration/`, which overstates what they are), and whether the
LRM-clause citation should be machine-readable — the `source_ref` marker already
registered in `pytest.ini` suggests someone was heading that way.
