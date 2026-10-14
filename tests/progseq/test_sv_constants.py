"""`sv/constants.py`: how op-model-sv names a folded PSS constant.

The WB DMA model has one constant per name; these hold the rules a model
with more needs -- two PSS packages declaring the same name, a constant
named like an enum item or an SV keyword, and an enum the package does not
declare -- on the table directly, and end to end on a small model.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess

from zuspec.ir.core import DataTypeArray, DataTypeEnum, ExprConstant

from pssc import driver
from pssc.targets.sv.constants import SvConstants

from .sv_text import sv_package_text


def _final(enums=(), known=(), refs=()):
    rec = SvConstants(enums, known)
    for c in refs:
        rec.name_of(c)
    return rec.final()


def _c(ref, value=1, width=32, signed=True):
    return ExprConstant(value=value, width=width, signed=signed, ref=ref)


def test_a_constant_keeps_its_pss_name():
    t = _final(refs=[_c("p::N", 4)])
    assert t.name_of(_c("p::N", 4)) == "N"
    assert t.declarations() == ["  localparam int N = 4;  // p::N"]


def test_two_constants_of_one_name_are_both_qualified():
    t = _final(refs=[_c("a_pkg::N", 1), _c("b_pkg::N", 2), _c("a_pkg::M")])
    assert t.name_of(_c("a_pkg::N")) == "a_pkg_N"
    assert t.name_of(_c("b_pkg::N")) == "b_pkg_N"
    assert t.name_of(_c("a_pkg::M")) == "M"


def test_a_constant_named_like_a_declared_enum_item_is_qualified():
    e = DataTypeEnum(name="e_t", items={"N": 0})
    t = _final(enums=[e], refs=[_c("p::N")])
    assert t.name_of(_c("p::N")) == "p_N"
    assert t.name_of(ExprConstant(value=0, ref="p::e_t::N")) == "N"


def test_an_sv_keyword_is_renamed():
    t = _final(refs=[_c("p::int")])
    assert t.name_of(_c("p::int")) == "int_"


def test_an_item_of_an_undeclared_enum_stays_a_number():
    e = DataTypeEnum(name="std_e", items={"LOW": 1})
    t = _final(known=[e], refs=[ExprConstant(value=1, ref="std_pkg::std_e::LOW")])
    assert t.name_of(ExprConstant(value=1, ref="std_pkg::std_e::LOW")) is None
    assert t.declarations() == []


def test_a_literal_is_its_value():
    assert _final().name_of(ExprConstant(value=3)) is None


def test_an_array_size_alone_declares_its_constant():
    a = DataTypeArray(size=4, size_ref="p::N")
    rec = SvConstants()
    rec.size_name(a)
    t = rec.final()
    assert t.size_name(a) == "N"
    assert t.declarations() == ["  localparam int N = 4;  // p::N"]


def test_typed_declarations():
    t = _final(refs=[_c("p::A", 0x1234, 64, False), _c("p::B", -3, 8, True),
                     ExprConstant(value=True, ref="p::C")])
    assert t.declarations() == [
        "  localparam bit [63:0]       A = 64'h1234;  // p::A",
        "  localparam bit signed [7:0] B = -8'sd3;  // p::B",
        "  localparam bit              C = 1'b1;  // p::C",
    ]


_TWO_PKGS = """
package a_pkg { static const bit[16] N = 0x1000; }
package b_pkg { static const int N = 3; static const int M = -2; }
component top_c {
    bit[16] x;
    int     arr[b_pkg::N];
    exec init_down { x = a_pkg::N; }
    target function int f(int v) { return v * b_pkg::N + b_pkg::M; }
    export target function f;
}
"""


def test_end_to_end(tmp_path):
    src = tmp_path / "m.pss"
    src.write_text(_TWO_PKGS)
    out = tmp_path / "out"
    ns = argparse.Namespace(progseq_root="top_c", progseq_package="top_pkg",
                            output_dir=str(out))
    driver.compile([str(src)], target="op-model-sv", opts=ns)
    sv = sv_package_text(out / "top_pkg.sv")
    assert "localparam bit [15:0] a_pkg_N = 16'h1000;" in sv
    assert "localparam int        b_pkg_N = 3;" in sv
    assert "localparam int        M       = -2;" in sv
    assert "int arr[b_pkg_N];" in sv
    assert "x = a_pkg_N;" in sv
    assert "status = (v * b_pkg_N) + M;" in sv
    if shutil.which("verilator"):
        r = subprocess.run(
            ["verilator", "--lint-only", "-sv", "--timing", f"-I{out}",
             str(out / "pssc_reg_pkg.sv"), str(out / "top_pkg.sv")],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
