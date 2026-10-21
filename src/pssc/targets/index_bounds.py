"""An out-of-bounds array index is an error (LRM 8.6.2; ruling R12),
decided once for every op-model target.

"In the case of an array or a list, it shall be illegal to access an
out-of-bounds index." C and C++ read or write past the array, SV reads a
default and drops a write, and Python wraps a negative index -- each a
model that runs on and does something else. So `lower` marks every index of
a fixed-size data array that is not KNOWN to be in range, and each target
renders a marked index with a check that reports a run-time error, as an
unmatched `match` does (R2), before the access:

* `index_bound(e)` gives the array's size for a marked `ExprSubscript`, or
  None. A target asks it in its subscript hook.

An index needs no check when it is a constant in range, or the index of a
`foreach` over an array no larger, or of a `repeat (i : K)` with a constant
`K` no larger, whose body does not assign it. Everything else -- an index
computed, read from memory, or guarded by `&&` (the guard is honoured: the
check is part of the access, so it runs only if the access does) -- is
checked.

The index is read twice by the check, so an index containing a call is
refused rather than called twice. An array of components or channels is
not data and is left alone.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, Optional

from ..diag import Refusal
from .array_methods import _nodes
from .body_walker import assigns_local, foreach_parts
from .expr_types import ExprTypes
from .progseq_model import _dt_name
from .ir_walk import NON_CHILD_FIELDS

__all__ = ["lower", "index_bound", "MESSAGE"]

MESSAGE = "array index out of bounds"
_ATTR = "pss_index_bound"


def index_bound(e) -> Optional[int]:
    """The size of the array ``e`` (an `ExprSubscript`) indexes, if its index
    must be checked; else None."""
    return getattr(e, _ATTR, None)


def lower(ctx) -> None:
    """Mark the array indexes in the bodies of ``ctx``'s types that need a
    check."""
    tm = getattr(ctx, "type_map", {}) or {}
    for dtype in list(tm.values()):
        for fn in list(getattr(dtype, "functions", None) or ()):
            if getattr(fn, "body", None):
                types = ExprTypes(fn, dtype, ctx)
                _walk(fn.body, types, {}, getattr(fn, "name", "?"))


def _walk(node, types, bounds: Dict[str, int], where: str) -> None:
    """Visit ``node``; ``bounds`` maps a loop index to the exclusive upper
    bound its loop guarantees."""
    if isinstance(node, list):
        for x in node:
            _walk(x, types, bounds, where)
        return
    if not dc.is_dataclass(node) or isinstance(node, type) \
            or _dt_name(node).startswith("DataType"):
        return
    cn = _dt_name(node)
    inner = bounds
    if cn == "StmtForeach":
        index, _ = foreach_parts(node)
        n = _array_size(node.iter, types)
        if index is not None and n is not None \
                and not assigns_local(node.body, index.name):
            inner = dict(bounds, **{index.name: n})
    elif cn == "StmtFor":
        name = getattr(getattr(node, "target", None), "name", None)
        k = _const(node.iter)
        if name is not None and k is not None \
                and not assigns_local(node.body, name):
            inner = dict(bounds, **{name: k})
    for f in dc.fields(node):
        if f.name in NON_CHILD_FIELDS:
            continue
        v = getattr(node, f.name, None)
        _walk(v, types, inner if f.name == "body" else bounds, where)
    if cn == "ExprSubscript":
        _mark(node, types, bounds, where)


def _const(e) -> Optional[int]:
    if _dt_name(e) == "ExprConstant" and isinstance(e.value, int) \
            and not isinstance(e.value, bool):
        return e.value
    return None


def _array_size(e, types) -> Optional[int]:
    """The size of the fixed-size DATA array ``e`` names, else None."""
    t = types.type_of(e)
    if t is None or t.kind != "array":
        return None
    dt = types.resolve(t.dtype)
    n = getattr(dt, "size", None)
    if not isinstance(n, int) or n < 0:
        return None
    elem = types.of_datatype(getattr(dt, "element_type", None))
    if elem is None or elem.kind == "comp":
        return None
    return n


def _mark(e, types, bounds, where) -> None:
    sl = e.slice
    if _dt_name(sl) in ("ExprSlice", "ExprRange"):
        return
    n = _array_size(e.value, types)
    if n is None:
        return
    k = _const(sl)
    if k is not None and 0 <= k < n:
        return
    if _dt_name(sl) == "ExprRefLocal" and sl.name in bounds \
            and bounds[sl.name] <= n:
        return
    if any(_dt_name(x) in ("ExprCall", "ExprMemberCall") for x in _nodes(sl)):
        raise Refusal(
            f"in '{where}': an array index is checked against the array's "
            f"size before the access (LRM 8.6.2), which reads it twice, and "
            f"this index contains a call; read it into a local first", sl, e)
    object.__setattr__(e, _ATTR, n)
