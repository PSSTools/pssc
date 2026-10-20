"""Every IR kind is rendered by each op-model target, or refused by its gate
before emission -- plan P5.1 (§4.4 of
docs/design/construct-test-audit/00-plan.md).

An emitter meeting a kind it has no rendering for raised an internal error
from the middle of emission ("defines no expr_list()"). The gate now
pre-screens every rendered body (`targets/body_kinds.py`) against the kinds
the target's body emitter renders, and refuses the rest with a location.
This test holds that account to the IR: every ``Stmt*``/``Expr*``/
``TypeExpr*`` class of ``zuspec.ir.core``, found BY INTROSPECTION, has a
row here saying, per target, whether it is rendered, read as part of its
parent, an aggregate literal placed by `body_kinds`, refused, or never
produced in an op-model body (and why). A new IR class fails by name.

The twins one layer up are `tests/unit/ir/test_ir_kind_registry.py`
(pssparser -> IR) and `test_activity_registry.py`.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import zuspec.ir.core as ir

from pssc import targets
from pssc.targets import body_kinds

TARGETS = ("op-model-c", "op-model-cpp", "op-model-py", "op-model-sv")

RENDERED, PART, AGGREGATE, REFUSED = "rendered", "part", "aggregate", "refused"


class NotProduced:
    """Never in an op-model body: *why*. An emitter may still have a hook."""
    def __init__(self, why: str):
        self.why = why


def _on(**per_target):
    """A row that differs by target: ``c=RENDERED, cpp=REFUSED, ...``."""
    return {f"op-model-{t}": d for t, d in per_target.items()}


#: IR class -> disposition, or {target: disposition}.
ROWS = {
    # -- statements -----------------------------------------------------------
    "StmtAnnAssign": RENDERED,
    "StmtAssign": RENDERED,
    "StmtAugAssign": RENDERED,
    "StmtBreak": RENDERED,
    "StmtContinue": RENDERED,
    "StmtExpr": RENDERED,
    "StmtFor": RENDERED,            # `repeat (n)`, `repeat (i : n)`
    "StmtForeach": RENDERED,
    "StmtIf": RENDERED,
    "StmtIfChain": RENDERED,        # `if ... else if ...`
    "StmtIfClause": PART,
    "StmtMatch": RENDERED,
    "StmtMatchCase": PART,
    "StmtRepeatWhile": RENDERED,
    "StmtReturn": RENDERED,
    "StmtWhile": RENDERED,
    "StmtYield": RENDERED,
    # `super;` (17.1, 20.1.4). C renders the flattened view, where
    # `comp_inherit` has already replaced it; C++ has no `super;` in a body
    # it renders yet, and refuses one.
    "StmtSuper": _on(c=REFUSED, cpp=REFUSED, py=RENDERED, sv=RENDERED),
    "StmtPass": NotProduced("ast2ir makes one only for an `if` with no "
                            "clause, which the grammar does not allow"),
    "StmtRandomize": NotProduced("refused by ast2ir with its line "
                                 "(test_ir_statements.py)"),
    "StmtDefault": NotProduced("a constraint statement (`default`)"),
    "StmtDefaultDisable": NotProduced("a constraint statement"),
    "StmtUnique": NotProduced("a constraint statement (`unique`)"),
    "StmtAssert": NotProduced("Python frontend (zuspec-dataclasses) only"),
    "StmtAssume": NotProduced("Python frontend only"),
    "StmtCover": NotProduced("Python frontend only"),
    "StmtExceptHandler": NotProduced("Python frontend only"),
    "StmtRaise": NotProduced("Python frontend only"),
    "StmtRepeat": NotProduced("ast2ir writes `repeat` as StmtFor"),
    "StmtTry": NotProduced("Python frontend only"),
    "StmtWith": NotProduced("Python frontend only"),
    # -- expressions ----------------------------------------------------------
    "ExprAttribute": RENDERED,
    "ExprBin": RENDERED,
    "ExprCall": RENDERED,
    "ExprCast": RENDERED,
    "ExprConstant": RENDERED,
    "ExprIfExp": RENDERED,
    "ExprIn": RENDERED,
    "ExprNull": RENDERED,
    "ExprRefLocal": RENDERED,
    "ExprSubscript": RENDERED,
    "ExprUnary": RENDERED,
    "ExprList": AGGREGATE,
    "ExprStructLiteral": AGGREGATE,
    "ExprStructField": PART,
    "ExprRange": PART,
    "ExprRangeList": PART,
    "ExprSlice": PART,
    # A callee's name (`f(...)`, `q::f(...)`), which the call renders. As a
    # value it stands only after ast2ir has reported an error; C++ renders it
    # as a plain identifier.
    "ExprRefUnresolved": _on(c=REFUSED, cpp=RENDERED, py=REFUSED,
                             sv=REFUSED),
    "ExprRefBottomUp": NotProduced(
        "the Python frontend's upward reference; ast2ir never makes one. "
        "The emitters that have a hook refuse it there"),
    "ExprDict": REFUSED,            # a map literal: maps are refused (R4)
    "ExprCompare": NotProduced("an activity `with` (`comp == target`), not "
                               "a procedural body"),
    "ExprAwait": NotProduced("made by the sw target's own lowering"),
    "ExprBool": NotProduced("Python frontend only"),
    "ExprCbit": NotProduced("Python frontend only"),
    "ExprCompileHas": NotProduced("refused by ast2ir with its line "
                                  "(test_ir_expressions.py)"),
    "ExprConcat": NotProduced("Python frontend only"),
    "ExprDictComp": NotProduced("Python frontend only"),
    "ExprFormattedValue": NotProduced("Python frontend only"),
    "ExprGeneratorExp": NotProduced("Python frontend only"),
    "ExprHierarchical": NotProduced("Python frontend only"),
    "ExprHierarchicalElem": NotProduced("Python frontend only"),
    "ExprJoinedStr": NotProduced("Python frontend only"),
    "ExprLambda": NotProduced("Python frontend only"),
    "ExprListComp": NotProduced("Python frontend only"),
    "ExprNamedExpr": NotProduced("Python frontend only"),
    "ExprNew": NotProduced("Python frontend only"),
    "ExprPartSelect": NotProduced("ast2ir writes a part select as "
                                  "ExprSubscript of an ExprSlice"),
    "ExprRef": NotProduced("an abstract base"),
    "ExprRefField": NotProduced("ast2ir writes a field as ExprAttribute"),
    "ExprRefParam": NotProduced("ast2ir writes a parameter as ExprAttribute "
                                "of self (test_param_shadows_field.py)"),
    "ExprRefPy": NotProduced("Python frontend only"),
    "ExprReplicate": NotProduced("Python frontend only"),
    "ExprSet": NotProduced("Python frontend only"),
    "ExprSetComp": NotProduced("Python frontend only"),
    "ExprSext": NotProduced("Python frontend only"),
    "ExprSigned": NotProduced("Python frontend only"),
    "ExprStaticRef": NotProduced("Python frontend only"),
    "ExprStringMethod": NotProduced("ast2ir writes a string method as "
                                    "ExprCall (string_methods.py)"),
    "ExprTuple": NotProduced("Python frontend only"),
    "ExprZext": NotProduced("Python frontend only"),
    "TypeExprRefSelf": PART,
    "TypeExprRefSuper": PART,
    "TypeExprRefTraversed": NotProduced("an action's traversal `with` "
                                        "block, not a procedural body"),
}


def _ir_kinds():
    return sorted(n for n, c in vars(ir).items()
                  if inspect.isclass(c) and n not in ("Stmt", "Expr")
                  and n.startswith(("Stmt", "Expr", "TypeExpr")))


def _disposition(kind, target):
    row = ROWS[kind]
    return row[target] if isinstance(row, dict) else row


def _kinds(target):
    return body_kinds.emitter_kinds(targets.get(target).body_emitter_class())


def test_every_ir_kind_has_a_row():
    missing = [k for k in _ir_kinds() if k not in ROWS]
    assert not missing, (
        f"IR kinds with no row: {missing}. Say how each op-model target "
        f"takes it: rendered (a hook), part, aggregate, refused, or "
        f"NotProduced(why)")


def test_every_row_names_an_ir_kind():
    stale = sorted(set(ROWS) - set(_ir_kinds()))
    assert not stale, stale


def test_per_target_rows_name_every_target():
    bad = [k for k, r in ROWS.items()
           if isinstance(r, dict) and set(r) != set(TARGETS)]
    assert not bad, bad


@pytest.mark.parametrize("target", TARGETS)
def test_rendered_kinds_are_what_the_emitter_renders(target):
    """A row says `rendered` exactly where the emitter has a rendering: a
    kind the emitter renders that a row calls refused would be refused by
    the gate for nothing, and the other way round is an internal error."""
    kinds = _kinds(target)
    said = {k for k in ROWS if _disposition(k, target) == RENDERED}
    produced = {k for k in ROWS
                if not isinstance(_disposition(k, target), NotProduced)}
    assert said - kinds == set(), f"rows say rendered, emitter lacks a hook"
    assert (kinds & produced) - said - set(body_kinds.PARTS) == set(), \
        "the emitter renders kinds a row calls refused"


def test_parts_and_aggregates_are_body_kinds():
    assert {k for k, r in ROWS.items() if r == PART} == set(body_kinds.PARTS)
    assert {k for k, r in ROWS.items() if r == AGGREGATE} \
        == set(body_kinds.AGGREGATES)


def test_cpp_kinds_are_its_cascades():
    """C++ dispatches by `if cn == "..."` cascades, not `BodyWalker` hooks,
    so its `KINDS` is a second statement of the same set. It must say what
    `expr` and `_stmt_lines` actually compare against."""
    from pssc.targets.cpp import lower_progseq as cpp
    tree = ast.parse(Path(cpp.__file__).read_text())
    found = set()
    for cls in tree.body:
        if not (isinstance(cls, ast.ClassDef) and cls.name == "_BodyEmitter"):
            continue
        for fn in cls.body:
            if not (isinstance(fn, ast.FunctionDef)
                    and fn.name in ("expr", "_stmt_lines")):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Compare) \
                        and isinstance(n.left, ast.Name) \
                        and n.left.id == "cn":
                    for c in n.comparators:
                        if isinstance(c, ast.Constant):
                            found.add(c.value)
    assert found == set(cpp._BodyEmitter.KINDS)
