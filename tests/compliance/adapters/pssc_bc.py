"""The ``pssc-bc`` adapter: run a compliance test on pssc's bytecode backend.

PSS -> ``pssc.Parser`` -> ``AstToIrTranslator`` -> ``PSSToScenarioPass`` ->
``zuspec.be.bc.lower_module`` -> the Python ZBC interpreter, solving with
dv-solve. ``message()`` lines are the log; nothing is reformatted (§5: an
adapter must not rewrite the log -- L0 exists to see the tool's formatting).

Outcome mapping (§5):

=====================================  ===============
parse/link error, ``ctx.errors``,      compile_error
a PSS rule bc enforces (PssSemantic)
frontend/lowering crash                compile_error (``detail`` says "crash")
bc cannot lower a construct            unsupported
dv-solve reports unsat                 solve_fail
interpreter error                      runtime_error
=====================================  ===============

Usable in-process (:func:`run`) or per the command-line contract::

    python -m adapters.pssc_bc run --root pss_top::A --seed 1 --out DIR a.pss...
"""

import argparse
import os
import sys
import traceback
from typing import List, Sequence

try:
    from . import diagnostics
except ImportError:                                   # run as a script
    import diagnostics

TOOL = "pssc"
TARGET = "bc"

#: message() verbosity the run uses (21.1.3); the suite's records are NONE.
VERBOSITY = 2


def run(sources: Sequence[str], root: str, seed: int, out_dir: str) -> str:
    """Run *root* from *sources* once with *seed*; write log.txt + outcome.json.

    Returns the outcome string.
    """
    os.makedirs(out_dir, exist_ok=True)
    lines: List[str] = []
    diags: List[dict] = []
    outcome, detail = _run(sources, root, seed, lines, diags)
    with open(os.path.join(out_dir, "log.txt"), "w") as fp:
        fp.write("".join(line + "\n" for line in lines))
    diagnostics.write(out_dir, outcome, detail, diags, seed_honored=True, tool=TOOL,
                      tool_version=_version(), target=TARGET)
    return outcome


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("pssc")
    except Exception:
        return "?"


def _run(sources, root, seed, lines, diags):
    try:
        import pssc
        from pssc.ast2ir import AstToIrTranslator
        from zuspec.ir.core.xf import PSSToScenarioPass
        from zuspec.ir.core.xf.validate import UnsupportedConstructError
        from zuspec.be.bc.lower import lower_module
        from zuspec.be.bc.lower.errors import LoweringError, PssSemanticError
        from zuspec.be.bc.interp import NativeBlobBackend, VMError, run_model
    except Exception as e:                            # environment, not the tool
        return "infra_error", f"import failed: {e}"
    from zuspec.ir.core import scenario
    if not hasattr(scenario, "ScField"):
        # A capability probe, not a version check: bc resolves exec-body names
        # through ScCoroutine.fields. Without it every attribute reference is
        # "unresolved" -- a stale environment, not a tool verdict.
        return "infra_error", (f"zuspec-ir-core at {scenario.__file__} predates "
                               f"ScCoroutine.fields (a PyPI copy instead of the "
                               f"checkout?)")

    # --- frontend ---------------------------------------------------------
    try:
        parser = pssc.Parser()
        parser.parse(list(sources))
        linked = parser.link()
    except Exception as e:
        diags.extend(diagnostics.from_exception(e))
        return "compile_error", f"parse/link: {e}"
    try:
        ctx = AstToIrTranslator().translate(linked)
    except Exception:
        return "compile_error", "crash in ast2ir:\n" + traceback.format_exc(limit=4)
    if ctx.errors:
        return "compile_error", "\n".join(ctx.errors)

    # --- lowering ---------------------------------------------------------
    comp, _, action = root.rpartition("::")
    try:
        module = PSSToScenarioPass(root=comp or None, exports=[action]).lower(ctx)
        model = lower_module(module, entry_action=action, solve_unconstrained=True)
    except PssSemanticError as e:
        return "compile_error", str(e)
    except (LoweringError, UnsupportedConstructError) as e:
        return "unsupported", str(e)
    except Exception:
        return "compile_error", "crash in lowering:\n" + traceback.format_exc(limit=6)

    # --- run --------------------------------------------------------------
    try:
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append, verbosity=VERBOSITY)
    except VMError as e:
        return "runtime_error", str(e)
    except RuntimeError as e:
        if "unsat" in str(e):
            return "solve_fail", str(e)
        return "runtime_error", str(e)
    except Exception:
        return "runtime_error", "crash in the interpreter:\n" + traceback.format_exc(limit=6)
    return "ok", ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pssc-bc", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", required=True)
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--iterations", type=int, default=1)
    r.add_argument("--platform", default=None)
    r.add_argument("--out", required=True)
    r.add_argument("sources", nargs="+")
    a = p.parse_args(argv)
    if a.platform is not None or a.iterations != 1:
        os.makedirs(a.out, exist_ok=True)
        diagnostics.write(a.out, "unsupported",
                          "--platform/--iterations are not wired in pssc-bc", [],
                          seed_honored=True, tool=TOOL, target=TARGET)
        open(os.path.join(a.out, "log.txt"), "w").close()
        return 0
    run(a.sources, a.root, a.seed, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
