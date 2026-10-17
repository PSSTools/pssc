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
        covers=("ST-MATCH", "ST-MATCH.1", "ST-MATCH.3")),

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
        covers=("ST-MATCH", "DT-ENUM", "ST-MATCH.6")),

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
        covers=("ST-MATCH", "ST-MATCH.5")),

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
        covers=("ST-MATCH", "ST-MATCH.2")),

    # --- P3: arms with several values (20.7.10) -------------------------------
    # An arm lists values and ranges; the arm holding the subject runs.
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

    # --- P4: overlapping choices (20.7.10 d) ----------------------------------
    # More than one matching choice is an error, as no matching choice is
    # (R2). A range bound known only at run time makes two choices overlap
    # on some iterations only (IP-36): 3 is in [3..5] alone until `hi`
    # reaches 3. Every target took the first choice silently (W34).
    "choices that overlap on some iterations": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    repeat (i : 4) {
      int hi = 1 + i;
      match (3) {
        [0..hi]: message(LOW, "%d low", i);
        [3..5]: message(LOW, "%d high", i);
      }
    }
  }
}""", ["0 high", "1 high", ERROR],
        covers=("ST-MATCH", "ST-REPEAT", "IP-36", "ST-MATCH.4", "ST-MATCH.8")),

    # Constant choices that overlap are an error only for a value in both;
    # a subject with a call is read once (`next(c)` runs once per match).
    "constant choices that overlap, with a call as the subject": Case("""
struct ctr_s { int n; }
component pss_top {""" + ONE + """
  target function int next(ctr_s c) { c.n += 1; return c.n; }
  target function void run() {
    ctr_s c;
    repeat (2) {
      match (next(c)) {
        [0..1]: message(LOW, "low %d", c.n);
        [2, 3]: message(LOW, "mid %d", c.n);
        [3..9]: message(LOW, "high %d", c.n);
      }
    }
    match (next(c)) {
      [0..1]: message(LOW, "low %d", c.n);
      [2, 3]: message(LOW, "mid %d", c.n);
      [3..9]: message(LOW, "high %d", c.n);
    }
  }
}""", ["low 1", "mid 2", ERROR],
        covers=("ST-MATCH", "FN-CALL", "ST-MATCH.4", "ST-MATCH.10")),

    # --- P5.3: choice typing and nesting ----------------------------------------------
    # The subject meets its choices as the left side of `in` meets its range
    # list (8.5.9.1, Table 22): bit[2] x + y against `int` choices is
    # evaluated at 32 bits, so 2 + 2 is 4, not 0. (No [0] choice: a bound
    # cast to the subject's type would make [4] and [0] overlap.)
    "match choices meet the subject at a common type": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[2] x, bit[2] y) {
    match (x + y) {
      [4]: message(LOW, "four");
      [5]: message(LOW, "five");
      default: message(LOW, "def");
    }
  }
  target function void run() { go(2, 2); go(3, 2); go(1, 1); }
}""", ["four", "five", "def"],
        covers=("ST-MATCH.9", "EX-IN.6")),

    # A match nested in an arm, braced and unbraced; the inner `default`
    # belongs to the inner match.
    "a match nested in match arms": Case("""
component pss_top {""" + ONE + """
  target function void go(int x, int y) {
    match (x) {
      [0]: {
        match (y) {
          [0]: message(LOW, "00");
          default: message(LOW, "0y");
        }
      }
      default:
        match (y) {
          [1]: message(LOW, "x1");
          default: { message(LOW, "xy"); }
        }
    }
  }
  target function void run() { go(0, 0); go(0, 5); go(3, 1); go(3, 2); }
}""", ["00", "0y", "x1", "xy"], covers=("ST-MATCH.11", "ST-MATCH.3")),
}

test_construct = define_tests(CASES)
