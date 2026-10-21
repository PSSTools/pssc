"""An access through a reserved register-group member is the model's error.

A reserved member (`reg_layout.is_reserved`: `_reserved0`, `_rsvd`) holds
address space open and is never surfaced: no backend gives it an accessor, a
class member or a manifest entry. A body that reaches one anyway used to be
lowered by whatever each backend's fallback happened to do -- C guessed an
untyped access and dropped a value struct's `.raw`, SV named a class member
that does not exist and failed only at elaboration. Refusing it here, in the
shared front (`OpModelTarget.validate`), makes every target refuse it the
same way and before any file is written.

Language-neutral, like `group_binding`: it reads the PSS tree and the bodies,
never a backend's spelling.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Iterator, List, Optional, Tuple

from ..diag import at
from .group_binding import _path
from .progseq_model import _dt_name
from .ir_walk import NON_CHILD_FIELDS

__all__ = ["check", "reserved_prefix"]


def check(bodies, comps, tm=None) -> List[str]:
    """One located diagnostic per register access, in ``bodies`` (``[(comp,
    function, ...)]``, `validate_calls.rendered_bodies`), whose path crosses
    a reserved member of a register group."""
    tm = tm or {}
    by_name = {getattr(c, "name", None): c for c in comps}
    errs: List[str] = []
    for body in bodies:
        comp, fn = body[0], body[1]
        if comp is None:
            continue                       # a package function has no registers
        params = {a.arg for a in getattr(getattr(fn, "args", None), "args",
                                         None) or []}
        cname = (getattr(comp, "name", None) or "?").split("::")[-1]
        for call, stmt in _calls(getattr(fn, "body", None), None):
            func = call.func
            if _dt_name(func) != "ExprAttribute":
                continue
            path = _path(func.value)
            if not path or path[0] in params:
                continue
            hit = reserved_prefix(comp, path, tm, by_name)
            if hit is None:
                continue
            errs.append(at(
                (call, func, stmt, fn),
                f"{cname}::{fn.name}: `{'.'.join(path)}.{func.attr}(...)` "
                f"accesses `{'.'.join(hit)}`, a reserved register-group "
                f"member. A member named `_` followed by a non-digit is a "
                f"placeholder that holds address space and has no access; "
                f"rename it (an underscore before a digit, `_0`, is an "
                f"ordinary member)"))
    return errs


def reserved_prefix(comp, path: Tuple[str, ...], tm=None,
                    by_name=None) -> Optional[Tuple[str, ...]]:
    """The prefix of the self-rooted ``path`` (indices dropped) that ends at a
    reserved register-group member, or ``None`` if it crosses none.

    The path is followed through sub-components and register groups as
    declared (`state_uses.resolve_reg_path`, the one walk that says what a
    path names); anything else ends the walk with ``None``."""
    from .state_uses import resolve_reg_path
    ref = resolve_reg_path(comp, path, tm, by_name)
    return ref.reserved if ref is not None else None


def _calls(node, stmt) -> Iterator[Tuple[Any, Any]]:
    """Every `ExprCall` under ``node``, with the innermost statement holding
    it (where a refusal points when the call has no location of its own)."""
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _calls(n, stmt)
        return
    if not dc.is_dataclass(node) or isinstance(node, type):
        return
    if _dt_name(node).startswith("Stmt"):
        stmt = node
    if _dt_name(node) == "ExprCall":
        yield node, stmt
    for f in dc.fields(node):
        if f.name in NON_CHILD_FIELDS or f.name == "metadata":
            continue
        yield from _calls(getattr(node, f.name), stmt)
