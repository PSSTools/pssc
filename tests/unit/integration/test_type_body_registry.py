"""Every element pssparser puts in a type body is translated, or is a located
error (O6).

Component, action and struct bodies each had a dispatch of their own, and each
ended in a debug log: an ``override`` block, a ``monitor``, an action's second
activity or an ``input`` added by ``extend action`` vanished from the model
without a word. They now share ``AstToIrTranslator._BODY_ELEMENTS``.

This test enumerates pssparser's ``ScopeChild`` classes BY INTROSPECTION and
requires each to have a row here -- a snippet that puts it in a body, and what
ast2ir must make of it -- or to be named as something pssparser never puts in
a type body. A new parser class fails ``test_every_scope_child_is_accounted_for``
by name, forcing a decision; a row for a class pssparser no longer has fails
too. The twin for activity statements is ``test_activity_registry.py``.
"""
import inspect
import os
import re
import tempfile

import pytest
from pssparser import ast as P

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core import activity as A

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_PRELUDE = """\
buffer b_b { rand int v; }
resource r_r { }
"""
#: The line a one-line snippet's element is on, after the prelude.
_LINE = _PRELUDE.count("\n") + 1


class Err:
    """The snippet must be refused with a located error matching *pattern*."""
    def __init__(self, pattern):
        self.pattern = pattern


def _comp(ctx):
    return ctx.type_map["pss_top"]


def _action(ctx):
    return ctx.type_map["pss_top::A"]


def _fields(t):
    return [f.name for f in t.fields]


def _exec(t, name):
    return [f for f in t.functions if (f.metadata or {}).get("exec_kind") == name]


# class name -> (source, the type whose body holds it, expectation). An
# expectation is a predicate over the translation context, or Err.
CASES = {
    # -- translated ----------------------------------------------------------
    "Field":             ("component pss_top { int x; }", "pss_top",
                          lambda c: "x" in _fields(_comp(c))),
    "ExecBlock":         ("component pss_top { exec init_down { } }", "pss_top",
                          lambda c: len(_exec(_comp(c), "init_down")) == 1),
    "ConstraintBlock":   ("component pss_top { action A { rand int x; "
                          "constraint c1 { x > 0; } } }", "A",
                          lambda c: any(f.name == "c1" for f in _action(c).functions)),
    "GenericConstraintDeclBool":
                         ("component pss_top { action A { rand int x; "
                          "constraint pos(int v) { v > 0; } } }", "A",
                          lambda c: True),
    "GenericConstraintDeclValue":
                         ("component pss_top { action A { rand int x; "
                          "constraint int plus1(int v) v + 1; } }", "A",
                          lambda c: True),
    "Covergroup":        ("component pss_top { action A { rand bit[4] v; "
                          "covergroup { cp: coverpoint v; } cg; } }", "A",
                          lambda c: len(_action(c).covergroups) == 1),
    "FunctionDefinition": ("component pss_top { function void f() { } }", "pss_top",
                           lambda c: any(f.name == "f" for f in _comp(c).functions)),
    "ExportFunction":    ("component pss_top { function void f() { } "
                          "export target function f; }", "pss_top",
                          lambda c: [e.function for e in c.exports] == ["f"]),
    "ExportAction":      ("component pss_top { action A { } export A(); }", "pss_top",
                          lambda c: c.export_actions == ["pss_top::A"]),
    "Action":            ("component pss_top { action A { } }", "pss_top",
                          lambda c: "pss_top::A" in c.type_map),
    "Struct":            ("component pss_top { struct in_s { int a; } }", "pss_top",
                          lambda c: any(k.endswith("in_s") for k in c.type_map)),
    "EnumDecl":          ("component pss_top { enum e_e { X } }", "pss_top",
                          lambda c: any(k.endswith("e_e") for k in c.type_map)),
    "FieldPool":         ("component pss_top { pool [2] r_r rp; }", "pss_top",
                          lambda c: [p.name for p in _comp(c).pools] == ["rp"]),
    "ComponentBind":     ("component pss_top { pool [2] r_r rp; bind rp *; }", "pss_top",
                          lambda c: len(_comp(c).pool_binds) == 1),
    "FieldRef":          ("component pss_top { action A { input b_b in_b; } }", "A",
                          lambda c: "in_b" in _fields(_action(c))),
    "FieldClaim":        ("component pss_top { action A { lock r_r r; } }", "A",
                          lambda c: "r" in _fields(_action(c))),
    "ActivityDecl":      ("component pss_top { action B { } action A { "
                          "activity { do B; } } }", "A",
                          lambda c: isinstance(_action(c).activity_ir,
                                               A.ActivitySequenceBlock)),
    # -- inert: nothing to lower -------------------------------------------
    "PackageImportStmt": ("package p { } component pss_top { import p::*; }", "pss_top",
                          lambda c: True),
    "FunctionPrototype": ("component pss_top { function void f(); }", "pss_top",
                          lambda c: True),
    "FieldCompRef":      ("component pss_top { action A { } }", "A",
                          lambda c: c.parent_comp_names["pss_top::A"] == "pss_top"),
    # -- refused, each with its line ------------------------------------------
    "OverrideDecl":      ("component pss_top { action A { } action B : A { } "
                          "override { type A with B; } }", "pss_top",
                          Err(r"an 'override' block in component 'pss_top'")),
    "Monitor":           ("component pss_top { action A { } monitor M { "
                          "activity { do A; } } }", "pss_top",
                          Err(r"a monitor in component 'pss_top'")),
    "CoverStmtInline":   ("component pss_top { action A { } cover { "
                          "activity { do A; } } }", "pss_top",
                          Err(r"a cover statement in component 'pss_top'")),
    "CoverStmtReference": ("component pss_top { action A { } monitor M { "
                           "activity { do A; } } cover M; }", "pss_top",
                           Err(r"a cover statement in component 'pss_top'")),
    "CovergroupType":    ("component pss_top { covergroup cg_t (int v) { "
                          "cp: coverpoint v; } }", "pss_top",
                          Err(r"a covergroup type in component 'pss_top'")),
    "CovergroupInstantiation":
                         ("component pss_top { covergroup cg_t (bit[4] x) { "
                          "cp: coverpoint x; } action A { rand bit[4] v; "
                          "cg_t cg(v); } }", "A",
                          Err(r"a covergroup instance in action 'pss_top::A'")),
    "ExecTargetTemplateBlock":
                         ('component pss_top { action A { '
                          'exec header C = """x"""; } }', "A",
                          Err(r"a target-template exec block in action 'pss_top::A'")),
    # Expanded at each call (LRM 11.7), so it is the activity that holds it.
    "SymbolDeclaration": ("component pss_top { action B { } action A { "
                          "symbol s { do B; } activity { s; } } }", "A",
                          lambda c: isinstance(_action(c).activity_ir.stmts[0].stmts[0],
                                               A.ActivityAnonTraversal)),
    "ActivitySchedulingConstraint":
                         ("component pss_top { action B { } action A { B b1; "
                          "B b2; activity { schedule { b1; b2; } } "
                          "constraint sequence {b1, b2}; } }", "A",
                          Err(r"a scheduling constraint in action 'pss_top::A'")),
    "FunctionImportType": ("component pss_top { static function void f(); "
                           "import target C function f; }", "pss_top",
                           Err(r"an 'import function' in component 'pss_top'")),
    "ImportClass":       ("component pss_top { import class cls_c { void f(); } }",
                          "pss_top", Err(r"an 'import class' in component 'pss_top'")),
    "TargetTemplateFunction":
                         ('component pss_top { target C function void f() '
                          '= """x"""; }', "pss_top",
                          Err(r"a target-template function in component 'pss_top'")),
    "ExtendType":        ("component pss_top { action A { } } extend component "
                          "pss_top { extend action A { rand int y; } }", "EXTEND",
                          Err(r"an 'extend' in component 'pss_top'")),
    "ExtendEnum":        ("enum e_e { X } component pss_top { extend enum e_e { Y } }",
                          "pss_top", Err(r"an 'extend enum' in component 'pss_top'")),
    "TypedefDeclaration": ("component pss_top { typedef bit[4] nib_t; }", "pss_top",
                           Err(r"a typedef in component 'pss_top'")),
}

#: Refused because ast2ir cannot translate the element in THIS kind of body,
#: though it translates it in another. Each is its own located error.
WRONG_BODY = [
    ("component pss_top { action B { } activity { do B; } }",
     r"an activity in component 'pss_top'"),
    ("component pss_top { action A { exec pre_body { } } }",
     r"'exec pre_body' in action 'pss_top::A'"),
    ("component pss_top { action A { exec run_start { } } }",
     r"'exec run_start' in action 'pss_top::A'"),
    ("struct t_s { int a; exec body { } } component pss_top { }",
     r"'exec body' in struct 't_s'"),
    ("struct t_s { int a; exec init_down { } } component pss_top { }",
     r"'exec init_down' in struct 't_s'"),
]

#: Classes pssparser never puts directly in a type body, and why. A family is
#: named by its prefix; the classes it covers that DO reach a body have rows
#: above and are excluded from the family.
NOT_A_BODY_ELEMENT_FAMILIES = {
    "Activity":    "an activity's contents (test_activity_registry.py)",
    "Monitor":     "a monitor's contents",
    "Procedural":  "a statement in an exec or function body",
    "Constraint":  "a constraint's contents",
    "Template":    "a template parameter, or a target template's text",
    "DataType":    "a type reference",
    "Symbol":      "a linker scope",
    "Covergroup":  "a covergroup's contents",
    "Coverpoint":  "a covergroup's contents",
    "Dist":        "a dist constraint's contents",
    "Annotation":  "an annotation, carried by the declaration it precedes",
    "PyImport":    "a package-level Python import",
    "Function":    "a function's signature or its parts",
    "Exec":        "an exec block's parts",
    "Enum":        "an enum's items",
    "Component":   "a bind's target and path",
    "GenericConstraint": "a generic constraint's parameter",
}
NOT_A_BODY_ELEMENT = {
    "Scope": "abstract base", "ScopeChild": "abstract base",
    "NamedScope": "abstract base", "NamedScopeChild": "abstract base",
    "TypeScope": "abstract base", "ScopeChildRef": "a reference, not a node",
    "GlobalScope": "a file", "PackageScope": "a package",
    "RootSymbolScope": "the linker's root",
    "Component": "pssparser rejects a component in a type body",
    "ActionFieldInitializer": "a traversal's `with` initializer",
    "Comment": "carried by the declaration it is attached to",
    "ActionHandleField": "declared in an activity block (LRM 11.8.2); in an "
                         "action body `B b1;` parses as a Field",
    "InstanceOverride": "an override block's contents",
    "TypeOverride": "an override block's contents",
    "OverrideStmt": "an override block's contents",
}


def _body_classes(root, owner):
    """The class names of *owner*'s body children, or of every `extend`'s
    when *owner* is "EXTEND"."""
    out = []

    def walk(node):
        for c in node.children():
            if c is None:
                continue
            try:
                name = c.getName().getId()
            except Exception:
                name = None
            if owner == "EXTEND" and isinstance(c, P.ExtendType) \
                    or isinstance(c, (P.Component, P.Action, P.Struct)) \
                    and name == owner:
                out.extend(type(k).__name__ for k in c.children() if k is not None)
            elif isinstance(c, (P.Component, P.PackageScope)):
                walk(c)
    for i in range(root.numUnits()):
        walk(root.getUnit(i))
    return out


def _translate(src):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, (_PRELUDE + src).encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        root = parser.link()
        return root, AstToIrTranslator().translate(root)
    finally:
        os.unlink(fname)


def _scope_child_classes():
    return sorted(n for n, c in vars(P).items()
                  if inspect.isclass(c) and issubclass(c, P.ScopeChild))


def _family(name):
    return next((f for f in NOT_A_BODY_ELEMENT_FAMILIES if name.startswith(f)), None)


def test_every_scope_child_is_accounted_for():
    missing = [n for n in _scope_child_classes()
               if n not in CASES and n not in NOT_A_BODY_ELEMENT
               and _family(n) is None]
    assert not missing, (
        f"pssparser classes with no decision: {missing}. Add a CASES row "
        f"(and a _BODY_ELEMENTS row if ast2ir translates it), or say why "
        f"it never reaches a type body")


def test_every_row_names_a_real_class():
    real = set(_scope_child_classes())
    assert not [n for n in list(CASES) + list(NOT_A_BODY_ELEMENT) if n not in real]


def test_every_translated_element_has_a_case():
    assert not [n for n in AstToIrTranslator._BODY_ELEMENTS if n not in CASES]


@pytest.mark.parametrize("name", sorted(CASES))
def test_body_element(name):
    src, owner, expect = CASES[name]
    root, ctx = _translate(src)
    assert name in _body_classes(root, owner), \
        f"the snippet does not put a {name} in {owner}'s body"
    if isinstance(expect, Err):
        assert any(e.startswith(f"line {_LINE}: ")
                   and re.search(expect.pattern, e) for e in ctx.errors), ctx.errors
    else:
        assert not ctx.errors, ctx.errors
        assert expect(ctx)


@pytest.mark.parametrize("src,pattern", WRONG_BODY)
def test_element_in_a_body_that_cannot_hold_it(src, pattern):
    _, ctx = _translate(src)
    assert any(e.startswith(f"line {_LINE}: ") and re.search(pattern, e)
               for e in ctx.errors), ctx.errors
