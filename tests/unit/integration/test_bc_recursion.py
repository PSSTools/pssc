"""Recursive functions on bc (bc procedural gaps G4, B-D5).

A native function is inlined at each call; a call to a function already being
inlined is a CALL of its *called form*, a coroutine of its own with its
arguments staged by ARG and read by LD_ARG. The callee runs at once and the
caller resumes as soon as it returns, so a call is no scheduling point, as an
inlined body is none.
"""
import pytest

from zuspec.be.bc.interp.vm import VMError
from zuspec.be.bc.lower.errors import LoweringError
from zuspec.be.bc.model import Op

from .test_activity_bc_runs import _lower, trace

_SRC = """\
import std_pkg::*;
function int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
function bool is_even(int n) { if (n == 0) { return true; } return is_odd(n - 1); }
function bool is_odd(int n) { if (n == 0) { return false; } return is_even(n - 1); }
function void down(int n) { message(NONE, "d%%d", n); if (n > 0) { down(n - 1); } }
function int fib(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }
function int deep(int n) { if (n == 0) { return 0; } return 1 + deep(n - 1); }
%s
component pss_top {
    int base = 100;
    function int tri(int n) { if (n == 0) { return base; } return n + tri(n - 1); }
    action P { exec body { %s } }
    action Q { exec body { message(NONE, "q"); } }
    action T { %s }
}
"""


def _run(body: str, decls: str = "", p: str = ""):
    return trace(_SRC % (decls, p, "exec body { %s }" % body))


@pytest.mark.parametrize("expr,want", [
    ("fact(1)", "1"), ("fact(5)", "120"), ("fact(10)", "3628800"),
    ("fib(10)", "55"), ("comp.tri(4)", "110")])
def test_values(expr, want):
    assert _run(f'message(NONE, "%d", {expr});') == [want]


def test_mutual_recursion():
    assert _run('message(NONE, "%n %n %n", is_even(10), is_odd(7), is_even(3));') \
        == ["true true false"]


def test_a_void_recursion_runs_its_effects_in_order():
    assert _run("down(3);") == ["d3", "d2", "d1", "d0"]


def test_a_call_is_no_scheduling_point():
    """P recurses while Q is ready; with no yield, P finishes first, as it
    would with the body inlined."""
    assert trace(_SRC % ("", "down(2);", "activity { parallel { do P; do Q; } }")) \
        == ["d2", "d1", "d0", "q"]


def test_a_non_recursive_call_is_still_inlined():
    model = _lower(_SRC % ("function int twice(int n) { return 2 * n; }", "",
                           'exec body { message(NONE, "%d", twice(3)); }'), "pss_top::T")
    assert not any(i.op == Op.CALL for c in model.coros for i in c.code)


def test_recursion_too_deep_is_a_run_time_error():
    assert _run('message(NONE, "%d", deep(1000));') == ["1000"]
    with pytest.raises(VMError, match="nested deeper than 1024"):
        _run('message(NONE, "%d", deep(2000));')


def test_a_struct_parameter_on_a_recursive_function_is_refused():
    decls = """
struct s_t { int v; }
function int sum_s(s_t s, int n) { if (n == 0) { return s.v; } return sum_s(s, n - 1); }
"""
    with pytest.raises(LoweringError, match="struct parameter 's'"):
        _run("s_t s0; int x = sum_s(s0, 2);", decls=decls)
