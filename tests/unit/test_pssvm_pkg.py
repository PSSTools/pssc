"""pssvm_pkg (P3.3b): the SV engine package and `pssc sv-pssvm-path`.

The run loop is checked under Verilator with a scripted SV engine (no
pyhdl-if): ACCESS writes/reads go to the platform, IMPORT to
`call_import`, WAIT TIME consumes sim time, WAIT EVENT waits for `notify`,
an engine error is a located failure that leaves the simulation running.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pssc import cli

ROOT = Path(__file__).resolve().parents[2]


def _verilator():
    v = shutil.which("verilator")
    if v:
        return v
    v = ROOT / "packages" / "verilator" / "bin" / "verilator"
    return str(v) if v.exists() else None


VERILATOR = _verilator()
needs_verilator = pytest.mark.skipif(VERILATOR is None, reason="verilator not found")


def _env():
    env = dict(os.environ)
    env["PATH"] = str(Path(VERILATOR).parent) + os.pathsep + env.get("PATH", "")
    return env


def test_sv_pssvm_path(capsys):
    assert cli.main(["sv-pssvm-path"]) == 0
    d = Path(capsys.readouterr().out.strip())
    assert d.name == "pssvm" and d.is_dir()
    assert cli.main(["sv-pssvm-path", "--file"]) == 0
    f = Path(capsys.readouterr().out.strip())
    assert f == d / "pssvm_pkg.sv" and f.is_file()


@needs_verilator
@pytest.mark.parametrize("defines", [[], ["+define+PSSVM_DPI"]], ids=["pyhdl", "dpi"])
def test_lint(defines):
    r = subprocess.run(
        [VERILATOR, "--lint-only", "--timing", *defines, str(cli.sv_pssvm_dir() / cli.SV_PSSVM_PKG)],
        capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stderr
    assert "%Warning" not in r.stderr and "%Error" not in r.stderr, r.stderr


# A scripted engine: each call replays a list of (status, req fields) and
# records what `complete` got.
TB = r"""
`timescale 1ns/1ns
module tb;
  import pssvm_pkg::*;

  class step_t;
    pssvm_status_e st;
    bit w; longint unsigned addr; int size; longint unsigned data;
    string name; pssvm_wait_on_e on; longint unsigned delay;
  endclass

  class script_engine extends pssvm_engine;
    step_t steps[$];
    int pos;
    longint unsigned got[$];
    longint unsigned notified_v;
    function void add(pssvm_status_e st, bit w = 0, longint unsigned addr = 0,
                      int size = 4, longint unsigned data = 0, string name = "",
                      pssvm_wait_on_e on = PSSVM_WAIT_TIME, longint unsigned delay = 0);
      step_t s = new;
      s.st = st; s.w = w; s.addr = addr; s.size = size; s.data = data;
      s.name = name; s.on = on; s.delay = delay;
      steps.push_back(s);
    endfunction
    virtual function int call_start(string entry, string comp_path, longint unsigned args[$]);
      if (entry == "bad") return 0;
      pos = -1;
      return 1;
    endfunction
    virtual function pssvm_status_e run(int h);
      pos++;
      return steps[pos].st;
    endfunction
    virtual function void request(int h, pssvm_req req);
      step_t s = steps[pos];
      req.status = s.st; req.write = s.w; req.addr = s.addr; req.size = s.size;
      req.data = s.data; req.name = s.name; req.on = s.on; req.delay = s.delay;
      req.args.delete(); req.args.push_back(7);
      req.blocking = 1;
    endfunction
    virtual function int complete(int h, longint unsigned data);
      got.push_back(data);
      return 0;
    endfunction
    virtual function longint unsigned result(int h); return 64'h42; endfunction
    virtual function void call_end(int h); endfunction
    virtual function int notify(int kind, int id, longint unsigned value);
      notified_v = value;
      return 0;
    endfunction
    virtual function string error(int h);
      return h == 0 ? "{\"message\": \"no entry\"}" : "{\"message\": \"boom\", \"line\": 3}";
    endfunction
  endclass

  class mem_plat extends pssvm_platform_if;
    bit [31:0] mem[longint unsigned];
    virtual task write32(longint unsigned addr, bit [31:0] data);
      #10; mem[addr] = data;
    endtask
    virtual task read32(longint unsigned addr, output bit [31:0] data);
      #10; data = mem.exists(addr) ? mem[addr] : 32'h0;
    endtask
    virtual task call_import(pssvm_req req, output longint unsigned ret);
      ret = req.args[0] + 1;
    endtask
  endclass

  initial begin
    script_engine eng = new;
    mem_plat plat = new;
    pssvm_ctxt ctxt = new(eng, plat);
    longint unsigned ret;
    bit ok;
    longint unsigned none[$];
    eng.add(PSSVM_ACCESS, 1, 'h100, 4, 'hcafe);
    eng.add(PSSVM_ACCESS, 0, 'h100, 4);
    eng.add(PSSVM_IMPORT, .name("tb_fn"));
    eng.add(PSSVM_WAIT, .on(PSSVM_WAIT_TIME), .delay(100));
    eng.add(PSSVM_WAIT, .on(PSSVM_WAIT_EVENT));
    eng.add(PSSVM_DONE);
    fork
      ctxt.call("dev_c::op", "top.dev", none, ret, ok);
      begin #500; ctxt.notify(1, 2, 3); end
    join
    $display("RUN ok=%0d ret=%0h t=%0t got=%p", ok, ret, $time, eng.got);
    eng.steps.delete();
    eng.add(PSSVM_ERROR);
    ctxt.call("dev_c::op", "top.dev", none, ret, ok);
    $display("ERR ok=%0d n=%0d last=%s", ok, ctxt.n_errors, ctxt.last_error);
    ctxt.call("bad", "", none, ret, ok);
    $display("BAD ok=%0d n=%0d last=%s", ok, ctxt.n_errors, ctxt.last_error);
    $display("ALIVE");
    $finish;
  end
endmodule
"""


@needs_verilator
def test_run_loop(tmp_path):
    (tmp_path / "tb.sv").write_text(TB)
    pkg = cli.sv_pssvm_dir() / cli.SV_PSSVM_PKG
    r = subprocess.run(
        [VERILATOR, "--binary", "--timing", "-Wno-fatal", "-o", "simv", "--Mdir", "obj",
         str(pkg), "tb.sv", "--top-module", "tb"],
        cwd=tmp_path, capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stderr[-3000:]
    r = subprocess.run([str(tmp_path / "obj" / "simv")], cwd=tmp_path,
                       capture_output=True, text=True, timeout=60)
    out = r.stdout
    assert r.returncode == 0, out + r.stderr
    # write 'hcafe (completed with 0), read it back, import 7 -> 8, then the
    # 100 ns wait, and the event wait released by notify at t=500.
    assert "RUN ok=1 ret=42 t=500 got='{'h0, 'hcafe, 'h8}" in out, out
    assert "ERR ok=0 n=1 last={\"message\": \"boom\", \"line\": 3}" in out, out
    assert "BAD ok=0 n=2 last={\"message\": \"no entry\"}" in out, out
    assert "PSSVM-ERROR: dev_c::op:" in out
    assert "ALIVE" in out
