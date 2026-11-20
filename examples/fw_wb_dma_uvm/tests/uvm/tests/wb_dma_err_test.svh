// ---- bus-error abort test -------------------------------------------------
// Runs the injected-error sequence and checks the abort is clean: the words
// BEFORE the error are copied, the channel reports exactly one error, and no
// done. The cross-comparator (run with the error scenario) independently checks
// the DUT and reference aborted identically (same master stream, same n_err).
class wb_dma_err_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_err_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    task run_phase(uvm_phase phase);
        wb_dma_err_seq seq;
        phase.raise_objection(this);

        seq = wb_dma_err_seq::type_id::create("seq");
        seq.model = model;
        seq.start(env.m_init.m_seqr);

        // Words moved before the aborting read are copied; the channel errored once.
        env.sb.check_copy(0, SRC_BASE, 0, DST_BASE, seq.err_word);
        if (model.irqc.n_err != 1)
            `uvm_error("ERR", $sformatf("expected 1 error interrupt, got %0d", model.irqc.n_err))
        if (model.irqc.n_done != 0)
            `uvm_error("ERR", $sformatf("expected 0 done interrupts (aborted), got %0d", model.irqc.n_done))

        phase.drop_objection(this);
    endtask
endclass
