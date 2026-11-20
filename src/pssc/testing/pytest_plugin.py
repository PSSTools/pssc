"""pytest-pssunit: PSS tests as pytest items, and a ``pss`` fixture.

Registered through the ``pytest11`` entry point, so every pytest run in an
environment with pssc installed loads it. It is inert until the pytest
configuration names PSS sources (R-7)::

    [tool.pytest.ini_options]
    pssunit_paths = ["tests/pss"]          # files or directories, as `pssc test`
    pssunit_platform = "py:board:Board"    # optional; as `pssc test --platform`

With ``pssunit_paths`` set, each ``.pss`` file under them that pytest visits
is a collector whose items are the catalog's tests declared in it (node ID
``tests/pss/dma.pss::dma_f::t_reset``). They run as ``pssc test`` runs them
(one translation for the run, an image per root component, the run's seed
for every test), so a failure's replay line reproduces it outside pytest.

The ``pss`` fixture opens sessions (``pssc.testing.vm.Session``) on the same
translation, for Python tests that drive operations themselves::

    def test_scale(pss):
        s = pss.load("board_c")
        assert s.call(s.construct(), "scale", 4) == 8
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

import pytest

_PATHS = "pssunit_paths"
_PLATFORM = "pssunit_platform"
_REG_SEMANTICS = "pssunit_reg_semantics"


def pytest_addoption(parser) -> None:
    parser.addini(_PATHS, type="paths", default=[],
                  help="PSS sources (files or directories) whose pssunit "
                       "tests pytest collects; unset: none")
    parser.addini(_PLATFORM, default="",
                  help="the PSS tests' platform: fake or py:<module>[:<Class>]")
    parser.addini(_REG_SEMANTICS, type="paths", default=[],
                  help="register description (IP-XACT .xml or JSON) the "
                       "fields take their kinds from (pssc test "
                       "--reg-semantics)")
    parser.addoption("--pss-seed", type=int, default=None,
                     help="seed for every PSS test (default: a fresh one)")


def _paths(config) -> List[str]:
    return [str(p) for p in config.getini(_PATHS)]


class _Run:
    """What the PSS tests of one pytest run share: the collection (one
    translation), the images built so far, the seed, the platform."""

    def __init__(self, config):
        from . import cmd, runner
        self.specs = _paths(config)
        self.coll = cmd.collect(self.specs)
        spec = config.getini(_PLATFORM) or None
        self.platform_spec = spec
        self.platform = None
        if spec:
            from . import vm
            self.platform = vm.load_platform(spec)
        sem = config.getini(_REG_SEMANTICS)
        self.reg_semantics = str(sem[0]) if sem else None
        self.seed = config.getoption("--pss-seed")
        if self.seed is None and self.platform is not None:
            self.seed = self.platform.recorded_seed()
        if self.seed is None:
            self.seed = runner.new_seed()
        self.regressions = str(config.rootpath / ".pssunit" / "regressions")
        self.images: Dict[str, Any] = {}
        self.functions: Dict[str, List[str]] = {}
        for item in self.coll.items:
            if item.kind == "function":
                self.functions.setdefault(item.comp, []).append(item.decl)

    def by_file(self, path) -> list:
        want = os.path.realpath(str(path))
        return [it for it in self.coll.items
                if getattr(it.loc, "file", None)
                and os.path.realpath(it.loc.file) == want]

    def run(self, item):
        from . import runner
        return runner._run_item(self.images, self.functions, self.coll.ctx,
                                item, seed=self.seed,
                                regressions=self.regressions,
                                platform=self.platform,
                                reg_semantics=self.reg_semantics)

    def replay(self, result) -> str:
        from .report import replay_command
        opts = ["--platform", self.platform_spec] if self.platform_spec else []
        if self.reg_semantics:
            opts += ["--reg-semantics", self.reg_semantics]
        return replay_command(result, self.specs, options=opts)


_RUN = pytest.StashKey[_Run]()


def _run(config) -> _Run:
    if _RUN not in config.stash:
        config.stash[_RUN] = _Run(config)
    return config.stash[_RUN]


def pytest_report_header(config) -> Optional[str]:
    if not _paths(config):
        return None
    seed = config.getoption("--pss-seed")
    return f"pssunit: {', '.join(_paths(config))}" + \
        (f" (seed {seed})" if seed is not None else "")


def pytest_collect_file(file_path, parent):
    if file_path.suffix != ".pss":
        return None
    roots = _paths(parent.config)
    if not roots:
        return None
    path = os.path.realpath(str(file_path))
    for r in roots:
        r = os.path.realpath(r)
        if path == r or path.startswith(r + os.sep):
            return PssFile.from_parent(parent, path=file_path)
    return None


class PssFile(pytest.File):
    """A ``.pss`` file under ``pssunit_paths``: the tests declared in it."""

    def collect(self):
        suites: Dict[str, list] = {}
        for t in _run(self.config).by_file(self.path):
            suites.setdefault(t.qname.rpartition("::")[0], []).append(t)
        for name, tests in suites.items():
            yield PssSuite.from_parent(self, name=name, tests=tests)


class PssSuite(pytest.Collector):
    """A suite (fixture component) of a ``.pss`` file: ``SUITE::TEST`` is
    the node ID's tail, as in ``pssc test SUITE::TEST``."""

    def __init__(self, *, tests, **kw):
        super().__init__(**kw)
        self.tests = tests

    def collect(self):
        for t in self.tests:
            yield PssItem.from_parent(self, name=t.qname.rpartition("::")[2],
                                      test=t)


class PssFailure(Exception):
    """A PSS test that did not pass; carries its ``runner.Result``."""

    def __init__(self, result, replay: str):
        super().__init__(result.status.value)
        self.result = result
        self.replay = replay


class PssItem(pytest.Item):
    """One catalog test. Its verdict is ``pssc test``'s: pass, skip and
    xfail as pytest's own; fail, error and xpass fail the item."""

    def __init__(self, *, test, **kw):
        super().__init__(**kw)
        self.test = test
        for tag in test.tags:   # `-m TAG` selects as in `pssc test`
            self.config.addinivalue_line("markers", f"{tag}: a PSS tag")
            self.add_marker(tag)

    def runtest(self) -> None:
        from .runner import Status
        run = _run(self.config)
        result = run.run(self.test)
        self.user_properties.append(("seed", result.seed))
        if result.status is Status.SKIP:
            pytest.skip(result.detail or "skipped")
        if result.status is Status.XFAIL:
            pytest.xfail(result.detail or "expected failure")
        if not result.ok:
            raise PssFailure(result, run.replay(result))

    def repr_failure(self, excinfo, style=None):
        if not isinstance(excinfo.value, PssFailure):
            return super().repr_failure(excinfo, style=style)
        r = excinfo.value.result
        what = f"{r.status.value}" + \
            (f" ({r.kind.value})" if r.kind.value != "none" else "")
        lines = [f"{r.item.qname}: {what}"]
        lines += [x for x in (r.detail, r.log) if x]
        lines.append(f"replay: {excinfo.value.replay}")
        return "\n".join(lines)

    def reportinfo(self):
        loc = self.test.loc
        return self.path, (loc.line - 1 if getattr(loc, "line", None)
                           else None), self.test.qname


class Engine:
    """The ``pss`` fixture: sessions on the run's translation."""

    def __init__(self, run: _Run):
        self._run = run

    @property
    def ctx(self):
        """The translated sources (``pssunit_paths`` and ``pssunit_pkg``)."""
        return self._run.coll.ctx

    def load(self, root: str, platform=None, seed: int = 0,
             functions: Sequence[str] = ("*",), use: Sequence[Any] = (),
             **kw):
        """A ``vm.Session`` on component ``root``, as ``vm.load`` would
        give from the same sources (``kw``: its ``events``, ``watch``,
        ``sanitize``, ...)."""
        from . import vm
        return vm.from_ctx(self.ctx, root, platform=platform, seed=seed,
                           functions=functions, use=use, **kw)


@pytest.fixture
def pss(request) -> Engine:
    """Sessions on the PSS sources of ``pssunit_paths``."""
    if not _paths(request.config):
        pytest.fail("the pss fixture needs pssunit_paths in the pytest "
                    "configuration", pytrace=False)
    return Engine(_run(request.config))
