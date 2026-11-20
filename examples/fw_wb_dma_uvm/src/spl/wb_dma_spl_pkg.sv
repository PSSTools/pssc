// Behavioral (class-based) model of the WISHBONE DMA/Bridge engine -- the "spl"
// model. One package holds the whole class layer: the DMA-specific types, the two
// DMA APIs, and the model components (one .svh per class, included below). The
// signal-level module wrapper (wb_dma_spl, ports matching wb_dma_top) lives in a
// separate compilation unit (wb_dma_spl.sv).
//
// Bus boundary: the engine speaks the WISHBONE access API (wb_proto_if, from
// fw-proto-wb) directly -- the SAME interface-class the RTL's transactors present
// -- so the fw-hdl model and the RTL share one boundary, which the signal-level
// wrapper bridges to WB pins via the fw-proto-wb xtor bridges. (This is a
// deliberate move away from the earlier protocol-independent fw_mem_if edge, to
// make RTL-vs-fw-hdl comparison apples-to-apples at the Wishbone boundary.) The
// interrupt outputs are
// driven through fw.proto.gpio's ABSTRACT drive API (gpio_drive_if) the same way
// -- the model depends on that method-level interface-class, NOT on the GPIO
// transactor (the kit's xtor bridges compile but are never instanced here; the
// signal-level initiator is instanced only at the wb_dma_spl edge). There is no
// dedicated types package -- the few shared types are declared directly here
// (behavioral model, so no synthesizable-sharing constraint).
//
// Package dependency: fw_hdl_pkg (modeling-library kernel) + fw_std_pkg, both from
// fw-hdl; fw_proto_wb_pkg (wb_proto_if, the bus boundary) from fw-proto-wb; and
// fw_proto_gpio_pkg (gpio_drive_if) from fw-proto-gpio.
`include "fw_hdl_macros.svh"
`include "dbg/fw_dbg_macros.svh"     // observable-events emit sites (compiled out by FW_TRACE_OFF)
`include "wb_dma_spl_macros.svh"     // FW_MEM_IMP + FW_WB_PROTO_IMP providers

package wb_dma_spl_pkg;
    import fw_hdl_pkg::*;
    import fw_std_pkg::*;           // fw event/component kernel helpers
    export fw_std_pkg::*;
    import fw_proto_wb_pkg::*;      // wb_proto_if -- the engine's Wishbone bus boundary
    export fw_proto_wb_pkg::*;      // re-export so consumers see wb_proto_if too
    import fw_proto_gpio_pkg::*;    // gpio_drive_if (interrupt-level output API only;
    export fw_proto_gpio_pkg::*;    // the transactor bridges ride along unused -- the
                                    // GPIO xtor is instanced only at the wb_dma_spl edge)
    // The REGISTER MODEL -- the MMIO field structs and the two fw_reg_block
    // classes -- generated from src/rdl/*.rdl by peakrdl-fw-hdl (dv-flow task
    // `reg-model`). Re-exported so a consumer that imports wb_dma_spl_pkg sees
    // the register types too.
    import wb_dma_reg_pkg::*;
    export wb_dma_reg_pkg::*;

    // ---- shared types (no dedicated types package) ---------------------------
    // Interrupt cause delivered through wb_dma_irq_if.
    typedef enum bit [1:0] { CAUSE_CHUNK, CAUSE_DONE, CAUSE_ERR } wb_dma_cause_e;

    // One interrupt event: which channel and why.
    typedef struct packed {
        bit [4:0]      channel;
        wb_dma_cause_e cause;
    } wb_dma_irq_evt_t;

    // ---- DMA-specific APIs (each ships a `FW_WB_DMA_*_IMP macro) -------------
    `include "wb_dma_hs_if.svh"
    `include "wb_dma_irq_if.svh"
    `include "wb_dma_int_if.svh"      // interrupt-change notification seam

    // ---- model components (one class per file) -------------------------------
    `include "wb_dma_ch.svh"          // per-channel register bank + working set
    `include "wb_dma_rf.svh"          // register file + host fw_mem_if provider
    `include "wb_dma_de.svh"          // data-mover engine (runnable; inlines arbitration)
    `include "wb_dma_irq.svh"         // interrupt propagation (de.irq -> irq_a/irq_b)
    `include "wb_dma.svh"             // top component (rf + de + irq)
endpackage
