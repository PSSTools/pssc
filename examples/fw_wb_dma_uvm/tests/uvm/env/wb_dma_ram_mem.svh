// Data memory whose backdoor fill/peek target a combinational SV RAM (the DUT
// masters into it directly). Overrides only the backdoor; m_write/m_read (the
// fw_mem_if edge) are inherited but unused on this path.
class wb_dma_ram_mem extends wb_dma_mem;
    virtual wb_ram_slv #(32, 32) ram;
    function new(string name, fw_component parent); super.new(name, parent); endfunction
    virtual function void fill(logic [31:0] base, int count, logic [15:0] seed);
        for (int i = 0; i < count; i++) ram.poke(base + 4*i, {seed, i[15:0]});
    endfunction
    virtual function logic [31:0] peek(logic [31:0] addr);
        return ram.peek(addr);
    endfunction
    // Injected bus error lives in the RAM (the DUT masters into it directly), not
    // the inherited wb_dma_mem responder.
    virtual function void inject_err(logic [31:0] addr);
        ram.inject_err(addr);
    endfunction
endclass
