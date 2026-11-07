"""Which named constants a target spells qualified (user ruling, 2026-11-06).

The front end folds a reference to a `static const` (or a package `const`)
and keeps the name the linker resolved, `ExprConstant.ref`
(`cfg_pkg::MAX_CH`). A target that spells such a reference BY NAME puts it in
one namespace -- an SV package's `localparam`s, C's macros -- where the
package is gone. So a constant is spelled by its own name, and qualified with
its scope only when another constant the same code spells, or a name the
target already declares there, has that name too.

One rule for every target that spells constants, so one model names its
constants alike in SV and in C. It depends only on the set of constants the
generated code references, which each target records while rendering.

Language-neutral, like `enum_names.py`: it says WHETHER a name is qualified,
and how the qualified name is formed; a target's keyword rules and style are
its own.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Set, Tuple

__all__ = ["split", "plain_name", "qualified_name", "clashing"]


def split(qname: str) -> Tuple[str, str]:
    """``(scope, name)`` of a qualified name; the scope may be empty."""
    head, _, tail = qname.rpartition("::")
    return head, tail


def plain_name(qname: str) -> str:
    """The constant's own name: `cfg_pkg::MAX_CH` -> `MAX_CH`."""
    return split(qname)[1]


def qualified_name(qname: str) -> str:
    """The name qualified with its scope, `::` spelled `_`:
    `cfg_pkg::MAX_CH` -> `cfg_pkg_MAX_CH`; a constant of the global scope
    (`::G`) is `global_G`."""
    head, name = split(qname)
    return f"{head.replace('::', '_') if head else 'global'}_{name}"


def clashing(qnames: Iterable[str], taken: Iterable[str] = (),
             scopeless: bool = False) -> Set[str]:
    """The constants among *qnames* that are spelled qualified: each whose
    own name another of *qnames* shares, or *taken* holds. A constant with
    no scope is in the result only with *scopeless*: a target whose
    constants share a namespace with every other name (C's macros) must
    qualify it, one that declares them in a scope of their own (SV's
    package) need not."""
    taken = set(taken)
    by_name: Dict[str, List[str]] = {}
    for q in qnames:
        by_name.setdefault(split(q)[1], []).append(q)
    return {q for name, qs in by_name.items() for q in qs
            if (len(qs) > 1 or name in taken)
            and (scopeless or split(q)[0])}
