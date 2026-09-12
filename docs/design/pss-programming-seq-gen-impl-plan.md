# PSS → SV Programming-Sequence Generation — Implementation, Test & Documentation Plan

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **Draft for review**
Date: 2026-06-18
Companion design: `design/pss-programming-seq-gen-design.md`
References: `examples/export/programming_seqs/` (validated `wb_dma_sv_proto.sv`,
`PROTOTYPE PASS`), `packages/dv-flow-libhdlsim/` (multi-sim pytest_dfm pattern),
`packages/pytest-dfm/` (the `DvFlow` fixture).

This plan turns the design into sequenced, individually-acceptable work items,
a multi-simulator test strategy built on `pytest_dfm`, and a `docs/*.rst`
documentation set.

---

## 0. Guiding principles

- **The reference is the oracle.** `wb_dma_sv_proto.sv` already passes in
  Verilator. Every phase's acceptance is "generated output converges on that
  reference and still passes its self-checking testbench."
- **Reuse the existing SV pipeline.** New work is mostly new lowering modules
  under `src/pssc/targets/sv/` + one `Target` + one packaged `.sv` + one CLI
  subcommand. We do not add new infrastructure (`LoweringContext`, SV IR,
  `emit_files`, the `Target` registry, and `lower_imports`/`lower_stmts` already
  exist).
- **Test like the HDL packages do.** `pytest_dfm` + `dv-flow-libhdlsim` give us
  the same test run across Verilator / Questa / VCS / Xcelium; we parametrize
  over whatever is on `PATH` (Verilator is in `ivpm.yaml`, so CI always has one).
- **Decisions are frozen** (design §10): `_if` interface suffix, `task`-only,
  `addr_handle_t = bit[63:0]`, `create()` takes import handle + root `ctor`
  args, registers internal.

---

## 1. Implementation plan (phased)

Each phase lists **files**, **work**, and **acceptance**. Phases are ordered so
that each ends at a compilable / testable artifact.

### Phase 0 — IR reconnaissance (de-risk the one open question) — ✅ DONE

The design's single open item (§10.A): does the IR distinguish runtime/target
vs. solve functions, and export vs. import?

**Findings (2026-06-18), confirmed by translating the DMA model:**

- `ir.Function` carries `is_import`, `is_target`, `is_solve`, `is_async`,
  `process_kind`, `returns`, `args`, `body`. The open question is **resolved** —
  no inference needed for kind.
- For the DMA model: `ctor` → `is_solve=True` (→ constructor); the operations
  (`configure_channel`, `mem_to_mem_copy*`) are plain component functions
  (`is_import=is_target=is_solve=False`) → these are the **export operations**.
  The bus `read*/write*` are *not* user functions — they live in the built-in
  `reg_c`/`reg_group_c` and are realized by the hand-written core
  `pss_mem_if`, so there is nothing per-model to lower for them.
- **Classification is by `super`** (a `DataTypeRef.ref_name`): `reg_group_c`
  → register group; `packed_s` → register value struct; else regular component.
- The IR is richer than assumed and removes most of Phase 3's hard work:
  - `DataTypeRegister`: `register_value_type`, `access_mode`
    (`READWRITE`/`READONLY`/`WRITEONLY`), `size_bits`, `template_args`.
  - `DataTypeRegisterGroup`: **`offset_map`** (name→byte offset, *pre-computed*
    for scalar children, e.g. `{'CSR':0,'INT_MSK_A':4,…}`), `fields`,
    `functions`.
  - `DataTypeArray`: `element_type`, `size`.
  - `DataTypeInt` for scalar consts (`NUM_CHANNELS`).
- **Gap found & fixed:** `match` arms with a *bare* statement (e.g.
  `["CSR"]: return 0x00;`, as the register offset functions use) crashed
  `ast2ir._translate_stmt_match` (`'ProceduralStmtReturn' has no children`).
  Added `_translate_stmt_body()` helper (handles block *or* bare statement);
  250 unit tests still green. This was a prerequisite to translating any reg
  model with offset functions.
- **Remaining Phase-3 detail:** `offset_map` covers scalar children only; **array**
  offsets (`channels`, `_reserved`) are not in it. Need to derive base+stride —
  either evaluate `get_offset_of_instance_array(name, 0)` and `(name, 1)` with a
  tiny IR interpreter, or check whether an existing register-lowering helper
  already does this (the `test_register_phase*` suite suggests address handling
  exists). Tracked in Phase 3.

- **Acceptance:** ✅ predicate is data-driven off the IR flags; classification
  by `super`. A formal `func_kind`/`classify` unit test lands with Phase 3 in
  `tests/progseq/test_classify.py`.

### Phase 1 — Core SV package + locator subcommand — ✅ DONE

Shipped `src/pssc/share/sv/pssc_reg_pkg.sv` (lints clean in Verilator); added
`pssc sv-core-path[ --file]` (`cli.py` `_cmd_sv_core_path` + `sv_core_dir()`).
Also removed a stray `print("TODO: annotate field")` in
`packages/zuspec-dataclasses/.../decorators.py` that polluted stdout for every
pssc command (and broke the scriptable path output). Tests:
`tests/progseq/test_core_pkg.py` (4, green).

- **Files:**
  - `src/pssc/share/sv/pssc_reg_pkg.sv` (new) — lift the `pss_reg_pkg` core from
    `wb_dma_sv_proto.sv`, renamed per §5/§10.1 (`pss_mem_if`, `reg_c`,
    `addr_handle_t`, `reg_access_e`).
  - `src/pssc/cli.py` — add `sv-core-path` subparser + `_cmd_sv_core_path`
    (prints `importlib.resources.files("pssc")/"share"/"sv"`; `--file` prints the
    `.sv`).
  - `pyproject.toml` — already globs `share/sv/*.sv`; no change needed (verify).
- **Acceptance:** `pssc sv-core-path` prints an existing dir; `--file` prints an
  existing `.sv`; the package compiles standalone in Verilator.

### Phase 2 — `sv-progseq` target skeleton — ✅ DONE

`ProgSeqTarget` (`progseq_tgt.py`, registered with alias `progseq`); backend-
neutral `progseq_model.py` (`func_kind`, `comp_kind`, `walk_tree`); `run()`
resolves `--root` (bare or qualified, with a helpful candidate list on miss) and
dispatches to `progseq_gen.generate()` (walks + classifies; emits in later
phases). Verified on the DMA model: `--root dma_engine_c` → 1 regular + 2
reg-group components, exit 0. Tests: `tests/progseq/test_classify.py` (8, green).
Note: the design's `wb_dma_c` is the PSS `dma_engine_c`; the rename is cosmetic
(use `--root dma_engine_c` for now).

- **Files:**
  - `src/pssc/targets/progseq_tgt.py` (new) — `class ProgSeqTarget(Target)`,
    `name = "sv-progseq"`, `add_args` (`--root` required, `--package`,
    `--no-core-copy`, `--single-file`), `run()` stub that resolves the root
    component from `ctx` and errors clearly if `--root` is missing/unknown.
  - `src/pssc/targets/__init__.py` — register it (alias `progseq`).
- **Acceptance:** `pssc targets` lists `sv-progseq`; `pssc compile -t sv-progseq
  --root wb_dma_c …` runs to a (still empty) emit and exits 0; `test_cli`/
  `test_target_registry`-style unit tests pass.

### Phase 3 — Register-model emission — ✅ DONE

`src/pssc/targets/sv/lower_reg_model.py` emits value structs (reversed) +
reg-group classes; `progseq_gen.generate()` wraps them in the package, imports
`pssc_reg_pkg`, writes `<pkg>.sv`, and copies the core. Verified on the DMA
model: output matches the reference register model (offsets, `READONLY`, channel
stride `0x20`, `_reserved` gap omitted) and **lints clean in Verilator** with the
core. Offsets: scalars from `offset_map`; arrays via an affine evaluator over
`get_offset_of_instance_array` (`base=f(0)`, `stride=f(1)-f(0)`).

Two front-end fixes were required and made along the way (both with the bare-
statement `match` arms the register offset functions use):
1. `match` arm bodies that are a single statement (Phase 0).
2. `match` arm **patterns** were dropped (all became wildcard); now translated
   to `PatternValue`/`PatternOr` (`_translate_match_pattern`). 250 unit tests
   still green. Note `PatternValue.value` is an `ExprConstant` (unwrap `.value`).

Tests: `tests/progseq/test_generate.py` (4, green).

- **Files:**
  - `src/pssc/targets/sv/lower_reg_model.py` (new):
    - `classify_components(root)` — walk the tree, tag `reg_group_c` subclasses
      vs. regular (shared with Phase 4).
    - `lower_packed_struct(struct)` — `packed_s` → SV packed struct, fields
      reversed.
    - `lower_reg_group(comp)` — group class with `reg_c #(...)` + nested-group
      fields; offsets lifted from `get_offset_of_instance[_array]` to
      `localparam`s; `(bus, base)` constructor folds `base+offset`, loops arrays.
- **Acceptance:** generating from `dma_regs.pss` yields `dma_regs_pkg` whose
  structs and group classes are structurally identical to
  `dma_regs_sv_proto.sv` (task form); a golden text/AST comparison passes and the
  emitted package compiles.

### Phase 4 — Export API + implementation classes — ✅ DONE

`src/pssc/targets/sv/lower_progseq.py`: `emit_export_api` (one `pure virtual
task` per `EXPORT_OP`; `int` return → `output int status`; every arg explicit
`input`; SV-keyword args renamed, e.g. `priority`→`priority_`) and `emit_impl`
(a focused `_BodyEmitter` translating the procedural subset: struct-field
assigns, register read→task-output rewrite `x=r.read()`→`r.read(x)`,
`repeat{}while`→`forever..break`, `return v`→`status=v; return;`). Constructor
derives from the `ctor` params. Output matches the reference engine; lints clean.
Impl operations are emitted **`virtual`** (required by stricter sims; see Phase
6). IR field-name notes: `StmtAssign.targets`, `StmtExpr.expr`,
`ExprSubscript.slice`, `StmtRepeatWhile.condition`.

- **Files:**
  - `src/pssc/targets/sv/lower_progseq.py` (new):
    - `lower_export_api(comp)` → `interface class <comp>_if` (one `task` per
      runtime fn; `int` ret → `output int status`; explicit `input` after any
      `output`; SV-keyword args renamed via `mangle_name`; one accessor per
      regular sub-component).
    - `lower_impl(comp)` → `class <comp>_impl implements <comp>_if`
      (register-group + child-impl fields; constructor from root `ctor`; bodies
      via existing `lower_stmts`/`lower_exprs` with the register-access rewrite
      `regs.X.read()` → `m_regs.X.read(tmp)` and `repeat{}while` → `forever …
      break`).
- **Acceptance:** generated `wb_dma_if` + `wb_dma_impl` match the
  reference engine (modulo `_if`); the engine package compiles against the core
  + register packages.

### Phase 5 — Import API + component handle/factory — ✅ DONE

Added to `lower_progseq.py`: `emit_import_api` (`interface class
<root>_import_if extends pss_mem_if` + any engine imports — none for DMA) and
`emit_component` — a **single class named after the component** (`<comp>`) that
both redirects the import API to `m_imp` (forwarding the 8 frozen primitives —
the duck-typing bridge) **and** exposes the static `create(imp, <ctor params>)`.
No separate adapter or `*_factory_c` class. `IMP_T` defaults to
`<root>_import_if`. Users write `<comp>#(my_bus_t)::create(bus, base)`. Full
package lints clean; end-to-end still `WB_DMA PROTOTYPE PASS`.

- **Files:**
  - extend `lower_progseq.py`:
    - compose per-component `_imp_if` (from `lower_imports.lower_import_interface`)
      into `interface class <root>_import_if extends pss_mem_if[, …]`.
    - `lower_import_adapter(root)` → `class <root>_imp_adapter_c #(IMP_T)
      implements <root>_import_if` forwarding to `m_imp`.
    - `lower_factory(root)` → `class <root>_factory_c #(IMP_T)` with
      `static function <root>_if create(IMP_T imp, <root ctor params>)`.
- **Acceptance:** generated factory/adapter match the reference; a user object
  that only matches signatures (no `implements`) wires through.

### Phase 6 — File emission & end-to-end wiring — ✅ DONE (Verilator)

`progseq_gen.generate()` assembles the package (core import → value structs →
reg groups → per-component export API+impl → root import/adapter/factory),
writes `<pkg>.sv`, and copies `pssc_reg_pkg.sv`. End-to-end: generate from the
two PSS sources, compile core + generated + the split-out testbench
(`tests/progseq/data/wb_dma_tb.sv`, mock + self-check using the *generated*
names, duck-typed bus), run → **`WB_DMA PROTOTYPE PASS`** (all 5 checks incl.
real data movement + error path).

Multi-sim status: **Verilator green** (the focus / CI gate). The generated code
also passes **Vivado xsim** (after emitting impl ops `virtual` and making the
testbench locals `automatic` — both fixed). **Questa** compiles clean (`vlog`
0 errors) but is license-blocked in this environment. `test_sim_wb_dma.py`
currently restricts to `vlt`; broaden when enabling the full matrix.

- **Files:**
  - `src/pssc/targets/sv/emit_files.py` — add a progseq emit entry that writes
    the generated package(s) and (unless `--no-core-copy`) copies
    `pssc_reg_pkg.sv` next to them, in dependency order
    (`pssc_reg_pkg` → value structs/reg groups → engine).
  - `src/pssc/targets/progseq_tgt.py` — wire `run()` to phases 3–5 + emit.
- **Acceptance:** `pssc compile -t sv-progseq --root wb_dma_c dma_regs.pss
  dma_engine.pss -o out/` produces a directory that compiles and runs the
  reference testbench to `WB_DMA PROTOTYPE PASS` (this is the headline
  integration test, §2.3).

### Phase 7 (stretch) — multi-language hooks

Per design §10.6 (C-embedded, C++/host). Out of scope to *implement*, but Phase
3's `classify_components` and the function-kind predicate (Phase 0) are written
language-neutral so a future `c-progseq` reuses them. **Acceptance:** those two
helpers live in a backend-agnostic module (e.g. `targets/progseq_model.py`), not
inside `targets/sv/`.

---

## 2. Test plan

A three-layer pyramid. Layers 1–2 are pure-Python (fast, no simulator, run on
every CI push). Layer 3 is the multi-simulator `pytest_dfm` suite.

### 2.0 Layout & markers

```
tests/progseq/
  conftest.py                 # sims discovery + progseq_dvflow fixture + gen helper
  test_core_pkg.py            # L1: sv-core-path subcommand, package presence
  test_classify.py            # L1: component classification + func-kind predicate
  test_generate.py            # L2: run target, assert emitted structure / golden
  test_sim_wb_dma.py          # L3: multi-sim compile+run -> PROTOTYPE PASS
  data/
    dma_regs.pss              # copied/symlinked from examples/export/programming_seqs
    dma_engine.pss
    wb_dma_tb.sv              # testbench-only: tb_pkg mock + top, imports generated names
    golden/                   # expected emitted .sv for golden compare (optional)
```

- Add `testpaths` for `tests/progseq` (or rely on default discovery) and a
  `progseq` marker in `pytest.ini`; reuse the existing **`sim`** marker for
  Layer 3 so `-m "not sim"` skips simulator tests on a bare machine.
- **Split the reference.** `wb_dma_sv_proto.sv` is monolithic (core + regs +
  engine + tb). Extract `wb_dma_tb.sv` = `tb_pkg` mock bus + `top` self-check,
  importing the *generated* package/interface names (`wb_dma_pkg`,
  `pss_mem_if`, `wb_dma_factory_c`, …). The generated packages replace the
  hand-written `pss_reg_pkg`/`dma_regs_pkg`/`wb_dma_pkg`. Keeping the tb separate
  is what lets the same self-check validate generator output.

### 2.1 Layer 1 — unit (pure Python)

- `test_core_pkg.py`: `pssc sv-core-path[ --file]` resolves to existing paths;
  the shipped `pssc_reg_pkg.sv` contains `pss_mem_if` and `reg_c`.
- `test_classify.py`: build the DMA IR (as `tests/sim/sv/conftest.py` already
  does via `build_ir`), assert `classify_components` tags `dma_regs_c`/
  `dma_channel_regs_c` as register groups and `wb_dma_c` as regular; assert
  `func_kind` over each DMA function (Phase 0 predicate).
- `test_generate.py` (structure half): call `driver.compile([...],
  target="sv-progseq", opts=…)` into a tmp dir; assert the expected files and
  that key declarations are present (`interface class wb_dma_if`,
  `class wb_dma_factory_c`, `extends pss_mem_if`, explicit `input` after
  `output`). Regex/AST assertions, not full-text diff, to stay robust.

### 2.2 Layer 2 — golden (optional, behind a flag)

Full-text compare of emitted `.sv` against `data/golden/*.sv` with an
`--update-golden` escape hatch. Use sparingly (brittle); prefer the structural
assertions above + the Layer-3 behavioral check as the real gate.

### 2.3 Layer 3 — multi-simulator simulation (`pytest_dfm`)

The behavioral gate: **generate → compile generated SV + core + tb → run →
assert `WB_DMA PROTOTYPE PASS`** across every simulator on `PATH`.

**Sim discovery** mirrors `dv-flow-libhdlsim` (`hdlsim_available_sims`):

```python
# tests/progseq/conftest.py
import os, shutil, pytest, pytest_dfm

def available_sims():
    sims = []
    for exe, tag in {"verilator":"vlt", "vsim":"mti", "vcs":"vcs",
                     "xsim":"xsm", "xmvlog":"xcm"}.items():
        if shutil.which(exe):
            sims.append(tag)
    return sims

@pytest.fixture
def progseq_gen(tmp_path):
    """Run the sv-progseq target on the DMA model; return the output dir."""
    from pssc import driver
    data = os.path.join(os.path.dirname(__file__), "data")
    out  = tmp_path / "gen"
    driver.compile(
        [os.path.join(data, "dma_regs.pss"), os.path.join(data, "dma_engine.pss")],
        target="sv-progseq",
        opts=_opts(root="wb_dma_c", output_dir=str(out)))
    return out
```

**The test** uses the `pytest_dfm` `DvFlow` fixture + the `hdlsim.<sim>` tasks
(exactly the `tests/sv/test_zsp_rt_pkg.py` / libhdlsim pattern):

```python
@pytest.mark.sim
@pytest.mark.parametrize("sim", available_sims())
def test_wb_dma_progseq(dvflow, progseq_gen, sim):
    data = os.path.join(os.path.dirname(__file__), "data")

    src = dvflow.mkTask("std.FileSet", name="src",
                        type="systemVerilogSource",
                        base=str(progseq_gen), include="*.sv")          # core + generated
    tb  = dvflow.mkTask("std.FileSet", name="tb",
                        type="systemVerilogSource",
                        base=data, include="wb_dma_tb.sv")
    img = dvflow.mkTask(f"hdlsim.{sim}.SimImage", name="img",
                        top=["top"], needs=[src, tb])
    run = dvflow.mkTask(f"hdlsim.{sim}.SimRun", name="run", needs=[img])

    status, out = dvflow.runTask(run)
    assert status == 0
    log = _read_sim_log(out)
    assert "WB_DMA PROTOTYPE PASS" in log
```

(Equivalently, drive it through a generated `flow.dv` + `dvflow.runFlow`, as the
libhdlsim *system* test does — keep one in-process `mkTask` test and optionally
one `flow.dv` subprocess test for end-to-end packaging coverage.)

**Negative path:** a second sim test that injects a poison source address and
asserts the error-path message / non-zero status surfaces (the reference tb
already exercises this — assert its specific line appears).

### 2.4 CI matrix

- GitHub Actions: a job per available simulator. Verilator is fetched via
  `ivpm.yaml` (`edapack/verilator-bin`), so the **Verilator lane is the always-on
  gate**; Questa/VCS/Xcelium lanes run where licensed (self-hosted), guarded by
  `available_sims()` returning empty → tests skip cleanly.
- Pure-Python layers (1–2) run on every push (no sim) and are the fast gate.
- Add `-m "not sim"` to the default fast job; a separate job runs `-m sim`.

---

## 3. Documentation plan (`docs/*.rst`)

Sphinx project already exists (`docs/index.rst` toctree: quickstart, api,
pss_to_sv). Add three `.rst` files and wire them in.

### 3.1 `docs/progseq.rst` — user guide (primary deliverable)

Outline:

1. **What it is** — turn a PSS component tree into a reusable SV programming API
   (register model + operations); one-paragraph motivation + the WB DMA example.
2. **Quick start** —
   ```
   pssc compile -t sv-progseq --root wb_dma_c dma_regs.pss dma_engine.pss -o out/
   pssc sv-core-path --file        # add the core package to your compile order
   ```
3. **The generated API** — package contents table (export `_if`, impl `_c`,
   import `_if`, adapter, factory, register model); the hierarchical accessor
   view; the `_if`/`_c` naming convention.
4. **Supplying the bus** — implement (or signature-match) `pss_mem_if`
   (`read*/write*`, 8/16/32/64, task form, `addr_handle_t = bit[63:0]`); the
   duck-typed adapter so existing BFMs need no `implements`.
5. **Constructing & calling** —
   ```systemverilog
   wb_dma_if dma = wb_dma_factory_c#(my_bus_c)::create(bus, 64'h4000_0000);
   dma.mem_to_mem_copy(status, 5, src, dst, 4096);
   ```
   note `create()` = import handle + root `ctor` args; status as `output`.
6. **Worked example** — point at `examples/export/programming_seqs/` and the
   `WB_DMA PROTOTYPE PASS` testbench.
7. **Limitations** — SV only this phase (C/C++ planned); registers internal;
   `task`-only/front-door timing.

### 3.2 `docs/progseq_design.rst` — architecture reference

Condensed, cross-linked summary of the design doc + the PSS→SV mapping table,
plus `automodule` autodoc for the new lowering modules (`lower_reg_model`,
`lower_progseq`, `progseq_model`, `progseq_tgt`). Source of truth stays the
`design/` docs; this is the rendered, code-linked view.

### 3.3 `docs/cli.rst` updates (or new section)

Document the new CLI surface: the `sv-progseq` target (and its `--root`,
`--package`, `--no-core-copy`, `--single-file` options) and the `sv-core-path`
subcommand. If CLI docs currently live in `docs/cli.md`, add the equivalent
`.rst` or convert; ensure the new target shows under `pssc targets`.

### 3.4 Wiring

- Add `progseq`, `progseq_design` to the `docs/index.rst` toctree.
- Extend `docs/api.rst` with autodoc stanzas for the new modules.
- A short note in `docs/quickstart.rst` linking to the programming-API guide.

---

## 4. Sequencing, milestones & dependencies

| Milestone | Phases | Gate | Status |
| --- | --- | --- | --- |
| **M1 Core shipped** | 0, 1 | `sv-core-path` works; core pkg compiles; func-kind predicate unit-tested | ✅ done |
| **M2 Register model** | 2, 3 | generated `dma_regs_pkg` compiles; structural unit tests green | ✅ done |
| **M3 Engine API** | 4, 5 | generated engine compiles against core+regs | ✅ done |
| **M4 End-to-end** | 6 | **Verilator** sim test → `WB_DMA PROTOTYPE PASS` (L3) | ✅ done |
| **M5 Docs + multi-sim** | docs, 7 | `docs/*.rst` published; L3 green on every available sim; classify/func-kind helpers backend-agnostic | 🟡 docs done (`docs/progseq.rst`, `docs/progseq_design.rst` in the toctree; `docs/cli.md`/`targets.md` updated; Sphinx builds clean); classify/func-kind backend-neutral (`progseq_model.py`); multi-sim still Verilator-only |

**Progress (2026-06-19):** Phases 0–6 complete. `pssc compile -t sv-progseq
--root dma_engine_c …` emits a self-contained SV package that passes the
reference self-check in Verilator. 20 progseq tests + 956 unit tests green.
Docs published: `docs/progseq.rst` (user guide) + `docs/progseq_design.rst`
(architecture) in the toctree, `docs/cli.md`/`targets.md` updated, Sphinx builds
clean. Remaining: broadening the sim matrix beyond Verilator.

**Naming/structure refinements (post-M4, all green):**
- Export API renamed `<comp>_api_if` → **`<comp>_if`** ("our interface to the
  component"); memory-access interface `mem_access_if` → **`pss_mem_if`**;
  generated impl drops the trailing `_c`.
- The separate `*_imp_adapter_c` and `*_factory_c` were merged, then the
  `<comp>_impl` was **folded in too**: the component is now **one class `<comp>`**
  that `implements <comp>_if, <comp>_import_if` — export operations + import
  redirect + static `create()`. It builds the register model with `this` as the
  bus (`<comp>` is-a `pss_mem_if`), so accesses route `m_regs → this → m_imp`.
  `IMP_T` defaults to `<comp>_import_if`. Usage: `<comp>#(my_bus_t)::create(bus, base)`.

Dependencies: M1 has no repo-internal blockers beyond the Phase-0 IR check. M2–M3
reuse `LoweringContext`, `lower_imports`, `lower_stmts`/`lower_exprs`,
`emit_files`. M4 depends on the split-out `wb_dma_tb.sv`. The multi-sim lanes
depend only on simulators being installed (Verilator always; others optional).

---

## 5. Risks & mitigations

- **IR lacks a solve/target flag** (Phase 0). *Mitigation:* inference rule +
  unit test; escalate to an IR change request only if inference is ambiguous.
- **`lower_stmts`/`lower_exprs` were built for the solver style** and may not
  cover the register-access / polling idioms. *Mitigation:* the rewrites are
  small and localized; if reuse is awkward, add a thin progseq-specific
  statement lowering rather than forcing the solver path.
- **Golden tests brittle.** *Mitigation:* prefer structural assertions +
  behavioral sim gate; keep golden behind `--update-golden`.
- **Reference drift** between the hand `wb_dma_sv_proto.sv` and generated output
  as the `_if` rename lands. *Mitigation:* rename the reference + split out
  `wb_dma_tb.sv` in M1 so the testbench tracks the frozen names from the start.
- **Simulator variance** (interface-class / task support). *Mitigation:* the
  reference already passes on Verilator; `available_sims()` skips absent tools;
  document the minimum SV-2012 feature set required.

---

## 6. Definition of done

- `pssc compile -t sv-progseq --root wb_dma_c …` emits a self-contained,
  compilable SV package set.
- The split `wb_dma_tb.sv` self-check prints `WB_DMA PROTOTYPE PASS` on every
  simulator available in CI (Verilator gating).
- Unit + generation tests green in the no-sim fast job.
- `docs/progseq.rst`, `docs/progseq_design.rst`, and the CLI doc updates are
  published and linked from the toctree.
- `classify_components` + `func_kind` live in a backend-neutral module, ready for
  the future C/C++ backends.
