# Register/memory-access readability: gap analysis and proposal

> **Archived working note.** Kept in `docs/design/` as the record of why the
> `reg_map` register-access change shipped; the code is the authority where
> the two disagree.

**Status (2026-10-12): implemented.** Item A (named masks) and the `reg_map`
default (separate `<prefix>_regs.h`, `pssc_read<N>`/`pssc_write<N>`/
`pssc_write_masked<N>`) described below as proposals are now in place -- see
"What shipped" at the end of this file.

Source request: `reg_mem_access.md` (repo root, uncommitted). This reviews
that wishlist against what the C op-model backend already does
(`src/pssc/targets/c/{mem_access,lower_reg_model,lower_progseq,style,c_progseq_gen}.py`)
and proposes what is actually left to build.

## What the wishlist is asking for, item by item

1. Replace per-register accessor functions (`REGXYZ_write(self, 1)`) with
   calls to generic primitives (`write32(&self->regs.REGXYZ, 1)`,
   `write32_masked(&self->regs.REGXYZ, 1, 0)`).
2. Put the register structs in a file only the `.c` includes, not the public
   header.
3. Make the primitives "raw" (no `self`), with a `pssc_write32`/`pssc_mmio.h`
   override seam.
4. More readable offsets: const-static field-offset/mask structs, or macros,
   generated alongside the register layout.

## What already exists

This backend grew most of this independently, under different names, while
`reg_mem_access.md` sat uncommitted:

- **(2) and (3) are done.** `--header-only=false` (the default) already
  puts `reg_values`, `reg_map` and the baked accessors in the `.c` only
  (`backend.py::header_sections` vs `impl_sections` — "the register value
  layouts and the baked accessors are how the device is poked, not what a
  caller may call"). And `--link-style {direct,mmio}` / `--mem-access
  {pointer,functions,selectable}` already give exactly the raw-primitive
  seam asked for: `pssc_mem_fn.h`'s `pssc_r32`/`pssc_w32` take `(const void
  *s, addr)` and ignore `s`, calling user-supplied `pssc_mem_read32`/
  `pssc_mem_write32` — which is precisely the `HAVE_PSSC_MMIO_H` idea, just
  spelled as a link-time symbol instead of a header macro. `--mem-access
  pointer` is the direct `*(volatile uint32_t*)addr` form. So "no `self`,
  user overrides the primitive" is shipped, under `--mem-access`/
  `--link-style`, not under a new flag.
- **A layout-independent register style already exists too**:
  `--reg-style accessors` (`lower_reg_model.py::_emit_accessor_struct`)
  emits a plain `{ uint32_t raw; }` plus `<name>_<FIELD>_get/_set` inline
  helpers instead of C bitfields — the "more accessible" alternative to
  bitfields the wishlist's §Masking section was reaching for, just as
  get/set functions rather than a `field_const_s{off,msk}` table.
- **The register-map struct with offset assertions already exists**
  (`lower_reg_model.py::emit_reg_map`): a `typedef struct {...} <group>_s`
  laid out by `reg_layout.py`, pinned with `_Static_assert` per member. This
  is the struct the wishlist's `write32(&self->regs.REGXYZ, ...)` would
  index into — it's generated today, just not yet what the accessors or the
  body lowering address through.

## What's genuinely missing

**(1) is not done.** The body emitter (`lower_progseq.py::_reg_access`) and
`lower_reg_model.py::emit_accessor` still always go through a *baked,
per-register* accessor (`<base>_read`/`_write`/`_write_val_masked`, folded
address and all), never through the register-map struct's field plus a
generic `pssc_w32(bus, &self->regs.REGXYZ, v)`. For a device with hundreds
of registers that's hundreds of tiny inline functions in the `.c`, most
unused by any one build (`_SI` / `PSSC_MAYBE_UNUSED` exists specifically to
keep `-Werror -Wunused-function` quiet about that).

**Field masks are still raw hex literals at the call site.** `write_field`/
`write_fields` compile down to `reg_masked_write(acc, h, idx, 0x7fu, val)`
(`lower_progseq.py` field-select lowering, `_c_int_literal`) — there's no
named constant or comment carrying the field name through to the generated
masked-write call, which is exactly the "when a field is referenced for a
bitmask, the literal is shown" complaint in the wishlist. This is real and
unaddressed by anything above.

## Proposal

Two independent improvements, in priority order:

### A. Name the mask at the call site (small, high payoff, no API change)

`write_field(REG1.F1, 1)` already carries the field name through the AST to
where `_c_int_literal(sel.mask, ...)` renders the literal
(`lower_progseq.py` around the bit-select helpers). Emit a trailing comment
naming the field — `write_val_masked(h, 0x7fu /* F1 */, val)` — or, for the
`--reg-style accessors` struct, emit real macros (`REG1_F1_MASK`,
`REG1_F1_SHIFT`) next to the `_get`/`_set` helpers and have the masked-write
call site use them instead of re-deriving the literal. The macro form is
strictly better (greppable, usable by hand-written code too) and costs
little: `_emit_accessor_struct` already computes `mask`/`fs.lsb` per field,
it just doesn't export them.

### B. A `reg_accessor_form` that addresses through the map struct

Add a third `reg_accessor_form` (today's binary is baked-inline vs.
addr-only/macro, chosen by the style policy, `style.py::reg_accessor_form`)
that skips generating `_read`/`_write`/`_write_val_masked` per register
entirely and has `lower_progseq._reg_access` call the funnel's existing
`read`/`write`/`masked_write` primitives directly against
`&h->regs.<path>` (the register-map member `emit_reg_map` already lays out
and asserts the offset of). This is exactly the wishlist's `write32(&self->
regs.REGXYZ, 1)` shape, built on infrastructure that's already there
(`reg_layout.collect_accessors` for the path, `MemAccess.write`/`masked_write`
for the call). It would cut generated code size roughly proportional to
register count for drivers that use few of many registers — the same
scaling argument `map_type_name`'s docstring already makes for the
register-map struct itself, just not yet carried through to how a register
is *touched*, only to how it's *laid out*.

This is a bigger change than A: it touches `MemAccess.reg_read/reg_write/
reg_masked_write`, `_collect_accessors`/`accessor_kinds` (nothing to bake if
this form is selected), and the READONLY/WRITEONLY direction checks in
`_require` would need to run against the map member instead of a baked
accessor's `.access`. It composes cleanly with the existing style-policy
seam (`CStylePolicy`) rather than replacing it, and the three forms (baked,
macro/addr-only, map-generic) would sit side by side exactly as
baked/addr-only do today.

## Recommendation

Do (A) first — it's a few lines in `_emit_accessor_struct` plus the
masked-write call sites, no new flag, and it directly kills the literal
complaint. Treat (B) as a follow-on `--reg-accessor-form map` (or similar)
once there's a concrete driver where the per-register accessor count is
actually the pain point, since the baked form's main cost (code size) is
already opt-outable today via `--reg-style accessors` + `--header-only=false`
and the `macro`/addr-only form, and B is a real new code path to test and
maintain (differential tests, golden snapshots, `c_names` collision
checking all need a third form added).

## What shipped (2026-10-12)

Scope, per the production evidence (10,000+ lines of baked accessors): the
existing `reg_map` mechanism (`--link-style mmio` / `--mem-access pointer` --
the only mechanism that is a real in-process pointer, so the only one this
applies to; `vtable`'s bus-indirected seam is unchanged) became the thing to
fix directly, not a new third form:

* **`pssc_mem_reg.h`** renamed its primitives `pssc_read<N>`/`pssc_write<N>`
  (from the unprefixed `read<N>`/`write<N>`, which cost the includer that
  name) and added `pssc_write_masked<N>` -- a real primitive now, not a
  read-modify-write spelled out at every call site
  (`lower_progseq.py::_reg_map_access`/`_mem_call`).
* **The private `<prefix>_regs.h`** (`COpModelBackend.regs_text`/
  `regs_sections`, `CStylePolicy.regs_header_name`): under every non-
  `--header-only` build (not only `reg_map`), the register value-union
  layouts the public header does not need to name move here, `#include`d by
  the generated `.c` alone. Under `reg_map` the register-map struct
  definitions move here too (`emit_regs_reg_maps`, `tagged=True` --
  `struct name { ... };` completing a tag), and the public header gets only
  a forward declaration (`emit_reg_map_decls`: `typedef struct name name;`)
  for the pointer member the component handle holds.
* **A register of a sub-component, reached directly from the parent**
  (`leaf[i].regs.stat.read()`, not `leaf[i].read_stat()`), now lowers under
  `reg_map` instead of raising -- `_reg_path`/`_reg_map_access` take the hop
  a sub-component chain starts from and whether it is a pointer or an
  embedded-by-value path, which is what the previous "not lowered yet"
  limitation was missing. Covered by
  `tests/progseq/test_c_reg_map_sub_component.py` (includes a real compile).

Golden snapshots regenerated and reviewed (`c-vtable`, `c-mmio`,
`c-mmio-hdr`, `c-fn-static`, `c-accessors`); every non-`reg_map` config's
diff is the value-layout extraction alone, nothing else moved. `c-mmio`'s and
`c-mmio-hdr`'s generated C recompiled clean under `gcc -std=c99 -Wall -Wextra
-Werror`.

## Follow-up (2026-10-14): the layout struct on the functions seam

The scope note above ("the only mechanism that is a real in-process pointer,
so the only one this applies to") was wrong in its premise. Generated code
never dereferences `&_self->regs->X`; it computes an address and hands it to a
primitive, and only the primitive decides what an address becomes. So:

* **`pssc_mem_reg.h` is seam-agnostic.** Each `pssc_read<N>`/`pssc_write<N>`
  converts the pointer back to a `pssc_addr_t` and calls the seam's own
  `pssc_r<N>`/`pssc_w<N>` with a NULL context. Barriers are the seam's
  (`pssc_mem_ptr.h` applies them; `pssc_mem_fn.h` deliberately does not). It
  requires `PSSC_MEM_SEAM_CONTEXT_FREE`, which only the pointer and functions
  seams define, and asserts `sizeof(pssc_addr_t) <= sizeof(uintptr_t)`.
* **`reg_map = link_style != "vtable"`** (`c_progseq_gen.settings_for`):
  `--link-style direct` (with or without `--mem-access functions`) and
  `--mem-access selectable` now get the layout struct too. vtable keeps the
  folded accessors: its primitives need a real `const pssc_mem_if *`.
* **Cost:** a functions-seam build on a 32-bit core with a 64-bit
  `pssc_addr_t` no longer compiles (the assert says `--addr-bits 32` or
  vtable). Before, its folded accessors carried the address by value.
* **Stride padding** (`reg_layout.element_sizes`): one struct per group type,
  padded to the largest stride any array of it uses, decided before any map is
  built. Previously the first visit won, and since `collect_reg_groups` is
  post-order an element type was always first seen unpadded.

