// ---- interrupt-cause collector (records completion order) -----------------
class wb_dma_irq_collector extends fw_component;
    int unsigned n_done, n_err, n_chunk;
    int          done_order[$];     // channels, in DONE order
    `FW_WB_DMA_IRQ_IMP(wb_dma_irq_collector, irq);

    function new(string name, fw_component parent); super.new(name, parent); endfunction
    function void build(); irq = new(this); endfunction

    virtual function void irq_raise(input wb_dma_irq_evt_t evt);
        case (evt.cause)
            CAUSE_DONE:  begin n_done++;  done_order.push_back(int'(evt.channel)); end
            CAUSE_ERR:   n_err++;
            CAUSE_CHUNK: n_chunk++;
            default: ;
        endcase
    endfunction

    function void reset();
        n_done = 0; n_err = 0; n_chunk = 0; done_order.delete();
    endfunction
endclass
