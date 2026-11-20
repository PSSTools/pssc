"""P2.3b: the ``pssc dbg`` session registry -- a DebugSession on one test of
the WB DMA suite (``examples/op_model/tests``), lowered for debugging and run
on the fake platform, by id; a run command bounded by a timeout; sessions
ended one by one or all at once when the daemon exits."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.devenv import daemon
from pssc.devenv.debug import sessions
from pssc.devenv.debug.frontend import DebugError

SUITE = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "tests"
SRC = ["@model.f", "."]
TEST = "wb_dma_fx_c::t_routing_mask"


def _line(text, name="prio_a.pss"):
    return next(i for i, ln in enumerate((SUITE / name).read_text().splitlines(), 1)
                if text in ln)


@pytest.fixture
def reg(monkeypatch):
    monkeypatch.chdir(SUITE)
    r = sessions.Registry()
    yield r
    r.end_all()


def _start(reg, test=TEST, **kw):
    return reg.start(SRC, test, seed=1, platform="fake", **kw)


def test_a_session_stops_at_a_breakpoint_in_its_test(reg):
    live = _start(reg)
    assert live.id == "1" and reg.latest == "1"
    assert live.debug.last["reason"] == "entry"
    line = _line('check_reg("dma.regs.int_msk_b"')
    [bp] = live.debug.set_breakpoints("prio_a.pss", [{"line": line}])
    assert bp["verified"], bp
    rec = reg.get().debug.cont()
    assert rec["event"] == "stopped" and rec["breakpoints"] == [bp["id"]]
    assert rec["source"]["file"].endswith("prio_a.pss")
    assert rec["source"]["line"] == line
    assert live.to_json()["state"] == "stopped"
    end = live.debug.cont()
    assert end["event"] == "exited", end
    assert live.error is None and live.checks[0] >= 2 and live.checks[1] == 0


def test_a_run_command_that_times_out_leaves_the_run_going(reg):
    live = _start(reg)
    d = live.debug
    line = _line('check_written("dma.regs.int_msk_b"')
    d.set_breakpoints("prio_a.pss", [{"line": line}])
    # The first wait sees no event within its timeout, whatever the speed.
    real, calls = d.stub.next_event, []

    def slow(timeout):
        calls.append(timeout)
        return None if len(calls) == 1 else real(timeout)
    d.stub.next_event = slow
    rec = d.cont(timeout=0.25)
    assert rec == {"event": "running", "timeout": 0.25, "output": []}
    assert d.running
    with pytest.raises(DebugError):
        d.step()
    rec = d.cont()                     # waits again; sends no second continue
    assert rec["event"] == "stopped" and rec["source"]["line"] == line


def test_sessions_are_independent_and_end_by_id(reg):
    a = _start(reg)
    b = _start(reg, test="wb_dma_fx_c::t_csr_prio_bits")
    assert (a.id, b.id, reg.latest) == ("1", "2", "2")
    assert [s["test"] for s in reg.list()] == [TEST, "wb_dma_fx_c::t_csr_prio_bits"]
    assert reg.get("1") is a and reg.get() is b
    assert a.debug.cont()["event"] == "exited"
    assert b.debug.last["reason"] == "entry"     # untouched by a's run
    reg.end("2")
    assert reg.latest == "1" and not b.debug.attached
    with pytest.raises(DebugError, match="no session 2"):
        reg.get("2")
    reg.end()
    with pytest.raises(DebugError, match="no debug session"):
        reg.get()


def test_a_selection_of_other_than_one_test_is_refused(reg):
    with pytest.raises(DebugError, match="selects 2 tests"):
        _start(reg, test="*::t_routing_mask")
    assert not reg.sessions


def test_the_daemon_ends_its_sessions_when_it_exits(reg, monkeypatch):
    monkeypatch.setattr(sessions, "REGISTRY", reg)
    live = _start(reg)
    live.debug.set_breakpoints("prio_a.pss", [{"line": _line('check_reg("dma.regs.int_msk_b"')}])
    assert live.debug.cont()["event"] == "stopped"
    daemon._end_dbg_sessions()
    assert not reg.sessions and live.debug.stub.state == "exited"


# --- P2.3c: `pssc dbg` ------------------------------------------------------

def _dbg(capsys, *argv):
    from pssc import cli
    rc = cli.main(["dbg", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def _dbgj(capsys, *argv):
    import json
    rc, out, err = _dbg(capsys, *argv, "--json")
    assert rc == 0, err
    return json.loads(out)


@pytest.fixture
def cli_reg(reg, monkeypatch):
    monkeypatch.setattr(sessions, "REGISTRY", reg)
    return reg


def _validate(doc):
    jsonschema = pytest.importorskip("jsonschema")
    import json
    schema = json.loads((SUITE.parents[2] / "docs/schemas/pssunit.stop-1.json").read_text())
    jsonschema.validate(doc, schema)


def test_dbg_start_break_run_inspect_end(cli_reg, capsys):
    doc = _dbgj(capsys, "start", "@model.f", ".", TEST, "--seed", "1",
                "--platform", "fake")
    _validate(doc)
    assert (doc["session"], doc["event"], doc["reason"]) == ("1", "stopped", "entry")
    assert (doc["test"], doc["seed"]) == (TEST, 1)
    line = _line('check_reg("dma.regs.int_msk_b"')
    bp = _dbgj(capsys, "break", "add", f"prio_a.pss:{line}")["breakpoint"]
    assert bp["verified"] and bp["id"] == 1
    bp2 = _dbgj(capsys, "break", "add", f"prio_a.pss:{line + 1}")["breakpoint"]
    assert [b["id"] for b in _dbgj(capsys, "break", "list")["breakpoints"]] == [1, bp2["id"]]
    doc = _dbgj(capsys, "run")
    _validate(doc)
    assert doc["reason"] == "breakpoint" and doc["source"]["line"] == line
    frames = _dbgj(capsys, "bt")["frames"]
    assert frames[0]["source"]["file"] == "prio_a.pss"
    scopes = _dbgj(capsys, "locals")["scopes"]
    assert scopes[0]["name"] == "Locals"
    assert isinstance(_dbgj(capsys, "eval", "1 + 2")["value"], int)
    assert _dbgj(capsys, "eval", "1 + 2")["value"] == 3
    regs = _dbgj(capsys, "regs", "dma.regs.int_msk_b")["registers"]
    assert [r["name"] for r in regs] == ["dma.regs.int_msk_b"]
    bus = _dbgj(capsys, "bus", "-n", "2")["bus"]
    assert 1 <= len(bus) <= 2 and bus[0]["value"].startswith("write ")
    assert _dbgj(capsys, "threads")["threads"]
    rc, out, _ = _dbg(capsys, "list")
    assert rc == 0 and f"1  {TEST}  seed 1  stopped" in out
    # to the end: the two breakpoints, then exited with the checks counted
    while (doc := _dbgj(capsys, "continue"))["event"] == "stopped":
        pass
    _validate(doc)
    assert doc["event"] == "exited" and doc["checks"]["failed"] == 0
    assert _dbgj(capsys, "end") == {"session": "1", "ended": True}
    rc, _, err = _dbg(capsys, "bt")
    assert rc == 1 and "no debug session" in err


def test_dbg_text_output_and_errors(cli_reg, capsys):
    rc, out, _ = _dbg(capsys, "start", "@model.f", ".", TEST, "--seed", "1",
                      "--platform", "fake")
    assert rc == 0 and out.startswith("session 1: stopped (entry)")
    rc, _, err = _dbg(capsys, "break", "add", "prio_a.pss")
    assert rc == 1 and "expected FILE:LINE" in err
    rc, out, _ = _dbg(capsys, "break", "add", "nowhere.pss:3")
    assert rc == 0 and "not set" in out
    rc, out, _ = _dbg(capsys, "watch", "dma.regs.int_msk_b")
    assert rc == 0 and out.startswith("watch 1 dma.regs.int_msk_b (w)")
    rc, out, _ = _dbg(capsys, "run")
    assert rc == 0 and "stopped (watch) at" in out
    rc, _, err = _dbg(capsys, "start", "@model.f", ".", "-k", "t_routing_mask")
    assert rc == 1 and "selects 2 tests" in err
    rc, _, err = _dbg(capsys, "start", "@model.f", ".")
    assert rc == 1 and "one ID" in err
    rc, _, err = _dbg(capsys, "run", "--session", "9")
    assert rc == 1 and "no session 9" in err


def test_dbg_run_timeout_says_running(cli_reg, capsys):
    _dbgj(capsys, "start", "@model.f", ".", TEST, "--seed", "1", "--platform", "fake")
    d = cli_reg.get().debug
    real, calls = d.stub.next_event, []

    def slow(timeout):
        calls.append(timeout)
        return None if len(calls) == 1 else real(timeout)
    d.stub.next_event = slow
    doc = _dbgj(capsys, "run", "--timeout", "0.25")
    _validate(doc)
    assert (doc["event"], doc["timeout"]) == ("running", 0.25)
    assert _dbgj(capsys, "run")["event"] == "exited"


def test_dbg_is_served_by_the_daemon():
    from pssc.devenv import protocol
    assert "dbg" in protocol.SERVED
