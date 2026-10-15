/* pssc_mem_reg.h -- register access through the layout struct: a pointer in,
 * the seam's own primitive out.
 *
 * The generated driver forms a register's effective address by following its
 * register-layout struct, and hands the result to one of these:
 *
 *     pssc_write32(&s->regs->int_msk_a, 25);
 *     uint32_t v = pssc_read32(&s->regs->csr);
 *
 * Nothing here dereferences that pointer. Each primitive turns it back into a
 * `pssc_addr_t` and calls the memory seam the generated header chose --
 * `pssc_w<N>`/`pssc_r<N>` -- so the layout struct is address ARITHMETIC only,
 * and what an address becomes is the seam's decision, as it is everywhere
 * else:
 *
 *   * pssc_mem_ptr.h (`--mem-access pointer`, `--link-style mmio`): a
 *     `volatile` load or store, inlined to one instruction;
 *   * pssc_mem_fn.h (`--mem-access functions`, `--link-style direct`): a call
 *     to the platform's `pssc_mem_read32`/`pssc_mem_write32`, which may log,
 *     mock, or reach a simulated bus;
 *   * `--mem-access selectable`: whichever of the two pssc_mem.h picked.
 *
 * The vtable seam is NOT served. Its primitives need a real
 * `const pssc_mem_if *`, and a pointer to a register has no room to carry one;
 * passing NULL would compile and crash. `--link-style vtable` keeps the folded
 * per-register accessors, and this header refuses to be included under it.
 *
 * WHY A POINTER RATHER THAN AN ADDRESS. With the layout struct doing the
 * arithmetic the address already exists as a pointer -- `&s->regs->bank[3].csr`
 * -- and it gets the compiler to check the arithmetic: a mistyped member is an
 * error here and a silently wrong offset in a hand-folded constant.
 *
 * WHY `pssc_` ON EVERY NAME. A bare `read32`/`write32` is exactly the name a
 * project's own code reaches for, and a generated header is not allowed to
 * cost its includer that name. Every other seam in this family already
 * prefixes its primitives (`pssc_r32`/`pssc_w32`); this one spells the prefix
 * out (`pssc_read32`/`pssc_write32`) because it is the one meant to be read
 * and called directly from hand-written driver code, not only from generated
 * bodies.
 *
 * WHY NO CONTEXT ARGUMENT. The seams this header serves ignore theirs, so it
 * passes NULL -- which is also why it cannot serve the one seam that does not.
 *
 * ORDERING is the seam's: pssc_mem_ptr.h applies PSSC_MEM_BARRIER() around
 * each access, so this header does not apply it a second time. pssc_mem_fn.h
 * does not apply it, deliberately -- the call is already a compiler barrier,
 * and what the platform's function does about the CPU is that function's
 * business (see pssc_mem.h).
 *
 * WIDTH. A pointer cannot hold an address wider than itself, under any seam:
 * `(T *)(uintptr_t)base` would truncate the base before the arithmetic began.
 * Checked below rather than left to wrap. The fix is `--addr-bits 32` when the
 * device map fits, else `--link-style vtable`, whose folded accessors carry
 * the address by value end to end.
 *
 * CONST IS LOAD-BEARING. A READONLY register is emitted as a `const` member of
 * the layout struct, so `&s->regs->int_src_a` is a `const uint32_t *` and will
 * not convert to the `volatile void *` the writes take. Writing a read-only
 * register is a compile error, which is what the absent `_write` accessor used
 * to buy and the reason the reads take a `const volatile void *`.
 *
 * `pssc_write_masked<N>` IS PSS 3.1 SEC21.14.1'S READ-MODIFY-WRITE, NAMED. A
 * masked write still reads the register first -- a status CSR that clears on
 * read has that as a real side effect, not an optimization to drop -- and
 * giving it one name means every masked-write call site stops spelling the
 * read-modify-write out by hand.
 */
#ifndef PSSC_MEM_REG_H
#define PSSC_MEM_REG_H

#include <stddef.h>
#include <stdint.h>

/* Set by the seams whose primitives ignore their context argument
 * (pssc_mem_ptr.h, pssc_mem_fn.h). Absent means either no seam was included
 * yet -- include the generated header's seam first -- or the seam is vtable. */
#ifndef PSSC_MEM_SEAM_CONTEXT_FREE
#  error "pssc_mem_reg.h needs pssc_mem_ptr.h or pssc_mem_fn.h (or pssc_mem.h selecting one) included first; the vtable seam has no layout-struct register access"
#endif

PSSC_STATIC_ASSERT(sizeof(pssc_addr_t) <= sizeof(uintptr_t),
                   pssc_addr_t_wider_than_a_pointer__regs_need_addr_bits_32_or_vtable);

#define PSSC_REG_ADDR(p) ((pssc_addr_t)(uintptr_t)(p))

static inline void     pssc_write8 (volatile void *p, uint8_t  d) { pssc_w8 (NULL, PSSC_REG_ADDR(p), d); }
static inline uint8_t  pssc_read8  (const volatile void *p)       { return pssc_r8 (NULL, PSSC_REG_ADDR(p)); }
static inline void     pssc_write16(volatile void *p, uint16_t d) { pssc_w16(NULL, PSSC_REG_ADDR(p), d); }
static inline uint16_t pssc_read16 (const volatile void *p)       { return pssc_r16(NULL, PSSC_REG_ADDR(p)); }
static inline void     pssc_write32(volatile void *p, uint32_t d) { pssc_w32(NULL, PSSC_REG_ADDR(p), d); }
static inline uint32_t pssc_read32 (const volatile void *p)       { return pssc_r32(NULL, PSSC_REG_ADDR(p)); }
static inline void     pssc_write64(volatile void *p, uint64_t d) { pssc_w64(NULL, PSSC_REG_ADDR(p), d); }
static inline uint64_t pssc_read64 (const volatile void *p)       { return pssc_r64(NULL, PSSC_REG_ADDR(p)); }

static inline void pssc_write_masked8(volatile void *p, uint8_t mask, uint8_t val) {
    uint8_t cur = pssc_read8(p);
    pssc_write8(p, (uint8_t)((cur & (uint8_t)~mask) | (val & mask)));
}
static inline void pssc_write_masked16(volatile void *p, uint16_t mask, uint16_t val) {
    uint16_t cur = pssc_read16(p);
    pssc_write16(p, (uint16_t)((cur & (uint16_t)~mask) | (val & mask)));
}
static inline void pssc_write_masked32(volatile void *p, uint32_t mask, uint32_t val) {
    uint32_t cur = pssc_read32(p);
    pssc_write32(p, (cur & ~mask) | (val & mask));
}
static inline void pssc_write_masked64(volatile void *p, uint64_t mask, uint64_t val) {
    uint64_t cur = pssc_read64(p);
    pssc_write64(p, (cur & ~mask) | (val & mask));
}

#endif /* PSSC_MEM_REG_H */
