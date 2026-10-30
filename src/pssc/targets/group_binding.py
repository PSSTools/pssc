"""Which handle each register group of a component is bound to, or that it is not.

A register group's registers are at the group's handle plus their offsets
(LRM 21.14.1), and the handle is whatever `set_handle` gave it. A group that
nothing binds has no address. Before this check, the C and Python backends
answered for it anyway: they bound every group to the constructor's first
address parameter, so a model binding `a` but forgetting `b` wrote `b`'s
registers over `a`'s, and the build exited 0.

One convention survives, because a shipped model states it on purpose: a
component with exactly ONE register group, whose constructor binds no group,
binds that group to the constructor's first address parameter ("the binding is
the generator's job", `src/pssc/testing/models/dma_engine.pss`). It cannot
alias anything: there is no second group for it to land on. Every other group
that no `set_handle` reaches is refused (`check`).

Language-neutral, like `reg_layout`: it says which groups are bound, never how
a backend stores the handle.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, FrozenSet, Iterator, List, Mapping, Optional, Tuple

from ..diag import at
from .progseq_model import (FuncKind, _dt_name, array_element_type,
                            field_is_array, func_kind, is_reg_group,
                            sub_components)
from .ir_walk import children

__all__ = ["Binding", "bindings", "check", "group_fields", "handle_params"]


def group_fields(comp) -> List[str]:
    """The register-group fields of *comp*, scalar or array, in order."""
    out = []
    for f in getattr(comp, "fields", None) or []:
        dt = f.datatype
        if field_is_array(f):
            dt = array_element_type(f)
        if is_reg_group(dt):
            out.append(f.name)
    return out


@dc.dataclass(frozen=True)
class Binding:
    """How one component type's register groups get their handles."""

    #: Its register-group fields, in declaration order.
    groups: Tuple[str, ...]
    #: Those a `set_handle` reaches: from the component's own functions
    #: (constructor, init blocks, the private copies of a base's), or from
    #: an enclosing component through an instance path.
    bound: FrozenSet[str]
    #: The one group the convention binds to this constructor parameter (its
    #: IR name), or ``None``.
    implicit: Optional[Tuple[str, str]] = None
    #: Every group path this component's functions bind -- its own groups,
    #: ``(g,)``, and a sub-component's, ``(sub, g)`` -- to the constructor
    #: parameter that gives the handle: the parameter's name when the
    #: argument is that parameter alone (through a `super.` call too),
    #: otherwise ``None``. The implicit binding is here as well.
    params: Mapping[Tuple[str, ...], Optional[str]] = dc.field(
        default_factory=dict, compare=False, hash=False)
    #: The constructor, or ``None``.
    init_fn: Any = dc.field(default=None, compare=False, hash=False)
    #: The constructor does nothing but bind: every statement is
    #: `<path>.set_handle(<parameter>)` on a path with no index, a
    #: sub-component's constructor call, or a `super.` constructor that is
    #: itself only bindings -- with no control flow. One parameter may bind
    #: two groups (two views of one register window are legal). A component
    #: with no constructor, or an empty one, is pure. A conflict (below)
    #: makes it impure.
    pure_bindings: bool = True
    #: Every group path the constructor binds to two or more different
    #: parameters, to those parameters in the order bound (`params` maps
    #: such a path to ``None``).
    conflicts: Mapping[Tuple[str, ...], Tuple[str, ...]] = dc.field(
        default_factory=dict, compare=False, hash=False)
    #: The constructor's parameters, in declaration order, that give no
    #: group a handle: not passed (alone) to a `set_handle`, the implicit
    #: binding, a `super.` constructor that binds it, or a sub-component's
    #: constructor that binds it. Every parameter is listed, whatever its
    #: type; the caller filters.
    unbound_params: Tuple[str, ...] = dc.field(
        default=(), compare=False, hash=False)

    @property
    def unbound(self) -> Tuple[str, ...]:
        done = set(self.bound)
        if self.implicit is not None:
            done.add(self.implicit[0])
        return tuple(g for g in self.groups if g not in done)


def bindings(comps, ctor_names=None, tm=None) -> Dict[int, Binding]:
    """`{id(comp): Binding}` for every component type in *comps*.

    A sub-component's group bound by an enclosing component counts only when
    the enclosing component is in *comps*: pass the whole model (as
    ``OpModel.bindings`` does), never one component, or the implicit binding
    of a group its parent binds disagrees with the gate's."""
    tm = tm or {}
    bound: Dict[int, set] = {id(c): set() for c in comps}
    by_name = {}
    for c in comps:
        by_name[getattr(c, "name", None)] = c
    for c in comps:
        subs = {s.name: s.dtype for s in sub_components(c)}
        for fn in getattr(c, "functions", None) or []:
            for path in _set_handle_paths(fn):
                if len(path) == 1:
                    bound[id(c)].add(path[0])
                elif len(path) == 2 and path[0] in subs:
                    sub = _resolve(subs[path[0]], tm, by_name)
                    if sub is not None and id(sub) in bound:
                        bound[id(sub)].add(path[1])
    out = {}
    walks = {}
    for c in comps:
        groups = tuple(group_fields(c))
        b = frozenset(g for g in bound[id(c)] if g in groups)
        ctor = _ctor(c, ctor_names)
        implicit = None
        if len(groups) == 1 and not b:
            arg = _first_addr_arg(ctor, tm)
            if arg is not None:
                implicit = (groups[0], arg)
        params, conflicts, sub_calls = _ctor_params(c, ctor, ctor_names)
        if implicit is not None:
            params[(implicit[0],)] = implicit[1]
        walks[id(c)] = (c, ctor, sub_calls)
        out[id(c)] = Binding(groups, b, implicit, params=params, init_fn=ctor,
                             pure_bindings=not conflicts
                             and _pure(c, ctor, ctor_names),
                             conflicts=conflicts)
    used: Dict[int, set] = {}

    def binding_params(cid, active):
        if cid in used:
            return used[cid]
        if cid in active or cid not in walks:
            return set()
        c, ctor, sub_calls = walks[cid]
        b = out[cid]
        got = {p for p in b.params.values() if p is not None}
        for ps in b.conflicts.values():
            got.update(ps)
        subs = {s.name: s.dtype for s in sub_components(c)}
        for name, args in sub_calls:
            sub = _resolve(subs[name], tm, by_name) if name in subs else None
            if sub is None:
                continue
            sub_used = binding_params(id(sub), active | {cid})
            sctor = walks[id(sub)][1] if id(sub) in walks else None
            for a, p in zip(_args(sctor), args):
                if p is not None and a.arg in sub_used:
                    got.add(p)
        used[cid] = got
        return got

    for c in comps:
        got = binding_params(id(c), frozenset())
        ctor = walks[id(c)][1]
        out[id(c)] = dc.replace(out[id(c)], unbound_params=tuple(
            a.arg for a in _args(ctor) if a.arg not in got))
    return out


def handle_params(model, table=None) -> Dict[Tuple[str, ...], Optional[str]]:
    """Every register group in the tree under ``model.root``, by instance
    path from the root (``("a",)``, ``("sub", "a")``), mapped to the ROOT
    constructor's parameter whose handle it is bound to, or ``None`` when it
    is not one parameter passed down unchanged.

    Composes a sub-component's bindings through the constructor call that
    passes it the parameter (`sub.initialize(base)`) and through a
    `super.initialize(base)`. An enclosing component's own
    `sub.g.set_handle(p)` takes precedence over the sub-component's. A group
    in an array of sub-components, or reached through an index, is ``None``.
    """
    table = table if table is not None else model.bindings
    root = model.root
    rctor = model.ctor(root)
    env = {a.arg: a.arg for a in _args(rctor)}
    out: Dict[Tuple[str, ...], Optional[str]] = {}
    _compose(model, table, root, (), env, out, set())
    return out


def _compose(model, table, comp, prefix, env, out, active):
    if id(comp) in active:
        return
    active = active | {id(comp)}
    b = table.get(id(comp))
    ctor = model.ctor(comp)
    for sub in sub_components(comp):
        sub_env: Dict[str, Optional[str]] = {}
        sctor = model.ctor(sub.dtype)
        call = _sub_ctor_call(ctor, sub.name, model.ctor_names)
        if call is not None and sctor is not None and not sub.size:
            for i, a in enumerate(_args(sctor)):
                arg = call.args[i] if i < len(call.args) else None
                q = _bare(arg, ctor)
                sub_env[a.arg] = env.get(q) if q is not None else None
        if sub.size:
            # An array of sub-components: one path for every element.
            for g in group_fields(sub.dtype):
                out[prefix + (sub.name, g)] = None
            continue
        _compose(model, table, sub.dtype, prefix + (sub.name,), sub_env,
                 out, active)
    if b is None:
        return
    for g in b.groups:
        out.setdefault(prefix + (g,), None)
    for path, p in b.params.items():
        out[prefix + path] = env.get(p) if p is not None else None


def _args(fn) -> List[Any]:
    return list(getattr(getattr(fn, "args", None), "args", None) or [])


def _bare(e, fn) -> Optional[str]:
    """The parameter of *fn* that *e* is, alone, or None. The front end
    spells a parameter `self.p`, as it does a field, so a name is looked up
    parameter first (AGENTS.md)."""
    params = {a.arg for a in _args(fn)}
    cn = _dt_name(e)
    if cn == "ExprRefLocal" and e.name in params:
        return e.name
    if cn == "ExprAttribute" and _dt_name(e.value) == "TypeExprRefSelf" \
            and e.attr in params:
        return e.attr
    return None


def _sub_ctor_call(ctor, sub_name, ctor_names):
    """`<sub_name>.initialize(...)` in *ctor*'s body, or None."""
    if ctor is None:
        return None
    from .progseq_model import current_ctor_names
    names = set(ctor_names or current_ctor_names())
    for call in _walk(getattr(ctor, "body", None)):
        if _dt_name(call) != "ExprCall" or _dt_name(call.func) != "ExprAttribute":
            continue
        if call.func.attr in names and _path(call.func.value) == (sub_name,):
            return call
    return None


def _self_callee(comp, call):
    """The function of *comp* that *call* (`self.f(...)`) calls, or None."""
    f = call.func
    if _dt_name(f) != "ExprAttribute" or _dt_name(f.value) != "TypeExprRefSelf":
        return None
    for fn in getattr(comp, "functions", None) or []:
        if fn.name == f.attr:
            return fn
    return None


def _ctor_params(comp, ctor, ctor_names=None):
    """`(params, conflicts, sub_calls)`. Binding.params: what each
    `set_handle` in the constructor binds, followed through the
    constructor's own calls (`super.initialize(...)`). A group bound outside
    the constructor, or bound twice to different parameters, maps to None.
    Binding.conflicts: the second case, with the parameters. *sub_calls*:
    `(sub, [constructor parameter or None per argument])` for every
    sub-component constructor call."""
    from .progseq_model import current_ctor_names
    names = set(ctor_names or current_ctor_names())
    out: Dict[Tuple[str, ...], Optional[str]] = {}
    seen_params: Dict[Tuple[str, ...], List[str]] = {}
    sub_calls: List[Tuple[str, List[Optional[str]]]] = []
    reached: set = set()

    def walk(fn, env, seen):
        if id(fn) in seen:
            return
        seen = seen | {id(fn)}
        reached.add(id(fn))
        for call in _walk(getattr(fn, "body", None)):
            if _dt_name(call) != "ExprCall":
                continue
            func = call.func
            if _dt_name(func) == "ExprAttribute" and func.attr == "set_handle":
                path = _path(func.value)
                if not path:
                    continue
                p = _bare(call.args[0], fn) if call.args else None
                p = env.get(p) if p is not None else None
                if p is not None and p not in seen_params.setdefault(path, []):
                    seen_params[path].append(p)
                if path in out and out[path] != p:
                    out[path] = None
                else:
                    out[path] = p
                continue
            if _dt_name(func) == "ExprAttribute" and func.attr in names \
                    and _dt_name(func.value) != "TypeExprRefSelf":
                sub = _path(func.value)
                if sub is not None and len(sub) == 1:
                    args = []
                    for a in call.args:
                        q = _bare(a, fn)
                        args.append(env.get(q) if q is not None else None)
                    sub_calls.append((sub[0], args))
                    continue
            callee = _self_callee(comp, call)
            if callee is not None:
                sub_env = {}
                for i, a in enumerate(_args(callee)):
                    arg = call.args[i] if i < len(call.args) else None
                    q = _bare(arg, fn)
                    sub_env[a.arg] = env.get(q) if q is not None else None
                walk(callee, sub_env, seen)

    if ctor is not None:
        walk(ctor, {a.arg: a.arg for a in _args(ctor)}, set())
    # Bound outside the constructor (an init block): no parameter.
    for fn in getattr(comp, "functions", None) or []:
        if id(fn) not in reached:
            for path in _set_handle_paths(fn):
                out[path] = None
    conflicts = {k: tuple(v) for k, v in seen_params.items() if len(v) > 1}
    return out, conflicts, sub_calls


def _pure(comp, ctor, ctor_names, seen=None) -> bool:
    """Binding.pure_bindings."""
    if ctor is None:
        return True
    seen = (seen or set()) | {id(ctor)}
    from .progseq_model import current_ctor_names
    names = set(ctor_names or current_ctor_names())
    for st in getattr(ctor, "body", None) or []:
        if _dt_name(st) != "StmtExpr" or _dt_name(st.expr) != "ExprCall":
            return False
        call, func = st.expr, st.expr.func
        if _dt_name(func) != "ExprAttribute":
            return False
        if not all(_bare(a, ctor) is not None for a in call.args):
            return False
        if func.attr == "set_handle":
            if not _plain_path(func.value):
                return False
            continue
        callee = _self_callee(comp, call)
        if callee is not None:
            if id(callee) in seen or not _pure(comp, callee, ctor_names, seen):
                return False
            continue
        if func.attr in names and _plain_path(func.value) \
                and len(_path(func.value) or ()) == 1:
            continue                    # a sub-component's constructor
        return False
    return True


def _plain_path(e) -> bool:
    """A self-rooted path with no index."""
    cn = _dt_name(e)
    if cn in ("TypeExprRefSelf", "ExprRefLocal"):
        return True
    if cn == "ExprAttribute":
        return _plain_path(e.value)
    return False


def check(comps, ctor_names=None, tm=None, table=None) -> List[str]:
    """One diagnostic per register group that nothing binds. *table* is the
    model's `bindings` when the caller has it (``OpModel.bindings``)."""
    errs = []
    table = table if table is not None else bindings(comps, ctor_names, tm)
    for c in comps:
        name = (getattr(c, "name", None) or "?").split("::")[-1]
        fields = {f.name: f for f in getattr(c, "fields", None) or []}
        for g in table[id(c)].unbound:
            errs.append(at(
                (fields.get(g), c),
                f"{name}: register group `{g}` is never bound: no "
                f"`{g}.set_handle(...)` reaches it, so its registers have no "
                f"address. Bind it in `{name}`'s constructor (or from an "
                f"enclosing component's, as `<instance>.{g}.set_handle(...)`)."))
    return errs


# --- the walk ---------------------------------------------------------------

def _set_handle_paths(fn) -> Iterator[Tuple[str, ...]]:
    """The self-rooted field path of every `<path>.set_handle(...)` in *fn*,
    array indices dropped: `a.set_handle` -> ("a",), `s[i].a.set_handle` ->
    ("s", "a")."""
    for call in _walk(getattr(fn, "body", None)):
        if _dt_name(call) != "ExprCall":
            continue
        func = call.func
        if _dt_name(func) != "ExprAttribute" or func.attr != "set_handle":
            continue
        path = _path(func.value)
        if path:
            yield path


def _path(e) -> Optional[Tuple[str, ...]]:
    cn = _dt_name(e)
    if cn == "TypeExprRefSelf":
        return ()
    if cn == "ExprAttribute":
        b = _path(e.value)
        return None if b is None else b + (e.attr,)
    if cn == "ExprSubscript":
        return _path(e.value)
    if cn == "ExprRefLocal":
        # `self.x` reaches the IR as a name (see AGENTS.md); a parameter of
        # the same name would shadow it, but a parameter is never a group.
        return (e.name,)
    return None


def _walk(node) -> Iterator[Any]:
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _walk(n)
        return
    if not dc.is_dataclass(node) or isinstance(node, type):
        return
    yield node
    for c in children(node):
        yield from _walk(c)


def _ctor(comp, ctor_names):
    for fn in getattr(comp, "functions", None) or []:
        if func_kind(fn, ctor_names) == FuncKind.CONSTRUCTOR:
            return fn
    return None


def _first_addr_arg(ctor, tm) -> Optional[str]:
    """The constructor's first `addr_handle_t` parameter (a chandle typedef;
    older standard libraries declared it as a placeholder struct)."""
    if ctor is None:
        return None
    for a in getattr(getattr(ctor, "args", None), "args", None) or []:
        dt = a.annotation
        ref = getattr(dt, "ref_name", None)
        if ref and ref in tm:
            dt = tm[ref]
        cn = _dt_name(dt)
        if cn == "DataTypeChandle" or (
                cn == "DataTypeStruct"
                and dt.name.split("::")[-1] == "addr_handle_t"):
            return a.arg
    return None


def _resolve(dtype, tm, by_name):
    ref = getattr(dtype, "ref_name", None)
    if ref and ref in tm:
        dtype = tm[ref]
    nm = getattr(dtype, "name", None)
    return by_name.get(nm, dtype)
