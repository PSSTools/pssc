"""`pssc trace slice|diff`: a register's accesses; two runs' first divergence (P5.3).

``slice`` lists every access to a register (``reg`` in the access event,
debug images) in trace order, each with the statement that made it: the last
``stmt`` event of the same frame before it (none in a C engine or simulation
trace).

``diff`` compares two traces step by step, a step being what a replay checks
(testing/replay.py ``steps``: each bus access and user import), with the
replay's rule (``same_step``), read data included: the first step that
differs, by its number and its event on each side. A run that ends first
diverges at the step it did not make.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..testing import replay
from .cov_cmd import Sources
from .trace_cmd import TraceError, load
from .trace_why import _Walk

#: ``pssc trace slice --json``'s schema
SLICE_SCHEMA = "pssc.trace-slice/1"
#: ``pssc trace diff --json``'s schema
DIFF_SCHEMA = "pssc.trace-diff/1"


def slice_reg(path: str, reg: str, sources: Sources,
              image: Optional[str] = None) -> Dict[str, Any]:
    """The ``pssc.trace-slice/1`` document: each access to *reg* in the
    trace at *path*, with its statement."""
    header, events = load(path, image)
    w = _Walk(events, sources)
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(events):
        d = e["detail"]
        if e["kind"] != "access" or d.get("reg") != reg:
            continue
        a = dict(w._loc(e), rw=d.get("rw"), addr=d.get("addr"),
                 data=d.get("data"), coro=e.get("coro", 0))
        if "fields" in d:
            a["fields"] = d["fields"]
        s = w.stmt_before(i, a["coro"])
        a["stmt"] = w._loc(events[s]) if s is not None else None
        out.append(a)
    if not out:
        regs = sorted({e["detail"]["reg"] for e in events
                       if e["kind"] == "access" and "reg" in e["detail"]})
        raise TraceError(f"no access to '{reg}' in the trace" + (
            f" (registers: {', '.join(regs[:8])}{' ...' if len(regs) > 8 else ''})"
            if regs else " (its accesses name no register: not a debug image)"))
    return {"schema": SLICE_SCHEMA, "trace": path, "test": header.get("test"),
            "seed": header.get("seed"), "reg": reg, "accesses": out}


def _side(path: str, sources: Sources, image: Optional[str]):
    header, events = load(path, image)
    return header, replay.steps(events)


def _step(st: Optional[Dict[str, Any]], sources: Sources
          ) -> Optional[Dict[str, Any]]:
    if st is None:
        return None
    keys = ("kind", "rw", "width", "addr", "data", "reg", "fields", "name",
            "ret", "file", "line")
    out = {k: st[k] for k in keys if k in st}
    out["index"] = st.get("seq")
    text = sources.lines(st["file"]) if st.get("file") else None
    n = st.get("line") or 0
    out["text"] = text[n - 1].strip() if text and 0 < n <= len(text) else None
    return out


def diff(a: str, b: str, sources: Sources,
         image: Optional[str] = None) -> Dict[str, Any]:
    """The ``pssc.trace-diff/1`` document: the first step where the traces
    at *a* and *b* differ (``divergence`` None when they do not)."""
    sides = [_side(p, sources, image) for p in (a, b)]
    (ha, sa), (hb, sb) = sides
    doc: Dict[str, Any] = {
        "schema": DIFF_SCHEMA,
        "a": {"trace": a, "test": ha.get("test"), "seed": ha.get("seed"),
              "steps": len(sa)},
        "b": {"trace": b, "test": hb.get("test"), "seed": hb.get("seed"),
              "steps": len(sb)},
        "divergence": None}
    for n in range(max(len(sa), len(sb))):
        x = sa[n] if n < len(sa) else None
        y = sb[n] if n < len(sb) else None
        if x is None or y is None or not replay.same_step(x, y, reads=True):
            doc["divergence"] = {"step": n + 1, "a": _step(x, sources),
                                 "b": _step(y, sources)}
            break
    return doc


def _acc(d: Dict[str, Any]) -> str:
    if d.get("kind") == "import":
        return f"import {d.get('name')} -> {d.get('ret')}"
    what = d.get("reg") or hex(d.get("addr") or 0)
    s = f"{'read' if d.get('rw') == 'r' else 'write'} {what} = {d.get('data')}"
    if d.get("fields"):
        s += " {" + ", ".join(f"{k}={v}" for k, v in d["fields"].items()) + "}"
    return s


def _where(d: Dict[str, Any]) -> str:
    return ((f" at {d['file']}:{d['line']}" if d.get("file") else "")
            + (f"  | {d['text']}" if d.get("text") else ""))


def slice_text(doc: Dict[str, Any], out) -> None:
    print(f"{doc['reg']}: {len(doc['accesses'])} accesses", file=out)
    for a in doc["accesses"]:
        st = a.get("stmt")
        by = (f"  by {st['file']}:{st['line']}" + (f"  | {st['text']}"
                                                  if st.get("text") else "")
              if st and st.get("file") else _where(a))
        print(f"  {a['index']:>6}  {_acc(dict(a, reg=None))}{by}", file=out)


def diff_text(doc: Dict[str, Any], out) -> None:
    dv = doc["divergence"]
    a, b = doc["a"], doc["b"]
    if dv is None:
        print(f"same: {a['steps']} steps (accesses and imports)", file=out)
        return
    print(f"first divergence at step {dv['step']} "
          f"(of {a['steps']} in a, {b['steps']} in b):", file=out)
    for k in ("a", "b"):
        d = dv[k]
        print(f"  {k}: " + ("the end of the run" if d is None else
                            f"event {d['index']}: {_acc(d)}{_where(d)}"), file=out)
