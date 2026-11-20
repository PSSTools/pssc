"""``pssc test``'s first stage after collection: the op-model gate.

A suite that passes on the bytecode VM proves nothing about a model the C
backend then refuses, so ``pssc test`` runs the declared targets' legality
checks (``OpModelTarget.check``: `validate_calls` and friends) before any test
runs. A refusal is a compile error (exit 2), located.

Only the code under test is gated. Test sources and ``pssunit_pkg`` are
exempt: a test may use what no target renders (``rt_c``'s strings, for one).
The gate therefore runs on the *device* subtrees, not on the test roots -- a
refusal of ``pssunit_pkg`` would otherwise stop the gate before it reached the
device's own -- and drops whatever refusal is located in an exempt file.

The targets are ``--targets a,b`` or ``[tool.pssunit] targets = [...]`` in
``./pyproject.toml``; the default is ``op-model-c``. ``--targets none`` gates
nothing.
"""
from __future__ import annotations

import argparse
import os
from typing import Iterable, List, Optional, Sequence, Set

from .. import driver
from . import sources as _sources

DEFAULT_TARGETS = ("op-model-c",)


def declared_targets(cli: Optional[str] = None,
                     pyproject: str = "pyproject.toml") -> List[str]:
    """``--targets`` if given, else ``[tool.pssunit].targets``, else the
    default. ``none`` (or an empty list) gates nothing."""
    if cli is not None:
        names = [n.strip() for n in cli.split(",") if n.strip()]
    else:
        names = list(DEFAULT_TARGETS)
        if os.path.isfile(pyproject):
            import tomllib
            with open(pyproject, "rb") as f:
                cfg = tomllib.load(f).get("tool", {}).get("pssunit", {})
            if "targets" in cfg:
                names = list(cfg["targets"])
    return [] if names == ["none"] else names


def _norm(path) -> str:
    return os.path.realpath(str(path))


def exempt_files(items) -> Set[str]:
    """The files whose refusals do not count: every file that declares a
    test, and the ``pssunit_pkg`` sources."""
    out = {_norm(p) for p in _sources.pssunit_sources()}
    for it in items:
        f = getattr(getattr(it, "loc", None), "file", None)
        if f:
            out.add(_norm(f))
    return out


def _decl_file(dt) -> Optional[str]:
    f = getattr(getattr(dt, "loc", None), "file", None)
    return _norm(f) if f else None


def device_roots(ctx, roots: Iterable[str], items,
                 exempt: Set[str]) -> List[str]:
    """The topmost component types under the test roots that are code under
    test, in first-seen order. A component is test-side when it is declared
    in an exempt file, is a fixture, or holds a ``pssunit_pkg`` component
    (``rt_c``); the walk goes through those into their component fields."""
    from zuspec.ir.core.data_type import DataTypeComponent

    tm = getattr(ctx, "type_map", {}) or {}
    pkg = {_norm(p) for p in _sources.pssunit_sources()}
    fixtures = {it.comp for it in items if getattr(it, "is_fixture", False)}
    names = {id(dt): n for n, dt in tm.items()}

    def fields(dt):
        for f in getattr(dt, "fields", None) or []:
            t = getattr(f, "datatype", None)
            if isinstance(t, DataTypeComponent):
                yield t

    def test_side(name, dt) -> bool:
        return (_decl_file(dt) in exempt or name in fixtures
                or any(_decl_file(t) in pkg for t in fields(dt)))

    out: List[str] = []
    seen: Set[int] = set()

    def walk(dt) -> None:
        if id(dt) in seen:
            return
        seen.add(id(dt))
        name = names.get(id(dt), getattr(dt, "name", ""))
        if _decl_file(dt) in pkg:
            return
        if not test_side(name, dt):
            out.append(name)
            return
        for t in fields(dt):
            walk(t)

    for r in roots:
        if r in tm:
            walk(tm[r])
    return out


def _err_file(err) -> Optional[str]:
    f = getattr(err, "file", None)
    return _norm(f) if f else None


def check(specs: Sequence[str], roots: Iterable[str], items,
          targets: Sequence[str], ctx=None) -> None:
    """Gate each device subtree for each target; raise
    ``driver.CompileError`` with the refusals that are not in an exempt file.

    Each (target, device) pair elaborates a fresh translation: the target's
    ``build_model`` completes the context in place, and the run that follows
    must not see that.

    ``ctx`` is the run's own translation of the same sources, if there is one
    (it is only read, to find the devices). In the daemon the verdict is
    cached on the targets, the devices and the hashes of the files not
    exempt (``devenv.cache.gate``).
    """
    from ..devenv import cache as _cache

    if not targets:
        return
    paths = [str(p) for p in _sources.all_sources(specs)]
    exempt = exempt_files(items)
    probe = ctx if ctx is not None else driver.translate(paths)
    devices = device_roots(probe, roots, items, exempt)
    key = (tuple(targets), tuple(devices),
           _cache.file_hashes([p for p in paths if _norm(p) not in exempt])
           if _cache.enabled() else ())
    _cache.gate(key, lambda: _check(paths, devices, exempt, targets))


def _check(paths, devices, exempt, targets) -> None:
    from .. import targets as _targets

    for tname in targets:
        tgt = _targets.get(tname)
        if not hasattr(tgt, "build_model"):
            raise driver.CompileError(
                f"--targets {tname}: not an operation-model target")
        for dev in devices:
            ctx = driver.translate(paths)
            opts = argparse.Namespace(progseq_root=dev, output_dir=".")
            model = tgt.build_model(ctx, opts)
            if model.total_operations() == 0:
                continue  # nothing exported: nothing for the target to refuse
            try:
                tgt.check(model)
            except driver.CompileError as e:
                kept = [x for x in e.errors if _err_file(x) not in exempt]
                if kept or not e.errors:
                    raise driver.CompileError(
                        f"{tname} refuses {dev}: {e}", kept) from e
            except ValueError as e:
                raise driver.CompileError(
                    f"{tname} refuses {dev}: {e}") from e
