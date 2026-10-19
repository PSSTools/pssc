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

from ..diag import Refusal
from ..reg_field_resolve import field_width
from .progseq_model import (OffsetFoldError, _dt_name, array_element_type,
                            array_base_stride, field_is_array, field_is_reg_group,
                            field_is_register, scalar_offset)

__all__ = ["RegAccessor", "collect_accessors", "is_reserved",
           "is_reserved_name", "is_strided_reg_array", "prim_bits",
           "value_bits", "value_struct"]

_DT_REGISTER = "DataTypeRegister"
_DT_REGISTER_GROUP = "DataTypeRegisterGroup"
_DT_STRUCT = "DataTypeStruct"


def is_reserved_name(name: str) -> bool:
    """Is *name* a reserved placeholder's: ``_`` followed by a non-digit?

    `_reserved0`, `_rsvd`, `_pad` and `_` are placeholders. `_0`, `_12` and
    `_3a` are NOT: a leading underscore before a digit is the only way to
    write an identifier that is really a number (a register generator names
    the elements of an interleaved series `_0`, `_1`, ...), so it is a name
    escape, and those members are ordinary registers.
    """
    return name.startswith("_") and not name[1:2].isdigit()


def is_reserved(field) -> bool:
    """Is *field* a reserved placeholder (`is_reserved_name`)?

    THE rule: every backend, the conformance kit and the reserved-access
    check (`reserved_access`) ask it here, so a member is either surfaced
    everywhere or refused everywhere. Reserved gaps hold the address space
    open and are never surfaced in a generated API. Their space is already
    accounted for in the following siblings' offsets, so skipping them
    changes no address.
    """
    return is_reserved_name(getattr(field, "name", "") or "")


def is_strided(stride: Optional[int], width: int) -> bool:
    """Is a register array with element ``width`` bytes laid out at
    ``stride`` with a gap after each element?

    THE predicate for "a strided register array": the C layout declares such
    an array as an element struct spanning the stride, the register its `v`
    member (`is_strided_reg_array`), and the C access path ends in `.v`
    exactly when this says so (`RegAccessor.strided`). One function, so the
    declaration and the access cannot disagree. ``None`` (a scalar register)
    is never strided.
    """
    return stride is not None and stride != width


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
    #: The register ARRAY's own stride, when the register is an element of
    #: one (it is then also the last of ``strides``); ``None`` for a scalar
    #: register, including one inside an array of groups.
    elem_stride: Optional[int] = None

    @property
    def strided(self) -> bool:
        """An element of a register array with a gap after each element
        (`is_strided`): C reaches it as the `v` member of a struct."""
        return is_strided(self.elem_stride, self.prim_bits // 8)

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
            try:
                field(group_dt, f, segs, const_off, strides)
            except OffsetFoldError as e:
                # The innermost declaration whose offset failed: where a
                # refusal points (`OpModelTarget.check_register_offsets`).
                if getattr(e, "node", None) is None:
                    e.node = f
                raise

    def field(group_dt, f, segs, const_off, strides):
        if is_reserved(f):
            return
        if field_is_register(f):
            accs.append(_mk(segs, f.name, f.datatype,
                            const_off + scalar_offset(group_dt, f.name),
                            strides, f))
        elif field_is_reg_group(f):
            visit(f.datatype, segs + [f.name],
                  const_off + scalar_offset(group_dt, f.name), strides)
        elif field_is_array(f):
            elem = array_element_type(f)
            base, stride = array_base_stride(group_dt, f.name)
            if _dt_name(elem) == _DT_REGISTER:
                accs.append(_mk(segs, f.name, elem, const_off + base,
                                strides + [stride], f, elem_stride=stride))
            elif _dt_name(elem) == _DT_REGISTER_GROUP:
                visit(elem, segs + [f.name], const_off + base,
                      strides + [stride])

    for f in getattr(comp_dtype, "fields", []) or []:
        try:
            if field_is_reg_group(f):
                visit(f.datatype, [f.name], 0, [])
            elif (field_is_array(f)
                    and _dt_name(array_element_type(f)) == _DT_REGISTER_GROUP):
                base, stride = array_base_stride(comp_dtype, f.name)
                visit(array_element_type(f), [f.name], base, [stride])
        except OffsetFoldError as e:
            if getattr(e, "node", None) is None:
                e.node = f
            raise
    return accs


def _mk(segs, name, reg_dtype, const_off, strides, decl=None,
        elem_stride: Optional[int] = None) -> RegAccessor:
    bits = value_bits(reg_dtype)
    if bits > 64:
        # The register's size selects its primitive (LRM 21.14.5 a), and the
        # widest is read64/write64. Accessing it at 64 bits, as every target
        # did, reads and writes part of it with no diagnostic.
        from ..diag import at
        from ..driver import CompileError
        msg = (f"register '{'.'.join(list(segs) + [name])}' is {bits} bits "
               f"wide; no read/write primitive is wider than 64 bits (LRM "
               f"21.14.5)")
        raise CompileError(msg, [at(decl, msg)] if decl is not None else [])
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
        elem_stride=elem_stride,
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
#
# OVERLAP is legal. A register map commonly describes one aperture through
# two views -- a normal block and a debug/copy view of the same bytes, two
# widths of one register -- and those are aliases of the same hardware, not
# two storage locations and not alternatives a discriminator picks between. A
# set of members whose byte ranges connect (transitively: A overlaps B, B
# overlaps C) becomes ONE `UNION` member: an anonymous C union whose
# alternatives are the source members, each at its own address, so the PSS
# access path is still the C member path. Only an access produces bus
# traffic; declaring an alias reads nothing.
#
# C, not this walk, decides where a member of a union alternative lands and
# how big the union is (it rounds to its alignment). So the walk models C's
# alignment rule -- conservatively, a primitive aligned to its own size --
# refuses an alternative that rule would move, and, where rounding makes the
# union longer than its members, says so explicitly with a span pad, so the
# union's size is the same on every ABI whose alignment is no stricter than
# the model's. The emitted `_Static_assert`s remain the proof.

#: `MapMember.kind` values.
REG, GROUP, PAD, UNION = "reg", "group", "pad", "union"


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
    #: UNION only: the alternatives, in address order. Each is
    #: ``(lead, member)``: ``lead`` is a PAD from the union's origin to
    #: ``member`` (None when ``member`` starts at the origin), and the pair is
    #: one anonymous struct in C.
    alts: Tuple[Tuple[Optional["MapMember"], "MapMember"], ...] = ()
    #: The C alignment this walk assumes for the member (see `_c_align`).
    align: int = 1

    @property
    def is_array(self) -> bool:
        return self.count is not None

    @property
    def total_size(self) -> int:
        return self.stride * self.count if self.is_array else self.elem_size

    @property
    def end(self) -> int:
        return self.offset + self.total_size


def is_strided_reg_array(m: MapMember) -> bool:
    """A REG member that is an array whose elements are not packed: C
    declares it as ``struct { uintN_t v; <pad> _rsvd[k]; } X[n]``, so its
    indexing steps by the stride, not the width (`is_strided`)."""
    return m.kind == REG and m.is_array and is_strided(m.stride, m.elem_size)


@dc.dataclass(frozen=True)
class RegMap:
    """A register group's layout: ordered members, gaps declared, total size."""

    dtype: Any
    members: Tuple[MapMember, ...]
    size: int
    align: int = 1

    @property
    def named(self) -> List[MapMember]:
        """Every REG and GROUP member, union alternatives included, in
        address order: the members a C access path can name."""
        out: List[MapMember] = []
        for m in self.members:
            if m.kind == UNION:
                out.extend(a for _, a in m.alts if a.kind != PAD)
            elif m.kind != PAD:
                out.append(m)
        return out

    @property
    def registers(self) -> List[MapMember]:
        return [m for m in self.named if m.kind == REG]

    @property
    def groups(self) -> List[MapMember]:
        return [m for m in self.named if m.kind == GROUP]

    @property
    def has_union(self) -> bool:
        return any(m.kind == UNION for m in self.members)


class RegMapError(Refusal):
    """A register group that cannot be expressed as a struct.

    A `Refusal`: the model's layout, not a compiler bug, so `OpModelTarget.run`
    reports it as a `CompileError` located at the innermost group that has a
    location (`build_reg_map` adds each group it was laying out)."""


def _pad_name(offset: int) -> str:
    """`_rsvd_<hex offset>`.

    Named for WHERE it is rather than counted, so inserting a register early in
    a map does not renumber every hole after it -- which would make a diff of
    two generated maps unreadable in exactly the situation (a register map
    changed) where it most needs to be read.
    """
    return f"_rsvd_{offset:x}"


def _group_size(group_dt, min_size: Optional[int] = None, _seen=None,
                _sizes=None) -> Tuple[int, int]:
    """``(bytes spanned, C alignment)`` of ``group_dt``, at least ``min_size``."""
    rm = build_reg_map(group_dt, min_size=min_size, _seen=_seen, sizes=_sizes)
    return rm.size, rm.align


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

    Overlapping members become one `UNION` member (see the module comment
    above `MapMember`).

    Raises `RegMapError` on a map a struct cannot express -- an alias C's
    alignment would move, an array whose stride is smaller than its element,
    one group type used with two different strides, or a group that contains
    itself.
    """
    _seen = set() if _seen is None else _seen
    if id(group_dt) in _seen:
        raise RegMapError(
            f"register group '{_name(group_dt)}' contains itself; a recursive "
            f"map has no struct layout")
    _seen = _seen | {id(group_dt)}
    try:
        return _build_reg_map(group_dt, min_size, sizes, _seen)
    except RegMapError as e:
        raise e.at_least(group_dt)


def _build_reg_map(group_dt, min_size, sizes, _seen) -> RegMap:
    placed: List[MapMember] = []
    for f in getattr(group_dt, "fields", []) or []:
        placed.append(_place(group_dt, f, _seen, sizes or {}))

    # Address order; a tie keeps declaration order (`sort` is stable), so
    # the layout -- and every name in it -- is deterministic.
    placed.sort(key=lambda m: m.offset)
    members: List[MapMember] = []
    cursor = 0
    for region in _regions(placed):
        start = region[0].offset
        if start > cursor:
            members.append(_pad(cursor, start - cursor))
        m = region[0] if len(region) == 1 else _union(group_dt, region)
        members.append(m)
        cursor = m.end

    align = max((x.align for x in members), default=1)
    # C rounds a struct up to its alignment, so a group whose last register
    # ends short of it is that much longer in C than its registers: state it
    # with a tail pad, as the union's span does, rather than leave the size
    # to the ABI. Without it, `sizeof` disagreed with this walk and every
    # use of the group after it moved.
    want = _round_up(cursor, align) if min_size is None else min_size
    if cursor < want:
        members.append(_pad(cursor, want - cursor))
        cursor = want
    if min_size is not None and cursor > min_size:
        raise RegMapError(
            f"register group '{_name(group_dt)}' spans 0x{cursor:x} bytes but "
            f"is used with a stride of 0x{min_size:x}; the elements would "
            f"overlap")
    if cursor % align:
        raise RegMapError(
            f"register group '{_name(group_dt)}' is used with a stride of "
            f"0x{cursor:x}, which is not a multiple of the {align}-byte "
            f"alignment of its widest register; C would round each element "
            f"up and index the array at a different stride")
    align = max(align, max((x.align for x in members), default=1))
    return RegMap(dtype=group_dt, members=tuple(members), size=cursor,
                  align=align)


def _regions(placed: List[MapMember]) -> List[List[MapMember]]:
    """``placed`` (address order) cut into connected overlap regions.

    A member joins the open region when it starts before the region's end --
    the running maximum, so containment and chains (A overlaps B, B overlaps C,
    A and C apart) are one region. Adjacent is not overlapping: a member
    starting exactly at the end opens a new region. The end is C's, not the
    intervals': a union rounds up to its alignment (`_union_extent`), and a
    member inside that rounding would collide with the union's storage, so it
    is an alternative too.
    """
    regions: List[List[MapMember]] = []
    end = 0
    for m in placed:
        if regions and m.offset < end:
            regions[-1].append(m)
        else:
            regions.append([m])
        cur = regions[-1]
        end = (cur[0].end if len(cur) == 1
               else cur[0].offset + _union_extent(cur)[0])
    return regions


def _union_extent(region: List[MapMember]) -> Tuple[int, int]:
    """``(bytes, alignment)`` C gives the union over ``region``.

    Each alternative is the member, or a struct of a lead pad and the member;
    the union is as long as its longest alternative rounded up to the
    strictest alignment among them.
    """
    start = region[0].offset
    align, natural = 1, 0
    for m in region:
        rel = m.offset - start
        a = max(m.align, _pad_align(start, rel)) if rel else m.align
        align = max(align, a)
        natural = max(natural, rel + m.total_size)
    return _round_up(natural, align), align


def _union(group_dt, region: List[MapMember]) -> MapMember:
    """One UNION member: ``region``'s members as aliases at one origin."""
    start = region[0].offset
    size, align = _union_extent(region)
    if start % align:
        raise RegMapError(
            f"'{_name(group_dt)}': the registers aliased at 0x{start:x} "
            f"({', '.join(m.name for m in region)}) need {align}-byte "
            f"alignment, and C would move a union at 0x{start:x} to the "
            f"next multiple of {align}; give the overlapping views an "
            f"origin aligned to their widest register")
    alts: List[Tuple[Optional[MapMember], MapMember]] = []
    natural = 0
    for m in region:
        rel = m.offset - start
        if rel % m.align:
            raise RegMapError(
                f"'{_name(group_dt)}.{m.name}' at 0x{m.offset:x} aliases "
                f"{region[0].name} at 0x{start:x}, but sits 0x{rel:x} bytes "
                f"into the overlap, which is not a multiple of its "
                f"{m.align}-byte alignment; C would move it. A register "
                f"view must start at a multiple of its own width from the "
                f"start of the views it overlaps")
        lead = None
        if rel:
            lead = dc.replace(_pad(start, rel), name=f"{_pad_name(start)}__{m.name}")
        alts.append((lead, m))
        natural = max(natural, rel + m.total_size)
    if size > natural:
        # Rounding made the union longer than any alternative. Say so: C's
        # rounding is per-ABI, a declared span is the same everywhere.
        alts.append((None, dc.replace(_pad(start, size),
                                      name=f"{_pad_name(start)}_{start + size:x}")))
    return MapMember(kind=UNION, name=f"_alias_{start:x}", dtype=None,
                     offset=start, elem_size=size, count=None, stride=size,
                     alts=tuple(alts), align=align)


def _round_up(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def _pad_align(offset: int, nbytes: int) -> int:
    """A PAD is `uint32_t[]` when word-aligned and whole words, else bytes
    (`lower_reg_model._member_c_type`)."""
    return 4 if (offset % 4 == 0 and nbytes % 4 == 0) else 1


def _pad(offset: int, nbytes: int) -> MapMember:
    return MapMember(kind=PAD, name=_pad_name(offset), dtype=None,
                     offset=offset, elem_size=nbytes, count=None,
                     stride=nbytes, align=_pad_align(offset, nbytes))


def _place(group_dt, f, _seen, sizes) -> MapMember:
    """One field, placed. Reserved instances keep their own name."""
    if field_is_register(f):
        width = prim_bits(value_bits(f.datatype)) // 8
        return MapMember(kind=REG, name=f.name, dtype=f.datatype,
                         offset=scalar_offset(group_dt, f.name),
                         elem_size=width, count=None, stride=width,
                         align=width)
    if field_is_reg_group(f):
        sz, al = _group_size(f.datatype, min_size=sizes.get(id(f.datatype)),
                             _seen=_seen, _sizes=sizes)
        return MapMember(kind=GROUP, name=f.name, dtype=f.datatype,
                         offset=scalar_offset(group_dt, f.name),
                         elem_size=sz, count=None, stride=sz, align=al)
    if field_is_array(f):
        elem = array_element_type(f)
        base, stride = array_base_stride(group_dt, f.name)
        count = int(f.datatype.size)
        if _dt_name(elem) == _DT_REGISTER:
            width = prim_bits(value_bits(elem)) // 8
            if stride < width:
                raise RegMapError(
                    f"'{_name(group_dt)}.{f.name}' has stride 0x{stride:x} but "
                    f"each element is {width} bytes; the elements overlap", f)
            if stride % width:
                # The element is a struct spanning the stride
                # (`is_strided_reg_array`), and C rounds a struct up to its
                # alignment -- the register's width -- so any other stride
                # would index the array at a different one.
                raise RegMapError(
                    f"'{_name(group_dt)}.{f.name}' has stride 0x{stride:x}, "
                    f"which is not a multiple of its {width}-byte element; "
                    f"C would align each element to {width} bytes and index "
                    f"the array at a different stride", f)
            return MapMember(kind=REG, name=f.name, dtype=elem, offset=base,
                             elem_size=width, count=count, stride=stride,
                             align=width)
        if _dt_name(elem) == _DT_REGISTER_GROUP:
            # min_size=stride: the element struct must be exactly the stride,
            # or C's indexing and the device's disagree from bank 1 onward.
            need = max(stride, sizes.get(id(elem), 0))
            if need > stride:
                raise RegMapError(
                    f"'{_name(group_dt)}.{f.name}' has stride 0x{stride:x}, "
                    f"but '{_name(elem)}' is also used with a stride of "
                    f"0x{need:x}; one struct cannot be both sizes")
            sz, al = _group_size(elem, min_size=stride, _seen=_seen,
                                 _sizes=sizes)
            return MapMember(kind=GROUP, name=f.name, dtype=elem, offset=base,
                             elem_size=sz, count=count, stride=stride,
                             align=al)
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
