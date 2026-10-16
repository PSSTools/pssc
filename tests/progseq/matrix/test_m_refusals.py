"""Constructs refused by ruling: floating point (R5), list/map/set (R4),
and an import's `output`/`inout` parameter (W18); and every other construct
an op-model target refuses, by design or for now (P3): each refusal checked
for what it names, and where a target does render the construct, its trace.

Each refusal is checked, with what it names. These are the known-unsupported
features of docs/design/construct-test-audit/00-plan.md §7.1.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, Refused


CASES = {

    "floating point": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    float64 f = 1.5;
    a.STS.write_val((bit[32])f);
  }
}""", Refused([r"line \d+: local 'f' is floating point", r"not supported"]),
        covers=("DT-FP",)),

    "a floating-point parameter is refused, not dropped": Case("""
component pss_top {""" + ONE + """
  function int g(int x, float32 y) { return x; }
  target function void run() { a.STS.write_val(g(1, 2.0)); }
}""", Refused([r"line \d+: parameter 'y' of 'g' is floating point"]),
        covers=("DT-FP", "FN-PARAM")),

    "a list": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    list<bit[8]> l;
    l.push_back(3);
    a.STS.write_val(l.size());
  }
}""", Refused([r"pss_top::run: local 'l': a list", r"ruling R4"]),
        covers=("DT-LIST",)),

    "a map": Case("""
component pss_top {""" + ONE + """
  map<int, int> m;
  target function void run() {
    m.insert(1, 2);
    a.STS.write_val(m[1]);
  }
}""", Refused([r"pss_top\.m: a map", r"ruling R4"]),
        covers=("DT-MAP",)),

    "a set": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    set<int> s;
    s.insert(4);
    if (4 in s) { a.STS.write_val(1); }
  }
}""", Refused([r"pss_top::run: local 's': a set", r"ruling R4"]),
        covers=("DT-SET",)),

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
        covers=("FN-IMPORT", "FN-PARAM")),

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
               r"be translated"]), covers=("ST-RAND",)),

    "a reduction operator": Case("""
component pss_top {""" + ONE + """
  target function void run() { bit[8] x = 3; a.STS.write_val(&x); }
}""", Refused([r"line \d+: the bit-reduction operator '&' is not supported"]),
        covers=("EX-RED",)),

    "a reference parameter": Case("""
component sub_c { int v; }
component pss_top {""" + ONE + """
  sub_c s;
  target function void g(ref sub_c r) { }
  target function void run() { g(s); a.STS.write_val(1); }
}""", Refused([r"line \d+: parameter 'r' of 'g' is a reference \(`ref T`\)"]),
        covers=("DT-REF",)),

    "a typedef in a component": Case("""
component pss_top {""" + ONE + """
  typedef bit[8] byte_t;
  target function void run() { byte_t x = 3; a.STS.write_val(x); }
}""", Refused([r"line \d+: a typedef in component 'pss_top' is not supported"]),
        covers=("DT-TYPEDEF",)),

    "a target-template function": Case('''
component pss_top {''' + ONE + '''
  target C function void tt(int x) = """ foo({{x}}); """;
  target function void run() { tt(1); a.STS.write_val(1); }
}''', Refused([r"line \d+: a target-template function in component "
               r"'pss_top' is not supported"]), covers=("FN-TT",)),

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
               r"'file_close'"]), covers=("LIB-FILE",)),

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
        covers=("LIB-RAND",)),

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
}

test_construct = define_tests(CASES)
