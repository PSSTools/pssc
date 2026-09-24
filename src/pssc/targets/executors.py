"""Executors, and the memory primitives they override (LRM 21.7, 21.13.9.5).

A call to a primitive read or write -- `read32(h)`, and every register access,
which is defined in terms of one (21.14) -- is DELEGATED to the function of the
same prototype in the executor assigned to the calling action. A component
states its executor with `set_executor` in `init_down`/`init_up`; one that does
not inherits its parent's; an executor is its own (21.7.2.6). An executor that
does not override a primitive leaves it to the default implementation, which
for an operation model is the platform seam.

Language-neutral, like `pkg_functions.py`: this says which components are
executors, which primitives each overrides, and whether the model has any. How
a delegated call is spelled is the backend's.

What is NOT supported, and refused rather than lowered wrongly:

* overriding `addr_value` (or its solve-time forms). The default primitives
  resolve their address through it (21.13.9.5), so an override changes every
  access; the backends lower `addr_value` as the identity on a handle.
* a target that does not delegate at all (`check`): a model
  whose executor overrides a primitive would have its accesses bypass it.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .progseq_model import _dt_name, resolve_ref

#: `name: (direction, width)` for every primitive an executor may override and
#: an operation model delegates (21.13.9.1, 21.13.9.2).
PRIMITIVES: Dict[str, Tuple[str, int]] = {
    "read8": ("read", 8), "read16": ("read", 16),
    "read32": ("read", 32), "read64": ("read", 64),
    "write8": ("write", 8), "write16": ("write", 16),
    "write32": ("write", 32), "write64": ("write", 64),
}

#: Overridable per Syntax 159, and refused (see the module docstring).
UNSUPPORTED_OVERRIDES = ("addr_value", "addr_value_solve", "addr_value_abs")

_EXECUTOR_BASE = "executor_pkg::executor_base_c"
_LIBRARY_BASES = ("executor_pkg::executor_base_c", "executor_pkg::executor_c")

#: The memory-access descriptor every primitive takes last (21.13.9).
MEM_ACCESS_DESC = "addr_reg_pkg::mem_access_desc_s"


def _bases(dtype, type_map) -> List[object]:
    """*dtype*'s base components, nearest first, resolved."""
    out = []
    seen = set()
    sup = resolve_ref(getattr(dtype, "super", None), type_map)
    while sup is not None and id(sup) not in seen:
        seen.add(id(sup))
        out.append(sup)
        sup = resolve_ref(getattr(sup, "super", None), type_map)
    return out


def is_executor(dtype, type_map) -> bool:
    """True if *dtype* derives from `executor_base_c` (21.7.1)."""
    return any(getattr(b, "name", None) == _EXECUTOR_BASE
               for b in _bases(dtype, type_map))


def overrides(dtype) -> Dict[str, object]:
    """``{primitive: function}`` for the primitives *dtype* overrides."""
    return {fn.name: fn for fn in (getattr(dtype, "functions", None) or [])
            if fn.name in PRIMITIVES and getattr(fn, "body", None)}


def has_executors(model) -> bool:
    """True if any component of *model* is an executor. The backends render
    delegation for such a model -- a component's executor is its own business
    once one exists -- and leave every other model exactly as it was."""
    tm = getattr(getattr(model, "ctx", None), "type_map", None) or {}
    return any(is_executor(n.dtype, tm) for n in model.components)


def _assigns_executor(model, comp) -> bool:
    """True if *comp* certainly has an executor once initialized: it is one,
    or an init block calls `set_executor` as a top-level statement -- not
    under an `if` or a loop, where it might not run."""
    from .validate_calls import callee_name

    tm = getattr(getattr(model, "ctx", None), "type_map", None) or {}
    if is_executor(comp, tm):
        return True
    for kind in ("init_down", "init_up"):
        for fn in model.init_blocks(comp, kind):
            for st in getattr(fn, "body", None) or []:
                e = getattr(st, "expr", None)
                if (_dt_name(st) == "StmtExpr" and _dt_name(e) == "ExprCall"
                        and callee_name(e.func) == "set_executor"):
                    return True
    return False


def default_reachable(model, prim: str) -> bool:
    """Can a call to *prim* reach the default implementation -- the platform?

    Three ways: a component with no executor (anything not under a root that
    assigns one unconditionally), an executor that does not override *prim*,
    or an override calling the default explicitly (the caller checks the
    text for that). A pure-PSS executor that overrides every primitive, and
    one mapping them to its own imports (LRM Example 352), leave the
    platform's primitives unused, and the generated import API does not ask
    for them.
    """
    tm = getattr(getattr(model, "ctx", None), "type_map", None) or {}
    if not _assigns_executor(model, model.root):
        return True
    return any(is_executor(n.dtype, tm) and prim not in overrides(n.dtype)
               for n in model.components)


def mem_access_desc(type_map):
    """The `mem_access_desc_s` type, which a delegated call passes."""
    return (type_map or {}).get(MEM_ACCESS_DESC)


def _type_word(dtype) -> str:
    return _dt_name(dtype).replace("DataType", "") or "?"


def check_prototype(fn, type_map) -> Optional[str]:
    """Why *fn* does not have its primitive's prototype (Syntax 159), or None.

    The delegating call passes the handle, the data for a write, and the
    descriptor, positionally; an override that declares something else would
    be called wrongly, not refused.
    """
    direction, width = PRIMITIVES[fn.name]
    args = list(getattr(getattr(fn, "args", None), "args", None) or [])
    want = 3 if direction == "write" else 2
    if len(args) != want:
        return (f"takes {len(args)} parameter(s); the prototype has {want} "
                f"(the handle{', the data' if direction == 'write' else ''} "
                f"and a mem_access_desc_s)")
    desc = resolve_ref(getattr(args[-1], "annotation", None), type_map)
    if getattr(desc, "name", None) != MEM_ACCESS_DESC:
        return (f"its last parameter is "
                f"{getattr(desc, 'name', None) or _type_word(desc)}, not "
                f"mem_access_desc_s")
    if direction == "write":
        data = getattr(args[1], "annotation", None)
        if (_dt_name(data) != "DataTypeInt" or getattr(data, "bits", None) != width
                or getattr(data, "signed", False)):
            return f"its data parameter is not bit[{width}]"
    ret = getattr(fn, "returns", None)
    if direction == "read":
        if (_dt_name(ret) != "DataTypeInt" or getattr(ret, "bits", None) != width
                or getattr(ret, "signed", False)):
            return f"it does not return bit[{width}]"
    return None


def check(components, type_map, *, delegating: bool) -> List[str]:
    """Diagnostics for the executors among *components*.

    ``delegating`` is whether the target renders delegation. One that does not
    refuses every override: its accesses would go straight to the platform and
    the override would never run -- a model that compiles and does something
    else.
    """
    msgs: List[str] = []
    for comp in components:
        if not is_executor(comp, type_map):
            continue
        name = getattr(comp, "name", "?")
        for fn in getattr(comp, "functions", None) or []:
            if fn.name in UNSUPPORTED_OVERRIDES:
                msgs.append(
                    f"{name}::{fn.name}: overriding '{fn.name}' in an executor "
                    f"is not supported. The default primitives resolve their "
                    f"address through it (LRM 21.13.9.5), and this target "
                    f"lowers it as the identity on a handle.")
        ovr = overrides(comp)
        if ovr and not delegating:
            msgs.append(
                f"{name}: overrides {', '.join(sorted(ovr))}, and this target "
                f"does not delegate memory primitives to executors (LRM "
                f"21.13.9.5). Accesses would bypass the override.")
            continue
        for fn in ovr.values():
            why = check_prototype(fn, type_map)
            if why is not None:
                msgs.append(f"{name}::{fn.name}: overrides a memory "
                            f"primitive, but {why} (LRM Syntax 159)")
    return msgs
