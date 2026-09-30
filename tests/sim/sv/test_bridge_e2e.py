"""Phase C1 end-to-end: drive libpssc_scenario.so from Verilator over DPI.

Generates the sv-dpi-bridge target (which builds the .so), then a testbench that
uses the generated pssc_bridge_pkg trampoline to spawn two actions and run each
to completion, and checks the C scenario output crosses the DPI boundary.

Uses verilator directly (it links the prebuilt .so via -LDFLAGS); the matrix is
Verilator-first per the project directive.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

import pssc

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None or shutil.which("gcc") is None,
    reason="verilator + gcc required")


_MODEL = """import std_pkg::*;
component pss_top {
    action Hello { exec body { print("hello from C\\n"); } }
    action World { exec body { print("world from C\\n"); } }
}
"""

_TB = """\
module tb;
  import pssc_bridge_pkg::*;
  initial begin
    chandle b = zsp_bridge_create();
    pssc_run_action(b, ACTION_Hello, 64'd1);
    pssc_run_action(b, ACTION_World, 64'd1);
    zsp_bridge_destroy(b);
    $display("[TB] done");
    $finish;
  end
endmodule
"""


def test_bridge_drives_scenario_from_verilator(tmp_path):
    src = tmp_path / "m.pss"
    src.write_text(_MODEL)
    out = tmp_path / "out"
    res = pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                       export_actions=["Hello", "World"])
    assert res.outputs
    assert (out / "libpssc_scenario.so").exists()
    (out / "tb.sv").write_text(_TB)

    build = subprocess.run(
        ["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-WIDTH",
         "--top-module", "tb", "pssc_bridge_pkg.sv", "tb.sv",
         "-LDFLAGS", f"-L{out} -lpssc_scenario -Wl,-rpath,{out}",
         "-o", "sim_tb"],
        cwd=str(out), capture_output=True, text=True)
    assert build.returncode == 0, f"verilator build failed:\n{build.stderr}"

    run = subprocess.run([str(out / "obj_dir" / "sim_tb")],
                         capture_output=True, text=True)
    assert "hello from C" in run.stdout, run.stdout
    assert "world from C" in run.stdout, run.stdout
    assert "[TB] done" in run.stdout, run.stdout


_RAND_MODEL = """import std_pkg::*;
component pss_top {
    action Entry {
        rand bit[8] x;
        constraint { x > 3; x < 8; }
        exec body { print("x=%d\\n", x); }
    }
}
"""

_TB_RAND = """\
module tb;
  import pssc_bridge_pkg::*;
  initial begin
    chandle b = zsp_bridge_create();
    pssc_run_action(b, ACTION_Entry, 64'd1);
    $display("[TB] done");
    $finish;
  end
endmodule
"""


def test_bridge_presolved_rand_runs_on_verilator(tmp_path):
    """A spawned action's presolved rand value crosses DPI and satisfies its
    constraint."""
    src = tmp_path / "m.pss"
    src.write_text(_RAND_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Entry"])
    (out / "tb.sv").write_text(_TB_RAND)
    build = subprocess.run(
        ["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-WIDTH",
         "--top-module", "tb", "pssc_bridge_pkg.sv", "tb.sv",
         "-LDFLAGS", f"-L{out} -lpssc_scenario -Wl,-rpath,{out}", "-o", "sim_tb"],
        cwd=str(out), capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(out / "obj_dir" / "sim_tb")],
                         capture_output=True, text=True)
    import re
    m = re.search(r"x=(\d+)", run.stdout)
    assert m, run.stdout
    assert 3 < int(m.group(1)) < 8, run.stdout


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

# do_write is a *target* -> a blocking SV task that consumes time (#10); do_read
# is a *solve* -> a synchronous function. The blocking import suspends the C
# coroutine, SV forks the task (advancing time), and the mailbox re-wakes it.
_TB_IMPORTS = """\
module tb;
  import pssc_bridge_pkg::*;
  class imp_impl implements pssc_import_if;
    virtual task do_write(int a, int b); #10; $display("[imp @%0t] do_write(%0d,%0d)", $time, a, b); endtask
    virtual function int do_read(int a); return a + 5; endfunction
  endclass
  initial begin
    chandle b = zsp_bridge_create();
    imp_impl imp = new();
    pssc_set_imports(imp);
    pssc_run_action(b, ACTION_Entry, 64'd1);
    $display("[TB @%0t] done", $time);
    $finish;
  end
endmodule
"""


def test_bridge_blocking_import_consumes_time(tmp_path):
    """Blocking (target) import: the C coroutine suspends, SV runs the
    time-consuming task via fork, and the mailbox re-wakes it. do_read (solve)
    flows back synchronously. Needs --export-dynamic for the solve export."""
    src = tmp_path / "m.pss"
    src.write_text(_IMPORT_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Entry"])
    (out / "tb.sv").write_text(_TB_IMPORTS)
    build = subprocess.run(
        ["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-WIDTH",
         "--top-module", "tb", "pssc_bridge_pkg.sv", "tb.sv",
         "-LDFLAGS",
         f"-L{out} -lpssc_scenario -Wl,-rpath,{out} -Wl,--export-dynamic",
         "-o", "sim_tb"],
        cwd=str(out), capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(out / "obj_dir" / "sim_tb")],
                         capture_output=True, text=True)
    # do_read(7)=12 flowed back into C, which called the blocking do_write(16,12);
    # the #10 in the task means it (and the action) complete at time 10.
    assert "do_write(16,12)" in run.stdout, run.stdout
    assert "@10" in run.stdout, run.stdout
    assert "done" in run.stdout, run.stdout


_TB_RAND_AUTOSEED = """\
module tb;
  import pssc_bridge_pkg::*;
  initial begin
    chandle b = zsp_bridge_create();
    pssc_run_action_rand(b, ACTION_Entry);   // seed from this thread's randstate
    $display("[TB] done");
    $finish;
  end
endmodule
"""


_PARALLEL_MODEL = """
package d { import target function void do_op(int id); }
component pss_top {
    import d::*;
    action Leaf1 { exec body { do_op(1); } }
    action Leaf2 { exec body { do_op(2); } }
    action Par { activity { parallel { do Leaf1; do Leaf2; } } }
}
"""

_TB_PARALLEL = """\
module tb;
  import pssc_bridge_pkg::*;
  class imp_impl implements pssc_import_if;
    virtual task do_op(int id); #10; $display("[imp @%0t] do_op(%0d)", $time, id); endtask
  endclass
  initial begin
    chandle b = zsp_bridge_create();
    imp_impl imp = new();
    pssc_set_imports(imp);
    pssc_run_action(b, ACTION_Par, 64'd1);
    $display("[TB @%0t] done", $time);
    $finish;
  end
endmodule
"""


def test_bridge_parallel_imports_run_concurrently(tmp_path):
    """Two blocking imports under `parallel` post at the same instant and the
    trampoline forks both: each #10 task overlaps, so BOTH fire at time 10 (not
    10 and 20) and the action completes at 10."""
    src = tmp_path / "m.pss"
    src.write_text(_PARALLEL_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Par"])
    (out / "tb.sv").write_text(_TB_PARALLEL)
    build = subprocess.run(
        ["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-WIDTH",
         "--top-module", "tb", "pssc_bridge_pkg.sv", "tb.sv",
         "-LDFLAGS", f"-L{out} -lpssc_scenario -Wl,-rpath,{out} -Wl,--export-dynamic",
         "-o", "sim_tb"],
        cwd=str(out), capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(out / "obj_dir" / "sim_tb")],
                         capture_output=True, text=True)
    import re
    times = [int(t) for t in re.findall(r"do_op\(\d+\)", run.stdout) and
             re.findall(r"@(\d+)\] do_op", run.stdout)]
    assert "do_op(1)" in run.stdout and "do_op(2)" in run.stdout, run.stdout
    # both imports complete at the same time (concurrent, not serialized)
    assert times == [10, 10], f"expected both @10 (concurrent), got {times}\n{run.stdout}"


def test_bridge_runtime_solve_seeded_from_sv_thread(tmp_path):
    """--runtime-solve: the C side solves the action's constraints per-spawn,
    seeded from the SV thread's random state, on Verilator."""
    src = tmp_path / "m.pss"
    src.write_text(_RAND_MODEL)
    out = tmp_path / "out"
    pssc.compile(str(src), target="sv-dpi-bridge", output_dir=str(out),
                 export_actions=["Entry"], runtime_solve=True)
    assert (out / "libpssc_scenario.so").exists()
    (out / "tb.sv").write_text(_TB_RAND_AUTOSEED)
    build = subprocess.run(
        ["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-WIDTH",
         "--top-module", "tb", "pssc_bridge_pkg.sv", "tb.sv",
         "-LDFLAGS", f"-L{out} -lpssc_scenario -Wl,-rpath,{out}", "-o", "sim_tb"],
        cwd=str(out), capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(out / "obj_dir" / "sim_tb")],
                         capture_output=True, text=True)
    import re
    m = re.search(r"x=(\d+)", run.stdout)
    assert m, run.stdout
    assert 3 < int(m.group(1)) < 8, run.stdout
