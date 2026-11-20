"""The PSS on ``docs/action-functions.rst`` does what the page says (D12, T-D1).

The first block is a suite: this file runs it and every test passes. The
second is the op-model example: a verbatim copy of ``_AF_MODEL`` in
``tests/progseq/test_op_model_entry.py``, which runs it on op-model-py and
op-model-sv and checks the messages the page quotes.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

from pssc.testing.cmd import collect
from pssc.testing.runner import Status, run

_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _ROOT / "docs" / "action-functions.rst"

_PSS_BLOCK = re.compile(r"^\.\. code-block:: pss\n\n((?:   .*\n|\n)+)", re.MULTILINE)


def _blocks():
    return [textwrap.dedent(m.group(1)).strip("\n") + "\n"
            for m in _PSS_BLOCK.finditer(_PAGE.read_text())]


def test_the_page_has_two_pss_blocks():
    assert len(_blocks()) == 2


def test_the_template_method_suite_passes(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text(_blocks()[0])
    results = {r.item.name: r for r in run(collect([str(src)]), seed=1,
                                           debug=True, regressions=None)}
    assert sorted(results) == ["add_five_twice", "add_one"]
    for r in results.values():
        assert r.status is Status.PASS, r.detail
        assert r.checks_passed == 1


def test_the_op_model_example_is_the_tested_model():
    text = (_ROOT / "tests" / "progseq" / "test_op_model_entry.py").read_text()
    assert _blocks()[1] in text, (
        "the op-model block on action-functions.rst is not _AF_MODEL in "
        "tests/progseq/test_op_model_entry.py; copy it from there")
