"""Constants reached by name fold to their values (P1.3).

ast2ir folded a `static const` only when its initializer was a non-bool
literal and a qualified reference only through its name. Anything else became
an attribute of the action -- ``cfg_pkg::REG_SPAN`` as ``self.cfg_pkg.REG_SPAN``
-- which bc refused ("reference through ExprAttribute") and the op-model
targets could not type. A constant is now folded from its initializer, and a
reference follows the linker's target to it.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir import core as ir

from .test_activity_bc_runs import trace

_SRC = """\
import std_pkg::*;
package cfg_pkg {
    static const int     MAX_CH = 4;
    static const bit[64] SPAN   = 0x20 + MAX_CH * 0x20;
    static const bool    HAS_X  = true;
    static const int     NEG    = -7 / 2;
}
package use_pkg { import cfg_pkg::*; static const int TWICE = MAX_CH * 2; }
component pss_top {
    static const int LOCAL = cfg_pkg::MAX_CH + 1;
    action T {
        exec body { message(NONE, "%s", %s); }
    }
}
"""


def _value(expr: str, fmt: str = "%d"):
    return trace(_SRC % (fmt, expr))


@pytest.mark.parametrize("expr,want", [
    ("cfg_pkg::MAX_CH", "4"),
    ("cfg_pkg::SPAN", "160"),
    ("use_pkg::TWICE", "8"),
    ("pss_top::LOCAL", "5"),
    ("cfg_pkg::NEG", "-3"),
])
def test_a_constant_folds(expr, want):
    assert _value(expr) == [want]


def test_a_bool_constant_folds():
    assert _value("cfg_pkg::HAS_X", "%n") == ["true"]


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


def test_the_reference_is_a_constant_not_an_attribute():
    ctx = _translate(_SRC % ("%d", "cfg_pkg::SPAN"))
    assert not ctx.errors, ctx.errors
    body, = [f for f in ctx.type_map["pss_top::T"].functions if f.name == "body"]
    arg = body.body[0].expr.args[2]
    assert isinstance(arg, ir.ExprConstant) and arg.value == 160
