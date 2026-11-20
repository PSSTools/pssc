"""Coverage points in debug lowering (P2.7a): be-bc's ``CoroDescriptor.cov``
table (statements by structural path, branch arms with their own pc) and
the ``branch`` / ``stmt`` events that report them when watched."""

from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources

MODEL = """\
component cov_c {
    function int one(int n) {
        int r;
        r = 0;
        if (n > 2) {
            r = 1;
        }
        return r;
    }
    function int chain(int n) {
        int r;
        if (n == 0) {
            r = 10;
        } else if (n == 1) {
            r = 11;
        } else {
            r = 12;
        }
        return r;
    }
    function int sel(int n) {
        int r;
        r = 0;
        match (n) {
            [1]: { r = 21; }
            [2]: { r = 22; }
            default: { r = 29; }
        }
        return r;
    }
    function int helper(int v) {
        return v + 1;
    }
    function int outer(int n) {
        int r;
        r = helper(n);
        return r;
    }
}
"""


def _translate(tmp_path_factory, text):
    p = tmp_path_factory.mktemp("cov") / "cov.pss"
    p.write_text(text)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors, c.errors
    return c


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    return _translate(tmp_path_factory, MODEL)


@pytest.fixture(scope="module")
def model(ctx):
    return bc_session.lower(ctx, "cov_c", functions=["*"], debug=True)


def _coro(m, fn):
    return m.coros[m.entries[f"cov_c::{fn}"].coro]


def _arms(coro):
    return {path: (pc, implicit) for k, pc, _, path, _, implicit in coro.cov if k == "arm"}


def _run(m, fn, n):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev
    sink = MemorySink()
    s = bc_session.Session(m, events=sink, watch=ev.EV_BRANCH | ev.EV_STMT)
    ret = s.call(s.construct(), fn, n)
    return ret, sink.events


def _taken(events):
    return [(e.detail["branch"], e.detail["arm"], e.detail.get("implicit", False))
            for e in events if e.kind == "branch"]


def test_if_without_else_has_an_implicit_else(model):
    arms = _arms(_coro(model, "one"))
    # a function's paths start at its body: the `if` is its third statement
    assert set(arms) == {"2/then", "2/else"}
    then, els = arms["2/then"], arms["2/else"]
    assert then[1] is False and els[1] is True
    assert then[0] != els[0]


@pytest.mark.parametrize("n,arm,ret", [(5, "then", 1), (0, "else", 0)])
def test_if_reports_the_arm_taken(model, n, arm, ret):
    r, events = _run(model, "one", n)
    assert r == ret
    taken = _taken(events)
    assert [a for _, a, _ in taken] == [arm]
    assert taken[0][2] == (arm == "else")


def test_if_chain_arms(model):
    arms = _arms(_coro(model, "chain"))
    assert sorted(p.rsplit("/", 1)[1] for p in arms) == ["0", "1", "else"]
    assert not any(imp for _, imp in arms.values())
    assert len({pc for pc, _ in arms.values()}) == 3
    for n, arm in [(0, "0"), (1, "1"), (7, "else")]:
        r, events = _run(model, "chain", n)
        assert r == {"0": 10, "1": 11, "else": 12}[arm]
        assert [a for _, a, _ in _taken(events)] == [arm]


def test_match_arms(model):
    arms = _arms(_coro(model, "sel"))
    assert sorted(p.rsplit("/", 1)[1] for p in arms) == ["0", "1", "default"]
    for n, arm, want in [(1, "0", 21), (2, "1", 22), (9, "default", 29)]:
        r, events = _run(model, "sel", n)
        assert r == want
        assert [a for _, a, _ in _taken(events)] == [arm]


def test_stmt_events_carry_paths(model):
    _, events = _run(model, "one", 5)
    paths = [e.detail.get("path") for e in events if e.kind == "stmt"]
    assert all(paths), paths
    assert paths == ["0", "1", "2", "2/then/0", "3"]
    scopes = {e.detail.get("scope") for e in events if e.kind == "stmt"}
    assert scopes == {"cov_c::one"}


def test_called_function_items_are_scoped_by_function(model):
    _, events = _run(model, "outer", 3)
    scopes = {e.detail.get("scope") for e in events if e.kind == "stmt"}
    assert "cov_c::helper" in scopes, scopes


def test_release_lowering_has_no_coverage(ctx, model):
    from zuspec.be.bc.model import Op
    rel = bc_session.lower(ctx, "cov_c", functions=["*"], debug=False)
    for fn in ("one", "chain", "sel"):
        c = _coro(rel, fn)
        assert not c.cov
        assert not any(i.op == Op.NOP for i in c.code)
    assert rel.to_container() == bc_session.lower(
        ctx, "cov_c", functions=["*"], debug=False).to_container()


def test_paths_do_not_move_with_lines(tmp_path_factory, model):
    shifted = bc_session.lower(_translate(tmp_path_factory, "\n\n\n" + MODEL),
                               "cov_c", functions=["*"], debug=True)
    for fn in ("one", "chain", "sel"):
        a, b = _coro(model, fn).cov, _coro(shifted, fn).cov
        assert [(k, pc, s, p, i) for k, pc, s, p, _, i in a] == \
            [(k, pc, s, p, i) for k, pc, s, p, _, i in b]
        la = [model.prov.entries[sr].line for *_, sr, _ in a if sr]
        lb = [shifted.prov.entries[sr].line for *_, sr, _ in b if sr]
        assert lb == [x + 3 for x in la] and la
