"""Field-named register writes in the SV backend.

`reg_rmw` folds every field-wise write to a constant (mask, value) pair, and no
field name survives into the IR -- `test_reg_ir_equivalence.py` asserts exactly
that, and it stays true. The SV emitter restores the name on the way out by
asking the register model which field has those bits, so that

    regs.csr.write_field("ars", enable)          -- PSS

reaches SystemVerilog as

    m_regs.csr.write_field(WB_DMA_CSR_ars, 32'(enable))

rather than as `write_val_masked(64, (32'(enable) & 1) << 6)`.

Three separate things have to hold, and each is checked against something that
is not the code under test:

1. **The constants are right.** Bit positions are transcribed by hand from
   `wb_dma_ch_regs_c.pss` below, not recomputed from the layout code, so a
   layout bug cannot agree with itself.
2. **The rewrite is faithful.** `--sv-reg-fields=folded` emits what this target
   emitted before any of this existed; the named form must denote the same
   write. The IR is asserted byte-identical across the two, which is what makes
   "spelling only" a measured claim rather than an intention.
3. **The emitted SV computes what it says.** A simulation drives the REAL
   generated `pssc_reg_pkg.sv` and the REAL generated constants against a stub
   bus and checks the resulting register value. Structural assertions on
   generated text have missed this class of bug before (see the operand-
   bracketing defect in `docs/design/reg-masked-access-status.md` §5).
"""
import argparse
import os
import shutil
import subprocess

import pytest

from pssc import driver
from pssc.targets.sv.reg_field_names import const_name, const_prefix, unplace
from pssc.reg_field_resolve import FieldSlice

from .codetext import code_only
from .op_model import OP_MODEL as _MODEL, op_model_sources


#: Transcribed BY HAND from src/pss/wb_dma_ch_c/wb_dma_ch_regs_c.pss -- field
#: name -> (lsb, width). Deliberately not derived from field_layout(), which is
#: the thing being checked.
_CSR_FIELDS = {
    "ch_en": (0, 1), "dst_sel": (1, 1), "src_sel": (2, 1), "inc_dst": (3, 1),
    "inc_src": (4, 1), "mode": (5, 1), "ars": (6, 1), "use_ed": (7, 1),
    "sz_wb": (8, 1), "stop": (9, 1), "busy": (10, 1), "done": (11, 1),
    "err": (12, 1), "prio": (13, 3), "rest_en": (16, 1), "ine_err": (17, 1),
    "ine_done": (18, 1), "ine_chk_done": (19, 1), "int_err": (20, 1),
    "int_done": (21, 1), "int_chk_done": (22, 1),
}


def _sources():
    return op_model_sources()


def _gen(tmp, reg_fields="named"):
    ns = argparse.Namespace(progseq_root="wb_dma_c",
                            progseq_package="wb_dma_c_pkg",
                            progseq_reg_fields=reg_fields,
                            output_dir=str(tmp))
    res = driver.compile(_sources(), target="sv-progseq", opts=ns)
    return tmp, res


@pytest.fixture(scope="module")
def named(tmp_path_factory):
    return _gen(tmp_path_factory.mktemp("named"))


@pytest.fixture(scope="module")
def folded(tmp_path_factory):
    return _gen(tmp_path_factory.mktemp("folded"), reg_fields="folded")


@pytest.fixture(scope="module")
def sv(named):
    out, _ = named
    return (out / "wb_dma_c_pkg.sv").read_text()


# --- 1. the constants ------------------------------------------------------

def test_every_csr_field_has_a_constant(sv):
    """One localparam per scalar field, with the hand-transcribed bits.

    Includes `reserved`: filtering by name would be a heuristic that silently
    drops a user field for being unluckily named, and an unused localparam
    costs nothing.
    """
    for name, (lsb, width) in _CSR_FIELDS.items():
        mask = ((1 << width) - 1) << lsb
        want = (f"localparam reg_field_t WB_DMA_CSR_{name}")
        assert want in sv, f"no constant for csr.{name}"
        line = next(ln for ln in sv.splitlines() if want in ln)
        assert f"mask:64'h{mask:016x}" in line, f"{name}: {line}"
        assert f"shift:{lsb}}}" in line, f"{name}: {line}"


def test_constants_cover_every_value_struct(sv):
    """Not just the CSR -- every emitted packed struct gets a constant block."""
    for struct in ("wb_dma_csr_s", "wb_dma_sz_s", "wb_dma_swptr_s",
                   "wb_dma_gcsr_s", "wb_dma_intmsk_s", "wb_dma_intsrc_s"):
        assert f"// Field positions of {struct}," in sv, struct


def test_prefix_is_the_value_struct_not_the_register_instance(sv):
    """`csr` is ambiguous in this model -- the channel bank and the global bank
    both have one. Keying on the value struct is what makes the constants
    collision-free, so the two must land on different prefixes.

    The generated register package resolves the ambiguity by naming the global
    one `gcsr`; the channel one keeps the plain name. Either way the prefix
    follows the STRUCT, which is the property being pinned -- keying on the
    register instance name would give both `WB_DMA_CSR` and collide."""
    assert const_prefix("wb_dma_csr_s") == "WB_DMA_CSR"
    assert const_prefix("wb_dma_gcsr_s") == "WB_DMA_GCSR"
    assert "WB_DMA_CSR_ch_en" in sv and "WB_DMA_GCSR_pause" in sv


def test_field_keeps_its_source_spelling():
    """The identifier round-trips: grepping `ch_en` finds the PSS declaration,
    the packed struct member and the constant. Upper-casing the field would be
    a lossy transform the reader has to reverse."""
    assert const_name("wb_dma_csr_s", "ch_en") == "WB_DMA_CSR_ch_en"
    assert const_name("wb_dma_csr_s", "int_chk_done") == "WB_DMA_CSR_int_chk_done"


def test_constants_agree_with_the_packed_struct(sv):
    """The struct and its constants are emitted adjacently and a reader will
    assume they agree. Walk the struct's own declared widths (MSB-first, as SV
    emits it) and check every constant against the running offset."""
    body = code_only(
        sv.split("} wb_dma_csr_s;")[0].split("typedef struct packed {")[-1])
    decls = []
    for ln in body.strip().splitlines():
        ln = ln.strip().rstrip(";")
        width = 1 if ln.startswith("bit ") and "[" not in ln else \
            int(ln.split("[")[1].split(":")[0]) + 1
        decls.append((ln.split()[-1], width))
    lsb = 0
    for name, width in reversed(decls):        # MSB-first -> LSB-first
        mask = ((1 << width) - 1) << lsb
        want = f"localparam reg_field_t WB_DMA_CSR_{name}"
        line = next((l for l in sv.splitlines() if want in l), None)
        assert line, f"struct declares {name} but no constant exists"
        assert f"mask:64'h{mask:016x}" in line, f"{name}: {line}"
        lsb += width


# --- 2. the rewrite --------------------------------------------------------

def test_all_five_call_sites_are_named(sv):
    """Every masked write in the model. `write_val_masked` must be GONE from
    the generated component -- a site left behind would be a silent
    half-conversion."""
    for frag in (
        "m_regs.csr.write_field(WB_DMA_CSR_ars, 32'(enable));",
        "m_regs.csr.write_field(WB_DMA_CSR_stop, 1);",
        "m_regs.csr.write_field(WB_DMA_CSR_use_ed, 1);",
        "m_regs.csr.write_field(WB_DMA_CSR_ch_en, 1);",
    ):
        assert frag in sv, frag
    assert "write_val_masked(" not in sv


def test_runtime_value_keeps_its_width_cast(sv):
    """`write_field(F, enable)` on a 1-bit `enable` is an implicit widening,
    which Verilator reports as WIDTHEXPAND and treats as fatal by default. The
    cast `_place()` inserts has to survive the un-placing."""
    assert "write_field(WB_DMA_CSR_ars, 32'(enable))" in sv
    assert "write_field(WB_DMA_CSR_ars, enable)" not in sv


def test_transfer_list_still_writes_twice(sv):
    """use_ed then ch_en, in that order, as two transactions. Coalescing them
    into one write_fields would be one bus operation where the device requires
    two -- so this is a behaviour assertion, not a formatting one."""
    # The impl, not the interface's `pure virtual` declaration of the same name.
    body = code_only(
        sv.split("virtual task transfer_list_start(input addr_handle_t head);")[-1]
          .split("endtask")[0])
    assert body.index("WB_DMA_CSR_use_ed") < body.index("WB_DMA_CSR_ch_en")
    assert body.count("write_field(") == 2
    assert "write_fields(" not in body


def test_folded_reproduces_the_pre_naming_output(folded):
    """`--sv-reg-fields=folded` is the escape hatch, and it has to actually
    escape: the literal pairs, and no field constant in any call."""
    text = (folded[0] / "wb_dma_c_pkg.sv").read_text()
    for frag in ("m_regs.csr.write_val_masked(64, (32'(enable) & 1) << 6);",
                 "m_regs.csr.write_val_masked(512, 512);",
                 "m_regs.csr.write_val_masked(128, 128);",
                 "m_regs.csr.write_val_masked(1, 1);"):
        assert frag in text, frag
    assert ".write_field(" not in text


def test_folded_still_emits_the_constants(folded):
    """The constants are for testbench code too, not only for the call sites,
    so switching the spelling must not withdraw them."""
    text = (folded[0] / "wb_dma_c_pkg.sv").read_text()
    assert "localparam reg_field_t WB_DMA_CSR_ars" in text


def _masked_writes(ctx):
    """Every folded (mask, value) pair the reduction left in the IR.

    Same shape as `test_op_model_rmw_equivalence._masked_writes`; `value` is
    None where it did not fold to a constant.
    """
    out = []
    for fn in ctx.type_map["wb_dma_ch_c"].functions:
        for s in fn.body or []:
            e = getattr(s, "expr", None)
            if e is None or type(e).__name__ != "ExprCall":
                continue
            if getattr(e.func, "attr", None) != "write_val_masked":
                continue
            mask, val = e.args
            out.append((
                fn.name,
                mask.value if type(mask).__name__ == "ExprConstant" else None,
                val.value if type(val).__name__ == "ExprConstant" else None,
            ))
    return sorted(out, key=repr)


def test_naming_does_not_touch_the_ir(tmp_path):
    """The claim that this is presentation-only, measured rather than intended.

    The SV emitter reads the register model to restore a name; if it also
    *wrote* -- rewriting a call, annotating a node -- then the C target would
    see a different model depending on an SV flag. One context, generated both
    ways, with the folded pairs sampled around each run.
    """
    ctx = driver.translate(_sources())
    assert ctx.errors == [], ctx.errors
    before = _masked_writes(ctx)
    assert before, "no masked writes in the model; this test proves nothing"

    from pssc.targets.op_model import elaborate
    from pssc.targets.progseq_gen import generate
    root = ctx.type_map["wb_dma_c"]
    for mode in ("named", "folded"):
        model = elaborate(ctx, root, tmp_path / mode)
        # `generate` no longer copies the core package -- the target does
        # (P4.T5), so there is nothing to turn off here.
        generate(model, "wb_dma_c_pkg", reg_fields=mode)
        assert _masked_writes(ctx) == before, mode

    # ...and the collapsed pair the C target consumes is still what is there.
    assert ("stop_channel_start", 512, 512) in before


# --- unplace: the part that can be wrong quietly ---------------------------

def test_unplace_refuses_a_shape_it_does_not_recognise():
    """A value that is not what `_place()` produces must return None so the
    caller falls back to the literal. Naming a field and writing different bits
    would be worse than the magic numbers this replaces."""
    import zuspec.ir.core as ir
    ars = FieldSlice(name="ars", lsb=6, width=1)
    # constant outside the field
    assert unplace(ir.ExprConstant(value=0x80), ars) is None
    # constant inside the field, de-shifted
    assert unplace(ir.ExprConstant(value=0x40), ars).value == 1
    # right shape but the wrong shift amount
    inner = ir.ExprBin(lhs=ir.ExprRefLocal(name="v"), op=ir.BinOp.BitAnd,
                       rhs=ir.ExprConstant(value=1))
    wrong = ir.ExprBin(lhs=inner, op=ir.BinOp.LShift, rhs=ir.ExprConstant(value=5))
    assert unplace(wrong, ars) is None
    right = ir.ExprBin(lhs=inner, op=ir.BinOp.LShift, rhs=ir.ExprConstant(value=6))
    assert unplace(right, ars) is inner.lhs


def test_mask_lookup_is_exact_set_equality():
    """Driven with the model's OWN receiver expression, so the resolution path
    under test is the one the emitter actually takes.

    A mask is named only when it is exactly one field's bits or exactly a union
    of whole fields. `prio` is the case that matters: at [15:13] it is the only
    multi-bit field in the CSR, so a mask covering part of it is the shape a
    'covers' test would wrongly accept and then mis-name.
    """
    from pssc.targets.sv.reg_field_names import FieldNamer

    ctx = driver.translate(_sources())
    assert ctx.errors == [], ctx.errors
    comp = ctx.type_map["wb_dma_ch_c"]

    # The `m_regs.csr` receiver, taken from a real folded call rather than
    # hand-built, so a change in how paths are represented fails here too.
    recv = None
    for fn in comp.functions:
        if fn.name != "stop_channel_start":
            continue
        for s in fn.body or []:
            e = getattr(s, "expr", None)
            if e is not None and getattr(getattr(e, "func", None), "attr", None) \
                    == "write_val_masked":
                recv = e.func.value
    assert recv is not None, "model no longer has the call this test drives"

    namer = FieldNamer(comp, ctx.type_map)
    named = lambda m: [r.const for r in (namer.fields_for(recv, m) or [])]

    assert named(0x0000_0040) == ["WB_DMA_CSR_ars"]
    assert named(0x0000_e000) == ["WB_DMA_CSR_prio"]
    # union of whole fields, returned LSB-first regardless of mask order
    assert named(0x0000_00c0) == ["WB_DMA_CSR_ars", "WB_DMA_CSR_use_ed"]
    # part of prio -- not prio
    assert namer.fields_for(recv, 0x0000_2000) is None
    assert namer.fields_for(recv, 0x0000_6000) is None
    # a bit belonging to no declared field (the CSR stops at bit 22)
    assert namer.fields_for(recv, 0x8000_0000) is None
    # a receiver this module cannot walk to a register
    assert namer.fields_for(object(), 0x0000_0040) is None


# --- 3. does the emitted SV compute what it says? --------------------------

_TB = r"""
module tb;
  import pssc_reg_pkg::*;
  import wb_dma_c_pkg::*;

  // Stub bus: one word, so the read-modify-write is observable.
  class stub_bus implements pss_mem_if;
    bit [31:0] word;
    int n_read, n_write;
    virtual task write8 (addr_handle_t a, bit [7:0]  d); endtask
    virtual task read8  (addr_handle_t a, output bit [7:0]  d); d = 0; endtask
    virtual task write16(addr_handle_t a, bit [15:0] d); endtask
    virtual task read16 (addr_handle_t a, output bit [15:0] d); d = 0; endtask
    virtual task write32(addr_handle_t a, bit [31:0] d); word = d; n_write++; endtask
    virtual task read32 (addr_handle_t a, output bit [31:0] d); d = word; n_read++; endtask
    virtual task write64(addr_handle_t a, bit [63:0] d); endtask
    virtual task read64 (addr_handle_t a, output bit [63:0] d); d = 0; endtask
  endclass

  stub_bus                 bus;
  reg_c #(wb_dma_csr_s) csr;
  bit [63:0]               rv;
  int                      errs;

  task check(string what, bit [31:0] got, bit [31:0] exp);
    if (got !== exp) begin
      $display("FAIL %s: got=%08h exp=%08h", what, got, exp); errs++;
    end
  endtask

  initial begin
    bus = new(); csr = new(bus, 64'h0);

    // set a bit-0 field
    bus.word = 32'h0;
    csr.write_field(WB_DMA_CSR_ch_en, 1);
    check("ch_en=1", bus.word, 32'h0000_0001);

    // read-modify-write must preserve neighbours; bit 9 starts CLEAR
    bus.word = 32'hdead_bced;
    csr.write_field(WB_DMA_CSR_stop, 1);
    check("stop=1 preserves", bus.word, 32'hdead_beed);

    // and must CLEAR a set bit, which an OR would not
    bus.word = 32'hffff_ffff;
    csr.write_field(WB_DMA_CSR_ars, 0);
    check("ars=0 clears", bus.word, 32'hffff_ffbf);

    // multi-bit field places, and an over-wide value truncates
    bus.word = 32'h0;
    csr.write_field(WB_DMA_CSR_prio, 5);
    check("prio=5", bus.word, 32'h0000_a000);
    bus.word = 32'h0;
    csr.write_field(WB_DMA_CSR_prio, 32'hff);
    check("prio=0xff truncates", bus.word, 32'h0000_e000);

    // the top declared field, at bit 22
    bus.word = 32'h0;
    csr.write_field(WB_DMA_CSR_int_chk_done, 1);
    check("int_chk_done=1", bus.word, 32'h0040_0000);

    // read_field de-shifts
    bus.word = 32'h0000_a000;
    csr.read_field(WB_DMA_CSR_prio, rv);
    check("read prio", 32'(rv), 32'h5);

    // one masked write == one read + one write
    bus.n_read = 0; bus.n_write = 0;
    csr.write_field(WB_DMA_CSR_ch_en, 1);
    check("reads",  32'(bus.n_read),  1);
    check("writes", 32'(bus.n_write), 1);

    // write_fields: several fields, ONE transaction, replacing not OR-ing
    bus.word = 32'hffff_ffff; bus.n_read = 0; bus.n_write = 0;
    csr.write_fields('{WB_DMA_CSR_prio, WB_DMA_CSR_ch_en}, '{2, 0});
    check("write_fields value",  bus.word, 32'hffff_5ffe);
    check("write_fields reads",  32'(bus.n_read),  1);
    check("write_fields writes", 32'(bus.n_write), 1);

    if (errs == 0) $display("REG_FIELD_TB_PASS");
    else           $display("REG_FIELD_TB_FAIL %0d", errs);
    $finish;
  end
endmodule
"""


@pytest.mark.skipif(not shutil.which("verilator"), reason="verilator not on PATH")
def test_generated_package_lints_clean(named):
    """At Verilator's DEFAULT settings, where warnings are fatal.

    `-Wno-fatal` would hide exactly the width defects this design has to get
    right, so it is deliberately not passed.
    """
    out, _ = named
    r = subprocess.run(
        ["verilator", "--lint-only", "-sv", "--timing",
         str(out / "pssc_reg_pkg.sv"), str(out / "wb_dma_c_pkg.sv")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not shutil.which("verilator"), reason="verilator not on PATH")
def test_field_writes_produce_the_right_register_value(named, tmp_path):
    """Drive the real generated runtime and the real generated constants.

    This is the check that the structural assertions above cannot make. The
    operand-bracketing defect (status doc §5) survived every IR-level test and
    every text assertion, and surfaced only when generated SV was executed.
    """
    out, _ = named
    tb = tmp_path / "tb.sv"
    tb.write_text(_TB)
    build = tmp_path / "obj"
    r = subprocess.run(
        ["verilator", "--binary", "-sv", "--timing", "--Mdir", str(build),
         "-o", "sim", str(out / "pssc_reg_pkg.sv"), str(out / "wb_dma_c_pkg.sv"),
         str(tb)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    run = subprocess.run([str(build / "sim")], capture_output=True, text=True)
    assert "REG_FIELD_TB_PASS" in run.stdout, run.stdout + run.stderr
