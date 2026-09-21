"""The C body emitter, construct by construct.

One test per PSS construct the emitter has to translate, each asserting the
emitted text AND compiling it. The two catch different things: the text
assertion catches a translation that is wrong, the compile catches one that is
merely unrepresentable. Neither catches the third kind -- a translation that
compiles and means something else -- which is why `test_build_wb_dma_c.py`
drives the result against a mock and checks the register trace.

ASSERTIONS COME IN PAIRS wherever the defect had plausible-looking output. That
is not a style preference here; it is the shape of this emitter's failures. The
one defect no compiler can see (C0.1, `self.caps` -> bare `caps`) produces C
that compiles wherever any `caps` is in scope, so `has_not=` is the only
structural signal there is.
"""
import argparse
import os
import shutil
import subprocess

import pytest

from pssc import driver

from .conftest import available_c_compilers, diagnostic_text

_MEM = """
package addr_reg_pkg {
}
"""


def _generate(tmp_path, src, root="probe_c", prefix="probe", **kw):
    """Compile one PSS source to C; return (header_text, impl_text)."""
    p = tmp_path / "model.pss"
    p.write_text(src)
    out = tmp_path / "out"
    ns = argparse.Namespace(
        progseq_root=root, c_prefix=prefix, c_link_style="vtable",
        c_reg_style="bitfields", c_header_only=False, progseq_core_copy=True,
        output_dir=str(out), **kw)
    driver.compile([str(p)], target="op-model-c", opts=ns)
    return (out / f"{prefix}.h").read_text(), (out / f"{prefix}.c").read_text(), out


def assert_c(text, *, has=(), has_not=()):
    for frag in has:
        assert frag in text, f"missing from generated C: {frag!r}"
    for frag in has_not:
        assert frag not in text, f"should not appear in generated C: {frag!r}"


def compile_c(out_dir, cc="gcc"):
    """Compile the generated translation unit with warnings fatal."""
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
         "-c", "probe.c", "-o", os.devnull],
        cwd=str(out_dir), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")


# --- C0.1: self-rooted member access ---------------------------------------
#
# The P0, and the only silent one in the set. Everything else here fails at
# generation or at compile; this one produces C that builds and reads the wrong
# memory.

_SELF_MEMBER = """
component probe_c {
    int scale = 3;
    target function int scaled(int x) {
        return scale * x;
    }
}
"""


def test_component_member_reaches_c_through_the_handle(tmp_path):
    _, impl, _ = _generate(tmp_path, _SELF_MEMBER)
    assert_c(impl,
             has=["s->scale * x"],
             # The defect's output. `scale * x` compiles anywhere a `scale` is
             # in scope -- an unrelated file-scope object, or a parameter of
             # the caller -- and silently computes with it.
             has_not=["return scale * x", "= scale;"])


def test_a_bare_member_name_never_survives(tmp_path):
    """The negative stated over the whole file, not just the one expression.

    A partial fix -- the read rewritten, an assignment target missed -- would
    pass the test above. This one fails unless every occurrence went through
    the handle.
    """
    _, impl, _ = _generate(tmp_path, _SELF_MEMBER)
    body = impl.split("probe_scaled")[-1]
    assert "scale" in body                      # the test is not vacuous
    for occurrence in body.split("scale")[:-1]:
        assert occurrence.endswith("s->"), \
            f"unqualified member reference in: {body}"


@needs_cc
def test_self_member_output_compiles(tmp_path):
    _, _, out = _generate(tmp_path, _SELF_MEMBER)
    compile_c(out, _CC[0])


# --- C0.2: unary operators --------------------------------------------------

_UNARY = """
component probe_c {
    target function int f(int x, bool b) {
        int m;
        m = ~x;
        if (!b) {
            m = -x;
        }
        return m;
    }
}
"""


def test_unary_operators(tmp_path):
    _, impl, _ = _generate(tmp_path, _UNARY)
    assert_c(impl, has=["~(x)", "!(b)", "-(x)"])


def test_unary_operand_is_bracketed(tmp_path):
    """`~0` reaching the compiler as `~0` and not as something precedence
    rearranged is the whole point -- pssparser defect D5 already ate a `~` once
    (`write_masked({.ch_en=~0}, ...)` selected no bits), and the mask that
    produced was a write that did nothing."""
    _, impl, _ = _generate(tmp_path, _UNARY)
    assert_c(impl, has_not=["~x;", "!b)", "-x;"])


@needs_cc
def test_unary_output_compiles(tmp_path):
    _, _, out = _generate(tmp_path, _UNARY)
    compile_c(out, _CC[0])


# --- C0.3: break / continue -------------------------------------------------

_LOOPS = """
component probe_c {
    target function int f(int n) {
        int i;
        int acc;
        i = 0;
        acc = 0;
        while (true) {
            i = i + 1;
            if (i > n) {
                break;
            }
            if (i == 3) {
                continue;
            }
            acc = acc + i;
        }
        return acc;
    }
}
"""


def test_break_and_continue(tmp_path):
    _, impl, _ = _generate(tmp_path, _LOOPS)
    assert_c(impl, has=["break;", "continue;", "while (1)"])


def test_while_uses_its_own_condition_field(tmp_path):
    """`StmtWhile.test`, not `.condition`.

    This arm was copied from `StmtRepeatWhile` (which does use `.condition`),
    so every `while` in a model raised AttributeError. Nothing caught it: the
    flat example has no `while`, and the real model could not reach the emitter
    at all until enums lowered.
    """
    _, impl, _ = _generate(tmp_path, _LOOPS)
    assert "while (1) {" in impl and "do {" not in impl


@needs_cc
def test_loop_output_compiles(tmp_path):
    _, _, out = _generate(tmp_path, _LOOPS)
    compile_c(out, _CC[0])


# --- C0.4: foreach ----------------------------------------------------------

_FOREACH = """
component probe_c {
    int vals[4];
    target function int total() {
        int acc;
        acc = 0;
        foreach (vals[i]) {
            acc = acc + vals[i];
        }
        return acc;
    }
}
"""


def test_foreach_becomes_a_bounded_for(tmp_path):
    _, impl, _ = _generate(tmp_path, _FOREACH)
    assert_c(impl,
             has=["for (unsigned i = 0; i < 4u; i++) {", "s->vals[i]"],
             # An unbounded loop, or one whose bound came from nowhere.
             has_not=["i < 0u", "i < -1", "foreach"])


@needs_cc
def test_foreach_output_compiles(tmp_path):
    _, _, out = _generate(tmp_path, _FOREACH)
    compile_c(out, _CC[0])


# --- C0.5: match ------------------------------------------------------------

_MATCH = """
package m_pkg {
    enum colour_e { RED = 0, GREEN = 1, BLUE = 7 }
}
import m_pkg::*;
component probe_c {
    target function int f(colour_e c) {
        int v;
        v = 0;
        match (c) {
            [RED]: { v = 1; }
            [GREEN]: { v = 2; }
        }
        return v;
    }
}
"""


def test_match_becomes_a_switch_with_breaks(tmp_path):
    """Every arm breaks.

    PSS arms do not fall through and C's do. This is the one place in the
    emitter where the two languages' DEFAULTS disagree, so an omitted `break`
    would not fail to compile -- it would quietly execute the next arm too.
    """
    _, impl, _ = _generate(tmp_path, _MATCH)
    assert_c(impl, has=["switch (", "case ", "break;"])
    arms = impl.split("switch (")[1].split("}")[0]
    assert arms.count("break;") >= 2, arms


def test_match_without_a_default_still_gets_one(tmp_path):
    """A `switch` with no default is legal C that silently does nothing, and
    PSS says an unmatched `match` is an error (3.1 §22.7.9). Saying nothing
    would convert a stated error into silence."""
    _, impl, _ = _generate(tmp_path, _MATCH)
    assert "default:" in impl


def test_match_default_can_be_suppressed(tmp_path):
    _, impl, _ = _generate(tmp_path, _MATCH, c_match_default="none")
    assert "switch (" in impl
    assert "default:" not in impl


@needs_cc
def test_match_output_compiles(tmp_path):
    _, _, out = _generate(tmp_path, _MATCH)
    compile_c(out, _CC[0])


# --- C0.6: enums ------------------------------------------------------------

def test_enum_typedef_with_explicit_values(tmp_path):
    """Explicit values, because the model's numbers are a contract.

    `BLUE = 7` is the case that matters: C's implicit numbering would make it
    2, silently, and the enum is a register field encoding.
    """
    hdr, _, _ = _generate(tmp_path, _MATCH)
    assert_c(hdr, has=["typedef enum {", "RED = 0", "GREEN = 1", "BLUE = 7",
                       "} colour_t;"])


def test_enum_typed_argument_is_the_enum_type(tmp_path):
    """The blocker `src/pss/flow.yaml` recorded verbatim: the first enum-typed
    operation argument raised `unsupported C type for DataTypeEnum`."""
    hdr, _, _ = _generate(tmp_path, _MATCH)
    assert_c(hdr, has=["probe_f(probe_t *s, colour_t c)"],
             has_not=["probe_f(probe_t *s, int c)"])


# --- C0.7: built-in calls ---------------------------------------------------

_BUILTINS = """
package addr_reg_pkg {}
import addr_reg_pkg::*;
component probe_c {
    target function int peek(addr_handle_t at) {
        int v;
        v = read32(at);
        write32(make_handle_from_handle(at, 4), v);
        return v;
    }
}
"""


def test_memory_primitives_reach_the_seam(tmp_path):
    _, impl, _ = _generate(tmp_path, _BUILTINS)
    assert_c(impl,
             has=["pssc_r32(pssc_bus(s), at)", "pssc_w32(pssc_bus(s), "],
             # Verbatim emission: C naming a function nothing declares. It is
             # what the registry in targets/call_legality.py exists to prevent.
             has_not=["read32(at)", "write32(", "= read32"])


def test_address_builtins_fold_to_arithmetic(tmp_path):
    """`addr_handle_t` is opaque in PSS and a plain address here, so deriving a
    handle from another is an add -- not a call to a function that would have
    to exist."""
    _, impl, _ = _generate(tmp_path, _BUILTINS)
    assert_c(impl, has=["(at + 4)"], has_not=["make_handle_from_handle"])


def test_an_unknown_name_is_caught_by_the_front_end(tmp_path):
    """A name nothing declares never reaches the emitter -- pssparser rejects
    it first. Recorded so the next reader does not go looking for a lowering
    check that would be unreachable."""
    src = _BUILTINS.replace("v = read32(at);", "v = mystery_helper(at);")
    with pytest.raises(Exception) as exc:
        _generate(tmp_path, src)
    # The driver renders the front end's markers into `.errors`; the summary
    # line only counts them.
    text = "\n".join(getattr(exc.value, "errors", [])) or str(exc.value)
    assert "mystery_helper" in text


def test_a_declared_builtin_with_no_c_rendering_is_rejected(tmp_path):
    """The registry's whole purpose, on the case that can actually happen.

    `urandom` is declared by PSS (std_pkg 21.4) and rendered by the SV target,
    so it passes the front end and arrives here. The C target claims no
    rendering for it -- there is no solver and no RNG in this image -- and the
    only safe answer is to RAISE. Emitting it verbatim exits 0 and produces a
    translation unit that fails to link, much later and somewhere else.

    The refusal now comes from the `validate_calls` GATE rather than from the
    body emitter (P1.T1): it runs before any file is opened and reports every
    offending call at once, where the emitter aborted at the first and left a
    partly-written header behind. The emitter's own check remains as the
    backstop for a call the gate vouched for -- reaching it is a compiler bug.
    """
    src = _BUILTINS.replace("v = read32(at);", "v = urandom();")
    with pytest.raises(Exception) as exc:
        _generate(tmp_path, src)
    msg = diagnostic_text(exc)
    assert "urandom" in msg
    # It must say WHY, not just that it cannot: the reason is what tells the
    # reader whether to change the model or the compiler.
    assert "PRNG" in msg or "no lowering" in msg or "verbatim" in msg
    assert not (tmp_path / "out" / "probe.h").exists(), (
        "the gate must refuse before any file is written")


@needs_cc
def test_builtin_output_compiles(tmp_path):
    _, _, out = _generate(tmp_path, _BUILTINS)
    compile_c(out, _CC[0])


# --- C0.9: yield ------------------------------------------------------------

_YIELD = """
component probe_c {
    target function void spin(int n) {
        int i;
        i = 0;
        while (i < n) {
            i = i + 1;
            yield;
        }
    }
}
"""


def test_yield_is_lowered_not_rejected(tmp_path):
    """Lowered, and by default to nothing.

    Rejecting `yield` was considered and withdrawn. A model reaches it on this
    profile precisely BECAUSE it has no event to wait on (see
    `wb_dma_ch_c/functions/wait_hint.pss`), so refusing would refuse the one
    wait a firmware target can actually perform. On a single-threaded part the
    honest cost of "let something else run" is zero.
    """
    _, impl, _ = _generate(tmp_path, _YIELD)
    assert "probe_spin" in impl
    assert "yield" in impl                       # the comment marking the point
    assert "yield_();" not in impl               # but no call, by default


def test_yield_can_call_an_import(tmp_path):
    """For a target that wants to charge for a spin -- a WFI, a watchdog kick."""
    _, impl, _ = _generate(tmp_path, _YIELD, c_yield="import")
    assert "yield_();" in impl


@needs_cc
def test_yield_output_compiles(tmp_path):
    _, _, out = _generate(tmp_path, _YIELD)
    compile_c(out, _CC[0])
