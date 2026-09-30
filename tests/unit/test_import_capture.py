"""S1: package-scope `import target/solve function` capture into the IR context.

Verifies the front end surfaces package-scope import prototypes on
``AstToIrContext.import_functions`` with the correct target/solve classification,
which drives the SV ``import_api_if`` projection (Phase C0).
"""
from pssc.frontend import Parser
from pssc.ast2ir import AstToIrTranslator


def _translate(pss_text):
    parser = Parser()
    parser.parses([("inline.pss", pss_text)])
    root = parser.link()
    return AstToIrTranslator().translate(root)


def test_import_target_and_solve_captured():
    ctx = _translate(
        """import std_pkg::*;
        package dut_api {
            import target function void doit(int i);
            import solve  function int  getval(int i);
        }
        component pss_top {
            action Entry { exec post_solve { print("hi"); } }
        }
        """
    )
    by_name = {f.name: f for f in ctx.import_functions}
    assert set(by_name) == {"doit", "getval"}

    doit = by_name["doit"]
    assert doit.is_import and doit.is_target and not doit.is_solve
    assert doit.returns is None                     # void
    assert [a.arg for a in doit.args.args] == ["i"]

    getval = by_name["getval"]
    assert getval.is_import and getval.is_solve and not getval.is_target
    assert getval.returns is not None               # int
    assert [a.arg for a in getval.args.args] == ["i"]


def test_no_imports_yields_empty_list():
    ctx = _translate(
        """import std_pkg::*;
        component pss_top {
            action Entry { exec post_solve { print("hi"); } }
        }
        """
    )
    assert ctx.import_functions == []
