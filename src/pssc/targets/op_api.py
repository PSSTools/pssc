"""An operation model's API: what the environment can call.

An operation model has no executors or contexts. Its API follows the
component tree's visibility, not export declarations:

* the root instance is public;
* a sub-component instance is public unless declared `private` or
  `protected` (LRM 17.4 access protection);
* in a public instance, every target function its type has -- its own and its
  bases' -- is on the API unless it is `private` or `protected` (a pssc
  extension: `private target function f();`, or a `private:` group before
  it). Solve functions and the constructor never are.

The environment reaches a sub-component's API through an accessor on its
parent's (`dma0()`, `pit(i)` and `pit_size()`), so the API is per component
TYPE: every public instance of a type shares the type's API. A type is on the
API if some public instance path reaches it and it has public functions or a
public child that leads to some.

`export target function` (LRM 20.4.2) is accepted and changes nothing here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import progseq_model as pm


def public_functions(comp, ctor_names) -> Tuple[Any, ...]:
    """``comp``'s public target functions, in declaration order. ``comp`` is
    the COMPLETED type: an inherited function is present, with its access.
    The private copies `comp_inherit` makes for `super.f()` are not."""
    from .comp_inherit import SUPER_IMPL
    return tuple(fn for fn in (getattr(comp, "functions", None) or [])
                 if pm.exec_kind(fn) is None
                 and SUPER_IMPL not in (getattr(fn, "metadata", None) or {})
                 and pm.func_kind(fn, ctor_names) is pm.FuncKind.EXPORT_OP
                 and pm.is_public(fn))


def type_apis(root, resolve, ctor_names):
    """``[(comp, functions, children)]`` for every component type on the API,
    children before parents; ``children`` are the public `SubComp`s that lead
    to an API, ``dtype`` resolved. The root is always present."""
    import dataclasses as dc
    done: Dict[int, Any] = {}       # id(type) -> entry, or None: no API
    order: List[Tuple[Any, Tuple[Any, ...], Tuple[Any, ...]]] = []
    active = set()

    def visit(comp):
        key = id(comp)
        if key in done:
            return done[key]
        if key in active:               # a cycle: the tree check reports it
            return None
        active.add(key)
        children = []
        for sub in pm.sub_components(comp):
            if sub.access != "public":
                continue
            sdt = resolve(sub.dtype)
            if visit(sdt) is not None:
                children.append(dc.replace(sub, dtype=sdt))
        active.discard(key)
        fns = public_functions(comp, ctor_names)
        entry = ((comp, fns, tuple(children))
                 if fns or children or comp is root else None)
        done[key] = entry
        if entry is not None:
            order.append(entry)
        return entry

    visit(root)
    return order
