"""P2.4a-b: the DAP server's framing and a client's requests over a socket
pair -- ``initialize``, ``launch`` (one test of the WB DMA suite on the fake
platform), ``attach`` to a session already started, ``configurationDone``,
``threads``, ``disconnect`` (a); breakpoints, data and exception
breakpoints, the stack, scopes, variables, ``evaluate``, ``continue``, steps
and ``pause``, with their events (b); ``pssc dap`` on stdin/stdout (c2);
the custom ``pssc/reload`` request, *Apply change* (P3.6c3)."""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.devenv.debug import dap, sessions
from pssc.devenv.debug.frontend import DebugError

SUITE = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "tests"
SRC = ["@model.f", "."]
TEST = "wb_dma_fx_c::t_routing_mask"


def test_framing_round_trips_and_ends_at_eof():
    buf = io.BytesIO()
    dap.write_message(buf, {"type": "event", "event": "output", "body": {"output": "é"}})
    dap.write_message(buf, {"type": "request", "seq": 2, "command": "threads"})
    assert buf.getvalue().startswith(b"Content-Length: ")
    buf.seek(0)
    assert dap.read_message(buf)["body"]["output"] == "é"
    assert dap.read_message(buf)["command"] == "threads"
    assert dap.read_message(buf) is None


class Client:
    """The editor's end of a socket pair; the server runs in a thread."""

    def __init__(self, reg):
        a, b = socket.socketpair()
        self.sock = a
        self.r, self.w = a.makefile("rb"), a.makefile("wb")
        self.conn = dap.Connection(b.makefile("rb"), b.makefile("wb"), registry=reg)
        self.thread = threading.Thread(target=self.conn.serve, daemon=True)
        self.thread.start()
        self.seq = 0
        self.events = []

    def request(self, command, **arguments):
        self.seq += 1
        dap.write_message(self.w, {"type": "request", "seq": self.seq,
                                   "command": command, "arguments": arguments})
        while True:
            msg = dap.read_message(self.r)
            assert msg is not None, "server closed"
            if msg["type"] == "event":
                self.events.append(msg)
            elif msg["request_seq"] == self.seq:
                return msg

    def wait_event(self, name, timeout=30.0):
        """The next ``name`` event (earlier ones already read count first)."""
        for i, e in enumerate(self.events):
            if e["event"] == name:
                return self.events.pop(i)
        self.sock.settimeout(timeout)
        while True:
            msg = dap.read_message(self.r)
            assert msg is not None, "server closed"
            if msg["type"] == "event" and msg["event"] == name:
                return msg
            self.events.append(msg)

    def close(self):
        self.sock.close()


@pytest.fixture
def reg():
    r = sessions.Registry()
    yield r
    r.end_all()


def _launch(c, **kw):
    rep = c.request("initialize", adapterID="pss")
    assert rep["success"] and rep["body"]["supportsConfigurationDoneRequest"]
    c.wait_event("initialized")
    args = dict(cwd=str(SUITE), sources=SRC, test=TEST, seed=1, platform="fake")
    args.update(kw)
    return c.request("launch", **args)


def test_launch_stops_on_entry_and_disconnect_ends_the_session(reg):
    c = Client(reg)
    rep = _launch(c, stopOnEntry=True)
    assert rep["success"], rep
    sid = rep["body"]["session"]
    assert list(reg.sessions) == [sid]
    assert c.request("configurationDone")["success"]
    ev = c.wait_event("stopped")["body"]
    assert ev["reason"] == "entry" and ev["allThreadsStopped"]
    threads = c.request("threads")["body"]["threads"]
    assert threads and ev["threadId"] in [t["id"] for t in threads]
    assert all(set(t) == {"id", "name"} for t in threads)
    assert c.request("disconnect")["success"]
    c.thread.join(5)
    assert not c.thread.is_alive() and reg.sessions == {}
    c.close()


def test_launch_without_stop_on_entry_runs_to_the_end(reg):
    c = Client(reg)
    assert _launch(c)["success"]
    assert c.request("configurationDone")["success"]
    assert c.wait_event("exited")["body"] == {"exitCode": 0}
    c.wait_event("terminated")
    c.request("disconnect")
    c.close()


def test_attach_joins_a_session_and_disconnect_leaves_it(reg, monkeypatch):
    monkeypatch.chdir(SUITE)
    live = reg.start(SRC, TEST, seed=1, platform="fake")
    c = Client(reg)
    c.request("initialize")
    rep = c.request("attach", session=live.id)
    assert rep["success"] and rep["body"] == {"session": live.id}
    c.request("configurationDone")
    assert c.wait_event("stopped")["body"]["reason"] == "entry"
    c.request("disconnect")
    assert reg.get(live.id) is live and live.debug.attached
    c.close()


def test_errors_are_failed_responses(reg):
    c = Client(reg)
    c.request("initialize")
    rep = c.request("threads")
    assert not rep["success"] and "no debug session" in rep["message"]
    rep = c.request("launch", cwd=str(SUITE), sources=SRC, test="no_such_test*",
                    platform="fake")
    assert not rep["success"] and "not one" in rep["message"], rep
    assert not c.request("attach", session="9")["success"]
    rep = c.request("frobnicate")
    assert not rep["success"] and "unsupported" in rep["message"]
    assert reg.sessions == {}
    c.request("disconnect")
    c.close()


def _line(text, name="prio_a.pss"):
    return next(i for i, ln in enumerate((SUITE / name).read_text().splitlines(), 1)
                if text in ln)


def _started(reg, **kw):
    """A client launched with stopOnEntry, stopped at the entry."""
    c = Client(reg)
    assert _launch(c, stopOnEntry=True, **kw)["success"]
    return c


def _go(c):
    """configurationDone, then the stop it reports."""
    assert c.request("configurationDone")["success"]
    return c.wait_event("stopped")["body"]


def test_breakpoint_stack_scopes_variables_evaluate(reg):
    c = _started(reg)
    line = _line('check_reg("dma.regs.int_msk_b"')
    path = str(SUITE / "prio_a.pss")
    rep = c.request("setBreakpoints", source={"path": path},
                    breakpoints=[{"line": line, "condition": "1 < 2"},
                                 {"line": line + 1, "hitCondition": "> 5"}])
    bps = rep["body"]["breakpoints"]
    assert rep["success"] and bps[0]["verified"] and bps[0]["line"] == line
    assert bps[0]["source"]["path"] == path
    _go(c)                                       # entry
    assert c.request("continue")["body"]["allThreadsContinued"]
    ev = c.wait_event("stopped")["body"]
    assert ev["reason"] == "breakpoint" and ev["hitBreakpointIds"] == [bps[0]["id"]]
    st = c.request("stackTrace", threadId=ev["threadId"])["body"]
    top = st["stackFrames"][0]
    assert st["totalFrames"] >= 1 and top["line"] == line
    assert top["source"]["path"] == path and top["source"]["name"] == "prio_a.pss"
    assert len(c.request("stackTrace", threadId=ev["threadId"],
                         levels=1)["body"]["stackFrames"]) == 1
    scopes = c.request("scopes", frameId=top["id"])["body"]["scopes"]
    assert scopes[0]["name"] == "Locals"
    names = [s["name"] for s in scopes]
    regs = scopes[names.index("Registers")]["variablesReference"]
    vs = c.request("variables", variablesReference=regs)["body"]["variables"]
    assert vs and all({"name", "value", "variablesReference"} <= set(v) for v in vs)
    rep = c.request("evaluate", expression="1 + 2", frameId=top["id"])
    assert rep["body"]["result"] == "3"
    rep = c.request("evaluate", expression="1 +", frameId=top["id"])
    assert not rep["success"]
    c.request("continue")
    assert c.wait_event("exited")["body"] == {"exitCode": 0}
    c.request("disconnect")
    c.close()


def test_logpoint_writes_output_and_goes_on(reg):
    c = _started(reg)
    line = _line('check_reg("dma.regs.int_msk_b"')
    c.request("setBreakpoints", source={"path": str(SUITE / "prio_a.pss")},
              breakpoints=[{"line": line, "logMessage": "at {1 + 1}"}])
    _go(c)
    c.request("continue")
    out = c.wait_event("output")["body"]
    assert out["output"] == "at 2\n"
    assert c.wait_event("exited")["body"]["exitCode"] == 0
    c.request("disconnect")
    c.close()


def test_data_breakpoint_on_a_register(reg):
    c = _started(reg)
    info = c.request("dataBreakpointInfo", name="dma.regs.int_msk_b")["body"]
    assert info["dataId"] == "dma.regs.int_msk_b" and "write" in info["accessTypes"]
    bad = c.request("dataBreakpointInfo", name="dma.regs.nope")["body"]
    assert bad["dataId"] is None and "no register" in bad["description"]
    rep = c.request("setDataBreakpoints", breakpoints=[
        {"dataId": info["dataId"], "accessType": "write"}])
    assert rep["success"] and rep["body"]["breakpoints"][0]["id"] == 1
    _go(c)
    c.request("continue")
    assert c.wait_event("stopped")["body"]["reason"] == "data breakpoint"
    c.request("disconnect")
    c.close()


def test_data_breakpoint_info_names_relative_to_the_variables_shown(reg):
    # VS Code's "Break on Value Change" sends the name as shown plus the
    # reference it is shown under: a register relative to the frame's
    # component, a field by its own name under its register.
    c = _started(reg)
    line = _line('check_reg("dma.regs.int_msk_b"')
    c.request("setBreakpoints", source={"path": str(SUITE / "prio_a.pss")},
              breakpoints=[{"line": line}])
    _go(c)
    c.request("continue")
    ev = c.wait_event("stopped")["body"]
    top = c.request("stackTrace", threadId=ev["threadId"])["body"]["stackFrames"][0]
    scopes = c.request("scopes", frameId=top["id"])["body"]["scopes"]
    regs = next(s["variablesReference"] for s in scopes if s["name"] == "Registers")
    shown = c.request("variables", variablesReference=regs)["body"]["variables"]
    r = next(v for v in shown if v["name"].endswith("int_msk_b"))
    info = c.request("dataBreakpointInfo", name=r["name"],
                     variablesReference=regs)["body"]
    assert info["dataId"] == "dma.regs.int_msk_b"
    if r["variablesReference"]:
        f = c.request("variables", variablesReference=r["variablesReference"]
                      )["body"]["variables"][0]
        info = c.request("dataBreakpointInfo", name=f["name"],
                         variablesReference=r["variablesReference"])["body"]
        assert info["dataId"] == "dma.regs.int_msk_b." + f["name"]
    c.request("disconnect")
    c.close()


def test_steps_and_exception_filters(reg):
    c = _started(reg)
    rep = c.request("setExceptionBreakpoints", filters=["error", "check", "bogus"])
    assert [b["verified"] for b in rep["body"]["breakpoints"]] == [True, True, False]
    caps = c.request("initialize")["body"]["exceptionBreakpointFilters"]
    assert {f["filter"] for f in caps} == {"error", "check", "sanitizer"}
    line = _line('check_reg("dma.regs.int_msk_b"')
    c.request("setBreakpoints", source={"path": str(SUITE / "prio_a.pss")},
              breakpoints=[{"line": line}])
    _go(c)
    c.request("continue")
    ev = c.wait_event("stopped")["body"]
    depth = c.request("stackTrace", threadId=ev["threadId"])["body"]["totalFrames"]
    assert c.request("stepIn", threadId=ev["threadId"])["success"]
    ev = c.wait_event("stopped")["body"]
    assert ev["reason"] == "step"
    inner = c.request("stackTrace", threadId=ev["threadId"])["body"]["totalFrames"]
    assert inner >= depth
    for _ in range(10):         # a check is lowered as several statements
        assert c.request("next", threadId=ev["threadId"])["success"]
        ev = c.wait_event("stopped")["body"]
        assert ev["reason"] == "step"
        top = c.request("stackTrace", threadId=ev["threadId"])["body"]["stackFrames"][0]
        if top["line"] != line:
            break
    assert top["line"] == line + 1
    assert c.request("pause", threadId=ev["threadId"])["success"]   # stopped: no-op
    assert c.request("stepOut", threadId=ev["threadId"])["success"]  # the outermost
    assert c.wait_event("exited")["body"]["exitCode"] == 0
    c.request("disconnect")
    c.close()


def test_step_back_and_reverse_continue(reg):
    c = _started(reg)
    assert c.request("initialize")["body"]["supportsStepBack"]
    line = _line('check_reg("dma.regs.int_msk_b"')
    c.request("setBreakpoints", source={"path": str(SUITE / "prio_a.pss")},
              breakpoints=[{"line": line}])
    _go(c)
    c.request("continue")
    ev = c.wait_event("stopped")["body"]

    def top():
        return c.request("stackTrace", threadId=ev["threadId"])["body"]["stackFrames"][0]
    at = []
    for _ in range(2):
        at.append(top())
        assert c.request("next", threadId=ev["threadId"])["success"]
        ev = c.wait_event("stopped")["body"]
    assert c.request("stepBack", threadId=ev["threadId"])["success"]
    ev = c.wait_event("stopped")["body"]
    assert ev["reason"] == "step"
    assert (top()["line"], top()["column"]) == (at[1]["line"], at[1]["column"])
    assert c.request("reverseContinue", threadId=ev["threadId"])["success"]
    ev = c.wait_event("stopped")["body"]
    assert ev["reason"] == "breakpoint" and top()["line"] == line
    assert c.request("reverseContinue", threadId=ev["threadId"])["success"]
    assert c.wait_event("stopped")["body"]["reason"] == "entry"     # the start
    # A session that cannot go back refuses the request itself.
    d = next(iter(reg.sessions.values())).debug
    d.respawn = None
    rep = c.request("stepBack", threadId=ev["threadId"])
    assert not rep["success"] and "Python engine" in rep["message"]
    c.request("disconnect")
    c.close()


def test_pause_stops_a_running_run(reg):
    c = _started(reg)
    live = next(iter(reg.sessions.values()))
    _go(c)
    stub = live.debug.stub
    real, gate = stub.request, threading.Event()

    def request(req):
        # hold ``continue`` until the pause comes, and pause as the stub's
        # own test does (so the stop is not racy)
        if req["cmd"] == "stop":
            with stub._cv:
                stub._pause = True
            gate.set()
            return {"ok": True, "body": {}}
        if req["cmd"] == "continue":
            gate.wait(10)
        return real(req)
    stub.request = request
    assert c.request("continue")["success"]
    rep = c.request("next", threadId=0)
    assert not rep["success"] and "pause first" in rep["message"]
    assert c.request("pause", threadId=0)["success"]
    assert c.wait_event("stopped")["body"]["reason"] == "pause"
    c.request("disconnect")
    c.close()


def test_every_client_sees_a_stop_whoever_ran(reg):
    a = _started(reg)
    live = next(iter(reg.sessions.values()))
    b = Client(reg)
    b.request("initialize")
    assert b.request("attach", session=live.id)["success"]
    _go(a)                                       # a's entry
    line = _line('check_reg("dma.regs.int_msk_b"')
    bp = a.request("setBreakpoints", source={"path": str(SUITE / "prio_a.pss")},
                   breakpoints=[{"line": line}])["body"]["breakpoints"][0]
    a.request("continue")                        # a's run: b hears of it too
    assert a.wait_event("stopped")["body"]["hitBreakpointIds"] == [bp["id"]]
    assert b.wait_event("continued")["body"]["allThreadsContinued"]
    ev = b.wait_event("stopped")["body"]
    assert ev["reason"] == "breakpoint" and ev["hitBreakpointIds"] == [bp["id"]]
    a.request("threads")                         # a's own stop came once
    assert not [e for e in a.events if e["event"] == "stopped"]
    a.events.clear()
    rec = live.debug.step("stmt")                # as `pssc dbg step` does
    assert rec["event"] == "stopped"
    for c in (a, b):                             # both clients see it
        assert c.wait_event("continued")["body"]["threadId"] == ev["threadId"]
        assert c.wait_event("stopped")["body"]["reason"] == "step"
    b.request("continue")
    for c in (a, b):
        assert c.wait_event("exited")["body"]["exitCode"] == 0
        c.wait_event("terminated")
    assert b.request("disconnect", terminateDebuggee=False)["success"]
    assert live.debug._listeners == [a.conn._on_record]
    a.request("disconnect")
    assert live.debug._listeners == []
    a.close()
    b.close()


def test_one_waiter_at_a_time(reg):
    c = _started(reg)
    live = next(iter(reg.sessions.values()))
    _go(c)
    stub = live.debug.stub
    real, gate = stub.request, threading.Event()

    def request(req):
        if req["cmd"] == "continue":
            gate.wait(10)
        return real(req)
    stub.request = request
    assert c.request("continue")["success"]
    for _ in range(100):                         # c's run thread holds the wait
        if live.debug.waiting:
            break
        threading.Event().wait(0.01)
    with pytest.raises(DebugError, match="another client"):
        live.debug.cont()
    gate.set()
    assert c.wait_event("exited")["body"]["exitCode"] == 0
    c.request("disconnect")
    c.close()


def _messages(data: bytes):
    r, out = io.BytesIO(data), []
    while (msg := dap.read_message(r)) is not None:
        out.append(msg)
    return out


def test_pssc_dap_serves_stdin_stdout_in_process():
    """With PSSC_NO_DAEMON=1 ``pssc dap`` is the server itself."""
    w = io.BytesIO()
    for seq, cmd, args in ((1, "initialize", {"adapterID": "pss"}),
                           (2, "launch", {"cwd": str(SUITE), "sources": SRC,
                                          "test": TEST, "seed": 1,
                                          "platform": "fake", "stopOnEntry": True}),
                           (3, "configurationDone", {}), (4, "disconnect", {})):
        dap.write_message(w, {"type": "request", "seq": seq, "command": cmd,
                              "arguments": args})
    env = dict(os.environ, PSSC_NO_DAEMON="1", PYTHONPATH=str(SUITE.parents[2] / "src")
               + os.pathsep + os.environ.get("PYTHONPATH", ""))
    p = subprocess.run([sys.executable, "-m", "pssc", "dap"], input=w.getvalue(),
                       capture_output=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr.decode()
    msgs = _messages(p.stdout)
    reps = {m["command"]: m for m in msgs if m["type"] == "response"}
    assert set(reps) == {"initialize", "launch", "configurationDone", "disconnect"}
    assert all(r["success"] for r in reps.values()), reps
    events = [m["event"] for m in msgs if m["type"] == "event"]
    assert "initialized" in events and "stopped" in events


# --- P3.6c3: *Apply change* (``pssc/reload``) ------------------------------

RL_DUT = """\
import function bit[32] plat_id();
component dut_c {
    function bit[32] id() { return plat_id() + %s; }
}
"""

RL_TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    rt_c rt;
    @pssunit_pkg::test
    function void t_a() { rt.check_eq_int(dut.id(), 0x5A, "id"); }
}
"""

RL_DOUBLES = """\
import pssc.testing.vm as pv

@pv.implements("plat_id")
def plat_id(ctx):
    return %s
"""


@pytest.fixture
def rl_project(tmp_path, monkeypatch):
    (tmp_path / "dut.pss").write_text(RL_DUT % "0")
    (tmp_path / "t.pss").write_text(RL_TESTS)
    (tmp_path / "rl_dap_doubles.py").write_text(RL_DOUBLES % "0x59")   # wrong
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "rl_dap_doubles", raising=False)
    return tmp_path


def _rl_started(reg, root):
    c = Client(reg)
    assert c.request("initialize")["body"]["pssc"]["requests"] == ["pssc/reload"]
    rep = c.request("launch", cwd=str(root), sources=["."], test="tests_c::t_a",
                    seed=1, platform="py:rl_dap_doubles", stopOnEntry=True)
    assert rep["success"], rep
    assert _go(c)["reason"] == "entry"
    return c


def test_apply_change_fixes_a_failing_test_without_a_restart(reg, rl_project):
    c = _rl_started(reg, rl_project)
    (rl_project / "rl_dap_doubles.py").write_text(RL_DOUBLES % "0x5A")   # the fix
    (rl_project / "dut.pss").write_text(RL_DUT % "0 + 0")
    rep = c.request("pssc/reload")
    assert rep["success"], rep
    r = rep["body"]
    assert (r["old"], r["new"], r["applied"]) == (1, 2, False)
    assert r["reimported"] == ["rl_dap_doubles"]
    assert "staged" in c.wait_event("output")["body"]["output"]
    assert c.request("continue")["success"]
    assert c.wait_event("exited")["body"] == {"exitCode": 0}
    live = reg.get()
    assert live.session.generation == 2           # applied when the run ended
    rep = c.request("pssc/reload")                # no run going: applied at once
    assert rep["body"]["applied"] and (rep["body"]["old"], rep["body"]["new"]) == (2, 3)
    assert c.wait_event("invalidated")["body"] == {"areas": ["all"]}
    c.request("disconnect")
    c.close()


def test_apply_change_refused_says_why(reg, rl_project):
    c = _rl_started(reg, rl_project)
    (rl_project / "dut.pss").write_text(
        RL_DUT.replace("plat_id();", "plat_id(int x);").replace("plat_id() +", "plat_id(1) +")
        % "0")
    rep = c.request("pssc/reload")
    assert not rep["success"]
    err = rep["body"]["error"]
    assert err["reason"] == "api" and err["changes"][0]["symbol"] == "import:plat_id", rep
    assert reg.get().session.generation == 1
    c.request("disconnect")
    c.close()
