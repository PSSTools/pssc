/* pssc's function seam (`--link-style direct`, pssc_mem_fn.h) over the
 * tracing memory: how the op-model-c baseline reaches the same bus. */
#include "pssc_mem_fn.h"
#include "tb_mem.h"

uint32_t pssc_mem_read32(pssc_addr_t a) { return tb_read32((uintptr_t)a); }
void pssc_mem_write32(pssc_addr_t a, uint32_t d) { tb_write32((uintptr_t)a, d); }
