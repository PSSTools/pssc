# Enum handling across the op-model targets — review

2026-10-14. **Decisions (user, 2026-10-14):** the prefix rule is (c) with (a)
as the default plus an always-on collision check (§4.2); C++ uses `enum class`;
the `%n` fix goes first. Status is in §5.

Scope: op-model-c, -cpp, -py, -sv (the testbench targets are noted
at the end). Findings come from reading the backends and from two probe models
run through `tests/progseq/trace_harness.py` (built and run on all four
targets). The probes are reproduced in §6.

## 1. The problem in one paragraph

A PSS enumerator is scoped by its type: `dir_e::RD` and `op_e::RD` are
different items, and a bare `RD` is resolved from the type the context expects
(the other side of `==`, the assignment target, the parameter). C,
SystemVerilog, and C++'s unscoped `enum`s put every enumerator into the
enclosing namespace. Python has no enum declarations at module scope, so the
module-level constants we emit share one namespace as well. Today **every
target emits enumerators verbatim**. A model with two enums sharing an item
name is legal PSS and fails to compile on C, C++ and SV. On Python it compiles
but gives a wrong value without any error (§3, D1).

## 2. What each target does today

| | declaration | spelling of an item in a body | how it knows the name |
|---|---|---|---|
| **C** | `typedef enum { RD = 1, … } dir_e;` (explicit values) | **the number** almost everywhere (`d = 2`, `return 4`, `case 9:`, `_self->fd = 2`); the item name only for a *default* (first item) | `enum_first_item` only |
| **C++** | `enum dir_e : int { RD = 1, … };` unscoped | name, in typed contexts (assign, return, arg, one side of `==`); **number** in `case` labels and `?:` arms (`static_cast<op_e>((d == WR) ? 9 : 3)`) | **reverse lookup by value** in the expected enum (`_coerce`) |
| **Python** | module constants `RD = 1` under a `# dir_e` comment | **the number** everywhere (`if d == 1: return 4`) — the constants are emitted and then never used | — |
| **SV** | `typedef enum {RD = 1, …} dir_e;` (int base) | name almost everywhere, including `case` labels and `?:` | `ExprConstant.ref` first, value lookup as fallback (`value_of`, `SvConstants.name_of`) |

The front end already does the hard part. ast2ir folds an item to
`ExprConstant(value=…, ref="p_pkg::mode_e::FAST")` with the item the **linker**
resolved (`_linked_enum_value`). Only SV reads `ref`. C++ re-derives the name
from the value. That gives the wrong item when two items share a value, and it
cannot see an item outside an enum-typed context. C and Python ignore `ref`
entirely.

The root cause is that `ExprTypes._ExprConstant` (`targets/expr_types.py:208`)
types a folded item as a plain `int`, because it never looks at `ref`. As a
result, no shared code can say "this operand is `op_e::RD`". Each backend that
wanted the name built its own partial recovery: C++ has `_expr_type`/`_coerce`/`_binop`,
and SV has `value_of` plus the case-label special case.

## 3. Defects, worst first

**D1. Python: a clashing item gets a wrong value, with no error.** `dir_e { RD = 1 }` and
`op_e { RD = 4 }` emit `RD = 1` and then `RD = 4` at module scope. A user of
the generated API who writes `mod.RD` gets 4 for every `RD`. The generated
bodies are correct only because they never use the constants.

**D2. C/C++: `%n` in `message` is passed to a printf-style function.**
`message(LOW, "x=%n", x)` becomes `pssc_message("x=%n", x)` /
`pssc::message("x=%n", x)`. In printf, `%n` *stores the character count
through an `int *` argument*, so with an enum value as the argument it writes
to address `x`. PSS's `%n` ("a bool as true/false, an enum as its item name")
has to be lowered before the call, as Python (`pss_format`) and SV already do.
This is a memory-safety bug in generated code, not only a spelling problem.

**D3. C, C++, SV: items with the same name in two enums do not compile.** C
fails with "redeclaration of enumerator", C++ with "'RD' conflicts with a
previous declaration", Verilator with "Duplicate declaration of enum value". Nothing
in pssc catches it first: `c_names` gathers enum *type* names but not
enumerators, and SV has no equivalent check. Because C++ recovers names by
value, the C++ errors also land on the wrong item (`return RD` meant `op_e::RD`
but binds to `dir_e::RD`).

**D4. C and Python spell items as numbers.** This is the cleanup you mentioned.
In C, every assignment, return, argument, comparison, `case` label and field
initializer is a number. Python is the same. The generated drivers read as
magic numbers, and the enum declarations they emit are unused.

**D5. C++ recovers names by value.** Two items with one value (aliases) print
as the first one. Values in `case` labels and `?:` arms stay numbers. A redundant
`static_cast<op_e>(this->arr[0])` appears because `_expr_type` does not see
through a subscript. All of this goes away once the type of a constant comes
from `ref`.

**D6. Enum width and signedness are fixed at `int`.** `ExprTypes` types every
enum as `PssType("enum", 32, True)` and ignores `DataTypeEnum.width`
(`enum st_e : bit[2]`). The C/C++/SV declarations have no base type either
(SV `typedef enum {…}` is `int`). This is harmless for small values. It is
wrong for a `bit[64]` (or `bit[32]` with bit 31 set) base: the C `enum` cannot
hold the value before C23, and SV truncates. It belongs with the
integer-semantics work, but the declaration change below should state the base
type.

**D7. The manifest has no enumerators.** `--emit-manifest` names an enum type
but not its items or their emitted spellings. Once spellings stop being
verbatim, a consumer (the corpus SV adapter writing a testbench, for example)
has to parse generated code to find them, which is exactly what the manifest
exists to prevent.

**D9. A negative enumerator value is lost in the front end.** (Fixed; see §5.)
`enum mode_e { OFF, SLOW = 5, NEG = -3, FAST = 6 }` gives `NEG` the value 6.
`-3` is an `ExprUnary`, which has no `getValue()`, so both ast2ir walks (the
`DataTypeEnum` build and `_linked_enum_value`) skip it and the auto-increment
from `SLOW` wins. `NEG` and `FAST` then share a value, and `%n` of `FAST`
prints `NEG`. The value must come from evaluating the constant expression.
Found by the `%n` probe.

**D8 (not enum-specific, found on the way). `x in [A, B]` is unsupported on
every op-model target.** `ExprIn` has no hook in any body emitter.

**SV smaller items.** An item of an enum the package does not declare "stays a
number" (`SvConstants`). A `static const lvl_e DEF = L1` renders as `L1`, not
`DEF`. Both are acceptable.

## 4. Proposed design

### 4.1 One fact, read by every backend

1. **Type a folded item as its enum.** `ExprTypes._ExprConstant`: when `ref`
   names an enum item, return the enum's `PssType`, resolved through the
   linker-qualified type name (never by item name alone). `IntSemantics`
   already treats `kind == "enum"` as "render as it stands", so integer
   lowering does not change.
2. **Spell an item from `ref`, not from its value.** Add a shared
   `targets/enum_names.py` that, given the enum `DataTypeEnum` and an item,
   returns the target spelling under the target's rule (§4.2). Every
   `expr_constant` hook asks it first, as SV's `consts.name_of` does today.
   The value lookup stays only as the fallback for a constant without a `ref`
   (a cast `(op_e)9` keeps the cast).
3. **Contextual conversion stays where it is.** In C++ and SV, an integer
   in an enum context still needs `static_cast<>`/`op_e'()`. With (1) in place
   that rule needs only "is the source enum-typed", which `ExprTypes` now
   answers. C++'s `_expr_type` and the value-lookup half of `_coerce` and
   `value_of` can be deleted.

### 4.2 The spelling, per language

| target | recommendation | why |
|---|---|---|
| C | **prefix**: `typedef enum { OP_RD = 4 } op_e;` | one ordinary namespace; nothing else is safe |
| SV | **prefix**, same rule as C | enum literals are package-scope names |
| C++ | **`enum class op_e : <base>`** → `op_e::RD`, no prefix | exact PSS scoping; the backend already emits a `static_cast` at every int↔enum boundary, and with §4.1 it knows where all of them are. The existing docstring argues for unscoped enums so that `status != 2` compiles; that argument was about missing names, which §4.1 removes. Alternative: same prefix as C (smaller change, uglier API). |
| Python | **a namespace class**: `class op_e: RD = 4` → `op_e.RD` | still imports nothing, gives exact scoping, and fixes D1 |

The prefix should be stable: it depends only on the enum, never on which other
enums happen to be in the model. A "prefix only on clash" rule would rename an
API symbol when someone adds an unrelated enum elsewhere.

**Default prefix rule** (your example): the type name with a trailing `_e`
removed, upper-cased, then `_`. `my_e { A }` → `MY_A`, `op_e { RD }` → `OP_RD`,
`mode { X }` → `MODE_X`. A package qualifier is dropped, as it is for the type
name (`Prefixes.type_name`). In C, a `--prefix-map` entry for the enum type
renames its items too.

**Open question — items that are already prefixed.** The WB DMA model writes
`wb_dma_status_e { WB_DMA_DONE, WB_DMA_ERROR, WB_DMA_PENDING }`. Under the rule
above these become `WB_DMA_STATUS_WB_DMA_DONE`. Options:

* (a) Always prefix. Uniform and predictable, but ugly for models written C-style.
* (b) Do not prefix an item that already starts with the prefix. Under this
  option `WB_DMA_DONE` would still get prefixed (its stem is `WB_DMA_STATUS_`),
  so it does not help here.
* (c) Make it a style decision. `CStylePolicy.enum_item_name(enum, item)`
  defaults to (a), and a style can choose "verbatim" for models whose items
  are globally unique by convention. SV takes the same choice through a target
  option.
* (d) Verbatim by default, and a hard pre-emission error naming both enums
  when items collide (the `c_names` check, extended to enumerators and to SV),
  with prefixing available as an option.

My recommendation is **(c) with (a) as the default, plus the collision check
from (d) always on**, so a verbatim style that collides fails with a clear
message instead of a C compiler error. Spelling is what `CStylePolicy` exists
for, and a policy cannot change an address or a value, only a name.

### 4.3 Checks that keep it this way

* `c_names` gathers enumerator spellings (C, and C++ if prefixed). SV gets the
  same pre-write check. The existing guard
  (`test_c_names.py` fails if the check gathers a name the backend does not
  emit) then covers enumerators as well.
* The manifest gains `enums: [{name, base, items: [{name, value, spelling}]}]`.
  This adds a field, so it is not a `VERSION` bump.
* `%n` (D2): C and C++ lower a `%n` argument to its item name. The enum's
  name table is a `static const char *const` per enum (C), with `%s` in the
  format, and a bool maps to `"true"/"false"`. Any other printf conversion
  PSS does not define is refused at the gate rather than passed through.

## 5. Plan

Each step is independently testable. Golden snapshots will change in steps 3–5
(spelling), which is a legitimate regeneration alongside an intentional
output change, reviewed in the same commit.

1. **D2 first** (`%n` in C/C++). **DONE.** C and C++ now format every
   integer, bool and enum field themselves (`targets/c/message_format.py`;
   `pssc_fmt_int`/`pssc_enum_name` in `pssc_env.h`, `pssc::fmt_int`/
   `pssc::enum_name` in `pssc_env.hpp`, using no libc), and pass the platform
   a format holding only `%s` and `%%`. This also fixes `%d` of a 64-bit
   value (no `ll`) and `%b` (not printf's). The argument check (LRM 21.1.1
   a-c) is shared with Python (`pss_format.check_args`). Tests:
   `test_c_message_format.py` holds the helpers to `_pss_fmt` over 1590
   cases. A construct-matrix case covers `%n`/`%d` on all four targets.
   Still open here: SV refuses flags, widths and `%X` ("no SystemVerilog
   rendering yet").
2. **§4.1 (1)**: enum-typed constants in `ExprTypes`. **DONE.**
   `ExprTypes.enum_of_item` types an `ExprConstant` whose `ref` names an item
   of a `DataTypeEnum` (with that value) as the enum. Tests:
   `test_enum_item_types.py`. No output change was expected, and the golden
   snapshots are byte-identical.
3. **Shared spelling module + Python namespace classes** (D1, D4-py).
   **DONE.** Ruled: namespace class only, no module-level aliases (a
   breaking change to the generated API, accepted). `targets/enum_names.py`
   (`item_of`) reads an item from `ref`, and `ExprTypes` uses it. Python
   emits `class wb_dma_status_e(object): WB_DMA_DONE = 0` and spells every
   item, including an enum local's default (its first item), as
   `wb_dma_status_e.WB_DMA_DONE`. An enum whose item a body or initializer
   writes is emitted even when no declaration names its type (so
   `message_verbosity_e` now appears). An enum and a struct with one class
   name are refused. The goldens `py-default` and `py-async` were
   regenerated: items replace numbers (`status != 2`, `match` arms, returns),
   and `wb_dma_ch_cfg_s` gains `_pss_defaults` for its enum fields. A
   construct-matrix case, "two enums with an item of one name", passes on
   py and is a strict xfail on c, cpp and sv (D3).
4. **C: prefix by policy, names from `ref`** (D3-C, D4-C), plus the enumerator
   check in `c_names`. **DONE.** `CStylePolicy.enum_item(enum_name, item)`
   defaults to the enum's name without `_e`, upper-cased, then `_` and the
   item (`WB_DMA_STATUS_WB_DMA_DONE`). `CEnums` (`c/lower_progseq.py`) spells
   items from `ref` in bodies, initializers and first-item defaults.
   `c_names` gathers every enumerator, so a verbatim style that collides is
   refused naming both enums (`test_c_names.py`). An enum whose item a body
   or initializer writes is declared even if no signature names its type
   (`collect_api_types(type_map=...)`, which uses the shared
   `enum_names.written_enums`). `message()`'s verbosity is skipped there, so
   `message_verbosity_e` is no longer declared in Python either. Goldens
   `c-*` were regenerated (enumerators, comparisons, returns, `case` labels).
   `tests/progseq/data/c/op_model_tb.c` uses the new names. The two-enum
   construct case now passes on C. `--prefix-map` does not rename enum
   items, since it does not rename enum types either.
5. **C++: `enum class`** (or prefix, per your choice), then delete the
   value-recovery code (D3-C++, D5). **DONE.** `enum class E : int`, and an
   item is `E::ITEM` (`cpp_enum_item`, from `ref`). The value lookups in
   `_coerce` and `_cpp_const` are gone. `_coerce` and `_binop` decide from
   `ExprTypes`, and an enum leaf of integer arithmetic is
   `static_cast<std::int32_t>` (`int_leaf_text`). An enum field of a
   register value struct is an unsigned bitfield in C++, so it is converted
   both ways (`_enum_type`/`_int_storage`). Golden `cpp-virtual` was
   regenerated, and `tests/progseq/data/cpp/op_model_tb.cpp` updated. The
   two-enum case passes on C++. A new construct case, "an enum field of a
   register value read back", passes on c, cpp and py. On sv it is a strict
   xfail: SV reads the packed `bit[1:0]` member into an enum without a cast.
   That is an existing SV gap, which step 6 takes up.
6. **SV: prefix**, plus the pre-write collision check (D3-SV). **DONE.**
   `SvConstants.item_name` spells an item with the prefix rule shared with
   C (`enum_names.prefixed_item`). `--sv-enum-items verbatim` keeps the PSS
   name. It is declared in `abi_settings`, because the names are what a
   testbench writes. Two enums' items with one name are then refused before
   the package is written (`item_clashes`). The SV value lookup in
   `value_of` and `emit_struct` is gone: a value the package does not hold
   as the enum gets `E'(x)`, which covers an enum field of a register
   value, held as bits. `%n` called `.name()`, which would print the
   prefixed enumerator. It now calls `pss_<enum>_name(v)`, a function the
   package declares only for enums whose `%n` is used. Goldens `sv-*` were
   regenerated. Both new construct cases pass on all four targets.
7. **Manifest enums** (D7). Update `docs/op-model-manifest.md`.
8. **Enum base type** in declarations and `ExprTypes` (D6). This can wait.
9. `ExprIn` (D8) is a separate item.
10. **D9** (negative enumerator values). **DONE**, before step 2. An item's
    value is folded as a constant expression (`_enum_decl_values`: `-3`,
    `K + 2`, `~0`). A value that does not fold is an error, not a guess. Two
    more defects turned up on the way and are fixed:
    - A reference to an item ADDED by `extend enum` became `self.B`, a field
      that does not exist. The linker resolves it into the enum's scope, but
      its value was looked up in the base `EnumDecl` only. Item values are now
      recorded by the item's AST node, and `extend enum` is applied in a pass
      of its own (`_Phase.ENUM`), before any body is translated.
    - `static const` folds were cached by `id()` of a pssparser wrapper.
      pssparser makes a new wrapper per access, so a collected wrapper's id
      could be reused by another field. They are now keyed by the node's own
      `id()` (`_node_key`).
    Tests: `test_scalar_types_ast_to_ir.py`.

Tests: the probe in §6 becomes a construct-matrix case ("two enums with an item
of one name") with the trace below, held on all four targets. Each backend also
gets a text test that items are spelled by name: no `== 4`, and `case`
labels by name.

## 6. Probes

Probe 1 (clashing items, every position). The expected trace was worked out by
hand: `write 0x1004 0x1`, `write 0x1004 0x5`, `write 0x1004 0x1`, `read 0x1000`,
`write 0x1000 0x2`, message `x=WR m=FAST`. Today only Python runs it, and its
module constants are wrong (D1). C, C++ and SV fail to compile (D3).

```pss
package p_pkg { enum mode_e { OFF, SLOW = 2, FAST = 5 } }
import p_pkg::*;
enum dir_e { RD = 1, WR = 2 }
enum op_e { NOP = 3, RD, WR = 9 }   // RD, WR clash with dir_e
enum st_e : bit[2] { IDLE, BUSY }
struct ctl_s : packed_s<> { bit[1] en; st_e st; bit[29] rsvd; }
struct cfg_s { dir_e d = WR; mode_e m; }
// ga_c: CTL (ctl_s) at 0x0, STS at 0x4
component pss_top {
  ga_c a;
  dir_e fd = WR;
  op_e  fo;
  array<op_e, 2> arr = {op_e::WR, NOP};
  cfg_s cfg;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
  function op_e pick(dir_e d) {
    if (d == dir_e::RD) { return op_e::RD; }
    return (d == WR) ? op_e::WR : NOP;
  }
  function int code(op_e o) {
    match (o) { [op_e::RD]: return 1; [NOP, op_e::WR]: return 2; default: return 3; }
  }
  target function void run() {
    op_e x = pick(RD);
    mode_e m = FAST;
    dir_e d;
    d = WR;
    a.STS.write_val(code(x));
    a.STS.write_val((bit[32])m);
    if (x != op_e::WR && fd == WR) { a.STS.write_val(1); }
    x = (op_e)9;
    ctl_s c = a.CTL.read();
    c.st = BUSY;
    a.CTL.write(c);
    if (c.st == IDLE) { a.STS.write_val(7); }
    message(LOW, "x=%n m=%n", x, m);
    cfg.d = RD;
    fo = arr[0];
  }
}
```

Probe 2 (no clash: an enum used only inside a body, an enum-typed
`static const`, two enum variables compared). It runs identically on all four
targets (`0x1, 0x2, 0x8, 0x3`), which isolates D4: C and Python output is all
numbers (`loc_e q = 8; if ((l == 2) || (l == 4))`), while C++ and SV use names.

## 7. Testbench targets (not op-model)

`sv-native`/`sv-pure` (`sv/lower_types.py: lower_enum`) also emit items
verbatim into a package, so they have the same clash (D3). They should share
the SV spelling rule from §4.2 once it exists. The `c-host`/`sw` targets were
not examined in depth.
