"""Test traces (P2.10a): a run's events, as NDJSON, with source lines.

A :class:`TraceWriter` is a be-bc trace sink: give it to a Session
(``events=w, watch=w.watch``) and it writes one JSON line per event to a
text stream. The format is be-bc's canonical JSONL, version 2
(``zuspec.be.bc.trace.schema``): a header line (``{"zbc_trace": 2}``, here
with the test's name and seed added), then one event per line, so
``schema.parse`` reads a trace back. Each event that has a source
reference also gets its ``file`` and ``line`` (from the model's source
table), so a trace can be read without the model that wrote it.

With ``--cov`` the session's one events sink is the ``Coverage``; it passes
every event on to the trace (``Coverage.forward``), and the session
watches what both need (:func:`session_sink`).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, TextIO, Tuple

from zuspec.be.bc.trace import events as ev
from zuspec.be.bc.trace.schema import TRACE_SCHEMA_VERSION
from zuspec.be.bc.trace.sink import TraceSink

from .bc_session import decode_access


#: ``--trace``'s directory, unless ``--trace-dir`` says otherwise.
TRACE_DIR = os.path.join(".pssunit", "traces")


def trace_path(directory: str, test_id: str) -> str:
    """The file ``--trace`` writes ``test_id``'s events to:
    ``<directory>/<test-id>.ndjson``, ``::`` spelled ``.`` and any other
    character a file name should not hold spelled ``_``."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", test_id.replace("::", "."))
    return os.path.join(directory, name + ".ndjson")


def _dumps(d: Dict[str, Any]) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


class TraceWriter(TraceSink):
    """Writes each event, with its source line, to ``stream``. ``watch``:
    the events the run should raise for the trace (all, by default).
    ``clock``: when given, each event also gets ``t``, what it returns (the
    sim time in fs, in a simulation). An ``access`` to a register (debug
    image) also gets ``reg`` and ``fields`` (``bc_session.decode_access``);
    a ``stmt``'s component-field tokens (``c#<slot>``, defs/uses) become
    ``c:<path>`` (``ch[1].caps.ars``), the field's name in the tree."""

    def __init__(self, stream: TextIO, model=None, watch: int = ev.EV_ALL,
                 clock=None, **header: Any) -> None:
        super().__init__()
        self.stream, self.watch, self.clock = stream, watch, clock
        self._prov = getattr(model, "prov", None)
        self._model_comps = lambda: model.components.new_obj()
        self._model = model if getattr(model, "registers", None) else None
        #: component-tree field names, by absolute slot (built on first use)
        self._comp = None if getattr(model, "components", None) else ()
        #: how many events were written
        self.count = 0
        stream.write(_dumps(dict(header, zbc_trace=TRACE_SCHEMA_VERSION)) + "\n")

    def where(self, src_ref: int) -> Dict[str, Any]:
        """``{"file", "line"}`` of ``src_ref``, or ``{}``."""
        if not src_ref or self._prov is None:
            return {}
        try:
            e = self._prov.entries[src_ref]
        except (IndexError, KeyError, TypeError):
            return {}
        return {"file": e.file, "line": e.line}

    def emit(self, event) -> None:
        d = event.to_dict()
        if self._model is not None and event.kind == ev.ACCESS and "reg_id" in d["detail"]:
            d["detail"] = decode_access(self._model, d["detail"])
        elif event.kind == ev.STMT and self._comp != ():
            self._name_comp(d["detail"])
        d.update(self.where(event.src_ref))
        if self.clock is not None:
            d["t"] = self.clock()
        self.stream.write(_dumps(d) + "\n")
        self.count += 1

    def _name_comp(self, detail: Dict[str, Any]) -> None:
        for k in ("defs", "uses"):
            v = detail.get(k)
            if not v or not any(t.startswith("c#") for t in v):
                continue
            if self._comp is None:
                try:
                    self._comp = list(self._model_comps().field_names)
                except Exception:  # noqa: BLE001 -- no tree: keep the slots
                    self._comp = ()
                    return
            n = self._comp
            detail[k] = [f"c:{n[int(t[2:])]}" if t.startswith("c#")
                         and int(t[2:]) < len(n) else t for t in v]


def session_sink(cov=None, trace: Optional[TraceWriter] = None
                 ) -> Tuple[Optional[TraceSink], int]:
    """The ``(events, watch)`` of a session counting ``cov`` and writing
    ``trace`` (either may be ``None``)."""
    if cov is None:
        return trace, trace.watch if trace is not None else 0
    if trace is None:
        return cov, cov.WATCH
    cov.forward = trace
    return cov, cov.WATCH | trace.watch
