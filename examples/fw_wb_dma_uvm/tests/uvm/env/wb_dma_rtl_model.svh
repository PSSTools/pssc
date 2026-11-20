// RTL model: host register path from wb_dma_wb_model, data memories on SV RAMs.
class wb_dma_rtl_model extends wb_dma_wb_model;
    virtual wb_ram_slv #(32, 32) vram0, vram1;   // seated by the SV top

    function new(string name, fw_component parent, int unsigned n_ch = 4);
        super.new(name, parent, n_ch);
    endfunction

    function void build();
        wb_dma_ram_mem r0, r1;
        // Host register path: the initiator bridge over the host xtor IS the
        // wb_proto_if register BFM -- same as the WB model; the CPU/register port
        // tolerates transactor latency.
        hbr    = new(vhost);
        // Data memories backed by the combinational SV RAMs.
        r0 = new("s0", this); r0.ram = vram0; s0 = r0;
        r1 = new("s1", this); r1.ram = vram1; s1 = r1;
        irqc = new("irqc", this);
    endfunction

    function void connect();
        // Intentionally empty: no transactor memory bridges. The wb_ram_slv
        // instances serve the DUT master ports combinationally (0-wait-state).
    endfunction
endclass
