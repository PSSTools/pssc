
# Codex
You must prefix all commands with 'direnv exec . <command>' to get
a proper environment. (If direnv is not approved, the vendored venv at
`packages/python/` can be used directly with `PYTHONPATH=./src`.)

# Package

This is **pssc** (the PSS compiler), import root `pssc`, source under `src/pssc/`.
It was previously `zuspec-fe-pss` (import root `zuspec.fe.pss`); see
`docs/migration-from-zuspec-fe-pss.md` for the import map.

# Build and run

Focus on Python and unit tests for now.

```
direnv exec . pytest tests/unit          # fast unit suite (pytest.ini default)
direnv exec . pssc --version             # console entry point
```

If `pytest` on PATH cannot import `pssc` (this checkout's `packages.envrc` does
not export PYTHONPATH), use the vendored venv, which has pssc installed
editable along with zuspec and pssparser:

```
direnv exec . ../python/bin/python -m pytest tests/unit
```

## Golden snapshots

`tests/progseq/golden/` holds byte-exact snapshots of the operation-model
backends' output for the configurations in `tests/progseq/golden_util.py`. They
are what makes "this refactor preserves output" a checked claim rather than an
assertion (see `docs/generator-style-extensions-plan.md`).

```
direnv exec . ../python/bin/python -m pytest tests/progseq -k golden
PSSC_GOLDEN_REGEN=1 ../python/bin/python scripts/regen_golden.py [config...]
```

Regenerating is legitimate **only** alongside an intentional change to
generated output, in the same commit, with the snapshot diff reviewed as part
of that change. It is never the way to make a failing test pass during a
refactor that was supposed to preserve output — that is the one failure this
mechanism exists to catch. The `PSSC_GOLDEN_REGEN=1` guard is there to make the
act deliberate.

Snapshots prove *sameness*, not correctness: a wrong address frozen into a
golden file stays green forever. Changes touching address computation or
register access must also run the behavioural tests
(`test_op_model_behaviour_c.py`, `test_build_wb_dma_c.py`).

## The C memory seam

`pssc_r32` / `pssc_w32` / `pssc_bus(s)` are emitted from exactly one place,
`targets/c/mem_access.py` (`MemAccess`), and
`test_package_layout.py::test_bus_spelling_has_one_home` keeps it that way. If
you need a memory access in the C backend, call the funnel rather than writing
the primitive out — that test will fail otherwise. It also owns the accessor
name scheme (`<base>_read_val`, `<base>_write_masked`, …), which
`lower_reg_model` uses to DEFINE the accessors and `lower_progseq` to CALL
them; those were two hand-kept tables before.

## The C backend is a class

`targets/c/backend.py` (`COpModelBackend`) assembles the generated files;
`c_progseq_gen.generate()` only turns command-line keywords into a `CSettings`.
Two rules hold it together, and both are tested
(`tests/progseq/test_c_backend_class.py`):

* **Every `emit_*` returns text; only `generate`/`write_files` touch disk.**
  A method that writes cannot be wrapped by a subclass calling `super()`, which
  is the whole point of the class.
* **The header is exactly its `header_sections()`**, in order, each section
  carrying its own trailing blank. Use `targets/sections.py`
  (`insert_after`/`replace`/…) to change one — those raise on a name that does
  not exist, so an upstream rename surfaces instead of silently dropping a
  customisation.

Which `solve function` is the constructor comes from `model.ctor_names`, never
from `progseq_model.current_ctor_names()`. The ambient value remains only for
callers outside a compile; no emitter reads it, and
`test_ctor_name.py::test_generation_does_not_read_the_ambient_ctor_names` fails
if one starts.

## One body walk, three renderings

`targets/body_walker.py` owns the walk over an operation body; the C and SV
`_BodyEmitter`s render what it finds. A node kind is handled by a hook **named
after it** — `StmtForeach` → `stmt_foreach`, `ExprRefBottomUp` →
`expr_ref_bottom_up` — so there is no cascade to read past and no table to keep
in step with the IR. A missing hook names itself in the error.

Comment attachment happens in `BodyWalker.stmt` alone, so every nesting level
of every ported language carries its PSS prose. A statement that lowers to no
lines takes its comment with it.

The two scans are free functions, not methods: `scan_write_only` (locals the
model assigns and never reads — emitted as `(void)x;`) and
`scan_output_locals` (the local a channel `try_get` writes through, widened to
`uint64_t`). C++ still carries its own copy of the first one; it is unported.

In C a CALL is dispatched on its `Disposition` from `call_legality.py` — the
same table `validate_calls.py` gates on — via `CallDispatch`. Add a hook when
you add an entry: `test_dispatch_matches_registry` fails otherwise. SV keeps
its own `expr_call`, deliberately: its chain ends in a generic call rendering
that is correct for everything, so there is nothing for a table to decide.

`body_walker.py` is NOT part of the published override surface. `body_emitter_cls`
is marked `provisional` for exactly this kind of change.

## Where a register lives

`targets/reg_layout.py` (`collect_accessors`) is the ONE walk that says which
registers exist and at what offset: a constant from the component's base, plus
one stride per array index crossed. C's `lower_reg_model` builds its `_Acc` from
it, the Python backend builds its methods from it, and `--emit-manifest`
reports it.

One walk because an address is the one thing in a generated API a golden
snapshot can never check — a wrong offset frozen into a snapshot stays green
forever. Two backends folding offsets two ways is the worst duplication
available here. If you need something the walk does not carry, add it to
`RegAccessor`; do not re-walk.

## The Python backend

`op-model-py` (`targets/py/`, `targets/py_progseq_tgt.py`) generates ONE module:
the platform-seam Protocol, component classes, folded register accessor methods,
value classes with a bit layout, and the operations.
`src/pssc/share/py/pssc_rt.py` is the runtime it copies out — a `MemoryBus` to
bring a model up on, a depth-1 `Chan1`, and `check_import_api()`.
`pssc_rt_async.py` joins it under `--py-await async`.

`--py-await` is a TARGET OPTION, not a style: the async form publishes
`HAVE_EVENT_WAIT=true`, so it compiles a different model rather than restyling
the same one. It is the only place in the tree where `target_cfg` and the
Tier-2 legality set depend on an option — `Target.resolved_target_cfg_for(opts)`
and `PyProgSeqTarget._register_legality()` are the two hooks, and both are
re-published per run.

Three properties are load-bearing and all three are tested:

* **The generated module imports nothing outside the standard library** unless
  the model has channels. A generated driver gets copied onto a lab machine;
  one file needing no install still runs there. The seam is structural (the
  module generates its own `Protocol` and requires nothing to inherit it), and
  the two base classes (`_RegValue`, `_Struct`) are emitted into the module.
* **An awaited call is never a subexpression.** `lower_progseq._awaited` hoists
  it to a statement and yields the name of its result, because `await f() &
  mask` parses and means `await (f() & mask)` — a wrong value, not an error. A
  loop whose CONDITION crosses the seam becomes bottom-tested for the same
  reason: a hoisted read in front of a `while` would be read once and spun on.
* **It is imported and DRIVEN by its tests**, not grepped. That is the only
  place in this repo where "is the address right" and "does the completion poll
  terminate" are asked of a running generated model without a compiler or a
  simulator — so an offset bug fails there first, in three languages' worth of
  shared code.

**Executors** (`targets/executors.py`, LRM 21.13.9.5). In a model with an
executor component, every memory primitive and register access is DELEGATED:
`self._pss_xtr.read32(h, desc)`, where `_pss_xtr` is the component's executor
(`set_executor` in an init block, else the parent's, resolved by `_pss_bind`
after `_pss_init`) or a `_PssDefaultExecutor` that calls the seam. Executor
classes derive from `_PssDefaultExecutor`, so an unoverridden primitive reaches
the platform by method lookup. A model with no executor renders exactly as
before. Targets without delegation (`supports_executor_delegation = False`)
REFUSE a model whose executor overrides a primitive; accesses bypassing the
override would be a model that compiles and does something else.

`self.f()` is an operation only if the component itself declares `f`
(`validate_calls.ops_for_call`): the front end writes a core-library call the
same way, and a model-wide name set made one component's `read32` override
capture every other component's `read32(h)`. A call written
`addr_reg_pkg::write32(...)` is the same function as `write32(...)`: it is
classified by its short name and delegated to the active executor -- so inside
that executor's own `write32` it recurses, as PSS says. The default
implementation is `super.write32(...)`, which ast2ir roots at
`ir.TypeExprRefSuper` (never `self`) and op-model-py renders as
`_PssDefaultExecutor.write32(self, ...)`.

**Component inheritance** (`targets/comp_inherit.py`, LRM 17.1 Table 27). The
IR keeps a type's OWN members plus a `super` named as the linker resolved it
(the SV testbench renders `extends` from that). Every op-model reader walks
own members, so `complete(ctx)` -- first thing in `build_model` -- gives each
derived user component its base's fields (first) and every function and exec
kind it does not shadow, IN PLACE (consumers compare components by identity).
Functions are VIRTUAL (user ruling): an inherited body is copied into the
derived component and rendered there, so its `f()` reaches the override on
every target with no dispatch table. `super.f(args)` is STATIC: it becomes a
call to a private copy of the base's `f` (`_pss_super_<base>_f`); a shadowed
field is two fields, the base's under the private name its bodies and
`super.a` read; `super;` in an init block calls private copies of the base's
blocks of that kind. Every base-member copy goes through `from_base`. Still
refused, for a component in the tree: shadowing a component INSTANCE, and a
differently-typed shadow that an inherited body calls.

That completed view is what C RENDERS (flattened; held to "generates
what writing it out by hand does" in `test_op_model_py_inherit.py`). Python,
C++ and SV render inheritance NATIVELY (`OpModelTarget.native_inheritance`): a
class per component type derived from its base's (`class Der(Base)`,
`class der : public base, public virtual der_if`, `class der extends base`),
emitting only what the component DECLARES (`comp_inherit.declared` -- the
members before completion, `super` intact), with the language's `super`
(`super().f`, `base::f`, `super.f`). They also emit
base types nothing instantiates (`OpModel.classes`/`base_classes`), and the
gate checks what they render: declared bodies, with `super.f` classified
against the base (`validate_calls(native=True)`). Python splits construction
(`_pss_construct`, `_pss_ctor`, `_pss_init_down`/`_up`) so each class adds its
part, and keeps a field declared by a base and a derived class under per-class
attributes behind properties (`naming.field_storage`) -- Python has one
attribute namespace per object. `test_op_model_inherit_native.py` runs every
case on all three (SV under Verilator) and requires the same trace. C is to get
a vtable.

## op-model-sv's API: exports

op-model-sv's component classes keep PSS names and PSS access: a member is
public and named as declared (`sub.a`, `ch[i].wake`), and the generated ones
take the `pss_` prefix (`pss_imp`, `pss_do_init`). The platform holds a
CONTEXT, never a component: `<root>_root #(Timp)::create(imp, <root ctor
args>)` returns `<root>_ctxt_if`, whose methods are the exported actions
(`--export-action`) and the root's EXPORTED functions -- `export target
function f;` in the root's body. Exporting an instance function is an
extension to LRM 20.4.2 (pssparser reports it as PSS120, a warning); only the
root's exports are accepted yet (`targets/export_function.py`), and nothing
else is on the API, so a model that exports nothing is an error. The WB DMA
device tree exports nothing: SV tests present it with
`tests/progseq/data/wb_dma_exports.pss` (`op_model.op_model_sv_sources`).
Design: `docs/design/sv-op-model-inheritance.md`.

Python binds each register GROUP's base separately (`naming.group_base`,
`self._pss_base_<group>`), as C and C++ do: one `_base` per component put two
groups bound to different handles at the second's address.

`self.x` in the IR is a NAME, not a field: the front end spells a parameter
the same way. Resolve it in scope order -- parameter first, then field -- as
`ExprTypes`, bc and every op-model emitter now do
(`test_param_shadows_field.py`).

**Action inheritance in an entry** (`export_action.py`, LRM 17.1, 20.1.4). A
derived action's `exec body` shadows its base's; `super;` (`ir.StmtSuper`, only
legal at the top level of an exec block) runs the base's body there, and an
action with no body runs its base's. Each base body `super;` reaches is its own
private method (`_pss_super_<entry>_<k>`, `EntryPoint.supers`), so its locals
and its `return` stay its own; the `super;` names it in `metadata["super"]`.
Anything consuming entries iterates `EntryPoint.functions`, never `.function`
alone. An action's base is recorded as the LINKER resolved it
(`ast2ir._linked_type_name`: `B` found in a base component is `base_c::B`).
A statement ast2ir cannot translate is a translation error, never dropped.

Its `PyOpModelBackend` follows `COpModelBackend`'s two rules (every `emit_*`
returns text; the module is exactly its `module_sections()`) but carries no
`@overridable` marks and is NOT a published surface. Publishing one is a
manifest edit when an extension actually needs it.

## The manifest

`--emit-manifest FILE` writes the elaborated model as JSON
(`targets/manifest.py`): components, operations with signatures, register
offsets and strides, value-struct bit layouts, and the produced files with
`generated` / `runtime` roles. It exists so a consumer never parses generated
code — a parser for generated C is a second copy of the naming rules that
nobody updates.

It is written from the `OpModel` the emitters render and AFTER `emit`, so it
cannot describe an API that was not produced. A target declares its
ABI-affecting options through `abi_settings()`; `{}` is a legitimate answer.
Bump `manifest.VERSION` only when a reader of the old shape would be WRONG —
adding a field is not a bump.

## The published override surface

A method of `COpModelBackend` that a mid-weight extension may subclass carries
`@overridable(since=..., stability=...)`, and the marked set is checked in at
`docs/override-surface.json`. Adding a mark alone fails
`test_override_surface.py::test_manifest_matches_code` — growing the surface
takes a second, human-written edit, which is the whole point: every public
method is overridable in Python, and without a marked set "the API" is whatever
somebody reached for.

```
direnv exec . pssc targets --overrides op-model-c   # what may be overridden
python scripts/regen_override_surface.py            # after an intended change
```

`stable` promises the signature and meaning; `provisional` says "published for
a real extension, expected to move". A marked method's docstring must state
its contract — a test enforces that too. `pairs_with` names a member that
cannot be overridden alone (the include guard's two halves; the API types and
the register value unions, which partition one set of declarations); taking one
half is refused at target REGISTRATION, before any model is read.

`derives_from = "op-model-c"` gives a target its ancestor's call legality, CLI
options, `target_cfg` and styles — each wired separately, because one
integration test would let two of the four silently not work.
`tests/plugins/pssc_fixture_plugin/backend.py` is the worked example: 84 lines
for an inserted section, a wrapped `emit_operation`, an extra file and a house
style. If a change of that size cannot be written short, the surface is wrong.

Extensions do not get a golden snapshot — upstream moves. Their checkable claim
is differential:

```python
assert_differs_from_baseline("op-model-acme-c", "op-model-c",
                             expect_changed=["acme_compliance", "impl"])
```

It fails on an undeclared difference AND on a declared one that did not happen;
the second half is what catches an override that silently stopped taking effect.

## Styles

`--style NAME` selects a `CStylePolicy` (`targets/c/style.py`) for the C
op-model backend, resolved from the `pssc.styles` entry-point group keyed
`"<target>:<style>"`. The default policy reproduces today's output exactly, so
every naming/layout decision in the C backend goes through it rather than being
hard-coded — add new ones there, not inline.

A policy decides SPELLING. It cannot decide an address, which registers exist,
the access-direction rules, or the read inside a masked write; those are the
model's, and `mem_access.py` raises `LegalityError` rather than asking a policy
for an access the register does not have. `tests/plugins/pssc_fixture_plugin`
ships a 43-line `AcmeStyle` that mandates house register macros — the worked
example.

## Target plugins

Third-party targets are discovered from the `pssc.targets` entry-point group.
`tests/plugins/pssc_fixture_plugin/` is a minimal real one, used by
`tests/unit/test_plugin_integration.py` (marker: `plugin`), which builds and
installs it into a temp prefix — never into your environment. It is skipped if
no installer (`pip`, or `build` + `setuptools`) is available.

`PSSC_NO_PLUGINS=1` skips discovery entirely. That is the first thing to try
when pssc misbehaves on a machine with plugins installed: it separates "pssc's
bug" from "a plugin's bug" in one command.

`pssc.testing` is the **public** kit for people writing targets — a bundled PSS
model, `compile_op_model`, `assert_common_tier`, `assert_deterministic`,
`golden_dir_compare`, and `pssc.testing.conformance.run()`. Use it in pssc's own
tests too where it fits: anything that only works via a private fixture is
something a plugin author cannot do.

```
direnv exec . ../python/bin/python -m pytest tests/progseq/test_conformance.py
```

The bundled model is a byte copy of `examples/export/programming_seqs/`
(setuptools cannot package files outside the package dir). If you edit one,
edit both — `test_the_bundled_model_matches_the_example_it_came_from` fails
otherwise. The real WB DMA model is deliberately not shipped.

The `c-host` target's compile-time constraint solving needs the **dv-solve** C
library built once:

```
cmake -S packages/dv-solve -B packages/dv-solve/build
cmake --build packages/dv-solve/build --target dv_solve
```

(Without it, rand fields fall back to zero-initialization.)

Do not make assumptions about the number of cores. Use what is available.

## Documentation that is checked

`docs/custom-generator-styles.md` is the user-facing extension guide (four
levels: style, backend override, new emitter, new language). Its code blocks are
**quoted verbatim** from files the test suite runs — mostly
`tests/plugins/pssc_fixture_plugin/` and `src/pssc/targets/py*` — and each block
names its source in a leading `#` comment.
`tests/unit/test_doc_examples.py` fails if a block and its source drift apart,
if a quoted file moves, if a `--flag` shown in a shell example is not a real
option, or if a link between doc pages dangles. So: **fix the doc, not the
source** — the source is what runs, and the guide is what the reader believes.

Two references hang off it and are checked the same way:
`docs/extension-stability.md` (what `stable`/`provisional` promise, the
admission rule for a new `@overridable`, and the deprecation window) and
`docs/op-model-manifest.md` (the `--emit-manifest` schema). If you change what a
surface promises, that page is the one to edit — nothing else states the policy.

## Activities on bc

Design: `docs/design/activity-flow-resource-bc-design.md`; P0 is tracked in
`docs/design/activity-p0-plan.md`.

**A traversal names what the linker resolved, never a guess.** ast2ir fills
`ActivityTraversal.type_qname`/`ActivityAnonTraversal.type_qname` from the
reference's `SymbolRefPath` (`_traversed_type_qname`). A handle, whether
declared in the action or in an activity block, gives its declared type;
`action_type` stays as written for the SV/sw/be-py consumers.
`PSSToScenarioPass` maps `type_qname` to a coroutine and refuses anything
else. bc raises `LoweringError` on an INVOKE/SPAWN it cannot resolve. It used
to run coroutine 0, a different action with a well-formed trace, and
`test_activity_bc_runs.py` is the trace-level guard.

**Every activity node is translated, or refused with a location.** The
activity translator has no `return None` fall-through. A new pssparser node
fails `test_activity_registry.py` by name, and a new ir-core activity IR node
fails `test_activity_ir_registry.py`, until a row says whether it lowers or
is refused. Add the row with the node. Type bodies hold to the same rule:
component, action and struct bodies (and their `extend`s) share one table,
`AstToIrTranslator._BODY_ELEMENTS`, and `test_type_body_registry.py` fails
on a pssparser class with no row. Exec blocks of one kind in a scope are
merged in source order (LRM 22.1 d); consumers see one function per kind. Constraint
statements too (`test_constraint_stmt_registry.py`): one with no IR form yet
(`soft`, `dist`, `default`) is recorded on its block as
`metadata["untranslated"]`, and a consumer that SOLVES the block must refuse
it (`collect_solve_problem` does); the op-model targets, which never solve,
are unaffected.

**Whose name it is comes from the linker.** In a traversal's `with` block
and on the left of an initializer, a name the linker found in the traversed
action (an `ElemKind_Inline` step in its path) is rooted at
`ir.TypeExprRefTraversed`, never `self` (LRM 13.1.4); `this.x` is `self.x`.
A traversal carries its handle declaration's initializers, then its own.

**A slot means one thing everywhere.** ir-core's `xf/pss_lower/layout.py`
lays out an action object: a plain-data struct is one slot per scalar leaf,
base fields first, named by dotted path (`s.csr.eol`). `ScField`, the solve
problem's `ScSolveVar.slot` and bc's struct locals all take it from there; do
not count slots anywhere else. bc moves a struct leaf by leaf (`StructT` in a
`_Place`) and never holds one in a register. An attribute's initial value is
the coroutine's first block, `ScExecBlock(kind="init")`. A `static const`
reference folds from its declaration, found through the linker
(`_linked_static_const`), never by name alone
(`test_bc_struct_values.py`, `test_bc_attribute_paths.py`).

**An activation is one object; its actions are nodes.** The scenario pass
builds an `ScActionTree` per export (`xf/pss_lower/action_tree.py`): every
handle, anonymous site and labeled-replicate instance has a slot range, its
type's layout followed by its children's subtrees, so `ScInvoke.child_base`
is static per (type, site). Constraints that tie nodes -- a parent's over
`b1.x`, a `with`, an activity `constraint` -- are resolved to slots and
grouped into cones (`ScScopeProblem`); a node tied to nothing keeps its own
`ScSolveProblem` (`test_action_tree.py`, `test_scope_cone.py`).

**bc runs the activation on one object.** A traversal is an INVOKE with
`INSTR_F_NODE`: the child runs on its parent's object at its node's base, and
every field access and SOLVE write-back is relative to the frame's base. A
type with a node in a cone solves through `SOLVE_NODE`, which looks the frame's
node up at run time. A member node is solved in its cone: committed values are
pinned, the constraints in force are enabled, and later traversals' values are
free, which is the lookahead. A node in no cone solves its own problem, so a
model with no cone keeps its bytecode (P1-D3). `SCOPE_ENTER` marks entry to an
activity block, which resets the handles traversed in it (13.4.8). Traversal
initializers run in the parent, on the child's slots, and the child then starts
past its own initial values (`INSTR_F_INITED`). A labeled `replicate` is
unrolled onto its nodes. The run-time side is `interp/activation.py`, specified
in be-bc's `docs/spec/activation.md`. The native engine implements the base
offset and refuses the rest before running anything (P1-D6). Tests:
`test_lookahead.py` (Ex 179/180/183/184, 200 seeds) and `test_scope_solve.py`
(`with`, activity constraints, Ex 84, the unsat error).

**Enums, not strings.** `JoinSpec.kind` is a `JoinKind` and
`DataTypeStruct.flow_kind` is a `FlowKind`; `test_flow_kind.py` holds the
second. The SV target keeps strings in its *own* binding records and converts
where it reads the IR (`analyze_flow._resolve_flow_kind`).

## Compliance tests (pss-corpus executable tier)

`tests/compliance/` runs the corpus's executable tier
(`packages/pss-corpus/compliance/`, design in
`packages/pss-corpus/COMPLIANCE-DESIGN.md`) on the bc backend through
`adapters/pssc_bc.py`. The corpus checker gives the verdict, not pssc.
`expected/bc.toml` lists bc's known non-PASS verdicts. Each entry is strict: a
listed test that starts passing fails the suite, so the list cannot go stale.

```
direnv exec . ../python/bin/python -m pytest tests/compliance
```

The same tests run on op-model-py (`test_compliance_op_model_py.py`) and on
op-model-sv (`test_compliance_op_model_sv.py`, marked `sim`: each test is a
Verilator build of the generated package plus a testbench the adapter writes
from `--emit-manifest`). Each has its own strict `expected/<target>.toml`.

It needs the **checkout** of zuspec-ir-core (`ScCoroutine.fields`). If the venv
holds a PyPI copy, the adapter reports `infra_error` and every case fails;
put `packages/zuspec-ir-core/src` first on `PYTHONPATH`.

Never edit a corpus model to make pssc pass. A disagreement is settled by the
LRM. If pssc is wrong, the entry goes in `expected/bc.toml` with its owning
defect.

The adapters write `outcome.json` and, on a rejection, `diagnostics.json`
(`adapters/diagnostics.py`, from pssparser's markers). Negative tests pass only
with an error on the right line, so an error that loses its marker shows up as
UNLOCATED. `test_handoff_roundtrip.py` runs the corpus's vendor hand-off
(`packages/pss-corpus/HANDOFF.md`: export a bundle, run an adapter over it as
a separate command, import) with bc playing the vendor, and requires the same
verdicts as the in-process run.

## Changing the AST
Schema for the AST is in `ast`. It is processed by `packages/pyastbuilder`.
This schema defines the data model created by parsing PSS code.
Any time an AST file is changed, the environment must be built from
scratch by removing the build directory and re-running cmake+make.
(The pssc migration itself touches only Python — no AST/cmake rebuild needed.)
