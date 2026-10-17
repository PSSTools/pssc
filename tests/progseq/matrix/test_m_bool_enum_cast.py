"""Booleans, enums and casts (LRM 7.4, 7.5, 7.12).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import ALL, Case, define_tests, on, ONE, Refused

F1 = ("F1: operand types are not checked (8.5.2): a relational operator on "
      "bools is accepted")
F2 = ("F2: enum values are not checked (7.5.1 e/j, 17.2.4): a duplicate "
      "value, or a variable of an itemless enum, is accepted")


CASES = {

    # --- P1: casts (7.12) ------------------------------------------------------
    # A size cast to a signed type gives a signed value: (int[8])200 is -56,
    # sign-extended into the 32-bit parameter, and cast back to bit[8] is
    # 0xC8 again. A bool cast is "not zero" (7.12.1 b): (bool)0x100 is true.
    # SV kept the operand's sign (W14); C and SV took the low bit and Python
    # the identity for a bool cast (W14, W15), all from bool being bit[1] in
    # the IR (I5).
    "size, sign and bool casts": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[8] u, bit[32] z) {
    bool b = (bool)z;
    a.STS.write_val((int[8])u);
    a.STS.write_val(b ? 7 : 9);
    a.STS.write_val((bit[32])((bool)z));
    a.STS.write_val((bit[8])((int[8])u));
  }
  target function void run() { go(200, 0x100); go(1, 0); }
}""", ["write 32 0x1004 0xffffffc8", "write 32 0x1004 0x7",
       "write 32 0x1004 0x1", "write 32 0x1004 0xc8",
       "write 32 0x1004 0x1", "write 32 0x1004 0x9", "write 32 0x1004 0x0",
       "write 32 0x1004 0x1"],
        covers=("DT-CAST", "DT-BOOL", "DT-INT.7", "DT-CAST.1")),

    # --- P3: bool (7.4, 7.12.1 b, 8.5.4) ---------------------------------------
    # `&& || !` on bools, `==`/`!=` between bools, and a bool cast to an
    # integer: false is 0, true 1 (7.12.1 b). An uninitialized bool is false.
    "bool operators and a bool cast to an integer": Case("""
component pss_top {""" + ONE + """
  target function void go(bool p, bool q) {
    bool r = p && !q;
    bool z;
    message(LOW, "%n %n %n %n %n", p && q, p || q, !p, r, z);
    message(LOW, "%d %d", (bit[8])p + (bit[8])q, (int)(p != q));
    if (p == q) { message(LOW, "eq"); } else { message(LOW, "ne"); }
  }
  target function void run() { go(true, false); go(true, true); }
}""", ["false true false true false", "1 1", "ne",
       "true true false false false", "2 0", "eq"],
        covers=("DT-BOOL.1", "DT-BOOL.3", "DT-BOOL.6", "DT-CAST.2",
                "EX-LOG.1", "EX-EQ.3", "EX-LIT.9")),

    # A cast to bool is a condition (IP-82): any non-zero value is true.
    "a bool cast as a condition": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[8] x) {
    bool b = (bool)(x & 0x10);
    if ((bool)x) { message(LOW, "t %n", b); } else { message(LOW, "f %n", b); }
  }
  target function void run() { go(0x20); go(0); go(0x10); }
}""", ["t false", "f false", "t true"],
        covers=("DT-BOOL.2", "DT-CAST.2", "ST-IF", "IP-82")),

    # --- P3: enums (7.5) ------------------------------------------------------------
    # An item after an explicit value counts on from it (GREEN = 5, BLUE 6);
    # an enum casts to an integer and back, and a value that is no item
    # reaches `default` of a `match` (7.12.1 e, Example 28; IP-12). An
    # uninitialized enum is its first item.
    "enum values and casts to and from an integer": Case("""
enum col_e { RED, GREEN = 5, BLUE, BLACK = 10 }
component pss_top {""" + ONE + """
  target function void go(col_e c, int n) {
    col_e d = (col_e)n;
    col_e z;
    message(LOW, "%n %d %u %n", c, (int)c, (bit[4])c, z);
    match (d) {
      [RED, BLUE]: message(LOW, "rb");
      [GREEN]: message(LOW, "g");
      [BLACK]: message(LOW, "k");
      default: message(LOW, "other %d", (int)d);
    }
  }
  target function void run() { go(BLUE, 10); go(BLACK, 6); go(RED, 11); }
}""", ["BLUE 6 6 RED", "k", "BLACK 10 10 RED", "rb", "RED 0 0 RED",
       "other 11"],
        covers=("DT-ENUM.1", "DT-ENUM.2", "DT-ENUM.5", "DT-ENUM.10",
                "DT-ENUM.12", "DT-CAST.3", "ST-MATCH", "IP-12")),

    # An unqualified item is resolved by the type it is expected to have
    # (8.4.3): in both arms of `?:` assigned to an enum (IP-32), as an
    # argument for an enum parameter, and as that parameter's default
    # (IP-33, IP-66). `in` with items (8.5.9, 7.5.2).
    "unqualified enum items, as arguments and defaults": Case("""
enum mode_e { OFF, SLOW, FAST }
component pss_top {""" + ONE + """
  target function void show(mode_e m = SLOW) {
    if (m in [OFF, FAST]) { message(LOW, "%n edge", m); }
    else { message(LOW, "%n mid", m); }
  }
  target function void go(bool p) {
    mode_e m = p ? FAST : OFF;
    show(m);
    show(mode_e::FAST);
    show();
  }
  target function void run() { go(true); go(false); }
}""", ["FAST edge", "FAST edge", "SLOW mid",
       "OFF edge", "FAST edge", "SLOW mid"],
        covers=("DT-ENUM.6", "DT-ENUM.7", "DT-ENUM.11", "EX-COND.5",
                "EX-IN.8", "FN-PARAM", "IP-32", "IP-33", "IP-66",
                "FN-PARAM.11", "ST-VAR.8")),

    # --- P3: `in` at a common type (Table 22, Example 45) ----------------------
    # Every item of a range list and the left side meet at the largest width,
    # unsigned unless all are signed: bit[2] and int[3] meet at bit[3], so
    # x + y is 5, a + b 4, and c + d with c an int[3] -4 is 4 + 2 = 6:
    # 3'd5 in [3'd4..3'd6] holds. The same values against a list of `int`s
    # meet at 32 bits.
    "in: operands at a common type": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[2] x, bit[2] y, bit[2] a, bit[2] b,
                          int[3] c, bit[2] d) {
    if ((x + y) in [a + b..c + d]) { message(LOW, "in"); }
    else { message(LOW, "out"); }
    if ((x + y) in [10, 5]) { message(LOW, "in"); }
    else { message(LOW, "out"); }
  }
  target function void run() { go(3, 2, 2, 2, -4, 2); }
}""", ["in", "in"], covers=("EX-IN.1", "EX-IN.6", "EX-WIDTH.12")),

    # --- P4 ---------------------------------------------------------------------------
    # An integer format prints a bool as 1 or 0 (21.1.1 c: an implicit
    # conversion; 7.12 b: true is 1). C, C++ and Python formatted it as a
    # one-bit value, which `%d` reads as signed: -1 (W35). An `in` is a bool
    # too, and had no type for a format at all.
    "a bool printed as an integer": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bool b = 3 in [1..4];
    bit[8] x = 7;
    message(LOW, "%d %u %x %d %d", b, b, x == 7, x != 7, x in [5, 7]);
  }
}""", ["1 1 1 0 1"], covers=("DT-BOOL", "LIB-MSG", "EX-IN")),

    # --- P5.3: bool, enums and casts ------------------------------------------------
    # `<` and the other relational operators take numeric operands only
    # (8.5.2); bool has `==`/`!=` alone.
    "a relational operator on bools is refused": Case("""
component pss_top {""" + ONE + """
  target function void go(bool p, bool q) {
    if (p < q) { message(LOW, "lt"); } else { message(LOW, "ge"); }
  }
  target function void run() { go(false, true); }
}""", Refused([r"<", r"bool"]), xfail=on(ALL, F1), covers=("DT-BOOL.4",)),

    # The base type sets an item's width and sign (7.5.1): M is an int[4] -1,
    # so cast to bit[8] it is sign-extended to 0xFF; U is a bit[4] 15, so it
    # is zero-extended (8.7.1).
    "an enum's base type sets its items' sign": Case("""
enum s_e : int[4] { M = -1, P = 1 }
enum u_e : bit[4] { U = 15 }
component pss_top {""" + ONE + """
  target function void run() {
    s_e m = M;
    message(LOW, "%d %x %x", (int)m, (bit[8])m, (bit[8])u_e::U);
  }
}""", ["-1 ff f"], covers=("DT-ENUM.3", "DT-CAST.3")),

    # No two items may have one value (7.5.1 e), in the declaration or across
    # an extension (17.2.4: an explicit conflicting value is illegal).
    "two enum items with one value are refused": Case("""
enum e_e { A = 1, B = 1 }
component pss_top {""" + ONE + """
  target function void run() { e_e v = B; message(LOW, "%n", v); }
}""", Refused([r"\bB\b"]), xfail=on(ALL, F2), covers=("DT-ENUM.4",)),

    "an extension item with an existing value is refused": Case("""
enum e_e { A = 1 }
extend enum e_e { C = 1 }
component pss_top {""" + ONE + """
  target function void run() { e_e v = C; message(LOW, "%n", v); }
}""", Refused([r"\bC\b"]), xfail=on(ALL, F2), covers=("DT-ENUM.4",)),

    # Extension items take values as if they ended the initial list (17.2.4):
    # YELLOW follows GREEN = 5. An empty enum may be filled by an extension
    # (7.5.1 j); an uninitialized one is its first item, P (7.5.1 k).
    "enum extensions, and an empty enum filled by one": Case("""
enum col_e { RED, GREEN = 5 }
extend enum col_e { YELLOW, PINK = 20 }
enum e_e { }
extend enum e_e { P = 3, Q }
component pss_top {""" + ONE + """
  target function void run() {
    col_e c = YELLOW;
    col_e d = col_e::PINK;
    e_e z;
    message(LOW, "%n %d %n %d %n %d", c, (int)c, d, (int)d, z, (int)z);
  }
}""", ["YELLOW 6 PINK 20 P 3"], covers=("DT-ENUM.8", "DT-ENUM.5")),

    # Item names are unique across the declaration and its extensions (7.5.1 g).
    "an extension reusing an item name is refused": Case("""
enum col_e { RED, GREEN }
extend enum col_e { RED }
component pss_top {""" + ONE + """
  target function void run() { col_e c = GREEN; message(LOW, "%n", c); }
}""", Refused([r"duplicate declaration of enum item 'RED'"]),
        covers=("DT-ENUM.8",)),

    # A variable of an enum that has no items anywhere is illegal (7.5.1 j).
    "a variable of an itemless enum is refused": Case("""
enum n_e { }
component pss_top {""" + ONE + """
  target function void run() { n_e v; message(LOW, "%d", (int)v); }
}""", Refused([r"n_e"]), xfail=on(ALL, F2), covers=("DT-ENUM.8",)),

    # A domain-restricted enum defaults to the first DECLARED item inside the
    # domain (7.5.1): GREEN for [GREEN..BLUE], and GREEN, not BLACK, for
    # [BLACK, GREEN].
    "an enum domain sets a local's default": Case("""
enum col_e { RED, GREEN, BLUE, BLACK }
component pss_top {""" + ONE + """
  target function void run() {
    col_e in [GREEN..BLUE] c;
    col_e in [BLACK, GREEN] d;
    message(LOW, "%n %n", c, d);
  }
}""", ["GREEN GREEN"], covers=("DT-ENUM.9",)),

    "an enum domain on a field": Case("""
enum col_e { RED, GREEN, BLUE, BLACK }
component pss_top {""" + ONE + """
  col_e in [BLUE..] f;
  target function void run() { message(LOW, "%n", f); }
}""", ["BLUE"], covers=("DT-ENUM.9",)),

    # An item is a static constant (7.5.1 a), so it may stand in a constant
    # expression (8.2): a `compile if` condition and an array size.
    "an enum item in a compile-if condition": Case("""
enum sz_e { ONE = 1, FOUR = 4 }
component pss_top {""" + ONE + """
  target function void run() {
    compile if ((int)sz_e::FOUR == 4) { message(LOW, "four"); }
  }
}""", ["four"], covers=("DT-ENUM.14", "ST-CIF")),

    "an enum item as an array size": Case("""
enum sz_e { ONE = 1, FOUR = 4 }
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], (int)sz_e::FOUR> x;
    message(LOW, "%d", x.size());
  }
}""", ["4"], covers=("DT-ENUM.14",)),

    # The cast's type is the expected type of its operand (8.4.3): `BLUE`
    # resolves unqualified under `(col_e)`, and not under `(int)` (Example 37).
    "an unqualified item under an enum cast": Case("""
enum col_e { RED, GREEN, BLUE }
component pss_top {""" + ONE + """
  target function void run() { col_e c = (col_e)BLUE; message(LOW, "%n", c); }
}""", ["BLUE"], covers=("DT-CAST.6",)),

    "an unqualified item under an integer cast is refused": Case("""
enum col_e { RED, GREEN, BLUE }
component pss_top {""" + ONE + """
  target function void run() { int c = (int)BLUE; message(LOW, "%d", c); }
}""", Refused([r"m\.pss:\d+:\d+: error: enum item 'BLUE' is used where no "
               r"enumeration type is expected \(7\.5\.1 i, 8\.4\.3; ruling R22\)"]),
        covers=("DT-CAST.6",)),

    # Only a numeric, bool or enum value casts, and only to such a type
    # (7.12.1 a): not a chandle or a struct, nor to a struct.
    "casts from a chandle or a struct, or to a struct, are refused": Case("""
struct p_s { int a; }
component pss_top {""" + ONE + """
  target function void run() {
    chandle c;
    p_s v;
    int x = (int)c;
    int y = (int)v;
    p_s w = (p_s)3;
    message(LOW, "%d %d %d", x, y, w.a);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: a cast from chandle: only a numeric, Boolean or "
               r"enumeration value, or a reference, can be cast \(LRM 7\.12\)",
               r"m\.pss:\d+:\d+: error: a cast from struct p_s: only a numeric",
               r"m\.pss:\d+:\d+: error: a cast to struct p_s: a casting type shall be a numeric, "
               r"Boolean, enumeration or reference type \(LRM 7\.12\.1\)"]),
        covers=("DT-CAST.7", "DT-CHND.4")),

    # `(chandle)` is not a casting_type at all (Syntax 18).
    "a cast to chandle is refused": Case("""
component pss_top {""" + ONE + """
  target function void run() { chandle c = (chandle)5; }
}""", Refused([r"syntax error at 'chandle'"]), covers=("DT-CAST.7", "DT-CHND.4")),

    "a cast from a string is refused": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "5";
    int x = (int)s;
    message(LOW, "%d", x);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: a cast from string: only a numeric, Boolean or "
               r"enumeration value, or a reference, can be cast \(LRM 7\.12\)"]),
        covers=("DT-CAST.7",)),

    # A typedef names its type in a cast (7.12.1): 0x1F is 15 as a bit[4]
    # and -1 as an int[4] (8.7.2: truncated to the low four bits).
    "casts to typedefs": Case("""
package tp { typedef bit[4] u4; typedef int[4] s4; }
component pss_top {""" + ONE + """
  target function void run() {
    bit[8] x = 0x1F;
    message(LOW, "%u %d", (tp::u4)x, (tp::s4)x);
  }
}""", ["15 -1"], covers=("DT-TYPEDEF.3", "DT-CAST.1")),

    # `&&` binds tighter than `||` (8.5.4): a || (b && c) is true, (a || b)
    # && c false. `&&` and `!` are bool operators, so the 64-bit target does
    # not reach the `==` inside them (Table 22): x + y meets z at 8 bits,
    # wraps to 0, and is equal.
    "logical precedence and self-determined operands": Case("""
component pss_top {""" + ONE + """
  target function void go(bool a, bool b, bool c,
                          bit[8] x, bit[8] y, bit[8] z) {
    if (a || b && c) message(LOW, "t"); else message(LOW, "f");
    if ((a || b) && c) message(LOW, "t"); else message(LOW, "f");
    bit[64] r = (bit[64])((x + y == z) && true);
    message(LOW, "%d %d", r, !(x + y != z));
  }
  target function void run() { go(true, false, false, 0xFF, 1, 0); }
}""", ["t", "f", "1 1"], covers=("EX-LOG.4", "EX-LOG.5", "EX-LOG.1")),
}

test_construct = define_tests(CASES)
