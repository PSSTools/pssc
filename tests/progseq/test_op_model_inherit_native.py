"""Component inheritance rendered NATIVELY, held to one behaviour.

op-model-py, op-model-cpp and op-model-sv render a derived component as a
derived class (`class Der(Base)`, `class der : public base`,
`class der extends base`), with the language's own `super` (`super().f`,
`base::f`, `super.f`). Three renderings of one model must behave as one: each
case here runs on all three, and the message and memory-access traces must be
the same, and the expected one. C, which flattens, is held to the same traces. See `targets/comp_inherit.py`.
"""
from __future__ import annotations

import pytest

from . import trace_harness as th

_CC = th.CC
_CXX = th.CXX
_VERILATOR = th.VERILATOR
_run_py = th._run_py
_run_sv = th._run_sv


def _run_cpp(tmp_path, pss, cxx, args=(), mem=None):
    return th._run_cpp(tmp_path, pss, args, mem, cxx=cxx)


_CASES = {
    "virtual dispatch": ("""
component leaf_c { target function void ping() { message(NONE, "ping"); } }
component base_c {
  int a = 5;
  leaf_c lf;
  target function int f(int x) { return x + a; }
  target function int h(int x) { return f(x) + 100; }
  target function void pinglf() { lf.ping(); }
}
component mid_c : base_c {
  int b = 7;
  target function int f(int x) { return x * 10 + b; }
}
component leafmost_c : mid_c { }
component pss_top {
  base_c bb;
  mid_c m;
  leafmost_c l;
  target function void run() {
    message(NONE, "%d %d %d", bb.h(1), m.h(1), l.h(1));
    m.pinglf();
    l.pinglf();
  }
}""", (), None, ["106 117 117", "ping", "ping"]),

    "a derived root": ("""
component base_c {
  target function int f(int x) { return x + 1; }
  target function int h(int x) { return f(x) * 2; }
}
component pss_top : base_c {
  target function int f(int x) { return x + 1000; }
  target function void run() { message(NONE, "%d %d", f(1), h(1)); }
}""", (), None, ["1001 2002"]),

    "an inherited register block": ("""
pure component regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTRL;
  reg_c<bit[32], READWRITE, 32> STAT;
  function bit[64] get_offset_of_instance(string name) {
    match (name) {
      ["CTRL"]: return 0x0;
      ["STAT"]: return 0x8;
    }
    return 0;
  }
}
component base_c {
  regs_c regs;
  solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
  target function void kick() { regs.CTRL.write_val(regs.STAT.read_val() | 1); }
}
component pss_top : base_c {
  target function void run() { kick(); }
}""", (0x100,), {0x108: 0x40},
        ["read 32 0x108 0x40", "write 32 0x100 0x41"]),

    "super and a field declared twice": ("""
component base_c {
  int a = 5;
  target function int f(int x) { return x + a + g(); }
  target function int g() { return 1; }
  target function int peek() { return a; }
}
component der_c : base_c {
  int a = 100;
  target function int f(int x) { return super.f(x) * 1000 + a + super.a; }
  target function int g() { return 2; }
  solve function void bump() { super.a += 1; a += 10; }
}
component third_c : der_c {
  target function int f(int x) { return super.f(x) + 7; }
}
component pss_top {
  base_c b;
  der_c d;
  third_c t;
  solve function void initialize() { d.bump(); }
  target function void run() {
    message(NONE, "%d %d %d", b.f(1), d.f(1), t.f(1));
    message(NONE, "%d %d %d", d.a, d.peek(), d.f(0));
  }
}""", (), None, ["7 9116 8112", "110 6 8116"]),

    "constructors": ("""
pure component regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> R;
}
component base_c {
  regs_c regs;
  int id;
  solve function void initialize(addr_handle_t base) {
    regs.set_handle(base);
    id = 1;
  }
  target function void poke() { regs.R.write_val(id); }
}
component der_c : base_c {
  regs_c more;
  solve function void initialize(addr_handle_t base) {
    super.initialize(base);
    more.set_handle(make_handle_from_handle(base, 0x40));
    id = id + 10;
  }
  target function void poke2() { more.R.write_val(id); }
}
component pss_top : der_c {
  target function void run() { poke(); poke2(); }
}""", (0x200,), None,
        ["write 32 0x200 0xb", "write 32 0x240 0xb"]),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_python(tmp_path, case):
    pss, args, mem, want = _CASES[case]
    assert _run_py(tmp_path, pss, args, mem) == want


#: C renders the COMPLETED (flattened) view rather than a class per type, but
#: the behaviour is the same and so is the trace. `super.initialize(...)` is a
#: call to a private copy of the base's constructor -- a solve function, which
#: C renders file-local (`_solve_impl`).
@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
@pytest.mark.parametrize("case", sorted(_CASES))
def test_c(tmp_path, case):
    pss, args, mem, want = _CASES[case]
    assert th._run_c(tmp_path, pss, args, mem) == want


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CXX, reason="no C++ compiler")
@pytest.mark.parametrize("case", sorted(_CASES))
def test_cpp(tmp_path, case):
    pss, args, mem, want = _CASES[case]
    assert _run_cpp(tmp_path, pss, _CXX[0], args, mem) == want


@pytest.mark.sim
@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
@pytest.mark.parametrize("case", sorted(_CASES))
def test_sv(tmp_path, case):
    pss, args, mem, want = _CASES[case]
    assert _run_sv(tmp_path, pss, args, mem) == want
