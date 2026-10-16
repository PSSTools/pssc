# 04 — SystemVerilog and Python emission: what the tests cover

Part of the construct test audit; construct IDs are those of `taxonomy.md`.
Scope: emission of data types, expressions, functions and procedural
statements as SystemVerilog (op-model-sv; also the sv-native / sv-oo / sv-pure
testbench targets where they emit exec bodies) and Python (op-model-py). The
corpus compliance tier is noted only (catalogued in detail elsewhere).

Audit date 2026-10-16, tree at `394f75d` + working changes. Verilator 5.052 is
on PATH (`packages/verilator`), so `sim`-marked op-model tests RUN here.

Check styles used throughout:

| key | meaning |
|---|---|
| **T** | text grep / equality of generated source |
| **G** | golden snapshot (byte-exact) |
| **X** | generated code EXECUTED with a behavioural check (Python: imported and driven; SV: Verilator build + run, trace compared) |
| **L** | compile/lint only (verilator `--lint-only`, `py_compile`, mypy) — no behaviour |
| **R** | refusal test (expects a CompileError / diagnostic) |
| **U** | unit check on an emitter helper or IR object, no full model |

"Executed" and "text-checked" are kept strictly apart below: a text check
proves a spelling, not that the spelling means the PSS.

---

## 1. The emitters: handled / refused / silently wrong

Sources: `src/pssc/targets/sv/lower_progseq.py` (SV), `targets/py/lower_progseq.py`
(PY), `targets/sv/sv_builtins.py` (SVB), `call_legality.py` (CL),
`validate_calls.py` (VC), `py_progseq_tgt.py` (PYT), `progseq_tgt.py` (PT),
`ast2ir.py` (A2I). Line numbers are as of the audit.

Refusals happen at four levels: the front end (A2I, all targets); the call
gate (`op_model.check` → `CL.classify`: UNKNOWN, Tier-0, wrong context); a
missing `BodyWalker` hook (`_BodyEmitter defines no x()` — an internal-style
error, raised before any file is written); and explicit raises in an emitter.
Only the first three are "designed" refusals; the fourth is often accidental.

### 1.1 Silently wrong or crashing after the gate (most important)

| # | Target | Construct | What happens | Where |
|---|---|---|---|---|
| S1 | SV | ST-FOREACH element form `foreach (v : arr)` | `stmt_foreach` uses `s.target` (the ITERATOR) as the SV index and ignores `index_var`: renders `foreach (arr[v])`, so `v` is an index, not an element. `foreach (v : arr[i])` leaves `i` undeclared. Index form is right. | SV:1504-1517 |
| S2 | SV | EX-ASSIGN `>>=` on a signed target | `stmt_aug_assign` emits `>>=` verbatim (logical); only `expr_bin` turns signed `>>` into `>>>`. | SV:1112 vs SV:797 |
| S3 | PY | LIB-ERR `error(...)` / `fatal(...)` | Pass the gate (Tier-1 utility) but are not in `PY_BUILTINS`; `CallDispatch` raises "call_utility() did not recognise it". Internal error, not a refusal. | PY:109, PY:1088 |
| S4 | PY | `get_offset_of_instance[_array]` called from an operation body | FOLD in every context, but `call_fold` exists only on the ctor mixin: "defines no call_fold()". | PY:1942 |
| S5 | both | FN-PARAM direction (`output`/`inout`) and `ref` kind | A2I never reads `getDir()`/`getKind()`; an `output` scalar is passed by value. A parameter whose type does not translate (float) silently vanishes from the signature. | A2I:3989-4012 |
| S6 | SV | LIB-MSG `print`, `format`, LIB-ERR `error`/`fatal` | Format string copied verbatim into `$write`/`$sformatf`/`$error`/`$fatal` (PSS 21.1.1 specifiers are not SV's); `$fatal(status,…)` passes a PSS status where SV accepts 0/1/2. Only `message` translates. | SVB:79-97 |
| S7 | PY | EX-SEL as an `op=` target (`x[7:4] += 1`) | `stmt_aug_assign` does not check `bit_select` on its target; appears to render an assignment to an expression (module would not import). Not run. | PY:1508 |
| S8 | SV | DT-REG `read_field` | Not reduced by the front end; emitted as a plain call to a runtime TASK with an output argument — does not compile. (PY refuses it: "unsupported register method 'read_field'".) | SV expr_call; `share/sv/pssc_reg_pkg.sv:181` |
| S9 | SV | DT-CHND | Every `chandle` is mapped to `addr_handle_t`. | SV:72-77 |
| S10 | SV | DT-CAST `(int[8])u`, `(bool)x` | Size cast keeps the operand's signedness; a bool cast is `1'(x)` (low bit, not `!= 0`). | SV:96, SV:828 |
| S11 | PY | DT-CAST to bool | Identity: `(bool)5` stays 5. | int_semantics.py:175 |
| S12 | SV | ST-MATCH with no matching arm and no default | Falls through silently (PY raises, per 20.7.10). | SV:1473 |
| S13 | both | DT-FP | A2I returns None for `DataTypeFloat` without an error; a later generic failure ("unsupported SV type for NoneType" / "no default value … NoneType"). Refused in effect, but unlocated. | A2I:5236 |

S1, S2 and S7 come from reading the code (S1 confirmed by reading
`stmt_foreach`); none is exercised by any test, which is why they survive.

### 1.2 Per-construct status

H = handled, R = refused (designed), I = fails with an internal / no-hook error, S = silently wrong (see 1.1).

| ID | op-model-sv | op-model-py |
|---|---|---|
| DT-INT | H any width (`sv_type`); literal >64 bits R | H any width (`IntSemantics`) |
| DT-FP | I (S13); LIB-FP Tier-0 R | I (S13) |
| DT-BOOL | H (`bit`) | H |
| DT-ENUM | H: prefixed items, `E'(x)`, first-item default, `%n` via `pss_<e>_name`; `--sv-enum-items verbatim` clash R | H: namespace class |
| DT-STR | type H; methods R (UNKNOWN); substring I (no `expr_slice`) | same |
| DT-CHND | S9 | H (int) |
| DT-STRUCT | H; packed for `packed_s`; struct literal H; non-const struct param `ref` | H: value classes, copy, `__eq__`; struct literal `{.a=..}` I |
| DT-ARR | H fields, locals, copy, initializers; array PARAM R (VC:447) | H incl. params by handle |
| DT-ARR methods / `in` | R (UNKNOWN) / I | same |
| DT-LIST/MAP/SET | I ("unsupported SV type for DataTypeList") | I ("no default value…"); foreach R |
| DT-REF | S5 | S5 |
| DT-TYPEDEF | package typedef resolved away (H); in a type body R (A2I) | same |
| DT-CAST | H (`N'()`, `E'()`), S10 | H for ints, S11 |
| DT-ADDR | H `make_handle_from_handle` → `+`, `addr_value`; `add_region` R | H incl. transparent `add_region` |
| DT-REG | H read/write/read_val/write_val/write_val_masked; `read_field` S8; >64-bit R | same; `read_field` R |
| DT-CONST | H as `localparam` by name | H folded |
| EX-ARITH | H; `**` I ("unsupported binop Exp", SV:782) | H incl. `**` |
| EX-REL/EQ/BIT | H | H (`_int_compare`) |
| EX-LOG | H, short-circuit hoisting | H; async R if RHS crosses the seam |
| EX-RED | R (A2I:5176, all targets) | R |
| EX-SHIFT | H (signed `>>>`), S2 | H |
| EX-COND | H, arms hoisted | H; async R if an arm crosses the seam |
| EX-IN | I (no `expr_in`) | I |
| EX-SEL | H read + write | H read + RMW write; S7 |
| EX-IDX | H | H |
| EX-SUBSTR | I | I |
| EX-WIDTH | H by SV's own context rules (no `IntSemantics`) | H by explicit LRM 8.7 conversions |
| EX-ASSIGN | H `= += -= <<= >>= \|= &=` (parser has no `*= /= %= ^=`); S2 | H |
| EX-REF | H this/super/param-shadows-field; `ExprRefBottomUp` I | same; bottom-up R with a message |
| FN-DECL | H; package fn reaching the platform R (SV:2392) | H |
| FN-PARAM | defaults H (`= {}` I); varargs not rendered; S5 | defaults H (constant only, else R) |
| FN-PURE | H as plain function | same |
| FN-CALL | H nested, recursion (`automatic`), qualified, task hoisting | H, `_awaited` hoisting |
| FN-IMPORT | H via `pss_imp` | H via `self._imports` |
| FN-EXPORT | root exports only; none → R | same |
| FN-SOLVE | H; target→solve R | H |
| FN-VIRT | H native `extends`, `super.f` | H native, `super().f` |
| FN-TT | R (A2I) | R |
| ST-BLOCK/VAR/ASSIGN/VCALL/RET | H | H |
| ST-REPEAT | H | H; index write R |
| ST-RWHILE | H (`forever`+`break`, first-pass flag for `continue`) | H bottom-tested |
| ST-WHILE | H | H |
| ST-FOREACH | index form H; element form S1; collections I | H fixed arrays both forms; R unknown size / iterator write |
| ST-IF | H | H |
| ST-MATCH | H incl. ranges (`case inside`) and default; S12 | H incl. default; ranges I (no `expr_range`) |
| ST-BRK | H | H |
| ST-RAND | R (A2I) | R |
| ST-CIF | H (resolved by parser) | H |
| ST-YIELD | H `#0` | sync: comment; async: `await yield_()` |
| ST-SUPER | H in init execs / exported actions, else R | same |
| EXEC-KIND | init_down/init_up H; component body/run_*/pre/post_solve R (VC:198); action body via `--export-action` | same |
| EXEC-TT | R (A2I) | R |
| LIB-MSG | `message` H (width/flags/precision/`%X` R); `print`/`format` S6 | `message`/`print` H; `format` R |
| LIB-FILE | R Tier-0 | R |
| LIB-ERR | S6 | I (S3) |
| LIB-RAND | H `$urandom`, `$urandom_range(max,min)` | R (`_NO_PY_PRNG`) |
| LIB-FP | R | R |
| LIB-MEM | read/write8..64 H; executor override R; bytes/struct R | H with executor delegation; bytes/struct R |
| LIB-REG | see DT-REG | see DT-REG |
| LIB-CHAN | H get/put tasks, try_* functions | try_* H; get/put R sync, H async; channel ARRAY I |

### 1.3 Other SV targets (sv-native / sv-oo / sv-pure)

"sv-oo" is sv-native with the `oo_api` projection. Its exec-body emitter is
the string-based `targets/sv/lower_stmts.py` + `lower_exprs.py`, entirely
separate from op-model-sv. It is considerably weaker, and its failures are
mostly SILENT (a stderr warning, a comment in the output, or invalid SV):

- Local variable declarations → `// unsupported stmt: StmtAnnAssign`; later uses do not compile (`lower_stmts.py:220`).
- Native PSS function DEFINITIONS are never emitted, only their calls (`pss_to_sv.py` has no function pass).
- Component `exec init_down/init_up` dropped with no message.
- `repeat (i : N)` → `foreach (3[i])` (invalid); match range → `[1:3]:` in a plain `case` (invalid).
- `?:`, aggregate literals, part-select, string methods, struct literals → `/* unsupported: X */`; `**` → `??`; `**=` → `+=`; reduction → `!`.
- Collection fields are emitted as `bit [31:0] [$] data` etc. (unpacked dimension on the wrong side) — and `tests/unit/sv/test_lower_types.py` ASSERTS that invalid text.
- `urandom_range` passes through without `$` and unswapped; `s.size()` verbatim.

sv-pure (`lower_pure.py` + be-sv `core_to_sv.py`): only `body` lowered; a user
`post_solve` is dropped silently; a local declaration or unknown statement
raises an uncaught `NotImplementedError` (no fallback to sv-native).

The `sw` family (c-host, sv-dpi, sv-dpi-bridge) emits exec bodies as C via
zuspec-be-sw; it emits no SV procedural code and is out of scope here
(`tests/unit/test_target_sw.py` is a C-target suite).

---

## 2. Test catalog

### 2.1 `tests/progseq/test_op_model_construct_matrix.py` — the cross-target trace matrix

Each case is a PSS model plus a hand-derived memory-access/message trace; it
runs on c, cpp, py and sv through `trace_harness.py` (py: imported in-process;
sv: `verilator --binary` with a generated `tb.sv` whose platform prints every
access). Every non-refused leg is **X**; refused cases are **R** on the same
diagnostic for every target. Run for this audit (py and sv legs only):
**126 passed, 2 xfailed** — every SV leg passes; the only strict xfails are two
py legs.

63 cases (+ `test_an_enum_field_has_its_base_types_width_in_the_manifest`, T on the manifest, all 4 targets):

| # | Case | Constructs | py | sv |
|---|---|---|---|---|
| 1 | two groups at two handles | DT-REG groups, LIB-REG write_val, FN-SOLVE | X pass | X pass |
| 2 | a group at a handle made from another | DT-ADDR make_handle_from_handle | X | X |
| 3 | three groups bound in reverse order | DT-REG | X | X |
| 4 | a group no set_handle reaches | DT-REG binding | R | R |
| 5 | a sub-component named t | naming, EX-REF sub-component, FN-CALL | X | X |
| 6 | a component, a struct and an enum with one stem | DT-STRUCT/DT-ENUM param, naming | X | X |
| 7 | names the generated code also uses | naming (`_self`, `bus`), EX-REF `this.` | X | X |
| 8 | root constructor parameters named like generated ones | FN-SOLVE params, naming | X | X |
| 9 | sub-components nobody initializes | field init, component arrays | X | X |
| 10 | a sub-component whose constructor nobody calls | FN-SOLVE (20.1.2) | **strict xfail** (`_PY_LAZY_SUB`) | X |
| 11 | a constructor writes into a child before constructing it | FN-SOLVE, EX-REF child field | **strict xfail** (`_PY_LAZY_SUB`) | X |
| 12 | a constructor with a local, a condition and a loop | ST-VAR, ST-IF/else, ST-REPEAT in ctor | X | X |
| 13 | calling a sub-component's operation | FN-CALL | X | X |
| 14 | a sub-component's register written from above | EX-REF, LIB-REG | X | X |
| 15 | operations of a sub-component array | ST-FOREACH (index) over component array, EX-IDX | X | X |
| 16 | a sub-component's value in a condition | ST-IF, FN-CALL value, LIB-REG read_val | X | X |
| 17 | a part select read | EX-SEL `[31:12]`, packed struct field | X | X |
| 18 | a bit select and the high half of 64 bits | EX-SEL bit + `[63:32]` | X | X |
| 19 | a bit select with a run-time index | EX-SEL dynamic, ST-WHILE, `+=` | X | X |
| 20 | a part and a bit select assigned | EX-SEL write (RMW) | X | X |
| 21 | a part select of a register field assigned | EX-SEL write on struct field, LIB-REG read/write | X | X |
| 22 | a match with a default arm | ST-MATCH values, `[2,3]`, default | X | X |
| 23 | match on enum items | ST-MATCH on DT-ENUM | X | X |
| 24 | match on items of a package's enum | ST-MATCH, package enum, import | X | X |
| 25 | offsets from an if chain | DT-REG offset eval (build-time), ST-IF on string | X | X |
| 26 | offsets from an if chain, out of order and sparse | same | X | X |
| 27 | an array-offset function that answers no for everything | same | X | X |
| 28 | an if chain with no case for a register | offset eval | R | R |
| 29 | an offset function the build cannot evaluate | offset eval, FN-IMPORT | R | R |
| 30 | an enum field in a packed register struct | DT-ENUM `: bit[2]` in packed struct | X | X |
| 31 | an unsized enum in a packed register struct | front-end refusal | R | R |
| 32 | a conditional expression | EX-COND | X | X |
| 33 | a nested conditional whose arm reads a register | EX-COND nested + hoisted task | X | X |
| 34 | a value-returning operation in a condition | FN-CALL in ST-IF, DT-BOOL return | X | X |
| 35 | ~ in a context narrower than int | EX-BIT `~`, EX-WIDTH (bit[4]) | X | X |
| 36 | ~ in a context wider than its operand | EX-WIDTH into 64-bit param | X | X |
| 37 | ~ on a bit passed to a 32-bit parameter | EX-WIDTH 8.7.2 | X | X |
| 38 | a narrow local wraps at its own width | DT-INT bit[4], `+=` wrap | X | X |
| 39 | a narrow signed value overflows to negative | DT-INT int[8], sign extension | X | X |
| 40 | signed division truncates toward zero | EX-ARITH signed `/` `%` | X | X |
| 41 | ~ then a right shift, assigned to 8 bits | EX-SHIFT `>>`, EX-WIDTH | X | X |
| 42 | fields, a local and a parameter named as SV keywords | naming | X | X |
| 43 | array fields of a component | DT-ARR fields (both syntaxes), ST-FOREACH index, `+=` | X | X |
| 44 | struct defaults in a local and a nested struct | DT-STRUCT defaults, nested, enum field | X | X |
| 45 | struct defaults in array elements | DT-ARR of struct | X | X |
| 46 | default values: an enum's first item, a bare local | DT-ENUM default, ST-VAR no init | X | X |
| 47 | repeat: counted, indexed, nested, empty | ST-REPEAT all forms, ST-BRK break/continue | X | X |
| 48 | solve functions the constructor calls | FN-SOLVE private ops | X | X |
| 49 | a target function calling a solve function | context gate | R | R |
| 50 | fields, locals and a parameter named as C keywords | naming | X | X |
| 51 | local arrays | DT-ARR local, copy `v = w`, of struct, ST-FOREACH | X | X |
| 52 | local arrays: enum elements, two dimensions, copied structs | DT-ARR 2-D, copy semantics | X | X |
| 53 | an array parameter | FN-PARAM array by handle | X | **R** (refused for c/cpp/sv) |
| 54 | array initializers | EX-LIT aggregate, nested, swap `w = {w[1], w[0]}`, enum array | X | X |
| 55 | message: %n of an enum and a bool, %d at the PSS width | LIB-MSG %n %d %u %x %% | X | X |
| 56 | enum items named as the core library's | DT-ENUM linker resolution, DT-CAST enum→bit[32] | X | X |
| 57 | two enums with an item of one name | DT-ENUM scoping, `e::A`, %n | X | X |
| 58 | an enum field of a register value read back | DT-ENUM in packed reg value, LIB-MSG | X | X |
| 59 | an array initializer of the wrong size | gate | R | R |
| 60 | register values an operation takes and returns | DT-REG value as param/result, ST-RET struct | X | X |
| 61 | a struct field wider than 64 bits | DT-INT bit[256], EX-SHIFT `<<200`, EX-SEL | X | X (c/cpp R) |
| 62 | a local, parameter and result wider than 64 bits | DT-INT >64 params/results, DT-CAST | X | X (c/cpp R) |
| 63 | a register wider than 64 bits | DT-REG | R | R |

(63 `test_construct` cases × 2 targets = the 126 that ran.)

What the matrix does NOT reach (no case): floats, strings beyond `match(name)`
in build-time offset functions, lists/maps/sets, `in`, `**`, reduction, casts
other than to `bit[N]`, `>>=`, `foreach (v : arr)` element form, repeat-while,
match ranges, `yield`, channels, `print`/`error`/`fatal`, urandom, package
functions, struct literals, EXEC init blocks, inheritance (that is
`test_op_model_inherit_native.py`).

### 2.2 Python (op-model-py)

| File | Tests → constructs | Style |
|---|---|---|
| `test_op_model_py.py` (WB DMA + `dma_engine`) | module shape, stdlib-only imports, Protocol = what bodies call | T |
| | import-API runtime checks (`check_import_api`) | X (runtime objects, no PSS body) |
| | DT-REG folded offsets, reserved regs, sub-component base, sparse/out-of-order/nested group offsets (ST-MATCH on string) | X |
| | DT-REG value class round trip, field masking; misspelled field → TypeError | X |
| | LIB-REG write + RMW arming write; EX-ARITH unsigned `/`; ST-RWHILE poll (do-while semantics), ST-RET | X (access trace) |
| | LIB-CHAN `try_get` output cell (T); try_get/try_put round trip (X); blocking `get` in async really suspends (X, async); sync refuses `get`/`put` (R) | mixed |
| | ST-YIELD sync → comment | T + import |
| | async vs sync: AST equality (T); identical access logs (X both forms); mypy colour check (L); banner snippet (X) | mixed |
| | conformance kit, common tier, `py_compile` | X / L |
| `test_op_model_py_int.py` | EX-ARITH signed `/` `%` (8.5.1), MIN/-1; EX-REL/EQ signedness; EX-WIDTH 8.7 (assignment-like contexts, self-determined operands, mixed signedness); unary `-`/`~`; EX-SHIFT both directions; `**` incl. negative exponent; EX-COND width; EX-ASSIGN `+= -= <<=` wrap; ST-RET / FN-PARAM conversion; DT-CAST `(int[8])` `(bit[4])`; field initializers; ST-FOREACH iterator + index | **X** (message log); constant `/0` R; foreach iterator/index write R; `pss_int` helpers exhaustively U (4-bit grid, Table 13) |
| `test_op_model_py_message.py` | LIB-MSG: `_pss_fmt` vs bc's formatter, 18 specifiers × 8 int types × 11 values; %n bool/enum, %s, %% | U (differential vs bc; skipped without be-bc) |
| | values print at their PSS type (int, bool, string, enum, bit[4], width of `bit[8]+1`) | X |
| | bad formats (5 cases), `%f` | R |
| `test_op_model_py_structs.py` | DT-STRUCT defaults, nested, enum first item, string field; copy on assign, `==` by value, no aliasing; struct params by handle, return copies; struct inheritance slicing; ST-FOREACH iterator aliases struct element; transparent `add_region` | X; region refusals R |
| `test_op_model_py_repeat.py` | ST-REPEAT count once (incl. side effect, register read in async), index scoping/shadowing (ST-BLOCK) | X; index write R |
| `test_op_model_py_pkg_functions.py` | FN-DECL package scope, FN-CALL recursion (`fact`), default args, qualified `util::neg`, shadowing | X; T for "generated once"; R for context |
| `test_op_model_py_init_blocks.py` | EXEC-KIND init_down/init_up order, multiple blocks merged, ST-SUPER `super;` in init, inherited blocks | X; context refusals R |
| `test_op_model_py_executors.py` | LIB-MEM / LIB-REG executor delegation (21.13.9), inheritance of executors, FN-VIRT `super.write32`, qualified recursion (RecursionError), descriptor params with defaults `= {}`, DT-CAST, EX-SHIFT/BIT, LIB-MSG `%x`, async | X; 8 refusal tests R; cross-target qualified-call identity (c/cpp/sv/py) T |
| `test_op_model_py_inherit.py` | FN-VIRT dispatch, inherited register block, `super.f`/`super.a`, shadowed fields, param shadows field | X; 3 refusals R |
| `test_op_model_py_super.py` | exported-action inheritance, ST-SUPER first/last/two levels, separate locals, ST-RET in base | X both forms; R for rand/pre_solve/activity in base |
| `test_op_model_entry.py` | `--export-action` entry: action exec body, `comp.` refs, `+=`, ST-VAR, ST-IF, LIB-MSG | X; 6 ExportActionError R (U-level) |
| `test_param_shadows_field.py` | EX-REF param shadows field | T on 4 targets; X on py |
| `test_forward_subcomponent.py` | declaration order | T (4 targets); X (py) |
| `test_manifest.py` | every manifest offset matches a live py accessor | X |
| `test_golden_op_models.py` | `py-default`, `py-async` snapshots of WB DMA | G |
| `test_op_model_inherit_native.py` | see 2.3 | X |

### 2.3 SystemVerilog (op-model-sv)

| File | Tests → constructs | Style |
|---|---|---|
| `test_op_model_construct_matrix.py` | 2.1 — 59 executed cases + 6 refusals per target | **X** (Verilator) |
| `test_op_model_inherit_native.py` | 5 cases × {py, c, cpp, sv}: FN-VIRT 3-level dispatch, derived root, inherited register block (EX-BIT `\|`, ST-MATCH offsets), `super.f`/`super.a`/`super.a += 1`, shadowed field, ctor `super.initialize` + DT-ADDR | **X** (same trace on all) |
| `test_sv_op_model_sim.py` (`sim`) | (1) DT-REG/LIB-REG, FN-SOLVE, EXEC init_down binding, ST-WHILE, `+=`, FN-VIRT + `super.wait_idle()`, shadowed field; (2) init_down/init_up order, ST-SUPER `super;`; (3) task hoisting: FN-CALL in expressions, EX-LOG short-circuit, EX-COND only chosen arm runs, ST-WHILE condition re-evaluated, ST-RWHILE + `continue`, nested `f(f(5))`, `%n` on bool; (4) FN-PARAM const struct as `input` taking an aggregate literal with default fill, non-const struct by `ref`, package functions, struct defaults | **X** |
| `test_op_model_sv.py` (WB DMA) | 22 structure tests: exports API (FN-EXPORT), members keep PSS names, component arrays, channels as members, construction split (FN-SOLVE), init propagation, ST-FOREACH over component array, DT-ADDR folding, field defaults, ST-YIELD → `#0`, task results via output arg, DT-ENUM typedefs, ST-MATCH → `case` (string presence only), `while(1)` → `forever` + `break`, struct `ref` params, packed/unpacked structs, DT-CONST spelled by name and typed | **T** (one test regex-extracts the channel offset expression and `eval`s it — values checked without a simulator) |
| | lint of the generated package | L (warnings fatal; skipped without Verilator, no `sim` marker) |
| | model exporting nothing | R |
| `test_sv_constants.py` | DT-CONST / DT-ENUM naming rules, typed localparams (64-bit hex, signed `-8'sd3`, bool), package-qualified constants, array size constants | U + T; end-to-end lint silently degrades to T without Verilator; verbatim enum clash R |
| `test_sv_reg_fields.py` | DT-REG field constants, LIB-REG `write_field`/`write_val_masked` rewrites, EX-WIDTH `32'(enable)`, named vs folded | T; IR invariance U; lint L |
| | `test_field_writes_produce_the_right_register_value`: runtime `pssc_reg_pkg` + constants under a hand-written tb (RMW preserves neighbours, top field, `read_field`, `write_fields` one transaction) | X — but of the RUNTIME package and constants, not of model call sites |
| `test_sv_struct_array_member.py` | DT-STRUCT with DT-ARR member as export param/result | T only (crash regression; no lint) |
| `test_sv_backend_class.py` | backend architecture (sections, files, subclassing) | T (structural) |
| `test_op_model_channel.py` | LIB-CHAN on SV: runtime channel, construction, blocking `get` as task, try_* keep return value, discard temps sized to element width, `void'()` predicates | T; lint L. No SV channel EXECUTION here (py: none in this file) |
| `test_sim_wb_dma.py` (`sim`) | small `dma_engine` model, hand-written self-checking tb via dv-flow on Verilator | X (whole-model PASS string; constructs not itemised) |
| `test_golden_op_models.py` | `sv-named`, `sv-folded` WB DMA snapshots | G |
| `test_executors.py::…every_target_lowers_a_qualified_call…`, `test_qualified_import_call.py`, `test_param_shadows_field.py` | qualified call identity, param shadowing | T |
| `tests/sv/test_zsp_rt_pkg.py` | hand-written runtime package tests | X on VCS/Questa only — not collected here (no Verilator param) |

### 2.4 Other SV targets

| File | Constructs | Style |
|---|---|---|
| `tests/unit/sv/test_lower_stmts.py` | hand-built IR: ST-VCALL, ST-ASSIGN, `+=`, ST-RET, ST-BRK, assert, raise→`$fatal`, ST-IF, ST-WHILE, ST-FOREACH, ST-REPEAT, ST-RWHILE, ST-MATCH value/default | U (exact strings) |
| `tests/unit/sv/test_lower_exprs.py` | EX-ARITH/BIT/SHIFT/EQ/REL/LOG, unary, EX-REF/IDX/SEL, EX-LIT, FN-CALL | U |
| `tests/unit/sv/test_lower_types.py` | DT-ENUM, DT-STRUCT, DT-LIST/ARR/MAP, chandle, signedness | T/U — **pins invalid collection syntax** (`bit [31:0] [$] data`) |
| `tests/unit/sv/test_lower_exec_body.py` | LIB-MSG, EXEC-KIND body/pre_solve; `yield` via an unreachable call path | U |
| `tests/unit/sv/test_sv_builtins.py` | message/print/error/fatal/urandom/urandom_range/format | U — of `sv_builtins.py`, which only op-model-sv uses |
| `tests/unit/sv/test_lower_imports.py` | FN-IMPORT (arg types never checked) | T/U |
| other `tests/unit/sv/*` (activity, flow, resources, schedule, constraints, e2e_control_flow) | activity-level; no exec procedural content beyond `message`/`cover` | T/U |
| `tests/unit/test_target_sv_oo.py` | CLI/API shape; model has `post_solve { print }`, unasserted | T |
| `tests/sim/sv/test_export_api_e2e.py`, `test_pss_flow_e2e.py` | sv-native: `post_solve { print }`, body with nested import calls | X (Verilator) |
| `tests/sim/sv/test_sv_pure_e2e.py` | sv-pure: `message("%0d", in.x)` | T + X (Verilator) |
| `tests/sim/sv/test_sv_pure_smoke.py` | hand-built be-sv IR, not pssc lowering | G + X |
| `tests/sim/sv/test_bridge_e2e.py` | sv-dpi-bridge (exec bodies in C): print, nested import calls | X |
| `tests/sim/sv/test_sim_pss.py`, `test_sim_sequential.py`, `test_sim_patterns.py` | no exec bodies / `message` only | X on VCS/Questa only — skipped here |

### 2.5 Compliance (noted only)

`tests/compliance/test_compliance_op_model_py.py` (no marker) and
`test_compliance_op_model_sv.py` (`sim`, Verilator) run the pss-corpus
executable tier (113 tests) as `--export-action` entries; verdict by the
corpus checker — **X**. Strict expected lists:

- `expected/op-model-sv.toml`, 32 entries: 22 activity, 4 rand, 1 action-attribute read (all out of op-model scope); real gaps are `L0P.tap.rw8/16/32/64` (no executor delegation on SV) and `types.string.match.001` (a user function named `get_offset_of_instance` taken for the reg-group method).
- `expected/op-model-py.toml`, 30 entries: the same 27 out-of-scope + `types.string.match.001`; py-only gaps `proc.match.ranges.001` (no `expr_range`) and `sync.channel.depth.001` (blocking channel needs async).

The corpus is the only executed coverage for several constructs on both
targets (e.g. match ranges on SV); see the corpus catalog for which.

---

## 3. Coverage table

Cells: handled? / best test / style. **X** = executed, **T** = text only,
**G** = golden only, **R** = refusal tested, **—** = no test. "M#" = row of the
construct matrix (2.1), all of which execute on both py and sv unless marked.

| Construct | SV (op-model-sv) | Py (op-model-py) |
|---|---|---|
| DT-INT bit[N] narrow wrap | H / M38 / X | H / M38, py_int / X |
| DT-INT signed int[8] overflow | H / M39 / X | H / M39, py_int / X |
| DT-INT >64 bits | H / M61-62 / X | H / M61-62 / X |
| DT-INT mixed signedness compare | H / — | H / py_int / X |
| DT-FP | I / — | I / — |
| DT-BOOL | H / M34, sv_sim(3) / X | H / py_message / X |
| DT-ENUM explicit/implicit values, default first item | H / M46, M56 / X | H / M46, py_structs / X |
| DT-ENUM scoping, same item in two enums | H / M57 / X | H / M57 / X |
| DT-ENUM in packed reg value | H / M30, M58 / X | H / M30, M58 / X |
| DT-ENUM `--sv-enum-items verbatim` | R / sv_constants / R | n/a |
| DT-STR locals/fields | H / — (string field only in WB DMA text) | H / py_structs, py_message / X |
| DT-STR methods, substring | R / I — / — | R / I — / — |
| DT-CHND | **S9** / — | H / — |
| DT-STRUCT defaults, nested | H / M44 / X | H / M44, py_structs / X |
| DT-STRUCT copy on assign | H / M52 (array of struct) / X | H / py_structs / X |
| DT-STRUCT `==` by value | ? / — | H / py_structs / X |
| DT-STRUCT param: const→input, non-const→ref | H / sv_sim(4) / X | H (handle) / py_structs / X |
| DT-STRUCT literal | H / sv_sim(4) / X | **I** / — |
| DT-STRUCT inheritance (value) | ? / — | H / py_structs / X |
| DT-STRUCT with array member | H / sv_struct_array_member / T | ? / — |
| DT-ARR fields, locals, 2-D, of struct, copy | H / M43, M45, M51, M52 / X | same / X |
| DT-ARR initializer literal | H / M54 / X; size R M59 | same |
| DT-ARR parameter | R / M53 / R | H / M53 / X |
| DT-ARR methods, `in` | R/I / — | R/I / — |
| DT-LIST / MAP / SET | I / — | I / — |
| DT-REF / param direction | **S5** / — | **S5** / — |
| DT-CAST to bit[N], enum→int | H / M56, M62 / X | H / M56, py_int / X |
| DT-CAST to int[N] (signed), to bool | **S10** / — | H int / py_int / X; bool **S11** / — |
| DT-ADDR make_handle_from_handle | H / M2 / X | H / M2 / X |
| DT-ADDR add_region | R / — | H / py_structs / X; R tested |
| DT-REG groups, offsets, arrays | H / M1-3, M25-29 / X | H / M1-3, test_op_model_py / X |
| DT-REG read_field | **S8** / — | R (untested) |
| DT-REG >64 bits | R / M63 / R | R / M63 / R |
| DT-CONST | H / test_op_model_sv, sv_constants / T + U | H (folded) / implicit |
| EX-LIT aggregate | H / M54, sv_sim(4) / X | H / M54 / X |
| EX-ARITH + - * | H / many / X | H / many / X |
| EX-ARITH signed / % | H / M40 / X | H / M40, py_int / X |
| EX-ARITH `**` | **I** ("unsupported binop Exp") / — | H / py_int / X |
| EX-REL / EX-EQ | H / M35 etc. / X | H / py_int / X |
| EX-LOG short-circuit | H / sv_sim(3) / X | H / — (sync, not specifically) ; async R untested here |
| EX-BIT `~` widths | H / M35-37, M41 / X | H / same + py_int / X |
| EX-RED | R (front end) / — | R / — |
| EX-SHIFT `<<` `>>` | H / M41, M61 / X | H / py_int / X |
| EX-SHIFT signed `>>` | H / — | H / py_int / X |
| EX-COND | H / M32-33, sv_sim(3) / X | H / M32-33, py_int / X |
| EX-IN | I / — | I / — |
| EX-SEL read, dynamic index | H / M17-19 / X | H / M17-19 / X |
| EX-SEL write (RMW) | H / M20-21 / X | H / M20-21 / X |
| EX-SEL `op=` target | ? / — | **S7** / — |
| EX-IDX | H / M43, M51 / X | same |
| EX-SUBSTR | I / — | I / — |
| EX-WIDTH 8.7 | H / M35-41 / X | H / M35-41 + py_int / X |
| EX-ASSIGN `+=` | H / M19, M38 / X | H / M38, py_int / X |
| EX-ASSIGN `-= <<= \|= &=` | H / — | H / py_int / X (`-=`,`<<=`), `\|=` T |
| EX-ASSIGN `>>=` signed | **S2** / — | H / — |
| EX-REF this / super / param-shadows-field | H / M7, inherit_native / X; param_shadows T | H / X |
| FN-DECL component | H / everywhere / X | H / X |
| FN-DECL package | H / sv_sim(4) / X; platform-reaching R untested | H / py_pkg_functions / X |
| FN-PARAM defaults | H / — | H / py_pkg_functions, py_executors / X |
| FN-PARAM varargs | not rendered / — | not rendered / — |
| FN-PURE | H (plain) / — | H / — |
| FN-CALL nested / in expression / hoisted | H / M33-34, sv_sim(3) / X | H / M33-34 / X |
| FN-CALL recursion | H / — | H / py_pkg_functions / X |
| FN-CALL qualified `pkg::f` | H / qualified_import_call / T | H / py_pkg_functions / X |
| FN-IMPORT | H / WB DMA / T, sim_wb_dma X | H / test_op_model_py / X |
| FN-EXPORT | H / test_op_model_sv / T; (all sims use it) | n/a (methods) |
| FN-SOLVE ctor + private solve fns | H / M8-12, M48 / X; M49 R | H / M48 X; **M10-11 strict xfail** |
| FN-VIRT / super.f | H / inherit_native, sv_sim(1) / X | H / inherit_native, py_inherit / X |
| FN-TT | R / — | R / — |
| ST-BLOCK shadowing | H / — | H / py_repeat / X |
| ST-VAR | H / M12, M46 / X | same |
| ST-ASSIGN / ST-VCALL | H / everywhere / X | same |
| ST-RET (value, struct) | H / M60, M34 / X | H / M60, py_int / X |
| ST-REPEAT counted/indexed/nested/zero | H / M47 / X | H / M47, py_repeat / X |
| ST-RWHILE | H / sv_sim(3) / X | H / test_op_model_py poll / X |
| ST-WHILE | H / M19, sv_sim(1,3) / X | H / M19 / X |
| ST-WHILE true → forever | H / test_op_model_sv / T | — |
| ST-FOREACH index form, data array | H / M43, M51 / X | H / M43, py_int / X |
| ST-FOREACH over component array | H / M15 / X | H / M15 / X |
| ST-FOREACH element form `(v : arr)` | **S1** / — | H / py_int, py_structs / X |
| ST-FOREACH list/map/set | I / — | R/I / — |
| ST-IF / else-if | H / everywhere / X | same |
| ST-MATCH values, default | H / M22 / X | H / M22 / X |
| ST-MATCH enum items | H / M23-24 / X | same |
| ST-MATCH ranges | H / corpus only (not in sv expected list) | **I** / corpus xfail `proc.match.ranges.001` |
| ST-MATCH strings | (build-time offset eval only, M25) | same |
| ST-MATCH no arm, no default | **S12** / — | H (raises) / — |
| ST-BRK break/continue, nested loops | H / M47 / X | H / M47 / X |
| ST-RAND | R (front end) / — | R / — |
| ST-CIF | H (parser) / — | H / test_op_model_py (target_cfg) / U |
| ST-YIELD | H `#0` / test_op_model_sv / T | sync comment T; async — |
| ST-SUPER in init | H / sv_sim(2) / X | H / py_init_blocks / X |
| ST-SUPER in exported action | H / corpus | H / py_super / X |
| EXEC init_down / init_up | H / sv_sim(1,2) / X | H / py_init_blocks / X |
| EXEC component body / pre/post_solve | R / — | R / — |
| EXEC action body (`--export-action`) | H / corpus / X | H / op_model_entry / X |
| EXEC-TT | R / — | R / — |
| LIB-MSG message %d %u %x %n %% | H / M55, sv_sim(3) / X | H / M55, py_message / X |
| LIB-MSG width/flags/precision | R / — | H / py_message (U vs bc) |
| LIB-MSG print / format | **S6** / test_sv_builtins U only | print H / —; format R / — |
| LIB-ERR error/fatal | **S6** / U only | **S3** (internal error) / — |
| LIB-RAND urandom | H / test_sv_builtins U only | R / — |
| LIB-FILE / LIB-FP | R / — | R / — |
| LIB-MEM read/write8..64 | H / WB DMA T, sim_wb_dma X | H / test_op_model_py, py_executors / X |
| LIB-MEM executor delegation | R / corpus xfail L0P.tap.* | H / py_executors / X |
| LIB-REG read/write/read_val/write_val | H / M-many / X | H / M-many / X |
| LIB-REG write_field/write_val_masked | H / sv_reg_fields T; runtime X | H / test_op_model_py RMW / X |
| LIB-CHAN try_get/try_put | H / test_op_model_channel / T + L | H / test_op_model_py / X |
| LIB-CHAN blocking get/put | H / test_op_model_channel / T + L | sync R / X async |
| LIB-CHAN channel array | H? / — | **I** / — |
| async py (`--py-await async`) | n/a | test_op_model_py, py_repeat, py_pkg_functions, py_super, py_executors / X; G `py-async` |

---

## 4. Holes

### 4.1 Silently wrong, and untested (fix or refuse; each needs a matrix case)

1. **SV `foreach (v : arr)` element form** (S1) — wrong code; py executes it correctly (`test_op_model_py_int`, `test_op_model_py_structs`). A matrix case would fail on sv.
2. **SV `>>=` on a signed target** (S2) — logical, not arithmetic.
3. **SV `print` / `format` / `error` / `fatal`** (S6) — PSS format passed to SV verbatim; only unit-tested in `test_sv_builtins.py`, which checks the verbatim spelling.
4. **SV `read_field`** (S8) — emits a call that does not compile.
5. **SV `chandle` as `addr_handle_t`** (S9); **SV signed/bool casts** (S10); **py bool cast** (S11).
6. **SV match with no matching arm** (S12) — LRM 20.7.10 says it is an error; py raises, SV falls through.
7. **Parameter direction / `ref`** (S5) on both targets — `output` scalars by value, untranslatable param types dropped from the signature.
8. **py `x[h:l] op= v`** (S7) — likely an unimportable module.

### 4.2 Internal errors where a designed refusal belongs

- py `error`/`fatal` (S3); py `get_offset_of_instance` in an operation (S4); py channel array; py struct literal; SV `**` ("unsupported binop Exp"); `in` (both); substring (both); match ranges on py (no `expr_range`); lists/maps/sets (both); floats (S13, unlocated). None has a refusal test; a refusal test would at least pin the message and location.

### 4.3 Refused, though the LRM allows it

- SV array parameters (M53 R; LRM 20.3.2) — py has them.
- SV executor delegation (21.13.9; corpus L0P.tap.* xfail) — py has it.
- py `urandom` (21.4), py `format`, py blocking channels in sync form (by design: async is the answer).
- Both: string methods (7.6), array methods / `in` (7.9.2), list/map/set (7.9), `randomize` (20.7.12), reduction ops, float, `read/write_bytes`, `read/write_struct`, file I/O, target-template functions/exec, component `exec body` / `run_start` / `run_end` / `pre_solve` / `post_solve` in op-models, `*= /= %= ^=` (parser).
- SV package functions that reach the platform (SV:2392) — untested refusal.

### 4.4 Handled, but only text-checked (never executed on that target)

- SV: ST-YIELD (`#0`), `while (1)` → `forever`, ST-MATCH → `case` on the WB DMA model (executed only via M22-24), DT-CONST spelling (lint only), channel lowering (try_*/get/put/discard temps — T + lint, never simulated in progseq; corpus `sync.*` is the only execution), struct with array member (T only, no lint), qualified calls, param-shadows-field (T on sv; py X), `write_field` rewrites at model call sites (runtime package executed separately with a hand-written tb), FN-IMPORT signatures on the WB DMA model.
- py: ST-YIELD sync comment; `|=` form; "generated once" package functions.
- SV builtins (`$urandom`, `$urandom_range` swap) — U only; no generated model runs them.

### 4.5 Golden-only

- `sv-named`, `sv-folded`, `py-default`, `py-async` hold the whole WB DMA output. Constructs whose only SV evidence on the real model is the snapshot plus text greps: channel operations in the WB DMA bodies, ST-YIELD, `forever`/`break`, the folded register-field RMW forms. A golden proves sameness only (AGENTS.md); these need either a matrix case or `test_sim_wb_dma`-style execution that reaches them (`test_sim_wb_dma` runs the small `dma_engine` model, not WB DMA).

### 4.6 Handled and executed on one target only

| Construct | Executed on | Missing on |
|---|---|---|
| ST-FOREACH element form | py | sv (and wrong there) |
| EX-ARITH `**` | py | sv (internal error) |
| DT-STRUCT `==`, value-struct inheritance | py | sv |
| FN-CALL recursion, default params | py | sv |
| ST-RWHILE | sv (sv_sim), py (WB DMA poll) | — but no matrix case holds both to one trace |
| EX-LOG short-circuit with side effects | sv | py (no dedicated test) |
| LIB-CHAN | py (try_*, async get) | sv (text only in progseq) |
| ST-SUPER in exported action | py | sv (corpus only) |
| ST-MATCH ranges | sv (corpus only) | py (xfail) |

### 4.7 Other SV targets (sv-native / sv-oo / sv-pure)

No Verilator test runs an sv-native or sv-pure exec body containing anything
beyond `print`/`message` and import calls. Local declarations, native function
definitions, component init execs, `repeat (i : N)`, match ranges, `?:` and
`**` are emitted as comments or invalid SV with no error, and no test asserts
a refusal for any of them. `tests/unit/sv/test_lower_types.py` pins invalid
collection-field syntax as expected output. sv-pure's `NotImplementedError`
paths and its silent `post_solve` drop are untested. Unit tests for these
emitters (`test_lower_stmts.py`, `test_lower_exprs.py`) work on hand-built IR
and check exact strings, so they assert spelling, never meaning.

### 4.8 Strict xfails in the matrix

Only two, both py: M10 "a sub-component whose constructor nobody calls" and
M11 "a constructor writes into a child before constructing it"
(`_PY_LAZY_SUB`: py builds a sub-component with a constructor at its
`initialize` call, so it is `None` until then — contrary to LRM 20.1.2).
SV passes every executed case.

---

## 5. Suggested next cases (in the matrix, so all four targets answer)

1. `foreach (v : arr)` and `foreach (v : arr[i])` over a data array (S1).
2. Signed `>>=`, `(int[8])` of an unsigned value, `(bool)` of a multi-bit value (S2, S10, S11).
3. `x ** y` (SV internal error today).
4. match with a range arm, and match with no matching arm and no default (S12; py `expr_range`).
5. `repeat { } while (c)` with `continue` — today held only by `test_sv_op_model_sim.py` and the WB DMA poll, separately.
6. `print("%d", x)` and `error("%d", x)` with a PSS-only specifier (S3, S6).
7. A struct `==` and a struct literal argument.
8. A channel round trip (try_put / try_get) — the first executed SV channel test in progseq.
9. Refusal cases for: string method, list field, `in`, `randomize`, float local — pinning a LOCATED user error instead of today's internal errors.
10. A function with an `output` parameter (S5) — expected to be refused until direction is carried.
