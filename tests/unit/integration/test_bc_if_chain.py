"""`if` / `else if` chains on bc: one flat `ir.StmtIfChain`, lowered as a
run of tests, each taken body jumping past the rest (if-chain-plan.md).

A clause's test runs only when every earlier one was false, so a call in a
later test is not made when an earlier clause is taken; the final `else`
runs when none is; an `else` belongs to the nearest `if`.
"""
from .test_activity_bc_runs import trace

_SRC = """\
import std_pkg::*;
function int pick(int x) {
    int y = -1;
%s
    return y;
}
function int f(int a, int b) {
    int r = 0;
    if (a == 1) if (b == 1) r = 1; else if (b == 2) r = 3; else r = 2;
    return r;
}
function bool seen(int k) { message(NONE, "seen %%d", k); return k == 5; }
function int g(int x) {
    if (x == 0) { return 100; }
    else if (seen(x)) { return 200; }
    else if (x == 2) { return 300; }
    return 400;
}
component pss_top {
    action T {
        exec body { %s }
    }
}
"""


def _chain(n):
    links = [f"if (x == {i}) {{ y = {i * 3}; }}" for i in range(n)]
    links.insert(8, "if (x == 7) { y = 0; }")    # the first `x == 7` wins
    return "    " + "\n    else ".join(links)


def test_the_first_true_clause_runs_else_the_final_else():
    assert trace(_SRC % (_chain(600), 'message(NONE, "%d %d %d %d", '
                 'pick(0), pick(7), pick(599), pick(600));')) \
        == ["0 21 1797 -1"]


def test_a_later_test_runs_only_when_reached():
    assert trace(_SRC % (_chain(2), 'message(NONE, "%d", g(0)); '
                 'message(NONE, "%d", g(5)); message(NONE, "%d", g(2));')) \
        == ["100", "seen 5", "200", "seen 2", "300"]


def test_an_else_belongs_to_the_nearest_if():
    assert trace(_SRC % (_chain(2), 'message(NONE, "%d %d %d %d", '
                 'f(1, 1), f(1, 2), f(1, 9), f(0, 2));')) == ["1 3 2 0"]
