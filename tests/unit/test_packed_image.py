"""`targets.packed_image`: how read_struct / write_struct move a packed_s image.

Access width follows the struct's size: up to 64 bits it is one raw value (one
access, or the fewest power-of-two pieces); wider, member by member, each at
its own size. See docs/design/write-struct-plan.md.
"""
import pytest

from pssc import Parser
from pssc.ast2ir import AstToIrTranslator
from pssc.targets.packed_image import (
    Access, PackedImageError, access_plan, struct_bits, union_fields,
    union_refusal)

_TYPES = """
import addr_reg_pkg::*;

enum mode_e : bit[2] { M0, M1, M2 };

struct csr_s : packed_s<> {          // 32 bits
    bit[12] tot_sz;
    bit[4]  reserved0;
    bool    dst_sel;
    bit[1]  src_sel;
    mode_e  mode;
    int[12] delta;
}

struct desc_s : packed_s<> {         // 128 bits
    csr_s   csr;
    bit[32] adr0;
    bit[32] adr1;
    bit[32] next;
}

struct b8_s  : packed_s<> { bit[5] a; bit[3] b; }
struct b24_s : packed_s<> { bit[20] a; bit[4] b; }
struct b48_s : packed_s<> { bit[40] a; bit[8] b; }
struct b57_s : packed_s<> { bit[57] a; }
struct b64_s : packed_s<> { bit[64] a; }

struct wide_s : packed_s<> {         // 192 bits, a 48-bit struct inside
    bit[64] a;
    b48_s   b;
    bit[16] c;
    bit[64] d;
}

struct deep_s : packed_s<> {         // 256 bits, a 128-bit struct inside
    desc_s  d;
    bit[64] x;
    bit[64] y;
}

struct straddle_s : packed_s<> { bit[32] a; bit[12] b; bit[52] c; }
struct misaligned_s : packed_s<> { bit[8] a; bit[32] b; bit[32] c; bit[24] d; }
struct big_s : packed_s<BIG_ENDIAN> { bit[32] a; }
struct nest64_s : packed_s<> { b8_s a; bit[8] b; }
struct plain_s { bit[32] a; }
"""


@pytest.fixture(scope="module")
def tm():
    parser = Parser()
    parser.parses([("t.pss", _TYPES)])
    return AstToIrTranslator(debug=False).translate(parser.link()).type_map


def _t(tm, name):
    return tm[name]


def _shape(plan):
    return [(a.offset, a.width, a.path, a.raw, a.shift) for a in plan]


def test_union_fields_place_lsb_first_with_sign(tm):
    f = {u.name: u for u in union_fields(_t(tm, "csr_s"))}
    assert [(n, f[n].lsb, f[n].width) for n in
            ("tot_sz", "reserved0", "dst_sel", "src_sel", "mode", "delta")] == [
        ("tot_sz", 0, 12), ("reserved0", 12, 4), ("dst_sel", 16, 1),
        ("src_sel", 17, 1), ("mode", 18, 2), ("delta", 20, 12)]
    assert f["delta"].signed and not f["tot_sz"].signed


def test_struct_of_32_bits_is_one_write32(tm):
    assert _shape(access_plan(_t(tm, "csr_s"))) == [(0, 32, (), True, 0)]


def test_descriptor_is_four_word_members(tm):
    # Exactly the accesses write_descriptor.pss makes by hand.
    assert struct_bits(_t(tm, "desc_s")) == 128
    assert _shape(access_plan(_t(tm, "desc_s"))) == [
        (0, 32, ("csr",), True, 0),
        (4, 32, ("adr0",), False, 0),
        (8, 32, ("adr1",), False, 0),
        (12, 32, ("next",), False, 0)]


@pytest.mark.parametrize("name,expect", [
    ("b8_s",  [(0, 8, 0)]),
    ("b24_s", [(0, 16, 0), (2, 8, 16)]),
    ("b48_s", [(0, 32, 0), (4, 16, 32)]),
    ("b57_s", [(0, 64, 0)]),                  # 8 bytes cover 57 bits
    ("b64_s", [(0, 64, 0)]),
])
def test_access_width_follows_size(tm, name, expect):
    plan = access_plan(_t(tm, name))
    assert [(a.offset, a.width, a.shift) for a in plan] == expect
    assert all(a.raw and a.path == () for a in plan)


def test_nested_union_keeps_its_offset(tm):
    assert _shape(access_plan(_t(tm, "wide_s"))) == [
        (0, 64, ("a",), False, 0),
        (8, 32, ("b",), True, 0), (12, 16, ("b",), True, 32),
        (14, 16, ("c",), False, 0),
        (16, 64, ("d",), False, 0)]


def test_nested_wide_struct_recurses(tm):
    plan = access_plan(_t(tm, "deep_s"))
    assert [(a.offset, a.path) for a in plan] == [
        (0, ("d", "csr")), (4, ("d", "adr0")), (8, ("d", "adr1")),
        (12, ("d", "next")), (16, ("x",)), (24, ("y",))]


@pytest.mark.parametrize("name,frag", [
    ("straddle_s", "'b' is 12 bits"),
    ("misaligned_s", "not 4-byte aligned"),
    ("big_s", "BIG_ENDIAN"),
    ("nest64_s", "not a scalar"),
    ("plain_s", "not a packed_s struct"),
])
def test_refusals_name_the_problem(tm, name, frag):
    with pytest.raises(PackedImageError, match=frag):
        access_plan(_t(tm, name))


def test_union_eligibility(tm):
    assert union_refusal(_t(tm, "csr_s")) is None
    assert union_refusal(_t(tm, "b64_s")) is None
    assert "wider than 64" in union_refusal(_t(tm, "straddle_s"))
    assert "not a scalar" in union_refusal(_t(tm, "desc_s"))
    assert "not a scalar" in union_refusal(_t(tm, "nest64_s"))
    assert "big-endian" in union_refusal(_t(tm, "big_s"))
    assert union_fields(_t(tm, "plain_s")) is None
