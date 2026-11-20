"""The C engine schedules activities as the Python VM does (P4.10f2).

Each case runs one model on both engines and compares the lines its exec
bodies print, in order: a called function runs at once, while a traversal
(`do`) waits its turn and so does its caller when it returns.
"""
import pytest

from . import act_sweep
from . import test_bc_recursion as rec
from .test_activity_bc_runs import _ACTIONS, _lower, trace


def _alike(src: str, seed: int = 0):
    lines, exc = act_sweep.run_c(_lower(src), seed, 0)
    assert exc is None
    assert lines == trace(src, seed=seed)
    return lines


def test_join_first_lets_a_started_branch_finish_on_c():
    src = _ACTIONS % "action T { activity { parallel join_first(1) { do B; do C; } } }"
    assert _alike(src) == ["B", "C"]


def test_a_recursive_call_is_no_scheduling_point_on_c():
    src = rec._SRC % ("", "down(2);", "activity { parallel { do P; do Q; } }")
    assert _alike(src) == ["d2", "d1", "d0", "q"]


@pytest.mark.parametrize("act", [
    "parallel { sequence { do A; do B; } do C; }",
    "parallel { do A; select { do B; do C; } }",
    "parallel join_first(1) { sequence { do A; do B; } do C; }",
])
def test_interleaved_traversals_run_in_the_same_order_on_c(act):
    _alike(_ACTIONS % ("action T { activity { %s } }" % act))


def test_footprints_give_the_same_lanes_on_c():
    """Each parallel branch's footprint (one lane of two) is chosen on C
    as on the Python VM, for every seed."""
    from .test_bc_resources import _LANES, _model, _runs
    for seed in range(40):
        py = [" ".join(w) for w in _runs(_model(_LANES), seeds=[seed])[0]]
        lines, exc = act_sweep.run_c(_model(_LANES), seed, 0)
        assert exc is None and lines == py, seed


def test_a_long_if_chain_fits_the_c_engines_registers():
    """bc gives each temporary its own register: 600 clauses use ~9700."""
    from .test_bc_if_chain import _SRC, _chain
    src = _SRC % (_chain(600), 'message(NONE, "%d %d %d %d", '
                  'pick(0), pick(7), pick(599), pick(600));')
    assert _alike(src) == ["0 21 1797 -1"]


def test_reading_comp_before_its_choice_is_an_error_on_c():
    src = """
import std_pkg::*;
component c_c {
    int f;
    action A { exec pre_solve { message(NONE, "%d", comp.f); } }
}
component pss_top { c_c c1, c2; action T { activity { do c_c::A; } } }
"""
    lines, exc = act_sweep.run_c(_lower(src), 0, 0)
    assert lines == [] and "before the action's solve chose" in str(exc)


@pytest.mark.parametrize("act", [
    "repeat (i : 3) { repeat (j : 2) { do A with { val == 4 * i + j; }; } }",
    "parallel { repeat (i : 3) { do A with { val == i; }; }"
    " repeat (i : 3) { do A with { val == 10 + i; }; } }",
    "repeat (i : 3) { parallel { do A with { val == i; }; do A with { val == 5 + i; }; } }",
    "repeat (i : 8) { do A with { val > i; }; }",
])
@pytest.mark.parametrize("seed", [0, 7])
def test_a_loop_index_in_a_with_is_the_counter_on_c(act, seed):
    """The C engine reads the counter from the frame that runs the loop,
    through calls and spawned branches (P4.10f3)."""
    from .test_lookahead import _HDR
    _alike(_HDR + "    action T { activity { %s } }\n}\n" % act, seed)
