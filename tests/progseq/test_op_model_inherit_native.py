"""Component inheritance rendered NATIVELY, held to one behaviour.

op-model-py and op-model-cpp render a derived component as a derived class
(`class Der(Base)`, `class der : public base`), with the language's own `super`
(`super().f`, `base::f`). Two renderings of one model must behave as one: each
case here runs on both, and the message and memory-access traces must be the
same, and the expected one. See `targets/comp_inherit.py`.
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys

import pytest

from pssc import driver

from .conftest import available_cpp_compilers

_CXX = available_cpp_compilers()

#: A platform that logs every access and answers reads from a map; the
#: Python side is `pssc_rt.MemoryBus`, which logs the same things.
_CPP_MAIN = r"""
#include "pss_top.hpp"
#include <cstdarg>
#include <cstdio>
#include <map>
namespace pssc {
void message(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); std::vprintf(fmt, ap); va_end(ap);
    std::printf("\n");
}
}
struct mem : pssc::mem_if {
    std::map<pssc::addr_t, std::uint64_t> m;
    std::uint64_t rd(int w, pssc::addr_t a) {
        std::uint64_t v = m[a] & (w == 64 ? ~0ull : ((1ull << w) - 1));
        std::printf("read %d 0x%llx 0x%llx\n", w, (unsigned long long)a,
                    (unsigned long long)v);
        return v;
    }
    void wr(int w, pssc::addr_t a, std::uint64_t d) {
        m[a] = d;
        std::printf("write %d 0x%llx 0x%llx\n", w, (unsigned long long)a,
                    (unsigned long long)d);
    }
    void write8 (pssc::addr_t a, std::uint8_t  d) override { wr(8, a, d); }
    std::uint8_t  read8 (pssc::addr_t a) override { return (std::uint8_t)rd(8, a); }
    void write16(pssc::addr_t a, std::uint16_t d) override { wr(16, a, d); }
    std::uint16_t read16(pssc::addr_t a) override { return (std::uint16_t)rd(16, a); }
    void write32(pssc::addr_t a, std::uint32_t d) override { wr(32, a, d); }
    std::uint32_t read32(pssc::addr_t a) override { return (std::uint32_t)rd(32, a); }
    void write64(pssc::addr_t a, std::uint64_t d) override { wr(64, a, d); }
    std::uint64_t read64(pssc::addr_t a) override { return rd(64, a); }
};
int main() {
    mem bus;
    @PRELOAD@
    auto top = pss_top::pss_top::create(bus@ARGS@);
    top->run();
    return 0;
}
"""


def _compile(tmp_path, pss, target):
    p = tmp_path / "m.pss"
    p.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n" + pss)
    out = tmp_path / target
    driver.compile([str(p)], target=target, opts=argparse.Namespace(
        progseq_root="pss_top", output_dir=str(out)))
    return out


def _run_py(tmp_path, pss, args=(), mem=None):
    out = _compile(tmp_path, pss, "op-model-py")
    sys.path.insert(0, str(out))
    try:
        for name in ("pss_top", "pssc_rt"):
            sys.modules.pop(name, None)
        mod = importlib.import_module("pss_top")
        rt = importlib.import_module("pssc_rt")
    finally:
        sys.path.remove(str(out))
    bus = rt.MemoryBus()
    for a, v in (mem or {}).items():
        bus.mem[a] = v
    mod.PssTop(bus, *args).run()
    return [d if k == "message" else f"{k} {w} 0x{a:x} 0x{d:x}"
            for k, w, a, d in bus.log]


def _run_cpp(tmp_path, pss, cxx, args=(), mem=None):
    out = _compile(tmp_path, pss, "op-model-cpp")
    preload = " ".join(f"bus.m[{a:#x}] = {v:#x};" for a, v in
                       (mem or {}).items())
    (out / "main.cpp").write_text(
        _CPP_MAIN.replace("@PRELOAD@", preload).replace(
            "@ARGS@", "".join(f", {a:#x}" for a in args)))
    build = subprocess.run(
        [cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(out),
         str(out / "main.cpp"), "-o", str(out / "run")],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(out / "run")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


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
  target function void bump() { super.a += 1; a += 10; }
}
component third_c : der_c {
  target function int f(int x) { return super.f(x) + 7; }
}
component pss_top {
  base_c b;
  der_c d;
  third_c t;
  target function void run() {
    message(NONE, "%d %d %d", b.f(1), d.f(1), t.f(1));
    d.bump();
    message(NONE, "%d %d %d", d.a, d.peek(), d.f(0));
  }
}""", (), None, ["7 8105 8112", "110 6 8116"]),

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
    more.set_handle(base + 0x40);
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


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CXX, reason="no C++ compiler")
@pytest.mark.parametrize("case", sorted(_CASES))
def test_cpp(tmp_path, case):
    pss, args, mem, want = _CASES[case]
    assert _run_cpp(tmp_path, pss, _CXX[0], args, mem) == want
