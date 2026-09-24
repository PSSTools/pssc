"""Package-scope functions in op-model-py (`targets/pkg_functions.py`).

A function declared outside any component is computation the model carries: it
becomes a module-level function of the generated module, passed the calling
component so it reaches the platform the way an operation does.

Driven, not grepped, where the claim is behaviour.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys

import pytest

from pssc import driver
from pssc.driver import CompileError
from pssc.testing import compile_op_model

_MODEL = """
import std_pkg::*;
import addr_reg_pkg::*;

function int add1(int x) { return x + 1; }
function int fact(int n) {
  if (n <= 1) { return 1; }
  return n * fact(n - 1);
}
function int scaled(int x, int k = 3) { return x * k; }
function void say(int v) { message(NONE, "say %d", v); }
function int twice(int x) { return 2 * x; }
function int unused(int x) { return x; }

package util {
  function int neg(int x) { return -x; }
}

component pss_top {
  int last;

  // Shadows the package `twice` inside this component (LRM 22.2).
  target function int twice(int x) { return 100 + x; }

  target function void run(int v) {
    last = fact(v) + add1(v) + scaled(v) + scaled(v, 10) + util::neg(v);
    say(twice(v));
  }
}
"""


def _load(out_dir, module):
    sys.path.insert(0, str(out_dir))
    try:
        for name in (module, "pssc_rt", "pssc_rt_async"):
            sys.modules.pop(name, None)
        return (importlib.import_module(module),
                importlib.import_module("pssc_rt"))
    finally:
        sys.path.remove(str(out_dir))


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    src = tmp_path_factory.mktemp("pkgfn") / "m.pss"
    src.write_text(_MODEL)
    with compile_op_model("op-model-py", sources=[str(src)],
                          root="pss_top") as outcome:
        mod, rt = _load(outcome.out_dir, "pss_top")
        yield mod, rt, outcome.read("pss_top.py")


def _src(tmp_path, pss):
    p = tmp_path / "m.pss"
    p.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n" + pss)
    return str(p)


def _compile(tmp_path, pss, target="op-model-py", **kw):
    ns = argparse.Namespace(progseq_root="pss_top",
                            output_dir=str(tmp_path / "out"), **kw)
    driver.compile([_src(tmp_path, pss)], target=target, opts=ns)
    return tmp_path / "out"


def test_package_functions_run_as_the_pss_says(gen):
    mod, rt, _ = gen
    bus = rt.MemoryBus()
    dut = mod.PssTop(bus)
    dut.run(4)
    # fact(4) + add1(4) + scaled(4) + scaled(4, 10) + util::neg(4)
    assert dut.last == 24 + 5 + 12 + 40 - 4
    # `twice` in the component body is the component's own function.
    assert [t for k, _, _, t in bus.log if k == "message"] == ["say 104"]


def test_each_function_is_generated_once_and_only_if_called(gen):
    _, _, text = gen
    for name in ("add1", "fact", "scaled", "say", "util__neg"):
        assert text.count(f"\ndef {name}(self") == 1, name
    assert "def unused(" not in text
    # The package `twice` is shadowed everywhere it is called from.
    assert "\ndef twice(" not in text


def test_a_default_argument_is_a_python_default(gen):
    _, _, text = gen
    assert "def scaled(self, x, k=3):" in text


def test_a_model_calling_none_has_no_functions_section(tmp_path):
    out = _compile(tmp_path, """
function int f(int x) { return x; }
component pss_top { target function void g() { } }""")
    assert "Package functions" not in (out / "pss_top.py").read_text()


def test_a_package_function_calls_the_package_function_not_the_components(
        tmp_path):
    """Inside a package function there is no component, so `twice` there is
    the package's -- even though the calling component has its own."""
    out = _compile(tmp_path, """
function int twice(int x) { return 2 * x; }
function int quad(int x) { return twice(twice(x)); }
component pss_top {
  int r;
  target function int twice(int x) { return 100 + x; }
  target function void g() { r = quad(1); }
}""")
    mod, rt = _load(out, "pss_top")
    dut = mod.PssTop(rt.MemoryBus())
    dut.g()
    assert dut.r == 4


# --- contexts (LRM 22.2.3) ---------------------------------------------------

def _gate_errors(tmp_path, pss, target="op-model-py"):
    with pytest.raises(CompileError) as ei:
        _compile(tmp_path, pss, target=target)
    return "\n".join(str(x) for x in ei.value.errors)


def test_a_target_function_is_refused_in_a_constructor(tmp_path):
    msg = _gate_errors(tmp_path, """
target function int f(int x) { return x; }
component pss_top {
  int n;
  solve function void initialize() { n = f(1); }
  target function void g() { }
}""")
    assert "'f' is a target function" in msg


def test_an_unqualified_function_is_checked_where_it_is_called(tmp_path):
    """Legal from an operation; its `write32` is refused from a constructor,
    and the diagnostic names the package function it is in."""
    ok = """
function void poke(addr_handle_t h) { write32(h, 1); }
component pss_top {
  target function void g(addr_handle_t h) { poke(h); }
}"""
    _compile(tmp_path, ok)
    msg = _gate_errors(tmp_path, """
function void poke(addr_handle_t h) { write32(h, 1); }
component pss_top {
  solve function void initialize(addr_handle_t h) { poke(h); }
  target function void g() { }
}""")
    assert "poke: cannot lower call: 'write32'" in msg


def test_a_backend_that_does_not_lower_them_names_the_function(tmp_path):
    msg = _gate_errors(tmp_path, """
function int f(int x) { return x; }
component pss_top {
  int r;
  target function void g() { r = f(1); }
}""", target="op-model-c")
    assert "'f' is a package-scope function, which 'op-model-c' does not " \
           "lower yet" in msg


# --- the async form ----------------------------------------------------------

_ASYNC = """
function int inc(int x) { return x + 1; }
function bit[32] peek(addr_handle_t h) { return read32(h); }
function bit[32] peek2(addr_handle_t h) { return peek(h) + inc(0); }
target function void tick() { }
component pss_top {
  bit[32] r;
  target function void g(addr_handle_t h) {
    tick();
    r = peek2(h) + inc(1);
  }
}"""


def test_async_colours_what_awaits_and_leaves_the_rest_plain(tmp_path):
    out = _compile(tmp_path, _ASYNC, py_await="async")
    text = (out / "pss_top.py").read_text()
    assert "\ndef inc(self, x):" in text
    # Reaches the platform directly, and through a call.
    assert "\nasync def peek(self, h):" in text
    assert "\nasync def peek2(self, h):" in text
    # A `target function` is coloured whatever its body does.
    assert "\nasync def tick(self):" in text
    assert "await tick(self)" in text


def test_the_async_form_runs(tmp_path):
    out = _compile(tmp_path, _ASYNC, py_await="async")
    sys.path.insert(0, str(out))
    try:
        for name in ("pss_top", "pssc_rt", "pssc_rt_async"):
            sys.modules.pop(name, None)
        mod = importlib.import_module("pss_top")
        rt = importlib.import_module("pssc_rt_async")
    finally:
        sys.path.remove(str(out))
    bus = rt.AsyncMemoryBus()
    dut = mod.PssTop(bus)

    async def go():
        await bus.write32(0x100, 41)
        await dut.g(0x100)
    asyncio.run(go())
    assert dut.r == 41 + 1 + 2
