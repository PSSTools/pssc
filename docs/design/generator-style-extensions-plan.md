# Pluggable generator styles — implementation, test and doc plan

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Companion to `docs/generator-style-extensions-design.md`. That document argues
*what* and *why*; this one is the work breakdown, the test strategy, and the
tracking surface.

Status: **Phases 0, 1, 1b, 2, 3 and 4 complete** (2026-08-14). Update the checkboxes in §2 as
tasks land; record any deviation from a task's stated plan in that task's
block, under **Landed**.

---

## 1. How to read and track this

### 1.1 Task shape

Every task is branch-sized (half a day to two days) and carries:

| Field | Meaning |
| --- | --- |
| **Depends** | Task IDs that must land first |
| **Files** | What is touched or created |
| **Change** | The work, stated so a reviewer can check it was done |
| **Tests** | New/changed tests, by file and function name |
| **Docs** | Doc deliverable, if any |
| **Accept** | The condition under which the task is done — objective, checkable |

IDs are `P<phase>.T<n>` and are stable: never renumber, mark superseded tasks
struck through and add a successor.

### 1.2 Phase gates

A phase is done when every task in it is done **and** its gate holds. Gates are
listed per phase; they are the things that would otherwise be discovered three
phases later.

### 1.3 The rule that governs the whole refactor

> **Behaviour-preserving steps must be proven byte-identical, not asserted.**

Phases 2, 5a and 6a all claim "no change in generated output". That claim is
only worth anything if a machine checks it, which is why Phase 0 exists and
comes first. A refactor that changes output *and* is described as
behaviour-preserving is the single most expensive failure available here — it
poisons every subsequent golden file.

### 1.4 Running the suite

```
direnv exec . pytest tests/unit tests/progseq tests/dvflow      # default set
direnv exec . pytest -m "not sim and not c_toolchain"           # no toolchain
direnv exec . pytest tests/progseq -k golden                    # snapshots only
```

New markers introduced by this plan: `golden` (snapshot comparison),
`plugin` (needs the fixture plugin installed).

---

## 2. Tracking table

| ID | Task | Phase gate | Status |
| --- | --- | --- | --- |
| P0.T1 | Golden-snapshot harness | | ☑ |
| P0.T2 | Freeze baseline snapshots for all six op-model configurations | | ☑ |
| P0.T3 | `--check-golden` / regeneration workflow + CI wiring | G0 | ☑ |
| P1.T1 | Run `validate_calls` for C and C++ (I1) | | ☑ |
| P1.T2 | Re-key `call_legality.EXTENSIONS` to canonical names (I2) | | ☑ |
| P1.T3 | Replace global `set_ctor_name` with per-run state (I5) | | ☑ |
| P1.T4 | `comments.HASH` (O8) | G1 | ☑ |
| P1b.T1 | Rebuild `op-model-cpp` for the real model (I16) | G1b | ☑ |
| P2.T1 | `OpModel` dataclass + `elaborate()` | | ☑ |
| P2.T2 | `OpModelTarget` base class | | ☑ |
| P2.T3 | Port `op-model-sv` onto it | | ☑ |
| P2.T4 | Port `op-model-c` onto it | | ☑ |
| P2.T5 | Port `op-model-cpp` onto it | | ☑ |
| P2.T6 | Delete the three duplicated helper sets | G2 | ☑ |
| P3.T1 | Implement `targets.discover()` | | ☑ |
| P3.T2 | Registration policy: collisions, `replaces`, API version | | ☑ |
| P3.T3 | Plugin-error reporting in `pssc targets` / `compile` | | ☑ |
| P3.T4 | `call_legality.register_extension()` | | ☑ |
| P3.T5 | Option namespacing + collision error + `-X/--target-opt` | | ☑ |
| P3.T6 | Fixture plugin package under `tests/plugins/` | G3 | ☑ |
| P4.T1 | `pssc.testing` public kit | | ☑ |
| P4.T2 | `pssc.testing.conformance` suite | | ☑ |
| P4.T3 | dv-flow memento covers plugin version (I4) | | ☑ |
| P4.T4 | `register_filetype` + warn on unmapped output | | ☑ |
| P4.T5 | `pssc.resources.core_dir` + `Target.core_files()` | G4 | ☑ |
| P5a.T1 | `MemAccess` funnel object | | ☑ |
| P5a.T2 | Route `emit_accessor` through it | | ☑ |
| P5a.T3 | Route `_mem_call` / `_reg_call` through it | | ☑ |
| P5a.T4 | Golden proof of no-change | G5a | ☑ |
| P5b.T1 | `CStylePolicy` — naming/layout half | | ☑ |
| P5b.T2 | `pssc.styles` entry-point group + `--style` | | ☑ |
| P5b.T3 | Style access hooks over the funnel | | ☑ |
| P5b.T4 | Access-hook enforcement (direction, masked-write read) (I12) | | ☑ |
| P5b.T5 | `--link-style vtable` + bus-overriding policy rejection | G5b | ☑ |
| P6a.T1 | `COpModelBackend` class; free functions become methods | | ☑ |
| P6a.T2 | Named-section pipeline + `insert_after`/`replace` | | ☑ |
| P6a.T3 | `body_emitter_cls` / `style_cls` hooks | | ☑ |
| P6a.T4 | Golden proof of no-change | | ☑ |
| P6a.T5 | Thread `ctor_names` into the body emitters (finishes I5) | G6a | ☑ |
| P6b.T1 | `@overridable` decorator + surface manifest test | | ☑ |
| P6b.T2 | `pairs_with` enforcement at registration | | ☑ |
| P6b.T3 | `derives_from` — legality, options, `target_cfg` inheritance | | ☑ |
| P6b.T4 | `emit_extra_files` hook | | ☑ |
| P6b.T5 | `assert_differs_from_baseline` | | ☑ |
| P6b.T6 | Tier-B fixture extension exercising all of it | G6b | ☑ |
| P7.T1 | `BodyWalker` ABC + shared scans | | ☑ |
| P7.T2 | Port C `_BodyEmitter` onto it | | ☑ |
| P7.T3 | Port SV `_BodyEmitter` onto it | | ☑ |
| P7.T4 | Dispatch through `call_legality.Disposition` | G7 | ☑ |
| P8.T1 | In-tree `op-model-py` | | ☑ |
| P8.T2 | `--emit-manifest` | | ☑ |
| P8.T3 | Express one built-in variant as a tier-B subclass (O10) | G8 | ☑ |
| PD.T1–T8 | Documentation (see §5) | GD | ☑ |

---

## 3. Phases

### Phase 0 — Baseline harness (blocks everything)

No production code changes. This phase exists so the words "byte-identical"
mean something in phases 2, 5a and 6a.

---

**P0.T1 — Golden-snapshot harness**

* **Depends:** —
* **Files:** `tests/progseq/golden_util.py` (new),
  `tests/progseq/golden/` (new tree)
* **Change:** A helper that runs a named target over the checked-in WB DMA
  model (`tests/progseq/op_model.py::op_model_sources`) with a named option set,
  writes into a tmpdir, and compares every produced file against
  `tests/progseq/golden/<config>/`. Comparison is byte-exact on file content
  **and** on the returned path list *and its order* — the order is the
  compilation order a build system consumes, and a reordering is a real
  regression that content-only comparison misses.
  Generator-version strings in banners are normalised out before comparison
  (one regex, applied to both sides, documented in the helper) so a version
  bump is not a 300-file diff.
* **Tests:** the helper is the test infrastructure; `test_golden_harness.py`
  covers the helper itself — normalisation, order sensitivity, and that a
  deliberately corrupted expectation fails.
* **Accept:** `pytest tests/progseq -k golden` passes; hand-editing one byte of
  one golden file makes exactly one test fail with a readable diff.
* **Landed** (`tests/progseq/golden_util.py`, `test_golden_harness.py`):
  accept criterion verified by hand — replacing `uint32_t` with `uint32_T` in
  `golden/c-vtable/files/wb_dma.c` fails `test_golden[c-vtable]` alone, with
  the diff and the regeneration command in the message.
  * A config is spelled as the **argv a user would type**, and is run through
    `cli.build_parser()`, so a snapshot reflects the option defaults the CLI
    hands out rather than a hand-built namespace that can drift from them.
  * A core seam header the target copies out of `src/pssc/share/` is recorded
    in `MANIFEST` as a pointer (`pssc_mem.h -> share/c/pssc_mem.h`) instead of
    being stored a second time. This is *stronger* than storing a copy: it
    asserts the shipped bytes arrived unmodified, and it cannot go stale
    against the file it duplicates. A target that starts modifying a header
    while copying it loses its pointer and must be snapshotted — covered by
    `test_manifest_stops_calling_a_modified_core_header_a_copy`.

---

**P0.T2 — Freeze baseline snapshots**

* **Depends:** P0.T1
* **Files:** `tests/progseq/golden/*`
* **Change:** Snapshot these configurations, chosen to cover every code path
  the later refactors touch:

  | Config | Target | Options |
  | --- | --- | --- |
  | `sv-named` | op-model-sv | `--sv-reg-fields named` |
  | `sv-folded` | op-model-sv | `--sv-reg-fields folded` |
  | `c-vtable` | op-model-c | defaults |
  | `c-mmio-hdr` | op-model-c | `--link-style mmio` (header-only) |
  | `c-fn-static` | op-model-c | `--mem-access functions --lifecycle static --link-style direct` |
  | `c-accessors` | op-model-c | `--reg-style accessors --emit-stubs` |
  | `cpp-virtual` | op-model-cpp | defaults |
  | `py-default` | op-model-py | defaults (added by P8.T1) |

* **Tests:** `tests/progseq/test_golden_op_models.py::test_golden[<config>]`
* **Accept:** all seven pass on a clean tree; the golden tree is committed and
  its size is reasonable (< ~1 MB).
* **Note:** `c-accessors` and `c-fn-static` are included specifically because
  they are the least-exercised C paths and the ones a bus-funnel refactor is
  most likely to break silently.
* **Landed:** all seven frozen; golden tree is 644 KB; 9 tests
  (7 configs + `test_every_config_is_snapshotted` +
  `test_no_orphaned_snapshots`, which keep the set and the tree from drifting
  apart in either direction). Full suite 1522 → 1542 passing, nothing changed.
* **Deviation, since RESOLVED — `cpp-virtual` was snapshotted against the small
  model.** The C++ backend could not generate the real operation model at all,
  so that one config ran on `examples/export/programming_seqs` while the other
  six ran on WB DMA. Filed as design I16, fixed 2026-08-14 (see below); all
  seven configs now run on the real model, and the golden tree is 712 KB.

---

**P0.T3 — Regeneration workflow and CI**

* **Depends:** P0.T2
* **Files:** `scripts/regen_golden.py` (new), CI config
* **Change:** `python scripts/regen_golden.py [config...]` rewrites snapshots.
  It refuses to run unless `PSSC_GOLDEN_REGEN=1` is set, so it cannot be run
  reflexively to make a failing test pass. Its output prints a summary diff
  stat per config so the regeneration itself is reviewable.
* **Docs:** a short section in `docs/contributing.md` (or the AGENTS.md build
  section) on when regenerating is legitimate: *only* alongside an intentional
  output change, never to silence a refactor.
* **Accept:** CI runs the golden set; regeneration without the env var exits
  non-zero with an explanatory message.
* **Landed:** `scripts/regen_golden.py`, plus a "Golden snapshots" section in
  `AGENTS.md` (there is no `docs/contributing.md`) stating the regeneration
  policy and that snapshots prove sameness, not correctness. The guard is
  covered by `test_regen_script_refuses_without_the_env_var`.
* **Deviation — the CI half of this task is not done, and cannot be done as
  stated.** `.github/workflows/ci.yml` has no test job at all; it builds and
  publishes a wheel. Adding one means standing up dependency fetching (`ivpm
  update`) in CI, which is its own piece of work and well outside this plan.
  What holds instead: the golden tests are in the **default** `pytest.ini`
  testpaths, so any run of the suite runs them. **Nothing runs the suite
  automatically.** Filed as R10 in §6 — until a test job exists, every gate in
  this plan depends on a human running pytest.

> **Gate G0:** every op-model output is under snapshot, and there is a
> reviewable, deliberately-awkward way to change one.

---

### Phase 1 — Correctness fixes that stand alone

These are defects today. None depends on the extension work; all of them get
cheaper to verify now that G0 holds.

---

**P1.T1 — Run `validate_calls` for C and C++ (I1)**

* **Depends:** P1.T2 (**order matters** — see Accept)
* **Files:** `src/pssc/targets/c/c_progseq_gen.py`,
  `src/pssc/targets/cpp/cpp_progseq_gen.py`
* **Change:** Call `validate_calls(root, ctx, <target>)` before any file is
  opened, raising `CompileError` on findings — matching
  `progseq_gen.py:64-69`. (Moves into `OpModelTarget.check()` in P2; done here
  because it is a live gap.)
* **Tests:** `tests/progseq/test_call_legality.py::test_c_target_gates_unknown_call`,
  `::test_cpp_target_gates_unknown_call` — a model with an undeclared call must
  fail compilation rather than emit a call to a nonexistent symbol.
* **Accept:** the C/C++ golden configs still pass unchanged (proving the real
  model has no illegal calls), and the new negative tests fail without the fix.
* **Landed:** the six lines were becoming three copies, so they are one
  function -- `validate_calls.gate(root, ctx, target, language)` -- called
  first by all three backends. Three copies is how the gate came to run for SV
  only in the first place.
  * All seven golden configs unchanged, which is the proof the real model has
    no illegal calls under the new gate.
  * **Five existing tests changed, and the change is user-visible.** Refusals
    that used to come from the body emitter (`ValueError`, naming the receiver
    expression `wake.get()`, aborting at the first offender, after the header
    had been written) now come from the gate: a `CompileError` carrying every
    offending call, raised before any file is opened. The tests were rewritten
    to assert the new diagnostic rather than relaxed -- `conftest.py` grew
    `diagnostic_text(exc)`, which joins the message with `.errors` the way the
    CLI prints them, so an assertion still covers the half that says what is
    wrong. Two of them now also assert that no artifact was written.
  * Net loss: the gate names `get` and `chan_c::take` where the emitter named
    `wake.get()`. Recorded in the test that gave it up.

---

**P1.T2 — Re-key `call_legality.EXTENSIONS` (I2)**

* **Depends:** —
* **Files:** `src/pssc/targets/call_legality.py`
* **Change:** Key `EXTENSIONS` by canonical target names (`op-model-c`,
  `op-model-cpp`), and have `entries_for()` resolve aliases through the target
  registry so `c-progseq` keeps working. `PROGSEQ_TARGETS` follows.
* **Tests:** `tests/progseq/test_call_legality.py::test_extensions_keyed_by_canonical_name`
  — asserts every `EXTENSIONS` key is a registered canonical target name;
  `::test_entries_for_resolves_alias` — `entries_for("c-progseq") ==
  entries_for("op-model-c")`.
* **Accept:** `entries_for("op-model-c")` returns a non-empty Tier-2 set. Both
  new tests fail on the pre-fix code.
* **Landed:** `EXTENSIONS` re-keyed to `op-model-c` / `op-model-cpp`; alias
  resolution is `_canonical()`, which asks the **target registry** rather than
  keeping a second alias table in this module -- one place decides what a name
  means. An unregistered name falls through unchanged (Tier 0 + COMMON only)
  rather than raising: a bad target name is diagnosed where targets are
  resolved, with the list of available ones, not here.
  Four tests, not two: the two the plan named, plus one asserting the C target
  actually has Tier 2 entries under its canonical name, plus the fall-through.
* **Why before P1.T1:** turning on the gate first would make every legal
  `print`/`try_get` in the model an error, pointing users at their model for a
  compiler bug.

---

**P1.T3 — Per-run constructor names (I5)**

* **Depends:** —
* **Files:** `src/pssc/targets/progseq_model.py`,
  `src/pssc/targets/progseq_tgt.py`, `c_progseq_tgt.py`, `cpp_progseq_tgt.py`
* **Change:** `func_kind(fn, ctor_names=DEFAULT_CTOR_NAMES)`; the global
  `_CTOR_NAMES` and `set_ctor_name()` are deprecated (kept as thin shims for
  one release, emitting a `DeprecationWarning`) and the value is threaded from
  the target instead.
* **Tests:** `tests/unit/test_ctor_name.py::test_two_compiles_do_not_interfere`
  — compile the model twice in one process with different `--ctor-name` values
  and assert both results are correct. Fails today.
* **Accept:** no module-level mutable ctor-name state on the non-deprecated
  path; existing `test_ctor_name.py` still passes.
* **Landed, but NOT as specified.** `func_kind` did grow an optional
  `ctor_names` parameter, and `set_ctor_name` is a deprecated shim that warns.
  The value is **not** threaded from the target to the ~20 `func_kind` call
  sites: it is a `ContextVar` entered and restored by `ctor_names_scope(name)`,
  which each target wraps its whole `run()` in.
  * **Why:** threading the value reaches into all three body emitters and
    `lower_api_types`, and `OpModel` (P2.T1) already carries `ctor_names` as a
    field — doing it here means doing it twice, the second time as churn in the
    files P2 is trying to move byte-identically.
  * **What this does fix:** the actual defect, which is *leakage between
    compiles*. `--ctor-name build` no longer survives its own compile. That is
    silent when it happens, and produces a class that builds its register model
    from an undeclared variable.
  * **What it does not fix:** two compiles in different THREADS at the same
    time still share nothing safely. Nothing does this today; asyncio and
    sequential reuse — how compiles actually share a process — are both correct
    with a ContextVar. **P2.T1 carried `OpModel.ctor_names`, but the body
    emitters still read the ambient value** — finishing that is P6a.T5, which
    is the phase that already reopens those files.
* **Tests:** the named test plus three more —
  `test_the_override_does_not_outlive_its_scope`,
  `test_the_deprecated_setter_still_works_and_warns`, and an autouse fixture
  that fails any test which leaves the set narrowed (a leak otherwise breaks a
  later test in a different file, which is a miserable thing to debug).

---

**P1.T4 — `comments.HASH` (O8)**

* **Depends:** —
* **Files:** `src/pssc/targets/comments.py`
* **Change:** A third style rendering `# ` line comments, with `doc_block`
  producing a `#`-prefixed block. No close-marker escape hatch is needed (there
  is no block form), which is worth a comment in the code.
* **Tests:** `tests/progseq/test_comment_propagation.py::test_hash_style_*` —
  single line, multi-line, blank-line preservation, trailing attachment.
* **Accept:** covered at parity with LINE/BLOCK.
* **Landed:** the three styles now share one `_MARKER` table rather than a
  chain of `if style ==`, so a fourth line style is a dict entry. `doc_block`
  in HASH renders exactly what `comment_lines` does — `#` has no documentation
  form distinct from its ordinary one, and inventing one would emit something
  no `#` language reads. Eight tests, the last of which pins LINE and BLOCK
  behaviour directly (the golden snapshots cover them through the generated
  output; this covers the primitive).

> **Gate G1:** the three op-model targets all gate illegal calls; legality
> lookups resolve for every target name; no global compile state; `#` comments
> available for Phase 8.
>
> **G1 holds, with one qualification.** "No global compile state" is true for
> sequential and asyncio reuse and false for two compiles in different threads
> — see P1.T3. P6a.T5 closes it. **Closed (2026-08-14), and the qualification
> was mis-stated:** threads were never the exposure (a ContextVar is
> per-thread). The exposure was a caller elaborating a model directly, whose
> generated output depended on what some other compile had set. Every emitter
> now takes `ctor_names` explicitly — see the G6a note.
> Evidence: full suite 1522 → 1560 passing, 0 failures; all seven golden
> configs byte-identical throughout Phase 1, which is what says these were
> correctness fixes and not behaviour changes.

---

### Phase 1b — Bringing `op-model-cpp` up to the real model (unplanned)

Not in the original breakdown. It was added because Phase 0 discovered that the
C++ backend could not generate the real operation model at all (design I16),
which makes it a poor thing to port in P2.T5 and a poor proving ground for any
later tier — a port is only as convincing as the model it is proven over.

Tracked as one task because it was one piece of work, and because splitting it
after the fact would invent a sequence that was not followed.

---

**P1b.T1 — Rebuild the C++ operation-model backend**

* **Depends:** P0 (the golden harness is what made the change reviewable)
* **Files:** `src/pssc/targets/cpp/lower_progseq.py` (rewritten),
  `cpp/lower_api_types.py` (new), `cpp/lower_reg_model.py`,
  `cpp/cpp_progseq_gen.py`, `cpp_progseq_tgt.py`, `call_legality.py`,
  `share/cpp/pssc_chan.hpp` (new), `share/cpp/pssc_env.hpp` (new),
  `share/cpp/pssc_reg.hpp`, `c/lower_progseq.py` (`Prefixes` gained a
  `language` label; it is now shared)
* **Change:** The enum was the first symptom, not the gap. The backend
  projected the ROOT component only and about half the procedural subset. What
  it grew: the component tree (interfaces, classes, sub-component members and
  accessors), enums and plain structs, `foreach`/`match`/`break`/`continue`/
  `yield`/`+=`, casts and unary operators, the memory primitives, register-group
  binding and offset folding, model-operation and import calls, a typed depth-1
  channel runtime, comment propagation, and per-model core-header copying.
* **Three decisions worth carrying forward:**
  * **Two-phase construction** (`explicit C(seam&)` then `initialize(...)`).
    Forced, not chosen: a parent computes its children's base addresses in its
    own constructor body, which runs after the children — as members — already
    exist. `pssc::reg` now holds a bus *pointer* so a group can be rebound.
  * **Enumerators do not survive into the IR.** `return PENDING` arrives as
    `ExprConstant(2)`; C converts int→enum implicitly so the C backend never had
    to notice, and C++ does not, which is how this was found. The value is
    looked back up in the enum's own table so the NAME is emitted -- at every
    typed context, comparisons included, so the output says
    `status != WB_DMA_PENDING` and not `status != 2`.
  * **A register access needs no translation** — the C++ register model mirrors
    the PSS structure, so the emitter's job is to refuse a method `pssc::reg`
    does not have, not to spell one. This is why the register model needed no
    change: it was already the hard part, and it was already right.
* **Tests:** `tests/progseq/test_cpp_op_model.py` (30, structural + a `-Werror`
  compile of the real model), `test_op_model_behaviour_cpp.py` (5),
  `data/cpp/op_model_tb.cpp` (new), plus rewrites of the C++ assertions in
  `test_cpp_target.py`, `test_c_reg_masked.py` and `test_op_model_channel.py`.
* **Accept:** the C++ backend generates the real model; the `cpp-virtual`
  golden runs on it; and the generated driver passes the SAME trace-asserted
  behavioural gate as the C driver, against the same mock, with both mutation
  checks. **Met.** Suite 1560 → 1600.
* **Why the behavioural gate matters more than the compile:** the two backends
  project one model into two languages, so if both are right they issue the
  same register accesses. Running the C gate's five cases against the C++
  driver makes any divergence a test failure rather than a discovery.
* **One capability now differs between backends** — a parent reaching a child's
  channel works in C++ and not in C (design I17, risk R12). Recorded rather
  than fixed: the C fix is small, but it is C-backend work and its output is
  under snapshot.

> **Gate G1b:** `op-model-cpp` generates the real operation model, compiles it
> under `-Wall -Wextra -Werror`, and passes the C gate's behavioural cases.
> P2.T5 is now proven over the same model as P2.T3 and P2.T4.

---

### Phase 2 — The shared op-model layer

---

**P2.T1 — `OpModel` + `elaborate()`**

* **Depends:** G0, G1
* **Files:** `src/pssc/targets/op_model.py` (new)
* **Change:** The frozen dataclass from design §2.3 plus the elaboration that
  builds it: resolver, `walk_tree`, post-order regular components, reg-group
  and value-struct collection, import map, `ctor_names`, `out_dir`. Convenience
  accessors (`operations`, `ctor`, `channels`, `sub_components`, `offset_of`,
  `base_stride_of`) delegate to `progseq_model` — no logic is reimplemented,
  only gathered.
* **Tests:** `tests/progseq/test_op_model_layer.py` — component order is
  children-before-parents; reg groups post-order; value structs de-duplicated
  and first-use ordered; `offset_of` raises `OffsetFoldError` with the group and
  instance named. Compare against what the current SV path computes, so the new
  layer is proven equivalent before anything consumes it.
* **Accept:** `OpModel` built from the WB DMA model matches the existing
  per-backend computations element-for-element.
* **Landed** (`src/pssc/targets/op_model.py`, `test_op_model_layer.py`).
* **It found the thing it was written to find.** The SV and C walks disagreed
  on the order of SIBLING components: SV emitted them in declaration order, C
  in reverse — its "post-order" was `reversed(pre_order)`, which is only the
  same thing when no component has two children. Both outputs were correct (a
  child preceded its parent either way), so nothing failed, and no model with
  two sibling component types was under test. `OpModel.components` is a true
  post-order and is now the single answer.
  * **This changes C's output for such a model.** Nothing under snapshot has
    one; the divergence survived precisely because of that. A model with
    siblings is now in the suite.
* **Two orders, not one.** `components` (children first) and
  `components_root_first` are both needed and neither derives from the other by
  reversal — symbol-prefix assignment requires index 0 to be the root.

---

**P2.T2 — `OpModelTarget`**

* **Depends:** P2.T1
* **Files:** `src/pssc/targets/op_model.py`
* **Change:** Base class with `add_args` (shared `--root`, `--ctor-name`,
  `--no-core-copy`), `run` = `elaborate` → `check` → `emit`, `check` running
  `validate_calls` + the empty-API assertion (hoisted from
  `progseq_gen._assert_api_is_not_empty`), `legality_target`, and a default
  `core_files()`-driven copy step.
* **Tests:** `tests/unit/test_op_model_target.py` — `emit` is abstract; `check`
  raises `CompileError` listing *every* offending call, not the first; the
  empty-API assertion fires for a component with no operations.
* **Accept:** a minimal subclass in the test emits a file with ~15 lines of
  subclass code.
* **Landed.** `run` is `elaborate -> check -> emit`; `check` runs the legality
  gate and the empty-API assertion. A backend can no longer forget either,
  which is the fix for the shape of both P1.T1 and the empty-API defect: they
  were each written for one backend and never propagated.
  The test's `_Recording` backend is 11 lines.

---

**P2.T3 / P2.T4 / P2.T5 — Port SV, C, C++**

* **Depends:** P2.T2
* **Files:** `progseq_tgt.py` + `progseq_gen.py`; `c_progseq_tgt.py` +
  `c/c_progseq_gen.py`; `cpp_progseq_tgt.py` + `cpp/cpp_progseq_gen.py`
* **Change:** Each target subclasses `OpModelTarget` and reduces to option
  handling + `emit`. The per-backend `_resolver`, `_count`,
  `_regular_components`, `_reg_value_structs*` are deleted in favour of the
  `OpModel`.
* **Tests:** the Phase-0 golden configs, unchanged. Plus
  `tests/progseq/test_op_model_layer.py::test_all_targets_share_one_walk` —
  assert each target's component list is the `OpModel`'s (guards against a port
  that keeps a private copy).
* **Accept:** **all seven golden configs byte-identical**, no snapshot
  regeneration. Any diff is a bug in the port, not a reason to regenerate.
* **Landed, and met: no golden file was regenerated.** SV and C were ported
  first (8 of 9 checks green immediately), then C++.
  * `Prefixes`, `regular_nodes` and `post_order` in `c/lower_progseq.py` now
    take the model and are views onto it; `lower_handles`, `lower_decls` and
    `lower_impl` take it too. They kept their names because the call sites read
    better for them — what they must not be is a second walk, and
    `test_all_targets_share_one_walk` is what fails if one grows back.
  * Six tests referenced helpers this deleted. Each was rewritten to assert the
    end state rather than relaxed: the equivalence assertions that justified
    the layer became identity assertions, and the one pinning the C/SV
    sibling-order DISAGREEMENT now pins their agreement.

---

**P2.T6 — Remove the duplicates**

* **Depends:** P2.T3–T5
* **Files:** as above
* **Change:** Delete the now-unreferenced helpers; leave no re-export shims for
  private names.
* **Tests:** `tests/unit/test_package_layout.py::test_no_duplicate_op_model_helpers`
  — greps the three backend modules for the deleted helper names.
* **Accept:** design §1.2's table is empty except the body-emitter row (Phase 7).
* **Landed.** `_resolver`, `_count`, `_regular_components`, `_value_structs`,
  `_reg_value_structs*` and `node_key` are gone from all three backends. Two
  guard tests in `test_package_layout.py`: one greps for the deleted names, one
  asserts no backend calls `walk_tree` itself. Greps rather than import checks,
  because the failure mode is a private copy being re-added, and a copy defined
  and used locally imports nothing.

> **Gate G2:** one walk, one classification, one offset fold, one legality gate,
> shared by three backends, with byte-identical output.
>
> **G2 holds.** Suite 1600 → 1635; all nine golden checks pass with no
> regeneration.
>
> **What G2 does NOT close, contrary to what P1.T3 anticipated.**
> `OpModel.ctor_names` exists and is authoritative for everything the
> `OpModel` layer decides — but the three body emitters still call
> `func_kind(fn)` without it, taking the ambient `ContextVar` that
> `OpModelTarget.run` enters. So two compiles in different THREADS still share
> that state. **[Closed by P6a.T5, 2026-08-14. The consequence
> stated here — threads — was wrong; the real one was a caller who elaborates a
> model directly getting output that depends on the process. See the G6a
> note.]** Threading the value through ~10 call sites across three emitters
> is a wide diff in exactly the files Phases 6a and 7 restructure, and doing it
> twice is churn in the files whose output is hardest to hold still.
> **Tracked as P6a.T5** — do it when those files are already open. Nothing
> today runs two compiles in one process on two threads.

---

### Phase 3 — Discovery and registration

---

**P3.T1 — `targets.discover()`**

* **Depends:** G2
* **Files:** `src/pssc/targets/__init__.py`
* **Change:** Entry-point group `pssc.targets`; accepts a `Target` subclass,
  instance, or zero-arg callable returning either or an iterable; idempotent;
  honours `PSSC_NO_PLUGINS`; failures collected in `_PLUGIN_ERRORS` rather than
  raised.
* **Tests:** `tests/unit/test_target_discovery.py` — each accepted shape loads;
  an iterable registers all or none; a raising entry point does not prevent the
  others; `PSSC_NO_PLUGINS=1` loads none; discovery is idempotent.
  Entry points are injected with a fake `entry_points` rather than by installing
  a package, so the unit suite needs no install step.
* **Accept:** `test_discover_is_noop_when_off` in the existing
  `test_target_registry.py` is updated (not deleted) to reflect the new
  behaviour.
* **Landed:** `discover(force=False)` in `targets/__init__.py`, with
  `_entry_points()` as the injection seam, `PluginError` /
  `plugin_errors()` / `plugin_error_report()`, and `is_builtin()`. All four
  shapes accepted, including a callable returning an iterable (a plugin
  shipping a family). Registration is snapshot/restore per entry point, so a
  family that collides on its third target leaves none of the first two
  registered — half a family with no indication which half is missing is worse
  than none of it. `discover` catches `BaseException`: a plugin's import side
  effects are arbitrary code, and whatever it raises the answer is the same.
  `test_discover_is_noop_when_off` became
  `test_discover_adds_nothing_without_plugins` (this checkout installs no
  plugin, so the observable result is unchanged and must stay so).
  `src/pssc/__main__.py` added so `python -m pssc` works — needed by P3.T6's
  subprocess tests, and worth having regardless.

---

**P3.T2 — Registration policy**

* **Depends:** P3.T1
* **Files:** `src/pssc/targets/__init__.py`, `base.py`
* **Change:** `register()` raises on a name already registered unless
  `replaces=` names it explicitly. `Target.PSSC_TARGET_API = 1`; discovery
  refuses a target declaring an incompatible major, with a message naming the
  plugin, its version, and both API numbers.
* **Tests:** `tests/unit/test_target_discovery.py::test_collision_is_error`,
  `::test_replaces_allows_override`, `::test_api_version_mismatch_refused`.
* **Accept:** a plugin cannot shadow `op-model-c` by accident.
* **Landed:** `TargetError`; `register(target, aliases=, replaces=)` checks
  every name it would claim, **aliases included** — an alias collision is as
  damaging as a canonical one, since `-t c-progseq` silently resolving to
  someone else's backend is exactly the substitution this guards against.
  `Target.PSSC_TARGET_API = 1`, plus `aliases` / `replaces` as class
  attributes so a plugin declares both without importing the registry. The
  refusal message names the plugin distribution, the class, and both API
  numbers. `replaces` licenses only the names it lists: naming the *wrong*
  target is still a collision (`test_replaces_does_not_license_an_unnamed_collision`).
  `_BUILTINS` is derived from what actually registered rather than hand-listed.

---

**P3.T3 — Plugin-error reporting**

* **Depends:** P3.T2
* **Files:** `src/pssc/cli.py`, `src/pssc/driver.py`
* **Change:** `pssc targets` prints a trailing block for failed plugins;
  `compile` with an unknown target appends the same. Both include the exception
  type and message, not a traceback.
* **Tests:** `tests/unit/test_cli.py::test_targets_reports_failed_plugin`,
  `::test_unknown_target_mentions_failed_plugin`.
* **Accept:** the message answers "why is my target missing" without a
  debugger.
* **Landed:** `plugin_error_report()` returns the block as lines; `pssc
  targets` prints it to **stderr** after the listing, so the listing stays
  correct and pipeable and the exit code stays 0 — a bad plugin is not a
  failed command. The block names `PSSC_NO_PLUGINS`, which is how a user
  establishes whether the fault is pssc's or the plugin's. `targets.get()`
  appends the same information to its `KeyError`, on one line because
  `KeyError` renders through `repr()`. **Also fixed here:** `driver.compile`
  raised `CompileError(str(e))` for an unknown target, and `str()` of a
  `KeyError` wraps the whole sentence in quotes and escapes anything inside
  it; it now uses `e.args[0]`.

---

**P3.T4 — `register_extension()`**

* **Depends:** P1.T2
* **Files:** `src/pssc/targets/call_legality.py`
* **Change:** `register_extension(target, entries, inherit=None)`. Rejects an
  entry that marks a Tier-1 (`COMMON`) name `unsupported`, with the reason.
  `EXTENSIONS` becomes private (`_EXTENSIONS`); the built-ins register through
  the new call.
* **Tests:** `tests/progseq/test_call_legality.py::test_extension_cannot_shrink_common`,
  `::test_inherit_chains`, and the existing Tier-1 contract test extended over
  the registry rather than the literal dict.
* **Accept:** the Tier-1 contract is enforced for plugins, not just built-ins.
* **Landed:** `register_extension(target, entries, inherit=None,
  replace=False)`, `extensions_for()`, `registered_targets()`, `LegalityError`.
  `EXTENSIONS` → `_EXTENSIONS`; all three built-ins register through the public
  call. `inherit` is a **snapshot, not a live link** — the reason `op-model-cpp`
  was spelled out rather than `dict()`-copied from C survives into the API: a
  call the base learns to render tomorrow must not silently become legal for a
  derived target whose emitter has no case for it. `replace=True` is required
  to re-register, so an accidental second registration is an error rather than
  a silent overwrite. The Tier-1 contract test now iterates
  `registered_targets()`, so it covers a plugin's Tier 2 as soon as the plugin
  is loaded — which is how the fixture plugin is held to it in P3.T6.

---

**P3.T5 — Option namespacing and `-X`**

* **Depends:** P3.T2
* **Files:** `src/pssc/cli.py`, `src/pssc/targets/base.py`
* **Change:** `_DedupArgGroup` learns whether the contributing target is a
  built-in or a plugin: built-ins keep first-wins dedup, a plugin whose option
  collides raises with both target names and the option. Plugin options must
  begin `--<target-name>-`; a violation is an error at parser build.
  Add `-X/--target-opt NAME=VALUE` (repeatable) → `opts.target_opts` dict, and
  `Target.opt(opts, name, default=None, choices=None)` with validation.
* **Tests:** `tests/unit/test_cli.py::test_plugin_option_collision_raises`,
  `::test_plugin_option_must_be_namespaced`,
  `::test_target_opt_roundtrip`, `::test_target_opt_choices_validated`,
  `::test_builtin_dedup_still_first_wins`.
* **Accept:** the silent-drop path (`cli.py:39`) is unreachable for plugins.
* **Landed:** `_DedupArgGroup.for_target(name, is_plugin)` attributes each
  `add_argument` to its contributor and tracks the owner of every option
  string. Built-ins keep first-wins (they are one codebase; `sv-pure` extends
  `sv-native` and both declare `--no-rt-pkg`). A plugin gets namespace
  enforcement first and collision detection second, and `OptionPolicyError`
  names both targets and the option.
  `Target.parse_target_opts` / `opt` / `opt_bool` implement `-X NAME=VALUE`
  (bare `NAME` means `NAME=true`; later wins so a wrapper can prepend
  defaults). `choices` is validated in `opt` rather than per target, because
  the alternative is what argparse already taught us to avoid.
* **Note on scope:** the namespace rule makes plugin-vs-plugin collision
  unreachable (two plugins cannot share a target name — registration refuses
  it), so the collision branch guards only a plugin colliding with a built-in
  that already claimed its namespaced option. Contrived, kept, and the test
  says so rather than pretending otherwise.

---

**P3.T6 — Fixture plugin**

* **Depends:** P3.T1–T5
* **Files:** `tests/plugins/pssc_fixture_plugin/` (new, installable)
* **Change:** A minimal real plugin: one trivial target, one deliberately
  broken target (raises on import) behind a second entry point, used by the
  integration tests. Installed by a `plugin`-marked fixture via
  `pip install -e` into the test environment, or skipped if unavailable.
* **Tests:** `tests/unit/test_plugin_integration.py` (marker: `plugin`) — real
  entry-point discovery end-to-end, including the failure path.
* **Accept:** discovery is proven against real installed metadata, not only
  against a fake.
* **Landed:** `tests/plugins/pssc_fixture_plugin/` with **three** entry
  points rather than two — `fixture` (loads, generates, declares its own Tier 2,
  reads both a namespaced `--fixture-note` and an un-namespaced
  `-X fixture-style`), `broken` (raises `ImportError` on import), and `shadow`
  (tries to take `op-model-c` without `replaces`). The third was added because
  the silent-shadow case is the one plugin failure with no symptom, and proving
  it is refused against real metadata is worth an entry point.
  `tests/unit/test_plugin_integration.py` (marker `plugin`, 8 tests) installs
  into a session-scoped temp prefix — never the developer's environment —
  covering in-process discovery and a subprocess `python -m pssc targets`.
* **Landed — the venv has no pip.** `pip install --target` is tried first;
  the fallback builds a wheel with `python -m build --no-isolation` and
  extracts it, which for a pure-Python wheel is what installing *is*,
  `.dist-info/entry_points.txt` included. Both are skipped cleanly if neither
  works. Without this the file would have skipped forever in this checkout,
  which is the failure mode the marker was supposed to prevent, not cause.
* **Landed — a real leak, found by the suite.** The plugin first registered
  its Tier 2 at module scope. The `discovered` fixture restored the target
  registry but not `call_legality._EXTENSIONS`, so `fixture` leaked into the
  tier-contract tests and they failed depending on file order. Two fixes, both
  kept: the fixture restores `_EXTENSIONS` too, **and** the plugin moved
  registration into `FixtureTarget.__init__` — import happens once per process
  but construction happens once per registration, so a re-discovery that
  re-registers the target also re-registers what it can render. Module-scope
  registration silently does not survive a second `discover()`.

> **Gate G3:** a third-party target can be installed, discovered, listed,
> selected, given options, and can declare its call legality — and cannot
> silently collide with or shadow a built-in.

> **G3 — met (2026-08-14).** 1685 passing (from 1635), 6 skipped, 3 xfailed;
> all nine golden checks green with no snapshot regenerated — Phase 3 is purely
> additive to the generators and the byte-exact output did not move.
>
> **What G3 does not close.** Discovery has no *sandbox*: a plugin's import
> still runs arbitrary code in the pssc process, and `PSSC_NO_PLUGINS` is a
> switch, not a boundary. That is Phase 4's subject, and nothing here should be
> read as making an untrusted plugin safe to install. Also unaddressed: a
> plugin's generated output is not covered by any golden or conformance
> check — `pssc.testing` (P4.T1/T2) is what gives a plugin author that.

---

### Phase 4 — Making plugins safe

---

**P4.T1 — `pssc.testing`**

* **Depends:** G3
* **Files:** `src/pssc/testing/__init__.py` (new)
* **Change:** Public helpers: `op_model_sources()`, `compile_op_model(target,
  **opts)`, `assert_common_tier(target)`, `assert_deterministic(target,
  **opts)`, `golden_dir_compare(actual, expected)`. The WB DMA model copy moves
  under package data so it ships (or is exposed by path with a clear error when
  absent from a wheel — decide and document which).
* **Tests:** `tests/unit/test_testing_kit.py` — each helper works against a
  built-in target; `assert_deterministic` fails for a deliberately
  nondeterministic stub target.
* **Docs:** PD.T6.
* **Accept:** a plugin author can write their first three tests without reading
  pssc's own test tree.
* **Open question for review:** shipping the WB DMA model as package data adds
  ~200 KB to the wheel. Alternative is a `pssc.testing` extra. Flagging rather
  than deciding.
* **Landed — the open question is decided: the WB DMA model is NOT shipped.**
  `pssc.testing` bundles the ~12 KB model instead (`pssc/testing/models/`,
  registers, a nested register array with an affine offset fold, packed value
  structs, read-modify-write, a do-while poll, an `addr_handle_t` constructor).
  Three reasons, in order: the WB DMA copy is a *vendored* artifact kept in
  sync with upstream by a script, and shipping it makes that copy part of the
  public surface; 176 KB of one specific device in every wheel buys a plugin
  author nothing the small model does not; and a plugin needing a component
  tree or a channel is better served by `compile_op_model(sources=...)` with
  its own model than by pssc guessing which device model resembles theirs.
  **What the bundled model does NOT cover is stated in the module docstring**
  rather than left for someone to discover: no component tree, no channel, no
  enum, no declared import.
* **Landed:** `model_dir`, `op_model_sources`, `op_model_root`,
  `compile_op_model` (returns a `CompileOutcome` that is a context manager and
  cleans up its temp dir, including on failure), `assert_common_tier`,
  `assert_deterministic`, `golden_dir_compare` (returns differences rather than
  raising, so a caller can report on several targets), `assert_dirs_match`.
  `assert_deterministic` compares the path list's ORDER as well as file
  contents -- that list is a compilation order, and a set comparison would miss
  a target that shuffles it.
* **Landed — one guarded copy.** The bundled model is a byte copy of
  `examples/export/programming_seqs/`, because setuptools cannot include files
  from outside the package directory and rewriting the 18 existing references
  to the example tree would be churn with real risk and no gain.
  `test_the_bundled_model_matches_the_example_it_came_from` is the drift guard.

---

**P4.T2 — Conformance suite**

* **Depends:** P4.T1
* **Files:** `src/pssc/testing/conformance.py`
* **Change:** `run(target, **opts) -> ConformanceReport` asserting: every model
  operation appears in the output; every register accessor's address matches
  the offset fold; no emitted symbol is undeclared (per `call_legality`);
  regeneration is byte-stable; the returned path list is in a valid compilation
  order.
* **Tests:** `tests/progseq/test_conformance.py` — passes for all three
  built-ins; fails for a stub target that drops an operation and for one that
  emits a wrong address.
* **Accept:** the built-ins pass their own conformance suite. (If one does not,
  that is a finding, not a reason to weaken the suite.)
* **Landed:** `pssc/testing/conformance.py` -- `Check`, `ConformanceReport`,
  `run()`, `assert_conforms()`. Five checks, each with a stub target in
  `tests/progseq/test_conformance.py` built to break exactly it, plus a
  conforming baseline stub as the control (without it, a stub failing proves
  only that the stub is broken *somewhere*).
* **Landed — all three built-ins pass, but the first run reported two failures
  and both were defects in the CHECKS.** Recorded because the plan anticipated
  the opposite and the distinction is the whole value of the exercise:
  1. *Operations "missing" from the C output.* The check required the bare
     operation name; C emits `dma_engine_configure_channel`, and `\b` does not
     match before a `_`. Fixed with a lookbehind that rejects an alphanumeric
     but allows the underscore a generated prefix joins at.
  2. *A "wrong address" from the SV and then the C backend.* The check knew
     only C-style `0x1c`, so SV's `64'h1c` read as absent; and it expected the
     offset WITHIN a group, while the C backend legitimately folds the whole
     path into one flat accessor (`base + 0x3c` = 0x20 bank + 0x1c). **Both
     spellings are correct**, so `_group_bases` now computes every base a
     nested group can sit at and the check accepts any of them.

  The second one is the important one: a check that reported a conforming
  backend as emitting a wrong address is the worst outcome available to a
  check whose entire purpose is to be believed. Neither check was loosened to
  make a backend pass -- each was made to ask the right question.

---

**P4.T3 — Memento covers plugin version (I4)**

* **Depends:** G3
* **Files:** `src/pssc/dvflow/common.py`
* **Change:** `compute_memento` includes the target name and the version of the
  distribution providing it (via `importlib.metadata`), so a plugin upgrade
  invalidates cached builds. Unknown provenance contributes a sentinel that
  forces a rebuild rather than an empty string that silently does not.
* **Tests:** `tests/dvflow/test_common.py::test_memento_changes_with_target_version`,
  `::test_unknown_provenance_forces_rebuild`.
* **Accept:** the same inputs with a different plugin version produce different
  mementos.
* **Landed:** `target_provenance(target)` -- a built-in resolves through
  pssc's own version, a plugin through the version of the distribution
  providing its module, anything else through a per-process sentinel that
  forces the rebuild. The built-in special case is not a shortcut: a source
  checkout has no distribution metadata, so hashing "unknown" there would
  rebuild everything on every dv-flow invocation and the feature would be
  turned off within a day.

---

**P4.T4 — `register_filetype` + loud skip**

* **Depends:** —
* **Files:** `src/pssc/dvflow/common.py`
* **Change:** `register_filetype(ext, filetype, is_incdir=False)`;
  `EXT_FILETYPE` becomes private. Unmapped outputs move from `_log.debug`
  (`common.py:102`) to `_log.warning` naming the file and the extension.
* **Tests:** `tests/dvflow/test_common.py::test_register_filetype`,
  `::test_unmapped_output_warns`.
* **Accept:** a plugin emitting `.pyi` can make it appear in a fileset, and
  one that forgets is told.
* **Landed:** `register_filetype(ext, filetype, is_incdir=False)` (extension
  normalised, leading dot optional, re-mapping allowed and last-wins),
  `filetype_for()`, `registered_filetypes()`; `EXT_FILETYPE` → `_EXT_FILETYPE`.
  The unmapped-output log moved to WARNING and now names the file, the
  extension, and `register_filetype` -- at debug level the file was generated,
  dropped from every fileset, and the build stayed green.

---

**P4.T5 — Resource seam**

* **Depends:** P2.T2
* **Files:** `src/pssc/resources.py` (new), `src/pssc/cli.py`,
  `src/pssc/targets/op_model.py`
* **Change:** `core_dir(package="pssc", lang="c") -> Path`;
  `Target.core_files() -> List[Tuple[Path, str]]` with the base class doing the
  copying and honouring `--no-core-copy`. The existing `sv_core_dir()` /
  `c_core_dir()` / `cpp_core_dir()` become thin wrappers (they are public CLI
  surface via `pssc <lang>-core-path`).
* **Tests:** `tests/unit/test_resources.py` — resolves for `pssc`; a plugin
  package's own `share/` resolves; `core_files()` copies and reports in order.
* **Accept:** golden configs unchanged; a plugin can ship a header.
* **Landed:** `pssc/resources.py` (`core_dir`, `core_file`, `core_files`,
  `ResourceError`) plus `OpModelTarget.core_package` / `core_lang` /
  `core_file_names()` / `install_core()`. All three backends' `copy_core`
  parameter and copy loop are gone; `cli.sv_core_dir` / `c_core_dir` /
  `cpp_core_dir` are thin wrappers, since `pssc <lang>-core-path` is published
  surface. Everything goes through `importlib.resources`: `__file__`
  arithmetic works right up until someone builds a zipped distribution, and
  then fails as a missing file.
* **Landed — `install_core` returns the paths, the CALLER places them.** That
  list is a compilation order and the answer differs by language: the SV core
  package must precede the generated package that imports it, while a C header
  is included by name and is written last. A base class that appended in a
  fixed position would have silently changed the SV order.
* **Accept met:** all nine golden checks byte-identical, no snapshot
  regenerated.

> **Gate G4:** a plugin can be tested, can prove conformance, ships its own
> runtime source, participates correctly in dv-flow caching, and its outputs
> reach filesets.

> **G4 — met (2026-08-14).** 1744 passing (from 1685), 6 skipped, 3 xfailed;
> all nine golden checks byte-identical with no snapshot regenerated, which is
> the accept criterion for P4.T5's move of the core-file copying.
>
> **What G4 does not close.** "Safe" here means *a plugin cannot silently
> produce wrong output* -- it does not mean a plugin is sandboxed. Discovery
> still imports and runs arbitrary code in the pssc process; `PSSC_NO_PLUGINS`
> is a switch, not a boundary. And the conformance suite checks the bundled
> model only: a backend that handles that model correctly and mishandles
> component trees, channels or enums passes. Those are covered for the
> built-ins by pssc's own suite against the real WB DMA model, and a plugin
> that needs them should point `compile_op_model(sources=...)` at a model of
> its own.

---

### Phase 5a — The memory-access funnel

Worth doing with no plugin in sight (design O9). Byte-identical throughout.

> **Named `MemAccess`, not `BusAccess` and not `MMIO`** (decided 2026-08-14).
> Two things ruled `MMIO` out, and both are concrete. It is **wrong for what
> the funnel actually carries**: P5a.T3 routes `_mem_call` through it, which is
> what lowers the `write32` calls in `wb_dma_c::write_descriptor` -- writes of
> a DMA descriptor into *system RAM*, which are not memory-mapped I/O. (The
> model says so itself: "device knowledge written through a memory path, not a
> path to memory".) And **`mmio` is already taken here**, as one of three seam
> spellings (`--link-style mmio`, `pssc_mem_mmio.h`); a class named `MMIO` used
> by the `vtable` and `direct` styles too would collide with a narrower meaning
> in the same codebase.
>
> `mem` is the word this codebase already uses for the ABSTRACTION --
> `pssc::mem_if`, `pssc_mem*.h`, `--mem-access`, `_MEM_PRIMS`,
> `pssc_mem_read`/`_write`. `bus` names the HANDLE you reach it through
> (`pssc_bus(s)`, `bus_`, the `bus` constructor parameter), which is why
> `BusAccess` read slightly off. The funnel names the abstraction, so it takes
> the abstraction's word -- and `--mem-access` then reads as what it now
> literally is: a knob on this object.

---

**P5a.T1 — `MemAccess`**

* **Depends:** G2
* **Files:** `src/pssc/targets/c/mem_access.py` (new)
* **Change:** One object owning every memory-touching spelling: `read(width,
  handle, addr)`, `write(width, handle, addr, value)`, `bus_expr(handle)`,
  `masked_write(...)`. `bus_expr` keeps its name: it renders the HANDLE
  (`pssc_bus(s)`), which is the one thing here that really is the bus. Default implementation emits exactly today's
  `pssc_r<N>(pssc_bus(s), ...)` / `pssc_w<N>(...)`.
* **Tests:** `tests/progseq/test_mem_access.py` — unit-level, each method's
  default rendering pinned as a string.
* **Accept:** the default renderings are pinned so a later style refactor
  cannot drift them unnoticed.

---

**P5a.T2 — Route the accessors**

* **Depends:** P5a.T1
* **Files:** `src/pssc/targets/c/lower_reg_model.py`
* **Change:** `emit_accessor` (`:224`) calls the funnel for all six accessor
  forms. The masked-write composition stays here and stays mandatory: it is
  PSS 3.1 §21.14.1 semantics, and its read is side-effecting on a status CSR.
* **Tests:** golden configs; plus
  `test_mem_access.py::test_masked_write_always_reads` asserting the read
  survives whatever the funnel renders.
* **Accept:** `c-vtable`, `c-mmio-hdr`, `c-fn-static`, `c-accessors` all
  byte-identical.

---

**P5a.T3 — Route the body emitter**

* **Depends:** P5a.T1
* **Files:** `src/pssc/targets/c/lower_progseq.py`
* **Change:** `_mem_call` (`:1051`) and the accessor selection in `_reg_call`
  (`:886`) go through the funnel. After this, `pssc_r`/`pssc_w` appears in
  exactly one module.
* **Tests:** golden configs; `tests/unit/test_package_layout.py::
  test_bus_spelling_has_one_home` — greps for `pssc_r3`/`pssc_w3`/`pssc_bus(`
  outside `mem_access.py`, over code only (comments, docstrings and emitted C
  banners are dropped first: a guardrail that forbids naming the seam in prose
  is one people route around by not writing the prose). Paired with
  `test_the_seam_grep_would_catch_an_inlined_access`, which runs the matcher
  against sample offenders — a grep that matches nothing passes silently.
* **Accept:** byte-identical; the grep test passes.

---

**P5a.T4 — Proof**

* **Depends:** P5a.T2, P5a.T3
* **Change:** No code. Confirm zero golden regeneration across the phase and
  record it in the phase-gate note.
* **Accept:** `git log` for the phase touches no file under
  `tests/progseq/golden/`.

> **Gate G5a:** one funnel, no output change, and the grep test that keeps it
> that way.

> **G5a — met (2026-08-14).** 1805 passing (from 1745), 30 skipped, 3 xfailed;
> all nine golden checks byte-identical, and no file under
> `tests/progseq/golden/` written during the phase.
>
> **P5a.T4's accept criterion had to be restated.** It said "`git log` for the
> phase touches no file under `tests/progseq/golden/`", and the snapshots are
> not committed yet in this checkout -- `git log` on that path is empty for
> *every* phase, so as written the criterion passes without proving anything.
> The proof used instead is direct and available now: the newest mtime under
> `golden/` predates the phase's first edit, and the golden comparisons pass
> against those unmodified files. Worth committing the snapshots so the
> criterion means what it was written to mean.
>
> **Two things went further than the task text.** Both because stopping short
> would have left the funnel not actually a funnel:
>
>   * **Accessor NAMES moved in too** (`MemAccess.accessor_suffix`). The task
>     said "route the accessor selection in `_reg_call`", and the only routable
>     thing there is the name. `lower_reg_model` defined `<base>_write_masked`
>     and `lower_progseq` had a private `_REG_ACCESSOR_NAME` producing the same
>     string -- two tables agreeing by hand, where a mismatch emits a call to a
>     function nothing defines. `_REG_ACCESSORS` keeps the ARITY, which is PSS
>     semantics and not the funnel's business.
>   * **`_bus_macro` renders `mem.bus_macro`** rather than a literal
>     `pssc_bus`. It DEFINES the macro rather than using it, so the grep test
>     would have exempted it either way -- but a funnel whose `bus_expr` and
>     whose definition site can disagree is one edit away from generating a
>     macro nothing calls.
>
> **`_mem_call` now checks arity** (`read32` takes 1, `write32` takes 2) where
> it used to splat whatever it was given. Unreachable for a well-formed model,
> since PSS declares these signatures; it is there because the funnel takes
> named parameters and silently dropping a third argument is worse than saying
> so.
>
> **What G5a does not close.** The funnel is C-only. The SV and C++ backends
> have their own memory spellings (`lower_progseq.py` under `sv/`,
> `cpp_progseq_gen.py`), untouched here and unguarded by the grep test, which
> globs `targets/c/*.py`. Phase 5b's style protocol is specified against the C
> funnel; if it later grows to the other two backends, they need this phase
> first. Also: the funnel renders strings and validates nothing about the
> ADDRESS it is handed -- offset folding stays in `lower_reg_model`, and a
> style cannot reach it.

---

### Phase 5b — Tier A: the style policy

---

**P5b.T1 — `CStylePolicy`, naming half**

* **Depends:** G5a
* **Files:** `src/pssc/targets/style.py` (new, base),
  `src/pssc/targets/c/style.py` (new, C default)
* **Change:** `banner`, `header_name`, `impl_name`, `symbol`, `type_name`,
  `include_order`, `comment_style`, `indent`. The default policy reproduces
  today's output exactly. Every backend site that currently hard-codes one of
  these consults `self.style`.
* **Tests:** golden configs unchanged; `tests/progseq/test_style_policy.py::
  test_default_policy_is_identity`, plus a test policy that changes `symbol`
  and asserts the change appears everywhere a symbol is spelled — *including*
  in prototypes and call sites, which is where a partial wiring shows up.
* **Accept:** byte-identical with the default; a one-method policy visibly and
  consistently changes the output.

---

**P5b.T2 — `pssc.styles` + `--style`**

* **Depends:** P5b.T1, P3.T1
* **Files:** `src/pssc/targets/style.py`, `src/pssc/cli.py`,
  `src/pssc/dvflow/{flow.yaml,build.py}`
* **Change:** Entry-point group `pssc.styles`, keyed `"<target>:<style>"`.
  `--style NAME` on `compile`; `style:` param on the op-model dv-flow tasks.
  An unknown style lists the available ones for that target.
* **Tests:** `tests/unit/test_style_discovery.py`;
  `tests/dvflow/test_build_tasks.py::test_style_param_forwarded`.
* **Accept:** the fixture plugin can ship a style and select it by name.

---

**P5b.T3 — Access hooks**

* **Depends:** P5b.T1, G5a
* **Files:** `src/pssc/targets/c/style.py`, `c/mem_access.py`
* **Change:** `reg_symbol`, `reg_accessor_form` (`inline`|`macro`|`none`),
  `render_reg_read`/`_write`/`_masked_write`, `render_mem_read`/`_write`,
  `seam_headers`. The funnel consults the policy; the default policy returns
  today's spellings.
* **Tests:** `tests/progseq/test_style_policy.py::test_macro_form_emits_no_accessor_block`,
  `::test_custom_macro_reaches_every_access_site` — a policy rendering
  `ACME_WRITE32` must leave no `pssc_w32` anywhere in the output, which is the
  assertion that catches a missed site.
* **Accept:** an ACME-style policy produces output containing zero `pssc_r`/
  `pssc_w` and zero copied seam headers.

---

**P5b.T4 — Enforcement (I12)**

* **Depends:** P5b.T3
* **Files:** `src/pssc/targets/c/mem_access.py`
* **Change:** A policy is never asked to render a write for a `READONLY`
  register or a read for a `WRITEONLY` one; if it returns a rendering where the
  funnel did not ask, that is an error. The masked-write read cannot be dropped:
  `render_reg_masked_write` either is overridden wholesale, or the funnel
  composes read-then-write itself. Addresses are never passed to the policy for
  computation, only for rendering.
* **Tests:** `tests/progseq/test_style_policy.py::test_readonly_register_never_written`,
  `::test_masked_write_read_cannot_be_dropped`,
  `::test_policy_cannot_alter_address`.
* **Accept:** each of the three "may not" rules from design §2.5.1 has a test
  that fails when the rule is removed.

---

**P5b.T5 — Incompatible-combination rejection**

* **Depends:** P5b.T3
* **Files:** `src/pssc/targets/c_progseq_tgt.py`
* **Change:** A bus-overriding policy plus `--link-style vtable` is rejected at
  start-up, naming both and saying why — in the shape of the existing
  `--mem-access` + `vtable` rejection (`c_progseq_gen.py:194`).
* **Tests:** `tests/progseq/test_style_policy.py::test_vtable_plus_bus_policy_rejected`.
* **Accept:** the message names both flags and the mechanism conflict.

> **Gate G5b:** a company can mandate its register macros in ~100 lines, cannot
> break §21.14.1 semantics while doing it, and the default output has not moved.

> **G5b — met (2026-08-14).** 1852 passing (from 1805), 30 skipped, 3 xfailed;
> all nine golden checks byte-identical, no snapshot written during the phase.
> The fixture plugin's `AcmeStyle` is **43 lines** and mandates ACME register
> macros across a backend it does not own -- the gate's claim, measured.
>
> **The macro form emits the ADDRESS accessors, and that was a correction.**
> The first cut of `reg_accessor_form() == "macro"` emitted no accessor block
> at all, which meant the folded offsets vanished from the generated C entirely
> and the house macro would have had to know them. `test_policy_cannot_alter_
> address` caught it. The offsets are the model's statement about the device, so
> `_addr` survives every form and a policy gets both the register's identity
> (`acc.base`) and its address (`<base>_addr(s, i)`) while computing neither.
>
> **`pssc_bus` is suppressed for a bus-overriding style.** Also caught by a
> test -- `test_custom_macro_reaches_every_access_site` found the macro still
> being defined in output that never expands it. A macro nothing uses is one a
> reader has to rule out.
>
> **One signal, not two.** `overrides_bus(link_style)` is DERIVED from
> `seam_headers(link_style) == ()` rather than declared separately. Three
> things read it -- no seam headers copied, no `pssc_bus` emitted, and T5's
> refusal to pair with `--link-style vtable` -- and two ways to say the same
> thing would be two things to keep in agreement.
>
> **Style built-ins register lazily, guarded on content.** `c/style.py` imports
> `StylePolicy` from `targets/style.py`, so registering at import time is a
> cycle. The first fix used a "have I run" flag, which left the registry
> permanently empty for any test that snapshot/restored it; the guard is now
> the registry's own content.
>
> **What G5b does not close.** The policy is **C-only** -- `banner`, `symbol`
> and the access hooks have no SV or C++ implementation, and neither backend
> consults a style at all. `indent()` and `comment_style()` are on the base
> class and are not yet read by any emitter: the C emitters still hard-code
> four spaces and `BLOCK`, so a policy overriding them changes nothing today.
> And a style is not sandboxed any more than a target is: `--style` runs a
> plugin's code in the pssc process.

---

### Phase 6a — The C backend becomes a class

Internal, behaviour-preserving, useful regardless of tier B.

---

**P6a.T1 — `COpModelBackend`**

* **Depends:** G5a
* **Files:** `src/pssc/targets/c/backend.py` (new), `c/c_progseq_gen.py`,
  `c/lower_progseq.py`, `c/lower_reg_model.py`, `c/lower_api_types.py`
* **Change:** The module-level `lower_*` functions and the 200-line
  `generate()` (`c_progseq_gen.py:156`) become methods. **Every method returns
  `str`/`List[str]`; only `generate` and the file emitters write.** `Settings`
  replaces the 18-keyword `generate()` signature.
* **Tests:** golden configs; `tests/progseq/test_c_backend_class.py::
  test_no_emit_method_writes_files` — introspects the class and asserts no
  `emit_*` method touches the filesystem (monkeypatched `Path.write_text`).
* **Accept:** byte-identical; `generate()` is under 40 lines.

---

**P6a.T2 — Section pipeline**

* **Depends:** P6a.T1
* **Files:** `src/pssc/targets/c/backend.py`, `src/pssc/targets/sections.py` (new)
* **Change:** `header_sections()` returns the named, ordered list from design
  §2.6.1. `insert_after`, `insert_before`, `replace`, `remove` raise on an
  unknown section name. The C ordering constraints keep their comments
  (`c_progseq_gen.py:300` is load-bearing) and move onto the section list.
* **Tests:** `tests/progseq/test_sections.py` — unknown name raises; order
  preserved; a replaced section's output appears in place;
  `test_c_backend_class.py::test_section_list_matches_emitted_order`.
* **Accept:** byte-identical; the emitted order is derivable from the section
  list alone.

---

**P6a.T3 — Swappable collaborators**

* **Depends:** P6a.T1
* **Files:** `src/pssc/targets/c/backend.py`
* **Change:** `body_emitter_cls` and `style_cls` as class attributes; the
  backend instantiates through them.
* **Tests:** `tests/progseq/test_c_backend_class.py::test_body_emitter_cls_is_honoured`.
* **Accept:** a subclass swapping `body_emitter_cls` changes body rendering
  without touching anything else.

---

**P6a.T4 — Proof**

* **Depends:** P6a.T1–T3
* **Accept:** zero golden regeneration across the phase.

**P6a.T5 — Thread `ctor_names` into the body emitters**

* **Depends:** P6a.T1
* **Files:** `sv/lower_progseq.py`, `c/lower_progseq.py`,
  `cpp/lower_progseq.py`, `sv/lower_api_types.py`, `validate_calls.py`
* **Change:** Replace the ~10 ambient `func_kind(fn)` calls with
  `model.func_kind(fn)` (or an explicit `ctor_names` parameter where the model
  is not to hand), and `func_kind_name` likewise. Then
  `progseq_model._ctor_names` is a fallback for callers outside a compile
  rather than the value a compile depends on.
* **Why here and not in P1.T3 or P2:** it touches every body emitter, and both
  of those phases would have had to do it in files a later phase reopens. This
  is the phase that reopens them.
* **Tests:** `tests/unit/test_ctor_name.py` gains a two-thread case — two
  `pssc.compile()` calls with different `--ctor-name` running concurrently,
  asserting both results are correct. It fails before this task.
* **Accept:** no emitter reads the ambient constructor names; the golden
  configs are byte-identical.

---

> **Gate G6a:** the C backend is an object with a declared assembly order, and
> its output has not moved.

> **G6a — met (2026-08-14).** Full suite 1852 → 1879 passing (30 skipped, 3 xfailed), 0 failures; all
> nine golden configs byte-identical and no file under `tests/progseq/golden/`
> written during the phase (newest mtime 17:51, hours before the phase's
> edits).
>
> **What the phase produced.** `targets/c/backend.py` — `COpModelBackend`, with
> the header assembled from an eleven-entry `header_sections()` and the `.c`
> from a three-entry `impl_sections()`. `targets/sections.py` — `Section` plus
> `insert_after` / `insert_before` / `replace` / `remove`, every one of which
> raises `UnknownSection` (naming the sections that DO exist) rather than
> silently doing nothing. `CSettings` grew the five body knobs and is now the
> backend's whole configuration; `c_progseq_gen.generate()` went from ~120
> lines of assembly to 44 — 13 of signature, 11 of docstring, 20 of code —
> whose entire job is turning the command line's keywords into a `CSettings`.
> It is not under 40 by the letter of P6a.T1; the assembly it used to hold is
> gone, which is what the criterion was for.
>
> **Two things the accept criteria did not ask for, and why they are here.**
>
> *The ctor emitter follows the body emitter.* `body_emitter_cls` is honoured
> for operations, but `_init` bodies are emitted by `_CtorEmitter` — a
> subclass. A swap that stopped at operations would render one model two ways
> in one file, which is §2.6.3's pair-of-overrides failure in its quietest
> form. `_CtorEmitter` is now `_CtorMixin` + the body emitter, composed per
> swap, and `test_a_swapped_body_emitter_reaches_ctor_bodies` asserts EVERY
> `_init` carries the marker, not just one.
>
> *`impl_sections()`.* Design §2.6.1 lists only `header_sections`. A company
> prologue goes at the top of a generated `.c` at least as often as at the top
> of a header, and with only the header sectioned an extension would have had
> to override `impl_text` wholesale to get it.
>
> **T5 needed a different test than the plan proposed.** The plan asked for a
> two-thread case that "fails before this task". It would not have: a
> ContextVar is per-thread, so two threaded compiles never shared the value —
> the leak P1.T3 fixed was sequential reuse. What the ambient read actually
> costs is a caller who elaborates a model DIRECTLY (`pssc.testing`, a plugin
> target, an embedding tool): the emitters believed the process over the model
> they were handed. `test_generation_does_not_read_the_ambient_ctor_names`
> generates with the ambient value deliberately disagreeing with the model and
> asserts the output is unchanged. It failed for all three backends before this
> task and passes for all three now, which is the stronger claim anyway:
> generated output is a function of the model.
>
> **Scope beyond the C backend.** T5 could not be C-only — `func_kind` is
> shared. `ctor_names` is now explicit at every call site in
> `sv/lower_progseq.py`, `sv/lower_api_types.py` (hence `collect_api_types`,
> which all three backends use), `cpp/lower_progseq.py`, `c/lower_progseq.py`
> and `validate_calls.py`. No emitter reads the ambient value; a single-argument
> `func_kind(fn)` no longer appears anywhere in `src/`.
>
> **What G6a does not close.** The class is C-only: SV and C++ are still free
> functions assembled by `progseq_gen.generate` / `cpp_progseq_gen.generate`.
> The override surface is not yet marked or manifested — every public method is
> de facto API until P6b.T1, which is the task that makes growing it a
> deliberate diff. `emit_extra_files` landed as `extra_files` returning
> `{name: text}` (the writer stays in one place); P6b may still rename it.
> Nothing yet asserts the C header's section ORDER is the one C requires —
> `test_section_list_matches_emitted_order` checks that the emitted file
> follows the list, not that the list is correct, and correctness there is
> still held by the golden set and the compile tests.

---

### Phase 6b — Tier B: the published override surface

Do not start until there is a real extension to validate against (design §6
note). P6b.T6 is that extension.

---

**P6b.T1 — `@overridable` + manifest**

* **Depends:** G6a
* **Files:** `src/pssc/targets/overridable.py` (new), `c/backend.py`
* **Change:** Decorator recording `since`, `stability` (`stable`/`provisional`),
  and optional `pairs_with`. A checked-in manifest
  (`docs/override-surface.json`) lists the marked methods with their metadata,
  in the manner of `test_package_layout.FROZEN_API`. `pssc targets --overrides
  <name>` prints it.
* **Tests:** `tests/unit/test_override_surface.py::test_manifest_matches_code`
  — fails if a method is marked, unmarked, or has its stability changed without
  the manifest being updated. `::test_provisional_methods_are_listed_as_such`.
* **Accept:** growing the surface is a reviewable diff in two files.

---

**P6b.T2 — `pairs_with` enforcement**

* **Depends:** P6b.T1
* **Files:** `src/pssc/targets/overridable.py`,
  `src/pssc/targets/op_model.py`
* **Change:** At target registration, walk the backend class's MRO; if a
  subclass overrides one member of a declared pair and not the other, raise
  naming both methods and why they move together. ~~Initial pairs:
  `emit_signature`/`emit_call`, `emit_handles`/`emit_ctor`,
  `emit_accessors`/`emit_reg_call`.~~ Those were sketch names; two of the three
  have no counterpart in the code as built. **As landed:**
  `emit_guard_open`/`emit_guard_close` and `emit_api_types`/`emit_value_unions`
  — see the G6b note.
* **Tests:** `tests/unit/test_override_surface.py::test_half_pair_override_rejected`,
  `::test_full_pair_override_accepted`.
* **Accept:** the mechanical half of the fragile-base-class risk (I13) is
  caught at registration, not at compile time or in generated output.

---

**P6b.T3 — `derives_from`**

* **Depends:** P3.T4, P6a.T1
* **Files:** `src/pssc/targets/op_model.py`, `call_legality.py`, `cli.py`
* **Change:** `derives_from = "<target>"` makes three things inherit:
  call-legality entries (through `register_extension(inherit=)`), CLI options
  (`super().add_args()` resolving the ancestor's contributions), and
  `target_cfg`. Each is wired and tested **separately** (I15) — one integration
  test would let two of the three silently not work. **Four, as landed:**
  STYLES are keyed `"<target>:<name>"`, so without inheriting them a derived
  target has no `--style default` and cannot generate at all.
* **Tests:** `tests/unit/test_derives_from.py::test_legality_inherited`,
  `::test_options_inherited`, `::test_target_cfg_inherited`,
  `::test_target_cfg_override_wins`, `::test_unknown_ancestor_is_error`.
* **Accept:** a derived target with an empty body behaves exactly like its
  ancestor under a different name — asserted by generating with both and
  diffing.

---

**P6b.T4 — `emit_extra_files`**

* **Depends:** P6a.T1
* **Files:** `src/pssc/targets/c/backend.py`, `op_model.py`
* **Change:** Hook returning additional written TEXT (`{name: text}`, not
  paths — the writer stays in `write_files`), merged into the result in
  compilation order (extras after the artifacts they accompany, before the
  copied core — document the rule).
* **Tests:** `tests/progseq/test_c_backend_class.py::test_extra_files_reported_in_order`;
  dv-flow classification picks them up (`tests/dvflow/test_common.py`).
* **Accept:** an extra generated header appears in the dv-flow fileset.

---

**P6b.T5 — `assert_differs_from_baseline`**

* **Depends:** P4.T1, P6a.T2
* **Files:** `src/pssc/testing/__init__.py`
* **Change:** Generate with the extension and with its baseline; attribute each
  difference to a section name (or `extra_files`); fail if a difference falls
  outside `expect_changed`, and *also* fail if a declared change did not occur —
  a stale expectation is as misleading as a missing one.
* **Tests:** `tests/unit/test_testing_kit.py::test_differential_detects_unexpected_change`,
  `::test_differential_detects_missing_declared_change`.
* **Accept:** the helper's failure message names the section and shows the diff.

---

**P6b.T6 — Tier-B fixture extension**

* **Depends:** P6b.T1–T5
* **Files:** `tests/plugins/pssc_fixture_plugin/backend.py`
* **Change:** A realistic tier-B extension exercising all four override kinds
  from the design's Level 2: an inserted section, a wrapped `emit_operation`, an
  extra file, and a swapped `style_cls`. It is the validation that the surface
  is the right one.
* **Tests:** `tests/unit/test_plugin_integration.py::test_tier_b_extension_*` —
  it registers, generates, passes conformance, and its differential test
  passes.
* **Accept:** the extension is under 150 lines. **If it is not, the surface is
  wrong and P6b.T1's marked set should change before this ships.**

> **Gate G6b:** a mid-weight extension exists, is small, is proven to differ
> from its baseline only where intended, and passes conformance.
>
> **G6b met.** `tests/plugins/pssc_fixture_plugin/pssc_fixture_plugin/backend.py`
> is 84 lines, over half of them the docstring saying why each override is
> there. It registers from an installed distribution, generates, renders the
> common tier, is deterministic, and differs from `op-model-c` in exactly four
> places — `acme_compliance` (added), `decls` and `impl` (the house style
> renaming exported symbols), `extra_files` (added). 1879 → 1926 passing;
> golden byte-identical, and no file under `tests/progseq/golden/` was written
> during the phase.
>
> **The phase was executed in the order the work demanded, not T1→T6.** The
> plan's own note says the surface must be validated by a real extension; the
> marked set therefore could not be decided first. Order run: T4, T3, then the
> extension as a spike, then T1/T2 marking what the spike had used, then T5,
> then the extension's tests. Every finding below came from the spike, which
> is what it was for.
>
> **The extension needed a seam that did not exist.** Three of the four Level-2
> override kinds were already there; a wrapped `emit_operation` was not — the
> per-operation loop was inside `lower_impl`, and `body_emitter_cls` cannot
> reach a signature or a prologue because it only ever sees statements of the
> model's. `lower_operation(fn, ctx)` is now extracted, `lower_impl` takes an
> `emit_operation` callback, and the backend supplies its own. `ctx` is an
> `OpCtx` record rather than eight keyword arguments, so a new lowering detail
> does not change a signature every override would have to follow.
>
> **`derives_from` had to carry a fourth thing: STYLES.** The plan names three
> (legality, options, `target_cfg`). The `pssc.styles` group is keyed
> `"<target>:<name>"`, so a derived target had no styles at all — not even
> `default`, which every C generation resolves. Found by
> `test_an_empty_derived_target_behaves_like_its_ancestor` failing with
> "unknown style 'default' … available: (none)". `style_targets()` searches the
> derivation chain, nearest first.
>
> **The CLI's plugin option policy refused a derived target.** A plugin's
> options must be namespaced `--<target>-…`, and a derived target contributes
> `--root` by delegating to its ancestor's `add_args`. Those are not the
> plugin's options; they are a built-in's, reaching the `dest` the derived
> target reads. `_inherited_options` asks the ancestor what it contributes, so
> the exemption covers exactly those — a derived plugin adding a NEW `--prefix`
> is still refused.
>
> **The pairs are not the ones the plan listed.** `emit_signature`/`emit_call`,
> `emit_handles`/`emit_ctor` and `emit_accessors`/`emit_reg_call` were sketch
> names; two of the three have no counterpart in the code as built (the call
> and reg-call sides live in the body emitter). The pairs declared are
> `emit_guard_open`/`emit_guard_close` (one macro, two halves) and
> `emit_api_types`/`emit_value_unions` (one set of type declarations,
> partitioned — an overlap declares one type twice, incompatibly). Both are
> mechanical and neither produces a false positive on a legitimate override.
> `emit_accessors` ↔ the body emitter's call side is real (DEFINE vs CALL, a
> link error if they disagree) and is deliberately NOT declared: the call side
> is not a method, and rejecting every `emit_accessors` override — most of
> which do not rename anything — would cost more than it catches. It is
> recorded in `emit_accessors`'s docstring, which is why that method is
> provisional rather than stable.
>
> **`assert_differs_from_baseline` needed a section map to attribute to.**
> `OpModelTarget.sections(model, opts)` returns `{"<file>:<section>": text}`
> without writing anything; the C target builds it from the same backend
> methods `generate` uses. Two supporting splits: `settings_for` out of
> `c_progseq_gen.generate` (so "what WOULD be generated" is answerable), and
> `build_model` out of `OpModelTarget.run` (so a caller need not restate how
> `--root` and `--ctor-name` are read). A target that publishes no sections
> falls back to whole-file comparison; comparing a sectioned target against an
> unsectioned one is refused outright, because it reports every section as a
> difference — a result that looks like a diff and means nothing.
>
> **What G6b does not close.** The surface is C-only, because the class is. The
> manifest covers one class and `pssc targets --overrides` prints only what a
> target publishes through `backend_class()`. `emit_extra_files` writes into
> one flat output directory — an extension wanting a subdirectory has no way to
> ask. `since` is `"0.1"` for everything, which is honest (nothing has shipped)
> but means the field carries no information yet. The admission rule for a new
> `@overridable` is stated in the decorator's module docstring and in
> `scripts/regen_override_surface.py`; `docs/extension-stability.md` (PD.T8)
> is still unwritten, so the deprecation WINDOW is named nowhere.

---

### Phase 7 — The body walker

Highest-risk refactor in the plan (design I6). One language at a time,
byte-identical at each step, abandonable without loss if the abstraction does
not hold.

---

**P7.T1 — `BodyWalker`**

* **Depends:** G2
* **Files:** `src/pssc/targets/body_walker.py` (new)
* **Change:** Dispatch (`emit`/`stmts`/`stmt`/`expr`), comment attachment, the
  write-only-local scan (`c/lower_progseq.py:722`) and the channel-output scan
  (`:775`) — all language-neutral. Rendering stays abstract.
* **Tests:** `tests/progseq/test_body_walker.py` — the scans reproduce the C
  emitter's current results on the WB DMA model exactly.
* **Accept:** the scans are proven equivalent before either backend moves.
* **Landed.** `targets/body_walker.py`: `hook_name`, the two scans as FREE
  FUNCTIONS (`scan_write_only`, `scan_output_locals`), and `BodyWalker` with
  `emit`/`stmts`/`stmt`/`render_stmt`/`expr`, `indent`, `comment_style`,
  `hook_for`, `hooks()`. Dispatch is by NODE CLASS NAME — `StmtAnnAssign` ->
  `stmt_ann_assign` — so a reader holding an IR class finds its rendering with
  no table to consult, and no table can fall out of step with the IR. The
  private method is `render_stmt`, not `stmt_lines`: anything spelled `stmt_*`
  would read as a hook.
  The scans are functions, not methods, because that is what let them be
  checked against the shipping C emitter's own results **before** either
  backend moved — 19 tests, every operation of the WB DMA model, captured from
  a real generation rather than a hand-built configuration.
  `scan_output_locals` takes the "is this an output call" predicate: which
  calls count is the target's business, the walk and the requirement that the
  argument have a NAME to record are not.

  **Two findings.** (1) `_scan_write_only` was duplicated verbatim in the C and
  C++ emitters — the plan cites only the C one. (2) **The WB DMA model no
  longer exercises it at all.** `_scan_write_only`'s docstring cites
  `ok = inflight.try_get(tok)` in `wait_completion`; that body now reads `ok`,
  and NO test in the suite looked for the `(void)x;` the analysis exists to
  emit. The equivalence is therefore measured on a probe model that does
  discard, and `test_the_wb_dma_model_no_longer_has_a_write_only_local` records
  the absence rather than leaving a guard that silently compares two empty
  sets.

---

**P7.T2 / P7.T3 — Port C, then SV**

* **Depends:** P7.T1
* **Change:** Each `_BodyEmitter` becomes a `BodyWalker` subclass rendering only.
  C first (most complete). **If C cannot be ported byte-identically in one
  focused attempt, stop and record why** — the abstraction is then wrong, and
  Phase 7 is dropped rather than forced. That outcome is an acceptable result of
  this plan, not a failure of it.
* **Tests:** golden configs; the existing `test_c_body_lowering.py` and
  `test_op_model_behaviour_c.py` unchanged.
* **Accept:** byte-identical per language; the C++ emitter may stay unported.
* **Landed, both languages, byte-identical.** No golden file was written. The C
  cascade became 13 statement hooks + 9 expression hooks; SV, 13 + 9 (its set
  differs from C's in exactly two places, and both are real: `this` exists in
  SV, and the upward-reference rejection is a C rule). `stmts`/`stmt` and the
  comment attachment are gone from both — one site now carries a body's prose
  into three languages' output.
  `_CtorMixin` stopped overriding `expr` wholesale (it ran on every operand of
  an `initialize` body to look at one node kind); after T4 it supplies
  dispositions instead.
  Two dead lines went with the port: SV's `_field_write` ended with an
  unreachable `raise ValueError(f"unsupported expr {cn}")` naming a variable
  not in scope.
  **C++ stays unported**, as the accept line allows. It shares the statement
  set, so the same port would apply; there is no third data point to be had
  from doing it and its `_scan_write_only` duplicate is the part worth taking.

---

**P7.T4 — Dispatch on `Disposition`**

* **Depends:** P7.T2
* **Files:** `body_walker.py`, `call_legality.py`
* **Change:** Calls are classified once via `classify()` and dispatched on the
  resulting `Disposition`, replacing the hand-written
  register→channel→builtin→model chain. The registry stops being a parallel
  list checked by a separate pass and becomes the dispatch table.
* **Tests:** `tests/progseq/test_call_legality.py::test_dispatch_matches_registry`
  — every `Disposition` has a walker hook, and every hook is reachable.
* **Accept:** byte-identical; adding a Tier-2 entry without a hook is a loud
  error.
* **Landed for C; SV deliberately not moved.** `CallDispatch` in
  `body_walker.py` classifies once and dispatches to `call_<disposition>`; the
  C emitter sets `legality_target = "op-model-c"` and `call_context`
  (`Ctx.TARGET`, `Ctx.SOLVE` in the ctor emitter) and defines seven hooks, with
  three more on `_CtorMixin`. Byte-identical: no golden file written.
  `validate_calls._model_names` became **public** `model_names` and the emitter
  calls it — two implementations of "what counts as an operation" would
  disagree exactly where it matters, with the gate vouching for a call the
  emitter then cannot place.
  Three failure modes are now distinct, where the chain produced one: a
  disposition with no hook ("defines no call_fold()"), a hook that returns
  `None` ("the NAME says one thing and the receiver another" — classification
  is by name), and a call the gate should have caught ("reached the emitter
  unclassified").
  **SV keeps its own `expr_call`.** Its chain ends in a generic
  `f"{callee}({args})"` that renders any call correctly — SV registers are
  objects with methods — so dispatching it on `Disposition` would be
  documentation with a risk attached, not a decision moved into the table. The
  arrangement is worth the change only where each branch is a distinct
  rendering, which is C.

> **Gate G7:** two languages share one walk, and the legality registry is the
> dispatch table rather than documentation about it.
>
> **G7 holds.** Suite 1926 -> 1958. All nine golden checks pass with **no
> regeneration**, across four separate edits (T1 additive, C port, SV port,
> disposition dispatch) — which is the whole reason the phase was run in that
> order rather than as one change.
>
> The order was T1 -> T2 -> T3 -> T4, exactly as written, and T1's "prove the
> scans before either backend moves" earned its place: it is what surfaced that
> the write-only scan had no coverage anywhere in the suite. R2 (the risk
> register's "extraction cannot preserve C behaviour") did not materialise —
> the C port went byte-identical on the first run of the golden checks.
>
> **What G7 does NOT close.**
>
> * The C++ emitter is unported and still carries its own copy of
>   `_scan_write_only`. Two copies of that analysis remain in the tree; the
>   walker's is a third until C++ moves.
> * `Disposition` dispatch is C-only (above). "The registry IS the dispatch
>   table" is true of one backend, not of pssc.
> * The dispatch classifies BY NAME, which is what the emitters already did:
>   `classify`'s own docstring records that it cannot tell a user component's
>   no-argument `get()` from a channel receive. Moving the decision into the
>   table did not fix that, and the "receiver says otherwise" error is where it
>   now surfaces.
> * `BodyWalker` is not published as an override surface — it carries no
>   `@overridable` marks and is absent from `docs/override-surface.json`. This
>   is the refactor `body_emitter_cls` was marked **provisional** for, and that
>   marking is now doing its job: an extension that subclassed the C emitter's
>   `_stmt_lines` has to move to hooks. Nothing in-tree or in the fixture plugin
>   did.

---

### Phase 8 — Validation and the wider win

---

**P8.T1 — In-tree `op-model-py`**

* **Depends:** G4, P1.T4; P7 if it landed
* **Files:** `src/pssc/targets/py/` (new)
* **Change:** A minimal but real Python operation-model style: component
  classes, register accessors, operations, `#` comments, a documented bus
  protocol the generated code calls into. Its purpose is threefold — it is the
  worked example the docs point at, the second consumer proving the shared
  layer is language-neutral, and useful on its own for cocotb bring-up.
* **Tests:** `tests/progseq/test_op_model_py.py` — generates, imports, and
  drives a stub bus; conformance passes; golden config `py-default` added.
* **Accept:** written using only the public API a plugin author has. **Any
  private import needed here is a gap in the public surface and must be closed
  rather than worked around** — that is what this task is for.
* **Landed:** `op-model-py` (alias `py-progseq`), `src/pssc/targets/py/`
  (naming, `lower_reg_model`, `lower_api_types`, `lower_progseq`, `backend`) +
  `src/pssc/targets/py_progseq_tgt.py` + `src/pssc/share/py/pssc_rt.py`.
  26 tests; golden config `py-default` frozen; conformance 5/5.

  **The accept line found one gap, and it was closed rather than worked
  around.** The folded register walk — which registers exist, at what constant
  offset, with which strides — was `c/lower_reg_model._collect_accessors`, and
  it is the FIRST thing a second backend needs. It is now
  `targets/reg_layout.py` (`RegAccessor`, `collect_accessors`), the C backend
  builds its `_Acc` from it, and the golden tree did not move. This matters
  more than the tidiness: an address is the one thing in a generated API a
  golden snapshot can never check (§4.2), so two backends computing offsets two
  ways was the worst duplication available here.

  **What the shared layer actually carried.** `BodyWalker` and `CallDispatch`
  took the Python emitter unchanged — 13 statement hooks, 10 expression hooks,
  one hook per `Disposition`, no walk of its own. `scan_output_locals` was the
  interesting one: C uses it to WIDEN a `try_get` output local to `uint64_t`,
  Python uses it to make that local a one-element CELL, and the analysis is
  identical because the question ("which locals does a channel write through?")
  is about the PSS. `scan_write_only` is not called here at all, which is the
  design's own answer working (`a target that does not care simply does not
  ask`): Python has no unused-variable diagnostic to silence.

  **The one defect this found in the new code, and how.** The first cut
  rewrote `ok = ch.try_get(tok)` into a tuple unpack at the ASSIGNMENT, which
  silently did nothing for `if (!inflight.try_get(tok))` — the form the real
  model actually uses. The generated module imported and ran and handed back a
  token that was never assigned. Caught by reading the generated `wait_hint`,
  not by a test, which is why `test_a_channel_output_local_is_a_cell` now
  asserts on the generated text as well as on behaviour.

  **Not done, deliberately.** `PyOpModelBackend` carries no `@overridable`
  marks and is absent from `docs/override-surface.json`. Publishing a second
  override surface is a decision with its own admission rule
  (`docs/extension-stability.md`: a method becomes overridable because a real
  extension needed it), and none has. The class is built to the same two rules
  as `COpModelBackend` — every `emit_*` returns text, the module is exactly its
  `module_sections()` — so publishing it later is a manifest edit, not a
  refactor.

---

**P8.T2 — `--emit-manifest`**

* **Depends:** P2.T1
* **Files:** `src/pssc/targets/op_model.py`, `cli.py`
* **Change:** `--emit-manifest FILE` writes the `OpModel` as JSON: components,
  operations and signatures, register groups with folded offsets, generated
  files and their roles, ABI-affecting settings. Schema versioned from day one.
* **Tests:** `tests/progseq/test_manifest.py` — schema stable; offsets match
  the generated accessors; round-trips.
* **Docs:** PD.T7.
* **Accept:** the manifest is sufficient to answer "what operations and
  registers exist" without parsing generated code.
* **Landed:** `src/pssc/targets/manifest.py` + `--emit-manifest FILE` on
  `OpModelTarget.add_args`, so the flag is the FAMILY's and not one backend's.
  17 tests.

  Written after `emit` and from the `OpModel` the emitters render, so it cannot
  describe a different API from the one produced. The offsets come from
  `reg_layout` — the same walk P8.T1 hoisted — and the two strongest tests
  check them against the OUTPUT rather than against the model:
  `test_every_offset_appears_in_the_generated_c` greps the emitted `_addr`
  accessor for the literal, and `test_every_offset_matches_a_live_python_accessor`
  CALLS the generated Python accessor and compares the address it returns.

  Two decisions worth recording. **Roles come from the target, not from the
  bytes:** `runtime` vs `generated` is decided by `core_file_names()`, the list
  the target copied FROM, because deciding by extension is wrong (`pssc_mem.h`
  beside `wb_dma.h`) and comparing bytes answers "does it match today" rather
  than "where did it come from". **ABI settings are the target's own answer:**
  `OpModelTarget.abi_settings()` returns `{}` and the C target returns its
  `flags_for(opts)` — the same method the generation reads, so the manifest
  cannot describe a build that was not produced. `op-model-py` returns `{}`,
  which is honest: it has no option that moves a symbol, an offset or a
  signature.

  One thing a reader will look for and not find: `addr_handle_t` reports as
  `chandle`. It is `typedef chandle addr_handle_t` and the typedef name does
  not reach the IR, so reporting it would be the manifest inventing a name the
  model no longer carries.

---

**P8.T3 — A built-in as a tier-B subclass (O10)**

* **Depends:** G6b
* **Change:** Re-express one existing built-in variant (candidate:
  `c-embedded-presolved`, or a `--lifecycle static` split) as a subclass of its
  sibling, and delete the flag threading it replaces.
* **Tests:** golden configs for the affected targets, unchanged.
* **Accept:** byte-identical, with a net reduction in conditional branches. **If
  a built-in variant cannot be expressed this way, that is evidence the override
  surface is wrong** — record it and revisit P6b.T1.
* **Landed as a MEASUREMENT, not a deletion.** `tests/progseq/test_builtin_as_tier_b.py`,
  6 tests. Both candidates were tried and they answer differently:

  * **`--emit-stubs` CAN be a subclass.** Six lines over `emit_extra_files`,
    reusing pssc's own `stubs_text`, reaching nothing unmarked — and its output
    is byte-identical to the flag's, including under
    `--lifecycle static --link-style direct`, which is what `derives_from`
    carrying the ancestor's options buys.
  * **`--lifecycle static` CANNOT.** The choice is read at five sites across
    `c/lower_progseq.py` and `c/style.py` — two prototype blocks, two `#include`
    decisions and the banner — and the nearest published overrides
    (`emit_decls`, `emit_impl`) are WHOLE-SECTION. A subclass removing
    `_create`/`_destroy` would have to re-derive every prototype in the API to
    drop two, which is the copy the surface exists to prevent. Both facts are
    now tests, so a sixth site or a new `emit_lifecycle_*` hook fails here
    rather than leaving this note wrong.

  **Neither flag was deleted, and that is a decision rather than an omission.**
  The plan's premise is that a variant expressed as a subclass replaces flag
  threading. `--emit-stubs` is not a variant of the API — it is a build-integration
  choice that composes with every other option, so promoting it to a target
  name trades one boolean for a combinatorial namespace (`op-model-c-stubs`,
  `-static`, `-stubs-static`). The evidence the task wanted is the byte-identity
  proof, which is checked in; the deletion would have been a worse codebase.
  Revisit if a real extension asks for `emit_lifecycle_decls`.

> **Gate G8:** the shared layer is proven across two languages *and* proven
> sufficient for an external author, because a built-in was written with it.
>
> **Met, 2026-08-15.** 1958 → 2008 tests (`pytest tests`; 1874 → 1924 for
> `tests/progseq tests/unit`). The seven pre-existing golden configs did not
> move: `py-default` is the only new snapshot, which is the byte-identity proof
> for hoisting the register walk out of the C backend. The gate's first half is
> the stronger result: `op-model-py` is a THIRD language on the shared layer, written after
> it existed, and it needed no walk, no call dispatch, no elaboration and no
> register fold of its own. What `targets/py/` contains is Python and nothing
> else. The one thing it did need that was not shared — the folded accessor
> walk — is now `targets/reg_layout.py` and the C backend uses it too, with the
> golden tree unmoved.
>
> The second half is met more narrowly than the wording suggests, and the
> difference is worth stating. A built-in variant WAS written with the surface
> and is byte-identical (P8.T3's `--emit-stubs` subclass), which is the
> evidence. But it ships as a test fixture rather than as a target, because
> promoting it would have been the wrong design — see P8.T3's note. And the
> other candidate the plan named could not be expressed at all: the surface has
> no hook at the granularity of "the lifecycle declarations", which is a real
> gap in P6b.T1's marked set and is now a failing-when-fixed test.
>
> **What G8 does not close:**
>
> * The C++ emitter is still unported from Phase 7 and still carries its own
>   `_scan_write_only`; the Python backend did not touch that.
> * `op-model-py` has no published override surface, by choice (P8.T1's note).
>   A tier-B extension of it is possible in Python and unsupported in writing.
> * The manifest has no consumer in-tree. Nothing reads it back except its own
>   tests, so its schema is versioned and unexercised — the first real consumer
>   is what will find what is missing from it.
> * `docs/op-model-manifest.md` (PD.T7) is unwritten, so the schema is
>   documented only by `targets/manifest.py`'s docstrings and the tests.
> * The Python target claims `format`/`format_string`/`urandom` unsupported for
>   reasons that are choices rather than obstacles (a PSS format string is not
>   Python's; seeding is the caller's policy). Both are reversible and neither
>   is a model anyone has asked to lower.

---

### Phase D — Documentation (PD.T1–T8)

Deliverables are listed in §5. What matters about how they landed:

**Every example is quoted, not written.** The guide's code blocks carry a
leading `#` comment naming a repository file, and
`tests/unit/test_doc_examples.py` asserts the block is a **verbatim contiguous
slice** of that file. There is no elision syntax on purpose: a `...` in the
middle is where a quotation stops being checkable and starts being a paraphrase
that drifts. Eleven blocks are quoted this way, from
`tests/plugins/pssc_fixture_plugin/` and `src/pssc/targets/py*` — files the
suite already runs, which is a much stronger guarantee than a snippet that
merely imports cleanly. The same file also checks that every `--flag` shown in a
shell example is a real option **of the subcommand that example invokes**, and
that no link between doc pages dangles.

That is exit criterion 6 ("every example in it is executed by a test") enforced
mechanically rather than by intent. It found two errors while being written:
`--overrides` shown against `pssc compile`, which has never had it, and two
hand-off links to pages that did not exist yet.

**Level 3 needed an example and did not have one.** Nothing in the tree
subclassed `OpModelTarget` from another distribution — the tier-B fixture
subclasses a built-in *target*, which inherits an emitter and so never asks
whether the base class is usable on its own. `pssc_fixture_plugin/listing.py`
(53 lines, `api-listing`) is that example: its own output shape over pssc's
elaboration, with `derives_from` supplying the legality it would otherwise not
have. Three tests cover it, including that its listing agrees with the API the C
target generates from the same elaboration.

**The deprecation window is now named** (`docs/extension-stability.md` §4), which
G6b recorded as the one thing the stability marks pointed at and nothing stated:
two minor releases for `stable`, one for `provisional`, never in a patch
release, never without a working replacement in the same release — with
correctness defects and a `PSSC_TARGET_API` bump as the two deliberate
exceptions. The admission rule from I14 is §3 of the same page, and
`scripts/regen_override_surface.py` already pointed at that file.

**Repairs made in passing.** The README's documentation index listed five files,
three of which did not exist (`dvflow-tasks.md`, `architecture.md`,
`targets.md`) and two of which were in a `design/` directory that is not in this
repository; `flow.yaml`'s agent-skill block promised the same missing dv-flow
page. Both now point at what exists. `docs/lowering-call-legality.md` still said
nothing on the C path consulted the registry, which stopped being true in P7.T4.
`docs/index.rst` and `conf.py` still called the package `zuspec-fe-pss`.

> **Gate GD:** the four levels are documented, every example is executed, and
> the stability promise is written down.
>
> **Met, 2026-08-15.** 2008 → 2025 tests (`pytest tests`): fifteen in
> `tests/unit/test_doc_examples.py`, two for the tier-3 example. New pages:
> `docs/custom-generator-styles.md`, `docs/extension-stability.md`,
> `op-model-manifest.md`; `api.rst` gains an Extension API section covering the
> nine public modules; `progseq.rst`/`progseq_design.rst` name the shared layer
> and the Python backend.
>
> **What GD does not close:**
>
> * The Markdown pages are not in the Sphinx toctree — this build configures no
>   Markdown parser, and adding `myst_parser` is a dependency decision, not a
>   documentation one. `index.rst` lists them by path instead, which is honest
>   but means `make html` does not render them.
> * There is no dv-flow task for `op-model-py`. The skill block now says so
>   rather than implying the family is complete; adding `OpModelPy` is small and
>   is not documentation work.
> * `docs/dvflow-tasks.md` is still unwritten. The task `doc:` blocks in
>   `flow.yaml` are the reference, and a page duplicating them by hand would
>   drift; generating it from `flow.yaml` (the pattern
>   `regen_override_surface.py` already establishes) is the shape that would
>   work.
> * The guide's shell examples are checked for flag *existence*, not for
>   producing the output shown. Nothing runs `pssc compile` from the doc.

---

## 4. Test strategy

### 4.1 Categories

| Category | Purpose | Where | Cost |
| --- | --- | --- | --- |
| **Golden snapshot** | prove behaviour-preserving refactors | `tests/progseq/golden/`, marker `golden` | fast |
| **Unit** | one function, one behaviour | `tests/unit/` | fast |
| **Contract** | a rule that must hold for every target (Tier-1, manifest, pairings) | `tests/unit/`, `tests/progseq/` | fast |
| **Differential** | an extension differs from its baseline only where declared | `pssc.testing` | fast |
| **Conformance** | semantic correctness of any target's output | `pssc.testing.conformance` | medium |
| **Build/behavioural** | generated C/SV actually compiles and drives a device correctly | existing `c_toolchain` / `sim` markers | slow |
| **Plugin integration** | real entry-point metadata | marker `plugin` | slow (install) |

### 4.2 What each category cannot catch

Stated because the gaps are where the defects will be:

* Golden snapshots prove *sameness*, never *correctness*. A wrong address
  frozen into a snapshot stays wrong and stays green. The behavioural tests
  (`test_op_model_behaviour_c.py`, `test_sim_wb_dma.py`) are the only thing
  standing behind them, which is why phases that touch address computation
  (5a, 5b) must run those, not only the golden set.
* Contract tests prove a rule is *stated*, not that it is *honoured* at every
  site. `test_custom_macro_reaches_every_access_site` (P5b.T3) is deliberately
  written as an absence assertion over the whole output for exactly this
  reason — a per-site test would miss the site nobody remembered.
* Differential tests are only as good as their `expect_changed` set, which is
  why P6b.T5 fails on *undeclared* and *unrealised* changes both.

### 4.3 New test files

```
tests/progseq/golden_util.py                 P0.T1
tests/progseq/golden/<config>/…              P0.T2
tests/progseq/test_golden_op_models.py       P0.T2
tests/progseq/test_op_model_layer.py         P2.T1
tests/progseq/test_mem_access.py             P5a.T1
tests/progseq/test_style_policy.py           P5b.T1
tests/progseq/test_sections.py               P6a.T2
tests/progseq/test_c_backend_class.py        P6a.T1
tests/progseq/test_body_walker.py            P7.T1, P7.T2, P7.T3
tests/progseq/test_conformance.py            P4.T2
tests/progseq/test_op_model_py.py            P8.T1
tests/progseq/test_manifest.py               P8.T2
tests/unit/test_op_model_target.py           P2.T2
tests/unit/test_target_discovery.py          P3.T1, P3.T2
tests/unit/test_style_discovery.py           P5b.T2
tests/unit/test_override_surface.py          P6b.T1, P6b.T2
tests/unit/test_derives_from.py              P6b.T3
tests/unit/test_testing_kit.py               P4.T1
tests/unit/test_resources.py                 P4.T5
tests/unit/test_plugin_integration.py        P3.T6, P6b.T6
tests/plugins/pssc_fixture_plugin/           P3.T6, P6b.T6
src/pssc/__main__.py                         P3.T6 (python -m pssc)
src/pssc/targets/overridable.py              P6b.T1, P6b.T2
docs/override-surface.json                   P6b.T1 (checked-in manifest)
scripts/regen_override_surface.py            P6b.T1
src/pssc/targets/body_walker.py              P7.T1, P7.T4 (CallDispatch)
src/pssc/targets/reg_layout.py               P8.T1 (folded accessor walk, shared)
src/pssc/targets/manifest.py                 P8.T2
src/pssc/targets/py/                         P8.T1 (the Python backend)
src/pssc/targets/py_progseq_tgt.py           P8.T1
src/pssc/share/py/pssc_rt.py                 P8.T1 (bus protocol + Chan1)
tests/progseq/test_builtin_as_tier_b.py      P8.T3
```

### 4.4 Changed existing tests

| File | Change | Task |
| --- | --- | --- |
| `tests/unit/test_target_registry.py` | `test_discover_is_noop_when_off` updated for real discovery | P3.T1 |
| `tests/progseq/test_call_legality.py` | contract asserted over the registry, not the literal dict; alias-resolution and gate tests added; dispatch-table tests (`test_dispatch_matches_registry`, hook reachability, the two loud errors) | P1.T2, P3.T4, P7.T4 |
| `src/pssc/targets/validate_calls.py` | `_model_names` promoted to public `model_names`, so the gate and the emitter's dispatch classify by one implementation | P7.T4 |
| `src/pssc/targets/c/lower_reg_model.py` | `_collect_accessors` delegates to the shared `reg_layout.collect_accessors`; `_prim_bits`/`_reg_value_bits` re-exported from there | P8.T1 |
| `src/pssc/targets/op_model.py` | `--emit-manifest` on the family parser; `run` writes it after `emit`; `abi_settings()` hook | P8.T2 |
| `tests/progseq/golden_util.py` | `py-default` config added | P8.T1 |
| `tests/unit/test_ctor_name.py` | two-compiles-in-one-process test added | P1.T3 |
| `tests/unit/test_package_layout.py` | duplicate-helper and bus-spelling greps added | P2.T6, P5a.T3 |
| `tests/dvflow/test_build_tasks.py` | `style:` param forwarding | P5b.T2 |
| `tests/dvflow/test_common.py` | memento, filetype registration; a backend extra file is claimed by a fileset | P4.T3, P4.T4, P6b.T4 |
| `tests/unit/test_cli.py` | `targets --overrides` | P6b.T1 |
| `tests/unit/test_testing_kit.py` | the differential helper | P6b.T5 |

---

## 5. Documentation plan

| ID | Deliverable | File | Depends | Done | Notes |
| --- | --- | --- | --- | --- | --- |
| PD.T1 | **Custom generator styles** — the user-facing guide | `docs/custom-generator-styles.md` (new) | G3 | ☑ | published whole rather than per phase, because every level's example now exists |
| PD.T2 | Level 1 (restyle) section | ″ | G5b | ☑ | quotes the 43-line `AcmeStyle` register-macro mandate verbatim |
| PD.T3 | Level 2 (override) section | ″ | G6b | ☑ | quotes the tier-B backend and the differential test it owns |
| PD.T4 | Levels 3–4 (new emitter / new language) | ″ | G7 or G4 | ☑ | Level 3 needed an example: `pssc_fixture_plugin/listing.py` (`api-listing`). Level 4 is `op-model-py` |
| PD.T5 | Extension API reference | `docs/api.rst` | G4 | ☑ | nine modules, appended below the existing `zuspec.fe.pss` content rather than replacing it |
| PD.T6 | Testing-a-style guide | `docs/custom-generator-styles.md` §Testing | P4.T2 | ☑ | four tests in the order they catch things |
| PD.T7 | Manifest schema reference | `docs/op-model-manifest.md` (new) | P8.T2 | ☑ | every field, from a real generated document |
| PD.T8 | Stability and deprecation policy | `docs/extension-stability.md` (new) | P6b.T1 | ☑ | the window is named: two minor releases for `stable`, one for `provisional` |

Also updated as their phases landed:

* `docs/index.rst`, `docs/conf.py` — the package is `pssc`, not `zuspec-fe-pss`.
  The Markdown pages are listed by path rather than added to the toctree: this
  build configures no Markdown parser (see gate GD).
* `docs/lowering-call-legality.md` — the status header said nothing on the C
  path consulted the registry, which stopped being true in P7.T4. It now
  distinguishes the two targets that dispatch through it from the two that are
  merely gated by it. ☑
* `docs/progseq.rst` / `progseq_design.rst` — a "shared layer" section, the
  Python backend, and `op-model-py` in the backend-choice table. ☑
* `AGENTS.md` — the golden-regeneration rule (P0.T3), and a "Documentation that
  is checked" section: fix the doc, not the source. ☑
* `README.md` — the doc index listed five files, three of which did not exist. ☑
* `src/pssc/dvflow/flow.yaml` — the `AgentSkill` block documents `style:`, drops
  its promise of a `docs/dvflow-tasks.md` that was never written, and says
  plainly that `op-model-py` has no task yet. ☑

**Doc rule for this work:** every `@overridable` method's docstring states what
a subclass may assume and what it must preserve. A published override point
with a docstring that only says what it returns is not documented — the
contract is the invariant, not the signature.

---

## 6. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner task |
| --- | --- | --- | --- | --- | --- |
| R1 | A "byte-identical" phase silently changes output | Med | High | Phase 0 first; regeneration gated behind an env var and reviewed | P0.T3 |
| R2 | `BodyWalker` extraction cannot preserve C behaviour | Med | Med | Abandon Phase 7 rather than force it; it is not load-bearing for tiers A–C | P7.T2 |
| R3 | Override surface published too early and wrongly | Med | High | 6b gated on a real extension; the ≤150-line test is the falsifier | P6b.T6 |
| R4 | Surface grows uncontrolled after release (I14) | High | Med | Admission rule in PD.T8; manifest makes each addition a review | P6b.T1 |
| R5 | A style policy generates plausible but wrong register access (I12) | Med | **Severe** | Enforcement tests P5b.T4; behavioural tests run for phases touching addresses | P5b.T4 |
| R6 | `derives_from` half-works (I15) | Med | Med | Three separate tests, not one integration test | P6b.T3 |
| R7 | Plugin discovery slows every CLI invocation (I10) | Med | Low | Keep entry-point modules dependency-free; defer discovery where the target is known; add a startup-time assertion | P3.T1 |
| R8 | Golden tree becomes unreviewably large | Low | Med | Seven configs only; size check in P0.T2 | P0.T2 |
| R9 | Test-suite runtime grows past usefulness | Med | Med | Slow work behind existing markers; golden set stays fast | §4.1 |
| R10 | **Nothing runs the test suite automatically** — CI builds a wheel and publishes it, with no test job. Every gate in this plan is enforced only by a human remembering to run pytest, including the byte-identical claims R1 depends on | High | High | Not mitigated. Standing up a test job needs `ivpm update` in CI, which is separate work. Until then, treat "gate holds" as meaning "someone ran the suite and said so in the commit" | P0.T3 |
| ~~R11~~ | ~~The C++ backend cannot lower the real operation model~~ | — | — | **Closed 2026-08-14.** The backend was rebuilt to full parity (design I16) and its golden config moved to the real model. What replaced this risk is R12 | P0.T2 |
| R12 | The C++ backend now supports something the C backend does not — a parent reaching a child's channel (design I17). A capability difference between backends is a trap for a model author, who has no way to know which target will refuse | Med | Med | Not mitigated; the C fix is small and the divergence is recorded. Nothing in the WB DMA model hits it, which is why it went unnoticed | — |

### Rollback

Phases 0–4 are additive or behaviour-preserving and can be reverted
independently. Phase 5b, 6b and 7 each land behind a flag or an unused class
until their gate holds, so an incomplete phase can be left in place without
affecting default behaviour. The one irreversible commitment is **P6b.T1**:
once an override surface ships, third parties depend on it. Nothing before it
creates a compatibility obligation.

---

## 7. Exit criteria

The work is complete when all of these hold:

1. A third-party package can add a target, a style, and a tier-B override, each
   discovered by entry point, with no pssc source change.
2. The WB DMA model generates byte-identically to the pre-work baseline for all
   seven original configurations.
3. The three built-in op-model targets share one walk, one classification, one
   offset fold, one legality gate, and pass their own conformance suite.
4. A company register-macro mandate is expressible in a policy of under 150
   lines, and cannot silently violate PSS 3.1 §21.14.1.
5. The published override surface is enumerable (`pssc targets --overrides`),
   pinned by a manifest test, and validated by an extension under 150 lines.
6. `docs/custom-generator-styles.md` covers all four levels, and every example
   in it is executed by a test.

Criterion 6 is deliberately strict: an extension guide whose examples are not
run rots within two releases, and a rotted extension guide is worse than none —
it costs the reader a day before they conclude the docs are wrong.

**Status, 2026-08-15: all six hold.** Criterion 1 is met a level further than it
asks — the fixture plugin ships a style, a tier-B override, a target of its own
*and* an `OpModelTarget` subclass, none of which required a pssc source change.
Criterion 3 now reads "four built-in op-model targets", with the qualification
recorded at gate G7 and G8: `op-model-cpp` shares the model, the fold and the
gate, but not yet the walk. Criterion 6 is enforced by
`tests/unit/test_doc_examples.py` rather than by review.

What the work leaves open is recorded in the risk register (R10, R12) and in the
"does not close" notes on gates G7, G8 and GD. The largest is R10: nothing runs
this suite automatically, so every gate above means "someone ran pytest and said
so".
