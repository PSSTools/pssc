"""What a traversal carries is lowered or refused, never ignored (P1.0, S1-S4).

The P1 survey found four things the scenario pass read past, each producing a
different scenario from the one written:

* S1 ``h[i]``: the index was dropped, so the traversal ran the element TYPE.
  Since P1.2 a constant index names its node; a computed one is refused.
* S2 ``comp == X``: taken out of the ``with`` and ignored, so the action ran
  in whatever instance. On a handle traversal it was not even taken out.
* S3 ``init_bindings`` (Python front end): not bound.
* S4 an ``if``/``foreach`` inside a ``with`` or an activity ``constraint``:
  dropped by ast2ir, because the IR holds those as expressions.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
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


def _activity(ctx, action="pss_top::A"):
    return ctx.type_map[action].activity_ir.stmts


def test_s1_an_array_element_traversal_runs_its_element_node():
    """P1.2: a constant index names a node of the action tree, and the
    traversal carries that node's offset."""
    ctx = _translate("""\
component pss_top {
    action B { }
    action A { B bs[2]; activity { bs[1]; } }
}
""")
    assert not ctx.errors, ctx.errors
    assert _activity(ctx)[0].index is not None
    m = PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)
    inv, = [s for s in m.coroutines["A"].body if type(s).__name__ == "ScInvoke"]
    assert inv.child_base == m.trees["A"].node_at("bs[1]").base


def test_s1_a_computed_array_index_is_refused_by_the_pass():
    ctx = _translate("""\
component pss_top {
    action B { }
    action A { rand bit[1] i; B bs[2]; activity { bs[i]; } }
}
""")
    assert not ctx.errors, ctx.errors
    with pytest.raises(UnsupportedConstructError,
                       match="handle array 'bs' with a computed index"):
        PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)


@pytest.mark.parametrize("activity,cls", [
    ("do B with { comp == this.comp.s0; };", A.ActivityAnonTraversal),
    ("b1 with { comp == this.comp.s0; };", A.ActivityTraversal),
])
def test_s2_comp_steer_is_taken_out_and_refused(activity, cls):
    ctx = _translate("""\
component sub_c { }
component pss_top {
    sub_c s0;
    action B { }
    action A { B b1; activity { %s } }
}
""" % activity)
    assert not ctx.errors, ctx.errors
    trav = _activity(ctx)[0]
    assert isinstance(trav, cls)
    assert trav.comp_expr is not None and trav.inline_constraints == []
    with pytest.raises(UnsupportedConstructError, match="comp =="):
        PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)


def test_s3_init_bindings_are_refused_by_the_pass():
    ctx = _translate("""\
component pss_top {
    action B { }
    action A { activity { do B; } }
}
""")
    _activity(ctx)[0].init_bindings.append(("x", "p", "y"))
    with pytest.raises(UnsupportedConstructError, match="flow bindings"):
        PSSToScenarioPass(root="pss_top", exports=["A"]).lower(ctx)


@pytest.mark.parametrize("stmt,what", [
    ("b1 with { if (n > 1) { x == 2; } };", "inside an inline 'with' constraint"),
    ("b1; constraint { if (n > 1) { b1.x == 2; } }", "inside an activity constraint"),
])
def test_s4_a_structured_constraint_where_an_expression_list_is_kept(stmt, what):
    ctx = _translate("""\
component pss_top {
    action B { rand bit[4] x; }
    action A { rand bit[4] n; B b1; activity { %s } }
}
""" % stmt)
    assert any(e.startswith("line 3: 'if' ") and what in e for e in ctx.errors), ctx.errors
