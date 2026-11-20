"""PSS reaction hooks (P2.6, design §7.2): the ``@pssunit_pkg::on_write``,
``on_read`` and ``on_event`` functions of the ``mock_c`` components a
fixture composes::

    component dma_mock_c : pssunit_pkg::mock_c {
        @pssunit_pkg::on_write {.reg = "grp_c.ctl", .field = "go"}
        target function void armed() { ... }
    }
    component dev_fx_c : pssunit_pkg::fixture_c {
        dma_mock_c mock;
        ...
    }

A register is named as the Python fake's hooks name one (``fake.py``): an
instance path from the fixture (``regs.ctl``), the same without its indices,
or ``<group type>.<register>``. The names are resolved when the image is
lowered (`declared`), so one that matches no register, a field the register
does not have, or a hook on a function that is not a ``target function`` is
an error located at the annotation.
"""
from __future__ import annotations

import dataclasses as _dc
import re
from typing import Any, Dict, FrozenSet, List, Optional

MOCK = "pssunit_pkg::mock_c"
#: annotation -> the op its hook runs on
OPS = {"pssunit_pkg::on_write": "write",
       "pssunit_pkg::on_read": "read",
       "pssunit_pkg::on_event": "event"}


@_dc.dataclass(frozen=True)
class Hook:
    """One hook: ``op`` (``write``/``read``/``event``), the mock instance
    ``inst`` (its path in the fixture) and function ``fn``
    (``<declaring comp>::<name>``), and what it runs on: the register
    spelling ``reg`` and the reg-ids it names (``rids``) with ``field``
    (or None), or the event ``name``. ``src`` is the annotation's
    ``file:line:col``. ``entry`` is its image entry's symbol
    (``<instance's type>::<name>``, `entry_symbols`)."""
    op: str
    inst: str
    fn: str
    reg: Optional[str] = None
    rids: FrozenSet[int] = frozenset()
    field: Optional[str] = None
    name: Optional[str] = None
    src: Optional[str] = None
    entry: Optional[str] = None


def reg_names(registers: Dict[int, Dict], components) -> Dict[str, set]:
    """Each way to name a register (module doc) -> the reg-ids it names."""
    ctype = (dict(zip(components.paths, components.types))
             if components is not None else {})
    names: Dict[str, set] = {}
    for rid, r in registers.items():
        path = r["path"]
        group, _, leaf = path.rpartition(".")
        leaf = re.sub(r"\[\d+\]$", "", leaf)
        keys = {path, re.sub(r"\[\d+\]", "", path)}
        t = ctype.get(group)
        if t:
            keys |= {f"{t}.{leaf}", f"{t.rpartition('::')[2]}.{leaf}"}
        for k in keys:
            names.setdefault(k, set()).add(rid)
    return names


def _src(ann) -> Optional[str]:
    loc = getattr(ann, "loc", None)
    if loc is None:
        return None
    return f"{loc.file}:{loc.line}:{loc.pos}"


def _hook_fns(ctx, tname: str):
    """(declaring comp, function, op, annotation) for each hook of component
    type ``tname`` (its own and its bases'), or nothing when it is not a
    ``mock_c``."""
    from .catalog import base_chain

    tm: Dict[str, Any] = ctx.type_map
    cdt = tm.get(tname)
    chain = base_chain(ctx, cdt) if cdt is not None else []
    if MOCK not in chain:
        return
    lineage = [c for c in reversed(chain) if c in tm] + [tname]
    fns: Dict[str, tuple] = {}
    for decl in lineage:
        for fn in getattr(tm[decl], "functions", None) or []:
            fns[fn.name] = (decl, fn)       # a redeclaration replaces
    for decl, fn in fns.values():
        for a in getattr(fn, "annotations", None) or []:
            op = OPS.get(a.type_qname)
            if op is not None:
                yield decl, fn, op, a


def entry_symbols(ctx, root: str) -> List[str]:
    """``<comp type>::<fn>`` of each hook function of the ``mock_c`` types in
    ``root``'s component tree (component fields, followed from ``root``
    through bases), for `bc_session.lower` to give an entry: ``"*"`` skips
    a function with an empty body, and ``pssc test`` names its functions.
    The default interrupt handler (``vm.IRQ_HANDLER``) of a type in the
    tree gets one too, so ``raise_irq`` is delivered to it."""
    from .catalog import base_chain
    from .vm import IRQ_HANDLER

    tm: Dict[str, Any] = ctx.type_map
    seen: List[str] = []
    todo = [root]
    while todo:
        t = todo.pop()
        if t in seen or t not in tm:
            continue
        seen.append(t)
        for decl in [t] + base_chain(ctx, tm[t]):
            for f in getattr(tm.get(decl), "fields", None) or []:
                dt = getattr(f, "datatype", None)
                dt = getattr(dt, "element_type", None) or dt
                if type(dt).__name__ == "DataTypeComponent":
                    todo.append(dt.name)
    out: List[str] = []
    for t in seen:
        for _decl, fn, _op, _a in _hook_fns(ctx, t):
            sym = f"{t}::{fn.name}"
            if sym not in out:
                out.append(sym)
        for decl in [t] + base_chain(ctx, tm[t]):
            fns = getattr(tm.get(decl), "functions", None) or []
            if any(fn.name == IRQ_HANDLER for fn in fns):
                out.append(f"{t}::{IRQ_HANDLER}")
                break
    return out


def declared(ctx, model) -> List[Hook]:
    """The hooks of every ``mock_c`` instance in ``model``'s component tree
    (its type's functions and its bases'), with their registers resolved
    against ``model.registers``. Raises `pssc.diag.Refusal` located at the
    annotation for a name that resolves to nothing."""
    from pssc import diag

    comps = getattr(model, "components", None)
    if comps is None:
        return []
    registers = getattr(model, "registers", None) or {}
    names = None
    out: List[Hook] = []
    for inst, tname in zip(comps.paths, comps.types):
        for decl, fn, op, a in _hook_fns(ctx, tname):
            def refuse(msg):
                return diag.Refusal(diag.at((a, fn), f"{a.type_qname} on "
                                            f"{decl}::{fn.name}: {msg}"), a, fn)
            if not getattr(fn, "is_target", False):
                raise refuse("a hook must be a 'target function'")
            v = a.values
            if op == "event":
                out.append(Hook(op, inst, f"{decl}::{fn.name}",
                                name=v["name"], src=_src(a),
                                entry=f"{tname}::{fn.name}"))
                continue
            if names is None:
                names = reg_names(registers, comps)
            rids = names.get(v["reg"])
            if not rids:
                raise refuse(f"{v['reg']!r} names no register")
            field = v.get("field") or None
            if field is not None:
                for rid in sorted(rids):
                    r = registers[rid]
                    if field not in {f["name"] for f in r["fields"]}:
                        raise refuse(f"register {r['path']} has no field "
                                     f"{field!r}")
            out.append(Hook(op, inst, f"{decl}::{fn.name}", reg=v["reg"],
                            rids=frozenset(rids), field=field, src=_src(a),
                            entry=f"{tname}::{fn.name}"))
    return out


#: the fields of ``pssunit_pkg::access_s``, as a register hook's parameter
#: takes them (one argument per field)
ACCESS_FIELDS = ("addr", "width", "data", "is_write", "index")


def _access_param(params) -> bool:
    """Whether a register hook's entry ``params`` are one ``access_s``."""
    names = tuple(p[0] for p in params or ())
    stem = names[0].rpartition(".")[0] if names else ""
    return bool(stem) and names == tuple(f"{stem}.{f}" for f in ACCESS_FIELDS)


def bind(session) -> None:
    """Add the ``on_write``/``on_read`` hooks of ``session``'s image to its
    ``RegisterFake`` platform's dispatch (after the fake's own, which
    ``attach`` found): the fixture composes the mock, so the fake runs its
    hooks. An access a hook names (with its field, a 1 written to it)
    runs the hook function in its mock instance as a thread of its own, at
    the next scheduling point, as an interrupt handler is
    (``vm.Ctx.raise_irq``); the access itself is not changed. A hook with
    a ``pssunit_pkg::access_s`` parameter is given the access (``index``
    -1 for a register not in an array)."""
    fake = session.platform
    hooks = [h for h in getattr(session.model, "hooks", None) or []
             if h.op in ("write", "read")]
    if not hooks or not hasattr(fake, "_hooks"):
        return
    for h in hooks:
        e = _entry(session, h)
        if e.params and not _access_param(e.params):
            raise ValueError(f"{h.src}: hook {h.fn} takes arguments other "
                             f"than one pssunit_pkg::access_s")
        comp = session.instance(h.inst).id
        fake._hooks.append((h.op, h.field, h.rids,
                            _starter(session, e.coro, comp, bool(e.params))))


def _entry(session, h: Hook):
    e = session.model.entries.get(h.entry)
    if e is None:
        raise ValueError(f"{h.src}: hook {h.fn} has no entry "
                         f"(bc_session.lower gives it one)")
    return e


def _frame(session, coro: int, comp: int, args: List[int]):
    vm = session.vm
    frame = vm.sched.new_frame(session.model.coros[coro], seed=session.seed,
                               obj=vm._new_obj(coro))
    frame.cobj = vm.cobj
    vm.set_comp(frame, comp)
    frame.args = list(args)
    return frame


def _starter(session, coro: int, comp: int, access: bool):
    def start(_ctx, acc) -> None:
        args = []
        if access:
            idx = acc.index if acc.index is not None else -1
            args = [acc.addr, acc.width, acc.data, int(acc.op == "write"), idx]
        session.vm.sched.ready(_frame(session, coro, comp, args))
    return start


def schedule(session, delay: int, name: str, arg: int) -> None:
    """``schedule_after``: ready each ``on_event`` hook named ``name`` at
    ``now + delay``, in its mock instance; a hook with a parameter (one
    ``int``) is given ``arg``. Raises ValueError when no hook has that
    name."""
    hooks = [h for h in getattr(session.model, "hooks", None) or []
             if h.op == "event" and h.name == name]
    if not hooks:
        raise ValueError(f"schedule_after: no on_event hook is named {name!r}")
    vm = session.vm
    for h in hooks:
        e = _entry(session, h)
        if len(e.params) > 1:
            raise ValueError(f"{h.src}: hook {h.fn} takes more than one "
                             f"argument; an on_event hook takes an int or none")
        frame = _frame(session, e.coro, session.instance(h.inst).id,
                       [arg] if e.params else [])
        vm.sched.schedule_at(frame, vm.sched.now + max(0, int(delay)))
