// ---- single-channel SW block-copy sequence -------------------------------
//
// Driven through the GENERATED operation model (wb_dma_c_pkg) rather than by
// hand-written register writes. Two register offsets and one CSR bit layout
// left this file when it was migrated; what remains is what the test is
// actually about -- which channel, from where to where, how much.
//
// The assertion also got stronger. `wait_int` could only prove that channel 0's
// INT_SRC bit had set, and INT_SRC is cause-BLIND: a completion and an abort
// set the same bit. `transfer_single` returns the cause, so a channel that
// errored can no longer pass this sequence.
//
// Design: docs/uvm-op-layer-adoption.md §7.1.
class wb_dma_sw_copy_seq extends wb_dma_base_seq;
    `uvm_object_utils(wb_dma_sw_copy_seq)
    bit src_sel = 0;
    bit dst_sel = 0;
    int tot      = 16;
    int chunk    = 4;     // 0 => whole transfer in one chunk
    bit int_bank = 0;     // 0=A, 1=B

    function new(string name = "wb_dma_sw_copy_seq"); super.new(name); endfunction

    task body();
        wb_dma_status_e st;

        model.irqc.reset();
        preload(src_sel, SRC_BASE, tot, 16'hC0DE);   // fan out to DUT + reference

        // Completion IS the interrupt, so routing is a hard prerequisite: a
        // channel routed nowhere raises nothing, and transfer_single() would
        // block forever. 31 bits, not 32 -- INTMSK.ch is a 31-bit field.
        dma().configure_interrupt_routing(
            int_bank ? WB_DMA_INT_B : WB_DMA_INT_A, 31'h7fff_ffff);

        begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
            wb_dma_ch_cfg_s cfg_ = mk_cfg(
                .src(SRC_BASE), .dst(DST_BASE), .tot_sz(tot), .chk_sz(chunk),
                .src_if(src_sel ? WB_DMA_IF1 : WB_DMA_IF0),
                .dst_if(dst_sel ? WB_DMA_IF1 : WB_DMA_IF0));
            dma().ch(0).transfer_single(st, cfg_);
        end

        if (st != WB_DMA_DONE)
            `uvm_error("SWCOPY", $sformatf(
                "channel 0 ended %s, expected WB_DMA_DONE", st.name()))
    endtask
endclass
