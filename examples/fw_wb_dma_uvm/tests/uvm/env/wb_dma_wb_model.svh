class wb_dma_wb_model extends wb_dma_model_base;
    // Transactor virtual interface (seated by the SV top before start()).
    virtual wb_initiator_xtor_if #(32, 32) vhost;          // -> wb_dma_spl WB0 slave

    // Host register path only. The host initiator bridge over the host xtor IS the
    // wb_proto_if register BFM (rf_if()). The DATA memories (s0/s1) are the
    // wb_proto_if responders the master-side fwvip_wb_target agents reach (their
    // config's converter polls the WB target xtor and calls s0.m/s1.m.access()) --
    // wired by the SV top, not built here.
    wb_initiator_xtor_bridge #(32, 32) hbr;                // host WB initiator (= reg BFM)

    function new(string name, fw_component parent, int unsigned n_ch = 4);
        super.new(name, parent, n_ch);
    endfunction

    function void build();
        hbr  = new(vhost);
        // Data memories (same wb_dma_mem the scoreboard fills/peeks; s0.m/s1.m are
        // the wb_proto_if responders registered into the target agents' configs).
        s0   = new("s0", this);
        s1   = new("s1", this);
        irqc = new("irqc", this);
        // hsdev left empty: HW-handshake is stubbed in wb_dma_spl (hw_hs/hol
        // scenarios are not run on the Wishbone path).
    endfunction

    // Register BFM: the host initiator bridge IS the wb_proto_if into WB0 slave.
    virtual function wb_dma_mem_if_t rf_if(); return hbr; endfunction

    // Completion reconstruction is cause-aware and done from the channel CSR
    // (note_ch_status), NOT the cause-blind INT_SRC snapshot -- an error and a
    // done set the SAME INT_SRC bit, so INT_SRC alone cannot tell them apart.
    // The ISR reads CH_CSR to clear each interrupt anyway; that read carries the
    // err/done status bits used here. (note_int_src stays the base no-op.)
    virtual function void note_ch_status(int c, logic [31:0] csr);
        if      (csr[12]) irqc.n_err++;                                  // err status
        else if (csr[11]) begin irqc.done_order.push_back(c); irqc.n_done++; end  // done status
    endfunction
endclass
