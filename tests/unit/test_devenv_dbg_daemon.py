"""T2.3: a ``pssc dbg`` session lives in the workspace daemon, so it survives
across client processes. Every command runs ``python -m pssc dbg`` in its own
subprocess, with ``PSSC_NO_DAEMON`` unset and ``PSSC_DAEMON_DIR`` private, on a
copy of the WB DMA suite (its own workspace, so its daemon is not the repo's).
Stop records are schema-valid, ``--timeout`` bounds a run command, and two
sessions are independent. A DAP client (``pssc dap``, relayed to the daemon's
DAP socket) attaches to a session ``pssc dbg start`` made, and it and the CLI
both see the stops either one's run command reaches (P2.4d)."""

from __future__ import annotations

import io
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
jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
EXAMPLE = REPO / "examples" / "op_model"
TEST = "wb_dma_fx_c::t_routing_mask"
SCHEMA = json.loads((REPO / "docs/schemas/pssunit.stop-1.json").read_text())


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    root = tmp_path_factory.mktemp("dbgws")
    (root / "pyproject.toml").write_text("[project]\nname = 'ws'\n")
    for sub in ("pss", "tests"):
        shutil.copytree(EXAMPLE / sub, root / sub)
    run = tempfile.mkdtemp(prefix="pssd")   # a socket path is limited to ~107 bytes
    e = dict(os.environ)
    e.pop("PSSC_NO_DAEMON", None)
    e.update(PSSC_DAEMON_DIR=run, PYTHONPATH=str(SRC) + os.pathsep
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


def _line(ws, text, name="prio_a.pss"):
    return next(i for i, ln in enumerate((ws[0] / name).read_text().splitlines(), 1)
                if text in ln)


def _start(ws):
    doc = _dbgj(ws, "start", "@model.f", ".", TEST, "--seed", "1", "--platform", "fake")
    jsonschema.validate(doc, SCHEMA)
    assert (doc["event"], doc["reason"]) == ("stopped", "entry")
    return doc["session"]


def test_a_session_survives_across_client_processes(ws):
    sid = _start(ws)
    st = json.loads(_pssc(ws[1], ws[0], "daemon", "status", "--json").stdout)
    assert st["root"] == str(ws[0].parent.resolve())
    line = _line(ws, 'check_reg("dma.regs.int_msk_b"')
    bp = _dbgj(ws, "break", "add", f"prio_a.pss:{line}", "--session", sid)["breakpoint"]
    assert bp["verified"]
    doc = _dbgj(ws, "run", "--session", sid)
    jsonschema.validate(doc, SCHEMA)
    assert (doc["session"], doc["reason"]) == (sid, "breakpoint")
    assert doc["source"]["line"] == line
    assert _dbgj(ws, "bt", "--session", sid)["frames"][0]["source"]["file"] == "prio_a.pss"
    while (doc := _dbgj(ws, "continue", "--session", sid))["event"] == "stopped":
        jsonschema.validate(doc, SCHEMA)
    jsonschema.validate(doc, SCHEMA)
    assert doc["event"] == "exited" and doc["checks"]["failed"] == 0
    assert _dbgj(ws, "end", "--session", sid) == {"session": sid, "ended": True}
    p = _pssc(ws[1], ws[0], "dbg", "bt", "--session", sid)
    assert p.returncode == 1 and f"no session {sid}" in p.stderr


def test_timeout_and_concurrent_sessions(ws):
    a, b = _start(ws), _start(ws)
    assert a != b
    ids = {s["session"] for s in _dbgj(ws, "list")["sessions"]}
    assert {a, b} <= ids
    line = _line(ws, 'check_reg("dma.regs.int_msk_b"')
    _dbgj(ws, "break", "add", f"prio_a.pss:{line}", "--session", a)
    # A zero timeout returns at once: the run left going, or (if the engine
    # was quicker) its stop. Either way the next run command finds the stop.
    doc = _dbgj(ws, "run", "--timeout", "0", "--session", a)
    jsonschema.validate(doc, SCHEMA)
    assert doc["event"] in ("running", "stopped"), doc
    if doc["event"] == "running":
        assert doc["timeout"] == 0
        doc = _dbgj(ws, "run", "--session", a)
    assert (doc["reason"], doc["source"]["line"]) == ("breakpoint", line)
    # b has no breakpoint: it runs to the end while a stays where it stopped
    doc = _dbgj(ws, "run", "--session", b)
    assert doc["event"] == "exited" and doc["session"] == b
    assert _dbgj(ws, "bt", "--session", a)["frames"][0]["source"]["line"] == line
    for sid in (a, b):
        assert _dbgj(ws, "end", "--session", sid)["ended"]


class _Dap:
    """``pssc dap`` in a subprocess: the editor's end of the stdio relay."""

    def __init__(self, ws):
        from pssc.devenv.debug import dap
        self.dap = dap
        self.p = subprocess.Popen([sys.executable, "-m", "pssc", "dap"], cwd=ws[0],
                                  env=ws[1], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.seq = 0

    def send(self, cmd, **args):
        self.seq += 1
        self.dap.write_message(self.p.stdin, {"type": "request", "seq": self.seq,
                                              "command": cmd, "arguments": args})
        self.p.stdin.flush()

    def until(self, pred):
        """The next message ``pred`` accepts (the ones before it are dropped)."""
        while True:
            msg = self.dap.read_message(self.p.stdout)
            assert msg is not None, self.p.stderr.read().decode()
            if pred(msg):
                return msg

    def request(self, cmd, **args):
        self.send(cmd, **args)
        return self.until(lambda m: m.get("command") == cmd)

    def attach(self, sid):
        assert self.request("initialize", adapterID="pss")["success"]
        rep = self.request("attach", session=sid)
        assert rep["success"] and rep["body"] == {"session": sid}, rep
        self.send("configurationDone")
        assert self.until(lambda m: m.get("event") == "stopped")["body"]["reason"] == "entry"

    def close(self):
        if self.p.poll() is None:
            assert self.request("disconnect")["success"]
            self.p.stdin.close()
            assert self.p.wait(30) == 0

    def kill(self):
        if self.p.poll() is None:
            self.p.kill()


def test_pssc_dap_attaches_to_a_dbg_session_through_the_daemon(ws):
    sid = _start(ws)
    c = _Dap(ws)
    try:
        c.attach(sid)
        # the CLI and the DAP client share the session while both are on it
        assert _dbgj(ws, "threads", "--session", sid)["threads"]
        c.close()
    finally:
        c.kill()
    assert _dbgj(ws, "end", "--session", sid)["ended"]


def test_cli_and_dap_on_one_session_both_see_a_stop(ws):
    """P2.4d: a stop reached by a ``pssc dbg`` run command reaches the DAP
    client as ``continued`` then ``stopped``, at the same place; a DAP
    ``continue`` then runs the same session on, which the CLI sees."""
    sid = _start(ws)
    line = _line(ws, 'check_reg("dma.regs.int_msk_b"')
    c = _Dap(ws)
    try:
        c.attach(sid)
        _dbgj(ws, "break", "add", f"prio_a.pss:{line}", "--session", sid)
        doc = _dbgj(ws, "run", "--session", sid)
        assert (doc["reason"], doc["source"]["line"]) == ("breakpoint", line)
        ev = c.until(lambda m: m.get("type") == "event")
        assert ev["event"] == "continued" and ev["body"]["allThreadsContinued"], ev
        ev = c.until(lambda m: m.get("type") == "event")
        assert ev["event"] == "stopped" and ev["body"]["reason"] == "breakpoint", ev
        top = c.request("stackTrace", threadId=ev["body"]["threadId"])
        top = top["body"]["stackFrames"][0]
        assert (top["line"], top["source"]["name"]) == (line, "prio_a.pss")
        # the other way round: the DAP client runs on, the CLI sees the end
        assert c.request("continue", threadId=ev["body"]["threadId"])["success"]
        assert c.until(lambda m: m.get("event") == "exited")["body"] == {"exitCode": 0}
        p = _pssc(ws[1], ws[0], "dbg", "run", "--session", sid)
        assert p.returncode == 1 and "the run has ended" in p.stderr, p
        c.close()
    finally:
        c.kill()
    assert _dbgj(ws, "end", "--session", sid)["ended"]
