"""op-model-sv: the value of an `import target function`.

A target import is a TASK on the import API, its value coming back through a
leading `output status`. A call of one is therefore hoisted to a statement
writing a temporary, as a value-returning operation is; called as a function
(`pss_imp.f() / x`), the package does not compile on a simulator that checks
it (an integration report: VCS XMREF). The qualified `p_pkg::f()` reaches
the same import and is hoisted the same way.
"""
from __future__ import annotations

import pytest

from . import trace_harness as th

#: The platform's side of the import.
_PLAT = """
    task plat_ref_clk_hz(output bit [31:0] status);
      status = 32'd48000000;
    endtask
"""


def _model(call):
    return """
package plat_pkg {
    import target function bit[32] plat_ref_clk_hz();
}
import plat_pkg::*;
component pss_top {
    addr_handle_t base;
    solve function void initialize(addr_handle_t b) { base = b; }
    target function void run() {
        bit[32] div = %s / (16 * 115200);
        bit[32] clk;
        clk = %s;
        write32(make_handle_from_handle(base, 0x4), div);
        write32(make_handle_from_handle(base, 0x8), clk);
    }
}""" % (call, call)


CALLS = {"unqualified": "plat_ref_clk_hz()",
         "qualified": "plat_pkg::plat_ref_clk_hz()"}


@pytest.mark.parametrize("call", sorted(CALLS))
def test_import_value_is_hoisted(tmp_path, call):
    out = th.compile_model(tmp_path, _model(CALLS[call]), "sv")
    text = (out / "pss_top.svh").read_text()
    assert "plat_ref_clk_hz()" not in text, text
    # In an expression, through a temporary; assigned whole, straight into
    # the variable.
    assert "pss_imp.plat_ref_clk_hz(pssc_tmp_0);" in text, text
    assert "pss_imp.plat_ref_clk_hz(clk);" in text, text


@pytest.mark.sim
@pytest.mark.skipif(th.VERILATOR is None, reason="verilator not on PATH")
@pytest.mark.parametrize("call", sorted(CALLS))
def test_import_value_runs(tmp_path, call):
    trace = th.finish(th._prepare_sv(tmp_path, _model(CALLS[call]),
                                     args=(0x1000,), plat=_PLAT))
    # 48 MHz / (16 * 115200) = 26
    assert trace == ["write 32 0x1004 0x1a", "write 32 0x1008 0x2dc6c00"]


#: An `import solve function` is an SV FUNCTION on the import API, called
#: from solve context. It was classified as the model's own solve operation
#: (`func_kind` read `is_solve` before `is_import`), so the API left it out
#: and the call reached nothing.
_SOLVE_MODEL = """
package plat_pkg {
    import solve function bit[32] plat_ref_clk_hz();
}
import plat_pkg::*;
component pss_top {
    addr_handle_t base;
    bit[32] clk;
    solve function void initialize(addr_handle_t b) {
        base = b;
        clk = plat_ref_clk_hz();
    }
    target function void run() {
        write32(make_handle_from_handle(base, 0x4), clk / (16 * 115200));
    }
}"""

_SOLVE_PLAT = """
    function bit [31:0] plat_ref_clk_hz();
      return 32'd48000000;
    endfunction
"""


def test_solve_import_is_on_the_import_api(tmp_path):
    out = th.compile_model(tmp_path, _SOLVE_MODEL, "sv")
    api = (out / "pss_top_imp_if.svh").read_text()
    assert "pure virtual function bit [31:0] plat_ref_clk_hz();" in api, api


@pytest.mark.sim
@pytest.mark.skipif(th.VERILATOR is None, reason="verilator not on PATH")
def test_solve_import_runs(tmp_path):
    trace = th.finish(th._prepare_sv(tmp_path, _SOLVE_MODEL, args=(0x1000,),
                                     plat=_SOLVE_PLAT))
    assert trace == ["write 32 0x1004 0x1a"]
