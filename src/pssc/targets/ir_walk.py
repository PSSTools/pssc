"""What a generic walk over IR nodes descends into.

Every IR node carries some fields that describe it rather than being part of
it: where it came from (``loc``, ``provenance``), its prose (``doc``,
``doc_trailing``) and its source annotations (``annotations``, LRM 7.13).
A walk that looks for calls, writes or subscripts must not descend into
them: ``provenance.source_nodes`` holds real IR nodes from *other* bodies,
and an annotation's values are not code.

Five walkers kept a copy of that list each, and four had none; adding
``annotations`` to ``Base`` would have meant finding all nine. Use
:func:`children` (or :data:`NON_CHILD_FIELDS`) instead.

``comp_inherit._rewrite`` does not use it, on purpose: it copies a base's
body into a derived component, and a node's ``provenance`` (the fields of a
reduced register write, for instance) names the body's own fields, so it is
rewritten along with the body.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Iterator, Tuple

#: Base fields that describe a node and are not its children.
NON_CHILD_FIELDS = frozenset(
    ("loc", "doc", "doc_trailing", "provenance", "annotations"))


def child_fields(node) -> Iterator[Tuple[str, Any]]:
    """``(name, value)`` for each field of dataclass *node* that may hold a
    child: every field but :data:`NON_CHILD_FIELDS`."""
    for f in dc.fields(node):
        if f.name not in NON_CHILD_FIELDS:
            yield f.name, getattr(node, f.name, None)


def children(node) -> Iterator[Any]:
    """The values of :func:`child_fields`."""
    for _, v in child_fields(node):
        yield v
