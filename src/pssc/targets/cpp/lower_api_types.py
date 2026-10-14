"""Emit the data types a C++ programming API mentions: enums and plain structs.

The C++ analogue of ``targets/c/lower_api_types.py``, and like it, it reuses
``sv.lower_api_types.collect_api_types``: WHICH types an API mentions is a
property of the model, not of the output language, and three collectors would
be three answers to one question. Only the rendering is here.

Register *value* structs are emitted by ``lower_reg_model``; this covers the
other half -- the enums and structs that appear in operation signatures, in
local declarations, and as component attributes.
"""
from __future__ import annotations

from typing import List

from typing import Optional

from ..enum_names import item_of
from ..progseq_model import _dt_name, enum_first_item
from ..sv.lower_api_types import collect_api_types, _strip_pkg
from ..c.lower_reg_model import c_struct_name

_DT_ENUM = "DataTypeEnum"
_DT_STRUCT = "DataTypeStruct"
_DT_INT = "DataTypeInt"
_DT_BOOL = "DataTypeBool"


def cpp_enum_name(enum_dtype) -> str:
    return _strip_pkg(enum_dtype.name)


def cpp_enum_item(enum_dtype, item: str) -> str:
    """An item, scoped by its enum as in PSS (7.5): `wb_dma_status_e::DONE`."""
    from .lower_progseq import mangle
    return f"{cpp_enum_name(enum_dtype)}::{mangle(item)}"


def cpp_enum_constant(e, type_map) -> Optional[str]:
    """The item the folded constant *e* is (`enum_names.item_of`), spelled;
    None if it is no item."""
    found = item_of(e, type_map)
    return cpp_enum_item(*found) if found is not None else None


def cpp_first_item(dtype) -> Optional[str]:
    """An enum's first item, spelled, when value-initialisation does not give
    it (`enum_first_item`); else None."""
    name = enum_first_item(dtype)
    return cpp_enum_item(dtype, name) if name is not None else None


def emit_enum(enum_dtype) -> str:
    """A SCOPED enum (`enum class`) with an explicit underlying type and
    explicit values.

    Scoped because PSS scopes an item by its enum (7.5): two enums may each
    have an `A`, and unscoped C++ enumerators would be two declarations of one
    name. An item is written `op_e::A`, as PSS writes `op_e::A`. Every place a
    PSS enum is used as an integer -- arithmetic, a cast, a register field --
    is known from `ExprTypes`, which types a folded item as its enum, and
    states its conversion.

    EXPLICIT VALUES for the same reason as in C: the numbers are register field
    encodings and cross-language contract. Implicit numbering would reproduce
    them today and renumber them the day someone reorders the PSS declaration.
    """
    from .lower_progseq import mangle
    name = cpp_enum_name(enum_dtype)
    items = ",\n".join(f"    {mangle(k)} = {v}"
                        for k, v in enum_dtype.items.items())
    return f"enum class {name} : int {{\n{items}\n}};"


def cpp_member_type(dtype) -> str:
    """C++ type for a plain-struct member.

    Bit-exact widths are NOT expressed as bitfields here: bitfield layout is
    implementation-defined, so `std::uint32_t x : 3` claims something the
    standard does not guarantee. Register value structs -- the ones that DO
    have a defined layout -- go through `lower_reg_model`. What is left has no
    layout contract, so a member takes the smallest standard type that holds it.
    """
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        from .lower_progseq import cpp_type
        return cpp_type(dtype)
    if cn == _DT_BOOL:
        return "bool"
    if cn == _DT_ENUM:
        return cpp_enum_name(dtype)
    if cn == _DT_STRUCT:
        if _strip_pkg(dtype.name) == "addr_handle_t":
            return "pssc::addr_t"
        return c_struct_name(dtype)
    if cn == "DataTypeChandle":
        return "pssc::addr_t"
    raise ValueError(f"unsupported C++ struct member type {cn}")


def _cpp_const(e, dtype=None, type_map=None) -> str:
    """A member's initial value, a constant by construction (as in C).

    An enum item is spelled as the item the linker resolved."""
    if _dt_name(e) == "ExprConstant" and isinstance(e.value, bool):
        return "true" if e.value else "false"
    item = cpp_enum_constant(e, type_map)
    if item is not None:
        return item
    if _dt_name(e) == "ExprConstant" and _dt_name(dtype) == _DT_ENUM:
        return f"static_cast<{cpp_enum_name(dtype)}>({e.value})"
    from ..c.lower_progseq import _const_expr
    return _const_expr(e)


def emit_struct(struct_dtype, type_map=None) -> str:
    """A plain struct, in DECLARATION order, every member initialised.

    A default member initializer on every member rather than a constructor: a
    generated aggregate stays an aggregate, so a caller can still write
    `wb_dma_ch_caps_s c{true, false};`, and a default-constructed one holds
    the PSS defaults (`bool present = true;`), zero where the model gives
    none, rather than whatever was on the stack. The C backend cannot say this
    in the type and has to initialise at each declaration site instead.
    """
    name = c_struct_name(struct_dtype)
    lines = [f"struct {name} {{"]
    for f in struct_dtype.fields:
        # A member's PSS default is its default member initializer, so every
        # value of the type has it -- a local, an array element, a member of
        # another struct -- not only a component's field.
        iv = getattr(f, "initial_value", None)
        if iv is not None:
            init = _cpp_const(iv, f.datatype, type_map)
        else:
            # An enum starts at its first item (7.5), which need not be 0.
            init = cpp_first_item(f.datatype) or "{}"
        lines.append(f"    {cpp_member_type(f.datatype)} {f.name} = {init};")
    lines.append("};")
    return "\n".join(lines)


def lower_api_types(components, reg_value_structs=(),
                    ctor_names=None, type_map=None) -> str:
    """Header text for the enums and structs the API mentions, and every
    enum whose item a body writes (see `collect_api_types`)."""
    enums, structs = collect_api_types(components, reg_value_structs,
                                       ctor_names, type_map=type_map or {})
    if not enums and not structs:
        return ""
    parts: List[str] = ["// ----- Data types used by the export API. -----"]
    for e in enums:
        parts.append(emit_enum(e))
        parts.append("")
    for s in structs:
        parts.append(emit_struct(s, type_map))
        parts.append("")
    return "\n".join(parts).rstrip()
