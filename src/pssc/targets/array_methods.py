"""`size()` and `sum()` of a fixed-size array (LRM 7.9.2.2), lowered once
for every op-model target.

A fixed-size array's methods mean expressions every target already renders:
`a.size()` is the constant N, and `a.sum()` -- an `int` for an array of
integers (Table 8) -- is `(int)a[0] + ... + (int)a[N-1]`, each addition an
`int` one that wraps at 32 bits as any other (8.7). So `lower` rewrites the
IR in place, before the call gate runs, rather than each emitter growing its
own spelling of both (E15).

`sum()` reads the array once per element, so a receiver containing a call is
refused rather than called N times. Any other method, and either method of a
receiver this pass cannot type, is left to the call gate, which refuses it
by name.

An array compared with an array literal (`v == {1, 2}`, 8.5.3; the literal
stands on the right, 8.4.2) is lowered here too, for the same reason: it
means `v[0] == 1 && v[1] == 2`, which every target renders (E20). A literal
of another length is refused, as `array_literal` refuses one assigned.
"""
from __future__ import annotations

import dataclasses as dc

import zuspec.ir.core as ir

from ..diag import Refusal
from .expr_types import ExprTypes
from .progseq_model import _dt_name

__all__ = ["lower"]

_INT = ir.DataTypeInt(bits=32, signed=True)


def lower(ctx) -> None:
    """Rewrite every `size()`/`sum()` of a fixed-size array in the bodies of
    ``ctx``'s types."""
    tm = getattr(ctx, "type_map", {}) or {}
    for dtype in list(tm.values()):
        for fn in list(getattr(dtype, "functions", None) or ()):
            if getattr(fn, "body", None):
                types = ExprTypes(fn, dtype, ctx)
                _rewrite(fn.body, lambda e: _method(e, types, fn)
                         or _literal_compare(e, types, fn))


def _literal_compare(e, types, fn):
    """`v == {a, b}` / `v != {a, b}` as element comparisons; else None."""
    if _dt_name(e) != "ExprBin" or _dt_name(e.rhs) != "ExprList" \
            or e.op not in (ir.BinOp.Eq, ir.BinOp.NotEq):
        return None
    out = _elements_equal(e.lhs, e.rhs, types, fn, e)
    if out is None:
        return None
    return out if e.op is ir.BinOp.Eq else ir.ExprUnary(op=ir.UnaryOp.Not,
                                                        operand=out)


def _elements_equal(v, lst, types, fn, at):
    """`v[0] == lst[0] && ...`, nested literals compared row by row."""
    t = types.type_of(v)
    dt = types.resolve(getattr(t, "dtype", None)) if t is not None else None
    if t is None or t.kind != "array" or _dt_name(dt) != "DataTypeArray":
        return None
    if len(lst.elts) != dt.size:
        raise Refusal(
            f"in '{getattr(fn, 'name', '?')}': an array of {dt.size} "
            f"element(s) compared with an aggregate literal of "
            f"{len(lst.elts)}", lst, at)
    if any(_dt_name(x) in ("ExprCall", "ExprMemberCall") for x in _nodes(v)):
        raise Refusal(
            f"in '{getattr(fn, 'name', '?')}': an array compared with an "
            f"aggregate literal is read once per element, and its expression "
            f"contains a call; read it into a local first", v, at)
    out = None
    for i, x in enumerate(lst.elts):
        elem = ir.ExprSubscript(value=v, slice=ir.ExprConstant(value=i))
        term = (_elements_equal(elem, x, types, fn, at)
                if _dt_name(x) == "ExprList" else None)
        if term is None:
            term = ir.ExprBin(lhs=elem, op=ir.BinOp.Eq, rhs=x)
        out = term if out is None else ir.ExprBin(lhs=out, op=ir.BinOp.And,
                                                  rhs=term)
    return out if out is not None else ir.ExprConstant(value=True)


def _method(e, types, fn):
    """The expression ``e`` means, if it is one of the two methods; else
    None."""
    if _dt_name(e) != "ExprCall" or e.args:
        return None
    f = e.func
    if _dt_name(f) != "ExprAttribute" or f.attr not in ("size", "sum"):
        return None
    t = types.type_of(f.value)
    dt = types.resolve(getattr(t, "dtype", None)) if t is not None else None
    if t is None or t.kind != "array" or _dt_name(dt) != "DataTypeArray":
        return None
    n = getattr(dt, "size", None)
    if not isinstance(n, int) or n < 0:
        return None
    if f.attr == "size":
        return ir.ExprConstant(value=n)
    et = types.of_datatype(dt.element_type)
    if et is None or et.kind != "int":
        return None
    if any(_dt_name(x) in ("ExprCall", "ExprMemberCall")
           for x in _nodes(f.value)):
        raise ValueError(
            f"in '{getattr(fn, 'name', '?')}': `sum()` reads its array once "
            f"per element, and the array's expression contains a call; read "
            f"it into a local first")
    if n == 0:
        return ir.ExprConstant(value=0)
    out = None
    for i in range(n):
        term = ir.ExprCast(target_type=_INT, value=ir.ExprSubscript(
            value=f.value, slice=ir.ExprConstant(value=i)))
        out = term if out is None else ir.ExprBin(lhs=out, op=ir.BinOp.Add,
                                                  rhs=term)
    return out


def _nodes(e):
    yield e
    if dc.is_dataclass(e) and not type(e).__name__.startswith("DataType"):
        for f in dc.fields(e):
            v = getattr(e, f.name, None)
            for x in (v if isinstance(v, list) else (v,)):
                if dc.is_dataclass(x) and not isinstance(x, type):
                    yield from _nodes(x)


def _rewrite(node, fn) -> None:
    """Replace, bottom-up and in place, each node under ``node`` for which
    ``fn`` gives a replacement."""
    if isinstance(node, list):
        for i, x in enumerate(node):
            _rewrite(x, fn)
            r = fn(x)
            if r is not None:
                node[i] = r
        return
    if not dc.is_dataclass(node) or isinstance(node, type) \
            or type(node).__name__.startswith("DataType"):
        return
    for f in dc.fields(node):
        if f.name in ("loc", "provenance", "doc", "doc_trailing"):
            continue
        v = getattr(node, f.name, None)
        if isinstance(v, list):
            _rewrite(v, fn)
        elif dc.is_dataclass(v) and not isinstance(v, type):
            _rewrite(v, fn)
            r = fn(v)
            if r is not None:
                setattr(node, f.name, r)
