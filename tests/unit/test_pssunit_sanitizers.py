"""P1.12: sanitizers, first set -- ``poll-budget``, ``unmapped``,
``access-rule`` (T1.12).

Each is a consumer of the VM's ``access`` events; a finding is a FAIL of the
test located at the access, and ``@pssunit_pkg::allow`` switches one off.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

import pssc.testing.vm as pv
from pssc.testing import sanitizers

BASE = 0x1000

SUITE = """\
import addr_reg_pkg::*;
import pssunit_pkg::*;

package s_pkg {
    import addr_reg_pkg::*;
    pure component rw_c : reg_c<bit[32], READWRITE, 32> {}
    pure component ro_c : reg_c<bit[32], READONLY, 32> {}
    pure component wo_c : reg_c<bit[32], WRITEONLY, 32> {}
    pure component grp_c : reg_group_c {
        rw_c st;
        ro_c id;
        wo_c cmd;
        pure function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["st"]: return 0x0;
                ["id"]: return 0x4;
                ["cmd"]: return 0xc;
                default: return -1;
            }
        }
        pure function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { default: return -1; }
        }
    }
}

component s_f : fixture_c {
    s_pkg::grp_c regs;
    function void place() { regs.set_handle(make_handle_from_handle(0, 0x1000)); }
    @pssunit_pkg::test
    function void t_spin() {
        place();
        while (regs.st.read_val() == 0) { }
    }
    @pssunit_pkg::test
    function void t_ok() {
        place();
        regs.cmd.write_val(1);
        while (regs.st.read_val() == 0) { }
        write32(make_handle_from_handle(0, 0x4000), 5);
    }
    @pssunit_pkg::test
    function void t_rules() {
        place();
        regs.id.write_val(1);
        regs.cmd.read_val();
    }
    @pssunit_pkg::allow {.what = "access-rule"}
    @pssunit_pkg::test
    function void t_rules_allowed() {
        place();
        regs.id.write_val(1);
    }
    @pssunit_pkg::test
    function void t_hole() {
        place();
        regs.st.write_val(0);
        write32(make_handle_from_handle(0, 0x1008), 5);
    }
}
"""


def _line(text: str) -> int:
    return SUITE[:SUITE.index(text)].count("\n") + 1


def _at(text: str) -> str:
    """``line:col`` (1-based) of the first ``text`` in SUITE."""
    i = SUITE.index(text)
    return "%d:%d" % (_line(text), i - SUITE.rfind("\n", 0, i))


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    f = tmp_path_factory.mktemp("san") / "suite.pss"
    f.write_text(SUITE)
    return f


class Ready(pv.RegisterFake):
    """``st`` reads 1 from its ``after``-th read on: a device that completes."""
    after = 5

    def __init__(self):
        super().__init__()
        self.reads = 0

    @pv.on_read("regs.st")
    def count(self, ctx, acc):
        self.reads += 1
        if self.reads >= self.after:
            self.poke_reg("regs.st", 1)


def test_a_spin_that_never_completes_is_a_located_failure(suite):
    s = pv.load([suite], root="s_f", platform=pv.RegisterFake(), poll_budget=50)
    n_pass, n_fail = s.run_test("s_f::t_spin")[:2]
    assert n_fail == 1
    [f] = s.findings
    assert f.name == "poll-budget"
    assert f.where == "%s:%s" % (suite, _at("while (regs.st"))
    assert "regs.st" in f.what and "51 times" in f.what
    assert "poll-budget" in s.stopped


@pytest.mark.parametrize("after", [5, 1000])
def test_a_poll_that_completes_within_the_budget_passes(suite, after):
    # calibration: the default budget lets a device that completes after
    # 1000 polls through
    fake = Ready()
    fake.after = after
    s = pv.load([suite], root="s_f", platform=fake)
    assert s.run_test("s_f::t_ok")[:2] == (0, 0)
    assert s.findings == [] and s.stopped is None
    assert fake.reads == after


def test_access_rules(suite):
    s = pv.load([suite], root="s_f", platform=pv.RegisterFake())
    assert s.run_test("s_f::t_rules")[1] == 2
    wr, rd = s.findings
    assert (wr.name, rd.name) == ("access-rule", "access-rule")
    assert "write to read-only register regs.id" in wr.what
    assert "read of write-only register regs.cmd" in rd.what
    assert wr.where == "%s:%s" % (suite, _at("regs.id.write_val(1);"))
    assert s.stopped is None        # recorded; the test went on


def test_allow_switches_one_off(suite):
    s = pv.load([suite], root="s_f", platform=pv.RegisterFake())
    assert s.model.allows == {"s_f::t_rules_allowed": {"access-rule"}}
    assert s.run_test("s_f::t_rules_allowed")[1] == 0 and s.findings == []
    assert s.run_test("s_f::t_rules")[1] == 2      # only for that test


def test_unmapped(suite):
    # a hole in a register block is unmapped; memory elsewhere is not ...
    s = pv.load([suite], root="s_f", platform=pv.RegisterFake())
    assert s.run_test("s_f::t_hole")[1] == 1
    [f] = s.findings
    assert f.name == "unmapped" and "0x1008" in f.what and "regs" in f.what
    assert f.where == "%s:%s" % (suite, _at("write32(make_handle_from_handle(0, 0x1008)"))
    fake = Ready()
    s = pv.load([suite], root="s_f", platform=fake)
    assert s.run_test("s_f::t_ok")[1] == 0
    # ... unless regions are declared, and it is in none
    fake = Ready()
    s = pv.load([suite], root="s_f", platform=fake, regions=[(0x8000, 0x100)])
    assert s.run_test("s_f::t_ok")[1] == 1
    assert "declared region" in s.findings[0].what
    fake = Ready()
    fake.regions = [(0x4000, 0x100)]
    s = pv.load([suite], root="s_f", platform=fake)
    assert s.run_test("s_f::t_ok")[1] == 0


def test_findings_are_sanitizer_events(suite):
    class Sink:
        def __init__(self):
            self.seen = []

        def event(self, kind, coro=0, src_ref=0, detail=None):
            if kind == "sanitizer":
                self.seen.append(detail)

    from zuspec.be.bc.trace import events as ev
    sink = Sink()
    s = pv.load([suite], root="s_f", platform=pv.RegisterFake(), events=sink,
                watch=ev.EV_SANITIZER)
    s.run_test("s_f::t_rules")
    assert [d["name"] for d in sink.seen] == ["access-rule", "access-rule"]


def test_sanitize_off_and_unknown_allow(suite, tmp_path):
    s = pv.load([suite], root="s_f", platform=pv.RegisterFake(), sanitize=False)
    assert s.sanitizers is None
    assert s.run_test("s_f::t_rules")[1] == 0
    bad = tmp_path / "bad.pss"
    bad.write_text(SUITE.replace('"access-rule"}', '"acess-rule"}'))
    with pytest.raises(ValueError, match="acess-rule"):
        pv.load([bad], root="s_f")


def test_stream_consumer_alone():
    regs = {0: {"path": "r.a", "comp": "r", "offset": 0, "width": 32,
                "access": "READWRITE", "fields": []}}
    san = sanitizers.Sanitizers(regs, poll_budget=2)
    assert san.access({"rw": "r", "addr": 0x10, "data": 0, "reg_id": 0}) is None
    san.access({"rw": "r", "addr": 0x10, "data": 0})
    san.access({"rw": "w", "addr": 0x10, "data": 1})     # progress
    san.access({"rw": "r", "addr": 0x10, "data": 0})
    san.access({"rw": "r", "addr": 0x10, "data": 0})
    with pytest.raises(sanitizers.SanitizerFailed, match="poll-budget"):
        san.access({"rw": "r", "addr": 0x10, "data": 0})


# -- P2.5a: inflight (T2.5) ---------------------------------------------------

LATCH = """\
import sync_pkg::*;
import pssunit_pkg::*;

component l_f : fixture_c {
    channel_c<bit, 1> busy;
    channel_c<bit, 1> wake;
    function bool start() {
        if (!busy.try_put(1)) { return false; }
        return true;
    }
    function void finish() {
        bit tok;
        busy.try_get(tok);
    }
    @pssunit_pkg::test
    function void t_released() {
        start();
        finish();
        wake.try_put(1);
        wake.get();
    }
    @pssunit_pkg::test
    function void t_leaked() {
        start();
    }
    @pssunit_pkg::allow {.what = "inflight"}
    @pssunit_pkg::test
    function void t_leaked_allowed() {
        start();
    }
    @pssunit_pkg::test
    function void t_stale_wake() {
        wake.try_put(1);
    }
}
"""


@pytest.fixture(scope="module")
def latch(tmp_path_factory):
    f = tmp_path_factory.mktemp("latch") / "latch.pss"
    f.write_text(LATCH)
    return f


def test_inflight_latches_are_try_only_channels(latch):
    s = pv.load([latch], root="l_f")
    assert s.sanitizers.latches == {"busy"}


def test_inflight_catches_a_missing_release(latch):
    # seeded bug (plan §8.4 "missing inflight release"): start() claims
    # the latch and nothing releases it
    s = pv.load([latch], root="l_f")
    assert s.run_test("l_f::t_leaked")[1] == 1
    [f] = s.findings
    assert f.name == "inflight"
    assert "channel busy still holds 1 token" in f.what
    i = LATCH.index("busy.try_put(1)")
    line = LATCH[:i].count("\n") + 1
    assert f.where is not None and f.where.split(":")[-2] == str(line)


def test_inflight_clean_and_allowed(latch):
    s = pv.load([latch], root="l_f")
    assert s.run_test("l_f::t_released")[1] == 0
    assert s.findings == []
    assert s.run_test("l_f::t_leaked_allowed")[1] == 0
    # a wake channel (someone get()s it) left with a stale token is not a latch
    assert s.run_test("l_f::t_stale_wake")[1] == 0
    # each test starts clean: the leak of one is not the next one's
    assert s.run_test("l_f::t_leaked")[1] == 1
    assert s.run_test("l_f::t_released")[1] == 0


def test_inflight_stream_consumer_alone():
    san = sanitizers.Sanitizers({}, latches=["busy"])
    san.chan({"channel": "busy", "op": "try_put", "ok": True, "value": 1}, 7)
    san.chan({"channel": "busy", "op": "try_put", "ok": False}, 8)
    san.chan({"channel": "other", "op": "try_put", "ok": True, "value": 1}, 9)
    [f] = san.end()
    assert (f.name, f.src_ref) == ("inflight", 7)
    san.start()
    san.chan({"channel": "busy", "op": "try_put", "ok": True, "value": 1}, 7)
    san.chan({"channel": "busy", "op": "try_get", "ok": True, "value": 1}, 8)
    assert san.end() == []


RC = """\
import addr_reg_pkg::*;
import pssunit_pkg::*;

package rc_pkg {
    import addr_reg_pkg::*;
    struct st_s : packed_s<LITTLE_ENDIAN> {
        bit done;
        bit[31] rest;
    }
    @pssunit_pkg::field_access {.field = "done", .kind = pssunit_pkg::access_kind_e::RC}
    pure component st_c : reg_c<st_s, READWRITE, 32> {}
    pure component rw_c : reg_c<bit[32], READWRITE, 32> {}
    pure component grp_c : reg_group_c {
        st_c st;
        rw_c ctl;
        pure function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["st"]: return 0x0;
                ["ctl"]: return 0x4;
                default: return -1;
            }
        }
        pure function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { default: return -1; }
        }
    }
}

component rc_f : fixture_c {
    rc_pkg::grp_c regs;
    function void place() { regs.set_handle(make_handle_from_handle(0, 0x1000)); }
    @pssunit_pkg::test
    function void t_dropped() {
        place();
        regs.st.read_val();
    }
    @pssunit_pkg::test
    function void t_used() {
        bit[32] v;
        place();
        v = regs.st.read_val();
        regs.ctl.write_val(v);
        regs.ctl.read_val();
    }
    @pssunit_pkg::allow {.what = "rc-read"}
    @pssunit_pkg::test
    function void t_dropped_allowed() {
        place();
        regs.st.read_val();
    }
}
"""


@pytest.fixture(scope="module")
def rc(tmp_path_factory):
    f = tmp_path_factory.mktemp("rc") / "rc.pss"
    f.write_text(RC)
    return f


def test_rc_read_catches_a_dropped_value(rc):
    # seeded bug: a read-to-clear status read as a statement loses `done`
    s = pv.load([rc], root="rc_f")
    assert s.run_test("rc_f::t_dropped")[1] == 1
    [f] = s.findings
    assert f.name == "rc-read"
    assert "regs.st" in f.what and "done" in f.what
    i = RC.index("regs.st.read_val();")
    line = RC[:i].count("\n") + 1
    assert f.where is not None and f.where.split(":")[-2] == str(line)


def test_rc_read_clean_and_allowed(rc):
    s = pv.load([rc], root="rc_f")
    # the value used, and a plain register's dropped
    assert s.run_test("rc_f::t_used")[1] == 0
    assert s.findings == []
    assert s.run_test("rc_f::t_dropped_allowed")[1] == 0


def test_rc_read_stream_consumer_alone():
    regs = {0: {"comp": "g", "path": "g.st", "offset": 0, "width": 32,
                "access": "READWRITE", "fields": [{"name": "done", "kind": "RC"}]}}
    san = sanitizers.Sanitizers(regs)
    assert san.access({"rw": "r", "addr": 0x10, "data": 1, "reg_id": 0}, 3) is None
    f = san.access({"rw": "r", "addr": 0x10, "data": 1, "reg_id": 0,
                    "discarded": True}, 4)
    assert (f.name, f.src_ref) == ("rc-read", 4)


UNBOUND = RC.replace("component rc_f", "component ub_f").replace("""\
    @pssunit_pkg::allow {.what = "rc-read"}
    @pssunit_pkg::test
    function void t_dropped_allowed() {
        place();
        regs.st.read_val();
    }
""", """\
    @pssunit_pkg::test
    function void t_unplaced() {
        bit[32] v;
        regs.ctl.write_val(1);
        v = regs.ctl.read_val();
    }
    @pssunit_pkg::allow {.what = "unbound"}
    @pssunit_pkg::test
    function void t_unplaced_allowed() {
        regs.ctl.write_val(1);
    }
""")


@pytest.fixture(scope="module")
def unbound(tmp_path_factory):
    f = tmp_path_factory.mktemp("ub") / "ub.pss"
    f.write_text(UNBOUND)
    return f


def test_unbound_catches_an_access_before_set_handle(unbound):
    # seeded bug: the test forgets place(); once per register, at the first access
    s = pv.load([unbound], root="ub_f")
    assert s.run_test("ub_f::t_unplaced")[1] == 1
    [f] = s.findings
    assert f.name == "unbound"
    assert "regs.ctl" in f.what and "set_handle" in f.what
    i = UNBOUND.index("regs.ctl.write_val(1);")
    line = UNBOUND[:i].count("\n") + 1
    assert f.where is not None and f.where.split(":")[-2] == str(line)


def test_unbound_clean_and_allowed(unbound):
    s = pv.load([unbound], root="ub_f")
    assert s.run_test("ub_f::t_used")[1] == 0
    assert s.findings == []
    # each test starts unplaced again
    assert s.run_test("ub_f::t_unplaced")[1] == 1
    assert s.run_test("ub_f::t_unplaced_allowed")[1] == 0


def test_unbound_stream_consumer_alone():
    regs = {0: {"comp": "g", "path": "g.ctl", "offset": 4, "width": 32,
                "access": "READWRITE", "fields": []}}
    san = sanitizers.Sanitizers(regs)
    assert san.access({"rw": "r", "addr": 0x1004, "data": 0, "reg_id": 0}, 3) is None
    f = san.access({"rw": "w", "addr": 4, "data": 0, "reg_id": 0}, 5)
    assert (f.name, f.src_ref) == ("unbound", 5)
    assert san.access({"rw": "r", "addr": 4, "data": 0, "reg_id": 0}, 6) is None


# -- unconsumed-irq (P2.6b) ---------------------------------------------------

IRQ = """\
import sync_pkg::*;
import pssunit_pkg::*;

import function void kick();

component irq_f : fixture_c {
    rt_c rt;
    channel_c<bit, 1> wake;
    function void notify_irq() {
        wake.try_put(1);
    }
    @pssunit_pkg::test
    function void t_kick() {
        kick();
    }
    @pssunit_pkg::allow {.what = "unconsumed-irq"}
    @pssunit_pkg::test
    function void t_kick_allowed() {
        kick();
    }
}
"""


def _kick() -> pv.Registry:
    r = pv.Registry()

    @r.implements("kick")
    def kick(ctx):
        ctx.raise_irq(3)
        ctx.raise_irq(3)        # one finding per interrupt number
    return r


@pytest.fixture(scope="module")
def irq(tmp_path_factory):
    f = tmp_path_factory.mktemp("irq") / "irq.pss"
    f.write_text(IRQ)
    return f


def test_unconsumed_irq_catches_an_irq_nothing_delivers(irq):
    # seeded bug: the run names no interrupt handler
    s = pv.load([irq], root="irq_f", use=[_kick()], irq_handler=None)
    assert s.run_test("irq_f::t_kick")[1] == 1
    [f] = s.findings
    assert f.name == "unconsumed-irq"
    assert "interrupt 3" in f.what and "irq_handler" in f.what
    line = IRQ[:IRQ.index("        kick();")].count("\n") + 1
    assert f.where is not None and f.where.split(":")[-2] == str(line)


def test_unconsumed_irq_clean_and_allowed(irq):
    s = pv.load([irq], root="irq_f", use=[_kick()])
    assert s.run_test("irq_f::t_kick")[1] == 0
    assert s.findings == []
    s = pv.load([irq], root="irq_f", use=[_kick()], irq_handler=None)
    assert s.run_test("irq_f::t_kick_allowed")[1] == 0
    assert s.run_test("irq_f::t_kick")[1] == 1        # fresh per test


def _irq_run(irq, engine, test, **kw):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev
    sink = MemorySink()
    s = pv.load([irq], root="irq_f", use=[_kick()], engine=engine, events=sink,
                watch=ev.EV_NOTIFY | ev.EV_IMPORT | ev.EV_SANITIZER | ev.EV_CHAN,
                **kw)
    counts = s.run_test(f"irq_f::{test}")[:2]
    trace = [(e.kind, {k: v for k, v in (e.detail or {}).items()
                       if k in ("irq", "delivered", "name", "what")})
             for e in sink.events]
    return counts, [(f.name, f.where, f.what) for f in s.findings], s.irqs, trace


@pytest.mark.parametrize("handler", [None, "notify_irq"])
@pytest.mark.parametrize("test", ["t_kick", "t_kick_allowed"])
def test_irqs_are_the_same_on_the_c_engine(irq, handler, test):
    # P4.8c3: delivered (the C engine starts the handler) or not (a finding)
    want = _irq_run(irq, "py", test, irq_handler=handler)
    got = _irq_run(irq, "c", test, irq_handler=handler)
    assert got == want
    assert any(k == "notify" for k, _ in want[3])
    # the handler ran (its try_put) only when there is one
    assert any(k == "chan" for k, _ in want[3]) == (handler is not None)
    assert want[0][1] == (1 if handler is None and test == "t_kick" else 0)


def test_unconsumed_irq_stream_consumer_alone():
    san = sanitizers.Sanitizers({})
    san.irq(1, delivered=True)
    san.irq(2, delivered=False)
    [f] = san.site(7)
    assert (f.name, f.src_ref) == ("unconsumed-irq", 7)
    assert san.site(8) == []
    san.irq(4, delivered=False)       # raised outside any access/import
    assert [g.name for g in san.end()] == ["unconsumed-irq"]


def _one(f, root, test, engine, plat=None, **kw):
    s = pv.load([f], root=root, platform=plat or pv.RegisterFake(),
                engine=engine, **kw)
    counts = s.run_test(f"{root}::{test}")[:2]
    return counts, [(x.name, x.where, x.what) for x in s.findings], s.stopped


@pytest.mark.parametrize("case", [
    ("suite", "s_f", "t_spin", {"poll_budget": 50}),
    ("suite", "s_f", "t_ok", {}),
    ("suite", "s_f", "t_rules", {}),
    ("suite", "s_f", "t_rules_allowed", {}),
    ("suite", "s_f", "t_hole", {}),
    ("latch", "l_f", "t_leaked", {}),
    ("latch", "l_f", "t_released", {}),
    ("rc", "rc_f", "t_dropped", {}),
    ("rc", "rc_f", "t_used", {}),
    ("unbound", "ub_f", "t_unplaced", {}),
])
def test_the_c_engine_finds_the_same(request, case):
    """P4.8c: on the C engine the sanitizers see the run's trace when it
    ends, and find what they find on the Python VM, located the same; a
    spin is stopped in the host and ends as the Python VM's does."""
    fixture, root, test, kw = case
    f = request.getfixturevalue(fixture)
    plat = Ready if test == "t_ok" else None
    got = [_one(f, root, test, e, plat and plat(), **kw) for e in ("py", "c")]
    assert got[0] == got[1]


def test_findings_are_sanitizer_events_on_c(suite):
    from zuspec.be.bc.trace import MemorySink, events as ev
    sink = MemorySink()
    s = pv.load([suite], root="s_f", platform=pv.RegisterFake(), events=sink,
                watch=ev.EV_SANITIZER, engine="c")
    s.run_test("s_f::t_rules")
    assert [e.detail["name"] for e in sink.events
            if e.kind == "sanitizer"] == ["access-rule", "access-rule"]
