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

import dataclasses as dc
from typing import Iterator, Optional, Tuple

from .progseq_model import _dt_name

__all__ = ["item_of", "prefixed_item", "written_enums"]


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


def prefixed_item(enum_name: str, item: str) -> str:
    """An item named for a language with one namespace for every enum's
    items (C, SV): the enum's name without `_e`, upper-cased, then the item.
    `op_e` + `RD` -> `OP_RD`. It depends on the enum alone, so adding an enum
    to a model never renames another's items."""
    stem = enum_name[:-2] if enum_name.endswith("_e") else enum_name
    return f"{stem.upper()}_{item}"


def written_enums(node, type_map) -> Iterator[object]:
    """The enum of each item written under *node* (a body, an initializer,
    or a list of them), in the order written; repeats included.

    A target that spells an item by its enum's name must declare that enum,
    whether or not a signature or a declaration names its type. The one item
    no target renders is `message()`'s verbosity (21.1.3), which is skipped:
    it would declare `message_verbosity_e` in every model that logs.
    """
    from .validate_calls import callee_name

    def walk(n):
        if isinstance(n, (list, tuple)):
            for x in n:
                yield from walk(x)
            return
        if not dc.is_dataclass(n) or isinstance(n, type):
            return
        cn = _dt_name(n)
        if cn == "ExprConstant":
            found = item_of(n, type_map)
            if found is not None:
                yield found[0]
            return
        if cn == "ExprCall" and _is_message(n.func, callee_name):
            yield from walk(list(n.args or [])[1:])
            return
        for f in dc.fields(n):
            yield from walk(getattr(n, f.name, None))

    yield from walk(node)


def _is_message(func, callee_name) -> bool:
    cn = _dt_name(func)
    if cn == "ExprRefUnresolved":
        return callee_name(func) == "message"
    return cn == "ExprAttribute" and func.attr == "message"
