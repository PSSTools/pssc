// ---- pause / arm / release: model-vs-RTL equivalence probe ----------------
// Two INDEPENDENT runs, selected by +PAUSE_ARM_MODE=<1|2>, because the first
// one deliberately leaves the device wedged and state must not leak between
// them (see wb_dma_pause_arm_seq.svh).
//
//   mode 1  arm WHILE paused   -> must be DROPPED on every view
//   mode 2  pause/release/arm  -> must COMPLETE on every view
class wb_dma_pause_arm_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_pause_arm_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    task run_phase(uvm_phase phase);
        wb_dma_pause_arm_seq seq;
        int mode = 1;
        void'($value$plusargs("PAUSE_ARM_MODE=%d", mode));
        phase.raise_objection(this);

        seq = wb_dma_pause_arm_seq::type_id::create("seq");
        seq.model      = model;
        seq.pause_mode = mode;
        seq.start(env.m_init.m_seqr);

        `uvm_info("PAUSEARM", $sformatf("mode %0d -> %s (status=%s)", mode,
            seq.completed ? "COMPLETED" : "did not complete",
            seq.final_st.name()), UVM_NONE)

        if (mode == 1) begin
            // Arming while paused is DROPPED, on the RTL and on the class model
            // alike. Pinned in both directions: a model that started deferring
            // it would diverge from the RTL, and an RTL fix would have to be
            // mirrored in the model.
            if (seq.completed)
                `uvm_error("PAUSEARM",
                    {"arm-while-paused completed. That contradicts the RTL,",
                     " where de_start is a one-cycle pulse the paused FSM",
                     " cannot catch (wb_dma_ch_sel.v:449, wb_dma_de.v:501)."})
        end else begin
            // The control: proves the failure above is specific to arming while
            // paused, not a broken pause path or a sequence that never arms.
            if (!seq.completed)
                `uvm_error("PAUSEARM", $sformatf(
                    "control failed: pause/release/arm did not complete (status=%s)",
                    seq.final_st.name()))
            else
                env.sb.check_copy(0, seq.src, 0, seq.dst, seq.tot);
        end

        phase.drop_objection(this);
    endtask
endclass
