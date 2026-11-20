"""T2.2 (register watches): a register or field path -> the stub's access
watches, placed once the component's base is known, and stops on write,
read and value over the engine's stub. Runs on the WB DMA of
``examples/op_model``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.devenv.debug.watches import INERT, WatchSet, learn_bases, paths, resolve
from pssc.testing import bc_session, sources
import pssc.testing.vm as pv

OP_MODEL = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "pss"
BASE = 0x10000000
CH1 = BASE + 0x40       # regs.bank[1]: the device's base + 64


@pytest.fixture(scope="module")
def model():
    files = [str(OP_MODEL / re.sub(r"^src/pss/", "", ln.strip()))
             for ln in (OP_MODEL / "files.f").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    ctx = driver.translate([str(x) for x in sources.all_sources(files)])
    assert not ctx.errors
    return bc_session.lower(ctx, "wb_dma_c", functions=["*"], debug=True)


def _cfg(model, **vals):
    return [vals.get(n.split(".")[-1], 0)
            for n, _ in model.entries["wb_dma_ch_c::configure_channel"].params]


def test_resolve_a_register_and_a_field(model):
    rid, field, msg = resolve(model.registers, "ch[1].regs.csr")
    assert model.registers[rid]["path"] == "ch[1].regs.csr" and field is None and not msg
    rid2, field, _ = resolve(model.registers, "ch[1].regs.csr.err")
    assert rid2 == rid and field == "err"
    assert resolve(model.registers, "ch[1].regs.csr.nope")[2] == \
        "register ch[1].regs.csr has no field 'nope'"
    assert resolve(model.registers, "ch[9].regs.csr")[2] == "no register ch[9].regs.csr"


def test_a_watch_is_placed_when_its_base_is_known(model):
    ws = WatchSet(model)
    [w, f, bad, rw] = ws.set([{"path": "ch[1].regs.adr0"},
                              {"path": "ch[1].regs.csr.prio", "rw": "rw", "value": 5},
                              {"path": "ch[1].regs.bogus"},
                              {"path": "ch[1].regs.csr", "rw": "x"}])
    assert not w.verified and "first access to a register of ch[1]" in w.message
    assert not bad.verified and "no register" in bad.message
    assert not rw.verified and "expected r, w or rw" in rw.message
    assert ws.access_watches() == [INERT]
    ws.learn("ch[1]", CH1)
    assert w.verified and w.addr == CH1 + 8 and f.addr == CH1
    assert ws.access_watches() == [
        {"id": w.id, "addr": CH1 + 8, "size": 4, "rw": "w"},
        {"id": f.id, "addr": CH1, "size": 4, "rw": "rw",
         "value": 5 << 13, "mask": 7 << 13}]
    assert f.to_json()["field"] == "prio"


def test_paths_lists_registers_and_fields(model):
    ps = paths(model.registers, "ch[1].regs.sw")
    assert ps == ["ch[1].regs.swptr", "ch[1].regs.swptr.en", "ch[1].regs.swptr.ptr"]


def _start(model, calls):
    """A stub on a session that runs ``calls`` (``(instance, fn, args)``)
    with a register fake, and a watch set learning bases from it."""
    from zuspec.be.bc.interp.debug import Stub

    fake = pv.RegisterFake()
    s = pv.Session(model, platform=fake)
    s.call(s.construct(), "initialize", BASE)
    ws = WatchSet(model)
    learn_bases(s.vm, ws)     # the VM's memory and stub, not the session's

    def run():
        for inst, fn, args in calls:
            s.call(s.instance(inst), fn, *args)

    stub = Stub(s.vm, run=run)
    assert stub.request({"cmd": "attach", "args": {"version": 1}})["ok"]
    return stub, ws, fake


def _run(stub, ws, evaluate=None):
    """Continue to the end; each user stop's ``(ids, seen)``."""
    stops = []
    while True:
        assert stub.request({"cmd": "continue"})["ok"]
        ev = stub.next_event()
        if ev["event"] == "exited":
            assert ev["body"]["status"] == "ok", ev
            return stops
        body = ev["body"]
        assert body["reason"] == "watch", body
        ids, seen = ws.on_stop(body["hits"], body["detail"], evaluate)
        if ids:
            stops.append((ids, seen))


def test_a_write_watch_stops_on_the_first_access_too(model):
    # adr0 is configure_channel's first register write that places ch[1]
    calls = [("ch[1]", "configure_channel", _cfg(model, src=0x1234, dst=0x5678))]
    stub, ws, fake = _start(model, calls)
    [w] = ws.set([{"path": "ch[1].regs.adr0", "rw": "w"}])
    rep = ws.apply(stub)
    assert rep["ok"], rep
    stops = _run(stub, ws)
    assert [s[1][0]["value"] for s in stops] == [0x1234]
    assert w.verified and w.addr == fake.bases["ch[1]"] + 8 and w.hits == 1


def test_a_field_value_watch_stops_only_on_that_value(model):
    calls = [("ch[1]", "configure_channel", _cfg(model, prio=p)) for p in (1, 5, 5, 2)]
    stub, ws, _ = _start(model, calls)
    ws.set([{"path": "ch[1].regs.csr.prio", "value": 5}])
    ws.apply(stub)
    stops = _run(stub, ws)
    assert len(stops) == 2           # the two calls with prio 5
    assert all(s[1][0]["value"] == 5 and (s[1][0]["data"] >> 13) & 7 == 5 for s in stops)


def test_read_watch_condition_and_hit_count(model):
    calls = [("ch[1]", "configure_channel", _cfg(model))] + \
        [("ch[1]", "probe_status", [])] * 3
    stub, ws, _ = _start(model, calls)
    [r] = ws.set([{"path": "ch[1].regs.csr", "rw": "r", "hit_condition": ">=2"}])
    ws.apply(stub)
    stops = _run(stub, ws)
    assert r.hits == 3 and len(stops) == 2   # one read per probe; the 1st is skipped
    assert all(s[1][0]["rw"] == "r" for s in stops)

    stub, ws, _ = _start(model, calls)
    ws.set([{"path": "ch[1].regs.csr", "rw": "r", "condition": "x"}])
    ws.apply(stub)
    assert _run(stub, ws, lambda e: 0) == []


def test_an_alias_is_one_register(model):
    # ch[1].regs is the device's regs.bank[1]: once both bases are known a
    # watch on the bank sees the channel's writes
    calls = [("", "pause_engine", [1] * len(model.entries["wb_dma_c::pause_engine"].params)),
             ("ch[1]", "configure_channel", _cfg(model, src=0x77))]
    stub, ws, _ = _start(model, calls)
    ws.set([{"path": "regs.bank[1].adr0"}])
    ws.apply(stub)
    stops = _run(stub, ws)
    assert [s[1][0]["value"] for s in stops] == [0x77]
