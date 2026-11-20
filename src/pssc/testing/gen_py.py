"""Tests run as generated Python: the op-model-py module of a fixture,
every atomic test exported, driven through the run's fake and doubles.

``build`` translates the sources ``pssc test`` read, rewrites the
``pssunit_pkg`` checks to capture their operands (``check_rewrite``, the
same rewrite pyvm's image gets, so the check table and failure cards
agree), and generates one module with each test as an exported action
(``--export-action``). A test that cannot be exported (an activity, random
attributes: ``export_action``'s refusals) is ``refused``, with the reason.

``run`` runs one exported test on a ``vm.Session``'s platform: the
generated code's import object (``Adapter``) sends each bus access to the
session's platform and each import to the double that serves it on pyvm
(the session's ``use``d doubles, the runner's ``pssunit_pkg`` imports on
the session's ``RunState``, else the platform's method). The steps it
records have ``replay.steps``' shape, for comparing with a pyvm run.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import imports as _imports

#: the generator the tests are run through
TARGET = "op-model-py"
#: accesses a generated run may make before it is stopped (a poll that
#: never sees its bit; pyvm's sanitizers stop it sooner)
STEP_BUDGET = 100000


class GenError(Exception):
    """The generated module could not be built or loaded."""


class StepBudget(Exception):
    """A generated run made more than ``STEP_BUDGET`` accesses."""


@dataclass
class Built:
    """One generated module and how to run its tests."""

    module: ModuleType
    #: the root component's class in ``module``
    root: type
    #: exported test -> its method on ``root``
    methods: Dict[str, str] = field(default_factory=dict)
    #: test -> why it was not exported
    refused: Dict[str, str] = field(default_factory=dict)
    #: test -> why the generator failed on it (a generator bug: an ERROR)
    broken: Dict[str, str] = field(default_factory=dict)
    #: the check table of the rewrite (``check_rewrite.CheckTable``)
    checks: Dict[int, Any] = field(default_factory=dict)
    #: the root's set-up and tear-down methods, in the order they run
    setup: List[str] = field(default_factory=list)
    teardown: List[str] = field(default_factory=list)


def _translate(paths: Sequence[str]):
    from .. import driver
    from . import check_rewrite
    ctx = driver.translate(list(paths))
    if getattr(ctx, "errors", None):
        raise GenError("PSS translation failed: %s" % "; ".join(
            str(e) for e in ctx.errors[:3]))
    checks = check_rewrite.rewrite(ctx)
    return ctx, checks


def _refusal(ctx, test: str) -> Optional[str]:
    from ..targets.export_action import ExportActionError, entry_points
    try:
        entry_points(ctx, [test])
    except ExportActionError as e:
        return str(e)
    return None


def _generate(paths: Sequence[str], comp: str, tests: Sequence[str],
              out_dir: str, target: str = TARGET):
    """Generate the module; ``(ctx, checks, outputs)``. The target mutates
    the context it runs on, so each call translates afresh."""
    from .. import targets as _targets
    ctx, checks = _translate(paths)
    _targets.discover()
    tgt = _targets.get(target)
    ns = argparse.Namespace(progseq_root=comp, export_actions=list(tests),
                            output_dir=out_dir, sources=list(paths))
    resolve = getattr(tgt, "resolve_options", None)
    if callable(resolve):
        ns = resolve(ns)
    return ctx, checks, list(tgt.run(ctx, ns))


@dataclass
class Generated:
    """What ``generate`` made: the outputs and how each test fared."""

    outputs: List[str]
    #: the tests exported
    ok: List[str]
    refused: Dict[str, str]
    broken: Dict[str, str]
    checks: Dict[int, Any]
    setup: List[str]
    teardown: List[str]


def generate(paths: Sequence[str], comp: str, tests: Sequence[str],
             out_dir: str, target: str = TARGET) -> Generated:
    """Run ``target`` (an op-model generator) on ``paths`` with ``comp``
    the root and each of ``tests`` that can be exported an exported
    action, into ``out_dir``; the rest are refused (the export rules) or
    broken (the generator failed on them)."""
    from ..targets.export_action import ExportActionError
    from .catalog import hooks
    ctx, _ = _translate(paths)
    refused = {t: why for t in tests for why in [_refusal(ctx, t)] if why}
    ok = [t for t in tests if t not in refused]
    setup, teardown = hooks(ctx, comp)
    os.makedirs(out_dir, exist_ok=True)
    broken: Dict[str, str] = {}
    try:
        ctx, checks, outputs = _generate(paths, comp, ok, out_dir, target)
    except Exception:
        # refused by the generator rather than by the export rules, or a
        # generator failure: find which test by generating each alone
        # (rare; a translation each)
        for t in list(ok):
            try:
                _generate(paths, comp, [t], out_dir, target)
            except ExportActionError as e:
                refused[t] = str(e)
                ok.remove(t)
            except Exception as e:
                broken[t] = "%s failed on it: %s" % (target, e)
                ok.remove(t)
        ctx, checks, outputs = _generate(paths, comp, ok, out_dir, target)
    return Generated(outputs=[str(p) for p in outputs], ok=ok,
                     refused=refused, broken=broken, checks=checks,
                     setup=[s.rpartition("::")[2] for s in setup],
                     teardown=[s.rpartition("::")[2] for s in teardown])


def build(paths: Sequence[str], comp: str, tests: Sequence[str],
          out_dir: str) -> Built:
    """Generate ``comp``'s module from ``paths`` (``pssc test``'s source
    list) into ``out_dir``, with each of ``tests`` (action types,
    ``<comp>::<action>``) that can be exported as a method of the root."""
    out_dir = os.path.abspath(out_dir)
    g = generate(paths, comp, tests, out_dir)
    module = _load(g.outputs, out_dir)
    from ..targets.py.naming import class_name
    leaf = comp.rpartition("::")[2]
    root = getattr(module, class_name(leaf), None)
    if root is None:
        raise GenError("%s has no class for %s" % (module.__file__, comp))
    return Built(module=module, root=root,
                 methods={t: t.rpartition("::")[2] for t in g.ok},
                 refused=g.refused, broken=g.broken, checks=g.checks,
                 setup=g.setup, teardown=g.teardown)


def _load(outputs: List[str], out_dir: str) -> ModuleType:
    """Import the generated module (not ``pssc_rt``, which it imports)."""
    mods = [p for p in outputs if p.endswith(".py")
            and os.path.basename(p) != "pssc_rt.py"]
    if len(mods) != 1:
        raise GenError("%s generated %d modules, not 1: %s"
                       % (TARGET, len(mods), ", ".join(mods) or "none"))
    path = os.path.join(out_dir, mods[0]) if not os.path.isabs(mods[0]) \
        else mods[0]
    name = "_pssc_gen_%x" % (abs(hash(os.path.abspath(path))))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(os.path.dirname(path))
    return module


class Adapter:
    """The generated code's import object over a ``vm.Session``: the bus
    to its platform, imports to the doubles that serve them on pyvm.
    ``steps``: each access and user import, in order (``replay.steps``)."""

    def __init__(self, session, hints: Sequence[Dict[str, Any]] = ()):
        self._session = session
        self._hints = list(hints)
        self._n = 0
        self.steps: List[Dict[str, Any]] = []
        #: import -> what each call returned, in order (what a generated
        #: SV run is served: ``gen_sv.run``)
        self.returns: Dict[str, List[Any]] = {}
        for w in (8, 16, 32, 64):
            setattr(self, "read%d" % w, self._reader(w))
            setattr(self, "write%d" % w, self._writer(w))

    def _count(self) -> None:
        if len(self.steps) >= STEP_BUDGET:
            raise StepBudget("more than %d bus accesses and imports; "
                             "a poll that never completes?" % STEP_BUDGET)

    def _name(self, op: str, width: int, addr: int) -> None:
        """Tell the fake which register access ``_n`` is: the hint's (the
        reference run's access), when it is the same access. Generated
        code names no register, and an address aliases share decodes to
        the one placed first."""
        n, self._n = self._n, self._n + 1
        h = self._hints[n] if n < len(self._hints) else None
        plat = self._session.platform
        if (h is not None and h.get("reg") and (h["op"], h["width"], h["addr"])
                == (op, width, addr) and hasattr(plat, "note_access")):
            plat.note_access(plat.by_path.get(h["reg"]), 0)

    def _reader(self, width: int):
        rd = getattr(self._session.platform, "read%d" % width)

        def read(addr: int) -> int:
            self._count()
            self._name("read", width, addr)
            v = rd(addr)
            self.steps.append({"kind": "access", "rw": "r", "width": width,
                               "addr": addr, "data": v})
            return v
        return read

    def _writer(self, width: int):
        wr = getattr(self._session.platform, "write%d" % width)

        def write(addr: int, data: int) -> None:
            self._count()
            self.steps.append({"kind": "access", "rw": "w", "width": width,
                               "addr": addr, "data": data})
            self._name("write", width, addr)
            wr(addr, data)
        return write

    def message(self, text: str) -> None:
        out = self._session.out
        if out is not None:
            out(text)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        s = self._session
        impl = s.impls.get(name)
        pkg = _imports.IMPORTS.get("%s::%s" % (_imports.PACKAGE, name))
        plat = getattr(s.platform, name, None)

        def call(*args):
            self._count()
            if pkg is None:
                self.steps.append({"kind": "import", "name": name,
                                   "args": list(args)})
            if impl is not None:
                ret = impl(s.ctx, *args)
            elif pkg is not None and (plat is None or name in _PKG_FIRST):
                ret = pkg(s.run_state, *args)
            elif plat is None:
                raise GenError("the platform (%s) does not implement "
                               "import %s()" % (type(s.platform).__name__, name))
            else:
                ret = plat(*args)
            if ret is not None:
                self.returns.setdefault(name, []).append(ret)
            return ret
        return call


def _pkg_first():
    from . import vm
    return getattr(vm, "_PKG_FIRST", ())


_PKG_FIRST = _pkg_first()


def run(built: Built, test: str, session,
        bases: Optional[Dict[str, int]] = None,
        hints: Sequence[Dict[str, Any]] = (),
        api: Optional[Adapter] = None
        ) -> Tuple[int, int, List[Dict[str, Any]]]:
    """Run exported ``test`` on ``session``'s platform and doubles (a
    fresh ``vm.Session``; ``runner._session_for``): set-up, the test,
    tear-down, as pyvm runs it. ``(checks passed, checks failed, steps)``;
    a ``require`` or ``fail`` that fails ends the run, which still returns
    its counts.

    ``bases`` (component path -> register base, a pyvm run's fake
    ``bases``) places the fake's registers before the run: generated code
    does not name the register it accesses, so the fake knows them only
    by address. ``hints`` (that run's fake ``bus``) name the register of
    each access that is the same as the reference's (``Adapter._name``).
    ``api``: the import object to use (an ``Adapter`` on ``session``,
    to read its ``returns`` after), else a new one."""
    st = session.run_state
    st.checks = built.checks
    fake = session.platform
    for comp, base in (bases or {}).items():
        fake._place(comp, base)
    api = api if api is not None else Adapter(session, hints)
    dut = built.root(api)
    try:
        for name in built.setup:
            getattr(dut, name)()
        getattr(dut, built.methods[test])()
        for name in built.teardown:
            getattr(dut, name)()
    except _imports.RequireFailed as e:
        session.stopped = str(e)
    else:
        session.stopped = None
    return st.ledger.n_pass, st.ledger.n_fail, api.steps


class Cache:
    """The generated modules of one ``pssc test --engine gen-py`` run, one
    per root component, each built on first use with every action test of
    ``items`` under that root, in ``out_dir/<root>``."""

    def __init__(self, paths: Sequence[str], out_dir: str, items):
        self._paths = list(paths)
        self._dir = os.path.abspath(out_dir)
        self._tests: Dict[str, List[str]] = {}
        for item in items:
            if getattr(item, "kind", "action") == "action":
                self._tests.setdefault(item.comp, []).append(item.type_name)
        self._built: Dict[str, Any] = {}

    ENGINE = "gen-py"

    def _build(self, comp: str, tests: List[str], out_dir: str):
        return build(self._paths, comp, tests, out_dir)

    def get(self, comp: str) -> Built:
        """``comp``'s module; raises why it could not be built, each time."""
        if comp not in self._built:
            try:
                if not self._paths:
                    raise GenError("--engine %s generates from the "
                                   "sources, not from an --image"
                                   % self.ENGINE)
                self._built[comp] = self._build(
                    comp, self._tests.get(comp, []),
                    os.path.join(self._dir, comp.replace("::", "_")))
            except Exception as e:
                self._built[comp] = e
        got = self._built[comp]
        if isinstance(got, BaseException):
            raise got
        return got


def _access(e: Optional[Dict[str, Any]]) -> str:
    if e is None:
        return "nothing (the run ended)"
    text = "%s%d 0x%x = 0x%x" % (e["op"], e["width"], e["addr"], e["data"])
    return "%s (%s)" % (text, e["reg"]) if e.get("reg") else text


def diverges(ref: List[Dict[str, Any]], got: List[Dict[str, Any]]
             ) -> Optional[str]:
    """Where the bus ``got`` (a fake's ``bus``) first differs from ``ref``
    (op, width, address, data; the fake's reads are deterministic, so a
    read's data counts too), or None."""
    def key(e):
        return None if e is None else (e["op"], e["width"], e["addr"],
                                       e["data"])
    for i in range(max(len(ref), len(got))):
        a = ref[i] if i < len(ref) else None
        b = got[i] if i < len(got) else None
        if key(a) != key(b):
            return ("diverges from pyvm at bus access %d: expected %s, "
                    "got %s" % (i + 1, _access(a), _access(b)))
    return None
