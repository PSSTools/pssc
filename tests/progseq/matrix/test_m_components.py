"""Component construction (LRM 20.1.2), sub-components and their
operations, solve functions.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import ALL, Case, define_tests, on, ONE, PY_LAZY_SUB, Refused

W66 = ("W66: `in` over a component array compares the components by value: "
       "the C/C++ does not compile")

F30 = "F30: `==` / assignment of component arrays is accepted (7.9.2)"
W60 = ("W60: solve-ness is classified by short name across the model, so a "
       "target function sharing a solve function's name is refused")

#: C and C++ do not lower init blocks yet (P6).
INIT_NOT_LOWERED = {t: Refused([r"`exec init_down` is not lowered by "
                                r"'op-model-c(pp)?' yet"])
                    for t in ("c", "cpp")}


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
}""", ["write 32 0x1004 0x10"], covers=("EXEC-KIND", "FN-CALL", "EX-REF.4")),

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
}""", ["write 32 0x1004 0x1"], covers=("FN-CALL", "ST-VCALL.5")),

    "a sub-component's register written from above": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
}
component pss_top {
  sub_c s;
  solve function void initialize(addr_handle_t base) { s.initialize(base); }
  target function void run() { s.a.STS.write_val(1); }
}""", ["write 32 0x1004 0x1"], covers=("EX-REF", "LIB-REG", "EX-REF.1")),

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
        covers=("FN-CALL", "ST-FOREACH", "EX-IDX", "DT-ARR.15", "EX-IDX.6",
                "EX-REF.4", "FN-CALL.5")),

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
}""", ["write 32 0x1004 0x8", "write 32 0x1004 0x4"], covers=("FN-SOLVE",
                                                              "FN-DECL.3",
                                                              "FN-SOLVE.1",
                                                              "FN-SOLVE.6")),

    # Target context may not call one: the platform runs `run`, and a solve
    # function exists only while the tree is built.
    "a target function calling a solve function": Case("""
component pss_top {""" + ONE + """
  int x = 0;
  solve function void setup(int v) { x = v; }
  target function void run() { setup(1); a.STS.write_val(x); }
}""", Refused([r"'setup' is a solve function", r"target context"]),
        covers=("FN-SOLVE", "FN-DECL.5", "FN-CALL.6", "FN-SOLVE.1")),

    # --- P4: interaction patterns ---------------------------------------------------
    # The iterator of a `foreach` over an array of components is the element
    # (20.7.8): its fields are the element's, its functions the element's.
    # The constructor writes them (target code may not: 20.7.3 b, R13).
    # C, C++ and SV had no type to declare it with, and Python refused it
    # (W36).
    "foreach with an iterator over a component array": Case("""
component sub_c {
  int id;
  target function void hi(int k) { message(LOW, "hi %d %d", id, k); }
}
component pss_top {
  ga_c a;
  sub_c s[3];
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    foreach (c : s[i]) { c.id = i * 10 + 1; }
  }
  target function void run() { foreach (c : s) { c.hi(c.id + 1); } }
}""", ["hi 1 2", "hi 11 12", "hi 21 22"],
        covers=("ST-FOREACH", "EX-REF", "FN-CALL", "DT-ARR.15",
                "ST-FOREACH.9")),

    # The same in `init_down`, as Example 347 configures cores (IP-89).
    # C and C++ do not lower init blocks yet (P6).
    "foreach over a component array in init_down": Case("""
component sub_c { int id; }
component pss_top {""" + ONE + """
  sub_c s[3];
  exec init_down { foreach (c : s[i]) { c.id = i * 10 + 1; } }
  target function void run() {
    message(LOW, "%d %d %d", s[0].id, s[1].id, s[2].id);
  }
}""", ["1 11 21"],
        refused={t: Refused([r"`exec init_down` is not lowered by "
                             r"'op-model-c(pp)?' yet"])
                 for t in ("c", "cpp")},
        covers=("ST-FOREACH", "EX-REF", "EXEC-KIND", "IP-89", "ST-FOREACH.9",
                "ST-ASSIGN.2")),

    # Initialization order (20.1.2, Example 280; IP-88): `init_down` runs
    # parent first, so the child's block sees and overwrites the parent's
    # value (1, then 12); `init_up` runs child first, so the parent's block
    # overwrites the child's (5, then 56).
    "init_down and init_up: who writes last": Case("""
component sub_c {
  int d; int u;
  exec init_down { d = d * 10 + 2; }
  exec init_up { u = 5; }
}
component pss_top {""" + ONE + """
  sub_c s;
  exec init_down { s.d = 1; }
  exec init_up { s.u = s.u * 10 + 6; }
  target function void run() { message(LOW, "%d %d", s.d, s.u); }
}""", ["12 56"],
        refused={t: Refused([r"`exec init_down` is not lowered by "
                             r"'op-model-c(pp)?' yet"])
                 for t in ("c", "cpp")},
        covers=("EXEC-KIND", "EX-REF", "IP-88", "EXEC-KIND.1")),

    # A solve function reached from target context through an unqualified
    # function is an error too (20.2.1.3, IP-97): the gate follows the call.
    "a solve function reached from a target function through another": Case("""
component pss_top {""" + ONE + """
  solve function int setup() { return 5; }
  function int mid() { return setup(); }
  target function void run() { message(LOW, "%d", mid()); }
}""", Refused([r"pss_top::mid: cannot lower call: 'setup' is a solve function",
               r"target context"]),
        covers=("FN-SOLVE", "FN-CALL", "IP-97", "FN-DECL.5", "FN-CALL.6")),

    # --- R13: target code does not write component fields (20.7.3 b, 9.1.6) ----
    # A component's fields are set while it is initialized; target code
    # reading them is fine, writing them is refused -- directly, or through a
    # function target code calls (IP-85).
    "a target function writing a component field": Case("""
component pss_top {""" + ONE + """
  int n;
  target function void run() { n += 1; a.STS.write_val(n); }
}""", Refused([r"pss_top::run: writes component field 'n'", r"R13"]),
        covers=("DT-MUT", "EXEC-KIND", "IP-85", "DT-MUT.1", "EXEC-KIND.11",
                "ST-ASSIGN.2")),

    "a field written by a function target code reaches": Case("""
component sub_c { int x; function void bump() { x += 1; } }
component pss_top {""" + ONE + """
  sub_c s;
  target function void run() { s.bump(); a.STS.write_val(s.x); }
}""", Refused([r"sub_c::bump: writes component field 'x', reached from "
               r"target function 'pss_top::run'"]),
        covers=("FN-CALL", "IP-85", "FN-NATIVE.3")),

    # The constructor, and a solve function it calls, may write fields; the
    # operation reads them.
    "fields written while the component is constructed": Case("""
component pss_top {
  ga_c a;
  int n;
  array<bit[8], 2> w;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    n = 3;
    fill();
  }
  solve function void fill() { w[1] = n + 1; }
  target function void run() { a.STS.write_val(n * 16 + w[1]); }
}""", ["write 32 0x1004 0x34"], covers=("FN-SOLVE", "IP-85", "DT-MUT.1",
                                        "FN-NATIVE.3", "ST-ASSIGN.2")),

    # Run-time state that must outlive one call is kept in a channel: each
    # read a `try_get`, each update a `try_put` (the workaround R13 leaves).
    "run-time state kept in a channel": Case("""
import sync_pkg::*;
component pss_top {""" + ONE + """
  channel_c<bit[32]> count;
  target function bit[32] bump() {
    bit[32] n = 0;
    if (!count.try_get(n)) { n = 0; }
    n += 1;
    (void)count.try_put(n);
    return n;
  }
  target function void run() {
    repeat (3) { a.STS.write_val(bump()); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x2", "write 32 0x1004 0x3"],
        covers=("LIB-CHAN", "FN-CALL")),

    # --- P5.3: component arrays, mutable fields, solve context ------------------
    # A component array offers `[]`, `size()`, `foreach` and `in` only
    # (7.9.2); membership is by identity (8.5.9.1): `t[0]` is not in `s`.
    "size() of and in over a component array": Case("""
component sub_c { }
component pss_top {""" + ONE + """
  sub_c s[3];
  sub_c t[2];
  target function void run() {
    message(LOW, "%d", s.size());
    if (s[1] in s) { message(LOW, "in"); }
    if (t[0] in s) { message(LOW, "bad"); } else { message(LOW, "out"); }
  }
}""", ["3", "in", "out"],
        xfail=on(("c", "cpp"), W66),
        covers=("DT-ARR.15", "EX-IN.10")),

    # ...and nothing else: `==` and assignment of component arrays are errors.
    "a component array compared": Case("""
component sub_c { }
component pss_top {""" + ONE + """
  sub_c s[2];
  sub_c t[2];
  target function void run() { if (s == t) { message(LOW, "eq"); } }
}""", Refused([r"component array"]), xfail=on(ALL, F30),
        covers=("DT-ARR.15",)),

    "a component array assigned": Case("""
component sub_c { }
component pss_top {
  ga_c a;
  sub_c s[2];
  sub_c t[2];
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    s = t;
  }
  target function void run() { message(LOW, "run"); }
}""", Refused([r"component array"]), xfail=on(ALL, F30),
        covers=("DT-ARR.15",)),

    # `mutable` fields (9.1.6) may be written by solve-time code, which in an
    # operation model is construction; the qualifier reaches a struct's
    # members. n = 3, bumped to 4, copied into q.b.
    "mutable fields written while the component is constructed": Case("""
struct q_s { int b; }
component pss_top {
  ga_c a;
  mutable int n;
  mutable q_s q;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    n = 3;
    bump();
    q.b = n;
  }
  solve function void bump() { n += 1; }
  target function void run() { message(LOW, "%d %d", n, q.b); }
}""", ["4 4"], covers=("DT-MUT.2", "FN-SOLVE.1")),

    # ...but not from target code, `mutable` or not (9.1.6, R13).
    "a target function writing a mutable field": Case("""
component pss_top {""" + ONE + """
  mutable int n;
  target function void run() { n = 2; a.STS.write_val(n); }
}""", Refused([r"pss_top::run: writes component field 'n'", r"R13"]),
        covers=("DT-MUT.2",)),

    # A target function is not callable from solve context (20.2.1.3), and
    # `set_handle` only from solve context (21.14.2).
    "a constructor calling a target function": Case("""
component pss_top {
  ga_c a;
  int k;
  target function int t() { return 3; }
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    k = t();
  }
  target function void run() { a.STS.write_val(k); }
}""", Refused([r"'t' is an operation of this model", r"solve context"]),
        covers=("FN-SOLVE.2", "FN-CALL.6")),

    "set_handle in a target function": Case("""
component pss_top {""" + ONE + """
  addr_handle_t h;
  target function void run() { a.set_handle(h); a.STS.write_val(1); }
}""", Refused([r"'set_handle' is legal only in a solve context"]),
        covers=("FN-SOLVE.6",)),

    # Whether `func5` is a solve or a target function depends on the type it
    # is called on (Example 297): c1's from the constructor, c2's from run.
    # k = 1 + 1, then 2 + (1 + 2).
    "a function solve in one derived type and target in another": Case("""
component base_c { function int func5(int x); }
component c1_c : base_c { solve function int func5(int x) { return x + 1; } }
component c2_c : base_c { target function int func5(int x) { return x + 2; } }
component pss_top {
  ga_c a;
  c1_c c1;
  c2_c c2;
  int k;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    k = c1.func5(1);
  }
  target function void run() { a.STS.write_val(k + c2.func5(1)); }
}""", ["write 32 0x1004 0x5"], xfail=on(ALL, W60),
        covers=("FN-SOLVE.4", "FN-CALL.6")),

    # --- P5.3: exec blocks of one kind, extension, `super;` -------------------
    # Several blocks of a kind in one scope are one block, in source order
    # (20.1.1 d), and an extension's block runs after the definition's
    # (20.1.4.3): v = 1, 12, 123; d = 1, 12.
    "init_down blocks merged in order, an extension's last": Case("""
component sub_c {
  int d;
  exec init_down { d = 1; }
  exec init_down { d = d * 10 + 2; }
}
component pss_top {""" + ONE + """
  sub_c s;
  int v;
  exec init_down { v = 1; }
  exec init_down { v = v * 10 + 2; }
  target function void run() { message(LOW, "%d %d", v, s.d); }
}
extend component pss_top { exec init_down { v = v * 10 + 3; } }""",
        ["123 12"], refused=INIT_NOT_LOWERED,
        covers=("EXEC-KIND.7", "EXEC-KIND.9")),

    # `super;` in a derived component's init block runs its base's blocks of
    # that kind there (20.1.4.2): first, last or in the middle, through two
    # levels, the base's two init_down blocks as one. `return;` ends the
    # block it is in (20.7.5): "never" is not printed. Whether a base's
    # `return;` ends the derived block too is unstated (ST-SUPER.7); each
    # base part is its own block here, so "md" and "dd" follow. One instance
    # only: the order between siblings is undefined (20.1.2).
    "super; in init blocks": Case("""
component base_c {
  exec init_down { message(LOW, "bd1"); }
  exec init_down { message(LOW, "bd2"); return; message(LOW, "never"); }
  exec init_up { message(LOW, "bu"); }
}
component mid_c : base_c {
  exec init_down { super; message(LOW, "md"); }
  exec init_up { message(LOW, "mu"); super; }
}
component der_c : mid_c {
  exec init_down { message(LOW, "dd0"); super; message(LOW, "dd"); }
}
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { message(LOW, "run"); }
}""", ["dd0", "bd1", "bd2", "md", "dd", "mu", "bu", "run"],
        refused=INIT_NOT_LOWERED,
        covers=("ST-SUPER.1", "ST-SUPER.3", "ST-SUPER.4", "ST-SUPER.7",
                "ST-RET.3", "EXEC-KIND.7")),

    # A derived block with no `super;` replaces its base's (20.1.4.1,
    # Example 283); the kind it does not declare is inherited.
    "an init block shadowing its base's": Case("""
component base_c {
  exec init_down { message(LOW, "bd1"); }
  exec init_down { message(LOW, "bd2"); }
  exec init_up { message(LOW, "bu"); }
}
component sh_c : base_c { exec init_up { message(LOW, "shu"); } }
component pss_top {""" + ONE + """
  sh_c s;
  target function void run() { message(LOW, "run"); }
}""", ["bd1", "bd2", "shu", "run"], refused=INIT_NOT_LOWERED,
        covers=("ST-SUPER.2",)),
}

test_construct = define_tests(CASES)
