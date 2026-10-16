/* op_model_tb.c -- C5.1: the behavioural gate for the COMPONENT TREE driver.
 *
 * Drives the generated wb_dma_c API against op_model_mock and asserts a
 * register ACCESS TRACE. What that buys over the existing flat-model gate:
 *
 *   * PER-CHANNEL ADDRESS ARITHMETIC. The tree puts channel i's bank at
 *     base + 0x20 + 0x20*i, folded at generation time from the tree walk. The
 *     flat model has one bank, so nothing there can tell a correct offset from
 *     a zero one. Every assertion below names a channel other than 0.
 *   * THE WAIT LOOP ACTUALLY ITERATES. The mock reports DONE only after
 *     OM_PENDING_POLLS reads, so an operation model whose loop never runs is
 *     visible as a count, not as a hang.
 *   * THE ABORT PATH. stop_channel is the one operation that claims no
 *     `inflight` token and whose result is an ERROR rather than a clean stop.
 *
 * Prints WB_DMA OP MODEL PASS on success; on failure prints the failing check
 * AND the whole trace, because a wrong offset is unreadable without it.
 */
#include <stdio.h>
#include <string.h>

#include "wb_dma.h"
#include "op_model_mock.h"

#define BASE  0x40000000ull

/* Register offsets within a channel bank, from the generated accessors. */
#define R_CSR   0x00u
#define R_SZ    0x04u
#define R_ADR0  0x08u
#define R_AM0   0x0cu
#define R_ADR1  0x10u
#define R_AM1   0x14u

static om_mock_t   mock;
static int         failures;

static const pssc_mem_if BUS = {
    om_write8, om_read8, om_write16, om_read16,
    om_write32, om_read32, om_write64, om_read64, &mock
};

#define CHECK(cond, ...) do {                                   \
    if (!(cond)) { failures++;                                  \
        printf("FAIL %s:%d: ", __FILE__, __LINE__);             \
        printf(__VA_ARGS__); printf("\n"); }                    \
} while (0)

static wb_dma_ch_cfg_s a_config(void)
{
    wb_dma_ch_cfg_s c;
    memset(&c, 0, sizeof(c));
    c.src            = 0x1000u;
    c.dst            = 0x2000u;
    c.tot_sz         = 64u;
    c.inc_src        = 1;
    c.inc_dst        = 1;
    c.prio           = 1;
    c.int_on_done    = 1;
    return c;
}

/* --- the cases ---------------------------------------------------------- */

/* A transfer on a channel that is NOT channel 0, so every offset below is
 * wrong-by-default if the tree's base arithmetic is wrong. */
static void case_transfer_on_channel_2(wb_dma_c *dma)
{
    const int CH = 2;
    wb_dma_ch_cfg_s cfg = a_config();
    wb_dma_status_e st;
    unsigned n_csr_reads;

    om_init(&mock, BASE);
    wb_dma_init(dma, &BUS, BASE);

    st = wb_dma_ch_transfer_single(wb_dma_ch(dma, CH), &cfg);

    CHECK(st == WB_DMA_STATUS_WB_DMA_DONE, "transfer_single returned %d, expected DONE", st);

    /* The configuration landed in CHANNEL 2's bank, at base+0x60, and nowhere
     * else. `om_n_writes` on channel 0's ADR0 is the assertion that actually
     * fails when the per-channel base is dropped. */
    CHECK(om_n_writes(&mock, om_ch_reg(&mock, CH, R_ADR0)) >= 1,
          "no write to ch%d ADR0 (0x%llx)", CH,
          (unsigned long long)om_ch_reg(&mock, CH, R_ADR0));
    CHECK(om_last_write(&mock, om_ch_reg(&mock, CH, R_ADR0)) == cfg.src,
          "ch%d ADR0 = 0x%llx, expected 0x%x", CH,
          (unsigned long long)om_last_write(&mock, om_ch_reg(&mock, CH, R_ADR0)),
          cfg.src);
    CHECK(om_last_write(&mock, om_ch_reg(&mock, CH, R_ADR1)) == cfg.dst,
          "ch%d ADR1 wrong", CH);
    CHECK(om_last_write(&mock, om_ch_reg(&mock, CH, R_SZ)) == cfg.tot_sz,
          "ch%d SZ = %llu, expected %u", CH,
          (unsigned long long)om_last_write(&mock, om_ch_reg(&mock, CH, R_SZ)),
          cfg.tot_sz);

    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 0, R_ADR0)) == 0,
          "channel 0 was programmed while driving channel %d -- the "
          "per-channel base is not being applied", CH);
    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 1, R_ADR0)) == 0,
          "channel 1 was programmed while driving channel %d", CH);
    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 3, R_ADR0)) == 0,
          "channel 3 was programmed while driving channel %d", CH);

    /* CH_EN was set, and the wait loop really polled. */
    CHECK((om_last_write(&mock, om_ch_reg(&mock, CH, R_CSR)) & OM_CSR_CH_EN) != 0,
          "ch%d CSR write did not set CH_EN", CH);
    n_csr_reads = om_n_reads(&mock, om_ch_reg(&mock, CH, R_CSR));
    CHECK(n_csr_reads >= OM_PENDING_POLLS,
          "ch%d CSR read %u times, expected at least %d -- the wait loop did "
          "not iterate", CH, n_csr_reads, OM_PENDING_POLLS);
}

/* The same operation on every channel, so a base that is right for one and
 * wrong for the rest (a stride error rather than an offset error) is caught. */
static void case_every_channel_is_distinct(wb_dma_c *dma)
{
    wb_dma_ch_cfg_s cfg = a_config();
    int i;

    om_init(&mock, BASE);
    wb_dma_init(dma, &BUS, BASE);

    for (i = 0; i < OM_CH_COUNT; i++) {
        cfg.src = 0x1000u + (unsigned)i * 0x100u;
        (void)wb_dma_ch_transfer_single(wb_dma_ch(dma, i), &cfg);
    }
    for (i = 0; i < OM_CH_COUNT; i++)
        CHECK(om_last_write(&mock, om_ch_reg(&mock, i, R_ADR0))
                  == 0x1000u + (unsigned)i * 0x100u,
              "ch%d ADR0 = 0x%llx, expected 0x%x -- channel banks overlap", i,
              (unsigned long long)om_last_write(&mock, om_ch_reg(&mock, i, R_ADR0)),
              0x1000u + (unsigned)i * 0x100u);
}

/* stop_channel: an abort is reported as an ERROR, and it writes STOP to the
 * right channel. */
static void case_stop_is_an_error(wb_dma_c *dma)
{
    const int CH = 3;
    wb_dma_ch_cfg_s cfg = a_config();
    wb_dma_status_e st;

    om_init(&mock, BASE);
    wb_dma_init(dma, &BUS, BASE);

    wb_dma_ch_transfer_single_start(wb_dma_ch(dma, CH), &cfg);
    st = wb_dma_ch_stop_channel(wb_dma_ch(dma, CH));

    CHECK(st == WB_DMA_STATUS_WB_DMA_ERROR,
          "stop_channel returned %d, expected ERROR -- the device sets ERR on "
          "a STOP write and the contract carries that verbatim", st);
    CHECK((om_last_write(&mock, om_ch_reg(&mock, CH, R_CSR)) & OM_CSR_STOP) != 0,
          "ch%d CSR write did not set STOP", CH);
}

/* The engine-global block is at the base itself, NOT at a channel bank -- the
 * other half of the address arithmetic. */
static void case_engine_global_registers(wb_dma_c *dma)
{
    om_init(&mock, BASE);
    wb_dma_init(dma, &BUS, BASE);

    wb_dma_pause_engine(dma, 1);

    CHECK(om_n_reads(&mock, BASE) + om_n_writes(&mock, BASE) > 0,
          "pause_engine touched no engine-global CSR at 0x%llx",
          (unsigned long long)BASE);
    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 0, R_CSR)) == 0,
          "pause_engine wrote a CHANNEL CSR -- the engine block and the "
          "channel banks are being conflated");
}

/* A masked write is a read-modify-write (§21.14.1): the read is still there,
 * and it happens BEFORE the write. */
static void case_masked_write_reads_first(wb_dma_c *dma)
{
    const int CH = 1;
    uint64_t csr;
    int r, w;

    om_init(&mock, BASE);
    wb_dma_init(dma, &BUS, BASE);
    csr = om_ch_reg(&mock, CH, R_CSR);

    wb_dma_ch_set_auto_restart(wb_dma_ch(dma, CH), 1);

    r = om_find(&mock, csr, 0, 0);
    w = om_find(&mock, csr, 1, 0);
    CHECK(r >= 0, "set_auto_restart did not READ CHn_CSR -- a field write is a "
                  "read-modify-write, so the read must be there");
    CHECK(w >= 0, "set_auto_restart did not WRITE CHn_CSR");
    CHECK(r >= 0 && w >= 0 && r < w,
          "CHn_CSR was written before it was read (r=%d w=%d)", r, w);
    CHECK((om_last_write(&mock, csr) & OM_CSR_ARS) != 0,
          "set_auto_restart(1) did not set ARS");
}

int main(void)
{
    static wb_dma_c dma;

    case_transfer_on_channel_2(&dma);
    case_every_channel_is_distinct(&dma);
    case_stop_is_an_error(&dma);
    case_engine_global_registers(&dma);
    case_masked_write_reads_first(&dma);

    CHECK(mock.overflow == 0, "trace overflowed (%u lost)", mock.overflow);

    if (failures) {
        printf("\n%d check(s) failed; last trace follows\n", failures);
        om_dump(&mock);
        return 1;
    }
    printf("WB_DMA OP MODEL PASS\n");
    return 0;
}
