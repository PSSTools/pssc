"""A package-qualified call to an import is the same call as an unqualified one.

`p_pkg::wait_ns(10)` and `wait_ns(10)` after `import p_pkg::*` name one
function, the one the linker found. The second worked on every op-model target
and the first was refused on all four ("no function named 'p_pkg::wait_ns'"):
an import is declared to the platform under its own name, and the qualified
call kept the qualifier. ast2ir now gives a qualified call the import's name
(`_translate_expr_ref_static_rooted`).

The claim is checked as a difference: each target generates the SAME files for
both spellings. The trace harness cannot run either -- its platforms implement
no imports -- and the existing import tests (`test_c_imports.py`, ...) hold
what an import call renders as.
"""
from __future__ import annotations

import pytest

from . import trace_harness as th

_MODEL = """
package p_pkg {
  import target function void wait_ns(int n);
  import function int ticks();
}
import p_pkg::*;
pure component ga_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> STS;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["STS"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  target function void run() { @WAIT@(10); a.STS.write_val(@TICKS@()); }
}
"""


def _files(out):
    return {p.relative_to(out).as_posix(): p.read_text()
            for p in sorted(out.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("target", th.TARGETS)
def test_a_qualified_call_generates_what_an_unqualified_one_does(tmp_path,
                                                                 target):
    (tmp_path / "plain").mkdir()
    (tmp_path / "qual").mkdir()
    plain = th.compile_model(
        tmp_path / "plain",
        _MODEL.replace("@WAIT@", "wait_ns").replace("@TICKS@", "ticks"),
        target)
    qual = th.compile_model(
        tmp_path / "qual",
        _MODEL.replace("@WAIT@", "p_pkg::wait_ns")
              .replace("@TICKS@", "p_pkg::ticks"),
        target)
    a, b = _files(plain), _files(qual)
    assert a.keys() == b.keys()
    for name in a:
        assert a[name] == b[name], name
