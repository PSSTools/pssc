"""Code and register coverage as a covsight UCIS database (P2.8b-d): the
statements, branch arms and registers round-trip through a ``.cdb`` file,
with a history node per test and its test-coveritem associations."""

from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")
pytest.importorskip("covsight.core")

from covsight.core.api import (CoverTypeT, HistoryNodeKind, ScopeTypeT,
                               TestStatusT as UcisStatus)
from covsight.core.api.enums.cover_flags import BranchCoverFlagsT
from covsight.core.ncdb.dfs_util import iter_flat_bins
from covsight.core.ncdb.ncdb_reader import NcdbReader

from pssc.testing import bc_session
from pssc.testing.cov_ucis import comp_of, write_cdb
from pssc.testing.coverage import Coverage

from .test_pssunit_coverage import (ACTIONS, _call, _lower, _regs_session,  # noqa: F401
                                    funcs, project)


def _read(report, tmp_path):
    out = tmp_path / "cov.cdb"
    write_cdb(report, str(out))
    return NcdbReader().read(str(out))


def _items(scope, kind):
    return {i.getName(): i for i in scope.coverItems(kind)}


def _by_name(parent, kind):
    return {s.getScopeName(): s for s in parent.scopes(kind)}


def test_comp_of():
    assert comp_of("sub_c::f") == "sub_c"
    assert comp_of("lib_pkg::sub_c::a_t") == "lib_pkg::sub_c"
    assert comp_of("pss_top") == "pss_top"


def test_statements_and_branches_round_trip(funcs, tmp_path):
    _, cov = _call(funcs, "sum", 5)
    rep = cov.report()
    db = _read(rep, tmp_path)

    insts = _by_name(db, ScopeTypeT.INSTANCE)
    assert list(insts) == ["cov_c"]
    du = insts["cov_c"].getInstanceDu()
    assert du.getScopeName() == "cov_c" and du.getAttribute("language") == "PSS"

    blocks = _by_name(insts["cov_c"], ScopeTypeT.BLOCK)
    assert set(blocks) == {"cov_c::sum", "cov_c::never"}
    st = _items(blocks["cov_c::sum"], CoverTypeT.STMTBIN)
    assert {n: i.getCoverData().data for n, i in st.items()} == {
        "0": 1, "1": 1, "2": 1, "2/0": 5, "2/0/then/0": 3, "3": 1}
    assert st["1"].getSourceInfo().line == 4
    assert st["1"].getSourceInfo().file.getFileName().endswith("cov.pss")
    assert _items(blocks["cov_c::never"], CoverTypeT.STMTBIN)["0"].getCoverData().data == 0

    brs = _by_name(blocks["cov_c::sum"], ScopeTypeT.BRANCH)
    assert list(brs) == ["2/0"]
    assert brs["2/0"].getSourceInfo().line == rep["statements"]["cov_c::sum/2/0"]["line"]
    arms = _items(brs["2/0"], CoverTypeT.BRANCHBIN)
    assert {n: i.getCoverData().data for n, i in arms.items()} == {"then": 3, "else": 2}
    assert arms["else"].getCoverFlags() & int(BranchCoverFlagsT.IS_BR_ELSE)
    assert not arms["then"].getCoverFlags() & int(BranchCoverFlagsT.IS_BR_ELSE)

    n = sum(1 for b in blocks.values() for _ in b.coverItems(CoverTypeT.STMTBIN))
    assert n == rep["summary"]["statements"]["total"]


def test_action_and_init_scopes(tmp_path_factory, tmp_path):
    m = _lower(tmp_path_factory, ACTIONS, "pss_top")
    cov = Coverage(m)
    s = bc_session.Session(m, events=cov, watch=Coverage.WATCH)
    s.construct()
    s.run_entry(m.entries["t_t"].coro, name="t_t", seed=1)
    db = _read(cov.report(), tmp_path)
    insts = _by_name(db, ScopeTypeT.INSTANCE)
    # pss_top's own action has no ``::`` scope: it is its own unit
    assert {"pss_top", "a_t"} <= set(insts)
    init = _items(_by_name(insts["pss_top"], ScopeTypeT.BLOCK)["pss_top"],
                  CoverTypeT.STMTBIN)
    assert init["init_down/0"].getCoverData().data >= 1
    body = _by_name(insts["a_t"], ScopeTypeT.BLOCK)["a_t"]
    arms = _items(_by_name(body, ScopeTypeT.BRANCH)["body/0"], CoverTypeT.BRANCHBIN)
    assert {n: i.getCoverData().data for n, i in arms.items()} == {"then": 2, "else": 0}


def test_comp_mapper(funcs, tmp_path):
    _, cov = _call(funcs, "sum", 1)
    out = tmp_path / "m.cdb"
    write_cdb(cov.report(), str(out), comp=lambda s: "top_c")
    db = NcdbReader().read(str(out))
    assert list(_by_name(db, ScopeTypeT.INSTANCE)) == ["top_c"]


def _bins(scope):
    return {p.getScopeName(): {b.getName(): b.getCoverData().data
                               for b in p.coverItems(CoverTypeT.CVGBIN)}
            for p in scope.scopes(ScopeTypeT.COVERPOINT)}


def test_registers_as_covergroups(tmp_path):
    s, fake, cov, root = _regs_session(tmp_path)
    fake.poke_reg("regs.st", 0b111)
    s.call(root, "wr_st", 0b011)
    s.call(root, "rd_st")
    s.call(root, "rd_st")
    rep = cov.report()
    st = rep["registers"]["regs.st"]
    db = _read(rep, tmp_path)
    inst = _by_name(db, ScopeTypeT.INSTANCE)[st["comp_type"]]
    cgs = _by_name(inst, ScopeTypeT.COVERGROUP)
    assert st["type"] in cgs
    cis = _by_name(cgs[st["type"]], ScopeTypeT.COVERINSTANCE)
    assert "regs.st" in cis
    pts = _bins(cis["regs.st"])
    assert pts["access"] == {"read": 2, "write": 1}
    assert pts["done"] == {"bit0=0": 1, "bit0=1": 2, "zero": 1, "max": 2, "cleared": 1}
    assert pts["err"] == st["fields"]["err"]["bins"]
    # the type's own points sum its instances
    tot = _bins(cgs[st["type"]])
    assert tot["access"] == {
        b: sum(_bins(c)["access"][b] for c in cis.values()) for b in ("read", "write")}


def test_registers_without_types(tmp_path):
    rep = {"registers": {"r0": {"comp": "", "reads": 1, "writes": 0, "fields": {
        "f": {"kind": "RW", "width": 1, "bins": {"bit0=0": 1, "bit0=1": 0}}}}}}
    db = _read(rep, tmp_path)
    cg = _by_name(_by_name(db, ScopeTypeT.INSTANCE)["registers"], ScopeTypeT.COVERGROUP)["r0"]
    assert _bins(_by_name(cg, ScopeTypeT.COVERINSTANCE)["r0"])["f"] == {"bit0=0": 1, "bit0=1": 0}


# -- tests and associations (P2.8d) --------------------------------------------

def _stmt(scope, path, hits):
    return {"scope": scope, "path": path, "hits": hits, "file": "/x/a.pss", "line": 3}


def _arm(scope, branch, arm, hits):
    return {"scope": scope, "branch": branch, "arm": arm, "hits": hits}


def _reg(reads, writes, b0, b1):
    return {"type": "st_r", "comp_type": "dut_c", "reads": reads, "writes": writes,
            "fields": {"f": {"kind": "RW", "width": 1,
                             "bins": {"bit0=0": b0, "bit0=1": b1}}}}


def _report(s0, s1, then, els, r0, r1):
    return {"statements": {"c::f/0": _stmt("c::f", "0", s0),
                           "c::f/1": _stmt("c::f", "1", s1)},
            "arms": {"c::f/1#then": _arm("c::f", "c::f/1", "then", then),
                     "c::f/1#else": _arm("c::f", "c::f/1", "else", els)},
            "registers": {"r0": r0, "r1": r1}}


def _flat(db):
    """flat bin index -> (path of scope names, bin name)."""
    paths = {}

    def walk(scope, pre):
        paths[id(scope)] = pre + scope.getScopeName()
        for c in scope.scopes(ScopeTypeT.ALL):
            walk(c, paths[id(scope)] + "/")
    for top in db.scopes(ScopeTypeT.ALL):
        walk(top, "")
    return {i: (paths[id(scope)], name) for i, scope, _, name in iter_flat_bins(db)}


def test_history_nodes_and_associations(tmp_path):
    t1 = _report(1, 1, 1, 0, _reg(1, 0, 1, 0), _reg(0, 0, 0, 0))
    t2 = _report(2, 0, 0, 0, _reg(0, 0, 0, 0), _reg(0, 1, 0, 1))
    merged = _report(3, 1, 1, 0, _reg(1, 0, 1, 0), _reg(0, 1, 0, 1))
    out = tmp_path / "t.cdb"
    write_cdb(merged, str(out), tests=[
        {"name": "top::t1", "status": "pass", "seed": 7, "coverage": t1, "engine": "pyvm"},
        {"name": "top::t2", "status": "fail", "seed": 9, "coverage": t2}])
    db = NcdbReader().read(str(out))
    hs = list(db.historyNodes(HistoryNodeKind.TEST))
    assert [h.getLogicalName() for h in hs] == ["top::t1", "top::t2"]
    assert [h.getTestStatus() for h in hs] == [UcisStatus.OK, UcisStatus.ERROR]
    assert [h.getSeed() for h in hs] == ["7", "9"]
    assert hs[0].getAttribute("pssunit.status") == "pass"
    assert hs[0].getAttribute("pssunit.engine") == "pyvm"
    every = list(db.historyNodes(HistoryNodeKind.ALL))
    i1, i2 = every.index(hs[0]), every.index(hs[1])
    tc = db.get_test_coverage_api()
    assert tc.has_test_associations()
    flat = _flat(db)

    def got(i):
        return {flat[b]: next(n for h, _, n in tc.get_tests_for_coveritem(b).tests if h == i)
                for b in tc.get_coveritems_for_test(i)}
    g1, g2 = got(i1), got(i2)
    leaf = {(p.rsplit("/", 1)[-1], b): n for (p, b), n in g1.items()}
    assert leaf[("c::f", "0")] == 1 and leaf[("c::f", "1")] == 1
    assert leaf[("1", "then")] == 1 and ("1", "else") not in leaf
    # a register bin counts on its instance and on its type
    assert {p: n for (p, b), n in g1.items() if b == "read"} == {
        "dut_c/st_r/access": 1, "dut_c/st_r/r0/access": 1}
    assert not any("r1" in p for p, _ in g1)
    assert {p: n for (p, b), n in g2.items() if b == "bit0=1"} == {
        "dut_c/st_r/f": 1, "dut_c/st_r/r1/f": 1}
    assert [n for (p, b), n in g2.items() if b == "0"] == [2]


def test_cov_cdb_from_the_cli(project, capsys):
    from pssc.cli import main
    assert main(["test", ".", "--no-ctrf", "--cov", "cov.cdb"]) == 0
    db = NcdbReader().read(str(project / "cov.cdb"))
    hs = list(db.historyNodes(HistoryNodeKind.TEST))
    assert len(hs) == 2 and all(h.getTestStatus() == UcisStatus.OK for h in hs)
    tc = db.get_test_coverage_api()
    every = list(db.historyNodes(HistoryNodeKind.ALL))
    flat = _flat(db)
    for h in hs:
        names = {flat[b][1] for b in tc.get_coveritems_for_test(every.index(h))}
        assert "else" in names and "then" not in names
    assert not (project / "cov.json").exists()
