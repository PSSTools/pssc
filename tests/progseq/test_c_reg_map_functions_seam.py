"""Registers through the layout struct on the FUNCTIONS seam.

The layout struct is address arithmetic: `pssc_write32(&_self->regs->LOAD, v)`
never dereferences the pointer, it hands it to `pssc_mem_reg.h`, which turns it
back into a `pssc_addr_t` and calls the seam's own primitive. So the seam that
routes an address to the platform's `pssc_mem_write32` takes the same shape as
the one that stores through it -- and only vtable, whose primitives need a bus
handle a pointer cannot carry, keeps the folded per-register accessors.

The model is the timer shape that found the stride defect: a channel bank of
0x10 bytes used as `ch[2]` at a stride of 0x100, and also standalone as one
channel component's own group. The array element must be padded to the stride
or `ch[1]` lands at 0x110; the run below reads the addresses back from the
platform functions, so an offset wrong in the struct fails here and not only in
a golden snapshot.
"""
import subprocess

import pytest

from pssc import driver

from .conftest import available_c_compilers

_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")

_MODEL = """
package timer_regs_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;

    struct word_s : packed_s<> { rand bit[32] value; }

    pure component word_r : reg_c<word_s, READWRITE, 32> {}
    pure component ro_r   : reg_c<word_s, READONLY, 32> {}

    pure component timer_regs_glb_c : reg_group_c {
        word_r ID;
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["ID"]: return 0x0; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    pure component timer_regs_ch_c : reg_group_c {
        word_r CTRL;
        word_r LOAD;
        word_r STATUS;
        ro_r   COUNT;
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["CTRL"]:   return 0x0;
                ["LOAD"]:   return 0x4;
                ["STATUS"]: return 0x8;
                ["COUNT"]:  return 0xC;
                default:    return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }

    pure component timer_regs_c : reg_group_c {
        timer_regs_glb_c glb;
        timer_regs_ch_c  ch[2];
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["glb"]: return 0x0; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { ["ch"]: return 0x100 + index*0x100; default: return -1; }
        }
    }
}

import addr_reg_pkg::*;

component timer_ch_c {
    timer_regs_pkg::timer_regs_ch_c regs;
    solve function void initialize(addr_handle_t at) { regs.set_handle(at); }

    target function void start(bit[32] v) {
        timer_regs_pkg::word_s w;
        w.value = v;
        regs.LOAD.write(w);
        regs.CTRL.write_val(1);
    }
    target function bit[32] count() {
        return regs.COUNT.read_val();
    }
}

component timer_c {
    timer_regs_pkg::timer_regs_c regs;
    timer_ch_c ch[2];

    solve function void initialize(addr_handle_t base) {
        regs.set_handle(base);
        foreach (ch[i]) {
            ch[i].initialize(make_handle_from_handle(base, 0x100 + i*0x100));
        }
    }

    target function void kick(int i, bit[32] v) {
        ch[i].start(v);
        regs.ch[1].STATUS.write_val(v);
    }
    target function bit[32] id() { return regs.glb.ID.read_val(); }
    target function bit[32] poke_count() {
        regs.ch[1].STATUS.write_masked(0xF, 0x5);
        return ch[1].count();
    }

    export target function kick;
    export target function id;
    export target function poke_count;
}
"""

_HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "timer.h"

static struct { char op; unsigned long long a; unsigned v; } trace[64];
static int n;

void pssc_mem_write32(pssc_addr_t a, uint32_t d) {
    trace[n].op = 'W'; trace[n].a = a; trace[n].v = d; n++;
}
uint32_t pssc_mem_read32(pssc_addr_t a) {
    trace[n].op = 'R'; trace[n].a = a; trace[n].v = 0; n++;
    return 0xA0u;
}
void     pssc_mem_write8 (pssc_addr_t a, uint8_t  d) { (void)a; (void)d; }
uint8_t  pssc_mem_read8  (pssc_addr_t a)             { (void)a; return 0; }
void     pssc_mem_write16(pssc_addr_t a, uint16_t d) { (void)a; (void)d; }
uint16_t pssc_mem_read16 (pssc_addr_t a)             { (void)a; return 0; }
void     pssc_mem_write64(pssc_addr_t a, uint64_t d) { (void)a; (void)d; }
uint64_t pssc_mem_read64 (pssc_addr_t a)             { (void)a; return 0; }

int main(void) {
    static timer_c t;
    int i;
    timer_init(&t, 0x40000000u);
    timer_kick(&t, 1, 7);
    timer_id(&t);
    timer_poke_count(&t);
    for (i = 0; i < n; i++)
        printf("%c %llx %x\n", trace[i].op, trace[i].a, trace[i].v);
    return 0;
}
"""

#: What the run must put on the bus. ch[1] is at 0x200, not 0x110: the
#: element is padded to its 0x100 stride.
_EXPECTED = """\
W 40000204 7
W 40000200 1
W 40000208 7
R 40000000 0
R 40000208 0
W 40000208 a5
R 4000020c 0
"""


def _gen(tmp_path, *argv):
    src = tmp_path / "timer.pss"
    src.write_text(_MODEL)
    out = tmp_path / "out"
    from pssc import cli
    ns = cli.build_parser().parse_args(
        ["compile", str(src), "-t", "op-model-c", "-o", str(out),
         "--root", "timer_c", "--lifecycle", "static", *argv])
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return out


_FUNCTIONS = ("--link-style", "direct", "--mem-access", "functions")


def test_the_functions_seam_addresses_registers_through_the_layout(tmp_path):
    out = _gen(tmp_path, *_FUNCTIONS)
    c = (out / "timer.c").read_text()
    regs = (out / "timer_regs.h").read_text()
    assert "pssc_write32(&_self->regs->LOAD," in c
    assert "pssc_write32(&_self->regs->ch[1].STATUS, v)" in c
    assert "Baked inline register accessors" not in c
    assert "_write_val(" not in c
    assert "struct timer_regs_ch_c {" in regs


def test_plain_direct_takes_the_same_shape(tmp_path):
    """`direct` is the functions seam under its old name: one seam, one
    register API, whichever spelling selected it."""
    out = _gen(tmp_path, "--link-style", "direct")
    assert '#include "pssc_mem_direct.h"' in (out / "timer.h").read_text()
    assert "pssc_write32(&_self->regs->LOAD," in (out / "timer.c").read_text()


def test_vtable_keeps_the_folded_accessors(tmp_path):
    """Its primitives need the instance's `pssc_mem_if`, which a pointer to a
    register cannot carry."""
    out = _gen(tmp_path, "--link-style", "vtable")
    c = (out / "timer.c").read_text()
    assert "Baked inline register accessors" in c
    assert not (out / "timer_regs.h").exists() or \
        "struct timer_regs_ch_c {" not in (out / "timer_regs.h").read_text()


def test_the_array_element_is_padded_to_its_stride(tmp_path):
    """One struct per group type, sized for every use: the 0x10 bank used as
    `ch[2]` at stride 0x100 is 0x100 bytes, standalone use or not."""
    regs = (_gen(tmp_path, *_FUNCTIONS) / "timer_regs.h").read_text()
    assert "PSSC_STATIC_ASSERT(sizeof(timer_regs_ch_c) == 0x100u" in regs
    assert "uint32_t _rsvd_10[60];" in regs
    assert "PSSC_STATIC_ASSERT(sizeof(timer_regs_c) == 0x300u" in regs


def _build(out, cc, *extra, harness=_HARNESS):
    (out / "main.c").write_text(harness)
    exe = out / "a.out"
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", *extra,
         "main.c", "timer.c", "-o", str(exe)],
        cwd=str(out), capture_output=True, text=True)
    return r, exe


def _sanitizes(cc, tmp_path) -> bool:
    probe = tmp_path / "probe.c"
    probe.write_text("int main(void) { return 0; }\n")
    r = subprocess.run([cc, "-fsanitize=undefined", str(probe),
                        "-o", str(tmp_path / "probe")],
                       capture_output=True, text=True)
    return r.returncode == 0


@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_the_functions_seam_puts_each_register_where_the_map_says(tmp_path, cc):
    """Compiled and RUN: the platform's functions see base + the register's
    offset, ch[1] included, so the stride is exercised."""
    out = _gen(tmp_path, *_FUNCTIONS)
    extra = (["-fsanitize=undefined", "-fno-sanitize-recover=all"]
             if _sanitizes(cc, tmp_path) else [])
    r, exe = _build(out, cc, *extra)
    assert r.returncode == 0, r.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout == _EXPECTED


@needs_cc
def test_the_pointer_seam_build_compiles_with_the_padded_element(tmp_path):
    """The defect's symptom: `sizeof(timer_regs_c) == 0x300u` failed to compile
    because the element was laid out unpadded."""
    out = _gen(tmp_path, "--link-style", "direct", "--mem-access", "pointer")
    r = subprocess.run([_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "-c", "timer.c", "-o", "/dev/null"],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_cc
def test_writing_a_readonly_register_is_a_compile_error(tmp_path):
    """CONST IS LOAD-BEARING on this seam too: a READONLY member is `const`, and
    `pssc_write32` takes a non-const `volatile void *`."""
    out = _gen(tmp_path, *_FUNCTIONS)
    (out / "bad.c").write_text(
        '#include "timer.h"\n#include "timer_regs.h"\n'
        "void bad(timer_ch_c *s) { pssc_write32(&s->regs->COUNT, 1); }\n")
    r = subprocess.run([_CC[0], "-std=c99", "-Werror", "-c", "bad.c",
                        "-o", "/dev/null"],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode != 0, "a write to a READONLY register compiled"
    assert "COUNT" in r.stderr or "const" in r.stderr, r.stderr


@needs_cc
def test_the_register_header_refuses_the_vtable_seam(tmp_path):
    """Its primitives would be called with a NULL bus handle, which compiles
    and crashes. Refused at the include instead."""
    out = _gen(tmp_path, *_FUNCTIONS)
    (out / "v.c").write_text(
        '#include "pssc_mem_vtable.h"\n#include "pssc_mem_reg.h"\n')
    r = subprocess.run([_CC[0], "-std=c99", "-c", "v.c", "-o", "/dev/null"],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode != 0
    assert "vtable" in r.stderr, r.stderr
