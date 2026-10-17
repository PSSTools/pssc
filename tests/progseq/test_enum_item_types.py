"""A folded enum item is of its enum's type (LRM 7.5), not `int`.

ast2ir folds an item to its value and keeps the item the linker resolved in
`ExprConstant.ref`. `ExprTypes` reads that, so a backend spells an item by
name from the type alone -- rather than each recovering the name its own way,
from the value, which picks the wrong one of two items with one value.
(Two items with one value are an error now -- pssparser PSS134, 7.5.1 e --
so `ALIAS` has a value of its own.)
"""
from __future__ import annotations

from pssc import Parser
from pssc.ast2ir import AstToIrTranslator
from pssc.targets.expr_types import ExprTypes

_MODEL = """
package p {
  enum mode_e { OFF, SLOW = 5, FAST, ALIAS = 9 }
  static const int K = 5;
}
enum e { A, B }
component pss_top {
  function p::mode_e f1() { return p::mode_e::SLOW; }
  function p::mode_e f2() { return p::mode_e::ALIAS; }
  function int f3() { return p::K; }
  function e f4(bool c) { return c ? A : B; }
  function int f5() { return 5; }
}
"""


def _types():
    parser = Parser()
    parser.parses([("t.pss", _MODEL)])
    ctx = AstToIrTranslator().translate(parser.link())
    assert not ctx.errors, ctx.errors
    comp = ctx.type_map["pss_top"]
    out = {}
    for fn in comp.functions:
        ret = fn.body[-1].value
        out[fn.name] = ExprTypes(fn, comp, ctx).type_of(ret)
    return out


def test_an_item_is_of_its_enum():
    t = _types()
    assert t["f1"].kind == "enum"
    assert t["f1"].dtype.name.endswith("mode_e")


def test_a_later_explicit_item_is_still_its_enums():
    assert _types()["f2"].kind == "enum"


def test_a_static_const_and_a_literal_are_integers():
    t = _types()
    assert (t["f3"].kind, t["f3"].width) == ("int", 32)
    assert (t["f5"].kind, t["f5"].width) == ("int", 32)


def test_a_choice_between_items_is_of_their_enum():
    assert _types()["f4"].kind == "enum"
