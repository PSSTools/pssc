"""PC-3: what an operation touches, transitively (`targets/state_uses.py`,
`OpModel.state_uses`, `OpModel.unpacked_signature`)."""
import argparse

import pytest

from pssc import driver, targets
from pssc.diag import Refusal
from pssc.targets.call_legality import COMMON, Disposition, REG_DIRECTION

REGS = """
package r_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;
    struct ctl_s : packed_s<> { rand bit[1] en; rand bit[3] mode; rand bit[28] rsvd; }
    struct w_s : packed_s<> { rand bit[32] v; }
    pure component ctl_r : reg_c<ctl_s, READWRITE, 32> {}
    pure component w_r : reg_c<w_s, READWRITE, 32> {}
    pure component g_c : reg_group_c {
        ctl_r ctl;
        w_r   stat;
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["ctl"]: return 0x0; ["stat"]: return 0x4;
                           default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }
}
import addr_reg_pkg::*;
"""

MODEL = """
import target function void plat_delay(int n);

function bit[32] pkg_peek(addr_handle_t h) { return read32(h); }

component leaf_c {
    r_pkg::g_c regs;
    int hits;
    solve function void initialize(addr_handle_t at) { regs.set_handle(at); }
    target function bit[32] stat() { return regs.stat.read().v; }
    target function void bump() { hits = hits + 1; }
}

component top_c {
    r_pkg::g_c a;
    r_pkg::g_c b;
    leaf_c l;
    int count;

    solve function void initialize(addr_handle_t pa, addr_handle_t pb,
                                   addr_handle_t pl) {
        a.set_handle(pa);
        b.set_handle(pb);
        l.initialize(pl);
    }

    target function void enable(bit[3] m) {
        a.ctl.write_fields({"en", "mode"}, {1, m});
    }
    target function bit[32] poll() {
        r_pkg::ctl_s c;
        repeat {
            c = b.ctl.read();
        } while (c.en == 0);
        return b.stat.read().v;
    }
    target function bit[32] leaf_stat() { return l.stat(); }
    target function bit[32] via_self() { return poll(); }
    target function void counts() { count = count + 1; l.bump(); }
    target function void waits(int n) { plat_delay(n); }
    target function bit[32] peeks(addr_handle_t h) { return pkg_peek(h); }
    target function void ping(int n) { if (n > 0) { pong(n - 1); } }
    target function void pong(int n) { if (n > 0) { ping(n - 1); } }
    target function bit[32] bounded(int n) {
        bit[32] s;
        repeat (n) { s = s + a.stat.read().v; }
        return s;
    }
}
"""


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    d = tmp_path_factory.mktemp("su")
    p = d / "m.pss"
    p.write_text(REGS + MODEL)
    tgt = targets.get("op-model-c")
    ctx = driver.translate([str(p)], prelude=tgt.prelude(argparse.Namespace()))
    return tgt.build_model(ctx, argparse.Namespace(
        progseq_root="top_c", output_dir=str(d)))


def _fn(model, comp, name):
    c = next(c for c in model.comp_dtypes if c.name.split("::")[-1] == comp)
    return next(f for f in c.functions if f.name == name)


def test_a_masked_write_reads_and_writes_and_names_its_fields(model):
    su = model.state_uses(_fn(model, "top_c", "enable"))
    assert su.groups == {("a",)}
    assert su.regs_read == su.regs_written == {("a", "ctl")}
    assert su.reg_fields_written == {(("a", "ctl"), "en"), (("a", "ctl"), "mode")}
    assert su.stateless and not su.unbounded_loops


def test_a_poll_is_an_unbounded_loop(model):
    su = model.state_uses(_fn(model, "top_c", "poll"))
    assert su.groups == {("b",)}
    assert su.regs_read == {("b", "ctl"), ("b", "stat")}
    assert not su.regs_written
    assert len(su.unbounded_loops) == 1


def test_repeat_with_a_runtime_count_is_bounded(model):
    su = model.state_uses(_fn(model, "top_c", "bounded"))
    assert su.unbounded_loops == ()
    assert su.regs_read == {("a", "stat")}


def test_a_sub_components_register_through_its_function(model):
    su = model.state_uses(_fn(model, "top_c", "leaf_stat"))
    assert su.groups == {("l", "regs")}
    assert su.regs_read == {("l", "regs", "stat")}
    assert [(c.kind, c.function.name, c.path) for c in su.callees] == \
        [("member", "stat", ("l",))]


def test_a_self_call_is_followed(model):
    su = model.state_uses(_fn(model, "top_c", "via_self"))
    assert su.groups == {("b",)}
    assert len(su.unbounded_loops) == 1


def test_component_fields_are_state(model):
    su = model.state_uses(_fn(model, "top_c", "counts"))
    assert su.fields_written == {("count",), ("l", "hits")}
    assert su.fields_read == {("count",), ("l", "hits")}
    assert not su.stateless


def test_imports_and_package_functions(model):
    assert model.state_uses(_fn(model, "top_c", "waits")).imports_called \
        == {"plat_delay"}
    su = model.state_uses(_fn(model, "top_c", "peeks"))
    assert su.mem_accessed
    assert [c.kind for c in su.callees] == ["package"]
    assert not su.executor_delegated


def test_mutual_recursion_terminates(model):
    su = model.state_uses(_fn(model, "top_c", "ping"))
    assert {c.function.name for c in su.callees} == {"ping", "pong"}


def test_the_result_is_memoised(model):
    fn = _fn(model, "top_c", "poll")
    assert model.state_uses(fn) is model.state_uses(fn)


# -- the unpacked signature -----------------------------------------------------

def test_unpacked_signature_orders_by_root_constructor_parameter(model):
    assert model.unpacked_signature(_fn(model, "top_c", "enable")) == \
        [(("a",), "pa")]
    assert model.unpacked_signature(_fn(model, "top_c", "leaf_stat")) == \
        [(("l", "regs"), "pl")]
    assert model.unpacked_signature(_fn(model, "top_c", "counts")) == []


def test_unpacked_signature_of_a_sub_components_function(model):
    assert model.unpacked_signature(_fn(model, "leaf_c", "stat")) == \
        [(("l", "regs"), "pl")]


def test_a_group_with_no_bare_parameter_is_refused(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(REGS + """
    component top_c {
        r_pkg::g_c a;
        solve function void initialize(addr_handle_t pa) {
            a.set_handle(make_handle_from_handle(pa, 0x100));
        }
        target function bit[32] s() { return a.stat.read().v; }
    }""")
    tgt = targets.get("op-model-c")
    ctx = driver.translate([str(p)], prelude=tgt.prelude(argparse.Namespace()))
    m = tgt.build_model(ctx, argparse.Namespace(progseq_root="top_c",
                                                output_dir=str(tmp_path)))
    with pytest.raises(Refusal, match="'a'"):
        m.unpacked_signature(_fn(m, "top_c", "s"))


# -- the tables ---------------------------------------------------------------

def test_every_register_method_has_a_direction():
    regs = {n for n, e in COMMON.items() if e.disposition is Disposition.REG}
    assert regs == set(REG_DIRECTION)


def test_an_executor_model_delegates_its_accesses(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text("""
import std_pkg::*;
import addr_reg_pkg::*;
import executor_pkg::*;
component tap_c : executor_c<> {
  target function void write32(addr_handle_t h, bit[32] data,
                               mem_access_desc_s d = {}) { }
}
component pss_top {
  tap_c tap;
  exec init_down { set_executor(tap); }
  target function void g(addr_handle_t h) { write32(h, 1); }
  target function int pure_math(int x) { return x + 1; }
}
""")
    tgt = targets.get("op-model-py")
    ctx = driver.translate([str(p)], prelude=tgt.prelude(argparse.Namespace()))
    m = tgt.build_model(ctx, argparse.Namespace(progseq_root="pss_top",
                                                output_dir=str(tmp_path)))
    g = _fn(m, "pss_top", "g")
    assert m.state_uses(g).mem_accessed
    assert m.state_uses(g).executor_delegated
    assert not m.state_uses(_fn(m, "pss_top", "pure_math")).executor_delegated
