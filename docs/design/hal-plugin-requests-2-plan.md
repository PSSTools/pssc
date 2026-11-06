# Second round of HAL-plugin requests: implementation, test and documentation plan

Source: `requests.md` (repo root, untracked, received 2026-11-05). As in
`hal-plugin-requests-plan.md`, this file does not name the requester or their
IP. The requester is **the HAL plugin**: a level-2 target, `derives_from =
"op-model-c"`, that currently runs on pssc `2a17298` with two local patches.
The patches are referred to as **the constants patch** (P1, P2, P3, P5) and
**the exports patch** (P4). The goal is for upstream pssc to provide the
behaviour, so the plugin can drop both patches and move its pin to a release.

**Vocabulary rule (user, 2026-10-21) applies.** Everything pssc ships or tests
uses generic names. Do not use the requester's package, macro, feature or block
names. The requester's fixtures (`tests/data/feedback/*.pss`,
`tests/integration/test_feedback.py`) are not in this repo. Every phase writes
its own generic model.

Every claim below was checked against pssc `4c2aefc` (main) and pssparser
`v3.6.0` (`packages/pssparser`). The request's base, `2a17298`, is 41 commits
older than that.

## Status

| Phase | Content | State |
|-------|---------|-------|
| 0 | Rulings R-Q1..R-Q8 (§2) | **partly ruled** 2026-11-06: R-Q1, R-Q2, R-Q7 as recommended; R-Q3 **widened** (symbolic by default in op-model-c); R-Q4 restated to match; R-Q5, R-Q6, R-Q8 stand as recommended unless reopened |
| 1 | P7: raise the pssparser floor | **done** 2026-11-06, ready to commit |
| 2 | P1: a package `const` folds as itself (bug) | **done** 2026-11-06, ready to commit |
| 3 | P2: op-model-c keeps named constants symbolic by default | not started; needs phase 2 |
| 4 | P5 + P6: prelude sees its sources, dedup, `pssc.preludes` | not started |
| 5 | P3: keep detached comments in bodies (opt-in) | not started |
| 6 | P4: verify on main, aperture-per-instance test | not started |
| 7 | Docs sweep, release notes, reply | not started |

Status note: (update as each step finishes, so that a fresh session can resume
from this file alone)

- 2026-11-06: plan written. Nothing implemented. R-Q1, R-Q2, R-Q3, R-Q7 ruled (R-Q3 widened: phase 3 rewritten). Sub-decisions D3a..D3e in phase 3 are working defaults.
- 2026-11-06 **baseline** (main `4c2aefc`, before any change; `direnv exec .
  python -m pytest`, the venv is `packages/python`, not `../python`):
  - unit 2802 passed, 2 failed. One is the allow-list for this plan's
    file, fixed in phase 1. The other is `test_ir_walk::
    test_every_base_field_is_either_a_child_or_listed`: the ir-core checkout
    has a new `Base.anchor` field that `ir_walk.NON_CHILD_FIELDS` does not
    list. **Pre-existing, out of scope.**
  - progseq 3035 passed, 7 failed, all environmental. 5 goldens and
    `test_c_seam::test_the_header_names_the_generator_version`: the venv
    has no pssc distribution metadata, so the header says `(unknown
    version)`; **each golden differs only in line 1**. One SV matrix case
    `channels of a struct...` XPASSes its strict xfail W64 (the installed
    Verilator has fixed the bug). Until the venv is fixed, read a golden
    failure as "anything beyond line 1?".
  - compliance 377 passed, 548 failed: every failure is `No module named
    'z3'` (the venv has no z3). Compliance cannot gate this work here
    unless z3 is installed (needs the user's OK).
- Phase 1 **done** (ready to commit): `pyproject.toml` `pssparser>=3.6`;
  `tests/unit/test_dependency_floors.py` (fails with the floor at 3.5,
  checked); a stale `_collectMarkers` reference in `diag.py`'s docstring
  (removed in pssparser 3.6) now names `ParseException.markers`. The audit
  of the 3.5→3.6 Python API diff found only `FunctionAccess`/`getAccess`.
  pssparser 3.6.0 **is on PyPI**, so the release is not blocked.
- Phase 2 **done** (ready to commit). `ast2ir.py`:
  - `_is_static_const(decl)`: `static`, or the field's AST parent is a
    `PackageScope`/`GlobalScope`. It uses the parent, not a set filled in
    `_record_const`, so declaration order cannot matter. It is used by
    `_linked_static_const`, `_linked_const_name` and `_rooted_constant`.
  - `_linked_decl`: the qualified and unqualified reference paths fold by
    name (`const_map`, `_resolve_enum_constant`) only when the linker left
    the reference unresolved.
  - `_const_by_name` replaces the falsy-0 `a or b`. That defect was latent:
    sizes take the linker path first, and the test pins it anyway.
  - A component-scope `const` without `static` is a parse error in
    pssparser, so R-Q1's "component scope unchanged" needs nothing.

  Tests:
  - `tests/unit/test_package_const_fold.py` (12): 9 fail without the fix.
  - Matrix case `a package const named like an enum item` in
    `test_m_names.py`: fails on all four targets without the fix and
    passes with it.
  - The planned `test_sv_constants.py` addition was dropped. That test
    works on IR constants, where static and non-static no longer differ,
    and the matrix case covers SV end to end.

  Suites:
  - Unit: 2821 passed, 1 failed (`ir_walk` `anchor`, pre-existing).
  - Progseq: 3039 passed, the same 7 environmental failures.
  - **Goldens:** all 10 configs are byte-identical apart from the
    version line, checked by a scratch script that normalises
    `(unknown version)` and compares every file.

  Next: phase 3.

---

## 1. Findings, per request

| Id | Verdict | Notes |
|----|---------|-------|
| P1 const vs enum item | **OK, the cause is wider** | The parser accepts package `const` without `static` (`PSSParser.g4:171`, `[static] const`; the corpus counts "`const` and `static const` at package scope" as separate storage cells). pssc assumes every package constant is `static`. `_record_const` (`ast2ir.py:904`) puts the non-static field in `const_map` with the correct value. However, the four linker-following paths require `FieldAttr.Static`: `_linked_static_const` `:962`, `_linked_const_name` `:1138`, `_rooted_constant` `:5134`, and through `:988` `_linked_constant`. When the field is not static, the unqualified path (`_translate_expr_ref` `:5485-5503`) calls `_resolve_enum_constant` (`:5673`, which scans every enum **by name**) **before** it calls `const_map`, so the enum item's ordinal wins. A qualified reference consults `const_map` first, which is why qualifying the name hides the bug. **Two side defects:** (a) `_fold_const_expr` (`:1777,1788,1799`) does `const_map.get(qualified) or const_map.get(bare)`, so a qualified constant whose value is **0** falls through to whatever bare name was registered last, possibly in another package; (b) `const_map` has a single bare-name slot, so two packages' same-named constants collide. A non-static constant also gets no `ref`/`width`/`signed`. That matters for P2, and for SV, which names constants by `ref`. |
| P2 symbolic constant | OK | Folding sites: `IntSemantics._int_leaf` `int_semantics.py:319` (`int_const`, a one-point range), `_fit` `:261`, unary `:348,353`, binary `_FOLD` `:373-400`. `CIntSemantics.convert_to` `c/lower_progseq.py:1497-1510` folds an out-of-range constant. `int_cmp_text` → `_cmp_decided` (`:1430-1435`) **decides a comparison from the ranges and prints `0`/`1`**. That is the repro's `(0)`. `CBodyEmitter.expr_constant` `:2452` prints an enum item or `str(value)`. **C++ does not share it.** Its `_BodyEmitter(CIntSemantics)` renders constants inline (`cpp/lower_progseq.py:1018`) and has no `body_emitter_cls` swap point. SV names constants itself (`sv/constants.py`) and does not use `IntSemantics`. Python shares `IntSemantics`. ast2ir does not fold `A+1` (`_translate_expr_bin` keeps an `ExprBin`). It does collapse array sizes, template arguments, SZ2 widths and static-const initializers. The hook cannot reach those, and the request does not need them to. |
| P3 detached comments | **OK, two gaps the patch misses** | `ast_comments` (`ast2ir.py:26-50`) keeps only the `Leading`/`Trailing` placements. pssparser classifies a run cut off by a blank line as `Orphan` and lists it on the **next** construct before its leading comments (`AstBuilderInt.cpp:6744-6765`). **Gap 1:** if the orphans are simply let through, they are joined with `"\n"` into one `stmt.comment`, the blank line is lost, and C's `BLOCK` style turns two blocks into one `/* */`. **Gap 2:** an orphan run at the end of a block, with no next statement, goes to the scope's `trailing_comments` (`AstBuilderInt.cpp:6905-6930`). pssc never reads it, and the IR has nowhere to put it. The request's "trailing blocks" case needs this. Statement comments flow through `_translate_statement` (`:4660-4692`) → `BodyWalker.stmt` (`body_walker.py:436-455`). C++ has its own `stmt` copy (`cpp/lower_progseq.py:1108`). Current behaviour is pinned by `test_comment_propagation.py::test_a_detached_comment_does_not_propagate` (`:258`). |
| P4 root exports only | **Superseded on main** | `f37708c` deleted `targets/export_function.py` (`root_exports`), so the error the exports patch works around no longer exists. The API is now the **visible component tree** (`targets/op_api.py`): the root, and every instance not declared `private`/`protected`, reached through accessors (`<root>_<inst>(&ctx[, i])`). `export target function` "is accepted and changes nothing". A function that is not on the API is emitted `static inline` in C. Two instances of one type are covered (`_HIER` in `test_c_op_model.py`), but **different register apertures per instance** are not. The patch no longer applies. What the plugin needs now is to confirm that its composition model compiles and that its API is what it expects. |
| P5 prelude sees sources | OK | `driver.compile` (`driver.py:139-216`) calls `tgt.prelude(opts)` at `:187`. Sources are normalized later, inside `translate()` (`:113-115`). `opts.sources` exists only on the CLI path (`cli.py:140`). No built-in target overrides `prelude()` (`base.py:165`). Nothing deduplicates (`frontend.py:245-270`). Two more callers build a prelude themselves: `testing.generated_sections` (`testing/__init__.py:240`) and `dvflow/check.py:82-149`. |
| P6 `pssc.preludes` | OK (new) | Copy the discovery pattern of `pssc.targets` (`targets/__init__.py:42,259-335`: idempotent, never raises, all or nothing per entry point, `PSSC_NO_PLUGINS`) and of `pssc.styles` (`style.py:35,185-230`). `extension-stability.md` §1 lists the public entry-point groups. |
| P7 pssparser floor | OK | `FunctionAccess` (`ast2ir.py:4471,4473`) is first in pssparser **3.6.0** (commit `5c30580`, `CHANGELOG.md:47-53`). `pyproject.toml:26-27` still says `>=3.5`. Other newer APIs have not been audited. |

---

## 2. Rulings (phase 0)

Each ruling has a recommendation. The working default is the recommendation
until the user rules.

- **R-Q1 What a package-scope `const` is** [2]. *Recommend:* treat it exactly
  like `static const`. At package scope there is no instance, so the
  distinction has no meaning. Every linker-following fold accepts any `Const`
  field whose parent is a package. Component-scope non-static `const` is
  unchanged. It is per-instance, and that is a separate question that is not
  in scope.
  **Ruled 2026-11-06: as recommended.**
- **R-Q2 The linker is authoritative; name lookup is a fallback only** [2].
  *Recommend:* when pssparser resolved a reference to a declaration, pssc
  folds from **that declaration** and never runs `_resolve_enum_constant`.
  Name lookup remains only for references the linker left unresolved. If it
  then finds both an enum item and a constant, the result is a located
  `CompileError`, not a choice. Fix the falsy-0 `or` and the bare-name
  collision in `_fold_const_expr` the same way: it goes through the linker
  first, and an ambiguous bare name with no link is an error.
  **Ruled 2026-11-06: as recommended.**
- **R-Q3 Named constants in generated C** [3]. **Ruled 2026-11-06, wider
  than recommended:** op-model-c keeps every named PSS constant as a symbol
  **by default**, not only when a plugin asks. The consuming C compiler
  folds it if it wants to. The value is the user's to change in a header,
  so pssc must never decide anything from it. pssc generates the
  definitions with `#ifndef` guards holding the PSS value, which a project
  header or `-D` may override. The plugin gets this behaviour for free, and
  it chooses the spelling through the style policy and the constants header
  section (phase 3, D3a..D3e). The narrower recommendation (an opt-in hook
  only, and `--symbolic-const` deferred) is withdrawn.
- **R-Q4 What "symbolic" means for range analysis** [3]. A named constant
  is an unknown value of its declared type: the range of `width`/`signed`,
  `const=None`, exactly as for a variable. `_cmp_decided` may still decide a
  comparison that the declared range alone decides (`u8 > 300`), since that
  holds for every value the header can give. A constant with no declared
  type (a bare literal) is never symbolic. *Follows from R-Q3; stands unless
  reopened.*
- **R-Q5 How detached comments are opted into** [5]. *Recommend:* a compile
  option, `driver.compile(..., keep_detached_comments=False)`, and a CLI flag
  `--keep-detached-comments`. The default comes from a target class attribute
  `OpModelTarget.keep_detached_comments = False`, so the plugin turns it on
  for itself. This follows the `native_inheritance` pattern. It affects
  statement comments only. File-scope and declaration orphans are still
  dropped, as the request asks.
- **R-Q6 How a detached block is kept apart and placed** [5]. *Recommend:*
  (a) separate runs with an empty line inside `stmt.comment`.
  `comment_lines` renders an empty line as a break between comments (a new
  `/* */` in C, a blank `#`-free line in Python). (b) An orphan run at the
  end of a block becomes a **`StmtPass` carrying the comment**, with
  `metadata["comment_only"] = True`, appended to that block. `BodyWalker.stmt`
  (and the C++ copy) render the comment of a comment-only statement even
  though it lowers to no lines. Python still emits `pass` when that is the
  block's only statement. This needs no new ir-core kind. A new
  `StmtComment` would have to add a row to every emitter kind registry.
- **R-Q7 P4: option or nothing** [6]. *Recommend:* **no new option.** On
  main, a child instance's functions are public unless the instance or the
  function is `private`/`protected`, and a level-2 plugin already decides
  linkage per function (`linkage(fn, ctx)`). Phase 6 confirms this with the
  plugin's composition shape. Only if it shows a case that neither
  `private` nor the linkage hook can express, add
  `OpModelTarget.api_scope = "tree" | "root"`.
  **Ruled 2026-11-06: as recommended.** The requester declares the child
  instances `private` to keep them off the API. The `linkage` hook alone is
  NOT enough: it hides the functions but not the child accessors, which
  `op_api` decides separately. The reply says so.
- **R-Q8 Prelude dedup and the contributed-prelude contract** [4].
  *Recommend:* (a) `compile()` sets `opts.sources` (a list of `str`) before
  `prelude()`, and documents it; (b) the driver drops a prelude unit whose
  text is byte-identical to a source file's or to an earlier prelude
  unit's; (c) `pssc.preludes` entry points are callables
  `(target_name, opts) -> list[(name, text)]`, applied after the target's own
  prelude, for every target, honouring `PSSC_NO_PLUGINS`. pssc does not check
  "annotation-only". The guide says a contributed package is in every
  compile, so it should declare only annotations and types. A target opting
  into another target's prelude is not needed once (c) exists.

---

## 3. Phases

Each phase ends with:
- the unit suite (`direnv exec . ../python/bin/python -m pytest tests/unit > unit.log 2>&1`);
- the progseq suite, including goldens (`... -m pytest tests/progseq > progseq.log 2>&1`);
- an update to the status table above.

Commits are the user's (global rule). Each phase stops at "ready to commit".

### Phase 1: P7 packaging

1. `pyproject.toml`: `pssparser>=3.6`, and fix the comment (3.6 for
   `FunctionAccess`).
2. Audit: grep `src/` for every `pssparser.*` attribute pssc uses and check
   each against the v3.5.0 tag (`git -C packages/pssparser show
   v3.5.0:python/pssparser/ast.pyi`). Record anything else that is 3.6-only.
3. Check whether pssparser 3.6.0 is on PyPI. If it is not, the reply says so,
   since a pssc release is blocked on it.
4. Test: `tests/unit/test_packaging.py` (new or extended) parses
   `pyproject.toml` and asserts that the floor is at least the version that
   introduced each pinned API (a small table, `FunctionAccess → 3.6`), so the
   next new API forces a bump.

### Phase 2: P1, a package `const` folds as itself

Implementation (`ast2ir.py`):
1. One predicate, `_is_package_const(field)`: `Const`, and the parent is a
   package (or `Static`). Use it in `_linked_static_const`,
   `_linked_const_name`, `_rooted_constant` (R-Q1). Rename
   `_linked_static_const` → `_linked_package_const` if it reads better. Fix
   the stale comments at `:833` and `:904`.
2. `_translate_expr_ref` (`:5485-5503`): when the reference is linked to a
   field, go through `_linked_constant` and stop. Run `_resolve_enum_constant`
   only for an unlinked reference. Make the qualified path (`:5102-5113`)
   follow the same order (R-Q2).
3. `_fold_const_expr` (`:1754-1804`): look up the linked declaration first.
   Replace `a or b` with an explicit `is None`. When an unlinked bare name
   matches more than one package's constant, raise a located error.
4. Result: a non-static package constant now carries `ref`, `width` and
   `signed`, like a static one.

Golden risk: SV spells any constant that has a `ref` as a `localparam`. A
golden model that references a non-static package `const` will change in SV.
That is a legitimate change in generated output. Regenerate in the same
commit and review the diff (AGENTS.md). Check first with
`grep -rn "^\s*const " tests/progseq/data examples` to know in advance.

Tests: new `tests/unit/test_package_const_fold.py`, plus a matrix case.
- An unqualified reference to a non-static package `const` that shares a name
  with an enum item folds to the constant (the repro, generic names:
  `pkg { extend enum e_e { A_ON }; const int A_ON = 0; }`).
- A qualified reference gives the same.
- The same name in two imported packages: qualified references each get
  their own value. An unqualified reference is whatever the linker says, or
  a located error if the linker reports it ambiguous.
- A function local that shadows the name wins.
- A qualified constant with value **0** next to a same-named bare constant
  in another package (the falsy-0 defect).
- `static const` and non-static give identical IR (`==`) and identical `ref`.
- Executed, not only grepped: a matrix case in
  `tests/progseq/matrix/test_m_names.py` (`covers=` the package-const row)
  driving the early-return branch on all four targets with one trace.
- ~~SV: `test_sv_constants.py`~~ dropped (see the status note): the matrix case covers SV.

### Phase 3: P2, named constants stay symbolic in op-model-c

Sub-decisions (working defaults; reopen any of them before you start):
- **D3a Which constants.** Every `ExprConstant` in an operation body whose
  `ref` names a `static const` or a package `const` (R-Q1). Enum items are
  unchanged (already named). Literals are never symbolic. Anything that
  fixes layout or an address stays a number: register offsets and strides,
  layout-struct array sizes, template arguments, SZ2 widths, and loop
  bounds pssc unrolls. An address is pssc's (AGENTS.md "Where a register
  lives"); a header must not be able to move one.
- **D3b Spelling.** `CStylePolicy.constant_name(qname)`. The default is the
  PSS name, qualified with its package only on a clash: the same rule SV
  uses (`sv/constants.py`), so one model names its constants alike in both.
  The name is gathered in `c_names`, so a clash with a function or an
  enumerator is refused before any file is written.
- **D3c Definitions.** A new named header section, `constants`, in
  `header_sections()`, before the API types: `#ifndef N` / `#define N
  (<value>)` / `#endif`, the value spelled with a suffix wide enough for
  its declared type. Only constants the code references are emitted
  (record while rendering, as `SvConstants` does). A plugin that takes its
  macros from project headers drops or replaces the section with
  `targets/sections.py`. That needs no new hook.
- **D3d Use sites.** Every use is cast to the declared type,
  `((int32_t)(N))`. A `-D` or a project header may define `N` as any
  integer expression, so pssc cannot rely on how the macro is spelled.
  `IntSemantics` then widens, wraps and sign-extends it as it would a
  variable of that type (R-Q4).
- **D3e An escape hatch.** `--fold-constants` restores today's output. It
  costs one flag. It is what the differential tests compare against, and it
  gives a user whose build cannot take the header change a way back. The
  manifest records which form was emitted (`abi_settings()`), and gains a
  `constants` list (name, qname, value, type); adding a field needs no
  version bump.

Implementation:
1. `IntSemantics.is_symbolic(e) -> bool`, default `False`. `CIntSemantics`
   answers per D3a unless `--fold-constants` is set. C++ shares
   `CIntSemantics` but must keep folding: gate it on the C emitter (an
   attribute that `CBodyEmitter` sets), so no C++ golden moves. C++ named
   constants (`constexpr`) go to `future-projects.md`.
2. `_int_leaf` (`int_semantics.py:319`): a symbolic constant is
   `_Val(<use-site text>, *int_range(width, signed), const=None)`. `_fit`,
   unary, binary `_FOLD` and the shift shortcuts then treat it as a variable.
   Verify each one with a test, not by reading the code.
3. `CIntSemantics.convert_to` (`:1497`): no folding of a symbolic constant.
   `CBodyEmitter.expr_constant` (`:2452`) prints the D3d use site.
4. `CStylePolicy.constant_name` (a new policy member, `provisional`) and the
   `constants` section (`COpModelBackend.emit_constants`,
   `@overridable(provisional)`, with a contract docstring). Run
   `scripts/regen_override_surface.py`, then hand-edit
   `docs/override-surface.json`.
5. `c_names` gathers the constant names. `test_c_names.py` fails if it
   gathers a name the backend does not emit, so the two stay in step.
6. Check how C renders `match` with a constant case label: a cast macro is
   still an integer constant expression, but confirm that the emitter
   does not need the value, e.g. for a duplicate-label check.
7. **Goldens.** Every C golden that references a named constant changes.
   That is the intended output change: regenerate in the same commit with
   `PSSC_GOLDEN_REGEN=1`, review the snapshot diff, and say so in the
   commit message. C++, SV and Python goldens must not move.

Tests:
- **Differential, folded vs symbolic.** On the WB DMA model
  (`test_build_wb_dma_c.py`) and `test_op_model_behaviour_c.py`, build both
  forms and require an identical bus trace. That proves the default keeps
  behaviour while the text changes. The address tests must stay green,
  since D3a keeps offsets numeric.
- **One source, two headers.** Compile one generated `.c` with `-DOPT=0`
  and `-DOPT=1` (and once with a project header included before the
  generated one). Run each and check that the bus trace or return value
  changes as the model says. This is the request's central test. Use
  generic names in a new `tests/progseq/test_c_named_constants.py`.
- Comparison: `if (MODE != 0 && OPT == 0)` is not decided at generation
  time (no `(0)`/`(1)` in the C), as the two runs confirm.
- Narrowing: `bit[4] x = OPT;` with `-DOPT=0x1F` gives `x == 0xF`.
- `~OPT`, `OPT + 1`, `OPT << 2` in a 64-bit context, and a signed constant
  with `-D` giving a negative value: run each and compare with
  `matrix/ref8_7.py`.
- The declared range still decides what it alone decides (`u8 > 300`), with
  no `-Wtype-limits` warning under the matrix's `-Werror` flags.
- `#ifndef`: the default is used when nothing defines the macro, and the
  header's value wins when one does.
- Naming: two packages with one constant name are qualified, and a
  constant named like a function is refused by `c_names`.
- Mutation check: with `--fold-constants` the two-header test must fail,
  which shows it tests something.
- HAL fixture: the plugin drops the `constants` section and supplies its
  own header, the plugin's real setup. Its differential bus trace still
  passes.
- The matrix (`tests/progseq/matrix/`) runs symbolic by default. Any case
  that now differs is a defect to fix, not an xfail.

### Phase 4: P5 + P6, preludes

Implementation:
1. `driver.compile`: normalize `sources` to `list[str]` once, set
   `opts.sources` before `tgt.prelude(opts)`, and pass the same list to
   `translate`.
2. One function, `driver.prelude_units(tgt, opts) -> list[(name, text)]`: the
   target's prelude, then each `pssc.preludes` contribution, then dedup
   (R-Q8 b) against the sources' text. Use it in `compile`,
   `testing.generated_sections` and `dvflow/check.py`, so the three cannot
   disagree.
3. `pssc/preludes.py`: `PRELUDE_GROUP = "pssc.preludes"` and `discover()`,
   copying the `targets`/`styles` pattern (snapshot, all or nothing,
   `PluginError`, `PSSC_NO_PLUGINS`). The CLI reports load errors as it
   does for styles (`cli.py:328`).
4. `_parse_error` (`driver.py:74-86`) must still map a contributed unit's
   name to its text for diagnostics.

Tests:
- `tests/unit/test_prelude_sources.py`: a target whose `prelude()` records
  `opts.sources` sees the list for `compile([path])`, `compile(path)` and
  the CLI.
- A prelude unit identical to a passed source → one package, no duplicate
  error. A *modified* copy still gives the duplicate-declaration error
  (dedup is by content, never by name).
- `pssc.preludes`: add an entry point to `tests/plugins/pssc_fixture_plugin`
  contributing a generic annotation package. Then, in
  `test_plugin_integration.py` (marker `plugin`, installed into a temp
  prefix):
  - an annotated model compiles on `op-model-c` and `op-model-sv` without
    passing the library;
  - passing it explicitly as well still compiles (P5 dedup);
  - `PSSC_NO_PLUGINS=1` makes it `unknown type` again;
  - a broken contributor is reported and does not stop the others.
- `testing.generated_sections` and `dvflow/check.py` see the contribution
  (one test each).

### Phase 5: P3, detached comments in bodies

Implementation:
1. `ast_comments(node, keep_detached=False)`: with the flag, keep `Orphan`
   comments that come before a statement. Insert an empty line wherever the
   placement changes or a source-line gap appears between runs (R-Q6 a).
2. Thread the flag through `compile` → `translate(..., keep_detached_comments=)`
   → `AstToIrTranslator` → `_translate_statement` only (R-Q5). The default
   comes from `getattr(tgt, "keep_detached_comments", False)`, so a CLI flag
   or keyword overrides it.
3. End of block: read the enclosing scope's `getTrailing_comments()` and
   append a comment-only `StmtPass` (R-Q6 b). Do this for every scope that
   holds statements: function body, exec body, `if`/`else`/`else if`
   clauses (an `StmtIfChain` clause), loops, `match` arms, `repeat`.
4. `BodyWalker.stmt`, the C++ `stmt` copy, and the Python emitter render a
   comment-only statement as its comment alone. Python adds `pass` only if
   the block is otherwise empty. `comment_lines` handles the empty-line
   separator in all three styles.
5. Check `ir_walk`, `effect_seq` and `scan_write_only`: a comment-only
   `StmtPass` must change no analysis. Run `test_effect_seq.py`,
   `test_reg_read_form.py` and `test_hal_fixture.py` (AGENTS.md: the
   statement order is checked by the bus trace).

Tests, `tests/progseq/test_comment_propagation.py` (new
`test_detached_*` group) on a generic model with detached, consecutive and
trailing blocks around commented-out calls:
- Default off: output is byte-identical to today, and the existing
  `test_a_detached_comment_does_not_propagate` still holds.
- On: each block appears once, in source order, in C, C++, Python and SV.
  Two runs separated by a blank line give two C comments.
- A trailing block at the end of a function, of an `if` clause and of a
  loop body.
- The bus trace is unchanged on vs off (HAL fixture differential).
- File-scope and declaration orphans are still dropped with the flag on.
- `test_comment_ir.py`: a comment-only `StmtPass` carries
  `metadata["comment_only"]`, and two comment-only statements are equal
  under `==` only as `StmtPass` is today. Check that `annotations`/`comment`
  are `compare=False`, so goldens and equivalence tests do not move.
- `body_kinds` pre-screen: `StmtPass` is already a rendered kind on every
  target. Confirm there is no new row in `test_emitter_kind_registry.py`.

### Phase 6: P4, verify on main

1. Write a generic composition model: a root that instantiates two
   instances of one child type, each with its own register group bound by
   the root to a different aperture, plus a third child type with its own
   functions, some of them `private`. Run it with and without
   `export target function` on the children.
2. Tests (`tests/progseq/test_c_op_model.py`, `_HIER`-style):
   - It compiles on op-model-c, -py, -cpp, -sv.
   - Each instance's register access goes to its own aperture: an
     executed bus trace, not a grep. This is the case `_HIER` does not
     cover.
   - With and without child exports the output is byte-identical
     ("accepted and changes nothing").
   - A `private` child function is `static inline` and off the header.
3. With the children declared `private` (R-Q7): no child accessor in the
   header, child functions `static inline`, and the root still calls them.
   Check this on op-model-c and through the HAL fixture plugin.
4. The reply (phase 7) explains that the exports patch is superseded, what
   the API on main is, and how to make a child function non-public.

### Phase 7: docs, release notes, reply

Docs (every code block in the guide is quoted verbatim and checked by
`tests/unit/test_doc_examples.py`, so quote from the fixture plugins, never
inline):
- `docs/custom-generator-styles.md`:
  - a "Named constants" subsection: the default, `--fold-constants`,
    `CStylePolicy.constant_name`, and dropping the `constants` section,
    quoting the fixture;
  - a "Prelude" section: `prelude(opts)`, `opts.sources`, dedup, and the
    `pssc.preludes` group with the fixture's entry point.
- `docs/extension-stability.md` §1: the new entry-point group;
  `opts.sources` as a promised input to `prelude()`; `constant_name` and
  `emit_constants` as provisional.
- `docs/cli.md`: `--keep-detached-comments`, `--fold-constants`.
- `docs/op-model-manifest.md`: the `constants` list.
- `docs/design/pss-comment-propagation-plan.md`: an "as applied" note on
  the opt-in and the comment-only statement.
- `AGENTS.md`:
  - under "A constant keeps its name", one paragraph: a package `const`,
    static or not, folds from its declaration via the linker, and name
    lookup never overrides a link;
  - a paragraph: op-model-c keeps named constants symbolic (D3a..D3e), never
    an address, so `IntSemantics` must treat them as variables;
  - one line on `pssc.preludes`.
- `docs/release-notes.md`: P1 as a wrong-code fix (call out that SV now
  names non-static package constants); P2 as a **visible output change**
  for every op-model-c user, with `--fold-constants` as the way back;
  P3/P5/P6 as additions; P7 as packaging.
- `docs/design/future-projects.md`: C++ named constants (`constexpr`), and
  the C++ `body_emitter_cls` swap point.

Reply: `requests-reply.md` (repo root, untracked, like `requests.md`). For
each item, give the upstream name and how to switch to it:
- P1: fixed, no hook.
- P2: the default now; drop the patch. Spell names with `constant_name` and
  drop the `constants` section so the project's own feature headers supply the
  values.
- P3: `keep_detached_comments = True` on the target class.
- P4: superseded by the visibility-based API. Drop the patch and declare the
  child instances `private`.
- P5: `opts.sources`.
- P6: the `pssc.preludes` entry point.
- P7: the new floor, and whether pssparser 3.6 is on PyPI.

Include the mapping from the patches' names to the upstream names, so the
plugin can update the check for the hooks that it runs at start-up. The
reply is untracked and may use the requester's names. This plan and
everything shipped may not.

Final check before handing over: unit, progseq (goldens), compliance
(`tests/compliance`), and plugin-marked tests all green, with the counts
recorded in the status table.
