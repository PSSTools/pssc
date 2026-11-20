// ---- head-of-line sequence: a stalled HS channel must not starve others --
// Arms an HW-handshake channel (ch1) but issues NO request, then arms a SW
// channel (ch0). If the engine arbitrated-then-blocked on ch1's wait_req it
// would starve ch0 forever; with HS gating ch1 is simply not ready, so ch0
// runs to completion. Afterwards ch1 is fed and must also finish.
//
// ARM ORDER IS THE TEST, so both channels are armed with the non-blocking
// `transfer_single_start()` and only then waited on. The end-to-end
// `transfer_single()` would arm ch1 and block, and ch0 would never be armed at
// all -- the starvation the test is trying to disprove would be created by the
// sequence instead of the device.
//
// Design: docs/uvm-op-layer-adoption.md §7.5.
class wb_dma_hol_seq extends wb_dma_base_seq;
    `uvm_object_utils(wb_dma_hol_seq)
    int tot   = 16;
    int chunk = 4;
    localparam logic [31:0] SW_SRC = 32'h0000_0000, SW_DST = 32'h0000_4000;
    localparam logic [31:0] HS_SRC = 32'h0000_0800, HS_DST = 32'h0000_4800;

    function new(string name = "wb_dma_hol_seq"); super.new(name); endfunction

    task body();
        wb_dma_status_e st_sw, st_hs;
        int             k;

        model.irqc.reset();
        model.s0.fill(SW_SRC, tot, 16'h5000);
        model.s0.fill(HS_SRC, tot, 16'hB100);              // distinct pattern

        dma().configure_interrupt_routing(WB_DMA_INT_A, 31'h7fff_ffff);

        // Arm the HS channel FIRST (head of the arbiter scan) but never request
        // -- it must stay un-ready and yield to ch0.
        begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
            wb_dma_ch_cfg_s cfg_ = mk_cfg(
                .src(HS_SRC), .dst(HS_DST), .tot_sz(tot), .chk_sz(chunk),
                .mode(WB_DMA_HW_HANDSHAKE));
            dma().ch(1).transfer_single_start(cfg_);
        end

        // Arm the SW channel; it must complete despite the stalled HS channel.
        begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
            wb_dma_ch_cfg_s cfg_ = mk_cfg(
                .src(SW_SRC), .dst(SW_DST), .tot_sz(tot), .chk_sz(chunk));
            dma().ch(0).transfer_single_start(cfg_);
        end

        dma().ch(0).wait_completion(st_sw);
        if (st_sw != WB_DMA_DONE)
            `uvm_error("HOL", $sformatf(
                "SW channel 0 ended %s -- starved behind stalled HS channel 1?",
                st_sw.name()))

        // Now feed the HS channel; it must complete too.
        k = (chunk == 0) ? 1 : ((tot + chunk - 1) / chunk);
        fork
            dma().ch(1).wait_completion(st_hs);
            begin
                for (int i = 0; i < k; i++) model.hsdev[1].request_chunk();
            end
        join

        if (st_hs != WB_DMA_DONE)
            `uvm_error("HOL", $sformatf(
                "HS channel 1 ended %s after its requests, expected WB_DMA_DONE",
                st_hs.name()))
    endtask
endclass
