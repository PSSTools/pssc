"""Phase 7 (P7.T1): the language-neutral body walk, before anything moves.

The plan calls Phase 7 its highest-risk refactor, and the reason is that the
three body emitters produce artifacts a customer compiles. So this file proves
the shared pieces are equivalent to what the emitters compute for themselves
BEFORE either backend is asked to use them -- on the real WB DMA model, every
operation, not on a construct sample.

If the scans here disagreed with the C emitter's, the port would produce C that
still compiles and quietly stops silencing an unused local, or widens the wrong
declaration. Neither is visible in a diff of one construct.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional

import pytest

from pssc.targets import body_walker as bw
from pssc.targets.c import lower_progseq as clp

from .op_model import op_model_sources


# -- what the C emitter computed for itself ----------------------------------

@pytest.fixture(scope="module")
def c_emitters(tmp_path_factory):
    """Every `_BodyEmitter` the C backend built for the WB DMA model.

    Captured from a real generation rather than constructed here: the point is
    to compare against what the SHIPPING path computes, including the ctor
    emitters and whatever flags the target passes, not against a second
    hand-built configuration that might differ from it.
    """
    return _capture(tmp_path_factory.mktemp("walker"), op_model_sources(),
                    progseq_root="wb_dma_c", c_prefix="wb_dma")[0]


#: A body that DISCARDS the result of a channel `try_get`, which is what the
#: write-only analysis exists for. It is here because the WB DMA model no
#: longer contains one -- see `test_the_wb_dma_model_no_longer_has_a_write_only_local`.
_DISCARD = """
import sync_pkg::*;

component probe_c {
    channel_c<bit, 1> inflight;

    target function void drain() {
        bit tok;
        bool ok;
        ok = inflight.try_get(tok);
    }
}
"""


@pytest.fixture(scope="module")
def discard(tmp_path_factory):
    """Emitters and generated C for `_DISCARD`."""
    out = tmp_path_factory.mktemp("discard")
    src = out / "probe.pss"
    src.write_text(_DISCARD)
    emitters, out_dir = _capture(out, [str(src)], progseq_root="probe_c",
                                 c_prefix="probe")
    return emitters, (out_dir / "probe.c").read_text()


def _capture(out_dir, sources, **flags):
    """Run the C target, recording every `_BodyEmitter` it builds.

    Captured from a real generation rather than constructed here: the point is
    to compare against what the SHIPPING path computes, including the ctor
    emitters and whatever flags the target passes, not against a second
    hand-built configuration that might differ from it.
    """
    import argparse

    from pssc import driver

    seen = []
    original = clp._BodyEmitter.__init__

    def recording(self, *args, **kw):
        original(self, *args, **kw)
        seen.append(self)

    clp._BodyEmitter.__init__ = recording
    try:
        ns = argparse.Namespace(c_header_only=False, output_dir=str(out_dir),
                                **flags)
        driver.compile(list(sources), target="op-model-c", opts=ns)
    finally:
        clp._BodyEmitter.__init__ = original
    assert seen, "no body emitter ran; the capture is measuring nothing"
    return seen, out_dir


def test_the_model_exercises_the_channel_output_scan(c_emitters):
    """The guard on the equivalence below: a model that binds no channel output
    would make the two agree trivially, and the claim would be empty."""
    assert any(e.chan_out_locals for e in c_emitters)


def test_the_wb_dma_model_no_longer_has_a_write_only_local(c_emitters):
    """Recorded, not assumed. `_scan_write_only`'s docstring cites
    `ok = inflight.try_get(tok)` in `wait_completion`, and that body now reads
    `ok` -- so the WB DMA model proves nothing about this scan, and the
    equivalence below is measured on `_DISCARD` instead.

    Stated as a test because the alternative is a guard that silently passes
    over an empty comparison. If a write-only local comes back, this fails and
    the reader is pointed at both places.
    """
    assert not any(e.write_only_locals for e in c_emitters)


def test_write_only_scan_matches_the_c_emitter(discard):
    emitters, _ = discard
    assert any(e.write_only_locals for e in emitters), "nothing to compare"
    for e in emitters:
        assert bw.scan_write_only(e.fn.body) == e.write_only_locals, (
            f"write-only locals differ for '{e.fn.name}': the walker says "
            f"{sorted(bw.scan_write_only(e.fn.body))}, the C emitter "
            f"{sorted(e.write_only_locals)}")


def test_the_discard_reaches_the_generated_c(discard):
    """What the scan is FOR, asserted end to end -- and untested until now: no
    test in this suite looked for the `(void)` the analysis exists to emit."""
    _, text = discard
    assert "(void)ok;" in text
    assert "uint64_t tok" in text        # the channel-output widening, likewise


def test_write_only_scan_matches_the_c_emitter_on_wb_dma(c_emitters):
    """The empty case still has to agree. A scan that over-reports would emit a
    `(void)x;` for a variable the body reads, and this is where that shows."""
    for e in c_emitters:
        assert bw.scan_write_only(e.fn.body) == e.write_only_locals


def test_channel_output_scan_matches_the_c_emitter(c_emitters):
    for e in c_emitters:
        found = bw.scan_output_locals(
            e.fn.body, lambda n: e._is_chan_method(n, "try_get"))
        assert found == e.chan_out_locals, (
            f"channel output locals differ for '{e.fn.name}': the walker says "
            f"{sorted(found)}, the C emitter {sorted(e.chan_out_locals)}")


def test_the_scans_are_measured_on_every_operation(c_emitters):
    """Not on a sample of them. Named operations, so a lowering that stopped
    running one is a failure here rather than a smaller silent comparison."""
    names = {e.fn.name for e in c_emitters}
    assert {"wait_completion", "transfer_single", "transfer_list",
            "configure_channel"} <= names


# -- the ported C emitter (P7.T2) -------------------------------------------

#: What the C target lowers. Frozen here rather than derived from the class,
#: because deriving it from the class would make a construct that silently
#: stopped being handled agree with its own absence. Byte-identical output is
#: checked by the golden snapshots; this is the other half -- that the SET did
#: not shrink.
_C_STMT_KINDS = {
    "StmtAnnAssign", "StmtAssign", "StmtAugAssign", "StmtExpr", "StmtReturn",
    "StmtIf", "StmtRepeatWhile", "StmtWhile", "StmtBreak", "StmtContinue",
    "StmtForeach", "StmtMatch", "StmtYield",
}

_C_EXPR_KINDS = {
    "ExprConstant", "ExprRefLocal", "ExprAttribute", "ExprSubscript",
    "ExprBin", "ExprCast", "ExprUnary", "ExprCall", "ExprRefBottomUp",
}


def test_the_c_emitter_walks_with_the_shared_walker():
    assert issubclass(clp._BodyEmitter, bw.BodyWalker)
    # Its own `stmts`/`stmt` are gone -- the comment attachment happens in one
    # place now, which is the point of the port.
    assert "stmt" not in vars(clp._BodyEmitter)
    assert "stmts" not in vars(clp._BodyEmitter)


def test_the_c_emitter_still_lowers_every_construct_it_did():
    kinds = set(clp._BodyEmitter.hooks())
    assert _C_STMT_KINDS <= kinds
    assert _C_EXPR_KINDS <= kinds


def test_the_ctor_emitter_adds_its_forms_without_taking_any_away():
    """`_CtorMixin` adds the two ctor-only DISPOSITIONS. It used to override
    `expr` wholesale, which ran on every operand in an `initialize` body to
    look at one node kind."""
    assert set(clp._CtorEmitter.hooks()) >= _C_STMT_KINDS | _C_EXPR_KINDS
    assert "expr" not in vars(clp._CtorMixin)
    assert "expr_call" not in vars(clp._CtorMixin)
    assert {"structural", "fold", "subcomp_ctor"} <= set(
        clp._CtorEmitter.call_hooks())


def test_an_unhandled_construct_names_the_hook_to_write(c_emitters):
    """The C emitter's `unsupported stmt X` became a message that says what is
    missing. Checked on the real emitter, since that error is what an author
    adding a construct actually meets."""
    with pytest.raises(ValueError) as exc:
        c_emitters[0].stmt(_Vanish(), 1)
    assert "unsupported stmt StmtVanish" in str(exc.value)
    assert "stmt_vanish()" in str(exc.value)


# -- the ported SV emitter (P7.T3) ------------------------------------------

#: SystemVerilog's expression set. It differs from C's in exactly two places,
#: and both are real: `this` exists in SV and not in C, and the upward-reference
#: rejection is a C rule (the C lowering embeds sub-components by value and
#: emits no parent pointer). Listed rather than shared, because "the two
#: languages happen to agree" is not the same claim as "they must".
_SV_EXPR_KINDS = (_C_EXPR_KINDS - {"ExprRefBottomUp"}) | {"TypeExprRefSelf"}


def test_the_sv_emitter_walks_with_the_shared_walker():
    from pssc.targets.sv import lower_progseq as svlp

    assert issubclass(svlp._BodyEmitter, bw.BodyWalker)
    assert "stmt" not in vars(svlp._BodyEmitter)
    assert "stmts" not in vars(svlp._BodyEmitter)


def test_the_sv_emitter_still_lowers_every_construct_it_did():
    from pssc.targets.sv import lower_progseq as svlp

    kinds = set(svlp._BodyEmitter.hooks())
    assert _C_STMT_KINDS <= kinds
    assert _SV_EXPR_KINDS <= kinds


def test_the_two_emitters_render_the_same_statement_kinds():
    """Two languages, one walk. If this stops holding, the walker is carrying
    a per-language statement set and the abstraction has leaked."""
    from pssc.targets.sv import lower_progseq as svlp

    c_stmts = {k for k in clp._BodyEmitter.hooks() if k.startswith("Stmt")}
    sv_stmts = {k for k in svlp._BodyEmitter.hooks() if k.startswith("Stmt")}
    assert c_stmts == _C_STMT_KINDS
    # `super;` is legal only in an exec block, and SV renders `exec init_down`
    # / `init_up` where C refuses them (`supports_init_blocks`). That is a
    # difference in what the targets ACCEPT, not in how the walk is shared.
    # So is `repeat` (`StmtFor`): SV renders it, which the compliance tier's
    # loop tests need; C does not yet.
    assert sv_stmts == _C_STMT_KINDS | {"StmtSuper", "StmtFor"}


def test_the_two_emitters_indent_differently():
    """The control on the test above: they share the walk, not the rendering.
    A walker that had absorbed the layout would make them identical here."""
    from pssc.targets.sv import lower_progseq as svlp

    assert clp._BodyEmitter.indent == "    "
    assert svlp._BodyEmitter.indent == "  "


# -- the scans, on the cases that are their reason for existing --------------

def test_write_only_scan_sees_a_subscripted_target_as_a_read():
    """`a[j] = 2` READS both a and j. The one arm of this scan whose absence
    produces a plausible wrong answer instead of a crash: `a` would be reported
    write-only, and the emitter would state a discard of a variable the body
    goes on to index."""
    body = _subscript_body()
    assert bw.scan_write_only(body) == set()


def test_output_scan_refuses_an_argument_with_no_name():
    """The scan records a NAME, to widen its declaration later. An expression
    has none, and accepting one silently would leave the declaration at its PSS
    width -- an eight-byte write into a one-byte object."""
    call = _Call(_Attr("try_get"), [_Attr("field")])
    with pytest.raises(ValueError, match="must be a local variable"):
        bw.scan_output_locals([call], lambda n: True, what="chan.try_get()")


def test_output_scan_refuses_a_call_with_no_argument():
    call = _Call(_Attr("try_get"), [])
    with pytest.raises(ValueError, match="takes one output argument"):
        bw.scan_output_locals([call], lambda n: True, what="chan.try_get()")


def test_the_output_scan_asks_the_caller_which_calls_count():
    """The predicate is the whole language-specific part. With one that says
    no, the same tree yields nothing -- which is what a target with no channel
    concept gets."""
    call = _Call(_Attr("try_get"), [_Local("tok")])
    assert bw.scan_output_locals([call], lambda n: True) == {"tok"}
    assert bw.scan_output_locals([call], lambda n: False) == set()


# -- dispatch ----------------------------------------------------------------

def test_hook_names_are_the_node_class_names():
    assert bw.hook_name("StmtAnnAssign") == "stmt_ann_assign"
    assert bw.hook_name("ExprRefBottomUp") == "expr_ref_bottom_up"
    assert bw.hook_name("TypeExprRefSelf") == "type_expr_ref_self"


class _Tiny(bw.BodyWalker):
    indent = "  "

    def stmt_break(self, s, ind):
        return [f"{self.pad(ind)}break;"]

    def stmt_expr(self, s, ind):
        return [f"{self.pad(ind)}{self.expr(s.expr)};"]

    def stmt_vanish(self, s, ind):
        return []

    def expr_constant(self, e):
        return str(e.value)


def test_dispatch_reaches_the_hook_named_after_the_node():
    assert _Tiny().stmts([_Break()], 1) == ["  break;"]
    assert _Tiny().stmts([_StmtExpr(_Const(7))], 2) == ["    7;"]


def test_a_node_with_no_hook_names_the_hook_that_is_missing():
    with pytest.raises(ValueError) as exc:
        _Tiny().stmts([_Continue()], 0)
    msg = str(exc.value)
    assert "unsupported stmt StmtContinue" in msg
    assert "stmt_continue()" in msg
    assert "_Tiny" in msg          # which walker, when several are in play


def test_an_unsupported_expression_says_expr_not_stmt():
    with pytest.raises(ValueError, match="unsupported expr ExprBin"):
        _Tiny().expr(_ExprBin())


def test_comments_attach_at_every_nesting_level():
    s = _Break(comment="why we stop", comment_trailing="here")
    assert _Tiny().stmts([s], 1) == ["  // why we stop", "  break;   // here"]


def test_a_statement_that_lowers_to_nothing_takes_its_comment_with_it():
    """Prose describing output that is not there is worse than no prose."""
    s = _Vanish(comment="about a statement that produces no lines")
    assert _Tiny().stmts([s], 1) == []


def test_the_comment_style_is_the_walkers():
    class _Block(_Tiny):
        comment_style = "block"

    assert _Block().stmts([_Break(comment="note")], 0) == ["/* note */",
                                                           "break;"]


def test_hooks_reports_the_node_kinds_a_walker_covers():
    """Introspection the ported emitters' tests lean on: what this renders,
    from the class rather than from a list somebody keeps."""
    kinds = _Tiny.hooks()
    assert kinds["StmtBreak"] == "stmt_break"
    assert kinds["ExprConstant"] == "expr_constant"
    # `render_stmt`, `stmts`, `pad` are the walk, not node hooks.
    assert "StmtLines" not in kinds and "Stmts" not in kinds


# -- fakes -------------------------------------------------------------------
#
# Dataclasses named for the IR nodes they stand in for. The walk dispatches on
# the class NAME and reads named fields, so these exercise it exactly as the
# real IR does, without a parse.


@dc.dataclass
class _Stmt:
    comment: Optional[str] = None
    comment_trailing: Optional[str] = None


@dc.dataclass
class StmtBreak(_Stmt):
    pass


@dc.dataclass
class StmtContinue(_Stmt):
    pass


@dc.dataclass
class StmtVanish(_Stmt):
    pass


@dc.dataclass
class ExprConstant:
    value: Any = 0


@dc.dataclass
class ExprBin:
    pass


@dc.dataclass
class ExprRefLocal:
    name: str = ""


@dc.dataclass
class ExprAttribute:
    attr: str = ""


@dc.dataclass
class ExprCall:
    func: Any = None
    args: List[Any] = dc.field(default_factory=list)


@dc.dataclass
class StmtExpr(_Stmt):
    expr: Any = None


def _Break(**kw):
    return StmtBreak(**kw)


def _Continue(**kw):
    return StmtContinue(**kw)


def _Vanish(**kw):
    return StmtVanish(**kw)


def _Const(v):
    return ExprConstant(v)


def _ExprBin():
    return ExprBin()


def _Local(name):
    return ExprRefLocal(name)


def _Attr(name):
    return ExprAttribute(name)


def _Call(func, args):
    return ExprCall(func, args)


def _StmtExpr(e):
    return StmtExpr(expr=e)


def _subscript_body():
    """`int a[4]; int j; j = 1; a[j] = 2;` as IR.

    Built rather than parsed: the arm under test is about the IR's shape, and a
    parse would make this test depend on the front end to say something about
    ten lines of tree walking.
    """
    @dc.dataclass
    class StmtAssign(_Stmt):
        targets: List[Any] = dc.field(default_factory=list)
        value: Any = None

    @dc.dataclass
    class StmtAnnAssign(_Stmt):
        target: Any = None
        annotation: Any = None
        value: Any = None

    @dc.dataclass
    class ExprSubscript:
        value: Any = None
        slice: Any = None

    return [
        StmtAnnAssign(target=ExprRefLocal("a")),
        StmtAnnAssign(target=ExprRefLocal("j")),
        StmtAssign(targets=[ExprRefLocal("j")], value=ExprConstant(1)),
        StmtAssign(targets=[ExprSubscript(ExprRefLocal("a"), ExprRefLocal("j"))],
                   value=ExprConstant(2)),
    ]
