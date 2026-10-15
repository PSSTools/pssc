"""Where every register sits, with the address arithmetic already folded.

`progseq_model` answers one question at a time ("what is this instance's
offset?"). This is the walk that asks all of them: it descends a component's
register subtree and returns one :class:`RegAccessor` per reachable register,
carrying the constant byte offset from the component's base and one stride per
array index between the component and the register.

It was `c/lower_reg_model._collect_accessors`, and it is here because it is the
first thing a SECOND backend needs and the last thing it should write for
itself. Nothing in the walk is C: it reads the PSS tree and does arithmetic.
What is C -- the accessor's spelling, its value type, whether it is emitted as
an inline function or a macro -- stays in the C backend, which builds its own
`_Acc` from these.

An address is the one thing in a generated programming API that a golden
snapshot cannot check (see `conformance.py`): a wrong offset frozen into a
snapshot stays green forever. Two backends computing offsets two ways is
therefore the worst kind of duplication available here, and this is the answer
to it.

Design: docs/design/generator-style-extensions-design.md; plan P8.T1.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

from ..reg_field_resolve import field_width
from .progseq_model import (_dt_name, array_element_type, array_base_stride,
                            field_is_array, field_is_reg_group,
                            field_is_register, scalar_offset)

__all__ = ["RegAccessor", "collect_accessors", "is_reserved", "prim_bits",
           "value_bits", "value_struct"]

_DT_REGISTER = "DataTypeRegister"
_DT_REGISTER_GROUP = "DataTypeRegisterGroup"
_DT_STRUCT = "DataTypeStruct"


def is_reserved(field) -> bool:
    """Is *field* a reserved placeholder -- a leading underscore?

    Reserved gaps hold the address space open and are never surfaced in a
    generated API. Their space is already accounted for in the following
    siblings' offsets, so skipping them changes no address.
    """
    return getattr(field, "name", "").startswith("_")


def prim_bits(bits: int) -> int:
    """The bus transaction width a register of *bits* is accessed at.

    There is no primitive wider than 64 bits (LRM 21.14.5 a), so a wider
    register has no access at all; `collect_accessors` refuses one naming it,
    and this raises rather than answer 64, which read half of it."""
    for w in (8, 16, 32, 64):
        if bits <= w:
            return w
    raise ValueError(f"no read/write primitive is {bits} bits wide")


def value_struct(reg_dtype) -> Optional[Any]:
    """The register's value STRUCT, or ``None`` if its value is a plain scalar."""
    vt = getattr(reg_dtype, "register_value_type", None)
    return vt if _dt_name(vt) == _DT_STRUCT else None


def value_bits(reg_dtype) -> int:
    """How many bits the register's value occupies.

    `size_bits` when the model states one -- `reg_c<dma_csr_s, READWRITE, 32>`
    says 32 and means it, even where the fields sum to less.
    """
    sb = getattr(reg_dtype, "size_bits", None)
    if sb:
        return int(sb)
    vs = value_struct(reg_dtype)
    if vs is not None:
        return sum(field_width(f) for f in vs.fields)
    vt = getattr(reg_dtype, "register_value_type", None)
    return int(getattr(vt, "bits", 32) or 32)


@dc.dataclass(frozen=True)
class RegAccessor:
    """One reachable register, with its address arithmetic folded.

    The address is ``base + const_off + sum(index[k] * strides[k])``: everything
    a generation can know is a constant here, and the only runtime terms are the
    component's base and the array indices, in the order the path crosses them.
    """

    #: Group / array field names between the component and the register.
    segs: Tuple[str, ...]
    #: The register instance's own name.
    name: str
    #: The `DataTypeRegister`, for anything a backend needs that is not here.
    dtype: Any
    #: Its value struct, or ``None`` for a scalar-valued register.
    value_struct: Optional[Any]
    #: Value width in bits, and the transaction width it rounds up to.
    value_bits: int
    prim_bits: int
    #: ``READWRITE`` / ``READONLY`` / ``WRITEONLY``.
    access: str
    #: Constant byte offset from the component's base.
    const_off: int
    #: One stride per array index crossed, outermost first.
    strides: Tuple[int, ...]

    @property
    def is_struct(self) -> bool:
        return self.value_struct is not None

    @property
    def readable(self) -> bool:
        return self.access != "WRITEONLY"

    @property
    def writable(self) -> bool:
        return self.access != "READONLY"

    @property
    def path(self) -> Tuple[str, ...]:
        """The full instance path, register included. `("regs", "channels", "CSR")`."""
        return tuple(self.segs) + (self.name,)


def collect_accessors(comp_dtype) -> List[RegAccessor]:
    """Every register reachable from *comp_dtype*, in declaration order.

    Depth-first at the point of the field, so a nested group's registers appear
    where the group is declared rather than after all of the outer group's --
    which is the order the C backend has emitted accessors in since it had them,
    and the order a reader of the PSS source expects.

    An array of GROUPS contributes a stride and keeps descending; an array of
    REGISTERS is one accessor with a stride. Both cases exist in the worked
    example (`channels[31]` is the first, and models with `reg_c ... [n]` are
    the second).
    """
    accs: List[RegAccessor] = []

    def visit(group_dt, segs: List[str], const_off: int, strides: List[int]):
        for f in group_dt.fields:
            if is_reserved(f):
                continue
            if field_is_register(f):
                accs.append(_mk(segs, f.name, f.datatype,
                                const_off + scalar_offset(group_dt, f.name),
                                strides))
            elif field_is_reg_group(f):
                visit(f.datatype, segs + [f.name],
                      const_off + scalar_offset(group_dt, f.name), strides)
            elif field_is_array(f):
                elem = array_element_type(f)
                base, stride = array_base_stride(group_dt, f.name)
                if _dt_name(elem) == _DT_REGISTER:
                    accs.append(_mk(segs, f.name, elem, const_off + base,
                                    strides + [stride]))
                elif _dt_name(elem) == _DT_REGISTER_GROUP:
                    visit(elem, segs + [f.name], const_off + base,
                          strides + [stride])

    for f in getattr(comp_dtype, "fields", []) or []:
        if field_is_reg_group(f):
            visit(f.datatype, [f.name], 0, [])
        elif (field_is_array(f)
                and _dt_name(array_element_type(f)) == _DT_REGISTER_GROUP):
            base, stride = array_base_stride(comp_dtype, f.name)
            visit(array_element_type(f), [f.name], base, [stride])
    return accs


def _mk(segs, name, reg_dtype, const_off, strides) -> RegAccessor:
    bits = value_bits(reg_dtype)
    if bits > 64:
        # The register's size selects its primitive (LRM 21.14.5 a), and the
        # widest is read64/write64. Accessing it at 64 bits, as every target
        # did, reads and writes part of it with no diagnostic.
        from ..driver import CompileError
        raise CompileError(
            f"register '{'.'.join(list(segs) + [name])}' is {bits} bits wide; "
            f"no read/write primitive is wider than 64 bits (LRM 21.14.5)")
    return RegAccessor(
        segs=tuple(segs),
        name=name,
        dtype=reg_dtype,
        value_struct=value_struct(reg_dtype),
        value_bits=value_bits(reg_dtype),
        prim_bits=prim_bits(value_bits(reg_dtype)),
        access=getattr(reg_dtype, "access_mode", "READWRITE") or "READWRITE",
        const_off=const_off,
        strides=tuple(strides),
    )


# --- the map, as a C struct would lay it out --------------------------------
#
# `collect_accessors` above answers "where is this register" one register at a
# time, with every offset folded to a constant. That is what a per-register
# accessor needs and it is why it exists.
#
# What follows answers a different question -- "what does this register group
# LOOK LIKE" -- and it exists because the folded constants are the wrong shape
# for an IP with a thousand registers. One `_addr` function per register is code
# proportional to the register map; a struct is DATA proportional to the
# register map and code proportional to the registers actually touched. The
# offsets stop being restated per accessor and get stated once, by the layout,
# with the compiler folding the arithmetic at each use.
#
# The gaps are the whole difficulty. A folded offset does not care what sits
# between two registers; a struct member's position is decided by everything
# declared before it, so an unmapped hole has to be declared or every later
# register silently moves. Hence `PAD` members and, in the C emitter, a
# `_Static_assert` per member: the layout is a claim about addresses, and a
# claim about addresses that nothing checks is the one defect a golden snapshot
# can never catch.

#: `MapMember.kind` values.
REG, GROUP, PAD = "reg", "group", "pad"


@dc.dataclass(frozen=True)
class MapMember:
    """One member of a register group's struct layout."""

    kind: str                 # REG / GROUP / PAD
    name: str                 # C member name (PAD: the synthesised `_rsvd_*`)
    dtype: Any                # register or group datatype; None for PAD
    offset: int               # byte offset within the enclosing group
    elem_size: int            # bytes occupied by ONE element
    count: Optional[int]      # array length, or None for a scalar member
    stride: int               # byte stride between elements (== elem_size when packed)

    @property
    def is_array(self) -> bool:
        return self.count is not None

    @property
    def total_size(self) -> int:
        return self.stride * self.count if self.is_array else self.elem_size

    @property
    def end(self) -> int:
        return self.offset + self.total_size


@dc.dataclass(frozen=True)
class RegMap:
    """A register group's layout: ordered members, gaps declared, total size."""

    dtype: Any
    members: Tuple[MapMember, ...]
    size: int

    @property
    def registers(self) -> List[MapMember]:
        return [m for m in self.members if m.kind == REG]

    @property
    def groups(self) -> List[MapMember]:
        return [m for m in self.members if m.kind == GROUP]


class RegMapError(Exception):
    """A register group that cannot be expressed as a struct."""


def _pad_name(offset: int) -> str:
    """`_rsvd_<hex offset>`.

    Named for WHERE it is rather than counted, so inserting a register early in
    a map does not renumber every hole after it -- which would make a diff of
    two generated maps unreadable in exactly the situation (a register map
    changed) where it most needs to be read.
    """
    return f"_rsvd_{offset:x}"


def _group_size(group_dt, min_size: Optional[int] = None, _seen=None,
                _sizes=None) -> int:
    """Bytes spanned by ``group_dt``, at least ``min_size``."""
    return build_reg_map(group_dt, min_size=min_size, _seen=_seen,
                         sizes=_sizes).size


def build_reg_map(group_dt, *, min_size: Optional[int] = None,
                  sizes=None, _seen=None) -> RegMap:
    """The struct layout of one register group.

    Members come back in ADDRESS order with every hole declared as a PAD, so a C
    emitter can render the list verbatim and get the offsets right by
    construction rather than by arithmetic it repeats.

    ``min_size`` pads the tail. It carries the stride of an enclosing array:
    a group used as `bank[4]` with stride 0x20 must have `sizeof == 0x20` or C's
    own indexing lands between banks, and a trailing hole in the last register
    of a bank is invisible without it.

    ``sizes`` maps ``id(group type)`` to the size every use of that type must
    have (`element_sizes`). There is ONE C struct per group type, so a group
    embedded here is laid out at that size, not at the size this one use would
    have asked for; without it, a group embedded both as an array element and
    as anything else gets two layouts, and whichever the emitter keeps is wrong
    for the other use. ``None`` lays out each embedded group for its own use
    alone.

    Raises `RegMapError` on a map a struct cannot express -- overlapping
    instances, an array whose stride is smaller than its element, or one group
    type used with two different strides.
    """
    _seen = set() if _seen is None else _seen
    if id(group_dt) in _seen:
        raise RegMapError(
            f"register group '{_name(group_dt)}' contains itself; a recursive "
            f"map has no struct layout")
    _seen = _seen | {id(group_dt)}

    placed: List[MapMember] = []
    for f in getattr(group_dt, "fields", []) or []:
        placed.append(_place(group_dt, f, _seen, sizes or {}))

    placed.sort(key=lambda m: m.offset)
    members: List[MapMember] = []
    cursor = 0
    for m in placed:
        if m.offset < cursor:
            prev = members[-1].name if members else "the start of the group"
            raise RegMapError(
                f"'{_name(group_dt)}.{m.name}' at 0x{m.offset:x} overlaps "
                f"{prev}, which ends at 0x{cursor:x}; a struct cannot place "
                f"two instances at one address")
        if m.offset > cursor:
            members.append(_pad(cursor, m.offset - cursor))
        members.append(m)
        cursor = m.end

    if min_size is not None and cursor < min_size:
        members.append(_pad(cursor, min_size - cursor))
        cursor = min_size
    if min_size is not None and cursor > min_size:
        raise RegMapError(
            f"register group '{_name(group_dt)}' spans 0x{cursor:x} bytes but "
            f"is used with a stride of 0x{min_size:x}; the elements would "
            f"overlap")
    return RegMap(dtype=group_dt, members=tuple(members), size=cursor)


def _pad(offset: int, nbytes: int) -> MapMember:
    return MapMember(kind=PAD, name=_pad_name(offset), dtype=None,
                     offset=offset, elem_size=nbytes, count=None,
                     stride=nbytes)


def _place(group_dt, f, _seen, sizes) -> MapMember:
    """One field, placed. Reserved instances keep their own name."""
    if field_is_register(f):
        return MapMember(kind=REG, name=f.name, dtype=f.datatype,
                         offset=scalar_offset(group_dt, f.name),
                         elem_size=prim_bits(value_bits(f.datatype)) // 8,
                         count=None,
                         stride=prim_bits(value_bits(f.datatype)) // 8)
    if field_is_reg_group(f):
        sz = _group_size(f.datatype, min_size=sizes.get(id(f.datatype)),
                         _seen=_seen, _sizes=sizes)
        return MapMember(kind=GROUP, name=f.name, dtype=f.datatype,
                         offset=scalar_offset(group_dt, f.name),
                         elem_size=sz, count=None, stride=sz)
    if field_is_array(f):
        elem = array_element_type(f)
        base, stride = array_base_stride(group_dt, f.name)
        count = int(f.datatype.size)
        if _dt_name(elem) == _DT_REGISTER:
            width = prim_bits(value_bits(elem)) // 8
            if stride < width:
                raise RegMapError(
                    f"'{_name(group_dt)}.{f.name}' has stride 0x{stride:x} but "
                    f"each element is {width} bytes; the elements overlap")
            return MapMember(kind=REG, name=f.name, dtype=elem, offset=base,
                             elem_size=width, count=count, stride=stride)
        if _dt_name(elem) == _DT_REGISTER_GROUP:
            # min_size=stride: the element struct must be exactly the stride,
            # or C's indexing and the device's disagree from bank 1 onward.
            need = max(stride, sizes.get(id(elem), 0))
            if need > stride:
                raise RegMapError(
                    f"'{_name(group_dt)}.{f.name}' has stride 0x{stride:x}, "
                    f"but '{_name(elem)}' is also used with a stride of "
                    f"0x{need:x}; one struct cannot be both sizes")
            sz = _group_size(elem, min_size=stride, _seen=_seen, _sizes=sizes)
            return MapMember(kind=GROUP, name=f.name, dtype=elem, offset=base,
                             elem_size=sz, count=count, stride=stride)
    raise RegMapError(
        f"'{_name(group_dt)}.{getattr(f, 'name', '?')}' is neither a register, "
        f"a register group, nor an array of either; it has no place in a "
        f"register map")


def _name(dtype) -> str:
    nm = getattr(dtype, "name", None) or _dt_name(dtype)
    return nm.split("::")[-1]


def element_sizes(groups, *, strides=None) -> Dict[int, int]:
    """``id(group type)`` -> the size its ONE struct must have.

    A group type used as an array element must span exactly its stride, and
    there is one C struct per type, so the struct is padded for every use at
    once: the largest stride any array of it has (plus ``strides``, a caller's
    own requirement per root). Tail padding moves no member, so the padded
    struct is still right where the type is used alone; a use that cannot take
    the padding is refused when its map is built.

    A pass of its own, ahead of any layout, because a use can be reached in
    any order: `collect_reg_groups` lists an element type BEFORE the group
    holding the array, and laying it out on first sight is what left
    `ch[2]` at stride 0x100 with a 0x10 element.
    """
    need: Dict[int, int] = dict(strides or {})
    seen = set()

    def walk(g):
        if id(g) in seen:
            return
        seen.add(id(g))
        for f in getattr(g, "fields", []) or []:
            if field_is_reg_group(f):
                walk(f.datatype)
            elif field_is_array(f):
                elem = array_element_type(f)
                if _dt_name(elem) == _DT_REGISTER_GROUP:
                    _, stride = array_base_stride(g, f.name)
                    need[id(elem)] = max(need.get(id(elem), 0), stride)
                    walk(elem)

    for g in groups:
        walk(g)
    return need


def reg_maps_for(groups, *, strides=None) -> "List[RegMap]":
    """Layouts for ``groups``, innermost first, ONE per group type.

    ORDER IS THE POINT: C has no forward reference for a struct used by value,
    so a bank's layout must be emitted before the map that embeds an array of
    it. Dependency order is derived here, from the maps themselves, rather than
    left to the caller's iteration order.

    Each map is built once, at the size every use of its type needs
    (`element_sizes`), so the order ``groups`` arrive in cannot decide which
    use's layout wins.
    """
    sizes = element_sizes(groups, strides=strides)
    by_id, order = {}, []

    def visit(g):
        if id(g) in by_id:
            return
        m = build_reg_map(g, min_size=sizes.get(id(g)), sizes=sizes)
        for sub in m.groups:
            visit(sub.dtype)
        by_id[id(g)] = m
        order.append(m)

    for g in groups:
        visit(g)
    return order
