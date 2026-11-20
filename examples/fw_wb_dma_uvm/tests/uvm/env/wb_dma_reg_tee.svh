// ---- register-access tee (TLM top) -----------------------------------------
// A wb_proto_if decorator seated as the register agent's rf_if binding on the
// TLM top: every host register access is forwarded to BOTH the DUT and the
// always-on reference, so the reference is programmed identically without a
// separate monitor. The DUT is authoritative for the response (its read data
// and err are what the agent sees); the reference's response is discarded, but
// its read-CLEAR side effects (INT_SRC / CH_CSR reads) replay in bus order.
//
// TLM-only: it relies on both models exposing a shared method seam (rf_if()).
// The signal tops fan register traffic out by monitor-replay instead (N4).
class wb_dma_reg_tee implements wb_proto_if #(32, 32);
    wb_proto_if #(32, 32) m_dut;   // authoritative responder (DUT register file)
    wb_proto_if #(32, 32) m_ref;   // reference register file

    function new(wb_proto_if #(32, 32) dut, wb_proto_if #(32, 32) ref_if);
        m_dut = dut;
        m_ref = ref_if;
    endfunction

    virtual task access(
            input  [31:0] adr,
            input  [31:0] dat_w,
            input  [3:0]  sel,
            input         we,
            output [31:0] dat_r,
            output        err);
        automatic bit [31:0] rr;
        automatic bit        re;
        m_dut.access(adr, dat_w, sel, we, dat_r, err);  // DUT: authoritative response
        m_ref.access(adr, dat_w, sel, we, rr, re);      // reference: same access, side effects
    endtask
endclass
