// ---- single-channel bus-error abort sequence ------------------------------
// Programs a single-channel block copy, but arms an injected bus error on the
// source read of word `err_word`. The engine moves words 0..err_word-1, then
// the read of word err_word terminates with ERR -> the channel aborts (CH_ERR)
// and, with int_on_err set, raises its interrupt. Both the DUT and the always-on
// reference get the SAME injected error (fanned out via inject_err), so both
// abort at the identical beat -- the cross-comparator checks their master
// streams and error counts match.
//
// Driven through the generated operation model. The assertion is the reason
// this migration is worth doing here: the old sequence waited on INT_SRC bit 0
// and could only say "channel 0 raised an interrupt" -- which is equally true
// of a clean completion, since INT_SRC is cause-blind. `transfer_single`
// returns the cause, so this now asserts that the channel ERRORED.
//
// Design: docs/uvm-op-layer-adoption.md §7.2.
class wb_dma_err_seq extends wb_dma_base_seq;
    `uvm_object_utils(wb_dma_err_seq)
    bit src_sel  = 0;
    bit dst_sel  = 0;
    int tot      = 16;
    int chunk    = 4;
    int err_word = 6;      // abort on the source read of this word

    function new(string name = "wb_dma_err_seq"); super.new(name); endfunction

    task body();
        wb_dma_status_e st;

        model.irqc.reset();
        ensure_ref();
        if (ref_model != null) ref_model.irqc.reset();
        preload(src_sel, SRC_BASE, tot, 16'hE00D);
        inject_err(src_sel, SRC_BASE + 4*err_word);   // source read of word err_word -> ERR

        dma().configure_interrupt_routing(WB_DMA_INT_A, 31'h7fff_ffff);

        begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
            wb_dma_ch_cfg_s cfg_ = mk_cfg(
                .src(SRC_BASE), .dst(DST_BASE), .tot_sz(tot), .chk_sz(chunk),
                .src_if(src_sel ? WB_DMA_IF1 : WB_DMA_IF0),
                .dst_if(dst_sel ? WB_DMA_IF1 : WB_DMA_IF0));
            dma().ch(0).transfer_single(st, cfg_);
        end

        if (st != WB_DMA_ERROR)
            `uvm_error("ERR", $sformatf(
                "channel 0 ended %s, expected WB_DMA_ERROR", st.name()))
    endtask
endclass
