"""Register-model lowering on the ``sv-native`` target (addr_reg_pkg).

Fixtures cover the repro from the pss-registers-up labs (scalar ``reg_c`` with
``read_val``/``write_val``), a struct-typed register (``read``/``write``),
groups nested in groups, and >=2 instances of one group type (ch0..ch3).

Each case compiles with ``pssc compile -t sv-native`` and lints the output with
Verilator. xfail until register lowering lands (outline items 2-5).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ._verilator import HAVE_VERILATOR, VERILATOR

_SRC = str(Path(__file__).resolve().parents[3] / "src")

_SCALAR = """import addr_reg_pkg::*;
import std_pkg::*;
pure component my_regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> r0;
  reg_c<bit[32], READWRITE, 32> r1;
  function bit[64] get_offset_of_instance(string name) { if (name == "r1") { return 4; } return 0; }
}
component pss_top {
  my_regs_c regs;
  exec init_down { regs.set_handle(0x1000); }
  action wr_rd {
    bit[32] v;
    exec body {
      comp.regs.r0.write_val(0x5);
      v = comp.regs.r0.read_val();
      if (v != 5) { error("mismatch"); }
    }
  }
}
"""

_STRUCT = """import addr_reg_pkg::*;
import std_pkg::*;
struct ctrl_s : packed_s<> {
  bit[1]  en;
  bit[7]  mode;
  bit[24] count;
}
pure component ctrl_r : reg_c<ctrl_s, READWRITE, 32> {}
pure component my_regs_c : reg_group_c {
  ctrl_r ctrl;
  reg_c<bit[32], READONLY, 32> status;
  function bit[64] get_offset_of_instance(string name) { if (name == "status") { return 4; } return 0; }
}
component pss_top {
  my_regs_c regs;
  exec init_down { regs.set_handle(0x2000); }
  action wr_rd {
    ctrl_s c;
    ctrl_s r;
    exec body {
      c.en = 1; c.mode = 3; c.count = 100;
      comp.regs.ctrl.write(c);
      r = comp.regs.ctrl.read();
      if (r.count != 100 || r.mode != 3 || r.en != 1) { error("mismatch"); }
    }
  }
}
"""

_NESTED = """import addr_reg_pkg::*;
import std_pkg::*;
pure component inner_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> a;
  reg_c<bit[32], READWRITE, 32> b;
  function bit[64] get_offset_of_instance(string name) { if (name == "b") { return 4; } return 0; }
}
pure component outer_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> top_r;
  inner_c sub;
  function bit[64] get_offset_of_instance(string name) { if (name == "sub") { return 0x100; } return 0; }
}
component pss_top {
  outer_c regs;
  exec init_down { regs.set_handle(0x3000); }
  action wr_rd {
    bit[32] v;
    exec body {
      comp.regs.sub.b.write_val(0x77);
      comp.regs.top_r.write_val(0x11);
      v = comp.regs.sub.b.read_val();
      if (v != 0x77) { error("mismatch"); }
    }
  }
}
"""

_CHANNELS = """import addr_reg_pkg::*;
import std_pkg::*;
pure component ch_regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> csr;
  reg_c<bit[32], READWRITE, 32> sz;
  function bit[64] get_offset_of_instance(string name) { if (name == "sz") { return 4; } return 0; }
}
pure component dma_regs_c : reg_group_c {
  ch_regs_c ch0;
  ch_regs_c ch1;
  ch_regs_c ch2;
  ch_regs_c ch3;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "ch1") { return 0x20; }
    if (name == "ch2") { return 0x40; }
    if (name == "ch3") { return 0x60; }
    return 0;
  }
}
component pss_top {
  dma_regs_c regs;
  exec init_down { regs.set_handle(0x4000); }
  action wr_rd {
    bit[32] v;
    exec body {
      comp.regs.ch0.sz.write_val(1);
      comp.regs.ch3.sz.write_val(4);
      v = comp.regs.ch3.sz.read_val();
      if (v != 4) { error("mismatch"); }
      v = comp.regs.ch0.sz.read_val();
      if (v != 1) { error("mismatch"); }
    }
  }
}
"""

CASES = {
    "scalar": _SCALAR,
    "struct": _STRUCT,
    "nested": _NESTED,
    "channels": _CHANNELS,
}


def _compile(tmp_path, src):
    pss = tmp_path / "r.pss"
    pss.write_text(src)
    out = tmp_path / "out"
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    res = subprocess.run(
        [sys.executable, "-m", "pssc", "compile", "-t", "sv-native", "-q",
         "--export-action", "wr_rd", "-o", str(out), str(pss)],
        capture_output=True, text=True, env=env, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
    return out


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not on PATH")
@pytest.mark.xfail(reason="sv-native register lowering not implemented",
                   strict=True)
@pytest.mark.parametrize("case", sorted(CASES))
def test_reg_model_lints(tmp_path, case):
    out = _compile(tmp_path, CASES[case])
    res = subprocess.run(
        [VERILATOR, "--lint-only", "--timing", "-Wno-fatal", "-Wno-lint",
         "-Wno-style", "zsp_rt_pkg.sv", "zsp_gen_pkg.sv"],
        cwd=str(out), capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
