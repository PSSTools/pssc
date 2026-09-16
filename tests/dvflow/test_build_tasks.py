"""Build tasks compile a trivial PSS model and emit correctly-typed filesets."""
import os
import types

import pytest

from .conftest import requires_dfm, run_task, filesets_by_type
from pssc.dvflow import build


# A component with an action AND an operation. The operation is not incidental:
# progseq refuses to emit an export API with zero operations (that shape is how
# every front-end defect in its history presented), so a model without one is
# not a valid input to those tasks.
_MODEL = """
component pss_top {
    int count;
    function void bump() { count = count + 1; }
    action Entry { exec body { print("hi\\n"); } }
}
"""


# --- override-keyword contract (no DFM runtime needed) -----------------------

def _params(**kw):
    return types.SimpleNamespace(**kw)


def test_sv_native_override_keywords():
    ov = build._sv_native_overrides(_params(
        projection="oo_api", package_name="zsp_gen_pkg",
        single_file=True, runtime=True))
    assert ov == {"sv_projection": "oo_api", "sv_package_name": "zsp_gen_pkg",
                  "sv_multi_file": True, "rt_pkg": True}


def test_progseq_override_keywords():
    ov = build._sv_progseq_overrides(_params(
        root="pss_top", package_name="", core_copy=True))
    assert ov["progseq_root"] == "pss_top"
    assert "progseq_package" not in ov   # empty package omitted
    assert ov["progseq_core_copy"] is True

    cov = build._c_progseq_overrides(_params(
        root="pss_top", prefix="", link_style="vtable",
        reg_style="bitfields", header_only=False, core_copy=True))
    assert cov["progseq_root"] == "pss_top"
    assert cov["c_link_style"] == "vtable"
    assert cov["c_reg_style"] == "bitfields"


def test_py_progseq_override_keywords():
    ov = build._py_progseq_overrides(_params(
        root="pss_top", module="", await_style="async", core_copy=True))
    assert ov["progseq_root"] == "pss_top"
    assert "py_module" not in ov        # empty module omitted
    assert ov["py_await"] == "async"
    assert ov["progseq_core_copy"] is True

    # The default is the sync form, matching the CLI's `--py-await` default.
    assert build._py_progseq_overrides(_params(
        root="pss_top", module="dma", await_style="sync",
        core_copy=True))["py_await"] == "sync"


def test_c_runtime_override_optionality():
    # Nothing set -> empty (each target keeps its own defaults)
    assert build._c_runtime_overrides(_params(
        root_action="", runtime_solve=False, presolve=False)) == {}
    assert build._c_runtime_overrides(_params(
        root_action="Entry", runtime_solve=True, presolve=False)) == {
            "root_action": "Entry", "runtime_solve": True}


def test_tasks_table_covers_all_entry_points():
    # Every TASKS entry has a matching module-level coroutine.
    import inspect
    for name in build.TASKS:
        fn = getattr(build, name, None)
        assert fn is not None and inspect.iscoroutinefunction(fn)


# --- end-to-end build runs (DFM, no simulator) -------------------------------

@requires_dfm
def test_sv_native_emits_systemverilog(tmp_path):
    status, output, errors = run_task(
        tmp_path, "pssc.SvNative", pss_text=_MODEL, export_action=["Entry"])
    assert status == 0, errors
    by_ft = filesets_by_type(output)
    assert "systemVerilogSource" in by_ft
    fs = by_ft["systemVerilogSource"][0]
    assert any(f.endswith(".sv") for f in fs.files)
    for f in fs.files:
        assert os.path.isfile(os.path.join(fs.basedir, f))


@requires_dfm
@pytest.mark.parametrize("task,filetype", [
    ("pssc.CHost", "cSource"),
    ("pssc.CHostPresolved", "cSource"),
    ("pssc.CEmbedded", "cSource"),
    ("pssc.CEmbeddedPresolved", "cSource"),
    ("pssc.SvDpi", "cSource"),
])
def test_c_family_emits_csource(tmp_path, task, filetype):
    status, output, errors = run_task(
        tmp_path, task, pss_text=_MODEL, export_action=["Entry"])
    assert status == 0, errors
    by_ft = filesets_by_type(output)
    assert filetype in by_ft
    fs = by_ft[filetype][0]
    assert fs.files


@requires_dfm
def test_pysource_emit_repr_manifest(tmp_path):
    status, output, errors = run_task(
        tmp_path, "pssc.PySource", pss_text=_MODEL, emit="repr")
    assert status == 0, errors
    by_ft = filesets_by_type(output)
    assert "pythonSource" in by_ft
    fs = by_ft["pythonSource"][0]
    assert fs.files
    for f in fs.files:
        assert os.path.isfile(os.path.join(fs.basedir, f))


@requires_dfm
def test_svprogseq_with_root(tmp_path):
    status, output, errors = run_task(
        tmp_path, "pssc.SvProgSeq", pss_text=_MODEL, root="pss_top")
    assert status == 0, errors
    assert "systemVerilogSource" in filesets_by_type(output)


@requires_dfm
def test_cppprogseq_with_root(tmp_path):
    status, output, errors = run_task(
        tmp_path, "pssc.CppProgSeq", pss_text=_MODEL, root="pss_top")
    assert status == 0, errors
    assert "cppSource" in filesets_by_type(output)


@requires_dfm
@pytest.mark.parametrize("await_style", ["sync", "async"])
def test_opmodelpy_with_root(tmp_path, await_style):
    status, output, errors = run_task(
        tmp_path, "pssc.OpModelPy", pss_text=_MODEL, root="pss_top",
        await_style=await_style)
    assert status == 0, errors
    by_ft = filesets_by_type(output)
    assert "pythonSource" in by_ft, list(by_ft)
    names = {os.path.basename(f) for f in by_ft["pythonSource"][0].files}
    assert "pssc_rt.py" in names
    # The async form ships the second runtime half; the sync one must not, or
    # a reader is left wondering which of the two channel implementations the
    # module is actually built on.
    assert ("pssc_rt_async.py" in names) is (await_style == "async"), names


@requires_dfm
@pytest.mark.skipif(__import__("shutil").which("gcc") is None,
                    reason="gcc required to build the bridge .so")
def test_svdpibridge_emits_sv_and_dpi(tmp_path):
    status, output, errors = run_task(
        tmp_path, "pssc.SvDpiBridge", pss_text=_MODEL, export_action=["Entry"])
    assert status == 0, errors
    by_ft = filesets_by_type(output)
    # The trampoline SV package plus the compiled DPI shared library.
    assert "systemVerilogSource" in by_ft
    assert "systemVerilogDPI" in by_ft
    so = by_ft["systemVerilogDPI"][0]
    assert any(f.endswith(".so") for f in so.files)
    for f in so.files:
        assert os.path.isfile(os.path.join(so.basedir, f))


@requires_dfm
def test_progseq_requires_root(tmp_path):
    # Empty root -> pssc raises ValueError -> task surfaces an error marker.
    status, output, errors = run_task(
        tmp_path, "pssc.CProgSeq", pss_text=_MODEL)
    assert status != 0
    assert any("root" in e.lower() for e in errors)


@requires_dfm
def test_cprogseq_with_root(tmp_path):
    status, output, errors = run_task(
        tmp_path, "pssc.CProgSeq", pss_text=_MODEL, root="pss_top")
    assert status == 0, errors
    by_ft = filesets_by_type(output)
    assert "cSource" in by_ft


@requires_dfm
def test_no_pss_input_errors(tmp_path):
    status, output, errors = run_task(tmp_path, "pssc.SvNative")
    assert status != 0
    assert any("pssSource" in e for e in errors)


@requires_dfm
def test_memento_skips_recompile(tmp_path):
    # First run compiles; a second identical run should report no change.
    from dv_flow.mgr import TaskSetRunner
    from dv_flow.mgr.task_graph_builder import TaskGraphBuilder
    from .conftest import _make_loader
    import asyncio
    from pathlib import Path

    rundir = str(Path(tmp_path) / "rundir")
    src = Path(tmp_path) / "model.pss"
    src.write_text(_MODEL)

    def build_and_run():
        errors = []
        builder = TaskGraphBuilder(_make_loader(errors), rundir)
        runner = TaskSetRunner(rundir)
        runner.builder = builder
        fileset = builder.mkTaskNode(
            "std.FileSet", name="pss_src", type="pssSource",
            base=str(tmp_path), include=["model.pss"], needs=[])
        node = builder.mkTaskNode(
            "pssc.SvNative", name="task", needs=[fileset],
            export_action=["Entry"])
        out = asyncio.run(runner.run(node))
        return out

    out1 = build_and_run()
    out2 = build_and_run()
    # Second run: the build task should be up-to-date (changed=False).
    assert out2 is not None
    assert out2.changed is False


def test_memento_hit_still_recompiles_when_an_output_is_gone():
    """The memento hash answers "would recompiling produce the same files?".
    It does not answer "are those files still there".

    Handing back a cached fileset naming a deleted file makes this task report
    success while its *consumer* fails to find the source -- the error surfaces
    one task downstream, which is the wrong place to debug it from.

    This is a SECOND cache: dv-flow-mgr has its own up-to-date check and may
    already have decided to invoke us. It can decide to run while we
    short-circuit, so the existence check has to exist in both layers or the
    outer one is unenforceable. That is exactly how this was found -- dfm
    logged "not up-to-date", ran the task, and the file still did not come back.

    Tested against the helper directly rather than end-to-end: reaching this
    branch requires the runner to hand back a populated memento *while* dfm has
    decided to re-run, which the unit harness does not reliably reproduce -- an
    end-to-end version of this test passed with the fix reverted, i.e. proved
    nothing.
    """
    import os
    from pssc.dvflow.common import _missing_cached_files

    class _FS:
        def __init__(self, basedir, files):
            self.basedir, self.files = basedir, files

    here = os.path.dirname(__file__)
    present = os.path.basename(__file__)

    # All present -> nothing missing, so the cache is still usable.
    assert _missing_cached_files([_FS(here, [present])]) == []

    # One gone -> reported, so the caller recompiles.
    assert _missing_cached_files(
        [_FS(here, [present, "definitely_not_here.sv"])]
    ) == [os.path.join(here, "definitely_not_here.sv")]

    # A fileset carrying no files contributes nothing rather than raising.
    assert _missing_cached_files([_FS(here, [])]) == []
    assert _missing_cached_files([_FS(None, None)]) == []


def test_the_memento_shortcut_is_actually_guarded_by_the_existence_check():
    """The helper being correct is worth nothing if the cache path does not call
    it, and a unit test of the helper cannot tell the difference -- neutering
    the CALL leaves such a test green.

    So assert the wiring: the early-return that reuses cached filesets must be
    reached only when nothing is missing. Checked against the source because the
    alternative -- reproducing "dfm decided to re-run while still handing us a
    populated memento" in-process -- is exactly the setup that made an earlier
    end-to-end version of this test vacuous.
    """
    import inspect
    from pssc.dvflow import common

    src = inspect.getsource(common.run_build)
    assert "_missing_cached_files(cached)" in src, (
        "the cached-fileset shortcut no longer consults the existence check")

    # The reuse must be inside the "nothing missing" branch, not before it.
    guard = src.index("_missing_cached_files(cached)")
    reuse = src.index("return TaskDataResult(changed=False, output=cached")
    assert guard < reuse, (
        "cached filesets are returned before the existence check runs")


def test_style_param_forwarded():
    """P5b.T2: `style:` reaches the target as `--style` would.

    Omitted when empty rather than passed as `""`: a flow written before styles
    existed must keep generating exactly what it did, and pinning it to a name
    -- even 'default' -- makes that a claim about the registry rather than
    about the target's own default.
    """
    ov = build._c_progseq_overrides(_params(
        root="pss_top", prefix="", style="acme", link_style="direct",
        reg_style="bitfields", header_only=False, core_copy=True))
    assert ov["c_style"] == "acme"

    ov = build._c_progseq_overrides(_params(
        root="pss_top", prefix="", style="", link_style="vtable",
        reg_style="bitfields", header_only=False, core_copy=True))
    assert "c_style" not in ov


def test_the_style_param_is_declared_in_the_flow():
    """A param `build.py` reads and `flow.yaml` does not declare is a param
    that silently takes its default on every real invocation."""
    import pathlib
    import re
    text = (pathlib.Path(build.__file__).resolve().parent
            / "flow.yaml").read_text()
    # inside the OpModelC task's `with:` block
    block = re.search(r"name: OpModelC\b.*?(?=\n  - name: )", text, re.S)
    assert block and re.search(r"^\s+style:\s*$", block.group(0), re.M), \
        "OpModelC declares no `style:` param"
