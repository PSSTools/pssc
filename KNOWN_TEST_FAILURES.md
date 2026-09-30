# Known test failures — zuspec-be-sv and zuspec-be-sw

Recorded 2026-09-01, during the pssc PyPI release work.

**None of this is packaging fallout.** It all predates the release work. It was
invisible because neither repo had CI until now, and the first run each got
could not even install `pytest` (see *How this surfaced*, below). The failures
are excluded in each repo's `pytest.ini`, with the same explanation inline, so
that the ~670 tests that *do* pass can gate CI. Every entry below is a bug to
fix, not a test to delete.

Baselines, measured on Python 3.13 with the full `default-dev` dep-set:

| repo | before excluding | after excluding |
|---|---|---|
| zuspec-be-sv | 180 passed, 14 failed, 4 skipped, 1 collection error | 180 passed, 4 skipped, 3 deselected |
| zuspec-be-sw | 544 passed, 47 failed, 36 skipped, 82 errors | 491 passed, 35 skipped |

---

## zuspec-be-sw

Two root causes account for essentially all of it.

### 1. `AbstractionFieldIR` has no `.datatype` — 53 failures

`src/zuspec/be/sw/passes/elaborate.py:123`:

```python
for field in dtype.fields:
    field_dtype = self._resolve_type(field.datatype, ctxt)
```

The loop assumes every entry in `dtype.fields` is an ordinary field. It is not.
An abstraction field (`IndexedRegFile`, `Counter`, …) elaborates to
`zuspec.ir.core.AbstractionFieldIR`, which is a *deliberately opaque* node —
`spec_type_name`, `field_name`, `field_index`, `py_cls`, `inst_kwargs`,
`ir_node`, and no `datatype`. So any component containing an abstraction field
raises `AttributeError` during elaboration.

**This is a gap in be-sw's abstraction-field support, not IR drift.** ir-core is
doing exactly what it documents; be-sw needs to dispatch on the node kind rather
than assume a single shape. Fixing this one loop should clear most of the 53.

Affected: `test_minicore_integration.py` (16), `test_backdoor_protocols.py` (8),
`test_topology_b.py` (7), and part of `test_rvcore_integration.py` and
`test_c_runtime_regfile.py`.

### 2. The `org.zuspec.example.*` fixture corpus is missing — 72 errors

```python
from org.zuspec.example.mls.riscv.rv_units import ALUUnit
```

No `org` package exists anywhere in the workspace, and none is declared by
`ivpm.yaml` or `pyproject.toml`. These tests were written against a corpus that
never shipped with the repo. Either vendor it, or declare where it comes from.

Affected: `test_rvcore_integration.py` (73 total), `test_rvcore_units_integration.py`
(10), `test_c_runtime_regfile.py` (11).

### 3. `zuspec.fe.pss` is absent — 19 skips, not failures

`_pss_harness.py` and `_sv_bridge_harness.py` use `importorskip`, so these
degrade to skips on their own and need no exclusion. Worth knowing that this
coverage is silently not running: `zuspec-fe-pss` is not in this workspace.

### Excluded files

`test_rvcore_integration.py`, `test_rvcore_units_integration.py`,
`test_c_runtime_regfile.py`, `test_minicore_integration.py`,
`test_backdoor_protocols.py`, `test_topology_b.py`, `test_pss_examples_e2e.py`,
`test_pss_sv_example_e2e.py`, `passes/rtl/test_debug_layer.py`

File-level rather than per-test: every one is broken in whole or in large part,
and per-test deselection would run to ~129 entries and rot faster than it helps.

---

## zuspec-be-sv

### 1. `zuspec.cli` does not exist — 1 collection error

`test_be_cli_plugin.py` imports it, via `src/zuspec/be/sv/cli_plugin.py`, which
does `from zuspec.cli.plugin import Plugin` **at module scope**. Nothing
declares that dependency and no such package is in this workspace.

`cli_plugin` is not reachable from `zuspec/be/sv/__init__.py`, so installed
wheels import fine — but it is a landmine of exactly the kind that produced the
truncated-wheel bugs earlier in this release: shipped source importing something
undeclared. Either declare `zuspec-cli` or guard the import.

### 2. Transactor API drift — 8 failures

`test_xtor_smoke.py` (8/8) fails with a `TypeError` from the `SVGenerator`
constructor plus assertion failures. The transactor API moved and its tests did
not follow.

### 3. Tests requiring a real simulator — 2 failures

`test_sim.py` (2/2) and the `[vlt]`/`[xsm]` parametrisations want verilator or
xsim on `PATH`. They cannot pass on a stock GitHub runner regardless of
correctness. These should be marked and skipped on absence, the way
`test_param_simulation.py` already does for iverilog.

### 4. Assorted assertion failures — 4

`test_smoke.py::test_smoke` (1/1), and 3 of 11 in `test_translation.py`
(`test_basic_counter`, `test_arithmetic_operations`, `test_multiple_outputs`).
The other 8 in `test_translation.py` still run and still gate.

### Not excluded — fixed properly instead

`test_fsm_to_sv.py`, `test_mmr_to_sv.py` and `test_protocol_to_sv.py` were also
failing collection, on `No module named 'zuspec.synth'`. Those tests were not
broken — the dependency was simply undeclared. `zuspec-synth` was added to
`ivpm.yaml`'s `default-dev` and all three pass, so they keep gating CI.

---

## How this surfaced

These repos had no CI at all before this release work. The workflow added during
it ran `ivpm update -a --py-pip` — and `-a` is `--anonymous-git`, *not* a
dep-set selector — so ivpm fell back to "the first dep-set in the file". For
be-py that was `default` (runtime deps only); for be-sv that dep-set was
outright *empty*. Nothing installed, so the runs died on
`pytest: No such file or directory` long before any test could fail.

Fixing that (`default-dep-set: default-dev`) is what made the suites run for the
first time, and what exposed everything above.

---

## Codegen gaps found while verifying the 0.1.0 release

Both were found by driving the **published wheels** (not the source tree) end
to end: `uvx pssc` → `pssc compile -t c-host` → `gcc` → `ld`. Both are
pre-existing. `git diff a847b31..v0.1.0` touches only `pyproject.toml`,
`.forgejo/workflows/docs.yml`, `KNOWN_TEST_FAILURES.md` and the deletion of the
unparseable `src/pssc/__build_num__.py` — no codegen file changed — so neither
of these is release fallout.

Everything up to the link step works: for

```pss
component pss_top {
    action A { rand bit[8] v; constraint { v > 10; v < 20; } }
    action Top { activity { do A; do A; } }
}
```

`c-host`, `c-embedded` and `sv-native` all emit files, and all 20 generated C
files plus the 16 runtime `.c` files shipped in the wheels compile cleanly
against wheel-supplied headers alone. So the packaging is sound — the headers,
the runtime sources and the DPI/solver libraries are all present and correct in
the published artifacts.

### 1. A compound root action emits a call to a body that is never defined

`main.c` calls `pss_top__Top_body(&root, &tb)`, but `pss_top__top.c` defines
only `pss_top__Top_init`. No translation unit defines `..._Top_body`, so the
link fails:

```
ld: obj/main.o: in function `pssc_run':
    undefined reference to `pss_top__Top_body'
```

Passing `--root-action pss_top::Top` explicitly does not change it. So an
activity-bodied root action currently generates C that compiles but cannot link.

### 2. A leaf action with `exec body` crashes the C generator

```pss
action A { rand bit[8] v; constraint { v > 10; v < 20; } exec body { } }
```

```
zuspec/be/sw/c_generator.py:909, in _generate_async_method
RuntimeError: Cannot generate async method 'body' for component 'pss_top::A':
method body is not available in datamodel. Ensure DataModelFactory.build() is
called with proper component classes and that the source code is accessible.
```

`pssc` reports this as `internal error` and exits 2. The async analyzer that
runs just before it prints `✓ pss_top::A.body` under "Convertible functions",
so the analysis and the generator disagree about whether the body is available.

The message names `DataModelFactory.build()` and "source code is accessible",
which reads like a path written for the `@zdc` Python frontend, where the body
is recovered by introspecting Python source. Coming from a `.pss` file through
pssparser there is no Python source to introspect — which would explain both
this and (1), and would make them one gap rather than two.

---

## pssc unit, pssparser R4 drift (2026-09-30)

`tests/unit` went from 11 failures to **151** when pssc was first run against
pssparser's symbol-resolution batches (R1–R4, 09-23 to 09-28). pssparser
recorded every AST change it made, with the pssc edit each one needs, in its
`docs/design/cross-repo-followups.md` (X-5, X-8, X-10, X-14 to X-18). None of
them was a pssparser bug. Tracked by `docs/design/activity-p0-plan.md` §1.

Fixed in P0 commit 1 (housekeeping):

| Cause | Tests | Fix |
|---|---|---|
| `print`/`message` without `import std_pkg::*;` (package visibility, LRM 18.1, now enforced) | 71 | fixtures import `std_pkg` |
| `ConstraintBlock.getName()` is an `ExprId` (X-10); named constraints were keyed by an AST object | 42 | `ast2ir` reads `.getId()` |
| Covergroup cross items are `ExprRefName` (X-5, X-18); names came out as AST objects, so 0 hits sampled | 6 | `_id_name` unwraps it |
| Redeclaring the built-in `initial`/`instance_id` is a duplicate declaration | 2 | tests now assert the located error |
| `export target function f;` of an undeclared `f` is an error | 1 | test declares `f` |
| `tx::send_pkt` (an *instance* as a type qualifier) in `tests/patterns/producer_consumer.pss` | 1 | fixture uses `tx_c::`. pssparser accepting it is reported upstream |

Fixed in P0 commit 2: the 28 `bind`/`schedule`/`unique` crashes (X-5).

Remaining, with owners:

| Tests | Cause | Owner |
|---|---|---|
| `test_doc_references.py::test_every_doc_reference_resolves[…]` ×3 | cite `docs/op-model-py-async-plan.md` and `docs/op-model-export-design.md`, which were never committed | op-model work |
| `test_doc_references.py::test_the_archived_notes_say_they_are_archived` | `op-model-output-defects.md`, `sv-op-model-inheritance{,-plan}.md` have no banner and are not on the allow-list | op-model work |

Also pre-existing on `HEAD` (confirmed on a clean worktree), in `tests/progseq`,
same drift, not activity work. Owner: op-model work.

| Tests | Cause |
|---|---|
| `test_c_body_lowering.py::test_a_declared_builtin_with_no_c_rendering_is_rejected` | fixture calls `urandom()` without `import std_pkg::*;` |
| `test_c_imports.py::test_a_component_scope_import_is_refused_rather_than_mislowered`, `::test_the_refusal_names_imports` | pssparser now rejects a component-scope `import target function` itself (20.4), before pssc's refusal is reached |
| `test_call_legality.py` ×5 | `std_pkg.pss` now declares more functions (math, file, `error`/`fatal`); the tier tables and two tests' expectations lag it |

`tests/sim/sv` (Verilator; `-k "not mti"`) had the same `std_pkg` fixture gap:
25 failed on `HEAD`. With the sweep applied (P0 commit 8), 13 remain, all in
`test_sv_pure_e2e.py`, and all pre-existing. On a clean `HEAD` worktree
with the same sweep, that file fails 19 of 20. The cause is that once a model imports
`std_pkg`, the sv-pure package declares fields of `std_pkg` types
(`rand alloc_access_mode_e access;`) without declaring the types
(`%Error: Can't find typedef/interface: 'alloc_access_mode_e'`). Owner: SV
target (sv-pure). `test_sv_pure_constraints_are_enforced` ("expected fatal on unsat")
is the same build failure.

