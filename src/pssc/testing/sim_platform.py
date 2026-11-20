"""``pssc test --platform sim:<session>``: run the tests in a server-mode
simulation (plan P3.6c2).

The simulation (a testbench run with ``+pssvm_serve[=NAME]``, see
``pssc sim``) keeps running; each ``pssc test`` builds the image of the
sources (``pssc build --image``) to ``.pssunit/sim/<session>.zbc``, hot
reloads it into the session when it differs from the image last sent there,
then has the session run the selected tests and reports them. With
``--watch`` every save does that again: the edit-run loop without restarting
the simulator.

The session runs every selected test each round (``--changed`` selects
nothing here: the slicer's record is pyvm's). Options that need pyvm's own
run (``--json``, ``--cov``, ``--trace``, ``--image``, ``--junit``) are
refused.
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Any, Dict, Optional

PREFIX = "sim:"

#: Where the image of each session is written (a directory).
IMAGE_DIR = os.path.join(".pssunit", "sim")

#: The daemon's command socket (None: the workspace's); tests set it.
DAEMON_PATH: Optional[str] = None

#: (session, image path) -> digest of the image last reloaded there, so an
#: unchanged image is not reloaded again (the ``--watch`` loop's rounds run
#: in one process).
_SENT: Dict[tuple, str] = {}

#: Options refused with ``sim:``: (dest, option, value that means "not set").
_REFUSED = (("json", "--json", False), ("cov", "--cov", None),
            ("cov_scope", "--cov-scope", None), ("trace", "--trace", "off"),
            ("image", "--image", None), ("junit", "--junit", None),
            ("seeds", "--seeds", None))


def is_sim(spec: Optional[str]) -> bool:
    return bool(spec) and spec.startswith(PREFIX)


def argv_is_sim(argv) -> bool:
    """``argv`` (of ``pssc test``) names a ``sim:`` platform: the run stays
    in this process (it asks the daemon itself)."""
    argv = list(argv)
    for i, a in enumerate(argv):
        if a == "--platform" and i + 1 < len(argv) and is_sim(argv[i + 1]):
            return True
        if a.startswith("--platform=") and is_sim(a.partition("=")[2]):
            return True
    return False


def _request(msg: Dict[str, Any]) -> Dict[str, Any]:
    from ..devenv import sim_cmd
    reply = sim_cmd._request(dict(msg, op="sim"), DAEMON_PATH)
    if reply is None:
        return {"ok": False, "error": "no workspace daemon: no sim sessions"}
    return reply


def run(args) -> int:
    """``pssc test`` with ``--platform sim:<session>``; its exit code."""
    from .cmd import (EXIT_INTERNAL, EXIT_OK, EXIT_TESTS_FAILED, Collection,
                      TestCommandError, collect)
    from ..devenv import build_cmd

    session = args.platform[len(PREFIX):] or None
    for dest, opt, unset in _REFUSED:
        if getattr(args, dest, unset) not in (unset, None):
            raise TestCommandError(f"{opt}: not with --platform {args.platform}")
    coll = collect(args.sources, k=args.k, fixture=args.fixture,
                   group=getattr(args, "group", None),
                   tags=getattr(args, "tags", None) or ())
    if not coll.items:
        raise TestCommandError("no tests found")
    roots = sorted({it.comp for it in coll.items})
    if len(roots) > 1:
        raise TestCommandError("--platform sim: runs one root's tests; the "
                               "selection has " + ", ".join(roots))
    if args.collect_only:
        for it in coll.items:
            print(it.qname)
        return EXIT_OK
    # the image holds every test of the root, so the next round's selection
    # needs no rebuild of the session's catalog
    whole = Collection(ctx=coll.ctx, items=coll.found, found=coll.found,
                       paths=coll.paths)
    try:
        model, root = build_cmd.build(whole, root=roots[0])
    except build_cmd.BuildError as e:
        raise TestCommandError(str(e))
    data = model.to_bytes()
    path = os.path.abspath(os.path.join(IMAGE_DIR, (session or "default")
                                        + ".zbc"))
    digest = hashlib.sha256(data).hexdigest()
    key = (session, path)
    if _SENT.get(key) != digest:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        r = _request({"cmd": "reload", "session": session, "image": path,
                      "strict": False, "cancel": False, "timeout": None})
        if not r.get("ok"):
            _SENT.pop(key, None)
            print(f"pssc: error: --platform {args.platform}: "
                  f"{r.get('error')}", file=sys.stderr)
            return EXIT_INTERNAL
        _SENT[key] = digest
        if not args.quiet:
            print(f"{r['session']}: reloaded {root}, generation "
                  f"{r.get('old')} -> {r.get('new')}")
    r = _request({"cmd": "run", "session": session,
                  "sel": [it.qname for it in coll.items], "timeout": None})
    if not r.get("ok"):
        print(f"pssc: error: --platform {args.platform}: {r.get('error')}",
              file=sys.stderr)
        return EXIT_INTERNAL
    tests = r.get("tests") or []
    for t in tests:
        status = str(t.get("status", "?"))
        if args.verbose or status != "pass":
            print(f"{status.upper()}  {t.get('name')}")
    passed, failed = r.get("passed", 0), r.get("failed", 0)
    print(f"{passed} passed, {failed} failed (sim session {r['session']})")
    return EXIT_TESTS_FAILED if failed else EXIT_OK
