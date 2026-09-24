"""Component inheritance in an operation model (LRM 17.1, Table 27).

"A derived type includes all elements from the base type" (17.1). The IR keeps a
type's OWN members and names its base (`super`), which is right for a target
that renders inheritance as inheritance -- the SV testbench `extends` its base
class. Every operation-model consumer, though, walks a component's own
`fields` and `functions`. So a derived component used to lose, silently,
everything it inherits: `component der_c : base_c` lowered without base_c's
fields, sub-components, register blocks and functions.

`complete(ctx)` gives each derived user component its inherited members, once
per translation and before anything reads the tree:

* Fields: the base's first, then its own (the order `struct_fields` uses for
  structs). A field is NOT virtual (17.1): when the derived component declares
  one of the same name, the base's copy is kept under a private name
  (`_pss_super_<base>_<name>`), and the base's bodies -- and `super.<name>` in
  the derived component's -- read that one. Shadowing a component INSTANCE is
  refused (its registers and sub-tree would need two names).
* Functions are virtual (a base function calling `f()` runs the derived
  override). A base function the component does not shadow is COPIED into it,
  and its body is then rendered in the derived component like one of its own:
  its `f()` reaches the derived `f`, on every target, with no dispatch table.
  A shadowing function whose signature differs from the base's is refused when
  an inherited body calls it -- that call was written against the base's.
* `super.f(args)` calls the BASE's `f`, statically: a private copy of it
  (`_pss_super_<base>_f`, `metadata["super_impl"]`) rendered in the derived
  component, so calls inside it stay virtual. That covers an executor's
  `super.read32(...)` reaching a user base executor's override too; one that
  reaches no user base is left to the backend (the default implementation).
* Exec blocks shadow per kind (Table 27): a kind the component does not
  declare is copied from the base; one it declares replaces the base's, and
  `super;` in it runs private copies of the base's blocks of that kind
  (`metadata["super_block"]`), listed in the block's `metadata["super"]`.

Every copy of a base member comes through one place (`from_base`), which
redirects the base's reads of a field the derived component shadows.

Only user components take part. A register group or register keeps its own
treatment, and a core-library base (`executor_c<>`, `reg_group_c`, ...) carries
no members to inherit.

The completion is IN PLACE on the translated types. That is deliberate: the
consumers identify a component by object (`EntryPoint.comp is dtype`, the
executor base walk, the tree), so a completed COPY would be a second `der_c`
that is not the first. A translation feeds one target, and the completion
runs once per translation (`ctx._pss_comp_inherit`).

A component that cannot be completed is recorded, not raised on: the model
may never instantiate it. `problems(ctx, comps)` reports the ones in a tree.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import zuspec.ir.core as ir

from .call_legality import CORE_PACKAGES
from .progseq_model import _dt_name, exec_kind, is_reg_group, is_register

#: Marks a member copied from a base: ``metadata["inherited_from"]`` names the
#: component whose source declares it.
INHERITED_FROM = "inherited_from"
#: A private copy of a base's function that `super.f(...)` calls:
#: ``metadata["super_impl"]`` is ``"<base>::<f>"``.
SUPER_IMPL = "super_impl"
#: A private copy of a base's exec block that `super;` runs:
#: ``metadata["super_block"]`` is its kind. Not run by the component itself.
SUPER_BLOCK = "super_block"
#: Name prefix of every private copy. `_pss_` names are the generator's own.
SUPER_PREFIX = "_pss_super_"


def _is_core(dtype) -> bool:
    name = getattr(dtype, "name", None) or ""
    return name.split("::", 1)[0] in CORE_PACKAGES and "::" in name


def user_base(ctx, comp) -> Optional[Any]:
    """``comp``'s base component, if it is a user component that inherits
    members; else None. ``ctx`` is a translation context or its type map."""
    tm = ctx if isinstance(ctx, dict) else (getattr(ctx, "type_map", None) or {})
    sup = getattr(comp, "super", None)
    name = getattr(sup, "ref_name", None) if sup is not None else None
    base = tm.get(name) if name else None
    if (base is None or _dt_name(base) != "DataTypeComponent"
            or is_reg_group(base) or is_register(base) or _is_core(base)):
        return None
    return base


def _name(dtype) -> str:
    return getattr(dtype, "name", None) or "?"


def _signature(fn):
    args = tuple(getattr(a, "annotation", None)
                 for a in (getattr(getattr(fn, "args", None), "args", None) or []))
    return args, getattr(fn, "returns", None)


def _self_calls(node, out: Set[str]) -> None:
    """Names called as `self.<name>(...)` anywhere under ``node``."""
    if isinstance(node, (list, tuple)):
        for n in node:
            _self_calls(n, out)
        return
    if not dc.is_dataclass(node) or isinstance(node, (type, ir.DataType)):
        return
    if isinstance(node, ir.ExprCall):
        f = node.func
        if (isinstance(f, ir.ExprAttribute)
                and isinstance(f.value, ir.TypeExprRefSelf)):
            out.add(f.attr)
    for fl in dc.fields(node):
        _self_calls(getattr(node, fl.name), out)


def _copy(member, base):
    md = dict(getattr(member, "metadata", None) or {})
    md.setdefault(INHERITED_FROM, _name(base))
    if hasattr(member, "metadata"):
        return dc.replace(member, metadata=md)
    return dc.replace(member)


def complete(ctx) -> None:
    """Give every derived user component its inherited members. Idempotent."""
    if getattr(ctx, "_pss_comp_inherit", None) is not None:
        return
    found: Dict[int, str] = {}
    ctx._pss_comp_inherit = found
    ctx._pss_declared = {}
    ctx._pss_bases = set()
    done: Set[int] = set()
    seen: Set[int] = set()
    for comp in list((getattr(ctx, "type_map", None) or {}).values()):
        if id(comp) in seen or _dt_name(comp) != "DataTypeComponent":
            continue
        seen.add(id(comp))
        _complete(ctx, comp, done, found, ())


def _complete(ctx, comp, done: Set[int], found: Dict[int, str], stack) -> None:
    if id(comp) in done:
        return
    base = user_base(ctx, comp)
    if base is None:
        done.add(id(comp))
        if getattr(comp, "super", None) is not None:
            # A core-library base declares no exec blocks: `super;` in one of
            # this component's runs nothing.
            _resolve_block_supers(comp, None, None)
        return
    if any(base is s for s in stack) or base is comp:
        done.add(id(comp))
        found[id(comp)] = f"'{_name(comp)}' inherits from itself"
        return
    _complete(ctx, base, done, found, stack + (comp,))
    done.add(id(comp))
    if id(base) in found:
        found[id(comp)] = (f"'{_name(comp)}' inherits from '{_name(base)}': "
                           f"{found[id(base)]}")
        return
    why = _completion_problem(comp, base)
    if why is not None:
        found[id(comp)] = why
        return

    # -- fields: the base's first; a shadowed one under a private name --------
    ctx._pss_declared[id(comp)] = Declared(
        base=base, fields=tuple(comp.fields or ()),
        functions=tuple(comp.functions or ()))
    ctx._pss_bases.add(id(base))
    own_fields = list(comp.fields or [])
    own_names = {f.name for f in own_fields}
    hidden_field: Dict[str, str] = {}
    fields = []
    for f in (base.fields or []):
        if f.name in own_names:
            hidden_field[f.name] = _hidden(base, f.name)
            fields.append(dc.replace(f, name=hidden_field[f.name]))
        else:
            fields.append(dc.replace(f))
    # A base field's initializer reads the base's fields.
    fields = [dc.replace(f, initial_value=_rewrite(
        f.initial_value, self_fields=hidden_field)) if hidden_field else f
        for f in fields]
    comp.fields = fields + own_fields

    # -- functions: virtual; what the component does not shadow is copied ----
    own = list(comp.functions or [])
    own_fn = {fn.name for fn in own if not _is_block(fn)}
    own_exec = {exec_kind(fn) for fn in own if _is_block(fn)}
    def from_base(fn):
        """A copy of the base's ``fn`` for this component. EVERY copy comes
        through here: a base body reads the base's fields, so one the
        component shadows is read under its private name."""
        cp = _copy(fn, base)
        if hidden_field:
            cp.body = _rewrite(cp.body, self_fields=hidden_field,
                               bound=_params(fn))
        return cp

    inherited = [from_base(fn) for fn in (base.functions or [])
                 if not (fn.name in own_fn if not _is_block(fn)
                         else exec_kind(fn) in own_exec)]

    # -- the component's own bodies: `super.x`, `super.f(...)` ---------------
    extra: List[Any] = []
    have = {fn.name for fn in inherited} | own_fn

    def super_fn(name: str) -> Optional[str]:
        """`super.<name>(...)` -> the private copy of the base's `name`."""
        target = next((fn for fn in (base.functions or [])
                       if fn.name == name and not _is_block(fn)), None)
        if target is None:
            return None
        hidden = _hidden(base, name)
        if hidden not in have:
            cp = from_base(target)
            cp.name = hidden
            cp.metadata = dict(cp.metadata, **{SUPER_IMPL: f"{_name(base)}::{name}"})
            extra.append(cp)
            have.add(hidden)
        return hidden

    base_field_names = {f.name for f in (base.fields or [])}
    # Rewritten COPIES: the declared functions keep `super`, for a backend that
    # renders inheritance natively (`declared`).
    rewritten = []
    for fn in own:
        body = _rewrite(fn.body, super_fields={
            n: hidden_field.get(n, n) for n in base_field_names},
            super_fns=super_fn)
        rewritten.append(fn if body is fn.body else dc.replace(fn, body=body))
    comp.functions = inherited + extra + rewritten
    _resolve_block_supers(comp, base, from_base)


def _is_block(fn) -> bool:
    """An exec block the component runs -- not a private copy of a base's."""
    return exec_kind(fn) is not None and SUPER_BLOCK not in (fn.metadata or {})


def _hidden(base, name: str) -> str:
    """The private name of ``base``'s member ``name`` in a derived component."""
    return f"{SUPER_PREFIX}{_name(base).replace('::', '__')}_{name}"


def _params(fn) -> Set[str]:
    return {a.arg for a in (getattr(getattr(fn, "args", None), "args", None)
                            or [])}


def _resolve_block_supers(comp, base, from_base) -> None:
    """Point each `super;` in the component's own exec blocks at the base's
    blocks of that kind (Table 27): private copies of them, run in order, or
    nothing when the base declares none. `metadata["super"]` lists them."""
    made: Dict[str, List[str]] = {}
    for i, fn in enumerate(list(comp.functions or [])):
        if not _is_block(fn) or (fn.metadata or {}).get(INHERITED_FROM) \
                or not _has_super(fn.body):
            continue
        kind = exec_kind(fn)
        if kind not in made:
            made[kind] = []
            for k, blk in enumerate(
                    b for b in ((base.functions or []) if base is not None
                                else [])
                    if _is_block(b) and exec_kind(b) == kind):
                cp = from_base(blk)
                cp.name = f"{_hidden(base, kind)}_{k}"
                cp.metadata = dict(cp.metadata, **{SUPER_BLOCK: kind})
                comp.functions.append(cp)
                made[kind].append(cp.name)
        # On a COPY: the declared block stays as written (`declared`).
        comp.functions[i] = dc.replace(
            fn, metadata=dict(fn.metadata or {}, super=made[kind] or None))


def _has_super(node) -> bool:
    if isinstance(node, (list, tuple)):
        return any(_has_super(n) for n in node)
    if isinstance(node, ir.StmtSuper):
        return True
    if not dc.is_dataclass(node) or isinstance(node, (type, ir.DataType)):
        return False
    return any(_has_super(getattr(node, f.name)) for f in dc.fields(node)
               if isinstance(getattr(node, f.name), (list, tuple, ir.Stmt)))


def _rewrite(node, *, self_fields=None, bound=frozenset(), super_fields=None,
             super_fns=None):
    """A copy of ``node`` with member references redirected; unchanged nodes
    are shared, and nothing is modified in place.

    * ``self_fields``: `self.a` -> `self.<hidden>` (a base body reading a field
      the derived component shadows), unless `a` is a parameter (``bound``) --
      the front end writes a parameter the same way.
    * ``super_fields``: `super.a` -> `self.<name>`.
    * ``super_fns(name)``: `super.f(...)` -> `self.<hidden>(...)`; None leaves
      the call (a core-library primitive's default, or an error the call gate
      reports).
    """
    def walk(n):
        if isinstance(n, list):
            new = [walk(x) for x in n]
            return n if all(a is b for a, b in zip(new, n)) else new
        if isinstance(n, tuple):
            new = tuple(walk(x) for x in n)
            return n if all(a is b for a, b in zip(new, n)) else new
        if not dc.is_dataclass(n) or isinstance(n, (type, ir.DataType)):
            return n
        if (super_fns is not None and isinstance(n, ir.ExprCall)
                and isinstance(n.func, ir.ExprAttribute)
                and isinstance(n.func.value, ir.TypeExprRefSuper)):
            hidden = super_fns(n.func.attr)
            if hidden is not None:
                return dc.replace(
                    n, func=ir.ExprAttribute(
                        value=ir.TypeExprRefSelf(loc=n.func.value.loc),
                        attr=hidden, loc=n.func.loc),
                    args=walk(n.args))
        if isinstance(n, ir.ExprAttribute):
            if (super_fields and isinstance(n.value, ir.TypeExprRefSuper)
                    and n.attr in super_fields):
                return dc.replace(n, value=ir.TypeExprRefSelf(loc=n.value.loc),
                                  attr=super_fields[n.attr])
            if (self_fields and isinstance(n.value, ir.TypeExprRefSelf)
                    and n.attr in self_fields and n.attr not in bound):
                return dc.replace(n, attr=self_fields[n.attr])
        changes = {}
        for f in dc.fields(n):
            v = getattr(n, f.name)
            nv = walk(v)
            if nv is not v:
                changes[f.name] = nv
        return dc.replace(n, **changes) if changes else n

    return walk(node)


def _completion_problem(comp, base) -> Optional[str]:
    """Why ``comp`` cannot take ``base``'s members, or None."""
    base_fields = {f.name: f for f in (base.fields or [])}
    for f in (comp.fields or []):
        if f.name not in base_fields:
            continue
        if _is_instance(f) or _is_instance(base_fields[f.name]):
            return (f"'{_name(comp)}' declares '{f.name}', shadowing the "
                    f"component instance '{_name(base)}' declares under that "
                    f"name; shadowing an instance is not lowered (its "
                    f"registers and sub-tree would need two names)")
    shadowed = {f.name for f in (comp.fields or []) if f.name in base_fields}
    for fn in (comp.functions or []):
        clash = sorted(n for n in _params(fn) & set(base_fields)
                       if n not in shadowed and _mentions_super(fn.body, n))
        if clash:
            return (f"'{_name(comp)}::{fn.name}' reads `super.{clash[0]}` and "
                    f"has a parameter '{clash[0]}': the front end spells a "
                    f"parameter and a field alike, so the two cannot be told "
                    f"apart once `super` is resolved")
    own = {fn.name: fn for fn in (comp.functions or [])
           if not _is_block(fn)}
    kept = [fn for fn in (base.functions or [])
            if not _is_block(fn) and fn.name not in own]
    called: Set[str] = set()
    for fn in kept:
        _self_calls(fn.body, called)
    for fn in (base.functions or []):
        if _is_block(fn) or fn.name not in own:
            continue
        if fn.name in called and _signature(fn) != _signature(own[fn.name]):
            return (f"'{_name(comp)}' shadows '{fn.name}' with a signature "
                    f"that differs from '{_name(base)}::{fn.name}', which a "
                    f"function it inherits calls: that call was written "
                    f"against the base's signature")
    return None


def _is_instance(field) -> bool:
    dt = getattr(field, "datatype", None)
    if _dt_name(dt) == "DataTypeArray":
        dt = getattr(dt, "element_type", None)
    return _dt_name(dt) in ("DataTypeComponent", "DataTypeRegisterGroup",
                            "DataTypeRegister")


def _mentions_super(node, name: str) -> bool:
    return _mentions(node, name, ir.TypeExprRefSuper)


def _mentions(node, name: str, root) -> bool:
    found: List[bool] = []

    def walk(n):
        if found:
            return
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
            return
        if not dc.is_dataclass(n) or isinstance(n, (type, ir.DataType)):
            return
        if (isinstance(n, ir.ExprAttribute)
                and isinstance(n.value, root) and n.attr == name):
            found.append(True)
            return
        for f in dc.fields(n):
            walk(getattr(n, f.name))

    walk(node)
    return bool(found)


@dc.dataclass(frozen=True)
class Declared:
    """A derived component's members AS DECLARED: before completion added its
    base's, and with `super` still in its bodies. What a backend that renders
    inheritance natively (a subclass of the base's class) emits."""
    base: Any
    fields: Tuple[Any, ...]
    functions: Tuple[Any, ...]


def declared(ctx, comp) -> Optional[Declared]:
    """``comp``'s declared members if it derives from a user component, else
    None (its members are its declared ones already)."""
    return (getattr(ctx, "_pss_declared", None) or {}).get(
        id(getattr(comp, "dtype", comp)))


def in_hierarchy(ctx, comp) -> bool:
    """Does ``comp`` derive from a user component, or is it one's base?"""
    c = getattr(comp, "dtype", comp)
    return (declared(ctx, c) is not None
            or id(c) in (getattr(ctx, "_pss_bases", None) or ()))


def declaring_component(fn, comp, type_map) -> Any:
    """The component whose source declares ``fn``: ``comp`` itself, or the
    base it was copied from (`INHERITED_FROM`)."""
    name = (getattr(fn, "metadata", None) or {}).get(INHERITED_FROM)
    return (type_map.get(name) if name else None) or comp


def super_owner(type_map, decl, name: str) -> Optional[Any]:
    """The user base `super.<name>` reaches from a function declared in
    ``decl``: the nearest base of ``decl`` that has a function ``name``.
    None when no user base has one -- the name is then the core library's
    (an executor's primitive), or nothing's. `complete` rewrites every
    `super.f` that reaches a user base, so a backend asking this after it is a
    check that nothing was missed."""
    base = user_base(type_map, decl)
    seen: Set[int] = set()
    while base is not None and id(base) not in seen:
        seen.add(id(base))
        if any(fn.name == name and exec_kind(fn) is None
               for fn in (base.functions or [])):
            return base
        base = user_base(type_map, base)
    return None


def problems(ctx, comps: Iterable[Any]) -> List[str]:
    """What `complete` could not do for any of ``comps``."""
    found = getattr(ctx, "_pss_comp_inherit", None) or {}
    out: List[str] = []
    seen: Set[int] = set()
    for c in comps:
        c = getattr(c, "dtype", c)
        if id(c) in found and id(c) not in seen:
            seen.add(id(c))
            out.append(found[id(c)])
    return out
