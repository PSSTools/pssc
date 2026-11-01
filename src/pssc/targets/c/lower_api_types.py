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

    A `packed_s<>` base means the PSS type HAS a bit layout. One of at most 64
    bits is declared as that layout (`emit_packed_union`). A wider one stays a
    plain struct -- see `c_member_type` -- and `read_struct`/`write_struct`
    move it member by member, so its C layout never matters.
    """
    name = c_struct_name(struct_dtype)
    union = emit_packed_union(struct_dtype, name)
    if union is not None:
        return union
    lines = [f"typedef struct {{"]
    for f in struct_dtype.fields:
        if _dt_name(f.datatype) == "DataTypeArray":
            # An array member: its bounds after its name (E7). A struct is
            # assigned whole in C, embedded arrays included -- the value
            # copy PSS means (8.1).
            from .lower_progseq import c_declarator
            lines.append(f"    {c_declarator(f.datatype, f.name, 'member')};")
            continue
        lines.append(f"    {c_member_type(f.datatype)} {f.name};")
    lines.append(f"}} {name};")
    return "\n".join(lines)


def emit_packed_union(struct_dtype, name: str):
    """A `packed_s` struct of at most 64 bits as its packed image: a union of
    the raw word and the fields as bitfields, or ``None`` for any other struct
    (`packed_image.union_fields`).

    This is the one place a C API struct DOES carry its PSS layout, because
    `read_struct` / `write_struct` move it to memory as that layout
    (docs/design/write-struct-plan.md): `raw` is the image, one access of its
    own size. Fields are declared by position from the LSB, as the register
    value unions are (`lower_reg_model.emit_value_union`), under the same
    little-endian bitfield assumption the header states, and the size is
    asserted so a compiler that breaks it fails to build rather than moving
    the wrong bits. Field access, assignment and designated initialisers keep
    their spelling: the struct is anonymous, so its members are the union's.
    """
    from ..packed_image import raw_bits, struct_bits, union_fields
    fields = union_fields(struct_dtype)
    if fields is None:
        return None
    rb = raw_bits(struct_bits(struct_dtype))
    ut = f"uint{rb}_t"
    lines = ["typedef union {", f"    {ut} raw;", "    struct {"]
    pos = 0
    for uf in sorted(fields, key=lambda u: u.lsb):
        if uf.lsb > pos:
            lines.append(f"        {ut} {'':12} : {uf.lsb - pos:2};")
        bt = f"int{rb}_t" if uf.signed else ut
        rng = (f"{uf.lsb}" if uf.width == 1
               else f"{uf.lsb + uf.width - 1}:{uf.lsb}")
        lines.append(f"        {bt} {uf.name:12} : {uf.width:2};   /* [{rng}] */")
        pos = uf.lsb + uf.width
    lines += ["    };", f"}} {name};",
              f"PSSC_STATIC_ASSERT(sizeof({name}) == {rb // 8}, "
              f"{name}_is_{rb // 8}_bytes);"]
    return "\n".join(lines)


def lower_api_types(components, reg_value_structs=(),
                    ctor_names=None, style=None, type_map=None) -> str:
    """Header text for the enums and structs the API mentions, and every
    enum whose item a body writes (see `collect_api_types`)."""
    enums, structs = collect_api_types(components, reg_value_structs,
                                       ctor_names, type_map=type_map or {})
    # An external type is the platform's to declare (`is_extern_type`).
    extern = getattr(style, "is_extern_type", None)
    if extern is not None:
        enums = [e for e in enums if not extern(e)]
        structs = [s for s in structs if not extern(s)]
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
