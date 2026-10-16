"""Functions: declaration, parameters, calls and results (LRM 20.3, 22).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, Refused


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

    # --- P3: recursion (20.2.7) ----------------------------------------------------
    # Each call has its own parameters and locals (IP-11).
    "recursive functions": Case("""
component pss_top {""" + ONE + """
  target function bit[32] fact(bit[32] n) {
    if (n <= 1) { return 1; }
    bit[32] r = n * fact(n - 1);
    return r;
  }
  target function int fib(int n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
  }
  target function void run() { message(LOW, "%d %d", fact(10), fib(10)); }
}""", ["3628800 55"], covers=("FN-CALL", "FN-PARAM", "ST-VAR", "IP-11")),

    # A parameter named like a field is the parameter in the body (20.3.2,
    # IP-23): assigning it leaves the field.
    "a parameter named like a field": Case("""
component pss_top {""" + ONE + """
  int v = 7;
  target function void set(int v) { v = v + 1; message(LOW, "p %d", v); }
  target function void run() { set(1); message(LOW, "f %d", v); }
}""", ["p 2", "f 7"], covers=("FN-PARAM", "EX-REF", "IP-23")),

    # A `const` scalar parameter is read like any other (20.2.3), and a call's
    # result may be discarded with `(void)` (20.7.4).
    "a const parameter and a discarded result": Case("""
struct ctr_s { int n; }
component pss_top {""" + ONE + """
  target function int twice(const int x) { return x * 2; }
  target function int bump(ctr_s c) { c.n += 1; return c.n; }
  target function void run() {
    ctr_s c;
    (void)bump(c);
    bump(c);
    message(LOW, "%d %d", twice(21), c.n);
  }
}""", ["42 2"], covers=("FN-PARAM", "FN-CALL", "DT-CAST.9", "ST-VCALL")),

    # Component functions are virtual (user ruling): a base function calling
    # `f()` in a loop reaches the derived override, which calls the base's
    # with `super.f()` (IP-62).
    "a virtual call in a loop and super": Case("""
component base_c {
  target function int f(int i) { return i + 1; }
  target function int sum(int n) {
    int s = 0;
    repeat (i : n) { s = s + f(i); }
    return s;
  }
}
component der_c : base_c {
  target function int f(int i) { return super.f(i) * 10; }
}
component pss_top {""" + ONE + """
  der_c d;
  base_c b;
  target function void run() { message(LOW, "%d %d", d.sum(3), b.sum(3)); }
}""", ["60 6"], covers=("FN-VIRT", "ST-REPEAT", "IP-62")),

    # A function declared in a package, called qualified and unqualified, and
    # a `pure` one (20.2, 20.2.1.2).
    "package and pure functions": Case("""
package util_pkg {
  pure function int sq(int x) { return x * x; }
  function int add1(int x) { return x + 1; }
}
component pss_top {""" + ONE + """
  target function void run() {
    message(LOW, "%d %d", util_pkg::sq(5), util_pkg::add1(util_pkg::sq(2)));
  }
}""", ["25 5"],
        refused={t: Refused([r"'util_pkg::sq' is a package-scope function",
                             r"'util_pkg::add1'"])
                 for t in ("c", "cpp")},
        covers=("FN-DECL", "FN-PURE", "FN-CALL")),
}

test_construct = define_tests(CASES)
