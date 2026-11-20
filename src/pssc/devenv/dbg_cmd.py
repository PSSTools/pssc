"""`pssc dbg`: command-line debug sessions (plan P2.3).

``pssc dbg start`` lowers one test for debugging and starts its run on the
engine's debug stub, stopped before its first instruction; the other
subcommands go to that session (the latest, or ``--session ID``): set
breakpoints and register watches, run it (``run``/``continue``, ``step``,
``next``, ``out``; backwards: ``back``, ``rc``) and look at it where it stopped (``bt``, ``locals``,
``regs``, ``bus``, ``eval``, ``threads``). ``reload`` makes the edited
sources its next generation (hot reload). ``end`` ends it.

The sessions live in the process that runs the command
(:mod:`.debug.sessions`): the workspace daemon, so one ``pssc dbg`` command
after another, each its own client process, works on the same run. A run
command waits for the next stop at most ``--timeout`` seconds; with the run
still going it says ``running``, and the next run command waits again.

``--json`` prints one JSON document: a run command's (and ``start``'s) is the
stop record, schema ``pssunit.stop/1`` (docs/schemas/pssunit.stop-1.json).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

#: run commands: the DebugSession call each makes
RUN = {"run": ("continue", None), "continue": ("continue", None),
       "step": ("step", "into"), "next": ("step", "stmt"),
       "out": ("step", "out"), "back": ("back", None),
       "rc": ("reverse", None)}
STOP_SCHEMA = "pssunit.stop/1"
#: how deep ``locals``/``regs`` expand structured values
DEPTH = 4


def add_parser(sub) -> None:
    """Register `pssc dbg` on the top-level subparsers *sub*."""
    db = sub.add_parser("dbg", help="debug one test from the command line "
                        "(sessions kept by the workspace daemon)")
    dsub = db.add_subparsers(dest="dbg_cmd", metavar="COMMAND")
    dsub.required = True

    def cmd(name, help, *, session=True, timeout=False):
        p = dsub.add_parser(name, help=help)
        if session:
            p.add_argument("--session", metavar="ID",
                           help="the session (default: the latest)")
        if timeout:
            p.add_argument("--timeout", type=float, metavar="SECONDS",
                           help="wait at most this long for the run to stop "
                           "(default: 30); then it is left running")
        p.add_argument("--json", action="store_true",
                       help="print one JSON document")
        return p

    st = cmd("start", "start a session on one test, stopped before its "
             "first instruction", session=False, timeout=True)
    st.add_argument("sources", nargs="*", metavar="SRC-SPEC|TEST-ID",
                    help="PSS file, directory, glob or @filelist (default: "
                    "the current directory), and the test's ID "
                    "(COMP::NAME; globs allowed)")
    st.add_argument("-k", metavar="EXPR", dest="k",
                    help="select the test by name expression instead of ID")
    st.add_argument("--seed", type=lambda s: int(s, 0), metavar="N")
    st.add_argument("--platform", metavar="SPEC",
                    help="as `pssc test --platform` (default: the test's)")
    st.add_argument("--trace", metavar="FILE|DIR",
                    help="replay a recorded run: the same as "
                    "--platform replay:FILE|DIR")
    st.add_argument("--reg-semantics", metavar="FILE", dest="reg_semantics",
                    help="as `pssc test --reg-semantics`")

    br = dsub.add_parser("break", help="breakpoints")
    bsub = br.add_subparsers(dest="break_cmd", metavar="ACTION")
    bsub.required = True
    ba = bsub.add_parser("add", help="add a breakpoint at FILE:LINE")
    ba.add_argument("where", metavar="FILE:LINE")
    ba.add_argument("--condition", metavar="EXPR",
                    help="stop only when EXPR is non-zero")
    ba.add_argument("--hit", dest="hit_condition", metavar="N|>=N|%N",
                    help="stop only on these hits")
    ba.add_argument("--log", dest="log_message", metavar="TEXT",
                    help="a logpoint: write TEXT ({expr} interpolated) and go on")
    bl = bsub.add_parser("list", help="the session's breakpoints")
    for p in (ba, bl):
        p.add_argument("--session", metavar="ID",
                       help="the session (default: the latest)")
        p.add_argument("--json", action="store_true",
                       help="print one JSON document")

    wa = cmd("watch", "stop when a register (or field) is accessed")
    wa.add_argument("path", metavar="PATH", help="register or register.field")
    wa.add_argument("--rw", choices=("r", "w", "rw"), default="w",
                    help="the accesses that stop (default: w)")
    wa.add_argument("--value", type=lambda s: int(s, 0), metavar="N",
                    help="only when the value written (or read) is N")
    wa.add_argument("--condition", metavar="EXPR")

    run_cmds = {}
    for name, (_, kind) in RUN.items():
        run_cmds[name] = cmd(name, {"run": "run to the next stop or the end",
                   "continue": "the same as run",
                   "step": "step into the next statement",
                   "next": "step over to the next statement",
                   "out": "run until the current function returns",
                   "back": "step back to the previous statement (Python "
                           "engine; the run is run again to it)",
                   "rc": "reverse-continue: back to the last breakpoint or "
                         "watch stop before here"}[name],
            timeout=True)
    run_cmds["rc"].add_argument(
        "--watch", metavar="PATH",
        help="back to the last write of this register or field only "
             "(through a watch that is gone afterwards)")
    cmd("pause", "ask a running run to stop (the next run command returns "
        "the stop)")

    bt = cmd("bt", "the stack of the stopped thread")
    bt.add_argument("--thread", type=int, metavar="ID")
    lo = cmd("locals", "the variables of a frame")
    lo.add_argument("--frame", type=int, metavar="ID",
                    help="a frame from bt (default: the stopped one)")
    rg = cmd("regs", "register values (through the back door)")
    rg.add_argument("path", nargs="?", metavar="PATH",
                    help="only registers under this path")
    bu = cmd("bus", "the last bus accesses, newest first")
    bu.add_argument("-n", type=int, default=16, metavar="N",
                    help="how many (default: 16)")
    ev = cmd("eval", "evaluate a PSS expression where the run stopped")
    ev.add_argument("expr", metavar="EXPR")
    ev.add_argument("--frame", type=int, metavar="ID")
    cmd("threads", "the run's threads")
    rl = cmd("reload", "hot reload: lower the session's sources again and make "
             "them its next generation (staged until the run ends)")
    rl.add_argument("--cancel", action="store_true",
                    help="end the running call first (refused while one runs)")
    rl.add_argument("--strict", action="store_true",
                    help="refuse a change to the component tree")
    cmd("end", "end the session")
    cmd("list", "the sessions", session=False)
    db.set_defaults(func=cmd_dbg)


def cmd_dbg(args: argparse.Namespace) -> int:
    from ..testing.sources import SourceSpecError
    from .debug.frontend import DebugError
    try:
        return _cmd_dbg(args)
    except RuntimeError as e:
        if not hasattr(e, "to_json"):             # be-bc's ReloadRefused
            raise
        if getattr(args, "json", False):
            print(json.dumps({"error": e.to_json()}, indent=2, sort_keys=True))
        else:
            print(f"pssc: error: reload refused ({e.reason}): {e}",
                  file=sys.stderr)
        return 1
    except (DebugError, SourceSpecError, KeyError, ValueError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        print(f"pssc: error: {msg}", file=sys.stderr)
        return 1


def _registry():
    from .debug import sessions
    return sessions.REGISTRY


def _cmd_dbg(args: argparse.Namespace) -> int:
    from .debug.frontend import DebugError
    reg = _registry()
    c = args.dbg_cmd
    if c == "start":
        return _start(reg, args)
    if c == "list":
        return _out(args, {"sessions": reg.list()}, _text_list)
    if c == "reload":
        r = reg.reload(args.session, cancel=args.cancel, strict=args.strict)
        return _out(args, r, _text_reload)
    live = reg.get(args.session)
    d = live.debug
    if c in RUN:
        call, kind = RUN[c]
        rec = d.cont(args.timeout) if call == "continue" else \
            d.back(args.timeout) if call == "back" else \
            d.reverse(args.timeout, watch=None if not args.watch else
                      {"path": args.watch, "rw": "w"}) if call == "reverse" else \
            d.step(kind, timeout=args.timeout)
        return _stop(args, live, rec)
    if c == "pause":
        d.pause()
        return _out(args, {"session": live.id, "paused": True},
                    lambda doc: [f"session {live.id}: pause asked"])
    if c == "break":
        if args.break_cmd == "add":
            file, _, line = args.where.rpartition(":")
            if not file or not line.isdigit():
                raise ValueError(f"{args.where!r}: expected FILE:LINE")
            spec = {"line": int(line), "condition": args.condition,
                    "hit_condition": args.hit_condition,
                    "log_message": args.log_message}
            bp = d.add_breakpoint(file, spec)
            return _out(args, {"session": live.id, "breakpoint": bp},
                        lambda doc: [_text_bp(doc["breakpoint"])])
        bps = [bp.to_json() for bp in d.breakpoints.all()]
        return _out(args, {"session": live.id, "breakpoints": bps},
                    lambda doc: [_text_bp(b) for b in bps])
    if c == "watch":
        w = d.add_watch({"path": args.path, "rw": args.rw, "value": args.value,
                         "condition": args.condition})
        return _out(args, {"session": live.id, "watch": w},
                    lambda doc: [_text_watch(w)])
    if c == "end":
        reg.end(live.id)
        return _out(args, {"session": live.id, "ended": True},
                    lambda doc: [f"session {live.id} ended"])
    if c == "bt":
        frames = d.frames(args.thread)
        return _out(args, {"session": live.id, "frames": frames},
                    lambda doc: [_text_frame(f) for f in frames])
    if c == "threads":
        ts = d.threads()
        return _out(args, {"session": live.id, "threads": ts},
                    lambda doc: [f"{t['id']}  {t.get('name', '')}" for t in ts])
    if c == "locals":
        scopes = [{"name": s["name"], "variables": _expand(d, s["ref"])}
                  for s in d.scopes(args.frame)
                  if s["name"] in ("Locals", "Action", "Component")]
        return _out(args, {"session": live.id, "scopes": scopes},
                    lambda doc: [ln for s in scopes for ln in
                                 [f"{s['name']}:"] + _text_vars(s["variables"])])
    if c == "regs":
        vs = [v for v in d.variables._regs("")
              if not args.path or v["name"] == args.path
              or v["name"].startswith(args.path + ".")]
        vs = [_tree(d, v, DEPTH) for v in vs]
        return _out(args, {"session": live.id, "registers": vs},
                    lambda doc: _text_vars(vs, 0))
    if c == "bus":
        var = d.variables
        if var.bus is None:
            raise DebugError("the session's platform keeps no bus log")
        saved, var.bus_n = var.bus_n, args.n
        try:
            vs = [_tree(d, v, 1) for v in var._bus()]
        finally:
            var.bus_n = saved
        return _out(args, {"session": live.id, "bus": vs},
                    lambda doc: _text_vars(vs, 0))
    if c == "eval":
        v = d.evaluate(args.expr, args.frame)
        return _out(args, {"session": live.id, "expr": args.expr, "value": v},
                    lambda doc: [f"{v} ({v:#x})" if v >= 0 else str(v)])
    raise ValueError(f"unknown dbg command {c!r}")


def _start(reg, args) -> int:
    srcs = [s for s in args.sources if "::" not in s]
    ids = [s for s in args.sources if "::" in s]
    if len(ids) + bool(args.k) != 1:
        raise ValueError("give the test: one ID (COMP::NAME) or -k EXPR")
    test = ids[0] if ids else args.k
    kw = {} if args.timeout is None else {"timeout": args.timeout}
    platform = args.platform
    if args.trace:
        if platform:
            raise ValueError("give --trace or --platform, not both")
        platform = "replay:" + args.trace
    if getattr(args, "reg_semantics", None):
        kw["reg_semantics"] = os.path.abspath(args.reg_semantics)
    live = reg.start(srcs or ["."], test, seed=args.seed,
                     platform=platform, **kw)
    return _stop(args, live, live.debug.last)


def _stop(args, live, rec: Dict[str, Any]) -> int:
    doc = {"schema": STOP_SCHEMA, "session": live.id, **rec}
    if args.dbg_cmd == "start":
        doc.update(test=live.item.qname, seed=live.seed)
    if doc["event"] == "exited":
        doc.setdefault("output", [])
        if live.checks is not None:
            doc["checks"] = dict(zip(("passed", "failed", "skipped"), live.checks))
        if live.error is not None:
            doc["error"] = str(live.error)
    return _out(args, doc, lambda doc: _text_stop(doc, live))


def _out(args, doc, text) -> int:
    if args.json:
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        for ln in text(doc):
            print(ln)
    return 0


# --- expanding variables -----------------------------------------------------

def _tree(d, v: dict, depth: int) -> dict:
    out = {k: v[k] for k in ("name", "value") if k in v}
    if v.get("ref") and depth > 0:
        out["children"] = _expand(d, v["ref"], depth - 1)
    return out


def _expand(d, ref: int, depth: int = DEPTH) -> List[dict]:
    return [_tree(d, v, depth) for v in d.vars(ref)]


# --- text --------------------------------------------------------------------

def _where(src: Optional[dict]) -> str:
    if not src:
        return "?"
    return f"{src['file']}:{src['line']}"


def _text_stop(doc: dict, live) -> List[str]:
    out = [o["output"].rstrip("\n") for o in doc.get("output") or ()]
    sid = f"session {live.id}"
    if doc["event"] == "running":
        out.append(f"{sid}: running ({doc.get('timeout')} s passed with no "
                   "stop; run again to wait, or pause)")
    elif doc["event"] == "exited":
        ch = doc.get("checks")
        tail = (f": {ch['passed']} checks passed, {ch['failed']} failed"
                if ch else "")
        out.append(f"{sid}: exited{tail}")
        if doc.get("error"):
            out.append(f"  error: {doc['error']}")
    else:
        head = f"{sid}: stopped ({doc.get('reason')})"
        if doc.get("source"):
            head += f" at {_where(doc['source'])}"
        if doc.get("breakpoints"):
            head += " breakpoint " + ", ".join(map(str, doc["breakpoints"]))
        out.append(head)
        if "test" in doc:
            out.append(f"  test {doc['test']} seed {doc['seed']}")
    return out


def _text_bp(bp: dict) -> str:
    s = f"breakpoint {bp['id']} {bp['file']}:{bp['line']}"
    if bp.get("resolved_line") not in (None, bp["line"]):
        s += f" (at line {bp['resolved_line']})"
    if not bp["verified"]:
        s += f" -- not set: {bp.get('message', '')}"
    return s


def _text_watch(w: dict) -> str:
    s = f"watch {w['id']} {w['path']} ({w['rw']})"
    if not w["verified"] and w.get("message"):
        s += f" -- {w['message']}"
    return s


def _text_frame(f: dict) -> str:
    return f"#{f['id']} {f.get('name', '?')}  {_where(f.get('source'))}"


def _text_vars(vs: List[dict], indent: int = 1) -> List[str]:
    out = []
    for v in vs:
        out.append("  " * indent + f"{v['name']} = {v.get('value', '')}")
        out += _text_vars(v.get("children") or [], indent + 1)
    return out


def _text_reload(r: dict) -> List[str]:
    state = "applied" if r.get("applied") else "staged until the run ends"
    out = [f"session {r['session']}: generation {r['old']} -> {r['new']} "
           f"({state})"]
    for k in ("changed", "structure", "unbound", "reimported"):
        if r.get(k):
            out.append(f"  {k}: {r[k]}")
    return out


def _text_list(doc: dict) -> List[str]:
    return [f"{s['session']}  {s['test']}  seed {s['seed']}  {s['state']}"
            for s in doc["sessions"]] or ["no debug sessions"]
