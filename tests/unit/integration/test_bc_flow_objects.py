"""Flow objects on bc (B5): buffers and states.

A flow-object reference is an object in its action, one slot per scalar
(``inp.tag``). ``bind a.out b.inp`` makes the two one object: each scalar of
one equals the other's, in the cone, so the consumer -- solved after the
producer committed -- holds exactly the producer's values (D-B13). A state
input reads its pool's current object, pinned when its action is solved; a
state output replaces it when its action completes; before any output, the
pool holds its initial object, solved with the root, ``initial`` true (LRM
12.5, D-B14). A buffer input no ``bind`` connects picks an object a completed
action output to its pool (B5d).
"""
from collections import Counter

import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model
from zuspec.be.bc.interp.activation import ScopeUnsatError
from zuspec.ir.core.xf.validate import UnsupportedConstructError

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _runs(src, seeds=range(40), root="pss_top::T"):
    """Each seed's run: the printed lines, each split into its words."""
    model = _lower(src, root=root)
    out = []
    for seed in seeds:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([ln.split() for ln in lines if ln.strip()])
    return out


_BUF = """import std_pkg::*;
component pss_top {
    buffer data_b { rand bit[8] v; rand bit[8] w; }
    pool data_b dp;
    bind dp *;
    action P {
        output data_b o;
        constraint o.v in [1..10];
        exec body { message(NONE, "P %u %u", o.v, o.w); }
    }
    action C {
        input data_b i;
        constraint i.v > 5;
        exec body { message(NONE, "C %u %u", i.v, i.w); }
    }
@BODY@
}
"""


def test_a_bound_input_is_the_output():
    """UC1: the consumer holds the producer's object; its constraint is
    lookahead for the producer's values."""
    got = _runs(_BUF.replace("@BODY@", """
    action T { P p; C c; activity { p; c; bind p.o c.i; } }"""))
    for (pt, pv, pw), (ct, cv, cw) in got:
        assert (pt, ct) == ("P", "C")
        assert (pv, pw) == (cv, cw)
        assert 5 < int(pv) <= 10
    assert len({r[0][1] for r in got}) > 2


def test_a_value_the_producers_body_wrote_reaches_the_consumer():
    got = _runs(_BUF.replace("@BODY@", """
    action W {
        output data_b o;
        constraint o.v > 5;
        exec body { o.v = 7; o.w = 9; }
    }
    action T { W p; C c; activity { p; c; bind p.o c.i; } }"""), seeds=range(5))
    assert all(r == [["C", "7", "9"]] for r in got)


def test_a_compound_passes_its_childs_output_on():
    """A hierarchical bind: the compound's output is its child's."""
    got = _runs(_BUF.replace("@BODY@", """
    action PP { output data_b o; P p; activity { p; bind p.o o; } }
    action T { PP pp; C c; activity { pp; c; bind pp.o c.i; } }"""))
    for (pt, pv, pw), (ct, cv, cw) in got:
        assert (pv, pw) == (cv, cw) and int(pv) > 5


def test_bind_operands_must_be_references():
    with pytest.raises(UnsupportedConstructError, match="bind operands"):
        _lower(_BUF.replace("@BODY@", """
    action T { P p; C c; rand bit[8] x; activity { p; c; bind p.o x; } }"""))


# -- states ---------------------------------------------------------------------

_STATE = """import std_pkg::*;
component pss_top {
    state cfg_s {
        rand bit[2] mode;
        rand bit[4] gen;
        constraint initial -> mode == 0;
    }
    pool cfg_s cp;
    bind cp *;
    action Set {
        input cfg_s pre;
        output cfg_s post;
        constraint pre.mode == 0;
        constraint post.mode == 2;
        exec body { message(NONE, "Set %u %u %u %u", pre.mode, (int)pre.initial,
                            post.mode, post.gen); }
    }
    action Get {
        input cfg_s cur;
        exec body { message(NONE, "Get %u %u %u", cur.mode, (int)cur.initial, cur.gen); }
    }
    action Need2 {
        input cfg_s cur;
        constraint cur.mode == 2;
        exec body { message(NONE, "Need2 %u", cur.gen); }
    }
@BODY@
}
"""


def test_a_reader_sees_the_initial_object_then_the_last_output():
    got = _runs(_STATE.replace("@BODY@", """
    action T { activity { do Get; do Set; do Get; do Need2; } }"""))
    for g0, st, g1, n2 in got:
        assert g0[:3] == ["Get", "0", "1"]           # initial: mode 0
        assert st[1:4] == ["0", "1", "2"]            # read the initial object
        assert g1 == ["Get", "2", "0", st[4]]        # Set's output, not initial
        assert n2 == ["Need2", st[4]]
    assert len({r[1][4] for r in got}) > 3           # post.gen is random


def test_the_initial_object_is_random_where_unconstrained():
    got = _runs(_STATE.replace("@BODY@", """
    action T { activity { do Get; do Get; } }"""))
    for (_, m0, i0, g0), (_, m1, i1, g1) in got:
        assert (m0, i0, g0) == (m1, i1, g1)          # one object
    assert len({r[0][3] for r in got}) > 3


def test_a_state_input_no_object_satisfies_is_an_error():
    """`Need2` before any `Set`: the pool holds the initial object, mode 0.
    Nothing infers a `Set` (P4); it is an error naming the traversal."""
    with pytest.raises(ScopeUnsatError, match="Need2"):
        _runs(_STATE.replace("@BODY@", """
    action T { activity { do Need2; } }"""), seeds=range(1))


# -- picking a producer ---------------------------------------------------------

_LANES = """import std_pkg::*;
buffer ext_b { rand bit[8] lane; rand bit[8] v; }
component lane_c {
    bit[8] id;
    pool ext_b ep;
    bind ep *;
    action W {
        output ext_b o;
        constraint o.lane == comp.id;
        exec body { message(NONE, "W %u %u", o.lane, o.v); }
    }
    action R {
        input ext_b i;
        exec body { message(NONE, "R %u %u %u", comp.id, i.lane, i.v); }
    }
}
component pss_top {
    lane_c lanes[3];
    exec init_down { foreach (lanes[k]) { lanes[k].id = k; } }
@BODY@
}
"""


def test_an_unbound_input_picks_a_completed_output_of_its_pool():
    """B5d: each read holds a write's object, from the pool of the lane it
    runs in; each write is read once while unread ones remain."""
    got = _runs(_LANES.replace("@BODY@", """
    action T { activity { repeat (6) { do lane_c::W; } repeat (6) { do lane_c::R; } } }"""))
    lanes = Counter()
    for r in got:
        writes = [tuple(x[1:]) for x in r if x[0] == "W"]
        reads = [x[1:] for x in r if x[0] == "R"]
        assert len(reads) == 6
        for comp, lane, v in reads:
            assert comp == lane and (lane, v) in writes
            lanes[comp] += 1
        assert sorted(tuple(x[1:]) for x in reads) == sorted(writes)
    assert set(lanes) == {"0", "1", "2"}


def test_a_pick_steers_the_lane_and_the_lane_the_pick():
    """One write only has v > 200: the read picks it, and so runs in its
    lane."""
    got = _runs(_LANES.replace("@BODY@", """
    action T { activity {
        repeat (3) { do lane_c::W with { o.v <= 200; }; }
        do lane_c::W with { o.v > 200; };
        repeat (3) { do lane_c::W with { o.v <= 200; }; }
        do lane_c::R with { i.v > 200; };
    } }"""), seeds=range(60))
    lanes = set()
    for r in got:
        big = [tuple(x[1:]) for x in r if x[0] == "W" and int(x[2]) > 200]
        comp, lane, v = [x[1:] for x in r if x[0] == "R"][0]
        assert [(lane, v)] == big and comp == lane
        lanes.add(comp)
    assert lanes == {"0", "1", "2"}


def test_a_pick_with_no_candidate_is_an_error():
    with pytest.raises(ScopeUnsatError, match="inferring a producer"):
        _runs(_LANES.replace("@BODY@", """
    action T { activity { do lane_c::R; } }"""), seeds=range(1))
