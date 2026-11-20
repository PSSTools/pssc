"""T2.2 (front end): a DebugSession over pyvm's debug stub on the WB DMA of
``examples/op_model`` -- conditional and logpoint breakpoints filtered by the
session, register watches whose base is learned during the run, variables
and eval at the user's stop, stepping and ending a run."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.devenv.debug.frontend import DebugError, DebugSession
from pssc.testing import bc_session, sources
import pssc.testing.vm as pv

OP_MODEL = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "pss"
CC = "configure_channel.pss"
CC_PATH = OP_MODEL / "wb_dma_ch_c" / "functions" / CC
BASE = 0x10000000


def _line(text):
    return next(i for i, ln in enumerate(CC_PATH.read_text().splitlines(), 1)
                if text in ln)


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


def _session(model, prios=(1, 5, 2), probe=True):
    """A session (not started) on configure_channel of ch[1] once per prio
    (src = 0x100 + prio); ``probe``: probe_status first, so the platform
    knows ch[1]'s base before the session starts."""
    from zuspec.be.bc.interp.debug import Stub

    fake = pv.RegisterFake()
    s = pv.Session(model, platform=fake)
    s.call(s.construct(), "initialize", BASE)
    if probe:
        s.call(s.instance("ch[1]"), "probe_status")

    def run():
        for p in prios:
            s.call(s.instance("ch[1]"), "configure_channel",
                   *_cfg(model, src=0x100 + p, prio=p))

    return DebugSession(Stub(s.vm, run=run), model, platform=fake), fake


@pytest.fixture
def session(model):
    d, _ = _session(model)
    yield d
    d.end()


def _stops(d):
    """Continue to the end: the user stops, and the exit record."""
    out = []
    while True:
        rec = d.cont()
        if rec["event"] == "exited":
            return out, rec
        out.append(rec)


def test_a_conditional_breakpoint_stops_where_it_holds(session):
    line = _line("csr.prio         = cfg.prio;")
    [bp] = session.set_breakpoints(CC, [{"line": line, "condition": "cfg.prio == 5"}])
    assert bp["verified"]
    session.start()
    stops, end = _stops(session)
    assert end["status"] == "ok", end
    [s] = stops
    assert s["reason"] == "breakpoint" and s["breakpoints"] == [bp["id"]]
    assert s["source"]["file"].endswith(CC) and s["source"]["line"] == line
    assert session.breakpoints.by_id(bp["id"]).hits == 1


def test_eval_and_variables_at_the_stop(model):
    d, _ = _session(model)
    d.set_breakpoints(CC, [{"line": _line("csr.prio         = cfg.prio;"),
                            "condition": "cfg.prio == 5"}])
    d.start()
    s = d.cont()
    assert s["event"] == "stopped"
    assert d.evaluate("cfg.src") == 0x105
    assert d.evaluate("cfg.prio * 2 + chan") == 10 + d.evaluate("chan")
    names = [sc["name"] for sc in d.scopes()]
    assert "Locals" in names and "Registers" in names, names
    [loc] = [sc for sc in d.scopes() if sc["name"] == "Locals"]
    assert d.vars(loc["ref"])
    [frame0] = d.frames()[:1]
    assert frame0["id"] == s["frame"] and frame0["source"]["line"] == s["source"]["line"]
    assert d.evaluate("cfg.prio", frame=frame0["id"]) == 5
    d.cont()                                   # the run moved on: refs are gone
    with pytest.raises(KeyError):
        d.vars(loc["ref"])
    d.end()


def test_logpoints_write_output_and_do_not_stop(session):
    session.set_breakpoints(CC, [{"line": _line("csr.prio         = cfg.prio;"),
                                  "log_message": "prio={cfg.prio} src={cfg.src}"}])
    session.start()
    stops, end = _stops(session)
    assert stops == []
    assert [o["output"] for o in end["output"]] == [
        "prio=1 src=257", "prio=5 src=261", "prio=2 src=258"]
    assert len(session.take_output()) == 3 and session.take_output() == []


def test_a_field_value_watch_stops_on_that_value(session):
    [w] = session.set_watches([{"path": "ch[1].regs.csr.prio", "value": 2}])
    assert w["verified"]                       # base known from the platform
    session.start()
    s = session.cont()
    assert s["reason"] == "watch" and s["watches"][0]["value"] == 2
    assert session.evaluate("cfg.prio") == 2
    assert session.evaluate("regs.csr.prio") == 2      # written: back door sees it
    stops, _ = _stops(session)
    assert stops == []


def test_a_watch_learns_its_base_during_the_run(model):
    d, fake = _session(model, prios=(3, 4), probe=False)
    [w] = d.set_watches([{"path": "ch[1].regs.adr0", "rw": "w"}])
    assert not w["verified"]
    d.start()
    stops, end = _stops(d)
    assert end["status"] == "ok"
    # the first write places ch[1]; the watch catches the second call's
    assert [s["watches"][0]["value"] for s in stops][-1] == 0x104
    assert d.watches.watches[0].addr == fake.bases["ch[1]"] + 8
    d.end()


def test_step_moves_to_the_next_statement(session):
    line = _line("regs.adr0.write_val(cfg.src);")
    session.set_breakpoints(CC, [{"line": line}])
    session.start()
    s = session.cont()
    assert s["source"]["line"] == line
    n = session.step("stmt")
    assert n["event"] == "stopped" and n["reason"] == "step"
    assert n["source"]["line"] > line


def test_end_mid_run_and_errors(model):
    d, _ = _session(model)
    with pytest.raises(DebugError):
        d.evaluate("cfg.prio")                 # not started
    d.set_breakpoints(CC, [{"line": _line("regs.adr0.write_val(cfg.src);")}])
    d.start()
    assert d.cont()["event"] == "stopped"
    d.end()
    assert not d.attached and d.stub.state == "exited"


def test_stop_records_match_the_stop_schema(model):
    """Every record a run command returns, with the `pssc dbg` envelope, is a
    valid pssunit.stop/1 (docs/schemas/pssunit.stop-1.json)."""
    import json
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).resolve().parents[2] / "docs" / "schemas"
                         / "pssunit.stop-1.json").read_text())

    def check(rec):
        jsonschema.validate({"schema": "pssunit.stop/1", "session": "1", **rec}, schema)

    d, _ = _session(model)
    line = _line("regs.adr0.write_val(cfg.src);")
    d.set_breakpoints(CC, [{"line": line, "condition": "cfg.prio == 5"},
                           {"line": _line("csr.prio         = cfg.prio;"),
                            "log_message": "prio={cfg.prio}"}])
    d.set_watches([{"path": "ch[1].regs.csr.prio", "value": 2}])
    d.start()
    check(d.last)
    recs = [d.cont(), d.step("stmt")]
    while recs[-1]["event"] != "exited":
        recs.append(d.cont())
    assert {r.get("reason") for r in recs} >= {"breakpoint", "step", "watch"}
    assert recs[-1]["output"] or any(r["output"] for r in recs)
    for r in recs:
        check(r)
    check({"event": "running", "timeout": 2.5, "output": []})
    with pytest.raises(jsonschema.ValidationError):
        check({"event": "stopped"})            # no reason
    d.end()
