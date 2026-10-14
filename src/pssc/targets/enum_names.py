"""Which enum item a folded constant is (LRM 7.5).

ast2ir folds a reference to an enum item to its value and keeps the item the
linker resolved in `ExprConstant.ref` (`pkg::mode_e::FAST`). This module is
the one place that reads that back: every op-model backend spells an item by
name from what it returns, never by looking its value up -- two items may
share a value, and an item of one enum may share a NAME with another's.

Language-neutral, like `expr_types.py`: it says which item, never how a
language spells it.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .progseq_model import _dt_name

__all__ = ["item_of"]


def item_of(c, type_map) -> Optional[Tuple[object, str]]:
    """``(enum dtype, item name)`` of the folded enum item *c*, or None.

    *c* is an item if its `ref` names an item of a `DataTypeEnum` in
    *type_map* that has *c*'s value. A `static const` also carries a `ref`;
    it names no enum, and is not an item.
    """
    ref = getattr(c, "ref", None)
    head, _, item = (ref or "").rpartition("::")
    if not head:
        return None
    tm = type_map or {}
    for name in (head, head.rpartition("::")[2]):
        dt = tm.get(name)
        if _dt_name(dt) == "DataTypeEnum":
            items = getattr(dt, "items", {}) or {}
            if item in items and items[item] == getattr(c, "value", None):
                return dt, item
            return None
    return None
