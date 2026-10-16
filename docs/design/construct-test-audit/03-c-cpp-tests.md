# 03 — C / C++ emission test catalog (op-model-c, op-model-cpp; c-host)

Scope: what pssc's tests prove about **emitting** DT-*, EX-*, FN-*, ST-*,
EXEC-* and LIB-* constructs (IDs from `taxonomy.md`) as C (`op-model-c`,
`src/pssc/targets/c/`) and C++ (`op-model-cpp`, `src/pssc/targets/cpp/`).
Parse/IR coverage is catalog 02; SV and Python emission are catalogs 04/05.
The older `c-host` / `c-embedded` / `sv-dpi*` family (zuspec-be-sw) is covered
briefly in §6: it emits action exec bodies, and pssc barely tests that.

Method (2026-10-16):

* Read every test file in the brief, plus each `tests/unit` and
  `tests/progseq` file that names `op-model-c`/`op-model-cpp`/`c-host`.
* Read the emitters: `c/lower_progseq.py` (a `BodyWalker` + `CallDispatch`
  subclass; hooks listed by `_BodyEmitter.hooks()`), `cpp/lower_progseq.py`
  (its own `_stmt_lines`/`expr` cascade, **not** ported to `body_walker`),
  `int_semantics.py`, `int_width.py`, `bit_select.py`, `array_literal.py`,
  `validate_calls.py`, `call_legality.py`, `op_model.py` (`OpModelTarget.check`).
* Ran `tests/progseq/test_op_model_construct_matrix.py -k "c- or cpp-"`:
  **126 passed, 0 xfail** (63 cases × 2 targets).
* **Probe**: ~75 small models outside the matrix, run through the matrix's own
  `trace_harness.run` (generate → `cc -std=c99 -Wall -Wextra -Werror` /
  `c++ -std=c++17 … -Werror` → execute → compare an access trace written by
  hand), with op-model-py as a cross-check where the expected value was
  arguable. The probe is what separates "handled", "refused with a location",
  "internal error" and "compiles and computes the wrong value" — the last kind
  is invisible to every text-checking test. Its results are in §4 and §5. (The
  probe script was ad hoc and is not checked in; every finding below states the
  PSS that reproduces it.)

Check-style legend used throughout:

* **T** — text grep of generated output (`assert "x" in text`).
* **K** — compiled `-Werror` (proves it is legal C/C++, not what it does).
* **G** — byte-exact golden snapshot (`tests/progseq/golden/`): proves
  *sameness*, never correctness.
* **X** — compiled **and executed**, with a behavioural assertion (an access
  trace, a printed value, a self-checking TB). The only style that proves
  semantics.
* **R** — refusal test (a `CompileError`/`ValueError` with a message pattern).

---

## 1. Per-file catalog

### 1.1 Executed (X) — the files that prove semantics

| File (tests) | Model | Constructs exercised | Style |
|---|---|---|---|
| `test_op_model_construct_matrix.py` (63 cases × c, cpp, py, sv) | Small `pss_top` per case + shared register groups | See §2 — the bulk of executed C/C++ coverage: register groups/handles, sub-components, construction, bit/part selects, match, ?:, PSS-width integers, arrays, struct defaults, repeat, solve functions, message, enums, >64-bit refusal | **X** (trace via `trace_harness`, C99/C++17 `-Werror`), **R** for 9 cases |
| `test_op_model_inherit_native.py` (5 cases × py, c, cpp, sv) | Component inheritance | FN-VIRT (virtual dispatch through a base `h()`), derived root, inherited register block, `super.f()`, a field declared in base and derived (`super.a`, `super.a += 1`), `super.initialize(...)` in a constructor; LIB-MSG `%d` | **X** (message + access trace). C renders the flattened view, C++ native classes |
| `test_op_model_behaviour_c.py` (5) | WB DMA component tree (`examples/op_model/pss`) | 5 TB cases in `data/c/op_model_tb.c`: `transfer_single` (→ `transfer_single_start`, `configure_channel`, `wait_completion`, `probe_status`, `wait_hint`), `stop_channel`, `pause_engine`, `set_auto_restart`. Reaches: ST-WHILE `while(true)`+ST-BRK, ST-IF, `!caps.ars`, enum returns/compare, struct-register read/write, `write_field` (masked RMW), channel `try_put`/`try_get`, ST-YIELD, ST-CIF, struct param, `(bit)` casts, per-channel stride. Plus mutation checks (dropped stride, poll count), footprint and `--gc-sections` | **X** (trace) |
| `test_op_model_behaviour_cpp.py` (3) | Same tree, same mock | Same 5 cases (`data/cpp/op_model_tb.cpp`); mutation: per-channel base, masked write that stops reading | **X** |
| `test_build_wb_dma_c.py` (2) | Narrow model `pssc/testing/models/dma_engine.pss` | `configure_channel`, `mem_to_mem_copy[_masked/_desc]`: struct-local field assigns, register array with run-time index (`regs.channels[channel]`), ST-RWHILE (`repeat {} while`), return inside loop, `/`; × 3 link styles × compilers. `--reg-style accessors` is **K** only | **X** (self-checking TB, "PROTOTYPE PASS"), K |
| `test_build_wb_dma_cpp.py` (1) | Same narrow model | Same operations | **X** |
| `test_c_message_format.py` (3) | Helper grid | LIB-MSG formatting: every `_SPECS` × int width/signedness × value, plus `%n` of enums, held to op-model-py's `_pss_fmt`; freestanding build | **X** (C and C++) |
| `test_c_imports.py` (15) | Small import models | FN-IMPORT: prototype + call without handle, solve/target imports, uncalled import still declared, weak `pssc_message` stub; refusals (call to nothing, component-scope import) | T, R; **X-weak** for link+run (exit 0, no value check) |
| `test_c_seam.py` (~27) | Seam headers | LIB-MEM seam mechanics (barrier counted at run time, `--addr-bits`, 32-bit refusal, selectable seam) — not model constructs | T, K, X (seam only) |
| `test_c_reg_map_functions_seam.py` | Register map | DT-REG placement under the functions seam, UBSan if available | **X** |

### 1.2 Text / compile-only (T, K) — prove spelling, not behaviour

| File (tests) | Constructs | Style |
|---|---|---|
| `test_c_op_model.py` (39) | WB DMA as C: struct per component, by-value sub-components, data members, sub-component arrays + count, folded offsets, register value types, access modes (no write accessor for READONLY), construction order, capability defaults, channel members, `write_field`→masked write, C string literals, discarded `try_get` stated; lifecycle/freestanding/32-bit | T; K (`-Werror`, freestanding, `-m32`) |
| `test_cpp_op_model.py` (28) | WB DMA as C++: class per component, two-phase construction, folded offsets, `enum class` with explicit values, enumerator by name in return/compare, aggregates with member initialisers, native register member path, mem primitives/address builtins as arithmetic, `this->` members, typed channels, yield→nothing, compile-if branch kept, match→switch+break, foreach→bounded loop, comments | T; K |
| `test_c_body_lowering.py` (26) | C body constructs one at a time: self-member through handle, unary `~ ! -` and bracketing, break/continue, `while(1)`, foreach bound, match→switch, `--match-default`, enum typedef, enum argument type, mem primitives, address builtins, a claimed built-in with no rendering refused, yield (default / `--yield import`) | T; K (each `*_compiles`); R |
| `test_body_walker.py` (27) | The shared walker: hook naming/dispatch, missing hook error, comment attachment, write-only and channel-output scans vs. the C emitter, C and SV hook sets | T / unit; R |
| `test_c_generate.py` (5), `test_c_target.py` (7), `test_cpp_target.py` (8) | Registration, value unions, baked accessors, export API, two-phase C++ construction, import interface | T |
| `test_c_reg_masked.py` (7) | LIB-REG `write_val_masked`: C accessor reads before writing; C++ native member call; unknown register method refused | T; K; R |
| `test_mem_access.py` (14), `test_style_policy.py` (25), `test_c_backend_class.py` (13), `test_sections.py` | Funnel/style/backend-class mechanics; RMW read cannot be dropped; policy cannot move an address | T; R |
| `test_c_names.py` (8) | C single namespace: refusals for `create`/`init`/`destroy` collisions, duplicate short struct/component names, enumerator prefixing | R; T |
| `test_param_shadows_field.py` (2) | EX-REF parameter shadows field — **C and C++ text only** (`->a * 2` absent); only op-model-py is executed | T (C/C++), X (py) |
| `test_op_model_channel.py` (C/C++ part: 6) | LIB-CHAN: depth-1 lowering (C and C++), blocking `get`/`put` refused by name, C refuses depth>1; C++ compile of the channel lowering | T; K; R |
| `test_golden_op_models.py` + `golden_util.py` | 6 C/C++ configs over the WB DMA model: `c-vtable`, `c-mmio-hdr`, `c-mmio`, `c-fn-static`, `c-accessors`, `cpp-virtual` | **G** |
| `test_conformance.py` | `pssc.testing.conformance` on op-model-c/cpp: operations present, folded address constants appear in text, gate, determinism, path order | T |
| `test_comment_propagation.py`, `unit/test_ctor_name.py`, `test_forward_subcomponent.py` (C/C++ params) | Comment carry-over; ctor name not read ambiently; declaration order changes nothing (byte equality) | T |
| `test_op_model_py_executors.py::test_every_target_lowers_a_qualified_call_as_the_unqualified_one` | `std_pkg::message` / `addr_reg_pkg::write32` qualified = unqualified (byte equality) | T |
| `test_op_model_py_repeat.py::test_op_model_c_lowers_the_loop_too` | ST-REPEAT spelled as a counted `for` | T |
| `unit/test_addr_handle_chandle.py` | DT-ADDR/DT-CHND type mapping (`c_type`, `cpp_type`) | T (unit) |

### 1.3 Refusal tests that name op-model-c (and implicitly C++)

| Test | What is refused on C |
|---|---|
| `test_op_model_py_pkg_functions.py::test_a_backend_that_does_not_lower_them_names_the_function` | package-scope function call (`supports_package_functions=False`) |
| `test_op_model_py_init_blocks.py::test_a_backend_that_does_not_run_them_refuses_them` | `exec init_up` / `init_down` (`supports_init_blocks=False`) |
| `test_op_model_py_executors.py::test_a_target_without_delegation_refuses_an_override` | executor overriding a memory primitive |
| `test_op_model_py_structs.py::test_other_op_model_targets_still_refuse_add_region` | `add_region` |
| `test_op_model_entry.py` | `--export-action` (`supports_entries=False`) |

None of these five has a C++ counterpart test; the probe confirms C++ refuses
package functions and init blocks with the same message.

---

## 2. The construct matrix, case by case

All 63 cases **pass on both C and C++** (run 2026-10-16). The only strict xfails
in the file are op-model-py's (`_PY_LAZY_SUB`, two cases). "R" means the
expected outcome is a refusal and the refusal is what passes.

| # | Case | Taxonomy IDs | C | C++ |
|---|---|---|---|---|
| 1 | two groups at two handles | DT-REG, DT-ADDR, LIB-REG `write_val`, FN-SOLVE `set_handle` | X | X |
| 2 | a group at a handle made from another | DT-ADDR `make_handle_from_handle` | X | X |
| 3 | three groups bound in reverse order | DT-REG | X | X |
| 4 | a group no set_handle reaches | DT-REG | R | R |
| 5 | a sub-component named t | EX-REF naming, FN-CALL into sub-component | X | X |
| 6 | a component, a struct and an enum with one stem | DT-STRUCT/DT-ENUM params, EX-EQ enum | X | X |
| 7 | names the generated code also uses (`self`,`_self`,`bus`,`_bus`) | ST-VAR, FN-PARAM naming, `this._bus` | X | X |
| 8 | root constructor parameters named like generated ones | FN-SOLVE params | X | X |
| 9 | sub-components nobody initializes | field initial values, construction (20.1.2), sub-component array | X | X |
| 10 | a sub-component whose constructor nobody calls | FN-SOLVE | X | X (py xfail) |
| 11 | a constructor writes into a child before constructing it | FN-SOLVE, EX-REF `c.a` | X | X (py xfail) |
| 12 | a constructor with a local, a condition and a loop | ST-VAR, ST-IF/else, ST-REPEAT in solve fn | X | X |
| 13 | calling a sub-component's operation | FN-CALL | X | X |
| 14 | a sub-component's register written from above | LIB-REG, EX-REF `s.a.STS` | X | X |
| 15 | operations of a sub-component array | ST-FOREACH over component array, EX-IDX | X | X |
| 16 | a sub-component's value in a condition | FN-CALL in expr, `read_val` | X | X |
| 17 | a part select read | EX-SEL `x[31:12]` read, DT-REG struct | X | X |
| 18 | a bit select and the high half of 64 bits | EX-SEL, DT-INT 64 | X | X |
| 19 | a bit select with a run-time index | EX-SEL, ST-WHILE, `+=` | X | X |
| 20 | a part and a bit select assigned | EX-SEL write (RMW of a local) | X | X |
| 21 | a part select of a register field assigned | EX-SEL write on struct field, LIB-REG read/write | X | X |
| 22 | a match with a default arm | ST-MATCH value/alternatives/default | X | X |
| 23 | match on enum items | ST-MATCH, DT-ENUM | X | X |
| 24 | match on items of a package's enum | DT-ENUM in package | X | X |
| 25 | offsets from an if chain | (generation-time evaluation) DT-REG | X | X |
| 26 | offsets from an if chain, out of order and sparse | DT-REG | X | X |
| 27 | an array-offset function that answers no for everything | DT-REG | X | X |
| 28 | an if chain with no case for a register | DT-REG | R | R |
| 29 | an offset function the build cannot evaluate | DT-REG | R | R |
| 30 | an enum field in a packed register struct | DT-REG, DT-ENUM `: bit[2]` | X | X |
| 31 | an unsized enum in a packed register struct | (front end) | R | R |
| 32 | a conditional expression | EX-COND | X | X |
| 33 | a nested conditional whose arm reads a register | EX-COND laziness | X | X |
| 34 | a value-returning operation in a condition | FN-CALL bool result | X | X |
| 35 | `~` in a context narrower than int | EX-BIT, EX-WIDTH | X | X |
| 36 | `~` in a context wider than its operand | EX-WIDTH (64) | X | X |
| 37 | `~` on a bit passed to a 32-bit parameter | EX-WIDTH 8.7.2 | X | X |
| 38 | a narrow local wraps at its own width (`bit[4]`) | DT-INT, `n = n + 1`; `n += 15` (does **not** overflow) | X | X |
| 39 | a narrow signed value overflows to negative (`int[8]`, `v = v + 1`) | DT-INT signed | X | X |
| 40 | signed division truncates toward zero | EX-ARITH `/ %` signed | X | X |
| 41 | `~` then a right shift, assigned to 8 bits | EX-SHIFT, EX-WIDTH | X | X |
| 42 | fields, a local and a parameter named as SV keywords | naming | X | X |
| 43 | array fields of a component | DT-ARR field, ST-FOREACH, EX-IDX, `+=` | X | X |
| 44 | struct defaults in a local and a nested struct | DT-STRUCT defaults, nested struct, enum member | X | X |
| 45 | struct defaults in array elements | DT-ARR of struct, sub-component | X | X |
| 46 | default values: an enum's first item, a bare local | DT-ENUM default, ST-VAR no init | X | X |
| 47 | repeat: counted, indexed, nested, empty | ST-REPEAT, ST-BRK break/continue | X | X |
| 48 | solve functions the constructor calls | FN-SOLVE (private ops) | X | X |
| 49 | a target function calling a solve function | FN-SOLVE | R | R |
| 50 | fields, locals and a parameter named as C keywords | naming | X | X |
| 51 | local arrays | DT-ARR local, whole-array copy, array of struct | X | X |
| 52 | local arrays: enum elements, two dimensions, copied structs | DT-ARR 2-D copy, array-of-struct copy into a field | X | X |
| 53 | an array parameter | FN-PARAM array (by handle, 20.3.2) | **R** (refused; py X) | **R** |
| 54 | array initializers | EX-LIT aggregate, nested, swap semantics | X | X |
| 55 | message: `%n` of an enum and a bool, `%d` at the PSS width | LIB-MSG | X | X |
| 56 | enum items named as the core library's | DT-ENUM linker resolution, DT-CAST enum→`bit[32]` | X | X |
| 57 | two enums with an item of one name | DT-ENUM scoping, `%n` | X | X |
| 58 | an enum field of a register value read back | DT-REG, DT-ENUM, LIB-MSG | X | X |
| 59 | an array initializer of the wrong size | EX-LIT aggregate | R | R |
| 60 | register values an operation takes and returns | FN-PARAM / return of register struct | X | X |
| 61 | a struct field wider than 64 bits | DT-INT >64 | **R** (py/sv X) | **R** |
| 62 | a local, parameter and result wider than 64 bits | DT-INT >64 | **R** (py/sv X) | **R** |
| 63 | a register wider than 64 bits | DT-REG | R | R |

(Also `test_an_enum_field_has_its_base_types_width_in_the_manifest[c,cpp]`: T on the manifest.)

---

## 3. What the emitters handle, refuse, and silently miss

### 3.1 Handled

* **C statement hooks** (`_BodyEmitter.hooks()`): `StmtAnnAssign, StmtAssign,
  StmtAugAssign, StmtExpr, StmtReturn, StmtIf, StmtWhile, StmtRepeatWhile,
  StmtFor` (= `repeat`), `StmtForeach, StmtMatch, StmtBreak, StmtContinue,
  StmtYield`. **Expression hooks**: `ExprConstant, ExprRefLocal,
  ExprAttribute, ExprSubscript` (incl. bit/part select via `bit_select`),
  `ExprBin, ExprUnary, ExprCast, ExprIfExp, ExprCall, ExprRefBottomUp`
  (always refuses). Calls by `Disposition`: reg, channel, mem, addr, utility
  (`message`/`print`), model_op, import.
* **C++** handles the same statement and expression kinds through its own
  `_stmt_lines`/`expr` cascade (not a `BodyWalker`; AGENTS.md notes it is
  unported).
* **Types**: `DataTypeInt` (≤64), `bool`, enum, chandle/`addr_handle_t`
  (`pssc_addr_t`), struct (incl. register value structs), fixed arrays as
  fields/locals (C: declarator with bounds; C++: `std::array`).
* **Integer semantics** are shared (`IntSemantics` → `CIntSemantics`):
  widening, wrap, sign extension, `**` (`_int_pow`, integral only), division
  by zero in a constant refused.

### 3.2 Refused with a located message (gate / `CompileError`)

| Construct | Where | Tested for C | Tested for C++ |
|---|---|---|---|
| Integer wider than 64 bits (field/local/param/result) | `int_width.refuse_too_wide` | matrix 61, 62 | matrix 61, 62 |
| Register wider than 64 bits | register walk | matrix 63 | matrix 63 |
| Array parameter/result | `validate_calls.array_signatures` | matrix 53 | matrix 53 |
| Array initializer of the wrong shape | `array_literal` | matrix 59 | matrix 59 |
| Target fn calling a solve fn | `validate_calls.solve_ops` | matrix 49 | matrix 49 |
| Unbound register group / bad offset fn | `group_binding`, `check_register_offsets` | matrix 4, 28, 29 | same |
| Package-scope function | `supports_package_functions=False` | `test_op_model_py_pkg_functions` | probe only |
| `exec init_down` / `init_up` | `supports_init_blocks=False` | `test_op_model_py_init_blocks` (init_up) | probe only |
| Executor overriding a primitive | `executors.check` | `test_op_model_py_executors` | **untested** |
| `--export-action` (action entries, `super;`) | `supports_entries=False` | `test_op_model_entry` | untested |
| Blocking channel `get`/`put`; channel depth>1 | emitter `ValueError` | `test_op_model_channel` | get/put only |
| Collection methods (`size()`, `push_back`, …) | call gate ("no function named …") | **untested** | **untested** |
| `urandom` / `urandom_range` | `call_legality` Tier-2 message | `test_call_legality` (registry) | same |
| C file-scope name clashes | `c_names.check` | `test_c_names` | n/a |
| Bit-reduction `&x |x ^x`, `randomize`, unknown stmt | **ast2ir** (all targets) | catalog 02 | catalog 02 |

### 3.3 Reached the emitter and crash (internal `ValueError`, not a located refusal)

Found by the probe; **no test covers any of them**, and each is legal PSS.

| PSS | C | C++ |
|---|---|---|
| `string s = "ab";` (local, field or parameter) | `unsupported C type for DataTypeString` | `unsupported C++ type for DataTypeString` |
| `float64 f = 1.5;` | `unsupported C type for NoneType` | same |
| `x in [1, 3..5]` (EX-IN) | `defines no expr_in()` | `unsupported expr ExprIn` |
| `2 ** x` with non-constant operand | ``**` is not lowered by this target`` | same |
| `p_s q = {.x = 3, .y = 4};` (struct aggregate literal) | `defines no expr_struct_literal()` | `unsupported expr ExprStructLiteral` |
| `match (x) { [1..3]: … }` (range pattern) | `defines no expr_range()` | `unsupported expr ExprRange` |
| struct with an array member `struct h_s { array<bit[8],2> v; }` | `unsupported C struct member type DataTypeArray` | same |
| `error("bad %d", 1)` (LIB-ERR; the front end accepts it) | `'error' is classified UTILITY, but call_utility() did not recognise it` | `call to 'error' has no lowering` (py crashes too) |

### 3.4 Accepted, and wrong (silent): generated code fails to compile or computes the wrong value

| PSS | C | C++ | Reference |
|---|---|---|---|
| `int[16] s = 32767; s += 1;` → write `s` | ✔ `0xffff8000` | ✘ **`0x8000`** | py ✔ |
| `bit[4] n = 15; n += 1;` (local or field) | ✔ `0` | ✘ **`0x10`** | — |
| `bit[12] m = 0xFFF; m += 2;` | ✔ `1` | ✘ **`0x1001`** | — |
| `function bit[8] bump(p_s v) { v.x = 7; … }`, caller reads `q.x` after (struct by handle, 20.3.2 / user ruling) | ✘ `1` (copy) | ✘ `1` (copy) | py ✔ `7` |
| `struct d_s : b_s { … }`, `v.a = 1` (inherited struct member) | ✘ does not compile (`d_s has no member a`) | ✘ same | py ✔ |
| `foreach (v : w[i]) { … i … }` (value iterator + index) | ✘ `i` undeclared | ✘ same | py ✔ |
| `function int add(int x, int y = 10)`; `add(1)` (default param) | ✘ too few arguments | ✘ no matching function | py ✔ |
| `bit[32] x; x << 33` | ✘ `2` (C UB: shift ≥ width) | ✘ `2` | py ✔ `0` |
| `array<bit[8],2> x, y; if (x == y)` | ✘ does not compile (`-Werror=array-compare`; without -Werror compares addresses) | ✔ | — |
| `write8(h, 0x1FF)` (8.7.2: argument truncated to `bit[8]`) | ✘ `-Werror=overflow` (literal not converted) | ✘ same | py ✔ |
| `function void bump(p_s v) { v.x = 7; }` (parameter only written) | ✘ `-Werror=unused-but-set-parameter` | ✘ same | — |

C++'s compound assignment (`cpp/lower_progseq.py`, `StmtAugAssign`) is
`x op= v` with no conversion back to the target's PSS width; C's
(`stmt_aug_assign`) converts through `IntSemantics`. Matrix case 38 uses
`n += 15` from 0, which never overflows, so it does not see this.

Not a defect, recorded so nobody "fixes" it: a `match` with no `default` that
matches nothing prints `"<prefix>: unmatched match subject in <fn>"` on C and
C++ (`--match-default message`, the default); op-model-py raises.

### 3.5 Probe constructs that worked (handled, but no executed test)

Both C and C++ produced the hand-computed trace for: struct local copy is a
value (`r = q; r.x = 2;`), struct returned from a function, nested-struct deep
assignment and whole-struct copy, nested `while` with inner `break`, `while` +
`continue`, `return` from a doubly nested `repeat`, if / else-if chain without
braces, signed `<` and signed-vs-unsigned compare, signed `>>` (arithmetic),
unsigned 64-bit multiply wrap, `int[64]` into a 32-bit parameter, `int[16]
s = s + 1`, unary minus on unsigned, `-= <<= >>= |= &= +=` on `bit[32]`,
package `static const` and `pkg::J`, `typedef`, `&&`/`||` short-circuit
around a register read, recursion, `pure function`, enum↔int casts, enum
relational compare, `bool` field/return/`!`, nested `foreach` over a struct
array field, 2-D array field indexing, `chandle` field, `int x = 1, y = 2;`,
writing a sub-component's field from the parent, `int in [0..10]`, `?:` with
mixed widths and nested `?:`, `message` `%d %x %b %o`, scoped block with
shadowing, `compile if` in a body, `yield` in a loop, `read8/read16/write8/
write16/write64`, `write_val_masked`, `% /` with a negative divisor, bool
compare in arithmetic, `x[7:0]` of a signed value and `w[40:33] = 0xFF` on 64
bits, enum `match` in a sub-component.

---

## 4. Coverage table

Cells: **handled?** / tests / strongest check style. "probe" = only this
audit's probe ran it; no test in the tree does. ✘ = not handled (refused,
crashes, or wrong).

### 4.1 Data types

| ID / variant | C | C++ |
|---|---|---|
| DT-INT `bit` (1) | ✔ matrix 37, WB DMA / X | ✔ same / X |
| DT-INT `bit[4]`/`bit[8]` | ✔ matrix 35, 38, 41, 54 / X | ✔ / X; ✘ `+=` overflow (§3.4) |
| DT-INT `bit[12]`/`bit[16]` | ✔ matrix 43 (`bit[16]` array, no overflow) / X; wrap: probe | ✔ / X; ✘ `+=` wrap |
| DT-INT `bit[32]` | ✔ everywhere / X | ✔ / X |
| DT-INT `bit[64]` | ✔ matrix 18, 36 / X | ✔ / X |
| DT-INT signed `int` | ✔ matrix 40 / X | ✔ / X |
| DT-INT `int[8]` | ✔ matrix 39 (`v = v + 1`) / X | ✔ `=` X; ✘ `+=` |
| DT-INT `int[16]`, `int[64]` | ✔ probe | ✔ `=` probe; ✘ `+=` |
| DT-INT > 64 bits | refused / matrix 61, 62 / R | refused / R |
| DT-INT range `int in [..]` | ✔ probe | ✔ probe |
| DT-BOOL | ✔ matrix 34, 55; WB DMA caps / X | ✔ / X |
| DT-ENUM explicit/implicit values, first-item default | ✔ matrix 23, 24, 30, 44, 46, 56–58 / X; enum typedef T | ✔ / X; `enum class` T |
| DT-ENUM ↔ int cast, relational | ✔ matrix 56 (enum→bit[32]) X; int→enum, `<`: probe | ✔ same |
| DT-ENUM extension (`extend enum`) | untested | untested |
| DT-STR | ✘ internal error (§3.3) / untested | ✘ / untested |
| DT-FP | ✘ internal error / untested | ✘ / untested |
| DT-CHND | ✔ (`pssc_addr_t`) probe; unit T | ✔ probe; unit T |
| DT-STRUCT fields, nesting, defaults | ✔ matrix 6, 44, 45 / X | ✔ / X |
| DT-STRUCT local copy (value) | ✔ probe; WB DMA `d = desc` G only | ✔ probe; G |
| DT-STRUCT array-of-struct copy | ✔ matrix 52 / X | ✔ / X |
| DT-STRUCT inheritance | ✘ does not compile / untested | ✘ / untested |
| DT-STRUCT with array member | ✘ internal error / untested | ✘ / untested |
| DT-STRUCT register value (packed) | ✔ matrix 17, 21, 30, 58, 60; behaviour tests / X | ✔ / X |
| DT-ARR field, local, 1-D | ✔ matrix 43, 51 / X | ✔ / X |
| DT-ARR multi-dim | ✔ matrix 52, 54 / X | ✔ / X |
| DT-ARR of struct / of enum | ✔ matrix 45, 51, 52 / X | ✔ / X |
| DT-ARR whole-array assign | ✔ matrix 51, 52, 54 / X | ✔ / X |
| DT-ARR `==` | ✘ does not compile / untested | ✔ probe / untested |
| DT-ARR `.size()`, `sum`, `in` | refused (gate) / untested | refused / untested |
| DT-ARR as parameter | refused / matrix 53 / R | refused / R |
| DT-LIST / DT-MAP / DT-SET | refused via method calls (gate); bare type unchecked / untested | same |
| DT-REF | n/a in op-model; untested | untested |
| DT-TYPEDEF | ✔ probe / untested | ✔ probe / untested |
| DT-CAST | ✔ matrix 56; WB DMA `(bit)`,`(bit[32])` (behaviour X partly, write_descriptor G) | ✔ same |
| DT-ADDR | ✔ matrix 1, 2; `addr_value` WB DMA G/T | ✔ same |
| DT-REG | ✔ matrix (20 cases); behaviour tests; conformance T / X | ✔ / X |
| DT-CONST `static const` | ✔ probe; WB DMA `WB_DMA_DESC_NEXT_OFF` G only | ✔ probe; G |

### 4.2 Expressions

| ID / variant | C | C++ |
|---|---|---|
| EX-LIT sized/based/unsized | ✔ X | ✔ X |
| EX-LIT array aggregate | ✔ matrix 54 / X | ✔ / X |
| EX-LIT struct aggregate `{.a=…}` | ✘ internal error / untested | ✘ / untested |
| EX-ARITH `+ - *` | ✔ X | ✔ X |
| EX-ARITH `/ %` signed | ✔ matrix 40 / X; negative divisor probe | ✔ / X |
| EX-ARITH `**` | ✘ non-constant: internal error / untested | ✘ / untested |
| EX-ARITH unary `-` | ✔ `test_c_body_lowering` T/K; probe X | ✔ probe |
| EX-REL `< <= > >=` | ✔ matrix 12, 19 / X; signed/mixed: probe | ✔ / X |
| EX-EQ | ✔ X | ✔ X |
| EX-LOG `&&` | ✔ matrix 54, 56 / X | ✔ / X |
| EX-LOG `||`, short-circuit with side effects | ✔ probe / untested | ✔ probe / untested |
| EX-LOG `!` | ✔ WB DMA `!caps.ars` (behaviour X); T | ✔ X |
| EX-BIT `~` | ✔ matrix 35–37, 41 / X | ✔ / X |
| EX-BIT `& |` | ✔ inherit "inherited register block" (`| 1`) X; WB DMA `|=` G | ✔ X |
| EX-BIT `^` | ✔ untested | ✔ untested |
| EX-RED | refused by ast2ir (all targets) | same |
| EX-SHIFT `<<` | ✔ WB DMA G; probe X | ✔ same |
| EX-SHIFT `>>` | ✔ matrix 41 / X; signed: probe | ✔ / X |
| EX-SHIFT by ≥ width | ✘ C UB, wrong value / untested | ✘ / untested |
| EX-COND | ✔ matrix 32, 33 / X | ✔ / X |
| EX-IN | ✘ internal error / untested | ✘ / untested |
| EX-SEL bit/part read | ✔ matrix 17–19 / X | ✔ / X |
| EX-SEL write (local, struct field) | ✔ matrix 20, 21 / X | ✔ / X |
| EX-IDX array, component array, run-time index | ✔ matrix 15, 43, 51; narrow-model `regs.channels[channel]` X | ✔ / X |
| EX-SUBSTR | n/a (no strings) | n/a |
| EX-WIDTH (8.7) assignment-like contexts | ✔ matrix 35–41 / X | ✔ `=`, args, return X; ✘ compound assignment |
| EX-ASSIGN `=` | ✔ X | ✔ X |
| EX-ASSIGN `+=` | ✔ matrix 19, 38, 43 / X (no overflow case) | ✔ / X, but ✘ on overflow (untested) |
| EX-ASSIGN `-= <<= >>= |= &=` | ✔ probe; `|=` WB DMA G | ✔ (no width wrap) probe |
| EX-REF field / sub-component / `this` | ✔ matrix 7, 11, 14 / X | ✔ / X |
| EX-REF `super.f`, `super.a` | ✔ inherit_native / X | ✔ / X |
| EX-REF parameter shadows field | ✔ `test_param_shadows_field` / **T only** | ✔ / **T only** |

### 4.3 Functions

| ID / variant | C | C++ |
|---|---|---|
| FN-DECL component scope, void / value | ✔ X | ✔ X |
| FN-DECL package scope | refused / `test_op_model_py_pkg_functions` R | refused / probe only |
| FN-PARAM scalar by value | ✔ X | ✔ X |
| FN-PARAM struct | ✔ passes, but by **value**: ✘ callee write lost (§3.4) / matrix 6, 60 X (read-only use) | same |
| FN-PARAM array | refused / matrix 53 R | refused / R |
| FN-PARAM default values | ✘ does not compile / untested | ✘ / untested |
| FN-PURE | ✔ probe / untested | ✔ probe / untested |
| FN-CALL nested, in expression, as statement | ✔ matrix 16, 33, 34 / X | ✔ / X |
| FN-CALL into sub-component / component array | ✔ matrix 13, 15 / X | ✔ / X |
| FN-CALL qualified `pkg::f` (core lib) | ✔ T (byte equality) | ✔ T |
| FN-CALL recursion | ✔ probe / untested | ✔ probe / untested |
| FN-NATIVE | ✔ X | ✔ X |
| FN-IMPORT | ✔ `test_c_imports` T + link/run exit 0 | ✔ `test_cpp_target` T |
| FN-EXPORT | n/a (every target fn is API) | n/a |
| FN-SOLVE constructor, other solve fns | ✔ matrix 8–12, 48 / X; R 49 | ✔ / X |
| FN-VIRT override, `super.f()` | ✔ inherit_native 5 cases / X (flattened) | ✔ / X (native) |
| FN-TT | untested | untested |

### 4.4 Procedural statements

| ID / variant | C | C++ |
|---|---|---|
| ST-BLOCK scoped, shadowing | ✔ probe / untested | ✔ probe / untested |
| ST-VAR with/without init, multiple | ✔ matrix 46 / X; multiple: probe | ✔ / X |
| ST-ASSIGN | ✔ X | ✔ X |
| ST-VCALL | ✔ X | ✔ X |
| ST-RET (incl. from nested loop) | ✔ X; nested: narrow model (repeat-while) X, probe | ✔ X |
| ST-REPEAT count, index, nested, zero | ✔ matrix 12, 47 / X | ✔ / X |
| ST-RWHILE | ✔ `test_build_wb_dma_c` / X | ✔ `test_build_wb_dma_cpp` / X |
| ST-WHILE | ✔ matrix 19; WB DMA `while(true)` / X | ✔ / X |
| ST-FOREACH index over array / component array / struct array | ✔ matrix 15, 43, 51 / X | ✔ / X |
| ST-FOREACH value iterator `foreach (v : a[i])` | ✘ does not compile / untested | ✘ / untested |
| ST-FOREACH list/map/set | n/a (types refused) | n/a |
| ST-IF / else | ✔ X | ✔ X |
| ST-IF else-if chain, unbraced | ✔ probe; offsets-if-chain is generation-time only | ✔ probe |
| ST-MATCH values, alternatives, default, enum | ✔ matrix 22–24 / X | ✔ / X |
| ST-MATCH ranges | ✘ internal error / untested | ✘ / untested |
| ST-MATCH no default | `--match-default` T (`test_c_body_lowering`) | T (switch+break) |
| ST-BRK break/continue, nested loops | ✔ matrix 47 / X; nested-while probe | ✔ / X |
| ST-RAND | refused by ast2ir | same |
| ST-CIF | ✔ WB DMA `wait_hint` (behaviour X); T | ✔ X; T |
| ST-YIELD | ✔ behaviour X (poll loop); T/K | ✔ X; T |
| ST-SUPER (`super;` in exec) | refused (`--export-action` unsupported) | same, untested |

### 4.5 Exec blocks and library

| ID / variant | C | C++ |
|---|---|---|
| EXEC-KIND `init_down`/`init_up` | refused / `test_op_model_py_init_blocks` R (init_up) | refused / probe only |
| EXEC-KIND `body` (action exec) | n/a for op-model (no entries); see §6 for c-host | n/a |
| EXEC-TT | untested | untested |
| LIB-MSG `message`/`print`, specifiers | ✔ matrix 55, 57, 58; `test_c_message_format` / X | ✔ / X |
| LIB-FILE | not claimed; untested | untested |
| LIB-ERR `error`/`fatal` | ✘ internal error / untested | ✘ / untested |
| LIB-RAND `urandom` | refused (registry) / `test_call_legality` | refused |
| LIB-MEM `read32`/`write32` in a body | ✔ T (`test_c_body_lowering`, `test_mem_access`), WB DMA `write_descriptor` G only; probe X | ✔ T, G; probe X |
| LIB-MEM 8/16/64 | ✔ probe X; funnel T | ✔ probe X |
| LIB-MEM executor delegation | refused if a primitive is overridden / R | refused / untested |
| LIB-REG `read`/`write`/`read_val`/`write_val` | ✔ matrix (most cases) / X | ✔ / X |
| LIB-REG `write_val_masked`, `write_field` | ✔ behaviour test (mutation-checked) X; `test_c_reg_masked` T/K | ✔ behaviour X (mutation on runtime) |
| LIB-CHAN `try_put`/`try_get` depth 1 | ✔ WB DMA behaviour X; T | ✔ X; K |
| LIB-CHAN blocking `get`/`put`, depth > 1 | refused / R (both) | get/put R; depth>1 untested |

---

## 5. Holes

### 5.1 Handled by the emitter but never executed by a test (text-only or untested)

* **`write_descriptor` and `read_descriptor_residual`** (WB DMA): the only
  model uses of `|=`, `<<`, `(bit[32])` widening casts, nested struct fields
  (`d.csr.tot_sz`), whole-struct copy (`d = desc`), `static const` offsets and
  `write32`/`read32` on computed handles. Covered by **golden snapshots and
  text greps only** on both C and C++; no TB calls them. The same holds for
  `transfer_list`, `check_completion`, `configure_interrupt_routing` (`match`
  on an enum writing two registers) and `set_software_pointer`.
* **Model-body memory primitives** (`read8..64`, `write8..64`): no executed
  C/C++ test; the matrix never calls one.
* **EX-REF parameter-shadows-field**: C and C++ are grep-only; the defect it
  guards (reading the field) would compile cleanly.
* **Compound assignment other than `+=`**, **`||`**, **`^`**, **signed `>>`**,
  **signed relational and mixed signed/unsigned compare**, **unary `-`**,
  **`int[16]`/`int[64]`**, **unsigned 64-bit wrap**, **recursion**, **`pure
  function`**, **`typedef`**, **`static const`**, **enum `<`**, **int→enum
  cast**, **scoped blocks**, **else-if chains**, **nested loops with an inner
  `break`**, **struct local copy**, **struct return**: all handled (probe), none
  in a test.
* **ST-YIELD / ST-CIF**: executed only as part of the WB DMA poll loop.

### 5.2 Accepted and wrong — semantics bugs no test catches

1. **C++ compound assignment ignores PSS width** (`bit[4] n=15; n+=1` → 16;
   `int[16]` overflow not sign-wrapped). Matrix 38's `+=` cannot overflow.
2. **Struct parameters are copied, not passed by handle** (C and C++); a
   callee's write is lost. Arrays are refused for exactly this reason
   (matrix 53) — structs are not.
3. **Shift by ≥ the operand width** is C/C++ UB and yields `2` for `1 << 33`.
4. Generated code that **does not compile** for legal PSS: struct inheritance,
   `foreach (v : a[i])`, default parameter values, array `==` (C only), an
   over-wide literal to `write8` (both, `-Werror=overflow`), a struct
   parameter that is only written (both, `-Werror`).

### 5.3 Internal errors instead of a located refusal (LRM-legal constructs)

`string` (any position), `float32/float64`, `in`, non-constant `**`, struct
aggregate literals, `match` range patterns, structs with array members,
`error(...)`. Each reaches the emitter and raises `ValueError` (C: "defines no
expr_*()" or "unsupported C type"); the gate (`validate_calls`) does not
pre-screen types or expression kinds, only calls.

### 5.4 Refused, though the LRM allows it (implementation gaps)

Package-scope functions; `exec init_down`/`init_up`; array parameters/results;
integers > 64 bits (user ruling — intentional); executor overrides of memory
primitives; action entries (`--export-action`, `super;`); collection types
and their methods (`list/map/set`, `size()`, `sum`); `urandom`; blocking
channel ops and depth > 1 (no scheduler — intentional for this profile). C++
refusals of package functions, init blocks, executors and entries have **no
test**; only the C refusal is pinned.

### 5.5 Semantics checked only by golden snapshot

Everything the six C/C++ golden configs freeze for the WB DMA operations that
no TB runs (§5.1 first bullet): descriptor packing (`csr_word |= … << 16`),
`(bit[12])` truncation in `read_descriptor_residual`, the linked-list `next`
write, interrupt-routing `match`, software-pointer struct, `--reg-style
accessors` bodies (K only), and every non-`vtable` link style's register
accessors beyond the narrow model's TB.

---

## 6. The older C family (`c-host`, `c-embedded`, `sv-dpi`, `sv-dpi-bridge`)

These lower action `exec` bodies through zuspec-be-sw (`targets/sw_tgt.py`,
`sw_lower.py`). Coverage of procedural emission is minimal:

* `tests/unit/test_target_sw.py`: one action with `int v = 1 + 2;` — **T**
  (`"1 + 2" in body`); runtime-solve vs. presolve file presence; presolved
  constants in range (T); `sv-dpi` C runtime compiled with `gcc -w` (**K**,
  warnings off).
* `tests/unit/test_target_bridge.py`, `tests/sim/sv/test_bridge_e2e.py`
  (Verilator + gcc, `sim`): `print("…%d", x)`, an import call whose argument
  is another import's result, blocking/parallel imports — **X** under
  Verilator, but only LIB-MSG, FN-IMPORT and an `int` local.

No DT-*, EX-* (beyond `+`) or ST-* construct is tested on these targets.
