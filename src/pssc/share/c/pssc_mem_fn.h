/* pssc_mem_fn.h -- the EXTERN-FUNCTION memory seam.
 *
 * Was `pssc_mem_direct.h`, which named a deployment ("direct / single DUT")
 * rather than a mechanism. `pssc_mem_direct.h` remains as an including shim.
 *
 * The address is passed BY VALUE and never becomes a pointer, so unlike
 * pssc_mem_ptr.h these primitives are correct on a target whose pointers are
 * narrower than `pssc_addr_t`. Register access is not: a generated driver
 * reaches its registers through the layout struct (pssc_mem_reg.h), which
 * forms the address as a pointer before handing it here, and asserts the
 * width for that reason.
 *
 * The seam inlines over user-supplied extern functions linked by name. Zero
 * per-instance storage, direct symbol linkage. Cost: one bus implementation per
 * link unit -- you cannot drive two different buses at once. The `self` argument
 * is ignored (there is no per-instance bus).
 */
#ifndef PSSC_MEM_FN_H
#define PSSC_MEM_FN_H

/* See pssc_mem_ptr.h for what this marker is for. */
#define PSSC_MEM_SEAM_CHOSEN 1
#include "pssc_mem.h"

/* Every primitive below ignores its context argument, so pssc_mem_reg.h may
 * call it with NULL (layout-struct register access). The vtable seam does not
 * set this, and pssc_mem_reg.h refuses to build without it. */
#define PSSC_MEM_SEAM_CONTEXT_FREE 1

/* User-provided -- implement these and link them in. */
void     pssc_mem_write8 (pssc_addr_t a, uint8_t  d);
uint8_t  pssc_mem_read8  (pssc_addr_t a);
void     pssc_mem_write16(pssc_addr_t a, uint16_t d);
uint16_t pssc_mem_read16 (pssc_addr_t a);
void     pssc_mem_write32(pssc_addr_t a, uint32_t d);
uint32_t pssc_mem_read32 (pssc_addr_t a);
void     pssc_mem_write64(pssc_addr_t a, uint64_t d);
uint64_t pssc_mem_read64 (pssc_addr_t a);

static inline void     pssc_w8 (const void *s, pssc_addr_t a, uint8_t  d) { (void)s; pssc_mem_write8 (a, d); }
static inline uint8_t  pssc_r8 (const void *s, pssc_addr_t a)             { (void)s; return pssc_mem_read8 (a); }
static inline void     pssc_w16(const void *s, pssc_addr_t a, uint16_t d) { (void)s; pssc_mem_write16(a, d); }
static inline uint16_t pssc_r16(const void *s, pssc_addr_t a)             { (void)s; return pssc_mem_read16(a); }
static inline void     pssc_w32(const void *s, pssc_addr_t a, uint32_t d) { (void)s; pssc_mem_write32(a, d); }
static inline uint32_t pssc_r32(const void *s, pssc_addr_t a)             { (void)s; return pssc_mem_read32(a); }
static inline void     pssc_w64(const void *s, pssc_addr_t a, uint64_t d) { (void)s; pssc_mem_write64(a, d); }
static inline uint64_t pssc_r64(const void *s, pssc_addr_t a)             { (void)s; return pssc_mem_read64(a); }

#endif /* PSSC_MEM_FN_H */
