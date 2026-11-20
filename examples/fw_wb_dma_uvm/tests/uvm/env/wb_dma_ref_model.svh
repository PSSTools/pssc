// ---- always-on passive reference model -------------------------------------
// A full SPL model (engine + two memories + irq collector) identical to the TLM
// flavour, PLUS a transaction tap on each master port so its data-mover activity
// is observable as a wb_dma_xact stream. Two roles:
//
//   * REFERENCE: elaborated in every top, fed the same register stimulus the DUT
//     receives (teed on the TLM top, monitor-replayed on the signal tops). It
//     runs its own engine over its OWN memories and emits the PREDICTED master
//     stream. Nothing drives a reactive ISR against it -- it is passive.
//   * DUT (spl/TLM top only): used AS the DUT so both sides of the null
//     differential are identically tapped (symmetric attribution + streams).
//
// It also resolves the owning channel for each tapped access (address-window
// attribution over the live channel registers), which both taps consult.
class wb_dma_ref_model extends wb_dma_tlm_model
        implements wb_dma_ch_resolver;
    wb_dma_xact_tap tap0, tap1;      // on IF0 / IF1 master

    function new(string name, fw_component parent, int unsigned n_ch = 4);
        super.new(name, parent, n_ch);
    endfunction

    function void build();
        super.build();               // dma, s0/s1, cx0/cx1, irqc, hsdev, m_qk
        tap0 = new("tap0", this, 1'b0);
        tap1 = new("tap1", this, 1'b1);
        tap0.m_resolver = this;
        tap1.m_resolver = this;
    endfunction

    // As tlm_model.connect(), but interpose the taps between the engine master
    // ports and the client exports: de.mifN -> tapN -> cxN -> sN.m.
    function void connect();
        dma.de.qk = m_qk;
        cx0.set_client(s0.m);
        cx1.set_client(s1.m);
        tap0.m_down = cx0;
        tap1.m_down = cx1;
        dma.de.mif0.connect(tap0);
        dma.de.mif1.connect(tap1);
        dma.de.irq.connect(irqc.irq);            // cause stream ON (n_done, done_order)
        foreach (hsdev[i]) dma.de.hs[i].connect(hsdev[i].hs);
        dma.rf.add_int_sink(this);
    endfunction

    // Bind the comparator (the xact sink) to both taps. Called by the env once
    // the comparator exists. Null-safe: until called, the taps are transparent.
    function void bind_sink(wb_dma_xact_sink s);
        tap0.m_sink = s;
        tap1.m_sink = s;
    endfunction

    // Register ingestion: replay one host register access into this model's
    // register file. Writes program the channels; reads carry the read-clear
    // side effects (INT_SRC / CH_CSR) and must replay in bus order.
    task apply_reg(bit [31:0] adr, bit [31:0] dat, bit [3:0] sel, bit we);
        bit [31:0] r; bit e;
        rf_if().access(adr, dat, sel, we, r, e);
    endtask

    // ---- channel attribution (wb_dma_ch_resolver) ----------------------------
    // Match `adr` on port `port` to the channel whose that-side PROGRAMMED window
    // covers it. ADR0 is always the source base, ADR1 the dest base; the physical
    // port a side uses is src_sel/dst_sel. Attribution is by ADDRESS ONLY -- NOT
    // by the live `busy` bit -- so it is timing-independent: the reference's own
    // tap (synchronous with its engine) and the signal DUT's monitor adapter
    // (which reads the reference's register state at a skewed time) resolve a
    // given address to the SAME channel. That timing-independence is essential on
    // the signal tops, where the reference is programmed by a lagging monitor-
    // replay. Exact for single-channel and disjoint per-channel windows (the
    // multi-channel test constraint); the optional S1 engine tag makes it exact
    // even with overlapping windows.
    virtual function int resolve_ch(bit [31:0] adr, bit port);
        foreach (dma.rf.ch[i]) begin
            wb_dma_csr_t csr = dma.rf.ch[i].regs.csr.read();
            int          words = int'(dma.rf.ch[i].regs.sz.read().tot_sz);
            if (csr.src_sel == port &&
                in_window(adr, dma.rf.ch[i].regs.adr0.read(), words)) return i;
            if (csr.dst_sel == port &&
                in_window(adr, dma.rf.ch[i].regs.adr1.read(), words)) return i;
        end
        return -1;
    endfunction

    local function bit in_window(bit [31:0] adr, bit [31:0] base, int words);
        // contiguous incrementing window [base, base + 4*words); circular-buffer
        // masks are out of scope for the baseline (S1 covers those).
        if (words <= 0) return 1'b0;
        return (adr >= base) && (adr < base + 32'(4*words));
    endfunction
endclass
