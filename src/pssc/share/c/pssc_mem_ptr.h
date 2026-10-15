/* pssc_mem_ptr.h -- the POINTER-DEREFERENCE memory seam.
 *
 * Was `pssc_mem_mmio.h`, which named a USE ("memory-mapped I/O") rather than a
 * MECHANISM. The mechanism is what varies: this seam forms a pointer from the
 * address and dereferences it, which is what makes it free and what makes it
 * unusable when the address space is wider than a pointer (see the assertion
 * below). `pssc_mem_mmio.h` remains as an including shim.
 *
 * When addr_handle_t is a real CPU address, the seam is just a `volatile`
 * load/store -- no user code, no function pointer, no call. As `static inline`
 * each primitive compiles to a single load/store instruction. There is no
 * import API to implement, so the generated <prefix>_create takes only the base.
 *
 * Caveat: the address space must be directly CPU-addressable, and there is no
 * hook for logging, backdoor, or a simulated bus -- use vtable/direct for that.
 *
 * ORDERING: each primitive ends (writes) or begins (reads) with
 * PSSC_MEM_BARRIER(), a no-op unless the platform defines it. `volatile` binds
 * the compiler and nothing else; see pssc_mem.h for what that does and does not
 * buy you.
 */
#ifndef PSSC_MEM_PTR_H
#define PSSC_MEM_PTR_H

/* Tells pssc_mem.h that a seam is already chosen, so its `--mem-access
 * selectable` block does not pull in a second one. */
#define PSSC_MEM_SEAM_CHOSEN 1
#include "pssc_mem.h"

/* Every primitive below ignores its context argument, so pssc_mem_reg.h may
 * call it with NULL (layout-struct register access). The vtable seam does not
 * set this, and pssc_mem_reg.h refuses to build without it. */
#define PSSC_MEM_SEAM_CONTEXT_FREE 1

/* THE ASSUMPTION THIS SEAM IS BUILT ON, checked rather than documented.
 *
 * `pssc_addr_t` is 64 bits because PSS `addr_handle_t` is, and this seam turns
 * one into a pointer. On a 32-bit target that cast TRUNCATES -- silently, with
 * no warning at any -W level, producing a driver that programs the low 4 GiB of
 * a device that is not there. This is the one place the width matters, so it is
 * the one place that says so.
 *
 * If you hit this: the address space is wider than the machine's pointers.
 * `--addr-bits 32` is the fix when the device map fits in 32 bits. Otherwise
 * use `--link-style vtable`, whose primitives and folded register accessors
 * take the address by value and never form a pointer from it. (The functions
 * seam takes it by value too, but its register access goes through the
 * layout struct -- pssc_mem_reg.h -- which needs the same width.)
 */
PSSC_STATIC_ASSERT(sizeof(pssc_addr_t) <= sizeof(uintptr_t),
                   pssc_addr_t_wider_than_a_pointer__use_addr_bits_32_or_vtable);

static inline void     pssc_w8 (const void *s, pssc_addr_t a, uint8_t  d) { (void)s; *(volatile uint8_t  *)(uintptr_t)a = d; PSSC_MEM_BARRIER(); }
static inline uint8_t  pssc_r8 (const void *s, pssc_addr_t a)             { (void)s; PSSC_MEM_BARRIER(); return *(volatile uint8_t  *)(uintptr_t)a; }
static inline void     pssc_w16(const void *s, pssc_addr_t a, uint16_t d) { (void)s; *(volatile uint16_t *)(uintptr_t)a = d; PSSC_MEM_BARRIER(); }
static inline uint16_t pssc_r16(const void *s, pssc_addr_t a)             { (void)s; PSSC_MEM_BARRIER(); return *(volatile uint16_t *)(uintptr_t)a; }
static inline void     pssc_w32(const void *s, pssc_addr_t a, uint32_t d) { (void)s; *(volatile uint32_t *)(uintptr_t)a = d; PSSC_MEM_BARRIER(); }
static inline uint32_t pssc_r32(const void *s, pssc_addr_t a)             { (void)s; PSSC_MEM_BARRIER(); return *(volatile uint32_t *)(uintptr_t)a; }
static inline void     pssc_w64(const void *s, pssc_addr_t a, uint64_t d) { (void)s; *(volatile uint64_t *)(uintptr_t)a = d; PSSC_MEM_BARRIER(); }
static inline uint64_t pssc_r64(const void *s, pssc_addr_t a)             { (void)s; PSSC_MEM_BARRIER(); return *(volatile uint64_t *)(uintptr_t)a; }

#endif /* PSSC_MEM_PTR_H */
