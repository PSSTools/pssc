"""Code coverage (P2.7, T2.7): the collector counts statements and branch
arms by stable ID; counts match a hand count; IDs survive moved lines."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources
from pssc.testing.coverage import Coverage

FUNCS = """\
component cov_c {
    function int sum(int n) {
        int acc;
        acc = 0;
        repeat (i : n) {
            if (i % 2 == 0) {
                acc += i;
            }
        }
        return acc;
    }
    function int never(int n) {
        return n;
    }
}
"""

ACTIONS = """\
component pss_top {
    int x;
    exec init_down { x = 1; }
    action a_t {
        rand bit[4] v;
        constraint v > 3;
        exec body {
            if (v > 3) { comp.x = 2; }
        }
    }
    action t_t {
        activity {
            do a_t;
            do a_t;
        }
    }
}
"""


def _lower(tmp_path_factory, text, comp, **kw):
    p = tmp_path_factory.mktemp("cov") / "cov.pss"
    p.write_text(text)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors, c.errors
    return bc_session.lower(c, comp, debug=True, **kw)


@pytest.fixture(scope="module")
def funcs(tmp_path_factory):
    return _lower(tmp_path_factory, FUNCS, "cov_c", functions=["*"])


def _call(model, fn, *args):
    cov = Coverage(model)
    s = bc_session.Session(model, events=cov, watch=Coverage.WATCH)
    ret = s.call(s.construct(), fn, *args)
    return ret, cov


def _hits(table):
    return {k: p["hits"] for k, p in table.items()}


def test_counts_match_a_hand_count(funcs):
    ret, cov = _call(funcs, "sum", 5)
    assert ret == 0 + 2 + 4
    st = _hits(cov.stmts)
    # int acc; acc = 0; repeat; if (x5); acc += i (i = 0, 2, 4); return
    assert {k: v for k, v in st.items() if k.startswith("cov_c::sum/")} == {
        "cov_c::sum/0": 1, "cov_c::sum/1": 1, "cov_c::sum/2": 1,
        "cov_c::sum/2/0": 5, "cov_c::sum/2/0/then/0": 3, "cov_c::sum/3": 1}
    assert _hits(cov.arms) == {"cov_c::sum/2/0/then": 3, "cov_c::sum/2/0/else": 2}
    assert cov.arms["cov_c::sum/2/0/else"]["implicit"] is True
    assert cov.arms["cov_c::sum/2/0/then"]["branch"] == "cov_c::sum/2/0"


def test_unreached_points_are_reported(funcs):
    _, cov = _call(funcs, "sum", 0)
    st = _hits(cov.stmts)
    assert st["cov_c::never/0"] == 0
    assert st["cov_c::sum/2/0"] == 0
    rep = cov.report()
    assert rep["summary"]["arms"] == {"total": 2, "hit": 0}
    assert rep["summary"]["statements"]["total"] == len(st)
    assert rep["statements"]["cov_c::sum/1"]["line"] == 4
    json.dumps(rep)


def test_ids_survive_moved_lines(tmp_path_factory, funcs):
    moved = _lower(tmp_path_factory, "// header\n\n" + FUNCS, "cov_c", functions=["*"])
    a, b = _call(funcs, "sum", 5)[1].report(), _call(moved, "sum", 5)[1].report()
    for kind in ("statements", "arms"):
        assert list(a[kind]) == list(b[kind])
        for pid, p in a[kind].items():
            assert b[kind][pid]["hits"] == p["hits"]
            assert b[kind][pid]["line"] == p["line"] + 2


def test_action_exec_and_init_points(tmp_path_factory):
    m = _lower(tmp_path_factory, ACTIONS, "pss_top")
    cov = Coverage(m)
    s = bc_session.Session(m, events=cov, watch=Coverage.WATCH)
    s.construct()
    s.run_entry(m.entries["t_t"].coro, name="t_t", seed=1)
    assert _hits(cov.arms) == {"a_t/body/0/then": 2, "a_t/body/0/else": 0}
    st = _hits(cov.stmts)
    assert st["a_t/body/0"] == 2 and st["a_t/body/0/then/0"] == 2
    assert st["pss_top/init_down/0"] >= 1


def test_forwards_events(funcs):
    from zuspec.be.bc.trace import MemorySink
    inner = MemorySink()
    cov = Coverage(funcs, forward=inner)
    s = bc_session.Session(funcs, events=cov, watch=Coverage.WATCH)
    s.call(s.construct(), "sum", 1)
    kinds = [e.kind for e in inner.events]
    assert kinds.count("branch") == 1 and "stmt" in kinds


# -- pssc test --cov (P2.7c1) -------------------------------------------------

DUT = """\
component dut_c {
    function int sign(int n) {
        if (n < 0) {
            return -1;
        } else {
            return 1;
        }
    }
}
"""

TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    rt_c rt;
    @pssunit_pkg::test
    function void t_pos() {
        rt.check_eq_int(dut.sign(3), 1, "pos");
    }
    @pssunit_pkg::test
    function void t_pos2() {
        rt.check_eq_int(dut.sign(5), 1, "pos2");
    }
}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "dut.pss").write_text(DUT)
    (tmp_path / "tests" / "t.pss").write_text(TESTS)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _sign_arms(report):
    return {p["arm"]: p["hits"] for p in report["arms"].values()
            if p["scope"] == "dut_c::sign"}


def test_cov_writes_the_report(project, capsys):
    from pssc.cli import main
    assert main(["test", ".", "--no-ctrf", "--cov", "cov.json"]) == 0
    rep = json.loads((project / "cov.json").read_text())
    assert rep["version"] == 1
    # both tests take the else arm; the then arm is reported, unreached
    assert _sign_arms(rep) == {"then": 0, "else": 2}
    s = rep["summary"]["arms"]
    assert s["hit"] < s["total"]


def test_cov_counts_the_same_under_j2(project, capsys):
    from pssc.cli import main
    assert main(["test", ".", "--no-ctrf", "--cov", "a.json"]) == 0
    assert main(["test", ".", "--no-ctrf", "--cov", "b.json", "-j", "2"]) == 0
    a = json.loads((project / "a.json").read_text())
    b = json.loads((project / "b.json").read_text())
    assert a == b


def test_cov_needs_the_debug_image(project, capsys):
    from pssc.cli import main
    assert main(["test", ".", "--no-ctrf", "--cov", "c.json",
                 "--no-debug"]) != 0
    assert "--no-debug" in capsys.readouterr().err
    assert not (project / "c.json").exists()


# -- the boundary (P2.7c2) ----------------------------------------------------

def _scopes(report):
    return {p["scope"] for kind in ("statements", "arms")
            for p in report[kind].values()}


def test_cov_leaves_out_the_tests(project, capsys):
    from pssc.cli import main
    assert main(["test", ".", "--no-ctrf", "--cov", "cov.json"]) == 0
    scopes = _scopes(json.loads((project / "cov.json").read_text()))
    assert "dut_c::sign" in scopes
    assert not any(s.startswith(("tests_c", "pssunit_pkg", "rt_c"))
                   for s in scopes), scopes


BOUNDARY = """\
import pssunit_pkg::*;
package lib_pkg {
    component base_c {
        function int twice(int n) { return 2 * n; }
    }
}
component part_c {
    function int inc(int n) { return n + 1; }
}
component dut_c : lib_pkg::base_c {
    part_c part;
    function int sign(int n) {
        if (n < 0) { return -1; }
        return 1;
    }
    @pssunit_pkg::nocover
    function int debug_dump(int n) { return n; }
}
component other_c {
    function int dec(int n) { return n - 1; }
}
component helper_mock_c : pssunit_pkg::mock_c {
    function int fake(int n) { return n; }
}
component top_c : pssunit_pkg::fixture_c {
    dut_c dut;
    other_c other;
    helper_mock_c m;
    rt_c rt;
    @pssunit_pkg::test
    function void t_all() {
        rt.check_eq_int(dut.sign(3) + dut.twice(1) + dut.part.inc(1)
                        + dut.debug_dump(0) + other.dec(1) + m.fake(0), 5, "all");
    }
    action t_act_a : test_a {
        exec body { comp.rt.check_eq_int(comp.dut.sign(1), 1, "act"); }
    }
}
"""


@pytest.fixture(scope="module")
def bctx(tmp_path_factory):
    from pssc.testing.discover import discover
    p = tmp_path_factory.mktemp("cov") / "b.pss"
    p.write_text(BOUNDARY)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors, c.errors
    return c, discover(c)


def test_boundary_default_drops_tests_mocks_nocover(bctx):
    from pssc.testing.coverage import boundary
    ctx, items = bctx
    keep = boundary(ctx, items)
    assert keep("dut_c::sign") and keep("other_c::dec") and keep("dut_c::twice")
    assert keep("part_c::inc")
    assert not keep("top_c::t_all")
    assert not keep("t_act_a")              # an action test's exec scope
    assert not keep("helper_mock_c::fake")
    assert not keep("dut_c::debug_dump")
    assert not keep("pssunit_pkg::rt_c::check_eq_int")


def test_boundary_scope_is_the_component_tree(bctx):
    from pssc.testing.coverage import boundary
    ctx, items = bctx
    keep = boundary(ctx, items, ["dut_c"])
    # its own, its base's (inherited) and its sub-component's code
    assert keep("dut_c::sign") and keep("lib_pkg::base_c::twice")
    assert keep("part_c::inc")
    assert not keep("other_c::dec")
    assert not keep("dut_c::debug_dump")
    with pytest.raises(ValueError):
        boundary(ctx, items, ["no_such_c"])


def test_cov_scope_and_under_test(tmp_path, monkeypatch, capsys):
    from pssc.cli import main
    (tmp_path / "b.pss").write_text(BOUNDARY)
    monkeypatch.chdir(tmp_path)
    assert main(["test", ".", "--no-ctrf", "--cov", "a.json"]) == 0
    a = _scopes(json.loads((tmp_path / "a.json").read_text()))
    assert "other_c::dec" in a and "dut_c::sign" in a
    assert main(["test", ".", "--no-ctrf", "--cov", "b.json",
                 "--cov-scope", "other_c"]) == 0
    assert _scopes(json.loads((tmp_path / "b.json").read_text())) == {"other_c::dec"}
    # the same boundary, written on the fixture
    (tmp_path / "b.pss").write_text(BOUNDARY.replace(
        "component top_c", '@pssunit_pkg::under_test {.comp = "other_c"}\ncomponent top_c'))
    assert main(["test", ".", "--no-ctrf", "--cov", "c.json"]) == 0
    assert _scopes(json.loads((tmp_path / "c.json").read_text())) == {"other_c::dec"}
    assert main(["test", ".", "--no-ctrf", "--cov-scope", "other_c"]) != 0
    assert "--cov-scope needs --cov" in capsys.readouterr().err


# -- register and field coverage (P2.7d) ----------------------------------------

from pssc.testing import vm as pv  # noqa: E402

from .test_pssunit_fake import BASE, FIELD_ACCESS_PSS  # noqa: E402

#: FIELD_ACCESS_PSS with an enum field (``rest`` narrowed to make room)
ENUM_PSS = FIELD_ACCESS_PSS.replace(
    "bit[29] rest;", "mode_e mode;\n        bit[27] rest;").replace(
    "struct st_s", "enum mode_e : bit[2] { OFF, ON, AUTO }\n    struct st_s")


def _regs_session(tmp_path, text=FIELD_ACCESS_PSS):
    src = tmp_path / "t.pss"
    src.write_text(text)
    model = pv.load([src], "t_pkg::dev_c").model
    fake, cov = pv.RegisterFake(), Coverage(model)
    s = pv.Session(model, platform=fake, events=cov, watch=Coverage.WATCH)
    root = s.construct()
    s.call(root, "initialize", BASE)
    return s, fake, cov, root


def test_register_reads_writes_and_bins(tmp_path):
    s, fake, cov, root = _regs_session(tmp_path)
    fake.poke_reg("regs.st", 0b111)
    s.call(root, "wr_st", 0b011)            # err W1C: cleared
    s.call(root, "rd_st")                   # reads 0b001: done RC, cleared
    s.call(root, "rd_st")                   # reads 0
    regs = cov.report()["registers"]
    assert sorted(regs) == ["regs.ctl", "regs.st", "regs.sts"]
    st = regs["regs.st"]
    assert (st["reads"], st["writes"]) == (2, 1)
    done, err, irq = (st["fields"][n]["bins"] for n in ("done", "err", "irq"))
    assert done == {"bit0=0": 1, "bit0=1": 2, "zero": 1, "max": 2, "cleared": 1}
    assert err == {"bit0=0": 2, "bit0=1": 1, "zero": 2, "max": 1, "cleared": 1}
    assert "cleared" not in irq             # RW: no side effect bin
    assert st["fields"]["done"]["kind"] == "RC"
    assert regs["regs.ctl"]["reads"] == regs["regs.ctl"]["writes"] == 0
    summ = cov.report()["summary"]
    assert summ["registers"] == {"total": 3, "hit": 1}
    assert 0 < summ["bins"]["hit"] < summ["bins"]["total"]


def test_register_side_effect_not_seen(tmp_path):
    s, fake, cov, root = _regs_session(tmp_path)
    s.call(root, "wr_st", 0b010)            # err W1C was 0: nothing cleared
    s.call(root, "rd_st")                   # done RC was 0: nothing cleared
    f = cov.report()["registers"]["regs.st"]["fields"]
    assert f["err"]["bins"]["cleared"] == f["done"]["bins"]["cleared"] == 0


def test_register_enum_bins(tmp_path):
    s, fake, cov, root = _regs_session(tmp_path, ENUM_PSS)
    s.call(root, "wr_st", 2 << 3)           # mode = AUTO
    mode = cov.report()["registers"]["regs.st"]["fields"]["mode"]["bins"]
    assert {k: v for k, v in mode.items() if not k.startswith("bit")} == \
        {"OFF": 0, "ON": 0, "AUTO": 1}


def test_register_counts_merge(tmp_path):
    s, fake, cov, root = _regs_session(tmp_path)
    fake.poke_reg("regs.st", 0b001)
    s.call(root, "rd_st")
    one = cov.report()
    both = Coverage()
    both.merge(one)
    both.merge(one)
    st = both.report()["registers"]["regs.st"]
    assert st["reads"] == 2
    assert st["fields"]["done"]["bins"]["cleared"] == 2
    assert one["registers"]["regs.st"]["reads"] == 1    # not aliased
