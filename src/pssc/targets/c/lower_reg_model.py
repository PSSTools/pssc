"""Lower a PSS register tree to C: value unions + baked inline accessors.

Emits, for the register subtree reachable from a root component:
  * one C11 anonymous-union value type per register value struct (fields in
    declaration order -- LSB-first, the opposite of SV; little-endian assumed);
  * a baked ``static inline`` accessor trio (``_addr``/``_read``/``_write``) per
    register, with the address arithmetic folded to gen-time constants (only the
    component ``base`` and any array indices are runtime). Every memory access
    in them is rendered by the funnel in ``mem_access.py`` -- this module owns
    the addresses, not the spelling of the access -- and they are emitted
    identically across all link styles; only ``pssc_bus`` (defined in
    lower_progseq) varies.

Design: docs/design/pss-c-cpp-progseq-gen-design.md (§3.3, §3.4).
"""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional

from ..progseq_model import (
    _dt_name, collect_reg_groups, collect_value_structs,
)
from ..reg_layout import (collect_accessors, prim_bits as _prim_bits,
                          value_bits as _reg_value_bits, value_struct)
from ...reg_field_resolve import struct_layout
from ..comments import BLOCK, append_trailing, comment_lines
from .mem_access import DEFAULT as DEFAULT_MEM, MemAccess
from .style import coerce as _style

_DT_REGISTER = "DataTypeRegister"
_DT_REGISTER_GROUP = "DataTypeRegisterGroup"
_DT_STRUCT = "DataTypeStruct"

#: The qualifier every accessor and field helper here carries.
#:
#: These are DERIVED FROM THE REGISTER MAP, not from what the operations
#: happen to call, so the set always covers registers no body in this model
#: touches. Since the split (implementation in the ``.c``), those land in a
#: single translation unit as `static inline` with no caller -- which clang
#: reports under -Wunused-function and -Werror turns into a failed build.
#:
#: Pruning to the called set was the alternative and is worse: the accessors
#: are the API's vocabulary for the device, so pruning makes deleting one
#: operation silently delete the only way to reach a register. Unconditional
#: rather than only-in-the-.c, because one spelling that is always right beats
#: two that agree today; `PSSC_MAYBE_UNUSED` is empty on a compiler that has no
#: such attribute (pssc_env.h).
_SI = "PSSC_MAYBE_UNUSED static inline"


# --- small helpers ---------------------------------------------------------

def _strip_pkg(name: Optional[str]) -> str:
    return name.split("::")[-1] if name else name


def c_struct_name(struct_dtype) -> str:
    """C value-type name for a register value struct: ``dma_csr_s`` -> ``dma_csr_t``."""
    n = _strip_pkg(struct_dtype.name)
    return (n[:-2] + "_t") if n.endswith("_s") else (n + "_t")


def _struct_total_bits(struct_dtype) -> int:
    return sum(int(f.datatype.bits) for f in struct_dtype.fields)


def _reg_is_struct(reg_dtype) -> bool:
    return value_struct(reg_dtype) is not None


def _reg_c_type(reg_dtype) -> str:
    """C value type for a register: a value-union name or ``uintN_t``."""
    vt = reg_dtype.register_value_type
    if _dt_name(vt) == _DT_STRUCT:
        return c_struct_name(vt)
    return f"uint{_prim_bits(_reg_value_bits(reg_dtype))}_t"


def accessor_base(prefix: str, segs: List[str], reg: str, style=None) -> str:
    """The shared accessor name stem: ``<prefix>_<seg>_..._<reg>``.

    The body emitter (lower_progseq) reconstructs the same name from a register
    access expression, so the two stay in lock-step without a shared registry --
    which is exactly why both must ask the same policy for it.
    """
    return _style(style).reg_symbol(prefix, segs, reg)


# --- value unions ----------------------------------------------------------

def emit_value_union(struct_dtype, reg_style: str = "bitfields") -> str:
    name = c_struct_name(struct_dtype)
    total = _struct_total_bits(struct_dtype)
    prim = _prim_bits(total)
    ut = f"uint{prim}_t"
    if reg_style == "accessors":
        return _emit_accessor_struct(struct_dtype, name, ut)
    lines = [f"typedef union {{ {ut} raw; struct {{"]
    # struct_layout emits exactly one slice per declared field, in order, so
    # the two zip. The slice carries the arithmetic; the field carries what the
    # source said about it.
    for fs, f in zip(struct_layout(struct_dtype), struct_dtype.fields):
        # The prose above, the layout facts beside -- the same split the PSS
        # source uses. `doc_trailing` already states the bit range, and more
        # besides (access mode, reset), so it replaces the range synthesized
        # here rather than joining it. Without it, nothing changes.
        lines += comment_lines(getattr(f, "doc", None), "    ", BLOCK)
        trailing = (getattr(f, "doc_trailing", None)
                    or f"[{fs.lsb + fs.width - 1}:{fs.lsb}]")
        decl = f"    {ut} {fs.name:12} : {fs.width:2};"
        lines += append_trailing([decl], trailing, BLOCK)
    lines.append(f"}}; }} {name};")
    return "\n".join(lines)


def _emit_accessor_struct(struct_dtype, name: str, ut: str) -> str:
    """Layout-independent fallback (--reg-style accessors): a plain raw word plus
    inline shift/mask helpers ``<name>_<FIELD>_get/_set``."""
    lines = [f"typedef struct {{ {ut} raw; }} {name};"]
    for fs in struct_layout(struct_dtype):
        if fs.name.startswith("_"):
            continue                      # reserved gap: space, but no accessor
        mask = (1 << fs.width) - 1
        lines.append(
            f"{_SI} {ut} {name}_{fs.name}_get({name} v) "
            f"{{ return ({ut})((v.raw >> {fs.lsb}) & 0x{mask:x}u); }}")
        lines.append(
            f"{_SI} void {name}_{fs.name}_set({name} *v, {ut} x) "
            f"{{ v->raw = (v->raw & ~(0x{mask:x}u << {fs.lsb})) | "
            f"(({ut})(x & 0x{mask:x}u) << {fs.lsb}); }}")
    return "\n".join(lines)


def value_structs_for(comps):
    """Every register value struct reachable from ``comps``, in emission order.

    Takes the whole component list, not just the root, and deduplicates: a
    sub-component's register group is usually ALSO reachable from the root
    (WB DMA's per-channel bank is `wb_dma_c.regs.bank[i]` and `wb_dma_ch_c.regs`
    at once), and emitting its value types twice is a redefinition error.

    Separate from `lower_value_unions` because the caller now has to PARTITION
    this list -- the layouts are implementation and belong in the .c, except
    for any the exported API mentions -- and it cannot partition a string.
    """
    out, seen = [], set()
    for comp in comps:
        for s in collect_value_structs(collect_reg_groups(comp)):
            if id(s) in seen:
                continue
            seen.add(id(s))
            out.append(s)
    return out


def lower_value_unions(comps, reg_style: str = "bitfields", *,
                       structs=None) -> str:
    """Value layouts, as header/impl text. ``structs`` restricts the set.

    ``None`` means every register value struct reachable from ``comps`` --
    which is what a `--header-only` build wants, since it has one file. A split
    build passes the subset that belongs in the file being assembled.

    Empty in, empty out: a section with no content contributes no banner, so a
    model with no registers does not get a "Register value layouts" heading
    over nothing.
    """
    structs = value_structs_for(comps) if structs is None else list(structs)
    if not structs:
        return ""
    parts = ["/* ----- Register value layouts. ----- */"]
    for s in structs:
        parts.append(emit_value_union(s, reg_style))
    return "\n".join(parts)


# --- baked accessors -------------------------------------------------------

@dc.dataclass
class _Acc:
    base: str            # accessor name stem
    c_type: str          # value type
    prim: int            # transaction width 8/16/32/64
    is_struct: bool
    access: str          # READWRITE / READONLY / WRITEONLY
    const_off: int
    strides: List[int]   # one per array index parameter


def _collect_accessors(root_dtype, prefix: str, style=None) -> List[_Acc]:
    """The C accessor set for one component: the shared walk, named and typed.

    `reg_layout.collect_accessors` decides WHERE each register is -- the walk,
    the folded offsets, the strides -- because that answer is not C's and a
    second backend must not compute it a second way. What is C's is here: the
    symbol stem the policy spells, and the value type the accessor hands back.
    """
    return [
        _Acc(
            base=accessor_base(prefix, list(a.segs), a.name, style),
            c_type=_reg_c_type(a.dtype),
            prim=a.prim_bits,
            is_struct=a.is_struct,
            access=a.access,
            const_off=a.const_off,
            strides=list(a.strides),
        )
        for a in collect_accessors(root_dtype)
    ]


def _idx_params(n: int, leading_comma: bool) -> str:
    if n == 0:
        return ""
    s = ", ".join(f"int i{k}" for k in range(n))
    return (", " + s) if leading_comma else s


def _idx_args(n: int) -> str:
    return "".join(f", i{k}" for k in range(n))


def _addr_expr(acc: _Acc) -> str:
    terms = [f"s->base + 0x{acc.const_off:x}u"]
    for k, stride in enumerate(acc.strides):
        terms.append(f"(pssc_addr_t)i{k} * 0x{stride:x}u")
    return " + ".join(terms)


def emit_accessor(acc: _Acc, prefix_t: str, mem: MemAccess = None,
                  addr_only: bool = False) -> str:
    """The accessor set for one register.

    ``addr_only`` emits the `_addr` accessor and nothing else, for a style
    whose `reg_accessor_form()` is `macro`. The ADDRESS accessor survives every
    form deliberately: the folded offsets are the model's statement about the
    device, and a style that had to recompute them from `_Acc.const_off` would
    be a supported way to generate firmware pointed at the wrong register. A
    house macro gets the identity (`acc.base`) AND the address (`<base>_addr(s)`),
    and computes neither.
    """
    mem = mem or DEFAULT_MEM
    n = len(acc.strides)
    idx_p = _idx_params(n, leading_comma=True)
    idx_a = _idx_args(n)
    fn = lambda kind: mem.accessor(acc.base, kind)   # noqa: E731
    addr = f"{fn('addr')}(s{idx_a})"
    lines = [
        f"{_SI} pssc_addr_t {fn('addr')}(const {prefix_t} *s{idx_p}) "
        f"{{ return {_addr_expr(acc)}; }}",
    ]
    if addr_only:
        return "\n".join(lines)
    if acc.access != "WRITEONLY":
        read = mem.read(acc.prim, "s", addr)
        if acc.is_struct:
            lines.append(
                f"{_SI} {acc.c_type} {fn('read')}({prefix_t} *s{idx_p}) "
                f"{{ {acc.c_type} v; v.raw = {read}; return v; }}")
        else:
            lines.append(
                f"{_SI} {acc.c_type} {fn('read')}({prefix_t} *s{idx_p}) "
                f"{{ return {read}; }}")
    if acc.access != "READONLY":
        raw = "v.raw" if acc.is_struct else "v"
        lines.append(
            f"{_SI} void {fn('write')}({prefix_t} *s{idx_p}, {acc.c_type} v) "
            f"{{ {mem.write(acc.prim, 's', addr, raw)}; }}")

    # Raw accessors. `_read`/`_write` above are typed -- they hand back the
    # value union -- and the masked forms work in bits, so they need the
    # untyped pair. On a scalar-valued register these duplicate `_read`/
    # `_write`; emitted anyway so the call site never has to ask which kind of
    # register it is holding.
    ut = f"uint{acc.prim}_t"
    if acc.access != "WRITEONLY":
        lines.append(
            f"{_SI} {ut} {fn('read_val')}({prefix_t} *s{idx_p}) "
            f"{{ return {mem.read(acc.prim, 's', addr)}; }}")
    if acc.access != "READONLY":
        lines.append(
            f"{_SI} void {fn('write_val')}({prefix_t} *s{idx_p}, {ut} v) "
            f"{{ {mem.write(acc.prim, 's', addr, 'v')}; }}")

    # The masked write -- PSS 3.1 §21.14.1:
    #
    #     REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)
    #
    # THE READ IS PART OF THE DEFINITION, not an implementation choice. On a
    # register whose read has side effects -- a channel CSR that clears its
    # status and interrupt-source bits -- a masked write has them too. Which is
    # also why it needs both directions: a WRITEONLY register cannot supply the
    # current value, and a READONLY one cannot take the result.
    #
    # The compiler folds write_field / write_fields / write_masked to a
    # (mask, val) constant pair before reaching here, so one accessor serves all
    # four spellings and no field name is involved.
    if acc.access not in ("READONLY", "WRITEONLY"):
        lines.append(
            f"{_SI} void {fn('write_val_masked')}({prefix_t} *s{idx_p}, "
            f"{ut} mask, {ut} val) "
            f"{{ {mem.masked_write(ut, acc.base, f's{idx_a}')} }}")
    return "\n".join(lines)


def lower_accessors(comps, prefixes, *, link_style: str = "vtable",
                    mem: MemAccess = None, style=None) -> str:
    """Accessors for every component, each keyed to ITS OWN handle and base.

    Per component rather than per model, because the address a register lives at
    depends on which component you reach it through. `wb_dma_ch_c.regs.csr` is
    `ch->base + 0x0`; the same physical register reached from the root is
    `dma->base + 0x20 + 0x20*i`. Both are emitted -- `wb_dma_ch_regs_csr_write`
    and `wb_dma_regs_bank_csr_write` -- and both are correct, because they take
    different handles. Folding them into one would mean picking a base, which
    means picking which of the two call sites to break.
    """
    style = _style(style)
    mem = mem or style.mem_access()
    addr_only = style.reg_accessor_form() == "macro"
    # Under a macro mandate the read/write accessors are not merely unnecessary
    # -- emitting inline functions that nothing calls would read to a reviewer
    # as though the mandate had not been applied. The ADDRESS accessors stay:
    # see `emit_accessor`.
    parts = ["/* ----- Baked register addresses. ----- */" if addr_only else
             "/* ----- Baked inline register accessors. ----- */"]
    for comp in comps:
        prefix = prefixes[comp]
        for a in _collect_accessors(comp, prefix, style):
            parts.append(emit_accessor(a, style.type_name(prefix), mem,
                                       addr_only))
    return "\n".join(parts)


def accessor_map(comps, prefixes, style=None):
    """`{accessor stem: _Acc}` for every register, per component.

    The body emitter needs the REGISTER, not just its name: a policy rendering
    `ACME_REG_WRITE32` is handed the access mode, the width and the folded
    offset, none of which can be recovered from the underscore-joined stem it
    used to reconstruct. Built once per generation and handed down, rather than
    re-walked per operation.
    """
    style = _style(style)
    out = {}
    for comp in comps:
        for a in _collect_accessors(comp, prefixes[comp], style):
            out[a.base] = a
    return out


# --- the register map, as C -------------------------------------------------
#
# The struct the bare-metal driver follows to reach a register. This replaces
# the per-register accessor set for the no-context link styles, and the reason
# is scale: an IP with a thousand registers, of which a driver touches thirty,
# got a thousand accessor sets. A layout is DATA proportional to the register
# map and leaves the code proportional to the registers actually used.

def map_type_name(group_dtype, style=None) -> str:
    """C type for a register group's layout: ``wb_dma_regs_c`` -> ``wb_dma_regs_t``.

    Derived from the GROUP's type name and not from the component's prefix,
    because one group is commonly reached through two components -- WB DMA's
    per-channel bank is `wb_dma_c.regs.bank[i]` and `wb_dma_ch_c.regs` at once.
    Naming it per component would declare the same layout twice under two names,
    and the two would be the same bytes until the day someone edited one.
    """
    n = _strip_pkg(getattr(group_dtype, "name", None) or "regs")
    if n.endswith("_c"):
        n = n[:-2]
    return f"{n}_t"


def _member_c_type(m, style=None) -> str:
    from ..reg_layout import GROUP, PAD, prim_bits, value_bits
    if m.kind == GROUP:
        return map_type_name(m.dtype, style)
    if m.kind == PAD:
        return "uint32_t" if (m.offset % 4 == 0 and m.total_size % 4 == 0) \
            else "uint8_t"
    return f"uint{prim_bits(value_bits(m.dtype))}_t"


def _pad_count(m) -> int:
    return m.total_size // 4 if (m.offset % 4 == 0 and m.total_size % 4 == 0) \
        else m.total_size


def emit_reg_map(rm, style=None) -> str:
    """One register group's layout, plus the assertions that pin it.

    THE ASSERTIONS ARE THE POINT, not decoration. This struct is a claim about
    where each register sits, and C -- not this generator -- decides where a
    member actually lands: an alignment rule, a member the layout walk mis-sized,
    a padding byte nobody accounted for, and every register after it moves with
    no diagnostic at all. A `_Static_assert` per member turns that class of
    defect into a compile error naming the register, which is the only check
    available here that a golden snapshot cannot silently freeze in the wrong
    state.
    """
    from ..reg_layout import GROUP, PAD, REG

    name = map_type_name(rm.dtype, style)
    lines = [f"typedef struct {{"]
    for m in rm.members:
        ct = _member_c_type(m, style)
        if m.kind == PAD:
            decl = f"{ct} {m.name}[{_pad_count(m)}];"
            lines.append(f"    {decl:<40} /* 0x{m.offset:03x} reserved */")
            continue
        # READONLY as `const`: the write primitives take `volatile void *`, so a
        # `const` member's address will not convert and writing it is a compile
        # error. That is exactly what the absent `_write` accessor used to buy.
        access = getattr(m.dtype, "access_mode", "READWRITE") or "READWRITE"
        qual = "const " if (m.kind == REG and access == "READONLY") else ""
        sub = f"[{m.count}]" if m.is_array else ""
        decl = f"{qual}{ct} {m.name}{sub};"
        note = f"0x{m.offset:03x}" + (f" {access.lower()}" if m.kind == REG else "")
        lines.append(f"    {decl:<40} /* {note} */")
    lines.append(f"}} {name};")

    lines.append(f"PSSC_STATIC_ASSERT(sizeof({name}) == 0x{rm.size:x}u,"
                 f" {name}_size);")
    for m in rm.members:
        if m.kind == PAD:
            continue
        lines.append(
            f"PSSC_STATIC_ASSERT(offsetof({name}, {m.name}) == 0x{m.offset:x}u,"
            f" {name}_{m.name}_offset);")
    return "\n".join(lines)


def lower_reg_maps(model, style=None) -> str:
    """Every register group's layout, innermost first.

    Order is `reg_maps_for`'s, which derives it from the maps themselves: C has
    no forward reference for a struct used by value, so a bank's layout must
    precede the map that embeds an array of it.
    """
    from ..reg_layout import reg_maps_for

    maps = reg_maps_for(model.reg_groups)
    if not maps:
        return ""
    parts = ["/* ----- Register map. The layout IS the address arithmetic. ----- */"]
    for rm in maps:
        parts.append(emit_reg_map(rm, style))
    return "\n".join(parts)
