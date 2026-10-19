"""Overlapping register views in the C layout struct: anonymous unions.

A register map commonly describes one aperture through two views -- a normal
block and a copy view of the same bytes, a
register at two widths, a 64-bit register and its two halves. Those are
ALIASES: one piece of hardware, two names. `build_reg_map` used to refuse them
("a struct cannot place two instances at one address"); it now gathers every
connected set of overlapping members into one union, so each source member
keeps its name, its PSS access path and its address.

The model below holds every shape at once, under one root group, and the
tests check it three ways: the emitted `_Static_assert`s compile (the layout
claim, checked by C), an extra translation unit asserts the deep paths
(`offsetof(alias_top_regs_c, ap.copy.COPY)`) and strides from outside, and
a run on the functions seam traces every bus access -- the two names of an
alias must reach the same address, at their own widths, once each.
"""
import subprocess

import pytest

from pssc import driver

from .conftest import available_c_compilers

_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")

_MODEL = """
package alias_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;

    pure component w_r  : reg_c<bit[32], READWRITE, 32> {}
    pure component h_r  : reg_c<bit[16], READWRITE, 16> {}
    pure component d_r  : reg_c<bit[64], READWRITE, 64> {}
    pure component ro_r : reg_c<bit[32], READONLY, 32> {}

    // Two widths at one offset, then a member right after the union; a
    // 64-bit register with its halves as a second union (the high half at a
    // second origin, contained in D).
    pure component widths_c : reg_group_c {
        w_r W32; h_r W16; w_r NEXT; d_r D; w_r D_LO; ro_r D_HI; w_r TAIL;
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["W32"]: return 0x0;  ["W16"]: return 0x0;
                ["NEXT"]: return 0x4;
                ["D"]: return 0x8;    ["D_LO"]: return 0x8;
                ["D_HI"]: return 0xC;
                ["TAIL"]: return 0x10;
                default: return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    pure component pair_c : reg_group_c {
        w_r lo; w_r hi;
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["lo"]: return 0x0; ["hi"]: return 0x4;
                           default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    // A chain: a overlaps b, b overlaps c, a and c are apart. ADJ starts
    // where the union ends and stays sequential; gaps either side.
    pure component chain_c : reg_group_c {
        w_r FIRST; pair_c a; pair_c b; pair_c c; w_r ADJ; w_r LATE;
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["FIRST"]: return 0x0;
                ["a"]: return 0x10; ["b"]: return 0x14; ["c"]: return 0x18;
                ["ADJ"]: return 0x20;
                ["LATE"]: return 0x40;
                default: return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    pure component bank_c : reg_group_c {
        w_r x; w_r y;
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["x"]: return 0x0; ["y"]: return 0x4;
                           default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    // The reported shape: a normal view and a copy view of one aperture,
    // both at 0, the copy starting with padding and shorter. Each holds
    // arrays; a third view is a distinct array of banks at another origin.
    pure component normal_c : reg_group_c {
        w_r CFG[8]; bank_c bank[2];
        function bit[64] get_offset_of_instance(string name) { return -1; }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) {
                ["CFG"]:  return index*4;
                ["bank"]: return 0x40 + index*0x10;
                default:  return -1;
            }
        }
    }

    pure component copy_c : reg_group_c {
        w_r COPY; w_r COPY_ARR[2];
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["COPY"]: return 0x18; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { ["COPY_ARR"]: return 0x20 + index*4;
                           default: return -1; }
        }
    }

    pure component aperture_c : reg_group_c {
        normal_c normal; copy_c copy; bank_c bank_view[2]; w_r AFTER;
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["normal"]: return 0x0; ["copy"]: return 0x0;
                ["AFTER"]: return 0x60;
                default: return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { ["bank_view"]: return 0x40 + index*0x10;
                           default: return -1; }
        }
    }

    // Alignment rounding: W and p end at 0xC, C rounds the union to 0x10,
    // so NEXT at 0xC is an alias too; the group ends at 0x14 and C rounds
    // it to 0x18.
    pure component round_c : reg_group_c {
        d_r W; pair_c p; w_r NEXT; w_r AFTER;
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["W"]: return 0x0; ["p"]: return 0x4;
                ["NEXT"]: return 0xC; ["AFTER"]: return 0x10;
                default: return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    // The same, with nothing at 0xC: the union's rounded span is declared.
    pure component span_c : reg_group_c {
        d_r W; pair_c p; w_r AFTER;
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["W"]: return 0x0; ["p"]: return 0x4; ["AFTER"]: return 0x10;
                default: return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    pure component alias_top_regs_c : reg_group_c {
        widths_c widths; chain_c chain; aperture_c ap; round_c rnd; span_c span;
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["widths"]: return 0x000; ["chain"]: return 0x100;
                ["ap"]: return 0x200; ["rnd"]: return 0x300;
                ["span"]: return 0x320;
                default: return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }
}

import addr_reg_pkg::*;

component alias_top_c {
    alias_pkg::alias_top_regs_c regs;

    solve function void initialize(addr_handle_t base) {
        regs.set_handle(base);
    }

    // Each pair writes through one name and reads through its alias.
    target function bit[64] poke() {
        bit[64] v;
        regs.widths.W32.write_val(0x11);
        v = regs.widths.W16.read_val();
        regs.widths.D.write_val(0x22);
        v = v + regs.widths.D_HI.read_val();
        regs.chain.a.hi.write_val(0x33);
        v = v + regs.chain.b.lo.read_val();
        regs.ap.normal.CFG[6].write_val(0x44);
        v = v + regs.ap.copy.COPY.read_val();
        regs.ap.normal.bank[1].y.write_val(0x55);
        v = v + regs.ap.bank_view[1].y.read_val();
        regs.ap.copy.COPY_ARR[1].write_val(0x66);
        regs.rnd.NEXT.write_val(0x77);
        v = v + regs.rnd.p.hi.read_val();
        regs.span.AFTER.write_val(0x88);
        return v;
    }

    export target function poke;
}
"""

_HARNESS = r"""
#include <stdio.h>
#include "alias.h"

static int n;
#define T(op, w, a, d) do { printf("%c%d %llx %llx\n", op, w, \
        (unsigned long long)(a) - 0x40000000ull, (unsigned long long)(d)); \
        n++; } while (0)

void     pssc_mem_write8 (pssc_addr_t a, uint8_t  d) { T('W', 8, a, d); }
uint8_t  pssc_mem_read8  (pssc_addr_t a) { T('R', 8, a, 0); return 0; }
void     pssc_mem_write16(pssc_addr_t a, uint16_t d) { T('W', 16, a, d); }
uint16_t pssc_mem_read16 (pssc_addr_t a) { T('R', 16, a, 0); return 1; }
void     pssc_mem_write32(pssc_addr_t a, uint32_t d) { T('W', 32, a, d); }
uint32_t pssc_mem_read32 (pssc_addr_t a) { T('R', 32, a, 0); return 1; }
void     pssc_mem_write64(pssc_addr_t a, uint64_t d) { T('W', 64, a, d); }
uint64_t pssc_mem_read64 (pssc_addr_t a) { T('R', 64, a, 0); return 1; }

int main(void) {
    static alias_top_c t;
    unsigned long long v;
    alias_init(&t, 0x40000000u);
    v = alias_poke(&t);
    printf("= %llx (%d accesses)\n", v, n);
    return 0;
}
"""

#: Every access, once, at its own width; each alias pair at one address.
_EXPECTED = """\
W32 0 11
R16 0 0
W64 8 22
R32 c 0
W32 114 33
R32 114 0
W32 218 44
R32 218 0
W32 254 55
R32 254 0
W32 224 66
W32 30c 77
R32 308 0
W32 330 88
= 6 (14 accesses)
"""

#: The layout, asserted from OUTSIDE the generated code: deep paths through
#: anonymous members, union extents (via the next member and sizeof), and
#: array strides.
_LAYOUT = r"""
#include <stddef.h>
#include "alias.h"
#include "alias_regs.h"
/* One per line: the C99 form is a typedef named by its line. */
#define CAT_(a, b) a ## b
#define CAT(a, b) CAT_(a, b)
#define AT(c) typedef char CAT(layout_, __LINE__)[(c) ? 1 : -1]
#define OFF(p, v) AT(offsetof(alias_top_regs_c, p) == (v));
#define SZ(e, v)  AT(sizeof(((alias_top_regs_c *)0)->e) == (v));
OFF(widths.W32, 0x000)
OFF(widths.W16, 0x000)
OFF(widths.NEXT, 0x004)
OFF(widths.D, 0x008)
OFF(widths.D_LO, 0x008)
OFF(widths.D_HI, 0x00c)
OFF(widths.TAIL, 0x010)
SZ(widths, 0x18)
SZ(widths.W16, 2)
SZ(widths.D, 8)
OFF(chain.FIRST, 0x100)
OFF(chain.a, 0x110)
OFF(chain.b, 0x114)
OFF(chain.c, 0x118)
OFF(chain.a.hi, 0x114)
OFF(chain.b.lo, 0x114)
OFF(chain.ADJ, 0x120)
OFF(chain.LATE, 0x140)
SZ(chain, 0x44)
OFF(ap.normal, 0x200)
OFF(ap.copy, 0x200)
OFF(ap.normal.CFG, 0x200)
OFF(ap.copy.COPY, 0x218)
OFF(ap.copy.COPY_ARR, 0x220)
OFF(ap.normal.bank, 0x240)
OFF(ap.bank_view, 0x240)
OFF(ap.AFTER, 0x260)
SZ(ap, 0x64)
SZ(ap.normal, 0x60)
SZ(ap.copy, 0x28)
SZ(ap.normal.CFG[0], 4)
SZ(ap.copy.COPY_ARR[0], 4)
SZ(ap.normal.bank[0], 0x10)
SZ(ap.bank_view[0], 0x10)
OFF(rnd.W, 0x300)
OFF(rnd.p, 0x304)
OFF(rnd.NEXT, 0x30c)
OFF(rnd.AFTER, 0x310)
SZ(rnd, 0x18)
OFF(span.W, 0x320)
OFF(span.p, 0x324)
OFF(span.AFTER, 0x330)
SZ(span, 0x18)
int layout_ok(alias_top_regs_c *r) {
    /* The two names of an alias are one address. */
    return (char *)&r->ap.normal.CFG[6] == (char *)&r->ap.copy.COPY
        && (char *)&r->ap.normal.bank[1].y == (char *)&r->ap.bank_view[1].y
        && (char *)&r->chain.a.hi == (char *)&r->chain.b.lo
        && (char *)&r->widths.W32 == (char *)&r->widths.W16;
}
"""


def _gen(tmp_path, *argv):
    src = tmp_path / "alias.pss"
    src.write_text(_MODEL)
    out = tmp_path / "out"
    from pssc import cli
    ns = cli.build_parser().parse_args(
        ["compile", str(src), "-t", "op-model-c", "-o", str(out),
         "--root", "alias_top_c", "--prefix", "alias",
         "--lifecycle", "static", *argv])
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return out


_FUNCTIONS = ("--link-style", "direct", "--mem-access", "functions")
_STRICT = ["-Wall", "-Wextra", "-Werror"]


def _cc(cc, out, *args):
    return subprocess.run([cc, *_STRICT, *args], cwd=str(out),
                          capture_output=True, text=True)


# -- what is emitted --------------------------------------------------------

def _struct(regs: str, name: str) -> str:
    start = regs.index(f"struct {name} {{")
    return regs[start:regs.index("\n};\n", start) + 4]


def test_a_block_and_its_copy_view_are_one_anonymous_union(tmp_path):
    regs = (_gen(tmp_path, *_FUNCTIONS) / "alias_regs.h").read_text()
    assert _struct(regs, "aperture_c") == """\
struct aperture_c {
    union {                                  /* 0x000..0x60 aliases */
        normal_c normal;                         /* 0x000 */
        copy_c copy;                             /* 0x000 */
        struct {
            uint32_t _rsvd_0__bank_view[16];         /* 0x000 reserved */
            bank_c bank_view[2];                     /* 0x040 */
        };
    };
    uint32_t AFTER;                          /* 0x060 readwrite */
};
"""
    for p, v in (("normal", 0), ("copy", 0), ("bank_view", 0x40),
                 ("AFTER", 0x60)):
        assert (f"PSSC_STATIC_ASSERT(offsetof(aperture_c, {p}) == 0x{v:x}u,"
                in regs)
    assert "PSSC_STATIC_ASSERT(sizeof(aperture_c) == 0x64u" in regs


def test_every_alias_keeps_its_name_and_gets_an_offset_assertion(tmp_path):
    regs = (_gen(tmp_path, *_FUNCTIONS) / "alias_regs.h").read_text()
    for t, p, v in (("widths_c", "W16", 0x0), ("widths_c", "D_HI", 0xc),
                    ("chain_c", "b", 0x14), ("chain_c", "c", 0x18),
                    ("round_c", "NEXT", 0xc), ("copy_c", "COPY", 0x18)):
        assert f"offsetof({t}, {p}) == 0x{v:x}u," in regs
    # A READONLY alias is still const: writing it through either name fails.
    assert "const uint32_t D_HI;" in regs


def test_the_rounded_span_and_tail_are_declared(tmp_path):
    regs = (_gen(tmp_path, *_FUNCTIONS) / "alias_regs.h").read_text()
    span = _struct(regs, "span_c")
    assert "uint32_t _rsvd_0_10[4];" in span
    assert "uint32_t _rsvd_14[1];" in span
    assert "PSSC_STATIC_ASSERT(sizeof(span_c) == 0x18u" in regs


def test_a_map_without_overlap_is_unchanged(tmp_path):
    """No union, no C11 guard: the same text as before overlap support (the
    golden snapshots hold the WB DMA maps to this)."""
    regs = (_gen(tmp_path, *_FUNCTIONS) / "alias_regs.h").read_text()
    assert "union {" not in _struct(regs, "bank_c")
    assert "union {" not in _struct(regs, "copy_c")


def test_accesses_use_the_pss_path(tmp_path):
    c = (_gen(tmp_path, *_FUNCTIONS) / "alias.c").read_text()
    assert "pssc_read32(&_self->regs->ap.copy.COPY)" in c
    assert "pssc_write32(&_self->regs->ap.normal.CFG[6], 68)" in c
    assert "pssc_read16(&_self->regs->widths.W16)" in c


def test_a_misaligned_alias_is_a_compile_error(tmp_path):
    from pssc.driver import CompileError
    bad = _MODEL.replace('["D_LO"]: return 0x8;', '["D_LO"]: return 0xA;')
    src = tmp_path / "bad.pss"
    src.write_text(bad)
    from pssc import cli
    ns = cli.build_parser().parse_args(
        ["compile", str(src), "-t", "op-model-c", "-o", str(tmp_path / "o"),
         "--root", "alias_top_c", "--prefix", "alias"])
    with pytest.raises(CompileError) as ei:
        driver.compile([str(src)], target="op-model-c", opts=ns)
    msg = " ".join(map(str, ei.value.errors))
    assert "D_LO" in msg and "alignment" in msg, msg


# -- what C makes of it -----------------------------------------------------

@needs_cc
@pytest.mark.parametrize("cc", _CC)
@pytest.mark.parametrize("std", ["c99", "c11"])
def test_the_layout_holds_in_c(tmp_path, cc, std):
    out = _gen(tmp_path, *_FUNCTIONS)
    (out / "layout.c").write_text(_LAYOUT)
    for s in ("layout.c", "alias.c"):
        r = _cc(cc, out, f"-std={std}", "-c", s, "-o", "/dev/null")
        assert r.returncode == 0, (s, r.stderr)


@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_each_alias_reaches_the_same_address_once(tmp_path, cc):
    """Run on the functions seam: one access per call, at the alias's own
    width, and both names of a register on one address."""
    out = _gen(tmp_path, *_FUNCTIONS)
    (out / "main.c").write_text(_HARNESS)
    r = _cc(cc, out, "-std=c99", "main.c", "alias.c", "-o", "a.out")
    assert r.returncode == 0, r.stderr
    run = subprocess.run([str(out / "a.out")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout == _EXPECTED


@needs_cc
@pytest.mark.parametrize("argv", [
    ("--link-style", "direct", "--mem-access", "pointer"),
    ("--link-style", "mmio"),
    ("--link-style", "direct", "--mem-access", "functions", "--header-only"),
], ids=["pointer", "mmio", "header-only"])
def test_every_style_sharing_the_map_emitter_compiles(tmp_path, argv):
    out = _gen(tmp_path, *argv)
    srcs = sorted(p.name for p in out.glob("*.c"))
    if not srcs:                                  # --header-only
        (out / "tu.c").write_text('#include "alias.h"\n')
        srcs = ["tu.c"]
    for s in srcs:
        r = _cc(_CC[0], out, "-std=c99", "-c", s, "-o", "/dev/null")
        assert r.returncode == 0, (s, r.stderr)


@needs_cc
def test_a_compiler_without_anonymous_members_is_told_why(tmp_path):
    """The guard ahead of the maps names C11 when the compiler is neither C11
    nor GNU-compatible. Simulated by undefining what it looks for."""
    out = _gen(tmp_path, *_FUNCTIONS)
    regs = (out / "alias_regs.h").read_text()
    assert "anonymous unions (C11)" in regs
    probe = regs.replace("!defined(__GNUC__)", "1")
    (out / "probe_regs.h").write_text(probe)
    (out / "p.c").write_text('#include "alias.h"\n#include "probe_regs.h"\n')
    r = _cc(_CC[0], out, "-std=c99", "-c", "p.c", "-o", "/dev/null")
    assert r.returncode != 0 and "C11" in r.stderr, r.stderr


def _has_m32(cc) -> bool:
    probe = subprocess.run([cc, "-m32", "-E", "-x", "c", "-"],
                           input="#include <stdint.h>\n",
                           capture_output=True, text=True)
    return probe.returncode == 0


@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_the_layout_and_trace_hold_on_i386(tmp_path, cc):
    """i386 aligns a `uint64_t` member to 4, not 8. The layout walk assumes 8
    and states every byte that assumption adds (the union's span, the tail),
    so the struct is the same size there -- and the same trace comes out."""
    if not _has_m32(cc):
        pytest.skip(f"{cc} cannot target 32-bit here (no multilib)")
    out = _gen(tmp_path, *_FUNCTIONS, "--addr-bits", "32")
    (out / "layout.c").write_text(_LAYOUT)
    (out / "main.c").write_text(_HARNESS)
    r = _cc(cc, out, "-m32", "-std=c99", "-c", "layout.c", "-o", "/dev/null")
    assert r.returncode == 0, r.stderr
    r = _cc(cc, out, "-m32", "-std=c99", "main.c", "alias.c", "-o", "a.out")
    assert r.returncode == 0, r.stderr
    run = subprocess.run([str(out / "a.out")], capture_output=True, text=True)
    assert run.stdout == _EXPECTED
