/* A tracing memory: every access printed as `R <addr> <data>` or
 * `W <addr> <data>`, so two builds of one API can be compared by their bus
 * traces. */
#ifndef TB_MEM_H
#define TB_MEM_H

#include <stdint.h>

uint32_t tb_read32(uintptr_t addr);
void tb_write32(uintptr_t addr, uint32_t data);
/* The next `reads` reads of `addr` return bit 0 set (busy), the rest clear. */
void tb_busy(uintptr_t addr, int reads);

#endif /* TB_MEM_H */
