# Design: DV Flow Manager (DFM) Tasks for `pssc`

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **Draft for review**
Author: (pssc maintainers)
Scope: Add a DFM task package that exposes `pssc` as first-class tasks in DFM
task graphs — both for *referencing* the shared SystemVerilog/C/C++ core source
that `pssc` ships, and for *building* each `pssc` output style from PSS source.

---

## 1. Motivation & Goals

Today, wiring `pssc` output into a DFM simulation flow is done in Python test
glue (see `tests/sim/sv/conftest.py::run_sim`): the test calls `pssc.compile(...)`
directly, drops the generated files into a directory, then hand-builds a
`std.FileSet` → `hdlsim.<sim>.SimImage` → `SimRun` graph with `TaskGraphBuilder`.

That works, but it means:

- PSS compilation is *not* a node in the task graph, so it gets no incremental
  rebuild, no dependency tracking, no marker propagation, and can't be reused
  outside Python.
- The bundled core source (`pssc_reg_pkg.sv`, `zsp_rt_pkg.sv`, the C/C++ seam
  headers) is located by reaching into `importlib.resources` from glue code.
- Every consumer re-implements the same target-specific option plumbing.

**Goal:** ship a `pssc` DFM package (loaded via the `dv_flow.mgr` entry point)
that provides:

1. **Reference tasks** — emit a `std.FileSet` for the shared core source that
   `pssc` bundles (SV `reg_pkg`/runtime, C seam headers, C++ header), so a
   downstream `SimImage`/compile step can consume them by `needs:` alone.
2. **Build tasks — one per output style** — consume PSS source filesets, run the
   corresponding `pssc` target, and emit correctly-typed output filesets
   (`systemVerilogSource`, `systemVerilogDPI`, `cSource`, …) ready for `hdlsim`
   or a C toolchain.

A flow author should be able to write, end to end:

```yaml
package:
  name: my_dv
  imports:
    - name: pssc
    - { name: hdlsim.vlt, as: sim }
  tasks:
    - name: pss
      uses: std.FileSet
      with: { type: pssSource, include: "pss/**/*.pss" }
    - name: gen            # PSS -> SystemVerilog (style 1)
      uses: pssc.SvNative
      needs: [pss]
      with: { export_action: [Entry] }
    - name: regpkg         # bundled reg_pkg / runtime SV
      uses: pssc.RegPkg
    - name: build
      uses: sim.SimImage
      needs: [gen, regpkg]
      with: { top: [zsp_test_top] }
    - name: run
      uses: sim.SimRun
      needs: [build]
```

---

## 2. Background

### 2.1 The DFM task model (as used by `dv-flow-libhdlsim`)

- A package is a YAML file (`flow.dv` or `flow.yaml` — DFM accepts both; this
  package uses **`flow.yaml`**): `package: { name, tasks, types, ... }`.
- A task is declared with `name:` (or `export:` for re-exportable), optional
  `consumes:` (filetypes / dataset types it reads from `needs`), `with:`
  (parameters), `passthrough:`, `uptodate:`, and is bound to a Python coroutine
  via `shell: pytask` + `run: <module>.<Func>` (std style) or `pytask:`
  (libhdlsim style).
- The Python entry point is `async def Func(ctxt, input) -> TaskDataResult`:
  - `input.params.<name>` — the resolved `with:` parameters.
  - `input.inputs` — input filesets/datasets gathered from `needs`
    (each has `.type`, `.filetype`, `.basedir`, `.files`, `.incdirs`, …).
  - `input.rundir`, `input.name`, `input.memento`, `input.changed`.
  - `ctxt` (`TaskRunCtxt`): `await ctxt.exec(cmd, logfile=…, logfilter=…)`,
    `ctxt.create(path, content)`, `ctxt.error/marker/info(...)`, `ctxt.env`,
    `ctxt.mkDataItem(type, **kw)`.
  - Returns `TaskDataResult(memento=, status=, output=[FileSet…], changed=,
    markers=)`.
- A `FileSet(src=, filetype=, basedir=, files=[…], incdirs=[…])` is the unit of
  file dataflow between tasks. `filetype` (e.g. `systemVerilogSource`,
  `systemVerilogDPI`, `cSource`) is what downstream `consumes:` matches on.
- The package is published to DFM through a `pyproject.toml` entry point:

  ```toml
  [project.entry-points."dv_flow.mgr"]
  <name> = "<module>.__ext__"
  ```

  where `__ext__.py` exposes `def dvfm_packages() -> dict[str, str]` mapping
  package name → absolute path of its `flow.yaml`.

### 2.2 `pssc` targets (the "output styles")

`pssc compile -t <target>` (and `pssc.compile(sources, target=…, output_dir=…,
export_actions=…, **overrides)`) supports these targets, each writing files to
`output_dir` and returning the written paths:

| Target | Description | Primary artifacts | Key options |
|---|---|---|---|
| `python` | Live `zdc` classes (in-memory) | none by default (`--emit repr/pickle` → manifest) | `emit` |
| `sv-native` (`sv`) | Pure SV classes, SV solver | `zsp_gen_pkg.sv`, `zsp_rt_pkg.sv`, `zsp_filelist.f` | `projection`, `package-name`, `single-file`, `no-rt-pkg`, `export_actions` |
| `sv-dpi` | SV facade over the c-host C runtime via DPI | `*.sv` + `executor_pkg__*.c/.h` | `export_actions` |
| `sv-dpi-bridge` | Multi-action C scenario as a DPI `.so` driven from SV | `pssc_bridge_pkg.sv` + `libpssc_scenario.so` | `export_actions` |
| `c-host` | Host C coroutine runtime, runtime solving | `executor_pkg__*.c/.h` | `export_actions` |
| `c-host-presolved` | Host C, pre-solved constraints | `executor_pkg__*.c/.h` | `export_actions` |
| `c-embedded` | Embedded C coroutine runtime, runtime solving | `executor_pkg__*.c/.h` | `export_actions` |
| `c-embedded-presolved` | Embedded C, pre-solved | `executor_pkg__*.c/.h` | `export_actions` |
| `sv-progseq` | SV programming-sequence API from a component tree | `*.sv` | `root` (**required**) |
| `c-progseq` | C programming-sequence API | `*.c/.h` | `root` (**required**) |
| `cpp-progseq` | C++ programming-sequence API | `*.cpp/.hpp` | `root` (**required**) |

Bundled core source (located today via `pssc sv-core-path` / `c-core-path` /
`cpp-core-path`, or `importlib.resources.files("pssc")`):

- `pssc/share/sv/pssc_reg_pkg.sv`, `pssc/share/sv/zsp_rt_pkg.sv`
- `pssc/share/c/pssc_mem*.h`, `pssc/share/c/zsp_bridge.{c,h}`
- `pssc/share/cpp/pssc_reg.hpp`

---

## 3. Requirements

1. A loadable DFM package named `pssc`.
2. Reference tasks that emit a `std.FileSet` for the bundled core source:
   - SV register/runtime package(s).
   - C seam headers (as an include dir) + the `zsp_bridge` C source.
   - C++ header (as an include dir).
3. One build task per `pssc` output style, each consuming PSS source and
   emitting correctly-typed output filesets.
4. Shared parameter handling (export actions, output options) factored into a
   reusable dataset type, mirroring `hdlsim.SimCompileArgs`.
5. Registration via `pyproject.toml`; `flow.yaml` + Python under package-data.
6. Incremental rebuild support (memento + `uptodate`) where practical.
7. Tests migrated to exercise the new tasks; the existing `run_sim` helper
   becomes a thin wrapper over a pure task graph.

---

## 4. Design

### 4.1 Code layout & registration

```
src/pssc/
  dvflow/
    __init__.py
    __ext__.py            # dvfm_packages() -> {"pssc": <flow.yaml path>}
    flow.yaml             # the pssc package definition (real content)
    common.py             # shared helpers: gather PSS sources, run pssc.compile,
                          #   classify outputs -> filetype, memento helpers
    reference.py          # RegPkg / CoreC / CoreCpp reference tasks
    build.py              # one run() coroutine per output-style build task
  share/sv/ …             # (unchanged) bundled core source
```

> **Note:** this package uses `flow.yaml` (not `flow.dv`). DFM accepts either
> extension; `pssc` standardizes on `flow.yaml`.

- The existing stub `src/pssc/share/flow.yaml` (just `package: { name: pssc }`)
  is **moved** to `src/pssc/dvflow/flow.yaml` and filled in with real content.
- `pyproject.toml`:

  ```toml
  [project.entry-points."dv_flow.mgr"]
  pssc = "pssc.dvflow.__ext__"

  [tool.setuptools.package-data]
  pssc = ["std_libs/*.pss", "share/sv/*.sv", "share/c/*.h", "share/c/*.c",
          "share/cpp/*.hpp", "dvflow/*.yaml"]
  ```

  `dv-flow-mgr` is added as a (dev/optional) dependency for the task layer;
  see §7 Open Questions on whether it is a hard runtime dep or an extra.

`__ext__.py`:

```python
import os
def dvfm_packages():
    here = os.path.dirname(os.path.abspath(__file__))
    return {"pssc": os.path.join(here, "flow.yaml")}
```

### 4.2 Filetype conventions

| Producer | `filetype` emitted | Consumed by |
|---|---|---|
| PSS source (author) | `pssSource` | pssc build tasks |
| SV build tasks, `RegPkg` | `systemVerilogSource` (+ `incdirs` for the pkg dir) | `hdlsim.*.SimImage`/`SimLib` |
| `sv-dpi-bridge` `.so` | `systemVerilogDPI` | `hdlsim.*.SimImage` (DPI link) |
| C build tasks, `CoreC` | `cSource` (+ `incdirs`) | C toolchain / DPI lib tasks |
| C++ build tasks, `CoreCpp` | `cppSource` (+ `incdirs`) | C++ toolchain |
| `python` build task | `pythonSource` (manifest) | downstream Python tasks |

`pssSource` is a new convention introduced by this package; authors set it via
`std.FileSet { type: pssSource }`. pssc build tasks `consumes: [{filetype:
pssSource}]`.

### 4.3 Shared option dataset

Mirror `hdlsim.SimCompileArgs`. In `flow.yaml`:

```yaml
types:
  - name: PsscArgs
    doc: Shared options forwarded to pssc.compile for build tasks
    with:
      export_action: { type: list }   # actions to expose as entry points
      args:          { type: list }   # extra raw pssc options (escape hatch)
```

Each build task `consumes: [{type: pssc.PsscArgs}]` and also exposes the most
common options directly in its own `with:` (so simple flows don't need a
separate dataset node). Target-specific options live only on the relevant task.

### 4.4 Reference tasks (shared core source)

These have **no** PSS input; they resolve the bundled path and emit a fileset.
`passthrough: all`, `consumes: none`, like `std.FileSet`.

- **`pssc.RegPkg`** — SV core. Params: `runtime` (bool, default `true` — also
  include `zsp_rt_pkg.sv`). Emits one `systemVerilogSource` FileSet with
  `basedir = <pssc>/share/sv`, `files = [pssc_reg_pkg.sv (, zsp_rt_pkg.sv)]`,
  `incdirs = [basedir]`.
- **`pssc.CoreC`** — C seam. Params: `flavor` (`direct`|`mmio`|`vtable`, default
  `mmio`) selecting which `pssc_mem_*.h` is primary; `bridge` (bool) to also
  emit `zsp_bridge.c`. Emits a `cSource`/header FileSet with `incdirs =
  [<pssc>/share/c]`.
- **`pssc.CoreCpp`** — C++ header. Emits a `cppSource` FileSet with `incdirs =
  [<pssc>/share/cpp]`.

Implementation: pure Python `run()` that calls the existing path helpers
(`pssc.cli.sv_core_dir()`, `c_core_dir()`, `cpp_core_dir()`) and builds the
FileSet. Memento keyed on `(flavor, runtime, …)`; effectively always up to date
because the bundled files only change when `pssc` itself is upgraded (memento can
record the package version to invalidate on upgrade).

### 4.5 Build tasks (one per output style)

All build tasks share one Python helper and differ only in `target` + which
options they pass. Declared in `flow.yaml`; bound to thin wrappers in `build.py`.

| Task | `pssc` target | Extra `with:` params |
|---|---|---|
| `pssc.PySource` | `python` | `emit` (`none`/`repr`/`pickle`) |
| `pssc.SvNative` | `sv-native` | `projection`, `package_name`, `single_file`, `runtime` |
| `pssc.SvDpi` | `sv-dpi` | — |
| `pssc.SvDpiBridge` | `sv-dpi-bridge` | — |
| `pssc.CHost` | `c-host` | — |
| `pssc.CHostPresolved` | `c-host-presolved` | — |
| `pssc.CEmbedded` | `c-embedded` | — |
| `pssc.CEmbeddedPresolved` | `c-embedded-presolved` | — |
| `pssc.SvProgSeq` | `sv-progseq` | `root` (**required**) |
| `pssc.CProgSeq` | `c-progseq` | `root` (**required**) |
| `pssc.CppProgSeq` | `cpp-progseq` | `root` (**required**) |

Each `consumes: [{filetype: pssSource}, {type: pssc.PsscArgs}]` and has
`with: { export_action: {type: list}, args: {type: list}, … }`.

Shared `run()` logic (`common.py`):

```python
async def run_build(ctxt, input, *, target, overrides_from_params):
    # 1. Gather PSS source files from input filesets (filetype == pssSource).
    sources = [os.path.join(fs.basedir, f)
               for fs in input.inputs
               if getattr(fs, "filetype", None) == "pssSource"
               for f in fs.files]
    if not sources:
        ctxt.error("no pssSource inputs"); return TaskDataResult(status=1)

    # 2. Collect options: export_action (+ PsscArgs datasets) + target opts.
    export_actions = list(input.params.export_action)
    for ds in input.inputs:
        if getattr(ds, "type", None) == "pssc.PsscArgs":
            export_actions += list(ds.export_action)
    overrides = overrides_from_params(input.params)

    # 3. Memento / up-to-date: hash (sources content, target, options).
    #    Skip recompile when unchanged (see §4.6).

    # 4. Run pssc into rundir. pssc.compile is synchronous Python; run it
    #    in a thread so the event loop is not blocked:
    out_dir = input.rundir
    res = await asyncio.to_thread(
        pssc.compile, sources, target=target, output_dir=out_dir,
        export_actions=export_actions or None, **overrides)

    # 5. Classify written outputs into filesets by extension and emit.
    return TaskDataResult(memento=memento, status=0,
                          output=classify_outputs(res.outputs, input.name,
                                                  out_dir),
                          changed=True)
```

`classify_outputs` maps extensions → filetypes and groups into FileSets:

| Extension | filetype | incdir? |
|---|---|---|
| `.sv`, `.svh` | `systemVerilogSource` | dir added to `incdirs` |
| `.so` | `systemVerilogDPI` | — |
| `.c` | `cSource` | — |
| `.h` | `cSource` (header) | dir added to `incdirs` |
| `.cpp`, `.cc` | `cppSource` | — |
| `.hpp` | `cppSource` (header) | dir added to `incdirs` |
| `.f` | `systemVerilogSource` filelist (or dropped) | — |
| `.py`, `.txt`, `.pkl` | `pythonSource` | — |

Rationale for **one task per style** (vs. a single `Compile` task with a
`target` param): matches the prompt's intent, keeps `consumes`/output filetypes
and target-specific options statically declared and documented per task, lets
DFM `uptodate`/skill docs describe each independently, and avoids a task whose
output filetypes vary at runtime (which complicates `consumes` matching
downstream). A single generic task can still be added later as a convenience;
the per-style tasks become thin `uses:` of it if we want to share the body.

### 4.6 Incremental build / `uptodate`

- **Memento:** record a content hash of the PSS sources plus the resolved
  option set and target. On re-run, if `input.changed` is false and the hash
  matches the memento, return the previous output filesets with `changed=False`
  and skip `pssc.compile`. (Mirrors `std.CreateFile`'s md5-memento pattern.)
- **`uptodate:`** hook can additionally short-circuit before the body runs, as
  `vlt.SimImage` does with `check_uptodate`. Initial version may rely on the
  memento check inside `run()` and add a dedicated `uptodate` later.

### 4.7 Markers / diagnostics

`pssc.compile(..., raise_on_error=True)` raises `CompileError` carrying the list
of PSS translation errors. The build wrapper catches it and converts each error
into a DFM error marker (`ctxt.error(msg)`), returning `status=1` rather than
propagating the exception — so PSS errors surface as task markers exactly like
simulator errors do today. Some targets (e.g. `sv-dpi`, `c-host`) print an
"Async-to-Sync Conversion Analysis Report" to stdout; that output is redirected
to the task logfile.

---

## 5. Example flows

**SV simulation (replaces today's `run_sim` glue):**

```yaml
tasks:
  - { name: pss, uses: std.FileSet, with: { type: pssSource, include: "*.pss" } }
  - { name: gen, uses: pssc.SvNative, needs: [pss], with: { export_action: [Entry] } }
  - { name: regpkg, uses: pssc.RegPkg }
  - { name: build, uses: sim.SimImage, needs: [gen, regpkg], with: { top: [zsp_test_top] } }
  - { name: run, uses: sim.SimRun, needs: [build] }
```

**SV-DPI-bridge (.so + SV trampoline), driven from Verilator:**

```yaml
tasks:
  - { name: pss, uses: std.FileSet, with: { type: pssSource, include: "*.pss" } }
  - { name: bridge, uses: pssc.SvDpiBridge, needs: [pss],
      with: { export_action: [Hello, World] } }
  - { name: tb, uses: std.FileSet, with: { type: systemVerilogSource, include: "tb.sv" } }
  - { name: build, uses: hdlsim.vlt.SimImage, needs: [bridge, tb], with: { top: [tb] } }
```

`pssc.SvDpiBridge` emits both the `systemVerilogSource` (`pssc_bridge_pkg.sv`)
and the `systemVerilogDPI` (`libpssc_scenario.so`), so `SimImage` links the
`.so` and compiles the trampoline package automatically.

**Programming-sequence C API:**

```yaml
tasks:
  - { name: pss, uses: std.FileSet, with: { type: pssSource, include: "regs.pss" } }
  - { name: api, uses: pssc.CProgSeq, needs: [pss], with: { root: pss_top } }
  - { name: core, uses: pssc.CoreC, with: { flavor: mmio } }
  # api + core -> a C compile task
```

---

## 6. Test plan & migration

1. **Unit (no simulator):** a test that loads the `pssc` package via
   `PackageLoader().load_rgy(["std", "pssc"])`, builds each build task over a
   tiny PSS model with `TaskGraphBuilder`, runs it, and asserts the expected
   output filesets/filetypes and that files exist in the rundir. Covers every
   output style without needing a simulator.
2. **Reference tasks:** assert `RegPkg`/`CoreC`/`CoreCpp` emit filesets pointing
   at the bundled files and that the files exist.
3. **Migrate `tests/sim/sv/conftest.py`:** re-implement `run_sim`/`build_and_run`
   so the PSS-→-SV step is a `pssc.SvNative` node and `RegPkg` replaces the
   `_get_runtime_lib_path()` hack, with `SimImage`/`SimRun` unchanged. The
   simulator-gated e2e tests (`test_export_api_e2e`, `test_bridge_e2e`,
   `test_sim_sequential`) then run on a fully graph-native flow.
4. **Bridge e2e:** `test_bridge_e2e` currently calls `pssc.compile` + raw
   `subprocess` `verilator`. Add a parallel graph-based variant using
   `pssc.SvDpiBridge` + `hdlsim.vlt.SimImage`.
5. Keep the existing direct-API tests as-is (they validate `pssc` itself); the
   new tests validate the task layer on top.

---

## 7. Open questions / decisions to confirm

1. **`dv-flow-mgr` dependency.** *Resolved: hard runtime dependency.* The
   extension is always registered — no special install target. The entry point
   and `flow.yaml` ship always; the Python task bodies still import
   `dv_flow.mgr` lazily to keep discovery cheap, and `__ext__` stays import-free.
2. **Task granularity.** Confirm one-task-per-output-style (this design) vs. a
   single `pssc.Compile` with a `target` param. *Recommendation:* per-style, per
   §4.5; optionally add a generic `Compile` later.
3. **`pssSource` filetype name.** Is `pssSource` the right convention, or should
   PSS inputs be passed by a dedicated `pssc.Sources` task instead of
   `std.FileSet { type: pssSource }`? *Recommendation:* `std.FileSet` +
   `pssSource` for consistency with how `hdlsim` consumes HDL.
4. **`python` target as a task.** It produces in-memory classes and little/no
   on-disk artifact. Include `pssc.PySource` for completeness (emit manifest) or
   omit it? *Recommendation:* include but document as niche.
5. **Filelist (`.f`) handling for `sv-native`.** Emit it as part of the fileset,
   or drop it (DFM tracks file membership itself)? *Recommendation:* drop the
   `.f` from the fileset; DFM provides ordering.

---

## 8. Implementation checklist

- [ ] `src/pssc/dvflow/{__init__,__ext__,common,reference,build}.py`
- [ ] `src/pssc/dvflow/flow.yaml` (package `pssc`: `PsscArgs` type; `RegPkg`,
      `CoreC`, `CoreCpp`; the 11 build tasks)
- [ ] Move stub `src/pssc/share/flow.yaml` → `src/pssc/dvflow/flow.yaml`
- [ ] `pyproject.toml`: `dv_flow.mgr` entry point; package-data for `dvflow/*.yaml`
      and `share/c/*.c`; `dv-flow-mgr` as a regular dependency
- [ ] `classify_outputs` + memento/uptodate helpers
- [ ] Unit tests (per-style build + reference tasks)
- [ ] Migrate `tests/sim/sv/conftest.py` to graph-native PSS→SV
- [ ] Docs: short section in `docs/` + a DFM skill doc block (like `hdlsim`)
```
