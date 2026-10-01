"""Which component instance an action runs in (P1.5; LRM 9.1.5, 13.4.5).

A traversed action runs in one of its candidates: an instance of its
action's component type in the subtree of its parent's instance (9.1.5.1).
With one candidate the instance is static. With more, ``comp`` is a variable
of the node's solve (P1-D4): chosen at random, and steered by ``comp == X``
in a ``with`` (Ex 143).

Coroutines of the root's actions keep their simple names; another
component's are qualified (``sub_c::S``), so two components may declare
actions of one name (O5).
"""
from collections import Counter

import pytest

from zuspec.ir.core.xf import PSSToScenarioPass
from zuspec.ir.core.xf.validate import UnsupportedConstructError
from zuspec.be.bc.interp import NativeBlobBackend, VMError, run_model
from zuspec.be.bc.lower import lower_module
from zuspec.be.bc.lower.errors import LoweringError

from .test_activity_bc_runs import _lower, trace
from .test_action_tree import _translate

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_EX50 = """
import std_pkg::*;
component my_comp_c {
    int f;
    action A_a {
        exec post_solve { message(NONE, "%d", comp.f); }
    }
}
component pss_top {
    my_comp_c comp1, comp2, comp3;
    exec init_up { comp1.f = 6; comp2.f = 7; comp3.f = 8; }
    action entry_a { activity { do my_comp_c::A_a; } }
}
"""


def _runs(src, root, seeds):
    model = _lower(src, root=root)
    out = []
    for seed in seeds:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append(tuple(ln.strip() for ln in lines if ln.strip()))
    return out


def test_ex50_a_traversal_runs_in_any_of_three_instances():
    """Example 50: `do my_comp_c::A_a` may run in comp1, comp2 or comp3."""
    got = Counter(_runs(_EX50, "pss_top::entry_a", range(300)))
    assert set(got) == {("6",), ("7",), ("8",)}
    assert min(got.values()) > 60            # each about a third


def test_ex143_comp_steers_the_traversal_to_a_sub_instance():
    src = """
import std_pkg::*;
component subc {
    int id;
    action A {
        rand bit[4] f;
        rand bit[4] g;
        exec post_solve { message(NONE, "%d %u %u", comp.id, f, g); }
    }
}
component top {
    subc sub1, sub2;
    exec init_down { sub1.id = 1; sub2.id = 2; }
    action B {
        rand bit[4] f;
        rand bit[4] h;
        subc::A a;
        activity {
            a with {
                f < h;
                g == this.f;
                comp == this.comp.sub1;
            };
        }
        exec post_solve { message(NONE, "%u %u", f, h); }
    }
}
"""
    for b_f_h, a in _runs(src, "top::B", range(40)):
        bf, h = map(int, b_f_h.split())
        cid, af, ag = map(int, a.split())
        assert cid == 1 and af < h and ag == bf


def test_comp_naming_the_only_candidate_holds_and_any_other_is_an_error():
    """One candidate: `comp == X` is decided at lowering."""
    src = """
import std_pkg::*;
component subc { action A { exec body { message(NONE, "A"); } } }
component other_c { }
component pss_top {
    subc sub1;
    other_c o;
    action T { activity { do subc::A with { comp == this.comp.%s; }; } }
}
"""
    assert trace(src % "sub1") == ["A"]
    with pytest.raises(UnsupportedConstructError, match="can never hold") as ei:
        _lower(src % "o")
    assert ei.value.loc is not None and ei.value.loc.line == 8


def test_a_steer_to_an_instance_that_is_not_a_candidate_never_holds():
    src = """
import std_pkg::*;
component subc { action A { exec body { message(NONE, "A"); } } }
component other_c { }
component pss_top {
    subc sub1, sub2;
    other_c o;
    action T { activity { do subc::A with { comp == this.comp.o; }; } }
}
"""
    model = _lower(src)
    with pytest.raises(VMError, match="no values for traversal"):
        run_model(model, seed=0, solve_backend=NativeBlobBackend(), out=lambda _: None)


def test_a_child_runs_relative_to_its_parents_chosen_instance():
    """P chooses n1 or n2; its child's instance is P's ``lf`` -- an offset
    from a value chosen at run time."""
    src = """
import std_pkg::*;
component leaf_c {
    int id;
    action L { exec post_solve { message(NONE, "L %d", comp.id); } }
}
component node_c {
    int id;
    leaf_c lf;
    action P {
        exec post_solve { message(NONE, "P %d", comp.id); }
        activity { do leaf_c::L with { comp == this.comp.lf; }; do leaf_c::L; }
    }
}
component pss_top {
    node_c n1, n2;
    exec init_down { n1.id = 1; n1.lf.id = 10; n2.id = 2; n2.lf.id = 20; }
    action T { activity { do node_c::P; } }
}
"""
    seen = set()
    for p, l1, l2 in _runs(src, "pss_top::T", range(30)):
        n = int(p.split()[1])
        assert l1 == l2 == "L %d" % (n * 10)
        seen.add(n)
    assert seen == {1, 2}


def test_ex51_an_action_outside_the_context_subtree_is_refused():
    """9.1.5.1, Example 51: bus_c is instantiated, but not under graphics."""
    src = """
import std_pkg::*;
component bus_c { action write { exec body { } } }
component graphics {
    action gr_a { activity { do bus_c::write; } }
}
component pss_top {
    bus_c a0;
    graphics g;
    action entry { activity { do graphics::gr_a; } }
}
"""
    with pytest.raises(UnsupportedConstructError,
                       match=r"bus_c::write.*not instantiated in the subtree of 'graphics'") as ei:
        _lower(src, root="pss_top::entry")
    assert ei.value.loc is not None and ei.value.loc.line == 5


def test_pre_solve_reading_comp_before_the_choice_is_a_run_time_error():
    """With more than one candidate, the node's solve chooses comp: pre_solve
    runs before it."""
    src = """
import std_pkg::*;
component c_c {
    int f;
    action A { exec pre_solve { message(NONE, "%d", comp.f); } }
}
component pss_top { c_c c1, c2; action T { activity { do c_c::A; } } }
"""
    with pytest.raises(VMError, match="before the action's solve chose"):
        trace(src)


def test_a_constraint_reading_a_component_attribute_is_refused():
    src = """
import std_pkg::*;
component c_c {
    int f = 5;
    action A { rand bit[4] v; constraint v < comp.f; exec body { } }
}
component pss_top { c_c c1; action T { activity { do c_c::A; } } }
"""
    with pytest.raises(LoweringError, match=r"component attribute \(comp.f\)"):
        _lower(src)


# --- coroutine keys (O5) ----------------------------------------------------

_TWO = """
import std_pkg::*;
component a_c { action S { exec body { message(NONE, "a"); } } }
component b_c { action S { exec body { message(NONE, "b"); } } }
component pss_top {
    a_c a; b_c b;
    action T { activity { do a_c::S; do b_c::S; } }
}
"""


def test_two_components_may_declare_actions_of_one_name():
    module = PSSToScenarioPass(root="pss_top", exports=["T"]).lower(_translate(_TWO))
    assert set(module.coroutines) == {"T", "a_c::S", "b_c::S"}
    assert trace(_TWO) == ["a", "b"]


def test_an_export_or_entry_names_an_action_simply_when_unambiguous():
    ctx = _translate(_TWO)
    for name in ("T", "pss_top::T"):
        module = PSSToScenarioPass(root="pss_top", exports=[name]).lower(ctx)
        assert module.export_actions == ["T"]
    module = PSSToScenarioPass(root="pss_top", exports=["a_c::S"]).lower(ctx)
    assert module.export_actions == ["a_c::S"]
    with pytest.raises(UnsupportedConstructError, match="'S' is ambiguous"):
        PSSToScenarioPass(root="pss_top", exports=["S"]).lower(ctx)
    module = PSSToScenarioPass(root="pss_top", exports=["T"]).lower(ctx)
    with pytest.raises(LoweringError, match="'S' is ambiguous"):
        lower_module(module, entry_action="S")


def test_a_non_root_action_runs_as_the_entry_in_its_instance():
    src = """
import std_pkg::*;
component sub_c {
    int k = 4;
    action S { exec body { message(NONE, "%d", comp.k); } }
}
component pss_top { sub_c s; action T { activity { do sub_c::S; } } }
"""
    assert trace(src, root="pss_top::T") == ["4"]
    module = PSSToScenarioPass(root="pss_top", exports=["sub_c::S"]).lower(_translate(src))
    lines = []
    run_model(lower_module(module, entry_action="sub_c::S"), seed=0,
              solve_backend=NativeBlobBackend(), out=lines.append)
    assert lines == ["4"]
