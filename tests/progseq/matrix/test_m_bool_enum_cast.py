"""Booleans, enums and casts (LRM 7.4, 7.5, 7.12).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, ONE


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
        covers=("DT-CAST", "DT-BOOL")),

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
                "EX-LOG.1", "EX-EQ.3")),

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
                "EX-IN.8", "FN-PARAM", "IP-32", "IP-33", "IP-66")),

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
}

test_construct = define_tests(CASES)
