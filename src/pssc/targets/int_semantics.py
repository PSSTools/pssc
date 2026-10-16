"""PSS integer semantics (LRM 8.5.1, 8.7), for any host language.

PSS carries every integer operation out at a width and a signedness that
`ExprTypes.propagate` gives it; a host language does not. This mixin decides,
once, where a rendered value must be converted -- widened, wrapped back into
range, sign-extended -- and asks the backend only how to SPELL each of those.
The decision is driven by each value's known range, so a wrap that cannot
change the value is not emitted.

It was op-model-py's, and it is shared because C and C++ were evaluating PSS
expressions at C's widths instead: `(~p) == q` over `bit[4]` compared ints,
and `~x` of a `bit[32]` written to a 64-bit register lost its upper half.
User ruling (2026-10-07): generated code evaluates in PSS semantics.

A backend mixes `IntSemantics` into its body emitter and supplies the
`int_*` spelling hooks; it must also provide `types` (an `ExprTypes`),
`_final` (a dict, initially empty) and `expr(e)`.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Optional

from ..diag import Refusal
from .expr_types import (CONTEXT_BINARY, CONTEXT_UNARY, LEFT_TYPED,
                         assign_source_type, convert, int_range, is_integral,
                         merge)
from .progseq_model import _dt_name
from .py import pss_int

__all__ = ["IntSemantics", "_Val", "_br"]


# -- integer arithmetic (LRM 8.5.1, 8.7) ---------------------------------------
#
# A host integer does not carry a PSS width -- a Python int has none, and a C
# `uint8_t` is promoted to `int` before any operator sees it -- so every PSS
# operation is carried out at the type
# `ExprTypes.propagate` gives it and brought back into range where it could
# leave it. "Could" is decided from each value's RANGE, which is known at
# generation time far more often than not: a `bit[8]` read is in [0, 255], a
# constant is itself. A wrap that cannot change the value is not emitted, so
# `x = y` stays `x = y` and a counter's `n + 1` gets its `& 0xffffffff`.
#
# Two relaxations make that affordable, and both are exact:
#
# * the RING operators (`+ - * & | ^ << ~`, unary `-`) give the same low N bits
#   whatever the operands' higher bits are, so an operand that is one of them
#   is left unwrapped and the wrap happens once, where the value is used;
# * `//` and `%` ARE PSS's `/` and `%` on operands that cannot be negative,
#   which is every unsigned operation. Only a possibly-negative one needs
#   `_pss_div`, which truncates toward zero.

#: Binary operators that commute with reduction modulo 2**N.
_RING_BIN = {"Add", "Sub", "Mult", "BitAnd", "BitOr", "BitXor", "LShift"}


_FOLD = {
    "Add": lambda a, b: a + b, "Sub": lambda a, b: a - b,
    "Mult": lambda a, b: a * b, "BitAnd": lambda a, b: a & b,
    "BitOr": lambda a, b: a | b, "BitXor": lambda a, b: a ^ b,
    "LShift": lambda a, b: a << b, "RShift": lambda a, b: a >> b,
    "Div": pss_int._pss_div, "Mod": pss_int._pss_mod,
}


@dc.dataclass
class _Val:
    """An integer expression as rendered: its text and what it may hold.

    ``lo``/``hi`` are ``None`` where the range is not known -- which is what
    makes the value be wrapped before anything relies on it.
    """
    text: str
    lo: Optional[int]
    hi: Optional[int]
    atom: bool = True
    const: Optional[int] = None

    def within(self, lo: int, hi: int) -> bool:
        return (self.lo is not None and self.hi is not None
                and lo <= self.lo and self.hi <= hi)

    def nonneg(self) -> bool:
        return self.lo is not None and self.lo >= 0


def _br(v: _Val) -> str:
    return v.text if v.atom else f"({v.text})"


def _mul_range(a: _Val, b: _Val):
    if None in (a.lo, a.hi, b.lo, b.hi):
        return None, None
    p = [a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi]
    return min(p), max(p)


def _bitwise_range(op: str, a: _Val, b: _Val, lo: int, hi: int):
    """The range of `a <op> b`, for `& | ^`. Two's complement on unbounded
    ints: an operand in [0, m] bounds `&` by m whatever the other is, and two
    operands inside a type's range give a result inside it."""
    if op == "BitAnd":
        cands = [v.hi for v in (a, b) if v.nonneg() and v.hi is not None]
        if cands:
            return 0, min(cands)
    if a.nonneg() and b.nonneg() and a.hi is not None and b.hi is not None:
        return 0, (1 << max(a.hi.bit_length(), b.hi.bit_length())) - 1
    if a.within(lo, hi) and b.within(lo, hi):
        return lo, hi
    return None, None


class IntSemantics:
    """The integer half of a body emitter. See the module docstring."""

    # -- spelling hooks: how a backend writes what is decided here -----------

    def int_atomic(self, text: str) -> bool:
        """Whether *text* can be an operand without brackets."""
        raise NotImplementedError

    def int_const(self, c: int, t, hexa: bool = False) -> "_Val":
        """The constant *c*, as an operand of an operation carried out at *t*."""
        raise NotImplementedError

    def int_mask_text(self, v: "_Val", hi: int) -> str:
        """*v* reduced to `[0, hi]`, `hi` being `2**N - 1`."""
        raise NotImplementedError

    def int_sint_text(self, v: "_Val", width: int) -> str:
        """*v*'s low *width* bits, read as a signed number."""
        raise NotImplementedError

    def int_ternary_text(self, test: str, a: "_Val", b: "_Val") -> str:
        raise NotImplementedError

    def int_binop_text(self, op: str, a: "_Val", b: "_Val") -> str:
        """`a <op> b` for an `ExprBin` op name. `Div`/`Mod` reach here only
        with operands that cannot be negative."""
        raise NotImplementedError

    def int_shift_text(self, op: str, a: "_Val", b: "_Val", t, e) -> str:
        """`a << b` or `a >> b` at type *t* (8.5.7), *e* being the `ExprBin`.

        A shift by the width or more is 0, or for a signed `>>` the sign
        (ruling R1). Where the host's own shift already gives that, as
        Python's does, this is `int_binop_text`."""
        return self.int_binop_text(op, a, b)

    def int_signed_div_text(self, op: str, a: "_Val", b: "_Val") -> str:
        """`a / b` or `a % b`, truncating toward zero, on operands that may be
        negative."""
        raise NotImplementedError

    def int_cmp_text(self, op: str, a: "_Val", b: "_Val") -> str:
        """A relational or equality operator over two converted operands."""
        raise NotImplementedError

    def int_leaf_text(self, e, s, t) -> str:
        """A primary of type *s* in an operation carried out at *t*. A host
        language whose own types are narrower than *t* widens it here."""
        return self.expr(e)

    def _arms(self, e, render):
        """The two arms of `?:`, each rendered by *render*."""
        return render(e.body), render(e.orelse)

    def _int_pow(self, e, t) -> "_Val":
        raise Refusal(
            f"in '{getattr(self.fn, 'name', '?')}': `**` is not lowered by "
            f"this target", e)

    # -- the decisions --------------------------------------------------------

    def convert_to(self, e, target) -> str:
        """*e* in an assignment-like context whose target type is *target*
        (LRM 8.7.2): evaluated at the target's width if that is wider, then
        truncated or extended to the target.

        Anything that is not integer-to-integer renders as it stands: an enum,
        a struct, a string, or a value this backend cannot type.
        """
        if type(e).__name__ == "ExprStructLiteral" and target is not None \
                and target.kind == "struct":
            # An aggregate literal's type is where it goes (8.4.2).
            return self.struct_literal_text(e, self.types.resolve(target.dtype))
        v = self._convert_val(e, target)
        return self.expr(e) if v is None else v.text

    def struct_literal_text(self, e, dtype) -> str:
        """`{.a = 1, .b = 2}` as a value of struct *dtype* (8.4.2): a member
        it does not name has its default."""
        raise ValueError(f"in '{getattr(self.fn, 'name', '?')}': a struct "
                         f"literal is not lowered by this target")

    def struct_literal_members(self, e, dtype):
        """``[(field, expr or None)]`` for every member of *dtype*, in
        declaration order: the literal's value, or None where it names none.
        A name the struct does not have is refused."""
        named = {f.name: f.value for f in e.fields}
        have = {f.name for f in dtype.fields}
        bad = sorted(set(named) - have)
        if bad:
            raise Refusal(
                f"in '{getattr(self.fn, 'name', '?')}': '{dtype.name}' has no "
                f"member {', '.join(repr(b) for b in bad)}", e)
        return [(f, named.get(f.name)) for f in dtype.fields]

    def _convert_val(self, e, target) -> Optional[_Val]:
        """`convert_to`, with the range of the result; ``None`` where the
        conversion does not apply."""
        s = self.types.type_of(e)
        if target is None or target.kind != "int" or not is_integral(s):
            return None
        # The source is evaluated at least as wide as the target, so the
        # target's wrap alone decides the value: a ring operator's result
        # need not be wrapped to the source type first.
        return self._fit(self._int_root(e, assign_source_type(target, s),
                                        ring=True), target)

    def converts_as_is(self, e, target) -> bool:
        """Whether `convert_to(e, target)` is *e* rendered as it stands, known
        WITHOUT rendering it. For the statements whose value may be a call
        that must not be hoisted -- which it has to be, if it gets wrapped."""
        s = self.types.type_of(e)
        if target is None or target.kind != "int" or not is_integral(s):
            return True
        return _Val("", *int_range(s)).within(*int_range(target))

    def _int_root(self, e, final, ring: bool = False) -> _Val:
        """*e*, evaluated at *final*: in range, or with *ring*, only right
        modulo 2**N (see `_int_opnd`) for a caller that wraps it anyway."""
        saved = self._final
        self._final = {}
        try:
            self.types.propagate(e, final, self._final)
            return self._int_opnd(e) if ring else self._int_val(e)
        finally:
            self._final = saved

    def _int_compare(self, e) -> Optional[str]:
        """A relational or equality operator over integers (8.5.2, Table 22):
        both operands at the larger width, unsigned unless both are signed.
        ``None`` if an operand is not an integer this backend can type."""
        lt = self.types.type_of(e.lhs)
        rt = self.types.type_of(e.rhs)
        if not (is_integral(lt) and is_integral(rt)):
            return None
        if lt.kind == "enum" or rt.kind == "enum":
            # Enum against enum compares items; there is nothing to convert.
            return None
        p = merge(lt, rt)
        a = self._int_root(e.lhs, p)
        b = self._int_root(e.rhs, p)
        return self.int_cmp_text(e.op.name, a, b)

    def _fit(self, v: _Val, t) -> _Val:
        """*v* brought into the range of *t*, if it may be outside it."""
        lo, hi = int_range(t)
        if v.const is not None:
            c = convert(v.const, t)
            return self.int_const(c, t, hexa=c != v.const and c > 0xffff)
        if v.within(lo, hi):
            return v
        if t.as_int().signed:
            return _Val(self.int_sint_text(v, t.as_int().width), lo, hi)
        return _Val(self.int_mask_text(v, hi), lo, hi, atom=False)

    def _int_val(self, e) -> _Val:
        """*e* at its final type, in range."""
        return self._fit(self._int_raw(e), self._final[id(e)])

    def _int_opnd(self, e) -> _Val:
        """*e* as the operand of a ring operator: left unwrapped if it is one
        too, since the result is the same modulo 2**N (see `_RING_BIN`)."""
        cn = _dt_name(e)
        if ((cn == "ExprBin" and e.op.name in _RING_BIN)
                or (cn == "ExprUnary" and e.op.name in CONTEXT_UNARY)):
            return self._int_raw(e)
        if (cn not in ("ExprBin", "ExprUnary", "ExprIfExp")
                and self.types.type_of(e).as_int().width
                >= self._final[id(e)].as_int().width):
            # A primary no narrower than the operation: extending it changes
            # no bit the operation keeps.
            return self._int_leaf(e, ring=True)
        return self._int_val(e)

    def _int_self(self, e) -> _Val:
        """A self-determined operand -- a shift amount, an exponent."""
        if id(e) in self._final:
            return self._int_val(e)
        text = self.expr(e)
        return _Val(text, None, None, atom=self.int_atomic(text))

    def _int_raw(self, e) -> _Val:
        cn = _dt_name(e)
        if cn == "ExprBin" and (e.op.name in CONTEXT_BINARY
                                or e.op.name in LEFT_TYPED):
            return self._int_bin(e)
        if cn == "ExprUnary" and e.op.name in CONTEXT_UNARY:
            return self._int_unary(e)
        if cn == "ExprIfExp":
            test = self.expr(e.test)
            a, b = self._arms(e, self._int_val)
            lo = hi = None
            if None not in (a.lo, a.hi, b.lo, b.hi):
                lo, hi = min(a.lo, b.lo), max(a.hi, b.hi)
            return _Val(self.int_ternary_text(test, a, b), lo, hi)
        return self._int_leaf(e)

    def _int_leaf(self, e, ring: bool = False) -> _Val:
        """A primary, converted to the type its context gives it (8.7.1):
        sign-extended if that is signed, which a Python int already is, and
        zero-extended if it is unsigned -- its own bit pattern, masked. With
        *ring*, left as it is (see `_int_opnd`)."""
        t = self._final[id(e)]
        s = self.types.type_of(e).as_int()
        v = getattr(e, "value", None) if _dt_name(e) == "ExprConstant" else None
        if isinstance(v, int) and not isinstance(v, bool):
            return self.int_const(v if t.signed else v & ((1 << s.width) - 1), t)
        if _dt_name(e) == "ExprCast":
            # Its range is what the conversion produced, which is often much
            # less than the whole of the cast type: `(bit[32])flag` is 0 or 1.
            cv = self._convert_val(e.value,
                                   self.types.of_datatype(e.target_type))
            if cv is not None:
                cv = dc.replace(cv, text=cv.text if cv.atom
                                else f"({cv.text})", atom=True)
                if not (s.signed and not t.signed and not ring) \
                        or cv.nonneg():
                    return cv
        text = self.int_leaf_text(e, s, t)
        lo, hi = int_range(s)
        if s.signed and not t.signed and not ring:
            m = (1 << s.width) - 1
            return _Val(self.int_mask_text(
                _Val(text, None, None, atom=self.int_atomic(text)), m),
                0, m, atom=False)
        return _Val(text, lo, hi, atom=self.int_atomic(text))

    def _int_unary(self, e) -> _Val:
        a = self._int_opnd(e.operand)
        op = e.op.name
        if op == "UAdd":
            return a
        if op == "USub":
            if a.const is not None:
                return self.int_const(-a.const, self._final[id(e)].as_int())
            lo = -a.hi if a.hi is not None else None
            hi = -a.lo if a.lo is not None else None
            return _Val(f"-{_br(a)}", lo, hi, atom=False)
        if a.const is not None:
            return self.int_const(~a.const, self._final[id(e)].as_int())
        lo = -a.hi - 1 if a.hi is not None else None
        hi = -a.lo - 1 if a.lo is not None else None
        return _Val(f"~{_br(a)}", lo, hi, atom=False)

    def _int_bin(self, e) -> _Val:
        op = e.op.name
        t = self._final[id(e)].as_int()
        tlo, thi = int_range(t)
        if op == "Exp":
            return self._int_pow(e, t)
        if op in _RING_BIN and op != "LShift":
            a, b = self._int_opnd(e.lhs), self._int_opnd(e.rhs)
        elif op == "LShift":
            a, b = self._int_opnd(e.lhs), self._int_self(e.rhs)
        elif op == "RShift":
            a, b = self._int_val(e.lhs), self._int_self(e.rhs)
        else:                                   # Div, Mod
            a, b = self._int_val(e.lhs), self._int_val(e.rhs)
        if a.const is not None and b.const is not None:
            if op in ("Div", "Mod") and b.const == 0:
                raise Refusal(
                    f"in '{getattr(self.fn, 'name', '?')}': division by zero "
                    f"in a constant expression (LRM 8.5.1)", e)
            if op in ("LShift", "RShift") and b.const < 0:
                raise Refusal(
                    f"in '{getattr(self.fn, 'name', '?')}': a negative shift "
                    f"amount (LRM 8.5.7)", e)
            return self.int_const(_FOLD[op](a.const, b.const), t)
        lo = hi = None
        if op == "Add" and None not in (a.lo, a.hi, b.lo, b.hi):
            lo, hi = a.lo + b.lo, a.hi + b.hi
        elif op == "Sub" and None not in (a.lo, a.hi, b.lo, b.hi):
            lo, hi = a.lo - b.hi, a.hi - b.lo
        elif op == "Mult":
            lo, hi = _mul_range(a, b)
        elif op in ("BitAnd", "BitOr", "BitXor"):
            lo, hi = _bitwise_range(op, a, b, tlo, thi)
        elif op == "LShift":
            if b.const is not None and b.const >= 0 and None not in (a.lo, a.hi):
                lo, hi = a.lo << b.const, a.hi << b.const
        elif op == "RShift":
            # `>>` of an in-range value stays in range; Python's is
            # arithmetic on a negative one, which is 8.5.7's fill with ones.
            lo, hi = min(a.lo, 0), max(a.hi, 0)
            if b.const is not None and b.const >= 0:
                lo, hi = a.lo >> b.const, a.hi >> b.const
        else:                                   # Div, Mod
            # |a / b| <= |a|, and a remainder has a's sign and at most its
            # size -- so both stay in the type, but for the one quotient
            # that cannot: the most negative value divided by -1.
            lo, hi = min(a.lo, -a.hi, 0), max(a.hi, -a.lo, 0)
            if op == "Mod":
                lo, hi = min(a.lo, 0), max(a.hi, 0)
            elif a.lo > tlo or b.lo > -1 or b.hi < -1:
                lo, hi = max(lo, tlo), min(hi, thi)
            if a.nonneg() and b.nonneg():
                return _Val(self.int_binop_text(op, a, b), lo, hi,
                            atom=False)
            return _Val(self.int_signed_div_text(op, a, b), lo, hi)
        if op in ("LShift", "RShift"):
            return _Val(self.int_shift_text(op, a, b, t, e), lo, hi,
                        atom=False)
        return _Val(self.int_binop_text(op, a, b), lo, hi, atom=False)
