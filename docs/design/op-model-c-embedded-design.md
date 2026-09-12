# Operation-Model Export to Embedded C — Design

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

**Status:** for review · **Date:** 2026-08-13
**Companion to:** [`op-model-export-design.md`](op-model-export-design.md) (the
SystemVerilog projection, §4.5 of which defers this work)
**Subject:** lowering `src/pss` to a C programming API for a bare-metal target,
in the **global-link / no-context-import** style.

---

## 1. Headline

The SV projection is done and running (`rundir/fw-wb-dma.pss.op-model-sv/wb_dma_c_pkg.sv`,
511 lines, the full operation model). This document specifies the same
projection in C for firmware.

Four properties define the style, and they are the whole of the brief:

1. **Import functions are linked globally.** No vtable, no function-pointer
   table, no registration call. `write32` is a symbol; the platform
   defines it; the linker resolves it.
2. **Import functions take no context handle.** There is one bus, one CPU, one
   address space per link unit — that is what makes this the *embedded* style
   rather than the simulation one.
3. **There is no allocator.** The factory becomes an **initialization** function
   that accepts a pointer to caller-owned storage for the whole component tree.
   Nothing in the generated code calls `malloc`, and `<stdlib.h>` is not
   included.
4. **Memory access is a knob, and so is the include list.** Register access is
   either a **direct pointer** dereference or a **function** call (§5.2), and the
   generated files can pull in platform headers the model's environment requires
   (§5.3). Neither changes a single line of an operation body.

`pssc` already has a `c-progseq`/`op-model-c` target with a `direct` link style
that satisfies (1) and (2) *for the memory primitives only*. It does not satisfy
(3), does not expose sub-components, and has never been run against this model —
`src/pss/flow.yaml` says so explicitly at the bottom of the file. This design
extends what exists; it does not replace it.
MSB: There are no users of op-model-c, so update this style without regard for back-compat.

**One structural claim worth stating up front:** the non-blocking profile the C
target already publishes (`HAVE_BLOCKING=false`, `HAVE_RUNTIME_SOLVER=false` in
`targets/c_progseq_tgt.py`; renamed to `HAVE_EVENT_WAIT` by §5.4) is exactly the firmware API this model was factored
to produce. `src/pss/wb_dma_cfg_pkg.pss` and `docs/op-model-blocking-layering.md`
already did the hard design work: the C target gets `configure_channel`,
`set_auto_restart`, `set_software_pointer`, the three `*_start()` functions,
`probe_status`, `check_completion`, and the engine-global operations. It does
**not** get `notify_irq()` or the interrupt-driven wait, because nothing in the
image can suspend a thread waiting for one.

It **does** get `wait_completion()` and the three end-to-end wrappers — see
§5.4, which re-scopes the profile flag so that "cannot suspend on an event"
stops meaning "has no blocking operations at all". Firmware can therefore call
`transfer_single()` and get a spin, or drive `*_start()` + `check_completion()`
and own the loop itself. Both, rather than only the second, and the same source
compiles for UVM.

---

## 2. The shape, by example

What a firmware author writes:

```c
#include "wb_dma.h"

static wb_dma_t g_dma;                    /* .bss -- the caller owns it */

void dma_bringup(void) {
    wb_dma_init(&g_dma, 0x9d000000u);     /* the PSS `init` solve function */
}

int dma_copy(uint32_t src, uint32_t dst, uint16_t words) {
    wb_dma_ch_t     *ch = wb_dma_ch(&g_dma, 0);
    wb_dma_ch_cfg_t  cfg = {0};                     /* caller-initialised (§6.2) */
    wb_dma_status_t  st;

    cfg.src = src; cfg.dst = dst; cfg.tot_sz = words;
    wb_dma_ch_transfer_single_start(ch, &cfg);

    do { st = wb_dma_ch_check_completion(ch); } while (st == WB_DMA_PENDING);
    return st == WB_DMA_DONE ? 0 : -1;
}

/* ...or let the model own the loop -- same API as the UVM build (§5.4): */
int dma_copy_blocking(const wb_dma_ch_cfg_t *cfg) {
    return wb_dma_ch_transfer_single(wb_dma_ch(&g_dma, 0), cfg) == WB_DMA_DONE
           ? 0 : -1;
}
```

What the platform must supply, and nothing else — under `--mem-access
functions` (§5.2):

```c
void     write32(pssc_addr_t a, uint32_t d) { *(volatile uint32_t *)a = d; }
uint32_t read32 (pssc_addr_t a)             { return *(volatile uint32_t *)a; }
/* ... 8/16/64 as required by the model's access widths ... */
```

Those are the **PSS names, unadorned** (§5.1.1). There is no `yield` and no
scheduler hook: this profile has no suspension points, and a model that contains
one is rejected at generation time (§5.4). Under `--mem-access pointer` even
those two disappear — the generated accessor *is* the dereference, inlined — and
the platform owes **nothing at all**.

Everything else in this document is the specification that makes those two
listings true.

---

## 3. Generated artifacts

| File | Content | Notes |
|---|---|---|
| `wb_dma.h` | user includes (§5.3), value unions, enums, arg structs, the component-tree struct, import prototypes, export prototypes, inline register accessors | the only file firmware includes |
| `wb_dma.c` | impl-only includes, `wb_dma_init` + one global function per operation | one TU; see §9 on `--gc-sections` |
| `pssc_mem.h` | `pssc_addr_t`, and the seam selection (§5.2) | bundled core, **extended** |
| `pssc_mem_ptr.h` | direct-pointer seam | bundled core (was `pssc_mem_mmio.h`) |
| `pssc_mem_fn.h` | global-function seam | bundled core (was `pssc_mem_direct.h`) |
| `pssc_env.h` | `message` import declaration (§5.1.1) | bundled core, **new** |
| `pssc_chan.h` | depth-N token channel (§7) | bundled core, **new** |
| `wb_dma_stubs.c` | weak default for `message` only | opt-in, §5.5 |

Prefix `wb_dma` is the root component name with `_c` stripped
(`CProgSeqTarget.default_prefix`, already implemented).

---

## 4. The component tree

### 4.1 Structs, and who owns the storage

`wb_dma_c` owns `wb_dma_ch_c ch[4]`. The lowering is the obvious one — and the
obvious one is also the one that removes the allocator:

```c
typedef struct {
    pssc_addr_t     base;        /* this channel's bank */
    int32_t         chan;        /* PSS: wb_dma_ch_c.chan */
    wb_dma_ch_caps_t caps;       /* PSS: build-time capabilities */
    pssc_chan1_t    inflight;    /* PSS: channel_c<bit,1> */
    pssc_chan1_t    wake;        /* PSS: channel_c<bit,1> */
} wb_dma_ch_t;

typedef struct {
    pssc_addr_t     base;
    int32_t         num_ch;
    int32_t         pri_levels;
    wb_dma_ch_t     ch[4];       /* BY VALUE -- one object, one allocation */
} wb_dma_t;
```

**Decision — sub-components are inline members, not pointers.** The tree is
static in PSS (elaboration-time; `WB_DMA_MAX_CH` is a compile-time constant), so
it is static in C. One caller-provided object covers the whole tree; there is
nothing to allocate at any level, and `wb_dma_ch(&g_dma, i)` is address
arithmetic the compiler folds.

**Decision — the struct is a complete type in the header, not opaque.** An
opaque handle would force the caller to ask for a size at run time or to
hard-code one, and both defeat static allocation. The cost is that `sizeof`
becomes ABI: recompiling firmware against a regenerated header is mandatory.
That is the normal contract for a generated header and is stated in the banner.

**Decision — no parent back-pointer.** Verified against the model: no function
on `wb_dma_ch_c` refers to its parent; the only cross-component reference runs
downward (`wb_dma_c::notify_irq` touching `ch[i].wake`, blocking-only and absent
here). The generator must **reject** an upward reference rather than silently
emit a member that does not exist.

### 4.2 Naming

| PSS | C |
|---|---|
| component type `wb_dma_ch_c` | prefix `wb_dma_ch`, struct `wb_dma_ch_t` |
| `wb_dma_ch_c::transfer_single_start` | `wb_dma_ch_transfer_single_start(wb_dma_ch_t *s, ...)` |
| `wb_dma_c.ch[i]` | `wb_dma_ch_t *wb_dma_ch(wb_dma_t *s, unsigned i)` + `WB_DMA_CH_COUNT` |
| register `regs.csr` on `wb_dma_ch_c` | `wb_dma_ch_regs_csr_read/_write(...)` |

**Decision — a component's function prefix comes from its TYPE name, not from
its instance path.** `ch[3]` and `ch[0]` share one body; the instance is the
`self` pointer, which is the entire point of passing one. Two component types
that collide after `_c`-stripping are a generation error with a `--prefix-map`
escape hatch; nothing in this model collides.

### 4.3 Initialization

```c
void wb_dma_init(wb_dma_t *self, pssc_addr_t base);
```

This is the lowering of `wb_dma_c::initialize(addr_handle_t base)` — the same
mechanism §4.3 of the SV design specifies, executed rather than re-derived:
`regs.set_handle(base)` becomes `self->base = base`, and the `foreach` loop with
`get_offset_of_instance_array("bank", i)` becomes the folded
`self->ch[i].base = base + 0x20 + 0x20*i`. Component attribute defaults
(`num_ch`, `pri_levels`, `caps`) are assigned here, because C has no
initializer for a caller-provided object.

**Decision — `_create`/`_destroy` are not emitted in this style.** The existing
backend emits `<prefix>_create` (malloc), `<prefix>_init`, and
`<prefix>_destroy` (free) unconditionally. Under `--lifecycle static` only
`_init` survives, and `<stdlib.h>` is not included. A firmware image that
accidentally links `malloc` because a generated header pulled it in is a real
and annoying failure; the fix is not to emit it.

**Decision — `wb_dma_init` returns `void`.** Returning `self` would read as an
allocation and invite `wb_dma_t *d = wb_dma_init(...)`, which is precisely the
mental model this style exists to prevent.

**Open (§12.1):** whether `init` should be idempotent/re-callable. It is today
(pure assignment), but nothing states it.

---

## 5. The import surface

This is the part the brief is actually about, so it gets stated as a contract
rather than as a code shape.

### 5.1 The rule

> An import is a property of the **platform**, not of the device. It is
> therefore an unprefixed global symbol, shared by every generated model in the
> link unit, and it takes no context argument.

That is what makes one bus implementation serve a DMA model and a UART model in
the same image without either knowing about the other, and it is the same
device/environment split `src/pss/README.md` already draws (the handshake
participant is environment because its platform contract is the environment's to
satisfy).

| Symbol | Required when | Contract |
|---|---|---|
| `{read,write}{8,16,32,64}` | `--mem-access functions`, for each width the model uses (this model: 32 only) | a completed bus transaction on return |
| `message(int lvl, const char *msg)` | `--message-style import` and the model calls `message()` | may be a no-op |

The header declares all three groups. **A missing implementation is a link error
naming the symbol** — which is the main thing global linking buys over a vtable
whose slots can be `NULL` at run time on a board with no debugger attached.

### 5.1.1 The symbols are the PSS names

**Decision — an import's C symbol is the PSS function's simple name, verbatim.**
`write32` in the model is `write32` in the link map. Not `pssc_mem_write32`,
which is what the shipping `direct` seam uses today.

Two reasons, and the second is the one that generalizes:

- **The generated API is supposed to be a projection, not a translation.** A
  firmware author reading `docs/wb_dma_operation_model.md` or `src/pss` sees
  `write32`; the symbol they implement should be spelled the same. A rename in
  the middle of the seam is one more thing to know that carries no information.
- **The name comes from the IR, not from a table in the generator.** Today's
  backend hard-codes the eight `pssc_mem_*` primitives. Taking the name from
  the resolved import function instead (`func_kind(fn) == IMPORT_TASK`) means
  the rule covers *any* import the model declares — a model that imports a
  platform hook the DMA does not have gets a correct prototype for free, with
  no generator change. That matters directly for `tests/pss/wb_dma_hs_c.pss`,
  whose foreign-function contract is exactly this shape.

**The cost is the C global namespace, and it is real.** `read32`/`write32` are
common names; a HAL in the same image may already have them. The failure mode is
benign in both directions: identical signature and the definitions unify, a
different signature and the compiler says so at the point of declaration. There
is no silent-wrong-callee case, because C has no overloading. Two things reduce
the exposure now — `--mem-access pointer` emits **no import symbols at all**, and
`--include` (§5.3) can pull the platform's own header in ahead of the seam so a
mismatch surfaces immediately.

**Deferred, by design: `--import-prefix` and `--import-map`.** A prefix
(`board_write32`) and an explicit per-name map (`write32=hal_bus_write32`) are
the general answer to a collision, and both are cheap: the symbol appears in
exactly one place — the seam header — so neither flag can reach an operation
body, a register accessor, or the export API. Not implemented now, because the
bare-name default is the one that will be right most of the time and adding
decoration later is strictly additive. §13.9 records the trigger for building
them.

Every name in the table above is therefore a real PSS identifier. That is not a
coincidence — it is what is left once `yield` is removed (§5.4). PSS `yield` is
a procedural *statement*, not a call (`targets/sv/sv_builtins.py` documents this
at length: it reaches the IR as `StmtYield`, never as a function reference), so
it never had a name to project, and the fact that this target would have had to
*invent* one was the first hint that it does not belong in the import surface at
all.

### 5.2 Memory access: pointer or functions

Two modes, and the choice is *only* about how the seam primitive is spelled:

| `--mem-access` | `pssc_w32(bus, a, d)` expands to | Import symbols | Use |
|---|---|---|---|
| `pointer` | `*(volatile uint32_t *)(uintptr_t)(a) = (d)` | none | the device is directly CPU-addressable; one load/store instruction, no call |
| `functions` | `write32(a, d)` — the bare PSS name, §5.1.1 | 2 per width used | a bus that is not a pointer — a host simulation, a bridge, an RPC, a traced/backdoor path |

(The leading `bus` argument is the existing seam signature. Both modes discard
it — that is the "no context handle" property — and §5.6 explains why it stays
in the signature anyway.)

Neither mode changes an operation body or a register accessor: both call
`pssc_w32`/`pssc_r32`, and only the seam header differs. That invariant already
holds in the shipping backend (`lower_reg_model.py` emits the same accessor for
all three current styles) and this design preserves it deliberately, because it
is what makes the next decision possible.

**Decision — the mode is selectable at COMPILE time, not only at generation
time.** `pssc_mem.h` reads:

```c
#if defined(PSSC_MEM_ACCESS_FUNCTIONS)
#  include "pssc_mem_fn.h"
#else
#  include "pssc_mem_ptr.h"     /* default */
#endif
```

so **one generated bundle serves both builds**: the firmware image compiles as
is and gets pointer dereferences; the host-side behavioural gate (§11) compiles
the *same* `wb_dma.c` with `-DPSSC_MEM_ACCESS_FUNCTIONS` and binds the bus to a
mock. Testing the artifact you ship, rather than a sibling of it, is worth more
than the `#if`.

`--mem-access {pointer,functions,selectable}` therefore defaults to
`selectable`; the two pinned values emit a direct `#include` of one seam and
copy only that header, for projects that want no choice in the tree.

**Pointer mode has three preconditions**, all checked in the generated header
rather than assumed:

1. `pssc_addr_t` must survive the round trip to a pointer. The header carries
   `_Static_assert(sizeof(pssc_addr_t) <= sizeof(uintptr_t), ...)`, so the
   combination `--addr-bits 64` on a 32-bit target fails at compile time instead
   of truncating a register address silently. (§6.3 is the other half of this.)
2. Every access must be naturally aligned. True for this model — the RDL gives
   word-aligned registers and 32-bit access only.
3. There is no hook for logging, tracing, or a backdoor. That is the mode's
   defining cost and is why `functions` exists.

**Ordering and barriers.** `volatile` orders accesses to volatile objects
against each other but says nothing about a store buffer or a bus bridge. The
seam therefore calls a barrier macro that is a no-op unless the platform defines
it:

```c
#ifndef PSSC_MEM_BARRIER
#  define PSSC_MEM_BARRIER() ((void)0)
#endif
```

A platform that needs a `DSB` after an MMIO write defines `PSSC_MEM_BARRIER` —
in a header supplied through §5.3, which is the point at which these two
features stop being independent.

**Rejected — making `pointer` mode the only embedded style.** It is the mode an
MMIO device wants, but it makes the generated code untestable off-target: there
is no seam left to bind a mock to. The behavioural gate in §11 is the check that
catches the one silent defect in §8, so it is not optional, and it needs
`functions`.

### 5.3 User-specified includes

The generated code must be able to see platform declarations it does not
generate: a barrier or MMIO-attribute macro (§5.2), a toolchain's fixed-width typedefs
where `<stdint.h>` does not exist, a board header declaring the bus functions so
a signature mismatch surfaces at the seam (§5.1.1), or a project's own
`bool`/attribute conventions.

| Option | Emitted into | For |
|---|---|---|
| `--include H` (repeatable) | `wb_dma.h`, after the standard headers and **before** the seam and every generated declaration | anything the public API or the seam depends on |
| `--include-impl H` (repeatable) | `wb_dma.c` only, after `#include "wb_dma.h"` | implementation-only dependencies, kept out of every consumer's translation unit |
| `--omit-stdint` | suppresses `<stdint.h>`/`<stdbool.h>` | a toolchain whose fixed-width types come from a `--include` header instead |

Spelling rule, so both forms are reachable with no second flag: a value starting
with `<` is emitted verbatim (`--include '<machine/mmio.h>'`); anything else is
quoted (`--include board.h` → `#include "board.h"`). Order is preserved as
given — a list of includes is a sequence, and a header that must precede another
is a real thing.

**Placement is the load-bearing part.** Includes land *before* the seam include,
so a platform header can define `PSSC_MEM_BARRIER`, `PSSC_MEM_ACCESS_FUNCTIONS`,
or an MMIO attribute macro and have the seam honour it. Landing them after the
seam would make exactly the macros they exist to set arrive too late — silently,
because `#ifndef` defaults would already have won.

**Decision — includes are not filtered, validated, or deduplicated against the
generated content.** The generator does not know what is in them. It emits what
it was given, in order, and a bad header is a compile error in a file whose
first lines say where it came from.

### 5.4 `yield` is the wait primitive, and `HAS_BLOCKING` is re-scoped

**This subsection specifies a change to `src/pss` — to the operation-model
methodology — not to the generator.** The C backend gains no feature from it; it
simply projects what the model then contains.

**An earlier draft of this section made `yield` a generation error.** That was
wrong, and why it was wrong is the useful part: it conflated two independent
questions.

| Question | Answered by |
|---|---|
| Does this wait have an **event** to wait on — a channel with a producer? | the device, and whether an interrupt reaches the model |
| Must the waiter **relinquish** something while waiting — a thread, simulation time? | the execution target |

`HAVE_BLOCKING` answered both at once. So a target that merely lacked a
scheduler lost four operations for a reason that applies only to the first
question — and `pause_engine`, which has no event but does not need one, became
an anomaly that fitted neither profile.

#### The change

**Scope the flag to event waits only.** Rename it accordingly:
`HAVE_BLOCKING` → `HAVE_EVENT_WAIT`, `HAS_BLOCKING` → `HAS_EVENT_WAIT`.

| | flag true | flag false |
|---|---|---|
| `wait_completion`, `transfer_single`, `transfer_list`, `stop_channel`, `pause_engine` | exist | **exist — unchanged signatures** |
| how a wait actually waits | `wake.get()` — suspends until `notify_irq()` posts | `yield` — spins, re-reading the device each pass |
| `wake`, `notify_irq()` | exist | absent |
| `check_completion`, `probe_status`, the `*_start()` core | exist | exist |

And the gate appears in exactly **one** place — a wait primitive, rather than in
each wrapper:

```pss
// src/pss/wb_dma_ch_c/functions/wait_hint.pss
extend component wb_dma_ch_c {
    // Block until something may have changed. NOT the completion condition --
    // that is re-read from CHn_CSR by the caller on every pass.
    target function void wait_hint() {
        compile if (wb_dma_cfg_pkg::HAS_EVENT_WAIT) {
            bit tok;
            tok = wake.get();       // suspend; an interrupt will post
        } else {
            yield;                  // spin; the target decides what that costs
        }
    }
}
```

Every wrapper is then written **once, ungated**:

```pss
target function wb_dma_status_e wait_completion() {
    wb_dma_status_e status;
    bit tok; bool ok;
    while (true) {
        status = probe_status();
        if (status != WB_DMA_PENDING) { break; }
        wait_hint();                // <-- the only line the profile changes
    }
    ok = inflight.try_get(tok);
    return status;
}
```

`compile if` in a procedural body is legal: PSS 3.1 §19.2.1 lists "Procedural
Scopes (Execs and Functions)" among its scopes, with grammar
`procedural_compile_if` (Syntax 88). Verified against Draft 19 in this repo.

This shape also **side-steps pssparser defect 4** (a declaration inside a
`compile if` is invisible to later source files,
`docs/pssparser-defects-2026-08-02.md`), because `wait_hint` itself is declared
unconditionally and only its *body* is conditional. The alternative — two gated
declarations of each wrapper — would walk straight into that defect, and is the
reason `wake` is currently ungated.

#### What it buys

1. **One export API across both profiles.** A test written against
   `transfer_single()` compiles for UVM *and* for firmware. For a portable-
   stimulus model that is close to the entire point; the current split quietly
   makes the firmware profile a different API, so nothing written for one can be
   moved to the other.
2. **`pause_engine` stops being an anomaly.** It has no event, so it takes the
   `yield` path on **both** profiles. There is nothing to split and nothing to
   gate — it is now the *representative* case rather than the exception. §5.4.1.
3. **`yield` gets an honest meaning:** not a suspension point, a *politeness*
   point — "I am polling; let the world advance." A model containing one is
   saying "this wait has no event", which is true and worth saying.
4. **The blocking layer stops being about blocking.** What it is really about,
   and always was, is whether an interrupt can wake a waiter. That is now what
   the flag is called.

#### What survives of the rejection rule

Two things, both narrower and both real:

- **`channel_c::get`/`put` on a target with no scheduler is still an error**
  (§7) — and now that is the *only* thing the flag gates, so the check and the
  flag finally say the same thing.
- **A target may lower `yield` to nothing only if it is single-threaded.**
  Otherwise a polling wait is a hang — in SV a zero-delay `forever` loop stops
  the simulator outright. This is a property of the target, not a prohibition on
  the model.

The embedded C target is single-threaded by construction, so `yield` lowers to
nothing by default, with `--yield {none,import}` offering a `yield()` hook for a
watchdog kick, an iteration cap, or a `WFI`. The earlier objection to a no-op —
that it "silently converts a cooperative wait into an unbounded spin" — loses
its force here: nothing is silent, because `HAS_EVENT_WAIT=false` *is* the
statement that the wait is a spin, and it is recorded in the generated header
banner. A caller who wants to own the loop still has `check_completion()`.

#### Cost

The rename is a `docs/target-cfg-contract.md` change with a version bump. A
provider that declares `TARGET_CFG_VERSION` must declare every constant in that
version, so the rename cannot pass silently as a stale `HAVE_BLOCKING=false` —
which is the behaviour we want from it.

### 5.4.1 Consequence: `pause_engine` needs no change

The previous draft of this document proposed splitting `pause_engine` into a
write plus a `probe_pause()`, deleting its blocking form, and — as a
consequence — deleting the SV `yield_` import entirely. **All three are
withdrawn.** Under §5.4 `pause_engine` is well-formed exactly as written:

```pss
target function void pause_engine(bit pause) {
    wb_dma_gcsr_s gcsr;
    gcsr.pause = pause;
    regs.csr.write(gcsr);
    while (true) {
        gcsr = regs.csr.read();
        if (gcsr.pause == pause) { break; }
        yield;                              // legal on every profile
    }
}
```

It is ungated because it is genuinely profile-independent: it polls, and polling
works everywhere. `GCSR` has no interrupt behind it, no `INT_SRC` bit and no
entry in either routing mask, so there is no event and no `wake.get()` to gate —
which is why it does not need `wait_hint()` at all, only the bare `yield`.

Two smaller points stand on their own merits and are now optional rather than
required:

- **`probe_pause() -> bool`** is still worth adding, for symmetry with
  `probe_status()` on `wb_dma_ch_c`: it lets a caller own the readback loop the
  same way `check_completion()` lets them own the completion loop. An addition,
  not a replacement.
- **`op-model-export-design.md` §4.4** keeps its subject after all. The yield
  contract, the event-or-timeout requirement and the per-environment binding
  table stay valid, and `yield_()` stays in `wb_dma_c_import_if` — because
  `pause_engine` still yields. The earlier claim that the SV import interface
  would collapse to `pss_mem_if` is withdrawn.

#### What M0 does to the SV projection, exactly

`op-model-sv` publishes `HAVE_BLOCKING=true` (`src/pss/flow.yaml:47`), so the SV
build takes the `wake.get()` branch of `wait_hint()` and the `yield` in the
`else` branch is compiled out of it entirely. Counted against the generated
`wb_dma_c_pkg.sv` as it stands:

| Wait primitive | Today | After M0 |
|---|---|---|
| `wake.get()` | 2 — `wait_completion` (`:275`) and `stop_channel` (`:302`), which duplicate the same loop | 1, inside `wait_hint()` |
| `m_imp.yield_()` | 1 — `pause_engine` (`:456`) | 1 — unchanged |

So M0 *reduces* SV wait sites from 3 to 2 by folding the two duplicated loops
into one primitive, and leaves the yield count at one. More yield sites would
appear only in an SV build generated with `HAS_EVENT_WAIT=false`, which is not a
build this project makes.

### 5.5 Weak stubs, and where they stop

`--emit-stubs` (default **on**) writes `wb_dma_stubs.c` with an
`__attribute__((weak))` empty-bodied `message`, so a bring-up build links with
only the bus implemented. That is now the whole of it — `yield` used to be the
other entry and is gone (§5.4).

**No weak stub is ever emitted for a memory primitive**, in either access mode.
A `read32` that silently returns 0 turns every status poll into a
plausible-looking wrong answer with no diagnostic anywhere. The asymmetry is the
rule: *a weak default is allowed only where the no-op is semantically correct*,
and a dropped diagnostic message is, while a fabricated bus read is not.

### 5.6 One body emitter, every seam

The existing backend calls the seam as `pssc_w32(pssc_bus(s), addr, val)` and
lets `pssc_bus()` — the one line that varies — return the vtable pointer or
`NULL`. Operation bodies are then byte-identical across every seam, and
`message` follows the same shape.

So the leading context argument stays in the seam signature even though this
style never uses it. It costs nothing (it is discarded in an inline function
before the optimiser sees it) and it buys the property that **the mode is a
header choice, never a generator fork** — which is what lets §5.2 push the
choice all the way out to a `-D` on the compiler command line.

---

## 6. Type lowering

| PSS | C | Note |
|---|---|---|
| `bit[N]` | `uint{8,16,32,64}_t` | rounded up; already implemented |
| `int` | `int32_t` | |
| `bool` | `bool` (`<stdbool.h>`) | **new** |
| `enum` | `typedef enum { ... } <name>_t` | **new**, §6.1 |
| `addr_handle_t` (chandle) | `pssc_addr_t` | §6.3 |
| value struct `x_s` | `typedef union { uintN_t raw; struct { ... :N; }; } x_t` | already implemented |
| arg struct `wb_dma_ch_cfg_s` | `typedef struct { ... } wb_dma_ch_cfg_t` + `_DEFAULT` macro | §6.2 |

### 6.1 Enums

`src/pss/flow.yaml` records the exact blocker: *"the first enum-typed operation
argument in this model raises `unsupported C type for DataTypeEnum`"*. The fix
is the C analogue of `targets/sv/lower_api_types.py`:

```c
typedef enum { WB_DMA_DONE = 0, WB_DMA_ERROR = 1, WB_DMA_PENDING = 2 } wb_dma_status_t;
```

Every enumerator gets an **explicit value**, from the PSS declaration. This
model depends on it — `wb_dma_types_pkg.pss` documents that `PENDING` was
appended specifically to leave `DONE == 0` and `ERROR == 1` where they were,
"because these values cross into generated SV", and now into generated C.

Enumerator names are emitted verbatim. They are already device-prefixed in this
model, and a collision in C is a compile error at the point of declaration —
loud, which is the acceptance criterion.

`match (bank) { [WB_DMA_INT_A]: ... }` lowers to `switch` with a `default:` that
is either unreachable-marked or a `message()`, per `--match-default`.

### 6.2 Struct arguments

**Decision — aggregates are passed by `const T *`, not by value**
(`--struct-args pointer`, the default for this style).

`wb_dma_ch_cfg_t` is 18 scalar fields (corrected 2026-08-13; the text said 15),
~48 bytes. By value that is a `memcpy`
into the callee frame on every `configure_channel` and every
`transfer_single_start`, on a part that may have 8 KB of RAM. By pointer it is
one register. The body emitter rewrites member access from `cfg.src` to
`cfg->src`; that is the whole cost.

`--struct-args value` remains available for host-side unit tests where matching
the PSS signature exactly is worth more than the copy.

**Struck 2026-08-13 — no constraint is projected.** This section previously
specified a `WB_DMA_CH_CFG_DEFAULT` designated-initializer macro derived from the
`constraint default` block in `wb_dma_ch_cfg_s`, on the grounds that the values
are real device knowledge (`src_mask == 0xfffffffc`, `int_on_done == 1`, …) even
without a solver.

That crosses a boundary this target holds: **constraints and actions are excluded
from an operation model, silently and by construction** (§10). `op-model-sv` does
not emit constraints either. Projecting *some* constraints — equality ones — would
have made the exclusion conditional and the C and SV surfaces disagree, for a
convenience firmware can get from a hand-written header in its own tree.

A caller therefore initialises `wb_dma_ch_cfg_t` itself. If the model's defaults
are wanted in C, the right mechanism is an explicit one (a solve function the
model states, projected like any other), not a backend inferring intent from a
constraint block.

### 6.3 Address width

`pssc_addr_t` is `uint64_t` today. On a 32-bit MCU every address computation,
every register accessor argument, and every `addr_handle_t` member of the tree
then costs a register pair for range that does not exist.

**Add `--addr-bits {32,64}` (default 64).** It changes one typedef in
`pssc_mem.h` and the platform's `read*`/`write*` prototypes, so it is a
generation-time property of the whole bundle and is recorded in the header
banner. For this project's firmware profile the intent is `--addr-bits 32`.

This is the one knob that genuinely interacts with §5.2: under `--mem-access
pointer` the address is cast to a pointer, so an address wider than `uintptr_t`
would truncate. The static assert in §5.2 makes that a compile error rather than
a wild store. Under `functions` there is no such constraint — a 32-bit host may
legitimately drive a 64-bit simulated address space.

---

## 7. Channels

`wb_dma_ch_c` declares two `channel_c<bit,1>` members, and — importantly —
**`inflight` is on the non-blocking profile too**, deliberately
(`src/pss/wb_dma_ch_c.pss` explains why at length: it is the detector for
polling a channel nobody armed, or polling past a completion already consumed).
`check_completion`, `transfer_single_start` and `transfer_list_start` all use
it. So the C backend cannot skip channels.

Today it does the opposite: `c/lower_progseq.py::_reject_channels` raises on any
component holding one. That is the right *policy* (a backend that pretends is
worse than one that admits) but it now blocks the deliverable.

**Design.** A new bundled `pssc_chan.h` provides a token channel with the
non-blocking half of the API only:

```c
typedef struct { uint32_t val; uint8_t full; } pssc_chan1_t;

static inline bool pssc_chan1_try_put(pssc_chan1_t *c, uint32_t v);
static inline bool pssc_chan1_try_get(pssc_chan1_t *c, uint32_t *v);
```

Depth-1 is the only instantiation this model needs, and depth 1 is a design
decision in the model rather than a buffer size (a coalescing binary semaphore —
`try_put` failing while a token is pending is the behaviour `wake` relies on).
A `PSSC_CHAN_DECL(name, T, DEPTH)` macro covers deeper channels when one appears;
until then the generator emits `pssc_chan1_t` and rejects `DEPTH > 1`.

**Blocking `get()`/`put()` are not implemented and are rejected at generation
time**, with a diagnostic naming the function. Under §5.4 this is now the *only*
thing `HAS_EVENT_WAIT` gates, so on this profile they are unreachable by
construction: `wait_completion()` exists but takes the `yield` branch of
`wait_hint()`, and `notify_irq()` — the only producer — is absent. The check is
the backstop for an ungated blocking channel op reaching the C target.

Cost: 8 bytes per channel as written above, 16 bytes per DMA channel, 64 bytes
of `.bss` for the tree. A `bit`-payload depth-1 channel could pack to 1 byte;
`--chan-packing` is a possible follow-on, not part of this work.

**ISR safety is out of scope, and that is a statement not an omission.**
`try_put` is a read-modify-write on a struct. On this profile nothing in an
interrupt context touches a channel: `notify_irq` — the only ISR-facing
operation in the model — is gated on `HAS_EVENT_WAIT` and is absent. If a future
profile posts to `wake` from an ISR, the channel needs
`__atomic_exchange`/critical-section treatment, and that must be designed rather
than assumed.

---

## 8. Emitter gaps to close

The C body emitter (`targets/c/lower_progseq.py`) handles
`StmtAnnAssign/Assign/Expr/Return/If/RepeatWhile/While` and
`ExprConstant/RefLocal/Attribute/Subscript/Bin/Cast/Call`. Measured against what
the model's non-blocking functions actually contain:

| Gap | Used by | Severity |
|---|---|---|
| **`self`-rooted member access emits a bare name** — `caps.ars` becomes `caps.ars`, not `s->caps.ars` | `configure_channel`, `set_auto_restart`, `set_software_pointer` | **P0 — silent.** Compiles wherever a matching name is in scope; wrong otherwise. Same failure class as the `write_val` trap already documented in `_reg_call` |
| `ExprUnary` (`!`) | `check_completion`, both `*_start` guards, `set_*` | P0, loud |
| `StmtBreak` | `pause_engine` | P0, loud |
| `StmtYield` | `pause_engine`, and `wait_hint()` on this profile (§5.4) | P0 — **lower**, not reject: emits nothing under `--yield none` (the default, sound because this target is single-threaded), or a `yield()` import call under `--yield import` |
| `StmtForeach` | `wb_dma_c::initialize` | P0 |
| `StmtMatch` | `configure_interrupt_routing` | P0 |
| `DataTypeEnum` in `c_type` | 6 operations | P0 — §6.1 |
| built-in calls (`message`, `addr_value`, `make_handle_from_handle`, bare `read32`/`write32`) | `write_descriptor`, `read_descriptor_residual`, `transfer_list_start`, both `message()` sites | P0 — mirror `targets/sv/sv_builtins.py`; the shared `call_legality.py` registry already says which names a target claims |
| `write_field("name", v)` string-resolved field | `set_auto_restart`, `stop_channel_start`, both `*_start` | P0 — `reg_field_resolve.py` is shared with SV; folds to `(mask, shift)` |
| sub-component walk in `lower_decls`/`lower_impl` | everything on `wb_dma_ch_c` | P0 — §4 |

All but the first fail loudly today. The first is the one to fix first.

**Carried-forward hazard, not introduced here:** `write_field` lowers to a
read-modify-write (`pssc_reg_pkg.sv::write_val_masked` and its C twin), and a
read of `CHn_CSR` clears `ERR` and the three interrupt-source bits. So
`regs.csr.write_field("ch_en", 1)` has a read side effect on the very register
firmware polls. This is already true of the shipping SV projection and of the
device; it is called out here because a firmware author reading the generated C
will meet it. A `write_field_no_rmw` (write-1-to-set semantics) is a separate
change to the register layer, in both projections at once.

---

## 9. Footprint

**MEASURED** as of C5.4, `-Os`, `--lifecycle static --link-style vtable`, the
whole 17-operation surface:

- **RAM:** `sizeof(wb_dma_t)` = **248 bytes** of `.bss`, all in one caller-owned
  object. No heap. `wb_dma_ch_t` = 56 = bus 8 + base 8 + chan 4 + caps 4 +
  inflight 16 + wake 16 — the two channels are more than half of a channel, so
  `--chan-packing` (§7) is where the RAM is.
- **Flash:** `.text` = **1358 bytes** for all 17 operations.

The estimate below was ~124 bytes; the 124-byte gap is three recorded decisions —
a 64-bit `pssc_addr_t` (+20; `--addr-bits` does not exist yet), a `bus` pointer
per channel (+40; the §4.1 deviation), and a `uint64_t` channel payload rather
than the `uint32_t` sketched in §7 (+64). See the plan's C5.4 log for the table.

The original estimate, kept because its *shape* is still the right way to think
about the cost — for `--addr-bits 32 --struct-args pointer`:

- **RAM:** root 12 bytes + 4 × (4 base + 4 chan + 4 caps + 16 channels) = ~124
  bytes of `.bss`, all in one caller-owned object. No heap.
- **Flash:** every operation is a separate global function and every register
  accessor is `static inline` with a constant-folded offset. With
  `-ffunction-sections -Wl,--gc-sections` an image that calls only
  `configure_channel` + `transfer_single_start` + `check_completion` links only
  those. This is a direct consequence of the global-linking style: there is no
  vtable forcing every operation to be reachable. **VERIFIED** by C5.5
  (`test_unreferenced_operations_are_dropped_by_gc_sections`): a two-call image
  drops `transfer_list`, `transfer_single`, `wait_completion` and
  `read_descriptor_residual`.
- **Stack:** the deepest chain is `transfer_single_start → configure_channel →
  accessor → write32`, with one `wb_dma_csr_t` (4 bytes) and one
  `wb_dma_sz_t` (4 bytes) live.

---

## 10. Not projected

Stated so review can disagree explicitly rather than by omission:

| Excluded | Why |
|---|---|
| actions (all 6) | scenario-generation constructs; no meaning without a solver. `op-model-export-design.md` §4.7 |
| `notify_irq` | the interrupt producer; gated on `HAVE_EVENT_WAIT`, deleted at the front end rather than in the backend |
| constraints (all, including `constraint default`) | scenario/solver constructs, excluded silently and by construction. `op-model-sv` emits none either — §6.2 |
| `create`/`destroy`/`malloc` | §4.3 |
| `printf`, `<stdio.h>` | `message()` goes to an import or to nothing (§5.1) |
| the event-driven wait (`wake.get()`) | gated; the polling wait replaces it and the wrappers survive (§5.4) |
| two *buses* in one link unit | a second DMA on the same bus is just a second `wb_dma_t`; two different buses need import-side context, i.e. `--link-style vtable` |

That last row is the one real limitation of the style, and it is inherent rather
than incidental: two `wb_dma_t` objects on the same bus work in both access
modes, because the context that varies is on the export side. Two different
buses need a context on the import side, which is exactly what the brief
excludes. (`--mem-access functions` does not rescue this: the function is a
global symbol, so the second bus would have to be demultiplexed from the
address, which the platform can do but the model cannot know about.)

---

## 11. Build wiring

Replaces the "the C API is NOT wired up yet" note at the bottom of
`src/pss/flow.yaml`:

```yaml
  - name: op-model-c
    uses: pssc.OpModelC
    needs: [reg-model-pss, src]
    with:
      root:        wb_dma_c
      prefix:      wb_dma
      link_style:  direct        # global symbols, no context handle
      lifecycle:   static        # NEW: init-into-caller-storage, no malloc
      mem_access:  selectable    # NEW: -DPSSC_MEM_ACCESS_FUNCTIONS picks the
                                 #      function seam; default is pointer
      includes:    []            # NEW: -> #include in wb_dma.h, in order
      includes_impl: []          # NEW: -> #include in wb_dma.c only
      addr_bits:   32            # NEW
      struct_args: pointer       # NEW
      reg_style:   bitfields
      core_copy:   true
      # target_cfg is NOT set: op-model-c already publishes
      # HAVE_EVENT_WAIT=false / HAVE_RUNTIME_SOLVER=false, and that is the
      # answer this target wants. Setting it here would restate it.
```

Two gates, both cheap and both necessary — a generated header that is never
compiled is a generated header that does not compile:

1. **`op-model-c-compile`** — `gcc -std=c99 -Wall -Werror -Wextra -ffreestanding
   -c` over `wb_dma.c` plus a translation unit that includes `wb_dma.h` twice
   (include-guard check). Runs **twice**, once per access mode (`-D` or not), so
   the pointer seam's static assert and the function seam's prototypes are both
   exercised. Catches the whole P0 list in §8 except the silent one.
2. **`op-model-c-behave`** — compile the *same* `wb_dma.c` with
   `-DPSSC_MEM_ACCESS_FUNCTIONS`, link against the existing C mock bus
   (`packages/pssc/tests/progseq/data/c/dma_mock.h`), and run the firmware
   sequence from §2 against it, asserting the register write trace. This is the
   gate that catches `caps.ars` resolving to the wrong object, which no compiler
   will — and §5.2's compile-time seam selection is what lets it run against the
   shipped artifact rather than a re-generated sibling of it.

`pssc` promotes `src/pss` to its progseq reference model
(`op-model-export-design.md` §3G), so both gates should land in the pssc
regression as well, not only here.

---

## 12. Phasing

| Phase | Content | Gate |
|---|---|---|
| **M0** | §5.4 **methodology change, in `src/pss` and the target-cfg contract — not in the generator**: re-scope `HAVE_BLOCKING` to `HAVE_EVENT_WAIT`; add `wait_hint()`; un-gate the four blocking wrappers; leave `pause_engine` alone | `check-nonblocking` now compiles the wrappers too — that is the whole point of it; the SV projection regenerates unchanged apart from the extra `wait_hint()` frame; every existing UVM test still passes |
| **C0** | §8 emitter gaps: `self` member access, unary, `break`, `foreach`, `match`, enums, built-ins, string `write_field` | pssc unit tests per construct |
| **C1** | §4 sub-component tree: structs, accessors, per-type prefixes, `init` lowering with folded bank offsets | §11 gate 1 |
| **C2** | §4.3 `--lifecycle static`; drop `create`/`destroy`/`<stdlib.h>` | §11 gate 1 |
| **C3** | §7 `pssc_chan.h`, `try_put`/`try_get`; replace `_reject_channels` with a blocking-op-only rejection | §11 gate 2 |
| **C4** | §5.1 import surface: names taken from the IR (§5.1.1), `pssc_env.h`, `message`, `--emit-stubs`; **and the §5.4 `yield` rejection** | a probe model with a `yield` must fail with file/line |
| **C4b** | §5.2 access modes: split the seam into `pssc_mem_ptr.h`/`pssc_mem_fn.h`, the `#if` selector, `PSSC_MEM_BARRIER`, the `uintptr_t` static assert | §11 gate 1, both modes |
| **C4c** | §5.3 `--include` / `--include-impl` / `--omit-stdint` | §11 gate 1 with a probe header that defines `PSSC_MEM_BARRIER` |
| **C5** | §6 embedded knobs: `--addr-bits`, `--struct-args`, `--message-style`, `_DEFAULT` macros | §11 both |
| **C6** | §11 flow wiring + promote both gates into pssc's regression | CI |

M0 is a model/methodology change and touches no generator code; it is worth
landing early because it *widens* what the C target must handle (the wrappers,
and `yield` on the polling path) and every later phase should be measured
against the widened model. C0 is the critical path and is the largest item;
C1–C5 are independent of each other once it lands.

Note that M0 makes the `check-nonblocking` task in `src/pss/flow.yaml` more
valuable rather than less: it stops being "does the core still link without the
blocking layer" and becomes "does the *whole* API still link when the event wait
is unavailable" — which is the property the C target actually depends on.

---

## 13. Open questions for review

1. **§4.3** — should `wb_dma_init` be documented as re-callable (re-init after a
   soft reset)? It is, mechanically. Making it a contract constrains future
   lowerings of `init` bodies that have side effects.
2. **§6.1** — `wb_dma_status_e` → `wb_dma_status_t` follows the existing `_s` →
   `_t` convention but erases the enum/struct distinction that the PSS names
   carry. Keep `_e`?
3. **§6.3** — is `--addr-bits 32` a per-generation flag, or should it follow the
   PSS `addr_handle_t` width if the front end ever carries one? A mismatch
   between the generated header and the platform's `read*`/`write*` prototypes is a
   compile error, so this is safe either way, but only one of them is the source
   of truth.
4. **§7** — 8 bytes for a 1-bit depth-1 channel. Worth `--chan-packing` now, or
   defer until a part with real RAM pressure appears?
5. **§10** — is single-bus-per-link-unit acceptable indefinitely for this style,
   or should `direct` and `vtable` be co-generatable from one run so a project
   can build firmware and a host-side model from the same source without two
   generator invocations?
6. **§5.2** — is `selectable` the right default, or should the shipped firmware
   bundle pin `pointer` and the test bundle be a second generator invocation?
   Pinning is tidier in the tree; selecting is the only way the behavioural gate
   tests the artifact that ships.
7. **§5.3** — should `--include` also be honoured by the *bundled core* headers
   (so a platform header could reach `pssc_chan.h` too), or only by the
   generated files? Today the core headers are copied verbatim, which is what
   makes them cacheable and diffable.
8. **§8, hazard** — is the `write_field` read-modify-write on a
   read-side-effecting `CHn_CSR` worth fixing in the register layer now? It
   affects the shipping SV projection identically, so fixing it is a
   two-projection change with its own regression.
9. **§5.1.1, the trigger for `--import-prefix` / `--import-map`** — bare names
   are the default and decoration is additive, so the question is only *when*.
   Proposed trigger: the first time a target platform's HAL declares `read32` or
   `write32` with an incompatible signature, or the first image that must drive
   two devices whose PSS models declare same-named imports with different
   contracts. Neither has happened. Worth noting the flags cost about a day
   each, and the seam is the only file they touch.
10. **§5.4, the rename** — `HAVE_BLOCKING` → `HAVE_EVENT_WAIT` is the honest
    name once the flag stops governing whether blocking operations exist, but it
    is a `target-cfg-contract.md` version bump that every provider must follow.
    Rename now, or keep the name and re-document its meaning? I lean to renaming:
    a flag whose `false` value no longer means what it says is the kind of thing
    that gets mis-set once and then believed.
11. **§5.4, `wait_hint()` naming and placement** — one per component that has a
    channel (`wb_dma_ch_c`), with `pause_engine` using a bare `yield` because
    `wb_dma_c` has no channel? Or a single engine-level primitive that channels
    override? The former is what §5.4 assumes and is simpler; the latter would
    let a future `GCSR` interrupt be added in one place.
12. **§5.4, verify before committing** — two front-end behaviours this rests on:
    (a) pssparser accepts `compile if` inside a function body (legal per §19.2.1
    and Syntax 88, but the model has never used it there); (b) the `else` branch
    reaching `yield` inside a `target function` still lowers correctly in the SV
    backend, which today only ever sees `yield` at the top level of
    `pause_engine`. Both are one-file probes.
13. **§5.4, does `inflight` want the same treatment?** It is ungated and uses
    only `try_get`/`try_put`, so the re-scoped flag leaves it untouched — which
    is correct. Worth confirming the re-scoping does not make its long
    justification comment in `wb_dma_ch_c.pss` stale, since that comment argues
    from "present on both profiles" and the set of things present on both
    profiles has just grown.
