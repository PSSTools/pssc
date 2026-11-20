"""``--platform replay:<trace>``: run a test against a recorded run.

The trace is what ``pssc test --trace`` wrote (NDJSON, one file per test),
or the same records in the binary form (``.pstrace``: what the C engine and
a simulation write; its events have no source line):
each bus access (``access``: rw, width, addr, data) and each user import
call (``import``: name, ret) in the order the run made them. Replay answers
the run's reads and imports from it, in order, and checks that each access
and import is the one recorded. The first that is not stops the test
(``ReplayDivergence``): the model, the test or the seed no longer does what
the recorded run did. A run that ends before the recording does diverges
too (``ReplayPlatform.left``).

Interrupts the recorded run raised (``notify`` events) are raised again
(``Ctx.raise_irq``) while the replay serves the access or import recorded
after them -- the one whose double raised them -- so the handler runs at
the same point. One recorded after the last access or import is not.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from zuspec.be.bc.interp import VMError

from . import imports as _imports
from .trace import trace_path
from .vm import Platform


class ReplayDivergence(VMError):
    """The replayed run made an access or import the recording did not."""


def _user_import(name: str) -> bool:
    base = name.rpartition("::")[2]
    return "%s::%s" % (_imports.PACKAGE, base) not in _imports.IMPORTS


def _show(d: Dict[str, Any]) -> str:
    if d.get("kind") == "import":
        s = "import %s()" % d.get("name", "?")
    else:
        s = "%s%s 0x%x" % ("read" if d.get("rw") == "r" else "write",
                           d.get("width", 0), d.get("addr", 0))
        if d.get("rw") == "w" and d.get("data") is not None:
            s += " = 0x%x" % d["data"]
    if d.get("file"):
        s += " (%s:%s)" % (d["file"], d.get("line", "?"))
    return s


def _records(path: str) -> tuple:
    """``(header, events)`` of the trace at ``path``: NDJSON, or the binary
    form (``.pstrace``: the C engine's, a simulation's), told by its magic."""
    from zuspec.be.bc.trace import binary
    with open(path, "rb") as f:
        data = f.read()
    if binary.is_binary(data):
        _, header, events = binary.read(data)
        return header, events
    lines = [ln for ln in data.decode("utf-8").splitlines() if ln.strip()]
    if not lines or "zbc_trace" not in json.loads(lines[0]):
        raise ValueError("%s: not a pssc trace (no zbc_trace header)" % path)
    return json.loads(lines[0]), [json.loads(ln) for ln in lines[1:]]


def load_trace(path: str) -> tuple:
    """``(header, steps)`` of the trace at ``path`` (``_records``): each
    step is an access or a user import, ``detail`` flattened with ``kind``/
    ``file``/``line``; ``irqs``: the interrupts recorded since the step
    before. An import the C engine could not name has the name ``""``."""
    header, events = _records(path)
    return header, steps(events)


def steps(events) -> List[Dict[str, Any]]:
    """The replay steps of trace *events* (``load_trace``); each also has
    the event's ``seq``."""
    out, irqs = [], []
    for e in events:
        kind, d = e.get("kind"), e.get("detail") or {}
        if kind == "notify" and "irq" in d:
            irqs.append(int(d["irq"]))
        elif kind == "access" or (kind == "import"
                                and _user_import(d.get("name") or "")):
            step = dict(d, kind=kind)
            if kind == "import":
                step["name"] = d.get("name") or ""
            if e.get("file"):
                step.update(file=e["file"], line=e.get("line"))
            if "seq" in e:
                step["seq"] = e["seq"]
            if irqs:
                step["irqs"], irqs = irqs, []
            out.append(step)
    return out


def same_step(exp: Optional[Dict[str, Any]], actual: Dict[str, Any],
              reads: bool = False) -> bool:
    """Whether step *actual* is the recorded step *exp*: the same import (by
    its name, without the package), or the same access (rw, width, addr; a
    write's data; with *reads*, a read's data too: ``pssc trace diff``)."""
    if exp is None or exp["kind"] != actual["kind"]:
        return False
    if actual["kind"] == "import":
        return (exp.get("name", "").rpartition("::")[2]
                == actual["name"].rpartition("::")[2])
    return (all(exp.get(k) == actual.get(k) for k in ("rw", "width", "addr"))
            and ((actual["rw"] == "r" and not reads)
                 or exp.get("data") == actual.get("data")))


def trace_for(spec_path: str, test: Optional[str]) -> str:
    """The trace file ``replay:<spec_path>`` gives ``test``: the file
    itself, or ``test``'s file in that directory (``--trace-dir``): its
    ``.ndjson`` or, failing that, its ``.pstrace``."""
    if os.path.isdir(spec_path):
        if test is None:
            raise ValueError("replay:%s is a directory; the test is not known"
                             % spec_path)
        path = trace_path(spec_path, test)
        binary = os.path.splitext(path)[0] + ".pstrace"
        if not os.path.isfile(path) and os.path.isfile(binary):
            path = binary
        if not os.path.isfile(path):
            raise ValueError("replay: no recorded trace for %s (%s)"
                             % (test, path))
        return path
    if not os.path.isfile(spec_path):
        raise ValueError("replay: %s does not exist" % spec_path)
    return spec_path


def recorded_seed(spec_path: str, test: Optional[str] = None) -> Optional[int]:
    """The seed the trace(s) of ``replay:<spec_path>`` were recorded with
    (the header's ``seed``): ``test``'s trace, or with no test, the file
    itself or the first trace in the directory. None if none is known."""
    try:
        if os.path.isdir(spec_path) and test is None:
            names = sorted(n for n in os.listdir(spec_path)
                           if n.endswith((".ndjson", ".pstrace")))
            if not names:
                return None
            path = os.path.join(spec_path, names[0])
        else:
            path = trace_for(spec_path, test)
        seed = _records(path)[0].get("seed")
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return int(seed) if isinstance(seed, int) else None


class ReplayPlatform(Platform):
    """Answers reads and user imports from a recorded trace (see the
    module). ``mem`` and ``log`` are kept as ``Platform``'s are."""

    def __init__(self, path: str, test: Optional[str] = None,
                 user_imports=()):
        super().__init__()
        self.path = path
        self.header, self.steps = load_trace(path)
        rec = self.header.get("test")
        if test is not None and rec is not None and rec != test:
            raise ValueError("replay: %s is a trace of %s, not %s"
                             % (path, rec, test))
        self.pos = 0
        self._session = None
        # every user import is answered (or diverges) here, recorded or not
        self._names = {s["name"].rpartition("::")[2] for s in self.steps
                       if s["kind"] == "import"} | set(user_imports)

    def attach_session(self, session) -> None:
        """The session the replay raises recorded interrupts in
        (``Session`` calls it)."""
        self._session = session

    def _next(self, actual: Dict[str, Any]) -> Dict[str, Any]:
        exp = self.steps[self.pos] if self.pos < len(self.steps) else None
        if not same_step(exp, actual):
            raise ReplayDivergence(
                "first divergent access (step %d of %d recorded): expected %s, "
                "got %s" % (self.pos + 1, len(self.steps),
                            _show(exp) if exp else "the end of the recording",
                            _show(actual)))
        self.pos += 1
        if exp.get("irqs") and self._session is not None:
            for irq in exp["irqs"]:
                self._session.ctx.raise_irq(irq)
        return exp

    def _read(self, width: int, addr: int) -> int:
        d = self._next({"kind": "access", "rw": "r", "width": width,
                        "addr": addr})
        v = int(d.get("data") or 0)
        Platform._write(self, width, addr, v)
        self.log[-1] = ("read", width, addr, v)
        return v

    def _write(self, width: int, addr: int, data: int) -> None:
        data &= (1 << width) - 1
        self._next({"kind": "access", "rw": "w", "width": width,
                    "addr": addr, "data": data})
        Platform._write(self, width, addr, data)

    def __getattr__(self, name):
        if name in self.__dict__.get("_names", ()):
            def call(*args):
                return int(self._next({"kind": "import", "name": name})
                           .get("ret") or 0)
            return call
        raise AttributeError(name)

    def left(self) -> Optional[str]:
        """Why the run diverged by ending early, or None: the first recorded
        step it did not make."""
        if self.pos >= len(self.steps):
            return None
        return ("first divergent access (step %d of %d recorded): expected "
                "%s, got the end of the test" % (
                    self.pos + 1, len(self.steps), _show(self.steps[self.pos])))
