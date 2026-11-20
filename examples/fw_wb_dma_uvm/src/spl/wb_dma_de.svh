// DMA engine / data mover -- the one runnable in the model. It owns the engine's
// EDGE ports (two master memory interfaces + the interrupt sink + per-channel HW
// handshake) and runs the service loop: pick a ready channel, transfer one chunk
// through the fw_mem_if master ports, update the channel's working/status state,
// and raise interrupts. One pass services one chunk and then re-arbitrates (spec
// §3.2.1/§3.2.2 per-chunk re-arbitration).
//
// Arbitration is register-centric: readiness and priority come straight from the
// channel CSR fields (busy/stop/err/prio) plus the global PAUSE, so the engine
// arbitrates locally -- no separate arbiter object. An HW-handshake channel is
// additionally gated on a pending request: it is "ready" only once its device has
// asserted dma_req, so a stalled HS device never blocks service of other channels
// (no head-of-line blocking -- the engine never commits to a channel it would
// then block inside).
//
// When nothing is ready the engine does not spin on a delay. It names the set of
// things it waits on as one `fw_event_set` (m_wake) -- a HETEROGENEOUS set of
// `fw_awaitable_if` event sources: every CSR (a host write produces into it) plus
// every connected HS port (a dma_req produces into it). The wait site is simply
// m_wake.wait_any() (a single @event, O(1)). Sources PUSH their "event occurred"
// into the set, so there is no per-wait fork; this produce/monitor split is what
// lowers to RTL (the set's event is the OR of its members' fired pulses, and the
// wait site is a process sensitive to it).
//
// The engine speaks the Wishbone access API (wb_proto_if) on its master ports;
// the signal-level transactors at the module boundary bridge that to WB pins.
class wb_dma_de extends fw_component implements fw_runnable;
    wb_dma_rf         rf;
    wb_dma_hs_if      m_hs[];     // resolved per-channel HS handles (null = unconnected)
    int unsigned      last_ch;    // round-robin pointer (last channel serviced)
    // Loosely-timed temporal-decoupling keeper (null unless the data path uses
    // one, i.e. the TLM flavour). The engine does NOT generate time; the memories
    // ACCOUNT their data-availability delays into it, and the engine only FLUSHES
    // (sync) that accumulated real delay at its re-arbitration synchronization
    // point -- where it must observe the latest register state (CPU channel arming).
    fw_quantum_keeper qk;

    // Edge ports.
    fw_port #(wb_proto_if #(32, 32)) mif0, mif1;   // WISHBONE IF0 / IF1 masters (data)
    fw_port #(wb_dma_irq_if)  irq;          // interrupt-cause sink
    fw_port #(wb_dma_hs_if)   hs[];         // per-channel HW handshake

    // `fw_component_type_begin(wb_dma_de)
    // `fw_component_type_end


    function new(string name, fw_component parent, wb_dma_rf rf);
        super.new(name, parent);
        this.rf = rf;
        parent.add_runnable(this);          // active component: opt in to run()
    endfunction

    function void build();
        mif0 = new("mif0", this);
        mif1 = new("mif1", this);
        irq  = new("irq",  this);
        hs   = new[rf.n_ch];
        foreach (hs[i]) hs[i] = new($sformatf("hs%0d", i), this);
        m_hs = new[rf.n_ch];
        last_ch = 0;
    endfunction

    // ---- service loop --------------------------------------------------------
    virtual task run();
        wb_proto_if #(32, 32) if0 = mif0.get_if();
        wb_proto_if #(32, 32) if1 = mif1.get_if();
        fw_event_set      m_wake;     // re-arbitrate: a watched CSR write OR an HS request

        // de.irq is an OPTIONAL cause seam (like the hs[] ports): resolve it only
        // when a scoreboard is connected; raise() null-guards it. The interrupt
        // LEVELS do not depend on it -- they flow through rf.signal_int below.
        wb_dma_irq_if  irqif = irq.is_connected() ? irq.get_if() : null;

        // Collect the heterogeneous wait list -- every CSR plus every connected HS
        // port -- into the wake set. Each entry is just an fw_awaitable_if; the loop
        // is source-agnostic.
        // Named and parented: the engine's one blocking point is the first thing
        // you want to see when the DMA appears to do nothing, and `this` is what
        // supplies the debug context (the set is created in run(), after
        // do_build(), so it pulls the resolved context rather than waiting to be
        // handed one).
        m_wake = new("de.wake", this);
        m_wake.add(rf.regs.csr);                            // global CSR (PAUSE)
        foreach (rf.ch[i]) m_wake.add(rf.ch[i].regs.csr);
        foreach (hs[i]) if (hs[i].is_connected()) begin
            m_hs[i] = hs[i].get_if();                       // also cache for has_req()
            m_wake.add(m_hs[i]);
        end

        forever begin
            automatic int c;
            `fw_op_span(sop)
            // Row 11 (`de.service`): one arbitration round, from re-arbitration
            // to the end of whatever it decided to do -- INCLUDING the wait when
            // nothing was ready. Opening before `next()` rather than after is
            // what makes an idle round a zero-work span of measurable duration
            // instead of a gap, and it gives the hang dump a named frame to
            // report ("inside de.service") rather than bare `.run`.
            //
            // The winner is not known at open, so it arrives by retro-tag.
            `fw_op_begin(dbg_if(), FW_L_OP, "de.service", sop)
            `fw_op_opened
            // Flush accumulated data-availability delays BEFORE re-arbitrating, so
            // the engine observes the latest register state (the CPU's channel
            // arming) at this synchronization point. This is NOT time generation:
            // the flushed time IS the sum of the just-serviced chunk's data delays.
            if (qk != null) qk.sync();
            c = next();          // emits de.arb -- winner, ready-vector, loser reasons
            // Only a round that GRANTED belongs to a channel. An idle round is
            // deliberately left with no flow key and no subject, for the same
            // reason `de.arb` names no subject when nobody wins: "nothing was
            // serviced" is not an event about a channel, and tagging it with one
            // would put it in the wrong bucket for every downstream filter.
            if (c >= 0) begin
                `fw_op_retag(sop, c)
                `fw_op_subject(sop, c)
            end
            if (c < 0) begin
                m_wake.wait_any();           // sleep until a CSR write or an HS request
            end else begin
                service_chunk(c, if0, if1, irqif);
            end
            `fw_op_close(dbg_if(), sop)
                `fw_field_i8_as("winner", c)
                `fw_op_outcome((c < 0) ? FW_OUT_STALL : FW_OUT_OK)
            `fw_op_end(sop)
        end
    endtask

    // ---- register-centric arbitration (spec §3.1) ----------------------------
    // A channel is ready when it is busy (armed/loaded, the CSR status bit) and
    // neither stopped nor errored. An HW-handshake channel is ALSO gated on a
    // pending dma_req, so the engine never picks (and then blocks on) an HS
    // channel whose device has not yet requested a chunk.
    local function bit ready(int i);
        wb_dma_csr_t csr = rf.ch[i].regs.csr.read();
        if (!csr.busy || csr.stop || csr.err) return 1'b0;
        if (!csr.mode) return 1'b1;                   // SW channel: ready
        if (m_hs[i] == null) return 1'b0;             // HS channel needs a device...
        return m_hs[i].has_req();                     // ...and a pending request
    endfunction

    // WHY channel i is not ready -- the same guard as ready(), reported instead of
    // returning. Codes are site-local and 1-based, so 0 keeps its "this candidate
    // won" meaning; the site's field schema is what names them:
    //   1 not_busy   -- not armed (no transfer loaded)
    //   2 stopped    -- host wrote STOP
    //   3 errored    -- a bus error aborted it
    //   4 no_hs_dev  -- HW-handshake channel with no device connected
    //   5 no_hs_req  -- HW-handshake channel whose device has not requested
    //   6 lower_prio -- ready, but a higher-priority channel was available
    //   7 lost_rr    -- ready and at the winning priority, but round-robin
    //                   reached another channel first
    //   8 paused     -- the global PAUSE bit stopped the whole engine
    local function int loss_reason(int i, int best_pri, int winner, bit paused);
        wb_dma_csr_t csr = rf.ch[i].regs.csr.read();
        if (i == winner)      return 0;
        if (paused)           return 8;
        if (!csr.busy)        return 1;
        if (csr.stop)         return 2;
        if (csr.err)          return 3;
        if (csr.mode) begin
            if (m_hs[i] == null)   return 4;
            if (!m_hs[i].has_req()) return 5;
        end
        if (int'(csr.prio) < best_pri) return 6;
        return 7;
    endfunction

    function int prio_of(int i);  return int'(rf.ch[i].regs.csr.read().prio);  endfunction

    // Index of the next channel to service, or -1 if none (or globally paused):
    // highest CSR priority first, then round-robin among equal-priority readies.
    local function int next();
        int n        = rf.n_ch;
        int best_pri = -1;
        int winner   = -1;
        bit paused   = rf.paused();

        if (!paused) begin
            foreach (rf.ch[i])
                if (ready(i) && prio_of(i) > best_pri)
                    best_pri = prio_of(i);

            if (best_pri >= 0) begin
                for (int k = 1; k <= n; k++) begin
                    int idx = (last_ch + k) % n;
                    if (ready(idx) && prio_of(idx) == best_pri) begin
                        last_ch = idx;
                        winner  = idx;
                        break;
                    end
                end
            end
        end

        // The arbitration record: the winner AND why every loser lost.
        //
        // This is the site the design doc singles out, and the reason is that the
        // ready-vector is computed here and then thrown away. Nothing downstream
        // can reconstruct it: a channel that is armed but never serviced leaves no
        // trace at all, so "channel 3 is starving" and "channel 3 was never
        // actually ready" are indistinguishable from the outside -- and they have
        // completely different fixes. `ready_vec` mirrors the RTL's
        // wb_dma_ch_arb.req[30:0], which is what lets the same record be
        // reconstructed from a waveform (design §11).
        `fw_ev_decision(dbg_if(), FW_L_OP, "de.arb")
            `fw_field_b(paused)
            `fw_field_u8(last_ch)
            `fw_field_i8(best_pri)
            `fw_field_h32_as("ready_vec", ready_vec())
            if (winner >= 0) begin
                `fw_dec_winner(winner)
                // The GRANTED channel is this round's endpoint, which is what
                // makes `+fw_dbg_subj=de.arb:1,2` mean "the rounds that served
                // channel 1 or 2". A round that grants nobody deliberately
                // names no subject and therefore survives every subject filter:
                // "nothing was serviced" is the answer to a different question,
                // and quietly filtering it out would hide the case someone
                // narrowing the trace is most likely hunting.
                `fw_subject(winner)
            end
            foreach (rf.ch[i]) begin
                `fw_dec_cand(i, loss_reason(i, best_pri, winner, paused))
            end
        `fw_ev_end

        return winner;
    endfunction

    // The per-channel ready bitmap, in the RTL's shape (wb_dma_ch_arb.req[30:0]).
    // Built only inside an enabled arbitration site -- it costs a read of every
    // channel CSR, which is exactly the kind of payload the emit macros exist to
    // keep out of a disabled build.
    local function bit [30:0] ready_vec();
        bit [30:0] v = '0;
        foreach (rf.ch[i]) if (i < 31 && ready(i)) v[i] = 1'b1;
        return v;
    endfunction

    // Transfer one chunk of channel c (CHK_SZ words, or all of TOT_SZ when
    // CHK_SZ==0), then settle the channel (chunk-int / done / ARS / error).
    //
    // Row 14 (`de.chunk`). This wrapper exists ONLY to make the span
    // correct-by-construction: the body has four early exits (two bus errors, a
    // host stop, and the chunk-boundary return), and a span closed by hand at
    // each of them is a span that will eventually be closed at three of them.
    // A leaked span is worse than no span -- it holds a pooled context forever
    // and emits no close, so the operation simply vanishes from the trace.
    //
    // Parameters are what is known at ENTRY. `n` (the words this chunk plans to
    // move) is not: it depends on the HS request that `do_chunk` waits for, and
    // opening the span after that wait would hide the wait itself -- which is
    // exactly the "is the device slow or is the DMA slow" question row 15
    // exists to answer. So `planned` rides on the close, beside `moved`.
    local task automatic service_chunk(int c,
                                       wb_proto_if #(32, 32) if0, wb_proto_if #(32, 32) if1,
                                       wb_dma_irq_if irqif);
        wb_dma_ch       ch  = rf.ch[c];
        wb_dma_csr_t    csr = ch.regs.csr.read();
        wb_dma_sz_t     sz  = ch.regs.sz.read();
        fw_op_outcome_e oc  = FW_OUT_OK;
        int             planned = 0, moved = 0;
        realtime        lt0 = 0;
        `fw_op_span(op)

        `fw_op_begin(dbg_if(), FW_L_OP, "de.chunk", op)
            `fw_subject(c)
            `fw_op_flow(c)
            `fw_field_u8_as("ch", c)
            `fw_field_u32_as("chk_sz", sz.chk_sz)
            `fw_field_u32_as("w_rem", ch.w_rem)
            `fw_field_b_as("hs", csr.mode)
            lt0 = (qk != null) ? qk.offset() : 0;   // inside the gate: G2 holds
        `fw_op_opened

        do_chunk(c, if0, if1, irqif, oc, planned, moved);

        `fw_op_close(dbg_if(), op)
            `fw_field_u32(planned)
            `fw_field_u32(moved)
            // TEMPORAL DECOUPLING makes `dur` a lie on the TLM path, and saying
            // so is better than shipping a number nobody can trust.
            //
            // The engine never advances time; the memories ACCOUNT their
            // data-availability delays into the quantum keeper, and the engine
            // FLUSHES them at the next round's sync. So `$time` does not move
            // inside a chunk and `dur` reads 0 -- the cost is real, it just
            // lands on the following `de.service` span.
            //
            // `accounted` is that cost, measured where it is incurred. On the
            // wb/rtl paths there is no keeper, `accounted` is 0, and `dur` is
            // the honest number. Both are emitted because which one is
            // meaningful depends on the view, and the stream should not require
            // the reader to know which view produced it.
            `fw_field_u64_as("accounted", ((qk != null) ? qk.offset() : 0) - lt0)
            `fw_op_outcome(oc)
        `fw_op_end(op)
    endtask

    // The chunk body. Reports how it ended through `oc` so the span above can
    // close on every path; `planned`/`moved` are the two numbers whose
    // disagreement IS the diagnosis for a short chunk.
    local task automatic do_chunk(int c,
                                  wb_proto_if #(32, 32) if0, wb_proto_if #(32, 32) if1,
                                  wb_dma_irq_if irqif,
                                  output fw_op_outcome_e oc,
                                  output int planned, output int moved);
        wb_dma_ch    ch  = rf.ch[c];
        wb_dma_csr_t csr = ch.regs.csr.read();           // config snapshot for this chunk
        wb_dma_sz_t  sz  = ch.regs.sz.read();
        automatic bit nd = 0, rest = 0;
        automatic int n;

        oc = FW_OUT_OK;  planned = 0;  moved = 0;

        // HW-handshake mode: consume the chunk request the arbiter gated on (it is
        // already pending -- ready() required has_req() -- so wait_req returns at
        // once), latching its nd/rest qualifiers.
        if (csr.mode) begin
            // Row 15 (`de.hs_req`). The arbiter only picks an HS channel whose
            // device has already requested, so this normally returns at once and
            // the span is ~zero. When it is NOT zero, that duration is the whole
            // answer to "is the device slow or is the DMA slow" -- a question
            // nothing else in the stream can settle, because a slow device and a
            // busy engine look identical from outside.
            `fw_op_span(hsop)
            `fw_op_begin(dbg_if(), FW_L_OP, "de.hs_req", hsop)
                `fw_subject(c)
                `fw_op_flow(c)
                `fw_field_u8_as("ch", c)
            `fw_op_opened
            m_hs[c].wait_req(nd, rest);
            `fw_op_close(dbg_if(), hsop)
                `fw_field_b(nd)
                `fw_field_b(rest)
            `fw_op_end(hsop)
            if (rest && csr.rest_en) ch.load_working();
        end

        n = (sz.chk_sz == 0) ? ch.w_rem : min2(int'(sz.chk_sz), ch.w_rem);
        planned = n;
        // BUSY is not re-asserted here: arm() set it, ready() required it to get
        // this far, and nothing clears it between chunks (the ARS reload keeps
        // it set). Writing it every chunk only added a masked register write.

        // The engine NEVER advances time itself -- it is a pure data mover. All
        // timing emerges from data-availability delays on the master access()
        // calls (the memory / bus paces each word). Those delays are quantum-
        // decoupled on the TLM path (see wb_dma_mem), so the account is cheap.

        for (int i = 0; i < n; i++) begin
            automatic bit [31:0] data;
            automatic bit        berr;
            if (swptr_stall(ch)) begin
                // FIFO backpressure (§3.5): the chunk ends EARLY, short of the n
                // words it planned to move, and the only evidence is a `break`.
                // Without this the channel simply appears slow.
                `fw_ev_decision(dbg_if(), FW_L_OP, "de.swptr_stall")
                    `fw_subject(c)
                    `fw_field_u8_as("ch", c)
                    `fw_field_u32_as("moved", i)
                    `fw_field_u32_as("planned", n)
                    `fw_field_h32_as("swptr", ch.regs.swptr.read())
                    `fw_field_h32_as("src", ch.w_src)
                    `fw_field_h32_as("dst", ch.w_dst)
                `fw_ev_end
                moved = i;                            // short of `planned`
                oc    = FW_OUT_STALL;                 // ...and the close says why
                break;                                // stop at software ptr
            end
            begin
                automatic bit [31:0] wdr;   // write response data (ignored)
                wb_proto_if #(32, 32) src = csr.src_sel ? if1 : if0;
                wb_proto_if #(32, 32) dst = csr.dst_sel ? if1 : if0;
                // Row 17 (`de.beat`): one word, both bus accesses.
                //
                // FW_L_DETAIL, not FW_L_OP, and that is not a small choice: this
                // fires once per word, so at the default level it is absent and
                // at level 4 a single large transfer will dominate the stream.
                // That is the correct trade -- per-beat latency attribution is
                // exactly what you want when you have narrowed the question to
                // one chunk, and exactly what you do not want before then.
                //
                // It also opens BEFORE the accesses and closes after, so once
                // D-6 splices the bus edge the two master operations nest inside
                // it and per-beat latency attribution costs nothing extra.
                `fw_op_span(bop)
                `fw_op_begin(dbg_if(), FW_L_DETAIL, "de.beat", bop)
                    `fw_subject(c)
                    `fw_op_flow(c)
                    `fw_op_flow(i)
                    `fw_field_u32_as("word", i)
                    `fw_field_h32_as("src", ch.w_src)
                    `fw_field_h32_as("dst", ch.w_dst)
                `fw_op_opened
                src.access(ch.w_src, 32'h0, 4'hf, 1'b0, data, berr);   // read
                if (berr) begin
                    // The channel errors and raises an interrupt, but the ADDRESS
                    // that faulted is consumed here and never stored anywhere --
                    // and it is the only thing that tells you whether the source
                    // pointer, the increment, or the memory map is wrong.
                    // Read and write get SEPARATE sites rather than one site with
                    // a phase field: the direction is the first thing you want to
                    // know, and a site name carries it for free.
                    `fw_error_begin(dbg_if(), "de.berr_rd")
                        `fw_subject(c)
                        `fw_field_u8_as("ch", c)
                        `fw_field_h32_as("addr", ch.w_src)
                        `fw_field_u32_as("word", i)
                    `fw_ev_end
                    moved = i;
                    oc    = FW_OUT_ERROR;
                    `fw_op_close(dbg_if(), bop)
                        `fw_op_outcome(FW_OUT_ERROR)
                    `fw_op_end(bop)
                    fail(ch, irqif); return;
                end
                dst.access(ch.w_dst, data, 4'hf, 1'b1, wdr, berr);     // write
                if (berr) begin
                    `fw_error_begin(dbg_if(), "de.berr_wr")
                        `fw_subject(c)
                        `fw_field_u8_as("ch", c)
                        `fw_field_h32_as("addr", ch.w_dst)
                        `fw_field_u32_as("word", i)
                    `fw_ev_end
                    moved = i;
                    oc    = FW_OUT_ERROR;
                    `fw_op_close(dbg_if(), bop)
                        `fw_op_outcome(FW_OUT_ERROR)
                    `fw_op_end(bop)
                    fail(ch, irqif); return;
                end
                `fw_op_close(dbg_if(), bop)
                    `fw_field_h32(data)
                `fw_op_end(bop)
            end
            if (csr.inc_src) ch.advance_src();
            if (csr.inc_dst) ch.advance_dst();
            ch.w_rem--;
            // STOP is host-writable mid-transfer, so read it fresh (§4.4.1).
            if (ch.regs.csr.read().stop) begin
                // A host abort mid-transfer. The channel ends up errored, which is
                // indistinguishable from a bus error unless the abort itself says
                // so -- and "did software stop this, or did the memory fault?" is
                // the first fork in diagnosing a failed transfer.
                `fw_warn_begin(dbg_if(), "de.host_stop")
                    `fw_subject(c)
                    `fw_field_u8_as("ch", c)
                    `fw_field_u32_as("moved", i + 1)
                    `fw_field_u32_as("planned", n)
                    `fw_field_u32_as("remaining", ch.w_rem)
                `fw_ev_end
                moved = i + 1;
                oc    = FW_OUT_ABORTED;
                fail(ch, irqif); return;
            end
        end
        // Only the paths that ran to completion moved all `n`; the early exits
        // above each recorded what they actually got through.
        if (oc == FW_OUT_OK) moved = n;

        if (csr.mode) begin
            m_hs[c].ack();                               // acknowledge the chunk (§3.8)
        end

        // More chunks remain in this transfer: optional chunk interrupt, then
        // re-arbitrate (return to the loop).
        if (sz.chk_sz != 0 && ch.w_rem != 0) begin
            if (csr.ine_chk_done) raise(ch, c, CAUSE_CHUNK, irqif);
            return;
        end

        // TOT_SZ exhausted -> transfer complete. ARS is capability-gated (App. A).
        if ((ch.cap_ars & csr.ars) && !ch.regs.csr.read().stop) begin
            ch.load_working();
        end else begin
            ch.set_busy(1'b0); ch.set_done(1'b1);
            ch.clr_en();                                 // HW clears CH_EN on done
            // Row 23 (`de.xfer_done`): the close of the TRANSFER, which is not a
            // span any one thread holds -- it is opened by `rf.arm` on the host
            // thread and closed here on the engine thread. That is precisely
            // what derived flow keys are for: the two records carry the same
            // key and the join happens downstream, with no handle passing
            // between the actors (a handle could never be reconstructed from
            // RTL, and the whole point of the key is that it can).
            //
            // `chunks` is deliberately NOT a field: it is the number of
            // `de.chunk` closes carrying this flow key, which a decoder counts.
            // Emitting it would be emitting the recoverable (G5).
            `fw_ev_op(dbg_if(), FW_L_TXN, "de.xfer_done", FW_OP_ENDED)
                `fw_subject(c)
                `fw_op_flow(c)
                `fw_field_u8_as("ch", c)
                `fw_field_u32_as("words", sz.tot_sz)
                `fw_op_outcome(FW_OUT_OK)
            `fw_ev_end
            if (csr.ine_done) raise(ch, c, CAUSE_DONE, irqif);
        end
    endtask

    // Abort the channel with an error (bus error or host STOP).
    local task fail(wb_dma_ch ch, wb_dma_irq_if irqif);
        ch.clr_stop();
        ch.set_busy(1'b0); ch.set_err(1'b1);
        ch.clr_en();                                     // HW clears CH_EN on error
        if (ch.regs.csr.read().ine_err) raise(ch, int'(ch.id), CAUSE_ERR, irqif);
    endtask

    // Latch the interrupt source and publish the cause.
    local task raise(wb_dma_ch ch, int c, wb_dma_cause_e cause,
                     wb_dma_irq_if irqif);
        case (cause)
            CAUSE_CHUNK: ch.set_int_chunk();
            CAUSE_DONE:  ch.set_int_done();
            CAUSE_ERR:   ch.set_int_err();
            default: ;
        endcase
        // Row 24 (`de.raise_int`): cause publication, emitted HERE rather than
        // in rf because this is the only place the cause is known -- rf sees a
        // channel index and nothing else. It is the head of the interrupt
        // chain, and the chain is only readable end to end if every link says
        // what it knows: cause here, source latch in the CSR's own state-change
        // event (and the aggregate in rf.int_pending_set), level move in
        // rf.int_level.
        `fw_ev_state(dbg_if(), FW_L_TXN, "de.raise_int")
            `fw_subject(c)
            `fw_field_u8_as("ch", c)
            `fw_field_u8_as("cause", int'(cause))     // 0 chunk, 1 done, 2 err
            `fw_state_change(0, longint'(cause), FW_ACTOR_HW, 0)
        `fw_ev_end
        // The source is already latched -- set_int_*() above wrote the CSR bit,
        // which IS the pending source. This only republishes the level.
        rf.signal_int();                                 // moves the level (rf int seam)
        if (irqif != null)                               // publish the cause (optional sink)
            irqif.raise('{channel: c[4:0], cause: cause});
    endtask

    // FIFO software-pointer stall (§3.5): the address bound for IF0 may not cross
    // the enabled software pointer. Approximate: compare the IF0-side working
    // address (whichever of src/dst targets IF0) to the pointer.
    local function bit swptr_stall(wb_dma_ch ch);
        bit [31:0]     ptr, a0;
        wb_dma_swptr_t sp  = ch.regs.swptr.read();       // .en / .ptr from the RDL
        wb_dma_csr_t   csr = ch.regs.csr.read();
        if (!sp.en) return 1'b0;
        ptr = {1'b0, sp.ptr} & 32'hffff_fffc;            // word-align
        if (csr.src_sel == 1'b0)      a0 = ch.w_src;
        else if (csr.dst_sel == 1'b0) a0 = ch.w_dst;
        else                          return 1'b0;       // neither side on IF0
        return (a0 & 32'h7fff_fffc) == ptr;
    endfunction

    local function int min2(int a, int b); return (a < b) ? a : b; endfunction
endclass
