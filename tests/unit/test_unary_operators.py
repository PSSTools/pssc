"""Unary operators must survive to the IR (pssparser D5 -- fixed).

They used not to. `!x` reached pssc as `x`, and so did `-x` and `~x`: the
parser's `visitExpression` built the operand of a unary expression and then
constructed nothing, so the operator was discarded with no diagnostic at any
severity. See `docs/design/pssparser-defects-2026-08-02.md`, D5.

This was the most damaging defect in the toolchain, because the output is not
wrong-looking -- it is *inverted*. Five operations in the WB DMA operation model
are guarded by `!`, and their generated SV compiled, linted clean, and did the
opposite of what the model says.

Each test asserts the *operator*, not just the presence of an `ExprUnary`.
Behind D5 sat a second defect that only a node-shape assertion would miss:
`_map_unaryop` assumed the ordinals `0=!  1=-  2=+  3=~` while the parser's
`ExprUnaryOp` declares `Plus, Minus, LogNot, BitNeg, ...`, so `!` and `+` were
transposed. That bug was unreachable while D5 hid it, and would have shipped
the same inversion by another route the moment D5 was fixed.
"""
import pytest

from pssc import Parser
from pssc.ast2ir import AstToIrTranslator
from zuspec.dataclasses import ir


def translate(pss_code: str):
    parser = Parser()
    parser.parses([("test.pss", pss_code)])
    return AstToIrTranslator(debug=False).translate(parser.link())


def _body(ctx, comp="c_c", fn="g"):
    return next(f for f in ctx.type_map[comp].functions if f.name == fn).body


_MODEL = """
component c_c {
    bit f;
    int n;
    function void g() {
        bit x;
        x = !f;
        n = -n;
        x = ~f;
    }
}
"""

@pytest.mark.parametrize("index,op", [(1, "Not"), (2, "USub"), (3, "Invert")])
def test_unary_operator_survives_translation(index, op):
    stmt = _body(translate(_MODEL))[index]
    assert isinstance(stmt.value, ir.ExprUnary), (
        f"expected a unary {op}; got {type(stmt.value).__name__} -- "
        f"the operator was dropped, so this statement now means its opposite")
    assert stmt.value.op is getattr(ir.UnaryOp, op), (
        f"expected {op}; got {stmt.value.op.name} -- the operator survived but "
        f"was mis-mapped, which for Not/UAdd means the opposite of the model")


def test_unary_plus_is_not_a_negation():
    """`+x` and `!x` are the two ordinals `_map_unaryop` used to transpose."""
    ctx = translate("""
        component c_c {
            int n;
            function void g() { int y; y = +n; }
        }
    """)
    stmt = _body(ctx)[1]
    assert stmt.value.op is ir.UnaryOp.UAdd, (
        f"`+n` mapped to {stmt.value.op.name}")


def test_negated_guard_is_not_the_guard():
    """The shape that actually bites: a capability guard reads back inverted.

    Asserted separately from the operator test because this is the consequence
    a reader needs to see -- `if (!caps) return;` becoming `if (caps) return;`
    is a behaviour change, not a representation detail.
    """
    ctx = translate("""
        component c_c {
            bit caps;
            function void g() { if (!caps) { return; } }
        }
    """)
    test = _body(ctx)[0].test
    assert isinstance(test, ir.ExprUnary), (
        "the guard lost its negation: the generated operation now declines to "
        "run exactly when it should run")
    assert test.op is ir.UnaryOp.Not, (
        f"the guard's negation became {test.op.name}: the generated operation "
        f"now declines to run exactly when it should run")
