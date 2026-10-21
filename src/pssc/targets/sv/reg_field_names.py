"""Name the register fields a folded ``write_val_masked`` came from.

``reg_rmw`` reduces every field-wise and masked register write to one primitive
carrying a *constant* (mask, value) pair, and ``test_reg_ir_equivalence.py``
asserts that no field name and no field-name string ever reaches a backend.
That invariant is what stops each new backend from needing its own name table,
and it is not relaxed here: **this module puts nothing into the IR**. It reads
the constant the reduction produced and asks the register model, which the SV
target already has, which field has exactly those bits.

So the flow is one-way and lossless in the direction that matters:

    write_field("ars", enable)          -- PSS source, front end resolves 'ars'
      -> write_val_masked(64, (32'(enable) & 1) << 6)     -- IR, no names
        -> csr.write_field(WB_DMA_CH_CSR_ars, 32'(enable))  -- SV, names restored

The C target sees the middle line unchanged, which is why it needs no changes
for any of this.

Two things can fail, and both fall back to emitting the folded literals rather
than guessing:

* the receiver is not a path this module can walk to a register;
* the value expression is not the shape ``_place()`` produces, so the shifted
  value cannot be turned back into the unshifted one the wrapper wants.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, Tuple

from ...reg_field_resolve import FieldSlice
from ..progseq_model import _dt_name
from ..reg_field_names import FieldNamer as _FieldNamer
from ..reg_field_names import unplace, whole_fields  # noqa: F401 (re-exported)


def _strip_pkg(name: Optional[str]) -> str:
    return name.split("::")[-1] if name else name


def const_prefix(struct_name: str) -> str:
    """``wb_dma_ch_csr_s`` -> ``WB_DMA_CH_CSR``.

    Keyed on the VALUE STRUCT's type name, not the register instance name.
    `csr` is ambiguous in a typical model -- a channel bank and a global bank
    both have one -- whereas struct typedef names are already unique within the
    generated package, so this is collision-free by construction rather than by
    luck.
    """
    n = _strip_pkg(struct_name) or "REG"
    if n.endswith("_s"):
        n = n[:-2]
    return n.upper()


def const_name(struct_name: str, field: str) -> str:
    """The localparam naming one field: ``WB_DMA_CH_CSR_ars``.

    The field keeps its source spelling rather than being upper-cased, so the
    identifier round-trips: grepping `ars` finds the PSS declaration, the packed
    struct member and this constant. Upper-casing would be a lossy transform a
    reader has to reverse by hand.
    """
    return f"{const_prefix(struct_name)}_{field}"


# --- the namer --------------------------------------------------------------

@dc.dataclass
class FieldRef:
    """One field of one register, with the constant that names it."""
    slice: FieldSlice
    const: str


class FieldNamer(_FieldNamer):
    """The shared namer (``targets/reg_field_names.py``), answering with the
    constant SV names each field by."""

    def struct_name(self, recv) -> Optional[str]:
        reg = self._register(recv)
        layout = self._layout(reg) if reg is not None else None
        return layout[0] if layout is not None else None

    def refs(self, recv, slices) -> Optional[List[FieldRef]]:
        sn = self.struct_name(recv)
        if sn is None or not slices:
            return None
        return [FieldRef(slice=fs, const=const_name(sn, fs.name)) for fs in slices]

    def fields_for(self, recv, mask: int) -> Optional[List[FieldRef]]:
        return self.refs(recv, super().fields_for(recv, mask))
