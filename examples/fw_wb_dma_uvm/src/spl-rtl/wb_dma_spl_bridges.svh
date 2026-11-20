// ======================================================================
// Signal-level bridges for the wb_dma_spl design top. Each is a SINGLE-LAYER
// fw-hdl bridge -- an fw_export (provider) or fw_port (consumer) that holds a
// live transactor virtual interface and adapts it to a model edge API. This is
// the shape the `fw_root_bind_port / `fw_root_bind_export macros expect (ctor
// new(name, parent, vif); the bridge IS the endpoint, not a wrapper around one),
// so the wb_dma model can be rooted directly with no hand-written bind class.
// ======================================================================

// ----------------------------------------------------------------------
// WB master: PROVIDES wb_proto_if to an engine master port (de.mif0/mif1) and
// drives each access() onto a Wishbone master through the initiator xtor's
// request/response task API. One wb_proto_if access == one Wishbone access (ACK
// implicit, single err bit, no RTY retry). Bound with `fw_root_bind_port.
// ----------------------------------------------------------------------
class wb_mem_initiator_bridge
        extends fw_export #(wb_proto_if #(32, 32))
        implements wb_proto_if #(32, 32);
    virtual wb_initiator_xtor_if #(32, 32) vif;

    function new(string name, fw_component parent,
                 virtual wb_initiator_xtor_if #(32, 32) vif);
        super.new(name, parent, null);   // set_imp(this) AFTER super.new: passing
        set_imp(this);                    // `this` INTO it segfaults Verilator
        this.vif = vif;
    endfunction

    virtual task access(input  logic [31:0] adr, input logic [31:0] dat_w,
                        input  logic [3:0]  sel, input bit          we,
                        output logic [31:0] dat_r, output bit        err);
        vif.request(adr, dat_w, sel, we);
        vif.response(dat_r, err);
    endtask
endclass

// ----------------------------------------------------------------------
// WB slave: an ACTIVE fw_port that CONSUMES the wb_proto_if the model provides
// (rf.host) and services the Wishbone slave. Its run() loop captures each
// request off the target xtor, calls the model's access(), and drives the
// response back. Bound with `fw_root_bind_export (endpoint is an export).
// ----------------------------------------------------------------------
class wb_mem_target_bridge
        extends fw_port #(wb_proto_if #(32, 32))
        implements fw_runnable;
    virtual wb_target_xtor_if #(32, 32) vif;

    function new(string name, fw_component parent,
                 virtual wb_target_xtor_if #(32, 32) vif);
        super.new(name, parent);
        this.vif = vif;
        parent.add_runnable(this);        // active port: opt in to run()
    endfunction

    virtual task run();
        wb_proto_if #(32, 32) mem = get_if();
        forever begin
            automatic logic [31:0] adr, dat_w, dat_r;
            automatic logic [3:0]  sel;
            automatic logic        we;
            automatic bit          e;
            vif.wait_req(adr, dat_w, sel, we);              // next captured request
            mem.access(adr, dat_w, sel, we, dat_r, e);      // model -> response
            vif.send_rsp(dat_r, e);                          // drive the response
        end
    endtask
endclass

// ----------------------------------------------------------------------
// GPIO output: PROVIDES the abstract gpio_drive_if to a model level-output port
// (wb_dma.irq_a / irq_b) and forwards each drive onto a 1-bit GPIO initiator via
// the transactor xtor's set()/set_bits()/clr_bits() task API. The model calls
// set() (it is the consumer), so this is the provider export bound with
// `fw_root_bind_port -- exactly mirroring wb_mem_initiator_bridge. The interrupt
// AGGREGATION lives in the model (wb_dma_irq); this bridge is a pure signal-level
// adapter with no DMA knowledge.
// ----------------------------------------------------------------------
class gpio_drive_bridge extends fw_export #(gpio_drive_if #(1))
        implements gpio_drive_if #(1);
    virtual gpio_initiator_xtor_if #(1) vif;

    function new(string name, fw_component parent,
                 virtual gpio_initiator_xtor_if #(1) vif);
        super.new(name, parent, null);   // set_imp(this) AFTER super.new: passing
        set_imp(this);                    // `this` INTO it segfaults Verilator
        this.vif = vif;
    endfunction

    virtual task set(input [0:0] value);      vif.set(value);      endtask
    virtual task set_bits(input [0:0] mask);  vif.set_bits(mask);  endtask
    virtual task clr_bits(input [0:0] mask);  vif.clr_bits(mask);  endtask
endclass
