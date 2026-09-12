# PSS → C / C++ Programming-Sequence Generation — Design

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **Draft for review**
Date: 2026-06-19
Scope: Extend the component-tree → programming-API generator (today: `sv-progseq`,
shipped) to two new backends — **C** (with a *direct-link* and a *struct-vtable*
flavor) and **C++** (pure-virtual API + generated implementation). Same tree
walk, same register/driver lowering; only the per-language emission differs.

Builds on: `design/pss-programming-seq-gen-design.md` (the SV scheme, §10 decision 6
already commits to multi-language parity), `src/pssc/targets/progseq_model.py`
(the backend-neutral model), and the validated reference
`examples/export/programming_seqs/wb_dma_sv_proto.sv`.

> **Note on scope.** This is the *programming-sequence* projection — a **direct**
> lowering of component structure + functions + registers into a reusable driver
> API. It is unrelated to `design/pss-c-runtime-design.md`, which generates a C
> runtime for **solved scenarios** (actions, coroutines, dv-solve). Different
> input (component tree vs. activity), different output (driver API vs. scenario
> `.so`). The two share neither code nor runtime.

---

## 1. What carries over unchanged

The generator is already split into a **language-neutral model** and **per-language
emitters**:

- `progseq_model.py` — `walk_tree()`, `func_kind()`/`FuncKind`, `comp_kind()`/
  `CompKind`, and the field/array/register introspection helpers. **Reused verbatim.**
- `sv/lower_reg_model.py` + `sv/lower_progseq.py` — the SV emitters. The C and C++
  emitters are siblings (`c/`, `cpp/`), structured identically.

The classification is already backend-free: a reg-group is a `super`-chain match,
a regular component owns operations, functions are classified by `is_import` /
`is_solve` / name. **Nothing in §6.2 of the SV design is SV-specific.** Two small
refactors are needed first (§7).

The one thing every backend must reproduce, and the only thing that changes shape
between them, is **the seam**: how a register access reaches the user's bus, and
how the user supplies import implementations. SV expresses the seam as an
interface class (`pss_mem_if`) plus a redirect; C and C++ express it differently,
but the lowered *operation bodies are identical across all flavors of a language*
— only the seam header differs. That invariant is the backbone of this design.

---

## 2. Mapping the four SV artifacts to C / C++

For each regular component the SV backend emits: a register model, an **export
API** (`<comp>_if`), an **import API** (`<comp>_import_if extends pss_mem_if`),
and a **component class** (`<comp>` = export impl + import redirect + factory).
Here is how each lands in C and C++:

| SV artifact | C (direct-link) | C (vtable) | C (mmio) | C++ |
| --- | --- | --- | --- | --- |
| `pss_mem_if` (core seam) | extern free fns `pssc_write32(...)` | `pssc_mem_if` struct of fn-ptrs + `ctx` | inlined `*(volatile T*)addr` load/store | `pssc::mem_if` abstract base (pure virtual) |
| `reg_c #(T,ACC)` | baked inline accessors (no runtime obj) | same | same | `pssc::reg<T,ACC>` template (1:1 with SV) |
| value `struct packed` | `union { uintN raw; struct {…} }` | same | same | same (header shared with C) |
| `reg_group_c` class | address-bookkeeping struct + `_init()` | same | same | `<group>_c` class, `(mem_if&, base)` ctor |
| export API `<comp>_if` | free fns `<comp>_<op>(self,…)` | same | same | `<comp>_if` abstract base |
| import API `<comp>_import_if` | extern fns (the seam itself) | the vtable struct | none — user supplies no impl | `<comp>_import_if : public mem_if` |
| component class `<comp>` | `<comp>_t` struct + `_create/_init` | same (+ holds `mem_if*`) | same (holds `base` only) | `<comp> : public <comp>_if` |
| factory `create()` | `<comp>_create(base)` | `<comp>_create(bus, base)` | `<comp>_create(base)` | `<comp>::create(imp, base)` |

Two themes:

1. **C/C++ bodies are *simpler* than SV.** SV had to rewrite `x = r.read()` into
   the task/output form `r.read(x)`, `return v` into `status = v; return;`, and
   `repeat{}while` into `forever … break` (SV has no do-while). C and C++ keep
   **native return values, native value-returning reads, and native `do…while`** —
   the body is closer to the PSS source than the SV output is. (Statement and
   expression lowering is mostly shared with the SV `_BodyEmitter`; the read/return/
   loop special-cases simply don't fire.)

2. **C++ needs no redirect trick.** SV folds the register model's bus onto `this`
   because interface classes are stateless. In C++ the user subclasses
   `pssc::mem_if`; the component just holds `mem_if& m_imp` and hands it to the
   register model. No `implements <root>_import_if` on the component, no
   self-as-bus.

---

## 3. The C projection

### 3.1 Target & CLI

New target `c-progseq` (alias `progseq-c`):

```
$ pssc compile -t c-progseq --root wb_dma_c \
      --link-style vtable dma_regs.pss dma_engine.pss -o out/
```

`add_args`:

- `--root NAME` (validated in `run`, as for `sv-progseq`).
- `--prefix NAME` — symbol/file prefix (default: root name sans `_c`, e.g. `wb_dma`).
- `--link-style {vtable,direct,mmio}` (default **`vtable`**) — selects the seam (§3.2).
- `--header-only` — emit a single `<prefix>.h` (static-inline everything) instead
  of `<prefix>.h` + `<prefix>.c`. Natural for the baked-accessor model, and the
  default for `mmio` (which has no `.c` to link). Default off otherwise.

Output: `<prefix>.h` (+ `.c`), plus the copied core seam header(s) so the directory
is self-contained (mirroring `--no-core-copy` from SV).

### 3.2 The seam — the *only* thing `--link-style` changes

A hand-written, IP-independent core header (`pssc_mem.h`) declares the address type
and the access primitives. Each link style is a different definition of the same
small set of **`static inline` seam functions** — `pssc_w32(self, addr, data)` /
`pssc_r32(self, addr)` and the 8/16/64 siblings. Static-inline (not macros) buys
type checking, real argument evaluation, and debuggability while still fully
inlining away; with all three styles the compiler emits the same code it would
for a hand-written access. **Every generated op body is byte-identical across the
three styles** — they call `pssc_w32(self, …)` regardless; only the seam header
selected at generation time differs.

**`direct` — free functions, linked by name (bare-metal / single DUT).** The seam
inlines over user-supplied extern functions:

```c
/* pssc_mem_direct.h — the user implements these and links them in. */
#include <stdint.h>
typedef uint64_t pssc_addr_t;                 /* PSS addr_handle_t; self unused */

void     pssc_mem_write32(pssc_addr_t a, uint32_t d);  /* user-provided */
uint32_t pssc_mem_read32 (pssc_addr_t a);
/* …8/16/64… */

static inline void pssc_w32(const void *self, pssc_addr_t a, uint32_t d) {
    (void)self; pssc_mem_write32(a, d);
}
static inline uint32_t pssc_r32(const void *self, pssc_addr_t a) {
    (void)self; return pssc_mem_read32(a);
}
```

Zero per-instance storage, direct symbol linkage. Cost: one bus implementation per
link unit — you cannot drive two different buses at once.

**`vtable` — a struct of function pointers (multi-instance / host).** The seam
inlines over a per-component `bus` pointer:

```c
/* pssc_mem_vtable.h */
typedef struct pssc_mem_if {
    void     (*write32)(void *ctx, pssc_addr_t a, uint32_t d);
    uint32_t (*read32 )(void *ctx, pssc_addr_t a);
    /* …8/16/64… */
    void *ctx;                                /* opaque user state */
} pssc_mem_if;

static inline void pssc_w32(const pssc_mem_if *bus, pssc_addr_t a, uint32_t d) {
    bus->write32(bus->ctx, a, d);
}
static inline uint32_t pssc_r32(const pssc_mem_if *bus, pssc_addr_t a) {
    return bus->read32(bus->ctx, a);
}
```

The component holds `const pssc_mem_if *bus` and passes it as `self`; multiple
instances with different buses coexist. This is the C analogue of SV's `pss_mem_if`
+ redirect.

**`mmio` — direct memory-mapped access via `volatile *` (bare-metal MMIO).** When
`addr_handle_t` is a real CPU address, the seam is just a `volatile` load/store —
no user code, no function pointer, no call. As a `static inline` it compiles to a
single load/store instruction:

```c
/* pssc_mem_mmio.h */
static inline void pssc_w32(const void *self, pssc_addr_t a, uint32_t d) {
    (void)self; *(volatile uint32_t *)(uintptr_t)a = d;
}
static inline uint32_t pssc_r32(const void *self, pssc_addr_t a) {
    (void)self; return *(volatile uint32_t *)(uintptr_t)a;
}
```

The user supplies *nothing*: there is no import API to implement (table §2), so
`<prefix>_create(base)` takes only the base. The natural mode for firmware/driver
code that the CPU runs against real registers. Caveat: the address space must be
directly CPU-addressable, and there is no hook for logging, backdoor, or a
simulated bus — use `vtable`/`direct` when you need one.

Because the only things that vary are the `pssc_w*/r*` seam definitions, the
one-line `pssc_bus(s)` shim, and whether `<prefix>_t` carries a `bus` field,
**the register accessors and every operation body are generated identically and
shared across all three styles** (§3.4).

### 3.3 Register value structs

PSS `packed_s<>` declares fields LSB-first. On all mainstream little-endian ABIs
(gcc/clang, x86/ARM) C bitfields also allocate LSB-first within a storage unit, so
fields are emitted in **declaration order** (the *opposite* of SV, which reverses
to MSB-first). A C11 anonymous `union`/`struct` gives both whole-register and
field access, matching the PSS `csr.FIELD` spelling exactly:

```c
typedef union {
    uint32_t raw;
    struct {
        uint32_t PAUSE    : 1;   /* [0]    */
        uint32_t reserved : 31;  /* [31:1] */
    };
} dma_csr_t;                      /* csr.raw and csr.PAUSE both valid */
```

> **Portability caveat & fallback.** Bitfield allocation order is
> implementation-defined. The default assumes the common little-endian layout
> (documented in the header banner). For strict portability, `--reg-style=accessors`
> instead emits a plain `uint32_t` plus inline shift/mask helpers
> (`dma_csr_TOT_SZ_set(&v, x)` / `_get(v)`), which are layout-independent.

### 3.4 Register access — baked inline accessors

C has no generics, so rather than a runtime `reg_c` object tree, the C backend
**bakes each register's address arithmetic and width into a static-inline accessor**
(offsets are gen-time constants; only `base` and the array index are runtime). This
is zero-storage and zero-overhead, and the width is resolved to the right primitive
at generation time:

```c
/* per-channel block stride/offsets are gen-time constants */
static inline pssc_addr_t wb_dma_ch_CSR_addr(const wb_dma_t *s, int ch) {
    return s->base + 0x20u + (pssc_addr_t)ch * 0x20u + 0x00u;
}
static inline dma_ch_csr_t wb_dma_ch_CSR_read (wb_dma_t *s, int ch) {
    return (dma_ch_csr_t){ .raw = pssc_r32(pssc_bus(s), wb_dma_ch_CSR_addr(s, ch)) };
}
static inline void wb_dma_ch_CSR_write(wb_dma_t *s, int ch, dma_ch_csr_t v) {
    pssc_w32(pssc_bus(s), wb_dma_ch_CSR_addr(s, ch), v.raw);
}
```

`pssc_bus(s)` is a one-line generated shim that yields the seam's first argument
— `s->bus` (a `pssc_mem_if *`) for `vtable`, and an ignored `NULL` for `direct`/
`mmio` (no `bus` field). It is the *only* generated line that varies by link style;
the `_addr`/`_read`/`_write` accessors and every operation body above them are
emitted identically for all three.

Array indexing (`regs.channels[ch].CSR`) folds into the `_addr` arithmetic; scalar
registers drop the `ch` parameter. Access mode (READONLY/WRITEONLY) suppresses the
unused accessor. (An optional `--reg-style=tree` emits a faithful POD
`pssc_reg_t{addr,width,access}` group tree with `_init()` address-folding for
callers that want a reflective model; not the default for C.)

### 3.5 Export API, component struct, factory

The export API is a set of free functions over an opaque `<prefix>_t`. PSS `int`
returns map to a real C `int` return (no `output status`):

```c
/* wb_dma.h */
typedef struct wb_dma_s wb_dma_t;         /* opaque; defined in .c */

wb_dma_t *wb_dma_create(const pssc_mem_if *bus, pssc_addr_t base);  /* vtable */
/* wb_dma_t *wb_dma_create(pssc_addr_t base);  — direct-link (no bus) */
void      wb_dma_destroy(wb_dma_t *self);

void wb_dma_configure_channel(wb_dma_t *s, int channel, int priority,
                              uint8_t mode, uint8_t src_if, uint8_t dst_if);
int  wb_dma_mem_to_mem_copy  (wb_dma_t *s, int channel,
                              uint32_t src_addr, uint32_t dst_addr, int num_bytes);
/* …_desc, _masked… */
```

`<prefix>_t` holds the root `ctor` state (here just `base`) and, for `vtable`, the
`bus` pointer. The root `solve function ctor(base)` body (`regs.set_handle(base)`)
becomes the initialization of `s->base`. `_create` allocates (or, with
`<prefix>_init(self, …)` for static allocation / embedded, fills a caller-provided
struct — no `malloc`, paralleling the C-runtime design's `ZSP_STATIC_ALLOC` theme).

### 3.6 Import functions beyond memory

The WB DMA engine adds no non-memory imports, but the scheme must cover them
(`import target function` → blocking, `import solve function` → value):

- **direct-link:** additional extern free functions the user links (`pssc_<fn>`).
- **vtable:** additional function pointers, either appended to `pssc_mem_if` or in a
  generated `<root>_import_if` struct that *embeds* `pssc_mem_if` as its first
  member (so a `pssc_mem_if*` still works for the register model).

### 3.7 Worked snippet (operation body, identical for both link styles)

```c
int wb_dma_mem_to_mem_copy(wb_dma_t *s, int channel,
                           uint32_t src_addr, uint32_t dst_addr, int num_bytes) {
    dma_ch_sz_t  sz;
    dma_ch_csr_t csr;

    wb_dma_ch_A0_write(s, channel, src_addr);
    wb_dma_ch_A1_write(s, channel, dst_addr);

    sz.raw = 0;
    sz.TOT_SZ = (uint32_t)(num_bytes / 4);
    sz.CHK_SZ = 0;
    wb_dma_ch_SZ_write(s, channel, sz);

    csr = wb_dma_ch_CSR_read(s, channel);
    csr.INC_SRC = 1; csr.INC_DST = 1; csr.USE_ED = 0; csr.CH_EN = 1;
    wb_dma_ch_CSR_write(s, channel, csr);

    do {                                  /* PSS repeat{}while — native in C */
        csr = wb_dma_ch_CSR_read(s, channel);
        if (csr.ERR == 1) return 1;
    } while (csr.DONE == 0);
    return 0;
}
```

Note how much closer this is to the PSS source than the SV output: native return,
native value-returning reads, native `do…while`.

---

## 4. The C++ projection

C++ is the closest of the three to SystemVerilog: pure-virtual interfaces, a real
`reg<T,ACC>` **template** (1:1 with SV's `reg_c #(T,ACC)`), and class hierarchies.

### 4.1 Target & CLI

New target `cpp-progseq` (alias `progseq-cpp`):

```
$ pssc compile -t cpp-progseq --root wb_dma_c \
      --namespace wb_dma --dispatch virtual dma_regs.pss dma_engine.pss -o out/
```

`add_args`: `--root`, `--namespace NAME` (default `<prefix>`), `--single-header`
(default on — header-only is idiomatic for a template-bearing API), and
`--dispatch {virtual,template}` (default **`virtual`**, §4.5).

### 4.2 Core runtime header `pssc_reg.hpp` (hand-written)

```cpp
namespace pssc {
using addr_t = std::uint64_t;             // PSS addr_handle_t

struct mem_if {                            // THE seam — user subclasses this
    virtual ~mem_if() = default;
    virtual void     write8 (addr_t, std::uint8_t ) = 0;
    virtual std::uint8_t  read8 (addr_t) = 0;
    /* …16/32/64… */
};

enum class access { rw, ro, wo };

// Stock concrete seam for bare-metal MMIO (the C++ analogue of C --link-style mmio):
// volatile load/store against a real CPU address. Shipped, not generated.
struct mmio_mem : mem_if {
    void write32(addr_t a, std::uint32_t d) override { *reinterpret_cast<volatile std::uint32_t *>(a) = d; }
    std::uint32_t read32(addr_t a) override { return *reinterpret_cast<volatile std::uint32_t *>(a); }
    /* …8/16/64… */
};

template <class T, access ACC = access::rw>
class reg {                                // 1:1 with SV reg_c #(T,ACC)
    mem_if &bus_;  addr_t addr_;
    static constexpr unsigned W = sizeof(T) * 8;   // ACC_W dispatch on W
public:
    reg(mem_if &bus, addr_t addr) : bus_(bus), addr_(addr) {}
    T    read()  const;                    // selects read8/16/32/64 on W
    void write(T v);                       // selects write8/16/32/64 on W
};
}
```

Reads return by value (`T v = csr.read();`), writes take the value — no output
args, no status outs. The `T'(raw)` round-trip is a `std::bit_cast`/`memcpy` of the
value-union.

### 4.3 Value structs & register-group classes

Value structs are the **same anonymous-union bitfield headers as C** (§3.3),
emitted into the generated header and usable from both languages. Register-group
classes hold `reg<…>` members and nested groups, constructed `(mem_if&, addr_t base)`,
folding `base + offset` into each child — a direct transliteration of the SV
group class:

```cpp
class dma_channel_regs_c {
public:
    pssc::reg<dma_ch_csr_t>            CSR;
    pssc::reg<dma_ch_sz_t>             SZ;
    pssc::reg<std::uint32_t>           A0, AM0, A1, AM1, DESC;
    pssc::reg<dma_ch_swptr_t>          SWPTR;
    dma_channel_regs_c(pssc::mem_if &bus, pssc::addr_t base)
      : CSR(bus, base + 0x00), SZ(bus, base + 0x04), A0(bus, base + 0x08),
        AM0(bus, base + 0x0c), A1(bus, base + 0x10), AM1(bus, base + 0x14),
        DESC(bus, base + 0x18), SWPTR(bus, base + 0x1c) {}
};
```

Arrays of groups become `std::array<dma_channel_regs_c, 31>`, constructed in a member
initializer with the array stride (an index-generating helper, since `std::array`
elements need explicit construction).

### 4.4 Export & import APIs

```cpp
struct wb_dma_if {                         // export API — pure virtual
    virtual ~wb_dma_if() = default;
    virtual void configure_channel(int channel, int priority,
                                   bool mode, bool src_if, bool dst_if) = 0;
    virtual int  mem_to_mem_copy(int channel, std::uint32_t src_addr,
                                 std::uint32_t dst_addr, int num_bytes) = 0;
    /* …_desc, _masked… */
};

struct wb_dma_import_if : pssc::mem_if {   // import API extends the seam
    /* engine-specific imports (none for WB DMA) added as pure virtuals here */
};
```

### 4.5 Component class & factory

The component implements the export API, holds a `mem_if&` and the register model,
and builds the model with that reference — **no redirect trick** (the C++ user
subclasses `mem_if` directly):

```cpp
class wb_dma : public wb_dma_if {
    pssc::mem_if &imp_;
    dma_regs_c    regs_;
public:
    wb_dma(pssc::mem_if &imp, pssc::addr_t base) : imp_(imp), regs_(imp_, base) {}

    int mem_to_mem_copy(int channel, std::uint32_t src_addr,
                        std::uint32_t dst_addr, int num_bytes) override {
        dma_ch_sz_t sz{};  dma_ch_csr_t csr{};
        regs_.channels[channel].A0.write(src_addr);
        regs_.channels[channel].A1.write(dst_addr);
        sz.TOT_SZ = num_bytes / 4; sz.CHK_SZ = 0;
        regs_.channels[channel].SZ.write(sz);
        csr = regs_.channels[channel].CSR.read();
        csr.INC_SRC = 1; csr.INC_DST = 1; csr.USE_ED = 0; csr.CH_EN = 1;
        regs_.channels[channel].CSR.write(csr);
        do {
            csr = regs_.channels[channel].CSR.read();
            if (csr.ERR == 1) return 1;
        } while (csr.DONE == 0);
        return 0;
    }
    // …configure_channel, _desc, _masked…

    static std::unique_ptr<wb_dma_if> create(pssc::mem_if &imp, pssc::addr_t base) {
        return std::make_unique<wb_dma>(imp, base);
    }
};
```

Usage:

```cpp
my_bus bus;                                  // : public pssc::mem_if
auto dma = wb_dma::create(bus, 0x4000'0000); // wb_dma_if handle
dma->mem_to_mem_copy(3, 0x1000'0000, 0x2000'0000, 16);
```

**`--dispatch template` (zero-overhead, embedded).** For callers who want SV's
duck-typed `#(IMP_T)` performance profile with no vtable, the component is emitted
as `template <class IMP_T> class wb_dma`, the register model as `reg<T, IMP_T>`,
and `IMP_T` is any signature-compatible bus (need not derive from `mem_if`). All
bus calls inline. This is the C++ analogue of C `direct-link` — same trade-off
(speed vs. one concrete bus type per instantiation), and it is the more complex
codegen path, hence not the default.

---

## 5. PSS construct → C / C++ artifact (summary)

| PSS construct | C artifact | C++ artifact |
| --- | --- | --- |
| `addr_handle_t` | `typedef uint64_t pssc_addr_t` | `pssc::addr_t` |
| primitive `read*/write*` | seam: extern fns / `pssc_mem_if` vtable / inlined `volatile *` | `pssc::mem_if` base (+ stock `mmio_mem`) |
| `reg_c<T,ACC,SZ>` | baked inline accessor (gen-time width) | `pssc::reg<T,ACC>` template |
| `struct S : packed_s<>` | `union{ uintN raw; struct{…}; }` | same union header |
| `component G : reg_group_c` | addr-bookkeeping + baked accessors | `<group>_c` class, `(mem_if&, base)` |
| regular `component C` | `C_t` struct + free fns | `C_if` + `C : public C_if` |
| component runtime fn | `int C_op(C_t*, …)` (native return) | `virtual int op(…)` |
| `import target`/`solve` fn | extern fn / vtable ptr | pure virtual in `C_import_if` |
| root `solve function ctor` | `_create`/`_init` params + body | constructor params + body |
| `repeat{}while`, value reads | **native** `do…while`, return values | **native** |

---

## 6. Shared model & refactors needed first

Two small, mechanical refactors to `progseq_model.py` before adding emitters, so
all three backends share them (today they live in `sv/lower_reg_model.py`):

1. **Hoist affine offset evaluation.** Move `_eval_off`, `_array_base_stride`,
   `_scalar_offset`, `_pattern_str` into `progseq_model.py`. They evaluate the PSS
   `get_offset_of_instance[_array]` bodies and are entirely language-neutral; SV,
   C, and C++ all need `(base, stride)` and scalar offsets.
2. **Hoist the value-struct / reg-group collection walks** (`collect_reg_groups`,
   `collect_value_structs`) similarly — they produce the ordered type lists every
   backend emits.

After this, `sv/`, `c/`, and `cpp/` each contain only language-specific *emission*
(type spelling, accessor shape, body rendering), and the body-statement walker can
be largely shared (the SV `_BodyEmitter` minus the three SV-only rewrites).

New files (proposed):

```
src/pssc/targets/c_progseq_tgt.py        Target: c-progseq
src/pssc/targets/cpp_progseq_tgt.py      Target: cpp-progseq
src/pssc/targets/c/lower_reg_model.py    value unions + baked accessors
src/pssc/targets/c/lower_progseq.py      export fns + component struct + bodies
src/pssc/targets/cpp/lower_reg_model.py  value unions + reg<T> group classes
src/pssc/targets/cpp/lower_progseq.py    *_if/*_import_if + component class + bodies
src/pssc/share/c/pssc_mem_direct.h       core seam (direct-link)
src/pssc/share/c/pssc_mem_vtable.h       core seam (vtable)
src/pssc/share/c/pssc_mem_mmio.h         core seam (volatile MMIO)
src/pssc/share/cpp/pssc_reg.hpp          core seam + reg<T,ACC> template + mmio_mem
```

---

## 7. Open questions / decisions to confirm

1. **C bitfield default vs. accessors.** Recommend anonymous-union bitfields as the
   default (legible, matches PSS `csr.FIELD`) with `--reg-style=accessors` as the
   portable fallback. Confirm the default targets little-endian LLP64/LP64 only,
   documented in the header banner. *(Decision needed.)*
2. **C link-style default.** Recommend `vtable` (multi-instance, host-friendly,
   matches SV semantics); `direct` (linked extern fns) and `mmio` (inlined
   `volatile *`) are the bare-metal opt-ins. *(Confirm.)*
3. **C++ dispatch default.** Recommend `virtual` (simple, matches the SV interface
   model); `template` is the zero-overhead opt-in. *(Confirm.)*
4. **Allocation.** Provide both `_create` (heap) and `_init(self,…)` (caller-owned
   storage) for C, and a stack-constructible class for C++, so embedded targets
   avoid `malloc` — consistent with the C-runtime design's static-alloc stance.
5. **Acceptance artifact.** SV has `wb_dma_sv_proto.sv` + a self-checking TB. We
   should hand-write the equivalent gold references — `wb_dma_c_proto.c` (both
   link styles) and `wb_dma_cpp_proto.cpp` — with a port of the same mock bus +
   self-check, so each backend has a compile-and-`PASS` target (as SV does with
   Verilator). *(Recommend writing these next, before the emitters.)*

---

## 8. Files & references

- `design/pss-programming-seq-gen-design.md` — the SV scheme this parallels.
- `src/pssc/targets/progseq_model.py` — the shared model (and refactor target, §6).
- `src/pssc/targets/sv/lower_reg_model.py`, `sv/lower_progseq.py` — emitter siblings.
- `src/pssc/share/sv/pssc_reg_pkg.sv` — the SV core seam this mirrors in C/C++.
- `examples/export/programming_seqs/wb_dma_sv_proto.sv` — the gold reference whose
  shape the C/C++ references (§7.5) should reproduce.
- `examples/export/programming_seqs/dma_engine.pss`, `dma_regs.pss` — the source PSS.
</content>
</invoke>
