"""An exported action is an entry point of an operation model.

A compliance test is ordinary PSS: an action whose `exec body` calls component
functions. Every PSS tool can run that. `--export-action` identifies the action
as an entry point, and an operation model then gets a method with no parameters
and no result on the component the action runs in, so the same test runs on it
unchanged. See `src/pssc/targets/export_action.py`.

Driven, not grepped: the generated module is imported and the entry called.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys

import pytest

from pssc import driver
from pssc.targets.export_action import ExportActionError, entry_point
from pssc.testing import compile_op_model

_MODEL = """
import std_pkg::*;

component leaf_c {
  int hits;
  target function void poke(int v) { hits += v; }
}

component eng_c {
  int count;
  leaf_c leaf;
  leaf_c lanes[2];
  target function int run(int v) {
    count += v;
    leaf.poke(v);
    lanes[1].poke(2 * v);
    return v * 10;
  }
}

component pss_top {
  eng_c e;
  int total;

  target function void add(int v) { total += v; }

  action A {
    exec body {
      message(NONE, "@@PSS-TRACE act pss_top::A");
      int r1 = comp.e.run(3);
      int r2 = comp.e.run(4);
      comp.add(r1 + r2);
      if (comp.total == 70) {
        message(NONE, "@@PSS-TRACE chk ok");
      }
    }
  }

  action B {
    exec body {
      comp.add(1);
    }
  }
}
"""


def _load(outcome, module):
    sys.path.insert(0, str(outcome.out_dir))
    try:
        for name in (module, "pssc_rt"):
            sys.modules.pop(name, None)
        return (importlib.import_module(module),
                importlib.import_module("pssc_rt"))
    finally:
        sys.path.remove(str(outcome.out_dir))


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    p = tmp_path_factory.mktemp("entry") / "entry.pss"
    p.write_text(_MODEL)
    return str(p)


@pytest.fixture(scope="module")
def gen(src):
    with compile_op_model("op-model-py", sources=[src], root="pss_top",
                          export_actions=["pss_top::A", "B"]) as outcome:
        mod, rt = _load(outcome, "pss_top")
        yield mod, rt, outcome


def _ctx(path):
    import pssc
    from pssc.ast2ir import AstToIrTranslator
    p = pssc.Parser()
    p.parse([path])
    return AstToIrTranslator().translate(p.link())


# --- running an entry ------------------------------------------------------

def test_the_entry_runs_its_exec_body_against_the_component(gen):
    mod, rt, _ = gen
    bus = rt.MemoryBus()
    dut = mod.PssTop(bus)
    dut.A()
    assert [t for k, _, _, t in bus.log if k == "message"] == [
        "@@PSS-TRACE act pss_top::A", "@@PSS-TRACE chk ok"]
    assert (dut.total, dut.e.count) == (70, 7)


def test_an_operation_is_called_through_sub_components(gen):
    """`leaf.poke()` and `lanes[1].poke()` from inside `eng_c.run`."""
    mod, rt, _ = gen
    dut = mod.PssTop(rt.MemoryBus())
    dut.A()
    assert dut.e.leaf.hits == 7
    assert [lane.hits for lane in dut.e.lanes] == [0, 14]


def test_an_entry_takes_no_parameters_and_returns_nothing(gen):
    mod, rt, _ = gen
    dut = mod.PssTop(rt.MemoryBus())
    assert dut.B() is None
    assert dut.total == 1
    with pytest.raises(TypeError):
        dut.B(1)


def test_the_manifest_lists_entries_apart_from_operations(src, tmp_path):
    man = tmp_path / "m.json"
    with compile_op_model("op-model-py", sources=[src], root="pss_top",
                          export_actions=["pss_top::A"],
                          progseq_manifest=str(man)):
        doc = json.loads(man.read_text())
    top = next(c for c in doc["components"] if c["name"] == "pss_top")
    assert top["entries"] == [{"name": "A", "action": "pss_top::A"}]
    assert [op["name"] for op in top["operations"]] == ["add"]


def test_root_defaults_to_the_component_the_entry_runs_in(src, tmp_path):
    ns = argparse.Namespace(progseq_root=None, output_dir=str(tmp_path),
                            export_actions=["pss_top::A"])
    driver.compile([src], target="op-model-py", opts=ns)
    assert "def A(self):" in (tmp_path / "pss_top.py").read_text()


def test_the_ir_is_not_changed(src):
    """The component does not gain a function it never declared."""
    ctx = _ctx(src)
    top = ctx.type_map["pss_top"]
    before = [f.name for f in top.functions]
    ep = entry_point(ctx, "pss_top::A")
    assert [f.name for f in top.functions] == before
    assert ep.function not in top.functions
    # The action's own statements still reach the component through `comp`.
    body = next(f for f in ep.action_dtype.functions if f.name == "body")
    assert "attr='comp'" in repr(body.body)
    assert "attr='comp'" not in repr(ep.function.body)


# --- what an entry cannot be -----------------------------------------------

def _refused(tmp_path, pss, action="pss_top::X", match=None):
    p = tmp_path / "m.pss"
    p.write_text("import std_pkg::*;\n" + pss)
    with pytest.raises(ExportActionError, match=match):
        entry_point(_ctx(str(p)), action)


def test_an_action_with_an_activity_is_refused(tmp_path):
    _refused(tmp_path, """
component pss_top {
  action Y { exec body { } }
  action X { activity { do Y; } }
}""", match="has an activity")


def test_rand_attributes_are_refused(tmp_path):
    _refused(tmp_path, """
component pss_top {
  action X { rand bit[4] v; exec body { } }
}""", match=r"rand attributes \(v\)")


def test_reading_an_action_attribute_is_refused(tmp_path):
    _refused(tmp_path, """
component pss_top {
  target function void f(int v) { }
  action X { int v = 3; exec body { comp.f(v); } }
}""", match="reads its attribute 'v'")


def test_a_solve_exec_is_refused(tmp_path):
    _refused(tmp_path, """
component pss_top {
  int n;
  action X { exec pre_solve { } exec post_solve { comp.n = 1; } exec body { } }
}""", match="post_solve")


def test_a_name_clash_with_a_component_function_is_refused(src):
    """Both would be the same member of the generated class. The parser already
    refuses a function and an action of one name in one scope, so this guards
    a component whose functions come from elsewhere (an extension, a future
    front-end change) and is driven directly."""
    import zuspec.ir.core as ir
    ctx = _ctx(src)
    ctx.type_map["pss_top"].functions.append(
        ir.Function(name="B", args=ir.Arguments(args=[]), body=[]))
    with pytest.raises(ExportActionError, match="already has a function named 'B'"):
        entry_point(ctx, "pss_top::B")


def test_an_unknown_action_names_the_candidates(tmp_path):
    _refused(tmp_path, """
component pss_top { action A { exec body { } } }""", action="Nope",
             match=r"actions: pss_top::A")


def test_a_backend_that_does_not_render_entries_says_so(src, tmp_path):
    """Refused rather than an API generated without the entry."""
    ns = argparse.Namespace(progseq_root="pss_top", output_dir=str(tmp_path),
                            export_actions=["pss_top::A"])
    with pytest.raises(ValueError, match="does not yet render exported actions"):
        driver.compile([src], target="op-model-c", opts=ns)
