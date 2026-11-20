"""Running PSS in this Python process: the host API (``pv`` in the design).

    import pssc.testing.vm as pv

    class Board(pv.Platform):
        def plat_id(self):                  # a user import, by its PSS name
            return 0x5A

    s = pv.load(["suite.pss"], root="my_fixture", platform=Board())
    s.run_test("my_fixture::t_id")          # -> (n_pass, n_fail, n_skip)

The seam is op-model-py's: a platform has the methods of the generated
``<Root>ImportApi`` Protocol -- ``read8``..``read64``, ``write8``..``write64``,
``message(text)`` and one method per user import, named as in PSS, taking
its arguments in order -- so a model written for generated op-model-py code
drives a pssvm run, and the reverse. ``pssc_rt.check_import_api(platform,
<Root>ImportApi)`` says what one is missing. Nothing has to inherit
``Platform``: any object with the methods a run reaches works.

Doubles (design §7.3) are collected by module-level decorators and applied
to a session with ``Session.use`` (or ``load(..., use=[...])``)::

    @pv.implements("plat_delay_us")         # a user import: before the platform
    def delay(ctx, us): ctx.time.advance_us(us)

    @pv.mock("timer_c::wait_us")            # any called PSS function
    def fast_wait(ctx, self_, us): return None

    @pv.spy("dma_c::check")                 # the real body runs; each call recorded
    def _(call): pass

    s.use(my_mocks)                         # a module (by object or name) or a Registry

Each decorator files the function under its module, so ``use`` of a module
takes every double that module declared, however its names are reused. A
mock or a spy lasts the session: it is applied again after each
construction (``VM.bind`` and the ``call``/``return`` events last one VM).
Mocks and spies need a debug lowering (the default of ``load``).

Every backend import is inside a function, as in ``bc_session``.
"""
from __future__ import annotations

import dataclasses as _dc
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import bc_session, hooks as _hooks, imports as _imports, \
    sanitizers as _sanitizers, sources as _sources

__all__ = ["Platform", "RegisterFake", "Session", "load", "Registry", "Call",
           "Ctx", "Time", "implements", "mock", "spy", "on_write", "on_read",
           "Access", "Hosted", "PlatformLoadError", "load_platform",
           "declared_platform"]


class Registry:
    """Doubles to apply to a session: ``implements`` (user import name ->
    ``fn(ctx, *args)``), ``mocks`` (``"<comp type>::<fn>"`` ->
    ``fn(ctx, self_, *args)``), ``spies`` (``"<comp type>::<fn>"`` -> a
    list of ``fn(call)``). Its methods are the decorators."""

    def __init__(self):
        self.implements_: Dict[str, Callable] = {}
        self.mocks: Dict[str, Callable] = {}
        self.spies: Dict[str, List[Callable]] = {}

    def implements(self, name: str):
        def deco(fn):
            self.implements_[name] = fn
            return fn
        return deco

    def mock(self, qname: str):
        def deco(fn):
            self.mocks[qname] = fn
            return fn
        return deco

    def spy(self, qname: str):
        def deco(fn):
            self.spies.setdefault(qname, []).append(fn)
            return fn
        return deco


#: module name -> the doubles its decorators declared
_MODULES: Dict[str, Registry] = {}


def _module_registry(fn) -> Registry:
    return _MODULES.setdefault(getattr(fn, "__module__", "") or "", Registry())


def implements(name: str):
    """Implement user import ``name`` (``fn(ctx, *args)``); it takes
    precedence over the platform's method, and its ``import`` events are
    ``mocked``."""
    return lambda fn: _module_registry(fn).implements(name)(fn)


def mock(qname: str):
    """Run ``fn(ctx, self_, *args)`` in place of PSS function ``qname``
    (``<comp type>::<fn>``); its result (None: 0) is the call's.
    ``self_`` is None for now: a bound call does not see its instance."""
    return lambda fn: _module_registry(fn).mock(qname)(fn)


def spy(qname: str):
    """Call ``fn(call)`` (a ``Call``) after each call of ``qname``
    returns; the real body runs."""
    return lambda fn: _module_registry(fn).spy(qname)(fn)


@_dc.dataclass
class Call:
    """One call of a spied function: its raw arguments and result
    (None: a void function); ``mocked`` if a mock ran in its place."""
    qname: str
    args: List[int]
    result: Optional[int] = None
    mocked: bool = False


class Time:
    """The run's clock (``vm.sched.now``, in VM ticks). ``ticks_per_us``
    is the session's (default 1000: a tick is a ns; a guess until PSS
    time units are lowered)."""

    def __init__(self, session):
        self._s = session

    @property
    def now(self) -> int:
        vm = self._s.vm
        return vm.sched.now if vm is not None else 0

    def advance(self, ticks: int) -> None:
        """Move the clock ``ticks`` ahead, at once (the calling thread does
        not wait; timed waits due by then become ready)."""
        self._s.vm.sched.now += max(0, int(ticks))

    def advance_ns(self, ns: int) -> None:
        self.advance(int(ns) * self._s.ticks_per_us // 1000)

    def advance_us(self, us: int) -> None:
        self.advance(int(us) * self._s.ticks_per_us)


class Ctx:
    """What a double sees: the ``session``, its ``platform``, the clock
    (``time``) and ``raise_irq``."""

    def __init__(self, session):
        self.session = session
        self.time = Time(session)

    @property
    def platform(self):
        return self.session.platform

    @property
    def vm(self):
        return self.session.vm

    def raise_irq(self, irq: int = 0) -> None:
        """Raise interrupt ``irq``: kept in ``session.irqs``, a ``notify``
        event (``{"irq": irq, "delivered": bool}``), and
        ``platform.raise_irq(irq)`` if the platform has one.

        It is delivered when the session has an interrupt handler
        (``Session.irq_entry``): the handler runs in the root component as
        a thread of its own, at the next scheduling point -- after the
        access or import that raised it returns and the running thread
        yields, as an interrupt is taken between instructions."""
        s = self.session
        s.irqs.append(irq)
        delivered = False
        if self.vm is None and s.engine == "c":
            s._c_irq(irq)
        if self.vm is not None:
            delivered = s.irq_entry is not None
            if delivered:
                vm = self.vm
                frame = vm.sched.new_frame(s.model.coros[s.irq_entry],
                                           seed=s.seed,
                                           obj=vm._new_obj(s.irq_entry))
                frame.cobj = vm.cobj
                vm.set_comp(frame, 0)
                vm.sched.ready(frame)
            self.vm.host_event("notify", {"irq": irq, "delivered": delivered})
            if s.sanitizers is not None:
                # a platform with ``raise_irq`` takes it itself
                s.sanitizers.irq(irq, delivered or hasattr(self.platform, "raise_irq"))
        m = getattr(self.platform, "raise_irq", None)
        if m is not None:
            m(irq)

    def schedule_after(self, delay: int, name: str, arg: int = 0) -> None:
        """Run the session's ``on_event`` hooks named ``name`` ``delay``
        ticks from now, each as a thread of its own in its mock instance
        (`hooks.schedule`)."""
        from . import hooks
        hooks.schedule(self.session, delay, name, arg)

_WIDTHS = (8, 16, 32, 64)


class Platform:
    """A platform to start from: sparse little-endian memory behind the
    ``read``/``write`` methods, ``message`` lines kept in ``messages``.
    Add the model's user imports as methods; override an access to give an
    address behaviour.

    ``log`` has every access: ``("read"|"write", width, addr, data)``, the
    shape of ``pssc_rt.MemoryBus.log``."""

    def __init__(self):
        self.mem: Dict[int, int] = {}
        self.log: List[tuple] = []
        self.messages: List[str] = []

    def _read(self, width: int, addr: int) -> int:
        v = 0
        for i in range(width // 8):
            v |= self.mem.get((addr + i) & ((1 << 64) - 1), 0) << (8 * i)
        self.log.append(("read", width, addr, v))
        return v

    def peek(self, addr: int, n: int) -> int:
        """``n`` bytes at ``addr`` through the back door: no log entry, no
        side effect (the debug stub's ``backdoor_read``)."""
        v = 0
        for i in range(n):
            v |= self.mem.get((addr + i) & ((1 << 64) - 1), 0) << (8 * i)
        return v

    def _write(self, width: int, addr: int, data: int) -> None:
        data &= (1 << width) - 1
        for i in range(width // 8):
            self.mem[(addr + i) & ((1 << 64) - 1)] = (data >> (8 * i)) & 0xFF
        self.log.append(("write", width, addr, data))

    def read8(self, addr: int) -> int: return self._read(8, addr)
    def read16(self, addr: int) -> int: return self._read(16, addr)
    def read32(self, addr: int) -> int: return self._read(32, addr)
    def read64(self, addr: int) -> int: return self._read(64, addr)
    def write8(self, addr: int, data: int) -> None: self._write(8, addr, data)
    def write16(self, addr: int, data: int) -> None: self._write(16, addr, data)
    def write32(self, addr: int, data: int) -> None: self._write(32, addr, data)
    def write64(self, addr: int, data: int) -> None: self._write(64, addr, data)

    def message(self, text: str) -> None:
        self.messages.append(text)


class _Memory:
    """be-bc's memory seam (``read(addr, n)``) onto a platform's
    ``read<w>(addr)``."""

    def __init__(self, platform):
        self.platform = platform

    def _method(self, rw: str, n: int):
        name = f"{rw}{8 * n}"
        m = getattr(self.platform, name, None)
        if m is None:
            from zuspec.be.bc.interp import VMError
            raise VMError(f"the platform ({type(self.platform).__name__}) has "
                          f"no {name}()")
        return m

    def read(self, addr: int, n: int) -> int:
        return int(self._method("read", n)(addr) or 0)

    def write(self, addr: int, n: int, value: int) -> None:
        self._method("write", n)(addr, value)

    @property
    def peek(self):
        """The platform's back door ``peek(addr, n)``, or None."""
        return getattr(self.platform, "peek", None)

    def note_access(self, reg_id, src_ref: int) -> None:
        note = getattr(self.platform, "note_access", None)
        if note is not None:
            note(reg_id, src_ref)


#: pssunit_pkg imports the runner keeps even when the platform has a method of
#: that name (``RegisterFake.check_reg`` is the back door, not the ledger's)
_PKG_FIRST = frozenset(("check_reg", "check_field", "check_written",
                        "check_not_accessed", "raise_irq"))


class _Imports:
    """be-bc's import seam (``call(fn_id, args)``) onto the platform method
    named as the import."""

    def __init__(self, platform, model, session=None):
        self.platform, self.session = platform, session
        self.names = {sig.get("fn_id"): n for n, sig in
                      (getattr(model, "imports", None) or {}).items()}
        self.calls: List[Tuple[str, List[int]]] = []

    def _impl(self, fn_id: int):
        impls = self.session.impls if self.session is not None else None
        if not impls:
            return None
        name = self.names.get(fn_id, "")
        return impls.get(name) or impls.get(name.rpartition("::")[2])

    def call(self, fn_id: int, args: Sequence[int]) -> Optional[int]:
        name = self.names.get(fn_id, f"import#{fn_id}")
        impl = self._impl(fn_id)
        if impl is not None:
            self.calls.append((name, list(args)))
            return impl(self.session.ctx, *args)
        base = name.rpartition("::")[2]
        m = getattr(self.platform, base, None)
        # the lowering names imports unqualified (``check_capture``)
        pkg = _imports.IMPORTS.get("%s::%s" % (_imports.PACKAGE, base)) \
            if name in (base, "%s::%s" % (_imports.PACKAGE, base)) else None
        if pkg is not None and (m is None or base in _PKG_FIRST):
            # pssunit_pkg's own imports: the runner's (``imports.IMPORTS``),
            # on this test's ``RunState``; a platform may still take the
            # capture calls (``check_operand``/``check_capture``) itself
            self.calls.append((name, list(args)))
            return pkg(self.session.run_state, *args)
        if m is None:
            from zuspec.be.bc.interp import VMError
            raise VMError(f"the platform ({type(self.platform).__name__}) does "
                          f"not implement import {name}()")
        self.calls.append((name, list(args)))
        return m(*args)

    def is_mocked(self, fn_id: int) -> bool:
        return self._impl(fn_id) is not None


class _Tee:
    """The session's events sink: ``call``/``return`` events of spied
    functions to the spies; every event the user watched (``watch``) to the
    user's sink."""

    def __init__(self, session, sink, watch: int):
        self.session, self.sink, self.watch = session, sink, watch
        self._open: Dict[int, List[Call]] = {}

    def host(self, kind: str, detail=None, src_ref: int = 0) -> None:
        """A host event: the VM's (``host_event``); on the C engine (no VM)
        straight to the user's sink, if ``watch`` selects it."""
        s = self.session
        if s.vm is not None:
            s.vm.host_event(kind, detail, src_ref=src_ref)
            return
        from zuspec.be.bc.trace import events as ev
        if self.sink is not None and ev.BITS.get(kind, 0) & self.watch:
            self.sink.event(kind, src_ref=src_ref, detail=detail)

    def event(self, kind: str, coro: int = 0, src_ref: int = 0, detail=None):
        s = self.session
        if kind in ("call", "return") and s._spied_names:
            qname = s._spied_names.get((detail or {}).get("fn"))
            if qname is not None:
                stack = self._open.setdefault(coro, [])
                if kind == "call":
                    stack.append(Call(qname, list(detail.get("args", ())),
                                      mocked=bool(detail.get("mocked"))))
                elif stack:
                    c = stack.pop()
                    c.result = detail.get("retval")
                    s.calls.setdefault(qname, []).append(c)
                    for fn in s.spies.get(qname, ()):
                        fn(c)
        if kind == "chan" and s.sanitizers is not None:
            s.sanitizers.chan(detail or {}, src_ref)
        if kind in ("access", "import") and s.vm is None:
            # on C, an interrupt nothing delivers, raised during this one
            s._c_seen += 1
            while s._c_undelivered and s._c_undelivered[0][0] <= s._c_seen:
                s._undelivered(s._c_undelivered.pop(0)[1])
        if kind in ("access", "import") and s.sanitizers is not None:
            # an interrupt the access/import's double raised, undelivered
            for f in s.sanitizers.site(src_ref):
                self.host("sanitizer", {"name": f.name, "verdict": "fail",
                                        "what": f.what}, src_ref=f.src_ref)
        if kind == "access" and s.sanitizers is not None:
            f = None
            try:
                f = s.sanitizers.access(detail or {}, src_ref)
            except _sanitizers.SanitizerFailed as e:
                f = e.finding
                raise
            finally:
                if f is not None:
                    self.host("sanitizer", {"name": f.name, "verdict": "fail",
                                            "what": f.what}, src_ref=f.src_ref)
        if self.sink is not None:
            from zuspec.be.bc.trace import events as ev
            if ev.BITS.get(kind, 0) & self.watch:
                self.sink.event(kind, coro=coro, src_ref=src_ref, detail=detail)


class _Served:
    """``memory`` or ``imports`` of a C engine run, which counts the requests
    it serves (``Session._c_served``): where an interrupt was raised."""

    def __init__(self, session, inner):
        self.session, self.inner = session, inner

    def __getattr__(self, name):
        return getattr(self.inner, name)        # note_access, peek, ...

    def read(self, addr: int, n: int) -> int:
        self.session._c_served += 1
        return self.inner.read(addr, n)

    def write(self, addr: int, n: int, value: int) -> None:
        self.session._c_served += 1
        self.inner.write(addr, n, value)

    def call(self, fn_id, args):
        self.session._c_served += 1
        return self.inner.call(fn_id, args)


class _PollGuard:
    """``memory`` for a C engine run, which ends a poll that has run well
    past the sanitizers' budget: the C run cannot be stopped from its trace,
    which the sanitizers only see when it ends. The guard counts more loosely
    than ``Sanitizers.access`` (every read, with twice the budget), so the
    trace's own ``poll-budget`` finding comes first and is the one reported."""

    def __init__(self, memory, budget: int):
        if memory is None:
            from zuspec.be.bc.interp.extern import Memory
            memory = Memory()
        self.memory, self.limit = memory, 2 * budget + 16
        self._polls: Dict[int, Tuple[int, int]] = {}

    def __getattr__(self, name):
        return getattr(self.memory, name)       # note_access, ...

    def read(self, addr: int, n: int) -> int:
        v = self.memory.read(addr, n)
        last, k = self._polls.get(addr, (None, 0))
        k = k + 1 if last == v else 1
        self._polls[addr] = (v, k)
        if k > self.limit:
            raise RuntimeError(f"0x{addr:x} read {k} times returning 0x{v:x} "
                               f"with no progress")
        return v

    def write(self, addr: int, n: int, value: int) -> None:
        self._polls.clear()
        self.memory.write(addr, n, value)


#: the interrupt handler a session looks for by default: the WB DMA model's
#: event producer (``wb_dma_c::notify_irq``), or any root function so named
IRQ_HANDLER = "notify_irq"


def _drop_bytecode(module) -> None:
    """Remove ``module``'s cached bytecode: an edit within the second that
    keeps the file's size would otherwise reload the old code."""
    import importlib.util
    import os
    src = getattr(module, "__file__", None)
    if src and src.endswith(".py"):
        try:
            os.remove(importlib.util.cache_from_source(src))
        except OSError:
            pass


def _irq_entry(model, handler) -> Optional[int]:
    """Coroutine of interrupt handler ``handler`` (``Session``), or None
    when the image has no function entry for it."""
    if handler is None or isinstance(handler, int):
        return handler
    table = getattr(model, "entries", None) or {}
    e = table.get(handler)
    if e is None and "::" not in handler:
        e = next((e for sym, e in table.items()
                  if sym.rpartition("::")[2] == handler), None)
    return e.coro if e is not None and e.kind == "function" else None


class Session(bc_session.Session):
    """A ``bc_session.Session`` whose memory accesses, user imports and
    ``message`` lines go to ``platform``. The platform outlives
    constructions: memory a test writes is there for the next unless the
    platform resets it. ``engine``: ``bc_session.Session``'s (``c`` runs
    function tests on a plain platform; ``_run_test_c``)."""

    def __init__(self, model, platform=None, seed: int = 0, events=None,
                 watch: int = 0, use: Sequence[Any] = (), sanitize: bool = True,
                 regions: Optional[Sequence[Tuple[int, int]]] = None,
                 poll_budget: int = _sanitizers.DEFAULT_POLL_BUDGET,
                 irq_handler: Any = IRQ_HANDLER,
                 engine: Optional[str] = None):
        self.platform = platform if platform is not None else Platform()
        #: coroutine of the function ``ctx.raise_irq`` runs, or None (not
        #: delivered). ``irq_handler``: an entry symbol, a function name
        #: looked up among the image's function entries (the run names it,
        #: never the PSS source), a coroutine index, or None
        self.irq_entry = _irq_entry(model, irq_handler)
        self._irq_handler = irq_handler
        #: what ``use`` was given, in order (``reimport`` applies it again)
        self.used: List[Any] = []
        #: the sanitizers (``sanitizers.Sanitizers``; None when ``sanitize``
        #: is off or the image has no register table, i.e. not debug).
        #: ``regions`` defaults to the platform's ``regions`` attribute
        regs = getattr(model, "registers", None)
        self.sanitizers = _sanitizers.Sanitizers(
            regs, regions=regions if regions is not None else
            getattr(self.platform, "regions", ()) or (),
            poll_budget=poll_budget,
            where=lambda r: _where(model, r),
            latches=_sanitizers.latches(model)) \
            if sanitize and regs is not None else None
        out = getattr(self.platform, "message", None)
        #: the doubles in effect (``use``)
        self.impls: Dict[str, Callable] = {}
        self.mocks: Dict[str, Callable] = {}
        self.spies: Dict[str, List[Callable]] = {}
        #: spied function -> its ``Call`` records, oldest first
        self.calls: Dict[str, List[Call]] = {}
        #: interrupts raised (``ctx.raise_irq``), in order
        self.irqs: List[int] = []
        self.ticks_per_us = 1000
        self.ctx = Ctx(self)
        # looked up on the type: a platform's ``__getattr__`` may forward
        attach = getattr(type(self.platform), "attach_session", None)
        if attach is not None:
            attach(self.platform, self)
        self._spied_names: Dict[str, str] = {}
        #: a C engine run: the running engine (``native.run_test``'s
        #: ``live``), the requests served, and the undelivered interrupts
        #: (request number, irq) its trace replays (``_Tee.event``)
        self._c_engine = None
        self._c_served = self._c_seen = 0
        self._c_undelivered: List[Tuple[int, int]] = []
        super().__init__(model, seed=seed, out=out,
                         events=_Tee(self, events, watch), watch=watch,
                         memory=_Memory(self.platform),
                         imports=_Imports(self.platform, model, self),
                         engine=engine)
        #: the pssunit_pkg ledger and state of the run (new per construction)
        self.run_state = self._run_state()
        #: why the last ``run_test`` stopped early (a failed require), or None
        self.stopped: Optional[str] = None
        if isinstance(self.platform, RegisterFake):
            self.platform.ctx = self.ctx    # what its hooks see
            if hasattr(events, "register_access"):
                self.platform.cov = events  # register coverage (P2.7d)
        attach = getattr(self.platform, "attach", None)
        if attach is not None:
            attach(model)
            _hooks.bind(self)           # the mocks' PSS hooks
        if hasattr(self.platform, "note_access") or self.sanitizers is not None:
            # the VM names each access's register to the platform before it
            # happens (a register fake); the user's sink still sees ``watch``
            from zuspec.be.bc.trace import events as ev
            self.watch = watch | ev.EV_ACCESS
            if self.sanitizers is not None:
                self.watch |= ev.EV_IMPORT      # where unconsumed-irq raised
            if self.sanitizers is not None and self.sanitizers.latches:
                self.watch |= ev.EV_CHAN
        for u in use:
            self.use(u)

    def use(self, doubles) -> "Session":
        """Apply the doubles of ``doubles``: a ``Registry``, a module, or a
        module name (imported). Later ones win; a tree already constructed
        gets them now."""
        if isinstance(doubles, str):
            import importlib
            doubles = importlib.import_module(doubles)
        reg = doubles if isinstance(doubles, Registry) else _MODULES.get(
            getattr(doubles, "__name__", None))
        if reg is None:
            raise ValueError(f"{doubles!r} declares no doubles "
                             f"(@pv.implements/mock/spy)")
        self.used.append(doubles)
        self.impls.update(reg.implements_)
        self.mocks.update(reg.mocks)
        for q, fns in reg.spies.items():
            self.spies.setdefault(q, []).extend(fns)
        coros = self.model.coros
        for q in self.spies:
            for c in coros:
                if getattr(c, "fn_qname", "") == q:
                    self._spied_names[c.name] = q
        if self.vm is not None:
            self._apply()
        return self

    def _apply(self) -> None:
        from zuspec.be.bc.trace import events as ev
        for q, fn in self.mocks.items():
            self.vm.bind(q, (lambda f: lambda *a: f(self.ctx, None, *a))(fn))
        if self._spied_names:
            for i, c in enumerate(self.model.coros):
                if c.name in self._spied_names:
                    self.vm.watch(self.vm._ev.get(id(c), 0) | ev.EV_CALL,
                                  coros=[i])

    def reimport(self) -> List[str]:
        """Hot reload of the doubles: import each module ``use`` was given
        again (``importlib.reload``: its decorators declare its doubles
        anew) and make what it now declares the doubles in effect, in place
        of the old ones. Returns the modules' names."""
        import importlib
        import types
        used, self.used = self.used, []
        names = []
        for u in used:
            if isinstance(u, types.ModuleType):
                _MODULES.pop(u.__name__, None)
                _drop_bytecode(u)
                u = importlib.reload(u)
                names.append(u.__name__)
        self.impls.clear()
        self.mocks.clear()
        self.spies.clear()
        self._spied_names.clear()
        for u in used:
            self.use(u)
        return names

    def _apply_reload(self) -> None:
        """``bc_session``'s, then what this session took from the image
        follows the new one: the import names, the interrupt handler, the
        checks, the spied coroutines and the mocks' bindings."""
        super()._apply_reload()
        m = self.model
        self.imports.names = {sig.get("fn_id"): n for n, sig in
                              (getattr(m, "imports", None) or {}).items()}
        self.irq_entry = _irq_entry(m, self._irq_handler)
        self.run_state.checks = getattr(m, "checks", None) or {}
        self._spied_names.clear()
        for q in self.spies:
            for c in m.coros:
                if getattr(c, "fn_qname", "") == q:
                    self._spied_names[c.name] = q
        if self.vm is not None:
            self._apply()

    def failure_cards(self, test: str = "", last: int = 3,
                      repro: Optional[str] = None) -> List[str]:
        """A failure card (``report.failure_card``) for each check of the last
        run that failed: its operands' values, the registers it names and the
        last ``last`` bus accesses when the platform is a ``RegisterFake``.
        ``repro`` is passed on (``""``: no repro line)."""
        from .report import failure_card
        st, fake = self.run_state, self.platform
        is_fake = isinstance(fake, RegisterFake)
        cards = []
        for cid, ok, ops in st.captured:
            site = st.checks.get(cid)
            if ok or site is None:
                continue
            regs = []
            if is_fake and site.strings:
                try:
                    regs = [fake.decoded(site.strings[0])]
                except KeyError:
                    pass
            if cid in st.reasons and not site.what:
                site = _dc.replace(site, what=st.reasons[cid])
            cards.append(failure_card(site, ops, test=test, seed=self.seed,
                                      bus=fake.bus if is_fake else (),
                                      last=last, registers=regs, repro=repro))
        return cards

    def _run_state(self) -> "_imports.RunState":
        p = self.platform
        return _imports.RunState(
            checks=getattr(self.model, "checks", None) or {},
            double=p if isinstance(p, RegisterFake) else None,
            irq=self.ctx.raise_irq, schedule=self.ctx.schedule_after)

    def _new_vm(self) -> None:
        self.run_state = self._run_state()
        super()._new_vm()
        self._apply()

    def run_test(self, symbol: str, seed: Optional[int] = None,
                 bindings=None) -> Tuple[int, ...]:
        """``bc_session.Session.run_test``, counting the pssunit_pkg checks
        too (``run_state.ledger``, whose ``failures`` give each one's text).
        A ``require`` or ``fail`` that fails ends the test, which then still
        returns its counts."""
        return self._counted(symbol, lambda: super(Session, self).run_test(
            symbol, seed=seed, bindings=bindings))

    def run_entry(self, coro: int, name: str = "", seed: Optional[int] = None,
                  **kw) -> Tuple[int, ...]:
        """``bc_session.Session.run_entry`` (an action or a root function,
        with set-up and tear-down: what ``pssc test`` runs), counted like
        ``run_test``; ``name`` is the symbol ``@pssunit_pkg::allow`` is
        looked up by."""
        return self._counted(name, lambda: super(Session, self).run_entry(
            coro, name, seed=seed, **kw))

    def _run_test_c(self, symbol: str, bindings, setup=(),
                    teardown=()) -> Tuple[int, ...]:
        """``bc_session``'s, with a new pssunit_pkg ledger; the mocks are
        bound on the engine (``Engine.bind``: the C engine asks the host for
        a mocked function's result, so the mock runs as the call is made).
        A register fake is told each access by rt-eng's ``run_sync``
        (``note_access``). The sanitizers and spies see the run's trace when
        it ends (``_Tee.event``, from ``native._emit``): the same events, in
        the same order, as on the Python VM, so the same findings and calls
        (a spy is called after the run, not as the call returns). A poll that
        would run past the budget is stopped in the host (``_PollGuard``),
        and the trace's ``poll-budget`` finding is what ends the test."""
        from zuspec.be.bc.trace import events as ev
        if self.mocks:
            mocked = {q: (lambda f: lambda *a: f(self.ctx, None, *a))(fn)
                      for q, fn in self.mocks.items()}
            bindings = {**mocked, **(bindings or {})}
        self.run_state = self._run_state()
        san, memory, watch = self.sanitizers, self.memory, self.watch
        if san is not None and "poll-budget" not in san.off:
            self.memory = _PollGuard(memory, san.poll_budget)
        if self._spied_names:
            self.watch |= ev.EV_CALL
        imports = self.imports
        self.memory = _Served(self, self.memory)
        self.imports = _Served(self, imports)
        self._c_served = self._c_seen = 0
        self._c_undelivered = []
        try:
            return super()._run_test_c(symbol, bindings, setup, teardown)
        finally:
            self.memory, self.imports, self.watch = memory, imports, watch
            for _, irq in self._c_undelivered:   # raised after the last access
                self._undelivered(irq)
            self._c_undelivered = []

    def _native_kw(self) -> dict:
        irq = None
        if self.irq_entry is not None:
            irq = next(sym for sym, e in self.model.entries.items()
                       if e.coro == self.irq_entry and e.kind == "function")
        return {"irq": irq, "live": lambda eng: setattr(self, "_c_engine", eng)}

    def _c_irq(self, irq: int) -> None:
        """``Ctx.raise_irq`` on the C engine: the running engine starts the
        handler (its trace has the ``notify`` event, as the VM's); with no
        handler, the ``notify`` event and sanitizer finding are made where the
        trace reaches the access or import in progress."""
        if self.irq_entry is not None and self._c_engine is not None:
            from zuspec.rt.eng.host import N_IRQ
            self._c_engine.notify(N_IRQ, irq)
        else:
            self._c_undelivered.append((self._c_served, irq))

    def _undelivered(self, irq: int) -> None:
        self.events.host("notify", {"irq": irq, "delivered": False})
        if self.sanitizers is not None:
            self.sanitizers.irq(irq, hasattr(self.platform, "raise_irq"))

    def _counted(self, symbol: str, run) -> Tuple[int, ...]:
        san = self.sanitizers
        if san is not None:
            san.start((getattr(self.model, "allows", None) or {}).get(symbol, ()))
        try:
            counts = run()
        except Exception as e:
            stop = _require_failed(e) or _caused_by(e, _sanitizers.SanitizerFailed)
            if stop is None:
                raise
            counts = bc_session.ledger(self.model, self.comp_obj)
            self.stopped = str(stop)
        else:
            self.stopped = None
            _imports.unchecked(self.run_state)
            for f in san.end() if san is not None else ():
                self.events.host("sanitizer", {"name": f.name, "verdict": "fail",
                                               "what": f.what}, src_ref=f.src_ref)
        led = self.run_state.ledger
        n_san = len(san.findings) if san is not None else 0
        return (counts[0] + led.n_pass, counts[1] + led.n_fail + n_san) \
            + tuple(counts[2:])

    @property
    def findings(self) -> List["_sanitizers.Finding"]:
        """The sanitizer findings of the last ``run_test`` (each a FAIL)."""
        return list(self.sanitizers.findings) if self.sanitizers is not None else []

    @classmethod
    def load(cls, model, seed: int = 0, platform=None,
             use: Sequence[Any] = ()) -> "Session":
        return cls(model, platform=platform, seed=seed, use=use)


def _require_failed(e: BaseException):
    """The ``RequireFailed`` that ``e`` is or wraps (the VM may wrap a host
    exception), else None."""
    return _caused_by(e, _imports.RequireFailed)


def _where(model, src_ref: int) -> Optional[str]:
    """``file:line:col`` of provenance entry ``src_ref`` of ``model``
    (``file:line`` when the column is unknown), or None."""
    entries = getattr(getattr(model, "prov", None), "entries", None) or ()
    if not 0 < src_ref < len(entries) or not entries[src_ref].file:
        return None
    p = entries[src_ref]
    col = getattr(p, "col", 0)
    return f"{p.file}:{p.line}:{col}" if col else f"{p.file}:{p.line}"


def _caused_by(e: BaseException, kind):
    """The ``kind`` exception that ``e`` is or wraps, else None."""
    seen = set()
    while e is not None and id(e) not in seen:
        if isinstance(e, kind):
            return e
        seen.add(id(e))
        e = e.__cause__ or e.__context__
    return None


class PlatformLoadError(Exception):
    """A ``--platform`` spec that names nothing loadable (``pssc test``:
    exit 3). ``__cause__`` is the import's own error, when there was one."""


@_dc.dataclass
class Hosted:
    """A Python platform bound by the run (``--platform py:<mod>[:<Class>]``
    or ``[tool.pssunit] platform``), never by the PSS source.

    Each test gets its own: ``make`` builds a new ``Class()`` (or returns
    ``default`` when the spec names none), and ``doubles`` are what to pass
    to ``Session.use`` -- the module, when its decorators declared any.

    ``fake`` (``--platform fake``): no module; each test runs on a new
    ``RegisterFake`` (storage per register, field kinds, bus log) instead
    of plain memory.

    ``replay`` (``--platform replay:<trace>``): the recorded trace file, or
    the directory of one per test; each test runs on a ``ReplayPlatform``
    of its own trace (``make``'s ``test``)."""

    spec: str
    module: Any
    cls: Optional[type] = None
    fake: bool = False
    replay: Optional[str] = None

    def make(self, default=None, test: Optional[str] = None):
        if self.replay is not None:
            from .replay import ReplayPlatform, trace_for
            return ReplayPlatform(trace_for(self.replay, test), test,
                                  getattr(default, "_unbound", ()))
        return self.cls() if self.cls is not None else default

    def recorded_seed(self, test: Optional[str] = None) -> Optional[int]:
        """The seed a replayed run was recorded with (None if not a replay):
        a replay defaults ``--seed`` to it, so random choices match."""
        if self.replay is None:
            return None
        from .replay import recorded_seed
        return recorded_seed(self.replay, test)

    @property
    def doubles(self) -> List[Any]:
        if self.module is None:
            return []
        return [self.module] if self.module.__name__ in _MODULES else []


def declared_platform(cli: Optional[str] = None,
                      pyproject: str = "pyproject.toml") -> Optional[str]:
    """``--platform`` if given, else ``[tool.pssunit].platform``, else None."""
    if cli is not None:
        return cli
    import os
    if os.path.isfile(pyproject):
        import tomllib
        with open(pyproject, "rb") as f:
            cfg = tomllib.load(f).get("tool", {}).get("pssunit", {})
        return cfg.get("platform")
    return None


def load_platform(spec: str) -> Hosted:
    """Import the module of ``py:<module>[:<Class>]``; the current directory
    is searched first, as for a test's own helpers. ``fake`` is the
    register fake (``Hosted.fake``); ``replay:<trace>`` replays a recorded
    trace (``Hosted.replay``). Raises ``PlatformLoadError``."""
    import importlib
    import os
    import sys
    if spec == "fake":
        return Hosted(spec=spec, module=None, fake=True)
    kind, _, rest = spec.partition(":")
    if kind == "replay":
        if not os.path.exists(rest):
            raise PlatformLoadError(
                f"platform {spec!r}: no trace file or directory {rest!r}")
        return Hosted(spec=spec, module=None, replay=rest)
    mod_name, _, cls_name = rest.partition(":")
    if kind != "py" or not mod_name:
        raise PlatformLoadError(
            f"platform {spec!r}: expected fake or py:<module>[:<Class>], "
            f"or replay:<trace>")
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    try:
        module = importlib.import_module(mod_name)
    except BaseException as e:
        raise PlatformLoadError(
            f"platform {spec!r}: module {mod_name!r} failed to import: "
            f"{type(e).__name__}: {e}") from e
    cls = None
    if cls_name:
        cls = getattr(module, cls_name, None)
        if not isinstance(cls, type):
            raise PlatformLoadError(
                f"platform {spec!r}: module {mod_name!r} has no class "
                f"{cls_name!r}")
    hosted = Hosted(spec=spec, module=module, cls=cls)
    if cls is None and not hosted.doubles:
        raise PlatformLoadError(
            f"platform {spec!r}: module {mod_name!r} declares no doubles "
            f"(@pv.implements/mock/spy) and names no class (py:<module>:<Class>)")
    return hosted


def load(files: Sequence[str], root: str, platform=None, seed: int = 0,
         functions: Sequence[str] = ("*",), debug: bool = True,
         events=None, watch: int = 0, use: Sequence[Any] = (),
         reg_semantics: Optional[str] = None, **sanitize) -> Session:
    """Translate ``files`` (with ``pssunit_pkg`` and the preludes), lower
    component ``root`` with an entry for each of ``functions`` (default:
    every function), and return a session running on ``platform`` with
    the doubles of ``use`` (``Session.use``). ``sanitize``: ``Session``'s
    ``sanitize``, ``regions``, ``poll_budget``. ``reg_semantics``: a
    register description file the fields take their kinds from
    (``--reg-semantics``). Translation errors raise ``ValueError`` with the messages."""
    from .. import driver

    ctx = driver.translate([str(x) for x in
                            _sources.all_sources([str(f) for f in files])])
    if ctx.errors:
        raise ValueError("\n".join(str(e) for e in ctx.errors))
    return from_ctx(ctx, root, platform=platform, seed=seed,
                    functions=functions, debug=debug, events=events,
                    watch=watch, use=use, reg_semantics=reg_semantics,
                    **sanitize)


def from_ctx(ctx, root: str, platform=None, seed: int = 0,
             functions: Sequence[str] = ("*",), debug: bool = True,
             events=None, watch: int = 0, use: Sequence[Any] = (),
             reg_semantics: Optional[str] = None, **sanitize) -> Session:
    """``load`` on an already translated ``ctx`` (a ``pssc test``
    collection's, so a Python test runs on what the PSS tests ran on)."""
    from . import reg_semantics as _sem
    model = bc_session.lower(ctx, root, functions=list(functions), debug=debug,
                             reg_semantics=(_sem.load(reg_semantics)
                                            if reg_semantics else None))
    model.allows = _sanitizers.allows(ctx)
    return Session(model, platform=platform, seed=seed, events=events,
                   watch=watch, use=use, **sanitize)


from .fake import Access, RegisterFake, on_read, on_write  # noqa: E402,F401
# (fake.py builds on Platform)
