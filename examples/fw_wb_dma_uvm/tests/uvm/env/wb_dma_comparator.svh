// ---- SPL<->DUT master-stream comparator ------------------------------------
// Collects the master-transaction stream from BOTH the always-on reference and
// the active DUT, aligns them by {channel, port, we} + arrival order (the seq
// index), and checks content equality pair-by-pair. Never aligns by $time (the
// SPL model is loosely-timed; the RTL is cycle-accurate -- see the strategy doc).
//
// Two sources feed the same comparator through small router adapters (the sink
// seam carries no direction, so a router tags each stream):
//   * sink_ref  <- the passive reference model's taps
//   * sink_dut  <- the DUT's taps (TLM top) or bus monitors (signal tops, later)
//
// Alignment is a per-key FIFO match: because both models emit each key's beats
// in the same order, popping the two queues in lockstep pairs beat N of one
// side with beat N of the other. A content mismatch, or a leftover at end of
// test (length mismatch), is an error.
//
// Barriers (strategy §8): P1 implements B4 (end-of-test drain: every key equal
// length, no leftovers) plus a final memory cross-check (DUT vs reference
// backing store). B2 (per-channel-complete) / B3 (INT_SRC) arrive in P4+.

typedef class wb_dma_comparator;

// Direction-tagging adapter: routes a tapped xact to the comparator as ref/dut.
class wb_dma_xact_router implements wb_dma_xact_sink;
    wb_dma_comparator m_cmp;
    bit               m_is_ref;
    function new(wb_dma_comparator cmp, bit is_ref); m_cmp = cmp; m_is_ref = is_ref; endfunction
    virtual function void write_xact(wb_dma_xact x); m_cmp.collect(m_is_ref, x); endfunction
endclass

class wb_dma_comparator extends uvm_scoreboard;
    `uvm_component_utils(wb_dma_comparator)

    // Model handles (for the final memory cross-check). dut_model is the abstract
    // base (works on every top); ref_model is the always-on reference.
    wb_dma_model_base dut_model;
    wb_dma_ref_model  ref_model;

    // Direction-tagged sinks the taps/monitors emit into.
    wb_dma_xact_router sink_dut, sink_ref;

    // Per-key FIFOs, keyed "channel.port.we". Held only until the opposite side
    // supplies the matching beat, then popped in pairs.
    wb_dma_xact q_dut [string][$];
    wb_dma_xact q_ref [string][$];
    // A key CLOSES once an errored beat matches on it: a bus error aborts the
    // access, so it is terminal for that channel's stream -- any further beats
    // (e.g. the RTL error-abort re-issuing/holding the erroring cycle, which the
    // SPL model does not) are the abort's tail and are discarded, not miscompared.
    bit          m_closed [string];

    int unsigned errors;    // content + length + memory mismatches
    int unsigned matched;   // paired (content-checked) transactions
    bit          m_enable = 1'b1;   // +NO_CMP disables all checking
    // Alignment key includes the channel only for multi-channel scenarios. For
    // single-channel rungs it must NOT: on a signal top the DUT beat's channel is
    // resolved against the reference's LIVE register state, which lags the fast
    // DUT -- so a channel-keyed single-channel stream mis-bins (channel -1) and
    // desyncs. {port, we} is exact and timing-independent for one channel. Set by
    // the env (+CMP_BY_CHANNEL) once exact attribution lands (P5).
    bit          m_key_channel = 1'b0;
    // Compare completion ORDER (done_order), not just count. Off (+CMP_NO_ORDER)
    // for equal-priority arbitration, where the round-robin completion order is
    // legitimate scheduling freedom that may differ SPL vs RTL (strategy §7.4);
    // the per-channel data streams still must match, and so must the done COUNT.
    bit          m_check_order = 1'b1;

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if ($test$plusargs("NO_CMP"))        m_enable = 1'b0;
        if ($test$plusargs("CMP_BY_CHANNEL")) m_key_channel = 1'b1;
        if ($test$plusargs("CMP_NO_ORDER"))  m_check_order = 1'b0;
        sink_dut = new(this, 1'b0);
        sink_ref = new(this, 1'b1);
    endfunction

    local function string key(wb_dma_xact x);
        return m_key_channel ? $sformatf("%0d.%0d.%0d", x.channel, x.port, x.we)
                             : $sformatf("%0d.%0d", x.port, x.we);
    endfunction

    // Ingest one tapped transaction and try to pair it.
    function void collect(bit is_ref, wb_dma_xact x);
        string k;
        if (!m_enable) return;
        k = key(x);
        if (m_closed.exists(k) && m_closed[k]) return;   // key retired on error
        if (is_ref) q_ref[k].push_back(x);
        else        q_dut[k].push_back(x);
        try_match(k);
    endfunction

    // Pop matched pairs while both queues for this key are non-empty.
    local function void try_match(string k);
        while (q_dut.exists(k) && q_ref.exists(k) &&
               q_dut[k].size() > 0 && q_ref[k].size() > 0) begin
            wb_dma_xact a = q_dut[k].pop_front();
            wb_dma_xact b = q_ref[k].pop_front();
            matched++;
            if (!a.content_eq(b)) begin
                errors++;
                `uvm_error("CMP", $sformatf("master-stream mismatch on key %s:\n  DUT: %s\n  REF: %s",
                                            k, a.convert2string(), b.convert2string()))
            end
            if (a.err && b.err) begin           // matched errored beat -> key is terminal
                m_closed[k] = 1'b1;
                q_dut[k].delete();
                q_ref[k].delete();
                return;
            end
        end
    endfunction

    // B4 drain + memory cross-check. Runs in check_phase (before report_phase, so
    // the test's PASS/FAIL sees the result).
    function void check_phase(uvm_phase phase);
        super.check_phase(phase);
        if (!m_enable) begin
            `uvm_info("CMP", "cross-comparison DISABLED (+NO_CMP)", UVM_LOW)
            return;
        end
        check_final();
        check_memory();
        if (m_key_channel) check_interrupts();   // completion order (multi-channel scenarios)
        `uvm_info("CMP", $sformatf("cross-comparison: %0d matched, %0d errors", matched, errors), UVM_LOW)
    endfunction

    // Completion (DONE-interrupt) parity: the DUT and reference must report the
    // SAME channels completing in the SAME order. The reference fills its irqc
    // from its engine's cause seam; the DUT from its engine (TLM) or from
    // note_int_src reconstruction (signal tops). Gated to multi-channel runs
    // (m_key_channel): single-channel sequences reset irqc per-iteration, so a
    // whole-test done_order compare there is not meaningful.
    local function void check_interrupts();
        int d[$], r[$];
        if (ref_model == null || dut_model == null) return;
        d = dut_model.irqc.done_order;
        r = ref_model.irqc.done_order;
        if (dut_model.irqc.n_done != ref_model.irqc.n_done) begin
            errors++;
            `uvm_error("CMP", $sformatf("done count: DUT=%0d != REF=%0d",
                                        dut_model.irqc.n_done, ref_model.irqc.n_done))
        end
        if (dut_model.irqc.n_err != ref_model.irqc.n_err) begin
            errors++;
            `uvm_error("CMP", $sformatf("error count: DUT=%0d != REF=%0d",
                                        dut_model.irqc.n_err, ref_model.irqc.n_err))
        end
        if (!m_check_order) begin
            // Equal-priority: order is scheduling freedom -- check only that the
            // SAME SET of channels completed (sorted compare), not the sequence.
            d.sort(); r.sort();
        end
        if (d.size() != r.size()) begin
            errors++;
            `uvm_error("CMP", $sformatf("done_order length: DUT=%p REF=%p", d, r))
            return;
        end
        foreach (r[i])
            if (d[i] != r[i]) begin
                errors++;
                `uvm_error("CMP", $sformatf("done%s[%0d]: DUT ch%0d != REF ch%0d (DUT=%p REF=%p)",
                                            m_check_order ? "_order" : "_set", i, d[i], r[i], d, r))
            end
    endfunction

    // Any leftover beat means one side emitted more than the other for that key.
    local function void check_final();
        foreach (q_dut[k])
            if (q_dut[k].size() > 0) begin
                errors++;
                `uvm_error("CMP", $sformatf("key %s: DUT has %0d unmatched (extra) beats; first: %s",
                                            k, q_dut[k].size(), q_dut[k][0].convert2string()))
            end
        foreach (q_ref[k])
            if (q_ref[k].size() > 0) begin
                errors++;
                `uvm_error("CMP", $sformatf("key %s: REF has %0d unmatched (extra) beats; first: %s",
                                            k, q_ref[k].size(), q_ref[k][0].convert2string()))
            end
        if (matched == 0)
            `uvm_warning("CMP", "no transactions were matched -- comparator saw an empty stream")
    endfunction

    // Final backing-store equality: every address the reference wrote must equal
    // the DUT's memory. (Both models ran the same transfers; this catches a data
    // divergence the stream compare might miss if the write payload matched but a
    // later beat overwrote it differently.)
    local function void check_memory();
        if (ref_model == null || dut_model == null) return;
        for (int sel = 0; sel <= 1; sel++) begin   // int index: a 1-bit counter never exits
            wb_dma_mem rm = ref_model.mem(sel[0]);
            wb_dma_mem dm = dut_model.mem(sel[0]);
            foreach (rm.mem[a]) begin
                logic [31:0] r = rm.mem[a];
                logic [31:0] d = dm.peek(a);
                if (d !== r) begin
                    errors++;
                    `uvm_error("CMP", $sformatf("memory[IF%0d][0x%08h]: DUT=0x%08h != REF=0x%08h",
                                                sel, a, d, r))
                end
            end
        end
    endfunction
endclass
