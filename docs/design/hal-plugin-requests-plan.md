# Requests from a downstream HAL plugin: implementation, test and documentation plan

Source: `requests.md` (repo root, untracked, received 2026-10-21). As with the
earlier integration reports, this file does not name the requester or their
IP. The requester is called **the HAL plugin** here: a level-2 pssc target,
`derives_from = "op-model-c"`, that turns an op-model component into a
stateless C HAL. The reference model is called **the two-window example**. It
is a root component with two register groups bound in a `solve function
initialize(addr_handle_t a, addr_handle_t b)`, and exported target functions
that do `write_fields` and return an external enum.

The requests span three repos: pssparser (PP-1..6), zuspec-ir-core (ZIR-1..2)
and pssc (PC-1..9). Every claim was checked against pssparser `f6389e6`,
ir-core `17953ab` and pssc `06fc98d`. §1 records what was found, §2 the
rulings needed before work starts, §3 the phased plan, and §4 the tests and
docs per phase.

## Status

| Phase | Content | State |
|-------|---------|-------|
| 0 | Rulings R-H1..R-H10 (§2) | **done** 2026-10-21: R-H2, R-H6, R-H7, R-H8, R-H9 ruled as recommended; the rest stand as recommended |
| 1 | pssparser: PP-4, PP-6, PP-1, PP-2, PP-3, PP-5; release | **committed** 2026-10-21 (pssparser `c904f33`, pyastbuilder `fb6577c`, both on master, unpushed). Open: tag/release pssparser 3.5.0 and pyastbuilder (needs user OK). See §3 phase 1 notes |
| 2 | ir-core: ZIR-1 (ZIR-2 deferred, R-H6 (a)) | **committed** (ir-core `e1523a6` on main, unpushed) |
| 3 | pssc: walker skip-list (prerequisite), PC-1 annotations into the IR | **committed** (pssc `21a1bea` on construct-test-audit, unpushed); guide section deferred to phase 6 |
| 4 | pssc analysis: PC-2 bindings, PC-4 provenance, PC-3 state uses | **committed** (pssc `21a1bea`). Suites green: unit 2587, progseq 2984 (goldens unchanged), compliance 925 |
| 5 | pssc C backend: PC-7, PC-5, PC-6, PC-8 (+ c_names) | **committed** 2026-10-21 (pssc `f55f9d9`..`5fdf4fc` on construct-test-audit, unpushed). Suites green: unit 2592, progseq 3005 (goldens unchanged). Guide section (Level 2) deferred to phase 6, which builds the fixture it quotes; see §3 phase 5 notes |
| 6 | End-to-end: a HAL-shaped fixture plugin, differential bus trace | **committed** 2026-10-21 (pssc `655842d`..`81b222f`, ir-core `c6e294c`; unpushed). Suites green: unit 2633 (incl. the 18 plugin-integration tests, which had been skipping here), progseq 3005 (goldens unchanged). See §3 phase 6 notes |
| 7 | Docs sweep, release, reply | **release commits made** 2026-10-21, nothing pushed or tagged: docs sweep done (it also found the requester's annotation name in phase 1 and 3 examples, now `gen_pkg::block_s`); deferred items in `future-projects.md`; reply drafted (`requests-reply.md`, repo root, untracked like `requests.md`). Suites green: pssc unit 2633, progseq 3005 (goldens unchanged); pssparser 9266; ir-core 107. Next: push and tag in the order below (needs the user's OK), then send the reply |

The requester's test files (temporary files on their side) no longer exist on this
machine. Phase 6 builds its own generic model rather than waiting for copies.

**Vocabulary rule (user, 2026-10-21).** Everything pssc ships or tests uses
generic names: no term from the requester's model, macros or platform
(their register, field, enum or macro names, or their word for a register
window). Phase 6 renamed what earlier phases had borrowed (the extern-enum
test, the composed-write test, a manifest example, this plan's wording, an
ir-core serializer test). `requests.md` itself stays untracked.

---

## 1. Findings, per request

Verdict key: **OK** means the claim holds as stated. **Partly** or
**Corrected** means the code differs from the claim; the note says how.

### pssparser

| Id | Verdict | Notes |
|----|---------|-------|
| PP-1 effective values | OK | `Annotation` has only `type`, `parameters`, `is_standalone` (`ast/annotation.yaml`). The "existing merge" (`python/pssparser/waivers.py:233-290`) is in pssparser itself and is no merge: it matches by spelled name and reads literal strings only. `TaskEvalExpr.h` folds constants but is C++-only and has no enum-item result. `TaskFindPathElem` already enumerates base and `extend annotation` fields for the linker (`TaskResolveRefs.cpp:6248-6256`). |
| PP-2 type checks | OK | Reproduced: wrong-typed values and a duplicate `.name` give 0 errors. Only unknown-field (`:6278`) and non-constant (`:6310`) are checked. Wider gap: `struct s { int x = "str"; }` is accepted too. |
| PP-3 doc after annotation | OK | Reproduced on functions and fields. `attachPendingAnnotations` records the annotation's start token (`AstBuilderInt.cpp:6874`, set at `:710`); `docstringAnchor()` (`:6941`) returns it, so `addDocstring` (`:6566`) looks only before the `@`. |
| PP-4 package-scope enum | OK, cause corrected | Reproduced at file scope and in a package. The cause is `AnnotationCollector::visitSymbolEnumScope` (`TaskResolveRefs.cpp:6064`): it calls `own(i)` on the *symbol scope*, while the annotations are on `i->getDecl()`. `getTarget()` is unset on purpose (`TaskBuildSymbolTree.cpp:410-424`). Fix: a one-line change. |
| PP-5 export | Partly stale | The warning already has its own waivable ID: **LINT301 `exported-instance-function`** (`TaskResolveRefs.cpp:5267`; PSS120 is a retired alias). It can be waived in `.pssparser.toml` today. Open: drop it for the root (R-H4). `export_of` does not exist. |
| PP-6 small fixes | OK, worse than reported | `Annotation`/`AnnotationParam` end locations are never set (`AstBuilderInt.cpp:687,693`). The stub bug is in **pyastbuilder** (`pyext_gen_pyx.py:124` enums, `:210` flags). Both emit `auto()`, so the stub's *flag* values are also wrong (1,2,3 vs 1,2,4,8). |

### zuspec-ir-core

| Id | Verdict | Notes |
|----|---------|-------|
| ZIR-1 annotation slot | OK | `Base` (`base.py:45`) is `@dc.dataclass(kw_only=True)` with `eq=True`. The serializer walks fields generically. The deserializer auto-registers exported dataclasses, so `Annotation` must be in `__all__`. Nothing pickles IR. **Naming clash:** `Arg.annotation` is already the parameter's *type* (`stmt.py:15`). |
| ZIR-2 extern enum | OK | `DataTypeExtern` (`data_type.py:137`) is a component subclass, used only by dataclasses/be-sv. |

### pssc

| Id | Verdict | Notes |
|----|---------|-------|
| PC-1 annotations dropped | OK | pssc never calls `getAnnotations()`. IR build points: component `ast2ir.py:1685`, action `:1966`, struct `:3523`, enum `:4243` (no loc, unqualified name), field `:4328`, function `:4414`, import function `:1220`, exec `:1438` (merged at `:1431`), extend `:1502/:1563`. No shipped or test `.pss` uses an annotation, so goldens cannot move. `override-surface.json` lists only `@overridable` *methods*; a free-function reader belongs in `extension-stability.md` §1. |
| PC-2 binding params | OK, plus a defect | `_set_handle_paths` (`group_binding.py:116`) drops `call.args`. **Defect found:** C (`c/lower_progseq.py:2968`) and Python (`py/backend.py:1111`) call `bindings([comp])`, so they never see a parent's `s.a.set_handle`, while `check` sees the whole model. The implicit binding can therefore disagree between the gate and the emitters. `group_binding` has no unit test. |
| PC-3 state uses | OK | `_callees` (`field_writes.py:89`) is private; `error_strings.py:24` reaches into it too. Two more call walks exist (`pkg_functions.reach`, `comp_inherit._self_calls`). There is **no language-neutral register-access recognizer**: C `_reg_call` and Py `_reg_call` are language-bound. `reserved_access.reserved_prefix` (`:66`) is the neutral path walk to generalise. |
| PC-4 field list | OK | `_reduce_call` (`reg_rmw.py:367`) builds a fresh `ExprCall`; `_or_all` merges constants, so `{1, x}` loses the constant field's identity. The rationale is plan §1.1 (`reg-masked-access-plan.md:168-198`): the four spellings must give identical IR, enforced by `test_reg_ir_equivalence.py`. `ExprCall` has no metadata slot, but every node has `provenance` (`base.py:71`), which survives `dc.replace`. |
| PC-5 resolver/hooks | OK, two corrections | No `write_masked` reaches a backend, only `write_val_masked`. Under the default **reg_map** link style the `render_reg_*` hooks are never called (`c_progseq_gen.py:155,167`). **Funnel hole found:** `_reg_map_access` (`lower_progseq.py:1699-1743`) spells `pssc_read{N}`/`pssc_write{N}`/`pssc_write_masked{N}` itself, and `test_bus_spelling_has_one_home` matches only `pssc_[rw]\d\|pssc_bus(`, so it misses them. Only SV recovers field names today, and it does so by reverse-engineering the mask (`sv/reg_field_names.py`, `FieldNamer`/`unplace`). |
| PC-6 extern types | OK, corrected | The pair rule is *enforced*: overriding `emit_api_types` alone is refused at registration (`overridable.py:155`). `CEnums.item` (`lower_progseq.py:332`) holds the dtype but passes only the name to `enum_item(enum_name, item)`, which is a **stable** style member, so its signature cannot simply change. C++ and Python already get the dtype. PSS has no extern-enum notion; `import class` is refused (`ast2ir.py:1320`). |
| PC-7 publish emitter | OK | `_BodyEmitter` `:1391`, `_op_signature` `:958`, `(void)_self` `:3087`, `_model_call` `:2206` (handle threaded `:2224,:2237`), `lower_impl` `:3123`. `ctor_emitter_cls` (`:2809`) derives from whatever emitter class is installed. **Exports-driven default linkage would change output:** `offset_fold.pss` exports only root functions, so its sub-component's `read_stat` would turn `static`. The WB DMA goldens are unaffected because that model has no exports. |
| PC-8 lifecycle | Partly | `_lifecycle_impl` (`:2912`) is called inside the per-component loop (`:3153`); its prototypes are mixed into `_sig_all` (`:2873`). Moving it into its own section would reorder output and break the goldens, so it must be a hook at the same position. Seam files can already be dropped through `core_file_names` (public, `c_progseq_tgt.py:241`). The `#include` lines are part of `include_order` (`style.py:350-360`). |
| PC-9 | No upstream work | It is satisfied by PC-3. |
| c_names (not raised) | Gap | `c_names.py` spells operation names itself (`:101-106`) and always lists lifecycle names (`:90-100`). A plugin that renames functions (one operation name in two components) escapes the clash check; one that drops `_init` gets a false clash. |

**Two points in the request that need no work.**
- The status-enum return needs no return-mapping hook: the PSS function already returns the external enum and writes `return <item>;`.
- One field-set macro per field does not change the bus trace. They modify a local between one read and one write, the same trace pssc's masked write gives, *provided pssc keeps ownership of the read-modify-write shape* (§3 phase 5, design D-5).

---

## 2. Rulings (phase 0)

Each ruling below comes with a recommendation. The phase that needs it is in
brackets. On 2026-10-21 the user accepted the recommendation for R-H2, R-H6,
R-H7, R-H8 and R-H9. The other five were not raised separately; their
recommendations are the working default unless reopened.

- **R-H1 Annotation values come from pssparser only** [1, 3]. pssc does not
  fold annotation values itself, even in the interim. *Recommend yes*
  (fix-at-source): PP-1 lands first, and pssc raises its pssparser floor.
  *Not raised separately; the recommendation is the working default unless reopened.*
- **R-H2 Unset field with no default** [1]. The LRM (7.13) is silent, and
  an unset data field takes its type's default. *Recommend a **warning** with
  its own LINT ID*, not the requested error. Wrong type and duplicate
  parameter are errors; they are LRM assignability. An integer too wide for
  its field is an error, as for any constant initializer. A repeated
  annotation type on one element is the requested warning (LINT409
  `duplicate-annotation`).
  **Ruled 2026-10-21: as recommended.**
- **R-H3 Doc comment in both positions** [1]. *Recommend:* the one nearer
  the declaration wins, with a LINT410 `ambiguous-doc-comment` warning on the
  other. A plain `//` comment is not a doc comment.
  *Not raised separately; the recommendation is the working default unless reopened.*
- **R-H4 LINT301 for the root** [1]. pssparser cannot know the root; it is
  a pssc notion. *Recommend no change.* The plugin waives LINT301 in its
  `.pssparser.toml`. Answers the PP-5 half.
  *Not raised separately; the recommendation is the working default unless reopened.*
- **R-H5 IR field name and equality** [2]. *Recommend* `Base.annotations`
  with `compare=False, repr=False`, so no existing `==` moves. Document the
  `Arg.annotation` (type) vs `annotations` (metadata) clash; do not rename
  `Arg.annotation`.
  *Not raised separately; the recommendation is the working default unless reopened.*
- **R-H6 How a type is declared extern** [2, 5]. Options:
  (a) a policy hook `is_extern_type(dtype)` only, with the plugin reading its
  own annotation;
  (b) a pssc-shipped `pssc_pkg::extern_type_s` annotation;
  (c) ZIR-2 `extern_name` in the IR, set by (b).
  *Recommend (a) now.* Keep (b)+(c) in `future-projects.md` until a second
  user needs it. Extern covers enums and structs alike.
  **Ruled 2026-10-21: as recommended.**
- **R-H7 Field provenance on the reduced write** [4]. This relaxes plan
  §1.1 to "args identical, provenance advisory". *Recommend yes*, as
  `provenance`, not a new IR node: a new node would mean an ir-core kind,
  four emitter hooks and every `write_val_masked` recogniser. The value
  captured is the source expression before `_place`, with its resolved
  `FieldSlice`. A backend may use the field list to spell the
  modify step, never to change the number or order of bus operations.
  **Ruled 2026-10-21: as recommended.**
- **R-H8 Who owns a masked write's shape** [5]. *Recommend:* pssc composes
  read → per-field set (or mask/merge) → write, and a plugin spells three
  primitives: read-into-local, field-set-on-local, write-from-local. This
  keeps the AGENTS.md rule that a policy decides spelling, never the read
  inside a masked write. The alternative is to publish a whole-statement
  hook and give up that rule for plugins.
  **Ruled 2026-10-21: as recommended.**
- **R-H9 How much of the C body emitter to publish** [5]. *Recommend:*
  rename it to `CBodyEmitter` and mark only the register, value-field and
  `model_call` hooks `provisional`. `stmt_*`/`expr_*` and `BodyWalker` stay
  unpublished, because printing is to move to be-* (D15 in
  `compound-actions-design.md`). Also fix `extension-stability.md:24`, which
  lists `body_walker` as public and contradicts AGENTS.md.
  **Ruled 2026-10-21: as recommended.**
- **R-H10 Default linkage** [5]. *Recommend:* the default stays `extern`
  for everything; `linkage()` is opt-in. Exports-driven linkage would
  change `offset_fold` output and is not the request's to impose on every
  C user.
  *Not raised separately; the recommendation is the working default unless reopened.*

Smaller questions to settle in review, not before work:
- PC-2: does a parameter bound to two groups (aliased window) count as pure? *Recommend yes; aliasing is legal.*
- PC-3: are field-level reads in scope? *Recommend register-level `regs_read` now and field level later.*
- PC-3: is `repeat (n)` with a runtime `n` unbounded? *Recommend yes; only a `while` with no constant bound is flagged.*
- Should a standalone annotation (`@a;`) attach to the enclosing scope node? *Recommend yes.*
- Does an overriding function inherit its base's annotations? *Recommend no;* an inherited (non-shadowed) function keeps its own.

---

## 3. Plan

The landing order follows the requester's §4, with two prerequisites added
(the walker skip-list, and closing the funnel hole). One commit per bullet
group. Each repo's commit carries its own tests and docs.

### Phase 1: pssparser (release 3.5.0)

1. **PP-4.** In `AnnotationCollector::visitSymbolEnumScope`, also call `own(i->getDecl())`. Check `extend enum` the same way.
2. **PP-6a.** `setEndLoc` on `Annotation` (`ctx->stop`) and `AnnotationParam` (`item->stop`). Use `setEndLoc`, not `setExtent`, so existing carets do not widen.
3. **PP-1.** C++ `AnnotationValues` (new `src/AnnotationValues.{h,cpp}`):
   - enumerate effective fields via `TaskFindPathElem`'s field walk (base, then `extend`, then own);
   - fold each value with `TaskEvalExpr`. An enum value is the `EnumItem` from the ref target. A value that does not fold gives `value=None`; the linker already errors on it.
   - bind it through `PyParserUtils`/`core.pyx`;
   - the thin Python module `python/pssparser/annotations.py` provides `find(node, qname|AnnotationDecl)`, matched on the resolved type target, never the spelling, and `effective(ann) -> {name: Value(expr, value, field, is_default, location)}`. `location` is the param's, or the field initializer's for a default.
   - `waivers.py` keeps its spelling match on purpose (lint_pkg needs no import); its comment says why.
4. **PP-2.** In `TaskResolveRefs::visitAnnotation`:
   - assignability via `TaskExprTypeCat::compatible`, plus a width check of the folded value;
   - a per-annotation name set for duplicates;
   - unset-no-default via the PP-1 field list.

   IDs: one new **PSS150 "annotation parameter"** with message patterns (type, width, duplicate). R-H2 decides whether unset is LINT411. The repeated annotation type is LINT409. Regenerate `catalog.json` (`scripts/gen_catalog.py`) and update `tests/data/core-codes.lock`.
5. **PP-3.** `addDocstring` keeps both anchors (declaration, then annotation). `extractLeading` is tried nearest-first; `attachComments` covers both spans. LINT410 is raised per R-H3. Touch the call sites `:6285-6418`.
6. **PP-5.** `pssparser.refs.export_of(fdef) -> list[ExportFunction]`. It is a list because a function can be exported twice. It indexes `ExportFunction` by `resolveSymbolPathRef` target. Pure Python.
7. **PP-6b.** In pyastbuilder, emit real enum and flag values in the stub. Release pyastbuilder, regenerate and commit `ast.pyi`.
8. Release: a `CHANGELOG.md` `## Unreleased` entry per item, then tag `v3.5.0`. Rebuild here (`cmake --build build && cmake --install build`; see memory `pssparser-rebuild-workflow`).

**Phase 1 progress notes (2026-10-21).**
- PP-4 done. The cause was wider than the enum scope: the *global* scope had
  no annotation pass at all, and an `extend`'s own annotations were never
  collected anywhere (`extend struct`/`extend component` failed in a package
  too, and an annotation on a global `import` did as well). Three fixes in
  `TaskResolveRefs.cpp`: `checkScopeAnnotations(root)` in `resolve(root)`; the
  collector takes an enum scope's `decl` and an extend scope's `target`.
  Tests: 7 new `SCOPE_CASES` plus `test_annotation_type_resolves_in_each_scope`
  (PSS002 for a bad field, never PSS042); 12 fail without the fix.
- PP-6a done: `setEndLoc` on `Annotation` and `AnnotationParam`;
  `test_annotation_and_parameters_have_an_end_location`.
- PP-1 done, **in Python, not C++** (deviation from item 3):
  `python/pssparser/annotations.py` (`find`, `effective`, `fields`, `fold`,
  `qualified_name`, `Value`, `EnumValue`). Reasons: `TaskEvalExpr` folds
  neither strings nor enum items, and the Cython extension reaches
  `libpssparser` only through `IFactory`'s virtual interface, so a C++ helper
  would need a new factory method and value bindings. The linker records each
  parameter's field binding (`param.getName().getTarget()`), and fields from
  `extend annotation` are in the type scope's children, so Python has all it
  needs. Folding: literals, strings, bools, unary/binary integer ops (PSS
  truncating `/` and `%`), `?:`, `static const` and enum-item references;
  the result is coerced to a `bit[N]`/`int[N]` field's width. A cast does not
  fold yet (`value=None`). The API takes the parser or root first
  (`find(root, node, "pkg::t")`), as `refs.occurrences` does: AST nodes do not
  reach the root. A default's `location` is its field declaration's (an
  expression carries no location). Tests: `tests/python/parsing/test_annotation_values.py` (16).
- PP-2 done (`TaskResolveRefs.cpp`: `checkAnnotationParamValue`,
  `checkAnnotationUnset`, `checkRepeatedAnnotations`). PSS150 covers type,
  literal width and a repeated parameter name. The type rule is stricter than
  `TaskExprTypeCat::compatible`, by design: string/number disjoint, `bool`
  takes only `bool`, an enum takes only its own items (7.5.1). The width check
  covers only an integer literal, negated or not, and accepts one that fits
  as signed *or* unsigned (`bit[8] m = -1` is fine). Folding `~0` without its
  8.7.2 context would misfire, and pssparser has no width check on ordinary
  initializers to align with. LINT409 and LINT411 are core-emitted, since
  2026.2, and **not in `minimal`**: `MINIMAL_IDS` is now the core-emitted rules
  that have a `PSS` alias. Without that, the default profile would change for
  every user. `lint_pkg::waive.reason` now defaults to `""`, so a waiver with
  no reason reports only PSSP205, not LINT411 as well. Bookkeeping updated:
  `core-codes.lock`, `catalog.json` (`scripts/gen_catalog.py`), `markers.rst`,
  `lint-rules.rst`, the `tests/lint/<rule>/` fixtures, the representative
  messages, the pin test, and the TypeScript `lint-parity.json` fixture. The
  TypeScript tests themselves did not run (no emscripten or `node_modules`
  here).
- PP-3 done. `docstringAnchor` records the first token after the annotations
  (`m_doc_after_annotation_tok`). `addDocstring` prefers a comment there,
  using `DocCommentExtractor::extractAfterAnnotation`, which does not treat a
  comment on the annotation's line as trailing it. `attachLeadingComments`
  puts it in `getComments()`. LINT410 is raised in `attachPendingAnnotations`
  (`checkDocCommentPositions`), whether or not doc comments are collected,
  because the CLI does not collect them. It counts only doc-marked comments
  (`///`, `/** */`). Tests: `test_doc_comments.py` (15 new).
- PP-5 done: `refs.exports(root)` and `refs.export_of(root, fn)`. An export
  inside `extend component` is reachable twice (in the extend scope and merged
  into the type), and is de-duplicated. Tests: `tests/python/linking/test_export_of.py`.
- PP-6b done in **pyastbuilder** (`pyext_gen_pyx.py`: an enum value is its
  position and flag i is `1 << i`; test `tests/unit/test_pyext_stub_values.py`).
  pssparser's `ast.pyi` was regenerated with it (only value lines and the
  `auto` import changed), and `test_ast_stub.py` now compares every stub value
  with the runtime. pyastbuilder needs a release (0.0.10) before pssparser's
  build depends on it. Until then, the committed stub is right, and the test
  catches a regeneration with the old generator.
- Docs: `annotations.rst` (checks, reading values, doc comments; also removed
  the stale claim that the 3.0 paren form is accepted, since it is rejected),
  `doc_comments.rst`, `refs.rst` (exports, and waiving LINT301 for R-H4), the
  skill references `source-tools.md` and `refs.md` (their examples run as
  tests), and `CHANGELOG.md` `## Unreleased`.
- Suite: 9541 passed. The only failure is an existing one:
  `error-suite/.gitignore` ignores `compare.*`, so
  `error-suite/pss_errsuite/compare.py` was never committed, and three
  error-suite test modules plus one integration test fail to import it. This
  was not caused by this work; report it upstream.

### Phase 2: zuspec-ir-core

1. **ZIR-1.** In `base.py`:
   - `@dc.dataclass class Annotation(type_qname: str, values: Dict[str, Any], loc: Optional[Loc])` is a plain dataclass, **not** a `Base`, so `visitDefault` does not demand a `visitAnnotation`;
   - add `Base.annotations: List[Annotation] = field(default_factory=list, compare=False, repr=False)`;
   - export it in `__all__`;
   - values are Python scalars, enum items `(enum_qname, item_name)`, lists and dicts.
2. ZIR-2 only if R-H6 picks (c).

### Phase 3: pssc, annotations into the IR (PC-1)

1. **Prerequisite: one walker skip-list.** Today five walkers keep hand-copied lists of what not to descend into (`field_writes`, `body_kinds`, `array_methods`, `index_bounds`, `comp_foreach`). Four more descend into `loc`/`provenance` (`group_binding._walk`, `reserved_access._calls`, `validate_calls._walk_calls`, `pkg_functions.calls_in`).
   - Add one `targets/ir_walk.py` with `NON_CHILD_FIELDS = {"loc", "doc", "doc_trailing", "provenance", "annotations"}` and a `children(node)` helper, and use it in all of them.
   - `comp_inherit._rewrite` is the one deliberate exception: it must rewrite inside provenance (phase 4). Comment it.

   This must land before PC-1 and PC-4 add anything walkable to nodes.
2. **ast2ir.** Add one helper, `_annotations(node) -> List[ir.Annotation]`, built on `pssparser.annotations.effective` (R-H1). `type_qname` comes from the linker's resolved target. Call it beside every existing `loc`/`ast_doc` point in the PC-1 row above:
   - merged exec blocks concatenate their annotations;
   - `extend` appends to the extended type's list;
   - a standalone annotation attaches to its enclosing scope;
   - an unresolved type (LINT403) is skipped, as the LRM says.

   While touching `_translate_enum`, give enums a `loc`. Leave their qualified name for a separate change (it moves `_qual` diagnostics).
3. **comp_inherit.** An inherited function keeps its annotations (`dc.replace` already does). The `_pss_super_*` copies keep them. An override does not inherit them. `export_action.py:303`, which builds a `Function` from an action, copies the action's annotations.
4. **Public reader.** `src/pssc/annotations.py`:
   - `of(node, qname) -> Optional[dict]` raises if the type is applied twice (then use `all_of`);
   - `all_of(node, qname) -> List[dict]`;
   - `qname` must be qualified. An unqualified spelling raises with the candidates; spellings resolve differently by scope, and the reader must not re-resolve.
5. **Manifest.** Add `"annotations": [{type, values}]` on components, operations and enums. It is an added field, so `VERSION` is not bumped. The plugin's JSON writer could then read the manifest instead of the IR; worth it because the manifest exists so a consumer never parses generated code.

**Phase 2 and 3 progress notes (2026-10-21).**
- ZIR-1: an enum-item value is a dataclass, `AnnotationEnum(enum, item,
  value)`, not the planned tuple. The serializer turns a tuple into a list, so
  a tuple would not round-trip. `Annotation` is a plain dataclass (not
  `Base`); `visitDefault` passes over it (tested). The `Arg.annotation`
  docstring now says it is a type, not a PSS annotation.
- Walker skip-list: `targets/ir_walk.py` (`NON_CHILD_FIELDS`, `children`),
  used by all nine walkers named above; `tests/unit/test_ir_walk.py` checks
  that the list equals `Base`'s fields and that no hand-kept tuple remains. A
  subagent's inventory of all ~55 generic walkers found no others that
  misbehave on `annotations`; several read through `provenance`, which is a
  separate, existing concern. `comp_inherit._rewrite` is commented as the
  deliberate exception.
- pssparser additions made for this phase: `annotations.on`, `.standalone`,
  `.type_of`. A further PSS042 bug was found and fixed: an `import function`
  in a package or the global scope had its annotations left unbound, because
  the symbol tree keeps the function's scope, not the `import` statement.
  `TaskResolveRefs::checkImportFunctionAnnotations` walks the physical
  package and global scopes for these, and three `SCOPE_CASES` cover it.
- ast2ir: `_annotations(ctx, *nodes)` and `_ir_annotation`, called for
  component, action, struct, enum (which now also gets a `loc`), `extend
  enum`, `extend` of a type, field, function (definition plus prototype),
  import function, and exec (merged blocks concatenate). A standalone
  annotation has a `_BODY_ELEMENTS` row (`_body_annotation`), because before
  this pssc *refused* one in a type body. `test_type_body_registry.py` has
  rows for `Annotation`, `AnnotationDecl` and `AnnotationParam`.
  `export_action` gives an entry its action's annotations, then its body's.
- comp_inherit needed no change: an inherited function keeps its
  annotations, an override has its own, and `_pss_super_*` keeps the base's
  (tested).
- Tests: `tests/unit/test_annotations.py` (13),
  `tests/progseq/test_manifest.py` (+2).
- Docs: `extension-stability.md` §1 row, `op-model-manifest.md`, and an
  AGENTS.md section "Source annotations". **Deferred to phase 6:** the
  `custom-generator-styles.md` section "Reading annotations", because its
  blocks must quote a fixture the suite runs.

### Phase 4: pssc analysis (PC-2, PC-4, PC-3)

1. **PC-2.** `group_binding`:
   - `_set_handle_paths` yields `(path, arg, fn)`.
   - `Binding` gains, all defaulted:
     - `params: Mapping[Tuple[str,...], Optional[str]]`, keyed by path, not dotted string. The value is the ctor parameter name only for a bare `ExprRefLocal` naming one, otherwise `None`.
     - `init_fn`.
     - `pure_bindings`: every statement is `<group>.set_handle(<param>)`, with no control flow and no indexed path. Aliasing is decided per the review question.
     - The implicit single-group rule fills `params` too.
   - Add a model-level `handle_params(model)` that composes through a sub-component's `initialize(arg)` (bare-parameter arguments) and through `_pss_super_<base>_initialize`. This maps every group in the tree to a **root** ctor parameter.
   - Cache it as `OpModel.bindings` (a `cached_property` writing `__dict__`). Point `check_group_bindings`, C and Python at it; this **fixes the gate/emitter disagreement** found above (own commit, own regression test).
   - Manifest: per component `"groups": [{"name", "param", "implicit"}]`, an added field.
2. **PC-4** (after R-H7).
   - Add `reg_rmw.RegWriteProvenance(ir.Provenance)`:
     - `pass_name = "reg_rmw"`;
     - `fields: Optional[Tuple[RegFieldWrite, ...]]`, where `RegFieldWrite = (slice: FieldSlice, value: Expr, mask: int)`, captured before `_place`;
     - `None` for a raw `write_val_masked`, or a `write_masked` given a whole struct.
   - Attach it in `_reduce_call`. The `args` are unchanged, so every backend's output and every golden is byte-identical.
   - `--reg-rmw=expand` copies the provenance onto the `write_val` it produces.
   - `comp_inherit._rewrite` rewrites inside it.
   - Move SV's `FieldNamer`/`unplace` behind a shared `targets/reg_field_names.py` that **prefers the provenance** and keeps reverse-engineering only as the fallback for `fields=None`. That leaves one way to get a field list.
   - Amend `reg-masked-access-plan.md` §1.1 and AGENTS.md "Where a register lives".
3. **PC-3.** New `targets/state_uses.py`, language-neutral:
   - `resolve_reg_path(comp, path, types) -> RegRef(sub_path, group, reg_segs, accessor)`, generalised from `reserved_access.reserved_prefix`, which is then rebased on it. `accessor` is looked up in `reg_layout.collect_accessors`, never recomputed.
   - A read/write direction table next to the REG rows in `call_legality.py:126` (`write_val_masked` = read+write).
   - `callees(fn, comp, ctx)` is promoted from `field_writes._callees`. It adds package functions (`pkg_functions.callee`) and imports. `field_writes.target_code` and `error_strings` move onto it, leaving one call graph.
   - `StateUses(groups, regs_read, regs_written, fields_written, fields_read, imports_called, callees, unbounded_loops, executor_delegated)` is transitive, with a visited set by `id(fn)` (recursion-safe). `fields_written`/`fields_read` are **component** fields (the request's meaning). Register fields come from PC-4 provenance as `reg_fields_written`.
   - `OpModel.state_uses(fn)` is memoised. `OpModel.unpacked_signature(fn) -> [(group_path, root_param)]` is ordered by the root ctor's parameter index. It raises a located `Refusal` for a used group with no bare parameter.
   - `extension-stability.md` §1 gets a "Model queries" row, `provisional`.

**Phase 4 progress notes (2026-10-21).**
- PC-2: `Binding.params`, `init_fn`, `pure_bindings`;
  `group_binding.handle_params(model)`; `OpModel.bindings` (a
  `cached_property` over `comp_dtypes` plus `classes`), `OpModel.binding(comp)`
  and `OpModel.handle_params()`. The gate, C (`_lifecycle_impl(binding=...)`)
  and Python (`_bind_groups`) all read the one table. A parameter is written
  `self.p` in the IR, as a field is, so `_bare` looks a name up parameter
  first. Composition follows `sub.initialize(...)` and any self call,
  including `_pss_super_*`; it walks the constructor first, so a `super` copy
  it calls is not marked "bound elsewhere".
  **Defect found and fixed:** a parent's `sub.g.set_handle(p)` made both the C
  and the Python emitter raise `ValueError` ("STRUCTURAL ... not recognised").
  It now renders as `_self->sub.base_g = p` (C, with the reg_map pointer too)
  and `self.sub._pss_base_g = p` (Python). Python requires the parent to have
  constructed the sub-component first: an unconstructed sub-component stays
  `None` by design. Tests: `tests/unit/test_group_binding.py` (13, including a
  driven Python read at the parent-bound address and a C compile).
  Manifest: `groups` on each component.
- PC-4: `reg_rmw.RegFieldWrite(slice, value, mask)` and
  `RegWriteProvenance(fields)` are attached in `_reduce_call`. The value is
  the source expression; the mask is the bits written for that field (a
  partial mask is kept); `fields` is `None` for a raw `write_val_masked` or a
  whole-struct `write_masked`. `--reg-rmw=expand` copies it onto the
  `write_val`. comp_inherit rewrites inside it (tested). `check_reduced`
  raises if a `write_field(s)` lost its fields. The shared
  `targets/reg_field_names.py` has `written_fields`, `whole_fields`,
  `unplace` and the language-neutral `FieldNamer`. SV's namer subclasses it,
  and SV takes the field list from the provenance when every field is written
  whole (LSB first, values still through `unplace`), so SV output and goldens
  are unchanged. Tests: `test_reg_ir_equivalence.py` (`_norm` ignores
  provenance, plus 6 new tests).

### Phase 5: pssc C backend (PC-7, PC-5, PC-6, PC-8)

1. **Close the funnel hole first (own commit).**
   - Move `_reg_map_access`'s `pssc_read{N}`/`pssc_write{N}`/`pssc_write_masked{N}` into `MemAccess` (`reg_ptr_read/write/masked_write`).
   - Widen `test_bus_spelling_has_one_home` to `pssc_(read|write)(_masked)?\d`. Show it fails before the move.
   - Goldens are unchanged.
2. **PC-7 signature, handle, linkage.** New `COpModelBackend` members, all `provisional`:
   - `signature(fn, ctx) -> (ret_ctype, name, [(ctype, pname)])`, used by `_sig_all`, `lower_operation`, `_solve_impl` **and `c_names`**. An empty list prints `(void)`.
   - `handle_param(fn, ctx) -> Optional[str]`, default `c_names.HANDLE`. `None` drops the parameter, the `(void)_self` and the argument in `model_call`. A rendered body that still names the handle is a located `diag.Refusal` (it reads component state), never C with an undeclared `_self`.
   - `linkage(fn, model) -> "extern" | "static"`, default `extern` (R-H10). A static operation's prototype moves into the `.c`, as `_solve_impl`'s does.

   `lower_impl` receives these as callables, as `emit_operation` already does.

   **c_names.** `declared_names` takes operation names from `backend.signature`, and lifecycle names only when `emit_lifecycle` emits them (item 5).
3. **PC-7 emitter.**
   - Rename `_BodyEmitter` to `CBodyEmitter`, keeping a `_BodyEmitter` alias for a release.
   - Publish only these hooks (R-H9):
     - `model_call(callee, args, ctx)`, the shared tail of the sibling/sub-component branches;
     - the register hooks below.
   - Add `CBodyEmitter` to `SURFACES` in `scripts/regen_override_surface.py`, and parametrize `test_manifest_matches_code` over every surface.
   - Confirm `ctor_emitter_cls` still derives correctly from a subclass.
4. **PC-5 register access.**
   - `reg_layout.RegAccess` (frozen) carries `accessor`, `comp`, `via` (sub hops), `group`, `index_exprs` (paired with `accessor.strides`), `method` (`read|read_val|write|write_val|write_val_masked`), `args`, and `fields` (from PC-4 via `reg_field_names`).
   - `reg_layout.resolve_access(comp, call) -> Optional[RegAccess]` is built on phase 4's `resolve_reg_path`, the same walk, not a third one.
   - `_reg_call` becomes `resolve_access` followed by `self.reg_access(ra, lowered, ctx)`. `lowered` carries the index and argument strings already converted to register width. The default reproduces today's text exactly, for both reg_map and accessors.
   - Masked write per R-H8 (**D-5**). pssc emits the structure:
     1. declare a temp of `reg_value_local_type(acc)`, hoisted to the top of the function through the existing struct-local machinery (`struct_locals`, `:1482`), extended to record which register a local holds;
     2. `reg_read_into(ra, temp)`;
     3. for each field, `reg_value_field_set(temp, slice, value, acc)` when `ra.fields`, else `reg_value_merge(temp, mask, val, acc)`;
     4. `reg_write_from(ra, temp)`.

     The default backend keeps today's single `pssc_write_masked32(...)` (a `reg_masked_write_inline` path), so goldens do not move. A plugin turns on the composed form by overriding the primitives. `MemAccess`'s read check then holds by construction, because pssc wrote the read.
   - Value-struct field access: `reg_value_field_get(local, slice, acc)` / `reg_value_field_set(...)` replace the `reg_style == "accessors"` branches (`:1851`, `:2329`).
   - Give `_Acc` an optional `layout: RegAccessor`, so the existing `render_reg_*` style hooks can reach the register's identity (additive).
5. **PC-8.**
   - Add `emit_lifecycle(node, ctx) -> List[str]` and `emit_lifecycle_decls(node, ctx)`, provisional, called where `_lifecycle_impl` and its prototypes are now. `[]` turns them off. Byte-identical by default.
   - Split `include_order` so `seam_includes(s)` can be dropped alone.
   - Document `core_file_names` as the way to stop copying the seam files.
6. **PC-6** (target-neutral, R-H6 (a)).
   - `StylePolicy.is_extern_type(dtype) -> bool` (default `False`) and `extern_type_name(dtype)` (default PSS name), on the base `targets/style.py`.
   - C, C++, SV and Python `lower_api_types` skip an extern type's definition. `c_enum_name`/`c_type` use the extern name.
   - Add `CStylePolicy.enum_item_for(dtype, item)`: verbatim for an extern enum, else `self.enum_item(c_enum_name(dtype), item)`. `CEnums.item` calls it. The stable `enum_item(name, item)` is unchanged; changing its signature would need the deprecation window (`extension-stability.md` §4).
   - `c_names` gathers extern type and item names as **occupied, not declared**: a clash is still refused, and `test_every_gathered_name_is_one_the_backend_emits` exempts them.
   - Manifest: `"extern": true` on an enum or struct.

**Phase 5 progress notes (2026-10-21).**
- Item 1 done (`f55f9d9`): `MemAccess.reg_ptr_read/write/masked_write`;
  `_mem_call`'s reg_map branch had the same hole and moved too. The widened
  pattern is `pssc_(read|write)(_masked)?(\d|\{)`.
- Item 2 done (`2faeeae`). Hooks take `(fn, ctx)` with `ctx` an `OpCtx`
  (also `linkage`, not `(fn, model)`: the backend has `self.model`).
  `OpCtx.api` is the backend (`DefaultOpApi` when None); `OpCtx.for_comp`
  gives a callee's context. `since='0.3'` (0.2.0 is the release commit
  already made). A static operation renders `static inline` (W45). The
  emitter's `h` is a property that raises the located `Refusal` when the
  handle is None; `_through_subs` was split (`_sub_walk`) so a handle-less
  sub-component call does not read it. `refuse_too_wide` now runs before
  `c_names.check`, which types every signature. Tests:
  `tests/progseq/test_c_op_api.py`.
- Item 3 done (`042b847`). `model_call(callee, args, ctx, handle)`: ``ctx``
  is the callee's `OpCtx`, ``handle`` the expression or None. The default
  calls the callee's `signature` name, so a rename reaches call sites. The
  constructor emitter gets an `OpCtx` too. `COpModelBackend.body_emitter_class()`
  resolves the emitter; `pssc targets --overrides` prints its members.
- Item 4 done (`e036133`). `RegAccess` has `comp`, `via` (name, index),
  `group`, `index_exprs`, `method`, `args`, `fields`. `resolve_reg_path`
  gained `owner` on `RegRef` and an `accessors=` memo. Hooks:
  `reg_access(ra, lw)` (`lw` = `LoweredRegAccess`: `acc`, `indices`, `args`,
  `sub_path`), `reg_masked_write_inline` (None composes),
  `reg_value_local` (type AND name, so a HAL gets `data`; declared at the
  function top via `function_local`), `reg_read_into`,
  `reg_value_field_set(local, fs, value, ra, lw, mask)` (value unnarrowed),
  `reg_value_merge`, `reg_write_from`, `reg_value_field_get` (a field of a
  whole `read()` only). **Not done:** a register-value struct LOCAL
  (`ctl_s v = regs.ctl.read(); v.en = 1;`) still uses the value union; the
  plan's `struct_locals` retyping is deferred (record in
  `future-projects.md`). Tests: `tests/progseq/test_c_reg_access.py`, incl.
  the WB DMA trace gate passing with every masked write composed.
- Item 5 done (`affcbc4`): `emit_lifecycle_decls`/`emit_lifecycle(node,
  ctx)`, a `pairs_with` pair; `lifecycle_decls`/`lifecycle_lines` are the
  defaults. `c_names` gathers lifecycle names iff `emit_lifecycle_decls` is
  non-empty. `CStylePolicy.seam_includes` (the tail of `include_order`);
  `seam_headers` `[]` already stopped the copies. The G8 tripwire in
  `test_builtin_as_tier_b.py` now pins the pair.
- Item 6 done (`5fdf4fc`), **C only**: `StylePolicy.is_extern_type` on the
  base class, but only op-model-c has a policy to ask; C++/SV/Python and
  `extern_type_name` are in `future-projects.md`. `CStylePolicy.enum_item_for`;
  `c_names.occupied_names`; manifest `"extern": true` via
  `OpModelTarget.is_extern_type`. Tests: `test_c_extern_types.py`.
- Docs: `extension-stability.md` §1 (surfaces, and `body_walker` removed
  from the shared-services row, reconciling it with AGENTS.md), AGENTS.md
  (memory seam, backend class, `CBodyEmitter`), `future-projects.md`
  (HAL-plugin follow-ups), the G8 note in
  `generator-style-extensions-plan.md`.
- Pre-existing, not caused here: `test_op_model_py.py::test_it_refuses_exactly_what_the_c_target_refuses_on_the_real_model`
  fails when run after some other progseq modules (e.g. `-k "golden or c_"`)
  and passes alone and in the full suite; reproduced at `b563c2c`.

### Phase 6: end to end

Add a second fixture plugin, `tests/plugins/pssc_hal_fixture/`, generic names only. It is the worked example for the whole stateless-HAL path and the test the requester's acceptance criteria map onto:
- **Model:** the two-window example, written fresh with neutral names. It has two groups, a `solve function initialize(addr_handle_t, addr_handle_t)` carrying an annotation, three exported functions (`write_fields` with a constant and a parameter; a read/poll; one calling a sibling), and an extern enum return.
- **Plugin:** `derives_from = "op-model-c"`. It:
  - reads annotations through `pssc.annotations`;
  - drops `_self` (`handle_param`) and builds the signature from `unpacked_signature`;
  - spells registers as `DEV_READ32(base, REG, v)` / `DEV_FIELD_SET(REG, F, (x), v)` / `DEV_WRITE32(base, REG, v)` through the PC-5 primitives;
  - marks the enum extern;
  - turns lifecycle off;
  - makes unexported functions `static`;
  - writes a JSON file through `emit_extra_files`.
- **Checks:**
  1. byte-exact expected output for one function, mirroring the request's example (the plugin's own golden, which is allowed because the plugin is in-tree);
  2. compile with `cc -Wall -Werror` against a header that defines the `DEV_*` macros over a memory array;
  3. **differential trace:** run each exported function against the same memory model through op-model-c and through the HAL output, and require identical bus traces. This catches a wrong offset, a lost field or an extra access, which a snapshot cannot.
  4. `assert_differs_from_baseline("op-model-hal-fixture", "op-model-c", expect_changed=[...])`.
  5. The negative path: a function reading a component field is refused with a location.

**Phase 6 notes (as built).**
- **Where.** `tests/plugins/pssc_hal_fixture/`: the plugin
  (`pssc_hal_fixture/backend.py`, target `op-model-hal-fixture`, entry point
  `hal-fixture`), its model (`model/dev.pss`: two groups `ctl`/`sts`, an
  annotated constructor, four exported functions and one unexported, an
  external `status_e`) and the platform side the test owns
  (`platform/dev_access.h` with the `DEV_*` macros, `tb_mem.c`, a tracing
  memory, and `pssc_seam.c`, pssc's function seam over the same memory for
  the baseline). Test: `tests/unit/test_hal_fixture.py` (marker `plugin`,
  installed into a temp prefix).
- **Checks 1-5 as planned**, plus a sixth: the trace comparison is run on
  two mutations of the fixture's output (a wrong array stride in the map, a
  dropped field set) and must fail on both. The trace also asserts its own
  oracle values (preserved bits of a masked write, the array element's
  address, the poll count), so two identically wrong builds cannot pass.
- **Checks map onto the request's acceptance shape**: no `_self`, the
  windows a function uses as parameters (from `unpacked_signature`), one
  field-set macro per field between one read and one write, an external
  enum with unprefixed items, unexported functions `static`, no lifecycle.
- **What phase 6 added to pssc** (both found by writing the fixture):
  `COpModelBackend.op_ctx(comp)` (provisional): an `OpCtx` outside a body,
  so `emit_extra_files` can ask `signature`/`linkage`; and
  `RegAccessor.fields`, the field layout a register map needs, so a plugin
  need not import the unpublished `reg_field_resolve`. `pssc.diag.Refusal`
  is now listed as public in `extension-stability.md`.
- **Test infrastructure.** `test_plugin_integration.py` gained
  `install_plugin(src, prefix)` and `discovering(prefix, monkeypatch)`, and a
  third installer: setuptools' PEP 517 hook called directly, on a copy of the
  plugin. The shared venv has neither pip nor `build` (and a `build/`
  directory in the working directory shadowed the `build` module), so all 18
  plugin-integration tests had been SKIPPING here; they now run and pass.
- **What the fixture leaves out, on purpose.** It handles registers of the
  root's own groups, one array index at most, and `read`/`read_val`/
  `write_val`/masked writes; anything else is a located refusal naming what
  it cannot say. A sub-component's operations would need the
  instance path of the group (`OpModel._instance_path` is private).
- **Docs.** `custom-generator-styles.md` Level 2 gained "A different API
  shape: a stateless driver layer", quoting the fixture and its model
  verbatim (this also delivers phase 3's deferred "Reading annotations"
  text); AGENTS.md names the fixture and the installers;
  `extension-stability.md` lists `op_ctx`, `RegAccessor.fields` and
  `pssc.diag.Refusal`.

### Phase 7: docs, release, reply

**Release set (found 2026-10-21).** pssc 0.2.0 was never published (PyPI has
0.1.0), and neither was zuspec-be-bc: its v0.1.0 tag's upload step failed
(PyPI's new-project rate limit, as in the 0.2.0 wave). be-bc's main lowers
`StmtIfChain` and be-sw's escapes decoded strings, so both need a release.
In order (each tag after its repo's main CI is green):

1. pyastbuilder: push master (`fb6577c`, the stub values). pssparser's CI
   fetches it from git, so no PyPI release is needed for 3.5.0.
2. pssparser **3.5.0** (`c5d1066`): push, tag `v3.5.0` (the tag is the
   version).
3. zuspec-ir-core **0.3.0** (`c439187`: `StmtIfChain`, `Base.annotations`).
4. zuspec-be-sw **0.2.1** (`37a02da`), zuspec-be-bc **0.1.1** (`0f2fab4`,
   `ir-core>=0.3.0`).
5. pssc **0.2.0**: floors `pssparser>=3.5`, `zuspec-ir-core>=0.3.0`,
   `zuspec-be-sw>=0.2.1`, `zuspec-be-bc>=0.1.1`; `docs/release-notes.md`;
   construct-test-audit fast-forwarded into main.

Verified: wheels of ir-core, be-bc, be-sw and pssc built from clean
exports hold every tracked module. Not verified here: a clean install
with pssparser 3.5.0 (it is a native build); CI's wheel jobs are the check.


- Re-read the docs touched in each phase as a whole (§4).
- Add any deferred items (ZIR-2, `pssc_pkg::extern_type_s`, field-level reads, C vtable interplay) to `docs/design/future-projects.md`.
- Release pssparser 3.5.0, ir-core, and pssc with its raised floors.
- Reply to the requester, item by item:
  - PP-5 is mostly already there (LINT301);
  - PC-2..PC-5's names and shapes as built (they differ in detail from the request: `params` keyed by path, `export_of` a list, `enum_item_for`, the RMW primitives in place of a whole-statement hook);
  - PC-9 needs nothing;
  - point at the HAL fixture as the worked example.

---

## 4. Tests and docs per phase

**Rule for every phase.** `tests/progseq/golden/` must stay byte-identical. Every change here is additive or opt-in, and a golden diff means the default changed by mistake. Address-touching phases (4, 5) also run `test_op_model_behaviour_c.py` and `test_build_wb_dma_c.py`. The full suites run at the end of each phase: unit, progseq, compliance, matrix.

| Phase | Tests | Docs |
|-------|-------|------|
| 1 pssparser | `tests/python/parsing/test_annotations_31.py`: add `package_enum`/`file_enum` to `SCOPE_CASES` (PP-4), and type/duplicate/unset/repeat markers via `assert_marker` (PP-2). New `test_annotation_values.py` (PP-1): explicit, default, inherited, `extend`, enum item, non-folding, `find` by qname and by decl. `error-suite/cases/semantic/annotation/*.pss`. Doc-comment tests (PP-3): function, field, action, both positions, standalone unchanged. `export_of` in the refs tests. `test_ast_stub.py` compares stub enum/flag values with runtime. End locations in `test_annotation_attachment.py`. | `docs/annotations.rst`, `docs/markers.rst`, `docs/lint-rules.rst`, `docs/doc_comments.rst`, `docs/refs.rst`, `docs/reference_api_docs.rst`, `skills/pssparser-api/` (new `references/annotations.md`), `CHANGELOG.md` |
| 2 ir-core | `tests/test_serializer_roundtrip.py`: an annotated enum with loc and nested values. New `test_annotations.py`: default empty and unshared; `==` ignores it. | Docstring on `Annotation`, and the `Arg.annotation` note |
| 3 PC-1 | New `tests/unit/test_annotations.py`, in the style of `test_comment_ir.py`: every node kind incl. exec/init, `extend`, merged exec, inherited vs overridden function, `_pss_super_*` copy, standalone, unresolved type ignored, unqualified qname refused. Walker skip-list: a test that every walker uses `ir_walk.children` (grep-style, like the funnel test). Manifest annotations in `test_manifest*.py`. | `extension-stability.md` §1 row "Annotation reader"; `custom-generator-styles.md` "Reading annotations", quoting the fixture (checked verbatim); `op-model-manifest.md`; AGENTS.md one line under "A refusal says where" or a new short section |
| 4 PC-2/3/4 | New `tests/unit/test_group_binding.py`: bare param, expression → `None`, param bound twice, implicit, through a sub's `initialize`, `super.initialize`, under `if`, `s[i].a`; plus a regression for the C/Py-vs-gate disagreement (a parent binding a sub's group, where it fails today). `test_reg_ir_equivalence.py`: `_norm` ignores provenance, and a new test pins the provenance per spelling (`{1, x}`, partial mask, whole struct, comp_inherit-rewritten value, expand mode). SV field-name tests stay green on the shared module. New `tests/unit/test_state_uses.py`: direct, sub's register, `self.sub.f()`, `super.f()`, mutual recursion, package fn, import, `while` poll, executor model; field-write refusals unchanged on the shared call graph (`test_m_components.py`). | `reg-masked-access-plan.md` §1.1 amendment; AGENTS.md "Where a register lives" (provenance, `OpModel.bindings`, `state_uses`); `extension-stability.md` "Model queries"; `op-model-manifest.md` `groups` |
| 5 C backend | Funnel: the widened test fails before the move and passes after. `test_c_backend_class.py`: `handle_param=None` (no `_self`, `(void)` for an empty list); a field read refused with a location; a static op absent from the header; a renamed `signature` in header and `.c`; a `CBodyEmitter` subclass reaching ctor bodies; `emit_lifecycle → []`. `test_c_names.py`: renamed-op clash caught, dropped-lifecycle no false clash, extern item clash refused but not "emitted". `test_override_surface.py` over every surface, new names in the tier-B needs set. `test_mem_access.py`: the composed masked write still reads. `test_enum_item_types.py`: an extern enum on C/C++/SV/Py. The existing fixture plugin's differential still holds. | `docs/override-surface.json` (regen script); `custom-generator-styles.md` Level 2: signature/handle, register primitives, extern types (quoted from the fixtures); `extension-stability.md` §1: reconcile the `body_walker` row with AGENTS.md, new provisional members; AGENTS.md "The C backend is a class", "The C memory seam" and "One body walk" (CBodyEmitter published hooks) |
| 6 E2E | §3 phase 6 checks 1-5, under the `plugin` marker like `test_plugin_integration.py` (installed into a temp prefix) | The guide's Level 2 points at the HAL fixture as the worked example for a stateless API |

## 5. Risks

- **Phase 5 is the large one.** `lower_progseq.py` is huge, and the register and value-field branches are spread across it. Land item 1 (funnel) and item 2 (signature) separately before item 4, and keep goldens running per commit.
- **pssparser releases gate pssc.** PC-1 cannot land until 3.5.0 is installed here. Watch for the PyPI-copy recurrence (memory `pssc-source-deps-installed-as-copies`).
- **Provenance is now load-bearing for plugins.** Any later pass that rebuilds a `write_val_masked` call must carry it. Add an assertion in `check_reduced` that a reduced call from `write_field(s)` has non-`None` fields.
- **Published surface growth.** Every hook here is `provisional`. The admission rule in `extension-stability.md` requires a real extension; the in-tree HAL fixture is it, and the external plugin is the second user.
