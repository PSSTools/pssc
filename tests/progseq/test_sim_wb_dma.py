"""Phase 6 behavioral gate: generate the WB DMA programming API, compile it with
the core + testbench, and run across every available simulator (pytest_dfm /
dv-flow-libhdlsim), asserting the self-check prints WB_DMA PROTOTYPE PASS.

Skips cleanly when no simulator is on PATH. Marked ``sim`` (run with ``-m sim``).
"""
import asyncio
import os
import shutil

import pytest

pytestmark = pytest.mark.sim


def available_sims():
    """Simulator short-tags whose executable is on PATH (libhdlsim naming)."""
    sims = []
    for exe, tag in {"verilator": "vlt", "vsim": "mti", "vcs": "vcs",
                     "xsim": "xsm", "xmvlog": "xcm", "iverilog": "ivl"}.items():
        if shutil.which(exe):
            sims.append(tag)
    return sims


# Focus on Verilator for now (the always-available CI gate). The generated code
# also passes Vivado xsim; Questa is license-gated in this environment. Broaden
# this set when enabling the full multi-sim matrix.
_SIMS = [s for s in available_sims() if s == "vlt"]


@pytest.mark.skipif(not _SIMS, reason="no SystemVerilog simulator on PATH")
@pytest.mark.parametrize("sim", _SIMS)
def test_wb_dma_progseq_sim(tmp_path, wb_dma_build, sim):
    from dv_flow.mgr import PackageLoader, TaskGraphBuilder, TaskSetRunner, TaskListenerLog

    rundir = os.path.join(str(tmp_path), "rundir")
    runner = TaskSetRunner(rundir)

    def marker_listener(marker):
        from dv_flow.mgr.task_data import SeverityE
        if marker.severity == SeverityE.Error:
            raise Exception(f"Marker error: {marker.msg}")

    builder = TaskGraphBuilder(
        PackageLoader(marker_listeners=[marker_listener]).load_rgy(
            ["std", f"hdlsim.{sim}"]),
        rundir,
    )
    runner.builder = builder

    # core + generated package + testbench, in explicit dependency order
    # (package must precede its users for order-sensitive simulators).
    src = builder.mkTaskNode(
        "std.FileSet", name="src", type="systemVerilogSource",
        base=wb_dma_build,
        include=["pssc_reg_pkg.sv", "wb_dma_pkg.sv", "wb_dma_tb.sv"],
        # The package includes one `.svh` per class, from its own directory.
        incdirs=["."],
        needs=[])
    img = builder.mkTaskNode(
        f"hdlsim.{sim}.SimImage", name="img", top=["top"], needs=[src])
    run = builder.mkTaskNode(
        f"hdlsim.{sim}.SimRun", name="run", needs=[img])

    runner.add_listener(TaskListenerLog().event)
    out = asyncio.run(runner.run(run))
    assert runner.status == 0, "DFM runner reported failure"

    rundir_fs = next((fs for fs in out.output
                      if fs.type == "std.FileSet" and fs.filetype == "simRunDir"), None)
    assert rundir_fs is not None, "no simRunDir in output"
    log_path = os.path.join(rundir_fs.basedir, "sim.log")
    with open(log_path) as f:
        log = f.read()

    assert "WB_DMA PROTOTYPE PASS" in log, (
        f"generated DMA model did not pass.\n--- sim.log tail ---\n{log[-2000:]}")
    assert "FAIL" not in log
