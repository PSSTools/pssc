"""The 8.7 reference model against the LRM's own worked examples.

`ref8_7` gives the expression grid its expected values; if it is wrong, every
target is held to the wrong number. So it is checked here against what the
LRM states outright, with no target involved.
"""
from __future__ import annotations

from .ref8_7 import (Bin, Cast, Cond, T, Un, Var, convert, evaluate, hex_at,
                     self_value)

B2, B8, B12, B16, B32 = T(2, False), T(8, False), T(12, False), \
    T(16, False), T(32, False)
I3, I8, I32 = T(3, True), T(8, True), T(32, True)


def test_example_42_size_propagation():
    x, y = Var("x", B8, 0xF0), Var("y", B12, 0xFF0)
    v, t = self_value(Bin("+", x, y))
    assert hex_at(v, t) == "e0"                 # print("%x", x+y)
    assert convert(Bin("+", x, y), B16) == 0x10E0   # bit[16] z = x+y


def test_example_43_signedness_propagation():
    x, y = Var("x", B32, 1), Var("y", I32, -10)
    assert self_value(Bin("<", x, y))[0] == 1   # "x is smaller than y"


def test_example_44_integer_part():
    x, y, z = Var("x", B2, 3), Var("y", B2, 2), Var("z", B32, 10)
    assert self_value(Bin("+", Bin("+", x, y), z))[0] == 15


def test_example_45_in_operands_at_a_common_type():
    # c + d with c an int[3] -4 and d a bit[2] 2, met at bit[3]: 4 + 2 = 6.
    c, d = Var("c", I3, -4), Var("d", B2, 2)
    assert evaluate(Bin("+", c, d), T(3, False)) == 6


def test_assignment_propagates_width_not_sign():
    v = Var("v", I8, -1)
    assert convert(v, B32) == 0xFFFFFFFF        # source signed: sign-extend
    u = Var("u", B8, 0xFF)
    assert convert(u, I32) == 255               # source unsigned: zero-extend
    # Mixed signedness is unsigned: the int[8] is zero-extended.
    assert convert(Bin("+", v, Var("z", B8, 0)), B32) == 0xFF


def test_a_wider_target_propagates_into_the_expression():
    a = Var("a", B8, 0x80)
    assert convert(Bin("+", a, a), B16) == 0x100
    assert convert(Bin(">>", Bin("+", a, a), Var("n", B8, 1)), B16) == 0x80
    assert self_value(Bin("+", a, a))[0] == 0
    # A narrower target does not: the source is self-determined, truncated.
    assert convert(Un("~", Var("x", B32, 1)), B8) == 0xFE


def test_shifts_by_the_width_or_more_ruling_r1():
    x = Var("x", B8, 0x81)
    assert self_value(Bin("<<", x, Var("n", B8, 8)))[0] == 0
    assert self_value(Bin(">>", x, Var("n", B8, 9)))[0] == 0
    s = Var("s", I8, -128)
    assert self_value(Bin(">>", s, Var("n", B8, 20)))[0] == -1
    assert self_value(Bin(">>", Var("p", I8, 64), Var("n", B8, 20)))[0] == 0


def test_division_truncates_toward_zero():
    assert self_value(Bin("/", Var("a", I32, -7), Var("b", I32, 2)))[0] == -3
    assert self_value(Bin("%", Var("a", I32, -7), Var("b", I32, 2)))[0] == -1
    assert self_value(Bin("%", Var("a", I32, 7), Var("b", I32, -2)))[0] == 1
    assert self_value(Bin("/", Var("a", B8, 0xFE), Var("b", B8, 2)))[0] == 127


def test_power_table_13_and_its_type():
    two, n = Var("t", I32, 2), Var("n", I32, 31)
    assert self_value(Bin("**", two, n))[0] == -(1 << 31)
    assert self_value(Bin("**", Var("m", I32, -1), Var("e", I32, -3)))[0] == -1
    assert self_value(Bin("**", two, Var("e", I32, -1)))[0] == 0
    # The result takes the LEFT operand's type; the right is self-determined.
    assert self_value(Bin("**", Var("b", B8, 3), Var("e", T(64, False), 5)))[0] \
        == 243 % 256


def test_casts_and_the_conditional():
    u = Var("u", B8, 200)
    assert self_value(Cast(I8, u))[0] == -56
    assert convert(Cast(I8, u), B32) == 0xFFFFFFC8
    # The arms meet at bit[16], unsigned: the int[8] -1 is ZERO-extended.
    c = Var("c", T(1, False), 1)
    assert self_value(Cond(c, Var("a", I8, -1), Var("b", B16, 0)))[0] == 0xFF
