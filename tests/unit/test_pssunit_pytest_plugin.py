"""T1.19: pytest-pssunit (``pssc.testing.pytest_plugin``).

pytest collects PSS tests only when ``pssunit_paths`` is set; the ``pss``
fixture calls an operation. Each case runs pytest in a subprocess on a tiny
project, loading the plugin with ``-p`` (the ``pytest11`` entry point is
only there once pssc is installed).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from .test_pssunit_isolation import SUITE  # noqa: E402

MODEL = """\
component calc_c {
    function bit[32] twice(bit[32] x) { return x * 2; }
}
"""

PY_TEST = """\
def test_twice(pss):
    s = pss.load("calc_c")
    assert s.call(s.construct(), "twice", 21) == 42
"""

SRC = str(Path(__file__).resolve().parents[2] / "src")


def _project(tmp_path: Path, ini: str) -> Path:
    (tmp_path / "pss").mkdir()
    (tmp_path / "pss" / "suite.pss").write_text(SUITE)
    (tmp_path / "pss" / "model.pss").write_text(MODEL)
    (tmp_path / "test_py.py").write_text(PY_TEST)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n" + ini)
    return tmp_path


def _pytest(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PSSC_NO_DAEMON="1",
               PYTHONPATH=os.pathsep.join(
                   [SRC] + [p for p in os.environ.get("PYTHONPATH", "")
                            .split(os.pathsep) if p]))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pssc.testing.pytest_plugin",
         "-p", "no:cacheprovider", "-rA", "-q", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


def test_inert_without_pssunit_paths(tmp_path):
    p = _project(tmp_path, "")
    out = _pytest(p)
    # the .pss files are not collected; only the Python test, whose `pss`
    # fixture says what is missing
    assert "suite.pss" not in out.stdout
    assert "1 error" in out.stdout, out.stdout
    assert "needs pssunit_paths" in out.stdout
    assert not (p / ".pssunit").exists()


def test_pss_tests_are_items(tmp_path):
    p = _project(tmp_path, 'pssunit_paths = ["pss"]\n')
    out = _pytest(p, "--pss-seed", "7")
    assert "2 failed, 3 passed" in out.stdout, out.stdout
    for t in ("t_a", "t_b"):
        assert f"PASSED pss/suite.pss::s_f::{t}" in out.stdout
    assert "FAILED pss/suite.pss::s_f::t_fails" in out.stdout
    assert "FAILED pss/suite.pss::s_f::t_errors" in out.stdout
    assert "PASSED test_py.py::test_twice" in out.stdout
    # the failure carries `pssc test`'s replay line, with the run's seed
    assert f"replay: pssc test {p / 'pss'} s_f::t_fails --seed 7" \
        in out.stdout


def test_a_node_id_selects_one_test(tmp_path):
    p = _project(tmp_path, 'pssunit_paths = ["pss"]\n')
    out = _pytest(p, "pss/suite.pss::s_f::t_b")
    assert "1 passed" in out.stdout, out.stdout
    assert "t_a" not in out.stdout


def test_the_doc_example_is_this_test():
    page = (Path(__file__).resolve().parents[2] / "docs"
            / "test-command.rst").read_text()
    block = "".join("   " + ln + "\n" for ln in PY_TEST.splitlines())
    assert block in page
