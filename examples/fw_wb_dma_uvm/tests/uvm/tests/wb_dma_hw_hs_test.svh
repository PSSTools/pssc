// ---- HW-handshake test: one chunk per external request -------------------
class wb_dma_hw_hs_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_hw_hs_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    task run_phase(uvm_phase phase);
        int chunks[] = '{0, 4, 8};
        phase.raise_objection(this);
        for (int mode = 0; mode < 4; mode++) begin
            bit ss = mode[1];
            bit ds = mode[0];
            foreach (chunks[ci]) begin
                wb_dma_hw_hs_seq seq = wb_dma_hw_hs_seq::type_id::create("seq");
                seq.model   = model;
                seq.src_sel = ss;
                seq.dst_sel = ds;
                seq.tot     = 32;
                seq.chunk   = chunks[ci];
                seq.start(env.m_init.m_seqr);

                env.sb.check_copy(ss, SRC_BASE, ds, DST_BASE, seq.tot);
                env.sb.check_done_count(1);
            end
            `uvm_info("HWHS", $sformatf("mode %0d (%0d->%0d) done", mode, mode[1], mode[0]), UVM_LOW)
        end
        phase.drop_objection(this);
    endtask
endclass
