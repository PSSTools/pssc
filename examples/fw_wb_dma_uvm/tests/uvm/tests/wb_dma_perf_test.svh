// Runs wb_dma_perf_seq and reports transfers/s + words/s. Same test on all three
// DUT flavours (TLM / SPL+xtors / RTL). Plusargs: +PERF_WORDS=<n> +PERF_SECS=<f>
// +PERF_LABEL=<str>. The top must be launched with +PERF (disables the sim-time
// watchdog so the run is bounded by wall-clock, not sim time).
class wb_dma_perf_test extends wb_dma_base_test;
    `uvm_component_utils(wb_dma_perf_test)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction

    // The result as tb_stats.json in the run directory: hdlsim's SimRun merges
    // it into the run's SimRunResult.stats on every simulator backend, and
    // tests/bench collects it (tests/bench/README.md). bench_wall_s is the
    // sequence's own loop time, so elaboration and UVM start-up are excluded.
    function void write_tb_stats(string label, wb_dma_perf_seq seq);
        int fd = $fopen("tb_stats.json", "w");
        if (fd == 0) begin
            `uvm_warning("PERF", "cannot write tb_stats.json")
            return;
        end
        $fdisplay(fd, "{\"bench_case\": \"uvm-%s\", \"xfers\": %0d, \"words\": %0d, \"bench_wall_s\": %0.6f, \"xfers_per_s\": %0.3f, \"mwords_per_s\": %0.6f}",
                  label, seq.xfers, seq.words, seq.wall,
                  (seq.wall > 0.0) ? real'(seq.xfers) / seq.wall : 0.0,
                  (seq.wall > 0.0) ? real'(seq.words) / seq.wall / 1.0e6 : 0.0);
        $fclose(fd);
    endfunction

    task run_phase(uvm_phase phase);
        wb_dma_perf_seq seq = wb_dma_perf_seq::type_id::create("seq");
        int    tot   = 256;
        real   secs  = 5.0;
        string label = "?";
        void'($value$plusargs("PERF_WORDS=%d", tot));
        void'($value$plusargs("PERF_SECS=%f", secs));
        void'($value$plusargs("PERF_LABEL=%s", label));
        seq.model = model; seq.tot = tot; seq.secs = secs;

        phase.raise_objection(this);
        seq.start(env.m_init.m_seqr);
        `uvm_info("PERF", $sformatf(
            "[%s] words/xfer=%0d  xfers=%0d  words=%0d  wall=%0.3f s  =>  %0.1f xfers/s  %0.3f Mword/s (%0.2f MB/s)",
            label, tot, seq.xfers, seq.words, seq.wall,
            real'(seq.xfers)/seq.wall,
            (real'(seq.words)/seq.wall)/1.0e6,
            (real'(seq.words)*4.0/seq.wall)/1.0e6), UVM_LOW)
        write_tb_stats(label, seq);
        phase.drop_objection(this);
    endtask
endclass
