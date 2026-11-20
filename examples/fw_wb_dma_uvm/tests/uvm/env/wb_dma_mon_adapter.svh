// ---- signal-top DUT master-stream adapter ----------------------------------
// On the signal tops (spl_wb, rtl) the DUT has no taps, so its master data-mover
// stream is observed by a fwvip_wb_monitor on each master bus (WB0/WB1). This
// subscriber converts each observed fwvip_wb_transaction into the canonical
// wb_dma_xact and feeds it to the comparator's DUT side -- the mirror of the
// reference's tap emit. Channel attribution reuses the reference's resolver
// (both are programmed identically), and the monitor's `dat` already follows the
// write-data-on-WE / read-data-on-!WE convention wb_dma_xact expects.
class wb_dma_mon_adapter extends uvm_subscriber #(fwvip_wb_transaction);
    `uvm_component_utils(wb_dma_mon_adapter)
    wb_dma_comparator  cmp;         // DUT-side collector
    wb_dma_ch_resolver resolver;    // reference model (live channel attribution)
    bit                port;        // 0 = WB0/IF0 master, 1 = WB1/IF1 master

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    virtual function void write(fwvip_wb_transaction t);
        wb_dma_xact x;
        if (cmp == null || !cmp.m_enable) return;
        x = wb_dma_xact::type_id::create("x");
        x.adr = t.adr; x.dat = t.dat; x.sel = t.sel; x.we = t.we; x.err = t.err;
        x.port    = port;
        x.channel = (resolver != null) ? resolver.resolve_ch(x.adr, port) : -1;
        cmp.collect(1'b0, x);       // DUT stream
    endfunction
endclass
