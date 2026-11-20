"""The ACT image section (P4.10a): an activation table written to an image
and read back has the same tree, slot names and initial values. Each
constraint's expression is not in the section (its cone's blobs and text
hold it, P4.10b2), so it is left out of the tree comparison, as are source
locations and docs; the blobs and text are compared instead."""

import dataclasses as dc
import enum

from zuspec.be.bc.format.writer import Section
from zuspec.be.bc.model import ZbcModel

_SKIP = {"loc", "doc", "doc_trailing", "constraint"}


def plain(x):
    """*x* as dicts, lists and scalars, without the fields ACT leaves out."""
    if dc.is_dataclass(x):
        return {f.name: plain(getattr(x, f.name)) for f in dc.fields(x) if f.name not in _SKIP}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    if isinstance(x, enum.Enum):
        return x.value
    return x


def roundtrip(tables):
    """``{coro: ActivationTable}`` through an ACT payload and back."""
    holder = ZbcModel.__new__(ZbcModel)
    holder.activations = tables
    payload = holder._serialize_activations()
    return ZbcModel._parse_activations(Section(0, payload, count=1, elem_size=0))


def check(tables, back):
    assert sorted(back) == sorted(tables)
    for coro, t in tables.items():
        b = back[coro]
        assert plain(b.tree) == plain(t.tree), coro
        assert b.names == t.names, coro
        assert b.init_values == t.init_values, coro
        for c in t.tree.cones:
            assert b.cone_image(c.id) == t.cone_image(c.id), (coro, c.id)
