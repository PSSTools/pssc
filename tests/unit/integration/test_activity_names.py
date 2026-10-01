"""Whose name is it: `with` blocks, initializers, activity declarations (P1.1).

* S5: in ``b1 with { x < px; }`` both names used to become ``self.<name>``,
  so nothing below could tell the traversed action's ``x`` from the
  enclosing action's ``px``. The linker records which it found (an
  ``ElemKind_Inline`` step for a name found in the traversed action), and
  ast2ir now roots that one at ``TypeExprRefTraversed`` (LRM 13.1.4).
* ``this.q`` was ``self.this.q``, everywhere; it is ``self.q``.
* Initializers (11.3.1) were a P0 refusal, and on a handle declared in an
  action body they were dropped outright. A traversal now carries its
  declaration's, then its own.
* A declaration in an activity block is an ``ActivityFieldDecl``. A handle
  declared there used to be skipped and a data field refused.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir import core as ir
from zuspec.ir.core import activity as A
from zuspec.ir.core.xf import PSSToScenarioPass
from zuspec.ir.core.xf.validate import UnsupportedConstructError

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _translate(src: str):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)


def _r(e):
    """A compact rendering: `<T>` is the traversed action."""
    if isinstance(e, ir.TypeExprRefSelf):
        return "self"
    if isinstance(e, ir.TypeExprRefTraversed):
        return "<T>"
    if isinstance(e, ir.ExprAttribute):
        return f"{_r(e.value)}.{e.attr}"
    if isinstance(e, ir.ExprConstant):
        return str(e.value)
    if isinstance(e, ir.ExprBin):
        return f"{_r(e.lhs)} {e.op.name} {_r(e.rhs)}"
    return type(e).__name__


def _activity(body: str, decls: str = ""):
    ctx = _translate("""\
component pss_top {
    struct S { rand bit[4] f; }
    action B { rand bit[4] x; rand bit[4] px; rand S s; }
    action A {
        rand bit[4] px; rand bit[4] q;
        B b1;
        %s
        activity {
            %s
        }
    }
}
""" % (decls, body))
    assert not ctx.errors, ctx.errors
    return ctx.type_map["pss_top::A"].activity_ir.stmts


# -- S5 ----------------------------------------------------------------------

@pytest.mark.parametrize("stmt", ["b1 with { x < q; };", "do B with { x < q; };"])
def test_a_name_of_the_traversed_action_is_rooted_at_it(stmt):
    trav, = _activity(stmt)
    assert [_r(c) for c in trav.inline_constraints] == ["<T>.x Lt self.q"]


def test_the_traversed_action_wins_a_name_both_declare():
    """13.1.4: child first. `px` is declared by B and by A."""
    trav, = _activity("b1 with { px == 1; };")
    assert [_r(c) for c in trav.inline_constraints] == ["<T>.px Eq 1"]


def test_this_reaches_the_enclosing_action():
    trav, = _activity("b1 with { x < this.px; };")
    assert [_r(c) for c in trav.inline_constraints] == ["<T>.x Lt self.px"]


def test_this_is_self_outside_a_with_too():
    ctx = _translate("""\
component pss_top {
    action A { rand bit[4] q; exec post_solve { this.q = 3; } }
}
""")
    assert not ctx.errors, ctx.errors
    fn, = [f for f in ctx.type_map["pss_top::A"].functions
           if (f.metadata or {}).get("exec_kind") == "post_solve"]
    assert _r(fn.body[0].targets[0]) == "self.q"


def test_comp_in_a_with_still_steers():
    trav, = _activity("b1 with { comp == this.comp; };")
    assert trav.comp_expr is not None and trav.inline_constraints == []


# -- initializers ------------------------------------------------------------

def _inits(trav):
    return [f"{_r(t)}={_r(v)}" for t, v in trav.initializers]


def test_a_traversal_carries_its_initializers():
    trav, = _activity("b1 {.x = q, .s.f = 3};")
    assert _inits(trav) == ["<T>.x=self.q", "<T>.s.f=3"]


def test_a_type_traversal_carries_its_initializers():
    trav, = _activity("do B {.x = 4};")
    assert _inits(trav) == ["<T>.x=4"]


def test_the_declarations_initializers_come_first():
    """11.3.1 b i-ii. On a handle declared in an action body they used to be
    dropped."""
    trav, = _activity("b2 {.x = 2};", decls="B b2 {.x = 1, .px = q};")
    assert _inits(trav) == ["<T>.x=1", "<T>.px=self.q", "<T>.x=2"]


def test_a_block_handles_initializers_come_first():
    decl, trav = _activity("B b3 {.x = 1}; b3 {.px = 2};")
    assert isinstance(decl, A.ActivityFieldDecl)
    assert _inits(trav) == ["<T>.x=1", "<T>.px=2"]


def test_the_pass_refuses_initializers_until_p1_4():
    ctx = _translate("""\
component pss_top {
    action B { rand bit[4] x; }
    action A { activity { do B {.x = 1}; } }
}
""")
    with pytest.raises(UnsupportedConstructError, match="traversal initializers"):
        PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)


# -- declarations in an activity block ---------------------------------------

def test_an_activity_data_field_is_a_declaration():
    decl, trav = _activity("action bit[4] n; n;")
    assert isinstance(decl, A.ActivityFieldDecl)
    assert decl.field.name == "n" and decl.type_qname is None
    assert decl.field.action_qualified


def test_an_action_qualified_field_in_an_action_body_says_so():
    """Ex 173. It read as a plain non-rand field."""
    ctx = _translate("""\
component pss_top {
    action A { action bit[4] a_bit; bit[4] plain; }
}
""")
    assert not ctx.errors, ctx.errors
    fields = {f.name: f for f in ctx.type_map["pss_top::A"].fields}
    assert fields["a_bit"].action_qualified
    assert not fields["plain"].action_qualified


def test_the_pass_refuses_an_activity_data_field_until_p1_4():
    ctx = _translate("""\
component pss_top {
    action B { }
    action A { activity { action bit[4] n; do B; } }
}
""")
    with pytest.raises(UnsupportedConstructError,
                       match="data field 'n' declared in an activity block"):
        PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)


def test_a_block_handle_declaration_lowers_to_nothing():
    """The traversal names the type; the declaration has no op until P1.2."""
    ctx = _translate("""\
component pss_top {
    action B { }
    action A { activity { B bb; bb; } }
}
""")
    PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)


def test_a_label_on_a_replicate_statement_is_refused():
    """ActivityReplicate.label is the `R[]:` iteration label; a statement
    label has no slot."""
    ctx = _translate("""\
component pss_top {
    action B { }
    action A { activity { L: replicate (2) do B; } }
}
""")
    assert any(e.startswith("line 3: a label on a replicate statement")
               for e in ctx.errors), ctx.errors


def test_an_iteration_label_is_carried():
    stmt, = _activity("replicate (2) R[]: do B;")
    assert isinstance(stmt, A.ActivityReplicate) and stmt.label == "R"
