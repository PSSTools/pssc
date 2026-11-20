"""The PSS on ``docs/writing-tests.rst`` is PSS a test compiles and runs (T-D1).

A code block on a reference page is copied by readers, so one that stopped
parsing -- or never did -- costs them more than prose would. Each block is
therefore an excerpt, verbatim, of a source string some unit test runs: the
test keeps the PSS working, and this file keeps the page a copy of it.

The match ignores each line's common indentation (a block is indented in the
page, a source string is not) and nothing else.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _ROOT / "docs" / "writing-tests.rst"

_PSS_BLOCK = re.compile(r"^\.\. code-block:: pss\n\n((?:   .*\n|\n)+)", re.MULTILINE)

#: Blocks written for the page alone, before this check; each names why.
_UNCHECKED = {
    "component dma_f : fixture_c {": "set-up/tear-down sketch",
    "@pssunit_pkg::property {.runs=200}": "property sketch",
    "@pssunit_pkg::test": "expect_error sketch",
}


def _blocks():
    text = _PAGE.read_text()
    return [textwrap.dedent(m.group(1)).strip("\n") + "\n"
            for m in _PSS_BLOCK.finditer(text)]


def _sources() -> str:
    return "\n".join(p.read_text() for p in sorted((_ROOT / "tests" / "unit").glob("test_*.py"))
                     if p.name != Path(__file__).name)


_BLOCKS = _blocks()


def test_the_page_has_pss_blocks():
    assert len(_BLOCKS) >= 6


@pytest.mark.parametrize("block", _BLOCKS, ids=[b.splitlines()[0] for b in _BLOCKS])
def test_each_block_is_a_tested_source(block):
    first = block.splitlines()[0]
    if first in _UNCHECKED:
        pytest.skip(_UNCHECKED[first])
    assert block in _sources(), (
        "this block on writing-tests.rst is not a verbatim excerpt of any "
        "source string in tests/unit; copy it from the test that runs it:\n" + block)
