# C engine (zuspec-rt-eng) issues found by benchmarking

Found 2026-11-20 while timing `pssc test --engine c` against `--engine py`
(~100x faster on loop-heavy tests: ~100 ns vs ~11 us per loop iteration of
~40 bytecode steps). Engine source: `packages/zuspec-rt-eng`
(`share/rt/zbc_interp.c`, `share/rt/pssvm_host.c`); pssc's call site:
`src/pssc/testing/native.py`.

Regression test: `tests/unit/test_native_long_runs.py` (`pssc test` in a
child process, so a crash fails a case). `t_arith` (arithmetic loop) and
`t_calls` (one function call per iteration), checked against exact values.
Before the fixes: 3 failed, 1 passed. After: 4 passed.

## 1. Segfault on the debug image -- FIXED

`--engine c` crashed (exit 139, no report) once a test made 4800 calls.

Cause: C stack overflow. CALL ran the callee with `zsp_timebase_call`; when
the callee halted, `zsp_timebase_return` (rt-core) resumed the caller by
calling `prev->func` recursively. Each completed call left ~1.7 KB of C stack
live until the run ended (8 MB / 1744 B ~ 4810 calls). The bytecode frame
arena was reclaimed correctly; only the C stack leaked. `--no-debug` inlines
calls, so it never crashed. pyvm queues the callee (`ready_first`) instead,
so its stack never grows.

Fix (`zbc_interp.c`): a CALL marks its frame `sync_call`; a callee that
completes while its caller is in that CALL pops its frame without continuing
(`ZSP_THREAD_FLAGS_BLOCKED` held across `zsp_timebase_return`), and the CALL
`goto resume`s the caller in its own loop -- the same prologue the recursive
resume ran (return value, error stop). A callee that suspends (import yield,
debug stop) returns to the scheduler as before.
Test: rt-eng `tests/test_engine_call.py::test_many_sequential_calls_do_not_grow_the_c_stack`
(200k calls).

Follow-up (not done): the BLOCKED toggle stands in for a non-continuing
return in rt-core (`zsp_timebase_return` variant, or a trampoline in
`zsp_timebase_run`). rt-core is shared, so it was left alone.

## 2. Step limit -- FIXED

`ZBC_STEP_LIMIT` (10M steps per frame, `zbc_interp.h`) stopped a test after
~246k loop iterations (~20 ms). pyvm counts no steps, so the same test
passed on py and errored on c. Nothing on the pssc path set it.

Fix: `zbc_host_t.step_limit` (0: the default; `ZBC_STEP_UNLIMITED`: none),
set from `pssvm_load`'s `step_limit=N` option (0: no limit). pssc's
`native.ENGINE_OPTS = "step_limit=0"` runs tests with no limit, as on py;
runaway polls remain the sanitizers' job. The raw engine and the SV DPI
engine keep the 10M default (documented in `docs/simulation.rst`).
Test: rt-eng `tests/test_engine_safety.py::test_a_host_sets_the_step_limit`;
the ctypes `ZbcHost` mirror in rt-eng `tests/conftest.py` gained the field.

Not done: a `pssc test --max-steps N` honoured by both engines (pyvm would
need a step counter) -- a feature, not part of this fix.

## Verification

- rt-eng suite: 343 passed, 13 failed -- all 13 in `tests/test_events.py`
  (trace `uses` field differs between engines), failing before the fixes too.
- `tests/unit/test_native_long_runs.py`: 4 passed.
- pssc `tests/unit tests/pssunit`: 4136 passed, 2 failed. One was this note
  missing from `test_doc_references.py`'s live-notes allow-list (added).
  The other, `test_ir_walk.py::test_every_base_field_is_either_a_child_or_listed`,
  is unrelated: ir-core's `Base` gained an `anchor` field that
  `ir_walk.NON_CHILD_FIELDS` does not list yet.

## Status

- [x] 1: root cause, fix, regression tests
- [x] 2: root cause, fix, regression tests
- [x] full pssc unit + pssunit run
- Nothing committed (pssc or zuspec-rt-eng).
