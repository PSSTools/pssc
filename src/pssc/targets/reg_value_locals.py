"""Which register a register-value struct local holds the value of.

A target that spells a register's `read()` as its word (the HAL fixture's
``DEV_READ32_INTO``) has no value struct in C to declare
``mode_s v = regs.ctl.read();`` as: the local is held as the register's
word, and its fields are read and set through the register hooks
(`reg_value_field_get`, `reg_value_field_set`). Those hooks need the
register's layout, which the local's type alone does not give (two
registers of one value struct may differ in width), so it is bound here
from what the body does with the local:

- ``v = r.read()`` (a declaration or an assignment) and ``r.write(v)`` bind
  ``v`` to ``r``;
- ``v = w`` and ``T v = w`` give ``v`` and ``w`` one binding.

A local bound to two layouts, a local never read from or written to a
register, and a local used whole anywhere else (an argument, a return
value, a comparison) are refused at the statement: a word has no other
spelling the target has said it accepts.

`bind` is target independent: the caller says which declarations are
candidates and which calls are register reads and writes.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..diag import Refusal
from .ir_walk import _is_node, children


def _dt_name(n) -> str:
    return type(n).__name__


def _kids(n):
    """The child nodes of ``n``, lists flattened, types skipped."""
    for v in children(n):
        for c in (v if isinstance(v, (list, tuple)) else [v]):
            if _is_node(c) and not _dt_name(c).startswith("DataType"):
                yield c


@dc.dataclass
class LocalBinding:
    """A word-held local's register: ``access`` is the read or write that
    bound it first, in source order (the caller's `access_of` key), and
    ``call`` that call. Only the register's layout may be relied on from
    it: another access in the class may name another register of the same
    layout, or other indices."""
    access: Any
    call: Any


def bind(body, access_of: Callable[[Any], Optional[Tuple[Any, str]]],
         candidate: Callable[[Any], bool],
         same_layout: Callable[[Any, Any], bool]) -> Dict[str, LocalBinding]:
    """``{local name: LocalBinding}`` for each local of ``body`` that
    ``candidate`` (given its `StmtAnnAssign`) says is word-held.

    ``access_of(call)`` is ``(access, method)`` for a register access, the
    method `read` or `write` (others are not bindings), else None.
    ``same_layout(a, b)`` compares two such accesses. Raises a located
    `Refusal` for a local this module's doc refuses."""
    decls: Dict[str, Any] = {}
    parent: Dict[str, str] = {}
    bound: Dict[str, Tuple[LocalBinding, Any]] = {}
    allowed: set = set()
    uses: List[Tuple[Any, Any]] = []

    def find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def cand(e) -> Optional[str]:
        if _dt_name(e) == "ExprRefLocal" and e.name in decls:
            return e.name
        return None

    def access(e):
        if _dt_name(e) != "ExprCall":
            return None
        return access_of(e)

    def add(name: str, lb: LocalBinding, at) -> None:
        r = find(name)
        have = bound.get(r)
        if have is None:
            bound[r] = (lb, at)
        elif not same_layout(have[0].access, lb.access):
            raise Refusal(
                f"local '{name}' holds the values of two registers whose "
                f"layouts differ", at, have[1])

    def union(a: str, b: str, at) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        parent[rb] = ra
        if rb in bound:
            lb, first = bound.pop(rb)
            if ra in bound and not same_layout(bound[ra][0].access, lb.access):
                raise Refusal(
                    f"locals '{a}' and '{b}' are copied into each other but "
                    f"hold the values of registers whose layouts differ",
                    at, first)
            bound.setdefault(ra, (lb, first))

    def assign(name: str, value, at) -> None:
        acc = access(value)
        if acc is not None and acc[1] == "read":
            add(name, LocalBinding(acc[0], value), at)
            return
        other = cand(value)
        if other is not None:
            allowed.add(id(value))
            union(name, other, at)

    # Declarations first: a candidate is known before its first use.
    def collect(n) -> None:
        if _dt_name(n) == "StmtAnnAssign" and \
                _dt_name(n.target) == "ExprRefLocal" and candidate(n):
            decls[n.target.name] = n
            parent[n.target.name] = n.target.name
        for c in _kids(n):
            collect(c)

    def visit(n, at) -> None:
        if getattr(n, "loc", None) is not None:
            at = n
        k = _dt_name(n)
        if k == "StmtAnnAssign" and getattr(n.target, "name", None) in decls:
            allowed.add(id(n.target))
            if getattr(n, "value", None) is not None:
                assign(n.target.name, n.value, at)
        elif k == "StmtAssign" and len(n.targets) == 1 and \
                cand(n.targets[0]) is not None:
            allowed.add(id(n.targets[0]))
            assign(n.targets[0].name, n.value, at)
        elif k == "ExprCall":
            acc = access(n)
            if acc is not None and acc[1] == "write" and len(n.args) == 1 \
                    and cand(n.args[0]) is not None:
                allowed.add(id(n.args[0]))
                add(n.args[0].name, LocalBinding(acc[0], n), at)
        elif k == "ExprAttribute" and cand(n.value) is not None:
            allowed.add(id(n.value))
        elif k == "ExprRefLocal" and n.name in decls:
            uses.append((n, at))
        for c in _kids(n):
            visit(c, at)

    for st in body if isinstance(body, (list, tuple)) else [body]:
        collect(st)
    if not decls:
        return {}
    for st in body if isinstance(body, (list, tuple)) else [body]:
        visit(st, None)
    for e, at in uses:
        if id(e) not in allowed:
            raise Refusal(
                f"local '{e.name}' holds a register's value as its word, "
                f"so it can only be read from or written to a register, "
                f"copied, or have a field read or set; it is used whole "
                f"here", at, e)
    out: Dict[str, LocalBinding] = {}
    for name, decl in decls.items():
        got = bound.get(find(name))
        if got is None:
            raise Refusal(
                f"local '{name}' has a register value type but is never "
                f"read from or written to a register, so it has no layout "
                f"(declare it as the register's word type instead)", decl)
        out[name] = got[0]
    return out
