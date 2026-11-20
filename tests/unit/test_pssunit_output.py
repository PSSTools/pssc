"""P1.16c: output -- ``--json`` NDJSON events, ``--report``, ``--junit`` (T1.16)."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pssc.cli import main

REPO = Path(__file__).resolve().parents[2]

# The P1.16b fixture: t_rw passes, t_ro passes only on the fake, t_bad fails.
_RUNS = (Path(__file__).parent / "test_pssunit_runs.py").read_text()
SUITE = _RUNS.split('SUITE = """\\\n', 1)[1].split('"""', 1)[0]
SCHEMA = REPO / "docs" / "schemas" / "pssunit.events-1.json"
ARGV = ["test", "t.pss", "--targets", "none", "--no-ctrf", "--seed", "3"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "t.pss").write_text(SUITE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _events(out):
    return [json.loads(line) for line in out.splitlines()]


@pytest.mark.parametrize("jobs", ["1", "2"])
def test_json_streams_valid_events_only(project, capsys, jobs):
    assert main(ARGV + ["--json", "--platform", "fake", "-j", jobs]) == 1
    evs = _events(capsys.readouterr().out)  # every stdout line is JSON
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    for ev in evs:
        jsonschema.validate(ev, schema)
    assert [e["event"] for e in evs] == (
        ["run.start"] + ["test.start", "test.end"] * 3 + ["run.end"])
    assert evs[0]["seed"] == 3 and evs[0]["tests"] == 3
    ends = {e["id"].split("::")[-1]: e for e in evs if e["event"] == "test.end"}
    assert ends["t_rw"]["status"] == "pass" and "repro" not in ends["t_rw"]
    bad = ends["t_bad"]
    assert bad["status"] == "fail" and "check(s) failed" in bad["message"]
    assert "bad" in bad["stdout"]
    assert bad["repro"] == (f"pssc test t.pss {bad['id']} --platform fake"
                            " --targets none --seed 3")
    assert evs[-1]["exit"] == 1 and evs[-1]["summary"]["failed"] == 1


def test_json_with_collect_only_is_still_the_catalog(project, capsys):
    assert main(ARGV + ["--collect-only", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "pssunit.catalog/1"


def test_report_is_ctrf(project, capsys):
    assert main(ARGV + ["--report", "r.json"]) == 1
    doc = json.loads((project / "r.json").read_text())
    assert doc["reportFormat"] == "CTRF"
    s = doc["results"]["summary"]
    assert (s["tests"], s["passed"], s["failed"]) == (3, 1, 2)
    assert len(doc["results"]["tests"]) == 3
    assert not (project / "pssunit-ctrf.json").exists()


def test_junit_xml(project, capsys):
    assert main(ARGV + ["--junit", "j.xml", "--platform", "fake"]) == 1
    root = ET.parse(project / "j.xml").getroot()
    assert root.tag == "testsuites"
    assert (root.get("tests"), root.get("failures"), root.get("errors")) == ("3", "1", "0")
    (suite,) = root.findall("testsuite")
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    assert set(cases) == {"t_rw", "t_ro", "t_bad"}
    assert cases["t_rw"].find("failure") is None
    fail = cases["t_bad"].find("failure")
    assert "check(s) failed" in fail.get("message")
    assert "bad" in cases["t_bad"].find("system-out").text
    assert (f"pssc test t.pss {cases['t_bad'].get('classname')}::t_bad"
            " --platform fake --targets none --seed 3") in fail.text
    assert cases["t_bad"].get("file", "").endswith("t.pss")


# --- P1.16d: every failure's repro command reproduces it --------------------

# a second suite with a passing test of the same name as the failing `t_bad`
SAME_NAME = """\
import pssunit_pkg::*;

component u_f : fixture_c {
    rt_c rt;
    @pssunit_pkg::test
    function void t_bad() {
        rt.check_eq_int(1, 1, "fine here");
    }
}
"""


def _replays(out):
    return [line.split("replay: ", 1)[1] for line in out.splitlines()
            if line.strip().startswith("replay: ")]


@pytest.mark.parametrize("extra", [[], ["--platform", "fake"]])
def test_the_replay_line_reproduces_each_failure(project, capsys, extra):
    """No `--seed`: the replay line must carry the one that ran. The options
    that change verdicts come along (t_ro fails on plain memory, passes on the
    fake), and the test is named by ID, not a `-k` that a same-named test in
    another suite would also match."""
    import shlex
    (project / "u.pss").write_text(SAME_NAME)
    argv = ["test", "t.pss", "u.pss", "--targets", "none", "--no-ctrf"]
    assert main(argv + extra) == 1
    out = capsys.readouterr().out
    failed = sorted(line.split()[1] for line in out.splitlines()
                    if line.startswith("FAIL "))
    assert failed == (["r_f::t_bad"] if extra else ["r_f::t_bad", "r_f::t_ro"])
    lines = _replays(out)
    assert len(lines) == len(failed)
    for line in lines:
        words = shlex.split(line)
        assert words[:2] == ["pssc", "test"] and "--seed" in words
        assert main(words[1:] + ["--no-ctrf"]) == 1
        again = capsys.readouterr().out
        assert [l for l in again.splitlines() if l.startswith("FAIL ")] == (
            [l for l in out.splitlines()
             if l.startswith("FAIL ") and l.split()[1] in line])
        assert "0 passed, 1 failed " in again
        assert _replays(again) == [line]          # the replay is a fixed point
