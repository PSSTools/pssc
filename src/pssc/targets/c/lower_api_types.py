"""Emit the data types a C programming API mentions: enums and plain structs.

The C analogue of ``targets/sv/lower_api_types.py``, and it deliberately reuses
that module's ``collect_api_types``: WHICH types an API mentions is a property
of the model, not of the output language, and two collectors would be two
answers to one question. Only the rendering is here.

Register *value* structs are emitted by ``lower_reg_model``; this covers the
other half -- the enums and structs that appear in operation signatures, in
local declarations, and as component attributes (``wb_dma_status_e``,
``wb_dma_ch_cfg_s``, ``wb_dma_desc_s``).
"""
from __future__ import annotations

from typing import List

from ..progseq_model import _dt_name
from ..sv.lower_api_types import collect_api_types, _is_packed, _strip_pkg
from .lower_reg_model import c_struct_name

_DT_ENUM = "DataTypeEnum"
_DT_STRUCT = "DataTypeStruct"
_DT_INT = "DataTypeInt"
_DT_BOOL = "DataTypeBool"


def emit_enum(enum_dtype, style=None) -> str:
    """``typedef enum`` with EXPLICIT values.

    C would happily number these itself, and that is exactly the risk. The
    model's numbers are register field encodings and cross-language contract
    (`wb_dma_types_pkg.pss` records that `PENDING` was appended specifically to
    leave `DONE == 0` and `ERROR == 1` undisturbed, "because these values cross
    into generated SV" -- and now into generated C). Implicit numbering would
    reproduce them today and silently renumber the day someone reorders the PSS
    declaration.

    An enumerator is named by the style (`CStylePolicy.enum_item`: the
    enum's name, upper-cased without `_e`, then the item), because C has one
    namespace for every enum's items and PSS has one per enum.
    """
    from .lower_progseq import CEnums, c_enum_name
    name = c_enum_name(enum_dtype)
    spell = CEnums(style)
    items = ",\n".join(f"    {spell.item(enum_dtype, k)} = {v}"
                        for k, v in enum_dtype.items.items())
    return f"typedef enum {{\n{items}\n}} {name};"


def c_member_type(dtype) -> str:
    """C type for a struct member.

    Bit-exact widths are NOT expressed as C bitfields here: C leaves bitfield
    layout implementation-defined, so a `uint32_t x : 3` says something the
    standard does not guarantee. Register value structs -- the ones with a
    defined bit layout -- go through `lower_reg_model` instead, which packs
    them into a plain integer with explicit shifts. What is left here has no
    layout contract to keep, so a member takes the smallest standard type that
    holds it.
    """
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        from .lower_progseq import c_type
        return c_type(dtype)
    if cn == _DT_BOOL:
        return "bool"
    if cn == _DT_ENUM:
        from .lower_progseq import c_enum_name
        return c_enum_name(dtype)
    if cn == _DT_STRUCT:
        if _strip_pkg(dtype.name) == "addr_handle_t":
            return "pssc_addr_t"
        return c_struct_name(dtype)
    if cn == "DataTypeChandle":
        return "pssc_addr_t"
    raise ValueError(f"unsupported C struct member type {cn}")


def emit_struct(struct_dtype) -> str:
    """A plain C struct, in DECLARATION order.

    Unlike the SV projection, the order is not reversed. SV packed structs are
    declared MSB-first, so lowering a PSS (LSB-first) declaration there means
    reversing; a C struct has no such convention and reversing would only make
    the generated type harder to read against the PSS beside it.

    A `packed_s<>` base means the PSS type HAS a bit layout. That layout is not
    reproduced by a C struct and is not attempted -- see `c_member_type`. Such
    a type reaching here at all means it is used as a plain aggregate; the
    register path is where its layout matters, and that path packs it.
    """
    name = c_struct_name(struct_dtype)
    lines = [f"typedef struct {{"]
    for f in struct_dtype.fields:
        lines.append(f"    {c_member_type(f.datatype)} {f.name};")
    lines.append(f"}} {name};")
    return "\n".join(lines)


def lower_api_types(components, reg_value_structs=(),
                    ctor_names=None, style=None, type_map=None) -> str:
    """Header text for the enums and structs the API mentions, and every
    enum whose item a body writes (see `collect_api_types`)."""
    enums, structs = collect_api_types(components, reg_value_structs,
                                       ctor_names, type_map=type_map or {})
    if not enums and not structs:
        return ""
    parts: List[str] = ["/* ----- Data types used by the export API. ----- */"]
    for e in enums:
        parts.append(emit_enum(e, style))
        parts.append("")
    for s in structs:
        parts.append(emit_struct(s))
        parts.append("")
    return "\n".join(parts).rstrip()
