"""Address spaces, claims and addr_handle read/write on the ``sv-native`` target.

Fixture ``addr_claims_a.pss`` (from the pss-registers-up labs): a component
``transparent_addr_space_c`` populated in ``exec init_down``, an action with two
``transparent_addr_claim_s`` claims, and ``write32``/``read32`` through a handle
made from one claim.  Memory accesses go through the ``import_api_if`` hooks
``mem_write``/``mem_read``.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ._verilator import HAVE_VERILATOR, VERILATOR

_SRC = str(Path(__file__).resolve().parents[3] / "src")
_PSS = Path(__file__).resolve().parent / "addr_claims_a.pss"

N_SEEDS = 20
REGION = (0x0, 0x1000)
CLAIM_SIZE = 64


def _compile(tmp_path):
    out = tmp_path / "out"
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    res = subprocess.run(
        [sys.executable, "-m", "pssc", "compile", "-t", "sv-native", "-q",
         "--export-action", "entry_a", "-o", str(out), str(_PSS)],
        capture_output=True, text=True, env=env, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
    return out


@pytest.mark.xfail(strict=True, reason="addr claims not lowered in sv-native yet")
@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not on PATH")
def test_addr_claims_lints(tmp_path):
    out = _compile(tmp_path)
    srcs = (out / "zsp_filelist.f").read_text().split()
    res = subprocess.run(
        [VERILATOR, "--lint-only", "--timing", "-Wno-fatal", "-Wno-lint",
         "-Wno-style"] + srcs,
        cwd=str(out), capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "add_region" in gen, "init_down add_region not emitted"
    assert "make_handle_from_claim" not in gen, "handle ops emitted verbatim"


# Simulation: a stub import_api_if models memory as an associative array and
# traces each access; the action's message line reports the claim addresses.
_TB = """class mem_stub extends zsp_gen_pkg::import_api_base;
  bit [63:0] mem[bit [63:0]];
  virtual task mem_write(input bit [63:0] addr, input bit [63:0] data, input int size);
    $display("MEM_WRITE 0x%0h 0x%0h %0d", addr, data, size);
    mem[addr] = data;
  endtask
  virtual task mem_read(input bit [63:0] addr, output bit [63:0] data, input int size);
    data = mem.exists(addr) ? mem[addr] : 0;
    $display("MEM_READ 0x%0h 0x%0h %0d", addr, data, size);
  endtask
endclass
module tb;
  initial begin
    mem_stub stub = new();
    zsp_gen_pkg::factory_if f = zsp_gen_pkg::pss_top::type_id();
    zsp_gen_pkg::export_api_if api = f.create(stub);
    api.entry_a();
    $display("TB_DONE");
    $finish;
  end
endmodule
"""

_MSG = re.compile(r"src (\d+) dst (\d+) v (\d+)")
_ACC = re.compile(r"^(MEM_WRITE|MEM_READ) 0x([0-9a-f]+) 0x([0-9a-f]+) (\d+)")


@pytest.mark.xfail(strict=True, reason="addr claims not lowered in sv-native yet")
@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not on PATH")
def test_addr_claims_sim_verilator(tmp_path):
    out = _compile(tmp_path)
    srcs = (out / "zsp_filelist.f").read_text().split()
    (out / "tb.sv").write_text(_TB)
    res = subprocess.run(
        [VERILATOR, "--binary", "--timing", "-Wno-fatal", "-Wno-lint",
         "-Wno-style", "--top-module", "tb", "-Mdir", "obj"] + srcs + ["tb.sv"],
        cwd=str(out), capture_output=True, text=True, timeout=600)
    assert res.returncode == 0, res.stdout + res.stderr
    for seed in range(1, N_SEEDS + 1):
        res = subprocess.run(
            [str(out / "obj" / "Vtb"), f"+verilator+seed+{seed}"],
            cwd=str(out), capture_output=True, text=True, timeout=60)
        log = res.stdout + res.stderr
        assert res.returncode == 0 and "TB_DONE" in log, log
        m = _MSG.search(log)
        assert m, log
        src, dst, v = (int(x) for x in m.groups())
        for a in (src, dst):
            assert a % 4 == 0, f"seed {seed}: 0x{a:x} not 4-aligned\n{log}"
            assert REGION[0] <= a and a + CLAIM_SIZE <= REGION[1], \
                f"seed {seed}: 0x{a:x} outside region\n{log}"
        assert src + CLAIM_SIZE <= dst or dst + CLAIM_SIZE <= src, \
            f"seed {seed}: claims overlap (0x{src:x}, 0x{dst:x})\n{log}"
        acc = [_ACC.match(l.strip()).groups() for l in log.splitlines()
               if _ACC.match(l.strip())]
        assert acc == [("MEM_WRITE", f"{src:x}", "deadbeef", "4"),
                       ("MEM_READ", f"{src:x}", "deadbeef", "4")], log
        assert v == 0xdeadbeef, log
