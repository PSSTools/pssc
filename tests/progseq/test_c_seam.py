"""C4.5 -- the two things the memory seam cannot decide for you.

Both are properties of the PART, not of the model, and both are silent when
wrong: a truncated address programs a device that is not there, and a missing
barrier gives you a poll loop that reads a stale value forever. So neither gets a
default that merely looks safe -- one is a compile error, the other is a
documented no-op with a hook.
"""
import argparse
import os
import subprocess

import pytest

from pssc import driver

from .conftest import available_c_compilers

_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")

_MODEL = """
component seam_c {
    target function void poke(bit[32] v) {
        write32(make_handle_from_handle(base(), 0), v);
    }
}
"""


def _core(tmp_path):
    """The seam headers, generated alongside a trivial model."""
    src = tmp_path / "seam.pss"
    src.write_text("component seam_c { target function int nop() { return 0; } }")
    ns = argparse.Namespace(progseq_root="seam_c", c_prefix="seam",
                            output_dir=str(tmp_path), c_link_style="mmio")
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return tmp_path


def _has_m32(cc) -> bool:
    """gcc without gcc-multilib accepts -m32 and then fails to find headers."""
    probe = subprocess.run([cc, "-m32", "-E", "-x", "c", "-"],
                           input="#include <stdint.h>\n",
                           capture_output=True, text=True)
    return probe.returncode == 0


# --- the width assertion ---------------------------------------------------

@needs_cc
def test_the_mmio_seam_compiles_where_the_assumption_holds(tmp_path):
    out = _core(tmp_path)
    (out / "t.c").write_text(
        '#include "pssc_mem_mmio.h"\n'
        "int main(void) { pssc_w32(0, 0x1000, 5); return (int)pssc_r32(0, 0x1000); }\n")
    r = subprocess.run([_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "-c", "t.c", "-o", os.devnull],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_cc
def test_a_64_bit_address_on_a_32_bit_target_fails_to_compile(tmp_path):
    """The failure mode without this: `(uintptr_t)a` truncates SILENTLY, at any
    -W level, and the driver programs the low 4 GiB of a device that is not
    there. There is no runtime symptom that points back at the cast.

    The assertion's tag is the fix, not a restatement of the problem: the reader
    is told to narrow the address (`--addr-bits 32`) or use vtable, whose
    primitives and register accessors take the address by value and never form
    a pointer from it.
    """
    cc = _CC[0]
    if not _has_m32(cc):
        pytest.skip(f"{cc} cannot target 32-bit here (no multilib)")
    out = _core(tmp_path)
    (out / "t.c").write_text(
        '#include "pssc_mem_mmio.h"\n'
        "int main(void) { pssc_w32(0, 0x1000, 5); return 0; }\n")
    r = subprocess.run([cc, "-m32", "-std=c99", "-c", "t.c", "-o", os.devnull],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode != 0, "the truncating cast compiled cleanly"
    assert "use_addr_bits_32_or_vtable" in r.stderr, r.stderr


def test_the_static_assert_works_without_c11(tmp_path):
    """The generated code is built `-std=c99` by design and `_Static_assert` is
    C11, so the C99 fallback is the branch that actually runs in this project's
    own gate. Both spellings must exist."""
    core = (_core(tmp_path) / "pssc_mem.h").read_text()
    assert "_Static_assert(cond" in core          # C11
    assert "[(cond) ? 1 : -1]" in core            # C99 fallback


# --- the barrier hook ------------------------------------------------------

def test_the_barrier_defaults_to_a_no_op_and_says_why(tmp_path):
    """A default that inserted a real fence would be a lie on every part that
    does not need one, and slow. A default that is silent about the gap would be
    worse. So: no-op, documented, with the hook next to the explanation."""
    core = (_core(tmp_path) / "pssc_mem.h").read_text()
    assert "#ifndef PSSC_MEM_BARRIER" in core
    assert "define PSSC_MEM_BARRIER() ((void)0)" in core
    assert "volatile" in core and "CPU" in core


@needs_cc
def test_a_platform_barrier_is_honoured(tmp_path):
    """The hook has to be reachable from OUTSIDE the generated tree -- defined
    before the include, honoured by every primitive. Counted at runtime rather
    than grepped: `#ifndef` guards are exactly the kind of thing that looks
    right and is never reached.

    8 primitives are not exercised here; one write and one read are enough to
    show the hook is live, and the header is uniform.
    """
    out = _core(tmp_path)
    (out / "t.c").write_text(
        "static int barriers;\n"
        "#define PSSC_MEM_BARRIER() (barriers++)\n"
        '#include "pssc_mem_mmio.h"\n'
        "static volatile uint32_t cell;\n"
        "int main(void) {\n"
        "  pssc_addr_t a = (pssc_addr_t)(uintptr_t)&cell;\n"
        "  pssc_w32(0, a, 7);\n"
        "  if (pssc_r32(0, a) != 7) return 1;\n"
        "  return barriers == 2 ? 0 : 2;\n"
        "}\n")
    exe = out / "a.out"
    r = subprocess.run([_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "t.c", "-o", str(exe)],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert subprocess.run([str(exe)]).returncode == 0


@needs_cc
@pytest.mark.parametrize("link_style", ["vtable", "direct"])
def test_the_call_seams_do_not_use_the_barrier(tmp_path, link_style):
    """Deliberate, not an oversight. The call itself is an optimization barrier
    to the compiler, and what the platform's implementation does about the CPU
    is that implementation's business -- inserting a fence around someone else's
    function would be this generator making a decision it has no basis for."""
    src = tmp_path / "seam.pss"
    src.write_text("component seam_c { target function int nop() { return 0; } }")
    ns = argparse.Namespace(progseq_root="seam_c", c_prefix="seam",
                            output_dir=str(tmp_path), c_link_style=link_style)
    driver.compile([str(src)], target="op-model-c", opts=ns)
    seam = (tmp_path / f"pssc_mem_{link_style}.h").read_text()
    assert "PSSC_MEM_BARRIER()" not in seam


# --- C4.3 / C4.4: the seam split and --mem-access --------------------------

def _gen_ma(tmp_path, mem_access=None, link_style="direct", **kw):
    import argparse
    from pssc import driver
    src = tmp_path / "seam.pss"
    src.write_text("component seam_c { target function int nop() { return 0; } }")
    ns = argparse.Namespace(progseq_root="seam_c", c_prefix="seam",
                            output_dir=str(tmp_path), c_link_style=link_style,
                            c_mem_access=mem_access, **kw)
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return tmp_path


def test_the_seams_are_named_for_their_mechanism(tmp_path):
    """C4.3. `mmio` named a USE and `direct` named a DEPLOYMENT; neither said
    what actually differs, which is how the address becomes an access. The
    property that decides which one you can use -- does it form a pointer? -- was
    not in either name."""
    out = _gen_ma(tmp_path)
    assert (out / "pssc_mem_ptr.h").exists()
    assert (out / "pssc_mem_fn.h").exists()
    ptr = (out / "pssc_mem_ptr.h").read_text()
    fn = (out / "pssc_mem_fn.h").read_text()
    assert "volatile uint32_t *" in ptr
    assert "pssc_mem_read32" in fn


def test_the_old_seam_names_still_work_as_shims(tmp_path):
    """C4.3 keeps them "for one release so existing consumers do not break".

    A generated header in the wild says `#include "pssc_mem_mmio.h"` by name, so
    the rename must not be observable to it. The shim includes the new file and
    contributes nothing else -- checked by size, so a later edit that puts real
    content back here is noticed.
    """
    out = _gen_ma(tmp_path)
    for old, new in (("pssc_mem_mmio.h", "pssc_mem_ptr.h"),
                     ("pssc_mem_direct.h", "pssc_mem_fn.h")):
        text = (out / old).read_text()
        assert f'#include "{new}"' in text, old
        code = [ln for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith(("/*", "*", "//"))]
        assert len(code) <= 5, f"{old} is no longer a thin shim: {code}"


def test_the_link_style_spelling_is_unchanged(tmp_path):
    """The compatibility claim, stated as a test. `--link-style mmio` must keep
    emitting the mmio include, or a regeneration produces a diff that has
    nothing to do with the model."""
    for style, inc in (("mmio", "pssc_mem_mmio.h"),
                       ("direct", "pssc_mem_direct.h"),
                       ("vtable", "pssc_mem_vtable.h")):
        d = tmp_path / style
        d.mkdir()
        out = _gen_ma(d, link_style=style)
        assert f'#include "{inc}"' in (out / "seam.h").read_text(), style


@pytest.mark.parametrize("mem_access,inc", [
    ("pointer", "pssc_mem_ptr.h"),
    ("functions", "pssc_mem_fn.h"),
    ("selectable", "pssc_mem.h"),
])
def test_mem_access_selects_the_seam(tmp_path, mem_access, inc):
    out = _gen_ma(tmp_path, mem_access=mem_access)
    assert f'#include "{inc}"' in (out / "seam.h").read_text()


def test_mem_access_and_vtable_are_refused_together(tmp_path):
    """Not a third value of the same knob. The vtable seam reaches the bus
    through a per-instance struct of function pointers -- which is why it is the
    only style that can drive two buses at once -- so "pointer or functions?" has
    no answer for it. Refused rather than silently ignored, because a caller who
    passed both had a belief about the result."""
    with pytest.raises(ValueError, match="does not apply to --link-style vtable"):
        _gen_ma(tmp_path, mem_access="pointer", link_style="vtable")


def test_every_seam_header_is_copied_whatever_the_style(tmp_path):
    """`selectable` decides at C-COMPILE time, so the file it will reach for is
    not knowable at generation time. Copying only the selected style would make
    that mode fail to build with a missing-include error that explains nothing."""
    out = _gen_ma(tmp_path, mem_access="selectable")
    for f in ("pssc_mem.h", "pssc_mem_ptr.h", "pssc_mem_fn.h",
              "pssc_mem_vtable.h", "pssc_mem_mmio.h", "pssc_mem_direct.h"):
        assert (out / f).exists(), f


@needs_cc
def test_selectable_builds_both_ways_from_one_source(tmp_path):
    """The whole point of C4.4, and it can only be shown by building twice.

    One generated driver: pointer on the part, extern functions in a host test
    that wants to intercept the bus -- no regenerate, no second copy to keep in
    step. `nm` is what proves the mechanism actually changed; both builds
    compile either way, so a selector that silently did nothing would pass a
    compile-only check.

    The REAL operation model, not this file's probe: the probe's `nop()` never
    touches memory, so neither build references a bus primitive and the two
    objects are indistinguishable. A test whose subject cannot exhibit the
    difference proves nothing -- which is exactly what it did on the first run.
    """
    import argparse
    from .op_model import op_model_sources
    ns = argparse.Namespace(progseq_root="wb_dma_c", c_prefix="wb_dma",
                            output_dir=str(tmp_path), c_link_style="direct",
                            c_mem_access="selectable", c_lifecycle="static",
                            c_emit_stubs=True)
    driver.compile(op_model_sources(), target="op-model-c", opts=ns)
    out = tmp_path
    src = str(out / "wb_dma.c")
    base = [_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
            "-I", str(out), "-c", src, "-o"]

    ptr_o = str(out / "ptr.o")
    r = subprocess.run(base + [ptr_o], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    fn_o = str(out / "fn.o")
    r = subprocess.run(base[:1] + ["-DPSSC_MEM_ACCESS_FUNCTIONS"] + base[1:] + [fn_o],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    ptr_syms = subprocess.run(["nm", "-u", ptr_o], capture_output=True,
                              text=True).stdout
    fn_syms = subprocess.run(["nm", "-u", fn_o], capture_output=True,
                             text=True).stdout
    assert "pssc_mem_read32" not in ptr_syms, \
        "the default build wants extern bus functions; the selector did nothing"
    assert "pssc_mem_read32" in fn_syms, \
        "-DPSSC_MEM_ACCESS_FUNCTIONS did not switch to the extern-function seam"


@needs_cc
@pytest.mark.parametrize("style", ["vtable", "direct", "mmio"])
def test_no_seam_defines_the_primitives_twice(tmp_path, style):
    """The hazard the PSSC_MEM_SEAM_CHOSEN marker exists for: pssc_mem.h's
    selector firing underneath a seam that was already chosen, giving two
    definitions of `pssc_r32`. Invisible until something includes the core
    header first, which `--mem-access selectable` now does routinely."""
    d = tmp_path / style
    d.mkdir()
    out = _gen_ma(d, link_style=style)
    (out / "tu.c").write_text('#include "seam.h"\nint main(void){return 0;}\n')
    r = subprocess.run(
        [_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror", "-c", "tu.c"],
        cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- C4.6: --include placement ---------------------------------------------

def _gen_inc(tmp_path, link_style="mmio", **kw):
    import argparse
    from .op_model import op_model_sources
    ns = argparse.Namespace(progseq_root="wb_dma_c", c_prefix="wb_dma",
                            output_dir=str(tmp_path), c_link_style=link_style,
                            c_lifecycle="static", c_emit_stubs=True, **kw)
    driver.compile(op_model_sources(), target="op-model-c", opts=ns)
    return tmp_path


@needs_cc
def test_an_included_header_can_define_the_barrier(tmp_path):
    """C4.6's stated test, and the reason placement is not a style question.

    PSSC_MEM_BARRIER, PSSC_CHAN_ENTER/EXIT and PSSC_UNREACHABLE are all
    `#ifndef` guards INSIDE the seam headers. A definition that arrives after
    them is silently ignored -- no error, no warning, and the platform's fence
    simply is not there. So the only way to check the placement is to define one
    from an `--include` header and COUNT the calls at runtime.
    """
    (tmp_path / "plat_hooks.h").write_text(
        "#ifndef PLAT_HOOKS_H\n#define PLAT_HOOKS_H\n"
        "extern int plat_barriers;\n"
        "#define PSSC_MEM_BARRIER() (plat_barriers++)\n"
        "#endif\n")
    out = _gen_inc(tmp_path, c_include=["plat_hooks.h"])

    assert '#include "plat_hooks.h"' in (out / "wb_dma.h").read_text()

    (out / "main.c").write_text(
        '#include "wb_dma.h"\n'
        "int plat_barriers;\n"
        "static uint32_t space[64];\n"
        "int main(void) {\n"
        "  static wb_dma_c dma;\n"
        "  wb_dma_init(&dma, (pssc_addr_t)(uintptr_t)space);\n"
        "  wb_dma_ch_set_auto_restart(wb_dma_ch(&dma, 1), 1);\n"
        "  return plat_barriers > 0 ? 0 : 1;\n"
        "}\n")
    exe = str(out / "a.out")
    r = subprocess.run([_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "-I", str(out), "main.c", "wb_dma.c",
                        "wb_dma_stubs.c", "-o", exe],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert subprocess.run([exe]).returncode == 0, (
        "the platform's PSSC_MEM_BARRIER was never called -- the --include "
        "landed after the seam header, where the #ifndef had already fired")


def test_the_include_lands_before_the_seam(tmp_path):
    """The structural half of the check above. Kept as well as the runtime one
    because when the runtime test fails, this says immediately whether the cause
    is ordering or something else."""
    out = _gen_inc(tmp_path, c_include=["plat_hooks.h"])
    h = (out / "wb_dma.h").read_text()
    assert h.index('#include "plat_hooks.h"') < h.index('#include "pssc_mem')


def test_include_spelling_is_taken_literally(tmp_path):
    """`<foo.h>` and `"foo.h"` are not interchangeable: quoted searches the
    generated directory first, which is where the seam headers live. Guessing
    from the name would be wrong for exactly the cases this flag exists for, so
    the spelling IS the instruction."""
    out = _gen_inc(tmp_path, c_include=["<mylib/types.h>", "board.h"])
    h = (out / "wb_dma.h").read_text()
    assert "#include <mylib/types.h>" in h
    assert '#include "board.h"' in h


def test_include_order_is_preserved(tmp_path):
    """A platform header set is usually ordered -- one defines what the next
    uses. Sorting or de-duplicating them would break a working command line for
    no gain."""
    out = _gen_inc(tmp_path, c_include=["first.h", "second.h", "third.h"])
    h = (out / "wb_dma.h").read_text()
    assert (h.index('"first.h"') < h.index('"second.h"')
            < h.index('"third.h"'))


def test_include_impl_stays_out_of_the_header(tmp_path):
    """The point of the separate flag: something the BODIES need must not become
    a requirement on every caller that includes the driver header."""
    out = _gen_inc(tmp_path, c_include_impl=["private_bits.h"],
                   link_style="direct")
    assert '"private_bits.h"' not in (out / "wb_dma.h").read_text()
    assert '#include "private_bits.h"' in (out / "wb_dma.c").read_text()


@needs_cc
def test_omit_stdint_leaves_the_types_to_the_platform(tmp_path):
    """For a platform that supplies the fixed-width types itself. Paired with
    `--include` in the same generation, because on its own it produces a header
    that names uint32_t with nothing declaring it -- which is the user's
    responsibility and is why the flag's help says so."""
    (tmp_path / "plat_types.h").write_text(
        "#ifndef PLAT_TYPES_H\n#define PLAT_TYPES_H\n"
        "#include <stdint.h>\n#include <stdbool.h>\n#endif\n")
    out = _gen_inc(tmp_path, c_omit_stdint=True, c_include=["plat_types.h"])
    h = (out / "wb_dma.h").read_text()
    assert "#include <stdint.h>" not in h
    assert "#include <stdbool.h>" not in h
    assert '#include "plat_types.h"' in h

    (out / "tu.c").write_text('#include "wb_dma.h"\nint main(void){return 0;}\n')
    r = subprocess.run([_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "-I", str(out), "-c", "tu.c"],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- C6.1: --addr-bits -----------------------------------------------------

def test_addr_bits_64_changes_nothing(tmp_path):
    """The default must stay byte-identical to what it produced before the knob
    existed, or every generated header in the tree gets a spurious diff."""
    out = _gen_inc(tmp_path, c_addr_bits=64)
    assert "#define PSSC_ADDR_BITS" not in (out / "wb_dma.h").read_text()


def test_addr_bits_32_is_emitted_before_the_seam(tmp_path):
    """pssc_mem.h READS the macro to pick the typedef, so it has to be defined
    above the include. Same class of ordering bug as --include, and it fails the
    same silent way: the `#ifndef` default wins and the address stays 64-bit."""
    out = _gen_inc(tmp_path, c_addr_bits=32)
    h = (out / "wb_dma.h").read_text()
    assert "#define PSSC_ADDR_BITS 32" in h
    assert h.index("#define PSSC_ADDR_BITS 32") < h.index('#include "pssc_mem')


def test_the_width_is_a_default_not_a_decree(tmp_path):
    """`#ifndef` in the seam header, so an integrator can override on the
    command line. A generated `#define` that could not be overridden would make
    the knob a regeneration for something the compile line should settle."""
    out = _gen_inc(tmp_path, c_addr_bits=32)
    assert "#ifndef PSSC_ADDR_BITS" in (out / "pssc_mem.h").read_text()


def test_an_unsupported_width_is_a_compile_error(tmp_path):
    """The `#error` branch. Reachable only by an integrator overriding the macro
    by hand, which is exactly when a silent fallthrough to 64 would be worst."""
    core = (_gen_inc(tmp_path) / "pssc_mem.h").read_text()
    assert '#  error "PSSC_ADDR_BITS must be 32 or 64"' in core


@needs_cc
def test_narrowing_the_address_shrinks_the_object_on_a_32_bit_target(tmp_path):
    """C6.1's whole justification, measured -- and measured on the TARGET, which
    is the correction this test exists to record.

    On the x86-64 host the saving is ZERO: `bus` is an 8-byte pointer forcing
    8-byte alignment, so a 4-byte `base` just buys 4 bytes of padding. The
    footprint number in the C5.4 log attributes +20 bytes to the 64-bit address,
    and that attribution is right only where pointers are 4 bytes too. Measuring
    this knob on the host would have said it does nothing.
    """
    cc = _CC[0]
    if not _has_m32(cc):
        pytest.skip(f"{cc} cannot target 32-bit here (no multilib)")

    sizes = {}
    for bits in (64, 32):
        d = tmp_path / f"a{bits}"
        d.mkdir()
        out = _gen_inc(d, link_style="vtable", c_addr_bits=bits)
        (out / "sz.c").write_text('#include "wb_dma.h"\nwb_dma_c d;\n')
        r = subprocess.run([cc, "-m32", "-std=c99", "-Os", "-I", str(out),
                            "-c", "sz.c", "-o", "sz.o"],
                           cwd=str(out), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        sz = subprocess.run(["size", "-A", "sz.o"], cwd=str(out),
                            capture_output=True, text=True)
        if sz.returncode != 0:
            pytest.skip("size(1) unavailable")
        sizes[bits] = next(int(ln.split()[1]) for ln in sz.stdout.splitlines()
                           if ln.startswith(".bss"))

    assert sizes[32] < sizes[64], (
        f"--addr-bits 32 saved nothing on a 32-bit target: "
        f"{sizes[64]} -> {sizes[32]}")
    # One address in the root plus one per channel: 5 x 4 bytes.
    assert sizes[64] - sizes[32] == 20, (
        f"expected 20 bytes (root + 4 channels x 4), got "
        f"{sizes[64] - sizes[32]}; the handle layout changed")


@needs_cc
def test_narrowing_the_address_unblocks_the_pointer_seam_on_32_bit(tmp_path):
    """The two knobs meet here, and the result is the right one.

    C4.5 refuses the pointer seam when `pssc_addr_t` is wider than a pointer.
    `--addr-bits 32` makes that false, so the same generation that was refused
    now compiles -- the assertion is guarding the actual condition, not the
    link style.
    """
    cc = _CC[0]
    if not _has_m32(cc):
        pytest.skip(f"{cc} cannot target 32-bit here (no multilib)")
    out = _gen_inc(tmp_path, link_style="mmio", c_addr_bits=32)
    (out / "t.c").write_text('#include "wb_dma.h"\nint main(void){return 0;}\n')
    r = subprocess.run([cc, "-m32", "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "-I", str(out), "-c", "t.c", "-o", "t.o"],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, (
        "the pointer seam is still refused with a 32-bit address on a 32-bit "
        "target; the assertion is testing the wrong thing:\n" + r.stderr)


# --- DOC-5: the banner must make an ABI mismatch diagnosable ----------------

def _banner(tmp_path, **kw):
    import argparse
    from pssc import driver
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "abi.pss"
    src.write_text("component abi_c { target function int nop() { return 0; } }")
    ns = argparse.Namespace(progseq_root="abi_c", c_prefix="abi",
                            output_dir=str(tmp_path), c_header_only=True, **kw)
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return (tmp_path / "abi.h").read_text()


def test_the_header_names_every_abi_affecting_knob(tmp_path):
    """DOC-5. A caller built with `--addr-bits 64` against an object built with
    32 has a different `pssc_addr_t`, so every struct holding an address has a
    different layout. Nothing catches that: both artefacts are validly
    generated, both compile, and the only evidence is a wrong offset at run
    time.

    The banner is the diagnostic. Stating the settings makes `diff` sufficient.
    """
    b = _banner(tmp_path)
    line = [l for l in b.splitlines() if "ABI-affecting settings" in l]
    assert len(line) == 1, b.splitlines()[:6]
    for knob in ("addr-bits=", "lifecycle=", "reg-style=", "mem-access=",
                 "struct-args="):
        assert knob in line[0], (knob, line[0])


def test_the_abi_line_actually_tracks_the_knobs(tmp_path):
    """The banner is worthless if it prints the same thing regardless -- which
    is the failure mode a "does it contain the words" test cannot see.

    Mutation-checked by construction: these two generations differ ONLY in the
    knobs, so if the line did not track them the assertion below would fail.
    """
    default = _banner(tmp_path / "a", )
    narrow = _banner(tmp_path / "b", c_addr_bits=32, c_lifecycle="static")

    d = [l for l in default.splitlines() if "ABI-affecting" in l][0]
    n = [l for l in narrow.splitlines() if "ABI-affecting" in l][0]
    assert "addr-bits=64" in d and "lifecycle=malloc" in d, d
    assert "addr-bits=32" in n and "lifecycle=static" in n, n
    assert d != n


def test_the_header_names_the_generator_version(tmp_path):
    """So a bug report can say which pssc produced the file. Asserted as
    "not the fallback" rather than against a literal, which would pin the test
    to a release."""
    first = _banner(tmp_path).splitlines()[0]
    assert "Generated by pssc" in first
    assert "(unknown version)" not in first, (
        "the installed distribution could not be queried for its version")
