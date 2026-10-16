"""Bit and part selects of an integer: what one selects, read or written.

`x[31:12]` and `x[3]` reach the IR as an `ExprSubscript` -- the node an array
index uses too. Which one it is depends on the TYPE of what is subscripted, so
the decision is made here, once, from `ExprTypes`, and each emitter only
spells the result. Every emitter used to render the subscript as an array
index, which is a C/C++ compile error on a bit select, a `TypeError` in
Python, and -- where the front end also dropped the select -- a silent read
of the whole value.

LRM 8.6.1 / Table 21: a part select `x[hi:lo]` is unsigned and `hi-lo+1` bits
wide, a bit select is one bit; the bounds of a part select are constant. A
write to a select changes those bits only (it is a read-modify-write of the
whole value in a language without native bit ranges).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Optional

from ..diag import Refusal
from .progseq_model import _dt_name

__all__ = ["BitSelect", "bit_select", "const_value"]


def const_value(e) -> Optional[int]:
    """The value of an integer constant expression, or ``None``."""
    cn = _dt_name(e)
    if cn == "ExprConstant":
        v = e.value
        return v if isinstance(v, int) and not isinstance(v, bool) else None
    if cn == "ExprUnary" and e.op.name == "USub":
        v = const_value(e.operand)
        return None if v is None else -v
    if cn == "ExprBin":
        a, b = const_value(e.lhs), const_value(e.rhs)
        if a is None or b is None:
            return None
        op = e.op.name
        if op == "Add":
            return a + b
        if op == "Sub":
            return a - b
        if op == "Mult":
            return a * b
    return None


@dc.dataclass(frozen=True)
class BitSelect:
    """`base[lo + width - 1 : lo]`."""

    #: The integer expression bits are selected from.
    base: Any
    #: The low bit: an IR expression, and its value when it is constant (a
    #: part select's always is; a bit select's index need not be).
    lo: Any
    lo_const: Optional[int]
    #: Bits selected, and the width of what they are selected from.
    width: int
    base_width: int

    @property
    def mask(self) -> int:
        """The selected bits, at bit 0."""
        return (1 << self.width) - 1

    @property
    def hi_const(self) -> Optional[int]:
        return None if self.lo_const is None else self.lo_const + self.width - 1


def bit_select(e, types) -> Optional[BitSelect]:
    """*e* as a select of an integer, or ``None`` if it is not one (an array
    index, or a subscript of something *types* cannot type)."""
    if _dt_name(e) != "ExprSubscript":
        return None
    bt = types.type_of(e.value)
    if bt is None or bt.kind != "int":
        return None
    sl = e.slice
    if _dt_name(sl) == "ExprSlice":
        hi, lo = const_value(sl.upper), const_value(sl.lower)
        if hi is None or lo is None:
            raise Refusal(
                "a part select's bounds must be constant expressions "
                "(LRM 8.6.1)", sl, e)
        if hi < lo or lo < 0 or hi >= bt.width:
            raise Refusal(f"part select [{hi}:{lo}] is outside a "
                          f"{bt.width}-bit value", sl, e)
        return BitSelect(e.value, sl.lower, lo, hi - lo + 1, bt.width)
    lo = const_value(sl)
    if lo is not None and not 0 <= lo < bt.width:
        raise Refusal(f"bit select [{lo}] is outside a {bt.width}-bit "
                      f"value", sl, e)
    return BitSelect(e.value, sl, lo, 1, bt.width)
