// ---- abstract model base: the accessor surface the UVM side depends on -----
// The UVM env/scoreboard/tests reach the "DUT" ONLY through this base: the
// register BFM (rf_if), the two data memories (mem/s0/s1 with fill/peek), the
// interrupt collector (irqc), and the per-channel HS devices (hsdev). Two
// flavours implement it: the TLM model below (engine driven by method calls)
// and the Wishbone model (engine inside wb_dma_spl, driven over transactors).
virtual class wb_dma_model_base extends fw_component
        implements wb_dma_int_if;
    int unsigned         n_ch;
    wb_dma_mem           s0, s1;    // IF0 / IF1 memories
    wb_dma_irq_collector irqc;
    wb_dma_hs_device     hsdev[];   // (TLM only; unused in the WB flavour)

    // The interrupt-change seam (wb_dma_int_if). A flavour-specific PRODUCER
    // calls int_changed() -- the TLM model wires rf.signal_int here; the WB
    // flavour feeds it from a GPIO monitor on inta_o/intb_o. The bench waits
    // ONLY on m_int_ev, so it is agnostic to how the interrupt was observed.
    bit [1:0] m_int_level;          // {intb, inta}
    event     m_int_ev;

    function new(string name, fw_component parent, int unsigned n_ch = 4);
        super.new(name, parent);
        this.n_ch = n_ch;
    endfunction

    // Register-access BFM (fw_mem_if). TLM: the engine's host export; WB: a
    // host adapter that drives a Wishbone initiator into wb_dma_spl.
    pure virtual function wb_dma_mem_if_t rf_if();
    // Data memory backdoor (both flavours own s0/s1).
    function wb_dma_mem mem(bit sel); return sel ? s1 : s0; endfunction
    // Bus-observed CHANNEL-STATUS hook. Called by wb_dma_pss_mem_adapter when
    // the operation layer reads a channel's CSR -- which is the read that also
    // clears the interrupt. The signal flavours override it to classify the
    // completion cause; the TLM model fills irqc from the engine's cause seam
    // directly and leaves this a no-op.
    //
    // The adapter EDGE-DETECTS before calling this: the CSR reports done/err as
    // a LEVEL that holds until the channel is re-armed, and the operation layer
    // reads that register several times per transfer. See the adapter.
    //
    //   csr bit 12 = err (status: errored), bit 11 = done (status: complete)
    //
    // Its INT_SRC counterpart, note_int_src(), is gone with the INT_SRC polling
    // it existed for: the operation layer never reads INT_SRC, and no flavour
    // ever overrode the hook.
    virtual function void note_ch_status(int c, logic [31:0] csr); endfunction

    // --- interrupt-change seam ---
    virtual function void int_changed(bit inta, bit intb);
        m_int_level = {intb, inta};
        -> m_int_ev;
    endfunction
    task wait_int_change(); @m_int_ev; endtask
    function bit inta_level(); return m_int_level[0]; endfunction
    function bit intb_level(); return m_int_level[1]; endfunction
endclass
