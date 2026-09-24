"""Reject calls the backend cannot lower, before anything is written.

A PASS, not an exception inside the emitter, for three reasons (docs §5.1):

  1. It reports EVERY illegal call in one run. An exception raised from
     expression rendering aborts at the first, so a model with six unmapped
     calls would take six compiles to find out.
  2. It runs before any output file is opened. A failure part-way through
     emission leaves a truncated artifact on disk that a later dv-flow run may
     treat as up-to-date.
  3. It is testable without a backend.

The pass and the emitters share ONE registry (`call_legality`), so they cannot
drift into two independently maintained lists -- which is the failure mode this
design would otherwise introduce. The pass produces the user-facing diagnostic;
an emitter that nevertheless meets an unclassified call raises, because by then
the pass has vouched for it and arriving there is a compiler bug.
"""
from __future__ import annotations

from typing import FrozenSet, List, Optional, Set

from . import pkg_functions as pf
from .call_legality import Ctx, Outcome, classify, core_function
from .progseq_model import (FuncKind, INIT_EXEC_KINDS, exec_kind, func_kind,
                            is_reg_group, _dt_name, sub_components)


def _where(node) -> str:
    """``file:line:col: `` for a node that carries a location, else ``''``.

    Same form as `reg_rmw._where`; diagnostics that do not agree on their prefix
    are diagnostics users learn to skim past.

    IT RETURNS '' FOR EVERYTHING TODAY. `loc` is a declared field on every IR
    node and the front end populates none of them -- `ExprCall.loc`,
    `Function.loc` and `DataTypeComponent.loc` are all None on a freshly
    translated model, which means `reg_rmw`'s diagnostics have never carried a
    location either. `_site()` below supplies component::function instead, and
    this stays so that real locations appear on their own the day ast2ir fills
    them in.
    """
    loc = getattr(node, "loc", None)
    if loc is None:
        return ""
    return f"{loc.file or '<unknown>'}:{loc.line}:{loc.pos}: "


def _site(comp, fn, call) -> str:
    """The most specific location available: a real one if the IR has it, else
    the component and function that contain the call."""
    if comp is None:
        return _where(call) or f"{pf.qualified_name(fn)}: "
    return _where(call) or f"{_name_of(comp)}::{fn.name}: "


def _name_of(dtype) -> str:
    nm = getattr(dtype, "name", None) or _dt_name(dtype)
    return nm.split("::")[-1]


def callee_name(func) -> Optional[str]:
    """The name a call is classified by, however the IR spells the reference.

    A call qualified by a core-library package is classified by its short
    name: `addr_reg_pkg::write32` is `write32` (`call_legality.core_function`).
    Any other qualified name stays qualified, so the registry finds nothing
    under it and the call is refused rather than mistaken for a built-in.
    """
    for attr in ("attr", "name"):
        v = getattr(func, attr, None)
        if isinstance(v, str):
            return core_function(v) or v
    return None


def is_core_call(func) -> bool:
    """True if the call was WRITTEN qualified by a core-library package.

    The spelling changes nothing about which function it is:
    `addr_reg_pkg::write32(h, d)` is delegated to the active executor exactly
    as `write32(h, d)` is (LRM 21.13.9.5), so inside that executor's own
    `write32` it is a call to itself. The default implementation is
    `super.write32(...)` (`is_super_call`).
    """
    return (_dt_name(func) == "ExprRefUnresolved"
            and core_function(getattr(func, "name", "") or "") is not None)


def is_super_call(func) -> bool:
    """True for `super.f(...)`: the base type's `f`, never an override."""
    return (_dt_name(func) == "ExprAttribute"
            and _dt_name(getattr(func, "value", None)) == "TypeExprRefSuper")


def own_ops(comp, ctor_names=None) -> FrozenSet[str]:
    """The operations ``comp`` itself declares."""
    return frozenset(fn.name for fn in (getattr(comp, "functions", None) or [])
                     if func_kind(fn, ctor_names) is FuncKind.EXPORT_OP)


def ops_for_call(comp, func, ops: FrozenSet[str],
                 ctor_names=None, super_base=None) -> FrozenSet[str]:
    """The operation names a call of this FORM may be classified against.

    ``ops`` is every operation in the model, by name (`model_names`). That is
    the right set for a call through a sub-component (`self.ch.start()`), whose
    receiver this pass does not type. It is the WRONG set for `self.f()`: the
    front end writes a call to a core-library function that way too, so an
    executor anywhere in the model that overrides `read32` (LRM 21.13.9.5)
    would turn every other component's `read32(h)` into a call to a method it
    does not have. `self.f()` is an operation only if ``comp`` declares `f`,
    and neither is a call written `addr_reg_pkg::read32(...)` or
    `super.read32(...)`. (A base component's operations are not inherited by
    the lowered component, so `super.f()` has no operation to name.)
    """
    if is_super_call(func):
        # The base's operation, where the body belongs to a class rendered as
        # derived from its base's (`super_base`); nothing otherwise.
        return (own_ops(super_base, ctor_names) if super_base is not None
                else frozenset())
    if is_core_call(func):
        return frozenset()
    if (_dt_name(func) == "ExprAttribute"
            and _dt_name(getattr(func, "value", None)) == "TypeExprRefSelf"):
        return own_ops(comp, ctor_names) if comp is not None else frozenset()
    return ops


def _walk_calls(node, out: List[object]) -> None:
    """Collect every ExprCall reachable from ``node``.

    Traverses by DATACLASS FIELDS, not by a hand-written list of child
    attribute names. The list version of this shipped for one iteration and
    missed `StmtExpr.expr` -- so a bare `write_bytes(h)` statement, the most
    ordinary call shape there is, was walked straight past and the whole gate
    silently passed the model. A gate whose traversal has holes is worse than no
    gate: it reports "no problems" with authority.

    The field walk cannot develop that hole. An IR node added later, or a field
    added to an existing one, is followed because it exists, not because someone
    remembered to add its name here.
    """
    if node is None or isinstance(node, (str, int, float, bool)):
        return
    if isinstance(node, (list, tuple, set)):
        for n in node:
            _walk_calls(n, out)
        return
    if isinstance(node, dict):
        for n in node.values():
            _walk_calls(n, out)
        return

    fields = getattr(type(node), "__dataclass_fields__", None)
    if fields is None:
        return                      # a leaf: enum, loc record, or a plain value
    if _dt_name(node) == "ExprCall":
        out.append(node)
    for fname in fields:
        child = getattr(node, fname, None)
        if child is not node:
            _walk_calls(child, out)


def _context_of(fn, ctor_names=None) -> Optional[Ctx]:
    """Which context ``fn``'s body is lowered into, or None if it is not
    lowered at all (a declaration, or an offset function that is evaluated)."""
    kind = func_kind(fn, ctor_names)
    if kind is FuncKind.EXPORT_OP:
        return Ctx.TARGET
    if kind in (FuncKind.CONSTRUCTOR, FuncKind.EXPORT_SOLVE):
        return Ctx.SOLVE
    if kind is FuncKind.EXEC and exec_kind(fn) in INIT_EXEC_KINDS:
        # Solve context: the tree is built before any scenario runs (20.1.2).
        # Whether the target lowers them at all is `exec_blocks`' question.
        return Ctx.SOLVE
    return None


def exec_blocks(root, ctx, target: str, init_blocks: bool) -> List[str]:
    """Refuse every component exec block the target would not run.

    An exec block the backend does not lower is a statement the model makes
    that the generated code silently does not: `exec init_down { a = 10; }`
    dropped leaves `a` at its initializer, and nothing says so. So a target
    either lowers init_down/init_up (``init_blocks``) or is refused them here,
    and every other component exec kind is refused everywhere.

    Inherited blocks arrive with the component (`comp_inherit`): a kind the
    derived component does not declare is the base's, and one it declares
    shadows the base's. `super;` in a derived block is the backend's to resolve
    or refuse (op-model-py's `stmt_super` refuses it outside an entry).
    """
    msgs: List[str] = []
    for comp in _components(root):
        kinds = [exec_kind(fn) for fn in (getattr(comp, "functions", None) or [])
                 if exec_kind(fn) is not None]
        for k in dict.fromkeys(kinds):
            if k not in INIT_EXEC_KINDS:
                msgs.append(f"{_name_of(comp)}: `exec {k}` is not lowered "
                            f"by '{target}'")
            elif not init_blocks:
                msgs.append(f"{_name_of(comp)}: `exec {k}` is not lowered "
                            f"by '{target}' yet")
    return msgs


def _components(root) -> List[object]:
    """Every regular component reachable from ``root``, root first.

    A local walk rather than `walk_tree`, which needs a type resolver this pass
    has no reason to carry. Register groups are not visited: their only
    functions are the offset accessors, which are evaluated rather than lowered.
    """
    out: List[object] = []
    seen: Set[int] = set()

    def visit(comp):
        if id(comp) in seen:
            return
        seen.add(id(comp))
        out.append(comp)
        for sub in sub_components(comp):
            visit(sub.dtype)

    visit(root)
    return out


def model_names(root, ctor_names=None):
    """Names the model itself supplies: operations, and sub-component ctors.

    Public because an emitter dispatching on `Disposition` has to classify a
    call the same way this pass did (P7.T4). Two implementations of "what
    counts as an operation" would disagree exactly where it matters -- the pass
    vouches for a call and the emitter then fails to place it.
    """
    ops: Set[str] = set()
    ctors: Set[str] = set()
    comps = _components(root)
    for comp in comps:
        for fn in (getattr(comp, "functions", None) or []):
            kind = func_kind(fn, ctor_names)
            if kind is FuncKind.EXPORT_OP:
                ops.add(fn.name)
            elif kind is FuncKind.CONSTRUCTOR:
                ctors.add(fn.name)
    return comps, frozenset(ops), frozenset(ctors)


def _with_extra(comps, extra) -> List[object]:
    """``comps`` plus the ``extra`` ones not already in it."""
    have = {id(c) for c in comps}
    return list(comps) + [c for c in extra or () if id(c) not in have]


def package_bodies(root, ctx, ctor_names=None, entries=(),
                   extra_components=()):
    """`pkg_functions.reach` from everything the tree rooted at ``root``
    lowers: ``[(function, context)]``. The ONE answer to "which package
    functions does this model carry", used by the gate and by `OpModel`.

    ``extra_components`` are rendered too though not in the tree: the base
    classes of a target that renders inheritance natively."""
    comps, ops, ctors = model_names(root, ctor_names)
    comps = _with_extra(comps, extra_components)
    bodies = [(fn, _context_of(fn, ctor_names))
              for comp in comps
              for fn in (getattr(comp, "functions", None) or [])]
    bodies += [(fn, Ctx.TARGET) for _, fn in entries]
    return pf.reach(ctx, bodies)


def validate_calls(root, ctx, target: str, *, report_only: bool = False,
                   ctor_names=None, entries=(),
                   pkg_functions: bool = False,
                   init_blocks: bool = False,
                   extra_components=(), native: bool = False) -> List[str]:
    """Classify every call in the component tree rooted at ``root``.

    ``entries`` is ``[(component, function)]`` for the exported actions
    (`export_action.EntryPoint`): target-context code that runs in
    ``component`` but is not one of its declared functions.

    ``pkg_functions`` says whether the target lowers package-scope functions.
    If it does, each one the model reaches is checked too, in the context it
    runs in (`pkg_functions.py`); if not, a call to one is refused by name.

    ``init_blocks`` says whether it lowers `exec init_down`/`init_up`. If it
    does, their bodies are checked in solve context; if not, each is refused
    (:func:`exec_blocks`).

    Returns the diagnostics. Unless ``report_only``, they are also pushed onto
    ``ctx.errors`` via ``add_error``, which is what stops the build --
    ``driver.compile`` checks ``ctx.errors`` only BEFORE the target runs, so
    the target has to check for itself (docs §5.3).
    """
    comps, ops, ctors = model_names(root, ctor_names)
    comps = _with_extra(comps, extra_components)
    import_fns = getattr(ctx, "import_functions", None) or []
    imports: FrozenSet[str] = frozenset(f.name for f in import_fns)
    # Only the `target` qualifier is enforced on imports: a target import
    # cannot run while the tree is built. The converse -- a `solve` import
    # called from a target function -- is also illegal (20.2.1.3), but models
    # and tests in this repo do it (`test_c_imports.py`), so refusing it is a
    # separate decision.
    import_contexts = {f.name: pf.contexts_of(f) for f in import_fns
                       if getattr(f, "is_target", False)}

    msgs: List[str] = exec_blocks(root, ctx, target, init_blocks)
    # What each class renders. A target rendering inheritance natively
    # (`native`) renders a derived component's DECLARED functions, `super`
    # intact, and reaches the rest through its base class; a flattening one
    # renders `comp_inherit`'s completed view.
    from .comp_inherit import declared as _declared_members
    bodies = []
    for comp in comps:
        dec = _declared_members(ctx, comp) if native else None
        fns = dec.functions if dec is not None else (
            getattr(comp, "functions", None) or [])
        sb = dec.base if dec is not None else None
        bodies += [(comp, fn, _context_of(fn, ctor_names), sb) for fn in fns
                   if init_blocks
                   or func_kind(fn, ctor_names) is not FuncKind.EXEC]
    bodies += [(comp, fn, Ctx.TARGET, None) for comp, fn in entries]

    declared = pf.declared(ctx)
    if pkg_functions:
        # A package function has no component, so no call in its body is an
        # operation or a sub-component constructor of one.
        bodies += [(None, fn, c, None) for fn, c in
                   package_bodies(root, ctx, ctor_names, entries,
                                  extra_components)]

    for comp, fn, context, super_base in bodies:
        if context is None:
            continue
        calls: List[object] = []
        _walk_calls(getattr(fn, "body", None), calls)
        in_pkg = comp is None
        for call in calls:
            func = getattr(call, "func", None)
            name = callee_name(func)
            if name is None:
                continue
            callee = pf.callee(call, declared)
            if callee is not None and not pkg_functions:
                msgs.append(
                    f"{_site(comp, fn, call)}cannot lower call: '{name}' is a "
                    f"package-scope function, which '{target}' does not "
                    f"lower yet")
                continue
            if callee is not None:
                # The front end says which function this is; the model's own
                # names do not enter into it.
                res = classify(name, context=context, target=target,
                               pkg_funcs={name: pf.contexts_of(callee)})
            else:
                res = classify(name, context=context, target=target,
                               model_ops=(frozenset() if in_pkg else
                                          ops_for_call(comp, func, ops,
                                                       ctor_names,
                                                       super_base)),
                               imports=imports,
                               import_contexts=import_contexts,
                               subcomps=frozenset() if in_pkg else ctors)
            if res.outcome is Outcome.SUPPORTED:
                continue
            msgs.append(f"{_site(comp, fn, call)}cannot lower call: "
                        f"{res.message}")

    if not report_only:
        for m in msgs:
            ctx.add_error(m)
    return msgs


def gate(root, ctx, target: str, language: str, ctor_names=None,
         entries=(), pkg_functions: bool = False,
         init_blocks: bool = False, extra_components=(),
         native: bool = False) -> None:
    """Run :func:`validate_calls` and raise if anything is unlowerable.

    Every operation-model backend calls this as its first act, BEFORE any file
    is opened -- see `progseq_gen.generate` for the two reasons that matters
    (a truncated artifact looks up-to-date to dv-flow, and `driver.compile`
    inspects `ctx.errors` only before the target runs).

    One function rather than three copies of the same six lines, because the
    three copies is how the gate came to run for SystemVerilog only: it was
    written where the SV backend needed it and never propagated to the two
    backends with the NARROWEST lowering, which are the ones most likely to
    meet a call they cannot render. `OpModelTarget.check()` absorbs this in
    P2 of docs/design/generator-style-extensions-plan.md.
    """
    from ..driver import CompileError
    bad = validate_calls(root, ctx, target, ctor_names=ctor_names,
                         entries=entries, pkg_functions=pkg_functions,
                         init_blocks=init_blocks,
                         extra_components=extra_components, native=native)
    if bad:
        raise CompileError(
            f"{len(bad)} call(s) cannot be lowered to {language}", bad)
