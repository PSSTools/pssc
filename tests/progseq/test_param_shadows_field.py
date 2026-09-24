"""A parameter shadows a component field of the same name.

The front end spells a parameter reference and a field reference alike
(`self.a`), so the name is resolved in scope order: parameter first. The type
analysis (`ExprTypes`) and bc did that; every op-model emitter checked fields
first, so `f(int a) { return a * 2; }` in a component with a field `a` read the
FIELD -- `self.a`, `s->a`, `m_a`, `this->a` -- and compiled cleanly.
"""
from __future__ import annotations

import argparse
import importlib
import sys

import pytest

from pssc import driver

_MODEL = """
import std_pkg::*;
component pss_top {
  int a = 100;
  target function int f(int a) { a = a + 1; return a * 2; }
}
"""

#: How each target would spell the FIELD `a`.
_FIELD = {"op-model-py": "self.a", "op-model-c": "->a",
          "op-model-sv": "m_a", "op-model-cpp": "this->a"}


def _compile(tmp_path, target):
    p = tmp_path / "m.pss"
    p.write_text(_MODEL)
    out = tmp_path / "out"
    driver.compile([str(p)], target=target, opts=argparse.Namespace(
        progseq_root="pss_top", output_dir=str(out)))
    return out


@pytest.mark.parametrize("target", sorted(_FIELD))
def test_the_body_never_names_the_field(tmp_path, target):
    text = "\n".join(f.read_text() for f in _compile(tmp_path, target).iterdir()
                     if f.is_file() and f.name.startswith("pss_top"))
    assert "a * 2" in text
    assert f"{_FIELD[target]} * 2" not in text
    assert f"{_FIELD[target]} + 1" not in text


def test_the_parameter_is_what_runs(tmp_path):
    out = _compile(tmp_path, "op-model-py")
    sys.path.insert(0, str(out))
    try:
        for name in ("pss_top", "pssc_rt"):
            sys.modules.pop(name, None)
        mod = importlib.import_module("pss_top")
        rt = importlib.import_module("pssc_rt")
    finally:
        sys.path.remove(str(out))
    dut = mod.PssTop(rt.MemoryBus())
    assert (dut.f(3), dut.a) == (8, 100)
