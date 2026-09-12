"""Named, ordered sections of a generated file.

A backend assembles a file from a list of these, so an extension can insert,
replace or drop ONE part without reimplementing the other twelve. The list is
the whole layout: what a subclass reads from `header_sections()` is what comes
out, in that order.

Every operation here NAMES the section it acts on and raises when that name
does not exist. That is the point of the module rather than a detail of it: a
list of callables would let `insert_after(s, "includes", ...)` silently do
nothing after an upstream rename, and the customisation would disappear from
the output with nothing to say it had. An exception at generation time is the
only way that surfaces at all.

Design: docs/design/generator-style-extensions-design.md §2.6.1.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Callable, List, Sequence


class UnknownSection(KeyError):
    """Names a section that is not in the list, and lists the ones that are.

    The message carries the available names because the caller is an extension
    author looking at a version of the backend they did not write, and "no
    section 'includes'" without them turns a one-line fix into an archaeology
    exercise.
    """

    def __init__(self, name: str, sections: Sequence["Section"]):
        super().__init__(
            f"no section named {name!r}; this file has: "
            + ", ".join(repr(s.name) for s in sections))


@dc.dataclass(frozen=True)
class Section:
    """One named part of a file, and the callable that renders it.

    `emit` is called with whatever the backend passes its sections (for the C
    backend, `(model, settings)`) and returns the LINES it contributes --
    including its own trailing blank line, so that a section which renders to
    nothing contributes nothing rather than leaving a stray gap behind.
    """
    name: str
    emit: Callable[..., List[str]]

    def __iter__(self):
        """Unpack as `(name, emit)`, so a caller that only wants the pair does
        not have to know the type."""
        return iter((self.name, self.emit))


def index_of(sections: Sequence[Section], name: str) -> int:
    for i, s in enumerate(sections):
        if s.name == name:
            return i
    raise UnknownSection(name, sections)


def insert_after(sections: Sequence[Section], name: str,
                 *new: Section) -> List[Section]:
    out = list(sections)
    out[index_of(out, name) + 1:index_of(out, name) + 1] = new
    return out


def insert_before(sections: Sequence[Section], name: str,
                  *new: Section) -> List[Section]:
    out = list(sections)
    at = index_of(out, name)
    out[at:at] = new
    return out


def replace(sections: Sequence[Section], name: str,
            new: Section) -> List[Section]:
    """Substitute one section, IN PLACE in the order.

    In place rather than remove-then-append because order is the part a
    backend's correctness depends on -- C has no forward reference for a type
    used by value -- and a replacement that also moved would be a second,
    unrequested change hidden inside the first.
    """
    out = list(sections)
    out[index_of(out, name)] = new
    return out


def remove(sections: Sequence[Section], name: str) -> List[Section]:
    out = list(sections)
    del out[index_of(out, name)]
    return out


def names(sections: Sequence[Section]) -> List[str]:
    return [s.name for s in sections]
