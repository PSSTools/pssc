# Embedded C operation models

**Audience:** firmware engineers consuming a generated C driver, and the
platform engineers who supply the layer beneath it.

`pssc compile -t op-model-c` turns a PSS component tree into a C programming API:
register accessors, an operation per model function, and a struct per component.
The output targets a part with no heap, no scheduler, and no C library beyond
freestanding C99.

For the flag reference see [`progseq.rst`](progseq.rst); this document is the
guide.

---

## 1. What you get

```sh
pssc compile -t op-model-c --root wb_dma_c --prefix wb_dma \
    --link-style vtable --lifecycle static \
    model/*.pss -o gen/
```

writes `gen/wb_dma.h`, `gen/wb_dma.c` (unless `--header-only`), and the core seam
headers. Then:

```c
#include "wb_dma.h"

static wb_dma_t g_dma;                       /* no malloc: --lifecycle static */

void dma_bringup(void) {
    wb_dma_init(&g_dma, &my_bus, 0x9d000000u);
}

int dma_copy(uint32_t src, uint32_t dst, uint16_t words) {
    wb_dma_ch_t     *ch = wb_dma_ch(&g_dma, 0);
    wb_dma_ch_cfg_t  cfg = {0};              /* you initialise this -- see §6 */
    cfg.src = src; cfg.dst = dst; cfg.tot_sz = words;
    cfg.int_on_done = 1;

    return wb_dma_ch_transfer_single(ch, cfg) == WB_DMA_DONE ? 0 : -1;
}
```

Every operation the model declares is present. There is no reduced "firmware
subset": the end-to-end operations exist here exactly as they do in the
SystemVerilog projection, and differ only in how they wait (§4).

---

## 2. What the platform must implement

Exactly two things, and one of them is optional.

### 2.1 Memory access — required

How the driver reaches the device. Three mechanisms; pick with `--link-style`
and `--mem-access`.

**`--link-style vtable`** — you fill a struct of function pointers. Use this when
more than one instance must reach different buses, or when the driver runs on a
host against a model.

```c
static uint32_t rd32(void *ctx, pssc_addr_t a) { ... }
static void     wr32(void *ctx, pssc_addr_t a, uint32_t d) { ... }

static const pssc_mem_if my_bus = {
    .read32 = rd32, .write32 = wr32, /* ...and the 8/16/64 pairs */
    .ctx = &my_state,
};
```

**`--mem-access pointer`** — the driver forms a pointer from the address and
dereferences it. Nothing to implement. Requires that a device address *fits* a
pointer, which the generated header asserts statically.

**`--mem-access functions`** — you supply `extern` symbols by name:

```c
uint32_t pssc_mem_read32 (pssc_addr_t a);
void     pssc_mem_write32(pssc_addr_t a, uint32_t d);
/* ...and the 8/16/64 pairs */
```

Forms no pointer, so it works where `pointer` cannot — a 32-bit core addressing a
64-bit device map, or a bus reached through an accessor rather than the address
space.

**`--mem-access selectable`** emits neither and lets the C compile line choose:
add `-DPSSC_MEM_ACCESS_FUNCTIONS` for the function seam, omit it for the pointer
seam. One generated driver, two mechanisms, chosen per build.

### 2.2 Messages — optional

The model can report a caller error (`check_completion()` with nothing in
flight, for instance). Those become:

```c
void pssc_message(const char *fmt, ...);
```

Three options: implement it; `--emit-stubs` to get a **weak** empty definition so
a link succeeds before you have; or `--message-style none`, which removes the
calls **and their format strings** — worth doing when `.rodata` is tight, at the
cost of silence on caller errors.

**Nothing else is ever stubbed.** A memory primitive or a model-declared import
with a weak empty body would link and then silently not touch the device, which
is worse than a link error.

---

## 3. What is not generated

- **`malloc`.** Under `--lifecycle static` no allocator is referenced at all;
  verify with `nm -u`. Grep proves the generator didn't *write* `malloc`; `nm`
  proves the compiler didn't *need* one.
- **A scheduler**, and therefore no blocking wait — see §4.
- **Actions and constraints.** They are scenario-generation constructs with no
  meaning without a solver, and are excluded silently and by construction. This
  includes `constraint default`: see §6.
- **`printf` / `<stdio.h>`.**

---

## 4. Waiting, and what a poll costs you

An end-to-end operation is `*_start()` followed by
`repeat { probe_status; wait_hint }`. On this target `wait_hint()` is a spin, so
the loop is a tight poll: one bus read per iteration.

`--yield import` makes each iteration call `yield_()`, which you supply. Use it
to charge for the spin — `WFI`, a watchdog kick, a delay:

```c
void yield_(void) { __asm__ volatile("wfi"); }
```

> **Do not implement `yield_()` as a wait on this device's interrupt.** The model
> spins precisely where it is asserting that *no interrupt exists for that
> condition*. One caller — the engine pause — has nothing to wait for, and
> would deadlock.

If you would rather not poll at all, drive the **core** API yourself:
`transfer_single_start()`, then `check_completion()` from your own event loop or
ISR. That layer never waits. Note its contract: once `check_completion()` returns
anything but `PENDING`, do not call it again until the next `*_start()` — the
call *consumes* the completion it reports, because reading the status register
clears it.

---

## 5. Concurrency

**The generated API is not ISR-safe.** Component channel members are
read-modify-written without a critical section, so no generated function may run
in an interrupt handler concurrently with foreground code. The header says so in
its banner.

To lift that, define `PSSC_CHAN_ENTER`/`PSSC_CHAN_EXIT` and pass them with
`--include`:

```c
/* my_critical.h */
#define PSSC_CHAN_ENTER()  uint32_t _s = disable_irq()
#define PSSC_CHAN_EXIT()   restore_irq(_s)
```

```sh
pssc compile -t op-model-c ... --include my_critical.h
```

**Placement is the whole specification.** These are `#ifndef` guards *inside* the
seam headers, so a definition arriving after them is silently ignored.
`--include` places yours *before* the seam includes, which is the only reason it
works. The same applies to `PSSC_MEM_BARRIER` and `PSSC_UNREACHABLE`.

---

## 6. Configuration structs have no defaults

A model may state `constraint default src_mask == 0xfffffffc`. **That does not
reach the C API**, because constraints are excluded (§3). `wb_dma_ch_cfg_t cfg =
{0};` gives you zeros, not the model's defaults.

This is deliberate rather than an omission: projecting *some* constraints would
make the exclusion conditional and put the C and SystemVerilog surfaces out of
step. Read the model — or the operation-model specification it is generated
from — and initialise the fields your device needs. If you want the defaults in
code, put them in a header in your own tree, where they are reviewable.

---

## 7. Footprint

Measured on the WISHBONE DMA model (20 functions, 4 channels), `-Os`,
`--lifecycle static`:

| | |
|---|---|
| `.text` | ~1.4 KB |
| `sizeof(wb_dma_t)` | 248 bytes (64-bit addresses) |
| undefined symbols | `pssc_message`, `memset` |

`memset` is required of a freestanding implementation (C99 §4), so it is not an
extra dependency.

Two knobs matter:

- **`--addr-bits 32`** narrows `pssc_addr_t`. On a 32-bit target this is −20
  bytes of RAM and about −15% of `.text`. Measuring it on an x86-64 host shows
  *nothing* — alignment padding absorbs the saving — so measure on the part.
- **`-ffunction-sections -Wl,--gc-sections`** drops operations you never call.
  Verified to work: the linker removes them cleanly.

---

## 8. Recommended build

```sh
pssc compile -t op-model-c --root <root> --prefix <p> \
    --lifecycle static --mem-access selectable --addr-bits 32 \
    --emit-stubs --include my_critical.h \
    model/*.pss -o gen/

cc -std=c99 -Wall -Wextra -Werror -ffreestanding -Os \
   -ffunction-sections -c gen/<p>.c
```

The generated code compiles clean under `-Wall -Wextra -Werror -ffreestanding`;
if it does not, that is a generator bug worth reporting.

---

## 9. ABI

The component structs are **complete types**, not opaque: firmware needs the size
to place one in static storage, and `_init` is inlinable only when the layout is
visible. The cost is that the layout is part of the header's contract.

**Rebuild every translation unit that names the type when the model changes.**

The header's banner carries the generator version and an `ABI-affecting
settings:` line — `addr-bits`, `lifecycle`, `reg-style`, `mem-access`,
`struct-args`. A caller built with different values is not compatible, and
because both artefacts are validly generated and both compile, `diff` on that
line is the only diagnostic you get.
