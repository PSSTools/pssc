"""A register of a SUB-COMPONENT, reached directly from the parent, under
``reg_map``.

Before this, `_reg_call` raised for exactly this shape: `_reg_map_access`
addressed every register from the ROOT's own handle (`self.h`), and a
sub-component is reached by value through it (`s->leaf[i]`, not a pointer),
which neither `_reg_path` nor the accessor lookup (keyed by the SUB's own
prefix) had a way to thread through. `read_bank_stat` in
`tests/progseq/data/offset_fold.pss` worked around the gap by calling an
OPERATION of the sub-component instead of touching its register directly --
which is a real pattern, but not the only one PSS allows (LRM 7.3: a
component's register group is a field like any other, reachable by path from
wherever a handle to it exists).
"""
import argparse

import pytest

from pssc import driver

from .conftest import available_c_compilers

_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")

_MODEL = """
package leaf_regs_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;

    struct ctrl_s : packed_s<> {
        rand bit[1]  en;
        rand bit[31] rsvd;
    }
    struct word_s : packed_s<> {
        rand bit[32] value;
    }

    pure component ctrl_r : reg_c<ctrl_s, READWRITE, 32> {}
    pure component word_r : reg_c<word_s, READWRITE, 32> {}

    pure component leaf_regs_c : reg_group_c {
        ctrl_r ctrl;   // 0x0
        word_r stat;   // 0x4

        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["ctrl"]: return 0x0;
                ["stat"]: return 0x4;
                default:  return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }
}

import addr_reg_pkg::*;

component leaf_c {
    leaf_regs_pkg::leaf_regs_c regs;

    solve function void initialize(addr_handle_t at) {
        regs.set_handle(at);
    }
}

component sub_top_c {
    leaf_c leaf[2];

    solve function void initialize(addr_handle_t base) {
        foreach (leaf[i]) {
            leaf[i].initialize(make_handle_from_handle(base, i * 0x8));
        }
    }

    // Every shape `_reg_call` has to classify for a sub-component chain,
    // reached directly from the PARENT rather than through an operation of
    // `leaf_c` itself.
    target function bit[32] read_leaf_stat(int i) {
        leaf_regs_pkg::word_s w;
        w = leaf[i].regs.stat.read();
        return w.value;
    }

    target function void write_leaf_stat(int i, bit[32] v) {
        leaf_regs_pkg::word_s w;
        w.value = v;
        leaf[i].regs.stat.write(w);
    }

    target function void enable_leaf(int i) {
        leaf[i].regs.ctrl.write_field("en", 1);
    }

    export target function read_leaf_stat;
    export target function write_leaf_stat;
    export target function enable_leaf;
}
"""


def _gen(tmp_path, root="sub_top_c"):
    src = tmp_path / "sub_top.pss"
    src.write_text(_MODEL)
    ns = argparse.Namespace(progseq_root=root, c_prefix="sub_top",
                            c_link_style="mmio",
                            output_dir=str(tmp_path))
    driver.compile([str(src)], target="c-progseq", opts=ns)
    return ((tmp_path / "sub_top.h").read_text(),
            (tmp_path / "sub_top.c").read_text())


def test_a_direct_sub_component_register_read_addresses_through_the_map(tmp_path):
    _, c = _gen(tmp_path)
    assert "pssc_read32(&_self->leaf[i].regs->stat)" in c


def test_a_direct_sub_component_register_write_addresses_through_the_map(tmp_path):
    _, c = _gen(tmp_path)
    assert "pssc_write32(&_self->leaf[i].regs->stat, (w).raw)" in c


def test_a_direct_sub_component_field_write_is_one_masked_primitive_call(tmp_path):
    _, c = _gen(tmp_path)
    assert "pssc_write_masked32(&_self->leaf[i].regs->ctrl, 1, 1)" in c


@needs_cc
def test_the_generated_driver_compiles(tmp_path):
    import subprocess

    _gen(tmp_path)
    cc = _CC[0]
    r = subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                       "-c", str(tmp_path / "sub_top.c"),
                       "-o", str(tmp_path / "sub_top.o")],
                       cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
