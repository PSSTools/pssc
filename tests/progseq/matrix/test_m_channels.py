"""Channels in a body (LRM 21.17, sync_pkg).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, ONE


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
}

test_construct = define_tests(CASES)
