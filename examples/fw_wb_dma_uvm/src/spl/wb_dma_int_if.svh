// Interrupt-change notification seam. The abstract "interrupt-changed" function the
// whole verification stack pivots on: a PRODUCER calls int_changed() whenever the
// aggregate interrupt outputs move, and a CONSUMER (the bench, via the model env)
// turns that into an event it waits on. The bench depends ONLY on this call being
// made -- never on HOW the interrupt was observed -- so the same testbench verifies
//   * the raw TLM model      (producer = wb_dma_rf.signal_int),
//   * the xtor-wrapped model  (producer = a GPIO monitor on inta_o/intb_o), and
//   * signal-level RTL        (producer = the same GPIO monitor).
// inta/intb are the two aggregate level lines (spec: any enabled+pending source in
// bank A/B). Per-channel identity is still read from INT_SRC over the register bus.
interface class wb_dma_int_if;
    pure virtual function void int_changed(bit inta, bit intb);
endclass
