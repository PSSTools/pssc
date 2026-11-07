"""PSS comments reaching the generated SystemVerilog and C.

The operation model is a close transcription of its PSS source, so the prose
belongs in the output at the same points -- it is the part a reader cannot
recover from the code. See docs/design/pss-comment-propagation-plan.md in fw-wb-dma.

Two things are asserted throughout, and the second matters more than the first:

1. A comment appears, at the right place.
2. ``--no-comments`` reproduces the pre-comment output **byte for byte**. That
   is what makes "only comments moved" a measured claim. Every emitter change
   here is guarded by it.
"""
import argparse

import pytest

import zuspec.ir.core as ir

from pssc import driver
from pssc.targets.c import lower_reg_model as c_reg
from pssc.targets.sv import lower_reg_model as sv_reg
from pssc.targets.comments import (BLOCK, HASH, LINE, append_trailing,
                                   comment_lines, doc_block)
from .sv_text import sv_package_text

SRC = '''
/** A channel of the device. */
component component_c {

    /** Which channel this is. */
    int chan;

    /**
     * Probe the status register.
     *
     * Completes when the read returns.
     */
    private target function int probe(int c) {
        // Read the status word.
        int s = 0;

        s = c + 1;          // fold in the channel

        if (s > 0) {
            // Nested two levels deep.
            s = 0;
        }

        // A note that is deliberately detached.

        return s;
    }

    /** Arm the channel and return. */
    target function void arm() {
        int was = chan + 1;
    }

    /** Stop the channel and return. */
    target function void stop() {
        int was = chan;
    }

    // An undocumented operation: a leading comment detached by the blank line
    // above it, so nothing propagates and the prototype stands alone.

    target function void reset() {
        int was = chan;
    }
}

component pss_top {
    /** The device under test. */
    component_c c[2];

    int armed;

    /** Arm the first channel. */
    target function void arm_first() {
        int was = armed + 1;
    }

    target function void stop_first() {
        int was = armed;
    }
}
'''


def _write(tmp_path):
    path = tmp_path / "m.pss"
    path.write_text(SRC)
    return [str(path)]


def _gen(tmp_path, target, no_comments=False, **extra):
    out = tmp_path / ("nc" if no_comments else "c")
    out.mkdir()
    ns = argparse.Namespace(progseq_root="pss_top", output_dir=str(out),
                            no_comments=no_comments, **extra)
    driver.compile(_write(tmp_path), target=target, opts=ns)
    return out


@pytest.fixture(scope="module")
def sv(tmp_path_factory):
    out = _gen(tmp_path_factory.mktemp("sv"), "op-model-sv",
               progseq_package="m_pkg")
    return sv_package_text(out / "m_pkg.sv")


@pytest.fixture(scope="module")
def c(tmp_path_factory):
    """Header and definitions together.

    The declarations and the bodies land in different files, and the prose
    goes to both: the doc block onto the prototype a caller reads, the
    statement comments into the definition a debugger reads.
    """
    out = _gen(tmp_path_factory.mktemp("c"), "op-model-c", c_prefix="m")
    return "\n".join((out / n).read_text() for n in ("m.h", "m.c"))


# --- what reaches the output ------------------------------------------------

@pytest.mark.parametrize(
    "frag",
    [
        "* Probe the status register.",      # function doc, on the declaration
        "* Completes when the read returns.",
        "// Read the status word.",          # leading, on a statement
        "// fold in the channel",            # trailing, on its own statement
        "// Nested two levels deep.",        # inside a nested body
        "* A channel of the device.",        # the component
        "// Which channel this is.",         # a data member
    ],
)
def test_the_prose_reaches_the_systemverilog(sv, frag):
    assert frag in sv


@pytest.mark.parametrize(
    "frag",
    [
        "* Probe the status register.",
        "/* Read the status word. */",       # C house style is /* */ throughout
        "/* fold in the channel */",
        "/* Nested two levels deep. */",
        "* A channel of the device.",
        "/* Which channel this is. */",
    ],
)
def test_the_prose_reaches_the_c(c, frag):
    assert frag in c


def test_the_c_output_keeps_its_block_comment_house_style(c):
    """Every comment the C target emitted before this was `/* */`.

    C11 permits `//`, so this is style rather than legality -- but a generated
    file that mixes the two for no reason reads as two generators.
    """
    for line in c.split("\n"):
        assert not line.lstrip().startswith("//"), line


# --- where it lands ---------------------------------------------------------

def test_a_statement_comment_sits_above_its_own_statement(sv):
    lines = [l.strip() for l in sv.split("\n")]
    i = lines.index("// Read the status word.")
    assert lines[i + 1].startswith("int s")


def test_a_trailing_comment_stays_on_its_statement_line(sv):
    hit = [l for l in sv.split("\n") if "fold in the channel" in l]
    assert len(hit) == 1
    assert hit[0].strip().startswith("s = ")


def test_the_doc_block_is_on_both_the_prototype_and_the_implementation(sv):
    """The context interface is what a caller reads; the body is what a
    debugger reads. The one place a comment is deliberately duplicated -- and
    only for a function on the API, since only those have a prototype there
    (`probe` is private).
    """
    assert sv.count("/** Arm the first channel. */") == 2
    assert "pure virtual task arm_first" in sv
    assert "virtual task arm_first" in sv
    assert sv.count("* Probe the status register.") == 1


def _doc_blocks_butting_against_code(text, opener, indented=True):
    """Lines where a *declaration's* doc block starts right after code.

    Three things are deliberately not flagged:

    * comment-after-comment -- a file banner is several consecutive block
      comments, and a section marker introduces the declaration under it;
    * statement comments, which belong against the statement above them; that
      is how they read in the PSS source, and spacing every statement apart
      would be worse than the problem;
    * struct-member comments, same reason.

    In SystemVerilog the two are already distinct: `/**` is only ever a
    declaration's doc block, statements use `//`. In C everything is `/* */`,
    so *indented=False* restricts the search to column 0, which is where
    function prototypes and definitions live and where statements never do.
    """
    bad = []
    lines = [l.rstrip() for l in text.split("\n")]
    for i, line in enumerate(lines):
        if not i:
            continue
        candidate = line.strip() if indented else line
        if not candidate.startswith(opener):
            continue
        prev = lines[i - 1].strip()
        if prev.endswith((";", "}", "{")) and not prev.startswith(("//", "/*", "*")):
            bad.append((i + 1, prev))
    return bad


def test_no_sv_doc_block_butts_against_a_declaration(sv):
    """A doc block butted against the previous declaration reads as part of it.

    Worst in the interface class, which is nothing but prototypes: without a
    blank line the eye has no break between the end of one operation's
    signature and the start of the next one's prose. Single-line blocks count
    too -- `/** Arm the channel. */` after a prototype has the same problem and
    is easier to miss.
    """
    assert _doc_blocks_butting_against_code(sv, "/**") == []


def test_no_c_doc_block_butts_against_a_declaration(c):
    assert _doc_blocks_butting_against_code(c, "/*", indented=False) == []


def test_undocumented_prototypes_are_spaced_too(sv):
    """The separation belongs to the declaration, not to its documentation.

    `notify_irq()` has no doc comment, so keying the blank line off the doc
    block left it butted against the prototype above it. An undocumented
    function needs the break as much as a documented one.
    """
    lines = [l.rstrip() for l in sv.split("\n")]
    protos = [i for i, l in enumerate(lines)
              if l.strip().startswith("pure virtual task ")]
    assert len(protos) > 1, "fixture must have adjacent prototypes to test"
    for i in protos:
        prev = lines[i - 1].strip()
        assert prev == "" or prev.endswith("*/"), (
            "prototype at line %d runs into %r" % (i + 1, prev))


def test_a_detached_comment_does_not_propagate(sv, c):
    """A blank line before the statement detaches the comment.

    This is not an omission -- it is the mechanism. It is how a file note above
    the imports stays out of the generated code, and how an author suppresses
    any one comment without deleting it.
    """
    assert "deliberately detached" not in sv
    assert "deliberately detached" not in c


# --- the regression gate ----------------------------------------------------

@pytest.mark.parametrize(
    "target,fname,extra",
    [
        ("op-model-sv", "m_pkg.sv", {"progseq_package": "m_pkg"}),
        ("op-model-c", "m.c", {"c_prefix": "m"}),
    ],
)
def test_no_comments_output_contains_none_of_the_prose(tmp_path, target, fname, extra):
    text = (_gen(tmp_path, target, no_comments=True, **extra) / fname).read_text()
    for frag in ("Probe the status register", "Read the status word",
                 "fold in the channel", "Which channel this is",
                 "A channel of the device"):
        assert frag not in text


# --- register value structs -------------------------------------------------
#
# A separate emitter from the operation bodies (`lower_reg_model.py`), and the
# only place a declaration carries comments in *both* positions at once: the
# SystemRDL `desc` above the member, the bit range and access mode beside it.
#
# Exercised against the emitters directly rather than through a generated
# model: the register package under `examples/op_model/` is a checked-in
# snapshot, so an end-to-end test here would assert against whenever it was
# last synced rather than against the emitter.

def _reg_struct(doc=None, doc_trailing=None):
    return ir.DataTypeStruct(
        name="regs_pkg::m_csr_s",
        super=None,
        fields=[
            ir.Field(name="ch_en", datatype=ir.DataTypeInt(bits=1, signed=False),
                     kind=ir.FieldKind.Field, doc=doc, doc_trailing=doc_trailing),
            ir.Field(name="rsvd_1", datatype=ir.DataTypeInt(bits=31, signed=False),
                     kind=ir.FieldKind.Field),
        ],
    )


_DOC = "Channel enable. Software sets this to arm the channel."
_TRAIL = "[0] sw=rw hw=r reset=0x0"


def test_a_register_field_carries_both_of_its_comments_to_sv():
    lines = [l.strip() for l in
             sv_reg.emit_value_struct(_reg_struct(_DOC, _TRAIL)).split("\n")]
    i = lines.index("// " + _DOC)
    assert lines[i + 1] == "bit ch_en;   // " + _TRAIL


def test_a_register_field_carries_both_of_its_comments_to_c():
    lines = [l.strip() for l in
             c_reg.emit_value_union(_reg_struct(_DOC, _TRAIL)).split("\n")]
    i = lines.index("/* %s */" % _DOC)
    assert "ch_en" in lines[i + 1]
    assert lines[i + 1].endswith("/* %s */" % _TRAIL)


def test_without_comments_the_c_falls_back_to_the_synthesized_bit_range():
    """The layout facts are re-derivable, so the emitter still states them.

    This is what keeps `--no-comments` byte-identical to the output from before
    any of this existed, rather than merely comment-free.
    """
    text = c_reg.emit_value_union(_reg_struct())
    decl = next(l for l in text.split("\n") if "ch_en" in l)
    assert decl.strip().endswith("/* [0:0] */")
    assert "Channel enable" not in text


def test_without_comments_the_sv_struct_member_is_bare():
    text = sv_reg.emit_value_struct(_reg_struct())
    assert "    bit ch_en;\n" in text
    assert "//" not in text


def test_the_prose_goes_above_and_the_layout_facts_beside():
    """Two positions, two kinds of fact. The prose is what nothing downstream
    can re-derive; the bit range is what a reader scanning the struct wants on
    the line itself."""
    decl = next(l for l in sv_reg.emit_value_struct(
        _reg_struct(_DOC, _TRAIL)).split("\n") if "bit ch_en" in l)
    assert _TRAIL in decl
    assert _DOC not in decl


# --- the rendering helpers --------------------------------------------------

def test_a_close_comment_marker_forces_line_comments():
    """`*/` inside the prose cannot be wrapped in a block comment.

    Escaping it would either corrupt the text or read as an escape rather than
    as prose, so the text falls back to `//`, which carries anything.
    """
    out = doc_block("ends a comment with */ here", "", BLOCK)
    assert out == ["// ends a comment with */ here"]

    out = comment_lines("a */ b", "", BLOCK)
    assert out == ["// a */ b"]


def test_a_blank_line_in_a_doc_block_stays_blank():
    """Otherwise a paragraph break renders as a line reading `//`."""
    assert doc_block("one\n\ntwo", "", LINE) == [
        "/**", " * one", " *", " * two", " */"]


def test_a_statement_that_lowers_to_nothing_takes_its_comment_with_it():
    assert append_trailing([], "note") == []


def test_a_multi_line_trailing_comment_moves_above_the_statement():
    """It cannot ride on the end of a line, and dropping it would lose it."""
    assert append_trailing(["  x = 1;"], "one\ntwo") == [
        "  // one", "  // two", "  x = 1;"]


def test_unicode_survives():
    assert comment_lines("µs and é", "", LINE) == ["// µs and é"]


# --- the `#` style (P1.T4) --------------------------------------------------
#
# Added ahead of any `#` backend: a Python operation-model style is what the
# extension work is for, and the comment renderer is shared infrastructure that
# should not be the thing an out-of-tree style has to reimplement.

def test_hash_style_renders_line_comments():
    assert comment_lines("hello", "    ", HASH) == ["    # hello"]


def test_hash_style_keeps_a_blank_line_blank():
    """A paragraph break in a doc comment must survive as a paragraph break,
    not become a line containing a bare `#`."""
    assert comment_lines("a\n\nb", "", HASH) == ["# a", "#", "# b"]


def test_hash_style_needs_no_close_marker_escape():
    """`*/` is ordinary text in a `#` comment. The LINE/BLOCK styles carry an
    escape hatch for it because a block comment cannot; this one must not
    inherit the special case and mangle the prose."""
    assert comment_lines("a */ b", "", HASH) == ["# a */ b"]


def test_hash_doc_block_is_the_same_as_an_ordinary_comment():
    """`#` has no documentation form distinct from its ordinary one, and
    pretending otherwise would emit something no `#` language reads."""
    text = "what it does\n\nand why"
    assert doc_block(text, "", HASH) == comment_lines(text, "", HASH)


def test_hash_trailing_comment_stays_on_its_line():
    assert append_trailing(["x = 1"], "why", HASH) == ["x = 1   # why"]


def test_hash_multi_line_trailing_comment_moves_above():
    """A multi-line comment cannot ride on the end of a line in any style."""
    out = append_trailing(["    x = 1"], "one\ntwo", HASH)
    assert out == ["    # one", "    # two", "    x = 1"]


def test_the_other_styles_are_unchanged_by_the_addition():
    """The renderer is shared with two shipping backends; the golden snapshots
    cover the generated output, and this covers the primitive directly."""
    assert comment_lines("hi", "", LINE) == ["// hi"]
    assert comment_lines("hi", "", BLOCK) == ["/* hi */"]
    assert doc_block("hi", "", LINE) == ["/** hi */"]
    assert doc_block("a */ b", "", BLOCK) == ["// a */ b"]
    assert append_trailing(["x;"], "c", BLOCK) == ["x;   /* c */"]


# --- detached comments, kept on request (`keep_detached_comments`) ----------
#
# Off by default: a blank line is how an author keeps a note out of the
# generated code. A project whose reviewers read the generated code against
# its source -- reasoning blocks, commented-out calls -- turns it on, per
# compile (`--keep-detached-comments`) or per target (the class attribute).

DETACHED_SRC = '''
// A file note the option must not carry.

pure component dev_regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  reg_c<bit[32], READWRITE, 32> STS;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "CTL") {
      return 0x0;
    } else if (name == "STS") {
      return 0x4;
    }
    return 0xFFFFFFFFFFFFFFFF;
  }
}

component pss_top {
  dev_regs_c regs;

  // A declaration note the option must not carry.

  int count;

  solve function void initialize(addr_handle_t base) {
    regs.set_handle(base);
  }

  target function void kick(bit[32] v) {
    regs.CTL.write_val(v);
  }

  target function void run() {
    regs.CTL.write_val(1);

    // Detached block: the reset value stays.
    // kick(0);

    // Consecutive block one.

    // Consecutive block two.
    // Leading comment of the write.
    regs.STS.write_val(2);
    if (count == 0) {
      regs.CTL.write_val(3);

      // Trailing block of the if clause.
      // kick(4);
    } else {
      regs.CTL.write_val(5);
    }
    if (count != 0) {
      // A clause holding nothing but a note.
    }
    repeat (2) {
      regs.STS.write_val(6);

      // Trailing block of the loop body.
    }

    // Trailing block of the function.
    // kick(7);
  }
}
'''

#: Every detached block, in source order.
_BLOCKS = ("Detached block: the reset value stays.", "kick(0);",
           "Consecutive block one.", "Consecutive block two.",
           "Leading comment of the write.",
           "Trailing block of the if clause.", "kick(4);",
           "A clause holding nothing but a note.",
           "Trailing block of the loop body.",
           "Trailing block of the function.", "kick(7);")

#: Hand-written: `count` is 0, so the first `if` takes its first clause and
#: the second does nothing.
_TRACE = ["write 32 0x1000 0x1", "write 32 0x1004 0x2",
          "write 32 0x1000 0x3", "write 32 0x1004 0x6",
          "write 32 0x1004 0x6"]

_DETACHED_TARGETS = ("c", "cpp", "py", "sv")


def _detached_text(tmp_path, target, **opts):
    """Every generated file of ``target`` for DETACHED_SRC, concatenated."""
    from . import trace_harness as th
    out = th.compile_model(tmp_path, DETACHED_SRC, target, **opts)
    return "\n".join(p.read_text() for p in sorted(out.iterdir())
                     if p.suffix in (".c", ".h", ".hpp", ".py", ".sv", ".svh")
                     and not p.name.startswith("pssc_"))


@pytest.fixture(scope="module")
def detached(tmp_path_factory):
    """``{(target, keep): generated text}``; keep None is the default."""
    out = {}
    for tgt in _DETACHED_TARGETS:
        for keep in (None, False, True):
            d = tmp_path_factory.mktemp(f"det_{tgt}_{keep}")
            extra = {} if keep is None else {"keep_detached_comments": keep}
            out[(tgt, keep)] = _detached_text(d, tgt, **extra)
    return out


@pytest.mark.parametrize("target", _DETACHED_TARGETS)
def test_detached_default_is_off(detached, target):
    """Off unless asked for; the goldens hold the rest of the output to
    what it was."""
    assert detached[(target, None)] == detached[(target, False)]
    attached = ("Consecutive block two.", "Leading comment of the write.")
    for frag in _BLOCKS:
        if frag not in attached:
            assert frag not in detached[(target, None)], frag


@pytest.mark.parametrize("target", _DETACHED_TARGETS)
def test_detached_each_block_appears_once_in_source_order(detached, target):
    text = detached[(target, True)]
    for frag in _BLOCKS:
        assert text.count(frag) == 1, (frag, text.count(frag))
    at = [text.index(f) for f in _BLOCKS]
    assert at == sorted(at), list(zip(_BLOCKS, at))


@pytest.mark.parametrize("target", _DETACHED_TARGETS)
def test_detached_file_and_declaration_notes_stay_dropped(detached, target):
    text = detached[(target, True)]
    assert "file note" not in text
    assert "declaration note" not in text


def test_detached_runs_are_separate_c_comments(detached):
    """C writes every comment as `/* */`: two runs a blank line kept apart
    are two comments, not one comment with a blank line in it."""
    c = detached[("c", True)]
    assert ("    /*\n"
            "     * Detached block: the reset value stays.\n"
            "     * kick(0);\n"
            "     */\n"
            "\n"
            "    /* Consecutive block one. */\n"
            "\n"
            "    /*\n"
            "     * Consecutive block two.\n"
            "     * Leading comment of the write.\n"
            "     */\n") in c


_COMMENT_START = ("//", "/*", "*", "#")


def _next_code_line(lines, i):
    """The first line after ``lines[i]`` that is neither blank nor comment."""
    for line in lines[i + 1:]:
        s = line.strip()
        if s and not s.startswith(_COMMENT_START):
            return line
    return ""


def _indent(line):
    return len(line) - len(line.lstrip())


@pytest.mark.parametrize("target", _DETACHED_TARGETS)
@pytest.mark.parametrize("frag,closes", [
    ("Trailing block of the if clause.", "else"),
    ("A clause holding nothing but a note.", None),
    ("Trailing block of the loop body.", None),
    ("Trailing block of the function.", None),
])
def test_detached_trailing_blocks_end_their_block(detached, target, frag,
                                                  closes):
    """A block's trailing note -- an `if` clause's, a loop body's, the
    function's -- is the last thing in that block: what follows it closes
    the block (Python: less indented)."""
    lines = detached[(target, True)].split("\n")
    i = next(k for k, l in enumerate(lines) if frag in l)
    nxt = _next_code_line(lines, i)
    if target == "py" and nxt.strip() == "pass":
        # A suite holding only a note still needs a statement.
        assert _indent(nxt) == _indent(lines[i])
        nxt = _next_code_line(lines, lines.index(nxt, i))
    assert _indent(nxt) < _indent(lines[i]), (lines[i], nxt)
    if closes:
        assert closes in nxt, nxt


@pytest.fixture(scope="module")
def detached_traces(tmp_path_factory):
    """``{(target, keep): trace}`` from the trace harness, with the option
    taken from the target CLASS (`OpModelTarget.keep_detached_comments`), as
    a plug-in sets it."""
    from . import trace_harness as th
    from pssc.targets.op_model import OpModelTarget
    builds, keys = [], []
    mp = pytest.MonkeyPatch()
    try:
        for keep in (False, True):
            mp.setattr(OpModelTarget, "keep_detached_comments", keep)
            for tgt in _DETACHED_TARGETS:
                if th.available(tgt):
                    continue
                d = tmp_path_factory.mktemp(f"trace_{tgt}_{keep}")
                builds.append(th.prepare(tgt, d, DETACHED_SRC,
                                          args=(0x1000,)))
                keys.append((tgt, keep))
    finally:
        mp.undo()
    return dict(zip(keys, th.run_many(builds)))


@pytest.mark.parametrize("target", [
    pytest.param("c", marks=pytest.mark.c_toolchain),
    pytest.param("cpp", marks=pytest.mark.c_toolchain),
    "py",
    pytest.param("sv", marks=pytest.mark.sim)])
def test_detached_bus_trace_is_unchanged(detached_traces, target):
    """Comments are not behaviour: the same accesses in the same order with
    the option on as off, and both the hand-written trace. The generated
    code builds with a comment-only block in it (Python's needs `pass`)."""
    from . import trace_harness as th
    why = th.available(target)
    if why:
        pytest.skip(why)
    off, on = detached_traces[(target, False)], detached_traces[(target, True)]
    for got in (off, on):
        if isinstance(got, BaseException):
            raise got
    assert off == _TRACE
    assert on == off


def test_detached_class_attribute_reaches_the_compile(tmp_path, monkeypatch):
    """A target turns the option on for itself; an explicit False wins."""
    from pssc.targets.op_model import OpModelTarget
    monkeypatch.setattr(OpModelTarget, "keep_detached_comments", True)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert "kick(7);" in _detached_text(tmp_path / "a", "c")
    assert "kick(7);" not in _detached_text(
        tmp_path / "b", "c", keep_detached_comments=False)


def test_detached_cli_flag(tmp_path):
    from pssc.cli import main
    from . import trace_harness as th
    p = tmp_path / "m.pss"
    p.write_text(th._PRELUDE + DETACHED_SRC)
    for flag, d in (([], "off"), (["--keep-detached-comments"], "on")):
        rc = main(["compile", str(p), "-t", "op-model-c", "--root", "pss_top",
                   "--prefix", "m", "-o", str(tmp_path / d), "-q", *flag])
        assert rc == 0
    assert "kick(7);" not in (tmp_path / "off" / "m.c").read_text()
    assert "kick(7);" in (tmp_path / "on" / "m.c").read_text()


def test_detached_note_the_default_drops_is_kept_on_request(tmp_path):
    """The note `test_a_detached_comment_does_not_propagate` pins as dropped
    is the one the option keeps."""
    out = _gen(tmp_path, "op-model-c", c_prefix="m",
               keep_detached_comments=True)
    assert "/* A note that is deliberately detached. */" in \
        (out / "m.c").read_text()


def test_detached_runs_render_apart_in_every_style():
    """A `RUN_BREAK` line ends one comment and starts the next; an empty
    line is still a paragraph break inside one."""
    from pssc.targets.comments import RUN_BREAK
    text = "\n".join(["a", "", "b", RUN_BREAK, "c"])
    assert comment_lines(text, "", BLOCK) == [
        "/*", " * a", " *", " * b", " */", "", "/* c */"]
    assert comment_lines(text, "", LINE) == ["// a", "//", "// b", "", "// c"]
    assert comment_lines(text, "", HASH) == ["# a", "#", "# b", "", "# c"]
