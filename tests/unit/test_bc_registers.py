"""Register access in the bytecode backend (reg_c / reg_group_c, LRM 21.14).

A register is read and written at its group's handle plus its offset; the
offsets are the group's own ``get_offset_of_instance[_array]`` (evaluated by
pssc, ``bc_session._reg_offset``), and ``set_handle`` binds a group's
sub-groups, arrays included. Each case calls a component function through a
:class:`bc_session.Session` and reads the VM's memory log.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources

MODEL = """\
import std_pkg::*;
import addr_reg_pkg::*;
struct ctl_s : packed_s<> {
    bit[4]  mode;
    bit[4]  pri;
    bit[24] len;
}
pure component ctl_r : reg_c<ctl_s, READWRITE, 32> {}
pure component bank_regs_c : reg_group_c {
    ctl_r a;
    ctl_r b;
    function bit[64] get_offset_of_instance(string name) {
        match (name) { ["a"]: return 0x0; ["b"]: return 0x4; default: return -1; }
    }
    function bit[64] get_offset_of_instance_array(string name, int index) { return -1; }
}
pure component top_regs_c : reg_group_c {
    ctl_r       ctl;
    bank_regs_c sub;
    bank_regs_c bank[2];
    function bit[64] get_offset_of_instance(string name) {
        match (name) { ["ctl"]: return 0x8; ["sub"]: return 0x20; default: return -1; }
    }
    function bit[64] get_offset_of_instance_array(string name, int index) {
        match (name) { ["bank"]: return 0x100 + index*0x10; default: return -1; }
    }
}
component dev_c {
    top_regs_c regs;
    function void attach(addr_handle_t base) { regs.set_handle(base); }
    function void put_val(bit[32] v) { regs.ctl.write_val(v); }
    function void put(bit[4] m, bit[4] p, bit[24] l) {
        ctl_s s;
        s.mode = m; s.pri = p; s.len = l;
        regs.ctl.write(s);
    }
    function bit[32] get_len() { ctl_s s; s = regs.ctl.read(); return s.len; }
    function bit[32] get_val() { return regs.ctl.read_val(); }
    function void set_pri(bit[4] p) { regs.ctl.write_field("pri", p); }
    function void put_sub(bit[32] v) { regs.sub.b.write_val(v); }
    function void put_bank1(bit[32] v) { regs.bank[1].b.write_val(v); }
    function void put_s(ctl_s s, bit[8] k) { s.len = s.len + k; regs.ctl.write(s); }
}
"""


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    p = tmp_path_factory.mktemp("regs") / "regs.pss"
    p.write_text(MODEL)
    ctx = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not ctx.errors
    return bc_session.lower(ctx, "dev_c", functions=["*"])


@pytest.fixture
def dev(model):
    s = bc_session.Session.load(model)
    root = s.construct()
    s.call(root, "attach", 0x1000)
    return s, root


def _writes(s):
    return [(a, v) for k, a, _, v in s.vm.memory.log if k == "w"]


def test_write_val_goes_to_the_offset_the_group_gives(dev):
    s, root = dev
    s.call(root, "put_val", 0xabcd1234)
    assert _writes(s) == [(0x1008, 0xabcd1234)]


def test_a_struct_is_packed_first_field_lowest(dev):
    s, root = dev
    s.call(root, "put", 3, 5, 0x10)
    assert _writes(s) == [(0x1008, 0x1053)]
    assert s.call(root, "get_val") == 0x1053
    assert s.call(root, "get_len") == 0x10


def test_write_field_reads_modifies_and_writes(dev):
    s, root = dev
    s.call(root, "put_val", 0xffff0003)
    s.call(root, "set_pri", 0xa)
    assert _writes(s)[-1] == (0x1008, 0xffff00a3)
    assert ("r", 0x1008, 4, 0xffff0003) in s.vm.memory.log


def test_set_handle_binds_named_sub_groups(dev):
    s, root = dev
    s.call(root, "put_sub", 7)
    assert _writes(s) == [(0x1024, 7)]


def test_set_handle_binds_group_arrays(dev):
    s, root = dev
    s.call(root, "put_bank1", 9)
    assert _writes(s) == [(0x1114, 9)]


def test_a_struct_parameter_is_one_argument_per_field(dev):
    s, root = dev
    assert [n for n, _ in s.entry(root, "put_s").params] == [
        "s.mode", "s.pri", "s.len", "k"]
    s.call(root, "put_s", 3, 5, 0x10, 2)
    assert _writes(s) == [(0x1008, 0x1253)]
