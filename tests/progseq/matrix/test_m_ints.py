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

    # --- P3: literal forms (4.6.1, Table 21) -----------------------------------
    # A sized literal too long for its size loses its left bits (4'hFF is
    # 0xF); a signed one sign-extends (4'sb1000 is -8); a leading 0 is octal.
    # A based literal without `s` is unsigned, so -'d12 / 3 is computed on
    # 0xFFFFFFF4. An unsized hex literal is unsigned too: 0xFFFFFFFF > -1
    # compares unsigned, and is false. 4294967296 needs 34 signed bits.
    "literal forms": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[64] big = 4294967296;
    bit[64] wide = 0x7_0000_0000;
    message(LOW, "%x %x %d %d %x", 8'hFF, 4'hFF, 4'sb1000, 017, 1_000);
    message(LOW, "%d %d %d %d", -12 / 3, -'d12 / 3, -'sd12 / 3, -4'sd12 / 3);
    if (0xFFFFFFFF > -1) { message(LOW, "gt"); } else { message(LOW, "le"); }
    message(LOW, "%x %x", big, wide);
  }
}""", ["ff f -8 15 3e8", "-4 1431655761 -4 1", "le", "100000000 700000000"],
        covers=("EX-LIT.1", "EX-LIT.2", "EX-LIT.3", "EX-LIT.4", "EX-LIT.5",
                "EX-LIT.6", "EX-LIT.7", "EX-REL.2")),

    # A shift of the literal 1 is carried out at the literal's 32 bits when
    # nothing wider is around it (EX-SHIFT.5), and at 64 in a 64-bit target;
    # the same holds for 0xFFFFFFFF << 4 (EX-WIDTH.15). With the amount a
    # loop index reaching the width (IP-77): 1 << 32 is 0 in 32 bits.
    "a shift of a literal, by a loop index, in 32 and 64 bits": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[64] m = 0xFFFFFFFF << 4;
    message(LOW, "%x %x", m, 1 << 40);
    repeat (i : 3) {
      int n = 30 + i * 2;
      bit[32] y = 1 << n;
      bit[64] z = 1 << n;
      message(LOW, "%x %x", y, z);
    }
  }
}""", ["ffffffff0 0", "40000000 40000000", "0 100000000", "0 400000000"],
        covers=("EX-SHIFT.4", "EX-SHIFT.5", "EX-WIDTH.15", "ST-REPEAT",
                "IP-77")),

    # --- P3: division (8.5.1) ---------------------------------------------------
    # Truncation toward zero and the sign of `%`, over a loop (IP-76).
    "signed division and modulus in a loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int n = -7;
    repeat (4) {
      message(LOW, "%d %d %d %d", n / 2, n % 2, 7 / n, 7 % n);
      n = n + 3;
    }
  }
}""", ["-3 -1 -1 0", "-2 0 -1 3", "0 -1 -7 0", "1 0 3 1"],
        covers=("EX-ARITH.2", "EX-ARITH.3", "ST-REPEAT", "IP-76")),

    # The most negative value divided by -1 overflows; PSS wraps it, as every
    # other operation (8.5.1, 8.7): -2**31 / -1 is -2**31, the modulus 0.
    # C's is undefined behaviour (x86 traps).
    "the most negative value divided by -1": Case("""
component pss_top {""" + ONE + """
  target function void go(int x, int y, int[8] a, int[8] b) {
    message(LOW, "%d %d %d %d", x / y, x % y, a / b, a % b);
  }
  target function void run() { go(0x80000000, -1, -128, -1); }
}""", ["-2147483648 0 -128 0"], covers=("EX-ARITH.2", "EX-ARITH.3")),

    # --- P3: precedence (8.4.1, Table 11) --------------------------------------
    # Unary minus binds tighter than `**` (-2 ** 2 is 4); `+` tighter than
    # `<<`; `-` and `/` associate left; `&` > `^` > `|`; `?:` associates right
    # and is lowest. A cast applies to its operand only, and a cast value is
    # widened by its context like any other (IP-81): (bit[4])18 is 2, and
    # 2 + (bit[4])15 wraps at 4 bits on its own but not in a bit[8].
    "precedence and associativity": Case("""
component pss_top {""" + ONE + """
  target function void go(int a, int b, int c, int n, bool p, bool q, int w) {
    bit[8] r = (bit[4])w + (bit[4])15;
    message(LOW, "%d %d %d %d", -a ** 2, 1 << n + 1, a - b - c, 100 / a / c);
    message(LOW, "%d %d %d", a | b ^ c & 6, p ? 1 : q ? 2 : 3, p ? a : b + 1);
    message(LOW, "%d %d %d", (bit[4])w + 15, (bit[4])w + (bit[4])15, r);
  }
  target function void run() { go(2, 3, 4, 2, false, true, 18); }
}""", ["4 8 -5 12", "7 2 4", "17 1 17"],
        covers=("EX-PREC.1", "EX-PREC.2", "EX-PREC.3", "EX-PREC.4",
                "EX-PREC.5", "DT-CAST", "IP-81")),

    # --- P3: sign and width interactions -----------------------------------------
    # Unary minus keeps an unsigned operand unsigned (Table 22): -x < 0 is
    # false for a bit[8] (IP-79), true for an int[8]; -x in 32 bits is
    # 0xFFFFFFFF.
    "unary minus of an unsigned value": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[8] x, int[8] s) {
    bit[32] y = -x;
    if (-x < 0) { message(LOW, "neg"); } else { message(LOW, "nonneg"); }
    if (-s < 0) { message(LOW, "neg"); } else { message(LOW, "nonneg"); }
    message(LOW, "%u %x", -x, y);
  }
  target function void run() { go(1, 1); }
}""", ["nonneg", "neg", "255 ffffffff"],
        covers=("EX-ARITH.6", "EX-REL.2", "IP-79")),

    # An int accumulating past 2**31 wraps negative; a bit[64] does not
    # (IP-78). int + bit[32] is unsigned 32 bits, converted back to int.
    "an int accumulating past 2**31": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int s = 0;
    bit[64] u = 0;
    repeat (3) { s = s + 0x40000000; u = u + 0x40000000; }
    message(LOW, "%d %d", s, u);
  }
}""", ["-1073741824 3221225472"],
        covers=("EX-ARITH.1", "EX-WIDTH", "ST-REPEAT", "IP-78")),

    # The arms of `?:` meet at the larger width, unsigned unless both are
    # signed, and a wider target propagates into the chosen arm (IP-80):
    # a + a is 30 in 16 bits, b + b is 0x100, and the int[8] -1 met at an
    # unsigned 16 bits is ZERO-extended (8.7.1): 0xFF.
    "nested ?: with arms of different widths in a wider target": Case("""
component pss_top {""" + ONE + """
  target function void go(bool p, bool q, bit[4] a, bit[8] b, int[8] c) {
    bit[16] r = p ? a + a : (q ? b + b : c);
    message(LOW, "%x", r);
  }
  target function void run() {
    go(true, false, 15, 0x80, -1);
    go(false, true, 15, 0x80, -1);
    go(false, false, 15, 0x80, -1);
  }
}""", ["1e", "100", "ff"], covers=("EX-COND.4", "EX-WIDTH.11", "IP-80")),

    # Extension follows the SOURCE's sign in an assignment-like context
    # (8.7.2, IP-20): (bit[4])15 into an int is 15, (int[4])-1 into a bit[32]
    # is 0xFFFFFFFF, and (int[4])15 is -1.
    "extension follows the source's sign": Case("""
component pss_top {""" + ONE + """
  target function void go(int a, int b) {
    int z = (bit[4])a;
    bit[32] w = (int[4])b;
    int v = (int[4])a;
    message(LOW, "%d %x %d", z, w, v);
  }
  target function void run() { go(15, -1); }
}""", ["15 ffffffff -1"], covers=("EX-WIDTH.3", "DT-CAST", "IP-20")),

    # bit[32] 1 < int i compares unsigned (Ex 43): true for every negative i
    # (IP-21), while the loop's own `i >= -2` is signed.
    "a mixed-sign comparison in a loop counting down": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[32] one = 1;
    int i = 2;
    while (i >= -2) {
      if (one < i) { message(LOW, "%d lt", i); }
      else { message(LOW, "%d ge", i); }
      i = i - 1;
    }
  }
}""", ["2 lt", "1 ge", "0 ge", "-1 lt", "-2 lt"],
        covers=("EX-REL.2", "ST-WHILE", "ST-IF", "IP-21")),

    # The target's width reaches into the expression in every assignment-like
    # context (8.7.2): `z += x << 4` shifts x in 16 bits (IP-18); a narrowing
    # assignment evaluates at the source's 16 bits, then truncates (IP-19); a
    # bit[16] parameter and a bit[16] result do not wrap bit[8] operands,
    # where a message argument, self-determined, does (IP-16, IP-17).
    "the target width in assignment-like contexts": Case("""
component pss_top {""" + ONE + """
  target function void show(bit[16] v) { message(LOW, "%x", v); }
  target function bit[16] sum(bit[8] x, bit[8] y) { return x + y; }
  target function void go(bit[8] x, bit[8] y, bit[16] p, bit[16] q) {
    bit[16] z = 1;
    bit[8] n = p + q;
    z += x << 4;
    message(LOW, "%x %x", z, n);
    show(x + y);
    message(LOW, "%x %x", x + y, sum(x, y));
  }
  target function void run() { go(0xF0, 0x20, 0x1F0, 0x20); }
}""", ["f01 10", "110", "10 110"],
        covers=("EX-WIDTH.3", "EX-WIDTH.4", "EX-WIDTH.5", "EX-WIDTH.6",
                "EX-ASSIGN.6", "IP-16", "IP-17", "IP-18", "IP-19")),

    # --- P3: selects as lvalues -------------------------------------------------
    # A bit select written with a run-time index, in a loop (IP-45): the bits
    # of 0x1E reversed are 0x78.
    "a value built bit by bit": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[8] y) {
    bit[8] x = 0;
    repeat (i : 8) { x[7 - i] = (y >> i) & 1; }
    message(LOW, "%x", x);
  }
  target function void run() { go(0x1E); }
}""", ["78"], covers=("EX-SEL", "ST-REPEAT", "IP-45")),

    # `a[i].f[7:4] += 1` (IP-6): a part select of a struct field of an array
    # element, compound-assigned, wraps in its 4 bits and keeps the others.
    "a part select of a field of an array element, compound-assigned": Case("""
struct ps_s { bit[16] f; }
component pss_top {""" + ONE + """
  target function void go(int i) {
    array<ps_s, 3> a;
    a[i].f = 0x12F4;
    a[i].f[7:4] += 1;
    a[i + 1].f[3:0] += 3;
    message(LOW, "%x %x %x", a[0].f, a[1].f, a[2].f);
  }
  target function void run() { go(1); }
}""", ["0 1204 3"], covers=("EX-SEL", "EX-ASSIGN.6", "DT-STRUCT", "IP-6")),

    # --- P3: a typedef (7.11) ---------------------------------------------------------
    # A package typedef names its type: 300 into a bit[8] is 0x2C.
    "a package typedef": Case("""
package tp { typedef bit[8] byte_t; }
component pss_top {""" + ONE + """
  target function tp::byte_t inc(tp::byte_t v) { return v + 1; }
  target function void run() {
    tp::byte_t x = 300;
    message(LOW, "%x %x", x, inc(0xFF));
  }
}""", ["2c 0"], covers=("DT-TYPEDEF", "EX-WIDTH.4")),
}

test_construct = define_tests(CASES)
