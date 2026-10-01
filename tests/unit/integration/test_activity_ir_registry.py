"""Every ir-core activity node lowers to bc correctly, or is refused.

The second silent-drop registry (P0 plan §4). ir-core's ``ActivityStmt``
subclasses are enumerated BY INTROSPECTION -- including the ones only the
Python front end builds (WhileDo, Fill, Chain, ConstraintForall) -- and each
needs a row: a hand-built IR fragment, and whether it LOWERS (through
``PSSToScenarioPass`` and bc's ``lower_module``) or is REFUSED with
``UnsupportedConstructError``/``LoweringError``. A new IR node fails
``test_every_activity_ir_node_has_a_row`` by name.
"""
import dataclasses as dc

import pytest

import zuspec.ir.core as ir
from zuspec.ir.core import activity as A
from zuspec.ir.core import scenario as SC
from zuspec.ir.core.xf import PSSToScenarioPass
from zuspec.ir.core.xf.validate import UnsupportedConstructError
from zuspec.be.bc.lower import lower_module
from zuspec.be.bc.lower.errors import LoweringError


def _b():
    return A.ActivityAnonTraversal(action_type="B")


def _c(v):
    return ir.ExprConstant(value=v)


def _self(name):
    return ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=name)


LOWERS = "lowers"


class Refused:
    def __init__(self, pattern):
        self.pattern = pattern


# "Class[variant]" -> (fragment factory, expectation). The class is the part
# before "[".
CASES = {
    "ActivityAnonTraversal": (lambda: _b(), LOWERS),
    "ActivityAnonTraversal[with]": (
        lambda: A.ActivityAnonTraversal(action_type="B",
                                        inline_constraints=[_c(True)]),
        Refused("inline traversal constraints")),
    "ActivityAtomic": (lambda: A.ActivityAtomic(stmts=[_b()]), LOWERS),
    "ActivityBind": (lambda: A.ActivityBind(src=_self("a"), dst=_self("b")),
                     Refused("ActivityBind")),
    "ActivityChain": (lambda: A.ActivityChain(), Refused("ActivityChain")),
    # In force while its scope is (13.1.9 b.3): a constraint of the action
    # tree's cone (P1.4).
    "ActivityConstraint": (lambda: A.ActivityConstraint(constraints=[_c(True)]),
                           LOWERS),
    "ActivityConstraintForall": (
        lambda: A.ActivityConstraintForall(var_name="x", type_name="B"),
        Refused("ActivityConstraintForall")),
    "ActivityDoWhile": (lambda: A.ActivityDoWhile(condition=_c(0), body=[_b()]),
                        LOWERS),
    "ActivityFieldDecl": (
        lambda: A.ActivityFieldDecl(
            field=ir.Field(name="bb", datatype=ir.DataTypeRef(ref_name="pss_top::B")),
            type_qname="pss_top::B"),
        LOWERS),
    "ActivityFieldDecl[data]": (
        lambda: A.ActivityFieldDecl(
            field=ir.Field(name="n", datatype=ir.DataTypeInt(bits=4, signed=False))),
        Refused("data field 'n' declared in an activity block")),
    "ActivityFill": (lambda: A.ActivityFill(), Refused("ActivityFill")),
    "ActivityForeach": (
        lambda: A.ActivityForeach(iterator="v", collection=_self("vals"),
                                  body=[_b()]),
        Refused("foreach")),
    "ActivityIfElse": (
        lambda: A.ActivityIfElse(condition=_c(1), if_body=[_b()], else_body=[_b()]),
        LOWERS),
    "ActivityMatch": (
        lambda: A.ActivityMatch(subject=_c(0), cases=[
            A.MatchCase(pattern=None, body=[_b()])]),
        LOWERS),
    "ActivityParallel": (lambda: A.ActivityParallel(stmts=[_b(), _b()]), LOWERS),
    "ActivityParallel[NONE]": (
        lambda: A.ActivityParallel(stmts=[_b()],
                                   join_spec=A.JoinSpec(kind=A.JoinKind.NONE)),
        LOWERS),
    "ActivityParallel[FIRST]": (
        lambda: A.ActivityParallel(stmts=[_b(), _b()], join_spec=A.JoinSpec(
            kind=A.JoinKind.FIRST, count=_c(1))),
        LOWERS),
    "ActivityParallel[SELECT]": (
        lambda: A.ActivityParallel(stmts=[_b(), _b()], join_spec=A.JoinSpec(
            kind=A.JoinKind.SELECT, count=_c(1))),
        Refused("SELECT")),
    "ActivityParallel[BRANCH]": (
        lambda: A.ActivityParallel(stmts=[_b(), _b()], join_spec=A.JoinSpec(
            kind=A.JoinKind.BRANCH, branch_labels=["L"])),
        Refused("BRANCH")),
    "ActivityRepeat": (lambda: A.ActivityRepeat(count=_c(2), body=[_b()]), LOWERS),
    "ActivityReplicate": (lambda: A.ActivityReplicate(count=_c(2), body=[_b()]),
                          LOWERS),
    # Unrolled: each iteration runs its own nodes (P1.4).
    "ActivityReplicate[label]": (
        lambda: A.ActivityReplicate(count=_c(2), label="R", body=[_b()]),
        LOWERS),
    "ActivitySchedule": (lambda: A.ActivitySchedule(stmts=[_b(), _b()]), LOWERS),
    "ActivitySchedule[constraint]": (
        lambda: A.ActivitySchedule(stmts=[_b(), A.ActivitySchedulingConstraint(
            is_parallel=True, targets=[_self("x")])]),
        Refused("schedule")),
    "ActivitySchedulingConstraint": (
        lambda: A.ActivitySchedulingConstraint(is_parallel=False),
        Refused("ActivitySchedulingConstraint")),
    "ActivitySelect": (
        lambda: A.ActivitySelect(branches=[A.SelectBranch(body=[_b()]),
                                           A.SelectBranch(body=[_b()])]),
        LOWERS),
    "ActivitySequenceBlock": (lambda: A.ActivitySequenceBlock(stmts=[_b()]), LOWERS),
    "ActivitySuper": (lambda: A.ActivitySuper(), Refused("ActivitySuper")),
    "ActivityTraversal": (lambda: A.ActivityTraversal(handle="b1"), LOWERS),
    "ActivityTraversal[initializers]": (
        lambda: A.ActivityTraversal(handle="b1", initializers=[
            (ir.ExprAttribute(value=ir.TypeExprRefTraversed(), attr="x"), _c(1))]),
        LOWERS),
    "ActivityWhileDo": (lambda: A.ActivityWhileDo(condition=_c(0), body=[_b()]),
                        Refused("ActivityWhileDo")),
}


def _ir_classes():
    def subs(c):
        for s in c.__subclasses__():
            yield s
            yield from subs(s)
    return {c.__name__ for c in subs(A.ActivityStmt)}


def _type_map(stmt):
    """pss_top with an atomic `B` (attribute `x`) and a compound `T`
    running *stmt*."""
    b = ir.DataTypeClass(name="B", super=None)
    b.fields = [ir.Field(name="x", datatype=ir.DataTypeInt(bits=4, signed=False))]
    b.functions = [ir.Function(name="body", body=[])]
    t = ir.DataTypeClass(name="T", super=None)
    t.fields = [ir.Field(name="b1", datatype=ir.DataTypeRef(ref_name="pss_top::B"))]
    t.activity_ir = A.ActivitySequenceBlock(stmts=[stmt])
    return {"pss_top": ir.DataTypeComponent(name="pss_top", super=None),
            "pss_top::B": b, "pss_top::T": t}


def _lower(stmt):
    module = PSSToScenarioPass(root="pss_top", exports=["T"]).lower(_type_map(stmt))
    return lower_module(module, entry_action="T", solve_unconstrained=True)


def test_every_activity_ir_node_has_a_row():
    classes = _ir_classes()
    rows = {k.split("[")[0] for k in CASES}
    missing = sorted(classes - rows)
    stale = sorted(rows - classes)
    assert not missing, (
        "ir-core has activity node(s) with no row here: %s. Decide whether "
        "bc lowers each (correctly) or refuses it, and add the row."
        % ", ".join(missing))
    assert not stale, "rows for classes ir-core no longer has: %s" % ", ".join(stale)


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_activity_ir_node_lowers_or_is_refused(name):
    make, expect = CASES[name]
    if expect == LOWERS:
        _lower(make())
    else:
        with pytest.raises((UnsupportedConstructError, LoweringError),
                           match=expect.pattern):
            _lower(make())


def test_invoke_of_unknown_target_is_refused():
    """Gate item 3: an INVOKE/SPAWN naming no lowered coroutine is a
    LoweringError in bc -- it never runs coroutine 0."""
    module = PSSToScenarioPass(root="pss_top", exports=["T"]).lower(_type_map(_b()))
    for coro in module.coroutines.values():
        for s in coro.body:
            if isinstance(s, SC.ScInvoke):
                s.target = "no_such_action"
    with pytest.raises(LoweringError, match="no_such_action"):
        lower_module(module, entry_action="T", solve_unconstrained=True)
