# 02 — Parse / IR test catalog (data types, expressions, functions, procedural statements)

Scope: what pssc's tests prove about the path **PSS text → pssparser AST →
`ast2ir` (`src/pssc/ast2ir.py`, `AstToIrTranslator`) → IR** for the DT-*, EX-*,
FN-*, ST-*, EXEC-* and LIB-* constructs of `taxonomy.md`. Emission by the four
op-model targets is out of scope here (catalogs 03–05); the bc integration
tests are included because a run on bc proves the construct parsed and reached
the IR correctly enough to execute.

Method: read every listed test file; `pytest --collect-only` (2299 tests in
`tests/unit`); grep of the PSS embedded in the tests; and a **probe** of ~110
small PSS snippets through `Parser → AstToIrTranslator` (2026-10-16, results
in §4). The probe is what tells "ast2ir refuses X" from "ast2ir silently drops
X" — the second kind is invisible to every test that only asserts
`len(ctx.errors) == 0`.

Depth scale used below:

* **smoke** — asserts only "no errors" / "type exists". Would pass if the construct vanished.
* **shape** — asserts IR node classes / operator / field values.
* **semantic** — executes the result (bc run-and-compare, runtime) and checks values.
* **hand-IR** — builds IR objects directly; proves the IR *class* exists, not that ast2ir produces it.

---

## 1. Per-file catalog

### 1.1 `tests/unit` — ast2ir translation tests

| File (tests) | Purpose | Constructs exercised | Check |
|---|---|---|---|
| `test_ast_to_ir.py` (24) | Original broad AST→IR suite | DT-INT/BOOL/STR builtins registered (int=32 signed, bool=1 unsigned); DT-STRUCT basic + inheritance; FN-DECL (void, return type, params); ST-VAR, ST-ASSIGN, ST-IF/else, ST-REPEAT (`repeat(n)` → `StmtFor`), ST-WHILE, ST-RET; EX-ARITH, EX-REL/EQ, EX-LOG, `!`/`-`, EX-COND | Mostly **smoke/weak shape**: body length and statement class; expression tests assert only `value is not None` (unary test comments "we accept any expression for now") |
| `test_stmt_expr.py` (11) | Statements + expressions in function bodies | ST-RET (literal, expr, void), ST-VAR (with/without init), ST-ASSIGN, ST-IF (no else / else); EX-LIT int; EX-ARITH/EX-REL/EX-EQ via subTest over `+ - * / == != < > <= >=` | **shape**: asserts `ExprBin.op` per operator, constant values |
| `test_stmt_translation.py` (11) | "Phase 2" statement translation | Docstrings claim repeat, repeat-with-index, repeat-while, foreach, yield, match, nested loops | **HOLLOW**: every PSS body is just `int x = 0;`-style declarations; none of the named statements appears in the source. Asserts no errors. Zero coverage of what it names. |
| `test_expr_translation.py` (7) | "Phase 1" expression translation | Docstrings claim range/`in`, list init, ternary, `**` | **HOLLOW**: same pattern — PSS contains only `int a = 2;`, `x + y * z`, `10 + 20`. Only `+`/`*` actually reach ast2ir. |
| `test_stmt_ir.py` (17) | IR classes for repeat/repeat-while/foreach/yield/randomize/match | ST-REPEAT, ST-RWHILE, ST-FOREACH (+index), ST-YIELD, ST-RAND, ST-MATCH (value patterns, guard), ST-BRK | **hand-IR** only — never parses PSS |
| `test_expr_ir.py` (19) | IR classes ExprRange/RangeList/In/IfExp/List/Dict/StructLiteral, `BinOp.Exp` | EX-IN, EX-COND, EX-LIT aggregates, EX-ARITH `**` | **hand-IR** only |
| `test_expr_phase3_ir.py` (21) | IR classes ExprCast, string methods, hierarchical/static refs, compile-has, null, slices | DT-CAST, DT-STR methods, EX-REF, EX-SEL, `compile has`, null | **hand-IR** only (note: ast2ir in fact drops `compile has`, §4) |
| `test_unary_operators.py` (3+param) | Regression for pssparser D5 (unary operator discarded) | EX-ARITH unary `-`/`+`, EX-LOG `!`, EX-BIT `~`; `+x` is not a negation; `!guard` ≠ `guard` | **shape**, operator-exact (parametrized over all four) |
| `test_scalar_types_ast_to_ir.py` (26) | Enum + typedef translation, enum→runtime IntEnum | DT-ENUM: auto values from 0, explicit, mixed, negative value folds, `extend enum` items fold, non-folding value is an error, item order, enum field in struct; DT-TYPEDEF (`typedef bit[8]`/`int[...]` → DataTypeInt, signedness, as field type) | **shape** + one **error** test; runtime builder checks (IntEnum values) |
| `test_collections_ast_to_ir.py` (28) | chandle + list/array/map/set fields, collection ops | DT-CHND field; DT-LIST/ARR/MAP/SET field types, element/key/value types, array size, `int a[4]` syntax; EX-IDX `items[0]`; list `push_back`, `size`, `clear`; map `delete`; list `==` | **shape** (field types; call is `ExprCall(ExprAttribute(attr=...))`); runtime type mapping |
| `test_aggregate_literals.py` (9) | Aggregate literals | EX-LIT: `{}`, value list, single element, map literal `{k:v}`, struct literal `{.a=..}` | **shape** (`ExprList`/`ExprDict`/`ExprStructLiteral`, element classes, field names) |
| `test_string_and_builtins.py` (11) | String methods, substring, builtins | DT-STR `size()`, `find(x)`, `lower()`, `upper()`; EX-SUBSTR `s[1..3]`; LIB-MSG `print("..")` | **shape**. `format`, `error`, `fatal` tests are **empty `pass` bodies** (claim std_pkg not importable — false; it is, see §4) |
| `test_package_function_calls_ir.py` (3) | Package-function calls carry the linker's resolution | FN-CALL qualified/unqualified to package scope (`ExprRefUnresolved("<pkg>::f")`), shadowing inside a package function (LRM 22.2), `target` qualifier recorded | **shape** |
| `test_extend_functions.py` (8) | `extend component` bodies reach the IR | FN-DECL via extend: plain, `target function`, `solve function`; actions/structs via extend; dispatch parity inline vs extend | **shape** (presence and count) |
| `test_inheritance_and_exec.py` (21) | Action/struct inheritance, abstract, exec kinds | DT-STRUCT super name, own-fields only; EXEC-KIND: struct pre/post_solve, component init_down/init_up/`init`/run_start/run_end; not async | **shape** |
| `test_component_arrays_and_functions.py` (27) | Component arrays, params, defaults, varargs, extends | FN-PARAM typed args, default values (`ExprConstant(42)`), varargs (`int ... b`→`args.vararg`), return/void; `super.x` in action; extend action/struct/enum | **shape**. The two `super.x` tests assert only `attr == "x"`; their docstring ("frontend cannot distinguish super from self") is stale — ast2ir now emits `TypeExprRefSuper`, which nothing asserts. The header's "pure function skipped" note is stale too: `is_invariant` IS set now (probe), and no test checks it |
| `test_addr_handle_chandle.py` (2+param) | `addr_handle_t` is a chandle in every projection | DT-ADDR / DT-CHND | **shape** on type-projection functions (not via parse) |
| `test_array_size_const.py` (4) | Array size given by a named constant folds | DT-ARR size: literal, `static const`, qualified const; unfoldable reported as -1 | **shape** |
| `test_ast2ir_super.py` (3) | `super;`, action bases as linked, refusal of untranslatable statements | ST-SUPER → `StmtSuper`; ST-RAND (`randomize s;`) → located error | **shape** + **error** |
| `test_package_and_yield.py` (8) | Package-qualified type keys; yield | ST-YIELD → `StmtYield(value=None)` | **shape** |
| `test_ctor_name.py` (8) | Which `solve function` is the constructor | FN-SOLVE (ctor / `\init` / override) | **shape** + generation-independence |
| `test_import_capture.py` (2) | Package-scope `import target/solve function` captured | FN-IMPORT | **shape** |
| `test_file_order_independence.py` (6) | Translation independent of file order | `extend` before target, cross-file const array size, unresolved type reported, template param not reported | **shape** + **error** |
| `test_ast_node_names.py` (1) | Every `pss_ast.X` referenced in pssc exists | (guard for dispatch) | source scan |
| `test_constraints_ast_to_ir.py` (18) | Constraint blocks | (constraints, not procedural — listed because it is the only unit test with a parsed `foreach` and `list<>`/`in` inside PSS) | **shape** |
| `test_register_*`, `test_reg_*`, `test_named_register_types.py`, `test_register_array*_bug.py` (~105) | Register model | DT-REG: `reg_c<T,ACC,SZ>`, named register types, reg_group_c, offsets, field extraction; LIB-REG read/write/read_val/write_val/write_masked/field forms; masked-write equivalence across spellings; RMW expansion | **shape** (several are hand-IR: `test_register_ir.py`); masks hand-derived |

### 1.2 `tests/unit/errors`

| File (tests) | Purpose | Constructs | Check |
|---|---|---|---|
| `test_load_pss_errors.py` (9) | `load_pss` error layering | syntax error, unresolved type → `ParseException`; ctx errors → `PssTranslationError` | **error** (exception type + message) |
| `test_parse_diagnostics.py` (14) | How a syntax error reaches the user | export-action misspellings, caret placement, CLI exit code | **error** (diagnostic text) |
| `test_reg_field_errors.py` (14) | Masked register writes that must be refused | LIB-REG: zero mask, write-only reg, unknown/aggregate/non-literal/hierarchical field name, duplicate names, length mismatch | **error** |

None of the three exercises a procedural-statement or expression error.

### 1.3 `tests/unit/integration` (bc backend — run-and-compare)

| File (tests) | Constructs proven on bc | Check |
|---|---|---|
| `test_bc_strings.py` (5+param) | DT-STR `==`/`!=` (literal, local, comp field, param), assignment, ST-MATCH on string w/ default; refused: `<` on strings, string==int, range pattern on string, string across an import | **semantic** + **error** |
| `test_bc_struct_values.py` (16) | DT-STRUCT locals with field initial values, deep copy on assign, nested struct assign, `==` over every field, struct param by handle, returned by value, call result as argument, attribute initial values, field initializer reading own struct; refused: struct where scalar needed, non-const initializer on local struct, struct exec block | **semantic** + **error** |
| `test_bc_recursion.py` (7) | FN-CALL recursion, mutual recursion, void recursion ordering, depth limit; refused: struct param on recursive fn | **semantic** + **error** |
| `test_bc_yield.py` (2) | ST-YIELD interleaving of parallel bodies; ST-WHILE + `+=` incidentally | **semantic** |
| `test_bc_memory.py` (5) | LIB-MEM read/write via platform and executors, DT-ADDR handle = address; refused: non-top-level `set_executor`, non-transparent space | **semantic** + **error** |
| `test_bc_channels.py` (8) | LIB-CHAN put/get/try_get FIFO, wrap, element type, blocking, per-instance, deadlocks, `try_get` arg assignable | **semantic** + **error** |
| `test_bc_literal_types.py` (3) | EX-LIT typing (Table 21): `0x10` unsigned, `16` signed, `8'hFF`, `4'sb1010`=-6; EX-WIDTH in procedural code (`bit[16] y = 8'hFF + 8'h01` = 0x100); DT-INT `int[8]` | **semantic** |
| `test_bc_comp_array.py` (8) | ST-FOREACH over component array (index form + iterator form), in component function, ST-BRK break/continue, EX-IDX run-time index dispatch; refused: assigning foreach index, component as value | **semantic** + **error** |
| `test_bc_attribute_paths.py` (3) | DT-CONST `static const` (incl. bool) folds; qualified ref is a constant | **semantic** |
| `test_bc_constraint_casts.py`, `test_bc_conditional_value.py`, `test_bc_enum_domain.py`, `test_bc_struct_compare.py` | DT-CAST, EX-COND, DT-ENUM, struct `==` — **in constraints**, not procedural code | **semantic** (200 seeds) |
| `test_type_body_registry.py` (5), `test_type_body_semantics.py` (6), `test_exec_file.py` (1) | Every type-body element translated or refused with a location; EXEC-KIND merge of same-kind blocks (LRM 22.1), clash refused; `exec file` refused | **shape** + **error** |

Out of scope but relevant: `tests/progseq/test_op_model_construct_matrix.py`
(and `test_op_model_py*`) carry most of the *semantic* coverage of match,
foreach, repeat, part/bit select, `~` width rules, arrays and struct defaults —
but through op-model emission, not at the IR. `tests/compliance` runs the
pss-corpus procedural tier (110 tests) on bc.

---

## 2. Coverage table

Columns: **IR-tested?** = a test parses PSS and inspects ast2ir's output or runs
it; **Tests**; **Depth**; **Neg** = a negative / error test exists at the
parse/IR layer.

### Data types

| ID / sub-construct | IR-tested? | Tests | Depth | Neg |
|---|---|---|---|---|
| DT-INT `int`, `bit`, builtin widths | yes | test_ast_to_ir (builtins), test_scalar_types (typedef) | shape | – |
| DT-INT `bit[N]`/`int[N]` field width & sign | partly | typedef tests; bc_literal_types (`int[8]`) | shape / semantic | – |
| DT-INT domain `int in [..]` on a field | **no** (IR) | only rt constraint tests | – | – |
| DT-FP `float32`/`float64` | **no** | none (no `float` in any unit test) | – | – |
| DT-BOOL | yes | builtins; bc tests use `bool` | shape | – |
| DT-ENUM decl, values, extend, fold | yes | test_scalar_types, test_component_arrays (extend enum) | shape | yes (non-folding value) |
| DT-ENUM with domain `e_t in [A..B]` | **no** | – | – | – |
| DT-ENUM ↔ int casts in procedural code | **no** (IR) | bc_constraint_casts (constraints only) | – | – |
| DT-STR decl, `==`/`!=` | yes | test_string_and_builtins, bc_strings | shape + semantic | yes (bc) |
| DT-STR methods `size/find/lower/upper` | yes | test_string_and_builtins | shape | – |
| DT-STR `find_last/find_all/split/chars` | **no** | hand-IR only (`split`) | – | – |
| DT-CHND | yes | test_collections (field), test_addr_handle_chandle | shape | – |
| DT-STRUCT fields, inheritance | yes | test_ast_to_ir, test_inheritance_and_exec | shape | – |
| DT-STRUCT values in procedural code | yes | bc_struct_values | semantic | yes |
| DT-STRUCT nested field write `o.i.a = 3` | bc only | bc_struct_values | semantic | – |
| DT-ARR field, size, `int a[N]`, const size | yes | test_collections, test_array_size_const | shape | yes (-1 reported) |
| DT-ARR multi-dim, array of struct | **no** (IR) | – (progseq matrix only) | – | – |
| DT-ARR methods `sum/to_list/to_set/size` | **no** | – | – | – |
| DT-LIST field + `push_back/size/clear/==/[i]` | yes | test_collections | shape | – |
| DT-LIST `insert/delete/pop_*`, list of list | **no** | – | – | – |
| DT-MAP field + `delete` | yes | test_collections | shape | – |
| DT-MAP `insert/keys/values/size` | **no** | – | – | – |
| DT-SET field | yes | test_collections | shape (type only) | – |
| DT-SET methods, `in set` | **no** | – | – | – |
| DT-REF `ref T` | **no** | – | – | – |
| DT-TYPEDEF at package/global scope | yes | test_scalar_types, test_addr_handle_chandle | shape | – |
| DT-TYPEDEF in a component body | **no** (refused, untested) | – | – | – |
| DT-CAST | hand-IR only | test_expr_phase3_ir | hand-IR | – |
| DT-ADDR | yes | test_addr_handle_chandle, bc_memory | shape + semantic | yes (bc) |
| DT-REG | yes, heavily | test_register_*, test_reg_* (~105) | shape | yes (14) |
| DT-CONST `static const` | yes | test_array_size_const, bc_attribute_paths | shape + semantic | – |

### Expressions

| ID / sub-construct | IR-tested? | Tests | Depth | Neg |
|---|---|---|---|---|
| EX-LIT int | yes | test_stmt_expr | shape | – |
| EX-LIT based/sized typing | yes | bc_literal_types | semantic | – |
| EX-LIT bool, string | weak | used incidentally | – | – |
| EX-LIT float | **no** | – | – | – |
| EX-LIT aggregate (list/map/struct/empty) | yes | test_aggregate_literals | shape | – |
| EX-ARITH `+ - * /` | yes | test_stmt_expr (op-exact) | shape | – |
| EX-ARITH `%`, `**` | **no** (IR) | `**` hand-IR only; test_expr_translation's `**` test has no `**` in its PSS | – | – |
| EX-ARITH unary `-`/`+` | yes | test_unary_operators | shape (op-exact) | – |
| EX-REL / EX-EQ | yes | test_stmt_expr | shape (op-exact) | – |
| EX-LOG `&&` `||` | smoke | test_ast_to_ir (`value is not None`) | smoke | – |
| EX-LOG `!` | yes | test_unary_operators | shape | – |
| EX-BIT `& | ^` | **no** | – | – | – |
| EX-BIT `~` | yes | test_unary_operators | shape | – |
| EX-RED reduction `& | ^` | **no** (refused, untested) | – | – | – |
| EX-SHIFT `<< >>` | **no** | – | – | – |
| EX-COND `?:` | smoke | test_ast_to_ir | smoke | – |
| EX-IN `in [..]` in procedural code | **no** (IR) | hand-IR only | – | – |
| EX-SEL bit / part select | **no** (IR) | hand-IR; progseq matrix | – | – |
| EX-IDX collection index | yes | test_collections (`items[0]`, `[2]`), bc_comp_array (run-time index) | shape + semantic | yes (bc) |
| EX-SUBSTR `s[a..b]` | yes | test_string_and_builtins | shape | – |
| EX-WIDTH | bc only | bc_literal_types | semantic | – |
| EX-ASSIGN `=` | yes | test_stmt_expr | shape | – |
| EX-ASSIGN `+= -= <<= >>= |= &=` | **no** (IR) | `+=` used incidentally in bc tests | – | – |
| EX-REF field / `self` | yes | many | shape | – |
| EX-REF `super.x` root | **no** (asserts attr only) | test_component_arrays (stale) | weak | – |
| EX-REF param shadows field | **no** in tests/unit | (progseq `test_param_shadows_field.py`) | – | – |
| EX-REF `pkg::f` / static | yes | test_package_function_calls_ir, bc_attribute_paths | shape | – |
| `compile has` | hand-IR only (and **dropped** by ast2ir) | – | – | – |
| `null` | hand-IR only | – | – | – |

### Functions

| ID / sub-construct | IR-tested? | Tests | Depth | Neg |
|---|---|---|---|---|
| FN-DECL component, return type, void | yes | test_ast_to_ir, test_component_arrays, test_extend_functions | shape | – |
| FN-DECL package scope | yes | test_package_function_calls_ir | shape | – |
| FN-PARAM typed, defaults, varargs | yes | test_component_arrays | shape | – |
| FN-PARAM `const` | **no** (IR; metadata `const_params` unasserted) | – | – | – |
| FN-PARAM struct/array params | bc only | bc_struct_values, bc_recursion | semantic | yes |
| FN-PARAM `type T` generic, `ref` | **no** (silently dropped, §4) | – | – | – |
| FN-PURE `is_invariant` | **no** | stale "skipped" note | – | – |
| FN-CALL in expr / as stmt / qualified | yes | test_package_function_calls_ir, test_string_and_builtins | shape | – |
| FN-CALL `(void)f()` | **no** | – | – | – |
| FN-CALL recursion | yes | bc_recursion | semantic | yes |
| FN-IMPORT | yes | test_import_capture, test_register_ir | shape | parser error (instance import) untested in pssc |
| FN-EXPORT | partly | test_parse_diagnostics (syntax), type_body_registry | error | yes |
| FN-SOLVE | yes | test_ctor_name, test_extend_functions | shape | – |
| FN-VIRT override / `super.f()` | no (IR) | progseq only | – | – |
| FN-TT target-template function | **no** (refused, untested at unit level) | – | – | – |

### Procedural statements

| ID / sub-construct | IR-tested? | Tests | Depth | Neg |
|---|---|---|---|---|
| ST-BLOCK nested `{}` + shadow rename (`x__s1`) | **no** | – | – | – |
| ST-VAR with/without init | yes | test_stmt_expr | shape | – |
| ST-VAR multiple in one decl | **no** | – | – | – |
| ST-ASSIGN | yes | test_stmt_expr | shape | – |
| ST-VCALL | yes | test_string_and_builtins (`print`), test_collections (`push_back`) | shape | – |
| ST-RET value / void | yes | test_stmt_expr | shape | – |
| ST-REPEAT count | yes | test_ast_to_ir (`StmtFor` present) | weak shape | – |
| ST-REPEAT with index `repeat (i : n)` | **no** (unit; bc activity-level only) | test_stmt_translation claims it but doesn't contain it | – | – |
| ST-RWHILE `repeat {..} while` | **no** | hollow test | – | – |
| ST-WHILE | yes | test_ast_to_ir, bc_yield | shape / semantic | – |
| ST-FOREACH array/list/map/set, `[i]` index | **no** (IR) | only component-array foreach on bc | semantic (comp arrays only) | yes (bc) |
| ST-IF / else | yes | test_ast_to_ir, test_stmt_expr | shape | – |
| ST-IF else-if chain, unbraced arms | **no** | – | – | – |
| ST-MATCH values / ranges / default | **no** (IR); bc on strings only | bc_strings | semantic (string only) | yes (bc, string range) |
| ST-BRK break / continue | bc only | bc_comp_array | semantic | – |
| ST-RAND `randomize` | yes (refusal) | test_ast2ir_super | error | yes |
| ST-CIF `compile if` in procedural code | **no** | – | – | – |
| ST-YIELD | yes | test_package_and_yield, bc_yield | shape + semantic | – |
| ST-SUPER | yes | test_ast2ir_super | shape | – |

### Exec blocks and library

| ID | IR-tested? | Tests | Depth | Neg |
|---|---|---|---|---|
| EXEC-KIND body/pre/post_solve/init_*/run_* | yes | test_inheritance_and_exec, type_body_semantics | shape | yes (merge clash) |
| EXEC-KIND header/declaration/pre_body/file | refusal tested for `file` only | test_exec_file | error | partial |
| EXEC-TT | refusal (`exec file`) | test_exec_file | error | yes |
| LIB-MSG `print` | yes | test_string_and_builtins | shape | – |
| LIB-MSG `message`/`format` | `message` used by every bc test; `format` test is empty | bc_* | semantic (message) | – |
| LIB-ERR `error`/`fatal` | **no** (empty tests) | – | – | – |
| LIB-RAND `urandom*` | **no** (IR) | – | – | – |
| LIB-FP | **no** | – | – | – |
| LIB-FILE | **no** | – | – | – |
| LIB-MEM | yes | bc_memory | semantic | yes |
| LIB-REG | yes | test_reg_*, errors/test_reg_field_errors | shape | yes |
| LIB-CHAN | yes | bc_channels | semantic | yes |

---

## 3. What ast2ir does NOT handle

From `_translate_statement_kind`, `_translate_expression`, `_translate_data_type`,
`_map_binop/_map_unaryop`, `_BODY_ELEMENTS`/`_EXEC_FUNCS`, and confirmed by the probe.

### 3.1 Refused with an error (good: never a silent miscompile)

| Construct | Message |
|---|---|
| `randomize x [with {..}]` (ST-RAND, 20.7.12) | "a 'ProceduralStmtRandomize' statement could not be translated" |
| Reduction `&x`, `|x`, `^x` (EX-RED) | "the bit-reduction operator '&' is not supported by this compiler" |
| Any statement whose call expression does not translate | same generic refusal |
| `target C function ... = """..."""` (FN-TT) | "a target-template function in component ... is not supported yet" |
| `exec header/declaration/file = """..."""` (EXEC-TT) | "a target-template exec block ... is not supported yet" |
| `exec pre_body` | "'exec pre_body' in action ... is not supported yet" |
| `typedef` inside a component body | "a typedef in component ... is not supported yet" (+ unresolved-type cascade) |
| Field of `float32`/`float64` type | "Failed to translate field type for f" (no location) |
| Field of `ref T` type | "Failed to translate field type for r" |
| Field of enum type with domain (`e_t in [A..B] x`) | "Failed to translate field type for x" |
| Two same-kind exec blocks declaring the same local | merge clash error |
| `override`, `monitor`, cover statements, covergroup types/instances, `import class`, scheduling constraints, a 2nd activity | located "... is not supported yet" via `_BODY_ELEMENTS` |

### 3.2 Silently dropped or degraded (no error — the dangerous class)

`_translate_expression` and `_translate_data_type` return `None` for anything
not in their dispatch, and only a bare expression *statement* turns `None` into
a refusal. Everywhere else the `None` is embedded in the IR:

| Construct | What ast2ir produces |
|---|---|
| `float32/float64` local | `StmtAnnAssign(annotation=None)` |
| Float literal `1.5` (`ExprFloatLiteral`) | `None` — initializer lost; `d * 2.0` becomes `ExprBin(rhs=None)` |
| Function returning `float64` | `returns=None`, i.e. looks **void** |
| Function parameter of `float32` type | **parameter removed** from `args` (arity changes) |
| Function parameter `ref T` | **parameter removed** |
| Generic parameter `type T` | **parameter removed** |
| `compile has(x)` (`ExprCompileHas`) | `None` (`return compile has(a);` → `StmtReturn(value=None)`) |
| `ExprTemplateString`, `ExprMemberCall`, `ExprHierarchicalId` (not in the dispatch) | `None` if ever reached |
| Function parameter direction (`input/output/inout`) | not recorded (pssparser already refuses output/inout on a defined function) |
| `x[7:4]` vs `s[1..2]` | both `ExprSubscript(slice=ExprSlice)` — bit-part select and string sub-string share one IR form; disambiguated downstream by type (`targets/bit_select.py`) |

Recommendation for the audit: a catch-all in `_translate_expression` /
`_translate_data_type` (error on `None`, as `_translate_statement` already
does) would turn every row of 3.2 into a row of 3.1.

### 3.3 Not reachable / parser-side limits seen while probing

* Compound assignment is LRM-limited to `= += -= <<= >>= |= &=`
  (`assign_op`, LRM grammar); `*=`, `/=`, `%=`, `^=` are syntax errors in
  pssparser, correctly. The taxonomy's EX-ASSIGN list over-states the LRM.
* `s.find_all("x").size()` fails with "'size' was left unbound by pssparser"
  (call-result member) — a pssparser resolution gap, not ast2ir.
* `print`/`format`/`error`/`fatal`/`message`/`urandom` need `import std_pkg::*;`;
  with it they translate (calls on `self`). The three empty tests in
  `test_string_and_builtins.py` are based on a false premise.

---

## 4. Probe summary (2026-10-16)

Constructs that translate cleanly with the expected IR shape (no test pins
most of them): else-if chains and unbraced arms; `repeat (i : n)` (`StmtFor`
with target); `repeat {} while` (`StmtRepeatWhile`); `while` with
`break`/`continue`; `foreach` over array/list/map/set, element and index forms
(`index_var`); `match` with ranges (`ExprRange`), multi-value arms
(`PatternOr`), enums and `default`; nested block with shadowing (renamed
`x__s1`); `compile if` (resolved by the parser, the taken branch inlined);
`<<= >>= |= &= -=` (`StmtAugAssign`); `%`, `**`, shifts, `& | ^ ~`; casts
`(bit[8])`, `(bool)`, enum↔int; `in [..]` with ranges and enums, `in` array;
bit select, part select, substring; aggregate literals in initializers;
`null`; sized literals carry width/sign; `static const` folds with its `ref`;
list/map/set/array/string methods (as `ExprCall(ExprAttribute)`); `(void)f()`
(the cast disappears — `StmtExpr(call)`); default params; varargs (`int ... b`);
`pure` → `is_invariant`; recursion; `super;`; `super.x` → `TypeExprRefSuper`.

---

## 5. PARSE/IR HOLES (ordered by risk)

1. **Floating point (DT-FP, EX-LIT float, LIB-FP) — no test, and ast2ir drops it silently** in locals, literals, returns and parameters (§3.2). pssparser has 23 float tests; pssc has 0.
2. **Hollow tests**: `test_stmt_translation.py` (repeat, repeat-with-index, repeat-while, foreach, yield, match, nested loops) and `test_expr_translation.py` (range/in, list init, ternary, `**`) test none of the constructs they name. They read as coverage in any listing. They should be rewritten or deleted.
3. **`match` (ST-MATCH)** — no IR test of ranges→`ExprRange`, multi-value→`PatternOr`, `default`, enum arms. The code comment records a past silent miscompile (`[0..3]` matched only 0); nothing pins the fix at the IR.
4. **`foreach` (ST-FOREACH)** over data collections (array/list/map/set, element+index form, map key) — no IR test; only component-array foreach on bc.
5. **`repeat (i : n)`, `repeat-while`, else-if chains, unbraced arms, nested `{}` scope + shadow rename** — no IR test. (Scope renaming is a correctness mechanism, LRM 20.7.1.)
6. **Silent parameter removal** (`ref T`, `type T`, unsupported type) — no test, and the arity of the IR function changes.
7. **`compile has`** — tested only as a hand-built IR class while ast2ir drops it to `None`; `compile if` in procedural code has no pssc test.
8. **Operators without an IR assertion**: `%`, `**`, `<< >>`, `& | ^`, `&&`/`||` (smoke only), `?:` (smoke only), compound assignments, `in` in procedural code, bit/part select, casts. All translate today; none is pinned (contrast `test_unary_operators.py`, which exists because exactly this kind of loss happened).
9. **Collection method coverage**: list `insert/delete/pop`, map `insert/keys/values/size`, set methods, array `sum/to_list/to_set/size`, string `find_last/find_all/split/chars` — no parse/IR test; pssparser-side gap on call-result members.
10. **Refusals without tests**: reduction operators, enum-with-domain field, `ref` field, `typedef` in component, `exec header/declaration/pre_body`, target-template functions. Each refusal is correct, but nothing prevents it reverting to a silent drop.
11. **Stale/weak assertions**: `super.x` tests (attr only, wrong docstring); `pure` note; `test_ast_to_ir` expression tests (`is not None`); `format/error/fatal` empty tests; FN-PARAM `const` metadata unasserted.
12. **LIB-ERR / LIB-RAND / LIB-FILE / LIB-FP** — no parse/IR test (message is exercised only incidentally on bc).
13. **No negative test of a procedural expression or statement at the parse/IR layer** apart from `randomize`; all procedural negatives live in bc lowering (`LoweringError`/`PssSemanticError`).

---

## 6. pssparser's own parser tests (coarse)

Checkout: `packages/pssparser` (327 Python test files, 117 `.pss` fixtures;
`error-suite/` with 366 accept/semantic/syntax cases). Parsing tests are in
`tests/python/parsing/`, linking in `tests/python/linking/`, error behaviour in
`tests/python/errors/`. `test_unrepresented_constructs.py` is a ledger of
constructs the grammar accepts but the AST drops (PSSP101); it is currently
**empty** — pssparser claims every parsed construct is represented, which
places the §3.2 losses squarely in pssc's ast2ir.

| Construct area | pssparser tests (file: count) |
|---|---|
| Data types (int/bit/bool/string/chandle, widths) | `test_data_types.py`: 30 |
| Floating point | `test_float_types.py`: 23 |
| Enums (incl. base type, extension) | `test_enums.py`: 19, `test_enum_base_type*.py`, `test_enum_extensions.py` |
| Structs | `test_structs.py`: 16 |
| Collections + methods | `test_collections.py`: 9, `test_collection_methods.py`: 15, `test_multidim_arrays_31.py`: 16 |
| Strings | `test_string_methods.py`: 11, `test_string_method_signatures.py`: 13, `test_template_strings_31.py`: 84 |
| `ref` types | `test_ref_types.py`: 5 |
| typedef / static const | `test_typedef.py`: 11, `test_static_const.py`: 5 |
| Casts | `test_type_casting.py`: 4, `test_cast_precedence.py`: 2 |
| Literals | `test_numeric_literals_31.py`: 18 |
| Expressions (all operators, precedence, `in`, ranges, slices, aggregates, null) | `test_expressions.py`: 37, `test_slices_31.py`: 14 |
| Procedural statements (yield, return, break/continue, compound assign, if/else-if, while, foreach, match, nesting) | `test_procedural_statements.py`: 37; linking `test_procedural_scopes.py`, `test_procedural_var_resolution.py`, `test_foreach_iterator_type.py`, `test_randomize_with.py` |
| Functions (decl, qualifiers, pure, calls, arity, arg types, const params) | `test_functions.py`: 34, `test_pure_functions.py`: 27, `test_function_qualifiers_31.py`: 19, `test_call_arity.py`: 26, `test_call_arg_types.py`: 24, errors `test_function_*`, `test_const_params.py` |
| Import/export/target-template functions | `test_export_function_31.py`: 10, `test_target_template_function_31.py`: 12, linking `test_import_*` |
| Exec blocks | `test_exec_variants.py`: 5, `test_exec_scopes_31.py`: 4, `test_target_exec.py`: 6 |
| compile if / has / assert | `test_compile_time*.py` (34 + multifile + semantics), linking `test_compile_if_regions.py` |
| Executors / stdlib | `test_executors.py`: 20, `test_stdlib_31.py`: 35 |
| Registers | linking `test_reg_model_3_1.py`, `test_reg_model_semantics.py`, errors `test_reg_field_resolution.py` |

Contrast: the constructs pssparser tests most densely (float, ref, template
strings, collection methods, compile-time) are the ones pssc has no
parse/IR test for — the parser side is covered, the translation side is not.
