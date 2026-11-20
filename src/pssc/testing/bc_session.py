"""Entering the ZBC (``zuspec-be-bc``) execution path: lower, run, read back.

The one place ``pssc test`` touches the bc backend, so that the runner speaks in
images, entries and ledgers rather than in the backend's API. Lowering goes the
way the compliance adapter (``tests/compliance/adapters/pssc_bc.py``) enters
bc: ``PSSToScenarioPass`` then ``lower_module(..., solve_unconstrained=True)``,
so an unconstrained rand field is drawn from its domain by dv-solve rather than
left at the run's default backend.

Every backend import is inside a function: the backend is a heavy import, and
the fast unit tier must be able to load the runner without it.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple


def lower(ctx, comp: str, functions: Sequence[str] = (), debug: bool = False,
          generation: int = 0, reg_semantics: Optional[Dict] = None):
    """Lower root component ``comp`` of ``ctx`` to a ZBC model. Raises on a
    construct the backend cannot express (see :func:`lowering_failures`).
    ``functions`` (``<comp type>::<fn>``, or ``"*"`` for every function of
    every component type in the tree) also get entries (see :class:`Session`).
    ``debug`` marks where each PSS statement starts (the image's LINE table)
    and makes a debug-profile image, which carries the PSS sources and the
    image's identity: the API hash (every entry and import signature) and
    ``generation``, the caller's build number (0 = unset). ``pssunit_pkg``
    checks are first rewritten to capture their operands (``check_rewrite``);
    the model's ``checks`` is their table. ``reg_semantics``
    (``reg_semantics.load``, ``--reg-semantics``) gives register fields
    their kinds; a ``field_access`` annotation wins over it."""
    from zuspec.be.bc.lower import lower_module
    from zuspec.ir.core.xf.pss_lower import PSSToScenarioPass

    from . import check_rewrite
    from . import hooks as _hooks

    checks = check_rewrite.rewrite(ctx)
    # every mock hook gets an entry, for the platform to run it
    functions = list(functions) + [h for h in _hooks.entry_symbols(ctx, comp)
                                   if h not in functions]
    module = PSSToScenarioPass(root=comp).lower(ctx)
    model = lower_module(module, solve_unconstrained=True,
                         entry_functions=list(functions), reg_offset=_reg_offset,
                         debug=debug, profile="debug" if debug else "codegen")
    model.generation = generation
    #: check id -> site (``check_rewrite.CheckSite``): the operand captures'
    #: source text, read by the runner's ``check_capture``
    model.checks = checks
    if debug:
        model.sources = _sources(model)
        model.registers = _registers(ctx, comp, model, reg_semantics)
        #: the ``mock_c`` reaction hooks, registers resolved (``hooks``)
        model.hooks = _hooks.declared(ctx, model)
    return model


def _registers(ctx, comp: str, model,
               semantics: Optional[Dict] = None) -> Dict[int, Dict]:
    """reg-id -> register, for decoding ``access`` events (P1.7). A reg-id
    is the register's component instance (``model.components.paths``). Its
    offset, width and fields are its ``reg_layout.RegAccessor``'s in the
    nearest enclosing component that is not a register group (``comp``: its
    path; the offset is from that component's register base) -- the walk the
    manifest's ``registers`` come from."""
    import re
    from zuspec.ir.core.data_type import DataTypeRegister, DataTypeRegisterGroup
    from pssc.reg_field_resolve import struct_layout
    from pssc.targets.reg_layout import collect_accessors
    from .reg_semantics import kinds_for

    comps = getattr(model, "components", None)
    tm = getattr(ctx, "type_map", None) or {}
    if comps is None:
        return {}
    index = {p: i for i, p in enumerate(comps.paths)}
    memo: Dict[str, Dict] = {}

    def accessors(qname):
        if qname not in memo:
            dt = tm.get(qname)
            memo[qname] = ({} if dt is None or isinstance(
                dt, (DataTypeRegister, DataTypeRegisterGroup))
                else {a.path: a for a in collect_accessors(dt)})
        return memo[qname]

    out: Dict[int, Dict] = {}
    for i, path in enumerate(comps.paths):
        if not path or not isinstance(tm.get(comps.types[i]), DataTypeRegister):
            continue
        segs = path.split(".")
        for k in range(len(segs) - 1, -1, -1):
            owner = ".".join(segs[:k])
            rest = segs[k:]
            acc = accessors(comps.types[index[owner]]).get(
                tuple(re.sub(r"\[\d+\]$", "", s) for s in rest))
            if acc is None:
                continue
            idx = [int(x) for x in re.findall(r"\[(\d+)\]", ".".join(rest))]
            out[i] = {
                "path": path,
                "comp": owner,
                "reg": ".".join(rest),
                "type": comps.types[i],
                "comp_type": comps.types[index[owner]],
                "offset": acc.const_off + sum(x * st for x, st in zip(idx, acc.strides)),
                "width": acc.prim_bits,
                "access": acc.access,
                "fields": ([_field(ctx, f) for f in struct_layout(acc.value_struct)]
                           if acc.is_struct else []),
            }
            # the file's kinds are a base (a field it names that PSS does
            # not declare is dropped); annotations win
            names = {f["name"] for f in out[i]["fields"]}
            base = {k: v for k, v in kinds_for(semantics or {}, path).items()
                    if k in names}
            _field_kinds(out[i], acc,
                         {**base, **_field_access(ctx, comps, index, path, acc)})
            break
    return out


def _field(ctx, f) -> Dict:
    """A register field: name, lsb, width (+ ``enum``, item -> value, when
    its type is an enum: the register coverage's bins)."""
    from zuspec.ir.core.data_type import DataTypeEnum, DataTypeRef
    out = {"name": f.name, "lsb": f.lsb, "width": f.width}
    dt = getattr(f.decl, "datatype", None)
    get = getattr(ctx, "get_type", None)
    if isinstance(dt, DataTypeRef) and get is not None:
        dt = get(dt.ref_name)
    if isinstance(dt, DataTypeEnum) and dt.items:
        out["enum"] = {n: int(v) for n, v in dt.items.items()}
    return out


#: ``pssunit_pkg::access_kind_e``, and the kind a register's access gives
#: each of its fields.
FIELD_KINDS = ("RO", "WO", "RW", "RC", "W1C", "W1S", "VOLATILE")
_DEFAULT_KIND = {"READONLY": "RO", "WRITEONLY": "WO"}


def _field_access(ctx, comps, index, path: str, acc) -> Dict[str, str]:
    """field name -> kind, from the ``@pssunit_pkg::field_access``
    annotations that reach register instance ``path`` (P1.10b): on its
    register type, then on each enclosing register group -- its type and
    its instance -- outermost first with ``reg`` naming the register from
    there (``bank.adr0`` or ``bank[1].adr0``), then on the register
    instance. A later one wins."""
    import re
    from zuspec.ir.core.data_type import DataTypeRegisterGroup
    from pssc import annotations

    tm = ctx.type_map
    q = "pssunit_pkg::field_access"

    def decl(p):
        parent, _, name = p.rpartition(".")
        dt = tm.get(comps.types[index[parent]]) if parent in index else None
        name = re.sub(r"\[\d+\]$", "", name)
        return next((f for f in getattr(dt, "fields", None) or []
                     if f.name == name), None)

    def apply(node, rel):
        for a in annotations.all_of(node, q) if node is not None else ():
            if a.get("reg", "") in rel:
                kind = a["kind"]
                kinds[a["field"]] = str(getattr(kind, "item", kind)).upper()

    kinds: Dict[str, str] = {}
    apply(tm.get(comps.types[index[path]]), ("",))
    segs = path.split(".")
    for k in range(1, len(segs)):
        group = ".".join(segs[:k])
        if group not in index or not isinstance(
                tm.get(comps.types[index[group]]), DataTypeRegisterGroup):
            continue
        rest = ".".join(segs[k:])
        rel = (rest, re.sub(r"\[\d+\]", "", rest))
        apply(tm.get(comps.types[index[group]]), rel)
        apply(decl(group), rel)
    apply(decl(path) or acc.decl, ("",))
    return kinds


def _field_kinds(reg: Dict, acc, kinds: Dict[str, str]) -> None:
    """Give each field of ``reg`` its ``kind`` (`FIELD_KINDS`)."""
    names = {f["name"] for f in reg["fields"]}
    for name, kind in kinds.items():
        if name not in names:
            raise ValueError(f"field_access on register {reg['path']}: "
                             f"it has no field {name!r}")
        if kind not in FIELD_KINDS:
            raise ValueError(f"field_access on register {reg['path']}: "
                             f"unknown kind {kind!r}")
    for f in reg["fields"]:
        f["kind"] = kinds.get(f["name"], _DEFAULT_KIND.get(reg["access"], "RW"))


def decode_access(model, detail: Dict) -> Dict:
    """An ``access`` event's detail with its register named: ``reg`` (the
    instance path) and, for a struct-valued register, ``fields`` (name ->
    value of ``data``). Unchanged when the access is no register's."""
    reg = (getattr(model, "registers", None) or {}).get(detail.get("reg_id"))
    if reg is None:
        return detail
    d = dict(detail, reg=reg["path"])
    if reg["fields"]:
        d["fields"] = {f["name"]: (d["data"] >> f["lsb"]) & ((1 << f["width"]) - 1)
                       for f in reg["fields"]}
    return d


def _sources(model) -> Dict[str, str]:
    """The text of each source file the model's provenance names, by path as
    provenance spells it; a file that cannot be read is left out."""
    sources: Dict[str, str] = {}
    for path in sorted({p.file for p in model.prov.entries if p.file}):
        try:
            with open(path, encoding="utf-8") as f:
                sources[path] = f.read()
        except (OSError, UnicodeDecodeError):
            continue
    return sources


def _reg_offset(group_dtype, name: str, index: Optional[int]) -> int:
    """Offset of ``name`` (``name[index]``) in a register group: the group's
    own ``get_offset_of_instance[_array]``, evaluated as progseq does."""
    from pssc.targets.progseq_model import array_base_stride, scalar_offset

    if index is None:
        return scalar_offset(group_dtype, name)
    base, stride = array_base_stride(group_dtype, name)
    return base + stride * index


def entries(model) -> Dict[str, int]:
    """Coroutine index of each entry of the model, by symbol: an action by
    its coroutine name, a function by ``<comp type>::<fn>``."""
    table = getattr(model, "entries", None)
    if table:
        return {sym: e.coro for sym, e in table.items()}
    return {c.name: i for i, c in enumerate(model.coros)}


def run(model, entry: int, seed: int, field_names: Sequence[str] = (),
        out: Optional[Callable[[str], None]] = None, args: Sequence[int] = (),
        events=None, watch: int = 0, name: str = "", bindings=None,
        setup: Sequence[int] = (), teardown: Sequence[int] = ()):
    """Run ``model`` from coroutine ``entry`` on a fresh action object.
    ``args`` are a function entry's arguments, in parameter order; its
    return value is the result's ``retval``.

    Returns ``(result, comp_obj)``: the backend's ``RunResult`` and the
    component object the run left behind, which is where a ledger's counts
    live. ``run_model`` does not hand the component object back, so this builds
    the VM the way it does. The model is not changed: the entry is a field of a
    shallow copy.

    ``events`` (a be-bc trace sink) gets the debug events in ``watch``
    (``zuspec.be.bc.trace.events.EV_*``); the run is a test named ``name``,
    between ``test.start`` and ``test.end``.

    ``bindings`` (``{"<comp type>::<fn>": callable}``) run host callables in
    place of those functions for this run only (``VM.bind``).

    ``setup`` and ``teardown`` are function entries (coroutine indices) of
    the root component, run in order on the same tree: setup after the tree
    is constructed, teardown after the entry whatever it did. A teardown that
    fails after the entry failed does not replace the entry's error.
    """
    import dataclasses

    from zuspec.be.bc.interp import NativeBlobBackend, Obj, VMError
    from zuspec.be.bc.interp.oracle import RunResult
    from zuspec.be.bc.interp.solve_cache import SolveCache
    from zuspec.be.bc.interp.vm import VM
    from zuspec.be.bc.trace import MemorySink

    model = dataclasses.replace(model, entry_coro=entry)
    obj = Obj(field_names=list(field_names)) if field_names else None
    cache = SolveCache()
    vm = VM(model, solve_backend=NativeBlobBackend(), sink=MemorySink(),
            out=out, solve_cache=cache, events=events)
    if watch:
        vm.watch(watch)
        watch_checks(vm)
    for qname, fn in (bindings or {}).items():
        vm.bind(qname, fn)
    root = None
    vm.host_event("test.start", {"name": name, "seed": seed})
    try:
        root = vm.root_frame(model.entry_coro, seed=seed, obj=obj)
        root.args = list(args)
        if root.cobj is not None:
            vm.construct_components()
        for coro in setup:
            _call_root(vm, coro, seed)
        try:
            _drain(vm, root)
        except BaseException:
            for coro in teardown:
                try:
                    _call_root(vm, coro, seed)
                except Exception:
                    pass
            raise
        for coro in teardown:
            _call_root(vm, coro, seed)
    finally:
        cache.clear()
        vm.host_event("test.end", {"name": name,
                                   "done": bool(root is not None and root.done)})
    if not root.done:
        raise VMError("the run ended with the entry unfinished: every thread "
                      "left is blocked")
    result = RunResult(obj=root.obj, retval=root.retval, events=[],
                       now=vm.sched.now, frames=vm.sched._next_id)
    return result, getattr(vm, "cobj", None)


def _drain(vm, frame) -> None:
    """Run ``frame`` and every thread it starts until nothing can run, as
    ``VM._drain`` does, over the host ABI: one ``HostCall`` on the tree as it
    stands, served by ``run_sync`` from the VM's own memory and imports. A
    frame left blocked returns normally; the caller checks ``frame.done``."""
    from zuspec.be.bc.interp.host import HostCall, run_sync

    call = HostCall(vm, frame, construct=False)
    try:
        run_sync(call)
    finally:
        call.end()


def _call_root(vm, coro: int, seed: int) -> None:
    """Run function entry ``coro`` in the root component, to completion."""
    from zuspec.be.bc.interp import VMError

    frame = vm.sched.new_frame(vm.model.coros[coro], seed=seed,
                               obj=vm._new_obj(coro))
    frame.cobj = vm.cobj
    vm.set_comp(frame, 0)
    _drain(vm, frame)
    if not frame.done:
        raise VMError(f"{vm.model.coros[coro].name} did not finish: every "
                      f"thread left is blocked")


def ledger(model, comp_obj, fields: Tuple[str, ...] = ("n_pass", "n_fail",
                                                        "n_skip")) -> Tuple[int, ...]:
    """``fields`` summed over every ledger instance in the component object.

    The component object is flat, each instance's fields named by its path
    (``rt.n_pass``). A ledger is an instance carrying all of ``fields``; all
    of them are summed rather than one required, since a fixture hierarchy
    may legitimately hold more than one.
    """
    comps = getattr(model, "components", None)
    if comp_obj is None or comps is None:
        return tuple(0 for _ in fields)
    names = set(comp_obj.field_names)
    totals: List[int] = [0] * len(fields)
    for path in comps.paths:
        pre = f"{path}." if path else ""
        if all(pre + f in names for f in fields):
            for k, f in enumerate(fields):
                totals[k] += comp_obj.get_field_name(pre + f)
    return tuple(totals)


class Instance:
    """A component instance of a :class:`Session`: its id and path in the
    tree (the root's path is ``""``) and its type."""

    def __init__(self, id: int, path: str, type_qname: str):
        self.id, self.path, self.type_qname = id, path, type_qname

    def __repr__(self) -> str:
        return f"Instance({self.path or '<root>'}: {self.type_qname})"


#: the native ledger's recording functions (pssunit_pkg ``rt_c``): every
#: ``check_*`` method ends in one of them
_LEDGER = "pssunit_pkg::rt_c::"
_RECORDS = {_LEDGER + "record": False, _LEDGER + "record_skip": True}


def watch_checks(vm) -> None:
    """Raise a ``check`` event (``EV_CHECK``) for each check the native
    ledger records: ``detail`` = ``{"verdict": "pass"|"fail"|"skip", "what",
    "check"}`` (``check``: the ``rt_c`` method the test called, such as
    ``check_eq_int``); the event's src_ref is that call's."""
    for i, c in enumerate(vm.model.coros):
        skip = _RECORDS.get(getattr(c, "fn_qname", ""))
        if skip is not None:
            vm.on_check(i, _ledger_check_skip if skip else _ledger_check)


def _ledger_site(frame):
    """The outermost ledger frame above ``frame`` and its call's src_ref."""
    f = frame
    while f.parent is not None and f.parent.coro.fn_qname.startswith(_LEDGER):
        f = f.parent
    caller = f.parent
    if caller is None or caller.pc == 0:
        return f, f.coro.src_ref
    return f, caller.coro.code[caller.pc - 1].src_ref


def _string(vm, v):
    strings = vm.model.strings
    return strings[v] if isinstance(v, int) and 0 <= v < len(strings) else v


def _ledger_check(vm, frame):
    ok, what = (list(frame.args) + [0, 0])[:2]
    f, sr = _ledger_site(frame)
    return ({"verdict": "pass" if ok else "fail", "what": _string(vm, what),
             "check": f.coro.fn_qname[len(_LEDGER):]}, sr)


def _ledger_check_skip(vm, frame):
    what, reason = (list(frame.args) + [0, 0])[:2]
    f, sr = _ledger_site(frame)
    return ({"verdict": "skip", "what": _string(vm, what),
             "reason": _string(vm, reason),
             "check": f.coro.fn_qname[len(_LEDGER):]}, sr)


class Session:
    """One component tree, constructed once, whose functions a host calls
    one after another: state a call leaves in the tree is there for the
    next (``pssvm`` in the design)::

        s = Session.load(model)
        root = s.construct()
        s.call(root, "ch[0].transfer_single", 1, 2)
        counts = s.run_test("my_fixture::t_ok", seed=1)

    Construction runs the model's ``$comp_init`` coroutine, as a run does
    before its entry. A function is called through its entry
    (``lower(..., functions=[...])``; ``"*"`` gives every function one), in
    the instance it is called on.
    """

    def __init__(self, model, seed: int = 0,
                 out: Optional[Callable[[str], None]] = None,
                 events=None, watch: int = 0, memory=None, imports=None,
                 engine: Optional[str] = None):
        from .native import engine_name
        self.model, self.seed, self.out = model, seed, out
        #: ``py`` (be-bc's VM) or ``c`` (rt-eng, :meth:`run_test` only;
        #: ``testing/native.py``); default ``PSSC_ENGINE``, else ``py``
        self.engine = engine_name(engine)
        #: the platform: what memory builtins reach (``read(addr, n)`` /
        #: ``write(addr, n, v)``) and what user imports call
        #: (``call(fn_id, args)``); None: be-bc's defaults
        self.memory, self.imports = memory, imports
        #: the debug events sink and mask (``zuspec.be.bc.trace.events``)
        self.events, self.watch = events, watch
        self.vm = None
        self._cache = None
        self._c_cobj = None       # the component object of the last C run
        #: the image's generation: 1, plus one per applied :meth:`reload`
        self.generation = 1
        #: each applied reload's result dict, oldest first
        self.history = []
        self._depth = 0           # calls running (a reload in one is staged)
        #: rand variable name -> (lo, hi) the entry's solves keep to (a
        #: property test's shrink; Python engine only); ``solved``: what the
        #: last ``run_entry``'s solves chose, by the same names
        self.solve_bounds = None
        self.solved = {}
        self._pending = None      # (model, vm, cache, result) staged

    @classmethod
    def load(cls, model, seed: int = 0,
             out: Optional[Callable[[str], None]] = None) -> "Session":
        return cls(model, seed=seed, out=out)

    @property
    def comp_obj(self):
        """The component object (every instance's fields, by path); on the
        C engine, as the last run left it."""
        if self.vm is None:
            return self._c_cobj
        return getattr(self.vm, "cobj", None)

    def construct(self, root_type: Optional[str] = None,
                  args: Sequence[int] = ()) -> Instance:
        """(Re)build the component tree; returns its root. ``root_type``, if
        given, must be the model's root type: the tree is fixed at lowering.
        PSS components take no constructor arguments, so ``args`` must be
        empty."""
        comps = getattr(self.model, "components", None)
        if comps is None:
            raise ValueError("the model has no component tree")
        if root_type is not None and root_type != comps.types[0]:
            raise ValueError(f"the model's root component is {comps.types[0]!r}, "
                             f"not {root_type!r}")
        if args:
            raise ValueError("components take no constructor arguments")
        self._new_vm()
        self.vm.cobj = comps.new_obj()
        self.vm._seed = self.seed
        self.vm.construct_components()
        return self.instance("")

    def _new_vm(self) -> None:
        """A fresh VM on the platform, with no component tree yet."""
        self.close()
        self.vm, self._cache = self._make_vm(self.model)

    def _make_vm(self, model):
        """A VM for ``model`` on the platform, and its solve cache."""
        from zuspec.be.bc.interp import NativeBlobBackend
        from zuspec.be.bc.interp.solve_cache import SolveCache
        from zuspec.be.bc.interp.vm import VM
        from zuspec.be.bc.trace import MemorySink

        cache = SolveCache()
        vm = VM(model, solve_backend=NativeBlobBackend(),
                sink=MemorySink(), out=self.out, solve_cache=cache,
                events=self.events, memory=self.memory,
                import_provider=self.imports)
        if self.watch:
            vm.watch(self.watch)
            watch_checks(vm)
        vm.solve_bounds = self.solve_bounds
        return vm, cache

    def close(self) -> None:
        if self._cache is not None:
            self._cache.clear()
        self.vm = self._cache = None

    def instance(self, path: str, under: Optional[Instance] = None) -> Instance:
        """The instance at ``path`` (``ch[0]``, ``a.sub``), from ``under``
        (default: the root)."""
        comps = self.model.components
        if under is not None and under.path:
            path = f"{under.path}.{path}" if path else under.path
        try:
            i = comps.paths.index(path)
        except ValueError:
            raise KeyError(f"no component instance {path!r}") from None
        return Instance(i, path, comps.types[i])

    def entry(self, inst: Instance, fn: str):
        """The entry of function ``fn`` for ``inst``'s type."""
        for sym, e in self.model.entries.items():
            if (e.kind == "function" and e.comp_type == inst.type_qname
                    and sym.rpartition("::")[2] == fn):
                return e
        raise KeyError(f"{inst.type_qname}::{fn} has no entry: lower with "
                       f"functions=[{inst.type_qname + '::' + fn!r}] or ['*']")

    def call(self, inst: Instance, target: str, *args: int):
        """Call function ``target`` (``f``, or ``ch[0].f`` below ``inst``) and
        return its value (None for a void function). A struct parameter
        takes one argument per field, in declaration order (the entry's
        ``params`` name them: ``cfg.src``, ``cfg.dst``, ...)."""
        from zuspec.be.bc.interp import VMError

        if self.vm is None:
            raise RuntimeError("construct() the component tree first")
        path, _, fn = target.rpartition(".")
        if path:
            inst = self.instance(path, under=inst)
        e = self.entry(inst, fn)
        if len(args) != len(e.params):
            raise TypeError(f"{target} takes {len(e.params)} arguments, "
                            f"not {len(args)}")
        vm = self.vm
        frame = vm.sched.new_frame(self.model.coros[e.coro], seed=self.seed,
                                   obj=vm._new_obj(e.coro))
        frame.cobj = vm.cobj
        vm.set_comp(frame, inst.id)
        frame.args = list(args)
        self._depth += 1
        try:
            _drain(vm, frame)
        finally:
            self._depth -= 1
            if not self._depth and self._pending is not None:
                self._apply_reload()
        if not frame.done:
            raise VMError(f"{target} did not finish: every thread left is "
                          f"blocked")
        return frame.retval

    def reload(self, model, cancel: bool = False, strict: bool = False) -> dict:
        """Hot reload (be-bc ``interp/generations.py``): make ``model``, a
        new image of the same sources, the session's generation N+1.

        The API check comes first, then (``strict``) no change to the
        component tree; a refusal raises be-bc's ``ReloadRefused`` (its
        ``to_json()`` is the error) and leaves the session as it was. With
        a tree constructed, N+1's is constructed beside it bus-isolated (a
        memory access or import there refuses, reason ``bus``) and, when
        applied, takes N's state, channel tokens included, per unchanged
        instance path; bindings carry over where the function is still
        called (``unbound`` lists the rest). A reload from inside a call (a
        binding, or a debugger stopped in one) is staged: the call finishes
        on N and N+1 applies when it returns; ``cancel`` is refused there,
        since a synchronous call cannot be ended early. Returns the result
        dict (``applied`` says whether N+1 is current yet); the applied
        result is raised as a ``reload`` event on N+1's VM."""
        from zuspec.be.bc.interp.generations import (check_reload,
                                                     construct_isolated)

        if cancel and self._depth:
            raise RuntimeError("cannot cancel the call running: reload once "
                               "it returns, or without cancel")
        result = check_reload(self.model, model, strict, self.generation)
        result["unbound"] = []
        vm = cache = None
        if self.vm is not None and getattr(self.vm, "cobj", None) is not None:
            vm, cache = self._make_vm(model)
            comps = getattr(model, "components", None)
            if comps is not None:
                vm.cobj = comps.new_obj()
                vm._seed = self.seed
                try:
                    construct_isolated(vm, result["new"])
                except BaseException:
                    cache.clear()
                    raise
        if self._pending is not None and self._pending[2] is not None:
            self._pending[2].clear()
        self._pending = (model, vm, cache, result)
        if not self._depth:
            self._apply_reload()
        return result

    def _apply_reload(self) -> None:
        """Make the staged generation current (:meth:`reload`)."""
        from zuspec.be.bc.interp.generations import migrate

        model, vm, cache, result = self._pending
        self._pending = None
        old = self.vm
        if vm is not None:
            result["migrated"] = migrate(old, vm)
            seen = {}
            for qname, fn in old.bindings.values():
                seen.setdefault(qname, fn)
            for qname, fn in seen.items():
                try:
                    vm.bind(qname, fn)
                except KeyError:
                    result["unbound"].append(qname)
            self.close()
            self.vm, self._cache = vm, cache
        else:
            self.close()
        self.model = model
        self.generation = result["new"]
        add = getattr(self.events, "add_model", None)
        if add is not None:             # a Coverage sink: N+1's points
            add(model)
        result["applied"] = True
        self.history.append(result)
        from zuspec.be.bc.trace import events as ev
        if self.events is not None and self.watch & ev.EV_RELOAD:
            self.events.event(ev.RELOAD, src_ref=0, detail=result)

    def bind(self, qname: str, fn) -> None:
        """Run host callable ``fn`` in place of function ``qname`` until the
        tree is next constructed (``VM.bind``)."""
        if self.vm is None:
            raise ValueError("construct() the tree before binding")
        self.vm.bind(qname, fn)

    def run_test(self, symbol: str, seed: Optional[int] = None,
                 bindings=None) -> Tuple[int, ...]:
        """Run test function ``symbol`` (``<comp type>::<fn>``) in the root of
        a freshly constructed tree; returns the ledger's
        ``(n_pass, n_fail, n_skip)``. ``bindings``
        (``{"<comp type>::<fn>": callable}``) last this test only: the next
        construction starts without them."""
        if seed is not None:
            self.seed = seed
        if self.engine == "c":
            return self._run_test_c(symbol, bindings)
        root = self.construct()
        for qname, fn in (bindings or {}).items():
            self.bind(qname, fn)
        fn = symbol.rpartition("::")[2]
        e = self.model.entries.get(symbol)
        if e is None:
            raise KeyError(f"{symbol} has no entry")
        if e.comp_type != root.type_qname:
            raise KeyError(f"{symbol} is not a function of the root "
                           f"{root.type_qname!r}")
        self.vm.host_event("test.start", {"name": symbol, "seed": self.seed})
        counts = None
        try:
            self.call(root, fn)
            counts = ledger(self.model, self.comp_obj)
        finally:
            end = {"name": symbol, "done": counts is not None}
            if counts is not None:
                end.update(zip(("n_pass", "n_fail", "n_skip"), counts))
            self.vm.host_event("test.end", end)
        return counts

    def _run_test_c(self, symbol: str, bindings, setup=(),
                    teardown=()) -> Tuple[int, ...]:
        """:meth:`run_test` on the C engine, with the session's mocks
        (``imports``) and ``bindings``: a root function, or an action on an
        object of its fields."""
        from . import native

        e = self.model.entries.get(symbol)
        if e is None:
            raise KeyError(f"{symbol} has no entry")
        self.close()
        return native.run_test(self.model, symbol, self.seed,
                               self.memory, self.imports,
                               self.events, self.watch,
                               keep=lambda o: setattr(self, "_c_cobj", o),
                               setup=setup, teardown=teardown,
                               bindings=bindings, **self._native_kw())

    def _native_kw(self) -> dict:
        """More keywords for ``native.run_test`` (a subclass's)."""
        return {}

    def _run_entry_c(self, coro: int, setup, teardown,
                     bindings) -> Tuple[int, ...]:
        """:meth:`run_entry` on the C engine: a root function or action
        entry (an action with an activity is refused by ``native.run_test``)."""
        sym = {e.coro: s for s, e in self.model.entries.items()}
        return self._run_test_c(sym[coro], bindings,
                                [sym[c] for c in setup],
                                [sym[c] for c in teardown])


    def run_entry(self, coro: int, name: str = "", seed: Optional[int] = None,
                  field_names: Sequence[str] = (), setup: Sequence[int] = (),
                  teardown: Sequence[int] = (), bindings=None,
                  new_vm: bool = True) -> Tuple[int, ...]:
        """``run`` on this session's platform: entry ``coro`` (an action or a
        root function) on a fresh tree, between ``setup`` and ``teardown``
        (root function entries); returns the ledger's
        ``(n_pass, n_fail, n_skip)``. ``name`` names the test in the
        ``test.start``/``test.end`` events. ``new_vm=False`` runs on the VM
        made beforehand (``_new_vm``), which a debug stub already holds. A
        :meth:`reload` during the run is staged until it ends."""
        from zuspec.be.bc.interp import Obj, VMError

        if seed is not None:
            self.seed = seed
        if self.engine == "c":
            return self._run_entry_c(coro, setup, teardown, bindings)
        if new_vm or self.vm is None:
            self._new_vm()
        vm = self.vm
        for qname, fn in (bindings or {}).items():
            vm.bind(qname, fn)
        obj = Obj(field_names=list(field_names)) if field_names else None
        vm.host_event("test.start", {"name": name, "seed": self.seed})
        root = counts = None
        self._depth += 1                   # a reload during the run is staged
        try:
            vm.solve_bounds = self.solve_bounds
            root = vm.root_frame(coro, seed=self.seed, obj=obj)
            vm.set_entry(root)
            # filled in as the run solves, so a failing run has it too
            self.solved = vm.solved
            if root.cobj is not None:
                vm.construct_components()
            for c in setup:
                _call_root(vm, c, self.seed)
            try:
                _drain(vm, root)
            except BaseException:
                for c in teardown:
                    try:
                        _call_root(vm, c, self.seed)
                    except Exception:
                        pass
                raise
            for c in teardown:
                _call_root(vm, c, self.seed)
            if not root.done:
                raise VMError("the run ended with the entry unfinished: every "
                              "thread left is blocked")
            counts = ledger(self.model, self.comp_obj)
        finally:
            end = {"name": name, "done": counts is not None}
            if counts is not None:
                end.update(zip(("n_pass", "n_fail", "n_skip"), counts))
            vm.host_event("test.end", end)
            self._depth -= 1
            if not self._depth and self._pending is not None:
                self._apply_reload()
        return counts


def solve_failures() -> tuple:
    """Exception types meaning "the model is unsatisfiable"."""
    try:
        from zuspec.be.bc.interp import SolveUnsatError
        return (SolveUnsatError,)
    except Exception:  # pragma: no cover - backend layout changed
        return ()


def lowering_failures() -> tuple:
    """Types meaning "the backend cannot express or decide this model": a
    construct it cannot lower, or a solve that ran out of budget (which may or
    may not have a solution -- a tool limit, not the model's verdict)."""
    types: List[type] = []
    for mod, name in (("zuspec.be.bc.lower.errors", "LoweringError"),
                      ("zuspec.ir.core.xf.validate", "UnsupportedConstructError"),
                      ("zuspec.be.bc.interp.solve_cache", "SolveBudgetError")):
        try:
            types.append(getattr(__import__(mod, fromlist=[name]), name))
        except Exception:  # pragma: no cover
            pass
    return tuple(types)
