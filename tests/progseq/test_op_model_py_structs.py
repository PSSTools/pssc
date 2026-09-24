"""Struct values, and address regions built from them, in op-model-py.

A PSS struct is a VALUE: assignment copies it element by element (LRM 8.3),
while an aggregate PARAMETER is a handle to the caller's instance (20.3.2). A
Python object is a reference, so every one of these is a place the generated
code could silently share what PSS copies -- and each is driven here.

`add_region` hands back the start of a region (21.10.1.2); for a transparent
region that is its `addr` (21.10.3.3), which is all an operation model needs to
bind a register block to.
"""
from __future__ import annotations

import argparse
import importlib
import sys

import pytest

from pssc import driver
from pssc.driver import CompileError


def _compile(tmp_path, pss, target="op-model-py", **kw):
    src = tmp_path / "m.pss"
    src.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n" + pss)
    kw.setdefault("progseq_root", "pss_top")
    ns = argparse.Namespace(output_dir=str(tmp_path / "out"), **kw)
    driver.compile([str(src)], target=target, opts=ns)
    return tmp_path / "out"


def _load(out_dir):
    sys.path.insert(0, str(out_dir))
    try:
        for name in ("pss_top", "pssc_rt"):
            sys.modules.pop(name, None)
        return (importlib.import_module("pss_top"),
                importlib.import_module("pssc_rt"))
    finally:
        sys.path.remove(str(out_dir))


def _run(tmp_path, body, decls="", extra=""):
    mod, rt = _load(_compile(tmp_path, f"""
{decls}
component pss_top {{
  {extra}
  target function void g() {{
    {body}
  }}
}}"""))
    bus = rt.MemoryBus()
    dut = mod.PssTop(bus)
    dut.g()
    return [t for k, _, _, t in bus.log if k == "message"], dut


_DESC = """
enum mode_e { B = 2, A = 1, C = 0 };
struct csr_s {
  bit[12] tot_sz;
  bit[3]  prio = 5;
}
struct desc_s {
  csr_s   csr;
  bit[32] adr0 = 0xa0;
  bit[32] next;
  mode_e  mode;
  string  tag;
}
"""


# --- defaults ----------------------------------------------------------------

def test_a_struct_starts_at_its_declared_defaults(tmp_path):
    """Initializers, a nested struct, an enum's FIRST item (7.5 k: here 2,
    not 0), the empty string."""
    msgs, _ = _run(tmp_path, """
    desc_s d;
    message(NONE, "%u %u %u %u %d [%s]", d.csr.prio, d.adr0, d.next,
            d.csr.tot_sz, d.mode, d.tag);""", decls=_DESC)
    assert msgs == ["5 160 0 0 2 []"]


def test_an_enum_local_starts_at_its_first_item(tmp_path):
    msgs, _ = _run(tmp_path, """
    mode_e m;
    message(NONE, "%d", m);""", decls=_DESC)
    assert msgs == ["2"]


def test_a_struct_field_of_a_component_starts_at_its_defaults(tmp_path):
    _, dut = _run(tmp_path, "", decls=_DESC, extra="desc_s cfg;")
    assert (dut.cfg.csr.prio, dut.cfg.adr0, dut.cfg.mode) == (5, 0xa0, 2)


def test_each_element_of_a_struct_array_is_its_own_value(tmp_path):
    _, dut = _run(tmp_path, "arr[0].next = 7;", decls=_DESC,
                  extra="desc_s arr[3];")
    assert [d.next for d in dut.arr] == [7, 0, 0]


def test_a_one_field_struct(tmp_path):
    """`FIELDS = ("x")` would be a string, iterated a character at a time."""
    msgs, _ = _run(tmp_path, """
    one_s o; o.value = 3; one_s p = o;
    message(NONE, "%d %n", p.value, p == o);""",
                   decls="struct one_s { int value; }")
    assert msgs == ["3 true"]


# --- assignment copies (8.3) ---------------------------------------------------

def test_assignment_copies_including_nested_structs(tmp_path):
    msgs, _ = _run(tmp_path, """
    desc_s a; a.next = 0x33; a.csr.tot_sz = 7;
    desc_s d; d = a;
    d.next = 0; d.csr.tot_sz = 9;
    desc_s e = a;
    e.csr.prio = 1;
    message(NONE, "%u %u %u %u %u", a.next, a.csr.tot_sz, d.csr.tot_sz,
            a.csr.prio, e.csr.prio);""", decls=_DESC)
    assert msgs == ["51 7 9 5 1"]


def test_equality_is_by_value(tmp_path):
    msgs, _ = _run(tmp_path, """
    desc_s a; desc_s b;
    bool eq1 = a == b;
    b.csr.prio = 1;
    message(NONE, "%n %n %n", eq1, a == b, a != b);""", decls=_DESC)
    assert msgs == ["true false true"]


def test_a_field_is_not_aliased_by_a_local(tmp_path):
    msgs, dut = _run(tmp_path, """
    desc_s d = cfg;
    d.next = 9;
    cfg.adr0 = 1;
    message(NONE, "%u %u", cfg.next, d.adr0);""", decls=_DESC,
                     extra="desc_s cfg;")
    assert msgs == ["0 160"]
    assert dut.cfg.next == 0


def test_field_assignment_converts_to_the_field_type(tmp_path):
    msgs, _ = _run(tmp_path, """
    desc_s d; bit[16] w = 0x1abc; bit[8] p = 9;
    d.csr.tot_sz = w; d.csr.prio = p;
    message(NONE, "0x%x %u", d.csr.tot_sz, d.csr.prio);""", decls=_DESC)
    assert msgs == ["0xabc 1"]


# --- parameters are handles (20.3.2) --------------------------------------------

_PARAMS = """
struct p_s { int x; int y; }
function void set_x(p_s p, int v) { p.x = v; }
function void assign(p_s dst, p_s src) { dst = src; }
function p_s echo(p_s p) { return p; }
function p_s make(int v) { p_s s; s.x = v; return s; }
"""


def test_a_callee_updates_the_callers_struct(tmp_path):
    msgs, _ = _run(tmp_path, """
    p_s p; p_s q;
    set_x(p, 4);
    q.y = 8;
    assign(p, q);
    message(NONE, "%d %d", p.x, p.y);""", decls=_PARAMS)
    assert msgs == ["0 8"]


def test_assigning_into_a_parameter_does_not_alias_the_source(tmp_path):
    """`dst = src` copies INTO the caller's `dst`; a later change to `src`
    does not show through."""
    msgs, _ = _run(tmp_path, """
    p_s p; p_s q; q.y = 8;
    assign(p, q);
    q.y = 9;
    message(NONE, "%d", p.y);""", decls=_PARAMS)
    assert msgs == ["8"]


def test_returning_a_parameter_returns_a_copy(tmp_path):
    msgs, _ = _run(tmp_path, """
    p_s p; p.x = 1;
    p_s r = echo(p);
    r.x = 2;
    message(NONE, "%d %d", p.x, r.x);""", decls=_PARAMS)
    assert msgs == ["1 2"]


def test_returning_a_local_needs_no_copy(tmp_path):
    out = _compile(tmp_path, _PARAMS + """
component pss_top {
  target function void g() { p_s r = make(3); }
}""")
    text = (out / "pss_top.py").read_text()
    assert "    return s\n" in text
    assert "r = make(self, 3)" in text


def test_an_exported_action_uses_the_same_struct_class(tmp_path):
    """The action's local and the package function's parameter are one type
    (export_action.py used to copy the type along with the body)."""
    out = _compile(tmp_path, _PARAMS + """
component pss_top {
  action A {
    exec body {
      p_s p;
      set_x(p, 5);
      message(NONE, "%d", p.x);
    }
  }
}""", progseq_root=None, export_actions=["pss_top::A"])
    mod, rt = _load(out)
    bus = rt.MemoryBus()
    mod.PssTop(bus).A()
    assert [t for k, _, _, t in bus.log if k == "message"] == ["5"]
    assert (out / "pss_top.py").read_text().count("class p_s(") == 1


# --- inheritance ---------------------------------------------------------------

def test_a_derived_value_assigned_to_a_base_keeps_the_base_fields(tmp_path):
    """8.3: only the elements the left-hand type has are assigned."""
    msgs, _ = _run(tmp_path, """
    der_s d; d.a = 1; d.b = 2;
    base_s x; x = d;
    base_s y; y.a = 1;
    message(NONE, "%d %n", x.a, x == y);""",
                   decls="struct base_s { int a; }\n"
                         "struct der_s : base_s { int b; }")
    assert msgs == ["1 true"]


# --- foreach over structs ------------------------------------------------------

def test_a_foreach_iterator_aliases_its_element(tmp_path):
    """20.7.8 c: the iterator IS the element, so a member write lands in the
    array."""
    _, dut = _run(tmp_path, "foreach (e : arr) { e.next = 4; }",
                  decls=_DESC, extra="desc_s arr[2];")
    assert [d.next for d in dut.arr] == [4, 4]


# --- add_region ----------------------------------------------------------------

_SPACE = """
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
"""


def test_a_transparent_region_is_its_address(tmp_path):
    mod, rt = _load(_compile(tmp_path, _SPACE + """
component pss_top {
  transparent_addr_space_c<> sys_mem;
  regs_c regs;
  addr_handle_t ram;
  exec init_down {
    transparent_addr_region_s<> mmio;
    mmio.size = 0x1000;
    mmio.addr = 0x40000000;
    regs.set_handle(sys_mem.add_nonallocatable_region(mmio));
    transparent_addr_region_s<> r;
    r.size = 0x10000;
    r.addr = 0x80000000;
    ram = sys_mem.add_region(r);
  }
  target function void g() {
    regs.CTRL.write_val(5);
    write32(make_handle_from_handle(ram, 0x10), regs.STAT.read_val());
  }
}"""))
    bus = rt.MemoryBus()
    dut = mod.PssTop(bus)
    bus.mem[0x40000008] = 0x77
    dut.g()
    assert [(k, a, v) for k, _, a, v in bus.log] == [
        ("write", 0x40000000, 5), ("read", 0x40000008, 0x77),
        ("write", 0x80000010, 0x77)]


def test_a_region_without_a_stated_address_is_refused(tmp_path):
    with pytest.raises(ValueError, match="`transparent_addr_region_s`"):
        _compile(tmp_path, """
component pss_top {
  contiguous_addr_space_c<> sys_mem;
  addr_handle_t h;
  exec init_down {
    addr_region_s<> r;
    r.size = 0x1000;
    h = sys_mem.add_region(r);
  }
  target function void g() { }
}""")


def test_add_region_outside_an_init_block_is_refused(tmp_path):
    """Regions are part of the static hierarchy; add_region is solve-only."""
    with pytest.raises(CompileError) as ei:
        _compile(tmp_path, """
component pss_top {
  transparent_addr_space_c<> sys_mem;
  target function void g() {
    transparent_addr_region_s<> r;
    addr_handle_t h = sys_mem.add_region(r);
  }
}""")
    assert "add_region" in "\n".join(str(x) for x in ei.value.errors)


def test_other_op_model_targets_still_refuse_add_region(tmp_path):
    with pytest.raises(CompileError) as ei:
        _compile(tmp_path, """
component pss_top {
  transparent_addr_space_c<> sys_mem;
  addr_handle_t h;
  solve function void initialize() {
    transparent_addr_region_s<> r;
    h = sys_mem.add_region(r);
  }
  target function void g() { }
}""", target="op-model-c")
    assert "add_region" in "\n".join(str(x) for x in ei.value.errors)
