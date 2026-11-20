"""Operation envelope coverage (P5.7a): per operation under test, the
calls and a value bin per parameter and for the result; only the
outermost operation call counts; reports merge."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources
from pssc.testing.coverage import Coverage, _point_bins, envelope_ops

DUT = """\
enum mode_e { A, B, C };
component dut_c {
    function bit[8] f(mode_e m, bit[8] v, bool b, int s) {
        return g(v);
    }
    function bit[8] g(bit[8] v) { return v; }
    function void h(string name, bit[2] k) { }
}
"""


@pytest.fixture(scope="module")
def dut(tmp_path_factory):
    p = tmp_path_factory.mktemp("env") / "dut.pss"
    p.write_text(DUT)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors, c.errors
    return c, bc_session.lower(c, "dut_c", debug=True, functions=["*"])


def _bins(point):
    return {b for b, n in point["bins"].items() if n}


def test_ops_and_bins_from_the_types(dut):
    ctx, _ = dut
    ops = envelope_ops(ctx)
    assert {"dut_c::f", "dut_c::g", "dut_c::h"} <= set(ops)
    f = ops["dut_c::f"]
    assert [p["name"] for p in f["params"]] == ["m", "v", "b", "s"]
    assert [_point_bins(p) for p in f["params"]] == [
        ["A", "B", "C"], ["zero", "other", "max"], ["false", "true"],
        ["min", "neg", "zero", "other", "max"]]
    assert _point_bins(f["result"]) == ["zero", "other", "max"]
    h = ops["dut_c::h"]
    assert h["result"] is None
    assert _point_bins(h["params"][1]) == ["0", "1", "2", "3"]
    # the boundary decides which operations count
    assert set(envelope_ops(ctx, lambda s: s.endswith("::g"))) == {"dut_c::g"}


def _caller(tmp_path):
    """A test-like caller, so the operations are called (not the entry)."""
    p = tmp_path / "t.pss"
    p.write_text(DUT + """\
component top_c {
    dut_c dut;
    function void run() {
        bit[8] r;
        r = dut.f(mode_e::B, 255, true, -3);
        r = dut.g(0);
        r = dut.g(7);
    }
}
""")
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors, c.errors
    ops = envelope_ops(c, lambda s: s.startswith("dut_c::"))
    m = bc_session.lower(c, "top_c", debug=True, functions=["*"])
    cov = Coverage(m, ops=ops)
    s = bc_session.Session(m, events=cov, watch=Coverage.WATCH)
    s.call(s.construct(), "run")
    return cov


def test_counts_args_and_result_outermost_only(tmp_path):
    cov = _caller(tmp_path)
    env = cov.report()["envelope"]
    f, g = env["dut_c::f"], env["dut_c::g"]
    assert f["calls"] == 1
    assert {p: _bins(v) for p, v in f["params"].items()} == {
        "m": {"B"}, "v": {"max"}, "b": {"true"}, "s": {"neg"}}
    assert _bins(f["result"]) == {"max"}
    # f's own call of g is not counted: only the test's two
    assert g["calls"] == 2
    assert _bins(g["params"]["v"]) == {"zero", "other"}
    assert env["dut_c::h"]["calls"] == 0
    assert list(env["dut_c::h"]["params"]) == ["k"]      # no string point


def test_summary_and_merge(tmp_path):
    cov = _caller(tmp_path)
    rep = cov.report()
    s = rep["summary"]["envelope"]
    assert s["total"] == (3 + 3 + 2 + 5 + 3) + (3 + 3) + 4     # f, g, h
    assert s["hit"] == (4 + 1) + (2 + 2) + 0
    both = Coverage()
    both.merge(rep)
    both.merge(json.loads(json.dumps(rep)))
    g = both.report()["envelope"]["dut_c::g"]
    assert g["calls"] == 4 and g["params"]["v"]["bins"]["zero"] == 2
    # a boundary that drops an operation drops its envelope too
    assert "dut_c::g" not in cov.report(lambda k: k != "dut_c::g")["envelope"]


# -- pssc test --cov --------------------------------------------------------

TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    rt_c rt;
    @pssunit_pkg::test
    function void t_g() {
        rt.check_eq_int(dut.g(3), 3, "g");
    }
    @pssunit_pkg::test
    function void t_f() {
        rt.check_eq_int(dut.f(mode_e::A, 0, false, 0), 0, "f");
    }
}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    # the C target (checked by `pssc test`) has no strings
    (tmp_path / "src" / "dut.pss").write_text(DUT.replace("string name, ", ""))
    (tmp_path / "tests" / "t.pss").write_text(TESTS)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cov_reports_the_envelope(project, capsys):
    from pssc.cli import main
    assert main(["test", ".", "--no-ctrf", "--cov", "a.json"]) == 0
    assert main(["test", ".", "--no-ctrf", "--cov", "b.json", "-j", "2"]) == 0
    a = json.loads((project / "a.json").read_text())
    b = json.loads((project / "b.json").read_text())
    assert a == b
    env = a["envelope"]
    # the tests' component is not under test; the rt_c checks are pssunit's
    assert set(env) == {"dut_c::f", "dut_c::g", "dut_c::h"}
    assert env["dut_c::g"]["calls"] == 1 and env["dut_c::f"]["calls"] == 1
    assert _bins(env["dut_c::f"]["params"]["m"]) == {"A"}
    assert _bins(env["dut_c::f"]["result"]) == {"zero"}


def test_cov_envelope_on_the_c_engine(project):
    """The C engine's call events carry the arguments and the result, so
    its envelope is the Python engine's (P5.7c)."""
    pytest.importorskip("zuspec.rt.eng.host")
    from pssc.cli import main
    assert main(["test", ".", "--no-ctrf", "--cov", "p.json"]) == 0
    assert main(["test", ".", "--no-ctrf", "--cov", "c.json",
                 "--engine", "c"]) == 0
    p = json.loads((project / "p.json").read_text())
    c = json.loads((project / "c.json").read_text())
    assert c["envelope"] == p["envelope"]
    assert c["envelope"]["dut_c::g"]["calls"] == 1


# -- crosses and the .cdb (P5.7b) ---------------------------------------------

def _crossed(tmp_path):
    src = DUT.replace(
        "    function bit[8] f(",
        '    @pssunit_pkg::cover_cross {.params = "m,b"}\n'
        '    @pssunit_pkg::cover_cross {.params = "m,result"}\n'
        '    @pssunit_pkg::cover_cross {.params = "m,nope"}\n'
        "    function bit[8] f(")
    p = tmp_path / "x.pss"
    p.write_text(src + """\
component top_c {
    dut_c dut;
    function void run() {
        bit[8] r;
        r = dut.f(mode_e::B, 255, true, -3);
        r = dut.f(mode_e::A, 0, true, 0);
        r = dut.f(mode_e::B, 9, true, 1);
    }
}
""")
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors, c.errors
    ops = envelope_ops(c, lambda s: s.startswith("dut_c::"))
    m = bc_session.lower(c, "top_c", debug=True, functions=["*"])
    cov = Coverage(m, ops=ops)
    s = bc_session.Session(m, events=cov, watch=Coverage.WATCH)
    s.call(s.construct(), "run")
    return ops, cov


def test_crosses_on_request(tmp_path):
    ops, cov = _crossed(tmp_path)
    # a cross naming a point the operation does not have is dropped
    assert ops["dut_c::f"]["crosses"] == [["m", "b"], ["m", "result"]]
    rep = cov.report()
    x = rep["envelope"]["dut_c::f"]["crosses"]
    assert set(x) == {"m,b", "m,result"}
    assert len(x["m,b"]["bins"]) == 3 * 2
    assert _bins(x["m,b"]) == {"B,true", "A,true"}
    assert x["m,b"]["bins"]["B,true"] == 2
    assert _bins(x["m,result"]) == {"B,max", "A,zero", "B,other"}
    assert rep["envelope"]["dut_c::g"]["crosses"] == {}
    s = rep["summary"]["envelope"]
    assert s["total"] == (3 + 3 + 2 + 5 + 3) + 6 + 9 + (3 + 3) + 4
    both = Coverage()
    both.merge(rep)
    both.merge(rep)
    assert both.report()["envelope"]["dut_c::f"]["crosses"]["m,b"]["bins"][
        "B,true"] == 4


def test_envelope_round_trips_through_the_cdb(tmp_path):
    from pssc.devenv.cov_cmd import load_cdb
    from pssc.testing.cov_ucis import write_cdb
    _, cov = _crossed(tmp_path)
    rep = cov.report()
    db = tmp_path / "c.cdb"
    write_cdb(rep, str(db), tests=[{"name": "t", "status": "pass",
                                    "coverage": rep}])
    assert load_cdb(str(db))["envelope"] == rep["envelope"]
