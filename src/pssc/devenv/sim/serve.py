"""The testbench end of a server-mode session (see :mod:`pssc.devenv.sim`).

A reader thread queues the daemon's requests; the simulator's own thread
answers them with :meth:`Server.poll` (at a point where it is safe to run
the engine: between tests, or while the testbench waits for work), so a
handler never runs concurrently with the simulation.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import socket
import threading
import time
from typing import Any, Callable, Dict, Optional

from . import PROTOCOL, dumps, error, sim_path

#: commands the testbench answers itself (not forwarded to the debug stub)
SESSION_CMDS = ("ping", "info", "end")

#: a handler's return value that leaves the request open: the testbench
#: answers it later with :meth:`Server.reply` (``Server.seq`` is its seq)
DEFER = object()


class ServeError(Exception):
    pass


_BUILTIN = ("ping", "info", "end")


class Server:
    """Register with the daemon and answer its requests.

    *handlers* maps a command to ``fn(args) -> body`` (raise to fail it);
    they and the built-in ``ping``/``info``/``end`` run on the thread that
    calls :meth:`poll`; one that returns :data:`DEFER` answers later with
    :meth:`reply`. *direct* handlers run on the channel's reader thread
    instead. Any other command goes to the attached debug stub
    (``request(msg) -> reply``), also on the reader thread: the stub may
    have stopped the run on the thread that polls; else fails
    ``unsupported``."""

    def __init__(self, name: str = "", info: Optional[Dict[str, Any]] = None,
                 handlers: Optional[Dict[str, Callable[[dict], Any]]] = None,
                 direct: Optional[Dict[str, Callable[[dict], Any]]] = None,
                 clock: Optional[Callable[[], int]] = None):
        self.info = dict(info or {})
        #: the sim time (fs) every event carries as ``t``; None: no ``t``
        self.clock = clock
        self.info.setdefault("name", name or f"sim-{os.getpid()}")
        self.info.setdefault("pid", os.getpid())
        self.info.setdefault("cwd", os.getcwd())
        self.info["protocol"] = PROTOCOL
        self.handlers = dict(handlers or {})
        self.direct = dict(direct or {})
        self.session: Optional[str] = None
        self.ended = False                  # the daemon sent `end`
        self.closed = False
        self._sock = None
        self._q: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._wlock = threading.Lock()
        self._stub = None
        self.seq: Any = None                # the request being handled

    # -- connection -------------------------------------------------------- #
    def connect(self, path: Optional[str] = None, root: Optional[str] = None,
                start: bool = True, timeout: float = 10.0) -> str:
        """Connect to the sim socket (default: env ``PSSC_SIM_SOCKET``, else
        the workspace daemon's for *root* or the cwd, started when *start*
        and none runs) and register. Returns the session id."""
        if path is None:
            path = os.environ.get("PSSC_SIM_SOCKET") or None
        if path is None:
            from .. import client, protocol
            root = root or protocol.find_root()
            cmd = protocol.socket_path(root)
            path = sim_path(cmd)
            if start and not os.path.exists(path):
                client.start(root, cmd)
        end = time.time() + timeout
        while True:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(path)
                break
            except OSError as e:
                s.close()
                if time.time() >= end:
                    raise ServeError(f"cannot connect to {path}: {e}") from e
                time.sleep(0.05)
        r = s.makefile("rb")
        s.sendall(dumps({"seq": 0, "cmd": "register", "args": self.info}))
        try:
            reply = json.loads(r.readline() or b"null")
        except ValueError:
            reply = None
        if not isinstance(reply, dict) or not reply.get("ok"):
            s.close()
            raise ServeError(f"register refused: {reply!r}")
        self._sock = s
        self.session = reply["body"]["session"]
        threading.Thread(target=self._read, args=(r,), name="pssvm-serve",
                         daemon=True).start()
        return self.session

    def close(self) -> None:
        self.closed = True
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                self._sock.close()

    def _read(self, r) -> None:
        try:
            for line in r:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or "cmd" not in msg:
                    continue
                if self._on_reader(msg.get("cmd")):
                    reply = self.handle(msg)
                    if reply is not None:
                        self._send(reply)
                else:
                    self._q.put(msg)
        except (OSError, ValueError):
            pass
        finally:
            self.closed = True
            self._q.put(None)               # wakes a blocked poll

    def _on_reader(self, cmd) -> bool:
        if cmd in self.direct:
            return True
        return (self._stub is not None and cmd not in self.handlers
                and cmd not in _BUILTIN)

    def _send(self, msg: dict) -> None:
        if self._sock is None:
            return
        if "event" in msg and self.clock is not None:
            msg = dict(msg, body=dict(msg.get("body") or {}, t=self.clock()))
        with self._wlock, contextlib.suppress(OSError):
            self._sock.sendall(dumps(msg))

    # -- serving ------------------------------------------------------------ #
    def poll(self, timeout: Optional[float] = 0.0) -> bool:
        """Answer one queued request, waiting up to *timeout* (None: until
        one comes). False when there was none (or the channel closed)."""
        try:
            msg = self._q.get(block=timeout != 0, timeout=timeout or None)
        except queue.Empty:
            return False
        if msg is None:
            self._q.put(None)               # keep later polls from blocking
            return False
        reply = self.handle(msg)
        if reply is not None:
            self._send(reply)
        return True

    def handle(self, msg: dict) -> Optional[dict]:
        seq, cmd = msg.get("seq"), msg.get("cmd")
        args = msg.get("args") or {}
        try:
            if cmd == "ping":
                return {"seq": seq, "ok": True, "body": {"pid": os.getpid()}}
            if cmd == "info":
                return {"seq": seq, "ok": True, "body": dict(self.info)}
            if cmd == "end":
                self.ended = True
                return {"seq": seq, "ok": True, "body": {}}
            if cmd in self.direct:
                return {"seq": seq, "ok": True,
                        "body": self.direct[cmd](args) or {}}
            if cmd in self.handlers:
                self.seq = seq
                body = self.handlers[cmd](args)
                if body is DEFER:
                    return None
                return {"seq": seq, "ok": True, "body": body or {}}
            if self._stub is not None:
                return self._stub.request(msg)
        except Exception as e:             # noqa: BLE001 -- a reply, not a crash
            return error(seq, getattr(e, "code", "failed"), str(e))
        return error(seq, "unsupported", f"no handler for {cmd!r}")

    def reply(self, seq: Any, body: Optional[dict] = None,
              err: Optional[str] = None, code: str = "failed",
              detail: Optional[dict] = None) -> None:
        """Answer a request a handler deferred: *body*, or the error *err*
        (*detail*: more of it, as JSON)."""
        self._send(error(seq, code, err, detail) if err is not None
                   else {"seq": seq, "ok": True, "body": body or {}})

    def event(self, name: str, body: Optional[dict] = None) -> None:
        self._send({"event": name, "body": body or {}})

    def attach_stub(self, stub) -> None:
        """Forward debug-stub commands to *stub* and its events (when it has
        ``next_event``) to the daemon."""
        self._stub = stub
        if hasattr(stub, "next_event"):
            threading.Thread(target=self._pump, args=(stub,),
                             name="pssvm-serve-ev", daemon=True).start()

    def _pump(self, stub) -> None:
        while not self.closed and self._stub is stub:
            ev = stub.next_event(0.2)
            if ev is not None:
                self._send(ev)
