"""The component tree on bc (P1.5; LRM 9.1.4, 20.1.2-20.1.3).

Every component instance under the root is a slot range of ONE component
object (ir-core ``comp_tree``), and a coroutine constructs it before the entry
runs: each instance's declared initial values, then ``exec init_down``
top-down, then ``exec init_up`` bottom-up -- the order LRM Example 281 lists.
"""
import pytest

from zuspec.be.bc.lower.errors import LoweringError, PssSemanticError

from .test_activity_bc_runs import _lower, trace
from .test_action_tree import _module

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def test_ex281_init_down_top_down_then_init_up_bottom_up():
    src = """
import std_pkg::*;
component C {
    int n;
    exec init_down { message(NONE, "down %d", n); }
    exec init_up { message(NONE, "up %d", n); }
}
component pss_top {
    C c1, c2;
    exec init_down { c1.n = 1; c2.n = 2; message(NONE, "down T"); }
    exec init_up { message(NONE, "up T"); }
    action A { exec body { message(NONE, "body"); } }
}
"""
    assert trace(src, root="pss_top::A") == [
        "down T", "down 1", "down 2", "up 1", "up 2", "up T", "body"]


def test_layout_one_object_each_instance_a_slot_range():
    module = _module("""
import std_pkg::*;
struct s_t { bit[8] a; bit[8] b; }
component sub_c { int k; s_t s; }
component dev_c { bit[32] base; sub_c sub; int tail; }
component pss_top {
    dev_c d[2];
    int n;
    action A { exec body { } }
}
""", "A")
    ct = module.comp_tree
    assert [(i.id, i.path, i.type_qname, i.parent) for i in ct.instances] == [
        (0, "", "pss_top", None),
        (1, "d[0]", "dev_c", 0), (2, "d[0].sub", "sub_c", 1),
        (3, "d[1]", "dev_c", 0), (4, "d[1].sub", "sub_c", 3)]
    names = [f.name for f in sorted(ct.fields, key=lambda f: f.slot)]
    assert names == [
        "d[0].base", "d[0].sub.k", "d[0].sub.s.a", "d[0].sub.s.b", "d[0].tail",
        "d[1].base", "d[1].sub.k", "d[1].sub.s.a", "d[1].sub.s.b", "d[1].tail",
        "n"]
    # A type's subtree has one shape wherever it is: d[1] is d[0] moved.
    assert [(i.base, i.size) for i in ct.instances] == [
        (0, 11), (0, 5), (1, 3), (5, 5), (6, 3)]


def test_initial_values_then_init_blocks_override_them():
    src = """
import std_pkg::*;
struct s_t { bit[8] a = 3; bit[8] b = 4; }
component sub_c {
    int k = 7;
    s_t s;
    exec init_down { s.b = 9; }
}
component pss_top {
    sub_c x, y;
    exec init_down { y.k = 70; }
    action A {
        exec body { message(NONE, "%d %d %d %d %d", comp.x.k, comp.y.k,
                            comp.x.s.a, comp.x.s.b, comp.y.s.b); }
    }
}
"""
    assert trace(src, root="pss_top::A") == ["7 70 3 9 9"]


def test_a_derived_component_has_its_bases_attributes_and_blocks():
    """17.1: fields of the base first; an init block the derived type does
    not declare is its base's; functions are virtual."""
    src = """
import std_pkg::*;
component base_c {
    int b = 1;
    exec init_down { b = b + 10; }
    function int f() { return 1; }
    function int g() { return f() * 100 + b; }
}
component der_c : base_c {
    int d = 2;
    function int f() { return 2; }
}
component pss_top {
    der_c x;
    action A { exec body { message(NONE, "%d %d %d", comp.x.b, comp.x.d, comp.x.g()); } }
}
"""
    assert trace(src, root="pss_top::A") == ["11 2 211"]


def test_component_attributes_are_per_instance():
    src = """
import std_pkg::*;
component cnt_c {
    int n;
    function void bump() { n = n + 1; }
    function int get() { return n; }
}
component pss_top {
    cnt_c a, b;
    exec init_down { a.bump(); a.bump(); b.bump(); }
    action A { exec body { message(NONE, "%d %d", comp.a.get(), comp.b.get()); } }
}
"""
    assert trace(src, root="pss_top::A") == ["2 1"]


def test_an_element_of_a_component_array_with_a_computed_index_is_refused():
    src = """
import std_pkg::*;
component ch_c { int id; }
component pss_top {
    ch_c ch[2];
    action A { exec body { int j = 1; message(NONE, "%d", comp.ch[j].id); } }
}
"""
    with pytest.raises(LoweringError, match="computed index"):
        _lower(src, root="pss_top::A")


def test_a_component_array_index_out_of_bounds_is_an_error():
    src = """
import std_pkg::*;
component ch_c { int id; }
component pss_top {
    ch_c ch[2];
    action A { exec body { message(NONE, "%d", comp.ch[2].id); } }
}
"""
    with pytest.raises(PssSemanticError, match="out of bounds"):
        _lower(src, root="pss_top::A")
