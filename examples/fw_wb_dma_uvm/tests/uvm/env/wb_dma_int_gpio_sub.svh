// ---- GPIO-monitor -> interrupt-change seam adapter ------------------------
// Subscribes to the GPIO monitor tapping {intb_o, inta_o} and forwards each
// observed level to the model's int_changed() -- the SAME seam the TLM model
// drives from rf.signal_int. The bench (wait_int/wait_done) waits only on that
// seam, so it is identical across the TLM, xtor, and RTL paths.
class wb_dma_int_gpio_sub extends uvm_subscriber #(fwvip_gpio_transaction);
    `uvm_component_utils(wb_dma_int_gpio_sub)
    wb_dma_model_base model;
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
    virtual function void write(fwvip_gpio_transaction t);
        if (model != null) model.int_changed(t.value[0], t.value[1]);  // {intb,inta}
    endfunction
endclass
