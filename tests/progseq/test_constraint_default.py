"""`constraint default` bodies reach the AST, and stop at the IR.

pssparser used to drop them: `constraint default X == V;` parsed without error
into a `ConstraintBlock` carrying **zero** statements, so `ast2ir` had nothing to
translate. That is fixed -- a default now costs exactly what a plain constraint
costs (see `test_a_default_constraint_carries_its_body`):

    two plain constraints    -> 2 blocks, 2 statements
    one plain + one default  -> 2 blocks, 2 statements
    only a default           -> 1 block,  1 statement

The IR carries a default as its own statement, ``StmtDefault`` (LRM 13.1.11),
which is what `test_the_models_defaults_are_visible_in_the_ir` checks against
the real model; bc resolves and solves them.

SCOPE -- this does NOT affect either shipping op-model API. Constraints and
actions are deliberately excluded from an operation model: there is no solver in
a generated C image, and the op-model-sv target emits no constraints either. The
generated SV package contains **zero** occurrences of `constraint`, including
`constraint tot_sz > 0`, which reaches the IR perfectly well. So the eleven
defaults on `wb_dma_ch_cfg_s` are absent from both APIs by design, not by this
defect, and a C-side `_DEFAULT` macro knob (plan C6.5) is out of scope rather
than blocked.

Where it matters is a solver-capable target, which is the only kind that
consumes constraints at all.
"""
import os

import pytest

from pssc import driver
from pssc.frontend import Parser

from .codetext import code_only
from .op_model import op_model_sources, op_model_sv_sources


_PROBE = """
package p {
    struct s_t {
        rand bit[32] a;
        rand bit[32] b;
%s
    }
}
"""


def _blocks_and_stmts(body):
    """(ConstraintBlock count, total constraint-statement count) in the AST.

    Counts only blocks that came from the probe source. The parser prepends a
    builtin prelude, and that prelude carries a constraint of its own, so an
    unfiltered walk reports one extra block *and* one extra statement -- which
    is what made these counts wrong when the prelude grew that constraint.
    """
    p = Parser()
    p.parses([("t.pss", _PROBE % body)])
    root = p.link()
    probe_fileids = set(p.file_map.keys())
    n = [0, 0]

    def walk(x):
        try:
            kids = x.children() or []
        except Exception:
            return
        for c in kids:
            if c is None:
                continue
            if type(c).__name__ == "ConstraintBlock":
                loc = c.getLocation()
                if loc is not None and loc.fileid in probe_fileids:
                    n[0] += 1
                    n[1] += len(c.getConstraints() or [])
            walk(c)

    walk(root)
    return tuple(n)


def test_a_plain_constraint_reaches_the_ast():
    """The control. Without it, "the default is dropped" could just as well mean
    constraints on structs are dropped wholesale."""
    assert _blocks_and_stmts("        constraint b > 0;")[1] == 1


def test_a_default_constraint_carries_its_body():
    """`constraint default X == V;` costs the same as a plain constraint.

    This measured the defect for as long as it existed. It now measures the fix:
    a default carries its body, so a mixed pair counts the same as a plain pair.
    If a regression drops the body again, `mixed` falls to 1 and this fails with
    both counts in hand.
    """
    # NOTE: this is a front-end fact only. No op-model backend reads constraints
    # -- see the module docstring for what does and does not follow from it.
    plain = _blocks_and_stmts("        constraint b > 0;\n"
                              "        constraint a == 7;")
    mixed = _blocks_and_stmts("        constraint b > 0;\n"
                              "        constraint default a == 7;")
    assert plain[1] == 2, plain
    assert mixed[1] == 2, (
        f"a default constraint lost its body again ({mixed}); it used to be "
        f"dropped before reaching the AST")


def test_a_default_constraint_should_reach_the_ast():
    assert _blocks_and_stmts("        constraint default a == 7;")[1] == 1


def test_the_models_defaults_are_visible_in_the_ir():
    """The same question, stated against the REAL model rather than a probe.

    `wb_dma_ch_cfg_s` states `constraint default src_mask == 0xfffffffc`. The
    IR carries it as a ``StmtDefault`` -- a default, not a hard constraint --
    which is what a solver-capable target reads (bc resolves it per LRM
    13.1.11 d).
    """
    from zuspec.ir import core as ir
    ctx = driver.translate(op_model_sources()).ir_context
    cfg = ctx.type_m["wb_dma_ch_cfg_s"]
    found = {}
    for fn in cfg.functions:
        for st in fn.body:
            if isinstance(st, ir.StmtDefault):
                found[getattr(st.target, "attr", None)] = getattr(st.value, "value", None)
    assert found.get("src_mask") == 0xfffffffc, found


# --- the exclusion itself, as behaviour rather than prose -------------------

def test_no_constraint_reaches_the_c_output():
    """Constraints and actions are excluded from an operation model silently and
    by construction (design §10) -- there is no solver in a generated C image.

    Stated as a test because it was previously only prose, and prose is what let
    me mistake an *intended* absence for a front-end defect: I found that
    pssparser drops `constraint default` bodies, saw the model's defaults missing
    from the generated APIs, and joined the two. They are unrelated. The plain
    `constraint tot_sz > 0` reaches the IR intact (see the control test above)
    and is equally absent from the output, which is the fact that distinguishes
    "excluded by design" from "lost in the front end".

    So this asserts the *surviving* constraint is dropped too. A backend that
    started projecting equality defaults would still pass a test that only looked
    for the defaults; it would fail this one.
    """
    import argparse
    import tempfile
    from pssc import driver

    with tempfile.TemporaryDirectory() as out:
        ns = argparse.Namespace(progseq_root="wb_dma_c", c_prefix="wb_dma",
                                output_dir=out, c_header_only=True)
        driver.compile(list(op_model_sources()), target="op-model-c", opts=ns)
        text = (
            open(os.path.join(out, "wb_dma.h")).read()
            if os.path.exists(os.path.join(out, "wb_dma.h")) else "")

    # Comments excluded. The output carries the register model's own
    # documentation, and `0xfffffffc` is also this register's RDL *reset*
    # value, which appears in a field's comment. That is a different fact
    # about the same number; the claim here is about what is emitted as code.
    code = code_only(text)

    # Positive control: the FIELD is emitted, so the absence below is the
    # constraint being excluded and not the struct being missing.
    assert "tot_sz" in code and "src_mask" in code
    # `tot_sz > 0` survives to the IR and is still not emitted...
    assert "tot_sz > 0" not in code
    # ...and neither is any value that only a `constraint default` states.
    assert "0xfffffffc" not in code.lower()


def test_no_constraint_reaches_the_sv_output():
    """The same boundary on the other op-model target, which is what makes it a
    boundary rather than a C limitation. The generated SV package contains zero
    occurrences of `constraint` -- checked against the model's own plain
    constraint, not just its defaults, for the reason given above.
    """
    import argparse
    import tempfile
    from pssc import driver

    with tempfile.TemporaryDirectory() as out:
        ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=out)
        driver.compile(list(op_model_sv_sources()), target="op-model-sv",
                       opts=ns)
        text = "".join(
            open(os.path.join(out, f)).read()
            for f in sorted(os.listdir(out)) if f.endswith(".sv"))

    code = code_only(text)
    assert "tot_sz" in code, "positive control: the field itself must be emitted"
    # Comments excluded for the same reason as above: the model's prose is now
    # carried into the output, and prose about a constraint is not a constraint.
    assert "constraint" not in code
