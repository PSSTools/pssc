"""Activity symbols on bc (LRM 11.7; open item O2).

A symbol call has the same effect as writing the symbol's body where the call
is: ast2ir expands the body at each call, as a block of its own, with each
parameter replaced by the call's argument. So bc, the action tree and the
cones never see a symbol, and every expansion has its own traversal sites and
labels. A symbol may call another, but not itself.
"""
import os
import re
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator

from .test_lookahead import _HDR, runs, unsat_seeds

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_BC = """
    action B { rand bit[4] val; exec body { message(NONE, "%u", 100 + val); } }
    action C { rand bit[4] val; exec body { message(NONE, "%u", 200 + val); } }"""


def _errors(src: str):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, (_HDR + src + "\n}\n").encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link(), files=parser.file_map).errors
    finally:
        os.unlink(fname)


def _kind(v: int) -> str:
    return "ABC"[v // 100]


_EX120 = _BC + """
    action T {
        A a1, a2, a3; B b1, b2, b3; C c1, c2, c3;
        symbol a_or_b { select { a1; b1; } select { a2; b2; } select { a3; b3; } }
        activity { a_or_b; c1; c2; c3; }
    }"""

_EX121 = _BC + """
    action T {
        A a1, a2; B b1, b2; C c1;
        symbol ab_or_ba(A aa, B bb) { select { { aa; bb; } { bb; aa; } } }
        activity { ab_or_ba(a1, b1); ab_or_ba(a2, b2); c1; }
    }"""


def _kinds(got):
    return ["".join(_kind(v) for v in r) for r in got]


def test_ex120_a_symbol_is_its_body_where_it_is_named():
    """`a_or_b;` -- no argument list, so it parses as a traversal the linker
    resolved to the symbol -- is three selects, then c1..c3."""
    for k in _kinds(runs(_EX120, range(40))):
        assert all(c in "AB" for c in k[:3]) and k[3:] == "CCC"


def test_ex121_a_handle_parameter_traverses_its_argument():
    """`ab_or_ba(a1, b1)` runs a1 and b1 in either order; each call its own."""
    for k in _kinds(runs(_EX121, range(40))):
        assert k[:2] in ("AB", "BA") and k[2:4] in ("AB", "BA") and k[4] == "C"


@pytest.mark.xfail(strict=True, reason=(
    "be-bc SeedStream.next_below is `lcg % n` on a 2**64 LCG, whose low bit "
    "alternates: after a run's first select, each two-way choice is the "
    "opposite of the one before (determinism.py; not a symbol defect)"))
@pytest.mark.parametrize("src,n", [(_EX120, 8), (_EX121, 4)], ids=["ex120", "ex121"])
def test_every_choice_of_the_selects_is_reached(src, n):
    assert len({k[:3] if n == 8 else k[:4] for k in _kinds(runs(src, range(80)))}) == n


def test_a_value_parameter_is_its_argument():
    """`n` is 2, then 3: the repeat count and the `with` both read it, and
    an argument naming a field of the caller reads the caller's."""
    got = runs("""
    action T {
        rand bit[4] m;
        constraint { m == 5; }
        symbol times(int n) { repeat (n) { do A with { val == n; }; } }
        activity { times(2); times(3); times(m + 1); }
    }""", range(4))
    assert all(r == [2, 2, 3, 3, 3, 6, 6, 6, 6, 6, 6] for r in got)


_LT = """
    action T {
        A a, b;
        symbol lt(A x, A y) { x; y; constraint { x.val + 10 < y.val; } }
        activity { lt(a, b); }
    }"""


def test_a_constraint_in_a_symbol_binds_the_arguments_and_looks_ahead():
    """`x.val + 10 < y.val` is `a.val + 10 < b.val`: a.val is chosen with
    room for b.val, before b is traversed."""
    for a, b in runs(_LT):
        assert a + 10 < b


def test_without_lookahead_the_symbol_constraint_fails_on_some_seed():
    """Calibration (P1.6): the test above tests lookahead."""
    assert unsat_seeds(_LT) > 0


def test_symbols_nest_and_each_argument_is_the_callers():
    """`one(q, v + 1)` inside `two`: q is two's parameter, so it is `a`."""
    got = runs(_BC + """
    action T {
        A a, b;
        symbol one(A h, int v) { h with { val == v; }; }
        symbol two(A p, A q, int v) { one(p, v); one(q, v + 1); }
        activity { two(b, a, 7); do C with { val == a.val; }; }
    }""", range(3))
    assert all(r == [7, 8, 208] for r in got)


def test_an_array_element_argument_and_an_index_parameter():
    got = runs(_BC + """
    action T {
        A arr[3];
        symbol at(A h) { h with { val == 9; }; }
        symbol idx(int i) { arr[i] with { val == i; }; }
        activity { at(arr[1]); idx(2);
                   do C with { val == arr[1].val; }; do C with { val == arr[2].val; }; }
    }""", range(3))
    assert all(r == [9, 2, 209, 202] for r in got)


def test_each_expansion_has_its_own_labels_and_handles():
    """The symbol's `l1`, `l2` and `h` are the expansion's: two calls are two
    sets of nodes, and `l1.val > l2.val` holds within each."""
    got = runs("""
    action T {
        symbol s(int v) {
            l1: do A; l2: do A; constraint { l1.val > l2.val; }
            A h; h with { val == v; };
        }
        activity { s(4); s(5); }
    }""")
    for r in got:
        assert r[0] > r[1] and r[2] == 4 and r[3] > r[4] and r[5] == 5


def test_a_labeled_call_a_later_declaration_and_an_inherited_symbol():
    got = runs("""
    action Base { symbol inh(A h) { h with { val == 2; }; } }
    action T : Base {
        A a, b;
        activity { L: fwd(a); inh(b); }
        symbol fwd(A h) { h with { val == 3; }; }
    }""", range(3))
    assert all(r == [3, 2] for r in got)


def test_a_symbol_in_a_sub_action_is_in_its_parents_cone():
    """S's symbol constrains s1.a < s1.b; T ties v to s1.b, so v.val is
    chosen above 0 before s1 is traversed."""
    for v, a, b in runs("""
    action S {
        A a, b;
        symbol lt(A x, A y) { x; y; constraint { x.val < y.val; } }
        activity { lt(a, b); }
    }
    action T { A v; S s1; constraint { v.val == s1.b.val; } activity { v; s1; } }"""):
        assert a < b == v


def test_a_labeled_traversal_is_a_name_in_its_block():
    """`l1: do A;` names its action (11.8) -- not only in a symbol."""
    for l1, l2 in runs("""
    action T { activity { l1: do A; l2: do A with { val < l1.val; }; } }"""):
        assert l2 < l1


@pytest.mark.parametrize("src,pattern", [
    ("""
    action T {
        symbol s1(int n) { s2(n); }
        symbol s2(int n) { s1(n); }
        activity { s1(1); }
    }""", r"line 7: symbol 's1' activates itself \(s1 -> s2 -> s1\); "
          r"symbols are not recursive"),
    ("""
    action T { symbol s(A h) { h; } activity { s(3); } }""",
     r"line 5: the argument for this symbol parameter is not an action handle"),
    # A `with` that names anything stops in pssparser first: it does not
    # resolve names in a `with` on a symbol, and reports them as unbound.
    ("""
    action T { symbol s { do A; } activity { s with { 1 < 2; }; } }""",
     r"line 5: symbol 's' is not an action: it takes no 'with'"),
], ids=["recursive", "not_a_handle", "with_on_a_symbol"])
def test_refused_with_a_location(src, pattern):
    errs = _errors(src)
    assert any(re.search(pattern, e) for e in errs), errs
