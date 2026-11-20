"""``pssc test`` -- wiring the stages together.

The pipeline is deliberately six separable stages::

    src-spec -> resolve -> translate -> discover -> plan -> execute -> report

This module is the only place that knows the order. Each stage lives in its own
module and is testable without the others, which is what keeps "did discovery
find it?" answerable separately from "did it run?" -- the two questions a test
runner gets wrong in different ways.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .. import diag
from .. import driver
from . import sources as _sources
from .discover import TestItem, discover

#: Exit codes. An agent or CI job must be able to tell "my test failed" from
#: "my PSS does not compile" without parsing text, so each outcome has its own:
#: `1` the suite ran and a test failed (a report exists), `2` the model never got
#: as far as running (a src-spec naming nothing, a parse or elaboration error,
#: a test whose base type does not resolve), `3` pssc itself fell over, `5`
#: nothing was selected -- which is not a pass. Other subcommands keep their own
#: codes; these are `pssc test`'s.
EXIT_OK = 0
EXIT_TESTS_FAILED = 1
EXIT_COMPILE_ERROR = 2
EXIT_INTERNAL = 3
EXIT_NO_TESTS = 5


class TestCommandError(Exception):
    """Nothing to run: no tests found, or none matched the selection.

    Exits 5 and writes no report.
    """


@dataclass
class Collection:
    """What discovery found, and the context it was found in.

    The context is carried because execution needs it -- one ``translate`` and
    one runtime build amortized across the whole suite, with a fresh component
    tree per test.
    """

    ctx: object
    items: List[TestItem]
    #: every test discovery found, before the selection narrowed it
    #: (``--cov`` lowers all their functions, so the points do not depend
    #: on which tests ran)
    found: List[TestItem] = field(default_factory=list)
    #: the source files translated (the slice's "rest" covers all of them)
    paths: List[str] = field(default_factory=list)

    @property
    def roots(self) -> List[str]:
        """Root components, in first-seen order -- the report's grouping."""
        seen: List[str] = []
        for it in self.items:
            if it.comp not in seen:
                seen.append(it.comp)
        return seen


def filter_items(items: Sequence[TestItem],
                 k: Optional[str] = None,
                 fixture: Optional[str] = None,
                 group: Optional[str] = None,
                 ids: Sequence[str] = (),
                 tags: Sequence[str] = ()) -> List[TestItem]:
    """Apply ``-k``, ``--fixture``, ``--group``, ID globs and ``-m``.

    ``-k`` matches a glob against the short name and the qualified name, and
    falls back to a plain substring test, so ``-k smoke`` and ``-k 'smoke_*'``
    both do what they look like.

    ``--group`` matches the annotation's ``.group`` exactly, not as a glob: a
    group is a name the author chose, and a fuzzy match would silently pull in
    a neighbouring group. Selecting several is what ``-k`` is for.

    An ID glob (``suite::test*``) matches the whole test ID, case-sensitively
    and with no substring fallback: an ID is what a report printed, so it is
    pasted back exactly. Several ID globs, or several ``-m`` tags, select the
    union; the different kinds of selection narrow each other.
    """
    out = list(items)
    if fixture:
        out = [it for it in out if it.comp == fixture]
    if group:
        out = [it for it in out if it.meta.group == group]
    if ids:
        out = [it for it in out
               if any(fnmatch.fnmatchcase(it.qname, g) for g in ids)]
    if tags:
        out = [it for it in out if any(t in it.tags for t in tags)]
    if k:
        def matches(it: TestItem) -> bool:
            return (fnmatch.fnmatch(it.name, k)
                    or fnmatch.fnmatch(it.qname, k)
                    or k in it.qname)
        out = [it for it in out if matches(it)]
    return out


_FILE_LINE = re.compile(r"^(.+):(\d+)$")


def split_line_specs(specs: Sequence[str]
                     ) -> Tuple[List[str], List[Tuple[str, int]]]:
    """Split ``FILE:LINE`` selections off the src-specs.

    ``FILE:LINE`` is a source (the file is still compiled) and a selection: the
    tests whose body spans that line. Only an existing file counts, so a spec
    that merely contains a colon is left alone.
    """
    out: List[str] = []
    where: List[Tuple[str, int]] = []
    for spec in specs:
        m = _FILE_LINE.match(spec)
        if m and os.path.isfile(m.group(1)):
            out.append(m.group(1))
            where.append((m.group(1), int(m.group(2))))
        else:
            out.append(spec)
    return out, where


def split_id_specs(specs: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Split test-ID globs (``suite::test*``) off the src-specs.

    A positional containing ``::`` that is not an existing path is a test ID:
    no file name contains ``::``, and every test ID does. With only IDs given,
    the sources are the default (the current directory).
    """
    out: List[str] = []
    ids: List[str] = []
    for spec in specs:
        if "::" in spec and not os.path.exists(spec):
            ids.append(spec)
        else:
            out.append(spec)
    return out, ids


def replay_options(args: argparse.Namespace) -> List[str]:
    """The options of this run that a replay line must carry.

    Only those that change a verdict: the platform, the targets the gate
    checks, the debug build, and the markers' handling. Selection (``-k``,
    ``-m``, ``--lf``) is replaced by the test's ID, and output options do not
    change what fails.
    """
    out: List[str] = []
    if getattr(args, "image", None):
        out += ["--image", args.image]
    if getattr(args, "platform", None):
        out += ["--platform", args.platform]
    if getattr(args, "engine", None):
        out += ["--engine", args.engine]
    if getattr(args, "reg_semantics", None):
        out += ["--reg-semantics", args.reg_semantics]
    if getattr(args, "targets", None):
        out += ["--targets", args.targets]
    if getattr(args, "debug", True) is False:
        out.append("--no-debug")
    for flag in ("strict", "runxfail", "noskip"):
        if getattr(args, flag, False):
            out.append(f"--{flag}")
    return out


def _with_doc_examples(ctx, paths: List[str]):
    """Add the tests the model's doc-comment examples make (P5.10).

    When a function's doc comment holds a ``pss-example`` block, the wrappers
    are written to ``.pssunit/doctest.pss`` and the model is translated again
    with that file; otherwise ``ctx`` and ``paths`` come back unchanged.
    """
    from . import doctest as _doctest
    exs = _doctest.examples(ctx)
    if not exs:
        return ctx, paths
    os.makedirs(".pssunit", exist_ok=True)
    gen = os.path.join(".pssunit", _doctest.FILE)
    with open(gen, "w") as f:
        f.write(_doctest.synth(exs))
    paths = list(paths) + [os.path.abspath(gen)]
    from ..devenv import cache as _cache
    ctx = _cache.translate(paths, lambda: driver.translate(paths))
    if getattr(ctx, "errors", None):
        raise driver.CompileError(
            f"PSS translation of the doc-comment examples ({gen}) failed "
            f"with {len(ctx.errors)} error(s)", ctx.errors)
    return ctx, paths


def collect(specs: Sequence[str],
            k: Optional[str] = None,
            fixture: Optional[str] = None,
            group: Optional[str] = None,
            tags: Sequence[str] = (),
            doctest: bool = True) -> Collection:
    """Resolve, translate, discover, filter.

    With ``doctest`` (the default), the ``pss-example`` blocks of the model's
    doc comments are tests too (``pssc test --no-doctest`` turns that off).

    A ``FILE:LINE`` spec selects the tests whose body spans that line; when
    none does, the collection is empty (and ``pssc test`` exits 5).

    A translation error is a hard error, not a test failure: a model that does
    not compile has no tests to report on, and reporting "0 failed" for it would
    be a lie CI believes.
    """
    specs, ids = split_id_specs(specs)
    specs, where = split_line_specs(specs)
    paths = [str(p) for p in _sources.all_sources(specs)]
    from ..devenv import cache as _cache
    ctx = _cache.translate(paths, lambda: driver.translate(paths))
    if getattr(ctx, "errors", None):
        raise driver.CompileError(
            f"PSS translation failed with {len(ctx.errors)} error(s)",
            ctx.errors)
    if doctest:
        ctx, paths = _with_doc_examples(ctx, paths)

    from . import catalog as _catalog
    _catalog._LINES.clear()         # spans are read from the sources as they are now
    items = found = discover(ctx)
    if where:
        from .catalog import select_lines
        items = select_lines(items, where)
    items = filter_items(items, k=k, fixture=fixture, group=group,
                         ids=ids, tags=tags)
    return Collection(ctx=ctx, items=items, found=list(found), paths=paths)


def _marks(item: TestItem) -> str:
    """Annotation markers for a collected test: ``[skip] [xfail] [group]``.

    Shown in ``--collect-only`` because that listing is what the user reads to
    answer "will this actually run?", and a test marked skip that lists exactly
    like one that runs answers it wrongly.
    """
    marks = []
    if item.meta.skip:
        marks.append("skip" + (f": {item.meta.reason}" if item.meta.reason else ""))
    if item.meta.xfail:
        marks.append("xfail" + (f": {item.meta.reason}" if item.meta.reason else ""))
    if item.meta.group:
        marks.append(item.meta.group)
    return "".join(f"  [{m}]" for m in marks)


def _print_collection(coll: Collection, verbose: bool = False) -> None:
    for root in coll.roots:
        print(root)
        for it in coll.items:
            if it.comp == root:
                print(f"  {it.name}{_marks(it)}"
                      + (f"  ({it.qname})" if verbose else ""))
    n = len(coll.items)
    print()
    print(f"{n} test{'' if n == 1 else 's'} collected")


def _compile_errors() -> tuple:
    """Exception types meaning the model never got as far as running (exit 2).

    ``pssparser`` is a native extension the pure-Python test paths may stub
    out; a missing one must not turn every error into an internal error.
    """
    from .discover import DiscoveryError
    from .sources import SourceSpecError

    types: list = [driver.CompileError, DiscoveryError, SourceSpecError]
    try:
        from pssparser import ParseException
        if isinstance(ParseException, type):
            types.append(ParseException)
    except Exception:  # pragma: no cover - parser absent or stubbed
        pass
    return tuple(types)


#: where a ``--platform`` that failed to load leaves its traceback
PLATFORM_LOG = os.path.join(".pssunit", "platform.log")


def _write_platform_log(e: BaseException) -> str:
    os.makedirs(os.path.dirname(PLATFORM_LOG), exist_ok=True)
    with open(PLATFORM_LOG, "w") as f:
        f.write("".join(traceback.format_exception(type(e), e,
                                                   e.__traceback__)))
    return PLATFORM_LOG


#: Where ``--lf`` finds the previous run's failures: beside the property-run
#: regressions, in the directory the suite is run from.
LAST_FAILED = os.path.join(".pssunit", "lastfailed")

#: Where ``--trace`` writes a test's events (``--trace-dir`` changes it).
from .trace import TRACE_DIR  # noqa: E402


def read_last_failed(path: Optional[str] = None) -> List[str]:
    """Test IDs that failed when last run; empty when there is no record."""
    path = path or LAST_FAILED
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    failed = data.get("failed") if isinstance(data, dict) else None
    return [x for x in failed or () if isinstance(x, str)]


def write_last_failed(results, path: Optional[str] = None) -> None:
    """Record this run's failures for ``--lf``.

    A test that did not run this time keeps its old verdict, so ``--lf -k a``
    does not forget a failure outside ``a``. The file is only written when the
    record changes: a green suite that never failed leaves no ``.pssunit/``.
    """
    path = path or LAST_FAILED
    old = read_last_failed(path)
    ran = {r.item.qname for r in results}
    new = [q for q in old if q not in ran]
    new += [r.item.qname for r in results
            if not r.ok and r.item.qname not in new]
    if new == old:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"failed": new}, f, indent=1)
        f.write("\n")


def _select_last_failed(coll: Collection) -> None:
    """``--lf``: narrow the selection to the tests that failed last time.

    When none of the selected tests failed last time (or nothing is recorded),
    the whole selection runs, and says so: ``--lf`` after a fix is "run what
    was broken", and an empty run would hide that the record is gone.
    """
    failed = set(read_last_failed())
    keep = [it for it in coll.items if it.qname in failed]
    if keep:
        coll.items[:] = keep
    else:
        print(f"--lf: no recorded failures among the selected tests; "
              f"running all {len(coll.items)}")


def _results_path() -> str:
    """The result record sits beside ``LAST_FAILED`` (so moving one moves both)."""
    return os.path.join(os.path.dirname(LAST_FAILED), "results.json")


def _select_changed(coll: Collection, slicer, options: List[str],
                    seed: Optional[int]) -> int:
    """``--changed``: drop the tests whose last result is a pass under the
    same key (:func:`.slice.unchanged`); return how many were dropped."""
    from . import slice as _slice
    record = _slice.read_results(_results_path())
    keep = [it for it in coll.items if not _slice.unchanged(
        record, it.qname, _slice.key(slicer.slice_hash(it), options, seed))]
    dropped = len(coll.items) - len(keep)
    coll.items[:] = keep
    return dropped


def _select_changed_since(coll: Collection, slicer, rev: str) -> int:
    """``--changed --base REV``: drop the tests whose slice is the same as at
    git ``rev``; return how many were dropped."""
    from . import slice as _slice
    try:
        base = _slice.base_slices(coll.paths, rev)
    except _slice.BaseError as e:
        raise TestCommandError(str(e))
    if base is None:
        print(f"--base {rev}: the sources there do not translate; running "
              f"every selected test", file=sys.stderr)
        return 0
    keep = [it for it in coll.items if base.get(it.qname) != slicer.slice_hash(it)]
    dropped = len(coll.items) - len(keep)
    coll.items[:] = keep
    return dropped


def _record_results(results, slicer, options: List[str]) -> None:
    """Keep each test's verdict and key for a later ``--changed``."""
    from . import slice as _slice
    _slice.write_results({
        r.item.qname: {"status": r.status.value,
                       "key": _slice.key(slicer.slice_hash(r.item), options,
                                         r.seed)}
        for r in results}, _results_path())


def cmd_test(args: argparse.Namespace) -> int:
    """Run ``pssc test``; return its exit code (see ``EXIT_*`` above)."""
    if getattr(args, "watch", False):
        from .watch import loop
        one = argparse.Namespace(**dict(vars(args), watch=False, changed=True))
        return loop(args.sources, lambda: cmd_test(one),
                    quiet=getattr(args, "json", False))
    try:
        return _cmd_test(args)
    except TestCommandError as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        return EXIT_NO_TESTS
    except driver.CompileError as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        for err in getattr(e, "errors", None) or []:
            print(diag.indent_block(str(err)), file=sys.stderr)
        return EXIT_COMPILE_ERROR
    except _compile_errors() as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        return EXIT_COMPILE_ERROR
    except Exception:
        traceback.print_exc()
        print("pssc: internal error (see traceback above)", file=sys.stderr)
        return EXIT_INTERNAL


#: Options ``--image`` refuses: each reads the sources or the IR, which an
#: image file does not carry.
_NOT_WITH_IMAGE = (("changed", "--changed"), ("base", "--base"),
                   ("watch", "--watch"), ("cov", "--cov"),
                   ("cov_scope", "--cov-scope"), ("targets", "--targets"),
                   ("mutate", "--mutate"))


def collect_image(path: str, ids: Sequence[str] = (),
                  k: Optional[str] = None, fixture: Optional[str] = None,
                  group: Optional[str] = None, tags: Sequence[str] = ()):
    """``(Collection, images)`` of image file ``path`` (``pssc build
    --image``): its tests, filtered as ``collect`` filters, with no IR
    context, and the image keyed by root for ``runner.run`` so nothing is
    lowered."""
    from ..devenv import build_cmd
    try:
        image, found, meta = build_cmd.load(path)
    except (OSError, build_cmd.BuildError, ValueError) as e:
        raise driver.CompileError(f"--image {path}: {e}")
    items = filter_items(found, k=k, fixture=fixture, group=group,
                         ids=ids, tags=tags)
    root = meta[build_cmd.IMAGE]["root"]
    return (Collection(ctx=None, items=items, found=list(found)),
            {root: image})


def _cmd_test(args: argparse.Namespace) -> int:
    from . import sim_platform
    if sim_platform.is_sim(getattr(args, "platform", None)):
        return sim_platform.run(args)
    tags = getattr(args, "tags", None) or ()
    image_path = getattr(args, "image", None)
    images = None
    if image_path:
        # A usage error (exit 2), not "nothing selected" (5).
        bad = [opt for dest, opt in _NOT_WITH_IMAGE if getattr(args, dest, None)]
        rest, ids = split_id_specs(args.sources)
        if bad or rest:
            print(f"pssc: error: {bad[0]}: not with --image" if bad else
                  f"pssc: error: --image: the tests come from the image; give "
                  f"test IDs (suite::test), not sources ({rest[0]})",
                  file=sys.stderr)
            return EXIT_COMPILE_ERROR
        coll, images = collect_image(image_path, ids=ids, k=args.k,
                                     fixture=args.fixture,
                                     group=getattr(args, "group", None),
                                     tags=tags)
    else:
        coll = collect(args.sources, k=args.k, fixture=args.fixture,
                       group=getattr(args, "group", None), tags=tags,
                       doctest=getattr(args, "doctest", True))
    if getattr(args, "lf", False):
        _select_last_failed(coll)
    from .slice import Slicer
    slicer = Slicer(coll.ctx, coll.paths) if images is None else None
    unchanged = 0
    base = getattr(args, "base", None)
    if base and not getattr(args, "changed", False):
        raise TestCommandError("--base needs --changed")
    if getattr(args, "changed", False) and coll.items:
        if base:
            unchanged = _select_changed_since(coll, slicer, base)
        else:
            unchanged = _select_changed(coll, slicer, replay_options(args),
                                        args.seed)
        if not coll.items:
            # `--json`: stdout is the event stream only
            print(f"--changed: all {unchanged} selected test(s) "
                  + (f"unchanged since {base}" if base else "passed unchanged")
                  + "; nothing to run",
                  file=sys.stderr if getattr(args, "json", False) else sys.stdout)
            return EXIT_OK

    if not coll.items:
        # Not a pass. A filter that matches nothing, or a suite whose tests were
        # never discovered, is the failure mode this runner most needs to avoid
        # reporting as green.
        what = "no tests found"
        group = getattr(args, "group", None)
        rest, ids = split_id_specs(args.sources)
        _, where = split_line_specs(rest)
        if args.k or args.fixture or group or where or ids or tags:
            sel = ", ".join(
                p for p in (*(f"{f}:{n}" for f, n in where), *ids,
                            *(f"-m {t}" for t in tags),
                            f"-k {args.k}" if args.k else None,
                            f"--fixture {args.fixture}" if args.fixture else None,
                            f"--group {group}" if group else None)
                if p)
            what = f"no tests matched {sel}"
        raise TestCommandError(what)

    if args.collect_only and getattr(args, "json", False):
        from .catalog import to_json
        print(json.dumps(to_json(coll.items, slicer), indent=2))
        return EXIT_OK
    if args.collect_only:
        _print_collection(coll, verbose=args.verbose)
        return EXIT_OK

    # The gate runs first: a suite that passes on the VM says nothing about a
    # model the declared targets refuse (`gate`).
    if images is None:
        from . import gate
        gate.check(split_line_specs(split_id_specs(args.sources)[0])[0], coll.roots, coll.items,
                   gate.declared_targets(getattr(args, "targets", None)), ctx=coll.ctx)

    # The Python platform is bound by the run, never by the PSS (R-3). One that
    # does not load is pssc's environment failing, not the model: exit 3, the
    # traceback to a log file and one line on stdout.
    from . import vm
    hosted = None
    spec = vm.declared_platform(getattr(args, "platform", None))
    if spec:
        try:
            hosted = vm.load_platform(spec)
        except vm.PlatformLoadError as e:
            log = _write_platform_log(e)
            print(f"pssc: error: {e} (traceback: {log})")
            return EXIT_INTERNAL

    from .runner import run
    from . import report as _report

    cov_path = getattr(args, "cov", None)
    coverage = None
    if cov_path:
        if getattr(args, "debug", True) is False:
            raise TestCommandError("--cov needs the debug image; drop --no-debug")
        from .coverage import Coverage, boundary, envelope_ops
        try:
            keep = boundary(coll.ctx, coll.items,
                            getattr(args, "cov_scope", None) or ())
        except ValueError as e:
            raise TestCommandError(str(e))
        coverage = Coverage(ops=envelope_ops(coll.ctx, keep))
    elif getattr(args, "cov_scope", None):
        raise TestCommandError("--cov-scope needs --cov PATH")

    trace = None
    trace_mode = getattr(args, "trace", "off") or "off"
    if trace_mode != "off":
        if getattr(args, "debug", True) is False:
            raise TestCommandError("--trace needs the debug image; drop --no-debug")
        trace = (trace_mode, getattr(args, "trace_dir", None) or TRACE_DIR)
    elif getattr(args, "trace_dir", None):
        raise TestCommandError("--trace-dir needs --trace fail|all")

    # `--json`: stdout is the NDJSON event stream and nothing else, so a reader
    # can parse every line; the human report is not printed.
    stream = getattr(args, "json", False)
    seed = args.seed
    if seed is None and hosted is not None:
        seed = hosted.recorded_seed()
    hooks = {}
    replay = replay_options(args)
    if stream:
        if seed is None:
            from .runner import new_seed
            seed = new_seed()
        _report.emit_event(_report.event_run_start(seed, len(coll.items)))
        hooks = dict(
            on_start=lambda item: _report.emit_event(_report.event_test_start(item)),
            on_result=lambda r: _report.emit_event(
                _report.event_test_end(r, args.sources, replay)))
    from .native import engine_name
    try:
        engine = engine_name(getattr(args, "engine", None))
    except ValueError as e:
        raise TestCommandError(str(e))
    sem = getattr(args, "reg_semantics", None)
    if sem:
        from . import reg_semantics as _sem
        try:
            _sem.load(sem)      # a bad file is a usage error, not N test errors
        except (OSError, ValueError, SyntaxError) as e:
            msg = str(e)
            raise TestCommandError(msg if msg.startswith("--reg-semantics")
                                   else f"--reg-semantics {sem}: {msg}")
        sem = os.path.abspath(sem)
    started = time.time()
    results = run(coll, seed=seed, exitfirst=args.exitfirst,
                  strict=getattr(args, "strict", False),
                  runxfail=getattr(args, "runxfail", False),
                  noskip=getattr(args, "noskip", False),
                  debug=getattr(args, "debug", True),
                  platform=hosted,
                  seeds=getattr(args, "seeds", None),
                  jobs=max(getattr(args, "jobs", 1) or 1, 1),
                  coverage=coverage, trace=trace, images=images,
                  engine=engine, reg_semantics=sem, **hooks)
    rc = EXIT_TESTS_FAILED if any(not r.ok for r in results) else EXIT_OK
    if stream:
        _report.emit_event(_report.event_run_end(results, rc, time.time() - started))
    else:
        _report.report_human(results, specs=args.sources,
                             verbose=args.verbose, quiet=args.quiet,
                             options=replay)
    if args.ctrf:
        _report.write_ctrf(results, args.ctrf)
    if getattr(args, "junit", None):
        _report.write_junit(results, args.junit, args.sources, replay)
    write_last_failed(results)
    # Kept once `--changed` has been used: a green suite run without it still
    # leaves no `.pssunit/`.
    if slicer is not None and ((getattr(args, "changed", False) and not base)
                               or os.path.exists(_results_path())):
        _record_results(results, slicer, replay_options(args))
    if getattr(args, "mutate", False):
        if rc != EXIT_OK:
            print("pssc: error: --mutate: the tests must pass first",
                  file=sys.stderr)
            return EXIT_COMPILE_ERROR
        from .mutate import run_mutants
        return run_mutants(args, coll, slicer, time.time() - started)
    if unchanged and not stream:
        print(f"--changed: {unchanged} test(s) "
              + (f"unchanged since {base}" if base else "passed unchanged")
              + ", not run")
    if coverage is not None and cov_path.endswith(".cdb"):
        from .cov_ucis import write_cdb
        write_cdb(coverage.report(keep), cov_path, tests=[
            {"name": r.item.qname, "status": r.status.value, "seed": r.seed,
             "coverage": r.coverage, "engine": {"c": "native"}.get(engine, "pyvm"),
             "platform": getattr(args, "platform", None)} for r in results])
    elif coverage is not None:
        with open(cov_path, "w") as f:
            json.dump(coverage.report(keep), f, indent=2)
            f.write("\n")

    return rc
