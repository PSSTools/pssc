"""`pssc build --native`: a test needing Python doubles is refused up front
with its location, since a simulation on the C engine has no Python (P4.9c,
design O14)."""
from __future__ import annotations

import sys

import pytest

from pssc import cli

SUITE = """\
import pssunit_pkg::*;

import function bit[32] plat_id();

component n_f : fixture_c {
    function bit[32] scale(bit[32] x) { return x * 2; }
    @pssunit_pkg::test
    function void t_scale() {
        check_eq(scale(2), 4);
    }
    @pssunit_pkg::test
    function void t_id() {
        check_eq(plat_id(), 7);
    }
}
"""

MOCK = """\
import pssc.testing.vm as pv

@pv.mock("n_f::scale")
def _scale(ctx, self_, x):
    return x * 3
"""

IMPL = """\
import pssc.testing.vm as pv

@pv.implements("plat_id")
def _id(ctx):
    return 7
"""

ELSEWHERE = """\
import pssc.testing.vm as pv

@pv.spy("other_c::f")
def _f(call):
    pass
"""


@pytest.fixture
def suite(tmp_path, monkeypatch):
    (tmp_path / "s.pss").write_text(SUITE)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    for m in [m for m in sys.modules if m.startswith("p49c_")]:
        del sys.modules[m]


def _build(suite, mod, text, *argv):
    (suite / f"{mod}.py").write_text(text)
    return cli.main(["build", "s.pss", "--image", "out/s.zbc", "--native",
                     "--platform", f"py:{mod}", *argv])


def test_a_mock_of_a_function_in_the_image_is_refused_with_both_places(
        suite, capsys):
    assert _build(suite, "p49c_mock", MOCK) == 2
    err = capsys.readouterr().err
    assert "no Python" in err
    assert f"{suite / 'p49c_mock.py'}:3: @pv.mock('n_f::scale')" in err
    assert "s.pss:6" in err
    assert not (suite / "out" / "s.zbc").exists()


def test_an_implemented_import_is_refused(suite, capsys):
    assert _build(suite, "p49c_impl", IMPL) == 2
    err = capsys.readouterr().err
    assert f"{suite / 'p49c_impl.py'}:3: @pv.implements('plat_id')" in err


def test_doubles_of_nothing_in_the_image_and_no_platform_build(
        suite, capsys):
    assert _build(suite, "p49c_else", ELSEWHERE) == 0
    assert (suite / "out" / "s.zbc").exists()
    assert cli.main(["build", "s.pss", "--image", "out/t.zbc",
                     "--native"]) == 0


def test_without_native_the_same_doubles_build(suite):
    (suite / "p49c_mock2.py").write_text(MOCK)
    assert cli.main(["build", "s.pss", "--image", "out/s.zbc",
                     "--platform", "py:p49c_mock2"]) == 0
