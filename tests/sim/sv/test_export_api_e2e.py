"""Phase C0 end-to-end: drive the OO export API on Verilator via pytest_dfm.

Generates the default ``oo_api`` projection with the *public* ``generate_sv``
entry point, writes a testbench that creates the export API through the factory
(``pss_top::type_id().create(imp)``) and calls the export task, then compiles +
runs on Verilator through the DV Flow Manager ``hdlsim.vlt`` flow.

The matrix is written so additional class/randomize-capable simulators
(``mti``/``vcs``) drop in by extending ``_sim_tags()`` -- no test changes needed.
"""
import asyncio
import os
import shutil
from pathlib import Path

import pytest

import pssc


def _sim_tags():
    """Simulator short-tags for the OO-API e2e.

    Verilator-only for now (the directive). The generated SV also *compiles*
    clean on Questa (``mti``)/VCS, but their DFM run-flow needs separate work;
    re-enable by extending this list once that lands -- the tests need no change.
    """
    tags = []
    if shutil.which("verilator"):
        tags.append("vlt")
    return tags


_SIMS = _sim_tags()
pytestmark = pytest.mark.skipif(not _SIMS, reason="no supported simulator on PATH")


def _run(tmpdir, sim, sv_dir, top_module="tb"):
    """Compile + run every .sv in *sv_dir* via the DFM hdlsim flow.

    Returns (status, sim_log_text). Mirrors tests/sim/sv/conftest.run_sim but
    enables Verilator ``--timing`` (the generated tasks are class methods).
    """
    from dv_flow.mgr import TaskListenerLog, TaskSetRunner, PackageLoader
    from dv_flow.mgr.task_graph_builder import TaskGraphBuilder

    rundir = str(Path(tmpdir) / "rundir")
    errors = []

    def marker_listener(marker):
        from dv_flow.mgr.task_data import SeverityE
        if marker.severity == SeverityE.Error:
            errors.append(str(marker.msg))

    builder = TaskGraphBuilder(
        PackageLoader(marker_listeners=[marker_listener]).load_rgy(
            ["std", f"hdlsim.{sim}"]),
        rundir)
    runner = TaskSetRunner(rundir)
    runner.builder = builder

    # Chain single-file FileSets so package/class declaration order is fixed
    # (zsp_rt_pkg -> zsp_gen_pkg -> tb); a single ``*.sv`` glob orders tb before
    # the package it uses, which both Verilator and Questa reject.
    def _fs(name, include, needs):
        return builder.mkTaskNode(
            "std.FileSet", name=name, type="systemVerilogSource",
            base=str(sv_dir), include=include, needs=needs)

    fs_rt = _fs("fs_rt", "zsp_rt_pkg.sv", [])
    fs_gen = _fs("fs_gen", "zsp_gen_pkg.sv", [fs_rt])
    fs_tb = _fs("fs_tb", "tb.sv", [fs_gen])
    sim_img = builder.mkTaskNode(
        f"hdlsim.{sim}.SimImage", name="sim_img", top=[top_module],
        timing=True, needs=[fs_tb])
    sim_run = builder.mkTaskNode(
        f"hdlsim.{sim}.SimRun", name="sim_run", needs=[sim_img])

    runner.add_listener(TaskListenerLog().event)
    out = asyncio.run(runner.run(sim_run))

    sim_log = ""
    if out is not None and getattr(out, "output", None):
        for fs in out.output:
            if getattr(fs, "filetype", None) == "simRunDir":
                log_path = os.path.join(fs.basedir, "sim.log")
                if os.path.isfile(log_path):
                    sim_log = open(log_path).read()
                break
    if errors and not sim_log:
        sim_log = "\n".join(errors)
    return runner.status, sim_log


_ATOMIC_PSS = """import std_pkg::*;
component pss_top {
    action Entry { exec post_solve { print("Hello World!"); } }
}
"""

_TB_ATOMIC = """\
module tb;
  import zsp_rt_pkg::*;
  import zsp_gen_pkg::*;
  initial begin
    export_api_if ep = pss_top::type_id().create(null);
    ep.Entry();
    $display("\\n[TB] done");
    $finish;
  end
endmodule
"""

_IMPORTS_PSS = """
package dut_api {
    import target function void doit(int i);
    import solve  function int  getval(int i);
}
component pss_top {
    import dut_api::*;
    action Entry { exec body { doit(getval(7)); } }
}
"""

# Mixin pattern: each impl overrides one import method over import_api_base
# (which $fatal-stubs every method).  doit = target -> task, getval = solve -> function.
_TB_IMPORTS = """\
module tb;
  import zsp_rt_pkg::*;
  import zsp_gen_pkg::*;
  class getval_impl #(type BaseT) extends BaseT;
    virtual function int getval(int i); return i + 5; endfunction
  endclass
  class doit_impl #(type BaseT) extends BaseT;
    virtual task doit(int i); $write("[imp] doit(%0d)\\n", i); endtask
  endclass
  class import_api_tb extends doit_impl #(getval_impl #(import_api_base));
  endclass
  initial begin
    import_api_tb imp = new();
    export_api_if ep = pss_top::type_id().create(imp);
    ep.Entry();
    $display("[TB] done");
    $finish;
  end
endmodule
"""


def _stage(tmp_path, pss_text, tb_text):
    sv_dir = tmp_path / "sv"
    pssc.generate_sv(pss_text, str(sv_dir))           # default projection: oo_api
    (sv_dir / "tb.sv").write_text(tb_text)
    return sv_dir


@pytest.mark.parametrize("sim", _SIMS)
def test_atomic_export_api_runs(tmp_path, sim):
    """``ep.Entry()`` runs the atomic action lifecycle; post_solve print fires."""
    sv_dir = _stage(tmp_path, _ATOMIC_PSS, _TB_ATOMIC)
    status, log = _run(tmp_path, sim, sv_dir)
    assert status == 0, f"sim failed:\n{log}"
    assert "Hello World!" in log, log
    assert "[TB] done" in log, log


@pytest.mark.parametrize("sim", _SIMS)
def test_import_api_routed_to_testbench(tmp_path, sim):
    """target->task and solve->function imports route through import_api_if."""
    sv_dir = _stage(tmp_path, _IMPORTS_PSS, _TB_IMPORTS)
    status, log = _run(tmp_path, sim, sv_dir)
    assert status == 0, f"sim failed:\n{log}"
    assert "[imp] doit(12)" in log, log     # getval(7)=12, doit(12)
    assert "[TB] done" in log, log


_MULTI_PSS = """import std_pkg::*;
component pss_top {
    action Hello { exec post_solve { print("hello "); } }
    action World { exec post_solve { print("world "); } }
}
"""

# Generic-run redirect: build a heterogeneous, ordered list of action
# invocations behind pss_action_run_if and replay it. ('program' is an SV
# keyword, so the queue is named 'prog'.)
_TB_MULTI = """\
module tb;
  import zsp_rt_pkg::*;
  import zsp_gen_pkg::*;
  initial begin
    pss_action_run_if prog[$];
    export_api_if ep = pss_top::type_id().create(null);
    prog.push_back(Hello_runner::create(ep));
    prog.push_back(World_runner::create(ep));
    prog.push_back(Hello_runner::create(ep));
    foreach (prog[i]) prog[i].run();
    $display("\\n[TB] done");
    $finish;
  end
endmodule
"""


@pytest.mark.parametrize("sim", _SIMS)
def test_action_runner_list_dispatch(tmp_path, sim):
    """pss_action_run_if proxies replay an ordered action list polymorphically."""
    sv_dir = tmp_path / "sv"
    pssc.generate_sv(_MULTI_PSS, str(sv_dir), export_actions=["Hello", "World"])
    (sv_dir / "tb.sv").write_text(_TB_MULTI)
    status, log = _run(tmp_path, sim, sv_dir)
    assert status == 0, f"sim failed:\n{log}"
    # Hello, World, Hello -- order preserved through the queue.
    assert "hello world hello" in log, log
    assert "[TB] done" in log, log
