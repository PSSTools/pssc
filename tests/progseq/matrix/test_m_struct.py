"""Structs as values: defaults, copies, nesting (LRM 8.8).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import ALL, Case, define_tests, on, ONE, Refused


#: A derived struct used where its base is expected (8.3, 20.3.2): C, C++
#: and SV declare a derived struct flattened, with no relation to its base,
#: and Python copies a derived struct's members into a base one.
W31 = "W31: a derived struct is not usable as its base (8.3, 20.3.2)"
E26 = ("E26: a static const struct is an internal error on c/cpp, and not "
       "translated at package scope")

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
        covers=("DT-STRUCT", "DT-ENUM", "EX-REF.1", "ST-VAR.5")),

    "struct defaults in array elements": Case("""
struct p_s { bit[8] x; bit[8] y = 7; }
component sub_c {
  array<p_s, 2> ps;
  function bit[8] g() { return ps[1].y; }
}
component pss_top {
  ga_c a;
  sub_c s;
  array<p_s, 3> qs;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    qs[0].x = 4;
  }
  target function void run() {
    a.STS.write_val(qs[0].x + qs[2].y + s.g());
  }
}""", ["write 32 0x1004 0x12"], covers=("DT-STRUCT", "DT-ARR", "DT-ARR.14")),

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
}""", ["write 32 0x1004 0x12"], covers=("DT-ENUM", "ST-VAR", "DT-STRUCT",
                                        "ST-VAR.1")),

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
        covers=("DT-STRUCT", "FN-PARAM", "FN-PARAM.2", "FN-NATIVE.4",
                "ST-ASSIGN.3")),

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
        covers=("DT-STRUCT", "EX-LIT", "FN-PARAM", "FN-PARAM.5",
                "FN-PARAM.11")),

    # --- P3: a struct is a value (8.3) -----------------------------------------
    # Assignment copies every member, nested ones too; changing the source
    # afterwards leaves the copy (DT-STRUCT.4).
    "a struct copy is a value": Case("""
struct pt_s { bit[8] x; bit[8] y; }
struct box_s { pt_s lo; pt_s hi; }
component pss_top {""" + ONE + """
  target function void run() {
    pt_s p;
    box_s bx;
    p.x = 1;
    p.y = 2;
    pt_s q = p;
    p.x = 9;
    message(LOW, "%d %d %d", q.x, q.y, p.x);
    bx.hi = p;
    bx.hi.y = 7;
    message(LOW, "%d %d", p.y, bx.hi.y);
    box_s c = bx;
    c.hi.x = 3;
    message(LOW, "%d %d", bx.hi.x, c.hi.x);
  }
}""", ["1 2 9", "2 7", "9 3"],
        covers=("DT-STRUCT.2", "DT-STRUCT.4", "EX-ASSIGN.8", "ST-ASSIGN.4")),

    # A struct result is returned by value (DT-STRUCT.11), and a call's
    # member is read directly (IP-74).
    "a struct returned by a function": Case("""
struct pt_s { bit[8] x; bit[8] y; }
component pss_top {""" + ONE + """
  target function pt_s mk(bit[8] v) { pt_s p; p.x = v; p.y = v + 1; return p; }
  target function void run() {
    pt_s q = mk(4);
    message(LOW, "%d %d %d", q.x, q.y, mk(7).y);
  }
}""", ["4 5 8"], covers=("DT-STRUCT.11", "FN-CALL", "IP-74", "EX-REF.9",
                         "FN-DECL.4", "FN-NATIVE.6", "ST-RET.6")),

    # Struct parameters are handles (20.3.2): a callee's whole-struct
    # assignments swap the caller's structs (IP-38, Example 291).
    "swapping structs through their handles": Case("""
struct pt_s { bit[8] x; bit[8] y; }
component pss_top {""" + ONE + """
  target function void swap(pt_s a, pt_s b) { pt_s t = a; a = b; b = t; }
  target function void run() {
    pt_s p;
    pt_s q;
    p.x = 1;
    q.x = 2;
    q.y = 3;
    swap(p, q);
    message(LOW, "%d %d %d %d", p.x, p.y, q.x, q.y);
  }
}""", ["2 3 1 0"], covers=("DT-STRUCT.10", "FN-PARAM", "IP-38", "FN-PARAM.3")),

    # Every level of a recursion holds the caller's struct, so its writes
    # accumulate, while each frame's local is its own (IP-10).
    "recursion with a struct parameter": Case("""
struct acc_s { int n; }
component pss_top {""" + ONE + """
  target function void bump(acc_s a, int d) {
    int mine = d;
    a.n = a.n + d;
    if (d > 0) { bump(a, d - 1); }
    message(LOW, "%d %d", mine, a.n);
  }
  target function void run() {
    acc_s s;
    bump(s, 2);
    message(LOW, "%d", s.n);
  }
}""", ["0 3", "1 3", "2 3", "3"],
        covers=("DT-STRUCT.10", "FN-CALL", "ST-VAR", "IP-10")),

    # A derived struct is a base (8.3): assigned to a base variable it gives
    # its base members, and compares with a base literal (IP-40).
    "a derived struct assigned to a base": Case("""
struct b_s { bit[8] a; }
struct d_s : b_s { bit[8] e; }
component pss_top {""" + ONE + """
  target function void run() {
    d_s d;
    d.a = 5;
    d.e = 2;
    b_s b = d;
    b_s c;
    c = d;
    if (b == {.a = 5}) { message(LOW, "eq %d", c.a); }
    else { message(LOW, "ne"); }
  }
}""", ["eq 5"], xfail=on(("c", "cpp", "sv"), W31),
        covers=("DT-STRUCT.3", "DT-STRUCT.5", "EX-EQ.6", "IP-40",
                "ST-ASSIGN.4")),

    # Passed for a base parameter, it is the caller's struct: the callee's
    # assignment copies base members only (20.3.2, IP-39).
    "a derived struct passed for a base parameter": Case("""
struct b_s { bit[8] a; }
struct d_s : b_s { bit[8] e; }
component pss_top {""" + ONE + """
  target function void setb(b_s p) { b_s q; q.a = 5; p = q; }
  target function void run() {
    d_s d;
    d.a = 1;
    d.e = 2;
    setb(d);
    message(LOW, "%d %d", d.a, d.e);
  }
}""", ["5 2"], xfail=on(ALL, W31),
        covers=("DT-STRUCT.10", "FN-PARAM", "IP-39", "FN-PARAM.4")),

    # `==` against a literal compares what the literal leaves out with its
    # default (8.4.2, Example 33; IP-91).
    "struct equality with a partial literal": Case("""
struct pt_s { bit[8] x; bit[8] y; }
component pss_top {""" + ONE + """
  target function void run() {
    pt_s p;
    p.x = 3;
    if (p == {.x = 3}) { message(LOW, "eq"); } else { message(LOW, "ne"); }
    p.y = 1;
    if (p == {.x = 3}) { message(LOW, "eq"); } else { message(LOW, "ne"); }
    if (p != {.y = 1, .x = 3}) { message(LOW, "ne"); }
    else { message(LOW, "eq"); }
  }
}""", ["eq", "ne", "eq"],
        covers=("DT-STRUCT.6", "DT-STRUCT.7", "EX-EQ.4", "EX-EQ.6",
                "IP-91")),

    # A literal assigned to a struct is the whole struct (IP-15): what it
    # leaves out returns to its default, nested members too; and into an
    # array element (IP-14).
    "a struct literal overwrites every member": Case("""
struct in_s { bit[8] x = 7; }
struct out_s { bit[8] a; in_s inn; bit[8] b = 9; }
component pss_top {""" + ONE + """
  target function void run() {
    out_s o;
    array<out_s, 3> arr;
    o.a = 1;
    o.inn.x = 2;
    o.b = 3;
    o = {.a = 4};
    message(LOW, "%d %d %d", o.a, o.inn.x, o.b);
    arr[1].b = 0;
    arr[2] = {.a = 1, .inn = {.x = 2}};
    message(LOW, "%d %d %d %d", arr[2].a, arr[2].inn.x, arr[2].b, arr[1].b);
  }
}""", ["4 7 9", "1 2 9 0"],
        covers=("DT-STRUCT.1", "DT-STRUCT.7", "EX-LIT.14", "EX-LIT.15",
                "IP-14", "IP-15")),

    # `?:` chooses between struct values (8.5.8; IP-31).
    "a conditional of struct values": Case("""
struct pt_s { bit[8] x; bit[8] y; }
component pss_top {""" + ONE + """
  target function void go(bool c) {
    pt_s p;
    pt_s q;
    p.x = 1;
    q.x = 2;
    pt_s r = c ? p : q;
    message(LOW, "%d", r.x);
  }
  target function void run() { go(true); go(false); }
}""", ["1", "2"], covers=("EX-COND.3", "DT-STRUCT", "IP-31")),

    # Struct literals as the arms of `?:` (8.4.3, EX-COND.5): each arm takes
    # the type the conditional is assigned to. No target lowers it yet; the
    # gate refuses it with its line, where it was an internal error (E21).
    "struct literals as the arms of a conditional": Case("""
struct pt_s { bit[8] x; bit[8] y; }
component pss_top {""" + ONE + """
  target function void go(bool c) {
    pt_s r = c ? {.x = 1, .y = 2} : {.x = 3, .y = 4};
    message(LOW, "%d %d", r.x, r.y);
  }
  target function void run() { go(true); go(false); }
}""", ["1 2", "3 4"],
        xfail=on(ALL, "E21: a struct literal as an arm of ?: is refused "
                      "(located) by every target"),
        covers=("EX-COND.5", "DT-STRUCT.7")),

    # --- P5.3: exec blocks, templates, constants, extensions, typedefs -----------
    # A struct's solve exec blocks run when a value of it is solved (7.8,
    # 20.1.2); a local is never solved, so its member keeps its default.
    "a struct's solve exec blocks do not run on a local": Case("""
struct p_s { int a; exec pre_solve { a = 5; } exec post_solve { a = 6; } }
component pss_top {""" + ONE + """
  target function void run() {
    p_s v;
    message(LOW, "%d", v.a);
  }
}""", ["0"], covers=("DT-STRUCT.9",)),

    # `exec body` is not one of a struct's exec kinds (20.1.2). pssc refuses it
    # as unsupported rather than illegal (M5); pssparser does not check it.
    "exec body in a struct": Case("""
struct p_s { int a; exec body { a = 5; } }
component pss_top {""" + ONE + """
  target function void run() {
    p_s v;
    message(LOW, "%d", v.a);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: exec body is not valid in a struct or object: it is valid in actions \(LRM 20\.1\.2\)"]),
        covers=("DT-STRUCT.9",)),

    # Each specialization of a template struct is its own type (7.8.1, 10):
    # `f` is 4 bits wide in `s_s<4>`, so 15 + 1 wraps to 0, and 8 in `s_s<8>`.
    "template struct specializations": Case("""
struct s_s<int N = 2> { bit[N] f; }
component pss_top {""" + ONE + """
  target function void run() {
    s_s<4> v;
    s_s<8> w;
    v.f = 15;
    v.f = v.f + 1;
    w.f = 15;
    w.f = w.f + 1;
    message(LOW, "%d %d", v.f, w.f);
  }
}""", ["0 16"], covers=("DT-STRUCT.12",)),

    # A `static const` struct is read like any struct, and passed to a
    # `const` parameter (7.1, 20.2.3).
    "a static const struct in a component": Case("""
struct p_s { int a; int b; }
component pss_top {""" + ONE + """
  static const p_s MY = {.a = 3, .b = 4};
  function int sum(const p_s v) { return v.a + v.b; }
  target function void run() {
    message(LOW, "%d %d %d", MY.a, MY.b, sum(MY));
  }
}""", ["3 4 7"], xfail=on(("c", "cpp"), E26),
        covers=("DT-STRUCT.13", "DT-CONST", "FN-PARAM.5")),

    "a static const struct in a package": Case("""
struct p_s { int a; int b; }
package pk { static const p_s MY = {.a = 3, .b = 4}; }
component pss_top {""" + ONE + """
  target function void run() {
    message(LOW, "%d %d", pk::MY.a, pk::MY.b);
  }
}""", ["3 4"], xfail=on(ALL, E26), covers=("DT-STRUCT.13", "DT-CONST")),

    # `extend struct` adds a member (17.2.3), defaulted like any other.
    "an extended struct": Case("""
struct p_s { int a; }
extend struct p_s { int x; }
component pss_top {""" + ONE + """
  target function void run() {
    p_s v;
    v.x = 7;
    message(LOW, "%d %d", v.a, v.x);
  }
}""", ["0 7"], covers=("DT-STRUCT.14",)),

    # An extension of a packed struct may not add a member (21.13.1).
    "an extension adding a member to a packed struct": Case("""
struct q_s : packed_s<> { bit[4] a; }
extend struct q_s { bit[4] x; }
component pss_top {""" + ONE + """
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"PSS013|packed"]), covers=("DT-STRUCT.14",)),

    # A struct member is plain data; a `ref` member is illegal (7.10 a).
    "a ref member of a struct": Case("""
component k_c { }
struct p_s { int a; ref k_c r; }
component pss_top {""" + ONE + """
  target function void run() {
    p_s v;
    message(LOW, "%d", v.a);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: 'r' is a reference field, which a struct "
               r"or object cannot declare \(LRM 7\.10 a\)"]), covers=("DT-STRUCT.15",)),

    # Access modifiers on struct members (7.1.1, 17.4): a public member is
    # read and written from outside the struct.
    "access modifiers on struct members": Case("""
struct p_s { public int a; protected int b; int c = 4; }
component pss_top {""" + ONE + """
  target function void run() {
    p_s v;
    v.a = 2;
    message(LOW, "%d %d", v.a, v.c);
  }
}""", ["2 4"], covers=("DT-STRUCT.16",)),

    # A private member is not visible outside its struct (17.4).
    "a private struct member written from outside": Case("""
struct p_s { public int a; private int b; }
component pss_top {""" + ONE + """
  target function void run() {
    p_s v;
    v.b = 2;
    message(LOW, "%d", v.b);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: 'b' is private to 'p_s' and cannot be reached from here \(LRM 17\.4 b\)"]),
        covers=("DT-STRUCT.16",)),

    # A typedef of a struct or an array names the same type (7.1): as a
    # parameter, a result and a local.
    "typedefs of a struct and an array": Case("""
struct p_s { bit[8] x; bit[8] y; }
package tp { typedef p_s pt; typedef array<bit[8], 2> a2; }
component pss_top {""" + ONE + """
  target function tp::pt mk(tp::pt v) {
    tp::pt r;
    r.x = v.y;
    r.y = v.x;
    return r;
  }
  target function void run() {
    p_s p;
    p.x = 1;
    p.y = 2;
    p_s q = mk(p);
    tp::a2 w = {3, 4};
    message(LOW, "%d %d %d", q.x, q.y, w[1]);
  }
}""", ["2 1 4"], covers=("DT-TYPEDEF.2", "DT-TYPEDEF", "DT-STRUCT.11")),
}

test_construct = define_tests(CASES)
