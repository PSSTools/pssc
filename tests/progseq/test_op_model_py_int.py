"""PSS integer semantics in op-model-py (LRM 8.5, 8.7).

A Python int has no width and `//` floors. Every expected value here is worked
from the LRM rules, and each is DRIVEN -- the generated module is imported and
run -- because the claim is about values, not spelling. Where the claim IS
spelling (no wrap on a value that provably fits, no helper a model does not
need) the text is checked, and says why.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import itertools
import sys

import pytest

from pssc import driver
from pssc.targets.py import pss_int


def _compile(tmp_path, pss, target="op-model-py", **kw):
    src = tmp_path / "m.pss"
    src.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n" + pss)
    ns = argparse.Namespace(progseq_root="pss_top",
                            output_dir=str(tmp_path / "out"), **kw)
    driver.compile([str(src)], target=target, opts=ns)
    return tmp_path / "out"


def _load(out_dir, rt="pssc_rt"):
    sys.path.insert(0, str(out_dir))
    try:
        for name in ("pss_top", "pssc_rt", "pssc_rt_async"):
            sys.modules.pop(name, None)
        return (importlib.import_module("pss_top"),
                importlib.import_module(rt))
    finally:
        sys.path.remove(str(out_dir))


def _run(tmp_path, body, extra="", top_extra=""):
    """Run `g()` of a pss_top whose body is *body*; its messages."""
    mod, rt = _load(_compile(tmp_path, f"""
{top_extra}
component pss_top {{
  {extra}
  target function void g() {{
    {body}
  }}
}}"""))
    bus = rt.MemoryBus()
    mod.PssTop(bus).g()
    return [t for k, _, _, t in bus.log if k == "message"]


def _text(tmp_path, pss, **kw):
    return (_compile(tmp_path, pss, **kw) / "pss_top.py").read_text()


# --- division (8.5.1) --------------------------------------------------------

def test_division_truncates_toward_zero_and_modulus_takes_the_first_sign(
        tmp_path):
    assert _run(tmp_path, """
    int a = -7; int b = 2; int c = 7; int d = -2;
    message(NONE, "%d %d %d %d", a / b, a % b, c / d, c % d);
    message(NONE, "%d %d", a / d, a % d);""") == [
        "-3 -1 -3 1", "3 -1"]


def test_the_most_negative_value_divided_by_minus_one_wraps(tmp_path):
    """The one quotient outside its type; the result is its low 32 bits."""
    assert _run(tmp_path, """
    int m = -2147483647 - 1; int n = -1;
    int q = m / n;
    message(NONE, "%d", q);""") == ["-2147483648"]


def test_unsigned_division_is_the_python_operator(tmp_path):
    """Both operands are non-negative, where `//` and `%` ARE PSS's."""
    text = _text(tmp_path, """
component pss_top {
  bit[32] q;
  target function void g(bit[32] a, bit[32] b) { q = a / b + a % b; }
}""")
    assert "a // b" in text and "a % b" in text
    assert "_pss_div" not in text


def test_a_constant_division_by_zero_is_a_build_error(tmp_path):
    with pytest.raises(ValueError, match="division by zero"):
        _compile(tmp_path, """
component pss_top {
  int q;
  target function void g() { q = 1 / 0; }
}""")


# --- comparison (8.5.2) ------------------------------------------------------

def test_a_comparison_with_an_unsigned_operand_is_unsigned(tmp_path):
    """Example 43: -10 read as bit[32] is 4294967286."""
    assert _run(tmp_path, """
    bit[32] x = 1; int y = -10; int a = 1; int b = -10;
    message(NONE, "%n %n", x < y, a < b);""") == ["true false"]


def test_the_narrower_operand_is_zero_extended_when_unsigned(tmp_path):
    """int[8] -1 is 0xff; against a bit[16] the compare is unsigned at 16
    bits, so it is zero-extended to 0x00ff, not sign-extended to 0xffff."""
    assert _run(tmp_path, """
    int[8] a = -1; bit[16] b = 0xff; bit[16] c = 0xffff;
    message(NONE, "%n %n", a == b, a == c);""") == ["true false"]


def test_signed_operands_compare_signed(tmp_path):
    assert _run(tmp_path, """
    int[8] a = -1; int b = 1;
    message(NONE, "%n", a < b);""") == ["true"]


# --- widths (8.7) ------------------------------------------------------------

def test_an_assignment_lends_its_width_to_the_expression(tmp_path):
    """8.7.2: the sum is carried out at 16 bits, so it does not wrap at 8."""
    assert _run(tmp_path, """
    bit[8] a = 200; bit[8] b = 100;
    bit[16] c = a + b;
    bit[8]  d = a + b;
    message(NONE, "%d %d", c, d);""") == ["300 44"]


def test_a_self_determined_operand_wraps_at_its_own_width(tmp_path):
    """A shift has its left operand's type, so `(a + b) >> 1` is carried out
    at 8 bits unless an assignment widens it: 44 >> 1, or 300 >> 1."""
    assert _run(tmp_path, """
    bit[8] a = 200; bit[8] b = 100;
    bit[8]  e = (a + b) >> 1;
    bit[16] f = (a + b) >> 1;
    message(NONE, "%d %d", e, f);""") == ["22 150"]


def test_a_comparison_widens_both_sides(tmp_path):
    """255 is an int, so `a + b > 255` is carried out at 32 bits: 300."""
    assert _run(tmp_path, """
    bit[8] a = 200; bit[8] b = 100;
    message(NONE, "%n", a + b > 255);""") == ["true"]


def test_signed_overflow_wraps(tmp_path):
    assert _run(tmp_path, """
    int m = 2147483647;
    m = m + 1;
    message(NONE, "%d", m);""") == ["-2147483648"]


def test_mixed_signedness_arithmetic_is_unsigned(tmp_path):
    """bit[8] + int is bit[32]: -1 is 0xffffffff, and 200 + that wraps to
    199 -- which the int target then holds."""
    assert _run(tmp_path, """
    bit[8] a = 200; int b = -1;
    int t = a + b;
    bit[64] w = a + b;
    message(NONE, "%d %d", t, w);""") == ["199 4294967495"]


# --- operators ---------------------------------------------------------------

def test_unary_minus_and_invert_wrap_to_an_unsigned_type(tmp_path):
    assert _run(tmp_path, """
    bit[8] u = 1; bit[8] k = 0x0f;
    bit[8] v = -u;
    bit[8] w = ~k;
    int i = 5;
    int j = ~i;
    message(NONE, "%u %u %d", v, w, j);""") == ["255 240 -6"]


def test_right_shift_fills_with_ones_only_when_signed_and_negative(tmp_path):
    assert _run(tmp_path, """
    int s = -8; bit[8] u = 0xf8;
    message(NONE, "%d %d", s >> 1, u >> 1);""") == ["-4 124"]


def test_left_shift_drops_the_bits_it_moves_out(tmp_path):
    assert _run(tmp_path, """
    bit[8] x = 0x81;
    bit[8] y = x << 1;
    message(NONE, "%d", y);""") == ["2"]


def test_power(tmp_path):
    """Table 13: an integer result, including for a negative exponent."""
    assert _run(tmp_path, """
    int two = 2; int m1 = -1; int n = -3;
    bit[8]  y = 3 ** 5;
    int[8]  z = 3 ** 5;
    int big = two ** 40;
    message(NONE, "%u %d %d %d %d", y, z, big, two ** n, m1 ** n);""") == [
        "243 -13 0 0 -1"]


def test_power_by_zero_to_a_negative_exponent_raises(tmp_path):
    mod, rt = _load(_compile(tmp_path, """
component pss_top {
  int r;
  target function void g(int a, int b) { r = a ** b; }
}"""))
    with pytest.raises(ZeroDivisionError):
        mod.PssTop(rt.MemoryBus()).g(0, -1)


def test_conditional_arms_take_the_width_of_the_whole(tmp_path):
    assert _run(tmp_path, """
    bit[8] a = 200; bool c = true;
    bit[16] r = c ? a + a : 0;
    message(NONE, "%d", r);""") == ["400"]


# --- assignment-like contexts (8.7.2) ----------------------------------------

def test_compound_assignment_converts_back(tmp_path):
    """`x op= e` is `x = x op e`, so the result is converted to x's type."""
    assert _run(tmp_path, """
    bit[8] x = 250; int[8] y = 127; bit[8] s = 3; bit[8] l = 0x81;
    x += 10; y += 1; s -= 5; l <<= 1;
    message(NONE, "%u %d %u %u", x, y, s, l);""") == ["4 -128 254 2"]


def test_compound_assignment_that_cannot_overflow_keeps_its_form(tmp_path):
    """`x |= f << 16` of a 1-bit f into a bit[32] fits, so it reads as the
    source does rather than as `x = (x | ...) & 0xffffffff`."""
    text = _text(tmp_path, """
component pss_top {
  bit[32] w;
  target function void g(bit[1] f) { w |= ((bit[32])f) << 16; }
}""")
    assert "self.w |= f << 16" in text


def test_a_return_converts_to_the_return_type(tmp_path):
    assert _run(tmp_path, """
    bit[64] m = neg1();
    int s = to_s8(0x1ff);
    int z = from_u8(0xff);
    message(NONE, "0x%x %d %d", m, s, z);""", top_extra="""
function bit[64] neg1() { return -1; }
function int[8] to_s8(int v) { return v; }
function int from_u8(bit[8] b) { return b; }""") == [
        "0xffffffffffffffff -1 255"]


def test_an_argument_converts_to_its_parameter_type(tmp_path):
    assert _run(tmp_path, """
    message(NONE, "%d %d", ident8(0x1ff), op8(-1));""", extra="""
  function int op8(bit[8] v) { return v; }""", top_extra="""
function int ident8(bit[8] v) { return v; }""") == ["255 255"]


def test_casts_convert(tmp_path):
    assert _run(tmp_path, """
    int a = 200; int b = 0x1f;
    message(NONE, "%d %u", (int[8])a, (bit[4])b);""") == ["-56 15"]


def test_a_register_write_converts_to_the_register_width(tmp_path):
    mod, rt = _load(_compile(tmp_path, """
pure component regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> R;
}
component pss_top {
  regs_c regs;
  solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
  target function void g(addr_handle_t h) {
    int v = -1;
    regs.R.write_val(v);
    write32(h, v);
  }
}"""))
    bus = rt.MemoryBus()
    mod.PssTop(bus, 0x100).g(0x200)
    assert bus.mem[0x100] == 0xffffffff
    assert bus.mem[0x200] == 0xffffffff


def test_field_initializers_convert(tmp_path):
    mod, rt = _load(_compile(tmp_path, """
component pss_top {
  bit[8] a = 300;
  int[8] b = 200;
  int    c = -1;
  bit[4] d = 3 * 7;
  target function void g() { }
}"""))
    dut = mod.PssTop(rt.MemoryBus())
    assert (dut.a, dut.b, dut.c, dut.d) == (44, -56, -1, 5)


def test_a_wrapped_read_is_hoisted_in_the_async_form(tmp_path):
    """Converting a 32-bit read into a bit[8] wraps it, so the read is an
    operand after all -- and an awaited call is never a subexpression."""
    out = _compile(tmp_path, """
pure component regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> R;
}
component pss_top {
  regs_c regs;
  bit[8] seen;
  solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
  target function void g() { seen = regs.R.read_val(); }
}""", py_await="async")
    text = (out / "pss_top.py").read_text()
    assert "self.seen = _v0 & 0xff" in text
    assert "await self.regs_R_read_val() &" not in text
    mod, rt = _load(out, "pssc_rt_async")
    bus = rt.AsyncMemoryBus()
    dut = mod.PssTop(bus, 0x100)
    bus.mem[0x100] = 0x1234
    asyncio.run(dut.g())
    assert dut.seen == 0x34


# --- what is not emitted -----------------------------------------------------

def test_a_value_that_fits_is_not_wrapped(tmp_path):
    """The range of every operand is known here, and none can overflow: the
    output is the source's arithmetic, with no mask and no helper."""
    text = _text(tmp_path, """
component pss_top {
  bit[32] w;
  bit[8]  n;
  target function void g(bit[8] a, bit[8] b, int i) {
    w = a + b;
    w = a * b;
    n = a & 0x0f;
    w = w;
    if (i < 3) { n = a >> 2; }
  }
}""")
    body = text[text.index("    def g("):]
    assert "&" not in body.replace("a & 15", "")
    assert not any(f"{h}(" in text for h in pss_int.HELPERS)


def test_the_helpers_a_model_calls_are_the_ones_it_carries(tmp_path):
    text = _text(tmp_path, """
component pss_top {
  int q;
  target function void g(int a, int b) { q = a % b; }
}""")
    assert "def _pss_mod(" in text and "def _pss_div(" in text
    assert "def _pss_pow(" not in text


# --- the helpers, exhaustively -----------------------------------------------

def test_the_helpers_agree_with_the_lrm_on_every_4_bit_value():
    for a, b in itertools.product(range(-8, 8), repeat=2):
        assert pss_int._pss_sint(a + 16, 4) == a
        if b == 0:
            continue
        q = pss_int._pss_div(a, b)
        # Truncation toward zero: |q| is |a| // |b|, with the sign of a*b.
        assert abs(q) == abs(a) // abs(b)
        assert q == 0 or (q < 0) == ((a < 0) != (b < 0))
        r = pss_int._pss_mod(a, b)
        assert a == b * q + r
        assert r == 0 or (r < 0) == (a < 0)


def test_power_table_13():
    n = 8
    for a, b in itertools.product(range(-4, 5), range(-3, 4)):
        if a == 0 and b < 0:
            with pytest.raises(ZeroDivisionError):
                pss_int._pss_pow(a, b, n)
            continue
        got = pss_int._pss_pow(a, b, n)
        if b >= 0:
            want = (a ** b) % (1 << n)
        elif a == 1:
            want = 1
        elif a == -1:
            want = (1 if b % 2 == 0 else -1) % (1 << n)
        else:
            want = 0
        assert got == want, (a, b)


# --- foreach, whose loop variables are now typed ------------------------------

def test_foreach_binds_the_iterator_to_each_element(tmp_path):
    """The iterator is the ELEMENT (20.7.8 c), not the index."""
    mod, rt = _load(_compile(tmp_path, """
component pss_top {
  int arr[3];
  int t;
  int u;
  target function void g() {
    foreach (v : arr) { t = t + v; }
    foreach (v : arr[k]) { u = u + v * k; }
  }
}"""))
    dut = mod.PssTop(rt.MemoryBus())
    dut.arr = [5, 6, 7]
    dut.g()
    assert dut.t == 18
    assert dut.u == 0 * 5 + 1 * 6 + 2 * 7


def test_foreach_index_is_an_int(tmp_path):
    assert _run(tmp_path, """
    foreach (arr[i]) { message(NONE, "%d", i - 1); }""",
                extra="int arr[2];") == ["-1", "0"]


@pytest.mark.parametrize("body, what", [
    ("v = 1;", "iterator 'v'"),
])
def test_foreach_refuses_a_write_to_its_iterator(tmp_path, body, what):
    with pytest.raises(ValueError, match=f"body assigns the {what}"):
        _compile(tmp_path, f"""
component pss_top {{
  int arr[3];
  target function void g() {{ foreach (v : arr) {{ {body} }} }}
}}""")


def test_foreach_refuses_a_write_to_its_index(tmp_path):
    with pytest.raises(ValueError, match="body assigns the index 'i'"):
        _compile(tmp_path, """
component pss_top {
  int arr[3];
  target function void g() { foreach (arr[i]) { i = 2; } }
}""")


def test_foreach_refuses_an_iterator_over_components(tmp_path):
    with pytest.raises(ValueError, match="array of components"):
        _compile(tmp_path, """
component ch_c { int x; }
component pss_top {
  ch_c ch[2];
  target function void g() { foreach (c : ch) { } }
}""")
