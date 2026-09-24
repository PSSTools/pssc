"""Static PSS types of the expressions in an operation body (LRM 8.7).

An operation-model backend renders values in a host language whose integers do
not carry PSS widths: a Python `int` is unbounded, and a C `uint64_t` is not a
`bit[12]`. Where the PSS type decides the result, the backend has to know it.
`message()` is the first such place -- `%d` on a `bit[8]` holding 255 and on an
`int` holding -1 print differently even when the host value is the same -- and
the width semantics of assignment and arithmetic are the next.

Language-neutral, like `body_walker.py`: it answers "what is the PSS type of
this expression", never how a backend spells it.

The rules are the ones the bc backend implements (`zuspec.be.bc.lower.types`),
which the compliance corpus checks:

* an unsized constant is `int` (32-bit signed), wider if its value needs it
  (Table 21). The IR does not keep a literal's radix, so a hex literal, which
  the LRM types `bit[N]`, is typed the same way;
* a binary arithmetic or bitwise result has the larger operand width and is
  signed only if both operands are (Table 22);
* a shift or a power has its left operand's type; a comparison or logical operator is
  `bool`;
* a cast has its target type; a call has its callee's return type.

`type_of` returns ``None`` for an expression it cannot type, and the caller
decides whether that is an error. It never guesses.

`propagate` goes one step further, to the type each operation is CARRIED OUT
at: an operand of `+` takes the width of the whole expression, including the
width an assignment's target lends it (8.7.1, 8.7.2).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, Optional, Tuple

from .progseq_model import _dt_name, struct_fields


@dc.dataclass(frozen=True)
class PssType:
    kind: str                     # "int" | "bool" | "enum" | "string" | "struct" | "array" | "comp"
    width: int = 32
    signed: bool = True
    items: Optional[Tuple[Tuple[str, int], ...]] = None   # enum (name, value)
    dtype: Any = None             # the IR datatype, for struct/array/comp/register

    def as_int(self) -> "PssType":
        """The integer type arithmetic sees: an enum is its value, a bool a bit."""
        if self.kind == "bool":
            return PssType("int", 1, False)
        if self.kind == "enum":
            return PssType("int", 32, True)
        return self


BOOL = PssType("bool", 1, False)
STRING = PssType("string", 0, False)
INT = PssType("int", 32, True)
U64 = PssType("int", 64, False)

_COMPARE = {"Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "And", "Or"}
#: Relational and equality operators: `bool`, over operands of a common type.
RELATIONAL = {"Eq", "NotEq", "Lt", "LtE", "Gt", "GtE"}
#: Binary operators whose operands take the expression's own type (Table 22).
CONTEXT_BINARY = {"Add", "Sub", "Mult", "Div", "Mod",
                  "BitAnd", "BitOr", "BitXor"}
#: Binary operators whose type is their LEFT operand's, which alone is
#: context-determined; the right one is self-determined (Table 22).
LEFT_TYPED = {"LShift", "RShift", "Exp"}
#: Unary operators whose operand takes the expression's type (Table 22).
CONTEXT_UNARY = {"USub", "UAdd", "Invert"}

#: Built-ins whose result type the model does not declare.
_BUILTIN_RESULT = {
    "read8": PssType("int", 8, False), "read16": PssType("int", 16, False),
    "read32": PssType("int", 32, False), "read64": U64,
    "addr_value": U64,
}


def literal_type(v) -> Optional[PssType]:
    if isinstance(v, bool):
        return BOOL
    if isinstance(v, str):
        return STRING
    if not isinstance(v, int):
        return None
    n = v.bit_length() + 1 if v >= 0 else (-v - 1).bit_length() + 1
    if n <= 32:
        return INT
    if n <= 64:
        return PssType("int", n, True)
    return U64


def merge(a: PssType, b: PssType) -> PssType:
    a, b = a.as_int(), b.as_int()
    return PssType("int", max(a.width, b.width), a.signed and b.signed)


def is_integral(t: Optional[PssType]) -> bool:
    """Is *t* a type integer arithmetic applies to (an enum or bool counts)?"""
    return t is not None and t.kind in ("int", "bool", "enum")


def int_range(t: PssType) -> Tuple[int, int]:
    """The values of *t*, as integers: ``(lowest, highest)``."""
    t = t.as_int()
    if t.signed:
        return -(1 << (t.width - 1)), (1 << (t.width - 1)) - 1
    return 0, (1 << t.width) - 1


def assign_source_type(target: PssType, source: PssType) -> PssType:
    """The type a source expression is evaluated at in an assignment-like
    context (8.7.2): the target's width if that is larger, the source's own
    signedness either way."""
    source = source.as_int()
    return PssType("int", max(target.as_int().width, source.width),
                   source.signed)


def convert(v: int, t: PssType) -> int:
    """The integer *v* truncated or extended to *t* (8.7.2): its low
    ``t.width`` bits, read as *t* reads them."""
    t = t.as_int()
    v &= (1 << t.width) - 1
    if t.signed and v >> (t.width - 1):
        v -= 1 << t.width
    return v


class ExprTypes:
    """Types for the expressions of one body: ``fn`` running in ``comp``."""

    def __init__(self, fn, comp, ctx=None, loop_vars=None):
        self.comp = comp
        tm = getattr(ctx, "type_map", {}) or {}
        self._tm = tm
        self.functions = getattr(ctx, "functions", {}) or {}
        self.args: Dict[str, Any] = {
            a.arg: a.annotation
            for a in (getattr(getattr(fn, "args", None), "args", None) or [])}
        self.locals: Dict[str, Any] = {}
        #: The loop variables in scope where an expression is being typed, by
        #: IR name: a `repeat` or array `foreach` index is `int` (20.7.6,
        #: 20.7.8 d), a `foreach` iterator the element type. Declared by their
        #: loop rather than by a statement, so a LIVE mapping, owned by the
        #: walk that knows the scope; the front end gives a loop variable that
        #: shadows another local a name of its own, so inside its loop the
        #: name is the loop's.
        self.loop_vars = loop_vars if loop_vars is not None else {}
        self._collect_locals(getattr(fn, "body", None))

    # -- declarations ------------------------------------------------------

    def _collect_locals(self, node) -> None:
        if isinstance(node, (list, tuple)):
            for n in node:
                self._collect_locals(n)
            return
        if not dc.is_dataclass(node) or isinstance(node, type):
            return
        if _dt_name(node) == "StmtAnnAssign":
            tgt = getattr(node, "target", None)
            if _dt_name(tgt) == "ExprRefLocal":
                self.locals[tgt.name] = node.annotation
        for f in dc.fields(node):
            self._collect_locals(getattr(node, f.name))

    def resolve(self, dtype):
        ref = getattr(dtype, "ref_name", None)
        if ref and ref in self._tm:
            return self._tm[ref]
        return dtype

    def of_datatype(self, dtype) -> Optional[PssType]:
        dt = self.resolve(dtype)
        cn = _dt_name(dt)
        if cn == "DataTypeInt":
            if getattr(dt, "name", None) == "bool":
                return BOOL
            bits = getattr(dt, "bits", None) or 32
            return PssType("int", int(bits), bool(getattr(dt, "signed", False)))
        if cn == "DataTypeEnum":
            items = tuple((k, int(v)) for k, v in
                          (getattr(dt, "items", {}) or {}).items())
            return PssType("enum", 32, True, items, dt)
        if cn == "DataTypeString":
            return STRING
        if cn == "DataTypeStruct":
            return PssType("struct", dtype=dt)
        if cn == "DataTypeArray":
            return PssType("array", dtype=dt)
        if cn in ("DataTypeComponent", "DataTypeRegisterGroup",
                  "DataTypeRegister"):
            return PssType("comp", dtype=dt)
        return None

    # -- expressions -------------------------------------------------------

    def type_of(self, e) -> Optional[PssType]:
        hook = getattr(self, "_" + _dt_name(e), None)
        return hook(e) if hook is not None else None

    def _ExprConstant(self, e):
        return literal_type(e.value)

    def _ExprRefLocal(self, e):
        if e.name in self.loop_vars:
            return self.loop_vars[e.name]
        if e.name in self.locals:
            return self.of_datatype(self.locals[e.name])
        if e.name in self.args:
            return self.of_datatype(self.args[e.name])
        return None

    def _ExprAttribute(self, e):
        if _dt_name(e.value) == "TypeExprRefSelf":
            # A parameter reaches the IR as an attribute of `self`.
            if e.attr in self.args:
                return self.of_datatype(self.args[e.attr])
            return self._field(self.comp, e.attr)
        base = self.type_of(e.value)
        if base is None or base.dtype is None:
            return None
        return self._field(base.dtype, e.attr)

    def _field(self, dtype, name) -> Optional[PssType]:
        dt = self.resolve(dtype)
        fields = (struct_fields(dt, self._tm) if _dt_name(dt) == "DataTypeStruct"
                  else getattr(dt, "fields", None) or [])
        for f in fields:
            if f.name == name:
                return self.of_datatype(f.datatype)
        return None

    def _ExprSubscript(self, e):
        base = self.type_of(e.value)
        if base is None or base.kind != "array":
            return None
        dt = base.dtype
        elem = getattr(dt, "element_type", None)
        return self.of_datatype(elem) if elem is not None else None

    def _TypeExprRefSelf(self, e):
        return PssType("comp", dtype=self.comp)

    def _TypeExprRefSuper(self, e):
        """`super`: the base component of the one this body is rendered in
        (a backend rendering inheritance natively renders a body in the class
        that declares it)."""
        from .comp_inherit import user_base
        base = user_base(self._tm, self.comp) if self.comp is not None else None
        return PssType("comp", dtype=base) if base is not None else None

    def _ExprCast(self, e):
        return self.of_datatype(e.target_type)

    def _ExprUnary(self, e):
        if e.op.name == "Not":
            return BOOL
        t = self.type_of(e.operand)
        return t.as_int() if is_integral(t) else None

    def _ExprBin(self, e):
        op = e.op.name
        if op in _COMPARE:
            return BOOL
        lt = self.type_of(e.lhs)
        if op in LEFT_TYPED:
            return lt.as_int() if is_integral(lt) else None
        rt = self.type_of(e.rhs)
        if not (is_integral(lt) and is_integral(rt)):
            return None
        return merge(lt, rt)

    def _ExprBool(self, e):
        return BOOL

    def _ExprCompare(self, e):
        return BOOL

    def _ExprIfExp(self, e):
        a, b = self.type_of(e.body), self.type_of(e.orelse)
        if a is None or b is None:
            return None
        if a.kind != "int" and a == b:
            return a
        if not (is_integral(a) and is_integral(b)):
            return None
        return merge(a, b)

    def _ExprCall(self, e):
        func = e.func
        name = getattr(func, "attr", None) or getattr(func, "name", None)
        if name in _BUILTIN_RESULT:
            return _BUILTIN_RESULT[name]
        if _dt_name(func) == "ExprAttribute":
            recv = (PssType("comp", dtype=self.comp)
                    if _dt_name(func.value) == "TypeExprRefSelf"
                    else self.type_of(func.value))
            # `super.f(...)` is typed by the base's `f`.
            if recv is not None and recv.dtype is not None:
                t = self._method_result(recv.dtype, name)
                if t is not None:
                    return t
        fn = self.functions.get(name)
        if fn is not None:
            return self._returns(fn)
        return None

    def _method_result(self, dtype, name) -> Optional[PssType]:
        dt = self.resolve(dtype)
        cn = _dt_name(dt)
        if cn == "DataTypeRegister" and name in ("read_val", "read"):
            bits = getattr(dt, "size_bits", None) or 32
            if name == "read":
                vt = self.of_datatype(getattr(dt, "register_value_type", None))
                if vt is not None:
                    return vt
            return PssType("int", int(bits), False)
        for fn in getattr(dt, "functions", None) or []:
            if fn.name == name:
                return self._returns(fn)
        return None

    def _returns(self, fn) -> Optional[PssType]:
        rt = getattr(fn, "returns", None)
        return self.of_datatype(rt) if rt is not None else None

    # -- final types (8.7.1) -----------------------------------------------

    def propagate(self, e, final: PssType, out: Dict[int, PssType]) -> None:
        """Record in *out*, by `id`, the FINAL type of *e* and of each integer
        operand under it that takes its type from context.

        *final* is the type *e* is evaluated at: its own type when it is
        self-determined, or the type its context propagates to it. Table 22
        says which operands inherit it; the rest -- a shift amount, an
        exponent, the operands of a comparison or of `&&` -- start again from
        their own type. A comparison's operands share the larger width, and
        are signed only if both are.

        Stops at a primary (a reference, a call, a cast, a constant): what is
        inside one is evaluated in a context of its own, which whoever renders
        it asks for separately.
        """
        out[id(e)] = final
        cn = _dt_name(e)
        if cn == "ExprBin":
            op = e.op.name
            if op in CONTEXT_BINARY:
                self.propagate(e.lhs, final, out)
                self.propagate(e.rhs, final, out)
            elif op in LEFT_TYPED:
                self.propagate(e.lhs, final, out)
                self._self_determined(e.rhs, out)
        elif cn == "ExprUnary" and e.op.name in CONTEXT_UNARY:
            self.propagate(e.operand, final, out)
        elif cn == "ExprIfExp":
            self.propagate(e.body, final, out)
            self.propagate(e.orelse, final, out)

    def _self_determined(self, e, out) -> None:
        t = self.type_of(e)
        if is_integral(t):
            self.propagate(e, t.as_int(), out)

