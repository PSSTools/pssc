"""What an IR operator is, asked of the operator enums rather than spelled
as a table of names.

``ExprBin`` carries a ``BinOp``; ``ExprBool`` a ``BoolOp``; ``ExprCompare``
a ``CmpOp``; ``StmtAugAssign`` an ``AugOp``. The predicates here take any of
them, so a walker asks ``is_comparison(e.op)`` instead of keeping its own
``('Eq', 'NotEq', ...)``.

Short-circuit form. The front end writes ``a && b`` and ``a || b`` as
``ExprBin(op=BinOp.And | BinOp.Or)``: that is the canonical form in pssc's
IR. ``ExprBool`` (n-ary, ``BoolOp``) can still reach a pass from another
producer or from a pass of its own, so a walker that needs the operands
asks :func:`short_circuit`, which reads either form, and handles one shape.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import zuspec.ir.core as ir

__all__ = ["is_short_circuit", "is_bitwise", "is_comparison", "swapped",
           "short_circuit"]

_SHORT = frozenset({ir.BinOp.And, ir.BinOp.Or, ir.BoolOp.And, ir.BoolOp.Or})

_BITWISE = frozenset({ir.BinOp.BitAnd, ir.BinOp.BitOr, ir.BinOp.BitXor,
                      ir.AugOp.BitAnd, ir.AugOp.BitOr, ir.AugOp.BitXor})

_SWAP = {}
for _E in (ir.BinOp, ir.CmpOp):
    _SWAP.update({_E.Eq: _E.Eq, _E.NotEq: _E.NotEq,
                  _E.Lt: _E.Gt, _E.Gt: _E.Lt, _E.LtE: _E.GtE, _E.GtE: _E.LtE})

_COMPARISON = frozenset(_SWAP) | {ir.CmpOp.Is, ir.CmpOp.IsNot, ir.CmpOp.In,
                                  ir.CmpOp.NotIn}

_AS_BOOL = {ir.BinOp.And: ir.BoolOp.And, ir.BinOp.Or: ir.BoolOp.Or}


def is_short_circuit(op: Any) -> bool:
    """``&&`` or ``||``: ``BinOp.And``/``Or`` or ``BoolOp.And``/``Or``."""
    return op in _SHORT


def is_bitwise(op: Any) -> bool:
    """``&``, ``|`` or ``^``, as a ``BinOp`` or an ``AugOp`` (not shifts)."""
    return op in _BITWISE


def is_comparison(op: Any) -> bool:
    """``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=`` (``BinOp`` or ``CmpOp``),
    and ``CmpOp``'s ``is``/``in`` forms."""
    return op in _COMPARISON


def swapped(op: Any) -> Any:
    """The comparison that gives the same result with its operands swapped
    (``a < b`` is ``b > a``); ``==`` and ``!=`` are their own. Raises
    ``ValueError`` for an operator with no swapped form (``in``, ``is``)."""
    try:
        return _SWAP[op]
    except (KeyError, TypeError):
        raise ValueError("%r has no swapped form" % (op,)) from None


def short_circuit(e: Any) -> Optional[Tuple[Any, List[Any]]]:
    """``(BoolOp, operands)`` for a ``&&`` / ``||`` in either form
    (``ExprBin`` gives two operands, ``ExprBool`` its ``values``); ``None``
    for any other node. Nested ones are not flattened."""
    if isinstance(e, ir.ExprBin) and e.op in _AS_BOOL:
        return _AS_BOOL[e.op], [e.lhs, e.rhs]
    if isinstance(e, ir.ExprBool):
        return e.op, list(e.values)
    return None
