"""T3.9: `pssc.Image` builds the image a simulation loads, `pssc.Test` runs it.

A simulation's run step depends on the image, never on generated SV: an edit
that leaves the image's bytes alone reports the image unchanged, so nothing
downstream reruns; a body edit changes it.
"""
import asyncio
import json
import os
import types
from pathlib import Path

from .conftest import _make_loader, requires_dfm
from pssc.dvflow import devenv

_SUITE = """\
import pssunit_pkg::*;
component pss_top {
    rt_c rt;
    int count;
    function void bump() { count = count + 1; }
    @pssunit_pkg::test
    function void t_ok() { bump(); }
    @pssunit_pkg::test
    function void t_two() { bump(); bump(); }
}
"""


def _run(tmp_path, *, test=None, **image_params):
    """Run fileset -> pssc.Image [-> pssc.Test] in `tmp_path/rundir`; returns
    (status, the image task's node, the last node, errors)."""
    from dv_flow.mgr import TaskSetRunner
    from dv_flow.mgr.task_graph_builder import TaskGraphBuilder
    from dv_flow.mgr.task_data import SeverityE

    rundir = str(Path(tmp_path) / "rundir")
    errors = []
    builder = TaskGraphBuilder(_make_loader(errors), rundir)
    runner = TaskSetRunner(rundir)
    runner.builder = builder

    def collect(task, reason):
        for m in getattr(getattr(task, "result", None), "markers", None) or []:
            if m.severity == SeverityE.Error and str(m.msg) not in errors:
                errors.append(str(m.msg))
    runner.add_listener(collect)

    src = builder.mkTaskNode("std.FileSet", name="pss_src", type="pssSource",
                             base=str(tmp_path), include=["suite.pss"],
                             needs=[])
    image = builder.mkTaskNode("pssc.Image", name="img", needs=[src],
                               **image_params)
    last = image
    if test is not None:
        last = builder.mkTaskNode("pssc.Test", name="t", needs=[image], **test)
    asyncio.run(runner.run(last))
    return runner.status, image, last, errors


def _write(tmp_path, text):
    (Path(tmp_path) / "suite.pss").write_text(text)


@requires_dfm
def test_image_task_writes_the_image(tmp_path):
    _write(tmp_path, _SUITE)
    status, image, _, errors = _run(tmp_path)
    assert status == 0, errors
    [fs] = image.output.output
    assert fs.filetype == devenv.PSS_VM_IMAGE_FILETYPE
    assert fs.files == ["img.zbc"]
    path = os.path.join(fs.basedir, "img.zbc")
    from pssc.devenv import build_cmd
    model, items, meta = build_cmd.load(path)
    assert {it.name for it in items} >= {"t_ok", "t_two"}


@requires_dfm
def test_image_unchanged_when_its_bytes_are(tmp_path):
    _write(tmp_path, _SUITE)
    p = {"sources_in_image": False}
    assert _run(tmp_path, **p)[0] == 0
    # a comment (after the code, so no line moves) changes the source but,
    # without sources in the image, not the image: nothing downstream (the
    # sim run) need rerun
    _write(tmp_path, _SUITE + "// edited\n")
    status, image, _, errors = _run(tmp_path, **p)
    assert status == 0, errors
    assert image.output.changed is False
    # a body edit changes the image
    _write(tmp_path, _SUITE.replace("bump(); bump();", "bump();"))
    status, image, _, errors = _run(tmp_path, **p)
    assert status == 0, errors
    assert image.output.changed is True


@requires_dfm
def test_test_task_runs_the_image(tmp_path):
    _write(tmp_path, _SUITE)
    status, _, t, errors = _run(tmp_path, test={"k": "t_two"})
    assert status == 0, errors
    [fs] = t.output.output
    assert fs.filetype == devenv.CTRF_FILETYPE
    report = json.loads(Path(fs.basedir, fs.files[0]).read_text())
    names = [x["name"] for x in report["results"]["tests"]]
    assert len(names) == 1 and "t_two" in names[0]


@requires_dfm
def test_test_task_fails_with_a_failing_test(tmp_path):
    _write(tmp_path, _SUITE.replace(
        "function void t_two() { bump(); bump(); }",
        "function void t_two() { bump(); rt.check_eq_int(count, 7, \"wrong\"); }"))
    status, _, _, errors = _run(tmp_path, test={})
    assert status != 0
    assert any("tests failed" in e for e in errors), errors


def test_option_mapping():
    p = types.SimpleNamespace(root="r", api=True, sources_in_image=False)
    assert devenv.image_args(p) == ["--root", "r", "--api", "--no-sources"]
    t = types.SimpleNamespace(k="x", platform="fake", seed=3)
    assert devenv.pssc_test_args(t) == ["-k", "x", "--platform", "fake",
                                        "--seed", "3"]
