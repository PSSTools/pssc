"""Style policies: the naming and layout decisions a backend asks about.

A backend has two kinds of decision in it. What the generated code MEANS --
which registers exist, what address each sits at, what a masked write does --
is the model's, and no policy may touch it. What the generated code LOOKS LIKE
-- that an operation is called `wb_dma_start`, that the header is `wb_dma.h`,
that a type carries a `_t` suffix -- is a convention, and it is the one thing
organisations actually need to change.

Before this, those two were the same code. Changing `wb_dma_start` to
`WbDma_Start` meant forking the backend, which means a fork that stops tracking
upstream the day it is made. A policy is ~50-150 lines and tracks upstream
automatically, which is the whole point of the tier.

The base class here is language-agnostic and nearly empty on purpose: the C
policy is in `targets/c/style.py`, and an SV or C++ one would sit beside it.
What they share is the *discovery* protocol -- `name`, `target`, and the
`pssc.styles` entry-point group -- not the methods, because "how do you spell a
type name" has no cross-language answer.

Design: docs/design/generator-style-extensions-design.md §2.5.
"""
from __future__ import annotations

import inspect
import os
from typing import Dict, List, Tuple

#: The entry-point group third-party styles advertise themselves in, keyed
#: ``"<target>:<name>"`` -- e.g. ``"op-model-c:acme"``. Keyed by target rather
#: than by style name alone because two targets may both want a style called
#: `acme` and they share no method contract; handing an SV style to the C
#: backend would fail as an AttributeError several hundred lines into a
#: generator, which is the least diagnosable shape a configuration error takes.
STYLE_GROUP = "pssc.styles"

#: Shared with target discovery: one switch turns off ALL plugin loading, which
#: is what makes it usable as a bisect tool.
NO_PLUGINS_ENV = "PSSC_NO_PLUGINS"


class StylePolicy:
    """Base for every style. Subclass the per-language policy, not this."""

    #: Selected as `--style <name>`. Unique per target, not globally.
    name: str = "default"

    #: Which target this styles. The `pssc.styles` group is keyed
    #: `"<target>:<name>"`, so a style for `op-model-c` cannot be handed to
    #: `op-model-sv` by mistake -- the two share no method contract, and the
    #: failure would otherwise be an AttributeError deep in a generator.
    target: str = ""

    #: One-line summary, shown when an unknown style is asked for.
    description: str = ""

    def indent(self) -> str:
        """One level of indentation. Four spaces unless a house style says
        otherwise; the emitters multiply this, they do not hard-code it."""
        return "    "

    def comment_style(self) -> str:
        """`LINE`, `BLOCK` or `HASH` -- see `targets/comments.py`."""
        return "BLOCK"

    def banner(self, model, settings) -> List[str]:
        """The header's opening comment block, already comment-delimited.

        Takes the whole `settings` object rather than the handful of fields
        today's banner names, because the reason a banner exists is that a
        consumer diffing two generated files needs to see which knobs produced
        them -- and a policy that cannot see a knob cannot report it.
        """
        return []

    def __repr__(self) -> str:      # pragma: no cover - diagnostics only
        return f"<{type(self).__name__} {self.target}:{self.name}>"


# -- registry ---------------------------------------------------------------

class StyleError(Exception):
    """A style could not be registered or resolved."""


#: `(target, name)` -> policy instance.
_REGISTRY: Dict[Tuple[str, str], StylePolicy] = {}
_BUILTINS: set = set()
_discovered = False

_STYLE_ERRORS: List[object] = []

#: The one style pssc itself ships. See `_register_builtins`.
_BUILTIN_KEY = ("op-model-c", "default")


def register(style: StylePolicy, replaces: bool = False) -> None:
    """Register one style under ``(style.target, style.name)``.

    Collisions are refused for the same reason target collisions are: a style
    silently replaced by another package's changes what every generated file
    looks like, and nothing in the output says which one produced it.
    """
    if not style.target:
        raise StyleError(f"{type(style).__name__} declares no 'target'")
    if not style.name:
        raise StyleError(f"{type(style).__name__} declares no 'name'")
    key = (style.target, style.name)
    if key in _REGISTRY and not replaces:
        held = _REGISTRY[key]
        raise StyleError(
            f"{type(style).__name__} cannot register style "
            f"'{style.target}:{style.name}': that name is already "
            f"{type(held).__module__}.{type(held).__name__}. Pass "
            f"replaces=True to override it deliberately")
    _REGISTRY[key] = style


def get(target: str, name: str) -> StylePolicy:
    """Resolve ``--style name`` for ``target``.

    The error lists what IS available for that target -- an unknown style is
    almost always a typo or a plugin that failed to load, and both are answered
    by seeing the real list.
    """
    discover()
    try:
        return _REGISTRY[(target, name)]
    except KeyError:
        avail = ", ".join(list_styles(target)) or "(none)"
        raise StyleError(
            f"unknown style '{name}' for target '{target}'; "
            f"available: {avail}") from None


def list_styles(target: str) -> List[str]:
    """The style names registered for one target, sorted."""
    discover()
    return sorted(n for (t, n) in _REGISTRY if t == target)


def registered_styles() -> Dict[Tuple[str, str], StylePolicy]:
    """The whole registry, copied. For `pssc targets` and for tests."""
    discover()
    return dict(_REGISTRY)


def is_builtin(target: str, name: str) -> bool:
    return (target, name) in _BUILTINS


# -- discovery --------------------------------------------------------------

def _coerce(obj) -> List[StylePolicy]:
    """Same four shapes target discovery accepts, for the same reasons."""
    from collections.abc import Iterable
    if isinstance(obj, StylePolicy):
        return [obj]
    if inspect.isclass(obj) and issubclass(obj, StylePolicy):
        return [obj()]
    if not isinstance(obj, (str, bytes)) and isinstance(obj, Iterable):
        out: List[StylePolicy] = []
        for item in obj:
            out.extend(_coerce(item))
        return out
    if callable(obj):
        return _coerce(obj())
    raise TypeError(
        f"expected a StylePolicy subclass, an instance, a callable returning "
        f"either, or an iterable of those; got {type(obj).__name__}")


def _entry_points():
    from importlib.metadata import entry_points
    try:                                    # 3.10+
        return list(entry_points(group=STYLE_GROUP))
    except TypeError:                       # pragma: no cover - 3.9 and older
        return list(entry_points().get(STYLE_GROUP, []))


def style_errors() -> Tuple[object, ...]:
    """Entry points that failed during the last :func:`discover`."""
    return tuple(_STYLE_ERRORS)


def discover(force: bool = False) -> None:
    """Load third-party styles from the ``pssc.styles`` group.

    Never raises, and for the same reason target discovery does not: a broken
    style package must cost you that style, not the compiler.
    """
    global _discovered
    _register_builtins()
    if _discovered and not force:
        return
    _discovered = True
    _STYLE_ERRORS.clear()

    if os.environ.get(NO_PLUGINS_ENV):
        return

    from . import PluginError, _dist_of
    try:
        eps = _entry_points()
    except Exception as e:      # pragma: no cover - broken metadata on the path
        _STYLE_ERRORS.append(PluginError("(entry-point scan)", "", e))
        return

    for ep in eps:
        try:
            saved = dict(_REGISTRY)
            try:
                for st in _coerce(ep.load()):
                    register(st)
            except BaseException:
                _REGISTRY.clear()
                _REGISTRY.update(saved)
                raise
        except BaseException as e:
            _STYLE_ERRORS.append(PluginError(ep.name, _dist_of(ep), e))


def _register_builtins() -> None:
    """Registered LAZILY, from `discover()`, not at import.

    `c/style.py` imports `StylePolicy` from this module, so registering the
    built-in policy at import time is a cycle: importing either half first
    leaves the other half half-built. Deferring costs nothing -- every path
    that reads the registry goes through `discover()` -- and the alternatives
    (a late import at the bottom, a registry keyed by string) both trade a
    real import order for an invisible one.
    """
    if _BUILTIN_KEY in _REGISTRY:
        return
    # Guarded on the registry's CONTENT, not on a "have I run" flag. A test
    # that snapshots and restores the registry would otherwise leave the flag
    # set and the built-in gone -- the registry would then be permanently
    # empty for the rest of the process, which is a worse failure than the
    # duplicate work the flag was saving.
    from .c.style import CStylePolicy
    register(CStylePolicy())
    _BUILTINS.update(_REGISTRY)
