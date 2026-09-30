"""Every activity node pssparser builds is translated, or is a located error.

The activity translator used to end in ``return None`` behind a debug log, so
a node it did not know -- a new pssparser node, or one whose shape changed --
vanished from the scenario without a word. This test enumerates pssparser's
activity node classes BY INTROSPECTION and requires a row for each: a PSS
snippet that produces it, and what ast2ir must make of it. A new parser node
fails ``test_every_activity_node_has_a_row`` by name, forcing a decision; a
row for a class pssparser no longer has fails too.
"""
import inspect
import os
import re
import tempfile

import pytest
from pssparser import ast as P

import pssc
from pssc.ast2ir import AstToIrContext, AstToIrTranslator
from zuspec.ir.core import activity as A

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

# Abstract bases: never built on their own.
_ABSTRACT = {"ActivityStmt", "ActivityLabeledStmt", "ActivityLabeledScope",
             "ActivityJoinSpec"}
_ROOTS = ("ActivityStmt", "ActivityLabeledScope", "ActivityJoinSpec",
          "ActivitySchedulingConstraint", "ActivitySelectBranch",
          "ActivityMatchChoice", "ActivityDecl")


def _activity_node_classes():
    roots = tuple(getattr(P, r) for r in _ROOTS)
    return sorted(n for n, c in vars(P).items()
                  if inspect.isclass(c) and issubclass(c, roots)
                  and n not in _ABSTRACT and not n.startswith("Monitor"))


class Err:
    """The snippet must be refused with a located error matching *pattern*."""
    def __init__(self, pattern):
        self.pattern = pattern


def _first(cls):
    return lambda stmts: isinstance(stmts[0], cls)


def _join(kind):
    return lambda stmts: stmts[0].join_spec.kind is kind


# name -> (activity body, [action-level declarations], expectation)
CASES = {
    "ActivityDecl":                  ("do B;", "", _first(A.ActivityAnonTraversal)),
    "ActivityActionHandleTraversal": ("b1;", "", _first(A.ActivityTraversal)),
    "ActivityActionTypeTraversal":   ("do B;", "", _first(A.ActivityAnonTraversal)),
    "ActivityAtomicBlock":           ("atomic { do B; }", "",
                                      lambda s: isinstance(s[0], A.ActivityAtomic)
                                      and len(s[0].stmts) == 1),
    "ActivityBindStmt":              ("p; c; bind p.o c.i;", "",
                                      lambda s: isinstance(s[2], A.ActivityBind)),
    "ActivityConstraint":            ("b1; constraint { n > 1; }", "",
                                      lambda s: isinstance(s[1], A.ActivityConstraint)),
    "ActivityForeach":               ("foreach (v: vals) do B;", "",
                                      _first(A.ActivityForeach)),
    "ActivityIfElse":                ("if (n > 1) do B;", "", _first(A.ActivityIfElse)),
    "ActivityJoinSpecBranch":        ("parallel join_branch(L1) { L1: do B; do B; }",
                                      "", _join(A.JoinKind.BRANCH)),
    "ActivityJoinSpecFirst":         ("parallel join_first(1) { do B; }", "",
                                      _join(A.JoinKind.FIRST)),
    "ActivityJoinSpecNone":          ("parallel join_none { do B; }", "",
                                      _join(A.JoinKind.NONE)),
    "ActivityJoinSpecSelect":        ("parallel join_select(1) { do B; }", "",
                                      _join(A.JoinKind.SELECT)),
    "ActivityMatch":                 ("match (n) { [0]: do B; default: do B; }", "",
                                      _first(A.ActivityMatch)),
    "ActivityMatchChoice":           ("match (n) { [0]: do B; default: do B; }", "",
                                      lambda s: len(s[0].cases) == 2
                                      and all(len(c.body) == 1 for c in s[0].cases)),
    "ActivityParallel":              ("parallel { do B; }", "", _first(A.ActivityParallel)),
    "ActivityRepeatCount":           ("repeat (2) do B;", "",
                                      lambda s: isinstance(s[0], A.ActivityRepeat)
                                      and len(s[0].body) == 1),
    "ActivityRepeatWhile":           ("repeat do B; while (n < 2);", "",
                                      lambda s: isinstance(s[0], A.ActivityDoWhile)
                                      and len(s[0].body) == 1),
    "ActivityReplicate":             ("replicate (2) do B;", "",
                                      _first(A.ActivityReplicate)),
    "ActivitySchedule":              ("schedule { b1; b2; }", "",
                                      _first(A.ActivitySchedule)),
    "ActivitySchedulingConstraint":  ("schedule { b1; b2; constraint sequence {b1, b2}; }",
                                      "", lambda s: isinstance(
                                          s[0].stmts[2], A.ActivitySchedulingConstraint)),
    "ActivitySelect":                ("select { do B; do B; }", "", _first(A.ActivitySelect)),
    "ActivitySelectBranch":          ("select { (n > 1) [3]: do B; do B; }", "",
                                      lambda s: len(s[0].branches) == 2
                                      and s[0].branches[0].guard is not None
                                      and s[0].branches[0].weight is not None),
    "ActivitySequence":              ("L1: sequence { do B; }", "",
                                      lambda s: isinstance(s[0], A.ActivitySequenceBlock)
                                      and s[0].label == "L1"),
    "ActivitySuper":                 (None, "", _first(A.ActivitySuper)),
    "ActivitySymbolCall":            ("S1();", "symbol S1 { do B; }",
                                      Err(r"line \d+: activity symbols are not supported")),
}

_MODEL = """\
component pss_top {
    stream s_t { rand bit[4] v; }
    action P { output s_t o; }
    action C { input s_t i; }
    action B { }
    action T {
        rand bit[4] n;
        rand bit[4] vals[4];
        B b1, b2;
        P p;
        C c;
        %s
        activity {
            %s
        }
    }
    action U : T {
        activity { super; }
    }
}
"""


def _translate(body, decls):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, (_MODEL % (decls, body or "do B;")).encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)


def test_every_activity_node_has_a_row():
    classes = set(_activity_node_classes())
    assert classes, "found no activity node classes in pssparser.ast"
    missing = sorted(classes - set(CASES))
    stale = sorted(set(CASES) - classes)
    assert not missing, (
        "pssparser has activity node(s) with no row here: %s. Decide what "
        "ast2ir does with each -- translate it, or refuse it with a located "
        "error -- and add the row." % ", ".join(missing))
    assert not stale, "rows for classes pssparser no longer has: %s" % ", ".join(stale)


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_activity_node_translates(name):
    body, decls, expect = CASES[name]
    ctx = _translate(body, decls)
    if isinstance(expect, Err):
        assert any(re.search(expect.pattern, e) for e in ctx.errors), ctx.errors
        return
    assert not ctx.errors, ctx.errors
    action = "pss_top::U" if name == "ActivitySuper" else "pss_top::T"
    stmts = ctx.type_map[action].activity_ir.stmts
    assert stmts and expect(stmts), stmts


def test_no_activity_fallthrough():
    """A node the translator does not know is an error naming it -- the old
    ``return None`` path is gone."""
    class NotAnActivityNode:
        pass
    ctx = AstToIrContext()
    assert AstToIrTranslator()._translate_activity_stmt(ctx, NotAnActivityNode()) is None
    assert any("NotAnActivityNode is not translated" in e for e in ctx.errors), ctx.errors
