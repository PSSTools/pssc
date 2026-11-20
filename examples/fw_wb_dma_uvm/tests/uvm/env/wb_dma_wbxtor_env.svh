// ---- WB-xtor env: WB env + fwvip_wb_target memory agents ------------------
// The GPIO-augmented WB env (wb_dma_wb_env) plus two fwvip_wb_target agents that
// back the DUT's master (data) ports. Each agent's config (a
// fwvip_wb_target_config_ap, set by the SV top) holds the WB target xtor vif on a
// master port AND the registered wb_proto_if responder (s0.m / s1.m); on start()
// its converter (wb_target_xtor_bridge) polls the xtor and drives access() into
// the responder. Used ONLY by the wrapped (WB) top -- the RTL top uses
// wb_dma_wb_env, since its master ports are combinational SV RAMs (no target xtor).
class wb_dma_wbxtor_env extends wb_dma_wb_env;
    `uvm_component_utils(wb_dma_wbxtor_env)
    fwvip_wb_target m_mem0, m_mem1;    // memory responders on WB0/WB1 master

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);      // wb_dma_env (reg agent + sb) + GPIO monitor
        m_mem0 = fwvip_wb_target::type_id::create("m_mem0", this);
        m_mem1 = fwvip_wb_target::type_id::create("m_mem1", this);
    endfunction
endclass
