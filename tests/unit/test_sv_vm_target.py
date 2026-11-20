"""`op-model-sv-vm` (P3.4a1): op-model-sv's API over a pssvm engine.

The package keeps op-model-sv's API files text for text, so a testbench
written against op-model-sv compiles against either; in place of the
component classes it holds the facade (`targets/sv_vm/facade.py`). On WB DMA
(`examples/op_model`): the target is listed, the API files are byte-identical
to op-model-sv's, and the package with a testbench that creates the model
through `wb_dma_c_root_vm::create` lints in Verilator.

P3.4a2: with an image of `wb_dma_c` (`pssc build --api`) loaded into pyvm
through the shim (pyhdl-if), the facade's calls run in Verilator against an
assoc-array memory and touch the same addresses as the calls made from
Python on the same image.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pssparser")

from pssc import cli
import pssc.targets as targets

ROOT = Path(__file__).resolve().parents[2]
OP_MODEL = ROOT / "examples" / "op_model" / "pss"
WB_TESTS = ROOT / "examples" / "op_model" / "tests"
API_FILES = ["wb_dma_c_exp_if.svh", "wb_dma_ch_c_exp_if.svh",
             "wb_dma_c_imp_if.svh", "wb_dma_c_types.svh", "wb_dma_c_regs.svh"]


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

TB = """\
module tb;
  import pssc_reg_pkg::*;
  import pssvm_pkg::*;
  import wb_dma_c_pkg::*;
  class mem_imp implements wb_dma_c_imp_if;
    virtual task write8 (addr_handle_t addr, bit [7:0]  data); endtask
    virtual task read8  (addr_handle_t addr, output bit [7:0]  data); data = 0; endtask
    virtual task write16(addr_handle_t addr, bit [15:0] data); endtask
    virtual task read16 (addr_handle_t addr, output bit [15:0] data); data = 0; endtask
    virtual task write32(addr_handle_t addr, bit [31:0] data); endtask
    virtual task read32 (addr_handle_t addr, output bit [31:0] data); data = 0; endtask
    virtual task write64(addr_handle_t addr, bit [63:0] data); endtask
    virtual task read64 (addr_handle_t addr, output bit [63:0] data); data = 0; endtask
  endclass
  initial begin
    mem_imp imp = new;
    wb_dma_c_exp_if m = wb_dma_c_root_vm #(mem_imp)::create(imp, 'h1000);
    wb_dma_status_e st;
    if (m != null) m.ch(0).wait_completion(st);
    $finish;
  end
endmodule
"""


def _files():
    return [str(OP_MODEL / re.sub(r"^src/pss/", "", ln.strip()))
            for ln in (OP_MODEL / "files.f").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


# As the WB DMA tests' target_cfg.pss (the image's): the facade must match it.
TESTS_CFG = ["--target-cfg", "HAVE_EVENT_WAIT=false",
             "--target-cfg", "HAVE_RUNTIME_SOLVER=false"]


@pytest.fixture(scope="module")
def outs(tmp_path_factory):
    base = tmp_path_factory.mktemp("svvm")
    for tgt, sub in (("op-model-sv", "sv"), ("op-model-sv-vm", "vm")):
        assert cli.main(["compile", *_files(), "-t", tgt, "--root", "wb_dma_c",
                         *TESTS_CFG, "-o", str(base / sub)]) == 0
    return base / "sv", base / "vm"


def test_target_listed():
    assert "op-model-sv-vm" in targets.list_targets()


def test_package_holds_api_and_facade_only(outs):
    _, vm = outs
    names = {p.name for p in vm.iterdir()}
    assert {"wb_dma_c_pkg.sv", "wb_dma_ch_c_exp_vm.svh", "wb_dma_c_vm_plat.svh",
            "wb_dma_c_root_vm.svh", *API_FILES} <= names
    # The tree is the engine's: no component classes, adapters or factory.
    assert not names & {"wb_dma_c.svh", "wb_dma_ch_c.svh", "wb_dma_c_root.svh",
                        "wb_dma_ch_c_exp_impl.svh", "wb_dma_c_component.svh"}
    pkg = (vm / "wb_dma_c_pkg.sv").read_text()
    assert "import pssvm_pkg::*;" in pkg and "typedef class" not in pkg


@pytest.mark.parametrize("name", API_FILES)
def test_api_identical_to_op_model_sv(outs, name):
    sv, vm = outs
    assert (vm / name).read_text() == (sv / name).read_text()


def test_calls_by_symbol_on_instance_path(outs):
    _, vm = outs
    ch = (vm / "wb_dma_ch_c_exp_vm.svh").read_text()
    assert 'pss_ctxt.call("wb_dma_ch_c::transfer_single", pss_path' in ch
    # A struct argument goes as its leaves, in declaration order.
    assert ch.index("64'(cfg.src)") < ch.index("64'(cfg.dst)")
    root = (vm / "wb_dma_c_root_vm.svh").read_text()
    assert 'call_start("wb_dma_c::initialize", "", pss_args)' in root
    assert "pssvm_default_engine()" in root


@pytest.mark.skipif(VERILATOR is None, reason="verilator not found")
def test_verilator_lint(outs, tmp_path):
    _, vm = outs
    (tmp_path / "tb.sv").write_text(TB)
    r = subprocess.run(
        [VERILATOR, "--lint-only", "--timing", "-Wno-fatal", f"-I{vm}",
         str(vm / "pssc_reg_pkg.sv"),
         str(cli.sv_pssvm_dir() / cli.SV_PSSVM_PKG),
         str(vm / "wb_dma_c_pkg.sv"), str(tmp_path / "tb.sv"),
         "--top-module", "tb"],
        capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "%Error" not in r.stdout + r.stderr


def _env():
    env = dict(os.environ)
    if VERILATOR:
        env["PATH"] = str(Path(VERILATOR).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = cli.sv_pssvm_pythonpath()
    env["PSSC_NO_DAEMON"] = "1"
    return env


@pytest.fixture(scope="module")
def api_image(tmp_path_factory):
    out = tmp_path_factory.mktemp("img") / "wb_api.zbc"
    r = subprocess.run(
        [sys.executable, "-m", "pssc", "build", "@model.f", ".", "--root", "wb_dma_c",
         "--api", "--image", str(out)],
        cwd=WB_TESTS, capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def _py_calls(image):
    """The sim test's calls, from Python on the same image: the accesses."""
    sys.path.insert(0, str(Path(cli.sv_pssvm_pythonpath().split(os.pathsep)[1])))
    import pssvm_dpi_shim as shim
    eng = shim.Engine()
    assert eng.load(str(image), "", "") == 0, eng.error(0)
    mem, seen, rets = {}, [], []
    for entry, path, args in (("wb_dma_c::initialize", "", [0x1000]),
                              ("wb_dma_c::pause_engine", "", [1]),
                              ("wb_dma_ch_c::probe_status", "ch[0]", []),
                              ("wb_dma_ch_c::set_auto_restart", "ch[1]", [1])):
        for a in args:
            eng.push_arg(a)
        h = eng.call_start(entry, path)
        assert h > 0, eng.error(0)
        while (st := eng.run(h)) == 1:
            addr = eng.req_u64(h, "addr") & ((1 << 64) - 1)
            if eng.req_int(h, "kind"):
                mem[addr] = eng.req_u64(h, "data")
                seen.append(f"W {addr:x} {mem[addr]:x}")
                eng.complete(h, 0)
            else:
                seen.append(f"R {addr:x}")
                eng.complete(h, mem.get(addr, 0))
        assert st == 0, eng.error(h)
        rets.append(eng.result(h))
        eng.call_end(h)
    return seen, rets


SIM_TB = """\
`timescale 1ns/1ns
module tb;
  import pssc_reg_pkg::*;
  import pssvm_pkg::*;
  import pssvm_hif_pkg::*;
  import wb_dma_c_pkg::*;
  class mem_imp implements wb_dma_c_imp_if;
    bit [31:0] mem[longint unsigned];
    virtual task write8 (addr_handle_t addr, bit [7:0]  data); endtask
    virtual task read8  (addr_handle_t addr, output bit [7:0]  data); data = 0; endtask
    virtual task write16(addr_handle_t addr, bit [15:0] data); endtask
    virtual task read16 (addr_handle_t addr, output bit [15:0] data); data = 0; endtask
    virtual task write32(addr_handle_t addr, bit [31:0] data);
      #10; mem[addr] = data; $display("ACC: W %0h %0h", addr, data);
    endtask
    virtual task read32 (addr_handle_t addr, output bit [31:0] data);
      #10; data = mem.exists(addr) ? mem[addr] : 32'h0; $display("ACC: R %0h", addr);
    endtask
    virtual task write64(addr_handle_t addr, bit [63:0] data); endtask
    virtual task read64 (addr_handle_t addr, output bit [63:0] data); data = 0; endtask
  endclass
  initial begin
    mem_imp imp = new;
    wb_dma_c_exp_if m;
    wb_dma_status_e st;
    string img;
    void'($value$plusargs("img=%s", img));
    pssvm_set_default_engine(pssvm_engine_hif::create(img, "", ""));
    m = wb_dma_c_root_vm #(mem_imp)::create(imp, 'h1000);
    $display("CREATED %0d", m != null);
    m.pause_engine(1);
    m.ch(0).probe_status(st);
    $display("STATUS %0d", st);
    m.ch(1).set_auto_restart(1);
    $display("CTRL %0h CH1 %0h t=%0t", imp.mem['h1000], imp.mem['h1040], $time);
    $finish;
  end
endmodule
"""


def _build_sim(tmp_path, pkg_dir, tb):
    """A Verilator binary of `tb` over pssvm_pkg (+ the shim) and the
    generated package in `pkg_dir`: obj/simv."""
    (tmp_path / "tb.sv").write_text(tb)
    d = cli.sv_pssvm_dir()
    share = Path(hdl_if.share())
    lib = Path(hdl_if.libs()[0]).parent
    r = subprocess.run(
        [VERILATOR, "--binary", "--timing", "-Wno-fatal", "-o", "simv", "--Mdir", "obj",
         f"+incdir+{share / 'dpi'}", f"-I{pkg_dir}",
         "-LDFLAGS", f"-L{lib}", "-LDFLAGS", "-lpyhdl_if",
         "-LDFLAGS", f"-Wl,-rpath,{lib}", "-LDFLAGS", "-Wl,--export-dynamic",
         # pyhdl-if ahead of the model's archive, so its DPI exports link
         "-MAKEFLAGS", f"LDFLAGS=-Wl,--no-as-needed,-L{lib},-lpyhdl_if",
         str(share / "dpi" / "pyhdl_if.sv"), str(d / "pssvm_pkg.sv"),
         str(d / "pssvm_hif_api_pkg.sv"), str(d / "pssvm_hif_pkg.sv"),
         str(pkg_dir / "pssc_reg_pkg.sv"), str(pkg_dir / "wb_dma_c_pkg.sv"),
         "tb.sv", "--top-module", "tb"],
        cwd=tmp_path, capture_output=True, text=True, env=_env())
    assert r.returncode == 0, r.stderr[-3000:]
    return tmp_path / "obj" / "simv"


def _run_sim(simv, *plusargs, ok=True):
    r = subprocess.run([str(simv), *plusargs], cwd=simv.parent.parent,
                       capture_output=True, text=True, timeout=120, env=_env())
    assert (r.returncode == 0) == ok, r.stdout + r.stderr
    return r.stdout


@pytest.mark.skipif(VERILATOR is None or hdl_if is None,
                    reason="verilator or pyhdl-if not found")
def test_verilator_runs_calls(outs, api_image, tmp_path):
    _, vm = outs
    out = _run_sim(_build_sim(tmp_path, vm, SIM_TB), f"+img={api_image}")
    assert "PSSVM-ERROR" not in out and "CREATED 1" in out, out
    seen, rets = _py_calls(api_image)
    assert re.findall(r"ACC: (.*)", out) == seen, out
    assert f"STATUS {rets[2]}" in out, out
    # CTRL pause bit; ch1 CSR auto-restart bit; 10 ns per access.
    assert f"CTRL 1 CH1 40 t={10 * len(seen)}" in out, out


# The same testbench through `<root>_root_sel`: `+pss_impl` picks the model.
SEL_TB = SIM_TB.replace("wb_dma_c_root_vm #(mem_imp)::create",
                        "wb_dma_c_root_sel #(mem_imp)::create")


@pytest.fixture(scope="module")
def with_gen(tmp_path_factory):
    out = tmp_path_factory.mktemp("withgen")
    assert cli.main(["compile", *_files(), "-t", "op-model-sv-vm", "--with-gen",
                     "--root", "wb_dma_c", *TESTS_CFG, "-o", str(out)]) == 0
    return out


def test_with_gen_holds_both(outs, with_gen):
    """op-model-sv's files, text for text, and the facade beside them."""
    sv, vm = outs
    for f in sv.iterdir():
        if f.name != "wb_dma_c_pkg.sv":
            assert (with_gen / f.name).read_text() == f.read_text(), f.name
    for f in vm.iterdir():
        if f.name != "wb_dma_c_pkg.sv":
            assert (with_gen / f.name).read_text() == f.read_text(), f.name
    sel = (with_gen / "wb_dma_c_root_sel.svh").read_text()
    assert '$value$plusargs("pss_impl=%s"' in sel
    assert "wb_dma_c_root_vm #(Timp)::create(pss_imp, base)" in sel
    assert "wb_dma_c_root #(Timp)::create(pss_imp, base)" in sel
    assert not (vm / "wb_dma_c_root_sel.svh").exists()


@pytest.mark.skipif(VERILATOR is None or hdl_if is None,
                    reason="verilator or pyhdl-if not found")
def test_pss_impl_vm_and_gen_same_bus_trace(with_gen, api_image, tmp_path):
    """T3.4: one testbench, compiled once; +pss_impl=vm and +pss_impl=gen
    make the same bus accesses at the same times, and return the same."""
    simv = _build_sim(tmp_path, with_gen, SEL_TB)
    runs = {}
    for impl in ("vm", "gen"):
        out = _run_sim(simv, f"+img={api_image}", f"+pss_impl={impl}")
        assert "PSSVM-ERROR" not in out and "CREATED 1" in out, out
        runs[impl] = (re.findall(r"ACC: (.*)", out),
                      re.search(r"STATUS .*", out).group(0),
                      re.search(r"CTRL .*", out).group(0))
    assert runs["vm"][0], runs
    assert runs["vm"] == runs["gen"], runs
    assert runs["vm"][0] == _py_calls(api_image)[0]
    # No +pss_impl: gen; anything else is refused.
    assert "CREATED 1" in _run_sim(simv, f"+img={api_image}")
    # (the testbench then calls through null: the sim stops)
    out = _run_sim(simv, f"+img={api_image}", "+pss_impl=c", ok=False)
    assert "+pss_impl=c: expected vm, native or gen" in out and "CREATED 0" in out, out


def _spec(vm):
    root = (vm / "wb_dma_c_root_vm.svh").read_text()
    m = re.search(r'pss_eng\.api_check\("([^"]*)"\)', root)
    assert m, root
    return m.group(1)


def test_create_checks_image_api(outs):
    _, vm = outs
    spec = _spec(vm)
    lines = spec.split(";")
    assert lines == sorted(lines)
    # PSS names, struct arguments as dotted leaves; `->r` marks a result.
    assert any(ln.startswith("wb_dma_ch_c::transfer_single(cfg.src,")
               for ln in lines), spec
    assert "wb_dma_c::initialize(" in spec and "->r" in spec
    root = (vm / "wb_dma_c_root_vm.svh").read_text()
    assert root.index("api_check(") < root.index('call_start("wb_dma_c::initialize"')
    assert "image API differs: %s" in root


def test_api_check_on_image(outs, api_image):
    sys.path.insert(0, str(Path(cli.sv_pssvm_pythonpath().split(os.pathsep)[1])))
    import pssvm_dpi_shim as shim
    eng = shim.Engine()
    assert eng.load(str(api_image), "", "") == 0, eng.error(0)
    spec = _spec(outs[1])
    assert eng.api_check(spec) == ""
    bad = eng.api_check(spec.replace("transfer_single(cfg.src,", "transfer_single(cfg.source,"))
    assert "wb_dma_ch_c::transfer_single: parameters" in bad, bad
    assert "not in the image" in eng.api_check(spec + ";wb_dma_c::nope()")


def test_api_check_finds_other_config(api_image, tmp_path):
    """A facade for event wait (`notify_irq`) does not match the tests'
    image (event wait off)."""
    assert cli.main(["compile", *_files(), "-t", "op-model-sv-vm",
                     "--root", "wb_dma_c", "-o", str(tmp_path)]) == 0
    sys.path.insert(0, str(Path(cli.sv_pssvm_pythonpath().split(os.pathsep)[1])))
    import pssvm_dpi_shim as shim
    eng = shim.Engine()
    assert eng.load(str(api_image), "", "") == 0, eng.error(0)
    assert eng.api_check(_spec(tmp_path)) == \
        "wb_dma_c::notify_irq: not in the image"
