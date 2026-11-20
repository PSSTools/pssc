"""Functions in an action, and action inheritance (a pssc extension).

PSS 3.0 has no functions in an action; pssc accepts them (pssparser LINT412,
`action-function`) so a family of actions can share a template method: the
base's `exec body` calls `arrange()` and `act()`, and each derived action
supplies its own `act()`. The IR keeps an action's OWN functions and names
its base (`super`), as it does for a component. `complete(ctx)` gives each
derived action the functions it inherits, the way `comp_inherit` does for a
component, so a consumer walks one action's `functions` and finds them all:

* Functions are virtual. A base function the action does not shadow is
  COPIED into it (`metadata["inherited_from"]`), so a base body's `f()`
  rendered in the derived action reaches the derived `f`.
* `super.f(args)` calls the BASE's `f`, statically: a private copy of it
  (`_pss_super_<base>_f`, `metadata["super_impl"]`) in the derived action.
* `function void f();` -- a prototype, no body -- is ABSTRACT
  (`metadata["abstract"]`). An abstract action may leave it so; a concrete
  action that neither declares nor inherits a body for it is an error, at
  the action's line. So is `super.f()` of an abstract `f`.

Only functions take part: an action's fields and exec blocks are inherited
by each consumer's walk of `super` as before, and are left alone here. The
completion runs once per translation (ast2ir, after every type is declared),
IN PLACE: a consumer identifies an action by object. `declared(ctx, action)`
keeps the functions as written, for a backend that renders inheritance
natively.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Set

from .comp_inherit import INHERITED_FROM, SUPER_IMPL, _copy, _hidden, _rewrite
from .progseq_model import _dt_name, exec_kind

#: Marks a function declared with no body: ``metadata["abstract"] = True``.
ABSTRACT = "abstract"


def is_method(fn) -> bool:
    """A function of the action -- not an exec block or a constraint, which
    share the `functions` list."""
    md = getattr(fn, "metadata", None) or {}
    return exec_kind(fn) is None and not md.get("_is_constraint")


def is_abstract(fn) -> bool:
    return bool((getattr(fn, "metadata", None) or {}).get(ABSTRACT))


def _is_action(dtype) -> bool:
    return _dt_name(dtype) == "DataTypeClass"


def _name(dtype) -> str:
    return getattr(dtype, "name", None) or "?"


def _line(node) -> Optional[int]:
    loc = getattr(node, "loc", None)
    line = getattr(loc, "line", None)
    return line if line and line > 0 else None


def _where(*nodes) -> str:
    for n in nodes:
        line = _line(n)
        if line:
            return f"line {line}: "
    return ""


def base_of(tm: Dict[str, Any], action) -> Optional[Any]:
    """``action``'s base action, if it names one the translation has."""
    sup = getattr(action, "super", None)
    name = getattr(sup, "ref_name", None) if sup is not None else None
    base = tm.get(name) if name else None
    return base if base is not None and _is_action(base) else None


def declared(ctx, action) -> Optional[List[Any]]:
    """``action``'s functions as written (with `super` in their bodies), if
    completion changed them; else None."""
    return (getattr(ctx, "_pss_action_declared", None) or {}).get(id(action))


def complete(ctx) -> List[str]:
    """Give every derived action its inherited functions; return the located
    errors (also added to ``ctx`` when it has ``add_error``). Idempotent."""
    if getattr(ctx, "_pss_action_inherit", None) is not None:
        return []
    errors: List[str] = []
    ctx._pss_action_inherit = True
    ctx._pss_action_declared = {}
    tm = getattr(ctx, "type_map", None) or {}
    done: Set[int] = set()
    actions = []
    for dt in list(tm.values()):
        if _is_action(dt) and id(dt) not in {id(a) for a in actions}:
            actions.append(dt)
    for a in actions:
        _complete(ctx, tm, a, done, (), errors)
    for a in actions:
        if getattr(a, "is_abstract", False):
            continue
        for fn in a.functions or []:
            if is_method(fn) and is_abstract(fn):
                src = (fn.metadata or {}).get(INHERITED_FROM) or _name(a)
                errors.append(
                    f"{_where(a, fn)}action '{_name(a)}' does not implement "
                    f"function '{fn.name}' (declared with no body in '{src}'"
                    + (f" at line {_line(fn)}" if _line(fn) else "") + ")")
    add = getattr(ctx, "add_error", None)
    if add is not None:
        for e in errors:
            add(e)
    return errors


def _complete(ctx, tm, action, done: Set[int], stack, errors: List[str]) -> None:
    if id(action) in done:
        return
    done.add(id(action))
    base = base_of(tm, action)
    if base is None:
        return
    if base is action or any(base is s for s in stack):
        errors.append(f"{_where(action)}action '{_name(action)}' "
                      f"inherits from itself")
        return
    _complete(ctx, tm, base, done, stack + (action,), errors)
    base_fns = [fn for fn in (base.functions or []) if is_method(fn)]
    own = list(action.functions or [])
    own_fn = {fn.name for fn in own if is_method(fn)}
    if not base_fns and not any(_mentions_super_call(fn.body) for fn in own
                                if is_method(fn) or exec_kind(fn)):
        return

    have = {fn.name for fn in base_fns} | own_fn
    extra: List[Any] = []

    def super_fn(name: str) -> Optional[str]:
        """`super.<name>(...)` -> the private copy of the base's `name`."""
        target = next((fn for fn in base_fns if fn.name == name), None)
        if target is None:
            return None
        if is_abstract(target):
            errors.append(f"{_where(action)}action '{_name(action)}' calls "
                          f"'super.{name}()', which has no body in "
                          f"'{_name(base)}'")
            return None
        hidden = _hidden(base, name)
        if hidden not in have:
            have.add(hidden)
            cp = _copy(target, base)
            cp.name = hidden
            cp.metadata = dict(cp.metadata, **{SUPER_IMPL: f"{_name(base)}::{name}"})
            extra.append(cp)
        return hidden

    inherited = [_copy(fn, base) for fn in base_fns if fn.name not in own_fn]
    rewritten = []
    for fn in own:
        if not is_method(fn) and exec_kind(fn) is None:
            rewritten.append(fn)        # a constraint: no calls to redirect
            continue
        body = _rewrite(fn.body, super_fns=super_fn)
        rewritten.append(fn if body is fn.body else dc.replace(fn, body=body))
    ctx._pss_action_declared[id(action)] = own
    action.functions = inherited + extra + rewritten


def _mentions_super_call(node) -> bool:
    """Is there a `super.f(...)` anywhere under ``node``?"""
    import zuspec.ir.core as ir
    if isinstance(node, (list, tuple)):
        return any(_mentions_super_call(n) for n in node)
    if not dc.is_dataclass(node) or isinstance(node, (type, ir.DataType)):
        return False
    if (isinstance(node, ir.ExprCall) and isinstance(node.func, ir.ExprAttribute)
            and isinstance(node.func.value, ir.TypeExprRefSuper)):
        return True
    return any(_mentions_super_call(getattr(node, f.name))
               for f in dc.fields(node))
