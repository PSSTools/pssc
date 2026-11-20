"""A debug stub in a server-mode simulation, seen from the daemon (plan P3.7).

:class:`RemoteStub` has the stub interface a :class:`DebugSession` drives
(``request(msg) -> reply``, ``next_event(timeout)``, ``join``) and forwards it
over a :class:`.registry.SimSession`'s channel: the testbench's shim answers
``debug`` {on} by putting a be-bc ``Stub`` on its VM and forwards the other
stub commands to it. The VM is in another process, so ``vm`` is None (no
register-base learning from its memory).

``terminate`` does not end the simulation: it takes the stub off
(``debug`` {on: false}), the calls stopped in it go on, and the session sees
an ``exited`` event with ``detached`` true.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: the stub's events (the channel also carries the testbench's: ``test``, ...)
STUB_EVENTS = ("stopped", "exited", "output", "continued")


class RemoteStub:
    vm = None

    def __init__(self, sim, timeout: Optional[float] = 30.0):
        self.sim, self.timeout = sim, timeout
        self.image = ""
        self._detached = False

    def on(self) -> Dict[str, Any]:
        """Put the stub on the simulation's VM; returns the shim's body
        ({image}). Earlier events on the channel are dropped."""
        with self.sim._cv:
            self.sim.events.clear()
        r = self.sim.request("debug", {"on": True}, timeout=self.timeout)
        if not r.get("ok"):
            raise RuntimeError((r.get("error") or {}).get("message", "debug failed"))
        body = r.get("body") or {}
        self.image = body.get("image", "")
        return body

    def off(self) -> None:
        """Take the stub off (the stopped calls go on); tolerates a closed
        session."""
        if self._detached:
            return
        self._detached = True
        from .registry import SimError
        try:
            self.sim.request("debug", {"on": False}, timeout=self.timeout)
        except SimError:
            pass

    def request(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        cmd = msg.get("cmd")
        if cmd == "terminate":
            self.off()
            return {"ok": True, "body": {}}
        from .registry import SimError
        try:
            return self.sim.request(cmd, msg.get("args"), timeout=self.timeout)
        except SimError as e:
            return {"ok": False, "error": {"message": str(e)}}

    def next_event(self, timeout: Optional[float] = None) -> Optional[dict]:
        if self._detached:
            return {"event": "exited", "body": {"detached": True}}
        import time
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            left = None if end is None else max(0.0, end - time.monotonic())
            ev = self.sim.next_event(left)
            if ev is None:
                if self.sim.closed:
                    self._detached = True
                    return {"event": "exited", "body": {"detached": True,
                                                        "closed": True}}
                return None
            if ev.get("event") in STUB_EVENTS:
                return ev

    def join(self, timeout: Optional[float] = None) -> None:
        self.off()
