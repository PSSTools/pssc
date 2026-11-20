"""What the run says afterwards: to a person, and to CI.

Both renderings come from the same ``list[Result]``. Neither is derived from the
other's text, so the summary a human reads and the report a machine reads cannot
drift apart -- which is the usual way a test runner ends up claiming two
different things about one run.

CTRF (https://github.com/ctrf-io/ctrf) is the machine format. Choosing an
existing standard over TAP or JUnit XML matters more here than it usually would:
pssunit is a portable framework and pssc is one runner of it, so a shared result
format is what lets two implementations' output be compared at all.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .runner import Kind, Result, Status

#: Total width the dotted leader fills, so the status column lines up.
_LEADER_WIDTH = 44

#: CTRF spec version this writer targets.
CTRF_SPEC_VERSION = "0.0.0"

_HUMAN_STATUS = {
    Status.PASS: "PASS",
    Status.FAIL: "FAIL",
    Status.ERROR: "ERROR",
    Status.SKIP: "SKIP",
    Status.XFAIL: "XFAIL",
    Status.XPASS: "XPASS",
}

#: Column width of the status field. Wide enough for XFAIL/XPASS so the
#: timings stay aligned whether or not a suite uses the annotation.
_STATUS_WIDTH = 5


def _roots(results: Sequence[Result]) -> List[str]:
    seen: List[str] = []
    for r in results:
        if r.item.comp not in seen:
            seen.append(r.item.comp)
    return seen


def _replay_sources(specs: Sequence[str]) -> List[str]:
    """The src-specs of a run, with its selections taken off.

    A test ID (``suite::test*``) is dropped and ``FILE:LINE`` becomes ``FILE``:
    the replay line selects its one test by ID, and another selection beside it
    could only narrow that to nothing.
    """
    import os
    import re
    out: List[str] = []
    for spec in specs:
        if "::" in spec and not os.path.exists(spec):
            continue
        m = re.match(r"^(.+):(\d+)$", spec)
        if m and os.path.isfile(m.group(1)):
            spec = m.group(1)
        if spec not in out:
            out.append(spec)
    return out


def replay_command(result: Result, specs: Sequence[str] = (),
                   options: Sequence[str] = ()) -> str:
    """The command line that re-runs just this test, exactly as it just ran.

    Printed on every failure. It is the highest-value line in the whole report
    and it costs nothing: the alternative is the user reconstructing the seed
    and the filter by hand, which is where a reproducible failure turns into an
    intermittent one.

    The test is selected by its ID (``suite::test``), not ``-k``: a name can
    match same-named tests in other suites. ``options`` are the run's options
    that change a verdict (``--platform``, ``--targets``, ``--no-debug``,
    ``--strict``, ...; see ``cmd.replay_options``), so the replay runs the test
    the way it failed.
    """
    import shlex
    parts = ["pssc", "test"]
    parts += _replay_sources(specs)
    parts.append(result.item.qname)
    parts += list(options)
    # a property test replays the run that failed
    failing = getattr(result, "failing_seed", None)
    seed = result.seed if failing is None else failing
    parts += ["--seed", str(seed)]
    return " ".join(shlex.quote(p) for p in parts)


def _value(v: int) -> str:
    return str(v) if -10 < v < 10 else f"{v} (0x{v & (2**64 - 1):x})"


def _operand_rows(indent: int, operands, values) -> List[str]:
    """Each operand's value under it: a bar at each operand's first column,
    then the values, left to right, on as few rows as do not overlap."""
    cols = sorted(((o.offset + indent, _value(v))
                   for o, v in zip(operands, values)), key=lambda c: c[0])
    rows = []
    while cols:
        line, rest, end = "", [], -1
        for col, label in cols:
            if col > end:
                line = line.ljust(col) + label
                end = col + len(label)
            else:
                rest.append((col, label))
        # the ones still to come keep their bar where the row has room
        chars = list(line)
        for col, _ in rest:
            chars.extend(" " * (col + 1 - len(chars)))
            if chars[col] == " ":
                chars[col] = "\u2502"
        rows.append("".join(chars).rstrip())
        cols = rest
    first = []
    for o in operands:
        col = o.offset + indent
        first.extend(" " * (col + 1 - len(first)))
        first[col] = "\u2502"
    return ["".join(first).rstrip()] + rows if operands else []


def _bus_line(i: int, e: Dict[str, Any]) -> str:
    rw = "R" if e.get("op") == "read" else "W"
    a = e.get("addr", 0)
    addr = f"0x{a >> 16 & 0xffff:04x}_{a & 0xffff:04x}"
    line = f"#{i:<3} {rw}{e.get('width', 32)} {addr}  {_reg_text(e)}"
    if e.get("src"):
        line += f"   {e['src']}"
    return line


def _reg_text(e: Dict[str, Any]) -> str:
    d = e.get("data", 0)
    text = f"{e['reg']} = 0x{d:0{max(1, e.get('width', 32) // 4)}x}" \
        if e.get("reg") else f"0x{d:x}"
    if e.get("fields"):
        text += " {" + ", ".join(f"{k}={v}" for k, v in e["fields"].items()) + "}"
    return text


def failure_card(site, values: Sequence[int], test: str = "",
                 seed: Optional[int] = None, bus: Sequence[Dict] = (),
                 last: int = 3, registers: Sequence[Dict] = (),
                 repro: Optional[str] = None) -> str:
    """The failure card of one check (design 6.6): where it is, the call as
    written with each operand's value under it, the registers it names
    (``RegisterFake.decoded``), the last ``last`` accesses of ``bus``
    (``RegisterFake.bus``) and the command that runs the test again.
    ``site`` is a ``check_rewrite.CheckSite``; ``values`` its operands'."""
    head = f"FAIL {site.location}"
    if test:
        head += f"  {test}"
    if seed is not None:
        head += f"  seed={seed}"
    lines = [head]
    if site.what:
        lines.append(f"  {site.what}")
    lines.append(f"  {site.text}")
    lines += _operand_rows(2, site.operands, values)
    for e in registers:
        lines.append(f"  {_reg_text(e)}")
    if bus and last > 0:
        shown = list(bus)[-last:]
        lines.append(f"  last bus activity ({len(shown)} of {len(bus)}):")
        first = len(bus) - len(shown) + 1
        lines += [f"    {_bus_line(first + i, e)}" for i, e in enumerate(shown)]
    if repro is None and test:
        repro = f"pssc test {test}" + (
            f" --seed {seed}" if seed is not None else "")
    if repro:
        lines.append(f"  repro: {repro}")
    return "\n".join(lines)


def report_human(results: Sequence[Result],
                 specs: Sequence[str] = (),
                 verbose: bool = False,
                 quiet: bool = False,
                 out=None,
                 options: Sequence[str] = ()) -> None:
    out = out if out is not None else sys.stdout
    seed = results[0].seed if results else None
    total_time = sum(r.elapsed for r in results)

    if not quiet:
        for root in _roots(results):
            print(root, file=out)
            for r in results:
                if r.item.comp != root:
                    continue
                leader = "." * max(3, _LEADER_WIDTH - len(r.item.name))
                line = f"  {r.item.name} {leader} {_HUMAN_STATUS[r.status]:{_STATUS_WIDTH}}"
                line += f"  {r.elapsed:5.2f}s"
                if r.checks_run:
                    line += f"  {r.checks_passed}/{r.checks_run} checks"
                runs = getattr(r, "runs", 0)
                if runs:
                    line += f"  {runs} run{'s' if runs != 1 else ''}"
                if r.status is Status.SKIP and r.detail:
                    # A skip is `ok`, so it never reaches the failure block
                    # below -- and a skip whose reason is invisible is a test
                    # that quietly stopped running. The reason goes on the one
                    # line the reader does see.
                    line += f"  ({r.detail})"
                if verbose:
                    line += f"  {r.item.qname}"
                print(line, file=out)
        print(file=out)

        for r in results:
            if r.ok:
                continue
            print(f"{_HUMAN_STATUS[r.status]} {r.item.qname}", file=out)
            if r.detail:
                for line in r.detail.splitlines():
                    print(f"  {line}", file=out)
            if r.log:
                print("  --- model output ---", file=out)
                for line in r.log.rstrip().splitlines():
                    print(f"  {line}", file=out)
            if verbose and r.trace:
                for line in r.trace.rstrip().splitlines():
                    print(f"  {line}", file=out)
            if getattr(r, "trace_file", None):
                flaky = ("  (the traced re-run did not fail the same way: "
                         "flaky)" if getattr(r, "trace_flaky", False) else "")
                print(f"  trace: {r.trace_file}{flaky}", file=out)
            print(f"  replay: {replay_command(r, specs, options)}", file=out)
            print(file=out)

    counts = summary_counts(results)
    parts = [f"{counts['passed']} passed"]
    for key in ("failed", "error", "skipped", "xfailed", "xpassed"):
        if counts.get(key):
            parts.append(f"{counts[key]} {key}")
    print(f"{', '.join(parts)}"
          f"{f'    seed {seed}' if seed is not None else ''}"
          f"    {total_time:.2f}s", file=out)


def summary_counts(results: Sequence[Result]) -> Dict[str, int]:
    """Counts keyed the way the human summary talks about them."""
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0,
              "xfailed": 0, "xpassed": 0}
    key = {
        Status.PASS: "passed",
        Status.FAIL: "failed",
        Status.ERROR: "error",
        Status.SKIP: "skipped",
        Status.XFAIL: "xfailed",
        Status.XPASS: "xpassed",
    }
    for r in results:
        counts[key[r.status]] += 1
    return counts


def _ctrf_status(result: Result) -> tuple:
    """``(status, rawStatus)`` for CTRF.

    CTRF allows five statuses and pssunit distinguishes more, so the collapse
    has to be stated rather than left to whoever writes the writer next:

    * a runtime or solver exception is ``failed`` with ``rawStatus: "error"`` --
      ``rawStatus`` exists precisely to carry the tool's own pre-normalization
      status, and folding a crash into ``other`` hides it from every summary
      that only counts failures.
    * an xfail that fails is ``passed``/``xfail``; an xfail that *passes* is
      ``failed``/``xpass``, because an unexpected pass means a stale annotation
      and silently accepting it defeats the point of the mark.

    Reads the status the runner settled on rather than re-deriving it from
    ``item.meta``: with ``--runxfail`` the two disagree, and this file agreeing
    with the exit code matters more than it agreeing with the annotation.
    """
    return _CTRF_STATUS[result.status]


#: The collapse, as a table -- there is no logic left in it, which is the point.
_CTRF_STATUS = {
    Status.PASS:  ("passed", None),
    Status.FAIL:  ("failed", None),
    Status.ERROR: ("failed", "error"),
    Status.SKIP:  ("skipped", "skip"),
    Status.XFAIL: ("passed", "xfail"),
    Status.XPASS: ("failed", "xpass"),
}


def _ctrf_test(result: Result) -> Dict[str, Any]:
    status, raw = _ctrf_status(result)
    entry: Dict[str, Any] = {
        "name": result.item.name,
        "status": status,
        "duration": int(result.elapsed * 1000),
        "suite": [result.item.comp],
        "extra": {"qname": result.item.qname, "seed": result.seed},
    }
    if getattr(result, "runs", 0):
        entry["extra"]["runs"] = result.runs
    if getattr(result, "failing_seed", None) is not None:
        entry["extra"]["failingSeed"] = result.failing_seed
    if raw is not None:
        entry["rawStatus"] = raw
    loc = result.item.loc
    if loc is not None:
        if getattr(loc, "file", None):
            entry["filePath"] = loc.file
        if getattr(loc, "line", None):
            entry["line"] = loc.line
    if result.item.meta.group:
        entry["tags"] = [result.item.meta.group]
    if result.detail:
        entry["message"] = result.detail
    if result.trace:
        entry["trace"] = result.trace
    if result.log:
        entry["stdout"] = result.log
    if result.checks_run:
        # Per-check granularity inside the report. CTRF has no first-class place
        # for it, so it goes in `extra` rather than being flattened into the
        # test's own pass/fail -- a reader can tell "1 test failed" from "1 of
        # 40 checks failed", which are different sizes of problem.
        entry["extra"]["checks"] = {
            "passed": result.checks_passed,
            "failed": result.checks_failed,
            "skipped": result.checks_skipped,
        }
    return entry


def build_ctrf(results: Sequence[Result],
               start_ms: Optional[int] = None,
               stop_ms: Optional[int] = None) -> Dict[str, Any]:
    """The CTRF document for ``results``, as a dict."""
    counts = summary_counts(results)
    tests = [_ctrf_test(r) for r in results]

    # CTRF has no `error` bucket: an errored test is a failed one, consistent
    # with `_ctrf_status` above, or the counts would not add up to `tests`.
    failed = sum(1 for t in tests if t["status"] == "failed")
    passed = sum(1 for t in tests if t["status"] == "passed")
    skipped = sum(1 for t in tests if t["status"] == "skipped")

    stop_ms = int(time.time() * 1000) if stop_ms is None else stop_ms
    if start_ms is None:
        start_ms = stop_ms - int(sum(r.elapsed for r in results) * 1000)

    return {
        "reportFormat": "CTRF",
        "specVersion": CTRF_SPEC_VERSION,
        "generatedBy": "pssc test",
        "results": {
            "tool": {"name": "pssc"},
            "summary": {
                "tests": len(tests),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "pending": 0,
                "other": 0,
                "start": start_ms,
                "stop": stop_ms,
            },
            "tests": tests,
        },
    }


def write_ctrf(results: Sequence[Result], path,
               start_ms: Optional[int] = None,
               stop_ms: Optional[int] = None) -> Path:
    """Write the CTRF report, and return where it went.

    Written by every run that executes tests, because exit code 2 is only a
    useful signal if a report is guaranteed to be sitting next to it.
    """
    path = Path(path)
    path.write_text(json.dumps(build_ctrf(results, start_ms, stop_ms), indent=2))
    return path


#: The NDJSON event stream ``pssc test --json`` writes
#: (docs/schemas/pssunit.events-1.json).
EVENTS_SCHEMA = "pssunit.events/1"


def _event_loc(item, ev: Dict[str, Any]) -> Dict[str, Any]:
    loc = item.loc
    if loc is not None and getattr(loc, "file", None):
        ev["file"] = loc.file
        if getattr(loc, "line", None):
            ev["line"] = loc.line
    return ev


def event_run_start(seed: Optional[int], tests: int) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"event": "run.start", "schema": EVENTS_SCHEMA,
                          "tests": tests}
    if seed is not None:
        ev["seed"] = seed
    return ev


def event_test_start(item) -> Dict[str, Any]:
    return _event_loc(item, {"event": "test.start", "id": item.qname,
                             "name": item.name, "suite": item.comp})


def event_test_end(result: Result, specs: Sequence[str] = (),
                   options: Sequence[str] = ()) -> Dict[str, Any]:
    """One test's verdict. ``status`` is the runner's own (finer than CTRF's);
    ``repro`` is the command that runs this test again with its seed."""
    ev: Dict[str, Any] = {"event": "test.end", "id": result.item.qname,
                          "status": result.status.value,
                          "duration_ms": int(result.elapsed * 1000)}
    if result.seed is not None:
        ev["seed"] = result.seed
    if result.detail:
        ev["message"] = result.detail
    if result.trace:
        ev["trace"] = result.trace
    if result.log:
        ev["stdout"] = result.log
    if getattr(result, "trace_file", None):
        ev["trace_file"] = result.trace_file
        if getattr(result, "trace_flaky", False):
            ev["trace_flaky"] = True
    if getattr(result, "failures", None):
        ev["failures"] = result.failures
    if result.checks_run:
        ev["checks"] = {"passed": result.checks_passed,
                        "failed": result.checks_failed,
                        "skipped": result.checks_skipped}
    if getattr(result, "runs", 0):
        ev["runs"] = result.runs
    if getattr(result, "failing_seed", None) is not None:
        ev["failing_seed"] = result.failing_seed
    if not result.ok:
        ev["repro"] = replay_command(result, specs, options)
    return ev


def event_run_end(results: Sequence[Result], exit_code: int,
                  duration: float) -> Dict[str, Any]:
    return {"event": "run.end", "summary": summary_counts(results),
            "exit": exit_code, "duration_ms": int(duration * 1000)}


def emit_event(ev: Dict[str, Any], out=None) -> None:
    """One NDJSON line, flushed so a reader sees each test as it ends."""
    out = sys.stdout if out is None else out
    out.write(json.dumps(ev, separators=(",", ":")) + "\n")
    out.flush()


def build_junit(results: Sequence[Result], specs: Sequence[str] = (),
                options: Sequence[str] = ()) -> str:
    """JUnit XML for ``results``: one ``<testsuite>`` per root component, in
    the order they first appear. ``error`` is ``<error>``, ``xpass`` a
    ``<failure>`` (a stale annotation), ``skip`` and ``xfail`` ``<skipped>``."""
    import xml.etree.ElementTree as ET
    suites: Dict[str, List[Result]] = {}
    for r in results:
        suites.setdefault(r.item.comp, []).append(r)
    root = ET.Element("testsuites", name="pssc test")
    tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    total_time = 0.0
    for comp, rs in suites.items():
        n = {"tests": len(rs),
             "failures": sum(r.status in (Status.FAIL, Status.XPASS) for r in rs),
             "errors": sum(r.status is Status.ERROR for r in rs),
             "skipped": sum(r.status in (Status.SKIP, Status.XFAIL) for r in rs)}
        t = sum(r.elapsed for r in rs)
        total_time += t
        for k in tot:
            tot[k] += n[k]
        ts = ET.SubElement(root, "testsuite", name=comp,
                           time=f"{t:.3f}", **{k: str(v) for k, v in n.items()})
        for r in rs:
            attrs = {"name": r.item.name, "classname": comp,
                     "time": f"{r.elapsed:.3f}"}
            loc = r.item.loc
            if loc is not None and getattr(loc, "file", None):
                attrs["file"] = str(loc.file)
                if getattr(loc, "line", None):
                    attrs["line"] = str(loc.line)
            tc = ET.SubElement(ts, "testcase", **attrs)
            msg = r.detail or r.status.value
            if r.status is Status.ERROR:
                ET.SubElement(tc, "error", message=msg).text = r.trace or None
            elif r.status is Status.FAIL:
                ET.SubElement(tc, "failure", message=msg).text = (
                    (r.trace or "") + f"\nrepro: {replay_command(r, specs, options)}").strip()
            elif r.status is Status.XPASS:
                ET.SubElement(tc, "failure", type="xpass",
                              message="unexpected pass (xfail)")
            elif r.status is Status.SKIP:
                ET.SubElement(tc, "skipped", message=msg)
            elif r.status is Status.XFAIL:
                ET.SubElement(tc, "skipped", type="xfail", message=msg)
            if r.log:
                ET.SubElement(tc, "system-out").text = r.log
    for k, v in tot.items():
        root.set(k, str(v))
    root.set("time", f"{total_time:.3f}")
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def write_junit(results: Sequence[Result], path,
                specs: Sequence[str] = (),
                options: Sequence[str] = ()) -> Path:
    path = Path(path)
    path.write_text(build_junit(results, specs, options))
    return path
