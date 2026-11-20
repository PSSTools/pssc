"""`pssc cov`: read a coverage report (P2.9).

``gaps``: every point the report has with 0 hits, with the source lines
around it. ``report``: points hit / points, in all and per scope and
register. ``lcov``, ``cobertura``: the statements and branch arms as
an LCOV tracefile or Cobertura XML, for CI and editors. ``merge``: several
reports summed into one. ``diff``: the gaps on lines changed since a git
revision. A report is the ``pssunit.coverage/1`` JSON ``pssc test --cov
x.json`` writes, or the covsight ``.cdb`` ``pssc test --cov x.cdb`` writes.

A point's ``file`` is the path as the model's provenance spells it, which
may be relative to a filelist's directory rather than the cwd. A relative
path is looked up under each ``--source-root``, then the cwd, then the
report's directory; a file not found there has no context.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

#: ``pssc cov diff --json``'s schema (docs/schemas/pssc.cov-diff-1.json)
DIFF_SCHEMA = "pssc.cov-diff/1"

#: ``pssc cov gaps --json``'s schema (docs/schemas/pssc.cov-gaps-1.json)
SCHEMA = "pssc.cov-gaps/1"
#: ``pssc cov report --json``'s schema (docs/schemas/pssc.cov-report-1.json)
REPORT_SCHEMA = "pssc.cov-report/1"
#: the kinds of gap, in report order
KINDS = ("statement", "arm", "register", "bin")


class CovError(Exception):
    pass


def load(path: str) -> Dict[str, Any]:
    """The coverage report at ``path``: a ``pssunit.coverage/1`` JSON file,
    or a covsight ``.cdb`` (:func:`load_cdb`)."""
    if path.endswith(".cdb"):
        return load_cdb(path)
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except OSError as e:
        raise CovError(f"cannot read '{path}': {e.strerror}")
    except (ValueError, UnicodeDecodeError) as e:
        raise CovError(f"'{path}' is not a coverage report: {e}")
    if not isinstance(report, dict) or report.get("version") != 1 \
            or "statements" not in report:
        raise CovError(f"'{path}' is not a pssunit.coverage/1 report")
    return report


def load_cdb(path: str) -> Dict[str, Any]:
    """The ``pssunit.coverage/1`` report a ``.cdb`` that ``pssc test --cov
    x.cdb`` wrote holds (:mod:`pssc.testing.cov_ucis`'s tree, read back).
    What the database does not keep is left out: a register's ``comp``, a
    field's ``kind`` and ``width``; a register's ``type`` is its
    covergroup's name."""
    if not os.path.isfile(path):
        raise CovError(f"cannot read '{path}': No such file")
    from covsight.core.api import CoverTypeT, ScopeTypeT
    from covsight.core.api.enums.cover_flags import BranchCoverFlagsT
    from covsight.core.ncdb.ncdb_reader import NcdbReader
    from pssc.testing.coverage import point_id
    try:
        db = NcdbReader().read(path)
    except Exception as e:      # noqa: BLE001  (any malformed file)
        raise CovError(f"'{path}' is not a coverage database: {e}")

    def where(item) -> Dict[str, Any]:
        si = item.getSourceInfo()
        if si is None or si.file is None:
            return {}
        return {"file": si.file.getFileName(), "line": si.line}

    def items(scope, kind):
        return list(scope.coverItems(kind))

    def kids(scope, kind):
        return list(scope.scopes(kind))

    stmts: Dict[str, Any] = {}
    arms: Dict[str, Any] = {}
    regs: Dict[str, Any] = {}
    env: Dict[str, Any] = {}
    for inst in kids(db, ScopeTypeT.INSTANCE):
        for blk in kids(inst, ScopeTypeT.BLOCK):
            scope = blk.getScopeName()
            for it in items(blk, CoverTypeT.STMTBIN):
                p = {"scope": scope, "path": it.getName(),
                     "hits": it.getCoverData().data}
                p.update(where(it))
                stmts[point_id(scope, p["path"])] = p
            for br in kids(blk, ScopeTypeT.BRANCH):
                bpath = br.getScopeName()
                for it in items(br, CoverTypeT.BRANCHBIN):
                    path = f"{bpath}/{it.getName()}"
                    p = {"scope": scope, "path": path,
                         "hits": it.getCoverData().data}
                    p.update(where(it))
                    p.update(branch=point_id(scope, bpath), arm=it.getName(),
                             implicit=bool(it.getCoverFlags()
                                           & int(BranchCoverFlagsT.IS_BR_ELSE)))
                    arms[point_id(scope, path)] = p
        ctype = inst.getScopeName()
        for cg in kids(inst, ScopeTypeT.COVERGROUP):
            if cg.getAttribute("pssunit.kind") == "envelope":
                env[cg.getAttribute("pssunit.op")] = _envelope(cg, items, kids)
                continue
            for ci in kids(cg, ScopeTypeT.COVERINSTANCE):
                cps = {cp.getScopeName(): {it.getName(): it.getCoverData().data
                                           for it in items(cp, CoverTypeT.CVGBIN)}
                       for cp in kids(ci, ScopeTypeT.COVERPOINT)}
                acc = cps.pop("access", {})
                regs[ci.getScopeName()] = {
                    "comp": None, "type": cg.getScopeName(),
                    "comp_type": None if ctype == "registers" else ctype,
                    "reads": acc.get("read", 0), "writes": acc.get("write", 0),
                    "fields": {n: {"bins": b} for n, b in cps.items()}}
    return {"version": 1, "statements": dict(sorted(stmts.items())),
            "arms": dict(sorted(arms.items())),
            "registers": dict(sorted(regs.items())),
            "envelope": dict(sorted(env.items()))}


def _envelope(cg, items, kids) -> Dict[str, Any]:
    """An operation's envelope (cov_ucis ``_envelope``), read back."""
    from covsight.core.api import CoverTypeT, ScopeTypeT

    def bins(s):
        return {it.getName(): it.getCoverData().data
                for it in items(s, CoverTypeT.CVGBIN)}
    params = {cp.getScopeName(): {"type": cp.getAttribute("pssunit.type"),
                                  "bins": bins(cp)}
              for cp in kids(cg, ScopeTypeT.COVERPOINT)}
    e = {"calls": int(cg.getAttribute("pssunit.calls") or 0),
         "params": params, "result": params.pop("result", None)}
    e["crosses"] = {x.getScopeName(): {"points": x.getScopeName().split(","),
                                       "bins": bins(x)}
                    for x in kids(cg, ScopeTypeT.CROSS)}
    return e


def gaps(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each unhit point of ``report``: statements, branch arms (by ID),
    registers never accessed (by path), then the value bins never seen of
    the registers that were (``<path>.<field>:<bin>``)."""
    out: List[Dict[str, Any]] = []
    for kind, table in (("statement", "statements"), ("arm", "arms")):
        for pid, p in report.get(table, {}).items():
            if p.get("hits"):
                continue
            g = {"kind": kind, "id": pid, "scope": p.get("scope")}
            if kind == "arm":
                g.update(branch=p.get("branch"), arm=p.get("arm"),
                         implicit=bool(p.get("implicit")))
            if p.get("file"):
                g.update(file=p["file"], line=p.get("line"))
            out.append(g)
    bins = []
    for path, r in report.get("registers", {}).items():
        if not (r.get("reads") or r.get("writes")):
            out.append({"kind": "register", "id": path, "comp": r.get("comp"),
                        "type": r.get("type")})
            continue
        for name, f in r.get("fields", {}).items():
            bins += [{"kind": "bin", "id": f"{path}.{name}:{b}",
                      "register": path, "field": name, "bin": b}
                     for b, n in f.get("bins", {}).items() if not n]
    return out + bins


class Sources:
    """Source files by the path a report spells, found under ``roots``."""

    def __init__(self, roots: Sequence[str] = ()) -> None:
        self.roots = list(roots)
        self._text: Dict[str, Optional[List[str]]] = {}

    def lines(self, path: str) -> Optional[List[str]]:
        if path not in self._text:
            self._text[path] = None
            cands = [path] if os.path.isabs(path) else [
                os.path.join(r, path) for r in self.roots]
            for c in cands:
                try:
                    with open(c, encoding="utf-8") as f:
                        self._text[path] = f.read().splitlines()
                    break
                except (OSError, UnicodeDecodeError):
                    continue
        return self._text[path]

    def find(self, path: str) -> Optional[str]:
        """The first of ``roots``/``path`` that exists, or None."""
        if os.path.isabs(path):
            return path if os.path.isfile(path) else None
        return next((os.path.join(r, path) for r in self.roots
                     if os.path.isfile(os.path.join(r, path))), None)

    def context(self, path: str, line: int, n: int) -> List[Dict[str, Any]]:
        """Lines ``line - n`` .. ``line + n`` of ``path``; [] if not found."""
        text = self.lines(path)
        if not text or not line or line > len(text):
            return []
        lo, hi = max(1, line - n), min(len(text), line + n)
        return [{"line": i, "text": text[i - 1]} for i in range(lo, hi + 1)]


def add_context(gs: List[Dict[str, Any]], sources: Sources, n: int) -> None:
    for g in gs:
        if g.get("file"):
            g["context"] = sources.context(g["file"], g.get("line") or 0, n)


def summary(gs: List[Dict[str, Any]]) -> Dict[str, int]:
    return {k: sum(1 for g in gs if g["kind"] == k) for k in KINDS}


def _text(gs: List[Dict[str, Any]], out) -> None:
    code = [g for g in gs if g["kind"] in ("statement", "arm")]
    for g in code:
        where = f"{g['file']}:{g['line']}" if g.get("file") else g["scope"]
        what = "statement" if g["kind"] == "statement" else (
            f"{'implicit ' if g['implicit'] else ''}{g['arm']} arm")
        print(f"{where}: {what} never run ({g['id']})", file=out)
        for c in g.get("context", ()):
            mark = ">" if c["line"] == g.get("line") else " "
            print(f"  {mark}{c['line']:5} | {c['text']}", file=out)
    for g in gs:
        if g["kind"] == "register":
            print(f"register {g['id']}: never accessed", file=out)
    bins: Dict[str, List[str]] = {}
    for g in gs:
        if g["kind"] == "bin":
            bins.setdefault(f"{g['register']}.{g['field']}", []).append(g["bin"])
    for f, bs in bins.items():
        print(f"field {f}: bins never seen: {' '.join(bs)}", file=out)
    s = summary(gs)
    print(", ".join(f"{s[k]} {k}{'s' if s[k] != 1 else ''}" for k in KINDS)
          + " not covered", file=out)


def _ratio(hit: int, total: int) -> Dict[str, int]:
    return {"hit": hit, "total": total}


def totals(report: Dict[str, Any]) -> Dict[str, Any]:
    """Points hit / points: in all (``summary``), per scope (statements and
    arms, with the scope's file) and per register (accesses, field bins);
    ``envelope``: the operations' argument and result bins (P5.7)."""
    scopes: Dict[str, Dict[str, Any]] = {}
    for kind, table in (("statements", "statements"), ("arms", "arms")):
        for p in report.get(table, {}).values():
            s = scopes.setdefault(p.get("scope") or "", {
                "scope": p.get("scope") or "", "file": None,
                "statements": _ratio(0, 0), "arms": _ratio(0, 0)})
            if s["file"] is None and p.get("file"):
                s["file"] = p["file"]
            s[kind]["total"] += 1
            s[kind]["hit"] += bool(p.get("hits"))
    regs = []
    for path, r in report.get("registers", {}).items():
        bins = [n for f in r.get("fields", {}).values()
                for n in f.get("bins", {}).values()]
        regs.append({"path": path, "type": r.get("type"),
                     "reads": r.get("reads", 0), "writes": r.get("writes", 0),
                     "bins": _ratio(sum(1 for n in bins if n), len(bins))})
    ss = list(scopes.values())

    def total(key, xs):
        return _ratio(sum(x[key]["hit"] for x in xs),
                      sum(x[key]["total"] for x in xs))
    summ = {"statements": total("statements", ss), "arms": total("arms", ss),
            "registers": _ratio(sum(1 for r in regs if r["reads"] or r["writes"]),
                                len(regs)),
            "bins": total("bins", regs)}
    ebins = [n for e in report.get("envelope", {}).values()
             for p in list(e.get("params", {}).values()) + [e.get("result")]
             + list((e.get("crosses") or {}).values())
             if p is not None for n in p.get("bins", {}).values()]
    summ["envelope"] = _ratio(sum(1 for n in ebins if n), len(ebins))
    return {"summary": summ, "scopes": sorted(ss, key=lambda s: s["scope"]),
            "registers": regs}


def _pct(r: Dict[str, int]) -> str:
    if not r["total"]:
        return f"{'-':>15}"
    return f"{r['hit']:>5}/{r['total']:<5}{100 * r['hit'] // r['total']:>3}%"


def _report_text(t: Dict[str, Any], out) -> None:
    if t["scopes"]:
        w = max(len("scope"), *(len(s["scope"]) for s in t["scopes"]))
        print(f"{'scope':<{w}}  {'statements':>15}  {'arms':>15}", file=out)
        for s in t["scopes"]:
            print(f"{s['scope']:<{w}}  {_pct(s['statements'])}  "
                  f"{_pct(s['arms'])}", file=out)
    if t["registers"]:
        w = max(len("register"), *(len(r["path"]) for r in t["registers"]))
        print(f"{'register':<{w}}  {'reads':>6}  {'writes':>6}  {'bins':>15}",
              file=out)
        for r in t["registers"]:
            print(f"{r['path']:<{w}}  {r['reads']:>6}  {r['writes']:>6}  "
                  f"{_pct(r['bins'])}", file=out)
    print("total: " + ", ".join(
        f"{k} {v['hit']}/{v['total']}" for k, v in t["summary"].items()),
        file=out)


def located(report: Dict[str, Any], sources: Sources) -> Dict[str, Any]:
    """``report`` with each relative ``file`` that ``sources`` finds made
    relative to the cwd (absolute if on another drive); others unchanged."""
    out = dict(report)
    for key in ("statements", "arms"):
        pts = {}
        for k, p in report.get(key, {}).items():
            f = p.get("file")
            if f and not os.path.isabs(f):
                found = sources.find(f)
                if found:
                    try:
                        f = os.path.relpath(found)
                    except ValueError:
                        f = os.path.abspath(found)
                    p = dict(p, file=f)
            pts[k] = p
        out[key] = pts
    return out


def write_code(report: Dict[str, Any], fmt: str, fp) -> None:
    """Write the statements and branch arms of ``report`` as ``fmt``
    (``lcov`` or ``cobertura``), with covsight's writers."""
    from covsight.analysis.code_coverage import CodeCoverage
    from pssc.testing.cov_ucis import to_ucis
    cc = CodeCoverage(to_ucis(report))
    if fmt == "lcov":
        from covsight.analysis.lcov import write_lcov
        write_lcov(cc, fp)
    else:
        from covsight.analysis.cobertura import write_cobertura
        write_cobertura(cc, fp)


def merge(paths: Sequence[str], out: str) -> None:
    """Sum the reports at ``paths`` into ``out`` (``.cdb``: a covsight
    database, else JSON). All ``.cdb`` into a ``.cdb``: covsight's merger,
    which keeps each run's tests; otherwise the points are summed as JSON
    (a ``.cdb`` written from that has no tests)."""
    if out.endswith(".cdb") and all(p.endswith(".cdb") for p in paths):
        from covsight.core.ncdb.ncdb_merger import NcdbMerger
        for p in paths:
            load_cdb(p)             # a CovError for what is not a .cdb
        NcdbMerger().merge(list(paths), out)
        return
    from pssc.testing.coverage import Coverage
    cov = Coverage()
    for p in paths:
        cov.merge(load(p))
    report = cov.report()
    if out.endswith(".cdb"):
        from pssc.testing.cov_ucis import write_cdb
        write_cdb(report, out)
    else:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True)
    except OSError as e:
        raise CovError(f"cannot run git: {e.strerror}")
    if r.returncode:
        raise CovError(f"git {args[0]}: {r.stderr.strip()}")
    return r.stdout


def changed_lines(base: str, cwd: str = ".") -> tuple:
    """(the git root, {path relative to it: the line numbers added or
    changed in the working tree since ``base``}). Untracked files are not
    in it."""
    root = _git(cwd, "rev-parse", "--show-toplevel").strip()
    text = _git(root, "diff", "-U0", "--no-color", "--no-ext-diff", base,
                "--")
    out: Dict[str, set] = {}
    cur: Optional[set] = None
    for ln in text.splitlines():
        if ln.startswith("+++ "):
            name = ln[4:]
            cur = None if name == "/dev/null" else out.setdefault(
                name[2:] if name.startswith("b/") else name, set())
        elif cur is not None:
            m = _HUNK.match(ln)
            if m:
                start, n = int(m.group(1)), int(m.group(2) or 1)
                cur.update(range(start, start + n))
    return root, {f: ls for f, ls in out.items() if ls}


def on_changed(gs: List[Dict[str, Any]], sources: Sources, root: str,
               changed: Dict[str, set]) -> List[Dict[str, Any]]:
    """The gaps of ``gs`` whose file (found by ``sources``) and line are in
    ``changed`` (paths relative to ``root``)."""
    real = os.path.realpath(root)
    out = []
    for g in gs:
        found = g.get("file") and sources.find(g["file"])
        if not found:
            continue
        rel = os.path.relpath(os.path.realpath(found), real)
        if g.get("line") in changed.get(rel.replace(os.sep, "/"), ()):
            out.append(g)
    return out


def cmd_cov(args: argparse.Namespace) -> int:
    try:
        if args.cov_cmd == "merge":
            merge(args.reports, args.output)
            return 0
        report = load(args.report)
        if args.cov_cmd == "diff":
            root, changed = changed_lines(args.base)
    except CovError as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"pssc: error: cannot write '{args.output}': {e.strerror}",
              file=sys.stderr)
        return 2
    if args.cov_cmd == "report":
        t = totals(report)
        if args.json:
            json.dump(dict(schema=REPORT_SCHEMA, report=args.report, **t),
                      sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            _report_text(t, sys.stdout)
        return 0
    roots = list(args.source_root or ()) + [
        os.getcwd(), os.path.dirname(os.path.abspath(args.report))]
    if args.cov_cmd in ("lcov", "cobertura"):
        report = located(report, Sources(roots))
        try:
            with open(args.output, "w", encoding="utf-8") as fp:
                write_code(report, args.cov_cmd, fp)
        except OSError as e:
            print(f"pssc: error: cannot write '{args.output}': {e.strerror}",
                  file=sys.stderr)
            return 2
        return 0
    gs = gaps(report)
    if args.kind:
        gs = [g for g in gs if g["kind"] in args.kind]
    if args.cov_cmd == "diff":
        gs = on_changed(gs, Sources(roots), root, changed)
    add_context(gs, Sources(roots), args.context)
    if args.json:
        doc = {"schema": SCHEMA, "report": args.report,
               "summary": summary(gs), "gaps": gs}
        if args.cov_cmd == "diff":
            doc.update(schema=DIFF_SCHEMA, base=args.base,
                       changed=sorted(changed))
        json.dump(doc, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _text(gs, sys.stdout)
    return 0


SOURCE_ROOT_HELP = ("a directory relative source paths are read from "
                    "(repeatable; then the cwd, then the report's directory)")
REPORT_HELP = ("a coverage report: pssunit.coverage/1 JSON (pssc test --cov "
               "x.json) or a covsight database (--cov x.cdb)")


def add_parser(sub) -> None:
    """Register `pssc cov` on the top-level subparsers *sub*."""
    co = sub.add_parser("cov", help="read a coverage report (pssc test --cov)")
    csub = co.add_subparsers(dest="cov_cmd", metavar="COMMAND")
    csub.required = True
    for name, what, schema in (
            ("gaps", "every statement, branch arm, register and field bin "
             "the tests never reached, with source context", SCHEMA),
            ("diff", "the statements and branch arms never reached on lines "
             "added or changed since a git revision (the working tree "
             "against --base)", DIFF_SCHEMA)):
        g = csub.add_parser(name, help=what)
        g.add_argument("report", metavar="REPORT", help=REPORT_HELP)
        if name == "diff":
            g.add_argument("--base", required=True, metavar="REF",
                           help="the git revision to compare with (a branch, "
                           "tag or commit)")
        g.add_argument("--kind", action="append", choices=KINDS,
                       help="only gaps of this kind (repeatable)")
        g.add_argument("--context", type=int, default=2, metavar="N",
                       help="source lines shown before and after a gap "
                       "(default 2)")
        g.add_argument("--source-root", action="append", metavar="DIR",
                       help=SOURCE_ROOT_HELP)
        g.add_argument("--json", action="store_true",
                       help=f"print one JSON document (schema {schema})")
    r = csub.add_parser("report", help="statements, branch arms, registers "
                        "and field bins hit, in all and per scope and "
                        "register")
    r.add_argument("report", metavar="REPORT", help=REPORT_HELP)
    r.add_argument("--json", action="store_true",
                   help="print one JSON document (schema pssc.cov-report/1)")
    for fmt, what in (("lcov", "an LCOV tracefile (genhtml, editors)"),
                      ("cobertura", "Cobertura XML (CI coverage views)")):
        w = csub.add_parser(fmt, help=f"statement and branch-arm coverage as "
                            f"{what}")
        w.add_argument("report", metavar="REPORT", help=REPORT_HELP)
        w.add_argument("-o", "--output", required=True, metavar="FILE",
                       help="the file to write")
        w.add_argument("--source-root", action="append", metavar="DIR",
                       help=SOURCE_ROOT_HELP + "; a file found there is "
                       "written relative to the cwd")
    m = csub.add_parser("merge", help="sum several coverage reports into one "
                        "(the runs of a CI matrix, say)")
    m.add_argument("reports", nargs="+", metavar="REPORT", help=REPORT_HELP)
    m.add_argument("-o", "--output", required=True, metavar="FILE",
                   help="the report to write: a covsight database if it ends "
                   "in .cdb (with each run's tests when every REPORT is a "
                   ".cdb), else pssunit.coverage/1 JSON")
    co.set_defaults(func=cmd_cov)
