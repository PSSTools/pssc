"""Server-mode simulation sessions (plan P3.5).

A testbench run with ``+pssvm_serve`` connects out to the workspace daemon's
sim socket (``<socket>.sim``, :func:`sim_path`) and registers a **session**;
the connection then stays open as the session's control channel.

The channel carries one JSON object per line, in the three shapes of the
engine debug stub (zuspec-be-bc, ``debug-stub.md`` in its spec docs): requests
``{"seq", "cmd", "args"}`` (daemon -> testbench), one reply per request
``{"seq", "ok", "body"|"error"}``, and unsolicited events ``{"event",
"body"}`` (testbench -> daemon). The first message is the testbench's
``register`` request; session commands (:data:`serve.SESSION_CMDS`) are
answered by the testbench, and any other command goes to the debug stub it
has attached (``unsupported`` without one).

- :mod:`.registry`: the daemon end (``REGISTRY``: sessions, requests, events).
- :mod:`.serve`: the testbench end (``Server``), used by the pssvm shim.
"""
import json
from typing import Optional

#: version of the control channel (``register`` carries it)
PROTOCOL = 1


def sim_path(path: str) -> str:
    """The sim socket next to the command socket *path*."""
    return (path[:-len(".sock")] if path.endswith(".sock") else path) + ".sim"


def dumps(msg) -> bytes:
    """One message as a line: sorted keys, compact (as the debug stub)."""
    return (json.dumps(msg, sort_keys=True, separators=(",", ":")) + "\n").encode()


def error(seq, code: str, message: str, detail: Optional[dict] = None) -> dict:
    err = {"code": code, "message": message}
    if detail is not None:
        err["detail"] = detail
    return {"seq": seq, "ok": False, "error": err}
