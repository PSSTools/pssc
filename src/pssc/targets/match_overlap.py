"""A `match` with more than one matching arm is an error (LRM 20.7.10 d),
checked once for every op-model target.

Every target renders a `match` as "the first arm that holds the subject":
a C `switch`, an `if` chain, an SV `case`. That is right only while no two
arms hold one value. Where two arms MAY both hold the subject -- constant
arms that overlap, or an arm whose value or range bound is only known at
run time -- `lower` puts an `error()` in front of the `match`, taken when
the subject is in two of them at once:

    if ((s in arm1 && s in arm2) || ...) error("match: ...");
    match (s) { ... }

A `match` whose arms are constant and disjoint, which is nearly every one,
is left exactly as it was: distinct integer intervals, and distinct string
literals (a register group's offset function is a `match` over its
instance names). The subject is read once per test, so a subject
containing a call is first read into a local.

Like `array_methods`, this rewrites the IR in place, before the call gate,
so no emitter grows its own spelling.
"""
from __future__ import annotations

import itertools

import zuspec.ir.core as ir

from ..diag import Refusal
from .array_methods import _nodes
from .body_walker import any_of, match_arm_test, match_values
from .expr_types import ExprTypes
from .progseq_model import _dt_name

__all__ = ["lower"]

MESSAGE = "match: more than one choice matches the value"


def lower(ctx) -> None:
    """Guard every `match` in the bodies of ``ctx``'s types whose arms may
    overlap."""
    tm = getattr(ctx, "type_map", {}) or {}
    for dtype in list(tm.values()):
        for fn in list(getattr(dtype, "functions", None) or ()):
            if getattr(fn, "body", None):
                types = ExprTypes(fn, dtype, ctx)
                counter = itertools.count()
                _guard_list(fn.body, types, counter)


def _guard_list(stmts, types, counter) -> None:
    """Insert the guard before each overlapping `match` in ``stmts``, and in
    every statement list nested in them."""
    i = 0
    while i < len(stmts):
        st = stmts[i]
        _guard_nested(st, types, counter)
        if _dt_name(st) == "StmtMatch":
            pre = _guard(st, types, counter)
            if pre:
                stmts[i:i] = pre
                i += len(pre)
        i += 1


def _guard_nested(st, types, counter) -> None:
    import dataclasses as dc
    if not dc.is_dataclass(st) or isinstance(st, type):
        return
    for f in dc.fields(st):
        v = getattr(st, f.name, None)
        if isinstance(v, list) and v:
            if _dt_name(v[0]).startswith("Stmt") \
                    and _dt_name(v[0]) != "StmtMatchCase":
                _guard_list(v, types, counter)
            else:
                for x in v:
                    _guard_nested(x, types, counter)
        elif _dt_name(v).startswith("Stmt"):
            _guard_nested(v, types, counter)


def _key(v):
    """What a choice value is known to hold: ``("int", lo, hi)`` for a
    constant integer or range, ``("str", s)`` for a string literal, else
    None (known only at run time)."""
    if _dt_name(v) == "ExprRange":
        lo, hi = v.lower, v.upper if v.upper is not None else v.lower
    else:
        lo = hi = v
    if _dt_name(lo) != "ExprConstant" or _dt_name(hi) != "ExprConstant":
        return None
    if lo is hi and isinstance(lo.value, str):
        return ("str", lo.value)
    if not all(isinstance(x.value, int) and not isinstance(x.value, bool)
               for x in (lo, hi)):
        return None
    return ("int", lo.value, hi.value)


def overlapping_pairs(arms) -> list:
    """The pairs ``(i, j)``, ``i < j``, of arms that may both hold one value,
    in order.

    Two string literals hold one value only when they are the same string;
    two integer intervals, when they intersect. A value known only at run
    time may meet anything, so its arm pairs with every other. The constant
    pairs are found by sorting, not by trying every pair: a register bank's
    offset function is a `match` over hundreds of distinct names, and the
    cost here must follow the overlaps there are, not the square of the
    choices.
    """
    pairs = set()
    unknown = set()
    strs = {}
    ivs = []
    for i, vs in enumerate(arms):
        for v in vs:
            k = _key(v)
            if k is None:
                unknown.add(i)
            elif k[0] == "str":
                strs.setdefault(k[1], set()).add(i)
            else:
                ivs.append((k[1], k[2], i))

    def add(i, j):
        if i != j:
            pairs.add((min(i, j), max(i, j)))

    for i in unknown:
        for j in range(len(arms)):
            add(i, j)
    for owners in strs.values():
        for i, j in itertools.combinations(sorted(owners), 2):
            add(i, j)
    # Sweep the intervals by lower bound, keeping those still open.
    ivs.sort()
    active = []
    for lo, hi, i in ivs:
        active = [a for a in active if a[0] >= lo]
        for _, j in active:
            add(i, j)
        active.append((hi, i))
    return sorted(pairs)


def _guard(st, types, counter):
    """The statements to put before ``st``: none when its arms cannot
    overlap."""
    arms = [vs for vs in (match_values(c.pattern) for c in st.cases) if vs]
    pairs = [(arms[i], arms[j]) for i, j in overlapping_pairs(arms)]
    if not pairs:
        return []
    pre = []
    subject = st.subject
    if any(_dt_name(x) in ("ExprCall", "ExprMemberCall")
           for x in _nodes(subject)):
        t = types.type_of(subject)
        if t is None or t.kind not in ("int", "enum", "bool"):
            raise Refusal(
                "a `match` whose choices may overlap tests its subject once "
                "per choice, and the subject contains a call; read it into a "
                "local first", subject, st)
        dt = (t.dtype if t.kind == "enum" and t.dtype is not None
              else ir.DataTypeInt(bits=t.width, signed=t.signed))
        name = f"_pss_match{next(counter)}"
        pre.append(ir.StmtAnnAssign(target=ir.ExprRefLocal(name=name),
                                    annotation=dt, value=subject))
        subject = ir.ExprRefLocal(name=name)
        st.subject = subject
    cond = any_of([ir.ExprBin(lhs=match_arm_test(subject, a),
                               op=ir.BinOp.And,
                               rhs=match_arm_test(subject, b))
                    for a, b in pairs])
    err = ir.StmtExpr(expr=ir.ExprCall(
        func=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="error"),
        args=[ir.ExprConstant(value=MESSAGE)], keywords=[]))
    pre.append(ir.StmtIf(test=cond, body=[err], orelse=[]))
    return pre
