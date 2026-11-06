"""A package-scope `const` folds to its own value, whatever else shares its name.

    package opt_pkg {
      extend enum opt_e { OPT_B }
      const int OPT_B = 0;
    }

pssparser links an unqualified `OPT_B` to the constant. pssc folded only a
`static const` through the linker, so for this one it fell back to a lookup by
NAME across every enum, found the item, and folded its ordinal: wrong code, no
diagnostic. At package scope there is no instance, so `const` and `static
const` are one thing (ruling R-Q1), and what the linker resolved is what is
folded; a name lookup never overrides it (R-Q2).
"""
import pytest

from pssc import Parser
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core import ExprConstant

PKGS = """
package opt_pkg {
  enum opt_e { OPT_A }
  extend enum opt_e { OPT_B }
  %(static)sconst int OPT_B = 0;
  %(static)sconst bit[8] OPT_W = 0x41;
}
package other_pkg { %(static)sconst int OPT_B = 9; }
"""


def body(stmts: str, static: bool = False, imports="import opt_pkg::*;"):
    """`f`'s body in the IR, `stmts` being its PSS body."""
    src = (PKGS % {"static": "static " if static else ""}
           + imports
           + "\ncomponent pss_top { target function int f(int mode) { "
           + stmts + " } }")
    p = Parser()
    p.parses([("t.pss", src)])
    ctx = AstToIrTranslator(debug=False).translate(p.link())
    assert not ctx.errors, ctx.errors
    fn, = ctx.type_map["pss_top"].functions
    return fn.body


def returns(stmts: str, static: bool = False):
    """What `f`'s last `return` translates to."""
    return body(stmts, static)[-1].value


def _const(e):
    assert isinstance(e, ExprConstant), e
    return e.value, e.ref, e.width, e.signed


def test_an_unqualified_reference_folds_the_constant_not_the_enum_item():
    assert _const(returns("return OPT_B;")) == (0, "opt_pkg::OPT_B", 32, True)


def test_in_a_condition_too():
    """The reported shape: `mode != 0 && OPT_B == 0` read `... && 1 == 0`."""
    test = body("if (mode != 0 && OPT_B == 0) { return 2; } return 3;")[0].test
    assert _const(test.rhs.lhs)[:2] == (0, "opt_pkg::OPT_B")


def test_a_qualified_reference_folds_the_same():
    assert _const(returns("return opt_pkg::OPT_B;")) == (
        0, "opt_pkg::OPT_B", 32, True)


def test_the_same_name_in_another_package_is_its_own():
    assert _const(returns("return other_pkg::OPT_B;")) == (
        9, "other_pkg::OPT_B", 32, True)


def test_the_enum_item_is_still_the_enum_item():
    cast = returns("return (int)opt_e::OPT_B;")
    assert (cast.value.value, cast.value.ref) == (1, "opt_pkg::opt_e::OPT_B")


def test_a_local_shadows_the_constant():
    e = returns("int OPT_B = 7; return OPT_B;")
    assert type(e).__name__ == "ExprRefLocal" and e.name == "OPT_B"


def test_the_declared_type_is_kept():
    assert _const(returns("return OPT_W;")) == (0x41, "opt_pkg::OPT_W", 8, False)


@pytest.mark.parametrize("body", ["return OPT_B;", "return opt_pkg::OPT_B;",
                                  "return other_pkg::OPT_B;", "return OPT_W;"])
def test_static_and_non_static_give_the_same_ir(body):
    a, b = returns(body), returns(body, static=True)
    assert _const(a) == _const(b)


def test_a_qualified_zero_is_not_another_packages_bare_name():
    """`_fold_const_expr`'s by-name fallback read `const_map.get('q::Z') or
    const_map.get('Z')`: a 0 is falsy, so it fell through to whichever `Z`
    was registered last (here p's). The linker answers first, so this held
    already; it pins the fallback too."""
    p = Parser()
    p.parses([("t.pss", """
        package q { const int Z = 0; }
        package p { const int Z = 4; }
        component s_c {}
        component c_c { s_c a[q::Z]; array<int, q::Z> b; s_c c[p::Z]; }
    """)])
    ctx = AstToIrTranslator(debug=False).translate(p.link())
    assert not ctx.errors, ctx.errors
    sizes = {f.name: f.datatype.size for f in ctx.type_map["c_c"].fields}
    assert sizes == {"a": 0, "b": 0, "c": 4}
