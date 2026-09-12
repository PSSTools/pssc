"""What a backend PUBLISHES as overridable, and what that promises.

Every public method of a class is overridable in Python. That is the problem
this module solves rather than the feature it provides: without a marked set,
"the override surface" is whatever an extension author happened to reach for,
pssc cannot change any of it without breaking someone, and nobody can tell
which methods were designed to be wrapped from which merely happen to be
callable.

`@overridable` marks the ones that were designed for it and states what the
promise is:

  * **stable** -- the signature and the meaning will not change without a
    deprecation window. Override it freely.
  * **provisional** -- published for a real extension to use, and expected to
    move. Override it, and read the release notes.

`docs/override-surface.json` is the checked-in manifest of the marked set, and
`tests/unit/test_override_surface.py` fails when code and manifest disagree.
That is the whole mechanism: growing the surface takes a diff in two files, so
it is a decision somebody makes rather than a thing that happens (design I14).

`pairs_with` records the other half of a method that cannot be overridden
alone -- see :func:`check_pairs`.

Design: docs/design/generator-style-extensions-design.md §2.6.2, §6.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Sequence, Tuple

#: The attribute the decorator leaves behind. Namespaced: it lands on methods
#: of classes pssc does not own (a plugin's subclass inherits it).
MARK = "__pssc_overridable__"

STABILITIES = ("stable", "provisional")


class OverrideError(Exception):
    """An override surface was declared or used wrongly."""


@dc.dataclass(frozen=True)
class OverrideInfo:
    """What is promised about one overridable member."""
    name: str
    since: str
    stability: str
    pairs_with: Tuple[str, ...] = ()
    #: Set for a marked CLASS ATTRIBUTE (`style_cls`, `body_emitter_cls`),
    #: which is a collaborator to swap rather than a method to wrap. It is part
    #: of the same published surface and carries the same promise.
    attribute: bool = False

    def as_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"since": self.since, "stability": self.stability}
        if self.pairs_with:
            out["pairs_with"] = list(self.pairs_with)
        if self.attribute:
            out["attribute"] = True
        return out


def overridable(*, since: str, stability: str = "provisional",
                pairs_with: Sequence[str] = ()):
    """Mark a method as part of the published override surface.

    The docstring of a marked method must state what it PROMISES -- what it is
    handed, what it must return, and what the caller does with it -- because
    that is the contract an extension author codes against and the one pssc is
    then held to.
    """
    if stability not in STABILITIES:
        raise OverrideError(
            f"stability must be one of {STABILITIES}, not {stability!r}")

    def deco(fn):
        setattr(fn, MARK, OverrideInfo(name=fn.__name__, since=since,
                                       stability=stability,
                                       pairs_with=tuple(pairs_with)))
        return fn
    return deco


def overridable_attr(*, since: str, stability: str = "provisional",
                     pairs_with: Sequence[str] = ()):
    """`@overridable` for a class ATTRIBUTE, which cannot carry an attribute
    of its own. Declared on the owning class as
    ``overridable_attrs = {"style_cls": overridable_attr(...)}``."""
    if stability not in STABILITIES:
        raise OverrideError(
            f"stability must be one of {STABILITIES}, not {stability!r}")
    return OverrideInfo(name="", since=since, stability=stability,
                        pairs_with=tuple(pairs_with), attribute=True)


def surface(cls) -> Dict[str, OverrideInfo]:
    """The marked members of ``cls``, including inherited ones, by name.

    Walks the MRO so a subclass that overrides a marked method without
    re-marking it still shows up as the same published member -- an extension
    does not re-publish pssc's surface by using it.
    """
    found: Dict[str, OverrideInfo] = {}
    for klass in reversed(cls.__mro__):
        for name, info in (getattr(klass, "overridable_attrs", None) or {}).items():
            found[name] = dc.replace(info, name=name)
        for name, value in vars(klass).items():
            info = getattr(value, MARK, None)
            if isinstance(info, OverrideInfo):
                found[name] = info
    return found


def manifest_for(cls) -> Dict[str, Any]:
    """``cls``'s marked surface, in the manifest's JSON shape."""
    return {name: info.as_json() for name, info in sorted(surface(cls).items())}


# -- pairs -------------------------------------------------------------------

def _defining_class(cls, name):
    """The class in ``cls``'s MRO that actually defines ``name``."""
    for klass in cls.__mro__:
        if name in vars(klass):
            return klass
    return None


def overridden(cls, base, name: str) -> bool:
    """Did ``cls`` (or something between it and ``base``) redefine ``name``?"""
    owner = _defining_class(cls, name)
    return owner is not None and owner is not _defining_class(base, name)


def check_pairs(cls, base=None) -> None:
    """Refuse a subclass that overrides one half of a declared pair.

    Some members are two halves of one decision -- the include guard's open and
    close agree on a macro name; the API types and the register value unions
    partition one set of type declarations between them. Overriding one and
    inheriting the other does not fail loudly: it produces a header that is
    subtly wrong (an unbalanced guard, one type declared twice) and compiles
    far from the override that caused it.

    Checked at target REGISTRATION, so the answer arrives before any model is
    read, rather than in a C compiler's output for a generated file.
    """
    base = base or _root_of(cls)
    if base is None or cls is base:
        return
    marked = surface(base)
    for name, info in sorted(marked.items()):
        if not info.pairs_with or not overridden(cls, base, name):
            continue
        missing = [other for other in info.pairs_with
                   if not overridden(cls, base, other)]
        if missing:
            raise OverrideError(
                f"{cls.__module__}.{cls.__qualname__} overrides '{name}' but "
                f"not {missing}, and they are two halves of one decision -- "
                f"see {base.__name__}.{name}'s docstring. Override both, or "
                f"neither: the failure otherwise is a generated file that is "
                f"wrong somewhere the override does not appear")


def _root_of(cls):
    """The highest class in ``cls``'s MRO that publishes a surface -- the one
    the pairs were declared on."""
    candidates = [k for k in cls.__mro__
                  if any(getattr(v, MARK, None) for v in vars(k).values())
                  or getattr(k, "overridable_attrs", None)]
    return candidates[-1] if candidates else None


# -- reporting ---------------------------------------------------------------

def report(cls) -> List[str]:
    """The surface as lines, for `pssc targets --overrides`."""
    lines = [f"{cls.__module__}.{cls.__qualname__}"]
    for name, info in sorted(surface(cls).items()):
        kind = "attr" if info.attribute else "method"
        extra = (f"  pairs with {', '.join(info.pairs_with)}"
                 if info.pairs_with else "")
        lines.append(f"  {name:22} {info.stability:12} {kind:6} "
                     f"since {info.since}{extra}")
    return lines
