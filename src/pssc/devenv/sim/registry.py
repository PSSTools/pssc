"""The daemon end of server-mode sessions: who is connected, and a request
API over each one's control channel (see :mod:`pssc.devenv.sim`)."""
from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from . import PROTOCOL, dumps

#: events kept per session (oldest dropped first)
EVENT_LOG = 1000


class SimError(Exception):
    pass


class SimSession:
    """One registered testbench. ``request`` may be called from any thread;
    the reader thread matches replies by ``seq`` and logs events."""

    def __init__(self, sid: str, conn, reader, info: Dict[str, Any]):
        self.id = sid
        self.info = info
        self.conn = conn
        self.connected = time.time()
        self.events: deque = deque(maxlen=EVENT_LOG)
        #: every event, numbered from 1 (``pssc sim log``; not popped)
        self.history: deque = deque(maxlen=EVENT_LOG)
        self.n_events = 0
        self.closed = False
        self._r = reader
        self._cv = threading.Condition()
        self._replies: Dict[int, dict] = {}
        self._seq = 0
        self._wlock = threading.Lock()

    def describe(self) -> Dict[str, Any]:
        d = {k: self.info.get(k) for k in ("name", "pid", "cwd", "top", "image")}
        d.update(id=self.id, connected=self.connected, closed=self.closed)
        return d

    def request(self, cmd: str, args: Optional[dict] = None,
                timeout: Optional[float] = 30.0) -> dict:
        """Send ``cmd`` and wait for its reply (the whole reply object)."""
        with self._cv:
            if self.closed:
                raise SimError(f"session {self.id} is closed")
            self._seq += 1
            seq = self._seq
        msg = {"seq": seq, "cmd": cmd}
        if args:
            msg["args"] = args
        try:
            with self._wlock:
                self.conn.sendall(dumps(msg))
        except OSError as e:
            raise SimError(f"session {self.id}: {e}") from e
        end = None if timeout is None else time.time() + timeout
        with self._cv:
            while seq not in self._replies:
                if self.closed:
                    raise SimError(f"session {self.id} closed before replying")
                left = None if end is None else end - time.time()
                if left is not None and left <= 0:
                    raise SimError(f"session {self.id}: no reply to {cmd!r}")
                self._cv.wait(left)
            return self._replies.pop(seq)

    def next_event(self, timeout: Optional[float] = None) -> Optional[dict]:
        """Pop the oldest logged event, waiting up to *timeout*."""
        end = None if timeout is None else time.time() + timeout
        with self._cv:
            while not self.events:
                left = None if end is None else end - time.time()
                if self.closed or (left is not None and left <= 0):
                    return None
                self._cv.wait(left)
            return self.events.popleft()

    def log(self, since: int = 0) -> List[dict]:
        """The kept events numbered above *since* (each with its ``n``)."""
        with self._cv:
            return [e for e in self.history if e["n"] > since]

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.conn.shutdown(socket.SHUT_RDWR)   # ends the reader thread
        with contextlib.suppress(OSError):
            self.conn.close()

    def _read(self) -> None:
        try:
            for line in self._r:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                with self._cv:
                    if "event" in msg:
                        self.events.append(msg)
                        self.n_events += 1
                        self.history.append(dict(msg, n=self.n_events))
                    elif "seq" in msg:
                        self._replies[msg["seq"]] = msg
                    self._cv.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            with self._cv:
                self.closed = True
                self._cv.notify_all()


class SimRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, SimSession] = {}
        self._next = 1

    def accept(self, conn) -> Optional[SimSession]:
        """Read ``register`` from a new connection and start its session
        (the caller's thread returns at once). ``None`` if it is not a valid
        registration (the connection is closed)."""
        r = conn.makefile("rb")
        try:
            msg = json.loads(r.readline() or b"null")
        except (OSError, ValueError):
            msg = None
        if not isinstance(msg, dict) or msg.get("cmd") != "register":
            with contextlib.suppress(OSError):
                conn.close()
            return None
        info = dict(msg.get("args") or {})
        seq = msg.get("seq", 0)
        if info.get("protocol", PROTOCOL) != PROTOCOL:
            with contextlib.suppress(OSError):
                conn.sendall(dumps({"seq": seq, "ok": False, "error": {
                    "code": "version", "message":
                    f"sim protocol {info.get('protocol')} != {PROTOCOL}"}}))
                conn.close()
            return None
        with self._lock:
            sid = f"sim{self._next}"
            self._next += 1
            s = SimSession(sid, conn, r, info)
            self._sessions[sid] = s
        conn.sendall(dumps({"seq": seq, "ok": True,
                            "body": {"session": sid, "protocol": PROTOCOL}}))
        threading.Thread(target=self._reader, args=(s,),
                         name=f"pssd-{sid}", daemon=True).start()
        return s

    def _reader(self, s: SimSession) -> None:
        s._read()
        with self._lock:
            self._sessions.pop(s.id, None)
        s.close()

    def get(self, ref: Optional[str] = None) -> SimSession:
        """A session by id or name; the only one when *ref* is None."""
        with self._lock:
            live = list(self._sessions.values())
        if ref is None:
            if len(live) != 1:
                raise SimError("no sim session" if not live else
                               "more than one sim session: name one")
            return live[0]
        for s in live:
            if ref in (s.id, s.info.get("name")):
                return s
        raise SimError(f"no sim session {ref!r}")

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [s.describe() for s in self._sessions.values()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def end_all(self) -> None:
        with self._lock:
            live = list(self._sessions.values())
        for s in live:
            s.close()


REGISTRY = SimRegistry()
