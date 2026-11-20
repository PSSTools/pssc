// pssvm_pkg -- runs a pssc image (`pssc build --image x.zbc`) inside a
// simulation. Hand-written once, shipped with pssc (`pssc sv-pssvm-path`).
// Holds:
//   * pssvm_status_e, pssvm_req -- the host ABI's run status and pending
//                                  request (zuspec-be-bc spec host-abi)
//   * pssvm_engine     -- the host ABI as an SV class: one engine, its calls
//   * pssvm_engine_dpi -- that class over DPI-C (native engine; `PSSVM_DPI`)
//   * pssvm_platform_if -- what the testbench supplies: bus accesses, imports,
//                          reset, time
//   * pssvm_ctxt       -- engine + platform; `call` is the run loop (design
//                         §10.3): one SV task per call, SV threads for
//                         parallel calls
//
// The engine behind `pssvm_engine` is pyvm through pyhdl-if (the shim,
// share/py/pssvm_dpi_shim.py) or the native engine over DPI-C. Switching
// engines changes no SV but the line that creates the engine.
//
// Values crossing the boundary are scalars (64-bit, little-endian as in the
// host ABI); access data is the read or written value, import arguments are
// one 64-bit value each, strings are SV strings.
//
// An engine error is a located failure: `call` returns ok = 0 and the
// message (JSON from `pssvm_error`) is in `last_error`. It never stops the
// simulation.
//
// Design: docs/design/pss-test-debug-env-design.md (§10).

package pssvm_pkg;

  localparam int PSSVM_ABI_VERSION = 1;

  // `pssvm_run` result.
  typedef enum int {
    PSSVM_ERROR  = -1,
    PSSVM_DONE   = 0,
    PSSVM_ACCESS = 1,
    PSSVM_IMPORT = 2,
    PSSVM_WAIT   = 3,
    PSSVM_STOP   = 4
  } pssvm_status_e;

  // What a WAIT waits on.
  typedef enum int {
    PSSVM_WAIT_TIME  = 0,
    PSSVM_WAIT_EVENT = 1
  } pssvm_wait_on_e;

  // The pending request of a call (`pssvm_request`). Only the fields of
  // `status` are meaningful.
  class pssvm_req;
    pssvm_status_e     status;
    // ACCESS
    bit                write;
    longint unsigned   addr;
    int                size;      // bytes: 1, 2, 4 or 8
    longint unsigned   data;      // write data
    int                reg_id;
    string             src_ref;
    // IMPORT
    int                fn_id;
    string             name;
    longint unsigned   args[$];
    bit                blocking;
    // WAIT
    pssvm_wait_on_e    on;
    longint unsigned   delay;     // engine time units (WAIT TIME)
    // STOP
    string             reason;
  endclass

  // The host ABI. A call handle is an int (> 0); 0 means the call could not
  // start (`error(0)` says why).
  virtual class pssvm_engine;
    pure virtual function int call_start(string entry, string comp_path,
                                         longint unsigned args[$]);
    pure virtual function pssvm_status_e run(int h);
    pure virtual function void request(int h, pssvm_req req);
    pure virtual function int complete(int h, longint unsigned data);
    pure virtual function longint unsigned result(int h);
    pure virtual function void call_end(int h);
    pure virtual function int notify(int kind, int id, longint unsigned value);
    pure virtual function string error(int h);

    // The image's tests and hooks, for pssvm_runner. An engine without a
    // test list has none.
    // Test `i` as `comp::fn`; "" past the last.
    virtual function string test_name(int i);
      return "";
    endfunction
    // The error test `i` expects (`@pssunit_pkg::expect_error`), or "".
    virtual function string test_expect(int i);
      return "";
    endfunction
    // Session (`kind` "session", run once per simulation), setup ("setup")
    // or teardown function `i`; "" past the last.
    virtual function string hook(string kind, int i);
      return "";
    endfunction
    // Build the component tree again (a fresh one for the next test); 0,
    // or -1 (`error(0)`).
    virtual function int fresh();
      return 0;
    endfunction
    // pssunit checks failed so far in this engine.
    virtual function int checks_failed();
      return 0;
    endfunction
    // "" when the image has every function of `spec` (a generated facade's
    // `comp::fn(p,s.f)[->r]` lines, ';'-separated) with those parameter
    // leaves and result; else what differs. An engine that cannot tell: "".
    virtual function string api_check(string spec);
      return "";
    endfunction
    // Server mode: register with the workspace daemon as `name` (top
    // module `top`); the session id, or "" (`error(0)`). An engine
    // without server mode: "".
    virtual function string serve(string name, string top);
      return "";
    endfunction
    // Answer one request from the daemon, waiting up to `timeout_ms`:
    // 1 answered, 2 a `run` is pending (`pending_sel`; the runner runs
    // it, then `serve_done`), 0 none, -1 the session closed or ended.
    virtual function int poll(int timeout_ms);
      return -1;
    endfunction
    // The pending `run`'s selection (`+pssunit` form; "" = every test).
    virtual function string pending_sel();
      return "";
    endfunction
    // A test of the pending `run` finished (a `test` event).
    virtual function void serve_test(string name, int passed);
    endfunction
    // The pending `run` finished: answer it with the counts.
    virtual function void serve_done(int n_pass, int n_fail);
    endfunction
    // Hold the simulation for a debugger (`why`: what failed): register
    // with the daemon if not serving yet, then answer its requests in
    // this call, so sim time stands still, until a `resume` comes. 1: run
    // the failed test again, 0: go on, -1: cannot hold (no daemon).
    virtual function int dbg_wait(string why);
      return -1;
    endfunction
    // The sim time now, in fs (`pssvm_now_fs()`); events the engine sends
    // carry the last time set. Set before each `run` step of a call.
    virtual function void set_time(longint unsigned t_fs);
    endfunction
    // Trace test `name` into `dir` (one be-bc NDJSON file per test, each
    // event with its sim time `t` in fs); "" `name` ends the trace. The
    // file written, or "" (none, or `error(0)`).
    virtual function string trace(string dir, string name);
      return "";
    endfunction
    // Where call `h` is: the function it runs, and the file and line of the
    // statement it last stopped at (an access or an import; ""/0 before
    // the first, or when the engine does not know).
    virtual function string cur_op(int h);
      return "";
    endfunction
    virtual function string cur_file(int h);
      return "";
    endfunction
    virtual function int cur_line(int h);
      return 0;
    endfunction
  endclass

  // `+pssvm_dbgsig`: where the last call step was, for module `pssvm_dbg`
  // to show in a waveform. `pssvm_ctxt::call` sets them after each `run`
  // step; `pssvm_dbg_seq` counts the updates.
  bit    pssvm_dbg_on;
  bit    pssvm_dbg_init;
  string pssvm_dbg_op;
  string pssvm_dbg_file;
  int    pssvm_dbg_line;
  int    pssvm_dbg_seq;

  function bit pssvm_dbgsig_on();
    if (!pssvm_dbg_init) begin
      pssvm_dbg_on   = $test$plusargs("pssvm_dbgsig");
      pssvm_dbg_init = 1;
    end
    return pssvm_dbg_on;
  endfunction

  // The sim time now in fs, whatever the timescale (`1s` is one second in
  // this package's time unit).
  function longint unsigned pssvm_now_fs();
    return longint'($realtime * (1.0e15 / real'(1s)));
  endfunction

  // The engine generated facades (`<root>_root_vm::create`) run on. The
  // testbench sets it once, before it creates a model.
  pssvm_engine pssvm_default_eng;

  function void pssvm_set_default_engine(pssvm_engine e);
    pssvm_default_eng = e;
  endfunction

  // Hold sim time for a debugger (`pssvm_engine::dbg_wait`) on the default
  // engine; what a testbench calls where it wants to stop.
  function int pssvm_dbg_wait(string why = "");
    if (pssvm_default_eng == null) return -1;
    return pssvm_default_eng.dbg_wait(why);
  endfunction

  // `+pssvm_dbg_start`: an engine that has just loaded holds sim time
  // (`dbg_wait("start")`) before its first call, so a debugger can attach
  // and set breakpoints in a testbench that does not serve. Called by the
  // engines' constructors.
  function void pssvm_dbg_start_hold(pssvm_engine e);
    if (e == null || !$test$plusargs("pssvm_dbg_start")) return;
    $display("PSSVM-DBG: held start @%0t", $time);
    void'(e.dbg_wait("start"));
  endfunction

  function pssvm_engine pssvm_default_engine();
    return pssvm_default_eng;
  endfunction

`ifdef PSSVM_DPI
  // The native engine's DPI-C entry points: the host ABI with handles as
  // chandles and the request read field by field (a thin C adapter over
  // pssvm.h).
  import "DPI-C" context function int     pssvm_abi_version();
  import "DPI-C" context function chandle pssvm_dpi_load(string path, string opts);
  import "DPI-C" context function int     pssvm_dpi_construct(chandle e, string root_type);
  // Arguments are staged one by one (no open array: Verilator 5.052 cannot
  // compile one in an import).
  import "DPI-C" context function void    pssvm_dpi_arg_push(chandle e, longint unsigned v);
  import "DPI-C" context function chandle pssvm_dpi_call_start(chandle e, string entry,
                                                               string comp_path);
  import "DPI-C" context function int     pssvm_run(chandle h);
  import "DPI-C" context function int     pssvm_dpi_req_int(chandle h, string field);
  import "DPI-C" context function longint unsigned pssvm_dpi_req_u64(chandle h, string field);
  import "DPI-C" context function string  pssvm_dpi_req_str(chandle h, string field);
  import "DPI-C" context function int     pssvm_dpi_req_nargs(chandle h);
  import "DPI-C" context function longint unsigned pssvm_dpi_req_arg(chandle h, int i);
  import "DPI-C" context function int     pssvm_dpi_complete(chandle h, longint unsigned data);
  import "DPI-C" context function longint unsigned pssvm_dpi_result(chandle h);
  import "DPI-C" context function void    pssvm_call_end(chandle h);
  import "DPI-C" context function int     pssvm_notify(chandle e, int kind, int id,
                                                       longint unsigned value);
  import "DPI-C" context function string  pssvm_dpi_error(chandle e, chandle h);
  import "DPI-C" context function void    pssvm_free(chandle e);
  import "DPI-C" context function string  pssvm_dpi_test_name(chandle e, int i);
  import "DPI-C" context function string  pssvm_dpi_test_expect(chandle e, int i);
  import "DPI-C" context function string  pssvm_dpi_hook(chandle e, string kind, int i);
  import "DPI-C" context function int     pssvm_dpi_checks_failed(chandle e);
  // pssvm_run, with a debugger's stops (server mode, `debug`) served in C.
  import "DPI-C" context function int     pssvm_dpi_run(chandle h);
  // Server mode (the workspace daemon's sim socket), in C.
  import "DPI-C" context function string  pssvm_dpi_serve(chandle e, string name, string top);
  import "DPI-C" context function int     pssvm_dpi_poll(chandle e, int timeout_ms);
  import "DPI-C" context function string  pssvm_dpi_pending_sel(chandle e);
  import "DPI-C" context function void    pssvm_dpi_serve_test(chandle e, string name,
                                                               int passed);
  import "DPI-C" context function void    pssvm_dpi_serve_done(chandle e, int n_pass,
                                                               int n_fail);
  import "DPI-C" context function void    pssvm_dpi_set_time(chandle e, longint unsigned t_fs);
  import "DPI-C" context function int     pssvm_dpi_dbg_wait(chandle e, string why);

  class pssvm_engine_dpi extends pssvm_engine;
    chandle e;
    chandle calls[int];
    int     next_id = 1;
    string  root_type;

    // Load `path` (a .zbc image) and construct `root_type` ("" = the image's).
    function new(string path, string root_type = "", string opts = "");
      this.root_type = root_type;
      e = pssvm_dpi_load(path, opts);
      if (e != null && pssvm_dpi_construct(e, root_type) != 0) e = null;
      if (e != null) pssvm_dbg_start_hold(this);
    endfunction

    virtual function int fresh();
      return (e != null) ? pssvm_dpi_construct(e, root_type) : -1;
    endfunction

    virtual function int call_start(string entry, string comp_path,
                                    longint unsigned args[$]);
      chandle h;
      if (e == null) return 0;
      foreach (args[i]) pssvm_dpi_arg_push(e, args[i]);
      h = pssvm_dpi_call_start(e, entry, comp_path);
      if (h == null) return 0;
      calls[next_id] = h;
      return next_id++;
    endfunction

    virtual function pssvm_status_e run(int h);
      return pssvm_status_e'(pssvm_dpi_run(calls[h]));
    endfunction

    virtual function void request(int h, pssvm_req req);
      chandle c = calls[h];
      req.status   = pssvm_status_e'(pssvm_dpi_req_int(c, "status"));
      req.write    = pssvm_dpi_req_int(c, "kind") != 0;
      req.addr     = pssvm_dpi_req_u64(c, "addr");
      req.size     = pssvm_dpi_req_int(c, "size");
      req.data     = pssvm_dpi_req_u64(c, "data");
      req.reg_id   = pssvm_dpi_req_int(c, "reg_id");
      req.src_ref  = pssvm_dpi_req_str(c, "src_ref");
      req.fn_id    = pssvm_dpi_req_int(c, "fn_id");
      req.name     = pssvm_dpi_req_str(c, "name");
      req.blocking = pssvm_dpi_req_int(c, "blocking") != 0;
      req.on       = pssvm_wait_on_e'(pssvm_dpi_req_int(c, "on"));
      req.delay    = pssvm_dpi_req_u64(c, "delay");
      req.reason   = pssvm_dpi_req_str(c, "reason");
      req.args.delete();
      for (int i = 0; i < pssvm_dpi_req_nargs(c); i++)
        req.args.push_back(pssvm_dpi_req_arg(c, i));
    endfunction

    virtual function int complete(int h, longint unsigned data);
      return pssvm_dpi_complete(calls[h], data);
    endfunction

    virtual function longint unsigned result(int h);
      return pssvm_dpi_result(calls[h]);
    endfunction

    virtual function void call_end(int h);
      if (!calls.exists(h)) return;
      pssvm_call_end(calls[h]);
      calls.delete(h);
    endfunction

    virtual function int notify(int kind, int id, longint unsigned value);
      return pssvm_notify(e, kind, id, value);
    endfunction

    virtual function string error(int h);
      chandle c = null;
      if (calls.exists(h)) c = calls[h];
      return pssvm_dpi_error(e, c);
    endfunction

    // The image's test list (its "pssc.native" document) and the pssunit
    // ledgers' failure counts in the component tree.
    virtual function string test_name(int i);
      return (e != null) ? pssvm_dpi_test_name(e, i) : "";
    endfunction

    virtual function string test_expect(int i);
      return (e != null) ? pssvm_dpi_test_expect(e, i) : "";
    endfunction

    virtual function string hook(string kind, int i);
      return (e != null) ? pssvm_dpi_hook(e, kind, i) : "";
    endfunction

    virtual function int checks_failed();
      return (e != null) ? pssvm_dpi_checks_failed(e) : 0;
    endfunction

    // Server mode, in C (no Python): the channel, a `run`'s tests, holding
    // for a debugger and the debug stub (`debug` {on}).
    virtual function string serve(string name, string top);
      return (e != null) ? pssvm_dpi_serve(e, name, top) : "";
    endfunction

    virtual function int poll(int timeout_ms);
      return pssvm_dpi_poll(e, timeout_ms);
    endfunction

    virtual function string pending_sel();
      return pssvm_dpi_pending_sel(e);
    endfunction

    virtual function void serve_test(string name, int passed);
      pssvm_dpi_serve_test(e, name, passed);
    endfunction

    virtual function void serve_done(int n_pass, int n_fail);
      pssvm_dpi_serve_done(e, n_pass, n_fail);
    endfunction

    virtual function int dbg_wait(string why);
      return (e != null) ? pssvm_dpi_dbg_wait(e, why) : -1;
    endfunction

    virtual function void set_time(longint unsigned t_fs);
      pssvm_dpi_set_time(e, t_fs);
    endfunction
  endclass
`endif

  // What the testbench supplies. Bus accesses and imports are tasks: they may
  // consume simulation time. Override the widths the bus has; an access of a
  // width left alone is reported and reads 0.
  virtual class pssvm_platform_if;
    virtual task read8 (longint unsigned addr, output bit [7:0]  data);
      unsupported("read8", addr);  data = '0;
    endtask
    virtual task read16(longint unsigned addr, output bit [15:0] data);
      unsupported("read16", addr); data = '0;
    endtask
    virtual task read32(longint unsigned addr, output bit [31:0] data);
      unsupported("read32", addr); data = '0;
    endtask
    virtual task read64(longint unsigned addr, output bit [63:0] data);
      unsupported("read64", addr); data = '0;
    endtask
    virtual task write8 (longint unsigned addr, bit [7:0]  data);
      unsupported("write8", addr);
    endtask
    virtual task write16(longint unsigned addr, bit [15:0] data);
      unsupported("write16", addr);
    endtask
    virtual task write32(longint unsigned addr, bit [31:0] data);
      unsupported("write32", addr);
    endtask
    virtual task write64(longint unsigned addr, bit [63:0] data);
      unsupported("write64", addr);
    endtask

    // A target import the engine yields (`req.name`, `req.args`). A
    // non-blocking one (`req.blocking == 0`) must not consume time.
    virtual task call_import(pssvm_req req, output longint unsigned ret);
      $display("PSSVM-ERROR: no platform import '%s'", req.name);
      ret = 0;
    endtask

    // Engine time units -> simulation time (the package timescale).
    virtual task wait_time(longint unsigned delay);
      #(delay);
    endtask

    // The debug stub holds simulation time here until it resumes (P3.7).
    virtual task dbg_wait(pssvm_req req);
    endtask

    // Put the design back in its reset state, between two tests that run
    // in one simulation (pssvm_runner). A platform whose design cannot be
    // reset says so with `can_reset`; the runner then runs one test.
    virtual task reset();
    endtask
    virtual function bit can_reset();
      return 1;
    endfunction

    protected function void unsupported(string what, longint unsigned addr);
      $display("PSSVM-ERROR: platform has no %s (addr 'h%0h)", what, addr);
    endfunction
  endclass

  // An engine and its platform. `call` runs one entry to completion; fork
  // calls for parallel operations on the one model.
  class pssvm_ctxt;
    pssvm_engine      engine;
    pssvm_platform_if plat;
    string            last_error;
    int               n_errors;
    event             notified;

    function new(pssvm_engine eng, pssvm_platform_if platform);
      engine = eng;
      plat   = platform;
    endfunction

    // A host event (interrupt, channel put) for calls waiting on EVENT.
    function void notify(int kind, int id, longint unsigned value);
      void'(engine.notify(kind, id, value));
      ->notified;
    endfunction

    // The run loop (design §10.3). `ok` = 0 on an engine error.
    task call(string entry, string comp_path, longint unsigned args[$],
              output longint unsigned ret, output bit ok);
      pssvm_req req = new;
      int h;
      engine.set_time(pssvm_now_fs());
      h = engine.call_start(entry, comp_path, args);
      ret = 0;
      ok  = 0;
      if (h == 0) begin
        fail(0, entry);
        return;
      end
      forever begin
        pssvm_status_e st;
        engine.set_time(pssvm_now_fs());
        st = engine.run(h);
        if (pssvm_dbgsig_on()) begin
          pssvm_dbg_op   = engine.cur_op(h);
          pssvm_dbg_file = engine.cur_file(h);
          pssvm_dbg_line = engine.cur_line(h);
          pssvm_dbg_seq++;
        end
        case (st)
          PSSVM_DONE: begin
            ret = engine.result(h);
            ok  = 1;
            engine.call_end(h);
            // A call can wake another one waiting on EVENT (a channel put,
            // e.g. an irq hook calling `notify_irq`): let it run again.
            ->notified;
            return;
          end
          PSSVM_ACCESS: begin
            longint unsigned rdata = 0;
            engine.request(h, req);
            access(req, rdata);
            void'(engine.complete(h, rdata));
          end
          PSSVM_IMPORT: begin
            longint unsigned r;
            engine.request(h, req);
            plat.call_import(req, r);
            void'(engine.complete(h, r));
          end
          PSSVM_WAIT: begin
            engine.request(h, req);
            if (req.on == PSSVM_WAIT_TIME) plat.wait_time(req.delay);
            else @(notified);
          end
          PSSVM_STOP: begin
            engine.request(h, req);
            plat.dbg_wait(req);
          end
          default: begin
            fail(h, entry);
            engine.call_end(h);
            return;
          end
        endcase
      end
    endtask

    protected task access(pssvm_req req, output longint unsigned rdata);
      bit [7:0]  d8;
      bit [15:0] d16;
      bit [31:0] d32;
      bit [63:0] d64;
      rdata = 0;
      if (req.write) begin
        case (req.size)
          1: plat.write8 (req.addr, req.data[7:0]);
          2: plat.write16(req.addr, req.data[15:0]);
          4: plat.write32(req.addr, req.data[31:0]);
          default: plat.write64(req.addr, req.data);
        endcase
      end else begin
        case (req.size)
          1: begin plat.read8 (req.addr, d8);  rdata = 64'(d8);  end
          2: begin plat.read16(req.addr, d16); rdata = 64'(d16); end
          4: begin plat.read32(req.addr, d32); rdata = 64'(d32); end
          default: begin plat.read64(req.addr, d64); rdata = d64; end
        endcase
      end
    endtask

    protected function void fail(int h, string entry);
      last_error = engine.error(h);
      n_errors++;
      $display("PSSVM-ERROR: %s: %s", entry, last_error);
    endfunction
  endclass


  // Runs the image's tests: those `+pssunit=SEL` selects (comma-separated
  // globs, `*` and `?`, on `comp::fn` or `fn`), or every test. Each test
  // runs between the setup and teardown hooks; it passes when its calls
  // complete and no pssunit check fails (or, for a test that expects an
  // error, when one of them does). Prints one `PSSVM-TEST: PASS|FAIL name
  // @start..end` line per test (the sim time it started and ended at) and a `PSSVM-SUMMARY:` line.
  //
  // Each test starts on a fresh model and design: before every test but
  // the first, the runner builds the component tree again (engine
  // `fresh`) and resets the design (platform `reset`). A platform that
  // cannot (`can_reset` 0) runs only the first test; the others print
  // `PSSVM-TEST: SKIP name (...)` and count as neither passed nor failed.
  // An image with `@pssunit_pkg::session` functions runs them once, before
  // the first test, and then keeps the model and design from one test to
  // the next (no fresh tree, no reset).
  //
  // With `+pssvm_serve[=name]` the runner is in server mode instead: it
  // registers with the workspace daemon (`PSSC_SIM_SOCKET`, else the
  // cwd's) and answers its requests until the daemon ends the session;
  // each `run {sel}` request runs the tests `sel` selects, as above.
  //
  // With `+pssvm_dbg_on_fail` a failing test holds the simulation instead
  // of going on (`PSSVM-DBG: held name @t`; `pssvm_dbg_wait`): a debugger
  // can attach (`pssc sim attach`), the code be reloaded (`pssc sim
  // reload`), and `pssc sim resume [--rerun]` goes on, or runs the test
  // again first. Only the last run of a test counts.
  //
  // With `+pssvm_trace=DIR` each run of a test writes a trace of its
  // events, setup and teardown included, to `DIR/<test-id>.ndjson`
  // (`PSSVM-TRACE: file`), each event with its sim time: `pssc trace show
  // FILE --at 1.234us`.
  class pssvm_runner;
    pssvm_ctxt ctxt;
    string     sel[$];
    int        n_pass;
    int        n_fail;
    bit        serving;
    string     serve_name;
    string     top = "";
    bit        dbg_on_fail;
    string     trace_dir = "";
    int        n_skip;
    int        n_runs;      // tests run so far in this simulation
    bit        session_ok = 1;

    function new(pssvm_ctxt c);
      string arg;
      ctxt = c;
      if ($value$plusargs("pssunit=%s", arg)) split(arg);
      if ($value$plusargs("pssvm_serve=%s", serve_name)) serving = 1;
      else if ($test$plusargs("pssvm_serve")) serving = 1;
      dbg_on_fail = $test$plusargs("pssvm_dbg_on_fail");
      void'($value$plusargs("pssvm_trace=%s", trace_dir));
    endfunction

    // Server mode: register, then answer requests (simulation time moves
    // by 1 between polls) until the session ends. 0, or 1 when the
    // registration failed.
    task serve(output int failed);
      string sid = ctxt.engine.serve(serve_name, top);
      failed = 0;
      if (sid == "") begin
        $display("PSSVM-SERVE: failed %s", ctxt.engine.error(0));
        failed = 1;
        return;
      end
      $display("PSSVM-SERVE: %s", sid);
      forever begin
        int r = ctxt.engine.poll(100);
        if (r < 0) break;
        if (r == 2) serve_run();
        #1;
      end
      $display("PSSVM-SERVE: end");
    endtask

    // Run the pending `run` request's tests and answer it.
    protected task serve_run();
      sel.delete();
      split(ctxt.engine.pending_sel());
      n_pass = 0;
      n_fail = 0;
      n_skip = 0;
      for (int i = 0; ; i++) begin
        string name = ctxt.engine.test_name(i);
        int was_fail = n_fail;
        if (name == "") break;
        if (!selected(name)) continue;
        run_test(i, name);
        ctxt.engine.serve_test(name, (n_fail == was_fail) ? 1 : 0);
      end
      summary();
      ctxt.engine.serve_done(n_pass, n_fail);
    endtask

    // Does `name` (`comp::fn`) match the selection?
    function bit selected(string name);
      string short = name;
      if (sel.size() == 0) return 1;
      for (int i = name.len() - 1; i > 0; i--)
        if (name[i] == ":" && name[i-1] == ":") begin
          short = name.substr(i + 1, name.len() - 1);
          break;
        end
      foreach (sel[i])
        if (glob(sel[i], name) || glob(sel[i], short)) return 1;
      return 0;
    endfunction

    // Run the selected tests; returns the number that failed.
    task run(output int failed);
      if (serving) begin
        serve(failed);
        return;
      end
      for (int i = 0; ; i++) begin
        string name = ctxt.engine.test_name(i);
        if (name == "") break;
        if (selected(name)) run_test(i, name);
      end
      summary();
      failed = n_fail;
    endtask

    // The `PSSVM-SUMMARY:` line ("..., N skipped" only when some were).
    protected function void summary();
      if (n_skip > 0)
        $display("PSSVM-SUMMARY: %0d passed, %0d failed, %0d skipped", n_pass,
                 n_fail, n_skip);
      else
        $display("PSSVM-SUMMARY: %0d passed, %0d failed", n_pass, n_fail);
    endfunction

    // Does the image have `@pssunit_pkg::session` functions?
    function bit has_session();
      return ctxt.engine.hook("session", 0) != "";
    endfunction

    task run_test(int i, string name);
      bit pass;
      if (n_runs > 0 && !has_session() && !ctxt.plat.can_reset()) begin
        $display("PSSVM-TEST: SKIP %s (platform cannot reset: one test per sim)",
                 name);
        n_skip++;
        return;
      end
      forever begin
        run_once(i, name, pass);
        if (pass || !dbg_on_fail) break;
        $display("PSSVM-DBG: held %s @%0t", name, $time);
        if (ctxt.engine.dbg_wait(name) != 1) break;
      end
      if (pass) n_pass++; else n_fail++;
    endtask

    protected task run_once(int i, string name, output bit pass);
      longint unsigned none[$];
      longint unsigned ret;
      string expect_ = ctxt.engine.test_expect(i);
      int checks;
      time t0 = $time;
      bit ok;
      bit all_ok = 1;
      if (has_session()) begin
        if (n_runs == 0) begin
          hooks("session", session_ok);
          if (!session_ok)
            $display("PSSVM-ERROR: a session function failed: every test fails");
        end
      end else if (n_runs > 0 && ctxt.plat.can_reset()) begin
        if (ctxt.engine.fresh() != 0) begin
          $display("PSSVM-ERROR: fresh tree: %s", ctxt.engine.error(0));
          all_ok = 0;
        end
        ctxt.plat.reset();
      end
      n_runs++;
      all_ok &= session_ok;
      checks = ctxt.engine.checks_failed();
      if (trace_dir != "") begin
        string f = ctxt.engine.trace(trace_dir, name);
        if (f == "") $display("PSSVM-TRACE: failed %s", ctxt.engine.error(0));
        else $display("PSSVM-TRACE: %s", f);
      end
      hooks("setup", all_ok);
      if (all_ok) begin
        ctxt.call(name, "", none, ret, ok);
        all_ok = ok;
      end
      hooks("teardown", all_ok);
      if (trace_dir != "") void'(ctxt.engine.trace(trace_dir, ""));
      if (ctxt.engine.checks_failed() != checks) all_ok = 0;
      pass = (expect_ == "") ? all_ok : !all_ok;
      $display("PSSVM-TEST: %s %s @%0t..%0t", pass ? "PASS" : "FAIL", name,
               t0, $time);
    endtask

    protected task hooks(string kind, inout bit all_ok);
      longint unsigned none[$];
      longint unsigned ret;
      bit ok;
      for (int j = 0; ; j++) begin
        string fn = ctxt.engine.hook(kind, j);
        if (fn == "") break;
        ctxt.call(fn, "", none, ret, ok);
        if (!ok) all_ok = 0;
      end
    endtask

    protected function void split(string arg);
      int b = 0;
      for (int i = 0; i <= arg.len(); i++)
        if (i == arg.len() || arg[i] == ",") begin
          if (i > b) sel.push_back(arg.substr(b, i - 1));
          b = i + 1;
        end
    endfunction

    // `*` and `?` glob match of the whole of `s`.
    static function bit glob(string p, string s);
      int pi = 0, si = 0, star = -1, mark = 0;
      while (si < s.len()) begin
        if (pi < p.len() && (p[pi] == "?" || p[pi] == s[si])) begin
          pi++; si++;
        end else if (pi < p.len() && p[pi] == "*") begin
          star = pi++; mark = si;
        end else if (star >= 0) begin
          pi = star + 1; si = ++mark;
        end else return 0;
      end
      while (pi < p.len() && p[pi] == "*") pi++;
      return pi == p.len();
    endfunction
  endclass

endpackage

// Where the PSS model is, as signals (`+pssvm_dbgsig`): instantiate it
// anywhere (`pssvm_dbg u_pssvm_dbg();`) and add its signals to the
// waveform. `cur_op` is the function a call runs, `cur_file`/`cur_line`
// the statement it last stopped at; `seq` counts the updates.
module pssvm_dbg;
  string cur_op;
  string cur_file;
  int    cur_line;
  int    seq;

  always @(pssvm_pkg::pssvm_dbg_seq) begin
    cur_op   = pssvm_pkg::pssvm_dbg_op;
    cur_file = pssvm_pkg::pssvm_dbg_file;
    cur_line = pssvm_pkg::pssvm_dbg_line;
    seq      = pssvm_pkg::pssvm_dbg_seq;
  end
endmodule
