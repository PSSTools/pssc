"""dv-flow task: the SystemVerilog model API from THIS repo's pssc.

The bench's own `pssc.OpModelSv` task belongs to the pssc installed in the
fw-wb-dma packages. This one runs the pssc this example lives in, as a
subprocess with that repo's Python (`PSSC_ROOT`, `PSSC_PYTHON`; env.sh sets
both), on target `op-model-sv-vm --with-gen`. The package it writes holds the
generated model AND the pyvm facade, and `<root>_root_sel::create` picks one
at run time from `+pss_impl=gen|vm` (default gen).

Output, in compile order: `pssvm_pkg.sv` (copied from pssc; pure SV unless
`PSSVM_DPI` is defined), `pssc_reg_pkg.sv`, `<package>.sv`.

With `dpi` on (the default), the image can also start the VM: pyhdl-if's
`pyhdl_if.sv` and DPI library (from the `hdl_if` of the Python running
dv-flow) come first, and the shim's `pssvm_hif_api_pkg.sv` /
`pssvm_hif_pkg.sv` follow `pssvm_pkg.sv`. `PSSVM_DPI` stays undefined: it
selects the native C engine, which the pyhdl-if one does not need (and whose
open-array import Verilator 5.052 cannot compile).

With `native` on (dpi then off), the image runs the model on pssc's C engine
instead and holds no Python: `PSSVM_DPI` is defined on the package, and the
engine library (`pssc sv-pssvm-path --lib`) is linked in its place.
"""
import asyncio
import hashlib
import os
import shutil


def _params(input, name, default):
    v = getattr(input.params, name, None)
    return default if v in (None, "") else v


def _sources(input):
    out = []
    for fs in input.inputs:
        if getattr(fs, "filetype", None) != "pssSource":
            continue
        for f in fs.files:
            out.append(f if os.path.isabs(f) else os.path.join(fs.basedir, f))
    return out


async def _engine_lib(py, root):
    """The C engine's DPI library, built (and cached) by pssc on demand."""
    env = dict(os.environ, PSSC_NO_DAEMON="1",
               PYTHONPATH=os.path.join(root, "src"))
    proc = await asyncio.create_subprocess_exec(
        py, "-m", "pssc", "sv-pssvm-path", "--lib", env=env,
        stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    return out.decode().strip() if proc.returncode == 0 else None


async def OpModelSvVm(ctxt, input):
    from dv_flow.mgr import FileSet, TaskDataResult
    root = os.environ.get("PSSC_ROOT")
    py = os.environ.get("PSSC_PYTHON")
    if not root or not py:
        print("pssvm_gen: PSSC_ROOT/PSSC_PYTHON unset (source env.sh)")
        return TaskDataResult(status=1, changed=True)
    rundir = input.rundir
    os.makedirs(rundir, exist_ok=True)
    rootc = _params(input, "root", "wb_dma_c")
    pkg = _params(input, "package_name", rootc + "_pkg")
    srcs = _sources(input)
    # `verbatim`: the bench names enum items as the model does (WB_DMA_DONE).
    items = _params(input, "enum_items", "verbatim")
    argv = ["compile", *srcs, "-t", "op-model-sv-vm", "--with-gen",
            "--sv-enum-items", items, "--root", rootc,
            "--package-name", pkg, "-o", rundir]

    h = hashlib.md5(" ".join(argv).encode())
    for f in srcs:
        with open(f, "rb") as fp:
            h.update(fp.read())
    pssvm_sv = os.path.join(root, "src/pssc/share/sv/pssvm/pssvm_pkg.sv")
    with open(pssvm_sv, "rb") as fp:
        h.update(fp.read())

    native = _params(input, "native", False)
    dpi = _params(input, "dpi", True) and not native
    h.update(b"dpi" if dpi else b"")
    h.update(b"native" if native else b"")
    hif = ["pssvm_hif_api_pkg.sv", "pssvm_hif_pkg.sv"] if dpi else []
    for f in hif:
        with open(os.path.join(os.path.dirname(pssvm_sv), f), "rb") as fp:
            h.update(fp.read())
    sig = h.hexdigest()

    files = ["pssvm_pkg.sv", *hif, "pssc_reg_pkg.sv", pkg + ".sv"]
    out = [FileSet(src=input.name, filetype="systemVerilogSource",
                   basedir=rundir, files=files, incdirs=[rundir],
                   defines=["PSSVM_DPI"] if native else [])]
    if native:
        lib = await _engine_lib(py, root)
        if lib is None:
            print("pssvm_gen: `pssc sv-pssvm-path --lib` failed")
            return TaskDataResult(status=1, changed=True)
        out.append(ctxt.mkDataItem("hdlsim.SimElabArgs", dpilibs=[lib]))
    if dpi:
        import hdl_if
        share = os.path.join(hdl_if.share(), "dpi")
        lib = hdl_if.get_entry()
        out = [FileSet(src=input.name, filetype="systemVerilogSource",
                       basedir=share, files=["pyhdl_if.sv"], incdirs=["."],
                       defines=["HAVE_PYHDL_IF"]),
               *out]
        # The library rides an elab-args item, not a systemVerilogDPI
        # FileSet: the image links it either way, but hdlsim's Verilator run
        # task rejects a DPI FileSet that reaches it through the image.
        out.append(ctxt.mkDataItem("hdlsim.SimElabArgs", dpilibs=[lib]))
    prev = input.memento or {}
    if (not input.changed and prev.get("sig") == sig
            and all(os.path.isfile(os.path.join(rundir, f)) for f in files)):
        return TaskDataResult(changed=False, output=out, memento=prev)

    env = dict(os.environ, PSSC_NO_DAEMON="1",
               PYTHONPATH=os.path.join(root, "src"))
    with open(os.path.join(rundir, "pssc_compile.log"), "w") as logf:
        proc = await asyncio.create_subprocess_exec(
            py, "-m", "pssc", *argv, cwd=rundir, env=env,
            stdout=logf, stderr=asyncio.subprocess.STDOUT)
        rc = await proc.wait()
    if rc != 0:
        print("pssvm_gen: pssc failed; see %s/pssc_compile.log" % rundir)
        return TaskDataResult(status=1, changed=True)
    for f in ["pssvm_pkg.sv", *hif]:
        shutil.copy(os.path.join(os.path.dirname(pssvm_sv), f),
                    os.path.join(rundir, f))
    return TaskDataResult(status=0, changed=True, output=out,
                          memento={"sig": sig})


async def PssvmImage(ctxt, input):
    """The pyvm image of the same sources (`pssc build --api --image`), for
    the `+pss_impl=vm` test cells. Output: an `hdlsim.SimRunArgs` item with
    `+pss_impl=vm +pss_img=<rundir>/<root>.zbc`, so a run task that needs
    this one starts the model in pyvm with no other change. Param `impl`
    (default `vm`; `native` for the C engine) sets `+pss_impl`."""
    from dv_flow.mgr import TaskDataResult
    root = os.environ.get("PSSC_ROOT")
    py = os.environ.get("PSSC_PYTHON")
    if not root or not py:
        print("pssvm_gen: PSSC_ROOT/PSSC_PYTHON unset (source env.sh)")
        return TaskDataResult(status=1, changed=True)
    rundir = input.rundir
    os.makedirs(rundir, exist_ok=True)
    rootc = _params(input, "root", "wb_dma_c")
    img = os.path.join(rundir, rootc + ".zbc")
    srcs = _sources(input)
    impl = _params(input, "impl", "vm")
    argv = ["build", *srcs, "--root", rootc, "--api", "--image", img]
    if impl == "native":
        # no Python in the simulator: refuse Python doubles up front
        argv.append("--native")

    h = hashlib.md5(" ".join(argv).encode())
    for f in srcs:
        with open(f, "rb") as fp:
            h.update(fp.read())
    sig = h.hexdigest()
    out = [ctxt.mkDataItem("hdlsim.SimRunArgs",
                           plusargs=["pss_impl=" + impl, "pss_img=" + img])]
    prev = input.memento or {}
    if not input.changed and prev.get("sig") == sig and os.path.isfile(img):
        return TaskDataResult(changed=False, output=out, memento=prev)

    env = dict(os.environ, PSSC_NO_DAEMON="1",
               PYTHONPATH=os.path.join(root, "src"))
    with open(os.path.join(rundir, "pssc_build.log"), "w") as logf:
        proc = await asyncio.create_subprocess_exec(
            py, "-m", "pssc", *argv, cwd=rundir, env=env,
            stdout=logf, stderr=asyncio.subprocess.STDOUT)
        rc = await proc.wait()
    if rc != 0:
        print("pssvm_gen: pssc build failed; see %s/pssc_build.log" % rundir)
        return TaskDataResult(status=1, changed=True)
    return TaskDataResult(status=0, changed=True, output=out,
                          memento={"sig": sig})
