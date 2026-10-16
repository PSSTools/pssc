# Construct test audit and plan: data types, expressions, functions, procedural statements

Status: **in progress** (started 2026-10-16, tree `394f75d`). Rulings R1–R3,
R4, R5, R9 decided (§7). Progress is logged in §8.

**Where it stands (2026-10-16):** P0.2, P0.3 done; P1 done but for three
items carried forward -- string METHODS on py/sv (E1, to P3 strings: the
call gate classifies by name and cannot yet type a receiver), W18 (to P2:
parameter direction at the IR), W24 (a Verilator limitation; declaration-site
workaround open). P2 started (`tests/unit/ir/`, 78 tests). The construct
matrix holds 104 cases (was 63) with 5 strict xfails (was 2); every other W
and E row of §2 is fixed and its case passes on all four targets, or is a
tested located refusal. Nothing is committed.

## 0. What is in this directory

| File | What it holds |
|---|---|
| `taxonomy.md` | Coarse construct IDs (DT-, EX-, FN-, ST-, EXEC-, LIB-) shared by every file here. |
| `01-lrm-inventory.md` | **LRM inventory**: 596 sub-constructs under 75 coarse IDs. Each gives its clause, the rule a test must exercise, edge cases (including "Neg" diagnostics), and where it is legal. §7 lists 20 LRM gaps and contradictions. §8 lists **100 interaction patterns** (IP-1..IP-100). |
| `02-parse-ir-tests.md` | What pssc tests prove about PSS → pssparser → ast2ir → IR. ast2ir refusals vs **silent drops** (from probing ~110 snippets). Hollow tests. pssparser's own coverage. |
| `03-c-cpp-tests.md` | op-model-c / op-model-cpp (and the older c-host family). Per-file catalog, construct-matrix status, what the emitters handle, refuse, crash on or **miscompile** (from probing ~75 models). |
| `04-sv-py-tests.md` | op-model-sv / op-model-py (and sv-native/sv-oo/sv-pure). Same structure; silently-wrong list S1–S13. |
| `05-compliance-corpus.md` | The pss-corpus executable tier: every test's constructs and its verdict on bc, py and sv. Corpus holes. What a C/C++ runner would need. The construct-matrix mechanism. |
| `00-plan.md` | This file: consolidated findings (§1–§3), test strategy (§4), phased plan (§5), construct → planned-test map (§6), rulings needed (§7). |

The two probes (02 §4, 03 §3.5) were ad-hoc scripts that are not checked in.
Every finding they produced states the PSS that reproduces it. Turning those
snippets into checked-in tests is phase P1 below.

---

## 1. Headline findings

1. **Only a small part of the suite proves semantics.** Generated code is
   executed with a value check in only these places: the construct matrix
   (`tests/progseq/test_op_model_construct_matrix.py`, 63 cases × 4 targets),
   `test_op_model_inherit_native.py`, the WB DMA behaviour/build tests (narrow
   model), `test_c_message_format.py`, op-model-py's import-and-drive tests,
   `test_sv_op_model_sim.py` (4 tests), and the corpus tier (py, sv, bc).
   Everything else greps text or compares goldens. Goldens prove sameness,
   never correctness. Several WB DMA operations (`write_descriptor`,
   `read_descriptor_residual`, the interrupt-routing `match`) have **no
   executed check on any target**.
2. **Some legal programs compile to wrong code with no error, and no test
   catches it** (§2, list W). Examples: C++ `+=` ignores PSS width. C/C++
   struct parameters are copied instead of passed by handle. Shift by ≥ width
   is C undefined behaviour. SV `foreach (v : arr)` binds `v` to the index.
   SV signed `>>=` shifts logically. SV passes PSS format strings to `$write`
   unchanged.
3. **ast2ir loses constructs silently** (02 §3.2). `_translate_expression`
   and `_translate_data_type` return `None` with no error. As a result:
   - float locals, literals, returns and parameters are lost;
   - parameters of type `ref T` or `type T` are **removed from the
     signature**;
   - `compile has` becomes `None`.
   Every test that asserts only "no errors" passes over these.
4. **Hollow tests.** `test_stmt_translation.py` and `test_expr_translation.py`
   are named after repeat, repeat-while, foreach, yield, match, `in`, `?:` and
   `**`, but their PSS contains none of them. Three `format`/`error`/`fatal`
   tests in `test_string_and_builtins.py` are empty `pass` bodies.
5. **Internal errors instead of located refusals.** About 15 LRM-legal
   constructs reach an emitter and fail with `ValueError: … defines no
   expr_*()` or "unsupported C type for NoneType":
   - string, float, `in`, non-constant `**`, struct literals, match ranges,
     structs with array members, `error()`;
   - SV `**`;
   - py `get_offset_of_instance` in an operation.
   No test pins any of them.
6. **Whole construct families have no test on any emission target.** These
   are float, chandle (beyond a field), list/map/set, string methods,
   reduction ops, `randomize`, data arrays as values in the corpus, `ref T`,
   file ops, `urandom`, and target-template functions. Most are refused, and
   most refusals are themselves untested (02 §5 item 10; 03 §5.4).
7. **C and C++ run no compliance tests.** The corpus enters through
   `--export-action`, and C/C++ have no entries, package functions or init
   blocks (05 §7). The construct matrix is the only semantic check of C/C++.
8. **The older targets are effectively untested for procedural code.**
   sv-native/sv-oo/sv-pure turn locals, functions, `repeat (i:N)`, `?:` and
   `**` into comments or invalid SV without an error.
   `tests/unit/sv/test_lower_types.py` pins **invalid** SV as its expected
   output. c-host has one `1 + 2` test.
9. **The taxonomy over-states one area:** `*= /= %= ^=` are not PSS (8.1
   `assign_op`). pssparser correctly rejects them.
10. **Environment note:** AGENTS.md says `../python/bin/python`. In this
    checkout the venv is `packages/python/bin/python`. pytest-xdist is not
    installed.

## 2. Consolidated defect list (to be pinned as strict xfails in P1)

"W" = compiles to wrong code or fails to compile with no diagnostic.
"E" = internal error where a located refusal belongs.
"I" = IR loss (ast2ir).
Sources are in brackets.

| ID | Target | Construct | Symptom | Src |
|---|---|---|---|---|
| W1 | cpp | `op=` on narrow / signed int (`bit[4] n=15; n+=1`) | 16; `int[16]` overflow not wrapped | 03 §3.4 |
| W2 | c, cpp | struct parameter written by the callee | copy: caller does not see the write (20.3.2) | 03 §3.4 |
| W3 | c, cpp | `x << n`, n ≥ width | C UB (`1<<33` → 2) | 03 §3.4 |
| W4 | c, cpp, **sv** | struct inheritance (`struct d_s : b_s`), access inherited field | does not compile (sv: found by the P1 case) | 03 §3.4 |
| W5 | c, cpp, sv | `foreach (v : a[i])` (value + index) | c/cpp: `i` undeclared; sv: `v` is index (S1) | 03, 04 S1 |
| W6 | c, cpp | default parameter value | too few arguments | 03 §3.4 |
| W7 | c | array `==` | `-Werror=array-compare`; compares addresses | 03 §3.4 |
| W8 | c, cpp | over-wide literal to `write8` (8.7.2) | `-Werror=overflow` | 03 §3.4 |
| W9 | c, cpp | struct parameter only written | `-Werror=unused-but-set-parameter` | 03 §3.4 |
| W10 | sv | signed `>>=` | logical shift (S2) | 04 S2 |
| W11 | sv | `print`/`format`/`error`/`fatal` | PSS format passed verbatim; `$fatal` status (S6) | 04 S6 |
| ~~W12~~ | – | `read_field` | **withdrawn**: `read_field` is not PSS (LRM 21.14 has only `write_field`); pssparser refuses it with a location. `write_field` executes on all four (P1 case). | 04 S8 |
| W13 | sv | `chandle` | mapped to `addr_handle_t` (S9) | 04 S9 |
| W14 | sv, **c** | `(int[8])u`, `(bool)x` | sv keeps operand sign; bool = low bit (S10). c: `(bool)0x100` is false (found by the P1 case) | 04 S10 |
| W15 | py | `(bool)x` | identity (S11) | 04 S11 |
| W16 | sv | match with no matching arm, no default | falls through (20.7.10: error) (S12) | 04 S12 |
| W17 | py | `x[h:l] op= v` | probably an unimportable module (S7; **unverified**) | 04 S7 |
| W18 | all | `output`/`inout`/`ref` param direction | dropped; scalar passed by value (S5). pssparser already refuses `output` on a DEFINED function; only an imported one reaches pssc, and the trace harness cannot implement imports yet, so this is pinned at the IR (P2) | 04 S5, 02 |
| W19 | py, sv | user function named `get_offset_of_instance` | taken as register fold (corpus `types.string.match`) | 05 §6 |
| E1 | c, cpp, sv, py | `string` local/field/param (c/cpp); substring, methods (sv/py) | internal error | 03 §3.3, 04 §4.2 |
| E2 | all | `float32/float64` | unlocated ("NoneType") | 03, 04 S13 |
| E3 | c, cpp, sv, py | procedural `in` | `defines no expr_in()` | 03, 04 |
| E4 | c, cpp, sv | `**` (c/cpp: non-constant; sv: any) | internal | 03, 04 |
| E5 | c, cpp, py | struct aggregate literal | internal | 03, 04 |
| E6 | c, cpp, py | match range arm | internal (py = corpus `proc.match.ranges` xfail) | 03, 05 |
| E7 | c, cpp | struct with array member | internal | 03 |
| E8 | c, cpp, py | `error()`/`fatal()` | "call_utility() did not recognise it" | 03, 04 S3 |
| E9 | py | `get_offset_of_instance` in an operation | "defines no call_fold()" (S4) | 04 |
| E10 | py | channel array | internal | 04 |
| E11 | c, cpp, sv, py | list/map/set | internal / call-gate message, no location | 03, 04 |
| I1 | ast2ir | float local/literal/return/param | `None` embedded; param removed | 02 §3.2 |
| I2 | ast2ir | `ref T`, `type T` param | removed from signature | 02 §3.2 |
| I3 | ast2ir | `compile has` | `None` | 02 §3.2 |
| I4 | ast2ir | float/`ref`/enum-with-domain FIELD | refused, but without a location | 02 §3.1 |
| W20 | c, cpp, py | escape in a string literal (`"\n"`) | printed as backslash-n (found by the P1 `print` case) | P1 |
| W21 | sv | `string` field of a component | no member declared; the body does not compile (found by P1) | P1 |
| E12 | all | `null` (`chandle d; d == null`) | internal error, no `expr_null` (found by P1) | P1 |
| W23 | c, cpp | `break` in a `match` arm inside a loop | **silently wrong**: the `switch` it lowers to swallows the `break`, so the loop goes on (IP-1; found by the P1 case, no test covered it) | P1 |
| W24 | sv (Verilator) | default of an array member of a struct (`array<bit[8],2> d = {3, 4};`) | the generated SV dropped it (fixed: `'{3, 4}`); Verilator 5.052 then ignores an unpacked array member's default (IEEE 1800 allows it), so a local still reads 0. Strict xfail; workaround = declaration-site initialisation | P1 |
| W22 | cpp | struct `==` / `!=` | does not compile: generated structs declare no `operator==` (found by P1 "struct equality") | P1 |
| I5 | ast2ir | `bool` is `bit[1]` in the IR; `(bool)x` is `Cast(bit[1], x)` | a bool cast truncates to the low bit (7.12.1 b: nonzero is true). Root of W14 (c, sv) and W15 (py) | P2 |

P1 found I1 to be **silently wrong in the matrix**: `g(int x, float32 y)`
called as `g(1, 2.0)` compiles and runs on all four targets, the parameter
and its argument gone. Collections (E11) are refused, but for their method
call ("no function named 'push_back' … declare it as an import"), not as a
collection.

Already-known open xfails: matrix M10/M11 (py lazy sub-component
construction), corpus U-goi (= W19), U-range (= E6 py), U-xtr (sv executor
delegation).

## 3. Coverage at a glance (per coarse ID, strongest evidence per layer)

Legend: **X** executed with a value check · **P** works in this audit's probe
only (no test) · **T** text/golden/compile only · **R** designed refusal
(tested) · **r** designed refusal (untested) · **W** silently wrong ·
**E** internal error / unlocated · **–** nothing.
IR = parse→IR tests (02); C, C++, SV, Py = the op-model targets;
Corp = pss-corpus (py/sv/bc).

| ID | IR | C | C++ | SV | Py | Corp | Main gap |
|---|---|---|---|---|---|---|---|
| DT-INT widths/sign | shape | X | X / W1 | X | X | yes | widths 1, 33–63; sign mixes in all contexts |
| DT-FP | **I1** | E | E | E | E | – | decision + located refusal |
| DT-BOOL | smoke | P | P | W14 | W15 | yes | casts |
| DT-ENUM | shape | X | X | X | X | yes | enum ranges/`in`, extend, enum in match on all targets |
| DT-STR | smoke | E | E | X (==, match) | X | partial | methods, substring, refusal on C |
| DT-CHND | – | P | P | W13 | P | – | |
| DT-STRUCT | shape | X / W2, W4 | X / W2, W4 | X | X | yes | by-handle params, inheritance, literal (E5), `==` |
| DT-PACK | – | – | – | – | – | – | read/write_struct |
| DT-ARR (data) | shape | X (fields) / W5, W7 | X / W5 | X / W5 | X | **none** | value copy, `==`, methods, params refused |
| DT-LIST/MAP/SET | hand-IR | E/r | E/r | E | E | – | refusal now; implementation is a decision |
| DT-REF | **I2** | – | – | – | – | – | |
| DT-TYPEDEF | – | P | P | ? | ? | – | |
| DT-CAST | smoke | P | P | W14 | W15 | partial | sign/size/bool matrix |
| DT-ADDR | shape | X | X | X | X | L0-tap | |
| DT-REG | shape | X | X | X / W12 | X | – | read_field, arrays with run-time index |
| DT-CONST | shape | P | P | T (lint) | X | partial | |
| EX-LIT | shape | X | X | X | X | yes | based/sized/octal, aggregate (E5) |
| EX-ARITH | smoke | X | X | X / E4 | X | yes | `**`, div/mod signs, wrap |
| EX-REL / EX-EQ | smoke | X | X | X | X | yes | mixed sign, struct/array `==` |
| EX-LOG | smoke | X | X | X | P | yes | side-effect short-circuit on py |
| EX-BIT | – | X | X | X | X | yes | |
| EX-RED | r | r | r | r | r | – | refusal tests only |
| EX-SHIFT | – | X / W3 | X / W3 | X / W10 | X | yes | ≥ width, run-time amount, signed |
| EX-COND | smoke | P | P | X | X | yes | |
| EX-IN | – | E3 | E3 | E3 | E3 | constraint only | |
| EX-SEL | – | X | X | X | X / W17 | – | lvalue, RMW, op= |
| EX-SUBSTR | – | E | E | E | E | – | |
| EX-WIDTH (8.7) | – | X | X / W1 | X | X | rt_* tests | per-context grid (§4.3) |
| EX-ASSIGN | – | X | W1 | W10 | X | yes | op= on fields/elements/selects |
| EX-REF | shape | T | T | T | X | partial | param-shadows-field executed on C/C++/SV |
| FN-DECL / FN-CALL | shape | X | X | X | X | yes | qualified `pkg::f`, call in conditions |
| FN-PARAM | smoke | W2, W6, W9 | W2, W6, W9 | R (arrays) | X | partial | defaults, struct by-handle, direction W18 |
| FN-PURE | smoke | P | P | ? | ? | – | |
| FN-IMPORT | shape | X | X | T | X | – | |
| FN-EXPORT | – | n/a | n/a | X (root) | n/a | – | |
| FN-SOLVE | shape | X | X | X | X | – | |
| FN-VIRT | shape | X | X | X | X | – | |
| FN-TT | r | r | r | r | r | – | |
| ST-BLOCK/VAR/ASSIGN | shape | X | X | X | X | yes | |
| ST-RET | smoke | X | X | X | X | yes | return from nested loops |
| ST-REPEAT/RWHILE/WHILE | **hollow** | X | X | X | X | yes | IR shape; continue in rwhile |
| ST-FOREACH | – | X (comp arr) / W5 | W5 | W5 | X | – | data collections, index+value |
| ST-IF | – | X | X | X | X | yes | |
| ST-MATCH | – | X / E6 | X / E6 | X / W16 | X / E6 | yes | ranges, no-default |
| ST-BRK | – | X | X | X | X | yes | nested, inside match |
| ST-RAND | R (ast2ir) | – | – | – | – | – | refusal test only (decision) |
| ST-CIF | – (`compile has` I3) | P | P | ? | ? | yes | |
| ST-YIELD | hollow | T | T | T | T | yes | |
| ST-SUPER | shape | n/a | n/a | corpus | X | – | |
| EXEC-KIND | shape | R (init) | r | X | X | comp/* | |
| LIB-MSG | – | X | X | X (message) / W11 | X | L0-protocol | print/format |
| LIB-ERR | empty test | E8 | E8 | W11 | E8 | – | |
| LIB-RAND | U | R | R | U | r | – | |
| LIB-MEM | shape | P | P | X | X | L0-tap | model-body primitives executed on C/C++ |
| LIB-REG | shape | X | X | X | X | – | |
| LIB-CHAN | – | X | X | T | X | sync/* | SV executed only via the corpus |

---

## 4. Test strategy

### 4.1 Principles

1. **Semantics are proved only by execution.** A construct counts as tested on
   a target only when generated code is executed and its output compared with
   a value written by hand from the LRM. Text greps and goldens stay, but they
   do not count as coverage. (This matches AGENTS.md: "snapshots prove
   sameness".)
2. **One expectation, four targets.** The vehicle is the construct matrix:
   one hand-written trace, run on C, C++, Python and SV. A target that cannot
   do a case gets a **strict xfail naming its defect** or a `refused=` entry.
   It never gets a separate expectation. Wherever the corpus can carry a case
   portably (exported action, no target-specific seam), the case also goes
   into pss-corpus so other implementations are held to it.
3. **Every LRM construct ends in exactly one of two states on every target:**
   executed and checked, or **refused with a located diagnostic**, and the
   refusal is tested. No internal error, no silent drop, no unlocated message.
   That is enforceable by a registry test (§4.4).
4. **Expectations come from the LRM.** Where the LRM is silent or
   contradictory (01 §7), the case waits for a recorded ruling (§7) and
   names it.
5. **Patterns are chosen systematically, not by inspiration.** Combinations
   are drawn from (a) the 100 interaction patterns (01 §8), and (b) a
   **context grid** for expressions (§4.3) covered pairwise. A differential
   fuzzer catches what neither list anticipated (§4.5).

### 4.2 Layers and vehicles

| Layer | Vehicle | Proves | New infrastructure |
|---|---|---|---|
| L0 Parse→IR | `tests/unit/ir/test_<area>_ir.py` (new, table-driven: PSS snippet → expected IR shape) | the construct reaches the IR intact, or is refused with a location | small `ir_of(pss, path)` helper; IR **kind registries** for expression and data-type nodes (like `test_type_body_registry.py`) |
| L1 Construct semantics | construct matrix, split by area: `tests/progseq/matrix/test_m_<area>.py` sharing `trace_harness` and `Case` | one trace on c/cpp/py/sv | split the 63-case file into a package. Add a `message`-only trace helper (cases need not touch memory). Add a **case ID ↔ construct ID** tag (`Case(…, covers=("ST-FOREACH.3","IP-1"))`) |
| L1' Refusals | the same matrix, `want=Refused([...])` with a **location** regex (`file:line`) | located refusal | `Refused(..., located=True)` default |
| L2 Portable compliance | pss-corpus `proc/`, `types/`, `lib/` (upstream), runners for bc/py/sv(/c/cpp) | conformance to the shared checker | C and C++ adapters (05 §7) once entries, package functions and init blocks exist |
| L3 Differential | random expression/statement programs: bc + py + c + cpp + sv must agree | width/sign interactions nobody listed | generator + reducer; bc is the in-process oracle |
| L4 Accounting | `docs/design/construct-test-audit/coverage.yaml` + `tests/unit/test_construct_coverage.py` | every sub-construct has, per target, an executed case or a tested refusal | yaml schema; the test reads matrix `covers=` tags and corpus `test.json` feature IDs |

### 4.3 The expression context grid (EX-WIDTH and friends)

The 8.7 rules make an expression's result depend on its **context**. Cases
are generated by combining three axes, covered pairwise (every value pair of
any two axes appears at least once), not by full Cartesian product.

* **Operator** (14): `+ - * / % ** & | ^ ~ << >> ?: unary-`
* **Operand types** (9): `bit[1]`, `bit[4]`, `bit[8]`, `int[8]`,
  `bit[16]`, `bit[32]`, `int` (32 signed), `bit[33]`, `bit[64]`/`int[64]`,
  mixed signed/unsigned pairs
* **Context** (11): local initializer · plain assignment · compound
  assignment · call argument (scalar formal) · `return` · `if`/`while`
  condition · `message`/`print` vararg (self-determined) · register
  `write_val` · struct field target · array element target · part-select
  target

A small generator in `tests/progseq/matrix/grid.py` emits the cases with the
**expected value computed by a reference model of 8.7**. The reference is
written independently of `IntSemantics`, as an arbitrary-precision Python
oracle (`tests/progseq/matrix/ref8_7.py`), so the targets are not compared
against the code they share. Estimated size is ~200 cases. Each case is one
`message("%d", …)` or one `write32`.

### 4.4 Registries (the "nothing silently missing" guarantee)

* `test_expr_kind_registry.py` / `test_datatype_kind_registry.py`: each
  pssparser `Expr*` and `DataType*` class has a row saying it is translated
  (IR kind) or refused (message). A new class fails by name, as
  `test_activity_registry.py` already does. This closes I1–I4 structurally,
  together with the ast2ir catch-all (refuse on `None`).
* `test_emitter_kind_registry.py`: for each op-model target, each IR
  statement/expression kind and data type is **handled** (hook exists) or
  **gated** (the gate refuses it with a location *before* emission). An IR
  kind with neither fails. This closes E1–E11 structurally: the gate must
  pre-screen types and expression kinds, not only calls (03 §5.3).
* `test_construct_coverage.py` (L4): reads `coverage.yaml`; for every
  sub-construct and every target there is a matrix case (by `covers=`), a
  corpus test (by feature ID), or a recorded `refused`/`n-a` with a tested
  refusal case.

### 4.5 Differential fuzzing (L3)

bc already executes procedural code and passes the whole corpus, so it is the
cheapest oracle: it runs in-process, with no compiler. The generator produces
a `target function void run()` with:
- random locals of grid types;
- straight-line code plus bounded loops, if/match, and calls to generated
  pure helpers;
- every intermediate value printed with `message`.

Each target must print the same lines. A disagreement is reduced (delta
debugging over statements) and becomes a matrix case. Run with a fixed seed
set in CI, with a seeds-per-minute budget, and longer in `-m perf`.
**Calibration:** the fuzzer must find W1 and W3 when they are re-introduced.
If it does not, it is not testing widths.

---

## 5. Phased plan

Each phase ends green: new cases that fail on current code are **strict
xfails naming the defect**, and they flip when the defect is fixed. Fixes are
listed so the plan can be read against the defect list, but each fix is its
own reviewed change.

### P0 — Rulings and scaffolding (no behaviour change)

1. Record rulings for the §7 questions that block expectations: R1–R10.
2. Split `test_op_model_construct_matrix.py` into `tests/progseq/matrix/`
   (harness unchanged, one file per area). Add `covers=` to `Case` and tag
   the existing 63 cases.
3. Add `coverage.yaml` generated from `01-lrm-inventory.md` (IDs only)
   and `test_construct_coverage.py`. Start it in **report mode**: it prints
   the gap table and fails only on a dangling ID. It becomes enforcing at
   the end of P5.
4. Fix AGENTS.md's venv path (`packages/python/bin/python`). Flagged for the
   user, since AGENTS.md is theirs.

### P1 — Pin what is known to be wrong (strict xfails, then fixes)

1. **Matrix cases for W1–W19 and E1–E11**, one per row of §2, using the
   PSS from 03 §3.3–3.4 and 04 §1.1. Expected values come from the LRM (W3
   needs ruling R1). Refusals expect a **located** message.
2. **Delete or rewrite the hollow tests** (`test_stmt_translation.py`,
   `test_expr_translation.py`, the empty `format/error/fatal` tests).
   Rewriting is P2's job, so P1 deletes them with a pointer to P2.
3. **`tests/unit/sv/test_lower_types.py`**: replace the assertion that pins
   invalid collection-field SV with an expected refusal (R9).
4. Fixes, in risk order:
   - W1 (port C++ `op=` to `IntSemantics`);
   - W3 (shift guard in `CIntSemantics`);
   - W2 + W9 (struct params by pointer/reference, 20.3.2);
   - W10, W14, W15 (SV `>>>=`, cast rules; py bool);
   - W5 (foreach value+index on c/cpp/sv);
   - W16 (SV match default arm raising);
   - W11 (SV format translation, shared with `c/message_format.py`);
   - W12, W13;
   - W4, W6, W7, W8;
   - W19 (classify by resolved declaration, never name);
   - W18 (carry direction; refuse `output` until supported).

   E1–E11 become located refusals through the gate pre-screen (§4.4) unless
   the ruling is "implement".

### P2 — Parse→IR suite (L0)

Table-driven, one file per area. Each row is a PSS snippet → IR shape or
located error.

| File | Rows (minimum) |
|---|---|
| `test_ir_statements.py` | if / else-if / unbraced; repeat count; `repeat (i : n)` (target, scope); repeat-while; while; foreach over array/list/map/set × {value, value+index, map key}; match {values, multi-value `PatternOr`, ranges `ExprRange` incl. `[0..3]` regression, enum items, default}; break/continue nested; return value/void; scoped block shadow rename (`x__s1`); `compile if` taken/not-taken; yield; `super;`; randomize → located refusal |
| `test_ir_expressions.py` | every binop (`% ** << >> & \| ^ && \|\|`), unary (`- ~ !`), reduction → refusal, `?:`, `in` (values, ranges, array), bit select, part select, substring (distinguished by type), casts (`(bit[8])`, `(bool)`, enum↔int), `null`, sized/based/octal/unsized literals with width+sign, aggregate literals (struct, array, nested), method calls (string/collection) |
| `test_ir_types.py` | int widths/sign/domain, bool, enum (values, implicit, extend, with domain → located refusal), string, chandle, struct (nested, inherited), arrays (multi-dim, of struct, `size_ref`), list/map/set, `ref T` → located refusal, typedef (package; component → located refusal), float → located refusal (until R5) |
| `test_ir_functions.py` | params: value, `const`, default, varargs, struct, array; `ref`/`type`/float param → located refusal (never removed); direction recorded; `pure`; recursion; qualified `pkg::f`; `(void)f()`; return types incl. float → refusal |
| `test_ir_library.py` | `print/message/format/error/fatal/urandom/urandom_range` with `std_pkg`; `compile has` → IR or refusal (never `None`) |

Plus the two kind registries from §4.4 and the ast2ir `None` catch-all.

### P3 — Construct matrix expansion (L1), by area

Case lists, all four targets. **Bold** cases are expected to fail somewhere
today (xfail per §2).

| File | Cases |
|---|---|
| `test_m_ints.py` | the §4.3 grid (~200, generated); literal forms (based, sized, octal, `'h` vs `0x` signedness 4.6.1); div/mod sign table; **`**` table 13** incl. negative exponent, `2**31` in `int`, left-assoc `2**3**2`; **shift ≥ width**, run-time shift amount, signed `>>`/**`>>=`**; unary minus of unsigned in relational (IP-79); `bit[1]` arithmetic; 33-bit and 63-bit operations; `int` accumulate past 2³¹ vs `bit[64]` (IP-78) |
| `test_m_bool_enum_cast.py` | bool ops, **`(bool)x`**, bool in arithmetic; enum explicit/implicit values, enum ↔ int casts, enum relational (R6), enum item as arg/default (IP-33, IP-66), `?:` with enum items (IP-32), `in [A..C]` (R5), **sign/size casts** (IP-20, IP-81) |
| `test_m_struct.py` | local copy is a value; whole-struct assign; nested deep assign; struct return; **struct `==`**; **struct literal** full and partial (IP-14, IP-15, IP-91); **inheritance** (W4, IP-39, IP-40); **by-handle param** (W2, IP-38 swap_structs, IP-10 recursion); **struct with array member** (E7) |
| `test_m_array.py` | local/field arrays, 2-D (IP-42), of struct; array copy semantics (IP-41); **`==`** (W7); `in` array (E3); `size()`/`sum()`; array literal shape (existing 59); **foreach value / value+index / nested** (W5, IP-24); array param (py/sv decision R8) |
| `test_m_control.py` | if/else-if/unbraced; nested loops with inner break/continue (IP-1–IP-3, IP-84); **break inside match inside foreach** (IP-1); return from nested loops in a function and in an entry (IP-4, IP-5, IP-83); repeat count evaluated once with a side effect (IP-9); call in while condition counted (IP-8); short-circuit with side-effecting calls (IP-29, IP-30); scoped shadowing (IP-22, IP-25); `compile if` choosing statements (IP-59) |
| `test_m_match.py` | values, multi-value, **ranges** (E6), enum with default (IP-12), string match (IP-13 where strings exist), **no-match-no-default** (W16; expectation per R2), match inside loop with break/continue |
| `test_m_functions.py` | **defaults** (W6), const params, varargs to message (IP-16, IP-67), recursion scalar (IP-11), qualified `pkg::f` (once C/C++ have package functions), function in loop condition, **param-shadows-field executed** (IP-23), virtual call in a loop + `super.f` (IP-62), pure function, **output param refusal** (W18) |
| `test_m_strings.py` | `==`, `!=`, match; substring; `size/find/upper/lower/split/chars` (IP-69, IP-70); **C/C++ refusal** located (E1) or implementation (R3) |
| `test_m_library.py` | `message` specifiers on all widths (IP-67); **`print/format` on SV** (W11); **`error/fatal`** (E8, IP-99); `urandom` refusal or seed-stable behaviour (R7); **model-body `read8..64`/`write8..64`** executed on C/C++ (03 §5.1); `write8` literal truncation (W8); `read_field`/`write_field` (W12); register array with run-time index (IP-50); `write_val(~x)` (IP-48); `write_masked` with `~0` field literal (IP-47); `make_handle_from_handle` in a loop (IP-52) |
| `test_m_channels.py` | try_put/try_get round trip **executed on SV** (first in progseq); try_get into struct field/array element (IP-54); depth >1 refusal or FIFO (IP-55) |
| `test_m_refusals.py` | one located-refusal case per construct a target refuses: float, list/map/set, `ref`, randomize, reduction, file ops, target-template fn/exec, `exec pre_body`, component typedef, `*=` (parse error, located), `output` param, init blocks / package fns / entries on c/cpp until supported |

The WB DMA golden-only operations (03 §5.1, 04 §4.5) get an executed check:
extend `test_op_model_behaviour_c/cpp.py` with traced calls of
`write_descriptor`, `read_descriptor_residual`, `transfer_list`,
`check_completion`, `configure_interrupt_routing` and
`set_software_pointer`. Add the same trace on py and sv (`test_sim_wb_dma`
style) so the real model's operations are held to one trace on all four.

### P4 — Interaction patterns

Every IP in 01 §8 is assigned to one of three buckets:

* **Matrix now** (op-model-expressible, constructs supported or with an
  intended refusal): IP-1–6, 8–12, 14–25, 28–33, 38–42, 45–50, 52–54, 59,
  62, 66, 67, 75–84, 88, 91, 99. Folded into the P3 files above, with
  `covers=` naming the IP.
- **After an implementation decision** (collections, strings on C, float,
  `ref`, randomize, map/set): IP-7, 13, 26, 27, 34–37, 43, 44, 56–58, 63,
  69–74, 92–96, 100. Each is written now as a located-refusal case on the
  targets that refuse, so the refusal is pinned. The executed form is added
  when the construct lands.
* **Not op-model territory** (activities, solve-time, mutable component
  state across traversals, parallel executors): IP-51, 55, 60, 61, 64, 65,
  68, 85–87, 89, 90, 97, 98. These go to bc
  (`tests/unit/integration/test_bc_*`) and/or the corpus. IP-61 (`super;`)
  and IP-89 also go to op-model-py/sv through `--export-action`.

P4 is done when `coverage.yaml` maps all 100 IPs.

### P5 — Refusal completeness and accounting

1. Gate pre-screen of data types and expression kinds per target
   (`OpModelTarget.check`), driven by the emitter-kind registry (§4.4).
2. `test_m_refusals.py` complete. Every located-refusal message carries
   `file:line` (the corpus `diag_location` rule applied to the matrix).
3. `test_construct_coverage.py` switched to **enforcing**.

### P6 — Corpus and C/C++ compliance

1. Upstream corpus tests for portable holes (05 §5): data arrays, bit/part
   select, `**`, procedural `in`, reduction (as a refusal-tolerant test),
   string `!=`/substring/methods, enum ranges/`in`, `print`, `error`/`fatal`
   (`runtime_error` verdict), pure/qualified calls, struct inheritance and
   by-handle params, nested break/continue in match, `continue` in
   repeat-while, return from nested loops in an exec, and negative tests
   (type mismatch, `break` outside a loop, wrong arg count, illegal context).
   Never edit an existing corpus model (AGENTS.md).
2. Add the corpus's `features.yaml` (spec'd in COMPLIANCE-DESIGN §9, absent)
   so `coverage.yaml` can read corpus coverage by feature ID.
3. C/C++ entries, package functions and init blocks (each a backend feature
   with its own plan). Then `adapters/pssc_op_model_c.py`/`_cpp.py` and the
   strict `expected/op-model-c.toml`/`-cpp.toml`.

### P7 — Differential fuzzer (L3)

The generator, reducer, fixed-seed CI job and calibration from §4.5. It
starts with integer straight-line code and then adds control flow, calls and
structs. Every reduced disagreement becomes a matrix case before its fix.

### P8 — Older targets (sv-native / sv-oo / sv-pure / c-host)

Decision R9. Either they are held to the matrix (add them as matrix targets
where their API allows a `run()`), or they **refuse** procedural constructs
they cannot render, with located refusals tested in `test_m_refusals.py`.
Today they emit comments or invalid SV silently. Either answer is better
than that.

### Order and size (rough)

| Phase | Depends on | New tests (approx.) | Notes |
|---|---|---|---|
| P0 | – | 2 | rulings are the long pole |
| P1 | P0 split | ~35 cases + fixes | most value per effort |
| P2 | – (parallel with P1) | ~150 rows | catches I1–I4 class |
| P3 | P0, P1 | ~200 grid + ~150 hand cases | ×4 targets; SV adds Verilator time, so group cases into one SV build per file |
| P4 | P3 | ~70 cases | |
| P5 | P3 | registry + ~40 refusals | |
| P6 | corpus repo; C/C++ backend work | ~30 corpus tests | cross-repo |
| P7 | P3 | generator | |
| P8 | R9 | ? | |

**Runtime risk:** each SV matrix case is a Verilator build. Before P3 grows
the matrix tenfold, the harness should batch all cases of a file into
**one** SV package and testbench (one `run_<case>()` per case), and likewise
one C/C++ executable per file. Otherwise the suite becomes unusable. This
batching is a P0 item if P3 is approved.

---

## 6. Construct → planned test map (by sub-construct group)

The full sub-construct list is 01 §1–§6. This table states, per coarse ID,
which planned file covers which sub-construct groups, so review can check
for completeness. "Ref" = located-refusal case.

| Coarse ID | Sub-construct groups (01) | L0 file | L1 file | Corpus/bc |
|---|---|---|---|---|
| DT-INT | widths, sign, domains, defaults | types | ints (grid) | types.* exist |
| DT-FP | all | types (Ref) | refusals (or ints-fp after R5) | – |
| DT-BOOL | literal, ops, cast | types/expr | bool_enum_cast | |
| DT-ENUM | values, implicit, extend, cast, relational, ranges, domain | types | bool_enum_cast, match | P6 enum ranges/in |
| DT-STR | ==, !=, match, substring, methods, format | types/expr | strings | P6 |
| DT-CHND | field, param, null compare | types | library (W13) | |
| DT-STRUCT | nesting, copy, ==, literal, inheritance, params, return, array member | types | struct | P6 |
| DT-PACK | layout, sizeof_s, read/write_struct | types | library (or Ref) | bc |
| DT-ARR | decl, multi-dim, of struct, literal, copy, ==, in, methods, foreach, params | types | array | P6 data arrays |
| DT-LIST/MAP/SET | all methods/operators | types | refusals (R4) | bc |
| DT-REF | decl, compare, call through | types (Ref) | refusals | bc |
| DT-MUT | mutable attributes | – | – | bc (IP-86) |
| DT-TYPEDEF | package / component | types | struct/ints (use sites) | |
| DT-CAST | size, sign, bool, enum | expr | bool_enum_cast, grid | types.cast.* |
| DT-ADDR | handles, make_handle_from_* | – | library | L0-tap |
| DT-REG | reg, group, arrays, fields, read_field | existing reg tests | library | P6 (next batch) |
| DT-CONST | static const, template value | types | ints | |
| EX-LIT / EX-TQS | forms, aggregate, TQS | expr | ints, struct, strings | |
| EX-PREC | table 11 | expr | grid (precedence column) | |
| EX-ARITH..EX-SHIFT, EX-COND, EX-WIDTH | operators × types × contexts | expr | grid + ints | rt_* |
| EX-RED | | expr (Ref) | refusals | |
| EX-IN | values, ranges, array, empty range | expr | array, bool_enum_cast | P6 |
| EX-SEL | read, write, RMW, op= | expr | ints, library (IP-45, IP-46) | P6 |
| EX-IDX | array, list, map | expr | array | |
| EX-SUBSTR / EX-METH | | expr | strings, array | |
| EX-ASSIGN | 7 ops × targets (local/field/element/select/sub-comp field) | statements | grid (context axis) | |
| EX-REF | this, super.x, sub-comp, param-shadows-field | expr | functions, control | |
| FN-* | 01 §3 | functions | functions | P6 |
| FN-TT / EXEC-TT | | functions (Ref) | refusals | |
| ST-* | 01 §4 | statements | control, match, array | P6 |
| ST-RAND | | statements (Ref) | refusals | bc (IP-56–58) |
| ST-YIELD | | statements | control (executed trace with yields) | sync |
| ST-SUPER | | statements | functions (py/sv via entries) | |
| EXEC-KIND | order, merge, init down/up | statements | existing init tests + control | comp/* |
| LIB-* | 01 §6 | library | library, channels | L0-*, sync |

## 7. Rulings (from 01 §7 and the findings)

**Decided 2026-10-16 (user):**

* **R1** — a shift by ≥ width gives 0 for `<<` and logical `>>`, and the
  sign fill for a signed `>>`.
* **R2** — an unmatched procedural `match` with no default is a run-time
  error on every target.
* **R3/R4/R5** — strings on C/C++, collections (list/map/set) on every
  op-model target, and floating point everywhere are **refused with a
  location for now**. They are kept on a list of known-unsupported features
  (§7.1), not forgotten: a strategy for supporting some level of
  collections is wanted soon.
* **R9** — sv-native / sv-oo / sv-pure / c-host refuse what they cannot
  render, with tested located refusals. Deprecation is a separate decision.

R6, R7, R8 and R10 are still open; work proceeds on the suggested defaults
only where a case does not depend on them.

| R | Question | Why it blocks | Suggested default |
|---|---|---|---|
| R1 | Result of a shift by ≥ width (8.5.7 silent) | W3 expectation; grid | 0 for `<<` and logical `>>`; sign fill for signed `>>` (follows "vacated positions filled") |
| R2 | Unmatched procedural `match` with no default | W16; C's `--match-default message` vs py raise | an error at run time on every target (20.7.10); C's message stays the default form of "error" |
| R3 | Strings on C/C++: implement or refuse | E1; ~15 cases | refuse (located) now, implement later |
| R4 | Collections (list/map/set) on op-model targets | E11; 30+ IPs | refuse (located) on all four for now; bc is the executing backend |
| R5 | Floating point | I1, E2 | refuse (located) everywhere until a design exists |
| R6 | Enum relational operators (only `==`/assign granted) and enum range order | current C/C++ accept `<` | allow, by value order (matches current behaviour) |
| R7 | `urandom` on op-model targets | R today on c/cpp, r on py | refuse; or a seeded platform hook (a design question) |
| R8 | Array params on SV/C/C++ (20.3.2) | M53 refusal | keep refusing on c/cpp; implement on SV (`ref` arrays) |
| R9 | sv-native / sv-oo / sv-pure / c-host: hold to the matrix, or refuse procedural code | P8 | refuse what they cannot render, with tests; decide on deprecation separately |
| R11 | `print`/`format` in a TARGET function. LRM 21.1.2 declares both `solve`; c/cpp/py accept `print` at target time, sv refuses `format` there | the P1 `print` case uses a constructor (solve context) | refuse at target time (LRM), or keep as a documented extension |
| R10 | Argument evaluation order and compound-assign LHS evaluation count | IP-44 and side-effect cases | left-to-right, LHS once; test only where pssc commits to it |

The remaining LRM gaps in 01 §7 (unary `+`, duplicate enum values, repeat
index type, missing return, break in `while`, foreach-contents writes, nested
`super;`, export syntax, float default, exec-merge scoping, `urandom_range`
inclusivity, short-circuit wording) do not block the op-model plan. They
should go to the PSWG list alongside the existing proposals.

### 7.1 Known-unsupported features (refused by ruling, to be supported later)

Each is a located refusal with a test. A line leaves this list when the
construct is implemented and its refusal test becomes an executed case.

| Feature | Targets refusing | Ruling | Refusal test | Note |
|---|---|---|---|---|
| `string` locals, fields, params, results | op-model-c, op-model-cpp | R3 | `test_m_strings.py` (both cases, `refused=`) | refused by type (`unsupported_types`); sv/py render `==`, `!=`, `match`; string METHODS are unimplemented on sv/py too (E1, strict xfail) |
| list / map / set | all op-model targets | R4 | `test_m_refusals.py` "a list", "a map", "a set" | refused by type, naming each use; **a support strategy is wanted soon** (user, 2026-10-16) |
| `float32` / `float64` | all (refused in ast2ir) | R5 | `test_m_refusals.py` "floating point", "a floating-point parameter ..."; `tests/unit/ir/test_ir_expressions.py` | located (`line N:`) |
| `ref T` parameter, `type T` parameter | all (refused in ast2ir) | (I2) | `test_ir_expressions.py::test_a_parameter_is_never_dropped` | was dropped from the signature |
| `compile has` in a procedural body | all (refused in ast2ir) | (I3) | `test_ir_expressions.py::test_compile_has_in_a_body_is_refused` | |

## 8. Progress log

Newest last. Each entry names what changed and the test that proves it.

* 2026-10-16 — Rulings R1, R2, R3–R5, R9 recorded (§7). §7.1 opened.
* 2026-10-16 — **P0.2 done.** `test_op_model_construct_matrix.py` split into
  `tests/progseq/matrix/` (`_matrix.py`: `Case`, `Refused`, shared register
  groups; `test_m_<area>.py` × 10). Every one of the 63 cases carries
  `covers=` (coarse IDs). **Batching (the §5 runtime item) done as parallel
  builds rather than merged builds**: `trace_harness.prepare`/`finish`/
  `run_many` split generation (in-process) from building, and
  `matrix/conftest.py::matrix_results` builds a module's selected cases
  together, one process per core. Same verdicts (254 passed, 2 xfailed);
  3m29s → 44s. Merging cases into one SV package was rejected: every case
  has its own `pss_top` and shared type names, so it would need renaming
  roots and per-case include directories for no further gain. AGENTS.md's
  pointer to the matrix updated.
* 2026-10-16 — **P0.3 done (report mode).** The construct list is read from
  `01-lrm-inventory.md` itself (596 sub-constructs, 75 coarse IDs, 100 IPs),
  not copied into `coverage.yaml`: one list. `coverage.yaml` holds only what
  a case cannot say (`elsewhere`, `n-a`); a refusal is always a matrix case.
  `tests/progseq/matrix/coverage.py` computes coverage (a tag counts on a
  target unless the case is an xfail there); `test_coverage.py` enforces
  that every tag and disposition names a real ID and every case covers
  something, and prints the gap table. The 63 existing cases carry coarse
  tags only; sub-construct tags (`DT-INT.3`) are added as cases are written
  or revisited.
* P0.4 (AGENTS.md venv path) left for the user.
* 2026-10-16 — **P1.1 done.** 35 new matrix cases pin every W and E row
  that a trace can express, in the area files, plus four new files
  (`test_m_bool_enum_cast`, `test_m_strings`, `test_m_refusals`,
  `test_m_channels`). 100 strict xfails, each naming its defect; 296 pass.
  New executed coverage that already passes: shift ≥ width on py/sv, signed
  `>>=` on c/cpp/py, compound-assignment wrap on c/py/sv, `**` on py,
  struct by handle on py/sv, struct literal on sv, `foreach (v : w[i])` on
  py, array `==` on cpp/py/sv, defaults on py/sv, offset fold in an
  operation on sv, `write_field` on all four, `write8` truncation on py/sv,
  single-channel `try_put`/`try_get` on all four (first executed SV channel
  case in progseq). `_matrix.ERROR`/`FATAL` express a run-time error in a
  trace (R2; LRM 21.3: `error()` may continue, `fatal()` stops).
  Corrections to §2: W12 withdrawn, W4 and W14 wider, W18 moved to P2, new
  W20, W21, E12. New open ruling R11.
* **Finding: refusals have no location.** `validate_calls._where` is ready
  but ast2ir never sets `loc` on procedural IR (functions, statements,
  expressions), so every op-model refusal names `comp::function` at best.
  Setting it is not free: `loc` takes part in IR equality (`compare=True` in
  ir-core), so two equal expressions at different places would compare
  unequal (RMW detection, folding). Needed before §4.1 principle 3 can be
  enforced; proposed as `loc` with `compare=False` in ir-core plus stamping
  in ast2ir's `_translate_statement`/`_translate_expression`/
  `_translate_function`. Until then `Refused` patterns name the construct,
  not a `file:line`.
* 2026-10-16 — **P1.2 done.** `tests/unit/test_stmt_translation.py` and
  `test_expr_translation.py` deleted; the three empty `format/error/fatal`
  tests removed from `test_string_and_builtins.py` with a pointer. Replaced
  by **P2's first files** (`tests/unit/ir/`): `_ir.py` (`body`, `shape` -- a
  compact rendering of IR, e.g. `For(i, self.n, [Assign([x], i)])`),
  `test_ir_statements.py` (14), `test_ir_expressions.py` (50 rows +
  silent-drop pins), `test_ir_library.py` (7). 69 pass, 8 strict xfails:
  I1 (float local, literal, parameter), I2 (`ref`, `type` parameter), I3
  (`compile has` -> `return;` with no value), **I5 (new: bool is `bit[1]`,
  so `(bool)x` is the low bit -- the root of W14/W15)**, and W20 (string
  escapes not processed; the IR holds `'v=%d\\n'`).
* Correction to 01: an open range (`[6..]`, `[..2]`) is a DOMAIN form only
  (7.2.1 `domain_open_range_value`); in an `in` expression both bounds are
  required (8.5.9 `open_range_value`), and pssparser refuses it there. The
  first P1 `in` case used one; fixed.
* 2026-10-16 — **P1.3 done.** sv-native's `pss_type_to_sv_type_str` now
  REFUSES a list, array, map or set (`CompileError`, ruling R9): its type
  string precedes the name, and every SV collection form puts the dimension
  after it, so `bit [7:0] [16] x;`, `bit [31:0] [$] x;` and
  `bit [31:0] [string] x;` were all invalid (checked with Verilator).
  `tests/unit/sv/test_lower_types.py` asserted those texts; it now expects
  the refusal for all four kinds.
* 2026-10-16 — **P1.4 fixes, first batch** (each flips its strict xfails):
  - **W1** C++ `op=` now goes through `convert_to` as C's does
    (`cpp/lower_progseq.py`, `StmtAugAssign`).
  - **W17** `x[h:l] op= v` is lowered as `x[h:l] = x[h:l] op v` on C, C++
    and Python (it rendered C that did not compile and a Python module that
    did not import).
  - **W3** (R1) new `IntSemantics.int_shift_text` hook; `CIntSemantics`
    guards a shift whose amount may reach the C width:
    `((uint64_t)(n) >= 32u ? 0 : x << n)`, a signed `>>` clamps the amount
    to width−1 (sign fill). A shift amount known in range is unchanged (no
    golden moved). An amount containing a call is refused (it would be
    evaluated twice), as C's `op=` already refuses a call in its target.
  - **W2/W9** struct parameters are BY HANDLE on C (`T *v`, read as
    `v->x`/`(*v)`, called with `&q`) and C++ (`T &v`), per the user ruling
    (LRM 20.3.2); a `const` struct parameter stays by value
    (`c/lower_progseq.by_handle_params`). An argument that is not a
    variable is refused for a by-handle parameter. **This changes the
    WB DMA C/C++ API** (`transfer_single`, `configure_channel`,
    `transfer_single_start`, `write_descriptor` take a pointer/reference);
    goldens regenerated (diff reviewed: only these signatures and
    `cfg.x` → `cfg->x`), `tests/progseq/data/c/op_model_tb.c` passes `&cfg`.
  - **I5 → W14 (c), W15 (py)** ast2ir translates `(bool)x` as `x != 0`
    (bool is `bit[1]` in the IR, so a cast was the low bit).
  - **W14 (sv)** a size cast states the target's sign where the operand's
    differs (`signed'(8'(u))`); three WB DMA SV casts gained `unsigned'()`
    (golden diff reviewed).
  - **W10** SV `>>=` on a signed target is `>>>=`.
* 2026-10-16 — **P1.4 fixes, second batch:**
  - **W5** `foreach (v : a[i])` / `foreach (v : a)` on C, C++ and SV: the
    element is bound at the top of each iteration and `i` is the index
    (`body_walker.foreach_parts`, one reading of the three forms; a body
    writing the iterator -- an alias of the element, 20.7.8 c -- is
    refused, as Python already did). The case declares a local in the loop
    body too, which SV's declarations-first rule could have broken.
  - **W16 + E8** (R2) run-time errors, each target in its own idiom:
    C/C++ report through the message seam with an `error: ` / `fatal: `
    prefix (an unmatched `match` included; the default
    `--match-default message` form, per the ruling), and `fatal()` then
    calls the new platform function **`pssc_fatal(int status)`** /
    **`pssc::fatal`** (declared in `pssc_env.h`/`.hpp`, documented as
    not returning, needed only by a model that calls `fatal()`); Python
    raises the module's own `PssError`/`PssFatal` (new `errors` module
    section, emitted only when a body uses it); SV uses `$error` /
    `$fatal(1, ...)` and an unmatched `match` gets
    `default: $error(...)`. `trace_harness` turns each target's report
    into the trace line (`error: ...`/`fatal: ...`) and accepts a nonzero
    exit after one. `fatal()`'s status has no SV counterpart and is
    dropped there.
  - **W11 (print) + W20**: string literal escapes are decoded in ast2ir
    (`_unescape`, LRM 4.7 Table 4; triple-quoted strings are raw); SV's
    `print`/`error`/`fatal` formats are translated as `message`'s is
    (`_sv_format`). A `print` format's last `\n` is dropped on C/C++/Python,
    whose message seam ends the line itself; SV's `$write` is exact.
    Knock-on: **zuspec-be-sw** (`dm_async_generator._c_escape`) put a
    string constant into a C literal without escaping a newline, which
    only worked while ast2ir passed raw text; fixed there (c-host
    `test_target_bridge.py` green; be-sw's own suite has 4 pre-existing
    collection errors, unchanged by this).
  - **W4** `progseq_model.flatten_struct_bases` (called first in
    `build_model`, beside `comp_inherit.complete`) gives a derived struct
    its bases' members in place, keeping as `super` only the `packed_s<>`
    the chain reaches. New case: a register value derived from a packed
    struct, laid out base first, on all four targets.
  - **W6** C/C++ write an omitted defaulted argument at the call
    (`with_defaults`; a default that refers to anything is refused).
  - **W7 + W22 (new)** aggregate `==`: C spells it member by member
    (`_aggregate_eq`, refused past 64 scalars or with a call in an operand);
    C++ structs get `operator==`/`!=` (they had none: new defect W22,
    found by the new "struct equality" case).
  - **W8** a memory primitive's value is converted to its width on C/C++.
  - Goldens regenerated for these (diff reviewed: `error: ` prefix, Python
    error section, SV `default: $error`, C++ struct `operator==`, a
    `write32` value conversion spelled `& 0xffffffffu`).
* 2026-10-16 — **P1.4 fixes, third batch:**
  - **W19** ast2ir marks each function named `get_offset_of_instance[_array]`
    with whether its component derives from `reg_group_c`
    (`metadata["reg_offset"]`, `_mark_offset_functions`); `func_kind` reads
    it, so another component's function of that name is an operation. The
    matrix case now uses an `int` parameter (a string one is E1's business
    on C/C++). Corpus `types.string.match.001` passes on py and sv: removed
    from both strict lists.
  - **E9** an operation may call its group's offset function: the fold
    moved from the constructor emitters to `call_offset_fold` (C, Python)
    / `_offset_fold` (C++), which the constructor also uses.
  - **E12** `null` is address 0 on every target (`ExprTypes` types it as
    the 64-bit handle). **W13 reclassified: not a defect** -- every
    op-model target holds a `chandle` as an address, as `addr_handle_t`
    (a `typedef chandle`, 21.13) is; consistent across the four.
  - **W21** SV declares a component's `string` field.
  - **E4** `**` with run-time operands: SV maps it to its own `**` (Table
    13 is SV's); C/C++ call `pssc_pow` / `pssc::ipow` (new `static inline`
    / `inline` in the shipped `pssc_env.h`/`.hpp`; C++ now copies
    `pssc_env.hpp` whenever a body uses `**` or `fatal()`, even under
    `--message-style none`). Constant powers fold (C/C++ refused them too).
    The case now covers Table 13: `2 ** 31` in an int, negative exponents,
    a base of -1, and `2 ** 3 ** 2` = 64 (left-associative, EX-PREC.2).
  - **E3** procedural `in` on all four (`body_walker.in_as_compare`: the
    comparisons it means; values, ranges, and a fixed-size array; a value
    with a call is refused). New cases: a negative range, `in` over an
    array.
  - **W23 (new) + E6** C/C++ lower a `match` with a range arm, or with a
    `break` meant for the enclosing loop, as an `if` chain
    (`match_needs_chain`, `match_arm_test`, `_match_chain`; a subject with
    a call is held in a local first); Python's chain renders a range as
    `lo <= s <= hi`. Corpus `proc.match.ranges.001` passes on py: removed
    from its strict list.
* 2026-10-16 — **P1.4 fixes, fourth batch:**
  - **E5** struct aggregate literals on C (designated compound literal),
    C++ (positional aggregate, C++17) and Python (keyword constructor),
    every member the literal omits at its default; typed by where the
    literal goes (`IntSemantics.convert_to` -> `struct_literal_text`).
    New case: a literal assigned, nested, and passed to a `const`
    parameter. (C's `_model_call` rendered every argument untyped before
    choosing a callee; it now renders them against the callee's
    parameters, imports included -- 8.7.2.)
  - **E7** a struct with an array member on C (`T m[N]` member; a struct
    assignment copies it) and C++ (`std::array` member). The case now also
    gives the array member an initializer, which found: C/C++ refused it
    with an internal error (fixed: `_const_expr`/`_cpp_const` render a
    list), and **W24** -- SV dropped it, and Verilator 5.052 ignores it
    even when emitted (strict xfail on sv, see §2).
  - **I1, I2, I3 (+E2)** ast2ir refuses, with the line, a parameter or
    result whose type it does not translate (float, `ref T`, `type T`), a
    float local, and an expression with no IR form (a float literal,
    `compile has`); an unlocated expression takes its statement's line.
    Nothing is dropped any more. The IR pins became ordinary tests; the
    matrix float cases expect `line N: ... floating point`.
  - **E1 (C/C++) + E11** new `targets/unsupported_types.py` refuses a TYPE
    a target does not render, naming each use (`comp.field`,
    `comp::fn: parameter 'p'`, `comp::fn: local 'l'`, `comp::fn: result`),
    before the call gate. `OpModelTarget.unsupported_types` holds
    list/map/set for every op-model target (R4); C and C++ add `string`
    (R3). This is the first piece of the §4.4 gate pre-screen.
  - Remaining open from §2: W18 (P2), W24 (Verilator), E1 string methods on
    py/sv, E10 channel arrays. Defects closed so far: W1-W11, W14-W17,
    W19-W23, E2-E9, E11, E12, I1-I5.
* 2026-10-16 — **P1.4 fixes, fifth batch:**
  - **W11 closed** `format()` (a solve function, 21.1.2): SV renders it
    through `_sv_format` (`$sformatf` with the format translated); Python
    renders it with `_pss_fmt` (it was refused with a stale reason --
    "implements no translation" -- though the module carries the PSS
    formatter). C/C++ refuse it through its `string` result (R3). New case
    "format in a constructor".
  - **E10** an array of channels on all four: `progseq_model` treats one
    as a channel field (`channel_dtype`, `channel_count`); C declares
    `pssc_chan1 ch[N]` and initialises each, C++ a `std::array` of
    `pssc::chan1`, Python a list of `Chan1`, SV an unpacked array of
    handles each `new()`ed; a call reaches `ch[i]` by run-time index.
    (Python's emitter read `loop_vars` before setting it once a channel
    index had to be rendered during construction; reordered.)
  - `unsupported_types` skips core-library structs: a `string` member of
    `addr_reg_pkg::transparent_addr_region_s` is not the model's use of a
    string, and the call gate already refuses `add_region` on C/C++.
