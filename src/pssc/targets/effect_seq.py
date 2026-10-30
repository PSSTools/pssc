"""Effect sequencing: move matched calls out of expressions into statements.

A platform whose register read is a statement (a macro that stores into a
local, a bus transaction a driver issues) cannot render
``if (self.r.read() & 1 && self.s.read() == 2)``. :func:`sequence` rewrites a
body so that each call its predicate matches is left only as

- a whole expression statement (``self.r.read();``),
- the whole value of a declaration (``bit[32] v = self.r.read();``), or
- the whole value of an assignment to a local (``v = self.r.read();``);

any other matched call is read into a fresh local declared just before the
statement that used it, in evaluation order, and the use refers to the local.
The right operands of ``&&`` / ``||`` and the arms of ``?:`` are evaluated
only sometimes, so a read in one becomes a nested ``if``, never an
unconditional read.

A call the predicate does not match stays in the expression. When one is
evaluated before a matched call of the same expression (``g() + r.read()``),
reading the register first would reorder them: that is refused, located,
rather than reordered.

This pass covers expression statements, declarations, assignments and
``return``; conditions of ``if`` / ``match`` / loops are left as they are
(PC-10c). Design: ``docs/design/effect-sequencing.md``.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, List, Optional, Tuple

import zuspec.ir.core as ir

from ..diag import Refusal
from .call_legality import REG_DIRECTION
from .ir_walk import Transformer, child_fields, rebuild, walk, _is_node

#: ``pred(call)``: the type of the local a matched call is read into, or
#: ``None`` when the call is not one to sequence.
Pred = Callable[[Any], Optional[Any]]

#: The type of a ``&&`` / ``||`` result read into a local.
BOOL = ir.DataTypeInt(name="bool", bits=1, signed=False)

_READS = frozenset(m for m, d in REG_DIRECTION.items() if d == "r")


def reg_reads(comp=None, tm=None) -> Pred:
    """The predicate that matches register reads (``REG_DIRECTION`` "r":
    ``read``, ``read_val``, ``read_field``) on a ``self``-rooted path.

    With ``comp``, the access is resolved (`reg_layout.resolve_access`) and
    the local is typed as the register's value: its value struct for
    ``read()`` of a struct-valued register, else unsigned at the register's
    width. Without ``comp`` the call is matched by method name alone and
    typed unsigned 32 bits; that is for IR-level tests, a backend passes
    ``comp``."""
    from .reg_layout import access_path, resolve_access, value_bits

    def pred(call):
        f = getattr(call, "func", None)
        if not isinstance(f, ir.ExprAttribute) or f.attr not in _READS:
            return None
        if not access_path(f.value):
            return None
        if comp is None:
            return ir.DataTypeInt(bits=32, signed=False)
        acc = resolve_access(comp, call, tm)
        if acc is None:
            return None
        a = acc.accessor
        if f.attr == "read" and a.value_struct is not None:
            return a.value_struct
        return ir.DataTypeInt(bits=value_bits(a.dtype), signed=False)
    return pred


def _names(body) -> set:
    out = set()
    for n in walk(body):
        name = getattr(n, "name", None)
        if isinstance(name, str) and type(n).__name__.startswith("ExprRef"):
            out.add(name)
    return out


def _fresh_names(body) -> Callable[[str], str]:
    used = _names(body)
    counter = itertools.count()

    def fresh(hint: str) -> str:
        while True:
            name = f"_pss_{hint}{next(counter)}"
            if name not in used:
                used.add(name)
                return name
    return fresh


def _callee(call) -> str:
    f = getattr(call, "func", None)
    return getattr(f, "attr", None) or getattr(f, "name", None) or "?"


def sequence(body: List[Any], pred: Optional[Pred] = None,
             fresh: Optional[Callable[[str], str]] = None,
             type_of: Optional[Callable[[Any], Optional[Any]]] = None
             ) -> List[Any]:
    """*body* with each call *pred* matches sequenced (module doc).

    *pred* defaults to :func:`reg_reads` without a component. *fresh(hint)*
    names a new local; the default spells ``_pss_<hint><n>``, avoiding the
    names *body* refers to. *type_of(expr)* types the local a ``?:`` is read
    into when neither arm is itself a matched call; without it such a
    ``?:`` is refused. Returns *body* itself when nothing matched; the input
    is not changed. Raises :class:`Refusal` (located) for a matched call
    that would be reordered past another call."""
    seq = _Sequencer(pred or reg_reads(), fresh or _fresh_names(body),
                     type_of)
    return seq.visit(body)


class _Sequencer(Transformer):

    def __init__(self, pred: Pred, fresh, type_of) -> None:
        self.pred = pred
        self.fresh = fresh
        self.type_of = type_of
        self.st = None
        self.temps: set = set()

    # -- statements --------------------------------------------------------

    def _stmt(self, st, field: str, keep_whole: bool):
        """Sequence ``st.<field>``; the value is left whole when it is a
        matched call and *keep_whole*."""
        self.st = st
        v = getattr(st, field)
        if v is None:
            return st
        if keep_whole and self._match(v) is not None:
            pre, v2, _ = self._children(v)
        else:
            pre, v2, _ = self._expr(v)
        out = rebuild(st, **{field: v2})
        return pre + [out] if pre else out

    def visit_StmtExpr(self, st):
        out = self._stmt(st, "expr", True)
        if isinstance(out, list):
            last = out[-1].expr
            if isinstance(last, ir.ExprRefLocal) and last.name in self.temps:
                out = out[:-1]          # `t;` of a sequenced `a && f()`
        return out

    def visit_StmtAnnAssign(self, st):
        return self._stmt(st, "value", True)

    def visit_StmtAssign(self, st):
        local = len(st.targets) == 1 and \
            isinstance(st.targets[0], ir.ExprRefLocal)
        return self._stmt(st, "value", local)

    def visit_StmtAugAssign(self, st):
        return self._stmt(st, "value", False)

    def visit_StmtReturn(self, st):
        return self._stmt(st, "value", False)

    # -- expressions -------------------------------------------------------

    def _match(self, e):
        return self.pred(e) if isinstance(e, ir.ExprCall) else None

    def _contains(self, e) -> bool:
        return any(self._match(n) is not None for n in walk(e))

    def _local(self, hint, dtype, value, at):
        name = self.fresh(hint)
        self.temps.add(name)
        decl = ir.StmtAnnAssign(target=ir.ExprRefLocal(name=name),
                                annotation=dtype, value=value)
        decl.loc = getattr(at, "loc", None) or getattr(self.st, "loc", None)
        return decl, ir.ExprRefLocal(name=name)

    def _set(self, name_ref, value):
        a = ir.StmtAssign(targets=[ir.ExprRefLocal(name=name_ref.name)],
                          value=value)
        a.loc = getattr(value, "loc", None) or getattr(self.st, "loc", None)
        return a

    def _expr(self, e) -> Tuple[list, Any, Any]:
        """``(statements, expr, residual)``: the statements to run first,
        what is left of *e*, and the first unmatched call left in it
        (``None`` if none)."""
        if not _is_node(e) or type(e).__name__.startswith("DataType"):
            return [], e, None
        if isinstance(e, ir.ExprCall):
            pre, e2, residual = self._children(e)
            t = self._match(e)
            if t is None:
                return pre, e2, residual or e
            decl, ref = self._local("rd", t, e2, e)
            return pre + [decl], ref, None
        if isinstance(e, ir.ExprBool) and \
                any(self._contains(v) for v in e.values[1:]):
            return self._short_circuit(e)
        if isinstance(e, ir.ExprIfExp) and \
                (self._contains(e.body) or self._contains(e.orelse)):
            return self._select(e)
        return self._children(e)

    def _children(self, node):
        pre: list = []
        new = {}
        residual = None

        def one(x):
            nonlocal residual
            s, y, r = self._expr(x)
            if s and residual is not None:
                raise Refusal(
                    f"`{_callee(residual)}()` is evaluated before a register "
                    f"read in the same expression; reading the register "
                    f"first would reorder them: call it into a local first",
                    residual, self.st)
            pre.extend(s)
            residual = residual or r
            return y

        for name, v in child_fields(node):
            if isinstance(v, list):
                out = [one(x) for x in v]
                if any(a is not b for a, b in zip(out, v)):
                    new[name] = out
            else:
                y = one(v)
                if y is not v:
                    new[name] = y
        return pre, rebuild(node, **new), residual

    def _short_circuit(self, e):
        """``a && b`` with a matched call in ``b``: ``t = a; if (t) { t = b; }``
        (``||``: ``if (!t)``), one nested ``if`` per later operand."""
        k = next(i for i in range(1, len(e.values))
                 if self._contains(e.values[i]))
        left = e.values[0] if k == 1 else rebuild(e, values=e.values[:k])
        pre, left2, _ = self._expr(left)
        decl, t = self._local("and" if e.op == ir.BoolOp.And else "or",
                              BOOL, left2, e)
        body: list = []
        inner = body
        for i, v in enumerate(e.values[k:]):
            s, v2, _ = self._expr(v)
            test = ir.ExprRefLocal(name=t.name)
            if e.op != ir.BoolOp.And:
                test = ir.ExprUnary(op=ir.UnaryOp.Not, operand=test)
            branch = s + [self._set(t, v2)]
            inner.append(ir.StmtIf(test=test, body=branch, orelse=[]))
            inner = branch
        return pre + [decl] + body, t, None

    def _select(self, e):
        """``c ? a : b`` with a matched call in an arm:
        ``if (c) { t = a; } else { t = b; }``."""
        pre, test, _ = self._expr(e.test)
        dtype = self._select_type(e)
        name = self.fresh("sel")
        self.temps.add(name)
        t = ir.ExprRefLocal(name=name)
        decl = ir.StmtAnnAssign(target=ir.ExprRefLocal(name=name),
                                annotation=dtype, value=None)
        decl.loc = getattr(e, "loc", None) or getattr(self.st, "loc", None)
        arms = []
        for arm in (e.body, e.orelse):
            s, a2, _ = self._expr(arm)
            arms.append(s + [self._set(t, a2)])
        return pre + [decl, ir.StmtIf(test=test, body=arms[0],
                                      orelse=arms[1])], t, None

    def _select_type(self, e):
        if self.type_of is not None:
            t = self.type_of(e)
            if t is not None:
                return t
        ts = [self._match(a) for a in (e.body, e.orelse)]
        ts = [t for t in ts if t is not None]
        ints = [t for t in ts if isinstance(t, ir.DataTypeInt)]
        if ts and len(ints) == len(ts):
            return ir.DataTypeInt(bits=max(t.bits for t in ints),
                                  signed=False)
        if len(ts) == 1:
            return ts[0]
        raise Refusal(
            "a `?:` with a register read in an arm is read into a local, "
            "and its type is not known here: assign it to a local first",
            e, self.st)
