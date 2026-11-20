// Signal-binding layer for the wb_dma_spl design top. Holds the fw-hdl bridges
// that adapt the fw-proto-wb / fw-proto-gpio transactor interfaces to the wb_dma
// model's fw_mem_if / interrupt edges. Separate from the WB-free core model
// package (wb_dma_spl_pkg): compiling this file pulls in the Wishbone + GPIO
// kits, so it lives with the wb_dma_spl module (the spl-wb build), not the core.
`include "fw_hdl_macros.svh"
`include "fw_std_macros.svh"          // FW_MEM_IMP support types (via fw_std_pkg)

package wb_dma_spl_wb_pkg;
    import fw_hdl_pkg::*;
    import fw_std_pkg::*;             // fw_mem_if
    import fw_proto_wb_pkg::*;        // wb_*_xtor_if + protocol API
    import fw_proto_gpio_pkg::*;      // gpio_*_xtor_if
    import wb_dma_spl_pkg::*;         // wb_dma model + wb_dma_irq_if / wb_dma_int_if

    `include "wb_dma_spl_bridges.svh"
endpackage
