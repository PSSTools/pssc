"""Address handles, memory and executors on bc (bc procedural gaps G5, B-D6).

bc models transparent address spaces only, so an ``addr_handle_t`` is its
address: ``add_region`` returns the region's ``addr``, and
``make_handle_from_handle`` adds. A memory access goes to the executor in force
for the calling code's instance (21.7.2.6), resolved when the model is
lowered; with none, or for a primitive the executor does not override, the
platform answers (a builtin import; the oracle's memory is sparse, little
endian, 0 where unwritten).
"""
import pytest

from zuspec.be.bc.lower.errors import LoweringError

from .test_activity_bc_runs import trace

_SRC = """\
import std_pkg::*;
import addr_reg_pkg::*;
import executor_pkg::*;
component log_xtr_c : executor_c<> {
    target function bit[32] read32(addr_handle_t hndl, mem_access_desc_s d = {}) {
        message(NONE, "x r32 0x%%x", addr_value(hndl));
        return 0x1234;
    }
}
%s
component pss_top {
    transparent_addr_space_c<> sys_mem;
    addr_handle_t ram;
    %s
    exec init_down {
        %s
        transparent_addr_region_s<> r;
        r.size = 0x1000;
        r.addr = 0x8000;
        ram = sys_mem.add_region(r);
    }
    action T { exec body { addr_handle_t h = make_handle_from_handle(comp.ram, 0x10);
        %s } }
}
"""


def _run(body: str, fields: str = "", init: str = "", decls: str = ""):
    return trace(_SRC % (decls, fields, init, body))


def test_with_no_executor_the_platform_memory_answers():
    assert _run('write32(h, 0xa1b2c3d4); write8(make_handle_from_handle(h, 4), 0x55); '
                'message(NONE, "%x %x %x %x", read32(h), read8(h), read16(h), read64(h));') \
        == ["a1b2c3d4 d4 c3d4 55a1b2c3d4"]


def test_unwritten_memory_reads_zero_and_a_handle_is_its_address():
    assert _run('message(NONE, "%x 0x%x", read32(h), addr_value(h));') == ["0 0x8010"]


def test_an_executor_overrides_the_primitives_it_declares():
    """read32 goes to the executor; write32, which it does not declare, and
    the read8 that follows, reach the platform."""
    assert _run('write32(h, 7); message(NONE, "%x %x", read32(h), read8(h));',
                fields="log_xtr_c xtr;", init="set_executor(xtr);") \
        == ["x r32 0x8010", "1234 7"]


def test_a_sub_component_inherits_its_parents_executor():
    decls = """
component dev_c {
    function bit[32] peek(addr_handle_t h) { return read32(h); }
}
"""
    assert _run('message(NONE, "%x", comp.dev.peek(h));', decls=decls,
                fields="log_xtr_c xtr; dev_c dev;", init="set_executor(xtr);") \
        == ["x r32 0x8010", "1234"]


@pytest.mark.parametrize("fields,init,body,match", [
    ("log_xtr_c xtr;", "if (ram == ram) { set_executor(xtr); }", "read32(h);",
     "not a top-level statement"),
    ("", "", "set_executor(comp.sys_mem);", "outside a component's init block"),
    ("contiguous_addr_space_c<> other;", "addr_region_s<> q; other.add_region(q);", "",
     "non-transparent address space"),
    ("", "transparent_addr_region_s<> q; q.trait = q.trait;", "",
     "struct template parameter"),
])
def test_refused(fields, init, body, match):
    with pytest.raises(LoweringError, match=match):
        _run(body, fields=fields, init=init)
