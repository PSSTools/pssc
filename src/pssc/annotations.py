"""Read the source annotations (LRM 7.13) the IR carries.

ast2ir records every resolved annotation on the node translated from the
declaration it was written on (``Base.annotations``: components, actions,
structs, enums, fields, functions, exec blocks), with every field's value,
defaults included, as pssparser folded it. A plug-in reads them here::

    from pssc import annotations

    hal = annotations.of(comp, "hal_pkg::hal_s")     # None if not annotated
    if hal is not None:
        hal["name"], hal["prefix"]

A value is a ``str``, ``int``, ``bool``, ``zuspec.ir.core.AnnotationEnum``
(``enum``, ``item``, ``value``) or ``None`` (it did not fold, or the field is
unset and has no default).

The name is the annotation type's *qualified* name, as the front end resolved
it. A spelling is not resolved here: ``hal_s`` means different types in
different scopes, and only the linker knows which. Asking for one that
matches only by its last component raises, naming the candidates.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["of", "all_of"]


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

    Raises ``ValueError`` if the type is applied more than once (pssparser
    warns, LINT409); use :func:`all_of` to read every application."""
    found = all_of(node, qname)
    if len(found) > 1:
        raise ValueError(
            "annotation '%s' is applied %d times to this node; use all_of()"
            % (qname.lstrip(":"), len(found)))
    return found[0] if found else None
