"""Target code does not write component fields (LRM 20.7.3 b, 9.1.6;
ruling R13).

"Since component attributes can only be set during the initialization
phase, a function that sets such data attributes shall be called only from
within exec init_down or init_up" (20.7.3 b), and even a `mutable` field
"shall not be modified directly or indirectly from target exec blocks"
(9.1.6). An operation model must be legal PSS, so every op-model target
refuses a function that writes a field of a component -- its own, a
sub-component's, an element of a field -- when target code reaches it: a
`target` function, an exported action's body, or a function either calls.
The constructor, a solve function and `init_down`/`init_up` may write
fields; so may a function only they call.

State that changes at run time belongs in a local, a parameter, or a
channel (each read a `try_get`, each update a `try_put`).

A write is an assignment or compound assignment whose target is rooted at
a component field, or a field passed as `try_get`'s output argument. A
field changed by a callee through a struct parameter passed by handle is
not followed yet.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, Tuple

from ..diag import at
from .progseq_model import _dt_name

__all__ = ["refusals", "target_code", "reached_from"]


def _walk(node):
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _walk(n)
        return
    if not dc.is_dataclass(node) or isinstance(node, type) \
            or _dt_name(node).startswith("DataType"):
        return
    yield node
    for f in dc.fields(node):
        if f.name not in ("loc", "provenance", "doc", "doc_trailing"):
            yield from _walk(getattr(node, f.name, None))


def _params(fn) -> set:
    return {getattr(a, "arg", None)
            for a in getattr(getattr(fn, "args", None), "args", None) or []}


def _field_root(e, fn, comp) -> Optional[str]:
    """The written path, if ``e`` (an assignment target) is rooted at a field
    of a component: ``self.x``, ``self.sub.x``, ``self.w[i]``, ``self.s.f``."""
    path = []
    while _dt_name(e) in ("ExprSubscript", "ExprAttribute"):
        if _dt_name(e) == "ExprAttribute":
            if _dt_name(e.value) == "TypeExprRefSelf":
                if e.attr in _params(fn):
                    return None       # a parameter is spelled `self.p` too
                names = {f.name for f in getattr(comp, "fields", None) or []}
                if e.attr not in names:
                    return None
                return ".".join([e.attr] + path[::-1])
            path.append(e.attr)
        e = e.value
    return None


def _writes(fn, comp) -> List[Tuple[str, object]]:
    """``(path, node)`` for each write: the field written, and the
    statement or call that writes it."""
    out = []
    for n in _walk(getattr(fn, "body", None)):
        cn = _dt_name(n)
        tgts = (n.targets if cn == "StmtAssign" else
                [n.target] if cn in ("StmtAugAssign", "StmtAnnAssign") else [])
        if cn == "ExprCall" and getattr(n.func, "attr", None) == "try_get" \
                and n.args:
            tgts = [n.args[0]]
        for t in tgts:
            p = _field_root(t, fn, comp)
            if p is not None:
                out.append((p, n))
    return out


def _callees(fn, comp, ctx, fns_of) -> List[Tuple[object, object]]:
    """The model's own functions ``fn`` calls: ``(component, function)``."""
    from .expr_types import ExprTypes
    types = None
    out = []
    for n in _walk(getattr(fn, "body", None)):
        if _dt_name(n) != "ExprCall":
            continue
        f = n.func
        if _dt_name(f) == "ExprAttribute":
            if _dt_name(f.value) == "TypeExprRefSelf":
                owner = comp
            else:
                types = types or ExprTypes(fn, comp, ctx)
                t = types.type_of(f.value)
                owner = getattr(t, "dtype", None) if t is not None else None
                owner = types.resolve(owner) if owner is not None else None
            name = f.attr
        elif _dt_name(f) == "ExprRefLocal":
            owner, name = comp, f.name
        else:
            continue
        callee = fns_of(owner).get(name) if owner is not None else None
        if callee is not None:
            out.append((owner, callee))
    return out


def target_code(comps, ctx, entries=(), ctor_names=None):
    """Every function target code runs, with what reaches it: ``(component,
    function, root component, root function)`` per root -- a `target`
    function or an exported action's body -- and per function they call,
    transitively. Solve-context functions are not followed (the call gate
    refuses target code calling them)."""
    from .progseq_model import FuncKind, func_kind

    def fns_of(c) -> Dict[str, object]:
        return {f.name: f for f in getattr(c, "functions", None) or []}

    roots = [(c, f) for c in comps for f in getattr(c, "functions", None) or []
             if getattr(f, "is_target", False)
             and func_kind(f, ctor_names) is FuncKind.EXPORT_OP]
    roots += list(entries)
    for rc, rf in roots:
        stack = [(rc, rf)]
        visited = set()
        while stack:
            c, f = stack.pop()
            if id(f) in visited:
                continue
            visited.add(id(f))
            kind = func_kind(f, ctor_names)
            if kind in (FuncKind.CONSTRUCTOR, FuncKind.EXPORT_SOLVE,
                        FuncKind.REG_OFFSET, FuncKind.EXEC):
                continue        # solve context: the call gate's business
            yield c, f, rc, rf
            stack.extend(_callees(f, c, ctx, fns_of))


def reached_from(f, rc, rf) -> str:
    """", reached from target function 'c::f'" unless ``f`` is the root."""
    return ("" if f is rf else
            f", reached from target function "
            f"'{getattr(rc, 'name', '?')}::{rf.name}'")


def refusals(comps, ctx, entries=(), ctor_names=None) -> List[str]:
    """One message per function that writes a component field and is reached
    from target code, naming the field and the target code it is reached
    from."""
    msgs, seen = [], set()
    for c, f, rc, rf in target_code(comps, ctx, entries, ctor_names):
        for p, node in _writes(f, c):
            key = (id(f), p)
            if key in seen:
                continue
            seen.add(key)
            msgs.append(at((node, f),
                f"{getattr(c, 'name', '?')}::{f.name}: writes component "
                f"field '{p}'{reached_from(f, rc, rf)}; a component's fields "
                f"are set only while it is initialized (LRM 20.7.3 b, 9.1.6; "
                f"ruling R13). Keep run-time state in a local, a parameter or "
                f"a channel"))
    return msgs
