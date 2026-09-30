"""What the one type-body dispatch changed in meaning (O6).

* Two exec blocks of one kind in a scope are ONE block, in source order, with
  the initial definition before its extensions (LRM 22.1 d). Each used to be a
  function of its own; bc ran the last and the SV target the first.
* ``extend action`` reaches every action member. An ``input`` or a ``lock``
  added there used to be dropped, because actions had a loop of their own.
* A second activity in an action is refused. It used to replace the first;
  LRM 11.1 runs the two as one ``schedule``.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _translate(src: str):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)


def _execs(t, name):
    return [f for f in t.functions if (f.metadata or {}).get("exec_kind") == name]


def _assigned(fn):
    """The names the top-level `x = <n>;` statements of *fn* assign, in order."""
    out = []
    for s in fn.body:
        tgt = getattr(s, "targets", None) or [getattr(s, "target", None)]
        out.append(getattr(tgt[0], "attr", None) if tgt[0] is not None else None)
    return out


def test_two_bodies_in_one_action_are_one_block_in_source_order():
    ctx = _translate("""\
component pss_top {
    action A {
        int a; int b;
        exec body { a = 1; }
        exec body { b = 2; }
    }
}
""")
    assert not ctx.errors, ctx.errors
    bodies = _execs(ctx.type_map["pss_top::A"], "body")
    assert len(bodies) == 1
    assert _assigned(bodies[0]) == ["a", "b"]


def test_an_extensions_body_follows_the_initial_definitions():
    """File order puts the extension first; the initial definition still
    comes first in the merged block (LRM 18.2 ordering, 22.1 d)."""
    ctx = _translate("""\
extend action pss_top::A { exec body { b = 2; } }
component pss_top {
    action A {
        int a; int b;
        exec body { a = 1; }
    }
}
""")
    assert not ctx.errors, ctx.errors
    bodies = _execs(ctx.type_map["pss_top::A"], "body")
    assert len(bodies) == 1
    assert _assigned(bodies[0]) == ["a", "b"]


def test_component_init_blocks_merge_too():
    ctx = _translate("""\
component pss_top {
    int a; int b;
    exec init_down { a = 1; }
}
extend component pss_top {
    exec init_down { b = 2; }
}
""")
    assert not ctx.errors, ctx.errors
    inits = _execs(ctx.type_map["pss_top"], "init_down")
    assert len(inits) == 1
    assert _assigned(inits[0]) == ["a", "b"]


def test_extend_action_adds_an_input_and_a_claim():
    ctx = _translate("""\
buffer b_b { rand int v; }
resource r_r { }
component pss_top {
    pool b_b bp; pool [1] r_r rp;
    bind bp *; bind rp *;
    action A { }
}
extend action pss_top::A {
    input b_b in_b;
    lock r_r r;
}
""")
    assert not ctx.errors, ctx.errors
    names = [f.name for f in ctx.type_map["pss_top::A"].fields]
    assert "in_b" in names and "r" in names


def test_a_second_activity_is_refused_not_a_replacement():
    ctx = _translate("""\
component pss_top {
    action B { }
    action C { }
    action A {
        activity { do B; }
        activity { do C; }
    }
}
""")
    assert any(e.startswith("line 6: a second activity in action 'pss_top::A'")
               for e in ctx.errors), ctx.errors


def test_merging_blocks_that_declare_the_same_local_is_refused():
    """Each exec block is its own scope; one merged body would redeclare
    `t`. Refused, with the second block's line, until the IR can scope it."""
    ctx = _translate("""\
component pss_top {
    action A {
        exec body { int t = 1; }
        exec body { int t = 2; }
    }
}
""")
    assert any(e.startswith("line 4: 'exec body' in action 'pss_top::A' "
                            "declares t") for e in ctx.errors), ctx.errors
