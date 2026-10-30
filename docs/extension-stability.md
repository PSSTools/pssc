# Extension stability and deprecation policy

This page says what an extension author may depend on, what each promise is
worth, and what happens when pssc needs to break one. It covers the surfaces in
[`custom-generator-styles.md`](custom-generator-styles.md).

The short version: **if it is not marked, it is not a promise.** Every public
method of a Python class is overridable, which is the problem this policy exists
to solve rather than the feature it provides. Without a marked set, "the
override surface" is whatever an extension author happened to reach for, pssc
cannot change any of it without breaking somebody, and nobody can tell which
methods were designed to be wrapped from which merely happen to be callable.

---

## 1. What is public

| Surface | Where | How it is pinned |
| --- | --- | --- |
| The target contract | `pssc.targets.Target`, `pssc.targets.op_model.OpModelTarget` | `Target.PSSC_TARGET_API`, checked at registration |
| Style policies | `pssc.targets.style.StylePolicy` (incl. `is_extern_type`), `pssc.targets.c.style.CStylePolicy` (incl. `enum_item_for`, `seam_includes`) | `tests/progseq/test_style_policy.py`, `test_c_extern_types.py` |
| The backend override surface | methods marked `@overridable` on `COpModelBackend` (an operation's `signature`, `handle_param` and `linkage`, and `op_ctx` to ask them from elsewhere; `plan(model, s)`, called by `prepare` before any of them, with `operations(model)` listing what they are asked of; the lifecycle pair `emit_lifecycle_decls`/`emit_lifecycle`; the sections) and on the C body emitter `pssc.targets.c.lower_progseq.CBodyEmitter` (`model_call` and the register hooks `reg_access`, `reg_masked_write_inline`, `reg_value_local`, `reg_value_local_scope`, `reg_read_into`, `reg_value_field_set`, `reg_value_merge`, `reg_write_from`, `reg_value_field_get`; all provisional), and its class attribute `partial_field_writes` (provisional). `LoweredRegAccess.args` is rendered on first read: a hook that composes from `ra.fields` never renders it. The emitter's other methods are not published | `docs/override-surface.json` (checked in), one entry per class |
| Call legality | `pssc.targets.call_legality` — `Entry`, `Disposition`, `Ctx`, `register_extension`, `COMMON` | the Tier-1 contract test |
| The shared services | `pssc.targets.reg_layout` (incl. `RegAccess`, `resolve_access`, `RegAccessor.fields`), `pssc.targets.sections`, `pssc.targets.comments` | their own test files |
| Diagnostics | `pssc.diag.Refusal(text, node...)`: the model's error, raised from an override, reported at the node's PSS location (or the statement being rendered) | `tests/progseq/test_c_op_api.py`, `tests/unit/test_hal_fixture.py` |
| Model queries (provisional) | `OpModel.bindings`, `.binding(comp)` (a `group_binding.Binding`: `params`, `pure_bindings`, `conflicts` -- a group path the constructor binds to two or more parameters, which makes it impure -- and `unbound_params`, the constructor parameters that give no group a handle, directly or through a `super.` or sub-component constructor), `.handle_params()`, `.state_uses(fn)`, `.unpacked_signature(fn)`, `.reachable(roots, follow=None)` (the functions reachable from `roots`, each once, callee-first; `follow(call)` keeps an edge; recursion is a located `Refusal` naming the cycle); `pssc.targets.state_uses` (`StateUses`, `state_uses`, `RegRef`, `resolve_reg_path`, `Call`, `callees`, `package_callees`, `reachable`); `pssc.targets.reg_field_names` (`written_fields`, `whole_fields`) | `tests/unit/test_group_binding.py`, `test_state_uses.py`, `test_reg_ir_equivalence.py` |
| Statement-form register reads (provisional) | `OpModelTarget.reg_read_form` (`"expression"`, the default, or `"statement"`: every read reaches `CBodyEmitter.reg_read_into` as a statement of its own, in evaluation order); `pssc.targets.effect_seq` (`sequence`, `reg_reads`, `TEMP`, the attribute marking a local the pass introduced); `pssc.targets.ir_walk` (`walk`, `children`, `child_fields`, `rebuild`, `Transformer`); `pssc.targets.ir_ops` (`is_short_circuit`, `is_bitwise`, `is_comparison`, `swapped`, `short_circuit(e)`: `(BoolOp, operands)` for `&&`/`||` as `ExprBin`, the canonical form, or `ExprBool`); `pssc.targets.reg_value_locals` (`bind`, `LocalBinding`: a value-struct local held as the register's word when the target spells `read()` as it, through the register hooks) | `tests/unit/test_reg_read_form.py`, `test_effect_seq.py`, `test_ir_walk.py`, `test_ir_ops.py`, `test_reg_value_locals.py`, `test_hal_fixture.py` (bus-trace read order, value-struct locals) |
| Composed masked writes (provisional) | `pssc.reg_rmw.RegWriteProvenance.fields` (`RegFieldWrite`: `slice`, `value`, `mask`), `pssc.reg_rmw.field_writes` (the one partial-field policy); a `write_masked` value-literal field the mask leaves out is never written, and one whose value calls a function is refused on every target | `tests/unit/test_hal_fixture.py`, `tests/progseq/test_c_reg_access.py` |
| C names (provisional) | `pssc.targets.c.c_names`: `C_KEYWORDS` (the one list), `check_identifier(name, node, reserved=())` (a located `Refusal` for a non-identifier, a keyword, a name C reserves, the generated code's own `RESERVED`, or `reserved`), `parameter_clashes` (run by `check`: a signature parameter that is refused, repeated, a local of the body, or a type's name); `CBodyEmitter.fresh_local(stem, ctype, scope="block")`, a local no name of the body takes (`"function"`: declared at the function's top) | `tests/unit/test_c_names.py`, `tests/progseq/test_c_names.py` |
| Derived-target options | `OpModelTarget.fixed_options`, `accepted_options` (by `dest`; either set turns the policy on, inherited options in neither are refused when set), `inherited_options()`, `Target.resolve_options(opts)` (called by the driver before parsing), `Target.requires_pssc` | `tests/unit/test_option_policy.py` |
| Subset targets (provisional) | `OpModelTarget.unsupported_kinds`, `unsupported_local_types` (`{IR class name: reason}`), refused at their location by `check_body_kinds` (`body_kinds.refusals(..., unsupported, unsupported_locals)`); Tier-1 call narrowing: `call_legality.register_extension(..., subset=True)` / `OpModelTarget.legality_subset`, `subset_of_common(target)`, honoured by `testing.assert_common_tier` | `tests/unit/test_testing_kit.py`, `tests/progseq/test_call_legality.py` |
| Source annotations | `pssc.annotations` (`of`, `all_of`, `find_all(model, qname)` over every declaration, reachable or not), read from `annotations` on a component, action, struct, enum, field, function or exec block of the IR. `of` refuses a repeated type with a located `Refusal`. Values are already typed per their field declarations (pssparser PSS150): do not recheck them | `tests/unit/test_annotations.py` |
| The testing kit | `pssc.testing` | used by pssc's own suite |
| Entry-point groups | `pssc.targets`, `pssc.styles` | `tests/unit/test_plugin_integration.py`, against a really-installed plugin |
| CLI options | `pssc compile`, `pssc targets` | [`cli.md`](cli.md) |
| The manifest schema | `--emit-manifest` | `pssc.targets.manifest.SCHEMA`, versioned in the emitted document |

`pssc.targets.body_walker` is **not** listed: the walk over an operation body,
and every `stmt_*`/`expr_*` hook of an emitter, are expected to move to the
be-* emitters (`compound-actions-design.md`, D15). An extension reaches a body
through the published `CBodyEmitter` hooks above, never by overriding a node
kind's hook.

Everything else — every underscore-prefixed name, every module not listed above,
the internal shape of the IR, and the *bytes* of generated output — is private
and changes without notice.

Generated output deserves an explicit word, because it is the surface people
assume is stable. It is not. pssc's own golden snapshots exist to make a change
in generated bytes **visible and reviewed**, not to prevent it. If your build
depends on the exact spelling of a generated symbol, express that as a style
policy (Level 1), which is a promise, rather than as a `grep`, which is not.

---

## 2. The two stability levels

Marked members carry one of two levels, reported by
`pssc targets --overrides <target>` and recorded in `docs/override-surface.json`:

**`stable`** — the signature and the meaning will not change without a
deprecation window (§4). Override it freely. If you can express what you need
against `stable` members alone, your extension survives pssc's refactors without
you reading a release note.

**`provisional`** — published because a real extension needed it, and expected
to move. Override it, and read the release notes. A provisional member may
change signature in any release, but not silently: see §4.

Neither level promises that the *output* stays the same, only that the seam
does. `emit_operation` will keep being handed a function and keep returning
lines; what the default implementation puts in those lines is pssc's business
and will improve.

A member's promise applies to the **contract in its docstring**, not to its
implementation. Every marked method states what a subclass may assume and what
it must preserve; a marked method whose docstring only says what it returns is
not documented, and that is a defect to report.

---

## 3. How a member joins the surface

The admission rule, and the reason the manifest is checked in:

> A method becomes `@overridable` because **a real extension needed it** — not
> because it looked useful, and not pre-emptively.

Growing the surface takes a diff in two files (the mark, and
`docs/override-surface.json` regenerated by `scripts/regen_override_surface.py`),
so it is a decision somebody makes in a review rather than a thing that happens
when somebody adds a decorator. `tests/unit/test_override_surface.py` fails when
the code and the manifest disagree.

The rule exists because the failure mode is one-directional. A surface that is
too small produces a request: an author says "I had to copy 40 lines of pssc to
change one of them", and the answer is to mark the method they needed. A surface
that is too large produces nothing at all until the day pssc cannot make a
change — and by then the cost is somebody else's broken build, paid by a
maintainer who never chose it.

So: **if you had to reach past the published surface, say so.** That is a
finding, not a workaround to keep to yourself. The evidence a request needs is
the extension itself — pssc's own tier-B validation is an extension of under 150
non-blank lines that reaches past nothing unmarked, and an extension that cannot
meet that bar is the argument for adding a member.

New members are added as `provisional`. A member is promoted to `stable` when it
has been used, out of tree, through at least one release without its signature
needing to move.

### Pairs

Some members are two halves of one decision — an include guard's open and close
agree on a macro name; the API types and the register value unions partition one
set of declarations. Those carry `pairs_with`, and overriding one without the
other is refused **at target registration**, naming both. That check is part of
the promise: pssc will not add a pair constraint to an existing `stable` member
without a deprecation window, because doing so turns a working extension into a
registration error.

---

## 4. Deprecation window

pssc is pre-1.0 (`pssc.__version__` is `0.x`), and this policy is written to
be the one that survives 1.0 rather than a looser one that has to be replaced.

**For a `stable` member**, a breaking change — removal, a renamed method, a
changed signature, a changed meaning, or a new `pairs_with` constraint — takes
**two minor releases**:

1. **Release N.** The replacement lands and works. The old member keeps
   working and starts emitting a `DeprecationWarning` naming what to use
   instead. The release notes say so, and the member's own docstring names the
   replacement — a warning a build filters out is not a notification.
2. **Release N+1.** The old member still works, still warns.
3. **Release N+2.** It may be removed.

**For a `provisional` member**, the window is **one minor release**: it may
change in release N+1 provided release N warns, and the release notes name the
replacement. A provisional member is not a licence to break someone between two
patch releases.

**In no case** is a member removed in a patch release, and in no case does a
change ship without a working replacement available in the same release. "Copy
what pssc does" is not a replacement.

The manifest records `stability` and `since`; it has no `deprecated_in` field
because nothing has been deprecated yet, and a field with no instances is a
field that is wrong the first time it is used. Adding one is not a schema break
(a reader that ignores it is still correct), so it lands with the first actual
deprecation.

Two things are outside the window, deliberately:

* **A security or correctness defect** in a seam — a hook that hands out a wrong
  address, a check that can be bypassed. Those are fixed immediately, in a patch
  release, and the release note says what changed and why. An extension
  generating a wrong register access is not a compatibility interest worth
  protecting.
* **`Target.PSSC_TARGET_API`.** If the target contract itself has to change
  incompatibly, the integer is bumped and plugins carrying the old one are
  **refused by name at discovery**, with a diagnostic. That is the deliberate
  choice: a target that half-works produces generated code nobody can trust, so
  a clean refusal beats a plugin failing later against a `run()` signature that
  moved.
* **`Target.requires_pssc`.** A member added since a plugin's major (such as
  `resolve_options`) is not a reason to bump the integer; a plugin that needs
  one declares a floor (`">=0.3"`), checked at registration, instead of probing
  with `hasattr`.

---

## 5. What an extension author should do

* **Prefer the smallest level that works.** A style policy (Level 1) depends on
  fewer promises than a backend subclass (Level 2), which depends on fewer than
  a target of your own.
* **Own a differential test.** `assert_differs_from_baseline` states what your
  extension changes and asserts everything else is untouched. It fails both when
  pssc moves under you and when your override silently stops taking effect —
  which is the failure that otherwise ships.
* **Run `assert_common_tier` and `assert_deterministic` in CI.** They are two
  lines and they are the contract every target is held to.
* **Pin what you depend on.** `pssc targets --overrides <target>` prints the
  surface with stability and `since`; `docs/override-surface.json` is the same
  data in a file you can diff between two pssc versions.
* **Report a hole rather than working around it.** See §3.
