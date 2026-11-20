"""Code and register coverage as a UCIS database (P2.8b-c), for covsight.

:func:`to_ucis` maps a ``pssunit.coverage/1`` report
(:meth:`pssc.testing.coverage.Coverage.report`) to an in-memory covsight
database; :func:`write_cdb` writes it as a ``.cdb`` (NCDB) file.

The shape: one design unit (``DU_MODULE``, attribute ``language=PSS``) per
component type, with one instance of the same name (the counts are per
component type, not per instance). Under it, a ``BLOCK`` per scope (a
function ``sub_c::f``, an action ``sub_c::a_t``, the type's init blocks
``sub_c``) holding a ``STMTBIN`` per statement, named by its stable path
(``2/then/0``), and a ``BRANCH`` per branch statement holding a
``BRANCHBIN`` per arm (``then``, ``else``, ...); an arm the source does not
write (the ``else`` of an ``if`` without one) is flagged ``IS_BR_ELSE``.
Source information is the point's file and line, where the report has it.

Registers (P2.8c) are covergroups: one per register type, under the
instance of the type of the component that holds the register (a unit
``registers`` when the report does not say), with a cover instance per
register instance path. Each has a coverpoint ``access`` (bins ``read``,
``write``: the access counts) and one per field (a ``CVGBIN`` per value
bin: ``bit0=1``, ``zero``, an enum item, ``cleared``). The covergroup's own
coverpoints sum its instances' counts.

The operation envelope (P5.7) is a covergroup per operation, named by the
function, under its component type's instance (attributes
``pssunit.kind=envelope``, ``pssunit.op``, ``pssunit.calls``): a
coverpoint per parameter and ``result`` (attribute ``pssunit.type``) and a
``CROSS`` per requested cross, named by its points (``m,result``).

Tests (P2.8d): with ``tests``, a ``HISTORYNODE_TEST`` per test (status,
seed, attributes ``pssunit.status``, ``pssunit.engine``,
``pssunit.platform``) and its
test-coveritem associations: each point the test's own report hit is
recorded against that point's flat bin index, with the test's hit count
(a register bin also against its covergroup's type-level bin).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterable, Optional

from covsight.core.api import (CoverData, CoverTypeT, FlagsT,
                               HistoryNodeKind, ScopeTypeT, SourceInfo,
                               SourceT, TestStatusT)
from covsight.core.api.test_data import TestData
from covsight.core.api.enums.cover_flags import BranchCoverFlagsT
from covsight.core.mem import MemFactory


def comp_of(scope: str) -> str:
    """A scope's component type: ``sub_c::f`` -> ``sub_c``; a scope with no
    ``::`` (a component type's init blocks, an action of ``pss_top``) is
    its own unit."""
    comp, sep, _ = scope.rpartition("::")
    return comp if sep else scope


def _cover(kind, hits: int) -> CoverData:
    d = CoverData(kind, 0)
    d.data = hits
    d.at_least = 1
    d.weight = 1
    return d


#: pssunit status -> UCIS test status
_STATUS = {"pass": TestStatusT.OK, "skip": TestStatusT.OK,
           "xfail": TestStatusT.OK, "fail": TestStatusT.ERROR,
           "xpass": TestStatusT.ERROR, "error": TestStatusT.FATAL}


def to_ucis(report: Dict[str, Any],
            comp: Callable[[str], str] = comp_of,
            tests: Iterable[Dict[str, Any]] = (),
            cmd: str = "pssc test"):
    """The report's statements, branch arms and registers as a covsight
    ``MemUCIS``.
    ``comp``: a scope -> the component type it is counted under.
    ``tests``: dicts ``name``, ``status`` (``pass``...), ``seed``,
    ``coverage`` (the test's own report), optionally ``engine``, ``platform``."""
    db = MemFactory.create()
    files: Dict[str, Any] = {}
    # a report point -> the (scope, local bin index) items that count it
    bins: Dict[tuple, list] = {}

    def cover(scope, keys, kind, name, hits, si):
        item = scope.createNextCover(name, _cover(kind, hits), si)
        n = len(list(scope.coverItems(CoverTypeT.ALL))) - 1
        for key in keys:
            bins.setdefault(key, []).append((scope, n))
        return item

    def where(p) -> Optional[SourceInfo]:
        f = p.get("file")
        if not f:
            return None
        fh = files.get(f)
        if fh is None:
            # the whole path: covsight's backends drop ``workdir``
            fh = files[f] = db.createFileHandle(f, "")
        return SourceInfo(fh, p.get("line", 0), 0)

    # scope -> its statements and its branches (branch id -> arms)
    scopes: Dict[str, Dict[str, Any]] = {}
    for p in report.get("statements", {}).values():
        scopes.setdefault(p["scope"], {"stmts": [], "branches": {}})["stmts"].append(p)
    for p in report.get("arms", {}).values():
        s = scopes.setdefault(p["scope"], {"stmts": [], "branches": {}})
        s["branches"].setdefault(p["branch"], []).append(p)
    stmts = report.get("statements", {})

    insts: Dict[str, Any] = {}

    def unit(name, si):
        inst = insts.get(name)
        if inst is None:
            du = db.createScope(name, si, 1, SourceT.OTHER,
                                ScopeTypeT.DU_MODULE, FlagsT.UOR_SAFE_SCOPE)
            du.setAttribute("language", "PSS")
            inst = insts[name] = db.createInstance(
                name, si, 1, SourceT.OTHER, ScopeTypeT.INSTANCE, du,
                FlagsT.ENABLED_STMT | FlagsT.ENABLED_BRANCH)
        return inst

    for scope in sorted(scopes, key=lambda s: (comp(s), s)):
        pts = scopes[scope]
        first = (pts["stmts"] or [a for b in pts["branches"].values() for a in b])[0]
        inst = unit(comp(scope), where(first))
        blk = inst.createScope(scope, where(first), 1, SourceT.OTHER,
                               ScopeTypeT.BLOCK, FlagsT.ENABLED_STMT)
        for p in pts["stmts"]:
            cover(blk, [("s", point_key(p))], CoverTypeT.STMTBIN, p["path"],
                  p["hits"], where(p))
        for bid, arms in pts["branches"].items():
            head = stmts.get(bid) or arms[0]
            path = bid[len(scope) + 1:] if bid.startswith(scope + "/") else bid
            br = blk.createScope(path, where(head), 1, SourceT.OTHER,
                                 ScopeTypeT.BRANCH, FlagsT.SCOPE_BLOCK_ISBRANCH)
            for a in arms:
                item = cover(br, [("a", point_key(a))], CoverTypeT.BRANCHBIN,
                             a["arm"], a["hits"], where(a))
                if a.get("implicit"):
                    item.setCoverFlags(int(BranchCoverFlagsT.IS_BR_ELSE))
    _registers(report.get("registers", {}), unit, cover)
    _envelope(report.get("envelope", {}), comp, unit, cover)
    _tests(db, tests, bins, cmd)
    return db


def point_key(p: Dict[str, Any]) -> tuple:
    """A statement's or arm's identity across reports."""
    return (p["scope"], p.get("path"), p.get("branch"), p.get("arm"))


def _tests(db, tests, bins: Dict[tuple, list], cmd: str) -> None:
    from covsight.core.ncdb.dfs_util import flat_bin_index
    date = time.strftime("%Y%m%d%H%M%S")
    for t in tests:
        node = db.createHistoryNode(None, t["name"], cmd, HistoryNodeKind.TEST)
        node.setTestData(TestData(
            teststatus=_STATUS.get(t.get("status"), TestStatusT.MISSING),
            toolcategory="pssc", date=date, seed=str(t.get("seed") or 0),
            cmd=cmd))
        node.setAttribute("pssunit.status", str(t.get("status")))
        for k in ("engine", "platform"):
            if t.get(k):
                node.setAttribute("pssunit." + k, str(t[k]))
        idx = len(list(db.historyNodes(HistoryNodeKind.ALL))) - 1
        for key, hits in _hits(t.get("coverage") or {}):
            for scope, local in bins.get(key, ()):
                db.record_test_association(idx, flat_bin_index(db, scope, local),
                                           hits)


def _hits(report: Dict[str, Any]):
    """(point key, hits) for every point a report hit."""
    for p in report.get("statements", {}).values():
        if p["hits"]:
            yield ("s", point_key(p)), p["hits"]
    for p in report.get("arms", {}).values():
        if p["hits"]:
            yield ("a", point_key(p)), p["hits"]
    for path, r in report.get("registers", {}).items():
        for cp, bs in _points(r).items():
            for b, n in bs.items():
                if n:
                    yield ("r", path, cp, b), n
    for op, e in report.get("envelope", {}).items():
        for kind, name, p in _env_points(e):
            for b, n in p["bins"].items():
                if n:
                    yield ("e", op, kind, name, b), n


def _points(r: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """A register's coverpoints: ``access``, then a point per field."""
    out = {"access": {"read": r["reads"], "write": r["writes"]}}
    out.update((n, f["bins"]) for n, f in r["fields"].items())
    return out


def _registers(regs: Dict[str, Any], unit, cover) -> None:
    groups: Dict[tuple, Dict[str, Any]] = {}
    for path, r in sorted(regs.items()):
        key = (r.get("comp_type") or "registers", r.get("type") or path)
        groups.setdefault(key, {})[path] = r
    for (ctype, rtype), members in groups.items():
        cg = unit(ctype, None).createCovergroup(rtype, None, 1, SourceT.OTHER)
        total: Dict[str, Dict[str, int]] = {}
        for r in members.values():
            for cp, bins in _points(r).items():
                t = total.setdefault(cp, {})
                for b, n in bins.items():
                    t[b] = t.get(b, 0) + n
        # a test's hit on a register bin counts for the type's bin too
        _fill(cg, total, cover, lambda cp, b: [("r", p, cp, b) for p in members])
        for path, r in members.items():
            _fill(cg.createCoverInstance(path, None, 1, SourceT.OTHER), _points(r),
                  cover, lambda cp, b, path=path: [("r", path, cp, b)])


def _env_points(e: Dict[str, Any]):
    """An operation's envelope points: (``p``, name, point) per parameter
    and the result, then (``x``, name, cross) per cross."""
    pts = [("p", n, p) for n, p in e["params"].items()]
    if e.get("result") is not None:
        pts.append(("p", "result", e["result"]))
    return pts + [("x", n, x) for n, x in (e.get("crosses") or {}).items()]


def _envelope(env: Dict[str, Any], comp, unit, cover) -> None:
    for op, e in sorted(env.items()):
        cg = unit(comp(op), None).createCovergroup(
            op.rpartition("::")[2] or op, None, 1, SourceT.OTHER)
        cg.setAttribute("pssunit.kind", "envelope")
        cg.setAttribute("pssunit.op", op)
        cg.setAttribute("pssunit.calls", str(e["calls"]))
        cps: Dict[str, Any] = {}
        for kind, name, p in _env_points(e):
            if kind == "p":
                cp = cps[name] = cg.createCoverpoint(name, None, 1, SourceT.OTHER)
                cp.setAttribute("pssunit.type", str(p.get("type")))
            else:
                cp = cg.createCross(name, None, 1, SourceT.OTHER,
                                    [cps[n] for n in p["points"]])
            for b, n in p["bins"].items():
                cover(cp, [("e", op, kind, name, b)], CoverTypeT.CVGBIN, b, n,
                      None)


def _fill(cg, points: Dict[str, Dict[str, int]], cover, keys) -> None:
    for name, bins in points.items():
        cp = cg.createCoverpoint(name, None, 1, SourceT.OTHER)
        for b, n in bins.items():
            cover(cp, keys(name, b), CoverTypeT.CVGBIN, b, n, None)


def write_cdb(report: Dict[str, Any], path: str, **kw) -> None:
    """Write the report as a covsight ``.cdb`` file (:func:`to_ucis`)."""
    from covsight.core.ncdb.ncdb_writer import NcdbWriter
    NcdbWriter().write(to_ucis(report, **kw), path)
