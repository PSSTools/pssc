"""String methods (LRM 7.6.3): which exist, what each returns, and how a call
to one is recognised.

A call is recognised by its RECEIVER's type: `s.size()` is a string method
because `s` is a string, which the call gate's by-name classification
(`call_legality.classify`) cannot see -- by name alone, `size` is no function
of PSS or of the model. So `ExprTypes` types a method's result from here, and
`validate_calls` admits or refuses one from here, before the name is
classified (E1).

`find_all`, `split` and `chars` return a `list`, which no op-model target
holds (ruling R4); they are refused as such.
"""
from __future__ import annotations

from typing import Optional

from .expr_types import INT, STRING, PssType
from .progseq_model import _dt_name

__all__ = ["RESULTS", "LISTS", "method_of", "substring"]

#: Method -> result type, for those an op-model target may render.
RESULTS = {"size": INT, "find": INT, "find_last": INT,
           "lower": STRING, "upper": STRING}

#: Methods that return a list.
LISTS = frozenset({"find_all", "split", "chars"})


def method_of(call, types) -> Optional[str]:
    """The string method ``call`` calls, or None. ``types`` is the body's
    `ExprTypes`."""
    if _dt_name(call) != "ExprCall":
        return None
    f = getattr(call, "func", None)
    if _dt_name(f) != "ExprAttribute" or (
            f.attr not in RESULTS and f.attr not in LISTS):
        return None
    if _dt_name(f.value) in ("TypeExprRefSelf", "TypeExprRefSuper"):
        return None
    t: Optional[PssType] = types.type_of(f.value)
    return f.attr if t is not None and t.kind == "string" else None


def substring(e, types):
    """``(string, lo, hi)`` if ``e`` is a sub-string (8.6.3) -- `s[a..b]`,
    `s[a..]`, `s[..b]`, or `s[i]`, which is `s[i..i]` -- with ``lo``/``hi``
    None for an omitted bound; else None."""
    if _dt_name(e) != "ExprSubscript":
        return None
    t = types.type_of(e.value)
    if t is None or t.kind != "string":
        return None
    sl = e.slice
    if _dt_name(sl) == "ExprSlice":
        return e.value, sl.lower, sl.upper
    return e.value, sl, sl
