"""Phase C1: the sv-dpi-bridge target.

Structural checks (no toolchain) on the generated dispatcher + SV package, plus
a gcc-gated end-to-end that builds libpssc_scenario.so and drives it from a C
harness through the zsp_bridge runtime.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

import pssc
from pssc import targets


_MODEL = """import std_pkg::*;
component pss_top {
    action Hello { exec body { print("hello from C\\n"); } }
    action World { exec body { print("world from C\\n"); } }
}
"""


def _besw_include():
    from zuspec.be import sw as _sw
    return Path(_sw.__file__).parent / "share" / "include"


def _gen(tmp_path):
    src = tmp_path / "m.pss"
    src.write_text(_MODEL)
    out = tmp_path / "out"
    res = pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                       export_actions=["Hello", "World"])
    assert res.outputs, "sv-dpi-bridge produced no files"
    return out


def test_bridge_target_registered():
    assert "sv-dpi-bridge" in targets.list_targets()


def test_bridge_generates_dispatch_and_pkg(tmp_path):
    out = _gen(tmp_path)
    disp = (out / "pssc_bridge_dispatch.c").read_text()
    # ACTION ids + one spawn case per action, in static storage
    assert "#define ACTION_Hello 0" in disp
    assert "#define ACTION_World 1" in disp
    assert "static pss_top__Hello root;" in disp
    assert "pss_top__Hello_body(&root, &b->tb);" in disp
    assert "static pss_top__World root;" in disp

    pkg = (out / "pssc_bridge_pkg.sv").read_text()
    assert 'import "DPI-C" function void    zsp_bridge_spawn' in pkg
    assert "localparam int ACTION_Hello = 0;" in pkg
    assert "localparam int ACTION_World = 1;" in pkg
    assert "task automatic pssc_run_action" in pkg
    # auto-seed trampoline: seed drawn from the calling SV thread's rand state
    assert "task automatic pssc_run_action_rand" in pkg
    assert "{$urandom(), $urandom()}" in pkg

    # the runtime is copied alongside (model-independent)
    assert (out / "zsp_bridge.h").exists()
    assert (out / "zsp_bridge.c").exists()


_RAND_MODEL = """import std_pkg::*;
component pss_top {
    action Entry {
        rand bit[8] x;
        constraint { x > 3; x < 8; }
        exec body { print("x=%d\\n", x); }
    }
}
"""


def test_bridge_presolves_rand_fields(tmp_path):
    """A spawned atomic action's own rand fields are baked to a
    constraint-satisfying value in the dispatcher (root.<field> = <v>)."""
    src = tmp_path / "m.pss"
    src.write_text(_RAND_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Entry"])
    disp = (out / "pssc_bridge_dispatch.c").read_text()
    # injected between _init and _body, value satisfying 3 < x < 8
    import re
    m = re.search(r"root\.x = (\d+);", disp)
    assert m, disp
    v = int(m.group(1))
    assert 3 < v < 8, f"presolved x={v} violates 3 < x < 8"
    assert disp.index("_init") < disp.index("root.x =") < disp.index("_body")


_IMPORT_MODEL = """
package dut_api {
    import target function void do_write(int a, int b);
    import solve  function int  do_read(int a);
}
component pss_top {
    import dut_api::*;
    action Entry { exec body { do_write(16, do_read(7)); } }
}
"""


def test_bridge_import_seam_splits_target_and_solve(tmp_path):
    """Imports split: a *solve* import is a synchronous export "DPI-C" function;
    a *target* import is a blocking task dispatched from the mailbox."""
    src = tmp_path / "m.pss"
    src.write_text(_IMPORT_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Entry"])
    pkg = (out / "pssc_bridge_pkg.sv").read_text()
    assert "interface class pssc_import_if;" in pkg
    # target -> task, solve -> function
    assert "pure virtual task do_write(int a, int b);" in pkg
    assert "pure virtual function int do_read(int a);" in pkg
    # solve import: synchronous export; target import: mailbox dispatch (no export)
    assert 'export "DPI-C" function do_read;' in pkg
    assert 'export "DPI-C" function do_write;' not in pkg
    assert "localparam int FN_do_write = 0;" in pkg
    assert "task automatic pssc_dispatch_import" in pkg
    assert "FN_do_write: g_pssc_imp.do_write(" in pkg
    # forking trampoline + re-entrancy plumbing
    assert "join_none" in pkg
    assert 'context function void zsp_bridge_capture_scope' in pkg
    # the C body: solve import is a global call; target import is a suspend
    # (zsp_timebase_call to the generated sub-task), not a struct member.
    body = (out / "pss_top__entry.c").read_text()
    assert "zsp_timebase_call(thread, &pss_top__Entry_do_write_task" in body
    assert "do_read(7)" in body
    assert "self->do_write" not in body
    # the blocking sub-task coroutine was generated
    assert (out / "pssc_bridge_imports.c").read_text().count("pss_top__Entry_do_write_task") >= 1


_PARALLEL_MODEL = """
package d { import target function void do_op(int id); }
component pss_top {
    import d::*;
    action Leaf1 { exec body { do_op(1); } }
    action Leaf2 { exec body { do_op(2); } }
    action Par { activity { parallel { do Leaf1; do Leaf2; } } }
}
"""


def test_bridge_parallel_generates_fork_join(tmp_path):
    """A top-level parallel action generates fork/join coroutines and the
    dispatcher spawns the par task instead of the (sequential) body."""
    src = tmp_path / "m.pss"
    src.write_text(_PARALLEL_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Par"])
    par = (out / "pssc_bridge_parallel.c").read_text()
    assert "zsp_par_block_init(&L->pb, 2);" in par         # two branches
    assert "pssc_par_pss_top__Par_b0" in par and "pssc_par_pss_top__Par_b1" in par
    assert "zsp_par_block_done_one" in par and "zsp_par_block_join" in par
    # each branch runs a leaf body coroutine
    assert "&pss_top__Leaf1_body_task" in par
    assert "&pss_top__Leaf2_body_task" in par
    # the dispatcher spawns the par task, not Par_body
    disp = (out / "pssc_bridge_dispatch.c").read_text()
    assert "&pssc_par_pss_top__Par_task" in disp
    assert "pss_top__Par_body(" not in disp


def test_bridge_runtime_solve_dispatch(tmp_path):
    """--runtime-solve emits a dv-solve TU and per-spawn solving into the action's
    rand fields (root.<f> = g_<prefix><f>), driven by the spawn seed."""
    src = tmp_path / "m.pss"
    src.write_text(_RAND_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Entry"], runtime_solve=True)
    assert (out / "pssc_solve.c").exists()
    disp = (out / "pssc_bridge_dispatch.c").read_text()
    assert "pssc_solve_all((unsigned long long)seed);" in disp
    assert "extern int32_t g_Entry__x;" in disp
    assert "root.x = g_Entry__x;" in disp


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not available")
def test_bridge_runtime_solve_varies_with_seed(tmp_path):
    src = tmp_path / "m.pss"
    src.write_text(_RAND_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Entry"], runtime_solve=True)
    lib = out / "libpssc_scenario.so"
    assert lib.exists()

    harness = out / "h.c"
    harness.write_text(
        '#include "zsp_bridge.h"\n#include <stdlib.h>\n'
        "int main(int c,char**v){ zsp_bridge_t*b=zsp_bridge_create();\n"
        "  zsp_bridge_spawn(b,0,strtoll(v[1],0,10)); zsp_bridge_run(b);\n"
        "  while(!zsp_bridge_done(b)) zsp_bridge_run(b); return 0; }\n")
    exe = out / "h"
    build = subprocess.run(
        ["gcc", "-w", f"-I{_besw_include()}", f"-I{out}", str(harness),
         "-L", str(out), "-lpssc_scenario", f"-Wl,-rpath,{out}", "-o", str(exe)],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr

    import re
    vals = []
    for seed in ("1", "2", "3", "42"):
        run = subprocess.run([str(exe), seed], capture_output=True, text=True)
        m = re.search(r"x=(\d+)", run.stdout)
        assert m, run.stdout
        v = int(m.group(1))
        assert 3 < v < 8, f"seed {seed}: x={v} violates constraint"
        vals.append(v)
    # the seed actually drives the solver (not a single baked constant)
    assert len(set(vals)) > 1, f"runtime solve looks constant: {vals}"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not available")
def test_bridge_so_drives_scenario_from_c(tmp_path):
    out = _gen(tmp_path)
    lib = out / "libpssc_scenario.so"
    assert lib.exists(), "libpssc_scenario.so was not built"

    # A tiny host: spawn both actions through the bridge and run each to done.
    harness = out / "harness.c"
    harness.write_text(
        '#include "zsp_bridge.h"\n'
        "int main(void){\n"
        "  zsp_bridge_t *b = zsp_bridge_create();\n"
        "  zsp_bridge_spawn(b,0,1); zsp_bridge_run(b);\n"
        "  while(!zsp_bridge_done(b)) zsp_bridge_run(b);\n"
        "  zsp_bridge_spawn(b,1,1); zsp_bridge_run(b);\n"
        "  while(!zsp_bridge_done(b)) zsp_bridge_run(b);\n"
        "  zsp_bridge_destroy(b); return 0;\n"
        "}\n")
    exe = out / "harness"
    build = subprocess.run(
        ["gcc", "-w", f"-I{_besw_include()}", f"-I{out}", str(harness),
         "-L", str(out), "-lpssc_scenario", f"-Wl,-rpath,{out}", "-o", str(exe)],
        capture_output=True, text=True)
    assert build.returncode == 0, f"harness link failed:\n{build.stderr}"

    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "hello from C" in run.stdout, run.stdout
    assert "world from C" in run.stdout, run.stdout
