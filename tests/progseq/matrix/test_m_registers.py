"""Register groups, handles, offsets and register values (LRM 21.14, 24).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

import json

import pytest

from .. import trace_harness as th
from ._matrix import ALL, Case, define_tests, on, Refused, REGS


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
        args=(0x1000, 0x2000), covers=("DT-REG", "DT-ADDR", "LIB-REG")),

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
        args=(0x1000, 0x2000, 0x3000), covers=("DT-REG", "LIB-REG")),

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
        mem={0x1000: 0xFFFFF001}, covers=("EX-SEL", "DT-REG", "LIB-REG")),

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
        covers=("DT-REG", "ST-IF", "DT-STR")),

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
        mem={0x1000: 0xFFFFFFF9}, covers=("DT-REG", "DT-ENUM", "DT-PACK")),

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
}""", Refused([r"fld_s", r"mode", r"u_e"]), covers=("DT-REG", "DT-ENUM")),

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
        mem={0x1000: 0x2}, covers=("DT-REG", "DT-ENUM", "LIB-MSG")),

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
  st_s last;
  solve function void initialize(addr_handle_t base) { q.set_handle(base); }
  target function st_s status() { return q.STS.read(); }
  target function void ack(st_s v) { q.CTL.write_val(v.done); }
  target function void run() { last = status(); ack(last); }
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
        covers=("DT-REG", "LIB-REG")),

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
        covers=("DT-REG", "DT-PACK", "DT-STRUCT")),
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
