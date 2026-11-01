"""Which register fields a reduced masked write writes, for any backend.

``reg_rmw`` reduces every spelling of a masked register write to one
``write_val_masked(mask, value)`` (PSS 3.1 §21.14.1), and records the fields
the source named on the call's provenance (``RegWriteProvenance``; ruling
R-H7). This module is the ONE way to ask which fields those are:

* :func:`written_fields` reads the provenance;
* :class:`FieldNamer` answers from a constant mask and the register's layout,
  the fallback for a write whose source named no field
  (``write_val_masked`` written out, or ``write_masked`` of a whole struct);
* :func:`unplace` turns the placed value back into the one the model wrote.

The answer is advisory. It may change how a backend SPELLS the modify step,
never the number or order of bus operations: one read, then one write.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..reg_field_resolve import FieldSlice, field_layout, value_struct
from .progseq_model import _dt_name

__all__ = ["FieldNamer", "unplace", "written_fields", "whole_fields"]

_DT_REGISTER = "DataTypeRegister"


def written_fields(call):
    """The ``RegFieldWrite`` records of a reduced ``write_val_masked`` (or of
    the ``write_val`` that ``--reg-rmw=expand`` made of one), in the order the
    source named the fields; ``None`` when its source named no field or it is
    not a reduced write."""
    from ..reg_rmw import RegWriteProvenance
    p = getattr(call, "provenance", None)
    return p.fields if isinstance(p, RegWriteProvenance) else None


def whole_fields(call) -> Optional[List[FieldSlice]]:
    """The fields of :func:`written_fields`, LSB first, when each is written
    WHOLE (its mask is all of its bits); otherwise ``None``. A partial mask
    is not that field, and naming it as though it were would be a lie
    (`reg_rmw.field_writes` with ``partial_field_writes=False``)."""
    from ..reg_rmw import field_writes
    fields = field_writes(written_fields(call), partial_field_writes=False)
    if fields is None:
        return None
    return sorted((w.slice for w in fields), key=lambda fs: fs.lsb)


# --- value un-placing -------------------------------------------------------

def unplace(value, fs: FieldSlice):
    """Undo ``reg_rmw._place()``: the value as the caller wrote it, or ``None``.

    ``_place`` is the sole producer of these expressions and its shape is known
    exactly: ``(v & ((1<<width)-1)) << lsb``, with the shift dropped when the
    field is at bit 0 and the whole thing folded to a literal when ``v`` is
    constant. Anything else returns ``None`` and the caller emits the folded
    form -- a wrong guess here would write the wrong register bits with no
    diagnostic anywhere, so the match is exact or it is refused.
    """
    import zuspec.ir.core as ir

    c = _const_int(value)
    if c is not None:
        # A constant folded all the way to a literal at its final position. It
        # has to lie inside the field, or this is not the value that mask was
        # placed for.
        if c & ~fs.mask:
            return None
        return ir.ExprConstant(value=(c & fs.mask) >> fs.lsb)

    # Non-constant: `_and_lit` never folds, so the width AND is always present.
    e = value
    if fs.lsb:
        if not _is_bin(e, "LShift") or _const_int(e.rhs) != fs.lsb:
            return None
        e = e.lhs
    if not _is_bin(e, "BitAnd") or _const_int(e.rhs) != (1 << fs.width) - 1:
        return None
    return e.lhs


def _is_bin(e, op_name: str) -> bool:
    return _dt_name(e) == "ExprBin" and getattr(e.op, "name", None) == op_name


def _const_int(e) -> Optional[int]:
    if _dt_name(e) != "ExprConstant":
        return None
    v = e.value
    if isinstance(v, bool):
        return int(v)
    return v if isinstance(v, int) else None


# --- the namer --------------------------------------------------------------

class FieldNamer:
    """Answers "which field(s) is this mask, on this receiver?" for one component.

    Constructed per component because receiver paths are resolved against that
    component's fields. ``type_map`` resolves ``DataTypeRef`` value types, which
    is how a register declared in one package and used in another is reached.
    """

    def __init__(self, comp, type_map: Optional[Dict[str, object]] = None):
        self._comp = comp
        self._type_map = type_map or {}
        self._layout_cache: Dict[int, Optional[Tuple[str, List[FieldSlice]]]] = {}

    def _resolve_name(self, name):
        return self._type_map.get(name)

    # -- receiver -> register --------------------------------------------

    def _steps(self, e) -> Optional[List[Optional[str]]]:
        """Flatten a ``self``-rooted receiver to attribute names.

        ``None`` marks a subscript, whose index is irrelevant: every element of
        a register array has the same value type, which is the only thing being
        looked up.
        """
        out: List[Optional[str]] = []
        while True:
            cn = _dt_name(e)
            if cn == "TypeExprRefSelf":
                out.reverse()
                return out
            if cn == "ExprAttribute":
                out.append(e.attr)
                e = e.value
                continue
            if cn == "ExprSubscript":
                out.append(None)
                e = e.value
                continue
            return None

    def _register(self, recv):
        """The ``DataTypeRegister`` a receiver expression denotes, or ``None``."""
        steps = self._steps(recv)
        if not steps:
            return None
        dtype = self._comp
        for step in steps:
            if step is None:
                dtype = _element_of(dtype)
            else:
                dtype = _field_type(dtype, step, self._resolve_name)
            if dtype is None:
                return None
        return dtype if _dt_name(dtype) == _DT_REGISTER else None

    def _layout(self, reg) -> Optional[Tuple[str, List[FieldSlice]]]:
        key = id(reg)
        if key in self._layout_cache:
            return self._layout_cache[key]
        vs = value_struct(reg, self._resolve_name)
        out = None
        if vs is not None:
            slices = [fs for fs in field_layout(reg, self._resolve_name) if fs.width]
            if slices:
                out = ((vs.name or '').split('::')[-1], slices)
        self._layout_cache[key] = out
        return out

    # -- public ------------------------------------------------------------

    def size_bits(self, recv) -> Optional[int]:
        reg = self._register(recv)
        if reg is None:
            return None
        from ...reg_field_resolve import reg_size_bits
        return reg_size_bits(reg)

    def fields_for(self, recv, mask: int) -> Optional[List[FieldSlice]]:
        """The declared fields whose bits are exactly ``mask``, or ``None``.

        Exact set equality, not "covers": a mask that spans part of a field is
        not that field, and naming it as though it were would be a lie in
        generated code. Fields are returned LSB-first, which makes the emitted
        list order stable across runs regardless of how the mask was written.
        """
        reg = self._register(recv)
        if reg is None:
            return None
        layout = self._layout(reg)
        if layout is None:
            return None
        _, slices = layout
        hits, acc = [], 0
        for fs in slices:
            if mask & fs.mask:
                if mask & fs.mask != fs.mask:
                    return None       # partial overlap: not this field
                hits.append(fs)
                acc |= fs.mask
        if not hits or acc != mask:
            return None               # mask has bits belonging to no field
        return hits


def _field_type(dtype, name: str, resolve_name):
    for f in getattr(dtype, "fields", []) or []:
        if f.name == name:
            ft = f.datatype
            if _dt_name(ft) == "DataTypeRef":
                ft = resolve_name(getattr(ft, "ref_name", None)) or ft
            return ft
    return None


def _element_of(dtype):
    for attr in ("element_type", "elem_type"):
        et = getattr(dtype, attr, None)
        if et is not None:
            return et
    return None
