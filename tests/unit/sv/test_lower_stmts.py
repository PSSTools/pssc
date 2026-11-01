"""Tests for statement lowering to SV statement strings."""

import pytest
from zuspec.dataclasses import ir

from pssc.targets.sv.context import LoweringContext
from pssc.targets.sv.lower_stmts import lower_stmt, lower_stmts


@pytest.fixture
def ctx():
    return LoweringContext()


class TestSimpleStatements:
    def test_expr_stmt(self, ctx):
        stmt = ir.StmtExpr(expr=ir.ExprCall(
            func=ir.ExprRefLocal(name="do_work"),
            args=[],
        ))
        lines = lower_stmt(ctx, stmt)
        assert lines == ["do_work();"]

    def test_assign(self, ctx):
        stmt = ir.StmtAssign(
            targets=[ir.ExprRefLocal(name="x")],
            value=ir.ExprConstant(value=42),
        )
        lines = lower_stmt(ctx, stmt)
        assert lines == ["x = 42;"]

    def test_aug_assign_add(self, ctx):
        stmt = ir.StmtAugAssign(
            target=ir.ExprRefLocal(name="count"),
            op=ir.AugOp.Add,
            value=ir.ExprConstant(value=1),
        )
        lines = lower_stmt(ctx, stmt)
        assert lines == ["count += 1;"]

    def test_return_value(self, ctx):
        stmt = ir.StmtReturn(value=ir.ExprConstant(value=0))
        lines = lower_stmt(ctx, stmt)
        assert lines == ["return 0;"]

    def test_return_void(self, ctx):
        stmt = ir.StmtReturn()
        lines = lower_stmt(ctx, stmt)
        assert lines == ["return;"]

    def test_break(self, ctx):
        assert lower_stmt(ctx, ir.StmtBreak()) == ["break;"]

    def test_continue(self, ctx):
        assert lower_stmt(ctx, ir.StmtContinue()) == ["continue;"]

    def test_pass(self, ctx):
        assert lower_stmt(ctx, ir.StmtPass()) == []

    def test_assert(self, ctx):
        stmt = ir.StmtAssert(test=ir.ExprRefLocal(name="valid"))
        lines = lower_stmt(ctx, stmt)
        assert "assert (valid);" in lines[0]

    def test_assert_with_msg(self, ctx):
        stmt = ir.StmtAssert(
            test=ir.ExprRefLocal(name="valid"),
            msg=ir.ExprConstant(value="check failed"),
        )
        lines = lower_stmt(ctx, stmt)
        assert "$error" in lines[0]

    def test_raise(self, ctx):
        stmt = ir.StmtRaise(exc=ir.ExprConstant(value="fatal error"))
        lines = lower_stmt(ctx, stmt)
        assert "$fatal" in lines[0]


class TestControlFlow:
    def test_if(self, ctx):
        stmt = ir.StmtIf(
            test=ir.ExprRefLocal(name="cond"),
            body=[ir.StmtExpr(expr=ir.ExprCall(func=ir.ExprRefLocal(name="a"), args=[]))],
        )
        lines = lower_stmt(ctx, stmt)
        assert lines[0] == "if (cond) begin"
        assert "  a();" in lines
        assert "end" in lines

    def test_if_else(self, ctx):
        stmt = ir.StmtIf(
            test=ir.ExprRefLocal(name="cond"),
            body=[ir.StmtExpr(expr=ir.ExprRefLocal(name="x"))],
            orelse=[ir.StmtExpr(expr=ir.ExprRefLocal(name="y"))],
        )
        lines = lower_stmt(ctx, stmt)
        assert "if (cond) begin" in lines[0]
        assert "end else begin" in lines

    def test_while(self, ctx):
        stmt = ir.StmtWhile(
            test=ir.ExprRefLocal(name="running"),
            body=[ir.StmtExpr(expr=ir.ExprCall(func=ir.ExprRefLocal(name="tick"), args=[]))],
        )
        lines = lower_stmt(ctx, stmt)
        assert lines[0] == "while (running) begin"
        assert lines[-1] == "end"

    def test_foreach(self, ctx):
        stmt = ir.StmtForeach(
            target=ir.ExprRefLocal(name="item"),
            iter=ir.ExprRefLocal(name="data"),
            body=[ir.StmtExpr(expr=ir.ExprRefLocal(name="item"))],
        )
        lines = lower_stmt(ctx, stmt)
        assert lines[0] == "foreach (data[item]) begin"

    def test_repeat(self, ctx):
        stmt = ir.StmtRepeat(
            count=ir.ExprConstant(value=10),
            body=[ir.StmtExpr(expr=ir.ExprCall(func=ir.ExprRefLocal(name="step"), args=[]))],
        )
        lines = lower_stmt(ctx, stmt)
        assert lines[0] == "repeat (10) begin"

    def test_do_while(self, ctx):
        stmt = ir.StmtRepeatWhile(
            condition=ir.ExprRefLocal(name="active"),
            body=[ir.StmtExpr(expr=ir.ExprCall(func=ir.ExprRefLocal(name="poll"), args=[]))],
        )
        lines = lower_stmt(ctx, stmt)
        assert lines[0] == "do begin"
        assert "end while (active);" in lines[-1]


class TestMatch:
    def test_simple_match(self, ctx):
        stmt = ir.StmtMatch(
            subject=ir.ExprRefLocal(name="cmd"),
            cases=[
                ir.StmtMatchCase(
                    pattern=ir.PatternValue(value=ir.ExprConstant(value=0)),
                    body=[ir.StmtExpr(expr=ir.ExprCall(func=ir.ExprRefLocal(name="read"), args=[]))],
                ),
                ir.StmtMatchCase(
                    pattern=ir.PatternValue(value=ir.ExprConstant(value=1)),
                    body=[ir.StmtExpr(expr=ir.ExprCall(func=ir.ExprRefLocal(name="write"), args=[]))],
                ),
            ],
        )
        lines = lower_stmt(ctx, stmt)
        assert lines[0] == "case (cmd)"
        assert "endcase" in lines
        assert any("0: begin" in l for l in lines)
        assert any("1: begin" in l for l in lines)

    def test_default_case(self, ctx):
        stmt = ir.StmtMatch(
            subject=ir.ExprRefLocal(name="x"),
            cases=[
                ir.StmtMatchCase(
                    pattern=ir.PatternAs(pattern=None, name=None),
                    body=[ir.StmtExpr(expr=ir.ExprRefLocal(name="fallback"))],
                ),
            ],
        )
        lines = lower_stmt(ctx, stmt)
        assert any("default: begin" in l for l in lines)


class TestMultipleStmts:
    def test_lower_stmts(self, ctx):
        stmts = [
            ir.StmtAssign(targets=[ir.ExprRefLocal(name="x")], value=ir.ExprConstant(value=0)),
            ir.StmtAssign(targets=[ir.ExprRefLocal(name="y")], value=ir.ExprConstant(value=1)),
        ]
        lines = lower_stmts(ctx, stmts)
        assert len(lines) == 2
        assert "x = 0;" in lines[0]
        assert "y = 1;" in lines[1]


def _reg_read_assign(attr):
    reg = ir.ExprAttribute(
        value=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="regs"),
        attr="r0")
    return ir.StmtAssign(
        targets=[ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="v")],
        value=ir.ExprCall(func=ir.ExprAttribute(value=reg, attr=attr), args=[]))


@pytest.mark.parametrize("attr", ["read", "read_val"])
def test_reg_read_lowers_to_task_output_arg(attr):
    """With a register model, `v = regs.r0.read_val();` becomes a task call
    whose output argument is the assignment target (reg_c reads are tasks)."""
    ctx = LoweringContext()
    ctx.uses_reg_model = True
    lines = lower_stmt(ctx, _reg_read_assign(attr))
    assert lines == [f"regs.r0.{attr}(v);"]


def test_read_without_reg_model_stays_assignment():
    ctx = LoweringContext()
    lines = lower_stmt(ctx, _reg_read_assign("read_val"))
    assert " = " in lines[0]
