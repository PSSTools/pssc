"""The Debug Adapter Protocol server (plan P2.4): one :class:`Connection` per
client (an editor such as VS Code) on the ``pssc dbg`` sessions of
:mod:`.sessions`. ``launch`` starts a session on one test; ``attach`` joins
one already started (by ``pssc dbg start`` or another client). The custom
request ``pssc/reload`` is *Apply change* (plan P3.6c3): ``pssc dbg reload``
on the client's session.

A connection is transport-free: it reads and writes DAP messages
(``Content-Length`` framed JSON) on two binary streams, so tests drive it over
a socket pair and the daemon and ``pssc dap`` only add the wire.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, BinaryIO, Callable, Dict, List, Optional

from .frontend import DebugError
from .watches import resolve
from .sessions import REGISTRY, Live, Registry

#: what a ``initialize`` reply says this server does
CAPABILITIES: Dict[str, Any] = {
    "supportsConfigurationDoneRequest": True,
    "supportTerminateDebuggee": True,
    "supportsConditionalBreakpoints": True,
    "supportsHitConditionalBreakpoints": True,
    "supportsLogPoints": True,
    "supportsDataBreakpoints": True,
    "supportsEvaluateForHovers": True,
    #: stepBack / reverseContinue: Python-engine sessions only (others refuse)
    "supportsStepBack": True,
    "exceptionBreakpointFilters": [
        {"filter": "error", "label": "Errors", "default": True},
        {"filter": "check", "label": "Failed checks", "default": False},
        {"filter": "sanitizer", "label": "Sanitizer findings", "default": False},
    ],
    #: not DAP: the custom requests this server answers
    "pssc": {"requests": ["pssc/reload"]},
}

#: DAP data breakpoint access types -> watch ``rw``
ACCESS = {"read": "r", "write": "w", "readWrite": "rw"}

#: DAP step requests -> ``DebugSession.step`` kinds
STEPS = {"next": "stmt", "stepIn": "into", "stepOut": "out"}

#: stub stop reasons -> DAP ``stopped`` reasons (others: ``exception``)
REASONS = {"entry": "entry", "start": "entry", "breakpoint": "breakpoint", "step": "step",
           "pause": "pause", "watch": "data breakpoint"}

#: ``launch`` and ``chdir``: the process has one cwd, connections are threads
_CWD_LOCK = threading.Lock()


# -- framing ---------------------------------------------------------------

def read_message(rfile: BinaryIO) -> Optional[Dict[str, Any]]:
    """The next message on ``rfile``; None at the end of the stream."""
    length = None
    while True:
        line = rfile.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            if length is None:
                continue                    # stray blank line before a header
            break
        name, _, value = line.decode("ascii").partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    data = rfile.read(length)
    if len(data) < length:
        return None
    return json.loads(data.decode("utf-8"))


def write_message(wfile: BinaryIO, msg: Dict[str, Any]) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    wfile.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
    wfile.flush()


# -- a client --------------------------------------------------------------

class Connection:
    """One DAP client on ``registry``'s sessions (default: this process's)."""

    def __init__(self, rfile: BinaryIO, wfile: BinaryIO,
                 registry: Optional[Registry] = None):
        self.rfile, self.wfile = rfile, wfile
        self.registry = registry if registry is not None else REGISTRY
        self.live: Optional[Live] = None
        #: the session was started by this client's ``launch``
        self.launched = False
        self.stop_on_entry = False
        #: a run command's thread is waiting for the run's next stop
        self.busy = False
        self.closed = False
        self._seq = 0
        self._wlock = threading.Lock()
        self._threads: List[dict] = []
        #: the thread of this client's own run command (its records come
        #: back to :meth:`_on_record` too and are sent once, by it)
        self._runner: Optional[threading.Thread] = None

    # -- wire --------------------------------------------------------------

    def send(self, msg: Dict[str, Any]) -> None:
        with self._wlock:
            self._seq += 1
            msg["seq"] = self._seq
            write_message(self.wfile, msg)

    def event(self, event: str, body: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {"type": "event", "event": event}
        if body is not None:
            msg["body"] = body
        self.send(msg)

    def serve(self) -> None:
        """Answer requests until ``disconnect`` or the end of the stream."""
        while not self.closed:
            try:
                msg = read_message(self.rfile)
            except (OSError, ValueError):
                msg = None
            if msg is None:
                break
            if msg.get("type") == "request":
                self.handle(msg)
        self.closed = True
        self._leave()

    def handle(self, req: Dict[str, Any]) -> None:
        cmd = req.get("command", "")
        rep: Dict[str, Any] = {"type": "response", "request_seq": req.get("seq"),
                               "command": cmd, "success": True}
        fn: Optional[Callable] = getattr(self, "on_" + cmd.replace("/", "_"), None)
        after = None
        try:
            if fn is None:
                raise DebugError(f"unsupported request {cmd!r}")
            out = fn(req.get("arguments") or {})
            body, after = out if isinstance(out, tuple) else (out, None)
            if body is not None:
                rep["body"] = body
        except Exception as e:              # any failure is a failed response
            rep["success"] = False
            rep["message"] = str(e.args[0] if isinstance(e, KeyError) and e.args else e)
            if hasattr(e, "to_json"):       # e.g. a refused reload: why
                rep["body"] = {"error": e.to_json()}
        self.send(rep)
        if after is not None:
            after()

    # -- session -----------------------------------------------------------

    def _live(self) -> Live:
        if self.live is None:
            raise DebugError("no debug session (launch or attach first)")
        return self.live

    def _join(self, live: Live) -> Live:
        live.debug.add_listener(self._on_record)
        return live

    def _leave(self) -> None:
        if self.live is not None:
            self.live.debug.remove_listener(self._on_record)

    def _on_record(self, rec: dict) -> None:
        """A record of the session's run asked for by someone else (a
        ``pssc dbg`` command, another client): its events."""
        if self.closed or threading.current_thread() is self._runner:
            return
        if rec.get("event") == "continued":
            thread = rec.get("thread")
            self.event("continued", {"threadId": thread if thread is not None else 0,
                                     "allThreadsContinued": True})
        else:
            self.stop_events(rec)

    def _run(self, fn: Callable[[], dict]) -> None:
        """Run ``fn`` (a run command) in its own thread; its stop record
        becomes events. A run still going when ``fn``'s wait runs out is
        waited for again, until it stops or this client goes."""
        live = self._live()
        if self.busy:
            raise DebugError("the run is going (pause first)")
        self.busy = True

        def go():
            try:
                rec = fn()
                while rec.get("event") == "running" and self.live is live:
                    rec = live.debug.wait()
            except Exception as e:
                self.event("output", {"category": "stderr", "output": f"{e}\n"})
                return
            finally:
                self.busy = False
            if self.live is live:
                self.stop_events(rec)
        self._runner = threading.Thread(target=go, name="dap-run", daemon=True)
        self._runner.start()

    def _path(self, file: str) -> str:
        """``file`` (a provenance name) as the editor's absolute path."""
        if os.path.isabs(file) or self.live is None:
            return file
        return os.path.normpath(os.path.join(self.live.cwd, file))

    def stop_events(self, rec: dict) -> None:
        """The events of stop record ``rec``."""
        for o in rec.get("output") or []:
            self.event("output", {"category": o.get("category", "console"),
                                  "output": o["output"] + "\n"})
        ev = rec.get("event")
        if ev == "stopped":
            reason = rec.get("reason")
            thread = rec.get("thread")
            if thread is None:              # the entry stop names none
                thread = next((t["id"] for t in self.on_threads({})["threads"]), 1)
            body = {"reason": REASONS.get(reason, "exception"),
                    "threadId": thread, "allThreadsStopped": True}
            if body["reason"] == "exception":
                body["description"] = str(reason)
                detail = rec.get("detail") or {}
                if detail.get("message"):
                    body["text"] = str(detail["message"])
            if rec.get("breakpoints"):
                body["hitBreakpointIds"] = list(rec["breakpoints"])
            self.event("stopped", body)
        elif ev == "exited":
            live = self.live
            failed = live is not None and (live.error is not None or
                                           bool(live.checks and live.checks[1]))
            self.event("exited", {"exitCode": 1 if failed else 0})
            self.event("terminated")

    # -- requests ----------------------------------------------------------

    def on_initialize(self, args):
        return dict(CAPABILITIES), lambda: self.event("initialized")

    def on_launch(self, args):
        if self.live is not None:
            raise DebugError("this client already has a session")
        test = args.get("test")
        if not test:
            raise DebugError("launch needs a 'test'")
        sources = args.get("sources") or ["."]
        if isinstance(sources, str):
            sources = [sources]
        with _CWD_LOCK:
            saved = os.getcwd()
            try:
                if args.get("cwd"):
                    os.chdir(args["cwd"])
                self.live = self._join(self.registry.start(
                    sources, test, seed=args.get("seed"),
                    platform=args.get("platform"),
                    timeout=args.get("timeout", 30.0),
                    reg_semantics=(os.path.abspath(args["regSemantics"])
                                   if args.get("regSemantics") else None)))
            finally:
                os.chdir(saved)
        self.launched = True
        self.stop_on_entry = bool(args.get("stopOnEntry", False))
        return {"session": self.live.id}

    def on_attach(self, args):
        if self.live is not None:
            raise DebugError("this client already has a session")
        sid = args.get("session")
        self.live = self._join(self.registry.get(str(sid) if sid is not None else None))
        return {"session": self.live.id}

    def on_configurationDone(self, args):
        live = self._live()
        last = live.debug.last or {}
        if not self.launched:
            # Attached: show where the session is now.
            if last.get("event") == "stopped":
                return None, lambda: self.stop_events({**last, "output": []})
            return None
        if self.stop_on_entry:
            return None, lambda: self.stop_events({**last, "output": []})
        return None, lambda: self._run(live.debug.cont)

    def on_pssc_reload(self, args):
        """*Apply change*: the session's sources (and its platform module)
        again, as :meth:`.Registry.reload`. Staged while the run has not
        ended (``applied`` false; doubles apply at once); a refusal is a
        failed response whose ``body.error`` says why."""
        live = self._live()
        with _CWD_LOCK:
            saved = os.getcwd()
            try:
                os.chdir(live.cwd)
                r = self.registry.reload(live.id, cancel=bool(args.get("cancel")),
                                         strict=bool(args.get("strict")))
            finally:
                os.chdir(saved)
        how = "applied" if r.get("applied") else "staged until the run ends"
        line = f"reload: generation {r.get('old')} -> {r.get('new')}, {how}\n"

        def after():
            self.event("output", {"category": "console", "output": line})
            if r.get("applied"):
                self.event("invalidated", {"areas": ["all"]})
        return r, after

    # -- breakpoints ---------------------------------------------------------

    def on_setBreakpoints(self, args):
        live = self._live()
        src = args.get("source") or {}
        file = src.get("path") or src.get("name")
        if not file:
            raise DebugError("setBreakpoints needs a source path")
        specs = [{"line": b["line"], "condition": b.get("condition"),
                  "hit_condition": b.get("hitCondition"),
                  "log_message": b.get("logMessage")}
                 for b in args.get("breakpoints") or []]
        out = []
        for bp in live.debug.set_breakpoints(file, specs):
            d = {"id": bp["id"], "verified": bp["verified"],
                 "line": bp.get("resolved_line", bp["line"]),
                 "source": {"path": file, "name": os.path.basename(file)}}
            if bp.get("message"):
                d["message"] = bp["message"]
            out.append(d)
        return {"breakpoints": out}

    def on_setExceptionBreakpoints(self, args):
        filters = list(args.get("filters") or [])
        took = self._live().debug.set_stop_on(filters)
        return {"breakpoints": [{"verified": f in took} for f in filters]}

    def on_dataBreakpointInfo(self, args):
        live = self._live()
        name = str(args.get("name") or "")
        ref = args.get("variablesReference")
        if ref:
            # A Registers scope names its registers relative to the frame's
            # component, a register its fields by their own name.
            name = live.debug.variables.register_path(int(ref), name)
        rid, _, msg = resolve(live.debug.watches.registers, name)
        if rid is None:
            return {"dataId": None, "description": msg or f"{name} is not a register"}
        return {"dataId": name, "description": name,
                "accessTypes": list(ACCESS), "canPersist": True}

    def on_setDataBreakpoints(self, args):
        live = self._live()
        specs = [{"path": b["dataId"], "rw": ACCESS.get(b.get("accessType"), "w"),
                  "condition": b.get("condition"),
                  "hit_condition": b.get("hitCondition")}
                 for b in args.get("breakpoints") or []]
        out = []
        for w in live.debug.set_watches(specs):
            d = {"id": w["id"], "verified": w["verified"]}
            if w.get("message"):
                d["message"] = w["message"]
            out.append(d)
        return {"breakpoints": out}

    # -- running -----------------------------------------------------------

    def on_continue(self, args):
        live = self._live()
        self._run(live.debug.cont)
        return {"allThreadsContinued": True}

    def _step(self, cmd, args):
        live = self._live()
        if self.busy:
            raise DebugError("the run is going (pause first)")
        thread = args.get("threadId")
        self._run(lambda: live.debug.step(STEPS[cmd], thread=thread))
        return None

    def on_next(self, args):
        return self._step("next", args)

    def on_stepIn(self, args):
        return self._step("stepIn", args)

    def on_stepOut(self, args):
        return self._step("stepOut", args)

    def _back(self, fn):
        # Capabilities are per server, going back per session: refuse here.
        live = self._live()
        if self.busy:
            raise DebugError("the run is going (pause first)")
        live.debug.check_back()
        self._run(fn)
        return None

    def on_stepBack(self, args):
        return self._back(self._live().debug.back)

    def on_reverseContinue(self, args):
        return self._back(self._live().debug.reverse)

    def on_pause(self, args):
        live = self._live()
        if self.busy or live.debug.going:
            waited = self.busy or live.debug.waiting
            live.debug.pause()
            if not waited:                  # nobody waits for the stop yet
                self._run(live.debug.wait)
        return None

    # -- inspection ----------------------------------------------------------

    def on_stackTrace(self, args):
        live = self._live()
        frames = live.debug.frames(args.get("threadId"))
        start = int(args.get("startFrame") or 0)
        levels = int(args.get("levels") or 0)
        shown = frames[start:start + levels] if levels else frames[start:]
        out = []
        for f in shown:
            d = {"id": f["id"], "name": f.get("name") or f"frame {f['id']}",
                 "line": 0, "column": 0}
            src = f.get("source")
            if src:
                path = self._path(src["file"])
                d.update(line=src["line"], column=src.get("col", 1),
                         source={"path": path, "name": os.path.basename(path)})
            out.append(d)
        return {"stackFrames": out, "totalFrames": len(frames)}

    def on_scopes(self, args):
        scopes = self._live().debug.scopes(args.get("frameId"))
        return {"scopes": [{"name": s["name"], "variablesReference": s["ref"],
                            "expensive": False} for s in scopes]}

    def on_variables(self, args):
        vs = self._live().debug.vars(int(args["variablesReference"]))
        return {"variables": [{"name": v["name"], "value": str(v.get("value", "")),
                               "variablesReference": v.get("ref", 0)} for v in vs]}

    def on_evaluate(self, args):
        live = self._live()
        v = live.debug.evaluate(str(args.get("expression", "")), args.get("frameId"))
        return {"result": str(v), "variablesReference": 0}

    def on_threads(self, args):
        live = self._live()
        try:
            self._threads = [{"id": t["id"], "name": t.get("name") or f"thread {t['id']}"}
                             for t in live.debug.threads()]
        except DebugError:
            pass                            # running or ended: the last ones seen
        return {"threads": list(self._threads)}

    def on_disconnect(self, args):
        self._leave()
        live, self.live = self.live, None
        self.closed = True
        if live is not None:
            end = args.get("terminateDebuggee", self.launched)
            if end and live.id in self.registry.sessions:
                self.registry.end(live.id)
        return None
