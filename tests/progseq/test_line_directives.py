"""P5.11 -- `--line-directives`: generated code names its PSS source lines.

With the option, each statement of an operation body is preceded by the
language's line directive (`#line N "file.pss"` in C and C++,
`` `line N "file.pss" 0 `` in SystemVerilog), so gdb steps and gcov counts
PSS lines rather than generated ones, and a simulator reports them. Without it the output
is unchanged -- the golden snapshots (`test_golden_op_models.py`) hold that.

The C case is the behavioural gate's model and testbench
(`test_op_model_behaviour_c.py`): the directives move no access, so the
trace still passes. The SV case is `test_sv_op_model_sim.py`'s model and
testbench, built and run by Verilator with the same expected trace.
"""
import argparse
import os
import re
import subprocess

import pytest

from pssc import driver

from .conftest import available_c_compilers, available_cpp_compilers
from .op_model import op_model_sources as _sources
from .test_op_model_behaviour_c import _build_and_run, _generate
from . import test_sv_op_model_sim as sv_sim

_CC = available_c_compilers()
_CXX = available_cpp_compilers()
_LINE = re.compile(r'^#line (\d+) "([^"]+)"$', re.M)


def _directives(text):
    return [(int(n), f) for n, f in _LINE.findall(text)]


def _check_points_at_pss(text):
    """Every directive names an absolute .pss path on disk and a line that
    exists in it."""
    found = _directives(text)
    assert found, "no #line directive"
    for line, path in found:
        assert os.path.isabs(path) and path.endswith(".pss"), path
        with open(path) as f:
            n = sum(1 for _ in f)
        assert 1 <= line <= n, (path, line, n)
    return found


def test_off_by_default(tmp_path):
    out = _generate(tmp_path, c_lifecycle="static")
    for name in ("wb_dma.c", "wb_dma.h"):
        with open(os.path.join(out, name)) as f:
            assert "#line" not in f.read(), name


def test_c_names_the_pss_lines(tmp_path):
    out = _generate(tmp_path, c_lifecycle="static", line_directives=True)
    with open(os.path.join(out, "wb_dma.c")) as f:
        found = _check_points_at_pss(f.read())
    # One per statement, so many, and more than one distinct line.
    assert len({ln for ln, _ in found}) > 10


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
def test_c_with_directives_builds_and_keeps_the_trace(tmp_path):
    out = _generate(tmp_path, c_lifecycle="static", line_directives=True)
    res = _build_and_run(out, _CC[0])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "WB_DMA OP MODEL PASS" in res.stdout, res.stdout


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
def test_c_debug_info_names_the_pss_file(tmp_path):
    """What gdb and gcov read: with `-g` the line table lists the .pss."""
    out = _generate(tmp_path, c_lifecycle="static", line_directives=True)
    asm = os.path.join(out, "wb_dma.s")
    res = subprocess.run([_CC[0], "-g", "-std=c99", "-S", "-I", out,
                          os.path.join(out, "wb_dma.c"), "-o", asm],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    with open(asm) as f:
        text = f.read()
    assert re.search(r'\.file\s+\d+\s+(?:"[^"]*"\s+)?"[^"]*\.pss"', text), \
        "no .pss in the line table"


@pytest.fixture(scope="module")
def cpp_out(tmp_path_factory):
    out = tmp_path_factory.mktemp("line_cpp")
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out),
                            line_directives=True)
    driver.compile(_sources(), target="op-model-cpp", opts=ns)
    return out


def test_cpp_names_the_pss_lines(cpp_out):
    _check_points_at_pss((cpp_out / "wb_dma.hpp").read_text())


@pytest.mark.skipif(not _CXX, reason="no C++ compiler on PATH")
def test_cpp_with_directives_compiles(cpp_out, tmp_path):
    tu = tmp_path / "tu.cpp"
    tu.write_text(
        '#include "wb_dma.hpp"\n'
        'namespace pssc { void message(const char *, ...) {} }\n'
        'int main() { return 0; }\n')
    res = subprocess.run(
        [_CXX[0], "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I",
         str(cpp_out), "-c", str(tu), "-o", os.devnull],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


_SV_LINE = re.compile(r'^`line (\d+) "([^"]+)" 0$', re.M)


@pytest.mark.skipif(sv_sim._VERILATOR is None, reason="verilator not on PATH")
def test_sv_names_the_pss_lines_and_runs_the_same(tmp_path):
    stdout = sv_sim._build_and_run(tmp_path, sv_sim._MODEL, sv_sim._TB,
                                   line_directives=True)
    trace = [ln[len("TRACE "):] for ln in stdout.splitlines()
             if ln.startswith("TRACE ")]
    assert trace == sv_sim._EXPECTED

    src = tmp_path / "model.pss"
    n = len(src.read_text().splitlines())
    found = []
    for p in (tmp_path / "gen").iterdir():
        if p.suffix in (".sv", ".svh"):
            found += [(int(ln), f) for ln, f in _SV_LINE.findall(p.read_text())]
    assert found, "no `line directive"
    for line, path in found:
        assert path == str(src), path
        assert 1 <= line <= n, (line, n)
    assert len({ln for ln, _ in found}) > 3


_PY_LINE = re.compile(r'^\s*# (/\S+\.pss):(\d+)$', re.M)


def _py_module(tmp_path, **opts):
    out = tmp_path / ("on" if opts else "off")
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out),
                            **opts)
    driver.compile(_sources(), target="op-model-py", opts=ns)
    return out / "wb_dma.py"


def test_py_names_the_pss_lines_in_comments(tmp_path):
    import py_compile
    off = _py_module(tmp_path).read_text()
    on_path = _py_module(tmp_path, line_directives=True)
    on = on_path.read_text()
    assert not _PY_LINE.search(off)

    found = [(int(n), p) for p, n in _PY_LINE.findall(on)]
    assert len({n for n, _ in found}) > 3
    for line, path in found:
        with open(path) as f:
            n = sum(1 for _ in f)
        assert 1 <= line <= n, (path, line, n)
    # Only comment lines are added, and the module still compiles.
    assert [ln for ln in on.splitlines() if not _PY_LINE.match(ln)] \
        == off.splitlines()
    py_compile.compile(str(on_path), doraise=True)
