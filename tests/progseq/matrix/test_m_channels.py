"""Channels in a body (LRM 21.17, sync_pkg).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE


CASES = {

    # A depth-1 channel (21.17): try_put fills it, a second try_put fails,
    # try_get empties it into its output argument, a second try_get fails.
    "a channel's try_put and try_get": Case("""
import sync_pkg::*;
component pss_top {""" + ONE + """
  channel_c<bit[32]> ch;
  target function void run() {
    bit[32] v;
    if (ch.try_put(5)) { a.STS.write_val(1); }
    if (ch.try_put(6)) { a.STS.write_val(2); }
    if (ch.try_get(v)) { a.STS.write_val(v); }
    if (!ch.try_get(v)) { a.STS.write_val(3); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x5", "write 32 0x1004 0x3"],
        covers=("LIB-CHAN",)),

    # Each element is its own channel, reached by a run-time index (E10).
    "an array of channels": Case("""
import sync_pkg::*;
component pss_top {""" + ONE + """
  channel_c<bit[32]> ch[2];
  target function void run() {
    bit[32] v;
    int k = 1;
    if (ch[k].try_put(5)) { a.STS.write_val(1); }
    if (ch[0].try_get(v)) { a.STS.write_val(9); }
    if (ch[1].try_get(v)) { a.STS.write_val(v); }
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x5"],
        covers=("LIB-CHAN", "EX-IDX")),

    # `try_get`'s output argument may be any lvalue (21.9.1.3, IP-54): a
    # struct field and an array element, filled in a loop.
    "try_get into a struct field and an array element": Case("""
import sync_pkg::*;
struct box_s { bit[32] v; bit[8] n; }
component pss_top {""" + ONE + """
  channel_c<bit[32]> ch;
  target function void run() {
    box_s b;
    array<bit[32], 3> got;
    int i = 0;
    while (i < 3) {
      if (ch.try_put(10 + i)) { }
      if (ch.try_get(got[i])) { b.n += 1; }
      i += 1;
    }
    if (ch.try_put(7)) { }
    if (ch.try_get(b.v)) { b.n += 1; }
    message(LOW, "%d %d %d %d %d", got[0], got[1], got[2], b.v, b.n);
  }
}""", ["10 11 12 7 4"],
        xfail=on(("c", "py"), "E17: try_get writes only into a plain local "
                              "on C and Python (an unlocated ValueError)"),
        covers=("LIB-CHAN", "EX-ASSIGN.9", "ST-WHILE", "IP-54")),
}

test_construct = define_tests(CASES)
