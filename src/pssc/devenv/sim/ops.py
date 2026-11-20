"""The daemon's ``sim`` op: what ``pssc sim list|run|log|reload|attach|resume|end`` asks of the
server-mode sessions in :data:`.registry.REGISTRY` (they live in the daemon,
so the CLI reaches them through its command socket).

A request is ``{"op": "sim", "cmd": ..., "session": ID|NAME|None, ...}``; the
reply's ``ok``/``error`` say whether it worked, the rest is the command's.
"""
from __future__ import annotations

from typing import Any, Dict

from .registry import REGISTRY, SimError

#: how long ``end`` waits for the testbench to say goodbye
END_TIMEOUT = 10.0
#: how long ``reload`` waits by default (the testbench answers between runs)
RELOAD_TIMEOUT = 600.0


def handle(msg: Dict[str, Any]) -> Dict[str, Any]:
    cmd = msg.get("cmd")
    try:
        if cmd == "list":
            return {"ok": True, "sessions": REGISTRY.list()}
        s = REGISTRY.get(msg.get("session"))
        if cmd == "log":
            return {"ok": True, "session": s.id,
                    "events": s.log(int(msg.get("since") or 0))}
        if cmd == "run":
            before = s.n_events
            r = s.request("run", {"sel": list(msg.get("sel") or [])},
                          timeout=msg.get("timeout"))
            if not r.get("ok"):
                return {"ok": False, "session": s.id,
                        "error": (r.get("error") or {}).get("message", "failed")}
            tests = [e["body"] for e in s.log(before) if e.get("event") == "test"]
            return dict({"ok": True, "session": s.id, "tests": tests},
                        **(r.get("body") or {}))
        if cmd == "reload":
            args = {"strict": bool(msg.get("strict")),
                    "cancel": bool(msg.get("cancel"))}
            if msg.get("image"):
                args["image"] = msg["image"]
            r = s.request("reload", args, timeout=msg.get("timeout") or RELOAD_TIMEOUT)
            if not r.get("ok"):
                err = r.get("error") or {}
                out = {"ok": False, "session": s.id,
                       "error": err.get("message", "failed")}
                if err.get("detail"):
                    out["refused"] = err["detail"]
                return out
            body = r.get("body") or {}
            if body.get("image"):
                s.info["image"] = body["image"]
            return dict(body, ok=True, session=s.id)
        if cmd == "resume":
            r = s.request("resume", {"rerun": bool(msg.get("rerun"))},
                          timeout=msg.get("timeout") or RELOAD_TIMEOUT)
            if not r.get("ok"):
                return {"ok": False, "session": s.id,
                        "error": (r.get("error") or {}).get("message", "failed")}
            return dict(r.get("body") or {}, ok=True, session=s.id)
        if cmd == "attach":
            from ..debug.frontend import DebugError
            from ..debug.sessions import REGISTRY as DBG
            try:
                live = DBG.attach_sim(s)
            except (DebugError, RuntimeError) as e:
                return {"ok": False, "session": s.id, "error": str(e)}
            return {"ok": True, "session": s.id, "dbg": live.id,
                    "image": live.debug.stub.image}
        if cmd == "end":
            _detach_all(s)                 # a stopped call would block `end`
            try:
                r = s.request("end", timeout=END_TIMEOUT)
                ok = bool(r.get("ok"))
            except SimError:               # it closed first: ended all the same
                ok = True
            s.close()
            return {"ok": ok, "session": s.id}
    except SimError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"unknown sim command {cmd!r}"}


def _detach_all(s) -> None:
    """End the ``pssc dbg`` sessions on simulation ``s`` (``attach``)."""
    from ..debug.sessions import REGISTRY as DBG
    for live in list(DBG.sessions.values()):
        if getattr(live.debug.stub, "sim", None) is s:
            try:
                DBG.end(live.id)
            except Exception:
                pass
