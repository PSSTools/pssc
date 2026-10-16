"""Structs as values: defaults, copies, nesting (LRM 8.8).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE


CASES = {
    # --- A struct's defaults, wherever a value of it is -------------------------
    # A member's initial value belongs to its TYPE: every value of the struct
    # has it -- a local, a nested member, an array element (LRM 8.1). C and C++
    # gave it only to a struct field of a component, and 0 everywhere else.
    "struct defaults in a local and a nested struct": Case("""
enum m_e { M_A = 1, M_B = 2 }
struct q_s { bit[8] z = 5; }
struct p_s { bit[8] x; bit[8] y = 7; m_e m = M_B; q_s q; }
component pss_top {""" + ONE + """
  p_s f;
  target function void run() {
    p_s l;
    a.STS.write_val(l.x + l.y + l.q.z);
    a.STS.write_val(f.q.z + f.m);
  }
}""", ["write 32 0x1004 0xc", "write 32 0x1004 0x7"],
        covers=("DT-STRUCT", "DT-ENUM")),

    "struct defaults in array elements": Case("""
struct p_s { bit[8] x; bit[8] y = 7; }
component sub_c {
  array<p_s, 2> ps;
  function bit[8] g() { return ps[1].y; }
}
component pss_top {""" + ONE + """
  sub_c s;
  array<p_s, 3> qs;
  target function void run() {
    qs[0].x = 4;
    a.STS.write_val(qs[0].x + qs[2].y + s.g());
  }
}""", ["write 32 0x1004 0x12"], covers=("DT-STRUCT", "DT-ARR")),

    # A value with no initializer has its type's default (LRM 7.5): 0 for a
    # number, an enum's FIRST item -- M_A, 3 here, neither 0 nor the smallest.
    # C left a scalar local indeterminate; C, C++ and SV started an enum at 0.
    "default values: an enum's first item, a bare local": Case("""
enum m_e { M_A = 3, M_B = 1 }
struct p_s { m_e m; }
component sub_c { m_e k; function int g() { return k; } }
component pss_top {""" + ONE + """
  m_e f;
  p_s ps;
  array<m_e, 2> ae;
  sub_c s;
  target function void run() {
    m_e l;
    p_s lp;
    bit[32] n;
    a.STS.write_val(f + l + ps.m + lp.m + ae[1] + s.g() + n);
  }
}""", ["write 32 0x1004 0x12"], covers=("DT-ENUM", "ST-VAR", "DT-STRUCT")),

    # --- P1: a struct is passed by handle (20.3.2; user ruling) ---------------
    # The callee's write is the caller's struct. C and C++ copied it, so the
    # write was lost (W2), and a parameter only written did not compile under
    # -Werror (W9).
    "a struct parameter written by the callee": Case("""
struct p_s { bit[8] x; bit[8] y; }
component pss_top {""" + ONE + """
  function void bump(p_s v) { v.x = 7; }
  function bit[8] bump2(p_s v) { v.y = v.y + 1; return v.y; }
  target function void run() {
    p_s q;
    q.x = 1;
    q.y = 2;
    bump(q);
    a.STS.write_val(q.x);
    a.STS.write_val(bump2(q));
    a.STS.write_val(q.y);
  }
}""", ["write 32 0x1004 0x7", "write 32 0x1004 0x3", "write 32 0x1004 0x3"],
        covers=("DT-STRUCT", "FN-PARAM")),

    # A derived struct has its base's members, first, with their defaults
    # (7.8, 17.1). C and C++ declared only the derived members (W4).
    "a derived struct's inherited members": Case("""
struct b_s { bit[8] a = 4; }
struct d_s : b_s { bit[8] b; }
component pss_top {""" + ONE + """
  target function void run() {
    d_s v;
    v.b = 2;
    a.STS.write_val(v.a + v.b);
    v.a = 1;
    a.STS.write_val(v.a);
  }
}""", ["write 32 0x1004 0x6", "write 32 0x1004 0x1"],
        covers=("DT-STRUCT",)),

    # An aggregate literal names members (8.4.2); one it leaves out has its
    # default.
    "a struct literal": Case("""
struct p_s { bit[8] x; bit[8] y = 9; bit[8] z; }
component pss_top {""" + ONE + """
  target function void run() {
    p_s q = {.x = 3, .z = 4};
    a.STS.write_val(q.x * 256 + q.y * 16 + q.z);
  }
}""", ["write 32 0x1004 0x394"],
        covers=("DT-STRUCT", "EX-LIT")),

    # A struct with an array member is copied whole, array included (8.1),
    # and an array member's initializer is part of the type (7.8).
    "a struct with an array member": Case("""
struct h_s { array<bit[8], 2> v; bit[8] n; array<bit[8], 2> d = {3, 4}; }
component pss_top {""" + ONE + """
  target function void run() {
    h_s h;
    h_s g;
    h.v[1] = 5;
    h.n = 1;
    g = h;
    h.v[1] = 6;
    a.STS.write_val(g.v[1] + g.n);
    a.STS.write_val(g.d[0] * 16 + g.d[1]);
  }
}""", ["write 32 0x1004 0x6", "write 32 0x1004 0x34"],
        xfail={"sv": "W24: Verilator 5.052 ignores the default of an array "
                     "member of an unpacked struct (pssc's SV is IEEE 1800 "
                     "conformant; a declaration-site initialisation would "
                     "work around it)"},
        covers=("DT-STRUCT", "DT-ARR")),

    # Structs compare member by member (8.5.3), nested ones included.
    "struct equality": Case("""
struct q_s { bit[8] z; }
struct p_s { bit[8] x; q_s q; }
component pss_top {""" + ONE + """
  target function void run() {
    p_s a1;
    p_s a2;
    a1.x = 1;
    a2.x = 1;
    if (a1 == a2) { a.STS.write_val(1); }
    a2.q.z = 3;
    if (a1 != a2) { a.STS.write_val(2); }
    if (a1 == a2) { a.STS.write_val(9); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x2"],
        covers=("DT-STRUCT", "EX-EQ")),

    # Assigned, nested, and passed to a `const` parameter -- the one kind a
    # literal may be passed to (20.3.2; user ruling).
    "struct literals assigned, nested and passed": Case("""
struct q_s { bit[8] z = 5; }
struct p_s { bit[8] x; q_s q; }
component pss_top {""" + ONE + """
  function bit[8] sum(const p_s v) { return v.x + v.q.z; }
  target function void run() {
    p_s p;
    p.x = 9;
    p = {.q = {.z = 2}};
    a.STS.write_val(p.x * 16 + p.q.z);
    a.STS.write_val(sum({.x = 3}));
  }
}""", ["write 32 0x1004 0x2", "write 32 0x1004 0x8"],
        covers=("DT-STRUCT", "EX-LIT", "FN-PARAM")),
}

test_construct = define_tests(CASES)
