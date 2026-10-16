"""Core-library functions in a body: message, print, error, memory
primitives (LRM 21).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, ERROR, FATAL, on, ONE, Refused


CASES = {
    # --- Array initializers ------------------------------------------------------
    # A PSS format is not a printf format (LRM 21.1.1): `%n` prints an enum's
    # item and a bool's name, and `%d` reads a value at its PSS width. C and
    # C++ passed the format to printf, whose `%n` STORES through its argument.
    "message: %n of an enum and a bool, %d at the PSS width": Case("""
enum mode_e { OFF, SLOW = 5, FAST }
component pss_top {""" + ONE + """
  target function void run() {
    mode_e m = FAST;
    bool ok = true;
    bit[8] b = 255;
    message(LOW, "m=%n ok=%n m=%d b=%d b=%u b=%x 100%%", m, ok, m, b, b, b);
    a.STS.write_val(1);
  }
}""", ["m=FAST ok=true m=6 b=-1 b=255 b=ff 100%", "write 32 0x1004 0x1"],
        covers=("LIB-MSG", "DT-ENUM", "DT-BOOL")),

    # --- P1: errors (LRM 21.3) -------------------------------------------------
    "error()": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    a.STS.write_val(1);
    error("bad %d", 7);
  }
}""", ["write 32 0x1004 0x1", ERROR],
        covers=("LIB-ERR",)),

    "fatal() stops the run": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    a.STS.write_val(1);
    fatal(3, "bad %d", 7);
    a.STS.write_val(2);
  }
}""", ["write 32 0x1004 0x1", FATAL],
        covers=("LIB-ERR",)),

    # `print` is a solve function (21.1.2), so a constructor may call it. Its
    # format is PSS's: `%d` of 5, `%x` of 255. SV passed the format to $write,
    # whose `%d` pads (W11).
    "print in a constructor": Case("""
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    print("v=%d x=%x\\n", 5, 255);
  }
  target function void run() { a.STS.write_val(1); }
}""", ["v=5 x=ff", "write 32 0x1004 0x1"],
        covers=("LIB-MSG",)),

    # --- P1: memory primitives in a model body (21.13.6) -----------------------
    # The value is converted to the primitive's width (8.7.2): 0x1FF is 0xFF
    # in a write8. C and C++ passed the literal, which -Werror refused (W8).
    "memory primitives truncate their value": Case("""
component pss_top {
  ga_c a;
  addr_handle_t h;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    h = base;
  }
  target function void run() {
    write8(h, 0x1FF);
    write16(make_handle_from_handle(h, 2), 0x12345);
  }
}""", ["write 8 0x1000 0xff", "write 16 0x1002 0x2345"],
        covers=("LIB-MEM", "EX-WIDTH")),

    # A chandle compares with null (7.7).
    "a chandle compared with null": Case("""
component pss_top {""" + ONE + """
  chandle c;
  target function void run() {
    chandle d = c;
    if (d == null) { a.STS.write_val(1); }
  }
}""", ["write 32 0x1004 0x1"],
        covers=("DT-CHND",)),

    # `format` is a solve function too (21.1.2): the text `print` would give.
    # C and C++ have no string (R3). SV passed the format to $sformatf
    # verbatim, and Python refused it (W11).
    "format in a constructor": Case("""
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) {
    string t = format("v=%d x=%x", 5, 255);
    a.set_handle(base);
    print("%s\\n", t);
  }
  target function void run() { a.STS.write_val(1); }
}""", ["v=5 x=ff", "write 32 0x1004 0x1"],
        refused=on(("c", "cpp"), Refused([r"local 't': a string",
                                           r"ruling R3"])),
        covers=("LIB-MSG", "DT-STR")),

    # --- P3: message specifiers (21.1.1, IP-67) ---------------------------------
    # `%d` reads a value as signed at its PSS width, `%u` `%x` `%o` `%b` as
    # unsigned: a bit[32] 0xFFFFFFFF is -1 under %d, an int -1 is ffffffff
    # under %x, an int[8] -1 is 255 under %u.
    "message: every integer specifier at mixed widths": Case("""
component pss_top {""" + ONE + """
  target function void go(bit[32] u, int i, int[8] s, bit[64] w, bit[8] o) {
    message(LOW, "%d %x %u %d %u", u, i, s, w, w);
    message(LOW, "%o %b %x %d%%", o, o, o, i);
  }
  target function void run() { go(0xFFFFFFFF, -1, -1, 0xFFFFFFFFFFFFFFFF, 8); }
}""", ["-1 ffffffff 255 -1 18446744073709551615", "10 1000 8 -1%"],
        covers=("LIB-MSG", "EX-WIDTH.6", "IP-67")),

    # `fatal()` ends the run where it is, however deeply nested: nothing after
    # it runs, the caller's remaining statements included (21.3, IP-99).
    "fatal() in nested loops of a called function": Case("""
component pss_top {""" + ONE + """
  target function void deep() {
    repeat (i : 3) {
      repeat (j : 3) {
        if (i == 1 && j == 1) { fatal(2, "stop %d", i); }
        message(LOW, "%d %d", i, j);
      }
    }
  }
  target function void run() { deep(); message(LOW, "after"); }
}""", ["0 0", "0 1", "0 2", "1 0", FATAL],
        covers=("LIB-ERR", "ST-REPEAT", "FN-CALL", "IP-99")),

    # --- P3: memory primitives in a model body (21.13.6) -------------------------
    # Every width, written and read back, at handles made from the base;
    # `make_handle_from_handle` with a loop index (IP-52), and with `sub`
    # (21.13.4.2). C and C++ masked a computed offset's sum to 32 bits,
    # address included, and ignored `sub` (W33).
    "memory primitives of every width": Case("""
component pss_top {
  ga_c a;
  addr_handle_t h;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    h = base;
  }
  target function void run() {
    write64(h, 0x1122334455667788);
    write32(make_handle_from_handle(h, 8), 0xAABBCCDD);
    write16(make_handle_from_handle(h, 12), 0xEEFF);
    write8(make_handle_from_handle(h, 14), 0x5A);
    bit[64] d = read64(h);
    bit[32] w = read32(make_handle_from_handle(h, 8));
    bit[16] s = read16(make_handle_from_handle(h, 12));
    bit[8] b = read8(make_handle_from_handle(h, 14));
    message(LOW, "%x %x %x %x", d, w, s, b);
    repeat (i : 3) { write32(make_handle_from_handle(h, 0x20 + 4 * i), i + 1); }
    write32(make_handle_from_handle(make_handle_from_handle(h, 0x40), 8, true), 7);
  }
}""", ["write 64 0x1000 0x1122334455667788", "write 32 0x1008 0xaabbccdd",
       "write 16 0x100c 0xeeff", "write 8 0x100e 0x5a",
       "read 64 0x1000 0x1122334455667788", "read 32 0x1008 0xaabbccdd",
       "read 16 0x100c 0xeeff", "read 8 0x100e 0x5a",
       "1122334455667788 aabbccdd eeff 5a",
       "write 32 0x1020 0x1", "write 32 0x1024 0x2", "write 32 0x1028 0x3",
       "write 32 0x1038 0x7"],
        covers=("LIB-MEM", "DT-ADDR", "ST-REPEAT", "IP-52")),

    # --- P3: registers (21.14) -----------------------------------------------------
    # `write_masked` reads the register and writes the masked bits only; `~0`
    # in a literal's member takes the member's width (8.7.2, IP-47): the mask
    # is base_lo's 20 bits, 0xFFFFF000.
    "write_masked with ~0 in a literal's member": Case("""
component pss_top {
  gs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    a.CTL.write_masked({.base_lo = ~0}, {.base_lo = 0x12345, .en = 1});
  }
}""", ["read 32 0x1000 0xabc", "write 32 0x1000 0x12345abc"],
        mem={0x1000: 0xABC},
        covers=("LIB-REG", "EX-LIT.14", "EX-WIDTH", "IP-47")),

    # A register in an array of groups, and an array of registers, each
    # reached with a run-time index (21.14.5, IP-50): offsets from
    # `get_offset_of_instance_array`.
    "register arrays with a run-time index": Case("""
pure component blk_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> R[2];
  function bit[64] get_offset_of_instance_array(string name, int index) {
    if (name == "R") { return 0x8 + 4 * index; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component top_c : reg_group_c {
  blk_c blk[3];
  function bit[64] get_offset_of_instance_array(string name, int index) {
    if (name == "blk") { return 0x100 * (index + 1); }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  top_c regs;
  solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
  target function void run() {
    repeat (i : 3) { regs.blk[i].R[i % 2].write_val(i + 1); }
  }
}""", ["write 32 0x1108 0x1", "write 32 0x120c 0x2", "write 32 0x1308 0x3"],
        covers=("LIB-REG", "DT-REG", "ST-REPEAT", "IP-50")),
}

test_construct = define_tests(CASES)
