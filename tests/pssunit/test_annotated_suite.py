"""`@pssunit_pkg::test` end to end (M4.3 / T4.5, T4.6).

The annotation is the first milestone that changes what a user can *write*, and
what it buys is the ability to keep a test in the suite that should not be run
or should not be believed. Both of those are ways of making a test not report
what it did, so each one here is paired with the thing that turns it back off:
an annotation with no escape hatch is a place for a real failure to hide.

`pss/annotated` is a fixture of its own rather than more tests in `pss/suite`,
so the counts the other end-to-end modules assert on stay stable and each
fixture reads on its own.
"""
from __future__ import annotations

import json
from pathlib import Path

from pssc.cli import main

ANNOTATED = str(Path(__file__).parent / "pss" / "annotated")

# R-5's exit codes; see pssc.testing.cmd.
OK, TESTS_FAILED, NO_TESTS = 0, 1, 5


def _run(*argv, ctrf=None):
    args = ["test", *argv]
    args += ["--ctrf", str(ctrf)] if ctrf else ["--no-ctrf"]
    return main(args)


def _by_name(path):
    return {t["name"]: t
            for t in json.loads(Path(path).read_text())["results"]["tests"]}


# --- discovery: the annotation reaches the runner ---------------------------

def test_collect_only_marks_a_skipped_test(capsys):
    """The listing is what answers "will this actually run?".

    A skipped test that lists exactly like a running one answers it wrongly,
    which is why the mark is on the collect line and not only in the results.
    """
    assert _run("--collect-only", ANNOTATED) == OK
    out = capsys.readouterr().out
    assert "skipped_a  [skip: waiting on the DMA model]" in out


def test_collect_only_marks_an_xfail_test(capsys):
    _run("--collect-only", ANNOTATED)
    assert "xfail_a  [xfail: known wrong until the fix lands]" in capsys.readouterr().out


def test_collect_only_shows_the_group(capsys):
    _run("--collect-only", ANNOTATED)
    assert "io_one_a  [io]" in capsys.readouterr().out


def test_an_unannotated_test_lists_and_runs_plainly(capsys):
    """The baseline. Every other assertion here is a departure from it."""
    assert _run("-k", "plain_a", ANNOTATED) == OK
    out = capsys.readouterr().out
    assert "PASS" in out and "[" not in out.split("plain_a")[1].split("\n")[0]


# --- T4.5: --group ----------------------------------------------------------

def test_group_selects_only_that_group(capsys):
    assert _run("--group", "io", ANNOTATED) == OK
    out = capsys.readouterr().out
    assert "io_one_a" in out and "io_two_a" in out
    assert "mem_one_a" not in out and "plain_a" not in out
    assert "2 passed" in out


def test_group_is_an_exact_match_not_a_prefix(capsys):
    """A group is a name the author chose; a fuzzy match would silently pull in
    a neighbouring group and the user would never see that it had."""
    assert _run("--group", "i", ANNOTATED) == NO_TESTS
    assert "no tests matched" in capsys.readouterr().err


def test_a_group_matching_nothing_is_an_error_not_a_green_run(capsys):
    assert _run("--group", "nosuch", ANNOTATED) == NO_TESTS
    err = capsys.readouterr().err
    assert "no tests matched" in err and "--group nosuch" in err


def test_group_composes_with_k(capsys):
    assert _run("--group", "io", "-k", "io_one*", ANNOTATED) == OK
    out = capsys.readouterr().out
    assert "1 passed" in out and "io_two_a" not in out


# --- T4.5: .skip and --noskip ----------------------------------------------

def test_a_skipped_test_is_reported_as_skipped(capsys):
    assert _run("-k", "skipped_a", ANNOTATED) == OK
    assert "SKIP" in capsys.readouterr().out


def test_a_skip_prints_its_reason(capsys):
    """A skip is not a failure, so it never reaches the failure block -- and a
    skip whose reason is invisible is a test that quietly stopped running."""
    _run("-k", "skipped_a", ANNOTATED)
    assert "waiting on the DMA model" in capsys.readouterr().out


def test_a_skipped_test_does_not_run(capsys):
    """`skipped_a`'s body fails a check if it executes.

    Asserted this way rather than by looking for absence of output: a skip that
    ran the test and then discarded the result would print an identical green
    line, and only the failing check distinguishes the two.
    """
    _run("-k", "skipped_a", ANNOTATED)
    out = capsys.readouterr().out
    assert "should not have run" not in out
    assert "0 passed, 1 skipped" in out


def test_noskip_runs_it_anyway_and_it_fails(capsys):
    """The escape hatch, and the evidence that the skip was load-bearing."""
    assert _run("--noskip", "-k", "skipped_a", ANNOTATED) == TESTS_FAILED
    out = capsys.readouterr().out
    assert "should not have run" in out
    assert "1 failed" in out


def test_a_skip_does_not_fail_the_suite(capsys):
    """Exit 0 with a skip present: a withheld test is not a failing one."""
    assert _run("-k", "skipped_a", ANNOTATED) == OK


# --- T4.6: .xfail, xpass, and --runxfail ------------------------------------

def test_an_xfail_that_fails_is_green(capsys):
    """The point of the mark: a known-broken case stays in the suite instead of
    being deleted, and does not turn CI red while it is known."""
    assert _run("-k", "xfail_a", ANNOTATED) == OK
    out = capsys.readouterr().out
    assert "XFAIL" in out and "1 xfailed" in out


def test_an_xfail_that_fails_does_not_print_its_failure(capsys):
    """It is expected, so it is not news. The output belongs to `--runxfail`."""
    _run("-k", "xfail_a", ANNOTATED)
    assert "not ok xfail_a" not in capsys.readouterr().out


def test_an_xfail_that_passes_is_red(capsys):
    """An unexpected pass means a stale annotation.

    Accepting it silently is how a suite accumulates marks nobody removes, and
    the test they were hiding stops being run for real long after it was fixed.
    """
    assert _run("-k", "xpass_a", ANNOTATED) == TESTS_FAILED
    out = capsys.readouterr().out
    assert "XPASS" in out and "1 xpassed" in out


def test_an_xpass_says_why_it_is_a_failure(capsys):
    """"XPASS" alone reads like a status code. The line has to say what to do."""
    _run("-k", "xpass_a", ANNOTATED)
    out = capsys.readouterr().out
    assert "expected to fail, but passed" in out
    assert "stale" in out                       # the annotation's own reason


def test_runxfail_reports_what_actually_happened(capsys):
    """Answers "what is it doing?" without editing the source to find out."""
    assert _run("--runxfail", "-k", "x*_a", ANNOTATED) == TESTS_FAILED
    out = capsys.readouterr().out
    assert "1 passed, 1 failed" in out
    assert "xfailed" not in out and "xpassed" not in out


def test_runxfail_shows_the_failure_the_mark_was_hiding(capsys):
    _run("--runxfail", "-k", "xfail_a", ANNOTATED)
    assert "not ok xfail_a :: known wrong" in capsys.readouterr().out


# --- the two reports agree ---------------------------------------------------

def test_ctrf_records_the_group_as_a_tag(tmp_path):
    path = tmp_path / "ctrf.json"
    _run("--group", "io", ANNOTATED, ctrf=path)
    assert _by_name(path)["io_one_a"]["tags"] == ["io"]


def test_ctrf_maps_xfail_to_passed_and_xpass_to_failed(tmp_path):
    """CTRF has five statuses and pssunit distinguishes more, so the collapse is
    asserted rather than left to whoever reads the writer next."""
    path = tmp_path / "ctrf.json"
    _run("-k", "x*_a", ANNOTATED, ctrf=path)
    tests = _by_name(path)

    assert tests["xfail_a"]["status"] == "passed"
    assert tests["xfail_a"]["rawStatus"] == "xfail"
    assert tests["xpass_a"]["status"] == "failed"
    assert tests["xpass_a"]["rawStatus"] == "xpass"


def test_ctrf_records_a_skip_as_skipped(tmp_path):
    path = tmp_path / "ctrf.json"
    _run("-k", "skipped_a", ANNOTATED, ctrf=path)
    t = _by_name(path)["skipped_a"]
    assert t["status"] == "skipped" and t["rawStatus"] == "skip"
    assert t["message"] == "waiting on the DMA model"


def test_runxfail_makes_ctrf_agree_with_the_exit_code(tmp_path):
    """The reason the runner settles xfail rather than the report deriving it.

    Under `--runxfail` the annotation and the verdict disagree by design. A
    report that re-derived the status from `item.meta` would say `xfail` while
    the process exited on the raw result -- two answers to one question.
    """
    path = tmp_path / "ctrf.json"
    assert _run("--runxfail", "-k", "x*_a", ANNOTATED, ctrf=path) == TESTS_FAILED
    tests = _by_name(path)

    assert tests["xfail_a"]["status"] == "failed"
    assert "rawStatus" not in tests["xfail_a"] or \
        tests["xfail_a"]["rawStatus"] != "xfail"
    assert tests["xpass_a"]["status"] == "passed"


def test_the_ctrf_summary_counts_add_up_to_the_tests(tmp_path):
    """With five statuses collapsed into three buckets, this is the guard that
    catches a status the collapse forgot."""
    path = tmp_path / "ctrf.json"
    _run(ANNOTATED, ctrf=path)
    summary = json.loads(path.read_text())["results"]["summary"]
    assert (summary["passed"] + summary["failed"] + summary["skipped"]
            + summary["pending"] + summary["other"]) == summary["tests"] == 7


# --- the whole fixture ------------------------------------------------------

def test_the_whole_annotated_suite(capsys):
    """One assertion over every status the annotation can produce."""
    assert _run(ANNOTATED) == TESTS_FAILED
    out = capsys.readouterr().out
    assert "4 passed, 1 skipped, 1 xfailed, 1 xpassed" in out
