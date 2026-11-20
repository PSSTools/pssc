// ---- head-of-line test: stalled HS channel must not block a SW channel ---
class wb_dma_hol_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_hol_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    task run_phase(uvm_phase phase);
        wb_dma_hol_seq seq;
        phase.raise_objection(this);
        seq = wb_dma_hol_seq::type_id::create("seq");
        seq.model = model;
        seq.tot   = 16;
        seq.chunk = 4;
        seq.start(env.m_init.m_seqr);

        env.sb.check_copy(0, seq.SW_SRC, 0, seq.SW_DST, seq.tot);   // ch0 (SW)
        env.sb.check_copy(0, seq.HS_SRC, 0, seq.HS_DST, seq.tot);   // ch1 (HS)
        env.sb.check_done_count(2);
        `uvm_info("HOL", "stalled HS channel did not starve the SW channel", UVM_LOW)

        phase.drop_objection(this);
    endtask
endclass
