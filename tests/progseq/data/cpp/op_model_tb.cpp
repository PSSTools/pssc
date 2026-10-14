// op_model_tb.cpp -- the behavioural gate for the C++ COMPONENT TREE driver.
//
// The C++ counterpart of data/c/op_model_tb.c, and deliberately the SAME five
// cases against the SAME mock (data/c/op_model_mock.c, compiled as C and linked
// in). That is what makes this more than a second smoke test: the two backends
// project one model into two languages, so if they are both right they issue
// the same register accesses, and any case where they differ is a defect in one
// of them.
//
// What this gates that no structural test can:
//
//   * PER-CHANNEL ADDRESS ARITHMETIC. Channel i's bank is at
//     base + 0x20 + 0x20*i, folded at generation time from the tree walk. Every
//     assertion below names a channel other than 0.
//   * THE WAIT LOOP ACTUALLY ITERATES. The mock reports DONE only after
//     OM_PENDING_POLLS reads.
//   * THE ABORT PATH. stop_channel claims no `inflight` token and returns ERROR.
//   * THAT A MASKED WRITE STILL READS FIRST (PSS 3.1 §21.14.1).
//
// Prints WB_DMA CPP OP MODEL PASS on success; on failure prints the failing
// check and the whole trace, because a wrong offset is unreadable without it.
#include <cstdio>

extern "C" {
#include "op_model_mock.h"
}

#include "wb_dma.hpp"

#define BASE 0x40000000ull

// Register offsets within a channel bank.
#define R_CSR  0x00u
#define R_SZ   0x04u
#define R_ADR0 0x08u
#define R_ADR1 0x10u

static om_mock_t mock;
static int       failures;

#define CHECK(cond, ...) do {                                   \
    if (!(cond)) { failures++;                                  \
        std::printf("FAIL %s:%d: ", __FILE__, __LINE__);        \
        std::printf(__VA_ARGS__); std::printf("\n"); }          \
} while (0)

// The platform side of the seam. `wb_dma_import_if` is `pssc::mem_if` for this
// model (it declares no imports); forwarding to the C mock is all it takes.
struct mock_bus : wb_dma::wb_dma_import_if {
    void          write8 (pssc::addr_t a, std::uint8_t  d) override { om_write8 (&mock, a, d); }
    std::uint8_t  read8  (pssc::addr_t a) override { return om_read8 (&mock, a); }
    void          write16(pssc::addr_t a, std::uint16_t d) override { om_write16(&mock, a, d); }
    std::uint16_t read16 (pssc::addr_t a) override { return om_read16(&mock, a); }
    void          write32(pssc::addr_t a, std::uint32_t d) override { om_write32(&mock, a, d); }
    std::uint32_t read32 (pssc::addr_t a) override { return om_read32(&mock, a); }
    void          write64(pssc::addr_t a, std::uint64_t d) override { om_write64(&mock, a, d); }
    std::uint64_t read64 (pssc::addr_t a) override { return om_read64(&mock, a); }
};

// The platform's other obligation: four operations call `message(...)`.
namespace pssc { void message(const char *, ...) {} }

static wb_dma::wb_dma_ch_cfg_s a_config()
{
    wb_dma::wb_dma_ch_cfg_s c{};
    c.src         = 0x1000u;
    c.dst         = 0x2000u;
    c.tot_sz      = 64u;
    c.inc_src     = 1;
    c.inc_dst     = 1;
    c.prio        = 1;
    c.int_on_done = 1;
    return c;
}

// --- the cases --------------------------------------------------------------

// A transfer on a channel that is NOT channel 0, so every offset below is
// wrong-by-default if the tree's base arithmetic is wrong.
static void case_transfer_on_channel_2(mock_bus &bus)
{
    const int CH = 2;
    om_init(&mock, BASE);
    auto dma = wb_dma::wb_dma_c::create(bus, BASE);
    auto cfg = a_config();

    wb_dma::wb_dma_status_e st = dma->ch(CH).transfer_single(cfg);

    CHECK(st == wb_dma::wb_dma_status_e::WB_DMA_DONE,
          "transfer_single returned %d, expected DONE", int(st));

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
          "ch%d SZ wrong", CH);

    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 0, R_ADR0)) == 0,
          "channel 0 was programmed while driving channel %d -- the "
          "per-channel base is not being applied", CH);
    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 1, R_ADR0)) == 0,
          "channel 1 was programmed while driving channel %d", CH);
    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 3, R_ADR0)) == 0,
          "channel 3 was programmed while driving channel %d", CH);

    CHECK((om_last_write(&mock, om_ch_reg(&mock, CH, R_CSR)) & OM_CSR_CH_EN) != 0,
          "ch%d CSR write did not set CH_EN", CH);
    unsigned n = om_n_reads(&mock, om_ch_reg(&mock, CH, R_CSR));
    CHECK(n >= OM_PENDING_POLLS,
          "ch%d CSR read %u times, expected at least %d -- the wait loop did "
          "not iterate", CH, n, OM_PENDING_POLLS);
}

// The same operation on every channel, so a base that is right for one and
// wrong for the rest (a stride error rather than an offset error) is caught.
static void case_every_channel_is_distinct(mock_bus &bus)
{
    om_init(&mock, BASE);
    auto dma = wb_dma::wb_dma_c::create(bus, BASE);
    auto cfg = a_config();

    for (int i = 0; i < OM_CH_COUNT; i++) {
        cfg.src = 0x1000u + unsigned(i) * 0x100u;
        (void)dma->ch(std::size_t(i)).transfer_single(cfg);
    }
    for (int i = 0; i < OM_CH_COUNT; i++)
        CHECK(om_last_write(&mock, om_ch_reg(&mock, i, R_ADR0))
                  == 0x1000u + unsigned(i) * 0x100u,
              "ch%d ADR0 = 0x%llx, expected 0x%x -- channel banks overlap", i,
              (unsigned long long)om_last_write(&mock, om_ch_reg(&mock, i, R_ADR0)),
              0x1000u + unsigned(i) * 0x100u);
}

// stop_channel: an abort is reported as an ERROR, and it writes STOP to the
// right channel.
static void case_stop_is_an_error(mock_bus &bus)
{
    const int CH = 3;
    om_init(&mock, BASE);
    auto dma = wb_dma::wb_dma_c::create(bus, BASE);
    auto cfg = a_config();

    dma->ch(CH).transfer_single_start(cfg);
    wb_dma::wb_dma_status_e st = dma->ch(CH).stop_channel();

    CHECK(st == wb_dma::wb_dma_status_e::WB_DMA_ERROR,
          "stop_channel returned %d, expected ERROR -- the device sets ERR on "
          "a STOP write and the contract carries that verbatim", int(st));
    CHECK((om_last_write(&mock, om_ch_reg(&mock, CH, R_CSR)) & OM_CSR_STOP) != 0,
          "ch%d CSR write did not set STOP", CH);
}

// The engine-global block is at the base itself, NOT at a channel bank.
static void case_engine_global_registers(mock_bus &bus)
{
    om_init(&mock, BASE);
    auto dma = wb_dma::wb_dma_c::create(bus, BASE);

    dma->pause_engine(true);

    CHECK(om_n_reads(&mock, BASE) + om_n_writes(&mock, BASE) > 0,
          "pause_engine touched no engine-global CSR at 0x%llx",
          (unsigned long long)BASE);
    CHECK(om_n_writes(&mock, om_ch_reg(&mock, 0, R_CSR)) == 0,
          "pause_engine wrote a CHANNEL CSR -- the engine block and the "
          "channel banks are being conflated");
}

// A masked write is a read-modify-write (§21.14.1): the read is still there,
// and it happens BEFORE the write.
static void case_masked_write_reads_first(mock_bus &bus)
{
    const int CH = 1;
    om_init(&mock, BASE);
    auto dma = wb_dma::wb_dma_c::create(bus, BASE);
    std::uint64_t csr = om_ch_reg(&mock, CH, R_CSR);

    dma->ch(CH).set_auto_restart(true);

    int r = om_find(&mock, csr, 0, 0);
    int w = om_find(&mock, csr, 1, 0);
    CHECK(r >= 0, "set_auto_restart did not READ CHn_CSR -- a field write is a "
                  "read-modify-write, so the read must be there");
    CHECK(w >= 0, "set_auto_restart did not WRITE CHn_CSR");
    CHECK(r >= 0 && w >= 0 && r < w,
          "CHn_CSR was written before it was read (r=%d w=%d)", r, w);
    CHECK((om_last_write(&mock, csr) & OM_CSR_ARS) != 0,
          "set_auto_restart(1) did not set ARS");
}

int main()
{
    mock_bus bus;

    case_transfer_on_channel_2(bus);
    case_every_channel_is_distinct(bus);
    case_stop_is_an_error(bus);
    case_engine_global_registers(bus);
    case_masked_write_reads_first(bus);

    CHECK(mock.overflow == 0, "trace overflowed (%u lost)", mock.overflow);

    if (failures) {
        std::printf("\n%d check(s) failed; last trace follows\n", failures);
        om_dump(&mock);
        return 1;
    }
    std::printf("WB_DMA CPP OP MODEL PASS\n");
    return 0;
}
