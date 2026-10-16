# 01 — LRM construct inventory (data types, expressions, functions, procedural code)

Source: `PSS 3.1 Public Review Draft 2026.08.28 (2).md` (repo root). Clause
numbers are the LRM's; "Ex N" is an LRM example number. Coarse IDs come from
[`taxonomy.md`](taxonomy.md); this file refines each into sub-constructs
`<ID>.<n>`. IDs marked **NEW** are not in the taxonomy (they are proposed
additions; a catalog may map tests to them).

This is the reference for the test plan: each row states the rule a test must
exercise, the edge cases the LRM implies, and where the construct is legal.

## Conventions

**Ctx** (where the construct is legal):

| Code | Meaning |
|------|---------|
| `T`  | target procedural code: `exec body`, `run_start`, `run_end`, target functions (and unqualified functions called from them) |
| `S`  | solve-time procedural code: `exec init_down`, `init_up`, `pre_solve`, `post_solve`, `pre_body`, solve functions |
| `C`  | declarative solve context: constraints, activities, covergroups |
| `D`  | declaration-level only (a type/declaration property, not an executable construct) |

`T S` = all procedural code on both platforms. A construct "legal in `C`" may
be used there only in non-modifying form (7.9: methods/operators that modify a
collection are procedural-only).

**Neg** in a rule means a negative test: the LRM makes it an error/illegal,
and the test should expect a diagnostic.

---

## 1. Data types (LRM 7, plus 4.6-4.8, 9.1.6, 10.3, 21.13-21.14)

### DT-INT — integer types (7.2, 8.5.1.1, 4.6.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-INT.1 | `int` default | 7.2 T6 | `int a;` is 32-bit signed, default value 0 | INT_MAX+1 wraps to INT_MIN (2's complement, 8.5.1.1) | T S C |
| DT-INT.2 | `bit` default | 7.2 T6 | `bit b;` is 1-bit unsigned, domain 0..1 | `bit b = 2;` truncates to 0 (8.7.2); `b + 1` in a wider context does not wrap | T S C |
| DT-INT.3 | sized `bit[N]` / `int[N]` | 7.2.1 c | width is a single constant positive expression; `int[5]` range is -16..15 | width from a `static const` / template param; width 0 or negative is Neg | T S C |
| DT-INT.4 | wide integers | 7.2.1 | widths 33..64 and > 64 are legal PSS (no upper bound in 7.2) | > 64 bits: no import/export mapping (20.4.1 c, 20.4.2); pssc C/C++ refuse, Python/SV must compute exactly | T S C |
| DT-INT.5 | value domain | 7.2.1 d | `bit[5] in [1..31]`, `in [1,2,4]`, `in [..10]`, `in [10..]`, `int in [-10..5]` | domain bounds are self-determined; default is smallest domain value when 0 not in domain (`int in [-10..-5]` → -10; `bit[5] in [1,2,4]` → 1); domain ∩ width (`bit[5] in [10..]` = 10..31) | D (decl), C (implicit constraint) |
| DT-INT.6 | domain as implicit constraint | 7.2.1 d | randomizing a domain-typed field/local yields only domain values | procedural assignment of an out-of-domain value: LRM states no check (only randomization enforces) | C, S (`randomize`) |
| DT-INT.7 | signed/unsigned reinterpretation | 8.5.1.1 | conversion between signed and unsigned keeps the bit pattern | `int[8] x = -1; bit[8] y = x;` → 255 | T S C |
| DT-INT.8 | 4-state from foreign code | 7.2 | X/Z values passed in through FPI become 0 | only testable with an import function returning 4-state (SV) | T S |
| DT-INT.9 | rand integer | 7.1 | `rand bit[8] f` randomizable; locals cannot be `rand` (20.7.2) | `rand` local declaration is Neg | C |

### DT-FP — floating point (7.3, 8.7, 21.5)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-FP.1 | `float32`/`float64` declaration | 7.3.1 T7 | IEEE binary32/binary64 variables, assignment, printing | default value is not stated in 7.3 (presumably 0.0) | T S |
| DT-FP.2 | not randomizable | 7.1, 7.3.1 | `rand float64 f;` is Neg; `randomize f;` is Neg | a struct with a non-rand float field is still randomizable | D |
| DT-FP.3 | float literal type | 4.6.2, 8.7.1 T21 | `14.72`, `39e8`, `1e-9` are float64 | `20 .15` and `1.` are Neg (digit required each side, no whitespace) | T S C |
| DT-FP.4 | float32 narrowing | 7.3, 8.7.2 | `float32 f = 0.1;` rounds to binary32 | compare `f == 0.1` (float32 vs float64) is false | T S |
| DT-FP.5 | int ↔ float conversion | 8.7.1, 8.7.2 | int→float keeps the integer value; float→int treats value as signed and truncates the fraction | `int i = -2.7;` → -2; `bit[8] b = 300.9;` → 300 truncated to 44 | T S |
| DT-FP.6 | mixed int/float arithmetic | 8.7.1 T22, Ex 44 | integer operand is self-determined, then converted just before the operator | `bit[2] x=3,y=2; float64 f=10; x+y+f` = 11 (x+y wraps at 2 bits) | T S |
| DT-FP.7 | operators not allowed on float | 8.4 T10 | `%`, `& \| ^ ~`, shifts, reductions on float are Neg | `**` with a float operand gives float64 (8.5.1) | T S |
| DT-FP.8 | cross-platform tolerance | 7.3.2 | solve-time and target-time FP results may legitimately differ | tests must compare with tolerance, not bit-exact, across platforms | T S |

### DT-BOOL — Booleans (7.4)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-BOOL.1 | `bool` default | 7.4 | uninitialized `bool` is `false`; `true`=1, `false`=0 | | T S C |
| DT-BOOL.2 | Boolean-typed conditions | 20.7.7 a, 20.7.9, 8.5.4, 8.5.8 | `if`/`while`/repeat-while conditions, `?:` predicate, `&& \|\| !` operands are `bool` | `if (x)` with `int x` is Neg; `if (x != 0)` is legal | T S C |
| DT-BOOL.3 | casts | 7.12.1 b | `(bool)5` → true, `(bool)0` → false; `(int)true` → 1 | `(bool)` of a negative or wide value (only zero is false) | T S C |
| DT-BOOL.4 | relational on bool | 8.5.2 | `<` etc. are numeric-only → Neg on bool; `==`/`!=` legal | | T S C |
| DT-BOOL.5 | bool in packed layouts | 21.13.1.1 | a `bool` field is 1 bit in a packed struct | | D |
| DT-BOOL.6 | `%n` of bool | 21.1.1 | prints `true`/`false` | | T S |

### DT-ENUM — enumerations (7.5, 17.2.4, 8.4.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-ENUM.1 | implicit values | 7.5.1 b | first item 0, each next = previous+1 | after an explicit `B=10`, the next implicit item is 11 | D |
| DT-ENUM.2 | explicit values | 7.5.1 d | values need not be contiguous or ascending; may be negative unless base is unsigned | negative value with unsigned base is Neg | D |
| DT-ENUM.3 | base type | 7.5.1 | `enum e : bit[8] {...}` sets width and signedness of items | base type with a domain (`int in [1..10]`) is Neg; explicit value outside base range is Neg; implicit item after the base max (bit[2], previous = 3) is Neg | D |
| DT-ENUM.4 | distinct values | 7.5.1 e | two items with the same value is Neg | (tooling note: pssc code assumes values may collide; the LRM forbids it) | D |
| DT-ENUM.5 | default value | 7.5.1 k | uninitialized enum = FIRST declared item, not 0, not the minimum | `enum {A=5, B=0}` → default A (5) | T S C |
| DT-ENUM.6 | qualified item reference | 7.5.1 h | `E::ITEM` works in any context | item names may repeat across enum types in one namespace (7.5.1 g) | T S C |
| DT-ENUM.7 | unqualified item via expected type | 7.5.1 i, 8.4.3, Ex 37 | unqualified item resolves on RHS of assignment/initializer, as a call argument with enum formal, in `return`, on RHS of `==`/`!=`, in `?:` branches, on RHS of `in`, as cast operand | unqualified item where the expected type is `int` (`print_num(ORANGE)`, `(int)ORANGE`) is Neg; two enums with the same item name in scope | T S C |
| DT-ENUM.8 | extension | 7.5.1 f/j, 17.2.4 | `extend enum` adds items; an empty enum filled by extensions | variable of an enum with no items across def+extensions is Neg; item name reused across def and extension is Neg | D |
| DT-ENUM.9 | domain restriction | 7.5.1, 7.5.2 | `E in [A..C]`, `[A, C]`, `[..B]`, `[B..]` | default = first declared item inside the domain; whether `[A..C]` means value order or declaration order is not stated (LRM examples are ascending) | D, C |
| DT-ENUM.10 | casts | 7.5.1, 7.12.1 e | enum↔int, enum↔bool, enum↔enum, int→enum | casting a value that is no item (Ex 28: 30) yields a non-item value that falls to `default` in `match` | T S C |
| DT-ENUM.11 | permitted operators | 7.5.1 | assignment and `==`/`!=`; `in` and `match` with items and item ranges | `<`, `+`, `&` on an enum without cast is Neg (relational/arith are numeric-only) | T S C |
| DT-ENUM.12 | `%n` formatting | 21.1.1 d | prints the item name; escaped identifier printed without backslash | value that is no item | T S |
| DT-ENUM.13 | enum in packed / channel / sizeof | 21.9.1 a, 21.13.1 | only enums WITH a base type are allowed in packed structs, channels, `sizeof_s` | enum without base type in a packed struct is Neg | D |
| DT-ENUM.14 | items as constants | 7.5.1 a, 8.2 | items are static constant members, usable in constant expressions (array size, compile if) | | D |

### DT-STR — strings (7.6, 4.7, 8.6.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-STR.1 | default, assign, compare | 7.6, 8.5.3 | default `""`; `==`/`!=` compare size and content | relational (`<`) and `+` on strings are Neg (no string concatenation operator) | T S C |
| DT-STR.2 | string domain | 7.6.1 | `string in ["a","b"]` | string ranges (`"a".."b"`) are Neg | D, C |
| DT-STR.3 | `size()` | 7.6.3 | number of characters | escaped chars count as one (`"\n"` size 1) | T S C |
| DT-STR.4 | `find(sub, first_pos=0)` | 7.6.3 | first occurrence at/after first_pos, -1 if absent | empty `sub` → 0 regardless; `first_pos` = size legal; > size invalid | T S C |
| DT-STR.5 | `find_last(sub, first_pos=-1)` | 7.6.3 | last occurrence searching backwards | empty `sub` → size of string; first_pos range -1..size | T S C |
| DT-STR.6 | `find_all(sub)` | 7.6.3 | `list<int>` of all starts, increasing | absent → empty list; empty `sub` → {0..size}; overlapping matches ("aaa".find_all("aa")) not specified | T S C |
| DT-STR.7 | `lower()`/`upper()` | 7.6.3 | ASCII case conversion, returns new string | non-letters unchanged; receiver unchanged | T S C |
| DT-STR.8 | `split(sep)` | 7.6.3 | `"Hello, world, and good bye!".split(", ")` → 3 parts | leading/trailing sep → empty first/last element; adjacent seps → empty elements; empty string → `{""}`; empty `sep` is Neg | T S C |
| DT-STR.9 | `chars()` | 7.6.3 | `list<bit[8]>` of ASCII codes | empty string → empty list | T S C |
| DT-STR.10 | `join` / `str_from_chars` | 7.9.2.2, 7.9.3.2 | collection→string helpers (see DT-ARR.11/12, DT-LIST.9) | | T S C |
| DT-STR.11 | quoted literal escapes | 4.7 T4 | `\a \b \f \n \r \t \v \\ \" \' \? \ddd` | escape not in the table is Neg; `\'`/`\?` same as unescaped; quoted literal spanning lines is Neg; `\ddd` > `\377` may error | T S C |
| DT-STR.12 | triple-quoted literal | 4.7 | may contain newlines, quotes, any ASCII, no escapes | `"""` inside is impossible; special elements see EX-TQS | T S C |
| DT-STR.13 | string in `match`/`in` | 7.6.4 | string-literal lists as match choices and `in` RHS | | T S C |
| DT-STR.14 | target-platform limits | 7.6.1 | string ops in target execs may be limited when operands are not solve-time known | a test must not assume full string support on `T` for every backend | T |
| DT-STR.15 | rand string | 7.6.4 Ex 9 | `rand string s` constrained by equality | | C |
| DT-STR.16 | string in import/collections | 20.4.1 c | string params/returns ok for import; `list<string>` is NOT an allowed import type | | D |

### DT-CHND — chandle (7.7)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-CHND.1 | default and null | 7.7 | default value 0; compare only with `==`/`!=`; only literal it may be compared to is 0 | `h == 1`, `h < h2`, arithmetic are Neg | T S |
| DT-CHND.2 | not randomizable | 7.1, 7.7 | `rand chandle` is Neg | | D |
| DT-CHND.3 | FPI round-trip | 7.7.2 Ex 11 | chandle returned by import function, stored in a field, passed back | | T S |
| DT-CHND.4 | not castable | 7.12 | casts to/from chandle are Neg | | — |
| DT-CHND.5 | `%p` formatting | 21.1.1 | hex pointer with `0x` | | T S |
| DT-CHND.6 | typedefs of chandle | 21.2, 21.13.3 | `addr_handle_t`, `file_handle_t` are `typedef chandle` | | D |

### DT-STRUCT — structs (7.8, 8.3, 17.1, 20.3.2, 20.7.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-STRUCT.1 | fields and defaults | 7.8 | each field takes its type's default | field with initializer; field of enum type defaults to first item | T S C |
| DT-STRUCT.2 | nesting | 7.8 | struct-in-struct, struct with array/list fields, arrays of structs | access `s.inner.arr[2].f` | T S C |
| DT-STRUCT.3 | inheritance | 7.8, 17.1 | `struct D : B` has base fields first, then its own | field shadowing in D; base field reachable as `super.name` | T S C |
| DT-STRUCT.4 | assignment = deep copy | 8.3, 20.7.3 e | `a = b` copies every field, including nested structs/collections | later modification of `b` does not change `a` | T S |
| DT-STRUCT.5 | derived→base assignment | 8.3 | `B b = d;` copies only base fields | base→derived assignment is Neg | T S |
| DT-STRUCT.6 | equality | 8.5.3 | `==`/`!=` element by element; both operands same type | different struct types is Neg | T S C |
| DT-STRUCT.7 | struct literal | 4.8.4 | `{.b=2,.a=1}`: any order, unspecified fields default | same field twice is Neg; unknown field name is Neg | T S C |
| DT-STRUCT.8 | rand fields / constraints | 7.8, 13.4.1 | rand sub-fields randomized with the struct | non-rand collection inside a rand struct is legal | C |
| DT-STRUCT.9 | exec blocks in structs | 7.8, 20.1.2 | `pre_solve`, `post_solve`, `pre_body`, `header`, `declaration`, `run_start`, `run_end` allowed | `init_down`, `init_up`, `body` in a struct are Neg | D |
| DT-STRUCT.10 | struct as parameter | 20.3.2 | passed by handle: callee field writes are visible to caller | derived struct passed for base formal: callee sees base fields only | T S |
| DT-STRUCT.11 | struct as return | 20.2.2, Ex 298 | returned by value (local returned, caller gets a copy) | | T S |
| DT-STRUCT.12 | template structs | 7.8.1, 10 | `struct S<int N> { bit[N] f; }` | value param used as width | D |
| DT-STRUCT.13 | const struct | 7.1, 20.2.3 | `static const s MY_S = {...}` readable; only passable to `const` params | assigning a field of it is Neg | T S C |
| DT-STRUCT.14 | type extension | 17.2.3 | `extend struct S { int x; }` adds a field | extension of a packed struct may not add fields (21.13.1) | D |
| DT-STRUCT.15 | restrictions | 7.8, 7.10.1 a | fields must be plain-data; `ref` fields in structs are Neg | flow/resource object deriving from a struct is not a struct (17.1) — passing it for a struct param is Neg | D |
| DT-STRUCT.16 | access modifiers **NEW** | 7.1.1, 17.4 | `public`/`protected`/`private` on attr fields | access of a private field from outside is Neg | D |

### DT-PACK **NEW** — packed structs and layout (21.13.1-21.13.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-PACK.1 | `packed_s<E>` base | 21.13.1 | struct deriving (directly/indirectly) from `packed_s` is packed | default endianness LITTLE_ENDIAN | D |
| DT-PACK.2 | allowed field types | 21.13.1 | numeric, bool, enum with base type, packed structs, arrays thereof | string/list/chandle/`addr_handle_t` field is Neg (use `sized_addr_handle_s`) | D |
| DT-PACK.3 | little-endian layout | 21.13.1.1-2, Ex 344 | first field at LSB; bytes LS-first; pad at MSB | 30-bit struct → 4 bytes | T |
| DT-PACK.4 | big-endian layout | 21.13.1.3, Ex 345 | first field at MSB; bytes MS-first; pad at LSB | | T |
| DT-PACK.5 | inheritance order | 21.13.1.1 | derived fields packed after base fields | | D |
| DT-PACK.6 | `sizeof_s<T>` | 21.13.2 | `nbytes`/`nbits`: int=4, bit=1, bit[33]=5, array<int,10>=40, `{bit[2]; int}`=5 | `sizeof_s<string>` is Neg; `int[4]` (= `int[4]`-bit integer!) nbytes = 1 | D (constant) |
| DT-PACK.7 | no new fields by extension | 21.13.1 | `extend` of a packed struct adding fields is Neg | | D |

### DT-ARR — fixed-size arrays (7.9.2, 8.3, 8.5.3, 8.6.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-ARR.1 | two syntaxes | 7.9.2 Ex 15 | `int a[20]` ≡ `array<int,20>` | only template syntax works as a return type / nested in other collections | T S C |
| DT-ARR.2 | multi-dimensional | 7.9.2 | `int a[3][4]` ≡ `array<array<int,4>,3>`; `a[i][j]`, i < 3 | `a[i]` is itself an array value (assignable, comparable) | T S C |
| DT-ARR.3 | default/initializer | 7.9.1, 4.8.2 | elements default to element-type default; literal must have exactly N elements | `{1}` for `int[4]` and 6 elements for 4 are Neg | T S C |
| DT-ARR.4 | indexing | 8.6.2 | read/write `a[i]`, index any integer expression | out-of-bounds (incl. negative) is illegal | T S C |
| DT-ARR.5 | assignment copies | 7.9.2.1, 8.3 | `a = b` copies; later write to `b` not seen in `a` | different sizes is Neg | T S |
| DT-ARR.6 | equality | 7.9.2.1, 8.5.3 | equal if all elements equal in order | different sizes or element types is Neg (incomparable) | T S C |
| DT-ARR.7 | `in` membership | 7.9.2.1, 8.5.9 | `4 in arr` | element type must match the array's | T S C |
| DT-ARR.8 | `in` with slice | 8.5.9.1 | `v in arr[a..b]`, `arr[a..]`, `arr[..b]` | empty slice (a > b, a >= size, b < 0) → false; slice on value-list literal is Neg | T S C |
| DT-ARR.9 | `size()` | 7.9.2.2 | number of elements; a constant expression | also on arrays of modeling elements; deprecated `.size` property (7.9.2.4) | T S C |
| DT-ARR.10 | `sum()` | 7.9.2.2 T8 | int/bit arrays → `int`; float arrays → float64 | sum of `bit[8]` array returns 32-bit signed int (overflow beyond int); on string/struct arrays Neg; deprecated `.sum` | T S C |
| DT-ARR.11 | `join(connector)` | 7.9.2.2 | concatenates string elements with connector | size-1 array ignores connector; `""` connector | T S C |
| DT-ARR.12 | `str_from_chars()` | 7.9.2.2 | integer elements, each implicitly cast to 8 bits | element 0x141 → 'A' (0x41) | T S C |
| DT-ARR.13 | `to_list()`/`to_set()` | 7.9.2.2 | list preserves order; set dedups | | T S C |
| DT-ARR.14 | arrays of structs | 7.9.2.3 | `east_routes[1].f = v` | | T S C |
| DT-ARR.15 | arrays of modeling elements | 7.9.2 | component/action-handle arrays: only `[]`, `size()`, `foreach`, `in` with matching `ref` LHS | `==`/assignment on a component array is Neg | T S C |
| DT-ARR.16 | const arrays | 7.9 a-c | `static const array<int,3> A = {...}`: index yields const; no modification | `A[0] = 1` is Neg; foreach iterator over it is const | T S C |
| DT-ARR.17 | rand arrays | 7.9, 13.1.8 | `rand int vals[10]` with foreach constraints | | C |
| DT-ARR.18 | array param/return | 20.2.2, 20.3.2, Ex 291, Ex 317 | array params passed by handle; arrays returned by value | `array<int,3>` formal with a 4-element actual is Neg | T S |
| DT-ARR.19 | element types | 7.9 | arrays of any plain-data type incl. strings, enums, nested collections | | T S C |

### DT-LIST — list<T> (7.9.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-LIST.1 | declaration/init | 7.9.3 | empty by default; `= {1,2,3}` | `{}` assignment empties it | T S C |
| DT-LIST.2 | indexing | 7.9.3.1 | read/write `l[i]` for 0..size-1 | out-of-bounds is illegal (incl. write to `l[size]`: lists do not grow by index) | T S C |
| DT-LIST.3 | assignment/equality | 7.9.3.1 | copy; equal iff same size and same elements in order | different element types incomparable (Neg) | T S C |
| DT-LIST.4 | `in` | 7.9.3.1 | element membership | slice form `x in l[1..]` | T S C |
| DT-LIST.5 | `size()`, `clear()` | 7.9.3.2 | | `clear` only procedural | T S (C: size) |
| DT-LIST.6 | `delete(idx)` | 7.9.3.2 | removes and returns element | out-of-bounds is illegal; discarding result as statement | T S |
| DT-LIST.7 | `insert(idx, e)` | 7.9.3.2 | idx < size shifts; idx == size ≡ push_back | idx > size is illegal | T S |
| DT-LIST.8 | push/pop front/back | 7.9.3.2 | push_front ≡ insert(0); pop_front ≡ delete(0); pop_back ≡ delete(size()-1) | pop on empty list is illegal | T S |
| DT-LIST.9 | `join`, `str_from_chars`, `to_set` | 7.9.3.2 | as for arrays | `join` on empty list → `""` | T S C |
| DT-LIST.10 | `shuffle()` | 7.9.3.2 | random permutation, same multiset | random stability with fixed seed | T S |
| DT-LIST.11 | storage by value | 7.9 Ex 13 | `push_back(v1)` then `l[0].a = 10` leaves `v1` unchanged | | T S |
| DT-LIST.12 | modifying methods are procedural only | 7.9 | `push_back` etc. in a constraint/activity is Neg | | T S |
| DT-LIST.13 | list randomization | 7.9.3.4 | elements randomized; size is fixed | constraint on `size()` outside an iterative constraint on that list is Neg; `size()` inside its foreach is an invariant | C |
| DT-LIST.14 | list param (by handle) | 20.3.2 | callee `push_back` on a list param is visible to caller | | T S |
| DT-LIST.15 | nested collections | 7.9 Ex 14 | `list<map<string,int>>`, `list<array<int,2>>`, `list<ref C>` | | T S C |

### DT-MAP — map<K,V> (7.9.4)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-MAP.1 | declaration/literal | 7.9.4, 4.8.3 | empty by default; `{"a":1,"b":2}` | duplicate key in a literal: last value wins | T S C |
| DT-MAP.2 | index read | 7.9.4.1, 8.6.2 | `m[k]` for an existing key | reading a missing key is illegal | T S C |
| DT-MAP.3 | index write | 7.9.4.1 | `m[k] = v` inserts or replaces | | T S |
| DT-MAP.4 | assignment/equality | 7.9.4.1, 8.5.3 | copy; equal iff same size, same keys, same values (order-free) | different key/value types incomparable (Neg) | T S C |
| DT-MAP.5 | no direct `in` | 7.9.4.1 | `k in m` is Neg; use `k in m.keys()` / `v in m.values()` | | T S C |
| DT-MAP.6 | `size`, `clear` | 7.9.4.2 | | | T S (C: size) |
| DT-MAP.7 | `delete(key)` | 7.9.4.2 | removes, returns value | missing key is illegal | T S |
| DT-MAP.8 | `insert(k, v)` | 7.9.4.2 | adds or replaces | | T S |
| DT-MAP.9 | `keys()`/`values()` | 7.9.4.2 | `keys()` → set; `values()` → list, order unspecified | a test must not depend on `values()` order | T S C |
| DT-MAP.10 | non-randomizable | 7.9.4 | `rand map<...>` is Neg | | D |
| DT-MAP.11 | key types | 7.9.4 | keys of any plain-data type: int, string, enum, struct | | T S C |

### DT-SET — set<T> (7.9.5)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-SET.1 | declaration/literal | 7.9.5, 4.8.2 | `{1,2,0,0}` has size 3 | | T S C |
| DT-SET.2 | assignment/equality | 7.9.5.1, Ex 34 | equal iff same elements, order-free | `s == {1,2}` false when s has 3 elements | T S C |
| DT-SET.3 | `in` | 7.9.5.1 | membership | slice on a set is Neg | T S C |
| DT-SET.4 | `size`, `clear`, `insert`, `delete` | 7.9.5.2 | insert of existing element is a no-op | delete of a missing element is illegal | T S |
| DT-SET.5 | `to_list()` | 7.9.5.2 | arbitrary order | | T S C |
| DT-SET.6 | no indexing | 7.9.5 | `s[0]` is Neg | | — |
| DT-SET.7 | non-randomizable | 7.9.5 | `rand set<...>` is Neg | | D |

### DT-REF — reference types (7.10, 8.3, 8.5.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-REF.1 | ref local/param/return | 7.10.1 a, Ex 25 | `ref A aref = comp.a_arr[3]; aref.foo();` | | T S |
| DT-REF.2 | where ref fields are legal | 7.10.1 a | component ref fields (and collections) in components, actions, monitors | ref field in struct or flow/resource object is Neg; action/object ref fields are Neg; `static const ref` is Neg | D |
| DT-REF.3 | null | 7.10.1 c/d | default null; `r == null`, `r != null` | member access through null is an error | T S |
| DT-REF.4 | assignment aliases | 8.3 | after `r = c`, writes via `r` are writes to `c` | RHS: same/derived ref, instance path, or `null` | S (component writes only in init) |
| DT-REF.5 | equality is identity | 8.5.3 | true iff same entity or both null | ref vs instance path comparison | T S C |
| DT-REF.6 | up/down casts | 7.12.1 f/g, Ex 30 | upcast implicit; `(ref sub_C)comp` → null when dynamic type differs | | T S |
| DT-REF.7 | collections of refs | Ex 26 | `list<ref my_comp>`, `comp in top.used_comps` | | T S |
| DT-REF.8 | ref param | 20.3.2, 20.2.3 b | parameter aliases the entity; may be null | `const ref` param is Neg | T S |
| DT-REF.9 | call through ref / ref-returning call | Ex 25 | `comp.choose_A(123).foo()` | | T S |
| DT-REF.10 | ref in `?:` | 8.5.8 | branches of ref type | | T S |
| DT-REF.11 | built-in `comp` | 7.10, 9.1.5 | `comp.field`, `comp.sub.f()` in an action exec | static type is the containing component type (17.1) | T S C |

### DT-MUT **NEW** — mutable / instance component fields (9.1.5.3, 9.1.6)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-MUT.1 | component immutability | 9.1.6, 20.7.3 b/c | component fields assignable only in `init_down`/`init_up` (directly or via functions called there) | assigning a non-mutable component field from `post_solve`/`body` is Neg | S |
| DT-MUT.2 | `mutable` fields | 9.1.6 | may be modified in solve-time execs, propagates to aggregate members | modification from target execs is Neg; `mutable` on a component field is Neg | S |
| DT-MUT.3 | `instance` ref fields | 9.1.5.3 | instance-qualified component ref behaves like a component field | | D |

### DT-TYPEDEF — typedef (7.11)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-TYPEDEF.1 | scalar typedef | 7.11, 7.1 | `typedef bit[32] uint32_t;` behaves exactly like `bit[32]` (width, sign) | typedef of a domain type `typedef int in [1..5] small_t` | T S C |
| DT-TYPEDEF.2 | aggregate typedef | 7.1 | typedef of struct / `array<…>` / `list<…>` usable as return type and param | | T S C |
| DT-TYPEDEF.3 | typedef in casts | 7.12.1 | `(uint32_t)x`; type_identifier casting_type must be numeric/bool/enum/ref | cast to a typedef of a struct is Neg | T S C |
| DT-TYPEDEF.4 | scoped typedef | 7.8.1 | typedef in component/struct scope, referenced qualified | | D |

### DT-CAST — type conversion (7.12, 8.7.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-CAST.1 | numeric→numeric | 7.12.1 d, 8.7.2 | truncate or extend; extension uses SOURCE signedness | `(bit[4])0x1F` → 15; `(int[4])0xF` → -1 (as int[4]); `(bit[16])(int[8])-1` → 0xFFFF | T S C |
| DT-CAST.2 | to/from bool | 7.12.1 b | non-zero → true; true → 1 | | T S C |
| DT-CAST.3 | to enum | 7.12.1 e | value converted to enum's base type first | result may be a non-item value (Ex 28) | T S C |
| DT-CAST.4 | float↔int | 8.7.2 | float→int truncates toward zero as signed | `(bit[8])-1.5` → -1 → 0xFF | T S |
| DT-CAST.5 | precedence/associativity | 8.4.1 T11 | cast binds at level 2, right-assoc: `(bit[4])a + b` casts `a` only; `(int)(bit[4])x` nests | | T S C |
| DT-CAST.6 | cast gives expected type | 8.4.3 | `(color_e)RED` resolves `RED` unqualified | `(int)ORANGE` is Neg (Ex 37) | T S C |
| DT-CAST.7 | illegal casts | 7.12.1 a | string, struct, chandle, collections as source or target are Neg | ref→incompatible ref is Neg | — |
| DT-CAST.8 | implicit numeric compatibility | 7.12.1 c | no explicit cast needed between int/bit/float widths | | T S C |
| DT-CAST.9 | `(void)` | 20.7.4 | `(void)f()` is a statement form, not a cast expression | `x = (void)f();` is Neg | T S |

### DT-ADDR — address handles (21.13.3-21.13.8)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-ADDR.1 | `addr_handle_t`, `nullhandle` | 21.13.3 | typedef chandle; `nullhandle` is address 0 | concrete value not obtainable on the solve platform | T S |
| DT-ADDR.2 | `make_handle_from_claim(claim, offset=0, sub=false)` | 21.13.4.1 | handle = claim address ± offset | | S |
| DT-ADDR.3 | `make_handle_from_handle(h, off, sub=false)` | 21.13.4.2 | resolved = h + off, or h - off when `sub` | handle chains; 64-bit offset | T S |
| DT-ADDR.4 | `addr_value(h)` | 21.13.5 | target function returning bit[64] | call from a solve exec is Neg | T |
| DT-ADDR.5 | `addr_value_solve`/`addr_value_abs` | 21.13.6-7 | only meaningful in `pre_body` | other contexts: undefined result | S (pre_body) |
| DT-ADDR.6 | `get_tag(h)` | 21.13.8 | region tag string; pre_body/body/run_* only | | T S |
| DT-ADDR.7 | `sized_addr_handle_s<SZ,lsb,E>` | 21.13.3.3 | address field in a packed struct; bits [lsb+SZ-1:lsb] of resolved address | `addr_handle_t` directly in packed struct is Neg | D, T |
| DT-ADDR.8 | handles in collections/flow objects | Ex 357 | `list<addr_handle_t>` copied between actions | | S T |

### DT-REG — register types (21.14.1-21.14.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-REG.1 | `reg_c<R, ACC, SZ>` | 21.14.1 | R = packed struct or `bit[N]`; SZ default 8*nbytes(R) | SZ < size(R) is Neg; SZ > nbits → reserved high bits, undefined in `read_val` | D |
| DT-REG.2 | register placement | 21.14.1 | registers instantiable only inside a `reg_group_c` | register in a plain component is Neg; extending `reg_c`/`reg_group_c` themselves is Neg | D |
| DT-REG.3 | derived register type | Ex 360 | `pure component my_reg0_c : reg_c<my_reg0_s> {}` | | D |
| DT-REG.4 | value struct with array field | Ex 360 | `bit[2] fld2[5]` in a register-value struct | | D |
| DT-REG.5 | register/group arrays, nested groups | Ex 361 | `my_reg1_c reg1[4]`, sub-group, direct `reg_c<>` instance | two elements at the same offset (RO + WO) legal | D |
| DT-REG.6 | access kinds | 21.14.1 | READWRITE / READONLY / WRITEONLY | read or RMW of WRITEONLY is Neg; write or RMW of READONLY is Neg | T |
| DT-REG.7 | `ref reg_sized_c<N>` | Ex 358 | generic code over `list<ref reg_sized_c<32>>` | literal list of register refs as argument | T |

### DT-CONST — constants (7.1, 8.2, 10.3.1, 19.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| DT-CONST.1 | `static const` | 7.1.1 | package/component/struct static constant with initializer; usable as width, array size, template arg | its declared type governs (`static const bit[64] OFF = 12` is not `int`) | T S C |
| DT-CONST.2 | `const` field | 7.1 | readable, never on LHS; aggregate members const too | | T S C |
| DT-CONST.3 | constant expressions | 8.2 | literals, constant aggregate literals, named constants, bit/part-selects of named constants, enum items, pure-function calls with constant args | a non-pure call in a width expression is Neg | D |
| DT-CONST.4 | template value params | 10.3.1 | scalar (not chandle) value params usable wherever a constant is expected | default referencing an earlier param; `(width > 10)` must be parenthesized | D |
| DT-CONST.5 | const aggregates as arguments | 20.2.3 | static const arrays/structs only to `const` formals | Ex 291 errors | T S |
| DT-CONST.6 | bit-select of constant | 8.6 | `C[3]`, `C[7:4]` where C is static const | | T S C |
| DT-CONST.7 | compile-time order | 19.1.2 | static const initializers are evaluated before compile-if conditions, in source order | forward reference to a later constant is Neg | D |

---

## 2. Expressions (LRM 8, 4.6-4.8)

### EX-LIT — literals (4.6-4.8, 8.4.2, 8.7.1 T21)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-LIT.1 | unsized decimal | 4.6.1, T21 | signed `int[N]`, N = max(32, minimal) | `4294967296` is a 34-bit signed literal | T S C |
| EX-LIT.2 | unsized hex/binary | 4.6.1, T21 | `0x..`/`0b..` are UNSIGNED `bit[N]`, N ≥ 32 | `0x7_0000_0000` is 35 bits; `0xFFFFFFFF > -1` is false (unsigned compare) | T S C |
| EX-LIT.3 | unsized octal | 4.6.1 | leading `0` means octal: `017` == 15, signed | `08` is Neg | T S C |
| EX-LIT.4 | based literals | 4.6.1 | `'d 'h 'o 'b` (case-insensitive), optional `s`, optional size: `8'hFF`, `4'sd12` | apostrophe and base letter may not be separated; digit illegal for base is Neg | T S C |
| EX-LIT.5 | size padding/truncation | 4.6.1 | unsigned → zero pad; signed → sign extend; too long → truncate from left | `4'hFF` = 0xF; `4'sb1000` = -8 | T S C |
| EX-LIT.6 | underscores | 4.6.1 | `1_000`, `0xFFFF_FFFF` | leading `_` is Neg | T S C |
| EX-LIT.7 | negated based literals | 4.6.1 | `-12/3` = -4; `-'d12/3` = 1431655761; `-'sd12/3` = -4; `-4'sd12/3` = 1 | the whole table is the test | T S C |
| EX-LIT.8 | float literals | 4.6.2 | decimal and scientific forms, float64 | | T S C |
| EX-LIT.9 | `true`/`false` | 7.4 | bool literals | | T S C |
| EX-LIT.10 | `null` | 8.1 | ref literal | null to non-ref type is Neg | T S |
| EX-LIT.11 | empty aggregate `{}` | 4.8.1 | empties list/map/set | `{}` to a fixed array is Neg (size mismatch) | T S C |
| EX-LIT.12 | value-list literal | 4.8.2 | elements are assignment-like converted to element type; may be non-constant (Ex 35) | no context type (e.g. `foreach (e : {1, 0x2})`): elements must have the same self-determined type, else Neg | T S C |
| EX-LIT.13 | map literal | 4.8.3 | `{k:v,...}`, keys/values converted to context types | duplicate key: last wins | T S C |
| EX-LIT.14 | struct literal | 4.8.4 | see DT-STRUCT.7 | | T S C |
| EX-LIT.15 | nested aggregate literals | 4.8.5 | `list<s> l = {{.a=1},{.b=2}}` | | T S C |
| EX-LIT.16 | literal positions | 8.4.2, 8.5.3, 8.5.9 | not an assignment target; not LHS of `==`; not LHS of `in` | aggregate literal argument requires a `const` formal (20.2.3) | T S C |

### EX-TQS **NEW** — triple-quoted strings with special elements (4.7.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-TQS.1 | mustache in a string expression | 4.7.1, 4.7.1.1 | `string s = """x={{x}}""";` expands in procedural code | expression must be scalar; calls must be pure | T S |
| EX-TQS.2 | mustache formatting | 4.7.1.1 | int → `%d`, bit → `%u`, bool → `true`/`false`, enum → item name, string, chandle `%p`, float `%f` | negative `bit`-typed expression prints unsigned | T S |
| EX-TQS.3 | control-flow directives | 4.7.1.2 T5 | `{% if %}`/`else if`/`else`, `{% repeat (i: n) %}`, `{% foreach %}`, `{%%}`, local var decl and assignment | directive-only lines drop their whitespace and newline | T S |
| EX-TQS.4 | embedded comments | 4.7.1.3 | `{# ... #}` and `{#}` line comments are removed | mustache inside a comment is not expanded | T S |
| EX-TQS.5 | constness | 4.7.1 | a TQS referencing only constants is constant | non-constant TQS where a constant string is required is Neg | D |

### EX-PREC **NEW** — precedence and associativity (8.4.1 T11)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-PREC.1 | level order | T11 | `() []` > cast, unary > `**` > `* / %` > `+ -` > `<< >>` > relational, `in` > `== !=` > `&` > `^` > `\|` > `&&` > `\|\|` > `?:` | `a & b == c` parses as `a & (b == c)` (type error when a is int) | T S C |
| EX-PREC.2 | left associativity | 8.4.1 | `A + B - C`, `a - b - c`, `a / b / c` | `2 ** 3 ** 2` is LEFT-assoc in PSS = 64 (not 512) | T S C |
| EX-PREC.3 | right associativity | 8.4.1 | `?:` and cast associate right to left | `a ? b : c ? d : e` | T S C |
| EX-PREC.4 | unary minus vs `**` | 8.5.1, T11 | unary binds tighter than `**`: `-2 ** 2` = 4 | | T S C |
| EX-PREC.5 | shift vs add | T11 | `1 << n + 1` = `1 << (n+1)` | | T S C |

### EX-ARITH — arithmetic (8.5.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-ARITH.1 | `+ - *` wrap at expression width | 8.5.1, 8.7.1, Ex 42 | `bit[8] 0xf0 + bit[12] 0xff0` printed via varargs = 0xe0 | same in a `bit[16]` target = 0x10e0 | T S C |
| EX-ARITH.2 | `/` truncates toward zero | 8.5.1 | `-7 / 2` = -3; `7 / -2` = -3 | unsigned division of a negative-looking value (`bit[8] 0xFE / 2` = 127) | T S C |
| EX-ARITH.3 | `%` sign follows dividend | 8.5.1 | `-7 % 2` = -1; `7 % -2` = 1 | `%` is integer-only (float Neg) | T S C |
| EX-ARITH.4 | division/modulus by zero | 8.5.1 | illegal | constant `/0` (compile-time) vs run-time zero | T S C |
| EX-ARITH.5 | `**` power | 8.5.1 T13, T22 | result type = LHS type (RHS self-determined); float operand → float64 | `2 ** 31` in int wraps negative; `x ** -1` = 0 for \|x\|>1; `1 ** -n` = 1; `(-1) ** -n` = ±1; `0 ** -1` unspecified | T S C |
| EX-ARITH.6 | unary `-` | 8.5.1, T22 | same type as operand, context-propagated | `bit[8] x = 1; -x` = 255 in 8 bits, `0xFFFFFFFF` in 32-bit context; `-INT_MIN` = INT_MIN | T S C |
| EX-ARITH.7 | signedness of result | T22 | signed only if both operands signed | `int + bit[8]` is unsigned (mixed) | T S C |
| EX-ARITH.8 | no short-circuit | 8.4.4 | all operands of arithmetic operators are evaluated | `f() * 0` still calls `f` | T S |
| EX-ARITH.9 | unary `+` | 8.1 vs T22 | `+x`: the grammar's unary_operator list has no `+` although T22 lists it | test what pssc accepts; LRM inconsistency | T S C |

### EX-REL — relational (8.5.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-REL.1 | `< <= > >=` | 8.5.2 | numeric operands, bool result | on enum/bool/string is Neg | T S C |
| EX-REL.2 | unsigned if any unsigned | 8.5.2, Ex 43 | `bit[32] 1 < int -10` is TRUE | `bit[8] 1 < int -1`: -1 → 0xFFFFFFFF | T S C |
| EX-REL.3 | width extension | 8.5.2 | smaller operand zero-/sign-extended per the above | `int[4] -1 < int[8] 0` (both signed) is true | T S C |
| EX-REL.4 | float operand | T22 | int operand self-determined, then converted | | T S |
| EX-REL.5 | chaining | 8.5.2 | `a < b < c` compares a bool with a number → Neg | | T S C |

### EX-EQ — equality (8.5.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-EQ.1 | numeric bitwise compare | 8.5.3 | same extension rules as relational | `bit[8] 255 == -1` is false (zero-extended 255 vs 0xFFFFFFFF); `int[8] -1 == -1` is true | T S C |
| EX-EQ.2 | string | 8.5.3 | size and content | | T S C |
| EX-EQ.3 | bool / enum | 7.5.1 | same-type equality | enum vs int without cast is Neg | T S C |
| EX-EQ.4 | struct | 8.5.3 | element-wise, same type | | T S C |
| EX-EQ.5 | collections | 8.5.3 | arrays (same size), lists (order), maps/sets (order-free) | arrays of different sizes is Neg | T S C |
| EX-EQ.6 | literal RHS | 8.5.3, 8.4.2 | RHS aggregate literal typed by LHS; unspecified struct fields default (Ex 33) | literal on LHS is Neg | T S C |
| EX-EQ.7 | references | 8.5.3 | identity; with `null`; ref vs instance path | | T S C |
| EX-EQ.8 | chandle | 7.7 | only with another chandle or `0` | | T S |
| EX-EQ.9 | unqualified enum on RHS | 8.4.3 | `e == RED` | `RED == e` (LHS unqualified) is Neg | T S C |

### EX-LOG — logical (8.5.4, 8.4.4)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-LOG.1 | `&& \|\| !` on bool | 8.5.4 | bool operands, bool result | `!x` with `int x` is Neg | T S C |
| EX-LOG.2 | short-circuit `&&` | 8.4.4, 8.5.4 | RHS not evaluated when LHS false | guard `i < l.size() && l[i] == v` must not fault | T S |
| EX-LOG.3 | short-circuit `\|\|` | 8.4.4 | RHS not evaluated when LHS true | side-effect call on RHS | T S |
| EX-LOG.4 | precedence | 8.5.4 | `&&` above `\|\|`; both below equality | `a \|\| b && c` | T S C |
| EX-LOG.5 | self-determined operands | T22 | no width propagation into operands | | T S C |

### EX-BIT — bitwise (8.5.5)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-BIT.1 | `& \| ^` | 8.5.5, T22 | integer only; width = larger, signed iff both signed; propagated to operands | `int[4] -1 & bit[8] 0xF0`: propagated type unsigned → `-1` ZERO-extended to 0x0F → result 0x00 | T S C |
| EX-BIT.2 | `~` | 8.5.5, T22 | same type as operand, context-propagated | `bit[8] x = 0; bit[32] y = ~x;` = 0xFFFFFFFF (x widened first); `~x == 0xFF` false in 32-bit compare | T S C |
| EX-BIT.3 | precedence | T11 | `&` > `^` > `\|`, all below `==` | `a \| b ^ c & d` | T S C |
| EX-BIT.4 | not on bool/float | T10 | Neg | | — |

### EX-RED — reduction (8.5.6)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-RED.1 | `&x \|x ^x` | 8.5.6 T20 | 1-bit `bit` result; operand self-determined | values of T20 (0000, 1111, 0110, 1000) | T S C |
| EX-RED.2 | result type is `bit` not `bool` | T22 | `if (&x)` requires a bool → Neg; `if (&x == 1)` legal | | T S C |
| EX-RED.3 | signed operand | 8.5.6 | `&(-1)` = 1 at the operand's own width | `^` of `int[5]` | T S C |
| EX-RED.4 | in arithmetic | T22 | `\|x + 1` = `(\|x) + 1` (unary binds tighter) | | T S C |

### EX-SHIFT — shifts (8.5.7)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-SHIFT.1 | `<<` | 8.5.7, T22 | zero fill; result type = LHS type, LHS context-propagated, RHS self-determined | `bit[8] x = 0x80; bit[16] y = x << 1;` = 0x100 (16 propagated into x) | T S C |
| EX-SHIFT.2 | `>>` | 8.5.7 | zero fill for unsigned or non-negative; ones fill for negative signed | `int x = -8; x >> 1` = -4; `bit[8] 0x80 >> 1` = 0x40 | T S C |
| EX-SHIFT.3 | negative shift amount | 8.5.7 | RHS shall be non-negative → illegal | run-time negative from a signed variable | T S C |
| EX-SHIFT.4 | shift ≥ width | 8.5.7 (implied) | all bits vacated: 0, or all ones for a negative signed `>>` | `int x = 1; x << 32` = 0 (not 1 as in C on x86) | T S C |
| EX-SHIFT.5 | literal LHS width | T21, 8.7.2 | `bit[64] y = 1 << 40;` — target width 64 propagated → 2**40 | `print("%d", 1 << 40)` (self-determined, 32-bit) → 0 | T S C |
| EX-SHIFT.6 | RHS width independence | T22 | `x << bit[2] 3` | RHS wider than LHS (`bit[8] x << bit[64] n`) | T S C |

### EX-COND — conditional `?:` (8.5.8)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-COND.1 | bool predicate | 8.5.8 | first operand bool | int predicate is Neg | T S C |
| EX-COND.2 | short-circuit | 8.4.4, 8.5.8 | only the chosen branch evaluated | side-effect calls in both branches | T S |
| EX-COND.3 | operand types | 8.5.8 | branches same type: any plain-data (struct, string, collection) or ref | int vs string branches is Neg | T S C |
| EX-COND.4 | numeric typing | T22 | width = larger, signed iff both; propagated | `c ? bit[4] 15 : int[8] -1` in 32-bit context | T S C |
| EX-COND.5 | expected type to branches | 8.4.3 | `e = c ? RED : GREEN;` unqualified items; struct literals in branches | | T S C |
| EX-COND.6 | right associativity / lowest precedence | T11 | nested ternaries; `c ? a : b + 1` = `c ? a : (b+1)` | | T S C |

### EX-IN — set membership (8.5.9)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-IN.1 | open range list | 8.5.9.1 | `x in [1, 2, a..b]`, `[..b]`, `[a..]` | ranges only for numeric/enum | T S C |
| EX-IN.2 | empty / overlapping ranges | 8.5.9.1 | `lo > hi` range is empty; overlaps fine | variable bounds become empty at run time | T S C |
| EX-IN.3 | collection RHS | 8.5.9.1 | array, list, set, value-list literal, `m.keys()` | map RHS is Neg; a list variable inside `[...]` is Neg (Ex 39) | T S C |
| EX-IN.4 | slice RHS | 8.5.9.1 | see DT-ARR.8 | slice on a set or literal is Neg | T S C |
| EX-IN.5 | typing: collection | T22 | integer LHS and items: larger width, signed iff both | `bit[2] x=3,y=2; (x+y) in list<int>{4,5,6}` holds (32-bit) | T S C |
| EX-IN.6 | typing: range list | T22, Ex 45 | all items and LHS share the propagated type | `(x+y) in [a+b..c+d]` with `int[3] c=-4` evaluates at bit[3]: `3'd5 in [3'd4..3'd6]` holds | T S C |
| EX-IN.7 | LHS restrictions | 8.5.9.1 | LHS not an unqualified enum item, not an aggregate literal | struct LHS requires a collection RHS | T S C |
| EX-IN.8 | strings / enums | 7.5.2, 7.6.4 | `s in ["a","b"]`, `e in [A..C]` (unqualified items allowed on RHS) | string range is Neg | T S C |
| EX-IN.9 | float mixing | T22 | float LHS converts integer items | | T S |
| EX-IN.10 | ref LHS / component arrays | 8.5.9.1 | `r in comp.sub_arr` | | T S C |
| EX-IN.11 | evaluation order | 8.5.9.1 | range expressions evaluated in nondeterministic order | tests must not depend on side-effect order inside the list | T S |

### EX-SEL — bit/part select (8.6.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-SEL.1 | bit-select read | 8.6.1, T21 | `x[i]` with non-constant i; type `bit` | on a signed int yields unsigned bit | T S C |
| EX-SEL.2 | part-select read | 8.6.1, T21 | `x[h:l]` constant h ≥ l; unsigned, width h-l+1 | `x[3:4]` (h < l) is Neg; non-constant bounds are Neg | T S C |
| EX-SEL.3 | out-of-bounds | 8.6.1 | illegal (constant OOB a compile error; run-time OOB illegal) | `bit[8] x; x[8]` | T S C |
| EX-SEL.4 | select as LHS | 8.6.1 | `x[3] = 1; x[7:4] = 0xA;` read-modify-write of x | RHS wider than the part is truncated | T S |
| EX-SEL.5 | select of named constants | 8.2, 8.6 | `C[7:4]` of a static const / template param is a constant expression | | T S C |
| EX-SEL.6 | select of path expressions | B.18 | `a[i][3:0]` (array element), `s.f[2]` (struct field), `data[63:32]` (Ex 349) | select on a non-integer (string/array of structs) is Neg | T S C |
| EX-SEL.7 | select in wider context | T21 | part-select is unsigned: `int y = x[7:0]` zero-extends | | T S C |

### EX-IDX — collection indexing (8.6.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-IDX.1 | array/list index | 8.6.2 | 0..size-1 integer index | negative or ≥ size illegal | T S C |
| EX-IDX.2 | map index | 8.6.2 | key-typed index; missing-key read illegal; write inserts | | T S C |
| EX-IDX.3 | indexed lvalue | 8.6.2 | `a[i] = v`, `a[i] += v`, `a[i].f = v`, `a[i][j] = v` | | T S |
| EX-IDX.4 | index expression type | 8.6.2 | any integral; bit[64] index; signed variable index | | T S C |
| EX-IDX.5 | index of call/method results | B.18, Ex 22 | `m.values()[i]`, `f()[2]` | | T S C |
| EX-IDX.6 | component / ref arrays | Ex 25 | `comp.a_arr[code % 5]` | | T S C |
| EX-IDX.7 | const collections | 7.9 a | index yields a constant | `CONST_ARR[0] = 1` is Neg | T S C |

### EX-SUBSTR — sub-string (7.6.2, 8.6.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-SUBSTR.1 | single char | 8.6.3 | `s[1]` is a one-char string | | T S C |
| EX-SUBSTR.2 | ranges | 8.6.3, Ex 10 | `s[m..k]` inclusive, `s[..4]`, `s[7..]` | `s[..k]`/`s[m..]` defaults | T S C |
| EX-SUBSTR.3 | bounds | 8.6.3, Ex 10 | positions 0..size-1, start ≤ end | `s[7..100]` and `s[4..1]` are errors; any position on `""` is an error | T S C |
| EX-SUBSTR.4 | not an lvalue | 7.6.2 | `s[0] = "x"` is Neg | | — |
| EX-SUBSTR.5 | in constraints | 7.6.2 | allowed only if neither string nor indices are random | | C |
| EX-SUBSTR.6 | non-constant indices | 8.6.3 | loop-variable positions | | T S |

### EX-METH **NEW** — built-in method calls in expressions (7.6.3, 7.9.x)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-METH.1 | method on variable | 7.9 | `l.size()`, `s.find("x")` as operands | | T S C |
| EX-METH.2 | chained methods | 7.6.3 | `s.lower().find("x")`, `m.keys().size()`, `s.split(",").join(";")` | | T S C |
| EX-METH.3 | modifying method in expression | 7.9.3.2 | `x = l.pop_back() + l.pop_front();` | evaluation order of the two calls unspecified; must not be used in `C` | T S |
| EX-METH.4 | pure built-ins in constraints | 7.9 | `size()`, `sum()`, `in` usable in constraints | modifying ones Neg in `C` | C |

### EX-WIDTH — numeric evaluation rules (8.7)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-WIDTH.1 | primary types | 8.7.1 T21 | decimal → `int[≥32]`, hex/bin → `bit[≥32]`, sized as given, float → float64, variable as declared, call → return type, bit-select → bit, part-select → unsigned | | T S C |
| EX-WIDTH.2 | binary arithmetic/bitwise | T22 | width = larger, signed iff both; propagated to both operands | operand conversion uses the PROPAGATED signedness (8.7.1): signed narrow operand in unsigned context is ZERO-extended | T S C |
| EX-WIDTH.3 | assignment-like: widen INTO source | 8.7.2, Ex 42 | target wider than source → target width propagated into source before evaluation (`bit[16] z = x+y` = 0x10e0) | source signedness stays self-determined | T S C |
| EX-WIDTH.4 | assignment-like: narrow AFTER | 8.7.2 | target narrower → evaluate at source width, then truncate | `bit[4] z = 0x1F + 1` = 0 | T S C |
| EX-WIDTH.5 | contexts list | 8.7.2 T23 | assignment (incl. initializers, compound assignment, enum item values, default param values, struct-literal items, annotation params), cast, call argument, `return`, value-list element, map key/value, template value arg, typed coverpoint | | T S C |
| EX-WIDTH.6 | generic/varargs self-determined | T23 | `print("%x", x+y)` evaluates at the operands' own width (Ex 42: 0xe0) | `message(LOW, "%d", a + b)` with bit[8] operands wraps | T S |
| EX-WIDTH.7 | float mixing | 8.7.1, Ex 44 | integer subexpression self-determined, converted just before the float operator | `x + y + z` (32) = 15 vs `x + y + f` = 11 | T S |
| EX-WIDTH.8 | relational/equality propagate to operands | T22 | result bool but operands share max width and signedness | | T S C |
| EX-WIDTH.9 | logical / reduction self-determined | T22 | no propagation | | T S C |
| EX-WIDTH.10 | shift / power | T22 | LHS propagated, RHS self-determined | `bit[64] y = x << n` widens x | T S C |
| EX-WIDTH.11 | conditional | T22 | branches propagated | | T S C |
| EX-WIDTH.12 | `in` | T22, Ex 45 | collection vs range-list rules (EX-IN.5/6) | | T S C |
| EX-WIDTH.13 | unary propagation | T22 | `-` and `~` take the context width | | T S C |
| EX-WIDTH.14 | uniform on all platforms | 8.7 | same rules in constraints, activities, solve and target code | target C must not use C's promotion rules | T S C |
| EX-WIDTH.15 | 32-bit literal in 64-bit context | T21, 8.7.2 | `bit[64] m = 0xFFFFFFFF << 4` = 0xFFFFFFFF0 | | T S C |
| EX-WIDTH.16 | > 64-bit arithmetic | 7.2 | `bit[128]` operations are exact | C/C++ refuse (pssc ruling); Python/SV must compute | T S C |

### EX-ASSIGN — assignment operators (8.3, 20.7.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-ASSIGN.1 | `=` | 8.3 T9 | any plain-data or ref type | | T S |
| EX-ASSIGN.2 | `+= -=` | 8.3 T9 | numeric (incl. float) | | T S |
| EX-ASSIGN.3 | `&= \|=` | 8.3 T9 | integer | on bool is Neg | T S |
| EX-ASSIGN.4 | `<<= >>=` | 8.3 T9 | integer | | T S |
| EX-ASSIGN.5 | `*= /= %= ^=` | 8.1 assign_op, T9 | NOT in the LRM (neither the grammar nor T9) | Neg per LRM; record what pssc accepts | T S |
| EX-ASSIGN.6 | compound ≡ `a = a op b` | 8.3 | width context is the LHS type; `bit[8] a=0xFF; a += 1` = 0 | LHS with a side effect (`l[f()] += 1`): single evaluation not stated | T S |
| EX-ASSIGN.7 | not an expression | 8.3 | `a = b = c`, `if (a = b)`, `x = (y += 1)` are Neg | | — |
| EX-ASSIGN.8 | aggregate assignment | 8.3 | element-wise copy; fixed arrays same size; struct derived→base | | T S |
| EX-ASSIGN.9 | lvalue forms | 20.7.3.1 | variable, field path, element, bit/part select, map element (insert) | aggregate literal, sub-string, const, foreach iterator/index as LHS are Neg | T S |
| EX-ASSIGN.10 | ref assignment | 8.3 | aliasing (DT-REF.4) | | T S |

### EX-REF — references and name resolution (8.6, 17.1, B.18)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EX-REF.1 | field paths | 8.6 | `s.a.b`, `a[i].f`, `comp.sub.x` | | T S C |
| EX-REF.2 | `this` | Ex 241 | `this.comp`, `this.x` in actions/structs | | T S C |
| EX-REF.3 | `comp` | 9.1.5 | action's context component and its sub-tree | | T S C |
| EX-REF.4 | sub-component / array element | 9.1.4 | `sub.x`, `sub[i].x`, `sub[i].f()` | run-time index into a component array | T S C |
| EX-REF.5 | `super.field` | 17.1 | shadowed base field in a derived type | | T S C |
| EX-REF.6 | static paths | B.18 static_ref_path | `pkg::C`, `comp_t::CONST`, `::name`, `E::ITEM` | | T S C |
| EX-REF.7 | parameter shadows field | 20.3, B.18 | inside an instance function, a param named like a field wins | assignment to the param leaves the field unchanged | T S |
| EX-REF.8 | local shadows field/outer local | 20.7.1 | nested-block local masks outer names | | T S |
| EX-REF.9 | call in path | B.18 member_path_elem | `comp.choose_A(1).foo()`, `f().x`, `f()[i]` | | T S |
| EX-REF.10 | through ref fields | Ex 26 | `close_sibling.data` | | T S |
| EX-REF.11 | access protection | 17.4 | private/protected members | | D |

---

## 3. Functions (LRM 20.2-20.6)

### FN-DECL — declarations (20.2, 20.2.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-DECL.1 | package/global function | 20.2.1 a | always static; prototype then separate definition, or definition-only | two definitions of one function is Neg | D |
| FN-DECL.2 | component static function | 20.2.1 c | `static function` called `C::f()` | | D |
| FN-DECL.3 | component instance function | 20.2, 20.2.1 d | called on an instance: `comp.f()`, `sub.f()`, plain `f()` in the component | | D |
| FN-DECL.4 | return types | 20.2.2 | void, scalars, struct, `array<…>`, list/map/set, `ref` types | returning an action/component/flow type without `ref` is Neg | D |
| FN-DECL.5 | platform qualifier | 20.2.1.3 | `solve`/`target`; unqualified = both; a definition may add a restriction (Ex 297) | definition qualifier different from a qualified declaration is Neg | D |
| FN-DECL.6 | definition location | 20.3 | static: same package/component (or its extension); instance: same component, its extension, or a derived component | | D |
| FN-DECL.7 | declared in base, defined in derived | 20.2.7 | call in context of a base instance is Neg; in derived is fine | | D |
| FN-DECL.8 | redeclaration consistency | 20.2.3 c, 20.2.4 c | `const` and default values must match | | D |

### FN-PARAM — parameters (20.2.2-20.2.5, 20.3.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-PARAM.1 | scalars by value | 20.3.2 | callee writes are not visible to caller (Ex 298 `a = 0`) | | T S |
| FN-PARAM.2 | aggregates by handle | 20.3.2 | callee writes to fields/elements/list content are visible to caller | | T S |
| FN-PARAM.3 | aggregate param assignment | 20.3.2, Ex 291 | `s1 = s2` inside the callee copies INTO the caller's object (`swap_structs` swaps the caller's structs) | | T S |
| FN-PARAM.4 | derived for base formal | 20.3.2 | only base fields visible; derived fields untouched | | T S |
| FN-PARAM.5 | `const` params | 20.2.3 | const param is read-only in the body; const aggregates (static const, aggregate literal) only to const formals | `const` with a direction, or on a ref/ref collection, is Neg | T S |
| FN-PARAM.6 | default values | 20.2.4 | constant expression, plain-data only, trailing; `f(3)` uses default | non-trailing default is Neg; default on output/inout of an import is Neg | T S |
| FN-PARAM.7 | directions | 20.2.2, 20.3.2 | `input/output/inout` only on imported functions | direction on a native or target-template function is Neg | D |
| FN-PARAM.8 | generic params | 20.2.5 a/b | `type x`, `struct x`, `numeric x`, `ref component x` | `ref` with struct/numeric category Neg; defaults on generic params Neg | D (built-ins) |
| FN-PARAM.9 | varargs | 20.2.5 c/d | `type ... args`, last parameter only; zero or more actuals | varargs not last is Neg | D (built-ins) |
| FN-PARAM.10 | ref params | 20.3.2 | alias, may be null | | T S |
| FN-PARAM.11 | expected type from formal | 8.4.3 | unqualified enum items, aggregate literals as arguments | | T S |
| FN-PARAM.12 | argument conversion | 8.7.2 T23 | arguments are assignment-like: widened into, or truncated to, the formal | not for generic params (self-determined) | T S |
| FN-PARAM.13 | array formal size | 8.3 | `array<int,3>` actual for `array<int,3>` formal | size mismatch is Neg | T S |

### FN-PURE — pure functions (20.2.6)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-PURE.1 | declaration | 20.2.6 a | non-void, no output/inout | `pure function void f()` is Neg | D |
| FN-PURE.2 | keyword inheritance | 20.2.6 b | definition may omit `pure` if the declaration has it | non-pure redeclared pure in a derived type is Neg | D |
| FN-PURE.3 | memoization licence | 20.2.6, Ex 295 | results may be reused for equal arguments | a test must not observe call counts of a pure function | T S |
| FN-PURE.4 | constant expression | 8.2 | pure call with constant args is a constant expression | as an array size / width | D |
| FN-PURE.5 | required in TQS/mustache | 4.7.1 | only pure calls inside `{{...}}` and directives | | T S |

### FN-CALL — calls (20.2.7, 20.7.4)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-CALL.1 | statement call | 20.7.4 | void call; non-void call discarding the result; `(void)f()` | void call inside an expression is Neg | T S |
| FN-CALL.2 | expression operand | 20.2.7 | `x = f(a) + g(b)`, nested `f(g(x))` | argument / operand evaluation order not specified | T S C |
| FN-CALL.3 | qualified | 20.2.1 c | `pkg::f()`, `comp_t::sf()` | | T S |
| FN-CALL.4 | recursion | 20.2.7 | direct and mutual recursion | deep recursion per frame locals | T S |
| FN-CALL.5 | instance calls | 20.2.1 d | `comp.f()`, `sub[i].f()`, via `ref` | | T S |
| FN-CALL.6 | platform legality | 20.2.1.3, Ex 297 | solve fn from `body` Neg; target fn from `post_solve` Neg; also indirectly via an unqualified function | | T S |
| FN-CALL.7 | definition availability | 20.2.7 | call is valid only if a definition exists for the platform/instance type | | T S |
| FN-CALL.8 | init execs and target templates | 20.1.2 | `init_down`/`init_up` may not call target-template functions | | S |
| FN-CALL.9 | calls in aggregate literals | Ex 35 | `{i, get_val(i)}` | | T S |
| FN-CALL.10 | calls in constraints | 8.2, 13 | pure/solve-available functions in constraints | target fn in a constraint is Neg | C |

### FN-NATIVE — native PSS function bodies (20.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-NATIVE.1 | procedural body | 20.3.1 | any procedural statement in a function body | | T S |
| FN-NATIVE.2 | return obligations | 20.7.5 | non-void returns a value; void uses `return;` | missing `return` on some path: behaviour unspecified | T S |
| FN-NATIVE.3 | component attribute writes | 20.7.3 b | instance function setting its component's fields — callable only from init execs | same call from `body` is Neg | S |
| FN-NATIVE.4 | struct/action writes through params | 20.7.3 d | function writing a struct param may be called anywhere | | T S |
| FN-NATIVE.5 | randomize in a function | Ex 297 | `randomize res with {res > a;}` in a solve function | | S |
| FN-NATIVE.6 | aggregate return | Ex 298 | local struct returned by value | | T S |

### FN-IMPORT — imported functions (20.4.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-IMPORT.1 | import by name | 20.4.1.1 | `import [solve\|target] [C] function pkg::f;` for a declared function | | D |
| FN-IMPORT.2 | import with prototype | 20.4.1.1 | prototype must match: static, count, names, types, directions, return | | D |
| FN-IMPORT.3 | allowed types | 20.4.1 c | int/bit ≤ 64, bool, enum, string, chandle, struct, arrays of those, lists of those except string/chandle | `bit[65]` param is Neg; `list<string>` param is Neg | D |
| FN-IMPORT.4 | directions | 20.4.1 d/e | input (default), output (unknown on entry), inout | modification of an input in foreign code not reflected | T S |
| FN-IMPORT.5 | scope restrictions | 20.4, 20.4.1 | instance functions cannot be imported; component static fn only imported in the same component type; not in a template component | | D |
| FN-IMPORT.6 | language identifier | 20.4.1.2 | `C`, `CPP`, `SV` | | D |
| FN-IMPORT.7 | import class | 20.4.3 | `import class B { ... }`, `: base`; fields in package (global) or component (per instance) | **NEW sub-construct** | T S |

### FN-EXPORT — exported functions (20.4.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-EXPORT.1 | `export target function f;` | 20.4.2.1 | only static functions with a native body, in a package or component | LRM Ex 300 writes `export function irq;` (no `target`) — grammar inconsistency | D |
| FN-EXPORT.2 | allowed types | 20.4.2.1 | int/bit ≤ 64, bool, enum (base ≤ 64), float32/64, string, chandle, const struct | non-const struct param is Neg | D |
| FN-EXPORT.3 | capabilities | 20.4.2.2 | statics, static functions, memory functions, `executor()`, components via executor | full-path component access (`pss_top.x`) not supported | T |
| FN-EXPORT.4 | pssc extension | (pssc) | root instance-function export (PSS120 warning) | outside LRM | T |

### FN-SOLVE — platform qualifiers and solve functions (20.2.1.3, 21.14)

The LRM has no "constructor" concept; the pssc constructor (`solve function`
designated as ctor) is a pssc ruling and belongs in pssc-specific catalogs.

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-SOLVE.1 | solve function | 20.2.1.3 | callable from init_down/init_up/pre_solve/post_solve/pre_body | from body/run_start/run_end (directly or indirectly) Neg | S |
| FN-SOLVE.2 | target function | 20.2.1.3 | callable from body/run_start/run_end | from solve execs Neg | T |
| FN-SOLVE.3 | restriction added by definition | Ex 297 | unqualified declaration with a `solve` definition behaves as solve | `c = func3(5)` in body is Neg | S |
| FN-SOLVE.4 | per-derived availability | Ex 297 | `comp1::func5` solve-only, `comp2::func5` target-only | call valid or not depending on the context component | S T |
| FN-SOLVE.5 | `solve pure` | 21.1.2, 21.14.6 | `format()`, mnemonic functions | | S |
| FN-SOLVE.6 | library solve functions | 21.14.2 | `set_handle` only in init execs | | S |

### FN-VIRT — component function inheritance (20.2, 17.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-VIRT.1 | instance override is polymorphic | 20.2, Ex 241 | `comp.foo()` runs the override of the context component's dynamic type | | T S |
| FN-VIRT.2 | same signature required | 20.2 | instance override must keep return type and params | differing signature is Neg; instance shadowed by a static is Neg | D |
| FN-VIRT.3 | `super.f(args)` | 20.2, 17.1 T27 | derived calls base implementation | `super.f` from a non-overriding function | T S |
| FN-VIRT.4 | base body calling `f()` | 20.2 (polymorphic) | a base function calling `f()` reaches the derived override (pssc ruling agrees) | | T S |
| FN-VIRT.5 | static shadowing | 20.2 | static fn shadowed with a different signature; static calls not polymorphic | | T S |
| FN-VIRT.6 | call through base `ref` | 7.10, 20.2 | `ref base_c r = derived_inst; r.f()` → derived | | T S |
| FN-VIRT.7 | qualifier on overrides | 20.2.3 c, 20.2.6 | `const`, defaults must match; non-pure cannot become pure | | D |

### FN-TT — target-template functions (20.6)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| FN-TT.1 | declaration | 20.6.1 | `target LANG [static] function void f(...) = """...""";` | non-void return is Neg; parameter directions Neg | T |
| FN-TT.2 | mustache params | 20.6, Ex 311 | `{{val}}` expands parameter values | in expression positions only | T |
| FN-TT.3 | instance TT functions | 20.6 | `{{this.attr}}` references component attributes | | T |
| FN-TT.4 | prototype match | 20.6.1 b | count, names, types, return | | D |
| FN-TT.5 | call restrictions | 20.1.2, 20.9 T29 | not from init execs or any solve exec | | T |

---

## 4. Procedural statements (LRM 20.7, 19, 20.1.4)

### ST-BLOCK — scoped blocks (20.7.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-BLOCK.1 | nested block scope | 20.7.1 | locals declared in `{}` vanish at `}` | | T S |
| ST-BLOCK.2 | `sequence { }` | 20.7.1.1 | optional `sequence` keyword | | T S |
| ST-BLOCK.3 | shadowing | 20.7.1, 20.7.8 e | inner local masks outer local/field; outer visible again after | assignment inside goes to the inner | T S |
| ST-BLOCK.4 | empty statement / empty block | B.7 stmt_terminator | `;` and `{}` as statements/bodies | | T S |
| ST-BLOCK.5 | redeclaration | 20.7.2 | same name twice in one scope is Neg | | T S |
| ST-BLOCK.6 | annotations in bodies | B.7 | `@ann` before a procedural statement is ignored if unrecognized | | T S |

### ST-VAR — local variables (20.7.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-VAR.1 | default initialization | 20.7.2, 7.x | each type's default (0, false, "", first enum item, smallest domain value, empty collections, struct defaults, null ref) | | T S |
| ST-VAR.2 | initializer | 20.7.2.1 | `= expression` (non-constant allowed) | | T S |
| ST-VAR.3 | multiple declarators | 20.7.2.1 | `int a = 1, b = a + 1;` | b reads a's new value (left-to-right) — not stated | T S |
| ST-VAR.4 | array dims | 20.7.2.1 | `int a[4] = {1,2,3,4};` | | T S |
| ST-VAR.5 | all Clause-7 types | 20.7.2 | struct, collections, ref, chandle, string, float, typedef, domain types | | T S |
| ST-VAR.6 | `rand` local | 20.7.2 | Neg | `static const` local also not in grammar (Neg) | — |
| ST-VAR.7 | declare anywhere | 20.7.2 | declaration after statements (C++ style) | reference before declaration is Neg | T S |
| ST-VAR.8 | initializer conversion | 8.7.2 | assignment-like width rules; aggregate literal / unqualified enum initializers | | T S |
| ST-VAR.9 | per-iteration re-init | 20.7.2 | a local with initializer inside a loop body is re-initialized each iteration | | T S |

### ST-ASSIGN — assignment statements (20.7.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-ASSIGN.1 | locals | 20.7.3 a | unrestricted in scope | | T S |
| ST-ASSIGN.2 | component attributes | 20.7.3 b/c | only from init execs or functions called from them | mutable fields excepted (DT-MUT) | S |
| ST-ASSIGN.3 | struct/action/object attributes via handle | 20.7.3 d | through a param in init/solve/body | | T S |
| ST-ASSIGN.4 | deep copy | 20.7.3 e | struct = struct, derived → base | | T S |
| ST-ASSIGN.5 | compound forms | 8.3 | EX-ASSIGN.2-6 as statements | | T S |
| ST-ASSIGN.6 | forbidden targets | 20.7.8 g, 7.1 | foreach iterator/index, iterated collection, const, const param | | — |
| ST-ASSIGN.7 | assignment to a scalar param | 20.3.2 | changes only the local copy | | T S |
| ST-ASSIGN.8 | action fields in exec | 20.7.13, Ex 318 | `body` assigning the action's own non-rand fields (`cs.curr_val`) feeds reactive activity | | T S |

### ST-VCALL — void call statement (20.7.4)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-VCALL.1 | void function statement | 20.7.4 | `f(x);` | | T S |
| ST-VCALL.2 | `(void)` discard | 20.7.4.1 | `(void)g();` | `(void)` on a void function — not stated (probably legal) | T S |
| ST-VCALL.3 | implicit discard | 20.7.4 | `g();` for non-void is legal | | T S |
| ST-VCALL.4 | method statements | 7.9 | `l.push_back(x);`, `(void)l.pop_front();`, `m.delete(k);` | | T S |
| ST-VCALL.5 | path calls | 20.2.1 d | `comp.sub.f();`, `regs.r.write_val(0);` | | T S |

### ST-RET — return (20.7.5)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-RET.1 | value return | 20.7.5 | `return expr;` converted assignment-like to the return type; expected type for enum items / literals | `return` of a wider value truncates | T S |
| ST-RET.2 | void return | 20.7.5 | `return;` in void functions | `return expr;` in void, `return;` in non-void are Neg | T S |
| ST-RET.3 | return in exec | 20.7.5 | `return;` ends the exec block — nothing after it runs | `return expr;` in an exec is Neg | T S |
| ST-RET.4 | return from nesting | 20.7.5 | return inside loop/match/block exits the whole function/exec | | T S |
| ST-RET.5 | missing return | 20.7.5 | "shall return a value" — fall-off-the-end behaviour unspecified | | T S |
| ST-RET.6 | aggregate return | 20.2.2 | returning a local array/struct copies it out | | T S |

### ST-REPEAT — repeat (count) (20.7.6, 11.4.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-REPEAT.1 | `repeat (N) stmt` | 20.7.6 | runs N times; N int/bit, non-negative | negative signed N is illegal | T S C(activity) |
| ST-REPEAT.2 | index variable | 20.7.6 b | `repeat (i : N)` i = 0..N-1 | index type unstated (int assumed); whether i is read-only unstated | T S C |
| ST-REPEAT.3 | zero count | 20.7.6 b | N == 0 → body never runs | | T S C |
| ST-REPEAT.4 | count evaluated once | 20.7.6 (implied) | changing the count variable inside the body does not change the iteration count | | T S |
| ST-REPEAT.5 | index scope | 20.7.8 e by analogy | index shadows an outer name, invisible after | | T S |
| ST-REPEAT.6 | count from call/field/expression | 20.7.6 | `repeat (l.size())`, `repeat (comp.n * 2)` | count wider than 32 bits | T S |
| ST-REPEAT.7 | unbraced body | 20.7.6.1 | single statement body | | T S |

### ST-RWHILE — repeat-while (20.7.7)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-RWHILE.1 | `repeat stmt while (c);` | 20.7.7 b | body runs at least once; condition sampled after | | T S C(activity) |
| ST-RWHILE.2 | bool condition | 20.7.7 a | | int condition is Neg | T S |
| ST-RWHILE.3 | `continue` | 20.7.11 | continue proceeds to the condition evaluation | | T S |
| ST-RWHILE.4 | condition with calls | 20.7.7 | call evaluated once per iteration | | T S |

### ST-WHILE — while (20.7.7)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-WHILE.1 | `while (c) stmt` | 20.7.7 b | condition sampled before body; may run zero times | | T S |
| ST-WHILE.2 | bool condition | 20.7.7 a | `while (n != 0)` (Ex 314) | `while (n)` is Neg | T S |
| ST-WHILE.3 | `continue` | 20.7.11 | re-evaluates the condition | | T S |
| ST-WHILE.4 | calls in condition | 20.7.7, Ex 319 | condition call (register poll) evaluated each iteration | awaited target call in a condition | T S |
| ST-WHILE.5 | `while (true)` + `break` | 20.7.11 | | | T S |
| ST-WHILE.6 | activity form | 11.4.2 | activities have only `repeat … while`, not `while` | — | — |

Note: 20.7.11 b lists only "repeat-count, repeat-while or foreach" as loops for
break/continue; `while` is part of `procedural_repeat_stmt` (Syntax 106) and
is presumed covered.

### ST-FOREACH — foreach (20.7.8, 11.4.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-FOREACH.1 | array | 20.7.8 | `foreach (e : a)`, `foreach (a[i])`, `foreach (e : a[i])` | index type int, ascending | T S C |
| ST-FOREACH.2 | list | 20.7.8 d1 | in order 0..size-1 | | T S C |
| ST-FOREACH.3 | map | 20.7.8 d2 | index = key (key type), iterator = value; order undetermined | results must be order-independent | T S C |
| ST-FOREACH.4 | set | 20.7.8 d3/f | iterator required | index on a set is Neg | T S C |
| ST-FOREACH.5 | at least one variable | 20.7.8 f | | `foreach (a)` is Neg | — |
| ST-FOREACH.6 | iterator is an alias, read-only | 20.7.8 c/g | iterator reads the element; writing iterator or index is Neg | | T S |
| ST-FOREACH.7 | no modification of iterated collection | 20.7.8 g | `push_back` / element write on the iterated collection is an error | writing a FIELD of an element (`a[i].f = v`) — "contents" ambiguous | T S |
| ST-FOREACH.8 | scope | 20.7.8 e | variables shadow outer names; invisible after | | T S |
| ST-FOREACH.9 | modeling-element arrays | 20.7.8 a | component arrays (not monitors) | Ex 347 init_down over `cpu_core[i]` | T S C |
| ST-FOREACH.10 | expression collections | 20.7.8 a, Ex 22 | `foreach (v : m.values())`, `foreach (c : s.chars())`, value-list literal | literal with mixed self-types is Neg | T S |
| ST-FOREACH.11 | multi-dim arrays | 7.9.2 | iterator over `int a[3][4]` is an `array<int,4>` | nested foreach over the row | T S |
| ST-FOREACH.12 | empty collection | 20.7.8 | zero iterations | | T S C |
| ST-FOREACH.13 | const collection | 7.9 c | iterator const | | T S |
| ST-FOREACH.14 | aggregate param | 20.3.2 | foreach over a parameter iterates the caller's collection | | T S |

### ST-IF — if/else (20.7.9)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-IF.1 | if / if-else | 20.7.9 | bool condition | int condition Neg | T S C(activity) |
| ST-IF.2 | else-if chain | 20.7.9 | `else if` is `else` + nested `if` | long chains; first true wins | T S |
| ST-IF.3 | dangling else | 20.7.9.1 | `else` binds to nearest `if` | unbraced nested if | T S |
| ST-IF.4 | unbraced bodies | 20.7.9.1 | single statements | declaration as an unbraced body (`if (c) int x = 1;`) — scope unstated | T S |
| ST-IF.5 | complex conditions | 8.5 | `&&`/`\|\|` short-circuit, `in`, calls | | T S |

### ST-MATCH — match (20.7.10, 11.4.6)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-MATCH.1 | value / list choices | 20.7.10 | `[1]:`, `[1, 3, 5]:` | | T S C(activity) |
| ST-MATCH.2 | range choices | 20.7.10, 8.5.9.1 | `[0..3]`, `[2..2]` (`open_range_value ::= expression [.. expression]`: no open bound; pssparser refuses `[..3]`, PSS026) | empty range (`[3..1]`) never matches | T S |
| ST-MATCH.3 | default | 20.7.10 e/f | at most one `default` | two defaults Neg | T S |
| ST-MATCH.4 | multiple matches (procedural) | 20.7.10 d | more than one matching choice is an ERROR | activity match instead picks one at random (11.4.6 d) | T S |
| ST-MATCH.5 | no match, no default | 20.7.10 g | error | | T S |
| ST-MATCH.6 | enum match | 7.5.2, Ex 28 | `[MODE_A, MODE_B]:`, item ranges, non-item cast value → default | unqualified items via match expression type | T S |
| ST-MATCH.7 | string match | 7.6.4, Ex 361 | `["reg0"]: return 0x0;` | | T S |
| ST-MATCH.8 | non-constant choices | 8.5.9.1 | `[lo..hi]` with variables | overlap only at some values → error only then | T S |
| ST-MATCH.9 | choice typing | 8.5.9.1, T22 | choices are context-determined against the match expression (as `in`) | `bit[2]` expression vs choice `[4]` | T S |
| ST-MATCH.10 | expression evaluated once | 20.7.10 a | side-effect call as match expression | | T S |
| ST-MATCH.11 | nested match / block bodies | 20.7.10.1 | | | T S |

### ST-BRK — break/continue (20.7.11)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-BRK.1 | break | 20.7.11 d | continue after the innermost loop | | T S |
| ST-BRK.2 | continue | 20.7.11 d | next iteration of innermost loop | | T S |
| ST-BRK.3 | placement | 20.7.11 b | only inside loops; may be nested in if/match | break outside a loop (incl. directly in a function) is Neg | T S |
| ST-BRK.4 | innermost only | 20.7.11 c | inner loop exit does not exit outer | | T S |
| ST-BRK.5 | break in match in loop | 20.7.11 b | exits the LOOP (match has no fall-through/break semantics) | | T S |
| ST-BRK.6 | continue per loop kind | 20.7.11 | repeat: next index; while: condition; repeat-while: condition; foreach: next element | | T S |

### ST-RAND — randomize statement (20.7.12, 13.4.6)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-RAND.1 | scalar target | 13.4.6 | `randomize x;` | | S, T (scalar ints only) |
| ST-RAND.2 | multiple targets + with | 13.4.6, Ex 177 | `randomize v1, v2 with { v1.f1.a < v2; }` solved together | | S |
| ST-RAND.3 | non-rand targets become random | 13.4.6 | targets random regardless of `rand` | | S T |
| ST-RAND.4 | struct target | 13.4.6 | rand sub-fields random, non-rand sub-fields invariants, type constraints applied | Ex 177 `v1.f1.a` in [0..14] | S |
| ST-RAND.5 | exec blocks during randomize | 13.4.6.1, Ex 178 | struct pre_solve/post_solve run (Pre S2, Pre S1, Pre S1, Post S2, Post S1, Post S1) | | S |
| ST-RAND.6 | target restriction | 13.4.6.1 | target platform: only scalar integers; struct randomization directly or indirectly from target is Neg | | T |
| ST-RAND.7 | unsatisfiable | 13.4 | error | | S T |
| ST-RAND.8 | random stability | 13.4.6.2 | same seed + same model → same values | | S T |
| ST-RAND.9 | hierarchical targets | 20.7.12.1 | `randomize this.f, s.a;` | | S |
| ST-RAND.10 | domain types | 7.2.1 d | domain-typed local respects domain | | S T |
| ST-RAND.11 | non-randomizable targets | 7.1 | float/chandle/map/set targets Neg | | — |
| ST-RAND.12 | list targets | 7.9.3.4 | elements random; size fixed | | S |

### ST-CIF — compile if in procedural code (19)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-CIF.1 | `compile if (c) {…} else {…}` | 19.2.2 procedural_compile_if | in exec and function bodies | not in target-template exec bodies (19.2.1 footnote) | T S |
| ST-CIF.2 | braces required | 19.2.2 NOTE | unbraced branch is deprecated | | T S |
| ST-CIF.3 | condition scope | 19.1.3 | only package-scope types/constants | type-scope member (`t::A`) is Neg | D |
| ST-CIF.4 | `compile has(path)` | 19.3 | true only if declared earlier (or in an enabled branch) | forward-declared item → false | D |
| ST-CIF.5 | disabled branch | 19.1.1 | only needs to parse: semantic errors inside are ignored | | T S |
| ST-CIF.6 | no new scope | 19.1.1 | locals declared in an enabled branch are visible after it | | T S |
| ST-CIF.7 | compile assert | 19.4 | not a procedural statement (struct/package/component body item) | `compile assert` in an exec is Neg | D |

### ST-YIELD — yield (20.7.14, 20.8)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-YIELD.1 | target-only | 20.7.14 a | `yield;` in target execs/functions | in solve execs/functions Neg | T |
| ST-YIELD.2 | no-op when alone | 20.7.14 b | no parallel exec → no effect | | T |
| ST-YIELD.3 | cooperative switch | 20.7.14 c | another exec on the same executor runs before continuation | | T |
| ST-YIELD.4 | polling loop | 20.8, Ex 319 | `while (reg.read_val() == 0) yield;` alongside a blocked `get()` | | T |
| ST-YIELD.5 | yield in called function | 20.7.14 a | yield inside a target function called from body | | T |

### ST-SUPER — `super;` in exec blocks (20.1.4)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| ST-SUPER.1 | prepend / append | 20.1.4.2, Ex 284 | `super;` first or last runs base same-kind block there | | T S |
| ST-SUPER.2 | exec shadowing | 20.1.4.1, Ex 283 | a derived exec completely replaces the base's without `super;` | | T S |
| ST-SUPER.3 | multi-level | 17.1 | A2 : A1 : A, each with `super;` | | T S |
| ST-SUPER.4 | base with several blocks | 20.1.4.2 | `super;` runs all base blocks of that kind | | T S |
| ST-SUPER.5 | target-template | 20.1.4.2 | `super;` in a target-template exec is Neg | | — |
| ST-SUPER.6 | nesting | 20.1.1 exec_super_stmt | grammar allows `super;` only at exec top level | `if (c) super;` is Neg per grammar (likely LRM defect) | T S |
| ST-SUPER.7 | `return` inside the base part | 20.7.5 | does a base `return;` end only the base portion? unstated | | T S |
| ST-SUPER.8 | in functions | 20.1.1 | `super;` in a function body is Neg (use `super.f()`) | | — |

---

## 5. Exec blocks (LRM 20.1, 20.5)

### EXEC-KIND — exec kinds and ordering

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EXEC-KIND.1 | init_down / init_up order | 20.1.2, Ex 280/281 | parent init_down before children; parent init_up after all children's | sibling order undefined; parent init_up overwrites child's init_up value | S |
| EXEC-KIND.2 | pre_solve / post_solve | 20.1.2, 13.4.12 | before/after solving in actions, objects, structs | nested struct fields' blocks run too | S |
| EXEC-KIND.3 | pre_body | 20.1.2 | after post_solve and executor/memory assignment, before body; solve functions, `executor()`, `addr_value_solve` | | S |
| EXEC-KIND.4 | body | 20.1.2 | actions only; target platform | body in a struct/component is Neg | T |
| EXEC-KIND.5 | run_start / run_end | 20.1.5.1 | per executor: execution unit, executor, components (pre-order / post-order), actions | | T |
| EXEC-KIND.6 | header / declaration | 20.1.2 | target-template only | procedural header is Neg | T |
| EXEC-KIND.7 | merging | 20.1.1 d | several blocks of a kind in one scope = one block in source order | each block declaring a local `x` — scope unstated | T S |
| EXEC-KIND.8 | validity per type | 20.1.2 | init_* in components; pre/post_solve/pre_body in actions, objects, structs | pre_solve in a component is Neg | D |
| EXEC-KIND.9 | extension | 20.1.4.3, Ex 285/286 | extension blocks run after the definition's, in processing order | extensions of a derived action with `super;` | T S |
| EXEC-KIND.10 | atomicity | 20.1.1 e | solve execs atomic w.r.t. shared component data | | S |
| EXEC-KIND.11 | component immutability | 9.1.6 | component fields frozen after init_up (except mutable) | | S |
| EXEC-KIND.12 | reactive values | Ex 289 | body assigns an action field read by the activity (`cs.curr_val`) | | T |
| EXEC-KIND.13 | executor assignment | 21.7.2.6 | `set_executor` in init; inherited from parent; error if none for component target execs | **LIB-EXEC** | S |

### EXEC-TT — target-template exec blocks (20.5, 4.7.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| EXEC-TT.1 | `exec body C = """…""";` | 20.5 | text inserted verbatim (quoted string) or expanded (TQS) | | T |
| EXEC-TT.2 | mustache references | 20.5.3, Ex 304/305 | `{{a}}`, `{{s1.b}}`, `{{a+s1.b}}` | cannot assign a PSS variable | T |
| EXEC-TT.3 | directives | Ex 306 | foreach over arrays in a declaration block | | T |
| EXEC-TT.4 | concatenation | 20.5 | several TT blocks of a kind concatenate | | T |
| EXEC-TT.5 | `exec file` | 20.5.2, Ex 303 | filename with mustache; same-file contributions concatenated in flow order | | T |
| EXEC-TT.6 | exec tags | 20.5.4 | dedup of tagged blocks; different text under the same tag is an error | untagged never match | T |
| EXEC-TT.7 | restrictions | 20.5 | not for solve execs; no `super;`; shadowed per language in derived types | | — |

---

## 6. Built-in procedural library (LRM 21)

### LIB-MSG — formatting and messages (21.1)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-MSG.1 | `print(fmt, ...)` | 21.1.2 | solve-only output | from body Neg | S |
| LIB-MSG.2 | `format(fmt, ...)` | 21.1.2 | `solve pure` returning string | from body Neg | S |
| LIB-MSG.3 | `message(vrb, fmt, ...)` | 21.1.3 | both platforms; trailing newline added; filtered by run verbosity (NONE always) | side effects in arguments not guaranteed | T S |
| LIB-MSG.4 | conversions | 21.1.1 | `%d %u %x %X %o %b %B %f %e %E %g %G %n %s %p %%` | `%x` of a negative int prints at its self-determined width | T S |
| LIB-MSG.5 | flags/width/precision | 21.1.1 | `- + space # 0`, width, `.prec` | `%.0d` of 0 prints nothing; `%.` = precision 0; `%5.2s` truncates | T S |
| LIB-MSG.6 | errors | 21.1.1 a-c | bad `%`, specifier/argument count mismatch, type mismatch | unsigned formats for float are Neg | T S |
| LIB-MSG.7 | implicit conversions | 21.1.1 c | `%d` on unsigned → signed; `%f` on int; `%d` on float → int | `%d` of `bit[32] 0xFFFFFFFF` prints -1 | T S |
| LIB-MSG.8 | `%n` | 21.1.1 | enum item name / bool name | | T S |
| LIB-MSG.9 | varargs self-determined | T23, Ex 42 | argument expressions evaluated at own width | | T S |

### LIB-FILE — file operations (21.2)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-FILE.1 | open/close | 21.2 | `file_open(name, TRUNCATE\|APPEND\|READ)`; `nullfilehandle` on failure | use after close is an error | S |
| LIB-FILE.2 | `file_exists` | 21.2 | bool | | S |
| LIB-FILE.3 | `file_write` | 21.2 | formatted, no newline added; requires TRUNCATE/APPEND handle | write to READ handle errors | S |
| LIB-FILE.4 | `file_read(h, size=-1)` | 21.2 | sequential reads; negative size reads to end | | S |
| LIB-FILE.5 | `file_write_lines` / `file_read_lines` | 21.2 | newline per line; APPEND adds a newline to existing content if missing | | S |
| LIB-FILE.6 | solve-only | 21.2 | any file call from target code is Neg | | S |

### LIB-ERR — error reporting (21.3)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-ERR.1 | `error(fmt, ...)` | 21.3 | logs; may or may not stop | | T S |
| LIB-ERR.2 | `fatal(status, fmt, ...)` | 21.3 | terminates at nearest point; status returned to environment | statements after fatal not executed | T S |
| LIB-ERR.3 | solve-time-known strings | 21.3 | format and string args must be solve-time known on target | | T |

### LIB-RAND — urandom (21.4)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-RAND.1 | `urandom()` | 21.4.1 | bit[32] | | T S |
| LIB-RAND.2 | `urandom_range(min, max)` | 21.4.2 | bit[32] between min and max | inclusivity not stated; min > max unspecified; negative args converted to bit[32] | T S |
| LIB-RAND.3 | target randomization | 13.4.6.1 | the only randomization on target besides scalar `randomize` | | T |
| LIB-RAND.4 | stability | 13.4.6.2 | reproducible with fixed seed | | T S |

### LIB-FP — floating-point library (21.5)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-FP.1 | math functions | 21.5.2 T31 | log, log10, exp, sqrt, pow, round, floor, ceil, sin, cos, tan, asin, acos, atan, atan2, hypot, sinh, cosh, tanh, asinh, acosh, atanh — match C | domain errors (`sqrt(-1)`, `log(0)`) follow C (NaN/inf) | T S |
| LIB-FP.2 | not in constraints | 21.5.2 | Neg in `C` | | — |
| LIB-FP.3 | field extraction | 21.5.3 | `float_mantissa`, `float_exponent`, `float_sign`, `to_float` | round-trip of 20.25 (Ex 326) | T S |
| LIB-FP.4 | storage types | 21.5.1 | `float_base_s<Wm,We,E>`, `float32_s`, `float64_s`, bfloat16 typedef | arithmetic on a storage type is Neg | T S |

### LIB-MEM — memory access (21.13.9)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-MEM.1 | `read8/16/32/64` | 21.13.9.1 | first byte → bits [7:0] (little-endian assembly) | | T |
| LIB-MEM.2 | `write8/16/32/64` | 21.13.9.2 | data bits [7:0] at the start address | data wider than the primitive is truncated (assignment-like) | T |
| LIB-MEM.3 | `read_bytes(h, list, size)` | 21.13.9.3 | list resized to `size`, previous contents overwritten | size 0 | T |
| LIB-MEM.4 | `write_bytes(h, list)` | 21.13.9.3 | count = list size | empty list | T |
| LIB-MEM.5 | `read_struct`/`write_struct` | 21.13.9.4 | packed struct by layout; 8/16/32/64-bit at aligned address = one primitive | non-packed struct is Neg | T |
| LIB-MEM.6 | `mem_access_desc_s` arg | 21.13.9, Ex 349/350 | default `{}`; extended by users; passed through to primitives | | T |
| LIB-MEM.7 | executor delegation | 21.13.9.5 | primitives and `addr_value` delegated to the executor's override; struct/register accesses follow | override calls `addr_reg_pkg::write64` / `super.addr_value` (Ex 355) | T |
| LIB-MEM.8 | target-only | 21.13.9 | memory calls from solve execs Neg | | T |

### LIB-REG — register access (21.14)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-REG.1 | `read()` / `write(R)` | 21.14.1 | register-value-type access | for `bit[N]` registers equivalent to `read_val`/`write_val` | T |
| LIB-REG.2 | `read_val()` / `write_val(v)` | 21.14.1 | bit[SZ] access | reserved bits undefined | T |
| LIB-REG.3 | `write_masked(mask, val)` | 21.14.1, Ex 359 | struct-typed mask/val; new = (cur & ~mask) \| (val & mask) | `{.mode=~0}` in a struct literal = all ones at field width; unspecified mask fields 0 | T |
| LIB-REG.4 | `write_val_masked(mask, val)` | 21.14.1 | numeric RMW | | T |
| LIB-REG.5 | `write_field(name, v)` / `write_fields(names, vals)` | 21.14.1 | struct registers only; string literals; top-level, non-aggregate fields; unique names | on a `bit[N]` register Neg; dotted or array field Neg; duplicate name Neg | T |
| LIB-REG.6 | access-kind errors | 21.14.1 | read/RMW of WRITEONLY, write/RMW of READONLY are errors | | T |
| LIB-REG.7 | offsets | 21.14.2, 21.14.5 | `get_offset_of_instance[_array]` or `get_offset_of_path` (not all three); offset = sum over hierarchy | providing all three is Neg; offset functions are pure | S T |
| LIB-REG.8 | `set_handle` | 21.14.3 | top-level group only, in init execs | on a sub-group Neg | S |
| LIB-REG.9 | `get_handle()` | 21.14.4, Ex 363 | handle arithmetic over a group | | T |
| LIB-REG.10 | translation to primitives | 21.14.5 | 32-bit register → `read32`/`write32` at `make_handle_from_handle(h, offset)` | | T |
| LIB-REG.11 | symbolic names | 21.14.6 | `get_mnemonic_*`, `set_mnemonic`, `use_symbolic_reg_names` | path vs instance exclusivity; mode selection T35 | S T |
| LIB-REG.12 | register arrays | Ex 361 | `reg1[i]` offset via `get_offset_of_instance_array(name, i)` | run-time index | T |

### LIB-CHAN — channels (21.9)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-CHAN.1 | `channel_c<T, DEPTH=1>` | 21.9.1 | FIFO of up to DEPTH items, copy by value | DEPTH ≤ 0 Neg; T must be numeric, bool, enum with base, packed struct, arrays thereof | D |
| LIB-CHAN.2 | `get()` | 21.9.1.1 | blocks until data; returns oldest | multiple waiters: one receives each item | T |
| LIB-CHAN.3 | `put(t)` | 21.9.1.2 | blocks until space | | T |
| LIB-CHAN.4 | `try_get(output T)` | 21.9.1.3 | returns false when empty; else true and element via output param | output into a local / field / element | T |
| LIB-CHAN.5 | `try_put(t)` | 21.9.1.4 | false when full | | T |
| LIB-CHAN.6 | target-only | 21.9.1 | calls from solve execs are Neg | | T |
| LIB-CHAN.7 | concurrency | 20.8 | blocked get lets other execs on the executor run | | T |

### LIB-EXEC **NEW** — executors (21.7.2.5-6)

| ID | Construct | LRM | Rule to exercise | Edge cases | Ctx |
|----|-----------|-----|------------------|------------|-----|
| LIB-EXEC.1 | `executor()` | 21.7.2.5 | ref to the current executor | null in `pre_solve`; null when unassigned | S T |
| LIB-EXEC.2 | delegation idiom | Ex 333 | `if (executor() != null) executor().f(); else …` | | T |
| LIB-EXEC.3 | `set_executor(xtr)` | 21.7.2.6 | in init execs; last call wins; inherited from parent | executor assigned another executor is Neg | S |
| LIB-EXEC.4 | `get_context()` | Ex 300 | chandle context for export callbacks | | T |

---

## 7. LRM inconsistencies and gaps worth pinning down

These are places where a test must decide what to expect, or where the LRM
contradicts itself. Each is a candidate for a ruling.

1. **Compound assignment set.** `assign_op` (8.1) and Table 9 define only
   `= += -= <<= >>= |= &=`. `*= /= %= ^=` are not PSS (EX-ASSIGN.5).
2. **Unary `+`.** Table 22 lists unary `+`; the grammar's `unary_operator`
   does not (EX-ARITH.9).
3. **`**` associativity.** T11 makes `**` left-associative (EX-PREC.2), unlike
   most languages.
4. **Shift by ≥ width.** Not stated; follows from "vacated positions filled"
   (EX-SHIFT.4). C's undefined behaviour must not leak into C targets.
5. **Enum ranges** (`[A..C]`) — value order or declaration order is not stated
   (DT-ENUM.9).
6. **Enum relational operators** — only assignment and equality are granted
   (DT-ENUM.11).
7. **Duplicate enum values** are an error (7.5.1 e), while pssc code allows
   for them (DT-ENUM.4).
8. **Repeat index** — type and read-only-ness unstated (ST-REPEAT.2).
9. **Argument / operand evaluation order** — unstated except `in` (explicitly
   nondeterministic) and short-circuit operators (FN-CALL.2).
10. **Compound-assignment LHS evaluation count** — unstated (EX-ASSIGN.6).
11. **Missing return** in a non-void function — unstated (ST-RET.5).
12. **`break`/`continue` in `while`** — 20.7.11 b names only repeat-count,
    repeat-while and foreach (ST-WHILE note).
13. **foreach "contents" modification** — whether writing a field of an element
    counts (ST-FOREACH.7).
14. **`super;` nesting** — grammar allows top level only; a nested `super;` is
    likely an LRM defect (ST-SUPER.6).
15. **Procedural vs activity `match`** — multiple matches is an error in
    procedural code but a random choice in activities (ST-MATCH.4).
16. **Export syntax** — Ex 300 omits `target` (FN-EXPORT.1).
17. **`float` default value** — not stated (DT-FP.1).
18. **Multiple exec blocks of one kind** — "single exec block" (20.1.1 d):
    are their locals in one scope? (EXEC-KIND.7).
19. **`urandom_range` inclusivity** and min > max (LIB-RAND.2).
20. **Short-circuit wording** — 8.5.4 says "in procedural contexts", 8.4.4 says
    always.

---

## 8. Interaction patterns

Combinations whose meaning is not the sum of the parts. Each names the
constructs combined and the LRM basis. A single test may cover several.

| ID | Pattern | Constructs | LRM basis |
|----|---------|------------|-----------|
| IP-1 | `break` inside `match` inside `foreach` exits the loop, not just the match | ST-BRK, ST-MATCH, ST-FOREACH | 20.7.11 b/c |
| IP-2 | `continue` in an inner `foreach` nested in an outer `repeat … while`: only the inner loop advances; outer condition evaluated after the inner finishes | ST-BRK, ST-FOREACH, ST-RWHILE | 20.7.11 c, 20.7.7 b |
| IP-3 | `continue` in `repeat … while` jumps to the condition: a false condition ends the loop without re-running the body | ST-BRK, ST-RWHILE | 20.7.11 d, 20.7.7 b |
| IP-4 | `return` from inside a loop inside a `match` inside a function: value converted to return type, no further iterations | ST-RET, ST-MATCH, ST-REPEAT, EX-WIDTH | 20.7.5, 8.7.2 |
| IP-5 | `return;` inside a `foreach` in an exec body ends the whole exec (statements after the loop skipped) | ST-RET, ST-FOREACH, EXEC-KIND | 20.7.5 |
| IP-6 | compound assignment to a part-select of a struct field of an array element: `a[i].f[7:4] += 1` wraps at 4 bits and leaves other bits | EX-ASSIGN, EX-SEL, EX-IDX, DT-STRUCT | 8.3, 8.6.1, 8.7.2 |
| IP-7 | compound assignment to a map element with a missing key reads it (illegal) while plain `=` inserts | EX-ASSIGN, DT-MAP | 8.3, 8.6.2 |
| IP-8 | function call in a `while` condition: called iterations+1 times; with a side effect the count is observable | ST-WHILE, FN-CALL | 20.7.7 b |
| IP-9 | function call as `repeat` count: evaluated once, side effect once | ST-REPEAT, FN-CALL | 20.7.6 |
| IP-10 | recursion with struct parameters: every level aliases the caller's struct, so writes accumulate across frames, while locals are per frame | FN-CALL, FN-PARAM, DT-STRUCT | 20.2.7, 20.3.2 |
| IP-11 | recursion with scalar params and a local accumulator (factorial, fib) | FN-CALL, FN-PARAM, ST-VAR | 20.2.7, 20.3.2 |
| IP-12 | `match` on an enum with item lists, item ranges and `default`; an int cast to the enum that is no item reaches `default` | ST-MATCH, DT-ENUM, DT-CAST | 20.7.10, 7.5, Ex 28 |
| IP-13 | `match` on a string produced by a sub-string or method call, with string-literal choices and `default` | ST-MATCH, DT-STR, EX-SUBSTR | 20.7.10, 7.6.4 |
| IP-14 | nested struct aggregate literal assigned to an array element: `arr[2] = {.a=1, .in={.x=2}}` — unspecified fields reset to defaults | EX-LIT, DT-STRUCT, DT-ARR, EX-IDX | 4.8.4-5, 8.4.2 |
| IP-15 | struct literal assigned to an existing struct is a full overwrite, not a partial update | EX-LIT, EX-ASSIGN | 4.8.4, 8.4.2 |
| IP-16 | call argument width: `f(x + y)` with `bit[16]` formal and `bit[8]` x,y does not wrap; the same expression as a `message` vararg wraps | FN-PARAM, EX-WIDTH, LIB-MSG | 8.7.2 T23 |
| IP-17 | return width: `return x + y;` in a `bit[16]` function with `bit[8]` operands does not wrap | ST-RET, EX-WIDTH | 8.7.2 T23 |
| IP-18 | compound-assign width: `bit[16] z; bit[8] x = 0xF0; z += x << 4;` is `z + 0xF00` (16 bits propagated into the shift) | EX-ASSIGN, EX-SHIFT, EX-WIDTH | 8.3, 8.7.2, T22 |
| IP-19 | narrowing assignment: `bit[8] z = a16 + b16;` evaluated at 16 bits then truncated | EX-ASSIGN, EX-WIDTH | 8.7.2 |
| IP-20 | extension follows source signedness in assignment-like contexts: `int z = (bit[4])15` → 15, `bit[32] z = (int[4])-1` → 0xFFFFFFFF | EX-WIDTH, DT-CAST | 8.7.2 |
| IP-21 | mixed signed/unsigned relational in an `if` (`bit[32] 1 < int -10` true) inside a loop that counts down a signed index | EX-REL, ST-IF, ST-REPEAT | 8.5.2, Ex 43 |
| IP-22 | local in a nested block shadows a component/action field; after the block the field is visible and unchanged | ST-BLOCK, EX-REF | 20.7.1, 20.7.8 e |
| IP-23 | parameter named like a field in an instance function; assignment updates the parameter only | FN-PARAM, EX-REF | 20.3.2 |
| IP-24 | foreach index named like an outer local: outer value unchanged after the loop | ST-FOREACH, ST-BLOCK | 20.7.8 e |
| IP-25 | nested `repeat (i : …)` both using `i`: inner shadows outer; outer iteration count unaffected | ST-REPEAT, ST-BLOCK | 20.7.6, 20.7.8 e |
| IP-26 | foreach over a list of structs writing `l[i].f`: is it "changing the contents of the iterated collection"? | ST-FOREACH, EX-IDX | 20.7.8 g |
| IP-27 | foreach over an aggregate parameter iterates the caller's collection (handle passing) | ST-FOREACH, FN-PARAM | 20.3.2 |
| IP-28 | `&&` guard against out-of-bounds index inside a `while` condition: `i < l.size() && l[i] != 0` | EX-LOG, EX-IDX, ST-WHILE | 8.4.4, 8.6.2 |
| IP-29 | short-circuit with a side-effecting call on the RHS of `\|\|` in an `if` within a loop: count calls | EX-LOG, FN-CALL, ST-IF | 8.4.4 |
| IP-30 | `?:` with calls in both branches: only the chosen one runs | EX-COND, FN-CALL | 8.5.8 |
| IP-31 | `?:` returning struct values (or struct literals typed by expected type) assigned to a struct | EX-COND, DT-STRUCT, EX-LIT | 8.5.8, 8.4.3 |
| IP-32 | `?:` with unqualified enum items in both branches assigned to an enum variable | EX-COND, DT-ENUM | 8.4.3 |
| IP-33 | unqualified enum item as an argument: legal for an enum formal, Neg for an int formal (same name in two enums) | FN-PARAM, DT-ENUM | 8.4.3, Ex 37 |
| IP-34 | `in` against a function-returned collection and against `m.keys()` | EX-IN, FN-CALL, DT-MAP | 8.5.9, 7.9.4 |
| IP-35 | `in` with variable range bounds that become empty (lo > hi) on some loop iterations | EX-IN, ST-REPEAT | 8.5.9.1 |
| IP-36 | `match` with variable ranges that overlap only on some iterations: error only on those | ST-MATCH, ST-REPEAT | 20.7.10 d |
| IP-37 | callee pushes onto a list parameter while the caller loops over a different list: caller sees the growth after the call | FN-PARAM, DT-LIST, ST-FOREACH | 20.3.2 |
| IP-38 | `swap_structs(a, b)` via struct assignment in the callee swaps the caller's structs | FN-PARAM, EX-ASSIGN, DT-STRUCT | 20.3.2, Ex 291 |
| IP-39 | derived struct passed to a base formal: callee `p = q` copies base fields only; derived fields untouched | FN-PARAM, DT-STRUCT | 20.3.2, 8.3 |
| IP-40 | derived→base struct assignment then `==` against a base struct literal | EX-ASSIGN, EX-EQ, EX-LIT | 8.3, 8.5.3 |
| IP-41 | array/list assignment then modify the source: copy unchanged; `push_back(v)` then modify element: `v` unchanged | DT-ARR, DT-LIST, EX-ASSIGN | 7.9, Ex 13 |
| IP-42 | assigning one row of a 2-D array from another (`a[1] = b[0]`) | DT-ARR, EX-IDX, EX-ASSIGN | 7.9.2, 8.3 |
| IP-43 | `map<string, list<int>>`: `m["k"].push_back(1)` on a missing key is illegal; after `m["k"] = {}` it works | DT-MAP, DT-LIST, EX-IDX | 8.6.2 |
| IP-44 | two modifying method calls in one expression (`l.pop_back() - l.pop_front()`) — evaluation order unspecified | EX-METH, DT-LIST | 8.4.4 (no ordering rule) |
| IP-45 | building a value bit by bit: `repeat (i : 8) x[i] = (y >> i) & 1;` (bit-select lvalue with loop index) | EX-SEL, ST-REPEAT, EX-SHIFT | 8.6.1 |
| IP-46 | part-select assignments to a register-value struct's fields in a loop, then `write()` | EX-SEL, DT-REG, LIB-REG | 8.6.1, 21.14.1 |
| IP-47 | `write_masked({.f=~0}, {.f=v})`: `~0` in a struct-literal item takes the field's width | LIB-REG, EX-LIT, EX-WIDTH | 21.14.1, 8.7.2 |
| IP-48 | `write_val(~x)` with `bit x = 1` on a 32-bit register is 0xFFFFFFFE (target width propagated before `~`) | LIB-REG, EX-BIT, EX-WIDTH | 8.7.2, T22 |
| IP-49 | register read in a `while` condition with `yield` in the body, alongside a parallel action blocked on `get()` | ST-WHILE, ST-YIELD, LIB-REG, LIB-CHAN | 20.8, Ex 319 |
| IP-50 | register array element with a run-time loop index: `regs.r[i].write_val(i)` → offset via `get_offset_of_instance_array` | LIB-REG, ST-REPEAT, DT-REG | 21.14.5 |
| IP-51 | `write_struct`/`read_struct` of a packed struct with bool and based-enum fields, both endiannesses, inside a loop of handles | LIB-MEM, DT-PACK | 21.13.1, 21.13.9.4 |
| IP-52 | `write32(make_handle_from_handle(h, 4*i), v)` in `repeat (i : n)` | LIB-MEM, DT-ADDR, ST-REPEAT | 21.13.4.2, Ex 363 |
| IP-53 | executor overriding `write64` in terms of `write32` (Ex 354), reached from a function called from `body` | LIB-MEM, LIB-EXEC, FN-VIRT | 21.13.9.5 |
| IP-54 | `try_get` in a loop writing through its output parameter into a struct field / array element | LIB-CHAN, EX-ASSIGN, ST-WHILE | 21.9.1.3 |
| IP-55 | channel with DEPTH > 1: several `put`s then `get`s preserve FIFO order; `try_put` false when full | LIB-CHAN | 21.9.1 |
| IP-56 | `randomize v with { v > i; }` inside a loop using the loop index | ST-RAND, ST-REPEAT | 13.4.6 |
| IP-57 | `randomize` of a struct local after assigning its non-rand fields (invariants), with struct exec blocks firing in order | ST-RAND, DT-STRUCT, EXEC-KIND | 13.4.6, Ex 177/178 |
| IP-58 | `randomize` of a domain-typed local (`int in [1..5] x`): domain is an implicit constraint | ST-RAND, DT-INT | 7.2.1 d |
| IP-59 | procedural `compile if` choosing statements by a package static const; a local declared in the enabled branch is used after it | ST-CIF, ST-VAR, DT-CONST | 19.1.1, 19.2 |
| IP-60 | `compile has` on an item declared later in the source (false) inside a procedural compile if | ST-CIF | 19.1.2, 19.3 |
| IP-61 | `super;` placement (first/last) plus an extension's exec block of the same kind on the derived action | ST-SUPER, EXEC-KIND | 20.1.4.2-3, Ex 285 |
| IP-62 | base function calling a virtual `f()` inside a loop reaches the derived override; the override calls `super.f()` | FN-VIRT, ST-REPEAT | 20.2, 17.1 |
| IP-63 | call through a `ref base_c` holding a derived instance dispatches to the derived override | FN-VIRT, DT-REF | 20.2, 7.10 |
| IP-64 | static function shadowed in a derived component with a different signature; base-qualified call is not polymorphic | FN-VIRT, FN-CALL | 20.2 |
| IP-65 | const aggregate param forwarded to a nested call: legal to a const formal, Neg to a non-const one | FN-PARAM, DT-CONST | 20.2.3 |
| IP-66 | default parameter values interacting with expected type: an enum-item default, a struct `{}` default (`mem_access_desc_s = {}`) | FN-PARAM, EX-LIT, DT-ENUM | 20.2.4, 21.13.9 |
| IP-67 | varargs formatting of mixed widths/signedness: `%d` of `bit[32] 0xFFFFFFFF` (-1), `%x` of `int -1`, `%u` of a negative `int[8]` | LIB-MSG, EX-WIDTH | 21.1.1 c, T23 |
| IP-68 | `message` with a side-effecting call argument inside a loop: count not guaranteed under verbosity filtering | LIB-MSG, FN-CALL | 21.1.3 |
| IP-69 | chained string methods in a condition and `split` + `foreach` + `join` round trip | DT-STR, EX-METH, ST-FOREACH | 7.6.3 |
| IP-70 | sub-string with loop-index bounds reaching `size()-1` exactly, and one past it (error) | EX-SUBSTR, ST-REPEAT | 8.6.3 |
| IP-71 | string "concatenation" with `+` is Neg; the legal forms are `format()`/`join`/TQS | DT-STR, EX-ARITH, EX-TQS | 8.4 T10 |
| IP-72 | foreach over a map accumulating keys into a set and values into a sum: result independent of iteration order | ST-FOREACH, DT-MAP, DT-SET | 20.7.8 d2 |
| IP-73 | set `insert` of duplicates inside a loop, then `size()` | DT-SET, ST-REPEAT | 7.9.5.2 |
| IP-74 | aggregate-returning calls used directly: `f().size()`, `g()[2]`, `h().field` | FN-CALL, EX-REF, EX-IDX | B.18 |
| IP-75 | `**` with negative exponents inside arithmetic (Table 13 cases) and `2 ** 31` overflow in `int` | EX-ARITH, EX-WIDTH | 8.5.1 T13 |
| IP-76 | signed division and modulus of negatives in a loop: truncation toward zero, sign of `%` | EX-ARITH, ST-REPEAT | 8.5.1 |
| IP-77 | shift by a loop index that reaches ≥ the width (`repeat (i : 40) y = 1 << i;` with `bit[32]` and `bit[64]` y) | EX-SHIFT, EX-WIDTH, ST-REPEAT | 8.5.7, 8.7.2 |
| IP-78 | accumulating in `int` past 2**31 wraps; the same in `bit[64]` does not | EX-ARITH, EX-WIDTH, ST-REPEAT | 8.5.1, 8.7 |
| IP-79 | unary minus of an unsigned operand in a relational: `-x < 0` with `bit[8] x` is false | EX-ARITH, EX-REL | T22, 8.5.2 |
| IP-80 | nested `?:` with branches of different widths, in a wider assignment target | EX-COND, EX-WIDTH | T22, 8.7.2 |
| IP-81 | cast inside arithmetic: `(bit[4])a + b` casts `a` only, and the casted value is then widened by context | DT-CAST, EX-PREC, EX-WIDTH | T11, 8.7 |
| IP-82 | `(bool)x` as a condition vs `if (x)` (Neg) | DT-CAST, DT-BOOL, ST-IF | 7.12.1 b, 20.7.9 |
| IP-83 | `return` inside a `repeat … while` inside a function that is itself called in another loop's condition | ST-RET, ST-RWHILE, ST-WHILE, FN-CALL | 20.7.5, 20.7.7 |
| IP-84 | `break` out of a loop whose body declares block locals; the locals are gone after the loop (re-declaring the name is legal) | ST-BRK, ST-BLOCK, ST-VAR | 20.7.1, 20.7.11 |
| IP-85 | instance function writing component fields: legal from `init_down`, Neg when called from `body` | FN-NATIVE, DT-MUT, EXEC-KIND | 20.7.3 b |
| IP-86 | `mutable` component field updated from `post_solve` through a `ref` and read in a later traversal | DT-MUT, DT-REF, EXEC-KIND | 9.1.6, Ex 55 |
| IP-87 | two `exec pre_solve` blocks in one action plus one from `extend action`: source order, then extension | EXEC-KIND | 20.1.1 d, 20.1.4.3 |
| IP-88 | parent `init_up` overwrites the value a child set in its own `init_up` (Ex 280); parent `init_down` value overwritten by child `init_down` | EXEC-KIND, EX-REF | 20.1.2, Ex 280 |
| IP-89 | foreach over a component array in `init_down` setting each element's field from the index (Ex 347) | ST-FOREACH, EX-REF, EXEC-KIND | 20.7.8 a, 9.1.4 |
| IP-90 | `yield` inside a loop of a target function called by two parallel bodies on one executor | ST-YIELD, FN-CALL, ST-WHILE | 20.7.14, 20.8 |
| IP-91 | struct `==` against a partial struct literal (unspecified fields compared to defaults) inside an `if` | EX-EQ, EX-LIT, ST-IF | 8.4.2, Ex 33 |
| IP-92 | list `==` literal (order matters) vs set `==` literal with duplicates (order-free, dedup) | EX-EQ, DT-LIST, DT-SET | 8.5.3, Ex 34 |
| IP-93 | value-list literal elements converted to the element type: `list<bit[4]> l = {17, 3}` stores 1 | EX-LIT, EX-WIDTH | 4.8.2, 8.7.2 T23 |
| IP-94 | value-list literal with no context type in `foreach (e : {1, 0x2})`: mixed self-determined types is Neg | EX-LIT, ST-FOREACH | 4.8.2 |
| IP-95 | `in` with an array slice whose bounds come from loop variables, including empty slices (left ≥ size, right < 0) | EX-IN, DT-ARR, ST-REPEAT | 8.5.9.1 |
| IP-96 | `foreach` over `m.values()` / `s.split(",")` (temporaries) while modifying the original map/string variable — legal, since the iterated collection is the temporary | ST-FOREACH, EX-METH | 20.7.8 g |
| IP-97 | a solve function called indirectly (via an unqualified function) from `body` is Neg; the same chain from `post_solve` is legal | FN-SOLVE, FN-CALL | 20.2.1.3 |
| IP-98 | `executor()` in `pre_solve` returns null; in `body` returns the assigned executor; delegation branch taken accordingly | LIB-EXEC, EXEC-KIND, ST-IF | 21.7.2.5 |
| IP-99 | `fatal()` inside a nested loop in a function: nothing after it runs, including the caller's remaining statements | LIB-ERR, ST-REPEAT, FN-CALL | 21.3 |
| IP-100 | struct with a list field passed by handle, callee assigns a NEW list to the field: caller sees the new list | FN-PARAM, DT-STRUCT, DT-LIST | 20.3.2 |

---

## 9. Counts

| Category | Coarse IDs | Sub-constructs |
|----------|-----------|----------------|
| Data types (DT-*) | 19 (incl. NEW DT-PACK, DT-MUT) | 183 |
| Expressions (EX-*) | 19 (incl. NEW EX-TQS, EX-PREC, EX-METH) | 146 |
| Functions (FN-*) | 10 | 71 |
| Procedural statements (ST-*) | 16 | 119 |
| Exec blocks (EXEC-*) | 2 | 20 |
| Built-in library (LIB-*) | 9 (incl. NEW LIB-EXEC) | 57 |
| **Total** | **75** | **596** |
| Interaction patterns | | 100 |
