"""pssvm_dpi_shim -- pyvm behind pssvm_pkg's ``pssvm_engine`` (pyhdl-if).

The SV side (``share/sv/pssvm/pssvm_hif_pkg.sv``) holds a ``PssvmEngine``
and calls it synchronously: the host ABI (zuspec-be-bc ``docs/spec/
host-abi.md``) with int call handles, the pending request read field by
field and call arguments pushed one at a time (``push_arg``) before
``call_start``. Bus accesses and the model's own imports go back to SV
(``pssvm_platform_if``); pssunit_pkg's imports (checks, ``base``) and
imports a ``use`` double implements run here, in the run loop, without
leaving Python.

Put this directory (``pssc sv-pssvm-path --py``) on PYTHONPATH; it imports
pssc and zuspec-be-bc. ``Engine`` is the same API with no pyhdl-if, for
tests and other hosts.

Values: SV ``longint unsigned`` crosses as a signed 64-bit Python int
(pyhdl-if converts with ``PyLong_AsLong``); ``_u64``/``_s64`` convert.
"""
import ctypes as ct
import json

_M64 = (1 << 64) - 1


def _u64(v) -> int:
    return int(v or 0) & _M64


def _s64(v) -> int:
    v = _u64(v)
    return v - (1 << 64) if v >> 63 else v


#: the hook lists of an image (META ``pssc.image``), as ``Engine.hook`` names them
HOOK_KINDS = ("session", "setup", "teardown")


class Engine:
    """pyvm over the host ABI, by int handles. Every method catches: an
    error is a status (``run`` -> -1, ``call_start`` -> 0) and its JSON is
    ``error(h)`` (h = 0: the engine's own, e.g. a failed ``load``)."""

    def __init__(self):
        self.session = None
        self.root = ""
        self.calls = {}
        self.next_id = 1
        self.args = []
        self.errors = {}
        #: h -> [entry, file, line]: where call h last was (``cur_*``)
        self.where = {}
        self.tests = []
        self.hooks = {"session": [], "setup": [], "teardown": []}
        self.root_type = None
        self.now_fs = 0

    # -- set-up ------------------------------------------------------------ #

    def load(self, path: str, root_type: str = "", platform: str = "") -> int:
        """Load image ``path`` (``pssc build --image``) and construct its
        tree. ``platform`` "fake": the register fake backs pssunit's
        back-door reads (``peek_reg``); bus accesses still go to SV.
        Returns 0, or -1 (``error(0)``)."""
        try:
            from pssc.devenv import build_cmd
            image, items, meta = build_cmd.load(path)
            doc = meta[build_cmd.IMAGE]
            self.load_model(image.model, root_type or None, platform,
                            doc.get("root", ""))
            self.image = path
            self.tests = [it for it in items
                          if it.kind == "function" and not it.meta.skip]
            self.hooks = {k: list(doc.get(k) or [])
                          for k in HOOK_KINDS}
            return 0
        except Exception as e:
            return self._fail(0, e)

    def load_model(self, model, root_type=None, platform: str = "",
                   root: str = ""):
        from pssc.testing import runner
        from pssc.testing import vm as _vm
        plat = (runner._FakeRunPlatform(model) if platform == "fake"
                else runner._RunPlatform(model))
        self.session = _vm.Session(model, platform=plat)
        self.session.construct(root_type)
        self.root_type = root_type
        self.root = root

    def fresh(self) -> int:
        """Build the component tree again, for the next test (plan P3.8):
        a new VM on the same model (the current generation's), platform and
        doubles. The pssunit check counts start again at 0, and an attached
        debugger follows the new VM. Returns 0, or -1 (``error(0)``)."""
        try:
            s = self.session
            if s is None:
                raise RuntimeError("no image loaded")
            for h in list(self.calls):
                self.call_end(h)
            s.construct(self.root_type)
            dbg = getattr(self, "_dbg", None)
            if dbg is not None:
                dbg[0].rebind(s.vm)
            return 0
        except Exception as e:
            return self._fail(0, e)

    # -- calls ------------------------------------------------------------- #

    def push_arg(self, v: int) -> None:
        self.args.append(_u64(v))

    def call_start(self, entry: str, comp_path: str = "") -> int:
        """Start function ``entry`` (``comp::f`` or plain ``f``) on the
        instance at ``comp_path`` ("" = the root) with the pushed args."""
        from zuspec.be.bc.interp.host import HostCall
        args, self.args = self.args, []
        try:
            s = self.session
            if s is None:
                raise RuntimeError("no image loaded")
            inst = s.instance(comp_path)
            e = s.model.entries.get(entry)
            if e is None or e.kind != "function":
                e = s.entry(inst, entry.rpartition("::")[2])
            if len(args) != len(e.params):
                raise TypeError(f"{entry} takes {len(e.params)} arguments, "
                                f"not {len(args)}")
            vm = s.vm
            frame = vm.sched.new_frame(s.model.coros[e.coro], seed=s.seed,
                                       obj=vm._new_obj(e.coro))
            frame.cobj = vm.cobj
            vm.set_comp(frame, inst.id)
            frame.args = list(args)
            call = HostCall(vm, frame, construct=False)
        except Exception as e:
            self._fail(0, e)
            return 0
        h, self.next_id = self.next_id, self.next_id + 1
        self.calls[h] = call
        self.where[h] = [entry, "", 0]
        return h

    def run(self, h: int) -> int:
        """Run call ``h`` until SV is needed; the host ABI status."""
        from zuspec.be.bc.interp import host as H
        try:
            call = self.calls[h]
            while True:
                r = call.run()
                self._at(h, r.src_ref)
                if r.status == H.IMPORT and self._local(r.fn_id):
                    try:
                        v = self.session.vm.import_provider.call(r.fn_id, r.args)
                    except BaseException as e:
                        call.fail(e)
                        continue
                    call.complete(v)
                    continue
                if r.status == H.ERROR:
                    self.errors[h] = json.dumps(r.error or {"message": str(r.exc)})
                return r.status
        except Exception as e:
            return self._fail(h, e)

    def _at(self, h: int, src_ref: int) -> None:
        """Call ``h`` stopped at ``src_ref`` (0: no place; keep the last)."""
        if not src_ref:
            return
        try:
            e = self.session.model.prov.entries[src_ref]
        except (AttributeError, IndexError, KeyError, TypeError):
            return
        w = self.where.get(h)
        if w is not None:
            w[1], w[2] = e.file or "", e.line or 0

    def cur_op(self, h: int) -> str:
        """The function call ``h`` runs (as ``call_start`` named it)."""
        return self.where.get(h, ["", "", 0])[0]

    def cur_file(self, h: int) -> str:
        """The source file of the statement call ``h`` last stopped at
        (an access or an import); "" before the first."""
        return self.where.get(h, ["", "", 0])[1]

    def cur_line(self, h: int) -> int:
        """That statement's line; 0 before the first."""
        return self.where.get(h, ["", "", 0])[2]

    def _local(self, fn_id: int) -> bool:
        """An import Python serves: pssunit_pkg's own, or a double's."""
        from pssc.testing import imports as _imports
        prov = self.session.vm.import_provider
        if getattr(prov, "is_mocked", None) and prov.is_mocked(fn_id):
            return True
        name = getattr(prov, "names", {}).get(fn_id, "")
        return f"{_imports.PACKAGE}::{name.rpartition('::')[2]}" in _imports.IMPORTS

    def _req(self, h: int):
        return self.calls[h].request

    def req_int(self, h: int, field: str) -> int:
        """An int field of the pending request: status, kind (1 = write),
        size, reg_id (-1: none), fn_id, blocking, on (0 TIME, 1 EVENT)."""
        r = self._req(h)
        if field == "kind":
            return 1 if r.kind == "w" else 0
        if field == "on":
            return 1 if r.on == "EVENT" else 0
        if field == "reg_id":
            return -1 if r.reg_id is None else int(r.reg_id)
        return int(getattr(r, field) or 0)

    def req_u64(self, h: int, field: str) -> int:
        """addr, data or delay."""
        return _s64(getattr(self._req(h), field))

    def req_str(self, h: int, field: str) -> str:
        """name (an import's, as the model names it), src_ref, reason."""
        r = self._req(h)
        if field == "name":
            prov = self.session.vm.import_provider
            return r.name or getattr(prov, "names", {}).get(r.fn_id, "") or ""
        return str(getattr(r, field, "") or "")

    def req_nargs(self, h: int) -> int:
        return len(self._req(h).args)

    def req_arg(self, h: int, i: int) -> int:
        return _s64(self._req(h).args[i])

    def complete(self, h: int, data: int) -> int:
        """Answer the pending access (the read value; ignored for a write)
        or import (its result)."""
        try:
            call = self.calls[h]
            r = call.request
            write = r.kind == "w" and r.status == 1
            call.complete(None if write else _u64(data))
            if r.status == 1:
                self._shadow(r, r.data if write else _u64(data))
            return 0
        except Exception as e:
            return self._fail(h, e)

    def _shadow(self, r, v: int) -> None:
        """Keep the register fake's value of a register SV accessed: the
        simulation is the truth, the fake answers back-door reads
        (``check_reg``, ``peek_reg``)."""
        plat = self.session.platform
        if not hasattr(plat, "reg_at"):
            return
        plat.note_access(r.reg_id, r.src_ref)   # places the device, as a
        cell = plat._reg(r.addr)                # bus access would
        if cell is not None:
            plat.values[cell] = v

    def result(self, h: int) -> int:
        try:
            return _s64(self.calls[h].result)
        except Exception as e:
            self._fail(h, e)
            return 0

    def call_end(self, h: int) -> None:
        call = self.calls.pop(h, None)
        self.where.pop(h, None)
        if call is not None:
            call.end()

    def notify(self, kind: int, id: int, value: int) -> int:
        for call in self.calls.values():
            call.notify(kind, id, _u64(value))
        return 0

    def error(self, h: int) -> str:
        return self.errors.get(h, "")

    def _fail(self, h: int, e: BaseException) -> int:
        self.errors[h] = json.dumps({"message": f"{type(e).__name__}: {e}"})
        return -1

    # -- tests ------------------------------------------------------------- #

    def test_name(self, i: int) -> str:
        """Test ``i`` of the image (its function tests, not skipped), as
        ``comp::fn``; "" past the last."""
        return self.tests[i].qname if 0 <= i < len(self.tests) else ""

    def test_expect(self, i: int) -> str:
        """The error test ``i`` expects (``expect_error``), or ""."""
        if not 0 <= i < len(self.tests):
            return ""
        return self.tests[i].expect_error or ""

    def hook(self, kind: str, i: int) -> str:
        """Session (``kind`` "session", run once per simulation), setup
        ("setup") or teardown function ``i``, as ``comp::fn``, in the order
        to call them; "" past the last. Unlike ``pssc test``'s, "setup"
        does not include the session functions."""
        fns = self.hooks.get(kind, [])
        return fns[i] if 0 <= i < len(fns) else ""

    # -- results ------------------------------------------------------------ #

    def checks_failed(self) -> int:
        """pssunit_pkg checks failed so far in this engine."""
        from pssc.testing import bc_session
        s = self.session
        led = getattr(s.run_state, "ledger", None)
        n = int(getattr(led, "n_fail", 0) or 0) if led is not None else 0
        return n + bc_session.ledger(s.model, s.comp_obj)[1]

    def api_check(self, spec: str) -> str:
        """"" when the image has every function of ``spec`` -- ``;``-separated
        ``comp::fn(p,s.f)`` lines, ``->r`` when it returns a value, as a
        generated facade (target op-model-sv-vm) calls them -- with those
        parameter leaves (the value ABI's, by name and order) and result;
        else what differs, one clause per function."""
        try:
            entries = self.session.model.entries
            bad = []
            for line in filter(None, spec.split(";")):
                sym, _, rest = line.partition("(")
                params, _, ret = rest.partition(")")
                want = [p for p in params.split(",") if p]
                e = entries.get(sym)
                if e is None or e.kind != "function":
                    bad.append(f"{sym}: not in the image")
                    continue
                have = [n for n, _ in e.params]
                if have != want:
                    bad.append(f"{sym}: parameters ({','.join(have)}) in the "
                               f"image, ({','.join(want)}) in the facade")
                elif (e.returns is not None) != (ret == "->r"):
                    bad.append(f"{sym}: " + ("returns a value in the image, "
                               "not in the facade" if e.returns is not None
                               else "returns nothing in the image"))
            return "; ".join(bad)
        except Exception as e:
            return f"{type(e).__name__}: {e}"

    # -- server mode -------------------------------------------------------- #

    def set_time(self, t_fs: int) -> None:
        """The sim time now, in fs (``pssvm_now_fs()``): the SV side sets it
        before each step of a call and before each runner hook, and every
        event to the daemon carries it as ``t``."""
        self.now_fs = _u64(t_fs)

    def trace(self, directory: str, name: str) -> str:
        """Trace test ``name`` (the runner's ``+pssvm_trace=DIR``): its
        events go to ``<directory>/<test-id>.ndjson``, as ``pssc test
        --trace`` writes them, each with ``t``, the sim time in fs. ""
        ``name`` ends the trace. Returns the file, or "" (``error(0)``)."""
        old = getattr(self, "_trace", None)
        tee = self.session.events if self.session is not None else None
        if old is not None:
            self._trace = None
            if tee is not None and tee.sink is old[0]:
                tee.sink, tee.watch = None, 0
                self.session.vm.watch(self.session.watch)
            old[0].stream.close()
        if not name:
            return ""
        try:
            import os
            from pssc.testing import bc_session
            from pssc.testing.trace import TraceWriter, trace_path
            if self.session is None:
                raise RuntimeError("no image loaded")
            path = trace_path(directory, name)
            os.makedirs(directory or ".", exist_ok=True)
            w = TraceWriter(open(path, "w"), self.session.model,
                            clock=lambda: self.now_fs, test=name,
                            seed=self.session.seed)
            tee.sink, tee.watch = w, w.watch
            self.session.vm.watch(self.session.watch | w.watch)
            bc_session.watch_checks(self.session.vm)
            self._trace = (w, path)
            return path
        except Exception as e:
            self._fail(0, e)
            return ""

    def serve(self, name: str = "", top: str = "") -> str:
        """Register with the workspace daemon's sim socket (env
        ``PSSC_SIM_SOCKET``, else the daemon of the cwd's workspace, which
        is not started from here) as ``name``. Returns the session id, or
        "" (``error(0)``)."""
        try:
            from pssc.devenv.sim import serve
            self.server = serve.Server(name, {
                "top": top, "image": getattr(self, "image", "")},
                handlers={"run": self._on_run, "reload": self._on_reload,
                          "resume": self._on_resume},
                direct={"debug": self._on_debug},
                clock=lambda: self.now_fs)
            self._run = None
            return self.server.connect(start=False)
        except Exception as e:
            self.server = None
            self._fail(0, e)
            return ""

    def poll(self, timeout_ms: int = 0) -> int:
        """Answer one request from the daemon, waiting up to ``timeout_ms``
        (< 0: until one comes): 1 answered, 2 tests to run (``pending_sel``;
        the testbench runs them, then ``serve_done``), 0 none, -1 the
        channel closed or the daemon ended the session."""
        srv = getattr(self, "server", None)
        if srv is None or srv.ended or srv.closed:
            return -1
        t = None if timeout_ms < 0 else timeout_ms / 1000.0
        done = srv.poll(t)
        if srv.ended:
            srv.close()
            return -1
        if getattr(self, "_run", None) is not None:
            return 2
        if done:
            return 1
        return -1 if srv.closed else 0

    def _on_debug(self, args: dict):
        """``debug`` {on}: on puts a debug stub on the VM (the daemon then
        ``attach``es it with ``entry: false``); it stops inside the calls SV
        makes, blocking them, and with them sim time. Off ends it. Answered
        on the channel's reader thread: a stopped run blocks ``poll``."""
        import threading
        from zuspec.be.bc.interp.debug import Stub
        old = getattr(self, "_dbg", None)
        if old is not None:
            stub, done = old
            self._dbg = None
            self.server.attach_stub(None)
            if stub.state not in ("detached", "exited"):
                stub.request({"cmd": "detach"})
            done.set()
            stub.join(5.0)
        if not args.get("on", True):
            return {}
        if self.session is None:
            raise RuntimeError("no image loaded")
        done = threading.Event()
        stub = Stub(self.session.vm, run=done.wait)
        self._dbg = (stub, done)
        self.server.attach_stub(stub)
        return {"image": getattr(self, "image", "")}

    def dbg_wait(self, why: str = "") -> int:
        """Hold the simulation for a debugger (plan P3.7b; the runner's
        ``+pssvm_dbg_on_fail``). Registers with the daemon if not serving
        yet, sends a ``held`` event {why}, then answers requests (``debug``,
        ``reload``, ...) on this, the SV, thread, so sim time stands still,
        until a ``resume`` {rerun} comes. 1: run the failed test again, 0:
        go on, -1: no daemon, or the session ended."""
        srv = getattr(self, "server", None)
        if srv is None or srv.ended or srv.closed:
            if not self.serve(""):
                return -1
            srv = self.server
        self._resume = None
        self._held = why
        srv.event("held", {"why": why})
        try:
            while self._resume is None:
                srv.poll(0.1)
                if srv.ended or srv.closed:
                    srv.close()
                    return -1
        finally:
            self._held = None
        rerun, self._resume = self._resume, None
        srv.event("resumed", {"why": why, "rerun": rerun})
        return 1 if rerun else 0

    def _on_resume(self, args: dict):
        if getattr(self, "_held", None) is None:
            raise RuntimeError("not held: nothing to resume")
        self._resume = bool(args.get("rerun"))
        return {"why": self._held, "rerun": self._resume}

    def _on_run(self, args: dict):
        from pssc.devenv.sim import serve
        if getattr(self, "_held", None) is not None:
            raise RuntimeError("held after a failed test: resume it first")
        if self._run is not None:
            raise RuntimeError("a run is in progress")
        sel = args.get("sel") or []
        if isinstance(sel, str):
            sel = [sel]
        self._run = (self.server.seq, ",".join(sel))
        return serve.DEFER

    def _on_reload(self, args: dict):
        """Hot reload (plan P3.6c): the image at ``args["image"]`` (default:
        the one loaded, rebuilt in place) becomes the engine's next
        generation. Requests are answered between runs, so it applies at
        once, at a test boundary; the tests and hooks are the new image's.
        A refusal is error ``refused`` (its ``detail``: be-bc's
        ``ReloadRefused.to_json()``) and leaves the engine as it was."""
        from pssc.devenv import build_cmd
        from pssc.devenv.sim import serve
        path = args.get("image") or getattr(self, "image", "")
        if not path:
            raise RuntimeError("no image: give one to reload")
        image, items, meta = build_cmd.load(path)
        try:
            r = self.session.reload(image.model, cancel=bool(args.get("cancel")),
                                    strict=bool(args.get("strict")))
        except RuntimeError as e:
            if not hasattr(e, "to_json"):
                raise
            self.server.reply(self.server.seq, code="refused", detail=e.to_json(),
                              err=f"reload refused ({e.reason}): {e}")
            return serve.DEFER
        doc = meta[build_cmd.IMAGE]
        self.image = path
        self.server.info["image"] = path
        self.tests = [it for it in items
                      if it.kind == "function" and not it.meta.skip]
        self.hooks = {k: list(doc.get(k) or []) for k in HOOK_KINDS}
        return dict(r, image=path, reimported=self.session.reimport())

    def pending_sel(self) -> str:
        """The test selection of the pending ``run`` (``+pssunit`` form;
        "" = every test)."""
        run = getattr(self, "_run", None)
        return run[1] if run else ""

    def serve_test(self, name: str, passed: int) -> None:
        """A test of the pending ``run`` finished: a ``test`` event."""
        srv = getattr(self, "server", None)
        if srv is not None:
            srv.event("test", {"name": name,
                               "status": "pass" if passed else "fail"})

    def serve_done(self, n_pass: int, n_fail: int) -> None:
        """The pending ``run`` finished: answer it with the counts."""
        srv, run = getattr(self, "server", None), getattr(self, "_run", None)
        self._run = None
        if srv is not None and run is not None:
            srv.reply(run[0], {"passed": n_pass, "failed": n_fail})


try:
    import hdl_if as hif
except ImportError:          # Engine alone, without a simulator
    hif = None

if hif is not None:

    @hif.api
    class PssvmEngine(object):
        """``Engine`` for SV (pyhdl-if). ``api-gen-sv`` of this module gives
        ``PssvmEngine_exp_impl``, which ``pssvm_engine_hif`` wraps."""

        def __init__(self):
            self.e = Engine()

        @hif.exp
        def load(self, path: str, root_type: str, platform: str) -> ct.c_int32:
            return self.e.load(path, root_type, platform)

        @hif.exp
        def fresh(self) -> ct.c_int32:
            return self.e.fresh()

        @hif.exp
        def push_arg(self, v: ct.c_uint64):
            self.e.push_arg(v)

        @hif.exp
        def call_start(self, entry: str, comp_path: str) -> ct.c_int32:
            return self.e.call_start(entry, comp_path)

        @hif.exp
        def run(self, h: ct.c_int32) -> ct.c_int32:
            return self.e.run(h)

        @hif.exp
        def req_int(self, h: ct.c_int32, field: str) -> ct.c_int32:
            return self.e.req_int(h, field)

        @hif.exp
        def req_u64(self, h: ct.c_int32, field: str) -> ct.c_uint64:
            return self.e.req_u64(h, field)

        @hif.exp
        def req_str(self, h: ct.c_int32, field: str) -> str:
            return self.e.req_str(h, field)

        @hif.exp
        def req_nargs(self, h: ct.c_int32) -> ct.c_int32:
            return self.e.req_nargs(h)

        @hif.exp
        def req_arg(self, h: ct.c_int32, i: ct.c_int32) -> ct.c_uint64:
            return self.e.req_arg(h, i)

        @hif.exp
        def complete(self, h: ct.c_int32, data: ct.c_uint64) -> ct.c_int32:
            return self.e.complete(h, data)

        @hif.exp
        def result(self, h: ct.c_int32) -> ct.c_uint64:
            return self.e.result(h)

        @hif.exp
        def call_end(self, h: ct.c_int32):
            self.e.call_end(h)

        @hif.exp
        def notify(self, kind: ct.c_int32, id: ct.c_int32,
                   value: ct.c_uint64) -> ct.c_int32:
            return self.e.notify(kind, id, value)

        @hif.exp
        def error(self, h: ct.c_int32) -> str:
            return self.e.error(h)

        @hif.exp
        def test_name(self, i: ct.c_int32) -> str:
            return self.e.test_name(i)

        @hif.exp
        def test_expect(self, i: ct.c_int32) -> str:
            return self.e.test_expect(i)

        @hif.exp
        def hook(self, kind: str, i: ct.c_int32) -> str:
            return self.e.hook(kind, i)

        @hif.exp
        def checks_failed(self) -> ct.c_int32:
            return self.e.checks_failed()

        @hif.exp
        def api_check(self, spec: str) -> str:
            return self.e.api_check(spec)

        @hif.exp
        def serve(self, name: str, top: str) -> str:
            return self.e.serve(name, top)

        @hif.exp
        def trace(self, directory: str, name: str) -> str:
            return self.e.trace(directory, name)

        @hif.exp
        def cur_op(self, h: ct.c_int32) -> str:
            return self.e.cur_op(h)

        @hif.exp
        def cur_file(self, h: ct.c_int32) -> str:
            return self.e.cur_file(h)

        @hif.exp
        def cur_line(self, h: ct.c_int32) -> ct.c_int32:
            return self.e.cur_line(h)

        @hif.exp
        def set_time(self, t_fs: ct.c_uint64):
            self.e.set_time(t_fs)

        @hif.exp
        def poll(self, timeout_ms: ct.c_int32) -> ct.c_int32:
            return self.e.poll(timeout_ms)

        @hif.exp
        def dbg_wait(self, why: str) -> ct.c_int32:
            return self.e.dbg_wait(why)

        @hif.exp
        def pending_sel(self) -> str:
            return self.e.pending_sel()

        @hif.exp
        def serve_test(self, name: str, passed: ct.c_int32):
            self.e.serve_test(name, passed)

        @hif.exp
        def serve_done(self, n_pass: ct.c_int32, n_fail: ct.c_int32):
            self.e.serve_done(n_pass, n_fail)
