"""T1.3 (part a): function entries in a ZBC image, and test functions run.

``lower_module(..., entry_functions=[...])`` gives each named component
function an entry (``ZbcModel.entries``): its called form, in the root
instance, its parameters loaded from the root frame's arguments. ``pssc test``
uses this to run ``@pssunit_pkg::test`` functions.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources

SUITE = """\
import pssunit_pkg::*;
component base_f : fixture_c {
    rt_c rt;
    int cnt;
    @pssunit_pkg::test
    function void t_ok() {
        cnt = cnt + 3;
        rt.check_eq_int(cnt, 3, "cnt");
    }
    @pssunit_pkg::test
    function void t_bad() {
        rt.check_eq_int(scale(2), 5, "bad");
    }
    function int scale(int x) { return x * 2; }
    function int add(int a, int b) { return a + b + cnt; }
}
component derived_f : base_f {
    function int scale(int x) { return x * 3; }
}
"""


@pytest.fixture
def suite(tmp_path):
    p = tmp_path / "suite.pss"
    p.write_text(SUITE)
    return p


def _ctx(path):
    ctx = driver.translate([str(p) for p in sources.all_sources([str(path)])])
    assert not ctx.errors
    return ctx


def _call(model, sym, *args):
    result, _ = bc_session.run(model, model.entries[sym].coro, seed=1, args=args)
    return result.retval


def test_a_function_entry_takes_arguments_and_returns(suite):
    model = bc_session.lower(_ctx(suite), "base_f", functions=["base_f::add"])
    e = model.entries["base_f::add"]
    assert e.kind == "function"
    assert [n for n, _ in e.params] == ["a", "b"]
    assert e.returns["width"] == 32
    assert _call(model, "base_f::add", 2, 5) == 7


def test_a_base_function_entry_runs_the_override(suite):
    model = bc_session.lower(_ctx(suite), "derived_f", functions=["base_f::scale"])
    assert _call(model, "base_f::scale", 4) == 12


def test_actions_keep_their_entries(tmp_path):
    p = tmp_path / "a.pss"
    p.write_text("component pss_top { action a { } }\n")
    model = bc_session.lower(_ctx(p), "pss_top")
    assert model.entries["a"].kind == "action"
    assert bc_session.entries(model) == {"a": model.entries["a"].coro}


def test_an_unknown_entry_is_a_lowering_error(suite):
    with pytest.raises(bc_session.lowering_failures()):
        bc_session.lower(_ctx(suite), "base_f", functions=["base_f::nope"])
    with pytest.raises(bc_session.lowering_failures()):
        bc_session.lower(_ctx(suite), "base_f", functions=["other_c::add"])


def test_pssc_test_runs_test_functions(capsys, suite):
    from pssc.cli import main
    rc = main(["test", str(suite)])
    out = capsys.readouterr().out
    assert rc == 1
    # t_bad passes nowhere: 4 != 5 in base_f, 6 != 5 in derived_f.
    assert "2 passed, 2 failed" in out
    assert "derived_f::t_ok" in out or "t_ok" in out


# -- P1.3b: the session API (one tree, many calls, any instance) -----------

TREE = """\
import pssunit_pkg::*;
component chan_c {
    int n;
    function int bump(int by) { n = n + by; return n; }
}
component fast_c : chan_c {
    function int bump(int by) { n = n + 10 * by; return n; }
}
component dma_f : fixture_c {
    rt_c rt;
    chan_c ch[2];
    fast_c fast;
    int total;
    function int sum() { return ch[0].n + ch[1].n + fast.n; }
    @pssunit_pkg::test
    function void t_sum() {
        total = ch[0].bump(2);
        rt.check_eq_int(sum(), 2, "sum");
    }
}
"""


@pytest.fixture
def tree(tmp_path):
    p = tmp_path / "tree.pss"
    p.write_text(TREE)
    return bc_session.lower(_ctx(p), "dma_f", functions=["*"])


def test_star_gives_every_component_function_an_entry(tree):
    syms = {s for s, e in tree.entries.items() if e.kind == "function"}
    assert {"dma_f::sum", "dma_f::t_sum", "chan_c::bump",
            "fast_c::bump"} <= syms
    assert tree.entries["chan_c::bump"].comp_type == "chan_c"


def test_calls_on_sub_instances_keep_their_state(tree):
    s = bc_session.Session.load(tree)
    root = s.construct("dma_f")
    assert s.call(root, "ch[0].bump", 2) == 2
    assert s.call(root, "ch[0].bump", 3) == 5
    assert s.call(root, "ch[1].bump", 1) == 1
    assert s.call(root, "fast.bump", 1) == 10      # the override
    ch1 = s.instance("ch[1]")
    assert ch1.type_qname == "chan_c"
    assert s.call(ch1, "bump", 1) == 2
    assert s.call(root, "sum") == 17
    # construct() starts over
    root = s.construct()
    assert s.call(root, "sum") == 0


def test_session_errors(tree):
    s = bc_session.Session.load(tree)
    with pytest.raises(RuntimeError):
        s.call(bc_session.Instance(0, "", "dma_f"), "sum")
    with pytest.raises(ValueError):
        s.construct("chan_c")
    root = s.construct()
    with pytest.raises(KeyError):
        s.call(root, "ch[5].bump", 1)
    with pytest.raises(KeyError):
        s.call(root, "ch[0].nope")
    with pytest.raises(TypeError):
        s.call(root, "ch[0].bump")


def test_run_test_constructs_afresh_and_returns_the_ledger(tree):
    s = bc_session.Session.load(tree)
    assert s.run_test("dma_f::t_sum", seed=1) == (1, 0, 0)
    assert s.run_test("dma_f::t_sum", seed=2) == (1, 0, 0)


# -- P1.3c: a test action runs its `super` chain, in order -----------------

SUPER = """\
import pssunit_pkg::*;
component sup_f : fixture_c {
    rt_c rt;
    action l1_t : test_a {
        int x;
        exec body { x = 1; }
    }
    action l2_t : l1_t {
        exec body {
            super;
            comp.rt.check_eq_int(x, 1, "l1 ran first");
            x = x * 10 + 2;
        }
    }
    action l3_t : l2_t {
        exec body {
            super;
            comp.rt.check_eq_int(x, 12, "l1 then l2");
            x = x * 10 + 3;
            comp.rt.check_eq_int(x, 123, "then l3");
        }
    }
    action l4_t : l3_t { }
}
"""


def test_test_actions_run_their_super_chain_in_order(capsys, tmp_path):
    from pssc.cli import main
    p = tmp_path / "sup.pss"
    p.write_text(SUPER)
    rc = main(["test", str(p)])
    out = capsys.readouterr().out
    assert rc == 0, out
    # l4_t declares no body: it runs l3_t's, and with it the whole chain.
    assert "4 passed" in out
