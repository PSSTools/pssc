"""`pssd`: the per-workspace daemon behind `pssc test` and `pssc model`.

It keeps the imported pipeline (parser, translator, targets, runner) warm,
keeps what `pssc test` builds keyed on source hashes (:mod:`.cache`), and
runs each request through :func:`pssc.cli.main` in the daemon's own process
(no fork: ``pssc dbg`` sessions live here between requests), with the
client's cwd, environment, stdout and stderr, so a served command prints the
same bytes as an in-process one (``PSSC_NO_DAEMON=1``). Requests are served
one at a time. With no request for ``idle`` seconds (and no DAP client
connected) it exits, ending the ``pssc dbg`` sessions it holds
(:mod:`.debug.sessions`).

Next to the command socket it serves the Debug Adapter Protocol on
``<socket>.dap`` (:func:`dap_path`; ``pssc dap`` relays to it), one
thread per client, on the same sessions as ``pssc dbg``, and takes
server-mode testbenches (``+pssvm_serve``) on ``<socket>.sim``
(:mod:`.sim`); it does not idle-stop while one is connected.

Started by :mod:`pssc.devenv.client` as
``python -m pssc.devenv.daemon --root R --socket S [--idle SECONDS]``.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import socket
import sys
import threading
import time
from typing import Any, Dict

from . import protocol

DEFAULT_IDLE = 900.0


class Daemon:
    def __init__(self, root: str, path: str, idle: float = DEFAULT_IDLE):
        self.root = root
        self.path = path
        self.idle = idle
        self.started = time.time()
        self.last = self.started
        self.requests = 0
        self.id = protocol.daemon_id()
        self.dap_path = dap_path(path)
        from .sim import sim_path
        self.sim_path = sim_path(path)
        #: DAP clients connected now (the daemon does not idle-stop under them)
        self.dap_clients = 0
        self._stop = False

    # -- requests ----------------------------------------------------------
    def handle(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        reply: Dict[str, Any] = dict(self.id)
        op = msg.get("op")
        if op == "stop":
            self._stop = True
            reply.update(ok=True)
            return reply
        if op == "status":                 # any version: `daemon status` shows it
            reply.update(ok=True, root=self.root, pid=os.getpid(),
                         started=self.started, requests=self.requests,
                         idle=self.idle, cache=_cache_stats())
            return reply
        if not protocol.same_id(msg, self.id):
            # The client stops and restarts us; our id tells it why.
            reply.update(ok=False, error="version mismatch")
            return reply
        if op == "dap":                    # where `pssc dap` connects
            reply.update(ok=True, dap=self.dap_path)
        elif op == "sim":                  # `pssc sim`: server-mode sessions
            from .sim import ops
            self.requests += 1
            reply.update(ops.handle(msg))
        elif op == "run":
            self.requests += 1
            reply.update(ok=True, **self.run(msg.get("argv") or [],
                                             msg.get("cwd") or self.root,
                                             msg.get("env")))
        else:
            reply.update(ok=False, error=f"unknown op {op!r}")
        return reply

    def run(self, argv, cwd: str, env) -> Dict[str, Any]:
        from .. import cli
        from .debug.dap import _CWD_LOCK   # DAP `launch` changes the cwd too
        with _CWD_LOCK:
            return self._run(cli, argv, cwd, env)

    def _run(self, cli, argv, cwd: str, env) -> Dict[str, Any]:
        out, err = io.StringIO(), io.StringIO()
        saved_env, saved_cwd = dict(os.environ), os.getcwd()
        saved_argv = sys.argv
        try:
            if env is not None:
                os.environ.clear()
                os.environ.update(env)
            os.chdir(cwd)
            sys.argv = ["pssc"] + list(argv)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = cli.main(list(argv))
                except SystemExit as e:   # argparse errors, --help
                    rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
                    if e.code is not None and not isinstance(e.code, int):
                        print(e.code, file=sys.stderr)
        finally:
            sys.argv = saved_argv
            os.chdir(saved_cwd)
            os.environ.clear()
            os.environ.update(saved_env)
        return {"rc": int(rc or 0), "stdout": out.getvalue(), "stderr": err.getvalue()}

    # -- serving -----------------------------------------------------------
    def serve(self) -> int:
        sock = _bind(self.path)
        if sock is None:
            return 0                       # another daemon already serves it
        dap_sock = _bind(self.dap_path)
        if dap_sock is not None:
            threading.Thread(target=self._serve_dap, args=(dap_sock,),
                             name="pssd-dap", daemon=True).start()
        sim_sock = _bind(self.sim_path)
        if sim_sock is not None:
            threading.Thread(target=self._serve_sim, args=(sim_sock,),
                             name="pssd-sim", daemon=True).start()
        try:
            sock.settimeout(min(self.idle, 1.0))
            while not self._stop:
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    if self.dap_clients or _sim_sessions():
                        self.last = time.time()
                    elif time.time() - self.last >= self.idle:
                        break
                    continue
                conn.settimeout(None)
                try:
                    msg = protocol.recv(conn)
                except (protocol.ProtocolError, OSError):
                    conn.close()
                    continue
                if msg.get("op") == "sim":
                    # `pssc sim run` waits for the testbench: not on this thread
                    threading.Thread(target=self._answer, args=(conn, msg),
                                     name="pssd-simop", daemon=True).start()
                else:
                    self._answer(conn, msg)
                self.last = time.time()
        finally:
            sock.close()
            with contextlib.suppress(OSError):
                os.unlink(self.path)
            self._stop = True
            if dap_sock is not None:
                dap_sock.close()
                with contextlib.suppress(OSError):
                    os.unlink(self.dap_path)
            if sim_sock is not None:
                sim_sock.close()
                with contextlib.suppress(OSError):
                    os.unlink(self.sim_path)
            _end_dbg_sessions()
        return 0

    def _answer(self, conn, msg) -> None:
        with conn:
            reply = self.handle(msg)
            try:
                protocol.send(conn, reply)
            except OSError:
                pass

    def _serve_dap(self, sock) -> None:
        sock.settimeout(1.0)
        while not self._stop:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:                # closed by serve()
                return
            conn.settimeout(None)
            threading.Thread(target=self._dap_client, args=(conn,),
                             name="pssd-dap-client", daemon=True).start()

    def _serve_sim(self, sock) -> None:
        from .sim.registry import REGISTRY
        sock.settimeout(1.0)
        while not self._stop:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:                # closed by serve()
                return
            conn.settimeout(None)
            threading.Thread(target=REGISTRY.accept, args=(conn,),
                             name="pssd-sim-register", daemon=True).start()
            self.last = time.time()

    def _dap_client(self, conn) -> None:
        from .debug import dap
        self.dap_clients += 1
        try:
            with conn, conn.makefile("rb") as r, conn.makefile("wb") as w:
                dap.Connection(r, w).serve()
        except OSError:
            pass
        finally:
            self.dap_clients -= 1
            self.last = time.time()


def _end_dbg_sessions() -> None:
    """End the ``pssc dbg`` sessions (they live in this process) and drop the
    sim sessions' channels when it exits."""
    for name in ("pssc.devenv.debug.sessions", "pssc.devenv.sim.registry"):
        mod = sys.modules.get(name)
        if mod is not None:
            mod.REGISTRY.end_all()


def _sim_sessions() -> int:
    mod = sys.modules.get("pssc.devenv.sim.registry")
    return len(mod.REGISTRY) if mod is not None else 0


def dap_path(path: str) -> str:
    """The DAP socket next to the command socket *path* (not ``*.sock``:
    that is a command socket)."""
    return (path[:-len(".sock")] if path.endswith(".sock") else path) + ".dap"


def _bind(path: str):
    """Bind *path*; ``None`` if a live daemon already answers on it. A stale
    socket file (its daemon crashed) is removed."""
    if os.path.exists(path):
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(path)
            return None
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(path)
        finally:
            probe.close()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(path)
    except OSError:                        # lost a start race
        sock.close()
        return None
    sock.listen(8)
    return sock


def _cache_stats() -> Dict[str, int]:
    from . import cache
    return dict(cache.stats)


def preload() -> None:
    """Import the pipeline before the first request (the ~160 ms S1 found)."""
    from .. import cli  # noqa: F401
    from ..testing import cmd, runner, vm, gate  # noqa: F401
    from . import model_cmd  # noqa: F401


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pssd")
    ap.add_argument("--root", required=True)
    ap.add_argument("--socket", required=True)
    ap.add_argument("--idle", type=float, default=DEFAULT_IDLE)
    a = ap.parse_args(argv)
    d = Daemon(a.root, a.socket, a.idle)   # id taken before the imports
    preload()
    from . import cache
    cache.enable()                         # source-hash caches (P1.18b)
    return d.serve()


if __name__ == "__main__":
    raise SystemExit(main())
