// ---- base test: build env, publish model, report ------------------------
class wb_dma_base_test extends uvm_test;
    `uvm_component_utils(wb_dma_base_test)
    wb_dma_env     env;
    wb_dma_model_base model;

    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    function void build_phase(uvm_phase phase);
        uvm_coreservice_t          cs = uvm_coreservice_t::get();
        wb_dma_name_check_visitor  nv = new();
        super.build_phase(phase);
        // Replace UVM's regex-based component-name check with one that works
        // without DPI -- see wb_dma_name_check.svh. uvm_root runs the visitor
        // at end_of_elaboration, so installing it here is early enough.
        cs.set_component_visitor(nv);
        if (!uvm_config_db #(wb_dma_model_base)::get(this, "", "model", model))
            `uvm_fatal("CFG", "no model env in config_db")
        env = wb_dma_env::type_id::create("env", this);
    endfunction

    function void report_phase(uvm_phase phase);
        int unsigned errs = env.sb.errors + env.cmp.errors;
        if (errs == 0)
            `uvm_info("RESULT", "** TEST PASSED **", UVM_LOW)
        else
            `uvm_error("RESULT", $sformatf("** TEST FAILED (%0d errors: sb=%0d cmp=%0d) **",
                                           errs, env.sb.errors, env.cmp.errors))
    endfunction
endclass
