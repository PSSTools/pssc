"""Long runs on the C engine: many calls and many steps, checked against the
same run on the Python engine.

Found by timing ``--engine c`` against ``--engine py``
(``docs/design/c-engine-issues-plan.md``): the C engine crashed (SIGSEGV, no
report) on the debug image once a test made 4800 calls, and stopped a run of
a few million steps with ``step limit exhausted``. Each case runs ``pssc
test`` in a child process, so a crash is a failed case rather than a dead
test session, and checks the exact value the loop computes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")
pytest.importorskip("zuspec.rt.eng.host")

_M = 0xFFFFFFFF

_SUITE = """\
import std_pkg::*;
import pssunit_pkg::*;

component bench_c : fixture_c {
    function bit[32] mix(bit[32] x) {
        return (x * 1103515245 + 12345) ^ (x >> 7);
    }

    // Arithmetic and branches in a loop: one function, no calls.
    @pssunit_pkg::test
    function void t_arith() {
        bit[32] acc = 1;
        repeat (i : N) {
            acc = acc * 1664525 + 1013904223;
            if ((acc & 1) == 1) { acc = acc ^ (acc >> 3); }
        }
        check_eq(acc, ARITH, "arith");
    }

    // A function call per iteration.
    @pssunit_pkg::test
    function void t_calls() {
        bit[32] acc = 1;
        repeat (i : N) {
            acc = mix(acc);
        }
        check_eq(acc, CALLS, "calls");
    }
}
"""


def _arith(n: int) -> int:
    a = 1
    for _ in range(n):
        a = (a * 1664525 + 1013904223) & _M
        if a & 1:
            a ^= a >> 3
    return a


def _calls(n: int) -> int:
    a = 1
    for _ in range(n):
        a = ((a * 1103515245 + 12345) & _M) ^ (a >> 7)
    return a


def _run(tmp_path: Path, n: int, test: str, *args: str):
    src = (_SUITE.replace("ARITH", str(_arith(n)))
           .replace("CALLS", str(_calls(n)))
           .replace("repeat (i : N)", f"repeat (i : {n})"))
    (tmp_path / "bench.pss").write_text(src)
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(root), env.get("PYTHONPATH")) if p)
    r = subprocess.run(
        [sys.executable, "-m", "pssc", "test", "bench.pss", f"bench_c::{test}",
         "--targets", "none", "--no-ctrf", "--seed", "1", *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=600)
    return r.returncode, r.stdout + r.stderr


@pytest.mark.parametrize("image", [(), ("--no-debug",)], ids=["debug", "release"])
def test_many_calls_on_the_c_engine(tmp_path, image):
    """10000 calls: the debug image keeps each as a call with a frame."""
    rc, out = _run(tmp_path, 10000, "t_calls", "--engine", "c", *image)
    assert rc == 0 and "1 passed" in out, out


@pytest.mark.parametrize("image", [(), ("--no-debug",)], ids=["debug", "release"])
def test_many_steps_on_the_c_engine(tmp_path, image):
    """1M loop iterations, ~10M steps: past where the run used to stop."""
    rc, out = _run(tmp_path, 1_000_000, "t_arith", "--engine", "c", *image)
    assert rc == 0 and "1 passed" in out, out
