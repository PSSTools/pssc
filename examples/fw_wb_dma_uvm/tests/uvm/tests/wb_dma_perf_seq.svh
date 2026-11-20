// ---- throughput / performance sequence -----------------------------------
// Repeatedly runs a single-channel block copy (channel 0, one chunk) back to
// back, WITHOUT scoreboard checks, until a wall-clock target elapses. Counts
// completed transfers + words so the test can report throughput. Registers are
// re-programmed every transfer (the engine writes back TXSZ/ADRx on completion)
// -- a realistic per-transfer driver loop, and `transfer_single()` is exactly
// that loop: configure, arm, wait.
//
// MIGRATED DELIBERATELY, not exempted. The operation layer is what a driver
// would use, so a throughput number measured through it is the more honest
// figure; and this is the only test that would notice if the layer were
// expensive. Baseline (raw register writes), 2026-08-12:
//
//   TLM       5408.0 xfers/s   1.384 Mword/s
//   SPL_XTOR   107.9 xfers/s   0.028 Mword/s
//
// Per-transfer bus traffic changes shape: the old loop did 5 writes plus, per
// wake, 2 INT_SRC reads and a CH_CSR read; this one does 6 writes (configure
// writes AM0/AM1 too) plus one CH_CSR read per wake. Fewer accesses, but the
// channel_c/mailbox handoff and the extra process add SV-side cost that shows
// up on the TLM view, where the number is a class-model figure rather than a
// bus figure.
//
// Design: docs/uvm-op-layer-adoption.md §7.6.
class wb_dma_perf_seq extends wb_dma_base_seq;
    `uvm_object_utils(wb_dma_perf_seq)
    int  tot  = 256;                 // words per transfer
    real secs = 5.0;                 // wall-clock target (>= this runs)
    // results (read by the test after start()):
    longint unsigned xfers = 0;
    longint unsigned words = 0;
    real             wall  = 0.0;

    function new(string name = "wb_dma_perf_seq"); super.new(name); endfunction

    task body();
        wb_dma_status_e st;
        real t0;

        model.mem(0).fill(SRC_BASE, tot, 16'hBEEF);   // src content (once)
        dma().configure_interrupt_routing(WB_DMA_INT_A, 31'h7fff_ffff);

        t0 = wc_now();
        forever begin
            // chunk 0 => the whole transfer is a single chunk.
            begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
                wb_dma_ch_cfg_s cfg_ = mk_cfg(
                    .src(SRC_BASE), .dst(DST_BASE), .tot_sz(tot), .chk_sz(0));
                dma().ch(0).transfer_single(st, cfg_);
            end
            if (st != WB_DMA_DONE) begin
                `uvm_error("PERF", $sformatf(
                    "transfer %0d ended %s, expected WB_DMA_DONE", xfers, st.name()))
                break;
            end
            xfers++; words += tot;
            // Check the clock every 8 transfers (amortise the /proc read).
            if ((xfers % 8 == 0) && (wc_now() - t0) >= secs) break;
        end
        wall = wc_now() - t0;
    endtask
endclass
