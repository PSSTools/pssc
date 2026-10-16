"""Expressions reach the IR whole (LRM 8) -- plan P2.

Each row is one assignment's right-hand side (or target) and the IR ast2ir
must make of it, rendered by `_ir.shape`. These replace
`test_expr_translation.py`, whose PSS contained none of the expressions its
tests were named after.

Rows that ast2ir gets WRONG today are strict xfails naming the defect
(docs/design/construct-test-audit/00-plan.md §2).
"""
from __future__ import annotations

import pytest

from ._ir import body, in_function, shape, translate

_DECLS = """  array<int, 3> w;
  list<int> l;
  string s;
  function int g(int a) { return a + 1; }
  function array<int, 3> ga(int a) { array<int, 3> r; return r; }
  function p_s gs(int a) { p_s r; return r; }"""
_LOCALS = "int x; bool c; bit[64] u; p_s q; m_e m; string z;"
_PRE = """enum m_e { M0, M1, M2 }
struct p_s { bit[8] x; bit[8] y = 3; }
"""

#: statement -> its IR. Parameters: int n, bit[8] b, bool t.
ROWS = {
    # 8.5.1 arithmetic, 8.5.5 bitwise, 8.5.7 shifts
    "x = n + 1;": "Assign([x], Bin(self.n, Add, 1))",
    "x = n - 1;": "Assign([x], Bin(self.n, Sub, 1))",
    "x = n * 2;": "Assign([x], Bin(self.n, Mult, 2))",
    "x = n / 2;": "Assign([x], Bin(self.n, Div, 2))",
    "x = n % 2;": "Assign([x], Bin(self.n, Mod, 2))",
    "x = 2 ** n;": "Assign([x], Bin(2, Exp, self.n))",
    "x = n & 1;": "Assign([x], Bin(self.n, BitAnd, 1))",
    "x = n | 1;": "Assign([x], Bin(self.n, BitOr, 1))",
    "x = n ^ 1;": "Assign([x], Bin(self.n, BitXor, 1))",
    "x = n << 2;": "Assign([x], Bin(self.n, LShift, 2))",
    "x = n >> 2;": "Assign([x], Bin(self.n, RShift, 2))",
    # 8.5.2-8.5.4
    "c = n < 1;": "Assign([c], Bin(self.n, Lt, 1))",
    "c = n <= 1;": "Assign([c], Bin(self.n, LtE, 1))",
    "c = n > 1;": "Assign([c], Bin(self.n, Gt, 1))",
    "c = n >= 1;": "Assign([c], Bin(self.n, GtE, 1))",
    "c = n == 1;": "Assign([c], Bin(self.n, Eq, 1))",
    "c = n != 1;": "Assign([c], Bin(self.n, NotEq, 1))",
    "c = t && c;": "Assign([c], Bin(self.t, And, c))",
    "c = t || c;": "Assign([c], Bin(self.t, Or, c))",
    "c = !t;": "Assign([c], Unary(Not, self.t))",
    "x = ~n;": "Assign([x], Unary(Invert, self.n))",
    "x = -n;": "Assign([x], Unary(USub, self.n))",
    # 8.4.1 precedence: * before +, << after +, left-associative `-`
    "x = 1 + 2 * n;": "Assign([x], Bin(1, Add, Bin(2, Mult, self.n)))",
    "x = n << 1 + 1;": "Assign([x], Bin(self.n, LShift, Bin(1, Add, 1)))",
    "x = n - 2 - 1;": "Assign([x], Bin(Bin(self.n, Sub, 2), Sub, 1))",
    # 8.5.8
    "x = t ? 1 : 2;": "Assign([x], IfExp(self.t, 1, 2))",
    "x = t ? 1 : c ? 2 : 3;": "Assign([x], IfExp(self.t, 1, IfExp(c, 2, 3)))",
    # 8.5.9: a single value is a range with no upper bound
    "c = n in [1, 3..5];":
        "Assign([c], In(self.n, RangeList([Range(1), Range(3, 5)])))",
    "c = n in w;": "Assign([c], In(self.n, self.w))",
    "c = n in l;": "Assign([c], In(self.n, self.l))",
    # 8.6.1 bit and part selects, read and written
    "x = b[3];": "Assign([x], Subscript(self.b, 3))",
    "x = b[7:4];": "Assign([x], Subscript(self.b, Slice(4, 7, True)))",
    "b[1:0] = 2;": "Assign([Subscript(self.b, Slice(0, 1, True))], 2)",
    "b[0] = 1;": "Assign([Subscript(self.b, 0)], 1)",
    # 7.12 casts
    "x = (int[8])b;": "Assign([x], Cast(int[8], self.b))",
    "x = (int)M1;": "Assign([x], Cast(int[32], m_e::M1=1))",
    "u = (bit[64])n;": "Assign([u], Cast(bit[64], self.n))",
    "m = (m_e)n;": "Assign([m], Cast(Enum('m_e', {'M0': 0, 'M1': 1, "
                   "'M2': 2}, 0), self.n))",
    # 8.6.2: a string's substring is a slice that is not a bit select
    "z = s[1..2];": "Assign([z], Subscript(self.s, Slice(1, 2, False)))",
    # 7.6: chandle and string comparisons with null and literals
    "c = s == null;": "Assign([c], Bin(self.s, Eq, Null()))",
    'c = z == "a";': "Assign([c], Bin(z, Eq, 'a'))",
    # 4.6.1 literals and their types (Table 21)
    "x = 8'hFF;": "Assign([x], 255'8u)",
    "x = 4'sb1010;": "Assign([x], -6'4s)",
    "x = 'h1F;": "Assign([x], 31'u)",
    "x = 0x10;": "Assign([x], 16'u)",
    "x = 017;": "Assign([x], 15)",
    "x = 'b101;": "Assign([x], 5'u)",
    "x = 16;": "Assign([x], 16)",
    "x = -3;": "Assign([x], Unary(USub, 3))",
    # 8.4.2 aggregate literals
    "q = {.x = 1, .y = 2};":
        "Assign([q], StructLiteral([StructField('x', 1), StructField('y', 2)]))",
    "w = {1, 2, 3};": "Assign([self.w], List([1, 2, 3]))",
    "w = {1, 2, {3}};": "Assign([self.w], List([1, 2, List([3])]))",
    # methods of a collection, a string, and a literal
    "x = l.size();": "Assign([x], Call(self.l.size))",
    "l.push_back(1);": "Expr(Call(self.l.push_back, [1]))",
    "x = s.size();": "Assign([x], Call(self.s.size))",
    'x = "abc".size();': "Assign([x], Call('abc'.size))",
    "x = {1, 2}.size();": "Assign([x], Call(List([1, 2]).size))",
    # references
    "x = w[n];": "Assign([x], Subscript(self.w, self.n))",
    "x = q.x;": "Assign([x], q.x)",
    "x = g(n);": "Assign([x], Call(self.g, [self.n]))",
    # an index of a call's result (IP-74): the call, then the index -- it
    # was `ga[2](n)` (I12)
    "x = ga(n)[2];": "Assign([x], Subscript(Call(self.ga, [self.n]), 2))",
    "x = gs(n).y;": "Assign([x], Call(self.gs, [self.n]).y)",
}

#: Rows ast2ir gets wrong today, and why (strict xfail).
XFAIL = {
}


def _one(tmp_path, stmt):
    src = _PRE + in_function(_DECLS, f"    {_LOCALS}\n    {stmt}",
                             params="int n, bit[8] b, bool t")
    got = body(tmp_path, src)
    assert len(got) == _LOCALS.count(";") + 1
    return shape(got[-1])


@pytest.mark.parametrize("stmt", [
    pytest.param(s, marks=[pytest.mark.xfail(strict=True, reason=XFAIL[s])]
                 if s in XFAIL else []) for s in ROWS])
def test_expression(tmp_path, stmt):
    assert _one(tmp_path, stmt) == ROWS[stmt]


def test_a_bool_cast_tests_for_nonzero(tmp_path):
    """`(bool)n` is `n != 0` (7.12.1 b). bool is `bit[1]` in the IR, so a
    cast to it would be the low bit (I5, the root of W14/W15)."""
    assert _one(tmp_path, "c = (bool)n;") == \
        "Assign([c], Bin(self.n, NotEq, 0))"


# Each of these was DROPPED by ast2ir with no error -- a None in the IR, a
# parameter removed from the signature (the arity changed), a result taken
# for `void` (I1-I3). Each is now refused where it stands, with its line.

@pytest.mark.parametrize("src, what", [
    pytest.param("float64 d = 1.5;", "local 'd' is floating point",
                 id="float local"),
    pytest.param("int i = 2; i = i * 2.0;", "expression is floating point",
                 id="float literal"),
])
def test_floating_point_in_a_body_is_refused(tmp_path, src, what):
    ctx = translate(tmp_path, in_function("", "    " + src))
    assert any(what in e and e.startswith("line ") for e in ctx.errors), \
        ctx.errors


@pytest.mark.parametrize("params, what", [
    pytest.param("int a, float32 b", "'b' of 'h' is floating point",
                 id="float"),
    pytest.param("int a, ref p_s b", "'b' of 'h' is a reference (`ref T`)",
                 id="ref"),
    pytest.param("int a, type T", "'T' of 'h' is a type parameter",
                 id="type"),
])
def test_a_parameter_is_never_dropped(tmp_path, params, what):
    ctx = translate(tmp_path, _PRE + f"""component pss_top {{
  function int h({params}) {{ return a; }}
}}""")
    assert any(what in e and e.startswith("line ") for e in ctx.errors), \
        ctx.errors


def test_a_float_result_is_refused_not_taken_for_void(tmp_path):
    ctx = translate(tmp_path, """component pss_top {
  function float64 h() { return 0; }
}""")
    assert any("result of 'h' is floating point" in e for e in ctx.errors), \
        ctx.errors


@pytest.mark.parametrize("op", ["&", "|", "^"])
def test_a_reduction_operator_is_refused_with_its_line(tmp_path, op):
    """No IR node exists for them (8.5.6); refused rather than approximated,
    and now located."""
    ctx = translate(tmp_path, in_function("", f"    int x = {op}n;",
                                          params="int n"))
    assert any(e.startswith("line 5: ") and
               f"bit-reduction operator '{op}'" in e for e in ctx.errors), \
        ctx.errors


def test_a_template_string_is_refused_not_taken_as_text(tmp_path):
    """A triple-quoted string's mustache expressions are expanded when it is
    evaluated (4.7.1); a TQS `v={{n}}` printed `v={{n}}` (I7)."""
    ctx = translate(tmp_path, in_function(
        "", '    string t = """v={{n}}""";', params="int n"))
    assert any(e.startswith("line 5: ") and "mustache" in e
               for e in ctx.errors), ctx.errors


def test_compile_has_in_a_body_is_refused(tmp_path):
    # Line 4: the prelude `translate` writes is line 1.
    ctx = translate(tmp_path, """component pss_top {
  int a;
  function bool h() { return compile has(a); }
}""")
    assert any("`compile has`" in e and e.startswith("line 4")
               for e in ctx.errors), ctx.errors
