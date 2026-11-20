// HDL top for the SPL (class-model) UVM path. The TLM "DUT" is the wb_dma fw-hdl
// class tree, instanced + run from hvl_top_spl -- so the only signal-level
// content here is the clock substrate the fw-hdl lifecycle needs. The model is
// delay-driven (it never ticks a real edge), but the lifecycle eagerly resolves
// every component's inherited clock domain during connect, so the root domain
// must be seated.
//
// The substrate is the PERIOD/delay-based clock transactor: no toggling clock
// (no `always #P clk`), so the run costs nothing for a clock the model does not
// use. The HVL binds the wrapper's inner u_if to fw_clock_period_xtor_bridge.
module hdl_top_spl;
    fw_clock_period_xtor #(.PERIOD(10ns)) u_clk();
endmodule
