"""`pssc trace show|why|slice|diff`: read a test trace (P2.10, P5.3; why:
trace_why.py; slice, diff: trace_reg.py).

A trace is the be-bc NDJSON v2 file ``pssc test --trace all|fail`` writes
(``.pssunit/traces/<test-id>.ndjson``): a header line ``{"zbc_trace": 2,
"test": ..., "seed": ...}``, then one event per line, each with ``file`` and
``line`` when it has a source location. An event's index is its ``seq``.
The binary form of the same records (be-bc ``trace/binary.py``,
``.pstrace``, what the C engine writes) is read too, by its magic; its
events may carry only a ``src_ref``, resolved through the model image
(``--image``, or the header's ``image``).

``show`` prints one line per event with its source line (``--kind``,
``--coro`` filter); ``--at IDX`` maps an event to its source line, with the
lines around it (an event without a location, a call say, maps to the last
located event before it); ``--line FILE:N`` lists every event that ran it.
A trace from a simulation (``+pssvm_trace=DIR``) has the sim time of each
event, ``t`` in fs; there ``--at`` also takes a time with a unit
(``1.234us``; fs, ps, ns, us, ms, s): the last event at or before it.

An event's ``file`` is the path as the model's provenance spells it, usually
relative to the cwd ``pssc test`` ran in. A relative path is looked up under
each ``--source-root``, then the cwd, then the directory two above the
trace's (the run's cwd for the default ``.pssunit/traces``), then the
trace's own.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from .cov_cmd import Sources

#: ``pssc trace show --json``'s schema (docs/schemas/pssc.trace-show-1.json)
SCHEMA = "pssc.trace-show/1"

#: detail keys not worth a column in the text listing
_QUIET = ("pc", "path")


class TraceError(Exception):
    pass


def load(path: str, image: Optional[str] = None
         ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """``(header, events)`` of the trace at *path*, NDJSON or binary
    (``.pstrace``), as raw dicts (so ``file``/``line`` are kept); be-bc's
    reader checks it first. An event with a ``src_ref`` but no ``file`` (the
    C engine's) gets its ``file``/``line`` from the model image: *image*, or
    the header's ``image`` (relative to the trace's directory)."""
    from zuspec.be.bc.trace import binary, schema
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise TraceError(f"cannot read '{path}': {e.strerror}")
    try:
        if binary.is_binary(data):
            version, header, rows = binary.read(data)
        else:
            text = data.decode("utf-8")
            version, _ = schema.parse(text)
            rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
            header, rows = rows[0], rows[1:]
    except (ValueError, KeyError, TypeError, IndexError,
            UnicodeDecodeError) as e:
        raise TraceError(f"'{path}' is not a zbc trace: {e}")
    if version != schema.TRACE_SCHEMA_VERSION:
        raise TraceError(f"'{path}': trace version {version}; this pssc reads "
                         f"{schema.TRACE_SCHEMA_VERSION}")
    if image is None and header.get("image"):
        image = os.path.join(os.path.dirname(os.path.abspath(path)),
                             header["image"])
    if image is not None and any(e.get("src_ref") and not e.get("file")
                                 for e in rows):
        _locate(rows, _prov(image))
    return header, rows


def _prov(image: str) -> list:
    """The provenance entries of the model image (``.zbc``) at *image*."""
    from zuspec.be.bc.model import ZbcModel
    try:
        with open(image, "rb") as f:
            return ZbcModel.from_bytes(f.read()).prov.entries
    except OSError as e:
        raise TraceError(f"cannot read image '{image}': {e.strerror}")
    except Exception as e:  # noqa: BLE001 -- any decode failure
        raise TraceError(f"'{image}' is not a zbc image: {e}")


def _locate(rows: List[Dict[str, Any]], prov: list) -> None:
    """Give each event with a ``src_ref`` and no ``file`` its source line,
    as pssc's NDJSON writer does (testing/trace.py ``TraceWriter.where``)."""
    for e in rows:
        r = e.get("src_ref")
        if r and not e.get("file") and 0 < r < len(prov) and prov[r].file:
            e["file"], e["line"] = prov[r].file, prov[r].line


def _event(e: Dict[str, Any], sources: Sources) -> Dict[str, Any]:
    out = {"index": e["seq"], "kind": e["kind"], "coro": e.get("coro", 0),
           "file": e.get("file"), "line": e.get("line"),
           "detail": e.get("detail", {})}
    if "t" in e:
        out["t"] = e["t"]
    text = sources.lines(e["file"]) if e.get("file") else None
    line = e.get("line") or 0
    out["text"] = (text[line - 1].strip()
                   if text and 0 < line <= len(text) else None)
    return out


def _same_file(want: str, have: Optional[str]) -> bool:
    if not have:
        return False
    w, h = os.path.normpath(want), os.path.normpath(have)
    return h == w or h.endswith(os.sep + w) or w.endswith(os.sep + h) or (
        os.path.abspath(w) == os.path.abspath(h))


def show(path: str, sources: Sources, kinds=None, coro=None, at=None,
         line=None, context: int = 2, image: Optional[str] = None
         ) -> Dict[str, Any]:
    """The ``pssc.trace-show/1`` document for the trace at *path*."""
    header, events = load(path, image)
    doc: Dict[str, Any] = {
        "schema": SCHEMA, "trace": path, "test": header.get("test"),
        "seed": header.get("seed"), "count": len(events)}
    if isinstance(at, AtTime):
        timed = [i for i, e in enumerate(events) if "t" in e]
        if not timed:
            raise TraceError(f"'{path}' has no event times (not a "
                             f"simulation trace): give --at an IDX")
        hit = [i for i in timed if events[i]["t"] <= at.fs]
        if not hit:
            raise TraceError(f"no event at or before {at.text} in '{path}' "
                             f"(the first is at {fmt_time(events[timed[0]]['t'])})")
        doc["at_time"] = at.fs
        at = events[hit[-1]]["seq"]
    if at is not None:
        hit = [i for i, e in enumerate(events) if e["seq"] == at]
        if not hit:
            raise TraceError(f"no event {at} in '{path}' (0..{len(events) - 1})")
        i = hit[0]
        loc = next((events[j] for j in range(i, -1, -1)
                    if events[j].get("file") and events[j].get("line")), None)
        doc["at"] = at
        doc["events"] = [_event(events[i], sources)]
        doc["file"] = loc["file"] if loc else None
        doc["line"] = loc["line"] if loc else None
        doc["from"] = loc["seq"] if loc else None
        doc["context"] = sources.context(loc["file"], loc["line"],
                                         context) if loc else []
        return doc
    sel = events
    if line is not None:
        f, n = line
        sel = [e for e in sel if e.get("line") == n and _same_file(f, e.get("file"))]
        doc["file"], doc["line"] = f, n
        doc["indexes"] = [e["seq"] for e in sel]
    if kinds:
        sel = [e for e in sel if e["kind"] in kinds]
    if coro is not None:
        sel = [e for e in sel if e.get("coro", 0) == coro]
    doc["events"] = [_event(e, sources) for e in sel]
    return doc


#: fs per unit of a ``--at`` time
_UNITS = {"fs": 1, "ps": 10**3, "ns": 10**6, "us": 10**9, "ms": 10**12,
          "s": 10**15}


class AtTime:
    """A ``--at`` time: ``text`` as given, ``fs`` the time in fs."""

    def __init__(self, text: str, fs: int) -> None:
        self.text, self.fs = text, fs


def _at(s: str):
    """``--at``: an event index, or a time with a unit (:class:`AtTime`)."""
    if s.isdigit():
        return int(s)
    m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*(fs|ps|ns|us|ms|s)", s.strip())
    if not m:
        raise argparse.ArgumentTypeError(
            f"expected an event index or a time with a unit "
            f"(fs, ps, ns, us, ms, s), got '{s}'")
    from decimal import Decimal
    return AtTime(s, int(Decimal(m.group(1)) * _UNITS[m.group(2)]))


def fmt_time(fs: int) -> str:
    """``fs`` in the largest unit that leaves an integer part, at most 3
    decimals (``1.234us``)."""
    for unit in ("s", "ms", "us", "ns", "ps"):
        if fs >= _UNITS[unit]:
            v = f"{fs / _UNITS[unit]:.3f}".rstrip("0").rstrip(".")
            return v + unit
    return f"{fs}fs"


def _detail(e: Dict[str, Any]) -> str:
    return " ".join(f"{k}={json.dumps(v) if isinstance(v, str) and ' ' in v else v}"
                    for k, v in e["detail"].items() if k not in _QUIET)


def _row(e: Dict[str, Any]) -> str:
    where = f"{e['file']}:{e['line']}" if e.get("file") else "-"
    s = f"{e['index']:6} #{e['coro']:<3} {e['kind']:<10} {where}  {_detail(e)}"
    if "t" in e:
        s = f"{e['index']:6} @{fmt_time(e['t']):<9}" + s[6:]
    return s + (f"  | {e['text']}" if e.get("text") else "")


def _text(doc: Dict[str, Any], out) -> None:
    print(f"trace {doc['trace']}: test {doc['test']} seed {doc['seed']}, "
          f"{doc['count']} events", file=out)
    if "at" in doc:
        print(_row(doc["events"][0]), file=out)
        if doc["file"] is None:
            print(f"event {doc['at']}: no source location", file=out)
            return
        via = "" if doc["from"] == doc["at"] else f" (from event {doc['from']})"
        print(f"{doc['file']}:{doc['line']}{via}", file=out)
        for c in doc["context"]:
            mark = ">" if c["line"] == doc["line"] else " "
            print(f"  {mark}{c['line']:5} | {c['text']}", file=out)
        return
    if "indexes" in doc:
        ix = ", ".join(str(i) for i in doc["indexes"]) or "never"
        print(f"{doc['file']}:{doc['line']}: run at {ix}", file=out)
    for e in doc["events"]:
        print(_row(e), file=out)


def _file_line(s: str) -> Tuple[str, int]:
    f, sep, n = s.rpartition(":")
    if not sep or not f or not n.isdigit():
        raise argparse.ArgumentTypeError(f"expected FILE:LINE, got '{s}'")
    return f, int(n)


def cmd_trace(args: argparse.Namespace) -> int:
    d = os.path.dirname(os.path.abspath(args.trace))
    roots = list(args.source_root or ()) + [
        os.getcwd(), os.path.dirname(os.path.dirname(d)), d]
    try:
        if args.trace_cmd == "why":
            from . import trace_why
            doc = trace_why.why(args.trace, args.path, Sources(roots),
                                image=args.image)
            text = trace_why.text
        elif args.trace_cmd == "slice":
            from . import trace_reg
            doc = trace_reg.slice_reg(args.trace, args.reg, Sources(roots),
                                      image=args.image)
            text = trace_reg.slice_text
        elif args.trace_cmd == "diff":
            from . import trace_reg
            doc = trace_reg.diff(args.trace, args.other, Sources(roots),
                                 image=args.image)
            text = trace_reg.diff_text
        else:
            doc = show(args.trace, Sources(roots), kinds=args.kind,
                       coro=args.coro, at=args.at, line=args.line,
                       context=args.context, image=args.image)
            text = _text
    except TraceError as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(doc, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        text(doc, sys.stdout)
    return 1 if args.trace_cmd == "diff" and doc["divergence"] else 0


def _common(s, schema: str) -> None:
    """The options every `pssc trace` command takes."""
    s.add_argument("--source-root", action="append", metavar="DIR",
                   help="a directory relative source paths are read from "
                   "(repeatable; then the cwd, then the run's directory)")
    s.add_argument("--image", metavar="ZBC",
                   help="the model image (.zbc) the run loaded: source lines "
                   "of events that have only a src_ref (the C engine's); "
                   "default: the trace header's 'image', if any")
    s.add_argument("--json", action="store_true",
                   help=f"print one JSON document (schema {schema})")


def add_parser(sub) -> None:
    """Register `pssc trace` on the top-level subparsers *sub*."""
    tr = sub.add_parser("trace", help="read a test trace (pssc test --trace)")
    tsub = tr.add_subparsers(dest="trace_cmd", metavar="COMMAND")
    tsub.required = True
    s = tsub.add_parser("show", help="the trace's events, one per line with "
                        "its source line; or one event's line in context "
                        "(--at); or the events that ran a line (--line)")
    s.add_argument("trace", metavar="TRACE",
                   help="a trace file, NDJSON or binary (.pstrace) (pssc "
                   "test --trace writes .pssunit/traces/<test-id>.ndjson)")
    s.add_argument("--kind", action="append", metavar="K",
                   help="only events of this kind: stmt, call, return, "
                   "check, access, branch, ... (repeatable)")
    s.add_argument("--coro", type=int, metavar="N",
                   help="only events of coroutine N")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--at", type=_at, metavar="IDX|TIME",
                   help="event IDX and its source line, with the lines "
                   "around it; in a simulation trace also a time with a "
                   "unit (1.234us): the last event at or before it")
    g.add_argument("--line", type=_file_line, metavar="FILE:N",
                   help="every event that ran line N of FILE (a path or "
                   "its trailing part)")
    s.add_argument("--context", type=int, default=2, metavar="N",
                   help="source lines shown before and after --at's line "
                   "(default 2)")
    _common(s, SCHEMA)
    w = tsub.add_parser("why", help="where a register's last written value "
                        "came from: the write, its statement, and each value "
                        "it read back to the operation's inputs (needs a "
                        "--trace all|fail trace of a debug image)")
    w.add_argument("trace", metavar="TRACE", help="a trace file (as for show)")
    w.add_argument("path", metavar="REG[.FIELD]",
                   help="a register path as the trace names it "
                   "(ch[1].regs.csr), or one of its fields (ch[1].regs.csr.mode: "
                   "the last write that changed it)")
    _common(w, "pssc.trace-why/1")
    sl = tsub.add_parser("slice", help="every access to a register, in trace "
                         "order, each with the statement that made it")
    sl.add_argument("trace", metavar="TRACE", help="a trace file (as for show)")
    sl.add_argument("--reg", required=True, metavar="REG",
                    help="a register path as the trace names it (ch[1].regs.csr)")
    _common(sl, "pssc.trace-slice/1")
    d = tsub.add_parser("diff", help="the first bus access or import where two "
                        "runs' traces differ (exit 1 when they do)")
    d.add_argument("trace", metavar="A", help="a trace file (as for show)")
    d.add_argument("other", metavar="B", help="the trace to compare it with")
    _common(d, "pssc.trace-diff/1")
    tr.set_defaults(func=cmd_trace)
