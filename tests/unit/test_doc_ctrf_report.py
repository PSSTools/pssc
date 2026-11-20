"""``docs/ctrf-report.rst`` says things that can stop being true (D1.5).

This page has a reader the other doc pages do not: whoever writes the *second*
runner. It is the result format, and the plan's §1.3.2 argument is that defining
the format is what makes conformance cheap -- so the page is closer to a
specification than to a description, and a wrong field name in it is a wrong
field name in someone else's implementation.

So the field table and the status table are both checked **against a document
this test actually generates**, in both directions. A field the page omits is
one the second implementation will not emit; a field it invents is one they will
emit and nobody will read.

The one thing deliberately pinned as *absent* is the replay line: the page tells
the reader it is reconstructible rather than stored, and the design doc it
contradicts still says otherwise, so "still not stored" is a claim worth
failing on.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parents[2] / "docs"
_PAGE = _DOCS / "ctrf-report.rst"


@pytest.fixture(scope="module")
def text() -> str:
    assert _PAGE.is_file(), f"{_PAGE} is missing"
    return _PAGE.read_text()


def _results():
    """One `Result` per status, with every optional field populated somewhere.

    Built by hand rather than by running a suite: the point is to exercise every
    branch of the writer, and a real run that happens to produce all six
    statuses is a fixture that can quietly stop producing one of them.
    """
    from pssc.testing.discover import TestItem, TestMeta
    from pssc.testing.runner import Kind, Result, Status

    class _Loc:
        file = "demo.pss"
        line = 4

    def item(name, group=""):
        return TestItem(qname=f"alu_tests_c::{name}", name=name,
                        comp="alu_tests_c", loc=_Loc(),
                        meta=TestMeta(group=group))

    return [
        Result(item=item("add_a"), status=Status.PASS, checks_passed=1),
        Result(item=item("overflow_a", group="arith"), status=Status.FAIL,
               kind=Kind.CHECK, detail="1 of 1 check(s) failed",
               log="not ok overflow_a :: 8-bit wrap\n", checks_failed=1),
        Result(item=item("fmt_a"), status=Status.ERROR, kind=Kind.UNSUPPORTED,
               detail="unsupported statement", trace="Traceback...\n"),
        Result(item=item("mul_wide_a"), status=Status.SKIP,
               detail="needs the wide multiplier"),
        Result(item=item("div_by_zero_a"), status=Status.XFAIL,
               detail="1 of 1 check(s) failed", checks_failed=1),
        Result(item=item("fixed_a"), status=Status.XPASS,
               detail="expected to fail, but passed", checks_passed=1),
    ]


@pytest.fixture(scope="module")
def doc():
    from pssc.testing.report import build_ctrf
    return build_ctrf(_results(), start_ms=0, stop_ms=90)


def test_the_page_is_in_the_toctree():
    index = (_DOCS / "index.rst").read_text()
    toctree = index.split(".. toctree::", 1)[1].split("\n\n", 2)[1]
    assert "ctrf-report" in toctree.split()


# --- the per-test field table, both directions -------------------------------

def _documented_fields(text: str) -> set:
    """The left column of the per-test field table."""
    table = text.split("Per-test fields", 1)[1].split("Four of these", 1)[0]
    return set(re.findall(r"^``([\w.]+)``\s+\S", table, re.M))


def _emitted_fields(doc) -> set:
    out = set()
    for t in doc["results"]["tests"]:
        for k, v in t.items():
            if k == "extra":
                out.update(f"extra.{ek}" for ek in v)
            else:
                out.add(k)
    return out


def test_the_field_table_documents_every_field_that_is_emitted(text, doc):
    """The direction a second implementation is hurt by: an undocumented field
    is one they will never know to produce."""
    missing = _emitted_fields(doc) - _documented_fields(text)
    assert not missing, f"emitted but undocumented: {sorted(missing)}"


def test_the_field_table_documents_nothing_that_is_not_emitted(text, doc):
    extra = _documented_fields(text) - _emitted_fields(doc)
    assert not extra, f"documented but never emitted: {sorted(extra)}"


def test_the_fields_the_page_calls_optional_really_are(text, doc):
    """Four fields are documented as omitted rather than emitted empty. A
    consumer that trusts that will use `in`, and a writer that starts emitting
    `"tags": []` breaks them quietly."""
    tests = {t["name"]: t for t in doc["results"]["tests"]}
    assert "rawStatus" not in tests["add_a"]
    assert "tags" not in tests["add_a"]
    assert "tags" in tests["overflow_a"]
    assert "checks" not in tests["mul_wide_a"]["extra"]
    assert "checks" in tests["add_a"]["extra"]

    # Counted rather than matched phrase-by-phrase: the table is wrapped, so
    # any exact phrase here would break on a reflow that changed nothing.
    assert len(re.findall(r"\bOmitted\b", text)) >= 4


# --- the status table, both directions ---------------------------------------

def _documented_statuses(text: str) -> dict:
    """``pssunit status -> (ctrf status, rawStatus or None)``."""
    block = text.split("Status mapping", 1)[1].split("Two of these rows", 1)[0]
    rows = {}
    for name, status, raw in re.findall(
            r"^``(\w+)``\s+``(\w+)``\s+(``\w+``|\*\(absent\)\*)", block, re.M):
        rows[name] = (status, None if raw.startswith("*") else raw.strip("`"))
    return rows


def test_the_status_table_is_the_collapse_the_writer_performs(text):
    """The single most load-bearing table on the page: it is what a second
    implementation matches against, so it is checked entry-for-entry rather
    than by count."""
    from pssc.testing.report import _CTRF_STATUS

    documented = _documented_statuses(text)
    real = {s.name: v for s, v in _CTRF_STATUS.items()}
    assert documented == real


def test_every_documented_status_actually_appears_in_a_document(doc):
    """Guards the fixture, not the page: if `_results` stops covering a status,
    the table tests above go on passing while checking less."""
    from pssc.testing.report import _CTRF_STATUS

    seen = {(t["status"], t.get("rawStatus")) for t in doc["results"]["tests"]}
    assert seen == set(_CTRF_STATUS.values())


# --- the summary block -------------------------------------------------------

def test_the_summary_counts_are_the_ones_the_page_quotes(text, doc):
    """The page's whole "the two summaries disagree, and both are right"
    section rests on these six numbers being what the writer produces."""
    summary = doc["results"]["summary"]
    quoted = {"tests": 6, "passed": 2, "failed": 3, "skipped": 1,
              "pending": 0, "other": 0}
    assert {k: summary[k] for k in quoted} == quoted

    for k, v in quoted.items():
        assert f'"{k}": {v}' in text, f"the page no longer quotes {k}: {v}"


def test_the_summary_adds_up_to_the_tests_array(doc):
    """Stated on the page as the reason the counts are collapsed rather than
    pssunit's own. If it stops holding, the page's justification is wrong."""
    s = doc["results"]["summary"]
    assert s["passed"] + s["failed"] + s["skipped"] + s["pending"] + s["other"] \
        == s["tests"] == len(doc["results"]["tests"])


def test_the_human_summary_quoted_for_contrast_is_the_real_one(text):
    """The page prints a human summary line beside the JSON one to show they
    differ. Rendered here from the same results rather than retyped."""
    import io

    from pssc.testing.report import report_human

    buf = io.StringIO()
    report_human(_results(), quiet=True, out=buf)
    line = buf.getvalue().strip()
    counts = line.split("    ")[0]
    assert counts == "1 passed, 1 failed, 1 error, 1 skipped, 1 xfailed, 1 xpassed"
    assert counts in text


# --- the envelope ------------------------------------------------------------

def test_the_envelope_fields_are_what_the_page_shows(text, doc):
    from pssc.testing.report import CTRF_SPEC_VERSION

    assert doc["reportFormat"] == "CTRF"
    assert doc["specVersion"] == CTRF_SPEC_VERSION
    assert doc["generatedBy"] == "pssc test"
    assert doc["results"]["tool"]["name"] == "pssc"

    for shown in ('"reportFormat": "CTRF"',
                  f'"specVersion": "{CTRF_SPEC_VERSION}"',
                  '"generatedBy": "pssc test"',
                  '"tool": {"name": "pssc"}'):
        assert shown in text


# --- the claims about what is absent -----------------------------------------

def test_the_replay_line_is_still_not_stored(text, doc):
    """The page says so explicitly, and contradicts the design doc to say it.
    If the writer ever starts storing it, that note becomes the wrong half."""
    assert "The replay line." in text
    blob = str(doc)
    assert "replay" not in blob
    assert "pssc test -k" not in blob


def test_the_page_still_contradicts_the_design_doc_it_names(text):
    """The note points at `pssunit-mvp-design.md` §10.1 saying the replay line
    goes in `message`. When that doc is corrected the note should go -- and it
    will not be noticed unless something fails."""
    design = Path(__file__).resolve().parents[4] / "pssunit-mvp-design.md"
    if not design.is_file():  # pragma: no cover - pssc built standalone
        pytest.skip("design doc not in this checkout")

    still_says_it = "replay line goes in `message`" in design.read_text()
    warns = "says the replay line goes in ``message``" in text
    assert still_says_it == warns, (
        "the design doc was corrected -- drop the note from ctrf-report.rst"
        if warns else
        "the page dropped the note, but the design doc still says it")
