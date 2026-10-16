"""Core-library calls in a body reach the IR whole (LRM 21) -- plan P2.

The front end roots a core-library call at ``self`` as it does a
component's own function; which it is, is decided downstream by name and
resolution (`call_legality`). What this pins is that every argument
arrives, in order, with its value.
"""
from __future__ import annotations

import pytest

from ._ir import body, in_function, shape

ROWS = {
    'message(LOW, "v=%d", n);':
        "Expr(Call(self.message, [std_pkg::message_verbosity_e::LOW=1, "
        "'v=%d', self.n]))",
    'error("bad %d", n);': "Expr(Call(self.error, ['bad %d', self.n]))",
    'fatal(2, "worse %d", n);':
        "Expr(Call(self.fatal, [2, 'worse %d', self.n]))",
    "int r = urandom_range(1, 4);":
        "AnnAssign(r, int[32], Call(self.urandom_range, [1, 4]))",
    "bit[32] q = urandom();": "AnnAssign(q, bit[32], Call(self.urandom))",
}


@pytest.mark.parametrize("stmt", list(ROWS))
def test_target_time_call(tmp_path, stmt):
    (got,) = body(tmp_path, in_function("", "    " + stmt, params="int n"))
    assert shape(got) == ROWS[stmt]


def test_solve_time_print_and_format(tmp_path):
    """`print` and `format` are solve functions (21.1.2)."""
    src = """component pss_top {
  solve function void f(int n) {
    print("v=%d", n);
    string t = format("v=%d", n);
  }
}"""
    assert [shape(s) for s in body(tmp_path, src)] == [
        "Expr(Call(self.print, ['v=%d', self.n]))",
        "AnnAssign(t, String(), Call(self.format, ['v=%d', self.n]))"]


def test_a_string_literal_escape_is_processed(tmp_path):
    """A string literal's value has its escapes decoded (4.7); they reached
    every target as a backslash and a letter (W20)."""
    (got,) = body(tmp_path, in_function(
        "", '    message(LOW, "a\\tb\\n\\"q\\"\\\\");'))
    assert got.expr.args[1].value == 'a\tb\n"q"\\'
