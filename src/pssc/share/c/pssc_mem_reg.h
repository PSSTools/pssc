/* pssc_mem_reg.h -- the BARE-METAL register seam: a pointer in, a load or a
 * store out, and nothing else.
 *
 * The other seams take an ADDRESS (`pssc_addr_t`) plus a context, because they
 * exist to route an access somewhere that is not this program's memory -- a
 * sequencer, a mock, a logger. This one exists for the case where the device IS
 * memory, and it carries neither: the generated driver forms the effective
 * address by following its register-layout struct, and hands the result
 * straight to a `volatile` load or store.
 *
 *     pssc_write32(&s->regs->int_msk_a, 25);
 *     uint32_t v = pssc_read32(&s->regs->csr);
 *
 * WHY A POINTER RATHER THAN AN ADDRESS. With the layout struct doing the
 * arithmetic there is no address to pass -- `&s->regs->bank[3].csr` is already
 * a pointer, and converting it to an integer only to convert it back would be
 * two casts around a value that was never anything else. It also gets the
 * compiler to check the arithmetic: a mistyped member is an error here and a
 * silently wrong offset in a hand-folded constant.
 *
 * WHY `pssc_` ON EVERY NAME. A bare `read32`/`write32` is exactly the name a
 * project's own code reaches for, and a generated header is not allowed to
 * cost its includer that name. Every other seam in this family already
 * prefixes its primitives (`pssc_r32`/`pssc_w32`); this one spells the prefix
 * out (`pssc_read32`/`pssc_write32`) because it is the one meant to be read
 * and called directly from hand-written driver code, not only from generated
 * bodies.
 *
 * WHY NO CONTEXT ARGUMENT. There is nothing to put in it. A `(void)s;` that
 * every primitive ignores is a parameter the caller has to have something to
 * pass, and on this target the caller has nothing.
 *
 * WHAT THIS COSTS. No hook for logging, backdoor access, or a simulated bus,
 * and no way to reach an address space wider than a pointer. Those are the
 * vtable seam's job and it still has it.
 *
 * ORDERING: each primitive ends (writes) or begins (reads) with
 * PSSC_MEM_BARRIER(), a no-op unless the platform defines it. `volatile` binds
 * the compiler and nothing else -- see pssc_mem.h for what that does and does
 * not buy you.
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

#include <stdint.h>

#ifndef PSSC_MEM_BARRIER
#  define PSSC_MEM_BARRIER() ((void)0)
#endif

static inline void     pssc_write8 (volatile void *p, uint8_t  d) { *(volatile uint8_t  *)p = d; PSSC_MEM_BARRIER(); }
static inline uint8_t  pssc_read8  (const volatile void *p)       { PSSC_MEM_BARRIER(); return *(const volatile uint8_t  *)p; }
static inline void     pssc_write16(volatile void *p, uint16_t d) { *(volatile uint16_t *)p = d; PSSC_MEM_BARRIER(); }
static inline uint16_t pssc_read16 (const volatile void *p)       { PSSC_MEM_BARRIER(); return *(const volatile uint16_t *)p; }
static inline void     pssc_write32(volatile void *p, uint32_t d) { *(volatile uint32_t *)p = d; PSSC_MEM_BARRIER(); }
static inline uint32_t pssc_read32 (const volatile void *p)       { PSSC_MEM_BARRIER(); return *(const volatile uint32_t *)p; }
static inline void     pssc_write64(volatile void *p, uint64_t d) { *(volatile uint64_t *)p = d; PSSC_MEM_BARRIER(); }
static inline uint64_t pssc_read64 (const volatile void *p)       { PSSC_MEM_BARRIER(); return *(const volatile uint64_t *)p; }

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
