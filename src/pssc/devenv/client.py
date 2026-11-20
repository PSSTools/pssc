"""The thin client: sends `pssc test` / `pssc model` to the workspace daemon.

It starts the daemon on first use, restarts one that runs other code
(version or build mismatch) or that died, and returns ``None`` when no
daemon can be had, so the caller runs the command in-process. Imports only
the standard library and :mod:`.protocol` at module scope (T-G6).
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import protocol

START_TIMEOUT = 30.0


def disabled() -> bool:
    return os.environ.get("PSSC_NO_DAEMON", "") not in ("", "0")


def _connect(path: str) -> Optional[socket.socket]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        return s
    except OSError:
        s.close()
        return None


def request(path: str, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One request on a fresh connection; ``None`` if nobody answers or the
    daemon died before replying."""
    s = _connect(path)
    if s is None:
        return None
    with s:
        try:
            protocol.send(s, msg)
            return protocol.recv(s)
        except (protocol.ProtocolError, OSError):
            return None


def log_path(root: str) -> str:
    return protocol.socket_path(root)[:-len(".sock")] + ".log"


def start(root: str, path: str) -> bool:
    """Start a daemon for *root* and wait until it answers."""
    with contextlib.suppress(OSError):
        if os.path.exists(path) and _connect(path) is None:
            os.unlink(path)                # left by a crashed daemon
    env = dict(os.environ)
    env.pop("PSSC_NO_DAEMON", None)
    idle = os.environ.get("PSSC_DAEMON_IDLE")
    cmd = [sys.executable, "-m", "pssc.devenv.daemon", "--root", root,
           "--socket", path] + (["--idle", idle] if idle else [])
    with open(log_path(root), "ab") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, env=env,
                                start_new_session=True)
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        s = _connect(path)
        if s is not None:
            s.close()
            return True
        if proc.poll() is not None:
            # Exited: it failed, or lost a start race to another daemon.
            return _connect_ok(path)
        time.sleep(0.02)
    return False


def _connect_ok(path: str) -> bool:
    s = _connect(path)
    if s is None:
        return False
    s.close()
    return True


def stop(path: str, wait: float = 5.0) -> bool:
    """Ask the daemon on *path* to exit; True if one was running."""
    if request(path, {"op": "stop"}) is None:
        return False
    deadline = time.monotonic() + wait
    while os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.01)
    return True


def call(msg: Dict[str, Any], root: Optional[str] = None,
         autostart: bool = True) -> Optional[Dict[str, Any]]:
    """Send *msg* (plus our id) to *root*'s daemon, starting or restarting it
    as needed. ``None``: no daemon could serve it."""
    root = root or protocol.find_root()
    try:
        path = protocol.socket_path(root)
    except OSError:
        return None
    msg = dict(msg, **protocol.daemon_id())
    for _ in range(2):
        reply = request(path, msg)
        if reply is not None and reply.get("error") == "version mismatch":
            stop(path)
            reply = None
        if reply is not None:
            return reply
        if not autostart or not start(root, path):
            return None
    return None


def run(argv: List[str]) -> Optional[int]:
    """Run ``pssc <argv>`` in the daemon; its rc, or ``None`` to fall back."""
    reply = call({"op": "run", "argv": list(argv), "cwd": os.getcwd(),
                  "env": dict(os.environ)})
    if reply is None or not reply.get("ok"):
        return None
    sys.stdout.write(reply.get("stdout", ""))
    sys.stdout.flush()
    sys.stderr.write(reply.get("stderr", ""))
    sys.stderr.flush()
    return int(reply.get("rc", 0))


# -- `pssc daemon start|status|stop` -----------------------------------------------

def add_parser(sub) -> None:
    d = sub.add_parser("daemon", help="the workspace daemon (pssd) that serves "
                       "`pssc test` and `pssc model`")
    d.add_argument("action", choices=("start", "status", "stop"))
    d.add_argument("--json", action="store_true", help="print one JSON object")
    d.set_defaults(func=lambda a: cmd_daemon([a.action] + (["--json"] if a.json else [])))


def cmd_daemon(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="pssc daemon")
    ap.add_argument("action", choices=("start", "status", "stop"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = protocol.find_root()
    path = protocol.socket_path(root)
    if a.action == "start":
        # A simulator holding for a debugger cannot start one itself.
        reply = request(path, dict({"op": "status"}, **protocol.daemon_id()))
        if reply is not None and not protocol.same_id(reply, protocol.daemon_id()):
            stop(path)                     # stale: replace it
            reply = None
        ok = reply is not None or start(root, path)
        doc = {"root": root, "running": ok, "started": ok and reply is None}
        text = (f"pssd: running ({root})" if ok
                else f"pssd: failed to start ({root}); see {log_path(root)}")
        print(json.dumps(doc, sort_keys=True) if a.json else text)
        return 0 if ok else 1
    if a.action == "stop":
        was = stop(path)
        doc = {"root": root, "running": False, "stopped": was}
        text = f"pssd: stopped ({root})" if was else f"pssd: not running ({root})"
    else:
        reply = request(path, dict({"op": "status"}, **protocol.daemon_id()))
        if reply is None:
            doc = {"root": root, "running": False}
            text = f"pssd: not running ({root})"
        else:
            current = protocol.same_id(reply, protocol.daemon_id())
            doc = {"root": root, "running": True, "current": current,
                   "pid": reply.get("pid"), "requests": reply.get("requests"),
                   "uptime": round(time.time() - reply.get("started", time.time()), 1),
                   "idle": reply.get("idle"), "socket": path,
                   "version": reply.get("pssc"), "protocol": reply.get("v"),
                   "cache": reply.get("cache")}
            text = (f"pssd: running ({root})\n  pid {doc['pid']}, "
                    f"{doc['requests']} requests, up {doc['uptime']} s, "
                    f"idle stop {doc['idle']} s\n  pssc {doc['version']}, "
                    f"protocol {doc['protocol']}"
                    + ("\n  cache: {builds} builds, {lowered} images, {gated} gate runs, "
                       "{hits} hits".format(**doc["cache"]) if doc["cache"] else "")
                    + ("" if current else "\n  stale: the next command restarts it"))
    print(json.dumps(doc, sort_keys=True) if a.json else text)
    return 0
