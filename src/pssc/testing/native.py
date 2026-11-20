"""Tests on the native (C) engine, zuspec-rt-eng's ``pssvm`` (P4.3d2).

``Session(engine="c")`` (or ``PSSC_ENGINE=c``) runs a test function on the C
engine instead of be-bc's Python VM, served by the same Python memory and
import mocks (``zuspec.rt.eng.host.run_sync``). The engine library is built
once per engine source and cached; libdv_solve is linked only for an image
that solves.

What the C engine cannot do yet is refused, not run on the Python VM, so a
test that passes on ``c`` ran there: host-callable bindings, messages
(``out``), activities with stream pools, resource pools over 64 or a node
in no known instance (:func:`_plain_activity`), and
the operations rt-eng lists as gaps (``ENGINE_C_GAPS``). A trace (``--trace``) is
written from the events the C engine raises (``ENGINE_C_EVENTS``).
"""

import hashlib
import os
from typing import Optional, Tuple

ENGINES = ("py", "c", "gen-py", "gen-sv")

def _c_events() -> int:
    from zuspec.be.bc.trace import events as ev
    return (ev.EV_STMT | ev.EV_CALL | ev.EV_ACCESS | ev.EV_IMPORT
            | ev.EV_MESSAGE | ev.EV_CHAN | ev.EV_BRANCH | ev.EV_NOTIFY)


#: the debug events the C engine raises (rt-eng zbc_events.c); a session's
#: others (check, host events) are not in a C run's trace
ENGINE_C_EVENTS = _c_events()

#: ``pssvm_load``'s options for a test run: no per-frame step budget, as on
#: the Python VM, which counts none -- the engine's default (10M steps, ~20 ms)
#: stopped a test there that passes here. Runaway polls are the sanitizers'.
ENGINE_OPTS = "step_limit=0"

#: ops the C engine refuses (rt-eng tests/test_ops_match_model.py
#: ``NATIVE_OP_GAPS``; P4.10)
ENGINE_C_GAPS: Tuple[str, ...] = ()


class NativeUnsupported(RuntimeError):
    """The model or run needs something the C engine does not do yet."""


def engine_name(engine: Optional[str]) -> str:
    """``engine``, else ``PSSC_ENGINE``, else ``py``; checked."""
    name = engine or os.environ.get("PSSC_ENGINE") or "py"
    if name not in ENGINES:
        raise ValueError(f"unknown engine {name!r}: one of {', '.join(ENGINES)}")
    return name


def _ops(model):
    for coro in model.coros:
        for ins in coro.code:
            yield ins.op


def needs_solver(model) -> bool:
    """Whether ``model`` has a SOLVE or a cone solve (SOLVE_NODE), so the
    engine must link libdv_solve."""
    from zuspec.be.bc.model import Op
    return any(op in (Op.SOLVE, Op.SOLVE_NODE) for op in _ops(model))


def check_model(model) -> None:
    """Raise :class:`NativeUnsupported` naming the first op C refuses."""
    from zuspec.be.bc.model import Op
    gaps = {Op[n] for n in ENGINE_C_GAPS}
    for coro in model.coros:
        for ins in coro.code:
            if ins.op in gaps:
                raise NativeUnsupported(
                    f"the C engine does not run {ins.op.name} yet (in "
                    f"{coro.name}): run this test on engine 'py'")
    # rt-eng refuses the whole image (zbc_engine.c act_solve_supported) if any
    # activation's cones need what its SOLVE_NODE does not do yet.
    for coro, table in (getattr(model, "activations", None) or {}).items():
        if table.cones and not _cone_solvable(table):
            raise NativeUnsupported(
                f"the C engine does not run SOLVE_NODE with stream pools or resource "
                f"pools over 64 yet (in "
                f"{model.coros[coro].name}): run this test on engine 'py'")


def _cone_solvable(table) -> bool:
    """Whether rt-eng's SOLVE_NODE takes activation ``table``'s cones: no
    pools but state, buffer and resource ones (state pools since P4.10d3,
    buffer picks since P4.10d4, resource claims since P4.10e1, of pools of at
    most 64 instances; parallel footprints, ``table.par``, since P4.10e2;
    loop indices since P4.10f3) (rt-eng ``act_solve_supported``)."""
    return not any(p.kind not in ("state", "buffer", "resource")
                   or (p.kind == "resource" and (p.size is None or p.size > 64))
                   for p in table.pools)


def _cache_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    return os.path.join(base, "pssc", "rt-eng")


def engine_lib(solver: bool = False) -> str:
    """The C engine's shared library, built on first use and cached by a hash
    of its sources (and, with ``solver``, the libdv_solve it links)."""
    from zuspec.rt import core as rtcore
    from zuspec.rt import eng
    from zuspec.rt.eng import build

    srcs = []
    for share in (rtcore.SHARE_DIR, eng.SHARE_DIR):
        for root, _, files in os.walk(share):
            srcs += [os.path.join(root, f) for f in files
                     if f.endswith((".c", ".h"))]
    h = hashlib.sha1()
    for p in sorted(srcs):
        with open(p, "rb") as f:
            h.update(p.encode() + b"\0" + f.read())
    link = None
    if solver:
        import dv_solve
        libdir = dv_solve.get_libdirs()[0]
        link = ["-L", libdir, "-ldv_solve", "-Wl,-rpath," + libdir]
        h.update(libdir.encode())
    out = os.path.join(_cache_dir(), h.hexdigest()[:16],
                       "libpssvm-solve.so" if solver else "libpssvm.so")
    if not os.path.exists(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        tmp = f"{out}.{os.getpid()}.tmp"
        build.build_shared_lib(tmp, extra_link_args=link)
        os.replace(tmp, out)      # another process may build it at once
    return out


def run_test(model, symbol: str, seed: int, memory=None,
             imports=None, events=None, watch: int = 0,
             keep=None, setup=(), teardown=(),
             bindings=None, irq: Optional[str] = None,
             live=None) -> Tuple[int, ...]:
    """Run test function or action ``symbol`` of the root on a fresh C
    engine (an action on a zeroed object of its fields), between
    root functions ``setup`` and ``teardown`` (symbols; tear-down runs also
    when the test fails) on one component tree; returns
    the ledger's ``(n_pass, n_fail, n_skip)``. A failing mock or engine
    error is raised (``zuspec.rt.eng.host.NativeError`` for the engine).
    With ``watch``, the engine writes the events it selects as a binary
    trace, passed on to the be-bc sink ``events`` when the run ends (also
    when it fails): the kinds the C engine raises (``ENGINE_C_EVENTS``).
    ``keep(obj)`` is given the component object as the run left it (also
    when it fails). ``bindings`` (``{"<comp type>::<fn>": callable}``) run
    host callables in place of those functions, on the raw argument values,
    as be-bc's ``VM.bind`` does (``Engine.bind``); a function never called
    through a CALL is a KeyError. ``irq``: the function entry an interrupt
    (``Engine.notify(N_IRQ, ...)``) starts (``Engine.set_irq``). ``live(eng)``
    is given the running ``Engine`` first and None when the run ends, so a
    host callback can notify it."""
    from zuspec.be.bc.interp import Obj
    from zuspec.be.bc.interp.extern import Memory, RecordingImportProvider
    from zuspec.rt.eng.host import Engine, run_sync

    from .bc_session import ledger

    check_model(model)
    nobj = _action_obj(model, symbol)
    lib = engine_lib(needs_solver(model))
    data = bytearray()
    imports = imports or RecordingImportProvider()
    with Engine(lib, model.to_bytes(), opts=ENGINE_OPTS) as eng:
        if watch and events is not None:
            eng.trace(data.extend, watch & ENGINE_C_EVENTS)
        names = model.components.names
        memory = memory if memory is not None else Memory()
        bound = _bind(eng, model, bindings)
        if irq is not None:
            eng.set_irq(irq)
        if live is not None:
            live(eng)

        def call(sym, obj_slots=0):
            run_sync(eng.call(sym, (), seed=seed, obj_slots=obj_slots),
                     memory, imports, bound=bound)
        try:
            for sym in setup:
                call(sym)
            try:
                call(symbol, nobj)
            except BaseException:
                for sym in teardown:
                    try:
                        call(sym)
                    except Exception:
                        pass
                raise
            for sym in teardown:
                call(sym)
        finally:
            if live is not None:
                live(None)
            slots = eng.comp_slots()
            obj = Obj(field_names=names, values=slots[:len(names)])
            if keep is not None:
                keep(obj)
            if data:
                _emit(bytes(data), events, model, imports)
    return ledger(model, obj)


def _action_obj(model, symbol: str) -> int:
    """The number of slots of action entry ``symbol``'s object (0: a
    function, or an action with none); an activity the C engine cannot run
    yet (see :func:`_plain_activity`) is refused."""
    e = model.entries.get(symbol)
    if e is None or e.kind != "action":
        return 0
    table = (getattr(model, "activations", None) or {}).get(e.coro)
    if table is None:
        return len((getattr(model, "obj_layouts", None) or {}).get(e.coro) or ())
    if not _plain_activity(table):
        raise NativeUnsupported(
            f"the C engine does not run activities with stream pools, resource "
            f"pools over 64 or nodes in no known instance yet ({symbol}): "
            f"run this test on engine 'py'")
    # One object for the whole activation, as be-bc's VM.root_frame makes it.
    return len(table.names)


def _plain_activity(table) -> bool:
    """Whether the C engine runs activation ``table`` (P4.10c1, P4.10d1-4): no
    pools but state ones (their initial objects, inputs and outputs, P4.10d3)
    and buffer ones (outputs and the picks of inputs no bind connects,
    P4.10d4), resource claims without parallel footprints (P4.10e1), every node in one known
    instance (``comp_rel``, P4.10c4) or in the one its cone's solve chooses
    (``comp_slot``, P4.10d2), and solve
    cones rt-eng ``act_solve_supported`` takes. The Python VM's activation then does nothing the
    C engine does not."""
    if not _cone_solvable(table):
        return False
    return all(n.comp_rel is not None or n.comp_slot is not None
               for n in table.nodes)


def _bind(eng, model, bindings) -> dict:
    """Bind ``bindings``' functions on ``eng``; ``{coro: callable}``."""
    if not bindings:
        return {}
    from zuspec.be.bc.model import Op
    coros = model.coros
    called = {ins.args[0] for c in coros for ins in c.code if ins.op == Op.CALL}
    bound = {}
    for qname, fn in bindings.items():
        idx = [i for i in called if getattr(coros[i], "fn_qname", "") == qname]
        if not idx:
            raise KeyError(f"{qname} is never called through a CALL "
                           f"(bindings need a debug lowering)")
        for i in idx:
            eng.bind(i)
            bound[i] = fn
    return bound


class Debugged:
    """A debug stub on a run of root functions on a fresh C engine
    (:func:`debug_stub`); ``checks`` is the ledger once the run has ended."""

    def __init__(self, stub, engine):
        self.stub, self.engine = stub, engine
        self.checks: Optional[Tuple[int, ...]] = None


def debug_stub(model, symbols, seed: int, memory=None, imports=None,
               on_end=None) -> Debugged:
    """A ``zuspec.rt.eng.debug.NativeStub`` (pyvm ``Stub``'s interface,
    P4.5c1) on a fresh C engine of ``model``, not attached. Its run calls the
    root functions ``symbols`` in order on one component tree (set-up, the
    test, tear-down), answered from ``memory`` and ``imports``; it starts on
    ``attach``. ``on_end(checks, error)`` is called when the run ends: the
    ledger (None on an error) and the exception (None on success). The engine
    is freed when the run ends."""
    from zuspec.be.bc.interp import Obj
    from zuspec.be.bc.interp.extern import Memory, RecordingImportProvider
    from zuspec.rt.eng.debug import NativeStub
    from zuspec.rt.eng.host import Engine, run_sync

    from .bc_session import ledger

    check_model(model)
    memory = memory if memory is not None else Memory()
    imports = imports or RecordingImportProvider()
    eng = Engine(engine_lib(needs_solver(model)), model.to_bytes(),
                 opts=ENGINE_OPTS)
    names = model.components.names

    def run(on_stop):
        checks = error = None
        try:
            for sym in symbols:
                run_sync(eng.call(sym, (), seed=seed), memory, imports,
                         on_stop=on_stop)
            slots = eng.comp_slots()
            checks = ledger(model, Obj(field_names=names,
                                       values=slots[:len(names)]))
            out.checks = checks
        except BaseException as e:
            error = e
            raise
        finally:
            if on_end is not None:
                on_end(checks, error)

    out = Debugged(NativeStub(eng, model, run=run, memory=memory,
                              import_provider=imports), eng)
    return out


def _emit(data: bytes, events, model=None, imports=None) -> None:
    """Pass the records of the binary trace ``data`` to the be-bc sink
    ``events`` (its ``event``), numbered by the sink as the Python VM's
    are. An import event gets the host's part, as the Python VM gives it:
    its ``name`` (``model.imports``; the image has none) and whether
    ``imports`` mocked it (``is_mocked``)."""
    from zuspec.be.bc.trace import binary

    names = {sig.get("fn_id"): n for n, sig in
             (getattr(model, "imports", None) or {}).items()}
    is_mocked = getattr(imports, "is_mocked", None)
    _, _, recs = binary.read(data)
    for d in recs:
        det = d.get("detail") or {}
        if d["kind"] == "import" and det.get("name") is None:
            det["name"] = names.get(det.get("fn_id"))
            if is_mocked is not None:
                det["mocked"] = bool(is_mocked(det.get("fn_id")))
        # ``event`` (not ``emit``): a vm.Session's sink is its ``_Tee``
        events.event(d["kind"], coro=d.get("coro", 0),
                     src_ref=d.get("src_ref", 0), detail=det)
