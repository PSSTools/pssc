"""Strided register arrays and `_<digit>` members in the C register map.

Two defects made a register-map driver fail to build while pssc exited 0:

* A register array whose stride exceeds its width (`FILT[4]` at stride 8)
  was declared `uint32_t FILT[4]` -- 0x10 bytes for an 0x20-byte span -- so
  every later member moved (the offset assertions caught it) and `FILT[i]`
  indexed at 4-byte steps. Such an element is now a struct spanning the
  stride, the register its `v` member, and the access is `FILT[i].v`
  (`reg_layout.is_strided`, one predicate for the declaration and the
  access).
* Members named `_0`, `_1` (a generator's spelling of an index) were taken
  for reserved placeholders: no accessor and no `_Acc`, so the reg-map seam
  dropped a value struct's `.raw` and the vtable seam called accessors that
  were never defined. An underscore before a digit is now an ordinary name
  (`reg_layout.is_reserved_name`).

The model is built on every C seam, as C99 and C11 under -Werror, and run:
the extern-functions and vtable seams trace each access, the pointer seams
run on a buffer, and every seam must leave the same words at the same
offsets.
"""
import subprocess

import pytest

from pssc import cli, driver
from pssc.driver import CompileError

from .conftest import available_c_compilers

_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")

_MODEL = """
package regarr_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;

    struct pair0_s : packed_s<> {
        rand bit      EN;
        rand bit[7]   MODE;
        rand bit[24]  RSVD;
    }

    pure component pair_c : reg_group_c {
        reg_c<pair0_s, READWRITE, 32> _0;
        reg_c<bit[32], READWRITE, 32> _1;
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["_0"]: return 0x0; ["_1"]: return 0x4;
                           default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    pure component blk_c : reg_group_c {
        reg_c<bit[32], READWRITE, 32> FILT[4];
        reg_c<bit[32], READWRITE, 32> NEXT;
        pair_c PAIR[4];
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["NEXT"]: return 0x50; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) {
                ["FILT"]: return 0x30 + index*0x8;
                ["PAIR"]: return 0x100 + index*0x8;
                default:  return -1;
            }
        }
    }
}

import addr_reg_pkg::*;

component dev_c {
    regarr_pkg::blk_c blk;

    solve function void initialize(addr_handle_t base) {
        blk.set_handle(base);
    }

    target function void set_filter(int idx, bit[32] v) {
        blk.FILT[idx].write_val(v);
    }
    target function bit[32] get_filter(int idx) {
        return blk.FILT[idx].read_val();
    }
    target function void set_next(bit[32] v) {
        blk.NEXT.write_val(v);
    }
    target function void enable_pair(int idx, bit[7] mode) {
        regarr_pkg::pair0_s p;
        p = blk.PAIR[idx]._0.read();
        p.EN = 1;
        p.MODE = mode;
        blk.PAIR[idx]._0.write(p);
    }
    target function void set_limit(int idx, bit[32] v) {
        blk.PAIR[idx]._1.write_val(v);
    }

    export target function set_filter;
    export target function get_filter;
    export target function set_next;
    export target function enable_pair;
    export target function set_limit;
}
"""

#: Every C seam. The pointer seams dereference the address, so they run on
#: a buffer; the others hand it to the platform (`direct` alone is the
#: extern-functions mechanism).
_SEAMS = {
    "mmio": (),
    "direct": ("--link-style", "direct"),
    "pointer": ("--mem-access", "pointer"),
    "functions": ("--link-style", "direct", "--mem-access", "functions"),
    "vtable": ("--link-style", "vtable"),
}
_TRACED = ("direct", "functions", "vtable")

_HARNESS = r"""
#include <stdio.h>
#include <stdint.h>
#include "dev.h"

static uint32_t mem[0x48];               /* the 0x120-byte block */
#define TRACE_BASE 0x40000000u

#if defined(SEAM_TRACED)
static uint32_t *cell(pssc_addr_t a) { return &mem[(a - TRACE_BASE) / 4]; }
static uint32_t rd(pssc_addr_t a) {
    printf("R32 %x\n", (unsigned)(a - TRACE_BASE)); return *cell(a);
}
static void wr(pssc_addr_t a, uint32_t d) {
    printf("W32 %x %x\n", (unsigned)(a - TRACE_BASE), (unsigned)d);
    *cell(a) = d;
}
#endif

#if defined(SEAM_FUNCTIONS)
void     pssc_mem_write8 (pssc_addr_t a, uint8_t  d) { (void)a; (void)d; }
uint8_t  pssc_mem_read8  (pssc_addr_t a) { (void)a; return 0; }
void     pssc_mem_write16(pssc_addr_t a, uint16_t d) { (void)a; (void)d; }
uint16_t pssc_mem_read16 (pssc_addr_t a) { (void)a; return 0; }
void     pssc_mem_write32(pssc_addr_t a, uint32_t d) { wr(a, d); }
uint32_t pssc_mem_read32 (pssc_addr_t a) { return rd(a); }
void     pssc_mem_write64(pssc_addr_t a, uint64_t d) { (void)a; (void)d; }
uint64_t pssc_mem_read64 (pssc_addr_t a) { (void)a; return 0; }
#endif

#if defined(SEAM_VTABLE)
static void     w8 (void *c, pssc_addr_t a, uint8_t  d) { (void)c; (void)a; (void)d; }
static uint8_t  r8 (void *c, pssc_addr_t a) { (void)c; (void)a; return 0; }
static void     w16(void *c, pssc_addr_t a, uint16_t d) { (void)c; (void)a; (void)d; }
static uint16_t r16(void *c, pssc_addr_t a) { (void)c; (void)a; return 0; }
static void     w32(void *c, pssc_addr_t a, uint32_t d) { (void)c; wr(a, d); }
static uint32_t r32(void *c, pssc_addr_t a) { (void)c; return rd(a); }
static void     w64(void *c, pssc_addr_t a, uint64_t d) { (void)c; (void)a; (void)d; }
static uint64_t r64(void *c, pssc_addr_t a) { (void)c; (void)a; return 0; }
static const pssc_mem_if bus = { w8, r8, w16, r16, w32, r32, w64, r64, 0 };
#endif

int main(void) {
    static dev_c t;
    unsigned k;
    uint32_t v;
    mem[0x48 / 4] = 0x33;                /* FILT[3] */
    mem[0x108 / 4] = 0xa500;             /* PAIR[1]._0 */
#if defined(SEAM_VTABLE)
    dev_init(&t, &bus, TRACE_BASE);
#elif defined(SEAM_TRACED)
    dev_init(&t, TRACE_BASE);
#else
    dev_init(&t, (pssc_addr_t)(uintptr_t)mem);
#endif
    dev_set_filter(&t, 2, 0x22);
    v = dev_get_filter(&t, 3);
    dev_set_next(&t, v + 1);
    dev_enable_pair(&t, 1, 3);
    dev_set_limit(&t, 2, 0x77);
    for (k = 0; k < 0x48; k++)
        if (mem[k]) printf("@%x %x\n", k * 4, (unsigned)mem[k]);
    return 0;
}
"""

#: The words the run leaves, at their offsets from the block's base: FILT
#: at 0x30 + 8*i, NEXT at 0x50, PAIR[i]._0 at 0x100 + 8*i, `_1` 4 past it.
_MEMORY = """\
@40 22
@48 33
@50 34
@108 a507
@114 77
"""

#: On the traced seams, each access once, at its own address: `enable_pair`
#: is one 32-bit read and one 32-bit write of PAIR[1]._0.
_TRACE = """\
W32 40 22
R32 48
W32 50 34
R32 108
W32 108 a507
W32 114 77
"""


def _gen(tmp_path, model, *argv, root="dev_c", prefix="dev", name="m"):
    src = tmp_path / f"{name}.pss"
    src.write_text(model)
    out = tmp_path / name
    ns = cli.build_parser().parse_args(
        ["compile", str(src), "-t", "op-model-c", "-o", str(out),
         "--root", root, "--prefix", prefix, "--lifecycle", "static", *argv])
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return out


def _build_and_run(cc, out, std, seam):
    defs = ["-DSEAM_FUNCTIONS" if seam == "direct" else
            f"-DSEAM_{seam.upper()}"]
    if seam in _TRACED:
        defs.append("-DSEAM_TRACED")
    (out / "main.c").write_text(_HARNESS)
    res = subprocess.run(
        [cc, f"-std={std}", "-Wall", "-Wextra", "-Werror", *defs, "-I",
         str(out), "main.c", "dev.c", "-o", f"run_{std}"],
        cwd=str(out), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    res = subprocess.run([str(out / f"run_{std}")], capture_output=True,
                         text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout


@needs_cc
@pytest.mark.c_toolchain
@pytest.mark.parametrize("seam", sorted(_SEAMS))
def test_every_seam_builds_and_addresses_every_register(tmp_path, seam):
    out = _gen(tmp_path, _MODEL, *_SEAMS[seam])
    for std in ("c99", "c11"):
        got = _build_and_run(_CC[0], out, std, seam)
        want = (_TRACE if seam in _TRACED else "") + _MEMORY
        assert got == want, (seam, std)


def test_a_strided_array_is_an_element_struct_of_the_stride(tmp_path):
    regs = (_gen(tmp_path, _MODEL) / "dev_regs.h").read_text()
    blk = regs[regs.index("struct blk_c {"):]
    assert "struct { uint32_t v; uint32_t _rsvd[1]; } FILT[4];" in blk
    assert "uint32_t NEXT;" in blk
    assert ("PSSC_STATIC_ASSERT(sizeof(((blk_c *)0)->FILT[0]) == 0x8u, "
            "blk_c_FILT_stride);") in regs
    assert "PSSC_STATIC_ASSERT(offsetof(blk_c, NEXT) == 0x50u" in regs
    assert "PSSC_STATIC_ASSERT(sizeof(blk_c) == 0x120u" in regs


def test_numbered_members_are_layout_members_and_typed_accesses(tmp_path):
    out = _gen(tmp_path, _MODEL)
    regs = (out / "dev_regs.h").read_text()
    assert "uint32_t _0;" in regs and "uint32_t _1;" in regs
    body = (out / "dev.c").read_text()
    # A value struct is opened and closed through `.raw`; the strided
    # element through `.v`.
    assert ("p = (pair0_s){ .raw = pssc_read32(&_self->blk->PAIR[idx]._0) };"
            in body)
    assert "pssc_write32(&_self->blk->PAIR[idx]._0, (p).raw);" in body
    assert "pssc_write32(&_self->blk->FILT[idx].v, v);" in body


def test_numbered_members_get_accessors_on_the_vtable_seam(tmp_path):
    out = _gen(tmp_path, _MODEL, "--link-style", "vtable")
    body = (out / "dev.c").read_text()
    for kind in ("addr", "read", "write"):
        assert f" dev_blk_PAIR__0_{kind}(" in body
    assert "p = dev_blk_PAIR__0_read(_self, idx);" in body
    # The accessors address the stride; no layout struct is involved.
    assert "_self->base_blk + 0x30u + (pssc_addr_t)i0 * 0x8u" in body


# --- the shapes a strided element takes ----------------------------------------

_SHAPES = """
package shp_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;

    pure component shapes_c : reg_group_c {
        reg_c<bit[32], READONLY,  32> RO[2];    // 0x00, stride 8: const v
        reg_c<bit[16], READWRITE, 16> H[2];     // 0x10, stride 4: byte pad
        reg_c<bit[64], READWRITE, 64> D[2];     // 0x20, stride 0x10: word pad
        reg_c<bit[32], READWRITE, 32> P[4];     // 0x40, packed: unchanged
        reg_c<bit[32], READWRITE, 32> A[2];     // 0x60, stride 8 ...
        reg_c<bit[64], READWRITE, 64> AD;       // 0x60, ... aliased by AD
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["AD"]: return 0x60; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) {
                ["RO"]: return 0x00 + index*0x8;
                ["H"]:  return 0x10 + index*0x4;
                ["D"]:  return 0x20 + index*0x10;
                ["P"]:  return 0x40 + index*0x4;
                ["A"]:  return 0x60 + index*0x8;
                default: return -1;
            }
        }
    }
}

import addr_reg_pkg::*;

component shp_c {
    shp_pkg::shapes_c r;
    solve function void initialize(addr_handle_t base) { r.set_handle(base); }
    target function bit[64] poke() {
        bit[64] v;
        v = r.RO[1].read_val();
        r.H[1].write_val(0x1234);
        r.D[1].write_val(0x55);
        r.P[3].write_val(0x66);
        r.A[1].write_val(0x77);
        v = v + r.AD.read_val();
        return v;
    }
    export target function poke;
}
"""

_SHAPES_HARNESS = r"""
#include <stdio.h>
#include "shp.h"
#define T(op, w, a, d) printf("%c%d %llx %llx\n", op, w, \
        (unsigned long long)(a) - 0x40000000ull, (unsigned long long)(d))
void     pssc_mem_write8 (pssc_addr_t a, uint8_t  d) { T('W', 8, a, d); }
uint8_t  pssc_mem_read8  (pssc_addr_t a) { T('R', 8, a, 0); return 0; }
void     pssc_mem_write16(pssc_addr_t a, uint16_t d) { T('W', 16, a, d); }
uint16_t pssc_mem_read16 (pssc_addr_t a) { T('R', 16, a, 0); return 0; }
void     pssc_mem_write32(pssc_addr_t a, uint32_t d) { T('W', 32, a, d); }
uint32_t pssc_mem_read32 (pssc_addr_t a) { T('R', 32, a, 0); return 1; }
void     pssc_mem_write64(pssc_addr_t a, uint64_t d) { T('W', 64, a, d); }
uint64_t pssc_mem_read64 (pssc_addr_t a) { T('R', 64, a, 0); return 2; }
int main(void) {
    static shp_c t;
    shp_init(&t, 0x40000000u);
    printf("= %llx\n", (unsigned long long)shp_poke(&t));
    return 0;
}
"""


def _shapes(tmp_path):
    return _gen(tmp_path, _SHAPES, "--link-style", "direct", "--mem-access",
                "functions", root="shp_c", prefix="shp", name="shp")


def _struct(regs: str, name: str) -> str:
    start = regs.index(f"struct {name} {{")
    return regs[start:regs.index("\n};\n", start) + 4]


def test_element_padding_constness_and_an_alias_union(tmp_path):
    regs = (_shapes(tmp_path) / "shp_regs.h").read_text()
    assert _struct(regs, "shapes_c") == """\
struct shapes_c {
    struct { const uint32_t v; uint32_t _rsvd[1]; } RO[2]; /* 0x000 readonly, stride 0x8 */
    struct { uint16_t v; uint8_t _rsvd[2]; } H[2]; /* 0x010 readwrite, stride 0x4 */
    uint32_t _rsvd_18[2];                    /* 0x018 reserved */
    struct { uint64_t v; uint32_t _rsvd[2]; } D[2]; /* 0x020 readwrite, stride 0x10 */
    uint32_t P[4];                           /* 0x040 readwrite */
    uint32_t _rsvd_50[4];                    /* 0x050 reserved */
    union {                                  /* 0x060..0x70 aliases */
        struct { uint32_t v; uint32_t _rsvd[1]; } A[2]; /* 0x060 readwrite, stride 0x8 */
        uint64_t AD;                             /* 0x060 readwrite */
    };
};
"""
    for m, stride in (("RO", 8), ("H", 4), ("D", 0x10), ("A", 8)):
        assert (f"PSSC_STATIC_ASSERT(sizeof(((shapes_c *)0)->{m}[0]) == "
                f"0x{stride:x}u, shapes_c_{m}_stride);") in regs
    assert "->P[0])" not in regs           # a packed array is not an element struct


@needs_cc
@pytest.mark.c_toolchain
def test_element_shapes_build_and_reach_their_addresses(tmp_path):
    out = _shapes(tmp_path)
    (out / "main.c").write_text(_SHAPES_HARNESS)
    for std in ("c99", "c11"):
        res = subprocess.run(
            [_CC[0], f"-std={std}", "-Wall", "-Wextra", "-Werror", "-I",
             str(out), "main.c", "shp.c", "-o", "run"],
            cwd=str(out), capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        res = subprocess.run([str(out / "run")], capture_output=True,
                             text=True)
        assert res.stdout == """\
R32 8 0
W16 14 1234
W64 30 55
W32 4c 66
W32 68 77
R64 60 0
= 3
""", std


def test_a_readonly_strided_element_cannot_be_written(tmp_path):
    """`const` is on `v`: the write primitive's `volatile void *` will not
    take its address, as for a scalar READONLY register."""
    if not _CC:
        pytest.skip("no C compiler on PATH")
    out = _shapes(tmp_path)
    (out / "bad.c").write_text(
        '#include "shp.h"\n#include "shp_regs.h"\n'
        "void bad(shapes_c *r) { pssc_write32(&r->RO[1].v, 1); }\n")
    res = subprocess.run([_CC[0], "-std=c11", "-Werror", "-c", "-I",
                          str(out), "bad.c", "-o", "/dev/null"],
                         cwd=str(out), capture_output=True, text=True)
    assert res.returncode != 0
    assert "const" in res.stderr or "qualifier" in res.stderr


def test_a_stride_that_is_not_a_multiple_of_the_width_is_refused(tmp_path):
    model = _MODEL.replace("0x30 + index*0x8", "0x30 + index*0x6")
    with pytest.raises(CompileError) as ei:
        _gen(tmp_path, model)
    text = "\n".join(ei.value.errors or [str(ei.value)])
    assert "'blk_c.FILT' has stride 0x6, which is not a multiple of its " \
           "4-byte element" in text
    # Located at the array's declaration in the model.
    assert "m.pss:" in text.split("'blk_c.FILT'")[0]


def test_the_accessor_seam_takes_the_same_stride(tmp_path):
    """vtable keeps addressed accessors, which never needed the layout: a
    stride that C's layout cannot express is still addressed there."""
    model = _MODEL.replace("0x30 + index*0x8", "0x30 + index*0x6")
    body = (_gen(tmp_path, model, "--link-style", "vtable")
            / "dev.c").read_text()
    assert "_self->base_blk + 0x30u + (pssc_addr_t)i0 * 0x6u" in body
