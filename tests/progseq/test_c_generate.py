"""Phase 4/5: structural assertions on emitted C (no compiler needed).

Robust regex/structure checks, plus the design's backbone invariant: the
operation bodies are byte-identical across the three link styles.
"""
import argparse
import os

import pytest

from pssc import driver

from .op_model import small_model_dir, small_model_sources

_DATA = small_model_dir()
_SRCS = small_model_sources()


def _gen(out_dir, link_style="vtable", reg_style="bitfields", prefix="wb_dma"):
    ns = argparse.Namespace(progseq_root="dma_engine_c", c_prefix=prefix,
                            c_link_style=link_style, c_reg_style=reg_style,
                            c_header_only=False, progseq_core_copy=True,
                            output_dir=str(out_dir))
    driver.compile(_SRCS, target="c-progseq", opts=ns)
    return out_dir


@pytest.fixture(scope="module")
def vtable(tmp_path_factory):
    return _gen(tmp_path_factory.mktemp("c_vtable"))


def _h(d):
    return (d / "wb_dma.h").read_text()


def _c(d):
    return (d / "wb_dma.c").read_text()


def _hc(d):
    """Both generated files. For an assertion about what the generator EMITS
    rather than about which file it lands in -- the register layouts are split
    between the two (API-mentioned ones in the header, the rest in the .c), so
    a search of either alone answers a question nobody asked."""
    return _h(d) + "\n" + _c(d)


def test_value_unions(vtable):
    h = _hc(vtable)
    for t in ("dma_ch_csr_t", "dma_ch_sz_t", "dma_ch_swptr_t", "dma_csr_t"):
        assert f"}} {t};" in h, t
    # LSB-first (declaration order): CH_EN [0] precedes INT_CHK_DONE [22]
    assert h.index("CH_EN") < h.index("INT_CHK_DONE")
    assert "uint32_t PRIORITY     :  3;" in h
    assert "uint32_t TOT_SZ       : 12;" in h


def test_baked_accessors(vtable):
    # The .c: the accessors are implementation and live there.
    h = _c(vtable)
    assert "wb_dma_regs_channels_CSR_addr" in h
    assert "0x20u + (pssc_addr_t)i0 * 0x20u" in h     # channel base + stride
    # READONLY -> read accessor present, write suppressed
    assert "wb_dma_regs_INT_SRC_A_read" in h
    assert "wb_dma_regs_INT_SRC_A_write" not in h
    assert "_reserved" not in h                       # reserved gap omitted


def test_export_api_native(vtable):
    h, c = _h(vtable), _c(vtable)
    # native int return, opaque handle first arg, no output-status
    assert "int wb_dma_mem_to_mem_copy(wb_dma_t *s, int channel," in h
    assert "do {" in c and "} while (" in c           # native do...while
    assert "output" not in c
    # vtable create takes the bus
    assert "wb_dma_create(const pssc_mem_if *bus, pssc_addr_t base)" in h


def _op_body(text, sig_substr):
    """Return the inner lines of the function whose signature contains
    ``sig_substr`` (between its opening { and the column-0 closing })."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if sig_substr in ln and ln.rstrip().endswith("{"))
    body = []
    for ln in lines[start + 1:]:
        if ln == "}":
            break
        body.append(ln)
    return body


def test_body_identical_across_address_seams(tmp_path):
    """The seams that take an ADDRESS emit the same body.

    `mmio` is deliberately not in the set any more. It is the pointer-
    dereference seam, so the device IS memory and the generated body follows
    the register-layout struct -- `write32(&s->regs->csr, v)` rather than a
    baked accessor over a folded offset. That is a different body on purpose,
    and it is the one thing this test must not average away.

    What remains is the invariant that was always the point: a change to WHERE
    an access goes must not change WHAT the operation does.
    """
    bodies = {}
    for style in ("vtable", "direct"):
        d = _gen(tmp_path / style, link_style=style)
        # Every style now has a .c, mmio included: it no longer implies a
        # header-only API, so there is no per-style question about where a
        # body landed -- which is itself part of the invariant.
        src = (d / "wb_dma.c").read_text()
        bodies[style] = _op_body(src, "wb_dma_mem_to_mem_copy(")
    assert bodies["vtable"] == bodies["direct"], \
        "operation bodies must be byte-identical across the address seams"


def test_reg_style_accessors(tmp_path):
    d = _gen(tmp_path / "acc", reg_style="accessors")
    # The .c: this style changes how a LAYOUT is spelled, and layouts are
    # implementation -- so the whole thing lands there, helpers included.
    h = _c(d)
    # layout-independent path: plain raw word + shift/mask helpers
    assert "dma_ch_csr_t_PRIORITY_get" in h
    assert "dma_ch_csr_t_PRIORITY_set" in h
