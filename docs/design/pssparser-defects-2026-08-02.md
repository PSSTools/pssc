# pssparser — defects found 2026-08-02

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

**Build under test:** `pssparser 3.0.0+b94827a-dirty` (commit `b94827a`, "Fixes for overrides
and function linking"), as vendored in `packages/pssparser`. This is **newer** than the build
the existing inventory was written against (`cf833bc`), so §4 below re-verifies what that
inventory and `src/pss/README.md` claimed.

Found while splitting the PSS model into device (`src/pss`) and environment (`tests/pss`).
Every repro here is self-contained: paste into the named files and run the command shown.

> **Note (2026-08-05):** the command lines below use `$(cat src/pss/files.f)` and
> `$(cat tests/pss/files.f)`, which is how the order was presented at the time. Those checked-in
> filelists have since been deleted — they went stale when the model was renamed. Derive the order
> instead: `$(pss-order --from-flow src/pss --task src --relative-to .)` for the device, and the
> two-tree `pss-order` invocation in `tests/pss/README.md` for device plus environment.

Related documents:

- [`pssparser-issues.md`](pssparser-issues.md) — the standing inventory. §5.2 there already
  describes the *symptom* class D1 belongs to; D2 and D3 are the first minimal repros of the
  underlying resolution failures.
- [`pssparser-fix-plan.md`](pssparser-fix-plan.md) — root causes and source locations.

Severity uses the inventory's legend (P0 crash, P1 legal PSS rejected, P2 stdlib gap,
P3 diagnostic quality).

---

## D1 — Internal errors print `Error:`, are not counted, and do not affect exit status

> **FIXED 2026-08-04.** `TaskIsPyRef`'s message was the source of all 78–104 lines; it was a
> mid-pass "not resolved yet" state reported as an error, and is now `DEBUG`. The condition it
> was gesturing at is caught properly by `TaskCheckRefsResolved`, a post-link gate that reports
> any still-unbound type reference with a location and a non-zero exit. Pinned by
> `pssparser/tests/python/errors/test_refs_resolved.py`. See
> [`silent-drop-review-2026-08-04.md`](silent-drop-review-2026-08-04.md).

**Severity: P1.** Not a diagnostic-quality nit: it is a **correctness signal that CI cannot
see.** A run that fails to resolve part of the model reports success.

```sh
$ pssparser $(find src/pss tests/pss -name '*.pss' | sort)
Error: pssp::TaskIsPyRef: Failed to resolve user-defined data type target
... (78 of these)
Error: TaskResolveSymbolPathRef: Failed to get scope @ 2/4
Error: TaskResolveSymbolPathRef: Failed to get scope @ 2/4
0 errors in 0 files
$ echo $?
0
```

**Observed:** 80 lines prefixed `Error:`, then `0 errors in 0 files`, then exit 0.
**Expected:** either these are real failures — in which case they need a name, a source
location, a count, and a non-zero exit — or they are internal chatter, in which case they must
not be printed as `Error:` on a successful run. The present behaviour is the worst of both: a
user or a pre-commit hook has no way to distinguish a clean link from a broken one except by
grepping stdout for `^Error:`.

This is the enabling defect for D2 and D3: both are invisible to any automated check today.

`pssparser-issues.md` §5.2 raised the same contradiction against `cf833bc` and proposed routing
these to a debug channel. That is only half a fix — see D2/D3 for the failures being hidden.

---

## D2 — `TaskResolveSymbolPathRef: Failed to get scope @ N/M` on `foreach` over a component array

**Severity: P3** (no user-visible wrong answer found — see "Impact"), **but see D1**: it is
reported as an `Error:` on a run that otherwise passes.

`t.pss`:

```pss
component sub_c { int chan; }
component pss_top {
    sub_c hs[4];
    exec init_down {
        int x;
        x = 1;
        foreach (hs[i]) { hs[i].chan = 0; }
    }
}
```

```sh
$ pssparser t.pss
Error: TaskResolveSymbolPathRef: Failed to get scope @ 2/4
0 errors in 0 files          # exit 0
```

**Expected:** silence. This is legal PSS and everything in it resolves.

### What it takes to trigger

The body must contain **a local variable declaration** *and* **a `foreach` over a component
array whose body accesses the element**. Neither alone does it. Nearby variants, all clean:

| Variant | Result |
|---|---|
| declaration removed (`x` dropped) | clean |
| declaration present, `x = 1;` removed | clean |
| `x = 1;` replaced by `if (1) { x = 1; }` | clean |
| **two declarations, no statements** (`int x; int y;`) | **fails** |
| `foreach` replaced by `hs[0].chan = 1;` | clean |
| `foreach` body does not use the loop variable | fails (so `i` is not involved) |
| component array replaced by a scalar sub-component | clean |

So the index pair in the message tracks something about the **shape of the enclosing body's
scope**, not about the path being resolved: the same path resolves or does not depending on how
many declarations precede it. In a `solve function` rather than an `exec`, the identical body
reports `@ 3/5` — one more level, consistent with the function adding a scope.

### Impact

A deliberately bogus member inside the failing `foreach` (`hs[i].nosuch = 0;`) **is** still
reported with correct source context, so the failure does not appear to suppress checking of
that statement. The cost is D1: real source produces `Error:` output.

### Where it bites this repository

`tests/pss/pss_top.pss` — the `exec init_down` there declares three locals and ends with
`foreach (hs[i]) { hs[i].chan = i; }`. It is the entire reason
`pssparser $(cat tests/pss/files.f)` is not silent, while
`pssparser $(cat src/pss/files.f)` is.

---

## D3 — Cross-file forward reference into a package that declares a register group is silently unresolved

> **FIXED 2026-08-04, and it was mis-attributed.** The parser's symbol tree was never
> order-dependent for this shape — the messages were D1's noise. The *content* loss was in
> `pssc/ast2ir.py`, a single-pass translator that resolved each `extend` against whatever it had
> translated so far and returned silently when the target's file came later. On this model that
> was all 13 actions and 38% of the IR. Fixed by three-pass elaboration (CONST → DECLARE →
> EXTEND); every declared type now survives in every order. Pinned by
> `pssc/tests/unit/test_file_order_independence.py` and `tests/progseq/test_op_model_order.py`.
> One residual, non-content difference remains — see the review doc §"What is left".

**Severity: P1.** A model that links clean in one file order fails to resolve in another, and
says nothing either time. This is the successor to the segfault recorded as
`pssparser-issues.md` §1.1 / `src/pss/README.md` defect 3: **it no longer crashes, which means
it is now silent** — arguably a regression in observability even though the crash is gone.

`a.pss` (listed **first**):

```pss
import addr_reg_pkg::*;
component d_c {
    function void f(addr_handle_t b) {
        addr_handle_t h;
        h = make_handle_from_handle(b, r_pkg::OFF);
    }
}
```

`b.pss` (listed **second**):

```pss
package r_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;
    const bit[64] OFF = 0x20;
    struct r_s : packed_s<> { rand bit[32] a; }
    pure component r_c : reg_c<r_s, READWRITE, 32> {}
    pure component grp_c : reg_group_c {
        r_c r0;
        function bit[64] get_offset_of_instance(string name) { return 0; }
        function bit[64] get_offset_of_instance_array(string n, int i) { return -1; }
    }
}
```

```sh
$ pssparser b.pss a.pss          # declaration first
0 errors in 0 files              # clean

$ pssparser a.pss b.pss          # reference first
Error: pssp::TaskIsPyRef: Failed to resolve user-defined data type target
0 errors in 0 files              # exit 0 -- but the reference did not resolve
```

**Expected:** identical results in both orders. Order of presentation is not part of PSS; a
linker sees all files before resolving.

### What it takes to trigger

The **later** file must declare a `reg_group_c` subtype that **holds a register instance**.
Removing `r_c r0;` from `grp_c` makes it clean; so does dropping `grp_c` entirely, or reducing
the later file to just the `const`, or to `packed_s` / `reg_c` declarations without the group.
That is the same ingredient — a register group holding a register — that used to be required
for the §1.1 segfault, so the two are almost certainly the same code path.

Note that a plain cross-file forward reference does **not** trigger it: a component referencing
a struct, a const, or a bare package type declared in a later file is fine. The register group
is essential.

### Scale on real source

```sh
$ pssparser $(find src/pss tests/pss -name '*.pss' | sort)     # alphabetical = wrong order
78 x TaskIsPyRef                                               # exit 0
$ pssparser $(cat tests/pss/files.f)                           # dependency order
0 x TaskIsPyRef
```

Reduced to a single trigger inside this model: `src/pss/wb_dma_c.pss` presented before
`src/pss/wb_dma_regs_pkg/` yields 52 of them on its own.

This is why `src/pss/files.f` and `tests/pss/files.f` exist and why the READMEs tell people not
to glob. Fixing D3 retires both files.

---

## D5 — **Every unary operator is silently discarded** — ✅ FIXED 2026-08-06

**Found:** 2026-08-03, while bringing up SV generation for the operation model.
**Fixed:** 2026-08-06. See *Resolution* at the end of this entry.
**Severity: P0.** Not a crash — worse. Legal PSS was accepted, linked clean, exited 0, and
**meant something different from what it said**. `!x` was parsed as `x`.

```pss
component C {
  bit f; int n;
  function void g() {
    bit x;
    x = !f;      // IR: x = f
    n = -n;      // IR: n = n
    x = ~f;      // IR: x = f
    if (!f) { return; }   // IR: if (f) { return; }
  }
}
```

Every one of those loses its operator. No diagnostic, at any severity.

**Diagnosis — the code says so directly.** `AstBuilderInt::visitExpression`
(`src/AstBuilderInt.cpp:3137`):

```cpp
antlrcpp::Any AstBuilderInt::visitExpression(PSSParser::ExpressionContext *ctx) {
	if (ctx->unary_op()) {
		ast::IExpr *lhs = mkExpr(ctx->lhs);
		                          // <- and that is the entire branch.
	} else if (ctx->lhs && ctx->rhs) {
		... builds an ExprBin ...
```

The unary branch builds the operand and then **constructs nothing**: no `ExprUnary` is
created and the operator is dropped, leaving the bare operand as the expression. The
lookup table it would have used is present but empty:

```cpp
static std::map<std::string, ast::ExprUnaryOp> prv_str2unop = {

};
```

`ast::ExprUnary` exists and is exposed to Python (`getOp`, `getRhs`), and pssc's
`ast2ir` has a complete `ExprUnary` branch with an operator mapping — the consumer side
is ready and has simply never received one.

**Why it went unnoticed.** `tests/python/parsing/test_expressions.py::test_unary_plus_minus`
covers `-x` and `+x`, but asserts only `has_symbol(...)` — that the file parses. It would
pass just as well if the parser deleted the whole expression. There is no test anywhere
that a unary operator reaches the AST.

**Suggested fix.** Populate `prv_str2unop` (`!` → `UnaryOp_Not`, `-` → `UnaryOp_Minus`,
`+` → `UnaryOp_Plus`, `~` → `UnaryOp_Not_Bitwise`) and build an `ExprUnary` in that
branch, as the binary branch does. Then add tests that assert on the **AST node**, not on
whether the file parsed — the existing test shape is what allowed this.

**Impact on this repository, while it was open.** Five operations in `src/pss` are
guarded by `!` — the two capability checks originally recorded here, plus three
`inflight` token guards found later:

| Operation | Source | What was generated |
|---|---|---|
| `wb_dma_ch_c::set_auto_restart` | `if (!caps.ars) { return; }` | `if (m_caps.ars) begin return; end` |
| `wb_dma_ch_c::set_software_pointer` | `if (!caps.cbuf) { return; }` | `if (m_caps.cbuf) begin return; end` |
| `wb_dma_ch_c::transfer_single_start` | `if (!inflight.try_put(1))` | `if (inflight.try_put(1))` |
| `wb_dma_ch_c::transfer_list_start` | `if (!inflight.try_put(1))` | `if (inflight.try_put(1))` |
| `wb_dma_ch_c::check_completion` | `if (!inflight.try_get(tok))` | `if (inflight.try_get(tok))` |

All five were **inverted**: they declined to run in exactly the case they were written to
handle. The generated package compiled and linted clean. Nothing in the toolchain could
detect this — the information was gone before pssc saw it.

### Resolution — 2026-08-06

`prv_str2unop` is populated with all seven operators the grammar admits
(`+ - ! ~ & | ^`) and the unary branch builds an `ExprUnary`, as the binary branch does.

**A second defect sat behind it, in pssc.** `ast2ir._map_unaryop` mapped the ordinals
`0=! 1=- 2=+ 3=~`, but `ast::ExprUnaryOp` declares `Plus, Minus, LogNot, BitNeg, ...` —
so `!` and `+` were transposed. That code was unreachable while D5 hid it (no `ExprUnary`
node was ever produced), and it would have shipped the same inversion by another route the
moment D5 was fixed. `_translate_expr_unary` also called `getExpr()`, which the Python
wrapper does not have; it is `getRhs()`. Both are fixed. Two lessons the entry is worth
keeping for: a defect that suppresses a whole node type also suppresses every bug in the
code that consumes it, and **`_map_unaryop`'s docstring said "best guess based on common
conventions"** — an unverified guess that no test could reach.

Both operator maps in `ast2ir` are now total and report an unmapped ordinal through
`ctx.add_error` rather than defaulting. That change caught a third live bug of the same
family: `**` was absent from `_map_binop`, so exponentiation silently lowered to addition.

The three bit-reduction operators (`&x`, `|x`, `^x`) parse but have no IR node, so pssc
refuses them explicitly instead of approximating.

**Verified:** all five guards above now emit `!` in the generated SV, and the one guard
that is genuinely un-negated in the source (`configure_channel.pss:49`, `if (caps.ars)`)
stayed un-negated. `compile if (!C)` also now selects correctly in both directions —
previously it evaluated as `compile if (C)`, silently choosing the wrong build profile.

**Pinned by:** `pssc/tests/unit/test_unary_operators.py` (xfail markers removed; each
test now asserts the *operator*, not just the presence of an `ExprUnary`, which is what
catches the transposed-ordinal bug).

---

## D4 — A function *definition* is rejected for its own default parameter value — ✅ FIXED 2026-08-09

**Found:** 2026-08-03, while restoring the pssc regression suite (op-model-export plan, Phase 0).
**Severity:** P2 — rejects legal PSS, but noisily and with an accurate location, so it costs time
rather than correctness.

A function that supplies a default value **and a body in the same declaration** is rejected:

```pss
component C {
    function void compute(int n = 42) { print("ok"); }
}
```
```
Error: parameter 1 ('n') of 'compute' is given a default value by more than one
       declaration; only one declaration may give it   test.pss:3:32
```

There is only one declaration. The rule being enforced (LRM 20.2.4 c) is real, and the existing
pssparser tests for it are correct — but every one of them uses *prototype-only* declarations
(`tests/python/errors/test_signature_consistency.py::test_a_default_given_twice_is_reported`),
so the definition case is untested and has never worked.

**Diagnosis.** `TaskResolveRefs::checkDeclarationConsistency`
(`src/TaskResolveRefs.cpp:1761`) compares `getPrototypes().front()` against every other entry:

```cpp
ast::IFunctionPrototype *base = i->getPrototypes().front();
for (uint32_t idx=1; idx<i->getPrototypes().size(); idx++) {
    ast::IFunctionPrototype *p = i->getPrototypes().at(idx);
```

The comment above `base` records that `visitFunctionDefinition` **inserts a definition's
prototype at the front** — so for a definition the same prototype is in the list twice, and the
loop compares the declaration against itself. `checkParamListConsistency`'s `b->getDflt() &&
q->getDflt()` (line 1913) is then trivially true for any defaulted parameter.

Only the default check is symmetric-and-self-triggering; return type, arity, kind, direction and
parameter type all compare equal to themselves, which is why this surfaced as one narrow
symptom rather than a flood.

### Resolution — 2026-08-09

Fixed one level further back than this entry suggested. The diagnosis above is right that
`checkDeclarationConsistency` compares a prototype against itself, but the reason it *can* is
that `TaskBuildSymbolTree::visitFunctionDefinition` registers the definition's prototype
**twice** when it also has to create the function symbol:

```cpp
if (!func_sym) {
    ...
    func_sym->getPrototypes().push_back(i->getProto());   // line 385
}
...
func_sym->getPrototypes().insert(                          // line 474
    func_sym->getPrototypes().begin(), i->getProto());
```

The `push_back` is now gone, leaving the insert — which already claims to be the authority
("Ensure that the definition takes the primary prototype location") — as the single
registration. No `return` sits between the two points, so the prototype is registered exactly
once on every path.

**Why not `if (p == base) continue;`.** That tolerates the duplicate instead of removing it, and
`getPrototypes()` has other readers. The `checkNativeParamDir` loop in this very function
iterates every prototype and breaks after one report specifically so that "a prototype and a
definition that both spell the direction are one mistake, not two" — with the duplicate present
that loop was examining the same object twice. Fixing the list makes every consumer correct
rather than each one defensive.

**Confirmed by measurement**, before and after:

| Shape | Before | After |
|---|---|---|
| `function void f(int a = 1) { }` (component or package) | rejected | accepted |
| declaration only, with default | accepted | accepted |
| declaration (no default) + definition (with default) | accepted | accepted |
| declaration (with default) + definition (with default) | rejected | rejected |
| two prototypes, both with defaults | rejected | rejected |
| declaration/definition type disagreement | rejected | rejected |

The third row is what proved the mechanism before the fix: with a declaration present the
`if (!func_sym)` branch does not run, there is no duplicate, and the identical definition was
accepted.

**Now pinned by:** `pssparser/tests/python/errors/test_signature_consistency.py` —
`test_a_definition_alone_may_give_a_default`,
`test_a_definition_that_repeats_a_declarations_default_is_still_reported`, and
`test_a_definition_and_a_declaration_still_disagree_about_types`. The last two exist so the fix
cannot degenerate into deleting the rule. The two `xfail(strict=True)` markers in
`pssc/tests/unit/test_component_arrays_and_functions.py` have been removed; those tests now
pass.

**Rebuild note.** `ninja` in `packages/pssparser/build` rebuilds `build/src/libpssparser.so`,
which is *not* what the Python extension loads. Testing a C++ change through Python needs
`python setup.py build_ext --inplace`; without it the old `.so` keeps answering and the change
looks like it did nothing.

---

## 4. Status of previously recorded defects (re-verified against `b94827a`)

`src/pss/README.md` carried three defects at the time the model was written. Re-run today:

| Recorded defect | Status on `b94827a` |
|---|---|
| **1.** Composite field access inside an `extend component` body does not resolve (`root ref-path element v is not a composite scope`) | **Fixed.** The repro links clean, exit 0. The whole device model — every function and action in an `extend` file — links clean. |
| **2.** SIGSEGV on a reference into a component-array element when the design contains a register group holding a register | **Fixed.** The repro links clean, exit 0. |
| **3.** Wrong file order is a SIGSEGV | **Changed, not fixed.** No longer crashes; now fails silently — see D3. |

`pssparser-issues.md` §1.1's whole-model segfault likewise no longer reproduces: the 35-file
model links without crashing in dependency order *and* in alphabetical order. What remains of
§1.1 is D3.

The README defect list has been superseded by this file; the workaround inventory in
`pssparser-issues.md` §8 should be re-checked, since defects 1 and 2 above were the
justification for at least two of its entries.

---

## 5. Suggested order of attack

1. **D1 first.** Until an unresolved reference is countable and changes the exit status, D2 and
   D3 cannot be regression-tested — a fix and a non-fix produce the same `0 errors`, exit 0.
   A test asserting on grep of stdout is not a test worth having.
2. **D3 next.** It is the one that silently changes the meaning of a model, and it has a
   two-file repro.
3. **D2 last.** No wrong answer has been demonstrated, only noise — but the scope-index
   arithmetic it exposes is shared with D3's code path, so it may fall out of the same fix.
