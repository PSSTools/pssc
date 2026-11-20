"""T1.14: ``pssc test`` runs the op-model gate on the code under test first.

A construct the C gate refuses in the device makes ``pssc test`` exit 2 with
the gate's location; the same construct in a test source is not gated.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.cli import main
from pssc.testing import gate

DUT_REFUSED = """\
component dut_c {
    target function int sum(array<int,4> a) {
        return a[0];
    }
}
"""

DUT_OK = """\
component dut_c {
    target function int one() {
        return 1;
    }
}
"""

TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    rt_c rt;
    @pssunit_pkg::test
    function void t_a() {
        rt.check_eq_int(1, 1, "one");
    }
}
"""

# The construct the gate refuses, in a test source: not gated.
TESTS_WITH_REFUSED = TESTS.replace(
    "    rt_c rt;\n",
    "    rt_c rt;\n"
    "    target function int sum(array<int,4> a) { return a[0]; }\n")


def _project(tmp_path, dut: str, tests: str):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "dut.pss").write_text(dut)
    (tmp_path / "tests" / "t.pss").write_text(tests)
    return str(tmp_path)


def test_refused_device_exits_2_located(tmp_path, capsys):
    d = _project(tmp_path, DUT_REFUSED, TESTS)
    assert main(["test", d, "--no-ctrf"]) == 2
    err = capsys.readouterr().err
    assert "op-model-c refuses dut_c" in err
    assert f"{tmp_path}/src/dut.pss:2:5:" in err


def test_test_sources_are_not_gated(tmp_path, capsys):
    d = _project(tmp_path, DUT_OK, TESTS_WITH_REFUSED)
    assert main(["test", d, "--no-ctrf"]) == 0
    assert "1 passed" in capsys.readouterr().out


def test_targets_none_gates_nothing(tmp_path, capsys):
    d = _project(tmp_path, DUT_REFUSED, TESTS)
    assert main(["test", d, "--no-ctrf", "--targets", "none"]) == 0


def test_declared_targets(tmp_path):
    assert gate.declared_targets(None, str(tmp_path / "x.toml")) \
        == ["op-model-c"]
    assert gate.declared_targets("op-model-c, op-model-py") \
        == ["op-model-c", "op-model-py"]
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text('[tool.pssunit]\ntargets = ["op-model-py"]\n')
    assert gate.declared_targets(None, str(cfg)) == ["op-model-py"]
    assert gate.declared_targets("none", str(cfg)) == []
