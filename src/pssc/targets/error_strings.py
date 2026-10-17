"""`error()` / `fatal()` strings are known at solve time (LRM 21.3; ruling
R15), checked once for every op-model target.

"The format_str parameter shall be a string expression whose value is known
at solve time. If there are strings ... among the subsequent data
parameters, they shall also be known at solve time. In other words, when
used in target contexts, the string values of those parameters shall be
constant at run time." The rule lets a target with no run-time string
formatting print the message. A model that broke it ran on op-model-py and
-sv, which format at run time, and was refused on C for an unrelated reason
(R3).

In target code -- what `field_writes.target_code` reaches -- `refusals`
names each `error()`/`fatal()` whose format, or a string argument, reads a
local, a parameter, a component field that is not a constant, or a call's
result. A literal, a `static const` and a package constant are known at
solve time. Solve context (a constructor, an init block) may use anything.
"""
from __future__ import annotations

from typing import List

from ..diag import at
from .field_writes import _params, _walk, reached_from, target_code
from .progseq_model import _dt_name

__all__ = ["refusals"]

#: The built-in -> the index of its format argument.
_FORMAT_AT = {"error": 0, "fatal": 1}


def refusals(comps, ctx, entries=(), ctor_names=None) -> List[str]:
    """One message per offending argument of an `error()`/`fatal()` call in
    target code."""
    from .expr_types import ExprTypes
    from .validate_calls import callee_name
    msgs, seen = [], set()
    for c, f, rc, rf in target_code(comps, ctx, entries, ctor_names):
        types = None
        for n in _walk(getattr(f, "body", None)):
            if _dt_name(n) != "ExprCall" or id(n) in seen:
                continue
            name = callee_name(n.func)
            if name not in _FORMAT_AT:
                continue
            seen.add(id(n))
            for k, a in enumerate(n.args):
                if k < _FORMAT_AT[name]:
                    continue
                if k > _FORMAT_AT[name]:
                    types = types or ExprTypes(f, c, ctx)
                    t = types.type_of(a)
                    if t is None or t.kind != "string":
                        continue
                if not _run_time(a, f, c):
                    continue
                what = ("format" if k == _FORMAT_AT[name]
                        else f"string argument {k + 1}")
                msgs.append(at((a, n, f),
                    f"{getattr(c, 'name', '?')}::{f.name}: {name}()'s {what} "
                    f"is not known at solve time{reached_from(f, rc, rf)}; in "
                    f"target code the format and string arguments of "
                    f"error() and fatal() shall be constant at run time "
                    f"(LRM 21.3; ruling R15)"))
    return msgs


def _run_time(e, fn, comp) -> bool:
    """True if ``e`` reads something whose value is only known at run time."""
    params = _params(fn)
    fields = {fl.name: fl for fl in getattr(comp, "fields", None) or []}
    for n in _walk(e):
        cn = _dt_name(n)
        if cn in ("ExprRefLocal", "ExprCall", "ExprMemberCall"):
            return True
        if cn == "ExprAttribute" and _dt_name(n.value) == "TypeExprRefSelf":
            if n.attr in params:
                return True
            fl = fields.get(n.attr)
            if fl is not None and not getattr(fl, "is_const", False):
                return True
    return False
