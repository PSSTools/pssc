// ---- canonical master-side transaction record (comparison currency) --------
// One record per master access() the DMA engine performs (a memory read on the
// source port, a write on the dest port). It carries TWO groups of fields:
//
//   * COMPARABLE PAYLOAD {adr, dat, sel, we, err} -- the wire content that must
//     match bit-for-bit between the reference model and the DUT. `dat` is the
//     data actually on the bus (write data on we=1, read data on we=0), so a
//     read and its paired write are independently checkable.
//   * CLASSIFICATION {port, channel, seq} -- NOT compared; used only to ALIGN
//     the two streams. Alignment is by {channel, port, we, seq}, never by $time
//     (the SPL model is loosely-timed / quantum-batched, the RTL is cycle-
//     accurate -- absolute timestamps are not comparable; see the strategy doc).
//
// A uvm_object so it rides analysis ports and prints/clones for triage.
class wb_dma_xact extends uvm_object;
    // --- comparable payload (the only fields do_compare looks at) ---
    bit [31:0] adr;
    bit [31:0] dat;     // bus data: write data (we=1) or read data (we=0)
    bit [3:0]  sel;
    bit        we;
    bit        err;
    // --- classification / alignment (never compared) ---
    bit        port;    // 0 = IF0/WB0 master, 1 = IF1/WB1 master
    int        channel; // owning channel (-1 = unresolved)
    int        seq;     // per-{channel,port,we} monotonic index -- the alignment key

    `uvm_object_utils(wb_dma_xact)

    function new(string name = "wb_dma_xact"); super.new(name); endfunction

    // Content equality -- the check the comparator applies once two records are
    // aligned. Deliberately ignores {port, channel, seq, and time}: those select
    // WHICH pair to compare, not WHETHER the pair matches. `dat` is compared only
    // when the access did NOT error: an errored access aborts, so its data bus is
    // a don't-care (a responder may or may not drive read data alongside ERR --
    // the injecting RAM returns the word, the TLM memory returns 0).
    function bit content_eq(wb_dma_xact o);
        if (o == null) return 1'b0;
        if (!((adr === o.adr) && (sel === o.sel) && (we === o.we) && (err === o.err)))
            return 1'b0;
        if (err) return 1'b1;                 // errored access: data is don't-care
        return (dat === o.dat);
    endfunction

    function string convert2string();
        return $sformatf("%s adr=0x%08h dat=0x%08h sel=0x%1h err=%0b | port=%0d ch=%0d seq=%0d",
                         we ? "WR" : "RD", adr, dat, sel, err, port, channel, seq);
    endfunction

    virtual function void do_copy(uvm_object rhs);
        wb_dma_xact o;
        super.do_copy(rhs);
        if (!$cast(o, rhs)) return;
        adr=o.adr; dat=o.dat; sel=o.sel; we=o.we; err=o.err;
        port=o.port; channel=o.channel; seq=o.seq;
    endfunction
endclass
