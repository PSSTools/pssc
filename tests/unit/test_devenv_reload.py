"""T3.6 (pyvm part): hot reload of a ``bc_session.Session``.

``Session.reload(model)`` makes a new image the session's next generation:
checked first (API, ``strict``), constructed bus-isolated, and given the old
tree's state per unchanged instance path (channel tokens included) when it
applies: at once between calls, or when the running call returns.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources
from zuspec.be.bc.interp.generations import ReloadRefused
from zuspec.be.bc.trace import MemorySink
from zuspec.be.bc.trace import events as ev

BODY = """\
import sync_pkg::*;
import solve function void poke_hw(int x);
component sub_c {
    int hits;
    function void hit() { hits = hits + 1; }
}
component top_c {
    sub_c a;
    int count;
    channel_c<bit[32]> ch;
    %(extra)s
    function void step() { count = count + %(inc)s; a.hit(); }
    function int get_count() { return count; }
    function int a_hits() { return a.hits; }
    function int put(int v) { if (ch.try_put(v)) { return 1; } return 0; }
    function int take() {
        bit[32] v;
        if (ch.try_get(v)) { return v; }
        return 99;
    }
    function int poke() { return helper(); }
    function int helper() { return 0; }
}
"""


def _model(tmp_path, name, inc="1", extra=""):
    p = tmp_path / f"{name}.pss"
    p.write_text(BODY % {"inc": inc, "extra": extra})
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors, c.errors
    return bc_session.lower(c, "top_c", functions=["*"], debug=True)


@pytest.fixture
def models(tmp_path):
    return lambda name, **kw: _model(tmp_path, name, **kw)


def test_reload_between_calls_keeps_state_and_runs_new_code(models):
    s = bc_session.Session(models("n"))
    root = s.construct()
    s.call(root, "step")
    s.call(root, "step")
    r = s.reload(models("n1", inc="10"))
    assert r["applied"] and (r["old"], r["new"]) == (1, 2)
    assert s.generation == 2 and s.history == [r]
    assert "" in r["migrated"] and "a" in r["migrated"]
    assert s.call(root, "get_count") == 2      # N's state carried over
    s.call(root, "step")                       # N+1's code
    assert s.call(root, "get_count") == 12
    assert s.call(root, "a_hits") == 3


def test_channel_tokens_carry_across(models):
    s = bc_session.Session(models("n"))
    root = s.construct()
    assert s.call(root, "put", 7) == 1
    s.reload(models("n1", inc="2"))
    assert s.call(root, "take") == 7
    assert s.call(root, "take") == 99


def test_a_reload_in_a_call_is_staged_until_it_returns(models):
    s = bc_session.Session(models("n"))
    root = s.construct()
    m1 = models("n1", inc="10")
    seen = []

    def helper():
        r = s.reload(m1)
        seen.extend([r["applied"], r])
        return 0
    s.bind("top_c::helper", helper)
    s.call(root, "poke")
    assert seen[0] is False            # staged while poke ran on N
    assert seen[1]["applied"] is True  # applied when it returned
    assert s.generation == 2 and s.model is m1
    with pytest.raises(RuntimeError, match="cancel"):
        s.bind("top_c::helper", lambda: s.reload(models("n2"), cancel=True) or 0)
        s.call(root, "poke")


def test_bindings_carry_over(models):
    s = bc_session.Session(models("n"))
    root = s.construct()
    s.bind("top_c::helper", lambda: 42)
    r = s.reload(models("n1"))
    assert r["unbound"] == []
    assert s.call(root, "poke") == 42


def test_api_change_refused_and_session_untouched(models):
    s = bc_session.Session(models("n"))
    root = s.construct()
    s.call(root, "step")
    m = models("bad", extra="function int extra(int x) { return x; }")
    old = s.model
    with pytest.raises(ReloadRefused) as ei:
        s.reload(m)
    assert ei.value.to_json()["reason"] == "api"
    assert s.model is old and s.generation == 1
    assert s.call(root, "get_count") == 1


def test_bus_touching_constructor_refuses(models):
    s = bc_session.Session(models("n"))
    root = s.construct()
    m = models("bus", extra="exec init_down { poke_hw(1); }")
    with pytest.raises(ReloadRefused) as ei:
        s.reload(m)
    assert ei.value.to_json()["reason"] == "bus"
    assert s.generation == 1 and s.call(root, "get_count") == 0


def test_structural_diff_reported_and_refused_when_strict(models):
    s = bc_session.Session(models("n"))
    root = s.construct()
    s.call(root, "step")
    m = models("st", extra="sub_c b;")
    with pytest.raises(ReloadRefused) as ei:
        s.reload(m, strict=True)
    assert ei.value.to_json()["reason"] == "structure"
    r = s.reload(m)
    assert r["structure"]["added"] == ["b"]
    assert "b" not in r["migrated"] and "a" in r["migrated"]
    assert s.call(root, "a_hits") == 1


def test_reload_event(models):
    sink = MemorySink()
    s = bc_session.Session(models("n"), events=sink, watch=ev.EV_RELOAD)
    s.construct()
    r = s.reload(models("n1", inc="3"))
    evs = [e for e in sink.events if e.kind == "reload"]
    assert len(evs) == 1 and evs[0].detail is r


def test_reload_without_a_tree_swaps_the_image(models):
    s = bc_session.Session(models("n"))
    m1 = models("n1", inc="5")
    r = s.reload(m1)
    assert r["applied"] and r["migrated"] == [] and s.model is m1
    root = s.construct()
    s.call(root, "step")
    assert s.call(root, "get_count") == 5


def test_c_session_reload_swaps_the_image_for_the_next_test(models):
    """P4.6b: on the C engine each test runs on a fresh engine of the
    session's model, so a reload swaps the image (applied at once, the
    reload event raised) and the next test runs N+1's code."""
    pytest.importorskip("zuspec.rt.eng")
    sink = MemorySink()
    s = bc_session.Session(models("n"), engine="c", events=sink,
                           watch=ev.EV_RELOAD)
    s.run_test("top_c::step")
    m1 = models("n1", inc="5")
    r = s.reload(m1)
    assert r["applied"] and (r["old"], r["new"]) == (1, 2) and s.model is m1
    assert [e.detail for e in sink.events if e.kind == "reload"] == [r]
    s.run_test("top_c::step")
    assert s.generation == 2 and s.history == [r]


def test_coverage_spans_generations(models):
    """P3.6b3: N's and N+1's hits count on the same points (keyed by scope
    and block path, not pc); the points take N+1's lines."""
    from pssc.testing.coverage import Coverage
    m = models("n")
    cov = Coverage(m)
    s = bc_session.Session(m, events=cov, watch=Coverage.WATCH | ev.EV_RELOAD)
    root = s.construct()
    s.call(root, "step")
    s.call(root, "step")
    step = [p for p in cov.stmts.values() if p["scope"] == "top_c::step"]
    assert step and all(p["hits"] == 2 for p in step)
    line, n = step[0]["line"], len(cov.stmts)
    s.reload(models("n1", inc="10", extra="\n\n"))   # moves step 2 lines down
    s.call(root, "step")
    assert len(cov.stmts) == n
    assert all(p["hits"] == 3 for p in step)
    assert step[0]["line"] == line + 2
    assert s.call(root, "get_count") == 12


# --- P3.6b2: `pssc dbg reload` ----------------------------------------------

DUT = """\
import function bit[32] plat_id();
component dut_c {
    function bit[32] id() { return plat_id() + %(add)s; }
}
"""

TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    rt_c rt;
    @pssunit_pkg::test
    function void t_a() { rt.check_eq_int(dut.id(), 0x5A, "id"); }
    %(more)s
}
"""

DOUBLES = """\
import pssc.testing.vm as pv

@pv.implements("plat_id")
def plat_id(ctx):
    return %s
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    import sys
    from pssc.devenv.debug import sessions
    (tmp_path / "dut.pss").write_text(DUT % {"add": "0"})
    (tmp_path / "t.pss").write_text(TESTS % {"more": ""})
    (tmp_path / "rl_doubles.py").write_text(DOUBLES % "0x5A")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "rl_doubles", raising=False)
    reg = sessions.Registry()
    monkeypatch.setattr(sessions, "REGISTRY", reg)
    yield tmp_path
    reg.end_all()


def _dbg(capsys, *argv):
    import json
    from pssc import cli
    rc = cli.main(["dbg", *argv, "--json"])
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if out.out else None), out.err


def test_dbg_reload_is_staged_until_the_run_ends(project, capsys):
    from pssc.devenv.debug import sessions
    rc, doc, err = _dbg(capsys, "start", ".", "tests_c::t_a", "--seed", "1",
                        "--platform", "py:rl_doubles")
    assert rc == 0 and doc["reason"] == "entry", err
    (project / "dut.pss").write_text(DUT % {"add": "1"})
    (project / "rl_doubles.py").write_text(DOUBLES % "0x59")
    rc, r, err = _dbg(capsys, "reload")
    assert rc == 0, err
    assert (r["session"], r["old"], r["new"], r["applied"]) == ("1", 1, 2, False)
    assert r["reimported"] == ["rl_doubles"]
    live = sessions.REGISTRY.get()
    assert live.session.generation == 1           # the run is on N still
    rc, doc, _ = _dbg(capsys, "run")
    # N's code (+0) with the re-imported double (0x59): doubles apply at once
    assert doc["event"] == "exited" and doc["checks"]["failed"] == 1
    assert live.session.generation == 2 and live.session.history[-1]["applied"]
    rc, r, _ = _dbg(capsys, "reload")             # no run going: applied
    assert r["applied"] and (r["old"], r["new"]) == (2, 3)


def test_c_dbg_reload_is_staged_until_the_run_ends(project, capsys,
                                                  monkeypatch):
    """P4.6b: as on py, a reload while a C debug run is going is staged; the
    run finishes on N (its own engine) and N+1 applies when it ends."""
    from pssc.devenv.debug import sessions
    pytest.importorskip("zuspec.rt.eng.debug")
    monkeypatch.setenv("PSSC_ENGINE", "c")
    rc, doc, err = _dbg(capsys, "start", ".", "tests_c::t_a", "--seed", "1",
                        "--platform", "py:rl_doubles")
    assert rc == 0 and doc["reason"] == "entry", err
    live = sessions.REGISTRY.get()
    assert live.session.engine == "c"
    (project / "dut.pss").write_text(DUT % {"add": "1"})
    rc, r, err = _dbg(capsys, "reload")
    assert rc == 0, err
    assert (r["old"], r["new"], r["applied"]) == (1, 2, False)
    assert live.session.generation == 1
    rc, doc, _ = _dbg(capsys, "run")
    assert doc["event"] == "exited" and doc["status"] == "ok", doc
    assert live.session.generation == 2 and live.session.history[-1]["applied"]
    rc, r, _ = _dbg(capsys, "reload")             # no run going: applied
    assert r["applied"] and (r["old"], r["new"]) == (2, 3)


def test_dbg_reload_refused_says_why(project, capsys):
    rc, _, err = _dbg(capsys, "start", ".", "tests_c::t_a", "--platform",
                      "py:rl_doubles")
    assert rc == 0, err
    (project / "dut.pss").write_text(DUT.replace("plat_id()", "plat_id(int x)", 1)
                                     .replace("plat_id() +", "plat_id(1) +") % {"add": "0"})
    rc, doc, err = _dbg(capsys, "reload")
    assert rc == 1 and doc["error"]["reason"] == "api", doc
    assert doc["error"]["changes"][0]["symbol"] == "import:plat_id", doc
    rc, doc, err = _dbg(capsys, "reload", "--cancel")
    assert rc == 1 and "cancel" in err
