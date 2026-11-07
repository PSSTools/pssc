"""Comments crossing from the PSS AST into the IR.

The layer between `pssparser`'s comment model and the back ends. Asserted
separately from emission because a defect here is invisible in one target and
wrong in all of them.
"""
import dataclasses as dc

import pytest

import zuspec.ir.core as ir
from pssc.ast2ir import AstToIrTranslator
from pssc.frontend import Parser

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
}

component pss_top {
    component_c c;
}
'''


def _translate(collect_comments=True):
    p = Parser(collect_comments=collect_comments)
    p.parses([("m.pss", SRC)])
    ctx = AstToIrTranslator().translate(p.link())
    assert not ctx.errors, ctx.errors
    return ctx


@pytest.fixture(scope="module")
def ctx():
    return _translate()


@pytest.fixture(scope="module")
def probe(ctx):
    comp = ctx.type_map["component_c"]
    return next(f for f in comp.functions if f.name == "probe")


def test_a_function_doc_lands_on_the_ir_function(probe):
    assert probe.doc == (
        "Probe the status register.\n\nCompletes when the read returns.")


def test_a_component_doc_lands_on_the_ir_type(ctx):
    assert ctx.type_map["component_c"].doc == "A channel of the device."


def test_a_field_doc_lands_on_the_ir_field(ctx):
    comp = ctx.type_map["component_c"]
    chan = next(f for f in comp.fields if f.name == "chan")
    assert chan.doc == "Which channel this is."


def test_a_leading_statement_comment_lands_on_its_statement(probe):
    assert probe.body[0].comment == "Read the status word."


def test_a_trailing_statement_comment_lands_in_its_own_slot(probe):
    """Distinct from `comment`, because they occupy different positions and a
    back end has to place them differently."""
    assign = probe.body[1]
    assert assign.comment is None
    assert assign.comment_trailing == "fold in the channel"


def test_a_comment_two_levels_deep_reaches_its_statement(probe):
    """`_translate_statement` is a single dispatch, so nesting is free -- but
    only as long as it stays single."""
    stmt_if = probe.body[2]
    assert stmt_if.body[0].comment == "Nested two levels deep."


def test_a_detached_comment_reaches_nothing(probe):
    for s in probe.body:
        assert s.comment != "A note that is deliberately detached."
        assert s.comment_trailing != "A note that is deliberately detached."


def test_without_collection_every_slot_is_empty():
    """`--no-comments` has to produce the IR that existed before this feature,
    not merely output that looks similar."""
    ctx = _translate(collect_comments=False)
    comp = ctx.type_map["component_c"]
    assert comp.doc is None
    assert all(f.doc is None for f in comp.fields)
    probe = next(f for f in comp.functions if f.name == "probe")
    assert probe.doc is None
    assert all(s.comment is None and s.comment_trailing is None
               for s in probe.body)


def test_the_new_fields_are_ordinary_dataclass_fields(probe):
    """IR serialization is generic over `dc.fields`, so these ride along with
    no serializer change -- provided they really are declared fields."""
    assert "doc" in {f.name for f in dc.fields(ir.Function)}
    assert {"comment", "comment_trailing"} <= {f.name for f in dc.fields(ir.Stmt)}
    assert "doc" in {f.name for f in dc.fields(type(probe.body[0]))}


DOXY_SRC = '''
component pss_top {
    /// A function documented with `///`.
    target function void f() {
        int i;
        /// Step: Reset the moderator
        i = 0;
        //! Bang form
        i = 1;
        /*! Block bang form */
        i = 2;
        /*!
         * Block bang, its own line
         */
        i = 3;
        i = 4;  ///< trailing doc
        i = 5;  //!< trailing bang
        i = 6;  /**< trailing block doc */
        // / a slash that is prose
        i = 7;
        ////////
        i = 8;
        ///   indented
        i = 9;
    }
}
'''


def test_a_doxygen_marker_is_not_prose():
    """pssparser leaves `///`'s third `/` in the text, and every target
    printed it: `/* / Step: Reset the moderator */`."""
    p = Parser(collect_comments=True)
    p.parses([("m.pss", DOXY_SRC)])
    ctx = AstToIrTranslator().translate(p.link())
    assert not ctx.errors, ctx.errors
    f = next(f for f in ctx.type_map["pss_top"].functions if f.name == "f")
    assert f.doc == "A function documented with `///`."
    got = [(s.comment, s.comment_trailing) for s in f.body[1:]]
    assert got == [
        ("Step: Reset the moderator", None),
        ("Bang form", None),
        ("Block bang form", None),
        ("Block bang, its own line", None),
        (None, "trailing doc"),
        (None, "trailing bang"),
        (None, "trailing block doc"),
        ("/ a slash that is prose", None),
        ("//////", None),
        ("  indented", None),
    ]


# --- detached comments, kept (`keep_detached_comments`) ---------------------

DETACHED_SRC = '''
component pss_top {
    target function int f(int n) {
        int s = 0;

        // Detached, line one.
        // Detached, line two.

        /* A second detached run. */

        // Leading.
        s = n;
        if (n > 0) {
            s = 1;

            // At the end of the clause.
        }
        return s;

        // At the end of the body.
    }
}
'''


def _detached(keep):
    p = Parser(collect_comments=True)
    p.parses([("m.pss", DETACHED_SRC)])
    ctx = AstToIrTranslator(keep_detached_comments=keep).translate(p.link())
    assert not ctx.errors, ctx.errors
    return next(f for f in ctx.type_map["pss_top"].functions
                if f.name == "f")


def test_detached_runs_join_the_statement_comment_each_its_own():
    """Each run the source kept apart is its own comment: separated by
    `RUN_BREAK`, never by an empty line, which is a paragraph break inside
    one comment."""
    from pssc.targets.comments import RUN_BREAK
    f = _detached(True)
    assert f.body[1].comment == "\n".join([
        "Detached, line one.", "Detached, line two.", RUN_BREAK,
        "A second detached run.", RUN_BREAK, "Leading."])


def test_detached_off_is_the_ir_there_was():
    f = _detached(False)
    assert f.body[1].comment == "Leading."
    assert [type(s).__name__ for s in f.body] == [
        "StmtAnnAssign", "StmtAssign", "StmtIf", "StmtReturn"]
    assert f.body[2].body[-1].comment is None


def test_detached_trailing_comment_is_a_comment_only_pass():
    """No IR kind of its own: a `StmtPass`, which every analysis already
    treats as doing nothing, marked by its provenance."""
    from pssc.targets.comments import COMMENT_ONLY, is_comment_only
    f = _detached(True)
    tail = f.body[-1]
    assert isinstance(tail, ir.StmtPass)
    assert tail.comment == "At the end of the body."
    assert tail.provenance.pass_name == COMMENT_ONLY
    assert is_comment_only(tail)
    assert tail.loc is not None and tail.loc.line == 20
    clause = f.body[2].body
    assert is_comment_only(clause[-1])
    assert clause[-1].comment == "At the end of the clause."
    assert not is_comment_only(ir.StmtPass())


def test_detached_comment_only_statements_compare_by_their_comment():
    """`comment` and `provenance` are compared fields of every statement
    (unlike `annotations`), so two comment-only statements are equal when
    their text is; a plain `StmtPass` is not one of them. Nothing compares
    IR across the option: it is off unless asked for."""
    from pssc.targets.comments import COMMENT_ONLY
    flds = {f.name: f.compare for f in dc.fields(ir.StmtPass)}
    assert flds["comment"] and flds["provenance"]
    assert not flds["annotations"]

    def only(text):
        return ir.StmtPass(comment=text, provenance=ir.Provenance(
            pass_name=COMMENT_ONLY))
    assert only("a") == only("a")
    assert only("a") != only("b")
    assert only("a") != ir.StmtPass()
