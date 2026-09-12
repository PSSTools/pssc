# Plan: propagate PSS comments into generated SV and C

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **All six phases applied**, plus the SystemRDL follow-up in §12.
Phase tracking is in §9.

Goal: the generated operation model reads like the PSS it was transcribed from.
Today `pssc.OpModelSv` / `pssc.OpModelC` produce a faithful *code* transcription
and drop every word of prose. The prose is the part a reader cannot recover from
the code.

Companion to `docs/pss-doc-comment-plan.md`, which restructured `src/pss` so doc
comments sit against their declarations rather than floating above the imports.
That work is what makes this feasible: the sources already distinguish a **file
note** (above the imports, deliberately unattached) from a **doc comment**
(immediately above the declaration). This plan carries the second across and
leaves the first behind, by construction.

---

## 1. Decisions taken

Settled before drafting; recorded here so the phases don't re-litigate them.

1. **On by default.** Comment propagation is enabled for the op-model targets
   with a `--no-comments` escape, not opt-in. A transcription that silently drops
   its rationale is the defect being fixed.
2. **All comments, not just doc comments.** `//` lines in statement position
   carry most of the reasoning in these bodies. Restricting to `/** */` would
   leave the majority behind.
3. **pssparser captures comments generally**, not merely as a docstring string.
   The existing `docstring` slot is a lossy single-string summary built for
   `sphinx-pss`; it cannot represent a trailing comment, a run of separate
   blocks, or a comment's own source location. Phase 1 adds a real comment model
   and keeps `docstring` working unchanged.

---

## 2. Layer map — verified current state

Every claim below was checked against the source or a live parse.

| Layer | Where | State |
|---|---|---|
| Lex | `pssparser/src/PSSLexer.g4:181,189,195` | Comments **preserved**: `WS`→ch 10, `SL_COMMENT`→ch 11, `ML_COMMENT`→ch 12. Nothing is skipped. |
| AST build | `pssparser/src/AstBuilderInt.cpp:3845` `addDocstring()` | Attaches one docstring to **declaration** `ScopeChild`s via `getHiddenTokensToLeft`. Gated on `m_collectDocStrings`, default **false** (`:55`). |
| AST schema | `pssparser/ast/coretypes.yaml:81` | `ScopeChild.docstring : string`. Generated from YAML by pyastbuilder (`scripts/gen_ast.py`) at build time. |
| Statements | `pssparser/src/AstBuilderInt.cpp:5054` `mkExecStmt()` | **No** docstring, **no** location. Single funnel for every procedural statement, including nested bodies; has `ctx->getStart()`. |
| Python bind | `pssparser/python/ast.pyx:1838`, `python/core.pyx:147` | `getDocstring()` and `setCollectDocStrings()` both exposed. `pssparser.Parser` offers **no way to enable** it. |
| Consumers | `sphinx-pss/src/sphinx_pss/model/{parse,builder}.py` | The only current consumer. Must keep working. |
| AST→IR | `pssc/src/pssc/ast2ir.py` | Propagates **neither** docstrings **nor** locations. This is the whole gap. |
| IR | `zuspec-ir-core/.../stmt.py:29` | `Stmt.comment : Optional[str]` **already exists and is never set or read** anywhere. `Base.loc` exists; `Function.metadata` exists. |
| IR serialization | `.../json_converter.py:91,135`, `serializer.py:106` | Generic over `dc.fields` — new fields ride along with no changes. |
| SV emit | `pssc/targets/sv/lower_progseq.py:397,403,809,978` | Plain line emitter over IR. `SVStmtComment` exists in the SV IR but this path doesn't use the SV IR. |
| C emit | `pssc/targets/c/lower_progseq.py:1106,1112,1453,1564` | Same shape as SV. |

### 2.1 Live-parse findings

Parsing a sample with `setCollectDocStrings(True)`:

```
Component DOC='\n     * A component that does things.\n     '
  Field DOC=' The channel index we operate on.\n'
  FunctionDefinition DOC=' Probe the status register. '
    ExecScope
      ProceduralStmtDataDeclaration      <- nothing
      ProceduralStmtIfElse               <- nothing
```

- Declarations work; **statements get nothing**.
- Docstrings **survive `extend`** — verified separately with a function declared
  in an `extend component` block, which is how every operation in this model is
  written. This was the main risk to the whole approach and it is clear.
- The `/** */` cleanup at `AstBuilderInt.cpp:3963` **does not strip the leading
  `*`** — note the `* A component` in the output above. Latent bug.
- `processDocStringSingleLineComment` (`:3992`) concatenates **every** preceding
  `//` token with no adjacency test. Consequences on real source: a trailing
  `return 0; // done` becomes the *next* statement's comment, and two blocks
  separated by a blank line merge into one. Must be fixed before statement
  capture is turned on, or the output will be actively wrong rather than merely
  incomplete.

---

## 3. Design — the comment model

### 3.1 What a comment attaches to

Three placements, decided by source geometry:

| Placement | Rule | Example |
|---|---|---|
| **leading** | A contiguous run of comment lines ending on the line immediately above the construct, with no blank line between. | `// Read the status word`<br>`int s = read();` |
| **trailing** | A comment beginning on the same line as, and after, the construct it follows. | `rand bit[32] src;   // CHn_A0` |
| **orphan** | Anything else — a comment separated by a blank line, or one at the end of a block with no following construct. Recorded on the enclosing scope, **not emitted** initially. | file notes above the imports |

The blank-line rule is not new: `sphinx-pss`'s `native-style.md` already defines
it, `docs/pss-doc-comment-plan.md` §1 relies on it, and the existing test
`pssparser/tests/python/source_references/test_docstrings.py:53` asserts it. This
plan extends the same rule to statements. The consequence worth stating plainly:
**a file note above the imports stays out of the generated code by design**, and
a deliberately-detached note remains the way to write a comment that does not
propagate.

### 3.2 AST representation

New node in `pssparser/ast/coretypes.yaml`:

```yaml
enums:
  - CommentPlacement:
      - CommentPlacement_Leading
      - CommentPlacement_Trailing
      - CommentPlacement_Orphan

classes:
  - Comment:
      super: ScopeChild          # gives location/endLocation for free
      data:
      - text:        { type: string }          # normalized, markers stripped
      - raw:         { type: string, is_ctor: false }   # verbatim source
      - is_block:    { type: bool,   is_ctor: false }   # /* */ vs //
      - placement:   { type: CommentPlacement, is_ctor: false }
```

Added to `ScopeChild`:

```yaml
      - comments: { type: list<UP<Comment>>, is_ctor: false, visit: false }
```

`list<string>` and enums are both already used in these YAML files
(`ast/field.yaml:181`, `ast/exec.yaml:6`), so nothing new is asked of
pyastbuilder. `visit: false` keeps comments out of visitor traversals, so no
existing pass changes behaviour.

`Scope` additionally gets `trailing_comments : list<UP<Comment>>` for orphans at
the end of a block.

**`docstring` is kept and unchanged.** It is derived from the leading comments
by the same code path as today, so `sphinx-pss` sees no difference. Two knobs,
independently settable:

- `setCollectDocStrings(bool)` — existing behaviour, existing default `false`.
- `setCollectComments(bool)` — new, default `false`; when on, populates
  `comments` on every `ScopeChild` **including statements**, and implies
  docstring collection.

### 3.3 Normalization

One function, `normalizeComment()`, applied once at capture:

- `//` → strip the leading `//` and at most one following space.
- `/* */` → strip the delimiters, then strip a leading `*` (plus one space) from
  each interior line — the bug at `:3963`. Strip the common leading indent.
- Preserve interior blank lines and relative indentation; strip trailing
  whitespace per line and blank lines at the ends.
- `raw` keeps the untouched source for any consumer that wants it.

### 3.4 IR representation

- **Statements** — use the existing dormant `Stmt.comment`. Widen it to carry
  leading and trailing separately:
  ```python
  comment  : Optional[str] = None          # leading  (already declared)
  comment_trailing : Optional[str] = None  # new
  ```
- **Everything else** — add to `ir.Base`:
  ```python
  doc : Optional[str] = None
  ```
  One field on the root covers `Function`, `DataTypeComponent`, `Field`,
  `DataTypeStruct` and anything added later. `Base` is `kw_only`, so no
  positional-argument breakage. `visitDefault` iterates `dc.fields` and ignores
  non-`BaseP` values, so traversal is unaffected.
- **Locations.** Plumb `Base.loc` in the same pass. It is the same AST accessor
  at the same call sites, and `pssc/targets/validate_calls.py:34` already
  documents that `Function.loc` / `ExprCall.loc` are always `None` today. Doing
  it separately would mean touching all of `ast2ir` twice.

### 3.5 Rewritten and synthesized statements

Not every IR statement corresponds to one PSS statement. `reg_rmw.py` expands a
field write into read-modify-write; offset functions get folded; constructors and
accessors are synthesized outright.

Rule, applied uniformly:

- An expansion carries the source comment to **the first** emitted statement and
  to no other. `reg_rmw.py:413,463,473` already does exactly this for `loc` —
  follow that precedent.
- A synthesized construct with no PSS origin gets **no** comment. It does not get
  an invented one.
- Never duplicate a comment across two emitted statements. A reader seeing the
  same sentence twice will assume the generator is confused, and will be right.

---

### 3.6 Where the implementation differs from the design above

Recorded as applied, so §3 is not read as the final word.

1. **`trailing_comments` sits on `ScopeChild`, not `Scope`.** `ExecScope`
   derives from `ISymbolScope`, not `IScope`, so a field on `Scope` could not
   hold the dangling comments at the end of a *function body* -- the case that
   motivated it. One field on `ScopeChild`, beside `comments`, covers every
   construct that has a body.
2. **Orphans attach to the construct they precede**, carrying
   `CommentPlacement_Orphan`, rather than being pushed onto the enclosing
   scope. They keep their own location and stay adjacent to what a reader would
   associate them with. `trailing_comments` is then exactly one thing: comments
   at the end of a block with no following construct.
3. **One `Comment` per token, not per run.** A run of three `//` lines is three
   `Comment` nodes. Each keeps its own location -- the stated reason for
   preferring a comment model over the single `docstring` string -- and an
   emitter that re-emits each as `// ...` reproduces the original block anyway.
4. **A trailing comment documents its own declaration** when no leading comment
   does, so `rand int a; // bytes` sets `docstring` to `bytes`. A leading run
   always wins. This is `sphinx-pss`'s stated convention and it has a test for
   it; the docstring is derived from the same partition as the comment list, so
   both had to agree.

### 3.7 Defects found in the process

Each was a silent wrong answer, not a missing feature, and each is now pinned
by a test.

| Where | Defect |
|---|---|
| `PSSLexer.g4:189` | `SL_COMMENT` is lexed as `'//' .*? '\r'? ('\n'\|EOF)`, so a `//` comment **contains its own line terminator**. Counting that newline puts the comment on the following line, which makes a blank-line-detached note look adjacent to whatever follows -- the exact case the blank-line rule exists to prevent. |
| `AstBuilderInt.cpp` `build()` | The token stream buffers only as far as the parser's lookahead has reached. A *trailing* comment is to the right of its construct and is frequently not buffered yet, so a forward scan finds nothing. `m_tokens->fill()` up front, gated on the flag. Leading comments never needed it, which is why the docstring path never noticed. |
| `visitProcedural_data_declaration` | Pushes statements straight into the exec scope instead of returning through `mkExecStmt`, so `int s = 0;` got neither a location nor a comment while every other statement kind did. |
| `visitAttr_field`, `visitConst_field_declaration`, `visitComponent_data_declaration` | All three parse a qualifier (`rand`, `static const`, `mutable`, an access modifier) *outside* the `data_declaration` that builds the field. The inner rule looks left of the **type** and finds the qualifier sitting beside it, so `rand int len;` never saw its doc comment. This is the first item in `sphinx-pss`'s 3.0.3 version-floor message. |
| `visitEnum_declaration`, `visitExtend_stmt` | Enum items are pushed onto the enum's item list directly, never through `addChild`, so they had no location and no docstring. |
| `processDocStringMultiLineComment` | Did not strip the `*` gutter, despite a loop that looked like it did. Subsumed by `normalizeComment()`. |
| `processDocStringSingleLineComment` | Concatenated every preceding `//` token with no adjacency test, so blocks separated by blank lines merged and a trailing comment documented the *next* construct. Both functions are deleted. |

The last four rows are the three defects `sphinx-pss`'s version floor names.
That floor -- `pssparser >= 3.0.3` against 3.0.2 installed -- had been failing
the whole `sphinx-pss` suite at import, so none of its 301 tests had ever run
here. With Phases 1-2 applied the version is 3.0.3 and 297 pass.

---

## 4. Phase 1 — pssparser: capture all comments

Files: `ast/coretypes.yaml`, `src/AstBuilderInt.{h,cpp}`, `src/AstBuilder.{h,cpp}`,
`src/include/pssparser/IAstBuilder.h`, `python/core.pyx`, `python/pssparser/{core,decl}.pxd`.

1. **Schema.** Add `Comment`, `CommentPlacement`, `ScopeChild.comments`,
   `Scope.trailing_comments` per §3.2. Rebuild — pyastbuilder regenerates the C++
   headers and the Cython layer, so `getComments()` appears in Python for free.
2. **Capture core.** Replace `addDocstring()` with `attachComments(node, tok)`:
   - `getHiddenTokensToLeft(idx, 11|12)` as today, merged and sorted by token
     index so `//` and `/* */` interleave correctly (today they are compared only
     by last line, `:3865`).
   - Partition: a comment starting on the same line as the **previous on-channel
     token** is that construct's *trailing* comment, not this one's leading.
   - Of the rest, keep the contiguous run ending on the line immediately above
     the construct — a blank line cuts it. Everything earlier is an orphan.
   - Normalize per §3.3.
   - Derive `docstring` from the leading run so existing behaviour is bit-identical
     for the cases the current tests cover.
3. **Statements.** In `mkExecStmt()` (`:5054`), after the statement is produced,
   set its location from `ctx->getStart()` (missing today, worth having on its
   own) and call `attachComments()`. Covers nested `if`/`while`/`foreach` bodies
   automatically, since those are built through the same funnel.
4. **Trailing comments.** Attach at `addExecStmt()` (`:5084`) and at the
   declaration `addChild` overloads, resolved on the *next* token fetch. Simplest
   correct implementation: at capture time, look right for a comment on the same
   line as the construct's end token and claim it.
5. **Orphans.** Push to the enclosing `Scope.trailing_comments`. Captured for
   completeness; nothing emits them in this plan.
6. **Fix `:3963`.** The `*`-stripping bug, subsumed by `normalizeComment()`.
7. **New API.** `setCollectComments(bool)` / `getCollectComments()` on
   `IAstBuilder`, mirrored into `core.pyx` and the `.pxd` files.

**Build note.** This is a C++ + Cython change; the extension must be rebuilt
(`ivpm-build` backend, `scripts/gen_ast.py` runs as part of it). Not a
Python-only edit, and `pssparser`'s version floor will need a bump —
`sphinx-pss` already pins `>= 3.0.3` (`pss-doc-comment-plan.md` §6).

### Tests (pssparser)

New `tests/python/source_references/test_comments.py`:

| Case | Assert |
|---|---|
| leading `//` run above a statement | one leading comment, exact text |
| leading `/** */` above a statement | `*` prefixes stripped, indent normalized |
| blank line between comment and statement | orphan, **not** leading |
| `x = 1; // note` | trailing on *that* statement, not leading on the next |
| two blocks separated by a blank line | two comments, not one merged blob |
| comment inside `if`/`while`/`foreach` body | attaches to the nested statement |
| comment as last thing in a block | `Scope.trailing_comments` |
| interleaved `//` and `/* */` | source order preserved |
| `setCollectComments(False)` | `comments` empty, everything else unchanged |
| existing `test_docstrings.py` | **unchanged and green** — the compatibility gate |

Add a `sphinx-pss` smoke run (`packages/sphinx-pss/tests/`) as an integration
gate; it is the one existing downstream consumer.

---

## 5. Phase 2 — Python surface

Files: `pssparser/python/pssparser/parser.py`, `pssc/src/pssc/frontend.py`.

1. `Parser(collect_comments: bool = False, collect_docstrings: bool = False)`.
   The builder is created lazily in `_mkBuilder` (`parser.py:28`); construct it
   in `__init__` instead so the flags can be set before the first `parse()`. Keep
   the reuse-across-calls behaviour that comment documents — it exists so
   compile-time expressions can see prior source units.
2. `pssc/frontend.py` passes `collect_comments=True` unless `--no-comments`.

### Tests

- `Parser(collect_comments=True)` round-trip on a snippet; comments present.
- Default construction leaves them empty (no behaviour change for other callers).
- Multi-file `parse()` then `link()`: comments survive linking **and `extend`**
  — verified informally already, must become a standing test since the whole
  model depends on it.

---

## 6. Phase 3 — IR and `ast2ir` (the missing middle)

Files: `zuspec-ir-core/src/zuspec/ir/core/{base,stmt}.py`, `pssc/src/pssc/ast2ir.py`,
`pssc/src/pssc/reg_rmw.py`.

1. **IR fields** per §3.4: `Base.doc`, `Stmt.comment_trailing`.
2. **Statement plumbing.** `_translate_statement` (`ast2ir.py:2114`) is a single
   dispatch — read the AST node's comments there and stamp the returned IR
   statement. One site covers every statement kind and every nesting level.
   `_translate_stmt_body:2359` needs nothing.
3. **Declaration plumbing.** `ir.Function` construction sites (`:400`, `:2081`,
   and the register-model sites `:3333-3480`), components, fields, structs. The
   generated register model has no hand-written PSS comments today — it is
   generated from `src/rdl` — so those sites will mostly stamp `None`. Carrying
   RDL `desc` text through PeakRDL into the generated PSS is a **separate**
   follow-up, noted in §10.
4. **`loc` plumbing** at the same sites (§3.4).
5. **Rewrite rule** (§3.5) applied in `reg_rmw.py` and the offset-folding path.

### Tests

New `pssc/tests/.../test_comment_propagation.py`, parse→IR only:

- statement leading/trailing comment appears on the right IR statement;
- comment on a statement nested two levels deep;
- function doc lands on `ir.Function.doc`;
- component / field / struct-field doc lands on `Base.doc`;
- RMW expansion: comment on the **first** emitted statement, absent on the rest,
  **never duplicated**;
- synthesized ctor/accessor: `doc is None`;
- `--no-comments`: IR identical to today's, field-by-field.

An IR round-trip test (serialize → deserialize → compare) guards the generic
`dc.fields` serialization assumption.

---

## 7. Phase 4 — SystemVerilog emission

File: `pssc/src/pssc/targets/sv/lower_progseq.py`.

| Site | Change |
|---|---|
| `_BodyEmitter.stmts:397` | before each statement's lines, emit its leading comment as `// ...` at the statement's own indent |
| `_BodyEmitter.stmt:403` | append a trailing comment to the statement's last line |
| `emit_export_api:809` | `/** ... */` above each `pure virtual task` prototype |
| `_operation_defs:978` | same doc block above each `virtual task` implementation |
| `emit_component:1056`, `emit_subcomponent_class:1013` | component doc above the class |
| member decls `_member_decls:911` | field doc as a trailing `// ...` |

The doc block goes on **both** the interface prototype and the implementation.
The interface class is the API surface a caller reads; the implementation is what
someone debugging reads. Duplication across those two is intentional and is the
one exception to §3.5's no-duplication rule.

Mechanics: a shared `emit_comment(text, indent, style)` helper, since Phase 5
needs the identical logic. Multi-line comments re-indent to the emission point.
`*/` inside comment text is escaped before it is wrapped in a block comment.

### Tests

- golden-file test: one operation carrying comments at file, function,
  top-level-statement and nested-statement level → exact expected SV;
- `--no-comments` reproduces today's golden output **byte for byte** (this is the
  regression gate for the whole plan);
- generated SV still compiles: existing `dfm run op-model-sv` (**139 types**) and
  `dfm run check-nonblocking` (**136 types**), run **from the repo root** — see
  `pss-doc-comment-plan.md` §4 for why the directory matters;
- a comment containing `*/`, and one containing a UTF-8 character, both survive.

---

## 8. Phase 5 — C emission

File: `pssc/src/pssc/targets/c/lower_progseq.py` — structurally identical:
`stmts:1106`, `stmt:1112`, prototype `:1453`, definition `:1564`.

**Decision taken: both.** The doc block goes on the header prototype (the API a
caller reads) *and* on the definition (what you are looking at when debugging),
matching SV. Statement comments only exist in the definition.

The C output is uniformly `/* */`, not `//`. C11 permits either and the
emitters share one implementation, but every comment this target emitted before
was a block comment, and a generated file that mixes the two for no reason
reads as two generators. `targets/comments.py` selects the style; a `//`
fallback handles text containing a close-comment marker, which cannot be
wrapped in a block comment at all.

C++ (`cpp_progseq_tgt.py`) follows the same shape and should be done in the same
pass if the C work lands cleanly.

### Tests

Mirror of Phase 4's golden tests, plus the existing `op-model-c` build.

---

## 9. Phase tracking

| # | Phase | Status | Gate |
|---|---|---|---|
| 1 | pssparser: comment capture | ☑ | 30 new comment tests, `test_docstrings.py` unchanged, full suite 2085 green |
| 2 | Python surface | ☑ | 7 round-trip tests through `link()` and `extend`; `--no-comments`; sphinx-pss 297 green |
| 3 | IR + `ast2ir` | ☑ | 9 IR-level propagation tests; IR empty under `--no-comments` |
| 4 | SV emission | ☑ | `--no-comments` byte-identical; `op-model-sv` **139 types**; `check-nonblocking` **136 types**; verilator lint clean |
| 5 | C emission | ☑ | `--no-comments` byte-identical; `op-model-c` builds; `gcc -std=c11 -Wall -Wextra -Werror` clean |
| 6 | Docs | ☑ | §10 |

Whole-suite state after the work: `pssparser` 2085 passed, `pssc` 1546 passed,
`sphinx-pss` 297 passed. The remaining failures are listed in §9.5 and are
unrelated.

### 9.1 The regression gate, as measured

`--no-comments` reproduced the pre-comment output **byte for byte** for both
targets -- `wb_dma_c_pkg.sv`, `pssc_reg_pkg.sv`, `wb_dma.h` and every core
header. That is what made "only comments moved" a measured claim, and it is
what the first-run diff should be reviewed against, since every generated file
changes at once.

**The invariant was then deliberately weakened, once**, by the declaration
spacing in §9.2: a blank line before every function declaration, documented or
not. That is a formatting change to the generated code and applies with or
without comments, so `--no-comments` no longer matches the pre-comment baseline
exactly. Measured against it: **19 differing lines in the SV and 17 in the C,
every one of them blank** -- zero non-blank differences.

The durable form of the check is therefore *no non-blank differences*, which is
stronger than byte-identity in the way that matters (it cannot be satisfied by
code that merely reformats) and survives intentional whitespace changes.

With comments, `wb_dma_c_pkg.sv` goes from 519 to 1574 lines and `wb_dma.h`
from 317 to 821. Nearly all of the growth is the model's own prose.

### 9.2 Blank lines around declarations

Two rounds of review feedback, both about the generated code being readable
rather than about the comments themselves.

1. **A doc block must not butt against the declaration above it.** Worst in an
   interface class, which is nothing but prototypes: with no blank line the eye
   has no break between the end of one signature and the start of the next
   one's prose.
2. **An undocumented function needs the same break.** Keying the blank line off
   the *doc block* left `notify_irq()` -- which has no doc comment -- butted
   against the prototype above it. The separation belongs to the declaration,
   not to its documentation.

So `comments.blank_line()` is called before every function declaration, and the
doc block, if any, goes after it. Definitions already had it, from the existing
blank after each `endtask` / closing brace; only the two prototype lists were
affected.

**Tightly-coupled generated groups are separated from their surroundings but
not internally** -- a sub-component's `ch()`/`ch_size()` pair, the
`_init`/`_create`/`_destroy` lifecycle, the per-register accessor one-liners.
Those are one member's plumbing rather than API entries with their own
contracts, and spacing each apart would bury the operations among them.

Statement comments and register struct-member comments still sit directly
against the code they document. That is how they read in the PSS source, and
spacing every statement apart would be worse than the problem.

### 9.3 A hazard this created for existing tests

Two tests in `pssc` failed, both structural assertions over generated text, and
both for the same reason: **the model's prose discusses the code it sits beside**.
`transfer_list_start` explains in a comment why it does *not* call
`write_fields`, so `assert "write_fields(" not in body` matched the explanation.
The C target's `wait_hint` explains what `notify_irq()` does on a profile that
has it, so `has_not=["wb_dma_notify_irq"]` matched that.

Neither was a defect in the generated code. Both were fixed by searching the
code rather than the raw text -- `tests/progseq/codetext.py`. Worth knowing
before writing the next `not in` assertion over generated output; and note that
several tests legitimately assert on the *generator's own* comments, so
stripping comments cannot be the blanket default.

### 9.4 Build loop (pssparser)

Three steps, and the middle one is not optional:

```
cd packages/pssparser/build && ninja install     # NOT bare `ninja`
cd .. && python setup.py build_ext --inplace     # only when ast/*.yaml changed
```

`core.pyx` loads the C++ library by searching `build/lib`, `build/lib64`,
`build/bin`, `build/src` **in that order**, so a bare `ninja` -- which writes
only `build/src/libpssparser.so` -- leaves Python running whatever stale copy
is in `build/lib`. That copy was five days old when this work started, so every
"rebuild and re-test" cycle silently tested the old parser. `ninja install`
refreshes `build/lib`.

The Cython step is only needed for AST schema changes; a pure `.cpp` edit is
picked up by the `ninja install` alone, since the extension dlopen's the
library rather than linking it.

### 9.5 Pre-existing failures, unrelated to this work

Confirmed present before these changes and left alone:

- `pssparser` `tests/python/corpus/test_corpus.py::test_whole_model_parses` --
  every error is `unknown type 'wb_dma_regs_pkg'`. The test derives its file
  list from `src/pss`'s `pss.FileSet`, which yields 23 files and does not
  include the *generated* register package the model moved to in `cde0902`.
  A missing input, not a parse defect.
- `sphinx-pss` (4 of 301): the `pss` Pygments lexer is not registered, the docs
  build that depends on it, and `pssparser.get_stdlib_files`, which
  `sphinx-pss` calls and this tree does not define.

---

## 10. Phase 6 — documentation

| Doc | Change | |
|---|---|---|
| `pssparser/docs/comments.rst` | new page: the two knobs, the three placements, the blank-line rule, normalization, and the qualifier case | ☑ |
| `pssparser` version | `3.0.2` → `3.0.3`, the floor `sphinx-pss` was already pinned to | ☑ |
| `pssc/docs/progseq.rst` | a *Comments* section: on by default, per-language style, what does not propagate and why that is the mechanism | ☑ |
| `pssc/docs/cli.md` | `--no-comments` in the option table, plus a section on when to use it | ☑ |
| `docs/wb_dma_operation_model.md` | §8.1, the before/after on `set_auto_restart` — the clearest statement of what this bought | ☑ |
| `pss-skills/.../pss-coding-guidelines` | new rule 9: the blank line is significant, and the prose is a deliverable | ☑ |
| `docs/pss-doc-comment-plan.md` | its §6 dependency is resolved; its §2 convention is now load-bearing for generated output, not only for Sphinx | ☑ |

`pss_to_sv_user_guide.md` was left alone: it documents the `sv` target, which
is a different projection and does not go through the progseq emitters.

---

## 11. Risks

1. **Comment misattribution is worse than no comment.** A comment on the wrong
   statement actively misleads. This is why the adjacency fixes are in Phase 1
   rather than deferred, and why the trailing-comment case has its own test.
2. **Diff blast radius.** Every generated file changes on the first run. Land
   Phase 4 as its own commit with the `--no-comments` byte-identical check as the
   proof that only comments moved.
3. **pssparser rebuild.** C++ + Cython + a version floor. Anything consuming a
   pinned pssparser wheel needs the bump before Phase 2 is usable.
4. **`sphinx-pss` regression.** It is the only existing `docstring` consumer.
   Keeping `docstring` derived from the same leading run, plus its smoke test in
   Phase 1's gate, is the mitigation.
5. **Generated-PSS blind spot.** ~~The register model comes from `src/rdl`, so it
   carries no PSS comments and this plan gives it none.~~ **Closed — see §12.**

---

## 12. Follow-up: SystemRDL `desc` (applied)

The register model is generated in full from `src/rdl`, so §11.5 recorded it as
a blind spot. It turned out to be three-quarters built already.

### 12.1 Only `desc` is worth carrying

SystemRDL has two ways to say something about a register field, and they are
not equivalent:

- **`//` and `/* */`** — lexical, and discarded by `systemrdl-compiler` before
  any exporter sees the design. Carrying them would mean repeating the
  hidden-token-channel work of Phase 1 inside a third-party compiler.
- **`desc`** — a *property*, part of the elaborated model, defined by the
  standard as the documentation slot.

`desc` is the one to carry, and because it is a property it says what it
documents. **None of Phase 1's hard part recurs**: no blank-line rule, no
leading/trailing partition, no adjacency heuristics. Those exist only because a
PSS comment carries no marker naming its subject.

### 12.2 What was missing

| Link | Before |
|---|---|
| RDL `desc` → peakrdl-pss IR | already done — `layout.py:118`, and in `signature.py`'s change-detection tuple |
| peakrdl-pss IR → generated PSS | **missing**: `field_comment` in `templates/utils.pss` emitted every field of `row` except `row.desc` |
| generated PSS → zuspec IR | already done, by Phases 1–3 |
| zuspec IR → SV / C | **missing**: `lower_reg_model.py` synthesized its own `/* [0:0] */` and never read the field |

`EnumMember.desc` had the identical collected-and-dropped defect.

### 12.3 What changed

- **`peakrdl_pss/prose.py`** — `desc_lines()` reflows a `desc` to the output's
  width. A `desc` is a paragraph; the newlines and indentation in the `.rdl`
  are that file's line-wrapping, so they are reflowed rather than transcribed.
  Paragraph breaks survive, being the one piece of structure a `desc` reliably
  carries. Registered as a Jinja filter, so the IR keeps the property's text as
  the model states it and presentation stays in the exporter.
- **`templates/{reg_struct,enums}.pss`** — the `desc` above the member, the
  existing mechanical facts (`[0] sw=rw hw=r reset=0x0`) beside it.
- **`ir.Base.doc_trailing`** — declarations gained the second slot statements
  already had. A register field is the canonical both-at-once case, and §3.1
  used `rand bit[32] src;   // CHn_A0` as its trailing example all along;
  collapsing the two would have dropped whichever lost.
- **`ast2ir`** — `ir.Field` fills both slots from `ast_comments` rather than
  `ast_doc`'s leading-else-trailing.
- **`lower_reg_model.py` (SV and C)** — emit `f.doc` above the member and
  `f.doc_trailing` beside it, falling back to the synthesized bit range when
  there is no trailing comment. That fallback is what keeps `--no-comments`
  byte-identical rather than merely comment-free.

### 12.4 Why it was worth doing

The longest `desc` in `wb_dma_ch_regs.rdl` is a datasheet-versus-silicon
divergence — the datasheet marks ERR as ROC, the implemented model does not
clear it on read. The RDL header states recording such divergences in `desc` as
a standing convention. That convention previously terminated in the `.rdl`
file; it now reaches both back ends.

It also retires a three-way duplication. The same field was described three
times, none derived from the others: the PSS said `// [0] sw=rw hw=r reset=0x0`,
the C independently said `/* [0:0] */`, and the SV said nothing. There is now
one statement, from the RDL, and no way for the copies to disagree.

### 12.5 Gates

`--no-comments` byte-identical on both targets; `op-model-sv`/`op-model-c` 139
types; `check-nonblocking` 136; verilator lint and `gcc -std=c11 -Wall -Wextra
-Werror` clean. Suites: peakrdl-pss 507, pssc 1546, pssparser 2085, sphinx-pss
297.

One golden expectation changed, reviewed line by line: `encode.pss` gained
`// disabled`, the single `desc` in that fixture. **Note for the next person:**
`tests/util/assert_golden` gates rewriting on `os.environ.get(...)`, which is
truthy for the string `"0"` — running with `PEAKRDL_PSS_UPDATE_GOLDEN=0`
*updates* the goldens. The harness is otherwise sound; it fails correctly and
does not self-update.

### 12.6 Not done

- **Register- and addrmap-level `desc`.** `RegType` carries no `desc` field.
  This design has no `reg { }` blocks at all, so there is nothing to carry
  today; it is a small addition when one appears.
- **The checked-in model snapshot.** `pssc/examples/op_model/` still holds the
  pre-`desc` register package, so pssc's end-to-end tests do not see the new
  comments. The register-struct emitters are covered by unit tests instead;
  a `scripts/sync_op_model.py` run would close it.
