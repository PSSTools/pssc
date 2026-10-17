# 06 — Front-end checks pssparser does not make (F1–F32)

Status: **done but F32** (pssparser branch `front-end-checks`, unpushed).
Done: step 0 typer (1675c3a); F8, F9, F10+W40, F16, F17 (index, iterator
written whole, collection replaced or changed by a method; element and
iterator-member writes left legal per Ex 291 / 20.7.8 c), F18, F21
(00b0eba, narrowed in a199765); F4, F29, F30, F12, F13 (a199765); F3
(5780a63); F1+W38 (fb0eabc; `in` precedence grammar fix a7bc8c4); F2
(657de18); F11, F14, F15, F26 (4c7d2f0); F5, F6, F7, F19, F20, F22, F25,
F27, F28, F31 (9dd8cea); F24 (1854235); F23 (33da9f3). Open: F32 (optional,
not done); the core library's `print` called from target code is not
reported (PSS144) pending a ruling. Marker IDs used: PSS121-PSS149. Written 2026-10-17 for the agent that implements them.
Source: the construct test audit's P5.3 mapping (`00-plan.md` §2, the
"Front-end checks missing (F)" table; §8 entries of 2026-10-16/17).

Each item below is something the PSS 3.1 LRM makes an **error** -- or that
a pssc ruling (R18-R23, `00-plan.md` §7) makes one where the LRM is silent
or ambiguous -- that pssparser accepts today. pssc then runs the model, generates code for it, or
refuses it for an unrelated reason. They belong in pssparser (fix at source:
pssc's rule is that a front-end gap is fixed in the front end, never worked
around in pssc).

## 1. How to work

**Where.** `packages/pssparser` inside this pssc checkout (it is what pssc
loads: `packages/pssparser/python/pssparser`, native library dlopened from
`packages/pssparser/build/lib/libpssparser.so`). Start a new branch from the
current one:

```
cd packages/pssparser
git checkout const-select-eval        # b52f1d2, stacked on chained-method-calls
git checkout -b front-end-checks
```

Both parent branches are unmerged and unpushed; do not rebase them.

**Load the `pssparser-pssparser-dev` skill** before changing the grammar, AST,
builder, linker or resolver -- it has the regeneration and rebuild rules.

**Rebuild** after any C++ change (the Python package dlopens the INSTALLED copy):

```
cmake --build build && cmake --install build      # both, every time
```

`cmake --build` alone leaves the old `build/lib/libpssparser.so` in place and
the old behaviour running. After an AST (`ast/*.yaml`) change, follow the
skill (regenerate, clear the AST stamps, `python setup.py build_ext --inplace`).

**Where a check goes.** The semantic checks are C++ linker tasks
(`src/Task*.cpp`; most, including PSS007 and LINT101, are in
`src/TaskResolveRefs.cpp`). `core_checker.py` holds no check logic: it only
maps the C++ message text to an ID. Two partial expression typers exist:
`src/TaskExprTypeCat.{h,cpp}` (categories Int/Bool/Float/String/Enum/
Chandle/Aggregate/Null/Unknown, but it follows only a bare name -- `a.b`,
`a[i]`, slices and calls are Unknown, known issue P3-X6c) and
`src/ExprTypeOf.h` (enum types only, for 8.4.3). See §2 step 0.

**Diagnostics.** Each check reports an error with the offending construct's
location (`file:line:col`), with a marker ID. New IDs start at **PSS121**
(PSS120 is retired and never reused). An ID is declared as a `MarkerDef` in
`python/pssparser/checkers/core_checker.py` (with the `patterns` that map the
C++ message text to it), pinned with a representative message in
`tests/python/test_marker_ids.py` (extend its PSS1xx band list too), and
documented in `docs/markers.rst`; error-suite goldens are updated as the dev
skill's `references/error-suites.md` says. Every active PSS marker must be
an error (a test enforces it). Group related checks under one ID where
the LRM clause is one rule. Message text names the LRM clause -- and quotes
its strength: several rules here are worded "may only", "cannot" or "do not
apply" rather than "shall", and an R-numbered ruling is cited as such.

**Starting models.** `06-snippets/` (next to this file) holds a minimal
complete model for every snippet below (`F<n><letter>.pss`, `L` = a legal
neighbour), and `06-snippets/results-b52f1d2.txt` what pssparser said of
each at b52f1d2. Note the imports the snippets need: `std_pkg::*` for
`message` and `file_*`; `addr_reg_pkg::*` for `addr_handle_t`, `write32`,
`addr_value`, `reg_c`, `reg_group_c`; `sync_pkg::*` for `channel_c`.

**Tests in pssparser.** One test per check under `tests/python/` (the error
suites are `tests/python/errors/`; semantics in `tests/python/parsing/`),
each **calibrated**: it fails without the fix. Every negative test has a
**legal neighbour** that must stay accepted -- most of these checks are about
types, and a false positive on a legal model is worse than the gap.

**Run pssparser's whole suite** before each commit:

```
cd packages/pssparser
PYTHONPATH=python ../python/bin/python -m pytest -q tests/python
```

(8662 passed, 58 skipped, 6 xfailed at `b52f1d2`.)

**Verify against pssc.** pssc pins most items as **strict xfails** in its
construct matrix (`tests/progseq/matrix/`, see `_matrix.py`); the case
expects the refusal the LRM requires, and the xfail names the F row. When
your check lands, the case starts passing, a strict xfail turns into a
failure (XPASS), and you:

1. remove that case's `xfail=...` (and the module constant `F<n> = ...` once
   unused -- also the variants `F5_RAND`, `F11_GENERIC`, `F8_E26` in
   `test_m_neg.py`). A case may also be xfailed on an E id for some
   targets, which then XPASS too: F13's on c/cpp/sv (E32), F15 "another
   signature" on sv (E38). Remove those as well and mark the E row fixed;
2. tighten its `Refused([...])` patterns to your actual message -- most were
   written as loose placeholders before any diagnostic existed. Some cases
   are xfailed only on some targets (F4's string cast, F9, W38: py, sv;
   F12: py) and PASS elsewhere on the loose pattern, matching pssc's own
   refusal; a tightened pattern must still match on those targets (the
   front-end error now comes first, so it normally will -- check);
3. in `docs/design/construct-test-audit/00-plan.md` §2, mark the F row
   **Fixed** (with the pssparser commit), and log it in §8;
4. for F23/F31, which pssc records as `open` dispositions rather than cases,
   replace the disposition in `docs/design/construct-test-audit/coverage.yaml`
   with a matrix case in `tests/progseq/matrix/test_m_neg.py` (the file for
   illegal models).

For items with **no pssc case yet** (marked *none* below), add one to
`test_m_neg.py` that expects your diagnostic. Every `Refused` line must be
located (`_matrix.LOCATED`).

pssc commands (from the pssc root):

```
PYTHONPATH=src packages/python/bin/python -m pytest -q tests/progseq/matrix/test_m_neg.py
PYTHONPATH=src packages/python/bin/python -m pytest -q tests/unit tests/progseq tests/compliance
```

The full pssc run is ~8 minutes. Its one known failure is
`tests/unit/test_doc_references.py::test_the_archived_notes_say_they_are_archived`
(an untracked design note, not yours). **The compliance suite matters**: it
runs the pss-corpus models through pssc; a new pssparser error on a corpus
model is either a corpus bug (raise it, never edit a corpus model) or your
false positive.

**Do not edit pssc's `src/`.** Where pssc has a stopgap for an item (noted
below), leave it; say in your report that it can now be removed.

**Commits.** One commit per item or per closely related group, in
pssparser; the matching pssc test/doc changes in a pssc commit that names
the pssparser commit. Commit messages end with the repo's attribution line.

## 2. Order

Do the ones that let a model **compute something wrong** first:

0. **The expression typer.** F1, F3, F4, F12, F29 and F30 all need the type
   of an arbitrary operand -- `a.b`, `a[i]`, `s[i..j]`, a call's result --
   which neither existing typer gives (§1). Extend `TaskExprTypeCat` (or
   grow `ExprTypeOf` into it) on the linker's resolved types first, with its
   own tests, and do not build per-check type logic. Group 3 and 4 depend
   on it; group 1 and 2 do not.
1. F8, F9, F10 (+W40): writes to things that are not writable.
2. F16, F17, F18, F21: control-flow errors (F16 makes op-model-py emit a
   module that does not import).
3. F3, F4, F29, F30, F12, F13: illegal operands, casts and arguments.
4. F1 (+W38), F2: operand and enum value typing -- broad; watch for false
   positives (see F1).
5. F11, F14, F15, F26: function declaration consistency.
6. F5, F6, F7, F19, F20, F22, F23, F24, F25, F27, F28, F31, F32.

## 3. The items

Each: the LRM rule, a minimal illegal model (and a legal neighbour where it
matters), what happens today, and what pins it in pssc. The LRM is
`PSS 3.1 Public Review Draft 2026.08.28 (2).md` at the pssc root.

### F1 — operand and condition types (8.4 Table 10, 8.5.2, 8.5.5, 8.5.8, 7.7, 8.3, 20.7.7 a, 20.7.9)

Table 10 says which operand types each operator takes; a condition is `bool`.
Illegal (all accepted and run today):

```
bool p, q;   if (p < q) ...            // relational on bool
string s, t; if (s < t) ...            // relational on string
int a, b, c; if (a < b < c) ...        // bool compared with an int
bool r = ~p; bool r2 = p & q; p &= false;   // bitwise on bool
chandle h;   if (h == 1) ...; chandle k = h + 1;   // chandle: only ==/!= with 0 or null
int x;       if (x) ...; while (x) ...; repeat { } while (x); y = x ? 1 : 2;
int y = null;
float64 f;   int m = f % 2;            // % on a float (R5 refuses floats later)
string u = s + "c";                    // no string `+` (W38)
```

Clauses: relational on bool/string and the chained form, 8.5.2 ("may be
applied only to numeric operands"); bitwise on bool, 8.5.5 (`&=`: 8.3
Table 9); chandle, 7.7 (only `==`/`!=`, and "the only literal value with
which it may be compared is 0"); `if`, 20.7.9; `while`/`repeat-while`,
20.7.7 a; `?:`, 8.5.8 ("cond_predicate shall be of Boolean type");
`int y = null`, 8.3 (assignment-compatible); float `%` and string `+` only
by their absence from Table 10.

Legal neighbours: `if (x != 0)`, `p == q`, `p && q`, `!p`, `h != 0`,
`h == 0`, `x ? ...` with `x` a bool, enum `==`/`!=`. (`h == null` is
doubtful: `null` belongs to reference types, 7.10, and a chandle is not
one. Do not add it as a neighbour; report what pssparser does with it.)
pssc: `test_m_bool_enum_cast` "a relational operator on bools is refused";
`test_m_neg` "a chained relational operator", "a bitwise and of bools", "a
bitwise not of a bool"; `test_m_strings` "string concatenation with +"
(pinned as **W38**, same root: remove its xfail too, and replace the
`coverage.yaml` IP-71 py/sv disposition `open, defect: W38` with that case).
**Risk:** this is the broadest check. pssparser has no expression typer yet
that this can reuse wholesale; build it on the resolved types the linker
already has. Run the pss-corpus compliance tests early.

### F2 — enum values (7.5.1, 7.5.1 c, e, j; 17.2.4)

```
enum e_e { A = 1, B = 1 }                  // duplicate value (7.5.1 e)
enum e_e { A = 1 } extend enum e_e { C = 1 }   // duplicate across an extension
enum f_e : bit[2] { A = 3, B }             // implicit B = 4 outside bit[2]
enum g_e : bit[4] { A = -1 }               // negative with an unsigned base
enum h_e : int in [1..10] { A = 1 }        // a base type with a domain (7.5.1, before item a)
enum n_e { }  n_e v;                       // a variable of an itemless enum (7.5.1 j)
```

Today all are accepted; a duplicate makes `%n` print the other item's name.
pssc: `test_m_bool_enum_cast` "two enum items with one value are refused",
"an extension item with an existing value is refused", "a variable of an
itemless enum is refused".

### F3 — an unqualified enum item where the expected type is not the enum (7.5.1 i, 8.4.3, Ex 37, 8.5.9.1; ruling R22)

```
enum col_e { RED, GREEN, BLUE }
int c = (int)BLUE;           // Ex 37: illegal; `(int)col_e::BLUE` is fine
col_e e; if (M1 == e) ...    // unqualified on the LEFT: no expected type yet
if (M1 in [M0, M1]) ...
```

**Not silent today:** `(int)BLUE` and `M1 in [...]` already get
**LINT101**, a warning ("enum item 'BLUE' is used where no enumeration type
is expected (7.5 i, 8.4.3); qualify it as 'col_e::BLUE'"), which was PSS046
until lint-framework D-1 moved it to lint. Ruling **R22** makes it an error
again: retire LINT101 for this rule (or turn it into the new PSS ID's alias,
as the retired rows do) and add `M1 == e`, which gets nothing today. The
`in` form has its own "shall" (8.5.9.1: "The left-hand side shall not be an
unqualified enum_item"); the `==` form rests on 8.4.3 giving only the RIGHT
of `==` an expected type.

Legal neighbours: `col_e c = BLUE`, `e == BLUE`, `(col_e)BLUE`, `f(BLUE)` with
an enum formal, `return BLUE`, `?:` arms. pssc: `test_m_bool_enum_cast` "an
unqualified item under an integer cast is refused"; `test_m_neg` "an
unqualified enum item on the left of in".

### F4 — casts (7.12, 7.12.1)

```
chandle c; int x = (int)c;            // from a chandle
struct p_s { int a; } p_s v; int y = (int)v;   // from a struct
string s; int z = (int)s;             // from a string
p_s w = (p_s)3;                       // to a struct (crashes op-model-py at run time)
```

A cast TO a struct breaks the 7.12.1 text ("casting_type shall refer to a
numeric, Boolean, enumeration, or reference type"); a cast FROM a chandle,
struct or string is illegal only because 7.12 does not list it -- word the
message accordingly. `(chandle)5` is already a syntax error (PSS028). pssc: `test_m_bool_enum_cast`
"casts from a chandle or a struct, or to a struct, are refused", "a cast
from a string is refused".

### F5 — non-randomizable `rand` and `randomize` (7.1, 7.7; ruling R18)

```
struct s_s { rand chandle h; int a; }
solve function void initialize(...) { chandle c; randomize c; }
struct t_s { rand float64 f; }        // R5 refuses floats later, but say it here
```

The `rand` field is the 7.1 shall. The `randomize` target is not: 13.4.6
requires only "plain-data", which a chandle is; ruling **R18** reads it as
"randomizable" (chandle, float32, float64), so cite 7.1, 13.4.6 and R18.
pssc: `test_m_neg` "a rand chandle field", "a randomize of a chandle".

### F6 — access protection (17.4)

```
struct p_s { public int a; private int b; }  p_s v; v.b = 2;
component sib_c { private int x = 4; }
component pss_top { sib_c s; target function void run() { message(LOW, "%d", s.x); } }
```

17.4 b is descriptive ("accessible only from the element ..."), not a
"shall". pssc: `test_m_neg` "a private field read from outside its component";
`test_m_struct` "a private struct member written from outside".

### F7 — `bit[0]` (7.2.1 c; ruling R19)

`bit[0] v;` is accepted, as is `bit[W]` with `W` a constant 0. 7.2.1 c says
widths "should" be positive -- a recommendation; ruling **R19** makes a
width that folds to 0 or less a static error. Cite both. **pssc already refuses it** (`ast2ir._int_width`,
since I13); a front-end check is still right (it also covers a constant
expression that folds to 0 or less). pssc case: *none* -- add one.

### F8 — assignment to a `static const` (7.1, 7.9 a)

```
component pss_top {
  static const int C = 5;
  static const array<bit[8], 3> TBL = {5, 6, 7};
  struct p_s { int x; }  static const p_s P = {.x = 1};
  target function void run() { C = 2; }        // op-model-py renders `5 = 2`
  exec init_down { TBL[0] = 1; P.x = 2; }      // op-model-py really changes them
}
```

Also an element/member through any path, and a compound assignment. pssc:
`test_m_neg` "assigning a static const", "assigning an element of a static
const array", "assigning a member of a static const struct".

### F9 — assignment to a sub-string (7.6.2)

`string s = "abc"; s[0] = "x";` -- op-model-py raises TypeError at run time,
op-model-sv emits it. pssc: `test_m_strings` "an assignment to a sub-string".

### F10 — writing a `const` parameter (20.2.3), and W40

```
target function int f(const int x) { x = 3; return x; }         // F10
function void g(p_s s) { s.a = 1; }
function void h(const p_s s) { g(s); }                           // W40: const passed to non-const
```

pssc: `test_m_functions` "an assignment to a const scalar parameter";
`test_m_neg` "assigning a const parameter"; `test_m_functions` "a const
struct parameter forwarded to a non-const formal" (pinned as **W40**).

### F11 — varargs and generic parameters of a user-written function (20.2.5; ruling R23)

```
target function int f(int k, int ... rest) { return k; }    // varargs: built-ins only
function int g(struct x) { return 1; }                      // generic (`type`, `struct`, `numeric`): built-ins only
```

Syntax 94 accepts both forms, with or without a body; 20.2.5 says they
"are used for the declaration of functions whose definition is built into
implementations". Ruling **R23**: only a core-library function may have
one. A user-written function with a generic or varargs parameter is an
error whether it has a PSS body, is imported, or is a bodiless declaration
-- so Ex 293 and 294, written in a user model, are errors too. "Core
library" means declared in one of the standard-library sources pssparser
bundles (`std_pkg`, `addr_reg_pkg`, `executor_pkg`, `sync_pkg`, ...); the
legal neighbour is a call of `message`/`print` with several arguments.
No pss-corpus model declares one (checked 2026-10-17; its `curated/stdlib/std_pkg.pss` copy has the varargs commented out). pssc:
`test_m_neg` "a varargs parameter of a PSS function", "a generic parameter
of a PSS function"; add a bodiless-declaration case.

### F12 — an array argument of another size than the formal (8.3, 20.3.2)

`function bit[8] sum(array<bit[8], 2> v) {...}  array<bit[8], 3> w; sum(w);`
-- op-model-py runs it. 8.3's same-size rule is stated for assignment; an
argument is passed by handle (20.3.2), so the mismatch follows from the
formal's type, not from an explicit rule. pssc: `test_m_array` "an array argument of another
size than the parameter".

### F13 — a function returning a component without `ref` (20.2.2)

`component sub_c {}  ...  function sub_c g() { return s; }` -- C/C++/SV then
raise an internal error (E32). pssc: `test_m_functions` "a function
returning a component without ref".

### F14 — a `target` declaration with a `solve` definition (20.3.1, 20.4.1 b)

`target function int f(int x);  solve function int f(int x) { return x; }`
20.3.1, after Syntax 94: "If the function declaration is provided separately
and is qualified, platform_qualifier shall be the same, or it may be
omitted. If the function declaration is unqualified, platform_qualifier
restricts the function availability". 20.4.1 b says the same for import
definitions. Ex 297 (20.2.7) gives the legal neighbours: an unqualified
declaration whose definition adds `solve`, and a qualified declaration whose
definition omits the qualifier.
pssc: `test_m_functions` "a target declaration with a solve definition".

### F15 — declaration/definition and override consistency (20.2.3 c, 20.2.4 c, 20.2.6, 20.2)

```
function int f(const int x);  function int f(int x) { return x; }          // const mismatch
component base_c { target function int f(const int x, int y = 2) {...} }
component der_c : base_c { target function int f(int x, int y = 5) {...} } // const and default
component der2_c : base_c { pure function int f(...) {...} }                // non-pure made pure
component der3_c : base_c { target function int f(int x, int y, int z) {...} }  // another signature
component der4_c : base_c { static function int f(int x) {...} }           // instance shadowed by static
```

PSS009 already checks defaults on a redeclaration, not const, and neither on
an override. op-model-sv raises an internal error for the different
signature (E38). pssc: `test_m_functions` "a definition that drops its
declaration's const", "an override that changes const and a default", "a
non-pure function overridden as pure", "an override with another
signature", "an instance function shadowed by a static one".

### F16 — `break`/`continue` outside a loop (20.7.11 b)

`target function void run() { if (x == 1) { break; } }` -- op-model-py
writes a module that does not import (SyntaxError). pssc case: *none* --
add one (and one for `continue`).

### F17 — modifying the collection a `foreach` iterates (20.7.8 g)

`array<int,3> w; foreach (w[i]) { w[0] = 5; }` -- accepted and run.
Writing the iterator or the index is also 20.7.8 g, and pssparser accepts
both: the iterator write is refused by pssc, and the index write by
op-model-py only (W61). Include both.
pssc: `test_m_neg` "a foreach changing its collection" (and "a foreach
writing its index", pinned W61 on c/cpp/sv).

### F18 — two `default` arms in one `match` (20.7.10 f)

`match (x) { [1]: ...; default: ...; default: ...; }` -- op-model-py runs the
second. pssc case: *none* -- add one.

### F19 — a type-scope constant in a `compile if` condition (19.1.3 b)

`component pss_top { static const int LOC = 1; ... compile if (pss_top::LOC == 1) {...} }`
-- only package-scope types and constants may be referenced. pssc case:
*none* -- add one.

### F20 — `yield` in solve code (20.7.14 a; ruling R20)

`solve function void initialize(addr_handle_t base) { ...; yield; }`, and
`yield` in a solve-kind exec (`init_down`, `pre_solve`, ...). 20.7.14 a
("may only be used in target exec blocks and functions") is ambiguous about
functions; ruling **R20** makes `yield` target-only. An unqualified
function containing `yield` stays legal (legal neighbour: one called from
target code). pssc case: *none* -- add one.

### F21 — `return <value>;` in an exec block (20.7.5; ruling R21)

`exec init_down { return 1; }` -- accepted silently. 20.7.5 only permits the
bare form in execs; ruling **R21** makes the valued form an error, as in a
void function. Report it under **PSS007** (the void-function form, "'run'
returns void, so 'return' cannot take a value"), not a new ID. pssc case:
*none* -- add one.

### F22 — exec placements and call contexts (20.1.2, 20.5, 20.2.1.3, 20.9 Table 29)

```
component pss_top { exec pre_solve { ... } exec body { ... } }   // not component exec kinds
component pss_top { exec init_down C = """int y;"""; }           // target-template exec of a solve kind
component pss_top { function void ts(int x); target C function void ts(int x) = """...""";
                    exec init_down { ts(1); } }                   // a target-template function from an init exec
target function void run() { if (file_exists("x")) ... }         // solve-only file_* from target code
solve function void initialize(...) { write32(base, 1); }        // a memory primitive from solve code
```

The `file_*` and memory-primitive rules are 20.2.1.3 ("Functions restricted
to the solve platform shall not be called ... from target execs", and the
converse); `file_*` from a target FUNCTION is illegal by inference.
`pre_solve`/`body` in a component rest on 20.1.2's list of where each kind
is valid (no "shall"). pssc refuses these, but as "not lowered / not
supported" (M5), or for `write32` as "legal only in a target context", so
the LRM error is unsaid. pssc: `test_m_neg` "a target-template function called from
init_down", "a target-template init_down block", "a file function called
from target code" (strict xfails on F22), and "a pre_solve exec in a
component", "a body exec in a component", "write32 in a constructor" (passing
today on pssc's wording -- tighten their patterns to your message).

### F23 — collection Negs (7.1, 7.9, 7.10 a, 7.12.1, 8.5.9.1, 8.6.2, 9.1.5.3 a, 20.2.3 b)

```
map<int,int> m; if (k in m) ...            // `in` on a map (8.5.9.1)
set<int> s; int a = s[0]; ... s[0..1]      // subscript (8.6.2, Table 10) / slice under `in` (8.5.9.1) of a set
struct t_s { rand map<int,int> m; rand set<int> s; }   // 7.1
constraint { x == l.pop_back(); }           // a modifying method in a constraint (7.9)
struct u_s { ref sub_c r; }                 // a ref field in a struct (7.10 a)
function void f(const ref sub_c c);         // const ref (20.2.3 b)
typedef p_s pt; ... (pt)v                    // cast to a typedef of a struct (7.12.1, as F4)
instance on a non-ref field                 // 9.1.5.3 a ("may only")
```

op-model targets refuse collections anyway (R4), which hides these. pssc:
`coverage.yaml` dispositions DT-LIST.12, DT-MAP.5, DT-MAP.10, DT-SET.6,
DT-SET.7 (`open`, `defect: F23`) -- replace each with a `test_m_neg.py` case.
The `ref` field already has a case, `test_m_struct` "a ref member of a
struct", passing on pssc's wording ("field 'r' is a reference"): tighten it.

### F24 — a method of a string-array element (a resolution bug, not a check)

`array<string,2> s = {"a","bc"}; int n = s[1].size();` -- PSS002 "cannot
resolve size: the enclosing scope is unknown". Same family as the
chained-method fix on `chained-method-calls` (65f6471). pssc case: *none*.

### F25 — `addr_value` from solve context (21.13.5)

`solve function void initialize(addr_handle_t base) { bit[64] v = addr_value(base); }`
-- `addr_value` is a target function. pssc case: *none*.

### F26 — import/export parameter types (20.4.1.1 c 1) and 8), 20.4.2.1 g)

```
import target function void put(bit[65] v);          // > 64 bits: no foreign mapping
import target function void put2(list<string> l);    // not an import type
static function int sf(p_s p) {...}  export target function sf;   // an export's struct parameter must be const
```

An import written in a component as above is refused for an unrelated
reason ("must be declared 'static'"); write the models as a static or
package function declaration plus `import target function put;`
(`06-snippets/F26*P.pss`).

pssc: `test_m_neg` "an import parameter wider than 64 bits", "an import
parameter of a list of strings", "an export with a non-const struct
parameter".

### F27 — a `reg_c` outside a `reg_group_c` (21.14.1, 21.14.2)

`component pss_top { reg_c<bit[32], READWRITE, 32> X; }`, and
`extend component reg_group_c { ... }` (21.14.2: "It shall be illegal to
extend the class"; the first is 21.14.1's "can be instantiated only in a
register group"). op-model-py then raises an unlocated
error (E39). pssc: `test_m_registers` "a register in a plain component".

### F28 — `channel_c<string>` (21.9.1)

The element type is numeric, bool, an enum with a base type, a packed
struct, or an array of those. pssc case: *none*.

### F29 — a struct on the left of `in` with a range list (8.5.9.1)

`struct p_s { int x; } p_s p; if (p in [1]) ...` -- op-model-py evaluates false.
pssc: `test_m_neg` "a struct on the left of in with a range list".

### F30 — `==` and assignment of component arrays (7.9.2)

`sub_c s[3], t[3]; if (s == t) ...; s = t;` -- only `[]`, `size()`, `foreach`
and `in` apply to an array of modeling elements (7.9.2: other operators "do
not apply ... unless otherwise specified"; no "shall"). pssc: `test_m_components`
"a component array compared", "a component array assigned".

### F31 — a constant zero divisor, a constant negative shift (8.5.1, 8.5.7)

`int y = x / 0; int z = x % 0; int w = x << -1;` (constant right operand).
The LRM makes both forms illegal (8.5.1 "shall be considered illegal",
8.5.7 "shall be a non-negative"); ruling R14 leaves the run-time form
undefined in pssc, so only the constant form -- the one a front end can
see -- is to be reported. pssc: `coverage.yaml` EX-ARITH.4, EX-SHIFT.3 (`open`,
`defect: F31`) -- replace with `test_m_neg.py` cases.

### F32 — `error()`/`fatal()` strings not known at solve time, in target code (21.3)

**pssc refuses this now** (ruling R15, `targets/error_strings.py`;
`test_m_strings` "error() and fatal() of run-time strings in target code").
A pssparser check is optional -- it needs the target/solve context
classification pssc already does. Do it last, or not at all; say which.

## 4. Report

When you stop, report: which items are done (pssparser and pssc commits),
which are not and why, any LRM reading you had to make beyond rulings R18-R23,
any corpus model your check rejected, and which pssc stopgaps (F7, F32,
the M5 wording for F22) are now redundant.
