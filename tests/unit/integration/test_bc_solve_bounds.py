"""A run's solves keep within given bounds, and report what they chose (P5.5a).

A failing property test shrinks by re-running with its rand fields bounded
toward their minimum (``run_model(solve_bounds=)``, ``Activation.bounds``);
``RunResult.solved`` is each rand variable's chosen value, by the name its
cone gives it.
"""
import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


_SRC = """import std_pkg::*;
component pss_top {
    action S { rand bit[8] v; exec body { message(NONE, "S %u", v); } }
    action T {
        rand bit[8] x;
        rand bit[8] y;
        constraint y > x;
        S s;
        constraint s.v == x;
        activity { s; }
    }
}
"""


def _run(model, seed, bounds=None):
    lines = []
    r = run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append, solve_bounds=bounds)
    return r, [ln.split() for ln in lines if ln.strip()]


def test_solved_values_are_reported_by_name():
    model = _lower(_SRC)
    r, lines = _run(model, 1)
    assert {"x", "y"} <= set(r.solved), r.solved
    assert r.solved["y"] > r.solved["x"]
    assert int(lines[0][1]) == r.solved["x"]


def test_an_upper_bound_holds_in_every_solve():
    model = _lower(_SRC)
    unbounded = [_run(model, seed)[0].solved["x"] for seed in range(20)]
    assert max(unbounded) > 3
    for seed in range(20):
        r, lines = _run(model, seed, {"x": (None, 3)})
        assert r.solved["x"] <= 3 and r.solved["y"] > r.solved["x"]
        assert int(lines[0][1]) == r.solved["x"]


def test_both_bounds_and_a_bound_naming_no_variable():
    model = _lower(_SRC)
    for seed in range(10):
        r, _ = _run(model, seed, {"x": (5, 6), "nope": (0, 0)})
        assert r.solved["x"] in (5, 6)
    # No bound in force: the same values as an unbounded run.
    assert _run(model, 4, {"nope": (0, 0)})[0].solved == _run(model, 4)[0].solved
