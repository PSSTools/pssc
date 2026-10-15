# Operation-Model Export — Design

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

**Status:** for review · **Date:** 2026-08-02 · **Supersedes:** the sketch in
[`op-model-export.md`](../op-model-export.md)

Goal: project the PSS operation model in `src/pss` into a **SystemVerilog**
(priority) and **C** implementation, and make that generated code the core of
the WB DMA UVM testbench — with generation wired into the dv-flow-mgr build.

---

## 1. Headline

Most of the machinery already exists, and it is better than the sketch in
`op-model-export.md` assumes. `pssc` already has three
programming-sequence targets (`sv-progseq`, `c-progseq`, `cpp-progseq`) whose
generated shape is almost exactly the projection the sketch describes, and it
already ships DFM tasks (`pssc.SvProgSeq`, `pssc.CProgSeq`, `pssc.CppProgSeq`)
that run them.

**But it does not work on this project's model.** Run today against `src/pss`,
`sv-progseq` emits a 58-line package that contains *no operations, no
registers, no channels*, and does not compile:

```console
$ pssc compile -t sv-progseq --root wb_dma_c \
    $(pss-order --from-flow src/pss --task src --relative-to .) -o out/
$ grep -c 'pure virtual task' out/wb_dma_c_pkg.sv
0
```

The gap is six specific, independently-verified defects (§3). None is
architectural — the design below keeps the existing projection and fixes the
places where it does not match how this model is written. The work is
concentrated in `packages/pssc`, and the largest single item (§3, defect A) is
a front-end hole that silently discards the entire operation model.

The genuinely *new* design content is §4.4 (blocking and interrupts — the `TBD`
in the sketch) and §4.3 (construction and address binding).

---

## 2. What already exists

### 2.1 In `packages/pssc`

| Piece | Where | State |
|---|---|---|
| `sv-progseq` target | `targets/progseq_tgt.py`, `targets/sv/lower_progseq.py`, `targets/sv/lower_reg_model.py` | works on simple models |
| `c-progseq` / `cpp-progseq` | `targets/c/`, `targets/cpp/` | ditto; 3 C link styles (`vtable`/`direct`/`mmio`) |
| Language-neutral tree walk | `targets/progseq_model.py` | shared by all three backends |
| SV core runtime | `share/sv/pssc_reg_pkg.sv` | `addr_handle_t`, `pss_mem_if`, `reg_c#(T,ACC)` |
| C core seams | `share/c/pssc_mem*.h` | vtable / direct / mmio |
| DFM task package | `src/pssc/dvflow/flow.yaml` | `SvProgSeq`, `CProgSeq`, `CppProgSeq`, `RegPkg`, `CoreC`, … |
| OO export API (actions) | `targets/sv/lower_export_api.py` | a *separate*, action-oriented projection — see §4.7 |

The generated SV shape (from `docs/progseq.rst`) is:

```
pssc_reg_pkg                       addr_handle_t, pss_mem_if, reg_c#(T,ACC)
<root>_pkg
  <value>_s          packed structs   register bit-field layouts
  <group>_c          classes          register model
  <root>_if          interface class  EXPORT API — one task per operation
  <root>_import_if   interface class  IMPORT API — extends pss_mem_if
  <root> #(IMP_T)    class            impl + import redirect + static create()
```

This already satisfies most of `op-model-export.md` §"SystemVerilog": interface
class for export and import, a factory (`static create()`), and a base for
default handlers. The `IMP_T` parameter is a nice extra — it lets a testbench
pass a *signature-compatible* bus object that does not formally implement the
interface, which matters for reusing `fwvip-wb` classes unchanged.

### 2.2 In this project

| Piece | Where |
|---|---|
| Device operation model (PSS) | `src/pss/` — `wb_dma_c`, `wb_dma_ch_c[4]` |
| Environment model (PSS) | `tests/pss/` — `pss_top`, `wb_dma_hs_c` |
| UVM testbench | `tests/uvm/` — env, 3 DUT views (tlm / wb / rtl), 8 tests |
| Register access seam (UVM) | `wb_dma_base_seq.reg_write/reg_read` over `fwvip_wb_transaction` |
| Abstract model seam (UVM) | `wb_dma_model_base` → `rf_if()` returns `wb_proto_if#(32,32)` |
| Interrupt seam (UVM) | `wb_dma_model_base.wait_int_change()` / `m_int_ev` / `irqc` |

`pssc` is **not** imported by any `flow.yaml` in this project yet.

---

## 3. Empirical status — what breaks, and why

All of the following were reproduced on 2026-08-02 against pssc `f7f0bce`. I
isolated each defect with a minimal inline model so they are attributable
independently rather than as one blob.

### A. `extend component` function declarations are silently discarded

**The blocking defect.** `AstToIr._translate_extend`
(`packages/pssc/src/pssc/ast2ir.py:226`) handles `pss_ast.Field`,
`pss_ast.ExecBlock` and `pss_ast.ConstraintBlock` — and nothing else. A
`function` declared inside `extend component` is dropped with no diagnostic.

Every operation in `src/pss` is authored as an `extend` file, because
`skills/pss-coding-guidelines` mandates it ("components are top-level files plus
a same-named companion directory whose `actions/` and `functions/`
subdirectories attach elements via `extend`"). So the entire operation model
vanishes:

```console
$ pssc parse $(pss-order --from-flow src/pss --task src --relative-to .) \
    --dump-ir dma.ir.yaml
$ grep -c 'pause_engine\|transfer_single\|configure_channel' dma.ir.yaml
0
```

IR after translation: `wb_dma_c.functions == [init]`, `wb_dma_ch_c.functions ==
[init]`. All 11 operations and all 6 actions are gone.

This is silent, which is worse than the failure. It also means the entire
`src/pss` tree is currently invisible to *every* pssc target, not just progseq.

### B. Named register component types are not recognized as registers

`progseq_model.field_is_register` tests `type(field.datatype).__name__ ==
"DataTypeRegister"`. `src/pss` declares named register types —

```pss
pure component wb_dma_gcsr_r : reg_c<wb_dma_gcsr_s, READWRITE, 32> {}
...
pure component wb_dma_regs_c : reg_group_c { wb_dma_gcsr_r csr; ... }
```

— and in the IR such a field's datatype is a `DataTypeComponent`, not a
`DataTypeRegister`. Consequences, all observed in the generated output:

- no value structs emitted at all;
- `class wb_dma_regs_c` emitted with an **empty body** — so any operation body
  that says `m_regs.csr.write(...)` references a non-existent member;
- each named register type is misclassified as a *regular component* and gets a
  bogus empty `interface class wb_dma_gcsr_r_if`.

Controlled proof — the same model with an **inline** `reg_c<>` field works
perfectly:

```systemverilog
// inline `reg_c<csr_s, READWRITE, 32> csr;`  ->  correct
typedef struct packed { bit [29:0] rsvd; bit err; bit done; } csr_s;
class regs_c;
  reg_c #(csr_s) csr;
  function new(pss_mem_if bus, addr_handle_t base);
    csr = new(bus, base + 64'h0);
  endfunction
endclass
```

```systemverilog
// named `pure component csr_r : reg_c<...> {}` then `csr_r csr;`  ->  empty
class regs_c;
  function new(pss_mem_if bus, addr_handle_t base);
  endfunction
endclass
interface class csr_r_if; endclass          // spurious
```

Note `is_reg_group` has the same shape of bug in milder form: it checks only the
*immediate* super's `ref_name`, so a register group one level further derived
would also be missed.

### C. Sub-components are not exposed

`lower_progseq.emit_component` walks only reg-group fields. `wb_dma_c.ch[4]`
(array of `wb_dma_ch_c`) produces nothing: no member, no accessor, no
`wb_dma_ch_c_if`, no per-channel operations. Verified independently of defect A
with an inline model — `sub_c` and its `wait_done()` are simply absent from the
output.

This is the single most important structural requirement here: **every
per-channel operation lives on `wb_dma_ch_c`**, by explicit review decision
(`src/pss/wb_dma_ch_c.pss` documents why). Without sub-component exposure the
export API is nine-tenths empty even after A and B are fixed.
`op-model-export.md` calls for it ("sub-component instances are exposed").

### D. The constructor convention does not match

`lower_progseq._ctor` looks for a solve function literally named `ctor`.
`src/pss` uses `solve function void \init (addr_handle_t base)` (the IR records
the name as `init` — the escaped identifier lexes cleanly). So `_ctor()` returns
`None`, `create()` takes no base address, and the emitted constructor body is

```systemverilog
function new(IMP_T imp);
  m_imp = imp;
  m_regs = new(this, base);   // `base` is not declared anywhere
endfunction
```

— which does not compile. Observed in both the real model and the probe.

### E. Array size and address binding are not resolved

- `wb_dma_c.ch` carries `size: -1` in the IR: `WB_DMA_MAX_CH` (a package
  `static const int`) is not folded.
- The per-channel banks are bound by `make_handle_from_handle()` inside
  `\init`, **not** by `get_offset_of_instance_array()`. The affine-offset
  recovery that `progseq_model._array_base_stride` implements therefore does not
  apply to this model at all.

### F. Body-emitter coverage gaps

Latent behind A — they will surface one at a time once operations reach the
emitter. Constructs `src/pss` actually uses that `lower_progseq` has no mapping
for:

| Construct | Used by | Emitter status |
|---|---|---|
| `yield` | `wait_completion`, `pause_engine`, all end-to-end ops | **no mapping** (see §4.4) |
| `break` | `wait_completion`, `pause_engine` | no `StmtBreak` case |
| enum-typed return (`wb_dma_status_e`) | `wait_completion`, `transfer_single` | `sv_type()` raises on `DataTypeEnum` |
| `foreach` | `wb_dma_c.\init` | no `StmtForeach` case |
| struct-typed args (`wb_dma_ch_cfg_s`, `wb_dma_desc_s`) | `configure_channel`, `write_descriptor` | untested |
| `message()` | several | no mapping |

`sv_type()` raises `ValueError` on anything but `DataTypeInt`/`DataTypeStruct`,
so these fail loudly rather than silently — good, but they are all on the
critical path.

### G. The progseq regression is dark

`tests/progseq/` (60 tests, including the Verilator behavioral gate
`test_sim_wb_dma.py`) depends on `examples/export/programming_seqs/` —
`dma_regs.pss`, `dma_engine.pss`, and the validated `wb_dma_sv_proto.sv` /
`c_proto/` / `cpp_proto/` references. **Those files do not exist**, and
`git log --all -- examples/export` is empty, so they were never committed to
this repo. `examples/` contains only `README.md` and `ast_to_ir_demo.py`.

```console
$ pytest tests/progseq -q
35 failed, 17 passed, 25 errors
```

Every doc page that cites them (`docs/progseq.rst`, `docs/progseq_design.rst`)
and both design notes they reference (`design/pss-programming-seq-gen-design.md`,
`design/pss-c-cpp-progseq-gen-design.md`) are also missing. So the feature we are
about to build on has **no runnable regression and no authoritative design note**
in this checkout.

**Decided (§3G fix): promote `src/pss` to be the pssc progseq reference model**
rather than reconstructing the lost toy. It is the real model; it exercises every
feature §4 adds (named register types, sub-component arrays, `init` binding,
`yield`, enums) — which the toy did not, and that is precisely why these six
defects were never caught; and it means the pssc regression and this project's
model cannot drift apart.

Mechanics: pssc carries a checked-in copy of the DMA PSS source under
`examples/`, refreshed from `src/pss` (a sync check in CI keeps the copy honest).
`tests/progseq/conftest.py`'s `_SRCS`/`generate_wb_dma` point at it; the
Verilator gate `test_sim_wb_dma.py` then runs the real model end to end. The
accepted cost is a one-way content dependency from pssc onto this project's
model.

---

## 4. Design

### 4.1 Projection contract (SystemVerilog)

Keep the existing shape; extend it for the component *tree*. For root `wb_dma_c`
with sub-component array `ch[4]` of `wb_dma_ch_c`:

```systemverilog
package wb_dma_c_pkg;
  import pssc_reg_pkg::*;

  // --- value layouts, enums, register groups (unchanged shape) ---
  typedef struct packed { ... } wb_dma_gcsr_s;
  typedef enum { WB_DMA_DONE, WB_DMA_ERROR } wb_dma_status_e;
  class wb_dma_regs_c;    reg_c #(wb_dma_gcsr_s) csr; ... endclass
  class wb_dma_ch_regs_c; ... endclass

  // --- export API, one interface class per REGULAR component ---
  interface class wb_dma_ch_c_if;
    pure virtual task configure_channel(input wb_dma_ch_cfg_s cfg);
    pure virtual task transfer_single(output wb_dma_status_e status,
                                      input bit [31:0] src, input bit [31:0] dst,
                                      input int num_bytes);
    pure virtual task stop_channel();
    ...
  endclass

  interface class wb_dma_c_if;
    pure virtual task pause_engine(input bit pause);
    pure virtual task configure_interrupt_routing(...);
    ...
    // sub-component navigation: METHODS, because an interface class
    // cannot carry data members (op-model-export.md, "SystemVerilog")
    pure virtual function wb_dma_ch_c_if ch(int index);
    pure virtual function int            ch_size();
  endclass

  // --- import API ---
  interface class wb_dma_c_import_if extends pss_mem_if;
    pure virtual task yield();              // see §4.4
  endclass
  virtual class wb_dma_c_import_base implements wb_dma_c_import_if;
    // every method stubbed with $fatal -- extend and override what you need
  endclass

  // --- implementations ---
  class wb_dma_ch_c implements wb_dma_ch_c_if;      // NOT parameterized
    protected wb_dma_c_import_if m_imp;
    protected wb_dma_ch_regs_c   m_regs;
    protected int                chan;
    function new(wb_dma_c_import_if imp, int id, addr_handle_t bank);
    ...
  endclass

  class wb_dma_c #(type IMP_T = wb_dma_c_import_if)
        implements wb_dma_c_if, wb_dma_c_import_if;
    protected IMP_T          m_imp;
    protected wb_dma_regs_c  m_regs;
    protected wb_dma_ch_c    m_ch[4];
    function new(IMP_T imp, addr_handle_t base); ... endfunction
    virtual function wb_dma_ch_c_if ch(int index); return m_ch[index]; endfunction
    virtual function int ch_size(); return 4; endfunction
    // ... operations, mem-primitive redirect to m_imp, static create()
    static function wb_dma_c_if create(IMP_T imp, addr_handle_t base);
  endclass
endpackage
```

**Decision — only the root is parameterized.** Sub-component classes take the
root's `..._import_if` (an interface-class handle), not `IMP_T`. The root is-a
`..._import_if` via its own redirect, so it passes `this` to its children.
This keeps the `IMP_T` duck-typing escape hatch at the boundary where it is
useful without templating the whole tree — a `wb_dma_ch_c #(IMP_T)` would force
every consumer of `ch()` to know `IMP_T`, which defeats the interface class.

**Decision — sub-components are exposed as accessor methods**, `ch(int)` for
arrays plus `ch_size()`, and a bare `sub()` for scalars. Forced by SV interface
classes having no data members.

### 4.2 Register recognition

Generalize the classification in `progseq_model.py`:

- `is_register(dtype)` — walk the **transitive** super chain (resolving
  `DataTypeRef` through the type map) looking for `reg_c`; return true for a
  `DataTypeRegister` *or* a `DataTypeComponent` that derives from `reg_c`.
- `is_reg_group(dtype)` — same treatment for `reg_group_c` (currently one level
  only).
- `field_is_register` / `field_is_reg_group` delegate to those, and follow
  array element types.

The value type / access mode / access width for a named register type come from
the `reg_c<T, ACC, W>` specialization on the super. **Verification item:** confirm
the IR carries those template arguments on `DataTypeComponent.super` — if it
does not, that is a second front-end fix, not a backend one.

This is a pure generator fix. **Decided:** `src/pss` keeps named register types
and is **not** rewritten to use inline `reg_c<>` fields — the generator adapts to
the model, not the reverse. Named register types are the documented house style,
they are what makes the register bank readable, and other tools consume them
fine.

### 4.3 Construction and address binding

Replace the affine `get_offset_of_instance_array` recovery (which does not apply
here, §3E) with the more general and more faithful mechanism: **lower the root's
`init` solve function into the generated constructor.**

The PSS source already says exactly what the binding is; execute it rather than
re-deriving it. Recognized forms:

| PSS in `init` | Generated SV |
|---|---|
| `regs.set_handle(h)` | `m_regs = new(<bus>, h);` |
| `make_handle_from_handle(base, off)` | `(base + off)` (`addr_handle_t` is `bit[63:0]`) |
| `ch[i].\init (a, b)` inside `foreach` | `m_ch[i] = new(this, a, b);` |
| `chan = id` | member assignment |
| `foreach (ch[i]) { ... }` | `for (int i = 0; i < N; i++) ...` |

`create()` / `new()` take the import handle followed by the root `init`
parameters — for this model, `create(imp, base)`.

**Constructor identification:** accept a `solve function void` named `init` or
`ctor`; add `--ctor-name` to override. (Fixes defect D; the IR already records
`\init` as `init`, so this is nearly a one-line change.)

Keep the affine path as a fallback for models with no explicit `init`.

Array size (defect E) requires folding package `static const int` into
`DataTypeArray.size` during translation. Until then, `ch_size()` cannot be
emitted and `foreach` cannot be unrolled — so this is on the critical path, not
optional.

### 4.4 Blocking, `yield`, and interrupts — the `TBD`

> **AMENDED — not superseded — by the M0 re-scoping (2026-08-13).** The yield
> contract below still governs, and still governs `pause_engine()` and the
> polling branch of `wait_hint()`. What changed is *where* the variation lives:
> the wait is now a named seam, `wait_hint()`, whose `compile if` is **inside the
> body**, so the end-to-end layer exists on **every** target rather than being
> gated to schedulers. The file this section cites,
> `wb_dma_ch_c/functions/wait_completion.pss`, is now
> `wb_dma_ch_c/functions/blocking_ops.pss` (four functions share it) with the
> wait extracted to `wait_hint.pss`. See
> [`wb_dma_operation_model.md`](wb_dma_operation_model.md) §3 for the current
> layering and §3.2 for the yield principle in its general form.

`op-model-export.md` records "TBD: must have a strategy for interrupts". Here it
is.

**Observation.** The model has already made the hard decision, and documented
the reasoning in `src/pss/wb_dma_ch_c/functions/wait_completion.pss`: blocking is

```pss
while (true) { check_cond; yield; }
```

deliberately **edge-free** — "nothing is edge-triggered, so nothing can be
missed: the condition is re-read from the device on every wake, and a spurious
wake is harmless." The condition is ordinary register-access code. There is no
event object, no interrupt vector, and no `wait(...)` in the model.

**Design: `yield` maps to an import task.**

```systemverilog
interface class wb_dma_c_import_if extends pss_mem_if;
  pure virtual task yield();
endclass
```

Contract on the implementer:

1. `yield()` **must** return whenever any device-observable state may have
   changed. Returning more often than that is always *correct*, only slower.
2. `yield()` **must not** block forever. A pure interrupt-only implementation
   deadlocks — `wait_completion.pss` notes that `INT_SRC` is ANDed with the
   routing mask, so a channel that is not routed never raises an interrupt, and
   `pause_engine`'s readback loop has no interrupt behind it at all. **The
   default binding must therefore be event-or-timeout**, not event-only.

Bindings:

| Environment | Implementation |
|---|---|
| UVM (this TB) | `fork wait(model.m_int_ev); #(TIMEOUT); join_any` — reuses the existing `wb_dma_model_base` interrupt seam verbatim |
| UVM, no interrupt routed | falls through on the timeout arm; correctness preserved |
| C (`vtable`) | a `void (*yield)(void *ctx)` slot; default = no-op busy-poll |
| C (`mmio`, firmware) | no-op, or a `wfi` |

**Why this is the right shape.** It keeps the model's missed-edge immunity; it
puts the interrupt *strategy* in the environment, which is exactly where this
project's device/environment split already puts it (`src/pss/README.md`:
`wb_dma_hs_c` is environment precisely because its platform contract is the
environment's to satisfy); and it costs one import method.

**Rejected alternative:** a first-class `wait_event(e)` / condition-registration
primitive. It re-introduces the missed-edge hazard the model was written to
avoid, and it forces the device model to name events the device specification
does not define.

**Open sub-issue (§7.2):** `yield`'s representation in the IR is unverified —
it is behind defect A, so it has never reached the emitter. Confirm it survives
translation before committing to the lowering.

### 4.5 C projection

The C backend needs the same §4.2/§4.3 fixes (they live in the shared
`progseq_model.py`) plus sub-component exposure in `c/lower_progseq.py`:

```c
wb_dma_t     *dma = wb_dma_create(&bus, 0x40000000u);
wb_dma_ch_t  *ch  = wb_dma_ch(dma, 5);          /* sub-component accessor */
int st = wb_dma_ch_transfer_single(ch, src, dst, 4096);
```

Prioritize **after** SV, as instructed. The `vtable` link style is the one to
target first: its `ctx` pointer already solves multi-instance (§4.6) and it is
the C analogue of `pss_mem_if`.

### 4.6 Multi-instance and context identity

`op-model-export.md` §"Instance mapping via the PSS API" designs a `root_id` /
`sub_id` scheme with component extensions and a registering shim. **Decided:
deferred out of the critical path** — that complexity is not needed for the SV
or C-vtable projections:

- **SV OO** — each `create()` returns an independent handle carrying its own
  import object. Multi-instance is free. No IDs.
- **C vtable** — the `pssc_mem_if.ctx` pointer is the instance. No IDs.
- **C `direct` / `mmio`** — genuinely single-instance by construction (that is
  the point of those styles). IDs would not help.

The ID scheme is needed only for the **PSS-side global-function projection**,
whose purpose is to let **existing PSS tools consume our generated operation-model
implementation**: a third-party PSS tool solves and runs the scenario, and calls
out through import functions into the SV or C model this design generates. That
is a real requirement and the natural follow-on to this work, but it is a
distinct deliverable with a distinct consumer, and nothing in the UVM testbench
depends on it. Phase 8 (§9); revisit with a concrete two-instance use case.

### 4.7 Actions are not projected

`src/pss` carries six actions (`transfer_single_a`, `stop_channel_a`, …)
alongside the functions. `progseq` projects **functions only**, which is
correct: actions exist for PSS-native scenario generation and constraint solving,
and have no meaning in a hand-written UVM sequence. The separate
`lower_export_api.py` projection (`sv-native`) is the one that projects
*actions*, for PSS-generated scenarios.

Two projections, two audiences — this should be stated explicitly in the docs,
because the two are easy to confuse and currently are not distinguished
anywhere. **Note:** defect A drops actions declared via `extend component` too,
so `sv-native` is equally broken on this tree.

---

## 5. DFM integration

`pssc.SvProgSeq` already exists and does the right thing. What is missing is
project-side wiring and one ordering hazard.

### 5.1 New `src/pss/flow.yaml`

```yaml
package:
  name: wb-dma-pss
  imports:
    - name: pssc
  tasks:
    - name: src
      uses: pss.FileSet          # see 5.2 -- NOT std.FileSet with a glob;
      with:                       # file order is load-bearing today
        root: wb_dma_c

    - name: op-model-sv
      uses: pssc.SvProgSeq
      needs: [src]
      with:
        root: wb_dma_c
        package_name: wb_dma_c_pkg
        core_copy: true

    - name: op-model-c            # after SV
      uses: pssc.CProgSeq
      needs: [src]
      with: { root: wb_dma_c, prefix: wb_dma, link_style: vtable }
```

`tests/uvm/flow.yaml`'s env-library task then `needs: [wb-dma-pss.op-model-sv]`.
`classify_outputs` already tags `.sv` as `systemVerilogSource` with the directory
as an incdir, so `SimImage` picks it up. Compile order must be
`pssc_reg_pkg.sv` → `wb_dma_c_pkg.sv` → `wb_dma_env_pkg.sv`.

### 5.2 Ordering hazard — two tracks

`src/pss/README.md` documents pssparser defect 3: **a wrong (e.g. alphabetical)
file order leaves references unresolved while still reporting `0 errors` and
exiting 0.** `std.FileSet` with a glob produces exactly that alphabetical order,
so the naive wiring is also the broken one.

**Decided: pursue both a root-cause fix and a structural one.** They are
independent and neither blocks the other.

#### Track 1 — make pssparser order-independent (root cause)

This is the real fix and it is already an open, tracked workstream, not new work:

- `docs/pssparser-defects-2026-08-02.md` **D3** ("Cross-file forward reference
  into a package that declares a register group is silently unresolved",
  severity P1) is exactly this defect, with a minimal repro.
- `docs/pssparser-fix-plan.md` §1.1 *is* D3, and the plan already carries
  file-order-independence work: `tests/python/linking/test_file_order_independence.py`
  links each model twice — declaration-first and use-first — and requires both to
  succeed. Measured progress is recorded there (whole model, sorted order: 72
  errors → 1; reverse order: 0).

Note D3's own warning: **it cannot be regression-tested by exit status**, because
a fix and a non-fix both produce `0 errors` and exit 0. The order-independence
test above (compare *resolved reference counts* between two orders, not exit
codes) is the only meaningful gate. Once D3 closes, `files.f` can be retired
entirely and a glob becomes safe.

#### Track 2 — `pss.FileSet` in `dv-flow-libpss` (structural)

Add a **`pss.FileSet`** task that understands the source layout this project is
developing, and emits a correctly-ordered `pssSource` fileset.

**It belongs in `dv-flow-libpss`, not in `pssc`.** That package already defines
the vendor-neutral abstract `pss` DFM package —
`packages/dv-flow-libpss/src/dv_flow/libpss/flow.dv` declares `pss.Lib`,
`pss.GenModelSV`, `pss.GenActorSV`, `pss.RuntimeSV`, with per-vendor
implementations (`pss.psp` → Perspec, `pss.zsp` → Zuspec). File-list construction
is a property of the *source layout*, not of any one compiler, so `pss.FileSet`
sits alongside `pss.Lib` and every vendor back end gets it for free. There is no
such task today.

What "understands the file structure" means concretely — the layout mandated by
`skills/pss-coding-guidelines` and described in `src/pss/README.md`:

```
wb_dma_types_pkg.pss             packages first
wb_dma_regs_pkg/*.pss            register banks
wb_dma_ch_c.pss                  component declaration ...
wb_dma_ch_c/functions/*.pss      ... then its attached functions
wb_dma_ch_c/actions/*.pss        ... then its attached actions
wb_dma_c.pss                     owner component after the components it owns
wb_dma_c/functions/*.pss
wb_dma_c/actions/*.pss
```

The ordering rule falls out of the convention: **a component's own `.pss` file
precedes everything in its companion directory**, and packages precede
components. That is derivable from the directory structure, so `pss.FileSet`
replaces the hand-maintained `src/pss/files.f` rather than reading it — removing
the duplication that would otherwise drift.

Sketch:

```yaml
- name: FileSet
  uses: std.FileSet             # same parameters; the difference is the ORDER
  with:
    root:   { type: str }       # root component -- orders owners after ownees
    order:  { type: list }      # escape hatch: ordered bands of path globs
    strict: { type: bool }      # layout warnings become errors
```

**Implemented as `pss.FileSet`, not `pss.FileList`** — it *is* a `std.FileSet`
for PSS, and inheriting the parameter surface (`base`/`include`/`exclude`/
`type`/…) means a project swaps one for the other and changes nothing else.

It should also **emit a marker when the derived order is ambiguous** (a
component file with no companion dir, an unreferenced package) rather than
guessing silently — given how this defect class behaves, silence is the enemy.

Grounding the rule in the language rather than in this project's habits
(PSS 3.1 §18): a package is the union of every `package` statement sharing its
name (§18.1.1a), so **a package is a set of files** and a directory named for it
is the natural unit; the three reference forms (`import p`, `import p::x`,
`p::x`, §18.1.3) give package-level dependency edges with nothing inferred;
root-scope declarations are in the unnamed global package (§18.1) and no import
names them, so only an identifier scan can order them — which is what the
companion-directory convention exists to make unnecessary for the case that
matters. The language's *only* file-order rule is that a constant may reference
another constant in its initializer only after its declaration (§18.2); it is
intra-package, and handled inside the unit.

Interim, until `pss.FileSet` lands: drive `std.FileSet`'s `include:` from
`src/pss/files.f` order explicitly. `gather_pss_sources` preserves fileset order,
so that is correct, just duplicative.

### 5.3 Also worth adding

- **`pssc.Check`** — parse/elaborate only, no codegen, as a CI gate on
  `src/pss`. There is no such task today, and given §5.2 the project needs a
  cheap "does this still link" node. Pair it with the order-independence check
  from Track 1: run the model in two orders and compare, since exit status alone
  proves nothing.
- **A shared `pssc.PsscArgs` dataset** so the SV, C and Python projections take
  `root`/`export_action` from one place.

---

## 6. UVM testbench integration

The seam already exists and matches well.

### 6.1 The import adapter

One new class in `tests/uvm/env/`:

```systemverilog
class wb_dma_op_import extends wb_dma_c_import_base;   // generated base
  wb_dma_base_seq  m_seq;      // for reg_write/reg_read + the note_* taps
  wb_dma_model_base m_model;   // for the interrupt seam

  virtual task write32(addr_handle_t addr, bit [31:0] data);
    m_seq.reg_write(addr[31:0], data);
  endtask
  virtual task read32(addr_handle_t addr, output bit [31:0] data);
    m_seq.reg_read(addr[31:0], data);
  endtask
  virtual task yield();
    fork begin
      fork
        m_model.wait_int_change();
        #(YIELD_TIMEOUT);
      join_any
      disable fork;
    end join
  endtask
  // 8/16/64-bit primitives: $fatal (this device is word-only)
endclass
```

**Route through `wb_dma_base_seq`, not around it.** Its `reg_read` already feeds
`note_int_src()` on `INT_SRC_A/B` reads, which the WB and RTL flavours depend on
to reconstruct completion cause. Bypassing it to talk to `rf_if()` directly
would silently break the scoreboard on two of three DUT views.

### 6.2 Using it in a sequence

```systemverilog
wb_dma_c_if dma = wb_dma_c #(wb_dma_op_import)::create(imp, WB_DMA_BASE);

dma.ch(5).configure_channel(cfg);
dma.ch(5).transfer_single(status, src, dst, 4096);
if (status != WB_DMA_DONE) `uvm_error(...)
```

replacing the current hand-coded `reg_write(REG_CH_CSR(c), ...)` sequences.

### 6.3 Per-channel exclusion — assert, in the UVM layer

`wait_completion.pss` states the rule **AT MOST ONE OPERATION PER CHANNEL AT A
TIME**: the channel CSR read is destructive (reading clears `ERR` and the
interrupt-source bits), so two threads polling one channel race to consume each
other's completion. A UVM testbench running per-channel sequences in parallel is
exactly the shape that violates it, and the symptom — a transfer that never
completes, or completes without its error — reads as a DUT bug.

**Decided: enforce it with an assertion in the UVM code, not in the generated
model.** Concretely, a per-channel in-flight flag in the adapter/env layer:

```systemverilog
// in wb_dma_op_import (or a thin wrapper around wb_dma_c_if)
protected bit m_busy[int];

function void claim(int ch);
  `uvm_fatal_if(m_busy.exists(ch) && m_busy[ch],
      $sformatf("two concurrent operations on channel %0d -- wait_completion's \
                 destructive CSR read makes this unsafe", ch))
  m_busy[ch] = 1;
endfunction
function void release_ch(int ch); m_busy[ch] = 0; endfunction
```

Why the UVM layer rather than the generated package:

- it keeps the generated model **free of any UVM dependency**, which the SV core
  deliberately preserves (and which §7.5 relies on for `message()`);
- the rule is about how a *test* schedules operations, so the check belongs where
  the scheduling happens and where the error message can name the sequence;
- a `semaphore` in the generated model would *hide* the violation by serializing
  it — the second sequence would block and pass, having silently lost its
  concurrency. An assertion surfaces the test bug, which is what we want.

### 6.4 Address width

`pss_mem_if` uses `addr_handle_t` = `bit[63:0]`; the WB bus and
`wb_proto_if#(32,32)` are 32-bit. The adapter narrows. Harmless here, but it
should be an explicit truncation with an assertion on the upper bits rather than
an implicit one.

### 6.5 Migration order

Convert **one** test first (`wb_dma_sw_copy_test` — the simplest end-to-end
path) and run it across all three DUT views before converting the rest. That
validates the adapter, the yield binding, and the scoreboard taps together, on
the smallest possible surface.

---

## 7. Open issues

1. ~~**Register template arguments in the IR (§4.2).**~~ **Resolved 2026-08-02 —
   they are not carried.** `wb_dma_gcsr_r` translates to a `DataTypeComponent`
   whose `super` is `DataTypeRef(ref_name='reg_c')` and nothing else;
   `_translate_component` keeps only the super's *name* and discards
   `getParams()`. So value type, access mode and width are lost in translation
   and no backend can recover them. **Defect B is therefore a front-end fix as
   well as a backend one.** Mitigating: no IR schema change is needed
   (`ir.DataTypeRegister` already has `register_value_type` / `access_mode` /
   `size_bits` / `template_args`) and `ast2ir._translate_reg_c` already parses
   `reg_c<R,ACC,SZ>` — it is simply never called from the super-type path. See
   the plan, §0 and §5.1.
2. **`yield` in the IR (§4.4)** — unverified, because it is behind defect A.
3. **`num_ch` vs `WB_DMA_MAX_CH`.** The API will expose 4 channels; only
   `num_ch` are usable on a given instance. `num_ch` is a runtime component
   attribute and is not surfaced by the projection at all. Should `ch(i)` range-
   check against it?
4. **Struct-valued arguments across the export API** (`wb_dma_ch_cfg_s`,
   `wb_dma_desc_s`). Untested end to end. `wb_dma_desc_s` is the one the model
   README already flags as blocked on missing `read_struct`/`write_struct` in
   the stdlib.
5. **`message()` / `error()`** have no SV mapping. Map to `uvm_info`? That would
   couple the generated package to UVM, which the SV core deliberately avoids
   (and §6.3 relies on). Probably `$display` plus an overridable hook.
6. **The two missing design notes** (`design/pss-programming-seq-gen-design.md`,
   `design/pss-c-cpp-progseq-gen-design.md`) are cited as authoritative by the
   surviving docs and by module docstrings. Do they exist anywhere?
7. **Scope of `pss.FileSet` (§5.2, Track 2).** Derived ordering is only as good
   as the layout convention. Does every PSS tree we care about follow
   `pss-coding-guidelines`, or does the task need a `files.f` escape hatch for
   trees that do not? (`tests/pss/` follows it; third-party PSS may not.)

---

## 8. Overlooked opportunities

1. **The import API is a free observability tap.** Every register access in the
   generated model funnels through eight methods. A decorating implementation of
   `wb_dma_c_import_if` gives functional coverage of the register map, a
   transaction trace, and protocol checks — for no change to the model. It also
   plugs straight into the `dbg_level` / `dbg_subj` observable-events
   infrastructure this project's `flow.yaml` already exposes (`dfm run uvm-trace
   -D dbg_level=5` is documented as "+ every register access" — this is where
   those events would come from).

2. **One model, two languages, cross-checked.** Once `c-progseq` works, the same
   PSS source produces a C driver that can run as *firmware* against the RTL
   (link style `mmio`, header-only) while the SV projection drives the same
   sequences from the testbench. Any divergence is a generator bug. This is a
   strong correctness argument and a strong demo, and it is nearly free once §4
   lands.

3. **Retire hand-written drift.** `wb_dma_ref_model` and the register sequences
   in `tests/uvm/tests/*_seq.svh` encode the same programming knowledge as
   `src/pss`, by hand, twice. Driving both the DUT and the reference through one
   generated API removes a whole class of "the model and the test disagree"
   bugs. Worth scoping as a follow-on.

4. **Python projection for cocotb.** `pssc --target python` exists, and
   `fwvip-wb` / `fwvip-gpio` already ship cocotb front-ends. A Python op model
   would give the cocotb tests the same operation vocabulary as the UVM tests —
   a third consumer of one source.

5. **`pssc.Check` as a PSS CI gate (§5.3).** `src/pss/README.md` documents three
   pssparser defects and two leaked internal errors, all found by hand on
   2026-08-02. A cheap DFM node would catch regressions in that surface
   automatically.

6. **Fix the silent-drop class of bug generally.** Defect A is silent; so is the
   file-ordering defect; so is defect B (empty output, exit 0). This project has
   now hit three silent-success failures in one toolchain. A single "the
   generator produced an export API with zero operations — that is almost
   certainly wrong" warning in `progseq_gen` would have caught two of them at
   zero cost. Recommend a general rule: **the generator errors, not warns, on an
   empty export API.**

---

## 9. Proposed phasing

| Phase | Content | Where | Unblocks |
|---|---|---|---|
| **0** | Promote `src/pss` to the progseq reference model; restore a runnable regression (§3G) | pssc | everything — currently no safety net |
| **1** | Defect A: `extend` functions + nested actions in `_translate_extend` | pssc | the entire model becomes visible |
| **2** | Defects B, D, E: register recognition, `init` as ctor, array sizes | pssc | registers + construction |
| **3** | Defect C: sub-component exposure (§4.1) + `init` lowering (§4.3) | pssc | per-channel API — the bulk of the model |
| **4** | Defect F: `yield` (§4.4), `break`, enums, `foreach`, struct args | pssc | operation bodies compile |
| **5** | `pss.FileSet` (§5.2 Track 2) + DFM wiring (§5.1) + `pssc.Check` | dv-flow-libpss, this repo | generation in the build |
| **6** | UVM adapter + per-channel assertion + convert `wb_dma_sw_copy_test` (§6) | this repo | proof end-to-end, 3 DUT views |
| **7** | Convert remaining tests; `c-progseq` parity | this repo, pssc | |
| **8** | PSS-side import projection / ID scheme (§4.6) | pssc | third-party PSS tools consume our model |
| **—** | pssparser D3 / file-order independence (§5.2 Track 1) | pssparser | *parallel track* — retires `files.f` when it lands |

Phases 1–4 are all in `packages/pssc` and are the real work. Phase 0 should not
be skipped: phases 1–4 change a code path that currently has no passing test.

The pssparser D3 track runs in parallel and gates nothing here — `pss.FileSet`
makes the build correct with D3 still open, and D3 closing later simply lets the
ordering constraint (and `files.f`) be retired.

---

## 10. Decisions taken

Recorded 2026-08-02.

| # | Question | Decision | Folded into |
|---|---|---|---|
| 1 | `files.f` ordering | **Both tracks.** Fix pssparser's order-dependence at the root (D3, already an open workstream), *and* add a **`pss.FileSet`** task — in `dv-flow-libpss`'s vendor-neutral `pss` package, not `pssc` — that derives the order from the component/companion-directory layout | §5.1, §5.2, §9 |
| 2 | Concurrency safety | **Assert, in the UVM code.** Not a semaphore in the generated model (it would hide the violation by serializing it) and not documentation alone | §6.3 |
| 3 | progseq reference model | **Yes — promote `src/pss`.** It exercises every feature being added, which the lost toy did not; that is why these defects went uncaught | §3G, §9 phase 0 |
| 4 | `root_id`/`sub_id` scheme | **Defer.** Follow-on step whose purpose is letting *existing PSS tools* consume our generated operation-model implementation — a distinct deliverable with a distinct consumer | §4.6, §9 phase 8 |
| 5 | Named register types | **Yes — `src/pss` keeps them.** The generator adapts to the model, not the reverse | §4.2 |
