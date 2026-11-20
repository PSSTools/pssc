// HVL top for the RTL signal-level UVM path. The DUT is the OpenCores wb_dma_top
// Verilog RTL, instanced in hdl_top_rtl. The SAME UVM env + tests as the TLM and
// SPL-wb tops run here -- only the DUT and its memory backing differ. The model
// published to the config_db is a wb_dma_rtl_model: its register BFM drives a
// Wishbone initiator into the DUT (register access = real WB cycles), its two
// data memories are combinational 0-wait-state SV RAMs (wb_ram_slv) that the DUT
// masters into DIRECTLY, and interrupts are observed off the DUT's inta_o/intb_o
// PINS via a fwvip-gpio monitor feeding the model's int_changed() seam.
//
// This top instances hdl_top_rtl (named after the type, emulation convention) and
// reaches its host initiator vif, the two RAM vifs, and the GPIO monitor vif
// hierarchically to seat them into the bus-side model.
`include "uvm_macros.svh"
`include "fwvip_gpio_macros.svh"

module hvl_top_rtl;
    import uvm_pkg::*;
    import fw_hdl_pkg::*;
    import fw_proto_wb_pkg::*;
    import fwvip_wb_pkg::*;
    import fwvip_gpio_pkg::*;
    import wb_dma_env_pkg::*;
    import wb_dma_tests_pkg::*;

    // Signal-level DUT + host xtor + RAMs + interrupt tap live in hdl_top_rtl.
    hdl_top_rtl hdl_top_rtl();

    // Wall-clock (real seconds) at startup / finish -- for throughput measurement.
    real wc_start;

    // Always-on passive SPL reference (a TLM model), elaborated for functional runs.
    fw_component_root #(wb_dma_ref_model) refroot;

    initial begin
        automatic fw_component_root #(wb_dma_rtl_model) root = new("root");
        automatic fw_clock_xtor_bridge clk_dom;
        automatic fw_clock_xtor_bridge ref_clk;
        automatic bit perf = $test$plusargs("PERF");
        wc_start = wc_now();
        $display("[PERF] %m START  wallclock=%0.3f s", wc_start);
        // Seat the host initiator vif + the two RAM vifs before start().
        root.vhost  = hdl_top_rtl.u_host.u_if;
        root.vram0  = hdl_top_rtl.u_ram0;
        root.vram1  = hdl_top_rtl.u_ram1;
        clk_dom = new("clock", root, hdl_top_rtl.u_clk);
        root.clock.connect(clk_dom);         // seat the root clock domain
        root.start();                        // build -> connect -> run
        // No memory-service fork: the wb_ram_slv RAMs serve the master ports directly.

        // Always-on passive SPL reference (skipped for perf: 2x work skews it). This
        // is the real cross-abstraction check -- SPL model vs OpenCores RTL.
        if (!perf) begin
            refroot = new("refroot");
            ref_clk = new("ref_clock", refroot, hdl_top_rtl.u_clk);
            refroot.clock.connect(ref_clk);
            // On this top the DUT is the OpenCores RTL, which has no fw-hdl
            // sites at all -- so the observable stream comes from the passive
            // SPL reference running beside it. Same events, same names, and
            // the pairing with the RTL waveform is the point (design §7).
            void'(fw_dbg_console::attach(refroot, "ref"));
            refroot.start();
            uvm_config_db #(wb_dma_ref_model)::set(null, "*", "ref_model", refroot);
            fwvip_wb_monitor_config_p #(virtual wb_monitor_xtor_if #(32, 32))::set(
                null, "uvm_test_top.env.m_mon_m0*",  "cfg", hdl_top_rtl.u_mon_m0.u_if);
            fwvip_wb_monitor_config_p #(virtual wb_monitor_xtor_if #(32, 32))::set(
                null, "uvm_test_top.env.m_mon_m1*",  "cfg", hdl_top_rtl.u_mon_m1.u_if);
            fwvip_wb_monitor_config_p #(virtual wb_monitor_xtor_if #(32, 32))::set(
                null, "uvm_test_top.env.m_mon_reg*", "cfg", hdl_top_rtl.u_mon_reg.u_if);
        end

        // Bind the interrupt-pin GPIO monitor + swap in the GPIO-augmented env.
        `fwvip_gpio_monitor_register(2, hdl_top_rtl.u_mon_int.u_if, "uvm_test_top.env.m_int*")
        wb_dma_env::type_id::set_type_override(wb_dma_wb_env::get_type());

        // No sim time before run_test(): reset is released by the concurrent
        // initial in hdl_top_rtl; the first register access blocks on the bus
        // until then.
        uvm_config_db #(wb_dma_model_base)::set(null, "*", "model", root);
        // Bind the fwvip-wb initiator to the model's wb_proto_if register port
        // (a WB initiator bridge over the host xtor -> real WB cycles into WB0).
        fwvip_wb_initiator_config_ap #(32, 32)::set(
            null, "uvm_test_top.env.m_init*", "cfg", root.rf_if());
        run_test();
    end

    // Sim-time watchdog (skipped for +PERF runs, which are bounded by wall-clock).
    initial begin
        if (!$test$plusargs("PERF")) begin
            #5ms;
            $fatal(1, "[hvl_top_rtl] TIMEOUT");
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
