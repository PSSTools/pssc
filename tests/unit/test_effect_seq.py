"""The effect-sequencing pass (``effect_seq.sequence``), IR-level: each
rule on a hand-built body, with the default register-read predicate."""
import pytest

import zuspec.ir.core as ir

from pssc.diag import Refusal
from pssc.targets import effect_seq, ir_walk


def _rd(reg="r", meth="read"):
    # self.<reg>.<meth>()
    return ir.ExprCall(func=ir.ExprAttribute(
        value=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=reg),
        attr=meth), args=[])


def _call(name, *args):
    return ir.ExprCall(func=ir.ExprRefUnresolved(name=name), args=list(args))


def _loc(st, line=7):
    st.loc = ir.Loc(file="t.pss", line=line, pos=1)
    return st


def _local(name):
    return ir.ExprRefLocal(name=name)


def _add(a, b):
    return ir.ExprBin(lhs=a, op=ir.BinOp.Add, rhs=b)


def _reads(nodes):
    return [n for n in ir_walk.walk(nodes) if isinstance(n, ir.ExprCall)
            and getattr(n.func, "attr", None) == "read"]


def _decl(st):
    assert isinstance(st, ir.StmtAnnAssign)
    return st.target.name


def test_no_match_returns_same_body():
    body = [ir.StmtExpr(expr=_call("f", _add(_call("g"), _local("x")))),
            ir.StmtReturn(value=_call("h"))]
    assert effect_seq.sequence(body) is body


def test_whole_statement_and_decl_and_local_assign_are_left():
    body = [ir.StmtExpr(expr=_rd()),
            ir.StmtAnnAssign(target=_local("v"),
                             annotation=ir.DataTypeInt(bits=32, signed=False),
                             value=_rd("s")),
            ir.StmtAssign(targets=[_local("v")], value=_rd("t"))]
    assert effect_seq.sequence(body) is body


def test_read_in_expression_is_hoisted_before_its_statement():
    st = _loc(ir.StmtExpr(expr=_call("f", _add(_rd(), ir.ExprConstant(value=1)))))
    out = effect_seq.sequence([st])
    assert len(out) == 2
    name = _decl(out[0])
    assert out[0].value.func.attr == "read"
    assert out[0].annotation.bits == 32 and not out[0].annotation.signed
    assert out[0].loc is st.loc
    assert out[1].expr.args[0].lhs.name == name
    assert st.expr.args[0].lhs.func.attr == "read"     # input unchanged


def test_reads_are_hoisted_in_source_order():
    st = ir.StmtReturn(value=_add(_rd("a"), _rd("b")))
    out = effect_seq.sequence([st])
    assert [s.value.func.value.attr for s in out[:2]] == ["a", "b"]
    assert out[2].value.lhs.name == out[0].target.name
    assert out[2].value.rhs.name == out[1].target.name


def test_assign_to_field_is_hoisted():
    tgt = ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="x")
    out = effect_seq.sequence([ir.StmtAssign(targets=[tgt], value=_rd())])
    assert len(out) == 2
    assert out[1].targets[0] is tgt
    assert out[1].value.name == _decl(out[0])


def test_decl_and_return_with_read_in_expression():
    d = ir.StmtAnnAssign(target=_local("v"), annotation=None,
                         value=_add(_rd(), ir.ExprConstant(value=2)))
    r = ir.StmtReturn(value=ir.ExprUnary(op=ir.UnaryOp.Invert, operand=_rd()))
    out = effect_seq.sequence([d, r])
    assert [type(s).__name__ for s in out] == [
        "StmtAnnAssign", "StmtAnnAssign", "StmtAnnAssign", "StmtReturn"]
    assert out[1].target.name == "v"
    assert out[1].value.lhs.name == _decl(out[0])
    assert out[3].value.operand.name == _decl(out[2])


def test_read_in_call_args_keeps_call_after_it():
    # f(r.read()) : the read is evaluated before f anyway
    out = effect_seq.sequence([ir.StmtExpr(expr=_call("f", _rd()))])
    assert out[1].expr.func.name == "f"
    assert out[1].expr.args[0].name == _decl(out[0])


def test_and_right_operand_becomes_nested_if():
    # x = c && (r.read() == 1)
    cmp = ir.ExprCompare(left=_rd(), ops=[ir.CmpOp.Eq],
                         comparators=[ir.ExprConstant(value=1)])
    e = ir.ExprBool(op=ir.BoolOp.And, values=[_local("c"), cmp])
    out = effect_seq.sequence([ir.StmtAssign(targets=[_local("x")], value=e)])
    t = _decl(out[0])
    assert out[0].annotation.name == "bool"
    assert out[0].value.name == "c"
    i = out[1]
    assert isinstance(i, ir.StmtIf) and i.test.name == t and not i.orelse
    assert _decl(i.body[0]) and i.body[0].value.func.attr == "read"
    assert i.body[1].targets[0].name == t
    assert out[2].value.name == t
    # no read outside the if
    assert not _reads(out[:1] + out[2:])


def test_or_tests_not_and_nests_per_operand():
    e = ir.ExprBool(op=ir.BoolOp.Or, values=[_local("c"), _rd("a"), _rd("b")])
    out = effect_seq.sequence([ir.StmtReturn(value=e)])
    t = _decl(out[0])
    i1 = out[1]
    assert isinstance(i1.test, ir.ExprUnary) and i1.test.op == ir.UnaryOp.Not
    assert i1.body[0].value.func.value.attr == "a"
    i2 = i1.body[-1]
    assert isinstance(i2, ir.StmtIf) and i2.body[0].value.func.value.attr == "b"
    assert out[2].value.name == t


def test_the_front_ends_binary_and_or_short_circuit():
    # The parser's `&&` / `||` is `ExprBin(op=BinOp.And/Or)`, not `ExprBool`.
    for op, neg in ((ir.BinOp.And, False), (ir.BinOp.Or, True)):
        e = ir.ExprBin(lhs=_local("c"), op=op, rhs=_rd())
        out = effect_seq.sequence([ir.StmtReturn(value=e)])
        t = _decl(out[0])
        assert out[0].value.name == "c"
        i = out[1]
        assert isinstance(i.test, ir.ExprUnary) is neg
        assert i.body[0].value.func.attr == "read"
        assert out[2].value.name == t and not _reads(out[:1] + out[2:])


def test_and_left_read_is_unconditional():
    e = ir.ExprBool(op=ir.BoolOp.And, values=[_rd(), _local("c")])
    out = effect_seq.sequence([ir.StmtReturn(value=e)])
    assert len(out) == 2 and out[1].value.values[0].name == _decl(out[0])


def test_and_as_statement_drops_the_bare_temp():
    e = ir.ExprBool(op=ir.BoolOp.And, values=[_local("c"), _rd()])
    out = effect_seq.sequence([ir.StmtExpr(expr=e)])
    assert [type(s).__name__ for s in out] == ["StmtAnnAssign", "StmtIf"]


def test_ifexp_arms_become_if_else():
    e = ir.ExprIfExp(test=_local("c"), body=_rd("a"),
                     orelse=ir.ExprConstant(value=0))
    out = effect_seq.sequence([ir.StmtReturn(value=e)])
    t = _decl(out[0])
    assert out[0].value is None and out[0].annotation.bits == 32
    i = out[1]
    assert i.test.name == "c"
    assert i.body[0].value.func.value.attr == "a"
    assert i.body[-1].targets[0].name == t
    assert i.orelse[0].value.value == 0 and i.orelse[0].targets[0].name == t
    assert out[2].value.name == t


def test_ifexp_arm_without_type_is_refused():
    e = ir.ExprIfExp(test=_local("c"), body=_add(_rd(), _local("y")),
                     orelse=ir.ExprConstant(value=0))
    with pytest.raises(Refusal):
        effect_seq.sequence([_loc(ir.StmtReturn(value=e))])
    out = effect_seq.sequence(
        [ir.StmtReturn(value=e)],
        type_of=lambda x: ir.DataTypeInt(bits=16, signed=False))
    assert out[0].annotation.bits == 16


def test_unmatched_call_before_read_is_refused_located():
    g = _call("g")
    g.loc = ir.Loc(file="t.pss", line=9, pos=5)
    st = _loc(ir.StmtReturn(value=_add(g, _rd())))
    with pytest.raises(Refusal) as ei:
        effect_seq.sequence([st])
    assert "g()" in str(ei.value)
    assert ei.value.nodes[0] is g and "t.pss" in ei.value.located()


def test_unmatched_call_after_read_is_kept():
    out = effect_seq.sequence([ir.StmtReturn(value=_add(_rd(), _call("g")))])
    assert out[1].value.rhs.func.name == "g"


def test_nested_bodies_are_sequenced():
    inner = ir.StmtReturn(value=_add(_rd(), _local("y")))
    st = ir.StmtIf(test=_local("c"), body=[inner], orelse=[])
    out = effect_seq.sequence([st])
    assert len(out) == 1 and len(out[0].body) == 2


def test_fresh_names_avoid_body_names():
    body = [ir.StmtAnnAssign(target=_local("_pss_rd0"), annotation=None,
                             value=ir.ExprConstant(value=0)),
            ir.StmtReturn(value=_add(_rd(), _local("_pss_rd0")))]
    out = effect_seq.sequence(body)
    assert _decl(out[1]) != "_pss_rd0"
    names = iter(["a", "b"])
    out = effect_seq.sequence([ir.StmtReturn(value=_add(_rd(), _rd()))],
                              fresh=lambda hint: next(names))
    assert [_decl(s) for s in out[:2]] == ["a", "b"]


def test_custom_predicate():
    pred = lambda c: ir.DataTypeInt(bits=8, signed=False) \
        if getattr(c.func, "name", None) == "h" else None
    out = effect_seq.sequence([ir.StmtReturn(value=_add(_call("h"), _rd()))],
                              pred=pred)
    assert out[0].value.func.name == "h" and out[0].annotation.bits == 8
    assert out[1].value.rhs.func.attr == "read"     # not matched: left


# -- conditions and bounds (PC-10c) ------------------------------------------

def _eq(a, v):
    return ir.ExprCompare(left=a, ops=[ir.CmpOp.Eq],
                          comparators=[ir.ExprConstant(value=v)])


def _ret(name):
    return ir.StmtReturn(value=_local(name))


def _breaks_unless(st, t):
    # if (!t) break;
    assert isinstance(st, ir.StmtIf) and st.test.op == ir.UnaryOp.Not
    assert st.test.operand.name == t and isinstance(st.body[0], ir.StmtBreak)


def test_if_test_is_hoisted_and_bodies_sequenced():
    st = ir.StmtIf(test=_eq(_rd(), 1),
                   body=[ir.StmtReturn(value=_add(_rd("s"), _local("y")))],
                   orelse=[])
    out = effect_seq.sequence([st])
    t = _decl(out[0])
    assert out[1].test.left.name == t
    assert _decl(out[1].body[0]) and out[1].body[0].value.func.value.attr == "s"


def test_if_chain_later_test_nests_under_else():
    # if (a) A else if (r.read()==1) B else if (c) C else D
    ch = ir.StmtIfChain(clauses=[
        ir.StmtIfClause(test=_local("a"), body=[_ret("A")]),
        ir.StmtIfClause(test=_eq(_rd(), 1), body=[_ret("B")]),
        ir.StmtIfClause(test=_local("c"), body=[_ret("C")])],
        orelse=[_ret("D")])
    out = effect_seq.sequence([ch])
    assert len(out) == 1
    top = out[0]
    assert [c.test.name for c in top.clauses] == ["a"]
    assert not _reads(top.clauses)
    t = _decl(top.orelse[0])
    rest = top.orelse[1]
    assert rest.clauses[0].test.left.name == t
    assert rest.clauses[1].test.name == "c"
    assert rest.orelse[0].value.name == "D"


def test_if_chain_first_test_is_hoisted_before_chain():
    ch = ir.StmtIfChain(clauses=[
        ir.StmtIfClause(test=_eq(_rd(), 1), body=[_ret("A")]),
        ir.StmtIfClause(test=_local("b"), body=[_ret("B")])], orelse=[])
    out = effect_seq.sequence([ch])
    t = _decl(out[0])
    assert out[1].clauses[0].test.left.name == t
    assert len(out[1].clauses) == 2


def test_match_subject_is_hoisted():
    st = ir.StmtMatch(subject=_rd(), cases=[])
    out = effect_seq.sequence([st])
    assert out[1].subject.name == _decl(out[0])


def test_match_guard_read_is_refused():
    case = ir.StmtMatchCase(pattern=None, guard=_eq(_rd(), 1), body=[])
    st = _loc(ir.StmtMatch(subject=_local("x"), cases=[case]))
    with pytest.raises(Refusal, match="guard"):
        effect_seq.sequence([st])


def test_while_condition_is_reread_each_iteration():
    # while (r.read() == 1) { f(); }
    st = ir.StmtWhile(test=_eq(_rd(), 1),
                      body=[ir.StmtExpr(expr=_call("f"))], orelse=[])
    out = effect_seq.sequence([st])
    assert len(out) == 1
    w = out[0]
    assert w.test.value is True
    t = _decl(w.body[0])
    assert w.body[0].value.func.attr == "read"
    assert w.body[1].test.operand.left.name == t
    assert isinstance(w.body[1].body[0], ir.StmtBreak)
    assert w.body[2].expr.func.name == "f"


def test_while_with_else_is_refused():
    st = ir.StmtWhile(test=_rd(), body=[], orelse=[_ret("x")])
    with pytest.raises(Refusal, match="else"):
        effect_seq.sequence([st])


def test_repeat_while_condition_is_read_at_end_of_body():
    st = ir.StmtRepeatWhile(condition=_rd(),
                            body=[ir.StmtExpr(expr=_call("f"))])
    out = effect_seq.sequence([st])
    w = out[0]
    assert w.condition.value is True
    assert w.body[0].expr.func.name == "f"
    _breaks_unless(w.body[2], _decl(w.body[1]))


def test_repeat_while_with_continue_is_refused():
    cont = _loc(ir.StmtContinue(), 11)
    st = ir.StmtRepeatWhile(condition=_rd(), body=[
        ir.StmtIf(test=_local("c"), body=[cont], orelse=[])])
    with pytest.raises(Refusal) as ei:
        effect_seq.sequence([st])
    assert ei.value.nodes[0] is cont


def test_repeat_while_continue_of_inner_loop_is_fine():
    inner = ir.StmtWhile(test=_local("c"), body=[ir.StmtContinue()],
                         orelse=[])
    st = ir.StmtRepeatWhile(condition=_rd(), body=[inner])
    out = effect_seq.sequence([st])
    assert out[0].body[0] is inner


def test_for_iter_and_repeat_count_are_hoisted():
    fe = ir.StmtForeach(target=_local("i"), iter=_rd(), body=[],
                        index_var=None)
    rp = ir.StmtRepeat(count=_rd("s"), iterator=None, body=[])
    out = effect_seq.sequence([fe, rp])
    assert out[1].iter.name == _decl(out[0])
    assert out[3].count.name == _decl(out[2])


def test_read_in_assignment_target_is_refused():
    tgt = ir.ExprSubscript(value=_local("a"), slice=_rd())
    st = _loc(ir.StmtAssign(targets=[tgt], value=_local("x")))
    with pytest.raises(Refusal, match="target"):
        effect_seq.sequence([st])


def test_masked_write_field_values_share_the_arguments_locals():
    # r.write_val_masked(m, f(s.read())) with fields [(lvl, s.read())]: the
    # field value is the argument's own node, so it names the same local.
    # Left alone, a field-wise backend would read `s` a second time.
    from pssc.reg_field_resolve import FieldSlice
    from pssc.reg_rmw import RegFieldWrite, RegWriteProvenance
    rd = _rd("s")
    call = ir.ExprCall(func=ir.ExprAttribute(
        value=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="r"),
        attr="write_val_masked"), args=[ir.ExprConstant(value=0xF0),
                                        _call("f", rd)])
    fs = FieldSlice("lvl", 4, 4)
    prov = RegWriteProvenance(pass_name="reg_rmw",
                              fields=(RegFieldWrite(fs, rd, fs.mask),))
    call.provenance = prov
    out = effect_seq.sequence([ir.StmtExpr(expr=call)])
    t = _decl(out[0])
    fw = out[1].expr.provenance.fields
    assert fw[0].value.name == t and out[1].expr.args[1].args[0].name == t
    assert not _reads(out[1:]) and prov.fields[0].value is rd
