// ---- env -----------------------------------------------------------------
// The register-access agent is the fwvip-wb initiator (m_init); its config is
// set by the SV top over the model's wb_proto_if register port (reg_if()), so
// the same agent drives the TLM class model and the signal-level DUTs alike.
//
// The env also hosts the always-on comparison harness: an ever-present passive
// reference model (ref_model, published by the top) and a comparator that
// cross-checks the DUT's master stream against the reference's. On the TLM top
// the DUT is itself a tapped wb_dma_ref_model, so both streams come from taps;
// on the signal tops the DUT stream comes from bus monitors (added P2/P3).
class wb_dma_env extends uvm_env;
    `uvm_component_utils(wb_dma_env)
    wb_dma_model_base       model;
    wb_dma_ref_model        ref_model;   // always-on passive reference (null if a top omits it)
    fwvip_wb_initiator      m_init;
    wb_dma_scoreboard       sb;
    wb_dma_comparator       cmp;

    // The generated operation model (wb_dma_c_pkg) and its import seam. Built
    // once, here, because the device handle outlives any one sequence; the
    // sequence binds itself to the adapter when it starts.
    wb_dma_pss_mem_adapter  pss;

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        void'(uvm_config_db #(wb_dma_model_base)::get(this, "", "model", model));
        void'(uvm_config_db #(wb_dma_ref_model)::get(this, "", "ref_model", ref_model));
        m_init = fwvip_wb_initiator::type_id::create("m_init", this);
        sb     = wb_dma_scoreboard::type_id::create("sb", this);
        cmp    = wb_dma_comparator::type_id::create("cmp", this);

        // Base 0: this bench's register accesses carry raw offsets (the
        // fwvip-wb initiator is bound directly over the model's register port),
        // so the device's window starts at zero.
        pss = new(model, 64'h0);
        uvm_config_db #(wb_dma_pss_mem_adapter)::set(null, "*", "pss", pss);
    endfunction

    // ---- the interrupt pump ------------------------------------------------
    //
    // `wait_completion()` in the generated model blocks on a per-channel
    // `wake` channel, and the ONLY thing that posts to it is
    // `wb_dma_c::notify_irq()`. Nothing in PSS calls that -- src/pss/README.md
    // is explicit that it is the environment's entry point -- so without this
    // thread every end-to-end operation would block forever.
    //
    // `int_changed()` is the seam, and it is already fed on all three views
    // (rf.signal_int on TLM, a GPIO monitor on WB/RTL), so one pump serves all
    // of them. It cannot call notify_irq() directly: int_changed() is a
    // `function void` and notify_irq() is a task.
    //
    // NOTIFY ON EVERY CHANGE, INCLUDING DEASSERT. The line deasserts when a
    // waiter's probe_status() reads its CSR; that deassert edge is what
    // re-wakes the OTHER channels' waiters. Filtering to assert-only edges
    // would stall a multi-channel run.
    //
    // No objection is raised: this must not keep the run phase alive. A token
    // posted before anyone waits is not lost -- `wake` is depth-1 and IS the
    // registration -- so there is no startup race with this thread either.
    //
    // Design: docs/uvm-op-layer-adoption.md §6.2.
    task run_phase(uvm_phase phase);
        if (model == null || pss == null) return;
        forever begin
            model.wait_int_change();
            pss.dma.notify_irq();
        end
    endtask

    function void connect_phase(uvm_phase phase);
        wb_dma_ref_model dut_rm;
        super.connect_phase(phase);
        cmp.dut_model = model;
        cmp.ref_model = ref_model;
        // No reference published (e.g. a perf run) => nothing to cross-check.
        // Disable the comparator and leave the taps unbound (transparent).
        if (ref_model == null) begin
            cmp.m_enable = 1'b0;
            return;
        end
        // Reference master stream -> comparator (REF side).
        ref_model.bind_sink(cmp.sink_ref);
        // DUT master stream -> comparator (DUT side). On the TLM top the DUT IS a
        // tapped ref_model; on the signal tops the cast fails and the DUT stream
        // arrives via bus monitors instead (wired in the signal tops' env, P2/P3).
        if ($cast(dut_rm, model)) dut_rm.bind_sink(cmp.sink_dut);
    endfunction
endclass
