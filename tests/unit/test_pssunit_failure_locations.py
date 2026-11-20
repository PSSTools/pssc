"""P2.12a: ``test.end`` carries ``failures``, where a test failed (T2.12).

An editor marks each failed check at its call: ``--json``'s ``test.end``
lists ``{file, line, col, kind, message}`` for each failed pssunit_pkg check
and each sanitizer finding with provenance.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pssc.cli import main
from pssc.testing import runner
from pssc.testing.sanitizers import Finding

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "docs" / "schemas" / "pssunit.events-1.json"

SUITE = """\
import pssunit_pkg::*;

component t_f : fixture_c {
    @pssunit_pkg::test
    function void t_ok() {
        check_eq(1, 1, "same");
    }
    @pssunit_pkg::test
    function void t_bad() {
        check_eq(1, 1, "same");
        check_eq(1 + 1, 3, "sum");
    }
}
"""


def test_json_test_end_lists_each_failed_check(tmp_path, monkeypatch, capsys):
    (tmp_path / "t.pss").write_text(SUITE)
    monkeypatch.chdir(tmp_path)
    assert main(["test", "t.pss", "--targets", "none", "--no-ctrf",
                 "--seed", "1", "--json"]) == 1
    evs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    for ev in evs:
        jsonschema.validate(ev, schema)
    ends = {e["id"].split("::")[-1]: e for e in evs if e["event"] == "test.end"}
    assert "failures" not in ends["t_ok"]
    [f] = ends["t_bad"]["failures"]
    assert f["file"].endswith("t.pss")
    assert (f["line"], f["col"]) == (11, 9)
    assert (f["kind"], f["message"]) == ("check_eq", "sum")


def test_findings_with_provenance_are_located():
    session = SimpleNamespace(run_state=None, findings=[
        Finding("rc-read", "discarded", where="dev.pss:7:5"),
        Finding("unbound", "no handle", where="dev.pss:9"),
        Finding("inflight", "left over"),          # no provenance: not listed
    ])
    assert runner._failure_locations(session) == [
        {"file": "dev.pss", "line": 7, "col": 5, "kind": "sanitizer rc-read",
         "message": "discarded"},
        {"file": "dev.pss", "line": 9, "col": 0, "kind": "sanitizer unbound",
         "message": "no handle"},
    ]
