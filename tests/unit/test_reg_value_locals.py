"""Binding register-value struct locals to a register layout
(``reg_value_locals.bind``), IR-level: hand-built bodies, with a register
``a`` and ``b`` of one layout and ``c`` of another."""
import pytest

import zuspec.ir.core as ir

from pssc.diag import Refusal
from pssc.targets.reg_value_locals import bind

LAYOUT = {"a": 1, "b": 1, "c": 2}


def _acc(reg, meth, *args):
    # self.<reg>.<meth>(args)
    return ir.ExprCall(func=ir.ExprAttribute(
        value=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=reg),
        attr=meth), args=list(args))


def _l(name):
    return ir.ExprRefLocal(name=name)


def _at(st, line):
    st.loc = ir.Loc(file="t.pss", line=line, pos=1)
    return st


def _decl(name, value=None, line=1):
    return _at(ir.StmtAnnAssign(target=_l(name), annotation=ir.DataTypeInt(),
                                value=value), line)


def _set(tgt, value, line=2):
    return _at(ir.StmtAssign(targets=[tgt], value=value), line)


def _expr(e, line=3):
    return _at(ir.StmtExpr(expr=e), line)


def _bind(body):
    def access_of(call):
        f = call.func
        if getattr(f, "attr", None) in ("read", "write"):
            return f.value.attr, f.attr
        return None
    return bind(body, access_of, lambda d: d.target.name.startswith("v"),
                lambda x, y: LAYOUT[x] == LAYOUT[y])


def _field(name, f="en"):
    return ir.ExprAttribute(value=_l(name), attr=f)


def test_read_field_set_write():
    body = [_decl("v", _acc("a", "read")),
            _set(_field("v"), ir.ExprConstant(value=1)),
            _expr(_acc("a", "write", _l("v")))]
    got = _bind(body)
    assert list(got) == ["v"] and got["v"].access == "a"
    assert got["v"].call is body[0].value


def test_bound_by_its_write_alone_and_through_a_copy():
    body = [_decl("v"), _decl("v2", _l("v")),
            _set(_field("v"), _field("v2")),
            _expr(_acc("b", "write", _l("v2")))]
    got = _bind(body)
    assert got["v"].access == got["v2"].access == "b"


def test_same_layout_two_registers():
    body = [_decl("v", _acc("a", "read")), _expr(_acc("b", "write", _l("v")))]
    assert _bind(body)["v"].access == "a"


def test_other_locals_are_not_candidates():
    body = [_decl("x", _acc("a", "read")),
            _expr(ir.ExprCall(func=ir.ExprRefUnresolved(name="f"),
                              args=[_l("x")]))]
    assert _bind(body) == {}


def test_two_layouts_refused():
    body = [_decl("v", _acc("a", "read")),
            _expr(_acc("c", "write", _l("v")), line=5)]
    with pytest.raises(Refusal, match="two registers whose layouts differ") \
            as ei:
        _bind(body)
    assert ei.value.nodes[0] is body[1]


def test_two_layouts_through_a_copy_refused():
    body = [_decl("v", _acc("a", "read")), _decl("v2", _acc("c", "read")),
            _set(_l("v"), _l("v2"), line=4)]
    with pytest.raises(Refusal, match="copied into each other"):
        _bind(body)


def test_no_register_refused():
    body = [_decl("v", line=9), _set(_field("v"), ir.ExprConstant(value=1))]
    with pytest.raises(Refusal, match="never read from or written to") as ei:
        _bind(body)
    assert ei.value.nodes[0] is body[0]


def test_whole_use_refused():
    call = ir.ExprCall(func=ir.ExprRefUnresolved(name="f"), args=[_l("v")])
    body = [_decl("v", _acc("a", "read")), _expr(call, line=6)]
    with pytest.raises(Refusal, match="used whole here") as ei:
        _bind(body)
    assert ei.value.nodes[0] is body[1]
