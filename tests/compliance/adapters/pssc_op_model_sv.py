"""The ``pssc-op-model-sv`` adapter: run a compliance test as an SV operation model.

PSS -> ``pssc`` op-model-sv with the test's root action as the entry point
(``--export-action``) -> a generated testbench that creates the root component
over a platform class and calls the entry -> Verilator (``--binary``) -> run.
The simulation's standard output is the log, unmodified (§5: an adapter must
not rewrite the log); the checker reads only its ``@@PSS-TRACE`` records.

The testbench names the root class and the entry from the ``--emit-manifest``
document, never by reading the generated package: the manifest exists so a
consumer does not parse generated code.

The platform is duck-typed (``<root>_root #(plat_c)``): it answers every memory
primitive (reads with 0), whether or not the model uses it. PSS `yield`
is `#0` in the generated model, so the platform supplies nothing for it. A test whose memory traffic matters uses the executor tap, which op-model-sv
does not delegate to yet, so it is refused at generation.

Outcome mapping (§5), as ``pssc_op_model_py``:

=====================================  ===============
parse/link error, ``ctx.errors``       compile_error
frontend/generator crash               compile_error (``detail`` says "crash")
not an operation-model entry, or a     unsupported
construct the backend cannot lower
generated SV that does not build       compile_error (``detail`` says "verilator")
simulation exits non-zero or hangs     runtime_error
=====================================  ===============

An operation model takes no seed, so ``seed_honored`` is false. Usable
in-process (:func:`run`) or per the command-line contract::

    python -m adapters.pssc_op_model_sv run --root pss_top::A --seed 1 --out DIR a.pss...
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from typing import List, Sequence

try:
    from . import diagnostics
except ImportError:                                   # run as a script
    import diagnostics

TOOL = "pssc"
TARGET = "op-model-sv"
#: The generated package's name. Fixed, so the testbench can import it
#: before anything is known about the model.
PACKAGE = "pss_model_pkg"
#: A simulation that has not finished by then is taken to hang.
RUN_TIMEOUT = 60


def verilator() -> str:
    """The Verilator executable, or ``""``."""
    return shutil.which("verilator") or ""


def run(sources: Sequence[str], root: str, seed: int, out_dir: str) -> str:
    """Run the entry *root* from *sources*; write log.txt + outcome.json.

    ``seed`` is accepted and not used. Returns the outcome string.
    """
    os.makedirs(out_dir, exist_ok=True)
    lines: List[str] = []
    diags: List[dict] = []
    outcome, detail = _run(sources, root, lines, out_dir, diags)
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


_TB = """\
module top;
  import pssc_reg_pkg::*;
  import {pkg}::*;

  // Answers every primitive the import API may declare; matched by
  // signature (`{root}_root #(plat_c)`), so it need not implement the
  // interface.
  class plat_c;
    task write8 (addr_handle_t addr, bit [7:0]  data); endtask
    task write16(addr_handle_t addr, bit [15:0] data); endtask
    task write32(addr_handle_t addr, bit [31:0] data); endtask
    task write64(addr_handle_t addr, bit [63:0] data); endtask
    task read8 (addr_handle_t addr, output bit [7:0]  data); data = 0; endtask
    task read16(addr_handle_t addr, output bit [15:0] data); data = 0; endtask
    task read32(addr_handle_t addr, output bit [31:0] data); data = 0; endtask
    task read64(addr_handle_t addr, output bit [63:0] data); data = 0; endtask
  endclass

  initial begin
    plat_c plat = new();
    {root}_ctxt_if dut = {root}_root #(plat_c)::create(plat);
    dut.{entry}();
    $finish;
  end
endmodule
"""


def _run(sources, root, lines, out_dir, diags):
    try:
        from pssc import driver
        from pssc.driver import CompileError
    except Exception as e:                            # environment, not the tool
        return "infra_error", f"import failed: {e}"
    vlt = verilator()
    if not vlt:
        return "infra_error", "verilator is not on PATH"

    gen_dir = os.path.join(out_dir, "gen")
    os.makedirs(gen_dir, exist_ok=True)
    manifest = os.path.join(gen_dir, "manifest.json")
    ns = argparse.Namespace(progseq_root=None, progseq_package=PACKAGE,
                            output_dir=gen_dir, export_actions=[root],
                            progseq_manifest=manifest, quiet=True)
    # --- generate ---------------------------------------------------------
    try:
        res = driver.compile(list(sources), target=TARGET, opts=ns)
    except CompileError as e:
        extra = getattr(e, "errors", None) or []
        text = "\n".join([str(e)] + [str(x) for x in extra])
        diags.extend(diagnostics.from_exception(e))
        return ("unsupported" if "cannot be lowered" in str(e)
                else "compile_error", text)
    except ValueError as e:
        return "unsupported", f"{type(e).__name__}: {e}"
    except Exception:
        return "compile_error", "crash in generation:\n" + traceback.format_exc(limit=6)

    # --- the testbench, from the manifest ---------------------------------
    with open(manifest) as fp:
        doc = json.load(fp)
    top = doc["root"]
    owner = next((c for c in doc.get("components", [])
                  for e in c.get("entries", []) if e["action"] == root
                  or e["action"].endswith("::" + root.rsplit("::", 1)[-1])), None)
    if owner is None:
        return "compile_error", f"the manifest records no entry for {root}"
    if owner["name"] != top:
        return "unsupported", (f"the entry runs in '{owner['name']}', not in the "
                               f"root '{top}'; this adapter calls the root only")
    entry = next(e["name"] for e in owner["entries"]
                 if e["action"] == root or e["action"].endswith(
                     "::" + root.rsplit("::", 1)[-1]))
    tb = os.path.join(gen_dir, "tb.sv")
    with open(tb, "w") as fp:
        fp.write(_TB.format(pkg=PACKAGE, root=top, entry=entry))

    # --- build and run ----------------------------------------------------
    srcs = [str(p) for p in res.outputs if str(p).endswith(".sv")] + [tb]
    obj = os.path.join(out_dir, "obj")
    build = subprocess.run(
        [vlt, "--binary", "-Wno-fatal", "-Wno-lint", "-Wno-style",
         "-j", "0", "--top-module", "top", "-Mdir", obj, "-o", "sim"] + srcs,
        capture_output=True, text=True, cwd=out_dir)
    if build.returncode != 0:
        return "compile_error", ("verilator could not build the generated SV:\n"
                                 + (build.stdout + build.stderr)[-4000:])
    try:
        sim = subprocess.run([os.path.join(obj, "sim")], capture_output=True,
                             text=True, cwd=out_dir, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        lines.extend((e.stdout or b"").decode(errors="replace").splitlines()
                     if isinstance(e.stdout, bytes) else (e.stdout or "").splitlines())
        return "runtime_error", f"the simulation did not finish in {RUN_TIMEOUT}s"
    lines.extend(sim.stdout.splitlines())
    if sim.returncode != 0:
        return "runtime_error", (sim.stdout + sim.stderr)[-4000:]
    return "ok", ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pssc-op-model-sv",
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
                          "--platform/--iterations are not wired in pssc-op-model-sv", [],
                          seed_honored=False, tool=TOOL, target=TARGET)
        open(os.path.join(a.out, "log.txt"), "w").close()
        return 0
    run(a.sources, a.root, a.seed, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
