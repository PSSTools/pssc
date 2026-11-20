// ---- master-port transaction tap (wb_proto_if decorator) -------------------
// A pass-through provider inserted between the DMA engine's master port
// (de.mif0 / de.mif1) and the real responder (the wb_proto_client_export that
// reaches the memory). It IS an fw_export#(wb_proto_if) -- so the engine port
// binds to it exactly as it would to the client export -- and it IMPLEMENTS
// wb_proto_if, so the engine calls tap.access() directly.
//
// Each access() first forwards to the downstream responder (so the read DATA is
// captured on the way back), THEN emits a wb_dma_xact describing what crossed
// the port. The engine (src/spl/wb_dma_de) is untouched: the interposition is
// done entirely in the reference model's connect() (see wb_dma_ref_model).
//
// The emit target is a wb_dma_xact_sink (an interface class), null until the
// UVM env injects the comparator. Keeping it an interface-class handle -- rather
// than a uvm_analysis_port -- lets this fw-hdl-tree object emit without owning a
// UVM component (uvm ports require a uvm_component parent). Null-safe: an
// unbound tap is a transparent forwarder with zero observable effect.

// The receive seam the comparator implements to collect tapped transactions.
interface class wb_dma_xact_sink;
    pure virtual function void write_xact(wb_dma_xact x);
endclass

// Resolves the owning channel for an access -- implemented by the reference
// model (it can see the live channel registers). Kept as a seam so the tap does
// not depend on the concrete model type.
interface class wb_dma_ch_resolver;
    pure virtual function int resolve_ch(bit [31:0] adr, bit port);
endclass

class wb_dma_xact_tap extends fw_export #(wb_proto_if #(32, 32))
        implements wb_proto_if #(32, 32);
    wb_proto_if #(32, 32)   m_down;    // downstream responder (cxN -> sN.m)
    wb_dma_xact_sink        m_sink;    // comparator (null => transparent)
    wb_dma_ch_resolver      m_resolver;// channel attribution (null => channel -1)
    bit                     m_port;    // 0 = IF0, 1 = IF1
    int                     m_count;   // total accesses forwarded (bring-up / stats)
    // per-{channel, we} monotonic sequence counters -- the alignment key. Sized
    // generously (channels x 2 directions); index [ch*2 + we].
    int                     m_seq [64];

    function new(string name, fw_component parent, bit port);
        super.new(name, parent, null);   // set_imp(this) AFTER super.new (this-in-ctor
        set_imp(this);                    // segfaults Verilator -- see wb_proto_client_export)
        m_port = port;
        foreach (m_seq[i]) m_seq[i] = 0;
    endfunction

    virtual task access(
            input  [31:0] adr,
            input  [31:0] dat_w,
            input  [3:0]  sel,
            input         we,
            output [31:0] dat_r,
            output        err);
        wb_dma_xact x;
        int ch;
        // Forward FIRST so read data is available to capture.
        m_down.access(adr, dat_w, sel, we, dat_r, err);
        m_count++;
        if (m_sink == null) return;      // transparent when unbound
        ch = (m_resolver != null) ? m_resolver.resolve_ch(adr, m_port) : -1;
        x = wb_dma_xact::type_id::create("x");
        x.adr = adr; x.sel = sel; x.we = we; x.err = err;
        x.dat = we ? dat_w : dat_r;
        x.port = m_port; x.channel = ch;
        x.seq = next_seq(ch, we);
        m_sink.write_xact(x);
    endtask

    // Monotonic per-{channel, direction} index. An unresolved channel (-1) uses
    // a shared bucket so both sides still advance in lockstep for single-channel
    // scenarios (they resolve identically).
    local function int next_seq(int ch, bit we);
        int idx = ((ch < 0) ? 0 : ch) * 2 + int'(we);
        if (idx < 0 || idx >= 64) idx = 0;
        return m_seq[idx]++;
    endfunction
endclass
