"""Strings in a body (LRM 7.6, 8.6.3). Refused on op-model-c and -cpp
(ruling R3).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, Refused


CASES = {

    "a string local, field and parameter": Case("""
component pss_top {""" + ONE + """
  string f = "ab";
  function bool same(string x, string y) { return x == y; }
  target function void run() {
    string s = "ab";
    if (same(s, f)) { a.STS.write_val(1); }
    s = "cd";
    if (s != f) { a.STS.write_val(2); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x2"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR",)),

    "string methods": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "abcab";
    a.STS.write_val(s.size());
    a.STS.write_val(s.find("ca"));
  }
}""", ["write 32 0x1004 0x5", "write 32 0x1004 0x2"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        xfail=on(("py", "sv"),
                 "E1: string methods are not lowered (refused as unknown "
                 "calls)"),
        covers=("DT-STR", "EX-METH")),
}

test_construct = define_tests(CASES)
