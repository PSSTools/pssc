// HVL top for the SIGNAL-LEVEL (Wishbone) UVM path. The DUT is the wb_dma_spl
// wrapper (the SPL model behind Wishbone pins), instanced in hdl_top_spl_wb. The
// SAME UVM env + tests as the TLM top run here -- only the "model" published to
// the config_db differs: a wb_dma_wb_model whose register BFM drives a Wishbone
// initiator into the DUT and whose data memories back the DUT's master ports
// through Wishbone target transactors. Interrupts are reconstructed from INT_SRC
// register reads plus a GPIO monitor on the DUT's inta_o/intb_o pins, which feeds
// the model's int_changed() seam -- the same seam the TLM model drives from
// rf.signal_int, so the bench waits are identical across paths.
//
// This top instances hdl_top_spl_wb (named after the type, emulation convention)
// and reaches its transactor virtual interfaces + GPIO monitor hierarchically to
// seat them into the bus-side model. Only the non-handshake scenarios (sw_copy,
// arb) run here; the per-channel HS bus is stubbed in wb_dma_spl (hw_hs/hol stay
// on the TLM top, hvl_top_spl).
`include "uvm_macros.svh"
`include "fwvip_gpio_macros.svh"

module hvl_top_spl_wb;
    import uvm_pkg::*;
    import fw_hdl_pkg::*;
    import fw_proto_wb_pkg::*;
    import fwvip_wb_pkg::*;
    import fwvip_gpio_pkg::*;
    import wb_dma_env_pkg::*;
    import wb_dma_tests_pkg::*;

    // Signal-level DUT + transactors live in hdl_top_spl_wb.
    hdl_top_spl_wb hdl_top_spl_wb();

    // Waveform dump, gated by the +trace runtime plusarg (image must be built
    // trace-enabled, e.g. the `debug` config's SimElabArgsDbg). The run args
    // pass +trace=waves.<fmt> so the file name matches the built format;
    // a bare +trace falls back to waves.fst. Dumping from this top captures the
    // whole hierarchy, including the DUT in hdl_top_spl_wb.
    initial begin
        string tracefile;
        if ($value$plusargs("trace=%s", tracefile)) begin
            $dumpfile(tracefile);
            $dumpvars(0, hvl_top_spl_wb);
        end else if ($test$plusargs("trace")) begin
            $dumpfile("waves.fst");
            $dumpvars(0, hvl_top_spl_wb);
        end
    end

    // Wall-clock (real seconds) at startup / finish -- for throughput measurement.
    real wc_start;

    // Always-on passive reference (a TLM model), elaborated for functional runs.
    fw_component_root #(wb_dma_ref_model) refroot;

    initial begin
        automatic fw_component_root #(wb_dma_wb_model) root = new("root");
        automatic fw_clock_xtor_bridge clk_dom;
        automatic fw_clock_xtor_bridge ref_clk;
        automatic bit perf = $test$plusargs("PERF");
        wc_start = wc_now();
        $display("[PERF] %m START  wallclock=%0.3f s", wc_start);
        // Seat the host initiator vif before start() (build() needs vhost). The
        // master-port target xtors are handed to the fwvip_wb_target agents below.
        root.vhost = hdl_top_spl_wb.u_host.u_if;
        clk_dom = new("clock", root, hdl_top_spl_wb.u_clk);
        root.clock.connect(clk_dom);         // seat the root clock domain
        // Observable events: wired unconditionally, active only under +fw_dbg.
        // Attached to the DUT root ONLY -- the always-on reference runs the same
        // engine, and two interleaved copies of every event is not a trace.
        void'(fw_dbg_console::attach(root, "dut"));
        root.start();                        // build -> connect -> run

        // Always-on passive reference (skipped for perf: 2x engine work skews it).
        if (!perf) begin
            refroot = new("refroot");
            ref_clk = new("ref_clock", refroot, hdl_top_spl_wb.u_clk);
            refroot.clock.connect(ref_clk);
            refroot.start();
            uvm_config_db #(wb_dma_ref_model)::set(null, "*", "ref_model", refroot);
            // Seat the three comparison monitors' vifs (WB0/WB1 master + register).
            fwvip_wb_monitor_config_p #(virtual wb_monitor_xtor_if #(32, 32))::set(
                null, "uvm_test_top.env.m_mon_m0*",  "cfg", hdl_top_spl_wb.u_mon_m0.u_if);
            fwvip_wb_monitor_config_p #(virtual wb_monitor_xtor_if #(32, 32))::set(
                null, "uvm_test_top.env.m_mon_m1*",  "cfg", hdl_top_spl_wb.u_mon_m1.u_if);
            fwvip_wb_monitor_config_p #(virtual wb_monitor_xtor_if #(32, 32))::set(
                null, "uvm_test_top.env.m_mon_reg*", "cfg", hdl_top_spl_wb.u_mon_reg.u_if);
        end

        // Bind the interrupt-pin GPIO monitor + swap in the WB-xtor env (GPIO
        // monitor + the two fwvip_wb_target memory agents).
        `fwvip_gpio_monitor_register(2, hdl_top_spl_wb.u_mon_int.u_if, "uvm_test_top.env.m_int*")
        wb_dma_env::type_id::set_type_override(wb_dma_wbxtor_env::get_type());

        // No sim time before run_test(): reset is released by the concurrent
        // initial in hdl_top_spl_wb; the first register access blocks on the bus
        // until then.
        uvm_config_db #(wb_dma_model_base)::set(null, "*", "model", root);
        // Bind the fwvip-wb initiator to the model's wb_proto_if register port
        // (a WB initiator bridge over the host xtor -> real WB cycles into WB0).
        fwvip_wb_initiator_config_ap #(32, 32)::set(
            null, "uvm_test_top.env.m_init*", "cfg", root.rf_if());
        // Back the DUT's master ports with the two fwvip_wb_target agents: each
        // config holds the master-port target xtor vif + the registered wb_proto_if
        // responder (s0.m / s1.m); its converter polls the xtor and drives access().
        fwvip_wb_target_config_ap #(32, 32)::set(
            null, "uvm_test_top.env.m_mem0*", "cfg", hdl_top_spl_wb.u_mem0.u_if, root.s0.m);
        fwvip_wb_target_config_ap #(32, 32)::set(
            null, "uvm_test_top.env.m_mem1*", "cfg", hdl_top_spl_wb.u_mem1.u_if, root.s1.m);
        run_test();
    end

    // Sim-time watchdog (skipped for +PERF runs, which are bounded by wall-clock).
    initial begin
        if (!$test$plusargs("PERF")) begin
            #5ms;
            $fatal(1, "[hvl_top_spl_wb] TIMEOUT");
        end
    end

    final begin
        real wc_end;
        wc_end = wc_now();
        $display("[PERF] %m FINISH wallclock=%0.3f s  ELAPSED=%0.3f s", wc_end, wc_end - wc_start);
    end
    // The site catalog + field schema, on +fw_dbg_catalog. At END of run:
    // a site's schema is written on its first activation, not at elaboration.
    final fw_dbg_console::dump_catalog();

endmodule
