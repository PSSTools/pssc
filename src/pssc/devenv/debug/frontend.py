"""The debug front end's session (plan P2.2): one engine debug stub (be-bc
``interp.debug.Stub``) with the source-level pieces of this package around
it -- breakpoints by line, register watches by path, the variable model and
expression evaluation.

The stub stops at every pc break and every watched access; the session
decides which stops are the user's (:meth:`BreakpointSet.on_stop`,
:meth:`WatchSet.on_stop`: conditions, hit counts, watched values) and
continues past the others, collecting logpoint lines as ``output`` records.
A run command (:meth:`DebugSession.resume`) returns once the run is stopped
for the user or has ended, with one stop record.

It is transport-free: requests are method calls and records are dicts, so the
daemon's ``pssc dbg`` sessions (P2.3) and the DAP server (P2.4) put their own
wire on it.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from .breakpoints import BreakpointSet
from .eval import Evaluator
from .variables import ChannelLog, Variables
from .watches import WatchSet, learn_bases

#: stop reasons the session filters (the stub stops at every candidate)
FILTERED = ("breakpoint", "watch")


class DebugError(Exception):
    pass


class DebugSession:
    """A debug session of ``stub`` (not attached yet) on the run of
    ``model`` (a debug-lowered image) over ``platform`` (a fake: register
    bases, bus log). ``learn``: learn register bases from the run's accesses
    (wraps ``stub.vm.memory``; leave it False when the stub's VM is not the
    one the platform serves)."""

    def __init__(self, stub, model, platform=None,
                 channels: Optional[ChannelLog] = None, learn: bool = True,
                 timeout: Optional[float] = 30.0):
        self.stub, self.model, self.platform = stub, model, platform
        self.timeout = timeout
        self.breakpoints = BreakpointSet(model)
        self.watches = WatchSet(model)
        for comp, base in dict(getattr(platform, "bases", None) or {}).items():
            self.watches.learn(comp, base)
        if learn and getattr(stub, "vm", None) is not None:
            learn_bases(stub.vm, self.watches)
        self.variables = Variables.for_session(stub, model, platform, channels)
        self.evaluator = Evaluator.for_session(stub, model, platform)
        #: logpoint lines not yet taken (:meth:`take_output`)
        self.output: List[dict] = []
        #: the last stop record; None before :meth:`start`
        self.last: Optional[dict] = None
        self.attached = False
        #: called with every stop/exit record :meth:`wait` returns and with
        #: ``{"event": "continued", "thread"}`` when a run command starts
        self._listeners: List[Callable[[dict], None]] = []
        #: one caller at a time takes the stub's events
        self._waiter = threading.Lock()
        #: the attach reply's capabilities; the ``set_stop_on`` kinds
        self.capabilities: List[str] = []
        self._stop_on: Optional[List[str]] = None
        #: ``() -> (stub, platform)``: the same run again from its start, on
        #: a fresh platform (:meth:`back`, :meth:`reverse`); None: no going back
        self.respawn: Optional[Callable[[], tuple]] = None

    # -- listeners ---------------------------------------------------------

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        """Call ``fn`` with each record of this session's run, whoever
        (a ``pssc dbg`` command, a DAP client) asked for the run."""
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, rec: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(rec)
            except Exception:               # a gone client must not stop the run
                pass

    # -- requests ----------------------------------------------------------

    def _req(self, cmd: str, **args) -> dict:
        rep = self.stub.request({"cmd": cmd, "args": args})
        if not rep.get("ok"):
            err = rep.get("error") or {}
            raise DebugError(f"{cmd}: {err.get('message', err)}")
        return rep.get("body") or {}

    def start(self, stop_on: Optional[Sequence[str]] = None,
              entry: bool = True) -> dict:
        """Attach; the run is stopped before its first instruction (with
        ``entry`` false: not stopped, the session is ``running`` until a
        breakpoint stops it; a simulation's VM, plan P3.7). Sends the
        breakpoints and watches set so far. Returns the attach reply's body."""
        args = {"version": 1} if entry else {"version": 1, "entry": False}
        body = self._req("attach", **args)
        self.attached = True
        self.capabilities = list(body.get("capabilities") or [])
        self.breakpoints.apply(self.stub)
        self.watches.apply(self.stub)
        if stop_on is not None:
            self.set_stop_on(stop_on)
        self.last = ({"event": "stopped", "reason": "entry"} if entry else
                     {"event": "running", "timeout": 0, "output": []})
        return body

    def set_breakpoints(self, file: str, specs: Sequence[dict]) -> List[dict]:
        """Replace ``file``'s breakpoints (``BreakpointSet.set_file`` specs)."""
        bps = self.breakpoints.set_file(file, specs)
        if self.attached:
            self.breakpoints.apply(self.stub)
        return [bp.to_json() for bp in bps]

    def set_watches(self, specs: Sequence[dict]) -> List[dict]:
        """Replace the register watches (``WatchSet.set`` specs)."""
        ws = self.watches.set(specs)
        if self.attached:
            self.watches.apply(self.stub)
        return [w.to_json() for w in ws]

    def add_breakpoint(self, file: str, spec: dict) -> dict:
        """Add one breakpoint to ``file``'s, keeping the others."""
        bp = self.breakpoints.add(file, spec)
        if self.attached:
            self.breakpoints.apply(self.stub)
        return bp.to_json()

    def add_watch(self, spec: dict) -> dict:
        """Add one register watch, keeping the others."""
        w = self.watches.add(spec)
        if self.attached:
            self.watches.apply(self.stub)
        return w.to_json()

    def set_stop_on(self, kinds: Sequence[str]) -> List[str]:
        """Stop on these engine events (the stub's ``set_stop_on`` kinds:
        ``error``, ``check``, ``sanitizer``); returns the kinds it took."""
        self._stop_on = list(kinds)
        return self._req("set_stop_on", kinds=list(kinds))["kinds"]

    # -- running -----------------------------------------------------------

    @property
    def running(self) -> bool:
        """The last run command's wait ran out with the run still going."""
        return (self.last or {}).get("event") == "running"

    @property
    def going(self) -> bool:
        """The run is going: someone waits for its stop, or a wait ran out."""
        return self.waiting or self.running

    @property
    def waiting(self) -> bool:
        """Someone (a command, a DAP client's run) waits for the run's stop."""
        return self._waiter.locked()

    def resume(self, cmd: str = "continue", timeout: Optional[float] = None,
               **args) -> dict:
        """Run (``continue``, or ``step`` with ``kind`` and ``thread``) to the
        next user stop or the end; returns its record (:meth:`wait`). While
        the run is still going from an earlier command, only waits again."""
        def run():
            self.variables.invalidate()
            if not self.running:
                self._req(cmd, **args)
                self._notify({"event": "continued", "thread": args.get("thread")})
            return self._wait(timeout)
        return self._waited(run)

    def cont(self, timeout: Optional[float] = None) -> dict:
        return self.resume("continue", timeout)

    def step(self, kind: str = "stmt", thread: Optional[int] = None,
             timeout: Optional[float] = None) -> dict:
        """Step the stopped thread (or ``thread``) by ``kind`` (stmt, into, out)."""
        if self.running:
            raise DebugError("step needs a stopped run (continue or pause first)")
        if thread is None:
            thread = (self.last or {}).get("thread")
        if thread is None:                 # at entry: the thread about to run
            thread = next((t["id"] for t in self.threads()
                           if t.get("state") == "running"), None)
        return self.resume("step", timeout, kind=kind, thread=thread)

    def pause(self) -> None:
        """Ask a running run to stop; the stop comes to :meth:`wait`."""
        self._req("stop")

    def wait(self, timeout: Optional[float] = None) -> dict:
        """Take the stub's events until the run is stopped for the user or
        has ended: ``{"event": "stopped", "reason", "thread", "frame", "coro",
        "pc", "src_ref", "source"?, "detail", "breakpoints"?, "watches"?,
        "output"}`` or ``{"event": "exited", ..., "output"}``. ``output``:
        the logpoint lines written on the way. With no stop within
        ``timeout`` seconds (default: the session's), the run is left going:
        ``{"event": "running", "timeout", "output"}``. A stop or exit
        record also goes to the listeners (:meth:`add_listener`)."""
        return self._waited(lambda: self._wait(timeout))

    def _waited(self, fn: Callable[[], dict]) -> dict:
        """``fn``'s record, as the session's one waiter; then the listeners'."""
        if not self._waiter.acquire(blocking=False):
            raise DebugError("the run is going (another client waits for it; pause first)")
        try:
            rec = fn()
        finally:
            self._waiter.release()
        if rec["event"] != "running":
            self._notify(rec)
        return rec

    def _wait(self, timeout: Optional[float]) -> dict:
        out: List[dict] = []
        if timeout is None:
            timeout = self.timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            ev = self.stub.next_event(left)
            if ev is None:
                self.last = {"event": "running", "timeout": timeout, "output": out}
                return self.last
            body = dict(ev.get("body") or {})
            if ev["event"] == "exited":
                self.attached = False
                self.last = {"event": "exited", **body, "output": out}
                return self.last
            if ev["event"] != "stopped":
                continue
            rec = {"event": "stopped", **body}
            reason, hits = body.get("reason"), body.get("hits") or []
            if reason in FILTERED:
                evaluate = self.evaluator.at_stop(body)
                if reason == "breakpoint":
                    ids, logs = self.breakpoints.on_stop(hits, evaluate)
                    lines = [{"category": "console", "output": s,
                              "src_ref": body.get("src_ref")} for s in logs]
                    out += lines
                    self.output += lines
                    rec["breakpoints"] = ids
                else:
                    ids, seen = self.watches.on_stop(hits, body.get("detail") or {},
                                                     evaluate)
                    rec["watches"] = seen
                if not ids:
                    self._req("continue")
                    continue
            src = self.source(body.get("src_ref"))
            if src is not None:
                rec["source"] = src
            rec["output"] = out
            self.last = rec
            return rec

    # -- going back (plan P5.2) ---------------------------------------------
    #
    # A run on a deterministic platform reaches the same statement count
    # (the stub's ``tick``) at the same place, so going back is running the
    # same run again from its start to an earlier tick (no snapshots).

    def back(self, timeout: Optional[float] = None) -> dict:
        """Step back: to the start of the statement before the current one in
        the stopped thread, at its depth or above (the reverse of ``step``).
        At the run's first statement: the run's start (reason ``start``)."""
        def run():
            now, hist = self._history()
            thread = (self.last or {}).get("thread")
            cur = next((h for h in reversed(hist) if h[0] <= now
                        and (thread is None or h[1] == thread)), None)
            if cur is None:
                return self._rerun(0, timeout)
            target = next((h[0] for h in reversed(hist)
                           if h[0] < cur[0] and h[1] == cur[1] and h[2] <= cur[2]), 0)
            return self._rerun(target, timeout, why={"reason": "step",
                                                     "detail": {"kind": "back"}})
        return self._going_back(run)

    def reverse(self, timeout: Optional[float] = None,
                watch: Optional[dict] = None) -> dict:
        """Reverse-continue: back to the last user stop (breakpoint, watch)
        before the current point; none: the run's start (reason ``start``).
        With ``watch`` (an :meth:`add_watch` spec): back to the last access
        it matches only, through a watch that is gone afterwards."""
        if watch is not None:
            self.check_back()
            w = self.watches.add(watch)
            try:
                if not w.verified:
                    raise DebugError(w.message or f"cannot watch {w.path}")
                self.watches.apply(self.stub)
                return self._reverse(timeout, w.id)
            finally:
                self.watches.remove(w.id)
                if self.attached:
                    self.watches.apply(self.stub)
        return self._reverse(timeout, None)

    def _reverse(self, timeout: Optional[float], only: Optional[int]) -> dict:
        def run():
            now, _ = self._history()
            stops: List[dict] = []
            self._rerun(now, timeout, stops=stops)
            before = [st for st in stops if st["tick"] < now and
                      (only is None or only in st.get("watches", ()))]
            if not before:
                return self._rerun(0, timeout)
            return self._rerun(before[-1]["tick"], timeout, why=before[-1])
        return self._going_back(run)

    def check_back(self) -> None:
        """Raise :class:`DebugError` saying why this session cannot go back
        now (:meth:`back`, :meth:`reverse`); return when it can."""
        if self.running:
            raise DebugError("going back needs a stopped run (pause first)")
        if self.respawn is None or "run_to" not in self.capabilities:
            raise DebugError("back needs the Python engine (this engine cannot "
                             "run to a statement count)")
        if not self.attached:
            raise DebugError("the run has ended")

    def _going_back(self, fn: Callable[[], dict]) -> dict:
        self.check_back()

        def run():
            self.variables.invalidate()
            self._notify({"event": "continued",
                          "thread": (self.last or {}).get("thread")})
            return fn()
        return self._waited(run)

    def _history(self):
        body = self._req("history")
        return body["tick"], [tuple(h) for h in body.get("history") or []]

    def _restart(self) -> None:
        """End this run quietly and attach to the same run from its start."""
        if self.attached:
            self.stub.request({"cmd": "terminate"})
            while True:
                ev = self.stub.next_event(self.timeout)
                if ev is None or ev.get("event") == "exited":
                    break
            self.attached = False
        self.stub.join(self.timeout)
        self.stub, self.platform = self.respawn()
        for comp, base in dict(getattr(self.platform, "bases", None) or {}).items():
            self.watches.learn(comp, base)
        if getattr(self.stub, "vm", None) is not None:
            learn_bases(self.stub.vm, self.watches)
        self.variables = Variables.for_session(self.stub, self.model, self.platform,
                                               None)
        self.evaluator = Evaluator.for_session(self.stub, self.model, self.platform)
        body = self._req("attach", version=1)
        self.attached = True
        self.capabilities = list(body.get("capabilities") or [])
        for bp in self.breakpoints.all():        # counted again on the way
            bp.hits = 0
        self.breakpoints.apply(self.stub)
        self.watches.apply(self.stub)
        if self._stop_on is not None:
            self._req("set_stop_on", kinds=self._stop_on)

    def _rerun(self, tick: int, timeout: Optional[float],
               stops: Optional[List[dict]] = None,
               why: Optional[dict] = None) -> dict:
        """Restart and run to ``tick``, counting breakpoint hits on the way
        (logpoints stay silent); ``stops`` gets each user stop passed
        (``{tick, reason, breakpoints|watches}``). Returns the stop record,
        its reason and ids from ``why`` (reason ``start`` when ``tick`` is at
        or before the run's start)."""
        self._restart()
        now, _ = self._history()
        if tick <= now:
            ids = [t.get("id") for t in self.threads() if t.get("state") == "running"]
            return self._landed({"reason": "start", "tick": now,
                                 "thread": ids[0] if ids else None})
        deadline = None if timeout is None and self.timeout is None else \
            time.monotonic() + (timeout if timeout is not None else self.timeout)
        self._req("run_to", tick=tick)
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            ev = self.stub.next_event(left)
            if ev is None:
                raise DebugError(f"going back: tick {tick} not reached in time")
            body = dict(ev.get("body") or {})
            if ev["event"] == "exited":
                self.attached = False
                raise DebugError(f"going back: the run ended before tick {tick} "
                                 "(is the platform deterministic?)")
            if ev["event"] != "stopped":
                continue
            if body.get("reason") == "goto" or body.get("tick", 0) >= tick:
                body.pop("hits", None)
                why = dict(why or {})
                counts = why.pop("counts", None)      # as when it stopped
                for bp in self.breakpoints.all() if counts else ():
                    bp.hits = counts.get(bp.id, bp.hits)
                body.update({k: v for k, v in why.items() if k != "tick"})
                return self._landed(body)
            if body.get("reason") in FILTERED:
                evaluate = self.evaluator.at_stop(body)
                hits = body.get("hits") or []
                if body["reason"] == "breakpoint":
                    ids, _ = self.breakpoints.on_stop(hits, evaluate)
                else:
                    ids, _ = self.watches.on_stop(hits, body.get("detail") or {},
                                                  evaluate)
                if ids and stops is not None:
                    key = "breakpoints" if body["reason"] == "breakpoint" else "watches"
                    stops.append({"tick": body.get("tick", 0),
                                  "reason": body["reason"], key: ids,
                                  "counts": {bp.id: bp.hits
                                             for bp in self.breakpoints.all()}})
            self._req("run_to", tick=tick)

    def _landed(self, body: dict) -> dict:
        rec = {"event": "stopped", **body}
        src = self.source(body.get("src_ref"))
        if src is not None:
            rec["source"] = src
        rec["output"] = []
        self.last = rec
        return rec

    def take_output(self) -> List[dict]:
        """The logpoint lines since the last call."""
        out, self.output = self.output, []
        return out

    def end(self) -> None:
        """End the run (terminate) and wait for the engine's thread."""
        if self.attached:
            self.stub.request({"cmd": "terminate"})
            while True:
                ev = self.stub.next_event(self.timeout)
                if ev is None or ev.get("event") == "exited":
                    break
            self.attached = False
            self._notify({"event": "exited", "ended": True, "output": []})
        self.stub.join(self.timeout)

    # -- inspection (stopped) ----------------------------------------------

    def source(self, src_ref: Optional[int]) -> Optional[Dict[str, Any]]:
        """``{"file", "line", "col"?}`` of provenance entry ``src_ref``."""
        prov = getattr(getattr(self.model, "prov", None), "entries", None) or []
        p = prov[src_ref] if src_ref and 0 < src_ref < len(prov) else None
        if p is None or not p.file:
            return None
        d = {"file": p.file, "line": p.line}
        if getattr(p, "col", 0):
            d["col"] = p.col
        return d

    def threads(self) -> List[dict]:
        return self._req("threads")["threads"]

    def frames(self, thread: Optional[int] = None) -> List[dict]:
        """The stack of ``thread`` (default: the stopped one), innermost
        first, each with its ``source``."""
        if thread is None:
            thread = (self.last or {}).get("thread")
        out = self._req("frames", thread=thread)["frames"]
        for f in out:
            src = self.source(f.get("src_ref"))
            if src is not None:
                f["source"] = src
        return out

    def scopes(self, frame: Optional[int] = None) -> List[dict]:
        return self.variables.scopes(self._frame(frame))

    def vars(self, ref: int) -> List[dict]:
        return self.variables.variables(ref)

    def evaluate(self, text: str, frame: Optional[int] = None) -> int:
        """``text`` in ``frame`` (a ``frames`` id; default: the stopped one)."""
        last = self.last or {}
        if frame is None or frame == last.get("frame"):
            if last.get("event") != "stopped" or "frame" not in last:
                raise DebugError("the run is not stopped in a frame")
            return self.evaluator.at_stop(last)(text)
        f = next((f for t in self.threads() for f in self.frames(t["id"])
                  if f["id"] == frame), None)
        if f is None:
            raise DebugError(f"no frame {frame}")
        return self.evaluator.evaluate(text, frame, f["coro"], f["pc"])

    def _frame(self, frame: Optional[int]) -> int:
        if frame is not None:
            return frame
        last = self.last or {}
        if "frame" not in last:
            raise DebugError("the run is not stopped in a frame")
        return last["frame"]
