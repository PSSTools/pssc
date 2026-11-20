// ======================================================================
// wb_dma_spl -- signal-level Wishbone wrapper around the wb_dma class model.
//
// Ports are IDENTICAL to the OpenCores RTL wb_dma_top
// (packages/wb_dma/rtl/verilog/wb_dma_top.v): two Wishbone interfaces, each with
// a slave + a master port, plus the per-channel DMA handshake bus and the two
// interrupt lines. A drop-in replacement at the pin level, backed by the
// behavioral model instead of RTL.
//
// STRUCTURE -- the standard fw-hdl design-top shape (cf. fw-hdl tests/rv_proto,
// tests/blinky): a thin module that instances the signal-level transactors and
// uses the `fw_root_begin/bind/end macros to make the wb_dma model the elaboration
// root and bind its edge endpoints to the transactors. fw_root news/kills the
// root across reset and drives its lifecycle (build -> connect -> run). All the
// class-layer glue (the wb_dma model, the bridges) lives in packages
// (wb_dma_spl_pkg / wb_dma_spl_wb_pkg); the module carries no class definitions.
// The binding map:
//   de.mif0 (fw_mem_if master)  <- wb_mem_initiator_bridge -> WB0 master (u_mst0)
//   de.mif1 (fw_mem_if master)  <- wb_mem_initiator_bridge -> WB1 master (u_mst1)
//   rf.host (fw_mem_if slave)   <- wb_mem_target_bridge    -> WB0 slave  (u_slv0)
//   irq_a   (gpio_drive_if out) <- gpio_drive_bridge       -> inta GPIO  (u_inta)
//   irq_b   (gpio_drive_if out) <- gpio_drive_bridge       -> intb GPIO  (u_intb)
//   WB1 slave -- tied off (Not Connected, mirrors the RTL slv1)
//
// The interrupt CAUSE port (de.irq) is NOT bound here -- the model wires it
// internally to its interrupt-propagation block, which drives irq_a/irq_b. Only
// the two aggregate level outputs cross the module boundary.
//
// The per-channel HW-handshake bus (dma_req_i/dma_ack_o/dma_nd_i/dma_rest_i) is
// exposed but STUBBED -- not wired to the engine's de.hs[] ports (dma_ack_o = 0).
// The engine treats unconnected HS ports as null, so HW-handshake channels simply
// never become "ready".
//
// The config parameters (rf_addr/pri_sel/chN_conf) are carried verbatim for a
// literal drop-in signature with wb_dma_top, but are inert here. NOTE: the model's
// channel count is wb_dma's default (4); ch_count sizes only the handshake bus
// pins. Threading ch_count into the rooted model needs the parameterized-root
// macro (see fw-hdl tests/root_param) and is left for when a non-default count is
// required.
// ======================================================================
`include "fw_hdl_macros.svh"

module wb_dma_spl #(
    parameter        rf_addr   = 0,
    parameter [1:0]  pri_sel   = 2'h0,
    parameter        ch_count  = 1,
    parameter [3:0]  ch0_conf  = 4'h1,
    parameter [3:0]  ch1_conf  = 4'h0,
    parameter [3:0]  ch2_conf  = 4'h0,
    parameter [3:0]  ch3_conf  = 4'h0,
    parameter [3:0]  ch4_conf  = 4'h0,
    parameter [3:0]  ch5_conf  = 4'h0,
    parameter [3:0]  ch6_conf  = 4'h0,
    parameter [3:0]  ch7_conf  = 4'h0,
    parameter [3:0]  ch8_conf  = 4'h0,
    parameter [3:0]  ch9_conf  = 4'h0,
    parameter [3:0]  ch10_conf = 4'h0,
    parameter [3:0]  ch11_conf = 4'h0,
    parameter [3:0]  ch12_conf = 4'h0,
    parameter [3:0]  ch13_conf = 4'h0,
    parameter [3:0]  ch14_conf = 4'h0,
    parameter [3:0]  ch15_conf = 4'h0,
    parameter [3:0]  ch16_conf = 4'h0,
    parameter [3:0]  ch17_conf = 4'h0,
    parameter [3:0]  ch18_conf = 4'h0,
    parameter [3:0]  ch19_conf = 4'h0,
    parameter [3:0]  ch20_conf = 4'h0,
    parameter [3:0]  ch21_conf = 4'h0,
    parameter [3:0]  ch22_conf = 4'h0,
    parameter [3:0]  ch23_conf = 4'h0,
    parameter [3:0]  ch24_conf = 4'h0,
    parameter [3:0]  ch25_conf = 4'h0,
    parameter [3:0]  ch26_conf = 4'h0,
    parameter [3:0]  ch27_conf = 4'h0,
    parameter [3:0]  ch28_conf = 4'h0,
    parameter [3:0]  ch29_conf = 4'h0,
    parameter [3:0]  ch30_conf = 4'h0
) (
    input               clk_i,
    input               rst_i,

    // -------- WISHBONE INTERFACE 0 : Slave (host register access) --------
    input      [31:0]   wb0s_data_i,
    output     [31:0]   wb0s_data_o,
    input      [31:0]   wb0_addr_i,
    input      [3:0]    wb0_sel_i,
    input               wb0_we_i,
    input               wb0_cyc_i,
    input               wb0_stb_i,
    output              wb0_ack_o,
    output              wb0_err_o,
    output              wb0_rty_o,
    // -------- WISHBONE INTERFACE 0 : Master (IF0 data mover) --------
    input      [31:0]   wb0m_data_i,
    output     [31:0]   wb0m_data_o,
    output     [31:0]   wb0_addr_o,
    output     [3:0]    wb0_sel_o,
    output              wb0_we_o,
    output              wb0_cyc_o,
    output              wb0_stb_o,
    input               wb0_ack_i,
    input               wb0_err_i,
    input               wb0_rty_i,

    // -------- WISHBONE INTERFACE 1 : Slave (Not Connected) --------
    input      [31:0]   wb1s_data_i,
    output     [31:0]   wb1s_data_o,
    input      [31:0]   wb1_addr_i,
    input      [3:0]    wb1_sel_i,
    input               wb1_we_i,
    input               wb1_cyc_i,
    input               wb1_stb_i,
    output              wb1_ack_o,
    output              wb1_err_o,
    output              wb1_rty_o,
    // -------- WISHBONE INTERFACE 1 : Master (IF1 data mover) --------
    input      [31:0]   wb1m_data_i,
    output     [31:0]   wb1m_data_o,
    output     [31:0]   wb1_addr_o,
    output     [3:0]    wb1_sel_o,
    output              wb1_we_o,
    output              wb1_cyc_o,
    output              wb1_stb_o,
    input               wb1_ack_i,
    input               wb1_err_i,
    input               wb1_rty_i,

    // -------- Per-channel DMA handshake (STUBBED -- not wired to core) --------
    input  [ch_count-1:0] dma_req_i,
    output [ch_count-1:0] dma_ack_o,
    input  [ch_count-1:0] dma_nd_i,
    input  [ch_count-1:0] dma_rest_i,

    // -------- Interrupts --------
    output              inta_o,
    output              intb_o
);
    import fw_hdl_pkg::*;
    import fw_std_pkg::*;
    import fw_proto_wb_pkg::*;
    import fw_proto_gpio_pkg::*;
    import wb_dma_spl_pkg::*;
    import wb_dma_spl_wb_pkg::*;

    // ================= transactors on the pins =================
    // WB0 slave: host register access.
    wb_target_xtor u_slv0 (
        .clock(clk_i), .reset(rst_i),
        .adr(wb0_addr_i), .dat_w(wb0s_data_i), .dat_r(wb0s_data_o),
        .cyc(wb0_cyc_i), .stb(wb0_stb_i), .sel(wb0_sel_i), .we(wb0_we_i),
        .ack(wb0_ack_o), .err(wb0_err_o));
    assign wb0_rty_o = 1'b0;                          // transactor has no RTY

    // WB0 master: IF0 data mover (de.mif0).
    wb_initiator_xtor u_mst0 (
        .clock(clk_i), .reset(rst_i),
        .adr(wb0_addr_o), .dat_w(wb0m_data_o), .dat_r(wb0m_data_i),
        .cyc(wb0_cyc_o), .stb(wb0_stb_o), .sel(wb0_sel_o), .we(wb0_we_o),
        .ack(wb0_ack_i), .err(wb0_err_i));           // wb0_rty_i ignored

    // WB1 master: IF1 data mover (de.mif1).
    wb_initiator_xtor u_mst1 (
        .clock(clk_i), .reset(rst_i),
        .adr(wb1_addr_o), .dat_w(wb1m_data_o), .dat_r(wb1m_data_i),
        .cyc(wb1_cyc_o), .stb(wb1_stb_o), .sel(wb1_sel_o), .we(wb1_we_o),
        .ack(wb1_ack_i), .err(wb1_err_i));           // wb1_rty_i ignored

    // Interrupts: one 1-bit GPIO initiator per line, driven by the model's
    // interrupt-propagation block through irq_a/irq_b (gpio_drive_bridge). Registered
    // on clk_i in the GPIO cores -- the same pin behavior as the RTL's level outputs.
    wire inta_line, intb_line;
    gpio_initiator_xtor #(.WIDTH(1)) u_inta (
        .clock(clk_i), .reset(rst_i), .gpio_o(inta_line));
    gpio_initiator_xtor #(.WIDTH(1)) u_intb (
        .clock(clk_i), .reset(rst_i), .gpio_o(intb_line));
    assign inta_o = inta_line;
    assign intb_o = intb_line;

    // WB1 slave: Not Connected (mirrors RTL slv1).
    assign wb1s_data_o = 32'h0;
    assign wb1_ack_o   = 1'b0;
    assign wb1_err_o   = 1'b0;
    assign wb1_rty_o   = 1'b0;

    // DMA handshake: stubbed (not wired to the engine).
    assign dma_ack_o = '0;

    // ================= model root + lifecycle =================
    // The wb_dma model IS the elaboration root. fw_root news/kills it across reset
    // and drives build -> connect -> run; each bind constructs one bridge over a
    // live transactor interface and wires it to a model endpoint.
    `fw_root_begin(wb_dma, u_root, clk_i, rst_i)
        `fw_root_bind_port  (de.mif0, u_mst0.u_if, wb_mem_initiator_bridge)
        `fw_root_bind_port  (de.mif1, u_mst1.u_if, wb_mem_initiator_bridge)
        `fw_root_bind_export(rf.host, u_slv0.u_if, wb_mem_target_bridge)
        // The two aggregate interrupt levels: the model drives irq_a/irq_b, each
        // adapted onto a 1-bit GPIO initiator. de.irq is wired inside the model.
        `fw_root_bind_port  (irq_a,   u_inta.u_if, gpio_drive_bridge)
        `fw_root_bind_port  (irq_b,   u_intb.u_if, gpio_drive_bridge)
    `fw_root_end

endmodule
