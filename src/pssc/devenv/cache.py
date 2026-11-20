"""Source-hash caches kept by the daemon (`pssd`) between requests.

A served `pssc test` keys what it builds on the bytes of every source file:
the translated context (and so the catalog discovered from it), the lowered
ZBC image of each root component, and the op-model gate's verdict. No edit, no
rebuild; any edit rebuilds parse -> link -> translate -> lower whole (S1:
pssparser cannot re-link cached units). The gate verdict is keyed on the
hashes of the files the gate does not exempt (test files are exempt), so an
edit to a test file does not re-run it.

Off unless :func:`enable` was called: only the daemon turns it on, so an
in-process run (``PSSC_NO_DAEMON=1``) builds everything every time, as before.
Only the latest build is kept: a daemon serves one workspace, and holding
older contexts would only hold memory.
"""
from __future__ import annotations

import hashlib
import os
from typing import Callable, Dict, Optional, Sequence, Tuple

_enabled = False
#: counters for `pssc daemon status`: translations (``builds``), lowered
#: images and gate runs actually done, and cache hits
stats: Dict[str, int] = dict(builds=0, lowered=0, gated=0, hits=0)

_ctx: Optional[Tuple[tuple, object]] = None
_images: Dict[tuple, object] = {}
_gate: Dict[tuple, Optional[BaseException]] = {}


def enable(on: bool = True) -> None:
    global _enabled
    _enabled = on
    clear()


def enabled() -> bool:
    return _enabled


def clear() -> None:
    global _ctx
    _ctx = None
    _images.clear()
    _gate.clear()
    for k in stats:
        stats[k] = 0


def file_hashes(paths: Sequence[str]) -> tuple:
    """``((realpath, sha256), ...)`` in the given order: the cache key."""
    out = []
    for p in paths:
        with open(p, "rb") as f:
            out.append((os.path.realpath(p), hashlib.sha256(f.read()).hexdigest()))
    return tuple(out)


def translate(paths: Sequence[str], build: Callable[[], object]) -> object:
    """The context for *paths*, built by ``build()`` unless the bytes of every
    path are those of the last build. A context with errors is not kept."""
    global _ctx
    if not _enabled:
        return build()
    key = file_hashes(paths)
    if _ctx is not None and _ctx[0] == key:
        stats["hits"] += 1
        return _ctx[1]
    ctx = build()
    stats["builds"] += 1
    _images.clear()                 # lowered from the context just replaced
    _ctx = None if getattr(ctx, "errors", None) else (key, ctx)
    return ctx


def image(ctx, key: tuple, build: Callable[[], object]) -> object:
    """A lowered image of the cached *ctx* under *key*; built once per context."""
    if not _enabled or _ctx is None or _ctx[1] is not ctx:
        return build()
    if key in _images:
        stats["hits"] += 1
        return _images[key]
    img = build()
    stats["lowered"] += 1
    _images[key] = img
    return img


def gate(key: tuple, run: Callable[[], None]) -> None:
    """Run the gate (``run()`` raises on a refusal) unless *key* has a verdict;
    re-raise a kept refusal."""
    if not _enabled:
        return run()
    if key not in _gate:
        stats["gated"] += 1
        _gate.clear()               # one verdict: the latest sources'
        try:
            run()
            _gate[key] = None
        except Exception as e:     # noqa: BLE001 -- the verdict is the exception
            _gate[key] = e
            raise
        return None
    stats["hits"] += 1
    if _gate[key] is not None:
        raise _gate[key].with_traceback(None)
    return None
