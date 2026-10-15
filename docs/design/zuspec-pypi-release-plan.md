# zuspec packages on PyPI: what a release needs (2026-10-15)

Survey of the eleven `zuspec-*` repos under `packages/`, against PyPI and
against what pssc `main` imports today. Follows the 0.1.0 release of
2026-09-01. The mechanism from that release still holds: the shared
`zuspec/zuspec-release/.../zuspec-pybuild.yml` workflow, a `v*` tag as the only
trigger, GitHub as the publishing forge (release-authority default), and an
org-wide `PYPI_API_TOKEN` that every zuspec repo can see. A first tag can
therefore create a new PyPI project, and no per-repo secrets are needed.

## Where each package stands

| package | PyPI | repo version | since last tag | CI | wheel audit | needed by `uvx pssc` |
|---|---|---|---|---|---|---|
| zuspec-ir-core | 0.1.0 | 0.1.0 | 19 commits (HEAD msg `XX`) | GH (pybuild) + Forgejo | ok (40/40) | **yes, a newer one** |
| zuspec-be-py | 0.1.0 | 0.1.0 | 1 | GH only | ok (126/126) | yes |
| zuspec-be-sv | 0.1.0 | 0.1.0 | 2 | GH + Forgejo | `share/sv/*.sv{,h}` not shipped | yes |
| zuspec-be-sw | 0.1.0 | 0.1.0 | 6 | GH + Forgejo | `scenario/cbridge/zsp_bridge.{c,h}` not shipped | yes |
| zuspec-dataclasses | 0.1.0 | 0.1.0 | 0 | GH + Forgejo | ok | test extra only |
| pssc | 0.1.0 | 0.1.0 | 76 | Forgejo | — | — |
| zuspec-be-bc | **none** | 0.0.1 | never tagged | **none** | ok (41/41) | no (tests, `scripts/bench_bc.py`) |
| zuspec-rt-core | **none** | 0.0.1 | never tagged | **none** | ok (C as package data) | no (via be-bc) |
| zuspec-rt-eng | **none** | 0.0.1 | never tagged | **none** | ok (C as package data) | no (be-bc tests) |
| zuspec-solver | **none** | 0.0.1 | never tagged | **none** | **native lib not built, 45 C/H/SV files not shipped** | lazily, by the `sv` activity path |
| zuspec-be-fv | **none** | 0.1.0 | never tagged (HEAD `XX`, May) | **none** | ok | no |
| zuspec-synth | **none** | 0.1.0 | never tagged | **none** | ok | no (lazy, optional) |

All six unreleased names are free on PyPI (HTTP 404), so they carry none of
the run-id-stamped junk versions that forced the 0.1.0 bump last time.
`zuspec-be-sw` does have `0.1.0.<runid>rc0` pre-releases that sort above 0.1.0.
They only matter under `--pre`, and the next be-sw version is above them
anyway.

## 1. The released set needs a coordinated 0.2.0 wave (blocking for pssc)

pssc `main` cannot run on what PyPI has. It reads IR that ir-core 0.1.0 does
not have: `ExprConstant.ref/width/signed`, `DataTypeArray.size_ref`, the
`FlowKind`/`JoinKind` enums, and the action/component-tree layout. be-py, be-sv
and be-sw have commits since 0.1.0 that depend on the same ir-core changes.
pssc's `dependencies` carry **no version floors**, so a `pssc 0.2.0` published
today would resolve to ir-core 0.1.0 and break on import or on first compile.

Order, since each step needs the previous one on PyPI:

1. **ir-core 0.2.0.** Give HEAD a real commit message first (two `XX` commits
   since the tag). Bump `BASE`, tag `v0.2.0`.
2. **be-py, be-sv, be-sw 0.2.0**, each with `zuspec-ir-core>=0.2.0`.
   be-sw also has a lazy `dv_solve` import (the dv-solve public-API commits),
   which needs a declaration or an extra.
3. **dataclasses 0.2.0**, floors bumped. It has no code changes, but its
   floors must allow the new be-py.
4. **pssc 0.2.0** with floors on every zuspec dependency and on `dv-solve`
   (0.2.3 is on PyPI now). Verify with a clean `uvx --refresh pssc` and the
   wheel-completeness audit before tagging.

Fix the two shipping gaps on the way:

- be-sv's `pyproject.toml` still uses a hard-coded `packages = [...]` list and
  has no `package-data`, so `share/sv/zuspec.sv` and `zuspec_component.svh` are
  not in the wheel. No Python in be-sv reads them today. Either ship them or
  delete them.
- be-sw's `package-data` covers `share/**` only. `scenario/cbridge/zsp_bridge.{c,h}`
  is not shipped; check whether anything compiles it.

### 1a. pssc `main` vs pssparser 3.2/3.3 (blocking, and already live)

pssparser 3.2.0 and 3.3.0 are on PyPI and both contain `b1bef8e`
("Downstream requests: pssc P1/P2/Q1-Q4"). Against a build of that source, 11
pssc unit tests fail. Against `6a7b2d0` (the last commit before it), all 132
tests in the affected files pass. This was verified on 2026-10-15 by building
both. The causes:

- **pssparser now rejects a reference through a component instance**
  (`'cpu' is a component instance, not a type or package; did you mean
  'cpu_c'?`), which pssc's own pattern models use:
  `tests/patterns/{nested_components,parallel_resources,abstract_ac...}.pss`,
  plus inline models in `test_flow_kind.py` and `test_lower_actions.py`.
  The LRM settles which side is right. If pssparser is right, fix the models.
- **Symbol diagnostics moved into pssparser** (Q2-Q4): a self-activating
  symbol, `with` on a symbol, and a value passed for a handle parameter are now
  `ParseException`s at link time. pssc's `test_activity_symbols.py` expects
  pssc's own located refusal, so update the tests to the parser's diagnostics.
- `test_constraint_stmt_registry.py`: the untranslated-statement record now
  carries `'line 1: '` where the test expects `''`.

**Resolved in pssc (2026-10-15):** the models and tests were brought in line
with the parser: type qualifiers instead of instance qualifiers, the parser's
symbol diagnostics, and `dist` located. pssc's full default suite and
compliance pass against pssparser `7be2d1e`, and again against `57c6b14`
(origin/master, which contains `v3.3.0`): 3644 passed / 858 compliance passed.
pssc now declares `pssparser>=3.3`. Building pssparser from source now needs
rapidyaml v0.16.0 under `packages/rapidyaml/` and a pyastbuilder with
`gen-reflect` (0.0.8). pssc's own symbol refusals in `ast2ir.py`
(`activates itself`, `not an action handle`, `takes no 'with'`) are now
backstops the parser always pre-empts.

## 2. Latent import breakage in released packages (fix in the same wave)

The undeclared-import audit (module-scope third-party imports compared with
declared `dependencies`) finds:

- **be-sv:** `passes/__init__.py` imports `pipeline_to_sv`/`sv_emit`, which
  import `zuspec.synth` at module scope. `import zuspec.be.sv.passes` therefore
  fails on a clean install of the published 0.1.0. pssc never imports it, which
  is why `uvx pssc` passed. Make it lazy, or move it behind a `synth` extra.
- **be-sv and synth:** `cli_plugin.py` imports `zuspec.cli` at module scope and
  is registered as a `zuspec.ext` entry point. `zuspec-cli` exists on PyPI only
  as a run-id dev build (`0.0.1.13321310739`), and its repo is not in this tree.
  Declare it as a `cli` extra and keep the plugin import-safe, or drop the entry
  point until zuspec-cli is released.
- **be-py:** `rt/pipeline_rt.py` imports `zuspec.dataclasses.pipeline_ns` at
  module scope, which conflicts with the "be-py does not need dataclasses" end
  state. It only fires if `pipeline_rt` is imported, but it should be lazy like
  the other back-edges.

## 3. First releases: the bc stack (be-bc, rt-core, rt-eng)

All three are pure-Python `py3-none-any` wheels. rt-core and rt-eng ship C
sources as package data, and rt-eng compiles them at run time. Wheels build
complete. Needed:

- **CI pair** in each repo: the ~15-line `.github/workflows/ci.yml` caller of
  `zuspec-pybuild.yml` (copy be-py's, with `version_dir` changed) plus the
  `.forgejo/workflows/` counterpart (rule 1: the pair is the unit).
- **`__version__.py` + `setup.py`/`dynamic = ["version"]`**, matching the
  others, so the workflow can stamp `SUFFIX`. Right now each has a literal
  `version = "0.0.1"`.
- **Dependencies, from the audit:**
  - be-bc declares `zuspec-solver`. Since be-bc ae24268 it really uses it: its
    constraints lower through `zuspec.solver.translate`. Keep the dependency.
    zuspec-solver must ship first, pure Python (section 5, item 5), so that
    dataclasses and be-sv drop out of be-bc's closure.
  - be-bc imports `dv_solve` lazily in four places, but that is the solve path
    every activation takes. Declare `dv-solve>=0.2.3`.
  - be-bc's `format/emit_c.py` imports `zuspec.rt.core` lazily. Declare it, or
    add a `native` extra together with rt-eng.
- **README for be-bc** (it has none; PyPI's long description will be empty).
- `ir-core>=0.2.0` floor (the activity/tree IR is all post-0.1.0).
- Version: start at **0.1.0** (names are clean).
- Release order: rt-core → rt-eng → be-bc (after ir-core 0.2.0).

**Decision (user, 2026-10-15): pssc 0.2.0 includes the bc backend.** The bc
stack becomes a pssc dependency (be-bc, and zuspec-solver once it is the
translation layer in section 5), so it must be on PyPI before pssc 0.2.0.
Settled (user, 2026-10-15): rt-core and rt-eng are **hard** dependencies, not an
extra.

## 4. First releases: be-fv and synth

**Decision (user, 2026-10-15): release both.**

- **synth:** declares dataclasses, ir-core, dv-flow-mgr, pydantic and PyYAML
  correctly. Needs CI, the `__version__.py` pattern, and the `zuspec.cli`
  question above. Lazy imports of be-sv and `zuspec.fe.sv` go behind extras.
- **be-fv:** declares only `z3-solver`, but lazily imports dataclasses, ir-core
  and synth for every non-trivial entry. Declare them, or add extras. Add a
  `license` field. Its last two commits are titled `XX`, so give the release
  commit a real message.

## 5. zuspec-solver: the pure-Python bridge between Zuspec IR and solvers

**Direction (user, 2026-10-15):** zuspec-solver holds no native code. Its
question is whether it should own the translation between Zuspec IR and an
external solver (dv-solve). The survey says yes: the translation exists five
times today, across four packages.

### What translates constraints today

| where | input | output | coverage |
|---|---|---|---|
| be-bc `lower/constraints.py` (1020 lines) | ir-core `ScSolveProblem` (slots) | dv-solve Python builder -> blob + slot write-back map | the most complete: casts, `in`/ranges, if-then-else, foreach, soft/dist refusal, calls, traversed refs, interval folding |
| be-sw `scenario/constraint_c.py` (329) | the same `ScSolveProblem` | **C source** calling `dvs_builder_*` | no casts, no foreach, no ite; its docstring says it "mirrors zuspec-solver/ir_translator.py's op mapping" |
| pssc `targets/sw_solve.py` (377) | raw constraint `Stmt`s, name-based | dv-solve Python builder | redoes field collection and path resolution that ir-core's `ConstraintCollect` already does; no casts, no compare/bool nodes |
| be-py `solver/backend/native_backend.py` (437) | be-py `ConstraintSystem` | dv-solve Python builder | the `@zdc` path |
| zuspec-solver `ir_translator.py` (459) | the same `ConstraintSystem` | **libzsp** builder | a duplicate of the be-py one, aimed at the retired native solver |

Problem collection itself (IR -> `ScSolveProblem`) is already shared:
ir-core `xf/pss_lower/constraints.py`. Only the step after it is duplicated.

The cost is already visible. Each copy has its own operator tables and its own
gaps, and the same model solves differently depending on the backend: be-bc
handles a cast in a constraint and be-sw does not. Any new operator (the recent
`>>` -> `BIN_ASHR` and literal-type work) has to be made N times, and the
copies drift.

### Also found

- **pssc's SV activity path is dead.** `targets/sv/analyze_activity.py` imports
  `zuspec.solver.chain_problem_builder`, which no longer exists. A blanket
  `except Exception` turns the failure into an empty plan.
- **Nothing outside zuspec-solver uses its Python modules.** No other repo
  imports icl, scheduling_graph, structural/stream solvers, partitioner,
  buffer_inference, state_graph, sv_randomizer_gen or the bench harnesses.
  be-py's registry only shares the `zuspec.solver.backend` entry-point group
  name.
- zuspec-solver's `builder.py`/`ctx.py`/`problem.py`/`lib.py` are the ctypes
  wrappers that dv-solve's `dv_solve.builder`/`ctx` were forked from. They are
  duplicates now.

### Proposed shape of zuspec-solver 0.1.0

Pure Python, `py3-none-any`, depending on `zuspec-ir-core` and `dv-solve`, with
no dataclasses or be-sv dependency.

1. **`ScSolveProblem` -> solver problem, written once, with pluggable sinks.**
   Move be-bc's `_Translator`, the complete one, here. Split its walk from its
   output: a *builder sink* (dv-solve Python builder -> blob, used by be-bc and
   pssc's presolve) and a *C-source sink* (`dvs_builder_*` text, used by be-sw).
   One walk means one coverage and one refusal list. A construct the solver
   cannot take is refused with its provenance, never dropped.
2. **The way back:** the var <-> slot map, solution write-back, and mapping an
   unsat result or solver explanation back to IR provenance (be-bc's
   `provenance.py`/`orchestration.py` pieces that are not bc-specific).
3. **pssc `sw_solve.py`** collects through ir-core `ConstraintCollect` and calls
   (1), instead of its own name-based translator.
4. **be-py `native_backend.py`** (`ConstraintSystem` -> dv-solve) moves here as
   the second front door, and `ir_translator.py` (-> libzsp) is deleted. Moving
   it is optional; the point is one copy.
5. **Delete:** `src/c`, `CMakeLists.txt`, the ctypes wrappers, the DPI/SV
   randomizer, the bench harnesses, and the unused analysis modules. Move to
   be-sv or a branch anything you want to keep. Fix or delete pssc's dead
   `analyze_activity` solver path.

The run-time drivers stay where they are, because they run a problem rather
than translate one: be-bc's interpreter calls `SolveCtx`, and rt-eng's
`zbc_solver.c` is its C mirror.

### Progress (2026-10-15)

- **(1) done for be-bc.** The walk is `zuspec.solver.translate` (`walk.py`,
  `ProblemTranslator`), writing through a `Sink` protocol: the subset of
  dv-solve's growable builder that it calls, so the builder is the sink as it
  stands. `build_solve_blob` and `translate(problem, sink)` are the front doors;
  refusals are `TranslateError`. be-bc's `lower/constraints.py` is a wrapper
  that re-raises a refusal as `LoweringError`. Its 62 translator tests moved to
  zuspec-solver `tests/translate/`, plus a recording-sink test that holds the
  walk to the protocol. zuspec-solver's `pyproject.toml` now lists the
  subpackage and `dv-solve`.
- **(2), the way back:** `writeback_slots`/`slot_of` moved with the walk. be-bc's
  `provenance.py` is bc's own (it builds a ZBC `ProvTable`), so nothing else in
  it is shared.
- **be-sw's C emitter is dead code.** pssc never calls
  `zuspec.be.sw.scenario` (c-host uses `CGenerator` plus `sw_solve.py`), and
  be-sw's end-to-end tests all skip: `_pss_harness.py` imports
  `zuspec.fe.pss`, the old name of pssc. Its emitter also names fields by
  `ExprAttribute`, not ir-core's `ExprRefField` slots, so it could not take an
  ir-core-collected problem anyway. **Decision (user, 2026-10-15): revive it
  over time**, because other flows will need IR to C; it does not block the
  release.
- **`sw_solve.py` cannot go onto `ConstraintCollect` yet.** It flattens rand
  arrays and arrays of structs and unrolls `foreach`. ir-core's collect refuses
  `foreach` ("not yet wired"), and `layout.py` gives an array one leaf. So the
  switch needs ir-core to flatten arrays in the slot layout first, and bc shares
  that layout. Meanwhile, `sw_solve` **silently drops** any constraint it cannot
  translate (`_translate` returns `None`), so c-host can bake in values that
  violate a constraint.
- **The copies already disagree on `>>`.** be-bc's and pssc's tables send
  `BinOp.RShift` to `BIN_RSHIFT` (logical); be-sw's sends it to `BIN_ASHR`.
  The LRM settles which is right for a signed operand. Once there is one walk,
  there is one answer.

**Release first, refactor after (user, 2026-10-15).** For 0.2.0, only
zuspec-solver's item (5) is release work: it must ship pure Python. Everything
else here, from be-sw onward, is in `future-projects.md`.

Original order: (1) and (2), with be-bc switched over and the bc suite and corpus
unchanged. Then be-sw on the C sink, checked by the golden snapshots and the
c-host behaviour tests. Then pssc's presolve. This is pre-release work for
pssc 0.2.0, since bc and be-sw would both depend on it.

## Checklist per release (from the 0.1.0 experience)

- Build the wheel and compare `.py` and package-data counts against `src/`
  (truncated wheels).
- Run the undeclared-import audit over `src/**/*.py`, comparing module-scope
  imports with declared deps.
- Install into a clean venv with `uv pip install --refresh` (uv's wheel cache
  serves stale wheels of the same filename) and import every subpackage, not
  just the top level.
- Tag with `vX.Y.Z` matching `BASE`. The shared workflow fails a mismatch.
- `git-sync release-authority zuspec/<repo>` must say `github`. Not available
  on this machine; ask on the host.

## Decisions

1. Release synth and be-fv: **yes** (2026-10-15).
2. zuspec-solver: **pure Python, no native code**; proposed role in section 5,
   awaiting review.
3. pssc 0.2.0 **includes the bc backend** (2026-10-15), with rt-core and rt-eng
   as hard dependencies.
