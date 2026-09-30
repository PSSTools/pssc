"""Tests for activity lowering to SV task body lines."""

import pytest
from zuspec.dataclasses import ir

from pssc.targets.sv.context import LoweringContext
from pssc.targets.sv.lower_activities import lower_activity, _lower_activity_stmt


@pytest.fixture
def ctx():
    return LoweringContext()


class TestTraversal:
    def test_named_traversal(self, ctx):
        trav = ir.ActivityTraversal(handle="a1")
        lines = _lower_activity_stmt(ctx, trav, "comp")
        text = "\n".join(lines)
        assert "begin" in text
        assert "a1.comp = comp;" in text
        assert "a1.pre_solve();" in text
        assert "a1.randomize()" in text
        assert '$fatal(1, "randomize failed: a1");' in text
        assert "a1.post_solve();" in text
        assert "a1.body();" in text
        assert "end" in text

    def test_traversal_with_inline_constraints(self, ctx):
        trav = ir.ActivityTraversal(
            handle="a1",
            inline_constraints=[
                ir.ExprCompare(
                    left=ir.ExprRefLocal(name="addr"),
                    ops=[ir.CmpOp.GtE],
                    comparators=[ir.ExprConstant(value=0x1000)],
                ),
            ],
        )
        lines = _lower_activity_stmt(ctx, trav, "comp")
        text = "\n".join(lines)
        assert "randomize() with" in text
        assert "addr" in text
        assert ">=" in text

    def test_anon_traversal(self, ctx):
        trav = ir.ActivityAnonTraversal(action_type="WriteAction")
        lines = _lower_activity_stmt(ctx, trav, "comp")
        text = "\n".join(lines)
        assert "WriteAction" in text
        assert "new()" in text
        assert ".comp = comp;" in text
        assert ".randomize()" in text
        assert ".body();" in text

    def test_anon_traversal_with_label(self, ctx):
        trav = ir.ActivityAnonTraversal(action_type="ReadAction", label="rd")
        lines = _lower_activity_stmt(ctx, trav, "comp")
        text = "\n".join(lines)
        assert "ReadAction rd = new();" in text
        assert "rd.comp = comp;" in text

    def test_anon_traversal_with_inline_constraints(self, ctx):
        trav = ir.ActivityAnonTraversal(
            action_type="Transfer",
            inline_constraints=[ir.ExprRefLocal(name="size_ok")],
        )
        lines = _lower_activity_stmt(ctx, trav, "comp")
        text = "\n".join(lines)
        assert "randomize() with" in text


class TestSequential:
    def test_sequential_two_actions(self, ctx):
        activity = ir.ActivitySequenceBlock(stmts=[
            ir.ActivityTraversal(handle="a1"),
            ir.ActivityTraversal(handle="a2"),
        ])
        lines = lower_activity(ctx, activity)
        text = "\n".join(lines)
        assert "a1.body();" in text
        assert "a2.body();" in text
        # a1 should appear before a2
        assert text.index("a1.body()") < text.index("a2.body()")


class TestLoops:
    def test_repeat(self, ctx):
        stmt = ir.ActivityRepeat(
            count=ir.ExprConstant(value=5),
            body=[ir.ActivityTraversal(handle="a1")],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "repeat (5) begin" in text
        assert "a1.body();" in text
        assert "end" in text

    def test_repeat_with_index(self, ctx):
        stmt = ir.ActivityRepeat(
            count=ir.ExprConstant(value=10),
            index_var="i",
            body=[ir.ActivityTraversal(handle="a1")],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "for (int i = 0; i < 10; i++) begin" in text

    def test_do_while(self, ctx):
        stmt = ir.ActivityDoWhile(
            condition=ir.ExprRefLocal(name="active"),
            body=[ir.ActivityTraversal(handle="a1")],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "do begin" in text
        assert "end while (active);" in text

    def test_while_do(self, ctx):
        stmt = ir.ActivityWhileDo(
            condition=ir.ExprRefLocal(name="running"),
            body=[ir.ActivityTraversal(handle="a1")],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "while (running) begin" in text

    def test_foreach(self, ctx):
        stmt = ir.ActivityForeach(
            iterator="item",
            collection=ir.ExprRefLocal(name="data"),
            body=[ir.ActivityTraversal(handle="a1")],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "foreach (data[item]) begin" in text


class TestConditional:
    def test_if_else(self, ctx):
        stmt = ir.ActivityIfElse(
            condition=ir.ExprRefLocal(name="mode"),
            if_body=[ir.ActivityTraversal(handle="a1")],
            else_body=[ir.ActivityTraversal(handle="a2")],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "if (mode) begin" in text
        assert "end else begin" in text
        assert "a1.body();" in text
        assert "a2.body();" in text

    def test_if_no_else(self, ctx):
        stmt = ir.ActivityIfElse(
            condition=ir.ExprRefLocal(name="flag"),
            if_body=[ir.ActivityTraversal(handle="a1")],
            else_body=[],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "if (flag) begin" in text
        assert "else" not in text

    def test_match(self, ctx):
        stmt = ir.ActivityMatch(
            subject=ir.ExprRefLocal(name="cmd"),
            cases=[
                ir.MatchCase(
                    pattern=ir.ExprConstant(value=0),
                    body=[ir.ActivityTraversal(handle="rd")],
                ),
                ir.MatchCase(
                    pattern=ir.ExprConstant(value=1),
                    body=[ir.ActivityTraversal(handle="wr")],
                ),
            ],
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "case (cmd)" in text
        assert "endcase" in text
        assert "rd.body();" in text
        assert "wr.body();" in text


class TestSelect:
    def test_select_uniform(self, ctx):
        stmt = ir.ActivitySelect(branches=[
            ir.SelectBranch(body=[ir.ActivityTraversal(handle="a1")]),
            ir.SelectBranch(body=[ir.ActivityTraversal(handle="a2")]),
        ])
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "$urandom_range" in text
        assert "_zsp_sel" in text  # select uses named block
        assert "a1.body();" in text
        assert "a2.body();" in text

    def test_select_with_weights(self, ctx):
        stmt = ir.ActivitySelect(branches=[
            ir.SelectBranch(weight=ir.ExprConstant(value=3),
                           body=[ir.ActivityTraversal(handle="a1")]),
            ir.SelectBranch(weight=ir.ExprConstant(value=1),
                           body=[ir.ActivityTraversal(handle="a2")]),
        ])
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        # Weights 3/1 → total=4; case $urandom_range(0, 3) with [0:2] for branch 0
        assert "$urandom_range(0, 3)" in text
        assert "_zsp_sel" in text  # named block

    def test_select_with_guard(self, ctx):
        stmt = ir.ActivitySelect(branches=[
            ir.SelectBranch(
                guard=ir.ExprRefLocal(name="enabled"),
                body=[ir.ActivityTraversal(handle="a1")],
            ),
        ])
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "if (enabled)" in text


class TestAtomic:
    def test_atomic_block(self, ctx):
        stmt = ir.ActivityAtomic(stmts=[
            ir.ActivityTraversal(handle="a1"),
            ir.ActivityTraversal(handle="a2"),
        ])
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "comp.atomic_sem.get(1);" in text
        assert "comp.atomic_sem.put(1);" in text
        assert "a1.body();" in text
        assert "a2.body();" in text


class TestParallel:
    def test_fork_join(self, ctx):
        stmt = ir.ActivityParallel(stmts=[
            ir.ActivityTraversal(handle="a1"),
            ir.ActivityTraversal(handle="a2"),
        ])
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "fork" in text
        assert "join" in text
        assert "a1.body();" in text
        assert "a2.body();" in text

    def test_fork_join_none(self, ctx):
        stmt = ir.ActivityParallel(
            stmts=[ir.ActivityTraversal(handle="a1")],
            join_spec=ir.JoinSpec(kind=ir.JoinKind.NONE),
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "fork" in text
        assert "join_none" in text

    def test_fork_join_any(self, ctx):
        stmt = ir.ActivityParallel(
            stmts=[ir.ActivityTraversal(handle="a1"), ir.ActivityTraversal(handle="a2")],
            join_spec=ir.JoinSpec(kind=ir.JoinKind.FIRST),
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "join_any" in text


class TestMisc:
    def test_super(self, ctx):
        stmt = ir.ActivitySuper()
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        assert lines == ["super.activity();"]

    def test_nested_sequence(self, ctx):
        stmt = ir.ActivitySequenceBlock(stmts=[
            ir.ActivityTraversal(handle="a1"),
            ir.ActivitySequenceBlock(stmts=[
                ir.ActivityTraversal(handle="a2"),
                ir.ActivityTraversal(handle="a3"),
            ]),
        ])
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "a1.body();" in text
        assert "a2.body();" in text
        assert "a3.body();" in text

    def test_bind(self, ctx):
        stmt = ir.ActivityBind(
            src=ir.ExprRefLocal(name="producer"),
            dst=ir.ExprRefLocal(name="consumer"),
        )
        lines = _lower_activity_stmt(ctx, stmt, "comp")
        text = "\n".join(lines)
        assert "bind" in text

    def test_empty_activity(self, ctx):
        activity = ir.ActivitySequenceBlock(stmts=[])
        lines = lower_activity(ctx, activity)
        assert lines == []
