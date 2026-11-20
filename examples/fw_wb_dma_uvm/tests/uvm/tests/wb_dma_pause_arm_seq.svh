// ---- pause / arm / release: is a CH_EN write honoured while paused? --------
//
// A DIAGNOSTIC, not a feature test. It isolates one question that everything
// else in this bench confounds:
//
//     PAUSE the engine, arm a channel, release PAUSE. Does the transfer run?
//
// WHY IT MATTERS. `wb_dma_ch_sel.v:409` makes channel readiness a pure
// combinational level:
//
//     assign valid[N] = chN_conf[0] & chN_csr[`WDMA_CH_EN]
//                       & (mode ? (req_r[N] & !ack_o[N]) : 1'b1);
//
// There is no edge detector and no arm latch in that path -- CH_EN *is* the
// request line, re-evaluated by the arbiter every clock, and PAUSE gates the
// ENGINE rather than the capture. So the RTL should defer this arming and grant
// it on release.
//
// The SPL model instead edge-detects, and gates at the edge
// (`src/spl/wb_dma_rf.svh:229`):
//
//     if (!p.ch_en && v.ch_en && c.cap_en && !v.busy && !paused()) c.arm();
//
// A rising CH_EN observed while paused is consumed and never re-evaluated, so
// the arming is lost rather than deferred. If that reading is right, this
// sequence completes on the `rtl` view and times out on `tlm` and `wb`.
//
// It is also the pattern real firmware uses to start several channels with a
// deterministic priority order -- arm everything, then release -- which is why
// this is worth making work rather than merely documenting.
//
// THE WAIT IS BOUNDED. On a model that drops the arming this never completes,
// and a diagnostic that hangs tells you less than one that reports.
//
// Background: docs/uvm-op-layer-adoption.md §7.3.1.
class wb_dma_pause_arm_seq extends wb_dma_base_seq;
    `uvm_object_utils(wb_dma_pause_arm_seq)
    int chan       = 0;      // the control run uses a DIFFERENT channel: the
                             // first operation is never completed, so its
                             // `inflight` token is still held and a second
                             // *_start() on the same channel would be refused.
    bit [31:0] src = SRC_BASE;
    bit [31:0] dst = DST_BASE;
    int tot        = 16;
    int chunk      = 4;
    int max_polls  = 200;      // bounded; ~200 * poll_ns of device time
    int poll_ns    = 1000;
    // CONTROL: +NO_PAUSE runs the identical sequence with no pause at all. If
    // that does not complete either, the fault is in the sequence, not in the
    // device's arm-while-paused behaviour.
    // 0 = no pause; 1 = arm WHILE paused; 2 = pause then release, THEN arm.
    // Mode 2 is the control for the release path itself.
    int pause_mode = 1;
    // Result, read by the test.
    bit             completed = 1'b0;
    wb_dma_status_e final_st;

    function new(string name = "wb_dma_pause_arm_seq"); super.new(name); endfunction

    task body();
        wb_dma_status_e st;

        model.irqc.reset();
        preload(1'b0, src, tot, 16'hA55E);

        dma().configure_interrupt_routing(WB_DMA_INT_A, 31'h7fff_ffff);

        // 1. Pause. 2. Arm while paused. 3. Release.
        if (pause_mode == 1) begin
            dma().pause_engine(1'b1);
            begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
                wb_dma_ch_cfg_s cfg_ = mk_cfg(
                    .src(src), .dst(dst), .tot_sz(tot), .chk_sz(chunk));
                dma().ch(chan).transfer_single_start(cfg_);
            end
            dma().pause_engine(1'b0);
        end else begin
            if (pause_mode == 2) begin
                dma().pause_engine(1'b1);
                dma().pause_engine(1'b0);
            end
            begin // pssc-debug: a struct argument is `ref` (PSS passes a handle)
                wb_dma_ch_cfg_s cfg_ = mk_cfg(
                    .src(src), .dst(dst), .tot_sz(tot), .chk_sz(chunk));
                dma().ch(chan).transfer_single_start(cfg_);
            end
        end

        // Poll rather than wait_completion(): a dropped arming raises no
        // interrupt, so the blocking form would never return.
        st = WB_DMA_PENDING;
        for (int i = 0; i < max_polls; i++) begin
            dma().ch(chan).probe_status(st);
            if (st != WB_DMA_PENDING) break;
            #(poll_ns * 1ns);
        end

        final_st  = st;
        completed = (st == WB_DMA_DONE);

        if (!completed)
            `uvm_info("PAUSEARM", $sformatf(
                {"channel did not complete after pause/arm/release (status=%s);",
                 " the arming was dropped rather than deferred"}, st.name()), UVM_LOW)
    endtask
endclass
