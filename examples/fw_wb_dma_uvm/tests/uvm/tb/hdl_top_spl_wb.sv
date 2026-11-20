// HDL top for the SIGNAL-LEVEL (Wishbone) UVM path. Holds all signal-level
// content: the wb_dma_spl DUT (the SPL model behind Wishbone pins), the three
// Wishbone buses, the external transactor instances (host initiator + two memory
// targets), the interrupt-pin GPIO monitor, and the clock/reset substrate. The
// class-side connection (bus-side model bring-up, config-db, run_test) lives in
// hvl_top_spl_wb, which reaches these instances hierarchically.
module hdl_top_spl_wb;
    logic clk = 1'b0;
    logic rst = 1'b1;
    always #5ns clk = ~clk;
    // Reset release runs CONCURRENTLY (UVM forbids sim time before run_test()); the
    // transactors hold during reset, so the first bus access simply waits it out.
    initial begin
        rst = 1'b1;
        repeat (5) @(posedge clk);
        rst = 1'b0;
    end

    // ---- three Wishbone buses ------------------------------------------------
    wire [31:0] h_dat_w, h_dat_r, h_adr;  wire [3:0] h_sel;
    wire        h_we, h_cyc, h_stb, h_ack, h_err;
    wire [31:0] m0_adr, m0_dat_w, m0_dat_r;  wire [3:0] m0_sel;
    wire        m0_we, m0_cyc, m0_stb, m0_ack, m0_err;
    wire [31:0] m1_adr, m1_dat_w, m1_dat_r;  wire [3:0] m1_sel;
    wire        m1_we, m1_cyc, m1_stb, m1_ack, m1_err;
    wire        inta, intb;
    wire [3:0]  dma_ack;

    // ---- DUT: the Wishbone-wrapped SPL model ---------------------------------
    wb_dma_spl #(.ch_count(4)) dut (
        .clk_i(clk), .rst_i(rst),
        .wb0s_data_i(h_dat_w), .wb0s_data_o(h_dat_r), .wb0_addr_i(h_adr),
        .wb0_sel_i(h_sel), .wb0_we_i(h_we), .wb0_cyc_i(h_cyc), .wb0_stb_i(h_stb),
        .wb0_ack_o(h_ack), .wb0_err_o(h_err), .wb0_rty_o(),
        .wb0m_data_i(m0_dat_r), .wb0m_data_o(m0_dat_w), .wb0_addr_o(m0_adr),
        .wb0_sel_o(m0_sel), .wb0_we_o(m0_we), .wb0_cyc_o(m0_cyc), .wb0_stb_o(m0_stb),
        .wb0_ack_i(m0_ack), .wb0_err_i(m0_err), .wb0_rty_i(1'b0),
        .wb1s_data_i(32'h0), .wb1s_data_o(), .wb1_addr_i(32'h0),
        .wb1_sel_i(4'h0), .wb1_we_i(1'b0), .wb1_cyc_i(1'b0), .wb1_stb_i(1'b0),
        .wb1_ack_o(), .wb1_err_o(), .wb1_rty_o(),
        .wb1m_data_i(m1_dat_r), .wb1m_data_o(m1_dat_w), .wb1_addr_o(m1_adr),
        .wb1_sel_o(m1_sel), .wb1_we_o(m1_we), .wb1_cyc_o(m1_cyc), .wb1_stb_o(m1_stb),
        .wb1_ack_i(m1_ack), .wb1_err_i(m1_err), .wb1_rty_i(1'b0),
        .dma_req_i(4'h0), .dma_ack_o(dma_ack), .dma_nd_i(4'h0), .dma_rest_i(4'h0),
        .inta_o(inta), .intb_o(intb));

    // ---- external transactors ------------------------------------------------
    wb_initiator_xtor u_host (
        .clock(clk), .reset(rst),
        .adr(h_adr), .dat_w(h_dat_w), .dat_r(h_dat_r),
        .cyc(h_cyc), .stb(h_stb), .sel(h_sel), .we(h_we), .ack(h_ack), .err(h_err));
    wb_target_xtor u_mem0 (
        .clock(clk), .reset(rst),
        .adr(m0_adr), .dat_w(m0_dat_w), .dat_r(m0_dat_r),
        .cyc(m0_cyc), .stb(m0_stb), .sel(m0_sel), .we(m0_we), .ack(m0_ack), .err(m0_err));
    wb_target_xtor u_mem1 (
        .clock(clk), .reset(rst),
        .adr(m1_adr), .dat_w(m1_dat_w), .dat_r(m1_dat_r),
        .cyc(m1_cyc), .stb(m1_stb), .sel(m1_sel), .we(m1_we), .ack(m1_ack), .err(m1_err));

    // GPIO monitor tapping the interrupt pins {intb_o, inta_o} (bit0=inta, bit1=intb).
    gpio_monitor_xtor #(.WIDTH(2)) u_mon_int (.clock(clk), .reset(rst), .gpio_i({intb, inta}));

    // ---- passive Wishbone monitors for SPL<->DUT comparison ------------------
    // Two on the DUT master buses (WB0/WB1 data movement) feed the comparator's
    // DUT side; one on the WB0-slave (register) bus replays the program into the
    // reference. All are read-only taps (the monitor drives nothing).
    wb_monitor_xtor #(.ADDR_WIDTH(32), .DATA_WIDTH(32)) u_mon_m0 (
        .clock(clk), .reset(rst),
        .adr(m0_adr), .dat_w(m0_dat_w), .dat_r(m0_dat_r),
        .cyc(m0_cyc), .stb(m0_stb), .sel(m0_sel), .we(m0_we), .ack(m0_ack), .err(m0_err));
    wb_monitor_xtor #(.ADDR_WIDTH(32), .DATA_WIDTH(32)) u_mon_m1 (
        .clock(clk), .reset(rst),
        .adr(m1_adr), .dat_w(m1_dat_w), .dat_r(m1_dat_r),
        .cyc(m1_cyc), .stb(m1_stb), .sel(m1_sel), .we(m1_we), .ack(m1_ack), .err(m1_err));
    wb_monitor_xtor #(.ADDR_WIDTH(32), .DATA_WIDTH(32)) u_mon_reg (
        .clock(clk), .reset(rst),
        .adr(h_adr), .dat_w(h_dat_w), .dat_r(h_dat_r),
        .cyc(h_cyc), .stb(h_stb), .sel(h_sel), .we(h_we), .ack(h_ack), .err(h_err));

    // Clock domain for the bus-side model tree.
    fw_clock_xtor_if u_clk(.clock(clk), .reset(rst));
endmodule
