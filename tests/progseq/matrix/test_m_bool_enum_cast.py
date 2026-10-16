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
}

test_construct = define_tests(CASES)
