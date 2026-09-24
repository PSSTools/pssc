"""A sub-component whose type is declared AFTER its use is still in the model.

PSS lets a field name a type declared further down the file or in a later file.
The front end held such a field as a `DataTypeRef`, and the operation-model
tree walk (`progseq_model.sub_components`) passes over anything that is not a
`DataTypeComponent` -- so `component top { sub_c s; } component sub_c {...}`
lowered with no `s` at all, silently. `ast2ir._resolve_subcomponent_refs` makes
the forward case produce the IR the backward case does; these tests hold every
op-model target to "the order of declarations changes nothing".
"""
from __future__ import annotations

import argparse
import importlib
import sys

import pytest

from pssc import driver

_TOP = """
component pss_top {
  sub_c s;
  sub_c arr[2];
  target function void g() { message(NONE, "g"); }
}
"""

#: The Python target also runs the sub-components' operations (op-model-c
#: does not lower a call into a sub-component in either order).
_TOP_CALLS = _TOP.replace('message(NONE, "g");', "s.f(); arr[1].f();")
_SUB = """
component sub_c {
  target function void f() { message(NONE, "f"); }
}
"""


def _gen(tmp_path, files, target):
    srcs = []
    for k, text in enumerate(files):
        p = tmp_path / f"f{k}.pss"
        p.write_text("import std_pkg::*;\n" + text)
        srcs.append(str(p))
    out = tmp_path / "out"
    ns = argparse.Namespace(output_dir=str(out), progseq_root="pss_top")
    driver.compile(srcs, target=target, opts=ns)
    return out


def _texts(out):
    return {f.name: f.read_text() for f in sorted(out.iterdir()) if f.is_file()}


@pytest.mark.parametrize("target", ["op-model-c", "op-model-cpp",
                                    "op-model-sv", "op-model-py"])
@pytest.mark.parametrize("layout", ["one file", "two files"])
def test_declaration_order_changes_nothing(tmp_path, target, layout):
    def files(first, second):
        return [first + second] if layout == "one file" else [first, second]

    (tmp_path / "bwd").mkdir()
    (tmp_path / "fwd").mkdir()
    backward = _texts(_gen(tmp_path / "bwd", files(_SUB, _TOP), target))
    forward = _texts(_gen(tmp_path / "fwd", files(_TOP, _SUB), target))
    assert forward == backward


def test_a_forward_declared_sub_component_runs(tmp_path):
    out = _gen(tmp_path, [_TOP_CALLS, _SUB], "op-model-py")
    sys.path.insert(0, str(out))
    try:
        for name in ("pss_top", "pssc_rt"):
            sys.modules.pop(name, None)
        mod = importlib.import_module("pss_top")
        rt = importlib.import_module("pssc_rt")
    finally:
        sys.path.remove(str(out))
    bus = rt.MemoryBus()
    mod.PssTop(bus).g()
    assert [t for k, _, _, t in bus.log if k == "message"] == ["f", "f"]
