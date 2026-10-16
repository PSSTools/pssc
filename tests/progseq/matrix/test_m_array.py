"""Arrays of data: fields, locals, copies, initializers, parameters, foreach,
equality (LRM 8.1, 8.5.3, 20.7.8).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, Refused


CASES = {
    # --- Arrays of data in a component -------------------------------------------
    # SV declared no member for an array field, so a body naming one did not
    # compile. Elements start at 0 (LRM 8.1); 5 + 6 + (0 + 0 + 6 + 0).
    "array fields of a component": Case("""
component pss_top {""" + ONE + """
  array<bit[32], 4> keys;
  bit[16] raw[3];
  target function void run() {
    bit[32] t = 0;
    keys[1] = 5;
    raw[2] = 6;
    foreach (raw[i]) { t += raw[i]; }
    a.STS.write_val(keys[1] + raw[2] + t);
  }
}""", ["write 32 0x1004 0x11"], covers=("DT-ARR", "EX-IDX", "ST-FOREACH")),

    # --- Arrays of data in a function ------------------------------------------
    # A local array starts with each element at its type's default (LRM 8.1),
    # a struct element with its own defaults; it is indexed, iterated, and
    # handed by value to an assignment. C, C++ and SV failed with an internal
    # error ("unsupported C type for DataTypeArray").
    # w: {0, 9}, v = w; v[0] = 2: 9 + 2 + 7 + (0 + 9) = 0x1b.
    "local arrays": Case("""
struct p_s { bit[8] x; bit[8] y = 7; }
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 2> w;
    array<bit[8], 2> v;
    array<p_s, 2> ps;
    bit[16] raw[3];
    bit[32] t = 0;
    w[1] = 9;
    v = w;
    v[0] = 2;
    raw[2] = w[0];
    foreach (w[i]) { t += w[i]; }
    a.STS.write_val(v[1] + v[0] + ps[1].y + t + raw[2]);
  }
}""", ["write 32 0x1004 0x1b"], covers=("DT-ARR", "ST-FOREACH", "EX-ASSIGN")),

    # An enum element starts at its first item, a 2-D array is copied whole,
    # and an array of structs copied into a field is a copy: writing the
    # local afterwards leaves the field alone. ae: 3; m2[1][0] = 4 copied to
    # n2; keep[1].x = 5, then l[1].x = 9: 3 + 4 + 5 + 7 = 0x13.
    "local arrays: enum elements, two dimensions, copied structs": Case("""
enum m_e { M_A = 3, M_B = 1 }
struct p_s { bit[8] x; bit[8] y = 7; }
component pss_top {""" + ONE + """
  array<p_s, 2> keep;
  target function void run() {
    array<m_e, 2> ae;
    array<array<bit[8], 2>, 2> m2;
    array<array<bit[8], 2>, 2> n2;
    array<p_s, 2> l;
    m2[1][0] = 4;
    n2 = m2;
    m2[1][0] = 8;
    l[1].x = 5;
    keep = l;
    l[1].x = 9;
    a.STS.write_val(ae[1] + n2[1][0] + keep[1].x + keep[0].y);
  }
}""", ["write 32 0x1004 0x13"], covers=("DT-ARR", "EX-ASSIGN")),

    # PSS passes an aggregate by handle (LRM 20.3.2): the callee's write is
    # the caller's array. 3 + 4 = 7, then w[0] is 4: 0x7, 0x4. Python's list
    # is a handle already; C and SV failed with an internal error, and C++'s
    # `std::array` would have been a copy.
    "an array parameter": Case("""
component pss_top {""" + ONE + """
  function bit[8] sum(array<bit[8], 2> v) { v[0] = 4; return v[0] + v[1]; }
  target function void run() {
    array<bit[8], 2> w;
    w[1] = 3;
    a.STS.write_val(sum(w));
    a.STS.write_val(w[0]);
  }
}""", ["write 32 0x1004 0x7", "write 32 0x1004 0x4"],
        refused=on(("c", "cpp", "sv"),
                    Refused([r"pss_top::sum: an array parameter 'v'"])),
        covers=("DT-ARR", "FN-PARAM")),

    # --- Array initializers ------------------------------------------------------
    # `{...}` as a field's initializer, a local's and an assigned value, nested
    # for an array of arrays. Each element is assigned to its element type
    # (8.7.2: 300 is 0x2c in a `bit[8]`), and every element is read before
    # any is written, so `w = {w[1], w[0]}` swaps. No target rendered one; the
    # case was refused before.
    "array initializers": Case("""
enum m_e { A, B, C }
component pss_top {""" + ONE + """
  array<bit[32], 3> v = {1, 2, 3};
  array<array<bit[8], 2>, 2> g = {{1, 2}, {3, 4}};
  array<m_e, 2> e = {C, A};
  target function void run() {
    array<bit[8], 2> w = {v[2], 300};
    a.STS.write_val(v[0] + w[1]);
    w = {w[1], w[0]};
    a.STS.write_val(w[0] * 16 + w[1]);
    g = {{w[1], 7}, {g[0][0], g[1][1]}};
    a.STS.write_val(g[0][0] * 1000 + g[0][1] * 100 + g[1][0] * 10 + g[1][1]);
    e = {e[0], B};
    if (e[0] == C && e[1] == B) { a.STS.write_val(1); }
  }
}""", ["write 32 0x1004 0x2d", "write 32 0x1004 0x2c3",
       "write 32 0x1004 0xe82", "write 32 0x1004 0x1"],
        covers=("DT-ARR", "EX-LIT")),

    # An initializer has the array's element count (8.1), at every level.
    "an array initializer of the wrong size": Case("""
component pss_top {""" + ONE + """
  array<bit[32], 3> v = {1, 2};
  target function void run() {
    array<array<bit[8], 2>, 2> w;
    w = {{1, 2}, {3, 4, 5}};
    a.STS.write_val(v[0] + w[1][0]);
  }
}""", Refused([r"pss_top\.v: an array initializer with 2 element\(s\), "
               r"for an array of 3",
               r"pss_top::run: an array initializer with 3 element\(s\), "
               r"for an array of 2"]), covers=("DT-ARR", "EX-LIT")),

    # --- P1: foreach over data (20.7.8) ----------------------------------------
    # `foreach (v : w[i])` binds v to each ELEMENT and i to its index. C and
    # C++ left i undeclared; SV made v the index (W5).
    "foreach with a value iterator, with and without an index": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 3> w = {5, 6, 7};
    foreach (v : w[i]) { bit[8] t = v * 16; a.STS.write_val(t + i); }
    foreach (e : w) { a.STS.write_val(e); }
  }
}""", ["write 32 0x1004 0x50", "write 32 0x1004 0x61", "write 32 0x1004 0x72",
       "write 32 0x1004 0x5", "write 32 0x1004 0x6", "write 32 0x1004 0x7"],
        covers=("ST-FOREACH", "DT-ARR")),

    # Arrays compare element by element (8.5.3). C compared addresses (W7).
    "array equality": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 2> x = {1, 2};
    array<bit[8], 2> y = {1, 2};
    if (x == y) { a.STS.write_val(1); }
    y[1] = 3;
    if (x != y) { a.STS.write_val(2); }
    if (x == y) { a.STS.write_val(9); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x2"],
        covers=("DT-ARR", "EX-EQ")),

    # `in` over an array (8.5.9): membership, element by element.
    "in over an array": Case("""
component pss_top {""" + ONE + """
  array<bit[8], 3> w = {5, 6, 7};
  target function void go(bit[8] v) {
    if (v in w) { a.STS.write_val(1); } else { a.STS.write_val(0); }
  }
  target function void run() { go(6); go(8); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0"],
        covers=("EX-IN", "DT-ARR")),

    # --- P3: arrays as values ----------------------------------------------------
    # An array assignment copies (7.9.2.1, IP-41), a row of a two-dimensional
    # array is itself an array value (DT-ARR.2, IP-42), and both copies keep
    # their values when the source changes afterwards.
    "array copies and rows of a two-dimensional array": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 3> a = {1, 2, 3};
    array<bit[8], 3> b = a;
    array<array<bit[8], 2>, 2> m = {{1, 2}, {3, 4}};
    array<array<bit[8], 2>, 2> n;
    a[0] = 9;
    n[1] = m[0];
    m[0][1] = 7;
    message(LOW, "%d %d %d %d %d %d", b[0], a[0], n[1][0], n[1][1], m[0][1],
            n[0][0]);
  }
}""", ["1 9 1 2 7 0"],
        covers=("DT-ARR.2", "DT-ARR.5", "EX-ASSIGN.8", "IP-41", "IP-42")),

    # `size()` is the element count; `sum()` of an integer array is an `int`
    # (7.9.2.2, Table 8), so 200 + 100 of bit[8] elements is 300.
    "size and sum of an array": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 3> a = {200, 100, 0};
    array<int, 2> b = {-5, 2};
    message(LOW, "%d %d %d", a.size(), a.sum(), b.sum());
  }
}""", ["3 300 -3"], covers=("DT-ARR.9", "DT-ARR.10")),

    # Nested `foreach` with `continue` in the inner loop; an index named like
    # an outer local is a new variable, and the outer one is unchanged after
    # the loop (20.7.8 e, IP-24).
    "nested foreach and an index named like a local": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 2> a = {1, 2};
    array<bit[8], 3> b = {10, 20, 30};
    int i = 99;
    foreach (x : a[i]) {
      foreach (y : b[j]) {
        if (j == 1) { continue; }
        message(LOW, "%d %d %d %d", i, j, x, y);
      }
    }
    message(LOW, "%d", i);
  }
}""", ["0 0 1 10", "0 2 1 30", "1 0 2 10", "1 2 2 30", "99"],
        covers=("ST-FOREACH", "ST-BRK", "ST-BLOCK", "IP-24")),

    # `in` over a slice of an array (8.5.9.1): `a[1..2]` holds 2 and 3; a
    # slice whose bounds cross is empty.
    "in over an array slice": Case("""
component pss_top {""" + ONE + """
  target function void go(int v, int lo, int hi) {
    array<int, 4> a = {1, 2, 3, 4};
    if (v in a[lo..hi]) { message(LOW, "%d in", v); }
    else { message(LOW, "%d out", v); }
  }
  target function void run() { go(2, 1, 2); go(1, 1, 2); go(3, 2, 1); }
}""", ["2 in", "1 out", "3 out"], covers=("DT-ARR.8", "EX-IN.4")),

    # A `static const` array is read like any other (DT-ARR.16).
    "a static const array": Case("""
component pss_top {""" + ONE + """
  static const array<bit[8], 3> TBL = {5, 6, 7};
  target function void run() {
    bit[8] t = 0;
    foreach (v : TBL) { t += v; }
    message(LOW, "%d %d", TBL[1], t);
  }
}""", ["6 18"], covers=("DT-ARR.16", "DT-CONST", "ST-FOREACH")),
}

test_construct = define_tests(CASES)
