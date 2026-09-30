"""Activity AST -> IR: every statement keeps its shape (P0, F3-F9).

Each test here covers a construct ast2ir used to translate into something
smaller than what was written -- an empty `atomic`, a loop with no body, a
lost index, a join kind spelled as a string, a dropped label -- with no
diagnostic. The rule these tests hold ast2ir to: an activity statement is
translated whole, or it is a located error.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core import activity as A
from zuspec.ir.core.expr import ExprAttribute, ExprConstant, TypeExprRefSelf

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _translate(body: str, decls: str = ""):
    src = """\
component pss_top {
    action A { rand bit[4] x; }
    action B { }
    action T {
        rand bit[4] n;
        A arr[3];
        A a1;
        B b1, b2;
        rand bit[4] vals[4];
        %s
        activity {
            %s
        }
    }
}
""" % (decls, body)
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)


def _stmts(body: str, decls: str = ""):
    ctx = _translate(body, decls)
    assert not ctx.errors, ctx.errors
    return ctx.type_map["pss_top::T"].activity_ir.stmts


def _one(body: str, cls, decls: str = ""):
    stmts = _stmts(body, decls)
    assert len(stmts) == 1, stmts
    assert isinstance(stmts[0], cls), type(stmts[0]).__name__
    return stmts[0]


def _errors(body: str, decls: str = ""):
    return _translate(body, decls).errors


def _is_do(stmt, name):
    return isinstance(stmt, A.ActivityAnonTraversal) and stmt.action_type == name


# --- F3: the atomic body ---------------------------------------------------

def test_atomic_body_non_empty():
    s = _one("atomic { do B; do A; }", A.ActivityAtomic)
    assert [x.action_type for x in s.stmts] == ["B", "A"]


# --- F4: single-statement bodies -------------------------------------------

def test_single_statement_repeat_body():
    s = _one("repeat (3) do B;", A.ActivityRepeat)
    assert len(s.body) == 1 and _is_do(s.body[0], "B")


def test_single_statement_dowhile_body():
    s = _one("repeat do B; while (n < 2);", A.ActivityDoWhile)
    assert len(s.body) == 1 and _is_do(s.body[0], "B")


def test_single_statement_replicate_body():
    s = _one("replicate (2) do B;", A.ActivityReplicate)
    assert len(s.body) == 1 and _is_do(s.body[0], "B")


def test_single_statement_foreach_body():
    s = _one("foreach (v: vals) do B;", A.ActivityForeach)
    assert len(s.body) == 1 and _is_do(s.body[0], "B")


def test_single_statement_if_else_bodies():
    s = _one("if (n > 1) do A; else do B;", A.ActivityIfElse)
    assert len(s.if_body) == 1 and _is_do(s.if_body[0], "A")
    assert len(s.else_body) == 1 and _is_do(s.else_body[0], "B")


def test_labeled_body_block_is_kept_as_a_block():
    """A labeled `{...}` body is a named sub-activity (LRM 11.8): flattening
    it into the loop body loses the label."""
    s = _one("repeat (2) L: { do B; }", A.ActivityRepeat)
    assert len(s.body) == 1
    assert isinstance(s.body[0], A.ActivitySequenceBlock)
    assert s.body[0].label == "L"


# --- F5: the foreach collection -------------------------------------------

def test_foreach_collection_present():
    s = _one("foreach (v: vals) do B;", A.ActivityForeach)
    assert isinstance(s.collection, ExprAttribute)
    assert s.collection.attr == "vals"
    assert isinstance(s.collection.value, TypeExprRefSelf)


# --- F6: indexed handles, and initializers --------------------------------

def test_indexed_handle_keeps_index():
    s = _one("arr[1];", A.ActivityTraversal)
    assert s.handle == "arr"
    assert isinstance(s.index, ExprConstant) and s.index.value == 1
    assert s.type_qname == "pss_top::A"


@pytest.mark.parametrize("body", ["do A {.x = 1};", "a1 {.x = 2};"])
def test_initializer_is_a_located_error(body):
    errs = _errors(body)
    assert any("traversal initializers are not supported" in e and "line 12" in e
               for e in errs), errs


# --- F7: join specs --------------------------------------------------------

def test_join_kinds_are_enum():
    first = _one("parallel join_first(1) { do A; do B; }", A.ActivityParallel)
    assert first.join_spec.kind is A.JoinKind.FIRST
    assert isinstance(first.join_spec.count, ExprConstant)
    assert first.join_spec.count.value == 1
    none = _one("parallel join_none { do A; }", A.ActivityParallel)
    assert none.join_spec.kind is A.JoinKind.NONE
    plain = _one("parallel { do A; }", A.ActivityParallel)
    assert plain.join_spec is None


def test_join_branch_labels():
    s = _one("parallel join_branch(P1, P2) { P1: do A; P2: do B; do A; }",
             A.ActivityParallel)
    assert s.join_spec.kind is A.JoinKind.BRANCH
    assert s.join_spec.branch_labels == ["P1", "P2"]


def test_schedule_join_read():
    s = _one("schedule join_select(1) { b1; b2; }", A.ActivitySchedule)
    assert s.join_spec.kind is A.JoinKind.SELECT
    assert s.join_spec.count.value == 1


# --- F8: labels ------------------------------------------------------------

def test_scope_labels_kept():
    s = _one("L1: sequence { do B; }", A.ActivitySequenceBlock)
    assert s.label == "L1"
    p = _one("L2: parallel { do B; }", A.ActivityParallel)
    assert p.label == "L2"
    r = _one("L3: repeat (2) do B;", A.ActivityRepeat)
    assert r.label == "L3"


def test_traversal_label_kept():
    s = _one("L2: do B;", A.ActivityAnonTraversal)
    assert s.label == "L2"


def test_replicate_it_label_kept():
    s = _one("replicate (i: 2) R[]: do B;", A.ActivityReplicate)
    assert s.label == "R"
    assert s.index_var == "i"


# --- F9: scheduling constraints, symbols, and the fall-through -------------

def test_scheduling_constraint_present():
    s = _one("schedule { b1; b2; constraint parallel {b1, b2}; }",
             A.ActivitySchedule)
    sc = [x for x in s.stmts if isinstance(x, A.ActivitySchedulingConstraint)]
    assert len(sc) == 1
    assert sc[0].is_parallel is True
    assert [t.attr for t in sc[0].targets] == ["b1", "b2"]


def test_symbol_call_is_a_located_error():
    errs = _errors("S1();", decls="symbol S1 { do A; }")
    assert any("activity symbols are not supported" in e and "line 12" in e
               for e in errs), errs


def test_block_handle_declaration_is_not_a_statement():
    """A handle declared in a block (LRM 11.8.2, pssparser X-8) is a
    declaration: it neither becomes a statement nor is an error, and a
    traversal of it resolves to its type."""
    s = _one("L1: sequence { B bb; bb; }", A.ActivitySequenceBlock)
    assert len(s.stmts) == 1
    assert isinstance(s.stmts[0], A.ActivityTraversal)
    assert s.stmts[0].type_qname == "pss_top::B"
