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
be-sw's sends it to `BIN_ASHR`, which is arithmetic in a signed context. Settle
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

## pssc

- **Dead SV activity solver path:** `targets/sv/analyze_activity.py` imports
  `zuspec.solver.chain_problem_builder`, which no longer exists. A blanket
  `except Exception` turns that into an empty plan. Fix it or delete it.
- **Unreachable symbol refusals in `ast2ir.py`:** since pssparser 3.3, the
  parser reports PSS006/PSS018/PSS064 first, so pssc's own refusals never fire.
  They are kept as backstops. Decide whether to keep them, and add a test if
  they stay.

## Release hygiene

- zuspec-ir-core's head commit (18f0065) is titled `XX`, as are be-fv's last
  two. Give the release commits real messages.
