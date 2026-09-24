"""`super;`, action bases, and statements the front end cannot translate.

Three front-end defects, each of which compiled a model that did less than it
said and reported nothing:

* `super;` in an exec block was dropped (LRM 17.1, 20.1.4). It is now
  `ir.StmtSuper`, and a consumer that cannot resolve it refuses it.
* Any statement kind with no IR form was dropped the same way. It is now a
  translation error naming the kind and its line.
* An action's base was recorded as it was SPELLED (`A`, `B`), not as the
  linker resolved it (`pss_top::A`, or `base_c::B` for an action found in a
  base component), so nothing downstream could find the base.
"""
from __future__ import annotations

import zuspec.ir.core as ir

from pssc import driver


def _translate(tmp_path, text):
    p = tmp_path / "m.pss"
    p.write_text("import std_pkg::*;\n" + text)
    return driver.translate([str(p)])


def _body(ctx, action):
    return next(fn for fn in ctx.type_map[action].functions
                if fn.name == "body").body


def test_super_statement_is_in_the_ir(tmp_path):
    ctx = _translate(tmp_path, """
component pss_top {
  action A { exec body { message(NONE, "A"); } }
  action A1 : A { exec body { super; message(NONE, "A1"); } }
}
""")
    assert ctx.errors == []
    body = _body(ctx, "pss_top::A1")
    assert isinstance(body[0], ir.StmtSuper)
    assert len(body) == 2


def test_action_bases_are_named_as_linked(tmp_path):
    ctx = _translate(tmp_path, """
package p {
  component pbase_c { action P { exec body { } } }
}
component base_c { action B { exec body { } } }
component mid_c : p::pbase_c { }
component pss_top : base_c {
  action A { exec body { } }
  action A1 : A { }
  action C : B { }
}
component other_c : mid_c {
  action Q : P { }
  action Q2 : p::pbase_c::P { }
}
""")
    assert ctx.errors == []
    base = {q: ctx.type_map[q].super.ref_name
            for q in ("pss_top::A1", "pss_top::C", "other_c::Q",
                      "other_c::Q2")}
    assert base == {"pss_top::A1": "pss_top::A",
                    "pss_top::C": "base_c::B",
                    "other_c::Q": "p::pbase_c::P",
                    "other_c::Q2": "p::pbase_c::P"}
    for name in base.values():
        assert name in ctx.type_map


def test_an_untranslatable_statement_is_an_error_not_a_drop(tmp_path):
    ctx = _translate(tmp_path, """
struct s_t { rand bit[8] v; }
component pss_top {
  target function void f() {
    s_t s;
    randomize s;      // line 7: `import std_pkg::*;` is prepended
  }
}
""")
    assert any("ProceduralStmtRandomize" in e and "line 7" in e
               for e in ctx.errors), ctx.errors
