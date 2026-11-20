// ---- arbitration test: 4 channels, distinct priorities -------------------
class wb_dma_arb_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_arb_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    task run_phase(uvm_phase phase);
        wb_dma_arb_seq    seq;
        int unsigned      prio[]     = '{1, 3, 0, 2};   // ch0..ch3 priorities
        int unsigned      exp_order[] = '{1, 3, 0, 2};  // highest-priority first
        phase.raise_objection(this);

        seq = wb_dma_arb_seq::type_id::create("seq");
        seq.model = model;
        seq.prio  = prio;
        seq.tot   = 32;
        seq.chunk = 4;
        seq.start(env.m_init.m_seqr);

        // Each channel's block copied correctly (distinct 0x80-byte regions).
        foreach (prio[c])
            env.sb.check_copy(0, seq.src_base(c), 0, seq.dst_base(c), seq.tot);
        env.sb.check_done_count(4);
        env.sb.check_order(exp_order);
        `uvm_info("ARB", $sformatf("completion order = %p", model.irqc.done_order), UVM_LOW)

        phase.drop_objection(this);
    endtask
endclass
