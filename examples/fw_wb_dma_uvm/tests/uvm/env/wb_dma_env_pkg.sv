// ======================================================================
// wb_dma_env_pkg -- the complete UVM + fw-hdl environment for the wb_dma SPL
// class model, in one package.
//
// This is the whole test-side stack: the fw-hdl "DUT" flavours, the always-on
// SPL reference + comparison currency, and the UVM env/scoreboard/sequences.
// The flavour-specific pieces (three model views + three env views) all live
// here as classes; each hvl/hdl top SELECTS its flavour (which model root it
// news, which env it factory-overrides in, which vifs it seats) -- see
// tb/hvl_top_spl*.sv and tb/hvl_top_rtl.sv.
//
// The contents form a single linear dependency chain; the includes below are in
// that topological order:
//
//   model    -- fw-hdl class "DUT": two fw_mem_if memories, an interrupt-cause
//               collector, per-channel HW-handshake devices, the abstract model
//               base, and the TLM (method-driven) engine flavour.
//   ref      -- the passive SPL reference used by the SPL<->RTL comparison
//               harness: the canonical master-transaction record, the wb_proto_if
//               taps, and the tapped reference model.
//   uvm      -- the register-access agent (fwvip-wb initiator over the model's
//               wb_proto_if seam), scoreboard, comparator + monitor adapters,
//               the env, and the register/CSR sequence helpers.
//   wb       -- the Wishbone signal-level flavour: engine inside wb_dma_spl driven
//               over transactors, GPIO-observed interrupts, the augmented envs.
//   rtl      -- the OpenCores wb_dma_top flavour: host register path reused from
//               the WB flavour, data memories on combinational SV RAMs.
//
// One class per .svh, no `ifndef guards (the project convention); the package is
// compiled once into the shared env SimLib. Scenario sequences/tests live in
// wb_dma_tests_pkg.
// ======================================================================
`include "wb_dma_spl_macros.svh"   // FW_WB_PROTO_IMP / FW_WB_DMA_IRQ_IMP / FW_WB_DMA_HS_IMP
`include "uvm_macros.svh"

package wb_dma_env_pkg;
    import uvm_pkg::*;
    import fw_hdl_pkg::*;
    import fw_std_pkg::*;
    import fw_proto_wb_pkg::*;      // wb_proto_if + xtor bridges
    import fw_proto_wb_spl_pkg::*;  // wb_mem_* adapters, wb_proto_client_export
    import wb_dma_spl_pkg::*;       // wb_dma engine + wb_dma_csr_t / _sz_t
    import fwvip_wb_pkg::*;         // fwvip_wb initiator/target/monitor agents
    import fwvip_gpio_pkg::*;       // fwvip_gpio_monitor + fwvip_gpio_transaction
    // The operation model generated from src/pss by `pss.op-model-sv`:
    // wb_dma_c / wb_dma_ch_c_exp_if, the wb_dma_ch_cfg_s config struct and
    // wb_dma_status_e, plus addr_handle_t and the reg_c runtime they build on.
    // No name collides with wb_dma_reg_pkg (imported via wb_dma_spl_pkg): the
    // two projections of src/rdl suffix their value types differently -- `_t`
    // from peakrdl-fw-hdl, `_s` from peakrdl-pss.
    import pssc_reg_pkg::*;
    import wb_dma_c_pkg::*;
    // pssc-debug: the pyvm engine behind `+pss_impl=vm` (see the adapter),
    // or, in the `native` build (`PSSVM_DPI`), the C engine behind
    // `+pss_impl=native`, which pssvm_pkg holds itself.
    import pssvm_pkg::*;
`ifndef PSSVM_DPI
    import pssvm_hif_pkg::*;
`endif
    // Re-exported, following the same convention wb_dma_spl_pkg uses for the
    // register model: the scenario sequences in wb_dma_tests_pkg name
    // wb_dma_status_e and the WB_DMA_* enum items, and a wildcard import does
    // not make an imported name visible to the importer's own importers.
    export pssc_reg_pkg::*;
    export wb_dma_c_pkg::*;

    // -----------------------------------------------------------------------
    // Shared module-level declarations, visible to every include below.
    // -----------------------------------------------------------------------

    // The model's 32-bit Wishbone edge -- the abstract BFM type used both as the
    // engine register slave and as the data-memory access API. Matches the RTL's
    // boundary, so the UVM env drives all three DUT flavours through one seam.
    typedef wb_proto_if #(32, 32) wb_dma_mem_if_t;

    // Wall-clock seconds from /proc/uptime (monotonic since boot, ~10 ms
    // resolution; DPI-free). Used by the tops and the perf test to measure
    // wall-clock runtime -- the delta between two calls is elapsed real time.
    function automatic real wc_now();
        int  fd;
        real up;
        up = 0.0;
        fd = $fopen("/proc/uptime", "r");
        if (fd == 0) return 0.0;
        void'($fscanf(fd, "%f", up));
        $fclose(fd);
        return up;
    endfunction

    // The hand-written register map that used to sit here -- REG_COR,
    // REG_INT_MASK{A,B}, REG_INT_SRC{A,B}, the CH_*(c) offset functions, and
    // mk_csr()/mk_sz() -- is GONE. It was a fourth statement of a map that
    // src/rdl already owns, sourced from the RTL's defines rather than from the
    // RDL, and nothing kept it honest: moving a field would have left these
    // programming the old layout while both generated projections followed.
    //
    // Stimulus now goes through the operation model generated from src/pss
    // (imported above), so the map reaches the tests as generated code. The one
    // place the testbench still needs to KNOW an address -- deciding whether a
    // read was a channel CSR, for completion accounting -- derives it from the
    // generated register group instead of restating it; see
    // wb_dma_pss_mem_adapter's constructor.
    //
    // docs/uvm-op-layer-adoption.md §1, §5, §6.3.

    // ---- model: fw-hdl class "DUT" (no UVM dependency in these classes) -------
    `include "wb_dma_mem.svh"
    `include "wb_dma_irq_collector.svh"
    `include "wb_dma_hs_device.svh"
    `include "wb_dma_model_base.svh"
    `include "wb_dma_tlm_model.svh"

    // ---- ref: always-on passive SPL reference + comparison currency -----------
    `include "wb_dma_xact.svh"
    `include "wb_dma_xact_tap.svh"
    `include "wb_dma_ref_model.svh"

    // ---- uvm: register agent, scoreboard, comparator, env, sequences ----------
    `include "wb_dma_scoreboard.svh"
    `include "wb_dma_reg_tee.svh"
    `include "wb_dma_comparator.svh"
    `include "wb_dma_mon_adapter.svh"
    `include "wb_dma_reg_forward.svh"
    // Before the env (which builds one) and before the base sequence (which
    // binds itself to one). It forward-declares wb_dma_base_seq, since the
    // dependency is mutual.
    `include "wb_dma_pss_mem_adapter.svh"
    `include "wb_dma_env.svh"
    `include "wb_dma_base_seq.svh"

    // ---- wb: Wishbone signal-level flavour (engine inside wb_dma_spl) ----------
    `include "wb_dma_wb_model.svh"
    `include "wb_dma_int_gpio_sub.svh"
    `include "wb_dma_wb_env.svh"
    `include "wb_dma_wbxtor_env.svh"

    // ---- rtl: OpenCores wb_dma_top flavour (data memories on SV RAMs) ----------
    `include "wb_dma_ram_mem.svh"
    `include "wb_dma_rtl_model.svh"
endpackage
