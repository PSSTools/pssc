# Masked & Field-Wise Register Access — Implementation, Test & Doc Plan

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

**Status:** phases 1–6 complete · **Date:** 2026-08-05 · **Spec:** PSS 3.1 Draft 19
§21.14.1 (Syntax159/160, Example356), §21.14.5

Goal: let the operation model in `src/pss` express read-modify-write register
updates with the LRM's own vocabulary — `write_masked()`, `write_val_masked()`,
`write_field()`, `write_fields()` — instead of hand-rolled
read → assign-field → write sequences, and carry those calls all the way through
to generated SV and C.

Four repos are in scope:

| Repo | Role |
|---|---|
| `packages/pssparser` | front end — already accepts the calls; **resolves the field name** and owes the §21.14.1 semantic rules (phase 1) |
| `packages/pssc` | compiler — bit layout, mask folding, IR builtins, SV + C targets (phases 2–4) |
| `packages/zuspec-be-sw` | SW backend — method-name sets (phase 5) |
| this repo (`fw-wb-dma`) | the model itself — five call sites (phase 6) |

---

## 0. Baseline — measured start state (2026-08-05)

Everything below was verified against the working tree, not inferred.

**Front end: already works.** `packages/pssparser/src/stdlib/addr_reg_pkg.pss`
declares all four methods — `write_val_masked`/`write_field`/`write_fields` on
`reg_sized_c<SZ>`, `write_masked` on `reg_c<R,ACC,SZ>`. The spec's Example356
shapes parse and link today:

```
$ python -m pytest tests/python/linking/test_reg_model_3_1.py -q
9 passed in 0.11s
```

`test_masked_and_field_wise_writes` covers struct-literal masks
(`{.mode=~0, .coeff=~0}`), `write_val_masked`, `write_fields({"mode","coeff"}, …)`
and `write_field("en", 1)`. **No front-end parse or link work is required.**

**Front end: no semantic enforcement.** Nothing checks the §21.14.1 (a)–(d)
restrictions on field names, that the named field exists in `R`, that `R` is a
struct at all, or the p.493 rule that an RMW method on a `WRITEONLY` register is
an error. `write_field("chan_en", 1)` — a typo for `ch_en` — links clean today.

**Front end: but it already resolves everything this needs** (measured
2026-08-05, and it is why phase 1 moved). `TaskResolveRefs.cpp` walks each
member-path element with the receiver's symbol scope in hand (`target_s`), then
calls `checkCallArity()` and `checkCallArgTypes()` on the resolved
`ISymbolFunctionScope` — that is how `PSS006` already reports *"argument 1 of
'f' is a string, but parameter 'a' is numeric"*. At the element for
`write_field`, the receiver scope is the specialized `reg_c<csr_s, …>`, and
`TaskGetSpecializedTemplateType` is already in the build. Resolving `"ch_en"`
against `csr_s`'s declared fields is a few steps from code that exists, in the
pass that already reports call errors, with a real location.

**A C++ change here is cheap to iterate on.** `python/core.pyx` `Factory.inst()`
`dlopen`s `build/lib/libpssparser.so` via `ctypes` (`ldd` on the extension shows
no link against it). So a `TaskResolveRefs.cpp` edit needs `ninja` in
`packages/pssparser/build` and nothing else — no Cython regeneration, no
`pip install`. Verified: `ninja` in the existing build tree reports *no work to
do*, so the tree is configured and current.

**pssparser defect D5 makes the LRM's own mask idiom silently wrong.** This is
the single most important measurement in this section.
`AstBuilderInt::visitExpression` discards the operator of a unary expression
(`docs/pssparser-defects-2026-08-02.md`, and four `xfail`s in
`pssc/tests/unit/test_unary_operators.py` pin it). Translating

```pss
regs.csr.write_masked({.ch_en=~0}, {.ch_en=1});
```

today yields `ExprStructLiteral(fields=[ExprStructField(name='ch_en',
value=ExprConstant(value=0))])` — **the `~` is gone and the mask is 0**. A naive
reduction would emit a masked write that selects no bits: a no-op, with no
diagnostic. Example356's `{.mode=~0, .coeff=~0}` is exactly this shape. Two
consequences, both binding on the phases below:

* phase 2 must **reject a zero mask on an explicitly-named field** rather than
  fold it — see §1.2;
* phase 6's style decision reverses: `write_field("ch_en", 1)` names no mask at
  all and therefore cannot be bitten by D5, so it is now the *safe* form, not
  merely a stylistic alternative.

Fixing D5 itself is a separate defect in a separate pass and is **not** in scope
here; this plan has to be correct in its presence.

**Compiler: the blocker.** `pssc/ast2ir.py:3296` `_add_register_functions()`
synthesizes exactly four builtins onto `ir.DataTypeRegister`: `read`, `write`,
`read_val`, `write_val`. The masked and field-wise forms have no IR target at
all.

Mitigating discovery: `_extract_register_fields()` (`ast2ir.py:3358`) already
copies the value struct's fields onto `reg.fields`, and the C target already
emits per-field pack/unpack helpers (`{ct}_{field}_get`, `targets/c/lower_reg_model.py`).
**The name → bit-range layout knowledge already exists**; it is not new work.

**Backends: each hand-matches method names.**

| Location | Recognises |
|---|---|
| `pssc/share/sv/pssc_reg_pkg.sv:60-88` (`reg_c`) | `read`/`write`/`read_val`/`write_val` |
| `pssc/targets/sv/lower_progseq.py:367` | `read`/`read_val`/`get` (value-returning → output-arg rewrite) |
| `pssc/targets/c/lower_progseq.py:188` (`_reg_call`) | literally `("read", "write")`; anything else falls through to a generic call and emits garbage |
| `zuspec-be-sw/passes/mem_reg_lower.py:38-39` | `_REG_READ_METHODS={"read","read_val"}`, `_REG_WRITE_METHODS={"write","write_val"}` |

**The model's five RMW sites** (`grep -rn 'regs\.\w*\.read()' src/pss`):

| Site | Shape |
|---|---|
| `wb_dma_ch_c/functions/transfer_single_start.pss:54` | read → `ch_en=1` → write |
| `wb_dma_ch_c/functions/transfer_list_start.pss:42,46` | two separate RMWs (`use_ed`, then `ch_en`) |
| `wb_dma_ch_c/functions/stop_channel_start.pss:49` | read → `stop=1` → write |
| `wb_dma_ch_c/functions/set_auto_restart.pss:30` | read → `ars=enable` → write |
| `wb_dma_ch_c/functions/probe_status.pss:40` | plain read, **not** an RMW — out of scope |

`wb_dma_c/functions/pause_engine.pss:25` is a blind write and is **correct as
is**: `wb_dma_gcsr_s` is `pause` + RO `reserved`, so there is nothing to
preserve. It is listed here only so a later reader does not "fix" it.

---

## 1. The semantic point that shapes the whole plan

Per §21.14.1 (p.492), the masked forms **are** a read-modify-write:

> cause the register to be read, a write value to be calculated from the current
> register value and the specified masked value, and the write value to be
> written back
>
> `REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)`

Two consequences, both load-bearing:

1. **The read side effect does not go away.** A channel-CSR read clears ERR and
   the interrupt sources — the hazard documented at length in
   `stop_channel_start.pss:21-26` and noted at `transfer_single_start.pss:51-53`.
   `write_masked` reads too. This change is a legibility and uniformity win, not
   a hazard removal, and **every comment describing the ROC read must survive the
   edit verbatim.** Phase 6 is a no-behaviour-change refactor and its test is
   exactly that.
2. **A single lowering serves everything.** Because all four forms reduce to the
   same equation, no backend needs four new primitives. It needs at most one.

Therefore the strategy is: **reduce in the compiler, implement once per
backend.**

```
write_field(name, v) ─┐
write_fields(ns, vs) ─┼─ phase 2 (IR, constant-folded) ─→ write_val_masked(m, v)
write_masked(M, V)  ──┘                                          │
                                                                 │ phase 3
                                       ┌─────────────────────────┴──────────┐
                                       │  --reg-rmw=expand (default until   │
                                       │  phase 4 lands):                   │
                                       │  t = read_val(); write_val(...)    │
                                       └────────────────────────────────────┘
```

`write_field` is constant-foldable because §21.14.1(a) restricts names to string
literals — that restriction exists precisely so a tool can resolve the mask at
compile time.

### 1.1 The governing invariant

**The compiler resolves `"ch_en"`. The IR for all four forms is the same IR, and
no field-name string survives into it.**

```pss
regs.csr.write_field("ch_en", 1);
regs.csr.write_masked({.ch_en=~0}, {.ch_en=1});
regs.csr.write_val_masked(32'h0000_0001, 32'h0000_0001);
```

All three must produce identical IR — a single `write_val_masked` call with
folded constant mask and value. `"ch_en"` is a *reference to a declared field*
that happens to be spelled as a string because the LRM's signature is
`write_field(string, bit[SZ])`; it is not data. Three consequences:

- **An unresolvable name is a compile error, not a lint finding.** It is the same
  class of error as `csr.ch_enn = 1` — misspelling a struct member — and must be
  reported the same way: hard failure, file/line/column, `did you mean 'ch_en'?`.
  A user who never runs an optional checker must never get silently wrong
  register bits.
- **Backends never see a string.** No backend needs a name table, and no backend
  can drift from another's interpretation of a name, because by the time IR is
  handed off the question is already answered. This is also what makes phase 3's
  SV `write_val_masked` a plain type-generic task rather than generated per
  register.
- **"Identical" means identical *modulo* `loc`.** The three spellings sit on
  different source lines, and throwing that away to make a dump compare equal
  would be the wrong trade. The equivalence test normalises `loc` and requires
  everything else to match exactly.

This invariant gets a dedicated test (phase 2) rather than being implied by the
other tests, because it is the property everything else here depends on.

### 1.2 Where the resolution goes, and why it is not one place

The first draft of this plan put the whole of it in `pssc`, reasoning that
`pssc` already computes packed-struct layout for the C target. That conflated
two different questions, and only one of them is the compiler's:

| Question | Kind | Owner |
|---|---|---|
| Is `"ch_en"` a field of this register's value type? Is it scalar? Is it named twice? | **name binding** | **pssparser** (phase 1) |
| Which bits is that field, given `packed_s<>`? | **bit layout** | **pssc** (phase 2) |

Name binding is what a front end does. pssparser resolves every *other* name in
the language — types, members, methods, enum items — and `"ch_en"` is a name.
Resolving it anywhere else means every future consumer of pssparser (another
backend, another compiler, a language server) either re-implements the lookup or
does without it. It is also where the answer can be *reported* well: pssparser
has locations and a marker catalogue; pssc's IR does not populate `Expr.loc` at
all today.

Bit layout is not a front-end question. `packed_s<>` field order is a target
representation, and the two targets already disagree about it on purpose — the C
emitter documents its own as *"declaration order — LSB-first, the opposite of
SV"*. Pushing that into the front end would put a target detail in the one
component that must stay target-neutral.

So: **pssparser answers "which field", pssc answers "which bits".** Each is
implemented once. pssc's by-name lookup is an index into a layout it computed
itself, over a name the front end has already proven exists — a miss there is an
internal error, not a user diagnostic.

The remaining §21.14.1 rule, **a zero mask on an explicitly-named field**, is
pssc's because it is a statement about folded *bits*, not about a name. It
exists because of defect D5 (§0): a field named in a `write_masked` literal that
contributes no bits writes nothing, which is never what the author meant and is
precisely what `{.ch_en=~0}` currently folds to.

---

## Phase 1 — pssparser: resolve the field name

**This is the enforcement point.** Every §21.14.1 rule about *what a name means*
is decided here, at link time, and reported as an ordinary linker error with
file/line/column. Nothing downstream may be the first place a bad field name is
noticed.

**Home:** `src/TaskResolveRefs.cpp` — the C++ pass that already checks calls —
**not** the Python plug-in checker framework. Two reasons, both measured (§0):

- the plug-in checkers run from the CLI only. `pssc.Parser` calls `parse()` and
  `link()` and never invokes a checker, so a rule implemented there is skippable
  by exactly the path that matters. §1.1 does not permit that.
- `TaskResolveRefs` already has, at the member-path element for `write_field`,
  both the resolved callee (`ISymbolFunctionScope`) and the receiver's symbol
  scope (`target_s`) — which is the specialized `reg_c<R, ACC, SZ>`. That is the
  whole of the context needed, and it is the same place `PSS006` is raised.

Iteration cost is low: `libpssparser.so` is `dlopen`ed at runtime, so the edit
loop is `ninja` in `packages/pssparser/build` (§0).

### Changes

- `src/TaskResolveRefs.{h,cpp}` — a `checkRegFieldRefs(elem, fn, target_s)`
  called from the same place as `checkCallArity`, firing when the callee is one
  of `write_field` / `write_fields` / `write_masked` on a register receiver:

  | Rule | Message shape | Source |
  |---|---|---|
  | name is not a field of `R` | `no field 'ch_enn' in register value type 'wb_dma_ch_csr_s' -- did you mean 'ch_en'?` | §21.14.1 |
  | name is an aggregate-typed field | `field 'sub' of 'R' has aggregate type; field-wise access applies to scalar fields only` | §21.14.1(c) |
  | name argument is not a string literal | `field name must be a string literal` | §21.14.1(a) |
  | name contains `.` | `field name must not be a hierarchical reference` | §21.14.1(b) |
  | duplicate name in one call | `duplicate field name 'mode'` | §21.14.1(d) |
  | names/values length mismatch | `write_fields: 2 names, 3 values` | implied |
  | `R` is not a struct | `register 'x' has no named fields: its value type is not a struct` | §21.14.1 |
  | RMW or read method on `ACC == WRITEONLY` | `cannot read register 'x' (WRITEONLY)` | §21.14.1, p.493 |

  Getting from `target_s` to `R`'s field declarations goes through
  `TaskGetSpecializedTemplateType`, which is already in the build. The
  `did you mean` suggestion reuses the same edit-distance helper as `PSS002`, so
  the two read alike.

- `python/pssparser/checkers/core_checker.py` — one new `MarkerDef`. **`PSS010`**
  is the next free id; the `PSS` prefix is reserved for `CoreChecker`, which is
  correct here because this *is* a core linker diagnostic. (The earlier draft's
  `PSSR0xx` ids were invalid on both counts and are withdrawn.)

### Tests

- **New** `tests/python/linking/test_reg_field_resolution.py` — one case per row
  of the table. Each asserts the link **fails** and that the message quotes the
  offending name; the unknown-name case additionally asserts the
  `did you mean 'ch_en'?` suggestion.
- **Extend** `tests/python/linking/test_reg_model_3_1.py` — Example356 still
  links clean (it is the regression that this phase does not over-reject).
- **New** a positive case per legal shape: multi-bit field, field at bit 0, field
  at the MSB, `write_fields` with two distinct names.

### Docs

- `packages/pssparser/docs` — `PSS010` in the marker catalogue, with one
  sentence stating the division of labour: pssparser decides *which field*, the
  compiler decides *which bits*.

### Exit criteria

- `python -m pytest tests/python -q` green, including the eight new negative
  cases.
- `pssparser --describe PSS010` prints the detail text.
- A `write_field("chan_en", 1)` typo is rejected **by `link()`**, therefore by
  `pssc` too, with no checker flag given.

---

## Phase 2 — pssc: bit layout, mask folding, and the shared reduction

The only place a mask constant is computed. Per §1.2 it does **not** decide
whether a field name is valid — phase 1 has already proven that — so a name it
cannot find is an internal error, not a user diagnostic.

### Changes

- `pssc/ast2ir.py` `_add_register_functions()` — add the four declarations,
  mirroring the existing `read`/`write` shape (`is_import=True, is_target=True`):
  - `write_val_masked(mask: bit[SZ], val: bit[SZ]) -> void`
  - `write_masked(mask: R, val: R) -> void`
  - `write_field(name: string, val: bit[SZ]) -> void`
  - `write_fields(names: list<string>, vals: list<bit[SZ]>) -> void`

  Guard on value type: `write_masked`, `write_field` and `write_fields` are
  declared only when the value type is a struct, so `reg_c<bit[32]>.write_field`
  fails as an unknown method.

- **New** `pssc/reg_field_resolve.py` — the single owner of packed-struct
  **layout**. Given a `DataTypeRegister` and a name, returns a `FieldSlice`
  (`lsb`, `width`, `mask`). It is the only code in the toolchain that maps a
  field to bits.

  Layout is declaration order, LSB-first — the same arithmetic
  `targets/c/lower_reg_model.py` performs inline today. An aggregate-typed field
  is recorded with `width == 0` rather than skipped, so it stays visible without
  shifting every field after it.

  It carries exactly one user-facing diagnostic, and it is a statement about
  bits rather than about a name (§1.2):

  | Condition | Message shape | Why |
  |---|---|---|
  | explicitly-named field folds to a zero mask in `write_masked` | `field 'ch_en' is given a zero mask ... note that ~0 currently reaches the compiler as 0 (pssparser defect D5)` | §0 |

  The other §21.14.1 conditions — unknown name, aggregate field, non-literal
  name, duplicates, length mismatch, `WRITEONLY` — are phase 1's, and reaching
  pssc with one of them means the front end let it through. `reg_rmw` reports
  those defensively rather than assuming, but they are not the plan of record
  for how a user learns about a typo.

- **New** `pssc/reg_rmw.py` — the reduction, run over the IR at the end of
  `ast2ir` translation so *every* consumer sees the reduced form. It calls
  `reg_field_resolve` and emits only folded constants; **no string reaches IR**:
  - `write_field(n, v)` → `write_val_masked(MASK(n), v << LSB(n))`
  - `write_fields([n…], [v…])` → one `write_val_masked` with the OR of the masks
    and the OR of the shifted values — **one** bus RMW, not N (this is the whole
    point of the plural form).
  - `write_masked(M, V)` → `write_val_masked(pack(M), pack(V))`
  - under `--reg-rmw=expand`: `write_val_masked(m, v)` →
    `t = read_val(); write_val((t & ~m) | (v & m));`

  Bit positions come from `reg.fields` + the packed-struct layout logic the C
  target already uses. **Move that layout helper into `reg_field_resolve` and
  have `targets/c/lower_reg_model.py` call it** rather than leaving two copies —
  LSB-first vs MSB-first is exactly the kind of thing that silently diverges
  (`targets/c/lower_reg_model.py` documents "declaration order — LSB-first, the
  opposite of SV"). After this phase there is one layout implementation with two
  callers, not two implementations.

- `pssc/cli.py` + `driver.py` — `--reg-rmw={expand,native}`. **Default `expand`**
  until phase 4 lands, then flip the default to `native` and keep `expand` as an
  escape hatch for a backend that has no RMW primitive.

### Tests

- **New** `tests/unit/test_reg_ir_equivalence.py` — **the invariant test for
  §1.1**, and the most important test in this plan. For each of several field
  shapes, the three spellings

  ```pss
  regs.csr.write_field("ch_en", 1);
  regs.csr.write_masked({.ch_en=~0}, {.ch_en=1});
  regs.csr.write_val_masked(32'h1, 32'h1);
  ```

  compile to **structurally identical IR** — compared node-by-node, not merely
  "all three are `write_val_masked` calls". Plus: a walk of the whole post-`ast2ir`
  IR asserting **no `write_field`/`write_fields`/`write_masked` call and no
  string-literal argument survives on any register receiver**. That walk is what
  keeps a future backend from ever needing a name table.
- **New** `tests/unit/test_reg_masked_ir.py`
  - each of the four methods appears on a `DataTypeRegister` after `ast2ir`;
  - `write_field("ch_en", 1)` on the real `wb_dma_ch_csr_s` reduces to a
    `write_val_masked` with the **exact** expected mask constant — spelled as a
    literal in the test, not recomputed by the same helper under test;
  - `write_fields({"use_ed","ch_en"}, {1,1})` reduces to **one** call with the
    OR'd mask;
  - a field at bit 0 and a field at the MSB, to pin the layout direction;
  - a multi-bit field (`prio`) — not just single bits.
- **New** `tests/unit/errors/test_reg_field_errors.py` — the zero-mask
  diagnostic (including the literal `{.ch_en=~0}` D5 shape, which must be
  **rejected**, not folded to a no-op write), plus the defensive backstops:
  a call whose receiver is not a register reachable from the enclosing
  component must be an error rather than silently pass through to a backend.
- **Extend** `tests/unit/test_ir_handoff.py` — the no-strings-in-IR walk runs on
  the real DMA model, not just synthetic snippets.
- **New** `tests/unit/test_reg_rmw_expand.py` — `--reg-rmw=expand` produces
  exactly `read_val` + `write_val` with the mask equation, and produces
  **nothing else** (no stray temporaries that would change evaluation order).

### Docs

- `packages/pssc/docs/progseq_design.rst` — a "Read-modify-write lowering"
  section: the reduction table, why `write_field` folds at compile time
  (§21.14.1(a)), and the `--reg-rmw` flag.

### Exit criteria

- New unit tests green; existing `tests/unit/test_named_register_types.py`,
  `test_register_array_bug.py`, `test_ast_to_ir.py` unchanged and green.
- With `--reg-rmw=expand`, a model using all four forms compiles end-to-end to
  **SV** with no phase 3 work.

**Correction, measured 2026-08-05.** The exit criterion above originally said
"to SV *and C*". That was wrong, and the error was in the baseline: §0 recorded
that `targets/c/lower_progseq.py` `_reg_call` matches only `("read", "write")`,
but the plan then assumed `expand` would land on `read_val`/`write_val` — which
that tuple does **not** contain either. The C target emits
`regs.csr.read_val()` — a generic call on a struct field that does not exist —
for expanded *and* native forms alike. So `expand` does not de-risk C; only
phase 4 does. This is the same `_reg_call` fall-through recorded as risk 3, one
step wider than the risk described.

---

## Phase 3 — pssc SV target: native `write_val_masked`

### Changes

- `pssc/share/sv/pssc_reg_pkg.sv` — one generic task on `reg_c`:

  ```systemverilog
  task write_val_masked(data_t mask, data_t val);
    data_t cur;
    read_val(cur);
    write_val((cur & ~mask) | (val & mask));
  endtask
  ```

  Type-generic, so it needs no per-register generation. It is also the single
  hook where a platform RMW instruction or a byte-enable write would later go —
  §21.14.1 permits, but does not require, that optimisation.
- `pssc/targets/sv/lower_progseq.py` — verify only. `write_val_masked` is void,
  so the value-returning rewrite at line 367 must **not** claim it. Add a
  regression asserting that.

### Tests

- **New** `tests/sv/test_reg_masked_sv.py` — generated SV contains
  `write_val_masked(...)` (not an inlined read/write pair) under
  `--reg-rmw=native`, and the file compiles under Verilator.
- **Extend** `tests/progseq/test_core_pkg.py` — `reg_c` exposes the new task.
- **New** simulation case in `tests/sim` — arm a channel via `write_field`,
  assert the observed bus traffic is read-then-write with the other CSR bits
  preserved. This is the only test in the plan that proves the *hardware-visible*
  behaviour is right.

### Docs

- `packages/pssc/docs/sv_runtime_library.md` — document the new task alongside
  `write_val`, including the explicit statement that it performs a read.

### Exit criteria

- Verilator-compiled SV model passes the new sim case.
- `--reg-rmw=expand` and `--reg-rmw=native` produce behaviourally identical bus
  traffic (assert on the same trace).

---

## Phase 4 — pssc C target: `_write_masked` accessor

### Changes

- `pssc/targets/c/lower_reg_model.py` — extend the baked accessor set from
  `_addr`/`_read`/`_write` to include `_write_masked(s, mask, val)`, same
  `static inline` style, same folded address arithmetic.
- `pssc/targets/c/lower_progseq.py` `_reg_call()` (line 188) — the method tuple
  becomes `("read", "write", "write_val_masked")`. **This line is the trap**: any
  method not in the tuple falls through to a generic call and emits code that
  compiles and is wrong. Add a defensive `raise` for unrecognised methods on a
  register receiver rather than silently falling through.
- Same for the C++ target if it shares this path — confirm during
  implementation (`tests/progseq/test_cpp_target.py` exists, so it is a real
  second consumer).

### Tests

- **New** `tests/progseq/test_c_reg_masked.py` — the accessor is emitted, the
  call site uses it, and `gcc -Wall -Werror` compiles the result (the existing
  `test_build_wb_dma_c.py` already establishes that harness).
- **Extend** `tests/progseq/test_op_model_reg_model.py` — the real DMA model with
  the phase 6 edits generates and builds.
- **New** negative test: an unrecognised register method raises a clear compiler
  error instead of emitting a generic call.

### Docs

- `packages/pssc/docs/progseq.rst` — the accessor trio becomes a quartet in the
  generated-API description.

### Exit criteria

- `tests/progseq` green, including the existing wb_dma C and C++ builds.
- Default flips to `--reg-rmw=native`; `expand` retained and still tested.

---

## Phase 5 — zuspec-be-sw: method-name sets

### Changes

- `zuspec-be-sw/passes/mem_reg_lower.py` — `write_val_masked` must be recognised.
  It is *both* a read and a write, so it cannot simply join `_REG_WRITE_METHODS`;
  emit an `SwRegRead` followed by an `SwRegWrite` (or a dedicated `SwRegRmw` node
  if the downstream C emitter would otherwise lose the ordering — decide when the
  emitter is read, and record the decision here).
- If the pass runs on IR that has **not** been through phase 2's reduction, it
  must also handle `write_field`/`write_masked`. Prefer wiring it to consume
  post-reduction IR; note the dependency explicitly in the pass docstring.

### Tests

- **New** `tests/…/test_mem_reg_lower_masked.py` — a `write_val_masked` call
  produces the read node and the write node, in that order, with the mask
  expression preserved.
- Regression: the existing read/write cases are untouched.

### Exit criteria

- zuspec-be-sw test suite green; the ISS and BFM modes both handle the new form.

---

## Phase 6 — the model: convert the four RMW sites

**Do this last**, so every backend can already carry the construct and the change
is verifiable end-to-end rather than on faith.

### Changes

| File | From | To |
|---|---|---|
| `wb_dma_ch_c/functions/transfer_single_start.pss:54-56` | read/assign/write | `regs.csr.write_field("ch_en", 1);` |
| `wb_dma_ch_c/functions/transfer_list_start.pss:42-48` | two RMWs | two `write_field` calls, **still two, still in that order** (§ steps 3 and 4 are deliberately separate writes) |
| `wb_dma_ch_c/functions/stop_channel_start.pss:49-51` | read/assign/write | `regs.csr.write_field("stop", 1);` |
| `wb_dma_ch_c/functions/set_auto_restart.pss:30-32` | read/assign/write | `regs.csr.write_field("ars", enable);` |

Each edit also drops the now-unused `wb_dma_ch_csr_s csr;` local.

**Style decision — settled, and it reversed.** The earlier draft recommended
`write_masked({.ch_en=~0}, {.ch_en=1})` on readability grounds, having concluded
that both forms were equally safe. §0 shows they are not: pssparser drops the
`~` (defect D5), so `{.ch_en=~0}` folds to a **zero mask** — a write that
selects no bits. The spec's own idiom is currently a trap.

So: **`write_field`**, which names no mask and therefore cannot be bitten. It is
also the shorter thing to read at the call site, and phase 2's zero-mask error
means a later reader who reaches for `write_masked` gets told rather than
silently getting a no-op. Use `write_fields` only where two fields genuinely
coalesce into one bus write, which in this model is **nowhere** (the two
`transfer_list_start` writes must stay separate). Record the decision — and the
D5 reason, so it can be revisited when D5 is fixed — in `src/pss/README.md`.

**Comments are not incidental here.** The ROC-read explanations at
`transfer_single_start.pss:51-53` and `stop_channel_start.pss:21-26` describe
behaviour that is *unchanged* by this edit and must be preserved — reworded only
to say "the masked write's read" instead of "the read". A reviewer seeing
`write_masked` and assuming the read is gone is the single most likely way this
change causes a bug later.

### Tests

- Existing SV/UVM regression must pass **unchanged** — this is a no-behaviour-
  change refactor and the burden of proof is that nothing moved.
- **New** `tests/…/test_rmw_bus_trace.py` (or an assertion added to the existing
  DMA sim test) — compare the observed Wishbone transaction sequence before and
  after the conversion; they must be identical.
- `tests/pss` elaboration unchanged.

### Docs

- `src/pss/README.md` — a short "Read-modify-write" note: which form the model
  uses and why, that masked writes still read (with the ROC consequence), and
  that `pause_engine`'s blind write is deliberate.
- `docs/op-model-export-design.md` — add `write_masked` to the construct
  inventory the export path must support, alongside the existing entries for
  component arrays, `packed_s<>` and `yield`.

### Exit criteria

- Identical bus trace before/after.
- `pssparser --checker reg-access src/pss` reports zero markers.
- Generated SV and C both build and pass their existing suites.

---

## Tracking

| # | Phase | Repo | Depends on | Status |
|---|---|---|---|---|
| 1 | Field-name resolution in `TaskResolveRefs` (`PSS010`) | pssparser | — | **done** |
| 2 | Bit layout + mask folding + IR builtins + `--reg-rmw` | pssc | — | **done** |
| 3 | SV `write_val_masked` | pssc | 2 | **done** |
| 4 | C `_write_masked` accessor (+ C++) | pssc | 2 | **done** |
| 5 | SW backend method sets | zuspec-be-sw | 2 | **done** |
| 6 | Convert the four model sites | fw-wb-dma | 1, 4 | **done** (see equivalence note) |

Phases 1 and 2 are independent in their *code* — one is C++ name binding, the
other Python bit arithmetic — and can be built in either order. They are not
independent in their *guarantee*: until phase 1 lands, a misspelled field name is
caught only by phase 2's defensive backstop, with a worse message and no source
location. Phase 6 should not land before phase 1 for that reason.

Phase 6 was expected to need nothing from 3/4/5, on the theory that
`--reg-rmw=expand` carries the construct on the existing primitives. That holds
for SV and **not** for C, which never handled `read_val`/`write_val` either (see
phase 2's exit criteria). Phase 6 therefore waits on phase 4.

**Progress log**

- *2026-08-05* — **phase 6's no-behaviour-change claim is now checked**, though
  not in the form the plan asked for. The plan wanted a before/after Wishbone
  trace diff; the op model has no simulation harness (the sim gate runs a
  different, simpler model), so that remains open and is honestly still open.

  What is closed is the step where the two forms could actually differ.
  `tests/progseq/test_op_model_rmw_equivalence.py` separates the claim in two:

  * **The transactions cannot differ in shape.** §21.14.1 defines the
    field-wise write as a read-modify-write; the reduction emits exactly one
    `write_val_masked` per site, lowering to one `read_val` and one `write_val`
    at the same address and width. The per-site count is asserted — including
    that `transfer_list_start` keeps *two*, since coalescing them would be one
    transaction where the device requires two, in order.
  * **The value written is the same.** The compiler folded a (mask, value) pair
    out of a field name; if its bit arithmetic disagreed with the field
    assignment it replaced, the register would take a different value with
    nothing to notice. So the folded pair is checked against an independent
    model of the old form, using each field's bit position transcribed by hand
    from `wb_dma_ch_regs_c.pss` rather than recomputed from the layout code
    under test, over starting values chosen to catch an inverted mask, a
    no-op write, and a mask one position out.

  The test was mutation-checked: moving `stop` from bit 9 to bit 8 in the
  transcribed table fails two assertions. A test of this kind that cannot fail
  is worse than none.

- *2026-08-05* — **the SV suite is green**: `1232 passed, 0 failed`, including
  `test_generated_package_lints_clean`, which lints the generated package with
  Verilator. That is phase 3's exit criterion, reached by closing the four
  defects the refreshed model exposed rather than by new phase-3 work.

  * **`status` was declared twice.** A PSS function returns a value; the SV
    lowering makes that a leading `output <T> status`, because anything that
    can consume time is a task and a task has no return value. A model that
    names its own result `status` -- the obvious name, and what this one uses
    -- then redeclared it. Suppressing the local is not a rename: every
    assignment to it already means "the value being returned".
  * **`message()` had no SV definition.** It is a PSS built-in, not part of the
    generated package, so emitting it verbatim referenced a task that does not
    exist. The mapping to `$display` already existed in `sv_builtins` for the
    other SV targets; `lower_progseq` was not consulting it.
  * **The `wb_dma_status_e` "missing typedef" was not a defect.** It is emitted;
    the test asserted a two-item enum and the model has three
    (`WB_DMA_PENDING`, which the non-blocking `check_completion()` answers).
    Another stale-snapshot assertion, updated.
  * **Width.** `(enable & 1) << 6` relied on a 32-bit mask literal to widen a
    one-bit value -- correct, but by accident of the constant's type, and
    Verilator flags it. `_place()` now casts a non-constant value to the
    register width first, which is what §21.14.1's equation is defined on:
    `(32'(enable) & 1) << 6`. This also needed `ExprCast` support in the C
    emitter, which had none.

  Separately: the C target cannot build this model at all —
  `c_type(DataTypeEnum)` raises. Pre-existing, unrelated to register access,
  and not tracked by this plan.

- *2026-08-05* — **phase 1 landed**, completing the plan. `checkRegFieldRefs()`
  in `TaskResolveRefs.cpp`, called alongside `checkCallArity()` where the
  receiver's scope is in hand; `PSS010` documented in the marker catalogue; 20
  new tests. All eight §21.14.1 rules are now link-time errors with file, line
  and column — `no field 'chan_en' in register value type 'csr_s'; did you mean
  'ch_en'? t.pss:13:44`. pssparser `2050 passed`; pssc `1230 passed`.

  Two things cost real time and are worth recording:

  * **`ninja` alone is not enough.** §0 said a C++ edit needs only `ninja`,
    which is half right: `ninja` rebuilds `build/src/libpssparser.so`, but
    `core.pyx` `dlopen`s `build/lib/libpssparser.so`, and only `ninja install`
    refreshes that. The first build appeared to do nothing at all — every
    diagnostic silently absent — because the stale library was still loaded.
  * **`resolvePath()` cannot reach a specialization.** It is a plain index walk
    over `getChildren()`, and `reg_c<csr_s,?,32>` lives in the base type's
    `getSpec_types()`. The super of a named register type therefore resolved to
    null and the walk gave up one link short. `TaskResolveSymbolPathRef` is the
    one that understands `SymbolRefPathElemKind`; it is what
    `TaskGetSpecializedTemplateType` itself uses.

  The consequence for pssc: five of its own error tests were no longer
  reachable from source, because the front end now rejects the model at
  `link()` before `translate()` runs. They were rewritten to assert the link
  failure — which is what a pssc user actually sees — rather than deleted, and
  pssc keeps its checks as unreachable backstops.

- *2026-08-05* — **phase 6 landed**, ahead of phase 1. The dependency on phase 1
  was a message-quality one, not a safety one: pssc already rejects an unknown
  field name (with a `did you mean` suggestion), so the conversion is safe
  without the front-end work. All five sites reduce, verified against the
  register's own bit comments — `ch_en` 0 → `0x1`, `ars` 6 → `0x40`, `use_ed`
  7 → `0x80`, `stop` 9 → `0x200`, and `set_auto_restart`'s value stays a runtime
  expression while its mask folds.

- *2026-08-05* — pssc baseline captured: `1181 passed, 5 skipped, 7 xfailed`.
  Phase 1 relocated from the Python checker layer to `TaskResolveRefs.cpp`
  (§1.2) after review. D5 discovered; §0, §1.2 and phase 6 updated accordingly.
- *2026-08-05* — **phase 2 landed.** `reg_field_resolve.py` (layout),
  `reg_rmw.py` (reduction + expansion), four IR builtins, `--reg-rmw`, and the
  C register-model emitter converted to call the shared layout. `1220 passed`
  (+39), no regressions. All four spellings reduce to one `write_val_masked`
  with folded constants; `write_fields` coalesces to a single bus RMW.
- *2026-08-05* — **phase 3 largely landed**: `write_val_masked` added to
  `pssc_reg_pkg.sv`. The SV value-returning rewrite needed no change — it
  claims only zero-argument `read`/`read_val`/`get`, and `write_val_masked` is
  neither.
- *2026-08-05* — **phase 4 landed.** C gained `_read_val` / `_write_val` /
  `_write_masked` accessors; `_reg_call` now resolves the receiver *before*
  the method name and **raises** on an unrecognised register method. C++ needed
  only `write_val_masked` on the `pssc::reg` template (risk 5 resolved). Both
  compile `-Wall -Wextra -Werror` clean in `native` and `expand`. `1229 passed`.
- *2026-08-05* — **phase 5 landed.** The open question in that phase —
  `SwRegRead`+`SwRegWrite` versus a dedicated node — resolves to **`SwRegRmw`**,
  and the reason is stronger than "the emitter might lose the ordering": there
  is no downstream emitter for these nodes at all, and `sw_nodes` is a flat
  per-type list with no ordering against the statements it came from. The pair
  could not express "one operation, one register, and the read may not be
  dropped" even in principle. The field-name forms are refused outright, since
  this pass has no layout to fold them with. zuspec-be-sw has a large
  **pre-existing** failure baseline (80 failed / 541 passed); measured identical
  with and without this change.

**Two pre-existing defects found by generating the code rather than trusting
the IR**, both in the SV target, both silent:

1. **`StmtAnnAssign` discarded its initializer.** `int x = 5;` lowered to
   `int x;` — compiles, runs, computes with zero. Reached by expansion, whose
   temporary is initialised from a register read: the read disappeared and the
   generated task consumed an uninitialised variable. Fixed, with a regression
   test in `tests/progseq/test_generate.py`.
2. **A declaration cannot go where the masked write was.** SystemVerilog allows
   a variable declaration only at the start of a block, so a temporary emitted
   at the expansion site is illegal the moment any statement precedes it, or the
   write sits inside an `if` arm. `_Expander` now hoists declarations to the top
   of the body and emits the read as an assignment.

Neither was visible in the IR, which was correct throughout. Worth stating
because the plan's own test strategy leaned on IR-level assertions: they are
necessary and they were not sufficient.

**The checked-in copy of the model was stale, and refreshing it exposed four
more pre-existing defects.** `pssc/examples/op_model/pss` is a snapshot of
`src/pss`; phase 6 required refreshing it, and the snapshot turned out to
predate both the `\init` → `initialize` rename and the two-profile
`wb_dma_cfg_pkg` split. Its own docstring names this failure mode — *"a
regression suite that passes against a stale model is a regression suite that
reports the wrong thing"* — and that is exactly what had happened:

1. **`sync_op_model.py` destroyed `files.f`.** It copies `.f` files *from*
   upstream, and upstream deleted `src/pss/files.f` on the grounds that a static
   list goes stale while a derivation cannot. The refresh therefore left the
   example with no file order at all. Fixed by deriving the order at sync time
   from `src/pss/flow.yaml`'s own `src` task via `pss-order` — which also picks
   the build profile correctly, since a tree holding both `wb_dma_cfg_pkg` files
   does not elaborate.
2. **`initialize` was not recognised as a constructor.** `_CTOR_NAMES` listed
   `ctor` and `init`; the renamed model's constructor was classified as an
   ordinary export function and the generated SV class built its register model
   from an undeclared `base`. The defect the existing comment describes,
   reproduced by the rename it did not anticipate. Fixed — including
   `set_ctor_name()`'s fallback, which repeated the literal and silently undid
   the fix until it too was changed.
3. **String literals were emitted with Python `repr()`.** `'x'` in SystemVerilog
   starts a based literal, so every `message()` in the model was a syntax error.
   Fixed.
4. **A fifth, found by reading the generated SV for the converted model.**
   `set_auto_restart`'s value expression `(enable & 1) << 6` printed *flat* as
   `enable & 1 << 6`, which SystemVerilog reads as `enable & (1 << 6)` -- so
   the one place in this model whose masked write has a runtime value wrote
   **zero for every value of `enable`**. The IR was correct; the emitter
   discarded the tree's grouping. Fixed the same way as the C target, by
   bracketing a binary operand that is itself a binary expression. Worth
   stating plainly: this is the bug the whole plan is meant to prevent -- a
   register write that is silently wrong -- and it survived every IR-level
   test, reaching the last end-to-end read of the output.

5. **Two SV defects remain open** (`test_enum_typedefs_and_mnemonics`,
   `test_generated_package_lints_clean`): the `wb_dma_status_e` typedef is not
   emitted, and a function whose return is lowered to an `output status`
   argument collides with a model-declared local of the same name
   (`wb_dma_status_e status;` declared twice). Both are in the blocking layer,
   which the stale snapshot did not contain. **Out of scope for this plan** —
   they are SV-target bugs with nothing to do with masked register access — but
   they are the only two failures in the suite and should be tracked separately.

---

## Risks and open questions

1. **Packed-struct bit order.** The C target notes its layout is "declaration
   order — LSB-first, the opposite of SV". If the mask constant is computed with
   one convention and the value packed with the other, the result is wrong bits
   with no diagnostic anywhere. Mitigation: `reg_field_resolve` is the sole
   layout implementation (phase 2) with the C target converted to call it, plus
   the bit-0/MSB/multi-bit tests, plus the §1.1 equivalence test. This is also
   why §1.2 leaves layout in pssc: putting it in the front end would make the
   target-specific bit order a front-end concern, which is the wrong shape of
   mistake to institutionalise.
2. **`write_fields` coalescing.** Reducing N field writes to one bus RMW is
   correct per the LRM and is the point of the plural form — but it is a
   *behaviour* difference from N separate RMWs on a register with side effects on
   read. The model does not use it (see phase 6), and phase 2's test pins the
   single-call behaviour so nobody "fixes" it into a loop.
3. **The `_reg_call` fall-through** (`targets/c/lower_progseq.py:188`). Today an
   unrecognised register method silently becomes a generic call. Phase 4 turns
   that into a hard error; that may surface pre-existing latent cases in other
   models. Worth running the full pssc corpus once the `raise` is in.
4. ~~**Marker id range.**~~ Resolved 2026-08-05: the `PSS` prefix is reserved
   for `CoreChecker` (`markerdef.py`), which is the right catalogue for a linker
   diagnostic. `PSS001`–`PSS009` are taken; phase 1 uses **`PSS010`**. The
   draft's `PSSR0xx` ids were invalid and are withdrawn.

7. **pssparser defect D5 — the live hazard.** `~0` reaches the compiler as `0`,
   so the LRM's own `{.f=~0}` mask idiom is a silent no-op (§0). This plan works
   around it: phase 2 rejects a zero mask on a named field, and phase 6 avoids
   mask literals entirely. Both should be revisited when D5 is fixed — the
   zero-mask error stays useful, but `write_masked` becomes usable again and the
   phase-6 style decision is worth re-opening. Fixing D5 is **not** in scope
   here.

8. **Phase 1 is C++, which the earlier draft did not account for.** It needs a
   `ninja` rebuild of `libpssparser.so` (cheap — it is `dlopen`ed, §0) but it is
   still front-end work in a language the rest of this plan does not touch, and
   getting from a receiver symbol scope to a template argument's field
   declarations is the least-charted step in the plan. If it proves larger than
   estimated, phases 2–5 are unaffected; only phase 6's landing order is.
5. ~~**Unverified:** whether the C++ progseq target shares `_reg_call`.~~
   Resolved 2026-08-05: it does **not**. `targets/cpp/lower_progseq.py` is a
   separate 249-line emitter that keeps *native member-call* register access
   (`regs_.csr.write_val_masked(1, 1)`) against the `pssc::reg<T, ACC>`
   template. So C++ needed no method table at all — the work was one method on
   that template in `share/cpp/pssc_reg.hpp`, the same shape as the SV change.
6. **Still open: an end-to-end bus-trace diff for phase 6.** The op model has
   no simulation harness — `test_sim_wb_dma.py` drives a different, simpler
   model — so "the observed Wishbone traffic is identical before and after" is
   argued (transaction shape) and checked (value written) rather than measured.
   Building that harness is worth doing on its own merits, not just for this.

7. **Not in scope, deliberately:** a byte-enable / no-read masked write. The LRM
   defines these methods as read-modify-write, so it would be non-conforming.
   `pssc_reg_pkg.sv::write_val_masked` is where it would go if a future device
   needed it.
