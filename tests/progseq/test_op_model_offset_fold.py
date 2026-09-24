"""Register-group offset functions are EVALUATED, never emitted.

`get_offset_of_instance[_array]` is classified `FuncKind.REG_OFFSET` -- the
enum's own comment says "evaluated, not emitted" -- but nothing consulted that
classification from the expression emitter, so a model that called one had the
call copied verbatim into the generated package. The generated register-group
class declares no such method, so the output did not compile:

    %Error: wb_dma_c_pkg.sv:423:47: Class method 'get_offset_of_instance_array'
            not found in class 'wb_dma_regs_c'

It shipped because nothing exercised the shape. The flat example
(`src/pssc/testing/models`) has an EMPTY `ctor`; `examples/op_model`
has a real one, but it restates the map as constants rather than asking the
register group. `data/offset_fold.pss` is the missing case, kept small and
separate rather than folded into either example so the other suites' golden text
does not move.

Two properties are asserted, and the second is the one with teeth:

  * the fold produces the right arithmetic, and it AGREES with what the
    register-model backend independently folded for the same array; and
  * the package ELABORATES. A package that is merely compiled never has its
    class bodies elaborated -- the broken output above lints clean on its own,
    which is exactly why CI stayed green. Only a top that instantiates the root
    class forces the check.

Every failure mode is an error rather than a fallback, because the PSS function
answers an unknown instance with -1, and -1 in an address computation wraps to a
wild address that nothing downstream will complain about. See
docs/design/lowering-call-legality.md §7.1.
"""
import argparse
import os
import re
import shutil
import subprocess

import pytest

from pssc import driver
from pssc.targets.progseq_model import (OffsetFoldError, array_base_stride,
                                        scalar_offset)

_MODEL = os.path.join(os.path.dirname(__file__), "data", "offset_fold.pss")


def _gen(out_dir, sources=None, root="fold_top_c"):
    ns = argparse.Namespace(progseq_root=root, progseq_package="fold_top_pkg",
                            output_dir=str(out_dir))
    driver.compile(sources or [_MODEL], target="sv-progseq", opts=ns)
    with open(os.path.join(str(out_dir), "fold_top_pkg.sv")) as fp:
        return fp.read()


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    out = tmp_path_factory.mktemp("offset_fold")
    return out, _gen(out)


# --- the fold ---------------------------------------------------------------

def test_offset_call_is_not_emitted(gen):
    """The defect, stated directly: the method name must not reach the output."""
    _, sv = gen
    assert "get_offset_of_instance" not in sv


def test_array_offset_folds_to_affine_expression(gen):
    """A run-time index folds to `base + stride*i`, at addr_handle_t width.

    The width matters: the original emission produced a 32-bit result added to a
    64-bit base, which Verilator reports as WIDTHEXPAND alongside the error.
    """
    _, sv = gen
    assert "m_bank[i] = new(this, i, (base + (64'h10 + 64'h10 * i)));" in sv


def test_fold_agrees_with_the_register_model(gen):
    """The constructor's arithmetic and the register group's own placement are
    two independent folds of ONE declaration. If they can disagree, the map is
    no longer stated once."""
    _, sv = gen
    group = re.search(r"bank\[i\] = new\(bus, base \+ 64'h(\w+) \+ i \* 64'h(\w+)\);", sv)
    ctor = re.search(r"m_bank\[i\] = new\(this, i, \(base \+ \(64'h(\w+) \+ 64'h(\w+) \* i\)\)\);", sv)
    assert group and ctor, sv
    assert group.groups() == ctor.groups()


@pytest.mark.skipif(not shutil.which("verilator"), reason="verilator not on PATH")
def test_generated_package_elaborates(gen, tmp_path):
    """Instantiate the root class. Compiling the package alone is NOT this test:
    the broken output this suite exists for passed a package-only lint."""
    out, _ = gen
    top = tmp_path / "elab_top.sv"
    top.write_text("""
module elab_top;
  import pssc_reg_pkg::*;
  import fold_top_pkg::*;
  class imp implements fold_top_c_import_if;
    virtual task write8 (addr_handle_t a, bit[7:0] d); endtask
    virtual task read8  (addr_handle_t a, output bit[7:0] d); d='0; endtask
    virtual task write16(addr_handle_t a, bit[15:0] d); endtask
    virtual task read16 (addr_handle_t a, output bit[15:0] d); d='0; endtask
    virtual task write32(addr_handle_t a, bit[31:0] d); endtask
    virtual task read32 (addr_handle_t a, output bit[31:0] d); d='0; endtask
    virtual task write64(addr_handle_t a, bit[63:0] d); endtask
    virtual task read64 (addr_handle_t a, output bit[63:0] d); d='0; endtask
  endclass
  initial begin
    imp                              i;
    fold_top_c #(fold_top_c_import_if) dut;
    bit [31:0]                       v;
    i   = new();
    dut = new(i, 64'h0);
    dut.set_gcsr(32'h1);
    dut.bank(2).read_stat(v);
  end
endmodule
""")
    r = subprocess.run(
        ["verilator", "--lint-only", "-sv", "--timing", "-Wno-WIDTHEXPAND",
         str(out / "pssc_reg_pkg.sv"), str(out / "fold_top_pkg.sv"), str(top),
         "--top-module", "elab_top"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --- the failure modes ------------------------------------------------------
#
# Driven against the evaluator directly: building a PSS model per case would
# test the front end more than the fold, and several of these are not spellable
# in legal PSS at all.

class _Fn:
    def __init__(self, name, body=None, args=()):
        self.name, self.body = name, body
        self.args = argparse.Namespace(
            args=[argparse.Namespace(arg=a) for a in args])


class _Group:
    def __init__(self, name="g", functions=(), offset_map=None):
        self.name, self.functions = name, list(functions)
        self.offset_map = offset_map or {}


def test_f3_unknown_array_instance_is_an_error():
    """The load-bearing case: a renamed instance. PSS answers -1; folding -1
    would bind every element to a wild address."""
    with pytest.raises(OffsetFoldError, match="no instance array named 'banks'"):
        array_base_stride(_arm_group("affine"), "banks")


def test_missing_offset_function_is_an_error():
    with pytest.raises(OffsetFoldError, match="declares no get_offset_of_instance_array"):
        array_base_stride(_Group(), "bank")


def test_non_match_body_is_an_error():
    """An offset function that is not a match over the name cannot be evaluated
    at build time, so it must not be emitted as a call either."""
    with pytest.raises(OffsetFoldError, match="not a match over the instance name"):
        array_base_stride(_Group(functions=[
            _Fn("get_offset_of_instance_array", body=None, args=("name", "index"))]),
            "bank")


def test_f5_unknown_scalar_instance_names_the_group_and_instance():
    """It used to escape as a bare KeyError -- no group, no instance, no hint."""
    with pytest.raises(OffsetFoldError, match="declares no instance named 'nope'"):
        scalar_offset(_Group(name="pkg::top_regs_c", offset_map={"gcsr": 0}), "nope")


def test_scalar_offset_resolves():
    assert scalar_offset(_Group(offset_map={"gcsr": 4}), "gcsr") == 4


def test_scalar_offset_comes_from_the_groups_own_function():
    """The model's `get_offset_of_instance` wins over the front end's
    sequential `offset_map`, which put a register placed at 0x8 at 0x4."""
    import zuspec.ir.core as ir
    case = argparse.Namespace(
        pattern=ir.PatternValue(value=ir.ExprConstant(value="stat")),
        body=[argparse.Namespace(value=ir.ExprConstant(value=0x8))])
    match = argparse.Namespace(cases=[case])
    match.__class__ = type("StmtMatch", (argparse.Namespace,), {})
    g = _Group(functions=[_Fn("get_offset_of_instance", body=[match],
                              args=("name",))],
               offset_map={"stat": 4})
    assert scalar_offset(g, "stat") == 0x8
    with pytest.raises(OffsetFoldError, match="declares no instance named 'ctrl'"):
        scalar_offset(g, "ctrl")


# --- the soundness fix ------------------------------------------------------

def _arm_group(expr_src):
    """A group whose get_offset_of_instance_array has one arm returning
    ``expr_src``, built from the real IR node types."""
    import zuspec.ir.core as ir
    idx = ir.ExprRefLocal(name="index")

    def c(v):
        return ir.ExprConstant(value=v)

    exprs = {
        "affine":     ir.ExprBin(op=ir.BinOp.Add, lhs=c(0x20),
                                 rhs=ir.ExprBin(op=ir.BinOp.Mult, lhs=idx, rhs=c(0x20))),
        "quadratic":  ir.ExprBin(op=ir.BinOp.Add, lhs=c(0x20),
                                 rhs=ir.ExprBin(op=ir.BinOp.Mult,
                                                lhs=ir.ExprBin(op=ir.BinOp.Mult,
                                                               lhs=idx, rhs=idx),
                                                rhs=c(0x20))),
        "shift":      ir.ExprBin(op=ir.BinOp.LShift, lhs=idx, rhs=c(5)),
    }
    case = argparse.Namespace(pattern=ir.PatternValue(value=c("bank")),
                              body=[argparse.Namespace(value=exprs[expr_src])])
    match = argparse.Namespace(cases=[case])
    match.__class__ = type("StmtMatch", (argparse.Namespace,), {})
    return _Group(functions=[_Fn("get_offset_of_instance_array", body=[match],
                                 args=("name", "index"))])


def test_affine_arm_folds():
    assert array_base_stride(_arm_group("affine"), "bank") == (0x20, 0x20)


def test_f4_quadratic_arm_is_rejected_not_mis_folded():
    """`0x20 + index*index*0x20` samples IDENTICALLY to the affine form at
    index 0 and 1 -- base 0x20, stride 0x20 -- and is wrong from index 2 on.

    The evaluator used to derive the stride by sampling those two points, so it
    accepted this and emitted plausible, wrong addresses. Matching the shape
    instead of sampling it is what makes this an error.
    """
    with pytest.raises(OffsetFoldError, match="not affine"):
        array_base_stride(_arm_group("quadratic"), "bank")


def test_f4_non_affine_operator_is_rejected():
    with pytest.raises(OffsetFoldError, match="affine"):
        array_base_stride(_arm_group("shift"), "bank")
