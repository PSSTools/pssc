"""Emit the data types a progseq export API mentions: enums and plain structs.

Register *value* structs are emitted by `lower_reg_model` -- they are part of
the register model. This module covers the other half: the enums and structs
that appear in operation signatures, in local declarations, and as component
attributes (`wb_dma_status_e`, `wb_dma_ch_cfg_s`, `wb_dma_desc_s`).

They are collected by walking what the API actually mentions rather than by
emitting every type in the model, so a generated package stays the projection of
one component tree and not a translation of the whole PSS source.
"""
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from ..progseq_model import _dt_name, enum_first_item, func_kind, FuncKind, sub_components

_DT_ENUM = "DataTypeEnum"
_DT_STRUCT = "DataTypeStruct"
_DT_INT = "DataTypeInt"


def _strip_pkg(name: Optional[str]) -> str:
    return name.split("::")[-1] if name else name


def _is_packed(struct_dtype) -> bool:
    """PSS structs derived from ``packed_s<>`` have a defined bit layout, and SV
    can express that exactly -- `struct packed`, declared MSB-first. A struct
    with no such base has no layout contract, so it stays unpacked."""
    sup = getattr(struct_dtype, "super", None)
    nm = getattr(sup, "ref_name", None) or getattr(sup, "name", None)
    return bool(nm) and _strip_pkg(nm).startswith("packed_s")


def collect_api_types(components, reg_value_structs=(),
                      ctor_names=None,
                      bodies=(),
                      type_map=None) -> Tuple[List[object], List[object]]:
    """``(enums, structs)`` mentioned by ``components``' exported API.

    Structs come back in dependency order (a nested struct before the struct
    that holds it), because SV requires a type to be declared before use.
    ``reg_value_structs`` are skipped: the register model already emits them.
    ``bodies`` are further functions whose signatures and locals count --
    exported actions and package-scope functions, which are no component's
    declared functions.

    With ``type_map``, an enum whose ITEM is written -- in any body of the
    components or in ``bodies``, or in a field's initializer -- counts too
    (`enum_names.written_enums`): a target that spells an item by its name
    must declare the enum, whatever names its type.
    """
    skip = {id(s) for s in reg_value_structs}
    enums: List[object] = []
    structs: List[object] = []
    seen: Set[int] = set()

    def visit(dt):
        if dt is None or id(dt) in seen:
            return
        cn = _dt_name(dt)
        if cn == _DT_ENUM:
            seen.add(id(dt))
            enums.append(dt)
        elif cn == _DT_STRUCT:
            seen.add(id(dt))
            if _strip_pkg(dt.name) == "addr_handle_t":
                return          # a core typedef, not a generated struct
            for f in dt.fields:
                visit(f.datatype)      # members first: declaration order
            if id(dt) not in skip:
                structs.append(dt)
        elif cn == "DataTypeArray":
            visit(getattr(dt, "element_type", None))

    for comp in components:
        subs = {s.name for s in sub_components(comp)}
        for f in comp.fields:
            if f.name not in subs:
                visit(f.datatype)
        for fn in comp.functions:
            if func_kind(fn, ctor_names) not in (FuncKind.EXPORT_OP,
                                                FuncKind.EXPORT_SOLVE,
                                                FuncKind.CONSTRUCTOR,
                                                FuncKind.IMPORT_TASK,
                                                FuncKind.IMPORT_SOLVE):
                continue
            visit(fn.returns)
            for a in (fn.args.args if fn.args else []):
                visit(a.annotation)
            for s in (fn.body or []):
                _visit_stmt(s, visit)
    for fn in bodies:
        visit(fn.returns)
        for a in (fn.args.args if fn.args else []):
            visit(a.annotation)
        for s in (fn.body or []):
            _visit_stmt(s, visit)
    if type_map is not None:
        from ..enum_names import written_enums
        written = [f.initial_value for c in components for f in c.fields
                   if getattr(f, "initial_value", None) is not None]
        written += [fn.body for c in components for fn in c.functions]
        written += [fn.body for fn in bodies]
        written += [f.initial_value for st in structs for f in st.fields
                    if getattr(f, "initial_value", None) is not None]
        for e in written_enums(written, type_map):
            visit(e)

    return enums, structs


def _visit_stmt(s, visit):
    """Local declarations name types too, and a body may declare a struct the
    signature never mentions."""
    if _dt_name(s) == "StmtAnnAssign":
        visit(s.annotation)
    for attr in ("body", "orelse"):
        for inner in (getattr(s, attr, None) or []):
            _visit_stmt(inner, visit)
    for case in (getattr(s, "cases", None) or []):
        for inner in (getattr(case, "body", None) or []):
            _visit_stmt(inner, visit)


def emit_enum(enum_dtype, consts=None) -> str:
    """``typedef enum`` with explicit values.

    Values are stated rather than left implicit because the model's own numbers
    are load-bearing -- they are register field encodings (`WB_DMA_IF1 = 1`),
    not an arbitrary ordering. An enumerator is a package-scope name, so it
    is spelled by `SvConstants.item_name` (prefixed with its enum's name).
    """
    from .constants import SvConstants
    spell = consts if consts is not None else SvConstants()
    name = _strip_pkg(enum_dtype.name)
    items = ", ".join(f"{spell.item_name(name, k)} = {v}"
                      for k, v in enum_dtype.items.items())
    return f"  typedef enum {{{items}}} {name};"


def sv_member_type(dtype) -> str:
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        bits = int(getattr(dtype, "bits", 32) or 32)
        sign = " signed" if getattr(dtype, "signed", False) else ""
        if sign and bits == 32:
            return "int"
        return f"bit{sign}" if bits == 1 else f"bit{sign} [{bits - 1}:0]"
    if cn in (_DT_ENUM, _DT_STRUCT):
        return _strip_pkg(dtype.name)
    raise ValueError(f"unsupported SV struct member type {cn}")


def _array_dims(dtype, consts=None):
    """Flatten nested ``DataTypeArray``s to ``(element_type, [size, ...])`` --
    SV spells every dimension after the member name (`k[4]`, `k[4][2]`), unlike
    C's declarator syntax that ``sv_member_type`` otherwise mirrors."""
    sizes = []
    while _dt_name(dtype) == "DataTypeArray":
        bound = consts.size_name(dtype) if consts is not None else None
        sizes.append(bound or int(getattr(dtype, "size", 0) or 0))
        dtype = getattr(dtype, "element_type", None)
    return dtype, sizes


def _sv_const_list(e, consts=None) -> str:
    """An array initializer of constants as an SV assignment pattern."""
    parts = []
    for x in e.elts:
        if _dt_name(x) == "ExprList":
            parts.append(_sv_const_list(x, consts))
        elif _dt_name(x) == "ExprConstant" and isinstance(x.value, (bool, int)):
            named = consts.name_of(x) if consts is not None else None
            parts.append(named or str(int(x.value)))
        else:
            raise ValueError(f"an array member's initializer element is not "
                             f"a constant ({_dt_name(x)})")
    return "'{" + ", ".join(parts) + "}"


def emit_struct(struct_dtype, consts=None) -> str:
    name = _strip_pkg(struct_dtype.name)
    packed = _is_packed(struct_dtype)
    fields = list(struct_dtype.fields)
    if packed:
        # Declaration order IS the bit numbering in PSS (LSB first); SV packed
        # structs are declared MSB first, so the order reverses.
        fields = list(reversed(fields))
    lines = [f"  typedef struct{' packed' if packed else ''} {{"]
    for f in fields:
        # A field initializer is part of the type (7.8): a local declared
        # without one starts with it. An unpacked SV struct member can carry
        # one; a packed struct's cannot, and keeps its zero.
        init = getattr(f, "initial_value", None)
        dflt = ""
        if (not packed and init is not None and _dt_name(init) == "ExprConstant"
                and isinstance(init.value, (bool, int))):
            named = consts.name_of(init) if consts is not None else None
            dflt = f" = {named or int(init.value)}"
            # SV converts no integer to an enum implicitly (6.19.3).
            if _dt_name(f.datatype) == "DataTypeEnum" and named is None:
                dflt = f" = {_strip_pkg(f.datatype.name)}'({int(init.value)})"
        elif (not packed and _dt_name(init) == "ExprList"
              and _dt_name(f.datatype) == "DataTypeArray"):
            # An array member's initializer is part of the type as a scalar
            # one is; it was dropped, so the member started at 0 (W24).
            dflt = f" = {_sv_const_list(init, consts)}"
        elif not packed and init is None and enum_first_item(f.datatype):
            # An enum starts at its first item (7.5); SV's starts at 0.
            from .constants import SvConstants
            spell = consts if consts is not None else SvConstants()
            dflt = f" = {spell.first_item(f.datatype)}"
        if _dt_name(f.datatype) == "DataTypeArray":
            elem, dims = _array_dims(f.datatype, consts)
            dim_str = "".join(f"[{n}]" for n in dims)
            lines.append(f"    {sv_member_type(elem)} {f.name}{dim_str}{dflt};")
        else:
            lines.append(f"    {sv_member_type(f.datatype)} {f.name}{dflt};")
    lines.append(f"  }} {name};")
    return "\n".join(lines)


def lower_api_types(components, reg_value_structs=(), ctor_names=None,
                    bodies=(), type_map=None, consts=None) -> str:
    """Package-body text for the enums and structs the API mentions, and every
    enum whose item the code writes (see `collect_api_types`)."""
    enums, structs = collect_api_types(components, reg_value_structs,
                                       ctor_names, bodies, type_map)
    if not enums and not structs:
        return ""
    parts = ["  // ----- Data types used by the export API. -----"]
    parts += [emit_enum(e, consts) for e in enums]
    if enums:
        parts.append("")
    fns = [l for e in enums
           for l in (consts.name_fn_decl(e) if consts is not None else [])]
    if fns:
        parts += fns + [""]
    for s in structs:
        parts.append(emit_struct(s, consts))
        parts.append("")
    return "\n".join(parts)
