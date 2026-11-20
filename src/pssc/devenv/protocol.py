"""The `pssd` wire protocol: versioned JSON frames over a Unix socket.

A frame is a 4-byte big-endian length followed by that many bytes of UTF-8
JSON. Every message carries ``v`` (:data:`PROTOCOL`), ``pssc`` (the
package version) and ``build`` (a fingerprint of the package's sources, so an
edited checkout is not served by stale code); a daemon whose id differs from
the client's is stopped and restarted (:func:`daemon_id`).

Requests (``op``):

* ``run``: ``argv``, ``cwd``, ``env`` -> ``rc``, ``stdout``, ``stderr``
* ``status`` (any version): -> ``root``, ``pid``, ``started``, ``requests``, ``idle``
* ``stop``: -> ``ok``; the daemon exits after answering (any version)

This module and the workspace helpers below import only the standard
library: the thin client uses them (T-G6).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import tempfile
from typing import Any, Dict, Optional

from ..__version__ import version as _version

PROTOCOL = 1
VERSION = _version
_HDR = struct.Struct(">I")
MAX_FRAME = 256 << 20

#: Commands the daemon serves; the rest run in the client's process.
SERVED = ("test", "model", "dbg")

#: Markers of a project root, nearest first wins.
ROOT_MARKERS = ("pyproject.toml", ".git", "flow.dv", "flow.yaml")


class ProtocolError(Exception):
    """A frame could not be read (peer closed, or not our protocol)."""


def build_id() -> str:
    """Count and newest mtime of the package's ``.py`` files (~3 ms)."""
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    newest = n = 0
    for d, dirs, files in os.walk(pkg):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                n += 1
                newest = max(newest, os.stat(os.path.join(d, f)).st_mtime_ns)
    return f"{n}:{newest}"


def daemon_id() -> Dict[str, Any]:
    return {"v": PROTOCOL, "pssc": VERSION, "build": build_id()}


def same_id(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in ("v", "pssc", "build"))


def send(sock: socket.socket, msg: Dict[str, Any]) -> None:
    data = json.dumps(msg).encode("utf-8")
    sock.sendall(_HDR.pack(len(data)) + data)


def _read(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ProtocolError("connection closed")
        buf += chunk
    return bytes(buf)


def recv(sock: socket.socket) -> Dict[str, Any]:
    (n,) = _HDR.unpack(_read(sock, _HDR.size))
    if n > MAX_FRAME:
        raise ProtocolError(f"frame of {n} bytes")
    try:
        msg = json.loads(_read(sock, n).decode("utf-8"))
    except ValueError as e:
        raise ProtocolError(f"bad frame: {e}") from None
    if not isinstance(msg, dict):
        raise ProtocolError("frame is not an object")
    return msg


def find_root(start: Optional[str] = None) -> str:
    """The workspace a command belongs to: the nearest ancestor of *start*
    (default cwd) holding a :data:`ROOT_MARKERS` file, else *start* itself."""
    here = os.path.realpath(start or os.getcwd())
    d = here
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in ROOT_MARKERS):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return here
        d = parent


def runtime_dir() -> str:
    """Where sockets and logs live: ``$PSSC_DAEMON_DIR``, else a per-user
    directory under ``$XDG_RUNTIME_DIR`` or the temp dir."""
    d = os.environ.get("PSSC_DAEMON_DIR")
    if not d:
        base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        d = os.path.join(base, f"pssd-{os.getuid()}")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def socket_path(root: str) -> str:
    """One socket per workspace, named by a hash of its root (a Unix socket
    path is limited to ~107 bytes, so the root itself can't be the name)."""
    key = hashlib.sha1(root.encode("utf-8")).hexdigest()[:16]
    return os.path.join(runtime_dir(), key + ".sock")
