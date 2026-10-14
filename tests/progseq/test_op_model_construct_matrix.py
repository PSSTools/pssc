"""PSS constructs in operation bodies, held to one trace on every op-model target.

Each case is a small model and the memory-access trace its ``run()`` must
produce, worked out by hand from the LRM -- never taken from a backend, since
an address a backend computed proves only that the backends agree. Every case
runs on op-model-c, -cpp, -py and -sv (`trace_harness`).

A case a target does not handle yet is a STRICT xfail naming its defect, so a
fix that makes it pass has to remove the marker too. The defects and their
fixes are in docs/design/op-model-field-defects-plan.md (D1-D9).

Integer expressions are evaluated at their PSS bit width (LRM 8.7), not at the
host language's: a call argument and an assignment are assignment-like
contexts (8.7.2) whose target width propagates into the expression, and
otherwise operands are propagated per Table 22. The D9 cases pin both
directions.
"""
from __future__ import annotations

import json
import re
from typing import Dict, NamedTuple, Optional, Sequence

import pytest

from pssc.driver import CompileError

from . import trace_harness as th
from .conftest import diagnostic_text

#: Register groups the cases share. `ga_c` puts CTL at 0x0 and STS at 0x4;
#: `gb_c` puts CTL at 0x10, so a group bound at the wrong base shows up as a
#: wrong address rather than a coincidentally right one.
_REGS = """
struct ctl_s : packed_s<> { bit[1] en; bit[11] rsvd; bit[20] base_lo; }
pure component ga_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  reg_c<bit[32], READWRITE, 32> STS;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["CTL"]: return 0x0; ["STS"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component gb_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["CTL"]: return 0x10; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component gs_c : reg_group_c {
  reg_c<ctl_s, READWRITE, 32> CTL;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["CTL"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component g64_c : reg_group_c {
  reg_c<bit[64], READWRITE, 64> W;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["W"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
"""

#: The root binds one `ga_c` at its one handle; most cases need no more.
_ONE = """
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
"""


class Refused(NamedTuple):
    """The model is refused as a user error whose text matches every pattern."""
    patterns: Sequence[str]


class Case(NamedTuple):
    pss: str
    want: object                         # trace lines, or Refused
    args: Sequence[int] = (0x1000,)
    mem: Optional[Dict[int, int]] = None
    xfail: Dict[str, str] = {}           # target -> defect it is waiting on
    refused: Dict[str, Refused] = {}     # target -> how it refuses the model



_ALL = ("c", "cpp", "py", "sv")


def _on(targets, why):
    return {t: why for t in targets}


#: Python builds a sub-component whose type has a constructor AT its
#: `initialize` call (`_sub_storage`), and it is `None` until then.
_PY_LAZY_SUB = "py: a sub-component with a constructor is None until called"



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
        args=(0x1000, 0x2000)),

    "a group at a handle made from another": Case("""
component pss_top {
  ga_c a;
  gb_c b;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    b.set_handle(make_handle_from_handle(base, 0x1000));
  }
  target function void run() { a.STS.write_val(1); b.CTL.write_val(2); }
}""", ["write 32 0x1004 0x1", "write 32 0x2010 0x2"]),

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
        args=(0x1000, 0x2000, 0x3000)),

    "a group no set_handle reaches": Case("""
component pss_top {
  ga_c a;
  gb_c b;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(1); b.CTL.write_val(2); }
}""", Refused([r"\bb\b", r"never bound"])),

    # --- Names: a generated API names a PSS type as PSS does -------------------
    # C named a component `<prefix>_t` and a sub-component's accessor
    # `<prefix>_<sub>`, so a sub-component `t` declared the type's name a
    # second time and the header did not compile.
    "a sub-component named t": Case("""
component leaf_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void poke(bit[32] v) { a.STS.write_val(v); }
}
component pss_top {
  leaf_c t;
  leaf_c u;
  solve function void initialize(addr_handle_t base) {
    t.initialize(base);
    u.initialize(make_handle_from_handle(base, 0x100));
  }
  target function void run() { t.poke(1); u.poke(2); }
}""", ["write 32 0x1004 0x1", "write 32 0x1104 0x2"]),

    # C and C++ rewrote `_c`, `_s` and `_e` to `_t`, which made these three
    # PSS types one C type.
    "a component, a struct and an enum with one stem": Case("""
enum x_e { XA = 1, XB = 2 }
struct x_s { bit[32] v; }
component x_c {
  target function bit[32] f(x_s p, x_e e) {
    if (e == XB) { return p.v; }
    return 0;
  }
}
component pss_top {""" + _ONE + """
  x_c x;
  target function void run() {
    x_s v;
    v.v = 5;
    a.STS.write_val(x.f(v, XB));
  }
}""", ["write 32 0x1004 0x5"]),

    # C named the handle parameter `s` (`self` in `_init`) and its bus pointer
    # `bus`, so a model's own `s` was declared twice: `x_f(x_c *s, x_s s)`.
    # The generated names are now `_self`/`_bus`, and a model's `_self` is
    # renamed like a C keyword.
    "names the generated code also uses": Case("""
component leaf_c {
  ga_c a;
  bit[32] bus;
  bit[32] _bus;
  solve function void initialize(addr_handle_t self, bit[32] _self) {
    a.set_handle(self);
    bus = _self;
    _bus = _self + 1;
  }
  target function bit[32] f(bit[32] s, bit[32] _self) {
    bit[32] self = s + _self;
    bit[32] _bus = self + bus;
    return _bus + this._bus;
  }
  target function void poke(bit[32] s) { a.STS.write_val(f(s, 1)); }
}
component pss_top {
  leaf_c s;
  solve function void initialize(addr_handle_t base) {
    s.initialize(base, 0x10);
  }
  target function void run() { s.poke(2); }
}""", ["write 32 0x1004 0x24"]),

    # ...and the ROOT's constructor parameters, which every target forwards
    # from a factory that takes the platform (`imp`, `imports`, `_bus`).
    "root constructor parameters named like generated ones": Case("""
component pss_top {
  ga_c a;
  bit[32] k;
  solve function void initialize(addr_handle_t imp, bit[32] self,
                                 bit[32] imports, bit[32] bus) {
    a.set_handle(imp);
    k = self + imports + bus;
  }
  target function void run() { a.STS.write_val(k); }
}""", ["write 32 0x1004 0x7"], args=(0x1000, 1, 2, 4)),

    # --- construction (LRM 20.1.2) -------------------------------------------
    # Every instance is constructed, whether or not a constructor reaches it.
    # C embeds a child by value, so a child its parent's constructor never
    # named read zeroes -- `int a = 5;` was 0, silently.
    "sub-components nobody initializes": Case("""
component inner_c { int k = 3; }
component sub_c {
  int a = 5;
  inner_c in1;
  target function int sum() { return a + in1.k; }
}
component pss_top {
  ga_c a;
  sub_c s;
  sub_c arr[2];
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(s.sum() + arr[1].sum()); }
}""", ["write 32 0x1004 0x10"]),

    # ...including one whose type has a constructor nobody calls.
    "a sub-component whose constructor nobody calls": Case("""
component cfg_c {
  int a = 10;
  int b = 1;
  solve function void initialize(int x) { b = a + x; }
  target function int get() { return a + b; }
}
component pss_top {
  ga_c a;
  cfg_c c;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { a.STS.write_val(c.get()); }
}""", ["write 32 0x1004 0xb"], xfail=_on(["py"], _PY_LAZY_SUB)),

    # A child is constructed before its parent's body runs, so what that body
    # writes into it (`c.a = 20`) is there when the child's constructor runs.
    "a constructor writes into a child before constructing it": Case("""
component cfg_c {
  int a = 10;
  int b;
  solve function void initialize(int x) { b = a + x; }
}
component pss_top {
  ga_c a;
  cfg_c c;
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    c.a = 20;
    c.initialize(1);
  }
  target function void run() { a.STS.write_val(c.b); }
}""", ["write 32 0x1004 0x15"], xfail={"py": _PY_LAZY_SUB}),

    # A constructor is a solve function like any other: locals, conditions
    # and loops. SV lowered only the address binding and refused the rest.
    # t = 2 + 4 = 6, then added three times.
    "a constructor with a local, a condition and a loop": Case("""
component pss_top {
  ga_c a;
  int x = 0;
  solve function void initialize(addr_handle_t base, int m) {
    int t = 2;
    a.set_handle(base);
    if (m > 1) { t = t + m; } else { t = 0; }
    repeat (3) { x = x + t; }
  }
  target function void run() { a.STS.write_val(x); }
}""", ["write 32 0x1004 0x12"], args=(0x1000, 4)),

    # --- D2: operations of a sub-component -------------------------------------
    "calling a sub-component's operation": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void poke() { a.STS.write_val(1); }
}
component pss_top {
  sub_c s;
  solve function void initialize(addr_handle_t base) { s.initialize(base); }
  target function void run() { s.poke(); }
}""", ["write 32 0x1004 0x1"]),

    "a sub-component's register written from above": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
}
component pss_top {
  sub_c s;
  solve function void initialize(addr_handle_t base) { s.initialize(base); }
  target function void run() { s.a.STS.write_val(1); }
}""", ["write 32 0x1004 0x1"]),

    "operations of a sub-component array": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void poke() { a.STS.write_val(1); }
}
component pss_top {
  sub_c s[2];
  solve function void initialize(addr_handle_t base) {
    foreach (s[i]) { s[i].initialize(base + 0x100 * i); }
  }
  target function void run() {
    s[1].poke();
    foreach (s[i]) { s[i].poke(); }
  }
}""", ["write 32 0x1104 0x1", "write 32 0x1004 0x1", "write 32 0x1104 0x1"]),

    "a sub-component's value in a condition": Case("""
component sub_c {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function bit[32] sts() { return a.STS.read_val(); }
  target function void poke() { a.STS.write_val(1); }
}
component pss_top {
  sub_c s;
  solve function void initialize(addr_handle_t base) { s.initialize(base); }
  target function void run() { if (s.sts() != 0) { s.poke(); } }
}""", ["read 32 0x1004 0x5", "write 32 0x1004 0x1"], mem={0x1004: 5}),

    # --- D3: bit and part selects (LRM 8.5.x; Table 21: unsigned, hi-lo+1) ----
    "a part select read": Case("""
component pss_top {
  gs_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() {
    bit[64] x = 0xABCDE123;
    ctl_s c;
    c.base_lo = x[31:12];
    a.CTL.write(c);
  }
}""", ["write 32 0x1000 0xabcde000"]),

    "a bit select and the high half of 64 bits": Case("""
component pss_top {""" + _ONE + """
  target function void run() {
    bit[64] x = 0x1234567800000008;
    a.STS.write_val(x[3]);
    a.STS.write_val(x[2]);
    a.STS.write_val(x[63:32]);
  }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0",
       "write 32 0x1004 0x12345678"]),

    "a bit select with a run-time index": Case("""
component pss_top {""" + _ONE + """
  target function void run() {
    bit[8] x = 0xA;
    int i = 0;
    while (i < 4) { a.STS.write_val(x[i]); i += 1; }
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x1",
       "write 32 0x1004 0x0", "write 32 0x1004 0x1"]),

    "a part and a bit select assigned": Case("""
component pss_top {""" + _ONE + """
  target function void run() {
    bit[32] v = 0xFFFFFFFF;
    v[7:4] = 0;
    v[0] = 0;
    v[31:28] = 0x5;
    a.STS.write_val(v);
  }
}""", ["write 32 0x1004 0x5fffff0e"]),

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
        mem={0x1000: 0xFFFFF001}),

    # --- D4: match arms -------------------------------------------------------
    # An enum item reaches the IR as the constant it names; what C, C++ and
    # Python could not lower was the `default:` arm (a wildcard pattern).
    "a match with a default arm": Case("""
component pss_top {""" + _ONE + """
  target function void go(int x) {
    match (x) {
      [1]: a.STS.write_val(10);
      [2, 3]: a.STS.write_val(20);
      default: a.STS.write_val(30);
    }
  }
  target function void run() { go(1); go(3); go(7); }
}""", ["write 32 0x1004 0xa", "write 32 0x1004 0x14", "write 32 0x1004 0x1e"]),

    "match on enum items": Case("""
enum mode_e { M0, M1, M2, M3 }
component pss_top {""" + _ONE + """
  target function void go(mode_e m) {
    match (m) {
      [M1]: a.STS.write_val(1);
      [M0, M2]: a.STS.write_val(2);
      default: a.STS.write_val(3);
    }
  }
  target function void run() { go(M0); go(M1); go(M2); go(M3); }
}""", ["write 32 0x1004 0x2", "write 32 0x1004 0x1",
       "write 32 0x1004 0x2", "write 32 0x1004 0x3"]),

    "match on items of a package's enum": Case("""
package modes_pkg { enum mode_e { M0, M1, M2 } }
import modes_pkg::*;
component pss_top {""" + _ONE + """
  target function void go(mode_e m) {
    match (m) {
      [M2]: a.STS.write_val(2);
      default: a.STS.write_val(0);
    }
  }
  target function void run() { go(M2); go(M0); }
}""", ["write 32 0x1004 0x2", "write 32 0x1004 0x0"]),

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
}""", ["write 32 0x1000 0x1", "write 32 0x1004 0x2"]),

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
}""", ["write 32 0x1028 0x1", "write 32 0x1008 0x2"]),

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
}""", ["write 32 0x1020 0x1"]),

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
}""", Refused([r"gi_c", r"STS"])),

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
}""", Refused([r"gi_c", r"get_offset_of_instance"])),

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
        mem={0x1000: 0xFFFFFFF9}),

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
}""", Refused([r"fld_s", r"mode", r"u_e"])),

    # --- D7: the conditional operator ------------------------------------------
    "a conditional expression": Case("""
component pss_top {""" + _ONE + """
  target function void go(bit x) { a.STS.write_val((x == 1) ? 0 : 1); }
  target function void run() { go(0); go(1); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x0"]),

    "a nested conditional whose arm reads a register": Case("""
component pss_top {""" + _ONE + """
  target function bit[32] sts() { return a.STS.read_val(); }
  target function void go(bit[2] x) {
    a.CTL.write_val((x == 1) ? sts() : ((x == 0) ? 7 : 9));
  }
  target function void run() { go(1); go(0); go(2); }
}""", ["read 32 0x1004 0x5", "write 32 0x1000 0x5",
       "write 32 0x1000 0x7", "write 32 0x1000 0x9"],
        mem={0x1004: 5}),

    # --- D8: a value-returning operation inside an expression ------------------
    "a value-returning operation in a condition": Case("""
component pss_top {""" + _ONE + """
  target function bool busy() { return a.STS.read_val() != 0; }
  target function void run() { if (busy()) { a.STS.write_val(0); } }
}""", ["read 32 0x1004 0x3", "write 32 0x1004 0x0"], mem={0x1004: 3}),

    # --- D9: PSS bit widths (LRM 8.7) ------------------------------------------
    # Equality propagates the larger operand width (4) to both sides, so ~15
    # is 0 here, and equals q.
    "~ in a context narrower than int": Case("""
component pss_top {""" + _ONE + """
  target function void go(bit[4] p, bit[4] q) {
    if ((~p) == q) { a.STS.write_val(1); } else { a.STS.write_val(2); }
  }
  target function void run() { go(15, 0); go(0, 15); go(1, 1); }
}""", ["write 32 0x1004 0x1", "write 32 0x1004 0x1", "write 32 0x1004 0x2"]),

    # The 64-bit parameter width propagates to x BEFORE the inversion.
    "~ in a context wider than its operand": Case("""
component pss_top {
  g64_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void go(bit[32] x) { a.W.write_val(~x); }
  target function void run() { go(1); }
}""", ["write 64 0x1000 0xfffffffffffffffe"]),

    # The case as originally reported: 0xFFFFFFFE is RIGHT (8.7.2, Table 23),
    # not 0. Pinned so nobody "fixes" it to the operand's width.
    "~ on a bit passed to a 32-bit parameter": Case("""
component pss_top {""" + _ONE + """
  target function void go(bit x) { a.STS.write_val(~x); }
  target function void run() { go(1); go(0); }
}""", ["write 32 0x1004 0xfffffffe", "write 32 0x1004 0xffffffff"]),

    # A `bit[4]` holds 0..15: 15 + 1 is 0, whatever C type stores it.
    "a narrow local wraps at its own width": Case("""
component pss_top {""" + _ONE + """
  target function void run() {
    bit[4] n = 15;
    n = n + 1;
    a.STS.write_val(n);
    n += 15;
    a.STS.write_val(n);
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0xf"]),

    # `int[8]` 127 + 1 is -128, sign-extended into the 32-bit parameter.
    "a narrow signed value overflows to negative": Case("""
component pss_top {""" + _ONE + """
  target function void run() {
    int[8] v = 127;
    v = v + 1;
    a.STS.write_val(v);
  }
}""", ["write 32 0x1004 0xffffff80"]),

    # PSS `/` and `%` truncate toward zero (8.5.1): -7 / 2 is -3, -7 % 2 is -1.
    "signed division truncates toward zero": Case("""
component pss_top {""" + _ONE + """
  target function void go(int x) {
    a.STS.write_val(x / 2);
    a.STS.write_val(x % 2);
  }
  target function void run() { go(-7); }
}""", ["write 32 0x1004 0xfffffffd", "write 32 0x1004 0xffffffff"]),

    # The assignment propagates 8 bits to `(~x) >> 1` and so to x: 0x01 ->
    # 0xFE -> 0x7F. In int it is -2 >> 1 = -1 -> 0xFF; at x's own 4 bits
    # it is 0xE >> 1 = 0x7.
    "~ then a right shift, assigned to 8 bits": Case("""
component pss_top {""" + _ONE + """
  target function void go(bit[4] x) {
    bit[8] y = (~x) >> 1;
    a.STS.write_val(y);
  }
  target function void run() { go(1); }
}""", ["write 32 0x1004 0x7f"]),

    # --- Names another language reserves -----------------------------------------
    # Each is free in PSS and reserved in SystemVerilog (IEEE 1800 Annex B);
    # `new` is reserved in C++ too. SV renamed from two partial
    # lists, and a field `new` was in neither.
    "fields, a local and a parameter named as SV keywords": Case("""
component pss_top {""" + _ONE + """
  int new = 1;
  int inside = 2;
  int xor = 3;
  int unique0 = 4;
  target function int add(int untyped) { int wor = untyped + new; return wor; }
  target function void run() { a.STS.write_val(add(inside + xor + unique0)); }
}""", ["write 32 0x1004 0xa"]),

    # --- Arrays of data in a component -------------------------------------------
    # SV declared no member for an array field, so a body naming one did not
    # compile. Elements start at 0 (LRM 8.1); 5 + 6 + (0 + 0 + 6 + 0).
    "array fields of a component": Case("""
component pss_top {""" + _ONE + """
  array<bit[32], 4> keys;
  bit[16] raw[3];
  target function void run() {
    bit[32] t = 0;
    keys[1] = 5;
    raw[2] = 6;
    foreach (raw[i]) { t += raw[i]; }
    a.STS.write_val(keys[1] + raw[2] + t);
  }
}""", ["write 32 0x1004 0x11"]),

    # --- A struct's defaults, wherever a value of it is -------------------------
    # A member's initial value belongs to its TYPE: every value of the struct
    # has it -- a local, a nested member, an array element (LRM 8.1). C and C++
    # gave it only to a struct field of a component, and 0 everywhere else.
    "struct defaults in a local and a nested struct": Case("""
enum m_e { M_A = 1, M_B = 2 }
struct q_s { bit[8] z = 5; }
struct p_s { bit[8] x; bit[8] y = 7; m_e m = M_B; q_s q; }
component pss_top {""" + _ONE + """
  p_s f;
  target function void run() {
    p_s l;
    a.STS.write_val(l.x + l.y + l.q.z);
    a.STS.write_val(f.q.z + f.m);
  }
}""", ["write 32 0x1004 0xc", "write 32 0x1004 0x7"]),

    "struct defaults in array elements": Case("""
struct p_s { bit[8] x; bit[8] y = 7; }
component sub_c {
  array<p_s, 2> ps;
  function bit[8] g() { return ps[1].y; }
}
component pss_top {""" + _ONE + """
  sub_c s;
  array<p_s, 3> qs;
  target function void run() {
    qs[0].x = 4;
    a.STS.write_val(qs[0].x + qs[2].y + s.g());
  }
}""", ["write 32 0x1004 0x12"]),

    # A value with no initializer has its type's default (LRM 7.5): 0 for a
    # number, an enum's FIRST item -- M_A, 3 here, neither 0 nor the smallest.
    # C left a scalar local indeterminate; C, C++ and SV started an enum at 0.
    "default values: an enum's first item, a bare local": Case("""
enum m_e { M_A = 3, M_B = 1 }
struct p_s { m_e m; }
component sub_c { m_e k; function int g() { return k; } }
component pss_top {""" + _ONE + """
  m_e f;
  p_s ps;
  array<m_e, 2> ae;
  sub_c s;
  target function void run() {
    m_e l;
    p_s lp;
    bit[32] n;
    a.STS.write_val(f + l + ps.m + lp.m + ae[1] + s.g() + n);
  }
}""", ["write 32 0x1004 0x12"]),

    # --- repeat (LRM 20.7.6) -------------------------------------------------------
    # The count is evaluated once: the body raising `n` does not add
    # iterations (0, 1, 2). Nested, zero, and break/continue (10, then 12).
    # C and C++ had no lowering for `repeat` at all.
    "repeat: counted, indexed, nested, empty": Case("""
component pss_top {""" + _ONE + """
  int n = 3;
  target function void run() {
    repeat (i : n) { n = n + 1; a.STS.write_val(i); }
    repeat (2) { repeat (j : 2) { a.CTL.write_val(j); } }
    repeat (0) { a.STS.write_val(99); }
    repeat (k : 5) {
      if (k == 1) { continue; }
      if (k == 3) { break; }
      a.STS.write_val(k + 10);
    }
    a.STS.write_val(n);
  }
}""", ["write 32 0x1004 0x0", "write 32 0x1004 0x1", "write 32 0x1004 0x2",
       "write 32 0x1000 0x0", "write 32 0x1000 0x1",
       "write 32 0x1000 0x0", "write 32 0x1000 0x1",
       "write 32 0x1004 0xa", "write 32 0x1004 0xc",
       "write 32 0x1004 0x6"]),

    # --- solve functions other than the constructor (LRM 22.2.3) ----------------
    # Private operations: the constructor calls them, they call each other, a
    # parent's constructor calls a child's. x = 7, then bump: 8; k = 3 + 1.
    # Every target refused the call ("no function named 'setup'").
    "solve functions the constructor calls": Case("""
component sub_c {
  int k = 1;
  solve function void prep(int v) { k = v + k; }
  function int get() { return k; }
}
component pss_top {
  ga_c a;
  int x = 0;
  sub_c s;
  solve function void setup(int v) { x = v; bump(); }
  solve function void bump() { x = x + 1; }
  solve function void initialize(addr_handle_t base) {
    a.set_handle(base);
    setup(7);
    s.prep(3);
  }
  target function void run() { a.STS.write_val(x); a.STS.write_val(s.get()); }
}""", ["write 32 0x1004 0x8", "write 32 0x1004 0x4"]),

    # Target context may not call one: the platform runs `run`, and a solve
    # function exists only while the tree is built.
    "a target function calling a solve function": Case("""
component pss_top {""" + _ONE + """
  int x = 0;
  solve function void setup(int v) { x = v; }
  target function void run() { setup(1); a.STS.write_val(x); }
}""", Refused([r"'setup' is a solve function", r"target context"])),

    # The same for C and C++: free in PSS, reserved there (`_self` is the
    # generated handle). C and C++ renamed fields and parameters, not locals.
    "fields, locals and a parameter named as C keywords": Case("""
component pss_top {""" + _ONE + """
  int auto = 1;
  int register = 2;
  target function int add(int short) { int char = short; return char; }
  target function void run() {
    int long = 4;
    int _self = 5;
    a.STS.write_val(auto + register + add(3) + long + _self);
  }
}""", ["write 32 0x1004 0xf"]),

    # --- Arrays of data in a function ------------------------------------------
    # A local array starts with each element at its type's default (LRM 8.1),
    # a struct element with its own defaults; it is indexed, iterated, and
    # handed by value to an assignment. C, C++ and SV failed with an internal
    # error ("unsupported C type for DataTypeArray").
    # w: {0, 9}, v = w; v[0] = 2: 9 + 2 + 7 + (0 + 9) = 0x1b.
    "local arrays": Case("""
struct p_s { bit[8] x; bit[8] y = 7; }
component pss_top {""" + _ONE + """
  target function void run() {
    array<bit[8], 2> w;
    array<bit[8], 2> v;
    array<p_s, 2> ps;
    bit[16] raw[3];
    bit[32] t = 0;
    w[1] = 9;
    v = w;
    v[0] = 2;
    raw[2] = w[0];
    foreach (w[i]) { t += w[i]; }
    a.STS.write_val(v[1] + v[0] + ps[1].y + t + raw[2]);
  }
}""", ["write 32 0x1004 0x1b"]),

    # An enum element starts at its first item, a 2-D array is copied whole,
    # and an array of structs copied into a field is a copy: writing the
    # local afterwards leaves the field alone. ae: 3; m2[1][0] = 4 copied to
    # n2; keep[1].x = 5, then l[1].x = 9: 3 + 4 + 5 + 7 = 0x13.
    "local arrays: enum elements, two dimensions, copied structs": Case("""
enum m_e { M_A = 3, M_B = 1 }
struct p_s { bit[8] x; bit[8] y = 7; }
component pss_top {""" + _ONE + """
  array<p_s, 2> keep;
  target function void run() {
    array<m_e, 2> ae;
    array<array<bit[8], 2>, 2> m2;
    array<array<bit[8], 2>, 2> n2;
    array<p_s, 2> l;
    m2[1][0] = 4;
    n2 = m2;
    m2[1][0] = 8;
    l[1].x = 5;
    keep = l;
    l[1].x = 9;
    a.STS.write_val(ae[1] + n2[1][0] + keep[1].x + keep[0].y);
  }
}""", ["write 32 0x1004 0x13"]),

    # PSS passes an aggregate by handle (LRM 20.3.2): the callee's write is
    # the caller's array. 3 + 4 = 7, then w[0] is 4: 0x7, 0x4. Python's list
    # is a handle already; C and SV failed with an internal error, and C++'s
    # `std::array` would have been a copy.
    "an array parameter": Case("""
component pss_top {""" + _ONE + """
  function bit[8] sum(array<bit[8], 2> v) { v[0] = 4; return v[0] + v[1]; }
  target function void run() {
    array<bit[8], 2> w;
    w[1] = 3;
    a.STS.write_val(sum(w));
    a.STS.write_val(w[0]);
  }
}""", ["write 32 0x1004 0x7", "write 32 0x1004 0x4"],
        refused=_on(("c", "cpp", "sv"),
                    Refused([r"pss_top::sum: an array parameter 'v'"]))),

    # --- Array initializers ------------------------------------------------------
    # `{...}` as a field's initializer, a local's and an assigned value, nested
    # for an array of arrays. Each element is assigned to its element type
    # (8.7.2: 300 is 0x2c in a `bit[8]`), and every element is read before
    # any is written, so `w = {w[1], w[0]}` swaps. No target rendered one; the
    # case was refused before.
    "array initializers": Case("""
enum m_e { A, B, C }
component pss_top {""" + _ONE + """
  array<bit[32], 3> v = {1, 2, 3};
  array<array<bit[8], 2>, 2> g = {{1, 2}, {3, 4}};
  array<m_e, 2> e = {C, A};
  target function void run() {
    array<bit[8], 2> w = {v[2], 300};
    a.STS.write_val(v[0] + w[1]);
    w = {w[1], w[0]};
    a.STS.write_val(w[0] * 16 + w[1]);
    g = {{w[1], 7}, {g[0][0], g[1][1]}};
    a.STS.write_val(g[0][0] * 1000 + g[0][1] * 100 + g[1][0] * 10 + g[1][1]);
    e = {e[0], B};
    if (e[0] == C && e[1] == B) { a.STS.write_val(1); }
  }
}""", ["write 32 0x1004 0x2d", "write 32 0x1004 0x2c3",
       "write 32 0x1004 0xe82", "write 32 0x1004 0x1"]),

    # A PSS format is not a printf format (LRM 21.1.1): `%n` prints an enum's
    # item and a bool's name, and `%d` reads a value at its PSS width. C and
    # C++ passed the format to printf, whose `%n` STORES through its argument.
    "message: %n of an enum and a bool, %d at the PSS width": Case("""
enum mode_e { OFF, SLOW = 5, FAST }
component pss_top {""" + _ONE + """
  target function void run() {
    mode_e m = FAST;
    bool ok = true;
    bit[8] b = 255;
    message(LOW, "m=%n ok=%n m=%d b=%d b=%u b=%x 100%%", m, ok, m, b, b, b);
    a.STS.write_val(1);
  }
}""", ["m=FAST ok=true m=6 b=-1 b=255 b=ff 100%", "write 32 0x1004 0x1"]),

    # An enum item is the one the linker found. By name alone, `C` and `SV`
    # here were the core library's `target_language_e::C` and `::SV` (0, 2).
    "enum items named as the core library's": Case("""
enum lang_e { SV, CPP = 4, C }
component pss_top {""" + _ONE + """
  lang_e f = C;
  target function void run() {
    lang_e x = SV;
    a.STS.write_val((bit[32])f * 16 + (bit[32])x);
    if (x == SV && f == C) { a.STS.write_val(1); }
  }
}""", ["write 32 0x1004 0x50", "write 32 0x1004 0x1"]),

    # An item is scoped by its enum (7.5): two enums may each have an `A`.
    # Python's module-level constants made them one name, the second
    # silently replacing the first; C, C++ and SV promote items to the
    # enclosing scope, where two `A`s do not compile.
    "two enums with an item of one name": Case("""
enum dir_e { A, B = 3 }
enum op_e { B, A = 7 }
component pss_top {""" + _ONE + """
  target function void run() {
    dir_e d = A;
    op_e o = A;
    a.STS.write_val((bit[32])d * 16 + (bit[32])o);
    if (d == dir_e::A && o == op_e::A) { a.STS.write_val(1); }
    message(LOW, "%n %n", d, o);
  }
}""", ["write 32 0x1004 0x7", "write 32 0x1004 0x1", "A A"]),

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
        mem={0x1000: 0x2}),

    # An initializer has the array's element count (8.1), at every level.
    "an array initializer of the wrong size": Case("""
component pss_top {""" + _ONE + """
  array<bit[32], 3> v = {1, 2};
  target function void run() {
    array<array<bit[8], 2>, 2> w;
    w = {{1, 2}, {3, 4, 5}};
    a.STS.write_val(v[0] + w[1][0]);
  }
}""", Refused([r"pss_top\.v: an array initializer with 2 element\(s\), "
               r"for an array of 3",
               r"pss_top::run: an array initializer with 3 element\(s\), "
               r"for an array of 2"])),

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
}""", ["read 32 0x1000 0x1", "write 32 0x1004 0x1"], mem={0x1000: 0x1}),

    # --- Integers wider than 64 bits -------------------------------------------
    # C and C++ have no integer type wider than 64 bits. `bit[256]` was
    # declared `uint64_t`, losing the top 192 bits with no diagnostic; it is
    # refused there (user ruling, 2026-10-07). Python and SV carry it whole:
    # 5 shifted up by 200 reads back from bits [231:200].
    "a struct field wider than 64 bits": Case("""
struct key_s { bit[256] k; bit[8] n; }
component pss_top {""" + _ONE + """
  key_s key;
  target function void run() {
    key.k = 5;
    key.k = key.k << 200;
    a.STS.write_val(key.k[231:200]);
  }
}""", ["write 32 0x1004 0x5"],
        refused=_on(("c", "cpp"), Refused([r"key_s\.k", r"bit\[256\]",
                                            r"wider than 64"]))),

    "a local, parameter and result wider than 64 bits": Case("""
component pss_top {""" + _ONE + """
  function bit[96] widen(bit[72] x) { return x; }
  target function void run() {
    bit[128] w = widen(3);
    a.STS.write_val((bit[32])w);
  }
}""", ["write 32 0x1004 0x3"],
        refused=_on(("c", "cpp"), Refused([r"pss_top::widen: bit\[72\]",
                                            r"pss_top::widen: bit\[96\]",
                                            r"pss_top::run: bit\[128\]"]))),

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
}""", Refused([r"'g\.W'", r"128 bits", r"wider than 64"])),
}


def _params():
    for name in sorted(CASES):
        case = CASES[name]
        for tgt in _ALL:
            marks = []
            why = th.available(tgt)
            if why:
                marks.append(pytest.mark.skip(reason=why))
            if tgt in ("c", "cpp"):
                marks.append(pytest.mark.c_toolchain)
            if tgt == "sv":
                marks.append(pytest.mark.sim)
            if tgt in case.xfail:
                marks.append(pytest.mark.xfail(strict=True,
                                               reason=case.xfail[tgt]))
            yield pytest.param(name, tgt, marks=marks, id=f"{tgt}-{name}")


@pytest.mark.parametrize("name, target", list(_params()))
def test_construct(tmp_path, name, target):
    case = CASES[name]
    want = case.refused.get(target, case.want)
    if isinstance(want, Refused):
        with pytest.raises(CompileError) as ei:
            th.compile_model(tmp_path, _REGS + case.pss, target)
        text = diagnostic_text(ei)
        for pat in want.patterns:
            assert re.search(pat, text), (pat, text)
        return
    got = th.run(target, tmp_path, _REGS + case.pss, case.args, case.mem)
    assert got == case.want


@pytest.mark.parametrize("target", _ALL)
def test_an_enum_field_has_its_base_types_width_in_the_manifest(tmp_path,
                                                                target):
    """`enum e2_e : bit[2]` makes the field two bits wide (D6). The layout is
    the manifest's statement, so a consumer packing the value by hand gets
    the same bits the generated accessors do."""
    man = tmp_path / "manifest.json"
    th.compile_model(tmp_path, _REGS + CASES[
        "an enum field in a packed register struct"].pss, target,
        progseq_manifest=str(man))
    doc = json.loads(man.read_text())
    (fld,) = [s for s in doc["value_structs"] if s["name"] == "fld_s"]
    assert fld["bits"] == 32
    assert [(f["name"], f["lsb"], f["width"]) for f in fld["fields"]] == [
        ("lo", 0, 1), ("mode", 1, 2), ("hi", 3, 29)]
