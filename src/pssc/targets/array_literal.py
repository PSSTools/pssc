"""Array initializers (`{1, 2, 3}`), checked once for every op-model target.

An aggregate literal gives an array its value as a field's initializer, a
local's initializer or an assigned value. Its element count must be the
array's (LRM 8.1); a nested list gives an array of arrays its rows. A literal
that does not fit is refused here, naming the field or function, before any
target renders it -- each backend then renders a literal it may assume fits.

`{}` (a type's default) is another construct, left to the emitters, and so is
a list passed to a call.
"""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional

from ..diag import at
from .progseq_model import _dt_name

_DT_ARRAY = "DataTypeArray"


def is_literal(e) -> bool:
    """A non-empty `{...}`: what this module checks and backends render."""
    return _dt_name(e) == "ExprList" and bool(getattr(e, "elts", None))


def array_size(dtype) -> Optional[int]:
    n = getattr(dtype, "size", None)
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError):
        return int(getattr(n, "value", -1)) if hasattr(n, "value") else None


def shape_error(lst, dtype, resolve=lambda d: d) -> Optional[str]:
    """Why ``lst`` cannot be the value of a ``dtype``, or None if it can."""
    dt = resolve(dtype)
    if _dt_name(dt) != _DT_ARRAY:
        return "an aggregate literal given to a value that is not an array"
    n = array_size(dt)
    if n is None or n < 0:
        return "an array initializer for an array of unknown size"
    if len(lst.elts) != n:
        return (f"an array initializer with {len(lst.elts)} element(s), "
                f"for an array of {n}")
    elem = resolve(dt.element_type)
    for e in lst.elts:
        if _dt_name(elem) == _DT_ARRAY:
            if not is_literal(e):
                return ("an element of an array of arrays given other than "
                        "as `{...}` is not supported yet")
            why = shape_error(e, elem, resolve)
            if why:
                return why
        elif _dt_name(e) == "ExprList":
            return ("an aggregate literal given to an element that is not "
                    "an array")
    return None


def literal_errors(comps, ctx) -> List[str]:
    """``"<where>: <why>"`` for each array initializer in ``comps`` that does
    not fit the array it is given to, or that no target can type."""
    from .expr_types import ExprTypes
    out: List[str] = []
    for comp in comps:
        cname = (getattr(comp, "name", None) or "?").split("::")[-1]
        for f in getattr(comp, "fields", None) or []:
            iv = getattr(f, "initial_value", None)
            if is_literal(iv):
                types = ExprTypes(None, comp, ctx)
                why = shape_error(iv, f.datatype, types.resolve)
                if why:
                    out.append(at((iv, f), f"{cname}.{f.name}: {why}"))
        for fn in getattr(comp, "functions", None) or []:
            types = None
            for s in _assignments(getattr(fn, "body", None)):
                if types is None:
                    types = ExprTypes(fn, comp, ctx)
                if _dt_name(s) == "StmtAnnAssign":
                    dtype = s.annotation
                else:
                    t = types.type_of(s.targets[0])
                    dtype = getattr(t, "dtype", None) if t else None
                    if t is not None and t.kind != "array":
                        dtype = None
                why = (shape_error(s.value, dtype, types.resolve)
                       if dtype is not None else
                       "an aggregate literal given to a value that is not "
                       "an array")
                if why:
                    out.append(at((s, fn), f"{cname}::{fn.name}: {why}"))
    return list(dict.fromkeys(out))


def _assignments(node):
    """Each `x = {...}` / `T x = {...}` in a body, at any depth."""
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _assignments(n)
        return
    if not dc.is_dataclass(node) or isinstance(node, type):
        return
    if (_dt_name(node) in ("StmtAnnAssign", "StmtAssign")
            and is_literal(getattr(node, "value", None))):
        yield node
    for f in dc.fields(node):
        if f.name in ("body", "orelse", "cases", "clauses", "stmts", "handlers",
                      "finalbody"):
            yield from _assignments(getattr(node, f.name, None))
