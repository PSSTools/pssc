"""P1.16a: selection -- test-ID globs, ``-m TAG``, ``--lf`` (T1.16)."""
from __future__ import annotations

import json

import pytest

from pssc.cli import main
from pssc.testing.cmd import LAST_FAILED, read_last_failed, split_id_specs

TESTS = """\
import pssunit_pkg::*;
component a_f {
    rt_c rt;
    @pssunit_pkg::test {.tags = "slow"}
    function void t_ok() {
        rt.check_eq_int(1, 1, "one");
    }
    @pssunit_pkg::test
    function void t_bad() {
        rt.check_eq_int(1, 2, "two");
    }
}
component b_f {
    rt_c rt;
    @pssunit_pkg::test
    @pssunit_pkg::tag {.name = "fast"}
    function void t_ok() {
        rt.check_eq_int(3, 3, "three");
    }
}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    # The real, cwd-relative record (conftest moves it out of the way).
    from pssc.testing import cmd
    monkeypatch.setattr(cmd, "LAST_FAILED", LAST_FAILED)
    (tmp_path / "t.pss").write_text(TESTS)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _collected(capsys, *argv) -> str:
    assert main(["test", *argv, "--collect-only"]) == 0
    return capsys.readouterr().out


def test_id_specs_split_off_the_sources(tmp_path):
    assert split_id_specs(["x.pss", "a_f::t_*", "d/"]) \
        == (["x.pss", "d/"], ["a_f::t_*"])


def test_id_glob_selects_by_whole_id(project, capsys):
    out = _collected(capsys, "t.pss", "a_f::*")
    assert "2 tests collected" in out and "b_f" not in out
    # Only IDs given: the sources default to the current directory.
    out = _collected(capsys, "*::t_ok")
    assert "2 tests collected" in out and "t_bad" not in out
    # An ID matches exactly; no substring fallback.
    assert main(["test", "a_f::t", "--no-ctrf"]) == 5
    assert "no tests matched a_f::t" in capsys.readouterr().err


def test_id_after_an_option_still_selects(project, capsys):
    # An editor builds `pssc test <src> <options> <id>`; argparse alone
    # fills SRC-SPEC once and rejects the trailing ID.
    out = _collected(capsys, "t.pss", "-k", "t_", "a_f::*")
    assert "2 tests collected" in out and "b_f" not in out
    with pytest.raises(SystemExit):
        main(["test", "t.pss", "--collect-only", "--no-such-option"])


def test_m_selects_by_tag_union(project, capsys):
    out = _collected(capsys, "-m", "slow")
    assert "1 tests collected" in out or "1 test collected" in out
    assert "a_f" in out and "b_f" not in out
    out = _collected(capsys, "-m", "slow", "-m", "fast")
    assert "2 tests collected" in out
    assert main(["test", "-m", "nope", "--no-ctrf"]) == 5
    assert "no tests matched -m nope" in capsys.readouterr().err


def test_lf_runs_last_failures_then_all(project, capsys):
    # No record: --lf runs everything and says so.
    assert main(["test", "--lf", "--no-ctrf"]) == 1
    out = capsys.readouterr().out
    assert "no recorded failures" in out and "1 failed" in out
    assert read_last_failed() == ["a_f::t_bad"]
    assert json.loads((project / LAST_FAILED).read_text()) \
        == {"failed": ["a_f::t_bad"]}

    out = _collected(capsys, "--lf")
    assert "1 test" in out and "t_bad" in out and "t_ok" not in out

    # A run that does not include the failure keeps it on record.
    assert main(["test", "b_f::*", "--no-ctrf"]) == 0
    capsys.readouterr()
    assert read_last_failed() == ["a_f::t_bad"]


def test_green_suite_leaves_no_record(project, capsys):
    assert main(["test", "-m", "slow", "--no-ctrf"]) == 0
    assert not (project / ".pssunit").exists()


def test_file_line_and_id_run_past_the_gate(project, capsys):
    # The gate compiles the sources, not the selections.
    assert main(["test", "t.pss:6", "a_f::*", "--no-ctrf"]) == 0
    assert "1 passed" in capsys.readouterr().out
