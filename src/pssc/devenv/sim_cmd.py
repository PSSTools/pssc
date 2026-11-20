"""`pssc sim`: server-mode simulations (plan P3.5).

A testbench run with ``+pssvm_serve[=NAME]`` registers with the workspace
daemon and waits for work; these commands go to it through the daemon:
``list`` the sessions, ``run`` tests in one (the simulation keeps running
between runs), ``log`` its events, ``reload`` a rebuilt image into it (hot
reload, plan P3.6c: the next run is on the new code), ``end`` it (the
simulation finishes), ``attach`` a debugger to it (plan P3.7: a ``pssc dbg``
session whose breakpoints stop the calls SV makes, and with them sim time),
``resume`` a test held after it failed (``+pssvm_dbg_on_fail``, plan P3.7b).
``--session`` names one by id or name; with one session it may be left out.

``--json`` prints one JSON object (not versioned; see docs/simulation.rst).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

def add_parser(sub) -> None:
    """Register `pssc sim` on the top-level subparsers *sub*."""
    sm = sub.add_parser("sim", help="server-mode simulations "
                        "(`+pssvm_serve`) registered with the workspace daemon")
    _add_commands(sm)
    sm.set_defaults(func=lambda a: _run(a, None))


def _add_commands(sm) -> None:
    ssub = sm.add_subparsers(dest="sim_cmd", metavar="COMMAND")
    ssub.required = True

    def cmd(name, help, *, session=True):
        p = ssub.add_parser(name, help=help)
        if session:
            p.add_argument("--session", metavar="ID",
                           help="the session, by id or name (default: the "
                           "only one)")
        p.add_argument("--json", action="store_true",
                       help="print one JSON object")
        return p

    cmd("list", "list the simulations waiting for work", session=False)
    r = cmd("run", "run the selected tests in a simulation and wait for them")
    r.add_argument("sel", nargs="*", metavar="SEL",
                   help="test name or glob, as for `+pssunit=` (none: every "
                   "test)")
    r.add_argument("--timeout", type=float, metavar="SECONDS",
                   help="give up waiting after this long (default: wait)")
    lg = cmd("log", "show a simulation's events (oldest kept first)")
    lg.add_argument("--since", type=int, default=0, metavar="N",
                    help="only events numbered above N")
    rl = cmd("reload", "hot reload: make a rebuilt image (`pssc build --image`) "
             "the simulation's next generation, between runs")
    rl.add_argument("image", nargs="?", metavar="IMAGE",
                    help="the image (default: the one the simulation loaded, "
                    "rebuilt in place)")
    rl.add_argument("--strict", action="store_true",
                    help="refuse a change to the component tree")
    rl.add_argument("--cancel", action="store_true",
                    help="end a running call first")
    rl.add_argument("--timeout", type=float, metavar="SECONDS",
                    help="give up waiting after this long (default: 600; the "
                    "simulation answers once a run ends)")
    cmd("attach", "debug a simulation: a `pssc dbg` session on its VM (not "
        "stopped; set breakpoints, then `pssc dbg continue` waits for a stop)")
    rs = cmd("resume", "go on from a test held by `+pssvm_dbg_on_fail` (the "
             "simulation is held after it failed; `held` in `pssc sim log`)")
    rs.add_argument("--rerun", action="store_true",
                    help="run the failed test again first (after a `pssc sim "
                    "reload`, say)")
    cmd("end", "end a simulation (its testbench finishes)")


def main(argv: List[str], path: Optional[str] = None) -> int:
    """``pssc sim ARGV``; *path* is the daemon's command socket (default:
    the workspace's)."""
    ap = argparse.ArgumentParser(prog="pssc sim")
    _add_commands(ap)
    return _run(ap.parse_args(argv), path)


def _request(msg: Dict[str, Any], path: Optional[str]) -> Optional[dict]:
    from . import client, protocol
    if path is None:
        path = protocol.socket_path(protocol.find_root())
    return client.request(path, dict(msg, **protocol.daemon_id()))


def _run(a, path: Optional[str]) -> int:
    msg: Dict[str, Any] = {"op": "sim", "cmd": a.sim_cmd,
                           "session": getattr(a, "session", None)}
    if a.sim_cmd == "run":
        msg.update(sel=a.sel, timeout=a.timeout)
    elif a.sim_cmd == "log":
        msg["since"] = a.since
    elif a.sim_cmd == "reload":
        msg.update(strict=a.strict, cancel=a.cancel, timeout=a.timeout,
                   image=os.path.abspath(a.image) if a.image else None)
    elif a.sim_cmd == "resume":
        msg["rerun"] = a.rerun
    reply = _request(msg, path)
    if reply is None:
        reply = ({"ok": True, "sessions": []} if a.sim_cmd == "list" else
                 {"ok": False, "error": "no workspace daemon: no sim sessions"})
    elif reply.get("error") == "version mismatch":
        reply["error"] = ("the workspace daemon is another pssc version "
                          "(`pssc daemon stop`, then restart the simulation)")
    from .protocol import daemon_id
    ok = bool(reply.get("ok"))
    ident = set(daemon_id()) | {"ok"}      # who answered, not the answer
    doc = {k: v for k, v in reply.items() if k not in ident}
    if a.json:
        print(json.dumps(dict(doc, ok=ok), sort_keys=True))
    elif not ok:
        print(f"pssc sim {a.sim_cmd}: {reply.get('error')}", file=sys.stderr)
    else:
        print(_text(a.sim_cmd, doc))
    if not ok:
        return 1
    return 1 if a.sim_cmd == "run" and doc.get("failed") else 0


def _text(cmd: str, doc: Dict[str, Any]) -> str:
    if cmd == "list":
        rows = doc["sessions"]
        if not rows:
            return "no sim sessions"
        return "\n".join(f"{r['id']}  {r.get('name') or '-'}  pid {r.get('pid')}"
                         f"  top {r.get('top') or '-'}  image {r.get('image') or '-'}"
                         for r in rows)
    if cmd == "run":
        lines = [f"{t.get('status', '?').upper()}  {t.get('name')}"
                 for t in doc.get("tests", [])]
        lines.append(f"{doc['session']}: {doc.get('passed', 0)} passed, "
                     f"{doc.get('failed', 0)} failed")
        return "\n".join(lines)
    if cmd == "log":
        out = []
        for e in doc["events"]:
            b = e.get("body") or {}
            if e.get("event") == "test":
                what = f"{b.get('status', '?').upper()}  {b.get('name')}"
            else:
                what = json.dumps(b, sort_keys=True)
            out.append(f"[{e['n']}] {e.get('event')}  {what}")
        return "\n".join(out) or f"{doc['session']}: no events"
    if cmd == "reload":
        out = [f"{doc['session']}: generation {doc.get('old')} -> "
               f"{doc.get('new')} ({doc.get('image')})"]
        for k in ("changed", "structure", "unbound", "reimported"):
            if doc.get(k):
                out.append(f"  {k}: {doc[k]}")
        return "\n".join(out)
    if cmd == "attach":
        return (f"{doc['session']}: dbg session {doc['dbg']} "
                f"(image {doc.get('image') or '-'})")
    if cmd == "resume":
        return (f"{doc['session']}: resumed {doc.get('why') or '-'}"
                + (" (run again)" if doc.get("rerun") else ""))
    return f"{doc['session']}: ended"
