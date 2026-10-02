"""A conditional value (``c ? a : b``) in a constraint on bc (LRM 8.4).

bc lowers it to the solver's if-then-else value, which is bounded by both
branches while ``c`` is open and narrows ``c`` from what the value may be.
"""
import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_SRC = """
import std_pkg::*;
component pss_top {
    action T {
        rand bit[8] x;
        rand bit[8] y;
        rand bit[8] z;
        constraint y == (x < 100 ? x + 101 : 7);
        constraint z == (x > 200 ? 1 : (x > 100 ? 2 : 3));
        exec body { message(NONE, "%u %u %u", x, y, z); }
    }
}
"""


def _runs(src, seeds):
    model = _lower(src, root="pss_top::T")
    out = []
    for seed in seeds:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.extend(tuple(int(v) for v in ln.split()) for ln in lines if ln.strip())
    return out


def test_a_conditional_value_takes_the_branch_its_condition_selects():
    got = _runs(_SRC, range(100))
    for x, y, z in got:
        assert y == (x + 101 if x < 100 else 7)
        assert z == (1 if x > 200 else 2 if x > 100 else 3)
    assert {z for _, _, z in got} == {1, 2, 3}


def test_a_constraint_on_the_value_narrows_the_condition():
    """`y == 7` holds only on the else branch, so x is at least 100."""
    src = _SRC.replace("rand bit[8] z;", "rand bit[8] z;\n        constraint y == 7;")
    for x, y, _ in _runs(src, range(50)):
        assert y == 7 and x >= 100
