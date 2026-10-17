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
    # 7.2.1 c: a width is a constant expression, folded (I13: any width but
    # a literal was 32 bits)
    "bit[k::M] a;": "bit[2]",
    "int[k::M * 3 + 1] a;": "int[7]",
    "bit[(int)e_e::C] a;": "bit[7]",
    "bit[(bit[2])k::M] a;": "bit[2]",
}


@pytest.mark.parametrize("decl", list(ROWS))
def test_field_type(tmp_path, decl):
    assert shape(_fields(tmp_path, "  " + decl)["a"].datatype) == ROWS[decl]


def test_a_width_from_a_component_constant(tmp_path):
    f = _fields(tmp_path, "  static const int W = 5;\n  bit[W] a;")
    assert shape(f["a"].datatype) == "bit[5]"


# (`bit[0]` and a constant width that folds to 0 or less are pssparser's
# PSS138 now, ruling R19, and never reach ast2ir: see
# test_a_non_positive_constant_width_is_a_front_end_error.)
@pytest.mark.parametrize("decl, msg", [
    ("int n = 3; bit[n] a;", "does not fold"),
])
def test_a_width_that_is_not_a_positive_constant_is_refused(tmp_path, decl,
                                                           msg):
    """Never a silent `bit[32]`: the error has the declaration's line."""
    ctx = translate(tmp_path, _PRE + "component pss_top {\n  " + decl
                    + "\n}\n")
    errs = [e for e in ctx.errors if "width" in str(e)]
    assert len(errs) == 1 and msg in str(errs[0]), ctx.errors
    assert str(errs[0]).startswith("line "), errs


@pytest.mark.parametrize("decl", ["bit[0] a;", "bit[k::M - 3] a;"])
def test_a_non_positive_constant_width_is_a_front_end_error(tmp_path, decl):
    from pssc.driver import CompileError
    with pytest.raises(CompileError) as ei:
        translate(tmp_path, _PRE + "component pss_top {\n  " + decl + "\n}\n")
    assert any("is not positive (LRM 7.2.1 c, ruling R19)" in str(e)
               for e in ei.value.errors), ei.value.errors


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


def test_each_template_specialization_is_its_own_type(tmp_path):
    """`s_s<4>` and `s_s<8>` are two types (I15: both were the generic
    `s_s` with a 32-bit field), named with their arguments appended (user
    ruling): a constant argument by its value, an enum item by its name, a
    default filled in."""
    ctx = translate(tmp_path, _PRE + """
struct s_s<int N = 2> { bit[N] f; }
struct w_s<int W = 8, int V = W * 2> { bit[W] x; bit[V] y; }
struct q_s<e_e E = A> { bit[4] g; }
component pss_top {
  s_s<4> a; s_s<8> b; s_s<k::M + 2> c; s_s<> d;
  w_s<3> e; q_s<C> f; q_s<> g;
}
""")
    assert ctx.errors == [], ctx.errors
    got = {f.name: (f.datatype.name,
                    [(m.name, shape(m.datatype)) for m in f.datatype.fields])
           for f in ctx.type_map["pss_top"].fields}
    assert got == {
        "a": ("s_s_4", [("f", "bit[4]")]),
        "b": ("s_s_8", [("f", "bit[8]")]),
        "c": ("s_s_4", [("f", "bit[4]")]),
        "d": ("s_s_2", [("f", "bit[2]")]),
        "e": ("w_s_3_6", [("x", "bit[3]"), ("y", "bit[6]")]),
        "f": ("q_s_C", [("g", "bit[4]")]),
        "g": ("q_s_A", [("g", "bit[4]")]),
    }
    a = ctx.type_map["pss_top"].fields[0].datatype
    assert ctx.type_map["pss_top"].fields[2].datatype is a


@pytest.mark.parametrize("order", ["struct first", "struct last"])
def test_a_specialization_name_taken_by_a_struct_is_refused(tmp_path, order):
    tmpl = "struct s_s<int N = 2> { bit[N] f; }\n"
    user = "struct s_s_4 { int a; }\n"
    comp = "component pss_top { s_s<4> v; }\n"
    src = tmpl + (user + comp if order == "struct first" else comp + user)
    ctx = translate(tmp_path, src)
    assert len(ctx.errors) == 1 and "s_s_4" in str(ctx.errors[0]), ctx.errors
    assert str(ctx.errors[0]).startswith("line "), ctx.errors


@pytest.mark.parametrize("decl, value", [
    # 7.2.1 d: the smallest domain value when 0 is outside the domain ...
    ("bit[5] in [1, 2, 4] a;", 1),
    ("int in [-10..-5] a;", -10),
    ("bit[5] in [10..] a;", 10),
    ("int in [3, 8..9] a;", 3),
    # ... and the type's own default (no initializer) when 0 is in it.
    ("int in [-10..5] a;", None),
    ("bit[4] in [..7] a;", None),
    # 7.5.1 k: the first DECLARED item in the domain (C = 7 comes after B).
    ("e_e in [B..C] a;", ("e_e::B", 3)),
    ("e_e in [D, B] a;", ("e_e::B", 3)),
    # An initializer wins.
    ("bit[5] in [1, 2, 4] a = 4;", 4),
])
def test_a_domain_sets_the_default(tmp_path, decl, value):
    """W46: a domain-restricted variable without an initializer starts at the
    domain's default, a field as a local does."""
    f = _fields(tmp_path, "  " + decl)["a"]
    got = f.initial_value
    if value is None:
        assert got is None
    elif isinstance(value, tuple):
        assert (got.ref, got.value) == value
    else:
        assert got.value == value
