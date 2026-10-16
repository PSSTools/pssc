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

    # --- P3: if / else (20.7.9) ----------------------------------------------------
    # An else-if chain, unbraced bodies, and a dangling `else`, which belongs
    # to the nearest `if`.
    "if, else if, unbraced, and a dangling else": Case("""
component pss_top {""" + ONE + """
  target function void go(int x) {
    if (x < 0) message(LOW, "neg");
    else if (x == 0) message(LOW, "zero");
    else message(LOW, "pos");
    if (x > 5) if (x > 10) message(LOW, "big"); else message(LOW, "mid");
  }
  target function void run() { go(-1); go(0); go(7); go(11); go(3); }
}""", ["neg", "zero", "pos", "mid", "pos", "big", "pos"],
        covers=("ST-IF",)),

    # --- P3: nested loops (20.7.6-20.7.11) -------------------------------------
    # `continue` and `break` act on the innermost loop only (IP-84: its
    # block's locals are gone after it, and the name is free again).
    "break and continue in an inner loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    repeat (i : 3) {
      int j = 0;
      while (j < 5) {
        j += 1;
        if (j == 2) { continue; }
        if (j == 4) { break; }
        message(LOW, "%d %d", i, j);
      }
    }
    repeat (3) { int t = 5; message(LOW, "t %d", t); break; }
    int t = 6;
    message(LOW, "t %d", t);
  }
}""", ["0 1", "0 3", "1 1", "1 3", "2 1", "2 3", "t 5", "t 6"],
        covers=("ST-BRK", "ST-WHILE", "ST-REPEAT", "ST-BLOCK", "ST-VAR",
                "IP-84")),

    # `continue` in `repeat ... while` goes to the condition (20.7.11 d,
    # IP-3): a false condition ends the loop there. In an inner `foreach`, it
    # advances only that loop (IP-2).
    "continue in repeat-while": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 3> a = {1, 2, 3};
    int n = 0;
    int m = 0;
    int r = 0;
    repeat { n += 1; if (n == 2) { continue; } message(LOW, "rw %d", n); }
    while (n < 4);
    repeat { m += 1; if (m == 1) { continue; } message(LOW, "never"); }
    while (m < 1);
    message(LOW, "m %d", m);
    repeat {
      foreach (v : a) { if (v == 2) { continue; } message(LOW, "%d %d", r, v); }
      r += 1;
    } while (r < 2);
  }
}""", ["rw 1", "rw 3", "rw 4", "m 1", "0 1", "0 3", "1 1", "1 3"],
        covers=("ST-RWHILE", "ST-BRK", "ST-FOREACH", "IP-2", "IP-3")),

    # --- P3: return (20.7.5) ---------------------------------------------------------
    # A `return` inside a `match` inside nested loops leaves the function,
    # its value converted to the result type (IP-4): 1*16 + 2 + 256 is 0x12
    # in a bit[8].
    "return from inside nested loops and a match": Case("""
component pss_top {""" + ONE + """
  target function bit[8] find(int want) {
    repeat (i : 4) {
      repeat (j : 4) {
        match (j) {
          [2]: { if (i == want) { return i * 16 + j + 256; } }
          default: { }
        }
      }
    }
    return 0xEE;
  }
  target function void run() { message(LOW, "%x %x", find(1), find(9)); }
}""", ["12 ee"], covers=("ST-RET", "ST-MATCH", "ST-REPEAT", "EX-WIDTH",
                         "IP-4")),

    # A `return;` inside a loop ends the whole function (IP-5).
    "return from a loop in a void function": Case("""
component pss_top {""" + ONE + """
  target function void go(int n) {
    array<int, 4> a = {1, 2, 3, 4};
    foreach (v : a) {
      if (v == n) { return; }
      message(LOW, "%d", v);
    }
    message(LOW, "end");
  }
  target function void run() { go(3); go(7); }
}""", ["1", "2", "1", "2", "3", "4", "end"],
        covers=("ST-RET", "ST-FOREACH", "IP-5")),

    # --- P3: calls in loop control --------------------------------------------------
    # A call in a `while` condition runs once per test: iterations + 1 times
    # (IP-8); a `repeat` count once (IP-9). The callee returns from inside a
    # `repeat ... while` (IP-83). Its count lives in a struct passed by
    # handle, so nothing writes a component field from target code.
    "calls in a while condition and a repeat count": Case("""
struct ctr_s { int n; }
component pss_top {""" + ONE + """
  target function int next(ctr_s c) {
    repeat { c.n += 1; return c.n; } while (true);
    return -1;
  }
  target function void run() {
    ctr_s c;
    int it = 0;
    while (next(c) < 3) { message(LOW, "in %d", c.n); }
    message(LOW, "n %d", c.n);
    repeat (next(c)) { it += 1; }
    message(LOW, "%d %d", it, c.n);
  }
}""", ["in 1", "in 2", "n 3", "4 4"],
        covers=("ST-WHILE", "ST-REPEAT", "ST-RWHILE", "ST-RET", "FN-CALL",
                "IP-8", "IP-9", "IP-83")),

    # --- P3: short-circuit (8.4.4) ---------------------------------------------------
    # The right operand of `||` runs only when the left is false, of `&&` only
    # when the left is true (IP-29); only the chosen arm of `?:` runs (IP-30).
    "short-circuit operators and the conditional with calls": Case("""
struct ctr_s { int n; }
component pss_top {""" + ONE + """
  target function bool hit(ctr_s c, bool v) { c.n += 1; return v; }
  target function bit[8] val(ctr_s c, bit[8] v) { c.n += 10; return v; }
  target function void run() {
    ctr_s c;
    repeat (i : 3) {
      if (i == 1 || hit(c, false)) { message(LOW, "t %d", i); }
    }
    if (c.n == 9 && hit(c, true)) { message(LOW, "never"); }
    message(LOW, "n %d", c.n);
    bit[8] v = (c.n == 2) ? val(c, 5) : val(c, 6);
    message(LOW, "%d %d", v, c.n);
  }
}""", ["t 1", "n 2", "5 12"],
        covers=("EX-LOG.2", "EX-LOG.3", "EX-COND.2", "FN-CALL", "IP-29",
                "IP-30")),

    # --- P3: scopes (20.7.1, 20.7.8 e) -------------------------------------------
    # A block's local hides a field of the same name, which is untouched and
    # visible again after the block (IP-22); a nested `repeat` index hides the
    # outer one without changing the outer loop's count (IP-25).
    "a local hiding a field, and nested indexes of one name": Case("""
component pss_top {""" + ONE + """
  int f = 5;
  target function void run() {
    {
      int f = 1;
      f += 1;
      message(LOW, "%d", f);
    }
    message(LOW, "%d", f);
    repeat (i : 2) { repeat (i : 2) { message(LOW, "i %d", i); } }
  }
}""", ["2", "5", "i 0", "i 1", "i 0", "i 1"],
        covers=("ST-BLOCK", "EX-REF", "ST-REPEAT", "IP-22", "IP-25")),

    # --- P3: compile if (19.2) ---------------------------------------------------------
    # Statements chosen by a constant at compile time (IP-59).
    "compile if choosing statements": Case("""
package cfg_pkg { static const int MODE = 2; }
component pss_top {""" + ONE + """
  target function void run() {
    compile if (cfg_pkg::MODE == 2) {
      message(LOW, "two");
    } else {
      message(LOW, "other");
    }
    compile if (cfg_pkg::MODE > 5) { message(LOW, "big"); }
  }
}""", ["two"], covers=("ST-CIF", "DT-CONST", "IP-59")),

    # --- P3: yield (20.7.14) ------------------------------------------------------------
    # A `yield` gives other threads a turn; with none, the trace is the
    # loop's.
    "yield in a loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    repeat (i : 2) { a.STS.write_val(i); yield; }
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x1"],
        covers=("ST-YIELD", "ST-REPEAT")),
}

test_construct = define_tests(CASES)
