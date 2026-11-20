"""P2.4d, T2.4: a DAP transcript test. An editor's session -- initialize,
launch, a breakpoint, configurationDone, the stop, the stack, scopes and
variables, continue to the end, disconnect -- is played against the server
and every message it sends is compared with the recorded one, volatile fields
(``seq``, the session id, absolute paths) masked. Responses must come in
request order; events in their own order, each after the request that caused
it. (CLI and DAP on one daemon session both seeing a stop: the last test of
test_devenv_dbg_daemon.py.)"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.devenv.debug import dap, sessions

SUITE = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "tests"
SRC = ["@model.f", "."]
TEST = "wb_dma_fx_c::t_routing_mask"
LINE = next(i for i, ln in enumerate((SUITE / "prio_a.pss").read_text().splitlines(), 1)
            if 'check_reg("dma.regs.int_msk_b"' in ln)
PATH = str(SUITE / "prio_a.pss")
SOURCE = {"path": "<SUITE>/prio_a.pss", "name": "prio_a.pss"}

CAPS = {
    "supportsConfigurationDoneRequest": True,
    "supportTerminateDebuggee": True,
    "supportsConditionalBreakpoints": True,
    "supportsHitConditionalBreakpoints": True,
    "supportsLogPoints": True,
    "supportsDataBreakpoints": True,
    "supportsEvaluateForHovers": True,
    "supportsStepBack": True,
    "exceptionBreakpointFilters": [
        {"filter": "error", "label": "Errors", "default": True},
        {"filter": "check", "label": "Failed checks", "default": False},
        {"filter": "sanitizer", "label": "Sanitizer findings", "default": False},
    ],
    "pssc": {"requests": ["pssc/reload"]},
}

# (command, arguments, expected response body; None = no body). Arguments
# naming ids are filled from earlier responses (see _play).
SCRIPT = [
    ("initialize", {"adapterID": "pss"}, CAPS),
    ("launch", {"cwd": str(SUITE), "sources": SRC, "test": TEST, "seed": 1,
                "platform": "fake"}, {"session": "<SID>"}),
    ("setBreakpoints", {"source": {"path": PATH}, "breakpoints": [{"line": LINE}]},
     {"breakpoints": [{"id": 1, "verified": True, "line": LINE, "source": SOURCE}]}),
    ("configurationDone", {}, None),
    ("stackTrace", {"threadId": "<TID>"},
     {"stackFrames": [{"id": 0, "name": "t_routing_mask$call9", "line": LINE,
                       "column": 9, "source": SOURCE}], "totalFrames": 1}),
    ("scopes", {"frameId": 0}, {"scopes": [
        {"name": n, "variablesReference": i, "expensive": False}
        for i, n in enumerate(["Locals", "Component", "Registers", "Bus"], 1)]}),
    ("variables", {"variablesReference": 1}, {"variables": []}),
    ("variables", {"variablesReference": 3}, "<REGISTERS>"),
    ("continue", {"threadId": "<TID>"}, {"allThreadsContinued": True}),
    ("disconnect", {}, None),
]

# (event, body, the command whose request it must follow)
EVENTS = [
    ("initialized", None, "launch"),
    ("stopped", {"reason": "breakpoint", "threadId": "<TID>",
                 "allThreadsStopped": True, "hitBreakpointIds": [1]},
     "configurationDone"),
    ("exited", {"exitCode": 0}, "continue"),
    ("terminated", None, "continue"),
]


def _mask(obj, sid):
    if isinstance(obj, dict):
        return {k: ("<SID>" if k == "session" and v == sid else _mask(v, sid))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v, sid) for v in obj]
    if isinstance(obj, str) and obj.startswith(str(SUITE)):
        return "<SUITE>" + obj[len(str(SUITE)):]
    return obj


def _registers(body):
    """The Registers scope is ~70 entries: check its shape and a few values."""
    vs = body["variables"]
    by = {v["name"]: v for v in vs}
    assert [v["name"] for v in vs] == sorted(by)
    assert all(set(v) == {"name", "value", "variablesReference"} for v in vs)
    assert by["dma.regs.int_msk_b"]["value"] == "0x5"     # written before the line
    assert by["dma.regs.csr"]["value"] == "0x0"
    assert by["dma.regs.csr"]["variablesReference"] > 0   # has fields
    assert by["dma.ch[0].regs.csr"] == {"name": "dma.ch[0].regs.csr",
                                       "value": "<address unknown>",
                                       "variablesReference": 0}


def _play(reg):
    """Run SCRIPT; return every message the server sent, in order, and the
    index in that list after which each request was sent."""
    a, b = socket.socketpair()
    a.settimeout(30)
    r, w = a.makefile("rb"), a.makefile("wb")
    conn = dap.Connection(b.makefile("rb"), b.makefile("wb"), registry=reg)
    t = threading.Thread(target=conn.serve, daemon=True)
    t.start()
    got, sent_at, tid = [], {}, None
    for seq, (cmd, args, _) in enumerate(SCRIPT, 1):
        if cmd == "disconnect":                 # the run must have ended first
            while not any(m.get("event") == "terminated" for m in got):
                got.append(dap.read_message(r))
        args = {k: (tid if v == "<TID>" else v) for k, v in args.items()}
        sent_at[cmd] = len(got)
        dap.write_message(w, {"type": "request", "seq": seq, "command": cmd,
                              "arguments": args})
        while True:
            msg = dap.read_message(r)
            assert msg is not None, "server closed"
            got.append(msg)
            if msg.get("event") == "stopped":
                tid = msg["body"]["threadId"]
            if msg["type"] == "response" and msg["request_seq"] == seq:
                break
        if cmd == "configurationDone":
            while tid is None:
                msg = dap.read_message(r)
                got.append(msg)
                if msg.get("event") == "stopped":
                    tid = msg["body"]["threadId"]
    t.join(10)
    assert not t.is_alive(), "server did not end after disconnect"
    a.close()
    return got, sent_at, tid


@pytest.fixture
def reg():
    r = sessions.Registry()
    yield r
    r.end_all()


def test_transcript(reg):
    got, sent_at, tid = _play(reg)
    assert [m["seq"] for m in got] == list(range(1, len(got) + 1))
    sid = next(m for m in got if m.get("command") == "launch")["body"]["session"]
    got = [_mask(m, sid) for m in got]

    responses = [m for m in got if m["type"] == "response"]
    assert [(m["request_seq"], m["command"]) for m in responses] == \
        [(i, c) for i, (c, _, _) in enumerate(SCRIPT, 1)]
    for m, (cmd, _, body) in zip(responses, SCRIPT):
        assert m["success"], m
        assert set(m) - {"body"} == {"type", "seq", "request_seq", "command", "success"}
        if body == "<REGISTERS>":
            _registers(m["body"])
        elif body is None:
            assert "body" not in m, m
        else:
            assert m["body"] == body, cmd

    events = [(i, m) for i, m in enumerate(got) if m["type"] == "event"]
    assert [m["event"] for _, m in events] == [e for e, _, _ in EVENTS]
    for (i, m), (name, body, after) in zip(events, EVENTS):
        if body is None:
            assert "body" not in m, m
        else:
            assert m["body"] == {k: (tid if v == "<TID>" else v)
                                 for k, v in body.items()}, name
        assert i >= sent_at[after], f"{name} came before {after} was sent"
    assert reg.sessions == {}                  # disconnect ended the launch
