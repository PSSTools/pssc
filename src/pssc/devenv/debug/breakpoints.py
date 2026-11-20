"""Source breakpoints: ``file:line`` -> the stub's ``(coro, pc)`` pc breaks.

A debug-lowered image marks where each PSS statement starts (the LINE table,
``CoroDescriptor.stmts``: ``(pc, src_ref)``, the provenance entry giving the
statement's file and line). One source line may start statements in several
coroutines -- a function inlined at two call sites, or its called form and an
inlined copy -- so a breakpoint is set at every one of them.

A line that starts no statement (a blank line, a ``}``, a comment) moves to
the next line that does, within a coroutine whose statements span the line;
outside every coroutine the breakpoint stays unverified, as DAP has it.

Conditions, hit counts and logpoints are decided here, not in the engine: the
stub stops at every location, and :meth:`BreakpointSet.on_stop` says whether
the stop is the user's, after counting hits, evaluating the condition and
writing log messages. Expressions are evaluated by the caller's ``evaluate``
(``eval.py``, P2.2e): ``evaluate(expr) -> int``, raising on an error.
"""

from __future__ import annotations

import bisect
import dataclasses as dc
import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: (coroutine index, pc)
Location = Tuple[int, int]
Evaluate = Callable[[str], int]


@dc.dataclass
class Breakpoint:
    """One user breakpoint. ``line`` is where the user asked; ``resolved_line``
    where it was set (the next statement), with ``locations`` in the image."""

    id: int
    file: str
    line: int
    condition: Optional[str] = None
    #: ``N`` / ``== N``, ``>= N``, ``> N``, ``<= N``, ``< N``, ``% N``
    hit_condition: Optional[str] = None
    #: a logpoint: written, ``{expr}`` interpolated, and the run goes on
    log_message: Optional[str] = None
    verified: bool = False
    message: str = ""
    resolved_line: Optional[int] = None
    locations: List[Location] = dc.field(default_factory=list)
    hits: int = 0

    def to_json(self) -> dict:
        d = {"id": self.id, "file": self.file, "line": self.line,
             "verified": self.verified, "hits": self.hits}
        if self.resolved_line is not None:
            d["resolved_line"] = self.resolved_line
        for k in ("condition", "hit_condition", "log_message"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.message:
            d["message"] = self.message
        return d


_HIT_RE = re.compile(r"^\s*(==|>=|<=|>|<|%)?\s*(\d+)\s*$")


def parse_hit_condition(text: str) -> Callable[[int], bool]:
    """``hits -> stop?`` for a hit condition; a bare ``N`` means ``== N``.
    Raises ValueError on anything else."""
    m = _HIT_RE.match(text or "")
    if not m:
        raise ValueError(f"hit condition {text!r}: expected N, ==N, >=N, >N, <=N, <N or %N")
    op, n = m.group(1) or "==", int(m.group(2))
    if op == "%" and n == 0:
        raise ValueError(f"hit condition {text!r}: % 0")
    return {"==": lambda h: h == n, ">=": lambda h: h >= n,
            ">": lambda h: h > n, "<=": lambda h: h <= n,
            "<": lambda h: h < n, "%": lambda h: h % n == 0}[op]


_INTERP_RE = re.compile(r"\{([^{}]*)\}")


def interpolate(message: str, evaluate: Optional[Evaluate]) -> str:
    """A logpoint's text: each ``{expr}`` replaced by its value, or by
    ``<error: ...>`` when it cannot be evaluated. ``{{``/``}}`` are braces."""
    def one(m):
        expr = m.group(1).strip()
        if evaluate is None:
            return "<error: no evaluator>"
        try:
            return str(evaluate(expr))
        except Exception as e:      # an expression error is the log's, not ours
            return f"<error: {e}>"
    parts = message.replace("{{", "\0").replace("}}", "\1")
    return _INTERP_RE.sub(one, parts).replace("\0", "{").replace("\1", "}")


def _same_file(a: str, b: str) -> bool:
    """Whether provenance path ``a`` names the user's ``b``: equal once made
    absolute, or one is a path suffix of the other (an image built elsewhere,
    or a relative name)."""
    if not a or not b:
        return False
    if os.path.abspath(a) == os.path.abspath(b):
        return True
    pa, pb = os.path.normpath(a).split(os.sep), os.path.normpath(b).split(os.sep)
    n = min(len(pa), len(pb))
    return pa[-n:] == pb[-n:]


class LineIndex:
    """Statement starts of a model by file: ``file -> line -> [Location]``,
    and each coroutine's span of lines in each file."""

    def __init__(self, model):
        prov = model.prov.entries
        self.starts: Dict[str, Dict[int, List[Location]]] = {}
        #: file -> [(first line, last line, coro index)]
        self.spans: Dict[str, List[Tuple[int, int, int]]] = {}
        for ci, coro in enumerate(model.coros):
            span: Dict[str, List[int]] = {}
            prev = None
            for pc, sr in sorted(coro.stmts or ()):
                p = prov[sr] if 0 < sr < len(prov) else None
                if p is None or not p.file or p.line <= 0:
                    continue
                # One statement lowered as several (a rewritten check) marks
                # each part at the same place: a breakpoint stops at the first.
                at = (p.file, p.line, getattr(p, "col", 0))
                if at == prev:
                    continue
                prev = at
                self.starts.setdefault(p.file, {}).setdefault(p.line, []).append((ci, pc))
                lo_hi = span.setdefault(p.file, [p.line, p.line])
                lo_hi[0] = min(lo_hi[0], p.line)
                lo_hi[1] = max(lo_hi[1], p.line, p.line_end)
            for f, (lo, hi) in span.items():
                self.spans.setdefault(f, []).append((lo, hi, ci))

    def files(self, file: str) -> List[str]:
        """The provenance file names that are the user's ``file``."""
        return [f for f in self.starts if _same_file(f, file)]

    def resolve(self, file: str, line: int) -> Tuple[Optional[int], List[Location], str]:
        """``(resolved line, locations, message)``; no locations = unverified."""
        names = self.files(file)
        if not names:
            return None, [], f"no statements from {file} in the image"
        best = None
        for f in names:
            lines = sorted(self.starts[f])
            for lo, hi, ci in self.spans.get(f, ()):
                if not lo <= line <= hi:
                    continue
                own = [ln for ln in lines if lo <= ln <= hi
                       and any(c == ci for c, _ in self.starts[f][ln])]
                i = bisect.bisect_left(own, line)
                if i < len(own) and (best is None or own[i] < best):
                    best = own[i]
        if best is None:
            return None, [], f"no statement at or after {file}:{line}"
        locs = sorted({loc for f in names for loc in self.starts[f].get(best, ())})
        return best, locs, ""


class BreakpointSet:
    """The session's source breakpoints, set per file (DAP's
    ``setBreakpoints`` replaces a file's set), sent to the stub as pc breaks."""

    def __init__(self, model):
        self.index = LineIndex(model)
        self._by_file: Dict[str, List[Breakpoint]] = {}
        self._next_id = 1

    def set_file(self, file: str, specs: Sequence[dict]) -> List[Breakpoint]:
        """Replace ``file``'s breakpoints with ``specs`` (each ``{"line",
        "condition"?, "hit_condition"?, "log_message"?}``); returns them,
        resolved. A malformed hit condition leaves its breakpoint unverified."""
        out = [self._make(file, s) for s in specs]
        self._by_file[file] = out
        return out

    def add(self, file: str, spec: dict) -> Breakpoint:
        """Add one breakpoint to ``file``'s (a :meth:`set_file` spec)."""
        bp = self._make(file, spec)
        self._by_file[file] = self._by_file.get(file, []) + [bp]
        return bp

    def _make(self, file: str, s: dict) -> Breakpoint:
        bp = Breakpoint(id=self._next_id, file=file, line=int(s["line"]),
                        condition=s.get("condition") or None,
                        hit_condition=s.get("hit_condition") or None,
                        log_message=s.get("log_message") or None)
        self._next_id += 1
        if bp.hit_condition:
            try:
                parse_hit_condition(bp.hit_condition)
            except ValueError as e:
                bp.message = str(e)
                return bp
        bp.resolved_line, bp.locations, bp.message = self.index.resolve(file, bp.line)
        bp.verified = bool(bp.locations)
        return bp

    def all(self) -> List[Breakpoint]:
        return [bp for bps in self._by_file.values() for bp in bps]

    def by_id(self, bid: int) -> Optional[Breakpoint]:
        return next((bp for bp in self.all() if bp.id == bid), None)

    def pc_breaks(self) -> List[dict]:
        """The stub's ``set_pc_breaks`` argument: every verified location."""
        return [{"id": bp.id, "coro": c, "pc": pc}
                for bp in self.all() if bp.verified for c, pc in bp.locations]

    def apply(self, stub) -> dict:
        """Send the pc breaks to ``stub`` (a ``request(msg) -> reply`` object)."""
        return stub.request({"cmd": "set_pc_breaks",
                             "args": {"breaks": self.pc_breaks()}})

    def on_stop(self, hit_ids: Sequence[int],
                evaluate: Optional[Evaluate] = None) -> Tuple[List[int], List[str]]:
        """A ``breakpoint`` stop with the stub's ``hits``: ``(ids that stop,
        log lines)``. No ids means continue. Each hit breakpoint counts the hit
        if its condition holds (a condition that cannot be evaluated stops, so
        the user sees why); then its hit condition decides; a logpoint logs
        instead of stopping."""
        stop, logs = [], []
        for bid in dict.fromkeys(hit_ids):
            bp = self.by_id(bid)
            if bp is None:
                continue
            if bp.condition:
                try:
                    if evaluate is None:
                        raise RuntimeError("no evaluator")
                    if not evaluate(bp.condition):
                        continue
                except Exception as e:
                    bp.message = f"condition {bp.condition!r}: {e}"
                    stop.append(bid)
                    continue
            bp.hits += 1
            if bp.hit_condition and not parse_hit_condition(bp.hit_condition)(bp.hits):
                continue
            if bp.log_message is not None:
                logs.append(interpolate(bp.log_message, evaluate))
                continue
            stop.append(bid)
        return stop, logs
