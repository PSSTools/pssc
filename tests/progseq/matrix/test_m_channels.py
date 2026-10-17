"""Channels in a body (LRM 21.17, sync_pkg).

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests, on, ONE, Refused

W64 = ("W64: Verilator reads `$signed(q.x)` of a non-constant packed struct "
       "from the wrong bits (q.x prints 67, 0x43, the whole value)")

E19 = ("E19: only depth-1 channels are implemented, and a deeper one is "
       "refused with an internal error (ValueError), not a located refusal")

E40 = "E40: a channel of a struct is refused with an unlocated ValueError"


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
        covers=("LIB-CHAN", "LIB-CHAN.1", "LIB-CHAN.4", "LIB-CHAN.5")),

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
        covers=("LIB-CHAN", "EX-IDX", "EX-IDX.6")),

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
        covers=("LIB-CHAN", "EX-ASSIGN.9", "ST-WHILE", "IP-54", "LIB-CHAN.4")),

    # --- P4: interaction patterns ---------------------------------------------------
    # A channel of depth 3 (21.9.1, IP-55): three `try_put`s fill it and the
    # fourth fails; `try_get`s return the values in FIFO order, then fail.
    # `try_put`'s result is a bool, which `%d` prints as 1 or 0 (it had no
    # type; SV printed -1). C, C++ and Python implement depth-1 channels
    # only, and refuse a deeper one with an internal error (E19).
    "a channel of depth 3": Case("""
import sync_pkg::*;
component pss_top {""" + ONE + """
  channel_c<bit[32], 3> ch;
  target function void run() {
    repeat (i : 4) { message(LOW, "put %d %d", i, ch.try_put(i + 10)); }
    bit[32] v;
    repeat (i : 4) {
      bool ok = ch.try_get(v);
      message(LOW, "get %d %d", ok, ok ? v : 0);
    }
  }
}""", ["put 0 1", "put 1 1", "put 2 1", "put 3 0",
       "get 1 10", "get 1 11", "get 1 12", "get 0 0"],
        xfail=on(("c", "cpp", "py"), E19),
        covers=("LIB-CHAN", "ST-REPEAT", "IP-55", "LIB-CHAN.1", "LIB-CHAN.5")),

    # --- P5.3: blocking calls, solve context, element types -------------------
    # `put` waits for space and `get` for an item (21.9.1.1-2); with the
    # channel empty neither waits here. A blocking call suspends, which only
    # SV and the async Python form can do: C, C++ and synchronous Python
    # refuse it.
    "a channel's put and get": Case("""
import sync_pkg::*;
component pss_top {""" + ONE + """
  channel_c<bit[32]> ch;
  target function void run() {
    ch.put(5);
    bit[32] v = ch.get();
    a.STS.write_val(v);
  }
}""", ["write 32 0x1004 0x5"],
        refused=on(("c", "cpp", "py"),
                   Refused([r"cannot lower call: 'put' is a PSS core-library "
                            r"function", r"get\(\)/put\(\) suspend"])),
        covers=("LIB-CHAN.2", "LIB-CHAN.3")),

    # Channel calls are target-only (21.9.1): not in a constructor, and not
    # in init_down (which C and C++ do not lower yet, P6).
    "try_put in a constructor": Case("""
import sync_pkg::*;
component pss_top {
  ga_c a;
  channel_c<bit[32]> ch;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    (void)ch.try_put(1);
  }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"cannot lower call: 'try_put' is legal only in a target "
               r"context"]),
        covers=("LIB-CHAN.6",)),

    "try_put in init_down": Case("""
import sync_pkg::*;
component pss_top {""" + ONE + """
  channel_c<bit[32]> ch;
  exec init_down { (void)ch.try_put(1); }
  target function void run() { a.STS.write_val(1); }
}""", Refused([r"cannot lower call: 'try_put' is legal only in a target "
               r"context"]),
        refused={t: Refused([r"`exec init_down` is not lowered by "
                             r"'op-model-c(pp)?' yet"])
                 for t in ("c", "cpp")},
        covers=("LIB-CHAN.6",)),

    # A channel holds a copy (21.9.1): the struct written after `try_put` is
    # not the one queued. Element types a packed struct, an enum with a base
    # type and a bool.
    "channels of a struct, an enum and a bool": Case("""
import sync_pkg::*;
enum e_e : bit[2] { E0, E1, E2 }
struct p_s : packed_s<> { bit[4] x; bit[4] y; }
component pss_top {""" + ONE + """
  channel_c<p_s> cs;
  channel_c<e_e> ce;
  channel_c<bool> cb;
  target function void run() {
    p_s p;
    p.x = 3;
    p.y = 4;
    (void)cs.try_put(p);
    p.x = 9;
    p_s q;
    (void)cs.try_get(q);
    (void)ce.try_put(E2);
    e_e e;
    (void)ce.try_get(e);
    (void)cb.try_put(true);
    bool b;
    (void)cb.try_get(b);
    message(LOW, "%d %d %n %n", q.x, q.y, e, b);
  }
}""", ["3 4 E2 true"], xfail={"c": E40,
               "sv": W64},
        covers=("LIB-CHAN.1", "LIB-CHAN.4", "LIB-CHAN.5")),
}

test_construct = define_tests(CASES)
