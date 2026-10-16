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
  target function void run() {
    int n = 3;
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
       "write 32 0x1004 0x6"], covers=("ST-REPEAT", "ST-BRK", "ST-ASSIGN.1",
                                       "ST-BRK.1", "ST-BRK.2", "ST-BRK.6",
                                       "ST-REPEAT.1", "ST-REPEAT.2",
                                       "ST-REPEAT.3", "ST-REPEAT.4")),

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
        covers=("ST-BRK", "ST-MATCH", "ST-REPEAT", "IP-1", "ST-BRK.3",
                "ST-BRK.5")),

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
        covers=("ST-IF", "ST-IF.1", "ST-IF.2", "ST-IF.3", "ST-IF.4",
                "ST-VCALL.1")),

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
                "IP-84", "ST-BLOCK.1", "ST-BRK.1", "ST-BRK.2", "ST-BRK.4",
                "ST-BRK.6", "ST-VAR.7", "ST-VAR.9", "ST-WHILE.3")),

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
        covers=("ST-RWHILE", "ST-BRK", "ST-FOREACH", "IP-2", "IP-3",
                "ST-BRK.6", "ST-RWHILE.1", "ST-RWHILE.2", "ST-RWHILE.3")),

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
                         "IP-4", "ST-RET.1", "ST-RET.4")),

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
        covers=("ST-RET", "ST-FOREACH", "IP-5", "FN-NATIVE.2", "FN-PARAM.9",
                "ST-RET.2")),

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
                "IP-8", "IP-9", "IP-83", "ST-REPEAT.6", "ST-WHILE.4")),

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
                "IP-30", "ST-IF.5")),

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
        covers=("ST-BLOCK", "EX-REF", "ST-REPEAT", "IP-22", "IP-25",
                "EX-REF.8", "ST-BLOCK.3", "ST-REPEAT.5")),

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
}""", ["two"], covers=("ST-CIF", "DT-CONST", "IP-59", "DT-CONST.7",
                       "ST-CIF.1", "ST-CIF.3")),

    # --- P3: yield (20.7.14) ------------------------------------------------------------
    # A `yield` gives other threads a turn; with none, the trace is the
    # loop's.
    "yield in a loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    repeat (i : 2) { a.STS.write_val(i); yield; }
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x1"],
        covers=("ST-YIELD", "ST-REPEAT", "ST-YIELD.1", "ST-YIELD.2")),

    # --- P4: interaction patterns ---------------------------------------------------
    # `&&` guards the index (IP-28): once `i < 4` is false, `w[i]` is never
    # read, so a loop over a full array stops at its end and a loop over one
    # with a zero stops at the zero.
    "&& guarding an index in a while condition": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 4> x = {3, 0, 5, 6};
    array<bit[8], 4> y = {3, 4, 5, 6};
    int i = 0;
    while (i < 4 && y[i] != 0) { i += 1; }
    message(LOW, "%d", i);
    i = 0;
    while (i < 4 && x[i] != 0) { i += 1; }
    message(LOW, "%d", i);
  }
}""", ["4", "1"], covers=("EX-LOG", "EX-IDX", "ST-WHILE", "IP-28",
                          "ST-WHILE.2")),

    # A register read in a `while` condition, with `yield` in the body
    # (20.8, Example 319; IP-49): the read is repeated per iteration and
    # sees the write. The other half of the pattern, a parallel action
    # blocked on `get()`, is bc's (test_bc_interaction_patterns.py).
    "a register polled in a while condition, yielding": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int n = 0;
    while (a.STS.read_val() == 0) {
      n += 1;
      if (n == 2) { a.STS.write_val(1); }
      yield;
    }
    message(LOW, "done %d", n);
  }
}""", ["read 32 0x1004 0x0", "read 32 0x1004 0x0", "write 32 0x1004 0x1",
       "read 32 0x1004 0x1", "done 2"],
        covers=("ST-WHILE", "ST-YIELD", "LIB-REG", "IP-49", "LIB-REG.2",
                "ST-YIELD.4")),

    # `compile has` sees only what is declared BEFORE it (19.3, IP-60):
    # `later_s` follows, so the `else` is taken.
    "compile has of a type declared later": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    compile if (compile has(later_s)) { message(LOW, "has"); }
    else { message(LOW, "not"); }
    compile if (compile has(ga_c)) { message(LOW, "has ga"); }
  }
}
struct later_s { int x; }""", ["not", "has ga"],
        covers=("ST-CIF", "IP-60", "ST-CIF.4")),

    # --- P5.3: blocks, statements and declarations ------------------------------------
    # Only `&&`, `||` and `?:` short-circuit (8.4.4): an arithmetic or
    # bitwise operator evaluates both operands, so `f() * 0` still calls f.
    "no short-circuit for arithmetic operators": Case("""
component pss_top {""" + ONE + """
  target function int f() { message(LOW, "f"); return 3; }
  target function void run() {
    int x = f() * 0;
    int y = 0 * f();
    int z = 0 & f();
    message(LOW, "%d %d %d", x, y, z);
  }
}""", ["f", "f", "f", "0 0 0"], covers=("EX-ARITH.8", "FN-CALL")),

    # A `sequence` block with its own local (20.7.1.1), the empty statement
    # and empty bodies (B.7: `;` and `{}`), and an annotation the tool does
    # not recognize before a statement, which is ignored: x = 1 + 2 + 1.
    "sequence blocks, empty statements and an annotation": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x = 1;
    sequence { int y = 2; x += y; }
    ;
    {}
    if (x == 3) {} else ;
    repeat (2) ;
    while (x == 0) {}
    @foo x = x + 1;
    message(LOW, "%d", x);
  }
}""", ["4"], covers=("ST-BLOCK.2", "ST-BLOCK.4", "ST-BLOCK.6", "ST-BLOCK")),

    # An unbraced `compile if` branch (19.2.2 NOTE: deprecated, accepted); a
    # disabled branch need only parse (19.1.1), so the undefined names in it
    # are no error; and an enabled branch opens no scope (19.1.1): q is
    # visible after it.
    "compile if: unbraced, a disabled branch, no new scope": Case("""
package cfg_pkg { static const int MODE = 2; }
component pss_top {""" + ONE + """
  target function void run() {
    compile if (cfg_pkg::MODE == 2) message(LOW, "two");
    else message(LOW, "other");
    compile if (cfg_pkg::MODE == 3) {
      int q = nosuch(zz);
      undefined_var = 1;
    } else {
      message(LOW, "ok");
    }
    compile if (cfg_pkg::MODE == 2) { int q = 5; } else { int q = 6; }
    message(LOW, "%d", q);
  }
}""", ["two", "ok", "5"], covers=("ST-CIF.2", "ST-CIF.5", "ST-CIF.6",
                                  "ST-CIF")),

    # Unbraced loop bodies; a `while` tests before its body, so it may run
    # zero times, and `repeat ... while` after it (20.7.6.1, 20.7.7); a call
    # in the condition runs once per test.
    "unbraced loop bodies, while(true), and a call in a repeat-while": Case("""
struct c_s { int n; }
component pss_top {""" + ONE + """
  target function bool t(c_s c) {
    c.n += 1;
    message(LOW, "t %d", c.n);
    return c.n < 3;
  }
  target function void run() {
    int x = 0;
    repeat (3) x += 1;
    while (x < 5) x += 1;
    repeat x += 1; while (x < 7);
    message(LOW, "x %d", x);
    int n = 0;
    while (n != 0) { message(LOW, "never"); }
    while (true) { n += 1; if (n == 3) { break; } }
    message(LOW, "n %d", n);
    c_s c;
    repeat { message(LOW, "b"); } while (t(c));
  }
}""", ["x 7", "n 3", "b", "t 1", "b", "t 2", "b", "t 3"],
        covers=("ST-REPEAT.7", "ST-WHILE.1", "ST-WHILE.5", "ST-RWHILE.4",
                "ST-WHILE", "ST-RWHILE", "ST-BRK")),

    # A `yield` in a function called from the body (20.7.14 a): with no
    # parallel execution it has no effect on the trace (20.7.14 b).
    "yield in a called function": Case("""
component pss_top {""" + ONE + """
  target function void w(int i) {
    message(LOW, "w %d", i);
    yield;
    message(LOW, "w2 %d", i);
  }
  target function void run() { repeat (i : 2) { w(i); } }
}""", ["w 0", "w2 0", "w 1", "w2 1"], covers=("ST-YIELD.5", "ST-YIELD")),

    # Several declarators in one declaration, and C-style array dimensions
    # (20.7.2.1). That b sees a = 1 is not stated by 20.7.2, but follows from
    # a name being visible after its declarator. An uninitialized element is
    # 0.
    "several declarators and an array declarator": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int a = 1, b = a + 1;
    int x[4] = {1, 2, 3, 4};
    bit[8] y[2];
    message(LOW, "%d %d %d %d %d", a, b, x[0], x[3], y[1]);
  }
}""", ["1 2 1 4 0"], covers=("ST-VAR.3", "ST-VAR.4", "ST-VAR")),
}

test_construct = define_tests(CASES)
