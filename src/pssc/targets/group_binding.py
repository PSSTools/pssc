"""Which handle each register group of a component is bound to, or that it is not.

A register group's registers are at the group's handle plus their offsets
(LRM 21.14.1), and the handle is whatever `set_handle` gave it. A group that
nothing binds has no address. Before this check, the C and Python backends
answered for it anyway: they bound every group to the constructor's first
address parameter, so a model binding `a` but forgetting `b` wrote `b`'s
registers over `a`'s, and the build exited 0.

One convention survives, because a shipped model states it on purpose: a
component with exactly ONE register group, whose constructor binds no group,
binds that group to the constructor's first address parameter ("the binding is
the generator's job", `src/pssc/testing/models/dma_engine.pss`). It cannot
alias anything: there is no second group for it to land on. Every other group
that no `set_handle` reaches is refused (`check`).

Language-neutral, like `reg_layout`: it says which groups are bound, never how
a backend stores the handle.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Tuple

from ..diag import at
from .progseq_model import (FuncKind, _dt_name, array_element_type,
                            field_is_array, func_kind, is_reg_group,
                            sub_components)

__all__ = ["Binding", "bindings", "check", "group_fields"]


def group_fields(comp) -> List[str]:
    """The register-group fields of *comp*, scalar or array, in order."""
    out = []
    for f in getattr(comp, "fields", None) or []:
        dt = f.datatype
        if field_is_array(f):
            dt = array_element_type(f)
        if is_reg_group(dt):
            out.append(f.name)
    return out


@dc.dataclass(frozen=True)
class Binding:
    """How one component type's register groups get their handles."""

    #: Its register-group fields, in declaration order.
    groups: Tuple[str, ...]
    #: Those a `set_handle` reaches: from the component's own functions
    #: (constructor, init blocks, the private copies of a base's), or from
    #: an enclosing component through an instance path.
    bound: FrozenSet[str]
    #: The one group the convention binds to this constructor parameter (its
    #: IR name), or ``None``.
    implicit: Optional[Tuple[str, str]] = None

    @property
    def unbound(self) -> Tuple[str, ...]:
        done = set(self.bound)
        if self.implicit is not None:
            done.add(self.implicit[0])
        return tuple(g for g in self.groups if g not in done)


def bindings(comps, ctor_names=None, tm=None) -> Dict[int, Binding]:
    """`{id(comp): Binding}` for every component type in *comps*."""
    tm = tm or {}
    bound: Dict[int, set] = {id(c): set() for c in comps}
    by_name = {}
    for c in comps:
        by_name[getattr(c, "name", None)] = c
    for c in comps:
        subs = {s.name: s.dtype for s in sub_components(c)}
        for fn in getattr(c, "functions", None) or []:
            for path in _set_handle_paths(fn):
                if len(path) == 1:
                    bound[id(c)].add(path[0])
                elif len(path) == 2 and path[0] in subs:
                    sub = _resolve(subs[path[0]], tm, by_name)
                    if sub is not None and id(sub) in bound:
                        bound[id(sub)].add(path[1])
    out = {}
    for c in comps:
        groups = tuple(group_fields(c))
        b = frozenset(g for g in bound[id(c)] if g in groups)
        implicit = None
        if len(groups) == 1 and not b:
            arg = _first_addr_arg(_ctor(c, ctor_names), tm)
            if arg is not None:
                implicit = (groups[0], arg)
        out[id(c)] = Binding(groups, b, implicit)
    return out


def check(comps, ctor_names=None, tm=None) -> List[str]:
    """One diagnostic per register group that nothing binds."""
    errs = []
    table = bindings(comps, ctor_names, tm)
    for c in comps:
        name = (getattr(c, "name", None) or "?").split("::")[-1]
        fields = {f.name: f for f in getattr(c, "fields", None) or []}
        for g in table[id(c)].unbound:
            errs.append(at(
                (fields.get(g), c),
                f"{name}: register group `{g}` is never bound: no "
                f"`{g}.set_handle(...)` reaches it, so its registers have no "
                f"address. Bind it in `{name}`'s constructor (or from an "
                f"enclosing component's, as `<instance>.{g}.set_handle(...)`)."))
    return errs


# --- the walk ---------------------------------------------------------------

def _set_handle_paths(fn) -> Iterator[Tuple[str, ...]]:
    """The self-rooted field path of every `<path>.set_handle(...)` in *fn*,
    array indices dropped: `a.set_handle` -> ("a",), `s[i].a.set_handle` ->
    ("s", "a")."""
    for call in _walk(getattr(fn, "body", None)):
        if _dt_name(call) != "ExprCall":
            continue
        func = call.func
        if _dt_name(func) != "ExprAttribute" or func.attr != "set_handle":
            continue
        path = _path(func.value)
        if path:
            yield path


def _path(e) -> Optional[Tuple[str, ...]]:
    cn = _dt_name(e)
    if cn == "TypeExprRefSelf":
        return ()
    if cn == "ExprAttribute":
        b = _path(e.value)
        return None if b is None else b + (e.attr,)
    if cn == "ExprSubscript":
        return _path(e.value)
    if cn == "ExprRefLocal":
        # `self.x` reaches the IR as a name (see AGENTS.md); a parameter of
        # the same name would shadow it, but a parameter is never a group.
        return (e.name,)
    return None


def _walk(node) -> Iterator[Any]:
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _walk(n)
        return
    if not dc.is_dataclass(node) or isinstance(node, type):
        return
    yield node
    for f in dc.fields(node):
        yield from _walk(getattr(node, f.name))


def _ctor(comp, ctor_names):
    for fn in getattr(comp, "functions", None) or []:
        if func_kind(fn, ctor_names) == FuncKind.CONSTRUCTOR:
            return fn
    return None


def _first_addr_arg(ctor, tm) -> Optional[str]:
    """The constructor's first `addr_handle_t` parameter (a chandle typedef;
    older standard libraries declared it as a placeholder struct)."""
    if ctor is None:
        return None
    for a in getattr(getattr(ctor, "args", None), "args", None) or []:
        dt = a.annotation
        ref = getattr(dt, "ref_name", None)
        if ref and ref in tm:
            dt = tm[ref]
        cn = _dt_name(dt)
        if cn == "DataTypeChandle" or (
                cn == "DataTypeStruct"
                and dt.name.split("::")[-1] == "addr_handle_t"):
            return a.arg
    return None


def _resolve(dtype, tm, by_name):
    ref = getattr(dtype, "ref_name", None)
    if ref and ref in tm:
        dtype = tm[ref]
    nm = getattr(dtype, "name", None)
    return by_name.get(nm, dtype)
