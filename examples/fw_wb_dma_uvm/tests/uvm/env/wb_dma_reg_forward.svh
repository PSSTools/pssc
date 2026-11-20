// ---- signal-top register fan-out (monitor-replay) --------------------------
// On the signal tops the register agent drives real Wishbone cycles into the
// DUT's WB0-slave port; the reference (a TLM model) gets the identical program
// by replaying those observed cycles into its register file. A fwvip_wb_monitor
// on the WB0-slave bus feeds this subscriber, which replays each access (writes
// AND reads -- reads carry the INT_SRC/CH_CSR read-clear side effects and must
// replay in bus order) into ref_model.apply_reg().
//
// apply_reg() is a task (it drives the register BFM), but uvm_subscriber::write()
// is a function -- so write() only ENQUEUES; a run_phase drain loop applies the
// queued accesses in order. The reference is loosely-timed, so the small
// enqueue->apply latency is immaterial to functional equivalence (the source
// memory is already preloaded by the sequence before any register write).
class wb_dma_reg_forward extends uvm_subscriber #(fwvip_wb_transaction);
    `uvm_component_utils(wb_dma_reg_forward)
    wb_dma_ref_model ref_model;

    typedef struct { bit [31:0] adr; bit [31:0] dat; bit [3:0] sel; bit we; } reg_acc_t;
    reg_acc_t m_q[$];

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    virtual function void write(fwvip_wb_transaction t);
        reg_acc_t a;
        if (ref_model == null) return;
        a.adr = t.adr; a.dat = t.dat; a.sel = t.sel; a.we = t.we;
        m_q.push_back(a);
    endfunction

    task run_phase(uvm_phase phase);
        if (ref_model == null) return;
        forever begin
            wait (m_q.size() > 0);
            while (m_q.size() > 0) begin
                reg_acc_t a = m_q.pop_front();
                ref_model.apply_reg(a.adr, a.dat, a.sel, a.we);
            end
        end
    endtask
endclass
