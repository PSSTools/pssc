"""T2.E, the Phase 2 exit check: an agent, using only ``pssc`` commands, finds
a seeded WB DMA bug (``configure_channel`` programs the wrong ``chk_sz``)
through a register watch. Every command runs ``python -m pssc`` in its own
subprocess against a copy of the companion example, through the copy's own
daemon (``PSSC_NO_DAEMON`` unset, ``PSSC_DAEMON_DIR`` private):

1. ``pssc test --json``: one test fails, at its ``chk_sz`` check;
2. ``pssc dbg start`` on that test, ``watch`` the field, ``run``: the stop
   is the write of ``sz`` in ``configure_channel``, the line after the
   mutated one, and ``locals`` there show ``sz.chk_sz`` != ``cfg.chk_sz``."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "op_model"
FUNC = "pss/wb_dma_ch_c/functions/configure_channel.pss"
GOOD = "sz.chk_sz = cfg.chk_sz;"
BAD = "sz.chk_sz = cfg.tot_sz;"


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    root = tmp_path_factory.mktemp("t2e")
    (root / "pyproject.toml").write_text("[project]\nname = 'ws'\n")
    for sub in ("pss", "tests"):
        shutil.copytree(EXAMPLE / sub, root / sub)
    func = root / FUNC
    text = func.read_text()
    assert text.count(GOOD) == 1
    func.write_text(text.replace(GOOD, BAD))
    run = tempfile.mkdtemp(prefix="pssd")   # a socket path is limited to ~107 bytes
    e = dict(os.environ)
    e.pop("PSSC_NO_DAEMON", None)
    e.update(PSSC_DAEMON_DIR=run, PYTHONPATH=str(REPO / "src") + os.pathsep
             + e.get("PYTHONPATH", ""))
    yield root / "tests", e
    p = _pssc(e, root / "tests", "daemon", "status", "--json")
    pid = (json.loads(p.stdout or "null") or {}).get("pid")
    _pssc(e, root / "tests", "daemon", "stop")
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    shutil.rmtree(run, ignore_errors=True)


def _pssc(env, cwd, *argv):
    return subprocess.run([sys.executable, "-m", "pssc", *argv], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=180)


def _dbgj(ws, *argv):
    cwd, env = ws
    p = _pssc(env, cwd, "dbg", *argv, "--json")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_an_agent_finds_the_seeded_chk_sz_bug_through_a_register_watch(ws):
    cwd, env = ws
    # 1. The suite: exactly one test fails, at the chk_sz check.
    p = _pssc(env, cwd, "test", "@model.f", ".", "--platform", "fake",
              "--seed", "1", "--json", "--no-ctrf")
    assert p.returncode == 1, p.stderr
    ends = [e for e in map(json.loads, p.stdout.splitlines())
            if e["event"] == "test.end"]
    failed = [e for e in ends if e["status"] == "fail"]
    assert len(ends) == 21 and [e["id"] for e in failed] \
        == ["wb_dma_routed_fx_c::hw_handshake_a"]
    (f,) = failed[0]["failures"]
    assert f["file"].endswith("hw_handshake_a.pss") and "chunk size" in f["message"]

    # 2. Debug that test: watch the field it checks, run to its write.
    sid = _dbgj(ws, "start", "@model.f", ".", failed[0]["id"], "--seed", "1",
                "--platform", "fake")["session"]
    w = _dbgj(ws, "watch", "dma.ch[2].regs.sz.chk_sz", "--session", sid)["watch"]
    assert w["path"] == "dma.ch[2].regs.sz.chk_sz"
    stop = _dbgj(ws, "run", "--session", sid)
    assert (stop["event"], stop["reason"]) == ("stopped", "watch")
    lines = (cwd.parent / FUNC).read_text().splitlines()
    bad = next(i for i, ln in enumerate(lines, 1) if BAD in ln)
    assert stop["source"]["file"].endswith("configure_channel.pss")
    assert stop["source"]["line"] == bad + 1          # regs.sz.write(sz)
    assert "regs.sz.write(sz)" in lines[bad]

    # 3. The frame's locals name the culprit: chk_sz is not the configured one.
    loc = json.dumps(_dbgj(ws, "locals", "--session", sid))
    assert _dbgj(ws, "eval", "sz.chk_sz", "--session", sid)["value"] == 64
    assert _dbgj(ws, "eval", "cfg.chk_sz", "--session", sid)["value"] == 16, loc
    assert _dbgj(ws, "end", "--session", sid)["ended"]
