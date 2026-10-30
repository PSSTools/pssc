"""Every node of a body is one the target renders, found before emission.

An emitter that meets an IR kind it has no rendering for raised an internal
error ("_BodyEmitter defines no expr_list()"), from the middle of emission,
with no location -- a struct literal in a `?:` arm did that on all four
op-model targets. This pass asks the question first, of every body the
target renders (`validate_calls.rendered_bodies`), and refuses each node it
cannot render with the node's location (plan P5.1,
docs/design/construct-test-audit/00-plan.md).

A node kind is one of three things:

* **rendered** by the target's body emitter: a hook named after it
  (`BodyWalker.hooks`), or C++'s cascade (`KINDS`);
* **a part** of its parent (`PARTS`): a `match` choice, a range, a slice, a
  struct literal's member, `self` -- the parent's rendering reads it, and
  its own children are checked;
* **an aggregate literal** (`AGGREGATES`), which has no type of its own
  (LRM 4.6.3, 8.4.2): rendered only where its place gives it one
  (`AGGREGATE_PLACES`).

Anything else is refused. `tests/progseq/test_emitter_kind_registry.py`
holds every IR kind to a row of this account, per target, so a kind added
to the IR fails by name until someone says how each target takes it.
"""
from __future__ import annotations

import dataclasses as dc
from typing import FrozenSet, Iterable, List, Mapping, Optional

from ..diag import at
from .progseq_model import _dt_name
from .ir_walk import NON_CHILD_FIELDS

__all__ = ["AGGREGATES", "AGGREGATE_PLACES", "PARTS", "emitter_kinds",
           "refusals"]

#: Kinds their parent renders: ``{kind: the parent that reads it}``.
PARTS = {
    "StmtMatchCase": "a `match` statement",
    "StmtIfClause": "an `else if` chain",
    "ExprRange": "an `in`, a `match` choice or a slice",
    "ExprRangeList": "an `in` or a `match` choice",
    "ExprSlice": "a subscript (a part select or a slice)",
    "ExprStructField": "a struct literal",
    "TypeExprRefSelf": "a member reference",
    "TypeExprRefSuper": "a member reference or a call",
}

#: Aggregate literals: `{1, 2}` and `{.a = 1}` (4.6.3).
AGGREGATES = ("ExprList", "ExprStructLiteral")

#: Where an aggregate literal takes its type from (8.4.2): what it is
#: assigned to, returned as, or passed for; the members of an enclosing
#: literal; and, for a struct literal, the other side of `==`/`!=` (8.5.3).
#: An array literal is also the set of an `in` (8.5.9), which compares each
#: element (`body_walker.in_as_compare`).
AGGREGATE_PLACES = frozenset({"initializer", "assigned value", "return value",
                              "call argument", "element", "member",
                              "compared"})

#: How a refusal names a kind, in PSS's words.
_PSS = {
    "ExprList": "an aggregate literal (`{...}`)",
    "ExprStructLiteral": "a struct literal (`{.m = ...}`)",
    "ExprRefUnresolved": "an unresolved name",
    "StmtSuper": "`super;`",
    "StmtPass": "an empty statement",
    "ExprDict": "a map literal",
}

#: How a refusal names a place.
_PLACE = {"expr": "an operand", "test": "a condition",
          "subject": "a `match` subject", "in": "the set of an `in`",
          "index": "an index", "iterated": "what a `foreach` iterates",
          "count": "a repeat count", "callee": "a call's receiver",
          "arm": "an arm of `?:`", "choice": "a `match` choice",
          "target": "an assignment's target", "stmt": "a statement"}


def emitter_kinds(emitter_cls) -> FrozenSet[str]:
    """The IR kinds ``emitter_cls`` renders: its hooks, or for an emitter
    that dispatches by a cascade, the ``KINDS`` it declares."""
    kinds = getattr(emitter_cls, "KINDS", None)
    if kinds is not None:
        return frozenset(kinds)
    return frozenset(emitter_cls.hooks())


def _is_body_node(n) -> bool:
    return (dc.is_dataclass(n) and not isinstance(n, type)
            and _dt_name(n).startswith(("Stmt", "Expr", "TypeExpr")))


def _place_of(parent, field: str, place: str) -> str:
    """The place a child in ``parent``'s ``field`` stands in."""
    cn = _dt_name(parent)
    if cn == "StmtAnnAssign":
        return {"value": "initializer", "target": "target"}.get(field, "skip")
    if cn == "StmtAssign":
        return "assigned value" if field == "value" else "target"
    if cn == "StmtAugAssign":
        return "expr" if field == "value" else "target"
    if cn == "StmtReturn":
        return "return value"
    if cn == "ExprCall":
        return "call argument" if field == "args" else "callee"
    if cn == "ExprList":
        return "element"
    if cn in ("ExprStructLiteral", "ExprStructField"):
        return "member"
    if cn == "ExprBin" and field == "rhs" and getattr(
            getattr(parent, "op", None), "name", "") in ("Eq", "NotEq"):
        return "compared"
    if cn == "ExprIfExp":
        return "test" if field == "test" else "arm"
    if cn == "ExprIn":
        return "in" if field == "container" else "expr"
    if cn == "ExprSubscript":
        return "index" if field == "slice" else "expr"
    if cn in ("StmtIf", "StmtIfClause", "StmtWhile", "StmtRepeatWhile"):
        return "test" if field in ("test", "condition") else "stmt"
    if cn == "StmtMatch":
        return "subject" if field == "subject" else "stmt"
    if cn == "StmtMatchCase":
        return "choice" if field == "pattern" else "stmt"
    if cn == "StmtForeach":
        return "iterated" if field == "iter" else "target"
    if cn == "StmtFor":
        return "count" if field == "iter" else "target"
    return "expr"


def refusals(bodies: Iterable, kinds: Optional[FrozenSet[str]],
             language: str, unsupported: Mapping[str, str] = {},
             unsupported_locals: Mapping[str, str] = {}) -> List[str]:
    """One located diagnostic per node of ``bodies`` (``[(component,
    function, ...)]``) that a target rendering ``kinds`` (None: every kind)
    cannot render. ``unsupported`` (``{kind: reason}``) narrows that: such
    a node is refused with its reason wherever it stands, a part included.
    ``unsupported_locals`` (``{type kind: reason}``, a `DataType` class
    name such as ``"DataTypeStruct"``) refuses a declared local of that
    type, with its reason."""
    out: List[str] = []

    def site(comp, fn) -> str:
        if comp is None:
            return f"{fn.name}: "
        return f"{(getattr(comp, 'name', None) or '?').split('::')[-1]}::" \
               f"{fn.name}: "

    for body in bodies:
        comp, fn = body[0], body[1]

        def refuse(n, stmt, text, comp=comp, fn=fn):
            out.append(at((n, stmt, fn), f"{site(comp, fn)}{text}"))

        def walk(n, place: str, stmt) -> None:
            if isinstance(n, (list, tuple)):
                for x in n:
                    walk(x, place, stmt)
                return
            if not _is_body_node(n):
                return
            cn = _dt_name(n)
            if cn.startswith("Stmt"):
                stmt = n
            if cn in unsupported and place != "callee":
                refuse(n, stmt, f"{_PSS.get(cn, cn)} is not lowered to "
                                f"{language}: {unsupported[cn]}")
                return
            if cn == "StmtAnnAssign":
                ann = getattr(n, "annotation", None)
                tn = _dt_name(ann) if ann is not None else None
                if tn in unsupported_locals:
                    name = getattr(getattr(n, "target", None), "name", "?")
                    refuse(n, stmt, f"local '{name}' of type "
                           f"{getattr(ann, 'name', None) or tn} is not "
                           f"lowered to {language}: {unsupported_locals[tn]}")
            if cn in AGGREGATES:
                if not (place in AGGREGATE_PLACES
                        or (place == "in" and cn == "ExprList")) or (
                        place == "compared" and cn == "ExprList"):
                    refuse(n, stmt, f"{_PSS[cn]} as "
                           f"{_PLACE.get(place, 'an operand')} is not lowered "
                           f"to {language}: an aggregate literal takes its "
                           f"type from where it is assigned, returned or "
                           f"passed (LRM 8.4.2)")
                    return
            elif cn not in PARTS and kinds is not None and \
                    cn not in kinds and place != "callee":
                refuse(n, stmt, f"{_PSS.get(cn, cn)} is not lowered to "
                                f"{language}")
                return
            for f in dc.fields(n):
                if f.name in NON_CHILD_FIELDS or f.name in (
                        "annotation", "comment", "comment_trailing",
                        "target_type", "ir_type"):
                    continue
                child_place = _place_of(n, f.name, place)
                if child_place == "skip":
                    continue
                walk(getattr(n, f.name, None), child_place, stmt)

        walk(getattr(fn, "body", None), "stmt", None)
    return list(dict.fromkeys(out))
