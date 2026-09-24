"""PSS integer arithmetic (LRM 8.5.1, 8.7) for the generated Python module.

A Python `int` has no width and `//` rounds toward minus infinity. PSS integers
have a width and a signedness, and `/` truncates toward zero. The body emitter
(`lower_progseq.py`) works out the type each operation is carried out at and
spells most of the difference inline -- `& 0xff` for an unsigned wrap, plain
`//` where both operands are provably non-negative. What cannot be said in an
operator is here.

ONE source, used twice, like `pss_format.py`: the generator folds a constant
expression with these functions, and each function a generated body calls is
copied into that module, because the module imports nothing outside the
standard library. A function here therefore uses nothing but builtins.

Every value is carried at its type's INTERPRETATION: an unsigned `bit[N]` is in
``[0, 2**N)`` and a signed `int[N]` in ``[-2**(N-1), 2**(N-1))``, so a Python
comparison or print of it is already the PSS one.
"""
from __future__ import annotations

import inspect
from typing import Dict


def _pss_sint(v, n):
    """*v* as an *n*-bit two's-complement value (LRM 8.5.1.1)."""
    v &= (1 << n) - 1
    return v - (1 << n) if v >> (n - 1) else v


def _pss_div(a, b):
    """Integer division, truncating toward zero (LRM 8.5.1)."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _pss_mod(a, b):
    """The remainder of `_pss_div`, with the sign of *a* (LRM 8.5.1)."""
    return a - b * _pss_div(a, b)


def _pss_pow(a, b, n):
    """`a ** b` on integers, as an *n*-bit pattern (LRM 8.5.1, Table 13).

    Python's `**` gives a float for a negative *b*; Table 13 gives an integer.
    """
    if b >= 0:
        return pow(a, b, 1 << n)
    if a == 0:
        raise ZeroDivisionError("0 ** a negative exponent is undefined "
                                "(LRM 8.5.1, Table 13)")
    if a == 1 or (a == -1 and b % 2 == 0):
        return 1
    if a == -1:
        return (1 << n) - 1
    return 0


#: The functions a generated module may call, by name.
HELPERS: Dict[str, object] = {
    f.__name__: f for f in (_pss_sint, _pss_div, _pss_mod, _pss_pow)}


def inline_source(names) -> str:
    """The source of the helpers in *names*, in a fixed order, for a module."""
    names = set(names)
    if "_pss_mod" in names:
        names.add("_pss_div")
    return "\n\n".join(inspect.getsource(f) for n, f in HELPERS.items()
                       if n in names)
