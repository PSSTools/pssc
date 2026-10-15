# Future projects

Work found along the way and deliberately deferred. Each entry says what is
wrong or missing, why it matters, and where to start. Remove an entry when it
is done; record the commit in its place for a release or two.

Priority as of 2026-10-15 (user): a coherent PyPI release first
(`zuspec-pypi-release-plan.md`), refactoring after.

## Zuspec IR -> solver translation (zuspec-solver)

Context: `zuspec-pypi-release-plan.md` section 5. The shared walk,
`zuspec.solver.translate`, exists, and be-bc uses it (zuspec-solver 5aa53dd,
be-bc ae24268).

### Revive be-sw's IR -> C translator on a C-source sink

**User direction (2026-10-15):** other tools and flows will need Zuspec IR to C
translation, so revive this over time rather than delete it. It is not a
priority now.

- State: `zuspec.be.sw.scenario` (`constraint_c.py`, `emitter.py`) is dead.
  pssc never calls it; c-host uses `CGenerator` plus `sw_solve.py`. be-sw's
  end-to-end tests all skip, because `tests/unit/_pss_harness.py` imports
  `zuspec.fe.pss`, the old name of pssc.
- Its constraint emitter names fields by `ExprAttribute(self, name)`, not
  ir-core's `ExprRefField` slots, so it cannot take a problem from
  `collect_solve_problem`.
- Plan:
  1. Point the harness at `pssc` and see what still runs.
  2. Write a `CSourceSink` in zuspec-solver: each `expr_*` emits
     `dvs_expr_t eN = dvs_builder_expr_*(b, ...)` and returns `eN`.
  3. Have `CEmitter._emit_randomize` call `translate(problem, CSourceSink(...))`.
  4. Delete `constraint_c.py`.

  be-sw's bit-slice masked compare (`slice == const` -> `x & mask == c`) is a
  translation rule; it moves into the shared walk, not into the sink.

### Move pssc's `sw_solve.py` (c-host presolve) onto the shared walk

- `sw_solve.py` has its own name-based translator. It flattens rand arrays and
  arrays of structs and unrolls `foreach`. ir-core's `collect_solve_problem`
  refuses `foreach`, and `layout.py` gives an array a single leaf.
- So this needs ir-core to lay out fixed-size arrays one slot per element and
  to fill `ScSolveProblem.arrays`. bc shares that layout, so it is a bc change
  too. The walk already unrolls `foreach` over `problem.arrays`.
- **Defect meanwhile:** `_translate` returns `None` for anything it cannot
  translate, and the constraint is then dropped. c-host can bake in values
  that violate a constraint. A cheap stopgap is to treat any untranslated
  constraint as "cannot presolve this action" (zero-fill the whole action, as
  on UNSAT) instead of solving a weaker problem.

### `>>` on a signed operand: the copies disagree

be-bc's and pssc's tables send `BinOp.RShift` to `BIN_RSHIFT` (logical).
be-sw's sends it to `BIN_ASHR`, which is arithmetic in a signed context. That
was a deliberate change (be-sw dd10526, "Map `>>` in constraints to dv-solve's
arithmetic shift"), so be-sw is probably the side that is right. Settle
it from the LRM (8.7 operators) and fix the shared walk's `_BINOP`. Procedural
code on every target must agree with constraints.

### be-py's `ConstraintSystem` front door

be-py `solver/backend/native_backend.py` (`@zdc` `ConstraintSystem` ->
dv-solve) and zuspec-solver `ir_translator.py` (the same, aimed at the retired
libzsp) are duplicates. Move the first into zuspec-solver as a second front
door and delete the second. This is optional; the point is one copy.

### Unsat back to provenance

The way back from a solve currently stops at write-back (`writeback_slots`).
Mapping an unsat core or solver explanation to the IR constraints and their
source locations would be shared work. be-bc's `provenance.py` is ZBC-specific
and does not do it.

## bc stack

- **rt-eng's engine cannot load without a dv-solve link.** The engine C
  references `dvs_solver_*` unconditionally, but `build.build_shared_lib`
  links dv-solve only when its caller passes `extra_link_args`. It does not
  find the library itself, for example through the installed `dv_solve`
  package (be-sw's `scenario/solver_paths.py` does that). Before pssc runs
  the native engine for users, rt-eng should locate dv-solve on its own, or
  build a solver-less engine that refuses SOLVE.

## pssc

- **Dead SV activity solver path:** `targets/sv/analyze_activity.py` imports
  `zuspec.solver.chain_problem_builder`, which no longer exists. A blanket
  `except Exception` turns that into an empty plan. Fix it or delete it.
- **Dead DPI solver path in the SV target:** `ctx.uses_dpi_solver` is never
  set, so `emit_files` never copies `zsp_dpi_pkg.sv`. Its default path is
  also a source-tree path into zuspec-solver (`src/sv/`), which the
  pure-Python zuspec-solver no longer has. Delete it together with
  `analyze_activity`'s solver path.
- **Unreachable symbol refusals in `ast2ir.py`:** since pssparser 3.3, the
  parser reports PSS006/PSS018/PSS064 first, so pssc's own refusals never fire.
  They are kept as backstops. Decide whether to keep them, and add a test if
  they stay.

- **`pssc run`: a command that runs a model on bc.** 0.2.0 depends on
  zuspec-be-bc but has no command-line path to it (user, 2026-10-15: ship bc
  as a library for now). The glue is about 20 lines (parse, link, ast2ir,
  `PSSToScenarioPass`, `lower_module`, `run_model`). It is copied today in
  `tests/compliance/adapters/pssc_bc.py` and `scripts/bench_bc.py`. Put it
  in `src/pssc` once, have both use it, and expose it as `pssc run --root
  C::A --seed N`.
- **The op-model-py golden snapshots carry pssc's version** (its module
  docstring says "generated by pssc X.Y.Z"), so every version bump
  regenerates them (0.2.0 did). Normalize the version in `golden_util`'s
  comparison, or leave it out of the banner.

## Test rot found during the 0.2.0 wave

These fail the same way before and after the release changes, so they are
not release blockers.

- **be-sv:** 14 `tests/performance/` simulator tests fail with
  `TaskSetRunner.mkDataItem() requires a builder`, a dv-flow-mgr API change.
- **be-sw:** 6 failures (`tests/integration/rtl/test_debug_build.py`, among
  others) and 47 collection errors. The RTL benchmark and correctness tests
  import sibling modules (`from pipeline import ...`) that are not on the
  path. The PSS end-to-end tests skip on `zuspec.fe.pss` (see above).

## Release hygiene

- zuspec-ir-core's head commit (18f0065) is titled `XX`, as are be-fv's last
  two. Give the release commits real messages.
