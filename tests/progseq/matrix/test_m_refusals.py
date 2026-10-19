"""Constructs refused by ruling: floating point (R5), list/map/set (R4),
and an import's `output`/`inout` parameter (W18); and every other construct
an op-model target refuses, by design or for now (P3): each refusal checked
for what it names, and where a target does render the construct, its trace.

Each refusal is checked, with what it names. These are the known-unsupported
features of docs/design/construct-test-audit/00-plan.md §7.1.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import ALL, Case, define_tests, on, ONE, Refused

I16 = ("I16: an `import class` is dropped silently; a field of its type "
       "gives an unlocated \"unresolved type reference\"")
E42 = ("E42: a template string initializing a `static const` is refused "
       "without a location")

FLOAT_EXPR = (r"line \d+: this expression is floating point \(float32/float64\), "
              r"which is not supported")
TQS = (r"a triple-quoted string with mustache expressions or control-flow "
       r"directives \(LRM 4.7.1\) is not supported yet")
RANDOMIZE = (r"line \d+: a 'ProceduralStmtRandomize' statement could not be "
             r"translated")


def each(pattern, n):
    """A pattern matching ``n`` separate occurrences of ``pattern``: one
    refusal line per use, none hidden behind the first."""
    return rf"(?s)(?:{pattern}.*?){{{n}}}"


CASES = {

    "floating point": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    float64 f = 1.5;
    a.STS.write_val((bit[32])f);
  }
}""", Refused([r"line \d+: local 'f' is floating point", r"not supported"]),
        covers=("DT-FP", "DT-FP.1", "EX-LIT.8", "ST-VAR.5")),

    "a floating-point parameter is refused, not dropped": Case("""
component pss_top {""" + ONE + """
  function int g(int x, float32 y) { return x; }
  target function void run() { a.STS.write_val(g(1, 2.0)); }
}""", Refused([r"line \d+: parameter 'y' of 'g' is floating point"]),
        covers=("DT-FP", "FN-PARAM", "DT-FP.1")),

    "a list": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    list<bit[8]> l;
    l.push_back(3);
    a.STS.write_val(l.size());
  }
}""", Refused([r"pss_top::run: local 'l': a list", r"ruling R4"]),
        covers=("DT-LIST", "DT-LIST.1", "DT-LIST.5", "DT-LIST.8", "ST-VAR.5",
                "ST-VCALL.4")),

    "a map": Case("""
component pss_top {""" + ONE + """
  map<int, int> m;
  target function void run() {
    m.insert(1, 2);
    a.STS.write_val(m[1]);
  }
}""", Refused([r"pss_top\.m: a map", r"ruling R4"]),
        covers=("DT-MAP", "DT-MAP.2", "DT-MAP.8", "EX-IDX.2", "ST-VCALL.4")),

    "a set": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    set<int> s;
    s.insert(4);
    if (4 in s) { a.STS.write_val(1); }
  }
}""", Refused([r"pss_top::run: local 's': a set", r"ruling R4"]),
        covers=("DT-SET", "DT-SET.3", "DT-SET.4")),

    # Every op-model target passes a scalar by value, so an `output`/`inout`
    # parameter's write would be lost (W18). ast2ir carries the direction
    # (`param_dirs`); the gate refuses the call.
    "an import's output parameter": Case("""
package q {
  function void fetch(output int v, inout int w, int k);
  import target function fetch;
}
component pss_top {""" + ONE + """
  target function void run() {
    int v;
    int w = 1;
    q::fetch(v, w, 2);
    a.STS.write_val(v + w);
  }
}""", Refused([r"pss_top::run.*'(q::)?fetch' has an output parameter 'v'"]),
        covers=("FN-IMPORT", "FN-PARAM", "FN-IMPORT.4", "FN-PARAM.7")),

    # --- P3: refused in ast2ir, with the line -------------------------------------
    # `randomize` in a body (20.7.12): op-model targets never solve.
    "randomize": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int x;
    randomize x with { x < 5; };
    a.STS.write_val(x);
  }
}""", Refused([r"line \d+: a 'ProceduralStmtRandomize' statement could not "
               r"be translated"]), covers=("ST-RAND", "ST-RAND.1",
                                           "ST-RAND.3")),

    "a reduction operator": Case("""
component pss_top {""" + ONE + """
  target function void run() { bit[8] x = 3; a.STS.write_val(&x); }
}""", Refused([r"line \d+: the bit-reduction operator '&' is not supported"]),
        covers=("EX-RED", "EX-RED.1")),

    "a reference parameter": Case("""
component sub_c { int v; }
component pss_top {""" + ONE + """
  sub_c s;
  target function void g(ref sub_c r) { }
  target function void run() { g(s); a.STS.write_val(1); }
}""", Refused([r"line \d+: parameter 'r' of 'g' is a reference \(`ref T`\)"]),
        covers=("DT-REF", "DT-REF.8", "FN-PARAM.10")),

    "a typedef in a component": Case("""
component pss_top {""" + ONE + """
  typedef bit[8] byte_t;
  target function void run() { byte_t x = 3; a.STS.write_val(x); }
}""", Refused([r"line \d+: a typedef in component 'pss_top' is not supported"]),
        covers=("DT-TYPEDEF", "DT-TYPEDEF.4")),

    "a target-template function": Case('''
component pss_top {''' + ONE + '''
  target C function void tt(int x) = """ foo({{x}}); """;
  target function void run() { tt(1); a.STS.write_val(1); }
}''', Refused([r"line \d+: a target-template function in component "
               r"'pss_top' is not supported"]), covers=("FN-TT", "FN-TT.1",
                                                        "FN-TT.2")),

    # `*=` is not a PSS assignment operator (8.1 `assign_op`): the parser
    # refuses it, at its column.
    "*= is not PSS": Case("""
component pss_top {""" + ONE + """
  target function void run() { int x = 3; x *= 2; a.STS.write_val(x); }
}""", Refused([r"m\.pss:\d+:\d+: error: syntax error at '\*'"]),
        covers=("EX-ASSIGN.5",)),

    # --- P3: refused by the call gate ------------------------------------------------
    # File functions (21.2) take and return strings and a file handle; a
    # generated device model has no file system.
    "file functions": Case("""
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) {
    file_handle_t f = file_open("x.txt", TRUNCATE);
    file_close(f);
    a.set_handle(base);
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"pss_top::initialize: cannot lower call: 'file_open'",
               r"'file_close'"]), covers=("LIB-FILE", "LIB-FILE.1")),

    # `urandom_range` (21.4) needs a seeded PRNG, a policy C, C++ and Python
    # do not make for their caller (ruling R7 open); SV takes the simulator's.
    # A range of one value is deterministic.
    "urandom_range": Case("""
component pss_top {""" + ONE + """
  target function void run() { a.STS.write_val(urandom_range(3, 3)); }
}""", ["write 32 0x1004 0x3"],
        refused={t: Refused([r"cannot lower call: 'urandom_range' is a PSS "
                             r"core-library function"])
                 for t in ("c", "cpp", "py")},
        covers=("LIB-RAND", "LIB-RAND.2", "LIB-RAND.3")),

    # `exec init_down`/`init_up` (20.1.2): Python and SV run them; C and C++
    # do not lower init blocks yet (P6).
    "init blocks": Case("""
component sub_c { int v; exec init_up { v = 5; } }
component pss_top {""" + ONE + """
  int v;
  sub_c s;
  exec init_down { v = 3; }
  exec init_up { v = v + s.v; }
  target function void run() { a.STS.write_val(v); }
}""", ["write 32 0x1004 0x8"],
        refused={t: Refused([r"`exec init_down` is not lowered by "
                             r"'op-model-c(pp)?' yet"])
                 for t in ("c", "cpp")},
        covers=("EXEC-KIND",)),

    # --- P4: interaction patterns waiting on a ruling ---------------------------------
    # Each pattern below needs a construct refused by ruling (R4 collections,
    # randomize, `ref`), so each is pinned here as that refusal, naming the
    # use. When the construct lands, the case becomes an executed one with
    # the trace the pattern's LRM clause gives.

    # IP-7: `m[k] += 1` reads a missing key (illegal); `m[k] = 1` inserts.
    "compound assignment to a map element": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    map<int, int> m;
    m[1] = 1;
    m[1] += 1;
    a.STS.write_val(m[1]);
  }
}""", Refused([r"pss_top::run: local 'm': a map", r"ruling R4"]),
        covers=("EX-ASSIGN", "DT-MAP", "IP-7", "DT-MAP.3")),

    # IP-26: writing `l[i].f` while iterating `l`.
    "foreach over a list of structs writing an element's field": Case("""
struct e_s { int f; }
component pss_top {""" + ONE + """
  target function void run() {
    list<e_s> l;
    e_s e;
    l.push_back(e);
    foreach (v : l[i]) { l[i].f = i + 1; }
    a.STS.write_val(l[0].f);
  }
}""", Refused([r"pss_top::run: local 'l': a list", r"ruling R4"]),
        covers=("ST-FOREACH", "EX-IDX", "DT-LIST", "IP-26", "DT-LIST.2",
                "ST-FOREACH.2")),

    # IP-27 and IP-37: an aggregate parameter is the caller's collection
    # (20.3.2): the callee iterates it, and a push is seen by the caller.
    "a list parameter iterated and grown by the callee": Case("""
component pss_top {""" + ONE + """
  target function int total(list<int> p) {
    int t = 0;
    foreach (v : p) { t += v; }
    p.push_back(t);
    return t;
  }
  target function void run() {
    list<int> l = {1, 2};
    a.STS.write_val(total(l));
    a.STS.write_val(l.size());
  }
}""", Refused([r"parameter 'p': a list", r"ruling R4"]),
        covers=("FN-PARAM", "ST-FOREACH", "DT-LIST", "IP-27", "IP-37",
                "DT-LIST.1", "DT-LIST.14")),

    # IP-34: `in` against a function-returned collection and `m.keys()`.
    "in over a returned list and a map's keys": Case("""
component pss_top {""" + ONE + """
  target function list<int> evens() { list<int> r = {0, 2, 4}; return r; }
  target function void run() {
    map<int, int> m = {1: 10, 3: 30};
    if (2 in evens() && 3 in m.keys()) { a.STS.write_val(1); }
  }
}""", Refused([r"a list", r"ruling R4"]),
        covers=("EX-IN", "FN-CALL", "DT-MAP", "IP-34", "DT-LIST.4",
                "DT-MAP.1", "DT-MAP.9", "FN-DECL.4")),

    # IP-43: `m["k"].push_back(1)` on a missing key is illegal; after
    # `m["k"] = {}` it works.
    "a map of lists": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    map<string, list<int>> m;
    m["k"] = {};
    m["k"].push_back(1);
    a.STS.write_val(m["k"].size());
  }
}""", Refused([r"pss_top::run: local 'm': a map", r"ruling R4"]),
        covers=("DT-MAP", "DT-LIST", "EX-IDX", "IP-43", "DT-LIST.15",
                "DT-MAP.2", "DT-MAP.3", "DT-MAP.11", "EX-IDX.2", "EX-LIT.11")),

    # IP-44: two modifying calls in one expression; the order is unspecified
    # (8.4.4; ruling R10 open).
    "two modifying method calls in one expression": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    list<int> l = {1, 5};
    a.STS.write_val(l.pop_back() - l.pop_front());
  }
}""", Refused([r"pss_top::run: local 'l': a list", r"ruling R4"]),
        covers=("EX-METH", "DT-LIST", "IP-44", "DT-LIST.8", "EX-METH.3")),

    # IP-56, IP-57, IP-58: `randomize` (13.4.6) with the loop index in its
    # `with`, of a struct after its non-rand fields are set, and of a
    # domain-typed local. Op-model targets never solve.
    "randomize with a loop index": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    repeat (i : 3) { int v; randomize v with { v > i; v < 10; }; a.STS.write_val(v); }
  }
}""", Refused([r"line \d+: a 'ProceduralStmtRandomize' statement could not "
               r"be translated"]),
        covers=("ST-RAND", "ST-REPEAT", "IP-56")),

    "randomize a struct after setting its non-rand fields": Case("""
struct p_s { int k; rand int r; constraint r > k; }
component pss_top {""" + ONE + """
  target function void run() { p_s p; p.k = 4; randomize p; a.STS.write_val(p.r); }
}""", Refused([r"line \d+: a 'ProceduralStmtRandomize' statement could not "
               r"be translated"]),
        covers=("ST-RAND", "DT-STRUCT", "IP-57", "DT-STRUCT.8", "ST-RAND.4",
                "ST-RAND.6")),

    "randomize a domain-typed local": Case("""
component pss_top {""" + ONE + """
  target function void run() { int in [1..5] x; randomize x; a.STS.write_val(x); }
}""", Refused([r"line \d+: a 'ProceduralStmtRandomize' statement could not "
               r"be translated"]),
        covers=("ST-RAND", "DT-INT", "IP-58", "DT-INT.6", "ST-RAND.10")),

    # IP-63: a call through `ref base_c` holding a derived instance.
    "a call through a reference to a base component": Case("""
component base_c { target function int f() { return 1; } }
component der_c : base_c { target function int f() { return 2; } }
component pss_top {""" + ONE + """
  der_c d;
  target function int call(ref base_c b) { return b.f(); }
  target function void run() { a.STS.write_val(call(d)); }
}""", Refused([r"line \d+: parameter 'b' of 'call' is a reference"]),
        covers=("FN-VIRT", "DT-REF", "IP-63", "DT-REF.8", "FN-VIRT.6",
                "FN-PARAM.10", "FN-CALL.5")),

    # IP-72, IP-73: foreach over a map into a set and a sum; duplicate set
    # inserts. The iterator is the value and the index the key (20.7.8 c/d).
    "a map iterated into a set and a sum": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    map<int, int> m = {1: 10, 2: 20};
    set<int> s;
    int t = 0;
    foreach (v : m[k]) { s.insert(k); t += v; }
    repeat (3) { s.insert(1); }
    a.STS.write_val(t + s.size());
  }
}""", Refused([r"pss_top::run: local 'm': a map", r"ruling R4"]),
        covers=("ST-FOREACH", "DT-MAP", "DT-SET", "IP-72", "IP-73",
                "DT-MAP.1", "DT-MAP.2", "DT-SET.4")),

    # IP-74: an array-returning call indexed and sized directly. Python
    # passes an array result by handle; C, C++ and SV refuse one (R8 open).
    "an aggregate-returning call used directly": Case("""
struct p_s { int x; int y; }
component pss_top {""" + ONE + """
  target function p_s h(int v) { p_s p; p.x = v; p.y = v + 1; return p; }
  target function array<int, 3> g(int v) {
    array<int, 3> r = {v, v + 1, v + 2};
    return r;
  }
  target function void run() {
    message(LOW, "%d %d %d", h(4).y, g(7)[2], g(1).size());
  }
}""", ["5 9 3"],
        refused={t: Refused([r"pss_top::g: an array result is not supported"])
                 for t in ("c", "cpp", "sv")},
        covers=("FN-CALL", "EX-REF", "EX-IDX", "IP-74", "EX-IDX.5",
                "EX-REF.9", "FN-DECL.4")),

    # IP-92, IP-93: list and set equality with literals; a list literal's
    # elements converted to the element type.
    "list and set literals compared and converted": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    list<bit[4]> l = {17, 3};
    set<int> s = {1, 1, 2};
    if (l == {1, 3} && s == {2, 1}) { a.STS.write_val(1); }
  }
}""", Refused([r"pss_top::run: local 'l': a list", r"ruling R4"]),
        covers=("EX-EQ", "EX-LIT", "DT-LIST", "DT-SET", "IP-92", "IP-93",
                "DT-LIST.3", "DT-SET.1", "DT-SET.2", "EX-EQ.5")),

    # IP-94: a foreach over a value-list literal (4.8.2: with mixed
    # self-determined types it is an error; with one type, legal).
    "foreach over a value-list literal": Case("""
component pss_top {""" + ONE + """
  target function void run() { foreach (e : {1, 2}) { a.STS.write_val(e); } }
}""", Refused([r"line \d+: a 'ProceduralStmtForeach' statement could not "
               r"be translated"]),
        covers=("EX-LIT", "ST-FOREACH", "IP-94", "ST-FOREACH.10")),

    # IP-96: foreach over a temporary (`m.values()`) while changing the map.
    "foreach over a map's values while changing the map": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    map<int, int> m = {1: 10};
    foreach (v : m.values()) { m[2] = v; }
    a.STS.write_val(m.size());
  }
}""", Refused([r"pss_top::run: local 'm': a map", r"ruling R4"]),
        covers=("ST-FOREACH", "EX-METH", "IP-96", "DT-MAP.3", "DT-MAP.6",
                "DT-MAP.9", "ST-FOREACH.10")),

    # IP-100: a struct with a list field passed by handle; the callee assigns
    # the field a new list.
    "a struct with a list field passed by handle": Case("""
struct h_s { list<int> l; }
component pss_top {""" + ONE + """
  target function void fill(h_s h) { h.l = {4, 5}; }
  target function void run() { h_s h; fill(h); a.STS.write_val(h.l.size()); }
}""", Refused([r"a list", r"ruling R4"]),
        covers=("FN-PARAM", "DT-STRUCT", "DT-LIST", "IP-100")),

    # IP-51: `write_struct`/`read_struct` (21.13.1) of a packed struct with a
    # bool and a based enum. No target lowers a generic struct parameter.
    "write_struct of a packed struct": Case("""
enum e_e : bit[2] { E0, E1, E2 }
struct p_s : packed_s<> { bool b; e_e e; bit[5] v; }
component pss_top {""" + ONE + """
  addr_handle_t hb;
  target function void run() {
    p_s p;
    p.b = true; p.e = E2; p.v = 3;
    write_struct(make_handle_from_handle(hb, 0), p);
  }
}""", Refused([r"'write_struct' is a PSS core-library function",
               r"generic struct parameters are not lowered"]),
        covers=("LIB-MEM", "DT-PACK", "IP-51", "LIB-MEM.5")),

    # --- P5.3: legal PSS refused by ruling or not lowered yet -------------------
    # A model below uses a construct several times, and the refusal is
    # required to name EVERY use (`each`), so none is hidden behind the first.

    # Float literals converted to integers (8.7.1, 8.7.2): -2.7 truncates to
    # -2, 300.9 to 300 and then 44 at bit[8], and (bit[8])-1.5 to -1, 0xFF.
    "float literals converted to integers": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int i = -2.7;
    bit[8] b = 300.9;
    bit[8] c = (bit[8])-1.5;
    a.STS.write_val(i + b + c);
  }
}""", Refused([each(FLOAT_EXPR, 3)]),
        covers=("DT-FP.3", "DT-FP.5", "DT-CAST.4")),

    # 0.1 rounded to binary32 is not 0.1 as a float64 (7.3, 8.7.2): false.
    "a float32 compared with a float64": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    float32 f = 0.1;
    if (f == 0.1) { a.STS.write_val(1); } else { a.STS.write_val(2); }
  }
}""", Refused([r"line \d+: local 'f' is floating point", each(FLOAT_EXPR, 2)]),
        covers=("DT-FP.4",)),

    # An integer operand is self-determined, then converted just before the
    # float operator (8.7.1 Table 22, Ex 44): `x + y` wraps at 2 bits to 1,
    # so `x + y + 10.0` is 11.0; `2 < 2.5` and `1.5 in [1..2]` are true, and
    # `x ** 2.0` is a float64 9.0 (8.5.1).
    "integer operands mixed with floating point": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    int i = 2;
    bit[2] x = 3;
    bit[2] y = 2;
    if (i < 2.5) { a.STS.write_val(1); }
    if (1.5 in [1..2]) { a.STS.write_val(2); }
    a.STS.write_val((int)(x + y + 10.0));
    a.STS.write_val((int)(x ** 2.0));
  }
}""", Refused([each(FLOAT_EXPR, 4)]),
        covers=("DT-FP.6", "DT-FP.7", "EX-REL.4", "EX-IN.9", "EX-WIDTH.7")),

    # A struct with a non-rand float field is still a randomizable type (7.1).
    "a struct with a floating-point field": Case("""
struct s_s { float64 f; rand int a; }
component pss_top {""" + ONE + """
  target function void run() { s_s s; s.a = 2; a.STS.write_val(s.a); }
}""", Refused([r"line \d+: field 'f' is floating point"]),
        covers=("DT-FP.2",)),

    # sqrt (21.5.2) and float_mantissa (21.5.3) take and return floats.
    "floating-point library functions": Case("""
import std_pkg::*;
component pss_top {""" + ONE + """
  target function void run() {
    a.STS.write_val((bit[32])sqrt(4.0));
    a.STS.write_val(float_mantissa(20.25));
  }
}""", Refused([each(FLOAT_EXPR, 2)]),
        covers=("LIB-FP.1", "LIB-FP.3")),

    # Reductions (8.5.6, Table 22), for when they are lowered: `|x + 1` is
    # `(|x) + 1` = 2; `^s` is 1 (five 1-bits at int[5]'s own width);
    # `&(-1)` is 1; `|(p + q)` is 0, its operand self-determined at 4 bits
    # (8 + 8 wraps), not widened to the bit[8] target. A reduction is a
    # `bit`, so a condition compares it (`&x == 1`), and `!` takes the bool.
    "reduction operators on signed operands and in arithmetic": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[8] x = 3;
    int[5] s = -1;
    bit[4] p = 8;
    bit[4] q = 8;
    a.STS.write_val(|x + 1);
    a.STS.write_val(^s);
    a.STS.write_val(&(-1));
    bit[8] r = |(p + q);
    a.STS.write_val(r);
    if (&x == 1) { a.STS.write_val(9); }
    if ((|x == 1) && !(^x == 1)) { a.STS.write_val(8); }
  }
}""", Refused([each(r"line \d+: the bit-reduction operator '[|^&]' is not "
                    r"supported", 7)]),
        covers=("EX-RED.2", "EX-RED.3", "EX-RED.4", "EX-WIDTH.9")),

    # Template strings in procedural code (4.7.1, ruling I7): a mustache of
    # each formatted type, both directives, an embedded comment, a pure call.
    "template strings in a body": Case('''
enum e_e { E0, E1 }
pure function int sq(int x) { return x * x; }
component pss_top {''' + ONE + '''
  target function void run() {
    int x = 2;
    bit[8] u = 0xFF;
    bool p = true;
    e_e e = E1;
    string sa = """x={{x}}""";
    string sb = """{% if (x > 1) %}y{% else %}n{%%}""";
    string sc = """{% repeat (i : 2) %}r{%%}""";
    string sd = """a{# {{x}} #}b""";
    string sf = """{{x}} {{u}} {{p}} {{e}}""";
    message(LOW, """v={{sq(3)}}""");
    a.STS.write_val(1);
  }
}''', Refused([each(TQS, 6)]),
        covers=("EX-TQS.1", "EX-TQS.2", "EX-TQS.3", "EX-TQS.4", "FN-PURE.5")),

    # A template string referencing only constants is a constant (4.7.1).
    "a constant template string": Case('''
component pss_top {''' + ONE + '''
  static const int K = 3;
  static const string S = """c={{K}}""";
  target function void run() { message(LOW, "%s", S); a.STS.write_val(1); }
}''', Refused([r"line \d+: " + TQS]), xfail=on(ALL, E42),
        covers=("EX-TQS.5",)),

    # An instance target-template function, declared first (20.6, 20.6.1 b).
    "an instance target-template function with a prototype": Case('''
component pss_top {
  ga_c a;
  int k = 3;
  function void tp(int x);
  target C function void tp(int x) = """ foo({{x}}, {{this.k}}); """;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { tp(1); a.STS.write_val(1); }
}''', Refused([r"line \d+: a target-template function in component "
               r"'pss_top' is not supported"]),
        covers=("FN-TT.3", "FN-TT.4")),

    # --- references (7.10), refused by ast2ir at each declaration ---
    "reference locals, a reference result and a reference in ?:": Case("""
component sub_c { int v; target function int foo() { return v; } }
component pss_top {""" + ONE + """
  sub_c s[2];
  target function ref sub_c pick() { return s[0]; }
  target function void run() {
    bool c = true;
    ref sub_c r = s[1];
    a.STS.write_val(r.foo());
    a.STS.write_val(pick().foo());
    ref sub_c q = c ? s[0] : s[1];
    ref sub_c n;
    n = r;
    if (n == r && r != null) { a.STS.write_val(1); }
  }
}""", Refused([r"line \d+: the result of 'pick' is a reference \(`ref T`\)",
               r"line \d+: local 'r' is a reference \(`ref T`\)",
               r"line \d+: local 'q' is a reference",
               r"line \d+: local 'n' is a reference"]),
        covers=("DT-REF.1", "DT-REF.9", "DT-REF.10", "EX-EQ.7",
                "EX-ASSIGN.10")),

    # Ref fields bound in init_down and the constructor, written through in
    # solve context, compared with null and an instance path (Ex 23, 9.1.5.3).
    "component reference fields": Case("""
component sib_c { int data = 4; }
component pss_top {
  ga_c a;
  sib_c s;
  ref sib_c r;
  instance ref sib_c ir;
  exec init_down { r = s; }
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    ir = s;
    r.data = 3;
  }
  target function void run() {
    if (r != null && r == s && ir == r) { message(LOW, "%d", r.data); }
  }
}""", Refused([r"line \d+: field 'r' is a reference \(`ref T`\)",
               r"line \d+: field 'ir' is a reference"]),
        covers=("DT-REF.2", "DT-REF.3", "DT-REF.4", "DT-REF.5", "DT-MUT.3",
                "EX-REF.10")),

    # A downcast of a ref whose dynamic type differs is null (7.12.1, Ex 30).
    "a reference downcast": Case("""
component sub_c { int v; }
component d_c : sub_c { }
component pss_top {""" + ONE + """
  sub_c s;
  target function void run() {
    ref sub_c r = s;
    ref d_c q = (ref d_c)r;
    if (q != null) { a.STS.write_val(1); } else { a.STS.write_val(2); }
  }
}""", Refused([r"line \d+: local 'r' is a reference",
               r"line \d+: local 'q' is a reference",
               r"line \d+: this type is a reference"]),
        covers=("DT-REF.6",)),

    # Ex 26 and Ex 358.
    "a list of references and a register reference parameter": Case("""
component sub_c { int v; }
component pss_top {""" + ONE + """
  sub_c s;
  list<ref sub_c> used;
  target function void clr(ref reg_sized_c<32> r) { r.write_val(0); }
  target function void run() { clr(a.STS); }
}""", Refused([r"line \d+: this type is a reference \(`ref T`\)",
               r"line \d+: parameter 'r' of 'clr' is a reference"]),
        covers=("DT-REF.7", "DT-REG.7")),

    # --- collections (ruling R4) ---
    "a list of address handles": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    list<addr_handle_t> hl;
    a.STS.write_val(hl.size());
  }
}""", Refused([r"pss_top::run: local 'hl': a list", r"ruling R4"]),
        covers=("DT-ADDR.8",)),

    # c and cpp also refuse the two strings (R3).
    "collection conversions": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<int, 3> c = {1, 2, 1};
    list<int> l = c.to_list();
    set<int> s = c.to_set();
    list<int> m = s.to_list();
    set<int> u = l.to_set();
    list<bit[8]> h = {104, 105};
    string j = h.str_from_chars();
    string k = l.join(",");
    a.STS.write_val(l.size() + s.size() + m.size() + u.size());
  }
}""", Refused([r"local 'l': a list", r"local 's': a set", r"local 'm': a list",
               r"local 'u': a set", r"local 'h': a list", r"ruling R4"]),
        covers=("DT-ARR.13", "DT-SET.5", "DT-LIST.9")),

    # Ex 13: a list stores a struct by value, so v1.a stays 0.
    "list delete, insert and shuffle, and storage by value": Case("""
struct v_s { int a; }
component pss_top {""" + ONE + """
  target function void run() {
    list<int> l = {1, 2, 3};
    int d = l.delete(1);
    l.insert(1, 9);
    l.insert(3, 7);
    l.shuffle();
    v_s v1;
    list<v_s> l2;
    l2.push_back(v1);
    l2[0].a = 10;
    a.STS.write_val(d + l.size() + v1.a);
  }
}""", Refused([r"local 'l': a list", r"local 'l2': a list", r"ruling R4"]),
        covers=("DT-LIST.6", "DT-LIST.7", "DT-LIST.10", "DT-LIST.11")),

    "map literal, copy, equality and delete": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    map<int, int> m = {1: 10, 2: 20};
    map<int, int> n = m;
    m[1] = 5;
    if (n != m) { a.STS.write_val(1); }
    int v = m.delete(2);
    m.clear();
    a.STS.write_val(v + m.size());
  }
}""", Refused([r"local 'm': a map", r"local 'n': a map", r"ruling R4"]),
        covers=("DT-MAP.4", "DT-MAP.7", "EX-LIT.13")),

    # 20.7.8: a map's index is its key and its iterator the value; a set
    # takes an iterator only; an empty list iterates zero times.
    "foreach over a map, a set and an empty list": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    map<int, bit[8]> mp = {1: 3, 2: 4};
    set<int> st = {5, 6};
    list<int> l;
    int t = 0;
    foreach (b : mp[k]) { t += k * b; }
    foreach (e : st) { t += e; }
    foreach (e : l) { t += 100; }
    a.STS.write_val(t);
  }
}""", Refused([r"local 'mp': a map", r"local 'st': a set",
               r"local 'l': a list", r"ruling R4"]),
        covers=("ST-FOREACH.3", "ST-FOREACH.4", "ST-FOREACH.12")),

    "read_bytes and write_bytes": Case("""
component pss_top {""" + ONE + """
  addr_handle_t h;
  target function void run() {
    list<bit[8]> l;
    read_bytes(h, l, 4);
    write_bytes(h, l);
  }
}""", Refused([r"pss_top::run: local 'l': a list", r"ruling R4"]),
        covers=("LIB-MEM.3", "LIB-MEM.4")),

    # --- core-library functions with no runtime form ---
    "address functions of the solve-time address space": Case("""
component pss_top {
  ga_c a;
  addr_handle_t h;
  bit[64] v;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    h = base;
    v = addr_value_solve(base);
  }
  target function void run() {
    transparent_addr_claim_s<> c;
    addr_handle_t h2 = make_handle_from_claim(c, 4);
    message(LOW, "%s", get_tag(h));
    a.STS.write_val(1);
  }
}""", Refused([r"pss_top::initialize: cannot lower call: 'addr_value_solve'",
               r"pss_top::run: cannot lower call: 'make_handle_from_claim'",
               r"cannot lower call: 'get_tag'"]),
        covers=("DT-ADDR.2", "DT-ADDR.5", "DT-ADDR.6")),

    # The rest of 21.2, in solve context where it is allowed.
    "the other file functions": Case("""
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) {
    if (file_exists("x.txt")) {
      file_handle_t f = file_open("x.txt", APPEND);
      file_write(f, "%d", 3);
      file_close(f);
    }
    file_write_lines("y.txt", {"a", "b"}, TRUNCATE);
    file_handle_t g = file_open("y.txt", READ);
    (void)file_read(g, -1);
    file_close(g);
    (void)file_read_lines("y.txt");
    a.set_handle(base);
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"cannot lower call: 'file_exists'",
               r"cannot lower call: 'file_write'",
               r"cannot lower call: 'file_write_lines'",
               r"cannot lower call: 'file_read'",
               r"cannot lower call: 'file_read_lines'"]),
        covers=("LIB-FILE.2", "LIB-FILE.3", "LIB-FILE.4", "LIB-FILE.5")),

    # urandom() is a bit[32] (21.4.1), so the upper word of a bit[64] is 0.
    # As for urandom_range, only SV has a PRNG policy.
    "urandom": Case("""
component pss_top {""" + ONE + """
  target function void run() { bit[64] w = urandom(); message(LOW, "%x", w >> 32); }
}""", ["0"],
        refused={t: Refused([r"cannot lower call: 'urandom' is a PSS "
                             r"core-library function"])
                 for t in ("c", "cpp", "py")},
        covers=("LIB-RAND.1",)),

    "symbolic register names": Case("""
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    a.set_mnemonic("A");
    use_symbolic_reg_names(a, true);
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"'set_mnemonic' is a PSS core-library function",
               r"'use_symbolic_reg_names' is a PSS core-library function",
               r"no runtime form"]),
        covers=("LIB-REG.11",)),

    # executor() (21.7.2.5, the Ex 333 idiom) and get_context() (Ex 300).
    # The refusal today says neither is declared by PSS, which is wrong (M2);
    # the pattern names only the function.
    "executor()": Case("""
import executor_pkg::*;
component pss_top {""" + ONE + """
  target function void run() {
    if (executor() != null) { a.STS.write_val(1); } else { a.STS.write_val(2); }
  }
}""", Refused([r"pss_top::run: cannot lower call: .*'executor'"]),
        covers=("LIB-EXEC.1", "LIB-EXEC.2")),

    "get_context()": Case("""
import executor_pkg::*;
component tap_c : executor_c<> {
  target function void ping() {
    chandle c = get_context();
    if (c == null) { message(LOW, "null"); }
  }
}
component pss_top {""" + ONE + """
  tap_c tap;
  target function void run() { tap.ping(); }
}""", Refused([r"tap_c::ping: cannot lower call: .*'get_context'"]),
        covers=("LIB-EXEC.4",)),

    # --- functions, exports and exec blocks not lowered yet ---
    # Ex 297.
    "randomize in a solve function": Case("""
component pss_top {
  ga_c a;
  int k;
  solve function int pick(int lo) { int res; randomize res with { res > lo; }; return res; }
  solve function void initialize(addr_handle_t base) { a.set_handle(base); k = pick(3); }
  target function void run() { a.STS.write_val(k); }
}""", Refused([RANDOMIZE]), covers=("FN-NATIVE.5",)),

    # In solve context, where randomize is allowed: two targets with one
    # `with` (Ex 177), a struct with solve execs (Ex 178), an unsatisfiable
    # `with` (an error at run time), hierarchical targets, a list.
    "randomize forms in solve context": Case("""
struct v_s { rand bit[4] a; }
struct s_s {
  rand bit[4] a;
  exec pre_solve { message(LOW, "pre"); }
  exec post_solve { message(LOW, "post"); }
}
component pss_top {
  ga_c a;
  int f;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    v_s v1;
    int v2;
    randomize v1, v2 with { v1.a < v2; };
    s_s s;
    randomize s;
    int x;
    randomize x with { x > 5; x < 3; };
    v_s w;
    randomize this.f, w.a;
    list<bit[4]> l = {1, 2};
    randomize l;
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([each(RANDOMIZE, 5)]),
        covers=("ST-RAND.2", "ST-RAND.5", "ST-RAND.7", "ST-RAND.9",
                "ST-RAND.12")),

    # Exports (20.4.2) of static functions of a package and of a
    # sub-component: only the root's are accepted yet.
    "exports of a package and a sub-component function": Case("""
package util_pkg {
  function int k(int x) { return x + 1; }
  export target function k;
}
component sub_c {
  static function int g() { return 2; }
  export target function g;
}
component pss_top {""" + ONE + """
  sub_c s;
  target function void run() { a.STS.write_val(util_pkg::k(1)); }
}""", Refused([r"line \d+: export of package function 'util_pkg::k': not "
               r"supported yet",
               r"line \d+: export of 'sub_c::g': only the root component"]),
        covers=("FN-EXPORT.1",)),

    # 20.4.3: an import class and one derived from it. The refusal must
    # name the declaration; today it is dropped (I16).
    "an import class": Case("""
import class base_ic { void base_function(); }
import class ext_ic : base_ic { int ext_function(int x); }
component pss_top {""" + ONE + """
  ext_ic b;
  target function void run() { b.base_function(); a.STS.write_val(b.ext_function(1)); }
}""", Refused([r"line \d+: .*import class"]), xfail=on(ALL, I16),
        covers=("FN-IMPORT.7",)),

    # pre_body (20.1.2) and run_start/run_end (20.1.5.1); ast2ir refuses an
    # action's before the gate sees a component's, so two models.
    "pre_body, run_start and run_end in an action": Case("""
component pss_top {""" + ONE + """
  action X {
    exec pre_body { }
    exec run_start { }
    exec run_end { }
    exec body { }
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"line \d+: 'exec pre_body' in action 'pss_top::X' is not "
               r"supported yet",
               r"line \d+: 'exec run_start' in action 'pss_top::X'",
               r"line \d+: 'exec run_end' in action 'pss_top::X'"]),
        covers=("EXEC-KIND.3", "EXEC-KIND.5")),

    "run_start and run_end in a component": Case("""
component pss_top {""" + ONE + """
  exec run_start { message(LOW, "rs"); }
  exec run_end { message(LOW, "re"); }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"pss_top: `exec run_start` is not lowered",
               r"pss_top: `exec run_end` is not lowered"]),
        covers=("EXEC-KIND.5",)),

    # Target-template exec blocks (20.5): two declaration blocks under one
    # tag (20.5.4: concatenated within an instance), a header with a
    # directive, an exec file with a mustache in its name, an action body.
    "target-template exec blocks": Case('''
struct t1_tag { }
component pss_top {''' + ONE + '''
  int v = 3;
  exec declaration C = t1_tag : """int x = {{v}};""";
  exec declaration C = t1_tag : """int x = {{v}};""";
  exec header C = """{% if (v > 1) %}/* big */{% else %}/* small */{%%}""";
  exec file """out_{{v}}.c""" = """hello""";
  action X { exec body C = """foo();"""; }
  target function void run() { a.STS.write_val(1); }
}''', Refused([each(r"line \d+: a target-template exec block in component "
                    r"'pss_top' is not supported yet", 4),
               r"line \d+: a target-template exec block in action "
               r"'pss_top::X'"]),
        covers=("EXEC-TT.1", "EXEC-TT.2", "EXEC-TT.3", "EXEC-TT.4",
                "EXEC-TT.5", "EXEC-TT.6", "EXEC-KIND.6")),

    # A group may give its offsets by `get_offset_of_path` (21.14.2); no
    # target evaluates it yet. Its registers took the dense layout, the
    # first at 0, without a word (W48).
    "a register group with get_offset_of_path": Case("""
pure component gp_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> R;
  function bit[64] get_offset_of_path(list<node_s> path) { return 0x8; }
}
component pss_top {
  gp_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() { g.R.write_val(1); }
}""", Refused([r"'gp_c' gives its offsets by get_offset_of_path, which is "
               r"not evaluated yet"]), covers=("LIB-REG.7",)),

    # A member named `_` and a non-digit is a reserved placeholder: it holds
    # address space and no target surfaces it. An access through one, from
    # a body or through a sub-component, is refused where it is written;
    # each backend used to lower it by its own fallback (C an untyped
    # access, SV a class member that failed only at elaboration).
    "an access to a reserved register": Case("""
pure component gr_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  reg_c<bit[32], READONLY, 32> _reserved0;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["CTL"]: return 0x0; ["_reserved0"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component go_c : reg_group_c {
  gr_c _rsvd;
  gr_c inner;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["_rsvd"]: return 0x0; ["inner"]: return 0x10; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component sub_c {
  go_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
}
component pss_top {
  go_c g;
  sub_c s;
  solve function void initialize(addr_handle_t base) {
    g.set_handle(base);
    s.initialize(base);
  }
  target function void run() {
    bit[32] v;
    g.inner.CTL.write_val(1);
    v = g.inner._reserved0.read_val();
    g._rsvd.CTL.write_val(v);
    s.g.inner._reserved0.read_val();
  }
}""", Refused([r"m\.pss:\d+:\d+: pss_top::run: `g\.inner\._reserved0\.read_val"
               r"\(\.\.\.\)` accesses `g\.inner\._reserved0`, a reserved "
               r"register-group member",
               r"`g\._rsvd\.CTL\.write_val\(\.\.\.\)` accesses `g\._rsvd`",
               r"`s\.g\.inner\._reserved0\.read_val\(\.\.\.\)` accesses "
               r"`s\.g\.inner\._reserved0`"]),
        covers=("LIB-REG.2",)),
}

test_construct = define_tests(CASES)
