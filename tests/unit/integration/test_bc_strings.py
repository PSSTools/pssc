"""Strings on bc (bc procedural gaps G1, decision B-D1).

A string is its index in the interned table, and bc makes no string at run
time, so ``==`` / ``!=`` and an equality ``match`` pattern compare indices.
Ordering is not a string operation (7.6), and a range pattern on a string
would compare indices, so both are located errors, as is an import that would
pass a string to the platform.
"""
import pytest

from zuspec.be.bc.lower.errors import LoweringError, PssSemanticError

from .test_activity_bc_runs import trace

_SRC = """\
import std_pkg::*;
function bool is_sz(string name) { return name == "sz"; }
function int code(string name) {
    match (name) {
        ["csr"]:        return 0;
        ["sz", "adr"]:  return 4;
        default:        return -1;
    }
}
%s
component pss_top {
    string tag = "sz";
    action T {
        string mine = "x";
        exec body { %s }
    }
}
"""


def _run(body: str, decls: str = ""):
    return trace(_SRC % (decls, body))


@pytest.mark.parametrize("expr,want", [
    ('"a" == "a"', "1"), ('"a" == "b"', "0"), ('"a" != "b"', "1"),
    ('is_sz("sz")', "1"), ('is_sz("s")', "0"), ('is_sz("")', "0"),
    ('mine == "x"', "1"), ('comp.tag == "sz"', "1"), ('comp.tag != mine', "1"),
])
def test_equality(expr, want):
    assert _run(f'bool r = {expr}; message(NONE, "%u", r);') == [want]


def test_a_local_assigned_from_another_compares_equal():
    assert _run('string s = mine; string t; t = s; bool r = t == "x"; '
                'string e; bool z = e == ""; message(NONE, "%u %u", r, z);') \
        == ["1 1"]


def test_match_on_a_string_takes_the_equal_arm_or_the_default():
    assert _run('message(NONE, "%d %d %d %d %d", code("csr"), code("sz"), '
                'code("adr"), code("ADR"), code("cs"));') == ["0 4 4 -1 -1"]


@pytest.mark.parametrize("body,exc,match", [
    ('bool r = "a" < "b";', PssSemanticError, "only with == and !="),
    ('bool r = mine == 1;', PssSemanticError, "only with a string"),
    ('match (mine) { ["a".."c"]: { } default: { } }', LoweringError,
     "range pattern on a string"),
])
def test_refused(body, exc, match):
    with pytest.raises(exc, match=match):
        _run(body)


@pytest.mark.parametrize("decl,call,match", [
    ("void log(string s)", 'log("x");', "import 'log' takes a string"),
    ("string name(int i)", 'string n = name(1);', "import 'name' returns a string"),
])
def test_an_import_with_a_string_is_refused(decl, call, match):
    """A string crosses the platform boundary as a table index, which means
    nothing outside the model."""
    with pytest.raises(LoweringError, match=match):
        trace(_SRC.replace("component pss_top {", "component pss_top {\n    import api::*;")
              % (f"package api {{ import solve function {decl}; }}", call))
