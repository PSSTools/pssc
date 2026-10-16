"""Core-library functions in a body: message, print, error, memory
primitives (LRM 21).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import ALL, Case, define_tests, ERROR, FATAL, on, ONE, Refused

M1 = ("M1: %p of a chandle is refused, 'this model has no ... chandle "
      "values', though it has one")
W51 = ("W51: sizeof_s<T>::nbytes/nbits is not folded: rendered as "
       "`sizeof_s.nbytes`")
W52 = "W52: nullhandle is not lowered: an undefined name"
E29 = ("E29: sized_addr_handle_s in a struct: unlocated 'unsupported SV "
       "struct member type DataTypeChandle'")
E36 = ("E36: a mem_access_desc_s argument with no executor: c an unlocated "
       "arity error, cpp/sv 'unsupported expr ExprList' on {}")
E37 = "E37: %n of an int: unlocated ValueError on sv"


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
        covers=("LIB-MSG", "DT-ENUM", "DT-BOOL", "LIB-MSG.3", "LIB-MSG.4",
                "LIB-MSG.7", "LIB-MSG.8")),

    # --- P1: errors (LRM 21.3) -------------------------------------------------
    "error()": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    a.STS.write_val(1);
    error("bad %d", 7);
  }
}""", ["write 32 0x1004 0x1", ERROR],
        covers=("LIB-ERR", "LIB-ERR.1")),

    "fatal() stops the run": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    a.STS.write_val(1);
    fatal(3, "bad %d", 7);
    a.STS.write_val(2);
  }
}""", ["write 32 0x1004 0x1", FATAL],
        covers=("LIB-ERR", "LIB-ERR.2")),

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
        covers=("LIB-MSG", "LIB-MSG.1")),

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
        covers=("LIB-MEM", "EX-WIDTH", "FN-PARAM.12", "LIB-MEM.2")),

    # A chandle compares with null (7.7).
    "a chandle compared with null": Case("""
component pss_top {""" + ONE + """
  chandle c;
  target function void run() {
    chandle d = c;
    if (d == null) { a.STS.write_val(1); }
  }
}""", ["write 32 0x1004 0x1"],
        covers=("DT-CHND", "DT-CHND.1", "EX-LIT.10", "ST-VAR.5")),

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
        covers=("LIB-MSG", "DT-STR", "FN-SOLVE.5", "LIB-MSG.2")),

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
        covers=("LIB-MSG", "EX-WIDTH.6", "IP-67", "LIB-MSG.3", "LIB-MSG.4",
                "LIB-MSG.7")),

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
        covers=("LIB-ERR", "ST-REPEAT", "FN-CALL", "IP-99", "LIB-ERR.2")),

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
        covers=("LIB-MEM", "DT-ADDR", "ST-REPEAT", "IP-52", "DT-ADDR.3",
                "LIB-MEM.1", "LIB-MEM.2")),

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
        covers=("LIB-REG", "EX-LIT.14", "EX-WIDTH", "IP-47", "LIB-REG.3")),

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
        covers=("LIB-REG", "DT-REG", "ST-REPEAT", "IP-50", "DT-REG.5",
                "LIB-REG.7", "LIB-REG.10", "LIB-REG.12")),

    # --- P4: interaction patterns ---------------------------------------------------
    # An executor's `write64` written as two `write32`s (Example 354), reached
    # from a function `run` calls (21.13.9.5, IP-53): the 64-bit write
    # becomes two 32-bit ones, low word first; a register access the
    # executor does not override reaches the platform. Only op-model-py
    # delegates to an executor; C and C++ do not lower init blocks yet, and
    # SV refuses `set_executor`.
    "an executor's write64 in terms of write32": Case("""
import executor_pkg::*;
component tap_c : executor_c<> {
  target function void write64(addr_handle_t h, bit[64] data,
                               mem_access_desc_s d = {}) {
    write32(h, data[31:0]);
    write32(make_handle_from_handle(h, 4), data[63:32]);
  }
}
component pss_top {
  ga_c a;
  tap_c tap;
  addr_handle_t hb;
  exec init_down { set_executor(tap); }
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    hb = base;
  }
  target function void put(bit[64] off, bit[64] v) {
    write64(make_handle_from_handle(hb, off), v);
  }
  target function void run() {
    put(8, 0x1122334455667788);
    a.STS.write_val(1);
  }
}""", ["write 32 0x1008 0x55667788", "write 32 0x100c 0x11223344",
       "write 32 0x1004 0x1"],
        refused={"c": Refused([r"`exec init_down` is not lowered"]),
                 "cpp": Refused([r"`exec init_down` is not lowered"]),
                 "sv": Refused([r"set_executor"])},
        covers=("LIB-MEM", "LIB-EXEC", "FN-VIRT", "IP-53", "EXEC-KIND.13",
                "LIB-EXEC.3", "LIB-MEM.7", "LIB-MEM.1")),

    # --- P5.3: chandles and address handles (7.7, 21.13.3-5) -------------------
    # `addr_handle_t` and `file_handle_t` are typedefs of chandle (21.2,
    # 21.13.3), so each assigns to and from a chandle. A default chandle is
    # null; a chandle compares with another chandle, with null and with 0,
    # the one literal it may be compared to (7.7).
    "chandle typedefs compared with null, 0 and each other": Case("""
component pss_top {""" + ONE + """
  chandle k;
  addr_handle_t h;
  target function void run() {
    chandle c = h;
    if (c == null) { message(LOW, "null"); }
    addr_handle_t h2 = k;
    if (h2 == null) { message(LOW, "null2"); }
    std_pkg::file_handle_t f;
    chandle g = f;
    if (g == null) { message(LOW, "fnull"); }
    if (k == 0) { message(LOW, "zero"); }
    chandle d = k;
    chandle e;
    if (d == k) { message(LOW, "eqc"); }
    if (e == 0) { message(LOW, "eq0"); }
    if (d != k) { message(LOW, "bad"); }
  }
}""", ["null", "null2", "fnull", "zero", "eqc", "eq0"],
        covers=("DT-CHND.6", "DT-CHND.1", "EX-EQ.8")),

    # `%p` prints a chandle in hexadecimal with a leading 0x (21.1.1); a
    # default chandle is 0 (7.7). Every target refuses it, saying the model
    # has no chandle values.
    "message: %p of a chandle": Case("""
component pss_top {""" + ONE + """
  chandle c;
  target function void run() { message(LOW, "p=%p", c); }
}""", ["p=0x0"], xfail=on(ALL, M1),
        covers=("DT-CHND.5",)),

    # `nullhandle` is address 0 (21.13.3.2), whatever the mapping.
    "nullhandle": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    addr_handle_t n = nullhandle;
    if (n == nullhandle) { message(LOW, "eq"); }
    message(LOW, "%x %x", addr_value(n),
            addr_value(make_handle_from_handle(nullhandle, 0x10)));
  }
}""", ["eq", "0 10"], xfail=on(ALL, W52),
        covers=("DT-ADDR.1", "DT-ADDR.4")),

    # `addr_value` of a chain of handles (21.13.4.2, 21.13.5): an offset
    # added, one subtracted (`sub`), and one past 32 bits.
    "addr_value of a chain of handles": Case("""
component pss_top {
  ga_c a;
  addr_handle_t h;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    h = base;
  }
  target function void run() {
    addr_handle_t g = make_handle_from_handle(h, 0x10);
    addr_handle_t s = make_handle_from_handle(g, 0x4, true);
    addr_handle_t w = make_handle_from_handle(h, 0x100000000);
    message(LOW, "%x %x %x %x", addr_value(h), addr_value(g), addr_value(s),
            addr_value(w));
  }
}""", ["1000 1010 100c 100001000"], covers=("DT-ADDR.3", "DT-ADDR.4")),

    # An address in a packed struct is a `sized_addr_handle_s` (21.13.3.3).
    "a sized_addr_handle_s in a packed struct": Case("""
struct d_s : packed_s<> { sized_addr_handle_s<32> p; bit[32] n; }
component pss_top {
  ga_c a;
  addr_handle_t h;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    h = base;
  }
  target function void run() {
    d_s d;
    d.p.hndl = make_handle_from_handle(h, 0x20);
    message(LOW, "%x", addr_value(d.p.hndl));
  }
}""", ["1020"], xfail={"sv": E29}, covers=("DT-ADDR.7", "DT-ADDR.4")),

    # `sizeof_s<T>` (21.13.2): bytes rounded up, `int[4]` a 4-bit integer,
    # a packed struct its fields' bits.
    "sizeof_s": Case("""
struct t_s : packed_s<> { bit[2] a; int b; }
component pss_top {""" + ONE + """
  target function void run() {
    int i = sizeof_s<int>::nbytes;
    int b = sizeof_s<bit>::nbytes;
    int w = sizeof_s<bit[33]>::nbytes;
    int r = sizeof_s<array<int, 10>>::nbytes;
    int t = sizeof_s<t_s>::nbytes;
    int n = sizeof_s<int[4]>::nbytes;
    int tb = sizeof_s<t_s>::nbits;
    message(LOW, "%d %d %d %d %d %d %d", i, b, w, r, t, n, tb);
  }
}""", ["4 1 5 40 5 1 34"], xfail=on(ALL, W51), covers=("DT-PACK.6",)),

    # --- P5.3: message formats (21.1.1) -------------------------------------------
    # Flags, width and precision. `%.0d` of 0 prints nothing; `#` prefixes
    # a nonzero value.
    "message: flags, width and precision": Case("""
component pss_top {""" + ONE + """
  target function void run() {
    bit[8] b = 255;
    int i = -5;
    message(LOW, "[%5d][%-5d][%05d][%+d][% d][%#x][%.3d][%.0d][%X][%B][%#o][%8.3x]",
            i, i, i, 5, 5, b, 7, 0, b, b, b, b);
  }
}""", ["[   -5][-5   ][-0005][+5][ 5][0xff][007][][FF][11111111][0377][     0ff]"],
        refused={"sv": Refused([r"'%5d' has no SystemVerilog rendering yet"])},
        covers=("LIB-MSG.5",)),

    # A format and its arguments must agree (21.1.1 a-c).
    "message: fewer arguments than specifiers": Case("""
component pss_top {""" + ONE + """
  target function void run() { int a = 1; message(LOW, "%d %d", a); }
}""", Refused([r"2 format specifier\(s\) for 1 argument"]),
        covers=("LIB-MSG.6",)),

    "message: a % that starts no specifier": Case("""
component pss_top {""" + ONE + """
  target function void run() { int a = 1; message(LOW, "50%, off", a); }
}""", Refused([r"does not start a valid format specifier"]),
        covers=("LIB-MSG.6",)),

    "message: %n of an int": Case("""
component pss_top {""" + ONE + """
  target function void run() { int a = 1; message(LOW, "%n", a); }
}""", Refused([r"%n cannot format a int"]), xfail={"sv": E37},
        covers=("LIB-MSG.6",)),

    # --- P5.3: memory access descriptors (21.13.9) --------------------------------
    # With no executor, a `mem_access_desc_s` argument reaches the platform's
    # primitive, which ignores it; `{}` is its default.
    "a mem_access_desc_s argument with no executor": Case("""
component pss_top {
  ga_c a;
  addr_handle_t h;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    h = base;
  }
  target function void run() {
    mem_access_desc_s d;
    write32(h, 5, d);
    write32(make_handle_from_handle(h, 4), 6, {});
    bit[32] r = read32(h, {});
    message(LOW, "%x", r);
  }
}""", ["write 32 0x1000 0x5", "write 32 0x1004 0x6", "read 32 0x1000 0x5", "5"],
        xfail=on(("c", "cpp", "sv"), E36), covers=("LIB-MEM.6",)),
}

test_construct = define_tests(CASES)
