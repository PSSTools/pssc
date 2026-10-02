"""A boolean as a condition on bc: `!b -> ...`, `b -> ...`, and `!e` in a
clause.

be-bc turns an implication into clauses: the antecedent's negation as a
disjunction of literals, OR-ed with each consequent clause. Only comparisons
were accepted, so `!rd_verify -> io.src_tag == 0` was refused. A bare value
holds when nonzero (its negation is `b == 0`), and `!e` negates through
(its negation is `e`'s own clause).

Each case is checked against every assignment of a small domain: the set of
values the solver produces over many seeds must be exactly the set that
satisfies the constraint.
"""
import itertools

import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

SEEDS = range(300)


def _seen(constraint: str):
    src = """import std_pkg::*;
component pss_top {
    action T {
        rand bool b, c;
        rand bit[2] x;
        constraint %s
        exec body { message(NONE, "%%d %%d %%u", (int)b, (int)c, x); }
    }
}""" % constraint
    model = _lower(src)
    out = set()
    for seed in SEEDS:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(), out=lines.append)
        out.add(tuple(int(v) for v in lines[0].split()))
    return out


def _all(pred):
    return {(b, c, x) for b, c, x in itertools.product((0, 1), (0, 1), range(4))
            if pred(bool(b), bool(c), x)}


@pytest.mark.parametrize("constraint,pred", [
    ("{ !b -> x == 0; }", lambda b, c, x: b or x == 0),
    ("{ b -> x == 3; }", lambda b, c, x: (not b) or x == 3),
    ("{ (!b && c) -> x == 1; }", lambda b, c, x: not ((not b) and c) or x == 1),
    ("{ !(x > 1) -> b; }", lambda b, c, x: x > 1 or b),
    ("{ x == 1 -> !b; }", lambda b, c, x: x != 1 or not b),
    ("{ !b -> !c; }", lambda b, c, x: b or not c),
    ("{ if (!b) { x == 0; } else { x == 3; } }",
     lambda b, c, x: x == (3 if b else 0)),
])
def test_the_values_are_exactly_the_satisfying_ones(constraint, pred):
    assert _seen(constraint) == _all(pred)


def test_a_negated_conjunction_as_antecedent_is_refused():
    """`!(b && c) -> e` is `(b || e) && (c || e)`: two clauses, which this
    translation does not build yet. Refused, not dropped."""
    from zuspec.be.bc.lower.errors import LoweringError
    with pytest.raises(LoweringError, match="disjunctive clause"):
        _seen("{ !(b && c) -> x == 2; }")
