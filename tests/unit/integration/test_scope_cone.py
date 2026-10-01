"""The solve cones of an action tree (P1.2, design P1-D2/P1-D3).

Every constraint that can be in force on an activation is resolved to slots
of its flattened object and tagged with when it holds: a node type's own
(TYPE), an activity ``constraint`` (ACTIVITY, with its scope, 13.1.9), an
inline ``with`` (WITH, with its traversal, 13.1.4). Nodes a constraint ties
together form a cone; a node nothing ties to another keeps its coroutine's
own solve (P1-D3). bc solves the cones (P1.4, ``test_lookahead.py``).
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir import core as ir
from zuspec.ir.core import scenario as SC
from zuspec.ir.core.xf.pss_lower.action_tree import Layouts, build_tree

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

K = SC.ScopeConstraintKind


def _tree(src: str, export: str = "T") -> SC.ScActionTree:
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        ctx = AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)
    assert not ctx.errors, ctx.errors
    return build_tree(Layouts(ctx.type_map), export, "pss_top::" + export)


def _r(t, e):
    """A constraint expression with slots named by node path."""
    names = {v.slot: v.name for c in t.cones for v in c.vars}
    if isinstance(e, ir.ExprRefField):
        return names.get(e.index, "@%d" % e.index)
    if isinstance(e, ir.ExprBin):
        return "%s %s %s" % (_r(t, e.lhs), e.op.name, _r(t, e.rhs))
    if isinstance(e, ir.ExprConstant):
        return str(e.value)
    if isinstance(e, ir.ExprAttribute):
        return "%s.%s" % (_r(t, e.value), e.attr)
    return type(e).__name__


def _cons(t, cone):
    return [(c.kind, _r(t, c.constraint.expr)) for c in cone.constraints]


_EX179 = """\
component pss_top {
    action A { rand bit[4] val; }
    action B { rand bit[4] val; }
    action C { rand bit[4] val; }
    action T {
        A a; B b; C c;
        activity { a; b; c; }
        constraint abc_c { a.val < b.val; b.val < c.val; }
    }
}
"""


def test_ex179_parent_constraints_tie_the_handles():
    t = _tree(_EX179)
    cone, = t.cones
    assert [t.nodes[n].path for n in cone.nodes] == ["a", "b", "c"]
    assert [v.name for v in cone.vars] == ["a.val", "b.val", "c.val"]
    assert _cons(t, cone) == [(K.TYPE, "a.val Lt b.val"), (K.TYPE, "b.val Lt c.val")]
    assert all(c.owner == 0 for c in cone.constraints)


_EX184 = """\
component pss_top {
    action A { rand bit[4] val; }
    action S { A a, b, c; activity { a; b; c; }
               constraint { a.val < b.val; b.val < c.val; } }
    action T {
        A v; S s1;
        activity { v; s1; }
        constraint s1.a.val == v.val;
    }
}
"""


def test_ex184_the_cone_reaches_into_an_untraversed_compound():
    """v's cone holds s1's sub-actions, before s1 is traversed (13.4.10)."""
    t = _tree(_EX184)
    cone, = t.cones
    assert sorted(t.nodes[n].path for n in cone.nodes) == [
        "s1.a", "s1.b", "s1.c", "v"]
    assert sorted(_cons(t, cone), key=str) == sorted([
        (K.TYPE, "s1.a.val Eq v.val"),
        (K.TYPE, "s1.a.val Lt s1.b.val"), (K.TYPE, "s1.b.val Lt s1.c.val")], key=str)
    owners = {_r(t, c.constraint.expr): t.nodes[c.owner].path for c in cone.constraints}
    assert owners["s1.a.val Lt s1.b.val"] == "s1"


def test_a_node_nothing_ties_keeps_its_own_solve():
    """P1-D3: its own constraints only -- no cone."""
    t = _tree("""\
component pss_top {
    action A { rand bit[4] val; constraint val < 3; }
    action T { A a; activity { a; } }
}
""")
    assert t.cones == []


def test_with_ties_the_traversal_to_its_parent():
    t = _tree("""\
component pss_top {
    action A { rand bit[4] val; rand bit[4] px; }
    action T { rand bit[4] px; A a1;
        activity { a1 with { val < px; px == 2; }; do A with { val > this.px; }; } }
}
""")
    # `px` in a1's with is a1's own (13.1.4: child first), so a1 is a cone of
    # its own; `this.px` is T's, which ties #0 to the root.
    a1, rest = sorted(t.cones, key=lambda c: len(c.nodes))
    assert [t.nodes[n].path for n in a1.nodes] == ["a1"]
    assert [t.nodes[n].path for n in rest.nodes] == ["", "#0"]
    cons = [(c.kind, _r(t, c.constraint.expr), c.site)
            for c in a1.constraints + rest.constraints]
    assert cons == [
        (K.WITH, "a1.val Lt a1.px", 0), (K.WITH, "a1.px Eq 2", 0),
        (K.WITH, "#0.val Gt px", 1)]
    assert [t.nodes[t.sites[i].target].path for i in (0, 1)] == ["a1", "#0"]


def test_a_with_on_one_action_alone_is_still_a_cone():
    """It holds at that traversal only, so the type's own solve cannot
    carry it."""
    t = _tree("""\
component pss_top {
    action A { rand bit[4] val; }
    action T { activity { do A with { val > 3; }; } }
}
""")
    cone, = t.cones
    assert [t.nodes[n].path for n in cone.nodes] == ["#0"]
    assert _cons(t, cone) == [(K.WITH, "#0.val Gt 3")]


def test_an_activity_constraint_is_tagged_with_its_scope():
    t = _tree("""\
component pss_top {
    action A { rand bit[4] val; }
    action T { A a, b;
        activity { a; sel: select { { b; constraint { a.val < b.val; } } { } } } }
}
""")
    cone, = t.cones
    c, = cone.constraints
    assert c.kind == K.ACTIVITY and _r(t, c.constraint.expr) == "a.val Lt b.val"
    # A branch's `{...}` is the branch's body itself.
    assert t.scopes[c.scope].kind == SC.ScopeKind.SELECT_BRANCH


def test_a_non_rand_value_a_constraint_reads_is_a_pinned_variable():
    t = _tree("""\
component pss_top {
    action A { rand bit[4] val; bit[4] lim; }
    action T { A a; bit[4] cap; activity { a; } constraint a.val < cap; }
}
""")
    cone, = t.cones
    assert [(v.name, v.rand) for v in cone.vars] == [("cap", False), ("a.val", True)]


def test_an_array_element_and_a_struct_attribute_resolve():
    t = _tree("""\
component pss_top {
    struct S { rand bit[4] f; }
    action A { rand S s; }
    action T { A arr[2]; activity { arr[0]; arr[1]; }
               constraint arr[0].s.f < arr[1].s.f; }
}
""")
    cone, = t.cones
    assert _cons(t, cone) == [(K.TYPE, "arr[0].s.f Lt arr[1].s.f")]


def test_a_reference_the_tree_cannot_resolve_is_left_for_the_backend():
    """`comp` is P1.5: the reference stays as written, and bc refuses it."""
    t = _tree("""\
component pss_top {
    bit[4] lim;
    action A { rand bit[4] val; constraint val < comp.lim; }
    action T { A a; activity { a; } }
}
""")
    assert t.cones == []      # it touches only `a`: a's own solve refuses it
