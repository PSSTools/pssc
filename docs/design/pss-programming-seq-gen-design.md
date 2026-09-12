# PSS → SystemVerilog Programming-Sequence Generation — Design

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **Draft for review**
Date: 2026-06-18
Scope: Add first-class support to `pssc` for transforming a PSS **component tree**
(rooted at a named root component) into a reusable, hierarchical **SystemVerilog
programming API** — the register model plus the driver/operation layer — backed
by a shared, hand-written core runtime package.

Feature spec: `pss_programming_seq_gen.md` (repo root).
Validated references: `examples/export/programming_seqs/` (see §3).

---

## 1. Goals & non-goals

**Goals**

- Ship a **core SV package** (reusable, IP-independent) under
  `src/pssc/share/sv/`: the memory-access interface and the generic register
  model runtime. Add a `pssc` subcommand that prints its install path so build
  flows can put it on the compile order.
- Add a **generation path** that accepts PSS source + a root-component name and
  emits one SV package implementing the component tree's programming API:
  - per-component-type **export-API** interface classes (a hierarchical view
    of the tree; component fields become accessors to child APIs);
  - an **import-API** interface class that extends the memory-access interface;
  - **implementation** classes for each component's functions;
  - a **factory** whose `create()` takes the import-API handle plus the root
    component's `ctor` arguments and returns the root export-API handle;
  - the register value structs and register-group classes for any
    `reg_group_c` subtrees.
- Make the generated code match the validated hand references byte-for-shape, so
  those references stay the gold standard for the translator.

**Non-goals (this phase)**

- Changing the PSS front end, AST→IR, or the IR node definitions.
- The existing solver-style SV target (`sv-native`) — untouched; this is a new,
  independent output style.
- Constraint solving, activities, flow objects — the programming-API output is a
  *direct* lowering of component structure + functions + registers, not a
  solved scenario.
- Auto-generating the user's bus implementation (front-door BFM / backdoor). The
  generated model calls an abstract interface the user supplies.
- Other language backends (C-embedded runtime, C++/host pure-virtual classes).
  These are **planned follow-ons** — the same scheme should target them
  symmetrically (§10.7) — but this phase delivers SystemVerilog only.

---

## 2. The two output layers, unified

The hand-built references split the problem into two cooperating schemes, each
with its own design note:

- **Register model** (`sv_reg_model_design.md`): `packed_s` structs → SV packed
  structs; `reg_group_c` components → group classes; `reg_c<T,ACC,SZ>` →
  parameterized handles with address-folding and width-based access.
- **Engine / programming sequence** (`sv_engine_model_design.md`): a regular
  component's functions → an export-API of `task`s; register access → the
  memory-access interface; the component `ctor` → a constructor; a
  factory/adapter wires a user import object to the model.

This document unifies them into **one generator over an arbitrary component
tree**. The unification is the new contribution: a real model root (e.g.
`wb_dma_c`) is a *regular* component that *contains* a `reg_group_c` subtree
(`regs : dma_regs_c`). Walking the tree, the generator classifies each component
and emits the right artifact, and stitches them into a single hierarchical API.

---

## 3. Validated references (inputs to this design)

Under `examples/export/programming_seqs/`:

| File | Role |
| --- | --- |
| `dma_regs.pss`, `dma_engine.pss` | source PSS (register model + driver) |
| `sv_reg_model_design.md` | register-model generation scheme |
| `sv_engine_model_design.md` | engine/programming-sequence generation scheme |
| `dma_regs_sv_proto.sv` | register-model prototype (function/32-bit backdoor form), Verilator `PROTOTYPE PASS` |
| `wb_dma_sv_proto.sv` | **full reference**: core + register model + engine + mock + self-check, Verilator `WB_DMA PROTOTYPE PASS` |

`wb_dma_sv_proto.sv` is the shape the generator must reproduce. Its packages map
directly onto the deliverables here: `pss_reg_pkg` → the core SV package (§5);
`dma_regs_pkg` + `wb_dma_pkg` → the generated package (§6).

Key decisions already settled in those references (carried forward verbatim):

- Memory-access primitives are **`task`s** (front-door access consumes time);
  reads return through an `output`. `reg_c`'s accessors and all export ops are
  therefore `task`s too.
- PSS `addr_handle_t` → **`bit [63:0]`** (a typedef in the core package).
- A register's bus transaction size is the value width **rounded up to the
  nearest legal size** (8/16/32/64) via `ACC_W`; `data_t` itself stays `$bits(T)`.
- The root `ctor` solve function's parameter list flows through
  `factory::create()` into the impl constructor.
- A blocking op returns its PSS `int` via an **`output status`** argument
  (`priority` and other SV keywords are renamed, e.g. `prio`).
- Codegen pitfall: SV task args **inherit the previous arg's direction**, so the
  generator must emit explicit `input` on every argument following an `output`.

---

## 4. What already exists in `pssc` to build on

The new path is a normal `pssc` code-generation target plus a small bit of CLI
and a packaged SV file. Concretely:

- **Target plug-in model** — `pssc.targets.base.Target` (ABC with `name`,
  `description`, `add_args`, `run(ctx, opts)`), registered in
  `pssc.targets.__init__._register_builtins`. `pssc compile -t <name>` already
  dispatches to it via `driver.compile`.
- **A real SV lowering pipeline** under `src/pssc/targets/sv/`:
  - `context.py` — `LoweringContext` (name mangling, type lookup, accumulation).
  - `lower_components.py` — `DataTypeComponent` → `SVClass`, including
    sub-component fields and an import-interface reference.
  - `lower_imports.py` — `lower_import_interface()` already builds a virtual
    class of `pure virtual task`/`function` decls from a component's
    `is_import` functions. **This is most of the import-API machinery.**
  - `lower_factory.py`, `lower_stmts.py`, `lower_exprs.py`, `lower_types.py`,
    `emit_files.py` — function bodies, expressions, types, file emission.
  - SV IR: `zuspec.be.sv.ir.sv` (`SVClass`, `SVFunctionDecl`, `SVTaskDecl`,
    `SVArg`, `SVClassField`, `SVRawItem`); PSS IR: `zuspec.dataclasses.ir`
    (`DataTypeComponent`, `.functions` with `.is_import`, `.super`, fields).
- **Shared SV runtime packaging** — `src/pssc/share/sv/zsp_rt_pkg.sv` is already
  shipped via `pyproject.toml` `package-data: pssc = [..., "share/sv/*.sv"]` and
  copied next to generated output by `targets/sv/emit_files.py`. The new core
  package rides the same mechanism.
- **Built-in reg types** — `reg_c`, `reg_group_c`, `addr_handle_t`,
  `packed_s<>`, `get_offset_of_instance[_array]` come from the parser's built-in
  `addr_reg_pkg` (`std_libs/addr_reg_pkg.pss` is only a doc stub). The generator
  recognizes these by name to drive register-model emission.

Net: this is mostly **new lowering modules + a new Target + one packaged .sv +
one CLI subcommand**, not new infrastructure.

---

## 5. Deliverable 1 — the core SV package (`src/pssc/share/sv/`)

A single hand-written, IP-independent package — the generalization of the
reference's `pss_reg_pkg`. Proposed name **`pssc_reg_pkg`** (namespaced under the
tool; `zsp_rt_pkg` is the solver runtime and is unrelated).

Contents (exactly the reference core, frozen as the supported runtime ABI):

```systemverilog
package pssc_reg_pkg;
  typedef bit [63:0] addr_handle_t;          // PSS addr_handle_t

  // The memory-access interface -- THE seam to the DUT. User supplies an impl.
  interface class pss_mem_if;              // reference proto: reg_access_c
    pure virtual task write8 (addr_handle_t addr, bit [7:0]  data);
    pure virtual task read8  (addr_handle_t addr, output bit [7:0]  data);
    pure virtual task write16(addr_handle_t addr, bit [15:0] data);
    pure virtual task read16 (addr_handle_t addr, output bit [15:0] data);
    pure virtual task write32(addr_handle_t addr, bit [31:0] data);
    pure virtual task read32 (addr_handle_t addr, output bit [31:0] data);
    pure virtual task write64(addr_handle_t addr, bit [63:0] data);
    pure virtual task read64 (addr_handle_t addr, output bit [63:0] data);
  endclass

  typedef enum {READWRITE, READONLY, WRITEONLY} reg_access_e;

  // Generic register handle: address folded at build, width-based dispatch.
  class reg_c #(type T = bit [31:0], reg_access_e ACC = READWRITE);
    localparam int          WIDTH = $bits(T);
    localparam int          POW2  = 1 << $clog2(WIDTH);
    localparam int          ACC_W = POW2 < 8 ? 8 : (POW2 > 64 ? 64 : POW2);
    typedef bit [WIDTH-1:0] data_t;
    // ... new(bus, addr), addr(), write/read (struct), write_val/read_val (raw)
    // exactly as in wb_dma_sv_proto.sv ...
  endclass
endpackage
```

**Naming convention (decided).** Interface classes are suffixed **`_if`**. So the
memory-access seam is `pss_mem_if` (the reference prototype's `reg_access_c`),
and the export/import APIs are `<comp>_if` / `<root>_import_if`. The component
itself is generated as a **single class named `<comp>`** that is *both* the
implementation of the export operations and the import redirect/factory — there
is no separate `<comp>_impl`. Register value/handle/group runtime classes keep
the conventional `_c` (`reg_c`, `<group>_c`). The validated reference files use
`_c` throughout and will be renamed to this convention.

**CLI: locating the package.** Build flows need the path to add it to the
compile order. Add a subcommand mirroring `cocotb-config --share` /
`verilator --getenv`:

```
$ pssc sv-core-path            # prints .../site-packages/pssc/share/sv
$ pssc sv-core-path --file     # prints .../share/sv/pssc_reg_pkg.sv
```

Implementation: `importlib.resources.files("pssc") / "share" / "sv"`, printed to
stdout (exit 0). New `_cmd_sv_core_path` in `cli.py`; one new `sub.add_parser`.
The generation target also *copies* the core package next to its output (as
`emit_files.py` already does for `zsp_rt_pkg.sv`), so a generated directory is
self-contained without needing the subcommand.

---

## 6. Deliverable 2 — the generation target

### 6.1 CLI surface

A new `Target`, registered as **`sv-progseq`** (alias `progseq`):

```
$ pssc compile -t sv-progseq --root wb_dma_c \
      dma_regs.pss dma_engine.pss -o out/
```

`add_args` contributes:

- `--root NAME` (required) — the root component type. The model is the subtree
  reachable from one implicit instance of it.
- `--package NAME` (default `<root>_pkg`) — generated package name.
- `--no-core-copy` — do not copy `pssc_reg_pkg.sv` into the output dir.
- `--single-file` / multi-file — match the `sv-native` convention.

`run(ctx, opts)` reads the PSS component from `ctx`, walks the tree, and emits
the package(s) (§6.3–§6.8) via the SV IR + `emit_files`.

### 6.2 Component classification (the tree walk)

From the root, classify every reachable component type:

- **Register group** — `super` chain includes `reg_group_c`. Emits a
  register-group class (§6.4). Its children are `reg_c` handles and nested
  groups; it has no user export API.
- **Regular component** — everything else (e.g. `wb_dma_c`). Emits an
  export-API interface (§6.5) + the single component class (§6.7). Its children
  are sub-components (regular or register-group), surfaced as accessors.

Also collect, across the whole tree: every `packed_s` struct used as a register
value type (§6.3), and every `import` function referenced (→ import API, §6.7).

### 6.3 Register value structs

Each `packed_s<>` struct → an SV `typedef struct packed`, fields emitted in
**reverse declaration order** (PSS is LSB-first, SV packed is MSB-first). This is
the existing `sv_reg_model_design.md` §2.1 rule; output equals the structs in
`dma_regs_sv_proto.sv` (validated bit positions).

### 6.4 Register-group classes

Each `reg_group_c` component → a class holding `reg_c #(...)` fields and nested
group fields. Offsets come from the PSS `get_offset_of_instance[_array]`
functions, lifted to `localparam`s; the constructor folds `base + offset` into
each child at build time, looping over arrays with the array stride. `(bus,
base)` constructor, `base : addr_handle_t`. Output equals `dma_regs_pkg` in the
reference.

### 6.5 Per-component export-API interface classes (the hierarchical view)

Each regular component type → an `interface class <comp>_if` exposing:

- **One `task` per runtime function** on the component (its operations). PSS
  `int` return → `output int status`; SV-keyword args renamed; explicit `input`
  after any `output`.
- **One accessor per sub-component**, returning that child's export-API handle
  (regular child) — so callers navigate the tree. Register-group children are
  **internal** (decided, §10): they are an implementation detail of the
  component's own operations and are never surfaced as a user API.

Example (root only has operations; its `regs` child is internal):

```systemverilog
interface class wb_dma_if;
  pure virtual task configure_channel(int channel, int prio,
                                      bit mode, bit src_if, bit dst_if);
  pure virtual task mem_to_mem_copy(output int status, input int channel,
                                    input bit [31:0] src_addr,
                                    input bit [31:0] dst_addr,
                                    input int num_bytes);
  // ... _desc, _masked ...
endclass
```

A component with sub-components `u0 : sub_c`, `u1 : sub_c` would additionally
declare `pure virtual function sub_c_if u0();` / `u1();`, letting a caller
write `root.u0().some_op(...)`.

### 6.6 Import-API interface

`interface class <root>_import_if extends pss_mem_if[, <comp>_imp_if ...];` —
the union of the memory-access interface and any per-component global
target-import (`task`) / solve-import (`function`) decls. `lower_imports.
lower_import_interface()` already produces the per-component `_imp` classes; this
step composes them via interface-class multiple inheritance. For the WB DMA tree
the engine adds no imports, so `<root>_import_if` is just `extends pss_mem_if`
with no extra methods.

### 6.7 The component class (impl + import redirect + factory, one class)

Each regular component is generated as a **single class named `<comp>`** that
folds together three roles — there is no separate `<comp>_impl`, adapter, or
`*_factory_c`:

1. **Export implementation.** It `implements <comp>_if`; each operation body is
   translated statement-for-statement, with the register-access and control-flow
   rewrites: `regs.<path>.read()` → `m_regs.<path>.read(tmp)` (task/output form);
   `repeat {..} while(c)` → `forever begin .. if (!(c)) break; end`;
   `return v` → `status = v; return;`.
2. **Import redirect.** It also `implements <root>_import_if`, forwarding every
   import primitive to a held `IMP_T m_imp` — the **bridge from the user object
   to the internal API** (duck-typed: the user object need only match
   signatures). `IMP_T` **defaults to `<root>_import_if`**; override it for a
   non-conforming object.
3. **Factory.** A `static create()` whose params are the import handle plus the
   root `ctor`'s parameters; it returns the export handle.

The key move: the class holds the register model and builds it with **`this` as
the bus**. Because `<comp>` is-a `pss_mem_if` (via the import interface), every
register access routes `m_regs → this.write32/read32 → m_imp` back out to the
user object — no separate adapter object is needed.

```systemverilog
class <comp> #(type IMP_T = <root>_import_if) implements <comp>_if, <root>_import_if;
  protected IMP_T     m_imp;
  protected dma_regs_c m_regs;

  function new(IMP_T imp, <root ctor params>);
    m_imp  = imp;
    m_regs = new(this, base);                 // `this` is-a pss_mem_if
  endfunction

  // export operations (translated bodies)
  virtual task mem_to_mem_copy(output int status, input int channel, ...); ... endtask
  // ...

  // import redirect: forward each primitive to the user object
  virtual task write32(addr_handle_t addr, bit [31:0] data); m_imp.write32(addr, data); endtask
  // ... read32/8/16/64, and any engine import funcs ...

  static function <comp>_if create(IMP_T imp, <root ctor params>);
    <comp> #(IMP_T) self = new(imp, <root ctor params>);
    return self;                              // up-cast to export API
  endfunction
endclass
```

Usage: `<comp>_if h = <comp>#(my_bus_t)::create(my_bus, base);`.

`create()` returns the **root-component export interface-class type**, and its
parameter list is `imp` followed by the root `ctor`'s parameters (empty if no
`ctor`).

---

## 7. PSS construct → SV artifact (summary)

| PSS construct | SV artifact | Package |
| --- | --- | --- |
| `addr_handle_t` | `typedef bit [63:0]` | core |
| primitive `read*/write*` import targets | `pss_mem_if` (interface class, tasks) | core |
| `reg_c<T,ACC,SZ>` | `reg_c #(T, ACC)` handle (ACC_W dispatch) | core |
| `struct S : packed_s<>` | `typedef struct packed {…}` (fields reversed) | generated |
| `component G : reg_group_c` | register-group class; offsets → localparams | generated |
| regular `component C` (functions) | `C_if` (interface) + `C` (impl + redirect + factory, one class) | generated |
| sub-component field `u : C` | accessor `C_if u()` on the parent API | generated |
| component runtime function | `task` in the export API (int ret → `output status`) | generated |
| component `import target`/`solve` fn | `task`/`function` in `*_imp_if` ⊂ import API | generated |
| root `solve function ctor(args)` | impl constructor params + `create()` params | generated |
| any consumer of imports | `*_import_if` + `<comp> #(IMP_T)` (redirect + static `create`) | generated |

---

## 8. Worked example (the reference)

Root `--root wb_dma_c` over `dma_regs.pss` + `dma_engine.pss` (component renamed
to `wb_dma_c`) produces:

```
pssc_reg_pkg            (core, copied in)   addr_handle_t, pss_mem_if, reg_c
wb_dma_c_pkg            (generated)
  ├─ value structs      dma_csr_s, dma_ch_csr_s, dma_ch_sz_s, dma_ch_swptr_s
  ├─ register groups    dma_channel_regs_c, dma_regs_c
  ├─ export API         wb_dma_c_if
  ├─ import API         wb_dma_c_import_if  (extends pss_mem_if; no extras here)
  └─ component class     wb_dma_c #(IMP_T)   (export impl + import redirect + static create)
```

This is `wb_dma_sv_proto.sv` (modulo package name and `pss_mem_if` vs.
`reg_access_c`). The generator is "correct" for this case when its output
compiles and the existing self-checking testbench prints `WB_DMA PROTOTYPE PASS`
against it.

---

## 9. Implementation plan (phased)

1. **Core package.** Add `src/pssc/share/sv/pssc_reg_pkg.sv` (lift from the
   reference core). Add `pssc sv-core-path` subcommand + a test that the file
   exists and the path resolves. *(No codegen yet.)*
2. **Register-model emission.** New `lower_reg_model.py`: classify
   `reg_group_c`, emit value structs + group classes (offsets from the PSS
   offset functions). Target stub `sv-progseq` that emits only the register
   model. Golden-compare against `dma_regs_sv_proto.sv` (task form).
3. **Export API + impls.** New `lower_progseq.py`: per-component export interface
   + impl, reusing `lower_stmts`/`lower_exprs` for bodies and the register-access
   / control-flow rewrites. Hierarchical accessors for sub-components.
4. **Import API + component handle/factory.** Compose `lower_imports` output into
   `*_import_if`; emit the single `<comp> #(IMP_T)` redirect/`create()` class with
   ctor-arg threading.
5. **End-to-end.** Wire `emit_files` to copy the core package and emit the
   generated package. CI: generate from the two PSS sources, compile with
   Verilator, run the bundled testbench, assert `WB_DMA PROTOTYPE PASS`.

Each phase has a concrete, compilable acceptance artifact (the reference files).

---

## 10. Decisions & remaining open questions

**Decided** (folded into the sections above):

1. **Naming convention.** Interface classes are suffixed `_if`, regular classes
   `_c`. The memory-access seam is `pss_mem_if` (§5); export/import APIs are
   `<comp>_if` / `<root>_import_if`; impls/adapter/factory stay `_c`. This is
   part of the shipped ABI. (The validated reference files use `_c` throughout
   and will be renamed to match.)
2. **Registers are internal.** Register-group children are never surfaced as a
   user-navigable API; they back a component's own operations only. (§6.5)
3. **`task`-only.** Only the timed `task`/64-bit form is emitted; no
   `function`/zero-time backdoor flavor and no `--backdoor` switch. (§3, §5)
4. **Base via `create()` only.** The root `ctor` parameters are accepted by
   `create()` and folded at construction; there is no late `set_handle()` rebind
   for relocation. (§6.6, §6.8)
5. **Name mangling.** All generated names go through
   `LoweringContext.mangle_name` using the §10.1 suffix scheme, so generated and
   hand-written reference code stay aligned.
6. **Multi-language parity is a goal (later).** The component-tree → API scheme
   will be extended to the **C-embedded runtime** and **C++/host pure-virtual
   classes** so non-SV testbenches get a symmetric programming API. The
   tree-walk and component classification (§6.2) are language-neutral and are
   designed to be shared; only the per-language emission differs. Out of scope
   for this phase (SV first), but the lowering is structured to not preclude it.

**Still open:**

A. **Function-kind detection.** Runtime (`task`) vs. solve (`function`), and
   export vs. import, must come from the IR. Confirm `ir.Function` carries
   `is_import` plus a solve/target flag (the SV target already reads
   `is_import`); if solve/target is not yet modeled, infer it (a body that
   blocks / accesses registers → `task`). This needs an IR check before
   implementation.

---

## 11. Files & references

- `pss_programming_seq_gen.md` — the feature spec this design elaborates.
- `examples/export/programming_seqs/sv_reg_model_design.md` — register-model scheme.
- `examples/export/programming_seqs/sv_engine_model_design.md` — engine/sequence scheme.
- `examples/export/programming_seqs/wb_dma_sv_proto.sv` — the gold reference output.
- `src/pssc/cli.py`, `src/pssc/targets/base.py`, `src/pssc/targets/__init__.py`
  — Target plug-in + CLI integration points.
- `src/pssc/targets/sv/` (`lower_imports.py`, `lower_components.py`,
  `context.py`, `emit_files.py`, …) — lowering infrastructure to reuse.
- `src/pssc/share/sv/zsp_rt_pkg.sv` + `pyproject.toml` package-data — the
  shipping mechanism the core package reuses.
