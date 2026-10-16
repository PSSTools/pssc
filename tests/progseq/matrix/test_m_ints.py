"""Integer expressions: widths and sign (LRM 8.7), bit and part selects,
shifts, `**`, `in`, the conditional operator.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, Refused


CASES = {
    # --- D3: bit and part selects (LRM 8.5.x; Table 21: unsigned, hi-lo+1) ----
    "a part select read": Case("""
component pss_top {
  gs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    bit[64] x = 0xABCDE123;
    ctl_s c;
    c.base_lo = x[31:12];
    a.CTL.write(c);
  }
}""", ["write 32 0x1000 0xabcde000"], covers=("EX-SEL",)),

    "a bit select and the high half of 64 bits": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[64] x = 0x1234567800000008;
    a.STS.write_val(x[3]);
    a.STS.write_val(x[2]);
    a.STS.write_val(x[63:32]);
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0",
       "write 32 0x1004 0x12345678"], covers=("EX-SEL",)),

    "a bit select with a run-time index": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[8] x = 0xA;
    int i = 0;
    while (i < 4) { a.STS.write_val(x[i]); i += 1; }
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x1",
       "write 32 0x1004 0x0", "write 32 0x1004 0x1"],
        covers=("EX-SEL", "ST-WHILE")),

    "a part and a bit select assigned": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[32] v = 0xFFFFFFFF;
    v[7:4] = 0;
    v[0] = 0;
    v[31:28] = 0x5;
    a.STS.write_val(v);
  }
}""", ["write 32 0x1004 0x5fffff0e"], covers=("EX-SEL", "EX-ASSIGN")),

    # --- D7: the conditional operator ------------------------------------------
    "a conditional expression": Case("""
component pss_top {""" + ONE + """
  target function void go(bit x) { a.STS.write_val((x == 1) ? 0 : 1); }
  target function void run() { go(0); go(1); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0"], covers=("EX-COND",)),

    "a nested conditional whose arm reads a register": Case("""
component pss_top {""" + ONE + """
  target function bit[32] sts() { return a.STS.read_val(); }
  target function void go(bit[2] x) {
    a.CTL.write_val((x == 1) ? sts() : ((x == 0) ? 7 : 9));
  }
  target function void run() { go(1); go(0); go(2); }
}""", ["read 32 0x1004 0x5", "write 32 0x1000 0x5",
       "write 32 0x1000 0x7", "write 32 0x1000 0x9"],
        mem={0x1004: 5}, covers=("EX-COND", "FN-CALL")),

    # --- D9: PSS bit widths (LRM 8.7) ------------------------------------------
    # Equality propagates the larger operand width (4) to both sides, so ~15
    # is 0 here, and equals q.
    "~ in a context narrower than int": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[4] p, bit[4] q) {
    if ((~p) == q) { a.STS.write_val(1); } else { a.STS.write_val(2); }
  }
  target function void run() { go(15, 0); go(0, 15); go(1, 1); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x1", "write 32 0x1004 0x2"],
        covers=("EX-BIT", "EX-WIDTH", "EX-EQ")),

    # The 64-bit parameter width propagates to x BEFORE the inversion.
    "~ in a context wider than its operand": Case("""
component pss_top {
  g64_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void go(bit[32] x) { a.W.write_val(~x); }
  target function void run() { go(1); }
}""", ["write 64 0x1000 0xfffffffffffffffe"], covers=("EX-BIT", "EX-WIDTH")),

    # The case as originally reported: 0xFFFFFFFE is RIGHT (8.7.2, Table 23),
    # not 0. Pinned so nobody "fixes" it to the operand's width.
    "~ on a bit passed to a 32-bit parameter": Case("""
component pss_top {""" + ONE + """
  target function void go(bit x) { a.STS.write_val(~x); }
  target function void run() { go(1); go(0); }
}""", ["write 32 0x1004 0xfffffffe", "write 32 0x1004 0xffffffff"],
        covers=("EX-BIT", "EX-WIDTH")),

    # A `bit[4]` holds 0..15: 15 + 1 is 0, whatever C type stores it.
    "a narrow local wraps at its own width": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[4] n = 15;
    n = n + 1;
    a.STS.write_val(n);
    n += 15;
    a.STS.write_val(n);
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0xf"],
        covers=("DT-INT", "EX-WIDTH", "EX-ASSIGN")),

    # `int[8]` 127 + 1 is -128, sign-extended into the 32-bit parameter.
    "a narrow signed value overflows to negative": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int[8] v = 127;
    v = v + 1;
    a.STS.write_val(v);
  }
}""", ["write 32 0x1004 0xffffff80"], covers=("DT-INT", "EX-WIDTH")),

    # PSS `/` and `%` truncate toward zero (8.5.1): -7 / 2 is -3, -7 % 2 is -1.
    "signed division truncates toward zero": Case("""
component pss_top {""" + ONE + """
  target function void go(int x) {
    a.STS.write_val(x / 2);
    a.STS.write_val(x % 2);
  }
  target function void run() { go(-7); }
}""", ["write 32 0x1004 0xfffffffd", "write 32 0x1004 0xffffffff"],
        covers=("EX-ARITH",)),

    # The assignment propagates 8 bits to `(~x) >> 1` and so to x: 0x01 ->
    # 0xFE -> 0x7F. In int it is -2 >> 1 = -1 -> 0xFF; at x's own 4 bits
    # it is 0xE >> 1 = 0x7.
    "~ then a right shift, assigned to 8 bits": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[4] x) {
    bit[8] y = (~x) >> 1;
    a.STS.write_val(y);
  }
  target function void run() { go(1); }
}""", ["write 32 0x1004 0x7f"], covers=("EX-BIT", "EX-SHIFT", "EX-WIDTH")),

    # --- Integers wider than 64 bits -------------------------------------------
    # C and C++ have no integer type wider than 64 bits. `bit[256]` was
    # declared `uint64_t`, losing the top 192 bits with no diagnostic; it is
    # refused there (user ruling, 2026-10-07). Python and SV carry it whole:
    # 5 shifted up by 200 reads back from bits [231:200].
    "a struct field wider than 64 bits": Case("""
struct key_s { bit[256] k; bit[8] n; }
component pss_top {""" + ONE + """
  key_s key;
  target function void run() {
    key.k = 5;
    key.k = key.k << 200;
    a.STS.write_val(key.k[231:200]);
  }
}""", ["write 32 0x1004 0x5"],
        refused=on(("c", "cpp"), Refused([r"key_s\.k", r"bit\[256\]",
                                            r"wider than 64"])),
        covers=("DT-INT", "EX-SHIFT")),

    "a local, parameter and result wider than 64 bits": Case("""
component pss_top {""" + ONE + """
  function bit[96] widen(bit[72] x) { return x; }
  target function void run() {
    bit[128] w = widen(3);
    a.STS.write_val((bit[32])w);
  }
}""", ["write 32 0x1004 0x3"],
        refused=on(("c", "cpp"), Refused([r"pss_top::widen: bit\[72\]",
                                            r"pss_top::widen: bit\[96\]",
                                            r"pss_top::run: bit\[128\]"])),
        covers=("DT-INT", "FN-PARAM", "DT-CAST")),

    # --- P1: compound assignment at the target's width (8.7.2) -----------------
    # `x op= y` is `x = x op y`: the result is converted to x's type, so a
    # bit[4] wraps at 16 and an int[16] at 2**15. C++ wrote `x op= y` on the
    # host type and kept the carry (W1).
    "compound assignment wraps at the target's width": Case("""
component pss_top {""" + ONE + """
  bit[4] f = 15;
  target function void run() {
    bit[4] n = 15;
    bit[12] m = 0xFFF;
    int[16] s = 32767;
    bit[8] b = 3;
    bit[8] c = 0x81;
    n += 1;
    a.STS.write_val(n);
    m += 2;
    a.STS.write_val(m);
    s += 1;
    a.STS.write_val(s);
    b -= 4;
    a.STS.write_val(b);
    c <<= 1;
    a.STS.write_val(c);
    f += 1;
    a.STS.write_val(f);
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x1",
       "write 32 0x1004 0xffff8000", "write 32 0x1004 0xff",
       "write 32 0x1004 0x2", "write 32 0x1004 0x0"],
        covers=("EX-ASSIGN", "EX-WIDTH", "DT-INT")),

    # A signed target shifts arithmetically under `>>=` as under `>>` (8.5.7):
    # -16 >> 2 is -4; an int[8] -128 >> 7 is -1. SV wrote `>>=`, which is
    # logical in SV whatever the operand's sign (W10).
    "a signed >>= shifts arithmetically": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x = -16;
    int[8] y = -128;
    x >>= 2;
    y >>= 7;
    a.STS.write_val(x);
    a.STS.write_val(y);
  }
}""", ["write 32 0x1004 0xfffffffc", "write 32 0x1004 0xffffffff"],
        covers=("EX-ASSIGN", "EX-SHIFT")),

    # A shift by the operand's width or more (R1): `<<` and an unsigned `>>`
    # give 0, a signed `>>` its sign. C's shift by >= the width of the type is
    # undefined, and x86 masks the count, so 1 << 33 was 2 (W3).
    "a shift by the width or more": Case("""
component pss_top {""" + ONE + """
  target function void go(int n) {
    bit[32] x = 1;
    bit[32] u = 0x80000000;
    int s = -8;
    bit[32] y = x << n;
    a.STS.write_val(y);
    a.STS.write_val(x << n);
    a.STS.write_val(u >> n);
    a.STS.write_val(s >> n);
  }
  target function void run() { go(33); go(32); }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x0", "write 32 0x1004 0x0",
       "write 32 0x1004 0xffffffff"] * 2,
        covers=("EX-SHIFT",)),

    "a 64-bit shift by 64": Case("""
component pss_top {
  g64_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void go(int n) {
    bit[64] w = 1;
    int[64] s = -1;
    a.W.write_val(w << n);
    a.W.write_val(s >> n);
  }
  target function void run() { go(64); }
}""", ["write 64 0x1000 0x0", "write 64 0x1000 0xffffffffffffffff"],
        covers=("EX-SHIFT",)),

    # A part select is an lvalue of `op=` (8.6.1): x[7:4] += 1 wraps in its
    # four bits and leaves the others. Python's `op=` did not look for a select
    # on its target (W17, unverified until now).
    "a part select as a compound-assignment target": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[8] x = 0xF5;
    x[7:4] += 1;
    a.STS.write_val(x);
    x[3:0] -= 6;
    a.STS.write_val(x);
  }
}""", ["write 32 0x1004 0x5", "write 32 0x1004 0xf"],
        covers=("EX-SEL", "EX-ASSIGN")),

    # --- P1: operators no target lowers yet ------------------------------------
    # `**` (8.5.1, Table 13): the left operand's type, the exponent self-
    # determined. 2 ** 31 wraps negative in an int; a negative exponent is
    # 0 but for a base of 1 or -1; `**` is left-associative (T11), so
    # 2 ** 3 ** 2 is 64. Run-time operands and constant ones.
    "a power with a run-time operand": Case("""
component pss_top {""" + ONE + """
  target function void go(int x, bit[8] b, int m) {
    int y = 2;
    a.STS.write_val(2 ** x);
    a.STS.write_val(b ** 4);
    a.STS.write_val(2 ** 10);
    a.STS.write_val(y ** 31);
    a.STS.write_val(y ** m);
    a.STS.write_val(m ** 3);
    a.STS.write_val(m ** m);
    a.STS.write_val(2 ** 3 ** 2);
  }
  target function void run() { go(3, 2, -1); }
}""", ["write 32 0x1004 0x8", "write 32 0x1004 0x10", "write 32 0x1004 0x400",
       "write 32 0x1004 0x80000000", "write 32 0x1004 0x0",
       "write 32 0x1004 0xffffffff", "write 32 0x1004 0xffffffff",
       "write 32 0x1004 0x40"],
        covers=("EX-ARITH", "EX-ARITH.5", "EX-PREC.2")),

    # `in` in procedural code (8.5.9): values and ranges. A range here has
    # both bounds (`open_range_value`); `[6..]` is a domain's form (7.2.1),
    # and the parser refuses it in an expression.
    "in with values and ranges": Case("""
component pss_top {""" + ONE + """
  target function void go(int v) {
    if (v in [1, 3..5]) { a.STS.write_val(1); } else { a.STS.write_val(0); }
    if (v in [6..9]) { a.STS.write_val(2); } else { a.STS.write_val(0); }
  }
  target function void run() { go(4); go(2); go(9); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0",
       "write 32 0x1004 0x0", "write 32 0x1004 0x0",
       "write 32 0x1004 0x0", "write 32 0x1004 0x2"],
        covers=("EX-IN",)),

    # A range compares at PSS widths and signs: -1 is in [-2..0] for an int.
    "in with a negative range": Case("""
component pss_top {""" + ONE + """
  target function void go(int v) {
    if (v in [-2..0]) { a.STS.write_val(1); } else { a.STS.write_val(0); }
  }
  target function void run() { go(-1); go(1); go(-3); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0", "write 32 0x1004 0x0"],
        covers=("EX-IN",)),
}

test_construct = define_tests(CASES)
