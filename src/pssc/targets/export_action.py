"""Exported actions: the entry points of an operation model.

An operation model's API is its components' functions, and a PSS tool that does
not generate operation models has no way to call one. Every PSS tool can run an
action. So a test of what an operation model uses is ordinary PSS: an action
whose `exec body` calls the component's functions. The same test runs on an
operation model when the action is identified as an entry point with the
`--export-action` tool directive (the LRM's exported action, 20.10, with no
parameters). There is one testcase for every tool, and none of it is specific to
operation models.

An entry is an ACTION, and is kept as one. The IR is not changed: the component
does not gain a function it never declared. `OpModel.entries` records each entry
beside the component it runs in, and a backend renders it as a task of that
component with no parameters and no result.

To render an entry, a backend needs its `exec body` in the scope of its
component, because that is where its code runs. Inside the action the component
is reached through `comp` (`comp.eng.run(3)`); inside the component it is the
object itself (`eng.run(3)`). `EntryPoint.function` is that view: the same
statements with the `comp.` step removed, in the form a component function
already has, so each backend's body walker renders it with nothing new to learn.

In scope: an atomic action whose `exec body` is procedural code over locals and
its component. Refused, each with a diagnostic naming the action: an activity
(an operation model has no scheduler), `rand` attributes (no solver), reads of
any other action attribute (an entry takes no parameters, so there is nothing to
bind one to), and `pre_solve`/`post_solve`.

Action inheritance (17.1, 20.1.4). A derived action with no `exec body` runs
its base's; one with an `exec body` shadows the base's, and `super;` in it runs
the base's body at that point. Each base body reached that way is rendered as a
function of its own (`EntryPoint.supers`) -- its locals and a `return` in it are
its own, which splicing its statements in would not keep -- and the `super;`
that reaches it names it (`metadata["super"]`, None when no base declares a
body, so the statement does nothing). What an action inherits is checked like
what it declares: a base's activity, `rand` attributes or solve execs refuse
the entry.

Parameters are future work. An exported action's parameters are associated by
name with its fields (20.10.1), so when entries take them, a parameter becomes
an argument of `EntryPoint.function` and the read of that field becomes a read
of the argument -- at exactly the place `_InComponentScope` now refuses it.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional, Sequence, Tuple

import zuspec.ir.core as ir


class ExportActionError(ValueError):
    """An `--export-action` that cannot be an operation-model entry point."""


@dc.dataclass(frozen=True)
class EntryPoint:
    """One exported action, as an entry point of the operation model."""

    #: The action's qualified name (`pss_top::A`).
    action: str
    #: The action's datatype, as translated.
    action_dtype: Any
    #: The datatype of the component the action runs in. The entry is rendered
    #: as a task of this component.
    comp: Any
    #: The entry's name in the generated API: the action's unqualified name.
    name: str
    #: The `exec body` in its component's scope: a no-argument, no-result
    #: function whose statements are the action's with `comp.` removed. A view
    #: for rendering, owned by this object and in no component's `functions`.
    function: Any
    #: The base actions' bodies that `super;` reaches, nearest base first, each
    #: rendered as a private function of the component (`SUPER_PREFIX`).
    supers: Tuple[Any, ...] = ()

    @property
    def functions(self) -> Tuple[Any, ...]:
        """Every function the entry renders: the entry itself, then `supers`."""
        return (self.function,) + tuple(self.supers)


#: Name prefix of a base action body's function. `_pss_` names are the
#: generator's own; no PSS identifier the model declares is rendered with it.
SUPER_PREFIX = "_pss_super_"


#: Solve-time execs. An entry is not solved, so it cannot run them.
_SOLVE_EXECS = ("pre_solve", "post_solve")


def resolve_action(ctx, name: str) -> Tuple[str, Any]:
    """``(qualified name, datatype)`` of the action ``name``.

    A bare name must be unambiguous. An action is recognised by having a
    context component (`ctx.parent_comp_names`), which is the one thing an
    entry cannot do without.
    """
    tm = getattr(ctx, "type_map", {}) or {}
    parents = getattr(ctx, "parent_comp_names", {}) or {}
    if name in parents:
        return name, tm[name]
    cands = sorted(q for q in parents if q == name or q.endswith("::" + name))
    if len(cands) == 1:
        return cands[0], tm[cands[0]]
    if cands:
        raise ExportActionError(
            f"ambiguous --export-action '{name}'; matches: {', '.join(cands)}")
    raise ExportActionError(
        f"unknown --export-action '{name}'; actions: "
        + (", ".join(sorted(parents)) or "(none)"))


def context_component(ctx, qname: str):
    """The datatype of the component action ``qname`` runs in."""
    comp_q = (getattr(ctx, "parent_comp_names", {}) or {}).get(qname)
    comp = (getattr(ctx, "type_map", {}) or {}).get(comp_q)
    if comp is None:
        raise ExportActionError(
            f"action '{qname}' has no context component in the type table")
    return comp


def _is_self_attr(e, attr: Optional[str] = None) -> bool:
    return (isinstance(e, ir.ExprAttribute)
            and isinstance(e.value, ir.TypeExprRefSelf)
            and (attr is None or e.attr == attr))


class _InComponentScope:
    """Copy an action's statements into its component's scope: `comp.X` -> `X`.

    Any other action attribute is refused. Returns new nodes where something
    changed and leaves the action's own untouched.

    A DATATYPE is never copied. The statements refer to types -- a local's
    declared struct, a cast's target -- and a type is one shared definition:
    a copy would be a second `params_s` that is not the first, and everything
    downstream that asks "is this the same type" would say no.
    """

    def __init__(self, qname: str, fields: Sequence[str]):
        self.qname = qname
        self.fields = frozenset(fields)

    def __call__(self, node):
        if isinstance(node, list):
            new = [self(n) for n in node]
            return node if all(a is b for a, b in zip(new, node)) else new
        if isinstance(node, tuple):
            new = tuple(self(n) for n in node)
            return node if all(a is b for a, b in zip(new, node)) else new
        if isinstance(node, dict):
            new = {k: self(v) for k, v in node.items()}
            return node if all(new[k] is v for k, v in node.items()) else new
        if not dc.is_dataclass(node) or isinstance(node, (type, ir.DataType)):
            return node
        if _is_self_attr(node, "comp"):
            return ir.TypeExprRefSelf(loc=node.loc)
        if _is_self_attr(node) and node.attr in self.fields:
            raise ExportActionError(
                f"action '{self.qname}' reads its attribute '{node.attr}'; an "
                f"operation-model entry takes no parameters, so an action "
                f"attribute has nothing to be bound to. Use a local, or a "
                f"component attribute")
        changes = {}
        for f in dc.fields(node):
            v = getattr(node, f.name)
            nv = self(v)
            if nv is not v:
                changes[f.name] = nv
        return dc.replace(node, **changes) if changes else node


def _check_atomic(qname: str, act, via: str = "") -> None:
    """Refuse what an entry cannot run. ``via`` names the derived action when
    ``act`` is one of its bases: what a base declares, the entry inherits."""
    who = f"action '{qname}'" + (f" (a base of '{via}')" if via else "")
    if getattr(act, "activity_ir", None) is not None:
        raise ExportActionError(
            f"{who} has an activity; an operation-model entry is an "
            f"atomic action's `exec body` (an operation model has no scheduler)")
    rand = [f.name for f in (getattr(act, "fields", None) or [])
            if getattr(f, "rand_kind", None) not in (None, "", 0)]
    if rand:
        raise ExportActionError(
            f"{who} has rand attributes ({', '.join(rand)}); an "
            f"operation-model entry is not randomized (there is no solver)")
    solve = [fn.name for fn in (getattr(act, "functions", None) or [])
             if fn.name in _SOLVE_EXECS and fn.body]
    if solve:
        raise ExportActionError(
            f"{who} has exec {', '.join(solve)}; an operation-model "
            f"entry runs only its `exec body`")


def _chain(ctx, qname: str, act) -> List[Tuple[str, Any]]:
    """``[(qualified name, datatype)]`` from the action to its root base.

    The base is the one the front end recorded as the linker resolved it; a
    name that is not an action of the model is refused, never guessed at.
    """
    tm = getattr(ctx, "type_map", {}) or {}
    parents = getattr(ctx, "parent_comp_names", {}) or {}
    out = [(qname, act)]
    while True:
        q, a = out[-1]
        sup = getattr(a, "super", None)
        if sup is None:
            return out
        name = getattr(sup, "ref_name", None) or getattr(sup, "name", None)
        base = tm.get(name) if name else None
        if base is None or name not in parents:
            raise ExportActionError(
                f"action '{q}' inherits from '{name}', which is not an action "
                f"of the model")
        if any(base is b for _, b in out):
            raise ExportActionError(
                f"action '{qname}' inherits from itself (through '{name}')")
        out.append((name, base))


def _body_of(act):
    """The action's own `exec body`, or None if it declares none."""
    return next((fn for fn in (getattr(act, "functions", None) or [])
                 if fn.name == "body"), None)


def _has_super(node) -> bool:
    """Does ``node`` hold a `super;` statement, at any depth?"""
    if isinstance(node, (list, tuple)):
        return any(_has_super(n) for n in node)
    if isinstance(node, ir.StmtSuper):
        return True
    if not dc.is_dataclass(node) or isinstance(node, (type, ir.DataType)):
        return False
    return any(_has_super(getattr(node, f.name)) for f in dc.fields(node)
               if isinstance(getattr(node, f.name), (list, tuple, ir.Stmt)))


def _component_bases(ctx, comp) -> List[Any]:
    """``comp`` and every component it inherits from, nearest first."""
    tm = getattr(ctx, "type_map", {}) or {}
    out = [comp]
    while True:
        sup = getattr(out[-1], "super", None)
        name = getattr(sup, "ref_name", None) if sup is not None else None
        base = tm.get(name) if name else None
        if base is None or any(base is b for b in out):
            return out
        out.append(base)


def entry_point(ctx, name: str) -> EntryPoint:
    """The entry point for the action ``name``. Raises `ExportActionError`."""
    qname, act = resolve_action(ctx, name)
    _check_atomic(qname, act)
    comp = context_component(ctx, qname)
    entry = qname.split("::")[-1]
    clash = next((fn for fn in (getattr(comp, "functions", None) or [])
                  if fn.name == entry), None)
    if clash is not None:
        raise ExportActionError(
            f"--export-action '{qname}': its component "
            f"'{getattr(comp, 'name', '?')}' already has a function named "
            f"'{entry}', and both would be the same member of the generated API")
    chain = _chain(ctx, qname, act)
    for q, a in chain[1:]:
        _check_atomic(q, a, via=qname)
    # A base action runs in its own context component. The entry runs in the
    # derived action's, which has to BE that component or inherit from it.
    runs_in = _component_bases(ctx, comp)
    for q, _ in chain[1:]:
        base_comp = context_component(ctx, q)
        if not any(base_comp is c for c in runs_in):
            raise ExportActionError(
                f"--export-action '{qname}': its base action '{q}' runs in "
                f"component '{getattr(base_comp, 'name', '?')}', which "
                f"'{getattr(comp, 'name', '?')}' neither is nor inherits from")
    # An action sees every attribute along its chain.
    fields = [f.name for _, a in chain
              for f in (getattr(a, "fields", None) or []) if f.name != "comp"]
    scope = _InComponentScope(qname, fields)
    # Only actions that DECLARE a body are levels: one that declares none runs
    # its base's, so `super;` above it reaches the next body down.
    levels = [(q, b) for q, b in ((q, _body_of(a)) for q, a in chain)
              if b is not None]
    if not levels or levels[0][0] != qname:
        levels.insert(0, (qname, None))

    def level_fn(k: int, name: str, super_name: Optional[str]):
        q, body = levels[k]
        stmts = list(body.body) if body is not None and body.body else []
        md = {"export_action": qname}
        if k:
            md["super_of"] = q
        if _has_super(stmts):
            md["super"] = super_name
        return ir.Function(
            loc=getattr(body, "loc", None) or getattr(act, "loc", None),
            name=name, args=ir.Arguments(args=[]), body=scope(stmts),
            returns=None, is_async=False, metadata=md), bool(md.get("super"))

    if levels[0][1] is None and len(levels) > 1:
        # No body of its own: the entry IS the nearest base body.
        levels.pop(0)
    fns: List[Any] = []
    k = 0
    while True:
        nxt = (f"{SUPER_PREFIX}{entry}_{k + 1}" if k + 1 < len(levels)
               else None)
        fn, calls_next = level_fn(k, entry if k == 0 else
                                  f"{SUPER_PREFIX}{entry}_{k}", nxt)
        fns.append(fn)
        if not calls_next:
            break
        k += 1
    return EntryPoint(action=qname, action_dtype=act, comp=comp, name=entry,
                      function=fns[0], supers=tuple(fns[1:]))


def entry_points(ctx, names: Sequence[str]) -> Tuple[EntryPoint, ...]:
    """Every `--export-action`, in the order given. Two entries may not share a
    component and a name."""
    out: List[EntryPoint] = []
    for name in names or []:
        ep = entry_point(ctx, name)
        if any(e.action == ep.action for e in out):
            continue
        dup = next((e for e in out if e.comp is ep.comp and e.name == ep.name),
                   None)
        if dup is not None:
            raise ExportActionError(
                f"--export-action '{ep.action}' and '{dup.action}' would both "
                f"be '{ep.name}' on the same component")
        out.append(ep)
    return tuple(out)
