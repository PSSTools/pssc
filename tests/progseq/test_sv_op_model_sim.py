"""The SV operation model, compiled and RUN under Verilator.

`test_op_model_sv.py` lints the generated package; a lint says the output is
SystemVerilog, not that it does what the model says. This drives a generated
model through a platform that records every access and compares the trace
with what the PSS means.

The model is the worked example of `docs/design/sv-op-model-inheritance.md`:
a root whose `exec init_down` binds its children (design D3), and a derived
component whose `super.wait_idle()` must read the BASE's `retries` (LRM 17.1).
Its expected trace is stated in that document.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess

import pytest

from pssc import driver

pytestmark = pytest.mark.sim

_VERILATOR = shutil.which("verilator")

_MODEL = """
import std_pkg::*;
import addr_reg_pkg::*;

pure component dev_regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTRL;
  reg_c<bit[32], READWRITE, 32> STAT;
  function bit[64] get_offset_of_instance(string name) {
    match (name) {
      ["CTRL"]: return 0x0;
      ["STAT"]: return 0x4;
    }
    return 0;
  }
}

component dev_base_c {
  dev_regs_c regs;
  int        retries = 3;

  solve function void initialize(addr_handle_t base) {
    regs.set_handle(base);
  }

  target function void start(bit[32] mode) {
    regs.CTRL.write_val(mode);
    wait_idle();
  }

  target function void wait_idle() {
    int n = 0;
    while (n < retries) {
      regs.STAT.read_val();
      n += 1;
    }
  }
}

component fast_dev_c : dev_base_c {
  int retries = 10;

  target function void wait_idle() {
    super.wait_idle();
    regs.CTRL.write_val(0);
  }
}

component pss_top {
  dev_base_c    slow;
  fast_dev_c    fast;
  addr_handle_t base;

  solve function void initialize(addr_handle_t b) {
    base = b;
  }

  exec init_down {
    slow.initialize(base);
    fast.initialize(base + 0x100);
  }

  target function void run() {
    slow.start(1);
    fast.start(2);
  }
}
"""

_TB = """
module top;
  import pssc_reg_pkg::*;
  import pss_top_pkg::*;

  class plat_c implements pss_top_import_if;
    virtual task write8(addr_handle_t addr, bit [7:0] data); endtask
    virtual task read8(addr_handle_t addr, output bit [7:0] data); data = 0; endtask
    virtual task write16(addr_handle_t addr, bit [15:0] data); endtask
    virtual task read16(addr_handle_t addr, output bit [15:0] data); data = 0; endtask
    virtual task write32(addr_handle_t addr, bit [31:0] data);
      $display("TRACE W %0h %0h", addr, data);
    endtask
    virtual task read32(addr_handle_t addr, output bit [31:0] data);
      $display("TRACE R %0h", addr);
      data = 0;
    endtask
    virtual task write64(addr_handle_t addr, bit [63:0] data); endtask
    virtual task read64(addr_handle_t addr, output bit [63:0] data); data = 0; endtask
  endclass

  initial begin
    plat_c plat = new();
    pss_top_if dut = pss_top#(plat_c)::create(plat, 64'h4000_0000);
    dut.run();
    $finish;
  end
endmodule
"""

#: `slow` at the base, `fast` at base + 0x100 (the root's `init_down`); each
#: `start` writes CTRL and polls STAT `retries` times -- THREE for `fast` too,
#: because `super.wait_idle()` is `dev_base_c`'s code reading its own field --
#: and `fast` then clears CTRL.
_EXPECTED = (["W 40000000 1"] + ["R 40000004"] * 3
             + ["W 40000100 2"] + ["R 40000104"] * 3 + ["W 40000100 0"])


def _build_and_run(tmp_path, model: str, tb: str) -> str:
    """Generate ``model`` rooted at `pss_top`, build it with ``tb``, run it,
    and return what it printed."""
    src = tmp_path / "model.pss"
    src.write_text(model)
    out = tmp_path / "gen"
    ns = argparse.Namespace(progseq_root="pss_top",
                            progseq_package="pss_top_pkg",
                            output_dir=str(out))
    res = driver.compile([str(src)], target="op-model-sv", opts=ns)
    (out / "tb.sv").write_text(tb)

    files = [str(p) for p in res.outputs] + [str(out / "tb.sv")]
    build = subprocess.run(
        [_VERILATOR, "--binary", "-Wno-fatal", "--top-module", "top",
         "-Mdir", str(tmp_path / "obj"), "-o", "sim"] + files,
        capture_output=True, text=True, cwd=tmp_path)
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run([str(tmp_path / "obj" / "sim")],
                         capture_output=True, text=True, cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_init_down_and_super_run_as_the_model_says(tmp_path):
    stdout = _build_and_run(tmp_path, _MODEL, _TB)
    trace = [ln[len("TRACE "):] for ln in stdout.splitlines()
             if ln.startswith("TRACE ")]
    assert trace == _EXPECTED


_INIT_MODEL = """
import std_pkg::*;

component base_c {
  int a = 1;
  exec init_down { a = 2; }
  exec init_up   { a = a + 10; }
  target function void show_a() { message(NONE, "a=%d", a); }
}

component der_c : base_c {
  int b = 0;
  exec init_down { super; b = a; }
  target function void show_b() { message(NONE, "b=%d", b); }
}

component pss_top {
  der_c d;
  target function void run() { d.show_a(); d.show_b(); }
}
"""

#: The same platform; this root has no constructor, so `create()` takes only it.
_NO_CTOR_TB = _TB.replace(", 64'h4000_0000", "")


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_init_blocks_run_in_lrm_order(tmp_path):
    """`der_c`'s `init_down` runs its base's through `super;` (a = 2) and then
    reads what that wrote (b = 2); the inherited `init_up` runs after the
    whole of `init_down` (a = 12). LRM 20.1.2, 20.1.4."""
    import re
    stdout = _build_and_run(tmp_path, _INIT_MODEL, _NO_CTOR_TB)
    got = dict(re.findall(r"\b([ab])=\s*(\d+)", stdout))
    assert got == {"a": "12", "b": "2"}, stdout


_HOIST_MODEL = """
import std_pkg::*;

component pss_top {
  target function int f(int x) {
    message(NONE, "f %d", x);
    return x + 1;
  }

  target function bool t(bool v) {
    message(NONE, "t %n", v);
    return v;
  }

  target function void run() {
    int a = f(1) + f(10);
    message(NONE, "a=%d", a);
    bool b1 = t(false) && t(true);
    bool b2 = t(true) || t(false);
    message(NONE, "b1=%n b2=%n", b1, b2);
    int c = (a > 5) ? f(100) : f(200);
    message(NONE, "c=%d", c);
    int n = 0;
    while (f(n) < 4) {
      n += 1;
    }
    message(NONE, "n=%d", n);
    int k = 0;
    repeat {
      k += 1;
      if (k < 3) {
        continue;
      }
    } while (f(k) < 5);
    message(NONE, "k=%d", k);
    int d = f(f(5));
    message(NONE, "d=%d", d);
  }
}
"""

#: What the PSS means. The calls a naive hoist would get wrong are the ones
#: that must NOT appear (`t true` after `t false &&`, `f 200`) and the ones
#: that must appear once per iteration (the loop conditions).
_HOIST_EXPECTED = [
    "f 1", "f 10", "a=13",
    "t false", "t true", "b1=false b2=true",   # && and || stop at the left
    "f 100", "c=101",                          # only the chosen arm runs
    "f 0", "f 1", "f 2", "f 3", "n=3",         # the condition, every iteration
    "f 1", "f 2", "f 3", "f 4", "k=4",         # `continue` still reaches the test
    "f 5", "f 6", "d=7",                       # arguments first
]


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_blocking_calls_are_hoisted_without_changing_what_runs(tmp_path):
    """A value-returning target function is a task; inside an expression it is
    hoisted to a temporary (SV-2) -- without running a call the PSS would not,
    or running a loop condition's call only once."""
    stdout = _build_and_run(tmp_path, _HOIST_MODEL, _NO_CTOR_TB)
    got = [ln for ln in stdout.splitlines()
           if ln and not ln.startswith(("-", " "))]
    assert got == _HOIST_EXPECTED, stdout


_PARAM_MODEL = """
import std_pkg::*;

struct cfg_s {
  int mode;
  int prio = 3;
}

function int weight(const cfg_s c) {
  return c.mode * 10 + c.prio;
}

function void bump(cfg_s c) {
  c.mode += 1;
}

component pss_top {
  target function void configure(const cfg_s c) {
    message(NONE, "configure mode=%d prio=%d", c.mode, c.prio);
  }

  target function void run() {
    configure({.mode = 2});
    int w = weight({.mode = 4, .prio = 1});
    message(NONE, "w=%d", w);
    cfg_s c;
    c.mode = 7;
    bump(c);
    message(NONE, "mode=%d", c.mode);
  }
}
"""


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_const_parameters_take_literals_and_others_are_handles(tmp_path):
    """A `const` struct parameter (LRM 20.2.3) is an SV `input`: it takes an
    aggregate literal, whose unnamed fields have their defaults (`prio = 3`).
    A non-const one is the caller's instance (20.3.2): `bump` changes `c`."""
    stdout = _build_and_run(tmp_path, _PARAM_MODEL, _NO_CTOR_TB)
    got = [ln for ln in stdout.splitlines()
           if ln and not ln.startswith(("-", " "))]
    assert got == ["configure mode=2 prio=3", "w=41", "mode=8"], stdout
