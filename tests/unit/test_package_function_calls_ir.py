"""A call to a package-scope function reaches the IR as the linker resolved it.

pssparser's linker resolves every call, shadowing included. The translator
keeps that answer in the IR's form: a call resolved to a package-scope function
is `ExprCall(func=ExprRefUnresolved(<qualified name>))`, and a call to a
component's own function stays a member of `self`. No backend re-derives which
function a name means (`ast2ir._package_function_at`, `targets/pkg_functions.py`).
"""
from __future__ import annotations

import pssc
from pssc.ast2ir import AstToIrTranslator

_MODEL = """
import std_pkg::*;
import target function void plat_go(int x);

function int twice(int x) { return 2 * x; }
function int quad(int x) { return twice(twice(x)); }
package util {
  function int neg(int x) { return -x; }
}

component pss_top {
  int r;
  function int twice(int x) { return 100 + x; }
  target function void g() {
    message(NONE, "hi");
    plat_go(1);
    r = twice(1) + quad(2) + util::neg(3);
  }
}
"""


def _ctx(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(_MODEL)
    parser = pssc.Parser()
    parser.parse([str(p)])
    ctx = AstToIrTranslator().translate(parser.link())
    assert not ctx.errors, ctx.errors
    return ctx


def _callees(node):
    """`[(form, name)]` of every call under ``node``, in order."""
    import dataclasses as dc
    out = []

    def walk(n):
        if isinstance(n, (list, tuple)):
            for c in n:
                walk(c)
            return
        if not dc.is_dataclass(n) or isinstance(n, type):
            return
        if type(n).__name__ == "ExprCall":
            f = n.func
            if type(f).__name__ == "ExprRefUnresolved":
                out.append(("pkg", f.name))
            elif type(f).__name__ == "ExprAttribute":
                out.append(("self", f.attr))
        for fld in dc.fields(n):
            c = getattr(n, fld.name)
            if c is not n:
                walk(c)

    walk(node)
    return out


def test_calls_carry_the_linkers_resolution(tmp_path):
    ctx = _ctx(tmp_path)
    g = next(f for f in ctx.type_map["pss_top"].functions if f.name == "g")
    assert _callees(g.body) == [
        ("self", "message"),        # a std_pkg built-in: no body, unchanged
        ("self", "plat_go"),        # an import: no body, unchanged
        ("self", "twice"),          # the component's own, which shadows
        ("pkg", "quad"),
        ("pkg", "util::neg"),       # qualified, by the resolved declaration
    ]


def test_inside_a_package_function_the_package_function_is_called(tmp_path):
    """The component's `twice` is not in scope there (LRM 22.2)."""
    ctx = _ctx(tmp_path)
    assert _callees(ctx.functions["quad"].body) == [("pkg", "twice"),
                                                    ("pkg", "twice")]


def test_the_target_qualifier_is_recorded_on_every_function(tmp_path):
    ctx = _ctx(tmp_path)
    comp = {f.name: f.is_target for f in ctx.type_map["pss_top"].functions}
    assert comp == {"twice": False, "g": True}
    assert ctx.functions["quad"].is_target is False
