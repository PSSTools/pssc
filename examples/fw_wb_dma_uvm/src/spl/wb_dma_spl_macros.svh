`ifndef INCLUDED_WB_DMA_SPL_MACROS_SVH
`define INCLUDED_WB_DMA_SPL_MACROS_SVH

// Implementation-template macros for the wb_dma_spl behavioral model's two
// DMA-specific APIs (fw-api-kit recipe). The memory-API provider macro
// (`FW_MEM_IMP) comes from fw-hdl's std layer (fw_std_macros.svh), pulled in here
// so a single include of this file makes every provider macro the model needs
// visible.
`include "fw_std_macros.svh"

// `FW_WB_DMA_HS_IMP(IMP, NAME) -- stamp a HW-handshake provider (the device /
//   stimulus that drives dma_req and observes dma_ack) inside a component.
//   IMP  : implementing component type (pass its own type; new with `this`)
//   NAME : export member name; implement wait_req()/ack() as NAME_wait_req /
//          NAME_ack. Outputs lead on wait_req.
`define FW_WB_DMA_HS_IMP(IMP, NAME) \
    class NAME``_imp_t extends fw_export #(wb_dma_hs_if) \
            implements wb_dma_hs_if; \
        local IMP m_imp; \
        function new(IMP imp); \
            super.new(`"NAME`", imp, null); \
            set_imp(this);  /* upcast `this` AFTER super.new: passing it INTO */ \
            m_imp = imp;    /* super.new segfaults Verilator 5.041 (this-in-ctor) */ \
        endfunction \
        virtual task wait_req(output bit nd, output bit rest); \
            m_imp.NAME``_wait_req(nd, rest); \
        endtask \
        virtual function void ack(); \
            m_imp.NAME``_ack(); \
        endfunction \
        virtual function bit has_req(); \
            return m_imp.NAME``_has_req(); \
        endfunction \
        virtual function void produce_to(fw_event_set s); \
            m_imp.NAME``_produce_to(s); \
        endfunction \
        virtual function int unsigned dbg_site(); \
            return m_imp.NAME``_dbg_site(); \
        endfunction \
    endclass \
    NAME``_imp_t NAME

// `FW_WB_DMA_IRQ_IMP(IMP, NAME) -- stamp an interrupt-cause sink inside a
//   component. raise() is a FUNCTION (non-blocking): a sink may not block.
//   Implement as NAME_raise(evt).
`define FW_WB_DMA_IRQ_IMP(IMP, NAME) \
    class NAME``_imp_t extends fw_export #(wb_dma_irq_if) \
            implements wb_dma_irq_if; \
        local IMP m_imp; \
        function new(IMP imp); \
            super.new(`"NAME`", imp, null); \
            set_imp(this);  /* upcast `this` AFTER super.new: passing it INTO */ \
            m_imp = imp;    /* super.new segfaults Verilator 5.041 (this-in-ctor) */ \
        endfunction \
        virtual function void raise(input wb_dma_irq_evt_t evt); \
            m_imp.NAME``_raise(evt); \
        endfunction \
    endclass \
    NAME``_imp_t NAME

// `FW_WB_PROTO_IMP(AW, DW, IMP, NAME) -- stamp a Wishbone-access provider inside
//   a component: an export NAME providing wb_proto_if #(AW,DW) whose access()
//   redirects to IMP's NAME``_access(...). This makes the model's bus boundary
//   the Wishbone API itself (the same interface-class fw-proto-wb's transactor
//   bridges speak), so the fw-hdl model and the RTL share one boundary. Mirrors
//   FW_MEM_IMP but for wb_proto_if. Requires wb_proto_if (fw_proto_wb_pkg) in
//   scope where the macro is expanded.
`define FW_WB_PROTO_IMP(AW, DW, IMP, NAME) \
    class NAME``_imp_t extends fw_export #(wb_proto_if #(AW, DW)) \
            implements wb_proto_if #(AW, DW); \
        local IMP m_imp; \
        function new(IMP imp); \
            super.new(`"NAME`", imp, null); \
            set_imp(this);  /* upcast `this` AFTER super.new: passing it INTO */ \
            m_imp = imp;    /* super.new segfaults Verilator (this-in-ctor) */ \
        endfunction \
        virtual task access( \
                input  [AW-1:0]      adr, \
                input  [DW-1:0]      dat_w, \
                input  [(DW/8)-1:0]  sel, \
                input                we, \
                output [DW-1:0]      dat_r, \
                output               err); \
            m_imp.NAME``_access(adr, dat_w, sel, we, dat_r, err); \
        endtask \
    endclass \
    NAME``_imp_t NAME

`endif // INCLUDED_WB_DMA_SPL_MACROS_SVH
