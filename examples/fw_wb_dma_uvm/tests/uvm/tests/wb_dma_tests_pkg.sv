// ======================================================================
// Scenario sequences + tests for the wb_dma SPL UVM environment.
//
// Scenarios mirror packages/wb_dma/bench (Phase-1 subset):
//   * wb_dma_sw_copy_test  -- sw_dma1/2: single-channel block copy across the
//        four src/dst-select modes, swept tot_sz/chunk_sz, interrupt bank A/B.
// (arbitration + HW-handshake scenarios are added on top of this base.)
//
// One class per .svh: sequences first, then the base test, then the tests
// (each derived class after its base / its sequence).
// ======================================================================
`include "uvm_macros.svh"

package wb_dma_tests_pkg;
    import uvm_pkg::*;
    import wb_dma_env_pkg::*;

    localparam logic [31:0] SRC_BASE = 32'h0000_0000;
    localparam logic [31:0] DST_BASE = 32'h0000_4000;

    // sequences
    `include "wb_dma_sw_copy_seq.svh"
    `include "wb_dma_arb_seq.svh"
    `include "wb_dma_err_seq.svh"
    `include "wb_dma_hw_hs_seq.svh"
    `include "wb_dma_hol_seq.svh"
    `include "wb_dma_perf_seq.svh"
    `include "wb_dma_pause_arm_seq.svh"

    // base test + scenario tests. The name-check visitor precedes the base
    // test, which installs it.
    `include "wb_dma_name_check.svh"
    `include "wb_dma_base_test.svh"
    `include "wb_dma_sw_copy_test.svh"
    `include "wb_dma_pause_arm_test.svh"
    `include "wb_dma_arb_test.svh"
    `include "wb_dma_arb_eq_test.svh"
    `include "wb_dma_err_test.svh"
    `include "wb_dma_hw_hs_test.svh"
    `include "wb_dma_hol_test.svh"
    `include "wb_dma_perf_test.svh"
endpackage
