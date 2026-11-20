"""Debug lowering in the bytecode backend (P1.4): statement markers, calls
kept as CALLs, and named locals.

Debug lowering marks where each PSS statement starts in the image's LINE
table (the zbc format spec in the be-bc package, "Statement markers"); a statement
with no code gets a NOP. Release lowering writes no LINE table, and both run
the same.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources

MODEL = """\
component dev_c {
    function int sum(int n) {
        int acc;
        acc = 0;
        repeat (i : n) {
            if (i % 2 == 0) {
                acc += i;
            }
        }
        return acc;
    }
}
"""
#: line -> statement, for the lines of ``sum`` that hold one
STMT_LINES = [3, 4, 5, 6, 7, 10]


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    p = tmp_path_factory.mktemp("dbg") / "dbg.pss"
    p.write_text(MODEL)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors
    return c


def _lower(ctx, debug):
    return bc_session.lower(ctx, "dev_c", functions=["*"], debug=debug)


def _coro(model, name="dev_c::sum"):
    return model.coros[model.entries[name].coro]


def _kinds(model):
    from zuspec.be.bc.format import spec
    return [s.kind for s in model.to_container().sections], spec.sec_kind("ZBC_SEC_LINE")


def test_release_lowering_has_no_markers(ctx):
    m = _lower(ctx, False)
    assert all(not c.stmts for c in m.coros)
    kinds, line = _kinds(m)
    assert line not in kinds


def test_release_lowering_is_deterministic(ctx):
    assert _lower(ctx, False).to_bytes() == _lower(ctx, False).to_bytes()


def test_each_statement_has_its_own_pc(ctx):
    c = _coro(_lower(ctx, True))
    pcs = [pc for pc, _ in c.stmts]
    assert pcs == sorted(set(pcs))
    assert all(0 <= pc < len(c.code) for pc in pcs)


def test_markers_resolve_to_the_statement_lines(ctx):
    m = _lower(ctx, True)
    lines = sorted({m.prov.entries[sr].line for _, sr in _coro(m).stmts})
    assert lines == STMT_LINES


def test_the_line_table_survives_the_image(ctx):
    m = _lower(ctx, True)
    kinds, line = _kinds(m)
    assert line in kinds
    from zuspec.be.bc.model import ZbcModel
    back = ZbcModel.from_bytes(m.to_bytes())
    assert [c.stmts for c in back.coros] == [c.stmts for c in m.coros]


@pytest.mark.parametrize("n", [0, 1, 5])
def test_debug_and_release_compute_the_same(ctx, n):
    out = []
    for debug in (False, True):
        s = bc_session.Session.load(_lower(ctx, debug))
        out.append(s.call(s.construct(), "sum", n))
    assert out[0] == out[1] == sum(range(0, n, 2))


# --- P1.4b: in debug, a call is a CALL of the function's called form -------

CALLS = """\
struct pt_s {
    int x;
    int y;
}
component calls_c {
    function int twice(int v) {
        int r;
        r = v + v;
        return r;
    }
    function void bump(int v) {
        int t;
        t = v;
    }
    function int norm(pt_s p) {
        return p.x + p.y;
    }
    function int run(int n) {
        int acc;
        pt_s p;
        acc = twice(n);
        bump(acc);
        acc = acc + twice(1);
        p.x = acc;
        p.y = 1;
        return norm(p);
    }
}
"""


@pytest.fixture(scope="module")
def calls_ctx(tmp_path_factory):
    p = tmp_path_factory.mktemp("dbgcall") / "calls.pss"
    p.write_text(CALLS)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors
    return c


def _lower_calls(c, debug):
    return bc_session.lower(c, "calls_c", functions=["calls_c::run"], debug=debug)


def _called(model):
    """Called forms other than the entry's own."""
    entry = model.entries["calls_c::run"].coro
    return sorted(c.name for i, c in enumerate(model.coros)
                  if "$call" in c.name and i != entry)


def test_release_inlines_calls(calls_ctx):
    assert _called(_lower_calls(calls_ctx, False)) == []


def test_debug_has_one_called_form_per_function(calls_ctx):
    names = _called(_lower_calls(calls_ctx, True))
    # twice is called twice but has one form; norm takes a struct: inlined
    assert [n.split("$call")[0].split("::")[-1] for n in names] == ["bump", "twice"]


def test_called_forms_mark_their_statements(calls_ctx):
    m = _lower_calls(calls_ctx, True)
    twice = next(c for c in m.coros if "twice$call" in c.name)
    lines = sorted({m.prov.entries[sr].line for _, sr in twice.stmts})
    assert lines == [7, 8, 9]


@pytest.mark.parametrize("n", [0, 3])
def test_debug_calls_compute_the_same(calls_ctx, n):
    out = []
    for debug in (False, True):
        s = bc_session.Session.load(_lower_calls(calls_ctx, debug))
        out.append(s.call(s.construct(), "run", n))
    assert out[0] == out[1] == 2 * n + 2 + 1


# --- P1.4c: in debug, each PSS local is named in a side table (LOCAL) -------

def _locals(model, coro):
    return {(lv.name, lv.slot) for lv in coro.locals}


def test_release_names_no_locals(calls_ctx):
    m = _lower_calls(calls_ctx, False)
    assert all(not c.locals for c in m.coros)
    from zuspec.be.bc.format import spec
    assert spec.sec_kind("ZBC_SEC_LOCAL") not in [
        s.kind for s in m.to_container().sections]


def test_locals_are_named_by_their_pss_names(calls_ctx):
    m = _lower_calls(calls_ctx, True)
    run = m.coros[m.entries["calls_c::run"].coro]
    names = {lv.name for lv in run.locals}
    assert {"n", "acc", "p.x", "p.y"} <= names
    assert not any("$" in n for n in names)
    for lv in run.locals:
        assert 0 <= lv.pc_start <= lv.pc_end <= len(run.code)
        assert run.frame_locals[lv.slot].split("$")[0] == lv.name.split(".")[0]
    acc = next(lv for lv in run.locals if lv.name == "acc")
    assert (acc.width, acc.kind, acc.signed) == (32, "int", True)
    twice = next(c for c in m.coros if "twice$call" in c.name)
    assert {lv.name for lv in twice.locals} == {"v", "r"}


def test_the_locals_table_survives_the_image(calls_ctx):
    m = _lower_calls(calls_ctx, True)
    from zuspec.be.bc.model import ZbcModel
    back = ZbcModel.from_bytes(m.to_bytes())
    assert [c.locals for c in back.coros] == [c.locals for c in m.coros]
    assert any(c.locals for c in m.coros)


# --- P1.4d / T1.4: markers counted against ast2ir; frames named; recursion -


def _ir_stmts(stmts):
    """Every statement of an ast2ir body, nested ones included."""
    for s in stmts:
        yield s
        for v in vars(s).values():
            if isinstance(v, list) and v and type(v[0]).__name__.startswith("Stmt"):
                yield from _ir_stmts(v)


def _ir_function(c, comp, name):
    return next(f for f in c.type_map[comp].functions if f.name == name)


def test_one_marker_per_ast2ir_statement(ctx):
    stmts = list(_ir_stmts(_ir_function(ctx, "dev_c", "sum").body))
    m = _lower(ctx, True)
    marks = _coro(m).stmts
    assert len(marks) == len(stmts)
    assert sorted(m.prov.entries[sr].line for _, sr in marks) \
        == sorted(s.loc.line for s in stmts)


def test_called_forms_mark_each_ast2ir_statement_once(calls_ctx):
    m = _lower_calls(calls_ctx, True)
    for fn in ("twice", "bump"):
        stmts = list(_ir_stmts(_ir_function(calls_ctx, "calls_c", fn).body))
        form = next(c for c in m.coros if f"{fn}$call" in c.name)
        assert len(form.stmts) == len(stmts), fn


def test_frames_are_named(calls_ctx):
    m = _lower_calls(calls_ctx, True)
    assert all(c.name for c in m.coros)
    # a function's frame carries its PSS name (``<fn>$callN``)
    assert m.coros[m.entries["calls_c::run"].coro].name.split("$")[0] == "run"
    assert {c.name.split("$")[0] for c in m.coros if "$call" in c.name} \
        == {"run", "twice", "bump"}
    assert len({c.name for c in m.coros}) == len(m.coros)


RECURSE = """\
component rec_c {
    function int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
    function bool is_even(int n) { if (n == 0) { return true; } return is_odd(n - 1); }
    function bool is_odd(int n) { if (n == 0) { return false; } return is_even(n - 1); }
    function int fib(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }
}
"""


@pytest.fixture(scope="module")
def rec_ctx(tmp_path_factory):
    p = tmp_path_factory.mktemp("dbgrec") / "rec.pss"
    p.write_text(RECURSE)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors
    return c


@pytest.mark.parametrize("fn,arg,want", [
    ("fact", 5, 120), ("fact", 1, 1), ("fib", 10, 55),
    ("is_even", 10, 1), ("is_odd", 7, 1), ("is_even", 3, 0)])
def test_recursion_runs_under_debug(rec_ctx, fn, arg, want):
    for debug in (False, True):
        s = bc_session.Session.load(
            bc_session.lower(rec_ctx, "rec_c", functions=["*"], debug=debug))
        assert s.call(s.construct(), fn, arg) == want, debug


# --- P1.6a: debug events (stmt, call/return, test.start/end) ----------------

def test_events_follow_the_calls(calls_ctx):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    m = _lower_calls(calls_ctx, True)
    sink = MemorySink()
    s = bc_session.Session(m, events=sink, watch=ev.EV_CALL | ev.EV_STMT)
    assert s.call(s.construct(), "run", 3) == 9
    calls = [(e.kind, e.detail["fn"].split("$call")[0].split("::")[-1])
             for e in sink.events if e.kind != "stmt"]
    assert calls == [("call", "twice"), ("return", "twice"),
                     ("call", "bump"), ("return", "bump"),
                     ("call", "twice"), ("return", "twice")]
    lines = [m.prov.entries[e.src_ref].line for e in sink.events
             if e.kind == "stmt"]
    assert lines[:4] == [19, 20, 21, 7]   # acc, p, acc = twice(n); into twice
    rets = [e.detail.get("retval") for e in sink.events if e.kind == "return"]
    assert rets == [6, None, 2]


def test_test_events_bracket_a_run(calls_ctx):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    m = _lower_calls(calls_ctx, True)
    sink = MemorySink()
    s = bc_session.Session(m, events=sink, watch=ev.EV_TEST)
    s.construct()
    s.vm.host_event("test.start", {"name": "t"})
    assert s.call(s.instance(""), "run", 0) == 3
    s.vm.host_event("test.end", {"name": "t"})
    assert [e.kind for e in sink.events] == ["test.start", "test.end"]


def test_no_events_without_a_watch(calls_ctx):
    from zuspec.be.bc.trace import MemorySink

    sink = MemorySink()
    s = bc_session.Session(_lower_calls(calls_ctx, True), events=sink)
    assert s.call(s.construct(), "run", 3) == 9
    assert sink.events == []


# --- P1.6b: debug events (check, message) -----------------------------------

LEDGER = """\
import pssunit_pkg::*;
import std_pkg::*;
component led_f : fixture_c {
    rt_c rt;
    int cnt;
    @pssunit_pkg::test
    function void t_mix() {
        cnt = cnt + 3;
        rt.check_eq_int(cnt, 3, "cnt");
        rt.check_true(cnt == 4, "four");
        message(LOW, "cnt=%d", cnt);
    }
}
"""


@pytest.fixture(scope="module")
def ledger_model(tmp_path_factory):
    p = tmp_path_factory.mktemp("dbgledger") / "led.pss"
    p.write_text(LEDGER)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors
    return bc_session.lower(c, "led_f", functions=["*"], debug=True)


def test_ledger_checks_raise_check_events(ledger_model):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    m, sink = ledger_model, MemorySink()
    s = bc_session.Session(m, events=sink, watch=ev.EV_CHECK | ev.EV_MESSAGE,
                           out=lambda line: None)
    assert s.run_test("led_f::t_mix", seed=1) == (1, 1, 0)
    got = [(e.kind, m.prov.entries[e.src_ref].line, e.detail) for e in sink.events
           if e.kind != "message" or e.detail["text"].startswith("cnt")]
    assert got == [
        ("check", 9, {"verdict": "pass", "what": "cnt", "check": "check_eq_int"}),
        ("check", 10, {"verdict": "fail", "what": "four", "check": "check_true"}),
        ("message", 11, {"severity": "info", "level": 1, "text": "cnt=3"})]


def test_no_check_events_without_the_bit(ledger_model):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    sink = MemorySink()
    s = bc_session.Session(ledger_model, events=sink, watch=ev.EV_CALL,
                           out=lambda line: None)
    s.run_test("led_f::t_mix", seed=1)
    assert {e.kind for e in sink.events} == {"call", "return"}


# --- P1.6c: debug events (chan, notify, sanitizer) --------------------------

CHAN = """\
import pssunit_pkg::*;
import sync_pkg::*;
component ch_f : fixture_c {
    channel_c<bit[32]> ch;
    @pssunit_pkg::test
    function void t_chan() {
        bit[32] v;
        if (ch.try_put(5)) { }
        if (ch.try_put(6)) { }
        if (ch.try_get(v)) { }
        if (ch.try_get(v)) { }
        ch.put(v + 2);
        v = ch.get();
    }
}
"""


@pytest.fixture(scope="module")
def chan_ctx(tmp_path_factory):
    p = tmp_path_factory.mktemp("dbgchan") / "ch.pss"
    p.write_text(CHAN)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors
    return c


def test_channel_operations_raise_chan_events(chan_ctx):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    m, sink = bc_session.lower(chan_ctx, "ch_f", functions=["*"], debug=True), MemorySink()
    s = bc_session.Session(m, events=sink, watch=ev.EV_CHAN)
    s.run_test("ch_f::t_chan", seed=1)
    got = [(e.kind, m.prov.entries[e.src_ref].line, e.detail) for e in sink.events]
    assert got == [
        ("chan", 8, {"channel": "ch", "op": "try_put", "ok": True, "value": 5}),
        ("chan", 9, {"channel": "ch", "op": "try_put", "ok": False}),
        ("chan", 10, {"channel": "ch", "op": "try_get", "ok": True, "value": 5}),
        ("chan", 11, {"channel": "ch", "op": "try_get", "ok": False}),
        ("chan", 12, {"channel": "ch", "op": "put", "value": 7}),
        ("chan", 13, {"channel": "ch", "op": "get", "value": 7})]


def test_release_lowering_has_no_chan_markers(chan_ctx):
    m = bc_session.lower(chan_ctx, "ch_f", functions=["*"], debug=False)
    assert not any(c.chans for c in m.coros)


def test_host_raises_notify_and_sanitizer(chan_ctx):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    m, sink = bc_session.lower(chan_ctx, "ch_f", functions=["*"], debug=True), MemorySink()
    s = bc_session.Session(m, events=sink, watch=ev.EV_NOTIFY)
    s.construct()
    s.vm.host_event("notify", {"source": "irq0", "op": "raise"})
    s.vm.host_event("sanitizer", {"name": "inflight", "verdict": "fail", "what": "ch"})
    s.vm.host_event("test.start", {"id": "x"})
    assert [(e.kind, e.detail) for e in sink.events] == [
        ("notify", {"source": "irq0", "op": "raise"})]
