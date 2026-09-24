"""The ``pssc-op-model-py`` adapter: run a compliance test as an operation model.

PSS -> ``pssc`` op-model-py with the test's root action as the entry point
(``--export-action``) -> import the generated module -> call the entry on the
root component over ``pssc_rt.MemoryBus``. The test is the same PSS every tool
runs; nothing here is specific to it. ``message()`` text is the log, exactly as
the generated module formatted it (§5: an adapter must not rewrite the log).

Outcome mapping (§5):

=====================================  ===============
parse/link error, ``ctx.errors``       compile_error
frontend/generator crash               compile_error (``detail`` says "crash")
not an operation-model entry           unsupported
(activity, rand, action attributes),
or a construct the backend cannot
lower (a diagnostic, not a crash)
exception while the entry runs         runtime_error
=====================================  ===============

An operation model takes no seed (it has no randomness), so ``seed_honored`` is
false. Usable in-process (:func:`run`) or per the command-line contract::

    python -m adapters.pssc_op_model_py run --root pss_top::A --seed 1 --out DIR a.pss...
"""

import argparse
import importlib
import os
import sys
import traceback
from typing import List, Sequence

try:
    from . import diagnostics
except ImportError:                                   # run as a script
    import diagnostics

TOOL = "pssc"
TARGET = "op-model-py"


def run(sources: Sequence[str], root: str, seed: int, out_dir: str) -> str:
    """Run the entry *root* from *sources*; write log.txt + outcome.json.

    ``seed`` is accepted and not used. Returns the outcome string.
    """
    os.makedirs(out_dir, exist_ok=True)
    lines: List[str] = []
    diags: List[dict] = []
    outcome, detail = _run(sources, root, lines, os.path.join(out_dir, "gen"), diags)
    with open(os.path.join(out_dir, "log.txt"), "w") as fp:
        fp.write("".join(line + "\n" for line in lines))
    diagnostics.write(out_dir, outcome, detail, diags, seed_honored=False, tool=TOOL,
                      tool_version=_version(), target=TARGET)
    return outcome


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("pssc")
    except Exception:
        return "?"


def _run(sources, root, lines, gen_dir, diags):
    try:
        from pssc import driver
        from pssc.driver import CompileError
    except Exception as e:                            # environment, not the tool
        return "infra_error", f"import failed: {e}"

    os.makedirs(gen_dir, exist_ok=True)
    ns = argparse.Namespace(progseq_root=None, output_dir=gen_dir,
                            export_actions=[root], quiet=True)
    # --- generate ---------------------------------------------------------
    try:
        driver.compile(list(sources), target=TARGET, opts=ns)
    except CompileError as e:
        # The legality gate's verdict ("N call(s) cannot be lowered") is the
        # backend declining a construct; anything else is the front end.
        extra = getattr(e, "errors", None) or []
        text = "\n".join([str(e)] + [str(x) for x in extra])
        diags.extend(diagnostics.from_exception(e))
        return ("unsupported" if "cannot be lowered" in str(e)
                else "compile_error", text)
    except ValueError as e:
        # The generator's diagnostics (an entry it refuses, a construct it has
        # no rendering for) are ValueErrors that name what they refuse.
        return "unsupported", f"{type(e).__name__}: {e}"
    except Exception:
        return "compile_error", "crash in generation:\n" + traceback.format_exc(limit=6)

    # --- run --------------------------------------------------------------
    modname = next((f[:-3] for f in sorted(os.listdir(gen_dir))
                    if f.endswith(".py") and not f.startswith("pssc_rt")), None)
    if modname is None:
        return "compile_error", "generation produced no module"
    entry = root.rsplit("::", 1)[-1]
    sys.path.insert(0, gen_dir)
    try:
        for name in (modname, "pssc_rt"):
            sys.modules.pop(name, None)
        rt = importlib.import_module("pssc_rt")
        mod = importlib.import_module(modname)
        cls = next((v for v in vars(mod).values()
                    if isinstance(v, type) and getattr(v, "__module__", "") == modname
                    and callable(getattr(v, entry, None))), None)
        if cls is None:
            return "compile_error", f"no generated class has the entry {entry}()"

        class _Bus(rt.MemoryBus):
            def message(self, text):
                lines.append(text)

        try:
            dut = cls(_Bus())
            getattr(dut, entry)()
        except Exception:
            return "runtime_error", traceback.format_exc(limit=6)
    finally:
        sys.path.remove(gen_dir)
    return "ok", ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pssc-op-model-py",
                                description=__doc__.split("\n")[0])
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
                          "--platform/--iterations are not wired in pssc-op-model-py", [],
                          seed_honored=False, tool=TOOL, target=TARGET)
        open(os.path.join(a.out, "log.txt"), "w").close()
        return 0
    run(a.sources, a.root, a.seed, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
