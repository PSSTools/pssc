/* The platform's register-access layer, as the HAL fixture's output expects
 * it: macros over the `<REG>_OFF`/`_STRIDE`/`_<F>_LSB`/`_<F>_MASK` the
 * generated `<prefix>_map.h` defines, and the status enum the model marks
 * external. Here the accesses go to the tracing memory (tb_mem.h). */
#ifndef DEV_ACCESS_H
#define DEV_ACCESS_H

#include <stdint.h>
#include "tb_mem.h"

typedef enum { STATUS_OK = 0, STATUS_TIMEOUT = 1 } status_e;

#define DEV_ADDR(base, REG, i) \
    ((uintptr_t)(base) + REG##_OFF + (uintptr_t)(i) * REG##_STRIDE)
#define DEV_READ32(base, REG, i) tb_read32(DEV_ADDR(base, REG, i))
#define DEV_WRITE32(base, REG, i, v) \
    tb_write32(DEV_ADDR(base, REG, i), (uint32_t)(v))
#define DEV_FIELD_SET(REG, F, x, v) \
    ((v) = ((v) & ~REG##_##F##_MASK) \
         | (((uint32_t)(x) << REG##_##F##_LSB) & REG##_##F##_MASK))
#define DEV_FIELD_GET(REG, F, v) \
    (((uint32_t)(v) & REG##_##F##_MASK) >> REG##_##F##_LSB)

#endif /* DEV_ACCESS_H */
