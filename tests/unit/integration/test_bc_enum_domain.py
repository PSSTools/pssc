"""A ``rand`` enum holds one of its members (LRM 7.5), wherever it is solved.

The solver sees a scalar's domain through one helper (ir-core
``layout.leaf_domain``), used both by an action's own problem and by the
action tree's cones; an enum's members become an ``in`` constraint beside its
type's own. Before, the variable was a 32-bit integer with no constraint, and
almost every value drawn was not a member.

Each case runs 200 seeds and requires every printed value to be a member, and
(so a test cannot pass by printing one constant) more than one member seen.
"""
from typing import List

import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

SEEDS = range(200)

_HDR = """\
import std_pkg::*;
enum op_e { OP_READ, OP_WRITE, OP_FLUSH }
enum sparse_e { X = 1, Y = 5, Z = 9 }
enum neg_e { NEG = -3, ZERO = 0, POS = 4 }
struct s_s { rand op_e op; rand bit[4] n; }
component pss_top {
"""


def runs(src: str, seeds=SEEDS) -> List[List[int]]:
    model = _lower(_HDR + src + "\n}\n")
    out = []
    for seed in seeds:
        lines: List[str] = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([int(ln) for ln in lines if ln.strip()])
    return out


def _members(got, legal):
    seen = {v for r in got for v in r}
    assert seen <= set(legal), "not members: %s" % sorted(seen - set(legal))
    assert len(seen) > 1
    return seen


def test_action_field():
    got = runs("""
    action T { rand op_e op; exec body { message(NONE, "%d", (int)op); } }""")
    assert _members(got, {0, 1, 2}) == {0, 1, 2}


def test_struct_leaf():
    got = runs("""
    action T { rand s_s s; exec body { message(NONE, "%d", (int)s.op); } }""")
    assert _members(got, {0, 1, 2}) == {0, 1, 2}


def test_sparse_enum():
    """Members 1, 5, 9: three separate values, not the range [1..9]."""
    got = runs("""
    action T { rand sparse_e v; exec body { message(NONE, "%d", (int)v); } }""")
    assert _members(got, {1, 5, 9}) == {1, 5, 9}


def test_negative_members():
    got = runs("""
    action T { rand neg_e v; exec body { message(NONE, "%d", (int)v); } }""")
    _members(got, {-3, 0, 4})


def test_constrained_enum_keeps_its_other_constraints():
    """The domain sits beside the type's constraints; it does not replace
    them, and a wide value tied to the enum is not cut to the enum's width."""
    got = runs("""
    action T {
        rand sparse_e v; rand bit[16] n;
        constraint { v != Z; (v == Y) -> n == 300; (v != Y) -> n == 7; }
        exec body { message(NONE, "%d", (int)v); message(NONE, "%d", n); }
    }""")
    for v, n in got:
        assert v in (1, 5)
        assert n == (300 if v == 5 else 7)
    assert {r[0] for r in got} == {1, 5}


def test_enum_in_a_cone():
    """A node in a cone (a parent constraint ties a and b) solves through the
    cone's variables, which take their domain from the same helper."""
    got = runs("""
    action A { rand op_e op; exec body { message(NONE, "%d", (int)op); } }
    action T {
        A a, b;
        constraint { a.op != b.op; }
        activity { a; b; }
    }""")
    for a, b in got:
        assert a != b
    _members(got, {0, 1, 2})


def test_enum_in_a_cone_struct_leaf():
    got = runs("""
    action A { rand s_s s; exec body { message(NONE, "%d", (int)s.op); } }
    action T {
        A a, b;
        constraint { a.s.n < b.s.n; }
        activity { a; b; }
    }""")
    _members(got, {0, 1, 2})
