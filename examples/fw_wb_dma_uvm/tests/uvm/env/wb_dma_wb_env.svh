// ---- WB env: the shared UVM env + a GPIO monitor on the interrupt pins ----
// Injected into the WB top via a factory override of wb_dma_env, so the shared
// env, tests, and the TLM path are untouched.
//
// This is also where the signal tops' SPL<->DUT comparison plumbing lives: when
// the top publishes an always-on reference (ref_model != null), the env builds
// three passive Wishbone monitors -- two on the DUT master buses (WB0/WB1) whose
// streams feed the comparator's DUT side, and one on the WB0-slave (register)
// bus that replays the program into the reference (wb_dma_reg_forward). The DUT
// on a signal top has no taps, so this is how its master stream reaches the
// comparator. The SV top provides each monitor's vif via config_db; with no
// reference (e.g. the RTL top before its rung lands) none of this is built.
class wb_dma_wb_env extends wb_dma_env;
    `uvm_component_utils(wb_dma_wb_env)
    fwvip_gpio_monitor  m_int;       // taps {intb_o, inta_o} (WIDTH=2)
    wb_dma_int_gpio_sub m_int_sub;

    // Comparison monitors (built only when a reference is present).
    fwvip_wb_monitor    m_mon_m0, m_mon_m1, m_mon_reg;
    wb_dma_mon_adapter  m_adapt_m0, m_adapt_m1;
    wb_dma_reg_forward  m_reg_fwd;

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);    // builds agent/sb/cmp/model + fetches ref_model
        m_int     = fwvip_gpio_monitor::type_id::create("m_int", this);
        m_int_sub = wb_dma_int_gpio_sub::type_id::create("m_int_sub", this);
        if (ref_model != null) begin
            m_mon_m0   = fwvip_wb_monitor  ::type_id::create("m_mon_m0",   this);
            m_mon_m1   = fwvip_wb_monitor  ::type_id::create("m_mon_m1",   this);
            m_mon_reg  = fwvip_wb_monitor  ::type_id::create("m_mon_reg",  this);
            m_adapt_m0 = wb_dma_mon_adapter::type_id::create("m_adapt_m0", this);
            m_adapt_m1 = wb_dma_mon_adapter::type_id::create("m_adapt_m1", this);
            m_reg_fwd  = wb_dma_reg_forward::type_id::create("m_reg_fwd",  this);
        end
    endfunction

    function void connect_phase(uvm_phase phase);
        wb_dma_ref_model rm;
        super.connect_phase(phase);  // binds interrupt seam below + comparator ref sink
        m_int_sub.model = model;     // model handle fetched by wb_dma_env
        m_int.ap.connect(m_int_sub.analysis_export);
        if (ref_model == null) return;
        void'($cast(rm, ref_model));
        // DUT master streams -> comparator (DUT side); attribution via the reference.
        m_adapt_m0.port = 1'b0; m_adapt_m0.cmp = cmp; m_adapt_m0.resolver = rm;
        m_adapt_m1.port = 1'b1; m_adapt_m1.cmp = cmp; m_adapt_m1.resolver = rm;
        m_mon_m0.ap.connect(m_adapt_m0.analysis_export);
        m_mon_m1.ap.connect(m_adapt_m1.analysis_export);
        // Register cycles -> reference program (monitor-replay).
        m_reg_fwd.ref_model = ref_model;
        m_mon_reg.ap.connect(m_reg_fwd.analysis_export);
    endfunction
endclass
