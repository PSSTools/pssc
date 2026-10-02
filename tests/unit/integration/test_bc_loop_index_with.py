"""A loop's index variable in a ``with`` on bc.

``repeat (i : N) { do A with { val == i; }; }``: the ``with`` holds at each
iteration's traversal with that iteration's ``i``. The traversed node is one
node reset on each iteration, so the index is a variable of that node: free
before its traversal (the parent's lookahead sees any value), pinned to the
loop's counter when the node is solved, and committed with the node's values
after.
"""
import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower
from .test_lookahead import _HDR

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _runs(src, seeds=range(20)):
    model = _lower(_HDR + src + "\n}\n")
    out = []
    for seed in seeds:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(), out=lines.append)
        out.append([int(v) for ln in lines for v in ln.split()])
    return out


def test_repeat_index():
    got = _runs("""
    action T { activity { repeat (i : 6) { do A with { val == i + 2; }; } } }""")
    assert all(r == [2, 3, 4, 5, 6, 7] for r in got)


def test_unlabeled_replicate_index():
    got = _runs("""
    action T { activity { replicate (j : 4) { do A with { val == 3 * j; }; } } }""")
    assert all(r == [0, 3, 6, 9] for r in got)


def test_nested_loops():
    got = _runs("""
    action T { activity {
        repeat (i : 3) { repeat (j : 2) { do A with { val == 4 * i + j; }; } }
    } }""")
    assert all(r == [0, 1, 4, 5, 8, 9] for r in got)


def test_the_index_shadows_an_attribute_of_the_same_name():
    got = _runs("""
    action T {
        rand bit[4] i;
        constraint i == 15;
        activity { repeat (i : 3) { do A with { val == i; }; } }
    }""")
    assert all(r == [0, 1, 2] for r in got)


def test_an_inequality_leaves_room():
    got = _runs("""
    action T { activity { repeat (i : 8) { do A with { val > i; }; } } }""", range(40))
    for r in got:
        assert all(v > i for i, v in enumerate(r))
    assert len({tuple(r) for r in got}) > 10


def test_in_parallel_branches():
    """The pattern `bench_par` uses: each branch its own loop and counter."""
    got = _runs("""
    action T { activity { parallel {
        repeat (i : 3) { do A with { val == i; }; }
        repeat (i : 3) { do A with { val == 10 + i; }; }
    } } }""")
    for r in got:
        assert sorted(v for v in r if v < 10) == [0, 1, 2]
        assert sorted(v for v in r if v >= 10) == [10, 11, 12]


def test_with_a_handle_and_a_parent_constraint():
    """The parent's constraint over the node and the `with` hold together."""
    got = _runs("""
    action T {
        A a;
        activity { repeat (i : 5) { a with { val >= i; }; } }
        constraint a.val <= 4;
    }""", range(40))
    for r in got:
        assert all(i <= v <= 4 for i, v in enumerate(r))
