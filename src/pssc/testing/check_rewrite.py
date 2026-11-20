"""Checks that capture their operands: the IR pass before lowering.

Each call of a ``pssunit_pkg`` check (``check``, ``require``, ``check_eq``,
..., ``check_reg``, ``fail``) is rewritten so that each operand is evaluated
once, into a temporary, and the check becomes a call of
``pssunit_pkg::check_operand(v)`` per operand and then one of
``pssunit_pkg::check_capture(id, ok)`` (a bc import takes at most two
arguments). ``id`` names the call's site in
the :class:`CheckTable`: its source text, the text of each operand, and its
string arguments (a register path, a message), which then need not cross the
platform boundary -- so the checks lower to bc, which passes only scalars.
::

    check_eq(dma.status(), 2, "done");

becomes, in effect::

    int[64] _pssunit_ck0_a = dma.status();
    int[64] _pssunit_ck0_b = 2;
    check_operand(_pssunit_ck0_a);
    check_operand(_pssunit_ck0_b);
    check_capture(0, _pssunit_ck0_a == _pssunit_ck0_b);

``ok`` is decided by the model where the check is a comparison, so its
meaning is the model's; for the register checks it is decided by the runner,
from the back door or the bus log. ``check(a == b)`` captures ``a`` and ``b``
as ``int[64]`` (an operand's own type is not known here, so an unsigned value
of 2**63 or more compares as negative).

A call is left as it is when a string argument is not a literal, when it
names its arguments, or when the enclosing component has a function of the
same name (it, not the import, is called). The generators reuse the pass
(P5.4).
"""
from __future__ import annotations

import copy
import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

import zuspec.ir.core as ir

__all__ = ["CheckSite", "CheckTable", "Operand", "CAPTURE", "OPERAND", "rewrite"]

PACKAGE = "pssunit_pkg"
CAPTURE = "check_capture"
OPERAND = "check_operand"

_COMPARE = {"check_eq": "==", "check_ne": "!=", "check_lt": "<",
            "check_le": "<=", "check_gt": ">", "check_ge": ">="}
#: check -> (number of leading string arguments, has a value argument)
_REGISTER = {"check_reg": (1, True), "check_field": (2, True),
             "check_written": (2, True), "check_not_accessed": (1, False),
             "check_seq": (0, False), "check_seq_strict": (0, False)}
_STOPS = ("require", "fail")
CHECKS = ("check", "require", "fail") + tuple(_COMPARE) + tuple(_REGISTER)
#: platform-double call -> (number of leading string arguments, has a value
#: argument); each becomes ``double_call(id, v)`` (``base``: ``double_handle(id)``)
DOUBLE, HANDLE = "double_call", "double_handle"
DOUBLES = {"base": (1, False), "poke_reg": (1, True), "poke_field": (2, True),
           "complete_after": (2, True), "status_sequence": (2, True),
           "fail_on_access": (1, False), "state_get": (1, False),
           "state_set": (1, True), "expect_write": (2, True),
           "expect_read": (1, False)}
#: platform-double call with one scalar argument before its strings ->
#: (number of strings, has a value argument); as a statement it becomes
#: ``check_operand(lead)`` and then its ``double_call``
LEADS = {"poke_field_at": (2, True), "schedule_after": (1, True)}


@dc.dataclass
class Operand:
    """One captured operand: its source text, and where that text starts in
    the check's text (-1 when the source was not readable)."""

    text: str
    offset: int = -1


@dc.dataclass
class CheckSite:
    """One check call in the source."""

    id: int
    kind: str                        # the check called: "check_eq", "require", ...
    file: Optional[str]
    line: int
    col: int                         # 1-based
    text: str                        # the call, as written
    op: Optional[str] = None         # "==", ... where the model decides ``ok``
    operands: List[Operand] = dc.field(default_factory=list)
    what: str = ""                   # the ``what`` (``msg`` for ``fail``) argument
    strings: List[str] = dc.field(default_factory=list)  # reg [, field]
    stops: bool = False              # require / fail end the test

    @property
    def location(self) -> str:
        from .doctest import source
        where = source(self.file, self.line, self.col)  # a doc-comment example
        return "%s:%d:%d" % (where or (self.file, self.line, self.col))


class CheckTable(Dict[int, CheckSite]):
    """Check id -> :class:`CheckSite`."""


def rewrite(ctx) -> CheckTable:
    """Rewrite every ``pssunit_pkg`` check call in ``ctx``'s types and
    functions, in place; return the table of the sites rewritten. A context
    without ``pssunit_pkg::check_capture`` is left alone.

    The rewrite is done once per context: a second call (the next fixture
    lowered from the same context) finds nothing left to rewrite, so it
    returns the first call's table."""
    done = getattr(ctx, "_pssunit_checks", None)
    if done is not None:
        return done
    imports = _imports(ctx)
    table = CheckTable()
    if CAPTURE not in imports or OPERAND not in imports:
        return table
    ctx._pssunit_checks = table
    rw = _Rewriter(imports, table)
    roots = list((getattr(ctx, "type_map", None) or {}).values())
    fns = getattr(ctx, "functions", None) or {}
    roots += list(fns.values()) if isinstance(fns, dict) else list(fns)
    rw.types = {id(t) for t in roots}
    for t in roots:
        rw.own = _own_functions(t)
        rw.walk(t, root=True)
    if DOUBLE in imports and HANDLE in imports:
        rw.seen = set()
        for t in roots:
            rw.own = _own_functions(t)
            rw.doubles(t, root=True)
    return table


def _imports(ctx) -> Dict[str, Any]:
    out = {}
    for fn in getattr(ctx, "import_functions", None) or []:
        q = (getattr(fn, "metadata", None) or {}).get("qualified_name", "")
        if q.startswith(PACKAGE + "::"):
            out[q[len(PACKAGE) + 2:]] = fn
    return out


def _own_functions(t) -> set:
    names, seen = set(), set()
    while t is not None and id(t) not in seen:
        seen.add(id(t))
        names.update(getattr(f, "name", None) for f in getattr(t, "functions", None) or [])
        t = getattr(t, "super", None)
        if t is not None and not hasattr(t, "functions"):
            break
    return names


class _Rewriter:
    def __init__(self, imports, table: CheckTable):
        self.imports, self.table = imports, table
        self.types: set = set()
        self.own: set = set()
        self.seen: set = set()
        self.src = _Sources()

    # -- walking ---------------------------------------------------------------

    def walk(self, obj, root: bool = False) -> None:
        if id(obj) in self.seen or (not root and id(obj) in self.types):
            return
        self.seen.add(id(obj))
        if isinstance(obj, list):
            if any(isinstance(s, ir.Stmt) for s in obj):
                obj[:] = [n for s in obj for n in self.stmt(s)]
            for x in obj:
                self.walk(x)
        elif isinstance(obj, dict):
            for x in list(obj.values()):
                self.walk(x)
        elif dc.is_dataclass(obj) and not isinstance(obj, type):
            if isinstance(obj, (ir.Expr, ir.Loc)):
                return
            for f in dc.fields(obj):
                v = getattr(obj, f.name, None)
                if isinstance(v, (list, dict)) or dc.is_dataclass(v):
                    self.walk(v)

    def doubles(self, obj, root: bool = False) -> None:
        """Replace each platform-double call (``DOUBLES``) under ``obj``,
        wherever it is an expression, by its ``double_call``."""
        if id(obj) in self.seen or (not root and id(obj) in self.types):
            return
        self.seen.add(id(obj))
        if isinstance(obj, list):
            for i, x in enumerate(obj):
                obj[i] = self.double(x)
                self.doubles(obj[i])
        elif isinstance(obj, dict):
            for x in list(obj.values()):
                self.doubles(x)
        elif dc.is_dataclass(obj) and not isinstance(obj, type):
            if isinstance(obj, ir.Loc):
                return
            for f in dc.fields(obj):
                v = getattr(obj, f.name, None)
                if isinstance(v, ir.ExprCall):
                    v = self.double(v)
                    setattr(obj, f.name, v)
                if isinstance(v, (list, dict)) or dc.is_dataclass(v):
                    self.doubles(v)

    def double(self, call):
        if not isinstance(call, ir.ExprCall) or call.keywords:
            return call
        name = self.callee(call)
        if name is None:
            return call
        if isinstance(call.func, ir.ExprRefUnresolved) and name not in self.own:
            # ``pssunit_pkg::f(...)``: bc lowers an import only as ``self.f``
            call.func = ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=name)
        if name not in DOUBLES or name not in self.imports or name in self.own:
            return call
        nstr, has_v = DOUBLES[name]
        args = list(call.args)
        if len(args) != nstr + has_v or not all(_is_str(a) for a in args[:nstr]):
            return call
        cid = len(self.table)
        loc = call.loc
        self.table[cid] = CheckSite(
            id=cid, kind=name, file=getattr(loc, "file", None),
            line=getattr(loc, "line", 0), col=getattr(loc, "pos", 0),
            text=self.src.call(loc, name)[0], strings=[a.value for a in args[:nstr]])
        fn = HANDLE if name == "base" else DOUBLE
        new = [_const(cid)] + ([] if name == "base" else
                               [args[nstr] if has_v else _const(0)])
        return ir.ExprCall(loc=loc, func=ir.ExprAttribute(
            value=ir.TypeExprRefSelf(), attr=fn), args=new)

    def callee(self, call) -> Optional[str]:
        """The name of a ``pssunit_pkg`` import ``call`` may call: ``self.f``
        (``f`` after ``import pssunit_pkg::*``), or the linker's
        ``pssunit_pkg::f`` (an ``ExprRefUnresolved`` of the import's
        name); None for anything else."""
        f = call.func
        if isinstance(f, ir.ExprAttribute) and isinstance(f.value, ir.TypeExprRefSelf):
            return f.attr
        if isinstance(f, ir.ExprRefUnresolved) and f.name in self.imports:
            return f.name
        return None

    def stmt(self, s) -> List[Any]:
        if not isinstance(s, ir.StmtExpr) or not isinstance(s.expr, ir.ExprCall):
            return [s]
        call = s.expr
        name = self.callee(call)
        if name is None:
            return [s]
        if name in LEADS and name in self.imports and name not in self.own \
                and DOUBLE in self.imports and not call.keywords:
            return self.lead(name, call, s)
        if name not in CHECKS or name not in self.imports or name in self.own:
            return [s]
        if call.keywords:
            return [s]
        out = self.lower(name, call, s.loc or call.loc)
        return [s] if out is None else out

    def lead(self, name: str, call, s) -> List[Any]:
        """A ``LEADS`` call: its leading scalar as a ``check_operand``, then
        the call as a ``double_call``, which takes the operand."""
        nstr, has_v = LEADS[name]
        args = list(call.args)
        if len(args) != 1 + nstr + has_v or not all(_is_str(a) for a in args[1:1 + nstr]):
            return [s]
        cid = len(self.table)
        loc = call.loc
        self.table[cid] = CheckSite(
            id=cid, kind=name, file=getattr(loc, "file", None),
            line=getattr(loc, "line", 0), col=getattr(loc, "pos", 0),
            text=self.src.call(loc, name)[0],
            strings=[a.value for a in args[1:1 + nstr]])

        def call_(fn, a):
            return ir.StmtExpr(loc=loc, expr=ir.ExprCall(
                loc=loc, func=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=fn),
                args=a))
        return [call_(OPERAND, [args[0]]),
                call_(DOUBLE, [_const(cid), args[1 + nstr] if has_v else _const(0)])]

    # -- one check -------------------------------------------------------------

    def lower(self, name: str, call, loc) -> Optional[List[Any]]:
        args = list(call.args)
        params = list(self.imports[name].args.args)
        if len(args) > len(params):
            return None
        nstr, has_v = _REGISTER.get(name, (0, False))
        if name == "fail":
            nstr = 0
        strings = []
        for a in args[:nstr]:
            if not _is_str(a):
                return None
            strings.append(a.value)
        # the trailing what / msg
        tail = args[len(params) - 1] if len(args) == len(params) else None
        if name == "fail":
            tail = args[0] if args else None
        if tail is not None and not _is_str(tail):
            return None
        what = tail.value if tail is not None else ""

        cid = len(self.table)
        text, argtexts = self.src.call(loc, name)
        site = CheckSite(id=cid, kind=name, file=getattr(loc, "file", None),
                         line=getattr(loc, "line", 0), col=getattr(loc, "pos", 0),
                         text=text, what=what, strings=strings,
                         stops=name in _STOPS)
        self.table[cid] = site
        pre, ok, ops = [], None, []

        def temp(k, value, ann):
            ref = ir.ExprRefLocal(name="_pssunit_ck%d_%s" % (cid, k))
            pre.append(ir.StmtAnnAssign(loc=loc, target=ref,
                                        annotation=copy.deepcopy(ann), value=value))
            return ir.ExprRefLocal(name=ref.name)

        i64 = ir.DataTypeInt(bits=64, signed=True)
        if name in ("check", "require"):
            e = args[0]
            op = _CMP.get(getattr(e, "op", None)) if isinstance(e, ir.ExprBin) else None
            if op is not None:
                site.op = op
                ops = [temp("a", e.lhs, i64), temp("b", e.rhs, i64)]
                ok = ir.ExprBin(lhs=ops[0], op=e.op, rhs=ops[1])
                lt, rt = _split(argtexts[0] if argtexts else None, op)
                site.operands = [Operand(*lt), Operand(*rt)]
            else:
                ok = temp("ok", e, ir.DataTypeInt(name="bool", bits=1, signed=False))
                if argtexts:
                    site.operands = [Operand(*argtexts[0])]
        elif name in _COMPARE:
            site.op = _COMPARE[name]
            ops = [temp("a", args[0], params[0].annotation),
                   temp("b", args[1], params[1].annotation)]
            ok = ir.ExprBin(lhs=ops[0], op=_BINOP[site.op], rhs=ops[1])
            site.operands = [Operand(*t) for t in argtexts[:2]]
        elif has_v:
            ops = [temp("a", args[nstr], params[nstr].annotation)]
            if len(argtexts) > nstr:
                site.operands = [Operand(*argtexts[nstr])]
        if ok is None:
            ok = _const(0 if name == "fail" else 1)

        def call_(fn, args):
            return ir.StmtExpr(loc=loc, expr=ir.ExprCall(
                loc=loc, func=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=fn),
                args=args))
        pre += [call_(OPERAND, [v]) for v in ops]
        pre.append(call_(CAPTURE, [_const(cid), ok]))
        return pre


def _is_str(e) -> bool:
    return isinstance(e, ir.ExprConstant) and isinstance(e.value, str)


def _const(v: int):
    return ir.ExprConstant(value=v)


_CMP = {ir.BinOp.Eq: "==", ir.BinOp.NotEq: "!=", ir.BinOp.Lt: "<",
        ir.BinOp.LtE: "<=", ir.BinOp.Gt: ">", ir.BinOp.GtE: ">="}
_BINOP = {v: k for k, v in _CMP.items()}


# ---- source text -------------------------------------------------------------

class _Sources:
    """The text of a call, and of each of its arguments, read from its file."""

    def __init__(self):
        self.files: Dict[str, Optional[str]] = {}

    def text(self, path: str) -> Optional[str]:
        if path not in self.files:
            try:
                with open(path) as f:
                    self.files[path] = f.read()
            except OSError:
                self.files[path] = None
        return self.files[path]

    def call(self, loc, name: str) -> Tuple[str, List[Tuple[str, int]]]:
        """``(call text, [(arg text, offset in call text)])``; ``(name + "(...)",
        [])`` when the source cannot be read or does not start with ``name``."""
        fallback = (name + "(...)", [])
        path = getattr(loc, "file", None)
        src = self.text(path) if path else None
        if src is None:
            return fallback
        lines = src.split("\n")
        if not 1 <= loc.line <= len(lines):
            return fallback
        start = sum(len(x) + 1 for x in lines[:loc.line - 1]) + loc.pos - 1
        if not src.startswith(name, start):
            return fallback
        i = start + len(name)
        while i < len(src) and src[i].isspace():
            i += 1
        if i >= len(src) or src[i] != "(":
            return fallback
        args, depth, j, a0, q = [], 0, i, i + 1, None
        while j < len(src):
            c = src[j]
            if q:
                if c == "\\":
                    j += 1
                elif c == q:
                    q = None
            elif c == '"':
                q = c
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    args.append((a0, j))
                    break
            elif c == "," and depth == 1:
                args.append((a0, j))
                a0 = j + 1
            j += 1
        else:
            return fallback
        text = src[start:j + 1]
        out = []
        for a, b in args:
            raw = src[a:b]
            lead = len(raw) - len(raw.lstrip())
            t = raw.strip()
            if t:
                out.append((" ".join(t.split()), a + lead - start))
        return " ".join(text.split()) if "\n" in text else text, out


def _split(arg: Optional[Tuple[str, int]], op: str) -> Tuple[Tuple[str, int], Tuple[str, int]]:
    """Split ``a OP b`` at its top-level ``op``; offsets stay in call text."""
    if arg is None:
        return ("", -1), ("", -1)
    t, off = arg
    depth, q, j = 0, None, 0
    while j < len(t):
        c = t[j]
        if q:
            if c == q:
                q = None
        elif c == '"':
            q = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and t.startswith(op, j):
            prev = t[j - 1] if j else ""
            nxt = t[j + len(op)] if j + len(op) < len(t) else ""
            # not part of <<, >>, <=, >=, ==, != when op is one character
            if len(op) == 2 or (prev not in "<>=!" and nxt not in "<>="):
                lhs = t[:j].rstrip()
                k = j + len(op)
                rest = t[k:]
                rhs = rest.lstrip()
                return (lhs, off), (rhs, off + k + len(rest) - len(rhs))
        j += 1
    return (t, off), ("", -1)
