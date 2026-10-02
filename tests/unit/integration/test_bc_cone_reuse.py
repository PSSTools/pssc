"""A node takes its values from its cone's last solution (B6e).

Each solve of a cone finds values for all of it. A later node of the cone
whose pins all equal that solution's values, solved under constraints that
solution satisfied, takes its values from it: the solution is a solution of
the later solve too. Only values the solution chose, and once per node --
a loop iteration does not repeat the last one's values.
"""
import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model
from zuspec.be.bc.interp.solve_cache import SolveCache

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _runs(model, seeds=range(30), reuse=True):
    """Each seed's run: its lines split into words, and the solves reused."""
    for table in model.activations.values():
        table.reuse = reuse
    out = []
    for seed in seeds:
        lines, cache = [], SolveCache()
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append, solve_cache=cache)
        cache.clear()
        out.append(([ln.split() for ln in lines if ln.strip()], cache.reused))
    return out


_PIPE = """import std_pkg::*;
component pss_top {
    action S { rand bit[8] v; exec body { message(NONE, "S %u", v); } }
    action P {
        rand bit[8] v;
        S a, b, c;
        constraint v < 200;
        constraint a.v == v; constraint b.v == v + 1; constraint c.v == v + 2;
        activity { a; b; c; }
    }
@BODY@
}
"""


def test_children_take_their_values_from_the_parents_solve():
    model = _lower(_PIPE.replace("@BODY@", "action T { activity { do P; } }"))
    for lines, reused in _runs(model):
        a, b, c = (int(x[1]) for x in lines)
        assert b == a + 1 and c == a + 2
        assert reused == 3


def test_a_loop_iteration_does_not_repeat_the_last_ones_values():
    model = _lower(_PIPE.replace("@BODY@", "action T { activity { repeat (8) { do P; } } }"))
    for lines, _ in _runs(model):
        firsts = [int(x[1]) for x in lines[0::3]]
        assert len(set(firsts)) > 4, firsts


def test_the_values_do_not_depend_on_reuse_but_through_the_seed():
    """Same constraints, either way; different values is allowed."""
    model = _lower(_PIPE.replace("@BODY@", "action T { activity { repeat (4) { do P; } } }"))
    for reuse in (True, False):
        for lines, reused in _runs(model, seeds=range(10), reuse=reuse):
            for i in range(0, len(lines), 3):
                a, b, c = (int(x[1]) for x in lines[i:i + 3])
                assert (b, c) == (a + 1, a + 2) and a < 200
            assert (reused > 0) == reuse


_STATE = """import std_pkg::*;
component pss_top {
    state cfg_s { rand bit[4] mode; constraint initial -> mode == 0; }
    pool cfg_s cp;
    bind cp *;
    action Set { output cfg_s post; constraint post.mode == 3; }
    action Get {
        input cfg_s cur;
        rand bit[8] v;
        constraint cur.mode == 3;
        constraint !cur.initial;
        exec body { message(NONE, "Get %u %u %u", cur.mode, (int)cur.initial, v); }
    }
    action P {
        rand bit[8] v;
        Get g1;
        Get g2;
        constraint g1.v == v; constraint g2.v == v;
        activity { g1; g2; }
    }
    action T { activity { do Set; do P; } }
}
"""


def test_a_state_input_taken_from_a_solution_holds_the_pools_object():
    """Each read is pinned when it is solved. The parent's solution agrees
    with it (the reads' constraints fix it), so the read takes its values from
    that solution -- and must still write the pinned ones into its node."""
    model = _lower(_STATE)
    for lines, reused in _runs(model):
        assert [x[1:3] for x in lines] == [["3", "0"], ["3", "0"]]
        assert lines[0][3] == lines[1][3]
        assert reused > 0
