"""Component inheritance in an operation model (LRM 17.1, Table 27).

A derived component includes everything its base declares, and its functions
are VIRTUAL: a base function calling `f()` runs the derived component's `f`.
Every op-model target used to drop what a component inherits -- fields,
sub-components, register blocks, functions -- without a word, because each
walks a component's own members. `targets/comp_inherit.py` completes a derived
component with its base's members; these tests hold the generated model to it.

Driven, not grepped: the generated module is imported and called.
"""
from __future__ import annotations

import argparse
import importlib
import sys

import pytest

from pssc import driver


def _compile(tmp_path, pss, target="op-model-py", **kw):
    p = tmp_path / "m.pss"
    p.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n" + pss)
    out = tmp_path / f"out_{target}"
    driver.compile([str(p)], target=target, opts=argparse.Namespace(
        progseq_root="pss_top", output_dir=str(out), **kw))
    return out


def _load(out):
    sys.path.insert(0, str(out))
    try:
        for name in ("pss_top", "pssc_rt"):
            sys.modules.pop(name, None)
        return (importlib.import_module("pss_top"),
                importlib.import_module("pssc_rt"))
    finally:
        sys.path.remove(str(out))


#: Targets that render inheritance by FLATTENING it (`comp_inherit`): a
#: derived component is its base's members written into it. The others render
#: it natively (`native_inheritance`) and are held to running the same.
_FLATTENED = ["op-model-c", "op-model-sv"]


def _messages(bus):
    return [t for k, _, _, t in bus.log if k == "message"]


_VIRTUAL = """
component leaf_c { target function void ping() { message(NONE, "ping"); } }
component base_c {
  int a = 5;
  leaf_c lf;
  target function int f(int x) { return x + a; }
  target function int h(int x) { return f(x) + 100; }
}
component mid_c : base_c {
  int b = 7;
  target function int f(int x) { return x * 10 + b; }
}
component leafmost_c : mid_c { }
component pss_top {
  base_c bb;
  mid_c m;
  leafmost_c l;
  target function void g() {
    message(NONE, "%d %d %d", bb.h(1), m.h(1), l.h(1));
    message(NONE, "%d %d %d", m.a, m.b, l.b);
    m.lf.ping();
    l.lf.ping();
  }
}
"""


def test_a_base_function_runs_the_derived_override(tmp_path):
    """bb.h -> base_c::f; m.h and l.h -> mid_c::f, two levels down too."""
    mod, rt = _load(_compile(tmp_path, _VIRTUAL))
    bus = rt.MemoryBus()
    mod.PssTop(bus).g()
    assert _messages(bus) == ["106 117 117", "5 7 7", "ping", "ping"]


def test_a_derived_root_exports_what_it_inherits(tmp_path):
    mod, rt = _load(_compile(tmp_path, """
component base_c {
  target function int f(int x) { return x + 1; }
  target function int h(int x) { return f(x) * 2; }
}
component pss_top : base_c {
  target function int f(int x) { return x + 1000; }
}"""))
    dut = mod.PssTop(rt.MemoryBus())
    assert (dut.f(1), dut.h(1)) == (1001, 2002)


def test_an_inherited_register_block_is_at_its_address(tmp_path):
    """The register block and the constructor that places it are both the
    base's; the derived root's accesses land where the base's would."""
    mod, rt = _load(_compile(tmp_path, """
pure component regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTRL;
  reg_c<bit[32], READWRITE, 32> STAT;
  function bit[64] get_offset_of_instance(string name) {
    match (name) {
      ["CTRL"]: return 0x0;
      ["STAT"]: return 0x8;
    }
    return 0;
  }
}
component base_c {
  regs_c regs;
  solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
  target function void kick() { regs.CTRL.write_val(regs.STAT.read_val() | 1); }
}
component pss_top : base_c { }"""))
    bus = rt.MemoryBus()
    bus.mem[0x108] = 0x40
    mod.PssTop(bus, 0x100).kick()
    assert [(k, a, d) for k, _, a, d in bus.log] == [
        ("read", 0x108, 0x40), ("write", 0x100, 0x41)]


def test_a_shadowing_function_with_another_signature_is_allowed_uncalled(
        tmp_path):
    """LRM Example 284's shape: der_c::scale() shadows base_c::scale(int),
    and nothing it inherits calls `scale`, so nothing is ambiguous."""
    mod, rt = _load(_compile(tmp_path, """
component base_c { target function int scale(int v) { return v * 2; } }
component pss_top : base_c { target function int scale() { return 9; } }"""))
    assert mod.PssTop(rt.MemoryBus()).scale() == 9


@pytest.mark.parametrize("model, msg", [
    ("""
component leaf_c { }
component base_c { leaf_c s; }
component der_c : base_c { leaf_c s; }
component pss_top { der_c d; target function void g() { } }""",
     "shadowing the component instance"),
    ("""
component base_c { int a; }
component der_c : base_c {
  target function int f(int a) { return a + super.a; }
}
component pss_top { der_c d; target function void g() { } }""",
     "has a parameter 'a'"),
    ("""
component base_c {
  target function int f(int x) { return x; }
  target function int h() { return f(1); }
}
component der_c : base_c { target function int f() { return 0; } }
component pss_top { der_c d; target function void g() { } }""",
     "with a signature that differs from 'base_c::f'"),
])
def test_what_cannot_be_inherited_is_refused(tmp_path, model, msg):
    with pytest.raises(Exception) as ei:
        _compile(tmp_path, model)
    assert msg in str(ei.value)


def test_a_refused_component_outside_the_tree_is_not_reported(tmp_path):
    """The model may declare what it never instantiates."""
    mod, rt = _load(_compile(tmp_path, """
component leaf_c { }
component base_c { leaf_c s; }
component der_c : base_c { leaf_c s; }
component pss_top { target function int g() { return 3; } }"""))
    assert mod.PssTop(rt.MemoryBus()).g() == 3


@pytest.mark.parametrize("target", _FLATTENED)
def test_inheriting_generates_what_writing_it_out_does(tmp_path, target):
    """For every op-model target, a derived component is the component with
    its base's members written into it -- base members first."""
    derived = """
component base_c {
  int a = 5;
  target function int f(int x) { return x + a; }
  target function int h(int x) { return f(x) + 100; }
}
component pss_top : base_c {
  int b = 7;
  target function int f(int x) { return x * 10 + b; }
}"""
    written = """
component pss_top {
  int a = 5;
  int b = 7;
  target function int h(int x) { return f(x) + 100; }
  target function int f(int x) { return x * 10 + b; }
}"""
    (tmp_path / "d").mkdir()
    (tmp_path / "w").mkdir()

    def texts(out):
        return {f.name: f.read_text() for f in sorted(out.iterdir())
                if f.is_file()}

    assert texts(_compile(tmp_path / "d", derived, target)) == \
        texts(_compile(tmp_path / "w", written, target))


# --- super and shadowing ----------------------------------------------------------

_SUPER = """
component base_c {
  int a = 5;
  target function int f(int x) { return x + a + g(); }
  target function int g() { return 1; }
  target function int peek() { return a; }
}
component der_c : base_c {
  int a = 100;
  target function int f(int x) { return super.f(x) * 1000 + a + super.a; }
  target function int g() { return 2; }
  target function void bump() { super.a += 1; a += 10; }
}
component third_c : der_c {
  target function int f(int x) { return super.f(x) + 7; }
}
component pss_top {
  base_c b;
  der_c d;
  third_c t;
  target function void run() {
    message(NONE, "%d %d %d", b.f(1), d.f(1), t.f(1));
    d.bump();
    message(NONE, "%d %d %d", d.a, d.peek(), d.f(0));
  }
}
"""


def test_super_calls_the_bases_function_statically(tmp_path):
    """super.f is base_c::f, even in third_c where `f` is third_c's; inside
    it, g() is still virtual (der_c::g), and base_c's own `a` is read.

    d.f(1) = (1 + 5 + 2) * 1000 + 100 + 5; t.f(1) adds 7 more.
    """
    mod, rt = _load(_compile(tmp_path, _SUPER))
    bus = rt.MemoryBus()
    mod.PssTop(bus).run()
    assert _messages(bus)[0] == "7 8105 8112"


def test_a_shadowed_field_is_two_fields(tmp_path):
    """der_c's `a` and base_c's `a` are separate (17.1): base_c's functions,
    and `super.a`, read base_c's; everything else reads der_c's."""
    mod, rt = _load(_compile(tmp_path, _SUPER))
    bus = rt.MemoryBus()
    mod.PssTop(bus).run()
    # after bump: der a = 110, base a = 6; f(0) = (0 + 6 + 2)*1000 + 110 + 6
    assert _messages(bus)[1] == "110 6 8116"


@pytest.mark.parametrize("target", _FLATTENED)
def test_super_and_shadowing_generate_what_writing_them_out_does(tmp_path,
                                                                  target):
    """The base's `f` and `a` become private members of the derived
    component; nothing else about it changes, on any op-model target."""
    derived = """
component base_c {
  int a = 5;
  target function int f(int x) { return x + a + g(); }
  target function int g() { return 1; }
}
component pss_top : base_c {
  int a = 100;
  target function int f(int x) { return super.f(x) * 1000 + a + super.a; }
  target function int g() { return 2; }
}"""
    written = """
component pss_top {
  int _pss_super_base_c_a = 5;
  int a = 100;
  target function int _pss_super_base_c_f(int x) {
    return x + _pss_super_base_c_a + g();
  }
  target function int f(int x) {
    return _pss_super_base_c_f(x) * 1000 + a + _pss_super_base_c_a;
  }
  target function int g() { return 2; }
}"""
    (tmp_path / "d").mkdir()
    (tmp_path / "w").mkdir()

    def texts(out):
        return {f.name: f.read_text() for f in sorted(out.iterdir())
                if f.is_file()}

    assert texts(_compile(tmp_path / "d", derived, target)) == \
        texts(_compile(tmp_path / "w", written, target))


def test_a_parameter_named_like_a_shadowed_field_is_the_parameter(tmp_path):
    """In a base body, `a` the parameter is not redirected to the base's
    field: the front end spells both `self.a`, and the parameter wins."""
    mod, rt = _load(_compile(tmp_path, """
component base_c {
  int a = 5;
  target function int f(int a) { return a * 2; }
  target function int h() { return f(a); }
}
component pss_top : base_c { int a = 100; }"""))
    dut = mod.PssTop(rt.MemoryBus())
    assert (dut.f(3), dut.h(), dut.a) == (6, 10, 100)
