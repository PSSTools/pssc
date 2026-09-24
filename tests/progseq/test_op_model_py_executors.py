"""Executors in op-model-py: memory primitives delegated to overrides.

LRM 21.13.9.5: a primitive read or write -- and a register access, which is
defined in terms of one -- is delegated to the function of the same prototype
in the executor assigned to the calling action. A component names its executor
with `set_executor` in `init_down`/`init_up`, inherits its parent's otherwise,
and an executor is its own (21.7.2.6). With no executor anywhere above it, a
component's accesses go to the default implementation: the platform.

This is what the compliance suite's executor tap stands on (COMPLIANCE-DESIGN
§4.12), so every case is DRIVEN: the generated module is imported, run, and its
platform log read.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys

import pytest

from pssc import driver
from pssc.driver import CompileError


def _compile(tmp_path, pss, target="op-model-py", **kw):
    src = tmp_path / "m.pss"
    src.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n"
                   "import executor_pkg::*;\n" + pss)
    kw.setdefault("progseq_root", "pss_top")
    ns = argparse.Namespace(output_dir=str(tmp_path / "out"), **kw)
    driver.compile([str(src)], target=target, opts=ns)
    return tmp_path / "out"


def _load(out_dir, rt="pssc_rt"):
    sys.path.insert(0, str(out_dir))
    try:
        for name in ("pss_top", rt):
            sys.modules.pop(name, None)
        return (importlib.import_module("pss_top"),
                importlib.import_module(rt))
    finally:
        sys.path.remove(str(out_dir))


def _log(bus):
    """The platform's log: messages as text, accesses as tuples."""
    return [v if k == "message" else (k, a, v) for k, _, a, v in bus.log]


def _run(tmp_path, pss, *args, op="g", mem=None, **kw):
    mod, rt = _load(_compile(tmp_path, pss, **kw))
    bus = rt.MemoryBus()
    for a, v in (mem or {}).items():
        bus.mem[a] = v
    dut = mod.PssTop(bus)
    getattr(dut, op)(*args)
    return _log(bus), dut


def _errors(ei):
    return "\n".join(str(x) for x in ei.value.errors)


#: One byte per address (COMPLIANCE-DESIGN §4.12).
_F8 = """
function bit[8] f8(bit[64] a) {
  bit[64] x = a ^ (a >> 32);
  x = x ^ (x >> 16);
  x = x ^ (x >> 8);
  return (bit[8])(x ^ 0x5A);
}
"""

#: A tap that overrides the 32-bit primitives only.
_TAP = _F8 + """
component tap_c : executor_c<> {
  target function bit[32] read32(addr_handle_t h, mem_access_desc_s d = {}) {
    bit[64] a = addr_value(h);
    bit[32] v = ((bit[32])f8(a)) | (((bit[32])f8(a + 1)) << 8)
              | (((bit[32])f8(a + 2)) << 16) | (((bit[32])f8(a + 3)) << 24);
    message(NONE, "r32 0x%x 0x%x", a, v);
    return v;
  }
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    message(NONE, "w32 0x%x 0x%x", addr_value(h), data);
  }
}
"""


def f8(a):
    x = a ^ (a >> 32)
    x ^= x >> 16
    x ^= x >> 8
    return (x ^ 0x5A) & 0xFF


def f32(a):
    return sum(f8(a + i) << (8 * i) for i in range(4))


# --- delegation ----------------------------------------------------------------

def test_a_primitive_is_delegated_to_the_assigned_executor(tmp_path):
    """The read answers F8, the write is only logged: the platform is never
    touched."""
    log, _ = _run(tmp_path, _TAP + """
component pss_top {
  tap_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) {
    write32(h, read32(h) + 1);
  }
}""", 0xa0000100)
    v = f32(0xa0000100)
    assert log == [f"r32 0xa0000100 0x{v:x}",
                   f"w32 0xa0000100 0x{(v + 1) & 0xffffffff:x}"]


def test_with_no_executor_assigned_a_primitive_reaches_the_platform(tmp_path):
    """An executor that exists but is assigned to no one changes nothing."""
    log, _ = _run(tmp_path, _TAP + """
component pss_top {
  tap_c tap;
  target function void g(addr_handle_t h) {
    write32(h, read32(h) + 1);
  }
}""", 0x40, mem={0x40: 7})
    assert log == [("read", 0x40, 7), ("write", 0x40, 8)]


def test_a_primitive_the_executor_does_not_override_reaches_the_platform(
        tmp_path):
    log, _ = _run(tmp_path, _TAP + """
component pss_top {
  tap_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) {
    write8(h, 3);
    write32(h, 4);
  }
}""", 0x40)
    assert log == [("write", 0x40, 3), "w32 0x40 0x4"]


def test_a_register_access_is_delegated(tmp_path):
    """21.13.9.5: register read/write functions are implemented in terms of
    the primitives, so an override applies to them too."""
    log, _ = _run(tmp_path, _TAP + """
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
component pss_top {
  tap_c tap;
  regs_c regs;
  transparent_addr_space_c<> sys_mem;
  exec init_down {
    set_executor(tap);
    transparent_addr_region_s<> mmio;
    mmio.size = 0x1000;
    mmio.addr = 0x40000000;
    regs.set_handle(sys_mem.add_nonallocatable_region(mmio));
  }
  target function void g() {
    regs.CTRL.write_val(regs.STAT.read_val() | 1);
  }
}""")
    v = f32(0x40000008)
    assert log == [f"r32 0x40000008 0x{v:x}", f"w32 0x40000000 0x{v | 1:x}"]


# --- assignment over the tree (21.7.2.6) ----------------------------------------

_TREE = _TAP + """
component leaf_c {
  target function void g(addr_handle_t h) {
    write32(h, 1);
  }
}
component mid_c {
  leaf_c leaf;
  target function void g(addr_handle_t h) {
    write32(h, 2);
  }
}
"""


def _tree_log(tmp_path, top):
    mod, rt = _load(_compile(tmp_path, _TREE + top))
    bus = rt.MemoryBus()
    dut = mod.PssTop(bus)
    dut.mid.g(0x20)
    dut.mid.leaf.g(0x10)
    dut.g(0x30)
    return _log(bus)


def test_a_sub_component_inherits_its_parents_executor(tmp_path):
    assert _tree_log(tmp_path, """
component pss_top {
  tap_c tap;
  mid_c mid;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) { }
}""") == ["w32 0x20 0x2", "w32 0x10 0x1"]


def test_an_assignment_below_the_root_covers_only_that_subtree(tmp_path):
    """`mid` assigns its own; the root has none, so the root's accesses go to
    the platform."""
    assert _tree_log(tmp_path, """
component pss_top {
  mid_c mid;
  target function void g(addr_handle_t h) {
    write32(h, 3);
  }
}
extend component mid_c {
  tap_c tap;
  exec init_down { set_executor(tap); }
}""") == ["w32 0x20 0x2", "w32 0x10 0x1", ("write", 0x30, 3)]


def test_an_explicit_assignment_beats_the_inherited_one(tmp_path):
    """The root assigns `a`; `mid` assigns its own `b`; `leaf` inherits `b`."""
    log = _tree_log(tmp_path, """
component tag_c : executor_c<> {
  int tag;
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    message(NONE, "%d:0x%x", tag, addr_value(h));
  }
}
component pss_top {
  tag_c a;
  mid_c mid;
  exec init_down { a.tag = 1; set_executor(a); }
  target function void g(addr_handle_t h) {
    write32(h, 3);
  }
}
extend component mid_c {
  tag_c b;
  exec init_down { b.tag = 2; set_executor(b); }
}""")
    assert log == ["2:0x20", "2:0x10", "1:0x30"]


def test_an_assignment_in_init_up_reaches_the_children(tmp_path):
    """init_up runs after the children's init blocks, so the tree's assignment
    is resolved only once the whole pass is over."""
    assert _tree_log(tmp_path, """
component pss_top {
  tap_c tap;
  mid_c mid;
  exec init_up { set_executor(tap); }
  target function void g(addr_handle_t h) { }
}""") == ["w32 0x20 0x2", "w32 0x10 0x1"]


def test_a_later_set_executor_overrides_an_earlier_one(tmp_path):
    log, _ = _run(tmp_path, _TAP + """
component other_c : executor_c<> {
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    message(NONE, "other");
  }
}
component pss_top {
  tap_c tap;
  other_c other;
  exec init_down { set_executor(tap); }
  exec init_up { set_executor(other); }
  target function void g(addr_handle_t h) {
    write32(h, 1);
  }
}""", 0x10)
    assert log == ["other"]


def test_an_element_of_an_executor_array(tmp_path):
    log, _ = _run(tmp_path, """
component tag_c : executor_c<> {
  int tag;
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    message(NONE, "%d", tag);
  }
}
component pss_top {
  tag_c t[2];
  exec init_down { t[0].tag = 5; t[1].tag = 6; set_executor(t[1]); }
  target function void g(addr_handle_t h) {
    write32(h, 1);
  }
}""", 0x10)
    assert log == ["6"]


def test_an_executors_own_operations_use_itself(tmp_path):
    """21.7.2.6: an executor is its own executor, with no assignment."""
    mod, rt = _load(_compile(tmp_path, _TAP + """
extend component tap_c {
  target function void poke(addr_handle_t h) {
    write32(h, 9);
  }
}
component pss_top {
  tap_c tap;
  target function void g() { }
}"""))
    bus = rt.MemoryBus()
    mod.PssTop(bus).tap.poke(0x44)
    assert _log(bus) == ["w32 0x44 0x9"]


def test_a_package_function_uses_its_callers_executor(tmp_path):
    log, _ = _run(tmp_path, _TAP + """
function void put(addr_handle_t h, bit[32] v) {
  write32(h, v);
}
component pss_top {
  tap_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) { put(make_handle_from_handle(h, 0x10), 5); }
}""", 0x40)
    assert log == ["w32 0x50 0x5"]


# --- the default, and the descriptor --------------------------------------------

def test_super_in_an_override_reaches_the_default(tmp_path):
    """`super.write32` is the base's `write32`: the default implementation,
    which for an operation model is the platform."""
    log, _ = _run(tmp_path, """
component pass_c : executor_c<> {
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    message(NONE, "pass 0x%x", data);
    super.write32(h, data, d);
  }
}
component pss_top {
  pass_c x;
  exec init_down { set_executor(x); }
  target function void g(addr_handle_t h) {
    write32(h, 0x77);
  }
}""", 0x10)
    assert log == ["pass 0x77", ("write", 0x10, 0x77)]


def test_a_qualified_call_in_an_override_is_the_override_again(tmp_path):
    """`addr_reg_pkg::write32` is the same function as `write32`, delegated
    to the active executor (21.13.9.5) -- inside that executor's own
    `write32`, a call to itself. It must not be quietly read as `super`."""
    mod, rt = _load(_compile(tmp_path, """
component loop_c : executor_c<> {
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    addr_reg_pkg::write32(h, data, d);
  }
}
component pss_top {
  loop_c x;
  exec init_down { set_executor(x); }
  target function void g(addr_handle_t h) { write32(h, 1); }
}"""))
    with pytest.raises(RecursionError):
        mod.PssTop(rt.MemoryBus()).g(0x10)


def test_the_descriptor_reaches_the_override(tmp_path):
    """LRM Example 349: an extended mem_access_desc_s selects the behaviour;
    a call that passes none gets the default value, fresh per call. The
    split writes are delegated like any other (`split_c` does not override
    write32, so they reach the platform); the single write is `super`'s."""
    log, _ = _run(tmp_path, """
extend struct mem_access_desc_s { bool split; }
component split_c : executor_c<> {
  target function void write64(addr_handle_t h, bit[64] data,
                               mem_access_desc_s d = {}) {
    if (d.split) {
      addr_reg_pkg::write32(make_handle_from_handle(h, 4), (bit[32])(data >> 32));
      addr_reg_pkg::write32(h, (bit[32])data);
    } else {
      super.write64(h, data, d);
    }
  }
}
component pss_top {
  split_c x;
  exec init_down { set_executor(x); }
  target function void g(addr_handle_t h) {
    mem_access_desc_s d;
    d.split = true;
    write64(h, 0x1122334455667788, d);
    write64(h, 0x99);
  }
}""", 0x10)
    assert log == [("write", 0x14, 0x11223344), ("write", 0x10, 0x55667788),
                   ("write", 0x10, 0x99)]


def test_with_no_executors_a_descriptor_is_ignored(tmp_path):
    """21.13.9: a tool may ignore it when it maps the primitive directly."""
    log, _ = _run(tmp_path, """
component pss_top {
  target function void g(addr_handle_t h) {
    mem_access_desc_s d;
    write32(h, 5, d);
  }
}""", 0x10)
    assert log == [("write", 0x10, 5)]


# --- classification ------------------------------------------------------------

def test_a_components_own_read32_is_not_a_primitive(tmp_path):
    """`read32` inside a component that declares one is that component's
    method; elsewhere it is the primitive. Classified by where the call is,
    not by whether the name occurs somewhere in the model."""
    mod, rt = _load(_compile(tmp_path, """
component dev_c {
  target function bit[32] read32(bit[32] x) { return x + 1; }
  target function void g() { message(NONE, "%d", read32(4)); }
}
component pss_top {
  dev_c dev;
  target function void g(addr_handle_t h) {
    message(NONE, "%d", read32(h));
  }
}"""))
    bus = rt.MemoryBus()
    bus.mem[0x10] = 9
    dut = mod.PssTop(bus)
    dut.dev.g()
    dut.g(0x10)
    assert _log(bus) == ["5", ("read", 0x10, 9), "9"]


def test_a_qualified_core_library_call_is_not_dropped(tmp_path):
    """ast2ir used to translate `std_pkg::message(...)` to nothing."""
    log, _ = _run(tmp_path, """
component pss_top {
  target function void g() { std_pkg::message(NONE, "hi"); }
}""")
    assert log == ["hi"]


def test_a_qualified_call_to_an_unknown_package_function_is_refused(tmp_path):
    with pytest.raises(CompileError) as ei:
        _compile(tmp_path, """
package p { import target function void poke(); }
component pss_top {
  target function void g() { p::poke(); }
}""")
    assert "p::poke" in _errors(ei)


# --- the async form ------------------------------------------------------------

def test_the_async_form_awaits_the_override(tmp_path):
    out = _compile(tmp_path, _TAP + """
component pss_top {
  tap_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) {
    write32(h, read32(h));
    write8(h, 1);
  }
}""", py_await="async")
    mod, rt = _load(out, "pssc_rt_async")
    bus = rt.AsyncMemoryBus()
    asyncio.run(mod.PssTop(bus).g(0x10))
    assert _log(bus) == [f"r32 0x10 0x{f32(0x10):x}",
                         f"w32 0x10 0x{f32(0x10):x}", ("write", 0x10, 1)]


# --- refusals ------------------------------------------------------------------

def _refused(tmp_path, pss, target="op-model-py"):
    with pytest.raises((CompileError, ValueError)) as ei:
        _compile(tmp_path, pss, target=target)
    e = ei.value
    return _errors(ei) if isinstance(e, CompileError) else str(e)


def test_set_executor_outside_an_init_block_is_refused(tmp_path):
    msg = _refused(tmp_path, _TAP + """
component pss_top {
  tap_c tap;
  solve function void initialize() { set_executor(tap); }
  target function void g() { }
}""")
    assert "init_down or init_up" in msg


def test_set_executor_of_a_non_executor_is_refused(tmp_path):
    msg = _refused(tmp_path, _TAP + """
component plain_c { }
component pss_top {
  tap_c tap;
  plain_c p;
  exec init_down { set_executor(p); }
  target function void g() { }
}""")
    assert "is an executor" in msg


def test_assigning_an_executor_to_an_executor_is_refused(tmp_path):
    """21.7.2.6 makes it an error."""
    msg = _refused(tmp_path, _TAP + """
component pss_top : executor_c<> {
  tap_c tap;
  exec init_down { set_executor(tap); }
  target function void g() { }
}""")
    assert "its own executor" in msg


def test_overriding_addr_value_is_refused(tmp_path):
    msg = _refused(tmp_path, """
component x_c : executor_c<> {
  target function bit[64] addr_value(addr_handle_t h,
                                     mem_access_desc_s d = {}) {
    return 0;
  }
}
component pss_top {
  x_c x;
  exec init_down { set_executor(x); }
  target function void g() { }
}""")
    assert "overriding 'addr_value'" in msg


def test_an_override_with_the_wrong_prototype_is_refused(tmp_path):
    msg = _refused(tmp_path, """
component x_c : executor_c<> {
  target function bit[32] read32(addr_handle_t h) { return 0; }
}
component pss_top {
  x_c x;
  exec init_down { set_executor(x); }
  target function void g() { }
}""")
    assert "Syntax 159" in msg


def test_a_target_without_delegation_refuses_an_override(tmp_path):
    """op-model-c would call the platform directly and never run the
    override: a model that compiles and does something else."""
    msg = _refused(tmp_path, """
component tap_c : executor_c<> {
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    message(NONE, "w32");
  }
}
component pss_top {
  tap_c tap;
  target function void g() { }
}""", target="op-model-c")
    assert "does not delegate memory primitives" in msg


def test_super_on_a_primitive_outside_an_executor_is_refused(tmp_path):
    msg = _refused(tmp_path, """
component pss_top {
  target function void g(addr_handle_t h) { super.write32(h, 1); }
}""")
    assert "write32" in msg


def test_a_descriptor_that_calls_a_function_is_refused_without_executors(
        tmp_path):
    msg = _refused(tmp_path, """
function mem_access_desc_s mk() { mem_access_desc_s d; return d; }
component pss_top {
  target function void g(addr_handle_t h) {
    write32(h, 5, mk());
  }
}""")
    assert "would drop the call" in msg


@pytest.mark.parametrize("target", ["op-model-c", "op-model-cpp",
                                    "op-model-sv", "op-model-py"])
def test_every_target_lowers_a_qualified_call_as_the_unqualified_one(
        tmp_path, target):
    """Outside an executor's override the qualifier changes nothing, so the
    two spellings generate the same files, byte for byte."""
    body = """
component pss_top {{
  target function void g(addr_handle_t h) {{
    {p}message(NONE, "hi");
    {q}write32(h, 5);
  }}
}}"""
    texts = []
    for k, (p, q) in enumerate([("", ""), ("std_pkg::", "addr_reg_pkg::")]):
        d = tmp_path / str(k)
        d.mkdir()
        out = _compile(d, body.format(p=p, q=q), target=target)
        texts.append({f.name: f.read_text() for f in sorted(out.iterdir())
                      if f.is_file()})
    assert texts[0] == texts[1]


# --- a behavioural model behind imports (COMPLIANCE-DESIGN D-12) ---------------

def test_an_override_reaches_a_model_through_dedicated_imports(tmp_path):
    """LRM Example 352: the executor maps each primitive to an import the
    test declares -- the platform's own primitive is never called."""
    mod, rt = _load(_compile(tmp_path, """
import target function bit[32] model_read32(bit[64] addr);
import target function void model_write32(bit[64] addr, bit[32] data);
component model_c : executor_c<> {
  target function bit[32] read32(addr_handle_t h, mem_access_desc_s d = {}) {
    return model_read32(addr_value(h));
  }
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    model_write32(addr_value(h), data);
  }
}
component pss_top {
  model_c m;
  exec init_down { set_executor(m); }
  target function void g(addr_handle_t h) {
    write32(h, read32(h) + 1);
  }
}"""))

    class Model(rt.MemoryBus):
        def __init__(self):
            super().__init__()
            self.cells = {0x40: 7}

        def model_read32(self, addr):
            return self.cells.get(addr, 0)

        def model_write32(self, addr, data):
            self.cells[addr] = data

    plat = Model()
    mod.PssTop(plat).g(0x40)
    assert plat.cells[0x40] == 8
    assert plat.log == []           # the platform primitives were not called
    # ...and the import API does not ask for them.
    assert _api(mod) == ["model_read32", "model_write32"]


def _api(mod):
    return sorted(n for n in vars(mod.PssTopImportApi) if not n.startswith("_"))


def test_the_import_api_asks_only_for_primitives_the_default_can_reach(
        tmp_path):
    """The tap overrides read32/write32 and the root assigns it: no platform
    read32/write32. write8 is not overridden, so the platform supplies it."""
    mod, _ = _load(_compile(tmp_path, _TAP + """
component pss_top {
  tap_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) {
    write32(h, read32(h));
    write8(h, 1);
  }
}"""))
    assert "read32" not in _api(mod) and "write32" not in _api(mod)
    assert "write8" in _api(mod)


def test_a_conditional_assignment_keeps_the_platform_primitives(tmp_path):
    """If the assignment might not run, the default might be reached."""
    mod, _ = _load(_compile(tmp_path, _TAP + """
component pss_top {
  tap_c tap;
  bool use_tap = true;
  exec init_down { if (use_tap) { set_executor(tap); } }
  target function void g(addr_handle_t h) { write32(h, read32(h)); }
}"""))
    assert "read32" in _api(mod) and "write32" in _api(mod)


# --- a user executor's base ------------------------------------------------------

def test_a_derived_executor_inherits_its_bases_overrides(tmp_path):
    """tap2_c overrides only write32; read32 is tap_c's, inherited (17.1)."""
    log, _ = _run(tmp_path, _TAP + """
component tap2_c : tap_c {
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) {
    message(NONE, "tap2 w32 0x%x", data);
  }
}
component pss_top {
  tap2_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) { write32(h, read32(h) + 1); }
}""", 0xa0000100)
    v = f32(0xa0000100)
    assert log == [f"r32 0xa0000100 0x{v:x}", f"tap2 w32 0x{v + 1:x}"]


def test_super_in_an_inherited_override_still_reaches_the_default(tmp_path):
    """`super` is the base of the component that DECLARED the body: tap_c's
    read32, copied into tap2_c, still means executor_c's read32."""
    log, _ = _run(tmp_path, _F8 + """
component tap_c : executor_c<> {
  target function bit[32] read32(addr_handle_t h, mem_access_desc_s d = {}) {
    message(NONE, "tap r32");
    return super.read32(h, d);
  }
}
component tap2_c : tap_c { }
component pss_top {
  tap2_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) { message(NONE, "%d", read32(h)); }
}""", 0x100, mem={0x100: 42})
    assert log == ["tap r32", ("read", 0x100, 42), "42"]


def test_super_reaches_a_user_base_executors_override(tmp_path):
    """tap2_c's `super.read32` is tap_c's read32 (17.1) -- never the
    platform's, which tap_c's own `super.read32` would be."""
    log, _ = _run(tmp_path, _TAP + """
component tap2_c : tap_c {
  target function bit[32] read32(addr_handle_t h, mem_access_desc_s d = {}) {
    return super.read32(h, d) + 1;
  }
}
component pss_top {
  tap2_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) {
    message(NONE, "0x%x", read32(h));
  }
}""", 0xa0000100)
    v = f32(0xa0000100)
    assert log == [f"r32 0xa0000100 0x{v:x}", f"0x{(v + 1) & 0xffffffff:x}"]
