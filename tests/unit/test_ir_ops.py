"""ZIR-3 (pssc-local): operator predicates over the IR operator enums, and
one reader for both short-circuit forms."""
import pytest

import zuspec.ir.core as ir
from pssc.targets import ir_ops


def _r(n):
    return ir.ExprRefLocal(name=n)


@pytest.mark.parametrize("op", [ir.BinOp.And, ir.BinOp.Or,
                                ir.BoolOp.And, ir.BoolOp.Or])
def test_short_circuit_operators(op):
    assert ir_ops.is_short_circuit(op)
    assert not ir_ops.is_bitwise(op) and not ir_ops.is_comparison(op)


def test_bitwise_is_and_or_xor_in_both_enums_not_shifts():
    for op in (ir.BinOp.BitAnd, ir.BinOp.BitOr, ir.BinOp.BitXor,
               ir.AugOp.BitAnd, ir.AugOp.BitOr, ir.AugOp.BitXor):
        assert ir_ops.is_bitwise(op)
    for op in (ir.BinOp.LShift, ir.BinOp.And, ir.AugOp.Add):
        assert not ir_ops.is_bitwise(op)


def test_comparisons_and_their_swaps():
    for E in (ir.BinOp, ir.CmpOp):
        assert ir_ops.swapped(E.Lt) is E.Gt and ir_ops.swapped(E.GtE) is E.LtE
        assert ir_ops.swapped(E.Eq) is E.Eq
        for op in (E.Eq, E.NotEq, E.Lt, E.LtE, E.Gt, E.GtE):
            assert ir_ops.is_comparison(op)
            assert ir_ops.swapped(ir_ops.swapped(op)) is op
    assert ir_ops.is_comparison(ir.CmpOp.In)
    assert not ir_ops.is_comparison(ir.BinOp.Add)
    with pytest.raises(ValueError, match="no swapped form"):
        ir_ops.swapped(ir.CmpOp.In)
    with pytest.raises(ValueError):
        ir_ops.swapped(ir.BinOp.Add)


def test_short_circuit_reads_either_form():
    a, b, c = _r("a"), _r("b"), _r("c")
    op, vals = ir_ops.short_circuit(ir.ExprBin(lhs=a, op=ir.BinOp.Or, rhs=b))
    assert op is ir.BoolOp.Or and vals == [a, b]
    op, vals = ir_ops.short_circuit(
        ir.ExprBool(op=ir.BoolOp.And, values=[a, b, c]))
    assert op is ir.BoolOp.And and vals == [a, b, c]
    assert ir_ops.short_circuit(
        ir.ExprBin(lhs=a, op=ir.BinOp.BitAnd, rhs=b)) is None
    assert ir_ops.short_circuit(a) is None
