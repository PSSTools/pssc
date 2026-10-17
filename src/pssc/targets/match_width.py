"""A `match` subject meets its choices at a common type (LRM 8.5.9.1, the
rule `in` follows; Table 22), checked once for every op-model target.

`match (x + y) { [4]: ... }` with `bit[2] x, y` compares a 32-bit sum: the
choice is an `int`, so 2 + 2 is 4, not the 0 a 2-bit sum wraps to. `in`
gets this for free -- it lowers to comparisons, whose operands every target
already evaluates at their common width -- but a `match` is rendered as a
`switch`, an `if` chain or a `case` on the subject alone, evaluated at its
own width (W55; SV's `case` sizes its items together and was right).

A cast cannot fix it: a cast's operand is self-determined, so the sum wraps
first. An assignment is context-determined (8.7.2), so `lower` reads a
subject narrower than the common type into a local of that type, and the
`match` tests the local:

    bit[32] _pss_msub0 = x + y;
    match (_pss_msub0) { [4]: ... }

A subject that is no operation (a variable, a call, a select: one value at
any width), or already as wide as its choices, is left as it was. Like
`match_overlap`, this rewrites the IR in place, before the call gate, so no
emitter grows its own spelling.
"""
from __future__ import annotations

import dataclasses as dc
import itertools

import zuspec.ir.core as ir

from .body_walker import match_values
from .expr_types import ExprTypes
from .progseq_model import _dt_name

__all__ = ["lower"]


def lower(ctx) -> None:
    """Widen the subject of every `match` in ``ctx``'s bodies that is
    narrower than its choices."""
    tm = getattr(ctx, "type_map", {}) or {}
    for dtype in {id(d): d for d in tm.values()}.values():
        for fn in list(getattr(dtype, "functions", None) or ()):
            if getattr(fn, "body", None):
                _widen_list(fn.body, ExprTypes(fn, dtype, ctx),
                            itertools.count())


def _widen_list(stmts, types, counter) -> None:
    i = 0
    while i < len(stmts):
        st = stmts[i]
        _widen_nested(st, types, counter)
        if _dt_name(st) == "StmtMatch":
            pre = _widen(st, types, counter)
            if pre is not None:
                stmts.insert(i, pre)
                i += 1
        i += 1


def _widen_nested(st, types, counter) -> None:
    if not dc.is_dataclass(st) or isinstance(st, type):
        return
    for f in dc.fields(st):
        v = getattr(st, f.name, None)
        if isinstance(v, list) and v:
            if _dt_name(v[0]).startswith("Stmt") \
                    and _dt_name(v[0]) != "StmtMatchCase":
                _widen_list(v, types, counter)
            else:
                for x in v:
                    _widen_nested(x, types, counter)
        elif _dt_name(v).startswith("Stmt"):
            _widen_nested(v, types, counter)


def _widen(st, types, counter):
    """The declaration to put before ``st``, or None if its subject is as
    wide as the common type of it and its choices."""
    if _dt_name(st.subject) not in ("ExprBin", "ExprUnary"):
        # A variable, a call, a select or a constant has one value at any
        # width; only an operation (`x + y`, `~x`) wraps at its own.
        return None
    t = types.type_of(st.subject)
    if t is None or t.kind != "int":
        return None
    width, signed = t.width, t.signed
    for c in st.cases:
        for v in match_values(c.pattern) or ():
            for b in ((v.lower, v.upper) if _dt_name(v) == "ExprRange"
                      else (v,)):
                bt = types.type_of(b) if b is not None else None
                if bt is None or bt.kind != "int":
                    continue
                width, signed = max(width, bt.width), signed and bt.signed
    if width <= t.width:
        return None
    name = f"_pss_msub{next(counter)}"
    decl = ir.StmtAnnAssign(
        target=ir.ExprRefLocal(name=name),
        annotation=ir.DataTypeInt(bits=width, signed=signed),
        value=st.subject, loc=getattr(st, "loc", None))
    st.subject = ir.ExprRefLocal(name=name)
    return decl
