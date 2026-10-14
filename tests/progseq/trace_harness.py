"""Run one PSS model on every op-model target and return its trace.

A case is a PSS source whose root is ``pss_top``, holding a
``target function void run()``, plus the handle arguments its ``initialize``
takes and memory to preload. Each runner generates the model for one target,
builds it, calls ``run()`` on a platform that logs every access and answers
reads from a map, and returns the log as lines:

    ``read 32 0x108 0x40`` / ``write 32 0x100 0x41`` / a message's text

The same lines from every target, so a case states ONE expected trace and
every rendering is held to it. That expected trace is written by hand in the
test: an address taken from a backend proves only that the backends agree.

A refused model surfaces as the ``CompileError`` the CLI reports as a user
error (exit 1). Anything else escaping ``compile`` is what the CLI calls an
internal error, and is left to propagate.
"""
from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys

from pssc import driver

from .conftest import available_c_compilers, available_cpp_compilers

CC = available_c_compilers()
CXX = available_cpp_compilers()
VERILATOR = shutil.which("verilator")

TARGETS = ("c", "cpp", "py", "sv")

_PRELUDE = "import std_pkg::*;\nimport addr_reg_pkg::*;\n"

#: op-model-sv's API is its exports, so `run` is exported for it.
_EXPORT_RUN = "\nextend component pss_top { export target function run; }\n"


def compile_model(tmp_path, pss, target, **opts):
    """Generate ``pss`` for ``op-model-<target>``; return the output dir.
    ``opts`` are further option attributes (``progseq_manifest=...``)."""
    p = tmp_path / "m.pss"
    p.write_text(_PRELUDE + pss + (_EXPORT_RUN if target == "sv" else ""))
    out = tmp_path / target
    opts = argparse.Namespace(progseq_root="pss_top", output_dir=str(out),
                              **opts)
    if target == "c":
        opts.c_prefix = "pss_top"
        # The harness traces every access through the bus it hands in, which
        # only the vtable seam takes.
        if not hasattr(opts, "c_link_style"):
            opts.c_link_style = "vtable"
    driver.compile([str(p)], target=f"op-model-{target}", opts=opts)
    return out


def run(target, tmp_path, pss, args=(), mem=None):
    """The trace of ``pss_top.run()`` on ``target``."""
    return _RUNNERS[target](tmp_path, pss, args, mem)


def available(target):
    """'' if ``target`` can be built and run here, else why not."""
    return {"c": "" if CC else "no C compiler",
            "cpp": "" if CXX else "no C++ compiler",
            "py": "",
            "sv": "" if VERILATOR else "verilator not on PATH"}[target]


# --- Python ------------------------------------------------------------------

def _run_py(tmp_path, pss, args=(), mem=None):
    out = compile_model(tmp_path, pss, "py")
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


# --- C -----------------------------------------------------------------------

_C_MAIN = r"""
#include "pss_top.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

void pssc_message(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
    printf("\n");
}

#define NMEM 256
static pssc_addr_t mem_a[NMEM];
static uint64_t mem_d[NMEM];
static int mem_n;

static uint64_t *cell(pssc_addr_t a) {
    int i;
    for (i = 0; i < mem_n; i++) if (mem_a[i] == a) return &mem_d[i];
    if (mem_n == NMEM) abort();
    mem_a[mem_n] = a; mem_d[mem_n] = 0;
    return &mem_d[mem_n++];
}
static uint64_t rd(int w, pssc_addr_t a) {
    uint64_t v = *cell(a) & (w == 64 ? ~0ull : ((1ull << w) - 1));
    printf("read %d 0x%llx 0x%llx\n", w, (unsigned long long)a,
           (unsigned long long)v);
    return v;
}
static void wr(int w, pssc_addr_t a, uint64_t d) {
    *cell(a) = d;
    printf("write %d 0x%llx 0x%llx\n", w, (unsigned long long)a,
           (unsigned long long)d);
}
static void     w8 (void *c, pssc_addr_t a, uint8_t  d) { (void)c; wr(8, a, d); }
static uint8_t  r8 (void *c, pssc_addr_t a) { (void)c; return (uint8_t)rd(8, a); }
static void     w16(void *c, pssc_addr_t a, uint16_t d) { (void)c; wr(16, a, d); }
static uint16_t r16(void *c, pssc_addr_t a) { (void)c; return (uint16_t)rd(16, a); }
static void     w32(void *c, pssc_addr_t a, uint32_t d) { (void)c; wr(32, a, d); }
static uint32_t r32(void *c, pssc_addr_t a) { (void)c; return (uint32_t)rd(32, a); }
static void     w64(void *c, pssc_addr_t a, uint64_t d) { (void)c; wr(64, a, d); }
static uint64_t r64(void *c, pssc_addr_t a) { (void)c; return rd(64, a); }

int main(void) {
    static const pssc_mem_if bus = { w8, r8, w16, r16, w32, r32, w64, r64, 0 };
    pss_top *top;
    @PRELOAD@
    top = pss_top_create(&bus@ARGS@);
    pss_top_run(top);
    pss_top_destroy(top);
    return 0;
}
"""


def _run_c(tmp_path, pss, args=(), mem=None):
    out = compile_model(tmp_path, pss, "c")
    preload = " ".join(f"*cell({a:#x}) = {v:#x}ull;" for a, v in
                       (mem or {}).items())
    (out / "main.c").write_text(
        _C_MAIN.replace("@PRELOAD@", preload).replace(
            "@ARGS@", "".join(f", {a:#x}ull" for a in args)))
    build = subprocess.run(
        [CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(out),
         str(out / "main.c"), str(out / "pss_top.c"), "-o", str(out / "run")],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    res = subprocess.run([str(out / "run")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout.splitlines()


# --- C++ ---------------------------------------------------------------------

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


def _run_cpp(tmp_path, pss, args=(), mem=None, cxx=None):
    out = compile_model(tmp_path, pss, "cpp")
    preload = " ".join(f"bus.m[{a:#x}] = {v:#x};" for a, v in
                       (mem or {}).items())
    (out / "main.cpp").write_text(
        _CPP_MAIN.replace("@PRELOAD@", preload).replace(
            "@ARGS@", "".join(f", {a:#x}" for a in args)))
    build = subprocess.run(
        [cxx or CXX[0], "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I",
         str(out), str(out / "main.cpp"), "-o", str(out / "run")],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    res = subprocess.run([str(out / "run")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout.splitlines()


# --- SystemVerilog (Verilator) -----------------------------------------------

#: The SV platform: logs as the others do, answers reads from a map. The
#: model's `run` is reached through the context API, which takes its methods
#: from exports -- so `compile_model` exports it (`_EXPORT_RUN`).
_SV_TB = r"""
module top;
  import pssc_reg_pkg::*;
  import pss_top_pkg::*;

  class plat_c;
    bit [63:0] m[addr_handle_t];
    task rd(int w, addr_handle_t a, output bit [63:0] v);
      v = m.exists(a) ? m[a] : 0;
      if (w != 64) v &= (64'h1 << w) - 1;
      $display("read %0d 0x%0h 0x%0h", w, a, v);
    endtask
    task wr(int w, addr_handle_t a, bit [63:0] d);
      m[a] = d;
      $display("write %0d 0x%0h 0x%0h", w, a, d);
    endtask
    task write8 (addr_handle_t a, bit [7:0]  d); wr(8, a, d); endtask
    task write16(addr_handle_t a, bit [15:0] d); wr(16, a, d); endtask
    task write32(addr_handle_t a, bit [31:0] d); wr(32, a, d); endtask
    task write64(addr_handle_t a, bit [63:0] d); wr(64, a, d); endtask
    task read8 (addr_handle_t a, output bit [7:0]  d); bit [63:0] v; rd(8, a, v); d = v; endtask
    task read16(addr_handle_t a, output bit [15:0] d); bit [63:0] v; rd(16, a, v); d = v; endtask
    task read32(addr_handle_t a, output bit [31:0] d); bit [63:0] v; rd(32, a, v); d = v; endtask
    task read64(addr_handle_t a, output bit [63:0] d); rd(64, a, d); endtask
  endclass

  initial begin
    plat_c plat = new();
    pss_top_ctxt_if dut;
    @PRELOAD@
    dut = pss_top_root #(plat_c)::create(plat@ARGS@);
    dut.run();
    $finish;
  end
endmodule
"""


def _run_sv(tmp_path, pss, args=(), mem=None):
    out = compile_model(tmp_path, pss, "sv")
    preload = " ".join(f"plat.m[64'h{a:x}] = 64'h{v:x};" for a, v in
                       (mem or {}).items())
    (out / "tb.sv").write_text(
        _SV_TB.replace("@PRELOAD@", preload).replace(
            "@ARGS@", "".join(f", 64'h{a:x}" for a in args)))
    files = [str(out / "pssc_reg_pkg.sv"), str(out / "pss_top_pkg.sv"),
             str(out / "tb.sv")]
    build = subprocess.run(
        [VERILATOR, "--binary", "-Wno-fatal", "--top-module", "top", f"-I{out}",
         "-Mdir", str(tmp_path / "obj"), "-o", "sim"] + files,
        capture_output=True, text=True, cwd=tmp_path)
    assert build.returncode == 0, build.stdout + build.stderr
    res = subprocess.run([str(tmp_path / "obj" / "sim")],
                         capture_output=True, text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    # Verilator's own report lines start with `- `; the model's never do.
    return [ln for ln in res.stdout.splitlines() if not ln.startswith("- ")]


_RUNNERS = {"c": _run_c, "cpp": _run_cpp, "py": _run_py, "sv": _run_sv}
