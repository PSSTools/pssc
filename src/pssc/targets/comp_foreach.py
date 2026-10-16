"""`foreach (c : comps[i])` over an array of components, lowered once for
every op-model target.

The iterator of a `foreach` over an array of components (20.7.8) is a
reference to the element, not a copy of a value: `c.id = i` writes the
element's field. Every target binds an iterator as a value it reads, which
has no meaning for a component (C, C++ and SV had no type to declare it with;
Python refused it). So `lower` rewrites the loop into its index form, each
use of `c` in the body becoming `comps[i]` -- a sub-component reached by
index, which every target already renders (W36). A loop with no index gets
one (`_pss_ci<n>`).
"""
from __future__ import annotations

import copy
import dataclasses as dc
import itertools

import zuspec.ir.core as ir

from .array_methods import _rewrite
from .expr_types import ExprTypes
from .progseq_model import _dt_name

__all__ = ["lower"]


def lower(ctx) -> None:
    """Rewrite every `foreach` with an iterator over an array of components
    in the bodies of ``ctx``'s types."""
    tm = getattr(ctx, "type_map", {}) or {}
    for dtype in list(tm.values()):
        for fn in list(getattr(dtype, "functions", None) or ()):
            if getattr(fn, "body", None):
                types = ExprTypes(fn, dtype, ctx)
                _lower(fn.body, types, itertools.count())


def _lower(node, types, counter) -> None:
    if isinstance(node, list):
        for x in node:
            _lower(x, types, counter)
        return
    if not dc.is_dataclass(node) or isinstance(node, type) \
            or _dt_name(node).startswith("DataType"):
        return
    for f in dc.fields(node):
        if f.name not in ("loc", "provenance", "doc", "doc_trailing"):
            _lower(getattr(node, f.name, None), types, counter)
    if _dt_name(node) == "StmtForeach":
        _rewrite_loop(node, types, counter)


def _is_comp_array(e, types) -> bool:
    t = types.type_of(e)
    if t is None or t.kind != "array":
        return False
    elem = types.of_datatype(getattr(types.resolve(t.dtype), "element_type",
                                     None))
    return elem is not None and elem.kind == "comp"


def _rewrite_loop(s, types, counter) -> None:
    from .body_walker import foreach_parts
    index, it = foreach_parts(s)
    if it is None or _dt_name(it) != "ExprRefLocal" \
            or not _is_comp_array(s.iter, types):
        return
    if index is None:
        index = ir.ExprRefLocal(name=f"_pss_ci{next(counter)}")
    name = it.name

    def elem(e):
        if _dt_name(e) == "ExprRefLocal" and e.name == name:
            return ir.ExprSubscript(value=copy.deepcopy(s.iter),
                                    slice=ir.ExprRefLocal(name=index.name))
        return None
    _rewrite(s.body, elem)
    s.target = index
    s.index_var = index
