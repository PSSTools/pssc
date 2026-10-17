"""Models the LRM makes illegal: each must be refused, at its place.

`test_m_refusals` holds models that are LEGAL PSS which an op-model target
refuses, by ruling (R4, R5, ...) or for now. A model here is an ERROR in the
LRM -- a type, a placement or a use the standard forbids -- and the clause
that forbids it is named with the case. The expected refusal is the error
the LRM requires, located (`_matrix.LOCATED`), whichever layer gives it
(pssparser, ast2ir or the gate). Where a target accepts the model today,
the case is a strict xfail naming the §2 defect (F-series: front-end checks
pssparser does not make yet).

A refusal names the first offending construct and may hide the rest, so each
illegal construct is a case of its own.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import ALL, Case, define_tests, on, ONE, Refused

F1 = ("F1: operand types are not checked (8.4 Table 10): a bool compared "
      "with a number, a bitwise operator on bool")
F3 = ("F3: an unqualified enum item where the expected type is not the enum "
      "is accepted")
F5 = "F5: `rand chandle` is accepted (7.1, 7.7)"
F6 = "F6: access protection is not checked (20.4 b)"
F11 = "F11: a varargs parameter of a PSS-bodied function is accepted (20.2.5)"
F26 = ("F26: import/export parameter types are not checked (20.4.1 c, "
       "20.4.2.1)")
F5_RAND = ("F5: `randomize` of a chandle is accepted (7.1); pssc refuses every "
           "randomize, so the error goes unsaid")
F11_GENERIC = ("F11: a generic parameter of a PSS-bodied function is accepted "
               "(20.2.5); pssc refuses it as unsupported")
F22 = ("F22: an exec placement or call context the LRM forbids is accepted; "
       "pssc refuses it as unsupported (M5)")


CASES = {
    # --- P5.3: data types ----------------------------------------------------
    # A chandle is not randomizable (7.1, 7.7): `rand chandle` is an error.
    "a rand chandle field": Case("""
struct s_s { rand chandle h; int a; }
component pss_top {""" + ONE + """
  target function void run() { s_s v; a.STS.write_val(v.a); }
}""", Refused([r"chandle"]), xfail=on(ALL, F5),
        covers=("DT-CHND.2",)),

    # A packed struct's fields are integral, bool or enum (21.13.1):
    # a string or an address handle is not.
    "a string field in a packed struct": Case("""
struct p_s : packed_s<> { bit[8] v; string s; }
component pss_top {""" + ONE + """
  target function void run() { p_s p; a.STS.write_val(p.v); }
}""", Refused([r"m\.pss:\d+:\d+: error: field 's' of packed struct 'p_s' "
               r"is a string"]), covers=("DT-PACK.2",)),

    "an addr_handle_t field in a packed struct": Case("""
struct p_s : packed_s<> { bit[8] v; addr_handle_t h; }
component pss_top {""" + ONE + """
  target function void run() { p_s p; a.STS.write_val(p.v); }
}""", Refused([r"m\.pss:\d+:\d+: error: field 'h' of packed struct 'p_s'"]),
        covers=("DT-PACK.2", "DT-ADDR.7")),

    # A packed struct's layout is its declaration: an extension may not add
    # a field (21.13.1).
    "an extension adding a field to a packed struct": Case("""
struct p_s : packed_s<> { bit[8] v; }
extend struct p_s { bit[4] b; }
component pss_top {""" + ONE + """
  target function void run() { p_s p; a.STS.write_val(p.v); }
}""", Refused([r"m\.pss:\d+:\d+: error: type extension of packed struct "
               r"'p_s' adds field 'b'"]), covers=("DT-PACK.7",)),

    # A constant is never on the left of an assignment (7.1, 7.9 a): not
    # whole, not an element, not a member; not in a target function and not
    # in a constructor, where py really changes it today.
    "assigning a static const": Case("""
component pss_top {""" + ONE + """
  static const int C = 5;
  target function void run() { C = 2; a.STS.write_val(C); }
}""", Refused([r"m\.pss:\d+:\d+: error: constant 'C' cannot be assigned: a constant "
               r"cannot appear on the left of an assignment \(LRM 7\.1\)"]),
        covers=("DT-CONST.2", "ST-ASSIGN.6")),

    "assigning an element of a static const array": Case("""
component pss_top {
  ga_c a;
  static const array<int, 2> A = {1, 2};
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    A[0] = 7;
  }
  target function void run() { a.STS.write_val(A[0]); }
}""", Refused([r"m\.pss:\d+:\d+: error: an element or member of constant 'A' cannot be "
               r"assigned"]),
        covers=("DT-CONST.2", "ST-ASSIGN.6")),

    "assigning a member of a static const struct": Case("""
struct p_s { int x; }
component pss_top {
  ga_c a;
  static const p_s P = {.x = 1};
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    P.x = 9;
  }
  target function void run() { a.STS.write_val(P.x); }
}""", Refused([r"m\.pss:\d+:\d+: error: an element or member of constant 'P' cannot be "
               r"assigned"]),
        covers=("DT-CONST.2", "ST-ASSIGN.6")),

    # Static-const initializers are evaluated in source order, over the
    # constants declared before them (19.1.2 b; Example 1's `C4`).
    "a constant used before its declaration": Case("""
package k {
  static const int A = B + 1;
  static const int B = 2;
}
component pss_top {""" + ONE + """
  target function void run() { a.STS.write_val(k::A); }
}""", Refused([r"m\.pss:\d+:\d+: error: constant 'B' is used in the "
               r"initializer of 'A' before its declaration"]),
        covers=("DT-CONST.7",)),

    # --- P5.3: expressions ---------------------------------------------------
    # An assignment is a statement, not an expression (8.1, 20.7.3).
    "a chained assignment": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x; int y; int z = 3;
    x = y = z;
    a.STS.write_val(x);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: expected ';' before '='"]),
        covers=("EX-ASSIGN.7",)),

    "an assignment as a condition": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x; int y = 3;
    if (x = y) { a.STS.write_val(x); }
  }
}""", Refused([r"m\.pss:\d+:\d+: error: unexpected '='"]),
        covers=("EX-ASSIGN.7",)),

    "a compound assignment as an operand": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x; int y = 3;
    x = (y += 1);
    a.STS.write_val(x);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: unexpected"]),
        covers=("EX-ASSIGN.7",)),

    # The left of `in` is a single value of the collection's element type
    # (8.5.9.1): not an aggregate literal, not an enum item the context does
    # not type, and a struct only against a collection.
    "an aggregate literal on the left of in": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    if ({1, 2} in [1]) { a.STS.write_val(1); }
  }
}""", Refused([r"m\.pss:\d+:\d+: error: the left side of 'in' shall not be "
               r"an aggregate literal \(LRM 8\.5\.9\.1\)"]),
        covers=("EX-IN.7",)),

    "an unqualified enum item on the left of in": Case("""
enum m_e { M0, M1 }
component pss_top {""" + ONE + """
  target function void run() {
    if (M1 in [M0, M1]) { a.STS.write_val(1); }
  }
}""", Refused([r"\bM1\b"]), xfail=on(ALL, F3), covers=("EX-IN.7",)),

    "a struct on the left of in with a range list": Case("""
struct p_s { int x; }
component pss_top {""" + ONE + """
  target function void run() {
    p_s p;
    if (p in [1]) { a.STS.write_val(1); }
  }
}""", Refused([r"m\.pss:\d+:\d+: error: the left side of 'in' is struct p_s, not a scalar, "
               r"so the right side shall be a collection, not a range list "
               r"\(LRM 8\.5\.9\.1\)"]),
        covers=("EX-IN.7",)),

    # Relational operators take numbers (8.4 Table 10): `a < b` is a bool,
    # and a bool is not ordered against `c`.
    "a chained relational operator": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x = 1; int y = 2; int z = 3;
    if (x < y < z) { a.STS.write_val(1); }
  }
}""", Refused([r"bool"]), xfail=on(ALL, F1), covers=("EX-REL.5",)),

    # Bitwise operators take integers (8.4 Table 10), not bool.
    "a bitwise and of bools": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bool p = true; bool q = false;
    bool r = p & q;
    if (r) { a.STS.write_val(1); }
  }
}""", Refused([r"bool"]), xfail=on(ALL, F1), covers=("EX-BIT.4",)),

    "a bitwise not of a bool": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bool p = true;
    bool r = ~p;
    if (r) { a.STS.write_val(1); }
  }
}""", Refused([r"bool"]), xfail=on(ALL, F1), covers=("EX-BIT.4",)),

    # A private attribute is accessible only from its declaring element
    # (20.4 b, 17.4).
    "a private field read from outside its component": Case("""
component sib_c { private int x = 4; }
component pss_top {""" + ONE + """
  sib_c s;
  target function void run() { a.STS.write_val(s.x); }
}""", Refused([r"\bx\b", r"private"]), xfail=on(ALL, F6),
        covers=("EX-REF.11",)),

    # --- P5.3: functions -----------------------------------------------------
    # A function's import must agree with its declaration (20.4.1.1).
    "an import disagreeing with its declaration": Case("""
package q {
  function int fetch(int k);
  import target function int fetch(int k, int j);
}
component pss_top {""" + ONE + """
  target function void run() { a.STS.write_val(q::fetch(1, 2)); }
}""", Refused([r"m\.pss:\d+:\d+: error: .*disagree about the number of "
               r"parameters"]), covers=("FN-IMPORT.2",)),

    # Varargs are for built-in definitions only; PSS has no native mechanism
    # to read them (20.2.5).
    "a varargs parameter of a PSS function": Case("""
component pss_top {""" + ONE + """
  target function int f(int k, int ... rest) { return k; }
  target function void run() { a.STS.write_val(f(1, 2, 3)); }
}""", Refused([r"\brest\b|varargs|\.\.\."]), xfail=on(ALL, F11),
        covers=("FN-PARAM.9",)),

    # An import's parameter is at most 64 bits wide, and a list of strings
    # is not importable (20.4.1 c).
    "an import parameter wider than 64 bits": Case("""
package q { import target function void put(bit[65] v); }
component pss_top {""" + ONE + """
  target function void run() { q::put(1); a.STS.write_val(1); }
}""", Refused([r"\bv\b", r"65|64"]), xfail=on(ALL, F26),
        covers=("FN-IMPORT.3",)),

    "an import parameter of a list of strings": Case("""
package q { import target function void put(list<string> v); }
component pss_top {""" + ONE + """
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"\bv\b", r"list"]), xfail=on(ALL, F26),
        covers=("FN-IMPORT.3",)),

    # A struct parameter of an exported function must be `const`
    # (20.4.2.1 g).
    "an export with a non-const struct parameter": Case("""
struct p_s { int x; }
component pss_top {""" + ONE + """
  static function int sf(p_s p) { p.x = 3; return p.x; }
  export target function sf;
  target function void run() {
    p_s q;
    message(LOW, "%d %d", sf(q), q.x);
  }
}""", Refused([r"\bp\b", r"const"]), xfail=on(ALL, F26),
        covers=("FN-EXPORT.2",)),

    # A parameter declared `const` is read-only (20.2.3).
    "assigning a const parameter": Case("""
component pss_top {""" + ONE + """
  function int f(const int x) { x = 2; return x; }
  target function void run() { a.STS.write_val(f(1)); }
}""", Refused([r"m\.pss:\d+:\d+: error: parameter 'x' is const, so its value cannot be "
               r"changed in the function body \(LRM 20\.2\.3\)"]),
        covers=("ST-ASSIGN.6",)),

    # --- P5.3: exec blocks and library placement -----------------------------
    # A component has no `pre_solve`/`post_solve`/`pre_body` (actions,
    # objects, structs) and no `body` (actions) (20.1.2).
    "a pre_solve exec in a component": Case("""
component pss_top {""" + ONE + """
  int n;
  exec pre_solve { n = 1; }
  target function void run() { a.STS.write_val(n); }
}""", Refused([r"`exec pre_solve` is not lowered"]),
        covers=("EXEC-KIND.8",)),

    "a body exec in a component": Case("""
component pss_top {""" + ONE + """
  exec body { message(LOW, "b"); }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"`exec body` is not lowered"]),
        covers=("EXEC-KIND.8", "EXEC-KIND.4")),

    # The memory primitives are target functions (21.13.5): a constructor is
    # solve context.
    "write32 in a constructor": Case("""
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    write32(base, 1);
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"pss_top::initialize: cannot lower call: 'write32' is legal "
               r"only in a target context"]), covers=("LIB-MEM.8",)),

    # --- P5.3: statements ----------------------------------------------------
    "a local declared twice in one scope": Case("""
component pss_top {""" + ONE + """
  target function void run() { int x = 1; int x = 2; a.STS.write_val(x); }
}""", Refused([r"m\.pss:\d+:\d+: error: duplicate variable declaration x"]),
        covers=("ST-BLOCK.5",)),

    # The iterator and index of a `foreach` are read-only, and the iterated
    # collection may not change in its body (20.7.8 g).
    "a foreach writing its iterator": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<int, 2> w = {1, 2};
    foreach (v : w) { v = 1; }
    a.STS.write_val(w[0]);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: foreach iterator variable 'v' is read-only and "
               r"cannot be changed in the foreach body \(LRM 20\.7\.8 g\)"]),
        covers=("ST-FOREACH.6",
                                                  "ST-ASSIGN.6")),

    "a foreach writing its index": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<int, 2> w = {1, 2};
    foreach (w[i]) { i = 2; }
    a.STS.write_val(w[0]);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: foreach index variable 'i' is read-only and "
               r"cannot be changed in the foreach body \(LRM 20\.7\.8 g\)"]),
        covers=("ST-FOREACH.6", "ST-ASSIGN.6")),

    # Replacing the collection whole. An assignment to an ELEMENT is not
    # reported: the LRM's own Example 291 assigns `dst[i]` inside
    # `foreach (dst[i])`, so "the contents" cannot mean an element's value
    # (06-front-end-checks F17; a reading for the user to confirm).
    "a foreach changing its collection": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<int, 2> w = {1, 2};
    array<int, 2> u = {3, 4};
    foreach (w[i]) { w = u; }
    a.STS.write_val(w[0]);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: 'w' is the collection this foreach iterates, so it "
               r"cannot be changed in the foreach body \(LRM 20\.7\.8 g\)"]),
        covers=("ST-FOREACH.7", "ST-ASSIGN.6")),

    # Either an index or an iterator is named (20.7.8 f).
    "a foreach with neither iterator nor index": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<int, 2> w = {1, 2};
    foreach (w) { a.STS.write_val(1); }
  }
}""", Refused([r"line \d+: a 'ProceduralStmtForeach' statement could not "
               r"be translated"]), covers=("ST-FOREACH.5",)),

    # `break` and `continue` only within a loop (20.7.11 b): py wrote a
    # module that does not import (F16).
    "a break outside a loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x = 1;
    if (x == 1) { break; }
    a.STS.write_val(x);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: 'break' may only appear within a "
               r"loop statement: repeat, while or foreach \(LRM 20\.7\.11 b\)"]),
        covers=("ST-BRK.3",)),

    "a continue outside a loop": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x = 1;
    if (x == 1) { continue; }
    a.STS.write_val(x);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: 'continue' may only appear within "
               r"a loop statement: repeat, while or foreach \(LRM 20\.7\.11 b\)"]),
        covers=("ST-BRK.3",)),

    # At most one default branch (20.7.10 f): py ran the second (F18).
    "a match with two defaults": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x = 1;
    int y;
    match (x) { [2]: y = 1; default: y = 2; default: y = 3; }
    a.STS.write_val(y);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: a match statement may have at most "
               r"one default branch; this is a second one \(LRM 20\.7\.10 f\)"]),
        covers=("ST-MATCH.3",)),

    # Only the bare `return;` in an exec (20.7.5; ruling R21) (F21).
    "a return with a value in an exec": Case("""
component pss_top {""" + ONE + """
  int n;
  exec init_down { n = 1; return 1; }
  target function void run() { a.STS.write_val(n); }
}""", Refused([r"m\.pss:\d+:\d+: error: an exec block returns void, so "
               r"'return' cannot take a value \(LRM 20\.7\.5, ruling R21\)"]),
        covers=("ST-RET.3",)),

    # `compile assert` is a package/type-body item, not a procedural
    # statement (19.4).
    "compile assert in a function body": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    compile assert (1 == 1, "x");
    a.STS.write_val(1);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: unexpected 'assert' in function "
               r"'run'"]), covers=("ST-CIF.7",)),

    # A procedural local is neither `rand` nor `static const` (20.7.2).
    "a rand local": Case("""
component pss_top {""" + ONE + """
  target function void run() { rand int x; a.STS.write_val(1); }
}""", Refused([r"m\.pss:\d+:\d+: error: unexpected keyword 'rand'"]),
        covers=("ST-VAR.6",)),

    "a static const local": Case("""
component pss_top {""" + ONE + """
  target function void run() { static const int x = 3; a.STS.write_val(x); }
}""", Refused([r"m\.pss:\d+:\d+: error: unexpected 'static'"]),
        covers=("ST-VAR.6",)),

    # `super;` is an exec block's top-level statement (20.1.1
    # exec_super_stmt): not nested (a likely LRM defect, but the grammar's
    # today), and not in a function.
    "a nested super": Case("""
component base_c { int v; exec init_down { v = 1; } }
component der_c : base_c {
  bool c = true;
  exec init_down { if (c) super; }
}
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { a.STS.write_val(d.v); }
}""", Refused([r"m\.pss:\d+:\d+: error: syntax error at ';'"]),
        covers=("ST-SUPER.6",)),

    "super in a function": Case("""
component pss_top {""" + ONE + """
  target function void run() { super; a.STS.write_val(1); }
}""", Refused([r"m\.pss:\d+:\d+: error: syntax error at ';'"]),
        covers=("ST-SUPER.8",)),

    # `return` takes a value exactly when the function returns one (20.7.5).
    "a void function returning a value": Case("""
component pss_top {""" + ONE + """
  function void f() { return 1; }
  target function void run() { f(); a.STS.write_val(1); }
}""", Refused([r"m\.pss:\d+:\d+: error: .*returns void, so 'return' cannot "
               r"take a value"]), covers=("ST-RET.2",)),

    "an int function returning no value": Case("""
component pss_top {""" + ONE + """
  function int f() { return; }
  target function void run() { a.STS.write_val(f()); }
}""", Refused([r"m\.pss:\d+:\d+: error: .*has a return type, so 'return' "
               r"must supply a value"]), covers=("ST-RET.2",)),

    # --- P5.3: errors masked by an unsupported-feature refusal ---------------
    # Each model below is refused today, but as something pssc does not
    # support yet, not as the error it is: the case expects the LRM's reason,
    # so it flips when the front end diagnoses it.

    # Generic parameters (`type`, `struct`, `numeric`) are for built-in
    # functions only (20.2.5 a).
    "a generic parameter of a PSS function": Case("""
struct a_s { int x; }
component pss_top {""" + ONE + """
  function int f(struct x) { return 1; }
  target function void run() { a_s v; a.STS.write_val(f(v)); }
}""", Refused([r"\bx\b", r"generic|built-in"]), xfail=on(ALL, F11_GENERIC),
        covers=("FN-PARAM.8",)),

    # A target-template function may not be called from an init exec, or
    # any solve exec (20.1.2, 20.9 Table 29).
    "a target-template function called from init_down": Case("""
component pss_top {""" + ONE + """
  function void ts(int x);
  target C function void ts(int x) = \"\"\" bar({{x}}); \"\"\";
  exec init_down { ts(1); }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"\bts\b", r"init_down|solve"]), xfail=on(ALL, F22),
        covers=("FN-CALL.8", "FN-TT.5")),

    # A chandle is not randomizable (7.1).
    "a randomize of a chandle": Case("""
component pss_top { ga_c a;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    chandle c;
    randomize c;
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"chandle"]), xfail=on(ALL, F5_RAND),
        covers=("ST-RAND.11",)),

    # A target-template exec block is not for solve exec kinds (20.5).
    "a target-template init_down block": Case("""
component pss_top {""" + ONE + """
  exec init_down C = \"\"\"int y;\"\"\";
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"init_down"]), xfail=on(ALL, F22),
        covers=("EXEC-TT.7",)),

    # The file functions are solve-only (21.2): any call from target code is
    # an error.
    "a file function called from target code": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    if (file_exists("x")) { a.STS.write_val(1); }
  }
}""", Refused([r"file_exists", r"solve|target"]), xfail=on(ALL, F22),
        covers=("LIB-FILE.6",)),

    # A register group shall not implement all three offset functions
    # (21.14.2).
    "a register group implementing all three offset functions": Case("""
pure component g3_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> R;
  function bit[64] get_offset_of_instance(string name) { return 0x0; }
  function bit[64] get_offset_of_instance_array(string name, int index) {
    return 0x0;
  }
  function bit[64] get_offset_of_path(list<node_s> path) { return 0x0; }
}
component pss_top {
  g3_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() { g.R.write_val(1); }
}""", Refused([r"'g3_c' implements get_offset_of_path, get_offset_of_instance "
               r"and get_offset_of_instance_array"]), covers=("LIB-REG.7",)),
}

test_construct = define_tests(CASES)
