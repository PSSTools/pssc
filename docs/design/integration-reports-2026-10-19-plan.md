# Integration reports of 2026-10-19 — triage and plan

Three reports came from a downstream integration of a packaged pssc release
("1.1.0" of the packaging, observed 2026-10-19). As with the D1–D9 report,
this file does not name the reporter or their IP. Their example is called
"the four-aperture model" here.

## Status (updated 2026-10-19)

| Step | State |
|------|-------|
| §3 read-only runtime install | **DONE (uncommitted).** `op_model._install_copy` writes to a temp file and `os.replace`s it in, so it no longer writes through the old file. `OpModelTarget.run` turns an `OSError` into `CompileError("cannot write the generated files (<path>: <reason>)")`. Tests: `test_recompile_same_output_dir.py`, which covers a 0444 stale copy on all four targets, the shipped source left untouched, and an unwritable directory. 5 of its 6 tests fail without the fix. |
| §1 register-group regressions | **DONE (uncommitted).** Three matrix cases in `test_m_registers.py`, which pass on c/cpp/py/sv: two groups at one handle; four groups bound out of order, one with a run-time-indexed register array; a sub-component's group beside the root's. |
| §2 literal sizing | **DONE (uncommitted).** The fix is `1971c28` (2026-10-16), which is **only on the local `construct-test-audit` branch and not pushed**. C/C++ already suffix literals (`4294967232u`, `...ull`). New matrix case "masks with bit 31 set, wider than 32 bits, and negative" (4 targets, executed). New `test_no_unsized_decimal_outside_int_reaches_sv`, which fails on the old rendering. §2.2 ruled no change (below). |
| §0 release + reply | open: needs the branch merged and pushed, then a release |

Original triage, against HEAD `b4d12ee`:

| # | Report | Finding |
|---|--------|---------|
| 1 | C uses the last `set_handle` for every group | Fixed by `a93c14a` (2026-10-07, on `main`). |
| 2 | SV emits `4294967232` for a 32-bit mask | Fixed by `1971c28` (local branch only). |
| 3 | Second compile fails on a read-only runtime copy | `41f617d` fixed only NEW copies; a stale read-only copy still failed, rc 2. Fixed now. |

**§2.2 decision.** LRM Table 21 makes an unsized decimal `int[N]`, where N is
the minimum width and at least 32, so `4294967232` is `int[33]` and
`33'sd4294967232` is the correct type. Assigning it to a `bit[32]` truncates,
as 8.7.2 says. Verilator's `WIDTHTRUNC` there is a lint warning. It is the
same class as the ~700 `WIDTHEXPAND`/`WIDTHTRUNC` warnings that the matrix's
256 SV cases raise at implicit conversions in assignments and arguments. Those
are legal SV and are the PSS semantics. Making the SV lint-clean under
Verilator `WIDTH` would mean an explicit cast at every implicit conversion.
That is a separate project, not this report, and is recorded here rather than
started. The reported VCS `DCTL` is a different, compile-time warning about an
unsized decimal outside `int`, and the new structural test rules it out.
(Measured with a throwaway script that lints every matrix case with
`verilator --lint-only`. 24 of the warnings involve a literal, and all of
those literals are sized.)

## 0. The real fix for the reporter is a release

Reports 1 and 2 are already fixed in the tree. What the reporter needs is a
pssc release that contains `a93c14a` and the literal-typing work, picked up by
their packaging. The PyPI wave of 2026-10-15 (see memory `zuspec-pypi-wave2`)
was committed locally but not pushed or tagged. Decide whether this fix goes
out with that wave or in a follow-up 0.2.x.

**Reply to send:** D1 was fixed on 2026-10-07 and the fix ships in <version>.
The literal sizing ships in the same version. Report 3 is fixed in <version>.
Until then, their `chmod -R u+w` workaround is correct.

## Done work, in brief

- **§1** asked for: an alias (two groups at one handle), a run-time-indexed
  array in a multi-group root, a nested group, and four groups bound out of
  order. `test_m_library.py` "register arrays with a run-time index" already
  covered an indexed array of groups inside ONE group. The new cases add the
  multi-handle shapes.
- **§2.4 (C/C++)**: no change needed. `CIntSemantics` already suffixes:
  `x & 4294967232u`, `x & 18446744073709551552ull`.
- **§3**: the plan's separate `_write_out` helper was not needed. Catching
  `OSError` once in `OpModelTarget.run` (around `emit` and the manifest)
  covers every writer in the family. A *generated* file the user has made
  read-only still fails, but now with that diagnostic. Only runtime copies are
  replaced through a temp file.

## Not done / follow-ups

- The release and the reply (§0).
- Verilator `WIDTH` lint cleanliness of op-model-sv: a separate project if it
  is ever wanted (see the §2.2 decision).
- Environment: `packages/dv-solve/build/lib/libdv_solve.so` is stale (missing
  `dvs_builder_expr_const_wide`), and the bc tests that solve fail until it is
  rebuilt. This is unrelated to these reports.
