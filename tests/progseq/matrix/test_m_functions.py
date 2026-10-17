"""Functions: declaration, parameters, calls and results (LRM 20.3, 22).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import ALL, Case, define_tests, on, ONE, Refused

E26 = "E26: a static const struct: 'unsupported field initializer ExprStructLiteral'"
E33 = ("E33: a call on a base instance of a function only a derived type "
       "defines is not refused (c, py: unlocated ValueError)")


CASES = {
    # --- D8: a value-returning operation inside an expression ------------------
    "a value-returning operation in a condition": Case("""
component pss_top {""" + ONE + """
  target function bool busy() { return a.STS.read_val() != 0; }
  target function void run() { if (busy()) { a.STS.write_val(0); } }
}""", ["read 32 0x1004 0x3", "write 32 0x1004 0x0"], mem={0x1004: 3},
        covers=("FN-CALL", "ST-IF", "DT-BOOL", "FN-SOLVE.2")),

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
        covers=("FN-PARAM", "FN-PARAM.6")),

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
}""", ["3628800 55"], covers=("FN-CALL", "FN-PARAM", "ST-VAR", "IP-11",
                              "FN-CALL.2", "FN-CALL.4", "FN-NATIVE.1",
                              "ST-VAR.2")),

    # A parameter named like a field is the parameter in the body (20.3.2,
    # IP-23): assigning it leaves the field.
    "a parameter named like a field": Case("""
component pss_top {""" + ONE + """
  int v = 7;
  target function void set(int v) { v = v + 1; message(LOW, "p %d", v); }
  target function void run() { set(1); message(LOW, "f %d", v); }
}""", ["p 2", "f 7"], covers=("FN-PARAM", "EX-REF", "IP-23", "EX-REF.7")),

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
}""", ["42 2"], covers=("FN-PARAM", "FN-CALL", "DT-CAST.9", "ST-VCALL",
                        "FN-PARAM.5", "FN-CALL.1", "ST-VCALL.2",
                        "ST-VCALL.3")),

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
}""", ["60 6"], covers=("FN-VIRT", "ST-REPEAT", "IP-62", "FN-VIRT.1",
                        "FN-VIRT.3", "FN-VIRT.4")),

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
        covers=("FN-DECL", "FN-PURE", "FN-CALL", "FN-DECL.1", "FN-PURE.1",
                "FN-CALL.2", "FN-CALL.3")),

    # --- P4: interaction patterns ---------------------------------------------------
    # A `const` struct parameter forwarded to a `const` formal (20.2.3,
    # IP-65); its legal half.
    "a const struct parameter forwarded to a const formal": Case("""
struct p_s { int x; }
component pss_top {""" + ONE + """
  target function int rd(const p_s p) { return p.x; }
  target function int fwd(const p_s p) { return rd(p) + 1; }
  target function void run() { p_s q; q.x = 4; message(LOW, "%d %d", fwd(q), q.x); }
}""", ["5 4"], covers=("FN-PARAM", "DT-CONST", "DT-STRUCT", "IP-65",
                       "FN-PARAM.5")),

    # ... and its error: a `const` parameter passed where the callee may
    # write it (20.2.3; was W40).
    "a const struct parameter forwarded to a non-const formal": Case("""
struct p_s { int x; }
component pss_top {""" + ONE + """
  target function void wr(p_s p) { p.x = 9; }
  target function int fwd(const p_s p) { wr(p); return p.x; }
  target function void run() { p_s q; q.x = 4; message(LOW, "%d", fwd(q)); }
}""", Refused([r"m\.pss:\d+:\d+: error: argument 1 of 'wr' is const parameter 'p', so "
               r"parameter 'p' must be declared const \(LRM 20\.2\.3\)"]),
        covers=("FN-PARAM", "DT-CONST")),

    # A static function called by its base type's name (20.2, IP-64): the
    # call is not polymorphic. ast2ir refuses a call by type (I11).
    "a static function called through its base type": Case("""
component base_c {
  static function int f(int x) { return x + 1; }
  target function int g() { return f(1); }
}
component der_c : base_c {
  static function int f(int x, int y) { return x + y; }
}
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { message(LOW, "%d %d", base_c::f(5), d.g()); }
}""", Refused([r"line \d+: this expression is a 'ExprRefPathStaticRooted', "
               r"which has no translation"]),
        covers=("FN-VIRT", "FN-CALL", "IP-64", "FN-DECL.2", "FN-CALL.3",
                "FN-VIRT.5")),

    # --- P5.3: declarations, definitions and calls -------------------------------
    # A scalar is passed by value (20.3.2): the callee's write to its
    # parameter is not the caller's.
    "a scalar parameter is a copy": Case("""
component pss_top {""" + ONE + """
  target function int f(int x) { x = x + 10; return x; }
  target function void run() { int y = 1; int r = f(y); message(LOW, "%d %d", y, r); }
}""", ["1 11"], covers=("FN-PARAM", "FN-PARAM.1", "ST-ASSIGN.7")),

    # A function declared in its component and defined in an extension of it,
    # and one declared in a base component and defined only in the derived
    # one, called on a derived instance (20.2, 20.2.7).
    "functions defined apart from their declaration": Case("""
component base_c { function int g(int x); }
component der_c : base_c { function int g(int x) { return x * 2; } }
component pss_top {""" + ONE + """
  der_c d;
  function int f(int x);
  target function void run() { message(LOW, "%d %d", f(1), d.g(4)); }
}
extend component pss_top { function int f(int x) { return x + 5; } }""",
        ["6 8"], covers=("FN-DECL", "FN-DECL.6", "FN-DECL.7")),

    # A definition may add a platform restriction the declaration did not
    # make (20.2.1.3, Ex 297): `f3` is then callable from solve context...
    "an unqualified declaration with a solve definition": Case("""
component pss_top {
  ga_c a;
  int k;
  function int f3(int x);
  solve function int f3(int x) { return x + 1; }
  solve function void initialize(addr_handle_t base) { a.set_handle(base); k = f3(5); }
  target function void run() { a.STS.write_val(k); }
}""", ["write 32 0x1004 0x6"], covers=("FN-DECL.5", "FN-SOLVE.3")),

    # ...and not from target context (Ex 297's `c = func3(5)`).
    "a solve-defined function called from target context": Case("""
component pss_top {""" + ONE + """
  function int f3(int x);
  solve function int f3(int x) { return x + 1; }
  target function void run() { int c = f3(5); a.STS.write_val(c); }
}""", Refused([r"m\.pss:\d+:\d+: error: 'f3' is a solve function and cannot be called from target function 'run'"]),
        covers=("FN-DECL.5", "FN-SOLVE.3")),

    # A `target` declaration with a `solve` definition: the function has no
    # definition on the target platform, the only one it may be called on,
    # so a call is invalid (20.2.1.3, 20.2.7)...
    "a call to a target declaration with a solve definition": Case("""
component pss_top {""" + ONE + """
  target function int f(int x);
  solve function int f(int x) { return x; }
  target function void run() { message(LOW, "%d", f(1)); }
}""", Refused([r"m\.pss:\d+:\d+: error: declarations of 'f' disagree about the platform "
               r"qualifier: one is target, the other solve \(LRM 20\.3\.1\)"]),
        covers=("FN-DECL.5", "FN-CALL.7")),

    # ...and the definition contradicts the declaration, which fixes the
    # function's availability: a definition only adds a restriction to an
    # unqualified declaration (20.2, 20.2.1.3).
    "a target declaration with a solve definition": Case("""
component pss_top {""" + ONE + """
  target function int f(int x);
  solve function int f(int x) { return x; }
  target function void run() { message(LOW, "a"); }
}""", Refused([r"m\.pss:\d+:\d+: error: declarations of 'f' disagree about the platform "
               r"qualifier: one is target, the other solve \(LRM 20\.3\.1\)"]), covers=("FN-DECL.5",)),

    # Calls inside aggregate literals (Ex 35), and mutual recursion (20.2.7).
    "calls in aggregate literals and mutual recursion": Case("""
struct p_s { int a; int b; }
component pss_top {""" + ONE + """
  target function int g(int i) { return i * 10; }
  target function bool is_even(bit[8] n) { if (n == 0) { return true; } return is_odd(n - 1); }
  target function bool is_odd(bit[8] n) { if (n == 0) { return false; } return is_even(n - 1); }
  target function void run() {
    int i = 2;
    p_s p = {.a = i, .b = g(i)};
    array<int, 2> v = {i, g(i + 1)};
    message(LOW, "%d %d %d %d", p.a, p.b, v[0], v[1]);
    message(LOW, "%d %d", is_even(10), is_odd(10));
  }
}""", ["2 20 2 30", "1 0"], covers=("FN-CALL", "FN-CALL.4", "FN-CALL.9")),

    # The definition of a `pure` declaration may omit `pure` (20.2.6 b).
    "a pure declaration defined without pure": Case("""
component pss_top {""" + ONE + """
  pure function int f(int x);
  function int f(int x) { return x * 3; }
  target function void run() { message(LOW, "%d", f(2)); }
}""", ["6"], covers=("FN-PURE", "FN-PURE.2")),

    # An exported static function reads a static const and calls a memory
    # primitive (20.4.2, 20.4.2.2); `bump` is a root instance function
    # exported by pssc's extension (PSS120). Both are also called from PSS.
    "exported functions": Case("""
component pss_top {
  ga_c a;
  static const bit[32] MAGIC = 0x5A;
  addr_handle_t hb;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); hb = base; }
  static function void poke(addr_handle_t h, bit[32] v) {
    write32(make_handle_from_handle(h, 8), v + MAGIC);
  }
  export target function poke;
  target function int bump(int x) { return x + 1; }
  export target function bump;
  target function void run() { poke(hb, 1); a.STS.write_val(bump(2)); }
}""", ["write 32 0x1008 0x5b", "write 32 0x1004 0x3"],
        covers=("FN-EXPORT", "FN-EXPORT.1", "FN-EXPORT.3", "FN-EXPORT.4")),

    # A call is valid only if the function has a definition (20.2.7).
    "a call to a function declared and never defined": Case("""
component pss_top {""" + ONE + """
  function int f(int x);
  target function void run() { message(LOW, "%d", f(1)); }
}""", Refused([r"no function named 'f'"]), covers=("FN-CALL.7",)),

    # A redeclaration repeats the defaults (20.2.4 c); pssparser checks it.
    "a definition whose default disagrees with its declaration": Case("""
component pss_top {""" + ONE + """
  function int f(int x, int y = 3);
  function int f(int x, int y = 4) { return x + y; }
  target function void run() { message(LOW, "%d", f(1)); }
}""", Refused([r"disagree about the default value"]), covers=("FN-DECL.8",)),

    # A static call is not polymorphic (20.2): `g`, in the base, calls the
    # base's `f` even on a derived instance that shadows it.
    "a shadowed static function called from an inherited body": Case("""
component base_c {
  static function int f(int x) { return x + 1; }
  target function int g() { return f(1); }
}
component der_c : base_c { static function int f(int x) { return x + 100; } }
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { message(LOW, "%d", d.g()); }
}""", ["2"], covers=("FN-VIRT", "FN-VIRT.5")),

    # ...and an instance function declared in a base and defined only in a
    # derived type may not be called on a base instance (20.2.7).
    "a call on a base instance of a function only its derived type defines":
    Case("""
component base_c { function int f(int x); }
component der_c : base_c { function int f(int x) { return x + 5; } }
component pss_top {""" + ONE + """
  der_c d;
  base_c b;
  target function void run() { a.STS.write_val(d.f(1)); a.STS.write_val(b.f(1)); }
}""", Refused([r"\bf\b"]), xfail=on(ALL, E33),
        covers=("FN-DECL.7", "FN-CALL.7")),

    # `const` is part of the signature: it appears in a redeclaration (20.2.3 c).
    "a definition that drops its declaration's const": Case("""
component pss_top {""" + ONE + """
  function int f(const int x);
  function int f(int x) { return x; }
  target function void run() { message(LOW, "%d", f(1)); }
}""", Refused([r"m\.pss:\d+:\d+: error: declarations of 'f' disagree about whether parameter "
               r"1 \('x'\) is const \(LRM 20\.2\.3 c\)"]), covers=("FN-DECL.8",)),

    # ...and in an override, whose defaults are the base's too (20.2.3 c,
    # 20.2.4 c).
    "an override that changes const and a default": Case("""
component base_c { target function int f(const int x, int y = 2) { return x + y; } }
component der_c : base_c { target function int f(int x, int y = 5) { return x * y; } }
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { message(LOW, "%d", d.f(3)); }
}""", Refused([r"m\.pss:\d+:\d+: error: 'der_c::f' and the 'base_c::f' it overrides disagree "
               r"about whether parameter 1 \('x'\) is const"]), covers=("FN-VIRT.7",)),

    # A non-pure function shall not be declared pure in a derived type
    # (20.2.6).
    "a non-pure function overridden as pure": Case("""
component base_c { function int f(int x) { return x; } }
component der_c : base_c { pure function int f(int x) { return x + 1; } }
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { message(LOW, "%d", d.f(2)); }
}""", Refused([r"m\.pss:\d+:\d+: error: 'der_c::f' is pure but overrides the non-pure "
               r"'base_c::f' \(LRM 20\.2\.6\)"]), covers=("FN-PURE.2", "FN-VIRT.7")),

    # An instance override keeps the base's signature (20.2)...
    "an override with another signature": Case("""
component base_c { target function int f(int x) { return x; } }
component der_c : base_c { target function int f(int x, int y) { return x + y; } }
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { message(LOW, "%d", d.f(1, 2)); }
}""", Refused([r"m\.pss:\d+:\d+: error: 'der_c::f' and the 'base_c::f' it overrides disagree "
               r"about the number of parameters \(1 and 2\)"]),
        covers=("FN-VIRT.2",)),

    # ...and an instance function cannot be shadowed by a static one (20.2).
    "an instance function shadowed by a static one": Case("""
component base_c { target function int f(int x) { return x; } }
component der_c : base_c { static function int f(int x) { return x + 1; } }
component pss_top {""" + ONE + """
  der_c d;
  target function void run() { message(LOW, "%d", d.f(1)); }
}""", Refused([r"m\.pss:\d+:\d+: error: static function 'der_c::f' cannot shadow the instance "
               r"function 'base_c::f' \(LRM 20\.2\)"]), covers=("FN-VIRT.2",)),

    # A component type is returned only with `ref` (20.2.2).
    "a function returning a component without ref": Case("""
component sub_c { }
component pss_top {""" + ONE + """
  sub_c s;
  function sub_c g() { return s; }
  target function void run() { message(LOW, "x"); }
}""", Refused([r"m\.pss:\d+:\d+: error: function 'g' returns component type 'sub_c' "
               r"without 'ref': a function shall not \(LRM 20\.2\.2\)"]),
        covers=("FN-DECL.4",)),

    # A `const` parameter cannot be changed in the body (20.2.3).
    "an assignment to a const scalar parameter": Case("""
component pss_top {""" + ONE + """
  target function int f(const int x) { x = 3; return x; }
  target function void run() { message(LOW, "%d", f(1)); }
}""", Refused([r"m\.pss:\d+:\d+: error: parameter 'x' is const, so its value cannot be "
               r"changed in the function body \(LRM 20\.2\.3\)"]),
        covers=("FN-PARAM.5",)),

    # A static const struct passed to a const formal (20.2.3).
    "a static const struct passed to a const formal": Case("""
struct p_s { int x; int y; }
component pss_top {""" + ONE + """
  static const p_s P = {.x = 3, .y = 4};
  function int f(const p_s v) { return v.x + v.y; }
  target function void run() { message(LOW, "%d", f(P)); }
}""", ["7"], xfail=on(("c", "cpp"), E26), covers=("DT-CONST", "DT-CONST.5")),
}

test_construct = define_tests(CASES)
