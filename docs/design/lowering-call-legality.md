# Call legality in the operation-model lowering path

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status (updated 2026-08-15): **implemented, and the registry is now load-bearing
on every op-model target.** The `FOLD` disposition, the tiered registry
(`targets/call_legality.py`) and the validation pass
(`targets/validate_calls.py`) are in place, and the gate runs from
`OpModelTarget.check()` — which is the shared base class of `op-model-sv`,
`op-model-c`, `op-model-cpp` and `op-model-py`, so all four are checked before
any file is opened. Phase 5 (the corpus sweep) has not been done.

The registry is also the **dispatch table** for the emitters that have moved to
the shared walk: `CallDispatch` (`targets/body_walker.py`) routes each call by
its `Disposition`, so the table that decides a call is legal and the table that
renders it are one table and cannot fall out of step. `op-model-c` and
`op-model-py` dispatch that way today; `op-model-sv` uses `BodyWalker` without
`CallDispatch`, and `op-model-cpp` has not been ported to either — both are
still gated, but their rendering and the registry agree only by test rather than
by construction.

> That distinction has teeth, and it bit once. While no C-path code read the
> registry, a wrong entry for C sat **inert** rather than failing: the
> `try_get`/`try_put` entries went on saying "no C runtime for `channel_c`" —
> citing a `_reject_channels` that had been deleted — for the whole time the C
> target was lowering both calls correctly. Corrected 2026-08-13, and pinned by
> `test_the_registry_agrees_with_what_the_c_emitter_actually_renders`. That test
> is what a column still holds for `op-model-sv` and `op-model-cpp`; for the two
> dispatching targets, the code holds it.
>
> `C_BUILTINS` survives in `targets/c/lower_progseq.py` as the C emitter's own
> claim set, cross-checked against the registry by that test.

For an extension author's view of the same registry — how to declare what your
target can lower — see
[`docs/custom-generator-styles.md`](docs/custom-generator-styles.md).

Two claims in the original design proved wrong when
implemented and are corrected in place: §3.1 (Tier 0 cannot be derived from the
IR) and §6 (the IR carries no source locations).

Scope: the three programming-sequence backends — `targets/sv/lower_progseq.py`,
`targets/c/lower_progseq.py`, `targets/cpp/lower_progseq.py` — plus the shared
`targets/progseq_model.py` and the init lowering in `targets/sv/lower_init.py`.

---

## 1. The defect

All three progseq backends render an unrecognized call by **emitting it
verbatim**:

```python
# targets/sv/lower_progseq.py:260-261
args = ", ".join(self.expr(a) for a in e.args)
return f"{self.expr(callee)}({args})"
```

```python
# targets/c/lower_progseq.py:303-304   (identical shape)
# targets/cpp/lower_progseq.py:129-130 (identical shape, with no reg hook at all)
```

Preceding that line is a chain of `if`-tests — address builtins, PSS exec
builtins, folded masked writes — each of which returns text or falls through.
The fallthrough is unconditional and unchecked. Any PSS call the backend has no
mapping for is copied into the output as though the target language had a
function by that name.

This is a **silent** failure mode. The generator exits 0, writes its files, the
dv-flow task goes green, and the artifact looks complete. The failure surfaces
later, somewhere else, as an unresolved symbol — and only if something actually
elaborates the generated class.

### 1.1 The instance that prompted this

`src/pss/wb_dma_c.pss:70` (the WB DMA operation model's constructor):

```pss
solve function void initialize(addr_handle_t base) {
    regs.set_handle(base);
    foreach (ch[i]) {
        ch[i].initialize(i,
            make_handle_from_handle(base,
                regs.get_offset_of_instance_array("bank", i)));
    }
}
```

lowers to `wb_dma_c_pkg.sv:423`:

```systemverilog
m_ch[i] = new(this, i, (base + m_regs.get_offset_of_instance_array("bank", i)));
```

`make_handle_from_handle` was recognized (`_ADDR_BUILTINS`) and folded to an
add. `get_offset_of_instance_array` was not recognized, so it was emitted — and
the generated `wb_dma_regs_c` declares no such method:

```
%Error: wb_dma_c_pkg.sv:423:47: Class method 'get_offset_of_instance_array'
        not found in class 'wb_dma_regs_c'
```

Two things make this worse than a plain missing feature:

* **pssc already knows this function is not callable.** `progseq_model.py:27`
  classifies it as `FuncKind.REG_OFFSET`, with the comment *"evaluated, not
  emitted"*. The classification exists and the emitter never consults it.
* **pssc already has the evaluator.** `progseq_model._array_base_stride()`
  evaluates exactly this function's affine body and returns `(base, stride)` —
  it is what the generator uses to fold `bank[i]` addresses inside
  `wb_dma_regs_c::new()`. The same call written by the *model author* gets none
  of that.

So the immediate bug is a missing fold. The reason it reached a user is the
missing gate, and the gate is what this document is mainly about: without it,
the next unmapped call fails the same way.

### 1.2 A second silent path, same file

`targets/sv/lower_progseq.py`'s `expr()` has **no trailing `raise`**. An
expression class it does not handle falls off the end and returns `None`, which
the callers interpolate into an f-string:

```python
return f"{self.expr(base)}.{e.attr}"      # -> "None.attr"
```

The C and C++ backends both end `expr()` with
`raise ValueError(f"unsupported expr {cn}")`. The SV backend should too. This is
in scope here because it is the same class of defect — an unhandled construct
producing text instead of a diagnostic — and it is a two-line fix.

---

## 2. Design principle

> A call that the backend cannot lower is a **compile error with a source
> location**, never emitted text.

Concretely: replace the open fallthrough with a **closed dispatch**. Every
`ExprCall` reaching a progseq backend is classified into exactly one
disposition. "Unclassified" is a diagnostic. There is no default branch that
emits.

This mirrors the rule `lower_init.py:20-23` already states for statements, and
which has held up well:

> **Anything not recognised raises.** An `init` is address binding; a statement
> this cannot lower is a binding that would silently not happen, and a component
> whose registers are bound to address zero is far worse than a build error.

The same argument applies to expressions, and to the other two backends.

---

## 3. The registry: three tiers

### 3.0 Why it is tiered

The support list is **not** currently a list. It is four ad-hoc copies, all
target-local, which disagree with each other and with the language:

| Location | Contents |
|---|---|
| `targets/sv/sv_builtins.py:23` | `message`, `print`, `error`, `fatal`, `yield` |
| `targets/sv/lower_stmts.py:23` | the same five, **as a separate literal**, with its own mapping |
| `targets/sv/lower_pure.py:397` | `message`, `print`, `fatal` — no `error`, no `yield` |
| `targets/sw_lower.py:48` | `print`, `message` |
| `targets/cpp/lower_progseq.py` | **`CPP_BUILTINS`** — the same claim set as C. *Updated 2026-08-14:* the C++ backend was rebuilt for the real operation model and now declares its claim set explicitly and renders every name in it, as C does. Before that it claimed nothing and `message(...)` was emitted verbatim into C++. |
| `targets/c/lower_progseq.py` | **`C_BUILTINS`** — `message`, `print`, `make_handle_from_handle`, `addr_value`, and the memory primitives. *Updated 2026-08-13:* the C target now declares its claim set explicitly and renders every name in it, which is the shape the rest of this document argues for. A name claimed with no rendering falls through to verbatim emission, which is the defect this registry exists to prevent. |

Meanwhile PSS 3.1 declares, in `std_pkg`: `print`, `format` (§21.1.2),
`message` (§21.1.3), `error`, `fatal` (§21.3), `urandom`, `urandom_range`
(§21.4), `format_string` (§19), and fourteen floating-point functions
(§21.5.2 Table 31 / Annex C). The front end's own stdlib
(`packages/pssparser/src/stdlib/std_pkg.pss`) declares a *third* set: it has
`print`, `message`, `format`, `format_string`, `urandom`, `urandom_range`, and
is missing `error`, `fatal` and all of the math.

The mismatch runs both ways and both directions are live bugs:

* `error()` / `fatal()` are mapped by the SV backend but **undeclared in the
  front end's stdlib** — core PSS per the LRM, unresolvable here.
* `urandom()` / `urandom_range()` **are** declared, so a model may legally call
  them, and they hit the verbatim fallthrough of §1 — a call to a nonexistent
  function in the generated output. Same defect as
  `get_offset_of_instance_array`, not yet tripped over.

A flat per-target list cannot fix this, because the targets genuinely differ in
capability — the C backend cannot support channels at all
(`c/lower_progseq.py:_reject_channels`) while the SV backend can. So the
registry is layered: **one common set every backend must implement, plus
per-target extensions that may add but never subtract.**

### 3.1 Tier 0 — the declared surface

What PSS says *exists*: `std_pkg`, `addr_reg_pkg`, `sync_pkg`. Tier 0
is **not** a support list; it is what lets the classifier tell

> *"`sqrt` is a PSS core-library function with no lowering for this target"*

apart from

> *"there is no function named `sqrtt`"*

— two different bugs needing two different fixes, and a flat whitelist reports
them identically.

**Correction (implementation).** This was specified as *"read from the front
end's stdlib declarations rather than restated in Python."* That is not
possible today: the declarations do not survive translation. `type_map` carries
`addr_reg_pkg::reg_group_c` and `reg_sized_c` with an **empty `functions`
list**, and package-scope functions (`print`, `read32`,
`make_handle_from_handle`) are not represented at all — the stdlib is parsed
(the AST exists, embedded in the C++ extension) but `ast2ir` keeps none of it.

So Tier 0 is a Python manifest, made safe by a cross-check rather than by
discipline: `test_call_legality.py::test_manifest_matches_the_front_end_stdlib`
regexes the declarations out of `pssparser/src/stdlib/*.pss` (46 names today)
and fails if any is in no tier. Drift breaks CI instead of shipping. Teaching
`ast2ir` to retain stdlib function declarations is the proper fix and is §10
item 8.

The cross-check earned its keep immediately: `urandom` / `urandom_range` /
`format` / `format_string` are declared by the stdlib, were in no target's
rendering table, and would have reached generated SystemVerilog as calls to
functions that do not exist. They now have renderings in `sv_builtins.py`.

### 3.2 Tier 1 — COMMON

The subset of Tier 0 (plus the structural dispositions) that **every** progseq
backend must render. A function belongs here only if all three hold:

1. it is declared in Tier 0, or is a structural construct of the lowering;
2. every target can render it with no runtime dependency that target may not
   have;
3. its argument and return types are inside the type mapper's common subset.

| Disposition | Contexts | Members |
|---|---|---|
| `UTILITY` | both | `message`, `error`, `fatal` |
| `ADDR` | both | `make_handle_from_handle`, `addr_value` — folded to arithmetic |
| `FOLD` | both | `get_offset_of_instance`, `get_offset_of_instance_array` — evaluated at generation time (§7) |
| `MEM` | target | `read8`…`read64`, `write8`…`write64` |
| `REG` | target | `read`, `write`, `read_val`, `write_val`, `write_val_masked`, `write_field`, `write_fields`, `write_masked`, `read_field` |
| `MODEL_OP` | target | a function declared on a component in the lowered subtree |
| `IMPORT` | both | a declared import function, routed through the import handle |
| `SUBCOMP_CTOR` | solve | a sub-component's constructor in an init body |

`ADDR` is in Tier 1 but is currently implemented **only in the SV backend**
(`_ADDR_BUILTINS`, `sv/lower_progseq.py:564`). C and C++ have no equivalent, so
`make_handle_from_handle` in a model compiled for C hits the verbatim
fallthrough today. Tier 1 membership is what makes that a contract violation
rather than an unnoticed gap. See §3.6 for what `ADDR` is and for the address
type each backend must use.

**The C and C++ gates have never exercised an `init` body at all.** Both run
against `examples/export/programming_seqs/dma_engine.pss`, whose constructor is
empty:

```pss
solve function void ctor(addr_handle_t base) { }        // dma_engine.pss:31
```

SV is better off: `examples/op_model` (the component-tree example behind
`test_op_model_sv.py`) has a real constructor that calls
`make_handle_from_handle`, so `ADDR` is covered there. But it places the channel
banks from **map constants**, not by asking the register group — which is the
spelling the WB DMA model left behind when its register package became generated
from SystemRDL. So a model-authored `get_offset_of_instance_array` was exercised
by nothing, anywhere. `dma_regs.pss:122` and `examples/op_model` both *declare*
the function, and the generator's internal fold consumes it, but no example ever
*called* it.

That gap is now closed by `tests/progseq/data/offset_fold.pss` and
`test_op_model_offset_fold.py` (§9, Phase 2) — a small dedicated model rather
than a change to either shared example, so no other suite's golden text moves.

### 3.3 Tier 2 — target extensions

A target declares what it adds. It may not remove anything from Tier 1; §3.4 is
how that is enforced.

Declared through `register_extension()`, by a built-in in `call_legality.py` or
by a plugin from its own package:

```python
from pssc.targets.call_legality import BOTH, Disposition, Entry, register_extension

register_extension("acme-c", [
    Entry("print", Disposition.UTILITY, BOTH, lrm="21.1.2"),
], inherit="op-model-c")
```

`inherit` is a **snapshot taken at registration**, not a live link. A derived
target starts from the base's answers, and a call the base learns to render
later does not silently become legal here — that would hand a derived backend a
call its emitter has no case for, which surfaces as an unresolved symbol in
somebody else's build. `op-model-cpp` is spelled out in full rather than
inherited from `op-model-c` for the same reason.

Registration refuses an entry that marks a Tier-1 name `unsupported`, and
refuses a second registration for a target unless `replace=True`. Read a
target's Tier 2 back with `extensions_for(target)` (canonical name or alias);
`registered_targets()` lists everything bound by the contract.

#### A Tier-2 set that depends on an option

Added 2026-08-18. `op-model-py` is the first target whose Tier 2 is not fixed at
construction: `--py-await sync` refuses blocking `get`/`put` and `--py-await
async` renders them, because only the second form has a scheduler to suspend to.
The mechanism is worth copying, and so are its two constraints:

* **Re-register per RUN, before `check()`.** `PyProgSeqTarget.run()` calls
  `_register_legality(await_style)` and then `super().run()`, which elaborates
  and then checks. The registry is process-global, so two generations in one
  interpreter must each re-publish; a hook placed after `check()` would gate the
  second run against the first run's answer. That is exactly what the
  both-orders test in `tests/progseq/test_op_model_py.py` fails on.
* **Construction registers the DEFAULT set**, so anything that asks what the
  target renders without a command line in hand — `extensions_for()`, a
  conformance report — gets the same answer `pssc targets` gives.

The matching capability question goes through `Target.resolved_target_cfg_for(opts)`,
which defaults to `resolved_target_cfg()` and exists for this case. The two
belong together: publishing `HAVE_EVENT_WAIT=true` without making `get`/`put`
renderable would produce a model whose `compile if` selects a branch the backend
then refuses.

| Function | SV | C | C++ | Why not common |
|---|---|---|---|---|
| `print` | ✅ `$write` | ⏳ | ⏳ | **Decision: print support is platform-specific.** `print` is the solve platform's console (§21.1.2 declares it `solve function`), so what it means on a given target is a property of that target, not of the language. SV supports it; C/C++ add it if a model needs it. |
| `CHANNEL` — `try_get`, `try_put` (target-only; `sync_pkg` declares all four `target function`) | ✅ `channel_c` in `pssc_reg_pkg` | ✅ `pssc_chan1_try_get/_try_put` in `share/c/pssc_chan.h` | ✅ `pssc::chan1<T>` in `share/cpp/pssc_chan.hpp` | **Updated 2026-08-14.** Both have a depth-1 runtime; `DEPTH > 1` is rejected by both. The C++ one is TYPED — C has one channel struct with a `uint64_t` payload, so a generated body there has to widen the model's own local to match, and a template does not. |
| `CHANNEL` — `get`, `put` (blocking) | ✅ | ❌ **rejected by name** | ❌ **rejected by name** | These suspend, and this backend has no scheduler to suspend to (`HAVE_EVENT_WAIT=false`). Refused with a diagnostic naming the offending function rather than lowered to a spin — a blocking call surviving into the C means the model asked for an event the target cannot deliver, which is a modelling error, not something to paper over. **`op-model-py` answers both ways**, per `--py-await`: the async form has a scheduler and renders them on `pssc_rt_async.Chan1`, and the sync form's diagnostic names the option. |
| `urandom`, `urandom_range` | ✅ `$urandom` / `$urandom_range` | ⏳ | ⏳ | Needs a PRNG in the core header, and seeding/determinism is a policy decision the SV target gets from the simulator for free. |
| `format`, `format_string` | ✅ `$sformatf` | ⏳ | ⏳ | Returns a PSS `string`; there is no `string` representation or ownership model in the C lowering. |

### 3.4 The contract, and how it is enforced

> A target may **extend** the common set. It may never **shrink** it.

Enforced twice — once at registration, once as a test over every target that
registered, plugins included:

```python
for target in registered_targets():
    missing = set(COMMON) - renderable(target)
    assert not missing, f"{target} does not render common calls: {sorted(missing)}"
```

Registration is where a plugin author finds out, at the point of the mistake.
The test is what keeps the built-ins honest and covers a plugin that is loaded
in the same process.

This is the whole value of tiering. Without it, "common" is an aspiration that
each backend independently fails to meet — which is precisely the state §3.0
documents.

### 3.5 Classification outcomes

The classifier returns one of four answers, and each gets its own diagnostic
(§6):

| Outcome | Meaning | Fix |
|---|---|---|
| `SUPPORTED` | in Tier 1, or in this target's Tier 2 | render it |
| `UNSUPPORTED_HERE` | in Tier 0, not renderable by this target | change target, or implement the extension |
| `WRONG_CONTEXT` | renderable, but not in this context (§4) | move the call |
| `UNKNOWN` | not in Tier 0 at all | typo, or an undeclared foreign function → declare it `import` |

`UTILITY`, `ADDR` and `FOLD` are *global* — they do not depend on a receiver.
The rest are recognized by method name on a receiver whose kind the model knows
(register group, register, channel, sub-component, import handle).

**Name-based matching is what exists today and this design does not change
it.** `_assign_from_call`'s docstring already records the resulting ambiguity —
a user component with a no-argument `get()` is rewritten as though it were a
channel. That is a pre-existing limitation, recorded rather than fixed, and it
is orthogonal: it produces the wrong *mapping*, not an unmapped call. Fixing it
needs receiver types in the emitter, which is a separate piece of work
(§8, item 3).

### 3.6 `ADDR`, and the address representation

`ADDR` is exactly two functions (`sv/lower_progseq.py:564`) — an add and an
identity:

```python
_ADDR_BUILTINS = {
    "make_handle_from_handle":
        lambda be, e: f"({be.expr(e.args[0])} + {be.expr(e.args[1])})",
    "addr_value": lambda be, e: be.expr(e.args[0]),
}
```

They collapse because `addr_reg_pkg` declares `typedef chandle addr_handle_t` —
in PSS a handle is **opaque**, obtained from the solve platform via an address
space, a region and a claim. A generated programming API has no address space
in it: the environment passes a base handle to `initialize()` and everything is
derived from that. Once a handle *is* an address, deriving one from another is
`+` and extracting its value is a no-op. There is no function left to call.

Only those two qualify out of the whole package. `make_handle_from_claim` takes
a solve-time allocation result and is Tier 0/unsupported; `read_bytes`,
`write_bytes` and `get_offset_of_path` take `list<>`; `get_mnemonic_*` /
`use_symbolic_reg_names` are solve-time symbolic naming with no runtime meaning;
`add_region` / `add_addr_space` build address spaces, which is the environment's
job. `ADDR` is the set of operations that touch a handle **without touching a
bus and without consulting the solver.**

#### The concrete type

**Decision: C and C++ must represent `addr_handle_t` as `uintptr_t`.**

| Backend | Today | Required |
|---|---|---|
| SV | `typedef bit [63:0] addr_handle_t` (`pssc_reg_pkg.sv:19`) | unchanged — SV has no pointers, and its seam is always a call into a testbench, never a dereference |
| C | `typedef uint64_t pssc_addr_t` (`share/c/pssc_mem.h:18`) | `typedef uintptr_t pssc_addr_t` |
| C++ | `using addr_t = std::uint64_t` (`share/cpp/pssc_reg.hpp:21`) | `using addr_t = std::uintptr_t` |

The C header already concedes this. Every one of the eight MMIO primitives in
`share/c/pssc_mem_mmio.h` casts through `uintptr_t` to get a dereferenceable
pointer:

```c
static inline void pssc_w32(const void *s, pssc_addr_t a, uint32_t d)
{ (void)s; *(volatile uint32_t *)(uintptr_t)a = d; }
```

So on a 32-bit target the `uint64_t`→`uintptr_t` narrowing happens **anyway**,
eight times, invisibly, at the point of access. Putting `uintptr_t` in the
typedef moves that truncation from eight silent casts to one declaration, and
lets the eight casts go. It also makes the type mean what it says: `uintptr_t`
is the integer type guaranteed to round-trip a `void *`, which is precisely the
contract the `mmio` link style depends on and `uint64_t` does not provide.

Three consequences worth stating rather than discovering:

* **The width becomes target-dependent**, and that is correct: on a 32-bit
  target the address space *is* 32 bits. `addr_value()` returns `bit[64]`, so it
  widens — fine. `make_handle_from_handle(h, bit[64] offset)` adds a 64-bit
  offset to a pointer-width handle and truncates — also correct for that target,
  but it is the one place a model could lose bits, so the C lowering should not
  quietly widen the intermediate to `uint64_t` and pretend otherwise.
* **This does not weaken `ADDR`'s Tier 1 membership.** Tier 1 says every target
  must render the call, not that every target must render it identically. The
  disposition (add, identity) is portable; the representation is a target
  property behind the typedef. `ADDR` is a good illustration of why the tiers
  are about *capability*, not about *sameness*.
* **`uintptr_t` is technically optional** in C99 (§7.18.1.4 — required only
  where an integer type can round-trip `void *`). Universally present on
  anything pssc targets, but a freestanding build is entitled to omit it, so
  guard it: `#ifdef UINTPTR_MAX` … `#else typedef uint64_t pssc_addr_t;` with a
  comment saying the `mmio` seam is unavailable in that configuration.

### 3.7 Math: Tier 0, unsupported everywhere

**Decision: the floating-point functions are unsupported on every target. They
get added individually, as a model needs them.**

That is the fourteen Annex C entries — `log`, `log10`, `exp`, `sqrt`, `pow`,
`round`, `floor`, `ceil`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan` — plus
`to_float` and the `float_base_s` storage types (§21.5.1).

They are still **registered**, in Tier 0 with a stated reason, because that is
the whole point of having a Tier 0: `sqrt()` in a model should produce

> `sqrt` is a PSS core-library function (§21.5.2); no target lowers it

and not the generic "unclassified call". Registering a name as known-and-
unsupported is not the same as claiming support for it.

The shared reason is that they are `pure function float64` and pssc has no
`float64` at all: `sv_type()` raises on anything that is not
int/chandle/struct/enum/channel, and a grep for `float64` across `src/` returns
nothing. So the first math function anyone actually wants brings a type-mapper
change with it, which is the natural point to decide whether it lands in Tier 1
or as a per-target extension. Nothing here pre-commits that.

---

## 4. Context: solve vs target

The permitted set is **not** the same everywhere, and the difference is the
substance of the review instruction that only global utility functions are
supported at solve time.

| Context | What it is | Permitted dispositions |
|---|---|---|
| **solve** | the constructor (`ctor`/`init`/`initialize`), and any `solve function` lowered into it | `UTILITY`, `ADDR`, `FOLD`, `SUBCOMP_CTOR`, plus `set_handle` on a register group |
| **target** | operation bodies (`FuncKind.EXPORT_OP`), i.e. everything that can consume time | all of the above, plus `MEM`, `REG`, `CHANNEL`, `MODEL_OP`, `IMPORT` |

A Tier 2 entry carries its own contexts, declared by the target (§3.3) — the SV
backend offers `print` in both.

The context axis is **declared in the language**, not invented here, and the
registry should carry the LRM's qualification per entry rather than a blanket
rule:

| Entry | LRM declaration | Contexts |
|---|---|---|
| `format` | `solve pure function` (§21.1.2) | solve |
| `print` | `solve function` (§21.1.2) | solve |
| `message` | `function` (§21.1.3) | both |
| `error`, `fatal` | `function` (§21.3) | both |
| `urandom`, `urandom_range` | `function` (§21.4) | both |
| math (Annex C) | `pure function` | both |
| `channel_c::get`/`put`/`try_get`/`try_put` | `target function` (§21.9.1) | target |
| `read_struct`, `write_struct` | `target function` (`addr_reg_pkg`) | target |

Note `print` is **solve-only** per §21.1.2, and today's backends lower it in
target bodies without complaint. Since `print` is a Tier 2 capability (§3.3),
the contexts it is offered in are part of what a target declares: **the SV
backend supports it in both**, which is what keeps existing models compiling.
The LRM qualification is recorded per entry so a stricter target can enforce it;
it is not enforced globally.

One place this design is deliberately **stricter than the front end**:
`addr_reg_pkg.pss` declares `read8`…`write64` as plain `function` (the
`target function` spellings are commented out at lines 88-95), so the front end
permits a register access from a solve context. The lowering cannot, for the
reasons below. The diagnostic must therefore explain itself rather than just
citing the declaration — a user who checked the stdlib will believe the call is
legal, and it is: it is the *lowering* that cannot represent it.

Why solve is narrower, stated so the restriction is defensible rather than
arbitrary:

* There is **no bus at construction time.** A generated constructor runs before
  the import object is necessarily usable; `MEM` and `REG` accesses from a
  solve context would be transactions issued from a class constructor, which is
  not a thing a testbench can service on any of the three targets.
* There is **no scheduler at construction time.** `CHANNEL` `get`/`put` block.
* A `MODEL_OP` is a task on all three backends, and a constructor cannot call a
  task.

So the solve-context rule is not merely "we haven't implemented it" — three of
the five exclusions are semantic. The one that *is* a current limitation is
**user-declared solve helper functions**: a model that factors its address
arithmetic into `solve function int bank_offset(int i)` and calls it from
`initialize` is legal PSS and is rejected by this design. That is a real
restriction, it should be named as such in the diagnostic, and lifting it
(inlining such helpers into the constructor) is §8 item 1.

`get_offset_of_instance_array` is `FOLD`, so it is legal at solve time — which
is exactly right, since evaluating it produces a constant.

---

## 5. Where the check runs

**A pre-lowering validation pass, plus an emitter assertion.** Not one or the
other.

### 5.1 The pass

`validate_calls(root_dtype, ctx) -> None`, run from `progseq_gen.generate()`
before any file is opened. It walks every function body reachable from the root
component, classifies each `ExprCall`, and calls `ctx.add_error(...)` for each
unclassified one.

Three reasons it is a pass and not just an exception inside `expr()`:

1. **It reports every illegal call in one run.** An exception raised from
   expression rendering aborts at the first one. A model with six unmapped calls
   should take one compile to find out, not six.
2. **It runs before any output is written.** A failure part-way through emission
   leaves a truncated `.sv` on disk that a subsequent dv-flow run may treat as
   up-to-date — the same stale-artifact hazard `src/rdl/flow.yaml` documents for
   the RDL exports.
3. **It is testable without a backend.** The classifier can be unit-tested
   against IR directly.

### 5.2 The assertion

The pass and the emitter must not become two independently maintained lists —
that is the failure mode this design would otherwise introduce. So there is
**one registry** with two consumers:

* the pass asks *"is this classifiable?"* and produces the user-facing
  diagnostic;
* the emitter asks *"how do I render disposition D?"* and, on reaching an
  unclassified call, raises `InternalError` — because by then the pass has
  already vouched for it, so arriving there means the registry and the emitter
  have drifted. That is a compiler bug and should read as one, not as a user
  error.

### 5.3 Errors must actually stop the build

`driver.compile()` checks `ctx.errors` **before** calling `tgt.run(...)`
(`driver.py:137`), so errors accumulated during lowering are currently not
checked at all. `progseq_gen.generate()` must check `ctx.errors` after the pass
and raise `CompileError` with the collected list. Worth confirming this is not
also true of other post-translate passes.

---

## 6. Diagnostics

Reuse the form `reg_rmw.py` already established — `file:line:col: ` from the
node's `.loc`, via `_where(call)` (`reg_rmw.py:79-86`). Hoist `_where` into a
shared module; three copies of it is how the formats diverge.

**Correction (implementation): there are no source locations.** `loc` is a
declared field on every IR node and `ast2ir` populates none of them —
`ExprCall.loc`, `Function.loc` and `DataTypeComponent.loc` are all `None` on a
freshly translated model. So `reg_rmw`'s diagnostics have never carried a
location either; `_where` has always returned `''`.

The pass therefore reports the most specific site it can actually produce,
`component::function`:

```
wb_dma_ch_c::wait_completion: cannot lower call: 'get' is a PSS core-library
function (PSS 21.9.1) that 'c-progseq' cannot lower: there is no C runtime for
sync_pkg::channel_c; see targets/c/lower_progseq.py::_reject_channels
```

`_where` is kept ahead of the fallback, so real locations appear on their own
the day the front end fills them in. Populating `loc` is §10 item 9.

A diagnostic must say what was called, why it cannot be lowered, and what to do.
For the four expected classes of failure:

```
src/pss/wb_dma_c.pss:70:21: cannot lower call to 'helper' in a solve context.
    A constructor may call only global utility functions (message/print/
    error/fatal), address-space builtins, and register-group offset functions.
    A user-declared `solve function` has no generated equivalent and is not
    yet inlined -- fold it into the constructor body, or make it a
    compile-time constant.
```

```
src/pss/foo.pss:31:9: cannot lower call to 'read32' in a solve context.
    Memory primitives require the import object, which is not available while
    the model is being constructed. Move this access into an operation body.
```

```
src/pss/foo.pss:88:14: cannot lower call to 'flush' on a register handle.
    Known register methods are: read, write, read_val, write_val,
    write_val_masked, write_field, write_fields, write_masked, read_field.
```

```
src/pss/foo.pss:12:5: cannot lower call to 'sqrt'.
    The generated API has no definition for it and the target language has no
    analogue registered. If this is a PSS core-library utility, it needs an
    entry in targets/call_legality.py; if it is a foreign function, declare it
    as an `import` function so it is routed through the import API.
```

The last one is the important shape: the message tells the reader **which of
the two fixes applies** — extend the compiler, or change the model — because
from a bare "unsupported call" a user cannot tell.

---

## 7. The `FOLD` disposition, concretely

This is the part that fixes the reported bug, and it needs no new evaluation
machinery.

`get_offset_of_instance_array(name, idx)` on a register-group receiver:

1. Resolve the receiver's group datatype (`regs` → `wb_dma_regs_c`).
2. `base, stride = progseq_model._array_base_stride(group_dtype, name)` — the
   existing evaluator; `name` must be a string literal, which it is by
   construction (peakrdl-pss emits a `match` over string patterns).
3. Emit:
   * `idx` a compile-time constant → the folded literal `base + idx*stride`;
   * `idx` a loop index or other runtime value → the affine expression
     `(base + <idx> * stride)`.

For the WB DMA constructor that yields

```systemverilog
m_ch[i] = new(this, i, (base + (64'h20 + i * 64'h20)));
```

which is the same arithmetic `wb_dma_regs_c::new()` already performs for
`bank[i]` — the map stays stated once, in the RDL, which is the property the
model's own comment (`src/pss/wb_dma_c.pss:56-62`) says the call was chosen for.

`get_offset_of_instance(name)` is the scalar case:
`progseq_model._scalar_offset(group_dtype, name)`, always a literal.

The type-width detail from the Verilator run should be fixed at the same time:
the current emission also produces `WIDTHEXPAND` because the call result is
32-bit and the base is 64-bit. A folded affine expression should be emitted at
`addr_handle_t` width.

### 7.1 When the fold fails

**Decision: a fold that does not resolve is a compile error.** `-1` — whether
reached through the `default:` arm or through an unmatched name — is an **error
sentinel**, not a value the generated code should carry.

There were three candidate policies and it is worth recording why the other two
lose, because the second is superficially the conformant one:

| | Policy | Outcome |
|---|---|---|
| (a) | **Error** ← chosen | build fails, with file:line |
| (b) | Fold to `-1` | faithful to PSS: the `default:` arm really does return `-1`. But `-1` as `bit[64]` is `0xFFFF…F`; added to a base it wraps to a wild address. **Conformant and useless.** |
| (c) | Emit the call verbatim | today's behaviour — uncompilable output (§1) |

(b) is exactly the hazard the WB DMA model already carries a paragraph about
(`src/pss/wb_dma_c.pss:56-62`): *"it returns -1 for an unknown instance name, so
`bank` is load-bearing: rename that instance in the RDL and every channel binds
to a wild address rather than failing here."* Choosing (a) converts that
paragraph into a build error, which is a second win independent of the compile
fix.

### 7.2 The five failure modes

They must be told apart. Today F1, F2 and F3 all surface as the same
`ValueError("no array offset for X")`, which is a diagnostic bug regardless of
policy.

| # | Failure | Current behaviour | Diagnostic |
|---|---|---|---|
| F1 | receiver is not a register group | "no array offset" (nothing to scan) | *"`get_offset_of_instance_array` is a `reg_group_c` method; the receiver here is `<type>`"* |
| F2 | `name` is not a string literal | "no array offset" (`_pattern_str` never matches) | *"the instance name must be a string literal so the offset can be evaluated at build time"* — see §7.4 |
| F3 | no arm matches the name | "no array offset" | *"group `<G>` declares no instance array named `<n>`"*. The load-bearing case: a renamed RDL instance. |
| F4 | non-affine body | `ValueError: unsupported offset op/expr` | *"the offset expression is not affine in `index`"* |
| F5 | scalar variant, unknown name | bare `KeyError` from `offset_map[name]` | same shape as F3; wrap it |

### 7.3 A soundness bug in the existing evaluator

`_array_base_stride` **assumes** affinity rather than checking it: it evaluates
the arm at index 0 and index 1 and subtracts. `Mul` is a supported op, so

```pss
["bank"]: return 0x20 + index*index*0x20;
```

evaluates cleanly at both sample points — base `0x20`, stride `0x20`. At index 2
the true offset is `0xa0` and the fold says `0x60`. Silently wrong addresses,
no diagnostic, and it would now also reach the constructor path.

Not reachable from peakrdl-pss, which emits affine bodies only. It becomes
reachable the moment FOLD is exposed to model-authored calls over hand-written
register packages. Fix: **structurally match `c0 + index*c1`** rather than
sampling two points (a third sample point would also do, but matching says what
is actually required). Anything else is F4.

### 7.4 What F2 is *not* an argument for

F2 — a runtime instance name — is unfoldable but not wrong; it is legal PSS.
The answer is **not** to relax the error into a fallback. If a model ever needs
it, emit a real accessor method on the generated reg-group class: the `case`
statement the PSS body already describes, which is a feature to add on demand.

Adding that accessor pre-emptively, as a way of avoiding the §7.1 decision,
would be the worst of both: it puts a runtime lookup where a constant belongs
*and* it reinstates (b)'s silent `-1`.

---

## 8. Deliberately out of scope

1. **Inlining user solve helpers.** Named in the diagnostic (§6) so the
   restriction is discoverable. Lifting it means inlining a `solve function`
   into the constructor, which needs argument substitution — a real feature, not
   a gate change.
2. **A `--allow-unlowered-calls` escape hatch.** Recommended **against**. The
   whole value of this change is that the artifact is trustworthy; a flag that
   restores verbatim emission restores the failure mode, and it would be set
   once in a flow.yaml and never removed. `docs/silent-drop-review-2026-08-04.md`
   and `src/pss/flow.yaml`'s note on the absent C backend (*"a task that pretends
   to generate a C API is worse than an absent one"*) both point the same way.
3. **Receiver-type-directed dispatch.** Would fix the `get`/`read` ambiguity
   `_assign_from_call` documents. Separate work; this design keeps name matching
   and does not make the ambiguity worse.
4. **The `sv_pure` / `sw` targets.** Same audit is probably warranted
   (`lower_pure.py` is 840 lines and was not reviewed for this) but they have a
   different lowering model and should be assessed on their own.

   They should still **consume the Tier 0/Tier 1 registry** even before the
   validation pass reaches them — that is what retires the duplicate literals in
   `lower_pure.py:397` and `sw_lower.py:48`, and it is why the registry lives in
   `targets/`, not in `targets/sv/`. Sharing the registry is cheap and
   independent; sharing the pass is not.

---

## 9. Implementation phases

| Phase | Work | Exit criterion |
|---|---|---|
| **0** ✅ | Add the elaboration test that would have caught this: a Verilator `--lint-only` run over a top module that `new()`s the generated root class. Package-only linting never elaborates class bodies, which is why CI was green. | Test **fails** on today's generated output — verified by reverting the Phase 2 fix: 4 of the 12 new tests fail, including the elaboration one |
| **0b** | *(superseded by Phase 2's dedicated model — kept only if the shared examples are wanted as the coverage vehicle.)* Give `examples/export/programming_seqs/dma_engine.pss` a **real constructor** — a sub-component array bound with `make_handle_from_handle` over a folded `get_offset_of_instance_array`. This is what puts `init` lowering, `ADDR` and `FOLD` under all three behavioral gates; today none of them has any coverage there (§3.2) | All three gates still pass, and each now exercises a non-empty `init` |
| **1a** | Close the Tier 0 gaps in the front end: add `error` and `fatal` to `pssparser/src/stdlib/std_pkg.pss` (core per §21.3, currently absent) | A model calling `error()` resolves |
| **1** ✅ | `targets/call_legality.py`: the three tiers (§3), the four outcomes (§3.5), and the per-entry context qualification (§4). Tier 2 declared per target | 15 tests in `test_call_legality.py`, including the §3.4 contract test and the stdlib cross-check |
| **1b** ✅ | Register Tier 0 entries that are declared-but-unlowerable, with their reasons: the math surface (§3.6, unsupported everywhere), `format`/`format_string`, `urandom`/`urandom_range` on C/C++ | `sqrt()` in a model produces the §6 "core-library function, no lowering" diagnostic, not the generic one |
| **2** ✅ | `FOLD` for the two offset functions, via `_array_base_stride` / `_scalar_offset`; emit at `addr_handle_t` width. Includes the five distinct diagnostics (§7.2), the `KeyError` wrap (F5), and replacing the two-point sampling in `_array_base_stride` with a structural `c0 + index*c1` match (§7.3) | Phase 0's test passes; `test_op_model_sv.py` golden updated to the affine form; negative tests for F1–F5; a non-affine body is rejected rather than mis-folded |
| **3** ✅ | `validate_calls()` pass + the `CompileError` raise in `progseq_gen.generate()`; SV `expr()` gains its trailing `raise` (§1.2) | Negative test: `write_bytes()` fails the compile, names `gate_c::go`, and writes **no** output files. Measured over the corpus: 0 findings for `op-model-sv` on all four models; 8 for `c-progseq` on the WB DMA model, all channel operations — which is correct, and now names the call sites instead of only the component |
| **4** | Wire the same pass into the C and C++ progseq targets; replace all three verbatim fallthroughs with the registry dispatch + `InternalError`. Implement `ADDR` in C and C++ (two lambdas each), and move `pssc_addr_t` / `pssc::addr_t` to `uintptr_t`, dropping the eight now-redundant `(uintptr_t)` casts in `pssc_mem_mmio.h` (§3.6) | `test_build_wb_dma_c.py` / `_cpp.py` still pass, including Phase 0b's constructor; the three backends share one legality suite |
| **5** | Run the full suite and `examples/`; triage fallout — each hit is either a registry gap (add it) or a model that was generating broken code (fix the model) | Green, with any newly-rejected example either fixed or explicitly waived in this doc |

Phase 5 is where the cost lands and it is unpredictable: a gate applied to a
codebase that has never had one usually finds more than the one call that
prompted it. That is the point, but it should be budgeted rather than
discovered.

---

## 10. Open questions

1. **Does `ctx.errors` get checked after any other post-translate pass?**
   `driver.py:137` runs before `tgt.run`, so §5.3's fix may be needed in more
   than one place.
2. ~~**Math builtins**~~ — **closed, see §3.7.** There is a defined surface
   (§21.5.2 / Annex C). It is registered in Tier 0 and unsupported on every
   target; entries get added as a model needs them. No tier placement is
   pre-committed.
3. ~~**Should `FOLD` failures be errors?**~~ — **closed, see §7.1.** Yes. `-1`
   and the `default:` fallthrough are error sentinels, not values; a fold that
   does not resolve fails the build. This makes the renamed-RDL-instance hazard
   in `src/pss/wb_dma_c.pss:56-62` a build error instead of a wild address.
4. ~~**`yield` is currently lowered to a comment**~~ — **resolved, and it is
   dead code.** `yield` is not a function: `procedural_yield_stmt ::= yield ;`
   (Syntax 111) parses to `ProceduralStmtYield` and `ast2ir.py:2149` maps it to
   `ir.StmtYield`. progseq handles that statement correctly at
   `sv/lower_progseq.py:432` → `m_imp.yield_()`. The `"yield"` entry in the
   *call* tables can never match a call target, so the `$display`-style comment
   lowering in `sv_builtins.py` is unreachable — everywhere, not just from
   progseq. Drop the entry; do not carry it into the registry.
5. ~~**Enforcing `print`'s solve-only qualification**~~ — **closed.** `print` is
   a Tier 2, platform-specific capability (§3.3). The SV backend supports it, in
   both contexts. The §21.1.2 qualification is recorded per entry but not
   globally enforced, so no existing model breaks.
6. ~~**Does the C backend get `ADDR`?**~~ — **closed, see §3.6.** Yes: `ADDR`
   stays Tier 1, and its absence from C/C++ is a contract violation to fix in
   Phase 4, not a capability difference. C and C++ must additionally move
   `addr_handle_t` from `uint64_t` to `uintptr_t`.
8. **Should `ast2ir` retain stdlib function declarations?** Tier 0 is a Python
   manifest today only because it has to be (§3.1). Keeping the declarations
   would let the registry derive its "what exists" tier and retire the
   cross-check test.
9. **Should `ast2ir` populate `loc`?** The field exists on every IR node and is
   never filled, so no pssc diagnostic anywhere carries a source location — not
   this pass, and not `reg_rmw`'s (§6). This is the single largest quality win
   available to every diagnostic in the compiler.
10. **`yield` in `lower_stmts.py:23`.** The `pss_to_sv` path keeps its own copy
   of the builtin list, still containing `yield`. Same dead entry, removed from
   `sv_builtins` but left there because that target was out of scope (§8.4).
7. **Does the example model get a real constructor?** Phase 0b proposes one.
   It is the change that would have caught this bug without the WB DMA model,
   and it is the only way `ADDR`, `FOLD` and `init` lowering get behavioral-gate
   coverage on all three backends. Confirm the appetite for touching the shared
   example, since every progseq golden test keys off it.
