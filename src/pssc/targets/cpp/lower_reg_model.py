"""Lower a PSS register tree to C++: value unions + ``pssc::reg<T,ACC>`` group
classes (1:1 with the SV register model).

Value unions are the same anonymous-union bitfield layouts as the C backend
(LSB-first), spelled with ``std::uintN_t``. Each ``reg_group_c`` component
becomes a class of ``pssc::reg`` members + nested-group members, constructed
``(pssc::mem_if&, addr_t base)`` with ``base+offset`` folded into each child;
arrays become ``std::array`` built via an index-sequence helper.

Design: docs/design/pss-c-cpp-progseq-gen-design.md (§4.2, §4.3).
"""
from __future__ import annotations

from typing import List, Optional

from ..progseq_model import (
    field_is_register, field_is_array, field_is_reg_group, array_element_type,
    array_size, _dt_name, _scalar_offset, _array_base_stride, _is_reserved,
    collect_reg_groups, collect_value_structs,
)
from ..c.lower_reg_model import (
    c_struct_name, _prim_bits, _struct_total_bits, _reg_value_bits, _strip_pkg,
)

_DT_REGISTER = "DataTypeRegister"
_DT_REGISTER_GROUP = "DataTypeRegisterGroup"
_DT_STRUCT = "DataTypeStruct"

_ACC = {"READWRITE": "", "READONLY": ", pssc::access::ro",
        "WRITEONLY": ", pssc::access::wo"}


# --- value unions ----------------------------------------------------------

def emit_value_union(struct_dtype) -> str:
    name = c_struct_name(struct_dtype)
    prim = _prim_bits(_struct_total_bits(struct_dtype))
    ut = f"std::uint{prim}_t"
    lines = [f"typedef union {{ {ut} raw; struct {{"]
    bit = 0
    for f in struct_dtype.fields:
        w = int(f.datatype.bits)
        lines.append(f"    {ut} {f.name:12} : {w:2};   /* [{bit + w - 1}:{bit}] */")
        bit += w
    lines.append(f"}}; }} {name};")
    return "\n".join(lines)


# --- reg<T,ACC> spelling ---------------------------------------------------

def _reg_type(reg_dtype) -> str:
    vt = reg_dtype.register_value_type
    if _dt_name(vt) == _DT_STRUCT:
        t = c_struct_name(vt)
    else:
        t = f"std::uint{_prim_bits(_reg_value_bits(reg_dtype))}_t"
    acc = _ACC.get(getattr(reg_dtype, "access_mode", "READWRITE") or "READWRITE", "")
    return f"pssc::reg<{t}{acc}>"


# --- reg-group classes -----------------------------------------------------

def emit_reg_group_class(group_dtype) -> str:
    cls = _strip_pkg(group_dtype.name)
    decls: List[str] = []
    inits: List[str] = []
    makers: List[str] = []

    for f in group_dtype.fields:
        if _is_reserved(f):
            continue
        if field_is_register(f):
            off = _scalar_offset(group_dtype, f.name)
            decls.append(f"    {_reg_type(f.datatype)} {f.name};")
            inits.append(f"{f.name}(bus, base + 0x{off:x})")
        elif field_is_reg_group(f):
            off = _scalar_offset(group_dtype, f.name)
            gt = _strip_pkg(f.datatype.name)
            decls.append(f"    {gt} {f.name};")
            inits.append(f"{f.name}(bus, base + 0x{off:x})")
        elif field_is_array(f):
            elem = array_element_type(f)
            size = array_size(f)
            base, stride = _array_base_stride(group_dtype, f.name)
            if _dt_name(elem) == _DT_REGISTER:
                et = _reg_type(elem)
            else:
                et = _strip_pkg(elem.name)
            decls.append(f"    std::array<{et}, {size}> {f.name};")
            inits.append(f"{f.name}(make_{f.name}(bus, base))")
            makers.append(_array_maker(f.name, et, size, base, stride))

    body = [f"class {cls} {{", "public:"]
    body += decls
    body.append(f"    {cls}(pssc::mem_if &bus, pssc::addr_t base)")
    body.append("      : " + ", ".join(inits) + " {}")
    if makers:
        body.append("private:")
        body += makers
    body.append("};")
    return "\n".join(body)


def _array_maker(name: str, elem_type: str, size: int, base: int, stride: int) -> str:
    arr = f"std::array<{elem_type}, {size}>"
    return "\n".join([
        f"    template <std::size_t... I>",
        f"    static {arr} make_{name}_impl(pssc::mem_if &bus, pssc::addr_t base, std::index_sequence<I...>) {{",
        f"        return {{ {elem_type}(bus, base + 0x{base:x} + I * 0x{stride:x})... }};",
        f"    }}",
        f"    static {arr} make_{name}(pssc::mem_if &bus, pssc::addr_t base) {{",
        f"        return make_{name}_impl(bus, base, std::make_index_sequence<{size}>{{}});",
        f"    }}",
    ])


def _groups_of(components) -> List[object]:
    """Every register group under ``components``, deduplicated, parents last.

    Across the whole TREE, not just the root: a per-channel bank is reached
    through the channel component, and emitting only the root's groups left
    every generated channel class naming a type nothing declared.

    Order is the order `collect_reg_groups` produces -- nested groups before
    the group that holds them -- which is what C++ needs, since a group holds
    its children by value.
    """
    out: List[object] = []
    seen = set()
    for comp in components:
        for g in collect_reg_groups(comp):
            if id(g) not in seen:
                seen.add(id(g))
                out.append(g)
    return out


def lower_value_unions(components) -> str:
    structs = collect_value_structs(_groups_of(components))
    if not structs:
        return ""
    parts = ["// ----- Register value layouts (shared with C; std::uintN_t). -----"]
    for s in structs:
        parts.append(emit_value_union(s))
    return "\n".join(parts)


def lower_reg_groups(components) -> str:
    groups = _groups_of(components)
    if not groups:
        return ""
    parts = ["// ----- Register-group classes. -----"]
    for g in groups:
        parts.append(emit_reg_group_class(g))
        parts.append("")
    return "\n".join(parts).rstrip()
