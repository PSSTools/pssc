"""Value selection with lookahead across an activity (P1.4; LRM 13.4.7-13.4.10).

A traversal solves its action in the cone of the activation it belongs to:
every constraint the rest of the activity will impose is in force, the values
already chosen are pinned, and only the traversed action's values are kept
(P1-D2). Each test runs 200 seeds and requires every run to finish with every
constraint holding. Without lookahead Ex 183 picks ``a.val = 15`` on some seed
and has no legal ``b.val`` left; P1.6 adds the switch that shows it.
"""
from typing import List

import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

SEEDS = range(200)

_HDR = """\
import std_pkg::*;
component pss_top {
    action A { rand bit[4] val; exec body { message(NONE, "%u", val); } }
"""


def runs(src: str, seeds=SEEDS) -> List[List[int]]:
    """The values each run prints, per seed. The model is lowered once."""
    model = _lower(_HDR + src + "\n}\n")
    out = []
    for seed in seeds:
        lines: List[str] = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([int(ln) for ln in lines if ln.strip()])
    return out


def test_ex179_183_sub_action_values_hold_the_parent_constraint():
    """13.4.7, 13.4.9: a < b < c, chosen in traversal order. a.val is never
    14 or 15: nothing would be left for b and c."""
    got = runs("""
    action T {
        A a, b, c;
        constraint abc_c { a.val < b.val; b.val < c.val; }
        activity { a; b; c; }
    }""")
    for a, b, c in got:
        assert a < b < c
    assert max(r[0] for r in got) <= 13
    assert len({r[0] for r in got}) > 5          # still random


def test_ex184_lookahead_reaches_into_an_untraversed_compound():
    """13.4.10: v.val == s1.a.val, and s1.a.val < s1.b.val < s1.c.val, so
    v.val is chosen <= 13 although s1 has not been traversed."""
    got = runs("""
    action sub {
        A a, b, c;
        constraint abc_c { a.val < b.val; b.val < c.val; }
        activity { a; b; c; }
    }
    action T {
        A v; sub s1;
        constraint c { s1.a.val == v.val; }
        activity { v; s1; }
    }""")
    for v, a, b, c in got:
        assert v == a and a < b < c
    assert max(r[0] for r in got) <= 13


def test_ex180_a_loop_iteration_resets_its_handles():
    """13.4.8: b and c are uninitialized on entry to each iteration, so the
    second iteration's b.x need not exceed the first's c.x -- but a.x, chosen
    before the loop, holds against both."""
    got = runs("""
    action T {
        A a, b, c;
        constraint abc_c { a.val < b.val; b.val < c.val; }
        activity { a; repeat (2) { b; c; } }
    }""")
    for a, b1, c1, b2, c2 in got:
        assert a < b1 < c1 and a < b2 < c2
    assert any(b2 <= c1 for _, _, c1, b2, _ in got)


def test_a_handle_never_traversed_makes_its_constraints_vacuous():
    """13.4.8: b is never traversed, so `a.val < b.val` never has to hold and
    does not stop a.val from being 15."""
    got = runs("""
    action T {
        A a, b;
        constraint { a.val > 14; a.val < b.val; }
        activity { a; }
    }""", range(5))
    assert got == [[15]] * 5


def test_parent_values_are_chosen_with_lookahead_into_the_activity():
    """The parent solves first, over the constraints its children will
    impose: n must leave room for a.val < n < b.val."""
    got = runs("""
    action T {
        rand bit[4] n;
        A a, b;
        constraint { a.val < n; n < b.val; }
        activity { a; b; }
        exec post_solve { message(NONE, "%u", n); }
    }""")
    for n, a, b in got:
        assert a < n < b
