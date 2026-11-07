"""Prelude contributions: PSS source an installed plug-in puts in every compile.

A target's own prelude (`Target.prelude`) is in scope for that target alone.
A package of annotation types a plug-in's target reads is not: the same
annotated model has to compile on every other target too, and there it is an
``unknown type``. Passing the package's source by hand in every flow is the
workaround this replaces.

An entry point in the ``pssc.preludes`` group names a callable::

    contribute(target_name, opts) -> list[(name, text)]

``target_name`` is the selected target's name (``None`` for a check that names
no target, ``pssc.Check``), and ``opts`` is the compile's option namespace,
``opts.sources`` included. Each ``(name, text)`` is an in-memory PSS source
unit, processed after the target's own prelude and before the user's sources;
``name`` is what a diagnostic pointing into it reports.

A contribution is in EVERY compile, on every target, so it should declare only
annotations and types: anything it declares can clash with the user's model.
pssc does not check that.

Discovery follows `pssc.targets` and `pssc.styles`: idempotent, never raises,
all or nothing per entry point, and ``PSSC_NO_PLUGINS=1`` skips it. A
contributor that fails to load, or raises or returns something malformed when
called, is recorded (:func:`prelude_errors`) and skipped; the others still
contribute. Contributors run in entry-point NAME order, so the order of the
units does not depend on how the environment happened to list them.

Which units actually reach the parser is :func:`pssc.driver.prelude_units`,
which also drops a unit whose text is byte-identical to a source's or to an
earlier unit's.
"""
from __future__ import annotations

import dataclasses as dc
import os
from typing import Any, Callable, List, Optional, Sequence, Tuple

__all__ = ["PRELUDE_GROUP", "Contributor", "discover", "register",
           "contributors", "contributions", "prelude_errors",
           "prelude_error_report"]

#: The entry-point group prelude contributors advertise themselves in.
PRELUDE_GROUP = "pssc.preludes"

Unit = Tuple[str, str]


@dc.dataclass(frozen=True)
class Contributor:
    """One loaded entry point: its name, its distribution, its callable."""
    name: str
    dist: str
    fn: Callable[[Optional[str], Any], Sequence[Unit]]


_CONTRIBUTORS: List[Contributor] = []
_LOAD_ERRORS: List[object] = []
_CALL_ERRORS: List[object] = []
_discovered = False


def _entry_points():
    from importlib.metadata import entry_points
    try:                                    # 3.10+
        return list(entry_points(group=PRELUDE_GROUP))
    except TypeError:                       # pragma: no cover - 3.9 and older
        return list(entry_points().get(PRELUDE_GROUP, []))


def register(name: str, fn: Callable, dist: str = "") -> None:
    """Add a contributor by hand (an embedding tool, or a test).

    Replaces a contributor already registered under ``name``: an entry-point
    name is unique within its group, and re-registering is how a test swaps
    one out.
    """
    if not callable(fn):
        raise TypeError(
            f"a prelude contributor must be callable as "
            f"(target_name, opts) -> list[(name, text)]; got "
            f"{type(fn).__name__}")
    _CONTRIBUTORS[:] = [c for c in _CONTRIBUTORS if c.name != name]
    _CONTRIBUTORS.append(Contributor(name, dist, fn))
    _CONTRIBUTORS.sort(key=lambda c: (c.name, c.dist))


def contributors() -> Tuple[Contributor, ...]:
    """The contributors loaded, in the order they are asked."""
    return tuple(_CONTRIBUTORS)


def discover(force: bool = False) -> None:
    """Load contributors from the ``pssc.preludes`` group.

    Idempotent, and never raises: a broken plug-in must cost the user its
    contribution, not the compiler. ``force=True`` re-runs it from scratch,
    which is for tests.
    """
    global _discovered
    if _discovered and not force:
        return
    _discovered = True
    _CONTRIBUTORS.clear()
    _LOAD_ERRORS.clear()
    _CALL_ERRORS.clear()

    from .targets import NO_PLUGINS_ENV, PluginError, _dist_of
    if os.environ.get(NO_PLUGINS_ENV):
        return

    try:
        eps = _entry_points()
    except Exception as e:      # pragma: no cover - broken metadata on the path
        _LOAD_ERRORS.append(PluginError("(entry-point scan)", "", e))
        return

    for ep in sorted(eps, key=lambda ep: (ep.name, _dist_of(ep))):
        try:
            register(ep.name, ep.load(), dist=_dist_of(ep))
        except BaseException as e:
            # Broad on purpose, as in target discovery: an import side effect
            # is arbitrary code, and the answer is the same whatever it raised.
            _LOAD_ERRORS.append(PluginError(ep.name, _dist_of(ep), e))


def _checked(c: Contributor, units: Any) -> List[Unit]:
    """``units`` as a list of ``(str, str)``, or a TypeError naming what is
    wrong with it -- for the whole contribution, never for one unit of it."""
    if isinstance(units, (str, bytes)) or not hasattr(units, "__iter__"):
        raise TypeError(
            f"returned {type(units).__name__}; expected a list of "
            f"(name, text) pairs")
    out: List[Unit] = []
    for u in units:
        if (not isinstance(u, (tuple, list)) or len(u) != 2
                or not all(isinstance(x, str) for x in u)):
            raise TypeError(
                f"returned {u!r} in its list; each unit is a (name, text) "
                f"pair of strings")
        out.append((u[0], u[1]))
    return out


def contributions(target_name: Optional[str], opts: Any) -> List[Unit]:
    """Every contributor's units for this compile, in contributor order.

    A contributor that raises, or returns anything but a list of
    ``(name, text)`` pairs, contributes nothing this time and is reported by
    :func:`prelude_errors` until the next call. All or nothing: half a
    contribution is a package with half its types, which fails later and
    further from the cause.
    """
    from .targets import PluginError
    _CALL_ERRORS.clear()
    out: List[Unit] = []
    for c in _CONTRIBUTORS:
        try:
            out += _checked(c, c.fn(target_name, opts))
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            _CALL_ERRORS.append(PluginError(c.name, c.dist, e))
    return out


def prelude_errors() -> Tuple[object, ...]:
    """Contributors that failed to load in the last :func:`discover`, then
    those that failed in the last :func:`contributions` call."""
    return tuple(_LOAD_ERRORS) + tuple(_CALL_ERRORS)


def prelude_error_report() -> List[str]:
    """The failed-contributor block, as lines, or ``[]``.

    Worth saying even when the compile succeeds: the usual symptom of a
    missing contribution is an ``unknown type`` in a model that compiled
    yesterday, and the cause should be on screen beside it.
    """
    errs = prelude_errors()
    if not errs:
        return []
    from .targets import NO_PLUGINS_ENV
    n = len(errs)
    lines = [f"{n} prelude contributor{'' if n == 1 else 's'} failed "
             f"(their PSS is not in scope):"]
    lines += [f"  {e}" for e in errs]
    lines.append(f"  set {NO_PLUGINS_ENV}=1 to skip plugin discovery entirely")
    return lines
