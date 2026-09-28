// Self-checking testbench for the pssc-generated WB DMA programming API.
//
// Compiled together with the generated package (wb_dma_pkg) and the core
// (pssc_reg_pkg). Validates the generated factory/adapter/impl + register model
// by driving every operation through a DMA-aware mock bus and checking results.
// The mock deliberately does NOT implement pss_mem_if -- it is wired in via
// the generated parameterized adapter purely by matching method signatures
// (the duck-typing path). Prints "WB_DMA PROTOTYPE PASS" on success.

package tb_pkg;
  import pssc_reg_pkg::*;
  import wb_dma_pkg::*;

  // Models just enough hardware: a channel completes (sets DONE) when CH_EN is
  // written, moving TOT_SZ words from A0 to A1 in a backing system memory. A
  // poison source address forces the error path (sets ERR).
  class dma_mock_bus_c;
    localparam bit [31:0] POISON_SRC = 32'hdead_0000;

    protected addr_handle_t m_base;
    bit [31:0] regmem [addr_handle_t];   // register space (word addresses)
    bit [7:0]  sysmem [bit [31:0]];      // system memory for data movement

    function new(addr_handle_t base); m_base = base; endfunction

    local function bit [31:0] rdword(addr_handle_t a);
      return regmem.exists(a) ? regmem[a] : 32'h0;
    endfunction

    // CSR sits at offset 0 of each 0x20-byte channel block; channels start at
    // base+0x20. Returns 1 and the channel index for a channel-CSR address.
    local function bit decode_csr(addr_handle_t a, output int ch);
      addr_handle_t rel;
      if (a < (m_base + 64'h20)) return 0;
      rel = a - (m_base + 64'h20);
      if (rel >= (31 * 64'h20))   return 0;
      if ((rel % 64'h20) != 0)    return 0;   // not a CSR (offset 0)
      ch = int'(rel / 64'h20);
      return 1;
    endfunction

    local task run_transfer(int ch, addr_handle_t csr_addr, bit [31:0] csr_data);
      addr_handle_t chbase = m_base + 64'h20 + ch * 64'h20;
      bit [31:0]    a0 = rdword(chbase + 64'h08);   // source
      bit [31:0]    a1 = rdword(chbase + 64'h10);   // destination
      automatic dma_ch_sz_s   sz = rdword(chbase + 64'h04);
      int           nbytes = int'(sz.TOT_SZ) * 4;

      if (a0 == POISON_SRC) begin
        regmem[csr_addr] = (csr_data & ~32'h0000_0401) | 32'h0000_1000; // ERR
        return;
      end
      for (int i = 0; i < nbytes; i++)
        sysmem[a1 + i] = sysmem.exists(a0 + i) ? sysmem[a0 + i] : 8'h00;
      regmem[csr_addr] = (csr_data & ~32'h0000_0401) | 32'h0000_0800;   // DONE
    endtask

    local task maybe_run(addr_handle_t addr, bit [31:0] data);
      int ch;
      if (decode_csr(addr, ch) && data[0] /*CH_EN*/)
        run_transfer(ch, addr, data);
    endtask

    // ---- pss_mem_if-shaped primitives (matched by signature). ----
    task write8 (addr_handle_t addr, bit [7:0]  data);
      addr_handle_t waddr = addr & ~64'h3;
      bit [31:0]    w     = rdword(waddr);
      w[addr[1:0]*8 +: 8] = data;
      regmem[waddr] = w;
    endtask
    task read8  (addr_handle_t addr, output bit [7:0] data);
      bit [31:0] w = rdword(addr & ~64'h3);
      data = w[addr[1:0]*8 +: 8];
    endtask
    task write16(addr_handle_t addr, bit [15:0] data);
      addr_handle_t waddr = addr & ~64'h3;
      bit [31:0]    w     = rdword(waddr);
      w[addr[1]*16 +: 16] = data;
      regmem[waddr] = w;
    endtask
    task read16 (addr_handle_t addr, output bit [15:0] data);
      bit [31:0] w = rdword(addr & ~64'h3);
      data = w[addr[1]*16 +: 16];
    endtask
    task write32(addr_handle_t addr, bit [31:0] data);
      regmem[addr] = data;
      maybe_run(addr, data);
    endtask
    task read32 (addr_handle_t addr, output bit [31:0] data);
      data = rdword(addr);
    endtask
    task write64(addr_handle_t addr, bit [63:0] data);
      regmem[addr]         = data[31:0];
      regmem[addr + 64'h4] = data[63:32];
    endtask
    task read64 (addr_handle_t addr, output bit [63:0] data);
      data = {rdword(addr + 64'h4), rdword(addr)};
    endtask
  endclass
endpackage


module top;
  import pssc_reg_pkg::*;
  import wb_dma_pkg::*;
  import tb_pkg::*;

  function automatic addr_handle_t chbase(addr_handle_t base, int ch);
    return base + 64'h20 + ch * 64'h20;
  endfunction

  initial begin
    automatic addr_handle_t       base = 64'h4000_0000;
    automatic dma_mock_bus_c      bus  = new(base);
    automatic int                 errors = 0;
    dma_engine_c_ctxt_if dma;
    int                 status;

    // Construct through the factory's create(): duck-typed import object
    // (Timp overridden to the mock) + the root ctor's base argument. What
    // comes back is the context API: the model's exported functions.
    dma = dma_engine_c_root#(dma_mock_bus_c)::create(bus, base);

    // 1) configure_channel: sets PRIORITY/MODE/SRC/DST, leaves CH_EN clear.
    dma.configure_channel(5, 7, 1, 1, 0);
    begin
      automatic dma_ch_csr_s csr = bus.regmem[chbase(base, 5)];
      if (csr.PRIORITY !== 3'd7 || csr.MODE !== 1'b1 ||
          csr.SRC_SEL  !== 1'b1 || csr.DST_SEL !== 1'b0) begin
        $display("  FAIL configure_channel fields: csr=0x%08h", csr); errors++;
      end
      if (csr.CH_EN !== 1'b0) begin
        $display("  FAIL configure_channel started the channel"); errors++;
      end
    end

    // 2) mem_to_mem_copy: program + complete + actually move data.
    for (int i = 0; i < 16; i++) bus.sysmem[32'h1000_0000 + i] = 8'(i + 1);
    dma.mem_to_mem_copy(status, 3, 32'h1000_0000, 32'h2000_0000, 16);
    if (status != 0) begin $display("  FAIL copy status=%0d", status); errors++; end
    for (int i = 0; i < 16; i++)
      if (bus.sysmem[32'h2000_0000 + i] !== 8'(i + 1)) begin
        $display("  FAIL copy data[%0d]=0x%02h", i, bus.sysmem[32'h2000_0000 + i]);
        errors++;
      end
    begin
      automatic addr_handle_t cb = chbase(base, 3);
      automatic dma_ch_sz_s   sz = bus.regmem[cb + 64'h04];
      if (bus.regmem[cb + 64'h08] !== 32'h1000_0000) begin $display("  FAIL A0"); errors++; end
      if (bus.regmem[cb + 64'h10] !== 32'h2000_0000) begin $display("  FAIL A1"); errors++; end
      if (sz.TOT_SZ !== 12'd4)                        begin $display("  FAIL TOT_SZ=%0d", sz.TOT_SZ); errors++; end
    end

    // 3) mem_to_mem_copy_masked: also programs AM0/AM1.
    dma.mem_to_mem_copy_masked(status, 7, 32'h3000_0000, 32'h0000_0fff,
                               32'h4000_0000, 32'h0000_00ff, 8);
    if (status != 0) begin $display("  FAIL masked status=%0d", status); errors++; end
    begin
      automatic addr_handle_t cb = chbase(base, 7);
      if (bus.regmem[cb + 64'h0c] !== 32'h0000_0fff) begin $display("  FAIL AM0"); errors++; end
      if (bus.regmem[cb + 64'h14] !== 32'h0000_00ff) begin $display("  FAIL AM1"); errors++; end
    end

    // 4) mem_to_mem_copy_desc: programs DESC and sets USE_ED.
    dma.mem_to_mem_copy_desc(status, 9, 32'h5000_0000);
    if (status != 0) begin $display("  FAIL desc status=%0d", status); errors++; end
    begin
      automatic addr_handle_t cb  = chbase(base, 9);
      automatic dma_ch_csr_s  csr = bus.regmem[cb];
      if (bus.regmem[cb + 64'h18] !== 32'h5000_0000) begin $display("  FAIL DESC"); errors++; end
      if (csr.USE_ED !== 1'b1) begin $display("  FAIL USE_ED not set"); errors++; end
    end

    // 5) Error path: poison source address makes the channel set ERR.
    dma.mem_to_mem_copy(status, 2, 32'hdead_0000, 32'h6000_0000, 4);
    if (status != 1) begin
      $display("  FAIL error path: expected status=1 got %0d", status); errors++;
    end

    if (errors == 0) $display("WB_DMA PROTOTYPE PASS");
    else             $display("WB_DMA PROTOTYPE FAIL (%0d errors)", errors);
    $finish;
  end
endmodule
