"""The register-group struct layout (`reg_layout.build_reg_map`).

The layout is a claim about ADDRESSES, and an address is the one thing a golden
snapshot cannot check -- a wrong offset frozen into a snapshot stays green
forever. So the offsets are asserted here against the register map as declared,
and the emitted C additionally carries a `_Static_assert` per member so the
claim is re-checked by the C compiler against the struct it actually built.

The gaps are the point. A folded per-register offset does not care what sits
between two registers; a struct member's position is decided by everything
declared before it, so an unmapped hole must be declared or every later
register silently moves.
"""
from __future__ import annotations

import pytest

from pssc.targets.reg_layout import (
    GROUP, PAD, REG, UNION, RegMapError, build_reg_map, reg_maps_for,
)


# -- stand-ins ---------------------------------------------------------------
#
# Hand-built rather than elaborated from PSS: this module is arithmetic over a
# declared layout, and a test that has to compile a model to state "0x14 is a
# three-word hole" cannot show the hole to a reader.

class _Reg:
    """A `DataTypeRegister` stand-in."""

    def __init__(self, bits=32, access="READWRITE"):
        self.size_bits = bits
        self.access_mode = access
        self.register_value_type = None


class _Group:
    """A `DataTypeRegisterGroup` stand-in with an explicit offset map."""

    def __init__(self, name, fields, offsets, arrays=None):
        self.name = name
        self.fields = fields
        self.offset_map = offsets
        self.array_map = arrays or {}


class _Field:
    def __init__(self, name, datatype):
        self.name = name
        self.datatype = datatype


class _Array:
    def __init__(self, element_type, size):
        self.element_type = element_type
        self.size = size


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    """Point the layout walk's predicates at the stand-ins above."""
    import pssc.targets.reg_layout as rl

    monkeypatch.setattr(rl, "field_is_register",
                        lambda f: isinstance(f.datatype, _Reg))
    monkeypatch.setattr(rl, "field_is_reg_group",
                        lambda f: isinstance(f.datatype, _Group))
    monkeypatch.setattr(rl, "field_is_array",
                        lambda f: isinstance(f.datatype, _Array))
    monkeypatch.setattr(rl, "array_element_type", lambda f: f.datatype.element_type)
    monkeypatch.setattr(rl, "scalar_offset", lambda g, n: g.offset_map[n])
    monkeypatch.setattr(rl, "array_base_stride", lambda g, n: g.array_map[n])
    monkeypatch.setattr(rl, "value_bits", lambda dt: dt.size_bits)
    monkeypatch.setattr(rl, "_dt_name",
                        lambda dt: ("DataTypeRegisterGroup"
                                    if isinstance(dt, _Group) else
                                    "DataTypeRegister" if isinstance(dt, _Reg)
                                    else "?"))


def _bank():
    return _Group(
        "ch_regs_c",
        [_Field(n, _Reg()) for n in ("csr", "sz", "adr0", "am0",
                                     "adr1", "am1", "desc", "swptr")],
        {n: i * 4 for i, n in enumerate(("csr", "sz", "adr0", "am0",
                                         "adr1", "am1", "desc", "swptr"))},
    )


def _top(bank):
    """The WB DMA shape: five registers, a three-word HOLE, then bank[4]."""
    return _Group(
        "regs_c",
        [_Field("csr", _Reg()), _Field("int_msk_a", _Reg()),
         _Field("int_msk_b", _Reg()), _Field("int_src_a", _Reg(32, "READONLY")),
         _Field("int_src_b", _Reg(32, "READONLY")),
         _Field("bank", _Array(bank, 4))],
        {"csr": 0x00, "int_msk_a": 0x04, "int_msk_b": 0x08,
         "int_src_a": 0x0c, "int_src_b": 0x10},
        {"bank": (0x20, 0x20)},
    )


# -- the layout --------------------------------------------------------------

def test_a_packed_bank_has_no_padding():
    m = build_reg_map(_bank())
    assert [x.name for x in m.members] == ["csr", "sz", "adr0", "am0",
                                           "adr1", "am1", "desc", "swptr"]
    assert m.size == 0x20
    assert all(x.kind == REG for x in m.members)


def test_an_unmapped_hole_becomes_a_named_pad():
    """0x14..0x1c is declared by nothing. Without a member there, `bank` would
    land at 0x14 and every channel register would be three words low."""
    m = build_reg_map(_top(_bank()))
    pads = [x for x in m.members if x.kind == PAD]
    assert len(pads) == 1
    assert pads[0].name == "_rsvd_14"
    assert (pads[0].offset, pads[0].total_size) == (0x14, 0xc)


def test_the_pad_is_named_for_its_offset_not_its_ordinal():
    """So inserting a register early in a map does not renumber every hole
    after it -- a diff of two generated maps has to stay readable in exactly
    the case (the map changed) where it most needs reading."""
    m = build_reg_map(_top(_bank()))
    assert [x.name for x in m.members if x.kind == PAD] == ["_rsvd_14"]


def test_the_array_lands_where_the_map_says():
    m = build_reg_map(_top(_bank()))
    bank = next(x for x in m.members if x.name == "bank")
    assert bank.kind == GROUP
    assert (bank.offset, bank.count, bank.stride) == (0x20, 4, 0x20)
    assert m.size == 0x20 + 4 * 0x20


def test_an_element_is_padded_up_to_its_stride():
    """A bank of 0x18 used with a 0x20 stride needs 8 trailing bytes, or C's
    own indexing and the device's disagree from bank 1 onward."""
    short = _Group("short_c", [_Field("a", _Reg()), _Field("b", _Reg())],
                   {"a": 0, "b": 4})
    m = build_reg_map(short, min_size=0x20)
    assert m.size == 0x20
    assert m.members[-1].kind == PAD
    assert m.members[-1].total_size == 0x20 - 0x8


# -- what a struct cannot express -------------------------------------------

def test_a_view_c_alignment_would_move_is_refused():
    """A 64-bit view 4 bytes into another: C aligns it to 8, so the struct
    would put it 4 bytes from where the register is."""
    bad = _Group("bad_c", [_Field("a", _Reg(64)), _Field("b", _Reg(64))],
                 {"a": 0x0, "b": 0x4})
    with pytest.raises(RegMapError, match="alignment"):
        build_reg_map(bad)


def test_an_alias_region_c_would_move_is_refused():
    """A union holding a 64-bit view at 0x4: C would put the union at 0x8."""
    bad = _Group("bad_c", [_Field("pre", _Reg()), _Field("a", _Reg(64)),
                           _Field("b", _Reg())],
                 {"pre": 0x0, "a": 0x4, "b": 0x4})
    with pytest.raises(RegMapError, match="origin aligned"):
        build_reg_map(bad)


def test_a_layout_refusal_is_a_located_model_error():
    """`OpModelTarget.run` reports a `Refusal` as a CompileError; a raw
    exception was a traceback."""
    from pssc.diag import Refusal
    bad = _Group("bad_c", [_Field("a", _Reg(64)), _Field("b", _Reg(64))],
                 {"a": 0x0, "b": 0x4})
    with pytest.raises(Refusal) as ei:
        build_reg_map(bad)
    assert bad in ei.value.nodes


def test_a_stride_smaller_than_the_element_is_refused():
    bad = _Group("bad_c", [_Field("r", _Array(_Reg(), 4))], {},
                 {"r": (0x0, 0x2)})          # 4-byte registers, 2-byte stride
    with pytest.raises(RegMapError, match="overlap"):
        build_reg_map(bad)


def test_a_group_too_big_for_its_stride_is_refused():
    with pytest.raises(RegMapError, match="stride"):
        build_reg_map(_bank(), min_size=0x10)


# -- emission order ----------------------------------------------------------

def test_maps_come_back_innermost_first():
    """C has no forward reference for a struct used by value, so a bank's
    layout must be emitted before the map that embeds an array of it."""
    bank = _bank()
    order = reg_maps_for([_top(bank)])
    names = [m.dtype.name for m in order]
    assert names.index("ch_regs_c") < names.index("regs_c")


# -- one struct per type, sized for every use --------------------------------

def _timer():
    """A 0x10 bank used as `ch[2]` at stride 0x100 -- and, through the list
    handed to `reg_maps_for`, standalone as well."""
    ch = _Group("ch_c", [_Field(n, _Reg()) for n in ("ctrl", "load",
                                                     "status", "count")],
                {"ctrl": 0x0, "load": 0x4, "status": 0x8, "count": 0xc})
    glb = _Group("glb_c", [_Field("id", _Reg())], {"id": 0x0})
    top = _Group("timer_c", [_Field("glb", glb), _Field("ch", _Array(ch, 2))],
                 {"glb": 0x0}, {"ch": (0x100, 0x100)})
    return ch, glb, top


@pytest.mark.parametrize("order", ["element_first", "holder_first"])
def test_an_array_element_is_padded_whatever_order_it_is_reached_in(order):
    """`collect_reg_groups` lists the element BEFORE the group that holds the
    array, and laying it out on first sight gave it no padding: `ch[1]` at
    0x110 in C, 0x200 on the device. The padded struct is still right where
    the type is used alone -- tail padding moves no member."""
    ch, glb, top = _timer()
    groups = [ch, glb, top] if order == "element_first" else [top, glb, ch]
    maps = reg_maps_for(groups)
    chs = [m for m in maps if m.dtype is ch]
    assert len(chs) == 1
    assert chs[0].size == 0x100
    assert [(x.name, x.offset) for x in chs[0].registers] == [
        ("ctrl", 0x0), ("load", 0x4), ("status", 0x8), ("count", 0xc)]
    tm = next(m for m in maps if m.dtype is top)
    arr = next(x for x in tm.members if x.name == "ch")
    assert (arr.offset, arr.stride, arr.elem_size) == (0x100, 0x100, 0x100)
    assert tm.size == 0x300
    names = [m.dtype.name for m in maps]
    assert names.index("ch_c") < names.index("timer_c")


def test_one_type_at_two_strides_is_refused():
    """One struct cannot be both sizes; the smaller stride would overlap."""
    ch, _, _ = _timer()
    top = _Group("two_c", [_Field("a", _Array(ch, 2)),
                           _Field("b", _Array(ch, 2))], {},
                 {"a": (0x0, 0x100), "b": (0x200, 0x200)})
    with pytest.raises(RegMapError, match="stride"):
        reg_maps_for([top])


def test_a_register_inside_a_padded_scalar_use_is_an_alias():
    """Padded for its array use, the struct runs past the register embedded
    right after it. That used to be refused; the register is now an alias
    inside the padding, at its own address."""
    ch, _, _ = _timer()
    top = _Group("tight_c", [_Field("one", ch), _Field("after", _Reg()),
                             _Field("arr", _Array(ch, 2))],
                 {"one": 0x0, "after": 0x10}, {"arr": (0x1000, 0x100)})
    tm = next(m for m in reg_maps_for([top]) if m.dtype is top)
    u = tm.members[0]
    assert u.kind == UNION and (u.offset, u.end) == (0x0, 0x100)
    assert [(a.name, a.offset) for a in tm.named] == [
        ("one", 0x0), ("after", 0x10), ("arr", 0x1000)]


def test_a_group_that_contains_itself_is_refused():
    """Overlap support must not turn recursion into an alias of itself."""
    g = _Group("loop_c", [], {})
    g.fields = [_Field("self_", g)]
    g.offset_map = {"self_": 0x0}
    with pytest.raises(RegMapError, match="contains itself"):
        build_reg_map(g)


# -- overlapping views: unions -------------------------------------------------
#
# Aliases of one aperture. Every source member keeps its name and address;
# the members whose byte ranges connect become ONE union.

def _alts(u):
    """``[(lead pad bytes or None, member name, member offset)]``."""
    return [(lead.total_size if lead else None, m.name, m.offset)
            for lead, m in u.alts]


def test_two_widths_at_one_offset_are_one_union():
    g = _Group("w_c", [_Field("w32", _Reg()), _Field("w16", _Reg(16)),
                       _Field("next", _Reg())],
               {"w32": 0x0, "w16": 0x0, "next": 0x4})
    m = build_reg_map(g)
    assert [x.kind for x in m.members] == [UNION, REG]
    assert _alts(m.members[0]) == [(None, "w32", 0), (None, "w16", 0)]
    assert m.members[0].end == 0x4 and m.size == 0x8


def test_same_origin_groups_of_different_extents_are_one_union():
    """The reported shape: a normal view and a copy view of one aperture,
    both at 0, the copy starting with its own padding."""
    big = _Group("big_c", [_Field(f"r{i}", _Reg()) for i in range(8)],
                 {f"r{i}": i * 4 for i in range(8)})
    copy = _Group("copy_c", [_Field("c", _Reg())], {"c": 0x18})
    top = _Group("ap_c", [_Field("normal", big), _Field("copy", copy)],
                 {"normal": 0x0, "copy": 0x0})
    m = build_reg_map(top)
    assert len(m.members) == 1
    u = m.members[0]
    assert _alts(u) == [(None, "normal", 0), (None, "copy", 0)]
    assert (u.elem_size, m.size) == (0x20, 0x20)


def test_partial_overlap_at_two_origins_leads_with_a_pad():
    g = _Group("p_c", [_Field("a", _Reg(64)), _Field("b", _Reg())],
               {"a": 0x0, "b": 0x4})       # a spans 0x0..0x8, b sits inside
    u = build_reg_map(g).members[0]
    assert _alts(u) == [(None, "a", 0), (4, "b", 4)]
    assert u.alts[1][0].name == "_rsvd_0__b"


def test_a_chain_is_one_region_and_adjacency_is_not_overlap():
    """a overlaps b, b overlaps c, a and c apart: one union. d starts where
    the union ends and stays a plain member; a gap either side is a pad."""
    pair = _Group("pair_c", [_Field("lo", _Reg()), _Field("hi", _Reg())],
                  {"lo": 0x0, "hi": 0x4})
    g = _Group("ch_c", [_Field("first", _Reg()), _Field("a", pair),
                        _Field("b", pair), _Field("c", pair),
                        _Field("d", _Reg()), _Field("late", _Reg())],
               {"first": 0x0, "a": 0x10, "b": 0x14, "c": 0x18,
                "d": 0x20, "late": 0x40})
    m = build_reg_map(g)
    assert [(x.kind, x.offset) for x in m.members] == [
        (REG, 0x0), (PAD, 0x4), (UNION, 0x10), (REG, 0x20), (PAD, 0x24),
        (REG, 0x40)]
    assert _alts(m.members[2]) == [(None, "a", 0x10), (4, "b", 0x14),
                                   (8, "c", 0x18)]


def test_a_contained_interval_is_an_alias():
    g = _Group("in_c", [_Field("wide", _Reg(64)), _Field("lo", _Reg()),
                        _Field("hi", _Reg())],
               {"wide": 0x0, "lo": 0x0, "hi": 0x4})
    u = build_reg_map(g).members[0]
    assert _alts(u) == [(None, "wide", 0), (None, "lo", 0), (4, "hi", 4)]


def test_rounding_pulls_the_next_member_into_the_union():
    """64-bit `w` and an 8-byte pair at 0x4 end at 0xC, but C rounds the union
    to 0x10, so `next` at 0xC would sit in its storage: it is an alias too."""
    pair = _Group("pair_c", [_Field("lo", _Reg()), _Field("hi", _Reg())],
                  {"lo": 0x0, "hi": 0x4})
    g = _Group("r_c", [_Field("w", _Reg(64)), _Field("p", pair),
                       _Field("next", _Reg()), _Field("after", _Reg())],
               {"w": 0x0, "p": 0x4, "next": 0xC, "after": 0x10})
    m = build_reg_map(g)
    assert [(x.kind, x.offset) for x in m.members] == [
        (UNION, 0), (REG, 0x10), (PAD, 0x14)]
    assert (m.size, m.align) == (0x18, 8)      # rounded, and stated
    assert _alts(m.members[0]) == [(None, "w", 0), (4, "p", 4),
                                   (0xC, "next", 0xC)]


def test_rounding_past_every_view_is_declared_as_a_span():
    """The union is 0x10 in C though its views end at 0xC; a span pad states
    it, so the size does not depend on the ABI's rounding."""
    pair = _Group("pair_c", [_Field("lo", _Reg()), _Field("hi", _Reg())],
                  {"lo": 0x0, "hi": 0x4})
    g = _Group("s_c", [_Field("w", _Reg(64)), _Field("p", pair),
                       _Field("after", _Reg())],
               {"w": 0x0, "p": 0x4, "after": 0x10})
    u = build_reg_map(g).members[0]
    lead, span = u.alts[-1]
    assert lead is None and span.kind == PAD
    assert (span.name, span.total_size) == ("_rsvd_0_10", 0x10)


def test_overlapping_array_views_keep_their_strides():
    bank = _Group("bank_c", [_Field("x", _Reg())], {"x": 0x0})
    g = _Group("arr_c", [_Field("regs", _Array(_Reg(), 8)),
                         _Field("banks", _Array(bank, 2))],
               {}, {"regs": (0x0, 0x4), "banks": (0x8, 0x10)})
    maps = reg_maps_for([g])
    bm = next(m for m in maps if m.dtype is bank)
    assert bm.size == 0x10
    u = next(m for m in maps if m.dtype is g).members[0]
    assert _alts(u) == [(None, "regs", 0), (8, "banks", 8)]
    banks = u.alts[1][1]
    assert (banks.stride, banks.count, banks.end) == (0x10, 2, 0x28)
    names = [m.dtype.name for m in maps]
    assert names.index("bank_c") < names.index("arr_c")


def test_a_group_is_rounded_to_its_alignment_by_a_tail_pad():
    """C makes a struct with a 64-bit member a multiple of 8; the map says so
    instead of disagreeing with `sizeof`."""
    g = _Group("t_c", [_Field("d", _Reg(64)), _Field("w", _Reg())],
               {"d": 0x0, "w": 0x8})
    m = build_reg_map(g)
    assert (m.size, m.members[-1].kind, m.members[-1].offset) == (0x10, PAD, 0xC)


def test_a_stride_c_would_round_is_refused():
    g = _Group("t_c", [_Field("d", _Reg(64))], {"d": 0x0})
    with pytest.raises(RegMapError, match="alignment"):
        build_reg_map(g, min_size=0xC)


def test_a_non_overlapping_map_has_no_union():
    m = build_reg_map(_top(_bank()))
    assert UNION not in [x.kind for x in m.members]
