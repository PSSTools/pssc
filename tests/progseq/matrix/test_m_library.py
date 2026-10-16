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
}

test_construct = define_tests(CASES)
