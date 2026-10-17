"""Register groups, handles, offsets and register values (LRM 21.14, 24).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

import json

import pytest

from .. import trace_harness as th
from ._matrix import ALL, Case, define_tests, on, ONE, Refused, REGS

W59 = "W59: a read of a WRITEONLY / write of a READONLY register is accepted"
E28 = "E28: a nested packed struct or array field in a register value"
M2 = "M2: get_handle() is refused as not declared by PSS (21.14.4)"


CASES = {
    # --- D1: each register group at its own handle ---------------------------
    "two groups at two handles": Case("""
component pss_top {
  ga_c a;
  gb_c b;
  solve function void initialize(addr_handle_t a_base, addr_handle_t b_base) {
    a.set_handle(a_base);
    b.set_handle(b_base);
  }
  target function void run() { a.STS.write_val(1); b.CTL.write_val(2); }
}""", ["write 32 0x1004 0x1", "write 32 0x2010 0x2"],
        args=(0x1000, 0x2000), covers=("DT-REG", "DT-ADDR", "LIB-REG",
                                       "DT-REG.1", "LIB-REG.8", "LIB-REG.10")),

    "a group at a handle made from another": Case("""
component pss_top {
  ga_c a;
  gb_c b;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    b.set_handle(make_handle_from_handle(base, 0x1000));
  }
  target function void run() { a.STS.write_val(1); b.CTL.write_val(2); }
}""", ["write 32 0x1004 0x1", "write 32 0x2010 0x2"],
        covers=("DT-REG", "DT-ADDR", "LIB-MEM")),

    "three groups bound in reverse order": Case("""
component pss_top {
  ga_c a;
  ga_c b;
  gb_c c;
  solve function void initialize(addr_handle_t ha, addr_handle_t hb,
                                 addr_handle_t hc) {
    c.set_handle(hc);
    b.set_handle(hb);
    a.set_handle(ha);
  }
  target function void run() {
    a.CTL.write_val(1); b.CTL.write_val(2); c.CTL.write_val(3);
  }
}""", ["write 32 0x1000 0x1", "write 32 0x2000 0x2", "write 32 0x3010 0x3"],
        args=(0x1000, 0x2000, 0x3000), covers=("DT-REG", "LIB-REG",
                                               "LIB-REG.8")),

    "a group no set_handle reaches": Case("""
component pss_top {
  ga_c a;
  gb_c b;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(1); b.CTL.write_val(2); }
}""", Refused([r"\bb\b", r"never bound"]), covers=("DT-REG",)),

    # --- D3: bit and part selects (LRM 8.5.x; Table 21: unsigned, hi-lo+1) ----
    "a part select of a register field assigned": Case("""
component pss_top {
  gs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    ctl_s c = a.CTL.read();
    c.base_lo[3:0] = 0xA;
    a.CTL.write(c);
  }
}""", ["read 32 0x1000 0xfffff001", "write 32 0x1000 0xffffa001"],
        mem={0x1000: 0xFFFFF001}, covers=("EX-SEL", "DT-REG", "LIB-REG",
                                          "DT-PACK.3", "LIB-REG.1")),

    # --- D5: offset functions are evaluated, whatever their form ---------------
    "offsets from an if chain": Case("""
pure component gi_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  reg_c<bit[32], READWRITE, 32> STS;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "CTL") {
      return 0x0;
    } else if (name == "STS") {
      return 0x4;
    }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gi_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.CTL.write_val(1); a.STS.write_val(2); }
}""", ["write 32 0x1000 0x1", "write 32 0x1004 0x2"],
        covers=("DT-REG", "ST-IF", "DT-STR", "LIB-REG.7")),

    "offsets from an if chain, out of order and sparse": Case("""
pure component gi_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  reg_c<bit[32], READWRITE, 32> STS;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "CTL") { return 0x28; }
    if (name == "STS") { return 0x8; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gi_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.CTL.write_val(1); a.STS.write_val(2); }
}""", ["write 32 0x1028 0x1", "write 32 0x1008 0x2"],
        covers=("DT-REG", "ST-IF")),

    "an array-offset function that answers no for everything": Case("""
pure component gi_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "CTL") { return 0x20; }
    return 0xFFFFFFFFFFFFFFFF;
  }
  function bit[64] get_offset_of_instance_array(string name, int index) {
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gi_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.CTL.write_val(1); }
}""", ["write 32 0x1020 0x1"], covers=("DT-REG",)),

    "an if chain with no case for a register": Case("""
pure component gi_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  reg_c<bit[32], READWRITE, 32> STS;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "CTL") { return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gi_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(2); }
}""", Refused([r"gi_c", r"STS"]), covers=("DT-REG",)),

    "an offset function the build cannot evaluate": Case("""
import target function bit[64] plat_offset();
pure component gi_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "CTL") { return plat_offset(); }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gi_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.CTL.write_val(1); }
}""", Refused([r"gi_c", r"get_offset_of_instance"]),
        covers=("DT-REG", "FN-IMPORT")),

    # --- D6: enum-typed fields in a packed register struct ---------------------
    "an enum field in a packed register struct": Case("""
enum e2_e : bit[2] { Z0, Z1, Z2, Z3 }
struct fld_s : packed_s<> { bit[1] lo; e2_e mode; bit[29] hi; }
pure component ge_c : reg_group_c {
  reg_c<fld_s, READWRITE, 32> F;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["F"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  ge_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    fld_s v = a.F.read();
    v.mode = Z2;
    a.F.write(v);
  }
}""", ["read 32 0x1000 0xfffffff9", "write 32 0x1000 0xfffffffd"],
        mem={0x1000: 0xFFFFFFF9}, covers=("DT-REG", "DT-ENUM", "DT-PACK",
                                          "DT-ENUM.3", "DT-ENUM.13",
                                          "DT-PACK.1", "DT-PACK.2",
                                          "DT-PACK.3", "DT-REG.1")),

    # Refused by the front end (pssparser), with its location: pinned here so
    # no target starts defaulting a width.
    "an unsized enum in a packed register struct": Case("""
enum u_e { UA, UB }
struct fld_s : packed_s<> { bit[1] lo; u_e mode; bit[30] hi; }
pure component ge_c : reg_group_c {
  reg_c<fld_s, READWRITE, 32> F;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["F"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  ge_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { fld_s v = a.F.read(); a.F.write(v); }
}""", Refused([r"fld_s", r"mode", r"u_e"]), covers=("DT-REG", "DT-ENUM",
                                                    "DT-ENUM.13",
                                                    "DT-PACK.2")),

    # --- Array initializers ------------------------------------------------------
    # An enum field of a register value is held as an integer where the
    # language's enums are scoped (a C++ bitfield): read into an enum, compared
    # with an item, and written back from an enum local.
    "an enum field of a register value read back": Case("""
enum e2_e : bit[2] { Z0, Z1, Z2, Z3 }
struct fld_s : packed_s<> { bit[1] lo; e2_e mode; bit[29] hi; }
pure component ge_c : reg_group_c {
  reg_c<fld_s, READWRITE, 32> F;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["F"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  ge_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    fld_s v = a.F.read();
    e2_e m = v.mode;
    if (v.mode == Z1 && m == Z1) { m = Z3; }
    v.mode = m;
    a.F.write(v);
    message(LOW, "%n %d", v.mode, v.mode);
  }
}""", ["read 32 0x1000 0x2", "write 32 0x1000 0x6", "Z3 3"],
        mem={0x1000: 0x2}, covers=("DT-REG", "DT-ENUM", "LIB-MSG",
                                   "DT-ENUM.3", "DT-ENUM.13")),

    # --- A register value on the API -------------------------------------------
    # A layout is implementation until the API names it: here an operation
    # returns one, another takes one, and the root holds one. C kept every
    # layout in the .c, so the header named `st_s` and did not compile.
    "register values an operation takes and returns": Case("""
enum st_e : bit[1] { IDLE, BUSY }
struct st_s : packed_s<> { bit[1] done; st_e mode; bit[30] rsvd; }
pure component gq_c : reg_group_c {
  reg_c<st_s, READWRITE, 32> STS;
  reg_c<bit[32], READWRITE, 32> CTL;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["STS"]: return 0x0; ["CTL"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gq_c q;
  solve function void initialize(addr_handle_t base) { q.set_handle(base); }
  target function st_s status() { return q.STS.read(); }
  target function void ack(st_s v) { q.CTL.write_val(v.done); }
  target function void run() { st_s last = status(); ack(last); }
}""", ["read 32 0x1000 0x1", "write 32 0x1004 0x1"], mem={0x1000: 0x1},
        covers=("DT-REG", "FN-PARAM", "ST-RET")),

    # --- Integers wider than 64 bits -------------------------------------------
    # The register's size selects its primitive (LRM 21.14.5 a); the widest
    # is 64 bits, so a 128-bit register has no access on any target. Every
    # target used to read and write half of it with read64/write64.
    "a register wider than 64 bits": Case("""
pure component gw_c : reg_group_c {
  reg_c<bit[128], READWRITE, 128> W;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["W"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gw_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() { g.W.write_val(1); }
}""", Refused([r"'g\.W'", r"128 bits", r"wider than 64"]), covers=("DT-REG",)),

    # --- P1 --------------------------------------------------------------------
    # A register group's offset function is a function: an operation may call
    # it (E9).
    "an operation calls its group's offset function": Case("""
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    a.STS.write_val(a.get_offset_of_instance("STS"));
  }
}""", ["write 32 0x1004 0x4"],
        covers=("DT-REG", "FN-CALL")),

    # `write_field` (21.14.2) is a read-modify-write of one field.
    "write_field": Case("""
component pss_top {
  gs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.CTL.write_field("en", 1); }
}""", ["read 32 0x1000 0xfffff000", "write 32 0x1000 0xfffff001"],
        mem={0x1000: 0xFFFFF000},
        covers=("DT-REG", "LIB-REG", "LIB-REG.5")),

    # A register value derived from a packed struct is laid out base first
    # (17.1, 21.13.1): lo is bits 3:0, hi bits 31:4.
    "a register value derived from a packed struct": Case("""
struct lo_s : packed_s<> { bit[4] lo; }
struct ext_s : lo_s { bit[28] hi; }
pure component gx_c : reg_group_c {
  reg_c<ext_s, READWRITE, 32> R;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["R"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gx_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() {
    ext_s v = g.R.read();
    v.lo = 0xA;
    g.R.write(v);
    message(LOW, "%x", v.hi);
  }
}""", ["read 32 0x1000 0x30", "write 32 0x1000 0x3a", "3"],
        mem={0x1000: 0x30},
        covers=("DT-REG", "DT-PACK", "DT-STRUCT", "DT-PACK.1", "DT-PACK.5")),

    # --- P4 ---------------------------------------------------------------------------
    # Part and bit selects of a register value's fields in a loop, then one
    # write (IP-46): base_lo's low nibble 0xF counts 0, 1, 2; its top nibble
    # ends at 2; rsvd gains bits 0-2 (0xE once shifted past `en`); `en`
    # ends at 0.
    "selects of a register value's fields in a loop, then a write": Case("""
component pss_top {
  gs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    ctl_s v = a.CTL.read();
    repeat (i : 3) {
      v.base_lo[3:0] += 1;
      v.base_lo[19:16] = i;
      v.rsvd[i] = 1;
      v.en = i % 2;
    }
    a.CTL.write(v);
  }
}""", ["read 32 0x1000 0xfffff001", "write 32 0x1000 0x2fff200e"],
        mem={0x1000: 0xFFFFF001},
        covers=("EX-SEL", "DT-REG", "LIB-REG", "ST-REPEAT", "IP-46")),
    # --- P5.3: register values, access kinds, handles ------------------------
    # A bool field of a packed struct is one bit (21.13.1.1): en is bit 0, x
    # bits 7:1, hi bit 8, r bits 31:9. 0x101 is en and hi set, x 0; x = 3
    # sets bits 2:1. (`%x`, not `%d`: Verilator 5.052 evaluates
    # `$signed(v.x)`, x being 0, as 128.)
    "a bool field of a register value": Case("""
struct bs_s : packed_s<> { bool en; bit[7] x; bool hi; bit[23] r; }
pure component gbs_c : reg_group_c {
  reg_c<bs_s, READWRITE, 32> F;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["F"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gbs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    bs_s v = a.F.read();
    message(LOW, "%n %x %n", v.en, v.x, v.hi);
    v.x = 3;
    a.F.write(v);
  }
}""", ["read 32 0x1000 0x101", "true 0 true", "write 32 0x1000 0x107"],
        mem={0x1000: 0x101}, covers=("DT-BOOL.5", "DT-PACK.2", "DT-REG",
                                     "LIB-REG.1")),

    # Big-endian packing (21.13.1.1, Ex 345) puts the first field at the
    # register's MSB: a is bits 31:28, b bits 27:0.
    "a big-endian register value": Case("""
struct be_s : packed_s<BIG_ENDIAN> { bit[4] a; bit[28] b; }
pure component gbe_c : reg_group_c {
  reg_c<be_s, READWRITE, 32> F;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["F"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gbe_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    be_s v = a.F.read();
    message(LOW, "%x %x", v.a, v.b);
    v.a = 5;
    a.F.write(v);
  }
}""", ["read 32 0x1000 0x12345678", "1 2345678", "write 32 0x1000 0x52345678"],
        mem={0x1000: 0x12345678},
        covers=("DT-PACK.4", "DT-REG")),

    # The LRM's own 30-bit struct, both ways (21.13.1.2-3, Ex 344, Ex 345):
    # little-endian packs A at bit 0 and pads at the MSB end; big-endian
    # packs A at bit 31 and pads at the LSB end (bits 1:0).
    "the LRM's packing examples, little- and big-endian": Case("""
struct le_s : packed_s<LITTLE_ENDIAN> {
  bit[6] A; bit[2] B; bit[9] C; bit[7] D; bit[6] E;
}
struct be_s : packed_s<BIG_ENDIAN> {
  bit[6] A; bit[2] B; bit[9] C; bit[7] D; bit[6] E;
}
pure component gle_c : reg_group_c {
  reg_c<le_s, READWRITE, 32> L;
  reg_c<be_s, READWRITE, 32> B;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["L"]: return 0x0; ["B"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gle_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    le_s l; be_s b;
    l.A = 1; l.B = 2; l.C = 3; l.D = 4; l.E = 5;
    b.A = 1; b.B = 2; b.C = 3; b.D = 4; b.E = 5;
    a.L.write(l);
    a.B.write(b);
  }
}""", ["write 32 0x1000 0x5080381", "write 32 0x1004 0x6018414"],
        covers=("DT-PACK.4", "DT-PACK.3", "DT-REG")),

    # A nested packed struct and an array field (21.13.1, Ex 360) pack in
    # place, densely: z bit 0, i.x bits 2:1, i.y bit 3, f[k] bits 5+2k:4+2k,
    # r bits 31:14. 0x1793d is z 1, i.x 2, i.y 1, f {3, 0, 1, 2, 3}, r 5;
    # f[1] = 2 and i.x = 1 make the low byte 0xbb.
    "a nested struct and an array field in a register value": Case("""
struct in_s : packed_s<> { bit[2] x; bool y; }
struct v_s : packed_s<> { bit[1] z; in_s i; bit[2] f[5]; bit[18] r; }
pure component gv_c : reg_group_c {
  reg_c<v_s, READWRITE, 32> V;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["V"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gv_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    v_s v = a.V.read();
    message(LOW, "%d %d %n %d %d %d", v.z, v.i.x, v.i.y, v.f[0], v.f[2], v.r);
    v.f[1] = 2;
    v.i.x = 1;
    a.V.write(v);
  }
}""", ["read 32 0x1000 0x1793d", "1 2 true 3 1 5", "write 32 0x1000 0x179bb"],
        mem={0x1000: 0x1793D}, xfail=on(ALL, E28),
        covers=("DT-PACK.2", "DT-REG.4", "DT-REG", "DT-BOOL.5")),

    # A register type derived from a reg_c specialization (Ex 360), and SZ's
    # default, 8*sizeof_s<R>::nbytes (21.14.1): 16 for both registers, so
    # both take the 16-bit primitives (21.14.5 a).
    "a derived register type and the default size": Case("""
struct r0_s : packed_s<> { bit[8] lo; bit[8] hi; }
pure component my_reg0_c : reg_c<r0_s> {}
pure component gd_c : reg_group_c {
  my_reg0_c R1;
  reg_c<bit[16]> R2;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["R1"]: return 0x4; ["R2"]: return 0x8; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gd_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    r0_s v = a.R1.read();
    v.hi = 0x12;
    a.R1.write(v);
    a.R2.write_val(v.lo);
  }
}""", ["read 16 0x1004 0xabcd", "write 16 0x1004 0x12cd",
       "write 16 0x1008 0xcd"],
        mem={0x1004: 0xABCD},
        covers=("DT-REG.1", "DT-REG.3", "DT-REG", "LIB-REG.10")),

    # SZ defaults to whole bytes (8*sizeof_s<R>::nbytes, 21.14.1): a bit[12]
    # value is a 16-bit register. A named SZ is a constant expression.
    "register sizes: a default of whole bytes and a named SZ": Case("""
pure component gz_c : reg_group_c {
  static const int W = 32;
  reg_c<bit[12]> R12;
  reg_c<bit[8], READWRITE, W> R8;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["R12"]: return 0x0; ["R8"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gz_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    a.R12.write_val(0xABC);
    a.R8.write_val(0x5A);
  }
}""", ["write 16 0x1000 0xabc", "write 32 0x1004 0x5a"],
        covers=("DT-REG.3", "LIB-REG.10")),

    # A register is instantiable only in a register group (21.14.1).
    "a register in a plain component": Case("""
component pss_top {
  ga_c a;
  reg_c<bit[32], READWRITE, 32> X;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { X.write_val(1); }
}""", Refused([r"m\.pss:\d+:\d+: error: register 'X' is in 'pss_top', which is not a register group \(LRM 21\.14\.1\)"]),
        covers=("DT-REG.2",)),

    # A READONLY and a WRITEONLY register may share an offset (Ex 361); each
    # is accessed in its own direction.
    "a read-only and a write-only register at one offset": Case("""
pure component grw_c : reg_group_c {
  reg_c<bit[16], READONLY> RO;
  reg_c<bit[16], WRITEONLY> WO;
  reg_c<bit[16], READWRITE> RW;
  function bit[64] get_offset_of_instance(string name) {
    match (name) {
      ["RO"]: return 0x4; ["WO"]: return 0x4; ["RW"]: return 0x6;
    }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  grw_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    bit[16] x = a.RO.read_val();
    a.WO.write_val(x + 1);
    a.RW.write_val(a.RW.read_val() + x);
  }
}""", ["read 16 0x1004 0x7", "write 16 0x1004 0x8",
       "read 16 0x1006 0x10", "write 16 0x1006 0x17"],
        mem={0x1004: 0x7, 0x1006: 0x10},
        covers=("DT-REG.5", "DT-REG.6", "DT-REG")),

    # Access-kind errors (21.14.1): a read or read-modify-write of a
    # WRITEONLY register, a write of a READONLY one.
    "a read of a write-only register": Case("""
pure component gk_c : reg_group_c {
  reg_c<bit[32], WRITEONLY, 32> W;
  reg_c<bit[32], READONLY, 32> R;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["W"]: return 0x0; ["R"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gk_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() { bit[32] v = g.W.read_val(); }
}""", Refused([r"read_val", r"WRITEONLY"]), xfail=on(ALL, W59),
        covers=("LIB-REG.6", "DT-REG.6")),

    "a write of a read-only register": Case("""
pure component gk_c : reg_group_c {
  reg_c<bit[32], WRITEONLY, 32> W;
  reg_c<bit[32], READONLY, 32> R;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["W"]: return 0x0; ["R"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gk_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() { g.R.write_val(1); }
}""", Refused([r"write_val", r"READONLY"]), xfail=on(ALL, W59),
        covers=("LIB-REG.6", "DT-REG.6")),

    "a masked write of a write-only register": Case("""
pure component gk_c : reg_group_c {
  reg_c<bit[32], WRITEONLY, 32> W;
  reg_c<bit[32], READONLY, 32> R;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["W"]: return 0x0; ["R"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gk_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() { g.W.write_val_masked(0xF, 1); }
}""", Refused([r"write_val_masked", r"WRITEONLY"]),
        covers=("LIB-REG.6", "DT-REG.6")),

    # A storage type is a packed struct and may be a register's value
    # (21.5.1): float32_s is mantissa bits 22:0, exponent 30:23, sign 31.
    # 0x83 << 23 | 0x220000 is 0x41a20000 (10.125).
    "a floating-point storage type as a register value": Case("""
import std_pkg::*;
pure component gf_c : reg_group_c {
  reg_c<float32_s, READWRITE, 32> F;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["F"]: return 0x8; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  gf_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() {
    float32_s v;
    v.mantissa = 0x220000;
    v.exponent = 0x83;
    v.sign = 0;
    g.F.write(v);
    float32_s r = g.F.read();
    message(LOW, "%x %x %d", r.mantissa, r.exponent, r.sign);
  }
}""", ["write 32 0x1008 0x41a20000", "read 32 0x1008 0x41a20000",
       "220000 83 0"],
        covers=("LIB-FP.4", "DT-REG")),

    # write_val_masked (21.14.1): new = (cur & ~mask) | (val & mask), so
    # 0xaabbccdd keeps all but byte 1, which becomes 0x12. A bit[32]
    # register's read()/write() are read_val()/write_val().
    "write_val_masked and read/write of a bit-vector register": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    a.CTL.write_val_masked(0xFF00, 0x1234);
    bit[32] v = a.STS.read();
    a.STS.write(v + 1);
  }
}""", ["read 32 0x1000 0xaabbccdd", "write 32 0x1000 0xaabb12dd",
       "read 32 0x1004 0x41", "write 32 0x1004 0x42"],
        mem={0x1000: 0xAABBCCDD, 0x1004: 0x41},
        covers=("LIB-REG.4", "LIB-REG.1", "LIB-REG")),

    # write_fields: one read-modify-write of the named fields; rsvd (11:1)
    # keeps its value.
    "write_fields": Case("""
component pss_top {
  gs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    a.CTL.write_fields({"en", "base_lo"}, {1, 0x5});
  }
}""", ["read 32 0x1000 0xfffff000", "write 32 0x1000 0x5001"],
        mem={0x1000: 0xFFFFF000}, covers=("LIB-REG.5", "LIB-REG")),

    # get_handle() (21.14.4, Ex 363): a group's handle is its base, a
    # register's is the base plus its offset.
    "get_handle of a group and of a register": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    addr_handle_t h = a.get_handle();
    write32(make_handle_from_handle(h, 0x8), 3);
    write32(a.STS.get_handle(), 4);
  }
}""", ["write 32 0x1008 0x3", "write 32 0x1004 0x4"],
        xfail=on(ALL, M2), covers=("LIB-REG.9", "LIB-MEM")),
}

test_construct = define_tests(CASES)


@pytest.mark.parametrize("target", ALL)
def test_an_enum_field_has_its_base_types_width_in_the_manifest(tmp_path,
                                                                target):
    """`enum e2_e : bit[2]` makes the field two bits wide (D6). The layout is
    the manifest's statement, so a consumer packing the value by hand gets
    the same bits the generated accessors do."""
    man = tmp_path / "manifest.json"
    th.compile_model(tmp_path, REGS + CASES[
        "an enum field in a packed register struct"].pss, target,
        progseq_manifest=str(man))
    doc = json.loads(man.read_text())
    (fld,) = [s for s in doc["value_structs"] if s["name"] == "fld_s"]
    assert fld["bits"] == 32
    assert [(f["name"], f["lsb"], f["width"]) for f in fld["fields"]] == [
        ("lo", 0, 1), ("mode", 1, 2), ("hi", 3, 29)]
