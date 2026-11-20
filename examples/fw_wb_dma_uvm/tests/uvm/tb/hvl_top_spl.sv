// HVL top for the SPL (class-model) UVM path. No signal-level DUT -- the "DUT"
// is the wb_dma fw-hdl class tree, brought up here as a wb_dma_tlm_model (engine +
// two fw_mem_if memories + irq collector + per-channel HS devices). This top:
//   1. instances hdl_top_spl (the clock substrate),
//   2. elaborates the model root under fw_component_root and forks its runnable(s),
//   3. publishes the model handle into the uvm_config_db (the UVM reg agent
//      reaches the engine register slave through model.rf_if(); the scoreboard
//      reads the memories / irq collector by backdoor), then
//   4. runs the selected UVM test (+UVM_TESTNAME).
//
// The instance of hdl_top_spl is named after the type (emulation convention);
// the HVL reaches its clock interface hierarchically (hdl_top_spl.u_clk).
`include "uvm_macros.svh"

module hvl_top_spl;
    import uvm_pkg::*;
    import fw_hdl_pkg::*;
    import fw_proto_wb_pkg::*;
    import fwvip_wb_pkg::*;
    import wb_dma_env_pkg::*;
    import wb_dma_tests_pkg::*;

    // Signal-level substrate (clock/reset + clock xtor) lives in hdl_top_spl.
    hdl_top_spl hdl_top_spl();

    // Wall-clock (real seconds) at startup / finish -- for throughput measurement.
    real wc_start;

    // The DUT on this top is a wb_dma_ref_model (a tapped TLM model): its master
    // ports are observable as a wb_dma_xact stream, so the always-on reference
    // (refroot, a second identical instance) can be compared against it -- the
    // ladder's null differential. Module-scoped so the final block can report tap
    // activity.
    fw_component_root #(wb_dma_ref_model) root;     // the DUT (tapped)
    fw_component_root #(wb_dma_ref_model) refroot;  // the always-on passive reference

    initial begin
        automatic fw_clock_period_xtor_bridge clk_dom;
        automatic fw_clock_period_xtor_bridge ref_clk;
        automatic wb_dma_reg_tee tee;
        // Perf runs measure DUT throughput; the always-on reference (2x engine
        // work) would skew the number, so it is elaborated only for functional
        // runs. Comparison is meaningless without it (env disables the comparator).
        automatic bit perf = $test$plusargs("PERF");
        automatic bit no_ref = $test$plusargs("NO_REF");   // debug: skip the reference entirely
        root = new("root");
        clk_dom = new("clock", root, hdl_top_spl.u_clk.u_if);
        wc_start = wc_now();
        $display("[PERF] %m START  wallclock=%0.3f s", wc_start);
        root.clock.connect(clk_dom);        // seat the DUT's clock domain
        // Observable events: wired unconditionally, active only under +fw_dbg.
        // Attached to the DUT root ONLY -- the always-on reference runs the same
        // engine, and two interleaved copies of every event is not a trace.
        void'(fw_dbg_console::attach(root, "dut"));
        root.start();     // do_build -> do_connect -> do_run (forks the mover); returns
        uvm_config_db #(wb_dma_model_base)::set(null, "*", "model", root);

        if (!perf && !no_ref) begin
            refroot = new("refroot");
            ref_clk = new("ref_clock", refroot, hdl_top_spl.u_clk.u_if);
            refroot.clock.connect(ref_clk);
            refroot.start();  // the reference runs its own engine, passively
            uvm_config_db #(wb_dma_ref_model)::set(null, "*", "ref_model", refroot);
            // Bind the fwvip-wb initiator to a TEE over both register files, so the
            // reference is programmed identically to the DUT with no separate monitor.
            // +NO_TEE (debug): bind DUT-only, leaving the reference elaborated but
            // unfed -- isolates a tee fault from a reference-engine fault.
            if ($test$plusargs("NO_TEE")) begin
                fwvip_wb_initiator_config_ap #(32, 32)::set(
                    null, "uvm_test_top.env.m_init*", "cfg", root.rf_if());
            end else begin
                tee = new(root.rf_if(), refroot.rf_if());
                fwvip_wb_initiator_config_ap #(32, 32)::set(
                    null, "uvm_test_top.env.m_init*", "cfg", tee);
            end
        end else begin
            // DUT-only: bind directly to the DUT register file.
            fwvip_wb_initiator_config_ap #(32, 32)::set(
                null, "uvm_test_top.env.m_init*", "cfg", root.rf_if());
        end
        run_test();
    end

    // Sim-time watchdog (skipped for +PERF runs, which are bounded by wall-clock).
    initial begin
        if (!$test$plusargs("PERF")) begin
            #5ms;
            $fatal(1, "[hvl_top_spl] TIMEOUT");
        end
    end

    final begin
        real wc_end;
        wc_end = wc_now();
        $display("[PERF] %m FINISH wallclock=%0.3f s  ELAPSED=%0.3f s", wc_end, wc_end - wc_start);
        if (root != null)
            $display("[TAP] DUT ref_model master accesses: IF0=%0d IF1=%0d",
                     root.tap0.m_count, root.tap1.m_count);
    end
    // The site catalog + field schema, on +fw_dbg_catalog. At END of run:
    // a site's schema is written on its first activation, not at elaboration.
    final fw_dbg_console::dump_catalog();

endmodule
