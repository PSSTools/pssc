"""OO export API: a value-returning ``import target`` function is an SV task.

A target import may consume time, so ``import_api_if`` declares it as a task
with the return value in a leading ``output`` argument. A component
``target`` function that wraps it is a task of the component class, reaching
the import through the component's ``import_if``. An exec body declaring a
local and calling the component function from two parallel branches must then
overlap in simulation time.
"""
import re

import pytest

import pssc

from ._verilator import HAVE_VERILATOR, verilator_build_run

pytestmark = pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not on PATH")


_PSS = """
import std_pkg::*;
package dev_pkg {
    enum status_e { DONE, ERROR }
    struct cfg_s { rand bit[32] src; rand bit[32] dst; rand bit[12] tot_sz; }
    function status_e transfer(int ch, cfg_s cfg);
    import target function transfer;
}
component dev_api_c {
    target function dev_pkg::status_e transfer(int ch, dev_pkg::cfg_s cfg) {
        return dev_pkg::transfer(ch, cfg);
    }
}
component pss_top {
    dev_api_c dma;
    action xfer_a {
        rand bit[2] ch;
        rand dev_pkg::cfg_s cfg;
        constraint cfg.tot_sz in [1..64];
        exec body {
            dev_pkg::status_e st;
            st = comp.dma.transfer(ch, cfg);
            if (st != dev_pkg::DONE) { error("transfer failed"); }
        }
    }
    action two_a {
        xfer_a x0, x1;
        constraint x0.ch != x1.ch;
        activity { parallel { x0; x1; } }
    }
}
"""

_TB = """
module tb;
  import zsp_rt_pkg::*;
  import zsp_gen_pkg::*;

  class my_imp extends import_api_base;
    virtual task transfer(output status_e ret, input int ch, input dev_pkg__cfg_s cfg);
      $display("XFER_START ch=%0d t=%0t", ch, $time);
      #10;
      $display("XFER_END ch=%0d t=%0t", ch, $time);
      ret = DONE;
    endtask
  endclass

  initial begin
    my_imp imp = new();
    export_api_if ep = pss_top::type_id().create(imp);
    ep.two_a();
    $display("TB_DONE t=%0t", $time);
    $finish;
  end
endmodule
"""


def test_target_import_task_overlaps(tmp_path, capfd):
    sv_dir = tmp_path / "sv"
    pssc.generate_sv(_PSS, str(sv_dir), export_actions=["pss_top::two_a"])
    err = capfd.readouterr().err
    assert "unsupported" not in err, err

    pkg = (sv_dir / "zsp_gen_pkg.sv").read_text()
    assert re.search(
        r"pure virtual task transfer\(output status_e ret, input int ch, "
        r"input dev_pkg__cfg_s cfg\);", pkg), pkg
    assert re.search(r"class dev_api_c\b[\s\S]*?virtual task transfer\(", pkg), pkg
    # Types are declared before the interface classes that spell them.
    assert pkg.index("} status_e;") < pkg.index("interface class import_api_if")
    assert pkg.index("class dev_pkg__cfg_s;") < pkg.index("interface class import_api_if")

    (sv_dir / "tb.sv").write_text(_TB)
    rc, log = verilator_build_run(
        sv_dir, top_module="tb",
        files=["zsp_rt_pkg.sv", "zsp_gen_pkg.sv", "tb.sv"])
    assert rc == 0, log
    starts = re.findall(r"XFER_START ch=\d+ t=(\d+)", log)
    assert starts == ["0", "0"], log  # both transfers start together: overlap
    assert len(re.findall(r"XFER_END ch=\d+ t=10\b", log)) == 2, log
    assert "TB_DONE t=10" in log, log
