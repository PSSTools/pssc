"""Every constraint statement pssparser builds is translated, or is a located
error (P1.0).

``_collect_constraint_stmt`` ended in a debug log, and its `if` and
implication branches kept only expression statements: `soft`, `default`, a
nested `if` and anything under an implication vanished, most often taking the
whole constraint block with them. The scope solve P1 builds is exactly as
right as the constraints that reach it.

A statement with no IR form yet (`soft`, `dist`, `default`) is recorded on its
block, ``metadata["untranslated"]``, with its line: a consumer that SOLVES the
block refuses it (``collect_solve_problem``), and one that only reads types
(the op-model targets, over WB DMA's `default`s) is unaffected.

This test enumerates pssparser's ``ConstraintStmt`` classes BY INTROSPECTION
and requires a row for each. The twins are ``test_activity_registry.py`` and
``test_type_body_registry.py``.
"""
import inspect
import os
import tempfile

import pytest
from pssparser import ast as P

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir import core as ir

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


class Ledger:
    """Recorded on c1 as untranslated *kind*, on line 1 when *located*."""
    def __init__(self, kind, located=True):
        self.kind = kind
        self.located = located


def _translate(body: str):
    src = ("component pss_top { action A { rand bit[4] x; rand bit[4] y; "
           "rand bit[4] z; rand bit[4] a[3]; constraint c1 { %s } } }" % body)
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)


def _c1_fn(ctx):
    fns = [f for f in ctx.type_map["pss_top::A"].functions if f.name == "c1"]
    return fns[0] if fns else None


def _c1(ctx):
    fn = _c1_fn(ctx)
    return fn.body if fn is not None else []


def _shape(stmts):
    """A compact rendering of statement kinds, nested."""
    out = []
    for s in stmts:
        if isinstance(s, ir.StmtIf):
            out.append(("if", _shape(s.body), _shape(s.orelse)))
        elif isinstance(s, ir.StmtForeach):
            out.append(("foreach", _shape(s.body)))
        else:
            out.append(type(s).__name__)
    return out


# class name -> (constraint body, expectation over c1's statements, or Err)
CASES = {
    "ConstraintStmtExpr":        ("x < 3;", lambda b: _shape(b) == ["StmtExpr"]),
    "ConstraintStmtImplication": ("x > 1 -> y == 2;", lambda b: _shape(b) == ["StmtExpr"]),
    "ConstraintStmtIf":          ("if (x > 1) { y == 2; } else { z == 1; }",
                                  lambda b: _shape(b) == [("if", ["StmtExpr"], ["StmtExpr"])]),
    "ConstraintStmtForeach":     ("foreach (a[i]) { a[i] < 3; }",
                                  lambda b: _shape(b) == [("foreach", ["StmtExpr"])]),
    "ConstraintStmtForall":      None,   # needs an action-handle collection; see test_forall*
    "ConstraintStmtUnique":      ("unique { x, y };", lambda b: _shape(b) == ["StmtUnique"]),
    "ConstraintStmtSoft":        ("soft x == 3;", Ledger("soft")),
    # pssparser does not locate a dist statement (pssc-requests-2026-09-30.md P2).
    "ConstraintStmtDist":        ("dist x in [0..3 := 1, 4 := 5];", Ledger("dist", located=False)),
    "ConstraintStmtDefault":     ("default x == 3;", Ledger("default")),
    "ConstraintStmtDefaultDisable": ("default disable x;", Ledger("default disable")),
}

#: Not statements a constraint body holds on its own, and why.
NOT_A_STATEMENT = {
    "ConstraintStmt": "abstract base",
    "ConstraintScope": "a `{...}` group: its statements are translated in place",
    "ConstraintBlock": "a constraint declaration, translated by _translate_constraint_block",
    "GenericConstraintDeclBool": "a generic constraint declaration (test_generic_constraints.py)",
    "ConstraintStmtField": "a forall iterator's synthetic field",
}


def _classes():
    return sorted(n for n, c in vars(P).items()
                  if inspect.isclass(c) and issubclass(c, P.ConstraintStmt))


def test_every_constraint_statement_is_accounted_for():
    missing = [n for n in _classes() if n not in CASES and n not in NOT_A_STATEMENT]
    assert not missing, f"constraint statements with no decision: {missing}"
    stale = [n for n in list(CASES) + list(NOT_A_STATEMENT) if n not in _classes()]
    assert not stale


@pytest.mark.parametrize("name", sorted(n for n, c in CASES.items() if c is not None))
def test_constraint_statement(name):
    body, expect = CASES[name]
    ctx = _translate(body)
    if isinstance(expect, Ledger):
        assert not ctx.errors, ctx.errors
        where = "line 1: " if expect.located else ""
        assert _c1_fn(ctx).metadata["untranslated"] == [(expect.kind, where)]
    else:
        assert not ctx.errors, ctx.errors
        assert expect(_c1(ctx)), _shape(_c1(ctx))


# -- nesting: what used to vanish ------------------------------------------

@pytest.mark.parametrize("body,shape", [
    ("if (x > 1) { if (y > 1) { z == 1; } }",
     [("if", [("if", ["StmtExpr"], [])], [])]),
    ("if (x > 1) { } else { z == 2; }",
     [("if", ["StmtExpr"], [])]),
    ("x > 1 -> { if (y > 1) { z == 1; } }",
     [("if", [("if", ["StmtExpr"], [])], [])]),
    ("x > 1 -> { y == 1; z == 2; }",
     ["StmtExpr", "StmtExpr"]),
    ("if (x > 1) { foreach (a[i]) { a[i] < 3; } }",
     [("if", [("foreach", ["StmtExpr"])], [])]),
])
def test_nested_constraint_bodies_survive(body, shape):
    ctx = _translate(body)
    assert not ctx.errors, ctx.errors
    assert _shape(_c1(ctx)) == shape


def test_an_empty_true_branch_keeps_its_else_under_the_negated_condition():
    ctx = _translate("if (x > 1) { } else { z == 2; }")
    stmt = _c1(ctx)[0]
    assert isinstance(stmt.test, ir.ExprUnary) and stmt.test.op is ir.UnaryOp.Not


@pytest.mark.parametrize("body,kind", [
    ("x > 1 -> { soft z == 1; }", "soft"),
    ("foreach (a[i]) { soft a[i] == 1; }", "soft"),
    ("if (x > 1) { default z == 1; }", "default"),
])
def test_an_untranslated_statement_is_recorded_at_any_depth(body, kind):
    ctx = _translate(body)
    assert not ctx.errors, ctx.errors
    assert [k for k, _ in _c1_fn(ctx).metadata["untranslated"]] == [kind]


def test_a_block_holding_only_an_untranslated_statement_is_kept():
    """It used to vanish: an empty body made no function at all."""
    ctx = _translate("soft x == 3;")
    assert _c1_fn(ctx) is not None and _c1(ctx) == []


def test_bc_refuses_to_solve_a_block_with_an_untranslated_statement():
    from zuspec.ir.core.xf import PSSToScenarioPass
    from zuspec.ir.core.xf.validate import UnsupportedConstructError
    ctx = _translate("x < 10; soft x == 3;")
    with pytest.raises(UnsupportedConstructError,
                       match=r"line 1: 'soft' constraint in 'c1'"):
        PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)


def test_outside_a_constraint_block_it_is_an_error():
    """A `with` holds a list of expressions; there is no block to carry it."""
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, b"component pss_top { action B { rand bit[4] x; } "
                     b"action A { B b1; activity { b1 with { soft x == 1; }; } } }")
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        ctx = AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)
    assert any("'soft' constraints are not supported yet" in e
               for e in ctx.errors), ctx.errors
