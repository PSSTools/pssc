"""Resource claims on bc (B5c, B5e).

A ``lock``'s ``instance_id`` is one of its pool's instances (LRM 12.4), and
not one a conflicting claim holds: the activation's claim table, as a bit
mask pinned when the claiming action is solved (D-B12). A claim is held from
the action's solve to its end; a compound's, for its whole activity.

Every lock in one branch of a ``parallel`` is concurrent with every lock in
every other (LRM 11.3.4 a), so the branches lock disjoint instances. On entry
the activation gives each branch a footprint per shared pool -- one
traversal of the branch probed, then the rest dealt out -- and each lock
takes an instance in its branch's footprint. More concurrent locks than
instances is an error, never a wait.
"""
import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model
from zuspec.be.bc.interp.activation import ScopeUnsatError

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _model(src, root="pss_top::T"):
    return _lower(src, root=root)


def _runs(model, seeds=range(40), footprints=True):
    """Each seed's run: the printed lines, each split into its words."""
    out = []
    for seed in seeds:
        if not footprints:
            # Calibration only: no footprints, so only claims already made
            # by another branch are excluded.
            for table in model.activations.values():
                table.par.clear()
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([ln.split() for ln in lines if ln.strip()])
    return out


_POOL = """import std_pkg::*;
component pss_top {
    resource r_s {}
    pool [@N@] r_s rp;
    bind rp *;
    action L { lock r_s r; exec body { message(NONE, "L %u", r.instance_id); } }
    action S { share r_s r; exec body { message(NONE, "S %u", r.instance_id); } }
    action H {
        lock r_s r;
        L l;
        activity { l; }
        exec post_solve { message(NONE, "H %u", r.instance_id); }
    }
@BODY@
}
"""


def _pool(n, body):
    return _POOL.replace("@N@", str(n)).replace("@BODY@", body)


def test_instance_id_is_one_of_the_pools_instances():
    got = _runs(_model(_pool(3, "action T { activity { repeat (6) { do L; } } }")))
    ids = {int(x[1]) for r in got for x in r}
    assert ids == {0, 1, 2}


def test_parallel_locks_take_different_instances():
    got = _runs(_model(_pool(2, "action T { activity { parallel { do L; do L; } } }")))
    for r in got:
        assert sorted(int(x[1]) for x in r) == [0, 1]


def test_parallel_over_subscription_is_an_error():
    """UC8: three concurrent locks of a pool of two. Not serialized."""
    with pytest.raises(ScopeUnsatError, match="parallel cannot lock"):
        _runs(_model(_pool(2, "action T { activity { parallel { do L; do L; do L; } } }")),
              seeds=range(1))


def test_shares_may_overlap():
    got = _runs(_model(_pool(1, "action T { activity { parallel { do S; do S; } } }")),
                seeds=range(5))
    assert all(sorted(x[1] for x in r) == ["0", "0"] for r in got)


def test_a_compound_holds_its_lock_through_its_activity():
    """`H`'s lock is held while its activity runs, so `Z`, in parallel with
    it, takes neither H's instance nor its child's."""
    got = _runs(_model(_pool(3, """
    action Z { lock r_s r; exec body { message(NONE, "Z %u", r.instance_id); } }
    action T { activity { parallel { do H; do Z; } } }""")))
    for r in got:
        ids = {x[0]: int(x[1]) for x in r}
        assert ids["Z"] not in (ids["H"], ids["L"])


def test_loops_in_parallel_branches_lock_disjoint_instances():
    got = _runs(_model(_pool(4, """
    action T { activity { parallel {
        repeat (5) { do L; }
        repeat (5) { do L; }
    } } }""")))
    used = set()
    for r in got:
        # The branches interleave: tell them apart by order of first use.
        ids = [int(x[1]) for x in r]
        a, b = set(ids[0::2]), set(ids[1::2])
        assert not a & b
        used.add(len(a | b))
    assert max(used) > 2                   # not one instance per branch


# -- the benchmark's shape: two pools that must be taken in one lane ---------

_LANES = """import std_pkg::*;
resource slot_s {}
resource chan_s {}
component lane_c {
    bit[8] id;
    pool [2] slot_s sp;
    bind sp *;
    pool [1] chan_s cp;
    bind cp *;
    action X {
        lock chan_s c;
        exec body { message(NONE, "X %u %u", comp.id, c.instance_id); }
    }
    action IO {
        lock slot_s s;
        X x;
        activity { x; }
    }
}
component pss_top {
    lane_c lanes[2];
    exec init_down { foreach (lanes[k]) { lanes[k].id = k; } }
    action T { activity { parallel {
        repeat (3) { do lane_c::IO; }
        repeat (3) { do lane_c::IO; }
    } } }
}
"""


def test_footprints_spread_branches_over_lanes():
    """Each lane has two slots but one channel, so the two branches must use
    different lanes. Their IOs interleave, and an IO chooses its lane before
    its child locks the channel, so excluding only claims already made
    picks a full lane on some seed; footprints never do."""
    model = _model(_LANES)
    for r in _runs(model):
        lanes = [x[1] for x in r]
        assert len(lanes) == 6 and set(lanes[0::2]).isdisjoint(lanes[1::2])
    failed = 0
    for seed in range(40):
        try:
            _runs(_model(_LANES), seeds=[seed], footprints=False)
        except ScopeUnsatError:
            failed += 1
    assert failed, "the test does not need footprints"


# -- LRM Example 134 ------------------------------------------------------------

_EX134 = """import std_pkg::*;
resource cpu_core_s {}
component dma_c {
    resource channel_s {}
    pool[2] channel_s channels;
    bind channels {*};
    action transfer {
        lock channel_s chan;
        lock cpu_core_s core;
        exec body { message(NONE, "%u %u", chan.instance_id, core.instance_id); }
    }
}
component pss_top {
    dma_c dma0, dma1;
    pool[4] cpu_core_s cpu;
    bind cpu {dma0.*, dma1.*};
    action par_dma_xfers {
        dma_c::transfer xfer_a;
        dma_c::transfer xfer_b;
        constraint xfer_a.comp != xfer_b.comp;
        constraint xfer_a.chan.instance_id == xfer_b.chan.instance_id; // OK
        @CORE@
        activity { parallel { xfer_a; xfer_b; } }
    }
}
"""


def test_ex134_the_same_channel_of_two_instances():
    got = _runs(_model(_EX134.replace("@CORE@", ""), root="pss_top::par_dma_xfers"))
    for (ca, ka), (cb, kb) in got:
        assert ca == cb and ka != kb


def test_ex134_the_same_core_is_a_conflict():
    with pytest.raises(ScopeUnsatError, match="parallel cannot lock"):
        _runs(_model(_EX134.replace(
            "@CORE@", "constraint xfer_a.core.instance_id == xfer_b.core.instance_id;"),
            root="pss_top::par_dma_xfers"), seeds=range(1))
