"""Component construction (LRM 20.1.2), sub-components and their
operations, solve functions.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, PY_LAZY_SUB, Refused


CASES = {
    # --- construction (LRM 20.1.2) -------------------------------------------
    # Every instance is constructed, whether or not a constructor reaches it.
    # C embeds a child by value, so a child its parent's constructor never
    # named read zeroes -- `int a = 5;` was 0, silently.
    "sub-components nobody initializes": Case("""
component inner_c { int k = 3; }
component sub_c {
  int a = 5;
  inner_c in1;
  target function int sum() { return a + in1.k; }
}
component pss_top {
  ga_c a;
  sub_c s;
  sub_c arr[2];
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(s.sum() + arr[1].sum()); }
}""", ["write 32 0x1004 0x10"], covers=("EXEC-KIND", "FN-CALL")),

    # ...including one whose type has a constructor nobody calls.
    "a sub-component whose constructor nobody calls": Case("""
component cfg_c {
  int a = 10;
  int b = 1;
  solve function void initialize(int x) { b = a + x; }
  target function int get() { return a + b; }
}
component pss_top {
  ga_c a;
  cfg_c c;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(c.get()); }
}""", ["write 32 0x1004 0xb"], xfail=on(["py"], PY_LAZY_SUB),
        covers=("FN-SOLVE",)),

    # A child is constructed before its parent's body runs, so what that body
    # writes into it (`c.a = 20`) is there when the child's constructor runs.
    "a constructor writes into a child before constructing it": Case("""
component cfg_c {
  int a = 10;
  int b;
  solve function void initialize(int x) { b = a + x; }
}
component pss_top {
  ga_c a;
  cfg_c c;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    c.a = 20;
    c.initialize(1);
  }
  target function void run() { a.STS.write_val(c.b); }
}""", ["write 32 0x1004 0x15"], xfail={"py": PY_LAZY_SUB},
        covers=("FN-SOLVE", "EX-REF")),

    # A constructor is a solve function like any other: locals, conditions
    # and loops. SV lowered only the address binding and refused the rest.
    # t = 2 + 4 = 6, then added three times.
    "a constructor with a local, a condition and a loop": Case("""
component pss_top {
  ga_c a;
  int x = 0;
  solve function void initialize(addr_handle_t base, int m) {
    int t = 2;
    a.set_handle(base);
    if (m > 1) { t = t + m; } else { t = 0; }
    repeat (3) { x = x + t; }
  }
  target function void run() { a.STS.write_val(x); }
}""", ["write 32 0x1004 0x12"], args=(0x1000, 4),
        covers=("FN-SOLVE", "ST-VAR", "ST-IF", "ST-REPEAT")),

    # --- D2: operations of a sub-component -------------------------------------
    "calling a sub-component's operation": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void poke() { a.STS.write_val(1); }
}
component pss_top {
  sub_c s;
  solve function void initialize(addr_handle_t base) { s.initialize(base); }
  target function void run() { s.poke(); }
}""", ["write 32 0x1004 0x1"], covers=("FN-CALL",)),

    "a sub-component's register written from above": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
}
component pss_top {
  sub_c s;
  solve function void initialize(addr_handle_t base) { s.initialize(base); }
  target function void run() { s.a.STS.write_val(1); }
}""", ["write 32 0x1004 0x1"], covers=("EX-REF", "LIB-REG")),

    "operations of a sub-component array": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void poke() { a.STS.write_val(1); }
}
component pss_top {
  sub_c s[2];
  solve function void initialize(addr_handle_t base) {
    foreach (s[i]) { s[i].initialize(base + 0x100 * i); }
  }
  target function void run() {
    s[1].poke();
    foreach (s[i]) { s[i].poke(); }
  }
}""", ["write 32 0x1104 0x1", "write 32 0x1004 0x1", "write 32 0x1104 0x1"],
        covers=("FN-CALL", "ST-FOREACH", "EX-IDX")),

    "a sub-component's value in a condition": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function bit[32] sts() { return a.STS.read_val(); }
  target function void poke() { a.STS.write_val(1); }
}
component pss_top {
  sub_c s;
  solve function void initialize(addr_handle_t base) { s.initialize(base); }
  target function void run() { if (s.sts() != 0) { s.poke(); } }
}""", ["read 32 0x1004 0x5", "write 32 0x1004 0x1"], mem={0x1004: 5},
        covers=("FN-CALL", "ST-IF", "EX-EQ")),

    # --- solve functions other than the constructor (LRM 22.2.3) ----------------
    # Private operations: the constructor calls them, they call each other, a
    # parent's constructor calls a child's. x = 7, then bump: 8; k = 3 + 1.
    # Every target refused the call ("no function named 'setup'").
    "solve functions the constructor calls": Case("""
component sub_c {
  int k = 1;
  solve function void prep(int v) { k = v + k; }
  function int get() { return k; }
}
component pss_top {
  ga_c a;
  int x = 0;
  sub_c s;
  solve function void setup(int v) { x = v; bump(); }
  solve function void bump() { x = x + 1; }
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    setup(7);
    s.prep(3);
  }
  target function void run() { a.STS.write_val(x); a.STS.write_val(s.get()); }
}""", ["write 32 0x1004 0x8", "write 32 0x1004 0x4"], covers=("FN-SOLVE",)),

    # Target context may not call one: the platform runs `run`, and a solve
    # function exists only while the tree is built.
    "a target function calling a solve function": Case("""
component pss_top {""" + ONE + """
  int x = 0;
  solve function void setup(int v) { x = v; }
  target function void run() { setup(1); a.STS.write_val(x); }
}""", Refused([r"'setup' is a solve function", r"target context"]),
        covers=("FN-SOLVE",)),
}

test_construct = define_tests(CASES)
