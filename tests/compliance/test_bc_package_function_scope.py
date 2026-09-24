"""bc calls the package function a package function names, not the component's.

`quad` is a package function calling `twice`. The component it runs from
declares its own `twice`, which is not in scope inside `quad` (LRM 22.2). bc
used to look a bare call up in the running component first and printed
`quad=201`; the front end now says which function a call is
(`ExprRefUnresolved` for a package function) and bc follows it.
"""
from adapters import pssc_bc

_MODEL = """
import std_pkg::*;
function int twice(int x) { return 2 * x; }
function int quad(int x) { return twice(twice(x)); }
package util { function int neg(int x) { return -x; } }
component pss_top {
  function int twice(int x) { return 100 + x; }
  action A {
    exec body {
      message(NONE, "own=%d quad=%d neg=%d", comp.twice(1), quad(1), util::neg(5));
    }
  }
}
"""


def test_bc_resolves_package_and_component_functions_as_pss_says(tmp_path):
    src = tmp_path / "m.pss"
    src.write_text(_MODEL)
    assert pssc_bc.run([str(src)], "pss_top::A", 1, str(tmp_path / "out")) == "ok"
    assert (tmp_path / "out" / "log.txt").read_text() == "own=101 quad=4 neg=-5\n"
