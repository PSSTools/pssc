"""Lower a PSS register tree (``reg_group_c`` components + ``packed_s`` value
structs) to SystemVerilog, for the ``sv-progseq`` target.

Emits, for the register subtree reachable from a root component:
  * one ``typedef struct packed`` per register value struct (fields reversed:
    PSS is LSB-first, SV packed is MSB-first);
  * one class per ``reg_group_c`` component, holding ``reg_c #(...)`` handles and
    nested-group fields, with a ``(pss_mem_if bus, addr_handle_t base)``
    constructor that folds ``base + offset`` into every child.

Scalar offsets come from the front-end's pre-computed ``offset_map``; array
offsets are derived by evaluating the affine ``get_offset_of_instance_array``
body at index 0 and 1.

Design: docs/design/pss-programming-seq-gen-design.md (§6.3, §6.4).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..comments import append_trailing, comment_lines

from ..progseq_model import (
    is_reg_group, field_is_register, field_is_array, field_is_reg_group,
    array_element_type, array_size, _dt_name,
    _eval_off, _pattern_str, _array_base_stride, _scalar_offset,
    _is_reserved, collect_reg_groups, collect_value_structs,
)
from ...reg_field_resolve import struct_layout
from .reg_field_names import const_name

_DT_REGISTER = "DataTypeRegister"
_DT_REGISTER_GROUP = "DataTypeRegisterGroup"
_DT_STRUCT = "DataTypeStruct"


# --- small helpers ---------------------------------------------------------

def _strip_pkg(name: Optional[str]) -> str:
    return name.split("::")[-1] if name else name


def _sv_bit_type(bits: int) -> str:
    return "bit" if bits == 1 else f"bit [{bits - 1}:0]"


def _reg_value_sv(reg_dtype) -> str:
    """SV type for a register's value (struct name or ``bit [w-1:0]``)."""
    vt = reg_dtype.register_value_type
    if _dt_name(vt) == _DT_STRUCT:
        return _strip_pkg(vt.name)
    bits = getattr(vt, "bits", None) or getattr(reg_dtype, "size_bits", 32)
    return _sv_bit_type(int(bits))


def _reg_handle_type(reg_dtype) -> str:
    """``reg_c #(T[, ACC])`` for a register field; ACC omitted when READWRITE."""
    vt = _reg_value_sv(reg_dtype)
    acc = getattr(reg_dtype, "access_mode", "READWRITE") or "READWRITE"
    return f"reg_c #({vt})" if acc == "READWRITE" else f"reg_c #({vt}, {acc})"


# --- emission --------------------------------------------------------------

def emit_value_struct(struct_dtype) -> str:
    """The register value struct, with whatever the source documented it with.

    A register field's two comments occupy different places and say different
    things: the prose above it comes from the SystemRDL ``desc`` and is the part
    nothing downstream can re-derive -- a datasheet-versus-silicon divergence,
    typically -- while the bit range and access mode beside it are facts about
    the layout. Both come from the PSS source rather than being synthesized
    here, so there is one statement of each and no way for them to disagree.
    """
    name = _strip_pkg(struct_dtype.name)
    lines = ["  typedef struct packed {"]
    for f in reversed(struct_dtype.fields):   # LSB-first -> MSB-first
        lines += comment_lines(getattr(f, "doc", None), "    ")
        decl = f"    {_sv_bit_type(int(f.datatype.bits))} {f.name};"
        lines += append_trailing([decl], getattr(f, "doc_trailing", None))
    lines.append(f"  }} {name};")
    return "\n".join(lines)


def emit_field_consts(struct_dtype) -> str:
    """One ``localparam reg_field_t`` per scalar field of a value struct.

    Derived from ``struct_layout()`` -- the same function the packed struct's
    own field order comes from -- so a constant cannot disagree with the type it
    describes. That matters more than it looks: the two are emitted next to each
    other and a reader will assume they agree.

    Every scalar field gets one, including padding fields a model happens to
    have named `reserved`. Filtering by name would be a heuristic that silently
    drops a user's field for being unluckily named, and an unused localparam
    costs nothing.
    """
    layout = [fs for fs in struct_layout(struct_dtype) if fs.width]
    if not layout:
        return ""
    name = _strip_pkg(struct_dtype.name)
    width = max(fs.lsb + fs.width for fs in layout)
    if width > 64:
        # reg_field_t.mask is 64 bits; a wider register's upper fields would be
        # silently truncated into the wrong bits. reg_c cannot address one
        # either (ACC_W caps at 64), so this is unreachable today -- but it is
        # the kind of unreachable that stops being unreachable quietly.
        raise ValueError(
            f"register value struct '{name}' is {width} bits; field constants "
            f"support at most 64")
    just = max(len(const_name(name, fs.name)) for fs in layout)
    lines = [f"  // Field positions of {name}, for write_field/read_field."]
    for fs in layout:
        lines.append(
            f"  localparam reg_field_t {const_name(name, fs.name):{just}} = "
            f"'{{mask:64'h{fs.mask:016x}, shift:{fs.lsb}}};")
    return "\n".join(lines)


def emit_reg_group(group_dtype) -> str:
    cls = _strip_pkg(group_dtype.name)
    decls: List[str] = []
    ctor: List[str] = []

    for f in group_dtype.fields:
        if _is_reserved(f):
            continue
        if field_is_register(f):
            off = _scalar_offset(group_dtype, f.name)
            decls.append(f"    {_reg_handle_type(f.datatype):28} {f.name};")
            ctor.append(f"      {f.name} = new(bus, base + 64'h{off:x});")
        elif field_is_reg_group(f):
            off = _scalar_offset(group_dtype, f.name)
            gt = _strip_pkg(f.datatype.name)
            decls.append(f"    {gt:28} {f.name};")
            ctor.append(f"      {f.name} = new(bus, base + 64'h{off:x});")
        elif field_is_array(f):
            elem = array_element_type(f)
            size = array_size(f)
            base, stride = _array_base_stride(group_dtype, f.name)
            if _dt_name(elem) == _DT_REGISTER:
                ht = _reg_handle_type(elem)
            else:  # array of register groups
                ht = _strip_pkg(elem.name)
            decls.append(f"    {ht:28} {f.name}[{size}];")
            ctor.append(f"      foreach ({f.name}[i])")
            ctor.append(
                f"        {f.name}[i] = new(bus, base + 64'h{base:x} + i * 64'h{stride:x});")

    body = [f"  class {cls};"]
    body += decls
    body.append("")
    body.append("    function new(pss_mem_if bus, addr_handle_t base);")
    body += ctor
    body.append("    endfunction")
    body.append("  endclass")
    return "\n".join(body)


def lower_register_model(root_dtype) -> Tuple[str, List[str], List[str]]:
    """Return (sv_body, struct_names, group_names) for a register tree.

    ``root_dtype`` is a component, or a **list** of components whose register
    models share one package. The list form matters for component trees: a
    per-channel bank belongs to the channel component, so lowering only the
    root's own registers emits nothing for it.

    ``sv_body`` is the package-body text (value structs then reg-group classes,
    nested groups first). Names are the emitted (package-stripped) type names.
    """
    roots = root_dtype if isinstance(root_dtype, (list, tuple)) else [root_dtype]
    groups: List[object] = []
    seen = set()
    for r in roots:
        for g in collect_reg_groups(r):
            if id(g) not in seen:
                seen.add(id(g))
                groups.append(g)
    structs = collect_value_structs(groups)

    parts: List[str] = []
    parts.append("  // ----- Register value layouts (packed structs). -----")
    for s in structs:
        parts.append(emit_value_struct(s))
        consts = emit_field_consts(s)
        if consts:
            parts.append(consts)
        parts.append("")
    parts.append("  // ----- Register-group classes. -----")
    for g in groups:
        parts.append(emit_reg_group(g))
        parts.append("")

    struct_names = [_strip_pkg(s.name) for s in structs]
    group_names = [_strip_pkg(g.name) for g in groups]
    return "\n".join(parts), struct_names, group_names
