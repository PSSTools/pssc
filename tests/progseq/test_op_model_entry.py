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

from . import trace_harness as th

_MODEL = """
import std_pkg::*;

component leaf_c {
  target function void poke(int v) { message(NONE, "poke %d", v); }
}

component eng_c {
  leaf_c leaf;
  leaf_c lanes[2];
  target function int run(int v) {
    leaf.poke(v);
    lanes[1].poke(2 * v);
    return v * 10;
  }
}

component pss_top {
  eng_c e;

  target function int add(int a, int b) { return a + b; }

  action A {
    exec body {
      message(NONE, "@@PSS-TRACE act pss_top::A");
      int r1 = comp.e.run(3);
      int r2 = comp.e.run(4);
      if (comp.add(r1, r2) == 70) {
        message(NONE, "@@PSS-TRACE chk ok");
      }
    }
  }

  action B {
    exec body {
      message(NONE, "B %d", comp.add(1, 0));
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
    msgs = [t for k, _, _, t in bus.log if k == "message"]
    assert [m for m in msgs if m.startswith("@@")] == [
        "@@PSS-TRACE act pss_top::A", "@@PSS-TRACE chk ok"]


def test_an_operation_is_called_through_sub_components(gen):
    """`leaf.poke()` and `lanes[1].poke()` from inside `eng_c.run`."""
    mod, rt, _ = gen
    bus = rt.MemoryBus()
    mod.PssTop(bus).A()
    assert [t for k, _, _, t in bus.log
            if k == "message" and t.startswith("poke")] == [
        "poke 3", "poke 6", "poke 4", "poke 8"]


def test_an_entry_takes_no_parameters_and_returns_nothing(gen):
    mod, rt, _ = gen
    bus = rt.MemoryBus()
    dut = mod.PssTop(bus)
    assert dut.B() is None
    assert [t for k, _, _, t in bus.log if k == "message"] == ["B 1"]
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
    with pytest.raises(driver.CompileError,
                       match="does not yet render exported actions"):
        driver.compile([src], target="op-model-c", opts=ns)


# --- a test action (`pssunit_pkg::test_a`) ---------------------------------

_MARKED = """\
import std_pkg::*;
package mark_pkg {
  abstract action test_a { }
  abstract action busy_a { int n; }
}
component dev_c {
  int base_addr;
  solve function void initialize(int b) { base_addr = b; }
  target function void go() { message(NONE, "go %d", base_addr); }
}
component pss_top {
  dev_c dev;
  function void bind_dev() { dev.initialize(7); }
  action t_a : mark_pkg::test_a {
    exec body { comp.bind_dev(); comp.dev.go(); }
  }
  action u_a : mark_pkg::busy_a { exec body { } }
}
"""


def test_a_test_action_derived_from_a_package_marker_exports(tmp_path):
    """`pssunit_pkg::test_a` is an empty action in a package: the chain ends
    there. A sub-component's constructor called from a target function (a
    fixture's setup binding its device) constructs it anew."""
    p = tmp_path / "m.pss"
    p.write_text(_MARKED)
    with compile_op_model("op-model-py", sources=[str(p)], root="pss_top",
                          export_actions=["pss_top::t_a"]) as outcome:
        mod, rt = _load(outcome, "pss_top")
        bus = rt.MemoryBus()
        mod.PssTop(bus).t_a()
    assert [t for k, _, _, t in bus.log if k == "message"] == ["go 7"]


def test_a_package_base_that_is_not_empty_is_a_user_error(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(_MARKED)
    ns = argparse.Namespace(progseq_root="pss_top", output_dir=str(tmp_path),
                            export_actions=["pss_top::u_a"])
    with pytest.raises(driver.CompileError,
                       match="inherits from 'mark_pkg::busy_a', which is not "
                             "an action of the model"):
        driver.compile([str(p)], target="op-model-py", opts=ns)


# --- action functions -------------------------------------------------------

_AF_MODEL = """\
import std_pkg::*;
component pss_top {
  target function int add(int a, int b) { return a + b; }
  abstract action base_a {
    function void arrange() { message(NONE, "arrange"); }
    function void act();
    function int twice(int v) { return comp.add(v, v); }
    exec body {
      arrange();
      act();
      message(NONE, "twice %d", twice(4));
    }
  }
  action d_a : base_a {
    function void arrange() { super.arrange(); message(NONE, "more"); }
    function void act() { message(NONE, "act %d", twice(2)); }
  }
  action r_a {
    function int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
    exec body { message(NONE, "fact %d", fact(5)); }
  }
}
"""


@pytest.fixture(scope="module")
def af_src(tmp_path_factory):
    p = tmp_path_factory.mktemp("af") / "af.pss"
    p.write_text(_AF_MODEL)
    return str(p)


@pytest.fixture(scope="module")
def af_gen(af_src):
    with compile_op_model("op-model-py", sources=[af_src], root="pss_top",
                          export_actions=["pss_top::d_a", "pss_top::r_a"]) as outcome:
        mod, rt = _load(outcome, "pss_top")
        yield mod, rt


def _messages(dut_call, mod, rt):
    bus = rt.MemoryBus()
    dut_call(mod.PssTop(bus))
    return [t for k, _, _, t in bus.log if k == "message"]


def test_a_template_method_runs_the_derived_actions_functions(af_gen):
    """The base `exec body` calls `arrange`/`act`; `d_a`'s overrides run,
    `super.arrange()` reaches the base, and a value function returns."""
    mod, rt = af_gen
    assert _messages(lambda d: d.d_a(), mod, rt) == [
        "arrange", "more", "act 4", "twice 8"]


def test_a_recursive_action_function_runs(af_gen):
    mod, rt = af_gen
    assert _messages(lambda d: d.r_a(), mod, rt) == ["fact 120"]


def test_action_functions_are_private_helpers_of_the_entry(af_src):
    ep = entry_point(_ctx(af_src), "pss_top::d_a")
    names = sorted(f.name for f in ep.helpers)
    assert names and all(n.startswith("_pss_af_d_a_") for n in names)
    for n in ("arrange", "act", "twice"):
        assert any(x.endswith("_" + n) for x in names), n


def test_an_action_function_reading_an_attribute_is_refused(tmp_path):
    _refused(tmp_path, """
component pss_top {
  target function void f(int v) { }
  action X {
    int v = 3;
    function void g() { comp.f(v); }
    exec body { g(); }
  }
}""", match="reads its attribute 'v'")


@pytest.mark.sim
@pytest.mark.skipif(th.available("sv") != "", reason=th.available("sv") or "-")
def test_action_functions_run_on_op_model_sv(tmp_path):
    """The same entries on op-model-sv: each action function a protected
    task, a value one returning through its `output status`."""
    pss = _AF_MODEL.replace("import std_pkg::*;\n", "").replace(
        "component pss_top {", "component pss_top {\n  target function void run() { }", 1)
    out = th.compile_model(tmp_path, pss, "sv",
                           export_actions=["pss_top::d_a", "pss_top::r_a"])
    sv = (out / "pss_top.svh").read_text()
    assert "protected task _pss_af_d_a_twice(output int status, input int v);" in sv
    tb = (th._SV_TB.replace("@PLAT@", "").replace("@PRELOAD@", "")
          .replace("@ARGS@", "")
          .replace("dut.run();", "dut.d_a(); dut.r_a();"))
    (out / "tb.sv").write_text(tb)
    build = th.Build(
        steps=[([th.VERILATOR, "--binary", "-j", "1", "-Wno-fatal",
                 "--top-module", "top", f"-I{out}", "-Mdir",
                 str(tmp_path / "obj"), "-o", "sim",
                 str(out / "pssc_reg_pkg.sv"), str(out / "pss_top_pkg.sv"),
                 str(out / "tb.sv")], str(tmp_path))],
        run=([str(tmp_path / "obj" / "sim")], str(tmp_path)),
        lines=th._sv_lines)
    assert th.finish(build) == [
        "arrange", "more", "act 4", "twice 8", "fact 120"]
