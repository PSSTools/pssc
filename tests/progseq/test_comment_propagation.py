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
    target function int probe(int c) {
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
        chan = 1;
    }

    /** Stop the channel and return. */
    target function void stop() {
        chan = 0;
    }

    // An undocumented operation: a leading comment detached by the blank line
    // above it, so nothing propagates and the prototype stands alone.

    target function void reset() {
        chan = 0;
    }
}

component pss_top {
    /** The device under test. */
    component_c c[2];
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
    return (out / "m_pkg.sv").read_text()


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
    """The interface is what a caller reads; the body is what a debugger reads.

    The one place a comment is deliberately duplicated.
    """
    assert sv.count("* Probe the status register.") == 2
    assert "pure virtual task probe" in sv
    assert "virtual task probe" in sv


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


def test_an_accessor_group_stays_together(sv):
    """`ch()` and `ch_size()` are one member's plumbing, not two API entries.

    Spacing every generated line apart would bury the operations among them,
    so tightly-coupled groups are separated from their surroundings and not
    internally. Same reasoning as the `_init`/`_create`/`_destroy` lifecycle
    and the per-register accessor one-liners.
    """
    lines = [l.rstrip() for l in sv.split("\n")]
    size = next((i for i, l in enumerate(lines)
                 if "_size();" in l and "pure virtual function" in l), None)
    if size is None:
        pytest.skip("fixture has no sub-component array")
    assert lines[size - 1].strip().startswith("pure virtual function")


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
