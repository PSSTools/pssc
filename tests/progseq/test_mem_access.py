"""Phase 5a: the memory-access funnel.

The golden snapshots already prove the funnel emits today's output -- they are
byte-identical across this whole phase. What they cannot do is say WHICH string
came from where, so a later refactor that moves a rendering back out of the
funnel passes them. These tests pin each rendering to the funnel itself, at the
method, so that question has an answer that does not depend on running a
generator.
"""
from __future__ import annotations

import pytest

from pssc.targets.c.mem_access import DEFAULT, WIDTHS, MemAccess, prim_names


# -- the default renderings, pinned -----------------------------------------

def test_bus_expr():
    assert DEFAULT.bus_expr("s") == "pssc_bus(s)"
    assert DEFAULT.bus_expr("self") == "pssc_bus(self)"


@pytest.mark.parametrize("width,fn", [(8, "pssc_r8"), (16, "pssc_r16"),
                                      (32, "pssc_r32"), (64, "pssc_r64")])
def test_read(width, fn):
    assert DEFAULT.read(width, "s", "wb_dma_regs_csr_addr(s)") == (
        f"{fn}(pssc_bus(s), wb_dma_regs_csr_addr(s))")


@pytest.mark.parametrize("width,fn", [(8, "pssc_w8"), (16, "pssc_w16"),
                                      (32, "pssc_w32"), (64, "pssc_w64")])
def test_write(width, fn):
    assert DEFAULT.write(width, "s", "wb_dma_regs_csr_addr(s)", "v.raw") == (
        f"{fn}(pssc_bus(s), wb_dma_regs_csr_addr(s), v.raw)")


def test_masked_write():
    assert DEFAULT.masked_write("uint32_t", "wb_dma_regs_csr", "s") == (
        "uint32_t cur = wb_dma_regs_csr_read_val(s); "
        "wb_dma_regs_csr_write_val(s, (cur & ~mask) | (val & mask));")


def test_masked_write_threads_the_array_indices():
    """The indices go to BOTH halves. A read at `[i]` composed with a write at
    `[0]` is a read-modify-write of two different registers, which compiles."""
    out = DEFAULT.masked_write("uint32_t", "wb_dma_bank_csr", "s, i0")
    assert out == ("uint32_t cur = wb_dma_bank_csr_read_val(s, i0); "
                   "wb_dma_bank_csr_write_val(s, i0, (cur & ~mask) | (val & mask));")


@pytest.mark.parametrize("kind,expected", [
    ("addr", "r_addr"), ("read", "r_read"), ("write", "r_write"),
    ("read_val", "r_read_val"), ("write_val", "r_write_val"),
    ("write_val_masked", "r_write_masked"),
])
def test_accessor_names(kind, expected):
    assert DEFAULT.accessor("r", kind) == expected


# -- the contracts -----------------------------------------------------------

def test_masked_write_always_reads():
    """P5a.T2's accept criterion, held against ANY funnel, not just the default.

    PSS 3.1 §21.14.1 defines a masked write as
    ``(current & ~mask) | (val & mask)`` -- the read is part of the definition,
    not an optimisation to be elided. On a status CSR that clears on read, that
    read is also a side effect the model asked for. A style is free to change
    how the two halves are spelled; dropping one is not a style choice.
    """
    class _Terse(MemAccess):
        read_prim = "R%d"
        write_prim = "W%d"
        bus_macro = "B"

    for mem in (DEFAULT, _Terse()):
        out = mem.masked_write("uint32_t", "reg", "s")
        assert mem.accessor("reg", "read_val") in out, out
        assert mem.accessor("reg", "write_val") in out, out
        assert out.index(mem.accessor("reg", "read_val")) < \
               out.index(mem.accessor("reg", "write_val")), \
               f"the read must precede the write it feeds: {out}"


def test_a_subclass_changes_every_access_it_renders():
    """What the funnel exists for: one override, and nothing is left behind."""
    class _Traced(MemAccess):
        read_prim = "trace_r%d"
        write_prim = "trace_w%d"
        bus_macro = "trace_bus"

    mem = _Traced()
    rendered = [mem.read(32, "s", "a"), mem.write(32, "s", "a", "v"),
                mem.bus_expr("s"), mem.masked_write("uint32_t", "reg", "s")]
    assert not any("pssc_" in r for r in rendered), rendered


def test_an_unsupported_width_is_refused():
    """Silently rounding 24 up to 32 would read a byte the model never named."""
    with pytest.raises(ValueError) as exc:
        DEFAULT.read(24, "s", "a")
    assert "24-bit" in str(exc.value) and "8, 16, 32, 64" in str(exc.value)


def test_an_unknown_register_method_is_refused():
    """`base_<kind>` as a fallback names a function nothing defines -- a defect
    that surfaces in a C compiler's output rather than in pssc's."""
    with pytest.raises(ValueError) as exc:
        DEFAULT.accessor("reg", "poke")
    assert "poke" in str(exc.value) and "write_val_masked" in str(exc.value)


def test_prim_names_covers_the_whole_seam():
    names = prim_names()
    assert len(names) == 2 * len(WIDTHS)
    assert set(names) == {"pssc_r8", "pssc_r16", "pssc_r32", "pssc_r64",
                          "pssc_w8", "pssc_w16", "pssc_w32", "pssc_w64"}


# -- agreement with the rest of the target -----------------------------------

def test_every_pss_register_method_has_an_accessor():
    """The two tables that used to be kept in agreement by hand. A PSS method
    the body emitter accepts but the funnel cannot name emits a call to a
    function `lower_reg_model` never defined."""
    from pssc.targets.c.lower_progseq import _REG_ACCESSORS
    missing = set(_REG_ACCESSORS) - set(DEFAULT.accessor_suffix)
    assert not missing, missing


def test_every_pss_memory_primitive_has_a_width():
    from pssc.targets.c.lower_progseq import _MEM_PRIMS
    for name, (direction, width) in _MEM_PRIMS.items():
        assert direction in ("read", "write")
        assert width in WIDTHS
        assert name == f"{direction}{width}"


def test_the_generated_accessors_use_the_funnel(tmp_path):
    """End to end, because the funnel being right is worth nothing if the
    generator kept its own copy."""
    from pssc import testing
    from pssc.targets.c.c_names import HANDLE
    with testing.compile_op_model("op-model-c", output_dir=tmp_path,
                                  c_link_style="vtable") as out:
        # The .c: every memory access the funnel renders is in an accessor or
        # an operation body, and both live there.
        text = out.read("dma_engine.c")
    assert f"{DEFAULT.read_fn(32)}({DEFAULT.bus_expr(HANDLE)}" in text
    assert f"{DEFAULT.write_fn(32)}({DEFAULT.bus_expr(HANDLE)}" in text
    for line in text.splitlines():
        if "_write_masked(" in line and "static inline" in line:
            assert f"_read_val({HANDLE}" in line, (
                f"a masked write lost its read: {line}")
