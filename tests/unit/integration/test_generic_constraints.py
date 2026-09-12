"""PSS 3.1 generic constraints (§13.1.2), observed through bc + dv-solve.

A generic constraint carries a parameter list -- ``constraint c(int lim) {...}`` --
and, unlike a fixed constraint, does **not** hold on its own. It is inert until
referenced, and each reference instantiates its body with the given arguments. The
parameter list, not the name, is what makes it generic: ``constraint c() {...}``
with an empty list is still generic (and is what the deprecated ``dynamic
constraint`` means).

See ``docs/design/generic-constraints-system-tests.md`` for the full matrix; this
file is its first slice (GC-1 activation, GC-10 deprecated form).

STATUS -- working for a declaration in any scope that can hold one (the type
itself, a base type, the enclosing component, a package) and for a reference
anywhere a boolean condition may appear. That is the whole of GC-1, GC-5, GC-9.1/9.2
and GC-10:

  * A *declaration* is inert. It lowers to an `ir.Function` marked
    `_is_generic_constraint`, carrying its body and its parameters, and nothing
    collects it into a solve problem. (Until phase 1b it was lowered as an
    ordinary *active* constraint against phantom `self.<param>` fields, so a
    model using the construct was silently mis-compiled rather than rejected.)
  * A *reference* resolves (phase 1a, in pssparser: generic constraints are now
    symbols in their enclosing scope) and is instantiated by `ast2ir`, which
    substitutes the arguments for the parameters. A reference that is a statement
    of its own has the body spliced in; one used as an operand (`g(10) || ...`,
    `!g(10)`, an implication consequent) has it folded into a conjunction (W1a).

  * A *value-yielding* declaration (``constraint int plus1(int v) v + 1;``) is a
    single expression, substituted at each use site, so ``j == plus1(k)`` holds
    and each reference is typed by its own arguments (W2). Referenced on its own
    as a constraint statement it is reported: a value is not a condition.

Still open, and each for a reason BELOW this construct rather than in it:

* **GC-6.x** — an action inheriting from another reaches the solver with no
  fields at all, with no generic constraint anywhere in the source. Asserted on
  the IR, because a solve would report that gap while looking like a
  generic-constraint failure.
* **The LRM's own `max`** — its body is a ternary, and be-bc lowers no
  `ExprIfExp` at all. Also IR-only.

The arithmetic limit that used to keep most of GC-3 off the solver is **gone**
(dv-solve, 2026-09-12): `j == (k + 1) + 1` and `sel == 1 -> j == k + 1` both
solve now, so the cases that were IR-only for that reason assert both the
substituted shape and the solved values. Where a test still asserts only on the
IR, the docstring says which layer is missing and names the hand-written control
that fails identically without any generic constraint.
"""
import pytest

from zuspec.ir import core as ir

from ._bc_harness import expect_error, solve_many, solves_in, translate


def _parser_resolves_generic_constraint_refs() -> bool:
    """Can the *installed* pssparser resolve a reference to a generic constraint?

    Phase 1a is a pssparser change (generic constraints become symbols in their
    enclosing scope); a parser predating it rejects every reference with "unknown
    identifier". Probed rather than version-checked, because the capability is
    what these tests need and a version is only a proxy for it.
    """
    try:
        translate("""
            component pss_top {
                action A { rand bit[8] x;
                    constraint g(int lim) { x < lim; }
                    constraint c { g(20); } }
            }
        """)
    except Exception:
        return False
    return True


#: Applies only to a parser without phase 1a, so that on a current parser these
#: tests must pass outright rather than being allowed to xpass.
_NEEDS_1A = pytest.mark.xfail(
    not _parser_resolves_generic_constraint_refs(),
    strict=True,
    reason=("the installed pssparser predates phase 1a, so a reference to a "
            "generic constraint fails to link with \"unknown identifier\""))


# --- GC-1: activation ------------------------------------------------------

def test_an_unreferenced_generic_constraint_is_inert():
    """§13.1.2(a): a generic constraint that is never referenced constrains nothing.

    The load-bearing test of the whole group. `lim_lt` would force x < 12 if it
    held; nothing references it, so the only constraint in force is x > 200 and
    the solver must be free to pick values above 12.

    Note what a codegen test could not do here: "the generic constraint was
    correctly ignored" and "the generic constraint was silently dropped on the
    floor" produce identical SystemVerilog. Solving tells them apart.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint lim_lt(int lim) { x < lim; }
                constraint c { x > 200; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=201, hi=255)


def test_a_fixed_constraint_alongside_a_generic_one_still_holds():
    """The control. Without it, the test above could pass because *both*
    constraints were dropped -- which would be a different bug with the same
    symptom.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint unused(int lim) { x == lim; }
                constraint c { x > 100; x < 110; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=101, hi=109)


@_NEEDS_1A
def test_referencing_a_generic_constraint_applies_its_body():
    """§13.1.2(b): a reference instantiates the body with the given arguments."""
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint lim_lt(int lim) { x < lim; }
                constraint c { lim_lt(20); x > 10; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=11, hi=19)


@_NEEDS_1A
def test_two_references_with_different_arguments_both_apply():
    """Each reference is its own instantiation, so both bodies must hold."""
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint lim_lt(int lim) { x < lim; }
                constraint c { lim_lt(20); lim_lt(15); x > 10; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=11, hi=14)


@_NEEDS_1A
def test_a_zero_parameter_constraint_is_generic_not_fixed():
    """§13.1.2: the parameter *list*, not its contents, is what makes a constraint
    generic. `g()` is inert until referenced, exactly like `g(int n)`.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint g() { x < 20; }
                constraint c { g(); x > 10; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=11, hi=19)


@_NEEDS_1A
def test_a_generic_constraint_may_reference_another_one():
    """Instantiation is recursive: `outer` references `inner`, passing its own
    parameter through. Both bodies must end up in force at the reference site.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint inner(int lim) { x < lim; }
                constraint outer(int lim) { inner(lim); x > 10; }
                constraint c { outer(20); }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=11, hi=19)


@_NEEDS_1A
def test_a_self_referential_generic_constraint_is_reported():
    """A cycle cannot be instantiated -- each expansion would produce another
    reference -- so it must be diagnosed rather than expanded until something
    gives out.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint loop(int lim) { loop(lim); }
                constraint c { loop(20); }
            }
        }
    """, r"generic constraint 'loop' refers to itself")


# --- GC-1.5 / GC-9: a reference somewhere other than a statement of its own -

def _some(results, field, pred) -> bool:
    """Did *pred* hold for *field* under at least one seed?

    Used where the point is that a value is *permitted*: a constraint wrongly left
    in force shows up as a value the solver never produces, which no per-seed
    bound can catch.
    """
    return any(pred(r[field]) for r in results)


@_NEEDS_1A
def test_a_reference_is_an_operand_of_a_disjunction():
    """GC-1.5. A generic constraint body is a boolean condition, so a reference to
    one may be an operand of `||`. Here the other operand is made false, which
    leaves the body itself in force.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] y;
                constraint g(int lim) { x < lim; }
                constraint c { g(10) || y > 200; y < 100; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", hi=9)


@_NEEDS_1A
def test_a_disjunction_is_satisfiable_by_the_other_operand():
    """The companion to the test above, and the one that can fail.

    Instantiating a reference into an operand means folding a body of several
    constraints into one expression, and the fold has to bind tighter than the
    `||` around it: `(x > 3 && x < 10) || y > 200`, not `x > 3 && (x < 10 || y >
    200)`. With `y > 200` the disjunction is already satisfied, so `x` must be
    free -- under a loose fold `x > 3` would still hold for every seed.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] y;
                constraint g(int lo, int hi) { x > lo; x < hi; }
                constraint c { g(3, 10) || y > 200; y > 200; }
            }
        }
    """, seeds=range(32))
    assert _some(results, "x", lambda v: v <= 3 or v >= 10), \
        "x was constrained by g even though the other operand satisfied the ||"


@_NEEDS_1A
def test_a_reference_in_an_if_arm_applies_only_in_that_arm():
    """GC-9.1. Each arm instantiates its own reference, and the condition still
    selects between them: with `sel == 1` the then-arm's `g(10)` bounds `x`.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] sel;
                constraint g(int lim) { x < lim; }
                constraint c { sel == 1; if (sel == 1) { g(10); } else { g(200); } }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", hi=9)


@_NEEDS_1A
def test_the_else_arms_reference_is_the_one_that_applies():
    """The same model with `sel == 0`, which is what tells a conditional
    instantiation from an unconditional one: the else-arm's `g(200)` permits
    values the then-arm's `g(10)` forbids.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] sel;
                constraint g(int lim) { x < lim; }
                constraint c { sel == 0; if (sel == 1) { g(10); } else { g(200); } }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", hi=199)
    assert _some(results, "x", lambda v: v >= 10), \
        "the then-arm's g(10) was applied even though sel == 0"


@_NEEDS_1A
def test_a_reference_is_an_implication_consequent():
    """GC-9.2. `sel == 1 -> g(10)` instantiates the body as the consequent."""
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] sel;
                constraint g(int lim) { x < lim; }
                constraint c { sel == 1; sel == 1 -> g(10); }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", hi=9)


@_NEEDS_1A
def test_a_reference_under_a_negation_inverts_its_body():
    """`!g(10)` is the complement of the body, so `x < 10` becomes `x >= 10`."""
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint g(int lim) { x < lim; }
                constraint c { !g(10); }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=10)


@_NEEDS_1A
def test_a_nested_reference_is_expanded_inside_an_operand():
    """Expansion in an operand is recursive too: `g` is folded into the `||`, and
    the `lo(lim)` inside `g`'s own body is instantiated as part of that fold.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] y;
                constraint lo(int lim) { x < lim; }
                constraint g(int lim) { lo(lim); x > 2; }
                constraint c { g(10) || y > 200; y < 100; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=3, hi=9)


@_NEEDS_1A
def test_a_body_with_no_value_cannot_be_an_operand():
    """`unique` is a constraint but not a condition, so a body containing one has
    nothing to fold into an operand. That has to be reported: silently dropping
    the `unique` would leave a model that compiles and under-constrains.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] y;
                rand bit[8] z;
                constraint g() { unique {y, z}; }
                constraint c { g() || x > 200; }
            }
        }
    """, r"generic constraint 'g' is referenced inside an expression")


# --- GC-3: the value-yielding form (§13.1.2 b) -----------------------------
#
# `constraint <type> name(params) expr;` is one expression, not a constraint set,
# and is "usable anywhere an expression of that type is legal". So it never holds
# on its own -- it contributes a value to whatever constrains the reference.
#
# These assert the substituted SHAPE on the IR and, where the shape can be
# solved, the values too. The shape check is not redundant: it is what says the
# substitution composed rather than looped, which a solve alone cannot
# distinguish from a reference that was dropped. Only the ternary case is still
# IR-only, and it names the layer that is missing.


@_NEEDS_1A
def test_a_value_yielding_reference_contributes_its_expression():
    """The core case: `j == plus1(k)` means `j == k + 1` for every seed."""
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] j;
                rand bit[8] k;
                constraint int plus1(int v) v + 1;
                constraint c { j == plus1(k); k < 100; }
            }
        }
    """, seeds=range(32))
    for r in results:
        assert r["j"] == r["k"] + 1, r


@_NEEDS_1A
def test_a_value_yielding_constraint_is_inert_unreferenced():
    """Inertness is not special-cased for this form -- it has nothing to apply.

    Worth its own test because the declaration *is* translated now, and a
    declaration that leaked into the solve problem would constrain `j` to `k + 1`
    with no reference asking it to.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] j;
                rand bit[8] k;
                constraint int plus1(int v) v + 1;
                constraint c { k < 100; }
            }
        }
    """, seeds=range(32))
    assert _some(results, "j", lambda v: v > 101), \
        "j was confined to plus1's range without any reference to it"


@_NEEDS_1A
def test_two_references_are_substituted_independently():
    """Each reference carries its own arguments, so the two do not share a value."""
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] j;
                rand bit[8] m;
                rand bit[8] k;
                constraint int plus1(int v) v + 1;
                constraint c { j == plus1(k); m == plus1(j); k < 50; }
            }
        }
    """, seeds=range(32))
    for r in results:
        assert r["j"] == r["k"] + 1 and r["m"] == r["j"] + 1, r


@_NEEDS_1A
def test_a_package_scope_value_constraint_applies_its_expression():
    """§13.1.2: a package-scope declaration is static, and this form is no different."""
    results = solve_many("""
        package p { static constraint int dbl(int v) v * 2; }
        component pss_top {
            action A {
                rand bit[8] j;
                rand bit[8] k;
                constraint c { j == p::dbl(k); k < 60; }
            }
        }
    """, seeds=range(32))
    for r in results:
        assert r["j"] == r["k"] * 2, r


@_NEEDS_1A
def test_one_numeric_declaration_serves_two_widths():
    """GC-3.7: two references at different widths, neither leaking into the other.

    This is the observable consequence of the §8.2 decision that `numeric` is
    reified per reference site rather than once for the declaration. Substituting
    the expression at the use site gives it for free -- there is no shared entity
    left to carry one reference's width into the other -- and this test is what
    would fail if that ever became a single reified specialization.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] a;
                rand bit[8] b;
                rand bit[16] c;
                rand bit[16] d;
                constraint numeric inc(numeric v) v + 1;
                constraint e { b == inc(a); a < 100; d == inc(c); c < 1000; }
            }
        }
    """, seeds=range(32))
    for r in results:
        assert r["b"] == r["a"] + 1 and r["d"] == r["c"] + 1, r
    assert _some(results, "c", lambda v: v > 255), \
        "the bit[16] reference was confined to 8 bits"


@_NEEDS_1A
def test_a_value_yielding_constraint_cannot_stand_alone():
    """A value is not a condition, so a bare reference to one is meaningless.

    The boolean form spliced as a statement and the value form substituted as an
    expression share one instantiation path, so this is the seam where the two
    forms must part company -- without the check the solver would be handed a
    number where it expects a constraint.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] k;
                constraint int plus1(int v) v + 1;
                constraint c { plus1(k); }
            }
        }
    """, r"generic constraint 'plus1' yields a value, not a constraint")


@_NEEDS_1A
def test_a_value_reference_nested_in_an_argument_is_not_recursion():
    """GC-3.4: `plus1(plus1(k))` composes; it is not a cycle.

    A reference inside an *argument* is evaluated at the calling site, so it is a
    sibling reference, not the body reaching itself. Expanding arguments after
    substitution instead of before made this report "refers to itself", rejecting
    a legal composition -- which is why arguments are expanded in the caller's
    context.

    Asserted BOTH ways. The IR check states the substitution shape -- that it
    composed rather than looped -- and the solve states that the shape means what
    it says. This used to be IR-only because dv-solve compiled at most one
    arithmetic operation in an equality; that limit closed 2026-09-12, and an
    IR-only assertion would now be a weaker claim than the tree can support.
    """
    src = ("component pss_top { action A { rand bit[8] j; rand bit[8] k; "
           "constraint int plus1(int v) v + 1; "
           "constraint c { j == plus1(plus1(k)); k < 50; } } }")
    ctx = translate(src)
    assert not ctx.errors, ctx.errors
    body = _constraint_body(ctx, "pss_top::A", "c")
    assert not _contains_call(body), body
    assert _render(body[0]) == "(self.j == ((self.k + 1) + 1))", _render(body[0])

    for r in solve_many(src, seeds=range(16)):
        assert r["j"] == r["k"] + 2 and r["k"] < 50, r


@_NEEDS_1A
def test_a_value_body_may_reference_another_value_constraint():
    """One value-yielding declaration built from another.

    The nested reference is in expression position inside the body, which is why
    a value body is expanded as an expression rather than as a statement list --
    expanding it statement-wise would route the inner reference through the
    bare-statement path and reject it as "yields a value".

    Asserted both ways, as in the test above, and for the same reason: the
    substituted `(k + 1) + 1` solves now.
    """
    src = ("component pss_top { action A { rand bit[8] j; rand bit[8] k; "
           "constraint int plus1(int v) v + 1; "
           "constraint int plus2(int v) plus1(v) + 1; "
           "constraint c { j == plus2(k); k < 50; } } }")
    ctx = translate(src)
    assert not ctx.errors, ctx.errors
    body = _constraint_body(ctx, "pss_top::A", "c")
    assert not _contains_call(body), body
    assert _render(body[0]) == "(self.j == ((self.k + 1) + 1))", _render(body[0])

    for r in solve_many(src, seeds=range(16)):
        assert r["j"] == r["k"] + 2 and r["k"] < 50, r


@_NEEDS_1A
def test_the_lrm_max_example_substitutes_its_ternary():
    """GC-3.1, Example139: `static constraint numeric max(numeric a, numeric b)
    (a < b) ? b : a;` used as `j == max(k, l)`.

    IR-level because be-bc does not lower a ternary at all -- `j == ((k < l) ? l
    : k)` written by hand, with no generic constraint, fails the same way. So
    this asserts the strongest claim the frontend can make: the reference is
    gone, and what replaced it is the declaration's expression with `a` and `b`
    bound to the two actuals.
    """
    src = ("package p { static constraint numeric max(numeric a, numeric b) "
           "(a < b) ? b : a; } "
           "component pss_top { action A { rand bit[8] j; rand bit[8] k; "
           "rand bit[8] l; constraint c { j == p::max(k, l); } } }")
    ctx = translate(src)
    assert not ctx.errors, ctx.errors
    body = _constraint_body(ctx, "pss_top::A", "c")
    assert not _contains_call(body), body
    assert _render(body[0]) == \
        "(self.j == ((self.k < self.l) ? self.l : self.k))", _render(body[0])


@_NEEDS_1A
def test_a_value_reference_inside_an_implication_is_substituted():
    """GC-3.3: the reference is an operand of a comparison under an implication.

    The IR check is that the substitution reached inside the implication's
    consequent at all. The solve is what says the consequent then holds -- and it
    pins `sel` so the antecedent is TRUE, since an implication with a false
    antecedent is satisfied by anything and would assert nothing.

    Was IR-only: dv-solve did not compile arithmetic inside an equality under an
    implication. It does as of 2026-09-12.
    """
    src = ("component pss_top { action A { rand bit[8] j; rand bit[8] k; "
           "rand bit[8] sel; constraint int plus1(int v) v + 1; "
           "constraint c { sel == 1 -> j == plus1(k); } } }")
    ctx = translate(src)
    assert not ctx.errors, ctx.errors
    body = _constraint_body(ctx, "pss_top::A", "c")
    assert "(self.j == (self.k + 1))" in _render(body[0]), _render(body[0])

    forced = src.replace("constraint c {", "constraint c { sel == 1;")
    for r in solve_many(forced, seeds=range(16)):
        assert r["sel"] == 1 and r["j"] == (r["k"] + 1) % 256, r


# --- GC-10: the deprecated `dynamic constraint` spelling -------------------

def test_a_dynamic_constraint_declaration_is_accepted():
    """`dynamic constraint d {...}` is the deprecated spelling of a zero-parameter
    generic constraint, so like any generic constraint it is inert unreferenced.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                dynamic constraint d { x < 12; }
                constraint c { x > 200; }
            }
        }
    """, seeds=range(32))
    solves_in(results, "x", lo=201, hi=255)


# --- the lowered shape, and the gap that is left ---------------------------

def test_a_generic_constraint_is_lowered_as_an_inert_template():
    """The IR-level companion to the inertness tests above.

    Inertness alone is also what "dropped on the floor" looks like, so this pins
    that the declaration survives lowering *as a template*:

      1. it is marked `_is_generic_constraint`, not `_is_constraint`, so no pass
         may collect it into a solve problem;
      2. its parameter list survives, since a reference has to bind arguments to
         it;
      3. `lim` in the body is a reference to that parameter -- not `self.lim`, a
         field the action does not have. This is the one that used to make the
         problem unsat rather than merely wrong.
    """
    ctx = translate("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint lim_lt(int lim) { x < lim; }
                constraint c { x > 200; }
            }
        }
    """)
    dt = ctx.type_map["pss_top::A"]
    fn = next(f for f in dt.functions if f.name == "lim_lt")

    meta = fn.metadata or {}
    assert meta.get("_is_generic_constraint") is True
    assert not meta.get("_is_constraint"), (
        "a generic constraint is in force again -- it must stay inert (§13.1.2)")

    assert [a.arg for a in fn.args.args] == ["lim"]

    stmt = fn.body[0]
    assert isinstance(stmt, ir.StmtExpr)
    assert isinstance(stmt.expr.rhs, ir.ExprRefLocal), (
        f"`lim` must resolve to the parameter, not to a field: {stmt.expr.rhs}")
    assert stmt.expr.rhs.name == "lim"

    assert "lim" not in {f.name for f in dt.fields}, (
        "sanity: `lim` is a parameter, so the action must not declare it a field")


@_NEEDS_1A
def test_a_reference_with_the_wrong_argument_count_is_rejected():
    """Arity is checked at the reference, against the declaration's parameters.

    A generic constraint is not an `ISymbolFunctionScope` and has no
    `IFunctionPrototype`, so it does not go through the linker's ordinary
    call-arity path -- it needs its own, or every reference would be rejected as
    "'lim_lt' is not a function".
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint lim_lt(int lim) { x < lim; }
                constraint c { lim_lt(20, 30); }
            }
        }
    """, r"too many arguments to constraint 'lim_lt': expected 1, got 2")

    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint lim_lt(int lim) { x < lim; }
                constraint c { lim_lt(); }
            }
        }
    """, r"too few arguments to constraint 'lim_lt': expected 1, got 0")


# --- W3: diagnosed at the frontend, not by the backend ---------------------
#
# Each of these used to reach be-bc or dv-solve and be reported as a lowering
# error, a bare unsat, or -- worse -- not reported at all. A diagnostic naming the
# spec clause is the deliverable; where in the frontend it fires is not, which is
# why they go through `expect_error` (parse, link and translate alike).


@_NEEDS_1A
def test_a_default_constraint_under_a_generic_is_rejected():
    """§13.3 g: `default` may not be used under a generic constraint.

    The failure this replaces was not a bad message but a *silently weaker model*:
    `default` is unimplemented everywhere, and unimplemented constraint items are
    skipped, so a body of `default x == 3; x < 100;` dropped the default and
    compiled with `x < 100` as the only thing left.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint g() { default x == 3; x < 100; }
                constraint c { g(); }
            }
        }
    """, r"'default' may not be used inside generic constraint 'g'.*13\.3 g")


@_NEEDS_1A
def test_a_default_disable_under_a_generic_is_rejected():
    """§13.3 g covers both spellings, so both are named as written."""
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint g() { default disable x; }
                constraint c { g(); }
            }
        }
    """, r"'default disable' may not be used inside generic constraint 'g'")


def test_a_default_outside_a_generic_constraint_is_not_rejected():
    """The control: §13.3 g is about generic constraints specifically.

    `default` is still unimplemented in a fixed constraint, and this test pins
    that W3 did not turn that gap into an error -- which would reject models that
    compile today, for a rule the spec does not state.
    """
    ctx = translate("""
        component pss_top {
            action A { rand bit[8] x; constraint c { default x == 3; x < 100; } }
        }
    """)
    assert not ctx.errors, ctx.errors


@_NEEDS_1A
def test_shadowing_with_a_different_parameter_list_is_rejected():
    """§13.1.2 c: a shadowing declaration's parameter types shall match."""
    text = expect_error("""
        component pss_top {
            action Base { rand bit[8] x; constraint g(int lim) { x < lim; } }
            action A : Base {
                constraint g(int lo, int hi) { x > lo; x < hi; }
                constraint c { g(3, 10); }
            }
        }
    """, r"shadows the one in 'Base'.*signatures differ.*13\.1\.2 c")
    assert "'A'" in text and "'Base'" in text, \
        f"the message must name both declarations: {text}"


@_NEEDS_1A
def test_shadowing_a_value_constraint_with_another_return_type_is_rejected():
    """§13.1.2 c covers the return type too, which only the value form has.

    This is the one case that needs the declared return type, which W2 otherwise
    goes out of its way not to record -- so it is also what pins that the type is
    still available for checking even though substitution never consults it.
    """
    expect_error("""
        component pss_top {
            action Base { rand bit[8] x; constraint int v(int a) a + 1; }
            action A : Base {
                constraint bool v(int a) a > 1;
                constraint c { x == 1; }
            }
        }
    """, r"generic constraint 'v'.*signatures differ.*13\.1\.2 c")


@_NEEDS_1A
def test_shadowing_with_a_matching_signature_is_accepted():
    """The control for the two above: matching signatures must stay silent.

    Without it the check could be "report every shadow", which would reject
    GC-6.1 -- the legal override that `test_a_derived_generic_constraint_shadows_
    the_base_declaration` requires to work.
    """
    ctx = translate("""
        component pss_top {
            action Base { rand bit[8] x; constraint g(int lim) { x < lim; } }
            action A : Base {
                constraint g(int lim) { x > lim; }
                constraint c { g(3); }
            }
        }
    """)
    assert not ctx.errors, ctx.errors


@_NEEDS_1A
def test_recursion_gated_by_a_random_expression_is_rejected():
    """§13.1.2 d: the gate must not involve randomization.

    Reported as a plain cycle before W3, which is the wrong rule: the defect is
    not that the constraint reaches itself -- §13.1.2 d expressly permits that --
    but that nothing can decide when to stop.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] n;
                constraint r(int d) { if (n > 0) { r(d); } }
                constraint c { r(1); }
            }
        }
    """, r"recurses.*gate that involves randomization.*13\.1\.2 d")


@_NEEDS_1A
def test_recursion_gated_by_an_implication_is_also_seen():
    """The gate need not be an `if`: an implication consequent is gated too.

    Worth its own test because an implication lowers to a *call*
    (`implies(cond, consequent)`), not to an `ir.StmtIf`, so a gate search that
    only knew about if/else would find no gate here and report the wrong one of
    the three §13.1.2 d messages.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] n;
                constraint r(int d) { n > 0 -> r(d); }
                constraint c { r(1); }
            }
        }
    """, r"recurses.*gate that involves randomization")


@_NEEDS_1A
def test_ungated_recursion_says_there_is_no_gate():
    """GC-4.4: recursion with no gate at all, distinguished from a bad gate.

    Both are errors, but they are different mistakes: this one is missing a gate,
    the test above has one that cannot be resolved before solving. A single
    message for both sends the reader looking for the wrong thing.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint r(int d) { x < d; r(d); }
                constraint c { r(10); }
            }
        }
    """, r"refers to itself with no gate.*13\.1\.2 d")


@_NEEDS_1A
def test_legal_recursion_is_reported_as_unsupported_not_as_a_cycle():
    """The third bucket: a non-random gate, which §13.1.2 d *permits*.

    `if (d > 0)` reads only a parameter, so this is correct PSS -- Example140's
    shape -- that this compiler cannot yet unroll. It must not be called circular:
    of the three ways to reach the cycle check this is the only one where the
    input is right, and telling that author their code refers to itself sends them
    to rewrite working code.
    """
    text = expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint r(int d) { if (d > 0) { x < d; r(d); } }
                constraint c { r(10); }
            }
        }
    """, r"non-random gate, which PSS 3\.1 §13\.1\.2 d permits but this compiler "
        r"does not yet unroll")
    assert "refers to itself" not in text, \
        f"legal recursion must not be described as a cycle: {text}"


@_NEEDS_1A
def test_a_random_actual_for_a_const_parameter_is_rejected():
    """A `const` parameter's argument must be a constant.

    §13.1.2 gives `const` no semantics beyond the grammar, so this is the reading
    that makes it mean something: `const` is what lets a parameter size or index
    an array, and a random actual makes that false exactly when it matters --
    after solving, with nothing downstream to notice.
    """
    expect_error("""
        component pss_top {
            action A {
                rand bit[8] x;
                rand bit[8] a;
                constraint lt(const int v) { x < v; }
                constraint c { lt(a); a == 20; }
            }
        }
    """, r"parameter 'v' const, so its argument must be a constant")


@_NEEDS_1A
def test_a_literal_actual_for_a_const_parameter_is_accepted():
    """GC-2.5's control: the const check must not reject the constant case."""
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint lt(const int v) { x < v; }
                constraint c { lt(20); x > 10; }
            }
        }
    """, seeds=range(16))
    solves_in(results, "x", lo=11, hi=19)


@_NEEDS_1A
def test_a_generic_constraint_with_an_empty_body_is_still_registered():
    """A declaration that translates to nothing must still be *known*.

    A fixed constraint with an empty body is dropped, which is harmless -- nothing
    references it. Doing the same to a generic constraint hid the declaration from
    the reference, so the reference survived to the backend as an unexpanded call
    and the *solver* was blamed for a frontend gap. `constraint g() {}` is legal
    besides, and constrains nothing.
    """
    results = solve_many("""
        component pss_top {
            action A {
                rand bit[8] x;
                constraint g() { }
                constraint c { g(); x < 10; }
            }
        }
    """, seeds=range(16))
    solves_in(results, "x", hi=9)


_PKG_SRC = """
    package p { static constraint lt(int v, int lim) { v < lim; } }
    component pss_top {
        action A {
            rand bit[8] x;
            constraint c { p::%s(x, 20); x > 10; }
        }
    }
"""


@_NEEDS_1A
def test_a_package_scope_generic_constraint_is_a_package_member():
    """A `static` generic constraint at package scope resolves through `p::`.

    Same symbol-table fix as the in-action case, seen from the other side: the
    name has to be a *member of the package*, which is how the reference failed
    before ("'p' has no member named ..."), rather than as an unknown identifier.

    The bogus-name half is what makes the first half mean anything: without it,
    "resolves" and "resolution was skipped entirely" look the same.
    """
    translate(_PKG_SRC % "lt")
    expect_error(_PKG_SRC % "nope", r"'p' has no member named 'nope'")


@_NEEDS_1A
def test_a_package_scope_generic_constraint_applies_its_body():
    """A package-scope reference instantiates the body, like any other (W1b).

    Worth a test of its own rather than folding into the in-action case, because
    the failure it guards is silent: `p::lt(x, 20)` reaches ast2ir as
    ``ExprRefPathStaticRooted``, which had no translation branch at all, and an
    expression that translates to ``None`` is not an error anywhere -- it is
    simply absent. Before W1b only `x > 10` was in force and x solved above 20,
    so the compiler produced *weaker stimulus than the source asks for* with no
    diagnostic. That is why this range is asserted on both sides.
    """
    results = solve_many(_PKG_SRC % "lt", seeds=range(32))
    solves_in(results, "x", lo=11, hi=19)


@_NEEDS_1A
def test_a_package_scope_generic_constraint_resolves_regardless_of_file_order():
    """PSS 18.2 makes declaration order irrelevant; the registry must agree.

    A package is not an IR type -- it is only a namespace prefix -- so a
    package-scope generic constraint has no type to be found on later. It is
    recorded in the CONST pass, before anything that could reference it is
    translated, and this is the test that says so: the package is declared
    *after* the component that references it.
    """
    src = (
        "component pss_top { action A { rand bit[8] x; "
        "constraint c { p::lt(x, 20); x > 10; } } } "
        "package p { static constraint lt(int v, int lim) { v < lim; } }")
    solves_in(solve_many(src, seeds=range(32)), "x", lo=11, hi=19)


@_NEEDS_1A
def test_a_package_scope_generic_constraint_is_inert_until_referenced():
    """Inertness is a property of the declaration, not of where it is declared."""
    src = (
        "package p { static constraint lt(int v, int lim) { v < lim; } } "
        "component pss_top { action A { rand bit[8] x; "
        "constraint c { x > 250; } } }")
    solves_in(solve_many(src, seeds=range(32)), "x", lo=251, hi=255)


@_NEEDS_1A
def test_a_component_scope_generic_constraint_applies_to_its_action():
    """§13.1.2 allows a declaration in component scope; an action may reference it.

    The body may only use its parameters here -- see the companion test below
    for what happens when it reaches for a field of the component instead.
    """
    src = (
        "component pss_top { constraint lt(int a, int b) { a < b; } "
        "action A { rand bit[8] x; constraint c { lt(x, 20); x > 10; } } }")
    solves_in(solve_many(src, seeds=range(32)), "x", lo=11, hi=19)


@_NEEDS_1A
def test_a_foreign_generic_constraint_may_not_reach_its_own_scopes_fields():
    """Expanding a component-scope body that reads a component field is reported.

    `self` inside that body is the *component*, but the body is instantiated
    into an action. Substituting it would rebind `y` to a same-named field of the
    action, or to nothing -- a constraint that reads as correct and constrains
    something else. The point of the diagnostic is that this is the one
    cross-scope case that cannot be made to work by substitution alone, so it
    must not be allowed to look like it did.
    """
    src = (
        "component pss_top { bit[8] y; constraint lt(int a) { a < y; } "
        "action A { rand bit[8] x; constraint c { lt(x); } } }")
    ctx = translate(src)
    assert any("declaring scope" in e for e in ctx.errors), ctx.errors


@_NEEDS_1A
def test_a_generic_constraint_is_inherited_from_a_base_type():
    """A non-shadowed base-type declaration resolves from the derived type.

    Asserted on the IR rather than through a solve: an action that inherits from
    another currently reaches the solver with *no* fields at all -- a plain
    `constraint g { x < 10; }` in the base is equally invisible -- so a solve
    here would report a be-bc inheritance gap (W4), not this one. Checking that
    the reference is gone from the IR is the strongest claim this layer can make.
    """
    src = (
        "component pss_top { action Base { rand bit[8] x; "
        "constraint g() { x < 10; } } "
        "action A : Base { constraint c { g(); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    assert not _contains_call(body), body


@_NEEDS_1A
def test_a_derived_generic_constraint_shadows_the_base_declaration():
    """§13.1.2 c: the derived declaration wins, not the base one."""
    src = (
        "component pss_top { action Base { rand bit[8] x; "
        "constraint g() { x < 10; } } "
        "action A : Base { constraint g() { x > 200; } "
        "constraint c { g(); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    assert not _contains_call(body), body
    assert _ops(body) == [ir.BinOp.Gt], "expanded the base declaration, not the override"


@_NEEDS_1A
def test_a_base_type_declared_after_the_derived_one_still_resolves():
    """The reason expansion is a whole-model pass rather than a per-type one.

    While each type expanded its own references as it finished translating, a
    base declared later in the file simply had not been translated yet, and the
    reference was left in place -- silently, because an unexpanded reference is
    only diagnosed much later, by the backend, as an unsupported expression.
    """
    src = (
        "component pss_top { action A : Base { constraint c { g(); } } "
        "action Base { rand bit[8] x; constraint g() { x < 10; } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    assert not _contains_call(body), body


# --- GC-11: provenance (W6) ------------------------------------------------
#
# Not a spec requirement. A generic constraint is the one PSS construct whose
# lowered form is *not* where the author wrote it: the body arrives somewhere
# else, with the arguments substituted, possibly several times over, possibly
# reached through another generic constraint. Without a link back, a diagnostic
# about the lowered form names a constraint the author never wrote.
#
# These tests assert the link *exists* and is specific enough to be useful. They
# do not assert any particular diagnostic, because no consumer emits one yet --
# which is exactly the condition under which this rots unnoticed.


@_NEEDS_1A
def test_an_instantiated_statement_names_the_generic_it_came_from():
    """GC-11.1: each spliced statement carries provenance naming its origin."""
    src = ("component pss_top { action A { rand bit[8] x; "
           "constraint lim_lt(int lim) { x < lim; } "
           "constraint c { lim_lt(200); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    prov = [s.provenance for s in body]
    assert all(p is not None for p in prov), body
    assert prov[0].pass_name == "generic_constraints"
    assert prov[0].source_names == ["lim_lt"]
    assert "lim_lt" in prov[0].description and "A" in prov[0].description


@_NEEDS_1A
def test_a_statement_the_author_wrote_carries_no_provenance():
    """The control: "has provenance" has to mean something.

    If every statement were stamped, the field would say nothing about where a
    constraint came from. `x < 10` written in a fixed constraint is at the place
    the author put it, and `loc` -- not provenance -- is what describes it.
    """
    src = ("component pss_top { action A { rand bit[8] x; "
           "constraint c { x < 10; } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    assert [s.provenance for s in body] == [None]


@_NEEDS_1A
def test_two_references_to_one_declaration_stay_distinguishable():
    """GC-11.2: the two instantiations are not merged into one origin.

    Both statements come from the same declaration, so the name cannot separate
    them and the substituted text need not either -- `lim_lt(200)` and
    `lim_lt(100)` differ here, but two references with equal arguments in
    different branches would not. `site` is what keeps them apart.
    """
    src = ("component pss_top { action A { rand bit[8] x; "
           "constraint lim_lt(int lim) { x < lim; } "
           "constraint c { lim_lt(200); lim_lt(100); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    sites = [s.provenance.site for s in body]
    assert all(s.provenance.source_names == ["lim_lt"] for s in body)
    assert len(set(sites)) == 2, f"both references share one site: {sites}"


@_NEEDS_1A
def test_an_indirect_reference_records_the_chain():
    """GC-11.3: reached through another generic constraint, both are named.

    The innermost declaration alone would be the minimum, but it is the less
    useful half: `lt` is where the constraint is written and `in_range` is what
    the author's own constraint actually referenced.
    """
    src = ("component pss_top { action A { rand bit[8] x; "
           "constraint lt(int hi) { x < hi; } "
           "constraint in_range(int hi) { lt(hi); x > 2; } "
           "constraint c { in_range(50); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    chains = sorted(tuple(s.provenance.source_names) for s in body)
    assert chains == [("in_range",), ("in_range", "lt")], chains
    inner = next(s for s in body
                 if s.provenance.source_names == ["in_range", "lt"])
    assert "reached from 'in_range'" in inner.provenance.description


@_NEEDS_1A
def test_a_substituted_value_carries_provenance_on_the_expression():
    """The value form is stamped on the expression, not on a statement.

    A value-yielding body is instantiated as a statement wrapping one expression,
    and the wrapper is discarded when the expression is substituted at the use
    site. Provenance recorded on the wrapper would be dropped with it -- and the
    drop would be invisible, since nothing downstream requires the field.
    """
    src = ("component pss_top { action A { rand bit[8] j; rand bit[8] k; "
           "constraint int plus1(int v) v + 1; "
           "constraint c { j == plus1(k); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    found = _provenances(body)
    assert [p.source_names for p in found] == [["plus1"]], found
    assert body[0].provenance is None, \
        "the enclosing equality is the author's, not the instantiation"


@_NEEDS_1A
def test_a_folded_operand_carries_provenance():
    """A reference used as an operand becomes a new node, which must be stamped.

    `g(20) || sel > 200` folds `g`'s body into a conjunction that did not exist
    before. Stamping only the statements it was built from would leave the
    provenance on nodes that are thrown away.
    """
    src = ("component pss_top { action A { rand bit[8] x; rand bit[8] sel; "
           "constraint g(int lim) { x < lim; } "
           "constraint c { g(20) || sel > 200; } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    found = _provenances(body)
    assert [p.source_names for p in found] == [["g"]], found


@_NEEDS_1A
def test_both_conflicting_generics_are_named_in_the_lowered_form():
    """GC-11.4, as far as it can be taken today.

    Two references that cannot both hold. The spec does not require the solver to
    say which constraint made the problem unsat (§8.4), and dv-solve does not, so
    what is asserted is the data a report would need: the unsat is reported as
    unsat, and each contributing statement names its own declaration.
    """
    src = ("component pss_top { action A { rand bit[8] x; "
           "constraint lo(int v) { x < v; } "
           "constraint hi(int v) { x > v; } "
           "constraint c { lo(10); hi(200); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    assert sorted(tuple(s.provenance.source_names) for s in body) == \
        [("hi",), ("lo",)]
    with pytest.raises(Exception) as caught:
        solve_many(src, seeds=range(2))
    assert "unsat" in type(caught.value).__name__.lower() or \
        "unsat" in str(caught.value).lower(), caught.value


@_NEEDS_1A
def test_provenance_adds_no_reachable_ir_nodes():
    """Annotating a node must not change what walking that node finds.

    The precise link would be a reference to the declaration's `ir.Function`.
    That makes every instantiated statement a back-edge into the declaration's
    subtree: a generic walk over the constraint then finds the declaration's
    *unexpanded* body -- which is how two of this file's own walkers started
    reporting a leftover reference -- and a recursive generic constraint would
    make it a cycle. Names are inert; nodes are not.
    """
    src = ("component pss_top { action A { rand bit[8] x; "
           "constraint lt(int hi) { x < hi; } "
           "constraint in_range(int hi) { lt(hi); x > 2; } "
           "constraint c { in_range(50); } } }")
    body = _constraint_body(translate(src), "pss_top::A", "c")
    found = _provenances(body)
    assert found, "nothing was stamped, so this proves nothing"
    for p in found:
        assert p.source_nodes == [], p
        assert all(isinstance(n, str) for n in p.source_names), p


def _provenances(node, depth: int = 0):
    """Every provenance record reachable in *node*, outermost first."""
    import dataclasses
    if depth > 32 or node is None:
        return []
    if isinstance(node, ir.Provenance):
        return [node]
    if isinstance(node, (list, tuple)):
        return [p for n in node for p in _provenances(n, depth + 1)]
    if not dataclasses.is_dataclass(node):
        return []
    return [p for f in dataclasses.fields(node)
            for p in _provenances(getattr(node, f.name), depth + 1)]


def _constraint_body(ctx, type_key: str, name: str):
    """The IR body of constraint *name* on type *type_key*."""
    for f in ctx.type_map[type_key].functions:
        if f.name == name:
            return f.body
    raise AssertionError(f"no constraint {name!r} on {type_key}: "
                         f"{[f.name for f in ctx.type_map[type_key].functions]}")


def _contains_call(node, depth: int = 0) -> bool:
    """Is there an unexpanded generic constraint reference left in *node*?"""
    import dataclasses
    if depth > 32 or node is None:
        return False
    if isinstance(node, ir.ExprCall):
        return True
    if isinstance(node, (list, tuple)):
        return any(_contains_call(n, depth + 1) for n in node)
    if not dataclasses.is_dataclass(node):
        return False
    return any(_contains_call(getattr(node, f.name), depth + 1)
               for f in dataclasses.fields(node))


_OP_TEXT = {
    "Add": "+", "Sub": "-", "Mult": "*", "Div": "/", "Mod": "%",
    "Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=", "Gt": ">", "GtE": ">=",
    "And": "&&", "Or": "||", "Not": "!",
}


def _render(node, depth: int = 0) -> str:
    """*node* as compact source-like text, for asserting a substituted shape.

    An IR-level assertion is only as good as its precision: "no call remains"
    passes for a reference that was dropped as well as for one that was
    substituted. Rendering lets a test name the expression it expects instead --
    which matters most for exactly the cases that cannot be solved.
    """
    import dataclasses
    if depth > 32 or node is None:
        return "?"
    if isinstance(node, ir.StmtExpr):
        return _render(node.expr, depth + 1)
    if isinstance(node, ir.ExprBin):
        op = _OP_TEXT.get(node.op.name, node.op.name)
        return f"({_render(node.lhs, depth + 1)} {op} {_render(node.rhs, depth + 1)})"
    if isinstance(node, ir.ExprUnary):
        op = _OP_TEXT.get(node.op.name, node.op.name)
        return f"{op}{_render(node.operand, depth + 1)}"
    if isinstance(node, ir.ExprIfExp):
        return (f"({_render(node.test, depth + 1)} ? "
                f"{_render(node.body, depth + 1)} : "
                f"{_render(node.orelse, depth + 1)})")
    if isinstance(node, ir.ExprAttribute):
        return f"{_render(node.value, depth + 1)}.{node.attr}"
    if isinstance(node, ir.TypeExprRefSelf):
        return "self"
    if isinstance(node, ir.ExprConstant):
        return str(node.value)
    if isinstance(node, ir.ExprRefLocal):
        return node.name
    if isinstance(node, ir.ExprCall):
        args = ", ".join(_render(a, depth + 1) for a in node.args)
        return f"{_render(node.func, depth + 1)}({args})"
    if isinstance(node, ir.ExprRefUnresolved):
        return node.name
    return type(node).__name__


def _ops(node, depth: int = 0):
    """Every binary operator in *node*, outermost first."""
    import dataclasses
    if depth > 32 or node is None:
        return []
    if isinstance(node, ir.ExprBin):
        return [node.op]
    if isinstance(node, (list, tuple)):
        return [op for n in node for op in _ops(n, depth + 1)]
    if not dataclasses.is_dataclass(node):
        return []
    return [op for f in dataclasses.fields(node)
            for op in _ops(getattr(node, f.name), depth + 1)]
