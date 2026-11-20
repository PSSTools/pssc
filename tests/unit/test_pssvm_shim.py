"""pssvm_dpi_shim (P3.3c): pyvm behind the host ABI, for pyhdl-if.

`Engine` is checked in Python on the WB DMA image (no simulator); the SV
side (`pssvm_hif_pkg::pssvm_engine_hif` under `pssvm_pkg::pssvm_ctxt`) runs
the same tests under Verilator with an assoc-array memory as the bus.
"""
import json
import re
import time
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pssc import cli
from pssc.devenv import build_cmd

ROOT = Path(__file__).resolve().parents[2]
SHIM_DIR = ROOT / "src" / "pssc" / "share" / "py"
WB_TESTS = ROOT / "examples" / "op_model" / "tests"

sys.path.insert(0, str(SHIM_DIR))
import pssvm_dpi_shim as shim  # noqa: E402


def _verilator():
    v = shutil.which("verilator")
    if v:
        return v
    v = ROOT / "packages" / "verilator" / "bin" / "verilator"
    return str(v) if v.exists() else None


VERILATOR = _verilator()
try:
    import hdl_if
except ImportError:  # pragma: no cover
    hdl_if = None
needs_sim = pytest.mark.skipif(VERILATOR is None or hdl_if is None,
                               reason="verilator or pyhdl-if not found")


def _env():
    env = dict(os.environ)
    if VERILATOR:
        env["PATH"] = str(Path(VERILATOR).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = cli.sv_pssvm_pythonpath()
    env["PSSC_NO_DAEMON"] = "1"
    return env


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    out = tmp_path_factory.mktemp("img") / "wb.zbc"
    r = subprocess.run(
        [sys.executable, "-m", "pssc", "build", "@model.f", ".", "--root", "wb_dma_fx_c",
         "--image", str(out)],
        cwd=WB_TESTS, capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def _call(eng, entry, mem):
    """Drive one call the way pssvm_ctxt.call does; memory is a dict."""
    h = eng.call_start(entry, "")
    assert h > 0, eng.error(0)
    seen = []
    while True:
        st = eng.run(h)
        if st == 0:
            ret = eng.result(h)
            eng.call_end(h)
            return ret, seen
        assert st == 1, (st, eng.error(h))
        addr = eng.req_u64(h, "addr") & ((1 << 64) - 1)
        if eng.req_int(h, "kind"):
            mem[addr] = eng.req_u64(h, "data")
            seen.append(("w", addr))
            eng.complete(h, 0)
        else:
            seen.append(("r", addr))
            eng.complete(h, mem.get(addr, 0))


def test_engine_runs_test(image):
    eng = shim.Engine()
    assert eng.load(str(image), "", "fake") == 0
    mem = {}
    _call(eng, "wb_dma_fx_c::bind_dma", mem)
    _, seen = _call(eng, "wb_dma_fx_c::t_csr_prio_bits", mem)
    assert ("r", 0x10000080) in seen and ("w", 0x10000080) in seen, seen
    assert eng.checks_failed() == 0


def test_engine_lists_tests(image):
    eng = shim.Engine()
    assert eng.load(str(image), "", "fake") == 0
    names = []
    while eng.test_name(len(names)):
        names.append(eng.test_name(len(names)))
    assert "wb_dma_fx_c::t_csr_prio_bits" in names, names
    assert all(n.startswith("wb_dma_fx_c::t_") for n in names), names   # functions only
    assert eng.test_expect(names.index("wb_dma_fx_c::t_forbidden_mask")) == "fail_on_access"
    assert eng.test_expect(names.index("wb_dma_fx_c::t_csr_prio_bits")) == ""
    assert [eng.hook("setup", 0), eng.hook("setup", 1)] == ["wb_dma_fx_c::bind_dma", ""]
    assert eng.hook("teardown", 0) == "" and eng.test_name(-1) == ""


def test_engine_errors(image):
    eng = shim.Engine()
    assert eng.load(str(image), "", "") == 0
    assert eng.call_start("wb_dma_fx_c::nope", "") == 0
    assert "message" in json.loads(eng.error(0))
    # No platform double: base() cannot be served -> located ERROR.
    h = eng.call_start("wb_dma_fx_c::bind_dma", "")
    assert h > 0
    assert eng.run(h) == -1
    assert json.loads(eng.error(h))["message"]
    assert shim.Engine().load(str(image.parent / "missing.zbc"), "", "") == -1


def test_sv_pssvm_path_py(capsys):
    assert cli.main(["sv-pssvm-path", "--py"]) == 0
    parts = capsys.readouterr().out.strip().split(os.pathsep)
    assert Path(parts[0]) == ROOT / "src"
    assert (Path(parts[1]) / "pssvm_dpi_shim.py").is_file()
    for f in ("pssvm_hif_pkg.sv", "pssvm_hif_api_pkg.sv"):
        assert (cli.sv_pssvm_dir() / f).is_file()


def test_generated_api_pkg_is_current(tmp_path):
    if hdl_if is None:
        pytest.skip("pyhdl-if not found")
    r = subprocess.run(
        [sys.executable, "-m", "hdl_if", "api-gen-sv", "-m", "pssvm_dpi_shim",
         "-p", "pssvm_hif_api_pkg", "-o", str(tmp_path / "api.sv")],
        capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stderr
    shipped = cli.sv_pssvm_dir() / "pssvm_hif_api_pkg.sv"
    assert (tmp_path / "api.sv").read_text() == shipped.read_text()


TB = r"""
`timescale 1ns/1ns
module tb;
  import pssvm_pkg::*;
  import pssvm_hif_pkg::*;

  class mem_plat extends pssvm_platform_if;
    bit [31:0] mem[longint unsigned];
    int n;
    virtual task write32(longint unsigned addr, bit [31:0] data);
      #10; mem[addr] = data; n++;
    endtask
    virtual task read32(longint unsigned addr, output bit [31:0] data);
      #10; data = mem.exists(addr) ? mem[addr] : 32'h0; n++;
    endtask
  endclass

  initial begin
    pssvm_engine_hif eng;
    mem_plat plat = new;
    pssvm_ctxt ctxt;
    longint unsigned ret;
    bit ok;
    longint unsigned none[$];
    string img;
    void'($value$plusargs("img=%s", img));
    eng = pssvm_engine_hif::create(img, "", "fake");
    ctxt = new(eng, plat);
    ctxt.call("wb_dma_fx_c::bind_dma", "", none, ret, ok);
    $display("BIND ok=%0d", ok);
    ctxt.call("wb_dma_fx_c::t_csr_prio_bits", "", none, ret, ok);
    $display("TEST ok=%0d n=%0d t=%0t failed=%0d", ok, plat.n, $time, eng.checks_failed());
    ctxt.call("wb_dma_fx_c::nope", "", none, ret, ok);
    $display("BAD ok=%0d n=%0d", ok, ctxt.n_errors);
    $finish;
  end
endmodule
"""


RUNNER_TB = r"""
`timescale 1ns/1ns
module tb;
  import pssvm_pkg::*;
  import pssvm_hif_pkg::*;

  class mem_plat extends pssvm_platform_if;
    bit [31:0] mem[longint unsigned];
    virtual task write32(longint unsigned addr, bit [31:0] data);
      #10; mem[addr] = data;
    endtask
    virtual task read32(longint unsigned addr, output bit [31:0] data);
      #10; data = mem.exists(addr) ? mem[addr] : 32'h0;
    endtask
  endclass

  initial begin
    mem_plat plat = new;
    pssvm_ctxt ctxt;
    pssvm_runner runner;
    string img;
    int failed;
    void'($value$plusargs("img=%s", img));
    ctxt = new(pssvm_engine_hif::create(img, "", "fake"), plat);
    runner = new(ctxt);
    runner.run(failed);
    $display("FAILED=%0d", failed);
    $finish;
  end
endmodule
"""


def _build(tmp_path, tb):
    (tmp_path / "tb.sv").write_text(tb)
    d = cli.sv_pssvm_dir()
    share = Path(hdl_if.share())
    lib = Path(hdl_if.libs()[0]).parent
    r = subprocess.run(
        [VERILATOR, "--binary", "--timing", "-Wno-fatal", "-o", "simv", "--Mdir", "obj",
         f"+incdir+{share / 'dpi'}",
         "-LDFLAGS", f"-L{lib}", "-LDFLAGS", "-lpyhdl_if",
         "-LDFLAGS", f"-Wl,-rpath,{lib}", "-LDFLAGS", "-Wl,--export-dynamic",
         # pyhdl-if ahead of the model's archive, so its DPI exports link
         "-MAKEFLAGS", f"LDFLAGS=-Wl,--no-as-needed,-L{lib},-lpyhdl_if",
         str(share / "dpi" / "pyhdl_if.sv"), str(d / "pssvm_pkg.sv"),
         str(d / "pssvm_hif_api_pkg.sv"), str(d / "pssvm_hif_pkg.sv"),
         "tb.sv", "--top-module", "tb"],
        cwd=tmp_path, capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stderr[-3000:]


def _sim(tmp_path, *plusargs):
    r = subprocess.run([str(tmp_path / "obj" / "simv"), *plusargs], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120, env=_env())
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@needs_sim
def test_verilator_runner(tmp_path, image):
    _build(tmp_path, RUNNER_TB)
    out = _sim(tmp_path, f"+img={image}", "+pssunit=*prio*")
    assert "PSSVM-TEST: PASS wb_dma_fx_c::t_csr_prio_bits" in out, out
    assert out.count("PSSVM-TEST:") == 1, out
    assert "PSSVM-SUMMARY: 1 passed, 0 failed" in out and "FAILED=0" in out, out
    out = _sim(tmp_path, f"+img={image}", "+pssunit=t_csr_*,nope")
    assert out.count("PSSVM-TEST: PASS") == 2, out
    out = _sim(tmp_path, f"+img={image}")
    eng = shim.Engine()
    assert eng.load(str(image), "", "") == 0
    n = 0
    while eng.test_name(n):
        n += 1
    assert out.count("PSSVM-TEST:") == n, out
    assert "PSSVM-SUMMARY:" in out, out


@needs_sim
def test_verilator(tmp_path, image):
    _build(tmp_path, TB)
    r = subprocess.run([str(tmp_path / "obj" / "simv"), f"+img={image}"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120, env=_env())
    out = r.stdout
    assert r.returncode == 0, out + r.stderr
    assert "BIND ok=1" in out, out
    # one read and one write of the CSR, 10 ns each; the check passes.
    assert "TEST ok=1 n=2 t=20 failed=0" in out, out
    assert "BAD ok=0 n=1" in out, out


# ---- P4.9a: the C engine over DPI-C (no Python in the simulator) -----------

# TB on pssvm_engine_dpi; imports are logged by name (the image's IMPORT table)
DPI_TB = TB.replace("  import pssvm_hif_pkg::*;\n", "").replace(
    "pssvm_engine_hif eng;", "pssvm_engine_dpi eng;").replace(
    'eng = pssvm_engine_hif::create(img, "", "fake");', "eng = new(img);").replace(
    "  endclass\n", """    virtual task call_import(pssvm_req req, output longint unsigned ret);
      $display("IMPORT %s fn=%0d nargs=%0d", req.name, req.fn_id, req.args.size());
      ret = 0;
    endtask
  endclass
""", 1).replace("    $finish;", """    $display("FRESH rc=%0d", eng.fresh());
    $finish;""")


def _build_dpi(tmp_path, tb):
    """Build ``tb`` on the C engine's DPI library (no pyhdl-if)."""
    (tmp_path / "tb.sv").write_text(tb)
    r = subprocess.run([sys.executable, "-m", "pssc", "sv-pssvm-path", "--lib"],
                       capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stderr
    lib = Path(r.stdout.strip())
    r = subprocess.run(
        [VERILATOR, "--binary", "--timing", "-Wno-fatal", "-o", "simv", "--Mdir", "obj",
         "+define+PSSVM_DPI", str(cli.sv_pssvm_dir() / "pssvm_pkg.sv"), "tb.sv",
         "--top-module", "tb", "-LDFLAGS", str(lib),
         "-LDFLAGS", f"-Wl,-rpath,{lib.parent}"],
        cwd=tmp_path, capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stderr[-3000:]


def _sim_dpi(tmp_path, *plusargs):
    env = _env()
    env.pop("PYTHONPATH")      # no Python in the simulation
    r = subprocess.run([str(tmp_path / "obj" / "simv"), *plusargs], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_dpi_native(tmp_path, image):
    _build_dpi(tmp_path, DPI_TB)
    out = _sim_dpi(tmp_path, f"+img={image}")
    # as the pyvm engine (test_verilator): one CSR read and write, 10 ns each
    assert "BIND ok=1" in out, out
    assert "TEST ok=1 n=2 t=20 failed=0" in out, out
    assert "BAD ok=0 n=1" in out and "no entry named 'wb_dma_fx_c::nope'" in out, out
    # the imports bind_dma and the test make, with their arguments (the fn
    # id is the import's index, which moves when pssunit_pkg gains imports)
    assert re.search(r"IMPORT double_handle fn=\d+ nargs=1", out), out
    assert re.search(r"IMPORT check_operand fn=\d+ nargs=1", out), out
    assert "FRESH rc=0" in out, out


# RUNNER_TB on the C engine: the image's test list and check ledger are read
# from its plain-text META document (build_cmd.NATIVE)
RUNNER_DPI_TB = RUNNER_TB.replace("  import pssvm_hif_pkg::*;\n", "").replace(
    "    int failed;\n", "    int failed;\n    pssvm_engine_dpi eng;\n").replace(
    'ctxt = new(pssvm_engine_hif::create(img, "", "fake"), plat);',
    "eng = new(img);\n    ctxt = new(eng, plat);")

FAIL_PSS = """\
import std_pkg::*;
import pssunit_pkg::*;
component c_x : fixture_c {
    rt_c rt;
    @pssunit_pkg::test
    function void t_ok() { rt.check_eq_int(1, 1, "one"); }
    @pssunit_pkg::test
    function void t_bad() { rt.check_eq_int(1, 2, "two"); }
}
"""


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_runner_dpi_native(tmp_path, image):
    _build_dpi(tmp_path, RUNNER_DPI_TB)
    out = _sim_dpi(tmp_path, f"+img={image}", "+pssunit=*prio*")
    assert "PSSVM-TEST: PASS wb_dma_fx_c::t_csr_prio_bits" in out, out
    assert out.count("PSSVM-TEST:") == 1, out
    assert "PSSVM-SUMMARY: 1 passed, 0 failed" in out and "FAILED=0" in out, out
    # every test of the image, as build_cmd lists them for the C engine
    out = _sim_dpi(tmp_path, f"+img={image}")
    model, _ = build_cmd.read(str(image))
    n = model.meta[build_cmd.NATIVE].count("test\t")
    assert n > 1 and out.count("PSSVM-TEST:") == n, out
    # a failing check is counted against its test
    bad = _image(tmp_path, "bad", FAIL_PSS, "c_x")
    out = _sim_dpi(tmp_path, f"+img={bad}")
    assert "PSSVM-TEST: PASS c_x::t_ok" in out, out
    assert "PSSVM-TEST: FAIL c_x::t_bad" in out, out
    assert "PSSVM-SUMMARY: 1 passed, 1 failed" in out and "FAILED=1" in out, out


# ---- P3.8b: a fresh tree and a reset between tests (T3.8) ------------------

FRESH_PSS = """\
import std_pkg::*;
import pssunit_pkg::*;
component c_f : fixture_c {
    rt_c rt;
    int cnt;
    @pssunit_pkg::test
    function void t_a() { cnt = cnt + 1; rt.check_eq_int(cnt, 1, "clean"); }
    @pssunit_pkg::test
    function void t_b() { cnt = cnt + 1; rt.check_eq_int(cnt, 1, "clean"); }
    @pssunit_pkg::test
    function void t_c() { cnt = cnt + 1; rt.check_eq_int(cnt, 1, "clean"); }
}
"""

SESSION_PSS = """\
import std_pkg::*;
import pssunit_pkg::*;
component s_f : fixture_c {
    rt_c rt;
    int booted;
    int cnt;
    @pssunit_pkg::session
    function void boot() { booted = booted + 1; }
    @pssunit_pkg::test
    function void t_a() { cnt = cnt + 1; rt.check_eq_int(booted, 1, "booted"); }
    @pssunit_pkg::test
    function void t_b() { cnt = cnt + 1; rt.check_eq_int(cnt, 2, "shared"); }
}
"""

# RUNNER_TB with a platform that counts its resets (`+no_reset`: can_reset 0)
RESET_TB = RUNNER_TB.replace("""  endclass
""", """    int n_reset;
    bit no_reset = $test$plusargs("no_reset");
    virtual task reset();
      #5; n_reset++;
    endtask
    virtual function bit can_reset();
      return !no_reset;
    endfunction
  endclass
""", 1).replace("""    $display("FAILED=%0d", failed);""",
                """    $display("FAILED=%0d RESETS=%0d", failed, plat.n_reset);""")


def _image(tmp_path, name, text, root):
    (tmp_path / f"{name}.pss").write_text(text)
    out = tmp_path / f"{name}.zbc"
    r = subprocess.run(
        [sys.executable, "-m", "pssc", "build", f"{name}.pss", "--root", root,
         "--image", str(out)],
        cwd=tmp_path, capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stdout + r.stderr
    return out


@needs_sim
def test_verilator_fresh_tree_and_reset_between_tests(tmp_path):
    fresh = _image(tmp_path, "fresh", FRESH_PSS, "c_f")
    sess = _image(tmp_path, "sess", SESSION_PSS, "s_f")
    _build(tmp_path, RESET_TB)
    # every test on a clean model, the platform reset before each but the first
    out = _sim(tmp_path, f"+img={fresh}")
    assert out.count("PSSVM-TEST: PASS") == 3, out
    assert "FAILED=0 RESETS=2" in out, out
    assert "PSSVM-SUMMARY: 3 passed, 0 failed\n" in out, out
    # a platform that cannot reset: one test per simulation
    out = _sim(tmp_path, f"+img={fresh}", "+no_reset", "+pssunit=t_b,t_c")
    assert "PSSVM-TEST: PASS c_f::t_b" in out, out
    assert ("PSSVM-TEST: SKIP c_f::t_c (platform cannot reset: one test per sim)"
            in out), out
    assert "PSSVM-SUMMARY: 1 passed, 0 failed, 1 skipped" in out, out
    assert "FAILED=0 RESETS=0" in out, out
    # session functions: run once, then the tests share model and design
    out = _sim(tmp_path, f"+img={sess}")
    assert out.count("PSSVM-TEST: PASS") == 2, out
    assert "FAILED=0 RESETS=0" in out, out
    # ... even on a platform that cannot reset
    out = _sim(tmp_path, f"+img={sess}", "+no_reset")
    assert out.count("PSSVM-TEST: PASS") == 2 and "SKIP" not in out, out


def test_engine_fresh_rebuilds_the_tree(tmp_path):
    eng = shim.Engine()
    assert eng.load(str(_image(tmp_path, "fresh", FRESH_PSS, "c_f")), "", "") == 0
    assert eng.hook("session", 0) == ""
    for i in range(2):
        h = eng.call_start("c_f::t_a", "")
        assert eng.run(h) == 0, eng.error(h) or eng.error(0)
        eng.call_end(h)
        assert eng.checks_failed() == i      # the second run sees cnt == 2
    assert eng.fresh() == 0
    assert eng.checks_failed() == 0
    h = eng.call_start("c_f::t_a", "")
    assert eng.run(h) == 0
    eng.call_end(h)
    assert eng.checks_failed() == 0


# ---- P4.9d2: server mode on the C engine (no Python in the simulator) -------

def _channel():
    """test_devenv_sim_channel's daemon fixture and helpers."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_devenv_sim_channel as ch
    return ch


@pytest.fixture
def pssd():
    """A daemon serving in a thread on a short /tmp socket."""
    import tempfile
    import threading
    from pssc.devenv import daemon
    d = tempfile.mkdtemp(prefix="pssd", dir="/tmp")    # AF_UNIX path limit
    dm = daemon.Daemon(d, os.path.join(d, "w.sock"), idle=60)
    t = threading.Thread(target=dm.serve, daemon=True)
    t.start()
    _channel()._wait(lambda: os.path.exists(dm.sim_path))
    yield dm
    dm._stop = True
    t.join(5)
    shutil.rmtree(d, ignore_errors=True)


def _serve_dpi(tmp_path, pssd, img, name):
    """RUNNER_DPI_TB serving as ``name`` on the daemon ``pssd``: (Popen, the
    registry's session)."""
    from pssc.devenv.sim.registry import REGISTRY
    ch = _channel()
    _build_dpi(tmp_path, RUNNER_DPI_TB)
    env = _env()
    env.pop("PYTHONPATH")      # no Python in the simulation
    env["PSSC_SIM_SOCKET"] = pssd.sim_path
    p = subprocess.Popen([str(tmp_path / "obj" / "simv"), f"+img={img}",
                          f"+pssvm_serve={name}"], cwd=tmp_path, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ch._wait(lambda: len(REGISTRY) == 1 or p.poll() is not None, 60)
    assert p.poll() is None, p.stdout.read()
    return p, REGISTRY.get(name)


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_serve_dpi_native(pssd, tmp_path, image, capsys):
    """As test_devenv_sim_channel's test_verilator_serve, on the C engine:
    register, ping/info, an unsupported command, runs with test events
    carrying sim time, end."""
    from pssc.devenv.sim.registry import REGISTRY
    ch = _channel()
    p, s = _serve_dpi(tmp_path, pssd, image, "tbN")
    try:
        (row,) = REGISTRY.list()
        assert row["name"] == "tbN" and row["pid"] == p.pid, row
        assert row["image"] == str(image), row
        assert s.request("ping")["body"] == {"pid": p.pid}
        info = s.request("info")["body"]
        assert info["name"] == "tbN" and info["protocol"] == 1, info
        assert info["cwd"] == str(tmp_path), info
        assert s.request("load")["error"]["code"] == "unsupported"
        assert s.request("resume")["error"]["message"].startswith("not held")
        r = ch._sim(capsys, pssd, "run", "*prio*", "--timeout", "60")
        assert r["passed"] == 1 and r["failed"] == 0, r
        (t1,) = r["tests"]
        assert (t1["name"], t1["status"]) == ("wb_dma_fx_c::t_csr_prio_bits", "pass"), r
        assert t1["t"] > 0, r
        r = ch._sim(capsys, pssd, "run", "t_csr_*", "nope", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (2, 0), r
        ev = ch._sim(capsys, pssd, "log")["events"]
        assert [(e["event"], e["body"]["status"]) for e in ev] == [("test", "pass")] * 3, ev
        assert ch._sim(capsys, pssd, "end")["ok"]
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert f"PSSVM-SERVE: {row['id']}" in out and "PSSVM-SERVE: end" in out, out
    assert out.count("PSSVM-TEST: PASS") == 3 and "FAILED=0" in out, out
    ch._wait(lambda: len(REGISTRY) == 0)


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_debug_dpi_native(pssd, tmp_path, capsys):
    """As test_devenv_sim_channel's test_shim_debug_stops_inside_a_call, on
    the C engine: ``debug`` puts the C stub on, attached with ``entry:
    false``; a breakpoint holds the testbench's call (and sim time) until
    ``continue``; ``debug`` off: the next run goes straight through."""
    ch = _channel()
    img = ch._rl_image(tmp_path, 4)
    image, _, _ = build_cmd.load(str(img))
    coro = image.model.entries["tests_c::t_v"].coro
    p, s = _serve_dpi(tmp_path, pssd, img, "tbG")
    try:
        assert s.request("debug", {"on": True})["body"]["image"] == str(img)
        r = s.request("attach", {"version": 1, "entry": False})
        assert r["ok"] and r["body"]["engine"] == "c", r
        r = s.request("set_pc_breaks", {"breaks": [{"id": 1, "coro": coro, "pc": 0}]})
        assert r["body"]["breaks"][0]["verified"], r
        t, res = ch._in_thread(lambda: ch._sim(capsys, pssd, "run", "t_v",
                                               "--timeout", "60"))
        ev = ch._next_stub_event(s, "stopped", 60)
        assert ev["body"]["reason"] == "breakpoint", ev
        assert "t" in ev["body"], ev
        time.sleep(0.3)
        assert t.is_alive() and p.poll() is None      # the call is held
        th = s.request("threads")
        assert th["ok"] and th["body"]["threads"], th
        assert s.request("continue")["ok"]
        t.join(60)
        assert (res["r"]["passed"], res["r"]["failed"]) == (0, 1), res
        assert s.request("debug", {"on": False})["ok"]
        r = ch._sim(capsys, pssd, "run", "t_v", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (0, 1), r
        assert ch._sim(capsys, pssd, "end")["ok"]
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    m = re.search(r"PSSVM-TEST: FAIL tests_c::t_v @(\d+)\.\.(\d+)", out)
    assert m and m.group(1) == m.group(2), out      # no sim time across the stop


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_reload_dpi_native(pssd, tmp_path, capsys):
    """As test_devenv_sim_channel's test_verilator_reload, on the C engine
    (P4.9e3): a failing test, a fix, ``pssc sim reload``, the same test
    passing in the same process; a tree change under ``--strict`` is refused
    and changes nothing, and without it is taken (P4.9e2)."""
    from pssc.devenv import sim_cmd
    ch = _channel()
    img = ch._rl_image(tmp_path, 4)
    p, s = _serve_dpi(tmp_path, pssd, img, "tbR")
    try:
        r = ch._sim(capsys, pssd, "run", "t_v", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (0, 1), r
        ch._rl_image(tmp_path, 5)
        assert sim_cmd.main(["reload", "--timeout", "60"], path=pssd.path) == 0
        out = capsys.readouterr().out
        assert "generation 1 -> 2" in out, out
        r = ch._sim(capsys, pssd, "run", "t_v", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (1, 0), r
        ch._rl_image(tmp_path, 5, more=True)
        r = ch._sim(capsys, pssd, "reload", str(img), "--strict", "--timeout", "60")
        assert not r["ok"] and r["refused"]["reason"] == "structure", r
        assert r["error"].startswith("reload refused (structure)"), r
        assert s.request("info")["body"]["image"] == str(img)
        r = ch._sim(capsys, pssd, "run", "t_v", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (1, 0), r     # still generation 2
        # without --strict the new tree is taken, state moving by path (P4.9e2)
        assert sim_cmd.main(["reload", "--timeout", "60"], path=pssd.path) == 0
        out = capsys.readouterr().out
        assert "generation 2 -> 3" in out, out
        r = ch._sim(capsys, pssd, "run", "t_v", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (1, 0), r
        assert ch._sim(capsys, pssd, "list")["sessions"][0]["pid"] == p.pid
        assert ch._sim(capsys, pssd, "end")["ok"]
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert "PSSVM-TEST: FAIL tests_c::t_v" in out, out
    assert out.count("PSSVM-TEST: PASS tests_c::t_v") == 3, out


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_dbg_on_fail_dpi_native(pssd, tmp_path, capsys):
    """As test_devenv_sim_channel's test_verilator_dbg_on_fail, on the C
    engine: a failing test holds the simulation (it registers, a `held`
    event, ``run`` refused); the debugger is put on while held and a
    breakpoint stops the rerun (``pssc sim resume --rerun``); a plain
    ``resume`` then lets the run go on, and the test counts once."""
    from pssc.devenv.sim.registry import REGISTRY
    ch = _channel()
    img = ch._rl_image(tmp_path, 4)
    image, _, _ = build_cmd.load(str(img))
    coro = image.model.entries["tests_c::t_v"].coro
    _build_dpi(tmp_path, RUNNER_DPI_TB)
    env = _env()
    env.pop("PYTHONPATH")      # no Python in the simulation
    env["PSSC_SIM_SOCKET"] = pssd.sim_path
    p = subprocess.Popen([str(tmp_path / "obj" / "simv"), f"+img={img}",
                          "+pssunit=t_v", "+pssvm_dbg_on_fail"], cwd=tmp_path,
                         env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        ch._wait(lambda: len(REGISTRY) == 1 or p.poll() is not None, 60)
        assert p.poll() is None, p.stdout.read()
        ch._wait(lambda: any(e["event"] == "held" for e in
                             ch._sim(capsys, pssd, "log")["events"]), 30)
        r = ch._sim(capsys, pssd, "run", "t_v", "--timeout", "10")
        assert not r["ok"] and "held" in r["error"], r
        (row,) = REGISTRY.list()
        s = REGISTRY.get(row["name"])
        assert s.request("debug", {"on": True})["ok"]
        r = s.request("attach", {"version": 1, "entry": False})
        assert r["ok"] and r["body"]["engine"] == "c", r
        r = s.request("set_pc_breaks", {"breaks": [{"id": 1, "coro": coro, "pc": 0}]})
        assert r["body"]["breaks"][0]["verified"], r
        r = ch._sim(capsys, pssd, "resume", "--rerun")
        assert r["ok"] and r["rerun"] and r["why"] == "tests_c::t_v", r
        ev = ch._next_stub_event(s, "stopped", 60)
        assert ev["body"]["reason"] == "breakpoint", ev
        assert s.request("continue")["ok"]
        ch._wait(lambda: sum(e["event"] == "held" for e in
                             ch._sim(capsys, pssd, "log")["events"]) == 2, 30)
        assert s.request("debug", {"on": False})["ok"]
        r = ch._sim(capsys, pssd, "resume")
        assert r["ok"] and not r["rerun"], r
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert out.count("PSSVM-DBG: held tests_c::t_v") == 2, out
    assert "PSSVM-SUMMARY: 0 passed, 1 failed" in out, out


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_attach_dpi_native(pssd, tmp_path, capsys):
    """As test_devenv_sim_channel's test_verilator_attach, on the C engine
    and through the CLI: ``pssc sim attach``, then ``pssc dbg break add
    FILE:LINE`` and ``continue`` stop the testbench's call at the line and
    hold sim time; ``pssc dbg end`` detaches."""
    from pssc.devenv.debug.sessions import REGISTRY as DBG
    ch = _channel()
    img = ch._rl_image(tmp_path, 4)
    p, _ = _serve_dpi(tmp_path, pssd, img, "tbA")
    try:
        sid = ch._sim(capsys, pssd, "attach")["dbg"]
        assert ch._dbg(capsys, "break", "add", f"{tmp_path / 'dut.pss'}:2",
                       "--session", sid)["breakpoint"]["verified"]
        t, res = ch._in_thread(lambda: ch._sim(capsys, pssd, "run", "t_v",
                                               "--timeout", "60"))
        stop = ch._dbg(capsys, "continue", "--session", sid, "--timeout", "30")
        assert (stop["event"], stop["reason"]) == ("stopped", "breakpoint"), stop
        time.sleep(0.5)                          # held: the sim waits
        assert t.is_alive() and p.poll() is None
        bt = ch._dbg(capsys, "bt", "--session", sid)
        assert bt["frames"], bt
        r = ch._dbg(capsys, "continue", "--session", sid, "--timeout", "1")
        assert r["event"] == "running", r
        t.join(60)
        assert (res["r"]["passed"], res["r"]["failed"]) == (0, 1), res
        assert ch._dbg(capsys, "end", "--session", sid)["ended"]
        assert ch._sim(capsys, pssd, "end")["ok"]
        out, _ = p.communicate(timeout=60)
    finally:
        DBG.end_all()
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    m = re.search(r"PSSVM-TEST: FAIL tests_c::t_v @(\d+)\.\.(\d+)", out)
    assert m and m.group(1) == m.group(2), out


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_dbg_start_dpi_native(pssd, tmp_path, capsys):
    """``+pssvm_dbg_start`` on the C engine, in a testbench that does not
    serve: the engine holds after loading (it registers, a `held` event);
    ``pssc sim attach``, a breakpoint and ``pssc sim resume``; the test's
    call then stops at the breakpoint with nobody polling the server, and
    ``pssc dbg continue`` lets it finish."""
    from pssc.devenv.debug.sessions import REGISTRY as DBG
    from pssc.devenv.sim.registry import REGISTRY
    ch = _channel()
    img = ch._rl_image(tmp_path, 4)
    _build_dpi(tmp_path, RUNNER_DPI_TB)
    env = _env()
    env.pop("PYTHONPATH")      # no Python in the simulation
    env["PSSC_SIM_SOCKET"] = pssd.sim_path
    p = subprocess.Popen([str(tmp_path / "obj" / "simv"), f"+img={img}",
                          "+pssunit=t_v", "+pssvm_dbg_start"], cwd=tmp_path,
                         env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        ch._wait(lambda: len(REGISTRY) == 1 or p.poll() is not None, 60)
        assert p.poll() is None, p.stdout.read()
        ch._wait(lambda: any(e["event"] == "held" for e in
                             ch._sim(capsys, pssd, "log")["events"]), 30)
        sid = ch._sim(capsys, pssd, "attach")["dbg"]
        assert ch._dbg(capsys, "break", "add", f"{tmp_path / 'dut.pss'}:2",
                       "--session", sid)["breakpoint"]["verified"]
        r = ch._sim(capsys, pssd, "resume")
        assert r["ok"] and r["why"] == "start", r
        stop = ch._dbg(capsys, "continue", "--session", sid, "--timeout", "30")
        assert (stop["event"], stop["reason"]) == ("stopped", "breakpoint"), stop
        assert p.poll() is None
        assert ch._dbg(capsys, "bt", "--session", sid)["frames"]
        ch._dbg(capsys, "continue", "--session", sid, "--timeout", "1")
        out, _ = p.communicate(timeout=60)
    finally:
        DBG.end_all()
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert "PSSVM-DBG: held start @0" in out, out
    assert "PSSVM-SUMMARY: 0 passed, 1 failed" in out, out
