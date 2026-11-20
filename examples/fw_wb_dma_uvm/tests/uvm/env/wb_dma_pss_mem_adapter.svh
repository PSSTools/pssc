// ---- the seam between the generated operation model and this testbench -----
//
// `wb_dma_c_pkg` (generated from src/pss by `pss.op-model-sv`) is a programming
// API with one hole in it: the import interface. It says WHAT accesses the
// device needs and leaves HOW to the integration. This class is the how.
//
// WHY IT DELEGATES TO THE SEQUENCE rather than driving the bus itself. Every
// register access in this bench already goes through
// `wb_dma_base_seq::reg_write`/`reg_read`, and three things ride on that path:
//
//   * the accesses land on ONE sequencer (`env.m_init.m_seqr`), so they keep a
//     single order relative to each other;
//   * on the TLM top, `wb_dma_reg_tee` fans each access out to the always-on
//     reference model, so the reference is programmed identically; and
//   * on the signal tops, `wb_dma_reg_forward` replays the observed cycles into
//     the reference instead.
//
// An adapter that drove its own transactions would have to re-derive all of
// that, and would interleave unpredictably with any sequence still using raw
// register writes -- which, during the migration, is all of them. Delegating
// gets it for free.
//
// The cost is that the adapter is bound to a RUNNING sequence: `seq` is set by
// `wb_dma_base_seq::pre_body()` and is only valid while that sequence is
// running. The `wb_dma_c` handle it builds outlives sequences, which is why the
// handle lives here and not on the sequence.
//
// Design: docs/uvm-op-layer-adoption.md §6.1.

typedef class wb_dma_base_seq;

class wb_dma_pss_mem_adapter implements wb_dma_c_imp_if;

    // The sequence currently driving the device. Set in pre_body().
    wb_dma_base_seq                     seq;

    // For the completion hooks below.
    wb_dma_model_base                   model;

    // The generated device handle. Built here because it is bound to THIS
    // import object for the life of the simulation.
    // pssc-debug: the facade both implementations share; `+pss_impl=gen|vm`
    // (default gen) picks the generated model or the pyvm one at run time.
    wb_dma_c_exp_if                     dma;

    // Address of each channel's CSR, derived from the generated register model
    // rather than restated -- see note_hooks() below.
    protected addr_handle_t             m_csr_addr[];

    // Per channel: has the current arming's terminal status already been
    // accounted for? See note_hooks() for why this cannot be a plain level
    // forward. Starts SET -- before the first arming there is no transfer, so
    // any done/err level the device is showing is stale by definition.
    protected bit                       m_reported[];

    function new(wb_dma_model_base model, addr_handle_t base = 0);
        wb_dma_regs_c probe;
        this.model = model;
        // pssc-debug: `+pss_impl=vm` runs the model in pyvm, and
        // `+pss_impl=native` (the `native` build only) on pssc's C engine,
        // loaded from the image `+pss_img=<file.zbc>` (`pssc build --api
        // --root wb_dma_c`).
        begin
            string impl = wb_dma_c_root_sel #(wb_dma_c_imp_if)::impl();
            if ((impl == "vm" || impl == "native") &&
                    pssvm_default_engine() == null) begin
                string img;
                if (!$value$plusargs("pss_img=%s", img))
                    `uvm_fatal("PSSVM", {"+pss_impl=", impl, " needs +pss_img=<image.zbc>"})
`ifdef PSSVM_DPI
                if (impl != "native")
                    `uvm_fatal("PSSVM", "this image is the native build: use +pss_impl=native|gen")
                begin
                    pssvm_engine_dpi eng = new(img);
                    pssvm_set_default_engine(eng);
                end
`else
                if (impl != "vm")
                    `uvm_fatal("PSSVM", "+pss_impl=native needs the native build (uvm.tests-native)")
                pssvm_set_default_engine(pssvm_engine_hif::create(img, "", "fake"));
`endif
            end
        end
        dma = wb_dma_c_root_sel #(wb_dma_c_imp_if)::create(this, base);
        if (dma == null)
            `uvm_fatal("PSSVM", "no model (see the PSSVM-ERROR above)")

        // DERIVED, NOT RESTATED. `wb_dma_regs_c` folds `bank[i]` at
        // base + 0x20 + i*0x20 and `csr` at bank + 0x0, both from src/rdl. A
        // second instance of the same generated class, bound to the same base,
        // answers where every channel CSR is without this file knowing a single
        // offset. It issues no transactions -- constructing a reg_c only folds
        // an address.
        probe = new(this, base);
        m_csr_addr = new[$size(probe.bank)];
        m_reported = new[$size(probe.bank)];
        foreach (probe.bank[i]) begin
            m_csr_addr[i] = probe.bank[i].csr.addr();
            // NOT 0. The OpenCores RTL comes out of reset with CSR.DONE (bit
            // 11) already set -- the very first read, the one inside
            // write_field(CH_EN)'s read-modify-write, returns 0x0006_0818
            // before any transfer has been armed. Starting at 0 counted that
            // as a completion, which is why only the FIRST transfer of an rtl
            // run over-counted: every later one was suppressed by the previous
            // transfer's latch.
            m_reported[i] = 1'b1;
        end
    endfunction

    // ---- pss_mem_if -------------------------------------------------------
    //
    // Only the 32-bit pair is reachable: every register in this map is 32 bits,
    // so `reg_c` folds ACC_W to 32 and never selects another width. The rest
    // are fatal rather than silently narrowed -- if that ever stops being true,
    // a wrong-width bus access is far harder to find than a stopped simulation.

    // pssc-debug: `+pss_bus_log` prints every model access as
    // `PSSBUS <time> R|W <addr> <data>`, to compare `+pss_impl=gen` and `vm`.
    static bit m_bus_log = $test$plusargs("pss_bus_log");

    virtual task write32(addr_handle_t addr, bit [31:0] data);
        check_seq("write32");
        arm_watch(32'(addr), data);
        seq.reg_write(32'(addr), data);
        if (m_bus_log) $display("PSSBUS %0t W %08h %08h", $time, 32'(addr), data);
    endtask

    virtual task read32(addr_handle_t addr, output bit [31:0] data);
        check_seq("read32");
        seq.reg_read(32'(addr), data);
        if (m_bus_log) $display("PSSBUS %0t R %08h %08h", $time, 32'(addr), data);
        note_hooks(32'(addr), data);
    endtask

    virtual task write8 (addr_handle_t a, bit [7:0]  d); unsupported("write8");  endtask
    virtual task read8  (addr_handle_t a, output bit [7:0]  d); unsupported("read8");  d = '0; endtask
    virtual task write16(addr_handle_t a, bit [15:0] d); unsupported("write16"); endtask
    virtual task read16 (addr_handle_t a, output bit [15:0] d); unsupported("read16"); d = '0; endtask
    virtual task write64(addr_handle_t a, bit [63:0] d); unsupported("write64"); endtask
    virtual task read64 (addr_handle_t a, output bit [63:0] d); unsupported("read64"); d = '0; endtask

    // ---- the blocking seam ------------------------------------------------
    //
    // Reached only from `pause_engine()`'s read-back confirm loop. The generated
    // contract says it must return "when the device may have progressed" and
    // MUST NOT wait on an interrupt alone -- a pause confirmation is not
    // signalled by an interrupt, so an interrupt-only implementation deadlocks.
    //
    // A bounded delay, deliberately NOT tied to the interrupt seam. On the
    // signal views `paused` reads back only once the engine has reached a safe
    // point, so the confirm loop really does iterate; a zero-delay body would
    // make it a busy spin that starves the very engine it is waiting for.
    //
    // The value is a modelling choice, not a device property: long enough that
    // a poll is not free, short enough not to dominate a pause. Exposed so a
    // test that cares can tighten it.
    int unsigned yield_ns = 200;

    virtual task yield_();
        #(yield_ns * 1ns);
    endtask

    // ---- completion accounting -------------------------------------------
    //
    // The operation layer never reads INT_SRC -- `probe_status()` reads the
    // channel's own CSR and decodes err/done from it. So `note_int_src` (already
    // a no-op on every flavour) stays dead, and `note_ch_status` -- which is
    // what fills `irqc.n_done` / `done_order` on the WB and RTL views, and what
    // the scoreboard's check_done_count() consumes -- has to be fed from here
    // instead of from wb_dma_base_seq::ack_int.
    //
    // A sequence still using raw reg_write/reg_read keeps feeding the hooks
    // through wb_dma_base_seq::ack_int instead; the two never see the same CSR
    // read, so they cannot both count one completion.
    //
    // ---- WHY THIS IS EDGE-DETECTED, WHICH THE DESIGN MISSED ---------------
    //
    // `note_ch_status` counts COMPLETIONS. The CSR reports a LEVEL, and the two
    // are not the same thing here because DONE and INT_DONE are different bits:
    //
    //     bit 11  done       status: the transfer finished
    //     bit 21  int_done   interrupt source, cleared by reading the CSR
    //
    // Reading CH_CSR clears the interrupt SOURCE bits. It does not clear the
    // STATUS bits, which hold until the channel is re-armed -- and
    // `wb_dma_wb_model::note_ch_status` decodes csr[12]/csr[11], the status
    // pair.
    //
    // That was invisible while `ack_int` was the only reader: it read each
    // channel's CSR exactly once per interrupt, so one completion produced one
    // read produced one count. The operation layer reads the same register
    // several times per transfer -- `write_field(ch_en)` is a read-modify-write,
    // and `probe_status()` re-reads on every wake -- so forwarding every read
    // counted one completion two or three times. It showed up as
    // "done-interrupt count 2 != expected 1" on the wb and rtl views; the TLM
    // view was silent, because there `note_ch_status` is a no-op and irqc is
    // fed from the engine's own cause seam.
    //
    // So the latch below reports the 0->1 edge of a terminal status, once per
    // arming. It is cleared by the ARM (a CSR write with ch_en set) rather than
    // by observing done go back to 0: after a completion the status stays set
    // until the next arming, so waiting to see a zero would either miss the
    // next completion or race with a transfer fast enough to finish before the
    // first poll.
    //
    // Design: docs/uvm-op-layer-adoption.md §6.3, Option A + this correction.
    protected function void note_hooks(bit [31:0] addr, bit [31:0] data);
        if (model == null) return;
        foreach (m_csr_addr[i])
            if (addr == 32'(m_csr_addr[i])) begin
                if ((data[12] || data[11]) && !m_reported[i]) begin
                    m_reported[i] = 1'b1;
                    model.note_ch_status(i, data);
                end
                return;
            end
    endfunction

    // A CSR write that sets CH_EN arms the channel, which is what clears its
    // done/err status in hardware. Re-arm the reporter with it.
    protected function void arm_watch(bit [31:0] addr, bit [31:0] data);
        foreach (m_csr_addr[i])
            if (addr == 32'(m_csr_addr[i])) begin
                if (data[0]) m_reported[i] = 1'b0;   // CSR.CH_EN
                return;
            end
    endfunction

    // ---- guards -----------------------------------------------------------

    protected function void check_seq(string who);
        if (seq == null)
            `uvm_fatal("PSSSEQ", $sformatf(
                "%s: no sequence bound. The adapter drives the device through the running sequence's reg_write/reg_read; wb_dma_base_seq::pre_body() binds it.", who))
    endfunction

    protected function void unsupported(string who);
        `uvm_fatal("PSSWIDTH", $sformatf(
            "%s: every wb_dma register is 32 bits, so reg_c should never select this width. A narrower access here would be a silent wrong-width transaction.", who))
    endfunction

endclass
