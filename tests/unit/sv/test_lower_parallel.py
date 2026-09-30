"""Tests for parallel block lowering with resource management."""

import pytest
from zuspec.dataclasses import ir

from pssc.targets.sv.context import LoweringContext
from pssc.targets.sv.lower_activities import _lower_activity_stmt
from pssc.targets.sv.lower_head_solve import (
    HeadAction,
    emit_head_action_solve,
    _emit_shuffle_solve,
    _emit_randomize_solve,
)
from pssc.targets.sv.lower_resources import ResourceClaim


@pytest.fixture
def ctx():
    return LoweringContext()


class TestParallelBasic:
    def test_two_branch_fork_join(self, ctx):
        par = ir.ActivityParallel(stmts=[
            ir.ActivityTraversal(handle="a1"),
            ir.ActivityTraversal(handle="a2"),
        ])
        lines = _lower_activity_stmt(ctx, par, "comp")
        text = "\n".join(lines)
        assert "fork" in text
        assert "join" in text
        assert "a1.body();" in text
        assert "a2.body();" in text

    def test_join_none(self, ctx):
        par = ir.ActivityParallel(
            stmts=[ir.ActivityTraversal(handle="a1")],
            join_spec=ir.JoinSpec(kind=ir.JoinKind.NONE),
        )
        lines = _lower_activity_stmt(ctx, par, "comp")
        text = "\n".join(lines)
        assert "join_none" in text

    def test_join_first(self, ctx):
        par = ir.ActivityParallel(
            stmts=[
                ir.ActivityTraversal(handle="a1"),
                ir.ActivityTraversal(handle="a2"),
            ],
            join_spec=ir.JoinSpec(kind=ir.JoinKind.FIRST),
        )
        lines = _lower_activity_stmt(ctx, par, "comp")
        text = "\n".join(lines)
        assert "join_any" in text

    def test_nested_parallel(self, ctx):
        inner = ir.ActivityParallel(stmts=[
            ir.ActivityTraversal(handle="inner1"),
            ir.ActivityTraversal(handle="inner2"),
        ])
        outer = ir.ActivityParallel(stmts=[
            ir.ActivityTraversal(handle="outer1"),
            inner,
        ])
        lines = _lower_activity_stmt(ctx, outer, "comp")
        text = "\n".join(lines)
        assert text.count("fork") == 2
        assert "inner1.body();" in text
        assert "outer1.body();" in text


class TestHeadActionSolve:
    def test_shuffle_solve(self):
        id_vars = ["a1.res_id", "a2.res_id"]
        lines = _emit_shuffle_solve(id_vars, 4)
        text = "\n".join(lines)
        assert "begin" in text
        assert "_pool_idx" in text
        assert "$urandom_range" in text
        assert "a1.res_id = _pool_idx[0];" in text
        assert "a2.res_id = _pool_idx[1];" in text
        assert "end" in text

    def test_randomize_solve(self):
        id_vars = ["a1.res_id", "a2.res_id", "a3.res_id"]
        lines = _emit_randomize_solve(id_vars, 5)
        text = "\n".join(lines)
        assert "std::randomize" in text
        assert "unique" in text
        assert "inside {[0:4]}" in text
        assert "a1.res_id = _rid_0;" in text
        assert "a2.res_id = _rid_1;" in text
        assert "a3.res_id = _rid_2;" in text

    def test_head_action_solve_two_claims_same_pool(self):
        heads = [
            HeadAction(
                branch_index=0, action_var="a1", action_type="T1",
                claims=[ResourceClaim(field_name="r", pool_expr="comp.pool", id_field="r_id")],
            ),
            HeadAction(
                branch_index=1, action_var="a2", action_type="T2",
                claims=[ResourceClaim(field_name="r", pool_expr="comp.pool", id_field="r_id")],
            ),
        ]
        lines = emit_head_action_solve(heads, {"comp.pool": 4})
        text = "\n".join(lines)
        # Should emit shuffle-based solve (2 claims, pool_size 4)
        assert "_pool_idx" in text or "std::randomize" in text

    def test_head_action_solve_no_shared_pool(self):
        heads = [
            HeadAction(
                branch_index=0, action_var="a1", action_type="T1",
                claims=[ResourceClaim(field_name="r1", pool_expr="comp.pool_a", id_field="r1_id")],
            ),
            HeadAction(
                branch_index=1, action_var="a2", action_type="T2",
                claims=[ResourceClaim(field_name="r2", pool_expr="comp.pool_b", id_field="r2_id")],
            ),
        ]
        lines = emit_head_action_solve(heads, {"comp.pool_a": 2, "comp.pool_b": 2})
        # No coordination needed -- different pools
        assert lines == []

    def test_head_action_solve_empty(self):
        assert emit_head_action_solve([], {}) == []

    def test_complex_case_many_claims(self):
        # More than 8 claims triggers randomize solve
        heads = [
            HeadAction(
                branch_index=i, action_var=f"a{i}", action_type=f"T{i}",
                claims=[ResourceClaim(field_name="r", pool_expr="comp.pool", id_field="r_id")],
            )
            for i in range(10)
        ]
        lines = emit_head_action_solve(heads, {"comp.pool": 16})
        text = "\n".join(lines)
        assert "std::randomize" in text
        assert "unique" in text
