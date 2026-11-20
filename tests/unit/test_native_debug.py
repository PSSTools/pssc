"""P4.5d: the ``pssc dbg`` session registry debugs a test function on the C
engine (``PSSC_ENGINE=c``): rt-eng's ``NativeStub`` behind the same
``DebugSession`` -- stop at the entry and at a breakpoint, frames, locals,
a step, and the run to its end -- on the WB DMA suite's fake platform."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")
pytest.importorskip("zuspec.rt.eng.debug")

from pssc.devenv.debug import sessions

SUITE = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "tests"
SRC = ["@model.f", "."]
TEST = "wb_dma_fx_c::t_routing_mask"


def _line(text, name="prio_a.pss"):
    return next(i for i, ln in enumerate((SUITE / name).read_text().splitlines(), 1)
                if text in ln)


@pytest.fixture
def reg(monkeypatch):
    monkeypatch.chdir(SUITE)
    monkeypatch.setenv("PSSC_ENGINE", "c")
    r = sessions.Registry()
    yield r
    r.end_all()


def _start(reg, test=TEST):
    return reg.start(SRC, test, seed=1, platform="fake")


def test_a_c_session_stops_steps_and_runs_to_its_end(reg):
    live = _start(reg)
    assert live.session.engine == "c"
    d = live.debug
    assert d.last["reason"] == "entry"
    line = _line('check_reg("dma.regs.int_msk_b"')
    [bp] = d.set_breakpoints("prio_a.pss", [{"line": line}])
    assert bp["verified"], bp
    rec = d.cont()
    assert rec["event"] == "stopped" and rec["breakpoints"] == [bp["id"]]
    assert rec["source"]["file"].endswith("prio_a.pss")
    assert rec["source"]["line"] == line
    assert live.to_json()["state"] == "stopped"
    [frame0] = d.frames()[:1]
    assert frame0["id"] == rec["frame"] and frame0["source"]["line"] == line
    names = [sc["name"] for sc in d.scopes()]
    assert "Locals" in names, names
    [loc] = [sc for sc in d.scopes() if sc["name"] == "Locals"]
    assert isinstance(d.vars(loc["ref"]), list)
    # the line's rewritten check is several statements (pcs), as on pyvm
    seen = []
    for _ in range(3):
        step = d.step("stmt")
        assert step["event"] == "stopped" and step["reason"] == "step"
        seen.append(step["source"]["line"])
    assert seen == [line, line, line + 1]
    end = d.cont()
    assert end["event"] == "exited" and end["status"] == "ok", end
    # the register fake is told C's accesses, so the checks count as on py
    assert live.error is None and live.checks == (2, 0, 0)


def test_end_mid_run_frees_the_c_session(reg):
    live = _start(reg)
    reg.end(live.id)
    assert live.id not in reg.sessions


def test_reload_during_a_c_run_is_staged(reg):
    """P4.6b: the run finishes on its own engine of N; N+1 applies at its end."""
    live = _start(reg)
    r = reg.reload(live.id)
    assert (r["old"], r["new"], r["applied"]) == (1, 2, False)
    assert live.session.generation == 1
    end = live.debug.cont()
    assert end["event"] == "exited", end
    assert live.session.generation == 2
