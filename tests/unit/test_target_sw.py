"""Phase-3: the c-host C target (Context-first via zuspec-be-sw).

Generation runs everywhere; the actual gcc build is gated behind the
``c_toolchain`` marker (and skipped if no compiler is present).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

import pssc
from pssc import targets
from pssc.targets.sw_lower import to_sw_context

SRC = """
component pss_top {
    action A {
        exec body {
            int v = 1 + 2;
        }
    }
}
"""


def _src(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(SRC)
    return str(p)


def _sw_include_dir() -> Path:
    import zuspec.be.sw as sw
    return Path(sw.__file__).parent / "share" / "include"


def test_chost_registered():
    assert "c-host" in targets.list_targets()


def test_c_target_family_registered():
    names = targets.list_targets()
    assert {"c-host", "c-host-presolved",
            "c-embedded", "c-embedded-presolved"} <= set(names)


def test_c_host_defaults_to_runtime_solve(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    assert (out / "pssc_solve.c").exists()  # runtime solve is the c-host default


def test_c_host_presolve_override(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out), presolve=True)
    assert not (out / "pssc_solve.c").exists()  # --presolve forces pre-solve


def test_c_host_presolved_target_bakes(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    assert not (out / "pssc_solve.c").exists()


def test_sv_dpi_emits_facade_and_dpi_entry(tmp_path):
    # sv-dpi (style 2): SV facade + c-host C runtime exposed via a DPI entry.
    assert "sv-dpi" in targets.list_targets()
    p = tmp_path / "m.pss"
    p.write_text(ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="sv-dpi", output_dir=str(out))
    sv = (out / "pssc_top.sv").read_text()
    assert 'import "DPI-C" function void pssc_run' in sv
    assert "pssc_run(" in sv  # called from the SV top module
    main_c = (out / "main.c").read_text()
    assert "void pssc_run(int seed)" in main_c
    assert "int main(" not in main_c  # DPI entry, not a CLI main


@pytest.mark.c_toolchain
def test_sv_dpi_c_runtime_compiles(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(ACTIVITY_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="sv-dpi", output_dir=str(out))
    inc = _sw_include_dir()
    # the C runtime compiles as position-independent DPI objects (no main needed);
    # co-simulating the SV facade requires a simulator (sim-gated, not run here).
    for c in [f for f in res.outputs if f.suffix == ".c"]:
        r = subprocess.run(["gcc", "-c", "-w", "-fPIC", f"-I{inc}", f"-I{out}",
                            str(c), "-o", str(c) + ".o"], capture_output=True, text=True)
        assert r.returncode == 0, f"gcc failed on {c.name}:\n{r.stderr}"


def test_c_embedded_defaults_to_runtime_solve(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-embedded", output_dir=str(out))
    assert (out / "pssc_solve.c").exists()  # runtime solve emitted


def test_c_embedded_presolved_bakes_constants(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-embedded-presolved", output_dir=str(out))
    assert not (out / "pssc_solve.c").exists()  # no runtime solver TU
    import re
    vals = {int(m) for m in re.findall(r"__v = (\d+);", (out / "pss_top__root.c").read_text())}
    assert vals and all(100 < v < 110 for v in vals)


def test_to_sw_context_lowers_actions_to_components():
    import zuspec.ir.core as ir
    core = pssc.to_core_context(_translate_ctx())
    sw_ctx = to_sw_context(core)
    # the action pss_top::A is a bare DataTypeClass in the core context ...
    assert isinstance(core.type_m["pss_top::A"], ir.DataTypeClass)
    assert not isinstance(core.type_m["pss_top::A"], ir.DataTypeComponent)
    # ... and a DataTypeComponent in the lowered context
    assert isinstance(sw_ctx.type_m["pss_top::A"], ir.DataTypeComponent)
    # non-actions are shared unchanged (identity preserved)
    assert sw_ctx.type_m["pss_top"] is core.type_m["pss_top"]


def _translate_ctx():
    import tempfile, os
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.pss")
    with open(p, "w") as fh:
        fh.write(SRC)
    return pssc.driver.translate(p)


def test_chost_generates_action_coroutine(tmp_path):
    out = tmp_path / "c"
    res = pssc.compile(_src(tmp_path), target="c-host", output_dir=str(out))
    assert res.ok
    names = [p.name for p in res.outputs]
    # the lowered action gets its own C file (name is lowercased by the backend)
    assert "pss_top__a.c" in names, names
    body = (out / "pss_top__a.c").read_text()
    # the body statement `int v = 1 + 2;` lowered to a real coroutine task
    assert "_body_task" in body
    assert "1 + 2" in body


def test_chost_via_cli(tmp_path):
    from pssc.cli import main
    out = tmp_path / "cli"
    code = main(["compile", _src(tmp_path), "-t", "c-host", "-o", str(out), "-q"])
    assert code == 0 and (out / "pss_top__a.c").is_file()


PRINT_SRC = """
component pss_top {
    action A {
        bit[8] a;
        exec body {
            print("hello");
            message(LOW, "a=%d", a);
            if (a > 0) { print("positive"); }
        }
    }
}
"""


MULTIVAL_SRC = """
component pss_top {
    action Leaf {
        rand bit[8] x; rand bit[8] y;
        constraint x > 100; constraint y > x; constraint y < 130;
        exec body { print("x=%d y=%d", x, y); message(LOW, "sum %d %d", x, y); }
    }
    action Root { Leaf a; activity { a; } }
}
"""


def test_multivalue_print_message_lowered(tmp_path):
    # print(fmt, v1, v2) / message(VERB, fmt, v1, v2) -> fprintf(fmt, v1, v2)
    p = tmp_path / "m.pss"
    p.write_text(MULTIVAL_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    assert 'fprintf(stdout, "x=%d y=%d\\n", ' in root_c
    assert 'fprintf(stdout, "sum %d %d\\n", ' in root_c
    assert "unsupported" not in root_c


@pytest.mark.c_toolchain
def test_chost_multivalue_print_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(MULTIVAL_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    # both the print and the message rendered all their values
    assert run.stdout.split("\n")[0].startswith("x=") and "y=" in run.stdout
    assert "sum " in run.stdout


def test_chost_lowers_print_and_message(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(PRINT_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    body = (out / "pss_top__a.c").read_text()
    assert 'fprintf(stdout, "hello\\n")' in body
    assert 'a=%d' in body and "locals->self->a" in body  # message with one value
    assert 'fprintf(stdout, "positive\\n")' in body       # builtin inside nested if
    assert "unsupported" not in body


def test_builtin_lowering_does_not_mutate_source(tmp_path):
    import zuspec.ir.core as ir
    p = tmp_path / "m.pss"
    p.write_text(PRINT_SRC)
    ctx = pssc.driver.translate(str(p))
    body = [f for f in ctx.ir_context.type_m["pss_top::A"].functions
            if f.name == "body"][0]
    before = type(body.body[0].expr.func).__name__
    to_sw_context(ctx.ir_context)  # must not touch the shared IR
    after = type(body.body[0].expr.func).__name__
    assert before == after == "ExprAttribute"  # still self.print(...), not rewritten


FLAT_PRINT_SRC = """
component pss_top {
    action A {
        bit[8] a;
        exec body {
            print("hello");
            message(LOW, "a=%d", a);
            print("done");
        }
    }
}
"""


COLLIDE_SRC = """
component comp1 { action A { exec body { print("c1"); } } }
component comp2 { action A { exec body { print("c2"); } } }
"""


def test_chost_action_name_collision_avoided(tmp_path):
    # two actions named `A` in different components must not collide on one C file
    p = tmp_path / "m.pss"
    p.write_text(COLLIDE_SRC)
    out = tmp_path / "c"
    names = {x.name for x in pssc.compile(str(p), target="c-host",
                                          output_dir=str(out)).outputs}
    assert "comp1__a.c" in names and "comp2__a.c" in names


ACTIVITY_SRC = """
component pss_top {
    action Leaf { exec body { print("leaf"); } }
    action Root {
        Leaf a;
        Leaf b;
        activity { a; b; }
    }
}
"""

NESTED_ACTIVITY_SRC = """
component pss_top {
    action Leaf { exec body { print("leaf"); } }
    action Mid  { Leaf x; activity { x; } }
    action Root { Mid m; Mid n; activity { m; n; } }
}
"""


def test_sequential_activity_inlined(tmp_path):
    # `activity { a; b; }` inlines both sub-action bodies into Root's coroutine
    p = tmp_path / "m.pss"
    p.write_text(ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    # 2 traversals × 2 emitted variants (sync + coroutine) = 4 inlined prints
    assert root_c.count('fprintf(stdout, "leaf\\n")') == 4


ANON_ACTIVITY_SRC = """
component pss_top {
    action Leaf { rand bit[8] v; constraint { v > 10; v < 20; } exec body { print("leaf"); } }
    action Root {
        activity { do Leaf; do Leaf; }
    }
}
"""


def test_anonymous_traversal_inlined(tmp_path):
    # `do Leaf;` -- an ANONYMOUS traversal, which names its action type instead
    # of declaring a field. Every other activity test here uses the named form
    # (`Leaf a; activity { a; }`), and that gap is why this shipped broken:
    # ActivityAnonTraversal had no case in _lower_activity_node, so it lowered
    # to nothing, Root got no synthesized body at all, and the generated main.c
    # still called pss_top__Root_body -- an undefined symbol at link time.
    p = tmp_path / "m.pss"
    p.write_text(ANON_ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    assert "pss_top__Root_body" in root_c, "no body synthesized for a `do`-only activity"
    # 2 traversals x 2 emitted variants (sync + coroutine) = 4 inlined prints
    assert root_c.count('fprintf(stdout, "leaf\\n")') == 4


def test_anonymous_traversal_siblings_get_distinct_locals(tmp_path):
    # Two `do Leaf;` in sequence are two separate action instances, so their
    # hoisted `v` locals must not alias. A named traversal prefixes locals with
    # its handle; an anonymous one has no handle, so it falls back to the type
    # name plus sibling index -- without that, both traversals would share one
    # local and one solver slot.
    p = tmp_path / "m.pss"
    p.write_text(ANON_ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out), runtime_solve=True)
    solve_c = (out / "pssc_solve.c").read_text()
    assert "g_leaf0__v" in solve_c and "g_leaf1__v" in solve_c, solve_c[:400]


@pytest.mark.c_toolchain
def test_chost_anonymous_traversal_links_and_runs(tmp_path):
    # The end-to-end case the missing ActivityAnonTraversal broke: the program
    # used to fail at LINK on an undefined pss_top__Root_body.
    #
    # _build_runtime_and_run rather than _link_and_run because Leaf has rand
    # fields, so c-host emits a pssc_solve.c that needs dv-solve's headers --
    # and those must stay in their own include set, away from be-sw's
    # conflicting zsp_alloc.h.
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(ANON_ACTIVITY_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out), runtime_solve=True)
    run = _build_runtime_and_run(out, res.outputs, seed=1)
    assert run.returncode == 0, run.stderr
    assert run.stdout.count("leaf") == 2, run.stdout


def test_nested_activity_inlined(tmp_path):
    # Root -> Mid -> Leaf : nested activities inline transitively
    p = tmp_path / "m.pss"
    p.write_text(NESTED_ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    # Root traverses m, n; each Mid traverses one Leaf -> 2 leaf prints × 2 variants
    assert root_c.count('fprintf(stdout, "leaf\\n")') == 4


def _sw_runtime_dir() -> Path:
    import zuspec.be.sw as sw
    return Path(sw.__file__).parent / "share" / "rt"


def _link_and_run(out_dir, sources) -> subprocess.CompletedProcess:
    inc, rt = _sw_include_dir(), _sw_runtime_dir()
    gen_c = [str(p) for p in sources if p.suffix == ".c"]
    rt_c = [str(p) for p in rt.glob("*.c")]
    exe = Path(out_dir) / "prog"
    r = subprocess.run(["gcc", "-g", "-O0", "-w", f"-I{inc}", f"-I{out_dir}",
                        "-o", str(exe), *gen_c, *rt_c],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"link failed:\n{r.stderr}"
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)


def _dv_dirs():
    """dv-solve's include dirs and lib dir, via its own API.

    This used to derive both from ``dv_solve.__file__``'s grandparent, which
    assumes an editable checkout (``.../dv-solve/src/c`` and
    ``.../dv-solve/build``). Against an INSTALLED WHEEL that grandparent is
    ``site-packages``, so the paths pointed at nothing and every runtime-solve
    test failed with

        fatal error: zsp_block_alloc.h: No such file or directory

    ``get_incdirs()``/``get_libdirs()`` answer correctly for both layouts, which
    is what they exist for.
    """
    import dv_solve
    return [Path(d) for d in dv_solve.get_incdirs()], Path(dv_solve.get_libdirs()[0])


def _build_runtime_and_run(out, sources, seed) -> subprocess.CompletedProcess:
    """Compile a --runtime-solve program (solver TU gets dv-solve headers; the
    runtime/component TUs get the be-sw headers — they carry a conflicting
    zsp_alloc.h) and run it with an explicit seed."""
    rtinc, rt = _sw_include_dir(), _sw_runtime_dir()
    dvinc, dvlib = _dv_dirs()
    objs = []

    def cc(src, incs):
        # incs is a LIST: dv-solve reports more than one include dir for a wheel
        # (base plus the namespaced dv_solve/ subdir). Still one include set per
        # TU, which is what keeps the two conflicting zsp_alloc.h files apart.
        if isinstance(incs, (str, Path)):
            incs = [incs]
        obj = str(out / (Path(src).name + ".o"))
        r = subprocess.run(["gcc", "-c", "-w", *[f"-I{i}" for i in incs], str(src), "-o", obj],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"cc {Path(src).name}:\n{r.stderr}"
        objs.append(obj)

    for c in sources:
        if c.suffix != ".c":
            continue
        cc(c, dvinc if c.name == "pssc_solve.c" else rtinc)
    for c in rt.glob("*.c"):
        cc(c, rtinc)

    exe = out / "prog"
    r = subprocess.run(["gcc", *objs, "-o", str(exe), f"-L{dvlib}", "-ldv_solve",
                        f"-Wl,-rpath,{dvlib}"], capture_output=True, text=True)
    assert r.returncode == 0, f"link:\n{r.stderr}"
    return subprocess.run([str(exe), str(seed)], capture_output=True, text=True, timeout=10)


@pytest.mark.c_toolchain
def test_chost_runtime_solve_varies_and_in_range(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)  # Leaf rand v, 100 < v < 110
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out), runtime_solve=True)
    assert (out / "pssc_solve.c").exists()

    seen = set()
    for seed in (1, 5, 9, 42):
        run = _build_runtime_and_run(out, res.outputs, seed)
        assert run.returncode == 0, run.stderr
        vals = [int(x.split("=")[1]) for x in run.stdout.split() if "v=" in x]
        assert len(vals) == 2 and all(100 < v < 110 for v in vals), run.stdout
        seen.add(tuple(vals))
    # runtime solving with different seeds is not constant (style 4, not baked)
    assert len(seen) > 1, f"runtime solve produced identical results: {seen}"


@pytest.mark.c_toolchain
def test_chost_full_program_links_and_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(FLAT_PRINT_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    # the complete generated program (incl. main.c) links and runs
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr


@pytest.mark.c_toolchain
def test_chost_comparison_model_links(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(PRINT_SRC)  # contains `if (a > 0)`
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr


@pytest.mark.c_toolchain
def test_chost_activity_model_links(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(NESTED_ACTIVITY_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr


FIELD_ACTIVITY_SRC = """
component pss_top {
    action Leaf { bit[8] v; exec body { print("leaf v=%d", v); } }
    action Root { Leaf a; Leaf b; activity { a; b; } }
}
"""


def test_activity_field_hoisting(tmp_path):
    # each traversed sub-action's field becomes a path-prefixed coroutine local;
    # siblings `a` and `b` get distinct locals (no collision)
    p = tmp_path / "m.pss"
    p.write_text(FIELD_ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    assert "a__v" in root_c and "b__v" in root_c
    # the hoisted local is the printed value (not a dropped/garbage arg)
    assert 'fprintf(stdout, "leaf v=%d\\n", locals->a__v)' in root_c


@pytest.mark.c_toolchain
def test_chost_activity_with_fields_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(FIELD_ACTIVITY_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    # two traversals, each prints its (zero-initialized, unsolved) field
    assert run.stdout.split("\n")[:2] == ["leaf v=0", "leaf v=0"], run.stdout


PARALLEL_SRC = """
component pss_top {
    action Leaf { exec body { print("leaf"); } }
    action Root { Leaf a; Leaf b; activity { parallel { a; b; } } }
}
"""

SELECT_SRC = """
component pss_top {
    action AA { exec body { print("A"); } }
    action BB { exec body { print("B"); } }
    action Root { AA a; BB b; activity { select { a; b; } } }
}
"""


REPEAT_SRC = """
component pss_top {
    action Leaf { exec body { print("leaf"); } }
    action Root { Leaf a; activity { repeat (3) { a; } } }
}
"""

SCHEDULE_SRC = """
component pss_top {
    action AA { exec body { print("A"); } }
    action BB { exec body { print("B"); } }
    action Root { AA a; BB b; activity { schedule { a; b; } } }
}
"""


def test_repeat_unrolls(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(REPEAT_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    # repeat (3) unrolls the body 3x (× 2 emitted variants)
    assert root_c.count('fprintf(stdout, "leaf\\n")') == 6


@pytest.mark.c_toolchain
def test_chost_repeat_runs_n_times(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(REPEAT_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    assert run.stdout.split() == ["leaf", "leaf", "leaf"], run.stdout


@pytest.mark.c_toolchain
def test_chost_schedule_runs_all(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(SCHEDULE_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    assert sorted(run.stdout.split()) == ["A", "B"], run.stdout


@pytest.mark.c_toolchain
def test_chost_parallel_runs_both(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(PARALLEL_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    # parallel runs both branches (sequential schedule)
    assert run.stdout.split() == ["leaf", "leaf"], run.stdout


@pytest.mark.c_toolchain
def test_chost_select_takes_first_branch(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(SELECT_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    # select takes the first branch deterministically (A, never B)
    assert run.stdout.split() == ["A"], run.stdout


PRESOLVE_SRC = """
component pss_top {
    action Leaf {
        rand bit[8] v;
        constraint v > 100;
        constraint v < 110;
        exec body { print("v=%d", v); }
    }
    action Root { Leaf a; Leaf b; activity { a; b; } }
}
"""


def test_presolve_bakes_constraint_satisfying_values(tmp_path):
    # rand fields are pre-solved (dv-solve) at compile time; baked values satisfy
    # the constraints and sibling traversals get distinct values
    import re
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    vals = {int(m) for m in re.findall(r"__v = (\d+);", root_c)}
    assert vals, "no baked field values found"
    assert all(100 < v < 110 for v in vals), vals
    assert len(vals) >= 2, f"sibling traversals should differ: {vals}"


@pytest.mark.c_toolchain
def test_chost_presolved_runs_in_range(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(PRESOLVE_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    vals = [int(line.split("=")[1]) for line in run.stdout.split() if "v=" in line]
    assert len(vals) == 2 and all(100 < v < 110 for v in vals), run.stdout


def test_presolve_range_set_implication(tmp_path):
    # the dv-solve translator handles `in [range]`, `in {set}`, and implication
    from pssc.targets.sw_solve import solve_action
    src = """
    component pss_top {
        action A {
            rand bit[8] x; rand bit[8] y; rand bit[8] z;
            constraint x in [10..20];
            constraint y in [1, 3, 5];
            constraint z > 0 -> z < 50;
        }
    }
    """
    p = tmp_path / "m.pss"
    p.write_text(src)
    a = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::A"]
    for seed in (1, 2, 7):
        sol = solve_action(a, seed=seed)
        assert 10 <= sol["x"] <= 20, sol
        assert sol["y"] in (1, 3, 5), sol
        assert not (sol["z"] > 0) or sol["z"] < 50, sol


ARRAY_SRC = """
component pss_top {
    action Leaf {
        rand bit[8] arr[3];
        constraint arr[0] > 200;
        constraint arr[1] < 5;
        exec body { print("arr0=%d", arr[0]); print("arr1=%d", arr[1]); }
    }
    action Root { Leaf a; activity { a; } }
}
"""


def test_presolve_array_elements(tmp_path):
    # fixed-size int array rand fields solve per element (arr[i] constraints)
    from pssc.targets.sw_solve import solve_action
    p = tmp_path / "m.pss"
    p.write_text(ARRAY_SRC)
    act = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::Leaf"]
    for seed in (1, 3, 7):
        s = solve_action(act, seed=seed)
        assert s["arr_0"] > 200, s          # array element slots: arr_0, arr_1, arr_2
        assert s["arr_1"] < 5, s
        assert "arr_2" in s


def test_array_field_hoisted_as_element_locals(tmp_path):
    # arr[i] flattens to scalar locals arr_0/arr_1/...; the inlined sub-action
    # standalone component is dropped (no broken array codegen)
    p = tmp_path / "m.pss"
    p.write_text(ARRAY_SRC)
    out = tmp_path / "c"
    files = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out)).outputs
    assert not any("leaf" in f.name.lower() for f in files)  # Leaf inlined, not emitted
    root_c = (out / "pss_top__root.c").read_text()
    assert "a__arr_0" in root_c and "a__arr_1" in root_c


@pytest.mark.c_toolchain
def test_chost_array_field_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(ARRAY_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    vals = {k: int(v) for k, v in (line.split("=") for line in run.stdout.split())}
    assert vals["arr0"] > 200 and vals["arr1"] < 5, run.stdout


STRUCT_SRC = """
component pss_top {
    struct Point { rand bit[8] x; rand bit[8] y; constraint x < y; }
    action Leaf {
        rand Point p;
        constraint p.x > 100;
        exec body { print("x=%d", p.x); print("y=%d", p.y); }
    }
    action Root { Leaf a; activity { a; } }
}
"""


def test_presolve_struct_field(tmp_path):
    # rand struct field: solves action constraints (p.x>100) AND the struct's own
    # constraints (x<y), flattening sub-fields to p_x / p_y slots
    from pssc.targets.sw_solve import solve_action
    p = tmp_path / "m.pss"
    p.write_text(STRUCT_SRC)
    act = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::Leaf"]
    for seed in (1, 3, 7):
        s = solve_action(act, seed=seed)
        assert s["p_x"] > 100, s
        assert s["p_x"] < s["p_y"], s


def test_struct_field_hoisted_as_subfield_locals(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(STRUCT_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    assert "a__p_x" in root_c and "a__p_y" in root_c  # sub-fields p.x/p.y -> locals


@pytest.mark.c_toolchain
def test_chost_struct_field_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(STRUCT_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    vals = {k: int(v) for k, v in (x.split("=") for x in run.stdout.split())}
    assert vals["x"] > 100 and vals["x"] < vals["y"], run.stdout


def test_lower_body_unrolls_foreach():
    # exec-body `foreach (arr[i]) { ... }` unrolls per element, rebinding arr[i] to
    # the flattened locals.
    import zuspec.ir.core as ir
    from pssc.targets.sw_lower import _lower_body
    body = [ir.StmtForeach(
        target=ir.ExprRefLocal(name="i"),
        iter=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="arr"),
        body=[ir.StmtExpr(expr=ir.ExprCall(
            func=ir.ExprRefUnresolved(name="print"),
            args=[ir.ExprSubscript(
                value=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="arr"),
                slice=ir.ExprRefLocal(name="i"))]))])]
    slot_map = {("arr", 0): "arr_0", ("arr", 1): "arr_1", ("arr", 2): "arr_2"}
    out = _lower_body(body, "a__", slot_map, {})
    assert len(out) == 3
    assert [s.expr.args[0].name for s in out] == ["a__arr_0", "a__arr_1", "a__arr_2"]


# Exec-body `foreach (arr[i])` now parses (pssparser ProceduralStmtForeach) and is
# unrolled when the action is inlined into a traversing activity.
FOREACH_BODY_SRC = """
component pss_top {
    action A {
        rand bit[8] arr[3];
        constraint { foreach (arr[i]) { arr[i] == i + 1; } }
        exec body {
            foreach (arr[i]) {
                print("v=%d", arr[i]);
            }
        }
    }
    action Root {
        A a;
        activity { a; }
    }
}
"""


def test_foreach_body_parses_to_ir(tmp_path):
    # The exec-body foreach reaches the IR as a StmtForeach over self.arr.
    import zuspec.ir.core as ir
    p = tmp_path / "m.pss"
    p.write_text(FOREACH_BODY_SRC)
    ctx = pssc.driver.translate(str(p))
    body = [f for f in ctx.ir_context.type_m["pss_top::A"].functions
            if f.name == "body"][0]
    assert len(body.body) == 1
    fe = body.body[0]
    assert isinstance(fe, ir.StmtForeach)
    assert fe.target.name == "i"
    assert fe.iter.attr == "arr"


def test_foreach_body_unrolls_in_activity(tmp_path):
    # When A is traversed by Root's activity, its foreach unrolls per element.
    p = tmp_path / "m.pss"
    p.write_text(FOREACH_BODY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    root_c = (out / "pss_top__root.c").read_text()
    # 3 elements x 2 emitted variants (sync + coroutine) = 6 unrolled prints
    assert root_c.count('v=%d') == 6


@pytest.mark.c_toolchain
def test_foreach_body_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(FOREACH_BODY_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    # constraint arr[i] == i+1, so the unrolled foreach prints 1,2,3
    assert [l for l in run.stdout.split("\n") if l][:3] == ["v=1", "v=2", "v=3"]


MATCH_BODY_SRC = """
component pss_top {
    action A {
        rand bit[8] x;
        constraint { x == 5; }
        exec body {
            match (x) {
                [0..4]: { print("low"); }
                [5..9]: { print("mid"); }
                default: { print("hi"); }
            }
        }
    }
}
"""


def test_match_body_parses_to_ir(tmp_path):
    # Exec-body `match` now parses (pssparser ProceduralStmtMatch) and reaches the
    # IR as a StmtMatch with one case per choice (incl. default).
    import zuspec.ir.core as ir
    p = tmp_path / "m.pss"
    p.write_text(MATCH_BODY_SRC)
    ctx = pssc.driver.translate(str(p))
    body = [f for f in ctx.ir_context.type_m["pss_top::A"].functions
            if f.name == "body"][0]
    assert len(body.body) == 1
    m = body.body[0]
    assert isinstance(m, ir.StmtMatch)
    assert m.subject.attr == "x"
    assert len(m.cases) == 3


def test_presolve_nested_struct(tmp_path):
    # rand struct whose sub-field is itself a struct: paths o.i.x resolve, and both
    # the inner struct's and outer struct's own constraints apply
    from pssc.targets.sw_solve import solve_action
    p = tmp_path / "m.pss"
    p.write_text("""
    component pss_top {
        struct Inner { rand bit[8] x; constraint x > 200; }
        struct Outer { rand Inner i; rand bit[8] z; constraint z < 10; }
        action A { rand Outer o; constraint o.i.x < 250; }
    }
    """)
    act = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::A"]
    for seed in (1, 3, 7):
        s = solve_action(act, seed=seed)
        assert 200 < s["o_i_x"] < 250, s   # Inner x>200 AND Outer's o.i.x<250
        assert s["o_z"] < 10, s


def test_presolve_array_of_struct(tmp_path):
    from pssc.targets.sw_solve import solve_action
    p = tmp_path / "m.pss"
    p.write_text("""
    component pss_top {
        struct P { rand bit[8] x; rand bit[8] y; constraint x < y; }
        action A { rand P pts[2]; constraint pts[0].x > 100; constraint pts[1].x > 100; }
    }
    """)
    act = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::A"]
    s = solve_action(act, seed=1)
    for i in (0, 1):
        assert s[f"pts_{i}_x"] > 100, s
        assert s[f"pts_{i}_x"] < s[f"pts_{i}_y"], s   # P's own x<y per element


NESTED_STRUCT_SRC = """
component pss_top {
    struct Inner { rand bit[8] x; constraint x > 200; }
    struct Outer { rand Inner i; constraint i.x < 250; }
    action Leaf { rand Outer o; exec body { print("oix=%d", o.i.x); } }
    action Root { Leaf a; activity { a; } }
}
"""


@pytest.mark.c_toolchain
def test_chost_nested_struct_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(NESTED_STRUCT_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    val = int(run.stdout.split("=")[1])
    assert 200 < val < 250, run.stdout


def test_presolve_foreach_constraint(tmp_path):
    # foreach (arr[i]) { ... } is unrolled per element; index arithmetic folds
    from pssc.targets.sw_solve import solve_action
    p = tmp_path / "m.pss"
    p.write_text("""
    component pss_top {
        action A {
            rand bit[8] r[4];
            rand bit[8] s[4];
            constraint { foreach (r[i]) { r[i] > 10; r[i] < 20; } }
            constraint { foreach (s[i]) { s[i] == i * 2; } }
        }
    }
    """)
    act = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::A"]
    sol = solve_action(act, seed=1)
    assert all(10 < sol[f"r_{i}"] < 20 for i in range(4)), sol
    assert [sol[f"s_{i}"] for i in range(4)] == [0, 2, 4, 6], sol


FOREACH_SRC = """
component pss_top {
    action Leaf {
        rand bit[8] arr[3];
        constraint { foreach (arr[i]) { arr[i] > 50; arr[i] < 60; } }
        exec body { print("a0=%d", arr[0]); print("a2=%d", arr[2]); }
    }
    action Root { Leaf a; activity { a; } }
}
"""


@pytest.mark.c_toolchain
def test_chost_foreach_constraint_runs(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(FOREACH_SRC)
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host-presolved", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    vals = {k: int(v) for k, v in (x.split("=") for x in run.stdout.split())}
    assert all(50 < v < 60 for v in vals.values()), run.stdout


def test_presolve_unique_constraint(tmp_path):
    # `unique {a, b, c}` -> dv-solve all-different
    from pssc.targets.sw_solve import solve_action
    src = """
    component pss_top {
        action A {
            rand bit[8] a; rand bit[8] b; rand bit[8] c;
            constraint a in [1..3]; constraint b in [1..3]; constraint c in [1..3];
            constraint unique {a, b, c};
        }
    }
    """
    p = tmp_path / "m.pss"
    p.write_text(src)
    act = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::A"]
    for seed in (1, 2, 5, 9):
        s = solve_action(act, seed=seed)
        vals = [s["a"], s["b"], s["c"]]
        assert len(set(vals)) == 3, s            # all distinct
        assert all(1 <= v <= 3 for v in vals), s


def test_presolve_conditional_constraint(tmp_path):
    # `if (T) {A} else {B}` conditional constraints are solved
    from pssc.targets.sw_solve import solve_action
    src = """
    component pss_top {
        action A {
            rand bit[8] x; rand bit[8] y;
            constraint x in [16..20];
            constraint if (x > 15) { y > 4; } else { y < 2; }
        }
    }
    """
    p = tmp_path / "m.pss"
    p.write_text(src)
    a = pssc.driver.translate(str(p)).ir_context.type_m["pss_top::A"]
    for seed in (1, 2, 3, 5):
        s = solve_action(a, seed=seed)
        assert 16 <= s["x"] <= 20, s
        assert (s["y"] > 4) if s["x"] > 15 else (s["y"] < 2), s


def test_chost_emits_root_harness(tmp_path):
    # the generated main.c instantiates the root action and drives its activity
    p = tmp_path / "m.pss"
    p.write_text(ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out))
    main_c = (out / "main.c").read_text()
    assert "pss_top__Root_body(&root, &tb)" in main_c
    assert "zsp_timebase_run(&tb)" in main_c


@pytest.mark.c_toolchain
def test_chost_activity_runs_and_prints(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    p = tmp_path / "m.pss"
    p.write_text(ACTIVITY_SRC)  # activity { a; b; }, each Leaf prints "leaf"
    out = tmp_path / "c"
    res = pssc.compile(str(p), target="c-host", output_dir=str(out))
    run = _link_and_run(out, res.outputs)
    assert run.returncode == 0, run.stderr
    # the activity ran the two traversals in order
    assert run.stdout.split() == ["leaf", "leaf"], run.stdout


def test_chost_root_action_option(tmp_path):
    # --root-action selects the harness target explicitly
    p = tmp_path / "m.pss"
    p.write_text(ACTIVITY_SRC)
    out = tmp_path / "c"
    pssc.compile(str(p), target="c-host", output_dir=str(out),
                 root_action="Root")
    assert "pss_top__Root_body(&root, &tb)" in (out / "main.c").read_text()
