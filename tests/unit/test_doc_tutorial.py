"""``docs/tutorial.rst`` (D1) shows what really runs (T-D1 for D1).

The page quotes its PSS with ``literalinclude``, so Sphinx makes it verbatim;
what can drift is the path and the ``:start-at:``/``:end-before:`` markers, and
the two outputs the page prints. Those are checked by running the tutorial: the
three files it builds pass, and deleting the lines it tells the reader to
delete gives the card it shows (seeds and times aside).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"
_PAGE = _DOCS / "tutorial.rst"
_EXAMPLE = _ROOT / "examples" / "op_model"
_FILES = ("model.f", "target_cfg.pss", "wb_dma_fx_c.pss", "sw_copy_a.pss")

_INCLUDE = re.compile(r"^(\s*)\.\. literalinclude:: (\S+)\n((?:\1   :.*\n)*)", re.MULTILINE)
_TEXT_BLOCK = re.compile(r"^\.\. code-block:: text\n\n((?:   .*\n|\n)+)", re.MULTILINE)


@pytest.fixture(scope="module")
def text() -> str:
    assert _PAGE.is_file(), f"{_PAGE} is missing"
    return _PAGE.read_text()


def test_the_tutorial_is_in_the_toctree():
    index = (_DOCS / "index.rst").read_text()
    assert re.search(r"^   tutorial$", index, re.MULTILINE)


def test_each_include_names_a_suite_file_and_markers_it_has(text):
    includes = list(_INCLUDE.finditer(text))
    assert len(includes) >= 5
    for m in includes:
        path = (_DOCS / m.group(2)).resolve()
        assert path.is_file(), m.group(2)
        assert path.parent == _EXAMPLE / "tests", f"{m.group(2)} is not in the tested suite"
        body = path.read_text()
        for opt, value in re.findall(r":(start-at|end-before|end-at|start-after): (.*)", m.group(3)):
            assert value in body, f"{m.group(2)}: :{opt}: {value!r} is not in the file"


def _blocks(text):
    return [re.sub(r"^   ", "", b, flags=re.MULTILINE).strip("\n")
            for b in (m.group(1) for m in _TEXT_BLOCK.finditer(text))]


def _pattern(line: str) -> re.Pattern:
    """A doc output line as a regex: seeds and durations may differ."""
    p = re.escape(line.strip())
    p = re.sub(r"seed\\ \d+", r"seed\\ \\d+", p)
    p = re.sub(r"\d+\\\.\d+s", r"\\d+\\.\\d+s", p)
    return re.compile(p)


def _run(tests: Path) -> str:
    env = dict(os.environ, PSSC_NO_DAEMON="1",
               PYTHONPATH=os.pathsep.join([str(_ROOT / "src"), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run(
        [sys.executable, "-m", "pssc", "test", "@model.f", ".", "--platform", "fake",
         "--targets", "none", "--no-ctrf"],
        cwd=tests, env=env, capture_output=True, text=True, timeout=120)
    return proc.stdout + proc.stderr


def _assert_shows(doc_block: str, out: str):
    lines = [l for l in out.splitlines() if l.strip()]
    for want in doc_block.splitlines():
        if not want.strip() or want.strip() == "...":
            continue
        pat = _pattern(want)
        assert any(pat.fullmatch(l.strip()) for l in lines), f"{want!r} not in:\n{out}"


@pytest.fixture
def project(tmp_path):
    shutil.copytree(_EXAMPLE / "pss", tmp_path / "pss")
    (tmp_path / "tests").mkdir()
    for f in _FILES:
        shutil.copy(_EXAMPLE / "tests" / f, tmp_path / "tests" / f)
    return tmp_path / "tests"


def test_the_tutorial_runs_and_prints_what_the_page_shows(text, project):
    command, passing, card = _blocks(text)[:3]
    assert command == "pssc test @model.f . --platform fake --targets none"
    _assert_shows(passing, _run(project))

    # Section 4: leave out the two status_sequence lines and their comment.
    src = project / "sw_copy_a.pss"
    body = src.read_text()
    kept = [l for l in body.splitlines(keepends=True)
            if "status_sequence" not in l and "// The fake has no DMA engine" not in l
            and "// read is the arming" not in l]
    assert len(body.splitlines()) - len(kept) == 4
    src.write_text("".join(kept))
    _assert_shows(card, _run(project))
