// ---- scoreboard: backdoor correctness checks -----------------------------
class wb_dma_scoreboard extends uvm_scoreboard;
    `uvm_component_utils(wb_dma_scoreboard)
    wb_dma_model_base model;
    int unsigned   errors;

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db #(wb_dma_model_base)::get(this, "", "model", model))
            `uvm_fatal("CFG", "no model env in config_db")
    endfunction

    // Verify `count` words copied from src_mem[src_base] to dst_mem[dst_base].
    function void check_copy(bit src_sel, logic [31:0] src_base,
                             bit dst_sel, logic [31:0] dst_base, int count);
        wb_dma_mem src = model.mem(src_sel);
        wb_dma_mem dst = model.mem(dst_sel);
        for (int i = 0; i < count; i++) begin
            logic [31:0] s = src.peek(src_base + 4*i);
            logic [31:0] d = dst.peek(dst_base + 4*i);
            if (d !== s) begin
                `uvm_error("SB", $sformatf("copy word %0d: dst[0x%08h]=0x%08h != src[0x%08h]=0x%08h",
                                           i, dst_base+4*i, d, src_base+4*i, s))
                errors++;
            end
        end
    endfunction

    function void check_done_count(int unsigned exp);
        if (model.irqc.n_done != exp) begin
            `uvm_error("SB", $sformatf("done-interrupt count %0d != expected %0d",
                                       model.irqc.n_done, exp))
            errors++;
        end
    endfunction

    // Channel completion order must match the priority schedule (highest
    // priority first; distinct priorities => strict order).
    function void check_order(int unsigned expected[]);
        if (model.irqc.done_order.size() != expected.size()) begin
            `uvm_error("SB", $sformatf("completion count %0d != expected %0d",
                                       model.irqc.done_order.size(), expected.size()))
            errors++;
            return;
        end
        foreach (expected[i])
            if (model.irqc.done_order[i] != expected[i]) begin
                `uvm_error("SB", $sformatf("completion order[%0d] = ch%0d, expected ch%0d",
                                           i, model.irqc.done_order[i], expected[i]))
                errors++;
            end
    endfunction
endclass
