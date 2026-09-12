/* op_model_mock.h -- a WB DMA mock with the register semantics the OPERATION
 * MODEL actually depends on, plus an access trace.
 *
 * Distinct from dma_mock.h, which serves the FLAT example model
 * (`src/pssc/testing/models`). That mock cannot gate the component
 * tree, because the flat model has one register bank and the tree has five: an
 * engine-global block plus four per-channel banks at base + 0x20 + 0x20*i. The
 * per-channel base arithmetic is generated from the tree walk and is exercised
 * by nothing else, so a mock that answers every address identically would let a
 * driver that programmed channel 0 four times pass.
 *
 * Hence the TRACE. `exit(0)` proves the driver ran; the trace proves it talked
 * to the right registers, in the right order, with the right values -- which is
 * the only thing that distinguishes a working driver from one whose offsets are
 * all zero.
 *
 * WHAT IS MODELLED (§3.2 of the operation model, and no more):
 *
 *   * CHn_CSR.ch_en written 1 -> the channel becomes busy, and completes after
 *     PENDING_POLLS subsequent reads of that channel's CSR. Not instantly:
 *     an operation model whose wait loop never iterates has not been tested.
 *   * CHn_CSR.stop written 1 -> err=1, busy=0. An abort is reported as an
 *     ERROR, not as a clean stop -- the device's behaviour, carried into the
 *     contract verbatim.
 *   * Reading CHn_CSR CLEARS done/err and the interrupt source bits. This is
 *     the read-to-clear the model warns about at length, and modelling it is
 *     what makes "who consumes the completion" observable at all.
 *   * Every other register is plain storage.
 */
#ifndef OP_MODEL_MOCK_H
#define OP_MODEL_MOCK_H

#include <stdint.h>

#define OM_REGWORDS     256          /* register space: base .. base+0x400 */
#define OM_TRACE_CAP    512
#define OM_PENDING_POLLS 2           /* CSR reads before DONE appears */

/* CHn_CSR bit positions, from the generated wb_dma_csr_t. Restated here rather
 * than included, deliberately: if the RDL moves a bit, this mock keeps the old
 * position and the gate FAILS. A mock that imported the same header as the
 * driver would move with it and agree with a wrong driver forever. */
#define OM_CSR_CH_EN    (1u <<  0)
#define OM_CSR_ARS      (1u <<  6)
#define OM_CSR_STOP     (1u <<  9)
#define OM_CSR_BUSY     (1u << 10)
#define OM_CSR_DONE     (1u << 11)
#define OM_CSR_ERR      (1u << 12)
#define OM_CSR_INT_ERR  (1u << 20)
#define OM_CSR_INT_DONE (1u << 21)

/* The tree's geometry. The mock must know it to model per-channel state; the
 * DRIVER must derive it from the model. That they agree is the point. */
#define OM_CH_COUNT     4
#define OM_CH_BASE      0x20u        /* first channel bank, from engine base */
#define OM_CH_STRIDE    0x20u

typedef struct {
    uint8_t  is_write;
    uint8_t  width;                  /* 8/16/32/64 */
    uint64_t addr;
    uint64_t data;
} om_access_t;

typedef struct {
    uint64_t base;
    uint32_t regmem[OM_REGWORDS];
    int      polls[OM_CH_COUNT];     /* CSR reads since ch_en */
    uint8_t  running[OM_CH_COUNT];
    om_access_t trace[OM_TRACE_CAP];
    unsigned n_trace;
    unsigned overflow;               /* trace lost -- a silent truncation would
                                      * make an incomplete driver look correct */
} om_mock_t;

void     om_init(om_mock_t *m, uint64_t base);

/* Bus primitives, matching the pssc_mem_if vtable signature. */
void     om_write8 (void *ctx, uint64_t a, uint8_t  d);
uint8_t  om_read8  (void *ctx, uint64_t a);
void     om_write16(void *ctx, uint64_t a, uint16_t d);
uint16_t om_read16 (void *ctx, uint64_t a);
void     om_write32(void *ctx, uint64_t a, uint32_t d);
uint32_t om_read32 (void *ctx, uint64_t a);
void     om_write64(void *ctx, uint64_t a, uint64_t d);
uint64_t om_read64 (void *ctx, uint64_t a);

/* --- trace queries, used by the testbench's assertions --- */

/*: address of channel `ch`'s register `off`, as the MOCK computes it. */
uint64_t om_ch_reg(om_mock_t *m, int ch, unsigned off);

/*: number of writes to `addr`. */
unsigned om_n_writes(om_mock_t *m, uint64_t addr);

/*: number of reads of `addr`. */
unsigned om_n_reads(om_mock_t *m, uint64_t addr);

/*: value of the last write to `addr`, or 0 if there was none. */
uint64_t om_last_write(om_mock_t *m, uint64_t addr);

/*: index of the first access to `addr` at or after `from`, or -1. */
int      om_find(om_mock_t *m, uint64_t addr, uint8_t is_write, unsigned from);

void     om_dump(om_mock_t *m);

#endif /* OP_MODEL_MOCK_H */
