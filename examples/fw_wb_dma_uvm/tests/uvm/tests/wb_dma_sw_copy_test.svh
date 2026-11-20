// ---- SW block-copy test: sweep modes x sizes x chunks --------------------
class wb_dma_sw_copy_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_sw_copy_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    task run_phase(uvm_phase phase);
        int sizes[]  = '{1, 7, 16, 33};
        int chunks[] = '{0, 4, 5};
        phase.raise_objection(this);
        for (int mode = 0; mode < 4; mode++) begin
            bit ss = mode[1];   // src_sel
            bit ds = mode[0];   // dst_sel
            foreach (sizes[si]) foreach (chunks[ci]) begin
                wb_dma_sw_copy_seq seq = wb_dma_sw_copy_seq::type_id::create("seq");
                seq.model    = model;
                seq.src_sel  = ss;
                seq.dst_sel  = ds;
                seq.tot      = sizes[si];
                seq.chunk    = chunks[ci];
                seq.int_bank = mode[0];   // exercise both interrupt banks
                seq.start(env.m_init.m_seqr);

                env.sb.check_copy(ss, SRC_BASE, ds, DST_BASE, sizes[si]);
                env.sb.check_done_count(1);
            end
            `uvm_info("SWCOPY", $sformatf("mode %0d (%0d->%0d) done", mode, mode[1], mode[0]), UVM_LOW)
        end
        phase.drop_objection(this);
    endtask
endclass
