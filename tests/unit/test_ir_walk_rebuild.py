"""The published walk and the rebuilding transformer
(``ir_walk.walk`` / ``rebuild`` / ``Transformer``)."""
import pytest

import zuspec.ir.core as ir

from pssc.targets import ir_walk


def _call(name, *args):
    return ir.ExprCall(func=ir.ExprRefUnresolved(name=name), args=list(args))


def _body():
    # x = f(1) + g();  if (h()) { k(); }
    a = ir.StmtAssign(targets=[ir.ExprRefUnresolved(name="x")],
                      value=ir.ExprBin(lhs=_call("f", ir.ExprConstant(value=1)),
                                       op=ir.BinOp.Add, rhs=_call("g")))
    a.loc = ir.Loc(file="t.pss", line=3, pos=1)
    a.annotations = [ir.Annotation(type_qname="p::a_s", values={})]
    i = ir.StmtIf(test=_call("h"), body=[ir.StmtExpr(expr=_call("k"))],
                  orelse=[])
    i.provenance = ir.Provenance(pass_name="t", source_nodes=[_call("z")])
    return [a, i]


def _names(nodes):
    return [n.func.name for n in nodes if isinstance(n, ir.ExprCall)]


def test_walk_is_pre_order_in_source_order():
    body = _body()
    nodes = list(ir_walk.walk(body))
    assert nodes[0] is body[0]
    assert _names(nodes) == ["f", "g", "h", "k"]
    # parent before child
    assert nodes.index(body[0].value) < nodes.index(body[0].value.lhs)


def test_walk_skips_provenance_and_annotations():
    nodes = list(ir_walk.walk(_body()))
    assert "z" not in _names(nodes)
    assert not [n for n in nodes if isinstance(n, ir.Annotation)]


def test_rebuild_keeps_loc_provenance_annotations():
    a = _body()[0]
    v = _call("n")
    b = ir_walk.rebuild(a, value=v)
    assert b is not a and b.value is v and a.value is not v
    assert b.loc is a.loc and b.annotations is a.annotations
    assert b.provenance is a.provenance and b.targets is a.targets


def test_rebuild_unchanged_is_identity():
    a = _body()[0]
    assert ir_walk.rebuild(a, value=a.value) is a
    assert ir_walk.rebuild(a) is a


def test_rebuild_refuses_non_child_fields():
    with pytest.raises(ValueError):
        ir_walk.rebuild(_body()[0], loc=None)


class _Rename(ir_walk.Transformer):
    def visit_ExprCall(self, node):
        node = self.generic_visit(node)
        if node.func.name == "g":
            return ir_walk.rebuild(node, func=ir.ExprRefUnresolved(name="G"))
        return node


def test_transformer_rewrites_copy_and_shares_untouched():
    body = _body()
    out = _Rename().visit(body)
    assert _names(ir_walk.walk(out)) == ["f", "G", "h", "k"]
    assert _names(ir_walk.walk(body)) == ["f", "g", "h", "k"]   # input intact
    assert out[0] is not body[0] and out[0].loc is body[0].loc
    assert out[0].annotations is body[0].annotations
    assert out[0].value.lhs is body[0].value.lhs                # shared
    assert out[1] is body[1]                                    # untouched


class _Split(ir_walk.Transformer):
    """Each `k();` becomes `pre(); k();`; each `drop();` goes."""
    def visit_StmtExpr(self, node):
        name = node.expr.func.name
        if name == "k":
            return [ir.StmtExpr(expr=_call("pre")), node]
        if name == "drop":
            return []
        return node


def test_transformer_splices_lists_in_nested_bodies():
    body = _body() + [ir.StmtExpr(expr=_call("drop"))]
    out = _Split().visit(body)
    assert len(out) == 2
    assert _names(ir_walk.walk(out[1].body)) == ["pre", "k"]
    assert out[1].provenance is body[1].provenance
    assert len(body[1].body) == 1


def test_transformer_noop_returns_same_tree():
    body = _body()
    assert ir_walk.Transformer().visit(body) is body


def test_list_for_a_single_child_is_an_error():
    class Bad(ir_walk.Transformer):
        def visit_ExprBin(self, node):
            return [node]
    with pytest.raises(TypeError):
        Bad().visit(_body())
