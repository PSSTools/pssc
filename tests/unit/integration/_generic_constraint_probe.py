"""Capability probe: one representative of each generic-constraint group.

Not a test -- a diagnostic. Run it to regenerate the capability map in
``docs/design/generic-constraints-system-tests.md`` (7.1):

    direnv exec . env PYTHONPATH=$PWD/src python \\
        tests/unit/integration/_generic_constraint_probe.py

It exists because the first round of xfail reasons could not tell "our bug" from
"a be-bc gap". It reports WHERE each case dies -- link, translate, lower, solve
-- rather than pass/fail, which is the distinction that assigns the work.

Every case that still fails now carries a ``control``: the same model with the
generic constraint written out by hand. A row whose control dies identically
reports ``NOT-OURS`` and names what actually broke. That turned out to be ALL
FIVE of the remaining failures -- ternary lowering, struct flattening, type
inheritance, array flattening and inline traversal constraints are each missing
a layer below this one. Without the controls those five read as generic-constraint
gaps, which is precisely the misattribution this file was written to prevent.

Underscore-prefixed so pytest does not collect it.
"""
import sys, traceback
sys.path.insert(0, "tests/unit/integration")
from _bc_harness import translate, solve_many

SEEDS = range(16)

CASES = []
def case(cid, desc, src, field="x", lo=None, hi=None, expect_error=None,
         action="pss_top::A", check_ir=None, control=None):
    """Register one probe case.

    *check_ir* is for a property of the *lowering* rather than of the solved
    values -- provenance is the one such group (GC-11). It receives the
    translation context and returns a detail string, or raises to report a gap.

    *control* is the same model with the generic constraint written out by hand.
    When a case fails, the control is run too: if it fails the same way, the
    generic constraint is not what is broken and the row reports ``NOT-OURS``.
    This is the probe earning its keep -- three of the failures it has reported
    so far turned out to belong to other layers, and a row that merely says
    "died at solve" invites the work to be assigned to whoever owns the feature
    named in the row.
    """
    CASES.append(dict(cid=cid, desc=desc, src=src, field=field, lo=lo, hi=hi,
                      expect_error=expect_error, action=action,
                      check_ir=check_ir, control=control))

A = lambda body: "component pss_top { action A { %s } }" % body

# --- GC-1 activation
case("GC-1.4", "indirect: c -> a() -> b()",
     A("rand bit[8] x; constraint b() { x < 20; } constraint a() { b(); } "
       "constraint c { a(); x > 10; }"), lo=11, hi=19)
case("GC-1.5", "reference in a logical-OR (value position)",
     A("rand bit[8] x; constraint small() { x < 50; } constraint big() { x > 200; } "
       "constraint c { small() || big(); }"))
case("GC-1.7", "two conflicting generics both referenced -> unsat",
     A("rand bit[8] x; constraint lo_() { x < 10; } constraint hi_() { x > 200; } "
       "constraint c { lo_(); hi_(); }"), expect_error="unsat|empty|conflict")

# --- GC-2 parameters
case("GC-2.1", "two literal params",
     A("rand bit[8] x; constraint in_range(int lo, int hi) { x >= lo; x <= hi; } "
       "constraint c { in_range(20, 30); }"), lo=20, hi=30)
case("GC-2.2", "two refs, different actuals (intersection)",
     A("rand bit[8] x; constraint in_range(int lo, int hi) { x >= lo; x <= hi; } "
       "constraint c { in_range(20, 40); in_range(30, 50); }"), lo=30, hi=40)
case("GC-2.3", "actual is a rand field",
     A("rand bit[8] x; rand bit[8] a; constraint lt(int v) { x < v; } "
       "constraint c { lt(a); a == 20; x > 10; }"), lo=11, hi=19)
case("GC-2.4", "actual is an expression",
     A("rand bit[8] x; rand bit[8] a; constraint lt(int v) { x < v; } "
       "constraint c { lt(a + 1); a == 19; x > 10; }"), lo=11, hi=19)
case("GC-2.5", "const parameter",
     A("rand bit[8] x; constraint lt(const int v) { x < v; } "
       "constraint c { lt(20); x > 10; }"), lo=11, hi=19)

# --- GC-3 value-yielding
case("GC-3.1", "value-yielding numeric, used as j == mx(k,l)",
     A("rand bit[8] j; rand bit[8] k; rand bit[8] l; "
       "constraint numeric mx(numeric a, numeric b) (a < b) ? b : a; "
       "constraint c { j == mx(k, l); k == 5; l == 9; }"), field="j", lo=9, hi=9,
     # The ternary is the body of the LRM's own value-yielding example, so this
     # row cannot go green until be-bc lowers ExprIfExp at all. The control
     # writes the same ternary inline with no generic constraint anywhere.
     control=A("rand bit[8] j; rand bit[8] k; rand bit[8] l; "
               "constraint c { j == ((k < l) ? l : k); k == 5; l == 9; }"))
case("GC-3.5", "explicitly typed value form",
     A("rand bit[8] x; constraint int twice(int a) a + a; "
       "constraint c { x == twice(10); }"), lo=20, hi=20)
case("GC-3.7", "one numeric value form at two widths",
     A("rand bit[8] a; rand bit[8] x; rand bit[16] c16; rand bit[16] d16; "
       "constraint numeric inc(numeric v) v + 1; "
       "constraint c { x == inc(a); a == 10; d16 == inc(c16); c16 == 300; }"),
     lo=11, hi=11)

# --- GC-4 recursion
case("GC-4.3", "recursion gated by a random expression -> error",
     A("rand bit[8] x; rand bit[8] n; constraint r(int d) { if (n > 0) { r(d); } } "
       "constraint c { r(1); }"), expect_error="recur|random|gate|itself")

# --- GC-5 scope
case("GC-5.1", "declared in a struct, referenced there",
     "struct S { rand bit[8] x; constraint lt(int v) { x < v; } "
     "constraint c { lt(20); x > 10; } } "
     "component pss_top { action A { rand S s; } }",
     field="s.x", lo=11, hi=19,
     # The frontend does its part: `c` comes out of ast2ir holding
     # `self.x < 20` and `self.x > 10`, provenance-stamped to `lt`. What never
     # happens is the STRUCT being flattened into the solve problem -- the
     # action's `rand S s` reaches the solver as one scalar var that solves to
     # 0, so no constraint inside S has anything to act on. The control below
     # writes the same bound out by hand and dies identically, which is what
     # keeps this row from being read as a generic-constraint gap. Same family
     # as GC-9.6's missing array flattening.
     control="struct S { rand bit[8] x; constraint c { x < 20; x > 10; } } "
             "component pss_top { action A { rand S s; } }")
case("GC-5.3", "declared in a component, referenced from its action",
     "component pss_top { constraint lt(int v) { 1 == 1; } "
     "action A { rand bit[8] x; constraint c { lt(20); x > 10; } } }", lo=11, hi=255)
case("GC-5.4", "package static, qualified reference",
     "package p { static constraint lt(int v, int lim) { v < lim; } } "
     "component pss_top { action A { rand bit[8] x; "
     "constraint c { p::lt(x, 20); x > 10; } } }", lo=11, hi=19)
case("GC-5.5", "package WITHOUT static (implicitly static)",
     "package p { constraint lt(int v, int lim) { v < lim; } } "
     "component pss_top { action A { rand bit[8] x; "
     "constraint c { p::lt(x, 20); x > 10; } } }", lo=11, hi=19)

# --- GC-6 inheritance
case("GC-6.1", "derived shadows a generic constraint",
     "component pss_top { action Base { rand bit[8] x; constraint g() { x < 10; } } "
     "action A : Base { constraint g() { x > 200; } constraint c { g(); } } }",
     lo=201, hi=255,
     # Type inheritance is the blocker, not shadowing: an `action A : Base {}`
     # solves to {} even for a plain inherited constraint. The control derives
     # the same way and states the bound directly.
     control="component pss_top { action Base { rand bit[8] x; "
             "constraint g0 { x < 10; } } "
             "action A : Base { constraint c { x > 200; } } }")

# --- GC-9 interaction with other forms
case("GC-9.1", "reference inside an if/else constraint",
     A("rand bit[8] x; rand bit[8] m; constraint lt(int v) { x < v; } "
       "constraint c { if (m == 1) { lt(20); } else { lt(100); } m == 1; x > 10; }"),
     lo=11, hi=19)
case("GC-9.2", "reference as an implication consequent",
     A("rand bit[8] x; rand bit[8] m; constraint lt(int v) { x < v; } "
       "constraint c { m == 1; m == 1 -> lt(20); x > 10; }"), lo=11, hi=19)
case("GC-9.5", "unique inside a generic body",
     A("rand bit[8] x; rand bit[8] y; constraint u() { unique { x, y }; } "
       "constraint c { u(); x < 3; y < 3; }"), lo=0, hi=2)
case("GC-9.6", "foreach inside a generic body",
     A("rand bit[8] arr[4]; constraint f(int lim) { foreach (e : arr) { e < lim; } } "
       "constraint c { f(20); }"), field="arr", lo=None, hi=None,
     # foreach needs per-element solver vars, which the lowering does not build
     # yet -- nothing to do with the generic wrapper around it.
     control=A("rand bit[8] arr[4]; "
               "constraint c { foreach (e : arr) { e < 20; } }"))
case("GC-9.7", "reference from an inline with{} on a traversal",
     "component pss_top { action B { rand bit[8] x; constraint lt(int v) { x < v; } } "
     "action A { activity { do B with { lt(20); }; } } }", field=None,
     # `do B with {...}` is declared unimplemented, generic reference or not.
     control="component pss_top { action B { rand bit[8] x; } "
             "action A { activity { do B with { x < 20; }; } } }")

# --- GC-11 provenance (a property of the lowering, so IR-inspecting)
def _provenance_of_c(ctx):
    body = [f for f in ctx.type_map["pss_top::A"].functions
            if f.name == "c"][0].body
    prov = [s.provenance for s in body]
    assert all(p is not None for p in prov), "unstamped statement(s) in `c`"
    chains = [tuple(p.source_names) for p in prov]
    sites = [p.site for p in prov]
    assert len(set(sites)) == len(sites), "two references share one site: %s" % sites
    return "chains %s, sites %s" % (chains, sites)

case("GC-11.1/11.2", "each instantiation names its origin and its own site",
     A("rand bit[8] x; constraint lim_lt(int lim) { x < lim; } "
       "constraint c { lim_lt(200); lim_lt(100); }"), check_ir=_provenance_of_c)
case("GC-11.3", "indirect reference records the chain",
     A("rand bit[8] x; constraint lt(int hi) { x < hi; } "
       "constraint in_range(int hi) { lt(hi); x > 2; } "
       "constraint c { in_range(50); }"), check_ir=_provenance_of_c)

# --- negative
case("NEG-1", "local variable declared in a generic body -> error",
     A("rand bit[8] x; constraint g() { int t; t == 1; } constraint c { g(); }"),
     expect_error="local|variable|declar")
case("NEG-5", "default under a generic constraint -> error",
     A("rand bit[8] x; constraint g() { default x == 3; } constraint c { g(); }"),
     expect_error="default")
case("NEG-6", "typo'd reference -> error",
     A("rand bit[8] x; constraint g() { x < 20; } constraint c { gg(); }"),
     expect_error="unknown|undeclared")
case("NEG-8", "value-yielding constraint used in boolean position",
     A("rand bit[8] x; constraint int twice(int a) a + a; constraint c { twice(1); }"),
     expect_error="bool|type|not a")
case("NEG-9", "assignment to a const parameter -> error",
     A("rand bit[8] x; constraint g(const int v) { v == 5; x < v; } "
       "constraint c { g(20); }"), expect_error="const"),


def _value_outcome(c, src):
    """(status, detail) for a value-expecting case built from *src*."""
    try:
        ctx = translate(src)
    except Exception as e:
        return ("LINK-FAIL", str(e).strip().splitlines()[0][:90])
    if ctx.errors:
        return ("TRANSLATE-FAIL", "\n".join(ctx.errors).splitlines()[0][:90])
    try:
        res = solve_many(src, action=c["action"], seeds=SEEDS)
    except Exception as e:
        return ("SOLVE-FAIL", "%s: %s" % (type(e).__name__,
                                          str(e).strip().splitlines()[0][:70]))
    if c["field"] is None:
        return ("SOLVED", "no value check (%s)" % list(res[0].keys())[:4])
    vals = [r.get(c["field"]) for r in res]
    if any(v is None for v in vals):
        return ("NO-FIELD", "field %r not in %s"
                % (c["field"], list(res[0].keys())[:6]))
    if c["lo"] is None:
        return ("SOLVED", "values %s" % sorted(set(vals))[:6])
    bad = [v for v in vals if v < c["lo"] or v > c["hi"]]
    if bad:
        return ("NOT-ENFORCED", "expected [%s..%s], saw %s"
                % (c["lo"], c["hi"], sorted(set(bad))[:6]))
    return ("OK", "all seeds in [%s..%s]" % (c["lo"], c["hi"]))


def run(c):
    # IR-property cases (GC-11): nothing about the solved values is at issue.
    if c["check_ir"]:
        try:
            ctx = translate(c["src"])
        except Exception as e:
            return ("LINK-FAIL", str(e).strip().splitlines()[0][:90])
        if ctx.errors:
            return ("TRANSLATE-FAIL", "\n".join(ctx.errors).splitlines()[0][:90])
        try:
            return ("OK", c["check_ir"](ctx))
        except AssertionError as e:
            return ("NO-PROVENANCE", str(e)[:90])
    # error-expecting cases
    if c["expect_error"]:
        import re
        try:
            ctx = translate(c["src"])
        except Exception as e:
            return ("ERROR-AT-LINK", str(e).strip().splitlines()[0][:90])
        errs = "\n".join(ctx.errors)
        if errs:
            return ("ERROR-AT-TRANSLATE", errs.splitlines()[0][:90])
        try:
            solve_many(c["src"], action=c["action"], seeds=[0])
        except Exception as e:
            return ("ERROR-AT-SOLVE", "%s: %s" % (type(e).__name__, str(e).strip().splitlines()[0][:70]))
        return ("NO-ERROR", "compiled and solved cleanly")
    # value-expecting cases
    status, detail = _value_outcome(c, c["src"])
    if status == "OK" or not c["control"]:
        return (status, detail)

    # It failed and there is a control. If the hand-written equivalent fails the
    # same way, the generic constraint is not the thing that is broken.
    c_status, c_detail = _value_outcome(c, c["control"])
    if c_status == status:
        return ("NOT-OURS", "control fails identically (%s: %s)"
                % (c_status, c_detail))
    return (status, "%s  [control: %s]" % (detail, c_status))


w = max(len(c["cid"]) for c in CASES)
for c in CASES:
    try:
        status, detail = run(c)
    except Exception:
        status, detail = "PROBE-BUG", traceback.format_exc().splitlines()[-1][:90]
    print("%-*s  %-18s %-52s %s" % (w, c["cid"], status, c["desc"][:52], detail))
