# `read_struct` / `write_struct` in the op-model targets — plan

Status (2026-10-31): **C and SV done; C++ and Python to do.** Q1–Q8 settled
(see "Decisions").

| Step | State |
|---|---|
| 1 Layout model (`targets/packed_image.py`, `tests/unit/test_packed_image.py`) | done |
| 2 C (`c/lower_api_types.py` union, `c/lower_progseq.py` lowering) | done |
| 3 C++ | to do |
| 4 SV (`sv/lower_progseq.py::_struct_mem`) | done |
| 5 Python | to do |
| 6 Legality + matrix (`tests/progseq/matrix/test_m_packed_struct.py`) | c, sv entries done; cpp, py strict xfail |
| 7 Example (`write_descriptor.pss` uses `write_struct`) | done; example 6/6, labs 1-4 pass |
| 8 Docs | lab doc sources needed no change (references are generated); design-doc sections still to write |

## Goal

Lower the PSS 21.12 core-library calls

```pss
target function void read_struct (addr_handle_t hndl, struct packed_struct, mem_access_desc_s d = {});
target function void write_struct(addr_handle_t hndl, struct packed_struct, mem_access_desc_s d = {});
```

on the C, C++, SV and Python op-model targets. A struct argument becomes its
`packed_s` bit image, and that image moves through the memory primitives that
already exist (`write8/16/32/64`, `read8/16/32/64`). The first user is
`example/wb-dma/pss/wb_dma_c/functions/write_descriptor.pss`. Its hand-built
`csr_word` and four `write32` calls become:

```pss
d = desc; d.next = 0;
write_struct(at, d);
```

## Where things are today

| Piece | State |
|---|---|
| Front end (pssparser stdlib `addr_reg_pkg.pss:135-138`) | Declares both functions. The comment in `write_descriptor.pss` saying they are commented out is stale. |
| `targets/call_legality.py:233-238` | Both calls are marked `unsupported`, so every op-model target refuses them. |
| C API structs (`c/lower_api_types.py::c_member_type`) | Members get the smallest standard type and no defined layout. `wb_dma_desc_csr_s` comes out as `uint8_t`/`uint16_t` members. |
| C/C++ register value types (`*/lower_reg_model.py::emit_value_union`) | Already `union { uintN_t raw; struct { bitfields }; }`, LSB-first, with N = 8/16/32/64. Fields must be scalars. |
| SV API structs | Already `struct packed` with the LSB-first field declared last, so they are already their own image. |
| Python register values (`py/lower_reg_model.py::_RegValue`) | Already have `pack()` (to an int) and `unpack(raw)` driven by a field/LSB/width table. Plain structs (`_Struct`) have no layout. |
| Layout arithmetic | `reg_field_resolve.struct_layout` / `is_big_endian` give one slice per field, with endianness handled. |

## Decisions

| Q | Decision |
|---|---|
| Q1 Which structs get a layout-exact type | Every `packed_s` struct of **64 bits or fewer** gets the C/C++ union. Larger `packed_s` structs stay plain structs and are accessed member by member (Q8). |
| Q2 Access width | **Follows the struct's size; there is no option.** A struct of 8, 16, 32 or 64 bits is one access of that width (`write8`…`write64`). |
| Q3 Sizes that aren't 8/16/32/64 bits | For a struct of 64 bits or fewer, use the fewest power-of-two accesses that cover `ceil(bits/8)` bytes, lowest address first (48 bits is `write32` then `write16`; 24 bits is `write16` then `write8`). No byte past the struct is touched. |
| Q4 Reserved fields | Keep them named, so `read_struct` round-trips exactly. |
| Q5 C++ type punning | Use the union for structs of 64 bits or fewer. Under Q8 as proposed, a larger struct is accessed member by member and needs no punning, so the planned `std::memcpy` drops out. |
| Q8 Structs over 64 bits (**agreed**) | Access **each top-level member at its own size**, recursively. For `wb_dma_desc_s` that is `csr` (a 32-bit union) and then `adr0`, `adr1` and `next`, giving four `write32`s at +0/+4/+8/+0xc, the same accesses the hand-written code makes. The alternative is 64-bit chunks of the whole image via `memcpy`, which would be two `write64`s on a 32-bit WISHBONE bus. |
| Q6 `mem_access_desc` | Refuse anything other than the default. |
| Q7 Python | Implement it as step 5 below. Python integers have no width limit, so this is the easiest target. |

## Approach per target

### C and C++, structs of 64 bits or fewer: the union

This is the register-value union, used for API structs as well:

```c
typedef union {
    uint32_t raw;
    struct {
        uint32_t tot_sz    : 12;          /* [11:0]  */
        uint32_t reserved0 :  4;          /* [15:12] */
        uint32_t dst_sel   :  1;          /* [16]    */
        ...
        uint32_t reserved1 : 11;          /* [31:21] */
    };
} wb_dma_desc_csr_s;
_Static_assert(sizeof(wb_dma_desc_csr_s) == 4, "...");
```

- `raw` is the smallest of 8/16/32/64 bits that holds the struct, as `prim_bits` already does for register values.
- `write_struct(at, v)` becomes one `writeN(at, v.raw)`, or the Q3 sequence when the size is not 8/16/32/64 bits.
- `read_struct(at, s)` is the reverse into `s->raw`.
- Field access, assignment, designated initialisers and the existing by-handle / `const T *` parameters keep their spelling, because C11 anonymous members are transparent.

Bitfield rules, each refused with a diagnostic when broken:

1. **Little-endian `packed_s` only.** A big-endian struct is refused for `read_struct`/`write_struct`.
2. **Every field has a width.** Integers, bools and enums are allowed. An enum is declared as an unsigned bitfield, and its value converts implicitly.
3. **No nested `packed_s` field inside a struct of 64 bits or fewer.** A bitfield can't hold a struct, and flattening it would break the `d.inner.x` spelling, so it is refused for now. Revisit only if a model needs it.

### C and C++, structs wider than 64 bits: member by member (Q8)

The struct stays a plain struct. Each member is accessed at its own size and
its offset in the packed image:

```c
/* write_struct(at, d) for wb_dma_desc_s (128 bits) */
wb_dma_write32(_self, at,                       d.csr.raw);   /* 32-bit union */
wb_dma_write32(_self, pssc_addr_add(at, 4),     d.adr0);
wb_dma_write32(_self, pssc_addr_add(at, 8),     d.adr1);
wb_dma_write32(_self, pssc_addr_add(at, 0xc),   d.next);
```

`read_struct` is the reverse: `s->csr.raw = read32(at); s->adr0 = ...`.

Each member must start at a byte offset that is a multiple of its own size,
and be one of:
- a scalar of exactly 8, 16, 32 or 64 bits;
- a nested `packed_s` of 64 bits or fewer, accessed through its `raw`;
- a nested `packed_s` wider than 64 bits, recursively.

Anything else is refused, naming the field. An example is a `bit[12]` field
directly in a 128-bit struct; wrap it in a 32-bit packed struct instead. The
host's struct layout never matters, since every access goes through a named
member, so these structs need no `_Static_assert`s.

### SV

SV needs no new types, but the SV `struct packed` is **not** reliably the
image: an SV enum is declared without a base type, so inside a packed struct
it is a 32-bit int (found by the matrix test). So the image is a
`bit [N-1:0] pssc_img` assembled field by field from the PSS layout
(`packed_image.leaves`, nested structs flattened), and each access is a slice
`pssc_img[8*off +: W]`. `read_struct` reads the slices through a temporary
per width, then assigns each field back, with a cast for an enum. The 64-bit
cap doesn't apply.

*Separate defect, not fixed here:* every SV `struct packed` with an enum field
is wider than its PSS layout for the same reason. That matters wherever such
a struct is treated as bits, for example a register value struct with an enum
field.

### Python (Q7)

Python integers have no width limit, so this target needs neither the 64-bit
split nor a union:

- **Layout.** Every `packed_s` struct class gets a `LAYOUT` table, the same
  shape `_RegValue` uses, computed by `packed_image`. A new base class,
  `_PackedStruct(_Struct)`, adds `_pss_pack() -> int` and
  `_pss_unpack_into(raw)`. Both recurse through nested packed structs, and
  unpacking happens in place so a by-handle parameter stays the caller's
  instance (LRM 20.3.2). Any width and any nesting works; only big-endian is
  refused, as elsewhere.
- **Lowering.** `write_struct(h, v)` becomes
  `img = v._pss_pack()` followed by
  the same access sequence as the other targets, each
  `self.writeN(h + off, (img >> 8*off) & mask)`, through
  the same `_MEM_PRIMS` path, with `await` when the model is async.
  `read_struct` builds `img` with ORs, then calls `s._pss_unpack_into(img)`.
- **Order.** Python can go in first. It has no layout to fight, so it can carry
  the cross-target reference image (step 6).

## Steps

1. **Layout model, independent of target.** Add `packed_image(struct_dtype)`
   beside `struct_layout`. It returns total bits and a member tree: a scalar
   slice, a nested packed struct, or reserved space. It also gives two
   verdicts: `union_ok` (64 bits or fewer and no nested struct) and
   `member_ok` (wider, every member naturally aligned at 8/16/32/64 bits or a
   nested packed struct that qualifies). Add `access_plan(struct_dtype)`, which
   returns the `(byte offset, width, member path)` sequence for both cases,
   including the Q3 tail. Unit tests go in
   `tests/unit/` and cover:
   - `wb_dma_desc_csr_s` and `wb_dma_desc_s`;
   - a field crossing a word boundary in a wide struct;
   - big-endian;
   - an enum field;
   - a 48-bit tail;
   - a nested struct in a struct of 64 bits or fewer;
   - sizes 8, 16, 24, 32, 48 and 64 bits, and 128 bits nested.
2. **C.** In `c/lower_api_types.py`, emit the union for each `packed_s` of 64
   bits or fewer, with `_Static_assert`s. Non-`packed_s` structs
   don't change. In `c/lower_progseq.py`, lower both calls through the
   existing memory-primitive path (the `mem_access.py` funnel), so every link
   style works unchanged. The struct argument can be a local variable, a `(*p)`
   handle parameter or a `const T *`. Compile with
   `-std=c11 -Wall -Wextra -Werror`.
3. **C++.** Make the same changes in `cpp/lower_api_types.py` and
   `cpp/lower_progseq.py`.
4. **SV.** In `sv/lower_progseq.py`, use an image temporary and slice it
   along the shared `access_plan`.
5. **Python.** Add `_PackedStruct` and `LAYOUT` in `py/lower_api_types.py`
   and the lowering in `py/lower_progseq.py`, in sync and async forms.
6. **Legality and cross-target test.** In `call_legality.py`, move both calls
   to MEM for c, cpp, sv and py. Add a case under `tests/progseq/matrix` that:
   - `write_struct`s a mixed struct (bits, bool, enum, a nested whole-word
     struct, a tail);
   - `read_struct`s it back into a second variable and compares the fields.

   Each target runs the case against its fake memory, and the byte image is
   checked against one computed from `packed_image`. That reference check is
   what catches a layout bug every target shares. Run it for struct
   sizes 8, 24, 32, 64 and 128 bits.
7. **Example.**
   - Rewrite `write_descriptor.pss` around `write_struct` and drop the stale
     comment.
   - Check whether `WB_DMA_DESC_BYTES` and `WB_DMA_DESC_NEXT_OFF` are still
     used. NEXT_OFF still is, for the `prev` link write.
   - Run `dfm run tests` in `example/wb-dma` (expect 6/6), then labs 1 and 2.
8. **Docs.**
   - Update the `write_descriptor` passages wherever they describe the manual
     serialisation:
     - `docs/lab1/{registers,exercises}.md`
     - `docs/lab2/{index,walkthrough,exercises}.md`
     - `docs/lab1/slides/index.md` and `docs/lab2/slides/index.md`
     - `docs/lab4/{soc,exercises}.md`
   - Add a "packed_s API types" section to
     `pss-c-cpp-progseq-gen-design.md`.
   - Update `lowering-call-legality.md`.

Steps 1–2 and the C part of step 6 are the smallest slice the firmware can
use. The other targets can follow independently.
