// Interrupt-cause API. The DMA engine RAISES one cause for one channel (holds an
// fw_port); an interrupt model / scoreboard PROVIDES the sink (via
// `FW_WB_DMA_IRQ_IMP). NON-BLOCKING (a function) -- an interrupt sink may not
// block. The level outputs inta_o/intb_o are a separate concern computed in the
// register file from INT_SRC & INT_MSK_{A,B}; this API delivers the raw causes.
interface class wb_dma_irq_if;
    // Publish one interrupt cause (channel + cause) to the sink.
    pure virtual function void raise(input wb_dma_irq_evt_t evt);
endclass
