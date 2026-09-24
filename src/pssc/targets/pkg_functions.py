"""Package-scope functions an operation model calls.

A PSS function declared outside any component (LRM 22.2) is part of the model
the same way a component's operations are: `function int sq(int x)` called
from an operation is computation the generated code has to carry. It is not an
import -- it has a body, and the environment supplies nothing.

Which ones: those REACHABLE from what the model lowers -- the operations, the
constructors and the entry points -- through calls, transitively. A package
declares many functions a given model never calls, and generating them would
put code in the output whose legality nothing has checked.

Language-neutral, like `body_walker.py`: this says which functions, in which
context each runs, and how a call names one. Spelling is the backend's.

Context. A `target function` runs in target context and a `solve function` in
solve context. A function with neither qualifier may be called from both (LRM
22.2.3), and its body is checked in the context of each caller that reaches it
-- so an unqualified function that calls `write32` is legal from an operation
and a diagnostic from a constructor, which is where the LRM puts the line.

Which function a call names is the LINKER's answer, not this module's: the
front end translates a call it resolved to a package-scope function as
`ExprCall(func=ExprRefUnresolved(<qualified name>))`, and a call to a component's
own function as a member of `self` (`ast2ir._package_function_at`). Shadowing is
therefore already decided in the IR; nothing here compares names to decide it.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from .call_legality import BOTH, Ctx, SOLVE_ONLY, TARGET_ONLY


def declared(ctx) -> Dict[str, object]:
    """``{name: function}`` for every package-scope function with a body.

    Keyed by every name a call may use: ast2ir records a function under its
    qualified name and, when unambiguous, its short one.
    """
    imports = {id(f) for f in (getattr(ctx, "import_functions", None) or [])}
    return {name: fn
            for name, fn in (getattr(ctx, "functions", None) or {}).items()
            if id(fn) not in imports and not getattr(fn, "is_import", False)}


def contexts_of(fn) -> FrozenSet[Ctx]:
    """Where ``fn`` may be called from (LRM 22.2.3)."""
    if getattr(fn, "is_target", False):
        return TARGET_ONLY
    if getattr(fn, "is_solve", False):
        return SOLVE_ONLY
    return BOTH


def qualified_name(fn) -> str:
    return (getattr(fn, "metadata", None) or {}).get("qualified_name") or fn.name


def callee(call, pkg: Dict[str, object]):
    """The package function ``call`` calls, or None.

    Decided by the IR form the front end gave the call (see the module
    docstring): a member call `self.f(...)` is never a package function, even
    when a package declares an `f`.
    """
    func = getattr(call, "func", None)
    if type(func).__name__ != "ExprRefUnresolved":
        return None
    return pkg.get(getattr(func, "name", None))


def calls_in(node) -> List[object]:
    """Every ExprCall under ``node``, by dataclass fields (see validate_calls)."""
    out: List[object] = []

    def walk(n):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple, set)):
            for c in n:
                walk(c)
            return
        if isinstance(n, dict):
            for c in n.values():
                walk(c)
            return
        if not dc.is_dataclass(n) or isinstance(n, type):
            return
        if type(n).__name__ == "ExprCall":
            out.append(n)
        for f in dc.fields(n):
            c = getattr(n, f.name, None)
            if c is not n:
                walk(c)

    walk(node)
    return out


def reach(ctx, bodies: Iterable[Tuple[object, Optional[Ctx]]]
          ) -> List[Tuple[object, Ctx]]:
    """The package functions reachable from ``bodies``, with each context.

    ``bodies`` is ``[(function, context)]`` for what the model lowers; a
    ``None`` context is a body that is not lowered and is skipped.

    Returns ``[(function, context)]`` in first-reached order, one pair per
    context a function is reached in. A function whose qualifier does not
    admit the caller's context is still returned in its OWN context: the call
    is the caller's diagnostic (`classify` says WRONG_CONTEXT), and the body
    is checked where it would run.
    """
    pkg = declared(ctx)
    if not pkg:
        return []
    out: List[Tuple[object, Ctx]] = []
    seen = set()
    work: List[Tuple[object, Ctx]] = [(fn, c) for fn, c in bodies
                                      if c is not None]
    while work:
        fn, c = work.pop(0)
        for call in calls_in(getattr(fn, "body", None)):
            target = callee(call, pkg)
            if target is None:
                continue
            ctxs = contexts_of(target)
            run_in = c if c in ctxs else next(iter(ctxs))
            key = (id(target), run_in)
            if key in seen:
                continue
            seen.add(key)
            out.append((target, run_in))
            work.append((target, run_in))
    return out


def unique(pairs: Iterable[Tuple[object, Ctx]]) -> Tuple[object, ...]:
    """The functions of :func:`reach`'s result, each once, in order."""
    seen, out = set(), []
    for fn, _ in pairs:
        if id(fn) not in seen:
            seen.add(id(fn))
            out.append(fn)
    return tuple(out)
