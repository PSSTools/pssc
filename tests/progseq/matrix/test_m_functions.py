"""Functions: declaration, parameters, calls and results (LRM 20.3, 22).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE


CASES = {
    # --- D8: a value-returning operation inside an expression ------------------
    "a value-returning operation in a condition": Case("""
component pss_top {""" + ONE + """
  target function bool busy() { return a.STS.read_val() != 0; }
  target function void run() { if (busy()) { a.STS.write_val(0); } }
}""", ["read 32 0x1004 0x3", "write 32 0x1004 0x0"], mem={0x1004: 3},
        covers=("FN-CALL", "ST-IF", "DT-BOOL")),

    # --- P1: parameters --------------------------------------------------------
    # A parameter's default is used when the call leaves it out (20.2.5).
    # C and C++ made the call with too few arguments (W6).
    "a parameter with a default": Case("""
component pss_top {""" + ONE + """
  function int add(int x, int y = 10) { return x + y; }
  target function void run() {
    a.STS.write_val(add(1));
    a.STS.write_val(add(1, 2));
  }
}""", ["write 32 0x1004 0xb", "write 32 0x1004 0x3"],
        covers=("FN-PARAM",)),

    # A function is classified by what it IS, not its name: this one is no
    # register group's (W19). Every target took it for the register-offset
    # fold.
    "a user function named get_offset_of_instance": Case("""
component helper_c {
  function bit[64] get_offset_of_instance(int k) {
    if (k == 1) { return 7; }
    return 9;
  }
}
component pss_top {""" + ONE + """
  helper_c h;
  target function void run() {
    a.STS.write_val(h.get_offset_of_instance(1));
  }
}""", ["write 32 0x1004 0x7"],
        covers=("FN-CALL",)),
}

test_construct = define_tests(CASES)
