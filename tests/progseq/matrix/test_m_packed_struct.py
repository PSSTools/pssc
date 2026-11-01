"""`read_struct` / `write_struct` of packed_s structs (LRM 21.12, 21.13.1).

The access width follows the struct's size (docs/design/write-struct-plan.md):
a struct of 8, 16, 32 or 64 bits is one access of that width; another size of
at most 64 bits is the fewest power-of-two accesses covering its bytes, lowest
address first; a wider struct is moved member by member, each at its own size.
The fields sit in the image LSB-first in declaration order (21.13.1.1).

Every trace here is worked out by hand from that layout. The C target lowers
the calls, and so does SV; C++ and Python are the plan's later steps, strict
xfails until they land.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, Refused

NOT_YET = "write-struct-plan.md: read_struct/write_struct not lowered yet"
LATER = on(("cpp", "py"), NOT_YET)

#: 32 bits: n [11:0], r [15:12], en [16], m [18:17], d [31:19] (signed).
#: A 128-bit descriptor of four words, csr first.
TYPES = """
enum m_e : bit[2] { M0, M1, M2 }
struct csr_s : packed_s<> { bit[12] n; bit[4] r; bool en; m_e m; int[13] d; }
struct desc_s : packed_s<> { csr_s csr; bit[32] a0; bit[32] a1; bit[32] nx; }
"""

#: `initialize` binds the handle every case uses (0x1000).
HANDLE = """
  addr_handle_t h;
  solve function void initialize(addr_handle_t base) { h = base; }
"""

CASES = {
    # csr: 0x123 | 0xA << 12 | 1 << 16 | 2 << 17 | (-2 & 0x1FFF) << 19
    #    = 0x00123 | 0x0A000 | 0x10000 | 0x40000 | 0xFFF00000 = 0xFFF5A123.
    "write_struct of a 128-bit descriptor, member by member": Case(TYPES + """
component pss_top {""" + HANDLE + """
  target function void run() {
    desc_s d;
    d.csr.n = 0x123; d.csr.r = 0xA; d.csr.en = true; d.csr.m = M2;
    d.csr.d = -2;
    d.a0 = 0x11111111; d.a1 = 0x22222222; d.nx = 0x33333333;
    write_struct(make_handle_from_handle(h, 0x10), d);
  }
}""", ["write 32 0x1010 0xfff5a123", "write 32 0x1014 0x11111111",
       "write 32 0x1018 0x22222222", "write 32 0x101c 0x33333333"],
        xfail=LATER, covers=("LIB-MEM", "LIB-MEM.5", "DT-PACK", "IP-51")),

    # The same word read back: each field unpacked, the signed one extended.
    "read_struct of a descriptor, then its fields": Case(TYPES + """
component pss_top {""" + HANDLE + """
  target function void run() {
    desc_s d;
    read_struct(h, d);
    addr_handle_t o = make_handle_from_handle(h, 0x20);
    write32(o, d.csr.n);
    write32(o, d.csr.r);
    write32(o, (bit[32])d.csr.en);
    write32(o, (bit[32])d.csr.m);
    write32(o, d.csr.d);
    write32(o, d.nx);
  }
}""", ["read 32 0x1000 0xfff5a123", "read 32 0x1004 0x44",
       "read 32 0x1008 0x55", "read 32 0x100c 0x66",
       "write 32 0x1020 0x123", "write 32 0x1020 0xa", "write 32 0x1020 0x1",
       "write 32 0x1020 0x2", "write 32 0x1020 0xfffffffe",
       "write 32 0x1020 0x66"],
        mem={0x1000: 0xFFF5A123, 0x1004: 0x44, 0x1008: 0x55, 0x100c: 0x66},
        xfail=LATER, covers=("LIB-MEM", "LIB-MEM.5", "DT-PACK", "IP-51")),

    # 48 bits is a write32 and a write16; 24 bits a write16 and a write8.
    # b48: raw 0x1234_89ABCDEF, so b = 0x12; b + 1 makes it 0x1334_89ABCDEF.
    # b24: raw 0x9A5678, so a = 0xA5678, b = 0x9.
    "sizes that are not 8/16/32/64 bits": Case("""
struct b48_s : packed_s<> { bit[40] a; bit[8] b; }
struct b24_s : packed_s<> { bit[20] a; bit[4] b; }
component pss_top {""" + HANDLE + """
  target function void run() {
    b48_s x;
    read_struct(h, x);
    x.b = x.b + 1;
    write_struct(make_handle_from_handle(h, 0x20), x);
    b24_s y;
    read_struct(make_handle_from_handle(h, 0x10), y);
    write_struct(make_handle_from_handle(h, 0x30), y);
    write32(make_handle_from_handle(h, 0x40), y.a);
  }
}""", ["read 32 0x1000 0x89abcdef", "read 16 0x1004 0x1234",
       "write 32 0x1020 0x89abcdef", "write 16 0x1024 0x1334",
       "read 16 0x1010 0x5678", "read 8 0x1012 0x9a",
       "write 16 0x1030 0x5678", "write 8 0x1032 0x9a",
       "write 32 0x1040 0xa5678"],
        mem={0x1000: 0x89ABCDEF, 0x1004: 0x1234, 0x1010: 0x5678,
             0x1012: 0x9A},
        xfail=LATER, covers=("LIB-MEM", "LIB-MEM.5", "DT-PACK")),

    # One access each, of the struct's own size. b8: 0x15 | 5 << 5 = 0xB5.
    "8, 16 and 64 bits are one access": Case("""
struct b8_s  : packed_s<> { bit[5] a; bit[3] b; }
struct b16_s : packed_s<> { bit[16] a; }
struct b64_s : packed_s<> { bit[32] lo; bit[32] hi; }
component pss_top {""" + HANDLE + """
  target function void run() {
    b8_s p;  p.a = 0x15; p.b = 5;
    b16_s q; q.a = 0xBEEF;
    b64_s r; r.lo = 0x11223344; r.hi = 0x55667788;
    write_struct(h, p);
    write_struct(make_handle_from_handle(h, 2), q);
    write_struct(make_handle_from_handle(h, 8), r);
  }
}""", ["write 8 0x1000 0xb5", "write 16 0x1002 0xbeef",
       "write 64 0x1008 0x5566778811223344"],
        xfail=LATER, covers=("LIB-MEM", "LIB-MEM.5", "DT-PACK")),

    # Through parameters: read_struct into the caller's struct (aggregates
    # pass by handle, 20.3.2), write_struct from a const one.
    "through a struct parameter and a const one": Case(TYPES + """
component pss_top {""" + HANDLE + """
  function void get(addr_handle_t at, desc_s v) { read_struct(at, v); }
  function void put(addr_handle_t at, const desc_s v) { write_struct(at, v); }
  target function void run() {
    desc_s d;
    get(h, d);
    d.a0 = d.a0 + 1;
    put(make_handle_from_handle(h, 0x10), d);
  }
}""", ["read 32 0x1000 0x7", "read 32 0x1004 0x8", "read 32 0x1008 0x9",
       "read 32 0x100c 0xa",
       "write 32 0x1010 0x7", "write 32 0x1014 0x9", "write 32 0x1018 0x9",
       "write 32 0x101c 0xa"],
        mem={0x1000: 7, 0x1004: 8, 0x1008: 9, 0x100c: 10},
        xfail=LATER, covers=("LIB-MEM", "LIB-MEM.5", "FN-PARAM", "IP-51")),

    # IP-51: a bool and a based enum. bool b [0], e [2:1], v [7:3]:
    # 1 | 2 << 1 | 3 << 3 = 0x1D, one write8 at the handle (0: never bound).
    "write_struct of a packed struct": Case("""
enum e_e : bit[2] { E0, E1, E2 }
struct p_s : packed_s<> { bool b; e_e e; bit[5] v; }
component pss_top {""" + ONE + """
  addr_handle_t hb;
  target function void run() {
    p_s p;
    p.b = true; p.e = E2; p.v = 3;
    write_struct(make_handle_from_handle(hb, 0), p);
  }
}""", ["write 8 0x0 0x1d"],
        xfail=LATER, covers=("LIB-MEM", "DT-PACK", "IP-51", "LIB-MEM.5")),

    # A struct wider than 64 bits is moved member by member, so each member
    # must be one access: a 12-bit field there has none.
    "a wide struct's narrow member is refused": Case("""
struct s_s : packed_s<> { bit[32] a; bit[12] b; bit[52] c; }
component pss_top {""" + HANDLE + """
  target function void run() { s_s s; write_struct(h, s); }
}""", Refused([r"'b' is 12 bits", r"group it"]),
        xfail=LATER, covers=("LIB-MEM", "DT-PACK")),
}

test_construct = define_tests(CASES)
