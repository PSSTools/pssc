"""Procedural `match` (LRM 20.7.10).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, ERROR, on, ONE


CASES = {
    # --- D4: match arms -------------------------------------------------------
    # An enum item reaches the IR as the constant it names; what C, C++ and
    # Python could not lower was the `default:` arm (a wildcard pattern).
    "a match with a default arm": Case("""
component pss_top {""" + ONE + """
  target function void go(int x) {
    match (x) {
      [1]: a.STS.write_val(10);
      [2, 3]: a.STS.write_val(20);
      default: a.STS.write_val(30);
    }
  }
  target function void run() { go(1); go(3); go(7); }
}""", ["write 32 0x1004 0xa", "write 32 0x1004 0x14", "write 32 0x1004 0x1e"],
        covers=("ST-MATCH",)),

    "match on enum items": Case("""
enum mode_e { M0, M1, M2, M3 }
component pss_top {""" + ONE + """
  target function void go(mode_e m) {
    match (m) {
      [M1]: a.STS.write_val(1);
      [M0, M2]: a.STS.write_val(2);
      default: a.STS.write_val(3);
    }
  }
  target function void run() { go(M0); go(M1); go(M2); go(M3); }
}""", ["write 32 0x1004 0x2", "write 32 0x1004 0x1",
       "write 32 0x1004 0x2", "write 32 0x1004 0x3"],
        covers=("ST-MATCH", "DT-ENUM")),

    "match on items of a package's enum": Case("""
package modes_pkg { enum mode_e { M0, M1, M2 } }
import modes_pkg::*;
component pss_top {""" + ONE + """
  target function void go(mode_e m) {
    match (m) {
      [M2]: a.STS.write_val(2);
      default: a.STS.write_val(0);
    }
  }
  target function void run() { go(M2); go(M0); }
}""", ["write 32 0x1004 0x2", "write 32 0x1004 0x0"],
        covers=("ST-MATCH", "DT-ENUM")),

    # --- P1 --------------------------------------------------------------------
    # An unmatched subject with no default is an error (20.7.10 g; R2).
    # SV fell through silently (W16).
    "a match nothing matches, with no default": Case("""
component pss_top {""" + ONE + """
  target function void go(int x) {
    match (x) { [1]: a.STS.write_val(10); [2]: a.STS.write_val(20); }
  }
  target function void run() { go(1); go(5); a.STS.write_val(99); }
}""", ["write 32 0x1004 0xa", ERROR],
        covers=("ST-MATCH",)),

    # A range arm (20.7.10) matches every value in it -- `[0..3]` once
    # matched only 0.
    "match arms with ranges": Case("""
component pss_top {""" + ONE + """
  target function void go(int x) {
    match (x) {
      [0..3]: a.STS.write_val(1);
      [4, 6..7]: a.STS.write_val(2);
      default: a.STS.write_val(3);
    }
  }
  target function void run() { go(0); go(3); go(4); go(5); go(7); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x1", "write 32 0x1004 0x2",
       "write 32 0x1004 0x3", "write 32 0x1004 0x2"],
        covers=("ST-MATCH",)),

    # --- P3: arms with several values (20.7.10) -------------------------------
    # An arm lists values and ranges; the first arm holding the subject runs.
    "arms with several values and ranges, in a loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    repeat (i : 9) {
      match (i) {
        [0, 2, 4]: message(LOW, "%d even", i);
        [1, 3, 5..7]: { if (i == 6) { continue; } message(LOW, "%d odd", i); }
        default: { message(LOW, "%d big", i); break; }
      }
    }
  }
}""", ["0 even", "1 odd", "2 even", "3 odd", "4 even", "5 odd", "7 odd",
       "8 big"],
        covers=("ST-MATCH", "ST-BRK", "ST-REPEAT")),
}

test_construct = define_tests(CASES)
