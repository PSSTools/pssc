"""Image and test tasks: the pyvm image a simulation loads, and a test run.

``Image`` runs ``pssc build --image`` over its ``pssSource`` inputs and emits
the ``.zbc`` as a ``pssVmImage`` fileset. A simulation's run step needs the
image; its build step needs only SystemVerilog (the ``op-model-sv-vm`` facade,
or a TB). A PSS edit then rebuilds the image and reruns the simulation, but
never recompiles it. ``Image`` reports ``changed=False`` when the new image
has the same bytes as the last one (the default generation 0 makes a build
reproducible), so a no-op edit reruns nothing downstream.

``Test`` runs ``pssc test --image`` on that image in pyvm and fails when a
test fails. Both run pssc in a child process (``run_child``'s reasoning); the
child's output goes to ``<rundir>/<task>.log``.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from typing import List

from .common import (compute_memento, gather_extra_args, gather_pss_sources,
                     _missing_cached_files)

#: Filetype of an ``Image`` task's output fileset.
PSS_VM_IMAGE_FILETYPE = "pssVmImage"
#: Filetype of a ``Test`` task's CTRF report.
CTRF_FILETYPE = "ctrfJson"


def _get(params, name, default=None):
    return getattr(params, name, default)


def image_args(p) -> List[str]:
    """``pssc build`` options from the ``Image`` task's params."""
    args: List[str] = []
    if _get(p, "root", ""):
        args += ["--root", _get(p, "root")]
    if _get(p, "api", False):
        args.append("--api")
    if not _get(p, "sources_in_image", True):
        args.append("--no-sources")
    return args


def pssc_test_args(p) -> List[str]:
    """``pssc test`` options from the ``Test`` task's params."""
    args: List[str] = []
    if _get(p, "k", ""):
        args += ["-k", _get(p, "k")]
    if _get(p, "platform", ""):
        args += ["--platform", _get(p, "platform")]
    if _get(p, "seed", 0):
        args += ["--seed", str(_get(p, "seed"))]
    return args


def gather_images(input) -> List[str]:
    """Absolute paths of every ``pssVmImage`` file in ``input.inputs``."""
    images: List[str] = []
    for fs in input.inputs:
        if getattr(fs, "filetype", None) != PSS_VM_IMAGE_FILETYPE:
            continue
        basedir = getattr(fs, "basedir", "") or ""
        for f in getattr(fs, "files", []) or []:
            images.append(f if os.path.isabs(f) else os.path.join(basedir, f))
    return images


async def _run_pssc(argv: List[str], out_dir: str, name: str) -> int:
    """``python -m pssc ARGV`` in ``out_dir``, output to ``<name>.log``.
    No daemon: a flow's pssc runs must not leave one behind."""
    env = dict(os.environ, PSSC_NO_DAEMON="1")
    with open(os.path.join(out_dir, name + ".log"), "w") as logf:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pssc", *argv, cwd=out_dir, env=env,
            stdout=logf, stderr=asyncio.subprocess.STDOUT)
        return await proc.wait()


def _md5(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


async def Image(ctxt, input):
    from dv_flow.mgr import FileSet, TaskDataResult

    sources = gather_pss_sources(input)
    if not sources:
        ctxt.error("pssc.Image: no 'pssSource' inputs found (needs a "
                   "std.FileSet with type: pssSource)")
        return TaskDataResult(status=1, changed=True)
    opts = image_args(input.params) + gather_extra_args(input)
    name = (_get(input.params, "image", "") or input.name) + ".zbc"
    out_dir = input.rundir
    os.makedirs(out_dir, exist_ok=True)
    out = FileSet(src=input.name, filetype=PSS_VM_IMAGE_FILETYPE,
                  basedir=os.path.abspath(out_dir), files=[name])

    memento = compute_memento(sources, "pssc-image", {"args": opts})
    prev = input.memento if isinstance(input.memento, dict) else None
    if (not input.changed and prev is not None
            and prev.get("hash") == memento
            and not _missing_cached_files([out])):
        return TaskDataResult(changed=False, output=[out], memento=prev)

    rc = await _run_pssc(["build", *sources, "--image", name, *opts],
                         out_dir, "pssc_build")
    if rc != 0:
        ctxt.error("pssc.Image: pssc build exited with status %d (see %s)"
                   % (rc, os.path.join(out_dir, "pssc_build.log")))
        return TaskDataResult(status=1, changed=True)
    digest = _md5(os.path.join(out_dir, name))
    changed = prev is None or prev.get("image") != digest
    return TaskDataResult(status=0, changed=changed, output=[out],
                          memento={"hash": memento, "image": digest})


async def Test(ctxt, input):
    from dv_flow.mgr import FileSet, TaskDataResult

    images = gather_images(input)
    if len(images) != 1:
        ctxt.error("pssc.Test: needs exactly one 'pssVmImage' input "
                   "(a pssc.Image task), found %d" % len(images))
        return TaskDataResult(status=1, changed=True)
    out_dir = input.rundir
    os.makedirs(out_dir, exist_ok=True)
    ctrf = "pssunit-ctrf.json"
    rc = await _run_pssc(
        ["test", "--image", images[0], "--ctrf", ctrf,
         *pssc_test_args(input.params), *gather_extra_args(input)],
        out_dir, "pssc_test")
    log = os.path.join(out_dir, "pssc_test.log")
    if rc != 0:
        ctxt.error("pssc.Test: %s (see %s)" % (
            "tests failed" if rc == 1 else "pssc test exited with status %d"
            % rc, log))
    output = []
    if os.path.exists(os.path.join(out_dir, ctrf)):
        output.append(FileSet(src=input.name, filetype=CTRF_FILETYPE,
                              basedir=os.path.abspath(out_dir),
                              files=[ctrf]))
    return TaskDataResult(status=1 if rc != 0 else 0, changed=True,
                          output=output)
