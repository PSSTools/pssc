"""Exported functions as operation-model API (LRM 20.4.2, and an extension).

`export target function f;` makes ``f`` callable by the environment. The LRM
exports static functions only. pssc's operation models take their API from the
EXTENSION pssparser reports as PSS120: a component's INSTANCE function,
exported in that component's body, is run on the instance the environment
calls it through. That needs no way to name the root from inside the model,
and it is the shape exported context functions will take.

For now only the ROOT component's exports are accepted: the root instance is
the one context a generated model hands the environment. Everything else an
export can name is refused rather than dropped, so a model's declared API is
either generated or reported:

* a package-scope export (a static function in the LRM's sense): not yet;
* an export in any other component of the tree, or in a base of the root:
  not yet -- reaching it needs an executor context (design D13);
* an exported `solve function`: `export target function` names a function
  called in target context.

Which function a name means is the linker's answer (`ast2ir.Export`); it is
looked up in the COMPLETED root (`comp_inherit.complete`), where an inherited
function is present under its own name.
"""
from __future__ import annotations

from typing import Any, List, Tuple

from . import progseq_model as pm


def _error(problems: List[str]):
    """A user error (`CompileError`), one entry per export, so the CLI
    reports each on its own line rather than as an internal error."""
    from ..driver import CompileError
    return CompileError(
        f"{len(problems)} exported function(s) cannot be generated", problems)


_KIND_WORDS = {
    pm.FuncKind.CONSTRUCTOR: "the constructor",
    pm.FuncKind.EXPORT_SOLVE: "a solve function",
    pm.FuncKind.IMPORT_TASK: "an import function",
    pm.FuncKind.IMPORT_SOLVE: "an import function",
    pm.FuncKind.REG_OFFSET: "a register-group offset function",
}


def _home(ctx, export) -> Any:
    """The component type whose body declares ``export``, or None."""
    if export.scope is None:
        return None
    return (getattr(ctx, "type_map", None) or {}).get(export.scope)


def root_exports(ctx, root, components, ctor_names) -> Tuple[Any, ...]:
    """The root's exported functions, in declaration order, each once.

    ``components`` is the walked tree. Raises `CompileError` naming
    every export that cannot be generated.
    """
    from .comp_inherit import declared
    in_tree = {id(getattr(n, "dtype", n)) for n in components}
    bases = set()
    dec = declared(ctx, root)
    while dec is not None:
        bases.add(id(dec.base))
        dec = declared(ctx, dec.base)

    out: List[Any] = []
    problems: List[str] = []
    for e in getattr(ctx, "exports", None) or ():
        home = _home(ctx, e)
        if e.scope is None:
            problems.append(
                f"{e.where}export of package function '{e.function}': not "
                f"supported yet; export an instance function of the root "
                f"component '{root.name}' instead")
            continue
        if home is not root:
            if home is not None and (id(home) in in_tree or id(home) in bases):
                problems.append(
                    f"{e.where}export of '{e.scope}::{e.function}': only the "
                    f"root component's ('{root.name}') functions can be "
                    f"exported yet")
            # A component outside the tree contributes nothing to this model.
            continue
        fn = next((f for f in (root.functions or [])
                   if f.name == e.function and pm.exec_kind(f) is None), None)
        if fn is None:
            problems.append(
                f"{e.where}exported function '{e.function}' is not a function "
                f"of '{root.name}' after inheritance")
            continue
        kind = pm.func_kind(fn, ctor_names)
        if kind is not pm.FuncKind.EXPORT_OP:
            problems.append(
                f"{e.where}'{e.function}' is exported as a target function, "
                f"but it is {_KIND_WORDS.get(kind, 'not an operation')}")
            continue
        if all(f is not fn for f in out):
            out.append(fn)
    if problems:
        raise _error(problems)
    return tuple(out)
