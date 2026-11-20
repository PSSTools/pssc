// ---- equal-priority arbitration test: 4 channels, round-robin -------------
// Same 4-channel arb sequence, but all channels share ONE priority, so the
// engine round-robins per chunk. The completion ORDER is scheduling freedom
// (may differ SPL vs RTL), so this test asserts only per-channel copy
// correctness and the done COUNT -- not a fixed order. The cross-comparator is
// run with +CMP_BY_CHANNEL +CMP_NO_ORDER: per-channel data streams must still
// match exactly (channel-keying absorbs the interleave), and the SET of
// completions must match, but not their sequence.
class wb_dma_arb_eq_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_arb_eq_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    task run_phase(uvm_phase phase);
        wb_dma_arb_seq seq;
        int unsigned   prio[] = '{2, 2, 2, 2};   // equal priority -> round-robin
        phase.raise_objection(this);

        seq = wb_dma_arb_seq::type_id::create("seq");
        seq.model = model;
        seq.prio  = prio;
        seq.tot   = 32;
        seq.chunk = 4;
        seq.start(env.m_init.m_seqr);

        foreach (prio[c])
            env.sb.check_copy(0, seq.src_base(c), 0, seq.dst_base(c), seq.tot);
        env.sb.check_done_count(4);
        `uvm_info("ARBEQ", $sformatf("completion order = %p (round-robin, not asserted)",
                                     model.irqc.done_order), UVM_LOW)

        phase.drop_objection(this);
    endtask
endclass
