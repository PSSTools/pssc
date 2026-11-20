"""The ``pssc dbg`` sessions (plan P2.3): each a :class:`DebugSession` on one
test of a suite, run on the engine's debug stub in its own thread.

They live in the process that runs ``pssc dbg``: the workspace daemon, so a
session outlives the client process that started it and the next ``pssc
dbg`` command finds it there; in-process (``PSSC_NO_DAEMON=1``) they last as
long as that process. ``reload`` makes a session's edited sources its next
generation (plan P3.6). A session ends on ``end``, or when the daemon exits
(:meth:`Registry.end_all`; it exits after its idle time).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .frontend import DebugError, DebugSession


@dataclass
class Live:
    """One session: its id, the test it runs and the run's state."""

    id: str
    item: Any
    seed: int
    debug: DebugSession
    #: the model's output lines (``message``, ``print``)
    lines: List[str] = field(default_factory=list)
    #: ``run_entry``'s ``(passed, failed, skipped)`` once the run has ended
    checks: Optional[tuple] = None
    #: why the run did not end normally (the exception), if it did not
    error: Optional[BaseException] = None
    #: the directory the session was started in: its relative sources' base
    cwd: str = field(default_factory=os.getcwd)
    #: the ``vm.Session`` the run is on, and what ``reload`` lowers again:
    #: the sources, the test and the platform (``vm.Hosted`` or None)
    session: Any = None
    sources: Sequence[str] = ()
    test: str = ""
    hosted: Any = None
    #: ``--reg-semantics``: the register description file (absolute)
    reg_semantics: Optional[str] = None
    started: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.time)

    def to_json(self) -> Dict[str, Any]:
        last = self.debug.last or {}
        return {"session": self.id, "test": self.item.qname, "seed": self.seed,
                "state": last.get("event", "new"),
                "started": self.started, "touched": self.touched}


class Registry:
    """The sessions of one process, by id. ``latest``: the one a command
    without ``--session`` goes to."""

    def __init__(self):
        self.sessions: Dict[str, Live] = {}
        self.latest: Optional[str] = None
        self._next = 1
        self._lock = threading.Lock()

    def start(self, sources: Sequence[str], test: str, seed: Optional[int] = None,
              platform: Optional[str] = None,
              timeout: Optional[float] = 30.0,
              reg_semantics: Optional[str] = None) -> Live:
        """A new session on the one test of ``sources`` that ``test`` (an ID
        glob or a ``-k`` pattern) selects, lowered for debugging, its run
        stopped before its first instruction. ``platform``: as ``pssc test
        --platform``; ``reg_semantics``: as ``--reg-semantics``."""
        from ...testing import cmd, runner, vm

        coll = cmd.collect(list(sources) + ([test] if "::" in test else []),
                           k=None if "::" in test else test)
        if len(coll.items) != 1:
            names = ", ".join(it.qname for it in coll.items) or "none"
            raise DebugError(f"{test!r} selects {len(coll.items)} tests, not one "
                             f"({names})")
        [item] = coll.items
        fns = [item.decl] if getattr(item, "kind", "action") == "function" else []
        image = runner._build_image(coll.ctx, item.comp, fns, debug=True,
                                    reg_semantics=reg_semantics)
        if image.error is not None:
            raise DebugError(f"{item.qname}: {image.error}")
        try:
            entry = runner._entry_of(image, item)
        except KeyError as e:
            raise DebugError(str(e.args[0]))
        hosted = None
        spec = vm.declared_platform(platform)
        if spec:
            hosted = vm.load_platform(spec)
        if seed is None and hosted is not None:
            seed = hosted.recorded_seed(item.qname)
        if seed is None:
            seed = runner.new_seed()
        session = runner._session_for(image, seed, hosted, test=item.qname)

        live: Optional[Live] = None
        if session.engine == "c":
            d, lines = self._c_debug(session, image, item, entry, seed,
                                     timeout, lambda: live)
        else:
            d, lines = self._py_debug(
                session, image, item, entry, seed, timeout, lambda: live,
                runner._action_fields(coll.ctx, item.type_name))
        with self._lock:
            sid = str(self._next)
            self._next += 1
            live = Live(id=sid, item=item, seed=seed, debug=d, lines=lines,
                        session=session, sources=list(sources), test=test,
                        hosted=hosted, reg_semantics=reg_semantics)
            self.sessions[sid] = live
            self.latest = sid
        d.start()
        return live

    @staticmethod
    def _py_debug(session, image, item, entry, seed, timeout, live, fields):
        """The debug session of the run on the Python VM (be-bc ``Stub``)."""
        from zuspec.be.bc.interp.debug import Stub

        def run():
            try:
                live().checks = session.run_entry(
                    entry, item.qname, seed=seed,
                    field_names=fields,
                    setup=image.setup, teardown=image.teardown, new_vm=False)
            except Exception as e:
                live().error = e
                raise

        lines: List[str] = []
        session.out = lines.append         # the VM takes it when made
        session._new_vm()                  # the stub's; run() runs on it
        d = DebugSession(Stub(session.vm, run=run), image.model,
                         platform=session.platform, timeout=timeout)

        def respawn():
            """The same run from its start: a new ``vm.Session`` of the same
            image and seed on a fresh platform (P5.2 ``back``)."""
            from ...testing import runner
            lv = live()
            fresh = runner._session_for(image, seed, lv.hosted, test=item.qname)
            del lines[:]
            fresh.out = lines.append
            fresh._new_vm()
            lv.session, lv.checks, lv.error = fresh, None, None

            def rerun():
                try:
                    lv.checks = fresh.run_entry(
                        entry, item.qname, seed=seed, field_names=fields,
                        setup=image.setup, teardown=image.teardown, new_vm=False)
                except Exception as e:
                    lv.error = e
                    raise
            return Stub(fresh.vm, run=rerun), fresh.platform

        d.respawn = respawn
        return d, lines

    @staticmethod
    def _c_debug(session, image, item, entry, seed, timeout, live):
        """The debug session of the run on the C engine (``session.engine``
        "c", P4.5d): rt-eng's ``NativeStub`` over the session's platform
        memory and imports, running the root's set-up, the test function and
        its tear-down. Messages are not captured (the C engine has no
        ``out``); an action test or an operation C lacks is refused. The run
        holds its own engine of ``image.model``, so the session counts as in
        a call until it ends: a reload meanwhile is staged, as on py (the run
        finishes on N, the next on N+1)."""
        from ...testing import native

        if getattr(item, "kind", "action") != "function":
            raise DebugError(f"{item.qname}: the C engine debugs test "
                             "functions only (use engine 'py')")
        syms = {e.coro: sym for sym, e in image.model.entries.items()}
        symbols = [syms[c] for c in list(image.setup) + [entry]
                   + list(image.teardown)]

        def on_end(checks, error):
            if checks is not None:         # with the platform's ledger, as
                led = session.run_state.ledger         # vm.Session counts
                checks = (checks[0] + led.n_pass, checks[1] + led.n_fail) \
                    + tuple(checks[2:])
            live().checks, live().error = checks, error
            session._depth -= 1
            if not session._depth and session._pending is not None:
                session._apply_reload()

        if hasattr(session, "_run_state"):     # a new pssunit_pkg ledger
            session.run_state = session._run_state()
        try:
            run = native.debug_stub(image.model, symbols, seed,
                                    memory=session.memory,
                                    imports=session.imports, on_end=on_end)
        except native.NativeUnsupported as e:
            raise DebugError(f"{item.qname}: {e}")
        session._depth += 1
        return DebugSession(run.stub, image.model, platform=session.platform,
                            timeout=timeout), []

    def attach_sim(self, sim, timeout: Optional[float] = 30.0) -> Live:
        """A new session on server-mode simulation ``sim`` (a
        ``sim.registry.SimSession``, plan P3.7): a debug stub on its VM
        (:class:`..sim.remote.RemoteStub`), not stopped -- the calls SV makes
        stop at the breakpoints, and with them sim time. The model is the
        image the simulation loaded. Ending the session takes the stub off;
        the simulation goes on."""
        from types import SimpleNamespace
        from ..build_cmd import load
        from ..sim.remote import RemoteStub

        stub = RemoteStub(sim, timeout=timeout)
        body = stub.on()
        path = body.get("image") or sim.info.get("image") or ""
        if not path:
            stub.off()
            raise DebugError(f"sim {sim.id}: no image loaded")
        try:
            image, _, _ = load(path)
        except Exception as e:
            stub.off()
            raise DebugError(f"sim {sim.id}: {e}")
        d = DebugSession(stub, image.model, learn=False, timeout=timeout)
        item = SimpleNamespace(qname=f"sim:{sim.info.get('name') or sim.id}")
        with self._lock:
            sid = str(self._next)
            self._next += 1
            live = Live(id=sid, item=item, seed=0, debug=d, test=item.qname)
            self.sessions[sid] = live
            self.latest = sid
        try:
            d.start(entry=False)
        except Exception:
            self.end(sid)
            raise
        return live

    def reload(self, sid: Optional[str] = None, cancel: bool = False,
               strict: bool = False) -> Dict[str, Any]:
        """Hot reload (plan P3.6) of session ``sid`` (or the latest): its
        sources translated and lowered again become its next generation
        (``bc_session.Session.reload``; a refusal raises be-bc's
        ``ReloadRefused``), then its platform module's doubles are imported
        again (``vm.Session.reimport``). While the run has not ended the new
        generation is staged (``applied`` false): the run finishes on the
        code it started with, and the next run is on the new code."""
        from ...testing import cmd, runner

        live = self.get(sid)
        if live.session is None:
            raise DebugError(f"session {live.id} is on a simulation: reload it "
                             "with `pssc sim reload`")
        base = live.cwd
        srcs = [s if s.startswith("@") and os.path.isabs(s[1:])
                else "@" + os.path.join(base, s[1:]) if s.startswith("@")
                else os.path.join(base, s) for s in live.sources]
        test = live.test
        coll = cmd.collect(srcs + ([test] if "::" in test else []),
                           k=None if "::" in test else test)
        item = next((it for it in coll.items if it.qname == live.item.qname),
                    None)
        if item is None:
            raise DebugError(f"{live.item.qname}: not in the sources any more")
        fns = [item.decl] if getattr(item, "kind", "action") == "function" else []
        image = runner._build_image(coll.ctx, item.comp, fns, debug=True,
                                    reg_semantics=live.reg_semantics)
        if image.error is not None:
            raise DebugError(f"{item.qname}: {image.error}")
        try:
            r = live.session.reload(image.model, cancel=cancel, strict=strict)
        except RuntimeError as e:
            if hasattr(e, "to_json"):
                raise
            raise DebugError(str(e))
        r = dict(r, reimported=live.session.reimport()
                 if live.hosted is not None else [])
        return {"session": live.id, **r}

    def get(self, sid: Optional[str] = None) -> Live:
        """Session ``sid``, or the latest one."""
        sid = sid or self.latest
        live = self.sessions.get(sid) if sid else None
        if live is None:
            raise DebugError(f"no session {sid}" if sid else "no debug session")
        live.touched = time.time()
        return live

    def end(self, sid: Optional[str] = None) -> Live:
        """End session ``sid`` (or the latest): terminate its run."""
        live = self.get(sid)
        with self._lock:
            self.sessions.pop(live.id, None)
            if self.latest == live.id:
                self.latest = next(reversed(self.sessions), None)
        live.debug.end()
        return live

    def end_all(self) -> None:
        for sid in list(self.sessions):
            try:
                self.end(sid)
            except Exception:
                pass

    def list(self) -> List[Dict[str, Any]]:
        return [live.to_json() for live in self.sessions.values()]


#: this process's sessions
REGISTRY = Registry()
