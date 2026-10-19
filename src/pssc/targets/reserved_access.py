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
from .group_binding import _path, _resolve
from .progseq_model import (_dt_name, array_element_type, field_is_array,
                            is_reg_group, sub_components)
from .reg_layout import is_reserved

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
    declared; anything else (a channel, a plain field, a name the component
    does not have) ends the walk with ``None``."""
    tm = tm or {}
    by_name = by_name or {}
    dt, in_group = comp, False
    for k, seg in enumerate(path):
        if in_group:
            f = next((x for x in getattr(dt, "fields", None) or []
                      if x.name == seg), None)
            if f is None:
                return None
            if is_reserved(f):
                return tuple(path[:k + 1])
            ft = array_element_type(f) if field_is_array(f) else f.datatype
            if not is_reg_group(ft):
                return None                # a register: the rest is a method
            dt = ft
            continue
        subs = {s.name: s.dtype for s in sub_components(dt)}
        if seg in subs:
            dt = _resolve(subs[seg], tm, by_name)
            continue
        f = next((x for x in getattr(dt, "fields", None) or []
                  if x.name == seg), None)
        if f is None:
            return None
        ft = array_element_type(f) if field_is_array(f) else f.datatype
        if not is_reg_group(ft):
            return None
        dt, in_group = ft, True
    return None


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
        if f.name in ("loc", "metadata"):
            continue
        yield from _calls(getattr(node, f.name), stmt)
