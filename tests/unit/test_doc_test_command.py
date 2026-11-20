"""``docs/test-command.rst`` says things that can stop being true (D1.1).

The page is the CLI reference for ``pssc test``, so nearly all of it is a copy
of something that lives in code: every option, every exit code, every status
name, the default report path, the wording of two errors. A copy in prose is the
kind nothing fails on when it drifts -- the reader is simply told something
false.

The option table is checked in **both** directions, which the other doc tests do
not need to do. A reference page that documents a flag the CLI dropped is bad; a
reference page that silently omits a flag the CLI has is worse, because the
reader concludes the flag does not exist. This page was written precisely
because ``pssc test`` had no reference at all and two other pages were routing
around the hole, so "the surface is complete" is the property most worth
pinning.

What is not checked is the prose -- the argument for why internal error moved to
``3``, say. That is not a value and cannot drift silently, only deliberately.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parents[2] / "docs"
_PAGE = _DOCS / "test-command.rst"


@pytest.fixture(scope="module")
def text() -> str:
    assert _PAGE.is_file(), f"{_PAGE} is missing"
    return _PAGE.read_text()


def _test_subparser(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["test"]
    raise AssertionError("no `test` subcommand")


@pytest.fixture(scope="module")
def sub():
    from pssc.cli import build_parser
    return _test_subparser(build_parser())


def test_the_page_is_in_the_toctree():
    """A page outside the toctree is not published, and Sphinx says so only as
    a warning -- which is to say, not at all in a build nobody reads."""
    index = (_DOCS / "index.rst").read_text()
    toctree = index.split(".. toctree::", 1)[1].split("\n\n", 2)[1]
    assert "test-command" in toctree.split()


# --- the option surface, both directions ------------------------------------

def _documented_options(text: str) -> set:
    """Every ``--flag`` / ``-f`` the page writes in literal markup."""
    # A trailing metavar is part of the literal (``--ctrf PATH``), so the flag
    # is matched up to the first space rather than to the closing backticks.
    return set(re.findall(r"``(--?[\w-]+)[^`]*``", text))


def _real_options(sub) -> set:
    known = set()
    for action in sub._actions:
        known.update(action.option_strings)
    return known - {"-h", "--help"}


def test_the_page_documents_no_option_the_command_lacks(text, sub):
    documented = _documented_options(text)
    assert documented, "the page names no options; this test has stopped checking"
    extra = documented - _real_options(sub)
    assert not extra, f"documented but absent from `pssc test`: {sorted(extra)}"


def test_the_page_documents_every_option_the_command_has(text, sub):
    """The direction that matters most: an undocumented flag reads to the
    reader as a flag that does not exist."""
    missing = _real_options(sub) - _documented_options(text)
    assert not missing, f"`pssc test` accepts but the page omits: {sorted(missing)}"


def test_the_option_reference_table_is_the_complete_one(text, sub):
    """Two places on the page name options -- the prose and the closing table.
    The table is the one a reader scans to see the whole surface, so it is the
    one that has to be exhaustive rather than merely correct.
    """
    table = text.split("Option reference", 1)[1]
    in_table = set(re.findall(r"``(--?[\w-]+)[^`]*``", table))
    missing = _real_options(sub) - in_table
    assert not missing, f"absent from the option-reference table: {sorted(missing)}"


def test_the_documented_default_report_path_is_the_real_one(text, sub):
    """Named twice on the page, and it is the file CI goes looking for."""
    default = next(a.default for a in sub._actions if "--ctrf" in a.option_strings)
    assert f"``{default}``" in text


# --- exit codes --------------------------------------------------------------

def test_the_exit_code_table_matches_the_constants(text):
    """The whole point of R-5's codes is that a caller can rely on the
    numbers, so these five are load-bearing in a way most documented values are
    not -- a wrong one here makes CI treat a crash as a test failure.
    """
    from pssc.testing import cmd

    # First match wins: the prose below the table also opens lines with ``2``,
    # and it is the table row that has to carry the gloss.
    rows: dict = {}
    for code, gloss in re.findall(r"^``(\d)``\s+(\S.*)$", text, re.M):
        rows.setdefault(code, gloss)
    assert set(rows) == {"0", "1", "2", "3", "5"}, f"exit-code table has {sorted(rows)}"

    assert str(cmd.EXIT_OK) == "0"
    assert str(cmd.EXIT_TESTS_FAILED) == "1"
    assert str(cmd.EXIT_COMPILE_ERROR) == "2"
    assert str(cmd.EXIT_INTERNAL) == "3"
    assert str(cmd.EXIT_NO_TESTS) == "5"

    # Not just the numbers: the page's gloss for each has to still describe it.
    assert "failed" in rows["1"]
    assert "did not" in rows["2"]
    assert "nternal error" in rows["3"]
    assert "nothing selected" in rows["5"]


def test_the_other_subcommands_codes_are_said_to_be_unchanged(text):
    """`pssc test`'s codes are its own; a reader of either page must not come
    away thinking `parse` or `compile` now exit 3 on an internal error."""
    assert "keep theirs" in text
    assert "`pssc test` has its own codes" in (_DOCS / "cli.md").read_text()


# --- statuses ----------------------------------------------------------------

def test_the_status_table_lists_exactly_the_statuses_that_exist(text):
    """A missing status is one the reader meets for the first time in a failing
    run and has to guess at; an invented one sends them looking for it."""
    from pssc.testing.report import _HUMAN_STATUS

    block = text.split("Statuses", 1)[1].split("Escape hatches", 1)[0]
    listed = set(re.findall(r"^``(\w+)``\s+\S", block, re.M))
    assert listed == set(_HUMAN_STATUS.values())


def test_xpass_is_documented_as_a_failure_because_it_is_one(text):
    """The page tells the reader an unexpected pass fails the run. If XPASS ever
    became `ok`, that sentence would be the misleading part, not the code."""
    from pssc.testing.report import _CTRF_STATUS
    from pssc.testing.runner import Status

    assert "``XPASS`` is a **failure**" in text
    assert _CTRF_STATUS[Status.XPASS][0] == "failed"


# --- the quoted output -------------------------------------------------------

def test_the_replay_line_has_the_shape_the_page_shows(text):
    """The page calls this the highest-value line in the report and shows it
    three times. It is also the one line a reader will copy verbatim.
    """
    from pssc.testing.report import replay_command

    shown = re.findall(r"^\s*replay: (.+)$", text, re.M)
    assert shown, "the page shows no replay line; this test has stopped checking"

    class _Meta:
        group = ""

    class _Item:
        name = "overflow_a"
        qname = "alu_tests_c::overflow_a"
        comp = "alu_tests_c"
        meta = _Meta()

    class _Result:
        item = _Item()
        seed = 7

    built = replay_command(_Result(), specs=["demo.pss"])
    assert built in shown, f"page shows {shown}, replay_command builds {built!r}"


def test_the_quoted_errors_are_the_ones_that_are_raised(text, tmp_path):
    """Two error messages are quoted verbatim as the thing the user will see."""
    from pssc.testing.cmd import filter_items
    from pssc.testing.sources import SourceSpecError, resolve_sources

    with pytest.raises(SourceSpecError) as e:
        resolve_sources([str(tmp_path / "tset")])
    assert f"source spec matches nothing: '{tmp_path / 'tset'}'" == str(e.value)
    assert "source spec matches nothing:" in text

    # The other one is assembled by cmd_test from the filters that were set, so
    # what is pinned here is that a filter matching nothing yields nothing to
    # run -- the condition the quoted `no tests matched -k nope` reports on.
    assert filter_items([], k="nope") == []
    assert "no tests matched -k nope" in text


def test_the_src_spec_table_covers_every_form_the_resolver_accepts(text, tmp_path):
    """Four forms, and the page's table is the only place they are listed
    together. Each is exercised rather than matched against a second list."""
    from pssc.testing.sources import resolve_sources

    (tmp_path / "sub").mkdir()
    a = tmp_path / "sub" / "a_a.pss"
    b = tmp_path / "sub" / "b_a.pss"
    a.write_text("")
    b.write_text("")
    flist = tmp_path / "files.f"
    flist.write_text("# a comment\nsub/a_a.pss\n")

    assert resolve_sources([str(a)]) == [a]
    assert resolve_sources([str(tmp_path / "sub")]) == [a, b]
    assert resolve_sources([str(tmp_path / "sub" / "*_a.pss")]) == [a, b]
    assert resolve_sources([f"@{flist}"]) == [a]

    for form in ("``@filelist``", "recursively", "glob"):
        assert form in text

    # Overlap is documented as harmless; a duplicate reaching the parser is not.
    assert resolve_sources([str(tmp_path / "sub"), str(a)]) == [a, b]
