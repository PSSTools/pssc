"""Default value constraints on bc (LRM 13.1.11).

A ``default x == v`` holds unless one of higher precedence reaches x: a
higher-level containing context, a derived type over its base, a later
statement over an earlier one in the same type (13.1.11 d). A ``default
disable`` removes it. Which one holds is resolved over the whole object
(ir-core ``pss_lower/defaults.py``): in an action's own problem, and across
the action tree, where a parent's ``default disable c.x`` reaches into its
child's node.
"""
import pytest

from zuspec.be.bc.interp import NativeBlobBackend, SolveUnsatError, run_model
from zuspec.ir.core.xf.validate import UnsupportedConstructError

from .test_activity_bc_runs import _lower
from .test_lookahead import _HDR

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

SEEDS = range(40)


def _runs(src):
    """Every number each seed's run prints, in order."""
    model = _lower(_HDR + src + "\n}\n")
    out = []
    for seed in SEEDS:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([int(v) for ln in lines for v in ln.split()])
    return out


def test_ex154_a_containing_context_overrides_and_disables():
    """LRM Ex 154 (with `bit[4]` and a named constraint for `in [0..3]`)."""
    got = _runs("""
    struct my_struct {
        rand bit[4] attr1;
        constraint attr1 <= 3;
        constraint default attr1 == 0;      // (1)
        rand bit[4] attr2;
        constraint attr2 <= 3;
        constraint attr1 < attr2;           // (2)
    }
    action T {
        rand my_struct s1;
        rand my_struct s2;
        constraint default s2.attr1 == 2;   // (3)
        rand my_struct s3;
        constraint default disable s3.attr1; // (4)
        constraint s3.attr1 > 0;            // (5)
        exec body { message(NONE, "%u %u %u %u %u %u", s1.attr1, s1.attr2,
                            s2.attr1, s2.attr2, s3.attr1, s3.attr2); }
    }""")
    for r in got:
        s1a1, s1a2, s2a1, s2a2, s3a1, s3a2 = r
        assert s1a1 == 0 and 1 <= s1a2 <= 3
        assert s2a1 == 2 and s2a2 == 3
        assert 1 <= s3a1 <= 2 and s3a1 < s3a2 <= 3
    assert {r[4] for r in got} == {1, 2}            # s3.attr1 is free


def _flat(got):
    return [v for r in got for v in r]


def test_a_later_statement_in_the_same_type_wins():
    got = _runs("""
    action T {
        rand bit[4] x, y;
        constraint default x == 1;
        constraint default disable x;
        constraint x > 8;
        constraint default disable y;
        constraint default y == 3;
        exec body { message(NONE, "%u %u", x, y); }
    }""")
    assert all(x > 8 and y == 3 for x, y in got)


def test_a_derived_struct_overrides_its_base():
    got = _runs("""
    struct base_s { rand bit[4] a; constraint default a == 1; }
    struct der_s : base_s { constraint default a == 2; }
    action T { rand der_s s; exec body { message(NONE, "%u", s.a); } }""")
    assert set(_flat(got)) == {2}


def test_disabling_an_aggregate_disables_every_scalar_under_it():
    got = _runs("""
    struct s_s { rand bit[4] a; rand bit[4] b;
                 constraint default a == 1; constraint default b == 2; }
    action T {
        rand s_s s1, s2;
        constraint default disable s2;
        exec body { message(NONE, "%u %u %u %u", s1.a, s1.b, s2.a, s2.b); }
    }""")
    assert all(r[0] == 1 and r[1] == 2 for r in got)
    assert len({r[2] for r in got}) > 3 and len({r[3] for r in got}) > 3


def test_a_default_that_contradicts_a_constraint_is_unsat():
    """13.1.11 a: the default holds; a conflict is a contradiction, not a
    reason to drop it."""
    with pytest.raises(SolveUnsatError, match="solving 'T': no values"):
        _runs("""
        action T {
            rand bit[4] x;
            constraint default x == 1;
            constraint x == 2;
            exec body { message(NONE, "%u", x); }
        }""")


# -- across the action tree --------------------------------------------------

_CHILD = """
    action C {
        rand bit[4] x;
        rand bool flag;
        constraint default flag == false;
        constraint default x == 3;
        exec body { message(NONE, "%u %u", x, (int)flag); }
    }"""


def test_a_parent_disables_its_childs_default():
    """The model's own pattern: the child defaults a flag; one parent turns
    it on, after disabling the default. A child traversed elsewhere keeps
    it."""
    got = _runs(_CHILD + """
    action P {
        C c;
        constraint default disable c.flag;
        constraint c.flag;
        activity { c; }
    }
    action T { P p; C plain; activity { p; plain; } }""")
    for (x1, f1, x2, f2) in got:
        assert (x1, f1) == (3, 1)
        assert (x2, f2) == (3, 0)


def test_a_parent_default_overrides_its_childs():
    got = _runs(_CHILD + """
    action T {
        C c1, c2;
        constraint default c1.x == 7;
        activity { c1; c2; }
    }""")
    assert all(r == [7, 0, 3, 0] for r in got)


def test_a_grandparent_wins_over_a_parent():
    got = _runs(_CHILD + """
    action P { C c; constraint default c.x == 7; activity { c; } }
    action T {
        P p1, p2;
        constraint default disable p1.c.x;
        constraint p1.c.x > 10;
        activity { p1; p2; }
    }""")
    for x1, _, x2, _ in got:
        assert x1 > 10 and x2 == 7
    assert len({r[0] for r in got}) > 2


def test_a_disabled_childs_default_does_not_reach_its_own_solve():
    """Without the parent, C's own problem holds the default; the node the
    parent disabled it on is solved in a cone, not by C's problem."""
    got = _runs(_CHILD + """
    action T {
        C c;
        constraint default disable c.x;
        activity { c; }
    }""")
    assert len({r[0] for r in got}) > 3


# -- refused ------------------------------------------------------------------

def test_a_default_under_a_condition_is_refused():
    with pytest.raises(UnsupportedConstructError, match="13.1.11 f"):
        _lower("""import std_pkg::*;
component pss_top {
    action T { rand bit[4] x, y;
        constraint if (y > 2) { default x == 1; }
        exec body { message(NONE, "%u", x); } }
}""")


def test_a_default_on_a_non_rand_attribute_is_refused():
    with pytest.raises(UnsupportedConstructError, match="13.1.11 c"):
        _lower("""import std_pkg::*;
component pss_top {
    action T { bit[4] x; rand bit[4] y;
        constraint default x == 1;
        exec body { message(NONE, "%u", y); } }
}""")
