"""Data types reach the IR whole (LRM 7) -- plan P2.

Each row is a field declaration and the IR type ast2ir must give it,
rendered by `_ir.shape`. A type with no IR form is refused with its line;
`test_ir_kind_registry.py` holds the list of kinds.
"""
from __future__ import annotations

import pytest
import zuspec.ir.core as ir

from ._ir import shape, translate

_PRE = """enum e_e { A = 2, B, C = 7 }
extend enum e_e { D }
enum w_e : bit[4] { W0, W1 = 5, W2 }
struct p_s { bit[8] x; }
package k { static const int M = 2; typedef bit[8] byte_t; }
"""


def _fields(tmp_path, body, comp="pss_top"):
    ctx = translate(tmp_path, _PRE + f"component {comp} {{\n{body}\n}}\n")
    assert ctx.errors == [], ctx.errors
    return {f.name: f for f in ctx.type_map[comp].fields}


#: field declaration -> its type's shape.
ROWS = {
    # 7.3: width and sign; `bit` alone is one bit, `int` alone 32 signed.
    "bit a;": "bit[1]",
    "bit[1] a;": "bit[1]",
    "bit[33] a;": "bit[33]",
    "bit[64] a;": "bit[64]",
    "int a;": "int[32]",
    "int[7] a;": "int[7]",
    # 7.4: bool is one bit in the IR; a cast to it is `!= 0`
    # (test_ir_expressions.py).
    "bool a;": "bit[1]",
    "string a;": "String()",
    "chandle a;": "Chandle()",
    # 7.5: explicit values, the implicit ones after them, and `extend enum`
    # continuing from the last; a base type gives the width.
    "e_e a;": "Enum('e_e', {'A': 2, 'B': 3, 'C': 7, 'D': 8}, 0)",
    "w_e a;": "Enum('w_e', {'W0': 0, 'W1': 5, 'W2': 6}, 4)",
    # 7.9: both spellings of a fixed-size array; nested; of a struct
    "array<int, 3> a;": "Array(int[32], 3)",
    "int a[3];": "Array(int[32], 3)",
    "array<array<bit[8], 2>, 3> a;": "Array(Array(bit[8], 2), 3)",
    "list<int> a;": "List(int[32])",
    "map<int, bit[8]> a;": "Map(int[32], bit[8])",
    "set<int> a;": "Set(int[32])",
    # 7.11: a package typedef is the type it names
    "k::byte_t a;": "bit[8]",
}


@pytest.mark.parametrize("decl", list(ROWS))
def test_field_type(tmp_path, decl):
    assert shape(_fields(tmp_path, "  " + decl)["a"].datatype) == ROWS[decl]


def test_an_array_of_structs(tmp_path):
    t = _fields(tmp_path, "  array<p_s, 2> a;")["a"].datatype
    assert isinstance(t, ir.DataTypeArray) and t.size == 2
    assert t.element_type.name == "p_s"


@pytest.mark.parametrize("decl, size, ref", [
    pytest.param("static const int N = 4;\n  array<int, N> a;", 4,
                 "pss_top::N", id="component constant"),
    pytest.param("static const int N = 4;\n  int a[N];", 4, "pss_top::N",
                 id="component constant, C form"),
    pytest.param("array<int, k::M> a;", 2, "k::M", id="package constant"),
    pytest.param("static const int N = 4;\n  array<int, N + 1> a;", 5, None,
                 id="constant expression"),
])
def test_an_array_size_folds_from_its_constant(tmp_path, decl, size, ref):
    """A size naming a component's `static const` was `size=-1`: the fold
    looked the name up in the package constants only. It is now the
    constant the linker resolved."""
    t = _fields(tmp_path, "  " + decl)["a"].datatype
    assert (t.size, t.size_ref) == (size, ref)


def test_an_array_size_that_does_not_fold_is_refused(tmp_path):
    ctx = translate(tmp_path, _PRE + """component pss_top {
  int n;
  array<int, n> a;
}""")
    assert any(e.startswith("line ") and "size must be a constant" in e
               for e in ctx.errors), ctx.errors


def test_a_static_const_is_const(tmp_path):
    f = _fields(tmp_path, "  static const bit[64] O = 12;")["O"]
    assert f.is_const and shape(f.initial_value) == "12"


def test_struct_nesting_and_inheritance(tmp_path):
    ctx = translate(tmp_path, _PRE + """struct b_s { int x; }
struct d_s : b_s { int y; }
struct n_s { d_s d; array<b_s, 2> bs; }
component pss_top { n_s v; }""")
    assert ctx.errors == []
    n = ctx.type_map["pss_top"].fields[0].datatype
    assert [(f.name, type(f.datatype).__name__) for f in n.fields] == [
        ("d", "DataTypeStruct"), ("bs", "DataTypeArray")]
    d = n.fields[0].datatype
    # A struct keeps its OWN fields and names its base; the op-model
    # flattens it (`progseq_model.flatten_struct_bases`).
    assert [f.name for f in d.fields] == ["y"]
    assert shape(d.super) == "Ref('b_s')"


# 7.3.1, 7.5.1: a domain is a constraint on the field, emitted by its type.
# An open bound is the type's bound: `[6..]` was the single value 6, and
# `[..2]` vanished.
@pytest.mark.parametrize("decl, want", [
    ("rand bit[4] in [6..] a;", "Expr(In(self.a, RangeList([Range(6, 15)])))"),
    ("rand int in [..2, 5] a;",
     "Expr(In(self.a, RangeList([Range(-2147483648, 2), Range(5)])))"),
    ("rand bit[8] in [1, 3..4] a;",
     "Expr(In(self.a, RangeList([Range(1), Range(3, 4)])))"),
    ("rand e_e in [A..B] a;",
     "Expr(In(self.a, RangeList([Range(e_e::A=2, e_e::B=3)])))"),
    ("rand e_e in [B..] a;",
     "Expr(In(self.a, RangeList([Range(e_e::B=3, 8)])))"),
])
def test_a_domain_is_a_constraint(tmp_path, decl, want):
    ctx = translate(tmp_path, _PRE + f"struct s_s {{ {decl} }}\n")
    assert ctx.errors == [], ctx.errors
    s = ctx.type_map["s_s"]
    assert shape(s.fields[0].datatype) != "None"
    (c,) = [f for f in s.functions if f.metadata.get("_is_constraint")]
    assert [shape(x) for x in c.body] == [want]


@pytest.mark.parametrize("decl, what", [
    ("float32 a;", "field 'a' is floating point"),
    ("float64 a;", "field 'a' is floating point"),
    ("ref p_s a;", "field 'a' is a reference (`ref T`)"),
    ("typedef bit[8] b_t;", "a typedef in component 'pss_top' is not "
                            "supported yet"),
])
def test_a_type_with_no_ir_form_is_refused_with_its_line(tmp_path, decl,
                                                          what):
    """Each was "Failed to translate field type for a", with no line."""
    ctx = translate(tmp_path, _PRE + f"component pss_top {{\n  {decl}\n}}\n")
    # Line 8: the prelude `translate` writes, _PRE, then the component.
    assert any(e.startswith("line 8: ") and what in e for e in ctx.errors), \
        ctx.errors
