"""`pssc trace why`: where a register value in a trace came from (P5.3).

The target is the last write to the register (``PATH``), or, for
``PATH.field``, the last write that changed the field (else the last write).
Its statement is the last ``stmt`` event of the same frame before it. Each
value that statement reads (its ``uses``, debug images; be-bc
``interp/defuse.py``) is followed back to the last earlier statement of the
frame that wrote it (``defs``), and so on, down to a leaf:

- ``read``: the defining statement read a register (an ``access`` before the
  frame's next statement);
- ``import``: it called an import (its return value);
- ``call``: it called a function; the walk goes on into the function's last
  statement before its ``return``;
- ``input``: no earlier statement of the frame wrote it: a parameter or an
  action field, the frame's ``call`` arguments when it has one;
- ``initial``: a component field no statement in the trace wrote.

A component field (``c:<path>``) is followed across frames: the last
statement of any frame that wrote it. A ``PATH.field`` target whose statement
reads a value named ``*.field`` follows only those. A trace without ``stmt``
events (C engine, simulation) gives the access alone, with a ``note``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .cov_cmd import Sources
from .trace_cmd import TraceError, load

#: ``pssc trace why --json``'s schema
SCHEMA = "pssc.trace-why/1"

#: how many hops the walk follows from the target's statement
DEPTH = 32


def _target(events: List[Dict[str, Any]], path: str
            ) -> Tuple[int, Optional[str]]:
    """``(index into events, field)`` of the write *path* names."""
    def writes(reg):
        return [i for i, e in enumerate(events) if e["kind"] == "access"
                and e["detail"].get("rw") == "w" and e["detail"].get("reg") == reg]
    w = writes(path)
    if w:
        return w[-1], None
    reg, _, field = path.rpartition(".")
    w = [i for i in writes(reg) if field in events[i]["detail"].get("fields", {})]
    if not w:
        regs = sorted({e["detail"]["reg"] for e in events
                       if e["kind"] == "access" and "reg" in e["detail"]})
        raise TraceError(f"no write to '{path}' in the trace" + (
            f" (registers: {', '.join(regs[:8])}{' ...' if len(regs) > 8 else ''})"
            if regs else " (its accesses name no register: not a debug image)"))
    prev, hit = None, None
    for i in w:
        v = events[i]["detail"]["fields"][field]
        if v != prev:
            hit = i
        prev = v
    return hit, field


class _Walk:
    def __init__(self, events: List[Dict[str, Any]], sources: Sources) -> None:
        self.ev, self.sources = events, sources
        self.chain: List[Dict[str, Any]] = []
        self.seen = set()

    def _loc(self, e: Dict[str, Any]) -> Dict[str, Any]:
        f, n = e.get("file"), e.get("line")
        text = self.sources.lines(f) if f else None
        return {"index": e["seq"], "file": f, "line": n,
                "text": text[n - 1].strip() if text and n and 0 < n <= len(text)
                else None}

    def stmt_before(self, i: int, coro: int) -> Optional[int]:
        for j in range(i - 1, -1, -1):
            e = self.ev[j]
            if e["kind"] == "stmt" and e.get("coro", 0) == coro:
                return j
        return None

    def _definer(self, token: str, i: int, coro: int) -> Optional[int]:
        anyf = token.startswith("c:")
        for j in range(i - 1, -1, -1):
            e = self.ev[j]
            if e["kind"] == "stmt" and (anyf or e.get("coro", 0) == coro) \
                    and token in e["detail"].get("defs", ()):
                return j
        return None

    def _input(self, i: int, coro: int) -> Dict[str, Any]:
        for j in range(i - 1, -1, -1):
            e = self.ev[j]
            if e["kind"] == "call" and e.get("coro", 0) == coro:
                return {"fn": e["detail"].get("fn"), "args": e["detail"].get("args")}
        return {}

    def _effect(self, j: int) -> Tuple[Optional[str], Dict[str, Any], int]:
        """What statement event *j* did besides its own code: ``(kind,
        detail, index)`` of the register read, import or call it made before
        its frame's next statement, or ``(None, {}, j)``."""
        coro = self.ev[j].get("coro", 0)
        found: Tuple[Optional[str], Dict[str, Any], int] = (None, {}, j)
        callees = set()
        for k in range(j + 1, len(self.ev)):
            e = self.ev[k]
            c = e.get("coro", 0)
            if c == coro and e["kind"] in ("stmt", "return"):
                break
            d = e["detail"]
            if e["kind"] == "call" and c != coro:
                callees.add(c)
            if c != coro and c not in callees:
                continue
            if c == coro and e["kind"] == "access" and d.get("rw") == "r":
                found = ("read", {x: d[x] for x in ("reg", "addr", "data", "fields")
                                  if x in d}, k)
            elif c == coro and e["kind"] == "import":
                found = ("import", {"name": d.get("name"), "ret": d.get("ret")}, k)
            elif c != coro and e["kind"] == "return" and found[0] in (None, "call"):
                found = ("call", {"fn": d.get("fn"), "retval": d.get("retval")}, k)
        return found

    def follow(self, uses, i: int, coro: int, depth: int, via: int) -> None:
        for tok in uses:
            if depth > DEPTH or (tok, i, coro) in self.seen:
                continue
            self.seen.add((tok, i, coro))
            j = self._definer(tok, i, coro)
            node: Dict[str, Any] = {"token": tok, "depth": depth, "via": via}
            if j is None:
                if tok.startswith("c:"):
                    node["kind"] = "initial"
                else:
                    node["kind"] = "input"
                    node.update(self._input(i, coro))
                self.chain.append(node)
                continue
            e = self.ev[j]
            kind, detail, k = self._effect(j)
            node.update(kind=kind or "stmt", coro=e.get("coro", 0), **self._loc(e))
            node.update(detail)
            self.chain.append(node)
            me = len(self.chain) - 1
            if kind == "call":
                cc = self.ev[k].get("coro", 0)
                s = self.stmt_before(k, cc)
                if s is not None:
                    self.follow(self.ev[s]["detail"].get("uses", ()), s, cc,
                                depth + 1, me)
            self.follow(e["detail"].get("uses", ()), j, e.get("coro", 0),
                        depth + 1, me)


def why(path: str, target: str, sources: Sources,
        image: Optional[str] = None) -> Dict[str, Any]:
    """The ``pssc.trace-why/1`` document: where *target* (``REG`` or
    ``REG.field``) got its last written value in the trace at *path*."""
    header, events = load(path, image)
    i, field = _target(events, target)
    acc = events[i]
    w = _Walk(events, sources)
    t = dict(w._loc(acc), reg=acc["detail"]["reg"], data=acc["detail"].get("data"),
             addr=acc["detail"].get("addr"), coro=acc.get("coro", 0))
    if field is not None:
        t["field"], t["value"] = field, acc["detail"]["fields"][field]
    doc: Dict[str, Any] = {"schema": SCHEMA, "trace": path,
                           "test": header.get("test"), "seed": header.get("seed"),
                           "target": t, "stmt": None, "chain": []}
    s = w.stmt_before(i, t["coro"])
    if s is None:
        doc["note"] = ("no dataflow: the trace has no stmt events (C engine or "
                       "simulation trace; rerun with pssc test --trace all)")
        return doc
    st = events[s]
    doc["stmt"] = dict(w._loc(st), uses=st["detail"].get("uses", []))
    uses = st["detail"].get("uses", [])
    if field is not None:
        mine = [u for u in uses if u.endswith("." + field)]
        uses = mine or uses
    w.follow(uses, s, t["coro"], 0, -1)
    doc["chain"] = w.chain
    return doc


def _what(n: Dict[str, Any]) -> str:
    k = n["kind"]
    if k == "read":
        return f"read {n.get('reg') or hex(n.get('addr', 0))} = {n.get('data')}"
    if k == "import":
        return f"import {n.get('name')} -> {n.get('ret')}"
    if k == "call":
        return f"call {n.get('fn')} -> {n.get('retval')}"
    if k == "input":
        return (f"operation input (call {n['fn']} args {n.get('args')})"
                if n.get("fn") else "operation input")
    if k == "initial":
        return "component field, not written in the trace (initial value)"
    return "set"


def text(doc: Dict[str, Any], out) -> None:
    t = doc["target"]
    what = t["reg"] + (f".{t['field']} = {t['value']}" if "field" in t else "")
    print(f"{what}: written {t['data']} at event {t['index']}"
          + (f", {t['file']}:{t['line']}" if t.get("file") else ""), file=out)
    if doc.get("note"):
        print(doc["note"], file=out)
        return
    st = doc["stmt"]
    print(f"  by {st['file']}:{st['line']}" + (f"  | {st['text']}" if st.get("text")
                                                 else ""), file=out)
    for n in doc["chain"]:
        pad = "  " * (n["depth"] + 2)
        where = (f" at {n['file']}:{n['line']}" if n.get("file") else "")
        src = f"  | {n['text']}" if n.get("text") else ""
        print(f"{pad}{n['token']}: {_what(n)}{where}{src}", file=out)
