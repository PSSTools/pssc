# `op-model-py`: async form + generated import API — implementation plan

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **implemented**  ·  2026-08-18

## What landed differently from this plan

Five things, each because the code said so:

1. **T-D5 is a HOIST, not parenthesisation.** The plan proposed `(await f())`
   in subexpression position. Instead every awaited call becomes a statement of
   its own and the expression gets the name of its result
   (`lower_progseq._awaited`). Same defect closed, and the position where the
   precedence question can be asked no longer exists.
2. **Two hazards the plan did not name, both found by writing it.**
   * A `while` whose CONDITION crosses the seam cannot hoist in front of the
     loop — the read would happen once and the loop would spin on that value
     forever. `stmt_while` emits a bottom-tested loop when the test hoists.
   * `&&`/`||` SHORT-CIRCUIT, so hoisting the right operand would perform an
     access the model says is conditional. `expr_bin` refuses it with a
     diagnostic rather than lowering it. This is the one thing the sync form
     can render and the async form cannot.
3. **T-C4's scaffolding became T5.2 instead of being deleted.** Byte-identity
   was never available: the Protocol is coloured in Phase B by design (T-B1
   items 4–5). The comparison is structural — both modules parsed, `async`/
   `await` erased, ASTs compared — which is stronger than the textual form the
   plan described and immune to layout.
4. **A pre-existing gap surfaced.** `notify_irq()` posts to `ch[i].wake`, a
   channel on a SUB-COMPONENT, which this backend never lowered because no form
   had `HAVE_EVENT_WAIT` true until now. `_chan_ref` replaces `_chan_name` and
   walks the declared field types. Not async-specific: `--target-cfg
   HAVE_EVENT_WAIT=true` hit it in the sync form too.
5. **The async `Chan1` takes the platform's `event()`**, rather than defaulting
   to `asyncio` with injection available. A generated model that built its own
   `asyncio.Event` would not be driven by a cocotb scheduler, which is the run
   this form exists to make possible — so `event()` is a Protocol member and the
   generated constructor passes it.

Also: running T5.8 with mypy actually installed found that the generated
module's `_RegValue.LAYOUT = ()` / `_Struct.FIELDS = ()` do not type-check —
inferred as the empty-tuple type, which no subclass can override. Both are now
annotated. A test that had only ever been skipped would not have found it.

Design: `docs/op-model-py-async-design.md` (reviewed; decisions D-Q1…D-Q9)
Code: `packages/pssc`

Back-compat is **not** a constraint on this work (decided 2026-08-18). Where the
design hedged — deprecating `pssc_rt.Bus`, keeping `bus=` working as a keyword,
staging the rename — the plan takes the direct path and says so.

---

## 1. Shape of the work

Six phases. Each ends at a green suite, and each is a commit.

| | Phase | What lands | Depends on |
| --- | --- | --- | --- |
| **A** | Rename the seam | `_bus` → `_imports` throughout; `py-default` golden re-flowed | — |
| **B** | Generated import API | `<Root>ImportApi` Protocol, `import_api` section, `__init__` annotation, `pssc_rt.Bus` deleted, `check_import_api()` | A |
| **C** | The `--py-await` knob | option, plumbing, mode-dependent `target_cfg` + call legality; **no** emitter change yet | B |
| **D** | Async lowering | `async def` / `await` per the colouring rule; `yield_()`; `pssc_rt_async.py` | C |
| **E** | dv-flow task | `pssc.OpModelPy`, flow.yaml, skill block | D |
| **F** | Docs | `docs/`, `AGENTS.md`, module banner, design-doc status | E |

**Why C before D.** The knob, the legality table and the `target_cfg` publication
are the part that touches shared target infrastructure; the emitter change is the
part that touches only `targets/py/`. Splitting them means the risky change lands
with the existing (sync) output as its regression: after C, `--py-await async`
must still produce *byte-identical sync output*, because nothing in the emitters
reads the mode yet. That is a strong assertion, it is cheap, and it is
unavailable if the two land together. See T-C4.

---

## 2. Phase A — rename the seam (D-Q5, D-Q8)

Mechanical, no behaviour change, but it re-flows the 1399-line golden, so it goes
first and alone.

**T-A1.** `targets/py/backend.py`
* `emit_constructor`: `params = ["self", "bus"] + …` → `["self", "imports"] + …`;
  body line `self._bus = bus` → `self._imports = imports`.
* Docstring of `emit_constructor` says "the bus, the base, …" — reword.

**T-A2.** `targets/py/lower_progseq.py` — five call sites emit `self._bus.…`:
`_mem_call`, `_message_call`, `_model_call` (the declared-import branch),
`_sub_ctor_call` (passes `self._bus` to the child constructor), and the register
branch reached through `lower_reg_model`. All become `self._imports`.

**T-A3.** `targets/py/lower_reg_model.py` — `emit_accessor` emits
`self._bus.read{width}(…)` / `self._bus.write{width}(…)`.

**T-A4.** Emit the attribute name from ONE place. Add `IMPORTS_ATTR = "_imports"`
to `targets/py/naming.py` and have all of the above reference it. Rationale: the
name currently appears as a literal in three modules; a second rename would be
this same three-file sweep again, and the constant makes the async work's own
edits (which touch every one of those call sites) impossible to get half-right.

**T-A5.** Regenerate `golden/py-default/` — `PSSC_GOLDEN_REGEN=1
scripts/regen_golden.py`, never by hand.

**T-A6.** `tests/progseq/test_op_model_py.py` — no assertion names `_bus` today
(checked); confirm and fix if the sweep says otherwise.

**Gate A:** full suite green; `git diff --stat` on the golden shows only
`_bus`→`_imports` lines.

---

## 3. Phase B — the generated import API (R6, D-Q6, D-Q9)

### 3.1 New emitter

**T-B1.** `targets/py/lower_import_api.py` (new, ~120 lines). One entry point:

```python
def lower_import_api(model, *, await_style: str) -> List[str]
```

Emits the `@runtime_checkable class <Root>ImportApi(Protocol)` block. Members, in
this order:

1. **Access widths actually used.** Collect from two sources and union them:
   `reg_layout.collect_accessors()` gives every register's `prim_bits` (this is
   the same walk the accessors are folded from, so the Protocol cannot disagree
   with them), and the body walk gives direct `read<N>`/`write<N>` memory
   primitives — `_MEM_PRIMS` in `lower_progseq.py` is already the table. Emit
   `read<N>` only if some accessor is readable at that width, `write<N>` likewise.
2. **`message`**, if any body calls `message`/`print`.
3. **Every DECLARED `import target function`** — `model.imports` — not only every
   called one. This is the C++ emitter's rule, quoted in the design; the
   docstring should say so and point at `cpp/lower_progseq.emit_import_api`.
   `model.imports` is `Mapping[str, fn]`, so `model.func_kind(fn)` separates
   `IMPORT_TASK` (coloured in async form) from `IMPORT_SOLVE` (never coloured).
4. **`yield_()`**, iff `await_style == "async"` (Phase D wires the caller; the
   member is emitted here so B and D do not both edit this file).
5. **`event()`**, iff the model declares channels AND `await_style == "async"`.
   Not async (§3.5 of the design).

Declared `import solve function`s are emitted **without** colour in both forms.

**T-B2.** Signatures. Parameter names come from the PSS declaration, types from
`lower_api_types`' existing PSS→Python mapping. Where the mapping has no answer
(a struct-typed import argument), emit the generated value-class name; where
there is genuinely none, emit no annotation rather than `Any` — an unannotated
parameter is honest, `Any` is a claim.

**T-B3.** Docstring (D3 in the design). Generated prose, not from the model:
what the platform must supply, that it is structural, and that `pssc_rt.MemoryBus`
is one implementation.

### 3.2 Wiring

**T-B4.** `targets/py/backend.py`:
* `module_sections()` gains `("import_api", self.emit_import_api)` **between**
  `runtime_imports` and `api_types`.
* `emit_runtime_imports` also emits `from typing import Protocol,
  runtime_checkable`. It is no longer conditional on channels — restructure it as
  "the module's imports", channels contributing `Chan1` on top.
* `emit_constructor` annotates: `def __init__(self, imports: <Root>ImportApi,
  …)`. **Every component class, not just the root** — a sub-component takes the
  same object.
* `emit_banner`: the usage snippet keeps working; no change until Phase D (D1).

**T-B5.** One Protocol per model, named for the ROOT, shared by every component
class. Rejected alternative: one Protocol per component. A sub-component is
constructed by its parent with the parent's object, so per-component Protocols
would be a set of types that are all satisfied by the same value and can never
diverge — a distinction with no referent.

### 3.3 Runtime

**T-B6.** `share/py/pssc_rt.py`:
* **Delete `Bus`.** No deprecation shim, no alias (back-compat waived).
* `MemoryBus` becomes standalone: it currently inherits `Bus` only for the
  `message()` default, which moves into it verbatim.
* Add `check_import_api(obj, proto) -> List[str]`. Returns a list of human-
  readable complaints, empty when satisfied. Checks, per member of
  `proto.__protocol_attrs__`: present; callable; arity compatible
  (`inspect.signature`); and **`inspect.iscoroutinefunction` matching the
  protocol member's own colour**. The last is the one `isinstance` cannot do and
  the reason this function exists.
  * It must see through `functools.partial` and bound methods — use
    `inspect.unwrap` and test `iscoroutinefunction` on the underlying function.
  * A class that returns an awaitable from a plain `def` (a legitimate pattern)
    would be reported as sync. Document that in the docstring rather than trying
    to detect it: the check is a diagnostic, not a gate, and nothing calls it
    automatically (D-Q6).

**T-B7.** Module docstring of `pssc_rt.py` is currently built around "THE BUS IS
A PROTOCOL, NOT A BASE CLASS". That is now literally true and stated by generated
code; rewrite the paragraph to point at the generated `<Root>ImportApi` as the
authority and describe this file as *implementations and helpers*.

**Gate B:** `py-default` golden regenerated; new tests T5.1, T5.6, T5.7 green.

---

## 4. Phase C — the knob (D-Q2, D-Q1, D-Q3)

**T-C1.** `targets/py_progseq_tgt.py` — `add_args` gains:

```python
parser.add_argument("--py-await", dest="py_await", choices=("sync", "async"),
                    default="sync",
                    help="op-model-py: sync (default) or async/await API form")
```

**T-C2.** `target_cfg` becomes option-dependent (D-Q1). Today it is a class
attribute; `Target.prelude(opts)` already takes `opts`, so override:

```python
def resolved_target_cfg_for(self, opts):
    return {"HAVE_EVENT_WAIT": opts.py_await == "async",
            "HAVE_RUNTIME_SOLVER": False}
```

and have `prelude()` use it. Keep the class attribute as the sync answer so
`pssc targets` and anything else reading it statically still gets a truthful
default. `--target-cfg HAVE_EVENT_WAIT=false` continues to override, and gives an
async model that spins.

**T-C3.** Call legality becomes mode-dependent. `register_extension` is keyed by
target name and is called once in `__init__` today. Move the registration into a
per-run hook that sees `opts` and re-registers with `replace=True`:
* sync: today's entries — `get`/`put` unsupported with `_NO_PY_BLOCKING_CHANNEL`.
* async: `get`/`put` **renderable**; the `unsupported` text goes away.
The `_NO_PY_BLOCKING_CHANNEL` message must be rewritten — it currently says "this
backend generates no scheduler", which stops being true of one of the two forms.
New text names the form: *"…in the sync form. Generate with `--py-await async`,
or use try_get/try_put."*

> **Hazard, and where it is caught.** Registration now happens per run, so two
> generations in one interpreter must each re-register before lowering. Test T5.2
> generates both forms in one process, in both orders, and is therefore the test
> that fails if the hook is placed wrong. Say so in the hook's docstring.

**T-C4.** **The phase-C assertion:** with the emitters untouched, `--py-await
async` must produce output byte-identical to `--py-await sync` *except* for what
`HAVE_EVENT_WAIT=true` changes in the model itself (on WB DMA: `notify_irq`
appears, `wait_hint` takes the `wake.get()` branch — which will not yet lower,
and must fail loudly rather than silently). Write this as a temporary test,
assert it, then delete it in Phase D. It is scaffolding, and scaffolding that
stays becomes a false claim two commits later.

**Gate C:** suite green; `--py-await async` on the bundled model (no channels)
produces the sync module byte-for-byte.

---

## 5. Phase D — async lowering

### 5.1 The colouring, in one place

**T-D1.** Both emitters take `await_style`. Add to `targets/py/naming.py`:

```python
def def_kw(await_style):    return "async def" if await_style == "async" else "def"
def await_kw(await_style):  return "await " if await_style == "async" else ""
```

Every site below uses these two rather than testing the mode inline. The point is
that the *list of coloured constructs* becomes greppable — `def_kw(` — which is
what makes the design's colouring table auditable against the code.

**T-D2.** `lower_reg_model.emit_accessor` — `read_val`, `write_val`, `read`,
`write`, `write_val_masked` take `def_kw`; **`addr` does not**. Their internal
calls take `await_kw`:
* `return {aw}self._imports.read{width}({addr_call})` — note `addr_call` is
  itself *not* awaited.
* `write_val_masked`: `cur = {aw}self.<stem>_read_val(...)` and
  `{aw}self.<stem>_write_val(...)`.

**T-D3.** `backend.emit_operation` — `def_kw` on every PSS target function.
`emit_sub_accessors` and `emit_constructor` keep plain `def` (design §3.1, §3.2).

Solve functions need no filtering, and this was checked rather than assumed:
`OpModel.operations()` already returns only `FuncKind.EXPORT_OP`
(`targets/op_model.py:196`), so an exported solve function is not emitted as a
method at all today. Colouring every member of `operations()` is therefore
exactly the design's rule, with nothing left to exclude.

**T-D4.** `lower_progseq._BodyEmitter` — awaited call sites:
* `_mem_call` → `{aw}self._imports.read32(...)`
* `_message_call` → `{aw}self._imports.message(...)`
* `_model_call`, both branches: another operation of this component
  (`{aw}self.foo(...)`) and a declared **target** import
  (`{aw}self._imports.foo(...)`). The **solve** import branch stays bare.
* `_chan_call`: `get`/`put` → `{aw}self.<chan>.get()`; `try_get`/`try_put`
  unchanged (design §3.5).
* `_reg_call` → the accessor calls it renders become awaited.
* `_CtorMixin` overrides — **no** awaits; a constructor is `Ctx.SOLVE` and a
  target call there is already a diagnostic.

**T-D5. Parenthesise every awaited call.** `await` is legal in an arbitrary
expression position but binds loosely; an awaited call rendered into a
subexpression (`(cur & ~mask)` where `cur` came from a read; `wb_dma_csr_s.unpack(
<read>)`) must be `(await …)`. Do this in the one helper that formats an awaited
call, not at each site. In sync form the helper adds no parentheses, so the
golden does not gain noise — and T5.2 (token equivalence) enforces exactly that:
stripping `await ` from the async output has to yield the sync text, which it
will not if async parenthesises where sync does not.

> This is the single most likely source of a silent bug in the phase, because the
> failure is a *wrong value*, not a syntax error: `await self.f() & mask` parses
> and means `await (self.f() & mask)`. T5.3 (behavioural equivalence, identical
> access logs) is what catches it.

**T-D6.** `stmt_yield` — sync: today's comment. Async: `{pad}await
self._imports.yield_()`. The comment explaining the sync no-op stays on the sync
branch; the async branch gets its own comment pointing at
`docs/op-model-export-design.md` §4.4 (the wait-primitive contract), because the
one thing a reader must not conclude is that this may become an interrupt wait.

**T-D7.** Delete the T-C4 scaffolding test.

### 5.2 Async channel runtime

**T-D8.** `share/py/pssc_rt_async.py` (new). `Chan1` with the same depth-1
restriction and the same `try_get(out)` cell protocol as the sync one, plus:
* `async def get(self)` / `async def put(self, value)`, implemented over an
  injected event factory.
* Constructor takes `event=None`; default resolves to `asyncio.Event` imported
  **lazily inside the factory**, so a cocotb harness that never touches asyncio
  never imports it.
* The event protocol is `await ev.wait()` / `ev.set()` / `ev.clear()` — satisfied
  by both `asyncio.Event` and `cocotb.triggers.Event`. Document that this
  compatibility is the whole reason the type is not named, and that a scheduler
  whose Event differs is supported by passing a factory.
* `get()` must re-check the flag after waking (spurious wake, and two waiters).
  Depth 1 with two concurrent `get`s is a model error, but the runtime should not
  silently hand both the same value — raise or serialise, and say which in the
  docstring.

**T-D9.** `py_progseq_tgt.core_file_names` returns `["pssc_rt.py"]` in sync form
and `["pssc_rt.py", "pssc_rt_async.py"]` in async. `pssc_rt.py` in both because
`MemoryBus` and `check_import_api` are wanted either way.

**T-D10.** `backend.emit_runtime_imports` — a model with channels imports `Chan1`
from `pssc_rt` (sync) or `pssc_rt_async` (async). The generated comment says
which and why.

**Gate D:** all of §6 green, including the new golden.

---

## 6. Phase E — dv-flow task

**T-E1.** `dvflow/build.py`: `_py_progseq_overrides(p)` mapping
`root`→`progseq_root`, `module`→`py_module`, `await_style`→`py_await`,
`core_copy`→`progseq_core_copy`, plus `_target_cfg_overrides(p)`. Add
`"OpModelPy": ("op-model-py", _py_progseq_overrides)` to `TASKS` and the
`async def OpModelPy(...)` entry point.

**T-E2.** `dvflow/flow.yaml`: the `pssc.OpModelPy` task, `with:` keys as in the
design §2.2. No `yield_mode` key (design §3.4).

**T-E3.** Same file, the `AgentSkill` block: delete the sentence "The
`op-model-py` target has no dv-flow task yet; drive it from the CLI…" and add
`op-model-py` to the task list above it, with `await_style` called out.

**T-E4.** `.py` already classifies as `pythonSource` in `dvflow/common.py` — no
change needed. Verify with the task test rather than by reading.

**Gate E:** `test_manifest.py`-style task-contract test extended to cover
`OpModelPy`'s override keywords (the suite already asserts this contract for the
other three).

---

## 7. Phase F — documentation

**T-F1.** `docs/cli.md`
* Line ~76: the sentence about which targets claim `HAVE_EVENT_WAIT` now needs
  `op-model-py` to answer *both ways*. Rewrite to say the answer is the
  `--py-await` mode.
* Line ~141 options table: add `--py-await {sync,async}`.

**T-F2.** `docs/progseq.rst` §"backend choice" — `op-model-py` row currently says
"duck-typed bus object". Update to the generated Protocol, and add the async form
to the one-line use case (cocotb bring-up is now the *primary* one).

**T-F3.** `docs/progseq_design.rst` — the `op-model-py` paragraph gains the
import-API section and the two runtime files.

**T-F4.** `docs/lowering-call-legality.md` — document that `op-model-py` is the
first target whose Tier-2 set is **mode-dependent**, and how the per-run
registration works. This is new behaviour in shared infrastructure and is the
thing a future backend author will copy.

**T-F5.** `docs/custom-generator-styles.md` §"op-model-py as an example" — it
quotes the target class; refresh the quote and note that `--py-await` is a target
option, not a style, with the one-line reason (a style may not change meaning).

**T-F6.** `AGENTS.md` — the `op-model-py` paragraph.

**T-F7.** `share/py/pssc_rt.py` and `pssc_rt_async.py` module docstrings are
user-facing documentation; they are the deliverable for T-B7 and T-D8, not a
separate task. Listed here so review sees them in the docs set.

**T-F8.** The generated module banner (D1). Async form's usage snippet:

```
    import asyncio
    from wb_dma import WbDma
    from pssc_rt import AsyncMemoryBus

    async def main():
        dut = WbDma(AsyncMemoryBus(), 0x1000)
        await dut.ch_at(0).transfer_single(cfg)

    asyncio.run(main())
```

Both forms' snippets must be *runnable as written* against the shipped
`MemoryBus`/`AsyncMemoryBus`. T5.9 executes them.

**T-F9.** `docs/op-model-py-async-design.md` — status line to
`implemented`, with the commit range.

---

## 8. Tests

Existing file `tests/progseq/test_op_model_py.py` grows; it already imports and
drives generated modules in-process, which is why this target can be tested for
*behaviour* rather than for spelling.

### 8.1 New tests

| | Test | Phase | What it would catch |
| --- | --- | --- | --- |
| **T5.1** | Protocol lists exactly the model's demands: scrape `self._imports.<m>(` from the emitted text, compare to the Protocol's members. Both directions. | B | A width declared but never used (dead demand on implementers); one used but not declared (the Protocol is a lie) |
| **T5.2** | **Token equivalence.** Generate both forms with `--target-cfg HAVE_EVENT_WAIT=false`; strip `async ` / `await `; assert textual equality except the `yield` line. Run both orders in one process. | D | Any async-only drift in structure, names, addresses or docstrings; **and** the C-phase registration hazard |
| **T5.3** | **Behavioural equivalence.** Same operation sequence on `MemoryBus` (sync) and `AsyncMemoryBus` (async); assert the two `log`s are identical — width, address, data, order. | D | The T-D5 parenthesisation bug, and any await placed where it changes evaluation order |
| **T5.4** | Async + `HAVE_EVENT_WAIT=true`: `wait_completion()` suspends and is woken by a concurrent task calling `notify_irq()`; assert the CSR read count is the minimum, i.e. it did **not** spin. | D | The whole D-Q1 payoff being silently a poll |
| **T5.5** | `get`/`put` render in async form and are still a clean diagnostic in sync form, naming `--py-await async`. | C/D | The legality table and its message |
| **T5.6** | `check_import_api` on: a complete sync bus (`[]`), a complete async bus (`[]`), each crossed with the other form's Protocol (**non-empty, naming the mis-coloured methods**), and a partial implementation (names the missing ones). | B | D-Q6's entire value |
| **T5.7** | `isinstance(MemoryBus(), <Root>ImportApi)` is `True` and does not raise — i.e. `@runtime_checkable` really is emitted. | B | Emitting `Protocol` without the decorator, which fails only at the user's `isinstance` |
| **T5.8** | Type-check fixtures: an async model paired with a sync bus **must** be rejected; each form paired with its own bus **must** pass. | D | The static half of R2 |
| **T5.9** | The banner snippet in each generated module executes. | F | Documentation that was true when written |
| **T5.10** | Async module with no channels imports only `typing`; with channels, `typing` + `pssc_rt_async`. | B/D | The §3.6 import invariant, in its tightened form |

### 8.2 Changed tests

* `test_the_generated_module_imports_nothing_when_the_model_has_no_channels` —
  rename and re-state: *nothing outside the standard library, and nothing from
  `pssc_rt` unless the model declares channels*.
* `test_it_produces_a_module_and_the_runtime` — async form produces three files.
* Anything asserting on `pssc_rt.Bus` — deleted with the class.

### 8.3 Goldens

* `golden/py-default/` — re-flowed twice (Phase A: the rename; Phase B: the
  Protocol section).
* `golden/py-async/` — new `Config("py-async", "op-model-py", ("--py-await",
  "async"), "the async form: awaited seam, yield_() on the import API")` in
  `tests/progseq/golden_util.py`.

Both regenerated with `PSSC_GOLDEN_REGEN=1 scripts/regen_golden.py` only.

### 8.4 Test-only dependency (D-Q7)

T5.8 needs mypy or pyright. **Recommend mypy**, as a `test` extra in
`pyproject.toml`, and mark T5.8 `skipif` it is absent rather than failing the
suite for a developer who did not install extras — with the CI job installing the
extra, so it is skipped locally at worst and never silently absent in CI.

### 8.5 What is deliberately not tested here

A cocotb-in-simulator run. It is the obvious next thing, it needs a simulator in
the loop, and none of the design decisions depend on it — the duck-typed `Event`
(T-D8) is what keeps it reachable later without regenerating anything. Say this
in the test module's docstring so its absence reads as a decision.

---

## 9. Risks

| | Risk | Mitigation |
| --- | --- | --- |
| **R-1** | `await` precedence silently changes a computed value (T-D5) | One formatting helper; T5.3 compares access logs, not text |
| **R-2** | Mode-dependent legality registration lands in the wrong place and leaks between runs in one process | T5.2 generates both forms in one process, both orders |
| **R-3** | The Protocol drifts from what bodies actually call | T5.1 scrapes the emitted text — the Protocol is checked against the *output*, not against the model twice |
| **R-4** | `HAVE_EVENT_WAIT=true` makes async form compile a *different model*, so token equivalence would be vacuous | T5.2 pins it false; T5.4 covers the true case separately. Both are needed and neither substitutes |
| **R-5** | Async `Chan1` correct under asyncio, wrong under cocotb | The event factory is injected, not imported; documented. Residual risk accepted until the simulator test exists (§8.5) |
| ~~R-6~~ | ~~`model.operations()` may not distinguish target from solve functions~~ | **Closed while writing this plan.** `FuncKind` already separates `EXPORT_OP` / `EXPORT_SOLVE` / `IMPORT_TASK` / `IMPORT_SOLVE`; `operations()` filters to `EXPORT_OP`; `model.imports` is `Mapping[str, fn]`, so the same classifier applies to declared imports. T-D3 and T-B1(3) are implementable as written |

No open technical unknowns. The remaining risks are execution risks, each with a
named test against it.

---

## 10. Estimate

Phases A, C, E are mechanical. B and D are the real work; F is real but bounded.

| Phase | Rough size |
| --- | --- |
| A | ~40 lines changed, one golden re-flow |
| B | ~200 new (emitter + runtime), ~60 changed |
| C | ~60 changed, mostly in the target class |
| D | ~150 changed across four emitters, ~120 new (`pssc_rt_async.py`) |
| E | ~60 new across two files |
| F | ~10 files, prose |
| Tests | ~400 lines, one new golden config |

The shared-infrastructure footprint is two things only: option-dependent
`target_cfg` (T-C2) and per-run call-legality registration (T-C3). Everything
else is inside `targets/py/` and `share/py/`.
