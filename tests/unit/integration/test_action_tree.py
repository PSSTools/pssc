"""The action tree of an exported action (P1.2, design P1-D1).

Every action an activation can run is a node with its own slots in one
flattened object: its type's layout, then its children's subtrees in
declaration order. A type's subtree is the same wherever it is instantiated,
so a traversal's ``ScInvoke.child_base`` -- the child's offset from the
invoking action -- is static. Nothing consumes the tree yet (P1.4 does).
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core import scenario as SC
from zuspec.ir.core.xf import PSSToScenarioPass
from zuspec.ir.core.xf.pss_lower.action_tree import Layouts, build_tree
from zuspec.ir.core.xf.validate import UnsupportedConstructError

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _translate(src: str):
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
    return ctx


def _module(src: str, export: str = "T"):
    return PSSToScenarioPass(root="pss_top", exports=[export]).lower(_translate(src))


def _tree(src: str, export: str = "T") -> SC.ScActionTree:
    """The tree alone: the pass still refuses some of what it holds (a
    labeled replicate, until P1.4)."""
    return build_tree(Layouts(_translate(src).type_map), export, "pss_top::" + export)


_SRC = """\
component pss_top {
    action L { rand bit[4] val; }
    action S1 { L a, b; bit[4] k; activity { a; b; } }
    action T {
        rand bit[4] n; S1 s1; L arr[2];
        activity {
            s1;
            do L;
            arr[1];
            L blk;
            blk;
            repeat (3) { do L; }
            replicate (2) R[]: do L;
            sel: select { do L; do L; }
        }
    }
}
"""


def test_every_action_is_a_node_with_its_own_slots():
    t = _tree(_SRC)
    got = [(n.path, n.type_qname.rsplit("::", 1)[-1], n.base, n.size) for n in t.nodes]
    # T's own slots: n, then one opaque slot per handle attribute (s1, arr).
    assert got == [
        ("", "T", 0, 3),
        ("s1", "S1", 3, 3), ("s1.a", "L", 6, 1), ("s1.b", "L", 7, 1),
        ("arr[0]", "L", 8, 1), ("arr[1]", "L", 9, 1),
        ("#0", "L", 10, 1),
        ("blk", "L", 11, 1),
        ("#1", "L", 12, 1),                       # the repeat body: ONE node
        ("R[0].#0", "L", 13, 1), ("R[1].#0", "L", 14, 1),
        ("#2", "L", 15, 1), ("#3", "L", 16, 1),   # one per select branch
    ]
    assert t.size == 17


def test_child_base_is_the_childs_offset_from_the_invoker():
    """Static per type: S1 lays out the same as the root and nested in T."""
    m = _module(_SRC.replace("replicate (2) R[]: do L;", ""))
    t = m.trees["T"]
    by_path = {n.path: n for n in t.nodes}

    def invokes(coro):
        out = []

        def walk(stmts):
            for s in stmts:
                if isinstance(s, SC.ScInvoke):
                    out.append(s)
                for attr in ("body", "then_body", "else_body", "branches"):
                    sub = getattr(s, attr, None)
                    if isinstance(sub, list):
                        walk(sub)
                for br in getattr(s, "branches", None) or []:
                    walk(getattr(br, "body", []) or [])
        walk(coro.body)
        return out

    s1 = by_path["s1"]
    assert [i.child_base for i in invokes(m.coroutines["S1"])] == [
        by_path["s1.a"].base - s1.base, by_path["s1.b"].base - s1.base]
    t_invokes = {i.inst: i.child_base for i in invokes(m.coroutines["T"])}
    assert t_invokes["s1"] == s1.base
    assert t_invokes["arr"] == by_path["arr[1]"].base
    assert t_invokes["blk"] == by_path["blk"].base


def test_handle_reset_scopes():
    """13.4.8: an attribute handle is reset on entry to its parent's activity;
    a block-declared one, on entry to its block."""
    t = _tree(_SRC)
    by_path = {n.path: n for n in t.nodes}
    root_activity = t.scopes[by_path["s1"].decl_scope]
    assert root_activity.kind == SC.ScopeKind.ACTIVITY and root_activity.node == 0
    loop = t.scopes[by_path["#1"].decl_scope]
    assert loop.kind == SC.ScopeKind.LOOP_BODY
    assert [t.scopes[by_path[p].decl_scope].kind for p in ("R[0].#0", "#2")] == [
        SC.ScopeKind.REPLICATE_ITER, SC.ScopeKind.SELECT_BRANCH]
    # s1's own children are reset by S1's activity, inside T's.
    s1_act = t.scopes[by_path["s1.a"].decl_scope]
    assert s1_act.kind == SC.ScopeKind.ACTIVITY and s1_act.node == by_path["s1"].id
    assert s1_act.parent == by_path["s1"].decl_scope


def test_sites_name_their_nodes():
    t = _tree(_SRC)
    paths = {n.id: n.path for n in t.nodes}
    assert [(paths[s.owner], paths[s.target]) for s in t.sites] == [
        ("s1", "s1.a"), ("s1", "s1.b"),
        ("", "s1"), ("", "#0"), ("", "arr[1]"), ("", "blk"), ("", "#1"),
        ("", "R[0].#0"), ("", "R[1].#0"), ("", "#2"), ("", "#3")]


@pytest.mark.parametrize("body,chain", [
    ("action T { activity { do T; } }", "pss_top::T -> pss_top::T"),
    ("action U { T t; activity { t; } } action T { activity { do U; } }",
     "pss_top::T -> pss_top::U -> pss_top::T"),
])
def test_an_action_that_traverses_itself_is_refused(body, chain):
    ctx = _translate("component pss_top { %s }" % body)
    with pytest.raises(UnsupportedConstructError, match="traverses itself") as e:
        PSSToScenarioPass(root="pss_top", exports=["T"]).lower(ctx)
    assert chain in str(e.value)


def test_a_labeled_replicate_needs_a_constant_count():
    ctx = _translate("""\
component pss_top {
    action L { }
    action T { rand bit[2] k; activity { replicate (k) R[]: do L; } }
}
""")
    with pytest.raises(UnsupportedConstructError,
                       match=r"iteration label \(R\[\]\) needs a constant count"):
        PSSToScenarioPass(root="pss_top", exports=["T"]).lower(ctx)


def test_the_pass_builds_a_tree_per_export():
    m = _module(_SRC.replace("replicate (2) R[]: do L;", ""))
    assert list(m.trees) == ["T"] and m.trees["T"].type_qname == "pss_top::T"


def test_a_labeled_replicate_is_refused_by_the_pass_until_p1_4():
    ctx = _translate(_SRC)
    with pytest.raises(UnsupportedConstructError, match=r"\(R\[\]\) is not supported yet"):
        PSSToScenarioPass(root="pss_top", exports=["T"]).lower(ctx)


def test_an_atomic_export_is_a_one_node_tree():
    t = _module("component pss_top { action T { rand bit[4] x; exec body { } } }").trees["T"]
    assert [(n.path, n.size) for n in t.nodes] == [("", 1)] and t.cones == []
