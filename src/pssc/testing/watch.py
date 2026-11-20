"""``pssc test --watch``: run ``--changed`` again each time a source is saved.

The loop runs in the process the user started, not in the daemon: each round
is one ``pssc test ... --changed`` (sent to the daemon when there is one, so
its caches stay warm), and its output reaches the terminal as the round ends.
Between rounds the loop polls the sources -- the src-specs resolved again, so
a file added to a directory or a filelist is seen -- by size and mtime.
Ctrl-C ends it; the exit code is the last round's.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, List, Optional, Sequence, Tuple

#: Seconds between two looks at the sources.
POLL = 0.3

Snapshot = Tuple[Tuple[str, int, int], ...]


def round_argv(argv: Sequence[str]) -> List[str]:
    """``argv`` for one round: without ``--watch``, with ``--changed``."""
    out = [a for a in argv if a != "--watch"]
    if "--changed" not in out:
        out.append("--changed")
    return out


def snapshot(specs: Sequence[str]) -> Snapshot:
    """Each source's path, size and mtime; a spec that does not resolve (a
    file mid-save) is part of the snapshot as an error."""
    from . import sources as _sources
    from .cmd import split_id_specs, split_line_specs
    try:
        specs = split_line_specs(split_id_specs(specs)[0])[0]
        paths = [str(p) for p in _sources.resolve_sources(specs)]
    except Exception as e:  # noqa: BLE001 -- retried on the next poll
        return (("!", 0, hash(str(e))),)
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((p, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((p, -1, 0))
    return tuple(out)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def loop(specs: Sequence[str], run_once: Callable[[], int],
         poll: float = POLL, quiet: bool = False) -> int:
    """Run ``run_once`` now and after every change to ``specs``' sources,
    until Ctrl-C; return the last round's exit code."""
    rc = 0
    try:
        while True:
            seen = snapshot(specs)
            rc = run_once()
            if not quiet:
                print("--watch: waiting for a change (Ctrl-C ends)",
                      file=sys.stderr, flush=True)
            while snapshot(specs) == seen:
                _sleep(poll)
    except KeyboardInterrupt:
        return rc


def main(argv: Sequence[str], run: Callable[[List[str]], Optional[int]]) -> int:
    """``pssc test ... --watch`` from the console entry: ``run(argv)`` runs
    one round (in the daemon) or returns None to run it here."""
    from ..cli import build_parser, main as cli_main, parse_argv
    args = parse_argv(build_parser(), list(argv))
    once = round_argv(argv)

    def run_once() -> int:
        rc = run(once)
        return cli_main(once) if rc is None else rc
    return loop(args.sources, run_once, quiet=getattr(args, "json", False))
