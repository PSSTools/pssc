#!/usr/bin/env python3
"""Validate dv-solve, zuspec-be-sw and pssc as INSTALLED packages, then again
after the installation tree has been moved.

Why this exists. Every unit test in the three repositories runs against
whatever ``dv_solve`` the test interpreter imports, and in a development
workspace that is a checkout (or a stale wheel in the shared venv). Worse,
both dv-solve's resolver and be-sw's discovery can find a *sibling checkout*
on their own, so a broken installed layout can pass by quietly resolving the
development build. This script removes both escape hatches: it installs the
real wheels into a fresh tree OUTSIDE the workspace, runs from an unrelated
directory with the development environment scrubbed, and asserts that every
resolved artifact lives inside that tree.

What it checks, per phase:

  installed   -- the tree as installed
  relocated   -- the same tree after ``mv`` to a different parent; discovery
                 and fresh builds must follow it, and nothing may still name
                 the old location
  negative    -- a copy with its headers removed, and a solver override that
                 points at nothing: both must fail naming the installation,
                 never succeed by finding something else

For installed and relocated:

  * interpreter and module paths recorded; dv_solve, zuspec.be.sw and pssc
    must all import from the tree
  * ``dv_solve.resolve_report()``: package installation selected; core lib,
    DPI lib, link dir, headers and SV sources all in the tree; no errors
  * the ctypes loader: builds a constrained problem through the Python API
    and confirms (``/proc/self/maps``) the libdv_solve it mapped is the tree's
  * be-sw ``build_executable``: compile, link and RUN a constrained solve
    (200 < x < 210)
  * be-sw ``build_dpi_library``: build, LOAD with ctypes, CALL the solve and
    read the result back (200 < x < 210), and confirm the libdv_solve the
    process mapped is the tree's
  * pssc ``sv-dpi-bridge --runtime-solve``: pssc generates the scenario and
    builds ``libpssc_scenario.so`` through its own solver compilation path;
    a C harness drives it across seeds (3 < x < 8, and not constant)

Not covered, and reported as such: running the DPI libraries inside an SV
simulator. Loading them and invoking the solve from C/ctypes is what is
checked; that is not the same thing as an elaboration. The be-sw DPI library
imports the simulator's ``svGetScope``/``svSetScope`` and the SV-side exports
the bridge calls back into; those (and only those) are stubbed for the load,
and the stubbed names are recorded in the report.

Usage (from anywhere; paths default to this workspace)::

    python pssc/scripts/validate_installed_solver.py
    python pssc/scripts/validate_installed_solver.py --wheels DIR   # prebuilt
    python pssc/scripts/validate_installed_solver.py --keep         # keep tree

Prerequisites: gcc, and -- unless ``--wheels`` is given -- the ``build``
module plus dv-solve's native build tooling (CMake, Ninja, ivpm-build) in the
running interpreter, since the wheels are built with ``--no-isolation``.
The third-party dependencies of pssc (pssparser, zuspec-ir-core, ...) come
from the running interpreter; only the three packages under test are
installed into the tree, and the script asserts they are the copies used.

Exit status is 0 only if every check passes. A JSON report is written to
``<workdir>/report.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

PACKAGES = ("dv-solve", "zuspec-be-sw", "pssc")
WHEEL_PREFIXES = {"dv-solve": "dv_solve-", "zuspec-be-sw": "zuspec_be_sw-",
                  "pssc": "pssc-"}

#: Variables that could let the development environment answer instead of
#: the installed tree: import paths, the solver override, and every search
#: path the dynamic loader or the compiler driver consults on its own.
SCRUB = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE",
         "ZSP_SOLVER_PATH", "LD_LIBRARY_PATH", "LD_PRELOAD", "LIBRARY_PATH",
         "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "IVPM_PACKAGES",
         "IVPM_PROJECT")


# --------------------------------------------------------------- building --


def build_wheels(packages_dir: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for pkg in PACKAGES:
        print("building wheel: %s" % pkg, flush=True)
        r = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation",
             "--outdir", str(out), str(packages_dir / pkg)],
            capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("FAIL: wheel build for %s:\n%s%s"
                     % (pkg, r.stdout[-4000:], r.stderr[-4000:]))
    return find_wheels(out)


def find_wheels(d: Path) -> dict:
    wheels = {}
    for pkg, prefix in WHEEL_PREFIXES.items():
        hits = sorted(d.glob(prefix + "*.whl"), key=lambda p: p.stat().st_mtime)
        if not hits:
            sys.exit("FAIL: no %s wheel in %s" % (pkg, d))
        wheels[pkg] = hits[-1]
    return wheels


def install(wheels: dict, target: Path) -> None:
    """Install by extraction -- which IS the install for these wheels.

    That holds only while a wheel carries no ``.data/`` scheme directories
    (scripts, headers, data) that an installer would relocate; checked here
    rather than assumed, so a packaging change cannot make this validation
    quietly test something other than what pip would install.
    """
    target.mkdir(parents=True)
    for pkg, whl in wheels.items():
        with zipfile.ZipFile(whl) as z:
            data = [n for n in z.namelist()
                    if n.split("/", 1)[0].endswith(".data")]
            if data:
                sys.exit("FAIL: %s has .data/ entries (%s); install with a "
                         "real installer instead of extraction" % (whl, data[0]))
            for info in z.infolist():
                path = Path(z.extract(info, target))
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    path.chmod(mode)


# ---------------------------------------------------------------- probing --

#: Runs inside the scrubbed child interpreter. Prints one JSON document.
PROBE = r'''
import ctypes, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = os.path.realpath(sys.argv[1])
WORK = Path(sys.argv[2]); WORK.mkdir(parents=True, exist_ok=True)
out = {"interpreter": sys.executable, "cwd": os.getcwd(), "checks": {},
       "failures": []}

def under(p):
    return p is not None and os.path.realpath(str(p)).startswith(ROOT + os.sep)

def check(name, ok, detail=""):
    out["checks"][name] = {"ok": bool(ok), "detail": detail}
    if not ok:
        out["failures"].append("%s: %s" % (name, detail))

def mapped(stem):
    """Paths of *stem* libraries mapped into this process."""
    with open("/proc/self/maps") as f:
        return sorted({l.split()[-1] for l in f if "/lib%s." % stem in l})

try:
    import dv_solve, pssc
    from dv_solve import lib as dvlib
    from zuspec.be import sw as besw
    from zuspec.be.sw.scenario import solver_paths as sp
    from zuspec.be.sw.scenario.driver import build_executable, build_dpi_library
    out["modules"] = {"dv_solve": dv_solve.__file__, "zuspec.be.sw": besw.__file__,
                      "pssc": pssc.__file__}
    for m, f in out["modules"].items():
        check("import:" + m, under(f), f)

    rep = dv_solve.resolve_report()
    out["resolve_report"] = rep
    inst = rep["installation"] or {}
    check("dv_solve:installation", inst.get("kind") == "package"
          and under(inst.get("root")), inst)
    for key in ("core_lib", "dpi_lib"):
        check("dv_solve:" + key, under(rep[key]), rep[key])
    for key in ("link_dirs", "incdirs", "svdirs"):
        vals = rep[key] or []
        check("dv_solve:" + key, vals and all(under(v) for v in vals), vals)
    check("dv_solve:no-errors", not rep["errors"], rep["errors"])

    # The ctypes loader, exercised through the Python API.
    from dv_solve.builder import SolveProblemBuilder
    from dv_solve.problem import BIN_GT, BIN_LT
    b = SolveProblemBuilder()
    b.add_var(0, width=8, is_signed=False, lo=0, hi=255)
    b.add_constraint(b.expr_binary(BIN_GT, b.expr_var(0), b.expr_const(200, 8)))
    b.add_constraint(b.expr_binary(BIN_LT, b.expr_var(0), b.expr_const(210, 8)))
    problem = b.finalize_bytes()
    maps = mapped("dv_solve")
    check("loader:mapped-from-tree", maps and all(under(m) for m in maps), maps)

    # be-sw discovery.
    paths = sp.find_solver_paths()
    check("be-sw:find_solver_paths", paths is not None and under(paths.lib_dir)
          and all(under(d) for d in paths.include_dirs),
          paths and {"lib_dir": str(paths.lib_dir),
                     "include_dirs": [str(d) for d in paths.include_dirs]})
    check("be-sw:no-sibling-checkout", sp._find_dv_solve_root() is None,
          str(sp._find_dv_solve_root()))

    # be-sw scenario sources: a generated problem embedded in a solver TU.
    scn = WORK / "besw"; scn.mkdir(exist_ok=True)
    (scn / "scenario_gen.h").write_text(
        "#ifndef SCENARIO_GEN_H\n#define SCENARIO_GEN_H\n#include <stdint.h>\n"
        "extern int32_t g_x;\nvoid scenario_solve_all(void);\n#endif\n")
    (scn / "scenario_gen.c").write_text(
        '#include "scenario_gen.h"\n#include "zsp_alloc.h"\n#include <stdio.h>\n'
        "int32_t g_x = -1;\n"
        "int main(void){ scenario_solve_all(); printf(\"x=%d\\n\", (int)g_x);"
        " return 0; }\n")
    (scn / "scenario_solve.c").write_text(
        '#include "scenario_gen.h"\n#include "zsp_problem.h"\n'
        '#include "zsp_block_alloc.h"\n#include "zsp_ctx.h"\n'
        '#include "zsp_search.h"\n#include <string.h>\n'
        "static const unsigned char prob[] = {%s};\n"
        "void scenario_solve_all(void) {\n"
        "  static unsigned char cbuf[1<<20];\n"
        "  zsp_block_alloc_t *ba = zsp_block_alloc_create(0, 1<<20);\n"
        "  SolveCtx *c = solver_create(cbuf, sizeof(cbuf), ba);\n"
        "  solver_compile(c, (SolveProblem *)prob);\n"
        "  SolveOpts o; memset(&o, 0, sizeof(o)); o.seed = 12345ull; o.fair_pick = 1;\n"
        "  solver_solve(c, &o);\n"
        "  g_x = (int32_t)solver_get_value(c, 0);\n"
        "  solver_destroy(c); zsp_block_alloc_destroy(ba);\n}\n"
        % ",".join(str(x) for x in problem))
    sources = [scn / "scenario_gen.c", scn / "scenario_solve.c"]

    # be-sw entry point 1: executable, run.
    res, p = build_executable(sources, scn / "scenario", scn / "exe", link_solver=True)
    check("be-sw:build_executable", res.success, res.stderr[-2000:])
    if res.success:
        r = subprocess.run([str(scn / "scenario")], capture_output=True, text=True,
                           cwd=str(scn))
        m = re.search(r"x=(-?\d+)", r.stdout)
        v = int(m.group(1)) if m else None
        check("be-sw:executable-solves", r.returncode == 0 and v is not None
              and 200 < v < 210, {"rc": r.returncode, "stdout": r.stdout,
                                  "stderr": r.stderr[-500:]})
        if shutil.which("ldd"):
            ldd = subprocess.run(["ldd", str(scn / "scenario")],
                                 capture_output=True, text=True).stdout
            hit = [l.split("=>")[1].split()[0] for l in ldd.splitlines()
                   if "libdv_solve" in l and "=>" in l]
            check("be-sw:executable-links-tree", hit and all(under(h) for h in hit), hit)

    # be-sw entry point 2: DPI library, loaded and invoked.
    res, so, p = build_dpi_library(sources, scn / "dpi", so_name="libscn.so",
                                   link_solver=True)
    check("be-sw:build_dpi_library", res.success, res.stderr[-2000:])
    if res.success:
        # Outside a simulator nothing supplies the DPI imports (svGetScope,
        # svSetScope) or the SV-side exports the bridge calls back into. Stub
        # exactly those -- whatever the library imports that neither
        # libdv_solve nor libc provides -- in a library loaded RTLD_GLOBAL
        # first. The solve path itself is the real one.
        solver_syms = set(subprocess.run(
            ["nm", "-D", "--defined-only", rep["core_lib"]],
            capture_output=True, text=True).stdout.split())
        undef = [l.split()[-1] for l in subprocess.run(
            ["nm", "-uD", str(so)], capture_output=True, text=True).stdout.splitlines()
            if l.split()[0] == "U"]
        stubs = sorted(u for u in undef if "@" not in u and u not in solver_syms)
        out["dpi_stubbed_symbols"] = stubs
        stub_c = scn / "sim_stubs.c"
        stub_c.write_text("".join("void *%s(void){return 0;}\n" % n for n in stubs))
        r = subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(scn / "libsimstub.so"),
                            str(stub_c)], capture_output=True, text=True)
        check("be-sw:dpi-sim-stubs", r.returncode == 0 and all(
            n.startswith("sv") or n.startswith("zsp_") for n in stubs),
            {"stubs": stubs, "stderr": r.stderr[-500:]})
        ctypes.CDLL(str(scn / "libsimstub.so"), mode=ctypes.RTLD_GLOBAL)
        h = ctypes.CDLL(str(so))
        h.scenario_solve_all.restype = None
        h.scenario_solve_all()
        v = ctypes.c_int32.in_dll(h, "g_x").value
        check("be-sw:dpi-library-solves", 200 < v < 210, v)
        maps = mapped("dv_solve")
        check("be-sw:dpi-maps-tree-solver", maps and all(under(m) for m in maps), maps)

    # pssc: its own solver compilation path (sv-dpi-bridge, runtime solve).
    ps = WORK / "pssc"; ps.mkdir(exist_ok=True)
    (ps / "m.pss").write_text(
        "component pss_top {\n  action Entry {\n    rand bit[8] x;\n"
        "    constraint { x > 3; x < 8; }\n"
        '    exec body { print("x=%d\\n", x); }\n  }\n}\n')
    gen = ps / "out"
    pssc.compile(str(ps / "m.pss"), target="sv-dpi-bridge", output_dir=str(gen),
                 export_actions=["Entry"], runtime_solve=True)
    lib = gen / "libpssc_scenario.so"
    check("pssc:libpssc_scenario-built", lib.exists(), str(lib))
    if lib.exists():
        if shutil.which("ldd"):
            ldd = subprocess.run(["ldd", str(lib)], capture_output=True, text=True).stdout
            hit = [l.split("=>")[1].split()[0] for l in ldd.splitlines()
                   if "libdv_solve" in l and "=>" in l]
            check("pssc:links-tree-solver", hit and all(under(h) for h in hit), hit)
        inc = Path(besw.__file__).parent / "share" / "include"
        (gen / "h.c").write_text(
            '#include "zsp_bridge.h"\n#include <stdlib.h>\n'
            "int main(int c,char**v){ zsp_bridge_t*b=zsp_bridge_create();\n"
            "  zsp_bridge_spawn(b,0,strtoll(v[1],0,10)); zsp_bridge_run(b);\n"
            "  while(!zsp_bridge_done(b)) zsp_bridge_run(b); return 0; }\n")
        r = subprocess.run(["gcc", "-w", "-I%s" % inc, "-I%s" % gen, str(gen / "h.c"),
                            "-L", str(gen), "-lpssc_scenario",
                            "-Wl,-rpath,%s" % gen, "-o", str(gen / "h")],
                           capture_output=True, text=True)
        check("pssc:harness-links", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            vals = []
            for seed in ("1", "2", "3", "42"):
                run = subprocess.run([str(gen / "h"), seed], capture_output=True,
                                     text=True, timeout=30)
                m = re.search(r"x=(\d+)", run.stdout)
                vals.append(int(m.group(1)) if m else None)
            check("pssc:runtime-solve-in-range",
                  all(v is not None and 3 < v < 8 for v in vals), vals)
            check("pssc:runtime-solve-varies", len(set(vals)) > 1, vals)
except Exception as e:
    import traceback
    out["failures"].append("exception: " + traceback.format_exc())

print(json.dumps(out, default=str))
'''

#: The negative phase: must fail, and fail naming the installation.
NEGATIVE = r'''
import json, os, sys
from pathlib import Path
ROOT, MODE = sys.argv[1], sys.argv[2]
out = {"checks": {}, "failures": []}
def check(name, ok, detail=""):
    out["checks"][name] = {"ok": bool(ok), "detail": detail}
    if not ok:
        out["failures"].append("%s: %s" % (name, detail))
try:
    import dv_solve
    from zuspec.be.sw.scenario import solver_paths as sp
    from zuspec.be.sw.scenario.driver import build_executable
    if MODE == "no-headers":
        try:
            dv_solve.get_incdirs(); err = None
        except RuntimeError as e:
            err = str(e)
        check("dv_solve:get_incdirs-raises", err and "package installation" in err
              and ROOT in err, err)
        try:
            sp.find_solver_paths(); err = None
        except sp.SolverDiscoveryError as e:
            err = str(e)
        check("be-sw:discovery-raises", err and ROOT in err, err)
    else:   # empty-override
        empty = os.environ["ZSP_SOLVER_PATH"]
        try:
            dv_solve.get_libdirs(); err = None
        except RuntimeError as e:
            err = str(e)
        check("dv_solve:get_libdirs-raises", err and empty in err, err)
        src = Path(sys.argv[3]) / "s.c"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("int solve_problem_init(void);\nint main(void){return 0;}\n")
        res, p = build_executable([src], src.parent / "x", src.parent,
                                  link_solver=True)
        check("be-sw:build-fails-naming-override", not res.success
              and empty in res.stderr, res.stderr[-1000:])
except Exception:
    import traceback
    out["failures"].append("exception: " + traceback.format_exc())
print(json.dumps(out, default=str))
'''


def child_env(tree: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env["PYTHONPATH"] = str(tree)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_child(script: str, args: list, env: dict, cwd: Path) -> dict:
    cwd.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, "-c", script, *map(str, args)],
                       env=env, cwd=str(cwd), capture_output=True, text=True,
                       timeout=900)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"checks": {}, "failures": [
            "child did not report (rc=%d):\n%s\n%s"
            % (r.returncode, r.stdout[-3000:], r.stderr[-3000:])]}


def report(phase: str, res: dict) -> bool:
    print("\n== %s" % phase)
    if "interpreter" in res:
        print("  interpreter: %s" % res["interpreter"])
        print("  cwd:         %s" % res["cwd"])
        for m, f in res.get("modules", {}).items():
            print("  %-13s %s" % (m + ":", f))
    for name, c in res["checks"].items():
        print("  [%s] %s" % ("ok" if c["ok"] else "FAIL", name))
    for f in res["failures"]:
        if f.startswith("exception") or f.startswith("child"):
            print(textwrap.indent(f, "  "))
    return not res["failures"]


# ------------------------------------------------------------------- main --


def main() -> int:
    here = Path(__file__).resolve()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--packages", type=Path, default=here.parents[2],
                    help="directory holding the dv-solve, zuspec-be-sw and "
                         "pssc checkouts (default: this workspace)")
    ap.add_argument("--wheels", type=Path,
                    help="use prebuilt wheels from this directory")
    ap.add_argument("--workdir", type=Path,
                    help="where to install (default: a new temp dir); must "
                         "be outside the workspace")
    ap.add_argument("--keep", action="store_true", help="keep the workdir")
    args = ap.parse_args()

    if shutil.which("gcc") is None:
        print("FAIL: gcc is required")
        return 2

    workdir = (args.workdir or Path(tempfile.mkdtemp(prefix="dvsolve-installed-"))).resolve()
    workspace = args.packages.resolve()
    if workdir == workspace or workspace in workdir.parents:
        print("FAIL: workdir %s is inside the workspace %s; a checkout could "
              "be discovered by walking up from it" % (workdir, workspace))
        return 2
    workdir.mkdir(parents=True, exist_ok=True)
    print("workdir: %s" % workdir)

    wheels = (find_wheels(args.wheels) if args.wheels
              else build_wheels(workspace, workdir / "wheels"))
    for pkg, w in wheels.items():
        print("  %-13s %s" % (pkg + ":", w))

    tree = workdir / "a" / "site"
    install(wheels, tree)
    results, ok = {}, True

    results["installed"] = run_child(PROBE, [tree, workdir / "run-installed"],
                                     child_env(tree), workdir / "elsewhere")
    ok &= report("installed (%s)" % tree, results["installed"])

    moved = workdir / "b" / "relocated-site"
    moved.parent.mkdir(parents=True)
    shutil.move(str(tree), str(moved))
    results["relocated"] = run_child(PROBE, [moved, workdir / "run-relocated"],
                                     child_env(moved), workdir / "elsewhere2")
    ok &= report("relocated (%s)" % moved, results["relocated"])
    stale = [c for c in results["relocated"].get("checks", {}).values()
             if str(tree) in json.dumps(c["detail"], default=str)]
    if stale:
        print("  [FAIL] relocated results still name %s" % tree)
        ok = False

    broken = workdir / "c" / "no-headers"
    shutil.copytree(str(moved), str(broken), symlinks=True)
    shutil.rmtree(str(broken / "dv_solve" / "share" / "include"))
    results["negative:no-headers"] = run_child(
        NEGATIVE, [broken / "dv_solve", "no-headers"], child_env(broken),
        workdir / "elsewhere3")
    ok &= report("negative: installation without headers",
                 results["negative:no-headers"])

    empty = workdir / "empty-override"
    empty.mkdir()
    env = child_env(moved)
    env["ZSP_SOLVER_PATH"] = str(empty)
    results["negative:empty-override"] = run_child(
        NEGATIVE, [moved, "empty-override", workdir / "run-negative"], env,
        workdir / "elsewhere4")
    ok &= report("negative: ZSP_SOLVER_PATH names an empty directory",
                 results["negative:empty-override"])

    (workdir / "report.json").write_text(json.dumps(results, indent=2, default=str))
    print("\nNot covered: executing the DPI libraries inside an SV simulator "
          "(they are loaded and invoked from C/ctypes instead).")
    print("report: %s" % (workdir / "report.json"))
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    if not args.keep and ok and not args.workdir:
        shutil.rmtree(str(workdir), ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
