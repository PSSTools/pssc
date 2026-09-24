"""`exec init_down` / `init_up` in op-model-py (LRM 20.1.2, 20.1.3).

The blocks run once, as part of building the component tree: the root's
constructor builds the tree, then runs `_pss_init`, which is each component's
init_down, then its sub-components' `_pss_init`, then its init_up. They are
solve context, like the constructor's body.

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


def _src(tmp_path, pss):
    p = tmp_path / "m.pss"
    p.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n" + pss)
    return str(p)


def _compile(tmp_path, pss, target="op-model-py", **kw):
    ns = argparse.Namespace(progseq_root="pss_top",
                            output_dir=str(tmp_path / "out"), **kw)
    driver.compile([_src(tmp_path, pss)], target=target, opts=ns)
    return tmp_path / "out"


def _load(out_dir, rt="pssc_rt"):
    sys.path.insert(0, str(out_dir))
    try:
        for name in ("pss_top", "pssc_rt", "pssc_rt_async"):
            sys.modules.pop(name, None)
        return (importlib.import_module("pss_top"),
                importlib.import_module(rt))
    finally:
        sys.path.remove(str(out_dir))


def _messages(bus):
    return [t for k, _, _, t in bus.log if k == "message"]


def _gate_errors(tmp_path, pss, target="op-model-py"):
    with pytest.raises(CompileError) as ei:
        _compile(tmp_path, pss, target=target)
    return "\n".join(str(x) for x in ei.value.errors)


# --- order -------------------------------------------------------------------

_ORDER = """
component leaf_c {
  int id;
  exec init_down { message(NONE, "down leaf"); }
  exec init_up   { message(NONE, "up leaf"); }
}
component mid_c {
  leaf_c l;
  exec init_down { message(NONE, "down mid"); }
  exec init_up   { message(NONE, "up mid"); }
}
component quiet_c {
  int q = 7;
}
component pss_top {
  mid_c   c1;
  quiet_c qq;
  leaf_c  arr[2];
  mid_c   c2;
  exec init_down { message(NONE, "down top"); }
  exec init_up   { message(NONE, "up top"); }
  target function void g() { }
}"""


def test_parents_go_down_first_and_up_last(tmp_path):
    """20.1.2: a parent's init_down before any child's; its init_up after
    every child's. Walked depth first, siblings in declaration order."""
    mod, rt = _load(_compile(tmp_path, _ORDER))
    bus = rt.MemoryBus()
    mod.PssTop(bus)
    assert _messages(bus) == [
        "down top",
        "down mid", "down leaf", "up leaf", "up mid",         # c1
        "down leaf", "up leaf", "down leaf", "up leaf",       # arr[0], arr[1]
        "down mid", "down leaf", "up leaf", "up mid",         # c2
        "up top",
    ]


def test_a_subtree_with_no_init_blocks_gets_no_pass(tmp_path):
    text = (_compile(tmp_path, _ORDER) / "pss_top.py").read_text()
    quiet = text[text.index("class Quiet"):]
    quiet = quiet[:quiet.index("\nclass ")]
    assert "_pss_init" not in quiet
    assert "self.qq._pss_init()" not in text


def test_a_model_without_init_blocks_is_unchanged(tmp_path):
    text = (_compile(tmp_path, """
component pss_top { int a = 1; target function void g() { } }""")
            / "pss_top.py").read_text()
    assert "_pss_init" not in text


# --- what the blocks do ------------------------------------------------------

def test_the_last_writer_wins(tmp_path):
    """Example 280's shape: a parent's init_down is overridden by the child's
    init_down, which the parent's init_up then overrides again."""
    mod, rt = _load(_compile(tmp_path, """
component sub_c {
  int a = 1000;
  int b;
  int c;
  exec init_down { a = 10; b = 20; }
  exec init_up   { c = a + b; }
}
component pss_top {
  sub_c s;
  int   t;
  exec init_down { s.a = 1; s.b = 2; }
  exec init_up   { s.b = 200; t = s.c + 1; }
  target function void g() { }
}"""))
    dut = mod.PssTop(rt.MemoryBus())
    assert (dut.s.a, dut.s.b, dut.s.c, dut.t) == (10, 200, 30, 31)


def test_several_blocks_of_one_kind_run_as_one_in_source_order(tmp_path):
    """20.1 d: including a block an `extend` adds, which comes after the
    initial definition's (19.x ordering of extensions)."""
    mod, rt = _load(_compile(tmp_path, """
component pss_top {
  int v = 1;
  exec init_down { v = v * 10; }
  exec init_up   { v = v + 3; }
  exec init_down { v = v + 2; }
  target function void g() { }
}
extend component pss_top {
  exec init_down { v = v * 100; }
}"""))
    assert mod.PssTop(rt.MemoryBus()).v == ((1 * 10 + 2) * 100) + 3


def test_the_root_constructor_runs_before_the_blocks(tmp_path):
    """The constructor builds; the blocks then initialize what it built --
    including a sub-component an init_down constructs."""
    mod, rt = _load(_compile(tmp_path, """
component ch_c {
  int id;
  int k = 5;
  solve function void initialize(int i) { id = i; }
  exec init_up { k = k + id; }
}
component pss_top {
  int n;
  ch_c ch[2];
  solve function void initialize(int v) { n = v; }
  exec init_down {
    foreach (ch[i]) { ch[i].initialize(n + i); }
  }
  target function void g() { }
}"""))
    dut = mod.PssTop(rt.MemoryBus(), 40)
    assert [(c.id, c.k) for c in dut.ch] == [(40, 45), (41, 46)]


def test_a_package_function_may_be_called(tmp_path):
    mod, rt = _load(_compile(tmp_path, """
function int sq(int x) { return x * x; }
component pss_top {
  int v;
  exec init_down { v = sq(9); }
  target function void g() { }
}"""))
    assert mod.PssTop(rt.MemoryBus()).v == 81


def test_the_async_form_initializes_synchronously(tmp_path):
    """A constructor cannot await, and a solve-context body has nothing to."""
    out = _compile(tmp_path, """
component pss_top {
  int v;
  exec init_down { v = 3; }
  target function void g() { v = v + 1; }
}""", py_await="async")
    text = (out / "pss_top.py").read_text()
    assert "    def _pss_init(self):" in text
    mod, rt = _load(out, "pssc_rt_async")
    dut = mod.PssTop(rt.AsyncMemoryBus())
    assert dut.v == 3
    asyncio.run(dut.g())
    assert dut.v == 4


# --- solve context (20.1.2, 22.2.3) -----------------------------------------

def test_a_target_function_is_refused(tmp_path):
    msg = _gate_errors(tmp_path, """
component pss_top {
  int v;
  target function int f() { return 1; }
  exec init_down { v = f(); }
  target function void g() { }
}""")
    assert "pss_top::init_down: cannot lower call: 'f'" in msg


def test_a_platform_access_is_refused(tmp_path):
    msg = _gate_errors(tmp_path, """
component pss_top {
  addr_handle_t h;
  exec init_down { write32(h, 1); }
  target function void g() { }
}""")
    assert "pss_top::init_down: cannot lower call: 'write32'" in msg


# --- what is refused, rather than dropped ------------------------------------

def test_a_backend_that_does_not_run_them_refuses_them(tmp_path):
    msg = _gate_errors(tmp_path, """
component sub_c {
  int a;
  exec init_up { a = 1; }
}
component pss_top {
  sub_c s;
  target function void g() { }
}""", target="op-model-c")
    assert "sub_c: `exec init_up` is not lowered by 'op-model-c' yet" in msg


# --- inheritance (LRM 17.1 Table 27: exec blocks shadow per kind) -------------

_INHERIT = """
component base_c {
  int a;
  exec init_down { a = 1; message(NONE, "base down"); }
  exec init_up   { message(NONE, "base up"); }
}
component der_c : base_c {
  int b;
  exec init_up { b = a + 1; message(NONE, "der up"); }
}
component pss_top {
  der_c d;
  base_c bb;
  target function void g() { message(NONE, "%d %d %d", d.a, d.b, bb.a); }
}"""


def test_an_inherited_block_runs_and_a_declared_kind_shadows(tmp_path):
    """der_c declares no init_down, so base_c's runs in it; it declares an
    init_up, which REPLACES base_c's."""
    mod, rt = _load(_compile(tmp_path, _INHERIT))
    bus = rt.MemoryBus()
    mod.PssTop(bus).g()
    assert _messages(bus) == ["base down", "der up",
                              "base down", "base up", "1 2 1"]


def test_super_in_a_derived_init_block_runs_the_bases(tmp_path):
    """`super;` runs base_c's init_up at that point (20.1.4.2)."""
    mod, rt = _load(_compile(tmp_path, _INHERIT.replace(
        'exec init_up { b = a + 1;', 'exec init_up { super; b = a + 1;')))
    bus = rt.MemoryBus()
    mod.PssTop(bus).g()
    assert _messages(bus) == ["base down", "base up", "der up",
                              "base down", "base up", "1 2 1"]


def test_every_base_block_of_the_kind_runs_and_a_missing_one_is_nothing(
        tmp_path):
    """Two base init_down blocks run as one (20.1 d), in order; the base
    declares no init_up, so `super;` in der_c's runs nothing."""
    mod, rt = _load(_compile(tmp_path, """
component base_c {
  exec init_down { message(NONE, "b1"); }
  exec init_down { message(NONE, "b2"); }
}
component der_c : base_c {
  exec init_down { super; message(NONE, "d"); }
  exec init_up { super; message(NONE, "u"); }
}
component pss_top { der_c d; target function void g() { } }"""))
    bus = rt.MemoryBus()
    mod.PssTop(bus)
    assert _messages(bus) == ["b1", "b2", "d", "u"]


def test_super_over_a_library_base_is_nothing(tmp_path):
    """An executor's base, `executor_c<>`, declares no exec blocks."""
    mod, rt = _load(_compile(tmp_path, """
import executor_pkg::*;
component x_c : executor_c<> {
  exec init_down { super; message(NONE, "x"); }
}
component pss_top { x_c x; target function void g() { } }"""))
    bus = rt.MemoryBus()
    mod.PssTop(bus)
    assert _messages(bus) == ["x"]
