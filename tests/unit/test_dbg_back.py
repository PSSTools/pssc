"""P5.2b: going back in a ``pssc dbg`` session -- ``back`` runs the same run
again (same image, seed and a fresh platform) to the statement before the
current one; ``rc`` (reverse-continue) to the last breakpoint or watch stop
before the current point. The WB DMA suite's test on the fake platform."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

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


@pytest.fixture
def live(reg):
    return reg.start(SRC, TEST, seed=1, platform="fake")


def _at(d, rec):
    """Where a stop is: file, line, tick, the stack's lines and the locals."""
    src = rec.get("source") or {}
    stack = [(f.get("source") or {}).get("line") for f in d.frames()]
    scopes = [s for s in d.scopes() if s["name"] == "Locals"]
    local = sorted((v["name"], v.get("value")) for s in scopes
                   for v in d.vars(s["ref"]))
    return (Path(src.get("file", "")).name, src.get("line"), rec.get("tick"),
            stack, local)


def test_back_returns_to_where_the_step_before_was(live):
    d = live.debug
    d.set_breakpoints("prio_a.pss", [{"line": _line("configure_interrupt_routing(WB_DMA_INT_B")}])
    rec = d.cont()
    assert rec["reason"] == "breakpoint"
    seen = [_at(d, rec)]
    for _ in range(3):
        rec = d.step("into")
        assert rec["event"] == "stopped", rec
        seen.append(_at(d, rec))
    assert len({s[2] for s in seen}) == 4          # four statement starts
    back = d.back()
    assert back["reason"] == "step" and back["detail"] == {"kind": "back"}
    assert _at(d, back) == seen[2]
    back = d.back()
    assert _at(d, back) == seen[1]
    # Forward again from there: the same statements as the first time.
    assert _at(d, d.step("into")) == seen[2]
    end = d.cont()
    assert end["event"] == "exited" and live.checks[1] == 0


def test_reverse_continue_goes_to_the_last_user_stop_before_here(live):
    d = live.debug
    a, b = _line('check_reg("dma.regs.int_msk_b"'), _line('check_written("dma')
    bps = d.set_breakpoints("prio_a.pss", [{"line": a}, {"line": b}])
    ra = d.cont()
    assert ra["source"]["line"] == a
    rb = d.cont()
    assert rb["source"]["line"] == b
    d.step("into")
    d.step("into")
    rec = d.reverse()
    assert rec["reason"] == "breakpoint" and rec["breakpoints"] == [bps[1]["id"]]
    assert (rec["source"]["line"], rec["tick"]) == (b, rb["tick"])
    # A hit counted on the way again: once each, as on the first run.
    assert [bp.hits for bp in d.breakpoints.all()] == [1, 1]
    rec = d.reverse()
    assert (rec["source"]["line"], rec["tick"]) == (a, ra["tick"])
    rec = d.reverse()
    assert rec["reason"] == "start"
    # From the start, forward: the first breakpoint again.
    assert d.cont()["source"]["line"] == a


def test_reverse_continue_to_the_last_write_of_a_register(live):
    d = live.debug
    d.set_breakpoints("prio_a.pss", [{"line": _line('check_written("dma')}])
    bp = d.cont()
    assert bp["reason"] == "breakpoint"
    rec = d.reverse(watch={"path": "dma.regs.int_msk_b", "rw": "w"})
    assert rec["reason"] == "watch" and rec["tick"] < bp["tick"], rec
    assert d.watches.watches == []                # the watch is gone again
    # Back lands at the start of the writing statement; with a watch, forward
    # stops at that write, then at the breakpoint: it was the last write.
    w = d.add_watch({"path": "dma.regs.int_msk_b", "rw": "w"})
    again = d.cont()
    assert (again["reason"], again["tick"]) == ("watch", rec["tick"]), again
    assert d.cont()["tick"] == bp["tick"]
    with pytest.raises(DebugError, match="no register"):
        d.reverse(watch={"path": "dma.regs.nope"})
    assert [x.id for x in d.watches.watches] == [w["id"]]


def test_back_needs_a_stopped_run_that_can_go_back(live):
    d = live.debug
    respawn, d.respawn = d.respawn, None
    with pytest.raises(DebugError, match="Python engine"):
        d.back()
    d.respawn = respawn
    d.cont()                           # to the end
    with pytest.raises(DebugError, match="ended"):
        d.back()


def test_dbg_back_and_rc_commands(reg, capsys):
    import json
    from pssc import cli

    def dbg(*argv):
        rc = cli.main(["dbg", *argv, "--json"])
        out = capsys.readouterr().out
        return rc, json.loads(out) if out.strip() else None

    from unittest import mock
    with mock.patch.object(sessions, "REGISTRY", reg):
        rc, doc = dbg("start", *SRC, TEST, "--seed", "1", "--platform", "fake")
        assert rc == 0, doc
        rc, _ = dbg("break", "add", f"prio_a.pss:{_line('check_reg(')}")
        assert rc == 0
        rc, first = dbg("continue")
        rc, nxt = dbg("next")
        assert nxt["tick"] > first["tick"]
        rc, doc = dbg("back")
        assert rc == 0 and doc["tick"] == first["tick"], doc
        assert doc["source"]["line"] == first["source"]["line"]
        rc, doc = dbg("rc")
        assert rc == 0 and doc["reason"] == "start", doc
