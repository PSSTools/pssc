"""Procedural control flow: loops, break/continue, return, blocks (LRM 20.7).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, ONE


CASES = {
    # --- repeat (LRM 20.7.6) -------------------------------------------------------
    # The count is evaluated once: the body raising `n` does not add
    # iterations (0, 1, 2). Nested, zero, and break/continue (10, then 12).
    # C and C++ had no lowering for `repeat` at all.
    "repeat: counted, indexed, nested, empty": Case("""
component pss_top {""" + ONE + """
  int n = 3;
  target function void run() {
    repeat (i : n) { n = n + 1; a.STS.write_val(i); }
    repeat (2) { repeat (j : 2) { a.CTL.write_val(j); } }
    repeat (0) { a.STS.write_val(99); }
    repeat (k : 5) {
      if (k == 1) { continue; }
      if (k == 3) { break; }
      a.STS.write_val(k + 10);
    }
    a.STS.write_val(n);
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x1", "write 32 0x1004 0x2",
       "write 32 0x1000 0x0", "write 32 0x1000 0x1",
       "write 32 0x1000 0x0", "write 32 0x1000 0x1",
       "write 32 0x1004 0xa", "write 32 0x1004 0xc",
       "write 32 0x1004 0x6"], covers=("ST-REPEAT", "ST-BRK")),

    # --- loops and match (20.7.10, 20.7.11) ------------------------------------
    # `break` and `continue` in a `match` arm act on the enclosing LOOP: a
    # match is not a loop (IP-1). 1 is written, 2 skipped, 3 ends the loop.
    "break and continue inside a match inside a loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    repeat (i : 5) {
      match (i) {
        [2]: continue;
        [3]: break;
        default: a.STS.write_val(i);
      }
      a.CTL.write_val(i);
    }
    a.STS.write_val(9);
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1000 0x0",
       "write 32 0x1004 0x1", "write 32 0x1000 0x1", "write 32 0x1004 0x9"],
        covers=("ST-BRK", "ST-MATCH", "ST-REPEAT", "IP-1")),
}

test_construct = define_tests(CASES)
