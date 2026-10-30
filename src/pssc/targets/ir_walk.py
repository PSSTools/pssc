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

:func:`walk` is the published pre-order walk over them, and
:func:`rebuild` / :class:`Transformer` the rebuilding side: a pass that
rewrites a body (effect sequencing, ``docs/design/effect-sequencing.md``)
gets a copy that keeps each node's ``loc``, ``provenance`` and
``annotations``.

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


def _is_node(v) -> bool:
    return dc.is_dataclass(v) and not isinstance(v, type)


def walk(node, into_types: bool = False) -> Iterator[Any]:
    """Each IR node under *node* (a node, or a list or tuple of them),
    pre-order and in field order, so a body's statements and an expression's
    operands come out in source order. Descends through :func:`children`
    only. ``DataType*`` nodes (and what they hold) are skipped unless
    *into_types*, as the walkers that look for calls and writes do."""
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from walk(n, into_types)
        return
    if not _is_node(node):
        return
    if not into_types and type(node).__name__.startswith("DataType"):
        return
    yield node
    for c in children(node):
        yield from walk(c, into_types)


def rebuild(node, **replaced):
    """A copy of *node* with the child fields in *replaced* set; every other
    field, ``loc``, ``provenance``, ``annotations`` and ``doc`` among them,
    is carried over. Returns *node* itself when nothing differs, so an
    unchanged subtree keeps its identity. Only child fields may be
    replaced: a pass that means to change where a node came from says so
    with :func:`dataclasses.replace`."""
    bad = set(replaced) & NON_CHILD_FIELDS
    if bad:
        raise ValueError(f"rebuild: {sorted(bad)} are not child fields")
    if all(getattr(node, k) is v for k, v in replaced.items()):
        return node
    return dc.replace(node, **replaced)


class Transformer:
    """Rebuilds a tree bottom-up, in the manner of :class:`ast.NodeTransformer`.

    ``visit(node)`` calls ``visit_<ClassName>`` if defined, else
    :meth:`generic_visit`, which visits each child (through
    :func:`child_fields`) and :func:`rebuild` s the node from the results.
    A visitor may return the node, a replacement, or, for a node held in a
    list (a statement in a body), a list of nodes to splice in its place
    (``[]`` removes it). The input tree is not changed; untouched subtrees
    are shared with the result. ``DataType*`` nodes are left as they are.
    """

    def visit(self, node):
        if isinstance(node, list):
            return self.visit_list(node)
        if not _is_node(node) or type(node).__name__.startswith("DataType"):
            return node
        meth = getattr(self, "visit_" + type(node).__name__, None)
        return meth(node) if meth is not None else self.generic_visit(node)

    def visit_list(self, nodes: list) -> list:
        out, changed = [], False
        for n in nodes:
            r = self.visit(n)
            if isinstance(r, list) and not isinstance(n, list):
                out.extend(r)
                changed = True
            else:
                out.append(r)
                changed = changed or r is not n
        return out if changed else nodes

    def generic_visit(self, node):
        new = {}
        for name, v in child_fields(node):
            r = self.visit(v)
            if isinstance(r, list) and not isinstance(v, list):
                raise TypeError(
                    f"{type(node).__name__}.{name}: a list replaces a "
                    f"node only inside a list")
            if r is not v:
                new[name] = r
        return rebuild(node, **new)
