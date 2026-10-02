"""A constraint that reads a component attribute (``comp.f``) on bc.

A component attribute is fixed once the component tree is constructed
(``init_down``/``init_up``), so a constraint reading it reads an input of
the solve: one per instance the action may run in, pinned from the
component object when the cone is solved. With several instances, the value
read is tied to the instance the action's ``comp`` chooses -- so a
constraint over the value steers the choice, and the other way round.
"""
from collections import Counter

import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_LANES = """
import std_pkg::*;
component lane_c {
    bit[8] lane_id;
    action io_a {
        rand bit[8] lane;
        rand bit[8] x;
        constraint lane == comp.lane_id;
        constraint x == comp.lane_id * 10 + 1;
        exec body { message(NONE, "%u %u %u", comp.lane_id, lane, x); }
    }
    action wrap_a {
        rand bit[8] w;
        constraint w == comp.lane_id + 100;
        io_a i;
        activity { i; }
        exec post_solve { message(NONE, "%u", w); }
    }
}
component pss_top {
    lane_c lanes[4];
    lane_c one;
    bit[8] top_id;
    exec init_down {
        foreach (lanes[i]) { lanes[i].lane_id = i + 2; }
        one.lane_id = 9;
        top_id = 77;
    }
@BODY@
}
"""


def _runs(body, root="pss_top::T", seeds=range(100)):
    model = _lower(_LANES.replace("@BODY@", body), root=root)
    out = []
    for seed in seeds:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([tuple(int(v) for v in ln.split()) for ln in lines if ln.strip()])
    return out


def test_the_value_read_is_the_chosen_instances():
    """io_a may run in any of the five lane_c instances: whichever is
    chosen, lane and x follow its lane_id."""
    got = _runs("action T { activity { do lane_c::io_a; } }")
    seen = Counter()
    for (lid, lane, x), in got:
        assert lane == lid and x == lid * 10 + 1
        seen[lid] += 1
    assert set(seen) == {2, 3, 4, 5, 9}


def test_a_constraint_on_the_value_steers_the_instance():
    got = _runs("action T { activity { do lane_c::io_a with { lane == 4; }; } }")
    assert all(r == [(4, 4, 41)] for r in got)


def test_steering_the_instance_fixes_the_value():
    got = _runs("""action T { activity {
        do lane_c::io_a with { comp == this.comp.lanes[3]; }; } }""")
    assert all(r == [(5, 5, 51)] for r in got)


def test_a_value_no_instance_has_is_unsat():
    from zuspec.be.bc.interp.activation import ScopeUnsatError
    with pytest.raises(ScopeUnsatError):
        _runs("action T { activity { do lane_c::io_a with { lane == 7; }; } }",
              seeds=range(1))


def test_one_instance_is_a_pinned_input():
    """The root runs in the root component only."""
    got = _runs("""action T {
        rand bit[8] y;
        constraint y == comp.top_id + 1;
        exec post_solve { message(NONE, "%u", y); } }""")
    assert all(r == [(78,)] for r in got)


def test_a_child_reads_the_instance_its_parent_chose():
    """io_a runs in wrap_a's instance, which wrap_a's solve chooses."""
    got = _runs("action T { activity { do lane_c::wrap_a; } }")
    seen = set()
    for (w,), (lid, lane, x) in got:
        assert w == lid + 100 and lane == lid and x == lid * 10 + 1
        seen.add(lid)
    assert seen == {2, 3, 4, 5, 9}


def test_successive_traversals_each_choose():
    """Each traversal of a loop chooses again; the reads follow."""
    got = _runs("action T { activity { repeat (8) { do lane_c::io_a; } } }",
                seeds=range(20))
    lids = Counter()
    for r in got:
        for lid, lane, x in r:
            assert lane == lid and x == lid * 10 + 1
            lids[lid] += 1
    assert set(lids) == {2, 3, 4, 5, 9}
