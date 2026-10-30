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

Statements with a condition or a bound:

- ``if`` test, ``match`` subject, ``assert`` test, ``for`` / ``foreach``
  iterable and ``repeat`` count are evaluated once: reads are hoisted before
  the statement.
- A read in a later test of an ``if`` / ``else if`` chain runs only when the
  earlier tests failed: the chain is split there and the rest nested under
  the ``else``, after the hoisted reads.
- A ``while`` condition is re-read every iteration:
  ``while (1) { reads; if (!c) break; body }``. ``repeat { body } while (c)``
  becomes ``repeat { body; reads; if (!c) break; } while (1)``, refused when
  the body has a ``continue`` of that loop (it would skip the test).
- Refused, located: a read in a ``match`` guard, in a ``while`` with an
  ``else``, or in an assignment target (``a[r.read()] = x``).

Design: ``docs/design/effect-sequencing.md``.
"""
from __future__ import annotations

import dataclasses as dc
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

#: The attribute, true on the declaration of a local that holds a matched
#: call's value (``getattr(decl, TEMP, False)``).
TEMP = "pss_effect_temp"

_LOGICAL = {ir.BinOp.And: ir.BoolOp.And, ir.BinOp.Or: ir.BoolOp.Or}

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


_LOOPS = (ir.StmtWhile, ir.StmtRepeatWhile, ir.StmtRepeat, ir.StmtFor,
          ir.StmtForeach)


def _loop_continue(body):
    """The first ``continue`` in *body* that belongs to the enclosing loop
    (not to a loop nested in *body*), or ``None``."""
    for st in body:
        if isinstance(st, ir.StmtContinue):
            return st
        if isinstance(st, _LOOPS):
            continue
        for _, v in child_fields(st):
            if isinstance(v, list) and v and _is_node(v[0]):
                c = _loop_continue(v)
                if c is not None:
                    return c
    return None


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


class _Subst(Transformer):
    """Replaces each node whose ``id`` is in *done* by its value there."""

    def __init__(self, done: dict) -> None:
        self.done = done

    def visit(self, node):
        if not isinstance(node, list) and id(node) in self.done:
            return self.done[id(node)]
        return super().visit(node)


class _Sequencer(Transformer):

    def __init__(self, pred: Pred, fresh, type_of) -> None:
        self.pred = pred
        self.fresh = fresh
        self.type_of = type_of
        self.st = None
        self.temps: set = set()
        #: ``id(call) -> ref``: each matched call this pass replaced.
        self.done: dict = {}

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
            elif out[-1] is not st:
                out[-1] = self._fields_too(out[-1], st)
        return out

    def _fields_too(self, new, st):
        """``new`` (``st`` sequenced) with its masked write's field values
        (`reg_rmw.RegWriteProvenance`) given the same locals as its
        arguments: they are the arguments' own nodes, and a backend that
        writes field by field renders them, so a read left in one would be
        read a second time, after the destination."""
        from ..reg_rmw import RegWriteProvenance
        call = new.expr
        prov = getattr(call, "provenance", None)
        if not isinstance(prov, RegWriteProvenance) or not prov.fields:
            return new
        sub = _Subst(self.done)
        fields = tuple(dc.replace(fw, value=sub.visit(fw.value))
                       for fw in prov.fields)
        for fw in fields:
            if self._contains(fw.value):
                raise Refusal(
                    f"internal error: field '{fw.slice.name}' of a masked "
                    f"write reads a register its arguments do not", st)
        if all(a.value is b.value for a, b in zip(fields, prov.fields)):
            return new
        call = dc.replace(call, provenance=dc.replace(prov, fields=fields))
        return rebuild(new, expr=call)

    def visit_StmtAnnAssign(self, st):
        return self._stmt(st, "value", True)

    def visit_StmtAssign(self, st):
        self.st = st
        for t in st.targets:
            self._refuse_in(t, "an assignment target")
        local = len(st.targets) == 1 and \
            isinstance(st.targets[0], ir.ExprRefLocal)
        return self._stmt(st, "value", local)

    def visit_StmtAugAssign(self, st):
        self.st = st
        self._refuse_in(st.target, "an assignment target")
        return self._stmt(st, "value", False)

    def visit_StmtReturn(self, st):
        return self._stmt(st, "value", False)

    def _refuse_in(self, e, where: str) -> None:
        for n in walk(e):
            if self._match(n) is not None:
                raise Refusal(
                    f"a register read in {where} cannot be sequenced: read "
                    f"it into a local first", n, self.st)

    def _head(self, st, *fields):
        """Hoist reads out of ``st.<fields>`` (each evaluated once, before
        the body), then sequence the bodies."""
        self.st = st
        pre: list = []
        new = {}
        for f in fields:
            v = getattr(st, f)
            if v is None:
                continue
            s, v2, _ = self._expr(v)
            pre.extend(s)
            if v2 is not v:
                new[f] = v2
        out = self.generic_visit(rebuild(st, **new))
        return pre + [out] if pre else out

    def visit_StmtIf(self, st):
        return self._head(st, "test")

    def visit_StmtAssert(self, st):
        return self._head(st, "test", "msg")

    def visit_StmtFor(self, st):
        return self._head(st, "iter")

    def visit_StmtForeach(self, st):
        return self._head(st, "iter")

    def visit_StmtRepeat(self, st):
        return self._head(st, "count")

    def visit_StmtMatch(self, st):
        self.st = st
        for c in st.cases:
            if c.guard is not None:
                self._refuse_in(c.guard, "a `match` guard")
        return self._head(st, "subject")

    def visit_StmtIfChain(self, st):
        out = self._chain(st, st.clauses, st.orelse)
        return out[0] if len(out) == 1 else out

    def _chain(self, st, clauses, orelse) -> list:
        """``if a .. else if b ..``: split the chain at the first later test
        holding a read; the rest goes under the ``else``, after its reads."""
        c0 = clauses[0]
        self.st = c0 if getattr(c0, "loc", None) else st
        pre, t0, _ = self._expr(c0.test)
        j = next((i for i in range(1, len(clauses))
                  if self._contains(clauses[i].test)), len(clauses))
        head = [rebuild(c0, test=t0)] + list(clauses[1:j])
        head = [rebuild(c, body=self.visit(c.body)) for c in head]
        if j < len(clauses):
            tail = self._chain(st, clauses[j:], orelse)
        else:
            tail = self.visit(orelse)
        if all(a is b for a, b in zip(head, clauses)) and \
                len(head) == len(clauses) and tail is orelse:
            return pre + [st]
        return pre + [rebuild(st, clauses=head, orelse=tail)]

    def _until(self, test, pre):
        """``pre; if (!test) break;``."""
        brk = ir.StmtBreak()
        brk.loc = getattr(self.st, "loc", None)
        return pre + [ir.StmtIf(
            test=ir.ExprUnary(op=ir.UnaryOp.Not, operand=test),
            body=[brk], orelse=[])]

    def visit_StmtWhile(self, st):
        if not self._contains(st.test):
            return self.generic_visit(st)
        self.st = st
        if st.orelse:
            raise Refusal("a register read in the condition of a `while` "
                          "with an `else` cannot be sequenced", st.test, st)
        pre, t2, _ = self._expr(st.test)
        head = self._until(t2, pre)
        return rebuild(st, test=ir.ExprConstant(value=True),
                       body=head + self.visit(st.body))

    def visit_StmtRepeatWhile(self, st):
        if not self._contains(st.condition):
            return self.generic_visit(st)
        self.st = st
        cont = _loop_continue(st.body)
        if cont is not None:
            raise Refusal(
                "a register read in a `repeat ... while` condition is read "
                "at the end of the body, and this `continue` would skip "
                "it: test the condition in the loop body instead",
                cont, st)
        body = self.visit(st.body)
        self.st = st
        pre, c2, _ = self._expr(st.condition)
        return rebuild(st, condition=ir.ExprConstant(value=True),
                       body=body + self._until(c2, pre))

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
        # `TEMP`: the local is the pass's, assigned once here and only read
        # after, so a printer may hold it in another form (a raw register
        # word for a value-struct read).
        setattr(decl, TEMP, True)
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
            self.done[id(e)] = ref
            return pre + [decl], ref, None
        if isinstance(e, ir.ExprBin) and e.op in _LOGICAL and \
                self._contains(e.rhs):
            # The front end's `&&` / `||` is a binary node, not `ExprBool`.
            return self._short_circuit(ir.ExprBool(
                op=_LOGICAL[e.op], values=[e.lhs, e.rhs]))
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
