"""Procedural statements reach the IR whole (LRM 20.7) -- plan P2.

Each row is a function body and the IR ast2ir must make of it, rendered by
`_ir.shape`. The rendering is complete, so a sub-statement or expression
ast2ir drops shows up as a difference, never as a pass.

These replace `test_stmt_translation.py`, whose PSS contained none of the
statements its tests were named after.
"""
from __future__ import annotations

import pytest

from ._ir import body, in_function, shape, translate

_DECLS = """  array<int, 3> w;
  list<int> l;
  map<int, bit[8]> mp;
  set<int> st;
  int fld;
  function int g(int a) { return a + 1; }"""

ROWS = {
    "if, unbraced, else-if chain": ("int x;", """
    if (n > 0) x = 1;
    if (n > 0) { x = 1; } else if (n < 0) { x = 2; } else { x = 3; }""", [
        "If(Bin(self.n, Gt, 0), [Assign([x], 1)])",
        "IfChain([IfClause(Bin(self.n, Gt, 0), [Assign([x], 1)]), "
        "IfClause(Bin(self.n, Lt, 0), [Assign([x], 2)])], [Assign([x], 3)])"]),

    # repeat (n) and repeat (i : n) are one node; the index is its target.
    "repeat, counted and indexed": ("int x;", """
    repeat (3) { x += 1; }
    repeat (i : n) { x = i; }""", [
        "For(None, 3, [AugAssign(x, Add, 1)])",
        "For(i, self.n, [Assign([x], i)])"]),

    "repeat-while": ("int x;", """
    repeat { x -= 1; } while (x > 0);""", [
        "RepeatWhile(Bin(x, Gt, 0), [AugAssign(x, Sub, 1)])"]),

    "while with continue and break": ("int x;", """
    while (x < 5) { if (x == 2) { continue; } x += 1; if (x == 4) break; }""", [
        "While(Bin(x, Lt, 5), [If(Bin(x, Eq, 2), [Continue()]), "
        "AugAssign(x, Add, 1), If(Bin(x, Eq, 4), [Break()])])"]),

    # The index form names its index as both target and index_var; the value
    # forms name the element as the target (20.7.8).
    "foreach: index, value and index, value": ("int x;", """
    foreach (w[i]) { x = w[i]; }
    foreach (v : w[j]) { x = v + j; }
    foreach (e : w) { x = e; }""", [
        "Foreach(i, self.w, [Assign([x], Subscript(self.w, i))], i)",
        "Foreach(v, self.w, [Assign([x], Bin(v, Add, j))], j)",
        "Foreach(e, self.w, [Assign([x], e)])"]),

    # Over a list, a set, and a map: a map's index is its KEY (20.7.8).
    "foreach over a list, a set and a map": ("int x;", """
    foreach (v : l) { x = v; }
    foreach (e : st) { x = e; }
    foreach (b : mp[k]) { x = b + k; }""", [
        "Foreach(v, self.l, [Assign([x], v)])",
        "Foreach(e, self.st, [Assign([x], e)])",
        "Foreach(b, self.mp, [Assign([x], Bin(b, Add, k))], k)"]),

    # break/continue bind to the innermost loop; return leaves them all.
    "break, continue and return in nested loops": ("", """
    repeat (i : 3) { repeat (j : 2) { if (j == 1) { break; } continue; } }
    while (n < 3) { repeat (k : n) { if (k == 2) { return; } } }""", [
        "For(i, 3, [For(j, 2, [If(Bin(j, Eq, 1), [Break()]), Continue()])])",
        "While(Bin(self.n, Lt, 3), [For(k, self.n, [If(Bin(k, Eq, 2), "
        "[Return()])])])"]),

    # Values, several values (PatternOr), a range, and default (`_`).
    # `[6..8]` is a range, never the single value 6.
    "match: values, several, a range, default": ("int x;", """
    match (x) { [1]: x = 2; [3, 4]: x = 5; [6..8]: x = 0; default: x = 1; }""", [
        "Match(x, [MatchCase(PatternValue(1), [Assign([x], 2)]), "
        "MatchCase(PatternOr([PatternValue(3), PatternValue(4)]), "
        "[Assign([x], 5)]), "
        "MatchCase(PatternValue(Range(6, 8)), [Assign([x], 0)]), "
        "MatchCase(PatternAs('_'), [Assign([x], 1)])])"]),

    "match on enum items": ("int x;\n    m_e m = M1;", """
    match (m) { [M0]: x = 1; [M1, M2]: x = 2; }""", [
        "Match(m, [MatchCase(PatternValue(m_e::M0=0), [Assign([x], 1)]), "
        "MatchCase(PatternOr([PatternValue(m_e::M1=1), "
        "PatternValue(m_e::M2=2)]), [Assign([x], 2)])])"]),

    # Every compound assignment PSS has (8.1 assign_op).
    "assignment operators": ("int x;", """
    x = 5; x += 1; x -= 1; x <<= 1; x >>= 1; x |= 1; x &= 3;""", [
        "Assign([x], 5)", "AugAssign(x, Add, 1)", "AugAssign(x, Sub, 1)",
        "AugAssign(x, LShift, 1)", "AugAssign(x, RShift, 1)",
        "AugAssign(x, BitOr, 1)", "AugAssign(x, BitAnd, 3)"]),

    # A nested block's local is renamed where it would shadow (20.7.1).
    "a nested block shadowing a local": ("int x = 1;", """
    { int x = 7; fld = x; }
    fld = x;""", [
        "AnnAssign(x__s1, int[32], 7)", "Assign([self.fld], x__s1)",
        "Assign([self.fld], x)"]),

    "compile if, taken and not": ("int x;", """
    compile if (1 == 1) { x = 3; } else { x = 4; }
    compile if (1 == 2) { x = 5; }""", [
        "Assign([x], 3)"]),

    "a call, a field and this.field": ("int x;", """
    x = g(n);
    fld = x;
    this.fld = x;""", [
        "Assign([x], Call(self.g, [self.n]))", "Assign([self.fld], x)",
        "Assign([self.fld], x)"]),

    "yield and return": ("", """
    yield;
    return;""", ["Yield()", "Return()"]),
}


@pytest.mark.parametrize("name", sorted(ROWS))
def test_statement(tmp_path, name):
    locals_, stmts, want = ROWS[name]
    src = "enum m_e { M0, M1, M2 }\n" + in_function(
        _DECLS, f"    {locals_}{stmts}", params="int n")
    got = [shape(s) for s in body(tmp_path, src)]
    n_locals = locals_.count(";")
    assert got[n_locals:] == want


def test_a_value_return(tmp_path):
    src = """component pss_top {
  function int h(int a) { if (a > 0) { return a; } return -a; }
}"""
    assert [shape(s) for s in body(tmp_path, src, fn="h")] == [
        "If(Bin(self.a, Gt, 0), [Return(self.a)])",
        "Return(Unary(USub, self.a))"]


def test_super_in_an_action_body(tmp_path):
    """`super;` runs the base action's body there (17.1)."""
    src = """component pss_top {
  action B { exec body { int x = 1; } }
  action D : B { exec body { super; int y = 2; } }
}"""
    assert [shape(s) for s in body(tmp_path, src, fn="body",
                                   comp="pss_top::D")] == [
        "Super()", "AnnAssign(y, int[32], 2)"]


def test_randomize_is_refused_with_its_line(tmp_path):
    """`randomize` has no IR form (20.7.12); it is refused, not dropped."""
    ctx = translate(tmp_path, """component pss_top {
  function void r() {
    int y;
    randomize y;
  }
}""")
    assert len(ctx.errors) == 1
    assert "line 5" in ctx.errors[0] and "Randomize" in ctx.errors[0]
