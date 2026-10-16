"""Every expression and data type pssparser builds is translated, or refused
with its line -- plan P2 (§4.4 of docs/design/construct-test-audit/00-plan.md).

`_translate_expression` and `_translate_data_type` returned None with no
error for a kind they did not know, and a None operand, parameter or field is
not an error anywhere downstream: it is a piece of the model that quietly
disappears (I1-I4). Both now refuse whatever they return None for without an
error of their own. This test enumerates pssparser's ``Expr`` and
``DataType`` classes BY INTROSPECTION and requires a row for each, so a new
class fails here by name. The twins are `test_activity_registry.py`,
`test_type_body_registry.py` and `test_constraint_stmt_registry.py`.

A row's PSS is checked to REACH the translator as that class: a row whose
snippet the parser spells some other way would pass without testing anything.
"""
from __future__ import annotations

import inspect
import re

import pytest
from pssparser import ast as P

from pssc import ast2ir

from ._ir import in_function, translate

_PRE = """enum m_e { M0, M1 }
struct p_s { bit[8] x; }
package q {
  static const int K = 3;
  function int pf(int a) { return a; }
}
"""
_DECLS = """  array<int, 3> w;
  map<int, int> mp;
  string s;
  int fld;"""


def _body(stmt: str, decls: str = _DECLS) -> str:
    return _PRE + in_function(
        decls, "    int x; bool c; string z; p_s ps;\n    " + stmt,
        params="int n, bit[8] b")


def _component(body: str) -> str:
    return _PRE + "component pss_top {\n" + body + "\n}\n"


class Refused:
    """Refused with its line, the message matching *pattern*."""
    def __init__(self, pattern: str):
        self.pattern = pattern


#: Expression class -> (model, Refused or None). None: translated cleanly.
EXPRESSIONS = {
    "ExprSignedNumber":        (_body("x = 4'sb1010;"), None),
    "ExprUnsignedNumber":      (_body("x = 16;"), None),
    "ExprString":              (_body('z = "a";'), None),
    "ExprTemplateString":      (_body('z = """a{{n}}""";'),
                                Refused(r"triple-quoted string with mustache")),
    "ExprBool":                (_body("c = true;"), None),
    "ExprBin":                 (_body("x = n + 1;"), None),
    "ExprUnary":               (_body("x = -n;"), None),
    "ExprCond":                (_body("x = c ? 1 : 2;"), None),
    "ExprCast":                (_body("x = (int[8])b;"), None),
    "ExprIn":                  (_body("c = n in [1, 3..5];"), None),
    "ExprNull":                (_body("c = s == null;"), None),
    "ExprRefPathContext":      (_body("x = fld;"), None),
    "ExprRefPathSuper":        (_PRE + """component base_c { int a; }
component pss_top : base_c {
  function void f() { int x = super.a; }
}""", None),
    "ExprRefPathStatic":       (_body("x = q::K;"), None),
    "ExprRefPathStaticRooted": (_body("x = q::pf(2);"), None),
    "ExprSliceRange":          (_body("z = s[0..1];"), None),
    "ExprAggrList":            (_body("w = {1, 2, 3};"), None),
    "ExprAggrMap":             (_body("mp = {1: 2};"), None),
    "ExprAggrStruct":          (_body("ps = {.x = 1};"), None),
    "ExprAggrEmpty":           (_body("mp = {};"), None),
    "ExprMemberCall":          (_body('x = "abc".size();'), None),
    "ExprFloatLiteral":        (_body("x = 1.5;"),
                                Refused(r"this expression is floating point")),
    "ExprCompileHas":          (_component(
        "  int a;\n  function bool f() { return compile has(a); }"),
                                Refused(r"`compile has`")),
}

#: Not an expression the translator meets on its own, and why.
NOT_AN_EXPRESSION = {
    "Expr": "abstract base",
    "ExprNumber": "abstract base of the integer literals",
    "ExprBitSlice": "a reference's part select, read by the reference "
                    "(`_apply_ref_bit_slice`; test_ir_expressions.py)",
    "ExprAggrLiteral": "abstract base of the aggregate literals",
    "ExprRefPath": "abstract base of the reference paths",
    "ExprDomainOpenRangeList": "a type's domain (test_ir_types.py)",
    "ExprDomainOpenRangeValue": "one range of a domain",
    "ExprOpenRangeList": "the range list of `in` or a match arm, read there",
    "ExprOpenRangeValue": "one range of an open range list",
    "ExprHierarchicalId": "the path inside an ExprRefPathContext",
    "ExprMemberPathElem": "one element of a path or member call",
    "ExprId": "a name, read by its declaration or reference",
    "ExprRefName": "a struct literal's member name",
    "MethodParameterList": "a call's arguments, read by the call",
    "TypeIdentifier": "a type's name, read by DataTypeUserDefined",
    "TypeIdentifierElem": "one element of a type's name",
}

#: Data type class -> (model, Refused or None).
DATA_TYPES = {
    "DataTypeInt":         (_component("  bit[33] a;"), None),
    "DataTypeBool":        (_component("  bool a;"), None),
    "DataTypeString":      (_component("  string a;"), None),
    "DataTypeChandle":     (_component("  chandle a;"), None),
    "DataTypeUserDefined": (_component("  p_s a;"), None),
    "DataTypeEnum":        (_PRE + "struct d_s { rand m_e in [M0] e; }\n",
                            None),
    "DataTypeFloat":       (_component("  float32 a;"),
                            Refused(r"field 'a' is floating point")),
    "DataTypeRef":         (_component("  ref p_s a;"),
                            Refused(r"field 'a' is a reference \(`ref T`\)")),
    "DataTypePyObj":       (_component("  pyobj a;"),
                            Refused(r"field 'a' is a 'DataTypePyObj'")),
}

NOT_A_DATA_TYPE = {
    "DataType": "abstract base",
}


def _classes(base):
    return sorted(n for n, c in vars(P).items()
                  if inspect.isclass(c) and issubclass(c, base))


@pytest.mark.parametrize("base, rows, nots", [
    (P.Expr, EXPRESSIONS, NOT_AN_EXPRESSION),
    (P.DataType, DATA_TYPES, NOT_A_DATA_TYPE),
], ids=["Expr", "DataType"])
def test_every_kind_is_accounted_for(base, rows, nots):
    classes = _classes(base)
    missing = [n for n in classes if n not in rows and n not in nots]
    assert not missing, f"{base.__name__} classes with no decision: {missing}"
    stale = [n for n in list(rows) + list(nots) if n not in classes]
    assert not stale, stale


def _run(monkeypatch, tmp_path, method, src):
    """Translate *src*; the classes *method* was called with, and the errors."""
    seen = set()
    orig = getattr(ast2ir.AstToIrTranslator, method)

    def spy(self, ctx, node):
        seen.add(type(node).__name__)
        return orig(self, ctx, node)

    monkeypatch.setattr(ast2ir.AstToIrTranslator, method, spy)
    ctx = translate(tmp_path, src)
    return seen, ctx.errors


def _check(seen, errors, name, refused):
    assert name in seen, f"the row's PSS never reached {name}: {sorted(seen)}"
    if refused is None:
        assert errors == [], errors
    else:
        assert any(re.search(refused.pattern, e) and e.startswith("line ")
                   for e in errors), errors


@pytest.mark.parametrize("name", sorted(EXPRESSIONS))
def test_expression_kind(monkeypatch, tmp_path, name):
    src, refused = EXPRESSIONS[name]
    seen, errors = _run(monkeypatch, tmp_path, "_translate_expression_kind",
                        src)
    _check(seen, errors, name, refused)


@pytest.mark.parametrize("name", sorted(DATA_TYPES))
def test_data_type_kind(monkeypatch, tmp_path, name):
    src, refused = DATA_TYPES[name]
    seen, errors = _run(monkeypatch, tmp_path, "_translate_data_type_kind",
                        src)
    _check(seen, errors, name, refused)
