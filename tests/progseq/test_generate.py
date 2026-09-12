"""Phase 3+ tests: run the sv-progseq target and assert generated structure.

Pure-Python (no simulator): structural/regex assertions on emitted SV, robust to
formatting. The behavioral gate is the multi-sim test (test_sim_wb_dma.py).
"""
import os

import pytest

from pssc import driver

from .op_model import small_model_dir, small_model_sources

_DATA = small_model_dir()
_SRCS = small_model_sources()


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    out = tmp_path_factory.mktemp("progseq_gen")
    import argparse
    ns = argparse.Namespace(progseq_root="dma_engine_c",
                            progseq_package="dma_regs_pkg",
                            output_dir=str(out))
    res = driver.compile(_SRCS, target="sv-progseq", opts=ns)
    return out, res


def _read(gen, name):
    out, _ = gen
    return (out / name).read_text()


def test_outputs_written(gen):
    out, res = gen
    names = {os.path.basename(str(p)) for p in res.outputs}
    assert "dma_regs_pkg.sv" in names
    assert "pssc_reg_pkg.sv" in names         # core copied alongside
    assert (out / "dma_regs_pkg.sv").is_file()


def test_value_structs(gen):
    sv = _read(gen, "dma_regs_pkg.sv")
    for s in ("dma_ch_csr_s", "dma_ch_sz_s", "dma_ch_swptr_s", "dma_csr_s"):
        assert f"}} {s};" in sv, s
    # MSB-first emission: high-bit field (INT_CHK_DONE, [22]) is emitted before
    # the bit-0 field (CH_EN). Both tokens are unique to dma_ch_csr_s.
    assert sv.index("INT_CHK_DONE") < sv.index("CH_EN")
    # field widths
    assert "bit [2:0] PRIORITY;" in sv
    assert "bit [11:0] TOT_SZ;" in sv


def test_reg_groups_and_offsets(gen):
    sv = _read(gen, "dma_regs_pkg.sv")
    assert "class dma_channel_regs_c;" in sv
    assert "class dma_regs_c;" in sv
    # access mode: READONLY surfaced, READWRITE omitted
    assert "reg_c #(bit [31:0], READONLY) INT_SRC_A;" in sv
    assert "reg_c #(dma_ch_csr_s)" in sv
    # folded addresses + array stride
    assert "base + 64'h1c" in sv                         # SWPTR offset
    assert "channels[i] = new(bus, base + 64'h20 + i * 64'h20);" in sv
    assert "channels[31];" in sv
    # reserved gap omitted
    assert "_reserved" not in sv


def test_imports_core(gen):
    sv = _read(gen, "dma_regs_pkg.sv")
    assert "import pssc_reg_pkg::*;" in sv
    assert "pss_mem_if bus" in sv
    assert "addr_handle_t base" in sv


def test_export_api(gen):
    sv = _read(gen, "dma_regs_pkg.sv")
    assert "interface class dma_engine_c_if;" in sv
    # int return -> output status; explicit input after output
    assert "pure virtual task mem_to_mem_copy(output int status, input int channel," in sv
    # SV keyword renamed
    assert "input int priority_" in sv


def test_operations_folded_into_component(gen):
    sv = _read(gen, "dma_regs_pkg.sv")
    # operations live on the component class itself (no separate _impl class)
    assert "_impl" not in sv
    assert "virtual task configure_channel(" in sv
    # register read uses the task-output form
    assert "m_regs.channels[channel].CSR.read(csr);" in sv
    # repeat{}while -> forever .. break
    assert "forever begin" in sv
    assert "if (!(csr.DONE == 0)) break;" in sv


def test_component_one_class(gen):
    sv = _read(gen, "dma_regs_pkg.sv")
    # import interface extends the core seam
    assert "interface class dma_engine_c_import_if extends pss_mem_if;" in sv
    # ONE class named after the component: export impl + import redirect + factory
    assert ("class dma_engine_c #(type IMP_T = dma_engine_c_import_if) "
            "implements dma_engine_c_if, dma_engine_c_import_if;") in sv
    # register model built with `this` as the bus (accesses route back to m_imp)
    assert "m_regs = new(this, base);" in sv
    # factory returns the export handle
    assert "static function dma_engine_c_if create(IMP_T imp, addr_handle_t base);" in sv
    assert "dma_engine_c #(IMP_T) self = new(imp, base);" in sv
    # no separate impl / adapter / factory classes
    assert "_impl" not in sv
    assert "_imp_adapter_c" not in sv
    assert "_factory_c" not in sv


def test_a_declaration_initializer_is_not_discarded(tmp_path):
    """`int x = 5;` must not lower to `int x;`.

    It did. The initializer was dropped on the floor by the StmtAnnAssign arm,
    which emitted only the declaration -- output that compiles, runs, and
    silently computes with zero. Found while checking the generated form of an
    expanded masked register write, whose temporary is initialised from a
    register read: the read vanished the same way.

    Emitted as an SV declaration-with-initializer rather than a declaration plus
    an assignment, because a declaration is legal only at the start of a block.
    """
    src = tmp_path / "m.pss"
    src.write_text(
        "import std_pkg::*;\n"
        "import addr_reg_pkg::*;\n"
        "component pss_top {\n"
        "    target function void f() { int x = 5; int y = x + 1; }\n"
        "    action A { exec body { comp.f(); } }\n"
        "}\n")
    out = tmp_path / "out"
    driver.compile([str(src)], target="sv-progseq", output_dir=str(out),
                   progseq_root="pss_top")
    text = (out / "pss_top_pkg.sv").read_text()
    assert "int x = 5;" in text, text
    assert "int y = x + 1;" in text, text
