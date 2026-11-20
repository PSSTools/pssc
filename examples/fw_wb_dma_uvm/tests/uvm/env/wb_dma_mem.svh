// ---- wb_proto_if memory with a per-access DATA-AVAILABILITY delay + backdoor -
// The access latency (7ns + optional `delay` wait-states) is the model's ONLY
// time source -- the engine is a pure data mover and never advances time itself;
// its execution time emerges from waiting here for data to be available.
// On the TLM path a `fw_quantum_keeper` is seated (qk != null): the latency is
// ACCOUNTED (accumulated) rather than yielded per access, so a 256-word transfer
// costs ~one scheduler yield per quantum instead of 512 -- loosely-timed temporal
// decoupling. On the signal paths qk is null and the latency is a real #delay
// (part of the Wishbone slave's response timing). The engine's master ports
// (de.mif0/mif1) drive access().
class wb_dma_mem extends fw_component;
    logic [31:0]      mem [logic [31:0]];
    int unsigned      delay = 0;    // extra ns per access (slave wait-states)
    fw_quantum_keeper qk;           // TLM temporal-decoupling keeper (null on signal paths)
    // Error injection (P7): when armed, any access to err_addr terminates with
    // ERR (no ACK). Set identically on the DUT and reference memories so both
    // engines abort at the same beat. Byte address, word-aligned.
    bit               err_en = 1'b0;
    logic [31:0]      err_addr;
    `FW_WB_PROTO_IMP(32, 32, wb_dma_mem, m);

    function new(string name, fw_component parent); super.new(name, parent); endfunction
    function void build(); m = new(this); endfunction

    // Arm/replace the injected error address (backdoor). virtual so the RAM-backed
    // flavour (wb_dma_ram_mem) can redirect it into the SV RAM the DUT masters.
    virtual function void inject_err(logic [31:0] addr); err_en = 1'b1; err_addr = addr; endfunction

    virtual task m_access(input  logic [31:0] adr, input logic [31:0] dat_w,
                          input  logic [3:0]  sel, input bit          we,
                          output logic [31:0] dat_r, output bit        err);
        // Data-availability delay: batched via the quantum keeper on TLM, a real
        // #delay on the signal paths (where the bus is cycle-accurate).
        if (qk != null) qk.account((7 + delay) * 1ns);
        else            #((7 + delay) * 1ns);
        if (err_en && adr == err_addr) begin        // injected bus error
            dat_r = 32'h0;
            err   = 1'b1;
            return;
        end
        if (we) begin mem[adr] = dat_w; dat_r = 32'h0; end
        else          dat_r = mem.exists(adr) ? mem[adr] : 32'h0;
        err = 1'b0;
    endtask

    // Backdoor: fill `count` words from byte `base` with a deterministic
    // pattern {seed[15:0], i[15:0]} (cf. bench fill_mem). virtual so a flavour
    // whose store lives elsewhere (e.g. the RTL path's combinational SV RAM)
    // can redirect the backdoor without changing the scoreboard/test API.
    virtual function void fill(logic [31:0] base, int count, logic [15:0] seed);
        for (int i = 0; i < count; i++)
            mem[base + 4*i] = {seed, i[15:0]};
    endfunction
    virtual function logic [31:0] peek(logic [31:0] addr);
        return mem.exists(addr) ? mem[addr] : 32'hxxxx_xxxx;
    endfunction
endclass
