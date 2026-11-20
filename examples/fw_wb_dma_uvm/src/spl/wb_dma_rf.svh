// Register file: the host-facing front end of the DMA. It keeps its protocol-
// independent fw_mem_if host port (the CPU programs registers through it; on the
// bus this is reached via a Wishbone slave + std_to_wb at the boundary) but now
// BACKS that port with the fw-hdl register model: a `wb_dma_regs` register block
// (globals + a per-channel sub-block array) decodes a byte offset to the target
// register and applies the documented access semantics (sw/hw write masks,
// read-to-clear, the CH_EN-start hardware hook) declaratively. The engine
// (wb_dma_de) reads/mutates the channels' working state directly; this component
// does not run a process.
//
// The block itself -- `wb_dma_regs`, globals plus the per-channel bank array --
// is GENERATED from src/rdl/*.rdl into wb_dma_reg_pkg (dv-flow task
// `reg-model`); this file holds only the behaviour wrapped around it.
//
// INT_SRC_A/B are hardware-driven registers (sw_wmask 0) whose value a read
// PROVIDER supplies at read time -- see wb_dma_int_src_view below.

typedef class wb_dma_rf;

// Hardware write-observer on a channel CSR (the register model's `fw_reg_wr_if`
// hook -- its canonical use is exactly "CH_EN was set -> start the engine"). It
// fires after a committed host write; a rising CH_EN arms the channel.
class wb_dma_ch_start_obs implements fw_reg_wr_if #(32);
    local wb_dma_rf m_rf;
    local int       m_idx;
    function new(wb_dma_rf rf, int idx); m_rf = rf; m_idx = idx; endfunction
    virtual task on_write(input bit [31:0] val, input bit [31:0] prev);
        m_rf.on_ch_csr_write(m_idx, val, prev);
    endtask
endclass

// Hardware read provider for INT_SRC_A/B (the register model's `fw_reg_rd_if`
// hook). INT_SRC is a masked snapshot -- int_srca = int_maska_r & ch_int is a
// WIRE in the RTL, not a flop -- so the register stores nothing and the value
// is computed at read time from the engine's pending vector and this bank's
// mask. A provider, not a stored value pushed on every change: a snapshot has
// to be exact at the instant of the read, and there is no update site that can
// guarantee that for a value depending on two independently-moving inputs.
//
// on_read() is a pure function with no side effects, which is the other half of
// the contract: unlike a channel CSR read, reading INT_SRC clears nothing.
class wb_dma_int_src_view implements fw_reg_rd_if #(32);
    local wb_dma_rf m_rf;
    local bit       m_bank_b;      // 0 = bank A (inta_o), 1 = bank B (intb_o)
    function new(wb_dma_rf rf, bit bank_b); m_rf = rf; m_bank_b = bank_b; endfunction
    virtual function bit [31:0] on_read(input bit [31:0] stored);
        return {1'b0, m_rf.int_pending() & (m_bank_b ? m_rf.regs.int_msk_b.read().ch
                                                     : m_rf.regs.int_msk_a.read().ch)};
    endfunction
endclass

// The top register file `wb_dma_regs` is generated into wb_dma_reg_pkg from
// src/rdl/wb_dma.rdl. Its per-channel banks are the same `wb_dma_ch_regs`
// objects the engine-facing `wb_dma_ch` objects hold views of, nested there so
// one offset decode spans the whole map.
//
// The bank array is sized at BUILD time by the RDL's NUM_CH parameter (4, set
// in src/rdl/flow.yaml), which is the RTL's `ch_count`. The runtime `n_ch`
// below is a different quantity -- how many of those channels this instance
// uses -- and must not exceed it. Same split the PSS model makes between
// WB_DMA_MAX_CH and wb_dma_c.num_ch.

class wb_dma_rf extends fw_component;
    int unsigned n_ch;
    wb_dma_ch    ch[];
    wb_dma_regs  regs;                 // the MMIO register file (register model)

    // Interrupt-change notification (the wb_dma_int_if seam): sinks are called
    // whenever the aggregate {intb,inta} level moves. Purely observational -- with
    // no sinks registered, signal_int() is a cheap no-op (existing TBs unaffected).
    wb_dma_int_if    m_int_sinks[$];
    local bit [1:0]  m_last_int;       // {intb, inta} last published
    local bit [30:0] m_last_pend;      // int_pending() last observed (edge detect only)

    // Provided host port: the Wishbone access() seam redirects to host_access(),
    // which dispatches to the register-block host_write()/host_read() helpers.
    `FW_WB_PROTO_IMP(32, 32, wb_dma_rf, host);

    function new(string name, fw_component parent, int unsigned n_ch = 4);
        super.new(name, parent);
        this.n_ch = n_ch;
        // The MAP first -- it owns the whole address space including the channel
        // banks -- then the channel objects, each handed a view of its region.
        // The map's shape is fixed by the RDL, so `new` takes no channel count;
        // n_ch selects how many of its banks this instance drives.
        regs = new("regs");
        if (n_ch > $size(regs.bank))
            $fatal(1, "%m: n_ch=%0d exceeds the generated bank count %0d (raise NUM_CH in src/rdl/flow.yaml)",
                   n_ch, $size(regs.bank));
        ch   = new[n_ch];
        foreach (ch[i]) ch[i] = new(i, regs.bank[i]);
        m_last_pend = '0;
    endfunction

    function void build();
        wb_dma_int_src_view src_a = new(this, 1'b0);
        wb_dma_int_src_view src_b = new(this, 1'b1);
        host = new(this);
        // Wire the CH_EN-start hardware hook onto every channel CSR.
        foreach (ch[i]) begin
            wb_dma_ch_start_obs obs = new(this, i);
            ch[i].regs.csr.add_wr(obs);
        end
        // ... and the masked-snapshot read providers onto INT_SRC_A/B.
        regs.int_src_a.set_rd(src_a);
        regs.int_src_b.set_rd(src_b);
    endfunction

    function bit  paused();   return regs.csr.read().pause;                           endfunction

    // The per-channel pending-interrupt-source vector, in the RTL's ch_int
    // shape. DERIVED, not stored: a channel's pending source IS its latched CSR
    // interrupt bits. The engine sets them (ch.set_int_*, hw_wmask) and the
    // generated rclr_mask clears them on a host CSR read, both through the
    // register model -- so a shadow vector could only ever agree with this or
    // be a bug.
    //
    // NOT re-masked by the ine_* enables: those gate whether the source latches
    // at all and the engine applies them at its three raise sites, but clearing
    // an enable after the bit latched does not retract it (the RTL flop stays
    // set).
    function bit [30:0] int_pending();
        int_pending = '0;
        foreach (ch[i]) begin
            wb_dma_csr_t c = ch[i].regs.csr.read();
            if (i < 31) int_pending[i] = c.int_err | c.int_done | c.int_chk_done;
        end
    endfunction

    // Level interrupt outputs: any pending source enabled by the mask.
    function bit  inta();     return |(int_pending() & regs.int_msk_a.read().ch);     endfunction
    function bit  intb();     return |(int_pending() & regs.int_msk_b.read().ch);     endfunction

    // Interrupt-change seam: register a sink, and publish the aggregate level to
    // all sinks whenever it changes. Called after every event that can move the
    // {intb,inta} level (raise, channel-CSR read-clear, mask write).
    function void add_int_sink(wb_dma_int_if s); m_int_sinks.push_back(s); endfunction
    function void signal_int();
        bit [30:0] pend  = int_pending();
        bit [30:0] msk_a = regs.int_msk_a.read().ch;
        bit [30:0] msk_b = regs.int_msk_b.read().ch;
        bit        a     = |(pend & msk_a);
        bit        b     = |(pend & msk_b);

        // Rows 7/8: the two edges of the pending-source VECTOR, diffed here
        // because the vector is derived and this is the only place it is
        // materialised. The register model already reports each individual bit
        // as a CSR state change with actor attribution; the aggregate is what
        // those cannot give -- "channel 3 raised while 1 was already up", and
        // the deassert that happens because something merely READ a CSR.
        //
        // `ch` is the lowest changed bit; every caller moves exactly one channel.
        if ((pend & ~m_last_pend) != 0) begin
            `fw_ev_state(dbg_if(), FW_L_TXN, "rf.int_pending_set")
                `fw_subject(lowest_bit(pend & ~m_last_pend))
                `fw_field_u8_as("ch", lowest_bit(pend & ~m_last_pend))
                `fw_state_change(longint'(m_last_pend), longint'(pend), FW_ACTOR_HW, 0)
            `fw_ev_end
        end
        if ((m_last_pend & ~pend) != 0) begin
            `fw_ev_state(dbg_if(), FW_L_TXN, "rf.int_pending_clear")
                `fw_subject(lowest_bit(m_last_pend & ~pend))
                `fw_field_u8_as("ch", lowest_bit(m_last_pend & ~pend))
                `fw_field_u8_as("by", 0)         // 0 = csr_read, 1 = reset
                `fw_state_change(longint'(m_last_pend), longint'(pend), FW_ACTOR_SW, 0)
            `fw_ev_end
        end
        m_last_pend = pend;

        if ({b, a} !== m_last_int) begin
            // Row 9 (`int_level`): the observable pin edge. The two per-bank AND
            // terms ride along because "the line moved" and "WHICH bank moved it
            // and why" are the same question in practice, and recomputing the
            // terms offline needs the mask values at that instant -- which no
            // other record carries.
            `fw_ev_state(dbg_if(), FW_L_TXN, "rf.int_level")
                `fw_state_change(longint'(m_last_int), longint'({b, a}), FW_ACTOR_HW, 0)
                `fw_field_h32_as("pending", pend)
                `fw_field_h32_as("term_a", pend & msk_a)
                `fw_field_h32_as("term_b", pend & msk_b)
            `fw_ev_end
            m_last_int = {b, a};
            foreach (m_int_sinks[i]) m_int_sinks[i].int_changed(a, b);
        end else if (pend != 0) begin
            // Row 10 (`int_level_stable`): "why did the line NOT assert". The
            // most common real answer is a mask -- the source latched into the
            // bank the handler is not watching -- and until now that produced
            // no record at all, because nothing happened.
            //
            // Gated on a pending source deliberately. signal_int() runs after
            // EVERY host access, and "the line did not move, and nothing was
            // pending" needs no explanation; emitting it would bury the case
            // that does. A diagnostic that fires when there is nothing to
            // diagnose is how a diagnostic gets filtered out and then ignored.
            //
            // Loss reasons (1-based; 0 keeps its "this one won" meaning):
            //   1 masked_out -- pending in neither bank's mask
            //   2 already_up -- the level is asserted; this source adds nothing
            `fw_ev_decision(dbg_if(), FW_L_OP, "rf.int_level_stable")
                `fw_field_h32_as("pending", pend)
                `fw_field_h32_as("msk_a", msk_a)
                `fw_field_h32_as("msk_b", msk_b)
                `fw_field_u8_as("level", m_last_int)
                for (int unsigned i = 0; i < n_ch; i++) begin
                    if (pend[i]) begin
                        `fw_dec_cand(i, (m_last_int != 2'b00) ? 2 : 1)
                    end
                end
            `fw_ev_end
        end
    endfunction

    // Index of the lowest set bit (0 if none) -- names the channel in the two
    // pending-vector edge records above.
    local function int unsigned lowest_bit(bit [30:0] v);
        for (int unsigned i = 0; i < 31; i++) if (v[i]) return i;
        return 0;
    endfunction

    // CH_EN-start hook, invoked by a channel CSR write-observer after a host
    // write commits. A rising CH_EN (re)arms the channel, ignored while paused /
    // already active / when the channel lacks EN capability (spec App. A).
    task on_ch_csr_write(int idx, bit [31:0] val, bit [31:0] prev);
        wb_dma_csr_t v = wb_dma_csr_t'(val);
        wb_dma_csr_t p = wb_dma_csr_t'(prev);
        wb_dma_ch    c = ch[idx];
        // v is the just-committed CSR (busy is hw-owned, preserved through the sw
        // write), so v.busy is the channel's current armed state -- no extra read.
        if (!p.ch_en && v.ch_en && c.cap_en && !v.busy && !paused()) begin
            c.arm();
            // The transfer flow OPENS here and closes in the engine
            // (`de.xfer_done`), joined by the flow key rather than a handle --
            // the two live on different threads and, in RTL, in different
            // blocks. `arm` is the accepting actor in the §14.10 sense: it makes
            // the work eligible without serving it, so `begun - accepted` on the
            // join is the channel's arbitration latency, measured.
            //
            // The key is just the channel: only one transfer is live per channel
            // at a time, so channel plus ordering pairs them unambiguously and
            // no arm counter has to be invented -- which matters, because a
            // counter the RTL does not hold could never be reconstructed there.
            `fw_op_accept(dbg_if(), FW_L_TXN, "rf.xfer")
                `fw_subject(idx)
                `fw_op_flow(idx)
                `fw_field_u8_as("ch", idx)
                `fw_field_u32_as("words", c.regs.sz.read().tot_sz)
            `fw_ev_end
            `fw_ev_decision(dbg_if(), FW_L_TXN, "rf.arm")
                `fw_subject(idx)
                `fw_field_u8_as("ch", idx)
                `fw_dec_winner(idx)
                `fw_field_h32_as("src", c.regs.adr0.read())
                `fw_field_h32_as("dst", c.regs.adr1.read())
                `fw_field_u32_as("words", c.regs.sz.read().tot_sz)
                `fw_field_u32_as("chunk", c.regs.sz.read().chk_sz)
                `fw_field_u8_as("prio", v.prio)
                `fw_field_b_as("hs", v.mode)
            `fw_ev_end
        end else if (!p.ch_en && v.ch_en) begin
            // "I set CH_EN and nothing happened." A rising CH_EN that does NOT
            // arm is the single most common way this model appears broken, and
            // until now it was completely silent -- the write landed, the
            // register read back exactly what was written, and the channel just
            // never ran. The reason is knowable only here, at the instant the
            // decision is made, which is why it is emitted rather than inferred.
            //
            // Reasons, in the order the guard tests them (1-based so 0 keeps its
            // "this candidate won" meaning):
            //   1 no_en_cap    -- the channel lacks the EN capability (App. A)
            //   2 already_busy -- a transfer is still running on this channel
            //   3 paused       -- the global PAUSE bit is set
            `fw_ev_decision(dbg_if(), FW_L_VITAL, "rf.arm_refused")
                `fw_subject(idx)
                `fw_field_u8_as("ch", idx)
                `fw_dec_cand(idx, !c.cap_en ? 1 : (v.busy ? 2 : 3))
            `fw_ev_end
        end
    endtask

    // ---- wb_proto_if provider implementation --------------------------------
    // access() dispatches to the register-block write/read helpers below.
    virtual task host_access(input  logic [31:0] adr, input logic [31:0] dat_w,
                             input  logic [3:0]  sel, input bit          we,
                             output logic [31:0] dat_r, output bit        err);
        automatic bit e;
        // Row 1 (`rf.host_access`): the model's host boundary, as a span.
        // Answers the first question anyone asks of a device that appears dead
        // -- "did my write reach the model at all" -- which no register event
        // can, because a write to an unmapped offset changes no register and a
        // write that never arrived looks exactly the same.
        `fw_op_span(hop)
        `fw_op_begin(dbg_if(), FW_L_TXN, "rf.host_access", hop)
            `fw_field_h32(adr)
            `fw_field_b(we)
            `fw_field_h32(dat_w)
            `fw_field_h8(sel)
        `fw_op_opened
        if (we) begin host_write(e, adr, dat_w, sel); dat_r = 32'h0; end
        else          host_read(dat_r, e, adr);
        err = e;
        `fw_op_close(dbg_if(), hop)
            `fw_field_h32(dat_r)
            `fw_field_b_as("err", e)
            `fw_op_outcome(e ? FW_OUT_ERROR : FW_OUT_OK)
        `fw_op_end(hop)
    endtask

    // The bus access routes through the register block: one offset decode, and
    // the register semantics (sw_wmask, hw_wmask, read-to-clear) come from the
    // generated map. No register is special-cased here, and no offset is named:
    // INT_SRC_A/B drop writes through their zero sw_wmask, and the RTL's
    // "reading a channel CSR clears that channel's interrupt source"
    // (wb_dma_ch_rf: ch_csr_re clears int_src_r) is the generated rclr_mask on
    // the CSR int bits -- applied by fw_reg_base::sw_read(), which is where
    // fw_reg_block routes a bus read.
    virtual task host_write(output bit err, input logic [31:0] addr,
                            input logic [31:0] data, input logic [3:0] strb);
        bit [11:0] off = addr[11:0];          // register file occupies < 4 KB
        err = 1'b0;
        regs.write_val(off, data);
        signal_int();                          // mask writes can move the int level
    endtask

    virtual task host_read(output logic [31:0] data, output bit err,
                           input logic [31:0] addr);
        bit [11:0] off = addr[11:0];
        err = 1'b0;
        data = regs.read_val(off);            // sw bus read (register semantics,
                                              // including the CSR read-to-clear)
        signal_int();                          // a channel-CSR read-clear can deassert the line
    endtask
endclass
