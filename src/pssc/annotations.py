"""Read the source annotations (LRM 7.13) the IR carries.

ast2ir records every resolved annotation on the node translated from the
declaration it was written on (``Base.annotations``: components, actions,
structs, enums, fields, functions, exec blocks), with every field's value,
defaults included, as pssparser folded it. A plug-in reads them here::

    from pssc import annotations

    blk = annotations.of(comp, "gen_pkg::block_s")   # None if not annotated
    if blk is not None:
        blk["name"], blk["prefix"]

A value is a ``str``, ``int``, ``bool``, ``zuspec.ir.core.AnnotationEnum``
(``enum``, ``item``, ``value``) or ``None`` (it did not fold, or the field is
unset and has no default).

The name is the annotation type's *qualified* name, as the front end resolved
it. A spelling is not resolved here: ``block_s`` means different types in
different scopes, and only the linker knows which. Asking for one that
matches only by its last component raises, naming the candidates.

The values are already typed as the annotation's field declarations say:
pssparser checks each against its field (PSS150), so a reader need not.
:func:`find_all` finds every node a type is applied to, reachable or not.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pssc.diag import Refusal

__all__ = ["of", "all_of", "find_all"]


def _check_qualified(node, qname: str) -> None:
    anns = getattr(node, "annotations", None) or []
    if any(a.type_qname == qname for a in anns):
        return
    near = sorted({a.type_qname for a in anns
                   if a.type_qname.rsplit("::", 1)[-1] == qname.rsplit("::", 1)[-1]})
    if near:
        raise ValueError(
            "no annotation '%s' on this node, but %s: name an annotation type "
            "by the qualified name the front end resolved"
            % (qname, ", ".join("'%s'" % n for n in near)))


def all_of(node, qname: str) -> List[Dict[str, Any]]:
    """The values of every ``qname`` annotation on ``node``, in source
    order (a copy of each; ``[]`` if there is none)."""
    qname = qname.lstrip(":")
    _check_qualified(node, qname)
    return [dict(a.values) for a in getattr(node, "annotations", None) or []
            if a.type_qname == qname]


def of(node, qname: str) -> Optional[Dict[str, Any]]:
    """The values of the one ``qname`` annotation on ``node``, or ``None``.

    Raises a `pssc.diag.Refusal` located at the second application if the
    type is applied more than once (pssparser warns, LINT409), so a plug-in
    that calls this from a pass reports it as the model's error; use
    :func:`all_of` to read every application."""
    found = all_of(node, qname)
    if len(found) > 1:
        qname = qname.lstrip(":")
        repeats = [a for a in node.annotations if a.type_qname == qname]
        raise Refusal(
            "annotation '%s' is applied %d times to this declaration; "
            "it may be applied once" % (qname, len(found)), repeats[1], node)
    return found[0] if found else None


def _declarations(ctx):
    seen = set()
    for t in list((getattr(ctx, "type_map", None) or {}).values()):
        for n in [t] + list(getattr(t, "fields", None) or []) \
                + list(getattr(t, "functions", None) or []):
            if id(n) not in seen:
                seen.add(id(n))
                yield n
    for f in getattr(ctx, "import_functions", None) or []:
        if id(f) not in seen:
            seen.add(id(f))
            yield f


def find_all(model, qname: str) -> List[Tuple[Any, Dict[str, Any]]]:
    """Every ``(node, values)`` with a ``qname`` annotation in ``model``
    (an `OpModel`, or the IR context itself): types, their fields and
    functions (exec blocks among them), and import functions, whether
    reachable from the root or not. A node with two applications appears
    twice. Matching is by qualified name only; there is no near-name check,
    as there is no one node to name the candidates on."""
    qname = qname.lstrip(":")
    ctx = getattr(model, "ctx", model)
    return [(n, dict(a.values)) for n in _declarations(ctx)
            for a in getattr(n, "annotations", None) or []
            if a.type_qname == qname]
