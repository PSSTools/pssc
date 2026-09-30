"""Graph-native end-to-end: PSS source -> pssc.SvNative -> Verilator.

The companion to test_export_api_e2e, but with PSS compilation expressed as a
DFM task (pssc.SvNative) rather than an out-of-band pssc.generate_sv() call.
Proves the pssc task package wires cleanly into an hdlsim simulation graph.
"""
import shutil

import pytest

from .conftest import run_pss_sim


def _sim_tags():
    return ["vlt"] if shutil.which("verilator") else []


_SIMS = _sim_tags()
pytestmark = pytest.mark.skipif(not _SIMS, reason="no supported simulator on PATH")


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


@pytest.mark.sim
@pytest.mark.parametrize("sim", _SIMS)
def test_pss_to_sv_runs_via_task_graph(tmp_path, sim):
    """pssc.SvNative output drives the OO export API to completion on Verilator."""
    status, log = run_pss_sim(tmp_path, sim, _ATOMIC_PSS, _TB_ATOMIC,
                              export_actions=["Entry"])
    assert status == 0, f"sim failed:\n{log}"
    assert "Hello World!" in log, log
    assert "[TB] done" in log, log
