"""T1.15: ``pssc test --platform py:<mod>[:<Class>]`` runs a Python device
model bound by the run, never by the PSS source.

The module's ``@pv.*`` doubles and a new ``Class()`` apply to each test;
``[tool.pssunit] platform`` is the default; a module that does not import is
exit 3, with the traceback in ``.pssunit/platform.log``.
"""
from __future__ import annotations

import sys

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.cli import main
from pssc.testing import vm as pv

DUT = """\
import function bit[32] plat_id();

component dut_c {
    function bit[32] id() {
        return plat_id();
    }
}
"""

# Two tests, each expecting a fresh platform: the class counts its calls.
TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    rt_c rt;
    @pssunit_pkg::test
    function void t_a() {
        rt.check_eq_int(dut.id(), 0x5A, "id");
    }
    @pssunit_pkg::test
    function void t_b() {
        rt.check_eq_int(dut.id(), 0x5A, "id");
    }
}
"""

BOARD = """\
import pssc.testing.vm as pv

class Board(pv.Platform):
    def __init__(self):
        super().__init__()
        self.calls = 0
    def plat_id(self):
        self.calls += 1
        return 0x5A if self.calls == 1 else 0   # a shared instance fails t_b
"""

DOUBLES = """\
import pssc.testing.vm as pv

@pv.implements("plat_id")
def plat_id(ctx):
    return 0x5A
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "dut.pss").write_text(DUT)
    (tmp_path / "tests" / "t.pss").write_text(TESTS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return tmp_path


def test_without_a_platform_the_import_returns_0(project, capsys):
    assert main(["test", ".", "--no-ctrf"]) == 1


def test_a_class_per_test(project, capsys):
    (project / "t115_board.py").write_text(BOARD)
    assert main(["test", ".", "--no-ctrf",
                 "--platform", "py:t115_board:Board"]) == 0
    assert "2 passed" in capsys.readouterr().out


def test_module_doubles_and_pyproject_default(project, capsys):
    (project / "t115_doubles.py").write_text(DOUBLES)
    (project / "pyproject.toml").write_text(
        '[tool.pssunit]\nplatform = "py:t115_doubles"\n')
    assert main(["test", ".", "--no-ctrf"]) == 0
    assert "2 passed" in capsys.readouterr().out


def test_a_missing_module_is_exit_3(project, capsys):
    assert main(["test", ".", "--no-ctrf",
                 "--platform", "py:t115_no_such_module"]) == 3
    out = capsys.readouterr().out
    assert "t115_no_such_module" in out and ".pssunit/platform.log" in out
    log = (project / ".pssunit" / "platform.log").read_text()
    assert "ModuleNotFoundError" in log


def test_bad_specs():
    with pytest.raises(pv.PlatformLoadError, match="expected fake or py:"):
        pv.load_platform("so:lib")
    with pytest.raises(pv.PlatformLoadError, match="no class 'Nope'"):
        pv.load_platform("py:pssc.testing.vm:Nope")
    with pytest.raises(pv.PlatformLoadError, match="declares no doubles"):
        pv.load_platform("py:json")
