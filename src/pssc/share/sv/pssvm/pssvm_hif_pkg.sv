// pssvm_hif_pkg -- `pssvm_engine` over pyhdl-if: pyvm (pssc's Python VM)
// runs the image in the simulator's Python. Hand-written once, shipped with
// pssc (`pssc sv-pssvm-path --py`).
//
// Compile after pyhdl_if.sv (pyhdl-if's share/dpi), pssvm_pkg.sv and
// pssvm_hif_api_pkg.sv (generated from the shim by
// `python -m hdl_if api-gen-sv -m pssvm_dpi_shim -p pssvm_hif_api_pkg`).
// At run time PYTHONPATH must name pssc and the shim's directory
// (`pssc sv-pssvm-path --py`).
//
//   pssvm_engine eng = pssvm_engine_hif::create("x.zbc", "", "fake");
//
// `platform` "fake" lets back-door checks (pssunit check_reg) see the values
// the simulation's bus returned.

package pssvm_hif_pkg;
  import pssvm_pkg::*;
  import pssvm_hif_api_pkg::*;

  class pssvm_engine_hif extends pssvm_engine;
    PssvmEngine_exp_impl py;
    bit                  loaded;

    function new(string path, string root_type = "", string platform = "fake");
      py = new();
      loaded = py.load(path, root_type, platform) == 0;
    endfunction

    // `new` that says why it failed: null and the error on stdout.
    static function pssvm_engine_hif create(string path, string root_type = "",
                                            string platform = "fake");
      pssvm_engine_hif e = new(path, root_type, platform);
      if (!e.loaded) begin
        $display("PSSVM-ERROR: load %s: %s", path, e.py.error(0));
        return null;
      end
      pssvm_dbg_start_hold(e);
      return e;
    endfunction

    virtual function int call_start(string entry, string comp_path,
                                    longint unsigned args[$]);
      if (!loaded) return 0;
      foreach (args[i]) py.push_arg(args[i]);
      return py.call_start(entry, comp_path);
    endfunction

    virtual function pssvm_status_e run(int h);
      return pssvm_status_e'(py.run(h));
    endfunction

    virtual function void request(int h, pssvm_req req);
      int n;
      req.status   = pssvm_status_e'(py.req_int(h, "status"));
      req.write    = py.req_int(h, "kind") != 0;
      req.addr     = py.req_u64(h, "addr");
      req.size     = py.req_int(h, "size");
      req.data     = py.req_u64(h, "data");
      req.reg_id   = py.req_int(h, "reg_id");
      req.src_ref  = py.req_str(h, "src_ref");
      req.fn_id    = py.req_int(h, "fn_id");
      req.name     = py.req_str(h, "name");
      req.blocking = py.req_int(h, "blocking") != 0;
      req.on       = pssvm_wait_on_e'(py.req_int(h, "on"));
      req.delay    = py.req_u64(h, "delay");
      req.reason   = py.req_str(h, "reason");
      req.args.delete();
      n = py.req_nargs(h);
      for (int i = 0; i < n; i++) req.args.push_back(py.req_arg(h, i));
    endfunction

    virtual function int complete(int h, longint unsigned data);
      return py.complete(h, data);
    endfunction

    virtual function longint unsigned result(int h);
      return py.result(h);
    endfunction

    virtual function void call_end(int h);
      py.call_end(h);
    endfunction

    virtual function int notify(int kind, int id, longint unsigned value);
      return py.notify(kind, id, value);
    endfunction

    virtual function string error(int h);
      return py.error(h);
    endfunction

    // pssunit checks that failed so far (0 = all passed).
    virtual function int checks_failed();
      return py.checks_failed();
    endfunction

    virtual function string api_check(string spec);
      return py.api_check(spec);
    endfunction

    virtual function string test_name(int i);
      return py.test_name(i);
    endfunction

    virtual function string test_expect(int i);
      return py.test_expect(i);
    endfunction

    virtual function string hook(string kind, int i);
      return py.hook(kind, i);
    endfunction

    virtual function string serve(string name, string top);
      return py.serve(name, top);
    endfunction

    virtual function int poll(int timeout_ms);
      py.set_time(pssvm_now_fs());
      return py.poll(timeout_ms);
    endfunction

    virtual function string pending_sel();
      return py.pending_sel();
    endfunction

    virtual function void set_time(longint unsigned t_fs);
      py.set_time(t_fs);
    endfunction

    virtual function int fresh();
      return py.fresh();
    endfunction

    virtual function string cur_op(int h);
      return py.cur_op(h);
    endfunction

    virtual function string cur_file(int h);
      return py.cur_file(h);
    endfunction

    virtual function int cur_line(int h);
      return py.cur_line(h);
    endfunction

    virtual function string trace(string dir, string name);
      py.set_time(pssvm_now_fs());
      return py.trace(dir, name);
    endfunction

    virtual function void serve_test(string name, int passed);
      py.set_time(pssvm_now_fs());
      py.serve_test(name, passed);
    endfunction

    virtual function void serve_done(int n_pass, int n_fail);
      py.set_time(pssvm_now_fs());
      py.serve_done(n_pass, n_fail);
    endfunction

    virtual function int dbg_wait(string why);
      py.set_time(pssvm_now_fs());
      return py.dbg_wait(why);
    endfunction
  endclass

endpackage
