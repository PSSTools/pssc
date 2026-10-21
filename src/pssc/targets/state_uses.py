"""What state an operation touches, transitively (PC-3).

Language-neutral, like `reg_layout` and `group_binding`: it reads the IR and
the register model, and says nothing about how a backend spells an access. A
plug-in asks it to decide an API -- which register groups an operation needs a
handle to, whether it keeps component state, whether it may block -- without
parsing generated code or re-walking bodies with a recognizer of its own.

Three things here are the one place they are decided:

* :func:`resolve_reg_path` -- which register a self-rooted path names, through
  sub-components and nested groups; `reserved_access` asks it too, and the
  accessor is the one `reg_layout.collect_accessors` produced, never a second
  computation of an offset;
* :func:`callees` -- the model's call graph (`field_writes.target_code` and
  `error_strings` use it);
* :func:`state_uses` -- the transitive summary.

Paths are instance paths from the component the operation belongs to, indices
dropped: ``("regs", "channels", "CSR")`` is every element of ``channels``.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

from .call_legality import COMMON, Disposition, REG_DIRECTION, core_function
from .group_binding import _path, _resolve
from .ir_walk import children
from .progseq_model import (_dt_name, array_element_type, field_is_array,
                            is_reg_group, sub_components)
from .reg_layout import collect_accessors, is_reserved

__all__ = ["Call", "RegRef", "StateUses", "callees", "resolve_reg_path",
           "state_uses"]

Path = Tuple[str, ...]


@dc.dataclass(frozen=True)
class RegRef:
    """What a self-rooted path names in the register model.

    ``sub_path`` is the sub-component instances crossed, ``group`` the
    register-group field of the last of them (or of the component itself),
    and ``reg_segs`` the nested groups and the register inside it. ``accessor``
    is the register's `reg_layout.RegAccessor`, relative to the component
    that declares ``group``; ``None`` when the path ends at a group rather
    than a register. ``reserved`` is the prefix of the path that ends at a
    reserved member (`reg_layout.is_reserved`), or ``None``."""
    sub_path: Path
    group: str
    reg_segs: Path
    accessor: Any = dc.field(default=None, compare=False)
    reserved: Optional[Path] = None
    #: The component that declares ``group`` (``sub_path``'s last instance's
    #: type, resolved), which ``accessor`` is relative to.
    owner: Any = dc.field(default=None, compare=False)

    @property
    def group_path(self) -> Path:
        return self.sub_path + (self.group,)

    @property
    def path(self) -> Path:
        return self.group_path + self.reg_segs


def resolve_reg_path(comp, path: Path, tm=None, by_name=None,
                     accessors=None) -> Optional[RegRef]:
    """The register (or register group) the self-rooted ``path`` names in
    ``comp``, or ``None`` when it names something else (a channel, a plain
    field, a name the component does not have).

    ``accessors(owner)`` gives a component's `collect_accessors`; a caller
    resolving many paths passes a memoised one."""
    tm = tm or {}
    by_name = by_name or {}
    dt = comp
    sub_path: List[str] = []
    for k, seg in enumerate(path):
        subs = {s.name: s.dtype for s in sub_components(dt)}
        if seg in subs:
            dt = _resolve(subs[seg], tm, by_name)
            sub_path.append(seg)
            continue
        f = _field(dt, seg)
        if f is None:
            return None
        ft = array_element_type(f) if field_is_array(f) else f.datatype
        if not is_reg_group(ft):
            return None
        return _in_group(dt, tuple(sub_path), seg, ft, tuple(path[k + 1:]),
                         tuple(path[:k + 1]), accessors or collect_accessors)
    return None


def _in_group(owner, sub_path, group, gdt, rest, prefix,
              accessors=collect_accessors) -> RegRef:
    reserved = None
    dt, segs = gdt, []
    for seg in rest:
        f = _field(dt, seg)
        if f is None:
            break
        segs.append(seg)
        if is_reserved(f) and reserved is None:
            reserved = prefix + tuple(segs)
        ft = array_element_type(f) if field_is_array(f) else f.datatype
        if not is_reg_group(ft):
            break                            # a register: the rest is a method
        dt = ft
    acc = None
    if segs:
        full = (group,) + tuple(segs)
        acc = next((a for a in accessors(owner) if a.path == full), None)
    return RegRef(sub_path, group, tuple(segs), acc, reserved, owner)


def _field(dt, name):
    return next((x for x in getattr(dt, "fields", None) or []
                 if x.name == name), None)


# --- the call graph -----------------------------------------------------------

@dc.dataclass(frozen=True)
class Call:
    """One call an operation makes into the model.

    ``kind`` is ``"member"`` (a function of a component: ``owner`` is its
    type and ``path`` the instance path from the caller's component, ``()``
    for its own), ``"package"`` (a package function; ``owner`` ``None``) or
    ``"import"`` (an imported function, which has no body here)."""
    kind: str
    function: Any = dc.field(compare=False)
    owner: Any = dc.field(default=None, compare=False)
    path: Path = ()
    call: Any = dc.field(default=None, compare=False)


def _fns(c) -> Dict[str, Any]:
    return {f.name: f for f in getattr(c, "functions", None) or []}


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


def _params(fn) -> Set[str]:
    return {a.arg for a in getattr(getattr(fn, "args", None), "args", None) or []}


def callees(fn, comp, ctx) -> List[Call]:
    """Every call ``fn`` (a function of ``comp``) makes to a function of the
    model, in body order: member functions (its own, a sub-component's, a
    base's private `_pss_super_*` copy), package functions and imports."""
    from . import pkg_functions as pf
    from .expr_types import ExprTypes

    pkg = pf.declared(ctx) if ctx is not None else {}
    imports = {f.name: f for f in getattr(ctx, "import_functions", None) or []}
    imports.update({(f.metadata or {}).get("qualified_name", f.name): f
                    for f in getattr(ctx, "import_functions", None) or []})
    types = None
    out: List[Call] = []
    for n in _walk(getattr(fn, "body", None)):
        if _dt_name(n) != "ExprCall":
            continue
        f = n.func
        if _dt_name(f) == "ExprAttribute":
            path = _path(f.value)
            if _dt_name(f.value) == "TypeExprRefSelf":
                owner, ipath = comp, ()
            elif path and path[0] not in _params(fn) \
                    and _sub_at(comp, path, ctx) is not None:
                owner, ipath = _sub_at(comp, path, ctx), path
            else:
                types = types or ExprTypes(fn, comp, ctx)
                t = types.type_of(f.value)
                owner = getattr(t, "dtype", None) if t is not None else None
                owner = types.resolve(owner) if owner is not None else None
                ipath = path or ()
            if owner is not None and (_dt_name(owner) == "DataTypeRegister"
                                      or is_reg_group(owner)):
                continue        # a register access: core library, not model
            callee = _fns(owner).get(f.attr) if owner is not None else None
            if callee is not None:
                out.append(Call("member", callee, owner, tuple(ipath), n))
            elif owner is comp and f.attr in imports:
                # `self.f()` is a member call only if the component declares
                # `f` (validate_calls.ops_for_call); an import is written so.
                out.append(Call("import", imports[f.attr], None, (), n))
        elif _dt_name(f) == "ExprRefLocal":
            callee = _fns(comp).get(f.name)
            if callee is not None:
                out.append(Call("member", callee, comp, (), n))
        else:
            target = pf.callee(n, pkg) if pkg else None
            if target is not None:
                out.append(Call("package", target, None, (), n))
                continue
            name = getattr(f, "name", None)
            if name in imports:
                out.append(Call("import", imports[name], None, (), n))
    return out


def _sub_at(comp, path: Path, ctx):
    """The component type at instance ``path`` (sub-components only)."""
    tm = getattr(ctx, "type_map", None) or {}
    dt = comp
    for seg in path:
        subs = {s.name: s.dtype for s in sub_components(dt)}
        if seg not in subs:
            return None
        dt = _resolve(subs[seg], tm, {})
    return dt


# --- the summary --------------------------------------------------------------

@dc.dataclass(frozen=True)
class StateUses:
    """What an operation touches, its callees' uses included.

    Register paths are instance paths (indices dropped) from the operation's
    component. ``fields_*`` are COMPONENT data fields (``self.count``), as
    ``("sub", "count")`` for a sub-component's; ``reg_fields_written`` are
    register fields a masked write names (from its provenance, PC-4), as
    ``(register path, field)``. ``unbounded_loops`` holds each loop whose
    trip count is not known before it starts (a `while`, a `repeat ...
    while`); a `repeat (n)` or `foreach` is bounded even when ``n`` is a
    run-time value."""
    groups: FrozenSet[Path] = frozenset()
    regs_read: FrozenSet[Path] = frozenset()
    regs_written: FrozenSet[Path] = frozenset()
    reg_fields_written: FrozenSet[Tuple[Path, str]] = frozenset()
    fields_read: FrozenSet[Path] = frozenset()
    fields_written: FrozenSet[Path] = frozenset()
    mem_accessed: bool = False
    imports_called: FrozenSet[str] = frozenset()
    callees: Tuple[Call, ...] = ()
    unbounded_loops: Tuple[Any, ...] = dc.field(default=(), compare=False)
    #: Set by ``OpModel.state_uses``: the model has an executor, so every
    #: register and memory access is delegated to it (`executors`).
    executor_delegated: bool = False

    @property
    def stateless(self) -> bool:
        """Touches no component field and calls no import."""
        return not (self.fields_read or self.fields_written
                    or self.imports_called)


_LOOPS_UNBOUNDED = ("StmtWhile", "StmtRepeatWhile", "StmtDoWhile")


def state_uses(fn, comp, ctx) -> StateUses:
    """:class:`StateUses` of ``fn``, a function of ``comp``. Recursion-safe:
    each (function, instance path) is visited once."""
    acc = _Acc()
    _visit(fn, comp, ctx, (), acc, set(), top=True)
    return StateUses(
        groups=frozenset(acc.groups), regs_read=frozenset(acc.r),
        regs_written=frozenset(acc.w),
        reg_fields_written=frozenset(acc.rf),
        fields_read=frozenset(acc.fr), fields_written=frozenset(acc.fw),
        mem_accessed=acc.mem, imports_called=frozenset(acc.imports),
        callees=tuple(acc.callees), unbounded_loops=tuple(acc.loops))


class _Acc:
    def __init__(self):
        self.groups: Set[Path] = set()
        self.r: Set[Path] = set()
        self.w: Set[Path] = set()
        self.rf: Set[Tuple[Path, str]] = set()
        self.fr: Set[Path] = set()
        self.fw: Set[Path] = set()
        self.mem = False
        self.imports: Set[str] = set()
        self.callees: List[Call] = []
        self.loops: List[Any] = []


def _data_fields(comp) -> Set[str]:
    subs = {s.name for s in sub_components(comp)}
    out = set()
    for f in getattr(comp, "fields", None) or []:
        ft = array_element_type(f) if field_is_array(f) else f.datatype
        if f.name in subs or is_reg_group(ft) \
                or _dt_name(ft) == "DataTypeChannel":
            continue
        out.add(f.name)
    return out


def _visit(fn, comp, ctx, prefix: Path, acc: _Acc, seen, top=False):
    key = (id(fn), prefix)
    if key in seen:
        return
    seen.add(key)
    from .reg_field_names import written_fields

    tm = getattr(ctx, "type_map", None) or {}
    params = _params(fn)
    data = _data_fields(comp) if comp is not None else set()
    written_ids: Set[int] = set()

    for n in _walk(getattr(fn, "body", None)):
        cn = _dt_name(n)
        if cn in _LOOPS_UNBOUNDED:
            acc.loops.append(n)
        elif cn in ("StmtAssign", "StmtAugAssign", "StmtAnnAssign"):
            targets = getattr(n, "targets", None) or [getattr(n, "target", None)]
            for t in targets:
                root = _field_root(t, params, data)
                if root is not None and cn != "StmtAnnAssign":
                    acc.fw.add(prefix + root)
                    # The target's own `self.x` is a write, not a read.
                    e = t
                    while _dt_name(e) in ("ExprSubscript", "ExprAttribute"):
                        written_ids.add(id(e))
                        e = e.value
        elif cn == "ExprCall" and comp is not None:
            f = n.func
            if _dt_name(f) != "ExprAttribute":
                name = getattr(f, "name", None) or ""
                e = COMMON.get(core_function(name) or name)
                if e is not None and e.disposition is Disposition.MEM:
                    acc.mem = True
                continue
            m = f.attr
            if m in REG_DIRECTION:
                path = _path(f.value)
                if path and path[0] not in params:
                    ref = resolve_reg_path(comp, path, tm)
                    if ref is not None and ref.reg_segs:
                        p = prefix + ref.path
                        acc.groups.add(prefix + ref.group_path)
                        d = REG_DIRECTION[m]
                        if "r" in d:
                            acc.r.add(p)
                        if "w" in d:
                            acc.w.add(p)
                        for w in written_fields(n) or ():
                            acc.rf.add((p, w.slice.name))
            elif COMMON.get(m) is not None \
                    and COMMON[m].disposition is Disposition.MEM:
                acc.mem = True
        elif cn == "ExprAttribute" and comp is not None and id(n) not in written_ids:
            if _dt_name(n.value) == "TypeExprRefSelf" and n.attr in data \
                    and n.attr not in params:
                acc.fr.add(prefix + (n.attr,))

    if comp is None:
        return
    for c in callees(fn, comp, ctx):
        acc.callees.append(dc.replace(c, path=prefix + c.path)
                           if c.kind == "member" else c)
        if c.kind == "import":
            acc.imports.add(c.function.name)
        elif c.kind == "package":
            _visit_pkg(c.function, ctx, acc, seen)
        else:
            _visit(c.function, c.owner, ctx, prefix + c.path, acc, seen)


def _visit_pkg(fn, ctx, acc, seen):
    """A package function: no component state of its own, but it may call
    imports and memory primitives, and other package functions."""
    key = (id(fn), None)
    if key in seen:
        return
    seen.add(key)
    from . import pkg_functions as pf
    pkg = pf.declared(ctx)
    imports = {f.name for f in getattr(ctx, "import_functions", None) or []}
    for n in _walk(getattr(fn, "body", None)):
        cn = _dt_name(n)
        if cn in _LOOPS_UNBOUNDED:
            acc.loops.append(n)
        if cn != "ExprCall":
            continue
        name = getattr(n.func, "name", None) or getattr(n.func, "attr", None) \
            or ""
        e = COMMON.get(core_function(name) or name)
        if e is not None and e.disposition is Disposition.MEM:
            acc.mem = True
        target = pf.callee(n, pkg) if pkg else None
        if target is not None:
            _visit_pkg(target, ctx, acc, seen)
        elif name in imports:
            acc.imports.add(name)


def _field_root(t, params, data) -> Optional[Path]:
    """``(name,)`` if the assignment target ``t`` is rooted at a component
    data field (``self.x``, ``self.x[i]``, ``self.s.f``)."""
    e = t
    while _dt_name(e) in ("ExprSubscript", "ExprAttribute"):
        if _dt_name(e) == "ExprAttribute" and _dt_name(e.value) == "TypeExprRefSelf":
            if e.attr in params or e.attr not in data:
                return None
            return (e.attr,)
        e = e.value
    return None
