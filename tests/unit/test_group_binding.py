"""PC-2: which constructor parameter gives each register group its handle
(`targets/group_binding.py`, `OpModel.bindings`, `OpModel.handle_params`).

Also the regression for a disagreement between the gate and the emitters:
the gate computed bindings over the whole model, while C and Python computed
them for one component at a time, so a group that a PARENT binds
(`sub.g.set_handle(p)`) was also bound implicitly by the C and Python output.
"""
import argparse

import pytest

from pssc import driver, targets

REGS = """
package r_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;
    struct w_s : packed_s<> { rand bit[32] v; }
    pure component w_r : reg_c<w_s, READWRITE, 32> {}
    pure component g_c : reg_group_c {
        w_r stat;
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["stat"]: return 0x0; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }
}
import addr_reg_pkg::*;
"""


def _model(tmp_path, src, root):
    p = tmp_path / "m.pss"
    p.write_text(REGS + src)
    tgt = targets.get("op-model-c")
    ctx = driver.translate([str(p)], prelude=tgt.prelude(argparse.Namespace()))
    return tgt.build_model(ctx, argparse.Namespace(
        progseq_root=root, output_dir=str(tmp_path)))


def _binding(model, name):
    comp = next(c for c in model.comp_dtypes
                if c.name.split("::")[-1] == name)
    return model.binding(comp)


def test_bare_parameters(tmp_path):
    m = _model(tmp_path, """
    component top_c {
        r_pkg::g_c a;
        r_pkg::g_c b;
        solve function void initialize(addr_handle_t pa, addr_handle_t pb) {
            a.set_handle(pa);
            b.set_handle(pb);
        }
    }""", "top_c")
    b = _binding(m, "top_c")
    assert dict(b.params) == {("a",): "pa", ("b",): "pb"}
    assert b.pure_bindings and b.init_fn is not None and b.implicit is None
    assert m.handle_params() == {("a",): "pa", ("b",): "pb"}


def test_an_expression_is_no_parameter(tmp_path):
    m = _model(tmp_path, """
    component top_c {
        r_pkg::g_c a;
        solve function void initialize(addr_handle_t pa) {
            a.set_handle(make_handle_from_handle(pa, 4));
        }
    }""", "top_c")
    b = _binding(m, "top_c")
    assert dict(b.params) == {("a",): None}
    assert not b.pure_bindings
    assert m.handle_params() == {("a",): None}


def test_one_parameter_may_bind_two_groups(tmp_path):
    m = _model(tmp_path, """
    component top_c {
        r_pkg::g_c a;
        r_pkg::g_c b;
        solve function void initialize(addr_handle_t p) {
            a.set_handle(p);
            b.set_handle(p);
        }
    }""", "top_c")
    b = _binding(m, "top_c")
    assert dict(b.params) == {("a",): "p", ("b",): "p"}
    assert b.pure_bindings


def test_the_implicit_binding_is_a_parameter_too(tmp_path):
    m = _model(tmp_path, """
    component top_c {
        r_pkg::g_c regs;
        solve function void initialize(addr_handle_t base) { }
    }""", "top_c")
    b = _binding(m, "top_c")
    assert b.implicit == ("regs", "base")
    assert dict(b.params) == {("regs",): "base"}
    assert b.pure_bindings
    assert m.handle_params() == {("regs",): "base"}


def test_composed_through_a_sub_components_constructor(tmp_path):
    m = _model(tmp_path, """
    component leaf_c {
        r_pkg::g_c regs;
        solve function void initialize(addr_handle_t at) { regs.set_handle(at); }
    }
    component top_c {
        leaf_c l;
        r_pkg::g_c own;
        solve function void initialize(addr_handle_t x, addr_handle_t y) {
            own.set_handle(y);
            l.initialize(x);
        }
    }""", "top_c")
    assert m.handle_params() == {("l", "regs"): "x", ("own",): "y"}
    assert _binding(m, "top_c").pure_bindings


def test_composed_through_super_initialize(tmp_path):
    m = _model(tmp_path, """
    component base_c {
        r_pkg::g_c regs;
        solve function void initialize(addr_handle_t at) { regs.set_handle(at); }
    }
    component der_c : base_c {
        solve function void initialize(addr_handle_t q) { super.initialize(q); }
    }
    component top_c {
        der_c d;
        solve function void initialize(addr_handle_t z) { d.initialize(z); }
    }""", "top_c")
    b = _binding(m, "der_c")
    assert dict(b.params) == {("regs",): "q"}
    assert b.pure_bindings
    assert m.handle_params() == {("d", "regs"): "z"}


def test_control_flow_is_not_pure(tmp_path):
    m = _model(tmp_path, """
    component top_c {
        r_pkg::g_c a;
        solve function void initialize(addr_handle_t pa, bit on) {
            if (on == 1) { a.set_handle(pa); }
        }
    }""", "top_c")
    b = _binding(m, "top_c")
    assert dict(b.params) == {("a",): "pa"}
    assert not b.pure_bindings


def test_an_array_of_sub_components_has_no_single_parameter(tmp_path):
    m = _model(tmp_path, """
    component leaf_c {
        r_pkg::g_c regs;
        solve function void initialize(addr_handle_t at) { regs.set_handle(at); }
    }
    component top_c {
        leaf_c leaf[2];
        solve function void initialize(addr_handle_t base) {
            foreach (leaf[i]) {
                leaf[i].initialize(make_handle_from_handle(base, i * 8));
            }
        }
    }""", "top_c")
    assert m.handle_params() == {("leaf", "regs"): None}
    assert not _binding(m, "top_c").pure_bindings


# -- the gate and the emitters read one table --------------------------------

PARENT_BINDS = """
component leaf_c {
    r_pkg::g_c regs;
    solve function void initialize(addr_handle_t at) { }
    target function bit[32] stat() { return regs.stat.read().v; }
}
component top_c {
    leaf_c l;
    solve function void initialize(addr_handle_t p, addr_handle_t q) {
        l.initialize(q);
        l.regs.set_handle(p);
    }
}
"""


def test_a_group_its_parent_binds_is_not_bound_implicitly(tmp_path):
    m = _model(tmp_path, PARENT_BINDS, "top_c")
    assert _binding(m, "leaf_c").implicit is None
    assert m.handle_params() == {("l", "regs"): "p"}


@pytest.mark.parametrize("target,ext,implicit,parent", [
    ("op-model-c", ".c", "= at;", "_self->l.base_regs = p"),
    ("op-model-py", ".py", "_pss_base_regs = at", "self.l._pss_base_regs = p"),
])
def test_the_emitters_agree_with_the_gate(tmp_path, target, ext, implicit,
                                          parent):
    """Was: both emitters raised on the parent's `l.regs.set_handle(p)`, and
    computed per component, would have bound `regs` to leaf_c's own `at`
    as well, which the gate counted as bound by top_c."""
    src = tmp_path / "m.pss"
    src.write_text(REGS + PARENT_BINDS)
    out = tmp_path / "out"
    driver.compile([str(src)], target=target, output_dir=str(out),
                   progseq_root="top_c")
    text = "".join(f.read_text() for f in out.rglob("*" + ext))
    assert implicit not in text
    assert parent in text


def test_the_python_driver_reads_the_parent_bound_address(tmp_path):
    """Driven, not grepped: the sub-component's register is read at the
    handle its PARENT gave it."""
    import importlib
    import sys

    src = tmp_path / "m.pss"
    src.write_text(REGS + PARENT_BINDS)
    out = tmp_path / "out"
    driver.compile([str(src)], target="op-model-py", output_dir=str(out),
                   progseq_root="top_c")
    mod_name = next(p.stem for p in out.glob("*.py") if p.stem != "pssc_rt")
    sys.path.insert(0, str(out))
    try:
        for n in (mod_name, "pssc_rt"):
            sys.modules.pop(n, None)
        rt = importlib.import_module("pssc_rt")
        mod = importlib.import_module(mod_name)
    finally:
        sys.path.remove(str(out))
    bus = rt.MemoryBus()
    bus.write32(0x3000, 0xCAFE)
    top_cls = next(getattr(mod, n) for n in dir(mod)
                   if n.lower().replace("_", "") == "topc"
                   or n.lower().replace("_", "") == "top")
    dut = top_cls(bus, 0x3000, 0x5000)
    assert dut.l.stat() == 0xCAFE


def test_the_c_driver_compiles(tmp_path):
    import shutil
    import subprocess

    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    src = tmp_path / "m.pss"
    src.write_text(REGS + PARENT_BINDS)
    out = tmp_path / "out"
    driver.compile([str(src)], target="op-model-c", output_dir=str(out),
                   progseq_root="top_c")
    for c in out.glob("*.c"):
        r = subprocess.run([cc, "-std=c99", "-Wall", "-Werror", "-c", str(c),
                            "-o", str(c.with_suffix(".o"))],
                           cwd=str(out), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
