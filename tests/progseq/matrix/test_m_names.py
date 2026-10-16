"""Names: PSS names kept, names another language reserves, names the
generated code uses.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE

W63 = ("W63: sv spells `::G` as `G`, and a local `G` (hoisted to the "
       "top of the task) hides the static const for both")


CASES = {
    # --- Names: a generated API names a PSS type as PSS does -------------------
    # C named a component `<prefix>_t` and a sub-component's accessor
    # `<prefix>_<sub>`, so a sub-component `t` declared the type's name a
    # second time and the header did not compile.
    "a sub-component named t": Case("""
component leaf_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void poke(bit[32] v) { a.STS.write_val(v); }
}
component pss_top {
  leaf_c t;
  leaf_c u;
  solve function void initialize(addr_handle_t base) {
    t.initialize(base);
    u.initialize(make_handle_from_handle(base, 0x100));
  }
  target function void run() { t.poke(1); u.poke(2); }
}""", ["write 32 0x1004 0x1", "write 32 0x1104 0x2"], covers=("FN-CALL",)),

    # C and C++ rewrote `_c`, `_s` and `_e` to `_t`, which made these three
    # PSS types one C type.
    "a component, a struct and an enum with one stem": Case("""
enum x_e { XA = 1, XB = 2 }
struct x_s { bit[32] v; }
component x_c {
  target function bit[32] f(x_s p, x_e e) {
    if (e == XB) { return p.v; }
    return 0;
  }
}
component pss_top {""" + ONE + """
  x_c x;
  target function void run() {
    x_s v;
    v.v = 5;
    a.STS.write_val(x.f(v, XB));
  }
}""", ["write 32 0x1004 0x5"], covers=("DT-STRUCT", "DT-ENUM", "FN-PARAM")),

    # C named the handle parameter `s` (`self` in `_init`) and its bus pointer
    # `bus`, so a model's own `s` was declared twice: `x_f(x_c *s, x_s s)`.
    # The generated names are now `_self`/`_bus`, and a model's `_self` is
    # renamed like a C keyword.
    "names the generated code also uses": Case("""
component leaf_c {
  ga_c a;
  bit[32] bus;
  bit[32] _bus;
  solve function void initialize(addr_handle_t self, bit[32] _self) {
    a.set_handle(self);
    bus = _self;
    _bus = _self + 1;
  }
  target function bit[32] f(bit[32] s, bit[32] _self) {
    bit[32] self = s + _self;
    bit[32] _bus = self + bus;
    return _bus + this._bus;
  }
  target function void poke(bit[32] s) { a.STS.write_val(f(s, 1)); }
}
component pss_top {
  leaf_c s;
  solve function void initialize(addr_handle_t base) {
    s.initialize(base, 0x10);
  }
  target function void run() { s.poke(2); }
}""", ["write 32 0x1004 0x24"], covers=("FN-PARAM", "ST-VAR", "EX-REF",
                                        "EX-REF.2")),

    # ...and the ROOT's constructor parameters, which every target forwards
    # from a factory that takes the platform (`imp`, `imports`, `_bus`).
    "root constructor parameters named like generated ones": Case("""
component pss_top {
  ga_c a;
  bit[32] k;
  solve function void initialize(addr_handle_t imp, bit[32] self,
                                 bit[32] imports, bit[32] bus) {
    a.set_handle(imp);
    k = self + imports + bus;
  }
  target function void run() { a.STS.write_val(k); }
}""", ["write 32 0x1004 0x7"], args=(0x1000, 1, 2, 4),
        covers=("FN-SOLVE", "FN-PARAM")),

    # --- Names another language reserves -----------------------------------------
    # Each is free in PSS and reserved in SystemVerilog (IEEE 1800 Annex B);
    # `new` is reserved in C++ too. SV renamed from two partial
    # lists, and a field `new` was in neither.
    "fields, a local and a parameter named as SV keywords": Case("""
component pss_top {""" + ONE + """
  int new = 1;
  int inside = 2;
  int xor = 3;
  int unique0 = 4;
  target function int add(int untyped) { int wor = untyped + new; return wor; }
  target function void run() { a.STS.write_val(add(inside + xor + unique0)); }
}""", ["write 32 0x1004 0xa"], covers=("ST-VAR", "FN-PARAM")),

    # --- solve functions other than the constructor (LRM 22.2.3) ----------------
    # The same for C and C++: free in PSS, reserved there (`_self` is the
    # generated handle). C and C++ renamed fields and parameters, not locals.
    "fields, locals and a parameter named as C keywords": Case("""
component pss_top {""" + ONE + """
  int auto = 1;
  int register = 2;
  target function int add(int short) { int char = short; return char; }
  target function void run() {
    int long = 4;
    int _self = 5;
    a.STS.write_val(auto + register + add(3) + long + _self);
  }
}""", ["write 32 0x1004 0xf"], covers=("ST-VAR", "FN-PARAM")),

    # --- Array initializers ------------------------------------------------------
    # An enum item is the one the linker found. By name alone, `C` and `SV`
    # here were the core library's `target_language_e::C` and `::SV` (0, 2).
    "enum items named as the core library's": Case("""
enum lang_e { SV, CPP = 4, C }
component pss_top {""" + ONE + """
  lang_e f = C;
  target function void run() {
    lang_e x = SV;
    a.STS.write_val((bit[32])f * 16 + (bit[32])x);
    if (x == SV && f == C) { a.STS.write_val(1); }
  }
}""", ["write 32 0x1004 0x50", "write 32 0x1004 0x1"],
        covers=("DT-ENUM", "DT-CAST", "EX-EQ.9")),

    # An item is scoped by its enum (7.5): two enums may each have an `A`.
    # Python's module-level constants made them one name, the second
    # silently replacing the first; C, C++ and SV promote items to the
    # enclosing scope, where two `A`s do not compile.
    "two enums with an item of one name": Case("""
enum dir_e { A, B = 3 }
enum op_e { B, A = 7 }
component pss_top {""" + ONE + """
  target function void run() {
    dir_e d = A;
    op_e o = A;
    a.STS.write_val((bit[32])d * 16 + (bit[32])o);
    if (d == dir_e::A && o == op_e::A) { a.STS.write_val(1); }
    message(LOW, "%n %n", d, o);
  }
}""", ["write 32 0x1004 0x7", "write 32 0x1004 0x1", "A A"],
        covers=("DT-ENUM", "LIB-MSG")),

    # --- P5.3: static reference paths (B.18 static_ref_path) -----------------
    # A package's constant and enum item, a component type's constant, and
    # the root scope's `::G`, which names the global even where a local `G`
    # hides it.
    "static reference paths": Case("""
package k_pkg {
  static const int C = 3;
  enum e_e { X, Y = 5 }
}
component sub_c { static const bit[8] K = 0x41; }
static const int G = 7;
component pss_top {""" + ONE + """
  target function void run() {
    message(LOW, "%d %x %d %d", k_pkg::C + 1, sub_c::K, ::G,
            (int)k_pkg::e_e::Y);
    int G = 1;
    message(LOW, "%d %d", G, ::G);
  }
}""", ["4 41 7 5", "1 7"], xfail=on(("sv",), W63),
        covers=("EX-REF.6",)),
}

test_construct = define_tests(CASES)
