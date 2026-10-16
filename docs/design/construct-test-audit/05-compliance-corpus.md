# 05 — pss-corpus executable compliance tier (data types, expressions, functions, statements)

Scope: what `packages/pss-corpus/compliance/` exercises in the DT-/EX-/FN-/ST-/EXEC-/LIB-
areas of `taxonomy.md`, and how each pssc backend fares on it. Activities (`act/`) are
skimmed only. Corpus models were read, not edited.

Status as of 2026-10-16 (pssc `main` @ 394f75d, corpus checkout in `packages/pss-corpus`).

## 1. How the tier runs in pssc

| Runner | Adapter | Expected list | Seeds | Run here |
|---|---|---|---|---|
| `tests/compliance/test_compliance_bc.py` | `adapters/pssc_bc.py` (in-process bc interpreter) | `expected/bc.toml` — **empty** | every seed the test declares (577 seed-runs) | all 577 passed |
| `tests/compliance/test_compliance_op_model_py.py` | `adapters/pssc_op_model_py.py` (`--export-action <root>`, import the module, call the entry over a `MemoryBus` whose `message()` is the log) | `expected/op-model-py.toml` — 30 entries | first seed only (no randomness) | 83 passed, 30 xfailed |
| `tests/compliance/test_compliance_op_model_sv.py` (marker `sim`) | `adapters/pssc_op_model_sv.py` (`--export-action`, `--emit-manifest`, testbench written from the manifest, Verilator `--binary`, stdout is the log) | `expected/op-model-sv.toml` — 32 entries | first seed only | 81 passed, 32 xfailed (Verilator, 2m46s) |

Each runner: one pytest case per (test, seed); the **corpus checker** (`pss_corpus.check_run`)
gives the verdict; an entry in the toml is strict (a listed test that passes fails the
suite, a different verdict fails too). Command used (venv is `packages/python`, ir-core
checkout first on the path):

```
PYTHONPATH=packages/zuspec-ir-core/src:src direnv exec . packages/python/bin/python \
    -m pytest tests/compliance/test_compliance_op_model_py.py tests/compliance/test_compliance_bc.py -q
```

(`../python/bin/python` from AGENTS.md does not exist in this checkout; pytest-xdist is not
installed, so `-n` is unavailable.)

**There is no C or C++ runner.** No adapter, runner or expected list names `op-model-c`
or `op-model-cpp` anywhere under `tests/compliance/` or the corpus checker. §7 assesses
what one would take.

## 2. The corpus's own coverage index

* There is **no machine-readable coverage index yet**. `COMPLIANCE-DESIGN.md` §9 specifies
  `compliance/features.yaml` (feature IDs → LRM clauses, BNF productions, level) and §9.5
  `compliance/shall-index.yaml` (every normative LRM statement); **neither file exists**.
* What exists:
  * per test, `test.json` carries `lrm` (clauses adjudicated) and `features.tests` /
    `features.uses` (dotted feature IDs, e.g. `proc.match.valuelist`, `rt.shift.arithmetic`);
  * `compliance/README.md` has a hand-written catalogue per slice with LRM clauses, and two
    explicit "not yet covered" lists: *`foreach` over collections, `randomize`, solve-time
    execs (`pre_solve`/`post_solve` result attributes), exec inheritance and `super`, `yield`
    ladders, `runtime_error`/`solve_fail` negative tests, `parallel`*; and *`parallel`,
    `schedule`, `select`* for activities. Memory and registers are "the next batch".
* The README's "Results" table is stale (bc 66/90, py 80/90 dated 2026-09-24); the strict
  toml lists in pssc are the current truth.

LRM clauses cited by the non-activity tests: 4.6.1, 7.2, 7.4, 7.5, 7.6, 7.8, 7.12, 8.3,
8.4.1, 8.5.1–8.5.9.1, 8.5.1.1, 8.7, 8.7.1, 8.7.2, 9.1, 9.1.4, 9.1.4.1, 10.2, 13.3, 18.3,
19.1, 19.3, 20.1, 20.1.2, 20.1.3, 20.2, 20.2.4, 20.3.2, 20.7, 20.7.1–20.7.11, 20.7.14,
21.1.1, 21.1.3, 21.7.2.6, 21.9.1(.1–.4), 21.10.3.3, 21.13.9.1/.2/.5.

## 3. Per-test catalogue

Corpus totals: 113 tests — L0-protocol 16, L0-tap 4, proc 47, comp 6, types 13, sync 3,
neg 4, act 20 (plus the library `lib/pct_tap.pss`). The task brief lists proc/comp/lib/sync/neg;
**`types/` is also part of the op-model batch and is catalogued here**.

Verdict columns: **P** = PASS (not listed). Otherwise the listed verdict, abbreviated
**U** = UNSUPPORTED, with a reason key from §6. bc is PASS for every test.

### 3.1 proc/ (47) — the procedural core (L1/L2)

| Test (id) | What it exercises (taxonomy IDs) | Neg | bc | py | sv |
|---|---|---|---|---|---|
| arith (`proc.expr.arith.001`) | EX-ARITH `+ - * / %`, unary `-` on `int` | | P | P | P |
| assign_basic | ST-ASSIGN, ST-VAR (decl then assign), EX-ARITH | | P | P | P |
| assign_compound | EX-ASSIGN `+= -= <<= >>= \|= &=` (all PSS has; no `*= /= %= ^=` in the LRM) | | P | P | P |
| bitwise | EX-BIT `& \| ^ ~`, EX-SHIFT `<< >>` on `int` | | P | P | P |
| break | ST-BRK `break` in `repeat (i : N)` | | P | P | P |
| break_nested | ST-BRK inner-loop `break` only exits the inner `repeat` | | P | P | P |
| break_while | ST-BRK in `while (true)` | | P | P | P |
| cast_precedence | DT-CAST `(bit[4])b + c` binds tighter than `+` | | P | P | P |
| compile_if | ST-CIF: `compile if`/`compile has` at package, component (function choice) and procedural scope; DT-CONST bool | | P | P | P |
| cond_op | EX-COND `?:` both arms | | P | P | P |
| continue | ST-BRK `continue` in `repeat` | | P | P | P |
| field_rw | EX-REF action attribute write/read (`int`, `bit[8]`) | | P | U-attr | U-attr |
| func_component | FN-CALL `comp.f()`, FN-DECL component scope | | P | P | P |
| func_default_param | FN-PARAM default value (L2) | | P | P | P |
| func_nested_calls | FN-CALL nested `sq(add1(x))`, call in function body | | P | P | P |
| func_param_byvalue | FN-PARAM scalar by value (callee writes its copy) | | P | P | P |
| func_recursion | FN-CALL recursion (`fact`) (L2) | | P | P | P |
| func_return | ST-RET from `if`, FN-DECL `int` result, negative args | | P | P | P |
| func_void_cast | FN-CALL `(void)f()` and value-returning call as statement | | P | P | P |
| func_void_return | FN-DECL `void`, ST-RET `return;` early | | P | P | P |
| if_else | ST-IF then / no-else / else | | P | P | P |
| if_elseif | ST-IF `else if` chain, 4 paths | | P | P | P |
| if_unbraced | ST-IF unbraced then/else, assignment and call bodies | | P | P | P |
| local_decl | ST-VAR default values (`int`, `bool`), initializer from another local; EX-WIDTH 8.7.2 | | P | P | P |
| local_multi | ST-VAR `int e = 1, f = e + 1, g;` | | P | P | P |
| logic | EX-LOG `&& \|\| !`, EX-REL, EX-EQ on `int`/`bool` | | P | P | P |
| match_ranges | ST-MATCH range patterns `[0..3]`, `default` | | P | **U-range** | P |
| match_values | ST-MATCH value lists `[1, 3]`, no default, no match | | P | P | P |
| poll_repeat_while | ST-RWHILE in a function, enum results, early return (op-model) | | P | P | P |
| poll_while_true | ST-WHILE `while(true)` + `break`, `return` from inside loop | | P | P | P |
| rand_input | `rand bit[4]` attribute feeding procedural code; ST-REPEAT `(i : n)` with run-time count; `+=` | | P | U-rand | U-rand |
| repeat_count | ST-REPEAT `(4)` | | P | P | P |
| repeat_index | ST-REPEAT `(i : 5)`, index in expression | | P | P | P |
| repeat_while | ST-RWHILE, incl. body runs once when condition false | | P | P | P |
| repeat_zero | ST-REPEAT with count 0 (from a variable) | | P | P | P |
| return_exec | ST-RET `return;` ends an `exec body` | | P | P | P |
| rt_div_mod | EX-ARITH `/ %` with negative operands (truncation toward 0, sign of `%`) (L2) | | P | P | P |
| rt_extend_assign | EX-WIDTH sign/zero extension on assignment (`int[8]`→`int`, `bit[8]`→`int`, `int[8]`→`bit[16]`) | | P | P | P |
| rt_shift_signed | EX-SHIFT `>>` arithmetic on signed, logical on `bit[8]` | | P | P | P |
| rt_sign_compare | EX-REL mixed signed/unsigned compare (Ex 43) | | P | P | P |
| rt_trunc_assign | EX-WIDTH truncation on assignment (`bit[8]=300`, `bit[4]=31`, `int[8]=200`) | | P | P | P |
| rt_width_propagate | EX-WIDTH context width into `x + y` vs self-determined vararg (Ex 42) | | P | P | P |
| scope_block | ST-BLOCK nested block, inner local | | P | P | P |
| scope_shadow | ST-BLOCK inner local shadows outer (L2) | | P | P | P |
| while | ST-WHILE, `n & (n-1)`, `bool` toggle | | P | P | P |
| while_zero | ST-WHILE zero iterations | | P | P | P |
| yield_single | ST-YIELD in exec body and in a function | | P | P | P |

### 3.2 comp/ (6) — component state and functions (op-model profile)

| Test | Exercises | Neg | bc | py | sv |
|---|---|---|---|---|---|
| array (`comp.array.001`) | component instance array sized by package const (DT-CONST); ST-FOREACH over a **component** array (`foreach (ch[i])`) in `init_down` and a target function; run-time index `comp.ch[j]`; FN-SOLVE `initialize` called per element; EX-SHIFT/EX-BIT `^` on `bit[32]` | | P | P | P |
| func_calls | FN-CALL unqualified sibling call, package function from component, `comp.sub.f()`; ST-RET early on an attribute; `init_down` per-instance | | P | P | P |
| init_order | EXEC-KIND `init_down`/`init_up` order across tree (Ex 280); field initializer overwritten | | P | P | P |
| init_solve_fn | FN-SOLVE (`solve function` called from `init_down`), field initializers kept | | P | P | P |
| instances | two instances of one type, sub-component state, `bit[32]` address arithmetic | | P | P | P |
| struct_attr | DT-STRUCT component attribute with field initializers, per-instance override; `bit[4] * 100` | | P | P | P |

### 3.3 types/ (13)

| Test | Exercises | Neg | bc | py | sv |
|---|---|---|---|---|---|
| bit_pack | DT-STRUCT of `bit` fields; DT-CAST widen/narrow; EX-SHIFT with context width (`d.eol << 20` into `bit[32]`), `\|=` | | P | P | P |
| cast_constraint | DT-CAST in **constraints** (widen, narrow, signed reinterpret, enum→bit) (64 seeds) | | P | U-rand | U-rand |
| enum_match | ST-MATCH on enum, value list, qualified item `bank_e::C_BANK`, default; FN-PARAM enum | | P | P | P |
| enum_rand | DT-ENUM rand in action and struct, implicit/sparse/negative values (64 seeds) | | P | U-rand | U-rand |
| enum_values | DT-ENUM default = first item, explicit values, `==`/`!=`, enum→`int`/`bit`/`bit[8]` casts, enum function result | | P | P | P |
| literal_type | EX-LIT typing `0x10` unsigned, `16` signed, `8'h80` (in constraints) (64 seeds) | | P | U-rand | U-rand |
| pkg_const | DT-CONST package/component `static const`, const expressions, `import pkg::*`, qualified refs, `bit[64]` const | | P | P | P |
| return_convert | EX-WIDTH conversion at `return` (−1→`bit[64]`, truncate, extend, sign) | | P | P | P |
| string_match | DT-STR param, ST-MATCH on string, `==` on strings, `return -1` as `bit[64]` | | P | **U-goi** | **U-goi** |
| struct_copy | DT-STRUCT nested, assignment is a deep copy, `==`/`!=` on structs | | P | P | P |
| struct_fields | DT-STRUCT nested fields, default values, field writes with truncation | | P | P | P |
| struct_param | FN-PARAM struct by handle (Ex 298): callee write visible, scalar not; struct returned as argument; component function | | P | P | P |
| struct_return | FN-DECL struct result, copy of result | | P | P | P |

### 3.4 sync/ (3) — channels (PSS 3.1)

| Test | Exercises | Neg | bc | py | sv |
|---|---|---|---|---|---|
| chan_depth | LIB-CHAN `channel_c<int,3>` FIFO, `try_put` full, blocking `get`/`put`, copy-in, `try_get` out param | | P | **U-async** | P |
| chan_guard | LIB-CHAN `channel_c<bit,1>` as in-flight guard, per instance in a component array, `foreach` over component array | | P | P | P |
| chan_try | LIB-CHAN `try_get` empty leaves out-param untouched, `try_put` depth 1 | | P | P | P |

### 3.5 neg/ (4) and L0-protocol/diag_location — negative tests

All five expect `compile_error` with a diagnostic on a stated line; all PASS on all three
backends (the front end rejects them before any backend runs).

| Test | Error | bc | py | sv |
|---|---|---|---|---|
| neg/call_undeclared | call to undeclared function (FN-CALL) | P | P | P |
| neg/ref_before_decl | local used before its declaration (ST-VAR, 20.7.2) | P | P | P |
| neg/ref_undeclared | assignment to undeclared name (ST-ASSIGN) | P | P | P |
| neg/type_undeclared | local of undeclared type (ST-VAR) | P | P | P |
| L0-protocol/diag_location | action field of undeclared type (diagnostics protocol) | P | P | P |

Negative coverage is **name resolution only**: no type errors, illegal assignment targets,
wrong argument counts, `break` outside a loop, non-constant `compile if`, etc.

### 3.6 lib/ and L0-tap/ (4) — memory through the executor tap

`lib/pct_tap.pss` is not a test: an executor (`pct_tap_executor_c : executor_c<>`)
overriding `read8..64`/`write8..64`, logging each access; reads return a hash of the address.
It itself uses FN-VIRT (executor override), EX-LIT aggregate default `= {}`, DT-ADDR
`addr_value`, casts and shifts. `L0-tap/rw8|16|32|64`: LIB-MEM `readN`/`writeN` through
`set_executor`, DT-ADDR `transparent_addr_space_c`, `add_region`, `make_handle_from_handle`.

| Test | bc | py | sv |
|---|---|---|---|
| rw8, rw16, rw32, rw64 | P | P | **U-xtr** |

### 3.7 L0-protocol/ (16, skimmed) — `message()` formatting

`act_single`, `dec_int32`, `dec_int64` (int64 min/max), `udec` (`%u` up to 2^64−1), `hex`,
`bool_n`, `enum_n` (`%n` of an enum, explicit value), `str_s` (`%s`, DT-STR local and
literal), `no_padding`, `percent`, `multi_record`, `from_function`, `from_recursive`
(recursive void function), `nested_action`, `seq_bodies`, `diag_location` (§3.5).
LIB-MSG, DT-INT 64-bit extremes, DT-BOOL, DT-ENUM, DT-STR. bc/py/sv PASS except
`nested_action` and `seq_bodies` (U-act on py and sv: they use an activity).

### 3.8 act/ (20, skimmed)

Traversal, `seq`, `repeat`, `replicate`, labels, `with`, activity constraints, lookahead,
component choice, symbols, compound `pre_solve`/`post_solve`. Procedurally they use only
`message`, `rand` scalars and `this.`; the only EXEC-KIND coverage beyond `body`/`init_*` is
`compound_pre_post`. bc PASS on all; py and sv U-act on all 20 (an operation model has no
activity).

## 4. Coverage table (taxonomy ID → corpus tests)

Status is bc / py / sv over the listed tests. "all P" = every listed test passes on all three.

| ID | Corpus tests covering it | bc / py / sv |
|---|---|---|
| DT-INT | nearly all; widths/sign: local_decl, rt_*, udec, dec_int64, hex, bit_pack, return_convert | all P |
| DT-FP | — | **hole** |
| DT-BOOL | bool_n, logic, while, local_decl (default), compile_if, pkg_const, chan_* | all P |
| DT-ENUM | enum_n, enum_values, enum_match, poll_*, enum_rand, cast_constraint | P / P except enum_rand, cast_constraint U-rand / same |
| DT-STR | str_s, string_match | P / string_match U-goi / string_match U-goi |
| DT-CHND | — | **hole** |
| DT-STRUCT | struct_fields, struct_copy, struct_param, struct_return, struct_attr, bit_pack, enum_rand | all P except enum_rand (U-rand py/sv) |
| DT-ARR | comp/array, chan_guard (component instance arrays only) | all P |
| DT-LIST / DT-MAP / DT-SET | — | **hole** |
| DT-REF | — | **hole** |
| DT-TYPEDEF | — | **hole** |
| DT-CAST | cast_precedence, bit_pack, enum_values, enum_n, return_convert (implicit), cast_constraint, pct_tap | all P except cast_constraint (U-rand py/sv) |
| DT-ADDR | L0-tap/rw* (+ pct_tap) | P / P / U-xtr |
| DT-REG | — | **hole** (README: "next batch") |
| DT-CONST | pkg_const, compile_if, comp/array | all P |
| EX-LIT | hex, udec, dec_int64, str_s, literal_type (constraints), pct_tap `{}` | P / literal_type U-rand / same |
| EX-ARITH | arith, rt_div_mod, assign_basic, repeat_index | all P |
| EX-REL | logic, rt_sign_compare, if_*, func_return | all P |
| EX-EQ | logic, enum_values, struct_copy (aggregate `==`), string_match (string `==`) | P / string_match U-goi / same |
| EX-LOG | logic, chan_depth (`&&`) | all P |
| EX-BIT | bitwise, while, comp/array (`^`), pct_tap | all P |
| EX-RED | — | **hole** |
| EX-SHIFT | bitwise, rt_shift_signed, bit_pack, assign_compound | all P |
| EX-COND | cond_op | all P |
| EX-IN | act/with_order (constraint only) | **procedural hole** |
| EX-SEL | — | **hole** |
| EX-IDX | comp/array (`comp.ch[j]`, component arrays) | all P |
| EX-SUBSTR | — | **hole** |
| EX-WIDTH | rt_trunc_assign, rt_extend_assign, rt_width_propagate, rt_sign_compare, local_decl, return_convert, bit_pack, struct_fields | all P |
| EX-ASSIGN | assign_basic, assign_compound, bit_pack (`\|=`), many `+=` | all P |
| EX-REF | field_rw (action attr), comp/* (sub-component paths), init_order, act/with (`this.`) | P / field_rw U-attr / same |
| FN-DECL | func_*, comp/func_calls, struct_return, return_convert, enum_values | all P |
| FN-PARAM | func_param_byvalue, func_default_param, struct_param, string_match, enum_match | P / string_match U-goi / same |
| FN-PURE | — | **hole** |
| FN-CALL | func_nested_calls, func_void_cast, func_recursion, from_recursive, func_component, comp/func_calls, comp/array | all P |
| FN-NATIVE | all package/component function tests; struct_param (20.3.2 passing) | all P |
| FN-IMPORT | — | **hole** |
| FN-EXPORT | — | **hole** |
| FN-SOLVE | comp/init_solve_fn, comp/array | all P |
| FN-VIRT | pct_tap executor overrides only | P / P / U-xtr |
| FN-TT | — | **hole** |
| ST-BLOCK | scope_block, scope_shadow | all P |
| ST-VAR | local_decl, local_multi, neg/ref_before_decl, neg/type_undeclared | all P |
| ST-ASSIGN | assign_basic, field_rw, neg/ref_undeclared | P / field_rw U-attr / same |
| ST-VCALL | func_void_return, func_void_cast, yield_single | all P |
| ST-RET | return_exec, func_return, func_void_return, poll_while_true, comp/func_calls | all P |
| ST-REPEAT | repeat_count, repeat_index, repeat_zero, rand_input (run-time count) | P / rand_input U-rand / same |
| ST-RWHILE | repeat_while, poll_repeat_while | all P |
| ST-WHILE | while, while_zero, break_while, poll_while_true, yield_single | all P |
| ST-FOREACH | comp/array, chan_guard (component arrays only) | all P |
| ST-IF | if_else, if_elseif, if_unbraced | all P |
| ST-MATCH | match_values, match_ranges, enum_match, string_match | P / match_ranges U-range, string_match U-goi / string_match U-goi |
| ST-BRK | break, break_nested, break_while, continue, poll_while_true | all P |
| ST-RAND | — | **hole** |
| ST-CIF | compile_if | all P |
| ST-YIELD | yield_single | all P |
| ST-SUPER | — | **hole** |
| EXEC-KIND | body (all), init_down/init_up (comp/*, L0-tap), pre/post_solve (act/compound_pre_post) | P / P except act U-act / same + L0-tap U-xtr |
| EXEC-TT | — | **hole** |
| LIB-MSG | all L0-protocol | P / P except nested/seq U-act / same |
| LIB-FILE | — | **hole** |
| LIB-ERR | — | **hole** |
| LIB-RAND | — | **hole** |
| LIB-FP | — | **hole** |
| LIB-MEM | L0-tap/rw8..64 (via executor) | P / P / U-xtr |
| LIB-REG | — | **hole** |
| LIB-CHAN | chan_try, chan_depth, chan_guard | P / chan_depth U-async / P |

## 5. Corpus holes (verified by reading / grepping every `.pss`)

Whole constructs no corpus test touches:

* **Data types:** `float32`/`float64` (DT-FP, LIB-FP); `chandle`; `list`/`map`/`set` and
  every collection method/operator; data arrays (local or attribute `int a[4]`, array of
  struct, array literal, array `==`/`in`/`size`/`sum`, multi-dim) — the only arrays are
  component-instance arrays; `ref T`; `typedef`; struct inheritance; `packed_s` /
  register value structs; `reg_c`/`reg_group_c` and all LIB-REG accessors; `read_struct`/
  `write_struct`/`read_bytes`/`write_bytes`.
* **Strings:** only `%s`, `==`, `match` and a string param. No `!=`, `+`/concatenation,
  `in`, sub-string `s[i..j]`, `size()`, `find`, `upper`/`lower`, `split`, `chars`.
* **Enums:** no enum `extend`, `in` over enum items/ranges, enum ranges in `match`, `%n`
  on an enum field of a struct, enum↔enum cast.
* **Expressions:** reduction `& | ^` (EX-RED); bit/part select `x[i]`, `x[h:l]` read or
  write (EX-SEL); `**`; procedural `in` (EX-IN only appears in a constraint); bool↔int cast;
  `!=` between mixed widths; shift by ≥ width or by a run-time amount; arithmetic overflow
  wrap inside an expression (only truncation on assignment); operations wider than 64 bits;
  hierarchical `super.x`; parameter-shadows-field.
* **Assignment variety:** all six PSS compound operators are present (`+= -= <<= >>= |=
  &=`); `*= /= %= ^=` are **not PSS**, so taxonomy EX-ASSIGN's list over-states the LRM.
  Missing: compound on a struct field / array element / sub-component field, compound with
  width conversion (`bit[4] x; x += 20`).
* **Functions:** `pure` functions; `import` functions (any availability, by any profile);
  `export`; target-template functions; qualified call `pkg::f()` (package functions are
  only ever called unqualified); `static` functions; component function override /
  `super.f()` (only the executor tap's overrides); array params/results; `inout`/`output`
  style passing; varargs/generic params; calling a function in a constraint / solve context.
* **Statements:** `randomize` (ST-RAND); `super;` (ST-SUPER); `foreach` over data
  arrays/lists/maps/sets, with key/value vars; `continue` in `while`/`repeat-while`;
  `break`/`continue` inside `match` inside a loop; nested `break` in `while` within
  `repeat`; `return` with a value from inside nested loops in an exec (only in functions);
  `match` on a non-constant/expression pattern; `repeat (i : n)` with a non-int index type;
  unbraced loop bodies.
* **Exec kinds:** `run_start`, `run_end`, action `pre_solve`/`post_solve` on an atomic
  action with result attributes, `header`/`declaration` (not observable), target-template
  execs, exec `super`/inheritance.
* **Library:** `print`, `error`, `fatal` (LIB-ERR), file ops, `urandom`/`urandom_range`
  (LIB-RAND), floating-point functions, message verbosity filtering.
* **Negative:** only unresolved names (4 + `diag_location`). No type-mismatch, illegal
  context (target function called from solve, `yield` in a function used at solve time),
  constant-required (`compile if` on a non-const, array size), `break` outside a loop,
  duplicate declaration, wrong argument count, or `runtime_error` / `solve_fail` tests
  (README lists these as not yet reachable).

## 6. Non-PASS expected entries, grouped by owning defect

bc: **none** (`bc.toml` is empty; all 113 tests, 662 seed-runs, pass).

| Key | Owning defect | op-model-py | op-model-sv |
|---|---|---|---|
| U-act | not an op-model entry: root action has an activity (`export_action.py`) — by design | 20 `act.*` + `L0.nested.001`, `L0.seq.bodies.001` (22) | same 22 |
| U-rand | not an op-model entry: action has `rand` attributes (`export_action.py`) — by design until entries take parameters | `proc.rand.input`, `types.cast.constraint`, `types.enum.rand`, `types.literal.type` | same 4 |
| U-attr | not an op-model entry: action reads its own attributes; entries take no parameters (`export_action.py`) | `proc.field.rw` | same |
| U-goi | a user function named `get_offset_of_instance` on a non-register component is taken for the `reg_group_c` method (py: `call_legality` classifies it as a register-offset FOLD by name; sv: `_fold_offset` matches by name) — **real defect**, name-based classification | `types.string.match` | same |
| U-range | op-model-py: range pattern in `match` (`ExprRange`) has no `expr_range` hook — **real defect** (sv passes) | `proc.match.ranges` | — |
| U-async | op-model-py sync form: blocking channel `get()`/`put()` need `--py-await async` (`HAVE_EVENT_WAIT=false`) — target option, the adapter runs the sync form | `sync.channel.depth` | — |
| U-xtr | op-model-sv: no executor delegation (`supports_executor_delegation=False`), the tap's overrides are refused — **real gap** | — | `L0P.tap.rw8/16/32/64` |

So op-model-py has 2 genuine backend defects (U-goi, U-range) plus 1 option-dependent
(U-async); op-model-sv has 2 (U-goi, U-xtr). Everything else is the entry-point model.

## 7. A C / C++ runner: what it would take

Confirmed absent. The blockers are in the backends, not only in the adapter:

1. **Entries.** Every corpus test is entered through `--export-action <root>`.
   `OpModelTarget.supports_entries` is `False` for op-model-c and op-model-cpp, so
   `op_model.py` raises `"... does not yet render exported actions"` — every test would be
   UNSUPPORTED. Needed: render `OpModel.entries` (and `EntryPoint.supers`) as an operation
   of the owning component (`<prefix>_<action>(_self)` in C, a member in C++), recorded in
   the manifest's `entries`. Py does it in ~15 lines (`PyOpModelBackend.emit_entry` reuses
   `emit_operation`); C must also add the names to `c_names` and the header sections.
2. **Package functions.** `supports_package_functions` is `False` for C/C++; ~25 corpus
   tests (most `proc/func_*`, `poll_*`, `yield_single`, `rand_input`, `types/*` with package
   functions, L0 `from_function`/`from_recursive`) call one and would be refused at the gate.
3. **Init blocks.** `supports_init_blocks` is `False`; `comp/*` (5 of 6), `func_calls`, and
   L0-tap need `exec init_down/init_up`.
4. **Executor delegation** (L0-tap ×4), and `HAVE_EVENT_WAIT=false` makes blocking
   channel ops (`chan_depth`) refused, as on py's sync form.
5. **Adapter** (`adapters/pssc_op_model_c.py`, `_cpp.py`, ~200 lines each, modelled on
   `pssc_op_model_sv.py`): compile with `--export-action`, `--emit-manifest`, C
   `c_link_style=vtable`; write `main.c`/`main.cpp` from the manifest (root create, entry
   name) — `tests/progseq/trace_harness.py`'s `_C_MAIN`/`_CPP_MAIN` are the template
   (`pssc_message` → `vprintf` + `\n`, a logging/answering memory seam); build with
   `available_c_compilers()`/`available_cpp_compilers()`; run with a timeout; map outcomes
   as the other adapters (gate refusal → unsupported, build failure → compile_error, non-zero
   exit/timeout → runtime_error). Message formatting is already shared and corpus-strict
   (`c/message_format.py`: `CFormat`/`CppFormat` handle `%d %u %x %s %n`).
6. **Runners + strict lists**: `test_compliance_op_model_c.py` / `_cpp.py` (marker
   `c_toolchain`, skip without a compiler) and `expected/op-model-c.toml`/`-cpp.toml`.

Until (1)–(3) land, a C/C++ runner would only record ~100 UNSUPPORTED entries. The
alternative — transcribing corpus tests into construct-matrix cases (`target function void
run()`, which C/C++ can already run) — avoids entries but creates a second copy of corpus
models that will drift from the checked source and loses the corpus checker's verdict; not
recommended except as a stop-gap for specific constructs.

## 8. The construct matrix mechanism (`tests/progseq/test_op_model_construct_matrix.py`)

(Mechanism only; another catalog covers its cases — 63 `Case`s at this revision.)

* **Case** = `Case(pss, want, args=(0x1000,), mem=None, xfail={}, refused={})` in the
  `CASES` dict, keyed by a descriptive name. `pss` defines `component pss_top` with a
  `target function void run()` (and usually a `solve function initialize(addr_handle_t…)`
  binding register groups); the shared `_REGS` register groups are prepended, and the
  harness adds `import std_pkg::*; import addr_reg_pkg::*;`. `want` is a hand-written list
  of trace lines, or `Refused([regex…])`. `args` are the constructor's handle arguments,
  `mem` preloads memory. `xfail` maps target → defect (strict xfail); `refused` maps
  target → `Refused` for a target that must reject the model.
* **Parametrization** (`_params`): every case × `("c","cpp","py","sv")`; skip if the
  toolchain is absent (`trace_harness.available`), markers `c_toolchain` (C/C++) and `sim`
  (SV).
* **Trace** (`trace_harness.run`): generate `op-model-<t>` with `progseq_root="pss_top"`;
  C uses prefix `pss_top` and the vtable link style; SV gets `extend component pss_top {
  export target function run; }` appended because its API is its exports. Then build and
  run: py imports the module and calls `PssTop(bus, *args).run()` over `pssc_rt.MemoryBus`
  (log from `bus.log`); C/C++ write a `main` with a map-backed memory that prints
  `read W 0xADDR 0xDATA` / `write …` and `pssc_message` → printf, compiled `-std=c99`/
  `c++17 -Wall -Wextra -Werror`; SV writes a testbench with a `plat_c` duck-typed platform
  and builds with Verilator `--binary`. Every target yields the same line format: memory
  accesses and `message()` text in order.
* **Comparison**: exact list equality `got == case.want`; for `Refused`, `compile_model`
  must raise `CompileError` whose diagnostic text matches every regex.
* **Difference from the corpus tier**: entered through a component function, not an
  exported action (so C/C++ run today); verdict is exact equality with a hand-written
  trace, not the corpus checker; no seeds; cases live in pssc, not in the shared corpus.
  It is the natural vehicle for constructs C/C++ must be held to before entries exist.
