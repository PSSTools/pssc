# Masked & Field-Wise Register Access — Implementation Status

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

**For review** · 2026-08-05 · Companion to `docs/reg-masked-access-plan.md`

All six phases of the plan are implemented. This document is what to review and
in what order; the plan remains the record of *why* each phase is shaped the way
it is, and its progress log carries the per-phase detail.

---

## 1. Result

The operation model now expresses its read-modify-write register updates in the
LRM's own vocabulary:

```pss
regs.csr.write_field("ch_en", 1);          // was: read / assign field / write
```

which reaches SystemVerilog as

```systemverilog
m_regs.csr.write_val_masked(1, 1);      // as of phase 7, spelled
m_regs.csr.write_field(WB_DMA_CH_CSR_ch_en, 1);   // -- see §7b
```

and C as

```c
pss_top_regs_csr_write_masked(s, 1, 1);
```

The field name is resolved by the front end and folded to a constant mask by the
compiler. **No field-name string reaches any backend**, and a misspelled name is
a link-time error rather than a wrong register bit.

### Test evidence

| Repo | Before | After |
|---|---|---|
| `pssc` | 1181 passed | **1244 passed, 0 failed** |
| `pssparser` | 2030 passed | **2050 passed** |
| `zuspec-be-sw` | 80 failed / 541 passed (pre-existing) | unchanged; `test_mem_reg_lower.py` 18 passed |

`zuspec-be-sw` has a large pre-existing failure baseline unrelated to this work
(missing external fixture modules). It was measured identical with and without
these changes before anything was added.

---

## 2. The design changed during implementation — this is the main thing to review

The plan as written put **all** field-name resolution in `pssc`. That was
wrong, and the correction came from review mid-implementation. It conflated two
questions:

| Question | Kind | Owner |
|---|---|---|
| Is `"ch_en"` a field of this register's value type? Is it scalar? Named twice? | **name binding** | **pssparser** |
| Which bits is that field, given `packed_s<>`? | **bit layout** | **pssc** |

Name binding is what a front end does — pssparser resolves every other name in
the language, and `"ch_en"` is a name. Resolving it anywhere else means every
future consumer of the parser re-implements the lookup or does without it.

Bit layout is *not* a front-end question: `packed_s<>` order is a target
representation, and the two backends order it oppositely on purpose (the C
emitter documents its own as "declaration order — LSB-first, the opposite of
SV"). Pushing it into the front end would put a target detail in the one
component that must stay target-neutral.

Two measurements settled *where* in pssparser:

- **The Python checker layer cannot be the enforcement point.** `pssc.Parser`
  calls `parse()`/`link()` and never invokes a checker, so a rule implemented
  there is skippable by exactly the path that matters.
- **`TaskResolveRefs.cpp` already had the context.** It walks each member-path
  element with the receiver's symbol scope in hand and the callee resolved —
  that is how `PSS006` already reports argument-type mismatches.

§1.2 of the plan records this split. **If the split is wrong, most of phase 1
and part of phase 2 are in the wrong repo**, so it is worth disagreeing with
now rather than later.

---

## 3. What to review, by repo

### `packages/pssparser` — phase 1

| File | Change |
|---|---|
| `src/TaskResolveRefs.{h,cpp}` | **new** `checkRegFieldRefs()`, `regValueStruct()`, `resolveRegField()`; one call added beside `checkCallArity()` |
| `python/pssparser/checkers/core_checker.py` | **new** `PSS010` marker definition |
| `tests/python/errors/test_reg_field_resolution.py` | **new**, 20 cases |

All eight §21.14.1 rules are link-time errors with file, line and column:

```
no field 'chan_en' in register value type 'csr_s'; did you mean 'ch_en'? t.pss:13:44
```

Review focus: `regValueStruct()` walks the super chain to find the
`reg_c<R, ACC, SZ>` specialization and reads its bound `R`. It handles both a
named register type (`pure component csr_r : reg_c<csr_s, ...>`, one link) and
an inline `reg_c<csr_s, ...> csr;` (no link). The loop is bounded at 32 rather
than "until super is null", because a cycle would otherwise hang the parse.

The tests include deliberate **false-positive guards**: the five `write_field`
sites in `src/pss` must still link, and a user type with its own `write_field`
method must not be judged against a register's fields.

### `packages/pssc` — phases 2, 3, 4

| File | Change |
|---|---|
| `src/pssc/reg_field_resolve.py` | **new** — sole owner of `packed_s<>` layout |
| `src/pssc/reg_rmw.py` | **new** — the reduction, and `--reg-rmw=expand` |
| `src/pssc/ast2ir.py` | four register builtins; reduction invoked at end of translation |
| `src/pssc/cli.py`, `driver.py` | `--reg-rmw={native,expand}` |
| `src/pssc/share/sv/pssc_reg_pkg.sv` | `write_val_masked` task |
| `src/pssc/share/cpp/pssc_reg.hpp` | `read_val` / `write_val` / `write_val_masked` on `pssc::reg` |
| `src/pssc/targets/c/lower_reg_model.py` | `_read_val` / `_write_val` / `_write_masked` accessors; layout now delegated |
| `src/pssc/targets/c/lower_progseq.py` | `_reg_call` hardened (see §5); `ExprCast`; operand bracketing |
| `src/pssc/targets/sv/lower_progseq.py` | four defect fixes (see §5) |

New tests: `test_reg_ir_equivalence.py`, `test_reg_masked_ir.py`,
`test_reg_rmw_expand.py`, `errors/test_reg_field_errors.py`,
`progseq/test_c_reg_masked.py`, `progseq/test_op_model_rmw_equivalence.py`.

Review focus:

- **`test_reg_ir_equivalence.py` is the invariant test.** All spellings must
  produce identical IR (modulo `loc`), and a walk of the whole IR asserts no
  register receiver carries a field-name method or a string argument. That walk
  is what stops a future backend from ever needing a name table.
- **`reg_rmw.check_reduced()`** refuses to hand a backend an unreduced call.
  The reduction follows `self`-rooted paths *and* action `exec` bodies, which
  reach registers through `comp` — without that, legal PSS would be rejected.
- **The zero-mask error** is pssc's one user-facing field diagnostic, and it
  exists because of pssparser defect D5 (below).

### `packages/zuspec-be-sw` — phase 5

`SwRegRmw` (new node), recognised in `mem_reg_lower.py`; field-name forms
refused outright. The plan left open whether to use `SwRegRead`+`SwRegWrite`;
the answer is a dedicated node, and for a stronger reason than the plan
anticipated: `sw_nodes` is a flat per-type list with no ordering against the
statements it came from, so the pair could not express "one operation, one
register, and the read may not be dropped" even in principle.

### `fw-wb-dma` — phase 6

Five call sites converted in `src/pss/wb_dma_ch_c/functions/`
(`transfer_single_start`, `transfer_list_start` ×2, `stop_channel_start`,
`set_auto_restart`), plus a "Read-modify-write" section in `src/pss/README.md`.

Every comment describing the ROC-clearing read was preserved and reworded to
say the read is still there. That is deliberate: a reviewer seeing
`write_field` and concluding the read is gone is the most likely way this
change causes a bug later.

---

## 4. The hazard the plan worked around: pssparser defect D5 — now fixed

*Updated 2026-08-06. What follows is the original finding; the resolution is
below it.*

`AstBuilderInt::visitExpression` discarded the operator of a unary expression, so
`~0` reached the compiler as `0`. The LRM's own mask idiom —

```pss
regs.csr.write_masked({.ch_en=~0}, {.ch_en=1});     // Example356's shape
```

— therefore folded to a **zero mask**: a write that selects no bits. Silently.

Two consequences, both live while D5 was open:

- `pssc` **rejected** a zero mask on an explicitly-named field rather than
  folding it, and the message named D5 so the reader was not left guessing.
- The model uses **`write_field`**, which names no mask and cannot be bitten.
  The plan's original recommendation was `write_masked` on readability grounds;
  that reversed on this evidence.

### Resolution

D5 is fixed — see `docs/pssparser-defects-2026-08-02.md`, which carries the full
entry including the two further defects that were sitting behind it. What
changed for this work:

- **The LRM idiom folds correctly.** `{.ch_en=~0}` now reduces to `ch_en`'s
  bits, and `test_reg_masked_ir.py` asserts the folded constants for both a
  one-bit and a three-bit field. `test_reg_field_errors.py`'s D5 test is
  inverted accordingly: the idiom is asserted *accepted*, not rejected.
- **The zero-mask error stays**, on its own merits — naming a field in a mask
  literal and selecting none of its bits is meaningless however it was spelled.
  Its message no longer blames D5.
- **`reg_rmw._const` folds unary operators.** Without that the mask still
  lowered to correct bits, but as an unfolded `ExprBin` rather than a literal —
  which would have broken the §3 invariant that a resolved mask reaches the
  backend as a constant. This is the one place the fix required a change *in*
  the register work rather than around it.
- **Phase 6's style decision is now genuinely open**, as this section
  anticipated. Both forms work. My recommendation is to leave the five call
  sites on `write_field`: for a write that names exactly one field it reads
  better than a two-literal `write_masked`, and the comments preserved in §3
  are already written against it. Changing them back would be churn against
  tested code for no correctness gain.

The model's five `!` guards — a bigger exposure than this section knew about,
and unrelated to registers — are covered in §5.

---

## 5. Defects found and fixed that were *not* in the plan's scope

These were all pre-existing, all silent, and all in shared toolchain. They are
listed separately because they broaden the review surface beyond register
access.

| # | Where | Defect |
|---|---|---|
| 1 | `sv/lower_progseq.py` | `StmtAnnAssign` **discarded its initializer** — `int x = 5;` lowered to `int x;` |
| 2 | `sv/lower_progseq.py` | String literals emitted with Python `repr()`; `'x'` starts a based literal in SV, so every `message()` was a syntax error |
| 3 | `sv/lower_progseq.py` | A function's `output status` collided with a model-declared local `status` |
| 4 | `sv/lower_progseq.py` | `message()` emitted verbatim — a call to a task that does not exist |
| 5 | `sv/lower_progseq.py` | **Binary operands not bracketed** — see below |
| 6 | `c/lower_progseq.py` | `_reg_call` tested the method name *before* resolving the receiver, so every register method outside `("read","write")` fell through to a generic call |
| 7 | `c/lower_progseq.py` | No `ExprCast` support |
| 8 | `targets/progseq_model.py` | `initialize` not recognised as a constructor |
| 9 | `scripts/sync_op_model.py` | Destroyed `files.f` on every refresh |
| 10 | `pssparser/src/AstBuilderInt.cpp` | **D5** — every unary operator discarded; five `!` guards in this model generated inverted (2026-08-06) |
| 11 | `pssc/ast2ir.py` | `_map_unaryop` used the wrong enum ordinals, transposing `!` and `+`; unreachable while D5 hid it |
| 12 | `pssc/ast2ir.py` | `_map_binop` had no entry for `**`, so exponentiation silently lowered to addition |

Defects 10–12 landed on 2026-08-06, after the rest of this document. They are
listed here because they are the same failure mode as defect 5 — an expression
that compiles, lints clean, and computes something other than what the model
says — and because 11 and 12 illustrate a hazard worth naming: **a defect that
suppresses a whole node type also suppresses every bug in the code that consumes
it.** `_map_unaryop`'s own docstring read "best guess based on common
conventions", and no test could reach it to check.

**Defect 5 is the one to look at.** Reading the generated SV for the converted
model:

```systemverilog
m_regs.csr.write_val_masked(64, enable & 1 << 6);      // before
```

SV binds `<<` tighter than `&`, so that parses as `enable & 64` — **zero for
every value of `enable`**. The IR tree was `(enable & 1) << 6` and correct
throughout; the emitter discarded its grouping. `set_auto_restart` is the one
site in this model whose masked write has a runtime value, so it was the one
site affected.

That is exactly the failure this plan exists to prevent — a register write that
is silently wrong — and **it survived every IR-level test**. It surfaced only
because the generated output was read. The same class of bug in the C emitter
was caught by `gcc -Wparentheses -Werror`; SV has no equivalent, and Verilator
only warns about the related width issue.

The lesson worth carrying: IR-level assertions were necessary and were not
sufficient. Defects 1–5 were all invisible in the IR, which was correct
throughout.

**Defect 6** deserves a second look during review: `_reg_call` now *raises* on
an unrecognised register method. That is the fix for a silent fall-through, but
it is a behaviour change for any other model in the corpus that reaches a
register method the C target does not implement.

---

## 6. Changes I made that a reviewer should specifically sanction

1. **`scripts/sync_op_model.py` now derives the file order** from
   `src/pss/flow.yaml`'s `src` task via `pss-order`, instead of copying a
   `files.f` that upstream deleted on purpose. This also selects the build
   profile, which it must — a tree holding both `wb_dma_cfg_pkg` files does not
   elaborate. I changed this because refreshing the snapshot (required by phase
   6) destroyed `files.f` and the script could not reproduce it.

2. **The checked-in model snapshot was stale**, predating both the
   `\init` → `initialize` rename and the two-profile split. Its own docstring
   names the hazard — *"a regression suite that passes against a stale model is
   a regression suite that reports the wrong thing"* — and that is what had
   happened. Refreshing it exposed defects 8 and 9 and three stale test
   assertions.

3. **Test assertions I updated rather than code I fixed** — each of these was a
   test asserting the *old* model, not a defect:
   - `test_enum_typedefs_and_mnemonics`: expected a two-item
     `wb_dma_status_e`; the model has three (`WB_DMA_PENDING`).
   - `test_constructors_present` and friends: looked for `init`.
   - `test_init_lowered_to_construction`: pinned an address expression's
     unbracketed spelling. Same arithmetic, new brackets.
   - `test_no_declared_type_is_lost_in_either_file_order`: reversed the file
     list, which the model no longer permits — `compile if` on a package
     constant is order-dependent by §19.1.2. Now reverses everything *except*
     the build-profile package, which preserves the test's actual intent
     (extends and actions must not be lost when their files move).

4. **Five pssc error tests were rewritten** to assert a link failure instead of
   a translation error, because phase 1 now rejects those models before
   `translate()` runs. pssc keeps its checks as unreachable backstops.

---

## 7. An incident worth recording

Mid-session I ran `git stash push -- src/pss` on the working tree to attribute a
test failure. The stash/pop round-trip **resurrected seven files** that had been
deleted as part of the in-progress `wb_dma_ops_c` → `wb_dma_c` rename (the
`RD`-status entries in `git status`).

I verified all seven referenced the old component name and that
`blocking_ops.pss` / `blocking_ops_a.pss` supersede them, removed them, and
confirmed the tree matched its prior state file-for-file. Nothing was lost.

The right tool was a worktree, not a stash, against a tree with an uncommitted
refactor in it. Flagged because the recovery was verified by reasoning about
file contents rather than by a checkout, so a second pair of eyes on
`git status` for `src/pss` is worth the minute it costs.

---

## 7b. Phase 7 — field names restored in the generated SystemVerilog

*Added 2026-08-07.*

The generated SV said `m_regs.csr.write_val_masked(64, (32'(enable) & 1) << 6)`.
It now says

```systemverilog
m_regs.csr.write_field(WB_DMA_CH_CSR_ars, 32'(enable));
```

with one `localparam reg_field_t <VALUE_STRUCT>_<field>` emitted per scalar
field of every register value struct. Those constants are also the only way a
hand-written testbench could name a CSR bit without re-deriving the layout,
which is at least half the reason to have them.

**§3's invariant is not relaxed.** Nothing new enters the IR. `reg_rmw` still
folds every field-wise write to a constant (mask, value) pair, and the SV
emitter restores the name on the way *out* by asking the register model which
field has exactly those bits (`targets/sv/reg_field_names.py`). The C target
consumes the identical IR and required no change.
`test_sv_reg_fields.test_naming_does_not_touch_the_ir` generates both spellings
from one context and asserts the folded pairs are unchanged around each run, so
"presentation only" is measured rather than asserted.

`--sv-reg-fields=folded` emits the pre-naming literals. It exists so the
collapsed form the other backends see stays reachable for diffing, and the
constants are still emitted under it.

Decisions worth sanctioning:

- **`localparam`, not `enum`.** An SV enum with no explicit base type is `int`
  — signed — so a mask on bit 31 is negative and a 64-bit register's mask does
  not fit at all. The two members also want different types (a mask is a sized
  vector, a shift is an `int`), which one enum cannot be.
- **The field keeps its source spelling** (`WB_DMA_CH_CSR_ch_en`, not
  `..._CH_EN_MASK`), so the identifier round-trips to the PSS declaration under
  grep, and typing the register's prefix lists its fields.
- **The prefix comes from the value struct, not the register instance.** `csr`
  is ambiguous in this model — the channel bank and the global bank both have
  one — and struct typedef names are already unique in the package.
- **`write_fields` was added to `reg_c`** for the multi-field case, coalescing
  into one read-modify-write. This model never emits it (see §5 of
  `src/pss/README.md`: `transfer_list_start` requires two ordered writes, and
  `test_transfer_list_still_writes_twice` pins that), but the reduction can
  produce a multi-field mask and the backend should not have to refuse it.

**The end-to-end gap in §8 is narrower than it was, though not closed.**
`test_sv_reg_fields.test_field_writes_produce_the_right_register_value` compiles
the *real* generated `pssc_reg_pkg.sv` and the *real* generated constants under
Verilator against a stub bus and checks the resulting register word — including
that an RMW clears a set bit, that an over-wide value truncates to its field,
and that one masked write is exactly one read plus one write. That is the first
time generated SV from this work has been executed rather than only asserted
over, which is what §5 said was missing. It still is not a Wishbone trace of the
op model.

Two width defects were found by running it, both fixed, both invisible to any
structural assertion:

1. A value argument narrower than `data_t` is an implicit widening, which
   Verilator treats as a fatal WIDTHEXPAND. The call site therefore keeps the
   `32'(...)` cast `_place()` inserts — `write_field(F, enable)` does not
   compile, `write_field(F, 32'(enable))` does.
2. `read_field` originally extracted under a `64'(...)` cast; the cast's width
   propagates *into* the `&`, extending both operands and failing the same way.
   It now extracts at `data_t` width and widens in a second statement.

Verified: `pssc` 1266 passed / 0 failed; `dfm run tests` 14/14; the generated
pair lints clean at Verilator's default settings (warnings fatal — `-Wno-fatal`
is deliberately not passed, since it would hide exactly these two defects). The
new tests were mutation-checked by shifting every emitted constant one bit,
which fails both the hand-transcribed layout test and the simulation.

## 8. Open items

| Item | Status |
|---|---|
| **End-to-end bus-trace diff for phase 6** | **Still open**, but narrowed — generated SV is now executed under Verilator (§7b). See below. |
| C target cannot build the full op model — `c_type(DataTypeEnum)` raises | Open, pre-existing, unrelated |
| pssparser defect D5 (`~0` → `0`) | **Closed 2026-08-06** — see §4 |
| `examples/op_model/pss` snapshot has drifted from `src/pss` | Open — drift is now larger: `wb_dma_cfg_pkg/` was replaced by a single `wb_dma_cfg_pkg.pss` (see `target-cfg-contract.md`; the interim `target_cfg_pkg/` stub was removed again). Run `sync_op_model.py --src` |

### On the bus-trace diff

The plan asked for a before/after Wishbone trace comparison to prove phase 6 is
a no-behaviour-change refactor. **I did not do that.** The op model has no
simulation harness — `test_sim_wb_dma.py` drives a different, simpler model —
and building one is real work worth doing on its own merits.

What is closed is the step where the two forms could actually differ, split in
two by `tests/progseq/test_op_model_rmw_equivalence.py`:

- **The transactions cannot differ in shape.** §21.14.1 defines the field-wise
  write as a read-modify-write; the reduction emits exactly one
  `write_val_masked` per site, lowering to one `read_val` and one `write_val` at
  the same address and width. Per-site counts are asserted, including that
  `transfer_list_start` keeps **two** — coalescing them would be one
  transaction where the device requires two, in order.
- **The value written is the same.** The folded (mask, value) pair is checked
  against an independent model of the old form, using each field's bit position
  transcribed by hand from `wb_dma_ch_regs_c.pss` rather than recomputed from
  the layout code under test, over starting values chosen to catch an inverted
  mask, a no-op write, and a mask one position out.

The test was mutation-checked: moving `stop` from bit 9 to bit 8 in the
transcribed table fails two assertions.

This is a weaker claim than a measured trace, and it is stated as such. What it
does not cover is anything outside the two forms' arithmetic — a backend that
reordered or elided a transaction would not be caught here.
