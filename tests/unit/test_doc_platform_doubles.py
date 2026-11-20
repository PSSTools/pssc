"""The code on ``docs/platform-doubles.rst`` is code a unit test runs (T-D1).

As for ``writing-tests.rst`` (test_doc_writing_tests.py): each PSS or Python
block is an excerpt, verbatim, of a unit test's source -- a PSS source
string, or the test's own Python -- so the test keeps it working and this
file keeps the page a copy of it. The match ignores the block's indentation
on the page and allows the source's to be 0, 4 or 8 (a block may come from
inside a test function); nothing else.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"
_PAGE = _DOCS / "platform-doubles.rst"

_BLOCK = re.compile(r"^\.\. code-block:: (pss|python)\n\n((?:   .*\n|\n)+)", re.MULTILINE)


def _blocks():
    return [(lang, textwrap.dedent(body).strip("\n") + "\n")
            for lang, body in _BLOCK.findall(_PAGE.read_text())]


def _sources() -> str:
    return "\n".join(p.read_text() for p in sorted((_ROOT / "tests" / "unit").glob("test_*.py"))
                     if p.name != Path(__file__).name)


_BLOCKS = _blocks()


def test_the_page_has_code_blocks():
    langs = [lang for lang, _ in _BLOCKS]
    assert langs.count("pss") >= 3 and langs.count("python") >= 6


@pytest.mark.parametrize("lang,block", _BLOCKS,
                         ids=[f"{i}-{b.splitlines()[0][:40]}" for i, (_, b) in enumerate(_BLOCKS)])
def test_each_block_is_a_tested_source(lang, block):
    src = _sources()
    assert any(textwrap.indent(block, " " * n) in src for n in (0, 4, 8)), (
        f"this {lang} block on platform-doubles.rst is not a verbatim excerpt "
        "of any unit test's source; copy it from the test that runs it:\n" + block)


def test_the_page_is_in_the_toctree():
    index = (_DOCS / "index.rst").read_text()
    toctree = index.split(".. toctree::", 1)[1].split("\n\n", 2)[1]
    assert "platform-doubles" in toctree.split()


@pytest.mark.parametrize("page", ["writing-tests.rst", "test-command.rst"])
def test_the_pages_it_extends_link_to_it(page):
    assert ":doc:`platform-doubles`" in (_DOCS / page).read_text()
