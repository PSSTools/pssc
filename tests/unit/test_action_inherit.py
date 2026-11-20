"""Functions in actions and action inheritance (P2.11b2,
src/pssc/targets/action_inherit.py): a prototype is an abstract function,
a derived action gets the functions it inherits, `super.f()` reaches a
private copy of the base's `f`, and an unimplemented abstract function in a
concrete action is a located error."""
import os
import tempfile

import zuspec.ir.core as ir

import pssc
from pssc.ast2ir import AstToIrTranslator
from pssc.targets import action_inherit as ai


def _translate(src):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)


def _fns(ctx, name):
    return {f.name: f for f in ctx.type_map[f"pss_top::{name}"].functions
            if ai.is_method(f)}


TEMPLATE = """
component pss_top {
  abstract action base_a {
    rand int in [0..7] ch;
    function void arrange() { ch = 2; }
    function void act();
    exec body { arrange(); act(); }
  }
  action d_a : base_a { function void act() { ch = 1; } }
}
"""


def test_a_prototype_in_an_action_is_abstract():
    ctx = _translate(TEMPLATE)
    assert not ctx.errors, ctx.errors
    act = _fns(ctx, "base_a")["act"]
    assert ai.is_abstract(act) and act.body == []
    assert not ai.is_abstract(_fns(ctx, "base_a")["arrange"])


def test_a_prototype_in_a_component_is_not_recorded():
    ctx = _translate("component pss_top { function void f(); }")
    assert not ctx.errors, ctx.errors
    assert not any(f.name == "f" for f in ctx.type_map["pss_top"].functions)


def test_a_derived_action_inherits_and_overrides():
    ctx = _translate(TEMPLATE)
    fns = _fns(ctx, "d_a")
    assert set(fns) == {"arrange", "act"}
    assert fns["arrange"].metadata[ai.INHERITED_FROM] == "base_a"
    assert not ai.is_abstract(fns["act"])           # the override wins
    assert ai.INHERITED_FROM not in fns["act"].metadata
    # The base keeps only its own; its exec body is not copied.
    assert not any(f.metadata.get("exec_kind")
                   for f in ctx.type_map["pss_top::d_a"].functions)


def test_super_call_reaches_a_private_copy():
    ctx = _translate("""
component pss_top {
  action base_a { rand int x; function void arrange() { x = 2; } }
  action d_a : base_a {
    function void arrange() { super.arrange(); x = 3; }
  }
}
""")
    assert not ctx.errors, ctx.errors
    fns = _fns(ctx, "d_a")
    hidden = "_pss_super_base_a_arrange"
    assert fns[hidden].metadata[ai.SUPER_IMPL] == "base_a::arrange"
    call = fns["arrange"].body[0].expr
    assert isinstance(call.func.value, ir.TypeExprRefSelf)
    assert call.func.attr == hidden
    # As written, for a backend that renders inheritance natively.
    own = ai.declared(ctx, ctx.type_map["pss_top::d_a"])
    assert isinstance(own[0].body[0].expr.func.value, ir.TypeExprRefSuper)


def test_inheritance_goes_through_every_level():
    ctx = _translate("""
component pss_top {
  abstract action a0 { function void f() { } function void g(); }
  abstract action a1 : a0 { }
  action a2 : a1 { function void g() { } }
}
""")
    assert not ctx.errors, ctx.errors
    fns = _fns(ctx, "a2")
    assert set(fns) == {"f", "g"} and not ai.is_abstract(fns["g"])


def test_an_unimplemented_abstract_function_is_a_located_error():
    ctx = _translate("""
component pss_top {
  abstract action base_a {
    function void act();
    exec body { act(); }
  }
  action d_a : base_a { }
}
""")
    assert ctx.errors == [
        "line 7: action 'd_a' does not implement function 'act' "
        "(declared with no body in 'base_a' at line 4)"]


def test_an_abstract_action_may_leave_it_abstract():
    ctx = _translate("""
component pss_top {
  abstract action base_a { function void act(); }
  abstract action mid_a : base_a { }
}
""")
    assert not ctx.errors, ctx.errors


def test_super_call_of_an_abstract_function_is_an_error():
    ctx = _translate("""
component pss_top {
  abstract action base_a { function void act(); }
  action d_a : base_a { function void act() { super.act(); } }
}
""")
    assert len(ctx.errors) == 1
    assert "calls 'super.act()', which has no body in 'base_a'" in ctx.errors[0]
    assert ctx.errors[0].startswith("line 4: ")


def test_actions_without_functions_are_untouched():
    ctx = _translate("""
component pss_top {
  action base_a { rand int x; exec body { x = 1; } }
  action d_a : base_a { }
}
""")
    assert not ctx.errors, ctx.errors
    assert ctx.type_map["pss_top::d_a"].functions == []
    assert ai.declared(ctx, ctx.type_map["pss_top::d_a"]) is None
