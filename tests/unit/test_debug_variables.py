"""T2.2 (variables): the scopes of a stopped frame (locals, action
attributes, component fields, Registers through the back door, Channels,
Bus) over the engine's stub. Runs on the WB DMA of ``examples/op_model``
and a small channel fixture."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.devenv.debug.variables import ChannelLog, Variables
from pssc.devenv.debug.watches import WatchSet
from pssc.testing import bc_session, sources
import pssc.testing.vm as pv

OP_MODEL = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "pss"
BASE = 0x10000000


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


def _stopped_in_configure(model, **cfg):
    """A stub stopped on configure_channel's write of ch[1]'s csr (after
    the channel's other registers), its frame id, and the fake."""
    from zuspec.be.bc.interp.debug import Stub

    fake = pv.RegisterFake()
    s = pv.Session(model, platform=fake)
    s.call(s.construct(), "initialize", BASE)
    s.call(s.instance("ch[1]"), "probe_status")     # ch[1]'s base is learnt

    def run():
        s.call(s.instance("ch[1]"), "configure_channel", *_cfg(model, **cfg))

    stub = Stub(s.vm, run=run)
    assert stub.request({"cmd": "attach", "args": {"version": 1}})["ok"]
    ws = WatchSet(model)
    ws.learn("ch[1]", fake.bases["ch[1]"])
    ws.set([{"path": "ch[1].regs.csr", "rw": "w"}])
    ws.apply(stub)
    assert stub.request({"cmd": "continue"})["ok"]
    ev = stub.next_event()
    assert ev["event"] == "stopped" and ev["body"]["reason"] == "watch", ev
    return stub, ev["body"]["frame"], fake


def _scope(v, frame, name):
    return {s["name"]: s["ref"] for s in v.scopes(frame)}[name]


def _by_name(vs):
    return {x["name"]: x for x in vs}


def test_scopes_of_a_component_function(model):
    stub, frame, fake = _stopped_in_configure(model, src=0x1234, prio=5)
    v = Variables.for_session(stub, model, fake)
    names = [s["name"] for s in v.scopes(frame)]
    assert names == ["Locals", "Component", "Registers", "Bus"]
    stub.request({"cmd": "terminate"})


def test_locals_group_a_struct_by_its_leaves(model):
    stub, frame, fake = _stopped_in_configure(model, src=0x1234, prio=5)
    v = Variables.for_session(stub, model, fake)
    [cfg] = v.variables(_scope(v, frame, "Locals"))
    assert cfg["name"] == "cfg" and cfg["ref"] > 0
    leaves = _by_name(v.variables(cfg["ref"]))
    assert leaves["src"]["int"] == 0x1234 and leaves["prio"]["value"] == "5"
    assert leaves["dst"]["int"] == 0
    stub.request({"cmd": "terminate"})


def test_component_fields_are_named_relative_to_it(model):
    stub, frame, fake = _stopped_in_configure(model)
    v = Variables.for_session(stub, model, fake)
    comp = v.variables(_scope(v, frame, "Component"))
    assert _by_name(comp)["chan"]["int"] == 1
    caps = _by_name(v.variables(_by_name(comp)["caps"]["ref"]))
    assert set(caps) >= {"present", "ars"}
    stub.request({"cmd": "terminate"})


def test_registers_are_read_through_the_back_door_and_decoded(model):
    stub, frame, fake = _stopped_in_configure(model, src=0x1234, prio=5)
    v = Variables.for_session(stub, model, fake)
    bus, log = len(fake.bus), list(fake.log)
    regs = _by_name(v.variables(_scope(v, frame, "Registers")))
    # the frame is ch[1]'s: its registers only, named relative to it
    assert all(n.startswith("regs.") for n in regs)
    assert regs["regs.adr0"]["int"] == 0x1234
    assert regs["regs.adr0"]["value"] == "0x1234"
    # the back door reads what the fake holds, side-effect free
    assert regs["regs.csr"]["int"] == fake.peek_reg("ch[1].regs.csr")
    sz = _by_name(v.variables(regs["regs.sz"]["ref"]))
    assert set(sz) >= {"tot_sz", "chk_sz"}
    assert (fake.bus, fake.log) == (fake.bus[:bus], log)    # no bus access
    stub.request({"cmd": "continue"})
    assert stub.next_event()["event"] == "exited"
    assert fake.peek_field("ch[1].regs.csr", "prio") == 5


def test_a_register_with_no_known_base_has_no_value(model):
    stub, frame, fake = _stopped_in_configure(model)
    v = Variables(stub, model.registers, bases={})
    regs = _by_name(v.variables(_scope(v, frame, "Registers")))
    assert regs["regs.csr"]["value"] == "<address unknown>"
    assert regs["regs.csr"]["ref"] == 0 and "int" not in regs["regs.csr"]
    stub.request({"cmd": "terminate"})


def test_bus_shows_the_last_n_accesses_newest_first(model):
    stub, frame, fake = _stopped_in_configure(model, src=0x1234)
    v = Variables.for_session(stub, model, fake, bus_n=3)
    bus = v.variables(_scope(v, frame, "Bus"))
    n = len(fake.bus)
    assert [b["name"] for b in bus] == [f"[{i}]" for i in (n - 1, n - 2, n - 3)]
    assert bus[0]["value"].startswith("write ch[1].regs.csr = ")   # the watched write
    assert "configure_channel.pss:" in bus[0]["value"]
    fields = _by_name(v.variables(bus[0]["ref"]))
    assert "prio" in fields
    stub.request({"cmd": "terminate"})


def test_references_are_dropped_on_invalidate(model):
    stub, frame, fake = _stopped_in_configure(model)
    v = Variables.for_session(stub, model, fake)
    ref = _scope(v, frame, "Locals")
    v.invalidate()
    with pytest.raises(KeyError):
        v.variables(ref)
    stub.request({"cmd": "terminate"})


def test_action_attributes_from_the_frame_object(model):
    from zuspec.be.bc.interp import Obj
    from zuspec.be.bc.interp.debug import Stub

    s = pv.Session(model, platform=pv.RegisterFake())
    s._new_vm()
    coro = model.entries["wb_dma_ch_c::set_software_pointer_a"].coro
    root = s.vm.root_frame(coro, seed=0, obj=Obj(["ptr", "en"], [0x40, 1]))
    stub = Stub(s.vm, root)
    assert stub.request({"cmd": "attach", "args": {"version": 1}})["ok"]
    th = stub.request({"cmd": "threads"})["body"]["threads"]
    tid = next(t["id"] for t in th if t["coro"] == coro)
    [f] = stub.request({"cmd": "frames", "args": {"thread": tid}})["body"]["frames"]
    v = Variables(stub)
    attrs = v.variables(_scope(v, f["id"], "Action"))
    assert [(a["name"], a["int"]) for a in attrs] == [("ptr", 0x40), ("en", 1)]
    stub.request({"cmd": "terminate"})


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
        ch.put(v + 2);
    }
}
"""


def test_channels_from_chan_events(tmp_path):
    from zuspec.be.bc.trace import events as ev

    p = tmp_path / "ch.pss"
    p.write_text(CHAN)
    ctx = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not ctx.errors
    m = bc_session.lower(ctx, "ch_f", functions=["*"], debug=True)
    log = ChannelLog()
    s = bc_session.Session(m, events=log, watch=ev.EV_CHAN)
    s.run_test("ch_f::t_chan", seed=1)
    assert log.channels["ch"] == {"puts": 2, "gets": 1, "failed": 1, "last": {
        "channel": "ch", "op": "put", "value": 7}}
    v = Variables(None, channels=log)
    [ch] = v._chans()
    assert ch["name"] == "ch" and ch["value"] == "1 queued, last put 7"
    assert _by_name(v.variables(ch["ref"]))["queued"]["int"] == 1
