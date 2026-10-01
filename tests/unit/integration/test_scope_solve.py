"""The activation's solve scope on bc (P1.4): `with`, activity constraints,
reads of sub-action attributes, and the error when nothing satisfies a
traversal.

A ``with`` holds at its own traversal (13.1.4) and is lookahead before it; an
activity ``constraint`` holds while its block is in force (13.1.9 b.3); a
sub-action's attributes are readable once it has been traversed (13.4.8). Each
is a constraint of the action tree's cone (``ScActionTree.cones``).
"""
import pytest

from zuspec.be.bc.interp import VMError

from .test_activity_bc_runs import trace
from .test_lookahead import runs, unsat_seeds

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


_WITH_PARENT = """
    action T {
        rand bit[4] px;
        A a1;
        activity { a1 with { val < this.px; }; do A with { val == this.px; }; }
        exec post_solve { message(NONE, "%u", px); }
    }"""

_ACT_CONSTRAINT = """
    action T {
        A a, b;
        activity { a; { b; constraint { b.val > a.val; } } }
    }"""


def test_with_constrains_its_traversal_and_the_parent_looks_ahead():
    """`this.px` is the parent's (13.1.4): px is chosen so both traversals
    have a value -- the first needs val < px, so px > 0."""
    got = runs(_WITH_PARENT)
    for px, v1, v2 in got:
        assert v1 < px and v2 == px


def test_a_with_holds_at_its_traversal_only():
    """The second block resets `a` (13.4.8) and traverses it with no `with`:
    its value is free again."""
    got = runs("""
    action T {
        A a;
        activity { { a with { val == 3; }; } { a; } }
    }""", range(40))
    assert all(first == 3 for first, _ in got)
    assert len({second for _, second in got}) > 3


def test_an_activity_constraint_holds_in_its_block():
    got = runs(_ACT_CONSTRAINT)
    for a, b in got:
        assert b > a
    assert max(a for a, _ in got) <= 14         # lookahead: room for b


@pytest.mark.parametrize("src", [_WITH_PARENT, _ACT_CONSTRAINT],
                         ids=["with_parent", "activity_constraint"])
def test_without_lookahead_the_lookahead_tests_fail_on_some_seed(src):
    """Calibration (P1.6): see ``test_lookahead.py``."""
    assert unsat_seeds(src) > 0


def test_an_activity_constraint_in_an_untaken_branch_is_not_in_force():
    got = runs("""
    action T {
        A a, b;
        activity {
            a;
            if (a.val > 7) { b; constraint { b.val == 0; } }
            else { b; constraint { b.val == 15; } }
        }
    }""")
    for a, b in got:
        assert b == (0 if a > 7 else 15)


def test_a_sub_action_attribute_is_read_after_its_traversal():
    """`if (a.val > 7)` reads a's slot of the activation's object."""
    got = runs("""
    action T {
        A a, arr[2];
        activity {
            a;
            arr[1] with { val == a.val; };
            if (arr[1].val > 7) { do A with { val == 0; }; }
            else { do A with { val == 15; }; }
        }
    }""")
    for a, e, last in got:
        assert e == a and last == (0 if a > 7 else 15)


def test_no_values_is_an_error_naming_the_traversal_and_constraints():
    with pytest.raises(VMError, match=r"traversal '#0' \(pss_top::A\).*#0\.val > 20"):
        trace("""
import std_pkg::*;
component pss_top {
    action A { rand bit[4] val; }
    action T { activity { do A with { val > 20; }; } }
}
""")


def test_a_pinned_value_that_leaves_nothing_is_an_error():
    """b's `with` is lookahead for a: a.val > b.val == 15 has no a.val."""
    with pytest.raises(VMError, match=r"traversal 'a'.*a\.val > b\.val; b\.val == 15"):
        trace("""
import std_pkg::*;
component pss_top {
    action A { rand bit[4] val; }
    action T { A a, b; constraint a.val > b.val; activity { a; b with { val == 15; }; } }
}
""")


def test_ex84_initializers_apply_declaration_first_then_traversal():
    """11.3.1 b i-ii, Ex 84: initial values, then the handle's initializers,
    then the traversal's; pre_solve sees them, and a later traversal of the
    type has its own initial values."""
    src = """
import std_pkg::*;
component pss_top {
    action A { rand bit knob; bit[4] a = 7; bit[4] b = 9;
        exec pre_solve { message(NONE, "pre %u %u", a, b); }
        exec body { message(NONE, "%u %u %u", a, b, knob); } }
    action T {
        rand bool sel;
        A a_h {.a=1, .b=2};
        activity {
            if (sel) { a_h {.a=3} with {knob == 0;}; }
            else { a_h {.b=1} with {knob == 1;}; }
            do A;
        }
    }
}
"""
    seen = set()
    for seed in range(16):
        got = trace(src, seed=seed)
        assert got[0] in ("pre 3 2", "pre 1 1")
        seen.add(got[0])
        assert got[1] == {"pre 3 2": "3 2 0", "pre 1 1": "1 1 1"}[got[0]]
        assert got[2] == "pre 7 9"
    assert len(seen) == 2


def test_a_labeled_replicate_runs_each_iteration_on_its_own_node():
    """Each iteration's `do A` is its own node (R[0].#0, R[1].#0, ...); its
    `with` sees the index variable of that iteration."""
    got = runs("""
    action T {
        activity { replicate (j: 3) R[]: { do A with { val == j + 4; }; } }
    }""", range(3))
    assert got == [[4, 5, 6]] * 3


def test_a_handle_traversed_again_in_its_scope_with_a_with_is_refused():
    """O-P1-3: `a; a with {...};` in one block is a located error; in a
    block of its own (a new scope entry, 13.4.8) it is not."""
    from zuspec.ir.core.xf.validate import UnsupportedConstructError
    from .test_activity_bc_runs import _lower
    src = """
import std_pkg::*;
component pss_top {
    action A { rand bit[4] val; }
    action T { A a; activity { a; %s } }
}
"""
    with pytest.raises(UnsupportedConstructError, match="traversed again") as ei:
        _lower(src % "a with { val == 1; };")
    assert ei.value.loc is not None and ei.value.loc.line == 5
    _lower(src % "{ a with { val == 1; }; }")
    _lower(src % "a;")
