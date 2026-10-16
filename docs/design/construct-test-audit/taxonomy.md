# Construct taxonomy (shared IDs for the construct test audit)

Coarse IDs. The LRM inventory (`01-lrm-inventory.md`) refines each into
sub-constructs `<ID>.<n>`; the test catalogs (`02..05`) map tests to these IDs.

## Data types (LRM 7)
- DT-INT   integer types: `int`, `bit`, widths `bit[N]`, `int[N]`, ranges `int in [..]`, signedness
- DT-FP    floating point `float32`/`float64`
- DT-BOOL  `bool`
- DT-ENUM  enums: explicit values, implicit values, extension, enum-as-int, ranges
- DT-STR   `string`, sub-string, string methods (size, find, upper/lower, split, chars)
- DT-CHND  `chandle`
- DT-STRUCT struct: fields, nesting, inheritance, packed/register value structs, struct in struct
- DT-ARR   fixed arrays: multi-dim, of struct, operators (==, in, index), methods (size, sum, list, to_set), foreach
- DT-LIST  list<T>: methods (push/pop/insert/delete/clear/size), operators
- DT-MAP   map<K,V>
- DT-SET   set<T>
- DT-REF   reference types `ref T` (7.10)
- DT-TYPEDEF typedef / user-defined (7.11)
- DT-CAST  casts / type conversion (7.12)
- DT-ADDR  addr_handle_t, chandle-as-handle (21.13)
- DT-REG   reg_c<>, reg_group_c, register value structs (21.14)
- DT-CONST static const, template value params as constants

## Expressions (LRM 8)
- EX-LIT   literals (sized/based/unsized, string, bool, aggregate literals 8.4.2)
- EX-ARITH + - * / % ** unary -
- EX-REL   < <= > >=
- EX-EQ    == !=
- EX-LOG   && || ! (short-circuit 8.4.4)
- EX-BIT   & | ^ ~
- EX-RED   reduction & | ^
- EX-SHIFT << >>
- EX-COND  ?: 
- EX-IN    set membership `in`
- EX-SEL   bit select / part select `x[i]`, `x[h:l]`
- EX-IDX   collection indexing
- EX-SUBSTR string sub-string
- EX-WIDTH evaluation rules 8.7 (width/sign propagation, assignment-like contexts)
- EX-ASSIGN assignment operators `= += -= <<= >>= |= &=` (LRM 8.1 `assign_op`; `*= /= %= ^=` are NOT PSS)
- EX-REF   hierarchical refs: field, sub-component, `this`, `super`, parameter-shadows-field

## Functions (LRM 20.2-20.4, 20.6)
- FN-DECL  function decl: package scope, component scope, return types, void
- FN-PARAM params: by value, const, default values, generic/varargs, struct/array params
- FN-PURE  pure functions
- FN-CALL  calls: nested, in expression, as statement, void cast, qualified `pkg::f`, recursion
- FN-NATIVE native PSS function bodies (20.3), parameter passing semantics
- FN-IMPORT import functions (20.4.1), target/solve availability
- FN-EXPORT export functions (20.4.2)
- FN-SOLVE  `solve function` / constructor
- FN-VIRT  component function inheritance/override/super.f()
- FN-TT    target-template functions (20.6)

## Procedural statements (LRM 20.7)
- ST-BLOCK scoped block, shadowing
- ST-VAR   local variable decl (with/without init, multiple)
- ST-ASSIGN assignment statement
- ST-VCALL void function call statement
- ST-RET   return
- ST-REPEAT repeat (count), repeat with index
- ST-RWHILE repeat-while
- ST-WHILE while
- ST-FOREACH foreach (arrays, lists, maps, sets; index/key vars)
- ST-IF    if / else / else-if chains, unbraced
- ST-MATCH match (values, ranges, default)
- ST-BRK   break / continue (nested loops)
- ST-RAND  randomize statement (20.7.12)
- ST-CIF   compile-if inside procedural code (19)
- ST-YIELD yield
- ST-SUPER `super;` in exec

## Exec blocks / context
- EXEC-KIND exec kinds: init_down/init_up/pre_solve/post_solve/body/run_start/run_end/header/declaration
- EXEC-TT  target-template exec

## Built-in procedural library (LRM 21)
- LIB-MSG  print / message / format specifiers (21.1)
- LIB-FILE file operations (21.2)
- LIB-ERR  error/fatal (21.3)
- LIB-RAND urandom / urandom_range (21.4)
- LIB-FP   floating-point functions (21.5)
- LIB-MEM  read/write8..64, read/write_bytes, read/write_struct, executor delegation (21.13.9)
- LIB-REG  register read/write/read_val/write_val/write_masked/field access (21.14)
- LIB-CHAN channels put/get/try_get (21.9)
