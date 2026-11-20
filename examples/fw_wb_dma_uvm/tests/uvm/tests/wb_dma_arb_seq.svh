// ---- 4-channel priority arbitration sequence -----------------------------
// All channels copy an equal block (mode 0->0, distinct regions) but at
// distinct priorities; the engine must complete them highest-priority-first.
//
// Driven through the generated operation model. Two things about the shape:
//
// ARM ALL, THEN WAIT. The end-to-end `transfer_single()` cannot be used here:
// it arms one channel and then blocks, so channel N+1 would not be armed until
// channel N had finished and there would be nothing to arbitrate. This is the
// case the model splits `transfer_single_start()` from `wait_completion()` for
// -- the split is not an implementation detail, it is what lets a test hold
// several operations in flight.
//
// THE WAITS ARE FORKED, one per channel, because each waits on its own
// channel's `wake` and reads its own CSR. `notify_irq()` posts to every
// channel, so all four wake on any interrupt; the three that are still
// running see PENDING and go back to sleep. Completion ORDER is therefore
// still the order the device completes them in, not the order the forks were
// created -- what a waiter reports is its own CSR, which only reads DONE after
// that channel has actually finished.
//
// Design: docs/uvm-op-layer-adoption.md §7.3.
class wb_dma_arb_seq extends wb_dma_base_seq;
    `uvm_object_utils(wb_dma_arb_seq)
    int          n_ch = 4;
    int          tot  = 32;
    int          chunk = 4;
    int unsigned prio[];                  // per-channel priority

    function new(string name = "wb_dma_arb_seq"); super.new(name); endfunction

    // src base for channel c (0x80-byte stride, like the bench).
    function logic [31:0] src_base(int c); return 32'h0000_0000 + c*32'h80; endfunction
    function logic [31:0] dst_base(int c); return 32'h0000_4000 + c*32'h80; endfunction

    task body();
        model.irqc.reset();
        ensure_ref();
        if (ref_model != null) ref_model.irqc.reset();   // reference completions start clean too
        // Fill each channel's distinct source region (IF0 == s0), fanned out to
        // the reference so it computes the same copies.
        foreach (prio[c]) preload(1'b0, src_base(c), tot, 16'(16'hB000 + c));

        dma().configure_interrupt_routing(WB_DMA_INT_A, 31'h7fff_ffff);

        // Program every channel's addresses and size first, with CH_EN clear,
        // so the arming pass below is as close to back-to-back as this API
        // allows. `transfer_single_start` re-writes the same values -- harmless
        // on an idle channel -- and then sets CH_EN.
        foreach (prio[c])
            begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
                wb_dma_ch_cfg_s cfg_ = cfg_for(c);
                dma().ch(c).configure_channel(cfg_);
            end

        foreach (prio[c])
            begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
                wb_dma_ch_cfg_s cfg_ = cfg_for(c);
                dma().ch(c).transfer_single_start(cfg_);
            end

        foreach (prio[c]) begin
            automatic int cc = c;
            fork
                begin
                    wb_dma_status_e st;
                    dma().ch(cc).wait_completion(st);
                    if (st != WB_DMA_DONE)
                        `uvm_error("ARB", $sformatf(
                            "channel %0d ended %s, expected WB_DMA_DONE", cc, st.name()))
                end
            join_none
        end
        wait fork;
    endtask

    protected function wb_dma_ch_cfg_s cfg_for(int c);
        return mk_cfg(.src(src_base(c)), .dst(dst_base(c)),
                      .tot_sz(tot), .chk_sz(chunk), .prio(3'(prio[c])));
    endfunction
endclass
