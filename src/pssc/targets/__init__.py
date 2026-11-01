"""pssc code-generation targets.

Holds the :class:`~pssc.targets.base.Target` ABC and a small internal registry.
Built-in targets register themselves when this package is imported; third-party
targets are discovered from the ``pssc.targets`` entry-point group by
:func:`discover`.

THE TWO RULES DISCOVERY IS BUILT AROUND.

1. *A broken plugin must not break pssc.* An entry point that raises on import,
   returns the wrong kind of object, or declares an API version this pssc does
   not speak is recorded in :data:`_PLUGIN_ERRORS` and skipped. Every other
   plugin still loads and every built-in still works. The alternative --
   propagating the exception -- means one bad package on the system makes
   ``pssc targets`` unusable, which is a worse failure than a missing target.

2. *A plugin must not silently take a name that is already taken.* Shadowing
   ``op-model-c`` by accident produces generated code from a backend the user
   never asked for, and nothing in the output says so. :func:`register` raises
   on a name collision unless the caller names the target it is replacing via
   ``replaces=``, which makes the override deliberate and greppable.

Set ``PSSC_NO_PLUGINS=1`` to skip discovery entirely -- the bisect tool for
"is this pssc's bug or my plugin's".
"""
from __future__ import annotations

import dataclasses as dc
import inspect
import os
import re
from collections.abc import Iterable
from typing import Any, Dict, List, Sequence, Tuple

from .base import Target

__all__ = ["Target", "register", "get", "list_targets", "discover",
           "plugin_errors", "plugin_error_report", "PluginError",
           "PLUGIN_GROUP", "TargetError", "is_builtin"]

#: The entry-point group third-party targets advertise themselves in.
PLUGIN_GROUP = "pssc.targets"

#: Environment variable that disables plugin discovery outright.
NO_PLUGINS_ENV = "PSSC_NO_PLUGINS"

_REGISTRY: Dict[str, Target] = {}
_BUILTINS: set = set()
_discovered = False


class TargetError(Exception):
    """A target could not be registered (collision, bad name, bad API version)."""


@dc.dataclass(frozen=True)
class PluginError:
    """One entry point that failed to contribute a target, and why.

    Carries the exception rather than a rendered traceback: the CLI prints type
    and message (that is what answers "why is my target missing"), while a test
    or an embedding tool can still inspect ``error``.
    """
    entry_point: str
    dist: str
    error: BaseException

    def __str__(self) -> str:
        where = f"{self.entry_point}"
        if self.dist:
            where += f" (from {self.dist})"
        return f"{where}: {type(self.error).__name__}: {self.error}"


_PLUGIN_ERRORS: List[PluginError] = []


def plugin_errors() -> Tuple[PluginError, ...]:
    """Entry points that failed during the last :func:`discover`, in load order."""
    return tuple(_PLUGIN_ERRORS)


def plugin_error_report() -> List[str]:
    """The failed-plugin block, as lines, or ``[]`` if every plugin loaded.

    Type and message, never a traceback: the reader is a user whose target went
    missing, and the actionable content is which package failed and what it
    said. `PSSC_NO_PLUGINS=1` is named because it is how they confirm the
    failure is the plugin's and not pssc's.
    """
    if not _PLUGIN_ERRORS:
        return []
    n = len(_PLUGIN_ERRORS)
    lines = [f"{n} plugin{'' if n == 1 else 's'} failed to load "
             f"(their targets are unavailable):"]
    lines += [f"  {e}" for e in _PLUGIN_ERRORS]
    lines.append(f"  set {NO_PLUGINS_ENV}=1 to skip plugin discovery entirely")
    return lines


# -- registration -----------------------------------------------------------

def register(target: Target, aliases: Sequence[str] = (),
             replaces: Sequence[str] = ()) -> None:
    """Register a target instance under its ``name`` (and any ``aliases``).

    Every name this call would claim -- canonical and alias alike -- must be
    free, or be listed in ``replaces``. An alias collision is as damaging as a
    canonical one: ``-t c-progseq`` resolving to somebody else's backend is
    exactly the silent substitution this guards against.

    ``replaces`` is a list of NAMES, not of targets, so a plugin can take over
    ``op-model-c`` without importing it.
    """
    if not target.name:
        raise ValueError(f"{type(target).__name__} has no 'name'")

    claimed = (target.name,) + tuple(aliases)
    allowed = set(replaces)
    for name in claimed:
        if name in _REGISTRY and name not in allowed:
            held = _REGISTRY[name]
            kind = "built-in" if held.name in _BUILTINS else "target"
            raise TargetError(
                f"{type(target).__name__} cannot register '{name}': that name "
                f"is already the {kind} '{held.name}' "
                f"({type(held).__module__}.{type(held).__name__}). Pass "
                f"replaces=('{name}',) to override it deliberately")

    _check_requires(target)

    # Before the name is claimed: a target that cannot be registered must not
    # be half-registered. `check_overrides` raises when a subclass has taken
    # one half of a paired override -- see `targets.overridable.check_pairs`.
    check = getattr(target, "check_overrides", None)
    if callable(check):
        check()

    for name in claimed:
        _REGISTRY[name] = target


_REQ_CLAUSE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*$")


def _release(text: str) -> Tuple[int, ...]:
    """``"0.3.1"`` -> ``(0, 3, 1)``; a suffix after the numbers is ignored."""
    m = re.match(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in m.group(0).split(".")) if m else ()


def _check_requires(target: Target) -> None:
    """Refuse a target whose :attr:`~Target.requires_pssc` this pssc misses.

    Comma-separated clauses, each an operator and a release (``>=0.3``);
    releases compare numerically, padded with zeros (``0.3 == 0.3.0``).
    """
    spec = getattr(target, "requires_pssc", "") or ""
    if not spec.strip():
        return
    from ..__version__ import version as _version
    have = _release(_version)
    for clause in spec.split(","):
        m = _REQ_CLAUSE.match(clause)
        if m is None:
            raise TargetError(
                f"{type(target).__name__} declares requires_pssc={spec!r}; "
                f"{clause.strip()!r} is not an operator and a release, "
                f"such as '>=0.3'")
        op, want = m.group(1), _release(m.group(2))
        n = max(len(have), len(want))
        a, b = have + (0,) * (n - len(have)), want + (0,) * (n - len(want))
        ok = {">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b,
              ">": a > b, "<": a < b}[op]
        if not ok:
            raise TargetError(
                f"{type(target).__name__} ('{target.name}') requires pssc "
                f"{spec.strip()}; this is pssc {_version}")


def get(name: str) -> Target:
    """Return the registered target ``name`` or raise a clear ``KeyError``."""
    try:
        return _REGISTRY[name]
    except KeyError:
        avail = ", ".join(list_targets()) or "(none)"
        msg = f"unknown target '{name}'; available: {avail}"
        # A failed plugin is the single most likely reason a name the user
        # believes in is not in that list, and nothing else in the session says
        # so. Kept on one line because KeyError renders through repr().
        if _PLUGIN_ERRORS:
            n = len(_PLUGIN_ERRORS)
            msg += (f" ({n} plugin{'' if n == 1 else 's'} failed to load: "
                    + "; ".join(str(e) for e in _PLUGIN_ERRORS) + ")")
        raise KeyError(msg) from None


def list_targets() -> List[str]:
    """Return the sorted canonical target names (aliases excluded)."""
    return sorted(name for name, t in _REGISTRY.items() if name == t.name)


def is_builtin(name: str) -> bool:
    """True if ``name`` resolves to a target that ships with pssc."""
    tgt = _REGISTRY.get(name)
    return tgt is not None and tgt.name in _BUILTINS


# -- discovery --------------------------------------------------------------

def _coerce(obj: Any) -> List[Target]:
    """Turn whatever an entry point resolved to into a list of Target instances.

    Four shapes are accepted, because all four are things a plugin author will
    reasonably write and none is ambiguous:

      * a ``Target`` subclass          -> instantiated
      * a ``Target`` instance          -> used as-is
      * a zero-arg callable            -> called, result coerced again
      * an iterable of the above       -> flattened (a plugin shipping a family)

    A callable returning an iterable is the shape that lets one entry point
    register a whole family without the plugin importing pssc's registry.
    """
    if isinstance(obj, Target):
        return [obj]
    if inspect.isclass(obj) and issubclass(obj, Target):
        return [obj()]
    if not isinstance(obj, (str, bytes)) and isinstance(obj, Iterable):
        out: List[Target] = []
        for item in obj:
            out.extend(_coerce(item))
        return out
    if callable(obj):
        return _coerce(obj())
    raise TypeError(
        f"expected a Target subclass, a Target instance, a callable returning "
        f"either, or an iterable of those; got {type(obj).__name__}")


def _check_api(target: Target, where: str) -> None:
    """Refuse a target built against an incompatible major API version."""
    declared = getattr(target, "PSSC_TARGET_API", None)
    if declared is None:
        raise TargetError(
            f"{type(target).__name__} from {where} declares no "
            f"PSSC_TARGET_API; it does not subclass pssc.targets.Target")
    if not isinstance(declared, int):
        raise TargetError(
            f"{type(target).__name__} from {where} declares "
            f"PSSC_TARGET_API={declared!r}, which is not an integer")
    if declared != Target.PSSC_TARGET_API:
        raise TargetError(
            f"{type(target).__name__} from {where} was built against pssc "
            f"target API {declared}; this pssc speaks API "
            f"{Target.PSSC_TARGET_API}. Upgrade whichever of the two is older")


def _entry_points():
    """The ``pssc.targets`` entry points, across supported importlib versions."""
    from importlib.metadata import entry_points
    try:                                    # 3.10+
        return list(entry_points(group=PLUGIN_GROUP))
    except TypeError:                       # pragma: no cover - 3.9 and older
        return list(entry_points().get(PLUGIN_GROUP, []))


def _dist_of(ep) -> str:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return ""
    name = getattr(dist, "name", None) or getattr(
        getattr(dist, "metadata", None) or {}, "get", lambda _k: None)("Name")
    version = getattr(dist, "version", "")
    return f"{name} {version}".strip() if name else ""


def _load_one(ep) -> None:
    """Load and register everything one entry point contributes -- all or none.

    The snapshot/restore is not defensive coding for its own sake: an entry
    point returning a family can collide on its third target, and leaving the
    first two registered would give the user half a backend family with no
    indication which half is missing.
    """
    where = _dist_of(ep) or ep.name
    targets = _coerce(ep.load())
    if not targets:
        raise TargetError(f"entry point '{ep.name}' contributed no targets")
    for tgt in targets:
        _check_api(tgt, where)

    saved = dict(_REGISTRY)
    try:
        for tgt in targets:
            register(tgt, aliases=getattr(tgt, "aliases", ()),
                     replaces=getattr(tgt, "replaces", ()))
    except BaseException:
        _REGISTRY.clear()
        _REGISTRY.update(saved)
        raise


def discover(force: bool = False) -> None:
    """Load third-party targets from the ``pssc.targets`` entry-point group.

    Idempotent: the CLI and driver both call it unconditionally, so the second
    call must not re-import anything or re-report an error. ``force=True``
    re-runs it, which is for tests that inject entry points.

    Never raises. Anything that goes wrong with one entry point lands in
    :func:`plugin_errors` and is reported by ``pssc targets``.
    """
    global _discovered
    if _discovered and not force:
        return
    _discovered = True
    _PLUGIN_ERRORS.clear()

    if os.environ.get(NO_PLUGINS_ENV):
        return

    try:
        eps = _entry_points()
    except Exception as e:      # pragma: no cover - broken metadata on the path
        _PLUGIN_ERRORS.append(PluginError("(entry-point scan)", "", e))
        return

    for ep in eps:
        try:
            _load_one(ep)
        except BaseException as e:
            # Broad on purpose. A plugin's import side effects are arbitrary
            # code; whatever it raises, the answer is the same -- skip it, say
            # so, and keep the rest of pssc working.
            _PLUGIN_ERRORS.append(PluginError(ep.name, _dist_of(ep), e))


def _register_builtins() -> None:
    from .python_tgt import PythonTarget
    from .sv_tgt import SvTarget
    from .sv_pure_tgt import SvPureTarget
    from .progseq_tgt import ProgSeqTarget
    from .c_progseq_tgt import CProgSeqTarget
    from .cpp_progseq_tgt import CppProgSeqTarget
    from .py_progseq_tgt import PyProgSeqTarget
    from .sw_tgt import (CHostTarget, CHostPresolvedTarget,
                         CEmbeddedTarget, CEmbeddedPresolvedTarget, SvDpiTarget,
                         SvDpiBridgeTarget)

    register(PythonTarget())
    register(SvTarget(), aliases=("sv",))   # `sv` kept as a back-compat alias
    register(SvPureTarget())                # incremental-traversal pure-SV path
    # The operation-model family. Named `op-model-<kind>` so the three read as
    # one family and sort together; the former `<kind>-progseq` names are kept
    # as aliases so existing command lines and flows keep working.
    register(ProgSeqTarget(), aliases=("progseq", "sv-progseq"))
    register(CProgSeqTarget(), aliases=("progseq-c", "c-progseq"))
    register(CppProgSeqTarget(), aliases=("progseq-cpp", "cpp-progseq"))
    register(PyProgSeqTarget(), aliases=("py-progseq",))
    for tgt in (CHostTarget(), CHostPresolvedTarget(),
                CEmbeddedTarget(), CEmbeddedPresolvedTarget(), SvDpiTarget(),
                SvDpiBridgeTarget()):
        register(tgt)

    # Recorded AFTER the fact so the set is derived from what actually
    # registered, not from a hand-maintained second list that can drift.
    _BUILTINS.update(t.name for t in _REGISTRY.values())


_register_builtins()
