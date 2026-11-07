"""Named PSS constants stay symbolic in op-model-c (user ruling, 2026-11-06).

A constant a body names is a macro the header defines only if the build has
not (`#ifndef`), so ONE generated `.c` compiled against two project
configurations behaves as each says. pssc decides nothing from the value: a
use is an unknown value of the declared type. A constant whose value pssc
folded somewhere else (an array size, here) is PINNED: overriding it is a
compile error naming it.

Every claim is checked by building and RUNNING the generated C (the
construct matrix's harness and flags, `-std=c99 -Wall -Wextra -Werror`),
never by reading it alone. The integer cases are held to the reference model
of LRM 8.7 (`matrix/ref8_7.py`), not to pssc's own `IntSemantics`.
"""
from __future__ import annotations

import argparse
import re
import subprocess

import pytest

from pssc import driver, targets
from pssc.driver import CompileError
from pssc.targets import op_model as om
from pssc.targets.c.backend import COpModelBackend
from pssc.targets.c.style import CSettings, CStylePolicy

from . import trace_harness as th
from .conftest import diagnostic_text
from .matrix._matrix import REGS
from .matrix.ref8_7 import Bin, T, Un, Var, bits, convert

pytestmark = [
    pytest.mark.c_toolchain,
    pytest.mark.skipif(not th.CC, reason="no C compiler"),
]

#: Generic feature parameters: a package `const` and `static const`s.
PKG = """
package feat_pkg {
    const int MODE_SUP = 1;
    static const int OPT = 0;
    static const int SGN = 5;
    static const bit[8] SMALL = 7;
    static const int DEPTH = 2;
}
"""

#: `a` (32-bit registers) at the first handle, `w` (one 64-bit) at the second.
TOP = PKG + """
component pss_top {
  ga_c a;
  g64_c w;
  int mode = 1;
  int buf[feat_pkg::DEPTH];
  solve function void initialize(addr_handle_t base, addr_handle_t wide) {
    a.set_handle(base);
    w.set_handle(wide);
  }
  target function void run() {
    bit[4] x = feat_pkg::OPT;
    // Not decidable at generation time: OPT is the build's.
    if (mode != 0 && feat_pkg::OPT == 0) {
      a.STS.write_val(2);
    } else {
      a.STS.write_val(3);
    }
    a.CTL.write_val(x);
    a.STS.write_val(feat_pkg::MODE_SUP);
    // Decided by the declared range alone, whatever the header says.
    if (feat_pkg::SMALL > 300) {
      a.STS.write_val(9);
    }
    w.W.write_val(~feat_pkg::SGN);
    w.W.write_val(feat_pkg::SGN + 1);
    w.W.write_val(feat_pkg::SGN << 2);
  }
}
"""

ARGS = (0x1000, 0x2000)


def _generate(tmp_path, pss=TOP, **opts):
    """Generate ``pss`` for op-model-c; the output directory."""
    return th.compile_model(tmp_path, REGS + pss, "c", **opts)


def _main(out, args=ARGS):
    (out / "main.c").write_text(
        th._C_MAIN.replace("@PRELOAD@", "").replace(
            "@ARGS@", "".join(f", {a:#x}ull" for a in args)))


def _cc(out, flags=(), args=ARGS):
    """Build ``out``'s model and harness; the completed compile."""
    _main(out, args)
    return subprocess.run(
        [th.CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror", *flags,
         "-I", str(out), str(out / "main.c"), str(out / "pss_top.c"),
         "-o", str(out / "run")],
        capture_output=True, text=True, cwd=str(out))


def _trace(out, flags=(), args=ARGS):
    """Build ``out`` with ``flags`` (a `-D`, an `-include`) and run it."""
    res = _cc(out, flags, args)
    assert res.returncode == 0, res.stdout + res.stderr
    run = subprocess.run([str(out / "run")], capture_output=True, text=True,
                         cwd=str(out))
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout.splitlines()


def _w64(v):
    return f"write 64 0x2000 {bits(v, T(64, False)):#x}"


def _expected(opt=0, sgn=5, mode_sup=1):
    """The trace the model gives for these constant values, worked out from
    the LRM (ref8_7), not from pssc."""
    s = Var("SGN", T(32, True), sgn)
    u64 = T(64, False)
    return ["write 32 0x1004 0x2" if opt == 0 else "write 32 0x1004 0x3",
            f"write 32 0x1000 {convert(Var('OPT', T(32, True), opt), T(4, False)):#x}",
            f"write 32 0x1004 {bits(mode_sup, T(32, False)):#x}",
            _w64(convert(Un("~", s), u64)),
            _w64(convert(Bin("+", s, Var("1", T(32, True), 1)), u64)),
            _w64(convert(Bin("<<", s, Var("2", T(32, True), 2)), u64))]


# --- one source, two configurations (the request's central test) -----------

def test_the_model_default_is_the_pss_value(tmp_path):
    assert _trace(_generate(tmp_path)) == _expected()


def test_one_c_file_follows_each_configuration(tmp_path):
    """`-DOPT=0` and `-DOPT=1` against one generated `.c`: the comparison
    `mode != 0 && OPT == 0` takes each branch as the build says."""
    out = _generate(tmp_path)
    assert _trace(out, ["-DOPT=0"]) == _expected(opt=0)
    assert _trace(out, ["-DOPT=1"]) == _expected(opt=1)


def test_a_project_header_included_first_wins(tmp_path):
    out = _generate(tmp_path)
    (out / "project.h").write_text("#define OPT 1\n#define MODE_SUP 0\n")
    assert _trace(out, ["-include", str(out / "project.h")]) == \
        _expected(opt=1, mode_sup=0)


def test_the_comparison_is_not_decided_at_generation_time(tmp_path):
    """No `(0)`/`(1)` in the C: the test reads the macro."""
    text = (_generate(tmp_path) / "pss_top.c").read_text()
    line = next(ln for ln in text.splitlines() if "mode" in ln and "if" in ln)
    assert re.search(r"\(\(int32_t\)\(OPT\)\) == 0", line), line


def test_a_narrowing_assignment_wraps_the_build_value(tmp_path):
    """`bit[4] x = OPT;` with `-DOPT=0x1F` is 0xF (8.7.2)."""
    got = _trace(_generate(tmp_path), ["-DOPT=0x1F"])
    assert got[1] == "write 32 0x1000 0xf"


@pytest.mark.parametrize("sgn", [5, -3, 0x7FFFFFFF, -0x80000000])
def test_operators_in_a_64_bit_context_follow_the_lrm(tmp_path, sgn):
    """`~SGN`, `SGN + 1`, `SGN << 2` written to a 64-bit register, with SGN
    given by `-D` (negative ones included): each as the reference model of
    8.7 computes it. Folded, these were three literals."""
    got = _trace(_generate(tmp_path), [f"-DSGN=({sgn})"])
    assert got[3:] == _expected(sgn=sgn)[3:]


def test_the_declared_range_still_decides_what_it_alone_decides(tmp_path):
    """`SMALL > 300` of a `bit[8]` is false for every value a header can
    give it, so it is decided, and nothing compares a `uint8_t` with 300
    (which `-Wtype-limits`, in `-Wextra`, refuses under `-Werror`)."""
    out = _generate(tmp_path)
    assert "300" not in (out / "pss_top.c").read_text()
    assert _trace(out, ["-DSMALL=200", "-Wtype-limits"]) == _expected()


# --- the definitions ---------------------------------------------------------

def test_each_definition_is_guarded_and_typed(tmp_path):
    h = (_generate(tmp_path) / "pss_top.h").read_text()
    assert "#ifndef OPT\n#define OPT (0)  /* feat_pkg::OPT */\n#endif" in h
    assert "#define SMALL (7u)" in h
    # Only what the bodies spell; SMALL > 300 was decided, but SMALL is
    # still named by no body, and so not defined.
    for name in ("OPT", "SGN", "MODE_SUP"):
        assert f"#define {name} " in h


def test_a_constant_pssc_folded_elsewhere_is_pinned(tmp_path):
    """DEPTH sizes `buf`: overriding it is a compile error naming it, never
    an array of one size and code of another."""
    pss = TOP.replace("a.STS.write_val(feat_pkg::MODE_SUP);",
                      "a.STS.write_val(feat_pkg::DEPTH);")
    out = _generate(tmp_path, pss)
    h = (out / "pss_top.h").read_text()
    assert re.search(r"PSSC_PINNED_CONSTANT\(DEPTH, 2, \"pssc folded DEPTH",
                     h), h
    assert "PSSC_PINNED_CONSTANT(OPT" not in h
    assert _cc(out, ["-DDEPTH=2"]).returncode == 0
    for std in ("-std=c99", "-std=c11"):
        res = _cc(out, ["-DDEPTH=3", std])
        assert res.returncode != 0
        assert "DEPTH" in res.stderr, res.stderr


def test_a_constant_another_was_folded_from_is_pinned(tmp_path):
    """`SPAN = BASE + 4`: SPAN's definition holds BASE's value, so a build
    that changes BASE alone would mix two."""
    pss = """
package lay_pkg {
    static const int BASE = 8;
    static const int SPAN = BASE + 4;
}
component pss_top {""" + """
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    a.STS.write_val(lay_pkg::BASE);
    a.STS.write_val(lay_pkg::SPAN);
  }
}"""
    h = (th.compile_model(tmp_path, REGS + pss, "c") / "pss_top.h").read_text()
    assert "PSSC_PINNED_CONSTANT(BASE, 8," in h
    assert "PSSC_PINNED_CONSTANT(SPAN" not in h


# --- names -------------------------------------------------------------------

_TWO_K = """
package p1_pkg { static const int K = 1; }
package p2_pkg { static const int K = 2; }
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    a.STS.write_val(p1_pkg::K);
    a.STS.write_val(p2_pkg::K);
  }
}"""


def test_two_constants_of_one_name_are_qualified(tmp_path):
    out = th.compile_model(tmp_path, REGS + _TWO_K, "c")
    h = (out / "pss_top.h").read_text()
    assert "#define p1_pkg_K (1)" in h and "#define p2_pkg_K (2)" in h
    assert _trace(out, args=(0x1000,)) == ["write 32 0x1004 0x1",
                                           "write 32 0x1004 0x2"]


def test_a_constant_named_like_a_local_is_qualified(tmp_path):
    """A macro rewrites every token of its name: a local `G` beside `::G`
    is kept apart by qualifying the constant, never by refusing the model."""
    pss = """
static const int G = 7;
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    int G = 1;
    a.STS.write_val(G);
    a.STS.write_val(::G);
  }
}"""
    out = th.compile_model(tmp_path, REGS + pss, "c")
    assert "#define global_G (7)" in (out / "pss_top.h").read_text()
    assert _trace(out, args=(0x1000,)) == ["write 32 0x1004 0x1",
                                           "write 32 0x1004 0x7"]


def test_a_constant_named_like_a_function_is_refused(tmp_path):
    pss = """
package k_pkg { static const int pss_top_run = 3; }
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(k_pkg::pss_top_run); }
}"""
    with pytest.raises(CompileError) as ei:
        th.compile_model(tmp_path, REGS + pss, "c")
    text = diagnostic_text(ei)
    assert "'pss_top_run' is both" in text and "constant" in text, text


class _Prefixed(CStylePolicy):
    def constant_name(self, qname, qualify):
        return "FEAT_" + super().constant_name(qname, qualify)


def test_a_style_spells_the_macro(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(th._PRELUDE + REGS + TOP)
    tgt = targets.get("op-model-c")
    ctx = driver.translate([str(p)], prelude=tgt.prelude(argparse.Namespace()))
    model = om.elaborate(ctx, ctx.type_map["pss_top"], tmp_path / "out")
    s = CSettings(prefix="pss_top", seam_include="pssc_mem_vtable.h")
    be = COpModelBackend(_Prefixed())
    be.prepare(model, s)
    assert "((int32_t)(FEAT_OPT))" in be.impl
    assert "#define FEAT_OPT (0)" in be.header_text(model, s)


# --- the escape hatch and the mutation check ----------------------------------

def test_fold_constants_restores_the_folded_form(tmp_path):
    out = _generate(tmp_path, c_fold_constants=True)
    h, c = ((out / f).read_text() for f in ("pss_top.h", "pss_top.c"))
    assert "#define OPT" not in h and "(OPT)" not in c
    assert _trace(out) == _expected()


def test_the_two_configuration_test_fails_on_folded_output(tmp_path):
    """Mutation check: folded, `-DOPT=1` changes nothing, so the central
    test above would fail. It tests what it says."""
    out = _generate(tmp_path, c_fold_constants=True)
    assert _trace(out, ["-DOPT=1"]) != _expected(opt=1)


def test_the_manifest_lists_the_constants(tmp_path):
    import json
    man = tmp_path / "m.json"
    _generate(tmp_path, pss=TOP.replace(
        "a.STS.write_val(feat_pkg::MODE_SUP);",
        "a.STS.write_val(feat_pkg::DEPTH);"), progseq_manifest=str(man))
    doc = json.loads(man.read_text())
    rows = {c["name"]: c for c in doc["constants"]}
    assert rows["OPT"] == {"name": "OPT", "qname": "feat_pkg::OPT",
                           "value": 0, "type": "int[32]", "pinned": False}
    assert rows["DEPTH"]["pinned"] is True
    assert doc["settings"]["fold_constants"] is False


# --- what pssc used the value of: pinned -----------------------

_USED = """
package u_pkg {
    static const int OFF = 4;
    static const int LO = 4;
    static const int SEL = 2;
}
pure component gu_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> R;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["R"]: return u_pkg::OFF; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gu_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() {
    bit[32] v = 0xF0;
    g.R.write_val(u_pkg::OFF);
    g.R.write_val(v[u_pkg::LO + 3:u_pkg::LO]);
    match (v[7:4]) {
      [u_pkg::SEL]: g.R.write_val(1);
      [14]: g.R.write_val(2);
      default: g.R.write_val(3);
    }
  }
}"""


def test_a_register_offset_and_a_select_bound_are_pinned(tmp_path):
    """An offset function's constant is an address: folded, and pinned
    where a body also spells it. A part select's bounds are its shift and
    mask: folded. A `match` label is spelled, and stays the build's."""
    out = th.compile_model(tmp_path, REGS + _USED, "c")
    h = (out / "pss_top.h").read_text()
    assert "PSSC_PINNED_CONSTANT(OFF, 4," in h, h
    # The bounds are folded and spelled nowhere: nothing to define.
    assert "LO" not in h and "LO" not in (out / "pss_top.c").read_text()
    assert "#define SEL (2)" in h and "PSSC_PINNED_CONSTANT(SEL" not in h
    assert _trace(out, args=(0x1000,)) == [
        "write 32 0x1004 0x4", "write 32 0x1004 0xf", "write 32 0x1004 0x3"]
    assert _trace(out, ["-DSEL=15"], args=(0x1000,))[-1] == \
        "write 32 0x1004 0x1"
    assert _cc(out, ["-DOFF=8"], args=(0x1000,)).returncode != 0


def test_a_header_making_two_match_labels_equal_does_not_compile(tmp_path):
    """The labels are the build's: one that makes two equal is C's
    duplicate-case error, never a silently dead arm."""
    out = th.compile_model(tmp_path, REGS + _USED, "c")
    res = _cc(out, ["-DSEL=14"], args=(0x1000,))
    assert res.returncode != 0 and "duplicate case" in res.stderr, res.stderr
