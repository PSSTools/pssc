"""`pssc dap`: a Debug Adapter Protocol server on stdin/stdout (plan P2.4).

An editor starts it as its debug adapter. It relays the stream to the
workspace daemon's DAP socket (:func:`.daemon.dap_path`), so a session it
launches is a ``pssc dbg`` session too, and ``pssc dbg`` commands see its
stops. With ``PSSC_NO_DAEMON=1``, or when no daemon can be had, it serves the
protocol in this process (:class:`.debug.dap.Connection`). Imports only the
standard library and the client at module scope (T-G6).
"""
from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
from typing import List, Optional

from . import client


def add_parser(sub) -> None:
    """Register `pssc dap`; :func:`pssc.cli.main` serves in-process (the
    console entry, :mod:`pssc._entry`, relays to the daemon first)."""
    d = sub.add_parser("dap", help="serve the Debug Adapter Protocol on stdin/stdout "
                       "(an editor's debug adapter; sessions kept by the workspace daemon)")
    d.set_defaults(func=lambda a: serve_here())


def main(argv: Optional[List[str]] = None) -> int:
    if argv:                               # --help (it takes no options)
        from ..cli import main as cli_main
        return cli_main(["dap", *argv])
    if not client.disabled():
        sock = _connect()
        if sock is not None:
            return relay(sock, sys.stdin.fileno(), sys.stdout.fileno())
    return serve_here()


def serve_here() -> int:
    from .debug import dap
    dap.Connection(sys.stdin.buffer, sys.stdout.buffer).serve()
    return 0


def _connect() -> Optional[socket.socket]:
    """The daemon's DAP socket, starting the daemon if needed; ``None``: no daemon."""
    reply = client.call({"op": "dap"})
    if reply is None or not reply.get("ok") or not reply.get("dap"):
        return None
    return client._connect(reply["dap"])


def relay(sock: socket.socket, in_fd: int, out_fd: int) -> int:
    """Copy *in_fd* to *sock* and *sock* to *out_fd* until the daemon closes it.
    Raw fds: a thread blocked in buffered ``stdin`` aborts the interpreter's exit."""
    def up():
        with contextlib.suppress(OSError):
            while data := os.read(in_fd, 65536):
                sock.sendall(data)
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_WR)

    threading.Thread(target=up, name="pssc-dap-up", daemon=True).start()
    with sock, contextlib.suppress(OSError):
        while data := sock.recv(65536):
            while data:
                data = data[os.write(out_fd, data):]
    return 0
