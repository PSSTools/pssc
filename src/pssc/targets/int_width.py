"""Integers a target's integer types cannot hold, found before anything is emitted.

C and C++ carry a PSS integer in a fixed-width host type, the widest being 64
bits. A `bit[256]` used to be declared `uint64_t`: it compiled, and every value
lost its top 192 bits with no diagnostic. A target with a limit refuses such a
model instead (user ruling, 2026-10-07: refusing is enough; no wide
representation).

The walk covers what the op-model backends render: every field of every
component in the tree, the struct types those reach, and each function's
parameters, result and body (a local's type, a cast's target). It never
descends into a component or action type found along the way -- a component
in the tree is walked as itself, and actions are not rendered.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional

from ..diag import at
from .progseq_model import _dt_name

_DT_INT = "DataTypeInt"
_DT_STRUCT = "DataTypeStruct"

#: Datatypes a walk never enters: what they hold is rendered (or not) as
#: itself, not as part of what refers to them.
_OPAQUE = ("DataTypeComponent", "DataTypeAction", "DataTypeClass")


def _name(dtype) -> str:
    return (getattr(dtype, "name", None) or "?").split("::")[-1]


def too_wide(model, limit: int) -> List[str]:
    """``"<where>: bit[N]"`` for each integer wider than ``limit`` bits the
    model's rendering would need, in model order, each place once."""
    out: List[str] = []
    seen_structs = set()

    def check_int(dtype, where: str, node) -> None:
        bits = int(getattr(dtype, "bits", 32) or 32)
        if bits > limit:
            kind = "int" if getattr(dtype, "signed", False) else "bit"
            out.append(at(node, f"{where}: {kind}[{bits}]"))

    def struct(dtype) -> None:
        if id(dtype) in seen_structs:
            return
        seen_structs.add(id(dtype))
        for f in getattr(dtype, "fields", None) or []:
            datatype(getattr(f, "datatype", None), f"{_name(dtype)}.{f.name}",
                     (f, dtype))

    def datatype(dtype, where: str, node) -> None:
        """``node``: where a refusal is located (a node or a sequence)."""
        cn = _dt_name(dtype)
        if cn == _DT_INT:
            check_int(dtype, where, node)
        elif cn == _DT_STRUCT:
            struct(dtype)
        else:
            # An array or a channel: its element type, under the same name.
            for attr in ("element_type", "elem_type", "type"):
                sub = getattr(dtype, attr, None)
                if sub is not None and dc.is_dataclass(sub):
                    datatype(sub, where, node)

    def body(node, where: str, fn) -> None:
        seen = set()

        def walk(n, near=fn) -> None:
            if n is None or isinstance(n, (str, int, float, bool, type)):
                return
            if isinstance(n, (list, tuple, set)):
                for c in n:
                    walk(c, near)
                return
            if isinstance(n, dict):
                for c in n.values():
                    walk(c, near)
                return
            if not dc.is_dataclass(n) or id(n) in seen:
                return
            seen.add(id(n))
            cn = type(n).__name__
            if cn in _OPAQUE:
                return
            if cn in (_DT_INT, _DT_STRUCT):
                datatype(n, where, near)
                return
            # The innermost node with a location: a statement, an
            # expression, a parameter.
            if getattr(getattr(n, "loc", None), "line", 0):
                near = n
            for f in dc.fields(n):
                walk(getattr(n, f.name, None), near)

        walk(node)

    funcs: List[Any] = []
    for comp in model.comp_dtypes_root_first:
        cname = _name(comp)
        for f in getattr(comp, "fields", None) or []:
            datatype(getattr(f, "datatype", None), f"{cname}.{f.name}",
                     (f, comp))
        funcs += [(cname, fn) for fn in getattr(comp, "functions", None) or []]
    funcs += [(None, fn) for fn in getattr(model, "functions", ()) or ()]
    for owner, fn in funcs:
        where = f"{owner}::{fn.name}" if owner else str(fn.name)
        body([getattr(fn, "args", None), getattr(fn, "returns", None),
              getattr(fn, "body", None)], where, fn)
    return list(dict.fromkeys(out))


def refuse_too_wide(model, limit: int, language: str) -> None:
    """Raise `CompileError` if ``model`` needs an integer wider than ``limit``
    bits, naming every place that does."""
    found = too_wide(model, limit)
    if not found:
        return
    from ..driver import CompileError
    raise CompileError(
        f"{language} has no integer type wider than {limit} bits, and "
        f"{'this value' if len(found) == 1 else 'these values'} would lose "
        f"bits silently", found)
