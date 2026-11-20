"""`pssc test` against real PSS: parse, translate, discover, run, report.

The unit tier checks each stage against synthetic input; this one checks that
the stages agree with each other and with the parser. Everything here goes
through `cli.main`, because the exit code is half of what `pssc test` promises
and it only exists at that level.

Fixtures live in `pss/` here rather than pointing at the pssunit repo's
`examples/wb_dma`: pssc is *a* runner of pssunit tests and, long-term, not the
only one, so its own suite should not need the framework repo checked out
alongside it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pssc.cli import main

SUITE = str(Path(__file__).parent / "pss" / "suite")
#: A second fixture whose tests record checks (M5.2/M5.3). Separate so the
#: counts above stay stable and each fixture reads on its own.
CHECKS = str(Path(__file__).parent / "pss" / "checks")

# The exit codes `pssc test` promises (plan R-5). `1` means the suite executed,
# so a report exists next to it; `2` means the model never got that far.
OK, TESTS_FAILED, COMPILE_ERROR, INTERNAL, NO_TESTS = 0, 1, 2, 3, 5


def _run(*argv, ctrf=None):
    args = ["test", *argv]
    args += ["--ctrf", str(ctrf)] if ctrf else ["--no-ctrf"]
    return main(args)


# --- discovery against real IR ----------------------------------------------

def test_collect_only_finds_exactly_the_tests(capsys):
    assert _run("--collect-only", SUITE) == OK
    out = capsys.readouterr().out

    assert "suite_c" in out                       # grouped by root component
    for name in ("smoke_a", "constrained_a", "unsat_a"):
        assert name in out
    assert "base_a" not in out                    # abstract: not runnable
    assert "helper_a" not in out                  # does not derive from test_a
    assert "3 tests collected" in out


def test_collect_only_runs_nothing(capsys):
    _run("--collect-only", SUITE)
    assert "doit:" not in capsys.readouterr().out


def test_k_filters_by_name(capsys):
    assert _run("--collect-only", "-k", "smoke*", SUITE) == OK
    out = capsys.readouterr().out
    assert "smoke_a" in out and "unsat_a" not in out


def test_fixture_filters_by_root_component(capsys):
    assert _run("--collect-only", "--fixture", "suite_c", SUITE) == OK
    assert "3 tests collected" in capsys.readouterr().out


def test_a_filter_matching_nothing_is_an_error_not_a_green_run(capsys):
    """The failure mode this runner most needs to avoid reporting as success."""
    assert _run("--collect-only", "-k", "nosuchtest", SUITE) == NO_TESTS
    assert "no tests matched" in capsys.readouterr().err


# --- execution --------------------------------------------------------------

def test_a_suite_with_a_failure_exits_1(capsys):
    assert _run(SUITE) == TESTS_FAILED
    out = capsys.readouterr().out
    assert "2 passed, 1 failed" in out


def test_a_wholly_passing_selection_exits_0(capsys):
    assert _run("-k", "smoke*", SUITE) == OK
    assert "1 passed" in capsys.readouterr().out


def test_an_unsatisfiable_constraint_fails_rather_than_erroring(capsys):
    assert _run("-k", "unsat_a", SUITE) == TESTS_FAILED
    out = capsys.readouterr().out
    assert "FAIL" in out and "ERROR" not in out


def test_constraints_are_actually_solved(tmp_path):
    """A guard on the solver, not on the runner.

    `constrained_a` pins `len` to 7 and passes it to `doit`, so this asserts a
    solved value made it all the way through: dv-solve produced it, the SOLVE
    write-back stored it under the field's *name*, `LD_FIELD` read it back by
    slot, and it crossed a function call as an argument. A constraint that were
    dropped, or a write-back that missed, would read some other number rather
    than raise -- which is why the number is asserted and not merely its
    presence.

    Read from the report rather than stdout, because the model's output is
    captured per test (see `test_model_output_is_captured_per_test`) and so
    never reaches the terminal on a passing run.
    """
    path = tmp_path / "ctrf.json"
    assert _run("-k", "constrained_a", SUITE, ctrf=path) == OK
    t = json.loads(path.read_text())["results"]["tests"][0]
    assert "doit: 7" in t["stdout"]


def test_the_failure_report_names_the_test_and_how_to_replay_it(capsys):
    _run("-k", "unsat_a", SUITE)
    out = capsys.readouterr().out
    assert "suite_c::unsat_a" in out
    assert "replay: pssc test" in out and "--seed" in out


def test_model_output_is_captured_per_test(capsys):
    """`doit:` output belongs to the test that produced it.

    Not printed by a passing test at all -- it is attached to the result, which
    is what makes it available to the failure report and to CTRF `stdout`.
    """
    _run("-k", "smoke_a", SUITE)
    assert "doit:" not in capsys.readouterr().out


def test_the_same_seed_reproduces_the_same_run(capsys):
    _run("--seed", "1234", SUITE)
    first = capsys.readouterr().out
    _run("--seed", "1234", SUITE)
    assert capsys.readouterr().out == first


def test_exitfirst_stops_after_the_first_failure(capsys):
    _run("-x", SUITE)
    out = capsys.readouterr().out
    assert "1 failed" in out
    # unsat_a is declared last, so -x should not lose a passing test before it
    assert "2 passed" in out


# --- reporting --------------------------------------------------------------

def test_a_run_that_executes_tests_writes_a_ctrf_report(tmp_path):
    """Exit 2 is only a useful signal because a report is guaranteed with it."""
    path = tmp_path / "ctrf.json"
    assert _run(SUITE, ctrf=path) == TESTS_FAILED
    doc = json.loads(path.read_text())

    summary = doc["results"]["summary"]
    assert summary["tests"] == 3
    assert summary["passed"] == 2 and summary["failed"] == 1


def test_ctrf_locates_each_test_in_the_source(tmp_path):
    path = tmp_path / "ctrf.json"
    _run("-k", "smoke_a", SUITE, ctrf=path)
    t = json.loads(path.read_text())["results"]["tests"][0]
    assert t["filePath"].endswith("tests.pss")
    assert t["line"] > 0


def test_ctrf_carries_the_model_output_of_each_test(tmp_path):
    path = tmp_path / "ctrf.json"
    _run("-k", "constrained_a", SUITE, ctrf=path)
    t = json.loads(path.read_text())["results"]["tests"][0]
    assert "doit: 7" in t["stdout"]


def test_no_ctrf_suppresses_the_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _run(SUITE)
    assert not list(tmp_path.glob("*.json"))


# --- tool errors ------------------------------------------------------------

def test_a_model_that_does_not_translate_exits_2_and_writes_no_report(
        tmp_path, capsys):
    bad = tmp_path / "bad_c.pss"
    bad.write_text("component bad_c { this is not pss }\n")
    path = tmp_path / "ctrf.json"

    assert _run(str(bad), ctrf=path) == COMPILE_ERROR
    assert not path.exists()
    assert "pssc: error" in capsys.readouterr().err


def test_a_source_spec_naming_nothing_exits_2(tmp_path, capsys):
    assert _run(str(tmp_path / "nosuch"), ctrf=tmp_path / "c.json") == COMPILE_ERROR
    assert "matches nothing" in capsys.readouterr().err


def test_a_test_whose_base_type_does_not_resolve_is_loud(tmp_path, capsys):
    """Never a quiet "not a test".

    Skipping it would delete the test from the run and still print green, which
    is the one failure mode a test framework must not have.
    """
    src = tmp_path / "typo_c.pss"
    src.write_text(
        "component typo_c {\n"
        "    action smoke_a : pssunit_pkg::tset_a { exec body { } }\n"
        "}\n")
    assert _run(str(src), ctrf=tmp_path / "c.json") == COMPILE_ERROR
    assert "tset_a" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Checks and the ledger (M5.2 / M5.3)
# ---------------------------------------------------------------------------

def test_a_test_that_raises_nothing_still_fails_on_a_failed_check(capsys):
    """The case "did not raise" cannot express.

    `checked_fail_a` runs to completion; the design simply produced a wrong
    value. Before the ledger this was indistinguishable from a pass, which made
    every check-bearing test one that could not fail.
    """
    assert _run("-k", "checked_fail_a", CHECKS) == TESTS_FAILED
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "1 of 2 check(s) failed" in out


def test_a_failing_check_names_itself(capsys):
    """A count alone sends you looking; the name says where."""
    _run("-k", "checked_fail_a", CHECKS)
    assert "this one does not" in capsys.readouterr().out


def test_a_passing_check_does_not_report_itself(capsys):
    """Only failures are worth the line. A green run stays quiet."""
    assert _run("-k", "checked_pass_a", CHECKS) == OK
    assert "this one holds" not in capsys.readouterr().out


def test_check_counts_reach_the_human_report(capsys):
    _run("-k", "checked_pass_a", CHECKS)
    assert "3/3 checks" in capsys.readouterr().out


def test_check_counts_reach_the_ctrf_report(tmp_path):
    path = tmp_path / "ctrf.json"
    _run("-k", "checked_fail_a", CHECKS, ctrf=path)
    t = json.loads(path.read_text())["results"]["tests"][0]
    assert t["extra"]["checks"] == {"passed": 1, "failed": 1, "skipped": 0}


def test_the_ledger_does_not_leak_between_tests(tmp_path):
    """Each test gets a fresh VM, so a fresh ledger.

    If it leaked, the passing test's counts would carry into the failing one --
    and worse, a test could fail because of a previous test's check.
    """
    path = tmp_path / "ctrf.json"
    _run("-k", "checked_*", CHECKS, ctrf=path)
    by_name = {t["name"]: t["extra"]["checks"]
               for t in json.loads(path.read_text())["results"]["tests"]}
    assert by_name["checked_pass_a"] == {"passed": 3, "failed": 0, "skipped": 0}
    assert by_name["checked_fail_a"] == {"passed": 1, "failed": 1, "skipped": 0}


def test_strict_fails_a_test_that_asserts_nothing(capsys):
    """A suite of check-free tests looks green while proving nothing.

    Off by default -- "does this elaborate and solve" is a legitimate test --
    but available for a suite that has decided every test must assert.
    """
    assert _run("--strict", "-k", "unchecked_a", CHECKS) == TESTS_FAILED
    assert "no checks recorded" in capsys.readouterr().out


def test_strict_leaves_a_checked_test_alone(capsys):
    assert _run("--strict", "-k", "checked_pass_a", CHECKS) == OK


def test_without_strict_a_check_free_test_still_passes(capsys):
    assert _run("-k", "unchecked_a", CHECKS) == OK
