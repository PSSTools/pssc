// ---- base sequence: register helpers -------------------------------------
// Runs on the fwvip-wb initiator sequencer (fwvip_wb_transaction). Its config,
// bound over the model's wb_proto_if register port, turns each transaction into
// a design access() -- direct method call for TLM, real WB cycles for the
// signal-level DUTs.
class wb_dma_base_seq extends uvm_sequence #(fwvip_wb_transaction);
    `uvm_object_utils(wb_dma_base_seq)
    wb_dma_model_base      model;     // set by the test before start (backdoor access)
    wb_dma_ref_model       ref_model; // always-on reference (fetched lazily; null if absent)
    wb_dma_pss_mem_adapter pss;       // the generated operation model's import seam

    function new(string name = "wb_dma_base_seq"); super.new(name); endfunction

    // Bind this sequence to the adapter, so operation-layer accesses issue on
    // THIS sequencer, in order with any raw reg_write/reg_read the sequence
    // still does. The adapter and the device handle outlive sequences; only
    // this pointer is per-run.
    //
    // `dma()` below is how a sequence reaches the operation model:
    //
    //     dma().ch(0).transfer_single(st, cfg);
    //
    // Design: docs/uvm-op-layer-adoption.md §6.1, §6.4.
    virtual task pre_body();
        if (pss == null)
            void'(uvm_config_db #(wb_dma_pss_mem_adapter)::get(null, "", "pss", pss));
        if (pss != null) pss.seq = this;
    endtask

    virtual task post_body();
        // Leaving a stale pointer to a finished sequence behind would let a
        // later operation-layer call issue items on a sequence that is no
        // longer running -- which UVM reports far from the cause.
        if (pss != null && pss.seq == this) pss.seq = null;
    endtask

    function wb_dma_c_exp_if dma();
        if (pss == null)
            `uvm_fatal("PSSCFG", "no wb_dma_pss_mem_adapter in the config DB; is wb_dma_env built?")
        return pss.dma;
    endfunction

    // ---- operation-model configuration ------------------------------------
    //
    // Replaces mk_csr()/mk_sz(): the same arguments, assigned to the generated
    // config struct instead of hand-placed into a register word.
    //
    // THE MASKS ARE THE REASON THIS IS A FUNCTION and not a struct literal at
    // each call site. `wb_dma_ch_cfg_s` in PSS carries
    // `constraint default src_mask == 0xfffffffc`, but a default constraint
    // only applies to a RANDOMIZED field; the generated SV struct is plain, so
    // an uninitialized cfg has src_mask == 0 -- "increment no address bits",
    // i.e. a transfer that copies one word tot_sz times. The old sequences
    // never wrote AM0/AM1 and relied on the reset value, so nothing here
    // carried that knowledge before.
    //
    // Design: docs/uvm-op-layer-adoption.md §5.2, §6.4.
    function wb_dma_ch_cfg_s mk_cfg(
            bit [31:0]      src,
            bit [31:0]      dst,
            int             tot_sz,
            int             chk_sz       = 0,
            wb_dma_if_e     src_if       = WB_DMA_IF0,
            wb_dma_if_e     dst_if       = WB_DMA_IF0,
            wb_dma_mode_e   mode         = WB_DMA_SOFTWARE,
            bit [2:0]       prio         = 3'd0,
            bit             int_on_done  = 1'b1,
            bit             int_on_err   = 1'b1,
            bit             int_on_chunk = 1'b0);
        wb_dma_ch_cfg_s cfg;
        cfg.src          = src;
        cfg.dst          = dst;
        cfg.src_mask     = 32'hffff_fffc;   // linear addressing; see above
        cfg.dst_mask     = 32'hffff_fffc;
        cfg.tot_sz       = 12'(tot_sz);
        cfg.chk_sz       = 9'(chk_sz);
        cfg.src_if       = src_if;
        cfg.dst_if       = dst_if;
        cfg.inc_src      = 1'b1;
        cfg.inc_dst      = 1'b1;
        cfg.mode         = mode;
        cfg.prio         = prio;
        cfg.auto_restart = 1'b0;
        cfg.hw_restart_en= 1'b0;
        cfg.sz_wb        = 1'b0;
        cfg.int_on_done  = int_on_done;
        cfg.int_on_err   = int_on_err;
        cfg.int_on_chunk = int_on_chunk;
        return cfg;
    endfunction

    // Lazily resolve the reference model published by the top. Sequences that
    // preload source memory fan the fill out to it so the reference computes the
    // same copy (the source pattern is a backdoor fill, not a register write, so
    // it is not covered by the register tee/forward).
    function void ensure_ref();
        if (ref_model == null)
            void'(uvm_config_db #(wb_dma_ref_model)::get(null, "", "ref_model", ref_model));
    endfunction

    // Preload `count` words at `base` in memory `sel` of BOTH the DUT and the
    // reference with the identical deterministic pattern -- so their source data
    // (and therefore their predicted copies) agree.
    function void preload(bit sel, logic [31:0] base, int count, logic [15:0] seed);
        ensure_ref();
        model.mem(sel).fill(base, count, seed);
        if (ref_model != null) ref_model.mem(sel).fill(base, count, seed);
    endfunction

    // Arm an injected bus error at `addr` in memory `sel` on BOTH the DUT and the
    // reference, so both engines abort at the identical beat (P7 error scenario).
    function void inject_err(bit sel, logic [31:0] addr);
        ensure_ref();
        model.mem(sel).inject_err(addr);
        if (ref_model != null) ref_model.mem(sel).inject_err(addr);
    endfunction

    task reg_write(logic [31:0] addr, logic [31:0] data);
        fwvip_wb_transaction t = fwvip_wb_transaction::type_id::create("t");
        start_item(t);
        t.we = 1'b1; t.adr = addr; t.dat = data; t.sel = 4'hf;
        finish_item(t);
    endtask

    task reg_read(logic [31:0] addr, output logic [31:0] data);
        fwvip_wb_transaction t = fwvip_wb_transaction::type_id::create("t");
        start_item(t);
        t.we = 1'b0; t.adr = addr; t.sel = 4'hf;
        finish_item(t);
        data = t.dat;
    endtask

    // The interrupt-driven wait helpers that used to live here -- ack_int(),
    // wait_int() and wait_done() -- are gone. Completion is now the operation
    // model's business: `wait_completion()` blocks on the channel's `wake`
    // channel (posted by wb_dma_env's IRQ pump) and decodes err/done from the
    // channel's own CSR, which is also the read that clears the interrupt. See
    // docs/uvm-op-layer-adoption.md §6.2 and §6.3.1.
    //
    // reg_write/reg_read stay, but as the ADAPTER's plumbing rather than a
    // stimulus API: wb_dma_pss_mem_adapter issues every operation-layer access
    // through them, which is what keeps those accesses on this sequencer and in
    // front of the reference-model tee/forward.

endclass
