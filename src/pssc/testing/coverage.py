"""Code coverage of a debug-lowered model (P2.7b).

A :class:`Coverage` is a be-bc trace sink: give it to a Session
(``events=cov, watch=cov.WATCH``) and it counts the ``stmt`` and ``branch``
events the run raises. Each point has a stable ID, ``<scope>/<path>``:
*scope* is the function (``<comp type>::<fn>``), the action's coroutine
(its exec blocks: ``<action>/body/0``) or the component type (its init
blocks), and *path* the statement's place in its block structure
(``2/then/0``). An edit that only moves lines keeps every ID; the line is
kept as source information only.

The points are read from the model's coverage tables
(``CoroDescriptor.cov``), so a point the run never reached is reported
with 0 hits. A point reached at several pcs (one component type's init
block in each instance) is one point.

:func:`boundary` says which scopes count (P2.7c2): the code under test,
not the tests, fixtures, mocks, ``pssunit_pkg`` or the standard libraries.

Register coverage (P2.7d) comes from a ``RegisterFake`` platform: the
session tells the fake of its ``Coverage`` and the fake reports each
register access, decoded (``register_access``). Per register instance
path: reads, writes and, per field, value bins (:func:`_bins`). Every
register of the model is reported, read or not; the boundary does not
apply to registers (they are the device's).

Envelope coverage (P5.7): per operation under test (a public function of
a component type :func:`boundary` keeps, ``<comp type>::<fn>``), the
calls, a value bin per parameter and one for the result, and the crosses
``@pssunit_pkg::cover_cross`` asks for (:func:`envelope_ops` gives the operations, :func:`_point_bins` the bins).
Only the outermost call counts: a call of an operation from within
another (``f`` calling ``g``) is the operation's own business.
"""

from __future__ import annotations

import itertools
import json
import os
from typing import Any, Callable, Dict, Iterable, Optional, Set

from zuspec.be.bc.trace import events as ev
from zuspec.be.bc.trace.sink import TraceSink

#: the report's format version
VERSION = 1


def point_id(scope: str, path: str) -> str:
    return f"{scope}/{path}" if path else scope


class Coverage(TraceSink):
    """Counts statement and branch-arm hits, and register accesses (a
    ``RegisterFake`` session). ``forward``: a sink every
    event is passed on to (the run's own events sink, if it has one)."""

    #: the events coverage needs (``watch=``)
    WATCH = ev.EV_STMT | ev.EV_BRANCH | ev.EV_CALL

    def __init__(self, model=None, forward: Optional[TraceSink] = None,
                 ops: Optional[Dict[str, Dict]] = None) -> None:
        super().__init__()
        self.forward = forward
        #: id -> point: kind, file, line, hits (+ branch, arm, implicit for an arm)
        self.stmts: Dict[str, Dict[str, Any]] = {}
        self.arms: Dict[str, Dict[str, Any]] = {}
        #: register path -> reads, writes, fields (name -> kind, width, bins)
        self.registers: Dict[str, Dict[str, Any]] = {}
        #: the operations of the envelope (:func:`envelope_ops`), by name
        self.ops: Dict[str, Dict] = dict(ops or {})
        #: operation -> calls, params (name -> type, bins), result
        self.envelope: Dict[str, Dict[str, Any]] = {
            name: _envelope_point(spec) for name, spec in self.ops.items()}
        #: a call event's coroutine name -> its function (``<type>::<fn>``)
        self._fns: Dict[str, str] = {}
        #: (coroutine, depth) of the outermost envelope call in progress
        self._open: Optional[tuple] = None
        #: that call's point bins, by point name (for the crosses)
        self._seen: Dict[str, Optional[str]] = {}
        if model is not None:
            self.add_model(model)

    # -- points -------------------------------------------------------------

    def add_model(self, model) -> None:
        """Every coverage point of ``model``, with 0 hits if new. A point
        already counted keeps its hits and takes ``model``'s file and line:
        a hot reload's new generation (``Session.reload``) adds to the
        same points."""
        prov = getattr(model, "prov", None)
        for coro in model.coros:
            if getattr(coro, "fn_qname", None):
                self._fns[coro.name] = coro.fn_qname
            for kind, _pc, scope, path, sr, implicit in getattr(coro, "cov", None) or ():
                scope = scope or coro.name
                pid = point_id(scope, path)
                table = self.stmts if kind == "stmt" else self.arms
                if pid in table:
                    table[pid].update(_where(prov, sr))
                    continue
                p: Dict[str, Any] = {"scope": scope, "path": path, "hits": 0}
                p.update(_where(prov, sr))
                if kind == "arm":
                    branch, _, arm = path.rpartition("/")
                    p.update(branch=point_id(scope, branch), arm=arm,
                             implicit=bool(implicit))
                table[pid] = p
        for reg in (getattr(model, "registers", None) or {}).values():
            self._register(reg)

    def _register(self, reg: Dict) -> Dict[str, Any]:
        """The point of register ``reg`` (``bc_session._registers``)."""
        r = self.registers.get(reg["path"])
        if r is None:
            r = self.registers[reg["path"]] = {
                "comp": reg["comp"], "type": reg.get("type"),
                "comp_type": reg.get("comp_type"), "reads": 0, "writes": 0,
                "fields": {f["name"]: {"kind": f.get("kind", "RW"),
                                       "width": f["width"],
                                       "bins": dict.fromkeys(_bins(f), 0)}
                           for f in reg["fields"]}}
        return r

    def register_access(self, reg: Dict, op: str, data: int,
                        cleared: int = 0) -> None:
        """A ``RegisterFake`` access to ``reg``: ``data`` the value written
        or read, ``cleared`` the bits an RC/W1C side effect cleared."""
        r = self._register(reg)
        r["reads" if op == "read" else "writes"] += 1
        for f in reg["fields"]:
            bins = r["fields"][f["name"]]["bins"]
            m = (1 << f["width"]) - 1
            v = (data >> f["lsb"]) & m
            hit = [f"bit{i}={(v >> i) & 1}" for i in range(f["width"])]
            if "enum" in f:
                hit += [n for n, e in f["enum"].items() if e == v]
            else:
                hit += ["zero"] * (v == 0) + ["max"] * (v == m)
            if (cleared >> f["lsb"]) & m:
                hit.append("cleared")
            for b in hit:
                if b in bins:
                    bins[b] += 1

    # -- sink ---------------------------------------------------------------

    def emit(self, event) -> None:
        if event.kind == ev.STMT:
            self._hit(self.stmts, event.detail, None)
        elif event.kind == ev.BRANCH:
            d = event.detail
            self._hit(self.arms, d, d.get("branch", "") + "/" + d.get("arm", ""))
        elif event.kind == ev.CALL and self.ops and self._open is None:
            self._call(event.detail)
        elif event.kind == ev.RETURN and self._open is not None:
            d = event.detail
            if self._open == (d.get("fn"), d.get("depth")):
                self._open = None
                op = self._fns.get(d.get("fn"), d.get("fn"))
                res = self.envelope[op]["result"]
                if res is not None and "retval" in d:
                    _count(res, self.ops[op]["result"], d["retval"])
                    if isinstance(d["retval"], int):
                        self._seen["result"] = _point_bin(
                            self.ops[op]["result"], d["retval"])
                        _cross(self.envelope[op], self._seen, True)
        if self.forward is not None:
            self.forward.emit(event)

    def _hit(self, table, d, path) -> None:
        if path is None:
            path = d.get("path")
            if path is None:            # a model lowered without coverage
                return
        scope = d.get("scope", "")
        pid = point_id(scope, path)
        p = table.get(pid)
        if p is None:                   # a model not given to add_model
            p = table[pid] = {"scope": scope, "path": path, "hits": 0}
        p["hits"] += 1

    def _call(self, d) -> None:
        op = self._fns.get(d.get("fn"), d.get("fn"))
        spec = self.ops.get(op)
        if spec is None:
            return
        self._open = (d.get("fn"), d.get("depth"))
        e = self.envelope[op]
        e["calls"] += 1
        args = d.get("args") or ()
        self._seen = {}
        for i, p in enumerate(spec["params"]):
            if i < len(args) and p["name"] in e["params"]:
                _count(e["params"][p["name"]], p, args[i])
                if isinstance(args[i], int):
                    self._seen[p["name"]] = _point_bin(p, args[i])
        _cross(e, self._seen, False)

    def merge(self, report: Dict[str, Any]) -> None:
        """Add another ``report()``'s points and hits (``pssc test`` counts
        each test apart, a ``-j`` worker's in its own process)."""
        for key, table in (("statements", self.stmts), ("arms", self.arms)):
            for pid, p in report.get(key, {}).items():
                mine = table.get(pid)
                if mine is None:
                    table[pid] = dict(p)
                else:
                    mine["hits"] += p.get("hits", 0)
        for path, r in report.get("registers", {}).items():
            mine = self.registers.get(path)
            if mine is None:
                self.registers[path] = json.loads(json.dumps(r))
                continue
            mine["reads"] += r["reads"]
            mine["writes"] += r["writes"]
            for name, f in r["fields"].items():
                bins = mine["fields"].setdefault(
                    name, dict(f, bins={}))["bins"]
                for b, n in f["bins"].items():
                    bins[b] = bins.get(b, 0) + n
        for op, e in report.get("envelope", {}).items():
            mine = self.envelope.get(op)
            if mine is None:
                self.envelope[op] = json.loads(json.dumps(e))
                continue
            mine["calls"] += e["calls"]
            pts = [(mine["params"].setdefault(n, dict(p, bins={})), p)
                   for n, p in e["params"].items()]
            if e.get("result") is not None:
                if mine.get("result") is None:
                    mine["result"] = dict(e["result"], bins={})
                pts.append((mine["result"], e["result"]))
            for k, x in (e.get("crosses") or {}).items():
                pts.append((mine.setdefault("crosses", {}).setdefault(
                    k, dict(x, bins={})), x))
            for m, p in pts:
                for b, n in p["bins"].items():
                    m["bins"][b] = m["bins"].get(b, 0) + n

    # -- report -------------------------------------------------------------

    def report(self, keep: Optional[Callable[[str], bool]] = None) -> Dict[str, Any]:
        """The counts as a JSON-ready dict: every point by ID, and a summary
        (points hit / points) per kind. ``keep``: a point's scope -> whether
        it counts (:func:`boundary`); default, every point."""
        def table(t):
            return {k: dict(v) for k, v in sorted(t.items())
                    if keep is None or keep(v["scope"])}

        def summary(t):
            return {"total": len(t),
                    "hit": sum(1 for p in t.values() if p["hits"])}
        stmts, arms = table(self.stmts), table(self.arms)
        regs = json.loads(json.dumps(dict(sorted(self.registers.items()))))
        bins = [n for r in regs.values() for f in r["fields"].values()
                for n in f["bins"].values()]
        env = json.loads(json.dumps({k: v for k, v in sorted(self.envelope.items())
                                     if keep is None or keep(k)}))
        ebins = [n for e in env.values()
                 for p in list(e["params"].values()) + [e.get("result")]
                 + list((e.get("crosses") or {}).values())
                 if p is not None for n in p["bins"].values()]
        return {
            "version": VERSION,
            "statements": stmts,
            "arms": arms,
            "registers": regs,
            "envelope": env,
            "summary": {"statements": summary(stmts),
                        "arms": summary(arms),
                        "registers": {"total": len(regs), "hit": sum(
                            1 for r in regs.values() if r["reads"] or r["writes"])},
                        "bins": {"total": len(bins),
                                 "hit": sum(1 for n in bins if n)},
                        "envelope": {"total": len(ebins),
                                     "hit": sum(1 for n in ebins if n)}},
        }


# -- the boundary (P2.7c2) ------------------------------------------------------

#: packages whose code is never under test
STD_PKGS = ("pssunit_pkg", "std_pkg", "addr_reg_pkg", "executor_pkg",
            "sync_pkg", "sml_pkg")
UNDER_TEST = "pssunit_pkg::under_test"
NOCOVER = "pssunit_pkg::nocover"
COVER_CROSS = "pssunit_pkg::cover_cross"


def boundary(ctx, items: Iterable = (), scopes: Iterable[str] = ()
             ) -> Callable[[str], bool]:
    """Which coverage scopes count, as a predicate on a point's scope.

    The component types under test are ``scopes`` (``--cov-scope``) and the
    ``@pssunit_pkg::under_test {.comp=...}`` of the tests' root components
    and their bases. With any, a scope counts only if its type is one of
    them, a type derived from one, a base of one, or (recursively) the type
    of a sub-component of one -- or an action of such a component. With
    none, every scope counts but the excluded ones, which never do: the
    standard libraries and ``pssunit_pkg``, the tests' root components and
    their bases, ``mock_c`` components, test actions, and anything marked
    ``@pssunit_pkg::nocover`` (a component, an action, or a function).

    A scope that is no type's (a package-level function) counts only when
    no component is named under test.
    """
    from pssc import annotations
    from .catalog import FIXTURE_BASE, base_chain, function_of
    from .discover import resolve_type_name
    from .hooks import MOCK

    tm: Dict[str, Any] = ctx.type_map
    parents: Dict[str, str] = getattr(ctx, "parent_comp_names", None) or {}
    chains: Dict[str, list] = {}

    def chain(name):
        if name not in chains:
            dt = tm.get(name)
            try:
                chains[name] = base_chain(ctx, dt) if dt is not None else []
            except Exception:
                chains[name] = []
        return chains[name]

    def resolve(name):
        if name in tm:
            return name
        try:
            return resolve_type_name(ctx, name)
        except Exception:
            return None

    def anns(node, qname):
        return [a for a in getattr(node, "annotations", None) or []
                if getattr(a, "type_qname", None) == qname]

    std = {id(dt) for k, dt in tm.items() if k.split("::")[0] in STD_PKGS}

    def is_std(name):
        dt = tm[name]
        if id(dt) in std or name.split("::")[0] in STD_PKGS:
            return True
        f = getattr(getattr(dt, "loc", None), "file", None) or ""
        return os.sep + "std_libs" + os.sep in f or os.sep + "stdlib" + os.sep in f

    # -- what never counts
    items = list(items)
    dropped: Set[str] = set()
    for it in items:
        dropped.add(it.comp)
        dropped.update(c for c in chain(it.comp) if c in tm)
        if it.kind == "action":
            dropped.add(it.decl)
    for name, dt in tm.items():
        if anns(dt, NOCOVER) or MOCK in chain(name) or FIXTURE_BASE in chain(name):
            dropped.add(name)
    nocover_fns = {(name, fn.name) for name, dt in tm.items()
                   for fn in getattr(dt, "functions", None) or []
                   if anns(fn, NOCOVER)}

    # -- what is under test
    named: Set[str] = set()
    for s in scopes:
        r = resolve(s)
        if r is None:
            raise ValueError(f"--cov-scope: no component type '{s}'")
        named.add(r)
    for root in {it.comp for it in items}:
        for c in [root] + chain(root):
            for a in anns(tm.get(c), UNDER_TEST):
                r = resolve(a.values.get("comp") or "")
                if r is not None:
                    named.add(r)
    under: Optional[Set[str]] = None
    if named:
        under = set()
        todo = list(named)
        while todo:
            c = todo.pop()
            if c in under:
                continue
            under.add(c)
            todo.extend(n for n in tm if c in chain(n))       # derived
            for f in getattr(tm.get(c), "fields", None) or []:
                dt = getattr(f, "datatype", None)
                dt = getattr(dt, "element_type", None) or dt
                n = getattr(dt, "name", None)
                if n and type(dt).__name__ == "DataTypeComponent":
                    r = resolve(n)
                    if r is not None:
                        todo.append(r)
        under.update(b for c in list(under) for b in chain(c) if b in tm)

    short: Dict[str, list] = {}
    for n in tm:
        short.setdefault(n.rpartition("::")[2], []).append(n)

    def keep(scope: str) -> bool:
        name, fn = scope, None
        if "::" not in name and name not in tm and len(short.get(name, ())) == 1:
            # an action's exec coroutine is named by the action alone
            # (``abort_a/body/0``): its type is the one of that name
            name = short[name][0]
        if name not in tm:
            name, _, fn = scope.rpartition("::")
            if name not in tm:
                return under is None and name.split("::")[0] not in STD_PKGS
        if name in dropped or is_std(name):
            return False
        comp = parents.get(name)
        if comp is not None and (comp in dropped or is_std(comp)):
            return False
        if fn is not None:
            decl = function_of(ctx, name, fn)
            if decl is not None and tuple(decl.rsplit("::", 1)) in nocover_fns:
                return False
        if under is None:
            return True
        return name in under or comp in under

    return keep


# -- the operation envelope (P5.7) -----------------------------------------------

def envelope_ops(ctx, keep: Optional[Callable[[str], bool]] = None
                 ) -> Dict[str, Dict]:
    """The operations of the envelope: each public function (an exported
    operation, ``op_api.public_functions``) of each component type, named
    ``<comp type>::<fn>``, that ``keep`` (:func:`boundary`) keeps. Per
    operation, its parameters' and result's point specs
    (:func:`_point_spec`); plain data, so it goes to ``-j`` workers."""
    from pssc.targets.op_api import public_functions
    out: Dict[str, Dict] = {}
    for name, dt in sorted((getattr(ctx, "type_map", None) or {}).items()):
        if type(dt).__name__ != "DataTypeComponent":
            continue
        try:
            fns = public_functions(dt, None)
        except Exception:
            continue
        for fn in fns:
            op = f"{name}::{fn.name}"
            if keep is not None and not keep(op):
                continue
            params = []
            for a in getattr(getattr(fn, "args", None), "args", None) or ():
                p = _point_spec(getattr(a, "annotation", None))
                params.append(dict(p or {}, name=a.arg, bins=bool(p)))
            res = _point_spec(getattr(fn, "returns", None))
            points = {p["name"] for p in params if p["bins"]}
            if res is not None:
                points.add("result")
            crosses = []
            for a in getattr(fn, "annotations", None) or ():
                if getattr(a, "type_qname", None) != COVER_CROSS:
                    continue
                names = [n.strip() for n in
                         str((a.values or {}).get("params") or "").split(",")
                         if n.strip()]
                if len(names) >= 2 and set(names) <= points \
                        and names not in crosses:
                    crosses.append(names)
            out[op] = {"params": params, "result": res, "crosses": crosses}
    return out


def _point_spec(dt) -> Optional[Dict[str, Any]]:
    """A parameter's or result's type, as what its bins need: ``enum``
    (items), ``bool`` or ``int`` (width, signed); None: no coverpoint (a
    string, struct, chandle, or void)."""
    if dt is None:
        return None
    cn = type(dt).__name__
    if cn == "DataTypeEnum":
        return {"type": dt.name or "enum", "kind": "enum",
                "items": dict(getattr(dt, "items", None) or {})}
    if cn == "DataTypeBool" or (cn == "DataTypeInt" and dt.name == "bool"):
        return {"type": "bool", "kind": "bool"}
    if cn == "DataTypeInt":
        w = int(getattr(dt, "bits", 32) or 32)
        sg = bool(getattr(dt, "signed", False))
        return {"type": (f"int[{w}]" if w != 32 else "int") if sg else f"bit[{w}]",
                "kind": "int", "width": w, "signed": sg}
    return None


def _point_bins(spec: Dict) -> list:
    """A point's bins: each enum item; ``false``/``true``; for an int of up
    to 3 bits each value, else ``zero``, ``max``, ``other`` (and, signed,
    ``min`` and ``neg``). Each value falls in exactly one bin."""
    k = spec["kind"]
    if k == "enum":
        return list(spec["items"])
    if k == "bool":
        return ["false", "true"]
    w, sg = spec["width"], spec["signed"]
    if w <= 3:
        lo, hi = (-(1 << (w - 1)), (1 << (w - 1)) - 1) if sg else (0, (1 << w) - 1)
        return [str(v) for v in range(lo, hi + 1)]
    return ["min", "neg", "zero", "other", "max"] if sg \
        else ["zero", "other", "max"]


def _point_bin(spec: Dict, v: int) -> Optional[str]:
    k = spec["kind"]
    if k == "enum":
        return next((n for n, e in spec["items"].items() if e == v), None)
    if k == "bool":
        return "true" if v else "false"
    w, sg = spec["width"], spec["signed"]
    v &= (1 << w) - 1
    if sg and v >> (w - 1):
        v -= 1 << w
    if w <= 3:
        return str(v)
    if sg:
        lo, hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
        return ("min" if v == lo else "max" if v == hi else "zero" if v == 0
                else "neg" if v < 0 else "other")
    return "zero" if v == 0 else "max" if v == (1 << w) - 1 else "other"


def _envelope_point(spec: Dict) -> Dict[str, Any]:
    res = spec.get("result")
    pts = {p["name"]: p for p in spec["params"] if p.get("bins")}
    if res is not None:
        pts["result"] = res
    return {"calls": 0,
            "params": {p["name"]: {"type": p["type"],
                                   "bins": dict.fromkeys(_point_bins(p), 0)}
                       for p in spec["params"] if p.get("bins")},
            "result": None if res is None else {
                "type": res["type"], "bins": dict.fromkeys(_point_bins(res), 0)},
            "crosses": {",".join(c): {"points": list(c), "bins": dict.fromkeys(
                (",".join(t) for t in itertools.product(
                    *(_point_bins(pts[n]) for n in c))), 0)}
                for c in spec.get("crosses") or ()}}


def _cross(e: Dict, seen: Dict[str, Optional[str]], result: bool) -> None:
    """Count a call's cross bins: those without the result at the call
    (``result`` False), those with it at the return."""
    for x in (e.get("crosses") or {}).values():
        if ("result" in x["points"]) != result:
            continue
        b = [seen.get(n) for n in x["points"]]
        if None not in b:
            k = ",".join(b)
            if k in x["bins"]:
                x["bins"][k] += 1


def _count(point: Dict, spec: Dict, v) -> None:
    if not isinstance(v, int):
        return
    b = _point_bin(spec, v)
    if b in point["bins"]:
        point["bins"][b] += 1


def _bins(field: Dict) -> list:
    """A register field's value bins: each bit seen 0 and 1 (``bit<i>=0``,
    ``bit<i>=1``: toggle coverage), then each enum item, or ``zero`` and
    ``max``; ``cleared`` for an RC or W1C field (its side effect seen)."""
    out = [f"bit{i}={b}" for i in range(field["width"]) for b in (0, 1)]
    out += list(field["enum"]) if "enum" in field else ["zero", "max"]
    if field.get("kind") in ("RC", "W1C"):
        out.append("cleared")
    return out


def _where(prov, sr) -> Dict[str, Any]:
    if not sr or prov is None:
        return {}
    try:
        e = prov.entries[sr]
    except (IndexError, KeyError, TypeError):
        return {}
    return {"file": e.file, "line": e.line}
