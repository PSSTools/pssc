"""Constructs refused by ruling: floating point (R5), list/map/set (R4),
and an import's `output`/`inout` parameter (W18).

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
}

test_construct = define_tests(CASES)
