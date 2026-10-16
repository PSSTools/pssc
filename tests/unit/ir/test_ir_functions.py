"""Function signatures and calls reach the IR whole (LRM 20, 22) -- plan P2.

A signature is rendered as ``Arguments([...], vararg, [defaults])`` by
`_ir.shape`. What `ir.Arg` has no field for, the function carries in its
metadata: ``const_params`` and ``param_dirs``.
"""
from __future__ import annotations

import pytest

from ._ir import function, shape, translate

_PRE = """struct p_s { int x; }
package q {
  function int pf(int a) { return a; }
  function void fetch(output int v, inout int w, input int c, int d);
  import target function fetch;
  import solve function int fmt(string f, int ... args);
  import target function void put(int a, bit[8] b = 3);
}
"""


def _ctx(tmp_path, body):
    ctx = translate(tmp_path, _PRE + "component pss_top {\n" + body + "\n}\n")
    assert ctx.errors == [], ctx.errors
    return ctx


def _sig(fn):
    flags = [k for k in ("is_invariant", "is_solve", "is_target", "is_import")
             if getattr(fn, k)]
    return f"{shape(fn.args)} -> {shape(fn.returns)} {flags}"


ROWS = {
    "function int h(int a, const int b, bit[8] c = 3) { return a; }":
        "Arguments([Arg('a', int[32]), Arg('b', int[32]), Arg('c', bit[8])], "
        "[3]) -> int[32] []",
    "function void h(p_s s, array<int, 2> a) { s.x = a[0]; }":
        None,           # struct and array parameters: checked below
    "pure function int h(int a) { return a; }":
        "Arguments([Arg('a', int[32])]) -> int[32] ['is_invariant']",
    "solve function void h() { }": "Arguments() -> None ['is_solve']",
    "target function bool h() { return true; }":
        "Arguments() -> bit[1] ['is_target']",
}


@pytest.mark.parametrize("decl", [d for d, w in ROWS.items() if w])
def test_signature(tmp_path, decl):
    assert _sig(function(_ctx(tmp_path, "  " + decl), "h")) == ROWS[decl]


def test_const_parameters_are_named(tmp_path):
    fn = function(_ctx(tmp_path, "  function int h(int a, const int b) "
                                 "{ return a; }"), "h")
    assert fn.metadata["const_params"] == ("b",)


def test_struct_and_array_parameters(tmp_path):
    fn = function(_ctx(tmp_path, "  " + next(iter(
        d for d, w in ROWS.items() if w is None))), "h")
    assert [(a.arg, type(a.annotation).__name__) for a in fn.args.args] == [
        ("s", "DataTypeStruct"), ("a", "DataTypeArray")]
    assert [shape(s) for s in fn.body] == [
        "Assign([self.s.x], Subscript(self.a, 0))"]


def _import(ctx, name):
    return next(f for f in ctx.import_functions if f.name == name)


def test_an_import_by_prototype_is_recorded(tmp_path):
    """`function ... fetch(...); import target function fetch;` (20.4.1):
    both declarations were dropped, and a call to `fetch` named nothing."""
    fn = _import(_ctx(tmp_path, ""), "fetch")
    assert _sig(fn) == ("Arguments([Arg('v', int[32]), Arg('w', int[32]), "
                        "Arg('c', int[32]), Arg('d', int[32])]) -> None "
                        "['is_target', 'is_import']")
    assert fn.metadata["qualified_name"] == "q::fetch"


def test_parameter_direction_is_recorded(tmp_path):
    """`output`/`inout` was dropped, so every target passed the argument by
    value and lost the callee's write (W18). The op-model gate refuses the
    call (test_m_refusals.py "an import's output parameter")."""
    fn = _import(_ctx(tmp_path, ""), "fetch")
    assert fn.metadata["param_dirs"] == {"v": "output", "w": "inout"}


def test_an_imports_varargs_and_defaults_are_kept(tmp_path):
    """The variadic parameter became an ordinary one, and a default was
    dropped (20.2.4, 20.2.5)."""
    ctx = _ctx(tmp_path, "")
    assert _sig(_import(ctx, "fmt")) == (
        "Arguments([Arg('f', String())], Arg('args', int[32])) -> int[32] "
        "['is_solve', 'is_import']")
    assert shape(_import(ctx, "put").args) == (
        "Arguments([Arg('a', int[32]), Arg('b', bit[8])], [3])")


def test_an_imports_parameter_is_never_dropped(tmp_path):
    ctx = translate(tmp_path, """package q {
  import target function void k(int a, float32 f);
}
component pss_top { }""")
    assert any(e.startswith("line 3: ") and "parameter 'f' of 'k' is "
               "floating point" in e for e in ctx.errors), ctx.errors


@pytest.mark.parametrize("stmt, want", [
    # A package function by its qualified name; the linker says which.
    ("int x = q::pf(1);", "AnnAssign(x, int[32], Call(RefUnresolved('q::pf'), "
                          "[1]))"),
    # An import is the platform's, named as declared to it.
    ("int v; int w; q::fetch(v, w, 1, 2);",
     "Expr(Call(RefUnresolved('fetch'), [v, w, 1, 2]))"),
    ("(void)g();", "Expr(Call(self.g))"),
    # Recursion is a call like any other (20.2).
    ("int y = r(3);", "AnnAssign(y, int[32], Call(self.r, [3]))"),
])
def test_calls(tmp_path, stmt, want):
    ctx = _ctx(tmp_path, """  function int g() { return 1; }
  function int r(int n) { if (n == 0) { return 1; } return n * r(n - 1); }
  function void f() { """ + stmt + " }")
    assert shape(function(ctx, "f").body[-1]) == want


def test_a_static_function_by_its_type_is_refused_with_its_line(tmp_path):
    """`u_c::sg(2)` translated to None: the local was declared with no
    initializer, silently."""
    ctx = translate(tmp_path, """component u_c {
  static function int sg(int a) { return a; }
}
component pss_top {
  function void f() { int x = u_c::sg(2); }
}""")
    assert any(e.startswith("line 6: ") for e in ctx.errors), ctx.errors
