# Construct test audit and plan: data types, expressions, functions, procedural statements

Status: **in progress** (started 2026-10-16, tree `394f75d`). Rulings R1–R3,
R4, R5, R9, R12, R13 decided (§7); R6-R8, R10, R14-R16 open. Progress is logged in §8.

**Where it stands (2026-10-16):** P0.2, P0.3 done; P1 done but for two
items carried forward -- string METHODS on py/sv (E1, to P3 strings: the
call gate classifies by name and cannot yet type a receiver) and W24 (a
Verilator limitation; declaration-site workaround open). P0+P1 committed
(`e2c801f`). **P2 done** (`tests/unit/ir/`, 180 tests, §8): the
expression and data-type kind registries, the ast2ir None catch-alls, W18,
and six new IR defects found and fixed (I6-I11). **P3 done** (§8): the
expression grid (194 rows, pairwise, against an independent 8.7 oracle),
~70 hand-written cases, the WB DMA operations traced on all four targets,
string methods on py/sv (E1 closed), and 16 new defects, 14 fixed (W25-W33,
E13-E16, E18) and two pinned as strict xfails (W31, E17). **P4 done**
(§8): all 100 interaction patterns mapped on every target (93-94 covered,
the rest `open` with a defect or ruling), enforced by `test_coverage.py`;
W34-W36, W41, I12 fixed; W37, W38, W40, W43, E19 pinned; new rulings R12,
R13 needed. **P5.1 and P5.2 done** (§8): every refusal is located
(`file:line:col`, also as markers), the gate pre-screens IR kinds per target
against an emitter-kind registry, E20/E22/E23 fixed, E21 pinned. **P5.3
done** (§8): coverage is enforcing -- all 596 sub-constructs mapped on every
target (530-539 proved per target), 239 -> 442 matrix cases, a new
`test_m_neg.py` for LRM errors, and ~75 new defects pinned (I13-I18,
W46-W66, E24-E42, M1-M6, F1-F32, H1); rulings R14-R16 needed. Next: fix the
silently-wrong ones (I13, I14, I15, W46-W50, W53, W55, W63), file F1-F32
with pssparser, then P6.

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
"M" = a located refusal giving the wrong reason.
"F" = the front end accepts what the LRM makes an error (table below).
"H" = a gap in the test harness, not in pssc.
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
| W25 | c, cpp | `w[63:0] = x ** y`: the source of a part-select assignment | evaluated at its own width, not the select's (8.7.2): `bit[4] 3 ** 7` gave 0xb, not 0x88b | P3 grid |
| W26 | cpp | assignment to an array element | not converted to the element type (8.7.2): no width propagated in, `bool` storage for `bit` | P3 grid |
| W27 | sv (Verilator) | a compound integer `message` argument (`x ** y`, `4'(w) + 4'(15)`) | Verilator 5.052 evaluates it too wide in `$display`, even under a size cast; held in a temporary of its PSS type now (IEEE-identical) | P3 grid |
| W28 | sv | an unsigned or sized literal (`'d12`, `8'hFF`, `0x10`) | rendered as a plain decimal, a signed 32-bit `int` in SV: `-'d12 / 3` divided signed | P3 |
| W29 | c, cpp, sv | the most negative value `/ -1`, `% -1` | C: undefined behaviour (x86 traps); SV (Verilator): 0. PSS wraps: -2**31 and 0 | P3 |
| W30 | c, cpp | a comparison its operands' ranges decide (`-x < 0`, x unsigned) | did not compile: `-Werror=type-limits` | P3 |
| W31 | c, cpp, sv (assignment); all (parameter) | a derived struct where its base is expected (8.3, 20.3.2) | C/C++/SV flatten struct inheritance, so the types are unrelated; Python copies a derived struct's members into a base one. **Open** (strict xfail) | P3 |
| W32 | py | `continue` in `repeat ... while` | jumped past the bottom-tested condition: the loop ran on (20.7.11 d) | P3 |
| W33 | c, cpp, py, sv | `make_handle_from_handle(h, off, sub)` | C/C++: a computed offset rendered unbracketed, so the 32-bit mask covered the whole address; all four ignored `sub` | P3 |
| E13 | all | a struct literal on the right of `==`/`!=` (8.5.3, Example 33) | internal error: nothing typed it from the left side | P3 |
| E14 | sv | a struct literal assigned to an array element | "needs a struct type from its context" | P3 |
| E15 | all | array `size()`/`sum()` (7.9.2.2) | refused by the call gate as unknown names | P3 |
| E16 | all | `in` over an array slice (`v in a[lo..hi]`) | internal error | P3 |
| E17 | c, py | `try_get` into a field or an array element (IP-54) | refused with an unlocated ValueError ("must be a local variable"). **Open** (strict xfail) | P3 |
| E18 | cpp | an operation two components down (`dma.ch[1].f()`) | "has no lowering in the C++ target" | P3 (WB DMA) |
| I5 | ast2ir | `bool` is `bit[1]` in the IR; `(bool)x` is `Cast(bit[1], x)` | a bool cast truncates to the low bit (7.12.1 b: nonzero is true). Root of W14 (c, sv) and W15 (py) | P2 |
| I6 | ast2ir | open domain bound (`bit[4] in [6..]`, `int in [..2]`); an enum domain (`e_e in [A..B]`) | **silently wrong**: `[6..]` constrained to the single value 6, `[..2]` vanished; an enum domain failed as "Failed to translate field type", no line | P2 |
| I7 | ast2ir | triple-quoted string with mustache expressions (4.7.1) in a body | **silently wrong**: the raw text (`v={{n}}`) reached every target | P2 |
| I8 | ast2ir | `function ... f(...); import target function f;` (20.4.1, the two-declaration import) | both declarations dropped; a call to `f` named nothing | P2 |
| I9 | ast2ir | array sized by a component `static const` or by a constant expression (`N + 1`) | `size=-1`, no error | P2 |
| I10 | ast2ir | an import prototype's float parameter, variadic parameter, default | parameter dropped (I1 for imports); `int ... args` became an ordinary parameter; default dropped | P2 |
| I11 | ast2ir | `u_c::sf(2)` (static function by its type); a method of a literal (`"a".size()`) | None: `int x = u_c::sf(2);` lost its initializer silently | P2 |
| W34 | c, cpp, py, sv | a `match` whose choices overlap (constant, or a range bound known at run time) | **silently wrong**: the first matching choice ran; 20.7.10 d makes more than one match an error. Fixed: `targets/match_overlap.py` puts an `error()` before such a match (IP-36) | P4 |
| W35 | c, cpp, py, **bc** | `%d`/`%u`/`%x` of a `bool` | printed -1: formatted as a one-bit value, which `%d` reads as signed (7.12 b: true is 1). Fixed in pssc's two formatters and in zuspec-be-bc's `interp/fmt.py` (which `_pss_fmt` is held equal to) | P4 |
| W36 | c, cpp, py, sv | `foreach (c : comps[i])`, an iterator over an array of components | c/cpp/sv: internal error ("unsupported type for DataTypeComponent"); py: refused. Fixed: `targets/comp_foreach.py` rewrites it to the index form | P4 |
| W37 | py, sv | a sub-string past the end (`s[4..3]` of `"abcd"`) | empty string, no error (8.6.3 makes the positions a "shall"). **Open** (strict xfail), waiting on R12 | P4 (IP-70) |
| W38 | py, sv | `s + "c"` on strings | accepted (8.4 Table 10 has no string `+`): Python concatenates, SV emits a `+` Verilator rejects. pssparser does not check operand types. **Open** (strict xfail), for pssparser | P4 (IP-71) |
| W39 | c, cpp | a call argument with side effects (`message("%d %d", ch.try_put(1), ch.try_put(2))`) | evaluated right to left (C leaves the order unspecified); Python and SV go left to right. Waiting on R10; no case pins it yet | P4 |
| W40 | all | a `const` struct parameter passed to a non-const formal (20.2.3) | accepted; the callee writes the caller's struct. pssparser does not check it. **Open** (strict xfail), for pssparser | P4 (IP-65) |
| W41 | c, sv | `%d` of a channel's `try_put`/`try_get` | c: "cannot determine its PSS type"; sv: -1. `ExprTypes` now types them `bool` | P4 (IP-55) |
| W42 | all | a part select with non-constant bounds (`v[4*i+3:4*i]`) | 8.6.1 requires constant bounds; pssparser accepts it, and pssc refuses it with an unlocated ValueError (an internal error) | P4 |
| W43 | bc | a solve function reached from `exec body` through an unqualified function | runs; 20.2.1.3 makes it an error (the op-model gate refuses it). **Open** (strict xfail in `test_bc_interaction_patterns.py`) | P4 (IP-97) |
| W44 | bc | an `exec body` writing a component field | runs; R13 refuses it (20.7.3 b, 9.1.6). **Open** (strict xfail in `test_bc_interaction_patterns.py`); `test_bc_comp_array.py` relies on it | R13 |
| W45 | c | a private solve function inherited by a component that never calls it | a `static` function, so `-Werror=unused-function` failed the build; now `static inline` | R13 rewrite |
| E19 | c, cpp, py | a channel deeper than 1 | refused, but with a ValueError (internal error), not a located refusal; SV runs it. **Open** (strict xfail) | P4 (IP-55) |
| I12 | ast2ir | an index of a call's result, `g(7)[2]` | **silently wrong**: translated as `g[2](7)` (the element's subscripts were applied before its call). Fixed: `_apply_call_and_subscripts` | P4 (IP-74) |
| E20 | c, cpp, py, sv | an array compared with an array literal (`v == {1, 2}`, 8.5.3) | internal error ("defines no expr_list()"). Fixed: `array_methods` lowers it to element comparisons, row by row; a literal of another length is a located refusal | P5 |
| E21 | c, cpp, py, sv | struct literals as the arms of `?:` (8.4.3, EX-COND.5) | internal error. Now a located refusal from the gate's pre-screen (`body_kinds`). **Open** (strict xfail) | P5 |
| E22 | c, cpp, py, sv | `v in {a, b}`, a value-list literal as the set of an `in` (8.5.9.1) | internal error ("`in` over a value is not lowered"). Fixed: `in_as_compare` compares each item | P5 |
| E23 | py | an array literal passed for an array parameter, or returned as an array result (20.3.2) | internal error. Fixed: `convert_to` renders it as a new list (C, C++, SV refuse array parameters, R8) | P5 |
| W46 | c, cpp, py, sv | a value of a domain-restricted type (`bit[5] in [1,2,4] x;`, `int in [-10..-5] y;`, an enum domain) left uninitialized (7.2.1 d, 7.5.1) | **silently wrong**: 0 / the first item; the LRM's default is the smallest domain value / the first declared item inside the domain (I6 fixed the bounds, not the default) | P5.3 |
| W47 | c, cpp, py, sv | a `static` component function redefined in a derived component, called from an inherited body (20.2) | **silently wrong**: dispatched virtually (`d.g()` runs `der_c::f`); a static call is not polymorphic. sv renders a virtual task | P5.3 |
| W48 | c, cpp, py, sv | a register group whose offsets come from `get_offset_of_path` (21.14.2) | **silently wrong**: ignored, the register lands at offset 0; a group defining all three offset functions (Neg) is accepted | P5.3 |
| W49 | c, py, sv (cpp not run) | `packed_s<BIG_ENDIAN>` as a register value (21.13.1) | **silently wrong**: laid out little-endian | P5.3 |
| W50 | c, py | `reg_c<R>` with a packed struct `R` and no `SZ` (21.14.1: `SZ` defaults to `8*nbytes`) | **silently wrong**: accessed with 32-bit primitives (16 expected) | P5.3 |
| W51 | c, cpp, py, sv | `sizeof_s<T>::nbytes` / `::nbits` (21.13.1) | not folded: rendered as `sizeof_s.nbytes` (py NameError; c/sv will not compile); `%d` of it refused as untyped | P5.3 |
| W52 | c, cpp, py, sv | `nullhandle` (21.13.3) | not lowered anywhere: an undefined name (py NameError) | P5.3 |
| W53 | py (c: E40) | a channel of a struct (21.9.1: copied by value) | **silently wrong**: the queued item aliases the local, so a write after `try_put` changes it | P5.3 |
| W54 | c, cpp, py, sv | a parameterized packed struct as a register value (`std_pkg::float32_s`, a user `t_s<8>`) | py **silently wrong** (only the first field packed); c/cpp/sv: unlocated "65 bits" | P5.3 |
| W55 | c, cpp, py (sv right) | a `match` subject (8.5.9.1: context-determined with its choices, as `in`) | **silently wrong**: evaluated at its own width; `bit[2] x=2,y=2`: `(x+y) in [4]` is true but `match (x+y)` takes `[0]` | P5.3 |
| W56 | sv; py, c | a value-returning import | sv: declared a `task`, called as a function, so the package does not compile; py/c: a message argument that is an import's result is refused as untyped | P5.3 |
| W57 | c, cpp | a non-void function whose end is reachable (20.7.5 leaves the value unspecified) | the build fails with `-Werror=return-type`, no location; emit a default return or refuse with one | P5.3 |
| W58 | c, cpp, py, sv | division / modulus by zero, a negative shift amount, at run time (8.5.1, 8.5.7) | py: an uncaught `ZeroDivisionError`/`ValueError`, not an `error:` line; C/C++ undefined. Waiting on **R14**; constant forms in F31 | P5.3 |
| W59 | c, cpp, py, sv | a read of a WRITEONLY / write of a READONLY register (21.14.1) | not refused by the gate: c an unlocated `LegalityError`, cpp/sv render it, py calls an accessor it never defines | P5.3 |
| W60 | c, cpp, py, sv | a target function whose short name some unrelated component uses for a solve function (Ex 297) | refused from target code: solve-ness is classified by short name across the model | P5.3 |
| W61 | c, cpp, sv | a write of a `foreach` index (20.7.8 g) | accepted and generated (py refuses it) | P5.3 |
| W62 | c, cpp | a comparison its operand's range decides (`u == -1`, `u` a `bit[8]` parameter) | folded to a constant (rightly), leaving the parameter unused: `-Werror=unused-parameter` fails the build (W30's sibling) | P5.3 |
| W63 | sv | a root-scope name, `::G`, with a local `G` in the same task | **silently wrong**: spelled `G`, and the local (hoisted to the top of the task) hides the `localparam` for every read, even before its declaration: `0`, `1 1` instead of `7`, `1 7` | P5.3 |
| W64 | sv (Verilator) | `%d` of a field of a non-constant packed struct (`$signed(v.x)`, `x` the 7-bit field at bits 7:1) | Verilator 5.052 evaluates it from the wrong bits (0 prints 128); the generated SV is right (a standalone 20-line SV file reproduces it). Workaround as W27: hold the field in a temporary of its PSS type. Pinned by `test_m_channels.py` "channels of a struct, an enum and a bool" (a struct taken from a mailbox: `q.x` prints the whole value 0x43; probably the same bug, not reduced further); "a bool field of a register value" prints with `%x` instead | P5.3 |
| W65 | cpp | an array index narrower than the array's size needs (`bit[2]` for 4 elements) | R12's bounds check `(unsigned long long)(j) < 4u` fails the build with `-Werror=type-limits` (W30's sibling); c passes | P5.3 |
| W66 | c, cpp | `in` over a component array (`s[1] in s`) | compares the component structs by value: does not compile | P5.3 |
| E24 | c, cpp, py; sv | a domain-typed FIELD (integer or enum) | py/c/cpp: internal error (`'NoneType' ... 'args'`); sv renders the domain as an invalid `_range_0` task | P5.3 |
| E25 | c, cpp, py, sv | a type-parameterized struct (`struct t_s<type T> { T f; }`) | py/c: its field refused as untyped; cpp/sv: "unsupported struct member type DataTypeRef" | P5.3 |
| E26 | c, cpp; all | a `static const` struct | component scope: c/cpp "unsupported field initializer ExprStructLiteral"; package scope: every target "ExprRefPathStaticRooted, which has no translation" | P5.3 |
| E27 | c, cpp, py, sv | import signature types (20.4.1 c): `string` (c/cpp, R3), `list<string>`, `bit[65]` | internal errors instead of the located R3/R4/width refusals (py accepts `list<string>`) | P5.3 |
| E28 | c, cpp, sv; py | a nested packed struct or an array field in a register value (21.13.1, Ex 360) | c/cpp/sv: unlocated "is not a scalar, so it has no width"; py crashes at run time | P5.3 |
| E29 | sv | `sized_addr_handle_s` in a struct | unlocated "unsupported SV struct member type DataTypeChandle" | P5.3 |
| E30 | all | a `typedef` inside a struct, used there | refused with a location, plus a second UNLOCATED "unresolved type reference" | P5.3 |
| E31 | py | `{}` assigned to a fixed-size array | internal error ("defines no expr_list()"); the element-count gate misses an empty literal | P5.3 |
| E32 | c, cpp, sv | a function returning a component without `ref` (F13), even uncalled | unlocated "unsupported C type for DataTypeComponent" | P5.3 |
| E33 | c, py | a call on a base instance of a function only a derived type defines (20.2.7) | unlocated ValueError (cpp/sv generate code) | P5.3 |
| E34 | all | `channel_c` with `DEPTH` 0 | refused, but unlocated | P5.3 |
| E35 | c, cpp, py; sv | `set_handle` on a sub-group (21.14.3) | internal errors; sv accepts it | P5.3 |
| E36 | c, cpp, sv | a `mem_access_desc_s` argument to a memory primitive, no executor (21.13.9) | c: unlocated arity error on any descriptor; cpp/sv: "unsupported expr ExprList" on `{}`. py ignores it, as it should | P5.3 |
| E37 | sv | `%n` of an int | unlocated ValueError (c, cpp, py refuse it with a location) | P5.3 |
| E38 | sv | an override with a different signature (F15) | unlocated "shadows ... with a different signature" | P5.3 |
| E39 | py | a `reg_c` in a plain component (F27) | unlocated "no default value for ... DataTypeRegister" | P5.3 |
| E40 | c | a channel of a struct | unlocated ValueError | P5.3 |
| E41 | c, sv | a `foreach` value iterator over a two-dimensional array (a row) | internal error, "unsupported type for DataTypeArray"; cpp and py work | P5.3 |
| E42 | all (ast2ir) | a template string initializing a `static const` (`static const string S = """c={{K}}""";`) | refused with the template-string message, but with no line | P5.3 |
| I13 | ast2ir | an integer width that is not a literal: a `static const` (package, component, struct), a constant expression (`bit[2+3]`), an enum item, a template value parameter, a pure call (`bit[sq(2)]`) | **silently wrong**: `bit[32]` (`ast2ir.py` reads only a literal width and defaults to 32). Array sizes fold correctly | P5.3 |
| I14 | ast2ir | a select of a `static const` | **silently wrong**: a bit select is dropped (`C[0]` is `C`), and any select of a package-qualified one (`k::C[7:4]` is `C`; elsewhere "no translation"); an array sized by it, a `compile if` and a `match` choice go wrong with it | P5.3 |
| I15 | ast2ir / linker | template struct specializations (`s_s<4>`, `s_s<8>` of `struct s_s<int N> { bit[N] f; }`) | **silently wrong**: one type with a 32-bit `f` | P5.3 |
| I16 | ast2ir | a package-scope `import class` | dropped silently; a field of its type gives an unlocated "unresolved type reference" | P5.3 |
| I17 | ast2ir | a package-scope target-template function | dropped silently; a call to it is refused as "declare it as an `import`" | P5.3 |
| I18 | ast2ir | an array size given by an enum item (`(int)sz_e::FOUR`) or a pure call (8.2 constant expressions) | refused as "does not fold" (located, but wrong: they are constant) | P5.3 |
| M1 | all | `%p` of a chandle | refused, "this model has no ... chandle values", though it has one | P5.3 |
| M2 | all | `get_handle()` (21.14.4), `executor()`, `get_context()` | "no function named X is declared by PSS": the core library declares all three | P5.3 |
| M3 | py, sv | array `join` / `str_from_chars` (7.9.2.2) | refused as unknown names, like E15 was | P5.3 |
| M4 | py, c | a constant out-of-range select as a `message` argument | "cannot determine its PSS type" instead of the out-of-range text | P5.3 |
| M5 | all | exec placements the LRM forbids (F22) | refused as "not lowered" / "not supported yet", not as illegal | P5.3 |
| M6 | all | `size()` of an array-typed `foreach` iterator (a row) | refused as an unknown function; `m[0].size()` works | P5.3 |
| H1 | harness | user import functions | the trace harness supplies none (py `MemoryBus`, C/C++/SV mains implement the core seam only), so no case can call an import: DT-CHND.3, DT-INT.8 (sv), FN-IMPORT executed | P5.3 |

**Front-end checks missing (F).** The LRM makes each of these an error;
pssparser accepts it, and pssc then runs it, generates it, or refuses it for
an unrelated reason. Per the fix-at-source rule these go to pssparser (a
request list like `pssc-requests-2026-10-01.md`); a pssc gate check is a
stopgap only where a target would otherwise miscompile. W38 and W40 are
members of this class.

| ID | Construct (LRM) | Today |
|---|---|---|
| F1 | operand and condition types (8.4 Table 10, 8.5.2, 20.7.7 a, 20.7.9): relational on bool/string, chained `a < b < c`, bitwise on bool, chandle arithmetic and `c == 1`, a non-bool `if`/`while`/`?:` condition, `int x = null`, `%` on a float | accepted and run (W38's root) |
| F2 | enum values (7.5.1 c/e/j): duplicates, outside the base type, negative with an unsigned base, implicit past the base's max, a base with a domain, a variable of an itemless enum | accepted; a duplicate makes `%n` print the other name |
| F3 | an unqualified enum item where the expected type is not the enum: `(int)BLUE` (Ex 37), `M1 == e`, `M1 in [...]` | accepted |
| F4 | casts (7.12.1 a): from a chandle, struct or string; to a struct | accepted; `(p_s)3` crashes py at run time |
| F5 | `rand chandle`, `rand float64` (7.1, 7.7); `randomize` of a chandle | accepted (float later refused by R5) |
| F6 | access protection (17.4, 20.4): a private/protected field written or read from outside | accepted |
| F7 | `bit[0]` (7.2.1 c) | accepted |
| F8 | assignment to a `static const` (scalar, element, member) (7.1, 7.9 a) | accepted; py renders `5 = 2` in a target function and really modifies it in a constructor |
| F9 | assignment to a sub-string, `s[0] = "x"` (7.6.2) | accepted; py raises TypeError at run time, sv emits it |
| F10 | assignment to a `const` scalar parameter (20.2.3) | accepted and run (W40 is the struct case) |
| F11 | a varargs or generic (`type`, `struct`, `numeric`) parameter of a PSS-bodied function (20.2.5) | varargs accepted (py TypeError at run time); generic refused only as "not supported" |
| F12 | an array argument of another size than the formal (8.3) | accepted; py runs it |
| F13 | a function returning a component without `ref` (20.2.2) | accepted (then E32) |
| F14 | a `target` declaration with a `solve` definition (20.2.1.3) | accepted |
| F15 | declaration/definition and override consistency: `const` and default mismatches, a non-pure function made `pure`, an override with another signature, an instance function shadowed by a static one (20.2.3 c, 20.2.4 c, 20.2.6) | accepted (PSS009 checks defaults on a redeclaration only); sv E38 |
| F16 | `break`/`continue` outside a loop (20.7.11 b) | accepted; py writes a module that does not import |
| F17 | modifying the collection a `foreach` iterates (20.7.8 g) | accepted and run |
| F18 | two `default` arms in a `match` (20.7.10 f) | accepted; py runs the second |
| F19 | a type-scope constant in a `compile if` condition (19.1.3 b) | accepted |
| F20 | `yield` in a solve function (20.7.14 a) | accepted |
| F21 | `return expr;` in an exec block (20.7.5) | accepted silently |
| F22 | exec placements: `pre_solve`/`body` in a component (20.1.2), a target-template exec of a solve kind (20.5), solve-only `file_*` from target code (21.2), memory/channel calls from solve code, a target-template function called from an init exec (20.1.2) | accepted; pssc refuses them as unsupported (M5) |
| F23 | collection Negs: `k in m` on a map, a subscript or slice of a set, `rand map`/`set`, a modifying collection method in a constraint, a `ref` field in a struct, `const ref`, a cast to a typedef of a struct, `instance` on a non-ref | accepted (all refused on op-model under R4 / `ref`) |
| F24 | a method of a string-array element, `s[1].size()` | PSS002 "the enclosing scope is unknown" (a resolution bug, not a check) |
| F25 | `addr_value` called from solve context (21.13.5) | accepted |
| F26 | import/export parameter types (20.4.1 c, 20.4.2.1): `bit[65]`, `list<string>`, a non-const struct to an export | accepted (then E27) |
| F27 | a `reg_c` outside a `reg_group_c`; `extend component reg_group_c` (21.14.1) | accepted (py E39) |
| F28 | `channel_c<string>` (21.9.1) | accepted (py) |
| F29 | a struct on the left of `in` with a range list (8.5.9.1) | accepted; py evaluates false |
| F30 | `==` / assignment of component arrays (7.9.2) | accepted; py runs it |
| F31 | a constant zero divisor, a constant negative shift amount (8.5.1, 8.5.7) | accepted |
| F32 | `error()`/`fatal()` with a string not known at solve time, in target code (21.3) | accepted on py/sv; waiting on **R15** |

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

* **R12** (2026-10-16) — an out-of-bounds array index is a run-time error
  on every target. Sub-strings are **not decided**: 8.6.3 says the
  positions "shall be between 0 and the size of the string less than 1"
  and start ≤ end, but, unlike 8.6.2 for arrays ("it shall be illegal to
  access an out-of-bounds index"), it states no consequence; an empty
  string has no legal sub-string at all. W37 stays pinned; PSWG question.
* **R13** (2026-10-16) — refused: an operation model must be legal PSS, so
  target code does not write component fields (20.7.3 b, 9.1.6). Run-time
  state goes in a local, a parameter, or a channel (each read a
  `try_get`/`get`, each update a `try_put`/`put`); tests may use the
  channel form, production models should not need it.

R6, R7, R8, R10 and R14-R16 (P5.3) are still open; work proceeds on the
suggested defaults only where a case does not depend on them.

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
| R10 | Argument evaluation order and compound-assign LHS evaluation count | IP-44 and side-effect cases; **P4 found C/C++ evaluate call arguments right to left (W39)** | left-to-right, LHS once; test only where pssc commits to it |
| R12 | Out-of-range positions: a sub-string past the end (8.6.3), and likewise an array index or bit select out of bounds (8.6.1 "illegal") | W37, IP-70 | a run-time error on every target, as R2 for `match` |
| R13 | A target function writing a component field. 20.7.3 b allows it only from `init_down`/`init_up`; 9.1.6 says even a `mutable` field "shall not be modified ... from target exec blocks". Every op-model target and bc accept it, and many matrix cases keep counters in component fields | IP-85 | keep it as a documented op-model extension (a programming-sequence driver keeps state in its component), or refuse it and move the matrix's counters to locals and parameters |
| R14 | A run-time zero divisor (8.5.1 "illegal") or a negative shift amount (8.5.7 "shall be non-negative") | W58; EX-ARITH.4, EX-SHIFT.3 `open`. Today py raises an uncaught Python exception, C/C++ are undefined | a run-time error on every target, as R12 does for an index (an `error:` line, not a crash); constant forms refused by the front end (F31) |
| R15 | `error()`/`fatal()` in target code with a string not known at solve time (21.3: "shall be solve-time known" on the target) | F32; LIB-ERR.3 `open`. py/sv accept it | enforce: refuse with a location in the gate (the restriction exists for platforms with no string formatting at run time) -- or keep as a documented extension |
| R16 | Unary `+` (Table 22 lists it, 8.1's `unary_operator` does not) | EX-ARITH.9: pssparser accepts it and every target runs it as the identity | keep accepting (identity); PSWG question |

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
| `string` locals, fields, params, results | op-model-c, op-model-cpp | R3 | `test_m_strings.py` (every case, `refused=`) | refused by type (`unsupported_types`); sv/py render `==`, `!=`, `match`, sub-strings and the methods `size/find/find_last/lower/upper` (E1, closed in P3) |
| string methods returning a list (`find_all`, `split`, `chars`) | op-model-py, op-model-sv (C/C++ refuse the type) | R4 | `test_m_strings.py` "string methods that return a list" | refused by the call gate as returning a list |
| list / map / set | all op-model targets | R4 | `test_m_refusals.py` "a list", "a map", "a set" | refused by type, naming each use; **a support strategy is wanted soon** (user, 2026-10-16) |
| `float32` / `float64` | all (refused in ast2ir) | R5 | `test_m_refusals.py` "floating point", "a floating-point parameter ..."; `tests/unit/ir/test_ir_expressions.py` | located (`line N:`) |
| `ref T` parameter, `type T` parameter | all (refused in ast2ir) | (I2) | `test_ir_expressions.py::test_a_parameter_is_never_dropped` | was dropped from the signature |
| `compile has` in a procedural body | all (refused in ast2ir) | (I3) | `test_ir_expressions.py::test_compile_has_in_a_body_is_refused` | |
| an import's `output` / `inout` parameter | all op-model targets (the gate) | (W18) | `test_m_refusals.py` "an import's output parameter" | ast2ir records it (`metadata["param_dirs"]`); every op-model target passes a scalar by value |
| a triple-quoted string with mustache expressions or directives, in a body | all (refused in ast2ir) | (I7) | `test_ir_expressions.py::test_a_template_string_is_refused_not_taken_as_text` | 4.7.1 expansion at evaluation |
| `comp::sf(...)`: a static component function by its type | all (refused in ast2ir) | (I11) | `test_ir_functions.py::test_a_static_function_by_its_type_is_refused_with_its_line` | the unqualified call works |
| `pyobj` (zuspec extension) | all (refused in ast2ir) | – | `test_ir_kind_registry.py` | |
| a register (`reg_c`) | bc | – | – | `'reg_c<None>' is not a component type`; found writing IP-49, whose register half is a matrix case |
| `super;` in an action's exec block | bc | – | – | "unsupported statement StmtSuper"; op-model-py runs it (IP-61) |
| `executor()` | all (bc refuses; op-model untested) | – | `test_bc_interaction_patterns.py::test_ip98_executor_is_refused` | IP-98 |
| `write_struct` / `read_struct` | all | – | `test_m_refusals.py` "write_struct of a packed struct"; `test_ip51_...` (bc) | IP-51 |
| `extend action` inside a component body | all (ast2ir) | – | – | "an 'extend' in component ... is not supported yet", with its line; at package scope it works |
| a channel deeper than 1 | op-model-c, -cpp, -py | – | `test_m_channels.py` "a channel of depth 3" (E19) | SV and bc run it |

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
* 2026-10-16 — **P2 done.** `tests/unit/ir/` holds 180 tests (was 78):
  - **Kind registries** (§4.4), `test_ir_kind_registry.py`: every
    pssparser `Expr*` and `DataType*` class, found by introspection, has a
    row -- translated, refused with its line, or not met on its own (and
    why). A row's PSS is checked to REACH the translator as that class (a
    spy on `_translate_expression_kind`/`_translate_data_type_kind`), which
    caught three rows that tested nothing.
  - **The None catch-alls.** `_translate_expression` and
    `_translate_data_type` are now wrappers: a kind that returns None
    without reporting is refused, located at the node or its statement or
    declaration. `_translate_data_type(what=, at=)` names the declaration
    (`field 'a' is floating point`), which closes **I4** (field refusals had
    no line and said "Failed to translate field type").
  - New files `test_ir_types.py` (35) and `test_ir_functions.py` (15); rows
    added to statements (foreach over list/set/map key, nested
    break/continue/return, `super;`), expressions (substring, enum cast,
    `null`, nested aggregate, collection/string/literal methods, `in` over
    a list, located reduction refusals).
  - **W18 closed**: ast2ir records `output`/`inout` per parameter
    (`Function.metadata["param_dirs"]`, beside `const_params`), and
    `validate_calls` refuses a call to an import with one on every op-model
    target. Defined functions with a direction are pssparser's refusal
    already.
  - **I6-I11 fixed**, each found by writing the rows (§2): open domain
    bounds take the type's bounds and an enum domain is a constraint like an
    integer one (`_translate_domain_range_list`); a template string is
    refused; the two-declaration import is translated from the prototype
    the linker resolves (`_translate_import_type`); an array size folds
    from the constant the linker resolved or from a constant expression,
    and a size that does not fold is refused; defined and imported functions
    share one signature reader (`_translate_signature`), so an import keeps
    its varargs and defaults and refuses a float parameter; a method of a
    literal is a call on it (`ExprMemberCall`).
  - `Field.is_const` is set for `const`/`static const` (was always False).
  - `bool` is still `bit[1]` in the IR (I5's root); the cast is fixed, and
    `test_ir_types.py` pins the representation.
* 2026-10-16 — **P3 done.** 188 matrix cases (was 98), 12 strict-xfail
  target cells. The matrix covers 52-53 of the 100 interaction patterns per
  target (`python -m progseq.matrix.coverage` from `tests/`).
  - **The expression grid** (§4.3): `matrix/grid.py` generates 194 rows
    covering every pair of values of five axes (operator × left type × right
    type × context × target type; 14 operators, 10 types, 11 contexts),
    grouped one case per operator in `test_m_grid.py`. Expected values come
    from `matrix/ref8_7.py`, an arbitrary-precision model of 8.7 that imports
    nothing from pssc and is itself checked against LRM Examples 42-45
    (`test_ref8_7.py`). Two choices make it sharp: operand values are chosen
    so the LRM's answer differs from a "compute in 64 bits" and a "never
    propagate a width" host, and a stored result is printed widened to 64
    bits, since `%x` prints at the PSS width and re-masked a wrong store.
    **Calibration:** re-introducing W1 (C++ `op=`) or W3 (C shift guard)
    fails the grid; before the two choices, W1 passed it.
  - **Hand-written cases** in every area file (ints, bool/enum/cast,
    struct, array, control, match, functions, library, channels, strings,
    refusals) -- the P3 table's list and the P4 "matrix now" patterns that
    fall in those areas, each tagged with sub-construct and IP IDs.
  - **WB DMA** (`test_m_wb_dma.py`): `write_descriptor`,
    `read_descriptor_residual`, `transfer_list`, `check_completion` (both
    paths), `configure_interrupt_routing` and `set_software_pointer` of the
    real model, driven from a `pss_top` through the matrix harness and held to
    one hand-worked trace on c, cpp, py and sv. `transfer_single` was already
    executed by the C/C++ behaviour tests.
  - **Fixed** (§2): W25-W30, W32, W33, E13-E16, E18. Shared lowerings, so
    every target gets them at once: `targets/array_methods.py` rewrites a
    fixed array's `size()`/`sum()` before the call gate (E15);
    `body_walker.in_as_compare` reads a slice (E16);
    `targets/string_methods.py` recognises a string method by its receiver's
    type for `ExprTypes` and the call gate (E1), with
    `OpModelTarget.supports_string_methods` (py, sv), Python helpers in
    `py/pss_str.py` emitted only when used, and SV's `pssc_str_find[_last]`
    in `pssc_reg_pkg.sv`. No golden snapshot changed.
  - **Open, pinned:** W31 (a derived struct as its base: needs the struct
    base chain kept past `flatten_struct_bases` and a copy-in/out or a cast
    per target) and E17 (`try_get` into a field or element on C and Python:
    a typed helper in C, a cell write-back in Python). W24 unchanged.
  - **pssparser defect, fixed at source (pssparser 65f6471, branch chained-method-calls):**
    a method of a built-in method's result (`s.lower().size()`,
    `s.split(",").size()`) reported PSS042 ("'size' was left unbound by
    pssparser"): `TaskResolveRefs::visitExprRefPathContext` stopped the path
    at the first string method. It now continues from the method's
    prototype's result: a string's methods are signature-checked, a
    `list<T>`'s name-checked, and a scalar's member is PSS004. Tests in
    pssparser's `test_string_method_signatures.py` (calibrated: six fail
    without the fix). pssc needed no change: `test_m_strings.py` "chained
    string method calls" runs on py and sv, and a chain on `split` is
    refused under R4. The same stop hit a member of a *collection* method's
    result (`l.to_list().size()`, `ls.front().x`); collection methods have
    no prototypes yet, so their results are classified
    (`collectionMethodResult`): a collection of the same elements (its
    methods name-checked), an element (typed by the collection's element
    parameter: a struct's members, a string's methods, a nested
    collection's methods), or nothing with members (PSS004). Tests in
    pssparser's `test_chained_collection_methods.py`. Collections remain
    refused on the op-model targets (R4).
  - Not done here: the R6/R7/R8/R10-dependent cases (enum ranges and
    relational, `urandom` beyond a one-value range, argument evaluation
    order); `test_m_refusals.py` patterns still name `comp::function`, not
    `file:line`, for gate refusals (the P1 location finding, P5).
* 2026-10-16 — **P4 done.** Every one of the 100 interaction patterns is
  accounted for on every op-model target, and
  `test_coverage.py::test_every_interaction_pattern_is_mapped_on_every_target`
  enforces it (calibrated: dropping one mapping fails it). Covered: 93-94 of
  100 per target (`python -m progseq.matrix.coverage` from `tests/`); the
  rest are `open` dispositions naming a defect or a ruling.
  - **Matrix cases** (~30 new): the plan's "matrix now" patterns still
    missing (IP-28, 35, 36, 46, 48, 49, 53, 55, 60, 64, 65, 74, 75, 88, 89,
    95, 97), and the "after a decision" patterns pinned as refusals on the
    targets that refuse them (IP-7, 26, 27, 34, 37, 43, 44, 51, 56-58, 63,
    72, 73, 92-94, 96, 100; R4 collections, randomize, `ref`), or executed
    where a target runs them (IP-69 strings on py and sv; IP-74 array
    results on py).
  - **`coverage.yaml`** now holds dispositions: `elsewhere` names a test as
    `path::function` (checked to exist), `n-a` a reason, and a new status
    `open` the defect or ruling a construct waits on; `open` does not count
    as coverage.
  - **bc** (`tests/unit/integration/test_bc_interaction_patterns.py`): the
    activity-side patterns IP-49 (parallel half), 60, 86, 87, 90, 97, and
    refusals of `executor()` (IP-98) and `write_struct` (IP-51).
    `test_op_model_py_super.py` gained Example 285 (IP-61: `super;` first
    and last, plus an `extend action` body).
  - **Fixed** (§2): W34 overlapping `match` choices (new shared pass
    `targets/match_overlap.py`: an `error()` before a match whose choices
    may overlap; a match with constant disjoint choices is untouched, so no
    golden moved), W35 `%d` of a bool (also in zuspec-be-bc,
    uncommitted on its `main`), W36 an iterator over a component
    array (new shared pass `targets/comp_foreach.py`), W41 `try_put`/
    `try_get` typed `bool`, I12 `g(7)[2]` read as `g[2](7)`; `ExprTypes`
    types `in` as `bool`.
  - **Open, pinned:** W37 (R12), W38 and W40 (for pssparser), W43 (bc), E19,
    plus W31 and E17 from P3. W39 (C/C++ argument order) waits on R10 and
    is not pinned by a case; W42 (non-constant part-select bounds) is a
    pssparser gap with an internal-error refusal in pssc.
  - **New open rulings:** R12 (out-of-range sub-string / index), R13
    (target functions writing component fields: 20.7.3 b and 9.1.6 forbid
    it, every target and bc accept it, and the matrix relies on it).
    Both ruled the same day; see the next entry.
  - Not done: IP-68 is `n-a` (21.1.3 leaves the count unspecified); IP-61 on
    SV has no test yet; C/C++ entries (P6) block IP-61 there.
* 2026-10-16 — **R12 and R13 implemented** (rulings in §7).
  - **R12** (arrays): `targets/index_bounds.py` marks every index of a
    fixed-size data array not known to be in range (a constant in range,
    or a `foreach`/`repeat (i : K)` index bounded by the array, needs no
    check); C/C++ render `a[(unsigned long long)(i) < N ? (i) : (error,
    0)]`, Python `a[i if 0 <= i < N else _pss_error(...)]`, SV
    `a[pssc_index(i, N)]` (new in `pssc_reg_pkg.sv`). An index containing a
    call is refused (it is read twice). Matrix: "an array index past the
    end", "a negative array index". No golden moved. Sub-strings stay
    pinned (W37): the LRM states the rule but not the consequence.
  - **R13**: `targets/field_writes.py`, run by the gate on every op-model
    target, refuses a function that writes a component field (its own, a
    sub-component's, an element or member of one, or `try_get` into one)
    when target code reaches it -- a `target` function, an entry, or a
    function either calls. The constructor, solve functions and init
    blocks may. Matrix: two refusals, "fields written while the component
    is constructed", and "run-time state kept in a channel" (the
    workaround). IP-85 is now covered by these cases.
  - 11 matrix cases and ~35 progseq tests wrote component fields from
    target code; each was rewritten to keep its point with locals,
    results, by-handle struct parameters, or writes moved into the
    constructor (`test_op_model_entry.py`'s model now reports through
    messages). Found on the way: W45. bc still accepts such writes (W44).
  - Full unit/progseq/compliance: 5316 passed, 87 xfailed.
* 2026-10-16 -- **P5.1 and P5.2 done** (P5.3, coverage enforcing, not
  started; see the end of this entry).
  - **Locations.** ast2ir now stamps `loc` (file, line, column) on every
    procedural statement, expression (the statement's when the parser left
    the expression unlocated), function, parameter, field, component and
    struct. `pssc.diag.at(nodes, text)` prefixes a diagnostic with the first
    location among `nodes` as `file:line:col: `, as a `Located` string;
    `CompileError.markers` reports them in pssparser's marker shape, so the
    corpus adapters read them. Every gate pass uses it: the call gate,
    `exec_blocks`, `array_signatures`, `unsupported_types`, `field_writes`,
    `array_literal`, `int_width` (which also now lists its places as errors,
    not inside its message), `group_binding`, `executors`, the register
    offset check (`OffsetFoldError.node`, set by `reg_layout`) and the
    register width check, and `reg_rmw`. Before, every gate refusal named
    `comp::fn` only; `validate_calls._where` had waited for ast2ir to fill
    `loc` since it was written.
  - **Refusals from inside a pass.** `diag.Refusal(text, node...)` is a
    `ValueError` that `OpModelTarget.run` reports as a located `CompileError`;
    the body emitters' `stmt` (C, Python, SV through `BodyWalker`, and C++)
    adds the statement being rendered as the fallback location. Converted:
    `in_as_compare`, `index_bounds`, `bit_select` (W42 is now a located
    refusal), `match_overlap`, `IntSemantics` (division by zero, negative
    shift, `**`, unknown member), `scan_output_locals` (E17 is located; still
    open), channel depth on C, C++ and Python (E19 is located; still open),
    the `foreach`/`repeat` index and iterator writes, and format checks
    (`message`/`print`/`format`) on all four. Twelve tests that expected a
    `ValueError` from a compile now expect a located `CompileError`
    (`conftest.assert_refused_at`).
  - **The matrix enforces it**: every line of every `Refused` case's
    diagnostic must begin with a location (`_matrix.LOCATED`). All 208
    refusal cells did once the passes above were converted.
  - **Gate pre-screen** (P5.1, §4.4): `targets/body_kinds.py` walks every
    body the target renders (`validate_calls.rendered_bodies`, now the one
    list the call gate also walks) and refuses, located, each node whose kind
    the target's body emitter does not render
    (`OpModelTarget.body_emitter_class`: its `BodyWalker` hooks, or C++'s
    `KINDS`), and each aggregate literal standing where nothing gives it a
    type. `tests/progseq/test_emitter_kind_registry.py` holds every
    `Stmt*`/`Expr*`/`TypeExpr*` class of the IR to a row per target
    (rendered, part, aggregate, refused, or never produced and why), checks
    the rows against the emitters, and holds C++'s `KINDS` to its cascades
    (calibrated: flipping a row fails it).
  - **Found and fixed by probing positions** (§2): E20 (array `==` an array
    literal: `array_methods` lowers it), E22 (`v in {a, b}`), E23 (Python:
    array literal as an argument or result). E21 (struct literals as `?:`
    arms) is now located and pinned as a strict xfail. Matrix cases: "in over
    a value-list literal", "an array literal as an argument and as a result",
    "an array compared with an array literal" (and "... of another length"),
    "struct literals as the arms of a conditional".
  - `test_m_refusals.py` has a located case for every construct in the P3
    list but two that do not apply: `exec pre_body` (an action's exec kind,
    which an op-model renders only through an entry) and entries on C/C++
    (refused for the `--export-action` option, not at a place in the model).
  - **Not done in P5.2:** ast2ir's own refusals still say `line N:` with no
    file (about 40 sites; the matrix accepts that form); the emitters still
    carry ~150 `raise ValueError` sites, some of them model refusals no test
    reaches yet -- the pre-screen and the matrix will find them one case at a
    time.
  - **P5.3 (coverage enforcing) is the remaining work**: 504 of the 596
    sub-constructs are mapped on no target (cases mostly tag the coarse ID),
    so enforcing needs a disposition or a sub-construct tag for each.
* 2026-10-16 -- **P5.3 done: coverage is enforcing.**
  `test_coverage.py::test_every_sub_construct_is_mapped_on_every_target`
  requires each of the inventory's 596 sub-constructs to be accounted for on
  every op-model target: a matrix case naming it in `covers=` (executed,
  refused, or a strict xfail naming its defect), or a `coverage.yaml`
  disposition (`elsewhere`, `n-a`, `open` with its defect or ruling). A
  coarse tag maps none of its rows. Calibrated: dropping one tag or one
  disposition fails it.
  - **How.** Eight read-only agents mapped the 504 unmapped rows by area
    (tag an existing case only where its PSS exercises the row's rule;
    probe pssc on op-model-py where unsure), then eight more wrote the
    proposed cases, each owning its files. Result: ~420 tags on existing
    cases, 67 dispositions (19 `elsewhere`, 24 `n-a`, 24 `open`), and
    ~200 new cases: 239 -> 442 matrix cases. The grid's extra tags
    (mixed-sign arithmetic, shift amounts of another width, each compound
    operator, `message` arguments) are computed from its rows
    (`grid.row_tags`), so a new seed keeps them true.
  - **New file `test_m_neg.py`**: models the LRM makes ILLEGAL, each
    expecting the error at its place; `test_m_refusals.py` keeps legal PSS a
    target refuses. Where pssc refuses an illegal model only as
    "unsupported" (M5, R4), the case expects the LRM's reason and is a strict
    xfail, so the missing check stays visible.
  - **Proved** (a case passing, or `elsewhere`/`n-a`): c 530, cpp 531,
    py 539, sv 534 of 596. The rest are strict xfails or `open`.
  - **Found** (§2, 70+ new rows): silently wrong on every target -- I13
    (any non-literal integer width is 32 bits), I14 (a select of a `static
    const` dropped), I15 (template struct specializations collapse), W46
    (domain defaults), W47 (static functions dispatched virtually), W48
    (`get_offset_of_path` ignored), W49 (`BIG_ENDIAN` ignored), W50, W53
    (py channel aliasing), W55 (`match` subject width, c/cpp/py), W63 (sv
    `::G` hidden by a local); E24-E42 internal errors; M1-M6 wrong-reason
    refusals; F1-F32 front-end checks pssparser does not make (for a
    pssparser request list); W64 a Verilator bug; H1 the harness cannot run
    imports. No defect was fixed in P5.3: each is pinned, not worked around.
  - **Corrected on the way:** inventory row ST-MATCH.2 (a match choice has
    no open bound); the refusal case "a map iterated into a set and a sum"
    used a `foreach` iterator as a map key.
  - **New rulings needed:** R14 (run-time zero divisor / negative shift),
    R15 (`error()` strings not known at solve time), R16 (unary `+`).
  - **Judgement calls to review:** F14 reads 20.2.1.3 / Ex 297 as making a
    `target` declaration with a `solve` definition illegal even uncalled;
    `%p` of a null chandle expects `p=0x0` (21.1.1 "including the preceding
    0x"); a `return;` in a base init block reached by `super;` ends only the
    base's part (LRM silent); `elsewhere` for EX-REF.3, DT-REF.11 and
    EXEC-KIND.4 on sv points at the parametrized corpus run, not a test aimed
    at the construct; ST-YIELD.3 counts a bc test, as IP-90 did.
