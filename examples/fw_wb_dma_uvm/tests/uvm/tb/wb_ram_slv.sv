// ----------------------------------------------------------------------------
// Combinational, 0-wait-state WISHBONE classic target RAM.
//
// The OpenCores wb_dma DMA engine is designed and proven against a same-cycle
// (0-wait-state) memory -- its reference bench memory (wb_dma/bench/verilog/
// wb_slv_model.v) asserts ACK combinationally: ack = cyc & stb (delay==0), with
// read data available the same cycle. A variable-latency (FIFO-backed) transactor
// target does NOT satisfy the engine's chunk/pointer write-back timing, so the
// RTL bench backs the DUT's MASTER (data) ports with this simple RAM instead.
//
// Backdoor poke()/peek() let the class-side data-memory model (wb_dma_ram_mem)
// fill source data and read back results, so the scoreboard/test API is unchanged.
// Byte addresses are word-aligned; storage is a flat word array (WORDS deep).
// ----------------------------------------------------------------------------
interface wb_ram_slv #(
        parameter int AW    = 32,
        parameter int DW    = 32,
        parameter int WORDS = 8192      // 32 KiB -- covers all test regions
    ) (
        input  wire            clock,
        input  wire [AW-1:0]   adr,
        input  wire [DW-1:0]   dat_w,
        output wire [DW-1:0]   dat_r,
        input  wire [DW/8-1:0] sel,
        input  wire            cyc,
        input  wire            stb,
        input  wire            we,
        output wire            ack,
        output wire            err
    );
    localparam int IDXW = $clog2(WORDS);

    logic [DW-1:0]   mem [0:WORDS-1];
    wire  [IDXW-1:0] widx = adr[IDXW+1:2];   // word index (byte adr >> 2)

    // Error injection (P7): when armed, an access to err_addr terminates with
    // ERR instead of ACK (and does not write) -- the DUT engine aborts. Set via
    // the wb_dma_ram_mem backdoor so it fans out with the reference's memory.
    bit          err_en = 1'b0;
    logic [AW-1:0] err_addr;
    wire         hit = err_en & (adr == err_addr);

    // Classic 0-wait-state target: ACK the same cycle CYC & STB are asserted
    // (unless the injected error hits, which asserts ERR instead), read data
    // available combinationally.
    assign ack   = cyc & stb & ~hit;
    assign err   = cyc & stb &  hit;
    assign dat_r = mem[widx];

    always @(posedge clock)
        if (cyc & stb & we & ~hit)
            for (int b = 0; b < DW/8; b++)
                if (sel[b]) mem[widx][b*8 +: 8] <= dat_w[b*8 +: 8];

    // Arm/replace the injected error address (backdoor, byte address).
    function automatic void inject_err(logic [AW-1:0] a); err_en = 1'b1; err_addr = a; endfunction

    // Backdoor access (word address = byte address >> 2).
    function automatic void poke(logic [AW-1:0] a, logic [DW-1:0] d);
        mem[a[IDXW+1:2]] = d;
    endfunction
    function automatic logic [DW-1:0] peek(logic [AW-1:0] a);
        return mem[a[IDXW+1:2]];
    endfunction
endinterface
