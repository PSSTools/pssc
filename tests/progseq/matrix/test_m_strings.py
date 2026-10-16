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

    # The methods that return a scalar or a string (7.6.3): `find` counts from
    # its start position, `find_last` backwards from its; an empty sub-string
    # is found at 0 and at the size respectively.
    "string methods": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "abcab";
    string e = "";
    a.STS.write_val(s.size());
    a.STS.write_val(s.find("ca"));
    message(LOW, "%d %d %d %d", s.find("ab", 1), s.find("x"), s.find(e),
            e.size());
    message(LOW, "%d %d %d %d", s.find_last("ab"), s.find_last("ab", 2),
            s.find_last("c", 1), s.find_last(""));
    message(LOW, "%s %s", s.upper(), "MiXed1".lower());
  }
}""", ["write 32 0x1004 0x5", "write 32 0x1004 0x2", "3 -1 0 0",
       "3 0 -1 5", "ABCAB mixed1"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-METH")),

    # The sub-string operator (8.6.3): both positions included; an omitted
    # one is the string's start or end; one position is one character. A
    # `match` on a sub-string (IP-13).
    "sub-strings": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "hello world";
    message(LOW, "[%s] [%s] [%s] [%s]", s[0..4], s[6..], s[..1], s[4]);
    repeat (i : 3) {
      match (s[i * 6..i * 6 + 4]) {
        ["hello"]: message(LOW, "greeting");
        ["world"]: message(LOW, "place");
        default: message(LOW, "other");
      }
    }
  }
}""", ["[hello] [world] [he] [o]", "greeting", "place", "other"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-SUBSTR", "ST-MATCH", "IP-13")),

    # A method called on a method's or a function's result (7.6.3): the chain
    # is evaluated left to right, each call on the string the one before it
    # returned, and the function is called once.
    "chained string method calls": Case("""
component pss_top {""" + ONE + """
  int n;
  function string f() { n += 1; return "xYz"; }
  target function void run() {
    string s = "aBcB";
    a.STS.write_val(s.lower().size());
    message(LOW, "%d %d", s.upper().find("B"), s.lower().find_last("b"));
    message(LOW, "%s %d %d", f().upper().lower(), f().size(), n);
    message(LOW, "%d", "Ab".upper().lower().find("b"));
  }
}""", ["write 32 0x1004 0x4", "1 3", "xyz 3 2", "1"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-METH", "FN-CALL")),

    # A list-returning method's result is a list, chained or not.
    "a method chained on a list-returning string method": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "a,b";
    a.STS.write_val(s.split(",").size());
  }
}""", Refused([r"the string method 'split' returns a list"]),
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-METH")),

    # `find_all`, `split` and `chars` return a list, which no op-model target
    # holds (ruling R4).
    "string methods that return a list": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "a,b";
    foreach (p : s.split(",")) { a.STS.write_val(1); }
  }
}""", Refused([r"the string method 'split' returns a list"]),
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-METH")),
}

test_construct = define_tests(CASES)
