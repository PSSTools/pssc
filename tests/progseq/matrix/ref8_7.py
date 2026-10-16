"""A reference model of PSS integer expression evaluation (LRM 8.7).

The expression grid (`grid`) states each case's expected value from this
model, so the four op-model targets are held to the LRM rather than to the
code they share (`pssc.targets.int_semantics`). It is written from the LRM's
text alone and imports nothing from pssc; keep it that way.

A value is an unbounded Python ``int``; a type is ``T(width, signed)``. The
rules, as 8.7.1 states them:

* An expression first has a SELF-DETERMINED type (Tables 21 and 22).
* Where Table 22 propagates a type to an operand, the operand is evaluated
  AT that type: a primary (a variable, literal, cast, call or select) is
  converted to it -- sign-extended if the PROPAGATED type is signed,
  zero-extended if not -- and a compound operand passes the type on to its
  own operands.
* In an assignment-like context (8.7.2, Table 23) a target wider than the
  source propagates its WIDTH, never its sign; the value is then truncated
  or extended to the target.

Shifts by the width or more follow ruling R1 (00-plan.md §7): 0, or the
sign for a signed `>>`.
"""
from __future__ import annotations

from typing import NamedTuple


class T(NamedTuple):
    width: int
    signed: bool

    def __str__(self):
        if self.signed:
            return "int" if self.width == 32 else f"int[{self.width}]"
        return f"bit[{self.width}]"


BOOL = T(1, False)


def bits(v: int, t: T) -> int:
    """``v`` reduced to ``t``'s width: the bit pattern, as a non-negative int."""
    return v & ((1 << t.width) - 1)


def value(v: int, t: T) -> int:
    """The number ``v``'s bit pattern means at ``t``."""
    b = bits(v, t)
    if t.signed and b >> (t.width - 1):
        b -= 1 << t.width
    return b


# --- expressions -------------------------------------------------------------

class Var(NamedTuple):
    """A primary of a declared type: a variable, or a parameter."""
    name: str
    type: T
    val: int


class Cast(NamedTuple):
    type: T
    x: object


class Un(NamedTuple):
    op: str          # '-', '~', '!'
    x: object


class Bin(NamedTuple):
    op: str
    l: object
    r: object


class Cond(NamedTuple):
    c: object
    a: object
    b: object


ARITH = ("+", "-", "*", "/", "%")
BITWISE = ("&", "|", "^")
SHIFT = ("<<", ">>")
REL = ("==", "!=", "<", "<=", ">", ">=")
LOGICAL = ("&&", "||")


def _join(a: T, b: T) -> T:
    """Table 22's binary rule: the larger size; signed only if both are."""
    return T(max(a.width, b.width), a.signed and b.signed)


def self_type(e) -> T:
    if isinstance(e, Var):
        return e.type
    if isinstance(e, Cast):
        return e.type
    if isinstance(e, Un):
        return BOOL if e.op == "!" else self_type(e.x)
    if isinstance(e, Cond):
        return _join(self_type(e.a), self_type(e.b))
    if isinstance(e, Bin):
        if e.op in ARITH or e.op in BITWISE:
            return _join(self_type(e.l), self_type(e.r))
        if e.op in SHIFT or e.op == "**":
            return self_type(e.l)
        if e.op in REL or e.op in LOGICAL:
            return BOOL
    raise TypeError(e)


def _truth(e) -> bool:
    return evaluate(e, self_type(e)) != 0


def _pow(base: int, exp: int) -> int:
    """Table 13 for an integer `**`, on numbers (not bit patterns)."""
    if exp >= 0:
        return base ** exp
    if base == 0:
        raise ValueError("0 ** negative is an error (Table 13)")
    if base == 1:
        return 1
    if base == -1:
        return 1 if exp % 2 == 0 else -1
    return 0


def evaluate(e, t: T) -> int:
    """``e`` evaluated with ``t`` propagated to it: the number, at ``t``.

    ``t`` must be at least ``e``'s self-determined width (8.7.1 only ever
    propagates a size up)."""
    if isinstance(e, Var):
        return _primary(e.val, e.type, t)
    if isinstance(e, Cast):
        return _primary(convert(e.x, e.type), e.type, t)
    if isinstance(e, Un):
        if e.op == "!":
            return _primary(int(not _truth(e.x)), BOOL, t)
        x = evaluate(e.x, t)
        return value(-x if e.op == "-" else ~x, t)
    if isinstance(e, Cond):
        return evaluate(e.a if _truth(e.c) else e.b, t)
    op = e.op
    if op in ARITH or op in BITWISE:
        a, b = evaluate(e.l, t), evaluate(e.r, t)
        if op == "/" or op == "%":
            if b == 0:
                raise ZeroDivisionError("division by zero in a grid case")
            q = abs(a) // abs(b)
            q = q if (a < 0) == (b < 0) else -q
            return value(q if op == "/" else a - q * b, t)
        r = {"+": a + b, "-": a - b, "*": a * b,
             "&": a & b, "|": a | b, "^": a ^ b}[op]
        return value(r, t)
    if op in SHIFT:
        a = evaluate(e.l, t)
        n = evaluate(e.r, self_type(e.r))
        if n < 0:
            raise ValueError("a negative shift amount is not in the grid")
        if op == "<<":
            return value(a << n if n < t.width else 0, t)
        if t.signed:
            return value(a >> min(n, t.width - 1), t)
        return value(bits(a, t) >> n if n < t.width else 0, t)
    if op == "**":
        a = evaluate(e.l, t)
        n = evaluate(e.r, self_type(e.r))
        return value(_pow(a, n), t)
    if op in REL:
        j = _join(self_type(e.l), self_type(e.r))
        a, b = evaluate(e.l, j), evaluate(e.r, j)
        r = {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b,
             ">": a > b, ">=": a >= b}[op]
        return _primary(int(r), BOOL, t)
    if op in LOGICAL:
        r = (_truth(e.l) and _truth(e.r)) if op == "&&" else \
            (_truth(e.l) or _truth(e.r))
        return _primary(int(r), BOOL, t)
    raise TypeError(e)


def _primary(v: int, own: T, t: T) -> int:
    """A primary's value ``v`` of type ``own``, converted to ``t``: extended
    by the PROPAGATED type's sign (8.7.1), so an `int[3]` -4 met at `bit[3]`
    or wider is 4 (Example 45)."""
    return value(v, t) if t.signed else bits(v, own)


def convert(e, target: T) -> int:
    """``e`` as the source of an assignment-like context (8.7.2) whose target
    is ``target``: the number the target holds."""
    st = self_type(e)
    at = T(target.width, st.signed) if target.width > st.width else st
    return value(evaluate(e, at), target)


def self_value(e) -> tuple:
    """``e`` self-determined (a `message` argument): (number, type)."""
    t = self_type(e)
    return evaluate(e, t), t


def hex_at(v: int, t: T) -> str:
    """What `%x` prints for ``v`` at ``t``: unsigned at its width (21.1.1)."""
    return f"{bits(v, t):x}"
