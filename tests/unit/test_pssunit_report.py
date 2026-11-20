"""The two renderings of a run: the human summary and the CTRF report.

The CTRF tests matter more than they look. CTRF is the interface a second
pssunit runner would match, and the parts most likely to drift between two
implementations are exactly the ones with a judgement call in them -- the status
collapse, and which pssunit concept lands in which CTRF field.
"""
from __future__ import annotations

import io
import json

import pytest

from pssc.testing.discover import TestItem, TestMeta
from pssc.testing.report import (
    build_ctrf, replay_command, report_human, summary_counts, write_ctrf)
from pssc.testing.runner import Kind, Result, Status


def _item(name="smoke_a", comp="wb_dma_tests_c", loc=None, meta=None):
    return TestItem(qname=f"{comp}::{name}", name=name, comp=comp, loc=loc,
                    meta=meta or TestMeta())


def _result(status=Status.PASS, **kw):
    kw.setdefault("item", _item())
    kw.setdefault("seed", 42)
    kw.setdefault("elapsed", 0.03)
    return Result(status=status, **kw)


class _Loc:
    file = "tests/pss/smoke_a.pss"
    line = 5


# --- human ------------------------------------------------------------------

def test_human_report_groups_by_root_component():
    out = io.StringIO()
    report_human([_result(item=_item("a_a", comp="x_c")),
                  _result(item=_item("b_a", comp="y_c")),
                  _result(item=_item("c_a", comp="x_c"))], out=out)
    text = out.getvalue()
    assert text.index("x_c\n") < text.index("y_c\n")
    assert "a_a" in text and "c_a" in text


def test_human_summary_counts_every_status():
    out = io.StringIO()
    report_human([_result(Status.PASS), _result(Status.FAIL),
                  _result(Status.ERROR)], out=out)
    last = out.getvalue().strip().splitlines()[-1]
    assert "1 passed" in last and "1 failed" in last and "1 error" in last


def test_the_seed_is_always_reported():
    out = io.StringIO()
    report_human([_result()], out=out)
    assert "seed 42" in out.getvalue()


def test_quiet_prints_only_the_summary():
    out = io.StringIO()
    report_human([_result()], quiet=True, out=out)
    assert len(out.getvalue().strip().splitlines()) == 1


def test_a_failure_prints_a_replay_line_and_the_captured_output():
    out = io.StringIO()
    report_human([_result(Status.FAIL, detail="unsat", log="Hello\n")],
                 specs=["examples/wb_dma"], out=out)
    text = out.getvalue()
    assert "unsat" in text
    assert "Hello" in text
    assert "replay: pssc test examples/wb_dma {} --seed 42".format(_result().item.qname) in text


def test_a_passing_test_does_not_print_its_output():
    """Model output belongs to the failure report, not the summary."""
    out = io.StringIO()
    report_human([_result(log="chatty\n")], out=out)
    assert "chatty" not in out.getvalue()


def test_the_replay_command_carries_the_seed_that_ran():
    r = _result(Status.FAIL, seed=8891)
    assert "--seed 8891" in replay_command(r, ["examples/wb_dma"])


# --- CTRF -------------------------------------------------------------------

def test_ctrf_has_the_required_envelope():
    doc = build_ctrf([_result()])
    assert doc["reportFormat"] == "CTRF"
    assert doc["specVersion"]
    assert doc["results"]["tool"]["name"] == "pssc"


def test_ctrf_summary_has_all_eight_required_fields():
    summary = build_ctrf([_result()])["results"]["summary"]
    assert set(summary) == {"tests", "passed", "failed", "skipped", "pending",
                            "other", "start", "stop"}


def test_ctrf_counts_agree_with_the_human_summary():
    results = [_result(Status.PASS), _result(Status.FAIL), _result(Status.ERROR)]
    summary = build_ctrf(results)["results"]["summary"]
    human = summary_counts(results)

    assert summary["tests"] == len(results)
    assert summary["passed"] == human["passed"]
    # CTRF has no `error` bucket, so an errored test counts as failed -- see
    # `_ctrf_status`. The two must still add up to `tests`.
    assert summary["failed"] == human["failed"] + human["error"]
    assert (summary["passed"] + summary["failed"] + summary["skipped"]
            + summary["pending"] + summary["other"]) == summary["tests"]


def test_ctrf_carries_the_pssunit_specific_fields():
    doc = build_ctrf([_result(item=_item(loc=_Loc()), log="Hello\n")])
    t = doc["results"]["tests"][0]
    assert t["name"] == "smoke_a"                       # short name, as users say it
    assert t["suite"] == ["wb_dma_tests_c"]             # the root component
    assert t["extra"]["qname"] == "wb_dma_tests_c::smoke_a"
    assert t["extra"]["seed"] == 42                     # replayable from the report
    assert t["filePath"] == "tests/pss/smoke_a.pss"
    assert t["line"] == 5
    assert t["stdout"] == "Hello\n"                     # per-test, not interleaved


def test_ctrf_omits_location_when_the_front_end_recorded_none():
    t = build_ctrf([_result()])["results"]["tests"][0]
    assert "filePath" not in t and "line" not in t


def test_a_group_becomes_a_ctrf_tag():
    r = _result(item=_item(meta=TestMeta(group="dma")))
    assert build_ctrf([r])["results"]["tests"][0]["tags"] == ["dma"]


# CTRF allows five statuses and pssunit distinguishes six, so the collapse is
# asserted here rather than left to whoever reads the writer next.
#
# The input is the status the *runner already settled on*, not the raw outcome
# plus `meta`. That moved in M4.3: with `--runxfail` the annotation and the
# verdict disagree by design, and a report that re-derived the status from
# `item.meta` would say `xfail` while the process exited on the raw result --
# two answers to one question. Turning an annotation plus an outcome into a
# verdict is now `runner._settle_xfail`, and `test_pssunit_meta.py` covers it.
@pytest.mark.parametrize("status,expect_status,expect_raw", [
    (Status.PASS,  "passed",  None),
    (Status.FAIL,  "failed",  None),
    # A crash is `failed` with rawStatus "error": folding it into `other` would
    # hide it from every summary that counts failures.
    (Status.ERROR, "failed",  "error"),
    (Status.SKIP,  "skipped", "skip"),
    # An expected failure that failed is a pass...
    (Status.XFAIL, "passed",  "xfail"),
    # ...and one that passed is a failure: an unexpected pass means a stale
    # annotation, and accepting it silently defeats the point of the mark.
    (Status.XPASS, "failed",  "xpass"),
])
def test_ctrf_status_mapping(status, expect_status, expect_raw):
    t = build_ctrf([_result(status)])["results"]["tests"][0]
    assert t["status"] == expect_status
    assert t.get("rawStatus") == expect_raw


def test_every_status_has_a_ctrf_mapping():
    """The guard on adding a seventh status and forgetting the table.

    A missing entry would raise at report time -- after the suite has run --
    which is the worst moment to discover it.
    """
    for status in Status:
        assert build_ctrf([_result(status)])["results"]["tests"][0]["status"]


def test_the_summary_buckets_account_for_every_test():
    """Six statuses into three buckets: this catches one that fell through."""
    results = [_result(s) for s in Status]
    summary = build_ctrf(results)["results"]["summary"]
    assert (summary["passed"] + summary["failed"] + summary["skipped"]
            + summary["pending"] + summary["other"]) == len(results)


def test_write_ctrf_produces_readable_json(tmp_path):
    path = write_ctrf([_result()], tmp_path / "ctrf.json")
    doc = json.loads(path.read_text())
    assert doc["results"]["summary"]["tests"] == 1
