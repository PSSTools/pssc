"""Running the tests, on the ZBC (``zuspec-be-bc``) execution path.

One ``translate``, one PSS->Scenario lowering and one ZBC image per root
component; **one fresh object and one fresh frame tree per test**. Isolation is
a property of the architecture rather than something this module maintains --
a run builds its own frames from the image, and the image is immutable, so
there is no state between tests to reset.

WHY BC AND NOT THE LIVE-PYTHON BUILDER. On this path constraints are lowered to
a relocatable dv-solve problem and solved by dv-solve itself, so the values a
test sees come from the same solver -- and the same bytes -- the native engine
will use. There is no backend to select and nothing to pin: ``print`` and
``message`` are implemented inside ZBC, so output does not depend on the host
supplying anything either.

WHY THE LOOP LOOKS LIKE THIS. Each test is an independent frame tree over an
independent object. Nothing here accumulates across iterations and nothing
writes to a buffer another test can see, which is deliberate: it costs nothing
now and is the difference between parallelism later being a change and being a
rewrite. ``-j N`` (``run(jobs=)``) forks workers; results stay in order.
"""
from __future__ import annotations

import os
import random
import time
import traceback
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from . import bc_session
from . import imports as _imports
from . import vm as _vm
from . import reg_semantics as _reg_semantics
from .discover import TestItem


class Status(str, Enum):
    """What happened to one test.

    ERROR is distinct from FAIL on purpose: a FAIL means the model said
    something untrue (a constraint that cannot hold, a check that did not), an
    ERROR means the test never got far enough to say anything. Collapsing them
    loses the distinction between "the design is wrong" and "the test is
    broken", which are read by different people.
    """

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"
    #: An `xfail` test that did fail. Green: the annotation was right.
    XFAIL = "xfail"
    #: An `xfail` test that passed. Red, deliberately -- an unexpected pass
    #: means the annotation is stale, and accepting it silently is how a suite
    #: accumulates marks nobody ever removes.
    XPASS = "xpass"


class Kind(str, Enum):
    """The distinguishable ways a test goes wrong, for failure formatting."""

    NONE = "none"
    UNSAT = "unsat"              # constraints cannot be satisfied
    CHECK = "check"              # a recorded check did not hold
    NO_CHECKS = "no-checks"      # --strict: the test asserted nothing
    UNSUPPORTED = "unsupported"  # the backend cannot express something the model uses
    UNBOUND_IMPORT = "import"    # the model needs an import the run did not supply
    DIVERGED = "diverged"        # --platform replay: not the recorded run;
                                 # --engine gen-py|gen-sv: not the py run's accesses
    EXCEPTION = "exception"      # anything raised out of a body


@dataclass
class Result:
    """One test's outcome. The single source both reports render from.

    The human summary and the CTRF document are both built from a list of these
    rather than one being parsed out of the other's text, so the two can never
    disagree about what happened.
    """

    item: TestItem
    status: Status
    elapsed: float = 0.0
    seed: Optional[int] = None
    kind: Kind = Kind.NONE
    detail: str = ""
    trace: str = ""
    log: str = ""
    #: Check counts read from the model's ledger, when it declares one.
    checks_passed: int = 0
    checks_failed: int = 0
    checks_skipped: int = 0

    @property
    def checks_run(self) -> int:
        return self.checks_passed + self.checks_failed + self.checks_skipped

    #: The status before ``@pssunit_pkg::test`` was taken into account, kept so
    #: a report can say *how* an xfail failed. ``None`` when nothing rewrote it.
    raw_status: Optional[Status] = None

    #: A property test: how many runs it made, and the seed of the run that
    #: failed (``--seed`` with it replays that run first). ``0`` / ``None``
    #: for an ordinary test.
    runs: int = 0
    failing_seed: Optional[int] = None

    #: A shrunk property test failure: each rand field of the test action
    #: and its value in the smallest failing run found (``_shrink``).
    shrunk: Optional[Dict[str, int]] = None

    #: ``--cov``: the test's ``Coverage.report()`` (``run`` merges them).
    coverage: Optional[Dict] = None

    #: ``--trace``: the file the test's events were written to (``trace`` is
    #: the Python traceback).
    trace_file: Optional[str] = None
    #: ``--trace fail``: the traced re-run of the failure did not fail the
    #: same way (the verdict is the first run's).
    trace_flaky: bool = False

    #: Where the test failed, for an editor to mark: one entry per failed
    #: check and located sanitizer finding (:func:`_failure_locations`).
    failures: List[Dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in (Status.PASS, Status.SKIP, Status.XFAIL)




def _solve_failures() -> tuple:
    """Exception types meaning "the model is unsatisfiable" (see bc_session)."""
    return bc_session.solve_failures()


def _lowering_failures() -> tuple:
    """Types meaning "this model uses something the backend cannot express".

    Distinct from a solve failure: the model may be perfectly satisfiable and
    simply out of the backend's current scope. Reporting that as a FAIL would
    blame the design for a tool limitation, and send whoever reads it looking
    for a constraint bug that is not there.
    """
    return bc_session.lowering_failures()


def _classify(exc: BaseException) -> tuple:
    """``(status, kind)`` for an exception out of a test."""
    from .replay import ReplayDivergence
    if isinstance(exc, ReplayDivergence):
        return Status.FAIL, Kind.DIVERGED
    solve = _solve_failures()
    if solve and isinstance(exc, solve):
        return Status.FAIL, Kind.UNSAT
    from .native import NativeUnsupported
    lowering = _lowering_failures() + (NativeUnsupported,)
    if isinstance(exc, lowering):
        return Status.ERROR, Kind.UNSUPPORTED
    return Status.ERROR, Kind.EXCEPTION


def new_seed() -> int:
    """A run seed, when the user gave none. Always reported, never hidden."""
    return random.randrange(1, 2 ** 31)


def _action_fields(ctx, qname: str) -> List[str]:
    """Field names of action ``qname``, in slot order.

    The object must carry them: SOLVE writes results back **by name** and skips
    a name the object does not have, so an object built without them would run
    a solve and silently discard every value it produced.
    """
    dt = getattr(ctx, "type_map", {}).get(qname)
    return [f.name for f in (getattr(dt, "fields", None) or [])]


@dataclass
class _Image:
    """One lowered root component: the ZBC image plus its entry index map."""

    model: object = None
    entries: Dict[str, int] = field(default_factory=dict)
    error: Optional[BaseException] = None
    #: the root's ``@pssunit_pkg::setup`` / ``teardown`` function entries,
    #: in the order they run
    setup: List[int] = field(default_factory=list)
    teardown: List[int] = field(default_factory=list)
    #: action field names by type, for an image read from a file (no IR
    #: context to take them from; ``build_cmd.load``)
    fields: Optional[Dict[str, List[str]]] = None


def _build_image(ctx, comp: str, functions: Sequence[str] = (),
                 debug: bool = True,
                 reg_semantics: Optional[str] = None) -> _Image:
    """Lower one root component to a ZBC image, or capture why it could not be.

    A lowering failure is captured rather than raised because it is scoped to a
    *component*, and every test under that component has to report it -- one
    unsupported construct must not abort the whole run and hide the tests that
    would have passed.

    ``functions`` are the test functions (``<comp type>::<fn>``) to give
    entries to; actions get theirs regardless.

    ``debug`` (the default) lowers for debugging: a marker per PSS statement,
    calls kept as calls, named locals. ``pssc test --no-debug`` turns it off to
    run exactly the release image. ``reg_semantics`` (``--reg-semantics``):
    a register description file the fields take their kinds from.

    The root's set-up and tear-down functions (``catalog.hooks``) are lowered
    with entries too, and every test of the image runs them; so is its
    interrupt handler (``vm.IRQ_HANDLER``), if it has one.
    """
    from .catalog import function_of, hooks
    from .sanitizers import allows

    try:
        setup, teardown = hooks(ctx, comp)
        # the interrupt handler, when the root has one (vm.Session.irq_entry)
        irq = function_of(ctx, comp, _vm.IRQ_HANDLER)
        model = bc_session.lower(ctx, comp,
                                 functions=list(functions) + setup + teardown
                                 + ([irq] if irq else []),
                                 debug=debug,
                                 reg_semantics=(_reg_semantics.load(reg_semantics)
                                                if reg_semantics else None))
        model.allows = allows(ctx)
        entries = bc_session.entries(model)
        return _Image(model=model, entries=entries,
                      setup=[entries[s] for s in setup],
                      teardown=[entries[s] for s in teardown])
    except Exception as e:
        return _Image(error=e)


def _entry_of(image: _Image, item: TestItem) -> int:
    """``item``'s entry in ``image``; KeyError if it has none."""
    # An inherited test lowers under its declaring action's name; a test
    # function under its declaring symbol.
    is_fn = getattr(item, "kind", "action") == "function"
    entry = None if is_fn else image.entries.get(item.name)
    if entry is None and getattr(item, "decl", ""):
        entry = image.entries.get(item.decl)
    if entry is None:
        raise KeyError(
            "%s did not lower to a runnable entry (have: %s)"
            % (item.qname, ", ".join(sorted(image.entries)) or "none"))
    return entry


def _session_for(image: _Image, seed: int, platform=None, cov=None,
                 trace=None, engine: Optional[str] = None,
                 test: Optional[str] = None):
    """A ``vm.Session`` of ``image`` on a fresh run platform: the register
    fake (``--platform fake``), ``platform``'s class around the default one
    (``vm.Hosted``), or the default one. ``cov`` (a ``Coverage``) counts
    the run's statements and branch arms; ``trace`` (a ``TraceWriter``)
    writes its events. ``engine`` (``--engine``) is the engine the test
    runs on; ``test`` (its id) picks a replay platform's trace."""
    from .trace import session_sink
    events, watch = session_sink(cov, trace)
    if platform is not None and getattr(platform, "fake", False):
        plat = _FakeRunPlatform(image.model)
    else:
        plat = _RunPlatform(image.model)
        if platform is not None:
            plat = platform.make(plat, test=test)
    return _vm.Session(image.model, platform=plat, seed=seed,
                       use=platform.doubles if platform is not None else (),
                       events=events, watch=watch,
                       engine=engine)


def _log(lines: List[str]) -> str:
    return "".join(l if l.endswith("\n") else l + "\n" for l in lines)


def run_one(image: _Image, ctx, item: TestItem, seed: int,
            strict: bool = False, platform=None, cov=None,
            trace=None, engine: Optional[str] = None,
            held: Optional[list] = None,
            bounds: Optional[Dict[str, tuple]] = None) -> Result:
    """Solve and run one test against a fresh object and frame tree.

    The verdict has two parts. A test errors if it does not run to completion,
    and it *fails* if its ledger records a failed check -- so a model that runs
    fine while producing a wrong value is a failure, which "did not raise"
    alone cannot express.

    ``strict`` additionally fails a test that recorded no checks at all. Off by
    default because a test that only has to elaborate and solve is legitimate;
    on, because a suite of them looks green while asserting nothing.

    ``platform`` (a ``vm.Hosted``, from ``--platform``) gives the test a new
    instance of its class and its module's doubles; nothing of it outlives
    the test. ``cov`` (a ``Coverage``) counts what the test ran; ``trace``
    (a ``TraceWriter``) writes its events. ``engine``: ``--engine``
    (``native.engine_name``); a test the C engine cannot run yet is an
    ERROR that says why; ``gen-py`` and ``gen-sv`` run it as generated code
    (``_run_gen``).
    ``held`` (a list) gets the run's session (``solved``: the values its
    solves chose). ``bounds`` keeps rand fields within ``(lo, hi)`` in every
    solve (``bc_session.Session.solve_bounds``; a property test's shrink).
    """
    if engine in GEN_ENGINES:
        return _run_gen(image, ctx, item, seed, strict, platform, cov, trace,
                        engine)
    started = time.perf_counter()

    def _fail(exc: BaseException, log: str = "") -> Result:
        return _error(item, seed, started, exc, log)

    if image.error is not None:
        return _fail(image.error)

    try:
        entry = _entry_of(image, item)
    except KeyError as e:
        return _fail(e)

    # The model's output is captured here rather than redirected: ZBC formats it
    # and hands each line to this callback, so nothing of the test's ever
    # reaches the real stdout and the capture keeps working under parallel
    # execution.
    #
    # The run goes through ``vm.Session``, the Python API's path, so the
    # pssunit_pkg checks count, a failed one gets its card and the sanitizers
    # watch the accesses.
    lines: List[str] = []
    try:
        session = _session_for(image, seed, platform, cov, trace, engine,
                               test=item.qname)
    except Exception as e:
        return _fail(e)
    session.out = lines.append
    session.solve_bounds = bounds
    if held is not None:
        held.append(session)
    try:
        passed, failed, skipped = session.run_entry(
            entry, item.qname, seed=seed,
            field_names=(image.fields.get(item.type_name, [])
                         if image.fields is not None
                         else _action_fields(ctx, item.type_name)),
            setup=image.setup, teardown=image.teardown)
    except Exception as e:
        return _fail(e, log=_log(lines))

    out = Result(item=item, status=Status.PASS,
                 elapsed=time.perf_counter() - started, seed=seed,
                 log=_log(lines), checks_passed=passed, checks_failed=failed,
                 checks_skipped=skipped)

    left = getattr(session.platform, "left", None)
    left = left() if callable(left) and not failed else None
    if left:
        out.status = Status.FAIL
        out.kind = Kind.DIVERGED
        out.detail = left
    elif failed:
        out.status = Status.FAIL
        out.kind = Kind.CHECK
        out.detail = "\n".join(_failure_detail(session, item, failed,
                                               out.checks_run))
        out.failures = _failure_locations(session)
    elif strict and out.checks_run == 0:
        out.status = Status.FAIL
        out.kind = Kind.NO_CHECKS
        out.detail = "no checks recorded (--strict)"
    return out


#: ``--engine gen-py``'s generated modules (``gen_py.Cache``), or
#: ``gen-sv``'s builds (``gen_sv.Cache``), set by ``run`` (a ``-j`` worker
#: inherits it by fork)
_GEN: Dict[str, object] = {}
#: the engines that run tests as generated code
GEN_ENGINES = ("gen-py", "gen-sv")


def _run_gen(image: _Image, ctx, item: TestItem, seed: int, strict: bool,
             platform, cov, trace, engine: str = "gen-py") -> Result:
    """``--engine gen-py|gen-sv``: ``item`` on pyvm, the reference (its
    register bases and its bus; ``cov`` and ``trace`` are of this run),
    then as generated Python (``gen_py.run``) on a fresh fake with the same
    doubles, or as generated SV under Verilator (``gen_sv.check``: served
    the reference's reads, its log replayed into a fresh fake). The verdict
    is the generated run's checks; a bus access that is not the
    reference's fails it (``Kind.DIVERGED``). A test that is not exported
    (a test function, an activity: ``Built.refused``) is a SKIP that says
    why."""
    from types import SimpleNamespace
    from . import gen_py
    started = time.perf_counter()
    skip = _gen_skip(item, seed, engine)
    if skip is not None:
        return skip
    if platform is None:
        platform = SimpleNamespace(fake=True, doubles=())
    elif not getattr(platform, "fake", False):
        return _error(item, seed, started, gen_py.GenError(
            "--engine %s runs on --platform fake" % engine))
    try:
        built = _GEN["cache"].get(item.comp)
    except Exception as e:
        return _error(item, seed, started, e)
    if item.type_name in built.broken:
        return _error(item, seed, started,
                      gen_py.GenError(built.broken[item.type_name]))
    held: list = []
    ref = run_one(image, ctx, item, seed, strict=strict, platform=platform,
                  cov=cov, trace=trace, engine="py", held=held)
    if not held or ref.status is Status.ERROR:
        if ref.detail:
            ref.detail = "py reference run: %s" % ref.detail
        return ref
    lines: List[str] = []
    try:
        session = _session_for(image, seed, platform, engine="py",
                               test=item.qname)
        session.out = lines.append
        ref_bus = held[0].platform.bus
        if engine == "gen-sv":
            from . import gen_sv
            py_session = _session_for(image, seed, platform, engine="py",
                                      test=item.qname)
            py_session.out = [].append
            log, diverged, passed, failed = gen_sv.check(
                built, item.type_name, session, ref_bus,
                held[0].platform.bases, py_session)
            lines[:0] = log.out
            if not diverged and log.error:
                raise gen_py.GenError("gen-sv: " + log.error)
        else:
            passed, failed, _ = gen_py.run(built, item.type_name, session,
                                           bases=held[0].platform.bases,
                                           hints=ref_bus)
            diverged = gen_py.diverges(ref_bus, session.platform.bus)
    except Exception as e:
        return _error(item, seed, started, e, log=_log(lines))
    out = Result(item=item, status=Status.PASS,
                 elapsed=time.perf_counter() - started, seed=seed,
                 log=_log(lines), checks_passed=passed, checks_failed=failed)
    if diverged:
        out.status = Status.FAIL
        out.kind = Kind.DIVERGED
        out.detail = "\n".join([diverged] + (_failure_detail(
            session, item, failed, out.checks_run) if failed else []))
    elif failed:
        out.status = Status.FAIL
        out.kind = Kind.CHECK
        out.detail = "\n".join(_failure_detail(session, item, failed,
                                               out.checks_run))
        out.failures = _failure_locations(session)
    elif strict and out.checks_run == 0:
        out.status = Status.FAIL
        out.kind = Kind.NO_CHECKS
        out.detail = "no checks recorded (--strict)"
    return out


def _gen_skip(item: TestItem, seed: int,
              engine: str = "gen-py") -> Optional[Result]:
    """The SKIP of a test ``--engine gen-py|gen-sv`` does not export (a test
    function, or one ``Built.refused`` names), else None. Asked before
    ``run_property`` too, so a property test's skip is not worded as a
    failed run."""
    why = None
    if getattr(item, "kind", "action") == "function":
        why = "a test function is not exported to generated code"
    else:
        try:
            why = _GEN["cache"].get(item.comp).refused.get(item.type_name)
        except Exception:
            return None     # _run_gen reports it
    if why is None:
        return None
    return Result(item=item, status=Status.SKIP, seed=seed,
                  detail="%s: %s" % (engine, why))


def _error(item: TestItem, seed: int, started: float, exc: BaseException,
           log: str = "") -> Result:
    """The result of a run that raised ``exc``."""
    status, kind = _classify(exc)
    detail = str(exc) or type(exc).__name__
    # A VMError (and error()/fatal()) knows the PSS statement it stopped at.
    where = getattr(exc, "location", "")
    if where:
        detail = "%s: %s" % (where, detail)
    return Result(item=item, status=status, kind=kind,
                  elapsed=time.perf_counter() - started, seed=seed,
                  detail=detail,
                  trace="".join(traceback.format_exception(
                      type(exc), exc, exc.__traceback__)),
                  log=log)


class _RunPlatform(_vm.Platform):
    """``vm.Platform`` for ``pssc test``: sparse memory, and a user import
    nothing implements returns 0, as be-bc's default provider does (the CLI
    has no way to supply one yet)."""

    def __init__(self, model):
        super().__init__()
        self._unbound = {
            n.rpartition("::")[2] for n in (getattr(model, "imports", None) or {})
            if "%s::%s" % (_imports.PACKAGE, n.rpartition("::")[2])
            not in _imports.IMPORTS}

    def __getattr__(self, name):
        if name in self.__dict__.get("_unbound", ()):
            return lambda *args: 0
        raise AttributeError(name)


class _FakeRunPlatform(_RunPlatform, _vm.RegisterFake):
    """``--platform fake``: a ``RegisterFake`` (registers keep their values,
    field kinds apply, the bus log decodes) with ``_RunPlatform``'s
    unimplemented user imports."""


def _failure_detail(session, item: TestItem, failed: int, run: int) -> List[str]:
    """What a test whose checks failed reports: the count, why it stopped, a
    failure card per rewritten pssunit_pkg check (else the check's text) and
    the sanitizer findings."""
    out = ["%d of %d check(s) failed" % (failed, run)]
    if session.stopped:
        out.append("stopped: %s" % session.stopped)
    # no repro on the cards: the report ends the failure with the replay line,
    # which also carries the sources and the options (`report.replay_command`)
    cards = session.failure_cards(test=item.qname, repro="")
    out.extend(cards or session.run_state.ledger.failures)
    out.extend(str(f) for f in session.findings)
    return out


def _failure_locations(session) -> List[Dict]:
    """``{file, line, col, kind, message}`` of each failed check whose site
    is known and of each sanitizer finding with provenance (``col`` 0: not
    known); ``kind`` is the check called or ``sanitizer <name>``."""
    out: List[Dict] = []
    st = getattr(session, "run_state", None)
    for cid, ok, _ops in getattr(st, "captured", None) or ():
        site = (getattr(st, "checks", None) or {}).get(cid)
        if ok or site is None or not site.file:
            continue
        out.append({"file": site.file, "line": site.line, "col": site.col,
                    "kind": site.kind, "message": site.what or site.text})
    for f in session.findings:
        parts = (f.where or "").split(":")
        nums = 0
        while nums < 2 and len(parts) - nums > 1 and parts[-1 - nums].isdigit():
            nums += 1
        if not nums:
            continue
        line, col = (int(parts[-nums]), int(parts[-1]) if nums == 2 else 0)
        out.append({"file": ":".join(parts[:-nums]), "line": line, "col": col,
                    "kind": "sanitizer " + f.name, "message": f.what})
    return out


#: Where a property test's failing seeds are kept, relative to the working
#: directory; every seed in a test's file is run before its fresh ones.
REGRESSIONS_DIR = os.path.join(".pssunit", "regressions")


def run_seeds(seed: int, item: TestItem, runs: int) -> List[int]:
    """The seeds of a property test's ``runs`` runs under run seed ``seed``.

    Run 0 uses ``seed`` itself, so ``--seed S`` with a failing run's seed
    replays that run first. The rest come from a generator seeded with the run
    seed and a *stable* hash of the test's name (``hash`` of a ``str`` changes
    with every interpreter), so two tests do not draw the same values and the
    same command always draws the same seeds.
    """
    rng = random.Random(seed ^ zlib.crc32(item.qname.encode()))
    out = [seed]
    while len(out) < runs:
        s = rng.randrange(1, 2 ** 31)
        if s not in out:
            out.append(s)
    return out[:max(runs, 1)]


def regressions_file(directory: str, item: TestItem) -> str:
    """``<directory>/<qname with :: as .>.seeds``."""
    return os.path.join(directory, item.qname.replace("::", ".") + ".seeds")


def read_regressions(path: str) -> List[int]:
    """The seeds recorded in ``path``, in order; none if it does not exist."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return []
    out: List[int] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            try:
                v = int(line, 0)
            except ValueError:
                continue
            if v not in out:
                out.append(v)
    return out


def _record_regression(path: str, seed: int) -> None:
    if seed in read_regressions(path):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write("%d\n" % seed)


#: The most re-runs a failing property test's shrink makes.
SHRINK_RUNS = 64


def _shrinkable(r: Result, engine: Optional[str], platform) -> bool:
    """Whether failing run *r* can be shrunk: on the Python engine (the C
    engine and generated code take no bounds; their seed is the repro), not
    on a replay (other values are not the recorded run), and not if the
    failure is an unsat solve (a bound could only cause another)."""
    from . import native
    try:
        if native.engine_name(engine) != "py":
            return False
    except ValueError:
        return False
    if platform is not None and getattr(platform, "replay", None) is not None:
        return False
    return r.kind not in (Kind.UNSAT, Kind.DIVERGED)


def _shrink(r: Result, solved: Dict[str, int], rerun) -> tuple:
    """The smallest failing run found from failing run *r*, whose test
    action's rand fields took the values *solved*: the same seed re-run by
    ``rerun(bounds)`` (-> the run and its solved values), each field in turn
    binary-searched down from its value: a probe pins the field to ``mid``
    (or, if that is unsat, bounds it to ``[0, mid]``), and ``mid`` is kept
    if the run still fails the same way. A shrunk field stays pinned while
    the next is searched. At most :data:`SHRINK_RUNS` re-runs. Returns the
    run and ``", shrunk: f=v (from w), ..."`` (empty if nothing shrank); the
    run's ``shrunk`` has every field's value."""
    names = sorted(n for n, v in solved.items() if "." not in n and v < 1 << 63)
    best, bounds, runs, now = r, {}, 0, dict(solved)

    def fails(probe):
        return probe.status is r.status and probe.kind is r.kind

    for n in names:
        lo, hi = 0, now[n]                   # hi fails; is anything below it?
        while lo < hi and runs < SHRINK_RUNS:
            mid = (lo + hi) // 2
            runs += 1
            probe, vals = rerun({**bounds, n: (mid, mid)})
            if probe.kind is Kind.UNSAT and runs < SHRINK_RUNS:
                runs += 1
                probe, vals = rerun({**bounds, n: (0, mid)})
            if fails(probe):
                best, now = probe, vals
                hi = min(mid, vals.get(n, mid))
            else:
                lo = mid + 1
        bounds[n] = (now.get(n, hi), now.get(n, hi))
    best.shrunk = {n: now[n] for n in names if n in now}
    moved = ["%s=%d (from %d)" % (n, now[n], solved[n])
             for n in names if now.get(n, solved[n]) != solved[n]]
    return best, (", shrunk: " + ", ".join(moved)) if moved else ""


def run_property(image: _Image, ctx, item: TestItem, seed: int,
                 strict: bool = False,
                 regressions: Optional[str] = REGRESSIONS_DIR,
                 platform=None, runs: Optional[int] = None,
                 cov=None, trace=None, engine: Optional[str] = None) -> Result:
    """Run a ``@pssunit_pkg::property`` test once per seed, re-solving each time.

    ``runs`` overrides the annotation's run count (``--seeds N``).

    Recorded failing seeds run first, then ``run_seeds``; the first run that
    is not a PASS ends the test and is its result, with ``failing_seed`` set
    and the seed appended to the regression file (``regressions=None`` keeps
    no file). A passing test's result sums the runs' time and checks.

    On the Python engine a failing run is then shrunk (:func:`_shrink`): the
    result is the smallest failing run found, its values in ``shrunk``.
    """
    n = max(runs or item.property_runs or 1, 1)
    path = regressions_file(regressions, item) if regressions else None
    seeds = read_regressions(path) if path else []
    seeds += [s for s in run_seeds(seed, item, n) if s not in seeds]
    total = Result(item=item, status=Status.PASS, seed=seed)
    for i, s in enumerate(seeds):
        held: list = []
        r = run_one(image, ctx, item, s, strict=strict, platform=platform,
                    cov=cov, trace=trace, engine=engine, held=held)
        total.elapsed += r.elapsed
        if r.status is not Status.PASS:
            shrunk = ""
            if held and _shrinkable(r, engine, platform):
                def rerun(b, s=s):
                    h: list = []
                    p = run_one(image, ctx, item, s, strict=strict,
                                platform=platform, engine=engine, held=h,
                                bounds=b)
                    return p, (dict(h[0].solved) if h else {})
                r, shrunk = _shrink(r, dict(held[0].solved), rerun)
            r.seed, r.runs, r.failing_seed = seed, i + 1, s
            r.elapsed = total.elapsed
            r.detail = "run %d of %d failed (seed %d%s): %s" % (
                i + 1, len(seeds), s, shrunk, r.detail)
            if path and image.error is None:
                _record_regression(path, s)
            return r
        total.checks_passed += r.checks_passed
        total.checks_skipped += r.checks_skipped
        total.log = r.log
    total.runs = len(seeds)
    return total


def _skipped(item: TestItem, seed: int) -> Result:
    """The result of a test that ``@pssunit_pkg::test{.skip=true}`` withheld.

    Built without touching the image: skipping a test whose component would not
    even lower has to work, since a skip mark is often exactly what stands in
    for "this does not build yet".

    The reason is carried into ``detail`` so the report can print it. A skip
    with no reason stays a skip -- unhelpful, but the annotation permits it and
    inventing text here would read as though the author had written it.
    """
    return Result(item=item, status=Status.SKIP, seed=seed,
                  detail=item.meta.reason)


def _settle_expect_error(result: Result) -> Result:
    """Apply ``@pssunit_pkg::expect_error {.text}``: the test passes only if it
    stopped with an error (``error()``, ``fatal()``, anything raised) whose
    message contains ``text``.

    Anything else fails, saying what was expected and what happened: a pass, a
    failed check, an error with other text. A lowering or solve failure is not
    the error a test can expect -- it says nothing about the body -- so it is
    left as it is. The error's location stays in ``detail``.
    """
    text = getattr(result.item, "expect_error", None)
    if text is None or result.status is Status.SKIP:
        return result
    if result.kind in (Kind.UNSUPPORTED, Kind.UNSAT):
        return result
    want = "expected an error containing %r" % text
    if result.status is Status.ERROR and text in result.detail:
        result.status, result.kind = Status.PASS, Kind.NONE
        result.detail = "expected error: " + result.detail
    elif result.status is Status.ERROR:
        result.status = Status.FAIL
        result.detail = "%s; got: %s" % (want, result.detail)
    elif result.status is Status.PASS:
        result.status = Status.FAIL
        result.detail = want + "; the test ran to completion"
    else:
        result.detail = "%s; got: %s" % (want, result.detail)
    return result


def _settle_xfail(result: Result) -> Result:
    """Rewrite an ``xfail`` test's status to XFAIL or XPASS.

    Applied here rather than at report time so that every consumer -- the human
    summary, CTRF, and the exit code -- reads one settled verdict instead of
    each re-deriving it from ``meta`` and the raw status. Two consumers deriving
    the same thing separately is how a runner ends up reporting one outcome and
    exiting with another.
    """
    if not result.item.meta.xfail or result.status is Status.SKIP:
        return result
    result.raw_status = result.status
    result.status = (Status.XPASS if result.status is Status.PASS
                     else Status.XFAIL)
    if result.status is Status.XPASS:
        result.detail = "expected to fail, but passed" + (
            f" ({result.item.meta.reason})" if result.item.meta.reason else "")
    return result


def _run_item(images: Dict[str, "_Image"], functions: Dict[str, List[str]],
              ctx, item: TestItem, seed: int, strict: bool = False,
              runxfail: bool = False, noskip: bool = False,
              debug: bool = True,
              regressions: Optional[str] = REGRESSIONS_DIR,
              platform=None, seeds: Optional[int] = None,
              cov=False, trace=None,
              engine: Optional[str] = None,
              reg_semantics: Optional[str] = None) -> Result:
    """One test of ``run``: skip, build (or reuse) its image, run, settle.
    ``cov``: count its coverage into ``Result.coverage`` (True, or the
    envelope's operations: ``Coverage.ops``). ``trace``:
    ``(mode, dir)`` of ``--trace``; ``all`` writes its events to
    ``Result.trace_file``, ``fail`` re-runs a failure to write them."""
    if item.meta.skip and not noskip:
        return _skipped(item, seed)
    if item.comp not in images:
        from ..devenv import cache as _cache
        fns = tuple(functions.get(item.comp, ()))
        # the file's time keeps an edited description from a stale image
        sem = (reg_semantics, os.path.getmtime(reg_semantics)) \
            if reg_semantics else None
        images[item.comp] = _cache.image(
            ctx, (item.comp, fns, debug, sem),
            lambda: _build_image(ctx, item.comp, fns, debug=debug,
                                 reg_semantics=reg_semantics))
    image = images[item.comp]
    counts = None
    if cov is not False and cov is not None and image.error is None:
        from .coverage import Coverage
        counts = Coverage(image.model,
                          ops=cov if isinstance(cov, dict) else None)
    mode, tdir = trace if trace is not None else ("off", None)
    if mode == "all" and image.error is None:
        result = _traced(tdir, image, item, seed, lambda w: _run_image(
            image, ctx, item, seed, strict, regressions, platform, seeds,
            counts, w, engine))
    else:
        result = _run_image(image, ctx, item, seed, strict, regressions,
                            platform, seeds, counts, None, engine)
    if counts is not None:
        result.coverage = counts.report()
    first = result.status
    result = _settle_expect_error(result)
    result = result if runxfail else _settle_xfail(result)
    if mode == "fail" and not result.ok and image.error is None:
        # Re-run the failure once, traced, with the seed that failed; no
        # coverage and no regression record, so the re-run changes nothing
        # but the trace. A re-run that does not fail again is a flaky test:
        # the card says so, and the verdict stays the first run's.
        s = result.failing_seed if result.failing_seed is not None else seed
        again = _traced(tdir, image, item, s, lambda w: run_one(
            image, ctx, item, s, strict=strict, platform=platform, trace=w,
            engine=engine))
        result.trace_file = again.trace_file
        result.trace_flaky = again.status is not first
    return result


def _traced(directory: str, image: "_Image", item: TestItem, seed: int,
            body) -> Result:
    """``body(writer)``'s result, its events written to ``item``'s trace
    file in ``directory`` (``Result.trace_file``)."""
    from .trace import trace_path
    path = trace_path(directory, item.qname)
    os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        result = body(_trace_writer(f, image, item, seed))
    result.trace_file = path
    return result


def _trace_writer(f, image: "_Image", item: TestItem, seed: int):
    from .trace import TraceWriter
    return TraceWriter(f, image.model, test=item.qname, seed=seed)


def _run_image(image: "_Image", ctx, item: TestItem, seed: int, strict: bool,
               regressions: Optional[str], platform, seeds: Optional[int],
               counts, trace, engine=None) -> Result:
    """Run ``item`` on its built ``image``: once, or per seed (property
    tests, ``--seeds N``)."""
    prop = getattr(item, "property_runs", None) is not None
    if engine in GEN_ENGINES and (prop or (seeds or 1) > 1):
        skip = _gen_skip(item, seed, engine)
        if skip is not None:
            return skip
    if prop or (seeds or 1) > 1:
        # --seeds N runs every test N times; a property test the larger of
        # N and its own count. Only property tests keep regression seeds.
        return run_property(image, ctx, item, seed,
                            strict=strict,
                            regressions=regressions if prop else None,
                            platform=platform,
                            runs=max(item.property_runs or 1, seeds or 1),
                            cov=counts, trace=trace, engine=engine)
    return run_one(image, ctx, item, seed,
                   strict=strict, platform=platform, cov=counts, trace=trace,
                   engine=engine)


#: What a ``-j`` worker inherits (by fork) from ``_run_parallel``.
_WORKER: Dict[str, object] = {}


def _worker_run(i: int) -> Tuple[int, Result]:
    w = _WORKER
    coll = w["coll"]
    r = _run_item(w["images"], w["functions"], coll.ctx, coll.items[i],
                  **w["opts"])
    r.item = None       # the parent has it; an AST location need not pickle
    return i, r


def _run_parallel(coll, jobs: int, exitfirst: bool,
                  functions: Dict[str, List[str]], opts: Dict,
                  on_start=None, on_result=None,
                  images: Optional[Dict[str, "_Image"]] = None) -> List[Result]:
    """``run`` over a pool of ``jobs`` forked processes, each with its own
    images. Results come back in collection order, and ``exitfirst`` cuts
    them after the first failure, so the verdicts equal a ``-j1`` run's.
    ``on_start`` is called for a test just before its ``on_result``: the
    parent does not see a worker start one."""
    import multiprocessing as mp
    _WORKER.clear()
    _WORKER.update(coll=coll, images=dict(images or {}), functions=functions,
                   opts=opts)
    results: List[Result] = []
    try:
        with mp.get_context("fork").Pool(jobs) as pool:
            for i, r in pool.imap(_worker_run, range(len(coll.items))):
                r.item = coll.items[i]
                if on_start is not None:
                    on_start(r.item)
                if on_result is not None:
                    on_result(r)
                results.append(r)
                if exitfirst and not r.ok:
                    break
    finally:
        _WORKER.clear()
    return results


def run(coll, seed: Optional[int] = None,
        exitfirst: bool = False, strict: bool = False,
        runxfail: bool = False, noskip: bool = False,
        debug: bool = True,
        regressions: Optional[str] = REGRESSIONS_DIR,
        platform=None, seeds: Optional[int] = None,
        jobs: int = 1, on_start=None, on_result=None,
        coverage=None, trace=None,
        images: Optional[Dict[str, "_Image"]] = None,
        engine: Optional[str] = None,
        reg_semantics: Optional[str] = None) -> List[Result]:
    """Run every test in ``coll``.

    Every test gets the run's seed rather than a derived per-test seed, so
    re-running one test with ``--seed N`` reproduces exactly what it did inside
    the suite. That is the property the replay line printed on failure depends
    on. A property test runs several seeds (``run_property``) and keeps its
    failing ones under ``regressions`` (``None``: keep none). ``seeds``
    (``--seeds N``) runs every test that many times the same way.

    Images are built per root component and cached, so a suite whose tests share
    a component lowers once. ``jobs`` > 1 runs the tests in that many forked
    processes, each building the images it needs; the results are those of a
    sequential run, in the same order.

    ``runxfail`` and ``noskip`` are the escape hatches for the two annotations
    that make a test *not* report what it did. Both exist because an annotation
    that cannot be turned off is a place for a real failure to hide: ``--noskip``
    answers "is this still broken?" and ``--runxfail`` answers "what is it
    actually doing?" without editing the source to find out.

    ``platform`` (``vm.Hosted``) is the run's Python platform, bound per test.
    ``on_start(item)`` and ``on_result(result)`` are called as each test
    starts and ends (``--json`` streams them).

    ``coverage`` (a ``Coverage``, ``--cov``) gets every test's counts; each
    test counts its own (``Result.coverage``), so a ``-j`` worker's come
    back with its results. ``trace`` (``(mode, dir)``, ``--trace``) writes
    a test's events to ``Result.trace_file``. ``images`` are images already
    built, by root (``--image``: read from a file; ``coll.ctx`` is None).
    ``engine`` (``--engine``): ``py`` or ``c``, the engine every test runs on.
    ``reg_semantics`` (``--reg-semantics``): the register description file
    the images' field kinds come from.
    """
    if seed is None:
        seed = new_seed()

    # With coverage, an image lowers every test function its component has,
    # selected or not: the points (and so a .cdb's schema) are the same for
    # a -k run as for the whole suite, and the runs merge.
    functions: Dict[str, List[str]] = {}
    found = getattr(coll, "found", None) if coverage is not None else None
    for item in found or coll.items:
        if getattr(item, "kind", "action") == "function":
            functions.setdefault(item.comp, []).append(item.decl)
    if engine in GEN_ENGINES:
        from . import gen_py, gen_sv
        _GEN["cache"] = (gen_sv if engine == "gen-sv" else gen_py).Cache(
            getattr(coll, "paths", None) or [],
            os.path.join(".pssunit", engine), coll.items)
    opts = dict(seed=seed, strict=strict, runxfail=runxfail, noskip=noskip,
                debug=debug, regressions=regressions, platform=platform,
                seeds=seeds,
                cov=coverage.ops if coverage is not None else False,
                trace=trace,
                engine=engine, reg_semantics=reg_semantics)
    if jobs > 1 and len(coll.items) > 1:
        results = _run_parallel(coll, jobs, exitfirst, functions, opts,
                                on_start, on_result, images)
        _merge_coverage(coverage, results)
        return results

    images = dict(images or {})
    results: List[Result] = []
    for item in coll.items:
        if on_start is not None:
            on_start(item)
        results.append(_run_item(images, functions, coll.ctx, item, **opts))
        if on_result is not None:
            on_result(results[-1])
        if exitfirst and not results[-1].ok:
            break
    _merge_coverage(coverage, results)
    return results


def _merge_coverage(coverage, results: List[Result]) -> None:
    if coverage is None:
        return
    for r in results:
        if r.coverage:
            coverage.merge(r.coverage)
