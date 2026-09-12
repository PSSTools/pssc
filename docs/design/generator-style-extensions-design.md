# Pluggable generator styles for pssc — design for review

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **proposal**. Nothing here is implemented yet.
Scope: how a *third party* (a company, a project, another package) adds a new
operation-model output style to `pssc` without forking it.

Motivating cases, all three real:

* **A company C style.** The same semantics `op-model-c` already emits, spelled
  the way one organisation's firmware group insists on: their banner, their
  symbol convention, their file layout, their clang-format profile — **and
  their register access**. This last one is not cosmetic and is the case that
  sizes the design: an organisation commonly mandates that every device-register
  touch go through *its* macros (`ACME_REG_WRITE32(a, v)`, a traced/checked
  wrapper, an MPU-aware accessor), so the generated code must reach the bus
  their way, not through `pssc_r32`/`pssc_w32`. See §2.5.1 — this is the one
  place where "restyle" crosses from spelling into the seam.
* **A Python style targeting a specific class library.** New language, no
  existing backend to start from, and the emitted code must inherit from and
  call into a library `pssc` has never heard of.
* **Mostly `op-model-c`, but with *this* bit different.** The case that sits
  between the two above and is probably the most common of the three: the C
  backend is right, except the handle struct needs an extra member, or every
  operation needs a tracing prologue, or a register-map header must be emitted
  alongside. No policy hook expresses it, and writing a backend to get it would
  be absurd. This is the mid-weight path — override a few published methods,
  inherit everything else including future fixes (§2.6).

The first and second are deliberately at opposite ends. The first should cost
~100 lines and reuse everything. The second should cost a real backend, but
should still reuse the whole front half — tree walk, classification, offset
folding, call legality, comments, dv-flow wiring. The third is what stops the
gap between them from being a cliff: an extension must be able to start as a
policy and grow into an override without a rewrite. If the design serves only
the ends, every real customisation lands at the expensive end.

---

## 1. What exists today

### 1.1 The layers, honestly

```
pssc.driver.compile
  frontend.Parser -> link -> AstToIrTranslator -> ir.to_core_context
  targets.get(name).prelude(opts)         # target_cfg_pkg, injected FIRST
  targets.get(name).run(ctx, opts)        # -> List[Path]

pssc.targets
  base.Target                 ABC: name, description, target_cfg, add_args,
                              prelude, run
  __init__                    _REGISTRY dict, register/get/list_targets,
                              discover() -- A STUB (targets/__init__.py:43)
  target_cfg                  the target_cfg_pkg prelude contract (v2)
  call_legality               Tier 0/1/2 registry of what a call may lower to
  validate_calls              the pass that uses it
  comments                    LINE / BLOCK comment rendering
  progseq_model               THE shared op-model layer (see below)

  progseq_tgt / c_progseq_tgt / cpp_progseq_tgt      Target subclasses
  progseq_gen / c/c_progseq_gen / cpp/cpp_progseq_gen  per-language assembly
  sv/*, c/*, cpp/*                                    per-language lowering
```

`progseq_model.py` is the only genuinely shared op-model layer, and it is
good: function classification (`func_kind`, `FuncKind`), component
classification (`comp_kind`, `is_reg_group`, `is_register`), field
introspection (`field_is_*`, `sub_components`, `channel_fields`), the tree walk
(`walk_tree` -> `CompNode`), affine offset folding (`array_base_stride`,
`scalar_offset`, `OffsetFoldError`) and the register collection walks
(`collect_reg_groups`, `collect_value_structs`). All of it language-neutral,
all of it documented with the defect that motivated it.

### 1.2 What is *not* shared, and is copied three times

Verbatim or near-verbatim duplicates across `progseq_gen.py`,
`c/c_progseq_gen.py` and `cpp/cpp_progseq_gen.py`:

| Duplicate | SV | C | C++ |
| --- | --- | --- | --- |
| `_resolver(ctx)` | `progseq_gen.py:26` | `c_progseq_gen.py:138` | `cpp_progseq_gen.py:20` |
| `_count(node, kind)` | `:206` | `:401` | `:96` |
| regular-component post-order | `_regular_components:192` | `_regular_components:351` + `regular_nodes` in `c/lower_progseq.py:145` | — |
| value-struct de-dup | `_value_structs:158` | `_reg_value_structs_all:375` | — |
| `mangle`, `_operations`, `_ctor` | `sv/lower_progseq.py` | `c/lower_progseq.py` | `cpp/lower_progseq.py` |
| body-emitter scaffolding | `_BodyEmitter` (1165 ln) | `_BodyEmitter` (1610 ln) | `_BodyEmitter` (249 ln) |

The three `_BodyEmitter`s are the big one. Their *rendering* is legitimately
per-language. Their *structure* is not: same `expr()` dispatch over the same IR
node-class names, same `stmt()`/`_stmt_lines()` split with the same comment
attachment hook, same call-classification order (register → channel → builtin →
model/import). A fourth backend written today copies that structure by hand a
fourth time.

### 1.3 The extension seams that already exist

* `Target` ABC — small, right shape, already the plug point.
* `targets.register(target, aliases=())` — works.
* `targets.discover()` — **live since Phase 3** (2026-08-14): the
  `pssc.targets` entry-point group, four accepted shapes, all-or-none per entry
  point, `PSSC_NO_PLUGINS`, and failures collected in `plugin_errors()` rather
  than raised. Was a commented-out stub when this was written.
* `call_legality._EXTENSIONS` — per target, Tier 2; written through
  `register_extension()` since Phase 3, which is where the "may extend, may
  never shrink COMMON" rule is now enforced. Was a public module-level dict.
* dv-flow: `pssc.dvflow.__ext__.dvfm_packages()` behind the `dv_flow.mgr`
  entry point; `pssc.dvflow.common.run_build` already does all the work
  (source gathering, incremental memento, output classification).
* Bundled runtime source under `pssc/share/{sv,c,cpp}` with
  `cli.sv_core_dir()` / `c_core_dir()` / `cpp_core_dir()` accessors, and
  `pssc <lang>-core-path` subcommands.

So roughly 60% of the mechanism is present. What is missing is: the discovery
switch, a *stable* API boundary, and a shared op-model base class so a plugin
does not have to re-implement §1.2.

---

## 2. Design

### 2.1 Principle: four tiers of extension effort

The scheme is sized so each of these is a distinct, documented amount of work,
and — critically — so that **the step up from one tier to the next is
incremental**. Discovering at 80% that your restyle needs one thing the policy
cannot express must not mean starting over as a full backend.

| Tier | You want | You write | Reuses | §|
| --- | --- | --- | --- | --- |
| **A. Restyle** | `op-model-c`'s semantics, your house style — naming, layout, **and your register-access macros** | a `StylePolicy` subclass (~50–150 ln) | the entire C backend | 2.5 |
| **B. Override** | mostly `op-model-c`, but *this* construct emitted differently — a different handle struct, an extra generated file, your own operation prologue | a `COpModelBackend` subclass overriding a handful of published methods (~100–400 ln) | the entire C backend, minus what you replace | **2.6** |
| **C. New emitter** | a different *shape* in a language pssc already emits (a C++ target on your RAII wrappers) | an `OpModelTarget` subclass + an emitter (~500 ln) | model layer, legality, comments, dv-flow | 2.3 |
| **D. New language** | Python-on-your-class-library | a `Target` + a `BodyWalker` subclass + emitters | model layer, legality, walker, comments, dv-flow | 2.4 |

Tiers A and B are *derivations* of a built-in backend and inherit its upstream
fixes. Tiers C and D are new backends that inherit only the shared layer. The
line between B and C is the one that matters commercially: everything that can
be done at B is maintenance somebody else pays for.

Everything below serves those four.

### 2.2 Entry-point discovery

Group: **`pssc.targets`**. Implement `targets.discover()`:

```python
_ENTRY_POINT_GROUP = "pssc.targets"

def discover() -> None:
    global _discovered
    if _discovered:
        return
    _discovered = True
    if os.environ.get("PSSC_NO_PLUGINS"):
        return
    from importlib.metadata import entry_points
    for ep in entry_points(group=_ENTRY_POINT_GROUP):
        try:
            _register_from_entry_point(ep)
        except Exception as e:
            _PLUGIN_ERRORS.append((ep.name, ep.value, e))
```

Decisions, each of which is a decision and not a default:

1. **What the entry point may resolve to.** A `Target` subclass, a `Target`
   instance, or a zero-argument callable returning either a `Target` or an
   iterable of them. The iterable case matters: an extension package that ships
   `acme-c` and `acme-c-presolved` should declare one entry point, not two, so
   the pair cannot half-load.

2. **Failure is isolated and *loud*, never silent.** A plugin that raises on
   import is recorded in `_PLUGIN_ERRORS` and reported by `pssc targets` and on
   any `compile` that names an unknown target — "3 targets available; 1 plugin
   failed to load: acme-pssc (ImportError: ...)". A missing target whose plugin
   silently failed is the single worst outcome here: the user gets "unknown
   target 'acme-c'" and no reason.

3. **Name collisions are an error, not last-wins.** If a plugin registers a name
   a built-in already owns, `register()` raises. A plugin that wants to
   *replace* a built-in must say so: `register(t, replaces="op-model-c")`.
   Silent shadowing of `op-model-c` by a plugin on the path would make two
   machines generate different firmware from the same command line.

4. **`--no-plugins` / `PSSC_NO_PLUGINS=1`.** Bisecting a bad build must not
   require uninstalling packages.

5. **API version.** `Target.PSSC_TARGET_API = 1` on the ABC. `discover()`
   compares the loaded class's value and refuses (with a clear message) a target
   built against an incompatible major. Cheap now, unbuyable later.

Declaration on the plugin side:

```toml
[project.entry-points."pssc.targets"]
acme = "acme_pssc.targets:register_all"
```

### 2.3 A shared op-model base class

New module `pssc/targets/op_model.py`. This is the tier-C/D plug point — and it
is what tiers A and B stand on too, since the built-in backends are ported onto
it. It absorbs §1.2.

```python
class OpModelTarget(Target):
    """Base for every operation-model style, whatever the output language."""

    #: Name used to look up Tier-2 call legality. Defaults to `name`.
    legality_target: str = ""

    def add_args(self, parser): ...        # --root, --ctor-name, --no-core-copy
                                           # (registered once, shared)

    def run(self, ctx, opts) -> List[Path]:
        model = self.elaborate(ctx, opts)  # -> OpModel
        self.check(model)                  # validate_calls, empty-API assert
        return self.emit(model, opts)      # <-- the ONE method a style writes

    # --- overridable seams ---
    def elaborate(self, ctx, opts) -> "OpModel": ...
    def check(self, model) -> None: ...
    def emit(self, model, opts) -> List[Path]: raise NotImplementedError
```

and the elaborated model handed to `emit`:

```python
@dc.dataclass(frozen=True)
class OpModel:
    ctx: Any                     # AstToIrContext (ctx.ir_context is the core Context)
    root: Any                    # root DataTypeComponent
    tree: CompNode               # walk_tree result
    components: Tuple[Any, ...]  # regular components, post-order (children first)
    reg_groups: Tuple[Any, ...]  # post-order, de-duplicated
    value_structs: Tuple[Any, ...]
    api_types: ApiTypes          # enums + plain structs the API mentions
    imports: Mapping[str, Any]   # declared package-scope import functions
    ctor_names: FrozenSet[str]   # replaces the global set_ctor_name()
    out_dir: Path

    # convenience, so a style never re-walks
    def operations(self, comp) -> List[Any]: ...
    def ctor(self, comp) -> Optional[Any]: ...
    def channels(self, comp) -> List[Any]: ...
    def sub_components(self, comp) -> List[SubComp]: ...
    def offset_of(self, group, name) -> int: ...
    def base_stride_of(self, group, field) -> Tuple[int, int]: ...
```

Three things this fixes beyond de-duplication:

* **`validate_calls` runs for every style.** Today it runs only for
  `op-model-sv` (`progseq_gen.py:66`); C and C++ never call it, so the gate that
  exists specifically to stop "emit an undeclared symbol and exit 0" is not
  applied to the backend most likely to hit it. Moving it into
  `OpModelTarget.check()` closes that, and gives every plugin the gate for free.
* **`set_ctor_name()` stops being global mutable state.**
  `progseq_model._CTOR_NAMES` is a module global mutated by every target on
  entry (`progseq_tgt.py:74`). Two `pssc.compile()` calls in one process with
  different `--ctor-name` values race; `OpModel.ctor_names` is per-run.
* **The empty-API assertion** (`progseq_gen._assert_api_is_not_empty`) becomes
  everyone's, which is right — it is a front-end-defect detector, not an SV one.

### 2.4 A language-neutral body walker

New module `pssc/targets/body_walker.py`. Extract the *structure* the three
`_BodyEmitter`s share, leaving rendering abstract:

```python
class BodyWalker(abc.ABC):
    """Walks one function body. Subclasses render; this class decides shape."""

    # provided
    def emit(self, fn, ind=1) -> List[str]      # comments + dispatch
    def stmts(self, body, ind) -> List[str]
    def stmt(self, s, ind) -> List[str]         # comment attachment hook
    def expr(self, e) -> str                    # dispatch by IR node class name
    def scan_write_only(self, fn) -> Set[str]   # the (void)x case
    def scan_chan_outs(self, body) -> Set[str]

    # abstract -- the language
    @abc.abstractmethod
    def render_const(self, v) -> str: ...
    @abc.abstractmethod
    def render_binop(self, op, lhs, rhs) -> str: ...
    @abc.abstractmethod
    def render_member(self, attr) -> str: ...
    @abc.abstractmethod
    def render_reg_call(self, recv, method, args) -> str: ...
    @abc.abstractmethod
    def render_mem_call(self, prim, args) -> str: ...
    @abc.abstractmethod
    def render_chan_call(self, chan, method, args) -> Optional[str]: ...
    @abc.abstractmethod
    def render_model_call(self, name, args) -> str: ...
    @abc.abstractmethod
    def render_if / render_foreach / render_match / render_yield ...
```

A call reaching `expr()` is classified once, by
`call_legality.classify(...)`, and dispatched on the resulting `Disposition` —
so the registry stops being advisory documentation consulted by a separate pass
and becomes the actual dispatch table. That is a strict improvement even
ignoring plugins: today `c/lower_progseq.py` re-derives the same classification
by hand (`_reg_call` → `_chan_call` → `_builtin_call` → `_model_call`) and can
drift from the registry that `validate_calls` checks against.

Migration is incremental: port C first (it is the most complete), keep SV and
C++ on their current emitters, and only claim the seam is real once two
languages sit on it. **Do not ship the abstraction and the first plugin at the
same time** — an abstraction validated by one user is a guess.

### 2.5 A style policy for tier A

For "same language, our house style", subclassing an emitter is too much. Add a
`StylePolicy` the C (and later SV/C++) backend consults for every naming and
formatting decision it currently hard-codes:

```python
class CStylePolicy:
    # --- naming and layout -------------------------------------------------
    def banner(self, model, settings) -> List[str]
    def header_name(self, prefix) -> str            # <prefix>.h
    def impl_name(self, prefix) -> str              # <prefix>.c
    def symbol(self, comp_prefix, name) -> str      # wb_dma_start
    def type_name(self, comp_prefix) -> str         # wb_dma_t
    def include_order(self, ...) -> List[str]
    def comment_style(self) -> str                  # LINE / BLOCK / HASH
    def indent(self) -> str

    # --- register / memory access (see 2.5.1; the funnel is `MemAccess`) ---
    def reg_symbol(self, comp_prefix, path, reg) -> str
    def reg_accessor_form(self) -> str              # "inline" | "macro" | "none"
    def render_reg_read(self, acc, handle, idx) -> str
    def render_reg_write(self, acc, handle, idx, value) -> str
    def render_reg_masked_write(self, acc, handle, idx, mask, val) -> List[str]
    def render_mem_read(self, width, handle, addr) -> str
    def render_mem_write(self, width, handle, addr, value) -> str
    def seam_headers(self) -> List[Tuple[Path, str]]   # [] = supply your own
```

Selected with `--style acme` (or `style: acme` in dv-flow), resolved through a
second entry-point group **`pssc.styles`** keyed `"<target>:<style>"`. A company
restyle is then one class and one entry point, and it tracks upstream C
semantics automatically — which is exactly the property a fork does not have.

The naming half of this is the highest-value/lowest-risk piece of the whole
proposal and could ship first, independently. The register half needs the
refactor in §2.5.1 first.

#### 2.5.1 Register access is not spelling — and today it is hard-coded in three places

An organisation that mandates `ACME_REG_WRITE32(addr, val)` is not asking for a
different *name*; it is asking for a different way to touch the bus. In the C
backend that decision is currently made, inline and independently, at three
sites:

| Site | What it hard-codes |
| --- | --- |
| `c/lower_reg_model.py:224` `emit_accessor` | every accessor body: `pssc_r<N>(pssc_bus(s), <base>_addr(s))`, and the six-accessor set (`_addr`, `_read`, `_write`, `_read_val`, `_write_val`, `_write_masked`) |
| `c/lower_progseq.py:1051` `_mem_call` | a model's raw `read32(h)`/`write32(h, v)` → `pssc_r32(pssc_bus(s), ...)` |
| `c/lower_progseq.py:886` `_reg_call` | which accessor a `regs.csr.write_val(v)` resolves to |

The masked-write expansion (`(cur & ~mask) | (val & mask)`, `emit_accessor`
lines 276–281) is a fourth: it is PSS 3.1 §21.14.1 *semantics*, not style, and a
policy that overrides the write must not be able to quietly drop the read —
which is a side-effecting operation on a status CSR.

What today's flags already get you, and where they stop:

* `--mem-access functions` (`share/c/pssc_mem_fn.h`) lets a project supply
  `pssc_mem_write32(addr, data)` and link it. That covers "route every access
  through our function", and is genuinely most of the way there.
* It does **not** cover a *macro* mandate (the generated text still reads
  `pssc_w32(...)`, which is what a house-style reviewer or a lint rule
  objects to), a different accessor *shape* (macro-per-register instead of
  inline functions; no accessor block at all), a different symbol convention for
  register constants, or an access that needs the register's identity rather
  than only its address — a traced accessor taking `ACME_REG(DEV, CH0, CSR)`.

**Prerequisite refactor: one funnel for bus access.** Route all three sites
above through a single object, so there is one place a policy can override and
one place the truth lives. This is worth doing on its own merits — the
`pssc_r<N>(pssc_bus(s), ...)` spelling is currently duplicated across
`lower_reg_model` and `lower_progseq`, and the two can drift.

**What the policy may and may not decide.** The boundary has to be explicit or
tier A becomes a way to generate subtly wrong firmware:

* *May* decide: the spelling of a read/write, whether accessors are inline
  functions or macros or absent, register symbol naming, which seam headers (if
  any) are copied, the width-selection spelling.
* *May not* decide: the address (it comes from the folded offsets on the
  `OpModel`, and `OffsetFoldError` stays fatal), the access-direction rules
  (a `READONLY` register gets no writer), the read inside a masked write, or
  which registers exist. A policy returning a rendering for a direction the
  register does not have is a hard error, not a silent emission.

**Interaction with `--link-style` / `--mem-access`.** Those select a *mechanism*
(vtable / pointer / extern function / compile-time selectable) and remain
orthogonal: a policy that renders `ACME_REG_WRITE32` is choosing the
mechanism too, so it declares `seam_headers() == []` and pairs with
`--no-core-copy`. Combining a bus-overriding policy with `--link-style vtable`
must be rejected at start-up with a message naming both, in the same spirit as
the existing `--mem-access` + `vtable` rejection (`c_progseq_gen.py:194`).

### 2.6 Tier B — overriding a backend through its API

A policy answers questions the backend thought to ask. The mid-weight case is
the one where you need to change something the backend did *not* think to ask
about: the handle struct should carry an extra member, every operation needs a
company prologue, the accessor block should be accompanied by a generated
register-map header, `_create`/`_destroy` should call your allocator. None of
that is a policy hook, and none of it justifies writing a backend.

So: **the built-in backends become classes with a published set of overridable
methods, and a tier-B extension subclasses one.**

#### 2.6.1 Turning the backends into subclassable objects

Today `op-model-c` is module-level functions (`lower_handles`, `lower_decls`,
`lower_impl`, `lower_accessors`, `emit_accessor`) plus a private
`_BodyEmitter`, assembled by a 200-line `generate()` free function
(`c/c_progseq_gen.py:156`). None of that can be overridden without copying it.

The refactor is mechanical and behaviour-preserving:

```python
class COpModelBackend:
    """The built-in C backend. Subclass to change what you need."""

    #: Swapped wholesale by a subclass that needs different body rendering.
    body_emitter_cls = CBodyEmitter
    style_cls        = CStylePolicy

    def generate(self, model, settings) -> List[Path]:
        files = [self.emit_header(model, settings)]
        if not settings.header_only:
            files.append(self.emit_impl(model, settings))
        files += self.emit_extra_files(model, settings)     # default: []
        files += self.copy_core(model, settings)
        return files
```

Two properties make this usable rather than merely possible:

1. **Every method returns text (`str` / `List[str]`), never writes a file.**
   Only `generate` and the file-emitting methods touch disk. A subclass can
   therefore call `super()` and wrap, prepend, filter or reindent the result —
   which is the whole point of a mid-weight path and is *not* possible if a
   method writes as a side effect.
2. **File assembly is a named-section pipeline, not a straight line.** The
   header is built from an ordered list of named sections, so a subclass can
   replace, insert-before or drop one without reimplementing the other twelve:

```python
def header_sections(self, model, settings) -> List[Section]:
    return [
        Section("banner",     self.emit_banner),
        Section("guard_open", self.emit_guard_open),
        Section("includes",   self.emit_includes),
        Section("api_types",  self.emit_api_types),
        Section("reg_values", self.emit_value_unions),
        Section("handles",    self.emit_handles),
        Section("accessors",  self.emit_accessors),
        Section("imports",    self.emit_import_decls),
        Section("decls",      self.emit_decls),
        Section("impl",       self.emit_impl_inline),   # header-only builds
        Section("guard_close",self.emit_guard_close),
    ]
```

```python
class AcmeCBackend(COpModelBackend):
    def header_sections(self, model, settings):
        s = super().header_sections(model, settings)
        return insert_after(s, "banner", Section("acme_hdr", self.emit_acme_hdr))
```

Section *order* stays the backend's business where correctness depends on it
(C has no forward references; the existing ordering comments in
`c_progseq_gen.py:300` are load-bearing). `insert_after`/`replace` are checked:
naming a section that does not exist raises, so an upstream rename surfaces as
an error rather than as a silently-dropped customisation.

#### 2.6.2 The published override surface

The danger of "subclass it" is that every method becomes API and the backend
can never be refactored again. So the surface is **explicit and small**, and
the rest is genuinely private:

```python
@overridable(since="1.0", stability="stable")
def emit_handles(self, model, settings) -> str: ...
```

* `@overridable` marks a method as part of the contract, with the version it
  appeared in and its stability (`stable` / `provisional`).
* Everything not marked is `_`-prefixed and may change in any release.
* A test enumerates the marked set and compares it against a checked-in
  manifest, so **growing or changing the override surface is a deliberate diff
  in a review**, not a side effect of someone making a method public.
* `provisional` is honest about the ones we are not yet sure of — the body
  emitter's internals in particular — and gives a real place to put them
  instead of pretending they are stable or hiding them.

Initial `stable` set (proposal): the section emitters above,
`emit_operation`, `emit_signature`, `emit_ctor`, `emit_extra_files`,
`copy_core`, plus `body_emitter_cls` / `style_cls`. Everything in
`_BodyEmitter` starts `provisional` until §2.4's `BodyWalker` extraction
settles it.

#### 2.6.3 Invariants a subclass can break, and what catches them

This is the fragile-base-class problem and it is real. The override points come
in *pairs* whose consistency nothing in Python enforces: a subclass that
overrides `emit_signature` but not the call rendering produces a header and
bodies that disagree; one that overrides `emit_handles` to add a member but not
`emit_ctor` produces an uninitialised field.

Three mitigations, in order of how much they actually buy:

1. **Declared pairings, checked at registration.** `@overridable(pairs_with=
   ("emit_signature", "emit_call"))` — overriding one without the other is an
   error at target-registration time, naming both. Cheap, catches the common
   case, and forces the doc to state which decisions are joint.
2. **The conformance suite (O6) run against the subclass**, which is what
   catches the semantic breakages the pairing rule cannot: every operation
   reachable, every accessor addressing what the offset fold says, no
   undeclared symbol.
3. **A differential baseline test** (§2.10): generate with the subclass and
   with its base, and assert the diff is confined to the constructs you meant
   to change. A tier-B extension's own suite should contain exactly one of
   these, and it doubles as the reviewable statement of what the extension
   does.

#### 2.6.4 How A and B compose

They must, because a real company extension will want both — house naming
*and* one construct emitted differently. Rules:

* A subclass inherits the style seam; `self.style` is available in every
  method, and the default implementations consult it exactly as before.
* **A subclass that overrides a method takes over that method's policy
  obligations.** If `emit_accessors` is overridden, the policy's accessor hooks
  are the subclass's to honour or ignore. This is stated rather than enforced —
  the alternative is a framework that calls your override and then second-
  guesses it.
* A backend subclass may set `style_cls` as its own default while still
  honouring `--style`, so an extension can ship "our backend, our style" as one
  installable and still let a project override the style half.

#### 2.6.5 Registering a tier-B backend

A tier-B extension is a normal target with a declared ancestry:

```python
class AcmeCTarget(OpModelTarget):
    name         = "acme-c"
    description  = "ACME C operation model (derives from op-model-c)"
    derives_from = "op-model-c"          # <-- the tier-B declaration
    backend_cls  = AcmeCBackend
```

`derives_from` is not decoration. It makes three things inherit automatically,
each of which is otherwise a silent hole:

* **Call legality.** `entries_for("acme-c")` resolves through the ancestor, so
  the derived target starts with the C target's Tier-2 set instead of an empty
  one (and cannot shrink Tier 1 — §2.7a).
* **CLI options.** `super().add_args()` gives the derived target every
  `op-model-c` option, so `--link-style`, `--lifecycle` and the rest keep
  working without restating them.
* **`target_cfg`.** The derived target inherits the ancestor's published
  capabilities unless it overrides them — a derived C backend that generates no
  scheduler must not accidentally claim `HAVE_EVENT_WAIT` by omission.

### 2.7 Making the shared services actually extensible

Four registries are currently closed and must open, each with a registration
call rather than a mutable public dict:

**a) Call legality (`call_legality.py`).**

```python
def register_extension(target: str, entries: Iterable[Entry],
                       inherit: Optional[str] = None) -> None
```

Two defects to fix while opening it:

* **Key mismatch.** `EXTENSIONS` is keyed `"op-model-sv"`, `"c-progseq"`,
  `"cpp-progseq"` (`call_legality.py:235,256`) but the registered target names
  are `op-model-sv`, `op-model-c`, `op-model-cpp`. `entries_for("op-model-c")`
  therefore returns **no Tier-2 entries at all**. It is latent only because the
  C backend never calls `validate_calls`; the moment §2.3 makes it do so, every
  `print`/`try_get`/`try_put` in a model becomes "no function named ... is
  declared", pointing the user at their model for a compiler bug. Re-key to
  canonical target names and resolve aliases in `entries_for`.
* Tier 1 (`COMMON`) must stay un-shrinkable by plugins —
  `register_extension` rejects an entry that marks a COMMON name
  `unsupported`, with the reason. `tests/progseq/test_call_legality.py` already
  enforces this for built-ins; extend it over the registry.

**b) `target_cfg` (`target_cfg.py`).** The contract is closed: `render()`
rejects any constant outside `CONTRACT` (`:196`). A Python-on-a-class-library
style that wants to publish e.g. "the runtime is async" has no path. Proposal:
keep the core contract closed and versioned, and allow **namespaced**
extensions:

```python
class AcmePyTarget(OpModelTarget):
    target_cfg = {"HAVE_EVENT_WAIT": True, "HAVE_RUNTIME_SOLVER": False}
    target_cfg_ext = {"X_ACME_ASYNC": True}     # must match X_[A-Z0-9_]+
```

`render()` emits them after the contract constants with a comment marking them
non-contract; `parse_overrides` accepts `X_*` names for the selected target
only. The `X_` prefix is what keeps a future core constant from colliding with
somebody's private one.

**c) dv-flow output classification.** `EXT_FILETYPE` in `dvflow/common.py:35`
is a closed dict; an unmapped extension is *silently skipped*
(`common.py:102`) — a plugin emitting `.pyx`, `.rs`, `.json` or `.rst` would
produce files that never appear in any fileset, with only a debug log. Add
`register_filetype(ext, filetype, is_incdir)` and promote the skip from
`_log.debug` to `_log.warning`.

**d) Bundled runtime source.** `cli.c_core_dir()` et al. are hard-wired to
`pssc/share`. Add:

```python
def core_dir(package: str = "pssc", lang: str = "c") -> Path
```

and an optional `Target.core_files() -> List[Tuple[Path, str]]` returning
(source, destination-name) pairs that the base `emit` copies. A plugin then
ships its own `share/` and gets `--no-core-copy` handling, path resolution and
the "written files, in compilation order" contract for free.

### 2.8 CLI options — the sharp edge

Every target contributes to **one** `compile` parser (`cli.py:113`), through
`_DedupArgGroup`, whose rule is **first wins, silently**
(`cli.py:39`). For built-in variants that share inherited options this is
intended. For plugins it is a trap: a plugin declaring `--prefix` gets *no*
option registered and no error, and `opts.<its dest>` is never set, so it
silently reads its default forever.

Proposal:

1. **Namespace requirement.** A plugin target's options must be spelled
   `--<target-name>-<opt>` (`--acme-c-header-style`) and its `dest` must start
   with the target's identifier. Enforced by a wrapper around `add_args` that
   *raises* for a plugin whose option collides with an already-registered one,
   while built-ins keep the dedup behaviour. A loud failure at parser-build time
   beats a silent no-op at run time.
2. **A generic escape hatch that needs no parser at all.**
   `-X, --target-opt NAME=VALUE` (repeatable) lands in `opts.target_opts`, a
   dict. `OpModelTarget` exposes `self.opt(opts, "header-style", default=...)`
   with per-target validation. A plugin can then be useful with *zero* parser
   integration, which is what makes tier A viable and what dv-flow's `args:`
   passthrough already wants.
3. Longer term, the right shape is a per-target subparser
   (`pssc compile -t acme-c -- --header-style banner`), which removes the
   global namespace entirely. Worth recording as the intended end state even if
   not done now.

### 2.9 dv-flow integration for plugins

No new mechanism needed — a plugin ships its own `flow.yaml` plus a
`dv_flow.mgr` entry point, and its task bodies call the *existing*
`pssc.dvflow.common.run_build`. What is needed is to **declare that public**:

* `run_build(ctxt, input, *, target, overrides_from_params)` — stable.
* `classify_outputs`, `gather_pss_sources`, `gather_export_actions`,
  `compute_memento` — stable.
* `EXT_FILETYPE` — replaced by `register_filetype` (§2.7c).

Plus one caveat to document: `compute_memento` must hash the *plugin's* version
too, or a plugin upgrade will not invalidate a cached build. Today the memento
covers sources + target + options; a plugin whose code changed but whose inputs
did not would be skipped as up-to-date. **This is a correctness bug the moment
plugins exist.** Fix: include `(target_name, target_module_version)` in the
memento, sourced from the plugin's distribution metadata.

### 2.10 A testing kit for plugin authors

Ship `pssc.testing` (importable, not test-only):

* `op_model_sources()` — the checked-in WB DMA model already used by
  `tests/progseq/op_model.py`, so a plugin has a real, non-trivial model to
  generate from on day one.
* `compile_op_model(target, **opts) -> CompileResult` — one call.
* `assert_common_tier(target_name)` — the Tier-1 contract check, so a plugin can
  assert its own legality table in its own suite.
* `assert_deterministic(target, **opts)` — generate twice, compare bytes.
  Cheap, and it catches the `set()`-iteration-order class of bug that makes
  generated artifacts churn in version control.
* `golden_dir_compare(actual, expected)` — the diff harness every backend here
  already has privately.

---

## 3. Draft documentation section

> To be added as `docs/custom-generator-styles.md`, linked from `docs/index.rst`.
> Written here in full so the API can be reviewed through the doc a user will
> actually read.

---

### Adding a custom generator style

`pssc` discovers output styles from the **`pssc.targets`** Python entry-point
group. A style lives in your own package, installs alongside `pssc`, and appears
in `pssc targets` and in dv-flow the same way the built-in styles do. You do not
fork `pssc`, and you do not patch its source.

Pick the smallest of the three levels that does what you need.

#### Level 1 — restyle an existing language

Use this when you want what `op-model-c` already generates, spelled your way:
your banner, symbol convention, include order, comment style.

```python
# acme_pssc/style.py
from pssc.targets.style import CStylePolicy

class AcmeCStyle(CStylePolicy):
    """ACME firmware house style: `acme_` prefix, banner block, tab indent."""

    def banner(self, model, settings):
        return ["/*", " * ACME confidential -- generated, do not edit.",
                f" * Model: {model.root.name}", " */"]

    def symbol(self, comp_prefix, name):
        return f"acme_{comp_prefix}_{name}"

    def indent(self):
        return "\t"
```

```toml
[project.entry-points."pssc.styles"]
"op-model-c:acme" = "acme_pssc.style:AcmeCStyle"
```

```bash
pssc compile --target op-model-c --style acme --root wb_dma_c -o gen/ *.pss
```

You inherit every semantic fix made upstream to the C backend. Anything the
policy does not override keeps the default spelling.

**Mandating your own register-access macros.** If your organisation requires
that every device-register touch go through its own macros, override the access
hooks as well. This replaces how the bus is reached, so declare that you supply
the seam yourself:

```python
class AcmeCStyle(CStylePolicy):
    ...
    def reg_accessor_form(self):
        return "macro"          # ACME_REG_* at the call site, no inline block

    def reg_symbol(self, comp_prefix, path, reg):
        # wb_dma / ("regs","bank") / "csr"  ->  ACME_REG_WB_DMA_BANK_CSR
        return "ACME_REG_" + "_".join([comp_prefix, *path[1:], reg]).upper()

    def render_reg_write(self, acc, handle, idx, value):
        return f"ACME_REG_WRITE{acc.width}({self.reg_symbol(*acc.key)}, {value})"

    def render_reg_read(self, acc, handle, idx):
        return f"ACME_REG_READ{acc.width}({self.reg_symbol(*acc.key)})"

    def render_mem_write(self, width, handle, addr, value):
        return f"ACME_WRITE{width}({addr}, {value})"

    def render_mem_read(self, width, handle, addr):
        return f"ACME_READ{width}({addr})"

    def seam_headers(self):
        return []               # we bring our own; copy none of pssc's
```

```bash
pssc compile -t op-model-c --style acme --no-core-copy \
             --include '<acme/hal_regs.h>' --root wb_dma_c -o gen/ *.pss
```

Three rules the generator enforces, and will not let a policy break:

* **Addresses stay ours.** They come from the folded PSS offset functions. A
  policy renders the *access*; it never computes the address.
* **Access direction is honoured.** A `READONLY` register is never handed to
  `render_reg_write`. Returning a rendering for a direction the register does
  not have is an error, not an emission.
* **A masked write keeps its read.** `write_field`/`write_masked` are defined
  (PSS 3.1 §21.14.1) as read-modify-write, and on a status CSR that read has
  side effects. Override `render_reg_masked_write` if your macro does the whole
  operation; otherwise the default composes your read and your write, in that
  order.

A bus-overriding policy is incompatible with `--link-style vtable` (that seam
reaches the bus through a per-instance function-pointer struct, which is a third
mechanism). Combining them is rejected at start-up.

#### Level 2 — override parts of an existing backend

Use this when a policy hook cannot express what you need, but you still want
everything else `op-model-c` does — and every fix it gets in future. You
subclass the backend, override a few published methods, and register the result
as your own target.

```python
# acme_pssc/backend.py
from pssc.targets.c.backend import COpModelBackend, Section, insert_after

class AcmeCBackend(COpModelBackend):

    # 1. Add a section to the generated header.
    def header_sections(self, model, settings):
        s = super().header_sections(model, settings)
        return insert_after(s, "banner",
                            Section("acme_meta", self.emit_acme_meta))

    def emit_acme_meta(self, model, settings):
        return [f"/* ACME-PART: {model.root.name}",
                f" * ACME-TOOLCHAIN: {settings.toolchain} */"]

    # 2. Wrap an existing method rather than replacing it.
    def emit_operation(self, model, comp, fn, settings):
        body = super().emit_operation(model, comp, fn, settings)
        return [f"ACME_TRACE_ENTER(\"{fn.name}\");", *body,
                "ACME_TRACE_EXIT();"]

    # 3. Emit an extra artifact alongside the header and .c.
    def emit_extra_files(self, model, settings):
        path = settings.out_dir / f"{settings.prefix}_regmap.h"
        path.write_text(self.render_regmap(model))
        return [path]
```

```python
# acme_pssc/targets.py
from pssc.targets.op_model import OpModelTarget
from .backend import AcmeCBackend

class AcmeCTarget(OpModelTarget):
    name         = "acme-c"
    description  = "ACME C operation model"
    derives_from = "op-model-c"      # inherit options, call legality, target_cfg
    backend_cls  = AcmeCBackend
```

Every emitting method **returns text and writes nothing**, which is what lets
you call `super()` and wrap the result. Only `emit_extra_files` and the
top-level file emitters touch disk.

`derives_from` is doing real work: without it your target starts with an empty
call-legality table (so a legal `print` in a model becomes "no such function"),
loses every `op-model-c` command-line option, and publishes no `target_cfg`.

**What you may override.** Only methods marked `@overridable` are contract; run
`pssc targets --overrides acme-c` to list them with their stability. Anything
else is private and will change without notice. Some overrides come in pairs —
`emit_signature` and `emit_call` must move together, for instance — and
overriding one without the other is an error at registration, naming both.

**Prove what you changed.** A tier-2 extension should own exactly one
differential test. It is the reviewable statement of what your backend does:

```python
from pssc.testing import assert_differs_from_baseline

def test_acme_c_only_adds_tracing():
    assert_differs_from_baseline(
        "acme-c", baseline="op-model-c", root="wb_dma_c",
        expect_changed={"acme_meta", "operations", "extra_files"})
```

If an upstream change makes your override diverge further than you declared,
that test fails — which is the point. Run
`pssc.testing.conformance.run("acme-c")` too: the pairing rule catches
mechanical mismatches, but only the conformance suite catches an override that
addresses the wrong register.

#### Level 3 — a new emitter for a language pssc already emits

Use this when the *shape* differs so much that overriding is a fight — a C++
style built on your RAII register wrappers, say. Subclass `OpModelTarget` and implement
`emit`. Everything before `emit` — parse, translate, resolve `--root`, walk the
tree, fold offsets, collect register groups and value structs, validate call
legality — has already run, and its results are on the `OpModel` you are handed.

```python
# acme_pssc/targets.py
from pathlib import Path
from pssc.targets.op_model import OpModelTarget

class AcmeCppTarget(OpModelTarget):
    name = "acme-cpp"
    description = "ACME C++ operation model (acme::hal register wrappers)"
    target_cfg = {"HAVE_EVENT_WAIT": True, "HAVE_RUNTIME_SOLVER": False}

    def add_args(self, parser):
        super().add_args(parser)              # --root, --ctor-name, --no-core-copy
        parser.add_argument("--acme-cpp-namespace", dest="acme_cpp_namespace",
                            default="acme", help="generated namespace")

    def emit(self, model, opts):
        ns = opts.acme_cpp_namespace
        lines = [f"namespace {ns} {{"]
        for comp in model.components:         # post-order: children first
            lines.append(f"class {comp.name.split('::')[-1]} {{")
            for fn in model.operations(comp):
                lines.append(f"  void {fn.name}();")
            lines.append("};")
        lines.append(f"}}  // namespace {ns}")

        out = model.out_dir / f"{ns}_hal.hpp"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n")
        return [out]                           # in COMPILATION order
```

Return paths in **compilation order**, not creation order: the list is what a
build system hands the compiler, and dv-flow preserves it.

#### Level 4 — a new output language

Use this when nothing existing applies — Python against your own class library,
Rust, a DSL. Add a `BodyWalker` subclass so operation bodies lower through the
shared walk rather than a hand-written one:

```python
from pssc.targets.body_walker import BodyWalker

class PyBodyWalker(BodyWalker):
    def render_const(self, v):
        return "True" if v is True else "False" if v is False else repr(v)

    def render_member(self, attr):
        return f"self.{attr}"

    def render_reg_call(self, recv, method, args):
        # acme.hal registers: r.write(v) / r.read()
        return f"self.{recv}.{method}({', '.join(args)})"

    def render_mem_call(self, prim, args):
        return f"self._bus.{prim}({', '.join(args)})"

    def render_model_call(self, name, args):
        return f"self.{name}({', '.join(args)})"

    def render_yield(self, ind):
        return [f"{self.pad(ind)}await asyncio.sleep(0)"]
```

The walker handles statement dispatch, nesting, comment propagation (choose
`comments.HASH` for Python), write-only-local detection and channel-output
locals. You render.

#### Registering

```toml
[project.entry-points."pssc.targets"]
acme = "acme_pssc.targets:register_all"
```

```python
# acme_pssc/targets.py
def register_all():
    return [AcmeCppTarget(), AcmePyTarget()]
```

Return a `Target`, a `Target` subclass, or an iterable of either. Registering
both styles from one entry point means they cannot half-load.

`pip install -e .` your package, then:

```bash
pssc targets            # acme-cpp and acme-py are listed
pssc compile -t acme-py --root wb_dma_c -o gen/ $(cat files.f)
```

If a style does not appear, `pssc targets` prints why — a plugin that fails to
import is reported, never silently dropped. `--no-plugins` (or
`PSSC_NO_PLUGINS=1`) disables discovery when bisecting.

#### Command-line options

All targets share one `compile` parser, so **a plugin's options must be
namespaced** with its target name (`--acme-cpp-namespace`, `dest="acme_cpp_*"`).
A collision with an existing option raises at start-up rather than silently
dropping your option.

For anything not worth an argparse entry, use the generic passthrough:

```bash
pssc compile -t acme-py -X header-style=banner -X strict=true ...
```

```python
def emit(self, model, opts):
    style = self.opt(opts, "header-style", default="plain",
                     choices=("plain", "banner"))
```

#### Declaring capabilities to the model

A target publishes what the *execution target* can do; the model reads it with a
plain `compile if` and never learns which tool answered. You must supply
**every** constant in the current contract — a partial set is an error, because a
model that sees the version marker is entitled to reference all of them:

```python
target_cfg = {"HAVE_EVENT_WAIT": True, "HAVE_RUNTIME_SOLVER": False}
target_cfg_ext = {"X_ACME_ASYNC": True}    # private; X_-prefixed by rule
```

`HAVE_EVENT_WAIT` asks one narrow question: *can a caller suspend until another
party posts an event?* It does not ask whether a caller may spin — every target
can spin. Answer the question that is asked; `pssc targets` prints what each
target claims.

#### Declaring what calls you can lower

If your style can lower something beyond the common set (or explicitly cannot),
register it. The registry is what the pre-emission gate checks, so this is not
documentation — it is the dispatch table:

```python
from pssc.targets.call_legality import (
    register_extension, Entry, Disposition, Ctx, BOTH, TARGET_ONLY)

register_extension("acme-py", inherit="op-model-sv", entries=[
    Entry("print", Disposition.UTILITY, BOTH, lrm="21.1.2"),
    Entry("urandom", Disposition.UTILITY, BOTH, lrm="21.4",
          unsupported="the ACME runtime provides no seeded PRNG"),
])
```

You may **add** to the common tier; you may never shrink it. A style that cannot
render a common-tier call is not a style — it is a different backend, and the
contract test will say so.

#### Shipping runtime source

Bundle your own headers/modules and declare them; the base class copies them
beside the generated output and honours `--no-core-copy`:

```python
def core_files(self):
    from pssc.resources import core_dir
    d = core_dir("acme_pssc", "py")
    return [(d / "acme_hal.py", "acme_hal.py")]
```

#### dv-flow tasks

Ship a `flow.yaml` and a `dv_flow.mgr` entry point; your task bodies reuse
`pssc.dvflow.common.run_build`:

```python
from pssc.dvflow.common import run_build

async def AcmePy(ctxt, input):
    return await run_build(ctxt, input, target="acme-py",
                           overrides_from_params=lambda p: {
                               "progseq_root": getattr(p, "root", ""),
                           })
```

If you emit a file extension `pssc` does not know, register it or it will not
appear in any fileset:

```python
from pssc.dvflow.common import register_filetype
register_filetype(".pyi", "pythonSource", is_incdir=False)
```

#### Testing your style

```python
from pssc.testing import (compile_op_model, assert_common_tier,
                          assert_deterministic)

def test_acme_py_generates():
    res = compile_op_model("acme-py", root="wb_dma_c")
    assert any(p.name.endswith(".py") for p in res.outputs)

def test_acme_py_keeps_common_tier():
    assert_common_tier("acme-py")

def test_acme_py_is_deterministic():
    assert_deterministic("acme-py", root="wb_dma_c")
```

`compile_op_model` runs against the checked-in WB DMA operation model — a real
model with register groups, component arrays, channels and imports, not a toy.

---

## 4. Issues and risks

**I1 — `validate_calls` runs for one target out of three.** Only
`progseq_gen.py:66` calls it. The C and C++ paths, which have the *narrowest*
lowering capability, have no gate. Independent of this design, this should be
fixed.

**I2 — `call_legality.EXTENSIONS` is keyed by stale alias names.**
`"c-progseq"` / `"cpp-progseq"` vs the registered `op-model-c` /
`op-model-cpp`. `entries_for("op-model-c")` silently returns zero extensions.
Fixing I1 without fixing I2 turns every legal `print`/`try_get` into a bogus
model error.

**I3 — `_DedupArgGroup` silently drops colliding options. RESOLVED
2026-08-14 (P3.T5).** The proxy now knows who is contributing: built-ins keep
first-wins, a plugin must namespace its options `--<target-name>-` and gets an
`OptionPolicyError` naming both targets on a collision. `-X NAME=VALUE` plus
`Target.opt`/`opt_bool` is the escape hatch for an option a plugin does not
want to spell out on the shared parser. Correct for
built-in variants, a trap for plugins. `--prefix`, `--include`, `--emit`,
`--root`, `--package`, `--link-style`, `--reg-style`, `--lifecycle` are all
already claimed on the shared parser.

**I4 — the dv-flow memento does not cover plugin code. RESOLVED 2026-08-14
(P4.T3).** `compute_memento` now hashes `target_provenance(target)` alongside
the target name: pssc's version for a built-in, the providing distribution's
version for a plugin, and a per-process sentinel that forces a rebuild when
neither can be established. The built-in case resolves through pssc's own
version rather than distribution metadata because a source checkout has none,
and hashing "unknown" there would rebuild on every invocation. A plugin upgrade
with unchanged inputs was a cache hit, so the build silently kept stale
generated code.

**I5 — `set_ctor_name()` is a process-global mutated per run.** Two compiles in
one process (which `pssc.compile` invites, and which the test suite already
does) can interfere.

**I6 — abstracting `_BodyEmitter` from three existing users is real risk.**
The C emitter's 1600 lines encode a lot of hard-won behaviour (write-only
locals, channel-output local widths, `-Wparentheses` bracketing, upward-ref
rejection). Extract behind golden-output tests, one language at a time, and
require byte-identical output at each step. If that cannot be held, the
abstraction is wrong.

**I7 — a plugin can generate wrong firmware under the pssc name.** Generated
banners say "Generated by pssc <version>". For plugin output that is misleading
during triage. Banners must name the plugin and its version too.

**I8 — the `Target` ABC's real contract is larger than it looks.**
`driver.compile` reads `tgt._last_value` (`driver.py:154`) — an undocumented
private attribute that is the only way to return an in-memory artifact. Promote
it to a documented `Target.value` property before third parties depend on the
private spelling.

**I9 — no dependency/ordering story between plugins.** Two plugins both
registering `"acme-c"`, or a style extending another plugin's target, are
undefined today. The collision-is-an-error rule (§2.2) covers the first; the
second should simply be unsupported and stated as unsupported.

**I10 — entry-point discovery costs import time on every CLI invocation.**
`cli.build_parser` calls `discover()` unconditionally before parsing, so every
`pssc --version` pays for every installed plugin. Defer discovery until the
target name is known where possible, and keep the entry-point module
dependency-free (the `dvflow/__ext__.py` convention already does this — apply it
to `pssc.targets` too, and say so in the doc).

**I11 — bus access is hard-coded at three independent sites, so tier A cannot
reach it.** `pssc_r<N>(pssc_bus(s), ...)` is emitted inline by
`c/lower_reg_model.py:224` (accessor bodies), `c/lower_progseq.py:1051` (raw
`read32`/`write32`) and selected by `c/lower_progseq.py:886` (which accessor a
register method resolves to). A company register-macro mandate — the most
likely tier-A request after naming — has nowhere to plug in, and the two
spellings can already drift from each other today. The funnel refactor in
§2.5.1 is a prerequisite for tier A being useful rather than cosmetic, and is
worth doing regardless.

**I12 — a register-access policy is the one extension point that can generate
silently wrong firmware.** Everything else a style overrides is visible in a
diff. An access hook that drops the read from a masked write, or renders a
write for a `READONLY` register, produces plausible code that misprograms the
device — the same class of failure the "no stubbed memory primitive" rule in
`c_progseq_gen._STUBS_BANNER` exists to prevent. Hence the may/may-not boundary
in §2.5.1, and hence it must be enforced in code (direction checks, default
masked-write composition) rather than stated in the doc.

**I13 — tier B is a fragile base class, and calling it a tier does not change
that.** Publishing override points couples upstream refactoring to third-party
code: the moment `emit_handles` is contract, restructuring how handles are
built becomes a breaking change. The mitigations in §2.6.2–2.6.3 (a small
marked surface, a checked-in manifest of it, declared pairings, the conformance
suite, differential baselines) reduce the blast radius; they do not remove the
coupling. The honest position is that tier B **buys adoption at the cost of
refactoring freedom in the C backend specifically**, and that trade should be
made deliberately and only for the backend we are willing to hold still.

**I14 — the override surface will be under constant pressure to grow.** Every
tier-B user who hits a wall will ask for one more `@overridable`. Without a
rule, the surface becomes the whole class within a year and tier C stops
existing. Proposed rule, to be written into the contributing guide: a method
becomes `overridable` only when a real extension needs it *and* it can be
described without reference to the backend's internal state. Everything else is
a reason to move the seam (a new policy hook, a new section) rather than to
publish another method.

**I15 — `derives_from` inheritance is the kind of thing that silently
half-works.** Three separate mechanisms have to honour it (call legality, CLI
options, `target_cfg`), and each fails differently when it does not: bogus
"unknown function" errors, a missing option that reads as its default, and a
capability claim the backend cannot meet. Each needs its own test at
registration time, not one integration test that happens to cover all three.

**I16 — the C++ backend could not lower the real operation model. RESOLVED
2026-08-14, and it was not one missing type arm.** Found while freezing the
Phase-0 snapshots: `op-model-cpp` raised `unsupported C++ type for
DataTypeEnum` three statements into the WB DMA model. Scoping the gap against
the model showed the enum was the first symptom of a backend that projected
only the ROOT component and about half the procedural subset — no
`foreach`/`match`/`break`/`yield`/`+=`, no cast or unary operator, no memory
primitive, no channel runtime, no sub-component tree, no API-type pass, and an
import interface that was emitted and then never used. What it *did* have was
the register model, which is the hard part and which needed no change.

The rebuild is in `targets/cpp/`; the shape and the decisions are documented
there. Three worth naming here because they are precedents for any later
backend:

* **Construction is two-phase** (`explicit C(seam&)` then `initialize(...)`),
  because a parent computes its children's base addresses in its own
  constructor body — which runs after the children, as members, already exist.
  `pssc::reg` holds a bus *pointer* rather than a reference so a group can be
  rebound; that is the one runtime change.
* **Enumerators do not survive into the IR.** `return PENDING` arrives as
  `ExprConstant(2)`. C converts int to enum implicitly so the C backend never
  had to notice; C++ does not, which is how this was found. The emitter looks
  the value back up in the enum's own table and emits the name, falling back to
  a `static_cast` when nothing matches.
* **A register access needs no translation at all.** The C++ register model
  mirrors the PSS structure, so `regs.channels[i].CSR.write_val_masked(m, v)`
  is already the C++ for itself — the emitter's job there is to REFUSE a method
  `pssc::reg` does not have, not to spell one.

The C++ golden config now runs on the real model, and the backend passes the
same trace-asserted behavioural gate as the C one, against the same mock, with
both mutation checks.

**I17 — a parent cannot reach a child's channel in the C backend.**
`ch[i].wake.try_put(1)` — a parent posting to a child's channel — raises "call
to 'ExprAttribute' has no lowering in the C target". Found while bringing the
C++ backend up; C++ supports it now (the child names its parent a `friend`,
which is what PSS component semantics say), so the two backends differ. It does
not affect the WB DMA model, whose only such call is `notify_irq()`, gated off
on both profiles by `HAVE_EVENT_WAIT` — which is exactly why nothing caught it.
The C fix is small (`&s->ch[i].wake` instead of `&s->wake`); the reason to do it
is that a capability difference between backends is a trap for a model author,
who has no way to know which target will refuse.

---

## 5. Overlooked opportunities

**O1 — there is no Python operation-model style at all.** `python` is the live
`zdc` class target, not an op-model. The first thing a plugin author is likely
to want has no in-tree example to copy, and §2.4's walker abstraction would be
validated by exactly one language. **Ship a minimal `op-model-py` in-tree as the
worked example** — it is simultaneously the proof the seams are real, the
reference the doc points at, and probably useful on its own for cocotb-driven
bring-up.

**O2 — separate "style" from "language" explicitly.** Half of what an
organisation wants to change is naming and formatting, not semantics. §2.5's
`StylePolicy` makes that a first-class, ~100-line extension instead of a fork,
and it is independently shippable ahead of everything else here.

**O3 — emit a machine-readable manifest.** `--emit-manifest model.json`
describing what was generated — components, operations and their signatures,
register groups with folded offsets, generated file roles, the ABI-affecting
settings the C header already prints as prose. Downstream company tooling
(doc generation, traceability, a test-list generator) then does not re-parse
generated C to find out what exists. This is nearly free — the `OpModel` in
§2.3 *is* the manifest — and it is probably more valuable to an adopting company
than a custom emitter.

**O4 — determinism as a contract, not a hope.** Several collection walks use
`set()`/`id()` keying. Assert byte-identical regeneration in the shared test kit
and in CI for built-ins. Generated code that churns in version control destroys
the review signal that makes generated code reviewable.

**O5 — the `OpModel` is a stable public API worth naming.** Once it exists, a
consumer that wants neither codegen nor PSS internals (a checker, a linter, a
coverage-model generator, a spreadsheet) can `pssc.op_model(sources, root=...)`
and get the elaborated structure. That is a bigger surface than "add a
generator style", and it costs one public function on top of this work.

**O6 — style/target conformance suite.** Publish the behavioural expectations
as an importable suite (`pssc.testing.conformance`) that a plugin runs against
its own output: every operation reachable, every register accessor addresses
what the offset fold says, no undeclared symbol emitted, regeneration stable.
It turns "does my backend work" from a judgement call into a command.

**O7 — reuse `--reg-fields`-style spelling knobs across languages.** The SV
target's `named`/`folded` choice (`progseq_tgt.py:57`) is a per-language
accident of a general idea: *how much of the model's intent survives into the
generated text*. Generalising it (`--fidelity`) would give every style — and
every plugin — the same lever, and gives the C backend the named-field spelling
it currently cannot produce.

**O8 — `comments.py` has no `#` style.** Two lines to add `HASH`, and it blocks
every scripting-language backend until it exists.

**O9 — the memory-access funnel (§2.5.1, `MemAccess`) is worth more than the
plugin story.** Once every
register and memory touch goes through one object, several things that are
currently out of reach become small: a trace/logging seam (wrap every access,
emit a register-level transcript from generated firmware), an access-checking
build (assert direction and width at run time under a debug flag), the
named-field spelling the SV target has and the C target does not (O7), and a
generated register-access *audit* — the manifest of O3 with the actual call
sites. All of that is blocked on the same refactor, and none of it needs a
plugin.

**O10 — tier B's mechanism is how pssc should express its own variants.**
`op-model-c` already carries eight boolean/enum knobs (`--link-style`,
`--mem-access`, `--reg-style`, `--lifecycle`, `--header-only`, `--yield`,
`--match-default`, `--message-style`), and the `c-host`/`c-embedded`/
`*-presolved` family is four near-identical targets. Some of those are
genuinely orthogonal settings; others are *variants*, and a variant expressed as
a flag threaded through fifteen call sites is exactly what a subclass expresses
better. Once §2.6.1 exists, the built-ins get to use it — which is also the
best possible validation that the override surface is the right one, because
we become its first consumer. If a built-in variant cannot be expressed as a
subclass, no third-party one will be either.

**O11 — the escalation path is a feature worth stating and testing.** A→B→C is
only cheap if A's work survives into B. It does, by construction (a subclass
still has `self.style`), but that should be demonstrated by an in-tree example
that starts as a policy and adds one override, and by a test that the policy
still applies to the un-overridden methods. Otherwise the claim decays quietly
the first time someone makes a section emitter stop consulting the style.

---

## 6. Suggested sequencing

Each step is independently shippable and independently useful.

| Step | Work | Unblocks |
| --- | --- | --- |
| 1 | Fix I1, I2, I5; add `comments.HASH` (O8) | correctness now |
| 2 | `OpModelTarget` + `OpModel` (§2.3); port SV/C/C++ onto it, byte-identical output | tiers B–D |
| 3 | Turn on `discover()` (§2.2) + `register_extension` (§2.7a) + option namespacing (§2.8) | plugins exist |
| 4 | `pssc.testing` (§2.10) + dv-flow memento fix (I4) + `register_filetype` | plugins are safe |
| 5a | **Bus funnel** (§2.5.1, I11): route the three access sites through one object, byte-identical output | O9's trace/check/audit work, and 5b |
| 5b | `StylePolicy` for C (§2.5) — naming half, then the access hooks + their enforcement (I12) | tier A — likely the most-used |
| 6a | **C backend becomes a class** (§2.6.1): free functions → methods, sections pipeline, no behaviour change | tier B |
| 6b | `@overridable` + surface manifest + pairings (§2.6.2–3), `derives_from` (§2.6.5), `assert_differs_from_baseline` | tier B is safe |
| 7 | `BodyWalker` extraction (§2.4), C first | tier D |
| 8 | In-tree `op-model-py` (O1) + manifest (O3) | validation + the wider win |

Steps 1–4 are the minimum that makes an external style possible *and* safe.
Steps 5–8 are what make it cheap. Step 5a is the one item on this list that
pays for itself with no plugin in sight, and is a reasonable place to start if
the rest is deferred.

Note the ordering of 6a and 6b: **6a is purely internal and worth doing
regardless** — the C backend's 200-line `generate()` free function and its
module-level lowering functions are hard to test in isolation today, and the
sections pipeline makes the ordering constraints explicit instead of implicit
in the order of appends. 6b is where the compatibility obligation starts, so it
should not ship until there is a real tier-B extension to validate it against.
Publishing an override surface with no user is how you publish the wrong one.
