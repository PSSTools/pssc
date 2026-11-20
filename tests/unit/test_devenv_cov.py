"""`pssc cov` gaps, report, lcov, cobertura (P2.9, T2.9)."""

import json
import os

import pytest

from pssc.cli import main
from pssc.devenv import cov_cmd

from .test_pssunit_coverage import project  # noqa: F401  (fixture)


def _gaps(capsys, *args):
    capsys.readouterr()
    assert main(["cov", "gaps", *args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def report(project, capsys):  # noqa: F811
    assert main(["test", ".", "--no-ctrf", "--cov", "cov.json"]) == 0
    return project / "cov.json"


def test_unhit_arm_with_source_context(report, capsys):
    doc = _gaps(capsys, str(report))
    assert doc["schema"] == "pssc.cov-gaps/1"
    (g,) = [g for g in doc["gaps"]
            if g["kind"] == "arm" and g["scope"] == "dut_c::sign"]
    assert g["arm"] == "then" and g["id"].endswith("/then")
    assert not g["implicit"]
    here = {c["line"]: c["text"] for c in g["context"]}
    assert "if (n < 0)" in here[g["line"]]
    assert set(here) == set(range(g["line"] - 2, g["line"] + 3))
    # the then arm's statement is never run either
    assert any(s["kind"] == "statement" and s["scope"] == "dut_c::sign"
               and "return -1" in {c["line"]: c["text"] for c in s["context"]}[s["line"]]
               for s in doc["gaps"])
    assert doc["summary"]["arm"] == sum(g["kind"] == "arm" for g in doc["gaps"])


def test_kind_and_context_options(report, capsys):
    doc = _gaps(capsys, str(report), "--kind", "arm", "--context", "0")
    assert {g["kind"] for g in doc["gaps"]} == {"arm"}
    assert doc["summary"]["statement"] == 0
    for g in doc["gaps"]:
        if g.get("file"):
            assert [c["line"] for c in g["context"]] == [g["line"]]


def test_source_root_finds_relative_files(report, tmp_path_factory, capsys,
                                          monkeypatch):
    rep = json.loads(report.read_text())
    for p in rep["arms"].values():
        if p.get("file"):
            p["file"] = os.path.relpath(p["file"], report.parent)
            assert not os.path.isabs(p["file"])
    other = tmp_path_factory.mktemp("elsewhere")
    (other / "cov.json").write_text(json.dumps(rep))
    monkeypatch.chdir(other)
    doc = _gaps(capsys, "cov.json", "--kind", "arm")
    assert all(g["context"] == [] for g in doc["gaps"] if g.get("file"))
    doc = _gaps(capsys, "cov.json", "--kind", "arm",
                "--source-root", str(report.parent))
    assert any(g["context"] for g in doc["gaps"])


def test_text_output(report, capsys):
    capsys.readouterr()
    assert main(["cov", "gaps", str(report)]) == 0
    out = capsys.readouterr().out
    assert "then arm never run (dut_c::sign/" in out
    assert "| " in out and "if (n < 0)" in out
    assert out.rstrip().endswith("not covered")


def test_register_and_bin_gaps():
    rep = {"version": 1, "statements": {}, "arms": {}, "registers": {
        "top.regs.a": {"comp": "top", "type": "a_t", "reads": 0, "writes": 0,
                       "fields": {"f": {"bins": {"zero": 0}}}},
        "top.regs.b": {"comp": "top", "type": "b_t", "reads": 1, "writes": 0,
                       "fields": {"f": {"bins": {"zero": 1, "max": 0}}}}}}
    gs = cov_cmd.gaps(rep)
    assert [(g["kind"], g["id"]) for g in gs] == [
        ("register", "top.regs.a"), ("bin", "top.regs.b.f:max")]
    assert cov_cmd.summary(gs) == {"statement": 0, "arm": 0, "register": 1,
                                   "bin": 1}


def test_not_a_report(tmp_path, capsys):
    (tmp_path / "x.json").write_text('{"a": 1}')
    assert main(["cov", "gaps", str(tmp_path / "x.json")]) == 2
    assert "not a pssunit.coverage/1 report" in capsys.readouterr().err
    assert main(["cov", "gaps", str(tmp_path / "none.json")]) == 2


# -- P2.9b: .cdb input, `pssc cov report` --------------------------------------

@pytest.fixture
def both(report, capsys):
    """The project's coverage as JSON and as a .cdb, from the same tests."""
    assert main(["test", ".", "--no-ctrf", "--cov", "cov.cdb"]) == 0
    return report, report.parent / "cov.cdb"


def test_cdb_reads_back_as_the_json_report(both, capsys):
    js, cdb = both
    a, b = cov_cmd.load(str(js)), cov_cmd.load(str(cdb))
    assert b["statements"] == a["statements"]
    assert b["arms"] == a["arms"]
    ids = [g["id"] for g in _gaps(capsys, str(js))["gaps"]]
    assert ids and [g["id"] for g in _gaps(capsys, str(cdb))["gaps"]] == ids


def test_cdb_implicit_arm_and_registers_read_back(tmp_path):
    from pssc.testing.cov_ucis import write_cdb
    arms = {"c::f/1/else": {"scope": "c::f", "path": "1/else", "hits": 2,
                            "branch": "c::f/1", "arm": "else", "implicit": True},
            "c::f/1/then": {"scope": "c::f", "path": "1/then", "hits": 0,
                            "file": "../src/a.pss", "line": 5,
                            "branch": "c::f/1", "arm": "then",
                            "implicit": False}}
    rep = {"version": 1, "statements": {}, "arms": arms, "registers": {
        "top.regs.a": {"comp": "top", "type": "a_t", "comp_type": "regs_c",
                       "reads": 0, "writes": 0,
                       "fields": {"f": {"bins": {"zero": 0}}}},
        "top.regs.b": {"comp": "top", "type": "b_t", "comp_type": "regs_c",
                       "reads": 1, "writes": 2,
                       "fields": {"f": {"bins": {"zero": 1, "max": 0}},
                                  "g": {"bins": {"bit0=1": 3}}}}}}
    write_cdb(rep, str(tmp_path / "r.cdb"))
    assert cov_cmd.load(str(tmp_path / "r.cdb"))["arms"] == arms
    back = cov_cmd.load(str(tmp_path / "r.cdb"))["registers"]
    for path, r in rep["registers"].items():
        b = back[path]
        assert (b["type"], b["comp_type"], b["reads"], b["writes"]) == (
            r["type"], r["comp_type"], r["reads"], r["writes"])
        assert {n: f["bins"] for n, f in b["fields"].items()} == {
            n: f["bins"] for n, f in r["fields"].items()}
    assert cov_cmd.gaps(cov_cmd.load(str(tmp_path / "r.cdb"))) == [
        dict(g, comp=None) if g["kind"] == "register" else g
        for g in cov_cmd.gaps(rep)]


def test_report_json(both, capsys):
    js, cdb = both
    docs = []
    for path in (js, cdb):
        capsys.readouterr()
        assert main(["cov", "report", str(path), "--json"]) == 0
        docs.append(json.loads(capsys.readouterr().out))
    doc = docs[0]
    assert doc["schema"] == "pssc.cov-report/1"
    assert doc["summary"] == json.loads(js.read_text())["summary"]
    (s,) = [s for s in doc["scopes"] if s["scope"] == "dut_c::sign"]
    assert s["arms"]["hit"] < s["arms"]["total"]
    assert s["file"].endswith(".pss")
    assert sum(s["statements"]["total"] for s in doc["scopes"]) \
        == doc["summary"]["statements"]["total"]
    assert {k: v for k, v in docs[1].items() if k != "report"} \
        == {k: v for k, v in doc.items() if k != "report"}


def test_report_text(report, capsys):
    capsys.readouterr()
    assert main(["cov", "report", str(report)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["scope", "statements", "arms"]
    assert any(l.startswith("dut_c::sign ") and "%" in l for l in out)
    assert out[-1].startswith("total: statements ")


def test_not_a_cdb(tmp_path, capsys):
    (tmp_path / "x.cdb").write_text("nonsense")
    assert main(["cov", "report", str(tmp_path / "x.cdb")]) == 2
    assert "not a coverage database" in capsys.readouterr().err
    assert main(["cov", "gaps", str(tmp_path / "none.cdb")]) == 2


def _lcov(path):
    """{SF: {"DA": {line: hits}, "BRDA": [(line, block, branch, hits)]}}"""
    files, cur = {}, None
    for l in path.read_text().splitlines():
        tag, _, val = l.partition(":")
        if tag == "SF":
            cur = files.setdefault(val, {"DA": {}, "BRDA": []})
        elif tag == "DA":
            ln, hits = val.split(",")[:2]
            cur["DA"][int(ln)] = int(hits)
        elif tag == "BRDA":
            cur["BRDA"].append(tuple(int(x) for x in val.split(",")))
    return files


def _line_of(src, text):
    return next(i for i, l in enumerate(src.read_text().splitlines(), 1)
                if text in l)


def test_lcov(both, project, capsys):  # noqa: F811
    js, cdb = both
    src = project / "src" / "dut.pss"
    assert main(["cov", "lcov", str(js), "-o", "a.info"]) == 0
    files = _lcov(project / "a.info")
    f = files[os.path.join("src", "dut.pss")]
    if_line = _line_of(src, "if (n < 0)")
    # sign(): the then arm (and its `return -1`) never runs
    assert sorted(h for ln, _, _, h in f["BRDA"] if ln == if_line)[0] == 0
    assert any(h > 0 for ln, _, _, h in f["BRDA"] if ln == if_line)
    assert f["DA"][_line_of(src, "return -1")] == 0
    assert f["DA"][if_line] > 0
    # the .cdb gives the same tracefile
    assert main(["cov", "lcov", str(cdb), "-o", "b.info"]) == 0
    assert (project / "b.info").read_text() == (project / "a.info").read_text()


def test_cobertura(report, project, capsys):  # noqa: F811
    import xml.etree.ElementTree as ET
    assert main(["cov", "cobertura", str(report), "-o", "c.xml"]) == 0
    root = ET.parse(project / "c.xml").getroot()
    (cls,) = [c for c in root.iter("class")
              if c.get("filename") == os.path.join("src", "dut.pss")]
    lines = {int(l.get("number")): l for l in cls.iter("line")}
    src = project / "src" / "dut.pss"
    assert lines[_line_of(src, "return -1")].get("hits") == "0"
    br = lines[_line_of(src, "if (n < 0)")]
    assert br.get("branch") == "true"
    assert br.get("condition-coverage") == "50% (1/2)"
    assert float(root.get("branch-rate")) < 1


def test_lcov_source_root(report, project, tmp_path_factory, capsys,  # noqa: F811
                          monkeypatch):
    rep = json.loads(report.read_text())
    for key in ("statements", "arms"):
        for p in rep[key].values():
            if p.get("file"):
                p["file"] = os.path.relpath(p["file"], project)
    other = tmp_path_factory.mktemp("elsewhere")
    (other / "cov.json").write_text(json.dumps(rep))
    monkeypatch.chdir(other)
    assert main(["cov", "lcov", "cov.json", "-o", "x.info",
                 "--source-root", str(project)]) == 0
    want = os.path.relpath(project / "src" / "dut.pss", other)
    assert set(_lcov(other / "x.info")) == {want}
    # not found: written as the report spells it
    assert main(["cov", "lcov", "cov.json", "-o", "y.info"]) == 0
    assert set(_lcov(other / "y.info")) == {os.path.join("src", "dut.pss")}
    assert main(["cov", "cobertura", "cov.json", "-o",
                 str(other / "no" / "such.xml")]) == 2
    assert "cannot write" in capsys.readouterr().err


def _tests_of(cdb):
    from covsight.core.ncdb.ncdb_reader import NcdbReader
    from covsight.core.api import HistoryNodeKind
    db = NcdbReader().read(str(cdb))
    return [h.getLogicalName() for h in db.historyNodes(HistoryNodeKind.TEST)]


def _hits(rep):
    return {k: p["hits"] for t in ("statements", "arms")
            for k, p in rep[t].items()}


def test_merge_json(both, project):  # noqa: F811
    js, cdb = both
    one = cov_cmd.load(str(js))
    assert main(["cov", "merge", str(js), str(cdb), "-o", "m.json"]) == 0
    m = json.loads((project / "m.json").read_text())
    assert m["version"] == 1
    assert _hits(m) == {k: 2 * n for k, n in _hits(one).items()}
    assert m["summary"]["arms"] == one["summary"]["arms"]
    # JSON in, .cdb out: the points, no tests
    assert main(["cov", "merge", str(js), str(js), "-o", "m.cdb"]) == 0
    assert _hits(cov_cmd.load(str(project / "m.cdb"))) == _hits(m)
    assert _tests_of(project / "m.cdb") == []


def test_merge_cdb_keeps_tests(both, project):  # noqa: F811
    _, cdb = both
    n = len(_tests_of(cdb))
    assert n
    assert main(["cov", "merge", str(cdb), str(cdb), "-o", "m.cdb"]) == 0
    assert len(_tests_of(project / "m.cdb")) == 2 * n
    one = _hits(cov_cmd.load(str(cdb)))
    assert _hits(cov_cmd.load(str(project / "m.cdb"))) == {
        k: 2 * h for k, h in one.items()}


def test_merge_errors(report, project, capsys):  # noqa: F811
    (project / "bad.json").write_text("{}")
    assert main(["cov", "merge", str(report), "bad.json", "-o", "m.json"]) == 2
    assert "not a pssunit.coverage/1 report" in capsys.readouterr().err
    assert not (project / "m.json").exists()


def _git(cwd, *args):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], cwd=cwd, check=True, capture_output=True)


def _diff(capsys, *args):
    capsys.readouterr()
    assert main(["cov", "diff", *args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_diff_gaps_on_changed_lines(report, project, capsys):  # noqa: F811
    _git(project, "init", "-q")
    _git(project, "add", "src", "tests")
    _git(project, "commit", "-q", "-m", "base")
    doc = _diff(capsys, str(report), "--base", "HEAD")
    assert doc["schema"] == "pssc.cov-diff/1" and doc["base"] == "HEAD"
    assert doc["changed"] == [] and doc["gaps"] == []
    # change the never-run `return -1` (same line count)
    src = project / "src" / "dut.pss"
    line = _line_of(src, "return -1")
    src.write_text(src.read_text().replace("return -1", "return  -1"))
    doc = _diff(capsys, str(report), "--base", "HEAD")
    assert doc["changed"] == ["src/dut.pss"]
    assert doc["gaps"] and {(g["kind"], g["line"]) for g in doc["gaps"]} \
        == {("statement", line)}
    assert doc["gaps"][0]["context"]
    assert doc["summary"]["statement"] == 1
    # a changed line that is covered gives no gap
    src.write_text(src.read_text().replace("return  -1", "return -1")
                   .replace("if (n < 0)", "if (n <  0)"))
    doc = _diff(capsys, str(report), "--base", "HEAD")
    assert doc["changed"] == ["src/dut.pss"]
    assert all(g["kind"] == "arm" for g in doc["gaps"])
    capsys.readouterr()
    assert main(["cov", "diff", str(report), "--base", "HEAD"]) == 0
    assert "not covered" in capsys.readouterr().out


def test_diff_bad_base(report, project, capsys):  # noqa: F811
    _git(project, "init", "-q")
    assert main(["cov", "diff", str(report), "--base", "nope"]) == 2
    assert "pssc: error: git" in capsys.readouterr().err
