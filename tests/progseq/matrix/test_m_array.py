"""Arrays of data: fields, locals, copies, initializers, parameters, foreach,
equality (LRM 8.1, 8.5.3, 20.7.8).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, ERROR, on, ONE, Refused

W65 = ("W65: the bounds check of an index narrower than the array's size "
       "(bit[1], bit[2] for 4) fails the build with -Werror=type-limits")
E41 = ("E41: a foreach iterator over a two-dimensional array (a row) is an "
       "internal error (unsupported type for DataTypeArray)")
M6 = ("M6: size() of an array-typed foreach iterator is refused as an "
      "unknown function")


CASES = {
    # --- Arrays of data in a component -------------------------------------------
    # SV declared no member for an array field, so a body naming one did not
    # compile. Elements start at 0 (LRM 8.1); 5 + 6 + (0 + 0 + 6 + 0).
    # Set while the component is constructed (a component's fields are not
    # written from target code: 20.7.3 b, R13), read by an operation.
    "array fields of a component": Case("""
component pss_top {
  ga_c a;
  array<bit[32], 4> keys;
  bit[16] raw[3];
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    keys[1] = 5;
    raw[2] = 6;
  }
  target function void run() {
    bit[32] t = 0;
    foreach (raw[i]) { t += raw[i]; }
    a.STS.write_val(keys[1] + raw[2] + t);
  }
}""", ["write 32 0x1004 0x11"], covers=("DT-ARR", "EX-IDX", "ST-FOREACH",
                                        "DT-ARR.1", "DT-ARR.4")),

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
}""", ["write 32 0x1004 0x1b"], covers=("DT-ARR", "ST-FOREACH", "EX-ASSIGN",
                                        "DT-ARR.1", "DT-ARR.3",
                                        "ST-FOREACH.1")),

    # An enum element starts at its first item, a 2-D array is copied whole,
    # and an array of structs copied into a field is a copy: writing the
    # local afterwards leaves the field alone. ae: 3; m2[1][0] = 4 copied to
    # n2; keep[1].x = 5, then l[1].x = 9: 3 + 4 + 5 + 7 = 0x13.
    "local arrays: enum elements, two dimensions, copied structs": Case("""
enum m_e { M_A = 3, M_B = 1 }
struct p_s { bit[8] x; bit[8] y = 7; }
component pss_top {""" + ONE + """
  target function void run() {
    array<p_s, 2> keep;
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
}""", ["write 32 0x1004 0x13"], covers=("DT-ARR", "EX-ASSIGN", "DT-ARR.14",
                                        "DT-ARR.19", "EX-IDX.3")),

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
        covers=("DT-ARR", "FN-PARAM", "FN-PARAM.13")),

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
    array<array<bit[8], 2>, 2> h = g;
    h = {{w[1], 7}, {h[0][0], h[1][1]}};
    a.STS.write_val(h[0][0] * 1000 + h[0][1] * 100 + h[1][0] * 10 + h[1][1]);
    array<m_e, 2> f = e;
    f = {f[0], B};
    if (f[0] == C && f[1] == B) { a.STS.write_val(1); }
  }
}""", ["write 32 0x1004 0x2d", "write 32 0x1004 0x2c3",
       "write 32 0x1004 0xe82", "write 32 0x1004 0x1"],
        covers=("DT-ARR", "EX-LIT", "DT-ARR.3")),

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
               r"for an array of 2"]), covers=("DT-ARR", "EX-LIT", "DT-ARR.3")),

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
        covers=("ST-FOREACH", "DT-ARR", "ST-FOREACH.1", "ST-FOREACH.6")),

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
        covers=("DT-ARR", "EX-EQ", "EX-EQ.5")),

    # `in` over an array (8.5.9): membership, element by element.
    "in over an array": Case("""
component pss_top {""" + ONE + """
  array<bit[8], 3> w = {5, 6, 7};
  target function void go(bit[8] v) {
    if (v in w) { a.STS.write_val(1); } else { a.STS.write_val(0); }
  }
  target function void run() { go(6); go(8); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0"],
        covers=("EX-IN", "DT-ARR", "DT-ARR.7", "ST-IF.5")),

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
        covers=("ST-FOREACH", "ST-BRK", "ST-BLOCK", "IP-24", "ST-FOREACH.8")),

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
}""", ["6 18"], covers=("DT-ARR.16", "DT-CONST", "ST-FOREACH", "DT-CONST.2",
                        "EX-IDX.7", "ST-FOREACH.13")),

    # --- P4: interaction patterns ---------------------------------------------------
    # `in` over a slice whose bounds are loop variables (8.5.9.1, IP-95): a
    # slice whose left bound passes its right, whose left bound is past the
    # end, or whose right bound is negative is empty, and `in` is false.
    "in over a slice with run-time bounds, some empty": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 4> w = {7, 8, 9, 10};
    repeat (i : 6) {
      int l = i;
      int r = 3 - i;
      message(LOW, "%d %d %d %d", i, 9 in w[l..r], 7 in w[..r], 10 in w[l..]);
    }
  }
}""", ["0 1 1 1", "1 1 1 1", "2 0 1 1", "3 0 1 1", "4 0 0 0", "5 0 0 0"],
        covers=("EX-IN", "DT-ARR", "ST-REPEAT", "IP-95")),

    # --- R12: an out-of-bounds index is an error (8.6.2) ------------------------
    # Every access of an index not known to be in range is checked: a
    # `foreach` or `repeat` index bounded by the array needs no check, an
    # `&&` guard keeps the check from running, and an index past the end is
    # the model's run-time error. C and C++ wrote past the array; SV dropped
    # the write.
    "an array index past the end": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 4> w = {7, 8, 9, 10};
    int i = 0;
    while (i < 4 && w[i] != 0) { i += 1; }
    repeat (k : 3) { w[k + 1] = k; }
    message(LOW, "%d %d %d %d", i, w[1], w[2], w[3]);
    int j = 4;
    w[j] = 1;
    message(LOW, "after");
  }
}""", ["4 0 1 2", ERROR], covers=("EX-IDX", "DT-ARR", "DT-ARR.4", "EX-IDX.1",
                                  "EX-IDX.3")),

    # A negative index is out of bounds too (Python would wrap it to the
    # last element).
    "a negative array index": Case("""
struct s_s { array<bit[8], 2> m; }
component pss_top {""" + ONE + """
  target function void run() {
    s_s s;
    s.m[1] = 5;
    int j = -1;
    message(LOW, "%d", s.m[j + 2]);
    message(LOW, "%d", s.m[j]);
  }
}""", ["5", ERROR], covers=("EX-IDX", "DT-ARR", "DT-STRUCT", "DT-ARR.4",
                            "EX-IDX.1")),

    # --- P5: aggregate literals where their place types them -------------------
    # `v in {a, b}` (8.5.9.1): a value-list literal is a collection. Its items
    # and the value are typed as for a collection (Table 22, EX-IN.5): the
    # larger width, so `(x + y)` of two `bit[2]` is 5 here, not 1. Each item
    # may be a run-time value (Ex 35). Was an internal error on all four (E22).
    "in over a value-list literal": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[2] x = 3;
    bit[2] y = 2;
    int k = 7;
    if ((x + y) in {4, 5, 6}) { a.STS.write_val(1); } else { a.STS.write_val(0); }
    if (k in {1, k - 1, 9}) { a.STS.write_val(1); } else { a.STS.write_val(0); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0"],
        covers=("EX-IN.3", "EX-IN.5", "EX-LIT.12")),

    # An array literal passed for a `const` array parameter (20.2.3) and
    # returned as an array result (20.3.2): typed by the parameter and the
    # result, each item assigned to the element type (300 is 0x2c in a
    # `bit[8]`). Python was an internal error (E23); the others refuse array
    # parameters (R8).
    "an array literal as an argument and as a result": Case("""
component pss_top {""" + ONE + """
  function bit[8] sum(const array<bit[8], 2> v) { return v[0] + v[1]; }
  function array<bit[8], 2> pair(bit[8] b) { return {b, b + 1}; }
  target function void run() {
    array<bit[8], 2> p = pair(4);
    a.STS.write_val(sum({3, 300}));
    a.STS.write_val(p[1]);
  }
}""", ["write 32 0x1004 0x2f", "write 32 0x1004 0x5"],
        refused=on(("c", "cpp", "sv"),
                   Refused([r"pss_top::sum: an array parameter 'v'"])),
        covers=("EX-LIT.12", "EX-LIT.16", "DT-ARR.18")),

    # An array compared with a literal (8.5.3; the literal may stand on the
    # right, 8.4.2): equal when every element is, a row of a two-dimensional
    # array compared row by row. Was an internal error on all four (E20);
    # `array_methods` lowers it to element comparisons.
    "an array compared with an array literal": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 2> v = {1, 2};
    array<array<bit[8], 2>, 2> m = {{1, 2}, {3, 4}};
    if (v == {1, 2}) { a.STS.write_val(1); } else { a.STS.write_val(0); }
    if (v != {1, 3}) { a.STS.write_val(1); } else { a.STS.write_val(0); }
    if (m == {{1, 2}, {3, 5}}) { a.STS.write_val(1); } else { a.STS.write_val(0); }
    if (m[1] == {3, 4}) { a.STS.write_val(1); } else { a.STS.write_val(0); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x1", "write 32 0x1004 0x0",
       "write 32 0x1004 0x1"],
        covers=("DT-ARR.6", "DT-ARR.2", "EX-LIT.16")),

    "an array compared with a literal of another length": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<bit[8], 2> v = {1, 2};
    if (v == {1, 2, 3}) { a.STS.write_val(1); }
  }
}""", Refused([r"m\.pss:\d+:\d+: .*an array of 2 element\(s\) compared with "
               r"an aggregate literal of 3"]),
        covers=("DT-ARR.6", "EX-EQ.5")),

    # --- P5.3: syntaxes, index types, parameters, rows ---------------------------
    # `int a[3]` and `array<int, 3>` are one type (7.9.2, Example 15):
    # assigned and compared with each other.
    "the two array syntaxes are one type": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int a[3];
    array<int, 3> b;
    a[1] = 4;
    b = a;
    message(LOW, "%d %d", b[1], a == b);
  }
}""", ["4 1"], covers=("DT-ARR.1", "EX-EQ.5")),

    # An index may be of any integral type (8.6.2): 64 bits wide, narrower
    # than the size needs, signed, one bit.
    "array indices of every integer type": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[64] i, bit[2] j, int[8] k, bit[1] b) {
    array<bit[8], 4> w = {10, 11, 12, 13};
    message(LOW, "%d %d %d %d", w[i], w[j], w[k], w[b]);
  }
  target function void run() { go(3, 2, 1, 1); }
}""", ["13 12 11 11"],
        xfail={"cpp": W65},
        covers=("EX-IDX.4", "EX-IDX")),

    # An array argument has the formal's size (8.3): a 3-element array for a
    # 2-element parameter is an error. C, C++ and SV refuse array parameters
    # (R8).
    "an array argument of another size than the parameter": Case("""
component pss_top {""" + ONE + """
  function bit[8] sum(array<bit[8], 2> v) { return v[0] + v[1]; }
  target function void run() {
    array<bit[8], 3> w;
    w[1] = 3;
    message(LOW, "%d", sum(w));
  }
}""", Refused([r"m\.pss:\d+:\d+: error: argument 1 of 'sum' is an array of 3 elements, but "
               r"parameter 'v' is an array of 2 \(LRM 8\.3\)"]),
        covers=("FN-PARAM.13",)),

    # The iterator over a two-dimensional array is a row, itself an array
    # (7.9.2): iterated in turn, and indexed.
    "foreach over the rows of a two-dimensional array": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<array<int, 3>, 2> m = {{1, 2, 3}, {4, 5, 6}};
    int a2[2][3] = {{7, 8, 9}, {10, 11, 12}};
    foreach (r : m[i]) {
      foreach (e : r[j]) { message(LOW, "%d %d %d", i, j, e); }
    }
    foreach (row : a2) { message(LOW, "%d %d", row[0], row[2]); }
  }
}""", ["0 0 1", "0 1 2", "0 2 3", "1 0 4", "1 1 5", "1 2 6", "7 9", "10 12"],
        xfail=on(("c", "sv"), E41),
        covers=("ST-FOREACH.11", "DT-ARR.2", "ST-FOREACH")),

    # A row's methods are an array's (7.9.2.2).
    "size of a foreach row": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<array<int, 3>, 2> m;
    foreach (r : m) { message(LOW, "%d", r.size()); }
  }
}""", ["3", "3"], xfail=on(("c", "cpp", "py", "sv"), M6),
        covers=("ST-FOREACH.11", "DT-ARR.9")),

    # `foreach` over an array parameter iterates the caller's array, which the
    # callee's write then changes (20.3.2). C, C++ and SV refuse array
    # parameters (R8).
    "foreach over an array parameter": Case("""
component pss_top {""" + ONE + """
  function int tot(array<int, 3> p) {
    int t = 0;
    foreach (v : p[i]) { t += v * (i + 1); }
    p[0] = 9;
    return t;
  }
  target function void run() {
    array<int, 3> w = {1, 2, 3};
    message(LOW, "%d", tot(w));
    message(LOW, "%d", w[0]);
  }
}""", ["14", "9"],
        refused=on(("c", "cpp", "sv"),
                   Refused([r"pss_top::tot: an array parameter 'p'"])),
        covers=("ST-FOREACH.14", "FN-PARAM")),
}

test_construct = define_tests(CASES)
