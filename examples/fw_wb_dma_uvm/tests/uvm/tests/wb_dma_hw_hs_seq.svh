// ---- HW-handshake sequence (mode=1): one chunk per external request ------
//
// The operation and the handshake run CONCURRENTLY, and the model says why:
// `src/pss/wb_dma_types_pkg.pss` states that a transfer in WB_DMA_HW_HANDSHAKE
// mode cannot complete without a concurrent producer calling request_chunk().
// So `transfer_single()` and the request loop are forked rather than sequenced.
//
// The pre-migration form worked only because arming was non-blocking: it wrote
// CSR, returned, and then issued the requests. The blocking wrapper makes the
// concurrency explicit, which is what the device actually requires.
//
// Design: docs/uvm-op-layer-adoption.md §7.4.
class wb_dma_hw_hs_seq extends wb_dma_base_seq;
    `uvm_object_utils(wb_dma_hw_hs_seq)
    bit src_sel = 0;
    bit dst_sel = 0;
    int tot     = 32;
    int chunk   = 4;        // 0 => whole transfer is one chunk
    wb_dma_status_e st;

    function new(string name = "wb_dma_hw_hs_seq"); super.new(name); endfunction

    task body();
        int        k;     // number of chunks (== external requests)
        wb_dma_mem srcm = model.mem(src_sel);

        model.irqc.reset();
        srcm.fill(SRC_BASE, tot, 16'hAA00);

        dma().configure_interrupt_routing(WB_DMA_INT_A, 31'h7fff_ffff);

        k = (chunk == 0) ? 1 : ((tot + chunk - 1) / chunk);

        fork
            // The transfer. Blocks until the last chunk is acked.
            begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
                wb_dma_ch_cfg_s cfg_ = mk_cfg(
                    .src(SRC_BASE), .dst(DST_BASE), .tot_sz(tot), .chk_sz(chunk),
                    .src_if(src_sel ? WB_DMA_IF1 : WB_DMA_IF0),
                    .dst_if(dst_sel ? WB_DMA_IF1 : WB_DMA_IF0),
                    .mode(WB_DMA_HW_HANDSHAKE));
                dma().ch(0).transfer_single(st, cfg_);
            end
            // The peripheral side: one request per chunk, each blocking until
            // the engine acks that chunk (models dma_req/dma_ack).
            begin
                for (int i = 0; i < k; i++) model.hsdev[0].request_chunk();
            end
        join

        if (st != WB_DMA_DONE)
            `uvm_error("HWHS", $sformatf(
                "channel 0 ended %s, expected WB_DMA_DONE", st.name()))
    endtask
endclass
