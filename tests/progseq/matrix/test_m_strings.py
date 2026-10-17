"""Strings in a body (LRM 7.6, 8.6.3). Refused on op-model-c and -cpp
(ruling R3).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, ERROR, on, ONE, Refused

W37 = ("W37: an out-of-range sub-string is not an error "
       "(8.6.3; ruling R12 open)")
M3 = "M3: array join / str_from_chars are refused as unknown names (7.9.2.2)"
E27 = ("E27: an import signature type (20.4.1 c) is an internal error, not "
       "a located refusal")
H1 = ("H1: the harness's platform implements no user import, so SV's "
      "import interface does not compile")


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
        covers=("DT-STR", "DT-STR.1", "EX-EQ.2", "ST-VAR.5")),

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
        covers=("DT-STR", "EX-METH", "DT-STR.3", "DT-STR.4", "DT-STR.5",
                "DT-STR.7", "EX-METH.1")),

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
        covers=("DT-STR", "EX-SUBSTR", "ST-MATCH", "IP-13", "DT-STR.13",
                "EX-SUBSTR.1", "EX-SUBSTR.2", "EX-SUBSTR.6", "ST-MATCH.7")),

    # A method called on a method's or a function's result (7.6.3): the chain
    # is evaluated left to right, each call on the string the one before it
    # returned, and the function is called once.
    "chained string method calls": Case("""
struct ctr_s { int n; }
component pss_top {""" + ONE + """
  function string f(ctr_s c) { c.n += 1; return "xYz"; }
  target function void run() {
    string s = "aBcB";
    ctr_s c;
    a.STS.write_val(s.lower().size());
    message(LOW, "%d %d", s.upper().find("B"), s.lower().find_last("b"));
    string u = f(c).upper().lower();
    int z = f(c).size();
    message(LOW, "%s %d %d", u, z, c.n);
    message(LOW, "%d", "Ab".upper().lower().find("b"));
  }
}""", ["write 32 0x1004 0x4", "1 3", "xyz 3 2", "1"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-METH", "FN-CALL", "DT-STR.7", "EX-METH.2")),

    # A list-returning method's result is a list, chained or not.
    "a method chained on a list-returning string method": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "a,b";
    a.STS.write_val(s.split(",").size());
  }
}""", Refused([r"the string method 'split' returns a list"]),
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-METH", "DT-STR.8")),

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
        covers=("DT-STR", "EX-METH", "DT-STR.8")),

    # --- P4: interaction patterns ---------------------------------------------------
    # Chained string methods in a condition, in a loop (IP-69; the `split`
    # half is "string methods that return a list").
    "chained string methods in a loop condition": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "AbCd";
    repeat (i : 2) {
      if (s.lower().find("c") == 2 && s.upper().size() > i + 3) {
        message(LOW, "%d yes", i);
      } else {
        message(LOW, "%d no", i);
      }
    }
  }
}""", ["0 yes", "1 no"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR", "EX-METH", "ST-IF", "ST-REPEAT", "IP-69")),

    # A sub-string's positions must lie in the string (8.6.3, IP-70):
    # `s[i..s.size()-1]` is good up to i = 3, and at i = 4 its start is past
    # its end. Python and SV give an empty string (W37).
    "a sub-string with loop-index bounds, then one past the end": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "abcd";
    repeat (i : 5) { message(LOW, "%s", s[i..s.size()-1]); }
  }
}""", ["abcd", "bcd", "cd", "d", ERROR],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        xfail=on(("py", "sv"), W37),
        covers=("EX-SUBSTR", "ST-REPEAT", "IP-70", "EX-SUBSTR.3")),

    # `+` is not a string operator (8.4, Table 10; IP-71): `format()` or a
    # template string joins strings. pssparser reports it (PSS131; was W38:
    # Python concatenated, and SV emitted a `+` Verilator rejects).
    "string concatenation with +": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "ab";
    string t = s + "c";
    message(LOW, "%s", t);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: operator '\+' takes numeric operands, not string "
               r"\(LRM 8\.4 Table 10, 8\.5\.1\)"]),
        covers=("DT-STR", "EX-ARITH", "IP-71", "DT-STR.1")),

    # --- P5.3: defaults, literals, arrays of strings, imports ----------------------
    # A string with no initializer is "" (7.6); `in` takes a list of string
    # literals (7.6.4).
    "an empty string by default, and in over strings": Case("""
component pss_top {""" + ONE + """
  string f;
  target function void go(string s) {
    if (s in ["ab", "cd"]) { message(LOW, "in"); } else { message(LOW, "out"); }
  }
  target function void run() {
    string s;
    if (s == "" && f == "") { message(LOW, "empty %d", s.size()); }
    go("cd");
    go("x");
  }
}""", ["empty 0", "in", "out"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR.1", "DT-STR.13", "EX-IN")),

    # `find_all` and `chars` return a list (7.6.3), which no op-model target
    # holds (ruling R4).
    "find_all and chars return a list": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "abab";
    foreach (p : s.find_all("ab")) { a.STS.write_val(p); }
    foreach (c : s.chars()) { a.STS.write_val(c); }
  }
}""", Refused([r"the string method 'find_all' returns a list",
               r"the string method 'chars' returns a list", r"ruling R4"]),
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-STR.6", "DT-STR.9")),

    # The array methods that make a string (7.9.2.2): `join` puts the
    # connector between elements (none for one element; "" joins them
    # bare), and `str_from_chars` casts each element to 8 bits (0x142 is
    # 'B').
    "join and str_from_chars of an array": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<string, 3> s = {"a", "b", "c"};
    array<string, 1> o = {"x"};
    array<bit[16], 3> c = {0x41, 0x142, 0x43};
    message(LOW, "%s|%s|%s|%s", s.join(","), o.join(","), s.join(""),
            c.str_from_chars());
  }
}""", ["a,b,c|x|abc|ABC"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        xfail=on(("py", "sv"), M3),
        covers=("DT-STR.10", "DT-ARR.11", "DT-ARR.12")),

    # The escapes of 4.7 Table 4 and a triple-quoted literal (quotes allowed,
    # no escapes), in a format string, so C and C++ run it too: a tab, one
    # backslash, ", ', ?, and octal 101, 'A'.
    "escapes and a triple-quoted literal": Case(r'''
component pss_top {''' + ONE + r'''
  target function void run() {
    message(LOW, "a\tb\\c\"d\'e\?f\101g|");
    message(LOW, """t "q" x""");
  }
}''', ["a\tb\\c\"d'e?fAg|", 't "q" x'],
        covers=("DT-STR.11", "DT-STR.12", "EX-LIT")),

    # An escape not in Table 4 is an error (4.7).
    "an unknown escape": Case(r'''
component pss_top {''' + ONE + r'''
  target function void run() { message(LOW, "a\qb"); }
}''', Refused([r"PSS027|escape"]), covers=("DT-STR.11",)),

    # An array of strings (7.9): elements assigned and read like any other.
    # (`s[1].size()` is F24, so the size is read through a local.)
    "an array of strings": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    array<string, 2> s = {"a", "bc"};
    s[0] = "zz";
    string t = s[1];
    message(LOW, "%s %s %d", s[0], s[1], t.size());
  }
}""", ["zz bc 2"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("DT-ARR.19", "DT-STR")),

    # A sub-string is not an lvalue (7.6.2).
    "an assignment to a sub-string": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    string s = "abc";
    s[0] = "x";
    message(LOW, "%s", s);
  }
}""", Refused([r"m\.pss:\d+:\d+: error: a sub-string of 's' on the left of an assignment: "
               r"the sub-string operator shall not be used there "
               r"\(LRM 7\.6\.2\)"]),
        covers=("EX-SUBSTR.4",)),

    # `string` is an import type (20.4.1 c); an import declared and not
    # called costs nothing where strings are held. (SV declares it on its import
    # interface, which the harness's platform does not implement: H1.)
    "a string in an import signature": Case("""
package pk { import target function string echo(string s); }
component pss_top {""" + ONE + """
  target function void run() { a.STS.write_val(1); }
}""", ["write 32 0x1004 0x1"],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        xfail={**on(("c", "cpp"), E27), "sv": H1},
        covers=("DT-STR.16", "FN-IMPORT")),

    # `list<string>` is not (20.4.1 c), and no target holds a list (R4).
    "a list of strings in an import signature": Case("""
package pk { import target function int n(list<string> s); }
component pss_top {""" + ONE + """
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"m\.pss:\d+:\d+: error: parameter 's' of imported function 'n' has type list "
               r"of string, which an import cannot take \(LRM 20\.4\.1 c\)"]),
        covers=("DT-STR.16",)),

    # --- error()/fatal() strings are known at solve time (21.3; R15) ----------
    # A string constant may be printed from target code ...
    "error() of constant strings in target code": Case("""
component pss_top {""" + ONE + """
  static const string S = "abc";
  target function void run() {
    a.STS.write_val(1);
    error("bad %s %d", S, 3);
  }
}""", ["write 32 0x1004 0x1", ERROR],
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("LIB-ERR.3", "LIB-ERR.1")),

    # ... a parameter or a local may not: in target context the format and
    # string arguments of error() and fatal() are constant at run time.
    "error() and fatal() of run-time strings in target code": Case("""
component pss_top {""" + ONE + """
  target function void f(string s) { error("bad %s", s); }
  target function void run() {
    string m = "z %d";
    f("x");
    fatal(1, m, 3);
  }
}""", Refused([r"pss_top::f: error\(\)'s string argument 2 is not known at "
               r"solve time", r"pss_top::run: fatal\(\)'s format is not known "
               r"at solve time"]),
        refused=on(("c", "cpp"), Refused([r": a string", r"ruling R3"])),
        covers=("LIB-ERR.3",)),
}

test_construct = define_tests(CASES)
